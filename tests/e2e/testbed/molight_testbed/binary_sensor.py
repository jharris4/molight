"""Simulated binary sensors for live MoLight acceptance tests."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import controller_for_entry
from .const import DOOR, MOTION, MOTION_REMOVAL, OCCUPANCY, SCHEDULE
from .entity import TestbedEntity


class TestbedBinarySensor(TestbedEntity, BinarySensorEntity):
    """A persistent simulated binary sensor."""

    def __init__(
        self, *args: Any, device_class: BinarySensorDeviceClass | None = None
    ) -> None:
        """Initialize a binary sensor with a fixed device class."""
        super().__init__(*args)
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool:
        """Return the persisted binary state."""
        return self.record["state"] == "on"

    def set_test_state(
        self, state: str | float | bool, attributes: dict[str, Any]
    ) -> None:
        """Set an on/off state through the test service."""
        normalized = str(state).lower()
        if normalized not in ("on", "off"):
            raise ValueError(f"Binary sensor state must be on or off, got {state!r}")
        self.record["state"] = normalized
        self.record["attributes"].update(attributes)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add motion, occupancy, door, and schedule fixtures."""
    controller = controller_for_entry(hass, entry)
    async_add_entities(
        [
            TestbedBinarySensor(
                controller,
                MOTION,
                "binary_sensor.e2e_motion",
                "E2E Motion",
                device_class=BinarySensorDeviceClass.MOTION,
            ),
            TestbedBinarySensor(
                controller,
                MOTION_REMOVAL,
                "binary_sensor.e2e_removal_motion",
                "E2E Removal Motion",
                device_class=BinarySensorDeviceClass.MOTION,
            ),
            TestbedBinarySensor(
                controller,
                OCCUPANCY,
                "binary_sensor.e2e_raw_occupancy",
                "E2E Raw Occupancy",
                device_class=BinarySensorDeviceClass.OCCUPANCY,
            ),
            TestbedBinarySensor(
                controller,
                DOOR,
                "binary_sensor.e2e_door",
                "E2E Door",
                device_class=BinarySensorDeviceClass.DOOR,
            ),
            TestbedBinarySensor(
                controller,
                SCHEDULE,
                "binary_sensor.e2e_schedule_source",
                "E2E Schedule Source",
            ),
        ]
    )
