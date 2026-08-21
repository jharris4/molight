"""Virtual binary sensor platform for MoLight.

Provides four sensor types, all created via the config flow:

  VirtualOccupancySensor         — wraps one real sensor with a timeout;
                                   exposes latest_occupied_time = last_off - timeout
  VirtualCombinedOccupancySensor — combines VirtualOccupancySensors using
                                   trigger/maintain logic; latest_occupied_time
                                   is the max across all constituents
  VirtualIlluminanceSensor       — compares a real illuminance sensor to a threshold
  VirtualScheduleSensor          — evaluates time windows or mirrors a binary sensor
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT,
    BinarySensorEntity,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
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
    CONF_SCHEDULE_DEFINITION,
    CONF_SCHEDULE_INVERT,
    CONF_SCHEDULE_SOURCE,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    DEFAULT_FALSE_DETECTION_GRACE,
    DEFAULT_ILLUMINANCE_HYSTERESIS,
    DEFAULT_ILLUMINANCE_THRESHOLD,
    DEFAULT_OCCUPANCY_TIMEOUT,
    EDGE_COMBINE,
    EDGE_OFFSET,
    EDGE_SUN,
    EDGE_TIME,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
    SCHEDULE_DEFINITION_BINARY_SENSOR,
    SCHEDULE_DEFINITION_TIME,
    SUN_EVENTS,
)
from .helpers import molight_config, suggested_entity_id

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, EventStateChangedData, State
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

# Minimum on-time for a maintain sensor to seed occupancy after a restart;
# filters out sensors that merely flapped on during startup itself.
STARTUP_MAINTAIN_SEED_SECONDS = 5

_LOGGER = logging.getLogger(__name__)


def _real_state_change(event: Event[EventStateChangedData]) -> bool:
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


def _restored_latest_occupied_time(last_state: State | None) -> datetime | None:
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


def _parse_datetime(value: object) -> datetime | None:
    """Parse a stored ISO datetime attribute."""
    if not isinstance(value, str):
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(value)
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
        entity = cls(hass, entry)
        if entity_id := suggested_entity_id(hass, entry, ENTITY_ID_FORMAT):
            entity.entity_id = entity_id
        async_add_entities([entity])


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
        """Initialize the virtual occupancy sensor."""
        self.hass = hass
        cfg = molight_config(entry)
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self._source_sensor: str = cfg[CONF_OCCUPANCY_SENSOR]
        self._timeout: int = int(
            cfg.get(CONF_OCCUPANCY_TIMEOUT, DEFAULT_OCCUPANCY_TIMEOUT)
        )
        self._grace: float = float(
            cfg.get(CONF_FALSE_DETECTION_GRACE, DEFAULT_FALSE_DETECTION_GRACE)
        )
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
        """Restore state and subscribe to the source sensor."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self._latest_occupied_time = _restored_latest_occupied_time(last)
        if last is not None:
            with contextlib.suppress(ValueError, TypeError):
                self._false_count = int(
                    last.attributes.get("false_detection_count") or 0
                )
            raw = last.attributes.get("last_on_time")
            if raw:
                with contextlib.suppress(ValueError, TypeError):
                    self._last_on_time = datetime.fromisoformat(raw)
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
            if (
                self._attr_is_on
                and self._last_on_time is None
                and self.hass.state is CoreState.running
            ):
                # Only a mid-run reload makes last_changed a real estimate; at
                # startup it is just the restart moment.
                self._last_on_time = state.last_changed
        self.async_write_ha_state()

    @callback
    def _handle_sensor_change(self, event: Event[EventStateChangedData]) -> None:
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
                # Before startup finishes, an on-event is a late-loading
                # source replaying a pre-restart detection: its true start is
                # unknown, so leave the cycle unclassified (as in _seed_state).
                self._last_on_time = (
                    datetime.now(UTC) if self.hass.state is CoreState.running else None
                )
            self._attr_is_on = True
        else:
            if not self._attr_is_on:
                # Already cleared (the unavailable timeout fired) — a late
                # recovery straight to "off" must not re-process the clear,
                # which would advance latest_occupied_time past the dropout
                # and overwrite the clear's classification.
                return
            now = datetime.now(UTC)
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
        now = datetime.now(UTC)
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
        """Return occupancy bookkeeping attributes."""
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
        """Initialize the combined occupancy sensor."""
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
        """Restore state and subscribe to all constituent sensors."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self._latest_occupied_time = _restored_latest_occupied_time(last)
        if last is not None:
            with contextlib.suppress(ValueError, TypeError):
                self._false_count = int(
                    last.attributes.get("false_detection_count") or 0
                )
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
            # (and thus can turn lights on). The minimum on-time filters out
            # sensors that merely flapped on during startup itself.
            now = datetime.now(UTC)
            for entity_id in self._maintain_sensors:
                state = self.hass.states.get(entity_id)
                if (
                    state
                    and state.state == "on"
                    and (now - state.last_changed).total_seconds()
                    > STARTUP_MAINTAIN_SEED_SECONDS
                ):
                    self._attr_is_on = True
                    break
        if self._attr_is_on:
            self._cycle_start_lot = self._latest_occupied_time
        self.async_write_ha_state()

    @callback
    def _handle_occupancy_change(self, event: Event[EventStateChangedData]) -> None:
        if not _real_state_change(event):
            self._reevaluate_on_dropout(event)
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
            self._last_clear_false = self._latest_occupied_time == self._cycle_start_lot
            if self._last_clear_false:
                self._false_count += 1

        self.async_write_ha_state()

    def _reevaluate_on_dropout(self, event: Event[EventStateChangedData]) -> None:
        """Clear occupancy when the last constituent still on drops out.

        A constituent leaving the state machine (its entry unloaded, the
        entity removed) must not hold the combined sensor on forever. Like
        the simple sensor's clear-on-unavailable, never classified as a
        false detection — the room may still be occupied.
        """
        new_state = event.data.get("new_state")
        if new_state is not None and new_state.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            return  # attribute-only update; the combined state can't change
        if not self._attr_is_on:
            return
        if self._any_on(self._trigger_sensors) or self._any_on(self._maintain_sensors):
            return
        self._attr_is_on = False
        self._last_clear_false = False
        self.async_write_ha_state()

    def _any_on(self, sensors: list[str]) -> bool:
        return any((s := self.hass.states.get(e)) and s.state == "on" for e in sensors)

    @property
    def extra_state_attributes(self) -> dict:
        """Return occupancy bookkeeping attributes."""
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
        """Initialize the virtual illuminance sensor."""
        self.hass = hass
        cfg = molight_config(entry)
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id

        self._source_entity: str = cfg[CONF_ILLUMINANCE_SENSOR]
        self._threshold: float = float(
            cfg.get(CONF_ILLUMINANCE_THRESHOLD, DEFAULT_ILLUMINANCE_THRESHOLD)
        )
        self._hysteresis: float = float(
            cfg.get(CONF_ILLUMINANCE_HYSTERESIS, DEFAULT_ILLUMINANCE_HYSTERESIS)
        )

        self._attr_is_on = False
        self._attr_available = False

    async def async_added_to_hass(self) -> None:
        """Restore state and subscribe to the illuminance source."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"
            self._attr_available = True
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
    def _handle_illuminance_change(self, event: Event[EventStateChangedData]) -> None:
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
        if not self._attr_available:
            # First-ever reading: there is no held state to apply the
            # hysteresis band to, so judge the bare threshold.
            self._attr_available = True
            self._attr_is_on = value >= self._threshold
            return
        if self._attr_is_on:
            if value < self._threshold - self._hysteresis:
                self._attr_is_on = False
        elif value >= self._threshold + self._hysteresis:
            self._attr_is_on = True


# ---------------------------------------------------------------------------
# Virtual Schedule Binary Sensor
# ---------------------------------------------------------------------------


class VirtualScheduleSensor(BinarySensorEntity, RestoreEntity):
    """Binary sensor driven by a time window or another binary sensor.

    Each window is {"start": <edge>, "end": <edge>} where an edge is either a
    plain "HH:MM" string or {"time": "HH:MM", "sun": "sunset"|"sunrise",
    "offset": <minutes>, "combine": "latest"|"earliest"} — e.g. start at the
    later of sunset-15min and 21:00. Overnight windows (end before start)
    roll the end to the next day.

    Rather than polling, the sensor resolves concrete boundary datetimes and
    schedules a single callback for the next transition, so state flips at the
    exact boundary. current_window_start identifies the active window; virtual
    lights in follow mode use it as a marker for restart catch-up.
    """

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the virtual schedule sensor."""
        self.hass = hass
        cfg = molight_config(entry)
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id

        self._definition = cfg.get(CONF_SCHEDULE_DEFINITION, SCHEDULE_DEFINITION_TIME)
        self._source: str | None = cfg.get(CONF_SCHEDULE_SOURCE)
        self._invert = bool(cfg.get(CONF_SCHEDULE_INVERT, False))
        self._windows: list[dict] = cfg.get(CONF_TIME_WINDOWS, [])
        self._attr_is_on = False
        self._attr_available = self._definition != SCHEDULE_DEFINITION_BINARY_SENSOR
        self._current_window_start: datetime | str | None = None
        self._next_transition: datetime | None = None
        self._unsub_transition = None

    async def async_added_to_hass(self) -> None:
        """Evaluate the schedule and arm the transition timer."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_transition_timer)
        if self._definition == SCHEDULE_DEFINITION_BINARY_SENSOR:
            last = await self.async_get_last_state()
            restored_start = (
                _parse_datetime(last.attributes.get("current_window_start"))
                if last is not None
                and last.attributes.get("source_entity") == self._source
                else None
            )
            self._current_window_start = restored_start
            self._attr_is_on = restored_start is not None
            if self._source:
                self.async_on_remove(
                    async_track_state_change_event(
                        self.hass, [self._source], self._handle_source_change
                    )
                )
            self._refresh_source(
                self.hass.states.get(self._source) if self._source else None,
                restored_start=restored_start,
            )
        else:
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
        raw_is_on = active_start is not None
        self._attr_is_on = raw_is_on != self._invert
        self._current_window_start = (
            active_start
            if self._attr_is_on and not self._invert
            else self._inverted_window_start(now)
            if self._attr_is_on
            else None
        )
        self._next_transition = next_transition
        self._attr_available = True

        self._cancel_transition_timer()
        if next_transition is None:
            # No boundaries in sight (no valid windows) — re-check tomorrow in
            # case sun events become resolvable again (polar day/night).
            next_transition = dt_util.start_of_local_day() + timedelta(days=1)
        self._unsub_transition = async_track_point_in_time(
            self.hass, self._refresh, next_transition + timedelta(seconds=1)
        )
        self.async_write_ha_state()

    @callback
    def _handle_source_change(self, event: Event[EventStateChangedData]) -> None:
        """Mirror valid source states; an outage is not a false boundary."""
        self._refresh_source(event.data.get("new_state"))

    @callback
    def _refresh_source(
        self, source: State | None, *, restored_start: datetime | None = None
    ) -> None:
        """Apply a source state, preserving the last value while unavailable."""
        self._next_transition = None
        if source is None or source.state not in ("on", "off"):
            self._attr_available = False
            self.async_write_ha_state()
            return

        effective_on = (source.state == "on") != self._invert
        if effective_on:
            if not self._attr_is_on:
                self._current_window_start = restored_start or source.last_changed
            elif restored_start is not None:
                self._current_window_start = restored_start
        else:
            self._current_window_start = None
        self._attr_is_on = effective_on
        self._attr_available = True
        self.async_write_ha_state()

    def _evaluate(self, now: datetime) -> tuple[datetime | None, datetime | None]:
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
        # Same-tzinfo aware datetimes sort by wall clock (PEP 495), which
        # misorders edges around a DST gap — order by real instant instead.
        future = sorted(
            (t for iv in intervals for t in iv if t > now),
            key=lambda t: t.timestamp(),
        )

        active_start = (
            max((iv[0] for iv in active), key=lambda t: t.timestamp())
            if active
            else None
        )
        return active_start, (future[0] if future else None)

    def _inverted_window_start(self, now: datetime) -> datetime | str:
        """Return when the current gap between configured windows began."""
        today = dt_util.as_local(now).date()
        intervals = []
        for offset in (-2, -1, 0, 1):
            day = today + timedelta(days=offset)
            intervals.extend(
                interval
                for window in self._windows
                if (interval := self._resolve_window(window, day)) is not None
            )

        merged: list[list[datetime]] = []
        # Order and compare by real instant — same-tzinfo datetimes sort by
        # wall clock (PEP 495), which misorders edges around a DST gap.
        for start, end in sorted(
            intervals, key=lambda iv: (iv[0].timestamp(), iv[1].timestamp())
        ):
            if merged and start.timestamp() <= merged[-1][1].timestamp():
                merged[-1][1] = max(merged[-1][1], end, key=lambda t: t.timestamp())
            else:
                merged.append([start, end])
        ended = [end for _start, end in merged if end <= now]
        # With no resolvable boundaries (for example, a sun-only window during
        # polar day/night), inversion is continuously on. Use a stable marker
        # so Follow mode can apply it once without re-triggering every restart.
        return max(ended, key=lambda t: t.timestamp(), default="inverted")

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

    def _resolve_edge(self, edge: str | dict | None, day: date) -> datetime | None:
        """Resolve an edge spec to a concrete datetime on the given day."""
        if isinstance(edge, str):
            edge = {EDGE_TIME: edge}
        if not isinstance(edge, dict):
            return None

        fixed: datetime | None = None
        if edge.get(EDGE_TIME):
            with contextlib.suppress(ValueError):
                fixed = datetime.combine(
                    day,
                    time.fromisoformat(edge[EDGE_TIME]),
                    tzinfo=dt_util.DEFAULT_TIME_ZONE,
                )

        sun: datetime | None = None
        if edge.get(EDGE_SUN) in SUN_EVENTS:
            # None on polar days when the event doesn't occur — the fixed
            # time (if any) then stands alone.
            sun = get_astral_event_date(self.hass, edge[EDGE_SUN], day)
            if sun is not None:
                with contextlib.suppress(ValueError, TypeError):
                    sun += timedelta(minutes=int(edge.get(EDGE_OFFSET, 0)))

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
        """Return the schedule window attributes."""

        def _fmt(t: datetime | str | None) -> str | None:
            return t.isoformat() if isinstance(t, datetime) else t

        return {
            "current_window_start": _fmt(self._current_window_start),
            "next_transition": _fmt(self._next_transition),
            "source_entity": self._source,
            "inverted": self._invert,
        }
