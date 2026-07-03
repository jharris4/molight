"""Tests for the Virtual Light's maintain occupancy entity.

The maintain entity holds an already-on light on while it is on; it never
turns the light on. Unlike the combined sensor's maintain_sensors (which only
extend occupancy started by a trigger sensor), it holds the light regardless
of how it was lit — manual, physical, or occupancy. The watched sensors are
plain states set via hass.states.async_set, as in test_light_matrix.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
)
from tests.conftest import make_light_entry, settle, setup_entries

OCC = "binary_sensor.occ"
MAINT = "binary_sensor.maint"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
HOLD = "input_boolean.hold"
REAL = "light.real_1"
VIRTUAL = "light.matrix_light"
MARKER = "2026-07-02T21:00:00+00:00"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


async def _tick(hass: HomeAssistant, freezer, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


# ---------------------------------------------------------------------------
# Maintain never turns the light on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintain_on_does_not_turn_light_on(hass: HomeAssistant) -> None:
    """Maintain going on while the light is off does nothing."""
    entry = make_light_entry(occupancy=OCC, maintain=MAINT)
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)

    hass.states.async_set(MAINT, "on")
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


# ---------------------------------------------------------------------------
# Maintain holds a manually-lit light
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintain_holds_manual_light(hass: HomeAssistant, freezer) -> None:
    """ACTIVE + maintain on → OCCUPIED with the timer suspended; maintain
    off → COUNTDOWN → lights off."""
    entry = make_light_entry(maintain=MAINT, timeout=60)
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()

    hass.states.async_set(MAINT, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Well past the timeout — the maintain hold suspends the timer.
    await _tick(hass, freezer, 300)
    assert _state(hass).state == "on"

    hass.states.async_set(MAINT, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["virtual", "physical"])
async def test_turn_on_while_maintain_already_on(
    hass: HomeAssistant, freezer, via: str
) -> None:
    """A light turned on while maintain is already occupied is held immediately."""
    entry = make_light_entry(maintain=MAINT, timeout=60)
    hass.states.async_set(MAINT, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, entry)

    if via == "virtual":
        await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
        await hass.async_block_till_done()
    else:
        hass.states.async_set(REAL, "on")
        await settle(hass)

    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 300)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_maintain_rescues_countdown(hass: HomeAssistant, freezer) -> None:
    """Maintain going on during COUNTDOWN returns the light to OCCUPIED."""
    entry = make_light_entry(occupancy=OCC, maintain=MAINT, timeout=60)
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    hass.states.async_set(MAINT, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 300)
    assert _state(hass).state == "on"


# ---------------------------------------------------------------------------
# Interplay with the regular occupancy entity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_occupancy_clear_held_by_maintain(hass: HomeAssistant, freezer) -> None:
    """Occupancy clearing while maintain is on keeps the light OCCUPIED;
    the countdown only starts when maintain clears too."""
    entry = make_light_entry(occupancy=OCC, maintain=MAINT, timeout=60)
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(MAINT, "on")
    await settle(hass)
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 300)
    assert _state(hass).state == "on"

    hass.states.async_set(MAINT, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_maintain_clear_held_by_occupancy(hass: HomeAssistant) -> None:
    """Maintain clearing while regular occupancy is still on stays OCCUPIED."""
    entry = make_light_entry(occupancy=OCC, maintain=MAINT)
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(MAINT, "on")
    await settle(hass)
    hass.states.async_set(MAINT, "off")
    await settle(hass)

    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_maintain_clear_countdown_anchors_to_latest_occupied_time(
    hass: HomeAssistant, freezer
) -> None:
    """The countdown after a maintain clear honors the maintain entity's
    latest_occupied_time (base + remaining, here 60 - 30 = 30s)."""
    entry = make_light_entry(maintain=MAINT, timeout=60)
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()

    hass.states.async_set(MAINT, "on")
    await settle(hass)

    lot = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    hass.states.async_set(MAINT, "off", {"latest_occupied_time": lot})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _tick(hass, freezer, 20)
    assert _state(hass).state == "on"

    await _tick(hass, freezer, 15)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# False-detection quick off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_quick_off_when_maintain_clear_genuine(
    hass: HomeAssistant, freezer
) -> None:
    """A false occupancy clear followed by a genuine maintain clear runs the
    normal countdown — the maintain sensor saw real presence."""
    entry = make_light_entry(occupancy=OCC, maintain=MAINT, timeout=60)
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(MAINT, "on")
    await settle(hass)

    hass.states.async_set(OCC, "off", {"last_clear_false_detection": True})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(MAINT, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # Past the false-off delay (5s) but inside the normal countdown.
    await _tick(hass, freezer, 10)
    assert _state(hass).state == "on"

    await _tick(hass, freezer, 55)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_quick_off_when_both_clears_false(hass: HomeAssistant, freezer) -> None:
    """Both sensors flagging their clears false → the whole episode was a
    false detection; the occupancy-lit light turns off quickly."""
    entry = make_light_entry(occupancy=OCC, maintain=MAINT, timeout=60)
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(MAINT, "on")
    await settle(hass)

    hass.states.async_set(OCC, "off", {"last_clear_false_detection": True})
    await settle(hass)
    hass.states.async_set(MAINT, "off", {"last_clear_false_detection": True})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _tick(hass, freezer, 6)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# Startup seeding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_adopts_lit_light_as_maintained(
    hass: HomeAssistant, freezer
) -> None:
    """A light already on with maintain on at startup is adopted as OCCUPIED."""
    entry = make_light_entry(maintain=MAINT, timeout=60)
    hass.states.async_set(REAL, "on")
    hass.states.async_set(MAINT, "on")
    await setup_entries(hass, entry)
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 300)
    assert _state(hass).state == "on"


# ---------------------------------------------------------------------------
# Forced offs and SCHEDULED still win
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bright_forces_off_while_maintained(hass: HomeAssistant) -> None:
    """Control-mode bright turns the lights off even while maintained."""
    entry = make_light_entry(maintain=MAINT, illuminance=ILLUM)
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()
    hass.states.async_set(MAINT, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(ILLUM, "on")  # bright
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_gate_window_end_forces_off_while_maintained(
    hass: HomeAssistant,
) -> None:
    """A gate-mode window ending turns the lights off even while maintained."""
    entry = make_light_entry(
        maintain=MAINT, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
    )
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()
    hass.states.async_set(MAINT, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(SCHED, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_scheduled_ignores_maintain(hass: HomeAssistant) -> None:
    """A follow-mode window owns the lights; maintain changes are ignored."""
    entry = make_light_entry(
        maintain=MAINT, schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, entry)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    hass.states.async_set(MAINT, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    hass.states.async_set(MAINT, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED


# ---------------------------------------------------------------------------
# Hold release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hold_release_respects_maintain(hass: HomeAssistant, freezer) -> None:
    """Releasing an auto-off hold with maintain on keeps the light OCCUPIED
    without arming a timer."""
    entry = make_light_entry(maintain=MAINT, hold_entities=[HOLD], timeout=60)
    hass.states.async_set(MAINT, "off")
    hass.states.async_set(HOLD, "off")
    await setup_entries(hass, entry)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()

    hass.states.async_set(HOLD, "on")
    await settle(hass)
    hass.states.async_set(MAINT, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 300)
    assert _state(hass).state == "on"
