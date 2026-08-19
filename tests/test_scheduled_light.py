"""Tests for the MoLight Virtual Scheduled Light."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, State
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.const import (
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_DOOR_ENTITY,
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_MODE,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHT_TIMEOUT,
    CONF_OCCUPANCY_ENTITY,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE,
    STATE_COUNTDOWN,
    STATE_OCCUPIED,
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
    assert state.attributes["active_settings"] == "outside_schedule"

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
    assert state.attributes["active_settings"] == "inside_schedule"


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
    assert state.attributes["active_settings"] == "inside_schedule"
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
    assert state.attributes["active_settings"] == "inside_schedule"
    assert state.state == expected_state


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
    assert state.attributes["active_settings"] == "inside_schedule"
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
    assert state.attributes["active_settings"] == "inside_schedule"
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
    assert hass.states.get(VIRTUAL).attributes["active_settings"] == "inside_schedule"

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
    assert hass.states.get(VIRTUAL).attributes["active_settings"] == (
        "outside_schedule"
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
        ("on", "outside_schedule", "inside_schedule", 51),
        ("off", "inside_schedule", "outside_schedule", 204),
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
    mock_restore_cache(hass, [State(VIRTUAL, "off", {"active_settings": restored})])
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
    assert state.attributes["active_settings"] == expected
    assert state.state == "on"
    assert state.attributes["brightness"] == expected_brightness


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
    assert state.attributes["active_settings"] == "outside_schedule"
    assert state.state == "on"
    assert state.attributes["brightness"] == 204


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_state", [None, "unavailable", "unknown"])
@pytest.mark.parametrize("restored", ["inside_schedule", "outside_schedule"])
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
        [State(VIRTUAL, "off", {"active_settings": restored})],
    )
    entry = make_scheduled_light_entry(
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
        },
    )
    await setup_entries(hass, entry)

    assert hass.states.get(VIRTUAL).attributes["active_settings"] == restored


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
        [State(VIRTUAL, "off", {"active_settings": "inside_schedule"})],
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
    assert state.attributes["active_settings"] == "inside_schedule"
    assert state.state == "off"


@pytest.mark.asyncio
async def test_scheduled_light_gets_shared_auto_off_switch(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(REAL, "off")
    hass.states.async_set(SCHEDULE, "off")
    await setup_entries(hass, make_scheduled_light_entry())

    assert hass.states.get("switch.scheduled_light_auto_off") is not None
