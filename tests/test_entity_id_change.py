"""Entity ID changes: every stored reference follows the entity.

Changing an entity ID in Home Assistant removes the entity and adds it again
under the new one. These tests change entity IDs through the entity registry,
with real entities on both sides, and check that the entries that reference
the entity are rewritten and that nothing reads the change as a change in
the room.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.molight import (
    _ENTITY_ID_KEYS,
    _ENTITY_ID_LIST_KEYS,
    ENTITY_ID_CHANGE_SETTLE_SECONDS,
)
from custom_components.molight.config_flow import (
    SECTION_ADVANCED,
    SECTION_BEHAVIOR,
    SECTION_SENSORS,
    SECTION_WARNING,
)
from custom_components.molight.const import (
    CONF_DOOR_ENTITY,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_HYSTERESIS,
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
    SCHEDULE_END_ACTION_TURN_OFF,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
)
from custom_components.molight.helpers import (
    entity_id_changed_to,
    entry_settings,
    same_entity,
)
from tests.conftest import (
    light_targets,
    make_light_entry,
    make_scheduled_light_entry,
    record_service_calls,
    restart_entries,
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
    change_entity_id,
    start_slow_entity_id_change,
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
    source: str = "binary_sensor.motion", name: str = "Room Occupancy", **cfg: Any
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
            **cfg,
        },
    )


def schedule_entry(name: str = "Day", **cfg: Any) -> MockConfigEntry:
    """Build a Virtual Schedule Sensor that is on at the test's noon."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: name,
            CONF_TIME_WINDOWS: [{"start": {"time": "07:00"}, "end": {"time": "22:00"}}],
            **cfg,
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
_REFERENCING_SETTINGS = {
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


def _entity_ids(cfg: Any) -> set[str]:
    """Every entity ID anywhere in entry settings."""
    if isinstance(cfg, dict):
        return set().union(
            *(_entity_ids(v) for k, v in cfg.items() if k not in _NOT_ENTITY_IDS)
        )
    if isinstance(cfg, list):
        return set().union(*(_entity_ids(v) for v in cfg))
    return {cfg} if isinstance(cfg, str) and "." in cfg else set()


def _with_new_entity_ids(cfg: Any) -> Any:
    """The entry settings with every entity ID replaced by its new entity ID."""
    if isinstance(cfg, dict):
        return {
            k: v if k in _NOT_ENTITY_IDS else _with_new_entity_ids(v)
            for k, v in cfg.items()
        }
    if isinstance(cfg, list):
        return [_with_new_entity_ids(v) for v in cfg]
    if isinstance(cfg, str) and "." in cfg:
        return f"{cfg}_new"
    return cfg


def test_reference_fixtures_cover_every_entity_id_key() -> None:
    """A key added to the entity ID change walk needs entry settings above
    referencing it."""
    used = set()
    for cfg in _REFERENCING_SETTINGS.values():
        used |= set(cfg)
        for side in (CONF_OUTSIDE_SCHEDULE_SETTINGS, CONF_INSIDE_SCHEDULE_SETTINGS):
            used |= set(cfg.get(side, ()))
    assert set(_ENTITY_ID_KEYS) | set(_ENTITY_ID_LIST_KEYS) <= used


@pytest.mark.parametrize("entity_type", list(_REFERENCING_SETTINGS))
@pytest.mark.parametrize("edited", [False, True], ids=["data", "options"])
async def test_entity_id_change_rewrites_every_reference(
    hass: HomeAssistant, freezer, entity_type: str, edited: bool
) -> None:
    """Each entity ID an entry stores follows its entity, in data and in options.

    The entities are registered but report nothing, so their references are
    rewritten once the time allowed for them to report is over.
    """
    cfg = _REFERENCING_SETTINGS[entity_type]
    options = {k: v for k, v in cfg.items() if k != CONF_ENTITY_TYPE}
    entry = MockConfigEntry(domain=DOMAIN, data=cfg, options=options if edited else {})
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    registry = er.async_get(hass)
    for entity_id in _entity_ids(cfg):
        domain, object_id = entity_id.split(".")
        registry.async_get_or_create(
            domain, "test", object_id, suggested_object_id=object_id
        )

    for entity_id in _entity_ids(cfg):
        registry.async_update_entity(entity_id, new_entity_id=f"{entity_id}_new")
    await settle(hass)
    assert dict(entry.data) == cfg

    await tick(hass, freezer, ENTITY_ID_CHANGE_SETTLE_SECONDS)
    assert dict(entry.data) == _with_new_entity_ids(cfg)
    assert dict(entry.options) == (_with_new_entity_ids(options) if edited else {})
    assert entry_settings(entry) == _with_new_entity_ids(cfg)


# ---------------------------------------------------------------------------
# A virtual light's references
# ---------------------------------------------------------------------------


async def test_occupancy_sensor_with_changed_entity_id_keeps_driving_the_light(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """The light follows its occupancy sensor to the new entity ID, mid-visit."""
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
    await change_entity_id(hass, OCCUPANCY, "binary_sensor.kitchen_presence")

    assert hass.states.get(OCCUPANCY) is None
    assert hass.states.get("binary_sensor.kitchen_presence").state == "on"
    assert attrs(hass)["molight_state"] == "occupied"
    assert attrs(hass)["last_off_manual"] is None
    assert light_targets(calls, "turn_on") == light_targets(calls, "turn_off") == []

    # The clear and the next visit arrive under the new entity ID.
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
    "changed",
    ["occupancy", "source", "member"],
)
async def test_entity_id_change_is_not_a_new_visit_for_a_light_turned_off_manually(
    hass: HomeAssistant, virtual_light_behavior_variant, changed: str
) -> None:
    """A light turned off in an occupied room stays off through an entity ID change.

    Whether the occupancy sensor, its motion source or the real light changes
    its entity ID, the visit that was already under way does not light the room.
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
    }[changed]
    await change_entity_id(hass, old_id, f"{old_id}_new")

    assert hass.states.get(VIRTUAL).state == "off"
    assert not member.is_on
    assert light_targets(calls, "turn_on") == []
    assert attrs(hass)["last_off_manual"] == turned_off
    assert attrs(hass)["last_on_occupancy"] < turned_off


async def test_real_light_with_changed_entity_id_is_not_a_change_at_the_wall(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """A real light that changes entity ID stays on, unclaimed, and is still driven."""
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
    await change_entity_id(hass, MEMBER, "light.kitchen_bulb")

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == "active"
    assert state.attributes["brightness"] == 120
    for attribute in ("last_on_physical", "last_on_virtual", "last_off_manual"):
        assert state.attributes[attribute] == before[attribute]
    assert light_targets(calls, "turn_on") == light_targets(calls, "turn_off") == []

    # The timeout turns the real light off under its new entity ID.
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"
    assert light_targets(calls, "turn_off") == [["light.kitchen_bulb", "light.real_2"]]
    assert not member.is_on
    assert attrs(hass)["last_off_manual"] is None


async def test_entity_id_change_waits_for_a_real_light_that_is_slow_to_come_back(
    hass: HomeAssistant, virtual_light_behavior_variant
) -> None:
    """The light reloads only once its real light reports again under its new entity ID.

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
    await start_slow_entity_id_change(hass, member, "light.kitchen_bulb")
    assert hass.states.get(MEMBER) is None
    assert entry_settings(entry)[CONF_LIGHTS] == [MEMBER]
    assert hass.states.get(VIRTUAL).state == "on"

    member.added_gate.set()
    await settle(hass)
    assert entry_settings(entry)[CONF_LIGHTS] == ["light.kitchen_bulb"]
    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == "occupied"
    assert state.attributes["brightness"] == 90
    assert state.attributes["last_on_physical"] is None
    assert light_targets(calls, "turn_on") == []


async def test_keep_on_entity_with_changed_entity_id_keeps_holding(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """A hold is kept through its entity ID change and released under the new one."""
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

    await change_entity_id(hass, "switch.guest_mode", "switch.visitors")
    assert attrs(hass)["auto_off_held"] is True
    await tick(hass, freezer, 3600)
    assert hass.states.get(VIRTUAL).state == "on"

    hold.set(False)
    await settle(hass)
    assert attrs(hass)["auto_off_held"] is False
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"


async def test_door_with_changed_entity_id_keeps_holding_and_closing(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """An open door holds the light through its entity ID change; closing counts."""
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
    await change_entity_id(
        hass, "binary_sensor.pantry_door", "binary_sensor.larder_door"
    )
    assert attrs(hass)["molight_state"] == "occupied"
    assert attrs(hass)["last_on_door"] == opened
    assert light_targets(calls, "turn_on") == []

    door.set(False)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "countdown"
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"


async def test_maintain_and_illuminance_sensors_keep_their_roles_on_new_entity_ids(
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
    await change_entity_id(hass, "binary_sensor.room_bright", "binary_sensor.daylight")
    await change_entity_id(hass, "binary_sensor.desk_occupancy", "binary_sensor.desk")

    # Bright: occupancy may not light the room.
    motion.set(True)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    # Dark: it does, and the maintain sensor holds the light.
    lux.set(1)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "occupied"
    await change_entity_id(hass, OCCUPANCY, "binary_sensor.room")
    assert attrs(hass)["molight_state"] == "occupied"
    motion.set(False)
    await settle(hass)
    assert attrs(hass)["molight_state"] == "countdown"


async def test_select_entities_on_new_entity_ids_still_apply_the_turn_on_selection(
    hass: HomeAssistant, virtual_light_behavior_variant
) -> None:
    """The turn-on selection reads the option source entity and sets the select
    entity by their new entity IDs."""
    select = RealSelect("preset")
    source = RealSelect("mood", "Bright")
    member = RealLight("real_1")
    await add_real(hass, select, source, member)
    await setup_entries(
        hass,
        make_light_entry(
            turn_on_select_entity="select.preset",
            turn_on_select_option="Cozy",
            turn_on_select_source_entity="select.mood",
        ),
    )
    await change_entity_id(hass, "select.preset", "select.wled_preset")
    await change_entity_id(hass, "select.mood", "select.theme")

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert select.current_option == "Bright"
    assert attrs(hass)["last_turn_on_selection_source"] == "select.theme"
    assert member.is_on


@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("definition", ["time", "source"])
async def test_follow_schedule_with_changed_entity_id_is_not_a_new_window(
    hass: HomeAssistant, virtual_light_behavior_variant, definition: str
) -> None:
    """A manual off mid-window holds when the schedule's entity ID changes.

    The window marker belongs to the schedule that set it, and a schedule
    whose entity ID changed is still that schedule.
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
    await change_entity_id(hass, "binary_sensor.day", "binary_sensor.daytime")
    assert entry_settings(light)[CONF_SCHEDULE_ENTITY] == "binary_sensor.daytime"
    assert hass.states.get(VIRTUAL).state == "off"
    assert light_targets(calls, "turn_on") == []
    assert attrs(hass)["schedule_window_start"] == marker
    assert attrs(hass)["schedule_window_schedule"] == "binary_sensor.daytime"

    if definition == "source":
        # Nor is the schedule's own source changing entity ID a new window.
        await change_entity_id(
            hass, "binary_sensor.workday", "binary_sensor.working_day"
        )
        assert attrs(hass, "binary_sensor.daytime")["current_window_start"] == marker
        assert hass.states.get(VIRTUAL).state == "off"
        assert light_targets(calls, "turn_on") == []
        # The end of the window still arrives.
        workday.set(False)
        await settle(hass)
        assert hass.states.get("binary_sensor.daytime").state == "off"
        assert attrs(hass)["schedule_window_start"] is None


@pytest.mark.regular_virtual_light_only
async def test_gate_schedule_with_changed_entity_id_still_gates(
    hass: HomeAssistant, virtual_light_behavior_variant
) -> None:
    """A gate schedule that changes entity ID can still open."""
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
    await change_entity_id(hass, "binary_sensor.day", "binary_sensor.daytime")

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


async def test_settings_schedule_with_changed_entity_id_is_not_a_boundary(
    hass: HomeAssistant,
) -> None:
    """A manual off inside the window still pauses standby after the entity ID change.

    What the light restored about its settings belongs to the schedule that
    selected them, and a schedule whose entity ID changed is still that schedule.
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
    await change_entity_id(hass, "binary_sensor.day", "binary_sensor.daytime")

    assert entry_settings(light)[CONF_SCHEDULE_ENTITY] == "binary_sensor.daytime"
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


async def test_motion_source_with_changed_entity_id_continues_the_same_visit(
    hass: HomeAssistant, freezer
) -> None:
    """A source that changes entity ID mid-visit neither drops out nor starts one."""
    motion = RealBinary("motion")
    await add_real(hass, motion)
    entry = occupancy_entry(**{CONF_FALSE_DETECTION_GRACE: 3})
    await setup_entries(hass, entry)
    motion.set(True)
    await settle(hass)
    before = dict(attrs(hass, OCCUPANCY))
    await tick(hass, freezer, 20)

    # While the source is gone, on its way to the new entity ID, it has not dropped out.
    await start_slow_entity_id_change(hass, motion, "binary_sensor.hall_motion")
    assert hass.states.get("binary_sensor.motion") is None
    assert (
        attrs(hass, OCCUPANCY)["latest_occupied_time"]
        == (before["latest_occupied_time"])
    )
    motion.added_gate.set()
    await settle(hass)

    assert entry_settings(entry)[CONF_OCCUPANCY_SENSOR] == "binary_sensor.hall_motion"
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
    """Only an entity ID change is spared: a source that is deleted clears after the
    clear-after-unavailable timeout."""
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
async def test_constituent_with_changed_entity_id_does_not_clear_the_combined_sensor(
    hass: HomeAssistant, role: str
) -> None:
    """The combined sensor stays on through a constituent's entity ID change.

    A clear and a fresh start would be a new visit to the lights that reference
    it: one turned off manually in the occupied room would come back on.
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
    await change_entity_id(hass, old_id, f"{old_id}_new")

    key = CONF_TRIGGER_SENSORS if role == "trigger" else CONF_MAINTAIN_SENSORS
    assert entry_settings(combined)[key] == [f"{old_id}_new"]
    assert hass.states.get("binary_sensor.floor_occupancy").state == "on"
    assert "off" not in seen
    assert hass.states.get(VIRTUAL).state == "off"
    assert light_targets(calls, "turn_on") == []

    # The real clear still arrives through the constituent under its new entity ID.
    (motion if role == "trigger" else desk).set(False)
    await settle(hass)
    assert hass.states.get("binary_sensor.floor_occupancy").state == "off"


async def test_constituent_slow_to_come_back_does_not_clear_the_combined_sensor(
    hass: HomeAssistant,
) -> None:
    """A constituent whose entity ID changed, gone for a while, has not dropped out."""
    constituent = RealBinary("presence", on=True)
    await add_real(hass, constituent)
    combined = _combined_occupancy_entry(["binary_sensor.presence"])
    await setup_entries(hass, combined)
    before = dict(attrs(hass, "binary_sensor.floor_occupancy"))

    await start_slow_entity_id_change(hass, constituent, "binary_sensor.radar")
    assert hass.states.get("binary_sensor.presence") is None
    state = hass.states.get("binary_sensor.floor_occupancy")
    assert state.state == "on"
    assert state.attributes == before

    constituent.added_gate.set()
    await settle(hass)
    assert entry_settings(combined)[CONF_TRIGGER_SENSORS] == ["binary_sensor.radar"]
    assert hass.states.get("binary_sensor.floor_occupancy").state == "on"
    constituent.set(False)
    await settle(hass)
    assert hass.states.get("binary_sensor.floor_occupancy").state == "off"


async def test_combined_clear_flag_survives_a_constituents_entity_id_change(
    hass: HomeAssistant,
) -> None:
    """The last clear's false-detection flag still describes the same sensors."""
    constituent = RealBinary("presence")
    await add_real(hass, constituent)
    await setup_entries(hass, _combined_occupancy_entry(["binary_sensor.presence"]))
    # A visit that never advanced latest_occupied_time is a false detection.
    constituent.set(True)
    await settle(hass)
    constituent.set(False)
    await settle(hass)
    assert attrs(hass, "binary_sensor.floor_occupancy")["last_clear_false_detection"]

    await change_entity_id(hass, "binary_sensor.presence", "binary_sensor.radar")
    state = hass.states.get("binary_sensor.floor_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is True
    assert state.attributes["false_detection_count"] == 1


async def test_deleted_constituent_still_clears_the_combined_sensor(
    hass: HomeAssistant,
) -> None:
    """Only an entity ID change is spared: a constituent that is deleted drops out."""
    constituent = RealBinary("presence", on=True)
    await add_real(hass, constituent)
    await setup_entries(hass, _combined_occupancy_entry(["binary_sensor.presence"]))
    assert hass.states.get("binary_sensor.floor_occupancy").state == "on"

    er.async_get(hass).async_remove("binary_sensor.presence")
    await settle(hass)
    assert hass.states.get("binary_sensor.floor_occupancy").state == "off"


async def test_lux_source_with_changed_entity_id_keeps_the_reading(
    hass: HomeAssistant,
) -> None:
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

    await change_entity_id(hass, "sensor.lux", "sensor.window_lux")
    assert entry_settings(entry)[CONF_ILLUMINANCE_SENSOR] == "sensor.window_lux"
    assert hass.states.get("binary_sensor.room_bright").state == "on"
    lux.set(1)
    await settle(hass)
    assert hass.states.get("binary_sensor.room_bright").state == "off"


@pytest.mark.parametrize("changed", ["source", "sensor"])
async def test_entity_id_change_keeps_an_illuminance_sensors_side_of_the_band(
    hass: HomeAssistant, changed: str
) -> None:
    """A source whose entity ID changed is the same source: its held reading stays.

    At 8 lx inside a 7 to 13 lx band the sensor is bright only because it
    was; a changed source would start over and read dark.
    """
    lux = RealLux("lux", 100)
    await add_real(hass, lux)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
            CONF_NAME: "Room Bright",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux",
            CONF_ILLUMINANCE_THRESHOLD: 10.0,
            CONF_ILLUMINANCE_HYSTERESIS: 3.0,
        },
    )
    await setup_entries(hass, entry)
    lux.set(8)
    await settle(hass)
    assert hass.states.get("binary_sensor.room_bright").state == "on"

    if changed == "source":
        await change_entity_id(hass, "sensor.lux", "sensor.window_lux")
        assert hass.states.get("binary_sensor.room_bright").state == "on"
    else:
        await change_entity_id(
            hass, "binary_sensor.room_bright", "binary_sensor.daylight"
        )
        assert hass.states.get("binary_sensor.daylight").state == "on"


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
async def test_input_with_changed_entity_id_keeps_combined_schedules_on_the_same_window(
    hass: HomeAssistant, definition: str
) -> None:
    """A combined schedule, and one nested above it, follow an input's new entity ID."""
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

    await change_entity_id(hass, "binary_sensor.day", "binary_sensor.daytime")

    assert entry_settings(inner)[CONF_SCHEDULE_INPUTS] == ["binary_sensor.daytime"]
    assert entry_settings(outer)[CONF_SCHEDULE_INPUTS] == ["binary_sensor.inner"]
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
# Virtual Remote, nested lights and the rest of the references
# ---------------------------------------------------------------------------


async def test_button_and_target_with_changed_entity_ids_keep_the_remote_working(
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

    # Neither entity ID change replays the button's last press.
    await change_entity_id(hass, "event.pico_on", "event.hall_pico_on")
    await change_entity_id(hass, VIRTUAL, "light.hall")
    assert entry_settings(remote)[CONF_ON_BUTTONS_SINGLE] == ["event.hall_pico_on"]
    assert entry_settings(remote)[CONF_TARGET_LIGHTS] == ["light.hall"]
    assert hass.states.get("light.hall").state == "off"

    button.press()
    await settle(hass)
    assert hass.states.get("light.hall").state == "on"
    assert member.is_on


async def test_wrapping_light_follows_the_inner_lights_entity_id_change(
    hass: HomeAssistant, freezer
) -> None:
    """An outer virtual light keeps driving a virtual light whose entity ID changed."""
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

    await change_entity_id(hass, "light.inner", "light.lamp")
    assert entry_settings(outer)[CONF_LIGHTS] == ["light.lamp"]
    state = hass.states.get("light.outer")
    assert state.state == "on"
    assert state.attributes["last_off_manual"] is None
    assert state.attributes["last_on_physical"] is None

    await tick(hass, freezer, 61)
    assert hass.states.get("light.outer").state == "off"
    assert hass.states.get("light.lamp").state == "off"
    assert not member.is_on


async def test_entity_id_change_reaches_an_entry_that_is_disabled(
    hass: HomeAssistant,
) -> None:
    """A disabled entry's references are rewritten too, and it stays disabled."""
    motion = RealBinary("motion")
    await add_real(hass, motion)
    light = make_light_entry(occupancy=OCCUPANCY)
    await setup_entries(hass, occupancy_entry(), light)
    await hass.config_entries.async_set_disabled_by(
        light.entry_id, ConfigEntryDisabler.USER
    )
    await settle(hass)

    await change_entity_id(hass, OCCUPANCY, "binary_sensor.presence")
    assert entry_settings(light)[CONF_OCCUPANCY_ENTITY] == "binary_sensor.presence"
    assert light.disabled_by is ConfigEntryDisabler.USER
    assert light.state is ConfigEntryState.NOT_LOADED


async def test_entity_id_changed_twice_before_reporting_ends_at_the_last_one(
    hass: HomeAssistant, freezer
) -> None:
    """Entity ID changes made in quick succession are followed to the final one."""
    assert await async_setup_component(hass, DOMAIN, {})
    registry = er.async_get(hass)
    registry.async_get_or_create("light", "test", "a", suggested_object_id="a")
    entry = make_light_entry(lights=["light.a"])
    entry.add_to_hass(hass)

    registry.async_update_entity("light.a", new_entity_id="light.b")
    registry.async_update_entity("light.b", new_entity_id="light.c")
    await tick(hass, freezer, ENTITY_ID_CHANGE_SETTLE_SECONDS)
    assert entry_settings(entry)[CONF_LIGHTS] == ["light.c"]
    assert (
        entity_id_changed_to(hass, "light.a")
        == entity_id_changed_to(hass, "light.b")
        == "light.c"
    )

    # Changed back, the first entity ID is the entity's own again.
    registry.async_update_entity("light.c", new_entity_id="light.a")
    await tick(hass, freezer, ENTITY_ID_CHANGE_SETTLE_SECONDS)
    assert entry_settings(entry)[CONF_LIGHTS] == ["light.a"]
    assert entity_id_changed_to(hass, "light.a") is None
    assert entity_id_changed_to(hass, "light.c") == "light.a"


async def test_new_entity_under_a_former_entity_id_is_not_the_one_that_had_it(
    hass: HomeAssistant, freezer
) -> None:
    """A state saved for a former entity ID is not credited to its next owner."""
    assert await async_setup_component(hass, DOMAIN, {})
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "test", "a", suggested_object_id="lux")
    registry.async_update_entity("sensor.lux", new_entity_id="sensor.window_lux")
    await tick(hass, freezer, ENTITY_ID_CHANGE_SETTLE_SECONDS)
    assert same_entity(hass, "sensor.lux", "sensor.window_lux")
    assert not same_entity(hass, "sensor.window_lux", "sensor.lux")
    assert not same_entity(hass, "sensor.lux", None)
    assert same_entity(hass, None, None)

    registry.async_get_or_create("sensor", "test", "b", suggested_object_id="lux")
    assert not same_entity(hass, "sensor.lux", "sensor.window_lux")


@pytest.mark.parametrize("path", ["reports", "times_out"])
@pytest.mark.parametrize("saved_by", ["create", "options"])
@pytest.mark.parametrize(
    ("domain", "role"),
    [("light", CONF_LIGHTS), ("binary_sensor", CONF_HOLD_ENTITIES)],
    ids=["light_member", "keep_on_entity"],
)
async def test_reference_saved_to_an_entity_that_took_a_former_entity_id_stays_with_it(
    hass: HomeAssistant, freezer, domain: str, role: str, saved_by: str, path: str
) -> None:
    """A reference from before another entity took the entity ID follows the
    entity that had it; one saved afterwards names the new entity and is left
    alone."""
    registry = er.async_get(hass)
    old, new = f"{domain}.original", f"{domain}.new_id"
    registry.async_get_or_create(domain, "test", "a", suggested_object_id="original")
    keeper = make_light_entry(name="Keeper", lights=["light.lamp"], hold_entities=[old])
    wrapper = make_light_entry(name="Wrapper", lights=["light.bulb"])
    await setup_entries(hass, keeper, *([wrapper] if saved_by == "options" else []))
    registry.async_update_entity(old, new_entity_id=new)
    await settle(hass)
    registry.async_get_or_create(domain, "test", "b", suggested_object_id="original")
    assert registry.async_get(old).unique_id == "b"

    refs = {CONF_LIGHTS: ["light.bulb"], role: [old]}
    submitted = {
        CONF_NAME: "Wrapper",
        CONF_LIGHTS: refs[CONF_LIGHTS],
        CONF_LIGHT_TIMEOUT: 60,
        SECTION_SENSORS: {CONF_HOLD_ENTITIES: refs.get(CONF_HOLD_ENTITIES, [])},
        SECTION_BEHAVIOR: {},
        SECTION_WARNING: {},
    }
    if saved_by == "create":
        manager = hass.config_entries.flow
        result = await manager.async_init(DOMAIN, context={"source": "user"})
        result = await manager.async_configure(
            result["flow_id"], {"next_step_id": "create"}
        )
        result = await manager.async_configure(
            result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
        )
        submitted[SECTION_ADVANCED] = {}
    else:
        manager = hass.config_entries.options
        result = await manager.async_init(wrapper.entry_id)
    result = await manager.async_configure(result["flow_id"], submitted)
    assert result["type"] == FlowResultType.CREATE_ENTRY, result.get("errors")
    saved = result["result"] if saved_by == "create" else wrapper
    await settle(hass)

    if path == "reports":
        hass.states.async_set(new, "off")
        await settle(hass)
    else:
        await tick(hass, freezer, ENTITY_ID_CHANGE_SETTLE_SECONDS)
    assert entry_settings(keeper)[CONF_HOLD_ENTITIES] == [new]
    assert entry_settings(saved)[role] == [old]


async def test_timeout_guard_and_removal_follow_a_sensor_whose_entity_id_changed(
    hass: HomeAssistant,
) -> None:
    """The light still constrains its sensor's occupancy timeout, and removal still
    strips it."""
    motion = RealBinary("motion")
    member = RealLight("real_1")
    await add_real(hass, motion, member)
    sensor = occupancy_entry()
    light = make_light_entry(occupancy=OCCUPANCY, timeout=60)
    await setup_entries(hass, sensor, light)
    await change_entity_id(hass, OCCUPANCY, "binary_sensor.presence")

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
    assert CONF_OCCUPANCY_ENTITY not in entry_settings(light)


# ---------------------------------------------------------------------------
# MoLight entities whose entity ID changes
# ---------------------------------------------------------------------------

SWITCH = "switch.matrix_light_auto_off"


async def test_auto_off_switch_with_changed_entity_id_keeps_the_hold(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """An Auto-off switch that is off stays off under its new entity ID."""
    member = RealLight("real_1", on=True)
    await add_real(hass, member)
    await setup_entries(hass, make_light_entry(timeout=60))
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": SWITCH}, blocking=True
    )
    await settle(hass)
    assert attrs(hass)["auto_off_held"] is True

    await change_entity_id(hass, SWITCH, "switch.guest_hold")
    assert hass.states.get("switch.guest_hold").state == "off"
    assert attrs(hass)["auto_off_held"] is True
    await tick(hass, freezer, 3600)
    assert hass.states.get(VIRTUAL).state == "on"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.guest_hold"}, blocking=True
    )
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"


async def test_switch_entity_id_changed_back_takes_its_latest_state_along(
    hass: HomeAssistant, virtual_light_behavior_variant
) -> None:
    """What was saved under an entity ID earlier does not come back with it."""
    await add_real(hass, RealLight("real_1", on=True))
    await setup_entries(hass, make_light_entry(timeout=60))
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": SWITCH}, blocking=True
    )
    await change_entity_id(hass, SWITCH, "switch.guest_hold")
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.guest_hold"}, blocking=True
    )
    await settle(hass)
    assert attrs(hass)["auto_off_held"] is False

    await change_entity_id(hass, "switch.guest_hold", SWITCH)
    assert hass.states.get(SWITCH).state == "on"
    assert attrs(hass)["auto_off_held"] is False


@pytest.mark.regular_virtual_light_only
async def test_follow_light_with_changed_entity_id_keeps_a_manual_off(
    hass: HomeAssistant, virtual_light_behavior_variant
) -> None:
    """A follow light turned off mid-window is not lit again by its entity ID change."""
    member = RealLight("real_1")
    await add_real(hass, member)
    await setup_entries(
        hass,
        schedule_entry(),
        make_light_entry(
            schedule="binary_sensor.day", schedule_mode=SCHEDULE_MODE_FOLLOW
        ),
    )
    await settle(hass)
    assert member.is_on
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    before = dict(attrs(hass))

    calls = record_service_calls(hass)
    await change_entity_id(hass, VIRTUAL, "light.porch")
    state = hass.states.get("light.porch")
    assert state.state == "off"
    assert light_targets(calls, "turn_on") == []
    for attribute in ("schedule_window_start", "last_off_manual", "last_on_virtual"):
        assert state.attributes[attribute] == before[attribute]


async def test_scheduled_light_with_changed_entity_id_does_not_replay_an_applied_end(
    hass: HomeAssistant,
) -> None:
    """A light lit manually after its window ended stays on through its entity ID
    change.

    The same object is added again, and what it restored at startup (inside
    the schedule) is long out of date.
    """
    workday = RealBinary("workday", on=True)
    member = RealLight("real_1", on=True)
    await add_real(hass, workday, member)
    light = make_scheduled_light_entry(
        schedule="binary_sensor.day", schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF
    )
    source_schedule = schedule_entry(
        **{
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: "binary_sensor.workday",
        }
    )
    await setup_entries(hass, source_schedule, light)
    await settle(hass)
    virtual = "light.scheduled_light"
    # Restart inside the schedule, so that is what the light restores.
    await restart_entries(hass, source_schedule, light)
    await settle(hass)
    assert attrs(hass, virtual)["active_settings"] == "inside_schedule"

    workday.set(False)
    await settle(hass)
    assert not member.is_on
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": virtual}, blocking=True
    )
    await settle(hass)
    assert member.is_on

    calls = record_service_calls(hass)
    await change_entity_id(hass, virtual, "light.porch")
    assert light_targets(calls, "turn_off") == []
    state = hass.states.get("light.porch")
    assert state.state == "on"
    assert state.attributes["active_settings"] == "outside_schedule"
    assert member.is_on


@pytest.mark.parametrize("resting", [True, False], ids=["standby", "turned-off"])
async def test_scheduled_light_with_changed_entity_id_keeps_its_standby(
    hass: HomeAssistant, resting: bool
) -> None:
    """A light resting at standby stays there, and one turned off stays off."""
    member = RealLight("real_1")
    await add_real(hass, member)
    await setup_entries(
        hass,
        schedule_entry(),
        make_scheduled_light_entry(
            schedule="binary_sensor.day",
            inside={CONF_LIGHT_TIMEOUT: 60, CONF_STANDBY_BRIGHTNESS: 10},
        ),
    )
    await settle(hass)
    virtual = "light.scheduled_light"
    assert attrs(hass, virtual)["molight_state"] == "standby"
    if not resting:
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": virtual}, blocking=True
        )
        await settle(hass)

    await change_entity_id(hass, virtual, "light.porch")
    state = hass.states.get("light.porch")
    assert state.attributes["molight_state"] == ("standby" if resting else "idle")
    assert state.attributes["standby_suppressed"] is not resting
    assert member.is_on is resting


async def test_occupancy_sensor_with_changed_entity_id_keeps_its_history(
    hass: HomeAssistant, freezer
) -> None:
    """latest_occupied_time, the false-detection count and the visit survive."""
    motion = RealBinary("motion")
    await add_real(hass, motion)
    await setup_entries(hass, occupancy_entry(**{CONF_FALSE_DETECTION_GRACE: 3}))
    # One false detection (on for exactly the timeout), one real visit.
    motion.set(True)
    await settle(hass)
    await tick(hass, freezer, 30)
    motion.set(False)
    await settle(hass)
    motion.set(True)
    await settle(hass)
    await tick(hass, freezer, 300)
    motion.set(False)
    await settle(hass)
    await tick(hass, freezer, 60)
    motion.set(True)
    await settle(hass)
    before = dict(attrs(hass, OCCUPANCY))
    assert before["false_detection_count"] == 1
    assert before["latest_occupied_time"] is not None
    assert before["last_on_time"] is not None

    await change_entity_id(hass, OCCUPANCY, "binary_sensor.presence")
    state = hass.states.get("binary_sensor.presence")
    assert state.state == "on"
    assert dict(state.attributes) == before


async def test_occupancy_sensor_with_changed_entity_id_keeps_its_dropout_deadline(
    hass: HomeAssistant, freezer
) -> None:
    """A clear-after-unavailable countdown runs on to the same moment."""
    motion = RealBinary("motion", on=True)
    await add_real(hass, motion)
    await setup_entries(hass, occupancy_entry())
    assert hass.states.get(OCCUPANCY).state == "on"
    motion._attr_available = False
    motion.async_write_ha_state()
    await settle(hass)

    await tick(hass, freezer, 40)
    await change_entity_id(hass, OCCUPANCY, "binary_sensor.presence")
    assert hass.states.get("binary_sensor.presence").state == "on"
    await tick(hass, freezer, 19)
    assert hass.states.get("binary_sensor.presence").state == "on"
    await tick(hass, freezer, 2)
    state = hass.states.get("binary_sensor.presence")
    assert state.state == "off"
    assert state.attributes["last_clear_unavailable"] is True


async def test_combined_sensor_with_changed_entity_id_keeps_its_history(
    hass: HomeAssistant,
) -> None:
    """A combined sensor keeps its false-detection count and flag."""
    constituent = RealBinary("presence")
    await add_real(hass, constituent)
    await setup_entries(hass, _combined_occupancy_entry(["binary_sensor.presence"]))
    constituent.set(True)
    await settle(hass)
    constituent.set(False)
    await settle(hass)
    before = dict(attrs(hass, "binary_sensor.floor_occupancy"))
    assert before["false_detection_count"] == 1

    await change_entity_id(hass, "binary_sensor.floor_occupancy", "binary_sensor.floor")
    assert dict(attrs(hass, "binary_sensor.floor")) == before


async def test_schedule_with_changed_entity_id_keeps_its_window_marker(
    hass: HomeAssistant,
) -> None:
    """A source-backed schedule does not date its window from the entity ID change.

    A moved marker would be a new window to the lights that follow it.
    """
    marker = "2026-01-14T09:00:00-08:00"
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("binary_sensor.day", "on"),
                {
                    "is_on": True,
                    "current_window_start": marker,
                    "source_entity": "binary_sensor.workday",
                    "invert": False,
                },
            )
        ],
    )
    # The source dates its state from now, later than the restored marker.
    workday = RealBinary("workday", on=True)
    await add_real(hass, workday)
    schedule = schedule_entry(
        **{
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: "binary_sensor.workday",
        }
    )
    await setup_entries(hass, schedule)
    assert attrs(hass, "binary_sensor.day")["current_window_start"] == marker

    await change_entity_id(hass, "binary_sensor.day", "binary_sensor.daytime")
    state = hass.states.get("binary_sensor.daytime")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker


async def test_remote_sensor_with_changed_entity_id_keeps_its_last_action(
    hass: HomeAssistant,
) -> None:
    """The Last Action sensor is not restored, and keeps its value in memory."""
    button = RealButton("pico_on")
    await add_real(hass, button, RealLight("real_1"))
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
    before = hass.states.get("sensor.pico_last_action")
    assert before.state == "turn_on"

    await change_entity_id(hass, "sensor.pico_last_action", "sensor.hall_pico")
    state = hass.states.get("sensor.hall_pico")
    assert state.state == "turn_on"
    assert dict(state.attributes) == dict(before.attributes)
