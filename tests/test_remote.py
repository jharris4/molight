"""Tests for MoLight Virtual Remote (runtime and config flow)."""

from __future__ import annotations

import asyncio
import itertools
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant import config_entries
from homeassistant.components.event import EventEntity
from homeassistant.const import (
    EVENT_CALL_SERVICE,
    EVENT_HOMEASSISTANT_STARTED,
    EntityCategory,
)
from homeassistant.core import CoreState, State, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.config_flow import (
    COLOR_MODE_NONE,
    COLOR_MODE_TEMP,
    CONF_PRESET_1_COLOR_MODE,
)
from custom_components.molight.const import (
    CONF_BRIGHTNESS_DOWN_BUTTONS_SINGLE,
    CONF_BRIGHTNESS_UP_BUTTONS_SINGLE,
    CONF_DIM_STEP,
    CONF_ENTITY_TYPE,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OFF_BUTTONS_DOUBLE,
    CONF_OFF_BUTTONS_SINGLE,
    CONF_ON_BUTTONS_DOUBLE,
    CONF_ON_BUTTONS_SINGLE,
    CONF_PRESET_1_BRIGHTNESS,
    CONF_PRESET_1_BUTTONS_SINGLE,
    CONF_PRESET_1_COLOR_TEMP,
    CONF_PRESET_1_RGB_COLOR,
    CONF_PRESET_2_BRIGHTNESS,
    CONF_PRESET_2_BUTTONS_SINGLE,
    CONF_TARGET_LIGHTS,
    CONF_TOGGLE_BUTTONS_SINGLE,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_REMOTE,
    REMOTE_ACTION_BRIGHTNESS_UP,
    REMOTE_ACTION_FIELDS,
    REMOTE_ACTION_OFF,
    REMOTE_ACTION_ON,
    REMOTE_ACTION_PRESET_1,
    REMOTE_ACTION_PRESET_2,
    REMOTE_ACTION_TOGGLE,
    STATE_ACTIVE,
    STATE_EFFECT,
    STATE_WARN,
)
from custom_components.molight.helpers import molight_config
from tests.conftest import make_light_entry, settle, setup_entries
from tests.test_config_flow import _suggested_values
from tests.test_light_selection import _SlowSelect

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Advertised event_types of the remote shapes under test. A Pico-style
# button only knows press/release; a Bilresa (Matter multi-press) button
# announces a completed single click as multi_press_1 and a double as
# multi_press_2, with initial_press/short_release as constituent noise that
# must never fire. A Lutron Caséta button re-exposed as an event entity (the
# lutron-caseta-events integration) speaks press/multi_tap.
PICO_TYPES = ["press", "release"]
# Hue's default button vocabulary; a Hue tap switch announces only
# initial_press.
HUE_TYPES = ["initial_press", "repeat", "short_release", "long_press", "long_release"]
HUE_TAP_TYPES = ["initial_press"]
LUTRON_EVENT_TYPES = ["press", "release", "multi_tap", "long_press"]
BILRESA_TYPES = [
    "initial_press",
    "short_release",
    "long_press",
    "long_release",
    "multi_press_1",
    "multi_press_2",
    "multi_press_complete",
]

# Event entity states are timestamps that change on every button event.
_ts_counter = itertools.count()


def _fire(
    hass: HomeAssistant, entity_id: str, event_type: str, event_types: list[str]
) -> None:
    """Write a new button event onto an event entity's state."""
    n = next(_ts_counter)
    hass.states.async_set(
        entity_id,
        f"2026-07-27T10:{n // 60:02d}:{n % 60:02d}+00:00",
        {"event_type": event_type, "event_types": event_types},
    )


def _seed(hass: HomeAssistant, entity_id: str, event_types: list[str]) -> None:
    """Give an event entity an initial (restored-looking) state.

    The runtime ignores an entity's first sighting (its state carries the
    last pre-shutdown event), so tests seed before pressing.
    """
    _fire(hass, entity_id, event_types[0], event_types)


def _remote_entry(**config) -> MockConfigEntry:
    data = {
        CONF_ENTITY_TYPE: ENTITY_TYPE_REMOTE,
        CONF_NAME: "Test Remote",
        CONF_TARGET_LIGHTS: ["light.test_light"],
        CONF_DIM_STEP: 10,
        **config,
    }
    return MockConfigEntry(domain=DOMAIN, data=data)


def _record_service_calls(hass: HomeAssistant) -> list[dict]:
    calls: list[dict] = []

    @callback
    def _record(event) -> None:
        calls.append(event.data)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return calls


def _vlight(hass: HomeAssistant):
    return hass.states.get("light.test_light")


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pico_single_click_on_off(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A Pico's press turns the virtual light on/off; its release is ignored."""
    remote = _remote_entry(
        **{
            CONF_ON_BUTTONS_SINGLE: ["event.pico_on"],
            CONF_OFF_BUTTONS_SINGLE: ["event.pico_off"],
        }
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_on", PICO_TYPES)
    _seed(hass, "event.pico_off", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)
    assert _vlight(hass).state == "off"

    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"

    _fire(hass, "event.pico_on", "release", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"

    _fire(hass, "event.pico_off", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "off"


@pytest.mark.asyncio
async def test_bilresa_single_vs_double_click(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """On a multi-press button, only multi_press_1/_2 fire; the constituent
    initial_press/short_release of the same physical click never do."""
    remote = _remote_entry(
        **{
            CONF_ON_BUTTONS_DOUBLE: ["event.bilresa_1"],
            CONF_TOGGLE_BUTTONS_SINGLE: ["event.bilresa_1"],
        }
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.bilresa_1", BILRESA_TYPES)
    await setup_entries(hass, light_entry, remote)

    # The double click's constituent events do nothing on their own...
    _fire(hass, "event.bilresa_1", "initial_press", BILRESA_TYPES)
    _fire(hass, "event.bilresa_1", "short_release", BILRESA_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "off"

    # ...only the completed double click fires the turn-on.
    _fire(hass, "event.bilresa_1", "multi_press_2", BILRESA_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"

    # A completed single click on the same button toggles the light back off.
    _fire(hass, "event.bilresa_1", "multi_press_1", BILRESA_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "off"


@pytest.mark.asyncio
async def test_lutron_event_entity_single_vs_double_click(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A Lutron button surfaced as an event entity (lutron-caseta-events)
    resolves press as the single click and multi_tap as the double; its
    release/long_press never fire."""
    remote = _remote_entry(
        **{
            CONF_TOGGLE_BUTTONS_SINGLE: ["event.closet_pico_on"],
            CONF_ON_BUTTONS_DOUBLE: ["event.closet_pico_on"],
        }
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.closet_pico_on", LUTRON_EVENT_TYPES)
    await setup_entries(hass, light_entry, remote)

    _fire(hass, "event.closet_pico_on", "press", LUTRON_EVENT_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"

    _fire(hass, "event.closet_pico_on", "release", LUTRON_EVENT_TYPES)
    _fire(hass, "event.closet_pico_on", "long_press", LUTRON_EVENT_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"

    _fire(hass, "event.closet_pico_on", "press", LUTRON_EVENT_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "off"

    _fire(hass, "event.closet_pico_on", "multi_tap", LUTRON_EVENT_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"


# Device-resolved vocabularies: (event_types, single, double, events that
# must never fire: constituents, long, held and triple presses).
DEVICE_RESOLVED_VOCABULARIES = {
    "homekit": (
        ["single_press", "double_press", "long_press"],
        "single_press",
        "double_press",
        ["long_press"],
    ),
    "shelly_rpc": (
        [
            "btn_down",
            "btn_up",
            "single_push",
            "double_push",
            "triple_push",
            "long_push",
        ],
        "single_push",
        "double_push",
        ["btn_down", "btn_up", "triple_push", "long_push"],
    ),
    "zwave_central_scene": (
        ["KeyHeldDown", "KeyPressed", "KeyPressed2x", "KeyPressed3x", "KeyReleased"],
        "KeyPressed",
        "KeyPressed2x",
        ["KeyHeldDown", "KeyReleased", "KeyPressed3x"],
    ),
    "bthome": (
        [
            "press",
            "double_press",
            "triple_press",
            "long_press",
            "long_double_press",
            "long_triple_press",
            "hold_press",
        ],
        "press",
        "double_press",
        [
            "triple_press",
            "long_press",
            "long_double_press",
            "long_triple_press",
            "hold_press",
        ],
    ),
    "xiaomi_ble": (
        ["press", "double_press", "long_press"],
        "press",
        "double_press",
        ["long_press"],
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("vocabulary", list(DEVICE_RESOLVED_VOCABULARIES))
async def test_device_resolved_click_vocabularies(
    hass: HomeAssistant, light_entry: MockConfigEntry, vocabulary: str
) -> None:
    """single_press/double_press and their siblings each fire their binding
    exactly once; constituent, long, held and triple presses fire nothing."""
    types, single, double, noise = DEVICE_RESOLVED_VOCABULARIES[vocabulary]
    remote = _remote_entry(
        **{
            CONF_TOGGLE_BUTTONS_SINGLE: ["event.button"],
            CONF_OFF_BUTTONS_DOUBLE: ["event.button"],
        }
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.button", types)
    await setup_entries(hass, light_entry, remote)
    calls = _record_service_calls(hass)

    def _actions() -> list[str]:
        return [
            c["service"]
            for c in calls
            if c["service_data"].get("entity_id") == ["light.test_light"]
        ]

    for event_type in noise:
        _fire(hass, "event.button", event_type, types)
        await settle(hass)
    assert _actions() == []

    _fire(hass, "event.button", single, types)
    await settle(hass)
    assert _actions() == ["toggle"]
    assert _vlight(hass).state == "on"

    _fire(hass, "event.button", double, types)
    await settle(hass)
    assert _actions() == ["toggle", "turn_off"]
    assert _vlight(hass).state == "off"
    last_action = hass.states.get("sensor.test_remote_last_action")
    assert last_action.attributes["click"] == "double"
    assert last_action.attributes["event_type"] == double


@pytest.mark.asyncio
async def test_native_lutron_keypad_single_press(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A native lutron keypad button advertises only single_press."""
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.keypad_button"]})
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.keypad_button", ["single_press"])
    await setup_entries(hass, light_entry, remote)

    _fire(hass, "event.keypad_button", "single_press", ["single_press"])
    await settle(hass)
    assert _vlight(hass).state == "on"


@pytest.mark.asyncio
async def test_hue_style_click_fires_on_short_release_only(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A Hue-style button fires on short_release, not on the initial_press
    that also starts a long press; a tap switch falls back to initial_press."""
    remote = _remote_entry(
        **{
            CONF_TOGGLE_BUTTONS_SINGLE: ["event.hue_1"],
            CONF_ON_BUTTONS_SINGLE: ["event.hue_tap"],
        }
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.hue_1", HUE_TYPES)
    _seed(hass, "event.hue_tap", HUE_TAP_TYPES)
    await setup_entries(hass, light_entry, remote)
    calls = _record_service_calls(hass)

    def _actions() -> list[str]:
        return [
            c["service"]
            for c in calls
            if c["service_data"].get("entity_id") == ["light.test_light"]
        ]

    for event_type in ("initial_press", "repeat", "long_press", "long_release"):
        _fire(hass, "event.hue_1", event_type, HUE_TYPES)
        await settle(hass)
    assert _actions() == []

    for event_type in ("initial_press", "short_release"):
        _fire(hass, "event.hue_1", event_type, HUE_TYPES)
        await settle(hass)
    assert _actions() == ["toggle"]
    assert _vlight(hass).state == "on"

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.test_light"}, blocking=True
    )
    await settle(hass)
    calls.clear()
    _fire(hass, "event.hue_tap", "initial_press", HUE_TAP_TYPES)
    await settle(hass)
    assert _actions() == ["turn_on"]
    assert _vlight(hass).state == "on"


@pytest.mark.asyncio
async def test_first_sighting_and_unavailable_never_fire(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A button's first (restored) state and its recovery from unavailable
    both carry a stale event that must never be replayed as a press."""
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    hass.states.async_set("light.living_room", "off")
    await setup_entries(hass, light_entry, remote)

    # First sighting after startup: the state IS a press, but a stale one.
    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "off"

    # Recovery from unavailable is equally suspect.
    hass.states.async_set("event.pico_on", "unavailable")
    await settle(hass)
    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "off"

    # The next real press fires.
    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"


@pytest.mark.asyncio
async def test_brightness_steps_add_up_while_the_turn_on_selection_waits(
    hass: HomeAssistant,
) -> None:
    """A second raise click while the first one's turn-on still waits for its
    selection builds on that click's level instead of replacing it."""
    select = _SlowSelect(hass)
    hass.states.async_set("select.scene", "Day")
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_raise", PICO_TYPES)
    light = make_light_entry(
        name="Test Light",
        lights=["light.living_room"],
        turn_on_select_entity="select.scene",
        turn_on_select_option="Night",
    )
    remote = _remote_entry(**{CONF_BRIGHTNESS_UP_BUTTONS_SINGLE: ["event.pico_raise"]})
    await setup_entries(hass, light, remote)
    calls = _record_service_calls(hass)

    _fire(hass, "event.pico_raise", "press", PICO_TYPES)
    await asyncio.wait_for(select.started.wait(), 2)
    assert _vlight(hass).state == "on"
    assert _vlight(hass).attributes["brightness"] == 26  # 10 %
    _fire(hass, "event.pico_raise", "press", PICO_TYPES)
    for _ in range(4):  # settle() would wait for the select call
        await asyncio.sleep(0)
    assert _vlight(hass).attributes["brightness"] == 51  # 20 %
    select.release.set()
    await settle(hass)

    assert _vlight(hass).state == "on"
    assert _vlight(hass).attributes["brightness"] == 51
    assert [
        d["service_data"]["brightness"]
        for d in calls
        if d["domain"] == "light"
        and d["service_data"].get("entity_id") == ["light.living_room"]
    ] == [51]


@pytest.mark.asyncio
async def test_brightness_step_up_and_down(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Dim buttons step the brightness; up from off turns on dim, and
    stepping below the minimum turns the light off."""
    remote = _remote_entry(
        **{
            CONF_BRIGHTNESS_UP_BUTTONS_SINGLE: ["event.pico_raise"],
            CONF_BRIGHTNESS_DOWN_BUTTONS_SINGLE: ["event.pico_lower"],
        }
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_raise", PICO_TYPES)
    _seed(hass, "event.pico_lower", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)
    calls = _record_service_calls(hass)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.test_light", "brightness": 200}
    )
    await settle(hass)
    assert _vlight(hass).attributes["brightness"] == 200

    _fire(hass, "event.pico_raise", "press", PICO_TYPES)
    await settle(hass)
    raised = _vlight(hass).attributes["brightness"]
    assert raised > 200
    step_calls = [
        d
        for d in calls
        if d["domain"] == "light" and "brightness_step_pct" in d["service_data"]
    ]
    assert step_calls
    assert step_calls[-1]["service_data"]["brightness_step_pct"] == 10

    _fire(hass, "event.pico_lower", "press", PICO_TYPES)
    await settle(hass)
    # HA's own step rounding may not be perfectly symmetric: back to ~200.
    assert abs(_vlight(hass).attributes["brightness"] - 200) <= 1

    # Down from near-minimum turns the light off (HA's own step handling).
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.test_light", "brightness": 10}
    )
    await settle(hass)
    _fire(hass, "event.pico_lower", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "off"

    # Up from off turns the light on dim.
    _fire(hass, "event.pico_raise", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"
    assert _vlight(hass).attributes["brightness"] <= 26


# Stage settings for a brightness step mid-warning: (light kwargs, the stage
# state, the level the light reports during it).
WARNING_STAGES = {
    "warn": ({"warn_timeout": 30, "warn_brightness": 10}, STATE_WARN, 26),
    "effect": ({"effect_timeout": 10, "effect_brightness": 30}, STATE_EFFECT, 76),
    "blink_effect": (
        {"effect_timeout": 10, "effect_brightness": 0},
        STATE_EFFECT,
        None,
    ),
}


async def _step_mid_warning(
    hass: HomeAssistant,
    freezer,
    stage: str,
    before: int,
    dim_step: int,
    button: str,
    extra_targets: list[str] | None = None,
) -> list[dict]:
    """Bring light.matrix_light into a stage from `before`, press `button`, and
    return the remote's light calls."""
    light_kwargs, stage_state, stage_level = WARNING_STAGES[stage]
    hass.states.async_set("light.real_1", "off")
    remote = _remote_entry(
        **{
            CONF_TARGET_LIGHTS: ["light.matrix_light", *(extra_targets or [])],
            CONF_DIM_STEP: dim_step,
            CONF_BRIGHTNESS_UP_BUTTONS_SINGLE: ["event.pico_raise"],
            CONF_BRIGHTNESS_DOWN_BUTTONS_SINGLE: ["event.pico_lower"],
        }
    )
    _seed(hass, "event.pico_raise", PICO_TYPES)
    _seed(hass, "event.pico_lower", PICO_TYPES)
    await setup_entries(hass, make_light_entry(timeout=60, **light_kwargs), remote)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.matrix_light", "brightness": before},
        blocking=True,
    )
    await settle(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    state = hass.states.get("light.matrix_light")
    assert state.attributes["molight_state"] == stage_state
    assert state.attributes["warning_active"] is True
    assert state.attributes["pre_warn_brightness"] == before
    if stage_level is not None:
        # The light still reports its stage level; only the step changes.
        assert state.attributes["brightness"] == stage_level

    calls = _record_service_calls(hass)
    _fire(hass, button, "press", PICO_TYPES)
    await settle(hass)
    return [
        {**c["service_data"], "service": c["service"]}
        for c in calls
        if c["domain"] == "light"
        and c["service_data"].get("entity_id") != ["light.real_1"]
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("stage", list(WARNING_STAGES))
@pytest.mark.parametrize(
    ("button", "before", "dim_step", "expected_pct", "expected_brightness"),
    [
        ("event.pico_raise", 200, 10, 88, 224),
        ("event.pico_lower", 200, 10, 68, 173),
        ("event.pico_raise", 250, 10, 100, 255),
        ("event.pico_lower", 77, 50, 1, 3),
    ],
    ids=["up", "down", "up-clamped-100", "down-clamped-1"],
)
async def test_brightness_step_mid_warning_steps_from_pre_warning_level(
    hass: HomeAssistant,
    freezer,
    stage: str,
    button: str,
    before: int,
    dim_step: int,
    expected_pct: int,
    expected_brightness: int,
) -> None:
    """A remote's brightness step during the effect or warn stage steps from
    the pre-warning brightness, clamped to 1 to 100 %, never turning the light
    off, and restarts the full timer like any other press."""
    calls = await _step_mid_warning(hass, freezer, stage, before, dim_step, button)

    assert calls == [
        {
            "entity_id": ["light.matrix_light"],
            "brightness_pct": expected_pct,
            "service": "turn_on",
        }
    ]
    state = hass.states.get("light.matrix_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == expected_brightness
    assert state.attributes["warning_active"] is False

    freezer.tick(timedelta(seconds=59))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.matrix_light").attributes["molight_state"] == (
        STATE_ACTIVE
    )


@pytest.mark.asyncio
async def test_brightness_step_mid_warning_leaves_other_targets_stepping(
    hass: HomeAssistant, freezer
) -> None:
    """Only the MoLight light mid-warning gets a fixed level: another light,
    even one carrying look-alike attributes, still takes the plain step."""
    hass.states.async_set(
        "light.other",
        "on",
        {"brightness": 26, "warning_active": True, "pre_warn_brightness": 200},
    )
    calls = await _step_mid_warning(
        hass,
        freezer,
        "warn",
        200,
        10,
        "event.pico_raise",
        extra_targets=["light.other", "light.missing"],
    )

    assert sorted(calls, key=lambda c: c["entity_id"]) == [
        {
            "entity_id": ["light.matrix_light"],
            "brightness_pct": 88,
            "service": "turn_on",
        },
        {
            "entity_id": ["light.other", "light.missing"],
            "brightness_step_pct": 10,
            "service": "turn_on",
        },
    ]


@pytest.mark.asyncio
async def test_preset_turns_on_with_values(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A preset press turns the lights on at its brightness/color."""
    remote = _remote_entry(
        **{
            CONF_PRESET_1_BUTTONS_SINGLE: ["event.pico_fav"],
            CONF_PRESET_1_BRIGHTNESS: 60,
            CONF_PRESET_1_COLOR_TEMP: 2700,
        }
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_fav", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)
    calls = _record_service_calls(hass)

    _fire(hass, "event.pico_fav", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"
    # 60% -> 153/255; the color temp is filtered out by HA for this
    # brightness-only virtual light, but the remote's call carries it.
    assert _vlight(hass).attributes["brightness"] == 153
    preset_calls = [
        d
        for d in calls
        if d["domain"] == "light" and "brightness_pct" in d["service_data"]
    ]
    assert preset_calls
    assert preset_calls[-1]["service_data"]["brightness_pct"] == 60
    assert preset_calls[-1]["service_data"]["color_temp_kelvin"] == 2700


@pytest.mark.asyncio
async def test_press_cancels_off_warning(hass: HomeAssistant, freezer) -> None:
    """A bound press mid effect-stage acts as manual control: it cancels the
    warning and the light returns to a fresh ACTIVE period."""
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Test Light",
            CONF_LIGHTS: ["light.living_room"],
            CONF_LIGHT_TIMEOUT: 60,
            "effect_timeout": 10,
            "effect_brightness": 0,
        },
    )
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_on", PICO_TYPES)
    await setup_entries(hass, light, remote)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.test_light", "brightness": 200}
    )
    await settle(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _vlight(hass).attributes["molight_state"] == STATE_EFFECT

    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    state = _vlight(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 200  # pre-warning brightness restored


@pytest.mark.asyncio
async def test_unload_stops_listening(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Unloading the remote entry tears down its button subscription."""
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_on", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)

    assert await hass.config_entries.async_unload(remote.entry_id)
    await settle(hass)

    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "off"


@pytest.mark.asyncio
async def test_removing_light_strips_remote_target(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Deleting a virtual light removes it from a remote's target list."""
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    hass.states.async_set("light.living_room", "off")
    await setup_entries(hass, light_entry, remote)

    await hass.config_entries.async_remove(light_entry.entry_id)
    await settle(hass)

    assert molight_config(remote).get(CONF_TARGET_LIGHTS, []) == []


@pytest.mark.asyncio
async def test_last_action_sensor(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """The diagnostic sensor records each executed binding, and starts
    unknown, since a pre-restart action shown as current would mislead."""
    remote = _remote_entry(
        **{
            CONF_ON_BUTTONS_SINGLE: ["event.pico_on"],
            CONF_BRIGHTNESS_UP_BUTTONS_SINGLE: ["event.pico_raise"],
        }
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_on", PICO_TYPES)
    _seed(hass, "event.pico_raise", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)

    sensor = hass.states.get("sensor.test_remote_last_action")
    assert sensor is not None
    assert sensor.state == "unknown"

    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    sensor = hass.states.get("sensor.test_remote_last_action")
    assert sensor.state == REMOTE_ACTION_ON
    assert sensor.attributes["button"] == "event.pico_on"
    assert sensor.attributes["click"] == "single"
    assert sensor.attributes["event_type"] == "press"
    assert sensor.attributes["time"]

    # An ignored event (release) leaves the sensor untouched...
    _fire(hass, "event.pico_on", "release", PICO_TYPES)
    await settle(hass)
    assert hass.states.get("sensor.test_remote_last_action").state == REMOTE_ACTION_ON

    # ...and the next executed binding overwrites it.
    _fire(hass, "event.pico_raise", "press", PICO_TYPES)
    await settle(hass)
    sensor = hass.states.get("sensor.test_remote_last_action")
    assert sensor.state == REMOTE_ACTION_BRIGHTNESS_UP
    assert sensor.attributes["button"] == "event.pico_raise"


@pytest.mark.asyncio
async def test_first_press_of_a_brand_new_button_fires(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A freshly paired button (state "unknown", never fired an event) has
    nothing stale to replay; its very first press must execute.

    Regression: the guard used to swallow any transition out of "unknown",
    so the first press of every new button did nothing.
    """
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    hass.states.async_set("light.living_room", "off")
    hass.states.async_set("event.pico_on", "unknown", {"event_types": PICO_TYPES})
    await setup_entries(hass, light_entry, remote)

    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"


@pytest.mark.asyncio
async def test_remote_added_before_startup_defers_subscription(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A remote set up before HA has started only subscribes at STARTED;
    events replayed during startup must never fire a binding."""
    hass.set_state(CoreState.not_running)
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_on", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)

    # Startup noise: a state change before STARTED is not even subscribed to.
    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "off"

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)

    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"


@pytest.mark.asyncio
async def test_remote_without_targets_is_inert(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Buttons with no target lights never call a service (e.g. after the
    last target light was deleted and stripped from the entry)."""
    remote = _remote_entry(
        **{CONF_TARGET_LIGHTS: [], CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]}
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_on", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)
    calls = _record_service_calls(hass)

    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    assert not [d for d in calls if d["domain"] == "light"]
    assert hass.states.get("sensor.test_remote_last_action").state == "unknown"


@pytest.mark.asyncio
async def test_last_action_sensor_is_not_restored(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """The Last Action sensor deliberately forgets across restarts: a stale
    pre-restart action shown as current would read as recent activity."""
    mock_restore_cache(
        hass,
        [
            State(
                "sensor.test_remote_last_action",
                REMOTE_ACTION_ON,
                {"button": "event.pico_on", "click": "single"},
            )
        ],
    )
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    await setup_entries(hass, light_entry, remote)

    sensor = hass.states.get("sensor.test_remote_last_action")
    assert sensor.state == "unknown"
    assert "button" not in sensor.attributes
    assert "click" not in sensor.attributes


@pytest.mark.asyncio
async def test_last_action_sensor_is_diagnostic(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """The sensor is registered as a diagnostic entity."""
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    await setup_entries(hass, light_entry, remote)

    reg_entry = er.async_get(hass).async_get("sensor.test_remote_last_action")
    assert reg_entry is not None
    assert reg_entry.entity_category is EntityCategory.DIAGNOSTIC


@pytest.mark.asyncio
async def test_preset_with_rgb_color(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """An RGB preset carries rgb_color (and never a color temp)."""
    remote = _remote_entry(
        **{
            CONF_PRESET_1_BUTTONS_SINGLE: ["event.pico_fav"],
            CONF_PRESET_1_BRIGHTNESS: 40,
            CONF_PRESET_1_RGB_COLOR: [255, 0, 0],
        }
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_fav", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)
    calls = _record_service_calls(hass)

    _fire(hass, "event.pico_fav", "press", PICO_TYPES)
    await settle(hass)
    preset_calls = [
        d
        for d in calls
        if d["domain"] == "light" and "brightness_pct" in d["service_data"]
    ]
    assert preset_calls
    data = preset_calls[-1]["service_data"]
    assert data["brightness_pct"] == 40
    assert data["rgb_color"] == [255, 0, 0]
    assert "color_temp_kelvin" not in data


@pytest.mark.asyncio
async def test_preset_without_values_stays_inert(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A preset binding with no values (possible on a hand-built entry; the
    flow rejects it) registers no binding at all."""
    remote = _remote_entry(**{CONF_PRESET_1_BUTTONS_SINGLE: ["event.pico_fav"]})
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_fav", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)
    calls = _record_service_calls(hass)

    _fire(hass, "event.pico_fav", "press", PICO_TYPES)
    await settle(hass)
    assert not [d for d in calls if d["domain"] == "light"]
    assert hass.states.get("sensor.test_remote_last_action").state == "unknown"


@pytest.mark.asyncio
async def test_preset_2_binding_fires(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """preset_2 resolves its own value keys (a mis-keyed const table would
    silently cross the presets)."""
    remote = _remote_entry(
        **{
            CONF_PRESET_2_BUTTONS_SINGLE: ["event.pico_fav2"],
            CONF_PRESET_2_BRIGHTNESS: 25,
        }
    )
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_fav2", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)
    calls = _record_service_calls(hass)

    _fire(hass, "event.pico_fav2", "press", PICO_TYPES)
    await settle(hass)
    preset_calls = [
        d
        for d in calls
        if d["domain"] == "light" and "brightness_pct" in d["service_data"]
    ]
    assert preset_calls
    assert preset_calls[-1]["service_data"]["brightness_pct"] == 25
    sensor = hass.states.get("sensor.test_remote_last_action")
    assert sensor.state == REMOTE_ACTION_PRESET_2


@pytest.mark.asyncio
async def test_attribute_only_write_never_refires(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A same-state write (battery/attribute update) is not a new press."""
    remote = _remote_entry(**{CONF_TOGGLE_BUTTONS_SINGLE: ["event.pico_on"]})
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_on", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)

    _fire(hass, "event.pico_on", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"

    # Re-write the same state with an extra attribute: not a new event.
    current = hass.states.get("event.pico_on")
    hass.states.async_set(
        "event.pico_on",
        current.state,
        {**current.attributes, "battery": 50},
    )
    await settle(hass)
    assert _vlight(hass).state == "on"  # a second toggle would turn it off


@pytest.mark.asyncio
async def test_event_sharing_a_timestamp_with_the_previous_one_still_fires(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Before HA 2026.8 two events in the same millisecond share a state: a
    new event_type is still a new event, the same one is not."""
    types = ["initial_press", "short_release", "long_press", "long_release"]
    remote = _remote_entry(**{CONF_TOGGLE_BUTTONS_SINGLE: ["event.matter"]})
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.matter", types)
    await setup_entries(hass, light_entry, remote)
    stamp = "2026-07-27T11:00:00.000+00:00"

    hass.states.async_set(
        "event.matter", stamp, {"event_type": "initial_press", "event_types": types}
    )
    hass.states.async_set(
        "event.matter", stamp, {"event_type": "short_release", "event_types": types}
    )
    await settle(hass)
    assert _vlight(hass).state == "on"

    hass.states.async_set(
        "event.matter",
        stamp,
        {"event_type": "short_release", "event_types": types, "battery": 50},
    )
    await settle(hass)
    assert _vlight(hass).state == "on"  # a second toggle would turn it off


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_types", "constituent", "click", "service"),
    [
        (
            ["initial_press", "short_release", "long_press", "long_release"],
            "initial_press",
            "short_release",
            "toggle",
        ),
        (
            ["btn_down", "btn_up", "double_push", "single_push"],
            "btn_up",
            "single_push",
            "toggle",
        ),
        (
            ["btn_down", "btn_up", "double_push", "single_push"],
            "btn_up",
            "double_push",
            "turn_off",
        ),
    ],
    ids=["matter-momentary", "shelly-single", "shelly-double"],
)
async def test_click_in_the_same_millisecond_as_a_constituent_fires(
    hass: HomeAssistant,
    light_entry: MockConfigEntry,
    freezer,
    event_types: list[str],
    constituent: str,
    click: str,
    service: str,
) -> None:
    """A real event entity firing a constituent and the click in one
    millisecond, which share a state before HA 2026.8, still runs the click."""
    hass.states.async_set("light.living_room", "on")
    await setup_entries(hass, light_entry)
    button = await _add_native_button(hass, event_types)
    sections = {REMOTE_ACTION_TOGGLE: {CONF_TOGGLE_BUTTONS_SINGLE: [button.entity_id]}}
    if "double_push" in event_types:
        sections[REMOTE_ACTION_OFF] = {CONF_OFF_BUTTONS_DOUBLE: [button.entity_id]}
    result = await _create_remote_through_flow(hass, sections)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await settle(hass)
    calls = _record_service_calls(hass)

    button.press(constituent)
    button.press(click)
    await settle(hass)
    assert [
        c["service"]
        for c in calls
        if c["service_data"].get("entity_id") == ["light.test_light"]
    ] == [service]


@pytest.mark.asyncio
async def test_options_rebind_takes_effect_live(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """After an options edit the old binding is dead and the new one live
    (the runtime builds its bindings once per setup, so this proves the
    reload rebuilt them)."""
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_a"]})
    hass.states.async_set("light.living_room", "off")
    _seed(hass, "event.pico_a", PICO_TYPES)
    _seed(hass, "event.pico_b", PICO_TYPES)
    await setup_entries(hass, light_entry, remote)

    result = await hass.config_entries.options.async_init(remote.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Remote",
            CONF_TARGET_LIGHTS: ["light.test_light"],
            CONF_DIM_STEP: 10,
            **EMPTY_REMOTE_SECTIONS,
            REMOTE_ACTION_ON: {CONF_ON_BUTTONS_SINGLE: ["event.pico_b"]},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await settle(hass)

    _fire(hass, "event.pico_a", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "off"  # old binding is gone

    _fire(hass, "event.pico_b", "press", PICO_TYPES)
    await settle(hass)
    assert _vlight(hass).state == "on"  # new binding is live


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

# The frontend always submits every action section, even collapsed ones.
EMPTY_REMOTE_SECTIONS = {action: {} for _s, _d, action in REMOTE_ACTION_FIELDS}


async def _start_remote_create(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "create"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_REMOTE}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "remote"
    return result


@pytest.mark.asyncio
async def test_create_remote_flow(hass: HomeAssistant) -> None:
    """The create flow stores bound slots flat and drops empty ones."""
    result = await _start_remote_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Closet Remote",
            CONF_TARGET_LIGHTS: ["light.test_light"],
            CONF_DIM_STEP: 15,
            **EMPTY_REMOTE_SECTIONS,
            REMOTE_ACTION_ON: {CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]},
            REMOTE_ACTION_OFF: {CONF_OFF_BUTTONS_SINGLE: ["event.pico_off"]},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_ENTITY_TYPE] == ENTITY_TYPE_REMOTE
    assert data[CONF_TARGET_LIGHTS] == ["light.test_light"]
    assert data[CONF_DIM_STEP] == 15
    assert data[CONF_ON_BUTTONS_SINGLE] == ["event.pico_on"]
    assert data[CONF_OFF_BUTTONS_SINGLE] == ["event.pico_off"]
    # Unbound slots are absent, not stored as [].
    assert CONF_TOGGLE_BUTTONS_SINGLE not in data
    assert CONF_ON_BUTTONS_DOUBLE not in data


@pytest.mark.asyncio
async def test_remote_flow_validation(hass: HomeAssistant) -> None:
    """The remote form rejects incoherent bindings, one rule at a time."""
    # A Pico-shaped button proves the double-click support check.
    _seed(hass, "event.pico_on", PICO_TYPES)
    base = {
        CONF_NAME: "Bad Remote",
        CONF_TARGET_LIGHTS: ["light.test_light"],
        CONF_DIM_STEP: 10,
        **EMPTY_REMOTE_SECTIONS,
    }
    cases = [
        (
            {**base, CONF_TARGET_LIGHTS: []},
            {"target_lights": "target_lights_required"},
        ),
        ({**base}, {"base": "buttons_required"}),
        (
            {
                **base,
                REMOTE_ACTION_ON: {CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]},
                REMOTE_ACTION_OFF: {CONF_OFF_BUTTONS_SINGLE: ["event.pico_on"]},
            },
            {"base": "button_click_conflict"},
        ),
        (
            {**base, REMOTE_ACTION_ON: {CONF_ON_BUTTONS_DOUBLE: ["event.pico_on"]}},
            {"base": "double_click_unsupported"},
        ),
        (
            {
                **base,
                REMOTE_ACTION_PRESET_1: {
                    CONF_PRESET_1_BUTTONS_SINGLE: ["event.pico_on"]
                },
            },
            {"base": "preset_values_required"},
        ),
        (
            {
                **base,
                REMOTE_ACTION_PRESET_1: {
                    CONF_PRESET_1_BUTTONS_SINGLE: ["event.pico_on"],
                    CONF_PRESET_1_BRIGHTNESS: 60,
                    CONF_PRESET_1_COLOR_TEMP: 2700,
                    CONF_PRESET_1_RGB_COLOR: [255, 0, 0],
                },
            },
            {"base": "preset_color_conflict"},
        ),
    ]
    result = await _start_remote_create(hass)
    for user_input, expected_errors in cases:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == expected_errors


@pytest.mark.asyncio
async def test_double_click_binding_allowed_when_capable_or_unknown(
    hass: HomeAssistant,
) -> None:
    """A double-click binding is accepted on a button that advertises one,
    and on a button with no state yet (it can't be judged, so it is allowed
    and simply never fires until the entity proves itself). An advertised
    empty event_types list is judged, and rejected."""
    _seed(hass, "event.bilresa", BILRESA_TYPES)
    hass.states.async_set("event.no_types", "unknown", {"event_types": []})
    base = {
        CONF_TARGET_LIGHTS: ["light.test_light"],
        CONF_DIM_STEP: 10,
        **EMPTY_REMOTE_SECTIONS,
    }

    # Advertised empty event_types → judged unsupported.
    result = await _start_remote_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **base,
            CONF_NAME: "No Types Remote",
            REMOTE_ACTION_ON: {CONF_ON_BUTTONS_DOUBLE: ["event.no_types"]},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "double_click_unsupported"}

    # Multi-press capable → accepted.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **base,
            CONF_NAME: "Bilresa Remote",
            REMOTE_ACTION_ON: {CONF_ON_BUTTONS_DOUBLE: ["event.bilresa"]},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    # No state at all → can't be judged, accepted.
    result = await _start_remote_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **base,
            CONF_NAME: "Stateless Remote",
            REMOTE_ACTION_ON: {CONF_ON_BUTTONS_DOUBLE: ["event.never_seen"]},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("click", "event_types", "error"),
    [
        ("single", ["rotate_left", "rotate_right"], "single_click_unsupported"),
        ("single", ["ring", "double_press", "long_press"], "single_click_unsupported"),
        ("single", ["upper", "lower", "released"], "single_click_unsupported"),
        ("single", [], "single_click_unsupported"),
        ("single", ["single_press", "double_press", "long_press"], None),
        ("single", ["single_press"], None),
        ("single", ["btn_down", "btn_up", "single_push", "double_push"], None),
        ("single", ["KeyPressed", "KeyReleased"], None),
        ("single", None, None),
        ("double", ["single_press"], "double_click_unsupported"),
        ("double", ["single_press", "double_press", "long_press"], None),
        ("double", ["press", "double_press", "long_press"], None),
        ("double", ["btn_down", "btn_up", "single_push", "double_push"], None),
        ("double", ["KeyPressed", "KeyPressed2x"], None),
        ("double", None, None),
    ],
)
async def test_click_binding_judged_on_advertised_vocabulary(
    hass: HomeAssistant,
    click: str,
    event_types: list[str] | None,
    error: str | None,
) -> None:
    """A binding is rejected only when the button advertises event types and
    none of them is that click. A button with no event_types (a state but
    no capability yet) or no state at all can't be judged and is allowed."""
    attributes = {} if event_types is None else {"event_types": event_types}
    hass.states.async_set("event.button", "unknown", attributes)
    key = CONF_ON_BUTTONS_SINGLE if click == "single" else CONF_ON_BUTTONS_DOUBLE
    user_input = {
        CONF_NAME: "Vocab Remote",
        CONF_TARGET_LIGHTS: ["light.test_light"],
        CONF_DIM_STEP: 10,
        **EMPTY_REMOTE_SECTIONS,
        REMOTE_ACTION_ON: {key: ["event.button", "event.never_seen"]},
    }
    result = await _start_remote_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    if error is None:
        assert result["type"] == FlowResultType.CREATE_ENTRY
    else:
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": error}


@pytest.mark.asyncio
async def test_options_reject_a_single_click_on_a_button_without_one(
    hass: HomeAssistant,
) -> None:
    """Configure judges single clicks too: a rotary dial can't be bound."""
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    _seed(hass, "event.pico_on", PICO_TYPES)
    hass.states.async_set(
        "event.dial", "unknown", {"event_types": ["rotate_left", "rotate_right"]}
    )
    await setup_entries(hass, remote)

    result = await hass.config_entries.options.async_init(remote.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Remote",
            CONF_TARGET_LIGHTS: ["light.test_light"],
            CONF_DIM_STEP: 10,
            **EMPTY_REMOTE_SECTIONS,
            REMOTE_ACTION_ON: {CONF_ON_BUTTONS_SINGLE: ["event.pico_on", "event.dial"]},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "single_click_unsupported"}


@pytest.mark.asyncio
async def test_remote_options_flow(hass: HomeAssistant) -> None:
    """Editing a remote replaces its stored config wholesale."""
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    await setup_entries(hass, remote)

    result = await hass.config_entries.options.async_init(remote.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "remote"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Renamed Remote",
            CONF_TARGET_LIGHTS: ["light.other_light"],
            CONF_DIM_STEP: 20,
            **EMPTY_REMOTE_SECTIONS,
            REMOTE_ACTION_OFF: {CONF_OFF_BUTTONS_SINGLE: ["event.pico_off"]},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await settle(hass)

    cfg = molight_config(remote)
    assert cfg[CONF_NAME] == "Renamed Remote"
    assert cfg[CONF_TARGET_LIGHTS] == ["light.other_light"]
    assert cfg[CONF_DIM_STEP] == 20
    assert cfg[CONF_OFF_BUTTONS_SINGLE] == ["event.pico_off"]
    # The original on-binding was cleared by the edit, not merged back in.
    assert CONF_ON_BUTTONS_SINGLE not in cfg
    assert remote.title == "Renamed Remote"


@pytest.mark.asyncio
async def test_remote_flow_color_mode_picks_one_of_a_pair(
    hass: HomeAssistant,
) -> None:
    """A submitted preset color mode resolves a temp+rgb double submission
    instead of the preset_color_conflict error."""
    _seed(hass, "event.pico_on", PICO_TYPES)
    result = await _start_remote_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Closet Remote",
            CONF_TARGET_LIGHTS: ["light.test_light"],
            CONF_DIM_STEP: 15,
            **EMPTY_REMOTE_SECTIONS,
            REMOTE_ACTION_PRESET_1: {
                CONF_PRESET_1_BUTTONS_SINGLE: ["event.pico_on"],
                CONF_PRESET_1_COLOR_MODE: COLOR_MODE_TEMP,
                CONF_PRESET_1_COLOR_TEMP: 2700,
                CONF_PRESET_1_RGB_COLOR: [255, 0, 0],
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_PRESET_1_COLOR_TEMP] == 2700
    assert CONF_PRESET_1_RGB_COLOR not in data
    assert CONF_PRESET_1_COLOR_MODE not in data


@pytest.mark.asyncio
async def test_remote_options_clears_a_set_preset_color(
    hass: HomeAssistant,
) -> None:
    """The "None" color mode removes a stored preset color, which the bare
    color selectors can't clear; the dropdown itself prefills from the store."""
    remote = _remote_entry(
        **{
            CONF_PRESET_1_BUTTONS_SINGLE: ["event.pico_on"],
            CONF_PRESET_1_BRIGHTNESS: 60,
            CONF_PRESET_1_COLOR_TEMP: 2700,
        }
    )
    await setup_entries(hass, remote)

    result = await hass.config_entries.options.async_init(remote.entry_id)
    suggested = _suggested_values(result["data_schema"])
    assert (
        suggested[REMOTE_ACTION_PRESET_1][CONF_PRESET_1_COLOR_MODE] == COLOR_MODE_TEMP
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Remote",
            CONF_TARGET_LIGHTS: ["light.test_light"],
            CONF_DIM_STEP: 10,
            **EMPTY_REMOTE_SECTIONS,
            REMOTE_ACTION_PRESET_1: {
                CONF_PRESET_1_BUTTONS_SINGLE: ["event.pico_on"],
                CONF_PRESET_1_BRIGHTNESS: 60,
                CONF_PRESET_1_COLOR_MODE: COLOR_MODE_NONE,
                CONF_PRESET_1_COLOR_TEMP: 2700,
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await settle(hass)

    cfg = molight_config(remote)
    assert CONF_PRESET_1_COLOR_TEMP not in cfg
    assert cfg[CONF_PRESET_1_BRIGHTNESS] == 60


@pytest.mark.asyncio
async def test_unbound_click_is_ignored(hass: HomeAssistant) -> None:
    """A recognised click with no binding fires nothing: no call, no action."""
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    await setup_entries(hass, remote)
    _seed(hass, "event.pico_on", LUTRON_EVENT_TYPES)
    calls = _record_service_calls(hass)

    # A double click on a button bound only for single clicks.
    _fire(hass, "event.pico_on", "multi_tap", LUTRON_EVENT_TYPES)
    await settle(hass)

    assert [c for c in calls if c["domain"] == "light"] == []
    assert hass.states.get("sensor.test_remote_last_action").state == "unknown"


@pytest.mark.asyncio
async def test_remote_options_reject_missing_targets(hass: HomeAssistant) -> None:
    """Clearing the target lights re-renders the options form with the error."""
    remote = _remote_entry(**{CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]})
    await setup_entries(hass, remote)

    result = await hass.config_entries.options.async_init(remote.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Remote",
            CONF_TARGET_LIGHTS: [],
            CONF_DIM_STEP: 10,
            **EMPTY_REMOTE_SECTIONS,
            REMOTE_ACTION_ON: {CONF_ON_BUTTONS_SINGLE: ["event.pico_on"]},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "remote"
    assert result["errors"] == {CONF_TARGET_LIGHTS: "target_lights_required"}


# ---------------------------------------------------------------------------
# Native integration vocabularies
# ---------------------------------------------------------------------------

# Each button's advertised event_types as Home Assistant 2026.9's own
# integrations build them (the components' event.py unless noted), with the
# single and double click MoLight must resolve (None: the button has none)
# and the events that must never fire a binding.
NATIVE_VOCABULARIES = {
    # matter: multi-press switch with long press.
    "matter_multi_press": (
        ["multi_press_1", "multi_press_2", "long_press", "long_release"],
        "multi_press_1",
        "multi_press_2",
        ["long_press", "long_release"],
    ),
    # matter: momentary switch with release and long press, no multi-press.
    "matter_momentary": (
        ["initial_press", "short_release", "long_press", "long_release"],
        "short_release",
        None,
        ["initial_press", "long_press", "long_release"],
    ),
    # hue: DEFAULT_BUTTON_EVENT_TYPES in hue/const.py.
    "hue": (HUE_TYPES, "short_release", None, ["initial_press", "repeat"]),
    # hue: the tap switch override in hue/const.py.
    "hue_tap_switch": (HUE_TAP_TYPES, "initial_press", None, []),
    "homekit_controller": (
        ["single_press", "double_press", "long_press"],
        "single_press",
        "double_press",
        ["long_press"],
    ),
    "homekit_controller_single_only": (
        ["single_press", "long_press"],
        "single_press",
        None,
        ["long_press"],
    ),
    "lutron_keypad": (["single_press"], "single_press", None, []),
    "lutron_raise_lower": (["press", "release"], "press", None, ["release"]),
    "bthome": (
        [
            "press",
            "double_press",
            "triple_press",
            "long_press",
            "long_double_press",
            "long_triple_press",
            "hold_press",
        ],
        "press",
        "double_press",
        [
            "triple_press",
            "long_press",
            "long_double_press",
            "long_triple_press",
            "hold_press",
        ],
    ),
    "xiaomi_ble_button": (
        ["press", "double_press", "long_press"],
        "press",
        "double_press",
        ["long_press"],
    ),
    "xiaomi_ble_dimmer": (
        [
            "press",
            "long_press",
            "rotate_left",
            "rotate_right",
            "rotate_left_pressed",
            "rotate_right_pressed",
        ],
        "press",
        None,
        ["long_press", "rotate_left", "rotate_right", "rotate_left_pressed"],
    ),
    # shelly: RPC_INPUTS_EVENTS_TYPES in shelly/const.py (a set).
    "shelly_rpc": (
        sorted(
            [
                "btn_down",
                "btn_up",
                "single_push",
                "double_push",
                "triple_push",
                "long_push",
            ]
        ),
        "single_push",
        "double_push",
        ["btn_down", "btn_up", "triple_push", "long_push"],
    ),
    # shelly: BLOCK_INPUTS_EVENTS_TYPES in shelly/const.py (a set).
    "shelly_block": (
        sorted(["single", "double", "triple", "long", "single_long", "long_single"]),
        "single",
        "double",
        ["triple", "long", "single_long", "long_single"],
    ),
    # zwave_js: sorted central scene states, named by zwave-js's
    # CentralSceneKeys.
    "zwave_js_central_scene": (
        ["KeyHeldDown", "KeyPressed", "KeyPressed2x", "KeyPressed3x", "KeyReleased"],
        "KeyPressed",
        "KeyPressed2x",
        ["KeyHeldDown", "KeyReleased", "KeyPressed3x"],
    ),
    "homematicip_cloud": (
        ["short_release", "long_press", "long_release"],
        "short_release",
        None,
        ["long_press", "long_release"],
    ),
    "govee_ble": (["press"], "press", None, []),
    "switchbot": (["press"], "press", None, []),
}

# Event entities with no click at all: a single-click binding is refused.
NATIVE_NON_CLICK_VOCABULARIES = {
    "bthome_dimmer": ["rotate_left", "rotate_right"],
    "bthome_command": ["off", "on", "toggle", "step_up", "step_down"],
    "xiaomi_ble_cube": ["rotate_left", "rotate_right"],
    "homekit_controller_doorbell": ["ring", "double_press", "long_press"],
    "homee_button_state": ["upper", "lower", "released"],
}


class _NativeButton(EventEntity):
    """A real event entity advertising one integration's vocabulary."""

    _attr_should_poll = False

    def __init__(self, event_types: list[str]) -> None:
        self._attr_event_types = list(event_types)
        self.entity_id = "event.native_button"

    def press(self, event_type: str) -> None:
        self._trigger_event(event_type)
        self.async_write_ha_state()


async def _add_native_button(
    hass: HomeAssistant, event_types: list[str]
) -> _NativeButton:
    assert await async_setup_component(hass, "event", {})
    button = _NativeButton(event_types)
    await hass.data["event"].async_add_entities([button])
    await settle(hass)
    return button


async def _create_remote_through_flow(hass: HomeAssistant, sections: dict) -> dict:
    result = await _start_remote_create(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Native Remote",
            CONF_TARGET_LIGHTS: ["light.test_light"],
            CONF_DIM_STEP: 10,
            **EMPTY_REMOTE_SECTIONS,
            **sections,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("integration", list(NATIVE_VOCABULARIES))
async def test_native_integration_vocabulary(
    hass: HomeAssistant, light_entry: MockConfigEntry, freezer, integration: str
) -> None:
    """Each native button shape passes setup for the clicks it has, is refused
    a double click it lacks, and fires each bound click exactly once."""
    types, single, double, noise = NATIVE_VOCABULARIES[integration]
    hass.states.async_set("light.living_room", "off")
    await setup_entries(hass, light_entry)
    button = await _add_native_button(hass, types)
    assert hass.states.get("event.native_button").attributes["event_types"] == types

    toggle = {REMOTE_ACTION_TOGGLE: {CONF_TOGGLE_BUTTONS_SINGLE: [button.entity_id]}}
    off = {REMOTE_ACTION_OFF: {CONF_OFF_BUTTONS_DOUBLE: [button.entity_id]}}
    if double is None:
        result = await _create_remote_through_flow(hass, {**toggle, **off})
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "double_click_unsupported"}
        result = await _create_remote_through_flow(hass, toggle)
    else:
        result = await _create_remote_through_flow(hass, {**toggle, **off})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await settle(hass)
    calls = _record_service_calls(hass)

    def _actions() -> list[str]:
        return [
            c["service"]
            for c in calls
            if c["service_data"].get("entity_id") == ["light.test_light"]
        ]

    async def _press(event_type: str) -> None:
        # Real presses are milliseconds apart, so each is its own state.
        freezer.tick(timedelta(milliseconds=100))
        button.press(event_type)
        await settle(hass)

    for event_type in noise:
        await _press(event_type)
    assert _actions() == []

    await _press(single)
    assert _actions() == ["toggle"]
    assert _vlight(hass).state == "on"
    last_action = hass.states.get("sensor.native_remote_last_action")
    assert last_action.attributes["click"] == "single"
    assert last_action.attributes["event_type"] == single

    if double is not None:
        await _press(double)
        assert _actions() == ["toggle", "turn_off"]
        assert _vlight(hass).state == "off"
        last_action = hass.states.get("sensor.native_remote_last_action")
        assert last_action.attributes["click"] == "double"


@pytest.mark.asyncio
@pytest.mark.parametrize("integration", list(NATIVE_NON_CLICK_VOCABULARIES))
async def test_native_non_click_entity_refuses_a_single_click(
    hass: HomeAssistant, light_entry: MockConfigEntry, integration: str
) -> None:
    """A dial, cube, command or doorbell entity has no single click to bind."""
    await setup_entries(hass, light_entry)
    button = await _add_native_button(hass, NATIVE_NON_CLICK_VOCABULARIES[integration])

    result = await _create_remote_through_flow(
        hass, {REMOTE_ACTION_ON: {CONF_ON_BUTTONS_SINGLE: [button.entity_id]}}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "single_click_unsupported"}


@pytest.mark.asyncio
@pytest.mark.parametrize("has_capabilities", [True, False])
async def test_button_of_an_unloaded_integration_is_judged_by_its_registry(
    hass: HomeAssistant, light_entry: MockConfigEntry, has_capabilities: bool
) -> None:
    """A button whose integration hasn't loaded shows Home Assistant's restored
    placeholder: its registered event_types are judged, and without them the
    capability is unknown and the binding is allowed."""
    await setup_entries(hass, light_entry)
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "event",
        "homekit_controller",
        "button-1",
        suggested_object_id="unloaded_button",
        capabilities=(
            {"event_types": ["single_press", "long_press"]}
            if has_capabilities
            else None
        ),
    )
    entry.write_unavailable_state(hass)

    result = await _create_remote_through_flow(
        hass, {REMOTE_ACTION_OFF: {CONF_OFF_BUTTONS_DOUBLE: [entry.entity_id]}}
    )
    if has_capabilities:
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "double_click_unsupported"}
    else:
        assert result["type"] == FlowResultType.CREATE_ENTRY
