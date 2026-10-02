"""Entity ID changes: every stored reference follows the entity.

Changing an entity ID in Home Assistant removes the entity and adds it again
under the new ID. These tests rename through the entity registry, with real
entities on both sides, and check that the entries that reference a renamed
entity are rewritten and that nothing reads the rename as a change in the
room.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.molight import (
    _ENTITY_ID_KEYS,
    _ENTITY_ID_LIST_KEYS,
    RENAME_SETTLE_SECONDS,
)
from custom_components.molight.config_flow import SECTION_ADVANCED
from custom_components.molight.const import (
    CONF_DOOR_ENTITY,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_ON_BUTTONS_SINGLE,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_SCHEDULE_DEFINITION,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_INPUTS,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_SOURCE,
    CONF_STANDBY_BRIGHTNESS,
    CONF_TARGET_LIGHTS,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_TURN_ON_SELECT_SOURCE_ENTITY,
    DOMAIN,
    DOOR_MODE_OPEN_CLOSE,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_COMBINED_SCHEDULE,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_REMOTE,
    ENTITY_TYPE_SCHEDULE,
    ENTITY_TYPE_SCHEDULED_LIGHT,
    REMOTE_ACTION_FIELDS,
    SCHEDULE_DEFINITION_BINARY_SENSOR,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
)
from custom_components.molight.helpers import (
    molight_config,
    renamed_to,
    same_entity,
)
from tests.conftest import (
    light_targets,
    make_light_entry,
    make_scheduled_light_entry,
    record_service_calls,
    settle,
    setup_entries,
)
from tests.real_entities import (
    RealBinary,
    RealButton,
    RealLight,
    RealLux,
    RealSelect,
    RealToggle,
    add_real,
    rename,
    start_slow_rename,
)

VIRTUAL = "light.matrix_light"
MEMBER = "light.real_1"


async def tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


def attrs(hass: HomeAssistant, entity_id: str = VIRTUAL) -> dict[str, Any]:
    return hass.states.get(entity_id).attributes


def occupancy_entry(
    source: str = "binary_sensor.motion", name: str = "Room Occupancy", **config: Any
) -> MockConfigEntry:
    """Build a Virtual Occupancy Sensor entry: binary_sensor.room_occupancy."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: name,
            CONF_OCCUPANCY_SENSOR: source,
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 0,
            **config,
        },
    )


def schedule_entry(name: str = "Day", **config: Any) -> MockConfigEntry:
    """Build a Virtual Schedule Sensor that is on at the test's noon."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: name,
            CONF_TIME_WINDOWS: [{"start": {"time": "07:00"}, "end": {"time": "22:00"}}],
            **config,
        },
    )


OCCUPANCY = "binary_sensor.room_occupancy"

# ---------------------------------------------------------------------------
# Every key that stores an entity ID is rewritten, in data and in options
# ---------------------------------------------------------------------------

_LIGHT_REFERENCES = {
    CONF_OCCUPANCY_ENTITY: "binary_sensor.occ",
    CONF_MAINTAIN_OCCUPANCY_ENTITY: "binary_sensor.maintain",
    CONF_ILLUMINANCE_ENTITY: "binary_sensor.bright",
    CONF_DOOR_ENTITY: "binary_sensor.door",
    CONF_HOLD_ENTITIES: ["switch.hold_a", "switch.hold_b"],
    CONF_TURN_ON_SELECT_ENTITY: "select.target",
    CONF_TURN_ON_SELECT_OPTION: "Cozy",
    CONF_TURN_ON_SELECT_SOURCE_ENTITY: "select.source",
}
_REFERENCING_CONFIGS = {
    "light": {
        CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
        CONF_NAME: "Refs Light",
        CONF_LIGHTS: ["light.a", "light.b"],
        CONF_LIGHT_TIMEOUT: 60,
        CONF_SCHEDULE_ENTITY: "binary_sensor.sched",
        CONF_SCHEDULE_MODE: SCHEDULE_MODE_GATE,
        **_LIGHT_REFERENCES,
    },
    "scheduled_light": {
        CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
        CONF_NAME: "Refs Scheduled",
        CONF_LIGHTS: ["light.a", "light.b"],
        CONF_SCHEDULE_ENTITY: "binary_sensor.sched",
        CONF_OUTSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 60, **_LIGHT_REFERENCES},
        CONF_INSIDE_SCHEDULE_SETTINGS: {
            CONF_LIGHT_TIMEOUT: 60,
            **_LIGHT_REFERENCES,
            CONF_DOOR_ENTITY: "binary_sensor.inner_door",
            CONF_HOLD_ENTITIES: ["switch.hold_b", "switch.hold_c"],
        },
    },
    "occupancy": {
        CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
        CONF_NAME: "Refs Occupancy",
        CONF_OCCUPANCY_SENSOR: "binary_sensor.motion",
    },
    "combined_occupancy": {
        CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
        CONF_NAME: "Refs Combined",
        CONF_TRIGGER_SENSORS: ["binary_sensor.occ", "binary_sensor.occ_2"],
        CONF_MAINTAIN_SENSORS: ["binary_sensor.maintain"],
    },
    "illuminance": {
        CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
        CONF_NAME: "Refs Illuminance",
        CONF_ILLUMINANCE_SENSOR: "sensor.lux",
    },
    "schedule": {
        CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
        CONF_NAME: "Refs Schedule",
        CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
        CONF_SCHEDULE_SOURCE: "binary_sensor.workday",
    },
    "combined_schedule": {
        CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_SCHEDULE,
        CONF_NAME: "Refs Combined Schedule",
        CONF_SCHEDULE_INPUTS: ["binary_sensor.sched", "binary_sensor.sched_2"],
    },
    "remote": {
        CONF_ENTITY_TYPE: ENTITY_TYPE_REMOTE,
        CONF_NAME: "Refs Remote",
        CONF_TARGET_LIGHTS: ["light.a", "light.b"],
        **{
            key: [f"event.{key}", "event.shared"]
            for single, double, _ in REMOTE_ACTION_FIELDS
            for key in (single, double)
        },
    },
}
_NOT_ENTITY_IDS = (CONF_ENTITY_TYPE, CONF_NAME, CONF_TURN_ON_SELECT_OPTION)


def _entity_ids(config: Any) -> set[str]:
    """Every entity ID anywhere in a config."""
    if isinstance(config, dict):
        return set().union(
            *(_entity_ids(v) for k, v in config.items() if k not in _NOT_ENTITY_IDS)
        )
    if isinstance(config, list):
        return set().union(*(_entity_ids(v) for v in config))
    return {config} if isinstance(config, str) and "." in config else set()


def _renamed(config: Any) -> Any:
    """The config with every entity ID in it replaced by its new ID."""
    if isinstance(config, dict):
        return {
            k: v if k in _NOT_ENTITY_IDS else _renamed(v) for k, v in config.items()
        }
    if isinstance(config, list):
        return [_renamed(v) for v in config]
    if isinstance(config, str) and "." in config:
        return f"{config}_new"
    return config


def test_reference_fixtures_cover_every_entity_id_key() -> None:
    """A key added to the rename walk needs a config above that uses it."""
    used = set()
    for config in _REFERENCING_CONFIGS.values():
        used |= set(config)
        for side in (CONF_OUTSIDE_SCHEDULE_SETTINGS, CONF_INSIDE_SCHEDULE_SETTINGS):
            used |= set(config.get(side, ()))
    assert set(_ENTITY_ID_KEYS) | set(_ENTITY_ID_LIST_KEYS) <= used


@pytest.mark.parametrize("entity_type", list(_REFERENCING_CONFIGS))
@pytest.mark.parametrize("edited", [False, True], ids=["data", "options"])
async def test_rename_rewrites_every_reference(
    hass: HomeAssistant, freezer, entity_type: str, edited: bool
) -> None:
    """Each entity ID an entry stores follows its entity, in data and in options.

    The entities are registered but report nothing, so their references are
    rewritten once the time allowed for them to report is over.
    """
    config = _REFERENCING_CONFIGS[entity_type]
    options = {k: v for k, v in config.items() if k != CONF_ENTITY_TYPE}
    entry = MockConfigEntry(
        domain=DOMAIN, data=config, options=options if edited else {}
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    registry = er.async_get(hass)
    for entity_id in _entity_ids(config):
        domain, object_id = entity_id.split(".")
        registry.async_get_or_create(
            domain, "test", object_id, suggested_object_id=object_id
        )

    for entity_id in _entity_ids(config):
        registry.async_update_entity(entity_id, new_entity_id=f"{entity_id}_new")
    await settle(hass)
    assert dict(entry.data) == config

    await tick(hass, freezer, RENAME_SETTLE_SECONDS)
    assert dict(entry.data) == _renamed(config)
    assert dict(entry.options) == (_renamed(options) if edited else {})
    assert molight_config(entry) == _renamed(config)


# ---------------------------------------------------------------------------
# A Virtual Light's references
# ---------------------------------------------------------------------------


async def test_renamed_occupancy_sensor_keeps_driving_the_light(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """The light follows its occupancy sensor to the new ID, mid-visit."""
    motion = RealBinary("motion")
    member = RealLight("real_1")
    await add_real(hass, motion, member)
    light = make_light_entry(occupancy=OCCUPANCY, timeout=60)
    await setup_entries(hass, occupancy_entry(), light)
    motion.set(True)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "occupied"
    assert member.is_on

    calls = record_service_calls(hass)
    await rename(hass, OCCUPANCY, "binary_sensor.kitchen_presence")

    assert hass.states.get(OCCUPANCY) is None
    assert hass.states.get("binary_sensor.kitchen_presence").state == "on"
    assert attrs(hass)["molight_state"] == "occupied"
    assert attrs(hass)["last_off_manual"] is None
    assert light_targets(calls, "turn_on") == light_targets(calls, "turn_off") == []

    # The clear and the next visit arrive under the new ID.
    motion.set(False)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "countdown"
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"
    motion.set(True)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    assert member.is_on


@pytest.mark.parametrize(
    "renamed",
    ["occupancy", "source", "member"],
)
async def test_rename_is_not_a_new_visit_for_a_light_turned_off_by_hand(
    hass: HomeAssistant, virtual_light_behavior_variant, renamed: str
) -> None:
    """A light turned off in an occupied room stays off through a rename.

    Whether the occupancy sensor, its motion source or the real light is
    renamed, the visit that was already under way does not light the room.
    """
    motion = RealBinary("motion")
    member = RealLight("real_1")
    await add_real(hass, motion, member)
    await setup_entries(
        hass, occupancy_entry(), make_light_entry(occupancy=OCCUPANCY, timeout=60)
    )
    motion.set(True)
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    turned_off = attrs(hass)["last_off_manual"]

    calls = record_service_calls(hass)
    old_id = {
        "occupancy": OCCUPANCY,
        "source": "binary_sensor.motion",
        "member": MEMBER,
    }[renamed]
    await rename(hass, old_id, f"{old_id}_new")

    assert hass.states.get(VIRTUAL).state == "off"
    assert not member.is_on
    assert light_targets(calls, "turn_on") == []
    assert attrs(hass)["last_off_manual"] == turned_off
    assert attrs(hass)["last_on_occupancy"] < turned_off


async def test_renamed_real_light_is_not_a_change_at_the_wall(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """A real light that changes ID stays on, unclaimed, and is still driven."""
    member = RealLight("real_1")
    other = RealLight("real_2")
    await add_real(hass, member, other)
    await setup_entries(
        hass, make_light_entry(lights=[MEMBER, "light.real_2"], timeout=60)
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 120}, blocking=True
    )
    await settle(hass)
    before = dict(attrs(hass))

    calls = record_service_calls(hass)
    await rename(hass, MEMBER, "light.kitchen_bulb")

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == "active"
    assert state.attributes["brightness"] == 120
    for attribute in ("last_on_physical", "last_on_virtual", "last_off_manual"):
        assert state.attributes[attribute] == before[attribute]
    assert light_targets(calls, "turn_on") == light_targets(calls, "turn_off") == []

    # The timeout turns the real light off under its new ID.
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"
    assert light_targets(calls, "turn_off") == [["light.kitchen_bulb", "light.real_2"]]
    assert not member.is_on
    assert attrs(hass)["last_off_manual"] is None


async def test_rename_waits_for_a_real_light_that_is_slow_to_come_back(
    hass: HomeAssistant, virtual_light_behavior_variant
) -> None:
    """The light reloads only once its renamed real light reports again.

    Reloading earlier would seed it as off, and the real light reporting in
    lit would then read as a turn-on at the wall.
    """
    member = RealLight("real_1", on=True, brightness=90)
    await add_real(hass, member)
    entry = make_light_entry(occupancy=OCCUPANCY, timeout=60, auto_on_brightness=100)
    hass.states.async_set(OCCUPANCY, "on")
    await setup_entries(hass, entry)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "occupied"
    assert attrs(hass)["last_on_physical"] is None

    calls = record_service_calls(hass)
    await start_slow_rename(hass, member, "light.kitchen_bulb")
    assert hass.states.get(MEMBER) is None
    assert molight_config(entry)[CONF_LIGHTS] == [MEMBER]
    assert hass.states.get(VIRTUAL).state == "on"

    member.added_gate.set()
    await settle(hass)
    assert molight_config(entry)[CONF_LIGHTS] == ["light.kitchen_bulb"]
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == "occupied"
    assert state.attributes["brightness"] == 90
    assert state.attributes["last_on_physical"] is None
    assert light_targets(calls, "turn_on") == []


async def test_renamed_keep_on_entity_keeps_holding(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """A hold is kept through its entity's rename and released under the new ID."""
    hold = RealToggle("guest_mode", on=True)
    member = RealLight("real_1")
    await add_real(hass, hold, member)
    await setup_entries(
        hass, make_light_entry(hold_entities=["switch.guest_mode"], timeout=60)
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert attrs(hass)["auto_off_held"] is True

    await rename(hass, "switch.guest_mode", "switch.visitors")
    assert attrs(hass)["auto_off_held"] is True
    await tick(hass, freezer, 3600)
    assert hass.states.get(VIRTUAL).state == "on"

    hold.set(False)
    await settle(hass)
    assert attrs(hass)["auto_off_held"] is False
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"


async def test_renamed_door_keeps_holding_and_closing(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """An open door holds the light through its rename; closing still counts."""
    door = RealBinary("pantry_door")
    member = RealLight("real_1")
    await add_real(hass, door, member)
    await setup_entries(
        hass,
        make_light_entry(
            door="binary_sensor.pantry_door", door_mode=DOOR_MODE_OPEN_CLOSE, timeout=60
        ),
    )
    door.set(True)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "occupied"
    opened = attrs(hass)["last_on_door"]

    calls = record_service_calls(hass)
    await rename(hass, "binary_sensor.pantry_door", "binary_sensor.larder_door")
    assert attrs(hass)["molight_state"] == "occupied"
    assert attrs(hass)["last_on_door"] == opened
    assert light_targets(calls, "turn_on") == []

    door.set(False)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "countdown"
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"


async def test_renamed_maintain_and_illuminance_sensors_keep_their_roles(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """The maintain sensor still holds and the illuminance sensor still gates."""
    motion = RealBinary("motion")
    lux = RealLux("lux", 500)
    member = RealLight("real_1")
    await add_real(hass, motion, lux, member)
    bright = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
            CONF_NAME: "Room Bright",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux",
            CONF_ILLUMINANCE_THRESHOLD: 10.0,
        },
    )
    await setup_entries(
        hass,
        occupancy_entry(),
        occupancy_entry("binary_sensor.motion", "Desk Occupancy"),
        bright,
        make_light_entry(
            occupancy=OCCUPANCY,
            maintain="binary_sensor.desk_occupancy",
            illuminance="binary_sensor.room_bright",
            timeout=60,
        ),
    )
    await rename(hass, "binary_sensor.room_bright", "binary_sensor.daylight")
    await rename(hass, "binary_sensor.desk_occupancy", "binary_sensor.desk")

    # Bright: occupancy may not light the room.
    motion.set(True)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    # Dark: it does, and the maintain sensor holds the light.
    lux.set(1)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "occupied"
    await rename(hass, OCCUPANCY, "binary_sensor.room")
    assert attrs(hass)["molight_state"] == "occupied"
    motion.set(False)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "countdown"


async def test_renamed_select_entities_still_apply_the_turn_on_selection(
    hass: HomeAssistant, virtual_light_behavior_variant
) -> None:
    """The turn-on selection reads the renamed source and sets the renamed target."""
    target = RealSelect("preset")
    source = RealSelect("mood", "Bright")
    member = RealLight("real_1")
    await add_real(hass, target, source, member)
    await setup_entries(
        hass,
        make_light_entry(
            turn_on_select_entity="select.preset",
            turn_on_select_option="Cozy",
            turn_on_select_source_entity="select.mood",
        ),
    )
    await rename(hass, "select.preset", "select.wled_preset")
    await rename(hass, "select.mood", "select.theme")

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert target.current_option == "Bright"
    assert attrs(hass)["last_turn_on_selection_source"] == "select.theme"
    assert member.is_on


@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("definition", ["time", "source"])
async def test_renamed_follow_schedule_is_not_a_new_window(
    hass: HomeAssistant, virtual_light_behavior_variant, definition: str
) -> None:
    """A light turned off by hand mid-window stays off when its schedule is renamed.

    The window marker belongs to the schedule that set it, and a renamed
    schedule is still that schedule.
    """
    workday = RealBinary("workday", on=True)
    member = RealLight("real_1")
    await add_real(hass, workday, member)
    schedule = (
        schedule_entry()
        if definition == "time"
        else schedule_entry(
            **{
                CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
                CONF_SCHEDULE_SOURCE: "binary_sensor.workday",
            }
        )
    )
    light = make_light_entry(
        schedule="binary_sensor.day", schedule_mode=SCHEDULE_MODE_FOLLOW
    )
    await setup_entries(hass, schedule, light)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "scheduled"
    marker = attrs(hass)["schedule_window_start"]
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)

    calls = record_service_calls(hass)
    await rename(hass, "binary_sensor.day", "binary_sensor.daytime")
    assert molight_config(light)[CONF_SCHEDULE_ENTITY] == "binary_sensor.daytime"
    assert hass.states.get(VIRTUAL).state == "off"
    assert light_targets(calls, "turn_on") == []
    assert attrs(hass)["schedule_window_start"] == marker
    assert attrs(hass)["schedule_window_schedule"] == "binary_sensor.daytime"

    if definition == "source":
        # Nor is the schedule's own source changing ID a new window.
        await rename(hass, "binary_sensor.workday", "binary_sensor.working_day")
        assert attrs(hass, "binary_sensor.daytime")["current_window_start"] == marker
        assert hass.states.get(VIRTUAL).state == "off"
        assert light_targets(calls, "turn_on") == []
        # The end of the window still arrives.
        workday.set(False)
        await settle(hass)
        assert hass.states.get("binary_sensor.daytime").state == "off"
        assert attrs(hass)["schedule_window_start"] is None


@pytest.mark.regular_virtual_light_only
async def test_renamed_gate_schedule_still_gates(
    hass: HomeAssistant, virtual_light_behavior_variant
) -> None:
    """A gate schedule that changes ID does not become a gate that never opens."""
    workday = RealBinary("workday", on=False)
    motion = RealBinary("motion")
    member = RealLight("real_1")
    await add_real(hass, workday, motion, member)
    await setup_entries(
        hass,
        schedule_entry(
            **{
                CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
                CONF_SCHEDULE_SOURCE: "binary_sensor.workday",
            }
        ),
        occupancy_entry(),
        make_light_entry(
            occupancy=OCCUPANCY,
            schedule="binary_sensor.day",
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
    )
    await rename(hass, "binary_sensor.day", "binary_sensor.daytime")

    motion.set(True)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    workday.set(True)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "occupied"
    workday.set(False)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"


# ---------------------------------------------------------------------------
# A Virtual Scheduled Light's settings schedule
# ---------------------------------------------------------------------------


async def test_renamed_settings_schedule_is_not_a_boundary(hass: HomeAssistant) -> None:
    """A manual off inside the window still pauses standby after the rename.

    What the light restored about its settings belongs to the schedule that
    selected them, and a renamed schedule is still that schedule.
    """
    member = RealLight("real_1")
    await add_real(hass, member)
    light = make_scheduled_light_entry(
        schedule="binary_sensor.day",
        inside={CONF_LIGHT_TIMEOUT: 60, CONF_STANDBY_BRIGHTNESS: 10},
    )
    await setup_entries(hass, schedule_entry(), light)
    await settle(hass)
    virtual = "light.scheduled_light"
    assert attrs(hass, virtual)["molight_state"] == "standby"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": virtual}, blocking=True
    )
    await settle(hass)
    assert attrs(hass, virtual)["standby_suppressed"] is True

    calls = record_service_calls(hass)
    await rename(hass, "binary_sensor.day", "binary_sensor.daytime")

    assert molight_config(light)[CONF_SCHEDULE_ENTITY] == "binary_sensor.daytime"
    state = hass.states.get(virtual)
    assert state.state == "off"
    assert state.attributes["active_settings"] == "inside_schedule"
    assert state.attributes["active_settings_schedule"] == "binary_sensor.daytime"
    assert state.attributes["standby_suppressed"] is True
    assert state.attributes["manual_off_cleared"] is False
    assert light_targets(calls, "turn_on") == []
    assert not member.is_on


# ---------------------------------------------------------------------------
# Sensors and schedules that reference other entities
# ---------------------------------------------------------------------------


async def test_renamed_motion_source_continues_the_same_visit(
    hass: HomeAssistant, freezer
) -> None:
    """A source that changes ID mid-visit neither drops out nor starts a visit."""
    motion = RealBinary("motion")
    await add_real(hass, motion)
    entry = occupancy_entry(**{CONF_FALSE_DETECTION_GRACE: 3})
    await setup_entries(hass, entry)
    motion.set(True)
    await settle(hass)
    before = dict(attrs(hass, OCCUPANCY))
    await tick(hass, freezer, 20)

    # While the source is gone, on its way to the new ID, it has not dropped out.
    await start_slow_rename(hass, motion, "binary_sensor.hall_motion")
    assert hass.states.get("binary_sensor.motion") is None
    assert (
        attrs(hass, OCCUPANCY)["latest_occupied_time"]
        == (before["latest_occupied_time"])
    )
    motion.added_gate.set()
    await settle(hass)

    assert molight_config(entry)[CONF_OCCUPANCY_SENSOR] == "binary_sensor.hall_motion"
    state = hass.states.get(OCCUPANCY)
    assert state.state == "on"
    assert state.attributes["last_on_time"] == before["last_on_time"]
    assert state.attributes["latest_occupied_time"] == before["latest_occupied_time"]

    # No dropout clear was left running, and the clear is classified by the
    # visit's real start: 10 minutes on is no false detection.
    await tick(hass, freezer, 600)
    assert hass.states.get(OCCUPANCY).state == "on"
    motion.set(False)
    await settle(hass)
    state = hass.states.get(OCCUPANCY)
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["last_clear_unavailable"] is False


async def test_deleted_motion_source_still_drops_out(
    hass: HomeAssistant, freezer
) -> None:
    """Only a rename is spared: a source that is deleted clears after its timeout."""
    motion = RealBinary("motion")
    await add_real(hass, motion)
    await setup_entries(hass, occupancy_entry())
    motion.set(True)
    await settle(hass)

    er.async_get(hass).async_remove("binary_sensor.motion")
    await settle(hass)
    assert hass.states.get("binary_sensor.motion") is None
    assert hass.states.get(OCCUPANCY).state == "on"
    await tick(hass, freezer, 61)
    state = hass.states.get(OCCUPANCY)
    assert state.state == "off"
    assert state.attributes["last_clear_unavailable"] is True


def _combined_occupancy_entry(
    triggers: list[str], maintain: list[str] | None = None
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Floor Occupancy",
            CONF_TRIGGER_SENSORS: triggers,
            CONF_MAINTAIN_SENSORS: maintain or [],
        },
    )


@pytest.mark.parametrize("role", ["trigger", "maintain"])
async def test_renamed_constituent_does_not_clear_the_combined_sensor(
    hass: HomeAssistant, role: str
) -> None:
    """The combined sensor stays on through a constituent's rename.

    A clear and a fresh start would be a new visit to the lights that use
    it: one turned off by hand in the occupied room would come back on.
    """
    motion = RealBinary("motion")
    desk = RealBinary("desk_motion")
    member = RealLight("real_1")
    await add_real(hass, motion, desk, member)
    combined = _combined_occupancy_entry([OCCUPANCY], ["binary_sensor.desk_occupancy"])
    await setup_entries(
        hass,
        occupancy_entry(),
        occupancy_entry("binary_sensor.desk_motion", "Desk Occupancy"),
        combined,
        make_light_entry(occupancy="binary_sensor.floor_occupancy", timeout=60),
    )
    motion.set(True)
    await settle(hass)
    if role == "maintain":
        # Only the maintain sensor still holds the combined sensor on.
        desk.set(True)
        await settle(hass)
        motion.set(False)
        await settle(hass)
    assert hass.states.get("binary_sensor.floor_occupancy").state == "on"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)

    seen: list[str] = []
    hass.bus.async_listen(
        "state_changed",
        callback(
            lambda event: (
                seen.append(event.data["new_state"].state)
                if event.data["entity_id"] == "binary_sensor.floor_occupancy"
                and event.data["new_state"] is not None
                else None
            )
        ),
    )
    calls = record_service_calls(hass)
    old_id = OCCUPANCY if role == "trigger" else "binary_sensor.desk_occupancy"
    await rename(hass, old_id, f"{old_id}_new")

    key = CONF_TRIGGER_SENSORS if role == "trigger" else CONF_MAINTAIN_SENSORS
    assert molight_config(combined)[key] == [f"{old_id}_new"]
    assert hass.states.get("binary_sensor.floor_occupancy").state == "on"
    assert "off" not in seen
    assert hass.states.get(VIRTUAL).state == "off"
    assert light_targets(calls, "turn_on") == []

    # The real clear still arrives through the renamed constituent.
    (motion if role == "trigger" else desk).set(False)
    await settle(hass)
    assert hass.states.get("binary_sensor.floor_occupancy").state == "off"


async def test_constituent_slow_to_come_back_does_not_clear_the_combined_sensor(
    hass: HomeAssistant,
) -> None:
    """A renamed constituent that is gone for a while has not dropped out."""
    constituent = RealBinary("presence", on=True)
    await add_real(hass, constituent)
    combined = _combined_occupancy_entry(["binary_sensor.presence"])
    await setup_entries(hass, combined)
    before = dict(attrs(hass, "binary_sensor.floor_occupancy"))

    await start_slow_rename(hass, constituent, "binary_sensor.radar")
    assert hass.states.get("binary_sensor.presence") is None
    state = hass.states.get("binary_sensor.floor_occupancy")
    assert state.state == "on"
    assert state.attributes == before

    constituent.added_gate.set()
    await settle(hass)
    assert molight_config(combined)[CONF_TRIGGER_SENSORS] == ["binary_sensor.radar"]
    assert hass.states.get("binary_sensor.floor_occupancy").state == "on"
    constituent.set(False)
    await settle(hass)
    assert hass.states.get("binary_sensor.floor_occupancy").state == "off"


async def test_combined_sensor_keeps_its_clear_flag_when_a_constituent_is_renamed(
    hass: HomeAssistant,
) -> None:
    """The last clear's false-detection flag still describes the same sensors."""
    constituent = RealBinary("presence")
    await add_real(hass, constituent)
    await setup_entries(hass, _combined_occupancy_entry(["binary_sensor.presence"]))
    # A cycle that never advanced latest_occupied_time is a false detection.
    constituent.set(True)
    await settle(hass)
    constituent.set(False)
    await settle(hass)
    assert attrs(hass, "binary_sensor.floor_occupancy")["last_clear_false_detection"]

    await rename(hass, "binary_sensor.presence", "binary_sensor.radar")
    state = hass.states.get("binary_sensor.floor_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is True
    assert state.attributes["false_detection_count"] == 1


async def test_deleted_constituent_still_clears_the_combined_sensor(
    hass: HomeAssistant,
) -> None:
    """Only a rename is spared: a constituent that is deleted drops out."""
    constituent = RealBinary("presence", on=True)
    await add_real(hass, constituent)
    await setup_entries(hass, _combined_occupancy_entry(["binary_sensor.presence"]))
    assert hass.states.get("binary_sensor.floor_occupancy").state == "on"

    er.async_get(hass).async_remove("binary_sensor.presence")
    await settle(hass)
    assert hass.states.get("binary_sensor.floor_occupancy").state == "off"


async def test_renamed_lux_source_keeps_the_reading(hass: HomeAssistant) -> None:
    """An illuminance sensor follows its source and keeps reading bright."""
    lux = RealLux("lux", 500)
    await add_real(hass, lux)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
            CONF_NAME: "Room Bright",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux",
            CONF_ILLUMINANCE_THRESHOLD: 10.0,
        },
    )
    await setup_entries(hass, entry)
    assert hass.states.get("binary_sensor.room_bright").state == "on"

    await rename(hass, "sensor.lux", "sensor.window_lux")
    assert molight_config(entry)[CONF_ILLUMINANCE_SENSOR] == "sensor.window_lux"
    assert hass.states.get("binary_sensor.room_bright").state == "on"
    lux.set(1)
    await settle(hass)
    assert hass.states.get("binary_sensor.room_bright").state == "off"


def _combined_schedule_entry(name: str, inputs: list[str]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_SCHEDULE,
            CONF_NAME: name,
            CONF_SCHEDULE_INPUTS: inputs,
        },
    )


@pytest.mark.parametrize("definition", ["time", "source"])
async def test_renamed_input_keeps_combined_schedules_on_the_same_window(
    hass: HomeAssistant, definition: str
) -> None:
    """A combined schedule, and one nested above it, follow a renamed input."""
    workday = RealBinary("workday", on=True)
    await add_real(hass, workday)
    schedule = (
        schedule_entry()
        if definition == "time"
        else schedule_entry(
            **{
                CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
                CONF_SCHEDULE_SOURCE: "binary_sensor.workday",
            }
        )
    )
    inner = _combined_schedule_entry("Inner", ["binary_sensor.day"])
    outer = _combined_schedule_entry("Outer", ["binary_sensor.inner"])
    await setup_entries(hass, schedule, inner, outer)
    await settle(hass)
    markers = {
        entity_id: attrs(hass, entity_id)["current_window_start"]
        for entity_id in ("binary_sensor.inner", "binary_sensor.outer")
    }
    assert all(markers.values())

    await rename(hass, "binary_sensor.day", "binary_sensor.daytime")

    assert molight_config(inner)[CONF_SCHEDULE_INPUTS] == ["binary_sensor.daytime"]
    assert molight_config(outer)[CONF_SCHEDULE_INPUTS] == ["binary_sensor.inner"]
    for entity_id, marker in markers.items():
        state = hass.states.get(entity_id)
        assert state.state == "on"
        assert state.attributes["current_window_start"] == marker
        assert state.attributes["resolved_schedules"] == ["binary_sensor.daytime"]

    if definition == "source":
        workday.set(False)
        await settle(hass)
        assert hass.states.get("binary_sensor.outer").state == "off"


# ---------------------------------------------------------------------------
# Virtual Remote, nested lights and the rest of the graph
# ---------------------------------------------------------------------------


async def test_renamed_button_and_target_keep_the_remote_working(
    hass: HomeAssistant,
) -> None:
    """A remote follows its button and its target, and replays no press."""
    button = RealButton("pico_on")
    member = RealLight("real_1")
    await add_real(hass, button, member)
    remote = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_REMOTE,
            CONF_NAME: "Pico",
            CONF_TARGET_LIGHTS: [VIRTUAL],
            CONF_ON_BUTTONS_SINGLE: ["event.pico_on"],
        },
    )
    await setup_entries(hass, make_light_entry(), remote)
    button.press()
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)

    # Neither rename replays the button's last press.
    await rename(hass, "event.pico_on", "event.hall_pico_on")
    await rename(hass, VIRTUAL, "light.hall")
    assert molight_config(remote)[CONF_ON_BUTTONS_SINGLE] == ["event.hall_pico_on"]
    assert molight_config(remote)[CONF_TARGET_LIGHTS] == ["light.hall"]
    assert hass.states.get("light.hall").state == "off"

    button.press()
    await settle(hass)
    assert hass.states.get("light.hall").state == "on"
    assert member.is_on


async def test_renamed_inner_light_is_followed_by_the_light_that_wraps_it(
    hass: HomeAssistant, freezer
) -> None:
    """An outer virtual light keeps driving a renamed virtual light."""
    member = RealLight("real_1")
    await add_real(hass, member)
    inner = make_light_entry(name="Inner", timeout=600)
    outer = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Outer",
            CONF_LIGHTS: ["light.inner"],
            CONF_LIGHT_TIMEOUT: 60,
        },
    )
    await setup_entries(hass, inner, outer)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.outer"}, blocking=True
    )
    await settle(hass)
    assert member.is_on

    await rename(hass, "light.inner", "light.lamp")
    assert molight_config(outer)[CONF_LIGHTS] == ["light.lamp"]
    state = hass.states.get("light.outer")
    assert state.state == "on"
    assert state.attributes["last_off_manual"] is None
    assert state.attributes["last_on_physical"] is None

    await tick(hass, freezer, 61)
    assert hass.states.get("light.outer").state == "off"
    assert hass.states.get("light.lamp").state == "off"
    assert not member.is_on


async def test_rename_reaches_an_entry_that_is_disabled(hass: HomeAssistant) -> None:
    """A disabled entry's references are rewritten too, and it stays disabled."""
    motion = RealBinary("motion")
    await add_real(hass, motion)
    light = make_light_entry(occupancy=OCCUPANCY)
    await setup_entries(hass, occupancy_entry(), light)
    await hass.config_entries.async_set_disabled_by(
        light.entry_id, ConfigEntryDisabler.USER
    )
    await settle(hass)

    await rename(hass, OCCUPANCY, "binary_sensor.presence")
    assert molight_config(light)[CONF_OCCUPANCY_ENTITY] == "binary_sensor.presence"
    assert light.disabled_by is ConfigEntryDisabler.USER
    assert light.state is ConfigEntryState.NOT_LOADED


async def test_renamed_twice_before_reporting_ends_at_the_last_id(
    hass: HomeAssistant, freezer
) -> None:
    """Renames made in quick succession are followed to the final ID."""
    assert await async_setup_component(hass, DOMAIN, {})
    registry = er.async_get(hass)
    registry.async_get_or_create("light", "test", "a", suggested_object_id="a")
    entry = make_light_entry(lights=["light.a"])
    entry.add_to_hass(hass)

    registry.async_update_entity("light.a", new_entity_id="light.b")
    registry.async_update_entity("light.b", new_entity_id="light.c")
    await tick(hass, freezer, RENAME_SETTLE_SECONDS)
    assert molight_config(entry)[CONF_LIGHTS] == ["light.c"]
    assert renamed_to(hass, "light.a") == renamed_to(hass, "light.b") == "light.c"

    # Renamed back, the first ID is the entity's own again.
    registry.async_update_entity("light.c", new_entity_id="light.a")
    await tick(hass, freezer, RENAME_SETTLE_SECONDS)
    assert molight_config(entry)[CONF_LIGHTS] == ["light.a"]
    assert renamed_to(hass, "light.a") is None
    assert renamed_to(hass, "light.c") == "light.a"


async def test_new_entity_under_a_former_id_is_not_the_renamed_one(
    hass: HomeAssistant, freezer
) -> None:
    """A state saved for a former ID is not credited to its next owner."""
    assert await async_setup_component(hass, DOMAIN, {})
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "test", "a", suggested_object_id="lux")
    registry.async_update_entity("sensor.lux", new_entity_id="sensor.window_lux")
    await tick(hass, freezer, RENAME_SETTLE_SECONDS)
    assert same_entity(hass, "sensor.lux", "sensor.window_lux")
    assert not same_entity(hass, "sensor.window_lux", "sensor.lux")
    assert not same_entity(hass, "sensor.lux", None)
    assert same_entity(hass, None, None)

    registry.async_get_or_create("sensor", "test", "b", suggested_object_id="lux")
    assert not same_entity(hass, "sensor.lux", "sensor.window_lux")


async def test_timeout_guard_and_removal_follow_a_renamed_sensor(
    hass: HomeAssistant,
) -> None:
    """The light still constrains its sensor's timeout, and removal still strips it."""
    motion = RealBinary("motion")
    member = RealLight("real_1")
    await add_real(hass, motion, member)
    sensor = occupancy_entry()
    light = make_light_entry(occupancy=OCCUPANCY, timeout=60)
    await setup_entries(hass, sensor, light)
    await rename(hass, OCCUPANCY, "binary_sensor.presence")

    result = await hass.config_entries.options.async_init(sensor.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Room Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion",
            CONF_OCCUPANCY_TIMEOUT: 120,
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_OCCUPANCY_TIMEOUT: "occupancy_timeout_too_long"}

    await hass.config_entries.async_remove(sensor.entry_id)
    await settle(hass)
    assert CONF_OCCUPANCY_ENTITY not in molight_config(light)
