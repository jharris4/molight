"""Tests for the Limer Virtual Schedule Sensor."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers.sun import get_astral_event_date

from custom_components.limer.const import (
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_TIME_WINDOWS,
    DOMAIN,
    ENTITY_TYPE_SCHEDULE,
)


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
    t = datetime(2026, 7, 2, 21, 0, 2, tzinfo=timezone.utc)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T21:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-03T07:00:00+00:00"

    # Window ends at 07:00 the next morning.
    t = datetime(2026, 7, 3, 7, 0, 2, tzinfo=timezone.utc)
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
