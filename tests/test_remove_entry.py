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
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_SCHEDULE_ENTITY,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
    SCHEDULE_MODE_GATE,
)
from custom_components.molight.helpers import molight_config
from tests.conftest import make_light_entry, settle

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
    ],
    ids=["occupancy", "combined_occupancy", "illuminance", "schedule", "light"],
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
