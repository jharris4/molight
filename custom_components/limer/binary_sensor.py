"""Virtual binary sensor platform for Limer.

Provides three sensor types, all created via the config flow:

  VirtualOccupancySensor   — combines trigger + maintain sensors with a timeout
  VirtualIlluminanceSensor — compares a real illuminance sensor to a threshold
  VirtualScheduleSensor    — evaluates configurable time windows
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Limer binary sensor entities from a config entry."""
    entity_type = entry.data[CONF_ENTITY_TYPE]

    entity_map = {
        ENTITY_TYPE_OCCUPANCY: VirtualOccupancySensor,
        ENTITY_TYPE_ILLUMINANCE: VirtualIlluminanceSensor,
        ENTITY_TYPE_SCHEDULE: VirtualScheduleSensor,
    }

    cls = entity_map.get(entity_type)
    if cls is not None:
        async_add_entities([cls(hass, entry)])


# ---------------------------------------------------------------------------
# Virtual Occupancy Binary Sensor
# ---------------------------------------------------------------------------


class VirtualOccupancySensor(BinarySensorEntity):
    """Binary sensor that synthesises occupancy from N real sensors.

    Logic:
      • Any *trigger* sensor going ON starts occupancy (is_on → True).
      • *Maintain* sensors can keep occupancy alive once started, but
        cannot start it on their own.
      • When ALL sensors (trigger + maintain) are OFF, a countdown begins.
        If no new activity arrives before the timeout, occupancy ends.
    """

    _attr_device_class = "occupancy"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_name = entry.data[CONF_NAME]
        self._attr_unique_id = entry.entry_id

        self._trigger_sensors: list[str] = entry.data.get(CONF_TRIGGER_SENSORS, [])
        self._maintain_sensors: list[str] = entry.data.get(CONF_MAINTAIN_SENSORS, [])
        self._timeout: int = int(entry.data.get(CONF_OCCUPANCY_TIMEOUT, 120))

        self._attr_is_on = False
        self._countdown_task: asyncio.Task | None = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to state changes of all tracked sensors."""
        all_sensors = self._trigger_sensors + self._maintain_sensors
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, all_sensors, self._handle_sensor_change
            )
        )

    @callback
    def _handle_sensor_change(self, event) -> None:
        """React to a tracked sensor changing state."""
        entity_id = event.data["entity_id"]
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        is_active = new_state.state == "on"

        if is_active:
            # Cancel any running countdown — something is on.
            self._cancel_countdown()

            if entity_id in self._trigger_sensors:
                # A trigger sensor fired — start / maintain occupancy.
                self._attr_is_on = True
                self.async_write_ha_state()
            elif self._attr_is_on:
                # A maintain sensor fired while already occupied — keep going.
                self.async_write_ha_state()
        else:
            # A sensor turned off — check if everything is now quiet.
            if self._all_sensors_off():
                self._start_countdown()

    def _all_sensors_off(self) -> bool:
        """Return True when every tracked sensor reports off/unavailable."""
        for entity_id in self._trigger_sensors + self._maintain_sensors:
            state = self.hass.states.get(entity_id)
            if state and state.state == "on":
                return False
        return True

    def _start_countdown(self) -> None:
        """Begin the timeout countdown to clear occupancy."""
        if not self._attr_is_on:
            return
        self._cancel_countdown()
        self._countdown_task = self.hass.async_create_task(self._run_countdown())

    async def _run_countdown(self) -> None:
        """Sleep for the configured timeout, then clear occupancy."""
        try:
            await asyncio.sleep(self._timeout)
        except asyncio.CancelledError:
            return
        self._attr_is_on = False
        self.async_write_ha_state()

    def _cancel_countdown(self) -> None:
        if self._countdown_task and not self._countdown_task.done():
            self._countdown_task.cancel()
        self._countdown_task = None

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_countdown()


# ---------------------------------------------------------------------------
# Virtual Illuminance Binary Sensor
# ---------------------------------------------------------------------------


class VirtualIlluminanceSensor(BinarySensorEntity):
    """Binary sensor that is ON when a real illuminance sensor is below a threshold.

    ON  → dark enough to warrant lighting  (value < threshold)
    OFF → bright enough, no lighting needed (value >= threshold)
    """

    _attr_device_class = "light"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_name = entry.data[CONF_NAME]
        self._attr_unique_id = entry.entry_id

        self._source_entity: str = entry.data[CONF_ILLUMINANCE_SENSOR]
        self._threshold: float = float(entry.data.get(CONF_ILLUMINANCE_THRESHOLD, 10.0))

        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Subscribe to the source illuminance sensor and seed the initial state."""
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity], self._handle_illuminance_change
            )
        )
        # Seed state from current value.
        state = self.hass.states.get(self._source_entity)
        if state:
            self._update_from_state(state.state)

    @callback
    def _handle_illuminance_change(self, event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        self._update_from_state(new_state.state)
        self.async_write_ha_state()

    def _update_from_state(self, state_value: str) -> None:
        try:
            self._attr_is_on = float(state_value) < self._threshold
        except (ValueError, TypeError):
            self._attr_is_on = False


# ---------------------------------------------------------------------------
# Virtual Schedule Binary Sensor
# ---------------------------------------------------------------------------


class VirtualScheduleSensor(BinarySensorEntity):
    """Binary sensor that is ON when the current time falls in any configured window.

    Each window is a dict: {"start": "HH:MM", "end": "HH:MM"}.
    Overnight windows (start > end) are supported.
    """

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_name = entry.data[CONF_NAME]
        self._attr_unique_id = entry.entry_id

        self._windows: list[dict[str, str]] = entry.data.get(CONF_TIME_WINDOWS, [])
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Evaluate immediately, then re-evaluate every minute."""
        self._evaluate()
        from datetime import timedelta

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._tick, timedelta(minutes=1)
            )
        )

    @callback
    def _tick(self, _now) -> None:
        self._evaluate()
        self.async_write_ha_state()

    def _evaluate(self) -> None:
        now = datetime.now().time().replace(second=0, microsecond=0)
        self._attr_is_on = any(self._in_window(now, w) for w in self._windows)

    @staticmethod
    def _in_window(now: time, window: dict[str, str]) -> bool:
        try:
            start = time.fromisoformat(window["start"])
            end = time.fromisoformat(window["end"])
        except (KeyError, ValueError):
            return False

        if start <= end:
            return start <= now < end
        # Overnight window: e.g. 22:00 → 06:00
        return now >= start or now < end
