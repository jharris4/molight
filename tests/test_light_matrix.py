"""Combination-matrix tests for the MoLight Virtual Light.

test_light.py covers the main end-to-end scenarios; this file sweeps the
remaining occupancy × illuminance × schedule × light-state combinations so
every gating rule of the state machine is pinned. The watched sensors are
plain states set via hass.states.async_set — the light only reads states
and attributes, so the tests stay independent of the virtual-sensor
implementations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
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


# ---------------------------------------------------------------------------
# Occupancy trigger × illuminance × gate-schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("illum", "sched", "expect_on"),
    [
        pytest.param(None, None, True, id="no-illum/no-sched"),
        pytest.param(None, "on", True, id="no-illum/gate-in-window"),
        pytest.param(None, "off", False, id="no-illum/gate-out-of-window"),
        pytest.param("dark", None, True, id="dark/no-sched"),
        pytest.param("dark", "on", True, id="dark/gate-in-window"),
        pytest.param("dark", "off", False, id="dark/gate-out-of-window"),
        pytest.param("bright", None, False, id="bright/no-sched"),
        pytest.param("bright", "on", False, id="bright/gate-in-window"),
        pytest.param("bright", "off", False, id="bright/gate-out-of-window"),
    ],
)
async def test_occupancy_trigger_gating(
    hass: HomeAssistant, illum: str | None, sched: str | None, expect_on: bool
) -> None:
    """Occupancy turns lights on iff it is dark (or ungated) and in-window."""
    entry = make_light_entry(
        occupancy=OCC,
        illuminance=ILLUM if illum else None,
        schedule=SCHED if sched else None,
        schedule_mode=SCHEDULE_MODE_GATE if sched else None,
    )
    if illum:
        hass.states.async_set(ILLUM, "on" if illum == "bright" else "off")
    if sched:
        hass.states.async_set(SCHED, sched)
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)

    state = _state(hass)
    assert (state.state == "on") is expect_on
    expected = STATE_OCCUPIED if expect_on else STATE_IDLE
    assert state.attributes["molight_state"] == expected


# ---------------------------------------------------------------------------
# Illuminance bright→dark activation × occupancy × gate-schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("occ", "lot", "sched", "expect_state"),
    [
        pytest.param("on", None, None, STATE_OCCUPIED, id="occupied"),
        pytest.param("on", None, "on", STATE_OCCUPIED, id="occupied/gate-in-window"),
        pytest.param("on", None, "off", STATE_IDLE, id="occupied/gate-out-of-window"),
        pytest.param("off", "stale", None, STATE_IDLE, id="clear-stale-lot"),
        # The next two pin current behavior: with no occupancy history at all
        # (no latest_occupied_time / no recorded on-period) the dark transition
        # still lights the room for the full light_timeout.
        pytest.param("off", None, None, STATE_COUNTDOWN, id="clear-no-lot"),
        pytest.param(None, None, None, STATE_COUNTDOWN, id="no-occ-no-history"),
    ],
)
async def test_illuminance_dark_activation(
    hass: HomeAssistant,
    occ: str | None,
    lot: str | None,
    sched: str | None,
    expect_state: str,
) -> None:
    """Going dark activates lights based on occupancy state/history and gating."""
    entry = make_light_entry(
        occupancy=OCC if occ else None,
        illuminance=ILLUM,
        schedule=SCHED if sched else None,
        schedule_mode=SCHEDULE_MODE_GATE if sched else None,
    )
    hass.states.async_set(ILLUM, "on")  # bright
    if occ:
        attrs = {}
        if lot == "stale":
            stale = datetime.now(timezone.utc) - timedelta(hours=1)
            attrs["latest_occupied_time"] = stale.isoformat()
        hass.states.async_set(OCC, occ, attrs)
    if sched:
        hass.states.async_set(SCHED, sched)
    await setup_entries(hass, entry)

    hass.states.async_set(ILLUM, "off")  # dark
    await settle(hass)

    state = _state(hass)
    assert state.attributes["molight_state"] == expect_state
    assert (state.state == "on") is (expect_state != STATE_IDLE)


@pytest.mark.asyncio
async def test_illuminance_dark_is_noop_while_running(
    hass: HomeAssistant, freezer
) -> None:
    """Dark while already ACTIVE changes nothing; the original timer stands."""
    entry = make_light_entry(illuminance=ILLUM)
    hass.states.async_set(ILLUM, "on")  # bright
    await setup_entries(hass, entry)

    # Manual turn-on is never gated by illuminance.
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# SCHEDULED (follow-mode window) isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduled_ignores_occupancy_and_illuminance(
    hass: HomeAssistant, freezer
) -> None:
    """While SCHEDULED, occupancy and illuminance changes are ignored entirely."""
    entry = make_light_entry(
        occupancy=OCC,
        illuminance=ILLUM,
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_FOLLOW,
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(hass, entry)
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED

    # Occupancy on/off must not take over or start a countdown.
    hass.states.async_set(OCC, "on")
    await settle(hass)
    state = _state(hass)
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["last_on_occupancy"] is None

    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    # Bright must not force the lights off; dark again must not re-trigger.
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    # No timer runs while SCHEDULED.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_occupancy_can_relight_after_manual_off_mid_window(
    hass: HomeAssistant,
) -> None:
    """After a manual off mid-window the state is IDLE, so occupancy applies again.

    Pins current behavior: only the SCHEDULED state shields the follow window
    from occupancy — a manual override hands control back to the sensors.
    """
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(hass, entry)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    hass.states.async_set(OCC, "on")
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


# ---------------------------------------------------------------------------
# Gate-mode schedule boundaries × machine state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["manual", "occupied", "countdown"])
async def test_gate_window_end_forces_off(hass: HomeAssistant, origin: str) -> None:
    """Window end turns the lights off from every running state."""
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
    )
    hass.states.async_set(SCHED, "on")
    await setup_entries(hass, entry)

    if origin == "manual":
        await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
        await hass.async_block_till_done()
        assert _state(hass).attributes["molight_state"] == STATE_ACTIVE
    else:
        hass.states.async_set(OCC, "on")
        await settle(hass)
        assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
        if origin == "countdown":
            hass.states.async_set(OCC, "off")
            await settle(hass)
            assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    hass.states.async_set(SCHED, "off")
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_gate_window_end_noop_while_idle(hass: HomeAssistant) -> None:
    """Window end while IDLE stays IDLE."""
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
    )
    hass.states.async_set(SCHED, "on")
    await setup_entries(hass, entry)

    hass.states.async_set(SCHED, "off")
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_gate_window_start_respects_illuminance(hass: HomeAssistant) -> None:
    """Window start with occupancy active but bright stays off; dark then lights."""
    entry = make_light_entry(
        occupancy=OCC,
        illuminance=ILLUM,
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_GATE,
    )
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(ILLUM, "on")  # bright
    hass.states.async_set(OCC, "on")
    await setup_entries(hass, entry)

    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    # It gets dark inside the window with occupancy still active → lights on.
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_gate_window_start_keeps_running_state(
    hass: HomeAssistant, freezer
) -> None:
    """Window start while already ACTIVE leaves the state and timer untouched."""
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
    )
    hass.states.async_set(SCHED, "off")
    await setup_entries(hass, entry)

    # Manual turn-on works outside the window (gating only applies to sensors).
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # The original 60s timer still fires.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# Manual control is never gated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["bright", "gate-out-of-window"])
async def test_manual_turn_on_never_gated(
    hass: HomeAssistant, freezer, blocker: str
) -> None:
    """The user can always turn the light on; the normal timeout applies."""
    if blocker == "bright":
        entry = make_light_entry(illuminance=ILLUM)
        hass.states.async_set(ILLUM, "on")
    else:
        entry = make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE)
        hass.states.async_set(SCHED, "off")
    await setup_entries(hass, entry)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# Occupancy × running light states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reoccupancy_during_countdown_cancels_timer(
    hass: HomeAssistant, freezer
) -> None:
    """Occupancy re-triggering during COUNTDOWN returns to OCCUPIED indefinitely."""
    entry = make_light_entry(occupancy=OCC)
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Way past any timer — occupied lights never time out.
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_expired_timer_defers_to_reoccupancy_in_same_iteration(
    hass: HomeAssistant, freezer
) -> None:
    """A timer expiring in the same loop iteration occupancy returns is a no-op.

    async_call_later has already fired the callback at that point, so the
    occupancy handler's _cancel_timer can't stop it — _timer_expired itself
    must notice the state machine moved on (mirroring its _held guard) instead
    of turning the lights off over an occupied room.
    """
    entry = make_light_entry(occupancy=OCC)
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # Occupancy returns; the expiry coroutine still runs afterwards, exactly
    # as when both land in the same event-loop iteration.
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    light = hass.data["entity_components"]["light"].get_entity(VIRTUAL)
    await light._timer_expired(datetime.now(timezone.utc))
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_occupancy_takes_over_manual_light(hass: HomeAssistant, freezer) -> None:
    """Occupancy during a manual on-period suspends the timeout until it clears."""
    entry = make_light_entry(occupancy=OCC)
    await setup_entries(hass, entry)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # The manual 60s timer was cancelled.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    # Clearing starts the countdown (no latest_occupied_time → full timeout).
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_occupancy_clear_is_noop_when_not_occupied(
    hass: HomeAssistant, freezer
) -> None:
    """An occupancy clear that never occupied us leaves ACTIVE and its timer alone."""
    entry = make_light_entry(occupancy=OCC, illuminance=ILLUM)
    hass.states.async_set(ILLUM, "on")  # bright → occupancy was suppressed
    await setup_entries(hass, entry)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()

    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_external_off_during_occupied_goes_idle(hass: HomeAssistant) -> None:
    """Turning the real light off wins over active occupancy and stays off."""
    entry = make_light_entry(occupancy=OCC)
    await setup_entries(hass, entry)

    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(REAL, "off")
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE

    # Occupancy is still on but fires no new event — the lights stay off.
    await settle(hass)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# Multiple real lights
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stays_on_until_all_real_lights_off(hass: HomeAssistant) -> None:
    """The virtual light only goes idle once every real light is off."""
    entry = make_light_entry(lights=[REAL, REAL2])
    await setup_entries(hass, entry)

    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(REAL2, "on")
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(REAL2, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
