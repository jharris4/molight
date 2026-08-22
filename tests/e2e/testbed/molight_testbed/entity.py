"""Shared entity implementation for the MoLight testbed."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import Entity

from . import TestbedController
from .const import DOMAIN


class TestbedEntity(Entity):
    """Base for a stateful entity controlled by the acceptance runner."""

    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(
        self,
        controller: TestbedController,
        testbed_key: str,
        entity_id: str,
        name: str,
    ) -> None:
        """Initialize a deterministic testbed entity."""
        self.controller = controller
        self.testbed_key = testbed_key
        self.entity_id = entity_id
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{testbed_key}"

    @property
    def record(self) -> dict[str, Any]:
        """Return this entity's mutable persistent record."""
        return self.controller.states[self.testbed_key]

    @property
    def available(self) -> bool:
        """Return the simulated availability."""
        return bool(self.record["available"])

    async def async_added_to_hass(self) -> None:
        """Register the final entity id with the controller."""
        await super().async_added_to_hass()
        self.controller.register(self)

    async def async_will_remove_from_hass(self) -> None:
        """Remove the controller registration during unload."""
        self.controller.unregister(self.entity_id)
        await super().async_will_remove_from_hass()

    def set_test_state(
        self, state: str | float | bool, attributes: dict[str, Any]
    ) -> None:
        """Store a generic state supplied by the test control service."""
        self.record["state"] = state
        self.record["attributes"].update(attributes)
