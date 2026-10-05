"""The MoLight integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_RESTORED
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    CONF_DOOR_ENTITY,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_SENSOR,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_SENSORS,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_INPUTS,
    CONF_SCHEDULE_SOURCE,
    CONF_TARGET_LIGHTS,
    CONF_TRIGGER_SENSORS,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_SOURCE_ENTITY,
    DATA_AUTO_OFF_ENABLED,
    DATA_AUTO_OFF_KEPT,
    DATA_PLATFORMS,
    DATA_REMOVED,
    DATA_RENAMED,
    DATA_SCHEDULE_WATCH,
    DOMAIN,
    ENTITY_TYPE_REMOTE,
    PLATFORMS,
    PLATFORMS_BY_ENTITY_TYPE,
    REMOTE_ACTION_FIELDS,
)
from .helpers import molight_config
from .remote import async_setup_remote

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.typing import ConfigType

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

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
# Every key that stores an entity ID. The further ones only ever name
# entities MoLight does not create, so a removed entry cannot leave them.
_ENTITY_ID_KEYS = (
    *_REFERENCE_KEYS,
    CONF_OCCUPANCY_SENSOR,
    CONF_ILLUMINANCE_SENSOR,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_SOURCE_ENTITY,
)
_ENTITY_ID_LIST_KEYS = (
    *_REFERENCE_LIST_KEYS,
    *(key for single, double, _ in REMOTE_ACTION_FIELDS for key in (single, double)),
)
# How long a renamed entity may take to report under its new ID (seconds).
RENAME_SETTLE_SECONDS = 10


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Follow entity ID changes for as long as Home Assistant runs."""
    renamed: dict[str, str] = hass.data.setdefault(DATA_RENAMED, {})
    removed: set[str] = hass.data.setdefault(DATA_REMOVED, set())
    # Renames whose entity has yet to report under its new ID.
    pending: dict[str, str] = {}

    @callback
    def _rewrite(entity_id: str) -> None:
        mapping = {old: new for old, new in pending.items() if new == entity_id}
        for old in mapping:
            del pending[old]
        if mapping:
            _rename_references(hass, mapping)

    @callback
    def _on_registry_updated(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        data = event.data
        if data["action"] == "create":
            # A new entity under a former ID is not the one that was renamed.
            renamed.pop(data["entity_id"], None)
            removed.discard(data["entity_id"])
        if data["action"] != "update" or "old_entity_id" not in data:
            return
        old_id, new_id = data["old_entity_id"], data["entity_id"]
        removed.discard(new_id)
        for mapping in (renamed, pending):
            # A second rename moves every earlier ID on to the newest one.
            for former, current in mapping.items():
                if current == old_id:
                    mapping[former] = new_id
            mapping[old_id] = new_id
            mapping.pop(new_id, None)
        _when_reporting(hass, new_id, _rewrite)

    hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _on_registry_updated)
    return True


@callback
def _when_reporting(
    hass: HomeAssistant, entity_id: str, action: Callable[[str], None]
) -> None:
    """Run action once the entity has a state, or at once if none is due.

    A renamed entity is removed and added again under its new ID. The entries
    that reference it reload when their reference is rewritten, and one that
    reloads before the entity is back would find, say, its real light gone.
    """
    registry_entry = er.async_get(hass).async_get(entity_id)
    entry = (
        hass.config_entries.async_get_entry(registry_entry.config_entry_id)
        if registry_entry is not None and registry_entry.config_entry_id
        else None
    )
    if (
        hass.states.get(entity_id) is not None
        or registry_entry is None
        or registry_entry.disabled
        or (entry is not None and entry.state is not ConfigEntryState.LOADED)
    ):
        action(entity_id)
        return

    @callback
    def _done(_event: object) -> None:
        unsub_state()
        unsub_timer()
        action(entity_id)

    unsub_state = async_track_state_change_event(hass, [entity_id], _done)
    unsub_timer = async_call_later(hass, RENAME_SETTLE_SECONDS, _done)


@callback
def _rename_references(hass: HomeAssistant, mapping: dict[str, str]) -> None:
    """Point every entry's references to renamed entities at their new IDs.

    Data and options are both rewritten; an entry that changed reloads.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        hass.config_entries.async_update_entry(
            entry,
            data=_remap_references(
                entry.data, mapping, _ENTITY_ID_KEYS, _ENTITY_ID_LIST_KEYS
            ),
            options=_remap_references(
                entry.options, mapping, _ENTITY_ID_KEYS, _ENTITY_ID_LIST_KEYS
            ),
        )


def current_references(
    hass: HomeAssistant, cfg: Mapping[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Return cfg with renamed references at their new IDs, and one since removed.

    A form left open still holds the IDs it showed, which a rename or a
    removal has since rewritten in every saved entry.
    """
    current = _remap_references(
        cfg, hass.data.get(DATA_RENAMED, {}), _ENTITY_ID_KEYS, _ENTITY_ID_LIST_KEYS
    )
    removed = hass.data.get(DATA_REMOVED, set())
    return current, next(
        (entity_id for entity_id in _referenced_ids(current) if entity_id in removed),
        None,
    )


def _referenced_ids(cfg: Mapping[str, Any]) -> Iterator[str]:
    """Yield every entity ID cfg stores, in both settings mappings too."""
    for settings in (
        cfg,
        cfg.get(CONF_OUTSIDE_SCHEDULE_SETTINGS),
        cfg.get(CONF_INSIDE_SCHEDULE_SETTINGS),
    ):
        if not isinstance(settings, Mapping):
            continue
        for key in _ENTITY_ID_KEYS:
            if isinstance(value := settings.get(key), str):
                yield value
        for key in _ENTITY_ID_LIST_KEYS:
            yield from settings.get(key) or ()


def _remap_references(
    cfg: Mapping[str, Any],
    mapping: Mapping[str, str | None],
    keys: tuple[str, ...],
    list_keys: tuple[str, ...],
) -> dict[str, Any]:
    """Return cfg with each mapped entity ID replaced, or dropped if mapped to None.

    Covers the top level and both settings mappings of a scheduled light.
    """
    remapped = _remap_settings(cfg, mapping, keys, list_keys)
    for side in (CONF_OUTSIDE_SCHEDULE_SETTINGS, CONF_INSIDE_SCHEDULE_SETTINGS):
        if isinstance(settings := remapped.get(side), dict):
            remapped[side] = _remap_settings(settings, mapping, keys, list_keys)
    return remapped


def _remap_settings(
    cfg: Mapping[str, Any],
    mapping: Mapping[str, str | None],
    keys: tuple[str, ...],
    list_keys: tuple[str, ...],
) -> dict[str, Any]:
    remapped = dict(cfg)
    for key in keys:
        value = remapped.get(key)
        if isinstance(value, str) and value in mapping:
            if mapping[value] is None:
                del remapped[key]
            else:
                remapped[key] = mapping[value]
    for key in list_keys:
        values = remapped.get(key)
        if values and any(e in mapping for e in values):
            # A list that already held the new ID must not hold it twice.
            remapped[key] = list(
                dict.fromkeys(
                    new for e in values if (new := mapping.get(e, e)) is not None
                )
            )
    return remapped


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
    if watch := hass.data.get(DATA_SCHEDULE_WATCH, {}).pop(entry.entry_id, None):
        watch.unsub()
    registry = er.async_get(hass)
    removed = {
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    if not removed:
        return
    hass.data.setdefault(DATA_REMOVED, set()).update(removed)
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
        cleaned = _remap_references(
            cfg, dict.fromkeys(removed), _REFERENCE_KEYS, _REFERENCE_LIST_KEYS
        )
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
