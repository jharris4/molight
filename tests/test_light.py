"""Tests for the Limer Virtual Light."""
from __future__ import annotations

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.limer.const import DOMAIN, STATE_ACTIVE, STATE_IDLE


@pytest.mark.asyncio
async def test_virtual_light_setup(hass: HomeAssistant, light_entry: MockConfigEntry) -> None:
    """Virtual light is created and starts off."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state is not None
    assert state.state == "off"


@pytest.mark.asyncio
async def test_virtual_light_turn_on(hass: HomeAssistant, light_entry: MockConfigEntry) -> None:
    """Turning on the virtual light turns on real lights and starts the timer."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call("light", "turn_on", {"entity_id": "light.test_light"})
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state.state == "on"
    assert state.attributes.get("limer_state") == STATE_ACTIVE


@pytest.mark.asyncio
async def test_virtual_light_auto_off(hass: HomeAssistant, light_entry: MockConfigEntry) -> None:
    """Virtual light turns off automatically after the configured timeout."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call("light", "turn_on", {"entity_id": "light.test_light"})
    await hass.async_block_till_done()

    # Fast-forward past the timeout (60s in fixture)
    await asyncio.sleep(61)
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state.state == "off"
    assert state.attributes.get("limer_state") == STATE_IDLE
