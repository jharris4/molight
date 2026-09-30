"""The MoLight integration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from homeassistant.const import ATTR_RESTORED
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DOOR_ENTITY,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_SENSORS,
    CONF_OCCUPANCY_ENTITY,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_INPUTS,
    CONF_SCHEDULE_SOURCE,
    CONF_TARGET_LIGHTS,
    CONF_TRIGGER_SENSORS,
    DATA_AUTO_OFF_ENABLED,
    DATA_AUTO_OFF_KEPT,
    DATA_PLATFORMS,
    DOMAIN,
    ENTITY_TYPE_REMOTE,
    ENTITY_TYPE_SCHEDULED_LIGHT,
    PLATFORMS,
    PLATFORMS_BY_ENTITY_TYPE,
)
from .helpers import molight_config
from .remote import async_setup_remote

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# Config keys under which one MoLight entry may reference another entry's
# entities. Cleaned up when the referenced entry is removed: a dangling
# reference is worse than a missing one (a light whose gate-mode schedule
# entity no longer exists would read the gate as permanently closed and
# silently stop automating).
_REFERENCE_KEYS = (
    CONF_DOOR_ENTITY,
    CONF_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_ILLUMINANCE_ENTITY,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_SOURCE,
)
_REFERENCE_LIST_KEYS = (
    CONF_TRIGGER_SENSORS,
    CONF_MAINTAIN_SENSORS,
    CONF_SCHEDULE_INPUTS,
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
    platforms = PLATFORMS_BY_ENTITY_TYPE.get(entry.data[CONF_ENTITY_TYPE], PLATFORMS)
    # Seeded before the platforms load so the light can always read the
    # auto-off flag; the companion switch overwrites it when it restores. A
    # switch disabled in the registry is not added and could never release
    # the hold, so its kept state is dropped, as a restart would drop it.
    auto_off_kept = hass.data.get(DATA_AUTO_OFF_KEPT, {}).pop(entry.entry_id, True)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_AUTO_OFF_ENABLED: auto_off_kept or _auto_off_switch_disabled(hass, entry),
        DATA_PLATFORMS: platforms,
    }
    if entry.data[CONF_ENTITY_TYPE] == ENTITY_TYPE_REMOTE:
        # A Virtual Remote's runtime is the event-entity listener set up
        # here (torn down with the entry); its Last Action sensor rides the
        # normal platform forwarding below.
        entry.async_on_unload(async_setup_remote(hass, entry))
    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    # Reload the entry whenever options are updated so entities pick up new values.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


def _auto_off_switch_disabled(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Return True when the entry's Auto-off switch is disabled in the registry."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_auto_off"
    )
    reg_entry = registry.async_get(entity_id) if entity_id else None
    return reg_entry is not None and reg_entry.disabled


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a MoLight config entry."""
    platforms = (
        hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get(DATA_PLATFORMS)
        or PLATFORMS
    )
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
        hass.data.setdefault(DATA_AUTO_OFF_KEPT, {})[entry.entry_id] = entry_data.get(
            DATA_AUTO_OFF_ENABLED, True
        )
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Strip references to a removed entry's entities from surviving entries.

    Runs before HA clears the entity registry, so the removed entry's entity
    ids can still be resolved. Every surviving entry that referenced one of
    them is updated (which reloads it), so lights lose their sensor instead of
    keeping a reference to an entity that no longer exists.
    """
    hass.data.get(DATA_AUTO_OFF_KEPT, {}).pop(entry.entry_id, None)
    registry = er.async_get(hass)
    removed = {
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    if not removed:
        return
    # The unload left restored placeholders that read as a reload in
    # progress; drop them now, as HA's registry cleanup would after the
    # reloads below, so the watchers see the removal for what it is.
    for entity_id in removed:
        state = hass.states.get(entity_id)
        if state is not None and state.attributes.get(ATTR_RESTORED):
            hass.states.async_remove(entity_id)
    # Some HA versions dispatch tracked state changes one loop iteration
    # later; yield once so the removed entities' dropout reaches the entries
    # that watched them before the reloads below unsubscribe those listeners.
    await asyncio.sleep(0)

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
        if cfg.get(CONF_ENTITY_TYPE) == ENTITY_TYPE_SCHEDULED_LIGHT:
            for side in (CONF_OUTSIDE_SCHEDULE_SETTINGS, CONF_INSIDE_SCHEDULE_SETTINGS):
                settings = dict(cleaned.get(side, {}))
                for key in _REFERENCE_KEYS:
                    if settings.get(key) in removed:
                        del settings[key]
                for key in _REFERENCE_LIST_KEYS:
                    values = settings.get(key)
                    if values and any(e in removed for e in values):
                        settings[key] = [e for e in values if e not in removed]
                # Materialising an absent side would differ from cfg and
                # force a needless reload.
                if side in cleaned or settings:
                    cleaned[side] = settings
        if cleaned == cfg:
            continue
        # Stored options fully replace data; authoritative entity_type and the
        # immutable entity_id live only in entry.data (as everywhere else).
        cleaned = {
            k: v
            for k, v in cleaned.items()
            if k not in (CONF_ENTITY_TYPE, CONF_ENTITY_ID)
        }
        hass.config_entries.async_update_entry(other, options=cleaned)
