"""Unavailability tests for the Limer Virtual Light.

Each entity the light watches (real lights, occupancy, illuminance,
schedule) can drop to unavailable/unknown at any time — e.g. a Zigbee
device falling off the mesh or an integration reloading. Those blips must
never be read as state changes, and recovery transitions must behave like
fresh events.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.limer.const import (
    SCHEDULE_MODE_FOLLOW,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
)
from tests.conftest import make_light_entry, settle, setup_entries

OCC = "binary_sensor.occ"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
REAL = "light.real_1"
REAL2 = "light.real_2"
VIRTUAL = "light.matrix_light"
MARKER = "2026-07-02T21:00:00+00:00"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["unavailable", "unknown"])
async def test_real_light_blip_and_recovery(hass: HomeAssistant, bad: str) -> None:
    """A real light dropping out is not an 'off'; coming back 'on' re-adopts it."""
    await setup_entries(hass, make_light_entry())

    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, bad)
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["limer_state"] == STATE_ACTIVE

    # Recovery to on is treated like an external turn-on (still ACTIVE).
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["limer_state"] == STATE_ACTIVE

    # A real off afterwards releases the light as usual.
    hass.states.async_set(REAL, "off")
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_real_light_recovers_directly_to_off(hass: HomeAssistant) -> None:
    """unavailable → off counts as a real off and releases the virtual light."""
    await setup_entries(hass, make_light_entry())

    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_occupancy_blip_keeps_occupied(hass: HomeAssistant, freezer) -> None:
    """Occupancy going unavailable must not start the countdown."""
    await setup_entries(hass, make_light_entry(occupancy=OCC))

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["limer_state"] == STATE_OCCUPIED

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    assert _state(hass).attributes["limer_state"] == STATE_OCCUPIED

    # No timer was started by the blip.
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    # Recovery straight to off starts the countdown as a real clear.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["limer_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_occupancy_recovers_directly_to_on(hass: HomeAssistant) -> None:
    """unavailable → on is a real occupancy trigger."""
    hass.states.async_set(OCC, "unavailable")
    await setup_entries(hass, make_light_entry(occupancy=OCC))

    hass.states.async_set(OCC, "on")
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["limer_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_illuminance_blip_holds_state(hass: HomeAssistant) -> None:
    """Illuminance going unavailable changes nothing; recovery acts on the value."""
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["limer_state"] == STATE_OCCUPIED

    hass.states.async_set(ILLUM, "unavailable")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["limer_state"] == STATE_OCCUPIED

    # Recovery straight to bright forces the lights off (control mode).
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_schedule_blip_keeps_window(hass: HomeAssistant) -> None:
    """A follow-mode schedule dropping out mid-window keeps the lights on."""
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await settle(hass)
    assert _state(hass).attributes["limer_state"] == STATE_SCHEDULED

    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["limer_state"] == STATE_SCHEDULED

    # Recovery straight to off applies the window-end boundary.
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_unavailable_real_light_counts_as_off(hass: HomeAssistant) -> None:
    """With one light unavailable, the last real off releases the virtual light.

    Pins current behavior: _all_lights_off treats an unavailable light as off,
    so a dead bulb can't hold the virtual light on forever.
    """
    await setup_entries(hass, make_light_entry(lights=[REAL, REAL2]))

    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(REAL2, "on")
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL2, "unavailable")
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE
