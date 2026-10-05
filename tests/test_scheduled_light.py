"""Tests for the MoLight Virtual Scheduled Light."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.const import ATTR_RESTORED, EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, ServiceCall, State
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
    ATTR_MANUAL_OFF_CLEARED,
    ATTR_SCHEDULE_END_OFF_PENDING,
    CONF_AUTO_OFF_TRANSITION,
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_DOOR_ENTITY,
    CONF_DOOR_MODE,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_RGB_COLOR,
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
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_SCHEDULE_DEFINITION,
    CONF_SCHEDULE_END_ACTION,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_SOURCE,
    CONF_STANDBY_BRIGHTNESS,
    CONF_TIME_WINDOWS,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_TIMEOUT,
    DOMAIN,
    DOOR_MODE_OPEN_CLOSE,
    ENTITY_TYPE_SCHEDULE,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE,
    SCHEDULE_DEFINITION_BINARY_SENSOR,
    SCHEDULE_END_ACTION_KEEP,
    SCHEDULE_END_ACTION_SWITCH,
    SCHEDULE_END_ACTION_TURN_OFF,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_WARN,
)
from tests.conftest import (
    TORONTO,
    crash_entries,
    finish_startup,
    light_targets,
    make_scheduled_light_entry,
    record_service_calls,
    restart_entries,
    set_home,
    settle,
    setup_entries,
)
from tests.real_entities import RealLight, add_real
from tests.test_light_selection import _SlowSelect

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
    """turn_off on an already-off light also honors a held-open outside door."""
    door = "binary_sensor.outside_door"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(door, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_DOOR_ENTITY: door,
            CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
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
    # The open door holds it, exactly as it would outside a schedule.
    assert state.attributes["molight_state"] == STATE_OCCUPIED


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
async def test_schedule_end_switch_uses_remaining_outside_occupancy_timeout(
    hass: HomeAssistant, freezer
) -> None:
    """Switch-state subtracts elapsed clear time from the outside timeout."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    cleared = datetime.now(UTC) - timedelta(seconds=20)
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
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes["molight_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=9))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_end_switch_without_presence_sensor_uses_full_timeout(
    hass: HomeAssistant, freezer
) -> None:
    """No incoming presence sensor gives the outside profile a full timeout."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={CONF_LIGHT_TIMEOUT: 30},
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
@pytest.mark.parametrize(
    "presence_settings",
    [
        pytest.param(
            {CONF_MAINTAIN_OCCUPANCY_ENTITY: "binary_sensor.outside_presence"},
            id="maintain-occupancy",
        ),
        pytest.param(
            {
                CONF_DOOR_ENTITY: "binary_sensor.outside_presence",
                CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
            },
            id="open-close-door",
        ),
    ],
)
async def test_schedule_end_switch_adopts_active_outside_presence_source(
    hass: HomeAssistant, freezer, presence_settings: dict
) -> None:
    """Active incoming maintain and door sensors both hold the switched light."""
    presence = "binary_sensor.outside_presence"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(presence, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={CONF_LIGHT_TIMEOUT: 30, **presence_settings},
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

    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"

    hass.states.async_set(presence, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_COUNTDOWN
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_end_switch_uses_outside_warning_settings(
    hass: HomeAssistant, freezer
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

    calls = record_service_calls(hass)
    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    assert light_targets(calls, "turn_off") == [[REAL]]
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("end_action", "lit_by", "outside_visit", "resumed_for"),
    [
        # Keep leaves the inside countdown running: 300 s from the turn-on.
        pytest.param(SCHEDULE_END_ACTION_KEEP, "hand", None, 190, id="keep-countdown"),
        # Inside presence held it and outside watches nothing: a fresh timeout.
        pytest.param(SCHEDULE_END_ACTION_KEEP, "presence", None, 20, id="keep-held"),
        pytest.param(SCHEDULE_END_ACTION_SWITCH, "hand", None, 20, id="switch-fresh"),
        # Switch counts from the outside sensor's last visit, 15 s before.
        pytest.param(SCHEDULE_END_ACTION_SWITCH, "hand", 15, 5, id="switch-history"),
    ],
)
async def test_schedule_end_into_brightness_resumes_what_the_end_would_have_run(
    hass: HomeAssistant,
    freezer,
    end_action: str,
    lit_by: str,
    outside_visit: int | None,
    resumed_for: int,
) -> None:
    """Going dark resumes the countdown the end boundary would have left."""
    inside_occupancy = "binary_sensor.inside_occupancy"
    outside_occupancy = "binary_sensor.outside_occupancy"
    illuminance = "binary_sensor.outside_illuminance"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(inside_occupancy, "off")
    hass.states.async_set(illuminance, "on")
    outside = {
        CONF_LIGHT_TIMEOUT: 30,
        CONF_ILLUMINANCE_ENTITY: illuminance,
        CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
    }
    if outside_visit is not None:
        outside[CONF_OCCUPANCY_ENTITY] = outside_occupancy
    entry = make_scheduled_light_entry(
        schedule_end_action=end_action,
        outside=outside,
        inside={CONF_LIGHT_TIMEOUT: 300, CONF_OCCUPANCY_ENTITY: inside_occupancy},
    )
    await setup_entries(hass, entry)
    if lit_by == "presence":
        hass.states.async_set(inside_occupancy, "on")
    else:
        await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"

    freezer.tick(timedelta(seconds=100))
    if outside_visit is not None:
        visit = datetime.now(UTC) - timedelta(seconds=outside_visit)
        hass.states.async_set(
            outside_occupancy, "off", {"latest_occupied_time": visit.isoformat()}
        )
    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes["bright_forced_off"] is True

    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(illuminance, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_COUNTDOWN
    freezer.tick(timedelta(seconds=resumed_for - 1))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("end_action", "resumes"),
    [
        (SCHEDULE_END_ACTION_TURN_OFF, False),
        (SCHEDULE_END_ACTION_KEEP, True),
        (SCHEDULE_END_ACTION_SWITCH, True),
    ],
)
@pytest.mark.parametrize("crossed", ["live", "while_down"])
async def test_schedule_end_off_ends_an_on_period_brightness_had_cut_short(
    hass: HomeAssistant, freezer, end_action: str, resumes: bool, crossed: str
) -> None:
    """A Turn off end crossed while brightness has the light off ends that
    on-period, so going dark does not resume it under the outside settings.
    Keep state and Switch state carry it past the end."""
    illuminance = "binary_sensor.illuminance"
    settings = {
        CONF_LIGHT_TIMEOUT: 60,
        CONF_ILLUMINANCE_ENTITY: illuminance,
        CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
    }
    entry = make_scheduled_light_entry(
        schedule_end_action=end_action, outside=settings, inside=settings
    )
    hass.states.async_set(REAL, "off")
    if crossed == "live":
        hass.states.async_set(SCHEDULE, "on")
        hass.states.async_set(illuminance, "off")
        await setup_entries(hass, entry)
        await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
        await settle(hass)
        freezer.tick(timedelta(seconds=10))
        hass.states.async_set(illuminance, "on")
        await settle(hass)
        state = hass.states.get(VIRTUAL)
        assert state.state == "off"
        assert state.attributes["bright_forced_off"] is True
        freezer.tick(timedelta(seconds=5))
        hass.states.async_set(SCHEDULE, "off")
        await settle(hass)
    else:
        hass.states.async_set(SCHEDULE, "off")
        hass.states.async_set(illuminance, "on")
        until = datetime.now(UTC) + timedelta(seconds=45)
        mock_restore_cache(
            hass,
            [
                State(
                    VIRTUAL,
                    "off",
                    {
                        ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                        ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                        "bright_forced_off": True,
                        "bright_resume_until": until.isoformat(),
                    },
                )
            ],
        )
        await setup_entries(hass, entry)
        await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes["bright_forced_off"] is resumes
    assert (state.attributes["bright_resume_until"] is not None) is resumes

    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(illuminance, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == ("on" if resumes else "off")


@pytest.mark.asyncio
async def test_schedule_end_switch_into_brightness_resumes_for_outside_presence(
    hass: HomeAssistant, freezer
) -> None:
    """Presence the outside settings watch gets its light back when dark."""
    maintain = "binary_sensor.outside_maintain"
    illuminance = "binary_sensor.outside_illuminance"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(maintain, "on")
    hass.states.async_set(illuminance, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_MAINTAIN_OCCUPANCY_ENTITY: maintain,
            CONF_ILLUMINANCE_ENTITY: illuminance,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
        },
        inside={CONF_LIGHT_TIMEOUT: 300},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    freezer.tick(timedelta(seconds=100))
    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(illuminance, "off")
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_schedule_end_switch_into_bright_control_respects_a_hold(
    hass: HomeAssistant,
) -> None:
    """A held light crossing a Switch state end into a bright control profile
    stays on; releasing the hold applies the brightness."""
    illuminance = "binary_sensor.outside_illuminance"
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(illuminance, "on")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_ILLUMINANCE_ENTITY: illuminance,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
            CONF_HOLD_ENTITIES: [hold],
        },
        inside={CONF_LIGHT_TIMEOUT: 300, CONF_HOLD_ENTITIES: [hold]},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    calls = record_service_calls(hass)
    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert light_targets(calls, "turn_off") == []
    assert state.state == "on"

    hass.states.async_set(hold, "off")
    await settle(hass)
    assert light_targets(calls, "turn_off") == [[REAL]]
    assert hass.states.get(VIRTUAL).state == "off"


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
    hass: HomeAssistant, freezer
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
async def test_restart_missed_schedule_end_switch_replaces_inside_warning(
    hass: HomeAssistant, freezer
) -> None:
    """Missed switch-state restores an inside warning before outside timing."""
    hass.states.async_set(REAL, "on", {"brightness": 255})
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
                    "brightness": 255,
                    "pre_warn_brightness": 200,
                },
            )
        ],
    )
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={CONF_LIGHT_TIMEOUT: 30},
        inside=_WARNING_OUTSIDE,
    )

    await setup_entries(hass, entry)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 200
    assert state.attributes["pre_warn_brightness"] is None

    freezer.tick(timedelta(seconds=29))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


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
async def test_reload_keeps_deferred_schedule_end_off_held(
    hass: HomeAssistant,
) -> None:
    """A reload with Auto-off switched off does not apply the waiting off."""
    switch = "switch.scheduled_light_auto_off"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    entry = make_scheduled_light_entry(schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF)
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.states.async_set(REAL, "on")  # no light platform: mirror the turn-on
    await settle(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch})
    await settle(hass)
    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    turn_offs: list[ServiceCall] = []
    hass.bus.async_listen(
        EVENT_CALL_SERVICE,
        lambda event: (
            turn_offs.append(event)
            if event.data["domain"] == "light" and event.data["service"] == "turn_off"
            else None
        ),
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    assert turn_offs == []
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["auto_off_held"] is True
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await settle(hass)
    assert len(turn_offs) == 1
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_schedule_change_rechecks_already_open_door(
    hass: HomeAssistant,
) -> None:
    """A held-open door selected by the new settings triggers the light."""
    door = "binary_sensor.inside_door"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(door, "on")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_DOOR_ENTITY: door,
            CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
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
async def test_schedule_change_ignores_a_standing_open_momentary_door(
    hass: HomeAssistant,
) -> None:
    """In plain open mode the door is a momentary trigger, so a profile switch
    is not an opening, matching startup, illuminance going dark and a
    gate-mode window starting, which all ignore a door that is merely open."""
    door = "binary_sensor.inside_door"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(door, "on")
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={CONF_LIGHT_TIMEOUT: 60, CONF_DOOR_ENTITY: door},
    )
    await setup_entries(hass, entry)

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)

    assert hass.states.get(VIRTUAL).state == "off"

    # Actually opening it still works.
    hass.states.async_set(door, "off")
    await settle(hass)
    hass.states.async_set(door, "on")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"


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

    calls = record_service_calls(hass)
    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)

    forced_off = [[REAL]] if expected_state == "off" else []
    assert light_targets(calls, "turn_off") == forced_off
    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.state == expected_state


@pytest.mark.asyncio
async def test_schedule_change_bright_gate_sensor_lets_occupancy_hold_on_light(
    hass: HomeAssistant,
) -> None:
    """A newly selected gate-mode sensor reading bright only gates an off
    light, so the new side's active occupancy holds an on one."""
    occupancy = "binary_sensor.inside_occupancy"
    illuminance = "binary_sensor.inside_illuminance"
    hass.states.async_set(REAL, "on")
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
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.attributes["molight_state"] == STATE_OCCUPIED


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
    # itself is the keep-on entity, and it is on.
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
    freezer.tick(timedelta(seconds=9))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    freezer.tick(timedelta(seconds=2))
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
    freezer.tick(timedelta(seconds=9))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    freezer.tick(timedelta(seconds=2))
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
    hass.states.async_set("select.light_mode", "Day")
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
@pytest.mark.parametrize(
    "end_action",
    [
        SCHEDULE_END_ACTION_TURN_OFF,
        SCHEDULE_END_ACTION_SWITCH,
        SCHEDULE_END_ACTION_KEEP,
    ],
)
async def test_schedule_end_takes_over_a_turn_on_waiting_for_its_selection(
    hass: HomeAssistant, freezer, end_action: str
) -> None:
    """A turn-on the inside profile has not sent yet is judged as an on light."""
    select = _SlowSelect(hass)
    inside_occupancy = "binary_sensor.inside_occupancy"
    hass.states.async_set("select.light_mode", "Night", {"options": ["Night"]})
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(inside_occupancy, "off")
    entry = make_scheduled_light_entry(
        schedule_end_action=end_action,
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={
            CONF_LIGHT_TIMEOUT: 600,
            CONF_OCCUPANCY_ENTITY: inside_occupancy,
            CONF_TURN_ON_SELECT_ENTITY: "select.light_mode",
            CONF_TURN_ON_SELECT_OPTION: "Night",
        },
    )
    await setup_entries(hass, entry)

    hass.states.async_set(inside_occupancy, "on")
    await asyncio.wait_for(select.started.wait(), 2)
    hass.states.async_set(SCHEDULE, "off")
    for _ in range(4):  # settle() would wait for the select call
        await asyncio.sleep(0)
    select.release.set()
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    if end_action == SCHEDULE_END_ACTION_TURN_OFF:
        assert (state.state, state.attributes["molight_state"]) == ("off", STATE_IDLE)
        return
    # The outside profile has no presence sensor, so its own timeout runs:
    # recalculated as a fresh one by switch, started for the lost hold by keep.
    expected = (
        STATE_ACTIVE if end_action == SCHEDULE_END_ACTION_SWITCH else STATE_COUNTDOWN
    )
    assert (state.state, state.attributes["molight_state"]) == ("on", expected)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "end_action",
    [
        SCHEDULE_END_ACTION_TURN_OFF,
        SCHEDULE_END_ACTION_SWITCH,
        SCHEDULE_END_ACTION_KEEP,
    ],
)
async def test_schedule_end_takes_over_a_manual_turn_on_waiting_for_its_selection(
    hass: HomeAssistant, freezer, end_action: str
) -> None:
    """A manual turn-on not sent yet is judged as an on light too."""
    select = _SlowSelect(hass)
    outside_occupancy = "binary_sensor.outside_occupancy"
    visit = (datetime.now(UTC) - timedelta(hours=4)).isoformat()
    hass.states.async_set("select.light_mode", "Night", {"options": ["Night"]})
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(outside_occupancy, "off", {"latest_occupied_time": visit})
    entry = make_scheduled_light_entry(
        schedule_end_action=end_action,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_OCCUPANCY_ENTITY: outside_occupancy},
        inside={
            CONF_LIGHT_TIMEOUT: 600,
            CONF_TURN_ON_SELECT_ENTITY: "select.light_mode",
            CONF_TURN_ON_SELECT_OPTION: "Night",
        },
    )
    await setup_entries(hass, entry)
    calls = record_service_calls(hass)

    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    hass.states.async_set(SCHEDULE, "off")
    for _ in range(4):  # settle() would wait for the select call
        await asyncio.sleep(0)
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    if end_action == SCHEDULE_END_ACTION_KEEP:
        # Keep hands the turn-on over: it runs the outside timeout.
        assert (state.state, state.attributes["molight_state"]) == ("on", STATE_ACTIVE)
        assert state.attributes["brightness"] == 200
        freezer.tick(timedelta(seconds=61))
        async_fire_time_changed(hass)
        await settle(hass)
        assert hass.states.get(VIRTUAL).state == "off"
        return
    # Turn off ends it; Switch finds the outside room long empty. Never lit.
    assert (state.state, state.attributes["molight_state"]) == ("off", STATE_IDLE)
    assert [REAL] not in light_targets(calls, "turn_on")


@pytest.mark.asyncio
async def test_held_schedule_end_off_waits_for_a_manual_turn_on_still_waiting(
    hass: HomeAssistant,
) -> None:
    """A held Turn off end is kept for a manual turn-on not sent yet, and
    applies when the hold releases."""
    select = _SlowSelect(hass)
    hold = "input_boolean.keep_on"
    hass.states.async_set("select.light_mode", "Night", {"options": ["Night"]})
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
        inside={
            CONF_LIGHT_TIMEOUT: 600,
            CONF_TURN_ON_SELECT_ENTITY: "select.light_mode",
            CONF_TURN_ON_SELECT_OPTION: "Night",
        },
    )
    await setup_entries(hass, entry)

    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    hass.states.async_set(SCHEDULE, "off")
    for _ in range(4):  # settle() would wait for the select call
        await asyncio.sleep(0)
    select.release.set()
    await turn_on
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    hass.states.async_set(hold, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert (state.state, state.attributes["molight_state"]) == ("off", STATE_IDLE)
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("lit_by", ["occupancy", "hand"])
@pytest.mark.parametrize("released", ["waiting", "sent"])
async def test_hold_release_applies_a_held_end_off_to_a_waiting_turn_on(
    hass: HomeAssistant, freezer, lit_by: str, released: str
) -> None:
    """Releasing the hold applies a deferred Turn off end to a turn-on still
    waiting for its selection, as it does once the turn-on was sent."""
    select = _SlowSelect(hass)
    occupancy = "binary_sensor.inside_occupancy"
    hold = "input_boolean.keep_on"
    hass.states.async_set("select.light_mode", "Night", {"options": ["Night"]})
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(occupancy, "off")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 5, CONF_HOLD_ENTITIES: [hold]},
        inside={
            CONF_LIGHT_TIMEOUT: 600,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_TURN_ON_SELECT_ENTITY: "select.light_mode",
            CONF_TURN_ON_SELECT_OPTION: "Night",
        },
    )
    await setup_entries(hass, entry)
    calls = record_service_calls(hass)

    if lit_by == "occupancy":
        hass.states.async_set(occupancy, "on")
    else:
        hass.async_create_task(
            hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
        )
    await asyncio.wait_for(select.started.wait(), 2)
    hass.states.async_set(SCHEDULE, "off")
    for _ in range(4):  # settle() would wait for the select call
        await asyncio.sleep(0)
    assert hass.states.get(VIRTUAL).attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True
    if released == "waiting":
        hass.states.async_set(hold, "off")
        for _ in range(4):
            await asyncio.sleep(0)
    select.release.set()
    await settle(hass)
    if released == "sent":
        assert hass.states.get(VIRTUAL).state == "on"
        hass.states.async_set(hold, "off")
        await settle(hass)

    for _ in range(2):
        state = hass.states.get(VIRTUAL)
        assert (state.state, state.attributes["molight_state"]) == ("off", STATE_IDLE)
        assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False
        assert state.attributes["auto_off_held"] is False
        freezer.tick(timedelta(seconds=120))
        async_fire_time_changed(hass)
        await settle(hass)
    if released == "waiting":
        assert [REAL] not in light_targets(calls, "turn_on")


@pytest.mark.asyncio
@pytest.mark.parametrize("lit_by", ["occupancy", "hand"])
async def test_waiting_turn_on_cancelled_by_a_schedule_end_reports_off(
    hass: HomeAssistant, lit_by: str
) -> None:
    """A turn-on reports on while it waits for its selection; a Turn off end
    that cancels it takes that back, and the real light is never lit."""
    select = _SlowSelect(hass)
    occupancy = "binary_sensor.inside_occupancy"
    hass.states.async_set("select.light_mode", "Night", {"options": ["Night"]})
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(occupancy, "off")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={
            CONF_LIGHT_TIMEOUT: 600,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_TURN_ON_SELECT_ENTITY: "select.light_mode",
            CONF_TURN_ON_SELECT_OPTION: "Night",
        },
    )
    await setup_entries(hass, entry)
    calls = record_service_calls(hass)

    if lit_by == "occupancy":
        hass.states.async_set(occupancy, "on")
    else:
        hass.async_create_task(
            hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
        )
    await asyncio.wait_for(select.started.wait(), 2)
    assert hass.states.get(VIRTUAL).state == "on"

    hass.states.async_set(SCHEDULE, "off")
    for _ in range(4):  # settle() would wait for the select call
        await asyncio.sleep(0)
    assert hass.states.get(VIRTUAL).state == "off"
    select.release.set()
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert (state.state, state.attributes["molight_state"]) == ("off", STATE_IDLE)
    assert [REAL] not in light_targets(calls, "turn_on")


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
async def test_source_backed_schedule_drives_scheduled_light_end_to_end(
    hass: HomeAssistant,
) -> None:
    """A real source propagates through its schedule wrapper to profile selection."""
    source = "binary_sensor.house_mode"
    schedule_entity = "binary_sensor.profile_schedule"
    hass.states.async_set(source, "off")
    schedule = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Profile Schedule",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: source,
        },
    )
    light = make_scheduled_light_entry(schedule=schedule_entity)
    await setup_entries(hass, schedule, light)

    assert hass.states.get(schedule_entity).state == "off"
    assert hass.states.get(VIRTUAL).attributes[ATTR_ACTIVE_SETTINGS] == (
        ACTIVE_SETTINGS_OUTSIDE
    )

    hass.states.async_set(source, "on")
    await settle(hass)
    assert hass.states.get(schedule_entity).state == "on"
    assert hass.states.get(VIRTUAL).attributes[ATTR_ACTIVE_SETTINGS] == (
        ACTIVE_SETTINGS_INSIDE
    )

    # Losing the real source makes the wrapper unavailable but does not invent
    # a profile boundary in the consumer.
    hass.states.async_set(source, "unavailable")
    await settle(hass)
    assert hass.states.get(schedule_entity).state == "unavailable"
    assert hass.states.get(VIRTUAL).attributes[ATTR_ACTIVE_SETTINGS] == (
        ACTIVE_SETTINGS_INSIDE
    )

    hass.states.async_set(source, "off")
    await settle(hass)
    assert hass.states.get(schedule_entity).state == "off"
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
async def test_restart_missed_schedule_end_keep_leaves_light_on(
    hass: HomeAssistant, freezer
) -> None:
    """Under the default keep action a missed end swaps settings, light on."""
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
        outside={CONF_LIGHT_TIMEOUT: 30, CONF_OCCUPANCY_ENTITY: occupancy},
        inside={CONF_LIGHT_TIMEOUT: 300},
    )
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False

    freezer.tick(timedelta(seconds=29))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_restart_inside_window_drops_restored_pending_off(
    hass: HomeAssistant,
) -> None:
    """A pending off from the last window dies once the next window has begun."""
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(hold, "on")
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
        inside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
    )
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False

    calls = record_service_calls(hass)
    hass.states.async_set(hold, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    assert light_targets(calls, "turn_off") == []


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
async def test_restart_clears_pending_off_when_physical_light_is_already_off(
    hass: HomeAssistant,
) -> None:
    """A completed pending off cannot cut a later manual on after a hold cycle."""
    switch = "switch.scheduled_light_auto_off"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
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
    entry = make_scheduled_light_entry(schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF)
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch})
    await settle(hass)
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is False


@pytest.mark.asyncio
async def test_restart_held_pending_off_restores_old_warning_before_release(
    hass: HomeAssistant,
) -> None:
    """A held pending off undoes its old warning presentation before waiting."""
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "on", {"brightness": 255})
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(hold, "on")
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
                    "brightness": 255,
                    "pre_warn_brightness": 200,
                },
            )
        ],
    )
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 200
    assert state.attributes["pre_warn_brightness"] is None

    hass.states.async_set(hold, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_restart_missed_switch_with_light_off_seeds_outside_occupancy(
    hass: HomeAssistant,
) -> None:
    """An off light follows normal outside occupancy after a missed switch."""
    occupancy = "binary_sensor.outside_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(occupancy, "on")
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
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={CONF_LIGHT_TIMEOUT: 30, CONF_OCCUPANCY_ENTITY: occupancy},
        inside={CONF_LIGHT_TIMEOUT: 300},
    )
    await setup_entries(hass, entry)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("was_inside", "schedule", "marker", "lit"),
    [
        (False, "off", None, False),
        (False, "on", "2026-07-02T21:00:00+00:00", True),
        (True, "on", "2026-07-02T07:00:00+00:00", False),
        (True, "on", "2026-07-02T21:00:00+00:00", True),
        (True, "off", None, True),
    ],
    ids=["same_outside", "into_window", "same_window", "next_window", "out_of_window"],
)
async def test_restart_across_a_boundary_ends_a_manual_off_over_presence(
    hass: HomeAssistant, was_inside: bool, schedule: str, marker: str | None, lit: bool
) -> None:
    """A manual off made during someone's visit holds across a restart only
    while no settings boundary has passed, as it would without the restart."""
    occupancy = "binary_sensor.occupancy"
    now = datetime.now(UTC)
    hass.states.async_set(REAL, "off")
    hass.states.async_set(
        SCHEDULE, schedule, {"current_window_start": marker} if marker else {}
    )
    hass.states.async_set(occupancy, "on")
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "off",
                {
                    ATTR_ACTIVE_SETTINGS: (
                        ACTIVE_SETTINGS_INSIDE
                        if was_inside
                        else ACTIVE_SETTINGS_OUTSIDE
                    ),
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHEDULE,
                    ATTR_ACTIVE_SETTINGS_WINDOW: (
                        "2026-07-02T07:00:00+00:00" if was_inside else None
                    ),
                    "last_on_occupancy": (now - timedelta(minutes=20)).isoformat(),
                    "last_off_manual": (now - timedelta(minutes=10)).isoformat(),
                },
            )
        ],
    )
    settings = {CONF_LIGHT_TIMEOUT: 30, CONF_OCCUPANCY_ENTITY: occupancy}
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_KEEP, outside=settings, inside=settings
    )
    await setup_entries(hass, entry)
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == ("on" if lit else "off")
    assert state.attributes["molight_state"] == (STATE_OCCUPIED if lit else STATE_IDLE)


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


@pytest.mark.asyncio
async def test_boundary_keeps_door_hold_through_unavailable_blip(
    hass: HomeAssistant,
) -> None:
    """A door blipping unavailable at the boundary must not drop its hold."""
    door = "binary_sensor.shared_door"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(door, "off")
    side = {
        CONF_LIGHT_TIMEOUT: 60,
        CONF_DOOR_ENTITY: door,
        CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
    }
    entry = make_scheduled_light_entry(outside=dict(side), inside=dict(side))
    await setup_entries(hass, entry)

    hass.states.async_set(door, "on")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(door, "unavailable")
    await settle(hass)
    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_boundary_does_not_carry_one_door_hold_onto_another_door(
    hass: HomeAssistant,
) -> None:
    """Only the same door keeps its cached open through a blip at the
    boundary; a different door reading unavailable counts as closed."""
    door_a = "binary_sensor.outside_door"
    door_b = "binary_sensor.inside_door"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(door_a, "off")
    hass.states.async_set(door_b, "unavailable")
    side = {CONF_LIGHT_TIMEOUT: 60, CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE}
    entry = make_scheduled_light_entry(
        outside=side | {CONF_DOOR_ENTITY: door_a},
        inside=side | {CONF_DOOR_ENTITY: door_b},
    )
    await setup_entries(hass, entry)
    hass.states.async_set(door_a, "on")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_COUNTDOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("inside_door", ["same", "other"])
async def test_boundary_and_a_door_unavailable_for_a_minute(
    hass: HomeAssistant, freezer, inside_door: str
) -> None:
    """The same door's outage runs on across the boundary and counts it closed
    60 s after it began; another door, open on the new side, keeps holding."""
    door = "binary_sensor.outside_door"
    other = "binary_sensor.inside_door"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(door, "off")
    hass.states.async_set(other, "on")
    side = {CONF_LIGHT_TIMEOUT: 60, CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE}
    entry = make_scheduled_light_entry(
        outside=side | {CONF_DOOR_ENTITY: door},
        inside=side | {CONF_DOOR_ENTITY: door if inside_door == "same" else other},
    )
    await setup_entries(hass, entry)
    hass.states.async_set(door, "on")
    await settle(hass)
    hass.states.async_set(door, "unavailable")
    await settle(hass)

    freezer.tick(timedelta(seconds=30))
    hass.states.async_set(SCHEDULE, "on")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_OCCUPIED
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == (
        STATE_COUNTDOWN if inside_door == "same" else STATE_OCCUPIED
    )


@pytest.mark.asyncio
async def test_boundary_off_defers_to_unavailable_keep_on_hold(
    hass: HomeAssistant,
) -> None:
    """A keep-on hold blipping unavailable at the window end still defers the off."""
    hold = "input_boolean.keep_on"
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(hold, "on")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
        inside={CONF_LIGHT_TIMEOUT: 60, CONF_HOLD_ENTITIES: [hold]},
    )
    await setup_entries(hass, entry)
    assert hass.states.get(VIRTUAL).state == "on"

    hass.states.async_set(hold, "unavailable")
    await settle(hass)
    hass.states.async_set(SCHEDULE, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes[ATTR_SCHEDULE_END_OFF_PENDING] is True

    # The recovered hold clearing applies the deferred off.
    hass.states.async_set(hold, "off")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_warn_undoes_effect_recolor_across_keep_boundary(
    hass: HomeAssistant, freezer
) -> None:
    """A keep boundary mid-effect must not stop the warn undoing the recolor."""
    hass.states.async_set(REAL, "off", {"supported_color_modes": ["hs"]})
    hass.states.async_set(SCHEDULE, "off")
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_KEEP,
        outside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_EFFECT_TIMEOUT: 10,
            CONF_EFFECT_BRIGHTNESS: 50,
            CONF_EFFECT_RGB_COLOR: [255, 0, 0],
            CONF_WARN_TIMEOUT: 30,
        },
        inside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_EFFECT_TIMEOUT: 10,
            CONF_EFFECT_BRIGHTNESS: 50,
            CONF_WARN_TIMEOUT: 30,
        },
    )
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": VIRTUAL, "hs_color": [120, 50]},
        blocking=True,
    )
    await settle(hass)

    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes["molight_state"] == STATE_EFFECT
    assert state.attributes["hs_color"] == (0.0, 100.0)

    hass.states.async_set(SCHEDULE, "on")  # keep boundary mid-effect
    await settle(hass)

    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.attributes["molight_state"] == STATE_WARN
    assert state.attributes["hs_color"] == (120.0, 50.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["start", "end"])
@pytest.mark.parametrize("running", [20, 0], ids=["dim", "blink"])
@pytest.mark.parametrize(
    "new_effect",
    [
        {},
        {CONF_EFFECT_TIMEOUT: 60, CONF_EFFECT_BRIGHTNESS: 0},
        {CONF_EFFECT_TIMEOUT: 60, CONF_EFFECT_BRIGHTNESS: 80},
    ],
    ids=["disabled", "blink", "dim"],
)
async def test_recovery_mid_effect_follows_the_running_stage_after_a_keep_boundary(
    hass: HomeAssistant, freezer, boundary: str, running: int, new_effect: dict
) -> None:
    """A real light back from unavailable after a keep boundary gets the
    effect still running, judged by that stage and not by the new settings'
    effect: a dim one relights it, a blackout leaves it dark. The effect
    still ends at its own deadline."""
    lamp = RealLight("real_1")
    await add_real(hass, lamp)
    hass.states.async_set(SCHEDULE, "on" if boundary == "end" else "off")
    old = {
        CONF_LIGHT_TIMEOUT: 10,
        CONF_EFFECT_TIMEOUT: 60,
        CONF_EFFECT_BRIGHTNESS: running,
    }
    new = {CONF_LIGHT_TIMEOUT: 10, **new_effect}
    entry = make_scheduled_light_entry(
        schedule_end_action=SCHEDULE_END_ACTION_KEEP,
        outside=new if boundary == "end" else old,
        inside=old if boundary == "end" else new,
    )
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 180}, blocking=True
    )
    await settle(hass)
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    shown = (True, 51) if running else (False, 180)
    assert (lamp.is_on, lamp.brightness) == shown

    hass.states.async_set(SCHEDULE, "off" if boundary == "end" else "on")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_EFFECT
    lamp._attr_available = False
    lamp.async_write_ha_state()
    await settle(hass)
    lamp._attr_available = True
    lamp._attr_is_on = False
    lamp.async_write_ha_state()
    await settle(hass)

    assert (lamp.is_on, lamp.brightness) == shown
    freezer.tick(timedelta(seconds=58))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_EFFECT
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_IDLE
    assert not lamp.is_on


@pytest.mark.asyncio
async def test_profile_switch_preserves_illuminance_cache_through_outage(
    hass: HomeAssistant,
) -> None:
    """A boundary crossed during an illuminance outage keeps the cached level.

    The boundary is judged against it, as with a readable sensor. Recomputing
    from the unavailable sensor would forget "bright", and its recovery to
    the same reading would then replay as a fresh bright edge, forcing an on
    light off in control mode.
    """
    illuminance = "binary_sensor.shared_illuminance"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(illuminance, "on")  # bright
    profile = {
        CONF_LIGHT_TIMEOUT: 60,
        CONF_ILLUMINANCE_ENTITY: illuminance,
        CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
    }
    entry = make_scheduled_light_entry(outside=dict(profile), inside=dict(profile))
    await setup_entries(hass, entry)

    # Bright never blocks a manual on.
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"

    # The sensor's entry reloading; it holds through its source's outages.
    hass.states.async_set(illuminance, "unavailable", {ATTR_RESTORED: True})
    await settle(hass)
    hass.states.async_set(SCHEDULE, "on")  # cross the boundary mid-outage
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"  # still known bright
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)

    # Recovery to the same "bright" is a replay, not a fresh bright edge.
    hass.states.async_set(illuminance, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role", [CONF_OCCUPANCY_ENTITY, CONF_MAINTAIN_OCCUPANCY_ENTITY]
)
async def test_profile_switch_keeps_the_hold_of_unreadable_presence(
    hass: HomeAssistant, role: str
) -> None:
    """A boundary crossed while a holding presence sensor is out keeps its hold."""
    presence = "binary_sensor.shared_presence"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    hass.states.async_set(presence, "off")
    profile = {CONF_LIGHT_TIMEOUT: 60, role: presence}
    entry = make_scheduled_light_entry(outside=dict(profile), inside=dict(profile))
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.states.async_set(presence, "on")
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(presence, "unavailable")
    await settle(hass)
    hass.states.async_set(SCHEDULE, "on")  # cross the boundary mid-outage
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


# ---------------------------------------------------------------------------
# A boundary that ended a manual off, across restarts
# ---------------------------------------------------------------------------

_OCCUPANCY = "binary_sensor.shared_occupancy"


async def _manual_off_then_boundary(
    hass: HomeAssistant, freezer, boundary: str, *, standby: bool = False
) -> MockConfigEntry:
    """Turn the light off by hand, then cross a boundary with presence unreadable."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(
        SCHEDULE,
        "off" if boundary == "start" else "on",
        {"current_window_start": "w1"},
    )
    hass.states.async_set(_OCCUPANCY, "unavailable")
    profile = {CONF_LIGHT_TIMEOUT: 60, CONF_OCCUPANCY_ENTITY: _OCCUPANCY}
    inside = {**profile, CONF_STANDBY_BRIGHTNESS: 20} if standby else profile
    entry = make_scheduled_light_entry(outside=profile, inside=inside)
    await setup_entries(hass, entry)
    freezer.tick(timedelta(seconds=1))
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes[ATTR_MANUAL_OFF_CLEARED] is False

    freezer.tick(timedelta(seconds=1))
    if boundary != "none":
        hass.states.async_set(
            SCHEDULE,
            "off" if boundary == "end" else "on",
            {"current_window_start": "w2"},
        )
        await settle(hass)
    return entry


def _visit_before_the_off(hass: HomeAssistant) -> None:
    """Report presence that began before the manual off."""
    started = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    hass.states.async_set(_OCCUPANCY, "on", {"last_on_time": started})


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["start", "end", "touch", "none"])
@pytest.mark.parametrize("how", ["live", "restart", "crash"])
async def test_boundary_ending_a_manual_off_stands_across_a_restart(
    hass: HomeAssistant, freezer, boundary: str, how: str
) -> None:
    """A manual off a boundary ended does not come back with a restart."""
    entry = await _manual_off_then_boundary(hass, freezer, boundary)
    cleared = boundary != "none"
    assert hass.states.get(VIRTUAL).attributes[ATTR_MANUAL_OFF_CLEARED] is cleared

    if how != "live":
        await restart_entries(hass, entry, started=how == "crash")
    if how == "crash":
        # Only what Home Assistant saved as it started is left.
        await crash_entries(hass, entry, started=False)
    _visit_before_the_off(hass)
    if how != "live":
        await finish_startup(hass)
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == ("on" if cleared else "off")
    assert state.attributes["molight_state"] == (
        STATE_OCCUPIED if cleared else STATE_IDLE
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["start", "end", "touch"])
async def test_boundary_missed_while_down_ends_a_manual_off_for_later_restarts(
    hass: HomeAssistant, freezer, boundary: str
) -> None:
    """A boundary crossed while HA was down is remembered by the next restart."""
    entry = await _manual_off_then_boundary(hass, freezer, "none")
    hass.states.async_remove(SCHEDULE)
    hass.states.async_set(
        SCHEDULE,
        "off" if boundary == "start" else "on",
        {"current_window_start": "w1"},
    )
    await restart_entries(hass, entry, started=False)
    hass.states.async_set(
        SCHEDULE,
        "off" if boundary == "end" else "on",
        {"current_window_start": "w2"},
    )
    await finish_startup(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    assert hass.states.get(VIRTUAL).attributes[ATTR_MANUAL_OFF_CLEARED] is True

    await restart_entries(hass, entry, started=False)
    _visit_before_the_off(hass)
    await finish_startup(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["live", "restart"])
async def test_leaving_a_standby_profile_ends_its_manual_off(
    hass: HomeAssistant, freezer, how: str
) -> None:
    """The end boundary ends a standby window's manual off for the outside
    profile too, which has no standby to scope it."""
    entry = await _manual_off_then_boundary(hass, freezer, "end", standby=True)
    if how == "restart":
        await restart_entries(hass, entry, started=False)
    _visit_before_the_off(hass)
    if how == "restart":
        await finish_startup(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"


@pytest.mark.asyncio
async def test_new_manual_off_after_a_boundary_stands_across_a_restart(
    hass: HomeAssistant, freezer
) -> None:
    """A boundary only ends the off before it, not one made in the new window."""
    entry = await _manual_off_then_boundary(hass, freezer, "touch")
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    freezer.tick(timedelta(seconds=1))
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert hass.states.get(VIRTUAL).attributes[ATTR_MANUAL_OFF_CLEARED] is False

    await restart_entries(hass, entry, started=False)
    _visit_before_the_off(hass)
    await finish_startup(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("when", "active", "brightness"),
    [
        # Sunset is at 21:02 in June, after the window's 21:00 end.
        ("2026-06-22 12:00", ACTIVE_SETTINGS_OUTSIDE, 204),
        ("2026-06-22 21:05", ACTIVE_SETTINGS_OUTSIDE, 204),
        ("2026-03-20 20:00", ACTIVE_SETTINGS_INSIDE, 51),
    ],
)
async def test_settings_schedule_with_a_sunset_past_its_fixed_end_stays_outside(
    hass: HomeAssistant, freezer, when: str, active: str, brightness: int
) -> None:
    """A sunset -> 21:00 settings schedule has no window on a June day, so
    the light keeps its outside settings instead of the inside ones all day."""
    tz = await set_home(hass, *TORONTO)
    freezer.move_to(datetime.fromisoformat(when).replace(tzinfo=tz))
    occupancy = "binary_sensor.room_occupancy"
    hass.states.async_set(REAL, "off")
    hass.states.async_set(occupancy, "off")
    schedule = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Settings Schedule",
            CONF_TIME_WINDOWS: [{"start": {"sun": "sunset"}, "end": {"time": "21:00"}}],
        },
    )
    light = make_scheduled_light_entry(
        outside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 80,
        },
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 20,
        },
    )
    await setup_entries(hass, schedule, light)
    assert hass.states.get(VIRTUAL).attributes[ATTR_ACTIVE_SETTINGS] == active

    hass.states.async_set(occupancy, "on")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["brightness"] == brightness
