"""Tests for the MoLight Virtual Schedule Sensor."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant.core import State
from homeassistant.helpers import restore_state
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_restore_state_shutdown_restart,
    mock_restore_cache,
)

from custom_components.molight.binary_sensor import (
    _end_days_after,
    _resolve_window,
    _sun_event,
)
from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_SCHEDULE_DEFINITION,
    CONF_SCHEDULE_INVERT,
    CONF_SCHEDULE_SOURCE,
    CONF_TIME_WINDOWS,
    DOMAIN,
    ENTITY_TYPE_SCHEDULE,
    SCHEDULE_DEFINITION_BINARY_SENSOR,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_IDLE,
)
from tests.conftest import (
    ANCHORAGE,
    APIA,
    AUCKLAND,
    CHATHAM,
    HONOLULU,
    KIRITIMATI,
    LONDON,
    TOKYO,
    TONGATAPU,
    TORONTO,
    TROMSO,
    make_light_entry,
    restart_entries,
    set_home,
    settle,
    setup_entries,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _window(start: str, end: str) -> dict:
    return {"start": {"time": start}, "end": {"time": end}}


def _schedule_entry(windows: list) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Night Schedule",
            CONF_TIME_WINDOWS: windows,
        },
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_fixed_overnight_window(hass: HomeAssistant, freezer) -> None:
    """A fixed-time overnight window flips at the exact boundaries."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(hass, _schedule_entry([_window("21:00", "07:00")]))

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["next_transition"] == "2026-07-02T21:00:00+00:00"

    # Window starts at 21:00.
    t = datetime(2026, 7, 2, 21, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T21:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-03T07:00:00+00:00"

    # Window ends at 07:00 the next morning.
    t = datetime(2026, 7, 3, 7, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None


@pytest.mark.asyncio
async def test_sun_anchored_edge(hass: HomeAssistant, freezer) -> None:
    """A sun-anchored edge resolves to the astral event, combined with the time."""
    # Default test location/timezone; sunset is ~20:00 local in July, so the
    # 'earliest of sunset and 23:00' start resolves to sunset.
    day = date(2026, 7, 2)
    sunset = get_astral_event_date(hass, "sunset", day)
    assert sunset is not None

    freezer.move_to(sunset + timedelta(minutes=5))
    await _setup(
        hass,
        _schedule_entry(
            [
                {
                    "start": {"time": "23:00", "sun": "sunset", "combine": "earliest"},
                    "end": {"time": "23:30"},
                }
            ]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == sunset.isoformat()


@pytest.mark.asyncio
async def test_sun_edge_with_offset(hass: HomeAssistant, freezer) -> None:
    """A sun offset shifts the resolved edge (sunset - 15 min)."""
    day = date(2026, 7, 2)
    sunset = get_astral_event_date(hass, "sunset", day)
    assert sunset is not None
    shifted = sunset - timedelta(minutes=15)

    # 5 minutes after (sunset - 15) but still before plain sunset: only the
    # offset edge puts us inside the window.
    freezer.move_to(shifted + timedelta(minutes=5))
    await _setup(
        hass,
        _schedule_entry(
            [
                {
                    "start": {
                        "time": "23:00",
                        "sun": "sunset",
                        "offset": -15,
                        "combine": "earliest",
                    },
                    "end": {"time": "23:30"},
                }
            ]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == shifted.isoformat()


@pytest.mark.asyncio
async def test_polar_day_fixed_time_stands_alone(hass: HomeAssistant, freezer) -> None:
    """A sun anchor whose event doesn't occur (polar day/night) resolves to
    nothing, leaving the edge's fixed time to stand alone."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 21:30:00+00:00")
    with patch(
        "custom_components.molight.binary_sensor.get_astral_event_date",
        return_value=None,
    ):
        await _setup(
            hass,
            _schedule_entry(
                [
                    {
                        "start": {
                            "time": "21:00",
                            "sun": "sunset",
                            "combine": "latest",
                        },
                        "end": {"time": "23:00"},
                    }
                ]
            ),
        )
        state = hass.states.get("binary_sensor.night_schedule")

    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T21:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-02T23:00:00+00:00"


@pytest.mark.asyncio
async def test_overlapping_windows(hass: HomeAssistant, freezer) -> None:
    """Overlapping windows merge into one on-period: current_window_start is
    the merged start throughout, so Follow mode never sees a new window where
    one window ends inside another."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 21:00:00+00:00")
    await _setup(
        hass,
        _schedule_entry(
            [
                _window("18:00", "23:00"),
                _window("20:00", "02:00"),
            ]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T18:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-03T02:00:00+00:00"

    # The first window ends at 23:00, still inside the second window.
    t = datetime(2026, 7, 2, 23, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T18:00:00+00:00"

    # The overnight window ends at 02:00; now everything is over.
    t = datetime(2026, 7, 3, 2, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.night_schedule").state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("new_end", "checkpoints"),
    [
        ("21:30", [("21:30:02", "off"), ("22:00:02", "off")]),
        ("23:00", [("22:00:02", "on"), ("23:00:02", "off")]),
    ],
)
async def test_editing_windows_rearms_the_transition_timer(
    hass: HomeAssistant, freezer, new_end: str, checkpoints: list
) -> None:
    """An edit while the 22:00 edge is armed switches to the new edge only."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 21:00:00+00:00")
    entry = _schedule_entry([_window("07:00", "22:00")])
    await _setup(hass, entry)

    options = {k: v for k, v in entry.data.items() if k != CONF_ENTITY_TYPE}
    options[CONF_TIME_WINDOWS] = [_window("07:00", new_end)]
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["next_transition"] == f"2026-07-02T{new_end}:00+00:00"

    for moment, expected in checkpoints:
        t = datetime.fromisoformat(f"2026-07-02T{moment}+00:00")
        freezer.move_to(t)
        async_fire_time_changed(hass, t)
        await hass.async_block_till_done()
        assert hass.states.get("binary_sensor.night_schedule").state == expected


@pytest.mark.asyncio
async def test_full_day_window_is_one_window_per_day(
    hass: HomeAssistant, freezer
) -> None:
    """Touching windows aren't merged, so a 00:00 → 00:00 window starts each day."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    await _setup(hass, _schedule_entry([_window("00:00", "00:00")]))

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T00:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-03T00:00:00+00:00"


@pytest.mark.asyncio
async def test_follow_light_takes_each_day_of_a_full_day_window(
    hass: HomeAssistant, freezer
) -> None:
    """A light turned off by hand comes back on when the next day's window starts."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    light = make_light_entry(
        name="Desk Lamp",
        schedule="binary_sensor.night_schedule",
        schedule_mode=SCHEDULE_MODE_FOLLOW,
    )
    schedule = _schedule_entry([_window("00:00", "00:00")])
    await setup_entries(hass, schedule, light)
    state = hass.states.get("light.desk_lamp")
    assert state.state == "on"
    assert state.attributes["schedule_window_start"] == "2026-07-02T00:00:00+00:00"

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.desk_lamp"}, blocking=True
    )
    freezer.move_to("2026-07-03 00:00:02+00:00")
    async_fire_time_changed(hass)
    await settle(hass)

    assert hass.states.get("binary_sensor.night_schedule").state == "on"
    state = hass.states.get("light.desk_lamp")
    assert state.state == "on"
    assert state.attributes["schedule_window_start"] == "2026-07-03T00:00:00+00:00"

    # Runtime and reload agree: a manual off in the new window stands.
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.desk_lamp"}, blocking=True
    )
    assert await hass.config_entries.async_reload(light.entry_id)
    await settle(hass)
    assert hass.states.get("light.desk_lamp").state == "off"


_CHAINED = [_window("00:00", "23:00"), _window("22:00", "01:00")]


@pytest.mark.asyncio
async def test_windows_chained_around_the_clock_keep_one_marker(
    hass: HomeAssistant, freezer
) -> None:
    """Windows that overlap around the clock never go off, so the merged
    on-period keeps its first marker instead of one per evaluation."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    await _setup(hass, _schedule_entry(_CHAINED))
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    marker = state.attributes["current_window_start"]

    for when in (
        "2026-07-02 23:00:02+00:00",
        "2026-07-03 00:00:02+00:00",
        "2026-07-03 01:00:02+00:00",
        "2026-07-03 12:00:00+00:00",
        "2026-07-04 01:00:02+00:00",
        "2026-07-06 12:00:00+00:00",
    ):
        freezer.move_to(when)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        state = hass.states.get("binary_sensor.night_schedule")
        assert state.state == "on"
        assert state.attributes["current_window_start"] == marker


async def _restart_at(
    hass: HomeAssistant, freezer, entry: MockConfigEntry, when: str
) -> None:
    """Restart the entry from what Home Assistant saves now, at a later time."""
    await async_mock_restore_state_shutdown_restart(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to(when)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_windows_chained_around_the_clock_keep_the_marker_across_a_restart(
    hass: HomeAssistant, freezer
) -> None:
    """A restart days later, with the schedule still on, restores the marker."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    entry = _schedule_entry(_CHAINED)
    await _setup(hass, entry)
    marker = hass.states.get("binary_sensor.night_schedule").attributes[
        "current_window_start"
    ]

    await _restart_at(hass, freezer, entry, "2026-07-05 12:00:00+00:00")
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
@pytest.mark.parametrize("day", ["2026-07-03", "2026-07-04"])
async def test_restart_after_the_window_ended_starts_a_new_window(
    hass: HomeAssistant, freezer, day: str
) -> None:
    """A saved marker is dropped once the schedule has been off since it,
    whether that off is within the resolved days or before them."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 22:00:00+00:00")
    entry = _schedule_entry([_window("21:00", "07:00")])
    await _setup(hass, entry)
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.attributes["current_window_start"] == "2026-07-02T21:00:00+00:00"

    await _restart_at(hass, freezer, entry, f"{day} 22:00:00+00:00")
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == f"{day}T21:00:00+00:00"


@pytest.mark.asyncio
async def test_time_window_schedule_ignores_a_marker_saved_by_a_source_definition(
    hass: HomeAssistant, freezer
) -> None:
    """A marker saved while the schedule mirrored a sensor dates that sensor's
    on-period, not the window the schedule now follows."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 22:00:00+00:00")
    mock_restore_cache(
        hass,
        [
            State(
                "binary_sensor.night_schedule",
                "on",
                {
                    "current_window_start": "2026-07-02T21:30:00+00:00",
                    "source_entity": "binary_sensor.house_mode",
                    "inverted": False,
                },
            )
        ],
    )
    await _setup(hass, _schedule_entry([_window("21:00", "07:00")]))
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T21:00:00+00:00"


@pytest.mark.asyncio
async def test_inverted_overlapping_windows_begin_after_merged_interval(
    hass: HomeAssistant, freezer
) -> None:
    """An inner window end is not the start of an inverted effective window."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 21:00:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Inverted Overlap",
            CONF_TIME_WINDOWS: [
                _window("18:00", "23:00"),
                _window("20:00", "02:00"),
            ],
            CONF_SCHEDULE_INVERT: True,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get("binary_sensor.inverted_overlap")
    assert state.state == "off"
    assert state.attributes["next_transition"] == "2026-07-03T02:00:00+00:00"

    # The first raw window ends, but the overlapping overnight window remains.
    t = datetime(2026, 7, 2, 23, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.inverted_overlap")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None
    assert state.attributes["next_transition"] == "2026-07-03T02:00:00+00:00"

    # Only the end of the merged raw interval starts the inverted on-period.
    t = datetime(2026, 7, 3, 2, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.inverted_overlap")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-03T02:00:00+00:00"


@pytest.mark.asyncio
async def test_inverted_unresolvable_sun_window_uses_stable_marker(
    hass: HomeAssistant, freezer
) -> None:
    """A continuously-on inverted polar schedule has a stable Follow marker."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Inverted Polar",
            CONF_TIME_WINDOWS: [
                {"start": {"sun": "sunset"}, "end": {"sun": "sunrise"}}
            ],
            CONF_SCHEDULE_INVERT: True,
        },
    )
    with patch(
        "custom_components.molight.binary_sensor.get_astral_event_date",
        return_value=None,
    ):
        await _setup(hass, entry)
        state = hass.states.get("binary_sensor.inverted_polar")
        assert state.state == "on"
        assert state.attributes["current_window_start"] == "inverted"
        assert state.attributes["next_transition"] is None

        # The daily polar re-check must not manufacture a new effective window.
        t = datetime(2026, 7, 3, 0, 0, 2, tzinfo=UTC)
        freezer.move_to(t)
        async_fire_time_changed(hass, t)
        await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.inverted_polar")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "inverted"
    assert state.attributes["next_transition"] is None


@pytest.mark.asyncio
async def test_no_windows_stays_off(hass: HomeAssistant, freezer) -> None:
    """With no windows the sensor is off and the daily re-check keeps working."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(hass, _schedule_entry([]))

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["next_transition"] is None

    # No boundaries in sight: the sensor re-evaluates tomorrow without error.
    freezer.tick(timedelta(days=1, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.night_schedule").state == "off"


@pytest.mark.asyncio
async def test_invalid_window_edges_never_activate(
    hass: HomeAssistant, freezer
) -> None:
    """Unresolvable edges (bad times, wrong types, missing end) yield no window."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(
        hass,
        _schedule_entry(
            [
                _window("25:99", "23:00"),  # unparsable start time
                {"start": 42, "end": {"time": "23:00"}},  # wrong edge type
                {"start": {"time": "19:00"}},  # missing end
            ]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None
    assert state.attributes["next_transition"] is None


@pytest.mark.asyncio
async def test_inverted_time_window_uses_effective_on_period(
    hass: HomeAssistant, freezer
) -> None:
    """Inversion turns the complement into the schedule's effective window."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Night Schedule",
            CONF_TIME_WINDOWS: [_window("21:00", "07:00")],
            CONF_SCHEDULE_INVERT: True,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T07:00:00+00:00"

    t = datetime(2026, 7, 2, 21, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None
    assert state.attributes["next_transition"] == "2026-07-03T07:00:00+00:00"

    # The configured window ends, beginning the next effective inverted window.
    t = datetime(2026, 7, 3, 7, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-03T07:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-03T21:00:00+00:00"


@pytest.mark.asyncio
async def test_source_schedule_mirrors_valid_states_and_holds_across_outage(
    hass: HomeAssistant,
) -> None:
    """A source outage is unavailable, not an effective off transition."""
    hass.states.async_set("binary_sensor.house_mode", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "House Mode",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: "binary_sensor.house_mode",
        },
    )
    await _setup(hass, entry)
    state = hass.states.get("binary_sensor.house_mode_2")
    assert state.state == "off"
    assert state.attributes["source_entity"] == "binary_sensor.house_mode"
    assert state.attributes["inverted"] is False

    hass.states.async_set("binary_sensor.house_mode", "on")
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.house_mode_2")
    assert state.state == "on"
    marker = state.attributes["current_window_start"]
    assert marker is not None
    assert state.attributes["next_transition"] is None

    hass.states.async_set("binary_sensor.house_mode", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.house_mode_2").state == "unavailable"

    hass.states.async_set("binary_sensor.house_mode", "on")
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.house_mode_2")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker

    hass.states.async_set("binary_sensor.house_mode", "off")
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.house_mode_2")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None


@pytest.mark.asyncio
async def test_inverted_source_schedule(hass: HomeAssistant) -> None:
    """Source inversion applies only to valid on/off values."""
    hass.states.async_set("binary_sensor.away", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Home",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: "binary_sensor.away",
            CONF_SCHEDULE_INVERT: True,
        },
    )
    await _setup(hass, entry)
    state = hass.states.get("binary_sensor.home")
    assert state.state == "on"
    assert state.attributes["source_entity"] == "binary_sensor.away"
    assert state.attributes["inverted"] is True

    hass.states.async_set("binary_sensor.away", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.home").state == "off"

    hass.states.async_set("binary_sensor.away", "unknown")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.home").state == "unavailable"


def _house_mode_schedule() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "House Mode Schedule",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: "binary_sensor.house_mode",
        },
    )


@pytest.mark.asyncio
async def test_source_schedule_restores_marker_from_a_save_without_extra_data(
    hass: HomeAssistant,
) -> None:
    """A state saved by an earlier version has its marker in the attributes.

    Such a state was saved while the schedule was on: an unavailable state
    never carried attributes.
    """
    source = "binary_sensor.house_mode"
    entity_id = "binary_sensor.house_mode_schedule"
    marker = "2026-07-02T21:00:00+00:00"
    mock_restore_cache(
        hass,
        [
            State(
                entity_id,
                "on",
                {
                    "current_window_start": marker,
                    "source_entity": source,
                    "inverted": False,
                },
            )
        ],
    )
    hass.states.async_set(source, "unavailable")
    await _setup(hass, _house_mode_schedule())

    state = hass.states.get(entity_id)
    assert state.state == "unavailable"

    hass.states.async_set(source, "on")
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
async def test_source_schedule_with_a_new_source_drops_the_saved_marker(
    hass: HomeAssistant, freezer
) -> None:
    """Choosing another source during an outage starts a new window on recovery."""
    entity_id = "binary_sensor.house_mode_schedule"
    freezer.move_to("2026-07-02 07:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "on")
    hass.states.async_set("binary_sensor.new_mode", "unavailable")
    entry = _house_mode_schedule()
    await _setup(hass, entry)
    hass.states.async_set("binary_sensor.house_mode", "unavailable")
    await hass.async_block_till_done()

    # Saving new options reloads the entry.
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_NAME: "House Mode Schedule",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: "binary_sensor.new_mode",
        },
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"

    freezer.move_to("2026-07-02 09:30:00+00:00")
    hass.states.async_set("binary_sensor.new_mode", "on")
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["source_entity"] == "binary_sensor.new_mode"
    assert state.attributes["current_window_start"] == "2026-07-02T09:30:00+00:00"


@pytest.mark.asyncio
async def test_source_schedule_saves_marker_while_unavailable(
    hass: HomeAssistant, freezer
) -> None:
    """The saved state has no attributes during an outage; the extra data does."""
    freezer.move_to("2026-07-02 07:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "on")
    await _setup(hass, _house_mode_schedule())
    hass.states.async_set("binary_sensor.house_mode", "unavailable")
    await hass.async_block_till_done()

    stored = {
        s.state.entity_id: s
        for s in restore_state.async_get(hass).async_get_stored_states()
    }["binary_sensor.house_mode_schedule"]
    assert stored.state.state == "unavailable"
    assert "current_window_start" not in stored.state.attributes
    assert stored.extra_data.as_dict() == {
        "is_on": True,
        "current_window_start": "2026-07-02T07:00:00+00:00",
        "source_entity": "binary_sensor.house_mode",
        "invert": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["reload", "restart"])
@pytest.mark.parametrize("back", ["on", "off"])
async def test_source_schedule_keeps_marker_through_outage_and_restart(
    hass: HomeAssistant, freezer, how: str, back: str
) -> None:
    """A reload or restart during a source outage does not start a new window."""
    entity_id = "binary_sensor.house_mode_schedule"
    freezer.move_to("2026-07-02 07:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "on")
    entry = _house_mode_schedule()
    await _setup(hass, entry)
    marker = hass.states.get(entity_id).attributes["current_window_start"]
    assert marker == "2026-07-02T07:00:00+00:00"

    freezer.move_to("2026-07-02 09:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "unavailable")
    await hass.async_block_till_done()
    if how == "reload":
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    else:
        await restart_entries(hass, entry)
    assert hass.states.get(entity_id).state == "unavailable"

    freezer.move_to("2026-07-02 09:30:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", back)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == back
    assert state.attributes["current_window_start"] == (
        marker if back == "on" else None
    )


@pytest.mark.asyncio
async def test_source_schedule_invert_edit_during_outage_starts_a_new_window(
    hass: HomeAssistant, freezer
) -> None:
    """A marker saved for the plain window is not reused by the inverted one."""
    entity_id = "binary_sensor.house_mode_schedule"
    freezer.move_to("2026-07-02 07:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "on")
    entry = _house_mode_schedule()
    await _setup(hass, entry)
    marker = hass.states.get(entity_id).attributes["current_window_start"]
    assert marker == "2026-07-02T07:00:00+00:00"

    freezer.move_to("2026-07-02 09:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "unavailable")
    await hass.async_block_till_done()
    options = {k: v for k, v in entry.data.items() if k != CONF_ENTITY_TYPE}
    hass.config_entries.async_update_entry(
        entry, options={**options, CONF_SCHEDULE_INVERT: True}
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"

    freezer.move_to("2026-07-02 09:30:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "off")
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["inverted"] is True
    assert state.attributes["current_window_start"] == "2026-07-02T09:30:00+00:00"


@pytest.mark.asyncio
async def test_source_schedule_saved_off_starts_a_window_after_the_outage(
    hass: HomeAssistant, freezer
) -> None:
    """An outage that began outside the window leaves no marker to restore."""
    entity_id = "binary_sensor.house_mode_schedule"
    freezer.move_to("2026-07-02 07:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "off")
    entry = _house_mode_schedule()
    await _setup(hass, entry)
    hass.states.async_set("binary_sensor.house_mode", "unavailable")
    await hass.async_block_till_done()
    await restart_entries(hass, entry)

    freezer.move_to("2026-07-02 09:30:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "on")
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T09:30:00+00:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["reload", "restart"])
async def test_follow_light_manual_off_survives_schedule_outage_and_restart(
    hass: HomeAssistant, freezer, how: str
) -> None:
    """A light turned off by hand mid-window stays off when the source returns."""
    freezer.move_to("2026-07-02 07:00:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "on")
    hass.states.async_set("light.real_1", "off")
    schedule = _house_mode_schedule()
    light = make_light_entry(
        name="Desk Lamp",
        schedule="binary_sensor.house_mode_schedule",
        schedule_mode=SCHEDULE_MODE_FOLLOW,
    )
    await setup_entries(hass, schedule, light)
    assert hass.states.get("light.desk_lamp").state == "on"

    freezer.move_to("2026-07-02 09:00:00+00:00")
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.desk_lamp"}, blocking=True
    )
    hass.states.async_set("binary_sensor.house_mode", "unavailable")
    await hass.async_block_till_done()
    if how == "reload":
        assert await hass.config_entries.async_reload(schedule.entry_id)
        await hass.async_block_till_done()
    else:
        await restart_entries(hass, schedule, light)
    assert hass.states.get("light.desk_lamp").state == "off"

    freezer.move_to("2026-07-02 09:30:00+00:00")
    hass.states.async_set("binary_sensor.house_mode", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.house_mode_schedule").state == "on"
    assert hass.states.get("light.desk_lamp").state == "off"


@pytest.mark.asyncio
async def test_source_schedule_restores_marker_when_source_is_on(
    hass: HomeAssistant,
) -> None:
    """An active source at startup keeps the restored effective-window marker."""
    source = "binary_sensor.house_mode"
    entity_id = "binary_sensor.house_mode_schedule"
    marker = "2026-07-02T21:00:00+00:00"
    mock_restore_cache(
        hass,
        [
            State(
                entity_id,
                "on",
                {
                    "current_window_start": marker,
                    "source_entity": source,
                    "inverted": False,
                },
            )
        ],
    )
    hass.states.async_set(source, "on")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "House Mode Schedule",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: source,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
async def test_source_schedule_does_not_restore_marker_from_previous_source(
    hass: HomeAssistant,
) -> None:
    """Changing the source starts a new effective window instead of reusing one."""
    old_source = "binary_sensor.old_mode"
    source = "binary_sensor.new_mode"
    entity_id = "binary_sensor.house_mode_schedule"
    old_marker = "2026-07-02T21:00:00+00:00"
    mock_restore_cache(
        hass,
        [
            State(
                entity_id,
                "on",
                {
                    "current_window_start": old_marker,
                    "source_entity": old_source,
                    "inverted": False,
                },
            )
        ],
    )
    hass.states.async_set(source, "on")
    source_started = hass.states.get(source).last_changed.isoformat()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "House Mode Schedule",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: source,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["current_window_start"] == source_started
    assert state.attributes["current_window_start"] != old_marker


@pytest.mark.asyncio
async def test_sun_edge_ignores_unparsable_offset(hass: HomeAssistant, freezer) -> None:
    """A sun edge with a non-numeric offset resolves as if it had none."""
    await set_home(hass, *LONDON)
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(
        hass,
        _schedule_entry(
            [{"start": {"sun": "sunset", "offset": "soon"}, "end": {"time": "23:00"}}]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state in ("on", "off")
    # The window still resolved: a boundary is scheduled.
    assert state.attributes["next_transition"] is not None


@pytest.mark.asyncio
async def test_spring_forward_gap_edge_does_not_shadow_real_next_edge(
    hass: HomeAssistant, freezer
) -> None:
    """Edges are ordered by real instant, not wall clock, across a DST gap.

    On 2026-03-08 in America/New_York, 02:30 does not exist; resolved with
    fold=0 it lands on EST (07:30 UTC), a *later* real instant than
    03:05 EDT (07:05 UTC) despite the earlier wall time. The 03:05 window's
    start must not be shadowed by the gap edge.
    """
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to("2026-03-08 06:50:00+00:00")  # 01:50 EST, before both edges
    await _setup(
        hass,
        _schedule_entry(
            [
                _window("03:05", "06:00"),
                _window("02:30", "02:45"),
            ]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    # 02:30 resolves into the gap (07:30 UTC as EST), a later real instant
    # than 03:05 EDT (07:05 UTC). The timer must be armed for 03:05.
    assert state.attributes["next_transition"] == "2026-03-08T03:05:00-04:00"

    freezer.move_to("2026-03-08 07:06:00+00:00")  # 03:06 EDT, past the edge
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-03-08T03:05:00-04:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("window", "on_at", "marker", "next_while_on", "off_at", "next_after"),
    [
        (
            _window("01:30", "01:45"),
            "2026-11-01 05:30:02+00:00",
            "2026-11-01T01:30:00-04:00",
            "2026-11-01T01:45:00-04:00",
            "2026-11-01 05:45:02+00:00",
            "2026-11-02T01:30:00-05:00",
        ),
        (
            _window("22:00", "01:30"),
            "2026-11-01 02:00:02+00:00",
            "2026-10-31T22:00:00-04:00",
            "2026-11-01T01:30:00-04:00",
            "2026-11-01 05:30:02+00:00",
            "2026-11-01T22:00:00-05:00",
        ),
    ],
    ids=["window inside the repeated hour", "overnight window ending in it"],
)
async def test_fall_back_edges_fire_once_at_their_first_occurrence(
    hass: HomeAssistant,
    freezer,
    window: dict,
    on_at: str,
    marker: str,
    next_while_on: str,
    off_at: str,
    next_after: str,
) -> None:
    """On 2026-11-01 in New York, 01:00 to 02:00 happens twice. An edge in
    that hour fires at its daylight-time occurrence, and the repeated hour
    neither ends the window early nor starts it a second time."""
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to("2026-11-01 01:00:00+00:00")  # 21:00 EDT the evening before
    await _setup(hass, _schedule_entry([window]))
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["next_transition"] == marker

    freezer.move_to(on_at)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker
    assert state.attributes["next_transition"] == next_while_on

    freezer.move_to(off_at)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["next_transition"] == next_after

    # The same wall times again, now in standard time.
    for when in (
        "2026-11-01 06:30:02+00:00",
        "2026-11-01 06:45:02+00:00",
        "2026-11-01 07:00:02+00:00",
    ):
        freezer.move_to(when)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        state = hass.states.get("binary_sensor.night_schedule")
        assert state.state == "off"
        assert state.attributes["next_transition"] == next_after


@pytest.mark.asyncio
async def test_window_collapsed_by_the_spring_forward_gap_is_skipped(
    hass: HomeAssistant, freezer
) -> None:
    """A start in the gap lands an hour later on the clock. When that puts it
    after the end, there is no window that day rather than one ending before
    it began."""
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to("2026-03-08 12:00:00+00:00")
    window = _window("02:30", "03:15")
    assert _resolve_window(hass, window, date(2026, 3, 8)) is None

    start, end = _resolve_window(hass, _window("02:30", "03:45"), date(2026, 3, 8))
    assert start.isoformat() == "2026-03-08T02:30:00-05:00"  # 03:30 EDT
    assert end.isoformat() == "2026-03-08T03:45:00-04:00"

    start, end = _resolve_window(hass, window, date(2026, 3, 9))
    assert end.timestamp() - start.timestamp() == 45 * 60


@pytest.mark.asyncio
async def test_inverted_schedule_keeps_its_marker_over_a_skipped_window(
    hass: HomeAssistant, freezer
) -> None:
    """The gap-collapsed window is no boundary: the inverted on-period runs on."""
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to("2026-03-07 17:00:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Inverted Gap",
            CONF_TIME_WINDOWS: [_window("02:30", "03:15")],
            CONF_SCHEDULE_INVERT: True,
        },
    )
    await _setup(hass, entry)
    state = hass.states.get("binary_sensor.inverted_gap")
    assert state.state == "on"
    marker = state.attributes["current_window_start"]
    assert marker == "2026-03-07T03:15:00-05:00"

    for when in (
        "2026-03-08 07:15:02+00:00",
        "2026-03-08 07:30:02+00:00",
        "2026-03-08 12:00:00+00:00",
    ):
        freezer.move_to(when)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        state = hass.states.get("binary_sensor.inverted_gap")
        assert state.state == "on"
        assert state.attributes["current_window_start"] == marker
    assert state.attributes["next_transition"] == "2026-03-09T02:30:00-04:00"


@pytest.mark.asyncio
async def test_window_pushed_past_the_next_midnight_is_found_by_a_fresh_evaluation(
    hass: HomeAssistant, freezer
) -> None:
    """Large sun offsets can start a window the day after its sun event and
    end it the day after that. A sensor set up inside it (as after a restart)
    must still find it."""
    await hass.config.async_set_time_zone("UTC")
    hass.config.latitude = 45.0
    hass.config.longitude = 0.0
    window = {
        "start": {"sun": "sunset", "offset": 720},
        "end": {"sun": "sunset", "offset": 660},
    }
    freezer.move_to("2026-07-01 12:00:00+00:00")
    start, end = _resolve_window(hass, window, date(2026, 7, 1))
    assert start.date() == date(2026, 7, 2)
    assert end.date() == date(2026, 7, 3)

    freezer.move_to(end - timedelta(hours=3))
    await _setup(hass, _schedule_entry([window]))
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == start.isoformat()
    assert state.attributes["next_transition"] == end.isoformat()


@pytest.mark.asyncio
async def test_source_schedule_discards_malformed_restored_marker(
    hass: HomeAssistant,
) -> None:
    """A stored window marker that no longer parses is dropped, not a crash.

    The live source then supplies a fresh marker from its own last change.
    """
    source = "binary_sensor.house_mode"
    entity_id = "binary_sensor.house_mode_schedule"
    mock_restore_cache(
        hass,
        [
            State(
                entity_id,
                "on",
                {
                    "current_window_start": "not-a-timestamp",
                    "source_entity": source,
                    "inverted": False,
                },
            )
        ],
    )
    hass.states.async_set(source, "on")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "House Mode Schedule",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: source,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get(entity_id)
    assert state.state == "on"
    marker = hass.states.get(source).last_changed.isoformat()
    assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
async def test_opposite_sun_offsets_resolve_an_overnight_window(
    hass: HomeAssistant,
) -> None:
    """A late start offset and an early end offset put the end two nominal
    days behind the start. The end is the first one after the start."""
    await hass.config.async_set_time_zone("UTC")

    def sun_event(_hass, event, day):
        hour = 18 if event == "sunset" else 6
        return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)

    window = {
        "start": {"sun": "sunset", "offset": 720},
        "end": {"sun": "sunrise", "offset": -720},
    }
    with patch(
        "custom_components.molight.binary_sensor.get_astral_event_date",
        side_effect=sun_event,
    ):
        assert _resolve_window(hass, window, date(2026, 7, 1)) == (
            datetime(2026, 7, 2, 6, tzinfo=UTC),
            datetime(2026, 7, 2, 18, tzinfo=UTC),
        )


# ---------------------------------------------------------------------------
# Overnight or not: judged from the configured edges
# ---------------------------------------------------------------------------

_SUNSET = {"sun": "sunset"}
_SUNRISE = {"sun": "sunrise"}


def _at(value: str) -> dict:
    return {"time": value}


def _sun(event: str, offset: int) -> dict:
    return {"sun": event, "offset": offset}


def _both(at: str, event: str, offset: int, combine: str) -> dict:
    return {"time": at, "sun": event, "offset": offset, "combine": combine}


@pytest.mark.parametrize(
    ("start", "end", "days"),
    [
        # Two fixed times: overnight when the end is not after the start.
        (_at("06:00"), _at("22:00"), 0),
        (_at("22:00"), _at("06:00"), 1),
        (_at("00:00"), _at("00:00"), 1),
        # One sun event twice, told apart by their offsets.
        (_sun("sunset", -30), _sun("sunset", 30), 0),
        (_sun("sunset", 30), _sun("sunset", -30), 1),
        (_SUNSET, _SUNSET, 1),
        (_sun("sunset", 720), _sun("sunset", -720), 2),
        # Sunrise is a morning edge and sunset an evening one.
        (_SUNSET, _SUNRISE, 1),
        (_SUNRISE, _SUNSET, 0),
        # A sun event with a fixed time in its own half of the day: same day.
        (_SUNSET, _at("21:00"), 0),
        (_SUNSET, _at("23:00"), 0),
        (_at("16:00"), _SUNSET, 0),
        (_at("12:00"), _SUNSET, 0),
        (_SUNRISE, _at("09:00"), 0),
        (_SUNRISE, _at("12:00"), 0),
        (_at("06:00"), _SUNRISE, 0),
        (_at("00:00"), _SUNRISE, 0),
        # An evening edge to a morning edge is overnight.
        (_SUNSET, _at("02:00"), 1),
        (_SUNSET, _at("07:00"), 1),
        (_SUNSET, _at("12:00"), 1),
        (_at("22:00"), _SUNRISE, 1),
        (_at("12:00"), _SUNRISE, 1),
        # A morning edge to an evening edge is not; an end at 00:00 is the
        # end of the day.
        (_SUNRISE, _at("22:00"), 0),
        (_at("05:00"), _SUNSET, 0),
        (_SUNRISE, _at("00:00"), 1),
        (_SUNSET, _at("00:00"), 1),
        # A combined edge sits where its time-or-sun pick puts it.
        (
            {"time": "21:00", "sun": "sunset", "offset": -15, "combine": "latest"},
            _at("07:00"),
            1,
        ),
        (
            _at("22:00"),
            {"time": "07:00", "sun": "sunrise", "combine": "earliest"},
            1,
        ),
        (
            {"time": "23:00", "sun": "sunset", "combine": "earliest"},
            _at("23:30"),
            0,
        ),
        # A fixed time past midnight paired with an evening sun event.
        (_SUNSET, _both("00:00", "sunset", 240, "latest"), 0),
        (_SUNSET, _both("01:00", "sunset", 300, "earliest"), 0),
        (_both("00:30", "sunset", 60, "latest"), _at("07:00"), 1),
        (_at("20:00"), _both("23:30", "sunrise", -300, "earliest"), 1),
        # Offsets move a sun edge into another half-day, or another day.
        (_sun("sunset", 480), _at("07:00"), 1),
        (_sun("sunset", 720), _sun("sunrise", -720), 2),
        (_sun("sunrise", -720), _at("23:00"), -1),
        # An edge with nothing valid in it gives no window.
        (_SUNSET, {}, None),
        (_at("25:99"), _at("07:00"), None),
        (None, _at("07:00"), None),
    ],
)
def test_overnight_is_judged_from_the_configured_edges(
    start: dict | None, end: dict | None, days: int | None
) -> None:
    """How many days after the start's day the end falls, by settings alone."""
    assert _end_days_after(start, end) == days


# Each row: home, window, a local time, the state then.
_CROSSING_CASES = [
    # Sunset (21:02 in June) is past the fixed end: no window that day.
    (TORONTO, (_SUNSET, _at("21:00")), "2026-06-22 12:00", "off"),
    (TORONTO, (_SUNSET, _at("21:00")), "2026-06-22 21:05", "off"),
    (TORONTO, (_SUNSET, _at("21:00")), "2026-03-20 20:00", "on"),
    # EXAMPLES Example 11 where sunset is after 23:00.
    (ANCHORAGE, (_SUNSET, _at("23:00")), "2026-06-22 12:00", "off"),
    (ANCHORAGE, (_SUNSET, _at("23:00")), "2026-06-22 23:50", "off"),
    (ANCHORAGE, (_SUNSET, _at("23:00")), "2026-09-22 21:00", "on"),
    # A wake-up light: sunrise (04:43 in June) is before the fixed start.
    (LONDON, (_at("06:00"), _SUNRISE), "2026-06-22 12:00", "off"),
    (LONDON, (_at("06:00"), _SUNRISE), "2026-06-22 06:30", "off"),
    (LONDON, (_at("06:00"), _SUNRISE), "2026-12-15 07:00", "on"),
    # A fixed start the winter sunset (15:52) comes before.
    (LONDON, (_at("16:00"), _SUNSET), "2026-12-15 17:00", "off"),
    (LONDON, (_at("16:00"), _SUNSET), "2026-06-22 17:00", "on"),
    # A fixed end the late winter sunrise (10:03) comes after.
    (TROMSO, (_SUNRISE, _at("09:00")), "2026-01-25 12:00", "off"),
    (LONDON, (_SUNRISE, _at("09:00")), "2026-06-22 08:00", "on"),
    # Overnight windows stay overnight in summer.
    (TORONTO, (_SUNSET, _at("02:00")), "2026-06-23 01:00", "on"),
    (TORONTO, (_SUNSET, _at("02:00")), "2026-06-22 12:00", "off"),
    (TORONTO, (_at("22:00"), _SUNRISE), "2026-06-23 03:00", "on"),
    (TORONTO, (_at("22:00"), _SUNRISE), "2026-06-23 12:00", "off"),
    (TORONTO, (_SUNSET, _SUNRISE), "2026-06-23 03:00", "on"),
    (TORONTO, (_SUNSET, _SUNRISE), "2026-06-23 12:00", "off"),
    # An end at 00:00 is the end of the day.
    (TORONTO, (_SUNRISE, _at("00:00")), "2026-06-22 23:30", "on"),
    (TORONTO, (_SUNRISE, _at("00:00")), "2026-06-23 00:30", "off"),
    (TORONTO, (_SUNSET, _at("00:00")), "2026-06-22 23:30", "on"),
    (TORONTO, (_SUNSET, _at("00:00")), "2026-06-23 00:30", "off"),
]


def _local(tz, when: str) -> datetime:
    return datetime.fromisoformat(when).replace(tzinfo=tz)


@pytest.mark.asyncio
@pytest.mark.parametrize(("home", "edges", "when", "expected"), _CROSSING_CASES)
@pytest.mark.parametrize("invert", [False, True], ids=["plain", "inverted"])
async def test_sun_edge_past_its_fixed_edge_gives_no_window(
    hass: HomeAssistant,
    freezer,
    home: tuple,
    edges: tuple,
    when: str,
    expected: str,
    invert: bool,
) -> None:
    """A sun event that has drifted past the window's fixed edge leaves the
    window empty that day, never running round the clock."""
    tz = await set_home(hass, *home)
    freezer.move_to(_local(tz, when))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Night Schedule",
            CONF_TIME_WINDOWS: [{"start": edges[0], "end": edges[1]}],
            CONF_SCHEDULE_INVERT: invert,
        },
    )
    await _setup(hass, entry)
    state = hass.states.get("binary_sensor.night_schedule")
    assert (state.state == expected) is not invert


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("home", "window", "longest", "empty_months"),
    [
        (TORONTO, (_SUNSET, _at("21:00")), 5, {6, 7}),
        (ANCHORAGE, (_SUNSET, _at("23:00")), 8, {5, 6, 7}),
        (LONDON, (_at("06:00"), _SUNRISE), 3, {3, 4, 5, 6, 7, 8, 9}),
        (LONDON, (_at("16:00"), _SUNSET), 6, {11, 12, 1}),
    ],
)
async def test_sun_and_fixed_window_is_short_or_empty_all_year(
    hass: HomeAssistant, home: tuple, window: tuple, longest: int, empty_months: set
) -> None:
    """Across the year the window shrinks to nothing and comes back; it never
    flips into a day-long one while the sun event is past the fixed edge."""
    await set_home(hass, *home)
    spec = {"start": window[0], "end": window[1]}
    empty = set()
    for offset in range(365):
        day = date(2026, 1, 1) + timedelta(days=offset)
        resolved = _resolve_window(hass, spec, day)
        if resolved is None:
            empty.add(day)
        else:
            assert resolved[1] - resolved[0] < timedelta(hours=longest), day
    assert empty
    assert {day.month for day in empty} <= empty_months


@pytest.mark.asyncio
async def test_window_returns_once_the_sun_event_is_back_before_the_fixed_edge(
    hass: HomeAssistant, freezer
) -> None:
    """With no boundary in sight the daily re-check finds the window again."""
    tz = await set_home(hass, *TORONTO)
    freezer.move_to(_local(tz, "2026-06-22 12:00"))
    await _setup(hass, _schedule_entry([{"start": _SUNSET, "end": _at("21:00")}]))
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["next_transition"] is None

    day = date(2026, 6, 22)
    while True:
        sunset = get_astral_event_date(hass, "sunset", day)
        if sunset < _local(tz, f"{day} 20:58"):
            break
        t = _local(tz, f"{day} 21:30")
        freezer.move_to(t)
        async_fire_time_changed(hass, t)
        await hass.async_block_till_done()
        assert hass.states.get("binary_sensor.night_schedule").state == "off", day
        day += timedelta(days=1)
        t = _local(tz, f"{day} 00:00:02")
        freezer.move_to(t)
        async_fire_time_changed(hass, t)
        await hass.async_block_till_done()
    assert date(2026, 7, 1) < day < date(2026, 7, 20)

    t = sunset + timedelta(seconds=2)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == sunset.isoformat()
    assert state.attributes["next_transition"] == f"{day}T21:00:00-04:00"


@pytest.mark.asyncio
async def test_follow_light_is_not_lit_all_day_by_a_sunset_past_the_fixed_end(
    hass: HomeAssistant, freezer
) -> None:
    """A follow light on sunset -> 21:00 stays off on a day with no window."""
    tz = await set_home(hass, *TORONTO)
    freezer.move_to(_local(tz, "2026-06-22 12:00"))
    hass.states.async_set("light.real_1", "off")
    light = make_light_entry(
        name="Desk Lamp",
        schedule="binary_sensor.night_schedule",
        schedule_mode=SCHEDULE_MODE_FOLLOW,
    )
    schedule = _schedule_entry([{"start": _SUNSET, "end": _at("21:00")}])
    await setup_entries(hass, schedule, light)
    assert hass.states.get("light.desk_lamp").state == "off"

    for when in ("2026-06-22 21:03", "2026-06-23 00:00:02", "2026-06-23 12:00"):
        t = _local(tz, when)
        freezer.move_to(t)
        async_fire_time_changed(hass, t)
        await settle(hass)
        assert hass.states.get("light.desk_lamp").state == "off", when


@pytest.mark.asyncio
async def test_gate_light_stays_gated_by_a_sunset_past_the_fixed_end(
    hass: HomeAssistant, freezer
) -> None:
    """A gate light on sunset -> 21:00 ignores occupancy on a day with no window."""
    tz = await set_home(hass, *TORONTO)
    freezer.move_to(_local(tz, "2026-06-22 12:00"))
    hass.states.async_set("light.real_1", "off")
    hass.states.async_set("binary_sensor.room_occupancy", "off")
    light = make_light_entry(
        name="Desk Lamp",
        occupancy="binary_sensor.room_occupancy",
        schedule="binary_sensor.night_schedule",
        schedule_mode=SCHEDULE_MODE_GATE,
    )
    schedule = _schedule_entry([{"start": _SUNSET, "end": _at("21:00")}])
    await setup_entries(hass, schedule, light)

    hass.states.async_set("binary_sensor.room_occupancy", "on")
    await settle(hass)
    state = hass.states.get("light.desk_lamp")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


# ---------------------------------------------------------------------------
# A combined edge whose fixed time and sun event are either side of midnight
# ---------------------------------------------------------------------------


# London: sunset is about 15:55 on 12-01 and 21:21 on 06-22; sunrise 07:44
# and 04:43. Each row: window, a local time, the state then.
_TO_MIDNIGHT = (_SUNSET, _both("00:00", "sunset", 240, "latest"))
_TO_ONE = (_SUNSET, _both("01:00", "sunset", 300, "earliest"))
_FROM_HALF_PAST = (_both("00:30", "sunset", 60, "latest"), _at("07:00"))
_TO_LATE_EVENING = (_at("20:00"), _both("23:30", "sunrise", -300, "earliest"))
_PORCH = (_both("21:00", "sunset", -15, "latest"), _at("07:00"))
_TO_DAWN = (_at("22:00"), _both("07:00", "sunrise", 0, "earliest"))
_MIDNIGHT_CASES = [
    # End at the later of sunset + 4 h and 00:00: the midnight that follows.
    (_TO_MIDNIGHT, "2026-12-01 22:00", "on"),
    (_TO_MIDNIGHT, "2026-12-02 00:10", "off"),
    (_TO_MIDNIGHT, "2026-06-23 01:00", "on"),
    (_TO_MIDNIGHT, "2026-06-23 01:30", "off"),
    # End at the earlier of sunset + 5 h and 01:00.
    (_TO_ONE, "2026-12-01 20:30", "on"),
    (_TO_ONE, "2026-12-01 21:30", "off"),
    (_TO_ONE, "2026-06-23 00:30", "on"),
    (_TO_ONE, "2026-06-23 01:10", "off"),
    # Start at the later of sunset + 1 h and 00:30.
    (_FROM_HALF_PAST, "2026-12-01 23:00", "off"),
    (_FROM_HALF_PAST, "2026-12-02 01:00", "on"),
    (_FROM_HALF_PAST, "2026-12-02 07:30", "off"),
    # End at the earlier of 23:30 and 5 h before sunrise: the evening before.
    (_TO_LATE_EVENING, "2026-12-01 23:00", "on"),
    (_TO_LATE_EVENING, "2026-12-01 23:40", "off"),
    # Both on the same side of midnight: as before.
    (_PORCH, "2026-06-22 21:03", "off"),
    (_PORCH, "2026-06-22 21:10", "on"),
    (_PORCH, "2026-12-01 20:00", "off"),
    (_PORCH, "2026-12-01 21:01", "on"),
    (_TO_DAWN, "2026-06-23 04:30", "on"),
    (_TO_DAWN, "2026-06-23 05:00", "off"),
    (_TO_DAWN, "2026-12-02 06:59", "on"),
    (_TO_DAWN, "2026-12-02 07:01", "off"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("edges", "when", "expected"), _MIDNIGHT_CASES)
@pytest.mark.parametrize("invert", [False, True], ids=["plain", "inverted"])
async def test_combined_edge_takes_the_fixed_time_nearest_its_sun_event(
    hass: HomeAssistant, freezer, edges: tuple, when: str, expected: str, invert: bool
) -> None:
    """A fixed time just past midnight, paired with an evening sun event, is
    the one that follows the event, not the one that began its day."""
    tz = await set_home(hass, *LONDON)
    freezer.move_to(_local(tz, when))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Night Schedule",
            CONF_TIME_WINDOWS: [{"start": edges[0], "end": edges[1]}],
            CONF_SCHEDULE_INVERT: invert,
        },
    )
    await _setup(hass, entry)
    state = hass.states.get("binary_sensor.night_schedule")
    assert (state.state == expected) is not invert


@pytest.mark.asyncio
async def test_combined_edge_past_midnight_reports_its_boundaries(
    hass: HomeAssistant, freezer
) -> None:
    """The window ends at the midnight after sunset, and the next one starts
    at the next sunset."""
    tz = await set_home(hass, *LONDON)
    freezer.move_to(_local(tz, "2026-12-01 22:00"))
    await _setup(
        hass,
        _schedule_entry(
            [{"start": _SUNSET, "end": _both("00:00", "sunset", 240, "latest")}]
        ),
    )
    state = hass.states.get("binary_sensor.night_schedule")
    sunset = get_astral_event_date(hass, "sunset", date(2026, 12, 1))
    assert state.attributes["current_window_start"] == sunset.isoformat()
    assert state.attributes["next_transition"] == "2026-12-02T00:00:00+00:00"

    t = _local(tz, "2026-12-02 00:00:02")
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    next_sunset = get_astral_event_date(hass, "sunset", date(2026, 12, 2))
    assert state.attributes["next_transition"] == next_sunset.isoformat()


@pytest.mark.asyncio
async def test_follow_light_stays_on_until_the_midnight_after_sunset(
    hass: HomeAssistant, freezer
) -> None:
    """A follow light keeps the window to midnight, past sunset + 4 h."""
    tz = await set_home(hass, *LONDON)
    freezer.move_to(_local(tz, "2026-12-01 22:00"))
    hass.states.async_set("light.real_1", "off")
    light = make_light_entry(
        name="Desk Lamp",
        schedule="binary_sensor.night_schedule",
        schedule_mode=SCHEDULE_MODE_FOLLOW,
    )
    schedule = _schedule_entry(
        [{"start": _SUNSET, "end": _both("00:00", "sunset", 240, "latest")}]
    )
    await setup_entries(hass, schedule, light)
    assert hass.states.get("light.desk_lamp").state == "on"

    t = _local(tz, "2026-12-02 00:00:02")
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await settle(hass)
    assert hass.states.get("light.desk_lamp").state == "off"


@pytest.mark.asyncio
async def test_polar_day_fixed_time_past_midnight_stands_alone(
    hass: HomeAssistant, freezer
) -> None:
    """With no sunset that day the fixed half still means the midnight after."""
    tz = await set_home(hass, *TROMSO)
    freezer.move_to(_local(tz, "2026-06-21 22:00"))
    await _setup(
        hass,
        _schedule_entry(
            [{"start": _at("20:00"), "end": _both("00:00", "sunset", 240, "latest")}]
        ),
    )
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["next_transition"] == "2026-06-22T00:00:00+02:00"


# ---------------------------------------------------------------------------
# Time zones a day ahead of their longitude (across the date line)
# ---------------------------------------------------------------------------

_DATE_LINE_HOMES = [APIA, KIRITIMATI, TONGATAPU, CHATHAM]
_ORDINARY_HOMES = [AUCKLAND, HONOLULU, TOKYO, TORONTO, LONDON]


@pytest.mark.asyncio
@pytest.mark.parametrize("home", [*_DATE_LINE_HOMES, *_ORDINARY_HOMES])
async def test_sun_events_are_those_of_the_local_day(
    hass: HomeAssistant, home: tuple
) -> None:
    """Sunrise and sunset for a day fall on that day by the home's clock."""
    await set_home(hass, *home)
    for offset in range(0, 365, 7):
        day = date(2026, 1, 1) + timedelta(days=offset)
        for event in ("sunrise", "sunset"):
            at = dt_util.as_local(_sun_event(hass, event, day))
            assert at.date() == day, (event, day)
            assert (at.hour < 12) is (event == "sunrise"), (event, day)


@pytest.mark.asyncio
@pytest.mark.parametrize("home", [*_DATE_LINE_HOMES, AUCKLAND, HONOLULU])
@pytest.mark.parametrize(
    ("edges", "when", "expected"),
    [
        # A fixed time with the same day's sun event, not the next day's.
        ((_at("05:00"), _SUNRISE), "05:30", "on"),
        ((_at("05:00"), _SUNRISE), "15:00", "off"),
        ((_SUNSET, _at("23:00")), "22:00", "on"),
        ((_SUNSET, _at("23:00")), "12:00", "off"),
        ((_SUNRISE, _at("12:00")), "11:00", "on"),
        ((_SUNRISE, _at("12:00")), "04:00", "off"),
        ((_at("13:00"), _SUNSET), "14:00", "on"),
        ((_at("13:00"), _SUNSET), "22:00", "off"),
        # A combined edge compares the two on one day.
        ((_both("05:00", "sunrise", 0, "latest"), _at("11:00")), "05:30", "off"),
        ((_both("05:00", "sunrise", 0, "latest"), _at("11:00")), "09:00", "on"),
        # Sun-only windows were right already.
        ((_SUNSET, _SUNRISE), "02:00", "on"),
        ((_SUNSET, _SUNRISE), "12:00", "off"),
    ],
)
async def test_fixed_time_pairs_with_the_same_days_sun_event_across_the_date_line(
    hass: HomeAssistant, freezer, home: tuple, edges: tuple, when: str, expected: str
) -> None:
    """05:00 -> sunrise in Samoa is the hour or two before sunrise, not a
    window running on to the next day's sunrise."""
    tz = await set_home(hass, *home)
    freezer.move_to(_local(tz, f"2026-06-21 {when}"))
    await _setup(hass, _schedule_entry([{"start": edges[0], "end": edges[1]}]))
    assert hass.states.get("binary_sensor.night_schedule").state == expected


@pytest.mark.asyncio
async def test_date_line_window_reports_the_same_days_boundaries(
    hass: HomeAssistant, freezer
) -> None:
    """In Apia the evening window starts at today's sunset and ends at 23:00."""
    tz = await set_home(hass, *APIA)
    freezer.move_to(_local(tz, "2026-06-21 22:00"))
    await _setup(hass, _schedule_entry([{"start": _SUNSET, "end": _at("23:00")}]))
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    started = dt_util.as_local(
        datetime.fromisoformat(state.attributes["current_window_start"])
    )
    assert started.strftime("%Y-%m-%d %H") == "2026-06-21 18"
    assert state.attributes["next_transition"] == "2026-06-21T23:00:00+13:00"


# ---------------------------------------------------------------------------
# Polar periods (Tromso): transition nights and days with no sun event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("edges", "when", "expected"),
    [
        # No sunrise on 01-20, the first one on 01-21 at 10:29.
        ((_at("20:00"), _SUNRISE), "2026-01-20 22:00", "on"),
        ((_at("20:00"), _SUNRISE), "2026-01-21 10:00", "on"),
        ((_at("20:00"), _SUNRISE), "2026-01-21 11:00", "off"),
        # Polar night: an evening with no sunrise the next day has no window.
        ((_at("20:00"), _SUNRISE), "2026-01-10 22:00", "off"),
        # The first night after the midnight sun: sunset 00:31, sunrise 01:12.
        ((_SUNSET, _SUNRISE), "2026-07-26 00:50", "on"),
        ((_SUNSET, _SUNRISE), "2026-07-26 00:20", "off"),
        ((_SUNSET, _SUNRISE), "2026-07-26 01:30", "off"),
        # The last night before it: sunset 00:19, sunrise 00:57.
        ((_SUNSET, _SUNRISE), "2026-05-18 00:40", "on"),
        ((_SUNSET, _SUNRISE), "2026-05-18 12:00", "off"),
        # Sun-only edges give no window while the sun never sets or rises.
        ((_SUNSET, _SUNRISE), "2026-06-21 00:30", "off"),
        ((_SUNRISE, _SUNSET), "2026-06-21 12:00", "off"),
        ((_SUNSET, _SUNRISE), "2026-12-21 00:30", "off"),
        ((_SUNRISE, _SUNSET), "2026-12-21 12:00", "off"),
        # The first and last short days around the polar night.
        ((_SUNRISE, _SUNSET), "2026-01-21 12:00", "on"),
        ((_SUNRISE, _SUNSET), "2026-11-22 11:30", "on"),
    ],
)
async def test_polar_transition_windows(
    hass: HomeAssistant, freezer, edges: tuple, when: str, expected: str
) -> None:
    """The nights and days next to a polar period are kept; a sun-only edge
    gives no window on a day its event doesn't occur."""
    tz = await set_home(hass, *TROMSO)
    freezer.move_to(_local(tz, when))
    await _setup(hass, _schedule_entry([{"start": edges[0], "end": edges[1]}]))
    assert hass.states.get("binary_sensor.night_schedule").state == expected
