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
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
)
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
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    return [
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
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
        + "; ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    )
    assert entry.entry_id not in {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
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
