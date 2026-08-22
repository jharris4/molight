"""Simulated sensors for live MoLight acceptance tests."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import LIGHT_LUX
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import controller_for_entry
from .const import ILLUMINANCE
from .entity import TestbedEntity


class TestbedIlluminanceSensor(TestbedEntity, SensorEntity):
    """A persistent simulated illuminance sensor."""

    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_native_unit_of_measurement = LIGHT_LUX

    @property
    def native_value(self) -> float:
        """Return the persisted lux reading."""
        return float(self.record["state"])

    def set_test_state(
        self, state: str | float | bool, attributes: dict[str, Any]
    ) -> None:
        """Set a numeric lux reading through the test service."""
        self.record["state"] = float(state)
        self.record["attributes"].update(attributes)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the illuminance fixture."""
    controller = controller_for_entry(hass, entry)
    async_add_entities(
        [
            TestbedIlluminanceSensor(
                controller,
                ILLUMINANCE,
                "sensor.e2e_illuminance",
                "E2E Illuminance",
            )
        ]
    )
