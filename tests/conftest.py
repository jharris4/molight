"""Shared pytest fixtures for Limer tests."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.limer.const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_ILLUMINANCE_SENSOR,
    CONF_LIGHTS,
    CONF_LIGHT_TIMEOUT,
    CONF_NAME,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom integrations in tests."""
    yield


@pytest.fixture
def occupancy_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Test Occupancy",
            CONF_TRIGGER_SENSORS: ["binary_sensor.motion_1"],
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
    )


@pytest.fixture
def illuminance_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
            CONF_NAME: "Test Illuminance",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux_1",
            CONF_ILLUMINANCE_THRESHOLD: 10.0,
        },
    )


@pytest.fixture
def schedule_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Test Schedule",
            "time_windows": [{"start": "07:00", "end": "22:00"}],
        },
    )


@pytest.fixture
def light_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Test Light",
            CONF_LIGHTS: ["light.living_room"],
            CONF_LIGHT_TIMEOUT: 60,
        },
    )
