"""Tests for the MoLight Virtual Scheduled Light."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, ServiceCall, State
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.const import (
    ACTIVE_SETTINGS_INSIDE,
    ACTIVE_SETTINGS_OUTSIDE,
    ATTR_ACTIVE_SETTINGS,
    ATTR_ACTIVE_SETTINGS_SCHEDULE,
    ATTR_SCHEDULE_END_OFF_PENDING,
    CONF_AUTO_OFF_TRANSITION,
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_DOOR_ENTITY,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_TIMEOUT,
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_MODE,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHT_TIMEOUT,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_ENTITY,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_SCHEDULE_END_ACTION,
    CONF_SCHEDULE_ENTITY,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_TIMEOUT,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE,
    SCHEDULE_END_ACTION_KEEP,
    SCHEDULE_END_ACTION_SWITCH,
    SCHEDULE_END_ACTION_TURN_OFF,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_EFFECT,
    STATE_OCCUPIED,
    STATE_WARN,
)
from tests.conftest import make_scheduled_light_entry, settle, setup_entries

REAL = "light.real_1"
SCHEDULE = "binary_sensor.settings_schedule"
VIRTUAL = "light.scheduled_light"


@pytest.mark.asyncio
async def test_schedule_selects_settings_for_automatic_turn_on(
    hass: HomeAssistant,
) -> None:
    """Each schedule side supplies its own sensors and appearance."""
    outside_occupancy = "binary_sensor.outside_occupancy"
    inside_occupancy = "binary_sensor.inside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(outside_occupancy, "off")
    hass.states.async_set(inside_occupancy, "off")
    entry = make_scheduled_light_entry(
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: outside_occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 80,
        },
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: inside_occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 20,
        },
    )
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE

    hass.states.async_set(outside_occupancy, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["brightness"] == 204
    assert state.attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(outside_occupancy, "off")
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    hass.states.async_set(inside_occupancy, "on")
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["brightness"] == 51
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE


@pytest.mark.asyncio
async def test_schedule_change_rechecks_already_active_occupancy(
    hass: HomeAssistant,
) -> None:
    """Switching settings notices a sensor that was already on."""
    occupancy = "binary_sensor.inside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(occupancy, "on")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 25,
        },
    )
    await setup_entries(hass, entry)

    assert hass.states.get(VIRTUAL).state == "off"
    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["brightness"] == 64
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_schedule_end_can_force_light_off(hass: HomeAssistant) -> None:
    """The explicit end action turns off before leaving the inside profile."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 30},
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False


@pytest.mark.asyncio
async def test_schedule_end_off_still_checks_outside_sensors_for_off_light(
    hass: HomeAssistant,
) -> None:
    """turn_off on an already-off light applies the outside profile's sensors."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(occupancy, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 25,
        },
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)
    assert hass.states.get(VIRTUAL).state == "off"

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["brightness"] == 64
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_schedule_end_off_still_checks_outside_door_for_off_light(
    hass: HomeAssistant,
) -> None:
    """turn_off on an already-off light also honours an open outside door."""
    door = "binary_sensor.outside_door"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(door, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_DOOR_ENTITY: door,
            CONF_AUTO_ON_BRIGHTNESS: 25,
        },
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)
    assert hass.states.get(VIRTUAL).state == "off"

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["brightness"] == 64
    assert state.attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
async def test_schedule_end_off_leaves_forced_off_light_off(
    hass: HomeAssistant,
) -> None:
    """A light forced off at the boundary waits for the next sensor edge."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(occupancy, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_OCCUPANCY_ENTITY: occupancy},
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"

    hass.states.async_set(occupancy, "off")
    await settle(hass)
    hass.states.async_set(occupancy, "on")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"


@pytest.mark.asyncio
async def test_schedule_end_off_uses_outgoing_profile_transition(
    hass: HomeAssistant,
) -> None:
    """The boundary off fades with the inside profile being left."""
    calls: list[dict] = []
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_AUTO_OFF_TRANSITION: 1},
        inside={CONF_LIGHT_TIMEOUT: 60, CONF_AUTO_OFF_TRANSITION: 4},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.bus.async_listen(EVENT_CALL_SERVICE, lambda event: calls.append(event.data))

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)

    off_calls = [
        call
        for call in calls
        if call["domain"] == "light"
        and call["service"] == "turn_off"
        and REAL in call["service_data"].get("entity_id", [])
    ]
    assert off_calls
    assert off_calls[-1]["service_data"]["transition"] == 4.0


@pytest.mark.asyncio
async def test_schedule_end_defaults_to_preserving_state(
    hass: HomeAssistant,
) -> None:
    """An absent action retains the pre-feature Scheduled Light behavior."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    await setup_entries(hass, make_scheduled_light_entry())
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE


@pytest.mark.asyncio
async def test_schedule_end_switches_state_from_outside_occupancy_history(
    hass: HomeAssistant, freezer
) -> None:
    """Switch-state replaces the inside timer with the outside deadline."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    cleared = datetime.now(UTC) - timedelta(seconds=120)
    hass.states.async_set(
        occupancy, "off", {"latest_occupied_time": cleared.isoformat()}
    )
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={CONF_LIGHT_TIMEOUT: 30, CONF_OCCUPANCY_ENTITY: occupancy},
        inside={CONF_LIGHT_TIMEOUT: 300},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE


@pytest.mark.asyncio
async def test_schedule_end_switch_without_history_uses_full_outside_timeout(
    hass: HomeAssistant, freezer
) -> None:
    """Missing incoming occupancy history earns one fresh outside timeout."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(occupancy, "off")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={CONF_LIGHT_TIMEOUT: 30, CONF_OCCUPANCY_ENTITY: occupancy},
        inside={CONF_LIGHT_TIMEOUT: 300},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=29))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_end_switch_adopts_active_outside_occupancy(
    hass: HomeAssistant,
) -> None:
    """The fully selected outside profile holds an already-on light."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(occupancy, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={CONF_LIGHT_TIMEOUT: 30, CONF_OCCUPANCY_ENTITY: occupancy},
        inside={CONF_LIGHT_TIMEOUT: 300},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_schedule_end_switch_uses_outside_warning_settings(
    hass: HomeAssistant,
) -> None:
    """An already-due switched deadline enters the incoming warning policy."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    cleared = datetime.now(UTC) - timedelta(seconds=120)
    hass.states.async_set(
        occupancy, "off", {"latest_occupied_time": cleared.isoformat()}
    )
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_WARN_TIMEOUT: 10,
            CONF_WARN_BRIGHTNESS: 25,
        },
        inside={CONF_LIGHT_TIMEOUT: 300},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_WARN
    assert state.attributes["brightness"] == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("warning_state", "warning_elapsed"),
    [(STATE_EFFECT, 0), (STATE_WARN, 11)],
)
async def test_schedule_end_switch_replaces_inside_warning_with_outside_timer(
    hass: HomeAssistant, freezer, warning_state: str, warning_elapsed: int
) -> None:
    """Switch-state restores an inside warning before starting outside timing."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={CONF_LIGHT_TIMEOUT: 30},
        inside=_WARNING_OUTSIDE,
    )
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}
    )
    await settle(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    if warning_elapsed:
        freezer.tick(timedelta(seconds=warning_elapsed))
        async_fire_time_changed(hass)
        await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes["molight_state"] == warning_state
    assert state.attributes["pre_warn_brightness"] == 200

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 200
    assert state.attributes["pre_warn_brightness"] is None

    # The discarded inside warning cannot expire the light; the complete
    # outside timeout now owns its deadline.
    freezer.tick(timedelta(seconds=29))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_end_switch_obeys_outside_bright_control(
    hass: HomeAssistant,
) -> None:
    """Switch-state turns off for the incoming profile's bright control sensor."""
    illuminance = "binary_sensor.outside_illuminance"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(illuminance, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_ILLUMINANCE_ENTITY: illuminance,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
        },
        inside={CONF_LIGHT_TIMEOUT: 300},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE


@pytest.mark.asyncio
async def test_schedule_end_switch_reconciles_while_outside_auto_off_is_held(
    hass: HomeAssistant, freezer
) -> None:
    """An incoming hold suppresses the recalculated off until it is released."""
    occupancy = "binary_sensor.outside_occupancy"
    hold = "input_boolean.outside_keep_on"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(
        occupancy,
        "off",
        {
            "latest_occupied_time": (
                datetime.now(UTC) - timedelta(seconds=120)
            ).isoformat()
        },
    )
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_HOLD_ENTITIES: [hold],
        },
        inside={CONF_LIGHT_TIMEOUT: 300},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes["auto_off_held"] is True
    assert state.attributes["molight_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"

    # Releasing a normal auto-off hold starts the incoming profile's complete
    # timeout, just as it does when a hold begins outside a schedule boundary.
    hass.states.async_set(hold, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes["auto_off_held"] is False
    assert state.attributes["molight_state"] == STATE_ACTIVE
    freezer.tick(timedelta(seconds=29))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_restart_catches_up_missed_schedule_end_switch(
    hass: HomeAssistant,
) -> None:
    """A restored inside side proves an offline switch-state boundary occurred."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "off")
    cleared = datetime.now(UTC) - timedelta(seconds=120)
    hass.states.async_set(
        occupancy, "off", {"latest_occupied_time": cleared.isoformat()}
    )
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                },
            )
        ],
    )
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={CONF_LIGHT_TIMEOUT: 30, CONF_OCCUPANCY_ENTITY: occupancy},
        inside={CONF_LIGHT_TIMEOUT: 300},
    )

    await setup_entries(hass, entry)
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE


@pytest.mark.asyncio
async def test_schedule_end_off_waits_for_hold_release(
    hass: HomeAssistant,
) -> None:
    """A keep-on entity from the newly active outside profile defers the off."""
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    assert hass.states.get(VIRTUAL).attributes[ATTR_SCHEDULE_END_OFF_PENDING]

    hass.states.async_set(hold, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_returning_inside_cancels_deferred_schedule_end_off(
    hass: HomeAssistant,
) -> None:
    """Reopening the schedule invalidates a held off from its prior boundary."""
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False

    # Releasing a now-inactive outside hold must not apply the old boundary.
    hass.states.async_set(hold, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"


@pytest.mark.asyncio
async def test_auto_off_switch_defers_schedule_end_until_reenabled(
    hass: HomeAssistant,
) -> None:
    """The shared companion switch holds and later applies a boundary off."""
    switch = "switch.scheduled_light_auto_off"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    entry = make_scheduled_light_entry(schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF)
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch})
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["auto_off_held"] is True

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False


@pytest.mark.asyncio
async def test_manual_off_consumes_deferred_schedule_end_off(
    hass: HomeAssistant,
) -> None:
    """Turning the light off while a boundary off is held discards that off."""
    switch = "switch.scheduled_light_auto_off"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    entry = make_scheduled_light_entry(schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF)
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch})
    await settle(hass)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False

    # A later manual on-period, still outside the window, must not be cut by
    # the stale boundary when the hold cycles again.
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await settle(hass)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch})
    await settle(hass)
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
async def test_manual_off_discards_deferred_schedule_end_off_across_restart(
    hass: HomeAssistant,
) -> None:
    """The boundary consumed by a manual off cannot be restored after a reload."""
    switch = "switch.scheduled_light_auto_off"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    entry = make_scheduled_light_entry(schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF)
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch})
    await settle(hass)
    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)
    # A later manual on-period is what the restart finds: still held, so a
    # surviving boundary would park it and cut it on release.
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.states.async_set(REAL, "on")  # no light platform: mirror the turn-on
    await settle(hass)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["auto_off_held"] is True
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
async def test_schedule_change_rechecks_already_open_door(
    hass: HomeAssistant,
) -> None:
    """An open-mode door selected by the new settings triggers the light."""
    door = "binary_sensor.inside_door"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(door, "on")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_DOOR_ENTITY: door,
            CONF_AUTO_ON_BRIGHTNESS: 25,
        },
    )
    await setup_entries(hass, entry)

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["brightness"] == 64


@pytest.mark.asyncio
async def test_schedule_change_releases_old_occupancy_hold(
    hass: HomeAssistant, freezer
) -> None:
    """An inactive settings sensor cannot keep holding the light."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(occupancy, "off")
    entry = make_scheduled_light_entry(
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: occupancy,
        },
        inside={CONF_LIGHT_TIMEOUT: 10},
    )
    await setup_entries(hass, entry)
    hass.states.async_set(occupancy, "on")
    await settle(hass)

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes["molight_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_change_releases_removed_keep_on_hold(
    hass: HomeAssistant, freezer
) -> None:
    """Removing the active side's keep-on entity starts its new timeout."""
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
        inside={CONF_LIGHT_TIMEOUT: 10},
    )
    await setup_entries(hass, entry)

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)

    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_change_does_not_restyle_an_on_light(
    hass: HomeAssistant,
) -> None:
    """Selecting settings alone does not apply their turn-on appearance."""
    hass.states.async_set(REAL, "on", {"brightness": 180})
    hass.states.async_set(SCHEDULE, "off")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_AUTO_ON_BRIGHTNESS: 80},
        inside={CONF_LIGHT_TIMEOUT: 60, CONF_AUTO_ON_BRIGHTNESS: 10},
    )
    await setup_entries(hass, entry)

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.attributes["brightness"] == 180


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_state"),
    [
        (ILLUMINANCE_MODE_CONTROL, "off"),
        (ILLUMINANCE_MODE_GATE, "on"),
    ],
)
async def test_schedule_change_applies_selected_illuminance_mode(
    hass: HomeAssistant, mode: str, expected_state: str
) -> None:
    """A selected bright sensor forces off only when its mode is control."""
    illuminance = "binary_sensor.inside_illuminance"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(illuminance, "on")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_ILLUMINANCE_ENTITY: illuminance,
            CONF_ILLUMINANCE_MODE: mode,
        },
    )
    await setup_entries(hass, entry)

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.state == expected_state


@pytest.mark.asyncio
async def test_schedule_change_bright_sensor_leaves_off_light_off(
    hass: HomeAssistant,
) -> None:
    """A newly selected bright sensor blocks the new side's active occupancy."""
    occupancy = "binary_sensor.inside_occupancy"
    illuminance = "binary_sensor.inside_illuminance"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(occupancy, "on")
    hass.states.async_set(illuminance, "on")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_ILLUMINANCE_ENTITY: illuminance,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_GATE,
        },
    )
    await setup_entries(hass, entry)

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.state == "off"

    # Going dark under the new settings lets the still-active occupancy fire.
    hass.states.async_set(illuminance, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"


async def _run_into_warn_stage(hass: HomeAssistant, freezer) -> None:
    """Turn the virtual light on and advance past its effect stage into WARN."""
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}
    )
    await settle(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes["molight_state"] == STATE_WARN
    assert state.attributes["pre_warn_brightness"] == 200


_WARNING_OUTSIDE = {
    CONF_LIGHT_TIMEOUT: 60,
    CONF_EFFECT_TIMEOUT: 10,
    CONF_EFFECT_BRIGHTNESS: 0,
    CONF_WARN_TIMEOUT: 30,
    CONF_WARN_BRIGHTNESS: 100,
}


@pytest.mark.asyncio
async def test_schedule_change_hold_mid_warning_restores_light(
    hass: HomeAssistant, freezer
) -> None:
    """A keep-on entity from the new side aborts a running warning sequence."""
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        outside=_WARNING_OUTSIDE,
        inside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
    )
    await setup_entries(hass, entry)
    await _run_into_warn_stage(hass, freezer)

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.state == "on"
    assert state.attributes["auto_off_held"] is True
    assert state.attributes["molight_state"] == STATE_ACTIVE
    # The pre-warning brightness is restored and the snapshot cleared.
    assert state.attributes["brightness"] == 200
    assert state.attributes["pre_warn_brightness"] is None

    # Held: the interrupted grace period never expires the light.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"


@pytest.mark.asyncio
async def test_held_schedule_end_mid_warning_restores_then_turns_off(
    hass: HomeAssistant, freezer
) -> None:
    """A held turn_off boundary aborts warning before applying on release."""
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
        inside=_WARNING_OUTSIDE,
    )
    await setup_entries(hass, entry)
    await _run_into_warn_stage(hass, freezer)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 200
    assert state.attributes["pre_warn_brightness"] is None

    hass.states.async_set(hold, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False


@pytest.mark.asyncio
async def test_schedule_may_also_serve_as_a_keep_on_entity(
    hass: HomeAssistant, freezer
) -> None:
    """The settings schedule can double as a role entity on one side."""
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "off")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 10},
        inside={CONF_LIGHT_TIMEOUT: 10, CONF_HOLD_ENTITIES: [SCHEDULE]},
    )
    await setup_entries(hass, entry)
    assert hass.states.get(VIRTUAL).attributes["auto_off_held"] is False

    # Entering the schedule selects the inside side, where the schedule
    # itself is the keep-on entity — and it is on.
    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.attributes["auto_off_held"] is True
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"

    # Leaving it drops both the side and the hold; a fresh timeout runs.
    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes["auto_off_held"] is False
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_change_hold_cancels_running_timer(
    hass: HomeAssistant, freezer
) -> None:
    """A keep-on entity from the new side suspends a plain running timeout."""
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 10},
        inside={CONF_LIGHT_TIMEOUT: 10, CONF_HOLD_ENTITIES: [hold]},
    )
    await setup_entries(hass, entry)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes["auto_off_held"] is True
    assert state.attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"

    # Releasing the hold under the new side starts a fresh full timeout.
    hass.states.async_set(hold, "off")
    await settle(hass)
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_change_occupancy_mid_warning_adopts_light(
    hass: HomeAssistant, freezer
) -> None:
    """Active occupancy from the new side rescues a light mid-warning."""
    occupancy = "binary_sensor.inside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(occupancy, "on")
    entry = make_scheduled_light_entry(
        outside=_WARNING_OUTSIDE,
        inside={CONF_LIGHT_TIMEOUT: 10, CONF_OCCUPANCY_ENTITY: occupancy},
    )
    await setup_entries(hass, entry)
    await _run_into_warn_stage(hass, freezer)

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED
    assert state.attributes["brightness"] == 200
    assert state.attributes["pre_warn_brightness"] is None

    # Occupancy clearing starts the new side's own (short) timeout.
    hass.states.async_set(occupancy, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_COUNTDOWN
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_each_schedule_side_applies_its_turn_on_selection(
    hass: HomeAssistant,
) -> None:
    """Automatic turn-ons use the select option from the active settings."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    outside_occupancy = "binary_sensor.outside_occupancy"
    inside_occupancy = "binary_sensor.inside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(outside_occupancy, "off")
    hass.states.async_set(inside_occupancy, "off")
    entry = make_scheduled_light_entry(
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: outside_occupancy,
            CONF_TURN_ON_SELECT_ENTITY: "select.light_mode",
            CONF_TURN_ON_SELECT_OPTION: "Day",
        },
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: inside_occupancy,
            CONF_TURN_ON_SELECT_ENTITY: "select.light_mode",
            CONF_TURN_ON_SELECT_OPTION: "Night",
        },
    )
    await setup_entries(hass, entry)

    hass.states.async_set(outside_occupancy, "on")
    await settle(hass)
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    hass.states.async_set(outside_occupancy, "off")
    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    hass.states.async_set(inside_occupancy, "on")
    await settle(hass)

    assert selected == ["Day", "Night"]


@pytest.mark.asyncio
async def test_options_reload_uses_current_schedule_side(
    hass: HomeAssistant, freezer
) -> None:
    """A reload selects the current side and applies that side's new timeout."""
    hass.states.async_set(REAL, "on", {"brightness": 180})
    hass.states.async_set(SCHEDULE, "on")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={CONF_LIGHT_TIMEOUT: 30},
    )
    await setup_entries(hass, entry)

    options = {
        key: value for key, value in entry.data.items() if key != CONF_ENTITY_TYPE
    }
    options[CONF_OUTSIDE_SCHEDULE_SETTINGS] = {CONF_LIGHT_TIMEOUT: 120}
    options[CONF_INSIDE_SCHEDULE_SETTINGS] = {CONF_LIGHT_TIMEOUT: 10}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.attributes["brightness"] == 180

    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_rapid_schedule_changes_finish_with_latest_settings(
    hass: HomeAssistant,
) -> None:
    """Queued actions from intermediate sides cannot win after the last edge."""
    occupancy = "binary_sensor.inside_occupancy"
    illuminance = "binary_sensor.outside_illuminance"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(occupancy, "on")
    hass.states.async_set(illuminance, "on")
    entry = make_scheduled_light_entry(
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_ILLUMINANCE_ENTITY: illuminance,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
        },
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 20,
        },
    )
    await setup_entries(hass, entry)

    # Do not settle between edges: their async light service calls overlap.
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.state == "on"
    assert state.attributes["brightness"] == 51


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_state", ["unavailable", "unknown"])
async def test_schedule_unavailable_keeps_last_selected_settings(
    hass: HomeAssistant, bad_state: str
) -> None:
    """A schedule blip keeps the last side active until a valid state returns."""
    outside_occupancy = "binary_sensor.outside_occupancy"
    inside_occupancy = "binary_sensor.inside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(outside_occupancy, "off")
    hass.states.async_set(inside_occupancy, "off")
    entry = make_scheduled_light_entry(
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: outside_occupancy,
        },
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: inside_occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 20,
        },
    )
    await setup_entries(hass, entry)

    hass.states.async_set(SCHEDULE, bad_state)
    await settle(hass)
    assert (
        hass.states.get(VIRTUAL).attributes[ATTR_ACTIVE_SETTINGS]
        == ACTIVE_SETTINGS_INSIDE
    )

    # The inactive outside sensor is still subscribed, but must be ignored.
    hass.states.async_set(outside_occupancy, "on")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"

    # The retained inside settings remain fully active during the blip.
    hass.states.async_set(inside_occupancy, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["brightness"] == 51

    # Recovery to a valid opposite state switches normally.
    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes[ATTR_ACTIVE_SETTINGS] == (
        ACTIVE_SETTINGS_OUTSIDE
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_state", ["unavailable", "unknown"])
async def test_schedule_blip_does_not_restart_running_timeout(
    hass: HomeAssistant, freezer, bad_state: str
) -> None:
    """Dropping out and recovering on the same side leaves its timer alone."""
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "on")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={CONF_LIGHT_TIMEOUT: 10},
    )
    await setup_entries(hass, entry)

    freezer.tick(timedelta(seconds=4))
    async_fire_time_changed(hass)
    hass.states.async_set(SCHEDULE, bad_state)
    await settle(hass)
    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)

    freezer.tick(timedelta(seconds=7))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("schedule_state", "restored", "expected", "expected_brightness"),
    [
        ("on", ACTIVE_SETTINGS_OUTSIDE, ACTIVE_SETTINGS_INSIDE, 51),
        ("off", ACTIVE_SETTINGS_INSIDE, ACTIVE_SETTINGS_OUTSIDE, 204),
    ],
)
async def test_restart_valid_schedule_state_overrides_restore(
    hass: HomeAssistant,
    schedule_state: str,
    restored: str,
    expected: str,
    expected_brightness: int,
) -> None:
    """The schedule's current state chooses the settings used to seed."""
    outside_occupancy = "binary_sensor.outside_occupancy"
    inside_occupancy = "binary_sensor.inside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, schedule_state)
    hass.states.async_set(outside_occupancy, "on")
    hass.states.async_set(inside_occupancy, "on")
    mock_restore_cache(hass, [State(VIRTUAL, "off", {ATTR_ACTIVE_SETTINGS: restored})])
    entry = make_scheduled_light_entry(
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: outside_occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 80,
        },
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: inside_occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 20,
        },
    )
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == expected
    assert state.state == "on"
    assert state.attributes["brightness"] == expected_brightness


@pytest.mark.asyncio
async def test_restart_catches_up_missed_schedule_end_off(
    hass: HomeAssistant,
) -> None:
    """A restored inside profile plus an off schedule applies the missed edge."""
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "off")
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                },
            )
        ],
    )
    entry = make_scheduled_light_entry(schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF)
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False


@pytest.mark.asyncio
async def test_options_reload_swapping_schedule_does_not_turn_light_off(
    hass: HomeAssistant,
) -> None:
    """Pointing an on light at a schedule that is off crosses no boundary."""
    other = "binary_sensor.other_schedule"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(other, "off")
    entry = make_scheduled_light_entry(schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF)
    await setup_entries(hass, entry)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.attributes[ATTR_ACTIVE_SETTINGS_SCHEDULE] == SCHEDULE

    options = {
        key: value for key, value in entry.data.items() if key != CONF_ENTITY_TYPE
    }
    options[CONF_SCHEDULE_ENTITY] = other
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes[ATTR_ACTIVE_SETTINGS_SCHEDULE] == other
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False

    # The new schedule's own boundary still applies.
    hass.states.async_set(other, "on")
    await settle(hass)
    hass.states.async_set(other, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_options_reload_to_unavailable_schedule_does_not_invent_boundary(
    hass: HomeAssistant,
) -> None:
    """A different schedule recovering as off was never observed to end."""
    other = "binary_sensor.other_schedule"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(other, "unavailable")
    entry = make_scheduled_light_entry(schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF)
    await setup_entries(hass, entry)
    assert hass.states.get(VIRTUAL).attributes[ATTR_ACTIVE_SETTINGS] == (
        ACTIVE_SETTINGS_INSIDE
    )

    options = {
        key: value for key, value in entry.data.items() if key != CONF_ENTITY_TYPE
    }
    options[CONF_SCHEDULE_ENTITY] = other
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes[ATTR_ACTIVE_SETTINGS_SCHEDULE] == other

    hass.states.async_set(other, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False


@pytest.mark.asyncio
async def test_schedule_swap_preserves_already_pending_boundary(
    hass: HomeAssistant,
) -> None:
    """A boundary actually observed before a schedule edit remains due."""
    other = "binary_sensor.other_schedule"
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(other, "off")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)

    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    options = {
        key: value for key, value in entry.data.items() if key != CONF_ENTITY_TYPE
    }
    options[CONF_SCHEDULE_ENTITY] = other
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS_SCHEDULE] == other
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    hass.states.async_set(hold, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_restart_with_held_pending_off_reports_maintained_hold(
    hass: HomeAssistant,
) -> None:
    """A held pending boundary seeds OCCUPIED under a maintain hold, then applies."""
    hold = "input_boolean.keep_on"
    maintain = "binary_sensor.maintain"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(hold, "on")
    hass.states.async_set(maintain, "on")
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_OUTSIDE,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                    ATTR_SCHEDULE_END_OFF_PENDING: True,
                },
            )
        ],
    )
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_HOLD_ENTITIES: [hold],
            CONF_MAINTAIN_OCCUPANCY_ENTITY: maintain,
        },
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    hass.states.async_set(hold, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_reload_with_different_schedule_does_not_catch_up_boundary(
    hass: HomeAssistant,
) -> None:
    """Switching to a schedule that is currently off crosses no boundary."""
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "off")
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: "binary_sensor.old_schedule",
                },
            )
        ],
    )
    entry = make_scheduled_light_entry(schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF)
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes[ATTR_ACTIVE_SETTINGS_SCHEDULE] == SCHEDULE
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False


@pytest.mark.asyncio
async def test_restored_pending_off_is_dropped_when_action_is_keep(
    hass: HomeAssistant,
) -> None:
    """A boundary off deferred under turn_off dies with a reload to keep."""
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "off")
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_OUTSIDE,
                    ATTR_SCHEDULE_END_OFF_PENDING: True,
                },
            )
        ],
    )
    await setup_entries(hass, make_scheduled_light_entry())

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False


@pytest.mark.asyncio
async def test_options_reload_to_keep_drops_deferred_schedule_end_off(
    hass: HomeAssistant,
) -> None:
    """Changing the end action to keep releases a boundary held by a keep-on."""
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)
    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    options = {
        key: value for key, value in entry.data.items() if key != CONF_ENTITY_TYPE
    }
    options[CONF_SCHEDULE_END_ACTION] = SCHEDULE_END_ACTION_KEEP
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False

    hass.states.async_set(hold, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_state", [None, "unavailable", "unknown"])
async def test_first_start_without_usable_schedule_defaults_outside(
    hass: HomeAssistant, bad_state: str | None
) -> None:
    """Without valid or restored schedule state, startup uses the outside side."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    if bad_state is not None:
        hass.states.async_set(SCHEDULE, bad_state)
    hass.states.async_set(occupancy, "on")
    entry = make_scheduled_light_entry(
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 80,
        },
        inside={CONF_LIGHT_TIMEOUT: 60, CONF_AUTO_ON_BRIGHTNESS: 20},
    )
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.state == "on"
    assert state.attributes["brightness"] == 204


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_state", [None, "unavailable", "unknown"])
@pytest.mark.parametrize("restored", [ACTIVE_SETTINGS_INSIDE, ACTIVE_SETTINGS_OUTSIDE])
async def test_restart_uses_restored_settings_while_schedule_unavailable(
    hass: HomeAssistant,
    bad_state: str | None,
    restored: str,
) -> None:
    """An unusable schedule keeps the settings side restored after a restart."""
    hass.states.async_set(REAL, "off")
    if bad_state is not None:
        hass.states.async_set(SCHEDULE, bad_state)
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "off",
                {
                    ATTR_ACTIVE_SETTINGS: restored,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                },
            )
        ],
    )
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
        },
    )
    await setup_entries(hass, entry)

    assert hass.states.get(VIRTUAL).attributes[ATTR_ACTIVE_SETTINGS] == restored


@pytest.mark.asyncio
async def test_restart_unavailable_does_not_apply_other_side_sensors(
    hass: HomeAssistant,
) -> None:
    """Restoration selects a side before startup sensor rules are evaluated."""
    outside_occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "unavailable")
    hass.states.async_set(outside_occupancy, "on")
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "off",
                {
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                },
            )
        ],
    )
    entry = make_scheduled_light_entry(
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: outside_occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 80,
        },
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.state == "off"


@pytest.mark.asyncio
async def test_scheduled_light_gets_shared_auto_off_switch(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    await setup_entries(hass, make_scheduled_light_entry())

    assert hass.states.get("switch.scheduled_light_auto_off") is not None
