"""Tests for the MoLight Virtual Schedule Sensor."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant.core import State
from homeassistant.helpers import restore_state
from homeassistant.helpers.sun import get_astral_event_date
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_restore_state_shutdown_restart,
    mock_restore_cache,
)

from custom_components.molight.binary_sensor import _resolve_window
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
)
from tests.conftest import make_light_entry, restart_entries, settle, setup_entries

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


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
    """Legacy fixed-time overnight window flips at the exact boundaries."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(hass, _schedule_entry([{"start": "21:00", "end": "07:00"}]))

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
                        "end": "23:00",
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
                {"start": "18:00", "end": "23:00"},
                {"start": "20:00", "end": "02:00"},
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
    entry = _schedule_entry([{"start": "07:00", "end": "22:00"}])
    await _setup(hass, entry)

    options = {k: v for k, v in entry.data.items() if k != CONF_ENTITY_TYPE}
    options[CONF_TIME_WINDOWS] = [{"start": "07:00", "end": new_end}]
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
    await _setup(hass, _schedule_entry([{"start": "00:00", "end": "00:00"}]))

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
    schedule = _schedule_entry([{"start": "00:00", "end": "00:00"}])
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


_CHAINED = [{"start": "00:00", "end": "23:00"}, {"start": "22:00", "end": "01:00"}]


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
    entry = _schedule_entry([{"start": "21:00", "end": "07:00"}])
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
    await _setup(hass, _schedule_entry([{"start": "21:00", "end": "07:00"}]))
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
                {"start": "18:00", "end": "23:00"},
                {"start": "20:00", "end": "02:00"},
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
                {"start": "25:99", "end": "23:00"},  # unparsable start time
                {"start": 42, "end": "23:00"},  # wrong edge type
                {"start": "19:00"},  # missing end
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
            CONF_TIME_WINDOWS: [{"start": "21:00", "end": "07:00"}],
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
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(
        hass,
        _schedule_entry(
            [{"start": {"sun": "sunset", "offset": "soon"}, "end": "23:00"}]
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
                {"start": "03:05", "end": "06:00"},
                {"start": "02:30", "end": "02:45"},
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
            {"start": "01:30", "end": "01:45"},
            "2026-11-01 05:30:02+00:00",
            "2026-11-01T01:30:00-04:00",
            "2026-11-01T01:45:00-04:00",
            "2026-11-01 05:45:02+00:00",
            "2026-11-02T01:30:00-05:00",
        ),
        (
            {"start": "22:00", "end": "01:30"},
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
    window = {"start": "02:30", "end": "03:15"}
    assert _resolve_window(hass, window, date(2026, 3, 8)) is None

    start, end = _resolve_window(
        hass, {"start": "02:30", "end": "03:45"}, date(2026, 3, 8)
    )
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
            CONF_TIME_WINDOWS: [{"start": "02:30", "end": "03:15"}],
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
