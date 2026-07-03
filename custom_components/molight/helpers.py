"""Shared helpers for the MoLight integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry

from .const import CONF_ENTITY_TYPE


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
