"""Shared helpers for the MoLight integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.light import LightEntityFeature
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.helpers.entity import async_generate_entity_id

from .const import CONF_ENTITY_ID, CONF_ENTITY_TYPE

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def molight_config(entry: ConfigEntry) -> dict:
    """Return the effective configuration for a MoLight entry.

    Options flows store the complete edited form, so once options exist they
    fully replace the original data — merging the two would resurrect
    optional fields the user cleared (e.g. removing a light's occupancy
    reference). entity_type is authoritative in data (and is changed only by
    the explicit light-conversion flow), never by ordinary options edits.
    """
    cfg = dict(entry.options) if entry.options else dict(entry.data)
    cfg[CONF_ENTITY_TYPE] = entry.data[CONF_ENTITY_TYPE]
    return cfg


def suggested_entity_id(
    hass: HomeAssistant,
    entry: ConfigEntry,
    entity_id_format: str,
    suffix: str = "",
) -> str | None:
    """Suggested entity_id from an entry's stored object_id, or None to derive.

    Honored only at first registration — HA uniquifies with _2 on a clash and
    the registry pins the id thereafter. The object_id is read straight from
    entry.data (the entity id is immutable and absent from molight_config once
    options exist). `suffix` lets the companion switch parallel its light, e.g.
    light.kitchen -> switch.kitchen_auto_off.
    """
    obj = entry.data.get(CONF_ENTITY_ID)
    if not obj:
        return None
    return async_generate_entity_id(entity_id_format, f"{obj}{suffix}", hass=hass)


def lights_support_transition(
    hass: HomeAssistant, entity_ids: Sequence[str]
) -> bool | None:
    """Whether any of these real lights can fade; None when it can't be judged.

    True as soon as one member can fade. False only once every member has been
    judged and none can. None while any member is missing from the state
    machine — a light always publishes supported_features, even as 0 and even
    while unavailable, so an absent one is genuinely unknown rather than
    incapable.

    Shared so the runtime (which advertises the feature) and the config flow
    (which rejects a fade nothing could apply) can't drift apart.
    """
    all_judged = True
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        features = state.attributes.get(ATTR_SUPPORTED_FEATURES) if state else None
        if features is None:
            all_judged = False
        elif features & LightEntityFeature.TRANSITION:
            return True
    return False if all_judged else None
