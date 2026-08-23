"""Simulated event entities for live MoLight acceptance tests."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import controller_for_entry
from .const import EVENT_BUTTON, EVENT_CASETA, EVENT_HUE, EVENT_MATTER, EVENT_Z2M
from .entity import TestbedEntity


class TestbedButtonEvent(TestbedEntity, EventEntity):
    """A button whose events are emitted by the testbed service."""

    _attr_device_class = EventDeviceClass.BUTTON

    def __init__(self, *args: Any, event_types: list[str]) -> None:
        """Initialize a button advertising one ecosystem's click vocabulary."""
        super().__init__(*args)
        self._attr_event_types = list(event_types)

    def fire_test_event(self, event_type: str, attributes: dict[str, Any]) -> None:
        """Publish one event as a normal EventEntity state change."""
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add remote buttons, one per click vocabulary MoLight resolves."""
    controller = controller_for_entry(hass, entry)
    async_add_entities(
        [
            TestbedButtonEvent(
                controller,
                EVENT_BUTTON,
                "event.e2e_button",
                "E2E Button",
                event_types=["short_release", "multi_press_2"],
            ),
            # Matter multi-press: single = multi_press_1, double = multi_press_2;
            # the constituent initial_press/short_release must not fire bindings.
            TestbedButtonEvent(
                controller,
                EVENT_MATTER,
                "event.e2e_button_matter",
                "E2E Matter Button",
                event_types=[
                    "initial_press",
                    "short_release",
                    "multi_press_1",
                    "multi_press_2",
                ],
            ),
            TestbedButtonEvent(
                controller,
                EVENT_Z2M,
                "event.e2e_button_z2m",
                "E2E Z2M Button",
                event_types=["single", "double"],
            ),
            TestbedButtonEvent(
                controller,
                EVENT_CASETA,
                "event.e2e_button_caseta",
                "E2E Caseta Button",
                event_types=["press", "multi_tap"],
            ),
            # Hue-style button announcing only initial_press: it is the single
            # click fallback and reports no double click at all.
            TestbedButtonEvent(
                controller,
                EVENT_HUE,
                "event.e2e_button_hue",
                "E2E Hue Button",
                event_types=["initial_press"],
            ),
        ]
    )
