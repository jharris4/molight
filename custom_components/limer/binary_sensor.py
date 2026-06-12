"""Virtual binary sensor platform for Limer.

Provides four sensor types, all created via the config flow:

  VirtualOccupancySensor         — wraps one real sensor with a timeout;
                                   exposes latest_occupied_time = last_off - timeout
  VirtualCombinedOccupancySensor — combines VirtualOccupancySensors using
                                   trigger/maintain logic; latest_occupied_time
                                   is the max across all constituents
  VirtualIlluminanceSensor       — compares a real illuminance sensor to a threshold
  VirtualScheduleSensor          — evaluates configurable time windows
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

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
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
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
        ENTITY_TYPE_COMBINED_OCCUPANCY: VirtualCombinedOccupancySensor,
        ENTITY_TYPE_ILLUMINANCE: VirtualIlluminanceSensor,
        ENTITY_TYPE_SCHEDULE: VirtualScheduleSensor,
    }

    cls = entity_map.get(entity_type)
    if cls is not None:
        async_add_entities([cls(hass, entry)])


# ---------------------------------------------------------------------------
# Virtual Occupancy Binary Sensor (simple)
# ---------------------------------------------------------------------------


class VirtualOccupancySensor(BinarySensorEntity):
    """Wraps a single real binary sensor with an occupancy timeout.

    is_on mirrors the real sensor directly (no countdown).
    latest_occupied_time = last_turn_off - timeout, representing our best
    estimate of when the person actually left.
    """

    _attr_device_class = "occupancy"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._attr_name = entry.data[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self._source_sensor: str = entry.data[CONF_OCCUPANCY_SENSOR]
        self._timeout: int = int(entry.data.get(CONF_OCCUPANCY_TIMEOUT, 0))
        self._attr_is_on = False
        self._latest_occupied_time: datetime | None = None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_sensor], self._handle_sensor_change
            )
        )
        state = self.hass.states.get(self._source_sensor)
        if state:
            self._attr_is_on = state.state == "on"

    @callback
    def _handle_sensor_change(self, event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        if new_state.state == "on":
            self._attr_is_on = True
        else:
            candidate = datetime.now(timezone.utc) - timedelta(seconds=self._timeout)
            if (
                self._latest_occupied_time is None
                or candidate > self._latest_occupied_time
            ):
                self._latest_occupied_time = candidate
            self._attr_is_on = False
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict:
        lot = self._latest_occupied_time
        return {
            "latest_occupied_time": lot.isoformat() if lot else None,
            "occupancy_timeout": self._timeout,
        }


# ---------------------------------------------------------------------------
# Virtual Combined Occupancy Binary Sensor
# ---------------------------------------------------------------------------


class VirtualCombinedOccupancySensor(BinarySensorEntity):
    """Combines multiple VirtualOccupancySensors using trigger/maintain logic.

    Trigger sensors start occupancy; maintain sensors keep it alive once started.
    latest_occupied_time is the max of all constituents' latest_occupied_time
    attributes, propagated whenever a constituent turns off.
    """

    _attr_device_class = "occupancy"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._attr_name = entry.data[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self._trigger_sensors: list[str] = entry.data.get(CONF_TRIGGER_SENSORS, [])
        self._maintain_sensors: list[str] = entry.data.get(CONF_MAINTAIN_SENSORS, [])
        self._attr_is_on = False
        self._latest_occupied_time: datetime | None = None

    async def async_added_to_hass(self) -> None:
        all_sensors = self._trigger_sensors + self._maintain_sensors
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, all_sensors, self._handle_occupancy_change
            )
        )

    @callback
    def _handle_occupancy_change(self, event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if new_state.state != "on":
            lot_str = new_state.attributes.get("latest_occupied_time")
            if lot_str:
                try:
                    lot = datetime.fromisoformat(lot_str)
                    if (
                        self._latest_occupied_time is None
                        or lot > self._latest_occupied_time
                    ):
                        self._latest_occupied_time = lot
                except (ValueError, TypeError):
                    pass

        if self._any_on(self._trigger_sensors):
            self._attr_is_on = True
        elif self._attr_is_on and self._any_on(self._maintain_sensors):
            pass
        else:
            self._attr_is_on = False

        self.async_write_ha_state()

    def _any_on(self, sensors: list[str]) -> bool:
        return any(
            (s := self.hass.states.get(e)) and s.state == "on"
            for e in sensors
        )

    @property
    def extra_state_attributes(self) -> dict:
        lot = self._latest_occupied_time
        return {"latest_occupied_time": lot.isoformat() if lot else None}


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
