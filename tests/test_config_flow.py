"""Tests for the Limer config flow."""
from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.limer.const import (
    CONF_ENTITY_TYPE,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
)
from custom_components.limer.helpers import limer_config


@pytest.mark.asyncio
async def test_config_flow_occupancy(hass: HomeAssistant) -> None:
    """Full config flow creates an occupancy sensor entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY},
    )
    assert result["step_id"] == "occupancy"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.hall_motion",
            CONF_OCCUPANCY_TIMEOUT: 60,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hall Occupancy"
    assert result["data"][CONF_ENTITY_TYPE] == ENTITY_TYPE_OCCUPANCY


@pytest.mark.asyncio
async def test_config_flow_schedule_with_sun(hass: HomeAssistant) -> None:
    """Schedule flow builds a window with sun-anchored edges."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE}
    )
    assert result["step_id"] == "schedule"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Night",
            "start_time": "21:00:00",
            "start_sun": "sunset",
            "start_offset": -15,
            "start_combine": "latest",
            "end_time": "07:00:00",
            "end_sun": "sunrise",
            "end_offset": 10,
            "end_combine": "earliest",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TIME_WINDOWS] == [
        {
            "start": {
                "time": "21:00:00",
                "sun": "sunset",
                "offset": -15,
                "combine": "latest",
            },
            "end": {
                "time": "07:00:00",
                "sun": "sunrise",
                "offset": 10,
                "combine": "earliest",
            },
        }
    ]


@pytest.mark.asyncio
async def test_config_flow_virtual_light(hass: HomeAssistant) -> None:
    """Full config flow creates a virtual light entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT},
    )
    assert result["step_id"] == "light"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Living Room",
            CONF_LIGHTS: ["light.living_room_1", "light.living_room_2"],
            CONF_LIGHT_TIMEOUT: 300,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room"


@pytest.mark.asyncio
async def test_light_flow_rejects_timeout_below_occupancy_timeout(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """light_timeout < the referenced occupancy sensor's timeout is rejected."""
    # Occupancy sensor with a 30s timeout (fixture) must be set up so its
    # entity exists in the registry.
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 20,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_LIGHT_TIMEOUT: "light_timeout_too_short"}

    # A timeout >= 30s is accepted.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_occupancy_options_reject_timeout_above_light_timeout(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Raising an occupancy timeout past a dependent light's timeout is rejected."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
        },
    )
    light.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(occupancy_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 120,  # > the light's 60s timeout
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_OCCUPANCY_TIMEOUT: "occupancy_timeout_too_long"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 45,  # fits under 60s
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_light_flow_validates_combined_timeout(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """A combined sensor's effective timeout is its largest constituent's."""
    occupancy2 = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Test Occupancy 2",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_2",
            CONF_OCCUPANCY_TIMEOUT: 45,
        },
    )
    combined = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined Occupancy",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.test_occupancy_2"],
        },
    )
    for entry in (occupancy_entry, occupancy2, combined):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )

    # 40s beats the 30s trigger but not the 45s maintain constituent.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 40,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.combined_occupancy",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_LIGHT_TIMEOUT: "light_timeout_too_short"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 45,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.combined_occupancy",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_light_options_can_clear_occupancy_reference(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Omitting an optional reference in the options flow actually removes it."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
        },
    )
    light.add_to_hass(hass)
    await hass.config_entries.async_setup(light.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(light.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            # occupancy_entity intentionally omitted — the user cleared it.
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert CONF_OCCUPANCY_ENTITY not in limer_config(light)


@pytest.mark.asyncio
async def test_config_flow_virtual_light_requires_lights(hass: HomeAssistant) -> None:
    """Virtual light config flow rejects an empty lights list."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Empty Light", CONF_LIGHTS: [], CONF_LIGHT_TIMEOUT: 300},
    )
    assert result["type"] == FlowResultType.FORM
    assert "lights" in result["errors"]
