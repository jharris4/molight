"""Companion Events entity for MoLight Virtual Lights and occupancy sensors.

Every Virtual Light or Virtual Scheduled Light entry gets one "<name> Events"
entity, as does every occupancy sensor entry. The light or sensor announces
what it did on a per-entry dispatcher signal, as the Auto-off switch does,
and this entity records it as its last event: the state is the time,
event_type says what happened and the attributes say why. An automation
triggers on it with Home Assistant's "Event received" trigger.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.event import ENTITY_ID_FORMAT, EventEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    CONF_ENTITY_TYPE,
    CONF_NAME,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULED_LIGHT,
    LIGHT_EVENT_TYPES,
    OCCUPANCY_EVENT_TYPES,
    SIGNAL_EVENT,
)
from .helpers import RenamableRestoreEntity, molight_config, suggested_entity_id

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_EVENT_TYPES = {
    ENTITY_TYPE_LIGHT: LIGHT_EVENT_TYPES,
    ENTITY_TYPE_SCHEDULED_LIGHT: LIGHT_EVENT_TYPES,
    ENTITY_TYPE_OCCUPANCY: OCCUPANCY_EVENT_TYPES,
    ENTITY_TYPE_COMBINED_OCCUPANCY: OCCUPANCY_EVENT_TYPES,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Events entity for a light or occupancy sensor entry."""
    event_types = _EVENT_TYPES.get(entry.data[CONF_ENTITY_TYPE])
    if event_types is None:
        return
    entity = MoLightEvents(entry, event_types)
    # Parallel the owner's explicit id: light.kitchen -> event.kitchen_events.
    if entity_id := suggested_entity_id(
        hass, entry, ENTITY_ID_FORMAT, suffix="_events"
    ):
        entity.entity_id = entity_id
    async_add_entities([entity])


class MoLightEvents(EventEntity, RenamableRestoreEntity):
    """Records the last thing a Virtual Light or occupancy sensor did."""

    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, event_types: list[str]) -> None:
        """Initialize the Events entity."""
        self._entry_id = entry.entry_id
        cfg = molight_config(entry)
        self._attr_name = f"{cfg[CONF_NAME]} Events"
        self._attr_unique_id = f"{entry.entry_id}_events"
        self._attr_event_types = list(event_types)

    async def async_added_to_hass(self) -> None:
        """Listen for the owner's announcements."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_EVENT.format(self._entry_id), self._on_event
            )
        )

    @callback
    def _on_event(self, event_type: str, attributes: dict[str, Any]) -> None:
        """Record an announcement as the last event."""
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()
