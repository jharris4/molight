"""Tests for the turned_off event on a light's Events entity.

It fires once per on-period end, with why: a person, the timer, a false
detection's off delay, brightness, or a schedule end, including one held
by an auto-off hold or missed during a restart. A rest at standby is a
turned_off with standby set.
"""

from __future__ import annotations

import pytest
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.molight.const import (
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_LIGHT_TIMEOUT,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_STANDBY_BRIGHTNESS,
    DOMAIN,
    ENTITY_TYPE_OCCUPANCY,
    EVENT_TURNED_OFF,
    EVENT_TURNED_ON,
    ILLUMINANCE_MODE_CONTROL,
    REASON_BRIGHT,
    REASON_FALSE_DETECTION,
    REASON_OCCUPANCY,
    REASON_PHYSICAL,
    REASON_SCHEDULE_END,
    REASON_STARTUP,
    REASON_TIMER,
    REASON_VIRTUAL,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
)
from tests.conftest import (
    make_light_entry,
    make_scheduled_light_entry,
    restart_entries,
    settle,
    setup_entries,
)
from tests.events import record_events, tick

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

REAL = "light.real_1"
VIRTUAL = "light.hall"
EVENTS = "event.hall_events"
OCC = "binary_sensor.occ"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
HOLD = "input_boolean.guest"
MARKER = "2026-01-14T18:00:00+00:00"


def _turned_off(reason: str, *, standby: bool = False) -> dict:
    return {
        "event_type": EVENT_TURNED_OFF,
        "reason": reason,
        "standby": standby,
    }


async def _setup(hass, entry, **states) -> list[dict]:
    hass.states.async_set(REAL, "off")
    for entity_id, state in states.items():
        hass.states.async_set(entity_id.replace("__", "."), state)
    await setup_entries(hass, entry)
    await settle(hass)
    return record_events(hass, EVENTS)


async def _lit_virtually(hass) -> None:
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on", {"brightness": 255})
    await settle(hass)


def _offs(records: list[dict]) -> list[dict]:
    return [r for r in records if r["event_type"] == EVENT_TURNED_OFF]


@pytest.mark.asyncio
async def test_virtual_turn_off(hass) -> None:
    records = await _setup(hass, make_light_entry(name="Hall"))
    await _lit_virtually(hass)

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)

    assert _offs(records) == [_turned_off(REASON_VIRTUAL)]


@pytest.mark.asyncio
async def test_physical_turn_off(hass) -> None:
    records = await _setup(hass, make_light_entry(name="Hall"))
    await _lit_virtually(hass)

    hass.states.async_set(REAL, "off")
    await settle(hass)

    assert _offs(records) == [_turned_off(REASON_PHYSICAL)]


@pytest.mark.asyncio
async def test_timer_turn_off(hass, freezer) -> None:
    records = await _setup(hass, make_light_entry(name="Hall", timeout=10))
    await _lit_virtually(hass)

    await tick(hass, freezer, 11)

    assert _offs(records) == [_turned_off(REASON_TIMER)]


@pytest.mark.asyncio
async def test_false_detection_off_delay(hass, freezer) -> None:
    """A light a blip lit goes off after the false-detection off delay, and says so."""
    sensor = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Motion",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.pir",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    hass.states.async_set("binary_sensor.pir", "off")
    sensor.add_to_hass(hass)
    assert await hass.config_entries.async_setup(sensor.entry_id)
    records = await _setup(
        hass, make_light_entry(name="Hall", occupancy="binary_sensor.motion")
    )

    hass.states.async_set("binary_sensor.pir", "on")
    await settle(hass)
    await tick(hass, freezer, 31)
    hass.states.async_set("binary_sensor.pir", "off")
    await settle(hass)
    await tick(hass, freezer, 6)

    assert hass.states.get(VIRTUAL).state == "off"
    assert _offs(records) == [_turned_off(REASON_FALSE_DETECTION)]


@pytest.mark.asyncio
async def test_bright_turn_off(hass) -> None:
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall", illuminance=ILLUM, illuminance_mode=ILLUMINANCE_MODE_CONTROL
        ),
        binary_sensor__illum="off",
    )
    await _lit_virtually(hass)

    hass.states.async_set(ILLUM, "on")
    await settle(hass)

    assert _offs(records) == [_turned_off(REASON_BRIGHT)]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_window_end(hass) -> None:
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall", schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW
        ),
        binary_sensor__sched="off",
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)

    hass.states.async_set(SCHED, "off")
    await settle(hass)

    assert _offs(records) == [_turned_off(REASON_SCHEDULE_END)]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_gate_window_end(hass) -> None:
    """A Gate and turn off window's end is a schedule_end too."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall", occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
        ),
        binary_sensor__occ="off",
        binary_sensor__sched="on",
    )
    hass.states.async_set(OCC, "on")
    await settle(hass)

    hass.states.async_set(SCHED, "off")
    await settle(hass)

    assert _offs(records) == [_turned_off(REASON_SCHEDULE_END)]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_window_end_held_by_auto_off_hold_fires_at_release(hass) -> None:
    """The off a hold deferred fires when the hold releases, as a schedule_end."""
    records = await _setup(
        hass,
        make_light_entry(
            name="Hall",
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_FOLLOW,
            hold_entities=[HOLD],
        ),
        binary_sensor__sched="off",
        input_boolean__guest="off",
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)
    hass.states.async_set(HOLD, "on")
    await settle(hass)

    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _offs(records) == []
    hass.states.async_set(HOLD, "off")
    await settle(hass)

    assert _offs(records) == [_turned_off(REASON_SCHEDULE_END)]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_window_end_missed_during_a_restart(hass) -> None:
    """A window that ended while Home Assistant was down is applied at startup."""
    mock_restore_cache(hass, [State(VIRTUAL, "on", {"schedule_window_start": MARKER})])
    hass.states.async_set(REAL, "on")
    hass.states.async_set(SCHED, "off")
    await setup_entries(
        hass,
        make_light_entry(
            name="Hall", schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW
        ),
    )
    await settle(hass)

    assert hass.states.get(VIRTUAL).state == "off"
    state = hass.states.get(EVENTS)
    assert state.attributes["event_type"] == EVENT_TURNED_OFF
    assert state.attributes["reason"] == REASON_SCHEDULE_END


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_timer_rests_at_standby(hass, freezer) -> None:
    """A raised standby light's timeout ends at standby, not dark."""
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
            },
        ),
        binary_sensor__occ="off",
        binary_sensor__sched="on",
    )
    assert hass.states.get(EVENTS).attributes["reason"] == REASON_STARTUP
    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(OCC, "off")
    await settle(hass)

    await tick(hass, freezer, 31)

    assert [r["event_type"] for r in records] == [EVENT_TURNED_ON, EVENT_TURNED_OFF]
    assert records[0]["reason"] == REASON_OCCUPANCY
    assert records[1] == _turned_off(REASON_TIMER, standby=True)
    assert hass.states.get(VIRTUAL).attributes["brightness"] == 51


async def _unload_and_switch_off(hass, entry, state: str = "off") -> None:
    """Take the entry down and change the member, as happens while HA is down.

    The Events entity restores its last event at the restart, which the
    records list first.
    """
    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    hass.states.async_set(REAL, state, {"restored": True} if state != "off" else {})
    await settle(hass)


@pytest.mark.asyncio
async def test_a_restart_finding_the_lights_off_ends_the_on_period(hass) -> None:
    """A light switched off while Home Assistant was down is found off at the
    restart: its on-period ended, which consumers are told."""
    entry = make_light_entry(name="Hall", timeout=60)
    records = await _setup(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on", {"brightness": 200})
    await settle(hass)
    assert [r["event_type"] for r in records] == [EVENT_TURNED_ON]
    del records[:]

    await _unload_and_switch_off(hass, entry)
    await restart_entries(hass, entry)

    assert hass.states.get(VIRTUAL).state == "off"
    assert records[1:] == [_turned_off(REASON_STARTUP)]


@pytest.mark.asyncio
async def test_a_restart_mid_warning_finding_the_lights_off_ends_the_on_period(
    hass, freezer
) -> None:
    """Landing mid-warning with the lights off is the same end, not a
    cancelled warning."""
    entry = make_light_entry(
        name="Hall", timeout=60, occupancy=OCC, warn_timeout=15, warn_brightness=100
    )
    records = await _setup(hass, entry, binary_sensor__occ="off")
    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(REAL, "on", {"brightness": 255})
    await settle(hass)
    hass.states.async_set(OCC, "off")
    await settle(hass)
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == "warn"
    del records[:]

    await _unload_and_switch_off(hass, entry)
    await restart_entries(hass, entry)

    assert hass.states.get(VIRTUAL).state == "off"
    assert hass.states.get(VIRTUAL).attributes["warning_active"] is False
    assert records[1:] == [_turned_off(REASON_STARTUP)]


@pytest.mark.asyncio
@pytest.mark.parametrize("late", ["off", "on"])
async def test_an_unreadable_member_at_a_restart_settles_the_on_period(
    hass, late: str
) -> None:
    """A member unreadable at the restart proves nothing yet: its first
    report does. Off ends the on-period; on continues it."""
    entry = make_light_entry(name="Hall", timeout=60)
    records = await _setup(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on", {"brightness": 200})
    await settle(hass)
    del records[:]
    await _unload_and_switch_off(hass, entry, "unavailable")
    await restart_entries(hass, entry)
    assert hass.states.get(VIRTUAL).state == "off"
    assert records[1:] == []

    hass.states.async_set(REAL, late, {"brightness": 200} if late == "on" else {})
    await settle(hass)

    assert hass.states.get(VIRTUAL).state == late
    if late == "off":
        assert records[1:] == [_turned_off(REASON_STARTUP)]
    else:
        assert EVENT_TURNED_OFF not in [r["event_type"] for r in records]


@pytest.mark.asyncio
async def test_a_restart_finding_the_lights_off_at_standby_rests_them_again(
    hass,
) -> None:
    """A light resting at standby that was switched off while Home Assistant
    was down: the rest ended, and the startup rests the lights again."""
    entry = make_scheduled_light_entry(
        name="Hall",
        schedule=SCHED,
        inside={CONF_LIGHT_TIMEOUT: 60, CONF_STANDBY_BRIGHTNESS: 20},
    )
    records = await _setup(hass, entry, binary_sensor__sched="on")
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == "standby"
    hass.states.async_set(REAL, "on", {"brightness": 51})
    await settle(hass)
    del records[:]

    await _unload_and_switch_off(hass, entry)
    await restart_entries(hass, entry)

    assert hass.states.get(VIRTUAL).attributes["molight_state"] == "standby"
    assert [(r["event_type"], r["reason"], r["standby"]) for r in records[1:]] == [
        (EVENT_TURNED_OFF, REASON_STARTUP, False),
        (EVENT_TURNED_ON, REASON_STARTUP, True),
    ]
