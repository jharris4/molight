"""Combination-matrix tests for virtual lights.

test_light.py covers the main end-to-end scenarios; this file sweeps the
remaining occupancy x illuminance x schedule x light-state combinations so
every gating rule of the state machine is pinned. The watched sensors are
plain states set via hass.states.async_set; the light only reads states
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
    DOOR_MODE_OPEN,
    DOOR_MODE_OPEN_CLOSE,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE_ONLY,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_KEEP,
    SCHEDULE_MODE_GATE_SWITCH,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
    STATE_WARN,
)
from tests.conftest import (
    light_targets,
    make_light_entry,
    record_service_calls,
    restart_entries,
    settle,
    setup_entries,
)

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
    ("illum", "expect_on"),
    [
        pytest.param(None, True, id="no-illum"),
        pytest.param("dark", True, id="dark"),
        pytest.param("bright", False, id="bright"),
    ],
)
async def test_occupancy_trigger_illuminance_gating(
    hass: HomeAssistant, illum: str | None, expect_on: bool
) -> None:
    """Occupancy turns lights on only when dark, if illuminance is configured."""
    await _assert_occupancy_trigger_gating(hass, illum, None, expect_on)


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    ("illum", "sched", "expect_on"),
    [
        pytest.param(None, "on", True, id="no-illum/in-window"),
        pytest.param(None, "off", False, id="no-illum/out-of-window"),
        pytest.param("dark", "on", True, id="dark/in-window"),
        pytest.param("dark", "off", False, id="dark/out-of-window"),
        pytest.param("bright", "on", False, id="bright/in-window"),
        pytest.param("bright", "off", False, id="bright/out-of-window"),
    ],
)
async def test_occupancy_trigger_schedule_gating(
    hass: HomeAssistant, illum: str | None, sched: str, expect_on: bool
) -> None:
    """A regular Virtual Light's gate schedule combines with illuminance gating."""
    await _assert_occupancy_trigger_gating(hass, illum, sched, expect_on)


async def _assert_occupancy_trigger_gating(
    hass: HomeAssistant,
    illum: str | None,
    sched: str | None,
    expect_on: bool,
) -> None:
    """Exercise occupancy-trigger gating with the supplied light inputs."""
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
# Illuminance bright→dark turn-on x occupancy x gate-schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("occ", "lot", "expect_state"),
    [
        pytest.param("on", None, STATE_OCCUPIED, id="occupied"),
        pytest.param("off", "stale", STATE_IDLE, id="clear-stale-lot"),
        # With no on-period at all there is nothing to resume: the
        # dark edge must not light a long-empty (or never-lit) room, exactly
        # as a stale latest_occupied_time doesn't.
        pytest.param("off", None, STATE_IDLE, id="clear-no-lot"),
        pytest.param(None, None, STATE_IDLE, id="no-occ-no-lot"),
    ],
)
async def test_illuminance_dark_turn_on(
    hass: HomeAssistant,
    occ: str | None,
    lot: str | None,
    expect_state: str,
) -> None:
    """Going dark turns lights on based on occupancy state and latest_occupied_time."""
    await _assert_illuminance_dark_turn_on(hass, occ, lot, None, expect_state)


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    ("sched", "expect_state"),
    [
        pytest.param("on", STATE_OCCUPIED, id="in-window"),
        pytest.param("off", STATE_IDLE, id="out-of-window"),
    ],
)
async def test_illuminance_dark_turn_on_obeys_gate_schedule(
    hass: HomeAssistant, sched: str, expect_state: str
) -> None:
    """A regular Virtual Light's gate schedule controls dark-triggered turn-ons."""
    await _assert_illuminance_dark_turn_on(hass, "on", None, sched, expect_state)


async def _assert_illuminance_dark_turn_on(
    hass: HomeAssistant,
    occ: str | None,
    lot: str | None,
    sched: str | None,
    expect_state: str,
) -> None:
    """Exercise dark-triggered turn-ons with the supplied light inputs."""
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

    # Virtual turn-on is never gated by illuminance.
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


async def _cross_bright_then_dark(hass: HomeAssistant, freezer) -> None:
    """A cloud passes: bright for 10 s, then dark again."""
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)


async def _tick(hass: HomeAssistant, freezer, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


def _real_light_reports(hass: HomeAssistant) -> None:
    """Have the real light report each command MoLight sends it."""

    @callback
    def _report(event) -> None:
        data = event.data["service_data"]
        if event.data["domain"] != "light" or data.get("entity_id") != [REAL]:
            return
        if event.data["service"] == "turn_off":
            hass.states.async_set(REAL, "off", context=event.context)
            return
        attrs = {"brightness": data["brightness"]} if "brightness" in data else {}
        hass.states.async_set(REAL, "on", attrs, context=event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _report)


async def _assert_resumed_for(hass: HomeAssistant, freezer, seconds: int) -> None:
    """The light is counting down and turns off after exactly this long."""
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, seconds - 1)
    assert _state(hass).state == "on"
    await _tick(hass, freezer, 2)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [ILLUMINANCE_MODE_CONTROL, ILLUMINANCE_MODE_GATE_ONLY])
@pytest.mark.parametrize("off_via", ["virtual", "physical"])
async def test_dark_edge_does_not_turn_on_after_manual_off(
    hass: HomeAssistant, freezer, mode: str, off_via: str
) -> None:
    """Going dark resumes no on-period that the person ended."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(illuminance=ILLUM, illuminance_mode=mode, timeout=300)
    )

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=10))
    if off_via == "virtual":
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
        )
    hass.states.async_set(REAL, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    calls = record_service_calls(hass)
    await _cross_bright_then_dark(hass, freezer)

    assert light_targets(calls, "turn_on") == []
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE
    assert _state(hass).attributes["bright_forced_off"] is False


@pytest.mark.asyncio
async def test_dark_edge_does_not_turn_on_after_manual_off_in_countdown(
    hass: HomeAssistant, freezer
) -> None:
    """A recent latest_occupied_time resumes nothing after a manual off."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(OCC, "off")
    await setup_entries(
        hass, make_light_entry(occupancy=OCC, illuminance=ILLUM, timeout=300)
    )

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    left = datetime.now(UTC).isoformat()
    hass.states.async_set(OCC, "off", {"latest_occupied_time": left})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    calls = record_service_calls(hass)
    await _cross_bright_then_dark(hass, freezer)

    assert light_targets(calls, "turn_on") == []
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_dark_edge_does_not_turn_on_after_gate_window_end(
    hass: HomeAssistant, freezer
) -> None:
    """An on-period ended by a gate window is not resumed in the next one."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(SCHED, "on")
    entry = make_light_entry(
        illuminance=ILLUM, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE, timeout=300
    )
    await setup_entries(hass, entry)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_IDLE
    hass.states.async_set(SCHED, "on")
    await settle(hass)

    calls = record_service_calls(hass)
    await _cross_bright_then_dark(hass, freezer)

    assert light_targets(calls, "turn_on") == []
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("held_for", [0, 3600])
async def test_dark_edge_resumes_after_bright_off_at_hold_release(
    hass: HomeAssistant, freezer, held_for: int
) -> None:
    """A hold release while bright resumes the timer that it would have started."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set("input_boolean.keep_on", "off")
    entry = make_light_entry(
        illuminance=ILLUM, keep_on_entities=["input_boolean.keep_on"]
    )
    await setup_entries(hass, entry)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set("input_boolean.keep_on", "on")
    await settle(hass)
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "on"  # held
    await _tick(hass, freezer, held_for)
    hass.states.async_set("input_boolean.keep_on", "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_IDLE
    assert _state(hass).attributes["bright_forced_off"] is True

    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    await _assert_resumed_for(hass, freezer, 50)


@pytest.mark.asyncio
async def test_dark_edge_resume_is_anchored_to_latest_occupied_time(
    hass: HomeAssistant, freezer
) -> None:
    """With a latest_occupied_time the resumed time runs from it."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))

    hass.states.async_set(OCC, "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=100))
    left = datetime.now(UTC).isoformat()
    hass.states.async_set(OCC, "off", {"latest_occupied_time": left})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _cross_bright_then_dark(hass, freezer)  # off at +10 s, dark at +20 s
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=38))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"
    freezer.tick(timedelta(seconds=3))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_dark_edge_after_the_timeout_resumes_nothing(
    hass: HomeAssistant, freezer
) -> None:
    """A bright off whose on-period has since ended is not resumed."""
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(hass, make_light_entry(illuminance=ILLUM))

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).attributes["bright_forced_off"] is True

    freezer.tick(timedelta(seconds=60))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_dark_edge_after_a_manual_off_while_bright_resumes_nothing(
    hass: HomeAssistant, freezer
) -> None:
    """A manual off of a light that brightness already had off ends that on-period."""
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(hass, make_light_entry(illuminance=ILLUM))

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).attributes["bright_forced_off"] is True
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    assert _state(hass).attributes["bright_forced_off"] is False

    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    ("mode", "ends"),
    [
        (SCHEDULE_MODE_GATE, True),
        (SCHEDULE_MODE_GATE_SWITCH, False),
        (SCHEDULE_MODE_GATE_KEEP, False),
    ],
)
async def test_gate_window_end_while_bright_off_ends_the_on_period(
    hass: HomeAssistant, freezer, mode: str, ends: bool
) -> None:
    """A Gate and turn off schedule end ends an on-period that going bright cut
    short, so a later dark edge does not light an empty room; Switch and Keep
    state carry it past the end."""
    hass.states.async_set(ILLUM, "off")
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(OCC, "off")
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC,
            illuminance=ILLUM,
            schedule=SCHED,
            schedule_mode=mode,
            timeout=300,
        ),
    )
    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(ILLUM, "on")  # going bright forces it off
    await settle(hass)
    hass.states.async_set(
        OCC, "off", {"latest_occupied_time": datetime.now(UTC).isoformat()}
    )
    await settle(hass)
    assert _state(hass).attributes["bright_forced_off"] is True

    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).attributes["bright_forced_off"] is not ends

    # The next window, still bright: someone walks past, and then dusk comes.
    freezer.tick(timedelta(hours=20))
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(
        OCC, "off", {"latest_occupied_time": datetime.now(UTC).isoformat()}
    )
    await settle(hass)
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).state == ("off" if ends else "on")


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    "mode", [SCHEDULE_MODE_GATE, SCHEDULE_MODE_GATE_SWITCH, SCHEDULE_MODE_GATE_KEEP]
)
async def test_dark_edge_outside_a_gate_window_resumes_a_bright_off(
    hass: HomeAssistant, freezer, mode: str
) -> None:
    """Going dark outside a gate window still resumes an on-period that
    going bright cut short: the gate blocks presence from lighting an off room, not
    an on-period that already exists from continuing."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(SCHED, "off")  # outside the window
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_light_entry(
            illuminance=ILLUM, schedule=SCHED, schedule_mode=mode, timeout=300
        ),
    )

    # Manual control is never gated.
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE
    freezer.tick(timedelta(seconds=100))
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["bright_forced_off"] is True

    freezer.tick(timedelta(seconds=100))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["bright_forced_off"] is False
    await _assert_resumed_for(hass, freezer, 100)


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    ("mode", "held"),
    [
        (SCHEDULE_MODE_GATE, False),
        (SCHEDULE_MODE_GATE_SWITCH, True),
        (SCHEDULE_MODE_GATE_KEEP, True),
    ],
)
async def test_dark_edge_outside_a_gate_window_resumes_for_presence_as_the_mode_allows(
    hass: HomeAssistant, freezer, mode: str, held: bool
) -> None:
    """Presence lights no off room outside a gate window, but once the resume
    has the light on, Switch and Keep state let it hold the light as they do
    any on light outside the window; Gate and turn off ignores it."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(SCHED, "off")  # outside the window
    hass.states.async_set(OCC, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC,
            illuminance=ILLUM,
            schedule=SCHED,
            schedule_mode=mode,
            timeout=300,
        ),
    )

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == (
        STATE_OCCUPIED if held else STATE_ACTIVE
    )
    freezer.tick(timedelta(seconds=100))
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["bright_forced_off"] is True

    freezer.tick(timedelta(seconds=100))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    if not held:
        await _assert_resumed_for(hass, freezer, 100)
        return
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    hass.states.async_set(
        OCC, "off", {"latest_occupied_time": datetime.now(UTC).isoformat()}
    )
    await settle(hass)
    await _assert_resumed_for(hass, freezer, 300)


@pytest.mark.asyncio
async def test_dark_edge_resumes_the_timer_of_a_light_adopted_at_startup(
    hass: HomeAssistant, freezer
) -> None:
    """A light found on at startup runs a timer, which going dark resumes."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(REAL, "on")
    _real_light_reports(hass)
    await setup_entries(hass, make_light_entry(illuminance=ILLUM))
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    await _cross_bright_then_dark(hass, freezer)
    await _assert_resumed_for(hass, freezer, 40)


@pytest.mark.asyncio
async def test_dark_edge_after_a_bright_off_during_the_warning_resumes_nothing(
    hass: HomeAssistant, freezer
) -> None:
    """An effect or warn stage has no timer: the on-period was already over."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(REAL, "off")
    _real_light_reports(hass)
    entry = make_light_entry(illuminance=ILLUM, warn_timeout=30, warn_brightness=50)
    await setup_entries(hass, entry)
    await _turn_on_by(hass, "virtual")
    await _tick(hass, freezer, 61)
    assert _state(hass).attributes["molight_state"] == STATE_WARN

    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).attributes["bright_forced_off"] is True
    assert _state(hass).attributes["bright_resume_until"] is None
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_dark_edge_resume_survives_restart(hass: HomeAssistant, freezer) -> None:
    """A restart between the bright off and the dark edge keeps the resume."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(REAL, "off")
    entry = make_light_entry(illuminance=ILLUM)
    await setup_entries(hass, entry)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    ends = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
    freezer.tick(timedelta(seconds=20))
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).attributes["bright_forced_off"] is True
    assert _state(hass).attributes["bright_resume_until"] == ends

    await restart_entries(hass, entry)
    assert _state(hass).attributes["bright_forced_off"] is True
    assert _state(hass).attributes["bright_resume_until"] == ends

    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["bright_forced_off"] is False
    assert _state(hass).attributes["bright_resume_until"] is None
    await _assert_resumed_for(hass, freezer, 30)


@pytest.mark.asyncio
async def test_dark_edge_resumes_the_timer_a_raw_sensors_clear_started(
    hass: HomeAssistant, freezer
) -> None:
    """A sensor with no latest_occupied_time still resumes its timer."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))

    hass.states.async_set(OCC, "on")  # an occupancy source
    await settle(hass)
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(OCC, "off")  # T0: a 60 s timer
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _cross_bright_then_dark(hass, freezer)  # off at T0+10, dark at T0+20
    await _assert_resumed_for(hass, freezer, 40)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["dim", "recolor"])
async def test_dark_edge_resumes_a_timer_restarted_by_a_physical_change(
    hass: HomeAssistant, freezer, change: str
) -> None:
    """A physical dim or recolor restarts the timer that going dark resumes."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(REAL, "off")
    _real_light_reports(hass)
    await setup_entries(hass, make_light_entry(illuminance=ILLUM))

    hass.states.async_set(REAL, "on", {"brightness": 200, "hs_color": (30, 80)})
    await settle(hass)
    await _tick(hass, freezer, 55)
    changed = {"brightness": 150} if change == "dim" else {"hs_color": (200, 80)}
    hass.states.async_set(
        REAL, "on", {"brightness": 200, "hs_color": (30, 80)} | changed
    )  # T0: a fresh 60 s timer
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    await _cross_bright_then_dark(hass, freezer)  # off at T0+10, dark at T0+20
    await _assert_resumed_for(hass, freezer, 40)


@pytest.mark.asyncio
@pytest.mark.parametrize("hold", ["keep_on_entity", "auto_off_switch"])
@pytest.mark.parametrize("restart", [False, True], ids=["live", "restart"])
async def test_dark_edge_resumes_the_timer_a_hold_release_started(
    hass: HomeAssistant, freezer, hold: str, restart: bool
) -> None:
    """Releasing a long hold starts a fresh timer, which going dark resumes."""

    async def _hold(held: bool) -> None:
        if hold == "keep_on_entity":
            hass.states.async_set(KEEP_ON, "on" if held else "off")
        else:
            await hass.services.async_call(
                "switch", "turn_off" if held else "turn_on", {"entity_id": SWITCH}
            )
        await settle(hass)

    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(REAL, "off")
    hass.states.async_set(KEEP_ON, "off")
    _real_light_reports(hass)
    entry = make_light_entry(illuminance=ILLUM, keep_on_entities=[KEEP_ON])
    await setup_entries(hass, entry)
    await _turn_on_by(hass, "virtual")
    await _hold(True)
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"
    await _hold(False)  # T0: a fresh 60 s timer
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    if restart:
        await restart_entries(hass, entry)
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    await _assert_resumed_for(hass, freezer, 40)


@pytest.mark.asyncio
async def test_dark_edge_resumes_a_maintain_occupancy_sensors_timer(
    hass: HomeAssistant, freezer
) -> None:
    """The timer that a maintain occupancy sensor's clear started is resumed like
    any."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(MAINT, "off")
    hass.states.async_set(REAL, "off")
    _real_light_reports(hass)
    await setup_entries(hass, make_light_entry(maintain=MAINT, illuminance=ILLUM))
    await _turn_on_by(hass, "virtual")
    hass.states.async_set(MAINT, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 300)
    left = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    hass.states.async_set(MAINT, "off", {"latest_occupied_time": left})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _cross_bright_then_dark(hass, freezer)  # 30 s after they left
    await _assert_resumed_for(hass, freezer, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("sensor", ["occupancy", "maintain"])
@pytest.mark.parametrize("away", [10, 100], ids=["just_left", "long_gone"])
async def test_dark_edge_after_occupancy_cleared_while_bright(
    hass: HomeAssistant, freezer, sensor: str, away: int
) -> None:
    """Presence held the light with no timer: when they left decides."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(OCC, "off")
    hass.states.async_set(REAL, "off")
    _real_light_reports(hass)
    entry = make_light_entry(illuminance=ILLUM, **{sensor: OCC})
    await setup_entries(hass, entry)
    if sensor == "maintain":
        await _turn_on_by(hass, "virtual")
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 300)
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    freezer.tick(timedelta(seconds=10))
    left = datetime.now(UTC).isoformat()
    hass.states.async_set(OCC, "off", {"latest_occupied_time": left})
    await settle(hass)
    freezer.tick(timedelta(seconds=away))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)

    if away < 60:
        await _assert_resumed_for(hass, freezer, 60 - away)
    else:
        assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("sensor", ["occupancy", "maintain", "door"])
async def test_dark_edge_resumes_a_held_light_for_a_timeout_from_the_bright_off(
    hass: HomeAssistant, freezer, sensor: str
) -> None:
    """A sensor that cannot date the departure: they were there at the off."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(OCC, "off")
    hass.states.async_set(REAL, "off")
    _real_light_reports(hass)
    mode = {"door_mode": DOOR_MODE_OPEN_CLOSE} if sensor == "door" else {}
    entry = make_light_entry(illuminance=ILLUM, **{sensor: OCC}, **mode)
    await setup_entries(hass, entry)
    if sensor == "maintain":
        await _turn_on_by(hass, "virtual")
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 300)
    hass.states.async_set(ILLUM, "on")  # T0
    await settle(hass)
    assert _state(hass).state == "off"
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(OCC, "off")
    await settle(hass)
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)

    await _assert_resumed_for(hass, freezer, 40)


@pytest.mark.asyncio
async def test_dark_edge_resumes_a_manual_timer_despite_an_old_visit(
    hass: HomeAssistant, freezer
) -> None:
    """An old visit does not end the timer of a light turned on since."""
    visit = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(OCC, "off", {"latest_occupied_time": visit})
    hass.states.async_set(REAL, "off")
    _real_light_reports(hass)
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    await _turn_on_by(hass, "virtual")

    await _cross_bright_then_dark(hass, freezer)
    await _assert_resumed_for(hass, freezer, 40)


@pytest.mark.asyncio
async def test_dark_edge_resume_lasts_until_a_timeout_after_a_later_visit(
    hass: HomeAssistant, freezer
) -> None:
    """Someone passing through while it was bright extends the resume."""
    hass.states.async_set(ILLUM, "off")  # dark
    hass.states.async_set(OCC, "off")
    hass.states.async_set(REAL, "off")
    _real_light_reports(hass)
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    await _turn_on_by(hass, "virtual")  # its timer finishes in 60 s

    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(OCC, "on")  # gated: it is bright
    await settle(hass)
    assert _state(hass).state == "off"
    freezer.tick(timedelta(seconds=20))
    left = datetime.now(UTC).isoformat()
    hass.states.async_set(OCC, "off", {"latest_occupied_time": left})
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)

    await _assert_resumed_for(hass, freezer, 55)


# ---------------------------------------------------------------------------
# Turn-on attribution: each trigger records its own timestamp, and only that
# ---------------------------------------------------------------------------

DOOR = "binary_sensor.door"
MAINT = "binary_sensor.maint"
KEEP_ON = "input_boolean.keep_on"
SWITCH = "switch.matrix_light_auto_off"
LAST_ON_KEYS = (
    "last_on_physical",
    "last_on_virtual",
    "last_on_occupancy",
    "last_on_illuminance",
    "last_on_door",
)


async def _turn_on_by(hass: HomeAssistant, trigger: str) -> None:
    if trigger == "physical":
        hass.states.async_set(REAL, "on")
    elif trigger == "virtual":
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
        )
    elif trigger == "occupancy":
        hass.states.async_set(OCC, "on")
    else:
        hass.states.async_set(DOOR, "on")
    await settle(hass)


def _assert_only_recorded(hass: HomeAssistant, recorded: dict[str, datetime]) -> None:
    attrs = _state(hass).attributes
    expected = dict.fromkeys(LAST_ON_KEYS) | {
        key: when.isoformat() for key, when in recorded.items()
    }
    assert {key: attrs[key] for key in LAST_ON_KEYS} == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["physical", "virtual", "occupancy", "door"])
async def test_turn_on_records_its_trigger(
    hass: HomeAssistant, freezer, trigger: str
) -> None:
    """A turn-on records when it happened, under its own trigger only."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(OCC, "off")
    hass.states.async_set(DOOR, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC, door=DOOR))
    _assert_only_recorded(hass, {})

    freezer.tick(timedelta(seconds=90))
    lit = datetime.now(UTC)
    await _turn_on_by(hass, trigger)
    assert _state(hass).state == "on"
    _assert_only_recorded(hass, {f"last_on_{trigger}": lit})


@pytest.mark.asyncio
@pytest.mark.parametrize("standing", ["occupancy", "door"])
async def test_dark_edge_with_presence_records_illuminance(
    hass: HomeAssistant, freezer, standing: str
) -> None:
    """Presence found at the dark edge is a turn-on caused by going dark."""
    hass.states.async_set(ILLUM, "on")  # bright
    hass.states.async_set(OCC, "off")
    hass.states.async_set(DOOR, "off")
    entry = make_light_entry(
        occupancy=OCC, door=DOOR, door_mode=DOOR_MODE_OPEN_CLOSE, illuminance=ILLUM
    )
    await setup_entries(hass, entry)
    await _turn_on_by(hass, standing)  # gated: it is bright
    assert _state(hass).state == "off"
    _assert_only_recorded(hass, {})

    freezer.tick(timedelta(seconds=90))
    lit = datetime.now(UTC)
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    _assert_only_recorded(hass, {"last_on_illuminance": lit})


@pytest.mark.asyncio
async def test_dark_edge_resume_records_illuminance(
    hass: HomeAssistant, freezer
) -> None:
    """A resumed on-period keeps its first turn-on and adds the dark edge."""
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(hass, make_light_entry(illuminance=ILLUM))
    first = datetime.now(UTC)
    await _turn_on_by(hass, "virtual")

    await _cross_bright_then_dark(hass, freezer)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN
    _assert_only_recorded(
        hass, {"last_on_virtual": first, "last_on_illuminance": datetime.now(UTC)}
    )


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("standing", ["occupancy", "door"])
async def test_gate_window_start_records_the_standing_presence(
    hass: HomeAssistant, freezer, standing: str
) -> None:
    """Presence found when a gate window starts is recorded at the start."""
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(OCC, "off")
    hass.states.async_set(DOOR, "off")
    entry = make_light_entry(
        occupancy=OCC,
        door=DOOR,
        door_mode=DOOR_MODE_OPEN_CLOSE,
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_GATE,
    )
    await setup_entries(hass, entry)
    await _turn_on_by(hass, standing)  # gated: outside the window
    assert _state(hass).state == "off"
    _assert_only_recorded(hass, {})

    freezer.tick(timedelta(seconds=90))
    lit = datetime.now(UTC)
    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    _assert_only_recorded(hass, {f"last_on_{standing}": lit})


# ---------------------------------------------------------------------------
# SCHEDULED (follow-mode window) isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
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

    # Occupancy on/off must not adopt it or start a timer.
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
@pytest.mark.regular_virtual_light_only
async def test_occupancy_can_turn_on_after_manual_off_mid_window(
    hass: HomeAssistant,
) -> None:
    """After a manual off mid-window the state is IDLE, so occupancy applies again.

    Pins current behavior: only the SCHEDULED state shields the follow window
    from occupancy: a manual off hands control back to the sensors.
    """
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(OCC, "off")
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


NEXT_MARKER = "2026-07-03T21:00:00+00:00"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_marker_change_turns_on_after_manual_off(
    hass: HomeAssistant,
) -> None:
    """A new marker on a schedule that stays on is the next window starting."""
    entry = make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(hass, entry)
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    calls = record_service_calls(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": NEXT_MARKER})
    await settle(hass)

    state = _state(hass)
    assert light_targets(calls, "turn_on") == [[REAL]]
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["schedule_window_start"] == NEXT_MARKER


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_marker_change_keeps_a_lit_light_scheduled(
    hass: HomeAssistant,
) -> None:
    """A light still on takes the new marker and is sent no command."""
    entry = make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(hass, entry)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    calls = record_service_calls(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": NEXT_MARKER})
    await settle(hass)

    state = _state(hass)
    assert light_targets(calls, "turn_on") == []
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["schedule_window_start"] == NEXT_MARKER


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    "attrs",
    [
        pytest.param(
            {"current_window_start": MARKER, "next_change": NEXT_MARKER},
            id="other-attribute",
        ),
        pytest.param({}, id="marker-removed"),
    ],
)
async def test_follow_attribute_change_without_new_marker_keeps_manual_off(
    hass: HomeAssistant, attrs: dict
) -> None:
    """Only a new marker starts a window while the schedule stays on."""
    entry = make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(hass, entry)
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )

    hass.states.async_set(SCHED, "on", attrs)
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["schedule_window_start"] == MARKER


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    "mode", [SCHEDULE_MODE_GATE, SCHEDULE_MODE_GATE_SWITCH, SCHEDULE_MODE_GATE_KEEP]
)
async def test_gate_marker_change_keeps_manual_off(
    hass: HomeAssistant, mode: str
) -> None:
    """A gate window's marker moving is no window start to re-evaluate."""
    entry = make_light_entry(occupancy=OCC, schedule=SCHED, schedule_mode=mode)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(hass, entry)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )

    hass.states.async_set(SCHED, "on", {"current_window_start": NEXT_MARKER})
    await settle(hass)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# Gate-mode schedule boundaries x machine state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("origin", ["virtual", "occupied", "countdown"])
async def test_gate_window_end_forces_off(hass: HomeAssistant, origin: str) -> None:
    """Window end turns the lights off from every running state."""
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
    )
    hass.states.async_set(SCHED, "on")
    await setup_entries(hass, entry)

    if origin == "virtual":
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

    calls = record_service_calls(hass)
    hass.states.async_set(SCHED, "off")
    await settle(hass)

    assert light_targets(calls, "turn_off") == [[REAL]]
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
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
@pytest.mark.regular_virtual_light_only
async def test_switching_gate_recalculates_from_latest_occupied_time(
    hass: HomeAssistant, freezer
) -> None:
    """Switch-state replaces a manual timer with occupancy-based remaining time."""
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE_SWITCH
    )
    hass.states.async_set(SCHED, "on")
    cleared = datetime.now(UTC) - timedelta(seconds=120)
    hass.states.async_set(OCC, "off", {"latest_occupied_time": cleared.isoformat()})
    await setup_entries(hass, entry)

    # A manual on-period has a fresh timer, but the switch-state boundary
    # replaces it with the occupancy-based finish time, already past.
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_switching_gate_adopts_active_occupancy_at_window_end(
    hass: HomeAssistant,
) -> None:
    """An already-on switch-state light remains occupied across the boundary."""
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE_SWITCH
    )
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(OCC, "on")
    await setup_entries(hass, entry)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # The still-on light can be released and re-held, but after its on-period
    # ends the inactive schedule continues to gate fresh turn-ons.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    hass.states.async_set(OCC, "off")
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("origin", ["virtual", "occupied", "countdown"])
async def test_state_preserving_gate_keeps_on_period_at_window_end(
    hass: HomeAssistant, freezer, origin: str
) -> None:
    """Gate and keep state preserves every running state and its existing timer."""
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE_KEEP
    )
    hass.states.async_set(SCHED, "on")
    await setup_entries(hass, entry)

    if origin == "virtual":
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
            # Let half the existing timer run before the boundary.
            freezer.tick(timedelta(seconds=30))
            async_fire_time_changed(hass)
            await settle(hass)

    hass.states.async_set(SCHED, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == expected_state

    if origin == "countdown":
        # The boundary must not restart the 60-second timer.
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
@pytest.mark.regular_virtual_light_only
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

    # The turn-on gate itself is unchanged for an off light.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_state_preserving_gate_adopts_occupancy_on_virtual_turn_on_outside(
    hass: HomeAssistant, freezer
) -> None:
    """A gate_keep light turned on while occupied outside is held, not timed."""
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE_KEEP
    )
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(OCC, "on")
    await setup_entries(hass, entry)
    assert _state(hass).state == "off"  # the turn-on gate still holds

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    # Occupancy clearing starts the turn-off timer.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("mode", [SCHEDULE_MODE_GATE_KEEP, SCHEDULE_MODE_GATE_SWITCH])
async def test_state_preserving_gate_adopts_open_door_after_virtual_turn_on(
    hass: HomeAssistant, freezer, mode: str
) -> None:
    """gate_keep and gate_switch gate an off light but let an open door hold
    an on light."""
    door = "binary_sensor.door"
    entry = make_light_entry(
        schedule=SCHED,
        schedule_mode=mode,
        door=door,
        door_mode=DOOR_MODE_OPEN_CLOSE,
    )
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(door, "off")
    await setup_entries(hass, entry)

    hass.states.async_set(door, "on")
    await settle(hass)
    assert _state(hass).state == "off"

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(door, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("how", ["virtual", "physical", "startup", "maintain_clear"])
async def test_gate_and_turn_off_light_on_outside_its_window_ignores_an_open_door(
    hass: HomeAssistant, freezer, how: str
) -> None:
    """Under gate, a light on outside the window is not held by a door that
    is already open, however it came to be on or to be re-evaluated."""
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(DOOR, "on")  # standing open
    hass.states.async_set(MAINT, "on" if how == "maintain_clear" else "off")
    hass.states.async_set(REAL, "on" if how == "startup" else "off")
    await setup_entries(
        hass,
        make_light_entry(
            door=DOOR,
            door_mode=DOOR_MODE_OPEN_CLOSE,
            maintain=MAINT,
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
    )
    if how == "physical":
        hass.states.async_set(REAL, "on")
    elif how != "startup":
        await _turn_on_by(hass, "virtual")
    await settle(hass)
    if how == "maintain_clear":
        assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
        hass.states.async_set(MAINT, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] in (STATE_ACTIVE, STATE_COUNTDOWN)

    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_gate_window_start_respects_illuminance(hass: HomeAssistant) -> None:
    """Window start while occupied but bright stays off; dark then lights."""
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

    # It goes dark inside the window while still occupied → lights on.
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_gate_window_start_turns_on_at_auto_on_brightness(
    hass: HomeAssistant,
) -> None:
    """A gate-mode window start re-evaluating standing occupancy is an
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

    hass.states.async_set(SCHED, "on")  # window starts with occupancy standing
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED
    assert state.attributes["brightness"] == 102


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_gate_window_start_keeps_running_state(
    hass: HomeAssistant, freezer
) -> None:
    """Window start while already ACTIVE leaves the state and timer untouched."""
    entry = make_light_entry(
        occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
    )
    hass.states.async_set(SCHED, "off")
    await setup_entries(hass, entry)

    # Virtual turn-on works outside the window (gating only applies to sensors).
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # The original 60s timer still finishes.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# Forced-off precedence with occupancy + control-illuminance + gate-schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_forced_off_beats_occupancy_with_all_three_configured(
    hass: HomeAssistant,
) -> None:
    """With occupancy + control-illuminance + gate-schedule all configured, each
    forced-off path wins over an occupied sensor.

    Companion to test_scheduled_ignores_occupancy_and_illuminance (follow mode):
    this pins the gate/control combination. Precedence while running is
    control-bright > occupancy and gate-window-end > occupancy: an occupied
    sensor never shields the light from either forced off.
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

    # Dark again while still occupied turns on again (control gate lifted).
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
async def test_virtual_turn_on_not_gated_by_bright_illuminance(
    hass: HomeAssistant, freezer
) -> None:
    """A person can turn the light on while its illuminance sensor is bright."""
    entry = make_light_entry(illuminance=ILLUM)
    hass.states.async_set(ILLUM, "on")
    await _assert_virtual_turn_on_runs_timer(hass, freezer, entry)


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_virtual_turn_on_not_gated_outside_schedule(
    hass: HomeAssistant, freezer
) -> None:
    """A person can turn a regular Virtual Light on outside its gate schedule."""
    entry = make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE)
    hass.states.async_set(SCHED, "off")
    await _assert_virtual_turn_on_runs_timer(hass, freezer, entry)


async def _assert_virtual_turn_on_runs_timer(
    hass: HomeAssistant, freezer, entry
) -> None:
    """Assert that a manually turned-on light runs its turn-off timer."""
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

    # Way past any timer: occupied lights run no timer.
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_finished_timer_defers_to_reoccupancy_in_same_iteration(
    hass: HomeAssistant, freezer
) -> None:
    """A timer finishing in the same loop iteration in which occupancy returns is a
    no-op.

    async_call_later has already fired the callback at that point, so the
    occupancy handler's _cancel_timer can't stop it; _timer_finished itself
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

    # Occupancy returns; `_timer_finished` still runs afterwards, exactly
    # as when both land in the same event-loop iteration.
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    light = hass.data["entity_components"]["light"].get_entity(VIRTUAL)
    await light._timer_finished(datetime.now(UTC))
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_occupancy_adopts_manual_light(hass: HomeAssistant, freezer) -> None:
    """Occupancy during a manual on-period suspends the timer until it clears."""
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

    # Clearing starts the timer (no latest_occupied_time → full timeout).
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=59))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("hold", ["occupancy", "maintain"])
@pytest.mark.parametrize("turn_on", ["virtual", "physical", "door"])
async def test_false_detection_clear_keeps_the_timer_of_the_users_turn_on(
    hass: HomeAssistant, freezer, turn_on: str, hold: str
) -> None:
    """A false detection must not time the person's light from an earlier visit."""
    old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    holder = OCC if hold == "occupancy" else MAINT
    hass.states.async_set(REAL, "off")
    hass.states.async_set(OCC, "off", {"latest_occupied_time": old})
    hass.states.async_set(MAINT, "off", {"latest_occupied_time": old})
    hass.states.async_set(DOOR, "off")
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC, maintain=MAINT if hold == "maintain" else None, door=DOOR
        ),
    )

    await _turn_on_by(hass, turn_on)  # T0
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE
    hass.states.async_set(holder, "on", {"latest_occupied_time": old})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # A single-blip clear leaves the visit-old latest_occupied_time as it is.
    freezer.tick(timedelta(seconds=31))
    hass.states.async_set(
        holder,
        "off",
        {"latest_occupied_time": old, "last_clear_false_detection": True},
    )
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # The person's 60 s timer from T0 still stands.
    freezer.tick(timedelta(seconds=28))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"  # T0+59
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"  # T0+61


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["brightness", "color"])
async def test_false_detection_clear_keeps_the_timer_of_a_physical_change(
    hass: HomeAssistant, freezer, change: str
) -> None:
    """A physical dim or recolor restarts the timer; a false detection keeps it."""
    old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    attrs = {"brightness": 100, "hs_color": (10.0, 50.0)}
    hass.states.async_set(REAL, "off")
    hass.states.async_set(OCC, "off", {"latest_occupied_time": old})
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    hass.states.async_set(REAL, "on", attrs)  # T0
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=40))
    changed = {"brightness": 200} if change == "brightness" else {"hs_color": (200, 50)}
    hass.states.async_set(REAL, "on", attrs | changed)  # T0+40: a fresh 60 s
    await settle(hass)
    freezer.tick(timedelta(seconds=2))
    hass.states.async_set(OCC, "on", {"latest_occupied_time": old})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    hass.states.async_set(
        OCC, "off", {"latest_occupied_time": old, "last_clear_false_detection": True}
    )
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=57))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"  # T0+99
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"  # T0+101


@pytest.mark.asyncio
@pytest.mark.parametrize("hold", ["occupancy", "maintain"])
@pytest.mark.parametrize("change", ["brightness", "color", "second_light"])
async def test_false_detection_clear_leaves_a_physically_changed_light_its_timer(
    hass: HomeAssistant, freezer, change: str, hold: str
) -> None:
    """A physical dim, recolor or turn-on while motion has the light on
    makes the on-period the person's, as the same change through this entity."""
    old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    attrs = {"brightness": 100, "hs_color": (10.0, 50.0)}
    contexts = []

    @callback
    def _capture(event) -> None:
        if event.data["domain"] == "light":
            contexts.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _capture)
    hass.states.async_set(REAL, "off")
    hass.states.async_set(REAL2, "off")
    hass.states.async_set(OCC, "off", {"latest_occupied_time": old})
    hass.states.async_set(MAINT, "off", {"latest_occupied_time": old})
    await setup_entries(
        hass,
        make_light_entry(
            lights=[REAL, REAL2],
            occupancy=OCC,
            maintain=MAINT if hold == "maintain" else None,
        ),
    )
    hass.states.async_set(OCC, "on", {"latest_occupied_time": old})  # T0
    await settle(hass)
    for member in (REAL, REAL2):
        hass.states.async_set(member, "on", attrs, context=contexts[-1])
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    assert _state(hass).attributes["last_on_physical"] is None
    if hold == "maintain":
        hass.states.async_set(MAINT, "on", {"latest_occupied_time": old})
        await settle(hass)
    if change == "second_light":
        hass.states.async_set(REAL2, "off")
        await settle(hass)

    freezer.tick(timedelta(seconds=10))
    if change == "second_light":
        hass.states.async_set(REAL2, "on", attrs)  # T0+10: a fresh 60 s
    else:
        changed = (
            {"brightness": 200} if change == "brightness" else {"hs_color": (200, 50)}
        )
        hass.states.async_set(REAL, "on", attrs | changed)
    await settle(hass)

    freezer.tick(timedelta(seconds=2))
    for sensor in (OCC, MAINT) if hold == "maintain" else (OCC,):
        hass.states.async_set(
            sensor,
            "off",
            {"latest_occupied_time": old, "last_clear_false_detection": True},
        )
        await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # Not the 5 s false-detection off: the timer from the change stands.
    freezer.tick(timedelta(seconds=57))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"  # T0+69
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"  # T0+71


@pytest.mark.asyncio
@pytest.mark.parametrize("hold", ["occupancy", "maintain"])
@pytest.mark.parametrize("lit_by", ["occupancy", "dark"])
async def test_false_blip_after_a_genuine_visit_keeps_its_timer(
    hass: HomeAssistant, freezer, lit_by: str, hold: str
) -> None:
    """Only the visit that lit the light gets the false-detection off delay: a
    blip after a genuine visit cleared keeps that visit's timer."""
    await _assert_false_blip_keeps_the_visits_timer(hass, freezer, lit_by, hold)


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("hold", ["occupancy", "maintain"])
async def test_false_blip_after_a_window_start_visit_keeps_its_timer(
    hass: HomeAssistant, freezer, hold: str
) -> None:
    """The same for a light that a gate window start lit for standing presence."""
    await _assert_false_blip_keeps_the_visits_timer(hass, freezer, "window", hold)


async def _assert_false_blip_keeps_the_visits_timer(
    hass: HomeAssistant, freezer, lit_by: str, hold: str
) -> None:
    sensors = (OCC, MAINT) if hold == "maintain" else (OCC,)
    _real_light_reports(hass)
    hass.states.async_set(REAL, "off")
    hass.states.async_set(ILLUM, "on" if lit_by == "dark" else "off")
    hass.states.async_set(SCHED, "off")
    for sensor in sensors:
        hass.states.async_set(sensor, "off")
    await setup_entries(
        hass,
        make_light_entry(
            timeout=300,
            occupancy=OCC,
            maintain=MAINT if hold == "maintain" else None,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_GATE_ONLY,
            schedule=SCHED if lit_by == "window" else None,
            schedule_mode=SCHEDULE_MODE_GATE if lit_by == "window" else None,
        ),
    )
    for sensor in sensors:
        hass.states.async_set(sensor, "on")  # T0
    await settle(hass)
    if lit_by == "dark":
        hass.states.async_set(ILLUM, "off")
    elif lit_by == "window":
        hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    freezer.tick(timedelta(seconds=120))
    left = datetime.now(UTC).isoformat()
    for sensor in sensors:  # T0+120: a genuine clear, off at T0+420
        hass.states.async_set(sensor, "off", {"latest_occupied_time": left})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _tick(hass, freezer, 60)
    for sensor in sensors:
        hass.states.async_set(sensor, "on", {"latest_occupied_time": left})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    freezer.tick(timedelta(seconds=31))
    for sensor in sensors:  # T0+211
        hass.states.async_set(
            sensor,
            "off",
            {"latest_occupied_time": left, "last_clear_false_detection": True},
        )
    await settle(hass)
    await _assert_resumed_for(hass, freezer, 209)


@pytest.mark.asyncio
@pytest.mark.parametrize("hold", ["occupancy", "maintain"])
@pytest.mark.parametrize(("delay", "off_after"), [(300, 60), (30, 30)])
async def test_false_off_delay_never_outlasts_the_turn_off_timeout(
    hass: HomeAssistant, freezer, hold: str, delay: int, off_after: int
) -> None:
    """A false detection's light goes off after the off delay, or after the
    turn-off timeout when that is shorter, held by occupancy or maintain."""
    old = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    sensors = (OCC, MAINT) if hold == "maintain" else (OCC,)
    _real_light_reports(hass)
    hass.states.async_set(REAL, "off")
    for sensor in sensors:
        hass.states.async_set(sensor, "off", {"latest_occupied_time": old})
    await setup_entries(
        hass,
        make_light_entry(
            timeout=60,
            false_off_delay=delay,
            occupancy=OCC,
            maintain=MAINT if hold == "maintain" else None,
        ),
    )
    for sensor in sensors:
        hass.states.async_set(sensor, "on", {"latest_occupied_time": old})
    await settle(hass)
    freezer.tick(timedelta(seconds=31))
    for sensor in sensors:
        hass.states.async_set(
            sensor,
            "off",
            {"latest_occupied_time": old, "last_clear_false_detection": True},
        )
    await settle(hass)
    await _assert_resumed_for(hass, freezer, off_after)


@pytest.mark.asyncio
async def test_false_detection_clear_after_genuine_maintain_presence_keeps_its_timer(
    hass: HomeAssistant, freezer
) -> None:
    """A blip lights the room, the maintain occupancy sensor then sees someone for
    real: the blip's false-detection clear no longer owns the light."""
    old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    _real_light_reports(hass)
    hass.states.async_set(REAL, "off")
    hass.states.async_set(OCC, "off", {"latest_occupied_time": old})
    hass.states.async_set(MAINT, "off", {"latest_occupied_time": old})
    await setup_entries(hass, make_light_entry(occupancy=OCC, maintain=MAINT))
    hass.states.async_set(OCC, "on", {"latest_occupied_time": old})  # T0
    await settle(hass)
    hass.states.async_set(MAINT, "on", {"latest_occupied_time": old})
    await settle(hass)

    freezer.tick(timedelta(seconds=20))
    seen = datetime.now(UTC).isoformat()
    hass.states.async_set(MAINT, "off", {"latest_occupied_time": seen})  # T0+20
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    freezer.tick(timedelta(seconds=11))
    hass.states.async_set(  # T0+31: the blip's own clear
        OCC, "off", {"latest_occupied_time": old, "last_clear_false_detection": True}
    )
    await settle(hass)
    await _assert_resumed_for(hass, freezer, 49)


@pytest.mark.asyncio
@pytest.mark.parametrize("door_mode", [DOOR_MODE_OPEN, DOOR_MODE_OPEN_CLOSE])
@pytest.mark.parametrize("when", ["occupied", "countdown"])
async def test_door_opened_after_a_false_blip_lit_the_light_keeps_its_timer(
    hass: HomeAssistant, freezer, when: str, door_mode: str
) -> None:
    """A door opened while a blip has the light on is a person: a false-detection clear
    then runs the full timeout from the opening, not the off delay."""
    old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    false_detection_clear = {
        "latest_occupied_time": old,
        "last_clear_false_detection": True,
    }
    _real_light_reports(hass)
    hass.states.async_set(REAL, "off")
    hass.states.async_set(OCC, "off", {"latest_occupied_time": old})
    hass.states.async_set(DOOR, "off")
    await setup_entries(
        hass, make_light_entry(occupancy=OCC, door=DOOR, door_mode=door_mode)
    )
    hass.states.async_set(OCC, "on", {"latest_occupied_time": old})  # T0
    await settle(hass)

    if when == "countdown":
        freezer.tick(timedelta(seconds=31))
        hass.states.async_set(OCC, "off", false_detection_clear)  # off delay armed
        await settle(hass)
        freezer.tick(timedelta(seconds=2))  # T0+33
    else:
        freezer.tick(timedelta(seconds=10))  # T0+10
    hass.states.async_set(DOOR, "on")
    await settle(hass)
    hass.states.async_set(DOOR, "off")
    await settle(hass)
    if when == "countdown":
        freezer.tick(timedelta(seconds=7))
        hass.states.async_set(OCC, "on", {"latest_occupied_time": old})  # T0+40
        await settle(hass)
        assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    freezer.tick(timedelta(seconds=31 if when == "countdown" else 21))
    # Door opened 38 / 21 s ago.
    hass.states.async_set(OCC, "off", false_detection_clear)
    await settle(hass)
    await _assert_resumed_for(hass, freezer, 22 if when == "countdown" else 39)


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
async def test_physical_off_during_occupied_goes_idle(hass: HomeAssistant) -> None:
    """Turning the real light off wins over an occupied sensor and stays off."""
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

    # The sensor is still occupied but fires no new event; the lights stay off.
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
async def test_virtual_turn_on_while_occupied_stays_occupied(
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
async def test_self_service_echo_does_not_upgrade_timer(
    hass: HomeAssistant, freezer
) -> None:
    """The real light confirming our own turn_on must not restart the timer.

    Turning back on when dark again grants only the remaining portion of the
    original on-period. When the real light's state then echoes our own
    service call, that echo must be recognized as self-caused; treating it
    as a physical turn-on would upgrade COUNTDOWN to ACTIVE with a fresh timer.
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

    # Bright forces the light off; 40s later it goes dark again, so only
    # ~20s of the original 60s on-period remain.
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    await settle(hass)
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # The real light confirms that turn-on's service call (same context).
    assert contexts
    hass.states.async_set(REAL, "on", context=contexts[-1])
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # The remaining ~20s timer still stands, not a fresh 60s timer.
    freezer.tick(timedelta(seconds=21))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_timer_falls_back_when_lot_unparsable(
    hass: HomeAssistant, freezer
) -> None:
    """An unparsable latest_occupied_time falls back to the full turn-off timeout."""
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
    """A non-brightness attribute update (battery, ...) is not a person's change."""
    await setup_entries(hass, make_light_entry())

    hass.states.async_set(REAL, "on", {"brightness": 100})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await settle(hass)
    hass.states.async_set(REAL, "on", {"brightness": 100, "battery": 42})
    await settle(hass)

    # The original 60s timer still finishes on schedule; it was not restarted
    # by the attribute update at the 30s mark.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_brightness_zero_on_one_light_keeps_running(hass: HomeAssistant) -> None:
    """Brightness 0 counts as off, but other lit lights keep us on."""
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
async def test_physical_on_at_brightness_zero_stays_off(hass: HomeAssistant) -> None:
    """A physical off→on at brightness 0 counts as off.

    The virtual light must stay off without attributing a turn-on or starting
    a timer, matching how a dim to 0, _all_lights_off and the startup seed
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

    # Brightness rising from 0 is the physical turn-on.
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
    restart the running timer; it is not a person's change."""
    hass.states.async_set(REAL, "on", {"brightness": 100})
    await setup_entries(hass, make_light_entry(lights=[REAL, REAL2]))
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await settle(hass)
    hass.states.async_set(REAL2, "on", {"brightness": 0})
    await settle(hass)
    assert _state(hass).state == "on"

    # The original 60s timer still finishes on schedule.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
