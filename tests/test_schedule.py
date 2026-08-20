"""Tests for the MoLight Virtual Schedule Sensor."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant.helpers.sun import get_astral_event_date
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
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
)

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
    """With several simultaneously active windows the latest start wins as
    current_window_start, and the sensor stays on across the seam where one
    window ends inside another."""
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
    # Both windows are active; the later start identifies the window.
    assert state.attributes["current_window_start"] == "2026-07-02T20:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-02T23:00:00+00:00"

    # The first window ends at 23:00 — still inside the second window.
    t = datetime(2026, 7, 2, 23, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T20:00:00+00:00"

    # The overnight window ends at 02:00 — now everything is over.
    t = datetime(2026, 7, 3, 2, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.night_schedule").state == "off"


@pytest.mark.asyncio
async def test_no_windows_stays_off(hass: HomeAssistant, freezer) -> None:
    """With no windows the sensor is off and the daily re-check keeps working."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(hass, _schedule_entry([]))

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["next_transition"] is None

    # No boundaries in sight — the sensor re-evaluates tomorrow without error.
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
    assert hass.states.get("binary_sensor.night_schedule").state == "off"


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
    assert hass.states.get("binary_sensor.house_mode_2").state == "off"

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
    assert hass.states.get("binary_sensor.home").state == "on"

    hass.states.async_set("binary_sensor.away", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.home").state == "off"

    hass.states.async_set("binary_sensor.away", "unknown")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.home").state == "unavailable"


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
