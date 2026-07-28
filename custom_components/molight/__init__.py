"""The MoLight integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_SENSORS,
    CONF_OCCUPANCY_ENTITY,
    CONF_SCHEDULE_ENTITY,
    CONF_TARGET_LIGHTS,
    CONF_TRIGGER_SENSORS,
    DATA_AUTO_OFF_ENABLED,
    DOMAIN,
    ENTITY_TYPE_REMOTE,
    PLATFORMS,
)
from .helpers import molight_config
from .remote import async_setup_remote

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# Config keys under which one MoLight entry may reference another entry's
# entities. Cleaned up when the referenced entry is removed — a dangling
# reference is worse than a missing one (a light whose gate-mode schedule
# entity no longer exists would read the gate as permanently closed and
# silently stop automating).
_REFERENCE_KEYS = (
    CONF_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_ILLUMINANCE_ENTITY,
    CONF_SCHEDULE_ENTITY,
)
_REFERENCE_LIST_KEYS = (
    CONF_TRIGGER_SENSORS,
    CONF_MAINTAIN_SENSORS,
    CONF_HOLD_ENTITIES,
    CONF_TARGET_LIGHTS,
    # A virtual light may wrap another virtual light (the member picker is any
    # light entity). A removed member must not linger: _all_lights_off treats
    # an unresolvable member as "maybe still on", which would pin the outer
    # light on forever.
    CONF_LIGHTS,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a MoLight virtual entity from a config entry."""
    # Seeded before the platforms load so the light can always read the
    # auto-off flag; the companion switch overwrites it when it restores.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_AUTO_OFF_ENABLED: True}
    if entry.data[CONF_ENTITY_TYPE] == ENTITY_TYPE_REMOTE:
        # A Virtual Remote's runtime is the event-entity listener set up
        # here (torn down with the entry); its Last Action sensor rides the
        # normal platform forwarding below.
        entry.async_on_unload(async_setup_remote(hass, entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry whenever options are updated so entities pick up new values.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a MoLight config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Strip references to a removed entry's entities from surviving entries.

    Runs before HA clears the entity registry, so the removed entry's entity
    ids can still be resolved. Every surviving entry that referenced one of
    them is updated (which reloads it), so lights lose their sensor instead of
    keeping a reference to an entity that no longer exists.
    """
    registry = er.async_get(hass)
    removed = {
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    if not removed:
        return

    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id == entry.entry_id:
            continue
        cfg = molight_config(other)
        cleaned = dict(cfg)
        for key in _REFERENCE_KEYS:
            if cleaned.get(key) in removed:
                del cleaned[key]
        for key in _REFERENCE_LIST_KEYS:
            values = cleaned.get(key)
            if values and any(e in removed for e in values):
                cleaned[key] = [e for e in values if e not in removed]
        if cleaned == cfg:
            continue
        # Stored options fully replace data; entity_type/entity_id are
        # immutable and live only in entry.data (as everywhere else).
        cleaned = {
            k: v
            for k, v in cleaned.items()
            if k not in (CONF_ENTITY_TYPE, CONF_ENTITY_ID)
        }
        hass.config_entries.async_update_entry(other, options=cleaned)
