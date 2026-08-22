"""Simulated selects for live MoLight acceptance tests."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import controller_for_entry
from .const import SOURCE_SELECT, TARGET_SELECT
from .entity import TestbedEntity

OPTIONS = ["Cozy", "Focus", "Night"]


class TestbedSelect(TestbedEntity, SelectEntity):
    """A persistent simulated select."""

    _attr_options = OPTIONS

    @property
    def current_option(self) -> str:
        """Return the persisted selection."""
        return str(self.record["state"])

    async def async_select_option(self, option: str) -> None:
        """Apply and persist a standard select command."""
        if option not in self.options:
            raise ValueError(f"Unsupported option: {option}")
        self.record["state"] = option
        self.record["last_command"] = {"service": "select_option", "option": option}
        await self.controller.async_save()
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the last option command for routing assertions."""
        return {"testbed_last_command": self.record.get("last_command")}

    def set_test_state(
        self, state: str | float | bool, attributes: dict[str, Any]
    ) -> None:
        """Set even an invalid option so fallback behavior can be exercised."""
        self.record["state"] = str(state)
        self.record["attributes"].update(attributes)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add source and target select fixtures."""
    controller = controller_for_entry(hass, entry)
    async_add_entities(
        [
            TestbedSelect(
                controller,
                TARGET_SELECT,
                "select.e2e_target_mode",
                "E2E Target Mode",
            ),
            TestbedSelect(
                controller,
                SOURCE_SELECT,
                "select.e2e_source_mode",
                "E2E Source Mode",
            ),
        ]
    )
