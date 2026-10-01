"""Tests for a Virtual Scheduled Light resting at standby instead of off."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import (
    Context,
    Event,
    HomeAssistant,
    ServiceCall,
    State,
    callback,
)
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.const import (
    ACTIVE_SETTINGS_INSIDE,
    ACTIVE_SETTINGS_OUTSIDE,
    ATTR_ACTIVE_SETTINGS,
    ATTR_ACTIVE_SETTINGS_SCHEDULE,
    ATTR_ACTIVE_SETTINGS_WINDOW,
    ATTR_SCHEDULE_END_OFF_PENDING,
    ATTR_STANDBY_SUPPRESSED,
    CONF_AUTO_OFF_TRANSITION,
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_AUTO_ON_COLOR_TEMP,
    CONF_AUTO_ON_TRANSITION,
    CONF_DOOR_ENTITY,
    CONF_DOOR_MODE,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_COLOR_TEMP,
    CONF_EFFECT_TIMEOUT,
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_MODE,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_SCHEDULE_INPUTS,
    CONF_STANDBY_BRIGHTNESS,
    CONF_STANDBY_COLOR_TEMP,
    CONF_STANDBY_RGB_COLOR,
    CONF_TIME_WINDOWS,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_TURN_ON_SELECT_SOURCE_ENTITY,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_COLOR_TEMP,
    CONF_WARN_TIMEOUT,
    DOMAIN,
    DOOR_MODE_OPEN_CLOSE,
    ENTITY_TYPE_COMBINED_SCHEDULE,
    ENTITY_TYPE_SCHEDULE,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE,
    SCHEDULE_END_ACTION_KEEP,
    SCHEDULE_END_ACTION_SWITCH,
    SCHEDULE_END_ACTION_TURN_OFF,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_STANDBY,
    STATE_WARN,
)
from tests.conftest import (
    make_scheduled_light_entry,
    record_service_calls,
    restart_entries,
    settle,
    setup_entries,
)

REAL = "light.real_1"
SCHEDULE = "binary_sensor.settings_schedule"
VIRTUAL = "light.scheduled_light"
AUTO_OFF_SWITCH = "switch.scheduled_light_auto_off"
OCCUPANCY = "binary_sensor.porch_occupancy"
STANDBY = 51  # 20 %
BOOST = 255  # 100 %
KELVIN = 2222


def _porch(
    *,
    end_action: str | None = SCHEDULE_END_ACTION_TURN_OFF,
    inside: dict | None = None,
    outside: dict | None = None,
) -> MockConfigEntry:
    """The issue's porch: rests at 20 % / 2222 K, motion raises it to 100 %."""
    return make_scheduled_light_entry(
        schedule_end_action=end_action,
        outside=outside or {CONF_LIGHT_TIMEOUT: 30},
        inside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_OCCUPANCY_ENTITY: OCCUPANCY,
            CONF_AUTO_ON_BRIGHTNESS: 100,
            CONF_STANDBY_BRIGHTNESS: 20,
            CONF_STANDBY_COLOR_TEMP: KELVIN,
            **(inside or {}),
        },
    )


async def _setup_porch(
    hass: HomeAssistant, entry: MockConfigEntry, *, schedule: str = "on"
) -> list[dict]:
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, schedule)
    hass.states.async_set(OCCUPANCY, "off")
    calls = record_service_calls(hass)
    _record_light_contexts(hass)
    await setup_entries(hass, entry)
    await settle(hass)
    return calls


def _record_light_contexts(hass: HomeAssistant) -> None:
    """Keep the context of each light command, to echo it like a member."""
    contexts: list[Context] = hass.data.setdefault("standby_test_contexts", [])

    @callback
    def _record(event: Event) -> None:
        if event.data["domain"] == "light":
            contexts.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)


async def _echo_standby(hass: HomeAssistant) -> None:
    """Report the member at standby under MoLight's last command context."""
    context = hass.data["standby_test_contexts"][-1]
    hass.states.async_set(REAL, "on", {"brightness": STANDBY}, context=context)
    await settle(hass)


def _light_calls(calls: list[dict], service: str) -> list[dict]:
    """Service data of each recorded light.<service> call, in order."""
    return [
        call["service_data"]
        for call in calls
        if call["domain"] == "light" and call["service"] == service
    ]


def _attrs(hass: HomeAssistant) -> dict:
    return hass.states.get(VIRTUAL).attributes


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


async def _set(hass: HomeAssistant, entity_id: str, state: str, **attrs) -> None:
    hass.states.async_set(entity_id, state, attrs)
    await settle(hass)


def _assert_standby(hass: HomeAssistant) -> None:
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_STANDBY
    assert state.attributes["brightness"] == STANDBY


@pytest.mark.asyncio
async def test_schedule_start_turns_an_off_light_on_at_standby(
    hass: HomeAssistant,
) -> None:
    """The start boundary lights the porch at standby with the auto-on fade."""
    select = "select.scene"
    hass.states.async_set(select, "Day", {"options": ["Day", "Night"]})
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    calls = await _setup_porch(
        hass,
        _porch(
            inside={
                CONF_AUTO_ON_TRANSITION: 2,
                CONF_AUTO_OFF_TRANSITION: 3,
                CONF_TURN_ON_SELECT_ENTITY: select,
                CONF_TURN_ON_SELECT_OPTION: "Night",
            }
        ),
        schedule="off",
    )
    assert hass.states.get(VIRTUAL).state == "off"

    await _set(hass, SCHEDULE, "on")

    _assert_standby(hass)
    assert _light_calls(calls, "turn_on")[-1] == {
        "entity_id": [REAL],
        "brightness": STANDBY,
        "color_temp_kelvin": KELVIN,
        "transition": 2.0,
    }
    assert selected == ["Night"]


@pytest.mark.asyncio
async def test_occupancy_raises_standby_and_the_timeout_returns_to_it(
    hass: HomeAssistant, freezer
) -> None:
    """Motion lifts the light to auto-on; vacancy drops it back, not off."""
    calls = await _setup_porch(
        hass, _porch(inside={CONF_AUTO_ON_TRANSITION: 2, CONF_AUTO_OFF_TRANSITION: 3})
    )
    _assert_standby(hass)

    await _set(hass, OCCUPANCY, "on")
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _attrs(hass)["brightness"] == BOOST
    assert _light_calls(calls, "turn_on")[-1]["brightness"] == BOOST

    await _set(hass, OCCUPANCY, "off")
    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 29)
    assert _attrs(hass)["brightness"] == BOOST
    await _tick(hass, freezer, 2)

    _assert_standby(hass)
    assert _light_calls(calls, "turn_off") == []
    assert _light_calls(calls, "turn_on")[-1] == {
        "entity_id": [REAL],
        "brightness": STANDBY,
        "color_temp_kelvin": KELVIN,
        "transition": 3.0,
    }


@pytest.mark.asyncio
async def test_occupancy_without_an_auto_on_brightness_holds_standby_as_it_is(
    hass: HomeAssistant, freezer
) -> None:
    """A blank auto-on brightness names no level to rise to."""
    calls = await _setup_porch(hass, _porch(inside={CONF_AUTO_ON_BRIGHTNESS: None}))
    await _echo_standby(hass)

    await _set(hass, OCCUPANCY, "on")

    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert "brightness" not in _light_calls(calls, "turn_on")[-1]
    assert _attrs(hass)["brightness"] == STANDBY
    await _set(hass, OCCUPANCY, "off")
    await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_warning_stages_lead_into_standby(hass: HomeAssistant, freezer) -> None:
    """Effect and warn still run, and their last step is standby."""
    calls = await _setup_porch(
        hass,
        _porch(
            inside={
                CONF_EFFECT_TIMEOUT: 2,
                CONF_EFFECT_BRIGHTNESS: 0,
                CONF_WARN_TIMEOUT: 5,
                CONF_WARN_BRIGHTNESS: 50,
            }
        ),
    )
    await _set(hass, OCCUPANCY, "on")
    await _set(hass, OCCUPANCY, "off")

    await _tick(hass, freezer, 31)
    assert _attrs(hass)["molight_state"] == STATE_EFFECT
    await _tick(hass, freezer, 2)
    assert _attrs(hass)["molight_state"] == STATE_WARN
    await _tick(hass, freezer, 5)

    _assert_standby(hass)
    assert _attrs(hass)["warning_active"] is False
    # Only the effect blink turned the members off.
    assert len(_light_calls(calls, "turn_off")) == 1
    assert _light_calls(calls, "turn_on")[-1]["brightness"] == STANDBY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    [
        {CONF_WARN_TIMEOUT: 2, CONF_WARN_COLOR_TEMP: 6000},
        {
            CONF_EFFECT_TIMEOUT: 2,
            CONF_EFFECT_BRIGHTNESS: 40,
            CONF_EFFECT_COLOR_TEMP: 6000,
        },
    ],
    ids=["warn", "effect_only"],
)
async def test_standby_without_a_color_restores_the_pre_warning_color(
    hass: HomeAssistant, freezer, stage: dict
) -> None:
    """A colored warning stage must not become the resting appearance."""
    hass.states.async_set(REAL, "off", {"supported_color_modes": ["color_temp"]})
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(OCCUPANCY, "off")
    calls = record_service_calls(hass)
    await setup_entries(
        hass,
        _porch(
            inside={
                CONF_STANDBY_COLOR_TEMP: None,
                CONF_AUTO_ON_COLOR_TEMP: 3000,
                **stage,
            }
        ),
    )
    await settle(hass)
    assert "color_temp_kelvin" not in _light_calls(calls, "turn_on")[-1]

    await _set(hass, OCCUPANCY, "on")
    await _set(hass, OCCUPANCY, "off")
    await _tick(hass, freezer, 31)
    assert _attrs(hass)["color_temp_kelvin"] == 6000
    await _tick(hass, freezer, 2)

    _assert_standby(hass)
    assert _light_calls(calls, "turn_on")[-1]["color_temp_kelvin"] == 3000
    assert _attrs(hass)["color_temp_kelvin"] == 3000


@pytest.mark.asyncio
async def test_warning_leads_into_an_rgb_standby_on_mixed_members(
    hass: HomeAssistant, freezer
) -> None:
    """One command carries the standby color; each member shows what it can."""
    white = "light.real_white"
    hass.states.async_set(REAL, "off", {"supported_color_modes": ["rgb"]})
    hass.states.async_set(white, "off", {"supported_color_modes": ["color_temp"]})
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(OCCUPANCY, "off")
    calls = record_service_calls(hass)
    entry = _porch(
        inside={
            CONF_STANDBY_COLOR_TEMP: None,
            CONF_STANDBY_RGB_COLOR: [0, 0, 255],
            CONF_AUTO_ON_COLOR_TEMP: 3000,
            CONF_WARN_TIMEOUT: 2,
            CONF_WARN_COLOR_TEMP: 6000,
        }
    )
    data = {**entry.data, CONF_LIGHTS: [REAL, white]}
    await setup_entries(hass, MockConfigEntry(domain=DOMAIN, data=data))
    await settle(hass)
    assert tuple(_attrs(hass)["hs_color"]) == (240.0, 100.0)

    await _set(hass, OCCUPANCY, "on")
    assert _attrs(hass)["color_temp_kelvin"] == 3000
    await _set(hass, OCCUPANCY, "off")
    await _tick(hass, freezer, 31)
    assert _attrs(hass)["molight_state"] == STATE_WARN
    await _tick(hass, freezer, 2)

    _assert_standby(hass)
    last = _light_calls(calls, "turn_on")[-1]
    assert last["entity_id"] == [REAL, white]
    assert tuple(last["rgb_color"]) == (0, 0, 255)
    assert "color_temp_kelvin" not in last
    assert tuple(_attrs(hass)["hs_color"]) == (240.0, 100.0)


@pytest.mark.asyncio
async def test_false_detection_drops_back_to_standby_quickly(
    hass: HomeAssistant, freezer
) -> None:
    """A boost from standby counts as lit by occupancy for the quick off."""
    await _setup_porch(hass, _porch())
    await _set(hass, OCCUPANCY, "on")
    await _set(hass, OCCUPANCY, "off", last_clear_false_detection=True)

    await _tick(hass, freezer, 6)
    _assert_standby(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["gate_lifts", "restart"])
async def test_false_detection_after_a_late_raise_drops_back_quickly(
    hass: HomeAssistant, freezer, route: str
) -> None:
    """Occupancy that raises standby late still owns the raise it caused."""
    illuminance = "binary_sensor.porch_bright"
    entry = _porch(
        inside={
            CONF_ILLUMINANCE_ENTITY: illuminance,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_GATE,
        }
    )
    if route == "gate_lifts":
        hass.states.async_set(illuminance, "on")
        await _setup_porch(hass, entry)
        await _set(hass, OCCUPANCY, "on")
        _assert_standby(hass)
        await _set(hass, illuminance, "off")
    else:
        mock_restore_cache(
            hass,
            [
                State(
                    VIRTUAL,
                    "on",
                    {
                        ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                        ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                        "molight_state": STATE_STANDBY,
                    },
                )
            ],
        )
        hass.states.async_set(illuminance, "off")
        hass.states.async_set(REAL, "on", {"brightness": STANDBY})
        hass.states.async_set(SCHEDULE, "on")
        hass.states.async_set(OCCUPANCY, "on")
        await setup_entries(hass, entry)
        await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _attrs(hass)["brightness"] == BOOST

    await _set(hass, OCCUPANCY, "off", last_clear_false_detection=True)
    await _tick(hass, freezer, 6)

    _assert_standby(hass)


@pytest.mark.asyncio
async def test_manual_off_cancels_standby_until_the_next_boundary(
    hass: HomeAssistant, freezer
) -> None:
    """After a manual off, occupancy cycles end in off until the window ends."""
    await _setup_porch(hass, _porch(end_action=SCHEDULE_END_ACTION_KEEP))
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True

    await _set(hass, OCCUPANCY, "on")
    assert _attrs(hass)["brightness"] == BOOST
    await _set(hass, OCCUPANCY, "off")
    await _tick(hass, freezer, 31)
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE

    # The window ending and a new one starting re-enable standby.
    await _set(hass, SCHEDULE, "off")
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False
    assert hass.states.get(VIRTUAL).state == "off"
    await _set(hass, SCHEDULE, "on")
    _assert_standby(hass)


def _windows_schedule(name: str, *windows: tuple[str, str]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: name,
            CONF_TIME_WINDOWS: [
                {"start": {"time": start}, "end": {"time": end}}
                for start, end in windows
            ],
        },
    )


async def _manual_off_at_standby(hass: HomeAssistant, *schedules) -> str:
    """Rest the porch at standby under real schedules, turn it off by hand,
    and return the settings schedule's window marker."""
    await setup_entries(hass, *schedules)
    hass.states.async_set(REAL, "off")
    hass.states.async_set(OCCUPANCY, "off")
    _record_light_contexts(hass)
    await setup_entries(hass, _porch())
    await settle(hass)
    await _echo_standby(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True
    return hass.states.get(SCHEDULE).attributes["current_window_start"]


@pytest.mark.asyncio
@pytest.mark.parametrize("windows", ["touching", "all_day"])
@pytest.mark.parametrize("raised", [False, True], ids=["off", "raised"])
async def test_a_window_that_touches_the_last_brings_standby_back(
    hass: HomeAssistant, freezer, windows: str, raised: bool
) -> None:
    """A window starting as the last one ends is a boundary, though the
    schedule stays on, so a manual off cancels standby only until then."""
    freezer.move_to("2026-01-14 12:00:00-08:00")
    marker = await _manual_off_at_standby(
        hass,
        _windows_schedule("Settings Schedule", ("11:00", "13:00"), ("13:00", "14:00"))
        if windows == "touching"
        else _windows_schedule("Settings Schedule", ("00:00", "00:00")),
    )
    if raised:
        await _set(hass, OCCUPANCY, "on")
        context = hass.data["standby_test_contexts"][-1]
        hass.states.async_set(REAL, "on", {"brightness": BOOST}, context=context)
        await settle(hass)

    await _tick(hass, freezer, 3602 if windows == "touching" else 43202)
    schedule = hass.states.get(SCHEDULE)
    assert schedule.state == "on"
    assert schedule.attributes["current_window_start"] != marker
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False
    if raised:
        assert _attrs(hass)["brightness"] == BOOST
        await _set(hass, OCCUPANCY, "off")
        await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_touching_windows_combined_into_one_keep_a_manual_off(
    hass: HomeAssistant, freezer
) -> None:
    """A Combined Schedule joins touching windows into one, so a manual off
    keeps standby off across the join, as follow mode stays off."""
    freezer.move_to("2026-01-14 12:00:00-08:00")
    marker = await _manual_off_at_standby(
        hass,
        _windows_schedule("Late Morning", ("11:00", "13:00")),
        _windows_schedule("Early Afternoon", ("13:00", "14:00")),
        MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_SCHEDULE,
                CONF_NAME: "Settings Schedule",
                CONF_SCHEDULE_INPUTS: [
                    "binary_sensor.late_morning",
                    "binary_sensor.early_afternoon",
                ],
            },
        ),
    )

    await _tick(hass, freezer, 3602)
    schedule = hass.states.get(SCHEDULE)
    assert schedule.state == "on"
    assert schedule.attributes["current_window_start"] == marker
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True


@pytest.mark.asyncio
async def test_manual_off_while_bright_cancels_the_raise_it_interrupted(
    hass: HomeAssistant,
) -> None:
    """A manual off of a light brightness had off stays off when it gets dark."""
    illuminance = "binary_sensor.porch_bright"
    hass.states.async_set(illuminance, "off")
    await _setup_porch(
        hass,
        _porch(
            inside={
                CONF_ILLUMINANCE_ENTITY: illuminance,
                CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
            }
        ),
    )
    await _set(hass, OCCUPANCY, "on")
    await _set(hass, OCCUPANCY, "off")
    await _set(hass, illuminance, "on")
    assert hass.states.get(VIRTUAL).state == "off"

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await _set(hass, illuminance, "off")

    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True


@pytest.mark.asyncio
async def test_turning_the_light_back_on_rejoins_standby(
    hass: HomeAssistant, freezer
) -> None:
    """A manual on after a manual off runs a timer that ends at standby."""
    await _setup_porch(hass, _porch())
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 128}
    )
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False

    await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("presence", ["occupancy", "door"])
@pytest.mark.parametrize("restart", [False, True], ids=["live", "restart"])
@pytest.mark.parametrize("how", ["turn_on", "dim_at_wall", "recolor_at_wall"])
async def test_turning_the_light_back_on_while_occupied_rejoins_standby(
    hass: HomeAssistant, freezer, presence: str, restart: bool, how: str
) -> None:
    """A manual on while presence holds the raised light rejoins standby too,
    as does a dim or recolour at the wall, and says so at once, so a restart
    before they leave keeps it."""
    door = "binary_sensor.porch_door"
    hass.states.async_set(door, "off")
    entry = _porch(
        inside={CONF_DOOR_ENTITY: door, CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE}
    )
    await _setup_porch(hass, entry)
    await _echo_standby(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    sensor = OCCUPANCY if presence == "occupancy" else door
    await _set(hass, sensor, "on")
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    context = hass.data["standby_test_contexts"][-1]
    hass.states.async_set(REAL, "on", {"brightness": BOOST}, context=context)
    await settle(hass)
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True

    if how == "turn_on":
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
        )
        await settle(hass)
    elif how == "dim_at_wall":
        await _set(hass, REAL, "on", brightness=200)
    else:
        await _set(hass, REAL, "on", brightness=BOOST, hs_color=(200.0, 50.0))
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False
    if restart:
        await restart_entries(hass, entry)
        await settle(hass)
        assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False

    await _set(hass, sensor, "off")
    await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_bright_sensor_that_is_also_a_hold_keeps_standby(
    hass: HomeAssistant,
) -> None:
    """Going bright holds standby when the illuminance sensor is also a
    keep-on entity, instead of forcing it off first."""
    bright = "binary_sensor.porch_bright"
    hass.states.async_set(bright, "off")
    await _setup_porch(
        hass,
        _porch(
            inside={
                CONF_ILLUMINANCE_ENTITY: bright,
                CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
                CONF_HOLD_ENTITIES: [bright],
            }
        ),
    )
    await _echo_standby(hass)
    await _set(hass, bright, "on")
    assert _attrs(hass)["auto_off_held"] is True
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_external_dim_at_standby_runs_a_timer_back_to_standby(
    hass: HomeAssistant, freezer
) -> None:
    """Brightening at the wall is activity, not a new resting level."""
    await _setup_porch(hass, _porch())
    await _echo_standby(hass)
    _assert_standby(hass)

    await _set(hass, REAL, "on", brightness=200)
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["brightness"] == 200

    await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("presence", ["maintain", "door"])
@pytest.mark.parametrize("source", ["physical", "virtual"])
async def test_raising_standby_by_hand_is_held_by_presence_already_there(
    hass: HomeAssistant, freezer, presence: str, source: str
) -> None:
    """Maintain or an open door that left standby alone holds a manual raise."""
    sensor = "binary_sensor.presence"
    illuminance = "binary_sensor.porch_bright"
    # Bright in gate mode keeps the open door from raising standby itself.
    hass.states.async_set(illuminance, "on")
    hass.states.async_set(sensor, "on")
    inside = (
        {CONF_MAINTAIN_OCCUPANCY_ENTITY: sensor}
        if presence == "maintain"
        else {
            CONF_DOOR_ENTITY: sensor,
            CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
            CONF_ILLUMINANCE_ENTITY: illuminance,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_GATE,
        }
    )
    await _setup_porch(hass, _porch(inside=inside))
    await _echo_standby(hass)
    _assert_standby(hass)

    if source == "physical":
        await _set(hass, REAL, "on", brightness=200)
    else:
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
        )
        await settle(hass)
    await _tick(hass, freezer, 31)

    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _attrs(hass)["brightness"] == 200
    await _set(hass, sensor, "off")
    await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["physical", "virtual"])
async def test_false_detection_does_not_cut_a_manual_raise_short(
    hass: HomeAssistant, freezer, source: str
) -> None:
    """False motion against an old visit leaves a raise by hand its timeout."""
    visit = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await _setup_porch(hass, _porch())
    await _echo_standby(hass)
    if source == "physical":
        await _set(hass, REAL, "on", brightness=200)
    else:
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
        )
        await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE

    await _tick(hass, freezer, 2)
    await _set(hass, OCCUPANCY, "on", latest_occupied_time=visit)
    await _set(
        hass,
        OCCUPANCY,
        "off",
        latest_occupied_time=visit,
        last_clear_false_detection=True,
    )

    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 26)
    assert _attrs(hass)["brightness"] == 200
    await _tick(hass, freezer, 3)
    _assert_standby(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["brightness", "color"])
async def test_false_detection_does_not_cut_short_a_raise_changed_at_the_wall(
    hass: HomeAssistant, freezer, change: str
) -> None:
    """Dimming or recolouring a light that occupancy raised from standby makes
    the raise the user's: false motion no longer drops it back quickly."""
    visit = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await _setup_porch(hass, _porch())
    await _echo_standby(hass)
    await _set(hass, OCCUPANCY, "on", latest_occupied_time=visit)
    context = hass.data["standby_test_contexts"][-1]
    hass.states.async_set(REAL, "on", {"brightness": BOOST}, context=context)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED

    await _tick(hass, freezer, 2)
    level = 200 if change == "brightness" else BOOST
    changed = {} if change == "brightness" else {"hs_color": (200.0, 50.0)}
    await _set(hass, REAL, "on", brightness=level, **changed)
    await _set(
        hass,
        OCCUPANCY,
        "off",
        latest_occupied_time=visit,
        last_clear_false_detection=True,
    )

    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 28)
    assert _attrs(hass)["brightness"] == level
    await _tick(hass, freezer, 3)
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_start_boundary_keeps_an_already_lit_light_until_its_timer_ends(
    hass: HomeAssistant, freezer
) -> None:
    """A light someone is using is not dimmed at the start; its timer is."""
    calls = await _setup_porch(
        hass, _porch(outside={CONF_LIGHT_TIMEOUT: 300}), schedule="off"
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}
    )
    await settle(hass)
    sent = len(_light_calls(calls, "turn_on"))

    await _set(hass, SCHEDULE, "on")
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["brightness"] == 200
    assert len(_light_calls(calls, "turn_on")) == sent

    await _tick(hass, freezer, 301)
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_maintain_leaves_standby_alone_but_the_door_raises_it(
    hass: HomeAssistant,
) -> None:
    """The maintain sensor never turns a light up; a door opening does."""
    maintain = "binary_sensor.porch_presence"
    door = "binary_sensor.front_door"
    hass.states.async_set(maintain, "off")
    hass.states.async_set(door, "off")
    await _setup_porch(
        hass,
        _porch(
            inside={
                CONF_MAINTAIN_OCCUPANCY_ENTITY: maintain,
                CONF_DOOR_ENTITY: door,
                CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
            }
        ),
    )
    await _set(hass, maintain, "on")
    _assert_standby(hass)

    await _set(hass, door, "on")
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _attrs(hass)["brightness"] == BOOST


@pytest.mark.asyncio
async def test_door_raises_standby_and_closing_returns_to_it(
    hass: HomeAssistant, freezer
) -> None:
    """An open-and-close door holds the raised light, then counts down."""
    door = "binary_sensor.front_door"
    hass.states.async_set(door, "off")
    await _setup_porch(
        hass,
        _porch(inside={CONF_DOOR_ENTITY: door, CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE}),
    )
    await _set(hass, door, "on")
    assert _attrs(hass)["brightness"] == BOOST
    await _set(hass, door, "off")
    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN

    await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_illuminance_gate_blocks_the_raise_but_not_standby(
    hass: HomeAssistant,
) -> None:
    """Gate mode only gates turn-ons above standby."""
    illuminance = "binary_sensor.porch_bright"
    hass.states.async_set(illuminance, "on")
    await _setup_porch(
        hass,
        _porch(
            inside={
                CONF_ILLUMINANCE_ENTITY: illuminance,
                CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_GATE,
            }
        ),
        schedule="off",
    )
    await _set(hass, SCHEDULE, "on")
    _assert_standby(hass)

    await _set(hass, OCCUPANCY, "on")
    _assert_standby(hass)

    # Darkness lifts the gate over presence that is already there.
    await _set(hass, illuminance, "off")
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _attrs(hass)["brightness"] == BOOST


@pytest.mark.asyncio
async def test_illuminance_control_keeps_standby_off_while_bright(
    hass: HomeAssistant,
) -> None:
    """Control mode makes standby wait for darkness, and forces it off."""
    illuminance = "binary_sensor.porch_bright"
    hass.states.async_set(illuminance, "on")
    await _setup_porch(
        hass,
        _porch(
            inside={
                CONF_ILLUMINANCE_ENTITY: illuminance,
                CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
            }
        ),
        schedule="off",
    )
    await _set(hass, SCHEDULE, "on")
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["last_on_illuminance"] is None

    await _set(hass, illuminance, "off")
    _assert_standby(hass)
    assert _attrs(hass)["last_on_illuminance"] is not None

    await _set(hass, illuminance, "on")
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["bright_forced_off"] is True
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False

    await _set(hass, illuminance, "off")
    _assert_standby(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("maintained", [True, False], ids=["maintained", "timed"])
@pytest.mark.parametrize("standby", [True, False], ids=["standby", "no_standby"])
async def test_raised_period_cut_short_by_brightness_resumes_when_dark(
    hass: HomeAssistant, freezer, maintained: bool, standby: bool
) -> None:
    """Going dark resumes a recent raised period, with or without standby."""
    maintain = "binary_sensor.porch_maintain"
    illuminance = "binary_sensor.porch_bright"
    hass.states.async_set(maintain, "on" if maintained else "off")
    hass.states.async_set(illuminance, "off")
    await _setup_porch(
        hass,
        _porch(
            inside={
                CONF_STANDBY_BRIGHTNESS: 20 if standby else None,
                CONF_MAINTAIN_OCCUPANCY_ENTITY: maintain,
                CONF_ILLUMINANCE_ENTITY: illuminance,
                CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
            }
        ),
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
    )
    await settle(hass)
    await _tick(hass, freezer, 2)
    await _set(hass, illuminance, "on")
    assert hass.states.get(VIRTUAL).state == "off"
    await _tick(hass, freezer, 2)

    await _set(hass, illuminance, "off")

    assert _attrs(hass)["brightness"] == BOOST
    if maintained:
        assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
        return
    # The rest of the 30 s period runs out, and ends where the profile rests.
    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 27)
    if standby:
        _assert_standby(hass)
    else:
        assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("warn", [False, True], ids=["plain", "warning"])
async def test_timeout_while_bright_in_control_mode_ends_in_off(
    hass: HomeAssistant, freezer, warn: bool
) -> None:
    """A manual on-period that ends in daylight turns off; dusk brings standby."""
    illuminance = "binary_sensor.porch_bright"
    hass.states.async_set(illuminance, "on")
    inside = {
        CONF_ILLUMINANCE_ENTITY: illuminance,
        CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
    }
    if warn:
        inside |= {CONF_WARN_TIMEOUT: 5, CONF_WARN_BRIGHTNESS: 40}
    await _setup_porch(hass, _porch(inside=inside))
    assert hass.states.get(VIRTUAL).state == "off"

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
    )
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    await _tick(hass, freezer, 31)
    if warn:
        assert _attrs(hass)["molight_state"] == STATE_WARN
        await _tick(hass, freezer, 6)

    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE
    await _set(hass, illuminance, "off")
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_hold_freezes_the_raised_level_and_release_times_back_to_standby(
    hass: HomeAssistant, freezer
) -> None:
    """A keep-on hold stops the drop; releasing it starts a fresh timer."""
    hold = "input_boolean.guests"
    hass.states.async_set(hold, "off")
    calls = await _setup_porch(hass, _porch(inside={CONF_HOLD_ENTITIES: [hold]}))

    # Holding and releasing at standby leaves it there, with nothing re-sent.
    sent = len(_light_calls(calls, "turn_on"))
    await _set(hass, hold, "on")
    await _set(hass, hold, "off")
    _assert_standby(hass)
    assert len(_light_calls(calls, "turn_on")) == sent

    await _set(hass, hold, "on")
    await _set(hass, OCCUPANCY, "on")
    await _set(hass, OCCUPANCY, "off")
    await _tick(hass, freezer, 120)
    assert _attrs(hass)["brightness"] == BOOST

    await _set(hass, hold, "off")
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    await _tick(hass, freezer, 29)
    assert _attrs(hass)["brightness"] == BOOST
    await _tick(hass, freezer, 2)
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_schedule_end_turn_off_turns_standby_off(hass: HomeAssistant) -> None:
    """Turn off ends standby with the inside profile's off fade."""
    calls = await _setup_porch(hass, _porch(inside={CONF_AUTO_OFF_TRANSITION: 4}))
    _assert_standby(hass)

    await _set(hass, SCHEDULE, "off")

    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert _light_calls(calls, "turn_off")[-1] == {
        "entity_id": [REAL],
        "transition": 4.0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("end_action", "watched", "state"),
    [
        (SCHEDULE_END_ACTION_TURN_OFF, True, STATE_IDLE),
        (SCHEDULE_END_ACTION_KEEP, True, STATE_OCCUPIED),
        (SCHEDULE_END_ACTION_SWITCH, True, STATE_OCCUPIED),
        (SCHEDULE_END_ACTION_KEEP, False, STATE_COUNTDOWN),
        (SCHEDULE_END_ACTION_SWITCH, False, STATE_ACTIVE),
    ],
)
async def test_schedule_end_hands_over_a_light_raised_by_presence(
    hass: HomeAssistant, freezer, end_action: str, watched: bool, state: str
) -> None:
    """Presence outlasts the end only where the outside settings still see it."""
    outside = {CONF_LIGHT_TIMEOUT: 60}
    if watched:
        outside[CONF_OCCUPANCY_ENTITY] = OCCUPANCY
    await _setup_porch(hass, _porch(end_action=end_action, outside=outside))
    await _set(hass, OCCUPANCY, "on")
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED

    await _set(hass, SCHEDULE, "off")

    assert _attrs(hass)["molight_state"] == state
    if not watched:
        await _tick(hass, freezer, 61)
        assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_end_keep_gives_standby_the_outside_timeout(
    hass: HomeAssistant, freezer
) -> None:
    """Keep state starts the outside profile's timeout from the boundary."""
    await _setup_porch(
        hass,
        _porch(end_action=SCHEDULE_END_ACTION_KEEP, outside={CONF_LIGHT_TIMEOUT: 300}),
    )
    await _set(hass, SCHEDULE, "off")
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == STANDBY

    await _tick(hass, freezer, 299)
    assert hass.states.get(VIRTUAL).state == "on"
    await _tick(hass, freezer, 2)
    assert hass.states.get(VIRTUAL).state == "off"


async def _member_reports_off(hass: HomeAssistant) -> None:
    """Report the member off under MoLight's last command context."""
    context = hass.data["standby_test_contexts"][-1]
    hass.states.async_set(REAL, "off", context=context)
    await settle(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "end_action", [SCHEDULE_END_ACTION_KEEP, SCHEDULE_END_ACTION_SWITCH]
)
@pytest.mark.parametrize("restart", [False, True], ids=["live", "restart"])
async def test_schedule_end_timeout_from_standby_resumes_after_brightness(
    hass: HomeAssistant, freezer, end_action: str, restart: bool
) -> None:
    """The fresh outside timeout is an on-period that going dark resumes."""
    illuminance = "binary_sensor.yard_bright"
    hass.states.async_set(illuminance, "off")
    entry = _porch(
        end_action=end_action,
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_ILLUMINANCE_ENTITY: illuminance,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
        },
    )
    await _setup_porch(hass, entry)
    await _echo_standby(hass)
    await _set(hass, SCHEDULE, "off")
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE

    await _tick(hass, freezer, 2)
    await _set(hass, illuminance, "on")
    assert hass.states.get(VIRTUAL).state == "off"
    await _member_reports_off(hass)
    if restart:
        await restart_entries(hass, entry)
        assert _attrs(hass)["bright_forced_off"] is True
    await _tick(hass, freezer, 2)
    await _set(hass, illuminance, "off")

    assert hass.states.get(VIRTUAL).state == "on"
    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    assert _attrs(hass)["last_on_virtual"] is None
    assert _attrs(hass)["last_on_physical"] is None
    # It ends when the timeout the boundary started would have.
    await _tick(hass, freezer, 54)
    assert hass.states.get(VIRTUAL).state == "on"
    await _tick(hass, freezer, 3)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_end_timeout_from_standby_resumes_despite_an_old_visit(
    hass: HomeAssistant, freezer
) -> None:
    """An hour-old outside visit does not end the timeout the boundary began."""
    yard = "binary_sensor.yard_occupancy"
    illuminance = "binary_sensor.yard_bright"
    visit = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    hass.states.async_set(yard, "off", {"latest_occupied_time": visit})
    hass.states.async_set(illuminance, "off")
    await _setup_porch(
        hass,
        _porch(
            end_action=SCHEDULE_END_ACTION_KEEP,
            outside={
                CONF_LIGHT_TIMEOUT: 60,
                CONF_OCCUPANCY_ENTITY: yard,
                CONF_ILLUMINANCE_ENTITY: illuminance,
                CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
            },
        ),
    )
    await _echo_standby(hass)
    await _set(hass, SCHEDULE, "off")

    await _tick(hass, freezer, 2)
    await _set(hass, illuminance, "on")
    assert hass.states.get(VIRTUAL).state == "off"
    await _member_reports_off(hass)
    await _tick(hass, freezer, 2)
    await _set(hass, illuminance, "off")

    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 54)
    assert hass.states.get(VIRTUAL).state == "on"
    await _tick(hass, freezer, 3)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_hold_release_after_a_keep_end_starts_a_timeout_darkness_resumes(
    hass: HomeAssistant, freezer
) -> None:
    """A hold outlasting the boundary's timeout releases into a fresh one."""
    hold = "input_boolean.guests"
    illuminance = "binary_sensor.yard_bright"
    hass.states.async_set(hold, "on")
    hass.states.async_set(illuminance, "off")
    settings = {
        CONF_LIGHT_TIMEOUT: 60,
        CONF_HOLD_ENTITIES: [hold],
        CONF_ILLUMINANCE_ENTITY: illuminance,
        CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
    }
    await _setup_porch(
        hass,
        _porch(end_action=SCHEDULE_END_ACTION_KEEP, inside=settings, outside=settings),
    )
    await _echo_standby(hass)
    await _set(hass, SCHEDULE, "off")
    await _tick(hass, freezer, 3600)
    await _set(hass, hold, "off")
    assert hass.states.get(VIRTUAL).state == "on"

    await _tick(hass, freezer, 2)
    await _set(hass, illuminance, "on")
    assert hass.states.get(VIRTUAL).state == "off"
    await _member_reports_off(hass)
    await _tick(hass, freezer, 2)
    await _set(hass, illuminance, "off")

    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 54)
    assert hass.states.get(VIRTUAL).state == "on"
    await _tick(hass, freezer, 3)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_maintain_countdown_cut_short_by_brightness_resumes_when_dark(
    hass: HomeAssistant, freezer
) -> None:
    """A maintain sensor's countdown resumes raised, and still ends at standby."""
    maintain = "binary_sensor.porch_maintain"
    illuminance = "binary_sensor.porch_bright"
    hass.states.async_set(maintain, "off")
    hass.states.async_set(illuminance, "off")
    await _setup_porch(
        hass,
        _porch(
            inside={
                CONF_LIGHT_TIMEOUT: 60,
                CONF_OCCUPANCY_ENTITY: None,
                CONF_MAINTAIN_OCCUPANCY_ENTITY: maintain,
                CONF_ILLUMINANCE_ENTITY: illuminance,
                CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
            }
        ),
    )
    await _echo_standby(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
    )
    await settle(hass)
    await _set(hass, maintain, "on")
    await _tick(hass, freezer, 300)
    left = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    await _set(hass, maintain, "off", latest_occupied_time=left)
    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN

    await _tick(hass, freezer, 2)
    await _set(hass, illuminance, "on")
    assert hass.states.get(VIRTUAL).state == "off"
    await _member_reports_off(hass)
    await _tick(hass, freezer, 2)
    await _set(hass, illuminance, "off")

    # 46 s of the countdown are left, at the auto-on level.
    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    assert _attrs(hass)["brightness"] == BOOST
    await _tick(hass, freezer, 45)
    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 2)
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_schedule_end_timeout_from_standby_survives_a_false_detection(
    hass: HomeAssistant, freezer
) -> None:
    """False motion against an old visit does not cut the fresh timeout short."""
    yard = "binary_sensor.yard_occupancy"
    visit = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    hass.states.async_set(yard, "off", {"latest_occupied_time": visit})
    await _setup_porch(
        hass,
        _porch(
            end_action=SCHEDULE_END_ACTION_KEEP,
            outside={CONF_LIGHT_TIMEOUT: 60, CONF_OCCUPANCY_ENTITY: yard},
        ),
    )
    await _echo_standby(hass)
    await _set(hass, SCHEDULE, "off")
    await _tick(hass, freezer, 2)

    await _set(hass, yard, "on", latest_occupied_time=visit)
    await _set(
        hass, yard, "off", latest_occupied_time=visit, last_clear_false_detection=True
    )

    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 56)
    assert hass.states.get(VIRTUAL).state == "on"
    await _tick(hass, freezer, 3)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_end_keep_leaves_a_raised_light_to_its_presence(
    hass: HomeAssistant, freezer
) -> None:
    """Someone still there at the boundary keeps the light until they leave."""
    await _setup_porch(
        hass,
        _porch(
            end_action=SCHEDULE_END_ACTION_KEEP,
            outside={CONF_LIGHT_TIMEOUT: 60, CONF_OCCUPANCY_ENTITY: OCCUPANCY},
        ),
    )
    await _set(hass, OCCUPANCY, "on")
    await _set(hass, SCHEDULE, "off")
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _attrs(hass)["brightness"] == BOOST

    await _set(hass, OCCUPANCY, "off")
    await _tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_end_switch_recalculates_standby_from_history(
    hass: HomeAssistant, freezer
) -> None:
    """Switch state finds old motion already due and turns standby off."""
    outside_occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(
        outside_occupancy,
        "off",
        {"latest_occupied_time": (datetime.now(UTC) - timedelta(hours=4)).isoformat()},
    )
    await _setup_porch(
        hass,
        _porch(
            end_action=SCHEDULE_END_ACTION_SWITCH,
            outside={CONF_LIGHT_TIMEOUT: 300, CONF_OCCUPANCY_ENTITY: outside_occupancy},
        ),
    )
    _assert_standby(hass)

    await _set(hass, SCHEDULE, "off")
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_end_switch_recalculates_a_standby_still_waiting(
    hass: HomeAssistant,
) -> None:
    """A standby waiting for its selection is recalculated like a lit one."""
    select = _ParkedSelect(hass, park=1)
    outside_occupancy = "binary_sensor.yard_occupancy"
    hass.states.async_set(
        outside_occupancy,
        "off",
        {"latest_occupied_time": (datetime.now(UTC) - timedelta(hours=4)).isoformat()},
    )
    calls = await _setup_porch(
        hass,
        _porch(
            end_action=SCHEDULE_END_ACTION_SWITCH,
            inside=_SCENE,
            outside={CONF_LIGHT_TIMEOUT: 30, CONF_OCCUPANCY_ENTITY: outside_occupancy},
        ),
        schedule="off",
    )
    hass.states.async_set(SCHEDULE, "on")
    await asyncio.wait_for(select.started.wait(), 2)
    assert _attrs(hass)["molight_state"] == STATE_STANDBY

    hass.states.async_set(SCHEDULE, "off")
    await _drain(hass)
    select.release.set()
    await settle(hass)

    # The outside history is long past its timeout: off, and never lit.
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE
    assert _light_calls(calls, "turn_on") == []


@pytest.mark.asyncio
async def test_held_schedule_end_off_waits_for_the_hold(hass: HomeAssistant) -> None:
    """A held Turn off leaves standby lit and applies the off on release."""
    await _setup_porch(hass, _porch())
    await hass.services.async_call("switch", "turn_off", {"entity_id": AUTO_OFF_SWITCH})
    await settle(hass)

    await _set(hass, SCHEDULE, "off")
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    await hass.services.async_call("switch", "turn_on", {"entity_id": AUTO_OFF_SWITCH})
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_outside_profile_without_standby_still_turns_off(
    hass: HomeAssistant, freezer
) -> None:
    """Standby belongs to the inside settings only."""
    await _setup_porch(
        hass,
        _porch(outside={CONF_LIGHT_TIMEOUT: 30, CONF_OCCUPANCY_ENTITY: OCCUPANCY}),
        schedule="off",
    )
    assert hass.states.get(VIRTUAL).state == "off"
    await _set(hass, OCCUPANCY, "on")
    await _set(hass, OCCUPANCY, "off")
    await _tick(hass, freezer, 31)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_rebooted_member_is_sent_standby_again(hass: HomeAssistant) -> None:
    """A member that comes back off is a reboot, not a manual off."""
    calls = await _setup_porch(hass, _porch(inside={CONF_AUTO_ON_TRANSITION: 2}))
    await _echo_standby(hass)
    sent = len(_light_calls(calls, "turn_on"))

    await _set(hass, REAL, "unavailable")
    await _set(hass, REAL, "off")

    _assert_standby(hass)
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False
    assert len(_light_calls(calls, "turn_on")) == sent + 1
    assert _light_calls(calls, "turn_on")[-1]["brightness"] == STANDBY


@pytest.mark.asyncio
@pytest.mark.parametrize("placeholder", [{}, {"restored": True}], ids=["back", "new"])
async def test_member_reporting_in_off_while_raised_is_sent_the_raise(
    hass: HomeAssistant, freezer, placeholder: dict
) -> None:
    """A member reporting in as off while occupancy has the light raised is
    sent the raised level again; standby is not paused, so the light drops
    back to standby once the room clears."""
    calls = await _setup_porch(hass, _porch())
    await _echo_standby(hass)
    await _set(hass, OCCUPANCY, "on")
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    await _set(hass, REAL, "unavailable", **placeholder)
    sent = len(_light_calls(calls, "turn_on"))

    await _set(hass, REAL, "off")

    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False
    assert _attrs(hass)["last_off_manual"] is None
    assert len(_light_calls(calls, "turn_on")) == sent + 1
    assert _light_calls(calls, "turn_on")[-1]["brightness"] == BOOST
    await _set(hass, OCCUPANCY, "off")
    await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "acknowledged", [True, False], ids=["acknowledged", "unanswered_command"]
)
async def test_rebooted_member_at_the_standby_level_is_selected_again(
    hass: HomeAssistant, freezer, acknowledged: bool
) -> None:
    """A member back at the right level may still have lost its selection."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.states.async_set("select.scene", "Day", {"options": ["Day", "Night"]})
    hass.services.async_register("select", "select_option", select_option)
    calls = await _setup_porch(
        hass, _porch(inside={**_SCENE, CONF_STANDBY_COLOR_TEMP: None})
    )
    assert selected == ["Night"]
    if acknowledged:
        await _echo_standby(hass)
    sent = len(_light_calls(calls, "turn_on"))

    await _tick(hass, freezer, 5)
    await _set(hass, REAL, "unavailable")
    await _set(hass, REAL, "on", brightness=STANDBY)

    _assert_standby(hass)
    assert selected == ["Night", "Night"]
    assert len(_light_calls(calls, "turn_on")) == sent + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recovered", "attrs"),
    [("off", {}), ("on", {"brightness": 200})],
    ids=["off", "on_at_another_level"],
)
async def test_recovered_member_is_selected_before_it_is_commanded(
    hass: HomeAssistant, recovered: str, attrs: dict
) -> None:
    """Recovery finishes the selection, read afresh, before the standby command."""
    source = "input_select.theme"
    order: list[tuple[str, str | int]] = []

    async def select_option(call: ServiceCall) -> None:
        await asyncio.sleep(0)
        order.append(("select", call.data["option"]))

    @callback
    def light_command(event: Event) -> None:
        if event.data["domain"] == "light" and event.data["service"] == "turn_on":
            order.append(("light", event.data["service_data"]["brightness"]))

    hass.states.async_set(source, "Night")
    hass.states.async_set("select.scene", "Day", {"options": ["Day", "Night", "Party"]})
    hass.services.async_register("select", "select_option", select_option)
    hass.bus.async_listen(EVENT_CALL_SERVICE, light_command)
    await _setup_porch(
        hass,
        _porch(inside={**_SCENE, CONF_TURN_ON_SELECT_SOURCE_ENTITY: source}),
    )
    assert order == [("select", "Night"), ("light", STANDBY)]
    hass.states.async_set(
        REAL,
        "on",
        {
            "brightness": STANDBY,
            "color_mode": "color_temp",
            "color_temp_kelvin": KELVIN,
        },
        context=hass.data["standby_test_contexts"][-1],
    )
    await settle(hass)
    order.clear()

    await _set(hass, REAL, "unavailable")
    await _set(hass, source, "Party")
    await _set(hass, REAL, recovered, **attrs)

    _assert_standby(hass)
    assert order == [("select", "Party"), ("light", STANDBY)]
    assert _attrs(hass)["last_turn_on_selection_option"] == "Party"


@pytest.mark.asyncio
async def test_restart_puts_a_standby_light_back_at_standby(
    hass: HomeAssistant,
) -> None:
    """A restart re-sends standby instead of adopting the light with a timer."""
    entry = _porch()
    calls = await _setup_porch(hass, entry)
    await _echo_standby(hass)
    sent = len(_light_calls(calls, "turn_on"))

    await restart_entries(hass, entry)
    await settle(hass)

    _assert_standby(hass)
    assert len(_light_calls(calls, "turn_on")) == sent + 1


@pytest.mark.asyncio
async def test_options_edit_applies_a_new_standby_level(hass: HomeAssistant) -> None:
    """The reload after an edit re-sends the edited standby level."""
    entry = _porch()
    calls = await _setup_porch(hass, entry)
    await _echo_standby(hass)

    data = dict(entry.data)
    inside = {**data[CONF_INSIDE_SCHEDULE_SETTINGS], CONF_STANDBY_BRIGHTNESS: 40}
    hass.config_entries.async_update_entry(
        entry, options={**data, CONF_INSIDE_SCHEDULE_SETTINGS: inside}
    )
    await hass.async_block_till_done()
    await settle(hass)

    assert _attrs(hass)["molight_state"] == STATE_STANDBY
    assert _light_calls(calls, "turn_on")[-1]["brightness"] == 102


@pytest.mark.asyncio
async def test_restart_keeps_a_manual_off_within_the_same_window(
    hass: HomeAssistant,
) -> None:
    """A light turned off by hand stays off across a restart."""
    entry = _porch()
    await _setup_porch(hass, entry)
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)

    await restart_entries(hass, entry)
    await settle(hass)

    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True


async def _manual_off_while_occupied(hass: HomeAssistant, entry) -> datetime:
    """Raise standby with occupancy, turn it off by hand, return when."""
    await _setup_porch(hass, entry)
    await _echo_standby(hass)
    await _set(hass, OCCUPANCY, "on")
    context = hass.data["standby_test_contexts"][-1]
    hass.states.async_set(REAL, "on", {"brightness": BOOST}, context=context)
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    context = hass.data["standby_test_contexts"][-1]
    hass.states.async_set(REAL, "off", context=context)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True
    return datetime.now(UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("started", "raised"),
    [("none", False), ("unknown", False), ("before", False), ("after", True)],
)
async def test_restart_keeps_a_manual_off_over_presence_it_already_had(
    hass: HomeAssistant, freezer, started: str, raised: bool
) -> None:
    """Occupancy still on at a restart only raises a light turned off by hand
    when its cycle started after that off; when the sensor can't tell, the
    light stays off."""
    entry = _porch()
    off = await _manual_off_while_occupied(hass, entry)
    await _tick(hass, freezer, 10)
    if started != "none":
        times = {
            "unknown": None,
            "before": (off - timedelta(seconds=5)).isoformat(),
            # A cycle the light did not see start, as its own reload missed it.
            "after": datetime.now(UTC).isoformat(),
        }
        hass.states.async_set(OCCUPANCY, "on", {"last_on_time": times[started]})

    await restart_entries(hass, entry)
    await settle(hass)
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True
    if raised:
        assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
        assert _attrs(hass)["brightness"] == BOOST
        return
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE

    # Their next visit raises it, and still ends in off.
    await _set(hass, OCCUPANCY, "off")
    await _set(hass, OCCUPANCY, "on")
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.parametrize("late", ["placeholder", "unknown_start"])
async def test_restart_keeps_a_manual_off_over_presence_that_loads_late(
    hass: HomeAssistant, late: str
) -> None:
    """An occupancy sensor that reports only after startup, or reports a cycle
    whose start it did not see, does not raise a light turned off by hand."""
    entry = _porch()
    await _manual_off_while_occupied(hass, entry)
    if late == "placeholder":
        hass.states.async_set(OCCUPANCY, "unavailable", {"restored": True})
    else:
        hass.states.async_set(OCCUPANCY, "off", {"last_on_time": None})
    await restart_entries(hass, entry)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"

    await _set(hass, OCCUPANCY, "on", last_on_time=None)
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_restart_outside_the_window_keeps_a_manual_off_over_presence(
    hass: HomeAssistant, freezer
) -> None:
    """Outside its window a standby light has no standby to suppress, and a
    manual off over presence still holds across a restart like any light's."""
    entry = _porch(outside={CONF_LIGHT_TIMEOUT: 30, CONF_OCCUPANCY_ENTITY: OCCUPANCY})
    await _setup_porch(hass, entry, schedule="off")
    await _set(hass, OCCUPANCY, "on")
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    await _tick(hass, freezer, 5)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False
    await _tick(hass, freezer, 10)

    await restart_entries(hass, entry)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE

    await _set(hass, OCCUPANCY, "off")
    await _set(hass, OCCUPANCY, "on")
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.parametrize("combined", [False, True], ids=["plain", "combined"])
@pytest.mark.parametrize(
    ("downtime", "suppressed"),
    [(timedelta(hours=1), True), (timedelta(days=1), False)],
    ids=["same_window", "later_window"],
)
async def test_restart_keeps_a_manual_off_only_within_its_window(
    hass: HomeAssistant,
    freezer,
    combined: bool,
    downtime: timedelta,
    suppressed: bool,
) -> None:
    """Downtime that spans into a later window brings standby back."""
    freezer.move_to("2026-03-02 22:00:00-08:00")
    schedules = [
        MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
                CONF_NAME: "Night" if combined else "Settings Schedule",
                CONF_TIME_WINDOWS: [
                    {"start": {"time": "21:00"}, "end": {"time": "07:00"}}
                ],
            },
        )
    ]
    if combined:
        schedules.append(
            MockConfigEntry(
                domain=DOMAIN,
                data={
                    CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_SCHEDULE,
                    CONF_NAME: "Settings Schedule",
                    CONF_SCHEDULE_INPUTS: ["binary_sensor.night"],
                },
            )
        )
    entry = _porch()
    hass.states.async_set(REAL, "off")
    hass.states.async_set(OCCUPANCY, "off")
    await setup_entries(hass, *schedules, entry)
    await settle(hass)
    _assert_standby(hass)
    window = _attrs(hass)[ATTR_ACTIVE_SETTINGS_WINDOW]
    assert window == hass.states.get(SCHEDULE).attributes["current_window_start"]
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True

    freezer.tick(downtime)
    await restart_entries(hass, *schedules, entry)
    await settle(hass)

    assert hass.states.get(SCHEDULE).state == "on"
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is suppressed
    assert (_attrs(hass)[ATTR_ACTIVE_SETTINGS_WINDOW] == window) is suppressed
    assert _attrs(hass)["molight_state"] == (
        STATE_IDLE if suppressed else STATE_STANDBY
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("restored_state", "restored_attrs"),
    [
        (
            "off",
            {
                ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_OUTSIDE,
                ATTR_STANDBY_SUPPRESSED: True,
            },
        ),
        ("off", {}),
    ],
    ids=["window_started_while_down", "first_start"],
)
async def test_startup_inside_the_window_lights_standby(
    hass: HomeAssistant, restored_state: str, restored_attrs: dict
) -> None:
    """An off light comes on at standby when no manual off stands."""
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                restored_state,
                {**restored_attrs, ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE},
            )
        ],
    )
    await _setup_porch(hass, _porch())
    _assert_standby(hass)
    assert _attrs(hass)[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("illuminance", "state", "level"),
    [
        (None, STATE_OCCUPIED, BOOST),
        (ILLUMINANCE_MODE_GATE, STATE_STANDBY, STANDBY),
        (ILLUMINANCE_MODE_CONTROL, STATE_IDLE, None),
    ],
    ids=["dark", "bright_gate", "bright_control"],
)
async def test_startup_with_the_door_already_open_raises_standby(
    hass: HomeAssistant,
    freezer,
    illuminance: str | None,
    state: str,
    level: int | None,
) -> None:
    """A door open at startup sends no opening edge, so the seed raises the light."""
    door = "binary_sensor.front_door"
    lux = "binary_sensor.bright"
    hass.states.async_set(door, "on")
    hass.states.async_set(lux, "on")
    inside = {CONF_DOOR_ENTITY: door, CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE}
    if illuminance:
        inside |= {CONF_ILLUMINANCE_ENTITY: lux, CONF_ILLUMINANCE_MODE: illuminance}
    calls = await _setup_porch(hass, _porch(inside=inside))

    assert _attrs(hass)["molight_state"] == state
    if level is None:
        assert _light_calls(calls, "turn_on") == []
        return
    assert _light_calls(calls, "turn_on")[-1]["brightness"] == level
    if state != STATE_OCCUPIED:
        return
    # The open door holds the raised light; closing it starts the timeout.
    await _tick(hass, freezer, 31)
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    await _set(hass, door, "off")
    await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_restart_with_presence_raises_a_standby_light(
    hass: HomeAssistant,
) -> None:
    """Motion that started while Home Assistant was down raises standby."""
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                    "molight_state": STATE_STANDBY,
                },
            )
        ],
    )
    hass.states.async_set(REAL, "on", {"brightness": STANDBY})
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(OCCUPANCY, "on")
    calls = record_service_calls(hass)
    await setup_entries(hass, _porch())
    await settle(hass)

    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _light_calls(calls, "turn_on")[-1]["brightness"] == BOOST


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("illuminance", "bright", "held", "state", "level"),
    [
        (ILLUMINANCE_MODE_GATE, False, False, STATE_OCCUPIED, BOOST),
        (ILLUMINANCE_MODE_GATE, True, False, STATE_STANDBY, STANDBY),
        (ILLUMINANCE_MODE_GATE, True, True, STATE_STANDBY, STANDBY),
        (ILLUMINANCE_MODE_CONTROL, False, True, STATE_OCCUPIED, BOOST),
        (ILLUMINANCE_MODE_CONTROL, True, True, STATE_STANDBY, STANDBY),
    ],
    ids=["dark", "bright_gate", "bright_gate_held", "dark_held", "bright_control_held"],
)
async def test_restart_with_the_door_open_raises_standby_only_when_dark(
    hass: HomeAssistant,
    illuminance: str,
    bright: bool,
    held: bool,
    state: str,
    level: int,
) -> None:
    """A door opened while down raises restored standby, unless it is bright."""
    door = "binary_sensor.front_door"
    lux = "binary_sensor.bright"
    hold = "input_boolean.guests"
    hass.states.async_set(door, "on")
    hass.states.async_set(lux, "on" if bright else "off")
    hass.states.async_set(hold, "on" if held else "off")
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                    "molight_state": STATE_STANDBY,
                },
            )
        ],
    )
    hass.states.async_set(REAL, "on", {"brightness": STANDBY})
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(OCCUPANCY, "off")
    calls = record_service_calls(hass)
    await setup_entries(
        hass,
        _porch(
            inside={
                CONF_DOOR_ENTITY: door,
                CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
                CONF_ILLUMINANCE_ENTITY: lux,
                CONF_ILLUMINANCE_MODE: illuminance,
                CONF_HOLD_ENTITIES: [hold],
            }
        ),
    )
    await settle(hass)

    assert _attrs(hass)["molight_state"] == state
    assert _light_calls(calls, "turn_on")[-1]["brightness"] == level


@pytest.mark.asyncio
async def test_restart_while_bright_in_control_mode_turns_standby_off(
    hass: HomeAssistant,
) -> None:
    """Standby restored into daylight waits for darkness."""
    illuminance = "binary_sensor.porch_bright"
    hass.states.async_set(illuminance, "on")
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                    "molight_state": STATE_STANDBY,
                },
            )
        ],
    )
    hass.states.async_set(REAL, "on", {"brightness": STANDBY})
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(OCCUPANCY, "off")
    await setup_entries(
        hass,
        _porch(
            inside={
                CONF_ILLUMINANCE_ENTITY: illuminance,
                CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
            }
        ),
    )
    await settle(hass)

    assert hass.states.get(VIRTUAL).state == "off"
    await _set(hass, illuminance, "off")
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_restart_outside_the_window_drops_a_restored_standby(
    hass: HomeAssistant, freezer
) -> None:
    """A window that ended while down hands the light to the end action."""
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                    "molight_state": STATE_STANDBY,
                },
            )
        ],
    )
    hass.states.async_set(REAL, "on", {"brightness": STANDBY})
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(OCCUPANCY, "off")
    await setup_entries(hass, _porch(end_action=SCHEDULE_END_ACTION_KEEP))
    await settle(hass)

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    await _tick(hass, freezer, 31)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_door_closing_at_standby_starts_no_countdown(
    hass: HomeAssistant,
) -> None:
    """A door that never raised the light has no countdown to start."""
    door = "binary_sensor.front_door"
    illuminance = "binary_sensor.porch_bright"
    hass.states.async_set(door, "off")
    hass.states.async_set(illuminance, "on")
    await _setup_porch(
        hass,
        _porch(
            inside={
                CONF_DOOR_ENTITY: door,
                CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
                CONF_ILLUMINANCE_ENTITY: illuminance,
                CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_GATE,
            }
        ),
    )
    await _set(hass, door, "on")
    _assert_standby(hass)
    await _set(hass, door, "off")
    _assert_standby(hass)


@pytest.mark.asyncio
async def test_member_loading_after_startup_gets_standby(hass: HomeAssistant) -> None:
    """A slow member's first off is not a manual off of the standby it missed."""
    hass.states.async_set(REAL, "unavailable", {"restored": True})
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(OCCUPANCY, "off")
    calls = record_service_calls(hass)
    await setup_entries(hass, _porch())
    await settle(hass)
    _assert_standby(hass)
    sent = len(_light_calls(calls, "turn_on"))

    await _set(hass, REAL, "off")

    _assert_standby(hass)
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False
    assert len(_light_calls(calls, "turn_on")) == sent + 1


@pytest.mark.asyncio
async def test_member_loading_after_startup_reports_the_standby_color(
    hass: HomeAssistant,
) -> None:
    """A late member's color modes arrive with its reply; the color follows."""
    hass.states.async_set(REAL, "unavailable", {"restored": True})
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(OCCUPANCY, "off")
    _record_light_contexts(hass)
    await setup_entries(
        hass,
        _porch(
            inside={CONF_STANDBY_COLOR_TEMP: None, CONF_STANDBY_RGB_COLOR: [0, 0, 255]}
        ),
    )
    await settle(hass)
    assert _attrs(hass).get("hs_color") is None

    hass.states.async_set(
        REAL,
        "on",
        {
            "brightness": STANDBY,
            "color_mode": "rgb",
            "rgb_color": (0, 0, 255),
            "hs_color": (240.0, 100.0),
            "supported_color_modes": ["rgb"],
        },
        context=hass.data["standby_test_contexts"][-1],
    )
    await settle(hass)

    _assert_standby(hass)
    assert tuple(_attrs(hass)["hs_color"]) == (240.0, 100.0)


class _ParkedSelect:
    """A select service whose chosen call blocks until released."""

    def __init__(self, hass: HomeAssistant, *, park: int) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self._park = park
        hass.states.async_set("select.scene", "Day", {"options": ["Day", "Night"]})
        hass.services.async_register("select", "select_option", self._select)

    async def _select(self, _call: ServiceCall) -> None:
        self.calls += 1
        if self.calls == self._park:
            self.started.set()
            await self.release.wait()


_SCENE = {
    CONF_TURN_ON_SELECT_ENTITY: "select.scene",
    CONF_TURN_ON_SELECT_OPTION: "Night",
}


async def _drain(hass: HomeAssistant) -> None:
    """Let dispatches run; settle() would wait for the parked select call."""
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_raise_during_a_waiting_standby_keeps_the_raised_level(
    hass: HomeAssistant,
) -> None:
    """A standby still waiting for its selection does not undo a later raise."""
    select = _ParkedSelect(hass, park=1)
    calls = await _setup_porch(hass, _porch(inside=_SCENE), schedule="off")
    hass.states.async_set(SCHEDULE, "on")
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set(OCCUPANCY, "on")
    await _drain(hass)
    assert [c["brightness"] for c in _light_calls(calls, "turn_on")] == [BOOST]
    select.release.set()
    await settle(hass)

    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _attrs(hass)["brightness"] == BOOST
    assert [c["brightness"] for c in _light_calls(calls, "turn_on")] == [BOOST]


@pytest.mark.asyncio
async def test_standby_during_a_waiting_raise_keeps_standby(
    hass: HomeAssistant, freezer
) -> None:
    """A raise still waiting for its selection does not undo a later standby."""
    select = _ParkedSelect(hass, park=2)
    calls = await _setup_porch(hass, _porch(inside=_SCENE))
    # The slow member has not come on yet, so the raise selects again.
    hass.states.async_set(OCCUPANCY, "on")
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set(OCCUPANCY, "off", {"last_clear_false_detection": True})
    await _drain(hass)
    assert _attrs(hass)["molight_state"] == STATE_COUNTDOWN
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await _drain(hass)
    assert _attrs(hass)["molight_state"] == STATE_STANDBY
    select.release.set()
    await settle(hass)

    _assert_standby(hass)
    assert [c["brightness"] for c in _light_calls(calls, "turn_on")] == [
        STANDBY,
        STANDBY,
    ]


@pytest.mark.asyncio
async def test_wall_turn_on_during_a_waiting_standby_keeps_its_level(
    hass: HomeAssistant, freezer
) -> None:
    """A standby still waiting for its selection does not undo a turn-on at
    the wall, which runs the timeout back to standby."""
    select = _ParkedSelect(hass, park=1)
    calls = await _setup_porch(hass, _porch(inside=_SCENE), schedule="off")
    hass.states.async_set(SCHEDULE, "on")
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set(REAL, "on", {"brightness": 200})
    await _drain(hass)
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["brightness"] == 200
    select.release.set()
    await settle(hass)

    assert _light_calls(calls, "turn_on") == []
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["brightness"] == 200
    await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["brightness", "color"])
async def test_wall_change_during_a_waiting_standby_resend_keeps_it(
    hass: HomeAssistant, change: str
) -> None:
    """A dim or recolour at the wall while standby is re-sent to a member
    that came back is not undone by that re-send."""
    select = _ParkedSelect(hass, park=2)
    calls = await _setup_porch(hass, _porch(inside=_SCENE))
    await _echo_standby(hass)
    await _set(hass, REAL, "unavailable")
    hass.states.async_set(REAL, "on", {"brightness": STANDBY})
    await asyncio.wait_for(select.started.wait(), 2)
    sent = len(_light_calls(calls, "turn_on"))

    level = 200 if change == "brightness" else STANDBY
    changed = {} if change == "brightness" else {"hs_color": (200.0, 50.0)}
    hass.states.async_set(REAL, "on", {"brightness": level, **changed})
    await _drain(hass)
    select.release.set()
    await settle(hass)

    assert len(_light_calls(calls, "turn_on")) == sent
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["brightness"] == level
    assert _attrs(hass)["last_color_change_physical" if changed else "brightness"]


@pytest.mark.asyncio
async def test_wall_turn_on_during_a_waiting_manual_on_keeps_its_level(
    hass: HomeAssistant, freezer
) -> None:
    """After a manual off, a turn-on at the wall overtakes a manual turn-on
    still waiting for its selection: the wall level stands, standby is
    rejoined, and the timeout ends back at it."""
    select = _ParkedSelect(hass, park=2)
    calls = await _setup_porch(hass, _porch(inside=_SCENE))
    await _echo_standby(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await _set(hass, REAL, "off")
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True
    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 100}, blocking=True
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set(REAL, "on", {"brightness": 200})
    await _drain(hass)
    select.release.set()
    await turn_on
    await settle(hass)

    to_real = [c for c in _light_calls(calls, "turn_on") if c["entity_id"] == [REAL]]
    assert [c["brightness"] for c in to_real] == [STANDBY]
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["brightness"] == 200
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False
    await _tick(hass, freezer, 31)
    _assert_standby(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", ["by_the_device", "under_the_select_call"])
async def test_selection_lighting_the_light_does_not_take_standby_over(
    hass: HomeAssistant, reported: str
) -> None:
    """A preset that lights the real light while standby waits for that select
    call is not a turn-on at the wall, whether the change arrives under the
    select call or as a push of the device both entities belong to: the
    light rests at standby, and standby is still sent."""
    if reported == "by_the_device":
        wled = MockConfigEntry(domain="wled")
        wled.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=wled.entry_id, identifiers={("wled", "porch")}
        )
        for entity_id in ("select.scene", REAL):
            domain, object_id = entity_id.split(".")
            registered = er.async_get(hass).async_get_or_create(
                domain,
                "wled",
                entity_id,
                suggested_object_id=object_id,
                device_id=device.id,
            )
            assert registered.entity_id == entity_id
    select = _ParkedSelect(hass, park=1)
    calls = await _setup_porch(hass, _porch(inside=_SCENE), schedule="off")
    select_calls: list[Context] = []

    @callback
    def _record(event: Event) -> None:
        if event.data["domain"] == "select":
            select_calls.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    hass.states.async_set(SCHEDULE, "on")
    await asyncio.wait_for(select.started.wait(), 2)

    context = None if reported == "by_the_device" else select_calls[-1]
    for brightness in (200, 180):  # on, then the preset's level
        hass.states.async_set(REAL, "on", {"brightness": brightness}, context=context)
        await _drain(hass)
    select.release.set()
    await settle(hass)

    _assert_standby(hass)
    assert [c["brightness"] for c in _light_calls(calls, "turn_on")] == [STANDBY]
    assert _attrs(hass)["last_on_physical"] is None
    assert _attrs(hass)["last_brightness_change_physical"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("placeholder", [{}, {"restored": True}], ids=["back", "new"])
@pytest.mark.parametrize("waiting", ["standby", "raise"])
async def test_member_reporting_in_off_during_a_waiting_turn_on_gets_it(
    hass: HomeAssistant, placeholder: dict, waiting: str
) -> None:
    """A real light that loads, or comes back, as off while standby or a raise
    waits for its selection was not turned off by hand: standby stays on and
    the level waited for is sent."""
    select = _ParkedSelect(hass, park=1 if waiting == "standby" else 2)
    hass.states.async_set(REAL, "unavailable", placeholder)
    hass.states.async_set(SCHEDULE, "off" if waiting == "standby" else "on")
    hass.states.async_set(OCCUPANCY, "off")
    calls = record_service_calls(hass)
    await setup_entries(hass, _porch(inside=_SCENE))
    await settle(hass)
    hass.states.async_set(SCHEDULE if waiting == "standby" else OCCUPANCY, "on")
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set(REAL, "off")
    await _drain(hass)
    select.release.set()
    await settle(hass)

    level = STANDBY if waiting == "standby" else BOOST
    assert hass.states.get(VIRTUAL).state == "on"
    assert _attrs(hass)["molight_state"] == (
        STATE_STANDBY if waiting == "standby" else STATE_OCCUPIED
    )
    assert _attrs(hass)["brightness"] == level
    assert _light_calls(calls, "turn_on")[-1]["brightness"] == level
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False
    assert _attrs(hass)["last_off_manual"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["startup", "boundary", "restored"])
@pytest.mark.parametrize("occupied", [False, True], ids=["clear", "occupied"])
@pytest.mark.parametrize("door_open", [False, True], ids=["closed", "open"])
@pytest.mark.parametrize("maintained", [False, True], ids=["unheld", "maintained"])
@pytest.mark.parametrize("bright", [False, True], ids=["dark", "bright"])
@pytest.mark.parametrize("control", [False, True], ids=["gate", "control"])
@pytest.mark.parametrize("held", [False, True], ids=["auto_off", "keep_on"])
async def test_what_the_inside_settings_come_on_at(
    hass: HomeAssistant,
    route: str,
    occupied: bool,
    door_open: bool,
    maintained: bool,
    bright: bool,
    control: bool,
    held: bool,
) -> None:
    """Startup, the start boundary and a restart at standby agree on the level.

    Bright blocks a rise, and in control mode keeps standby off unless a hold
    keeps a restored level; occupancy or an open door raise a dark light; the
    maintain sensor alone never does.
    """
    door = "binary_sensor.front_door"
    maintain = "binary_sensor.porch_maintain"
    illuminance = "binary_sensor.porch_bright"
    hold = "input_boolean.guests"
    restored = route == "restored"
    hass.states.async_set(REAL, "on" if restored else "off", {"brightness": STANDBY})
    hass.states.async_set(SCHEDULE, "off" if route == "boundary" else "on")
    for entity_id, on in (
        (OCCUPANCY, occupied),
        (door, door_open),
        (maintain, maintained),
        (illuminance, bright),
        (hold, held),
    ):
        hass.states.async_set(entity_id, "on" if on else "off")
    if restored:
        mock_restore_cache(
            hass,
            [
                State(
                    VIRTUAL,
                    "on",
                    {
                        ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                        ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                        "molight_state": STATE_STANDBY,
                        "brightness": STANDBY,
                    },
                )
            ],
        )
    await setup_entries(
        hass,
        _porch(
            inside={
                CONF_DOOR_ENTITY: door,
                CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
                CONF_MAINTAIN_OCCUPANCY_ENTITY: maintain,
                CONF_ILLUMINANCE_ENTITY: illuminance,
                CONF_ILLUMINANCE_MODE: (
                    ILLUMINANCE_MODE_CONTROL if control else ILLUMINANCE_MODE_GATE
                ),
                CONF_HOLD_ENTITIES: [hold],
            }
        ),
    )
    await settle(hass)
    if route == "boundary":
        await _set(hass, SCHEDULE, "on")

    if bright and control and not (restored and held):
        assert _attrs(hass)["molight_state"] == STATE_IDLE
        assert hass.states.get(VIRTUAL).state == "off"
    elif not bright and (occupied or door_open):
        assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
        assert _attrs(hass)["brightness"] == BOOST
    else:
        _assert_standby(hass)
