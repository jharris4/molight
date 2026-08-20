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
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, callback
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    DOOR_MODE_OPEN,
    DOOR_MODE_OPEN_CLOSE,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
    STATE_WARN,
)
from tests.conftest import make_light_entry, settle, setup_entries

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

DOOR = "binary_sensor.door"
OCC = "binary_sensor.occ"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
HOLD = "input_boolean.guest"
REAL = "light.real_1"
VIRTUAL = "light.matrix_light"
MARKER = "2026-07-02T21:00:00+00:00"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


def _record_service_calls(hass: HomeAssistant) -> list[dict]:
    calls: list[dict] = []

    @callback
    def _record(event) -> None:
        calls.append(event.data)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return calls


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
    ("illum", "expect_on"),
    [
        pytest.param(None, True, id="no-illum"),
        pytest.param("dark", True, id="dark"),
        pytest.param("bright", False, id="bright-suppresses"),
    ],
)
async def test_door_open_is_illuminance_gated_like_occupancy(
    hass: HomeAssistant, illum: str | None, expect_on: bool
) -> None:
    """Opening only lights the room when dark, when illuminance is configured."""
    await _assert_door_open_gating(hass, illum, None, expect_on)


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    ("sched", "expect_on"),
    [
        pytest.param("on", True, id="in-window"),
        pytest.param("off", False, id="out-of-window"),
    ],
)
async def test_door_open_is_schedule_gated_like_occupancy(
    hass: HomeAssistant, sched: str, expect_on: bool
) -> None:
    """A regular light's gate schedule also controls automatic door activation."""
    await _assert_door_open_gating(hass, "dark", sched, expect_on)


async def _assert_door_open_gating(
    hass: HomeAssistant,
    illum: str | None,
    sched: str | None,
    expect_on: bool,
) -> None:
    """Exercise door-trigger gating with the supplied light inputs."""
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
    entry = make_light_entry(door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, occupancy=OCC)
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
    entry = make_light_entry(door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, occupancy=OCC)
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


# ---------------------------------------------------------------------------
# gate lifts while the door stands open — dark arrival / gate window start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_close_dark_arrival_lights_open_door(
    hass: HomeAssistant, freezer
) -> None:
    """Going dark with the door standing open lights the room and holds it."""
    entry = make_light_entry(
        door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, illuminance=ILLUM
    )
    hass.states.async_set(ILLUM, "on")  # bright
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")  # opened while bright — gated, no light
    await settle(hass)
    assert _state(hass).state == "off"

    hass.states.async_set(ILLUM, "off")  # dark, door still open
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Held with no timer while the door stays open.
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_open_close_dark_return_re_holds_open_door(
    hass: HomeAssistant, freezer
) -> None:
    """Bright forces off; dark returning re-lights AND re-holds the open door."""
    entry = make_light_entry(
        door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, illuminance=ILLUM
    )
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(ILLUM, "on")  # bright forces off over the held door
    await settle(hass)
    assert _state(hass).state == "off"

    hass.states.async_set(ILLUM, "off")  # dark again, door never closed
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Re-held, not a countdown: no timer may expire while the door is open.
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    ("door_mode", "expect_on"),
    [
        pytest.param(DOOR_MODE_OPEN_CLOSE, True, id="open_close-lights"),
        pytest.param(DOOR_MODE_OPEN, False, id="open-stays-off"),
    ],
)
async def test_gate_window_start_reevaluates_open_door(
    hass: HomeAssistant, door_mode: str, expect_on: bool
) -> None:
    """A standing-open open_close door lights the room when the window opens.

    An open-mode door is a momentary trigger with no standing state, so the
    window start does not re-fire it.
    """
    entry = make_light_entry(
        door=DOOR,
        door_mode=door_mode,
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_GATE,
    )
    hass.states.async_set(SCHED, "off")
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")  # opened outside the window — gated
    await settle(hass)
    assert _state(hass).state == "off"

    hass.states.async_set(SCHED, "on")  # window opens, door still open
    await settle(hass)
    state = _state(hass)
    assert (state.state == "on") is expect_on
    if expect_on:
        assert state.attributes["molight_state"] == STATE_OCCUPIED
        assert state.attributes["last_on_door"] is not None
        # Closing hands over to the normal countdown.
        hass.states.async_set(DOOR, "off")
        await settle(hass)
        assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN


# ---------------------------------------------------------------------------
# unavailability — the hold survives a sensor blip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_close_unavailable_door_keeps_holding(
    hass: HomeAssistant, freezer
) -> None:
    """A held-open door that blips unavailable keeps its hold until it closes."""
    entry = make_light_entry(door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, occupancy=OCC)
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(DOOR, "unavailable")  # battery sensor blip
    await settle(hass)
    hass.states.async_set(OCC, "off")  # occupancy clears during the blip
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(DOOR, "off")  # recovers closed — countdown starts
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN


# ---------------------------------------------------------------------------
# follow-mode schedule — the window owns the lights
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_window_ignores_door(hass: HomeAssistant) -> None:
    """While SCHEDULED, door open/close events are ignored entirely."""
    entry = make_light_entry(
        door=DOOR,
        door_mode=DOOR_MODE_OPEN_CLOSE,
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_FOLLOW,
    )
    await setup_entries(hass, entry)

    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED
    assert _state(hass).attributes["last_on_door"] is None

    hass.states.async_set(DOOR, "off")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    # Window end forces off even though the door reopened meanwhile.
    hass.states.async_set(DOOR, "on")
    await settle(hass)
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


# ---------------------------------------------------------------------------
# interactions with other holds and the warning sequence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_mode_occupancy_holds_after_trigger(
    hass: HomeAssistant, freezer
) -> None:
    """An open-mode trigger lands OCCUPIED while occupancy holds, then defers."""
    entry = make_light_entry(door=DOOR, door_mode=DOOR_MODE_OPEN, occupancy=OCC)
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Occupancy, not the open-mode door, is what holds: no timer while on.
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(OCC, "off")  # the open-mode door does not hold
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN


@pytest.mark.asyncio
async def test_reopen_mid_warning_retriggers(hass: HomeAssistant, freezer) -> None:
    """Re-opening mid effect/warn undoes the warning stage like any re-trigger."""
    entry = make_light_entry(
        door=DOOR, door_mode=DOOR_MODE_OPEN, effect_timeout=10, effect_brightness=0
    )
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")
    await settle(hass)
    hass.states.async_set(DOOR, "off")  # ignored in open mode
    await settle(hass)

    freezer.tick(timedelta(seconds=61))  # timeout → warning (EFFECT) begins
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_EFFECT

    hass.states.async_set(DOOR, "on")  # re-open mid-warning: a re-trigger
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE


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


@pytest.mark.asyncio
async def test_door_open_applies_auto_on_brightness_and_transition(
    hass: HomeAssistant,
) -> None:
    """A door-triggered turn-on is automatic: it carries the configured
    auto-on brightness and fade, exactly like an occupancy turn-on."""
    entry = make_light_entry(
        door=DOOR,
        door_mode=DOOR_MODE_OPEN,
        auto_on_brightness=40,  # 40% → 102 of 255
        auto_on_transition=2.5,
    )
    await setup_entries(hass, entry)
    calls = _record_service_calls(hass)

    hass.states.async_set(DOOR, "on")  # opened
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 102

    on_calls = [
        d
        for d in calls
        if d["domain"] == "light"
        and d["service"] == "turn_on"
        and REAL in d["service_data"].get("entity_id", [])
    ]
    assert on_calls
    assert on_calls[-1]["service_data"]["brightness"] == 102
    assert on_calls[-1]["service_data"]["transition"] == 2.5


@pytest.mark.asyncio
async def test_open_close_close_while_idle_stays_idle(hass: HomeAssistant) -> None:
    """A door opened under a bright gate never lit the room; closing it must
    not start a countdown from IDLE."""
    entry = make_light_entry(
        door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, illuminance=ILLUM
    )
    hass.states.async_set(ILLUM, "on")  # bright
    await setup_entries(hass, entry)

    hass.states.async_set(DOOR, "on")  # opened — bright-gated, no turn-on
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    hass.states.async_set(DOOR, "off")  # closed — nothing to count down
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_open_close_close_mid_warning_lets_it_finish(
    hass: HomeAssistant, freezer
) -> None:
    """Closing the door mid effect/warn lets the sequence wind down to off.

    A door standing open under a bright gate never held the light (the open
    was gated away), so its close must not restart the countdown either —
    the warning already in flight runs to completion.
    """
    entry = make_light_entry(
        door=DOOR,
        door_mode=DOOR_MODE_OPEN_CLOSE,
        illuminance=ILLUM,
        effect_timeout=10,
        effect_brightness=0,
        warn_timeout=15,
    )
    hass.states.async_set(ILLUM, "on")  # bright
    await setup_entries(hass, entry)

    # Manual on is never gated: ACTIVE with the 60s timer.
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_EFFECT

    hass.states.async_set(DOOR, "on")  # opened mid-warning — bright-gated
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_EFFECT

    hass.states.async_set(DOOR, "off")  # closed mid-warning — let it finish
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_EFFECT

    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_WARN

    freezer.tick(timedelta(seconds=16))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE
