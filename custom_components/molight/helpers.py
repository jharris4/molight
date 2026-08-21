"""Shared helpers for the MoLight integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.light import (
    ATTR_SUPPORTED_COLOR_MODES,
    COLOR_MODES_BRIGHTNESS,
    COLOR_MODES_COLOR,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.helpers.entity import async_generate_entity_id

from .const import CONF_ENTITY_ID, CONF_ENTITY_TYPE

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, State


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


def _judge_lights(
    hass: HomeAssistant,
    entity_ids: Sequence[str],
    capable: Callable[[State], bool | None],
) -> bool | None:
    """Tri-state capability judgement across a set of real lights.

    True once any member is capable, False only once every member is judged and
    none is, None while any can't be judged. Shared so the runtime and the
    config flow can't drift apart.
    """
    all_judged = True
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        verdict = capable(state) if state is not None else None
        if verdict is None:
            all_judged = False
        elif verdict:
            return True
    return False if all_judged else None


def _color_modes(state: State) -> set[str] | None:
    """Return a light's advertised color modes, or None if it hasn't said yet.

    A light that reports nothing but "unknown" is explicitly undecided, which
    is not the same as being incapable.
    """
    modes = state.attributes.get(ATTR_SUPPORTED_COLOR_MODES)
    if not modes:
        return None
    modes = set(modes)
    return None if modes <= {ColorMode.UNKNOWN} else modes


def lights_support_transition(
    hass: HomeAssistant, entity_ids: Sequence[str]
) -> bool | None:
    """Whether any of these real lights can fade; None when it can't be judged.

    A light always publishes supported_features, even as 0 and even while
    unavailable, so an absent one is genuinely unknown rather than incapable.
    """

    def _capable(state: State) -> bool | None:
        features = state.attributes.get(ATTR_SUPPORTED_FEATURES)
        if features is None:
            return None
        return bool(features & LightEntityFeature.TRANSITION)

    return _judge_lights(hass, entity_ids, _capable)


def lights_support_color_temp(
    hass: HomeAssistant, entity_ids: Sequence[str]
) -> bool | None:
    """Whether any of these real lights can show a white color temperature."""

    def _capable(state: State) -> bool | None:
        modes = _color_modes(state)
        return None if modes is None else ColorMode.COLOR_TEMP in modes

    return _judge_lights(hass, entity_ids, _capable)


def lights_support_color(hass: HomeAssistant, entity_ids: Sequence[str]) -> bool | None:
    """Whether any of these real lights can show a color.

    Uses Home Assistant's own color-capable mode set, which is also what the
    virtual light checks before advertising HS.
    """

    def _capable(state: State) -> bool | None:
        modes = _color_modes(state)
        return None if modes is None else not COLOR_MODES_COLOR.isdisjoint(modes)

    return _judge_lights(hass, entity_ids, _capable)


def lights_support_brightness(
    hass: HomeAssistant, entity_ids: Sequence[str]
) -> bool | None:
    """Whether any of these real lights is dimmable.

    Every color-bearing mode implies brightness, so this only decides the
    floor: brightness-only versus on/off-only.
    """

    def _capable(state: State) -> bool | None:
        modes = _color_modes(state)
        return None if modes is None else not COLOR_MODES_BRIGHTNESS.isdisjoint(modes)

    return _judge_lights(hass, entity_ids, _capable)
