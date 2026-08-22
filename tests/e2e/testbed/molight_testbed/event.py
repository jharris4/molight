"""Simulated event entities for live MoLight acceptance tests."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import controller_for_entry
from .const import EVENT_BUTTON
from .entity import TestbedEntity


class TestbedButtonEvent(TestbedEntity, EventEntity):
    """A button whose events are emitted by the testbed service."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types: ClassVar[list[str]] = ["short_release", "multi_press_2"]

    def fire_test_event(self, event_type: str, attributes: dict[str, Any]) -> None:
        """Publish one event as a normal EventEntity state change."""
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a deterministic remote button fixture."""
    controller = controller_for_entry(hass, entry)
    async_add_entities(
        [
            TestbedButtonEvent(
                controller,
                EVENT_BUTTON,
                "event.e2e_button",
                "E2E Button",
            )
        ]
    )
