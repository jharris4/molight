"""Tests for what a wrapping light's Events entity records of a wrapped light.

The wrapped light's own on and off start and end the outer light's on-period,
so they fire there with reason wrapped. Its own look changes fire nothing at
the outer light, and a person's change on its bulb is still physical.
"""

from __future__ import annotations

import pytest

from custom_components.molight.const import (
    CONF_LIGHT_TIMEOUT,
    CONF_STANDBY_BRIGHTNESS,
    EVENT_TURNED_OFF,
    EVENT_TURNED_ON,
    REASON_PHYSICAL,
    REASON_WRAPPED,
)
from tests.conftest import make_light_entry, make_scheduled_light_entry, settle
from tests.events import record_events, tick
from tests.test_light_nested import (
    INNER,
    INNER_OCC,
    OUTER,
    REAL,
    WINDOW,
    _lit_by_outer,
    _nested,
)

OUTER_EVENTS = "event.outer_events"


def _outer_on(brightness: int | None) -> dict:
    return {
        "event_type": EVENT_TURNED_ON,
        "reason": REASON_WRAPPED,
        "standby": False,
        "brightness": brightness,
        "color": None,
        "selection": None,
    }


def _outer_off(reason: str) -> dict:
    return {"event_type": EVENT_TURNED_OFF, "reason": reason, "standby": False}


async def _lit_virtually(hass, brightness: int) -> list[dict]:
    """Light the room through the outer light; record what follows."""
    records = record_events(hass, OUTER_EVENTS)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": OUTER, "brightness": brightness},
        blocking=True,
    )
    await settle(hass)
    assert [r["event_type"] for r in records] == [EVENT_TURNED_ON]
    del records[:]
    return records


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["occupancy", "standby"])
async def test_the_inner_lighting_itself_is_wrapped(hass, trigger: str) -> None:
    """The inner's own sensor or standby lights it: wrapped at the outer,
    with the brightness the inner reported with its on."""
    hass.states.async_set(INNER_OCC, "off")
    if trigger == "occupancy":
        inner = make_light_entry(
            name="Inner", lights=[REAL], timeout=60, occupancy=INNER_OCC
        )
    else:
        inner = make_scheduled_light_entry(
            name="Inner",
            lights=[REAL],
            schedule=WINDOW,
            inside={CONF_LIGHT_TIMEOUT: 60, CONF_STANDBY_BRIGHTNESS: 20},
        )
    await _nested(hass, "regular", inner=inner)
    records = record_events(hass, OUTER_EVENTS)

    hass.states.async_set(INNER_OCC if trigger == "occupancy" else WINDOW, "on")
    await settle(hass)

    assert hass.states.get(INNER).state == "on"
    assert records == [_outer_on(None if trigger == "occupancy" else 51)]


@pytest.mark.asyncio
async def test_the_inner_timer_off_is_wrapped(hass, freezer) -> None:
    """The inner's own timer ends the outer's on-period as wrapped."""
    await _nested(hass, "regular", occupancy="binary_sensor.occ")
    records = record_events(hass, OUTER_EVENTS)
    await _lit_by_outer(hass, "regular")
    assert [r["event_type"] for r in records] == [EVENT_TURNED_ON]

    await tick(hass, freezer, 61)

    assert hass.states.get(OUTER).state == "off"
    assert records[1:] == [_outer_off(REASON_WRAPPED)]


@pytest.mark.asyncio
async def test_the_inner_warn_dim_fires_nothing_at_the_outer(hass, freezer) -> None:
    """A stage that only dims the inner is mirrored, not an event."""
    inner = make_light_entry(
        name="Inner", lights=[REAL], timeout=100, warn_timeout=50, warn_brightness=20
    )
    await _nested(hass, "regular", inner=inner)
    records = await _lit_virtually(hass, 200)

    await tick(hass, freezer, 101)

    assert hass.states.get(OUTER).attributes["brightness"] == 51
    assert records == []


@pytest.mark.asyncio
async def test_an_off_at_the_wall_on_the_inner_bulb_is_physical(hass) -> None:
    """A person switching the bulb off is physical for the outer light too."""
    await _nested(hass, "regular", occupancy="binary_sensor.occ")
    records = record_events(hass, OUTER_EVENTS)
    await _lit_by_outer(hass, "regular")

    hass.states.async_set(REAL, "off")
    await settle(hass)

    assert hass.states.get(OUTER).state == "off"
    assert records[1:] == [_outer_off(REASON_PHYSICAL)]
