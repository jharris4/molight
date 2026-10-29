"""Diagnostics for a MoLight config entry.

The download holds the entry's data and options, the state of every entity it created
and of every entity it references, and which other MoLight entries use it.
Home Assistant adds the integration's Repairs issues itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er

from . import referenced_entity_ids
from .const import CONF_ENTITY_TYPE, DOMAIN
from .helpers import entry_settings

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    registry = er.async_get(hass)
    cfg = entry_settings(entry)
    own = er.async_entries_for_config_entry(registry, entry.entry_id)
    own_ids = {reg_entry.entity_id for reg_entry in own}
    others = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ]
    owners = {
        reg_entry.entity_id: other
        for other in others
        for reg_entry in er.async_entries_for_config_entry(registry, other.entry_id)
    }
    return {
        "entry": {
            **_summary(entry),
            "state": entry.state.value,
            "disabled_by": entry.disabled_by,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "entities": [
            {
                "entity_id": reg_entry.entity_id,
                "unique_id": reg_entry.unique_id,
                "disabled_by": reg_entry.disabled_by,
                **_state_of(hass, reg_entry.entity_id),
            }
            for reg_entry in own
        ],
        "references": {
            entity_id: {
                **_state_of(hass, entity_id),
                "molight_entry": (
                    _summary(owners[entity_id]) if entity_id in owners else None
                ),
            }
            for entity_id in sorted(set(referenced_entity_ids(cfg)))
        },
        "referenced_by": [
            _summary(other)
            for other in others
            if own_ids & set(referenced_entity_ids(entry_settings(other)))
        ],
        "runtime": dict(hass.data.get(DOMAIN, {}).get(entry.entry_id, {})),
    }


def _summary(entry: ConfigEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "entity_type": entry.data.get(CONF_ENTITY_TYPE),
    }


def _state_of(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    state = hass.states.get(entity_id)
    if state is None:
        return {"state": None}
    return {
        "state": state.state,
        "attributes": dict(state.attributes),
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
    }
