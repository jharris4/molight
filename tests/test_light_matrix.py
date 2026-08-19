"""Combination-matrix tests for the MoLight Virtual Light.

test_light.py covers the main end-to-end scenarios; this file sweeps the
remaining occupancy x illuminance x schedule x light-state combinations so
every gating rule of the state machine is pinned. The watched sensors are
plain states set via hass.states.async_set — the light only reads states
and attributes, so the tests stay independent of the virtual-sensor
implementations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, callback
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_KEEP,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
)
from tests.conftest import make_light_entry, settle, setup_entries

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

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
# Occupancy trigger x illuminance x gate-schedule
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
# Illuminance bright→dark activation x occupancy x gate-schedule
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
            stale = datetime.now(UTC) - timedelta(hours=1)
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
# Gate-mode schedule boundaries x machine state
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
@pytest.mark.parametrize("origin", ["manual", "occupied", "countdown"])
async def test_state_preserving_gate_keeps_on_period_at_window_end(
    hass: HomeAssistant, freezer, origin: str
) -> None:
    """The soft gate preserves every running state and its existing timer."""
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE_KEEP
    )
    hass.states.async_set(SCHED, "on")
    await setup_entries(hass, entry)

    if origin == "manual":
        await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
        await settle(hass)
        expected_state = STATE_ACTIVE
    else:
        hass.states.async_set(OCC, "on")
        await settle(hass)
        expected_state = STATE_OCCUPIED
        if origin == "countdown":
            hass.states.async_set(OCC, "off")
            await settle(hass)
            expected_state = STATE_COUNTDOWN
            # Let half the existing countdown elapse before the boundary.
            freezer.tick(timedelta(seconds=30))
            async_fire_time_changed(hass)
            await settle(hass)

    hass.states.async_set(SCHED, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == expected_state

    if origin == "countdown":
        # The boundary must not restart the 60-second countdown.
        freezer.tick(timedelta(seconds=31))
        async_fire_time_changed(hass)
        await settle(hass)
        assert _state(hass).state == "off"
        return

    # Once this on-period ends, another occupancy edge remains gated.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_state_preserving_gate_keeps_sensor_hold_outside_window(
    hass: HomeAssistant, freezer
) -> None:
    """Once on, a gate_keep light is held and re-held by occupancy outside."""
    switch = "switch.matrix_light_auto_off"
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE_KEEP
    )
    hass.states.async_set(SCHED, "on")
    await setup_entries(hass, entry)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch})
    await settle(hass)

    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Releasing the hold with the occupant still present keeps the hold.
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Occupancy returning mid-countdown re-holds the preserved on-period.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    # The activation gate itself is unchanged for an off light.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "off"


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
async def test_gate_window_start_relights_at_auto_on_brightness(
    hass: HomeAssistant,
) -> None:
    """A gate-mode window start re-activating standing occupancy is an
    automatic turn-on: the configured auto-on brightness applies."""
    entry = make_light_entry(
        occupancy=OCC,
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_GATE,
        auto_on_brightness=40,  # 40% → 102 of 255
    )
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(OCC, "on")
    await setup_entries(hass, entry)
    assert _state(hass).state == "off"

    hass.states.async_set(SCHED, "on")  # window opens with occupancy standing
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED
    assert state.attributes["brightness"] == 102


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
# Forced-off precedence with occupancy + control-illuminance + gate-schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forced_off_beats_occupancy_with_all_three_configured(
    hass: HomeAssistant,
) -> None:
    """With occupancy + control-illuminance + gate-schedule all configured, each
    forced-off path wins over active occupancy.

    Companion to test_scheduled_ignores_occupancy_and_illuminance (follow mode):
    this pins the gate/control combination. Precedence while running is
    control-bright > occupancy and gate-window-end > occupancy — active
    occupancy never shields the light from either forced off.
    """
    entry = make_light_entry(
        occupancy=OCC,
        illuminance=ILLUM,
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_GATE,
    )
    hass.states.async_set(SCHED, "on")  # in-window
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(OCC, "on")  # occupied
    await setup_entries(hass, entry)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Bright in control mode forces the lights off despite occupancy + in-window.
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    # Dark again with occupancy still active re-lights (control gate lifted).
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # Gate window ending forces the lights off despite occupancy + dark.
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


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
# Occupancy x running light states
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
    await light._timer_expired(datetime.now(UTC))
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


# ---------------------------------------------------------------------------
# Self-caused service echoes and manual control while occupied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_turn_on_while_occupied_stays_occupied(
    hass: HomeAssistant, freezer
) -> None:
    """Turning the virtual light on during occupancy keeps OCCUPIED, no timer."""
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED

    # No timer was armed: the light outlives its timeout while occupied.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_self_service_echo_does_not_upgrade_countdown(
    hass: HomeAssistant, freezer
) -> None:
    """The real light confirming our own turn_on must not restart the timer.

    Illuminance-dark re-activation grants only the remaining portion of the
    original on-period. When the real light's state then echoes our own
    service call, that echo must be recognised as self-caused — treating it
    as an external turn-on would upgrade COUNTDOWN to ACTIVE with a fresh
    full timer.
    """
    contexts = []

    @callback
    def _capture(event) -> None:
        if (
            event.data["domain"] == "light"
            and event.data["service"] == "turn_on"
            and REAL in event.data["service_data"].get("entity_id", [])
        ):
            contexts.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _capture)

    await setup_entries(hass, make_light_entry(illuminance=ILLUM))
    hass.states.async_set(ILLUM, "off")  # dark
    await settle(hass)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    # Bright forces the light off; 40s later it gets dark again, so only
    # ~20s of the original 60s on-period remain.
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    await settle(hass)
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # The real light confirms the re-activation service call (same context).
    assert contexts
    hass.states.async_set(REAL, "on", context=contexts[-1])
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # The remaining ~20s countdown still stands — not a fresh 60s timer.
    freezer.tick(timedelta(seconds=21))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_countdown_falls_back_when_lot_corrupt(
    hass: HomeAssistant, freezer
) -> None:
    """A garbage latest_occupied_time falls back to the full light_timeout."""
    await setup_entries(hass, make_light_entry(occupancy=OCC))

    hass.states.async_set(OCC, "on", {"latest_occupied_time": "garbage"})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(OCC, "off", {"latest_occupied_time": "garbage"})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # The unparsable anchor is ignored: the base 60s timeout applies.
    freezer.tick(timedelta(seconds=59))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_attribute_only_change_does_not_restart_timer(
    hass: HomeAssistant, freezer
) -> None:
    """A non-brightness attribute update (battery, ...) is not human activity."""
    await setup_entries(hass, make_light_entry())

    hass.states.async_set(REAL, "on", {"brightness": 100})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await settle(hass)
    hass.states.async_set(REAL, "on", {"brightness": 100, "battery": 42})
    await settle(hass)

    # The original 60s timer still expires on schedule — it was not restarted
    # by the attribute update at the 30s mark.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_brightness_zero_on_one_light_keeps_running(hass: HomeAssistant) -> None:
    """Brightness 0 is an off in disguise, but other lit lights keep us on."""
    await setup_entries(hass, make_light_entry(lights=[REAL, REAL2]))

    hass.states.async_set(REAL, "on", {"brightness": 100})
    await settle(hass)
    hass.states.async_set(REAL2, "on", {"brightness": 100})
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, "on", {"brightness": 0})
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["last_brightness_change_physical"] is not None

    # Dimming the second light to 0 too counts as everything off.
    hass.states.async_set(REAL2, "on", {"brightness": 0})
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_external_on_at_brightness_zero_stays_off(hass: HomeAssistant) -> None:
    """An external off→on at brightness 0 is an off in disguise.

    The virtual light must stay off without attributing a turn-on or starting
    a timer — matching how a dim to 0, _all_lights_off and the startup seed
    already treat brightness 0. The later 0 → non-zero dim is the turn-on.
    """
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry())

    hass.states.async_set(REAL, "on", {"brightness": 0})
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["last_on_physical"] is None

    # Brightness rising from 0 is the turn-on in disguise.
    hass.states.async_set(REAL, "on", {"brightness": 120})
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 120
    assert state.attributes["last_on_physical"] is not None


@pytest.mark.asyncio
async def test_member_on_at_brightness_zero_keeps_running_timer(
    hass: HomeAssistant, freezer
) -> None:
    """A member appearing on at brightness 0 while another is lit must not
    restart the running countdown — it carries no human activity."""
    hass.states.async_set(REAL, "on", {"brightness": 100})
    await setup_entries(hass, make_light_entry(lights=[REAL, REAL2]))
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await settle(hass)
    hass.states.async_set(REAL2, "on", {"brightness": 0})
    await settle(hass)
    assert _state(hass).state == "on"

    # The original 60s timer still expires on schedule.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
