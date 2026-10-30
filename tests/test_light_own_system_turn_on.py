"""Tests for a light whose own system turns it on for occupancy.

The occupancy edge opens a wait; a turn-on from off inside it is an
occupancy turn-on that skips MoLight's gates, with the auto-on brightness
and color sent at the wait's end. With own_system_then_molight, MoLight
turns the light on itself, gated, when its own system has not by then.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_LIGHT_TIMEOUT,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_TURN_ON,
    CONF_STANDBY_BRIGHTNESS,
    EVENT_TURN_ON_BLOCKED,
    EVENT_TURNED_ON,
    ILLUMINANCE_MODE_CONTROL,
    OCCUPANCY_TURN_ON_MOLIGHT,
    OCCUPANCY_TURN_ON_OWN_SYSTEM,
    OCCUPANCY_TURN_ON_OWN_SYSTEM_THEN_MOLIGHT,
    REASON_BRIGHT,
    REASON_DOOR,
    REASON_ILLUMINANCE,
    REASON_OCCUPANCY,
    REASON_PHYSICAL,
    REASON_SCHEDULE,
    REASON_SCHEDULE_START,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
)
from tests.conftest import (
    make_light_entry,
    make_scheduled_light_entry,
    record_service_calls,
    settle,
    setup_entries,
)
from tests.events import record_events, tick
from tests.real_entities import InstantLight, RealLight, RealSelect, add_real

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

VIRTUAL = "light.hall"
EVENTS = "event.hall_events"
OCC = "binary_sensor.occ"
DOOR = "binary_sensor.door"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
MARKER = "2026-01-14T18:00:00+00:00"
OWN = [OCCUPANCY_TURN_ON_OWN_SYSTEM, OCCUPANCY_TURN_ON_OWN_SYSTEM_THEN_MOLIGHT]


def _turned_on(reason: str, **look) -> dict:
    return {
        "event_type": EVENT_TURNED_ON,
        "reason": reason,
        "standby": False,
        "brightness": None,
        "color": None,
        "selection": None,
        **look,
    }


def _turn_ons(calls: list[dict]) -> list[dict]:
    """What MoLight sent its real lights with each light.turn_on."""
    return [
        {k: v for k, v in call["service_data"].items() if k != "entity_id"}
        for call in calls
        if call["domain"] == "light"
        and call["service"] == "turn_on"
        and call["service_data"]["entity_id"] != VIRTUAL
    ]


def _own_turn_on(lamp: RealLight, brightness: int = 150) -> None:
    """The light's own system turns it on, which reaches HA as a report."""
    lamp._attr_is_on = True
    lamp._attr_brightness = brightness
    lamp.async_write_ha_state()


async def _setup(hass, entry, *lamps: RealLight, **states):
    lamps = lamps or (RealLight("real_1"),)
    await add_real(hass, *lamps)
    for entity_id, state in states.items():
        hass.states.async_set(entity_id.replace("__", "."), state)
    await setup_entries(hass, entry)
    await settle(hass)
    return record_events(hass, EVENTS), record_service_calls(hass), lamps[0]


def _hall(choice: str, **kwargs):
    return make_light_entry(
        name="Hall", occupancy=OCC, occupancy_turn_on=choice, **kwargs
    )


async def tick_past_wait(hass) -> None:
    """Move past the wait, with no turn-on by the light's own system."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await settle(hass)


async def _occupied(hass, on: bool = True, **attrs) -> None:
    hass.states.async_set(OCC, "on" if on else "off", attrs)
    await settle(hass)


def _molight_state(hass) -> str:
    return hass.states.get(VIRTUAL).attributes["molight_state"]


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", OWN)
@pytest.mark.parametrize("auto_on", [None, 60], ids=["blank", "set"])
@pytest.mark.parametrize("bright", [False, True], ids=["dark", "bright"])
async def test_a_turn_on_inside_the_wait_is_occupancy(
    hass, freezer, choice: str, auto_on: int | None, bright: bool
) -> None:
    """Matched, skipping the gates; the auto-on brightness follows at the wait's end."""
    records, calls, lamp = await _setup(
        hass,
        _hall(
            choice,
            auto_on_brightness=auto_on,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_CONTROL,
        ),
        binary_sensor__occ="off",
        binary_sensor__illum="on" if bright else "off",
    )

    await _occupied(hass)
    await tick(hass, freezer, 0.2)
    _own_turn_on(lamp)
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED
    assert state.attributes["last_on_occupancy"] is not None
    assert state.attributes["last_on_physical"] is None
    assert _turn_ons(calls) == []

    await tick(hass, freezer, 1)

    assert records == [_turned_on(REASON_OCCUPANCY, brightness=153 if auto_on else 150)]
    assert _turn_ons(calls) == ([{"brightness": 153}] if auto_on else [])
    assert _molight_state(hass) == STATE_OCCUPIED
    assert hass.states.get(VIRTUAL).attributes["brightness"] == (
        153 if auto_on else 150
    )


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("choice", OWN)
async def test_a_turn_on_inside_the_wait_skips_a_gate_window(
    hass, freezer, choice: str
) -> None:
    """Outside a gate-mode window, the light's own turn-on still counts."""
    records, calls, lamp = await _setup(
        hass,
        _hall(choice, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE),
        binary_sensor__occ="off",
        binary_sensor__sched="off",
    )

    await _occupied(hass)
    _own_turn_on(lamp)
    await settle(hass)
    await tick(hass, freezer, 1)

    assert records == [_turned_on(REASON_OCCUPANCY, brightness=150)]
    assert _molight_state(hass) == STATE_OCCUPIED
    assert _turn_ons(calls) == []


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_the_fallback_is_blocked_outside_a_gate_window(hass, freezer) -> None:
    records, calls, _lamp = await _setup(
        hass,
        _hall(
            OCCUPANCY_TURN_ON_OWN_SYSTEM_THEN_MOLIGHT,
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
        binary_sensor__occ="off",
        binary_sensor__sched="off",
    )

    await _occupied(hass)
    await tick(hass, freezer, 1)

    assert records == [
        {
            "event_type": EVENT_TURN_ON_BLOCKED,
            "trigger": REASON_OCCUPANCY,
            "reason": REASON_SCHEDULE,
        }
    ]
    assert _turn_ons(calls) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", OWN)
async def test_a_turn_on_after_the_wait_is_physical(hass, freezer, choice) -> None:
    records, calls, lamp = await _setup(
        hass,
        _hall(choice, illuminance=ILLUM, illuminance_mode=ILLUMINANCE_MODE_CONTROL),
        binary_sensor__occ="off",
        # Bright, so the fallback is blocked and the light stays off.
        binary_sensor__illum="on",
    )

    await _occupied(hass)
    await tick(hass, freezer, 1.5)
    _own_turn_on(lamp)
    await settle(hass)

    assert records[-1] == _turned_on(REASON_PHYSICAL, brightness=150)
    assert hass.states.get(VIRTUAL).attributes["last_on_occupancy"] is None
    assert _turn_ons(calls) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", OWN)
async def test_a_turn_on_before_the_edge_is_physical(hass, freezer, choice) -> None:
    records, calls, lamp = await _setup(
        hass, _hall(choice, auto_on_brightness=60), binary_sensor__occ="off"
    )

    _own_turn_on(lamp)
    await settle(hass)
    await tick(hass, freezer, 0.1)
    await _occupied(hass)
    await tick(hass, freezer, 1)

    assert records == [_turned_on(REASON_PHYSICAL, brightness=150)]
    assert _molight_state(hass) == STATE_OCCUPIED
    assert _turn_ons(calls) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", OWN)
async def test_a_turn_on_alone_is_physical(hass, freezer, choice) -> None:
    records, _calls, lamp = await _setup(hass, _hall(choice), binary_sensor__occ="off")

    _own_turn_on(lamp)
    await settle(hass)
    await tick(hass, freezer, 1)

    assert records == [_turned_on(REASON_PHYSICAL, brightness=150)]


@pytest.mark.asyncio
async def test_own_system_alone_stays_idle(hass, freezer) -> None:
    """Its own system did not turn the light on: no turn-on, no event."""
    records, calls, _lamp = await _setup(
        hass, _hall(OCCUPANCY_TURN_ON_OWN_SYSTEM), binary_sensor__occ="off"
    )

    await _occupied(hass)
    await tick(hass, freezer, 5)

    assert records == []
    assert _turn_ons(calls) == []
    assert hass.states.get(VIRTUAL).state == "off"
    assert _molight_state(hass) == STATE_IDLE


@pytest.mark.asyncio
async def test_own_system_alone_while_bright_sends_no_blocked(hass, freezer) -> None:
    records, _calls, _lamp = await _setup(
        hass,
        _hall(
            OCCUPANCY_TURN_ON_OWN_SYSTEM,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_CONTROL,
        ),
        binary_sensor__occ="off",
        binary_sensor__illum="on",
    )

    await _occupied(hass)
    await tick(hass, freezer, 5)

    assert records == []


@pytest.mark.asyncio
async def test_then_molight_turns_on_at_the_wait_end(hass, freezer) -> None:
    records, calls, _lamp = await _setup(
        hass,
        _hall(OCCUPANCY_TURN_ON_OWN_SYSTEM_THEN_MOLIGHT, auto_on_brightness=60),
        binary_sensor__occ="off",
    )

    await _occupied(hass)
    await tick(hass, freezer, 0.2)
    assert _turn_ons(calls) == []

    await tick(hass, freezer, 0.8)

    assert records == [_turned_on(REASON_OCCUPANCY, brightness=153)]
    assert _turn_ons(calls) == [{"brightness": 153}]
    assert _molight_state(hass) == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_then_molight_fallback_is_blocked_while_bright(hass, freezer) -> None:
    records, calls, _lamp = await _setup(
        hass,
        _hall(
            OCCUPANCY_TURN_ON_OWN_SYSTEM_THEN_MOLIGHT,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_CONTROL,
        ),
        binary_sensor__occ="off",
        binary_sensor__illum="on",
    )

    await _occupied(hass)
    assert records == []
    await tick(hass, freezer, 1)

    assert records == [
        {
            "event_type": EVENT_TURN_ON_BLOCKED,
            "trigger": REASON_OCCUPANCY,
            "reason": REASON_BRIGHT,
        }
    ]
    assert _turn_ons(calls) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", OWN)
async def test_a_clear_inside_the_wait_closes_it(hass, freezer, choice) -> None:
    records, calls, lamp = await _setup(hass, _hall(choice), binary_sensor__occ="off")

    await _occupied(hass)
    await tick(hass, freezer, 0.3)
    await _occupied(hass, on=False)
    await tick(hass, freezer, 0.3)
    _own_turn_on(lamp)
    await settle(hass)
    await tick(hass, freezer, 1)

    assert records == [_turned_on(REASON_PHYSICAL, brightness=150)]
    assert _turn_ons(calls) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", OWN)
async def test_the_follow_up_is_read_as_its_own_echo(hass, freezer, choice) -> None:
    """One MoLight command after the turn-on, whose reply is no physical change."""
    records, calls, lamp = await _setup(
        hass, _hall(choice, auto_on_brightness=60), binary_sensor__occ="off"
    )

    await _occupied(hass)
    _own_turn_on(lamp)
    await settle(hass)
    await tick(hass, freezer, 1)

    assert lamp.brightness == 153
    assert _turn_ons(calls) == [{"brightness": 153}]
    assert [r["event_type"] for r in records] == [EVENT_TURNED_ON]
    state = hass.states.get(VIRTUAL)
    assert state.attributes["molight_state"] == STATE_OCCUPIED
    assert state.attributes["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_the_follow_up_applies_the_turn_on_selection(hass, freezer) -> None:
    await add_real(hass, RealSelect("scene", option="Bright"))
    records, calls, lamp = await _setup(
        hass,
        _hall(
            OCCUPANCY_TURN_ON_OWN_SYSTEM,
            turn_on_select_entity="select.scene",
            turn_on_select_option="Cozy",
        ),
        binary_sensor__occ="off",
    )

    await _occupied(hass)
    _own_turn_on(lamp)
    await settle(hass)
    assert hass.states.get("select.scene").state == "Bright"

    await tick(hass, freezer, 1)

    assert hass.states.get("select.scene").state == "Cozy"
    assert records == [_turned_on(REASON_OCCUPANCY, brightness=150, selection="Cozy")]
    assert _turn_ons(calls) == [{}]


@pytest.mark.asyncio
async def test_a_clear_after_the_match_keeps_the_follow_up(hass, freezer) -> None:
    _records, calls, lamp = await _setup(
        hass,
        _hall(OCCUPANCY_TURN_ON_OWN_SYSTEM, auto_on_brightness=60),
        binary_sensor__occ="off",
    )

    await _occupied(hass)
    _own_turn_on(lamp)
    await settle(hass)
    await _occupied(hass, on=False, last_clear_false_detection=True)
    await tick(hass, freezer, 1)

    assert _turn_ons(calls) == [{"brightness": 153}]
    assert _molight_state(hass) == STATE_COUNTDOWN


@pytest.mark.asyncio
async def test_a_manual_off_inside_the_wait_drops_the_follow_up(hass, freezer) -> None:
    _records, calls, lamp = await _setup(
        hass,
        _hall(OCCUPANCY_TURN_ON_OWN_SYSTEM, auto_on_brightness=60),
        binary_sensor__occ="off",
    )

    await _occupied(hass)
    _own_turn_on(lamp)
    await settle(hass)
    lamp._attr_is_on = False
    lamp.async_write_ha_state()
    await settle(hass)
    await tick(hass, freezer, 1)

    assert _turn_ons(calls) == []
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_every_real_light_of_the_turn_on_counts_once(hass, freezer) -> None:
    """A second real light its own system turns on is part of the same turn-on."""
    second = RealLight("real_2")
    records, _calls, lamp = await _setup(
        hass,
        _hall(
            OCCUPANCY_TURN_ON_OWN_SYSTEM,
            lights=["light.real_1", "light.real_2"],
        ),
        RealLight("real_1"),
        second,
        binary_sensor__occ="off",
    )

    await _occupied(hass)
    _own_turn_on(lamp)
    await settle(hass)
    await tick(hass, freezer, 0.2)
    _own_turn_on(second, 120)
    await settle(hass)
    await tick(hass, freezer, 31)
    await _occupied(hass, on=False, last_clear_false_detection=True)

    assert records == [_turned_on(REASON_OCCUPANCY, brightness=150)]
    assert hass.states.get(VIRTUAL).attributes["last_on_physical"] is None

    await tick(hass, freezer, 6)  # the false-detection off delay

    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", OWN)
async def test_the_false_detection_off_delay_applies(hass, freezer, choice) -> None:
    _records, _calls, lamp = await _setup(
        hass, _hall(choice, false_off_delay=5), binary_sensor__occ="off"
    )

    await _occupied(hass)
    _own_turn_on(lamp)
    await settle(hass)
    await tick(hass, freezer, 31)
    await _occupied(hass, on=False, last_clear_false_detection=True)
    await tick(hass, freezer, 6)

    assert hass.states.get(VIRTUAL).state == "off"
    assert _molight_state(hass) == STATE_IDLE


@pytest.mark.asyncio
async def test_the_door_keeps_its_gate(hass, freezer) -> None:
    records, calls, _lamp = await _setup(
        hass,
        _hall(
            OCCUPANCY_TURN_ON_OWN_SYSTEM,
            door=DOOR,
            illuminance=ILLUM,
            illuminance_mode=ILLUMINANCE_MODE_CONTROL,
        ),
        binary_sensor__occ="off",
        binary_sensor__door="off",
        binary_sensor__illum="on",
    )

    hass.states.async_set(DOOR, "on")
    await settle(hass)

    assert records == [
        {
            "event_type": EVENT_TURN_ON_BLOCKED,
            "trigger": REASON_DOOR,
            "reason": REASON_BRIGHT,
        }
    ]
    assert _turn_ons(calls) == []


@pytest.mark.asyncio
async def test_a_virtual_turn_on_inside_the_wait_is_virtual(hass, freezer) -> None:
    """MoLight's own command wins; the wait then matches nothing."""
    records, calls, _lamp = await _setup(
        hass,
        _hall(OCCUPANCY_TURN_ON_OWN_SYSTEM_THEN_MOLIGHT, auto_on_brightness=60),
        binary_sensor__occ="off",
    )

    await _occupied(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
    )
    await settle(hass)
    await tick(hass, freezer, 1)

    assert [r["reason"] for r in records] == ["virtual"]
    assert _turn_ons(calls) == [{"brightness": 200}]
    assert hass.states.get(VIRTUAL).attributes["brightness"] == 200


@pytest.mark.asyncio
async def test_molight_turns_on_at_the_edge_as_before(hass, freezer) -> None:
    records, calls, _lamp = await _setup(
        hass,
        _hall(OCCUPANCY_TURN_ON_MOLIGHT, auto_on_brightness=60),
        binary_sensor__occ="off",
    )

    await _occupied(hass)

    assert records == [_turned_on(REASON_OCCUPANCY, brightness=153)]
    assert _turn_ons(calls) == [{"brightness": 153}]


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", OWN)
async def test_a_reload_inside_the_wait_forgets_it(hass, choice) -> None:
    """Presence re-read at the reload lights the room only with then MoLight."""
    records, calls, lamp = await _setup(hass, _hall(choice), binary_sensor__occ="off")
    entry = hass.config_entries.async_entries("molight")[-1]

    await _occupied(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    if choice == OCCUPANCY_TURN_ON_OWN_SYSTEM:
        assert _turn_ons(calls) == []
        _own_turn_on(lamp)
        await settle(hass)
        assert records == [_turned_on(REASON_PHYSICAL, brightness=150)]
    else:
        assert records == [_turned_on(REASON_OCCUPANCY)]
        assert len(_turn_ons(calls)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("choice", "lit"),
    [
        (OCCUPANCY_TURN_ON_OWN_SYSTEM, False),
        (OCCUPANCY_TURN_ON_OWN_SYSTEM_THEN_MOLIGHT, True),
        (OCCUPANCY_TURN_ON_MOLIGHT, True),
    ],
)
async def test_going_dark_in_an_occupied_room(hass, choice, lit) -> None:
    records, calls, _lamp = await _setup(
        hass,
        _hall(choice, illuminance=ILLUM, illuminance_mode=ILLUMINANCE_MODE_CONTROL),
        binary_sensor__occ="off",
        binary_sensor__illum="on",
    )
    await _occupied(hass)
    await tick_past_wait(hass)
    records.clear()

    hass.states.async_set(ILLUM, "off")
    await settle(hass)

    assert bool(_turn_ons(calls)) is lit
    assert records == ([_turned_on(REASON_ILLUMINANCE)] if lit else [])


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    ("choice", "lit"),
    [
        (OCCUPANCY_TURN_ON_OWN_SYSTEM, False),
        (OCCUPANCY_TURN_ON_OWN_SYSTEM_THEN_MOLIGHT, True),
        (OCCUPANCY_TURN_ON_MOLIGHT, True),
    ],
)
async def test_a_gate_window_start_in_an_occupied_room(hass, choice, lit) -> None:
    records, calls, _lamp = await _setup(
        hass,
        _hall(choice, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE),
        binary_sensor__occ="off",
        binary_sensor__sched="off",
    )
    await _occupied(hass)
    await tick_past_wait(hass)
    records.clear()

    hass.states.async_set(SCHED, "on")
    await settle(hass)

    assert bool(_turn_ons(calls)) is lit
    assert records == ([_turned_on(REASON_OCCUPANCY)] if lit else [])


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_a_follow_window_owns_the_light(hass, freezer) -> None:
    """In a follow-mode window the setting does nothing."""
    records, calls, _lamp = await _setup(
        hass,
        _hall(
            OCCUPANCY_TURN_ON_OWN_SYSTEM_THEN_MOLIGHT,
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_FOLLOW,
        ),
        binary_sensor__occ="off",
        binary_sensor__sched="off",
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)

    await _occupied(hass)
    await tick(hass, freezer, 1)

    assert records == [_turned_on(REASON_SCHEDULE_START)]
    assert len(_turn_ons(calls)) == 1


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("choice", OWN)
async def test_standby_is_raised_at_the_edge_as_before(hass, freezer, choice) -> None:
    """A light at standby is raised by MoLight on the edge, as with molight."""
    entry = make_scheduled_light_entry(
        name="Hall",
        schedule=SCHED,
        outside={CONF_LIGHT_TIMEOUT: 30},
        inside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_OCCUPANCY_ENTITY: OCC,
            CONF_OCCUPANCY_TURN_ON: choice,
            CONF_AUTO_ON_BRIGHTNESS: 100,
            CONF_STANDBY_BRIGHTNESS: 20,
        },
    )
    records, calls, _lamp = await _setup(
        hass, entry, binary_sensor__occ="off", binary_sensor__sched="off"
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)

    await _occupied(hass)

    assert records[-1] == _turned_on(REASON_OCCUPANCY, brightness=255)
    assert _turn_ons(calls)[-1] == {"brightness": 255}
    assert _molight_state(hass) == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "choice",
    [
        OCCUPANCY_TURN_ON_MOLIGHT,
        OCCUPANCY_TURN_ON_OWN_SYSTEM,
        OCCUPANCY_TURN_ON_OWN_SYSTEM_THEN_MOLIGHT,
    ],
)
async def test_a_light_found_on_and_occupied_is_adopted(hass, freezer, choice) -> None:
    """Lit while Home Assistant was down: occupancy holds it, then it turns off."""
    _records, calls, _lamp = await _setup(
        hass,
        _hall(choice),
        RealLight("real_1", on=True, brightness=150),
        binary_sensor__occ="on",
    )

    assert hass.states.get(VIRTUAL).state == "on"
    assert _molight_state(hass) == STATE_OCCUPIED
    assert _turn_ons(calls) == []

    await _occupied(hass, on=False)
    assert _molight_state(hass) == STATE_COUNTDOWN
    await tick(hass, freezer, 61)

    assert hass.states.get(VIRTUAL).state == "off"


class _ColorTempLight(InstantLight):
    """An instant light that also applies the color temperature asked for."""

    async def async_turn_on(self, **kwargs) -> None:
        if "color_temp_kelvin" in kwargs:
            self._attr_color_temp_kelvin = kwargs["color_temp_kelvin"]
        await super().async_turn_on(**kwargs)


@pytest.mark.asyncio
async def test_the_follow_up_applies_the_auto_on_color(hass, freezer) -> None:
    lamp = _ColorTempLight("real_1", kelvin=2700)
    records, calls, _lamp = await _setup(
        hass,
        _hall(OCCUPANCY_TURN_ON_OWN_SYSTEM, auto_on_color_temp=3000),
        lamp,
        binary_sensor__occ="off",
    )

    await _occupied(hass)
    _own_turn_on(lamp)
    await settle(hass)
    assert _turn_ons(calls) == []

    await tick(hass, freezer, 1)

    assert records == [
        _turned_on(REASON_OCCUPANCY, brightness=150, color={"color_temp_kelvin": 3000})
    ]
    assert _turn_ons(calls) == [{"color_temp_kelvin": 3000}]
    assert lamp.color_temp_kelvin == 3000
    state = hass.states.get(VIRTUAL)
    assert state.attributes["color_temp_kelvin"] == 3000
    assert state.attributes["last_color_change_physical"] is None
