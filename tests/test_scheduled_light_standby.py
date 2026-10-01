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
    CONF_AUTO_ON_TRANSITION,
    CONF_DOOR_ENTITY,
    CONF_DOOR_MODE,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_TIMEOUT,
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_MODE,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHT_TIMEOUT,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_SCHEDULE_INPUTS,
    CONF_STANDBY_BRIGHTNESS,
    CONF_STANDBY_COLOR_TEMP,
    CONF_TIME_WINDOWS,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_WARN_BRIGHTNESS,
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

    await _set(hass, illuminance, "off")
    _assert_standby(hass)

    await _set(hass, illuminance, "on")
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["bright_forced_off"] is True
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is False

    await _set(hass, illuminance, "off")
    _assert_standby(hass)


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
