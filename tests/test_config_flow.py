"""Tests for the Limer config flow."""
from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.limer.const import (
    CONF_ENTITY_TYPE,
    CONF_LIGHTS,
    CONF_LIGHT_TIMEOUT,
    CONF_NAME,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
)


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
            CONF_TRIGGER_SENSORS: ["binary_sensor.hall_motion"],
            CONF_OCCUPANCY_TIMEOUT: 60,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hall Occupancy"
    assert result["data"][CONF_ENTITY_TYPE] == ENTITY_TYPE_OCCUPANCY


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
