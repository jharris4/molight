"""Tests for the turn_on_blocked event on a light's Events entity.

A trigger that would have lit the room but was refused by a gate names the
trigger and the gate. A trigger arriving while the light is on is not a
refusal.
"""

from __future__ import annotations

import pytest

from custom_components.molight.const import (
    EVENT_TURN_ON_BLOCKED,
    ILLUMINANCE_MODE_CONTROL,
    REASON_BRIGHT,
    REASON_DOOR,
    REASON_OCCUPANCY,
    REASON_SCHEDULE,
    SCHEDULE_MODE_GATE,
)
from tests.conftest import make_light_entry, settle, setup_entries
from tests.events import record_events

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

REAL = "light.real_1"
VIRTUAL = "light.hall"
EVENTS = "event.hall_events"
OCC = "binary_sensor.occ"
DOOR = "binary_sensor.door"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"


def _blocked(trigger: str, reason: str) -> dict:
    return {
        "event_type": EVENT_TURN_ON_BLOCKED,
        "entity_id": VIRTUAL,
        "trigger": trigger,
        "reason": reason,
    }


async def _setup(hass, entry, **states) -> list[dict]:
    hass.states.async_set(REAL, "off")
    for entity_id, state in states.items():
        hass.states.async_set(entity_id.replace("__", "."), state)
    await setup_entries(hass, entry)
    await settle(hass)
    return record_events(hass, EVENTS)


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", [OCC, DOOR], ids=["occupancy", "door"])
async def test_bright_refuses_a_trigger(hass, trigger: str) -> None:
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall",
            occupancy=OCC,
            door=DOOR,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_CONTROL,
        ),
        binary_sensor__occ="off",
        binary_sensor__door="off",
        binary_sensor__illum="on",
    )

    hass.states.async_set(trigger, "on")
    await settle(hass)

    kind = REASON_OCCUPANCY if trigger == OCC else REASON_DOOR
    assert records == [_blocked(kind, REASON_BRIGHT)]
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("trigger", [OCC, DOOR], ids=["occupancy", "door"])
async def test_a_gate_window_refuses_a_trigger(hass, trigger: str) -> None:
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall",
            occupancy=OCC,
            door=DOOR,
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
        binary_sensor__occ="off",
        binary_sensor__door="off",
        binary_sensor__sched="off",
    )

    hass.states.async_set(trigger, "on")
    await settle(hass)

    kind = REASON_OCCUPANCY if trigger == OCC else REASON_DOOR
    assert records == [_blocked(kind, REASON_SCHEDULE)]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_going_dark_outside_a_gate_window_is_refused(hass) -> None:
    """The dark edge with presence is a trigger too, and the window refuses it."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall",
            occupancy=OCC,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_CONTROL,
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
        binary_sensor__occ="off",
        binary_sensor__illum="on",
        binary_sensor__sched="off",
    )
    hass.states.async_set(OCC, "on")
    await settle(hass)

    hass.states.async_set(ILLUM, "off")
    await settle(hass)

    assert records == [
        _blocked(REASON_OCCUPANCY, REASON_BRIGHT),
        _blocked(REASON_OCCUPANCY, REASON_SCHEDULE),
    ]


@pytest.mark.asyncio
async def test_a_trigger_while_the_light_is_on_is_no_refusal(hass) -> None:
    """Nothing was refused when the room was already lit."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall",
            occupancy=OCC,
            illuminance=ILLUM,
            illuminance_mode="gate",
        ),
        binary_sensor__occ="off",
        binary_sensor__illum="off",
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on", {"brightness": 255})
    await settle(hass)
    hass.states.async_set(ILLUM, "on")
    await settle(hass)

    hass.states.async_set(OCC, "on")
    await settle(hass)

    assert [r["event_type"] for r in records] == ["turned_on"]
