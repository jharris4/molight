"""Direct tests for the shared helpers (molight_config, suggested_entity_id)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.components.light import ENTITY_ID_FORMAT as LIGHT_ENTITY_ID_FORMAT
from homeassistant.components.switch import ENTITY_ID_FORMAT as SWITCH_ENTITY_ID_FORMAT
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.const import (
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
)
from custom_components.molight.helpers import molight_config, suggested_entity_id

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _entry(data: dict, options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data=data, options=options or {})


# ---------------------------------------------------------------------------
# molight_config
# ---------------------------------------------------------------------------


def test_molight_config_without_options_uses_data() -> None:
    entry = _entry(
        {
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Kitchen",
            CONF_LIGHTS: ["light.kitchen_real"],
        }
    )
    cfg = molight_config(entry)
    assert cfg[CONF_NAME] == "Kitchen"
    assert cfg[CONF_LIGHTS] == ["light.kitchen_real"]
    assert cfg[CONF_ENTITY_TYPE] == ENTITY_TYPE_LIGHT


def test_molight_config_options_fully_replace_data() -> None:
    """Options replace data — cleared optional fields must not resurrect."""
    entry = _entry(
        {
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Kitchen",
            CONF_LIGHTS: ["light.kitchen_real"],
            CONF_LIGHT_TIMEOUT: 60,
            # The user cleared this reference in the options flow, so it is
            # present in data but absent from options.
            CONF_OCCUPANCY_ENTITY: "binary_sensor.kitchen_occupancy",
        },
        options={
            CONF_NAME: "Kitchen Renamed",
            CONF_LIGHTS: ["light.kitchen_real"],
            CONF_LIGHT_TIMEOUT: 120,
        },
    )
    cfg = molight_config(entry)
    assert cfg[CONF_NAME] == "Kitchen Renamed"
    assert cfg[CONF_LIGHT_TIMEOUT] == 120
    assert CONF_OCCUPANCY_ENTITY not in cfg


def test_molight_config_entity_type_always_comes_from_data() -> None:
    """entity_type is immutable: even a corrupt options value cannot change it."""
    entry = _entry(
        {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT, CONF_NAME: "Kitchen"},
        options={CONF_ENTITY_TYPE: "occupancy", CONF_NAME: "Kitchen"},
    )
    assert molight_config(entry)[CONF_ENTITY_TYPE] == ENTITY_TYPE_LIGHT


def test_molight_config_returns_copies() -> None:
    """Mutating the returned mapping must not leak into the entry."""
    entry = _entry({CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT, CONF_NAME: "Kitchen"})
    cfg = molight_config(entry)
    cfg[CONF_NAME] = "Mutated"
    assert entry.data[CONF_NAME] == "Kitchen"


# ---------------------------------------------------------------------------
# suggested_entity_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggested_entity_id_absent_or_empty_returns_none(
    hass: HomeAssistant,
) -> None:
    absent = _entry({CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT, CONF_NAME: "Kitchen"})
    assert suggested_entity_id(hass, absent, LIGHT_ENTITY_ID_FORMAT) is None

    empty = _entry(
        {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT, CONF_NAME: "Kitchen", CONF_ENTITY_ID: ""}
    )
    assert suggested_entity_id(hass, empty, LIGHT_ENTITY_ID_FORMAT) is None


@pytest.mark.asyncio
async def test_suggested_entity_id_formats_object_id(hass: HomeAssistant) -> None:
    entry = _entry(
        {
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Kitchen",
            CONF_ENTITY_ID: "kitchen",
        }
    )
    assert suggested_entity_id(hass, entry, LIGHT_ENTITY_ID_FORMAT) == "light.kitchen"
    assert (
        suggested_entity_id(hass, entry, SWITCH_ENTITY_ID_FORMAT, suffix="_auto_off")
        == "switch.kitchen_auto_off"
    )


@pytest.mark.asyncio
async def test_suggested_entity_id_uniquifies_on_clash(hass: HomeAssistant) -> None:
    """A taken id gets HA's _2 suffix instead of colliding."""
    hass.states.async_set("light.kitchen", "off")
    entry = _entry(
        {
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Kitchen",
            CONF_ENTITY_ID: "kitchen",
        }
    )
    assert suggested_entity_id(hass, entry, LIGHT_ENTITY_ID_FORMAT) == "light.kitchen_2"


@pytest.mark.asyncio
async def test_suggested_entity_id_reads_data_not_options(hass: HomeAssistant) -> None:
    """The object_id is immutable: it must come from entry.data even once
    options exist (molight_config would drop it)."""
    entry = _entry(
        {
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Kitchen",
            CONF_ENTITY_ID: "kitchen",
        },
        options={CONF_NAME: "Kitchen Renamed"},
    )
    assert suggested_entity_id(hass, entry, LIGHT_ENTITY_ID_FORMAT) == "light.kitchen"
