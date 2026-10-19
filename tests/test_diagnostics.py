"""The diagnostics download of a MoLight entry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    DATA_AUTO_OFF_ENABLED,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
)
from tests.conftest import make_light_entry, settle, setup_entries

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

REAL = "light.real_1"
PIR = "binary_sensor.hall_pir"
MOTION = "binary_sensor.hall_motion"
GUEST = "input_boolean.guest_mode"


def _occupancy() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hall Motion",
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Hall Motion",
            CONF_OCCUPANCY_SENSOR: PIR,
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
    )


@pytest.mark.asyncio
# The download goes over HTTP, whose access log reads the real time zone.
@pytest.mark.real_clock
async def test_light_diagnostics_hold_its_settings_entities_and_references(
    hass: HomeAssistant, hass_client
) -> None:
    """The entry, its own entities, and what it points at, owner included."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(PIR, "off")
    occupancy = _occupancy()
    light = make_light_entry(
        name="Hall", lights=[REAL], occupancy=MOTION, hold_entities=[GUEST]
    )
    await setup_entries(hass, occupancy, light)
    await settle(hass)

    diag = await get_diagnostics_for_config_entry(hass, hass_client, light)

    assert diag["entry"]["entity_type"] == ENTITY_TYPE_LIGHT
    assert diag["entry"]["state"] == "loaded"
    assert diag["entry"]["data"][CONF_LIGHTS] == [REAL]
    entities = {e["entity_id"]: e for e in diag["entities"]}
    assert set(entities) == {"light.hall", "switch.hall_auto_off"}
    assert entities["light.hall"]["state"] == "off"
    assert entities["light.hall"]["attributes"]["molight_state"] == "idle"
    assert set(diag["references"]) == {REAL, MOTION, GUEST}
    assert diag["references"][REAL]["state"] == "off"
    assert diag["references"][REAL]["molight_entry"] is None
    assert diag["references"][MOTION]["molight_entry"] == {
        "entry_id": occupancy.entry_id,
        "title": "Hall Motion",
        "entity_type": ENTITY_TYPE_OCCUPANCY,
    }
    # A referenced entity that does not exist is listed with no state.
    assert diag["references"][GUEST] == {"state": None, "molight_entry": None}
    assert diag["referenced_by"] == []
    assert diag["runtime"][DATA_AUTO_OFF_ENABLED] is True


@pytest.mark.asyncio
# The download goes over HTTP, whose access log reads the real time zone.
@pytest.mark.real_clock
async def test_sensor_diagnostics_name_the_entries_that_use_it(
    hass: HomeAssistant, hass_client
) -> None:
    """A sensor's download says which lights read it."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(PIR, "on")
    occupancy = _occupancy()
    light = make_light_entry(name="Hall", lights=[REAL], occupancy=MOTION)
    await setup_entries(hass, occupancy, light)
    await settle(hass)

    diag = await get_diagnostics_for_config_entry(hass, hass_client, occupancy)

    assert [e["entity_id"] for e in diag["entities"]] == [MOTION]
    assert diag["entities"][0]["state"] == "on"
    assert set(diag["references"]) == {PIR}
    assert diag["references"][PIR]["molight_entry"] is None
    assert diag["referenced_by"] == [
        {
            "entry_id": light.entry_id,
            "title": light.title,
            "entity_type": ENTITY_TYPE_LIGHT,
        }
    ]
