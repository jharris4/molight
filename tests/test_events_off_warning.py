"""Tests for the off_warning event on a light's Events entity.

Each stage of the warning sequence fires with the seconds left and the
stage's look; a re-trigger that ends the sequence early fires cancelled,
with the look the lights show after it.
"""

from __future__ import annotations

import pytest

from custom_components.molight.const import (
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_TIMEOUT,
    CONF_LIGHT_TIMEOUT,
    CONF_OCCUPANCY_ENTITY,
    CONF_STANDBY_BRIGHTNESS,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_TIMEOUT,
    EVENT_ADJUSTED,
    EVENT_OFF_WARNING,
    EVENT_TURNED_OFF,
    EVENT_TURNED_ON,
    REASON_MANUAL,
    REASON_TIMER,
    STAGE_CANCELLED,
    STAGE_EFFECT,
    STAGE_WARN,
)
from tests.conftest import (
    make_light_entry,
    make_scheduled_light_entry,
    settle,
    setup_entries,
)
from tests.events import record_events, tick

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

REAL = "light.real_1"
VIRTUAL = "light.hall"
EVENTS = "event.hall_events"
OCC = "binary_sensor.occ"
SCHED = "binary_sensor.sched"


def _warning(stage: str, off_in: int | None, **look) -> dict:
    return {
        "event_type": EVENT_OFF_WARNING,
        "entity_id": VIRTUAL,
        "stage": stage,
        "off_in": off_in,
        "standby": False,
        "brightness": None,
        "color": None,
        **look,
    }


async def _setup(hass, entry) -> list[dict]:
    hass.states.async_set(REAL, "off")
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, entry)
    await settle(hass)
    records = record_events(hass, EVENTS)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
    )
    hass.states.async_set(REAL, "on", {"brightness": 200})
    await settle(hass)
    assert [r["event_type"] for r in records] == [EVENT_TURNED_ON]
    del records[:]
    return records


@pytest.mark.asyncio
async def test_effect_then_warn_then_off(hass, freezer) -> None:
    """Each stage names the seconds left and its look; the off follows."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall",
            timeout=60,
            effect_timeout=10,
            effect_brightness=0,
            warn_timeout=15,
            warn_brightness=100,
        ),
    )

    await tick(hass, freezer, 61)
    assert records == [_warning(STAGE_EFFECT, 25, brightness=0)]
    await tick(hass, freezer, 11)
    assert records[1:] == [_warning(STAGE_WARN, 15, brightness=255)]
    await tick(hass, freezer, 16)

    assert records[2:] == [
        {
            "event_type": EVENT_TURNED_OFF,
            "entity_id": VIRTUAL,
            "reason": REASON_TIMER,
            "standby": False,
        }
    ]


@pytest.mark.asyncio
async def test_warn_stage_with_a_color(hass, freezer) -> None:
    """A warn stage keeping the brightness names it, and its own color."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall", timeout=60, warn_timeout=15, warn_color_temp=2700
        ),
    )

    await tick(hass, freezer, 61)

    assert records == [
        _warning(STAGE_WARN, 15, brightness=200, color={"color_temp_kelvin": 2700})
    ]


@pytest.mark.asyncio
async def test_a_retrigger_cancels_the_warning(hass, freezer) -> None:
    """Occupancy mid-warning cancels it; the lights stay on at their old look."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall", timeout=60, occupancy=OCC, warn_timeout=15, warn_brightness=100
        ),
    )
    await tick(hass, freezer, 61)

    hass.states.async_set(OCC, "on")
    await settle(hass)

    assert records == [
        _warning(STAGE_WARN, 15, brightness=255),
        _warning(STAGE_CANCELLED, None, brightness=200),
    ]
    assert hass.states.get(VIRTUAL).state == "on"


@pytest.mark.asyncio
async def test_a_turn_on_by_hand_cancels_the_warning_with_its_own_look(
    hass, freezer
) -> None:
    """A turn-on with a brightness mid-warning is an adjustment that cancels it."""
    records = await _setup(
        hass,
        make_light_entry(name="Hall", timeout=60, warn_timeout=15, warn_brightness=100),
    )
    await tick(hass, freezer, 61)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 90}, blocking=True
    )
    await settle(hass)

    assert records[1:] == [
        {
            "event_type": EVENT_ADJUSTED,
            "entity_id": VIRTUAL,
            "reason": REASON_MANUAL,
            "brightness": 90,
            "color": None,
        },
        _warning(STAGE_CANCELLED, None, brightness=90),
    ]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_a_warning_ending_at_standby_says_so(hass, freezer) -> None:
    """standby is set when the sequence rests at standby rather than dark."""
    hass.states.async_set(SCHED, "on")
    records = await _setup(
        hass,
        make_scheduled_light_entry(
            name="Hall",
            schedule=SCHED,
            outside={CONF_LIGHT_TIMEOUT: 30},
            inside={
                CONF_LIGHT_TIMEOUT: 30,
                CONF_OCCUPANCY_ENTITY: OCC,
                CONF_AUTO_ON_BRIGHTNESS: 100,
                CONF_STANDBY_BRIGHTNESS: 20,
                CONF_EFFECT_TIMEOUT: 5,
                CONF_EFFECT_BRIGHTNESS: 0,
                CONF_WARN_TIMEOUT: 10,
                CONF_WARN_BRIGHTNESS: 60,
            },
        ),
    )

    await tick(hass, freezer, 31)
    await tick(hass, freezer, 6)
    await tick(hass, freezer, 11)

    assert records == [
        _warning(STAGE_EFFECT, 15, standby=True, brightness=0),
        _warning(STAGE_WARN, 10, standby=True, brightness=153),
        {
            "event_type": EVENT_TURNED_OFF,
            "entity_id": VIRTUAL,
            "reason": REASON_TIMER,
            "standby": True,
        },
    ]
