"""Tests for Limer binary sensor entities."""
from __future__ import annotations

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.limer.const import DOMAIN


@pytest.mark.asyncio
async def test_occupancy_sensor_setup(hass: HomeAssistant, occupancy_entry: MockConfigEntry) -> None:
    """Occupancy sensor is created and starts off."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state is not None
    assert state.state == "off"


@pytest.mark.asyncio
async def test_occupancy_trigger_turns_on(hass: HomeAssistant, occupancy_entry: MockConfigEntry) -> None:
    """Occupancy turns on when a trigger sensor fires."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "on"


@pytest.mark.asyncio
async def test_occupancy_clears_after_timeout(hass: HomeAssistant, occupancy_entry: MockConfigEntry) -> None:
    """Occupancy clears after the configured timeout once all sensors go off."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    # Trigger occupancy
    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_occupancy").state == "on"

    # Sensor goes off — countdown starts (timeout = 30s in fixture)
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    # Still on during the countdown
    assert hass.states.get("binary_sensor.test_occupancy").state == "on"

    # Fast-forward past the timeout
    await asyncio.sleep(31)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_occupancy").state == "off"


@pytest.mark.asyncio
async def test_illuminance_sensor_below_threshold(hass: HomeAssistant, illuminance_entry: MockConfigEntry) -> None:
    """Illuminance sensor is ON when lux is below the threshold."""
    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.lux_1", "5")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_illuminance")
    assert state.state == "on"


@pytest.mark.asyncio
async def test_illuminance_sensor_above_threshold(hass: HomeAssistant, illuminance_entry: MockConfigEntry) -> None:
    """Illuminance sensor is OFF when lux is above the threshold."""
    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.lux_1", "500")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_illuminance")
    assert state.state == "off"
