"""Tests for the turned_on event on a light's Events entity.

It fires once per on-period start, and when a resting light is raised,
with the reason, the look MoLight commanded and the turn-on selection it
applied. A re-send, a member back from unavailable and a restart that
re-applies a look fire nothing.
"""

from __future__ import annotations

import pytest
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.molight.const import (
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_LIGHT_TIMEOUT,
    CONF_OCCUPANCY_ENTITY,
    CONF_STANDBY_BRIGHTNESS,
    CONF_STANDBY_COLOR_TEMP,
    EVENT_TURN_ON_BLOCKED,
    EVENT_TURNED_OFF,
    EVENT_TURNED_ON,
    ILLUMINANCE_MODE_CONTROL,
    REASON_BRIGHT,
    REASON_DOOR,
    REASON_ILLUMINANCE,
    REASON_MANUAL,
    REASON_OCCUPANCY,
    REASON_PHYSICAL,
    REASON_RESUME,
    REASON_SCHEDULE,
    REASON_SCHEDULE_START,
    REASON_STARTUP,
    SCHEDULE_MODE_FOLLOW,
)
from tests.conftest import (
    make_light_entry,
    make_scheduled_light_entry,
    restart_entries,
    settle,
    setup_entries,
)
from tests.events import record_events, tick
from tests.real_entities import RealSelect, add_real

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

REAL = "light.real_1"
VIRTUAL = "light.hall"
EVENTS = "event.hall_events"
OCC = "binary_sensor.occ"
DOOR = "binary_sensor.door"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
MARKER = "2026-01-14T18:00:00+00:00"


def _turned_on(reason: str, **look) -> dict:
    return {
        "event_type": EVENT_TURNED_ON,
        "entity_id": VIRTUAL,
        "reason": reason,
        "standby": False,
        "brightness": None,
        "color": None,
        "selection": None,
        **look,
    }


async def _setup(hass, entry, **states) -> list[dict]:
    hass.states.async_set(REAL, "off")
    for entity_id, state in states.items():
        hass.states.async_set(entity_id.replace("__", "."), state)
    await setup_entries(hass, entry)
    await settle(hass)
    return record_events(hass, EVENTS)


async def _turn_on(hass, **data) -> None:
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, **data}, blocking=True
    )
    await settle(hass)


@pytest.mark.asyncio
async def test_manual_turn_on(hass) -> None:
    """A turn-on through the entity names the caller's look."""
    records = await _setup(hass, make_light_entry(name="Hall"))

    await _turn_on(hass, brightness=200)

    assert records == [_turned_on(REASON_MANUAL, brightness=200)]


@pytest.mark.asyncio
async def test_physical_turn_on(hass) -> None:
    """A member lit at the wall names what the member reported."""
    records = await _setup(hass, make_light_entry(name="Hall"))

    hass.states.async_set(REAL, "on", {"brightness": 120})
    await settle(hass)

    assert records == [_turned_on(REASON_PHYSICAL, brightness=120)]


@pytest.mark.asyncio
async def test_occupancy_turn_on_names_the_auto_on_look(hass) -> None:
    """An automatic turn-on names the auto-on brightness and color."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall", occupancy=OCC, auto_on_brightness=100, auto_on_color_temp=3000
        ),
        binary_sensor__occ="off",
    )

    hass.states.async_set(OCC, "on")
    await settle(hass)

    assert records == [
        _turned_on(REASON_OCCUPANCY, brightness=255, color={"color_temp_kelvin": 3000})
    ]


@pytest.mark.asyncio
async def test_turn_on_selection_is_named(hass) -> None:
    """The selection an off-to-on command applies is in the event."""
    await add_real(hass, RealSelect("theme"))
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall",
            occupancy=OCC,
            turn_on_select_entity="select.theme",
            turn_on_select_option="Bright",
        ),
        binary_sensor__occ="off",
    )

    hass.states.async_set(OCC, "on")
    await settle(hass)

    assert records == [_turned_on(REASON_OCCUPANCY, selection="Bright")]
    assert hass.states.get("select.theme").state == "Bright"


@pytest.mark.asyncio
async def test_door_turn_on(hass) -> None:
    """A door opening is its own reason."""
    records = await _setup(
        hass, make_light_entry(name="Hall", door=DOOR), binary_sensor__door="off"
    )

    hass.states.async_set(DOOR, "on")
    await settle(hass)

    assert records == [_turned_on(REASON_DOOR)]


@pytest.mark.asyncio
async def test_going_dark_with_presence(hass) -> None:
    """Presence refused while bright lights the room when it goes dark."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall",
            occupancy=OCC,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_CONTROL,
        ),
        binary_sensor__occ="off",
        binary_sensor__illum="on",
    )

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(ILLUM, "off")
    await settle(hass)

    assert records == [
        {
            "event_type": EVENT_TURN_ON_BLOCKED,
            "entity_id": VIRTUAL,
            "trigger": REASON_OCCUPANCY,
            "reason": REASON_BRIGHT,
        },
        _turned_on(REASON_ILLUMINANCE),
    ]


@pytest.mark.asyncio
async def test_going_dark_resumes_an_on_period_cut_short(hass, freezer) -> None:
    """Dark within the interrupted on-period resumes it, as its own reason."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall", illuminance=ILLUM, illuminance_mode=ILLUMINANCE_MODE_CONTROL
        ),
        binary_sensor__illum="off",
    )

    await _turn_on(hass)
    await tick(hass, freezer, 20)
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    await tick(hass, freezer, 10)
    hass.states.async_set(ILLUM, "off")
    await settle(hass)

    assert [r["event_type"] for r in records] == [
        EVENT_TURNED_ON,
        EVENT_TURNED_OFF,
        EVENT_TURNED_ON,
    ]
    assert records[0]["reason"] == REASON_MANUAL
    assert records[1]["reason"] == REASON_BRIGHT
    assert records[2] == _turned_on(REASON_RESUME)


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_window_start(hass) -> None:
    """A follow window starting is a schedule_start."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall", schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW
        ),
        binary_sensor__sched="off",
    )

    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)

    assert records == [_turned_on(REASON_SCHEDULE_START)]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_presence_outside_a_gate_window_is_blocked_then_lit_at_its_start(
    hass,
) -> None:
    """Occupancy refused by a gate window lights the room when it opens."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall", occupancy=OCC, schedule=SCHED, schedule_mode="gate"
        ),
        binary_sensor__occ="off",
        binary_sensor__sched="off",
    )

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(SCHED, "on")
    await settle(hass)

    assert records == [
        {
            "event_type": EVENT_TURN_ON_BLOCKED,
            "entity_id": VIRTUAL,
            "trigger": REASON_OCCUPANCY,
            "reason": REASON_SCHEDULE,
        },
        _turned_on(REASON_OCCUPANCY),
    ]


def _porch(**inside) -> object:
    return make_scheduled_light_entry(
        name="Hall",
        schedule=SCHED,
        outside={CONF_LIGHT_TIMEOUT: 30},
        inside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_OCCUPANCY_ENTITY: OCC,
            CONF_AUTO_ON_BRIGHTNESS: 100,
            CONF_STANDBY_BRIGHTNESS: 20,
            CONF_STANDBY_COLOR_TEMP: 2222,
            **inside,
        },
    )


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_standby_at_a_schedule_start_and_its_raise(hass) -> None:
    """Resting at standby is a turned_on with standby set; a raise is another."""
    records = await _setup(
        hass, _porch(), binary_sensor__occ="off", binary_sensor__sched="off"
    )

    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)

    assert records == [
        _turned_on(
            REASON_SCHEDULE_START,
            standby=True,
            brightness=51,
            color={"color_temp_kelvin": 2222},
        ),
        _turned_on(REASON_OCCUPANCY, brightness=255),
    ]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_startup_at_standby(hass) -> None:
    """A light found off where it rests at standby comes on for the startup."""
    records = await _setup(
        hass, _porch(), binary_sensor__occ="off", binary_sensor__sched="on"
    )
    state = hass.states.get(EVENTS)

    assert state.attributes["event_type"] == EVENT_TURNED_ON
    assert state.attributes["reason"] == REASON_STARTUP
    assert state.attributes["standby"] is True
    assert records == []


@pytest.mark.asyncio
async def test_a_member_back_from_unavailable_fires_nothing(hass) -> None:
    """A member reporting in again continues the on-period."""
    records = await _setup(hass, make_light_entry(name="Hall"))
    await _turn_on(hass)
    hass.states.async_set(REAL, "on", {"brightness": 255})
    await settle(hass)

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    hass.states.async_set(REAL, "on", {"brightness": 255})
    await settle(hass)

    assert records == [_turned_on(REASON_MANUAL)]


@pytest.mark.asyncio
async def test_a_second_turn_on_of_a_lit_light_fires_nothing(hass) -> None:
    """Turning on a light that is on, with no look, is not an on-period."""
    records = await _setup(hass, make_light_entry(name="Hall"))
    await _turn_on(hass)
    hass.states.async_set(REAL, "on", {"brightness": 255})
    await settle(hass)

    await _turn_on(hass)
    hass.states.async_set(REAL, "on", {"brightness": 255})
    await settle(hass)

    assert records == [_turned_on(REASON_MANUAL)]


@pytest.mark.asyncio
async def test_a_restart_with_the_lights_on_fires_nothing(hass) -> None:
    """Adopting lit lights at startup is not a turn-on."""
    entry = make_light_entry(name="Hall")
    await _setup(hass, entry)
    await _turn_on(hass, brightness=200)
    hass.states.async_set(REAL, "on", {"brightness": 200})
    await settle(hass)
    before = hass.states.get(EVENTS)

    await restart_entries(hass, entry)

    after = hass.states.get(EVENTS)
    assert after.state == before.state
    assert after.attributes["event_type"] == EVENT_TURNED_ON
    assert after.attributes["reason"] == REASON_MANUAL


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_a_follow_window_missed_during_a_restart_is_a_schedule_start(
    hass,
) -> None:
    """A window that started while Home Assistant was down lights the room."""
    mock_restore_cache(hass, [State(VIRTUAL, "off", {"schedule_window_start": None})])
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall", schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW
        ),
        binary_sensor__sched="on",
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)

    assert records == [_turned_on(REASON_SCHEDULE_START)]
