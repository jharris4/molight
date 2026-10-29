"""A blank turn-off timeout: the light runs no timer.

Nothing but a manual off, a false detection or a schedule window end turns
the light off, whatever lit it. These tests sweep every path that would
otherwise start the turn-off timer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    DOMAIN,
    DOOR_MODE_OPEN,
    DOOR_MODE_OPEN_CLOSE,
    ENTITY_TYPE_OCCUPANCY,
    ILLUMINANCE_MODE_CONTROL,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
)
from tests.conftest import (
    light_targets,
    make_light_entry,
    record_service_calls,
    restart_entries,
    settle,
    setup_entries,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

OCC = "binary_sensor.occ"
ILLUM = "binary_sensor.illum"
DOOR = "binary_sensor.door"
HOLD = "input_boolean.hold"
MAINT = "binary_sensor.maint"
MOTION = "binary_sensor.motion_1"
FD_OCC = "binary_sensor.fd_occupancy"
REAL = "light.real_1"
VIRTUAL = "light.matrix_light"
# Longer than the longest timeout the form allows (4 h).
LONG = 5 * 3600


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


async def _tick(hass: HomeAssistant, freezer, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


async def _turn_on(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)


async def _assert_stays_on(hass: HomeAssistant, freezer, calls: list[dict]) -> None:
    """The light is on with no timer and is still on hours later."""
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE
    await _tick(hass, freezer, LONG)
    assert _state(hass).state == "on"
    assert light_targets(calls, "turn_off") == []


def _fd_occupancy_entry() -> MockConfigEntry:
    """Occupancy sensor that classifies a 31 s blip as a false detection."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "FD Occupancy",
            CONF_OCCUPANCY_SENSOR: MOTION,
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )


@pytest.mark.asyncio
async def test_manual_turn_on_runs_no_timer(hass: HomeAssistant, freezer) -> None:
    """A light turned on by hand stays on until turned off by hand."""
    await setup_entries(hass, make_light_entry(timeout=None))
    calls = record_service_calls(hass)

    await _turn_on(hass)
    await _assert_stays_on(hass, freezer, calls)

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_occupancy_clear_leaves_the_light_on(
    hass: HomeAssistant, freezer
) -> None:
    """Occupancy turns the light on; its clearing starts no countdown."""
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, make_light_entry(timeout=None, occupancy=OCC))
    calls = record_service_calls(hass)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    freezer.tick(timedelta(seconds=100))
    left = datetime.now(UTC).isoformat()
    hass.states.async_set(OCC, "off", {"latest_occupied_time": left})
    await settle(hass)
    await _assert_stays_on(hass, freezer, calls)


@pytest.mark.asyncio
async def test_false_detection_still_turns_the_light_off(
    hass: HomeAssistant, freezer
) -> None:
    """A false detection turns the light off after the false-detection off delay."""
    hass.states.async_set(MOTION, "off")
    await setup_entries(
        hass,
        _fd_occupancy_entry(),
        make_light_entry(timeout=None, occupancy=FD_OCC, false_off_delay=5),
    )

    hass.states.async_set(MOTION, "on")
    await settle(hass)
    assert _state(hass).state == "on"

    freezer.tick(timedelta(seconds=31))
    hass.states.async_set(MOTION, "off")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _tick(hass, freezer, 6)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("door_mode", [DOOR_MODE_OPEN, DOOR_MODE_OPEN_CLOSE])
async def test_door_turns_the_light_on_without_a_timer(
    hass: HomeAssistant, freezer, door_mode: str
) -> None:
    """The door lights the room; neither the opening nor the closing times it out."""
    hass.states.async_set(DOOR, "off")
    await setup_entries(
        hass, make_light_entry(timeout=None, door=DOOR, door_mode=door_mode)
    )
    calls = record_service_calls(hass)

    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(DOOR, "off")
    await settle(hass)
    await _assert_stays_on(hass, freezer, calls)


@pytest.mark.asyncio
async def test_hold_release_starts_no_timer(hass: HomeAssistant, freezer) -> None:
    """Releasing a keep-on hold leaves the light on, as the hold did."""
    hass.states.async_set(HOLD, "off")
    await setup_entries(hass, make_light_entry(timeout=None, hold_entities=[HOLD]))
    calls = record_service_calls(hass)

    await _turn_on(hass)
    hass.states.async_set(HOLD, "on")
    await settle(hass)
    assert _state(hass).attributes["auto_off_held"] is True
    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert _state(hass).attributes["auto_off_held"] is False
    await _assert_stays_on(hass, freezer, calls)


@pytest.mark.asyncio
async def test_restart_adopts_a_lit_light_without_a_timer(
    hass: HomeAssistant, freezer
) -> None:
    """A real light found on at startup is adopted and never timed out."""
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, make_light_entry(timeout=None))
    await settle(hass)
    calls = record_service_calls(hass)
    await _assert_stays_on(hass, freezer, calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("lit_by", ["hand", "occupancy"])
@pytest.mark.parametrize("restart", [False, True], ids=["live", "restart"])
async def test_bright_off_resumes_whenever_dark(
    hass: HomeAssistant, freezer, lit_by: str, restart: bool
) -> None:
    """Daylight pauses the on-period; dark brings it back, hours later too."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(OCC, "off")
    entry = make_light_entry(
        timeout=None,
        occupancy=OCC,
        illuminance=ILLUM,
        illuminance_mode=ILLUMINANCE_MODE_CONTROL,
    )
    await setup_entries(hass, entry)

    if lit_by == "hand":
        await _turn_on(hass)
    else:
        hass.states.async_set(OCC, "on")
        await settle(hass)
        freezer.tick(timedelta(seconds=100))
        left = datetime.now(UTC).isoformat()
        hass.states.async_set(OCC, "off", {"latest_occupied_time": left})
        await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["bright_forced_off"] is True

    await _tick(hass, freezer, LONG)
    if restart:
        await restart_entries(hass, entry)
        await settle(hass)
        assert _state(hass).state == "off"
    calls = record_service_calls(hass)
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    await _assert_stays_on(hass, freezer, calls)


@pytest.mark.asyncio
async def test_manual_off_while_bright_ends_the_on_period(
    hass: HomeAssistant, freezer
) -> None:
    """After a manual off, dark no longer brings the light back."""
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(
        hass,
        make_light_entry(
            timeout=None, illuminance=ILLUM, illuminance_mode=ILLUMINANCE_MODE_CONTROL
        ),
    )
    await _turn_on(hass)
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).attributes["bright_forced_off"] is True

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    await _tick(hass, freezer, 10)
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_maintain_clear_leaves_the_light_on(hass: HomeAssistant, freezer) -> None:
    """The maintain sensor holds a lit light; its clearing starts no countdown."""
    hass.states.async_set(MAINT, "off")
    await setup_entries(hass, make_light_entry(timeout=None, maintain=MAINT))
    calls = record_service_calls(hass)

    await _turn_on(hass)
    hass.states.async_set(MAINT, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    freezer.tick(timedelta(seconds=100))
    left = datetime.now(UTC).isoformat()
    hass.states.async_set(MAINT, "off", {"latest_occupied_time": left})
    await settle(hass)
    await _assert_stays_on(hass, freezer, calls)


@pytest.mark.asyncio
async def test_bright_while_occupied_resumes_once_dark_and_empty(
    hass: HomeAssistant, freezer
) -> None:
    """Daylight pauses an occupied room's light; dark brings it back once empty."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(OCC, "off")
    await setup_entries(
        hass,
        make_light_entry(
            timeout=None,
            occupancy=OCC,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_CONTROL,
        ),
    )
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["bright_forced_off"] is True

    freezer.tick(timedelta(seconds=100))
    left = datetime.now(UTC).isoformat()
    hass.states.async_set(OCC, "off", {"latest_occupied_time": left})
    await settle(hass)
    assert _state(hass).state == "off"

    await _tick(hass, freezer, LONG)
    calls = record_service_calls(hass)
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    await _assert_stays_on(hass, freezer, calls)


@pytest.mark.asyncio
async def test_hold_release_while_bright_pauses_until_dark(
    hass: HomeAssistant, freezer
) -> None:
    """A release into daylight turns the light off; dark brings it back, no timer."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(HOLD, "off")
    await setup_entries(
        hass,
        make_light_entry(
            timeout=None,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_CONTROL,
            hold_entities=[HOLD],
        ),
    )
    await _turn_on(hass)
    hass.states.async_set(HOLD, "on")
    await settle(hass)
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "on"  # held through the daylight

    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["bright_forced_off"] is True

    await _tick(hass, freezer, LONG)
    calls = record_service_calls(hass)
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    await _assert_stays_on(hass, freezer, calls)
