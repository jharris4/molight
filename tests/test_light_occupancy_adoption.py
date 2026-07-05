"""Tests for adopting already-active occupancy into an on light.

Occupancy normally takes the light over on its own rising edge. When it is
already on while the light turns on (manual/physical after a manual off, or
suppressed earlier by bright/window), or when the gate suppressing it lifts
(illuminance turning dark, a gate-mode window opening) while the light is
already on, the light must adopt it as OCCUPIED — otherwise a timer expires
despite presence and the steady-on sensor produces no event that could ever
rescue the light. Adoption is gated exactly like a turn-on (bright or
outside a gate-mode window suppress it); the maintain entity stays ungated.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    ILLUMINANCE_MODE_GATE,
    SCHEDULE_MODE_GATE,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
)
from tests.conftest import make_light_entry, settle, setup_entries

OCC = "binary_sensor.occ"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
REAL = "light.real_1"
VIRTUAL = "light.matrix_light"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


async def _tick(hass: HomeAssistant, freezer, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


@pytest.mark.asyncio
async def test_manual_on_adopts_active_occupancy(
    hass: HomeAssistant, freezer
) -> None:
    """Turning the virtual light on while occupancy is already on → OCCUPIED."""
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Manual off despite occupancy is respected; occupancy stays on.
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    # Turning back on must re-adopt the still-active occupancy: no timer may
    # expire while the room is occupied.
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"

    # Occupancy clearing starts the normal countdown.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_physical_on_adopts_active_occupancy(
    hass: HomeAssistant, freezer
) -> None:
    """A wall-switch turn-on while occupancy is already on → OCCUPIED."""
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    hass.states.async_set(OCC, "on")
    await settle(hass)
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_manual_on_while_bright_does_not_adopt_occupancy(
    hass: HomeAssistant, freezer
) -> None:
    """Adoption is gated like a turn-on: bright keeps the timer running."""
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    hass.states.async_set(ILLUM, "on")  # bright
    hass.states.async_set(OCC, "on")  # suppressed — no turn-on
    await settle(hass)
    assert _state(hass).state == "off"

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_dark_adopts_active_occupancy_into_on_light(
    hass: HomeAssistant, freezer
) -> None:
    """Illuminance turning dark lifts the gate: an on light becomes OCCUPIED."""
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC, illuminance=ILLUM, illuminance_mode=ILLUMINANCE_MODE_GATE
        ),
    )
    hass.states.async_set(ILLUM, "on")  # bright
    hass.states.async_set(OCC, "on")  # suppressed
    await settle(hass)

    # User turns the light on regardless — ACTIVE with a timer (still bright).
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # It gets dark while the room is occupied: adopt, cancel the timer.
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"

    hass.states.async_set(OCC, "off")
    await settle(hass)
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_dark_without_occupancy_keeps_timer(
    hass: HomeAssistant, freezer
) -> None:
    """Dark with no active occupancy leaves a running on-period untouched."""
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    hass.states.async_set(ILLUM, "on")  # bright
    await settle(hass)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.states.async_set(ILLUM, "off")  # dark, occupancy never triggered
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_gate_window_start_adopts_active_occupancy(
    hass: HomeAssistant, freezer
) -> None:
    """A gate-mode window opening over an on light adopts active occupancy."""
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
        ),
    )
    hass.states.async_set(SCHED, "off")  # outside the window
    hass.states.async_set(OCC, "on")  # suppressed
    await settle(hass)
    assert _state(hass).state == "off"

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # Window opens while the room is occupied: adopt, cancel the timer.
    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"

    # Window end still forces the lights off, as it does over occupancy.
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_dark_mid_warning_adopts_occupancy_and_resumes(
    hass: HomeAssistant, freezer
) -> None:
    """Dark arriving mid effect/warn with occupancy on undoes the warning."""
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_GATE,
            effect_timeout=10,
            warn_timeout=20,
        ),
    )
    hass.states.async_set(ILLUM, "on")  # bright
    hass.states.async_set(OCC, "on")  # suppressed
    await settle(hass)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # Timer expiry starts the warning sequence (still bright, still ACTIVE-gated).
    await _tick(hass, freezer, 61)
    assert _state(hass).attributes["molight_state"] == STATE_EFFECT

    # Dark lifts the gate mid-warning: adopt occupancy, restore the lights.
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"
