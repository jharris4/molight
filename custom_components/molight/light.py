"""Virtual Light platform for MoLight.

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

Turn-on attribution
  Four timestamps record the last time the virtual light was activated and why:
    last_on_physical   — an underlying real light entity changed to ON from an
                         external source (physical switch, another automation, HA
                         UI acting on the real entity) while this virtual light
                         was IDLE.
    last_on_virtual    — the user toggled this virtual light entity ON via the HA UI
                         (async_turn_on was called directly).
    last_on_occupancy  — occupancy sensor triggered the lights.
    last_on_illuminance— an illuminance→dark change triggered the lights.

  All four are exposed as extra state attributes (ISO strings or null).
  The most-recent value is used when computing the illuminance-dark countdown
  in the absence of an occupancy sensor.

  Brightness changes are tracked the same way: last_brightness_change_physical
  records external changes on the real lights (and restarts a running
  ACTIVE/COUNTDOWN timer; brightness 0 counts as off, leaving 0 as on), while
  last_brightness_change_virtual records brightness set through this entity.

Maintain occupancy (when a maintain occupancy entity is configured)
  The maintain entity holds an already-on light on while it is on; it never
  turns the light on and is ignored while the light is off. Unlike the
  combined sensor's maintain_sensors (which only extend occupancy started by
  a trigger sensor), it holds the light regardless of how it was lit —
  manual, physical, or occupancy.

  • Maintain ON while the light is on (ACTIVE/COUNTDOWN) → OCCUPIED, timer
    cancelled. Illuminance/schedule gating does not apply: it is not a
    turn-on. Forced offs (bright in control mode, gate window end) still win,
    exactly as they do over regular occupancy.
  • Occupancy clearing while maintain is on keeps the light OCCUPIED.
  • The countdown starts only when both the regular occupancy entity and the
    maintain entity are clear, anchored to the max of their
    latest_occupied_time attributes.
  • The false-detection quick off applies on a maintain clear only when both
    sensors flagged their clears false (a genuine presence on either side
    means the light earns its normal countdown).
  • Startup: a light that is already on with the maintain entity on is
    adopted as OCCUPIED (no timer).

Illuminance handling (when an illuminance entity is configured), per
illuminance_mode:
  • Occupancy only turns lights ON when illuminance is OFF (dark) — both modes.
  • Illuminance ON→OFF (bright→dark): if currently occupied, enter OCCUPIED;
    else if recent occupancy (countdown > 0), enter COUNTDOWN with adjusted
    timer — both modes.
  • Illuminance OFF→ON (dark→bright):
      control — go IDLE, turn lights off.
      gate    — no effect; bright never turns lights off. Use when the lux
                sensor can see the controlled lights, which would otherwise
                oscillate (lights on → reads bright → forced off → dark → …).

Holding auto-off
  Auto-off is *held* while the companion "<name> Auto-off" switch is off OR
  any configured keep-on entity (hold_entities) is on. While held, every
  automatic turn-off is suspended — timer expiry, the false-detection quick
  off, bright-forces-off, and schedule window ends — but turn-ons and manual
  control work exactly as usual (a manual off still turns the lights off).
  State-machine transitions keep happening; they just never arm a timer.

  When the last hold releases, the light re-evaluates its rules from current
  conditions: a follow-mode window that ended while held turns it off now
  (the window marker is kept while held for exactly this), as does being
  outside a gate-mode window or bright in illuminance control mode; active
  occupancy keeps it on (OCCUPIED); an active follow window keeps it
  SCHEDULED; otherwise a fresh full timer starts (ACTIVE).

  A keep-on entity going unavailable/unknown holds its last known value, as
  everywhere else in the integration; at startup an unavailable keep-on
  entity counts as not holding.

Schedule handling (when a schedule entity is configured), per schedule_mode:
  • follow — lights turn ON at window start (state SCHEDULED, no timer) and
    OFF at window end. Boundaries are edge-triggered: manual changes between
    them stand. schedule_window_start records the window whose start we
    applied; it persists across restarts so a boundary missed while HA was
    down is applied exactly once at startup, while a manual off mid-window
    is respected. Occupancy/illuminance are ignored while SCHEDULED.
  • gate — occupancy may only activate lights inside the window; window end
    forces lights off (like illuminance turning bright), window start
    re-evaluates occupancy.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    CoreState,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_ENTITY_TYPE,
    CONF_FALSE_OFF_DELAY,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_MODE,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    DATA_AUTO_OFF_ENABLED,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    SIGNAL_AUTO_OFF_TOGGLED,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
)
from .helpers import molight_config

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MoLight light entities from a config entry."""
    if entry.data[CONF_ENTITY_TYPE] == ENTITY_TYPE_LIGHT:
        async_add_entities([VirtualLight(hass, entry)])


class VirtualLight(LightEntity, RestoreEntity):
    """A virtual light with occupancy/illuminance/schedule awareness."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        cfg = molight_config(entry)
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self._entry_id = entry.entry_id

        self._lights: list[str] = cfg.get(CONF_LIGHTS, [])
        self._light_timeout: int = int(cfg.get(CONF_LIGHT_TIMEOUT, 300))
        self._false_off_delay: int = int(cfg.get(CONF_FALSE_OFF_DELAY, 5))
        # True while the current on-period was started by occupancy (not by
        # the user) — the only case where a false-detection clear may cut the
        # lights short.
        self._occupancy_lit_lights: bool = False

        self._occupancy_entity: str | None = cfg.get(CONF_OCCUPANCY_ENTITY)
        self._maintain_entity: str | None = cfg.get(CONF_MAINTAIN_OCCUPANCY_ENTITY)
        self._illuminance_entity: str | None = cfg.get(CONF_ILLUMINANCE_ENTITY)
        self._illuminance_mode: str = cfg.get(
            CONF_ILLUMINANCE_MODE, ILLUMINANCE_MODE_CONTROL
        )
        self._schedule_entity: str | None = cfg.get(CONF_SCHEDULE_ENTITY)
        self._schedule_mode: str = cfg.get(CONF_SCHEDULE_MODE, SCHEDULE_MODE_FOLLOW)
        self._hold_entities: list[str] = cfg.get(CONF_HOLD_ENTITIES, [])
        # Last known on/off of each keep-on entity — kept ourselves so an
        # unavailable entity holds its last value instead of reading as off.
        self._hold_states: dict[str, bool] = {}
        # Effective hold: companion switch off OR any keep-on entity on.
        self._held: bool = False
        # Start marker (ISO string) of the follow-mode window we last turned
        # lights on for and whose end we have not yet applied. Survives
        # restarts so missed boundaries are caught up exactly once while
        # manual overrides mid-window are respected.
        self._schedule_window_applied: str | None = None

        self._machine_state: str = STATE_IDLE
        self._attr_is_on = False
        self._timer_unsub: CALLBACK_TYPE | None = None
        # Context ids of our own light service calls, used to tell self-caused
        # state echoes apart from genuinely external changes.
        self._self_context_ids: deque[str] = deque(maxlen=16)

        self._last_on_physical: datetime | None = None
        self._last_on_virtual: datetime | None = None
        self._last_on_occupancy: datetime | None = None
        self._last_on_illuminance: datetime | None = None
        self._last_brightness_change_physical: datetime | None = None
        self._last_brightness_change_virtual: datetime | None = None

    # ------------------------------------------------------------------
    # HA lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Defer state-change subscriptions until HA has fully started.

        During startup, entities are restored and integrations initialize in
        arbitrary order, firing spurious state-change events; reacting to them
        could toggle lights or start countdowns based on incomplete state.
        """
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            # Restore turn-on attribution so the illuminance re-activation
            # countdown keeps working across a restart.
            for source in ("physical", "virtual", "occupancy", "illuminance"):
                raw = last.attributes.get(f"last_on_{source}")
                if raw:
                    try:
                        setattr(
                            self, f"_last_on_{source}", datetime.fromisoformat(raw)
                        )
                    except (ValueError, TypeError):
                        pass
            self._schedule_window_applied = last.attributes.get(
                "schedule_window_start"
            )
            for attr, field in (
                # Fall back to the pre-rename attribute name for restores
                # from before the physical/virtual split.
                ("last_brightness_change_physical", "_last_brightness_change_physical"),
                ("last_brightness_change", "_last_brightness_change_physical"),
                ("last_brightness_change_virtual", "_last_brightness_change_virtual"),
            ):
                raw = last.attributes.get(attr)
                if raw and getattr(self, field) is None:
                    try:
                        setattr(self, field, datetime.fromisoformat(raw))
                    except (ValueError, TypeError):
                        pass
            raw_brightness = last.attributes.get(ATTR_BRIGHTNESS)
            if isinstance(raw_brightness, int):
                self._attr_brightness = raw_brightness

        watch = list(self._lights)
        if self._occupancy_entity:
            watch.append(self._occupancy_entity)
        if self._maintain_entity and self._maintain_entity not in watch:
            watch.append(self._maintain_entity)
        if self._illuminance_entity:
            watch.append(self._illuminance_entity)
        if self._schedule_entity:
            watch.append(self._schedule_entity)
        watch.extend(self._hold_entities)

        @callback
        def _subscribe(_event=None) -> None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, watch, self._handle_state_change
                )
            )
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_AUTO_OFF_TOGGLED.format(self._entry_id),
                    self._on_auto_off_toggled,
                )
            )
            self._seed_state()

        if self.hass.state is CoreState.running:
            _subscribe()
        else:
            self.async_on_remove(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, _subscribe
                )
            )

    def _seed_state(self) -> None:
        """Initialise the machine state from current entity states after startup."""
        # Unavailable/unknown/missing keep-on entities count as not holding.
        self._hold_states = {
            e: (s := self.hass.states.get(e)) is not None and s.state == "on"
            for e in self._hold_entities
        }
        self._held = self._compute_held()

        self._attr_is_on = any(
            (s := self.hass.states.get(e)) and s.state == "on"
            for e in self._lights
        )

        if self._follow_schedule_seed():
            return

        # Occupancy only takes over when it's dark (or no illuminance is
        # configured) and inside any gate-mode schedule window.
        if (
            self._occupancy_entity
            and not self._is_illuminance_bright()
            and not self._gate_schedule_inactive()
        ):
            occ_state = self.hass.states.get(self._occupancy_entity)
            if occ_state and occ_state.state == "on":
                self._on_occupancy_change(occupied=True)
                return

        if self._attr_is_on and self._maintain_active():
            # Adopt an already-on light as maintained — no gating, since this
            # is not a turn-on; the maintain entity clearing starts the
            # countdown as usual.
            self._machine_state = STATE_OCCUPIED
            self.async_write_ha_state()
            return

        if self._attr_is_on:
            # Lights are already on (whatever the illuminance) — adopt them
            # and run the normal timer so they still turn off eventually.
            self._machine_state = STATE_ACTIVE
            self._start_timer()

        self.async_write_ha_state()

    def _follow_schedule_seed(self) -> bool:
        """Apply follow-mode schedule state at startup. Returns True if handled.

        The stored window marker distinguishes a boundary missed while HA was
        down (apply it now) from one we already handled before the restart
        (leave the lights alone — if they're off, the user turned them off).
        """
        if not self._schedule_entity or self._schedule_mode != SCHEDULE_MODE_FOLLOW:
            return False
        sched = self.hass.states.get(self._schedule_entity)
        if sched is None:
            return False

        if sched.state == "on":
            marker = sched.attributes.get("current_window_start")
            if marker and marker != self._schedule_window_applied:
                # Window started while HA was down — catch up now.
                self._apply_window_start(marker)
            elif self._attr_is_on:
                # Already handled this window before the restart; adopt.
                self._machine_state = STATE_SCHEDULED
                self.async_write_ha_state()
            else:
                # Already handled, lights off → manual override; respect it.
                self.async_write_ha_state()
            return True

        if self._schedule_window_applied is not None:
            if self._held and self._attr_is_on:
                # Auto-off is held — keep the marker so releasing the hold
                # applies the missed off boundary.
                self._machine_state = STATE_SCHEDULED
                self.async_write_ha_state()
                return True
            # Window ended while HA was down — apply the off boundary.
            self._schedule_window_applied = None
            self._machine_state = STATE_IDLE
            if self._attr_is_on:
                self.hass.async_create_task(self._set_lights(False))
            self.async_write_ha_state()
            return True

        return False

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_timer()

    # ------------------------------------------------------------------
    # LightEntity API
    # ------------------------------------------------------------------

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on all real lights and transition the state machine."""
        now = datetime.now(timezone.utc)
        self._last_on_virtual = now
        self._occupancy_lit_lights = False  # the user owns this on-period now
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is not None:
            self._last_brightness_change_virtual = now
            self._attr_brightness = brightness
        await self._set_lights(True, brightness=brightness)
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
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        same_state = old_state is not None and old_state.state == new_state.state

        if entity_id in self._lights:
            if event.context.id in self._self_context_ids:
                return  # echo of our own service call; call sites manage state
            if same_state:
                if new_state.state == "on":
                    self._on_light_brightness_change(old_state, new_state)
                return
            self._on_light_state_change(new_state.state)
        elif same_state:
            return  # attribute-only change (battery, ...)
        elif entity_id == self._occupancy_entity:
            self._on_occupancy_change(new_state.state == "on")
        elif entity_id == self._maintain_entity:
            self._on_maintain_change(new_state.state == "on")
        elif entity_id == self._illuminance_entity:
            self._on_illuminance_change(new_state.state == "on")
        elif entity_id == self._schedule_entity:
            self._on_schedule_change(new_state)
        elif entity_id in self._hold_entities:
            self._hold_states[entity_id] = new_state.state == "on"
            self._refresh_hold()

    def _on_light_state_change(self, state: str) -> None:
        """Handle a real light being turned on/off externally."""
        if state == "on":
            if self._machine_state == STATE_IDLE:
                self._last_on_physical = datetime.now(timezone.utc)
                self._occupancy_lit_lights = False  # the user owns this on-period
            self._transition_on(manual=True)
        else:
            if self._all_lights_off():
                self._go_idle()

    def _on_light_brightness_change(self, old_state, new_state) -> None:
        """Handle an external brightness change on a real light (state stays on).

        Dimming is human activity: record it and restart any running
        countdown with the full timeout. Brightness 0 means off in disguise.
        """
        old_b = old_state.attributes.get("brightness")
        new_b = new_state.attributes.get("brightness")
        if new_b is None or new_b == old_b:
            return  # some other attribute changed

        self._last_brightness_change_physical = datetime.now(timezone.utc)
        if new_b:
            self._attr_brightness = new_b

        if new_b == 0:
            if self._all_lights_off():
                self._go_idle()
            else:
                self.async_write_ha_state()
            return

        if self._machine_state == STATE_IDLE:
            # 0 → non-zero while we're idle is a turn-on in disguise: run the
            # normal external turn-on logic (last_on_physical, ACTIVE/timer
            # or rejoining an active follow window).
            self._on_light_state_change("on")
            self.async_write_ha_state()
            return

        if self._machine_state in (STATE_ACTIVE, STATE_COUNTDOWN):
            self._machine_state = STATE_ACTIVE
            self._start_timer()
        self.async_write_ha_state()

    def _all_lights_off(self) -> bool:
        """True when every real light is off (brightness 0 counts as off)."""
        for entity_id in self._lights:
            state = self.hass.states.get(entity_id)
            if state is None:
                return False  # unknown entity — don't assume it's off
            if state.state == "on" and state.attributes.get("brightness") != 0:
                return False
        return True

    def _is_illuminance_bright(self) -> bool:
        """Return True when illuminance is bright enough to suppress lighting."""
        if not self._illuminance_entity:
            return False
        state = self.hass.states.get(self._illuminance_entity)
        return state is not None and state.state == "on"

    def _gate_schedule_inactive(self) -> bool:
        """True when a gate-mode schedule forbids activating the lights."""
        if not self._schedule_entity or self._schedule_mode != SCHEDULE_MODE_GATE:
            return False
        state = self.hass.states.get(self._schedule_entity)
        return not (state is not None and state.state == "on")

    def _follow_schedule_state(self):
        """The schedule entity's state when in follow mode and ON, else None."""
        if not self._schedule_entity or self._schedule_mode != SCHEDULE_MODE_FOLLOW:
            return None
        state = self.hass.states.get(self._schedule_entity)
        return state if state is not None and state.state == "on" else None

    # ------------------------------------------------------------------
    # Auto-off hold
    # ------------------------------------------------------------------

    def _auto_off_enabled(self) -> bool:
        """State of the companion Auto-off switch (mirrored via hass.data)."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        return entry_data.get(DATA_AUTO_OFF_ENABLED, True)

    def _compute_held(self) -> bool:
        return not self._auto_off_enabled() or any(self._hold_states.values())

    @callback
    def _on_auto_off_toggled(self) -> None:
        self._refresh_hold()

    def _refresh_hold(self) -> None:
        """Re-derive the effective hold and act on engage/release edges."""
        held = self._compute_held()
        if held == self._held:
            return
        self._held = held
        if held:
            # Suspend any pending automatic off; the machine state stays put.
            self._cancel_timer()
        else:
            self._resume_after_hold_release()
        self.async_write_ha_state()

    def _resume_after_hold_release(self) -> None:
        """Return to normal behaviour when the last hold releases.

        Automatic turn-offs suppressed while held are applied from current
        conditions: a follow window that ended, being outside a gate window,
        or bright in control mode turn the lights off now; an active follow
        window or active occupancy keeps them on without a timer; otherwise
        a fresh full timer starts.
        """
        if not self._attr_is_on:
            return  # lights-off transitions were never suppressed

        sched = self._follow_schedule_state()
        if sched is not None:
            # Active follow window owns the lights — no timer.
            self._apply_window_start(sched.attributes.get("current_window_start"))
            return
        if (
            self._schedule_entity
            and self._schedule_mode == SCHEDULE_MODE_FOLLOW
            and self._schedule_window_applied is not None
        ):
            # The follow window we turned on for ended while held.
            self._schedule_window_applied = None
            self.hass.async_create_task(self._set_lights(False))
            self._go_idle()
            return

        if self._gate_schedule_inactive() or (
            self._is_illuminance_bright()
            and self._illuminance_mode == ILLUMINANCE_MODE_CONTROL
        ):
            self.hass.async_create_task(self._set_lights(False))
            self._go_idle()
            return

        if self._occupancy_active() or self._maintain_active():
            self._machine_state = STATE_OCCUPIED
            return

        self._machine_state = STATE_ACTIVE
        self._start_timer()

    # ------------------------------------------------------------------
    # Schedule handling
    # ------------------------------------------------------------------

    def _on_schedule_change(self, new_state) -> None:
        """Handle the virtual schedule sensor changing."""
        if self._schedule_mode == SCHEDULE_MODE_FOLLOW:
            if new_state.state == "on":
                marker = new_state.attributes.get("current_window_start")
                if marker and marker == self._schedule_window_applied:
                    # Same window we already applied — the schedule entity
                    # blipped unavailable and recovered mid-window. A manual
                    # off in between stands, mirroring the restart seed.
                    if self._attr_is_on:
                        self._machine_state = STATE_SCHEDULED
                        self._cancel_timer()
                        self.async_write_ha_state()
                    return
                self._apply_window_start(marker)
            elif self._held:
                # Auto-off held — keep the window marker so releasing the
                # hold applies this off boundary.
                pass
            else:
                # Window ended — apply the off boundary.
                self._schedule_window_applied = None
                self.hass.async_create_task(self._set_lights(False))
                self._go_idle()
            return

        # Gate mode: window end forces lights off; window start re-evaluates
        # occupancy the same way illuminance going dark does.
        if new_state.state != "on":
            if self._machine_state != STATE_IDLE and not self._held:
                self.hass.async_create_task(self._set_lights(False))
                self._go_idle()
        else:
            if self._machine_state != STATE_IDLE:
                return
            occ_state = (
                self.hass.states.get(self._occupancy_entity)
                if self._occupancy_entity
                else None
            )
            if (
                occ_state
                and occ_state.state == "on"
                and not self._is_illuminance_bright()
            ):
                self._last_on_occupancy = datetime.now(timezone.utc)
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
                self._occupancy_lit_lights = True
                self.hass.async_create_task(self._set_lights(True))
                self.async_write_ha_state()

    def _apply_window_start(self, marker: str | None) -> None:
        """Enter the SCHEDULED state and turn the lights on (follow mode)."""
        self._schedule_window_applied = marker
        self._machine_state = STATE_SCHEDULED
        self._cancel_timer()
        if not self._attr_is_on:
            self.hass.async_create_task(self._set_lights(True))
        self.async_write_ha_state()

    def _on_occupancy_change(self, occupied: bool) -> None:
        """Handle the virtual occupancy sensor changing."""
        if self._machine_state == STATE_SCHEDULED:
            return  # follow-mode window owns the lights
        if occupied:
            if self._is_illuminance_bright():
                # Bright enough — suppress lights; when illuminance turns off we
                # re-evaluate occupancy from the sensor's current state.
                return
            if self._gate_schedule_inactive():
                # Outside the schedule window — occupancy may not turn lights
                # on; window start re-evaluates occupancy.
                return
            self._last_on_occupancy = datetime.now(timezone.utc)
            self._machine_state = STATE_OCCUPIED
            self._cancel_timer()
            if not self._attr_is_on:
                self._occupancy_lit_lights = True
                self.hass.async_create_task(self._set_lights(True))
            self.async_write_ha_state()
        else:
            if self._machine_state == STATE_OCCUPIED:
                if self._maintain_active():
                    return  # maintain entity holds the light on
                self._machine_state = STATE_COUNTDOWN
                if self._occupancy_lit_lights and self._occupancy_clear_was_false():
                    # The whole cycle was a false detection and nobody else
                    # asked for these lights — turn them off quickly.
                    self._start_timer(self._false_off_delay)
                else:
                    self._start_timer(self._compute_occupancy_countdown())
                self.async_write_ha_state()

    def _on_maintain_change(self, maintained: bool) -> None:
        """Handle the maintain occupancy entity changing.

        Holds an already-on light on while occupied; never turns lights on.
        """
        if self._machine_state == STATE_SCHEDULED:
            return  # follow-mode window owns the lights
        if maintained:
            # Not a turn-on, so no illuminance/schedule gating: an on light
            # is simply adopted; an off light stays off.
            if self._machine_state in (STATE_ACTIVE, STATE_COUNTDOWN):
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
                self.async_write_ha_state()
        else:
            if self._machine_state != STATE_OCCUPIED:
                return
            if self._occupancy_active():
                return  # regular occupancy still holds the light on
            self._machine_state = STATE_COUNTDOWN
            if (
                self._occupancy_lit_lights
                and self._occupancy_clear_was_false()
                and self._clear_was_false(self._maintain_entity)
            ):
                # Both sensors flagged their clears false — the whole episode
                # was a false detection; a genuine presence on either side
                # earns the normal countdown instead.
                self._start_timer(self._false_off_delay)
            else:
                self._start_timer(self._compute_occupancy_countdown())
            self.async_write_ha_state()

    def _occupancy_active(self) -> bool:
        """True when the regular occupancy entity is configured and on."""
        if not self._occupancy_entity:
            return False
        state = self.hass.states.get(self._occupancy_entity)
        return state is not None and state.state == "on"

    def _maintain_active(self) -> bool:
        """True when the maintain occupancy entity is configured and on."""
        if not self._maintain_entity:
            return False
        state = self.hass.states.get(self._maintain_entity)
        return state is not None and state.state == "on"

    def _occupancy_clear_was_false(self) -> bool:
        """True when the occupancy sensor flagged its clear as a false detection."""
        return self._clear_was_false(self._occupancy_entity)

    def _clear_was_false(self, entity_id: str | None) -> bool:
        """True when the given sensor flagged its clear as a false detection."""
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return bool(
            state is not None
            and state.attributes.get("last_clear_false_detection")
        )

    def _on_illuminance_change(self, is_bright: bool) -> None:
        """Handle the virtual illuminance sensor changing.

        is_bright=True  (illuminance ON  = bright): natural light is sufficient
                        → turn off artificial lights if they were on.
        is_bright=False (illuminance OFF = dark):   need artificial light
                        → turn on if currently occupied, or if the occupancy
                          countdown still has time remaining.
        """
        if self._machine_state == STATE_SCHEDULED:
            return  # follow-mode window owns the lights
        if is_bright:
            if self._illuminance_mode == ILLUMINANCE_MODE_GATE:
                # Gate-only: bright never forces the lights off (breaks the
                # feedback loop when the lux sensor sees the controlled
                # lights). Occupancy/timeout handle turning off.
                return
            if self._held:
                # Auto-off held — releasing the hold re-checks brightness.
                return
            if self._machine_state != STATE_IDLE:
                self.hass.async_create_task(self._set_lights(False))
                self._go_idle()
        else:
            if self._machine_state != STATE_IDLE:
                return  # already running; no change needed
            if self._gate_schedule_inactive():
                return  # outside the schedule window — no activation

            # Check whether we should activate due to occupancy or recent history.
            occ_state = (
                self.hass.states.get(self._occupancy_entity)
                if self._occupancy_entity
                else None
            )
            if occ_state and occ_state.state == "on":
                self._last_on_illuminance = datetime.now(timezone.utc)
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
                self._occupancy_lit_lights = True
                self.hass.async_create_task(self._set_lights(True))
                self.async_write_ha_state()
            else:
                countdown = self._compute_illuminance_countdown()
                if countdown > 0:
                    self._last_on_illuminance = datetime.now(timezone.utc)
                    self.hass.async_create_task(self._set_lights(True))
                    if self._maintain_active():
                        # Recent history justified the turn-on; the maintain
                        # entity now holds the re-lit light.
                        self._machine_state = STATE_OCCUPIED
                        self._cancel_timer()
                    else:
                        self._machine_state = STATE_COUNTDOWN
                        self._start_timer(countdown)
                    self.async_write_ha_state()

    def _compute_occupancy_countdown(self) -> int:
        """Seconds to wait after occupancy clears before turning lights off.

        Anchors to the latest_occupied_time across the occupancy and maintain
        entities (whichever saw the person last) so that each sub-sensor's
        individual timeout is respected, then adds light_timeout on top as an
        extra grace period.
        """
        base = self._light_timeout
        lots: list[datetime] = []
        for entity_id in (self._occupancy_entity, self._maintain_entity):
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            lot_str = state.attributes.get("latest_occupied_time")
            if lot_str:
                try:
                    lots.append(datetime.fromisoformat(lot_str))
                except (ValueError, TypeError):
                    pass
        if lots:
            now = datetime.now(timezone.utc)
            remaining = (max(lots) - now).total_seconds()
            return max(0, int(base + remaining))
        return base

    def _most_recent_on_time(self) -> datetime | None:
        """Return the latest recorded turn-on timestamp across all sources
        except for illuminance (which is only relevant for gating, not attribution)."""
        candidates = [
            t for t in (
                self._last_on_physical,
                self._last_on_virtual,
                self._last_on_occupancy,
            )
            if t is not None
        ]
        return max(candidates) if candidates else None

    def _compute_illuminance_countdown(self) -> int:
        """Countdown (seconds) to use when illuminance going dark re-activates lights.

        When an occupancy entity is configured, delegates to the occupancy-based
        countdown which is already anchored to latest_occupied_time.

        When there is no occupancy entity, subtracts elapsed time since the light
        was last on from light_timeout, so the re-activation uses only the
        remaining portion of the original on-period.
        """
        if self._occupancy_entity:
            return self._compute_occupancy_countdown()

        last_on = self._most_recent_on_time()
        if last_on is not None:
            elapsed = (datetime.now(timezone.utc) - last_on).total_seconds()
            return max(0, int(self._light_timeout - elapsed))
        return self._light_timeout

    def _transition_on(self, manual: bool = False) -> None:
        """Move to ACTIVE (or stay OCCUPIED/SCHEDULED) when lights come on."""
        if self._machine_state in (STATE_OCCUPIED, STATE_SCHEDULED):
            return  # already managed by occupancy / schedule window

        # Turned back on during an active follow-mode window (after a manual
        # off): rejoin the window instead of running the auto-off timer, so
        # the light stays on until the window ends.
        sched = self._follow_schedule_state()
        if sched is not None:
            self._attr_is_on = True
            self._apply_window_start(sched.attributes.get("current_window_start"))
            return

        # Turned on while the maintain entity is already occupied: hold the
        # light immediately instead of running a timer that would expire
        # despite presence.
        if self._maintain_active():
            self._machine_state = STATE_OCCUPIED
            self._attr_is_on = True
            self._cancel_timer()
            self.async_write_ha_state()
            return

        self._machine_state = STATE_ACTIVE
        self._attr_is_on = True
        # Restart even when already ACTIVE: turning on / dimming again is
        # activity and extends the on-period.
        self._start_timer()
        self.async_write_ha_state()

    def _go_idle(self) -> None:
        """Cancel any timer and move to IDLE."""
        self._cancel_timer()
        self._machine_state = STATE_IDLE
        self._attr_is_on = False
        self._occupancy_lit_lights = False
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Timer helpers
    # ------------------------------------------------------------------

    def _start_timer(self, duration: int | None = None) -> None:
        self._cancel_timer()
        if self._held:
            # Auto-off held — the state machine transitions normally but no
            # timer is armed; releasing the hold starts a fresh one.
            return
        self._timer_unsub = async_call_later(
            self.hass,
            duration if duration is not None else self._light_timeout,
            self._timer_expired,
        )

    async def _timer_expired(self, _now: datetime) -> None:
        self._timer_unsub = None
        if self._held:
            return  # engaged in the same loop iteration the timer fired
        await self._set_lights(False)
        self._go_idle()

    def _cancel_timer(self) -> None:
        if self._timer_unsub is not None:
            self._timer_unsub()
            self._timer_unsub = None

    # ------------------------------------------------------------------
    # Real-light control
    # ------------------------------------------------------------------

    async def _set_lights(self, on: bool, brightness: int | None = None) -> None:
        context = Context()
        self._self_context_ids.append(context.id)
        service_data: dict = {"entity_id": self._lights}
        if on and brightness is not None:
            service_data[ATTR_BRIGHTNESS] = brightness
        await self.hass.services.async_call(
            "light",
            "turn_on" if on else "turn_off",
            service_data,
            blocking=False,
            context=context,
        )
        self._attr_is_on = on
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Extra state attributes
    # ------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict:
        def _fmt(t: datetime | None) -> str | None:
            return t.isoformat() if t else None

        return {
            "molight_state": self._machine_state,
            "auto_off_held": self._held,
            "last_on_physical": _fmt(self._last_on_physical),
            "last_on_virtual": _fmt(self._last_on_virtual),
            "last_on_occupancy": _fmt(self._last_on_occupancy),
            "last_on_illuminance": _fmt(self._last_on_illuminance),
            "last_brightness_change_physical": _fmt(
                self._last_brightness_change_physical
            ),
            "last_brightness_change_virtual": _fmt(
                self._last_brightness_change_virtual
            ),
            "schedule_window_start": self._schedule_window_applied,
        }
