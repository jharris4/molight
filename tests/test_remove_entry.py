"""Config-entry removal (teardown) tests for every MoLight virtual device type.

Removing an entry unloads its entities, firing every async_on_remove cleanup.
These tests pin that teardown down for each device type: the entry unloads,
its entities disappear, and nothing is logged at ERROR level.

The light additionally defers its subscriptions behind a one-time
EVENT_HOMEASSISTANT_STARTED listener when added before HA has started. That
listener's remove callback is not idempotent, so a naive teardown removed it a
second time and logged "Unable to remove unknown job listener"; the deferred
test below is the regression guard for that.
"""

from __future__ import annotations

import logging

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.const import (
    ACTIVE_SETTINGS_INSIDE,
    ACTIVE_SETTINGS_OUTSIDE,
    ATTR_ACTIVE_SETTINGS,
    CONF_DOOR_ENTITY,
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_ON_BUTTONS_SINGLE,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_SCHEDULE_ENTITY,
    CONF_TARGET_LIGHTS,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_REMOTE,
    ENTITY_TYPE_SCHEDULE,
    SCHEDULE_MODE_GATE,
)
from custom_components.molight.helpers import molight_config
from tests.conftest import make_light_entry, make_scheduled_light_entry, settle

REMOVE_ERROR = "Unable to remove unknown job listener"


def _occupancy_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Rm Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_rm",
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
    )


def _combined_occupancy_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Rm Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.trigger_rm"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.maintain_rm"],
        },
    )


def _illuminance_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
            CONF_NAME: "Rm Illuminance",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux_rm",
            CONF_ILLUMINANCE_THRESHOLD: 10.0,
        },
    )


def _schedule_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Rm Schedule",
            CONF_TIME_WINDOWS: [{"start": "07:00", "end": "22:00"}],
        },
    )


def _light_entry() -> MockConfigEntry:
    return make_light_entry(name="Rm Light")


def _scheduled_light_entry() -> MockConfigEntry:
    return make_scheduled_light_entry(
        name="Rm Scheduled Light", schedule="binary_sensor.rm_schedule"
    )


def _remote_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_REMOTE,
            CONF_NAME: "Rm Remote",
            CONF_TARGET_LIGHTS: ["light.rm_target"],
            CONF_ON_BUTTONS_SINGLE: ["event.rm_button"],
        },
    )


def _entities_for(hass: HomeAssistant, entry: MockConfigEntry) -> list[str]:
    """Entity ids this entry currently owns, via the entity registry."""
    registry = er.async_get(hass)
    return [
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    ]


async def _remove_and_assert_clean(
    hass: HomeAssistant, entry: MockConfigEntry, caplog
) -> None:
    """Remove the entry and assert entities vanish without any error logged."""
    entities = _entities_for(hass, entry)
    assert entities, "entry created no entities to remove"
    for entity_id in entities:
        assert hass.states.get(entity_id) is not None

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    assert REMOVE_ERROR not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "removal logged errors: "
        + "; ".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
        )
    )
    assert entry.entry_id not in {
        e.entry_id for e in hass.config_entries.async_entries(DOMAIN)
    }
    for entity_id in entities:
        assert hass.states.get(entity_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_entry",
    [
        _occupancy_entry,
        _combined_occupancy_entry,
        _illuminance_entry,
        _schedule_entry,
        _light_entry,
        _scheduled_light_entry,
        _remote_entry,
    ],
    ids=[
        "occupancy",
        "combined_occupancy",
        "illuminance",
        "schedule",
        "light",
        "scheduled_light",
        "remote",
    ],
)
async def test_remove_entry_is_clean(hass: HomeAssistant, caplog, make_entry) -> None:
    """Each virtual device type removes cleanly once HA is running."""
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    await _remove_and_assert_clean(hass, entry, caplog)


@pytest.mark.asyncio
async def test_remove_light_added_before_startup_is_clean(
    hass: HomeAssistant, caplog
) -> None:
    """Regression: a light added while HA is not yet running defers its
    subscriptions behind a one-time EVENT_HOMEASSISTANT_STARTED listener. After
    that listener fires, removing the entry must not try to remove it again
    (which logged "Unable to remove unknown job listener")."""
    hass.set_state(CoreState.not_running)

    entry = _light_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Fire startup so the deferred once-listener runs (and self-removes).
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)

    await _remove_and_assert_clean(hass, entry, caplog)


@pytest.mark.asyncio
async def test_remove_light_added_before_startup_never_started_is_clean(
    hass: HomeAssistant, caplog
) -> None:
    """The other branch: the entry is removed while still not-running, so the
    once-listener never fired and teardown must remove it exactly once."""
    hass.set_state(CoreState.not_running)

    entry = _light_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await _remove_and_assert_clean(hass, entry, caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [True, False], ids=["started", "never_started"])
async def test_remove_remote_added_before_startup_is_clean(
    hass: HomeAssistant, caplog, started: bool
) -> None:
    """The remote defers its button subscription behind the same one-time
    EVENT_HOMEASSISTANT_STARTED listener as the light; both the fired and
    the never-fired variants must tear down exactly once."""
    hass.set_state(CoreState.not_running)

    entry = _remote_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    if started:
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await settle(hass)

    await _remove_and_assert_clean(hass, entry, caplog)


@pytest.mark.asyncio
async def test_remove_entry_strips_references_from_dependents(
    hass: HomeAssistant,
) -> None:
    """Removing a sensor entry cleans its references out of surviving entries.

    A dangling reference silently degrades the dependents — worst of all a
    gate-mode schedule reference, which would read as a gate that never opens
    and block every automatic turn-on.
    """
    occupancy = _occupancy_entry()  # registers binary_sensor.rm_occupancy
    schedule = _schedule_entry()  # registers binary_sensor.rm_schedule
    combined = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Rm Combined",
            CONF_TRIGGER_SENSORS: [
                "binary_sensor.rm_occupancy",
                "binary_sensor.other",
            ],
        },
    )
    light = make_light_entry(
        name="Rm Light",
        occupancy="binary_sensor.rm_occupancy",
        schedule="binary_sensor.rm_schedule",
        schedule_mode=SCHEDULE_MODE_GATE,
        hold_entities=["binary_sensor.rm_occupancy", "input_boolean.guest"],
    )
    for entry in (occupancy, schedule, combined, light):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    await hass.config_entries.async_remove(occupancy.entry_id)
    await settle(hass)

    light_cfg = molight_config(light)
    assert CONF_OCCUPANCY_ENTITY not in light_cfg
    assert light_cfg[CONF_SCHEDULE_ENTITY] == "binary_sensor.rm_schedule"
    assert light_cfg[CONF_HOLD_ENTITIES] == ["input_boolean.guest"]
    assert molight_config(combined)[CONF_TRIGGER_SENSORS] == ["binary_sensor.other"]

    await hass.config_entries.async_remove(schedule.entry_id)
    await settle(hass)
    assert CONF_SCHEDULE_ENTITY not in molight_config(light)


@pytest.mark.asyncio
async def test_remove_entry_strips_scheduled_light_references(
    hass: HomeAssistant,
) -> None:
    """Shared and per-side references are cleaned from a scheduled light."""
    occupancy = _occupancy_entry()
    illuminance = _illuminance_entry()
    schedule = _schedule_entry()
    inner = make_light_entry(name="Rm Inner", lights=["light.inner_real"])
    light = make_scheduled_light_entry(
        name="Rm Scheduled Light",
        lights=["light.rm_inner", "light.real_1"],
        schedule="binary_sensor.rm_schedule",
        outside={
            CONF_OCCUPANCY_ENTITY: "binary_sensor.rm_occupancy",
            CONF_ILLUMINANCE_ENTITY: "binary_sensor.rm_illuminance",
            CONF_HOLD_ENTITIES: ["binary_sensor.rm_occupancy", "input_boolean.guest"],
        },
        inside={
            CONF_DOOR_ENTITY: "binary_sensor.rm_occupancy",
            CONF_MAINTAIN_OCCUPANCY_ENTITY: "binary_sensor.rm_occupancy",
            CONF_ILLUMINANCE_ENTITY: "binary_sensor.rm_illuminance",
        },
    )
    for entry in (occupancy, illuminance, schedule, inner):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    # Pin the selected side before loading the light; deleting the schedule
    # must visibly move an inside-schedule light to its outside settings.
    hass.states.async_set("binary_sensor.rm_schedule", "on")
    light.add_to_hass(hass)
    assert await hass.config_entries.async_setup(light.entry_id)
    await settle(hass)
    assert (
        hass.states.get("light.rm_scheduled_light").attributes[ATTR_ACTIVE_SETTINGS]
        == ACTIVE_SETTINGS_INSIDE
    )

    await hass.config_entries.async_remove(occupancy.entry_id)
    await settle(hass)

    cfg = molight_config(light)
    assert CONF_OCCUPANCY_ENTITY not in cfg[CONF_OUTSIDE_SCHEDULE_SETTINGS]
    assert CONF_DOOR_ENTITY not in cfg[CONF_INSIDE_SCHEDULE_SETTINGS]
    assert CONF_MAINTAIN_OCCUPANCY_ENTITY not in cfg[CONF_INSIDE_SCHEDULE_SETTINGS]
    assert cfg[CONF_OUTSIDE_SCHEDULE_SETTINGS][CONF_HOLD_ENTITIES] == [
        "input_boolean.guest"
    ]

    await hass.config_entries.async_remove(illuminance.entry_id)
    await settle(hass)
    cfg = molight_config(light)
    assert CONF_ILLUMINANCE_ENTITY not in cfg[CONF_OUTSIDE_SCHEDULE_SETTINGS]
    assert CONF_ILLUMINANCE_ENTITY not in cfg[CONF_INSIDE_SCHEDULE_SETTINGS]

    await hass.config_entries.async_remove(inner.entry_id)
    await settle(hass)
    assert molight_config(light)[CONF_LIGHTS] == ["light.real_1"]

    await hass.config_entries.async_remove(schedule.entry_id)
    await settle(hass)
    assert CONF_SCHEDULE_ENTITY not in molight_config(light)
    state = hass.states.get("light.rm_scheduled_light")
    assert state is not None
    assert state.attributes[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE

    # The missing schedule is a configuration problem, not a reason to make
    # the surviving light unusable by dashboards or voice control.
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.rm_scheduled_light"}
    )
    await settle(hass)
    assert hass.states.get("light.rm_scheduled_light").state == "on"


@pytest.mark.asyncio
async def test_remove_wrapped_virtual_light_strips_member_reference(
    hass: HomeAssistant,
) -> None:
    """A virtual light wrapping another virtual light loses the member when
    the inner entry is removed.

    Regression: CONF_LIGHTS was missing from the reference cleanup, and
    _all_lights_off treats an unresolvable member as "maybe still on" — the
    dangling id would have pinned the outer light on forever.
    """
    inner = make_light_entry(name="Rm Inner", lights=["light.inner_real"])
    outer = make_light_entry(
        name="Rm Outer", lights=["light.rm_inner", "light.outer_real"]
    )
    for entry in (inner, outer):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    await hass.config_entries.async_remove(inner.entry_id)
    await settle(hass)

    assert molight_config(outer)[CONF_LIGHTS] == ["light.outer_real"]


@pytest.mark.asyncio
async def test_remove_entry_without_references_leaves_others_alone(
    hass: HomeAssistant,
) -> None:
    """Removing an unreferenced entry must not rewrite unrelated entries."""
    occupancy = _occupancy_entry()
    light = make_light_entry(name="Rm Light")  # references nothing
    for entry in (occupancy, light):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    await hass.config_entries.async_remove(occupancy.entry_id)
    await settle(hass)

    # Untouched: no options were written onto the light entry.
    assert not light.options

    # An entry that never loaded registered no entities — removing it has
    # nothing to clean up and equally touches nobody.
    never_loaded = _illuminance_entry()
    never_loaded.add_to_hass(hass)
    await hass.config_entries.async_remove(never_loaded.entry_id)
    await settle(hass)
    assert not light.options
