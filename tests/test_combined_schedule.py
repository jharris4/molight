"""Tests for the MoLight Virtual Combined Schedule."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.core import CoreState, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

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
from tests.conftest import make_light_entry, settle

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant


def _time_schedule(name: str, start: str, end: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: name,
            CONF_TIME_WINDOWS: [{"start": start, "end": end}],
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
            CONF_TIME_WINDOWS: [{"start": "06:30", "end": "08:00"}],
        },
    )
    await settle(hass)
    assert (
        hass.states.get("binary_sensor.bedside").attributes["next_transition"]
        == "2026-07-02T06:30:00+00:00"
    )


@pytest.mark.asyncio
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

    await _move_to(hass, freezer, "2026-07-02 23:00:02+00:00")
    state = hass.states.get("light.bedside_lamp")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
