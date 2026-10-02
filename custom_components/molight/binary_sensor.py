"""Virtual binary sensor platform for MoLight.

Provides five sensor types, all created via the config flow:

  VirtualOccupancySensor:          wraps one real sensor with a timeout;
                                   exposes latest_occupied_time = last_off - timeout
  VirtualCombinedOccupancySensor:  combines VirtualOccupancySensors using
                                   trigger/maintain logic; latest_occupied_time
                                   is the max across all constituents
  VirtualIlluminanceSensor:        compares a real illuminance sensor to a threshold
  VirtualScheduleSensor:           evaluates time windows or mirrors a binary sensor
  VirtualCombinedScheduleSensor:   combines schedules with any/all logic
"""

from __future__ import annotations

import contextlib
import logging
import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT,
    BinarySensorEntity,
)
from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntryChange,
    ConfigEntryDisabler,
)
from homeassistant.const import (
    ATTR_RESTORED,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    async_call_later,
    async_track_entity_registry_updated_event,
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import RestoredExtraData
from homeassistant.helpers.start import async_at_started
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
    CONF_SCHEDULE_INPUTS,
    CONF_SCHEDULE_INVERT,
    CONF_SCHEDULE_OPERATOR,
    CONF_SCHEDULE_SOURCE,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    DEFAULT_FALSE_DETECTION_GRACE,
    DEFAULT_ILLUMINANCE_HYSTERESIS,
    DEFAULT_ILLUMINANCE_THRESHOLD,
    DEFAULT_OCCUPANCY_TIMEOUT,
    DEFAULT_SCHEDULE_OPERATOR,
    DOMAIN,
    EDGE_COMBINE,
    EDGE_OFFSET,
    EDGE_SUN,
    EDGE_TIME,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_COMBINED_SCHEDULE,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
    SCHEDULE_DEFINITION_BINARY_SENSOR,
    SCHEDULE_DEFINITION_TIME,
    SCHEDULE_OPERATOR_ALL,
    SUN_EVENTS,
)
from .helpers import (
    RenamableRestoreEntity,
    entity_gone,
    molight_config,
    renamed_to,
    run_unless_renamed,
    same_entity,
    suggested_entity_id,
)

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, EventStateChangedData, State
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.restore_state import RestoreEntity

_LOGGER = logging.getLogger(__name__)

# How long a constituent of a combined occupancy sensor may take to come back
# from a reload of its entry before it counts as gone (seconds).
_RELOAD_GRACE = 10


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
        ENTITY_TYPE_COMBINED_SCHEDULE: VirtualCombinedScheduleSensor,
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


class VirtualOccupancySensor(BinarySensorEntity, RenamableRestoreEntity):
    """Wraps a single real binary sensor with an occupancy timeout.

    is_on mirrors the real sensor directly (no countdown).
    latest_occupied_time = last_turn_off - timeout, representing our best
    estimate of when the person actually left.

    False-detection classification (when false_detection_grace > 0): a cycle
    whose on-duration exceeds the timeout by no more than the grace contained
    exactly one instantaneous detection: the sensor never re-triggered
    during its hold time, so it was almost certainly a fly/heat blip, or a
    brief pass-through. Such cycles don't advance latest_occupied_time, are
    counted in false_detection_count, and flag the clear via
    last_clear_false_detection so lights can turn off quickly.

    Clear-on-unavailable (when clear_on_unavailable_timeout > 0): a source
    that stays unavailable/unknown while occupancy is active would otherwise
    hold occupancy (and any lights it lit) on forever. Instead, the person
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
        # A cycle's anchor, its clear's classification and an occupied
        # dropout describe one source; an options edit that switches the
        # source leaves them behind. A save that predates the source record
        # is trusted.
        extra = await self.async_get_last_extra_data()
        saved_source = extra.as_dict().get("source") if extra is not None else None
        same_source = saved_source is None or same_entity(
            self.hass, saved_source, self._source_sensor
        )
        if last is not None:
            with contextlib.suppress(ValueError, TypeError):
                self._false_count = int(
                    last.attributes.get("false_detection_count") or 0
                )
            attrs = last.attributes
            if same_source and last.state == "off":
                # The flags describe this very clear, which still stands.
                self._last_clear_false = bool(attrs.get("last_clear_false_detection"))
                self._last_clear_unavailable = bool(attrs.get("last_clear_unavailable"))
            # Only a cycle that was still running owns its anchor: a
            # last_on_time restored alongside an "off" state belongs to a
            # finished pre-restart cycle and would skew the next
            # classification.
            raw = attrs.get("last_on_time")
            if raw and last.state == "on" and same_source:
                with contextlib.suppress(ValueError, TypeError):
                    self._last_on_time = datetime.fromisoformat(raw)
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_sensor], self._handle_sensor_change
            )
        )
        self.async_on_remove(self._cancel_unavailable_timer)
        self._seed_state(
            restored_on=same_source and last is not None and last.state == "on"
        )

    def _seed_state(self, *, restored_on: bool) -> None:
        state = self.hass.states.get(self._source_sensor)
        if (
            restored_on
            and (state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN))
            and self.hass.state is CoreState.running
        ):
            # Not at startup, where the source may just not have loaded yet.
            # A source with no state at all was removed; that dropout
            # advanced latest_occupied_time to the moment it happened.
            self._resume_dropout(
                state.last_changed
                if state is not None
                else self._latest_occupied_time or datetime.now(UTC)
            )
        elif state:
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
        if new_state is None:
            # A renamed source did not drop out: this sensor is about to
            # reload with the new ID.
            self.async_on_remove(
                run_unless_renamed(
                    self.hass, self._source_sensor, self._on_source_unavailable
                )
            )
            return
        if new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._on_source_unavailable()
            return
        if not _real_state_change(event):
            return
        self._cancel_unavailable_timer()
        if new_state.state == "on":
            # A recovery from unavailable while already occupied continues the
            # running cycle; restamping last_on_time here would make a later
            # clear measure the on-duration from the recovery moment and
            # misclassify a long occupancy as a false detection.
            if not self._attr_is_on:
                # Before startup finishes, an on-event is a late-loading
                # source replaying a pre-restart detection: its true start is
                # unknown, so leave the cycle unclassified (as in _seed_state).
                # The same holds when the source is first provided after boot
                # (HA kept a restored placeholder for it until its integration
                # loaded), however long after HA reported running that is.
                old_state = event.data.get("old_state")
                first_sighting = (
                    old_state is not None
                    and old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                    and bool(old_state.attributes.get(ATTR_RESTORED))
                )
                self._last_on_time = (
                    None
                    if first_sighting or self.hass.state is not CoreState.running
                    else datetime.now(UTC)
                )
            self._attr_is_on = True
        else:
            if not self._attr_is_on:
                # Already cleared (the unavailable timeout fired): a late
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

    def _resume_dropout(self, dropout: datetime) -> None:
        """Carry an occupied dropout across a reload, keeping its clear deadline."""
        self._attr_is_on = True
        if self._unavailable_timeout <= 0:
            return
        if self._latest_occupied_time is None or dropout > self._latest_occupied_time:
            self._latest_occupied_time = dropout
        elapsed = (datetime.now(UTC) - dropout).total_seconds()
        if elapsed >= self._unavailable_timeout:
            self._attr_is_on = False
            self._last_clear_false = False
            self._last_clear_unavailable = True
            return
        self._unavailable_unsub = async_call_later(
            self.hass, self._unavailable_timeout - elapsed, self._unavailable_expired
        )

    @callback
    def _on_source_unavailable(self) -> None:
        """Start the clear-on-unavailable countdown for an occupied dropout."""
        if (
            self._unavailable_timeout <= 0
            or not self._attr_is_on
            or self._unavailable_unsub is not None
        ):
            return
        # The person may have been present right up to the dropout, so advance
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
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Save which source the saved anchor and classification belong to."""
        return RestoredExtraData({"source": self._source_sensor})

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


class VirtualCombinedOccupancySensor(BinarySensorEntity, RenamableRestoreEntity):
    """Combines multiple VirtualOccupancySensors using trigger/maintain logic.

    Trigger sensors start occupancy; maintain sensors keep it alive once started.
    latest_occupied_time is the max of all constituents' latest_occupied_time
    attributes, propagated whenever a constituent turns off.

    False-detection classification: constituents that classify a clear as a
    false detection don't advance their latest_occupied_time, so a combined
    cycle during which our own latest_occupied_time never advanced was made
    up entirely of false (or stale) cycles, so count it and flag the clear.
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
        # The anchor saved with a restored "on": the running cycle keeps it,
        # so a genuine detection it already contained is not forgotten.
        self._saved_cycle = False
        self._saved_cycle_start_lot: datetime | None = None
        self._false_count: int = 0
        self._last_clear_false: bool = False
        # A restored "on" that no constituent confirmed at seed time: a
        # maintain sensor showing presence as the rest of startup unfolds
        # may still carry it across (see _seed_state).
        self._restored_carry = False
        self._startup_done = True
        self._unreported_maintain: set[str] = set()
        self._unsub_started: CALLBACK_TYPE | None = None
        # Constituents that were on when their entry unloaded, each with the
        # timer that ends its grace: they count as on until they report again.
        self._reloading: dict[str, CALLBACK_TYPE] = {}

    async def async_added_to_hass(self) -> None:
        """Restore state and subscribe to all constituent sensors."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self._latest_occupied_time = _restored_latest_occupied_time(last)
        extra = await self.async_get_last_extra_data()
        saved = extra.as_dict() if extra is not None else {}
        if last is not None:
            with contextlib.suppress(ValueError, TypeError):
                self._false_count = int(
                    last.attributes.get("false_detection_count") or 0
                )
            # A clear's classification describes these constituents; an edit
            # leaves it behind. A save that predates the record is trusted.
            if last.state == "off" and self._saved_for_constituents(saved):
                self._last_clear_false = bool(
                    last.attributes.get("last_clear_false_detection")
                )
        # An "on" still waiting to be carried when the state was saved (see
        # _seed_state) was published as "off"; the save says so itself.
        restored_on = (last is not None and last.state == "on") or bool(
            saved.get("carry")
        )
        if restored_on and extra is not None:
            self._saved_cycle = True
            self._saved_cycle_start_lot = _parse_datetime(
                saved.get("cycle_start_latest_occupied_time")
            )
        all_sensors = list(
            dict.fromkeys(self._trigger_sensors + self._maintain_sensors)
        )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, all_sensors, self._handle_occupancy_change
            )
        )
        self._seed_state(restored_on=restored_on)

    def _seed_state(self, *, restored_on: bool) -> None:
        self._absorb_constituent_history()
        if self._any_on(self._trigger_sensors):
            self._attr_is_on = True
        elif restored_on and self._any_on(self._maintain_sensors):
            # Intentional startup exception to "maintain sensors never start
            # occupancy": the restored state is direct evidence occupancy was
            # already triggered before the restart, so a maintain sensor
            # still showing presence carries it across. (An on-duration
            # heuristic can't do this job: constituents rewrite their state
            # at boot, resetting last_changed, and it would wrongly start
            # occupancy on a mid-run options reload.)
            self._attr_is_on = True
        elif restored_on:
            # Constituents are separate config entries that set up
            # concurrently, so a maintain sensor may still be HA's restored
            # placeholder here (or, for a virtual sensor whose own source has
            # not loaded, a provisional "off"). Let a maintain sensor that
            # shows presence before startup finishes, or on its first sighting
            # after it, carry the restored occupancy instead (see
            # _handle_occupancy_change). Seeding off meanwhile is harmless:
            # dependent lights adopt with a full timer and re-evaluate.
            self._restored_carry = True
            self._startup_done = self.hass.state is CoreState.running
            self._unreported_maintain = {
                e for e in self._maintain_sensors if self._unreported(e)
            }
            if not self._startup_done:
                self._unsub_started = self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, self._on_startup_done
                )
        if self._attr_is_on:
            self._cycle_start_lot = (
                self._saved_cycle_start_lot
                if self._saved_cycle
                else self._latest_occupied_time
            )
        self.async_write_ha_state()

    def _absorb_constituent_history(self) -> None:
        """Take over the latest_occupied_time the constituents already carry.

        A visit they saw before this sensor existed, or while it was not
        loaded, is history to measure the next cycle against, not a
        detection made during it.
        """
        for entity_id in self._trigger_sensors + self._maintain_sensors:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            lot = _parse_datetime(state.attributes.get("latest_occupied_time"))
            if lot is not None and (
                self._latest_occupied_time is None or lot > self._latest_occupied_time
            ):
                self._latest_occupied_time = lot

    @callback
    def _on_startup_done(self, _event: Event) -> None:
        # A fired one-time listener is already gone; drop our reference so
        # removal doesn't try to unsubscribe it again.
        self._unsub_started = None
        self._startup_done = True

    async def async_will_remove_from_hass(self) -> None:
        """Drop the startup listener and any reload grace timers."""
        await super().async_will_remove_from_hass()
        if self._unsub_started is not None:
            self._unsub_started()
            self._unsub_started = None
        for cancel in self._reloading.values():
            cancel()
        self._reloading.clear()

    def _unreported(self, entity_id: str) -> bool:
        """Return True while an entity has no state or only a restored placeholder."""
        state = self.hass.states.get(entity_id)
        return state is None or (
            state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            and bool(state.attributes.get(ATTR_RESTORED))
        )

    @callback
    def _handle_occupancy_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        if cancel := self._reloading.pop(entity_id, None):
            cancel()
        if event.data.get("new_state") is None:
            # A renamed constituent did not drop out: this sensor is about to
            # reload with the new ID.
            dropout = datetime.now(UTC)
            self.async_on_remove(
                run_unless_renamed(
                    self.hass,
                    entity_id,
                    lambda: self._reevaluate_on_dropout(event, dropout),
                )
            )
            return
        if not _real_state_change(event):
            self._hold_through_reload(event)
            self._reevaluate_on_dropout(event, datetime.now(UTC))
            return
        new_state = event.data["new_state"]
        # A maintain sensor showing presence while startup is still under
        # way, on its first sighting after that, or with a start it did not
        # witness (a virtual sensor whose source loaded late reports
        # last_on_time: None) carries a restored "on" across (see
        # _seed_state); any later report cannot start occupancy.
        unknown_start = (
            "last_on_time" in new_state.attributes
            and new_state.attributes["last_on_time"] is None
        )
        carry = (
            self._restored_carry
            and entity_id in self._maintain_sensors
            and new_state.state == "on"
            and (
                not self._startup_done
                or entity_id in self._unreported_maintain
                or unknown_start
            )
        )
        self._unreported_maintain.discard(entity_id)

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
        elif carry:
            self._attr_is_on = True
        else:
            self._attr_is_on = False

        if not was_on and self._attr_is_on:
            self._cycle_start_lot = (
                self._saved_cycle_start_lot
                if self._restored_carry and self._saved_cycle
                else self._latest_occupied_time
            )
            # Occupancy has (re)started since the restart; the restored
            # evidence is spent.
            self._restored_carry = False
        elif was_on and not self._attr_is_on:
            self._last_clear_false = self._latest_occupied_time == self._cycle_start_lot
            if self._last_clear_false:
                self._false_count += 1

        self.async_write_ha_state()

    def _hold_through_reload(self, event: Event[EventStateChangedData]) -> None:
        """Keep counting a constituent as on while its entry reloads.

        An unloaded entry leaves HA's restored placeholder, and a reload
        brings the entity back moments later. A real outage gets no grace,
        an entry that stays unloaded only a short one, and a removed entry's
        placeholder is dropped by async_remove_entry before it can matter.
        """
        entity_id = event.data["entity_id"]
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if (
            old_state is None
            or old_state.state != "on"
            or new_state is None
            or not self._unreported(entity_id)
        ):
            return
        dropout = datetime.now(UTC)

        @callback
        def _expired(_now: datetime) -> None:
            del self._reloading[entity_id]
            self._reevaluate_on_dropout(event, dropout)

        self._reloading[entity_id] = async_call_later(
            self.hass, _RELOAD_GRACE, _expired
        )

    def _reevaluate_on_dropout(
        self, event: Event[EventStateChangedData], dropout: datetime
    ) -> None:
        """Clear occupancy when the last constituent still on drops out.

        A constituent leaving the state machine (its entry unloaded, the
        entity removed) must not hold the combined sensor on forever. Like
        the simple sensor's clear-on-unavailable, never classified as a
        false detection (the room may still be occupied), and the person is
        assumed present right up to the dropout, so latest_occupied_time
        advances to that moment: dependent lights run their normal gentle
        countdown, and a nesting combined sensor sees an advanced lot rather
        than misreading the clear as a false cycle.
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
        if self._latest_occupied_time is None or dropout > self._latest_occupied_time:
            self._latest_occupied_time = dropout
        self._attr_is_on = False
        self._last_clear_false = False
        self.async_write_ha_state()

    def _any_on(self, sensors: list[str]) -> bool:
        return any(
            e in self._reloading or ((s := self.hass.states.get(e)) and s.state == "on")
            for e in sensors
        )

    def _constituents(self) -> dict[str, list[str]]:
        return {
            "trigger": sorted(set(self._trigger_sensors)),
            "maintain": sorted(set(self._maintain_sensors)),
        }

    def _saved_for_constituents(self, saved: dict) -> bool:
        """Return whether a save describes the current constituents.

        A save that predates the record is trusted, and one made before a
        constituent was renamed names its former ID.
        """
        constituents = saved.get("constituents")
        if constituents is None:
            return True
        if not isinstance(constituents, dict):
            return False
        return {
            role: sorted({renamed_to(self.hass, e) or e for e in entity_ids})
            for role, entity_ids in constituents.items()
        } == self._constituents()

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Save the running cycle's anchor and the constituents of the clear.

        Home Assistant saves as it starts, while a restored "on" may still
        wait for a maintain sensor to carry it: that wait is saved too.
        """
        carry = self._restored_carry and (
            not self._startup_done or bool(self._unreported_maintain)
        )
        anchor = self._cycle_start_lot
        if carry:
            anchor = (
                self._saved_cycle_start_lot
                if self._saved_cycle
                else self._latest_occupied_time
            )
        return RestoredExtraData(
            {
                "cycle_start_latest_occupied_time": (
                    anchor.isoformat() if anchor else None
                ),
                "constituents": self._constituents(),
                "carry": carry,
            }
        )

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


class VirtualIlluminanceSensor(BinarySensorEntity, RenamableRestoreEntity):
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
        # A held reading and its side of the hysteresis band belong to their
        # source. A save that predates the source record is trusted.
        extra = await self.async_get_last_extra_data()
        saved_source = extra.as_dict().get("source") if extra is not None else None
        if (
            last is not None
            and last.state in ("on", "off")
            and (
                saved_source is None
                or same_entity(self.hass, saved_source, self._source_entity)
            )
        ):
            self._attr_is_on = last.state == "on"
            self._attr_available = True
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity], self._handle_illuminance_change
            )
        )
        self.async_on_remove(
            async_track_entity_registry_updated_event(
                self.hass, [self._source_entity], self._handle_registry_change
            )
        )
        self._seed_state()
        # A source that has not loaded yet is not gone: judge once started.
        self.async_on_remove(async_at_started(self.hass, self._on_source_gone))

    def _seed_state(self) -> None:
        state = self.hass.states.get(self._source_entity)
        if state:
            self._update_from_state(state.state)
        self.async_write_ha_state()

    @callback
    def _handle_illuminance_change(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            # Deleted or disabled, unless it is only being renamed.
            self.async_on_remove(
                run_unless_renamed(self.hass, self._source_entity, self._on_source_gone)
            )
            return
        if new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return  # hold last known value while the source is unavailable
        self._update_from_state(new_state.state)
        self.async_write_ha_state()

    @callback
    def _handle_registry_change(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        """Notice a source disabled along with its entry, which keeps a placeholder."""
        if event.data["action"] == "update" and "old_entity_id" not in event.data:
            self._on_source_gone()

    @callback
    def _on_source_gone(self, _hass: HomeAssistant | None = None) -> None:
        """Drop the held reading of a source that can never report again."""
        if not self._attr_available or not entity_gone(self.hass, self._source_entity):
            return
        self._attr_available = False
        self._attr_is_on = False
        self.async_write_ha_state()

    def _update_from_state(self, state_value: str) -> None:
        try:
            value = float(state_value)
        except (ValueError, TypeError):
            return  # unparsable reading: hold last known value
        if not math.isfinite(value):
            return  # nan or inf is no reading either
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

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Save which source the held reading belongs to."""
        return RestoredExtraData({"source": self._source_entity})


def _edge_time(edge: dict) -> time | None:
    """Return an edge's fixed time, if it has a valid one."""
    if edge.get(EDGE_TIME):
        with contextlib.suppress(ValueError):
            return time.fromisoformat(edge[EDGE_TIME])
    return None


def _edge_sun(edge: dict) -> tuple[str, int] | None:
    """Return an edge's sun event and its offset in minutes, if it has one."""
    if edge.get(EDGE_SUN) not in SUN_EVENTS:
        return None
    offset = 0
    with contextlib.suppress(ValueError, TypeError):
        offset = int(edge.get(EDGE_OFFSET, 0))
    return edge[EDGE_SUN], offset


def _picks_latest(edge: dict) -> bool:
    return edge.get(EDGE_COMBINE, COMBINE_LATEST) == COMBINE_LATEST


def _resolve_edge(hass: HomeAssistant, edge: dict | None, day: date) -> datetime | None:
    """Resolve an edge spec to a concrete datetime on the given day."""
    if not isinstance(edge, dict):
        return None

    fixed: datetime | None = None
    if (at := _edge_time(edge)) is not None:
        fixed = datetime.combine(day, at, tzinfo=dt_util.DEFAULT_TIME_ZONE)

    sun: datetime | None = None
    if (anchor := _edge_sun(edge)) is not None:
        # None on polar days when the event doesn't occur; the fixed
        # time (if any) then stands alone.
        sun = get_astral_event_date(hass, anchor[0], day)
        if sun is not None:
            sun += timedelta(minutes=anchor[1])

    candidates = [d for d in (fixed, sun) if d is not None]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return max(candidates) if _picks_latest(edge) else min(candidates)


_DAY = 24 * 3600
_HALF_DAY = _DAY // 2
# Where a sun event sits on the clock when judging overnight windows (seconds).
_SUN_CLOCK = {"sunrise": 6 * 3600, "sunset": 18 * 3600}


def _edge_clock(edge: dict | None) -> tuple[str, int] | None:
    """Return an edge's kind and its place on the clock, from its settings alone.

    The place is in seconds after midnight; a sun offset can push it into a
    neighbouring day.
    """
    if not isinstance(edge, dict):
        return None
    at, anchor = _edge_time(edge), _edge_sun(edge)
    fixed = None if at is None else at.hour * 3600 + at.minute * 60 + at.second
    if anchor is None:
        return None if fixed is None else ("time", fixed)
    sun = _SUN_CLOCK[anchor[0]] + anchor[1] * 60
    if fixed is None:
        return (anchor[0], sun)
    return ("both", max(fixed, sun) if _picks_latest(edge) else min(fixed, sun))


def _end_days_after(start_edge: dict | None, end_edge: dict | None) -> int | None:
    """Return how many days after the start's day the end's day is.

    Judged from the settings, never the resolved times: a sun event drifts
    past a fixed time with the seasons, and the window is then empty that
    day, as with Home Assistant's own sun and time conditions.
    """
    start, end = _edge_clock(start_edge), _edge_clock(end_edge)
    if start is None or end is None:
        return None
    (start_kind, start_at), (end_kind, end_at) = start, end
    if start_kind == end_kind != "both":
        # Two fixed times, or one sun event twice, never swap order: the end
        # is the first one after the start.
        return (start_at - end_at) // _DAY + 1
    # Otherwise by half-days: the end is in the start's half or the next one,
    # so an evening start with a morning end runs overnight. An end at 00:00
    # or 12:00 closes the half before it.
    start_half = start_at // _HALF_DAY
    end_half = -(-end_at // _HALF_DAY) - 1
    return -((end_half - start_half) // 2)


def _resolve_window(
    hass: HomeAssistant, window: dict, day: date
) -> tuple[datetime, datetime] | None:
    start_edge, end_edge = window.get("start"), window.get("end")
    start = _resolve_edge(hass, start_edge, day)
    days = _end_days_after(start_edge, end_edge)
    if start is None or days is None:
        return None
    end = _resolve_edge(hass, end_edge, day + timedelta(days=days))
    if end is None or end.timestamp() <= start.timestamp():
        # No window that day: a sun event has passed the edge it is paired
        # with, or a start in the spring-forward gap landed after the end.
        return None
    return (start, end)


# Sun offsets move edges up to 12h into a neighbouring day, so extra days are
# resolved and values trusted only from yesterday to the day after tomorrow.
_DAY_OFFSETS = range(-3, 4)
_TRUSTED_DAYS = (-1, 2)


def _merged_window_intervals(
    hass: HomeAssistant, windows: list[dict], now: datetime, day_offsets: Iterable[int]
) -> list[tuple[datetime, datetime]]:
    """Resolve windows on days around now and merge overlaps, oldest first."""
    today = dt_util.as_local(now).date()
    intervals = [
        interval
        for offset in day_offsets
        for window in windows
        if (interval := _resolve_window(hass, window, today + timedelta(days=offset)))
        is not None
    ]
    merged: list[list[datetime]] = []
    # Order and compare by real instant; same-tzinfo datetimes sort by
    # wall clock (PEP 495), which misorders edges around a DST gap.
    for start, end in sorted(
        intervals, key=lambda iv: (iv[0].timestamp(), iv[1].timestamp())
    ):
        # Touching windows stay separate: a 00:00→00:00 window is one per day.
        if merged and start.timestamp() < merged[-1][1].timestamp():
            merged[-1][1] = max(merged[-1][1], end, key=lambda t: t.timestamp())
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


async def _restored_schedule_data(entity: RestoreEntity) -> dict:
    """Return a schedule's saved on/off value, marker and source.

    Read from the extra data, or from the last state where a save predates it.
    """
    if (extra := await entity.async_get_last_extra_data()) is not None:
        return extra.as_dict()
    last = await entity.async_get_last_state()
    if last is None:
        return {}
    return {
        "is_on": last.state == "on" if last.state in ("on", "off") else None,
        "current_window_start": last.attributes.get("current_window_start"),
        "source_entity": last.attributes.get("source_entity"),
        "invert": last.attributes.get("inverted"),
    }


# ---------------------------------------------------------------------------
# Virtual Schedule Binary Sensor
# ---------------------------------------------------------------------------


class VirtualScheduleSensor(BinarySensorEntity, RenamableRestoreEntity):
    """Binary sensor driven by a time window or another binary sensor.

    Each window is {"start": <edge>, "end": <edge>} where an edge is either a
    plain "HH:MM" string or {"time": "HH:MM", "sun": "sunset"|"sunrise",
    "offset": <minutes>, "combine": "latest"|"earliest"}, e.g. start at the
    later of sunset-15min and 21:00. A window whose end is set earlier in the
    day than its start runs overnight (see _end_days_after).

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
            # An unavailable state is saved without attributes, so the marker
            # is also kept as extra data; attributes serve older saves.
            saved = await _restored_schedule_data(self)
            restored_start = (
                _parse_datetime(saved.get("current_window_start"))
                if same_entity(self.hass, saved.get("source_entity"), self._source)
                and self._saved_invert_matches(saved)
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
            # A saved marker is kept while its on-period runs on, so a
            # restart inside the window does not date the window anew.
            saved = await _restored_schedule_data(self)
            restored_start = (
                _parse_datetime(saved.get("current_window_start"))
                if saved.get("is_on") is True
                and saved.get("source_entity") is None
                and self._saved_invert_matches(saved)
                else None
            )
            self._attr_is_on = restored_start is not None
            self._current_window_start = restored_start
            self._refresh()

    def _saved_invert_matches(self, saved: dict) -> bool:
        """Return whether the saved marker was made under the current invert setting."""
        return bool(saved.get("invert", self._invert)) == self._invert

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
            # No boundaries in sight (no valid windows): re-check tomorrow in
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

        Windows are resolved for the days around today, so overnight windows,
        sun offsets that push a window past the next midnight and day-to-day
        sun drift are handled correctly. Overlapping windows merge into one
        on-period, so Follow mode never sees a new window start partway
        through it.
        """
        merged = _merged_window_intervals(self.hass, self._windows, now, _DAY_OFFSETS)
        active_start = next(
            (start for start, end in merged if start <= now < end), None
        )
        if active_start is not None:
            active_start = self._continued_marker(active_start, now) or active_start
        future = [t for interval in merged for t in interval if t > now]
        return active_start, min(future, key=lambda t: t.timestamp(), default=None)

    def _continued_marker(
        self, active_start: datetime, now: datetime
    ) -> datetime | None:
        """Return the current marker if its on-period has run on into this window.

        Windows chained around the clock show each evaluation a later merged
        start, and a moved marker would re-trigger Follow mode. The merged
        windows are complete only from the first trusted day, so an older
        marker is kept unless the schedule went off since then.
        """
        marker = self._current_window_start
        if self._invert or not self._attr_is_on or not isinstance(marker, datetime):
            return None
        known_from = dt_util.start_of_local_day(
            dt_util.as_local(now).date() + timedelta(days=_TRUSTED_DAYS[0])
        )
        if active_start.timestamp() <= max(marker.timestamp(), known_from.timestamp()):
            return marker
        return None

    def _inverted_window_start(self, now: datetime) -> datetime | str:
        """Return when the current gap between configured windows began."""
        merged = _merged_window_intervals(self.hass, self._windows, now, _DAY_OFFSETS)
        ended = [end for _start, end in merged if end <= now]
        # With no resolvable boundaries (for example, a sun-only window during
        # polar day/night), inversion is continuously on. Use a stable marker
        # so Follow mode can apply it once without re-triggering every restart.
        return max(ended, key=lambda t: t.timestamp(), default="inverted")

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Return what a restore needs even when saved while unavailable."""
        marker = self._current_window_start
        return RestoredExtraData(
            {
                "is_on": self._attr_is_on,
                "current_window_start": (
                    marker.isoformat() if isinstance(marker, datetime) else marker
                ),
                "source_entity": self._source,
                "invert": self._invert,
            }
        )

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


# ---------------------------------------------------------------------------
# Virtual Combined Schedule Binary Sensor
# ---------------------------------------------------------------------------

_NEG_INF = float("-inf")
# current_window_start for an on-period with no known start (an inverted empty
# combination): stable, so Follow mode applies it only once.
_ALWAYS_ON_MARKER = "always_on"


def _kleene(require_all: bool, values: list[bool | None]) -> bool | None:
    """Combine on/off/unknown values; unknown only when it could change the answer."""
    # "Any" is decided by one input that is on, "all" by one that is off.
    decisive = not require_all
    if decisive in values:
        return decisive
    if None in values:
        return None
    return require_all


class _Timeline:
    """A value over time that is on, off or unknown (None).

    segments are (start timestamp, value) pairs in time order; the first
    starts at -inf and each value holds until the next start.
    """

    __slots__ = ("segments",)

    def __init__(self, segments: list[tuple[float, bool | None]]) -> None:
        self.segments = segments

    @classmethod
    def constant(cls, value: bool | None) -> _Timeline:
        return cls([(_NEG_INF, value)])

    @classmethod
    def from_intervals(
        cls, intervals: list[tuple[float, float]], known_from: float, known_until: float
    ) -> _Timeline:
        """Build an on/off timeline from ordered on-intervals; unknown outside."""
        segments: list[tuple[float, bool | None]] = [
            (_NEG_INF, None),
            (known_from, False),
        ]
        for raw_start, raw_end in intervals:
            start, end = max(raw_start, known_from), min(raw_end, known_until)
            if start < end:
                segments += [(start, True), (end, False)]
        segments.append((known_until, None))
        return cls(segments).normalized()

    @classmethod
    def combine(cls, require_all: bool, timelines: list[_Timeline]) -> _Timeline:
        starts = sorted({start for tl in timelines for start, _ in tl.segments})
        segments = [
            (start, _kleene(require_all, [tl.value_at(start) for tl in timelines]))
            for start in starts
        ]
        return cls(segments).normalized()

    def normalized(self) -> _Timeline:
        """Drop empty segments and boundaries where the value doesn't change."""
        segments: list[tuple[float, bool | None]] = []
        for start, value in self.segments:
            if segments and segments[-1][0] == start:
                segments.pop()
            if segments and segments[-1][1] == value:
                continue
            segments.append((start, value))
        return _Timeline(segments)

    def inverted(self) -> _Timeline:
        return _Timeline([(s, None if v is None else not v) for s, v in self.segments])

    def _index(self, ts: float) -> int:
        return bisect_right([start for start, _ in self.segments], ts) - 1

    def value_at(self, ts: float) -> bool | None:
        return self.segments[self._index(ts)][1]

    def period_start(self, ts: float) -> float:
        """Return when the value holding at ts began."""
        return self.segments[self._index(ts)][0]

    def off_between(self, start: float, end: float) -> bool:
        """Return whether the value is known to be off at any point in [start, end]."""
        ends = [s for s, _ in self.segments[1:]] + [float("inf")]
        return any(
            value is False and seg_start <= end and seg_end > start
            for (seg_start, value), seg_end in zip(self.segments, ends, strict=True)
        )

    def next_change(self, ts: float) -> float | None:
        index = self._index(ts) + 1
        return self.segments[index][0] if index < len(self.segments) else None


@dataclass
class _ScheduleNode:
    """One schedule in an expanded combined schedule."""

    kind: str  # "time", "source", "combined" or "unknown"
    entity_id: str | None = None
    windows: list[dict] = field(default_factory=list)
    invert: bool = False
    require_all: bool = False
    children: list[_ScheduleNode] = field(default_factory=list)


class _ScheduleTree:
    """Expands a combined schedule down to its plain MoLight schedules.

    Time-window schedules are read from their config and sensor-mirroring
    schedules from their state, so the result never depends on another
    combined schedule's state or on the order entities start up.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.configs: dict[str, dict] = {}
        self.disabled: dict[str, ConfigEntryDisabler | None] = {}
        # Each MoLight input entity, and whether its registry entry is disabled.
        self.entities: dict[str, bool] = {}
        self.source_entities: set[str] = set()
        self.plain_schedules: set[str] = set()

    def combined(
        self,
        entry_id: str,
        cfg: dict,
        path: frozenset[str] = frozenset(),
    ) -> _ScheduleNode:
        path = path | {entry_id}
        self.configs[entry_id] = cfg
        return _ScheduleNode(
            "combined",
            invert=bool(cfg.get(CONF_SCHEDULE_INVERT, False)),
            require_all=cfg.get(CONF_SCHEDULE_OPERATOR, DEFAULT_SCHEDULE_OPERATOR)
            == SCHEDULE_OPERATOR_ALL,
            children=[
                self._expand(entity_id, path)
                for entity_id in cfg.get(CONF_SCHEDULE_INPUTS, [])
            ],
        )

    def _expand(self, entity_id: str, path: frozenset[str]) -> _ScheduleNode:
        reg_entry = er.async_get(self.hass).async_get(entity_id)
        entry = (
            self.hass.config_entries.async_get_entry(reg_entry.config_entry_id)
            if reg_entry is not None and reg_entry.config_entry_id is not None
            else None
        )
        if reg_entry is None or entry is None or entry.domain != DOMAIN:
            return _ScheduleNode("unknown")
        cfg = molight_config(entry)
        entity_type = cfg[CONF_ENTITY_TYPE]
        if entry.entry_id in path:
            # The config flow rejects loops; this guards hand-edited or
            # concurrently saved configs against infinite expansion. Not an
            # input to track: the root's own updates already reload it.
            _LOGGER.warning(
                "Combined schedule %s includes itself; treating it as unknown",
                entity_id,
            )
            return _ScheduleNode("unknown")
        self.configs.setdefault(entry.entry_id, cfg)
        self.disabled[entry.entry_id] = entry.disabled_by
        self.entities[entity_id] = reg_entry.disabled
        if entry.disabled_by is not None or reg_entry.disabled:
            return _ScheduleNode("unknown")
        if entity_type == ENTITY_TYPE_COMBINED_SCHEDULE:
            return self.combined(entry.entry_id, cfg, path)
        if entity_type != ENTITY_TYPE_SCHEDULE:
            return _ScheduleNode("unknown")
        self.plain_schedules.add(entity_id)
        definition = cfg.get(CONF_SCHEDULE_DEFINITION, SCHEDULE_DEFINITION_TIME)
        if definition == SCHEDULE_DEFINITION_BINARY_SENSOR:
            # Its state already applies its own invert and outage handling.
            self.source_entities.add(entity_id)
            return _ScheduleNode("source", entity_id=entity_id)
        return _ScheduleNode(
            "time",
            windows=cfg.get(CONF_TIME_WINDOWS, []),
            invert=bool(cfg.get(CONF_SCHEDULE_INVERT, False)),
        )


class VirtualCombinedScheduleSensor(BinarySensorEntity, RenamableRestoreEntity):
    """Binary sensor combining MoLight schedules with any/all logic.

    The result is unknown (unavailable) only when an unavailable input could
    change it. current_window_start is the start of the combined on-period,
    so overlapping or back-to-back inputs read as one window to Follow mode.
    """

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the virtual combined schedule sensor."""
        self.hass = hass
        self._cfg = molight_config(entry)
        self._entry_id = entry.entry_id
        self._attr_name = self._cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self._attr_is_on = False
        self._root = _ScheduleNode("combined")
        self._plain_schedules: list[str] = []
        self._input_configs: dict[str, dict] = {}
        self._input_disabled: dict[str, ConfigEntryDisabler | None] = {}
        self._input_entities: dict[str, bool] = {}
        self._reload_scheduled = False
        self._current_window_start: str | None = None
        self._next_transition: datetime | None = None
        self._restored = False
        self._startup_done = True
        self._unsub_transition: CALLBACK_TYPE | None = None
        self._unsub_started: CALLBACK_TYPE | None = None
        self._refresh_handle: asyncio.Handle | None = None

    async def async_added_to_hass(self) -> None:
        """Expand the inputs, restore state and start tracking."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_timers)

        tree = _ScheduleTree(self.hass)
        self._root = tree.combined(self._entry_id, self._cfg)
        self._plain_schedules = sorted(tree.plain_schedules)
        self._input_configs = {
            entry_id: cfg
            for entry_id, cfg in tree.configs.items()
            if entry_id != self._entry_id
        }
        self._input_disabled = tree.disabled
        self._input_entities = tree.entities

        # An unavailable state is saved without attributes or on/off value,
        # so both are also kept as extra data; the state serves older saves.
        saved = await _restored_schedule_data(self)
        if isinstance(saved.get("is_on"), bool):
            self._restored = True
            self._attr_is_on = saved["is_on"]
            marker = saved.get("current_window_start")
            if self._attr_is_on and isinstance(marker, str):
                self._current_window_start = marker

        if tree.source_entities:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, sorted(tree.source_entities), self._handle_input_change
                )
            )
        # Inputs are read from config, so an edit anywhere beneath must rebuild this.
        # Not an update listener on the input: HA can skip it while that entry reloads.
        if self._input_disabled:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._handle_entry_change
                )
            )
        # Disabling an input's entity leaves its entry enabled.
        if self._input_entities:
            self.async_on_remove(
                async_track_entity_registry_updated_event(
                    self.hass,
                    sorted(self._input_entities),
                    self._handle_registry_change,
                )
            )

        if self.hass.state is not CoreState.running:
            self._startup_done = False
            self._unsub_started = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._on_startup_done
            )
        self._refresh()

    async def async_will_remove_from_hass(self) -> None:
        """Drop the startup listener if HA has not finished starting yet."""
        await super().async_will_remove_from_hass()
        if self._unsub_started is not None:
            self._unsub_started()
            self._unsub_started = None

    @callback
    def _on_startup_done(self, _event: Event) -> None:
        # A fired one-time listener is already gone; drop our reference so
        # removal doesn't try to unsubscribe it again.
        self._unsub_started = None
        self._startup_done = True
        self._refresh()

    @callback
    def _handle_entry_change(
        self, change: ConfigEntryChange, entry: ConfigEntry
    ) -> None:
        # UPDATED also fires as an entry loads and unloads; only a changed
        # config or disabled flag needs a rebuild.
        if (
            change is ConfigEntryChange.UPDATED
            and not self._reload_scheduled
            and entry.entry_id in self._input_disabled
            and (
                entry.disabled_by != self._input_disabled[entry.entry_id]
                or molight_config(entry) != self._input_configs[entry.entry_id]
            )
        ):
            self._reload_scheduled = True
            self.hass.config_entries.async_schedule_reload(self._entry_id)

    @callback
    def _handle_registry_change(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        entity_id = event.data["entity_id"]
        reg_entry = er.async_get(self.hass).async_get(entity_id)
        disabled = reg_entry is not None and reg_entry.disabled
        if not self._reload_scheduled and disabled != self._input_entities.get(
            entity_id, disabled
        ):
            self._reload_scheduled = True
            self.hass.config_entries.async_schedule_reload(self._entry_id)

    @callback
    def _cancel_timers(self) -> None:
        if self._unsub_transition is not None:
            self._unsub_transition()
            self._unsub_transition = None
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None

    @callback
    def _handle_input_change(self, _event: Event[EventStateChangedData]) -> None:
        # Deferred to the next loop pass so inputs changing together (two
        # schedules mirroring one sensor) are evaluated once, never half-updated.
        if self._refresh_handle is None:
            self._refresh_handle = self.hass.loop.call_soon(self._deferred_refresh)

    @callback
    def _deferred_refresh(self) -> None:
        self._refresh_handle = None
        self._refresh()

    def _timeline(
        self, node: _ScheduleNode, now: datetime, known: tuple[float, float]
    ) -> _Timeline:
        if node.kind == "time":
            merged = _merged_window_intervals(
                self.hass, node.windows, now, _DAY_OFFSETS
            )
            timeline = _Timeline.from_intervals(
                [(start.timestamp(), end.timestamp()) for start, end in merged], *known
            )
        elif node.kind == "source":
            return self._source_timeline(node.entity_id)
        elif node.kind == "combined":
            timeline = (
                _Timeline.combine(
                    node.require_all,
                    [self._timeline(child, now, known) for child in node.children],
                )
                if node.children
                else _Timeline.constant(False)
            )
        else:
            return _Timeline.constant(None)
        return timeline.inverted() if node.invert else timeline

    def _source_timeline(self, entity_id: str | None) -> _Timeline:
        """Return a sensor-mirroring schedule's value since its last change."""
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state not in ("on", "off"):
            return _Timeline.constant(None)
        since = state.last_changed
        if state.state == "on":
            # The schedule restores this across restarts, unlike last_changed.
            since = (
                _parse_datetime(state.attributes.get("current_window_start")) or since
            )
        return _Timeline([(_NEG_INF, None), (since.timestamp(), state.state == "on")])

    @callback
    def _refresh(self, _now: datetime | None = None) -> None:
        """Evaluate the expanded schedules and arm the next transition timer."""
        now = dt_util.utcnow()
        ts = now.timestamp()
        today = dt_util.as_local(now).date()
        known_from, known_until = (
            dt_util.start_of_local_day(today + timedelta(days=days)).timestamp()
            for days in _TRUSTED_DAYS
        )
        timeline = self._timeline(self._root, now, (known_from, known_until))
        value = timeline.value_at(ts)

        if value is None:
            # Keep the last value and marker so a recovery reads as a blip; while HA
            # starts, hold the restored state as inputs may not have reported yet.
            self._attr_available = not self._startup_done and self._restored
        else:
            self._attr_available = True
            if value and not self._continues_period(timeline, ts):
                start = timeline.period_start(ts)
                self._current_window_start = (
                    _ALWAYS_ON_MARKER
                    if start == _NEG_INF
                    else dt_util.as_local(
                        datetime.fromtimestamp(start, UTC)
                    ).isoformat()
                )
            elif not value:
                self._current_window_start = None
            self._attr_is_on = value

        change = timeline.next_change(ts)
        self._next_transition = (
            dt_util.as_local(datetime.fromtimestamp(change, UTC))
            if change is not None and change < known_until
            else None
        )

        if self._unsub_transition is not None:
            self._unsub_transition()
        # With no change in sight, re-check tomorrow as sun times drift.
        wake = self._next_transition or (
            dt_util.start_of_local_day() + timedelta(days=1)
        )
        self._unsub_transition = async_track_point_in_time(
            self.hass, self._refresh, wake + timedelta(seconds=1)
        )
        self.async_write_ha_state()

    def _continues_period(self, timeline: _Timeline, ts: float) -> bool:
        """Return whether the marked on-period may still be under way.

        Only a known off since the marker ends it. Inputs' history is partly
        unknown (a mirror's value before its last change, anything before a
        restart), and a moved marker would re-trigger Follow mode.
        """
        marker = self._current_window_start
        if not (self._attr_is_on and marker):
            return False
        if marker == _ALWAYS_ON_MARKER:
            start = _NEG_INF
        elif parsed := _parse_datetime(marker):
            start = parsed.timestamp()
        else:
            return False
        return not timeline.off_between(start, ts)

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Return what a restore needs even when saved while unavailable."""
        return RestoredExtraData(
            {
                "is_on": self._attr_is_on,
                "current_window_start": self._current_window_start,
            }
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Return the combined schedule attributes."""
        return {
            "current_window_start": self._current_window_start,
            "next_transition": (
                self._next_transition.isoformat() if self._next_transition else None
            ),
            "operator": self._cfg.get(
                CONF_SCHEDULE_OPERATOR, DEFAULT_SCHEDULE_OPERATOR
            ),
            "inverted": bool(self._cfg.get(CONF_SCHEDULE_INVERT, False)),
            "resolved_schedules": self._plain_schedules,
        }
