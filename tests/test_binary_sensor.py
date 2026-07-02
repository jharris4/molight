"""Tests for Limer binary sensor entities."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant


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
async def test_occupancy_mirrors_source_on(hass: HomeAssistant, occupancy_entry: MockConfigEntry) -> None:
    """Virtual occupancy turns on when the real sensor turns on."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "on"


@pytest.mark.asyncio
async def test_occupancy_clear_records_latest_occupied_time(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Clearing mirrors the source immediately and back-dates latest_occupied_time.

    latest_occupied_time = clear time - occupancy_timeout (30s in fixture),
    estimating when the person actually left before the real sensor's own
    hardware delay elapsed.
    """
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_occupancy").state == "on"

    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "off"

    lot = datetime.fromisoformat(state.attributes["latest_occupied_time"])
    expected = datetime.now(timezone.utc) - timedelta(seconds=30)
    assert abs((lot - expected).total_seconds()) < 2
    assert state.attributes["occupancy_timeout"] == 30


@pytest.mark.asyncio
async def test_occupancy_ignores_unavailable_blip(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """A source sensor going unavailable must not read as 'occupancy cleared'."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "on"
    assert state.attributes["latest_occupied_time"] is None


@pytest.mark.asyncio
async def test_occupancy_ignores_attribute_only_updates(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """Attribute-only source updates must not advance latest_occupied_time."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    lot_before = hass.states.get("binary_sensor.test_occupancy").attributes[
        "latest_occupied_time"
    ]
    assert lot_before is not None

    # Real sensors push battery/lux attribute updates while their state is off.
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_1", "off", {"battery": 50})
    await hass.async_block_till_done()

    lot_after = hass.states.get("binary_sensor.test_occupancy").attributes[
        "latest_occupied_time"
    ]
    assert lot_after == lot_before


@pytest.mark.asyncio
async def test_illuminance_holds_value_when_source_unavailable(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """An unavailable lux sensor must not read as 'dark'."""
    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.lux_1", "500")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == "on"

    hass.states.async_set("sensor.lux_1", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == "on"


@pytest.mark.asyncio
async def test_illuminance_sensor_dark(hass: HomeAssistant, illuminance_entry: MockConfigEntry) -> None:
    """Illuminance sensor is OFF (no light detected) when lux is below the threshold."""
    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.lux_1", "5")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_illuminance")
    assert state.state == "off"


@pytest.mark.asyncio
async def test_illuminance_sensor_bright(hass: HomeAssistant, illuminance_entry: MockConfigEntry) -> None:
    """Illuminance sensor is ON (light detected) when lux meets the threshold."""
    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.lux_1", "500")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_illuminance")
    assert state.state == "on"
