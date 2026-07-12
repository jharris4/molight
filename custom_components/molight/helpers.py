"""Shared helpers for the MoLight integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.entity import async_generate_entity_id

from .const import CONF_ENTITY_ID, CONF_ENTITY_TYPE

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def molight_config(entry: ConfigEntry) -> dict:
    """Return the effective configuration for a MoLight entry.

    Options flows store the complete edited form, so once options exist they
    fully replace the original data — merging the two would resurrect
    optional fields the user cleared (e.g. removing a light's occupancy
    reference). entity_type is immutable and only lives in data.
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
    entry.data (it is immutable and absent from molight_config once options
    exist). `suffix` lets the companion switch parallel its light, e.g.
    light.kitchen -> switch.kitchen_auto_off.
    """
    obj = entry.data.get(CONF_ENTITY_ID)
    if not obj:
        return None
    return async_generate_entity_id(entity_id_format, f"{obj}{suffix}", hass=hass)
