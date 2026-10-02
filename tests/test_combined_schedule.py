"""Tests for the MoLight Virtual Combined Schedule Sensor."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.core import CoreState, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.binary_sensor import _NEG_INF, _kleene, _Timeline
from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_SCHEDULE_DEFINITION,
    CONF_SCHEDULE_INPUTS,
    CONF_SCHEDULE_INVERT,
    CONF_SCHEDULE_OPERATOR,
    CONF_SCHEDULE_SOURCE,
    CONF_TIME_WINDOWS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_SCHEDULE,
    ENTITY_TYPE_SCHEDULE,
    SCHEDULE_DEFINITION_BINARY_SENSOR,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_OPERATOR_ALL,
    SCHEDULE_OPERATOR_ANY,
    STATE_IDLE,
    STATE_SCHEDULED,
)
from custom_components.molight.helpers import molight_config
from tests.conftest import (
    ANCHORAGE,
    APIA,
    LONDON,
    TROMSO,
    finish_startup,
    light_targets,
    make_light_entry,
    record_service_calls,
    restart_entries,
    set_home,
    settle,
)

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant


def _edge(edge: str | dict) -> dict:
    return {"time": edge} if isinstance(edge, str) else edge


def _time_schedule(name: str, start: str | dict, end: str | dict) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: name,
            CONF_TIME_WINDOWS: [{"start": _edge(start), "end": _edge(end)}],
        },
    )


def _mirror_schedule(
    name: str, source: str, *, invert: bool = False
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: name,
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: source,
            CONF_SCHEDULE_INVERT: invert,
        },
    )


def _combined(
    name: str,
    inputs: list[str],
    *,
    operator: str = SCHEDULE_OPERATOR_ANY,
    invert: bool = False,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_SCHEDULE,
            CONF_NAME: name,
            CONF_SCHEDULE_INPUTS: inputs,
            CONF_SCHEDULE_OPERATOR: operator,
            CONF_SCHEDULE_INVERT: invert,
        },
    )


async def _setup(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    for entry in entries:
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)


async def _move_to(hass: HomeAssistant, freezer, when: str) -> None:
    t = datetime.fromisoformat(when)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await settle(hass)


def _record_states(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Collect every state written for an entity from now on."""
    seen: list[str] = []

    def _listener(event: Event) -> None:
        if event.data["entity_id"] == entity_id and event.data["new_state"]:
            seen.append(event.data["new_state"].state)

    hass.bus.async_listen(EVENT_STATE_CHANGED, _listener)
    return seen


# ---------------------------------------------------------------------------
# Timeline primitives
# ---------------------------------------------------------------------------


def test_timeline_from_intervals_clamps_and_normalizes() -> None:
    """Intervals are clipped to the known span; touching ones fuse; unknown
    lies outside it."""
    t = _Timeline.from_intervals([(0.0, 10.0), (10.0, 20.0), (25.0, 40.0)], 5.0, 30.0)
    assert t.segments == [
        (_NEG_INF, None),
        (5.0, True),
        (20.0, False),
        (25.0, True),
        (30.0, None),
    ]
    assert t.value_at(4.0) is None
    assert t.value_at(5.0) is True
    assert t.value_at(22.0) is False
    assert t.period_start(27.0) == 25.0
    assert t.next_change(22.0) == 25.0
    assert t.next_change(30.0) is None


def test_timeline_off_between_boundaries() -> None:
    """A known off anywhere in the closed range counts; unknown never does."""
    t = _Timeline.from_intervals([(10.0, 20.0)], 0.0, 100.0)
    assert t.off_between(10.0, 19.0) is False
    assert t.off_between(20.0, 25.0) is True
    # The range's end touching the start of an off segment counts.
    assert t.off_between(10.0, 20.0) is True
    # An off segment ending exactly where the range starts does not.
    assert t.off_between(10.0, 15.0) is False
    assert t.off_between(5.0, 10.0) is True
    assert t.off_between(200.0, 300.0) is False


def test_timeline_combine_and_invert() -> None:
    """Unknown decides a combination only when a known input can't."""
    a = _Timeline.from_intervals([(10.0, 20.0)], 0.0, 100.0)
    unknown = _Timeline.constant(None)
    assert _Timeline.combine(False, [a, unknown]).segments == [
        (_NEG_INF, None),
        (10.0, True),
        (20.0, None),
    ]
    assert _Timeline.combine(True, [a, unknown]).segments == [
        (_NEG_INF, None),
        (0.0, False),
        (10.0, None),
        (20.0, False),
        (100.0, None),
    ]
    assert a.inverted().segments == [
        (_NEG_INF, None),
        (0.0, True),
        (10.0, False),
        (20.0, True),
        (100.0, None),
    ]


@pytest.mark.parametrize(
    ("require_all", "values", "expected"),
    [
        (False, [False, None], None),
        (False, [True, None], True),
        (False, [False, False], False),
        (True, [True, None], None),
        (True, [False, None], False),
        (True, [True, True], True),
    ],
)
def test_kleene(require_all: bool, values: list, expected: bool | None) -> None:
    assert _kleene(require_all, values) is expected


# ---------------------------------------------------------------------------
# Combined schedule entity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_any_follows_each_window(hass: HomeAssistant, freezer) -> None:
    """A morning and an evening window drive one schedule."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 05:00:00+00:00")
    await _setup(
        hass,
        _time_schedule("Morning", "06:00", "08:00"),
        _time_schedule("Evening", "20:00", "23:00"),
        _combined("Bedside", ["binary_sensor.morning", "binary_sensor.evening"]),
    )

    state = hass.states.get("binary_sensor.bedside")
    assert state.state == "off"
    assert state.attributes["next_transition"] == "2026-07-02T06:00:00+00:00"
    assert state.attributes["operator"] == SCHEDULE_OPERATOR_ANY
    assert state.attributes["resolved_schedules"] == [
        "binary_sensor.evening",
        "binary_sensor.morning",
    ]

    await _move_to(hass, freezer, "2026-07-02 06:00:02+00:00")
    state = hass.states.get("binary_sensor.bedside")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T06:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-02T08:00:00+00:00"

    await _move_to(hass, freezer, "2026-07-02 08:00:02+00:00")
    state = hass.states.get("binary_sensor.bedside")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None
    assert state.attributes["next_transition"] == "2026-07-02T20:00:00+00:00"

    await _move_to(hass, freezer, "2026-07-02 20:00:02+00:00")
    state = hass.states.get("binary_sensor.bedside")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T20:00:00+00:00"


@pytest.mark.asyncio
async def test_back_to_back_windows_are_one_period(
    hass: HomeAssistant, freezer
) -> None:
    """Where one input ends as another starts there is no off blip and the
    marker stays the start of the whole period."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 07:00:00+00:00")
    await _setup(
        hass,
        _time_schedule("Early", "06:00", "08:00"),
        _time_schedule("Late", "08:00", "10:00"),
        _combined("Bedside", ["binary_sensor.early", "binary_sensor.late"]),
    )
    state = hass.states.get("binary_sensor.bedside")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T06:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-02T10:00:00+00:00"

    seen = _record_states(hass, "binary_sensor.bedside")
    await _move_to(hass, freezer, "2026-07-02 08:00:02+00:00")
    assert "off" not in seen
    state = hass.states.get("binary_sensor.bedside")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T06:00:00+00:00"

    await _move_to(hass, freezer, "2026-07-02 10:00:02+00:00")
    assert hass.states.get("binary_sensor.bedside").state == "off"


@pytest.mark.asyncio
async def test_all_is_on_only_where_inputs_overlap(
    hass: HomeAssistant, freezer
) -> None:
    """With "all", the on-period is the overlap and starts where it begins."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(
        hass,
        _time_schedule("Evening", "18:00", "23:00"),
        _time_schedule("Night", "21:00", "02:00"),
        _combined(
            "Overlap",
            ["binary_sensor.evening", "binary_sensor.night"],
            operator=SCHEDULE_OPERATOR_ALL,
        ),
    )
    state = hass.states.get("binary_sensor.overlap")
    assert state.state == "off"
    assert state.attributes["next_transition"] == "2026-07-02T21:00:00+00:00"

    await _move_to(hass, freezer, "2026-07-02 21:00:02+00:00")
    state = hass.states.get("binary_sensor.overlap")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T21:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-02T23:00:00+00:00"

    await _move_to(hass, freezer, "2026-07-02 23:00:02+00:00")
    assert hass.states.get("binary_sensor.overlap").state == "off"


@pytest.mark.asyncio
async def test_inverted_combination(hass: HomeAssistant, freezer) -> None:
    """Invert output turns the gaps between inputs into the on-periods."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 19:00:00+00:00")
    await _setup(
        hass,
        _time_schedule("Evening", "20:00", "23:00"),
        _combined("Not Evening", ["binary_sensor.evening"], invert=True),
    )
    state = hass.states.get("binary_sensor.not_evening")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-01T23:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-02T20:00:00+00:00"
    assert state.attributes["inverted"] is True

    await _move_to(hass, freezer, "2026-07-02 20:00:02+00:00")
    assert hass.states.get("binary_sensor.not_evening").state == "off"


@pytest.mark.asyncio
async def test_always_on_marker_is_stable_across_days(
    hass: HomeAssistant, freezer
) -> None:
    """Windows are only resolved for a few days around today; the unresolved
    days beyond are unknown, not off, so the marker doesn't creep forward."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    await _setup(
        hass,
        _time_schedule("Day", "06:00", "22:00"),
        _time_schedule("Night", "22:00", "06:00"),
        _combined("Always", ["binary_sensor.day", "binary_sensor.night"]),
    )
    state = hass.states.get("binary_sensor.always")
    assert state.state == "on"
    assert state.attributes["next_transition"] is None
    marker = state.attributes["current_window_start"]

    for when in ("2026-07-03 00:00:02+00:00", "2026-07-04 00:00:02+00:00"):
        await _move_to(hass, freezer, when)
        state = hass.states.get("binary_sensor.always")
        assert state.state == "on"
        assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
async def test_disabled_input_is_unknown(hass: HomeAssistant, freezer) -> None:
    """A disabled input counts as unknown, like a mirror whose state is gone."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 07:00:00+00:00")
    morning = _time_schedule("Morning", "06:00", "08:00")
    await _setup(hass, morning, _combined("Bedside", ["binary_sensor.morning"]))
    assert hass.states.get("binary_sensor.bedside").state == "on"

    await hass.config_entries.async_set_disabled_by(
        morning.entry_id, ConfigEntryDisabler.USER
    )
    await settle(hass)
    assert hass.states.get("binary_sensor.bedside").state == "unavailable"

    await hass.config_entries.async_set_disabled_by(morning.entry_id, None)
    await settle(hass)
    assert hass.states.get("binary_sensor.bedside").state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["time", "mirror", "combined"])
async def test_disabled_input_entity_is_unknown(
    hass: HomeAssistant, freezer, kind: str
) -> None:
    """Disabling an input's entity, not its entry, counts it as unknown too,
    and enabling it again brings it back."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 07:00:00+00:00")
    hass.states.async_set("binary_sensor.door", "on")
    entries = {
        "time": [_time_schedule("Morning", "06:00", "08:00")],
        "mirror": [_mirror_schedule("Morning", "binary_sensor.door")],
        "combined": [
            _time_schedule("Early", "06:00", "08:00"),
            _combined("Morning", ["binary_sensor.early"]),
        ],
    }[kind]
    await _setup(hass, *entries, _combined("Bedside", ["binary_sensor.morning"]))
    assert hass.states.get("binary_sensor.bedside").state == "on"

    registry = er.async_get(hass)
    registry.async_update_entity(
        "binary_sensor.morning", disabled_by=er.RegistryEntryDisabler.USER
    )
    await settle(hass)
    assert hass.states.get("binary_sensor.morning") is None
    assert hass.states.get("binary_sensor.bedside").state == "unavailable"

    registry.async_update_entity("binary_sensor.morning", disabled_by=None)
    await settle(hass)
    # HA reloads the input's entry to add the entity back after a delay.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("binary_sensor.morning").state == "on"
    assert hass.states.get("binary_sensor.bedside").state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"name": "Early morning"},
        {"icon": "mdi:weather-sunset-up"},
        {"hidden_by": er.RegistryEntryHider.USER},
    ],
    ids=["name", "icon", "hidden"],
)
async def test_input_registry_edit_does_not_rebuild(
    hass: HomeAssistant, freezer, change: dict
) -> None:
    """Only disabling an input rebuilds the combination; renaming, a new icon
    or hiding it leaves the combined schedule untouched."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 07:00:00+00:00")
    await _setup(
        hass,
        _time_schedule("Morning", "06:00", "08:00"),
        _combined("Bedside", ["binary_sensor.morning"]),
    )
    writes = _record_states(hass, "binary_sensor.bedside")

    er.async_get(hass).async_update_entity("binary_sensor.morning", **change)
    await settle(hass)

    assert writes == []
    assert hass.states.get("binary_sensor.bedside").state == "on"


@pytest.mark.asyncio
async def test_no_inputs_is_off(hass: HomeAssistant, freezer) -> None:
    """A combined schedule whose inputs were all removed is permanently off."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    await _setup(hass, _combined("Empty", []))
    state = hass.states.get("binary_sensor.empty")
    assert state.state == "off"
    assert state.attributes["next_transition"] is None
    assert state.attributes["resolved_schedules"] == []


@pytest.mark.asyncio
async def test_nested_combined_expands_to_plain_schedules(
    hass: HomeAssistant, freezer
) -> None:
    """(Morning OR Evening) AND Daytime, built by nesting combined schedules."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 06:30:00+00:00")
    await _setup(
        hass,
        _time_schedule("Morning", "06:00", "08:00"),
        _time_schedule("Evening", "20:00", "23:00"),
        _time_schedule("Daytime", "07:00", "21:00"),
        _combined("Either", ["binary_sensor.morning", "binary_sensor.evening"]),
        _combined(
            "Both",
            ["binary_sensor.either", "binary_sensor.daytime"],
            operator=SCHEDULE_OPERATOR_ALL,
        ),
    )
    state = hass.states.get("binary_sensor.both")
    assert state.state == "off"
    assert state.attributes["next_transition"] == "2026-07-02T07:00:00+00:00"
    # The inner combined schedule is expanded, not listed.
    assert state.attributes["resolved_schedules"] == [
        "binary_sensor.daytime",
        "binary_sensor.evening",
        "binary_sensor.morning",
    ]

    await _move_to(hass, freezer, "2026-07-02 07:00:02+00:00")
    state = hass.states.get("binary_sensor.both")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T07:00:00+00:00"

    await _move_to(hass, freezer, "2026-07-02 08:00:02+00:00")
    assert hass.states.get("binary_sensor.both").state == "off"

    await _move_to(hass, freezer, "2026-07-02 20:00:02+00:00")
    state = hass.states.get("binary_sensor.both")
    assert state.state == "on"
    assert state.attributes["next_transition"] == "2026-07-02T21:00:00+00:00"


@pytest.mark.asyncio
async def test_unavailable_input_only_matters_when_it_could_change_the_result(
    hass: HomeAssistant, freezer
) -> None:
    """An unavailable input makes the result unknown only when it could decide it."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 21:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "off")
    await _setup(
        hass,
        _time_schedule("Evening", "20:00", "23:00"),
        _mirror_schedule("House", "binary_sensor.house_mode"),
        _combined("Any", ["binary_sensor.evening", "binary_sensor.house"]),
        _combined(
            "All",
            ["binary_sensor.evening", "binary_sensor.house"],
            operator=SCHEDULE_OPERATOR_ALL,
        ),
    )
    hass.states.async_set("binary_sensor.house_mode", "unavailable")
    await settle(hass)
    assert hass.states.get("binary_sensor.house").state == "unavailable"
    # Evening is on, so "any" is on whatever House is; "all" depends on it.
    any_state = hass.states.get("binary_sensor.any")
    assert any_state.state == "on"
    assert any_state.attributes["current_window_start"] == "2026-07-02T20:00:00+00:00"
    assert hass.states.get("binary_sensor.all").state == "unavailable"

    # Evening ends: now "any" depends on House and "all" is off regardless.
    await _move_to(hass, freezer, "2026-07-02 23:00:02+00:00")
    assert hass.states.get("binary_sensor.any").state == "unavailable"
    assert hass.states.get("binary_sensor.all").state == "off"

    # House recovers on. Nothing shows the combination was ever off, so the
    # outage reads as a blip in the same on-period.
    hass.states.async_set("binary_sensor.house_mode", "on")
    await settle(hass)
    any_state = hass.states.get("binary_sensor.any")
    assert any_state.state == "on"
    assert any_state.attributes["current_window_start"] == "2026-07-02T20:00:00+00:00"

    # Once "any" is known off, House's next on-period is a new one.
    hass.states.async_set("binary_sensor.house_mode", "off")
    await settle(hass)
    assert hass.states.get("binary_sensor.any").state == "off"
    await _move_to(hass, freezer, "2026-07-02 23:30:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "on")
    await settle(hass)
    any_state = hass.states.get("binary_sensor.any")
    assert any_state.state == "on"
    assert any_state.attributes["current_window_start"] == "2026-07-02T23:30:00+00:00"


@pytest.mark.asyncio
async def test_inputs_reached_twice_never_glitch(hass: HomeAssistant, freezer) -> None:
    """A sensor reached through two routes never shows a half-updated result.

    Each combination below is always on. Two mirrors of one sensor change as
    separate updates, and a nested combined schedule would lag its input if
    it were read by state.
    """
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "off")
    await _setup(
        hass,
        _mirror_schedule("Home", "binary_sensor.house_mode"),
        _mirror_schedule("Away", "binary_sensor.house_mode", invert=True),
        _combined("Mirrors", ["binary_sensor.home", "binary_sensor.away"]),
        _combined("Not Home", ["binary_sensor.home"], invert=True),
        _combined("Nested", ["binary_sensor.home", "binary_sensor.not_home"]),
    )
    assert hass.states.get("binary_sensor.mirrors").state == "on"
    assert hass.states.get("binary_sensor.nested").state == "on"

    markers = {
        entity_id: hass.states.get(entity_id).attributes["current_window_start"]
        for entity_id in ("binary_sensor.mirrors", "binary_sensor.nested")
    }
    mirrors = _record_states(hass, "binary_sensor.mirrors")
    nested = _record_states(hass, "binary_sensor.nested")
    for value in ("on", "off", "on", "off"):
        hass.states.async_set("binary_sensor.house_mode", value)
        await settle(hass)

    assert set(mirrors) <= {"on"}
    assert set(nested) <= {"on"}
    for entity_id, marker in markers.items():
        state = hass.states.get(entity_id)
        assert state.state == "on"
        assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
async def test_marker_holds_while_inputs_hand_over(
    hass: HomeAssistant, freezer
) -> None:
    """The on-period's start doesn't move when the input that began it turns off
    while another keeps the combination on, even though that input's earlier
    history is then unknown."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 08:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "off")
    await _setup(
        hass,
        _time_schedule("Late Morning", "10:00", "12:00"),
        _mirror_schedule("House", "binary_sensor.house_mode"),
        _combined("Either", ["binary_sensor.late_morning", "binary_sensor.house"]),
    )
    await _move_to(hass, freezer, "2026-07-02 09:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "on")
    await settle(hass)
    state = hass.states.get("binary_sensor.either")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T09:00:00+00:00"

    await _move_to(hass, freezer, "2026-07-02 11:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "off")
    await settle(hass)
    state = hass.states.get("binary_sensor.either")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T09:00:00+00:00"

    await _move_to(hass, freezer, "2026-07-02 12:00:02+00:00")
    assert hass.states.get("binary_sensor.either").state == "off"


@pytest.mark.asyncio
async def test_restart_keeps_marker_when_start_came_from_last_changed(
    hass: HomeAssistant, freezer
) -> None:
    """A mirror's off-since time resets on restart, so an on-period that began
    there keeps its restored marker instead of re-triggering Follow mode."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "off")
    marker = "2026-07-01T09:00:00+00:00"
    mock_restore_cache(
        hass,
        [State("binary_sensor.out", "on", {"current_window_start": marker})],
    )
    await _setup(
        hass,
        _mirror_schedule("House", "binary_sensor.house_mode"),
        _combined("Out", ["binary_sensor.house"], invert=True),
    )
    state = hass.states.get("binary_sensor.out")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
async def test_restart_rederives_an_unparsable_marker(
    hass: HomeAssistant, freezer
) -> None:
    """A restored marker that does not parse is replaced by the derived start."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "off")
    mock_restore_cache(
        hass,
        [State("binary_sensor.out", "on", {"current_window_start": "garbage"})],
    )
    await _setup(
        hass,
        _mirror_schedule("House", "binary_sensor.house_mode"),
        _combined("Out", ["binary_sensor.house"], invert=True),
    )
    since = hass.states.get("binary_sensor.house").last_changed
    state = hass.states.get("binary_sensor.out")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == since.isoformat()


@pytest.mark.asyncio
async def test_start_from_last_changed_is_used_without_a_restored_period(
    hass: HomeAssistant, freezer
) -> None:
    """With no on-period to continue, the mirror's off-since time is the start."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "off")
    await _setup(
        hass,
        _mirror_schedule("House", "binary_sensor.house_mode"),
        _combined("Out", ["binary_sensor.house"], invert=True),
    )
    since = hass.states.get("binary_sensor.house").last_changed
    state = hass.states.get("binary_sensor.out")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == since.isoformat()


@pytest.mark.asyncio
async def test_restart_catches_up_a_missed_time_window(
    hass: HomeAssistant, freezer
) -> None:
    """Time-window starts don't move on restart, so a new one replaces the
    restored marker (Follow mode then catches up the missed start)."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 21:00:00+00:00")
    mock_restore_cache(
        hass,
        [
            State(
                "binary_sensor.bedside",
                "on",
                {"current_window_start": "2026-07-01T20:00:00+00:00"},
            )
        ],
    )
    await _setup(
        hass,
        _time_schedule("Evening", "20:00", "23:00"),
        _combined("Bedside", ["binary_sensor.evening"]),
    )
    state = hass.states.get("binary_sensor.bedside")
    assert state.attributes["current_window_start"] == "2026-07-02T20:00:00+00:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["reload", "restart"])
async def test_handed_over_marker_survives_outage_and_restart(
    hass: HomeAssistant, freezer, how: str
) -> None:
    """A marker the inputs can't re-derive is restored, not recomputed."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 07:00:00+00:00")
    hass.states.async_set("binary_sensor.src_a", "on")
    hass.states.async_set("binary_sensor.src_b", "off")
    combined = _combined("Either", ["binary_sensor.mirror_a", "binary_sensor.mirror_b"])
    await _setup(
        hass,
        _mirror_schedule("Mirror A", "binary_sensor.src_a"),
        _mirror_schedule("Mirror B", "binary_sensor.src_b"),
        combined,
    )
    marker = "2026-07-02T07:00:00+00:00"
    state = hass.states.get("binary_sensor.either")
    assert state.attributes["current_window_start"] == marker

    # B takes over from A: still the period that began at 07:00.
    await _move_to(hass, freezer, "2026-07-02 08:00:00+00:00")
    hass.states.async_set("binary_sensor.src_b", "on")
    await settle(hass)
    await _move_to(hass, freezer, "2026-07-02 08:30:00+00:00")
    hass.states.async_set("binary_sensor.src_a", "off")
    await settle(hass)

    await _move_to(hass, freezer, "2026-07-02 09:00:00+00:00")
    hass.states.async_set("binary_sensor.src_b", "unavailable")
    await settle(hass)
    state = hass.states.get("binary_sensor.either")
    assert state.state == "unavailable"
    assert "current_window_start" not in state.attributes

    if how == "reload":
        assert await hass.config_entries.async_reload(combined.entry_id)
        await settle(hass)
    else:
        await restart_entries(hass, combined)
    hass.states.async_set("binary_sensor.src_b", "on")
    await settle(hass)
    state = hass.states.get("binary_sensor.either")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
async def test_startup_holds_state_saved_during_an_outage(
    hass: HomeAssistant, freezer
) -> None:
    """The on/off value saved while unavailable is held while HA starts."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 09:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "on")
    combined = _combined("Bedside", ["binary_sensor.house"])
    await _setup(hass, _mirror_schedule("House", "binary_sensor.house_mode"), combined)
    marker = hass.states.get("binary_sensor.bedside").attributes["current_window_start"]
    hass.states.async_set("binary_sensor.house_mode", "unavailable")
    await settle(hass)
    assert hass.states.get("binary_sensor.bedside").state == "unavailable"

    await restart_entries(hass, combined, started=False)
    state = hass.states.get("binary_sensor.bedside")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker

    await finish_startup(hass)
    await settle(hass)
    assert hass.states.get("binary_sensor.bedside").state == "unavailable"


@pytest.mark.asyncio
async def test_startup_holds_restored_state_until_inputs_report(
    hass: HomeAssistant, freezer
) -> None:
    """While HA starts, an input that hasn't reported can't make it unavailable."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    marker = "2026-07-02T09:00:00+00:00"
    mock_restore_cache(
        hass,
        [State("binary_sensor.bedside", "on", {"current_window_start": marker})],
    )
    hass.set_state(CoreState.starting)
    # House's source hasn't loaded, so House is unavailable.
    await _setup(
        hass,
        _mirror_schedule("House", "binary_sensor.house_mode"),
        _combined("Bedside", ["binary_sensor.house"]),
    )
    assert hass.states.get("binary_sensor.house").state == "unavailable"
    state = hass.states.get("binary_sensor.bedside")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)
    assert hass.states.get("binary_sensor.bedside").state == "unavailable"


@pytest.mark.asyncio
async def test_editing_an_input_rebuilds_the_combination(
    hass: HomeAssistant, freezer
) -> None:
    """Inputs are read from config, so editing one must reach the combination."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 05:00:00+00:00")
    morning = _time_schedule("Morning", "06:00", "08:00")
    await _setup(hass, morning, _combined("Bedside", ["binary_sensor.morning"]))
    assert (
        hass.states.get("binary_sensor.bedside").attributes["next_transition"]
        == "2026-07-02T06:00:00+00:00"
    )

    hass.config_entries.async_update_entry(
        morning,
        options={
            CONF_NAME: "Morning",
            CONF_TIME_WINDOWS: [{"start": {"time": "06:30"}, "end": {"time": "08:00"}}],
        },
    )
    await settle(hass)
    assert (
        hass.states.get("binary_sensor.bedside").attributes["next_transition"]
        == "2026-07-02T06:30:00+00:00"
    )


@pytest.mark.asyncio
async def test_unload_drops_an_input_change_still_waiting_to_be_counted(
    hass: HomeAssistant, freezer
) -> None:
    """An input change counted on the next loop pass is dropped when the
    combination unloads first: no state is written and no boundary is armed."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 07:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "off")
    combined = _combined("Bedside", ["binary_sensor.morning", "binary_sensor.house"])
    await _setup(
        hass,
        _time_schedule("Morning", "06:00", "08:00"),
        _mirror_schedule("House", "binary_sensor.house_mode"),
        combined,
    )
    assert hass.states.get("binary_sensor.bedside").state == "on"
    writes = _record_states(hass, "binary_sensor.bedside")

    unloads = []

    @callback
    def _unload_before_counting(_event: Event) -> None:
        # Heard after the combination, which counts the change a pass later.
        unloads.append(
            hass.async_create_task(hass.config_entries.async_unload(combined.entry_id))
        )

    async_track_state_change_event(hass, "binary_sensor.house", _unload_before_counting)
    hass.states.async_set("binary_sensor.house_mode", "on")
    await settle(hass)
    assert await unloads[0]

    # A boundary armed after the unload would fail the lingering-timer check.
    assert writes == ["unavailable"]


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_loop_is_unknown_instead_of_hanging(
    hass: HomeAssistant, freezer, caplog
) -> None:
    """A loop the config flow would reject (e.g. a hand edit) is contained."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    loop_a = _combined("Loop A", ["binary_sensor.loop_b"])
    loop_b = _combined("Loop B", ["binary_sensor.loop_a"])
    with caplog.at_level(logging.WARNING):
        await _setup(hass, loop_a, loop_b)
        assert await hass.config_entries.async_reload(loop_a.entry_id)
        await settle(hass)
        # An edit of the root reaches its own entry-change handler too.
        hass.config_entries.async_update_entry(
            loop_a, options={**molight_config(loop_a), CONF_NAME: "Loop A2"}
        )
        await settle(hass)

    assert "includes itself" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert hass.states.get("binary_sensor.loop_a").state == "unavailable"
    assert hass.states.get("binary_sensor.loop_b").state == "unavailable"


@pytest.mark.asyncio
async def test_follow_light_turns_on_for_each_window(
    hass: HomeAssistant, freezer
) -> None:
    """A manual off in the morning doesn't stop the evening turn-on."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 05:00:00+00:00")
    await _setup(
        hass,
        _time_schedule("Morning", "06:00", "08:00"),
        _time_schedule("Evening", "20:00", "23:00"),
        _combined("Bedside", ["binary_sensor.morning", "binary_sensor.evening"]),
        make_light_entry(
            name="Bedside Lamp",
            schedule="binary_sensor.bedside",
            schedule_mode=SCHEDULE_MODE_FOLLOW,
        ),
    )
    assert hass.states.get("light.bedside_lamp").state == "off"

    await _move_to(hass, freezer, "2026-07-02 06:00:02+00:00")
    state = hass.states.get("light.bedside_lamp")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.bedside_lamp"}, blocking=True
    )
    await settle(hass)
    assert hass.states.get("light.bedside_lamp").state == "off"

    await _move_to(hass, freezer, "2026-07-02 08:00:02+00:00")
    assert hass.states.get("light.bedside_lamp").state == "off"

    await _move_to(hass, freezer, "2026-07-02 20:00:02+00:00")
    state = hass.states.get("light.bedside_lamp")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED

    calls = record_service_calls(hass)
    await _move_to(hass, freezer, "2026-07-02 23:00:02+00:00")
    assert light_targets(calls, "turn_off") == [["light.real_1"]]
    state = hass.states.get("light.bedside_lamp")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_inverted_empty_combination_is_off(hass: HomeAssistant, freezer) -> None:
    """With no inputs there is nothing to invert, so it stays off."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    await _setup(hass, _combined("Nothing", [], invert=True))
    state = hass.states.get("binary_sensor.nothing")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None
    assert state.attributes["next_transition"] is None

    await _move_to(hass, freezer, "2026-07-03 00:00:02+00:00")
    assert hass.states.get("binary_sensor.nothing").state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [1, 2])
async def test_inverting_an_empty_combination_is_off(
    hass: HomeAssistant, freezer, depth: int
) -> None:
    """An input with no schedules left counts as deleted, however deep it sits."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    nested = [_combined("Nothing", [])]
    if depth == 2:
        nested.append(_combined("Wrapper", ["binary_sensor.nothing"]))
    top = "binary_sensor.wrapper" if depth == 2 else "binary_sensor.nothing"
    await _setup(hass, *nested, _combined("Not Nothing", [top], invert=True))
    state = hass.states.get("binary_sensor.not_nothing")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None

    await _move_to(hass, freezer, "2026-07-03 00:00:02+00:00")
    assert hass.states.get("binary_sensor.not_nothing").state == "off"


@pytest.mark.asyncio
async def test_emptying_an_inner_combination_does_not_turn_its_inverse_on(
    hass: HomeAssistant, freezer
) -> None:
    """A porch on "Dark" = not "Daylight" stays off when Daylight's schedules go."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    morning = _time_schedule("Morning", "06:00", "12:00")
    afternoon = _time_schedule("Afternoon", "12:00", "20:00")
    daylight = _combined(
        "Daylight", ["binary_sensor.morning", "binary_sensor.afternoon"]
    )
    dark = _combined("Dark", ["binary_sensor.daylight"], invert=True)
    evening_light = _combined(
        "Evening Light",
        ["binary_sensor.evening", "binary_sensor.daylight"],
        operator=SCHEDULE_OPERATOR_ALL,
    )
    porch = make_light_entry(
        name="Porch",
        lights=["light.porch_real"],
        schedule="binary_sensor.dark",
        schedule_mode=SCHEDULE_MODE_FOLLOW,
    )
    hass.states.async_set("light.porch_real", "off")
    await _setup(
        hass,
        _time_schedule("Evening", "18:00", "23:00"),
        morning,
        afternoon,
        daylight,
        dark,
        evening_light,
        porch,
    )
    assert hass.states.get("binary_sensor.dark").state == "off"

    for entry in (morning, afternoon):
        await hass.config_entries.async_remove(entry.entry_id)
        await settle(hass)
    assert molight_config(daylight)[CONF_SCHEDULE_INPUTS] == []
    for when in ("2026-07-02 21:00:02+00:00", "2026-07-03 03:00:02+00:00"):
        await _move_to(hass, freezer, when)
        assert hass.states.get("binary_sensor.dark").state == "off"
        assert hass.states.get("light.porch").state == "off"
    # Evening AND an emptied Daylight is every evening, as a deleted input would be.
    await _move_to(hass, freezer, "2026-07-03 19:00:02+00:00")
    assert hass.states.get("binary_sensor.evening_light").state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize("invert", [False, True], ids=["plain", "inverted"])
@pytest.mark.parametrize("operator", [SCHEDULE_OPERATOR_ANY, SCHEDULE_OPERATOR_ALL])
async def test_deleting_every_input_leaves_the_combination_off(
    hass: HomeAssistant, freezer, operator: str, invert: bool
) -> None:
    """A follow-mode porch on "Not Day" does not come on for good when Day goes."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(
        "2026-07-02 03:00:00+00:00" if invert else "2026-07-02 12:00:00+00:00"
    )
    day = _time_schedule("Day", "07:00", "22:00")
    combined = _combined(
        "Porch Hours", ["binary_sensor.day"], operator=operator, invert=invert
    )
    porch = make_light_entry(
        name="Porch",
        lights=["light.porch_real"],
        schedule="binary_sensor.porch_hours",
        schedule_mode=SCHEDULE_MODE_FOLLOW,
    )
    hass.states.async_set("light.porch_real", "off")
    await _setup(hass, day, combined, porch)
    assert hass.states.get("binary_sensor.porch_hours").state == "on"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.porch"}, blocking=True
    )
    await _move_to(hass, freezer, "2026-07-02 12:00:00+00:00")
    assert hass.states.get("binary_sensor.porch_hours").state == (
        "off" if invert else "on"
    )

    await hass.config_entries.async_remove(day.entry_id)
    await settle(hass)
    assert molight_config(combined)[CONF_SCHEDULE_INPUTS] == []
    state = hass.states.get("binary_sensor.porch_hours")
    assert state.state == "off"
    assert state.attributes["next_transition"] is None
    for when in ("2026-07-02 23:00:02+00:00", "2026-07-03 12:00:02+00:00"):
        await _move_to(hass, freezer, when)
        assert hass.states.get("binary_sensor.porch_hours").state == "off"
        assert hass.states.get("light.porch").state == "off"


@pytest.mark.asyncio
async def test_deleting_one_input_of_an_all_combination_widens_it(
    hass: HomeAssistant, freezer
) -> None:
    """Evening AND Workday without Workday is every evening, weekends included."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-04 19:00:00+00:00")  # a Saturday
    hass.states.async_set("binary_sensor.workday_sensor", "off")
    workday = _mirror_schedule("Workday", "binary_sensor.workday_sensor")
    combined = _combined(
        "Work Evenings",
        ["binary_sensor.evening", "binary_sensor.workday"],
        operator=SCHEDULE_OPERATOR_ALL,
    )
    await _setup(hass, _time_schedule("Evening", "18:00", "23:00"), workday, combined)
    assert hass.states.get("binary_sensor.work_evenings").state == "off"

    await hass.config_entries.async_remove(workday.entry_id)
    await settle(hass)
    assert molight_config(combined)[CONF_SCHEDULE_INPUTS] == ["binary_sensor.evening"]
    assert hass.states.get("binary_sensor.work_evenings").state == "on"


@pytest.mark.asyncio
async def test_sun_anchored_input(hass: HomeAssistant, freezer) -> None:
    """A sun-based input's window is read from its config, sun event included."""
    day = date(2026, 7, 2)
    sunset = get_astral_event_date(hass, "sunset", day)
    assert sunset is not None
    freezer.move_to(sunset + timedelta(minutes=5))
    await _setup(
        hass,
        _time_schedule(
            "Evening",
            {"time": "23:00", "sun": "sunset", "combine": "earliest"},
            {"time": "23:30"},
        ),
        _combined("Dusk", ["binary_sensor.evening"]),
    )
    state = hass.states.get("binary_sensor.dusk")
    assert state.state == "on"
    assert datetime.fromisoformat(state.attributes["current_window_start"]) == sunset
    end = datetime.fromisoformat(state.attributes["next_transition"])
    assert end > sunset
    assert dt_util.as_local(end).strftime("%H:%M") == "23:30"


@pytest.mark.asyncio
async def test_dst_gap_edges_order_by_instant_and_marker_is_local(
    hass: HomeAssistant, freezer
) -> None:
    """A window resolved into a DST gap lands on a later instant than a later
    wall time; the combination orders by instant and reports local markers."""
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to("2026-03-08 06:50:00+00:00")  # 01:50 EST, before both edges
    await _setup(
        hass,
        _time_schedule("Late", "03:05", "06:00"),
        _time_schedule("Gap", "02:30", "02:45"),
        _combined("Either", ["binary_sensor.late", "binary_sensor.gap"]),
    )
    state = hass.states.get("binary_sensor.either")
    assert state.state == "off"
    # 02:30 resolves into the gap as EST (07:30 UTC), after 03:05 EDT (07:05 UTC).
    assert state.attributes["next_transition"] == "2026-03-08T03:05:00-04:00"

    await _move_to(hass, freezer, "2026-03-08 07:06:00+00:00")
    state = hass.states.get("binary_sensor.either")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-03-08T03:05:00-04:00"
    assert state.attributes["next_transition"] == "2026-03-08T06:00:00-04:00"


@pytest.mark.asyncio
async def test_dst_fold_edges_fire_once_at_their_first_occurrence(
    hass: HomeAssistant, freezer
) -> None:
    """On 2026-11-01 in New York, 01:00 to 02:00 happens twice. Edges in that
    hour take their daylight-time occurrence, so an input ending and another
    starting at 01:30 hand over once, and the repeated hour is quiet."""
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to("2026-11-01 01:00:00+00:00")  # 21:00 EDT the evening before
    await _setup(
        hass,
        _time_schedule("Late", "22:00", "01:30"),
        _time_schedule("Fold", "01:30", "01:45"),
        _combined("Either", ["binary_sensor.late", "binary_sensor.fold"]),
    )
    state = hass.states.get("binary_sensor.either")
    assert state.state == "off"
    assert state.attributes["next_transition"] == "2026-10-31T22:00:00-04:00"

    await _move_to(hass, freezer, "2026-11-01 02:00:02+00:00")
    state = hass.states.get("binary_sensor.either")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-10-31T22:00:00-04:00"
    assert state.attributes["next_transition"] == "2026-11-01T01:45:00-04:00"

    await _move_to(hass, freezer, "2026-11-01 05:30:02+00:00")  # 01:30 EDT
    state = hass.states.get("binary_sensor.either")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-10-31T22:00:00-04:00"

    await _move_to(hass, freezer, "2026-11-01 05:45:02+00:00")  # 01:45 EDT
    state = hass.states.get("binary_sensor.either")
    assert state.state == "off"
    assert state.attributes["next_transition"] == "2026-11-01T22:00:00-05:00"

    # The same wall times again, now in standard time.
    for when in (
        "2026-11-01 06:30:02+00:00",
        "2026-11-01 06:45:02+00:00",
        "2026-11-01 07:00:02+00:00",
    ):
        await _move_to(hass, freezer, when)
        state = hass.states.get("binary_sensor.either")
        assert state.state == "off"
        assert state.attributes["next_transition"] == "2026-11-01T22:00:00-05:00"


@pytest.mark.asyncio
async def test_editing_a_nested_input_rebuilds_the_outer_combination(
    hass: HomeAssistant, freezer
) -> None:
    """An edit two levels down reaches the outer combination."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 05:00:00+00:00")
    inner = _combined("Inner", ["binary_sensor.morning"])
    await _setup(
        hass,
        _time_schedule("Morning", "06:00", "08:00"),
        _time_schedule("Evening", "20:00", "23:00"),
        inner,
        _combined("Outer", ["binary_sensor.inner"]),
    )
    state = hass.states.get("binary_sensor.outer")
    assert state.attributes["next_transition"] == "2026-07-02T06:00:00+00:00"
    assert state.attributes["resolved_schedules"] == ["binary_sensor.morning"]

    hass.config_entries.async_update_entry(
        inner,
        options={
            CONF_NAME: "Inner",
            CONF_SCHEDULE_INPUTS: ["binary_sensor.evening"],
            CONF_SCHEDULE_OPERATOR: SCHEDULE_OPERATOR_ANY,
            CONF_SCHEDULE_INVERT: False,
        },
    )
    await settle(hass)
    state = hass.states.get("binary_sensor.outer")
    assert state.attributes["next_transition"] == "2026-07-02T20:00:00+00:00"
    assert state.attributes["resolved_schedules"] == ["binary_sensor.evening"]


# ---------------------------------------------------------------------------
# Sun edges past a fixed edge, and polar periods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("when", "bedside", "workdays", "not_bedside"),
    [
        ("2026-06-22 07:00", "on", "on", "off"),
        ("2026-06-22 12:00", "off", "off", "on"),
        # Sunset is at 23:42, after the evening window's 23:00 end.
        ("2026-06-22 23:30", "off", "off", "on"),
        ("2026-06-22 23:50", "off", "off", "on"),
        ("2026-06-23 03:00", "off", "off", "on"),
        # In September sunset (19:57) is before it again.
        ("2026-09-22 21:00", "on", "on", "off"),
    ],
)
async def test_sun_input_past_its_fixed_end_adds_no_window(
    hass: HomeAssistant,
    freezer,
    when: str,
    bedside: str,
    workdays: str,
    not_bedside: str,
) -> None:
    """EXAMPLES Example 11 where the June sunset is after 23:00: the evening
    input is empty, not on round the clock, in every combination built on it."""
    tz = await set_home(hass, *ANCHORAGE)
    freezer.move_to(datetime.fromisoformat(when).replace(tzinfo=tz))
    hass.states.async_set("binary_sensor.workday", "on")
    await _setup(
        hass,
        _time_schedule("Bedside Morning", "06:30", "08:00"),
        _time_schedule("Bedside Evening", {"sun": "sunset"}, "23:00"),
        _mirror_schedule("Workday", "binary_sensor.workday"),
        _combined(
            "Bedside Schedule",
            ["binary_sensor.bedside_morning", "binary_sensor.bedside_evening"],
        ),
        _combined(
            "Bedside Workdays",
            ["binary_sensor.bedside_schedule", "binary_sensor.workday_2"],
            operator=SCHEDULE_OPERATOR_ALL,
        ),
        _combined("Not Bedside", ["binary_sensor.bedside_schedule"], invert=True),
    )
    assert hass.states.get("binary_sensor.bedside_schedule").state == bedside
    assert hass.states.get("binary_sensor.bedside_workdays").state == workdays
    assert hass.states.get("binary_sensor.not_bedside").state == not_bedside


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "end", "when", "expected"),
    [
        # No sunrise on 01-20; the first one is on 01-21 at 10:29.
        ("20:00", {"sun": "sunrise"}, "2026-01-20 22:00", "on"),
        ("20:00", {"sun": "sunrise"}, "2026-01-21 11:00", "off"),
        # The first night after the midnight sun: sunset 00:31, sunrise 01:12.
        ({"sun": "sunset"}, {"sun": "sunrise"}, "2026-07-26 00:50", "on"),
        ({"sun": "sunset"}, {"sun": "sunrise"}, "2026-07-26 01:30", "off"),
        # No sun event, no window.
        ({"sun": "sunrise"}, {"sun": "sunset"}, "2026-06-21 12:00", "off"),
    ],
)
async def test_polar_transition_windows_in_a_combination(
    hass: HomeAssistant, freezer, start: str | dict, end: dict, when: str, expected: str
) -> None:
    """A time-window input keeps the nights next to a polar period."""
    tz = await set_home(hass, *TROMSO)
    freezer.move_to(datetime.fromisoformat(when).replace(tzinfo=tz))
    await _setup(
        hass,
        _time_schedule("Night", start, end),
        _combined("Nights", ["binary_sensor.night"]),
        _combined("Days", ["binary_sensor.nights"], invert=True),
    )
    assert hass.states.get("binary_sensor.nights").state == expected
    opposite = "off" if expected == "on" else "on"
    assert hass.states.get("binary_sensor.days").state == opposite


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("when", "expected"),
    [
        # Sunset is about 15:55, so sunset + 4 h is before midnight.
        ("2026-12-01 22:00", "on"),
        ("2026-12-02 00:10", "off"),
    ],
)
async def test_input_ending_at_the_midnight_after_its_sun_event(
    hass: HomeAssistant, freezer, when: str, expected: str
) -> None:
    """An input that ends at the later of sunset + 4 h and 00:00 runs to the
    midnight that follows, in a combination and in one nested or inverted."""
    tz = await set_home(hass, *LONDON)
    freezer.move_to(datetime.fromisoformat(when).replace(tzinfo=tz))
    await _setup(
        hass,
        _time_schedule(
            "Evening",
            {"sun": "sunset"},
            {"time": "00:00", "sun": "sunset", "offset": 240, "combine": "latest"},
        ),
        _combined("Evenings", ["binary_sensor.evening"]),
        _combined("Nested", ["binary_sensor.evenings"], operator=SCHEDULE_OPERATOR_ALL),
        _combined("Not Evenings", ["binary_sensor.evenings"], invert=True),
    )
    opposite = "off" if expected == "on" else "on"
    assert hass.states.get("binary_sensor.evenings").state == expected
    assert hass.states.get("binary_sensor.nested").state == expected
    assert hass.states.get("binary_sensor.not_evenings").state == opposite


@pytest.mark.asyncio
@pytest.mark.parametrize(("when", "expected"), [("05:30", "on"), ("15:00", "off")])
async def test_input_pairs_a_fixed_time_with_the_same_days_sun_event_in_samoa(
    hass: HomeAssistant, freezer, when: str, expected: str
) -> None:
    """A 05:00 -> sunrise input in Apia ends at that morning's sunrise."""
    tz = await set_home(hass, *APIA)
    freezer.move_to(datetime.fromisoformat(f"2026-06-21 {when}").replace(tzinfo=tz))
    await _setup(
        hass,
        _time_schedule("Early", "05:00", {"sun": "sunrise"}),
        _combined("Mornings", ["binary_sensor.early"]),
        _combined("Nested", ["binary_sensor.mornings"], operator=SCHEDULE_OPERATOR_ALL),
        _combined("Not Mornings", ["binary_sensor.mornings"], invert=True),
    )
    opposite = "off" if expected == "on" else "on"
    assert hass.states.get("binary_sensor.mornings").state == expected
    assert hass.states.get("binary_sensor.nested").state == expected
    assert hass.states.get("binary_sensor.not_mornings").state == opposite


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("combined", "entity_id"),
    [
        (
            _combined("Day", ["binary_sensor.night"], invert=True),
            "binary_sensor.day",
        ),
        (
            _combined("Day", ["binary_sensor.not_night"]),
            "binary_sensor.day",
        ),
    ],
    ids=["inverted combination", "combination of an inverted schedule"],
)
async def test_inverted_sun_window_keeps_one_marker_through_the_midnight_sun(
    hass: HomeAssistant, freezer, combined: MockConfigEntry, entity_id: str
) -> None:
    """Daytime in Tromso is one on-period from the last sunrise on 05-18,
    however many days the sun then stays up."""
    await set_home(hass, *TROMSO)
    freezer.move_to("2026-05-18 12:00:00+02:00")
    night = _time_schedule("Night", {"sun": "sunset"}, {"sun": "sunrise"})
    not_night = MockConfigEntry(
        domain=DOMAIN,
        data={
            **_time_schedule("Not Night", {"sun": "sunset"}, {"sun": "sunrise"}).data,
            CONF_SCHEDULE_INVERT: True,
        },
    )
    await _setup(hass, night, not_night, combined)
    state = hass.states.get(entity_id)
    assert state.state == "on"
    began = state.attributes["current_window_start"]
    assert began.startswith("2026-05-18T00:57")

    for day in range(19, 31):
        for moment in ("00:00:05", "12:00:00"):
            await _move_to(hass, freezer, f"2026-05-{day} {moment}+02:00")
            state = hass.states.get(entity_id)
            assert state.state == "on"
            assert state.attributes["current_window_start"] == began, day


# ---------------------------------------------------------------------------
# A time zone or home location change while the combination runs
# ---------------------------------------------------------------------------


async def _update_config(hass: HomeAssistant, **changes) -> None:
    await hass.config.async_update(**changes)
    await settle(hass)


@pytest.mark.asyncio
async def test_time_zone_change_ends_the_combined_window_and_arms_the_new_boundary(
    hass: HomeAssistant, freezer
) -> None:
    """18:00 -> 23:00 at 20:00 UTC; in Toronto it is 16:00. The combination,
    one nested on it and its inverse all move to the new clock, and nothing
    is left armed for the old 23:00 UTC end."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(
        hass,
        _time_schedule("Evening", "18:00", "23:00"),
        _combined("Evenings", ["binary_sensor.evening"]),
        _combined("Nested", ["binary_sensor.evenings"], operator=SCHEDULE_OPERATOR_ALL),
        _combined("Not Evenings", ["binary_sensor.evenings"], invert=True),
    )
    entities = {
        "binary_sensor.evenings": "on",
        "binary_sensor.nested": "on",
        "binary_sensor.not_evenings": "off",
    }

    def _assert(inside: bool, next_transition: str) -> None:
        for entity_id, inside_state in entities.items():
            state = hass.states.get(entity_id)
            assert (state.state == inside_state) is inside, entity_id
            assert state.attributes["next_transition"] == next_transition

    _assert(True, "2026-07-02T23:00:00+00:00")
    await _update_config(hass, time_zone="America/Toronto")
    _assert(False, "2026-07-02T18:00:00-04:00")

    await _move_to(hass, freezer, "2026-07-02 22:00:02+00:00")
    _assert(True, "2026-07-02T23:00:00-04:00")
    state = hass.states.get("binary_sensor.evenings")
    assert state.attributes["current_window_start"] == "2026-07-02T18:00:00-04:00"

    # 23:00 UTC, the old end, is 19:00 in Toronto.
    await _move_to(hass, freezer, "2026-07-02 23:00:02+00:00")
    _assert(True, "2026-07-02T23:00:00-04:00")


@pytest.mark.asyncio
async def test_time_zone_change_inside_the_combined_window_keeps_its_marker(
    hass: HomeAssistant, freezer
) -> None:
    """08:00 -> 22:00 is under way at 12:00 UTC and in Toronto (08:00). The
    combination stays on: one window, through the old boundary, a
    source-backed input changing, and a restart."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    hass.states.async_set("binary_sensor.guests", "off")
    await settle(hass)
    entries = (
        _time_schedule("Day", "08:00", "22:00"),
        _mirror_schedule("Guests Here", "binary_sensor.guests"),
        _combined("Lit", ["binary_sensor.day", "binary_sensor.guests_here"]),
        _combined("Nested", ["binary_sensor.lit"], operator=SCHEDULE_OPERATOR_ALL),
    )
    await _setup(hass, *entries)
    marker = "2026-07-02T08:00:00+00:00"
    watched = ("binary_sensor.lit", "binary_sensor.nested")

    def _assert_same_window() -> None:
        for entity_id in watched:
            state = hass.states.get(entity_id)
            assert state.state == "on", entity_id
            assert state.attributes["current_window_start"] == marker, entity_id

    _assert_same_window()
    await _update_config(hass, time_zone="America/Toronto")
    _assert_same_window()
    state = hass.states.get("binary_sensor.lit")
    assert state.attributes["next_transition"] == "2026-07-02T22:00:00-04:00"

    for guests in ("on", "off"):
        freezer.tick(timedelta(minutes=5))
        hass.states.async_set("binary_sensor.guests", guests)
        await settle(hass)
        _assert_same_window()

    await _move_to(hass, freezer, "2026-07-02 22:00:02+00:00")
    _assert_same_window()

    freezer.move_to("2026-07-02 23:00:00+00:00")
    await restart_entries(hass, *entries)
    await settle(hass)
    _assert_same_window()

    # The window ends at 22:00 in Toronto; tomorrow's is a new one.
    await _move_to(hass, freezer, "2026-07-03 02:00:02+00:00")
    assert hass.states.get("binary_sensor.lit").state == "off"
    await _move_to(hass, freezer, "2026-07-03 12:00:02+00:00")
    state = hass.states.get("binary_sensor.lit")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-03T08:00:00-04:00"


@pytest.mark.asyncio
async def test_time_zone_change_while_an_input_is_out_keeps_the_marker(
    hass: HomeAssistant, freezer
) -> None:
    """With All, an unavailable input leaves the result unknown as the time
    zone changes. When it is back the window is still the one that began."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 09:00:00+00:00")
    hass.states.async_set("binary_sensor.guests", "on")
    await settle(hass)
    freezer.move_to("2026-07-02 12:30:00+00:00")
    await _setup(
        hass,
        _time_schedule("Day", "08:00", "22:00"),
        _mirror_schedule("Guests Here", "binary_sensor.guests"),
        _combined(
            "Lit",
            ["binary_sensor.day", "binary_sensor.guests_here"],
            operator=SCHEDULE_OPERATOR_ALL,
        ),
    )
    marker = hass.states.get("binary_sensor.lit").attributes["current_window_start"]
    assert marker == "2026-07-02T09:00:00+00:00"

    # In Toronto the day window starts at 12:00 UTC, after the marker.
    hass.states.async_set("binary_sensor.guests", "unavailable")
    await settle(hass)
    assert hass.states.get("binary_sensor.lit").state == "unavailable"
    await _update_config(hass, time_zone="America/Toronto")
    hass.states.async_set("binary_sensor.guests", "on")
    await settle(hass)
    state = hass.states.get("binary_sensor.lit")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
async def test_location_change_reevaluates_a_sun_input(
    hass: HomeAssistant, freezer
) -> None:
    """Ten minutes after sunset in London; 15 degrees west the sun sets an
    hour later, so the input's window has not started at the new home."""
    await hass.config.async_set_time_zone("UTC")
    hass.config.latitude = 51.5
    hass.config.longitude = 0.0
    sunset = get_astral_event_date(hass, "sunset", date(2026, 7, 2))
    freezer.move_to(sunset + timedelta(minutes=10))
    await _setup(
        hass,
        _time_schedule("Evening", {"sun": "sunset"}, "23:59"),
        _combined("Evenings", ["binary_sensor.evening"]),
        _combined("Not Evenings", ["binary_sensor.evenings"], invert=True),
    )
    assert hass.states.get("binary_sensor.evenings").state == "on"

    await _update_config(hass, latitude=51.5, longitude=-15.0)
    later_sunset = get_astral_event_date(hass, "sunset", date(2026, 7, 2))
    state = hass.states.get("binary_sensor.evenings")
    assert state.state == "off"
    assert datetime.fromisoformat(state.attributes["next_transition"]) == later_sunset
    assert hass.states.get("binary_sensor.not_evenings").state == "on"


@pytest.mark.asyncio
async def test_follow_light_manual_off_stands_through_a_time_zone_change(
    hass: HomeAssistant, freezer
) -> None:
    """The combination stays on through the change, so the light turned off
    by hand is not lit again as if a new window had started."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    hass.states.async_set("light.real_1", "off")
    await _setup(
        hass,
        _time_schedule("Day", "08:00", "22:00"),
        _combined("Lit", ["binary_sensor.day"]),
        make_light_entry(
            name="Desk Lamp",
            schedule="binary_sensor.lit",
            schedule_mode=SCHEDULE_MODE_FOLLOW,
        ),
    )
    assert hass.states.get("light.desk_lamp").state == "on"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.desk_lamp"}, blocking=True
    )
    await settle(hass)

    await _update_config(hass, time_zone="America/Toronto")
    assert hass.states.get("binary_sensor.lit").state == "on"
    assert hass.states.get("light.desk_lamp").state == "off"
    await _move_to(hass, freezer, "2026-07-02 22:00:02+00:00")
    assert hass.states.get("light.desk_lamp").state == "off"


# ---------------------------------------------------------------------------
# A source-backed input is dated from its window marker, not its last change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_state", "invert"),
    [("on", False), ("off", True)],
    ids=["plain", "inverted"],
)
async def test_combined_period_starts_when_a_source_backed_input_started(
    hass: HomeAssistant, freezer, source_state: str, invert: bool
) -> None:
    """A mirror set up two hours into its source's on-period dates its window
    from the source, while its own state is new. The combination, and one
    nested on it, start with the window, not with the mirror's state."""
    started = datetime(2026, 7, 2, 18, 0, tzinfo=dt_util.UTC)
    freezer.move_to(started)
    hass.states.async_set("binary_sensor.away", source_state)
    await settle(hass)
    freezer.move_to(started + timedelta(hours=2))
    await _setup(
        hass,
        _mirror_schedule("Away Mode", "binary_sensor.away", invert=invert),
        _combined("Away Combined", ["binary_sensor.away_mode"]),
        _combined(
            "Away Nested",
            ["binary_sensor.away_combined"],
            operator=SCHEDULE_OPERATOR_ALL,
        ),
    )
    mirror = hass.states.get("binary_sensor.away_mode")
    assert mirror.state == "on"
    assert mirror.attributes["current_window_start"] == started.isoformat()
    assert mirror.last_changed == started + timedelta(hours=2)
    for entity_id in ("binary_sensor.away_combined", "binary_sensor.away_nested"):
        state = hass.states.get(entity_id)
        assert state.state == "on"
        marker = datetime.fromisoformat(state.attributes["current_window_start"])
        assert marker == started, entity_id
