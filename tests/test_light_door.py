"""Door-sensor tests for the MoLight Virtual Light.

A configured door entity drives the light directly. Opening the door is a
turn-on trigger, gated by illuminance/a gate-mode schedule exactly like
occupancy. door_mode then decides what the door state does afterwards:

  open        — opening lights the room with the normal timeout; the door is
                otherwise ignored (a momentary trigger, closing does nothing).
  open_close  — the open door holds the light on (OCCUPIED, no timer) while it
                stays open; closing starts the countdown, deferring to any
                active occupancy/keep-on hold.

The watched entities are plain states set via hass.states.async_set — the
light only reads their state, so these stay independent of the real
door/occupancy implementations.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    DOOR_MODE_OPEN,
    DOOR_MODE_OPEN_CLOSE,
    SCHEDULE_MODE_GATE,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
)
from tests.conftest import make_light_entry, settle, setup_entries

DOOR = "binary_sensor.door"
OCC = "binary_sensor.occ"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
HOLD = "input_boolean.guest"
VIRTUAL = "light.matrix_light"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


# ---------------------------------------------------------------------------
# open mode — momentary trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_mode_trigger_times_out(hass: HomeAssistant, freezer) -> None:
    """Opening the door lights the room (ACTIVE) with the normal timeout."""
    entry = make_light_entry(door=DOOR, door_mode=DOOR_MODE_OPEN)
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")  # opened
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["last_on_door"] is not None

    # The normal light_timeout applies even while the door stays open.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_open_mode_close_is_noop(hass: HomeAssistant, freezer) -> None:
    """In open mode, closing the door does nothing — the timer stands."""
    entry = make_light_entry(door=DOOR, door_mode=DOOR_MODE_OPEN)
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(DOOR, "off")  # closed — ignored
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_open_mode_reopen_retriggers_timer(hass: HomeAssistant, freezer) -> None:
    """Re-opening the door restarts the full timeout."""
    entry = make_light_entry(door=DOOR, door_mode=DOOR_MODE_OPEN)
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")
    await settle(hass)
    hass.states.async_set(DOOR, "off")
    await settle(hass)

    # 40s in, re-open: a fresh 60s timer starts.
    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    await settle(hass)
    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=40))  # 80s since first open, 40s since re-open
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    freezer.tick(timedelta(seconds=21))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# open-trigger gating (dark / schedule) — like occupancy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("illum", "sched", "expect_on"),
    [
        pytest.param(None, None, True, id="no-illum/no-sched"),
        pytest.param("dark", None, True, id="dark"),
        pytest.param("bright", None, False, id="bright-suppresses"),
        pytest.param("dark", "on", True, id="dark/in-window"),
        pytest.param("dark", "off", False, id="dark/out-of-window"),
    ],
)
async def test_door_open_is_gated_like_occupancy(
    hass: HomeAssistant, illum: str | None, sched: str | None, expect_on: bool
) -> None:
    """Opening only lights the room when dark (if gated) and inside a window."""
    entry = make_light_entry(
        door=DOOR,
        door_mode=DOOR_MODE_OPEN,
        illuminance=ILLUM if illum else None,
        schedule=SCHED if sched else None,
        schedule_mode=SCHEDULE_MODE_GATE if sched else None,
    )
    if illum:
        hass.states.async_set(ILLUM, "on" if illum == "bright" else "off")
    if sched:
        hass.states.async_set(SCHED, sched)
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")
    await settle(hass)

    state = _state(hass)
    assert (state.state == "on") is expect_on
    expected = STATE_ACTIVE if expect_on else STATE_IDLE
    assert state.attributes["molight_state"] == expected


# ---------------------------------------------------------------------------
# open_close mode — held while open, off (countdown) when closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_close_holds_while_open(hass: HomeAssistant, freezer) -> None:
    """The open door holds the light on with no timer until it closes."""
    entry = make_light_entry(door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE)
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")  # opened
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # No timer runs while the door is held open.
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Closing starts the countdown toward off.
    hass.states.async_set(DOOR, "off")  # closed
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_open_close_close_defers_to_occupancy(hass: HomeAssistant) -> None:
    """Closing the door keeps the light on while occupancy is still active."""
    entry = make_light_entry(
        door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, occupancy=OCC
    )
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Someone walked in and shut the door — occupancy still holds the light.
    hass.states.async_set(DOOR, "off")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Only when occupancy also clears does the countdown begin.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN


@pytest.mark.asyncio
async def test_open_close_occupancy_clear_defers_to_open_door(
    hass: HomeAssistant, freezer
) -> None:
    """Occupancy clearing while the door is still open keeps the light held."""
    entry = make_light_entry(
        door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, occupancy=OCC
    )
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Occupancy clears but the door is still open → still held, no countdown.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_open_close_close_defers_to_hold_entity(hass: HomeAssistant) -> None:
    """A keep-on hold suspends the close-countdown's turn-off."""
    entry = make_light_entry(
        door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, hold_entities=[HOLD]
    )
    hass.states.async_set(HOLD, "on")  # auto-off held
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Closing moves to COUNTDOWN but the hold means no timer is armed.
    hass.states.async_set(DOOR, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN
    assert _state(hass).attributes["auto_off_held"] is True


@pytest.mark.asyncio
async def test_open_close_bright_forces_off_over_held_door(
    hass: HomeAssistant,
) -> None:
    """Bright in control mode forces off even while the open door holds the light."""
    entry = make_light_entry(
        door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, illuminance=ILLUM
    )
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(ILLUM, "on")  # bright
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_open_close_seed_adopts_open_door(hass: HomeAssistant, freezer) -> None:
    """At startup an already-on light with the door open is adopted as OCCUPIED."""
    entry = make_light_entry(door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE)
    hass.states.async_set("light.real_1", "on")
    hass.states.async_set(DOOR, "on")  # door already open at startup
    await setup_entries(hass, entry)
    await settle(hass)

    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # No timer while held.
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"
