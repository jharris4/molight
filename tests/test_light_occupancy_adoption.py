"""Tests for adopting already-active occupancy into an on light.

Occupancy normally takes the light over on its own rising edge. When it is
already on while the light turns on (manual/physical after a manual off, or
suppressed earlier by bright/window), or when the gate suppressing it lifts
(illuminance turning dark, a gate-mode window opening) while the light is
already on, the light must adopt it as OCCUPIED; otherwise a timer expires
despite presence and the steady-on sensor produces no event that could ever
rescue the light. Adoption is gated like a turn-on (bright or outside a
gate-mode window suppress it), except that bright in illuminance gate mode
only gates an off light; the maintain entity stays ungated.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    DOOR_MODE_OPEN,
    DOOR_MODE_OPEN_CLOSE,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_SWITCH,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_WARN,
)
from tests.conftest import make_light_entry, settle, setup_entries

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

OCC = "binary_sensor.occ"
DOOR = "binary_sensor.door"
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
async def test_manual_on_adopts_active_occupancy(hass: HomeAssistant, freezer) -> None:
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
@pytest.mark.parametrize("source", ["virtual", "physical"])
@pytest.mark.parametrize("mode", [ILLUMINANCE_MODE_CONTROL, ILLUMINANCE_MODE_GATE])
async def test_turn_on_while_bright_adopts_occupancy_only_in_gate_mode(
    hass: HomeAssistant, freezer, mode: str, source: str
) -> None:
    """Bright in control mode keeps the timer running over a turn-on; in gate
    mode it only stops occupancy lighting an off light, so it holds this one."""
    await setup_entries(
        hass, make_light_entry(occupancy=OCC, illuminance=ILLUM, illuminance_mode=mode)
    )
    hass.states.async_set(ILLUM, "on")  # bright
    hass.states.async_set(OCC, "on")  # suppressed: no turn-on
    await settle(hass)
    assert _state(hass).state == "off"

    if source == "virtual":
        await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    else:
        hass.states.async_set(REAL, "on")
    await settle(hass)
    if mode == ILLUMINANCE_MODE_CONTROL:
        assert _state(hass).attributes["molight_state"] == STATE_ACTIVE
        await _tick(hass, freezer, 61)
        assert _state(hass).state == "off"
        return
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("standing", [OCC, DOOR], ids=["occupancy", "door"])
async def test_dark_adopts_active_occupancy_into_on_light(
    hass: HomeAssistant, freezer, standing: str
) -> None:
    """Illuminance turning dark lifts the gate: an on light becomes OCCUPIED,
    for active occupancy or an open open_close door alike. Control mode: in
    gate mode brightness never keeps presence from holding an on light."""
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC, door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, illuminance=ILLUM
        ),
    )
    hass.states.async_set(ILLUM, "on")  # bright
    hass.states.async_set(OCC, "on" if standing == OCC else "off")  # suppressed
    await settle(hass)

    # User turns the light on regardless: ACTIVE with a timer (still bright).
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    if standing == DOOR:
        # Opened afterwards: brightness gates the opening, not an open door.
        hass.states.async_set(DOOR, "on")
        await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # It gets dark while the room is occupied: adopt, cancel the timer.
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"

    hass.states.async_set(standing, "off")
    await settle(hass)
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_dark_without_occupancy_keeps_timer(hass: HomeAssistant, freezer) -> None:
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
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("standing", [OCC, DOOR], ids=["occupancy", "door"])
async def test_gate_window_start_adopts_active_occupancy(
    hass: HomeAssistant, freezer, standing: str
) -> None:
    """A gate-mode window opening over an on light adopts active occupancy or
    an open open_close door."""
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC,
            door=DOOR,
            door_mode=DOOR_MODE_OPEN_CLOSE,
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
    )
    hass.states.async_set(SCHED, "off")  # outside the window
    hass.states.async_set(standing, "on")  # suppressed
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
    """Dark arriving mid effect/warn with occupancy on undoes the warning
    (control mode, where brightness gates occupancy over an on light)."""
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC, illuminance=ILLUM, effect_timeout=10, warn_timeout=20
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


async def _setup_gate_mode(hass: HomeAssistant, **kwargs) -> None:
    """A lit light whose gate-mode lux sensor sees it and reads bright."""
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_GATE,
            **kwargs,
        ),
    )
    hass.states.async_set(ILLUM, "off")
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    hass.states.async_set(ILLUM, "on")
    await settle(hass)


@pytest.mark.asyncio
async def test_gate_mode_occupancy_rehold_while_bright(
    hass: HomeAssistant, freezer
) -> None:
    """Moving again during the countdown holds the light the lamp makes bright."""
    await _setup_gate_mode(hass, timeout=300)
    hass.states.async_set(
        OCC, "off", {"latest_occupied_time": dt_util.utcnow().isoformat()}
    )
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _tick(hass, freezer, 60)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    await _tick(hass, freezer, 600)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_gate_mode_occupancy_cancels_a_warning_while_bright(
    hass: HomeAssistant, freezer
) -> None:
    """Occupancy cancels the warning of a light the lamp makes bright."""
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_GATE,
            warn_timeout=60,
            warn_brightness=50,
        ),
    )
    hass.states.async_set(ILLUM, "on")
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
    )
    await _tick(hass, freezer, 61)
    assert _state(hass).attributes["molight_state"] == STATE_WARN

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    assert _state(hass).attributes["brightness"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("door_mode", [DOOR_MODE_OPEN, DOOR_MODE_OPEN_CLOSE])
async def test_gate_mode_door_opening_retriggers_an_on_light_while_bright(
    hass: HomeAssistant, freezer, door_mode: str
) -> None:
    """Bright stops a door lighting an off light, not re-triggering an on one."""
    await setup_entries(
        hass,
        make_light_entry(
            door=DOOR,
            door_mode=door_mode,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_GATE,
        ),
    )
    hass.states.async_set(ILLUM, "on")
    hass.states.async_set(DOOR, "off")
    await settle(hass)
    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    hass.states.async_set(DOOR, "off")
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    await _tick(hass, freezer, 50)

    hass.states.async_set(DOOR, "on")
    await settle(hass)
    if door_mode == DOOR_MODE_OPEN:
        assert _state(hass).attributes["molight_state"] == STATE_ACTIVE
        await _tick(hass, freezer, 50)
        assert _state(hass).state == "on"  # the opening restarted the timer
        await _tick(hass, freezer, 11)
        assert _state(hass).state == "off"
    else:
        assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
        await _tick(hass, freezer, 3600)
        assert _state(hass).state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [ILLUMINANCE_MODE_CONTROL, ILLUMINANCE_MODE_GATE])
async def test_startup_adopts_occupancy_over_a_bright_on_light_in_gate_mode(
    hass: HomeAssistant, mode: str
) -> None:
    """At startup a lit light in a bright occupied room is held in gate mode."""
    hass.states.async_set(REAL, "on", {"brightness": 200})
    hass.states.async_set(ILLUM, "on")
    hass.states.async_set(OCC, "on")
    await setup_entries(
        hass, make_light_entry(occupancy=OCC, illuminance=ILLUM, illuminance_mode=mode)
    )
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == (
        STATE_OCCUPIED if mode == ILLUMINANCE_MODE_GATE else STATE_ACTIVE
    )


@pytest.mark.asyncio
async def test_gate_mode_occupancy_recovering_holds_a_bright_light_lit_meanwhile(
    hass: HomeAssistant, freezer
) -> None:
    """Occupancy back from an outage holds a light turned on during it."""
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC, illuminance=ILLUM, illuminance_mode=ILLUMINANCE_MODE_GATE
        ),
    )
    hass.states.async_set(ILLUM, "on")
    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_gate_switch_end_keeps_occupancy_holding_a_bright_light(
    hass: HomeAssistant, freezer
) -> None:
    """A Gate and switch state end leaves occupancy holding the lit light."""
    hass.states.async_set(SCHED, "on")
    await _setup_gate_mode(
        hass, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE_SWITCH
    )
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"


MAINT = "binary_sensor.maint"
HOLD = "input_boolean.keep_on"
SWITCH = "switch.matrix_light_auto_off"


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh", ["door", "auto_off", "keep_on", "reload", "turn_on"])
@pytest.mark.parametrize("pulse", ["occupancy", "maintain"])
@pytest.mark.parametrize("warned", [False, True])
async def test_false_pulse_does_not_shorten_a_fresh_timeout(
    hass: HomeAssistant, freezer, fresh: str, pulse: str, warned: bool
) -> None:
    """A fresh full timeout, as a hold releasing or the door closing starts,
    outlives a false detection: the sensors' old history is no reason to end
    it early, and its warning still starts on time."""
    old = (dt_util.utcnow() - timedelta(seconds=600)).isoformat()
    history = {"latest_occupied_time": old, "last_clear_false_detection": False}
    hass.states.async_set(REAL, "off")
    hass.states.async_set(OCC, "off", history)
    hass.states.async_set(MAINT, "off", history)
    hass.states.async_set(DOOR, "off")
    hass.states.async_set(HOLD, "off")
    entry = make_light_entry(
        occupancy=OCC,
        maintain=MAINT,
        door=DOOR,
        door_mode=DOOR_MODE_OPEN_CLOSE,
        hold_entities=[HOLD],
        timeout=60,
        false_off_delay=5,
        warn_timeout=10 if warned else None,
        warn_brightness=10 if warned else None,
    )
    await setup_entries(hass, entry)
    await settle(hass)

    async def switch(service: str) -> None:
        await hass.services.async_call("switch", service, {"entity_id": SWITCH})
        await settle(hass)

    async def turn_on() -> None:
        await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
        await settle(hass)

    # Hold the light for longer than its timeout, then start the fresh one.
    if fresh == "door":
        hass.states.async_set(DOOR, "on")
        await settle(hass)
    else:
        await turn_on()
        # The member reports lit, which a reload goes by.
        hass.states.async_set(REAL, "on")
        await settle(hass)
    if fresh == "auto_off":
        await switch("turn_off")
    elif fresh == "keep_on":
        hass.states.async_set(HOLD, "on")
        await settle(hass)
    await _tick(hass, freezer, 70 if fresh not in ("reload", "turn_on") else 40)
    assert _state(hass).state == "on"
    if fresh == "door":
        hass.states.async_set(DOOR, "off")
    elif fresh == "auto_off":
        await switch("turn_on")
    elif fresh == "keep_on":
        hass.states.async_set(HOLD, "off")
    elif fresh == "reload":
        assert await hass.config_entries.async_reload(entry.entry_id)
    else:
        await turn_on()
    await settle(hass)

    await _tick(hass, freezer, 1)
    sensor = OCC if pulse == "occupancy" else MAINT
    hass.states.async_set(sensor, "on", history)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    await _tick(hass, freezer, 2)
    history["last_clear_false_detection"] = True
    hass.states.async_set(sensor, "off", history)
    await settle(hass)

    await _tick(hass, freezer, 56)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 2)
    if warned:
        assert _state(hass).attributes["molight_state"] == STATE_WARN
        await _tick(hass, freezer, 10)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_false_pulse_still_turns_off_quickly_a_light_it_lit(
    hass: HomeAssistant, freezer
) -> None:
    """A fresh timeout of an earlier on-period gives a later false detection
    nothing to wait for."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(OCC, "off")
    await setup_entries(
        hass, make_light_entry(occupancy=OCC, timeout=60, false_off_delay=5)
    )
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    await _tick(hass, freezer, 10)
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)

    await _tick(hass, freezer, 10)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    await _tick(hass, freezer, 2)
    hass.states.async_set(OCC, "off", {"last_clear_false_detection": True})
    await settle(hass)
    await _tick(hass, freezer, 6)
    assert _state(hass).state == "off"
