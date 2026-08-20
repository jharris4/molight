"""Effect/warn warning-sequence tests for the MoLight Virtual Light.

test_light.py covers the core effect→warn→off lifecycle and the common
re-triggers; this file sweeps the remaining interactions of the EFFECT and
WARN states: attribute invariance while the warning drives the real lights,
forced offs (manual, external, bright, gate window end), the maintain
entity, follow-mode schedule windows taking over mid-warning, and the
pre-warn brightness restore on every kind of re-trigger.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, callback
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    ILLUMINANCE_MODE_CONTROL,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_ACTIVE,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
    STATE_WARN,
)
from tests.conftest import make_light_entry, settle, setup_entries

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

OCC = "binary_sensor.occ"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
MAINTAIN = "binary_sensor.maintain"
REAL = "light.real_1"
VIRTUAL = "light.matrix_light"
MARKER = "2026-07-02T21:00:00+00:00"

ATTR_KEYS = (
    "last_on_physical",
    "last_on_virtual",
    "last_on_occupancy",
    "last_on_illuminance",
    "last_brightness_change_physical",
    "last_brightness_change_virtual",
)


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


def _mstate(hass: HomeAssistant) -> str:
    return _state(hass).attributes["molight_state"]


def _snapshot(hass: HomeAssistant) -> dict:
    attrs = _state(hass).attributes
    return {k: attrs[k] for k in ATTR_KEYS}


def _record_service_calls(hass: HomeAssistant) -> list[dict]:
    calls: list[dict] = []

    @callback
    def _record(event) -> None:
        calls.append(event.data)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return calls


def _real_calls(calls: list[dict], service: str) -> list[dict]:
    return [
        d
        for d in calls
        if d["domain"] == "light"
        and d["service"] == service
        and REAL in d["service_data"].get("entity_id", [])
    ]


async def _turn_on_virtual(hass: HomeAssistant, brightness: int | None = None) -> None:
    data: dict = {"entity_id": VIRTUAL}
    if brightness is not None:
        data["brightness"] = brightness
    await hass.services.async_call("light", "turn_on", data)
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# Attribute invariance — the warning stages drive the real lights themselves
# and must never register as external activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attributes_unchanged_through_effect_and_warn(
    hass: HomeAssistant, freezer
) -> None:
    """None of the last_on_* / last_brightness_change_* attributes may move
    while the effect and warn stages control the real lights."""
    entry = make_light_entry(
        effect_timeout=10, effect_brightness=0, warn_timeout=15, warn_brightness=100
    )
    await setup_entries(hass, entry)

    await _turn_on_virtual(hass, brightness=200)
    base = _snapshot(hass)
    assert base["last_on_virtual"] is not None
    assert base["last_brightness_change_virtual"] is not None

    # Timer expiry → EFFECT (real lights blinked off, self-caused).
    assert _state(hass).attributes["pre_warn_brightness"] is None

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT
    assert _snapshot(hass) == base
    # The snapshot is exposed while the warning shows (restart insurance).
    assert _state(hass).attributes["pre_warn_brightness"] == 200

    # EFFECT → WARN (real lights re-lit at the warn brightness, self-caused).
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN
    assert _state(hass).attributes["brightness"] == 255
    assert _state(hass).attributes["pre_warn_brightness"] == 200
    assert _snapshot(hass) == base

    # WARN → off.
    freezer.tick(timedelta(seconds=16))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"
    assert _mstate(hass) == STATE_IDLE
    assert _snapshot(hass) == base
    assert _state(hass).attributes["pre_warn_brightness"] is None


# ---------------------------------------------------------------------------
# Stage configuration variants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_effect_only_goes_straight_off_after_effect(
    hass: HomeAssistant, freezer
) -> None:
    """With the warn stage disabled the effect blink is followed by the off."""
    entry = make_light_entry(effect_timeout=10, effect_brightness=0, warn_timeout=0)
    await setup_entries(hass, entry)
    await _turn_on_virtual(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT
    assert _state(hass).state == "on"

    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"
    assert _mstate(hass) == STATE_IDLE


@pytest.mark.asyncio
async def test_effect_dim_stage_commands_stage_brightness(
    hass: HomeAssistant, freezer
) -> None:
    """A non-zero effect brightness dips the real lights instead of blinking
    them off (50% → 128 of 255)."""
    entry = make_light_entry(effect_timeout=10, effect_brightness=50, warn_timeout=0)
    await setup_entries(hass, entry)
    await _turn_on_virtual(hass, brightness=200)
    calls = _record_service_calls(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_EFFECT
    assert state.attributes["brightness"] == 128
    dim = _real_calls(calls, "turn_on")
    assert dim
    assert dim[-1]["service_data"]["brightness"] == 128


@pytest.mark.asyncio
async def test_warn_defaults_to_full_brightness_without_history(
    hass: HomeAssistant, freezer
) -> None:
    """No warn brightness configured and no brightness ever recorded → the
    warn stage falls back to full brightness."""
    entry = make_light_entry(warn_timeout=20)
    await setup_entries(hass, entry)

    # Adopt a real light that reports no brightness at all.
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _mstate(hass) == STATE_ACTIVE

    calls = _record_service_calls(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN
    assert _real_calls(calls, "turn_on")[-1]["service_data"]["brightness"] == 255


# ---------------------------------------------------------------------------
# Offs during the warning — manual, external, bright, gate window end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_off_during_effect_goes_idle(hass: HomeAssistant, freezer) -> None:
    entry = make_light_entry(effect_timeout=30, effect_brightness=0, warn_timeout=30)
    await setup_entries(hass, entry)
    await _turn_on_virtual(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT

    calls = _record_service_calls(hass)
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert _real_calls(calls, "turn_off")

    # No leftover stage timer revives anything.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_external_off_during_warn_goes_idle(hass: HomeAssistant, freezer) -> None:
    """The real light being switched off externally mid-warn ends everything."""
    entry = make_light_entry(warn_timeout=30, warn_brightness=100)
    await setup_entries(hass, entry)
    hass.states.async_set(REAL, "on", {"brightness": 200})
    await settle(hass)
    assert _mstate(hass) == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN

    hass.states.async_set(REAL, "off")
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE

    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_bright_forces_off_during_warn(hass: HomeAssistant, freezer) -> None:
    """Illuminance turning bright (control mode) wins over the warn grace."""
    entry = make_light_entry(
        illuminance=ILLUM,
        illuminance_mode=ILLUMINANCE_MODE_CONTROL,
        warn_timeout=30,
        warn_brightness=100,
    )
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(hass, entry)
    await _turn_on_virtual(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN

    calls = _record_service_calls(hass)
    hass.states.async_set(ILLUM, "on")  # bright
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert _real_calls(calls, "turn_off")


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_gate_window_end_during_warn_forces_off(
    hass: HomeAssistant, freezer
) -> None:
    entry = make_light_entry(
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_GATE,
        warn_timeout=30,
        warn_brightness=100,
    )
    hass.states.async_set(SCHED, "on")
    await setup_entries(hass, entry)
    await _turn_on_virtual(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN

    hass.states.async_set(SCHED, "off")
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


# ---------------------------------------------------------------------------
# Re-triggers during the warning restore the pre-warn brightness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_occupancy_retrigger_during_effect_restores_brightness(
    hass: HomeAssistant, freezer
) -> None:
    """Occupancy returning during the effect blink re-lights the room at the
    brightness it had before the warning began."""
    entry = make_light_entry(
        occupancy=OCC, effect_timeout=30, effect_brightness=0, warn_timeout=30
    )
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, entry)
    await _turn_on_virtual(hass, brightness=200)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT

    calls = _record_service_calls(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED
    assert state.attributes["brightness"] == 200
    resume = _real_calls(calls, "turn_on")
    assert resume
    assert resume[-1]["service_data"]["brightness"] == 200


@pytest.mark.asyncio
async def test_manual_on_during_warn_restores_pre_warn_brightness(
    hass: HomeAssistant, freezer
) -> None:
    """A virtual turn-on with no explicit brightness mid-warning restores the
    pre-warning brightness instead of freezing the warn brightness in place."""
    entry = make_light_entry(warn_timeout=30, warn_brightness=100)
    await setup_entries(hass, entry)
    await _turn_on_virtual(hass, brightness=200)
    base_change = _state(hass).attributes["last_brightness_change_virtual"]

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN
    assert _state(hass).attributes["brightness"] == 255

    calls = _record_service_calls(hass)
    await _turn_on_virtual(hass)  # no brightness requested
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 200
    assert _real_calls(calls, "turn_on")[-1]["service_data"]["brightness"] == 200
    # The restore is not a user brightness change.
    assert state.attributes["last_brightness_change_virtual"] == base_change

    # A fresh full timer runs from the re-trigger.
    freezer.tick(timedelta(seconds=59))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_maintain_on_during_warn_resumes_and_occupies(
    hass: HomeAssistant, freezer
) -> None:
    """The maintain entity turning on mid-warn adopts the light as OCCUPIED
    and undoes the warn stage."""
    entry = make_light_entry(maintain=MAINTAIN, warn_timeout=30, warn_brightness=100)
    hass.states.async_set(MAINTAIN, "off")
    await setup_entries(hass, entry)
    await _turn_on_virtual(hass, brightness=200)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN

    calls = _record_service_calls(hass)
    hass.states.async_set(MAINTAIN, "on")
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED
    assert state.attributes["brightness"] == 200
    assert _real_calls(calls, "turn_on")[-1]["service_data"]["brightness"] == 200

    # OCCUPIED runs no timer.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


# ---------------------------------------------------------------------------
# Follow-mode schedule windows taking over mid-warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_window_start_during_effect_restores_lights(
    hass: HomeAssistant, freezer
) -> None:
    """A follow window starting while the effect has the real lights blinked
    off must re-light them for the window (regression: they stayed off for
    the whole window because the virtual light was still logically on)."""
    entry = make_light_entry(
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_FOLLOW,
        effect_timeout=30,
        effect_brightness=0,
        warn_timeout=30,
    )
    hass.states.async_set(SCHED, "off")
    await setup_entries(hass, entry)
    await _turn_on_virtual(hass, brightness=200)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT

    calls = _record_service_calls(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["schedule_window_start"] == MARKER
    assert state.attributes["brightness"] == 200
    resume = _real_calls(calls, "turn_on")
    assert resume, "window start must re-light the blinked-off real lights"
    assert resume[-1]["service_data"]["brightness"] == 200

    # SCHEDULED runs no timer — the stage timer must be gone.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_schedule_recovery_same_marker_during_warn_restores_lights(
    hass: HomeAssistant, freezer
) -> None:
    """The schedule entity recovering mid-window while the light reached the
    warn stage re-adopts the light as SCHEDULED and undoes the warn dim."""
    entry = make_light_entry(
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_FOLLOW,
        warn_timeout=30,
        warn_brightness=100,
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(hass, entry)
    await settle(hass)
    assert _mstate(hass) == STATE_SCHEDULED

    # Manual off mid-window is respected; the applied marker is retained.
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _mstate(hass) == STATE_IDLE

    # The schedule blips unavailable, then someone lights the room physically:
    # with the window unreadable the light runs a normal ACTIVE timer.
    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    hass.states.async_set(REAL, "on", {"brightness": 200})
    await settle(hass)
    assert _mstate(hass) == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN

    calls = _record_service_calls(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["brightness"] == 200
    assert _real_calls(calls, "turn_on")[-1]["service_data"]["brightness"] == 200

    # No timer while SCHEDULED.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_external_dim_during_warning_honors_new_brightness(
    hass: HomeAssistant, freezer
) -> None:
    """An external dim mid-warning cancels the sequence and restarts the full
    timer, but keeps the dim's own brightness — the pre-warn snapshot is not
    restored over the user's explicit choice."""
    entry = make_light_entry(effect_timeout=10, effect_brightness=50, warn_timeout=15)
    await setup_entries(hass, entry)

    await _turn_on_virtual(hass, brightness=200)
    hass.states.async_set(REAL, "on", {"brightness": 200})
    await settle(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT
    assert _state(hass).attributes["pre_warn_brightness"] == 200

    # Someone dims the real light mid-effect.
    hass.states.async_set(REAL, "on", {"brightness": 120})
    await settle(hass)

    state = _state(hass)
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 120
    assert state.attributes["pre_warn_brightness"] is None
    assert state.attributes["last_brightness_change_physical"] is not None

    # The full timer restarted: the warning comes back around, snapshotting
    # the dimmed brightness this time.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT
    assert _state(hass).attributes["pre_warn_brightness"] == 120
