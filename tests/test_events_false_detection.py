"""Tests for the false_detection event on an occupancy sensor's Events entity.

The sensor whose judgment it is fires it, with how long the cycle lasted;
a genuine visit fires nothing.
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_OCCUPANCY,
    EVENT_FALSE_DETECTION,
)
from tests.conftest import settle, setup_entries
from tests.events import record_events, tick

PIR = "binary_sensor.pir"


def _sensor() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Motion",
            CONF_OCCUPANCY_SENSOR: PIR,
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )


def _combined() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Room",
            CONF_TRIGGER_SENSORS: ["binary_sensor.motion"],
        },
    )


async def _cycle(hass, freezer, seconds: int) -> None:
    hass.states.async_set(PIR, "on")
    await settle(hass)
    await tick(hass, freezer, seconds)
    hass.states.async_set(PIR, "off")
    await settle(hass)


@pytest.mark.asyncio
async def test_a_blip_fires_with_its_duration(hass, freezer) -> None:
    hass.states.async_set(PIR, "off")
    await setup_entries(hass, _sensor())
    records = record_events(hass, "event.motion_events")

    await _cycle(hass, freezer, 31)

    assert records == [
        {
            "event_type": EVENT_FALSE_DETECTION,
            "entity_id": "binary_sensor.motion",
            "on_duration": 31.0,
        }
    ]


@pytest.mark.asyncio
async def test_a_genuine_visit_fires_nothing(hass, freezer) -> None:
    hass.states.async_set(PIR, "off")
    await setup_entries(hass, _sensor())
    records = record_events(hass, "event.motion_events")

    await _cycle(hass, freezer, 45)

    assert records == []


@pytest.mark.asyncio
async def test_a_combined_sensor_fires_its_own(hass, freezer) -> None:
    hass.states.async_set(PIR, "off")
    await setup_entries(hass, _sensor(), _combined())
    await settle(hass)
    records = record_events(hass, "event.room_events")

    await _cycle(hass, freezer, 31)

    assert records == [
        {
            "event_type": EVENT_FALSE_DETECTION,
            "entity_id": "binary_sensor.room",
            "on_duration": 31.0,
        }
    ]
