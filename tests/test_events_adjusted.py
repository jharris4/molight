"""Tests for the adjusted event on a light's Events entity.

A person's dim or recolor of a light that is on names what changed, and
whether it was through the entity or at the wall. MoLight's own changes
fire nothing here: they are turned_on and off_warning events.
"""

from __future__ import annotations

import pytest

from custom_components.molight.const import (
    EVENT_ADJUSTED,
    EVENT_TURNED_ON,
    REASON_PHYSICAL,
    REASON_VIRTUAL,
)
from tests.conftest import make_light_entry, settle, setup_entries
from tests.events import record_events

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

REAL = "light.real_1"
VIRTUAL = "light.hall"
EVENTS = "event.hall_events"
TEMP_CAPS = {
    "supported_color_modes": ["color_temp"],
    "color_mode": "color_temp",
    "min_color_temp_kelvin": 2000,
    "max_color_temp_kelvin": 6500,
}


def _adjusted(reason: str, **look) -> dict:
    return {
        "event_type": EVENT_ADJUSTED,
        "reason": reason,
        "brightness": None,
        "color": None,
        **look,
    }


async def _setup_lit(hass) -> list[dict]:
    hass.states.async_set(REAL, "off", TEMP_CAPS)
    await setup_entries(hass, make_light_entry(name="Hall", auto_on_brightness=50))
    await settle(hass)
    records = record_events(hass, EVENTS)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
    )
    hass.states.async_set(
        REAL, "on", {**TEMP_CAPS, "brightness": 200, "color_temp_kelvin": 4000}
    )
    await settle(hass)
    assert [r["event_type"] for r in records] == [EVENT_TURNED_ON]
    del records[:]
    return records


@pytest.mark.asyncio
async def test_a_dim_through_the_entity(hass) -> None:
    records = await _setup_lit(hass)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 100}, blocking=True
    )
    await settle(hass)

    assert records == [_adjusted(REASON_VIRTUAL, brightness=100)]


@pytest.mark.asyncio
async def test_a_recolor_through_the_entity(hass) -> None:
    records = await _setup_lit(hass)

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": VIRTUAL, "color_temp_kelvin": 3000},
        blocking=True,
    )
    await settle(hass)

    assert records == [_adjusted(REASON_VIRTUAL, color={"color_temp_kelvin": 3000})]


@pytest.mark.asyncio
async def test_a_dim_at_the_wall(hass) -> None:
    records = await _setup_lit(hass)

    hass.states.async_set(
        REAL, "on", {**TEMP_CAPS, "brightness": 100, "color_temp_kelvin": 4000}
    )
    await settle(hass)

    assert records == [_adjusted(REASON_PHYSICAL, brightness=100)]


@pytest.mark.asyncio
async def test_a_recolor_at_the_wall(hass) -> None:
    records = await _setup_lit(hass)

    hass.states.async_set(
        REAL, "on", {**TEMP_CAPS, "brightness": 200, "color_temp_kelvin": 3000}
    )
    await settle(hass)

    assert records == [_adjusted(REASON_PHYSICAL, color={"color_temp_kelvin": 3000})]


@pytest.mark.asyncio
async def test_a_turn_on_without_a_look_adjusts_nothing(hass) -> None:
    records = await _setup_lit(hass)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)

    assert records == []
