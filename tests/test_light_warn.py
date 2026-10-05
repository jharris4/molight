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
from homeassistant.core import Context, HomeAssistant, callback
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    DOOR_MODE_OPEN_CLOSE,
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
from tests.real_entities import FadingLight, add_real

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
# Attribute invariance: the warning stages drive the real lights themselves
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
async def test_warn_brightness_below_one_percent_keeps_the_level(
    hass: HomeAssistant, freezer
) -> None:
    """A warn brightness that truncates to 0% is unset, not a blink off."""
    entry = make_light_entry(warn_timeout=20, warn_brightness=0.5)
    await setup_entries(hass, entry)
    await _turn_on_virtual(hass, brightness=200)
    calls = _record_service_calls(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_WARN
    assert state.attributes["brightness"] == 200
    assert not _real_calls(calls, "turn_off")
    assert all(
        d["service_data"].get("brightness") != 0 for d in _real_calls(calls, "turn_on")
    )


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
# Offs during the warning: manual, external, bright, gate window end
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
    assert _mstate(hass) == STATE_ACTIVE

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN


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

    # SCHEDULED runs no timer; the stage timer must be gone.
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
    timer, but keeps the dim's own brightness; the pre-warn snapshot is not
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
    freezer.tick(timedelta(seconds=59))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_ACTIVE

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT
    assert _state(hass).attributes["pre_warn_brightness"] == 120


@pytest.mark.asyncio
async def test_external_dim_mid_effect_restores_pre_warning_color(
    hass: HomeAssistant, freezer
) -> None:
    """A brightness-only re-trigger mid-warning undoes the stage recolor."""
    entry = make_light_entry(
        effect_timeout=10,
        effect_brightness=50,
        effect_rgb_color=[255, 0, 0],
        warn_timeout=15,
    )
    await setup_entries(hass, entry)

    await _turn_on_virtual(hass, brightness=200)
    hass.states.async_set(
        REAL,
        "on",
        {
            "brightness": 200,
            "supported_color_modes": ["hs"],
            "color_mode": "hs",
            "hs_color": (30.0, 40.0),
        },
    )
    await settle(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT

    calls = _record_service_calls(hass)
    # Someone dims the real light mid-effect, bringing no color of its own.
    hass.states.async_set(
        REAL,
        "on",
        {
            "brightness": 120,
            "supported_color_modes": ["hs"],
            "color_mode": "hs",
            "hs_color": (30.0, 40.0),
        },
    )
    await settle(hass)

    state = _state(hass)
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 120
    restores = [
        d
        for d in calls
        if d["domain"] == "light"
        and d["service"] == "turn_on"
        and tuple(d["service_data"].get("hs_color") or ()) == (30.0, 40.0)
    ]
    assert restores, "pre-warning color was not restored"


HS_CAPS = {"supported_color_modes": ["hs"], "color_mode": "hs"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effect_color", [None, [255, 0, 0]], ids=["plain", "recolored"]
)
async def test_colorless_warn_stage_names_a_color_only_to_undo_the_effect(
    hass: HomeAssistant, freezer, effect_color: list[int] | None
) -> None:
    """Blank keeps the color the lights already had: two real lights showing
    different colors keep them through the warn stage. Only after an effect
    stage recolored them does it name each one's own pre-warning color."""
    members = ["light.a", "light.b"]
    lit = {"brightness": 200, **HS_CAPS}
    hass.states.async_set("light.a", "on", {**lit, "hs_color": (30.0, 80.0)})
    hass.states.async_set("light.b", "on", {**lit, "hs_color": (240.0, 80.0)})
    stages = {"warn_timeout": 10, "warn_brightness": 20}
    if effect_color:
        stages |= {
            "effect_timeout": 10,
            "effect_brightness": 50,
            "effect_rgb_color": effect_color,
        }
    await setup_entries(hass, make_light_entry(lights=members, **stages))
    calls = _record_service_calls(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    if effect_color:
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await settle(hass)

    assert _mstate(hass) == STATE_WARN
    turn_ons = [
        d["service_data"]
        for d in calls
        if d["domain"] == "light" and d["service"] == "turn_on"
    ]
    stage = turn_ons[-2:] if effect_color else turn_ons[-1:]
    assert all(command["brightness"] == 51 for command in stage)
    named = [
        (
            command["entity_id"],
            {
                key: command[key]
                for key in ("hs_color", "rgb_color", "color_temp_kelvin")
                if key in command
            },
        )
        for command in stage
    ]
    assert named == (
        [
            (["light.a"], {"hs_color": [30.0, 80.0]}),
            (["light.b"], {"hs_color": [240.0, 80.0]}),
        ]
        if effect_color
        else [(members, {})]
    )


async def _enter_stage_at_200(hass: HomeAssistant, freezer, stage: str) -> list[dict]:
    """Light at 200 in a stage that dims it to 26; returns later calls."""
    entry = make_light_entry(
        effect_timeout=10 if stage == STATE_EFFECT else None,
        effect_brightness=10,
        warn_timeout=30,
        warn_brightness=10,
    )
    await setup_entries(hass, entry)
    calls = _record_service_calls(hass)
    contexts: list[Context] = []
    hass.bus.async_listen(
        EVENT_CALL_SERVICE, callback(lambda event: contexts.append(event.context))
    )
    await _turn_on_virtual(hass, brightness=200)
    hass.states.async_set(
        REAL,
        "on",
        {"brightness": 200, "hs_color": (30.0, 40.0), **HS_CAPS},
        context=contexts[-1],
    )
    await settle(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == stage
    # The real light's reply to the stage command.
    hass.states.async_set(
        REAL,
        "on",
        {"brightness": 26, "hs_color": (30.0, 40.0), **HS_CAPS},
        context=contexts[-1],
    )
    await settle(hass)
    assert _mstate(hass) == stage
    assert _state(hass).attributes["brightness"] == 26
    calls.clear()
    return calls


def _member_turn_ons(calls: list[dict]) -> list[dict]:
    return [
        d["service_data"]
        for d in calls
        if d["domain"] == "light"
        and d["service"] == "turn_on"
        and REAL in d["service_data"]["entity_id"]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [STATE_EFFECT, STATE_WARN])
async def test_external_recolor_during_warning_restores_pre_warning_brightness(
    hass: HomeAssistant, freezer, stage: str
) -> None:
    """A color-only re-trigger mid-warning undoes the stage dimming."""
    calls = await _enter_stage_at_200(hass, freezer, stage)

    hass.states.async_set(
        REAL, "on", {"brightness": 26, "hs_color": (200.0, 90.0), **HS_CAPS}
    )
    await settle(hass)

    state = _state(hass)
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 200
    assert state.attributes["hs_color"] == (200.0, 90.0)
    assert state.attributes["pre_warn_brightness"] is None
    # The user's color stands: only the brightness is sent.
    assert _member_turn_ons(calls) == [{"entity_id": [REAL], "brightness": 200}]


@pytest.mark.asyncio
async def test_external_dim_and_recolor_during_warning_restores_nothing(
    hass: HomeAssistant, freezer
) -> None:
    """A re-trigger bringing both brightness and color keeps both."""
    calls = await _enter_stage_at_200(hass, freezer, STATE_WARN)

    hass.states.async_set(
        REAL, "on", {"brightness": 120, "hs_color": (200.0, 90.0), **HS_CAPS}
    )
    await settle(hass)

    state = _state(hass)
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 120
    assert state.attributes["hs_color"] == (200.0, 90.0)
    assert _member_turn_ons(calls) == []


@pytest.mark.asyncio
async def test_external_recolor_during_warning_without_a_brightness_to_restore(
    hass: HomeAssistant, freezer
) -> None:
    """With no pre-warning brightness known, a recolor sends no command."""
    await setup_entries(hass, make_light_entry(warn_timeout=30))
    await _turn_on_virtual(hass)
    hass.states.async_set(REAL, "on", {"hs_color": (30.0, 40.0), **HS_CAPS})
    await settle(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN
    assert _state(hass).attributes["pre_warn_brightness"] is None

    calls = _record_service_calls(hass)
    hass.states.async_set(REAL, "on", {"hs_color": (200.0, 90.0), **HS_CAPS})
    await settle(hass)

    assert _mstate(hass) == STATE_ACTIVE
    assert _member_turn_ons(calls) == []


# ---------------------------------------------------------------------------
# A turn-on naming no brightness: the warning uses the level the real light
# came on at
# ---------------------------------------------------------------------------


def _record_real_contexts(hass: HomeAssistant) -> list:
    """Capture the context of every light service call aimed at the real light."""
    contexts: list = []

    @callback
    def _record(event) -> None:
        if event.data["domain"] == "light" and REAL in event.data["service_data"].get(
            "entity_id", []
        ):
            contexts.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return contexts


@pytest.mark.asyncio
async def test_warn_after_plain_turn_on_keeps_real_brightness(
    hass: HomeAssistant, freezer
) -> None:
    """A warn stage with no brightness of its own keeps the level the real
    light came on at instead of jumping to full brightness."""
    entry = make_light_entry(warn_timeout=20)
    await setup_entries(hass, entry)
    hass.states.async_set(REAL, "off")
    await settle(hass)
    contexts = _record_real_contexts(hass)
    calls = _record_service_calls(hass)

    await _turn_on_virtual(hass)
    hass.states.async_set(REAL, "on", {"brightness": 180}, context=contexts[-1])
    await settle(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN
    assert _real_calls(calls, "turn_on")[-1]["service_data"]["brightness"] == 180


@pytest.mark.asyncio
async def test_retrigger_after_plain_auto_on_restores_real_brightness(
    hass: HomeAssistant, freezer
) -> None:
    """Occupancy returning mid-warn brings back the level the real light came
    on at instead of leaving the room at the warn brightness."""
    hass.states.async_set(OCC, "off")
    entry = make_light_entry(occupancy=OCC, warn_timeout=30, warn_brightness=20)
    await setup_entries(hass, entry)
    hass.states.async_set(REAL, "off")
    await settle(hass)
    contexts = _record_real_contexts(hass)
    calls = _record_service_calls(hass)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(REAL, "on", {"brightness": 200}, context=contexts[-1])
    await settle(hass)
    hass.states.async_set(OCC, "off")
    await settle(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN
    assert _real_calls(calls, "turn_on")[-1]["service_data"]["brightness"] == 51
    hass.states.async_set(REAL, "on", {"brightness": 51}, context=contexts[-1])
    await settle(hass)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _mstate(hass) == STATE_OCCUPIED
    assert _real_calls(calls, "turn_on")[-1]["service_data"]["brightness"] == 200


# ---------------------------------------------------------------------------
# Several real lights: a change at the wall on one cancels the warning for all
# ---------------------------------------------------------------------------

GREEN, BLUE, STAGE_RED = (120.0, 100.0), (240.0, 100.0), [255, 0, 0]
WALL_KEYS = (
    "last_on_physical",
    "last_brightness_change_physical",
    "last_color_change_physical",
    "last_off_manual",
)


async def _pair_in_stage(
    hass: HomeAssistant, freezer, touched: int, **stages
) -> tuple[FadingLight, FadingLight, list[dict]]:
    """Two real lights, green at 200, taken into a stage.

    Returns the one about to be changed at the wall, the other, and the
    light commands sent from here on.
    """
    pair = [
        FadingLight(name, on=True, brightness=200, hs=GREEN)
        for name in ("real_1", "real_2")
    ]
    await add_real(hass, *pair)
    await setup_entries(
        hass, make_light_entry(lights=["light.real_1", "light.real_2"], **stages)
    )
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).attributes["warning_active"] is True
    assert _state(hass).attributes["pre_warn_brightness"] == 200
    calls = _record_service_calls(hass)
    return pair[touched], pair[1 - touched], calls


def _wall_stamps(hass: HomeAssistant) -> list[str]:
    attrs = _state(hass).attributes
    return [key for key in WALL_KEYS if attrs[key] is not None]


def _commands_to(calls: list[dict], light: FadingLight) -> list[dict]:
    """What each light command named for a real light, target left out."""
    return [
        {k: v for k, v in d["service_data"].items() if k != "entity_id"}
        for d in calls
        if d["domain"] == "light" and light.entity_id in d["service_data"]["entity_id"]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("touched", [0, 1])
async def test_wall_turn_on_during_blink_off_relights_the_other_light(
    hass: HomeAssistant, freezer, touched: int
) -> None:
    """One switch flipped back on during the blink: the other light comes
    back at its pre-warning look, not left dark for the new on-period."""
    one, other, calls = await _pair_in_stage(
        hass, freezer, touched, effect_timeout=10, effect_brightness=0
    )
    assert not one.is_on
    assert not other.is_on

    one.wall(brightness=180)
    await settle(hass)

    assert _mstate(hass) == STATE_ACTIVE
    assert _wall_stamps(hass) == ["last_on_physical"]
    assert _state(hass).attributes["brightness"] == 180
    assert (other.is_on, other.brightness, other.hs_color) == (True, 200, GREEN)
    assert one.brightness == 180
    assert _commands_to(calls, one) == []
    assert _commands_to(calls, other) == [{"brightness": 200, "hs_color": list(GREEN)}]


@pytest.mark.asyncio
@pytest.mark.parametrize("touched", [0, 1])
@pytest.mark.parametrize("stage", ["effect", "warn"])
@pytest.mark.parametrize(
    ("change", "keeps"),
    [
        ({"brightness": 150}, (150, GREEN)),
        ({"hs_color": BLUE}, (200, BLUE)),
        ({"brightness": 150, "hs_color": BLUE}, (150, BLUE)),
    ],
    ids=["dim", "recolor", "both"],
)
async def test_wall_change_during_a_stage_restores_the_other_light(
    hass: HomeAssistant, freezer, touched: int, stage: str, change: dict, keeps: tuple
) -> None:
    """The light changed at the wall keeps what was changed on it and gets
    the rest of its pre-warning look back; the other gets all of it."""
    stages = {
        f"{stage}_timeout": 30,
        f"{stage}_brightness": 10,
        f"{stage}_rgb_color": STAGE_RED,
    }
    one, other, _ = await _pair_in_stage(hass, freezer, touched, **stages)
    assert (other.brightness, other.hs_color) == (26, (0.0, 100.0))

    one.wall(**change)
    await settle(hass)

    assert _mstate(hass) == STATE_ACTIVE
    assert _wall_stamps(hass) == [
        f"last_{name}_change_physical"
        for name, key in (("brightness", "brightness"), ("color", "hs_color"))
        if key in change
    ]
    assert (other.brightness, other.hs_color) == (200, GREEN)
    assert (one.brightness, one.hs_color) == keeps
    attrs = _state(hass).attributes
    assert (attrs["brightness"], tuple(attrs["hs_color"])) == keeps
    assert attrs["warning_active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("touched", [0, 1])
async def test_wall_off_and_on_during_a_stage_restores_the_other_light(
    hass: HomeAssistant, freezer, touched: int
) -> None:
    """One light switched off at the wall leaves the warning running on the
    other; switched back on, it cancels it for both."""
    one, other, _ = await _pair_in_stage(
        hass, freezer, touched, warn_timeout=30, warn_brightness=10
    )

    one.wall(on=False)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN
    assert other.brightness == 26

    one.wall(brightness=180)
    await settle(hass)

    assert _mstate(hass) == STATE_ACTIVE
    assert (other.is_on, other.brightness) == (True, 200)
    assert one.brightness == 180


@pytest.mark.asyncio
@pytest.mark.parametrize("touched", [0, 1])
async def test_wall_change_outside_a_warning_leaves_the_other_light_alone(
    hass: HomeAssistant, freezer, touched: int
) -> None:
    """Real lights are not kept in step: only a warning's cancelling sends
    the others anything."""
    pair = [FadingLight(name, on=True, brightness=200) for name in ("real_1", "real_2")]
    await add_real(hass, *pair)
    await setup_entries(
        hass,
        make_light_entry(
            lights=["light.real_1", "light.real_2"], warn_timeout=30, warn_brightness=10
        ),
    )
    calls = _record_service_calls(hass)

    pair[touched].wall(brightness=150)
    await settle(hass)

    assert _mstate(hass) == STATE_ACTIVE
    assert _wall_stamps(hass) == ["last_brightness_change_physical"]
    assert pair[1 - touched].brightness == 200
    assert [d for d in calls if d["domain"] == "light"] == []


# ---------------------------------------------------------------------------
# Each real light keeps its own look through a warning
# ---------------------------------------------------------------------------

DOOR = "binary_sensor.door"
HOLD_SWITCH = "switch.matrix_light_auto_off"
# Green at 50, blue at 200, warm white at 120, and one off.
BEFORE = {"a": (50, GREEN), "b": (200, BLUE), "k": (120, 2700), "c": None}
MIXED_STAGES = {
    "dim": {"effect_timeout": 30, "effect_brightness": 10},
    "blink": {"effect_timeout": 30, "effect_brightness": 0},
    "colored": {
        "effect_timeout": 30,
        "effect_brightness": 10,
        "effect_rgb_color": STAGE_RED,
    },
    "warn": {"warn_timeout": 30, "warn_brightness": 10},
    "blank_warn": {"warn_timeout": 30},
}


async def _mixed_in_stage(
    hass: HomeAssistant, freezer, stage: str
) -> tuple[dict[str, FadingLight], list[dict], object]:
    """Four real lights with looks of their own, taken into a stage.

    Returns the lights by name, the calls made from the stage on, and the
    entry.
    """
    lights = {
        "a": FadingLight("a", on=True, brightness=50, hs=GREEN),
        "b": FadingLight("b", on=True, brightness=200, hs=BLUE),
        "k": FadingLight("k", on=True, brightness=120, kelvin=2700),
        "c": FadingLight("c", on=False, brightness=90, hs=GREEN),
    }
    await add_real(hass, *lights.values())
    for sensor in (OCC, MAINTAIN, DOOR):
        hass.states.async_set(sensor, "off")
    entry = make_light_entry(
        lights=[light.entity_id for light in lights.values()],
        occupancy=OCC,
        maintain=MAINTAIN,
        door=DOOR,
        door_mode=DOOR_MODE_OPEN_CLOSE,
        **MIXED_STAGES[stage],
    )
    await setup_entries(hass, entry)
    assert _shown(lights) == BEFORE
    calls = _record_service_calls(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).attributes["warning_active"] is True
    return lights, calls, entry


def _shown(lights: dict[str, FadingLight]) -> dict[str, tuple | None]:
    """What each real light shows: brightness and color, None when off (or
    at brightness 0)."""
    return {
        name: (
            light.brightness,
            tuple(light.hs_color) if light.hs_color else light.color_temp_kelvin,
        )
        if light.is_on and light.brightness
        else None
        for name, light in lights.items()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", list(MIXED_STAGES))
async def test_stage_leaves_a_real_light_that_was_off_alone(
    hass: HomeAssistant, freezer, stage: str
) -> None:
    """The stage drives the real lights that were lit; a blank warn stage
    keeps each one's own level, and the light that was off is never lit."""
    lights, calls, _ = await _mixed_in_stage(hass, freezer, stage)

    shown = _shown(lights)
    assert shown["c"] is None
    assert _commands_to(calls, lights["c"]) == []
    if stage == "blank_warn":
        assert shown == BEFORE
    elif stage == "blink":
        assert set(shown.values()) == {None}
    else:
        assert {shown[name][0] for name in ("a", "b", "k")} == {26}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", list(MIXED_STAGES))
@pytest.mark.parametrize("trigger", ["occupancy", "maintain", "door", "hold"])
async def test_retrigger_restores_each_real_lights_own_look(
    hass: HomeAssistant, freezer, stage: str, trigger: str
) -> None:
    """Presence, a door or a hold cancelling the warning gives each real
    light back its own brightness, color and power, not one shared look."""
    lights, calls, _ = await _mixed_in_stage(hass, freezer, stage)

    if trigger == "hold":
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": HOLD_SWITCH}, blocking=True
        )
    else:
        hass.states.async_set(
            {"occupancy": OCC, "maintain": MAINTAIN}.get(trigger, DOOR), "on"
        )
    await settle(hass)

    assert _state(hass).attributes["warning_active"] is False
    assert _shown(lights) == BEFORE
    assert _commands_to(calls, lights["c"]) == []
    attrs = _state(hass).attributes
    assert attrs["pre_warn_brightness"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["dim", "blink", "colored", "warn"])
@pytest.mark.parametrize("brightness", [None, 100])
async def test_turn_on_during_a_stage_restores_each_real_lights_own_look(
    hass: HomeAssistant, freezer, stage: str, brightness: int | None
) -> None:
    """A turn-on through the light gives each real light its own look where
    the command names none; the one that was off comes on as it would
    outside a warning."""
    lights, _, _ = await _mixed_in_stage(hass, freezer, stage)

    await _turn_on_virtual(hass, brightness=brightness)

    assert _mstate(hass) == STATE_ACTIVE
    if brightness is None:
        assert _shown(lights) == {**BEFORE, "c": (90, GREEN)}
    else:
        assert _shown(lights) == {
            name: (100, look[1] if look else GREEN) for name, look in BEFORE.items()
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["dim", "colored", "warn"])
async def test_wall_dim_during_a_stage_restores_the_others_own_looks(
    hass: HomeAssistant, freezer, stage: str
) -> None:
    """The real light dimmed at the wall keeps it; the others each get
    their own look back, and the one that was off stays off."""
    lights, _, _ = await _mixed_in_stage(hass, freezer, stage)

    lights["b"].wall(brightness=150)
    await settle(hass)

    assert _mstate(hass) == STATE_ACTIVE
    # Only its brightness was changed: its own color comes back too.
    assert _shown(lights) == {**BEFORE, "b": (150, BLUE)}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["dim", "blink", "warn"])
async def test_wall_turn_on_of_the_off_light_restores_the_others(
    hass: HomeAssistant, freezer, stage: str
) -> None:
    """Switching on, mid-warning, the real light that was off cancels the
    warning: it keeps what it came on at, the others get their own looks."""
    lights, _, _ = await _mixed_in_stage(hass, freezer, stage)

    lights["c"].wall(brightness=180)
    await settle(hass)

    assert _mstate(hass) == STATE_ACTIVE
    assert _shown(lights) == {**BEFORE, "c": (180, GREEN)}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["dim", "colored", "warn"])
@pytest.mark.parametrize("off", [{"on": False}, {"brightness": 0}], ids=["off", "zero"])
async def test_real_light_switched_off_mid_warning_stays_off_on_retrigger(
    hass: HomeAssistant, freezer, stage: str, off: dict
) -> None:
    """A real light switched off at the wall during the warning was not
    darkened by it: the re-trigger leaves it off."""
    lights, _, _ = await _mixed_in_stage(hass, freezer, stage)

    lights["b"].wall(**off)
    await settle(hass)
    assert _state(hass).attributes["warning_active"] is True

    hass.states.async_set(OCC, "on")
    await settle(hass)

    assert _shown(lights) == {**BEFORE, "b": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["dim", "blink"])
async def test_real_light_recovering_mid_warning_gets_the_stage_then_its_look(
    hass: HomeAssistant, freezer, stage: str
) -> None:
    """A real light back from unavailable mid-warning is sent the stage
    like the others, and its own look when the warning is cancelled."""
    lights, calls, _ = await _mixed_in_stage(hass, freezer, stage)
    hass.states.async_set("light.b", "unavailable")
    await settle(hass)
    lights["b"].wall(on=False)
    await settle(hass)

    assert _shown(lights)["b"] == ((26, BLUE) if stage == "dim" else None)
    assert _commands_to(calls, lights["c"]) == []

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _shown(lights) == BEFORE


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["dim", "colored", "warn"])
async def test_reload_mid_warning_restores_each_real_lights_own_look(
    hass: HomeAssistant, freezer, stage: str
) -> None:
    """An options reload landing mid-warning keeps each real light's own
    pre-warning look, and the one that was off stays off."""
    lights, _, entry = await _mixed_in_stage(hass, freezer, stage)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    assert _state(hass).attributes["warning_active"] is False
    assert _shown(lights) == BEFORE


@pytest.mark.asyncio
async def test_reload_mid_blink_relights_only_the_real_lights_found_lit(
    hass: HomeAssistant, freezer
) -> None:
    """A reload finding one real light lit mid blink-off restores that one
    to its own look; the ones the blink left off stay off."""
    lights, _, entry = await _mixed_in_stage(hass, freezer, "blink")
    lights["b"].wall(on=False)  # still dark, as the blink left it
    await settle(hass)
    await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    lights["k"].wall(brightness=26)
    await settle(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    assert _shown(lights) == {"a": None, "b": None, "k": (120, 2700), "c": None}
