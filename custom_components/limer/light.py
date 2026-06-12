"""Virtual Light platform for Limer.

A VirtualLight controls N real light entities and manages a state machine
that integrates optional occupancy, illuminance, and schedule virtual sensors.

State machine
─────────────
  IDLE       lights off, no timer
  ACTIVE     lights on, timer running
               → entered when light turned on manually, or when occupancy
                 triggers and there is no active occupancy
  OCCUPIED   lights on, occupancy active — timer suspended
  COUNTDOWN  occupancy just cleared, timer ticking toward lights-off

Transitions
  IDLE + (manual on OR occupancy trigger [no occupancy sensor / not occupied])
       → ACTIVE  (start timer immediately)

  IDLE/ACTIVE + occupancy becomes active
       → OCCUPIED  (cancel any running timer)

  OCCUPIED + occupancy clears
       → COUNTDOWN  (start timer)

  ACTIVE/COUNTDOWN + timer expires
       → IDLE  (turn off real lights)

  any + light turned off externally
       → IDLE

Illuminance / schedule gating (TBD — see design notes in README):
  Currently these are read but the gating behaviour (hands-off vs force-off
  when state changes) is not yet implemented.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_ENTITY,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_SCHEDULE_ENTITY,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Limer light entities from a config entry."""
    if entry.data[CONF_ENTITY_TYPE] == ENTITY_TYPE_LIGHT:
        async_add_entities([VirtualLight(hass, entry)])


class VirtualLight(LightEntity):
    """A virtual light with occupancy/illuminance/schedule awareness."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        cfg = {**entry.data, **entry.options}
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id

        self._lights: list[str] = cfg.get(CONF_LIGHTS, [])
        self._light_timeout: int = int(cfg.get(CONF_LIGHT_TIMEOUT, 300))

        self._occupancy_entity: str | None = cfg.get(CONF_OCCUPANCY_ENTITY)
        self._illuminance_entity: str | None = cfg.get(CONF_ILLUMINANCE_ENTITY)
        self._schedule_entity: str | None = cfg.get(CONF_SCHEDULE_ENTITY)

        self._machine_state: str = STATE_IDLE
        self._attr_is_on = False
        self._timer_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # HA lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Subscribe to state changes for lights and optional virtual entities."""
        watch = list(self._lights)
        if self._occupancy_entity:
            watch.append(self._occupancy_entity)
        if self._illuminance_entity:
            watch.append(self._illuminance_entity)
        if self._schedule_entity:
            watch.append(self._schedule_entity)

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, watch, self._handle_state_change
            )
        )

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_timer()

    # ------------------------------------------------------------------
    # LightEntity API
    # ------------------------------------------------------------------

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on all real lights and transition the state machine."""
        await self._set_lights(True)
        self._transition_on(manual=True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off all real lights and go idle."""
        await self._set_lights(False)
        self._go_idle()

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    @callback
    def _handle_state_change(self, event) -> None:
        """React to a tracked entity changing state."""
        entity_id: str = event.data["entity_id"]
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if entity_id in self._lights:
            self._on_light_state_change(new_state.state)
        elif entity_id == self._occupancy_entity:
            self._on_occupancy_change(new_state.state == "on")
        # Illuminance / schedule gating TBD

    def _on_light_state_change(self, state: str) -> None:
        """Handle a real light being turned on/off externally."""
        if state == "on":
            self._transition_on(manual=True)
        else:
            # Check if ALL real lights are now off.
            if all(
                (s := self.hass.states.get(e)) and s.state != "on"
                for e in self._lights
            ):
                self._go_idle()

    def _on_occupancy_change(self, occupied: bool) -> None:
        """Handle the virtual occupancy sensor changing."""
        if occupied:
            # Lights on, cancel any timer.
            self._machine_state = STATE_OCCUPIED
            self._cancel_timer()
            if not self._attr_is_on:
                self.hass.async_create_task(self._set_lights(True))
            self.async_write_ha_state()
        else:
            if self._machine_state == STATE_OCCUPIED:
                self._machine_state = STATE_COUNTDOWN
                self._start_timer(self._compute_occupancy_countdown())
                self.async_write_ha_state()

    def _compute_occupancy_countdown(self) -> int:
        """Seconds to wait after occupancy clears before turning lights off.

        Anchors to the occupancy sensor's latest_occupied_time so that each
        sub-sensor's individual timeout is respected, then adds light_timeout
        on top as an extra grace period.
        """
        base = self._light_timeout
        if self._occupancy_entity:
            occ_state = self.hass.states.get(self._occupancy_entity)
            if occ_state:
                lot_str = occ_state.attributes.get("latest_occupied_time")
                if lot_str:
                    try:
                        lot = datetime.fromisoformat(lot_str)
                        now = datetime.now(timezone.utc)
                        remaining = (lot - now).total_seconds()
                        return max(0, int(base + remaining))
                    except (ValueError, TypeError):
                        pass
        return base

    def _transition_on(self, manual: bool = False) -> None:
        """Move to ACTIVE (or stay OCCUPIED) when lights come on."""
        if self._machine_state == STATE_OCCUPIED:
            return  # already managed by occupancy

        if self._machine_state != STATE_ACTIVE:
            self._machine_state = STATE_ACTIVE
            self._attr_is_on = True
            self._start_timer()
            self.async_write_ha_state()

    def _go_idle(self) -> None:
        """Cancel any timer and move to IDLE."""
        self._cancel_timer()
        self._machine_state = STATE_IDLE
        self._attr_is_on = False
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Timer helpers
    # ------------------------------------------------------------------

    def _start_timer(self, duration: int | None = None) -> None:
        self._cancel_timer()
        self._timer_task = self.hass.async_create_task(
            self._run_timer(duration if duration is not None else self._light_timeout)
        )

    async def _run_timer(self, duration: int) -> None:
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            return
        # Timer expired — turn off lights and go idle.
        await self._set_lights(False)
        self._go_idle()

    def _cancel_timer(self) -> None:
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = None

    # ------------------------------------------------------------------
    # Real-light control
    # ------------------------------------------------------------------

    async def _set_lights(self, on: bool) -> None:
        service = "turn_on" if on else "turn_off"
        for light in self._lights:
            await self.hass.services.async_call(
                "light",
                service,
                {"entity_id": light},
                blocking=False,
            )
        self._attr_is_on = on

    # ------------------------------------------------------------------
    # Extra state attributes
    # ------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict:
        return {"limer_state": self._machine_state}
