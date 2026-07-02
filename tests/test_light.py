"""Tests for the Limer Virtual Light."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.core import HomeAssistant

from custom_components.limer.const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_ENTITY,
    CONF_LIGHTS,
    CONF_LIGHT_TIMEOUT,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
)


def _gated_light_entry() -> MockConfigEntry:
    """A virtual light gated by the fixture occupancy and illuminance sensors."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Gated Light",
            CONF_LIGHTS: ["light.living_room"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
            CONF_ILLUMINANCE_ENTITY: "binary_sensor.test_illuminance",
        },
    )


async def _setup_entries(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    for entry in entries:
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _settle(hass: HomeAssistant) -> None:
    """Flush chained state-change dispatches.

    async_track_state_change_event defers each dispatch by one event-loop
    iteration (loop.call_soon), so a motion → virtual occupancy → virtual
    light chain needs several iterations before the light has reacted.
    """
    for _ in range(4):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


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
async def test_virtual_light_auto_off(
    hass: HomeAssistant, light_entry: MockConfigEntry, freezer
) -> None:
    """Virtual light turns off automatically after the configured timeout."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call("light", "turn_on", {"entity_id": "light.test_light"})
    await hass.async_block_till_done()
    assert hass.states.get("light.test_light").state == "on"

    # Fast-forward past the timeout (60s in fixture)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state.state == "off"
    assert state.attributes.get("limer_state") == STATE_IDLE


@pytest.mark.asyncio
async def test_occupancy_suppressed_when_bright(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, illuminance_entry: MockConfigEntry
) -> None:
    """Occupancy must not turn the light on while the illuminance sensor reads bright."""
    await _setup_entries(hass, occupancy_entry, illuminance_entry, _gated_light_entry())

    hass.states.async_set("sensor.lux_1", "500")  # bright (threshold 10 in fixture)
    await _settle(hass)

    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE

    # It gets dark while occupancy is still active → light comes on.
    hass.states.async_set("sensor.lux_1", "5")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "on"
    assert state.attributes["limer_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_occupancy_turns_light_on_when_dark(
    hass: HomeAssistant,
    occupancy_entry: MockConfigEntry,
    illuminance_entry: MockConfigEntry,
    freezer,
) -> None:
    """When dark, occupancy turns the light on; clearing starts the precise countdown."""
    await _setup_entries(hass, occupancy_entry, illuminance_entry, _gated_light_entry())

    hass.states.async_set("sensor.lux_1", "5")  # dark
    await _settle(hass)

    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "on"
    assert state.attributes["limer_state"] == STATE_OCCUPIED

    hass.states.async_set("binary_sensor.motion_1", "off")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "on"
    assert state.attributes["limer_state"] == STATE_COUNTDOWN

    # Countdown anchors to latest_occupied_time: light_timeout (60s) minus the
    # occupancy timeout (30s) ≈ 30s after the sensor cleared.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_bright_turns_light_off(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, illuminance_entry: MockConfigEntry
) -> None:
    """Illuminance switching to bright turns an occupancy-lit light off."""
    await _setup_entries(hass, occupancy_entry, illuminance_entry, _gated_light_entry())

    hass.states.async_set("sensor.lux_1", "5")
    await _settle(hass)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)
    assert hass.states.get("light.gated_light").state == "on"

    hass.states.async_set("sensor.lux_1", "500")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE
