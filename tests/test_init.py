"""Entry lifecycle tests: setup/unload plumbing and per-type platform guards."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_SENSOR,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_ON_BUTTONS_SINGLE,
    CONF_TARGET_LIGHTS,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DATA_AUTO_OFF_ENABLED,
    DATA_PLATFORMS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_REMOTE,
    ENTITY_TYPE_SCHEDULE,
    PLATFORMS_BY_ENTITY_TYPE,
)
from tests.conftest import make_light_entry, settle, setup_entries

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Each entry type registers exactly these entity domains, which must also be
# the platforms PLATFORMS_BY_ENTITY_TYPE forwards for it.
TYPE_ENTRIES: dict[str, tuple[dict, dict[str, int]]] = {
    ENTITY_TYPE_OCCUPANCY: (
        {
            CONF_NAME: "Init Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_init",
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
        {"binary_sensor": 1},
    ),
    ENTITY_TYPE_COMBINED_OCCUPANCY: (
        {
            CONF_NAME: "Init Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.trigger_init"],
        },
        {"binary_sensor": 1},
    ),
    ENTITY_TYPE_ILLUMINANCE: (
        {
            CONF_NAME: "Init Illuminance",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux_init",
        },
        {"binary_sensor": 1},
    ),
    ENTITY_TYPE_SCHEDULE: (
        {
            CONF_NAME: "Init Schedule",
            CONF_TIME_WINDOWS: [{"start": "07:00", "end": "22:00"}],
        },
        {"binary_sensor": 1},
    ),
    ENTITY_TYPE_LIGHT: (
        {
            CONF_NAME: "Init Light",
            CONF_LIGHTS: ["light.real_init"],
            CONF_LIGHT_TIMEOUT: 60,
        },
        {"light": 1, "switch": 1},
    ),
    ENTITY_TYPE_REMOTE: (
        {
            CONF_NAME: "Init Remote",
            CONF_TARGET_LIGHTS: ["light.real_init"],
            CONF_ON_BUTTONS_SINGLE: ["event.button_init"],
        },
        {"sensor": 1},
    ),
}


@pytest.mark.parametrize("entity_type", list(TYPE_ENTRIES), ids=list(TYPE_ENTRIES))
def test_forwarded_platforms_match_registered_domains(entity_type: str) -> None:
    """Only the platforms an entry actually uses are forwarded to it."""
    assert sorted(PLATFORMS_BY_ENTITY_TYPE[entity_type]) == sorted(
        TYPE_ENTRIES[entity_type][1]
    )


def _make_entry(entity_type: str) -> MockConfigEntry:
    data, _expected = TYPE_ENTRIES[entity_type]
    return MockConfigEntry(domain=DOMAIN, data={CONF_ENTITY_TYPE: entity_type, **data})


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_type", list(TYPE_ENTRIES), ids=list(TYPE_ENTRIES))
async def test_entry_registers_exactly_its_own_entities(
    hass: HomeAssistant, entity_type: str
) -> None:
    """An entry registers its own entities and no others (no stray Auto-off
    switch on a sensor entry, no Last Action sensor on a light entry, ...)."""
    entry = _make_entry(entity_type)
    await setup_entries(hass, entry)

    registry = er.async_get(hass)
    domains = Counter(
        e.domain for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    )
    assert domains == Counter(TYPE_ENTRIES[entity_type][1])


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_type", list(TYPE_ENTRIES), ids=list(TYPE_ENTRIES))
async def test_unload_entry_succeeds_for_every_type(
    hass: HomeAssistant, entity_type: str
) -> None:
    entry = _make_entry(entity_type)
    await setup_entries(hass, entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)

    # Unloaded (not removed) entities are left as restored "unavailable"
    # placeholders — the live entity objects are gone.
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        state = hass.states.get(reg_entry.entity_id)
        assert state is not None
        assert state.state == "unavailable"


@pytest.mark.asyncio
async def test_light_entry_seeds_and_clears_hass_data(hass: HomeAssistant) -> None:
    """hass.data is seeded (auto-off enabled) at setup and dropped at unload."""
    entry = make_light_entry(name="Data Light")
    await setup_entries(hass, entry)
    assert hass.data[DOMAIN][entry.entry_id] == {
        DATA_AUTO_OFF_ENABLED: True,
        DATA_PLATFORMS: ["light", "switch"],
    }

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_auto_off_switch_state_survives_reload(hass: HomeAssistant) -> None:
    """A disabled auto-off survives an entry reload via the switch's restore."""
    entry = make_light_entry(name="Reload Light")
    hass.states.async_set("light.real_1", "off")
    await setup_entries(hass, entry)

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.reload_light_auto_off"},
        blocking=True,
    )
    await settle(hass)
    assert hass.data[DOMAIN][entry.entry_id][DATA_AUTO_OFF_ENABLED] is False

    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    assert hass.states.get("switch.reload_light_auto_off").state == "off"
    assert hass.data[DOMAIN][entry.entry_id][DATA_AUTO_OFF_ENABLED] is False
    light = hass.states.get("light.reload_light")
    assert light.attributes["auto_off_held"] is True
