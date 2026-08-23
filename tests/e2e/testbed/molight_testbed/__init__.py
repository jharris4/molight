"""Persistent simulated entities for live MoLight acceptance tests."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store

from .const import (
    ATTR_AVAILABLE,
    ATTR_BEHAVIOR,
    ATTR_EVENT_TYPE,
    DATA_CONTROLLER,
    DEFAULT_STATES,
    DOMAIN,
    PLATFORMS,
    SERVICE_FIRE_EVENT,
    SERVICE_SET_AVAILABLE,
    SERVICE_SET_BEHAVIOR,
    SERVICE_SET_STATE,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

SET_STATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required("state"): vol.Any(str, int, float, bool),
        vol.Optional("attributes", default={}): dict,
    }
)
SET_AVAILABLE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_AVAILABLE): cv.boolean,
    }
)
SET_BEHAVIOR_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_BEHAVIOR): dict,
    }
)
FIRE_EVENT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_EVENT_TYPE): str,
        vol.Optional("attributes", default={}): dict,
    }
)


class TestbedController:
    """Own simulated state, persistence, and live entity registrations."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the controller."""
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.states: dict[str, dict[str, Any]] = deepcopy(DEFAULT_STATES)
        self.entities: dict[str, Any] = {}

    async def async_load(self) -> None:
        """Restore simulated device state from Home Assistant storage."""
        stored = await self.store.async_load()
        if not stored:
            return
        for key, value in stored.get("states", {}).items():
            if key in self.states and isinstance(value, dict):
                self.states[key].update(value)

    def register(self, entity: Any) -> None:
        """Make an added entity addressable by the test control services."""
        self.entities[entity.entity_id] = entity

    def unregister(self, entity_id: str) -> None:
        """Remove an unloaded entity registration."""
        self.entities.pop(entity_id, None)

    async def async_save(self) -> None:
        """Persist every simulated state before returning to the test."""
        await self.store.async_save({"states": self.states})

    async def async_set_state(
        self, entity_id: str, state: str | float | bool, attributes: dict
    ) -> None:
        """Set one entity's state through its platform-specific coercion."""
        entity = self.entities.get(entity_id)
        if entity is None:
            raise ValueError(f"Unknown testbed entity: {entity_id}")
        entity.set_test_state(state, attributes)
        await self.async_save()
        entity.async_write_ha_state()

    async def async_set_available(self, entity_id: str, available: bool) -> None:
        """Make one entity available or unavailable and persist the choice."""
        entity = self.entities.get(entity_id)
        if entity is None:
            raise ValueError(f"Unknown testbed entity: {entity_id}")
        entity.set_test_available(available)
        await self.async_save()
        entity.async_write_ha_state()

    async def async_set_behavior(
        self, entity_id: str, behavior: dict[str, Any]
    ) -> None:
        """Change how one simulated light reports back and persist it."""
        entity = self.entities.get(entity_id)
        if entity is None or not hasattr(entity, "set_test_behavior"):
            raise ValueError(f"Unknown testbed light: {entity_id}")
        entity.set_test_behavior(behavior)
        await self.async_save()
        entity.async_write_ha_state()

    def fire_event(
        self, entity_id: str, event_type: str, attributes: dict[str, Any]
    ) -> None:
        """Fire a fresh event from a simulated event entity."""
        entity = self.entities.get(entity_id)
        if entity is None or not hasattr(entity, "fire_test_event"):
            raise ValueError(f"Unknown testbed event entity: {entity_id}")
        entity.fire_test_event(event_type, attributes)


async def async_setup(hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Import the singleton config entry declared by configuration.yaml."""
    if not hass.config_entries.async_entries(DOMAIN):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_IMPORT},
                data={},
            ),
            "import_molight_testbed",
        )
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Set up the testbed and its entity platforms."""
    controller = TestbedController(hass)
    await controller.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_CONTROLLER: controller}

    async def _set_state(call: ServiceCall) -> None:
        await controller.async_set_state(
            call.data[ATTR_ENTITY_ID],
            call.data["state"],
            call.data["attributes"],
        )

    async def _set_available(call: ServiceCall) -> None:
        await controller.async_set_available(
            call.data[ATTR_ENTITY_ID], call.data[ATTR_AVAILABLE]
        )

    async def _set_behavior(call: ServiceCall) -> None:
        await controller.async_set_behavior(
            call.data[ATTR_ENTITY_ID], call.data[ATTR_BEHAVIOR]
        )

    @callback
    def _fire_event(call: ServiceCall) -> None:
        controller.fire_event(
            call.data[ATTR_ENTITY_ID],
            call.data[ATTR_EVENT_TYPE],
            call.data["attributes"],
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_STATE, _set_state, schema=SET_STATE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_AVAILABLE,
        _set_available,
        schema=SET_AVAILABLE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_BEHAVIOR, _set_behavior, schema=SET_BEHAVIOR_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FIRE_EVENT, _fire_event, schema=FIRE_EVENT_SCHEMA
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload all testbed platforms and services."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.services.async_remove(DOMAIN, SERVICE_SET_STATE)
    hass.services.async_remove(DOMAIN, SERVICE_SET_AVAILABLE)
    hass.services.async_remove(DOMAIN, SERVICE_SET_BEHAVIOR)
    hass.services.async_remove(DOMAIN, SERVICE_FIRE_EVENT)
    hass.data[DOMAIN].pop(entry.entry_id)
    return True


def controller_for_entry(
    hass: HomeAssistant, entry: config_entries.ConfigEntry
) -> TestbedController:
    """Return the controller owned by a platform's config entry."""
    return hass.data[DOMAIN][entry.entry_id][DATA_CONTROLLER]
