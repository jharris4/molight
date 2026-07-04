"""Virtual binary sensor platform for MoLight.

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
from datetime import date, datetime, time, timedelta, timezone

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import (
    COMBINE_LATEST,
    CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_ILLUMINANCE_HYSTERESIS,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    EDGE_COMBINE,
    EDGE_OFFSET,
    EDGE_SUN,
    EDGE_TIME,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
    SUN_EVENTS,
)
from .helpers import molight_config

_LOGGER = logging.getLogger(__name__)


def _real_state_change(event) -> bool:
    """Return True when the event is an actual state transition.

    Filters out entities going unavailable/unknown (sensor blips must not be
    read as occupancy or darkness changes) and attribute-only updates (many
    real sensors push battery/lux attributes while their state is unchanged).
    """
    new_state = event.data.get("new_state")
    old_state = event.data.get("old_state")
    if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return False
    return old_state is None or old_state.state != new_state.state


def _restored_latest_occupied_time(last_state) -> datetime | None:
    """Parse latest_occupied_time from a restored state, if present and valid."""
    if last_state is None:
        return None
    raw = last_state.attributes.get("latest_occupied_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MoLight binary sensor entities from a config entry."""
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


class VirtualOccupancySensor(BinarySensorEntity, RestoreEntity):
    """Wraps a single real binary sensor with an occupancy timeout.

    is_on mirrors the real sensor directly (no countdown).
    latest_occupied_time = last_turn_off - timeout, representing our best
    estimate of when the person actually left.

    False-detection classification (when false_detection_grace > 0): a cycle
    whose on-duration exceeds the timeout by no more than the grace contained
    exactly one instantaneous detection — the sensor never re-triggered
    during its hold time, so it was almost certainly a fly/heat blip, or a
    brief pass-through. Such cycles don't advance latest_occupied_time, are
    counted in false_detection_count, and flag the clear via
    last_clear_false_detection so lights can turn off quickly.

    Clear-on-unavailable (when clear_on_unavailable_timeout > 0): a source
    that stays unavailable/unknown while occupancy is active would otherwise
    hold occupancy — and any lights it lit — on forever. Instead, the person
    is assumed present right up to the dropout: latest_occupied_time advances
    to that moment immediately, and if the source hasn't recovered after the
    timeout the occupancy is cleared, flagged via last_clear_unavailable.
    Such clears are never classified as false detections (the room may well
    still be occupied), so dependent lights run their normal gentle countdown.
    A recovery cancels the pending clear: straight to "off" is processed as a
    real clear, straight to "on" simply continues the occupancy.
    """

    _attr_device_class = "occupancy"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        cfg = molight_config(entry)
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self._source_sensor: str = cfg[CONF_OCCUPANCY_SENSOR]
        self._timeout: int = int(cfg.get(CONF_OCCUPANCY_TIMEOUT, 0))
        self._grace: float = float(cfg.get(CONF_FALSE_DETECTION_GRACE, 0))
        self._unavailable_timeout: int = int(
            cfg.get(
                CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
                DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT,
            )
        )
        self._attr_is_on = False
        self._latest_occupied_time: datetime | None = None
        self._last_on_time: datetime | None = None
        self._false_count: int = 0
        self._last_clear_false: bool = False
        self._last_clear_unavailable: bool = False
        self._unavailable_unsub: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self._latest_occupied_time = _restored_latest_occupied_time(last)
        if last is not None:
            try:
                self._false_count = int(
                    last.attributes.get("false_detection_count") or 0
                )
            except (ValueError, TypeError):
                pass
            raw = last.attributes.get("last_on_time")
            if raw:
                try:
                    self._last_on_time = datetime.fromisoformat(raw)
                except (ValueError, TypeError):
                    pass
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_sensor], self._handle_sensor_change
            )
        )
        self.async_on_remove(self._cancel_unavailable_timer)
        self._seed_state()

    def _seed_state(self) -> None:
        state = self.hass.states.get(self._source_sensor)
        if state:
            self._attr_is_on = state.state == "on"
            if self._attr_is_on and self._last_on_time is None:
                # Restart mid-cycle without a restored on-time: the source's
                # last_changed is our best estimate.
                self._last_on_time = state.last_changed
        self.async_write_ha_state()

    @callback
    def _handle_sensor_change(self, event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._on_source_unavailable()
            return
        if not _real_state_change(event):
            return
        self._cancel_unavailable_timer()
        if new_state.state == "on":
            # A recovery from unavailable while already occupied continues the
            # running cycle — restamping last_on_time here would make a later
            # clear measure the on-duration from the recovery moment and
            # misclassify a long occupancy as a false detection.
            if not self._attr_is_on:
                self._last_on_time = datetime.now(timezone.utc)
            self._attr_is_on = True
        else:
            if not self._attr_is_on:
                # Already cleared (the unavailable timeout fired) — a late
                # recovery straight to "off" must not re-process the clear,
                # which would advance latest_occupied_time past the dropout
                # and overwrite the clear's classification.
                return
            now = datetime.now(timezone.utc)
            self._last_clear_false = self._is_false_cycle(now)
            self._last_clear_unavailable = False
            if self._last_clear_false:
                self._false_count += 1
            else:
                candidate = now - timedelta(seconds=self._timeout)
                if (
                    self._latest_occupied_time is None
                    or candidate > self._latest_occupied_time
                ):
                    self._latest_occupied_time = candidate
            self._attr_is_on = False
        self.async_write_ha_state()

    @callback
    def _on_source_unavailable(self) -> None:
        """Start the clear-on-unavailable countdown for an occupied dropout."""
        if (
            self._unavailable_timeout <= 0
            or not self._attr_is_on
            or self._unavailable_unsub is not None
        ):
            return
        # The person may have been present right up to the dropout — advance
        # latest_occupied_time now, whether or not the source recovers.
        now = datetime.now(timezone.utc)
        if self._latest_occupied_time is None or now > self._latest_occupied_time:
            self._latest_occupied_time = now
        self._unavailable_unsub = async_call_later(
            self.hass, self._unavailable_timeout, self._unavailable_expired
        )
        self.async_write_ha_state()

    @callback
    def _unavailable_expired(self, _now: datetime) -> None:
        self._unavailable_unsub = None
        self._attr_is_on = False
        self._last_clear_false = False
        self._last_clear_unavailable = True
        self.async_write_ha_state()

    @callback
    def _cancel_unavailable_timer(self) -> None:
        if self._unavailable_unsub is not None:
            self._unavailable_unsub()
            self._unavailable_unsub = None

    def _is_false_cycle(self, now: datetime) -> bool:
        if self._grace <= 0 or self._last_on_time is None:
            return False
        on_duration = (now - self._last_on_time).total_seconds()
        return on_duration - self._timeout <= self._grace

    @property
    def extra_state_attributes(self) -> dict:
        lot = self._latest_occupied_time
        return {
            "latest_occupied_time": lot.isoformat() if lot else None,
            "occupancy_timeout": self._timeout,
            "last_on_time": (
                self._last_on_time.isoformat() if self._last_on_time else None
            ),
            "last_clear_false_detection": self._last_clear_false,
            "false_detection_count": self._false_count,
            "last_clear_unavailable": self._last_clear_unavailable,
        }


# ---------------------------------------------------------------------------
# Virtual Combined Occupancy Binary Sensor
# ---------------------------------------------------------------------------


class VirtualCombinedOccupancySensor(BinarySensorEntity, RestoreEntity):
    """Combines multiple VirtualOccupancySensors using trigger/maintain logic.

    Trigger sensors start occupancy; maintain sensors keep it alive once started.
    latest_occupied_time is the max of all constituents' latest_occupied_time
    attributes, propagated whenever a constituent turns off.

    False-detection classification: constituents that classify a clear as a
    false detection don't advance their latest_occupied_time, so a combined
    cycle during which our own latest_occupied_time never advanced was made
    up entirely of false (or stale) cycles — count it and flag the clear.
    """

    _attr_device_class = "occupancy"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        cfg = molight_config(entry)
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self._trigger_sensors: list[str] = cfg.get(CONF_TRIGGER_SENSORS, [])
        self._maintain_sensors: list[str] = cfg.get(CONF_MAINTAIN_SENSORS, [])
        self._attr_is_on = False
        self._latest_occupied_time: datetime | None = None
        self._cycle_start_lot: datetime | None = None
        self._false_count: int = 0
        self._last_clear_false: bool = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self._latest_occupied_time = _restored_latest_occupied_time(last)
        if last is not None:
            try:
                self._false_count = int(
                    last.attributes.get("false_detection_count") or 0
                )
            except (ValueError, TypeError):
                pass
        all_sensors = list(
            dict.fromkeys(self._trigger_sensors + self._maintain_sensors)
        )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, all_sensors, self._handle_occupancy_change
            )
        )
        self._seed_state()

    def _seed_state(self) -> None:
        if self._any_on(self._trigger_sensors):
            self._attr_is_on = True
        else:
            # Intentional startup exception to "maintain sensors never start
            # occupancy": after a restart we can't know whether occupancy was
            # already triggered before HA went down. A maintain sensor that has
            # been on for more than 5 seconds is assumed to reflect ongoing
            # occupancy from before the restart, so it seeds the sensor on
            # (and thus can turn lights on). The 5-second minimum filters out
            # sensors that merely flapped on during startup itself.
            now = datetime.now(timezone.utc)
            for entity_id in self._maintain_sensors:
                state = self.hass.states.get(entity_id)
                if (
                    state
                    and state.state == "on"
                    and (now - state.last_changed).total_seconds() > 5
                ):
                    self._attr_is_on = True
                    break
        if self._attr_is_on:
            self._cycle_start_lot = self._latest_occupied_time
        self.async_write_ha_state()

    @callback
    def _handle_occupancy_change(self, event) -> None:
        if not _real_state_change(event):
            return
        new_state = event.data["new_state"]

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

        was_on = self._attr_is_on
        if self._any_on(self._trigger_sensors):
            self._attr_is_on = True
        elif self._attr_is_on and self._any_on(self._maintain_sensors):
            pass
        else:
            self._attr_is_on = False

        if not was_on and self._attr_is_on:
            self._cycle_start_lot = self._latest_occupied_time
        elif was_on and not self._attr_is_on:
            self._last_clear_false = (
                self._latest_occupied_time == self._cycle_start_lot
            )
            if self._last_clear_false:
                self._false_count += 1

        self.async_write_ha_state()

    def _any_on(self, sensors: list[str]) -> bool:
        return any(
            (s := self.hass.states.get(e)) and s.state == "on"
            for e in sensors
        )

    @property
    def extra_state_attributes(self) -> dict:
        lot = self._latest_occupied_time
        return {
            "latest_occupied_time": lot.isoformat() if lot else None,
            "last_clear_false_detection": self._last_clear_false,
            "false_detection_count": self._false_count,
        }


# ---------------------------------------------------------------------------
# Virtual Illuminance Binary Sensor
# ---------------------------------------------------------------------------


class VirtualIlluminanceSensor(BinarySensorEntity, RestoreEntity):
    """Binary sensor tracking whether illuminance meets a threshold.

    ON  → bright enough; no artificial lighting needed
    OFF → too dark; artificial lighting may be required

    An optional hysteresis band suppresses flapping when the reading hovers
    around the threshold: the state only becomes bright at
    threshold + hysteresis and only becomes dark below
    threshold - hysteresis; readings inside the band hold the current state.
    """

    _attr_device_class = "light"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        cfg = molight_config(entry)
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id

        self._source_entity: str = cfg[CONF_ILLUMINANCE_SENSOR]
        self._threshold: float = float(cfg.get(CONF_ILLUMINANCE_THRESHOLD, 10.0))
        self._hysteresis: float = float(cfg.get(CONF_ILLUMINANCE_HYSTERESIS, 0.0))

        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity], self._handle_illuminance_change
            )
        )
        self._seed_state()

    def _seed_state(self) -> None:
        state = self.hass.states.get(self._source_entity)
        if state:
            self._update_from_state(state.state)
        self.async_write_ha_state()

    @callback
    def _handle_illuminance_change(self, event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return  # hold last known value while the source is unavailable
        self._update_from_state(new_state.state)
        self.async_write_ha_state()

    def _update_from_state(self, state_value: str) -> None:
        try:
            value = float(state_value)
        except (ValueError, TypeError):
            return  # unparsable reading — hold last known value
        if self._attr_is_on:
            if value < self._threshold - self._hysteresis:
                self._attr_is_on = False
        elif value >= self._threshold + self._hysteresis:
            self._attr_is_on = True


# ---------------------------------------------------------------------------
# Virtual Schedule Binary Sensor
# ---------------------------------------------------------------------------


class VirtualScheduleSensor(BinarySensorEntity):
    """Binary sensor that is ON when the current time falls in any configured window.

    Each window is {"start": <edge>, "end": <edge>} where an edge is either a
    plain "HH:MM" string or {"time": "HH:MM", "sun": "sunset"|"sunrise",
    "offset": <minutes>, "combine": "latest"|"earliest"} — e.g. start at the
    later of sunset−15min and 21:00. Overnight windows (end before start)
    roll the end to the next day.

    Rather than polling, the sensor resolves concrete boundary datetimes and
    schedules a single callback for the next transition, so state flips at the
    exact boundary. current_window_start identifies the active window; virtual
    lights in follow mode use it as a marker for restart catch-up.
    """

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        cfg = molight_config(entry)
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id

        self._windows: list[dict] = cfg.get(CONF_TIME_WINDOWS, [])
        self._attr_is_on = False
        self._current_window_start: datetime | None = None
        self._next_transition: datetime | None = None
        self._unsub_transition = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_transition_timer)
        self._refresh()

    @callback
    def _cancel_transition_timer(self) -> None:
        if self._unsub_transition is not None:
            self._unsub_transition()
            self._unsub_transition = None

    @callback
    def _refresh(self, _now: datetime | None = None) -> None:
        """Evaluate the current window state and schedule the next transition."""
        now = dt_util.utcnow()
        active_start, next_transition = self._evaluate(now)
        self._attr_is_on = active_start is not None
        self._current_window_start = active_start
        self._next_transition = next_transition

        self._cancel_transition_timer()
        if next_transition is None:
            # No boundaries in sight (no valid windows) — re-check tomorrow in
            # case sun events become resolvable again (polar day/night).
            next_transition = dt_util.start_of_local_day() + timedelta(days=1)
        self._unsub_transition = async_track_point_in_time(
            self.hass, self._refresh, next_transition + timedelta(seconds=1)
        )
        self.async_write_ha_state()

    def _evaluate(
        self, now: datetime
    ) -> tuple[datetime | None, datetime | None]:
        """Return (active window start, next boundary after now).

        Windows are resolved for yesterday, today, and tomorrow so overnight
        windows and day-to-day sun drift are handled correctly.
        """
        today = dt_util.as_local(now).date()
        intervals = []
        for offset in (-1, 0, 1):
            day = today + timedelta(days=offset)
            for window in self._windows:
                interval = self._resolve_window(window, day)
                if interval is not None:
                    intervals.append(interval)

        active = [iv for iv in intervals if iv[0] <= now < iv[1]]
        future = sorted(t for iv in intervals for t in iv if t > now)

        active_start = max(iv[0] for iv in active) if active else None
        return active_start, (future[0] if future else None)

    def _resolve_window(
        self, window: dict, day: date
    ) -> tuple[datetime, datetime] | None:
        start = self._resolve_edge(window.get("start"), day)
        if start is None:
            return None
        end = self._resolve_edge(window.get("end"), day)
        if end is not None and end <= start:
            # Overnight window — the end belongs to the next day.
            end = self._resolve_edge(window.get("end"), day + timedelta(days=1))
        if end is None:
            return None
        return (start, end)

    def _resolve_edge(self, edge, day: date) -> datetime | None:
        """Resolve an edge spec to a concrete datetime on the given day."""
        if isinstance(edge, str):
            edge = {EDGE_TIME: edge}
        if not isinstance(edge, dict):
            return None

        fixed: datetime | None = None
        if edge.get(EDGE_TIME):
            try:
                fixed = datetime.combine(
                    day,
                    time.fromisoformat(edge[EDGE_TIME]),
                    tzinfo=dt_util.DEFAULT_TIME_ZONE,
                )
            except ValueError:
                pass

        sun: datetime | None = None
        if edge.get(EDGE_SUN) in SUN_EVENTS:
            # None on polar days when the event doesn't occur — the fixed
            # time (if any) then stands alone.
            sun = get_astral_event_date(self.hass, edge[EDGE_SUN], day)
            if sun is not None:
                try:
                    sun += timedelta(minutes=int(edge.get(EDGE_OFFSET, 0)))
                except (ValueError, TypeError):
                    pass

        candidates = [d for d in (fixed, sun) if d is not None]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if edge.get(EDGE_COMBINE, COMBINE_LATEST) == COMBINE_LATEST:
            return max(candidates)
        return min(candidates)

    @property
    def extra_state_attributes(self) -> dict:
        def _fmt(t: datetime | None) -> str | None:
            return t.isoformat() if t else None

        return {
            "current_window_start": _fmt(self._current_window_start),
            "next_transition": _fmt(self._next_transition),
        }
