"""Tests for MoLight Virtual Remote (runtime and config flow)."""

from __future__ import annotations

import itertools
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant import config_entries
from homeassistant.const import (
    EVENT_CALL_SERVICE,
    EVENT_HOMEASSISTANT_STARTED,
    EntityCategory,
)
from homeassistant.core import CoreState, State, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
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
    STATE_ACTIVE,
    STATE_EFFECT,
)
from custom_components.molight.helpers import molight_config
from tests.conftest import settle, setup_entries
from tests.test_config_flow import _suggested_values

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Advertised event_types of the remote shapes under test. A Pico-style
# button only knows press/release; a Bilresa (Matter multi-press) button
# announces a completed single click as multi_press_1 and a double as
# multi_press_2, with initial_press/short_release as constituent noise that
# must never fire. A Lutron Caséta button re-exposed as an event entity (the
# lutron-caseta-events integration) speaks press/multi_tap.
PICO_TYPES = ["press", "release"]
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

    The runtime ignores an entity's first sighting — its state carries the
    last pre-shutdown event — so tests seed before pressing.
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
    """On a multi-press button, only multi_press_1/_2 fire — the constituent
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
    # HA's own step rounding may not be perfectly symmetric — back to ~200.
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
    """The diagnostic sensor records each executed binding — and starts
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
    nothing stale to replay — its very first press must execute.

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
    """A remote set up before HA has started only subscribes at STARTED —
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
    """The Last Action sensor deliberately forgets across restarts — a stale
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

    # Re-write the same state with an extra attribute — not a new event.
    current = hass.states.get("event.pico_on")
    hass.states.async_set(
        "event.pico_on",
        current.state,
        {**current.attributes, "battery": 50},
    )
    await settle(hass)
    assert _vlight(hass).state == "on"  # a second toggle would turn it off


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
    empty event_types list is judged — and rejected."""
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
    """A recognised click with no binding fires nothing — no call, no action."""
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
