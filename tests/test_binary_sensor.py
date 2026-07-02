"""Tests for Limer binary sensor entities."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.limer.const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_HYSTERESIS,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_OCCUPANCY,
)
from tests.conftest import settle


def _occupancy2_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Test Occupancy 2",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_2",
            CONF_OCCUPANCY_TIMEOUT: 45,
        },
    )


def _combined_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined Occupancy",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.test_occupancy_2"],
        },
    )


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
async def test_illuminance_hysteresis_holds_state_in_band(
    hass: HomeAssistant,
) -> None:
    """Readings inside the hysteresis band never flip the state."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
            CONF_NAME: "Hyst Illuminance",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux_1",
            CONF_ILLUMINANCE_THRESHOLD: 10.0,
            CONF_ILLUMINANCE_HYSTERESIS: 2.0,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sensor = lambda: hass.states.get("binary_sensor.hyst_illuminance")  # noqa: E731

    # Dark; a reading above the threshold but inside the band stays dark.
    hass.states.async_set("sensor.lux_1", "11")
    await hass.async_block_till_done()
    assert sensor().state == "off"

    # Crossing threshold + hysteresis flips to bright.
    hass.states.async_set("sensor.lux_1", "12")
    await hass.async_block_till_done()
    assert sensor().state == "on"

    # Below the threshold but inside the band stays bright.
    hass.states.async_set("sensor.lux_1", "9")
    await hass.async_block_till_done()
    assert sensor().state == "on"

    # Dropping below threshold - hysteresis flips to dark.
    hass.states.async_set("sensor.lux_1", "7.9")
    await hass.async_block_till_done()
    assert sensor().state == "off"


@pytest.mark.asyncio
async def test_combined_trigger_maintain_and_lot(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """Trigger starts, maintain sustains but never starts, lot is the max."""
    for entry in (occupancy_entry, _occupancy2_entry(), _combined_entry()):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Maintain sensor alone must not start occupancy.
    hass.states.async_set("binary_sensor.motion_2", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.combined_occupancy").state == "off"

    # Trigger sensor starts it.
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.combined_occupancy").state == "on"

    # Trigger clears — the maintain sensor keeps occupancy alive.
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await settle(hass)
    assert hass.states.get("binary_sensor.combined_occupancy").state == "on"

    # Maintain clears — occupancy ends; latest_occupied_time is the max of
    # the constituents': trigger cleared at T+10 with timeout 30 (→ T−20),
    # maintain at T+20 with timeout 45 (→ T−25). The trigger's wins.
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_2", "off")
    await settle(hass)

    combined = hass.states.get("binary_sensor.combined_occupancy")
    assert combined.state == "off"
    trigger_lot = hass.states.get("binary_sensor.test_occupancy").attributes[
        "latest_occupied_time"
    ]
    assert combined.attributes["latest_occupied_time"] == trigger_lot


@pytest.mark.asyncio
async def test_occupancy_restores_latest_occupied_time(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """latest_occupied_time survives a restart via RestoreEntity."""
    lot = "2026-07-01T10:00:00+00:00"
    mock_restore_cache(
        hass,
        [
            State(
                "binary_sensor.test_occupancy",
                "off",
                {"latest_occupied_time": lot, "occupancy_timeout": 30},
            )
        ],
    )

    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.attributes["latest_occupied_time"] == lot


@pytest.mark.asyncio
async def test_illuminance_restores_state(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """A restored 'bright' reading survives a restart with the source missing."""
    mock_restore_cache(hass, [State("binary_sensor.test_illuminance", "on")])

    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    # sensor.lux_1 does not exist yet — the restored value must hold.
    assert hass.states.get("binary_sensor.test_illuminance").state == "on"


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
