"""Unavailability tests for the MoLight Virtual Light.

Each entity the light watches (real lights, occupancy, illuminance,
schedule) can drop to unavailable/unknown at any time, e.g. a Zigbee
device falling off the mesh or an integration reloading. Those blips must
never be read as state changes, and recovery transitions must behave like
fresh events.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.const import (
    ATTR_RESTORED,
    EVENT_CALL_SERVICE,
    EVENT_HOMEASSISTANT_STARTED,
)
from homeassistant.core import CoreState, State, callback
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_ENTITY,
    CONF_LIGHT_TIMEOUT,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    DOOR_MODE_OPEN_CLOSE,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_OCCUPANCY,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_KEEP,
    SCHEDULE_MODE_GATE_SWITCH,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
    STATE_WARN,
)
from tests.conftest import (
    make_light_entry,
    make_scheduled_light_entry,
    settle,
    setup_entries,
)

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

OCC = "binary_sensor.occ"
DOOR = "binary_sensor.door"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
REAL = "light.real_1"
REAL2 = "light.real_2"
VIRTUAL = "light.matrix_light"
HOLD = "input_boolean.hold"
MARKER = "2026-07-02T21:00:00+00:00"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


def _record_calls(hass: HomeAssistant) -> list[dict]:
    calls: list[dict] = []

    @callback
    def _record(event) -> None:
        calls.append(event.data)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return calls


def _real_calls(calls: list[dict], service: str) -> list[dict]:
    return [
        d["service_data"]
        for d in calls
        if d["domain"] == "light"
        and d["service"] == service
        and REAL in d["service_data"].get("entity_id", [])
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["unavailable", "unknown"])
async def test_real_light_blip_and_recovery(hass: HomeAssistant, bad: str) -> None:
    """A real light dropping out is not an 'off'; coming back 'on' re-adopts it."""
    await setup_entries(hass, make_light_entry())

    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, bad)
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE

    # Recovery to on is treated like an external turn-on (still ACTIVE).
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # A real off afterwards releases the light as usual.
    hass.states.async_set(REAL, "off")
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_real_light_recovery_keeps_running_countdown(
    hass: HomeAssistant, freezer
) -> None:
    """A member blip/recovery mid-countdown must not win a fresh full timer.

    Regression: unavailable → on used to be handled as an external turn-on,
    replacing an occupancy-anchored countdown with a full ACTIVE timer: a
    bulb that blips off the mesh every few minutes never turned off.
    """
    await setup_entries(hass, make_light_entry(occupancy=OCC, timeout=60))

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # 30s into the 60s countdown the bulb drops off the mesh and rejoins.
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    # The original countdown completes on schedule: 31s later, not 60s.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_real_light_recovery_mirrors_brightness(hass: HomeAssistant) -> None:
    """The recovery adoption still mirrors the member's brightness."""
    await setup_entries(hass, make_light_entry())

    hass.states.async_set(REAL, "on", {"brightness": 200})
    await settle(hass)
    assert _state(hass).attributes["brightness"] == 200

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    hass.states.async_set(REAL, "on", {"brightness": 120})
    await settle(hass)
    state = _state(hass)
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 120


@pytest.mark.asyncio
async def test_real_light_recovery_mirrors_color(hass: HomeAssistant) -> None:
    """A recovered member's color is mirrored without restarting its timer."""
    original = {
        "supported_color_modes": ["hs"],
        "color_mode": "hs",
        "hs_color": [30, 40],
        "brightness": 200,
    }
    recovered = {
        **original,
        "hs_color": [120, 60],
        "brightness": 120,
    }
    await setup_entries(hass, make_light_entry())

    hass.states.async_set(REAL, "on", original)
    await settle(hass)
    assert tuple(_state(hass).attributes["hs_color"]) == (30.0, 40.0)

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    hass.states.async_set(REAL, "on", recovered)
    await settle(hass)
    state = _state(hass)
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 120
    assert tuple(state.attributes["hs_color"]) == (120.0, 60.0)


_NOT_LOADED = [None, "unavailable", "unknown", "restored"]
_LEVEL = {"occupancy": 102, "manual": 200, "door": 102}


async def _light_up(
    hass: HomeAssistant, trigger: str, member: str | None, **kwargs
) -> list[dict]:
    """Light the room by trigger while the real light has not reported yet."""
    hass.states.async_set(OCC, "off")
    hass.states.async_set(DOOR, "off")
    if member == "restored":
        hass.states.async_set(REAL, "unavailable", {ATTR_RESTORED: True})
    elif member:
        hass.states.async_set(REAL, member)
    await setup_entries(
        hass,
        make_light_entry(occupancy=OCC, door=DOOR, auto_on_brightness=40, **kwargs),
    )
    await settle(hass)
    calls = _record_calls(hass)
    if trigger == "manual":
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
        )
    else:
        hass.states.async_set(OCC if trigger == "occupancy" else DOOR, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["occupancy", "manual", "door"])
@pytest.mark.parametrize("member", _NOT_LOADED)
@pytest.mark.parametrize("report", [{}, {"brightness": 0}], ids=["off", "on_at_0"])
async def test_real_light_reporting_in_off_while_on_is_sent_its_settings(
    hass: HomeAssistant, freezer, trigger: str, member: str | None, report: dict
) -> None:
    """A real light that loads, or comes back, as off while the light is on
    missed its command: it is sent the light's settings again, nothing is
    recorded as a manual off, and the running timer carries on."""
    calls = await _light_up(hass, trigger, member)
    calls.clear()
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)

    hass.states.async_set(REAL, "on" if report else "off", report)
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == (
        STATE_OCCUPIED if trigger == "occupancy" else STATE_ACTIVE
    )
    assert state.attributes["last_off_manual"] is None
    assert [c["brightness"] for c in _real_calls(calls, "turn_on")] == [_LEVEL[trigger]]
    if trigger == "occupancy":
        return
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("member", _NOT_LOADED)
async def test_real_light_reporting_in_off_while_off_sends_nothing(
    hass: HomeAssistant, member: str | None
) -> None:
    """A real light reporting in as off while the light is off changes nothing."""
    hass.states.async_set(OCC, "off")
    if member == "restored":
        hass.states.async_set(REAL, "unavailable", {ATTR_RESTORED: True})
    elif member:
        hass.states.async_set(REAL, member)
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    await settle(hass)
    calls = _record_calls(hass)

    hass.states.async_set(REAL, "off")
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["last_off_manual"] is None
    assert [c for c in calls if c["domain"] == "light"] == []


@pytest.mark.asyncio
async def test_one_of_two_real_lights_reporting_in_off_is_relit(
    hass: HomeAssistant,
) -> None:
    """A member rebooting dark beside one still on gets the settings again."""
    calls = _record_calls(hass)
    await setup_entries(hass, make_light_entry(lights=[REAL, REAL2]))
    hass.states.async_set(REAL, "on", {"brightness": 150})
    hass.states.async_set(REAL2, "on", {"brightness": 150})
    await settle(hass)

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    calls.clear()
    hass.states.async_set(REAL, "off")
    await settle(hass)

    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE
    on_calls = _real_calls(calls, "turn_on")
    assert len(on_calls) == 1
    assert set(on_calls[0]["entity_id"]) == {REAL, REAL2}
    assert on_calls[0]["brightness"] == 150


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("effect_brightness", "sent"), [(30, 76), (0, None)], ids=["dim", "blink_off"]
)
async def test_real_light_reporting_in_off_mid_warning_gets_the_stage(
    hass: HomeAssistant, freezer, effect_brightness: int, sent: int | None
) -> None:
    """Mid-warning the stage's settings are sent again, and the warning runs
    on; a stage that blinks the lights off has nothing to send."""
    calls = await _light_up(
        hass,
        "manual",
        "off",
        effect_timeout=20,
        effect_brightness=effect_brightness,
        warn_timeout=20,
    )
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_EFFECT
    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    calls.clear()

    hass.states.async_set(REAL, "off")
    await settle(hass)

    assert _state(hass).attributes["molight_state"] == STATE_EFFECT
    assert _state(hass).attributes["last_off_manual"] is None
    assert [c["brightness"] for c in _real_calls(calls, "turn_on")] == (
        [sent] if sent else []
    )
    freezer.tick(timedelta(seconds=20))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_WARN


@pytest.mark.asyncio
async def test_occupancy_blip_keeps_occupied(hass: HomeAssistant, freezer) -> None:
    """Occupancy going unavailable must not start the countdown."""
    await setup_entries(hass, make_light_entry(occupancy=OCC))

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # No timer was started by the blip.
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    # Recovery straight to off starts the countdown as a real clear.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_occupancy_recovers_directly_to_on(hass: HomeAssistant) -> None:
    """unavailable → on is a real occupancy trigger."""
    hass.states.async_set(OCC, "unavailable")
    await setup_entries(hass, make_light_entry(occupancy=OCC))

    hass.states.async_set(OCC, "on")
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["unavailable", "unknown"])
async def test_occupancy_blip_respects_manual_off(
    hass: HomeAssistant, bad: str
) -> None:
    """on → outage → on is no new occupancy: a manual off stands."""
    hass.states.async_set(OCC, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)

    calls = _record_calls(hass)
    hass.states.async_set(OCC, bad)
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)

    assert _real_calls(calls, "turn_on") == []
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    # A real clear and return is a new occupancy again.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_occupancy_seen_on_at_startup_blip_respects_manual_off(
    hass: HomeAssistant,
) -> None:
    """The value read at startup counts as known, not only observed edges."""
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    await settle(hass)
    assert _state(hass).state == "on"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_occupancy_outage_spanning_a_clear_is_a_new_occupancy(
    hass: HomeAssistant,
) -> None:
    """off → outage → on differs from the last known value, so it triggers."""
    hass.states.async_set(OCC, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC))

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_occupancy_recovery_adopts_light_turned_on_during_outage(
    hass: HomeAssistant, freezer
) -> None:
    """A light lit by hand during the outage is held once occupancy is back."""
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE
    last_on_occupancy = _state(hass).attributes["last_on_occupancy"]

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    # Adopted, not lit by occupancy.
    assert _state(hass).attributes["last_on_occupancy"] == last_on_occupancy

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_occupancy_recovery_applies_dark_that_fell_during_outage(
    hass: HomeAssistant, freezer
) -> None:
    """Illuminance going dark during the outage read the sensor as clear; the
    recovery to still-occupied applies that start instead of passing as a replay."""
    hass.states.async_set(ILLUM, "on")
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    await settle(hass)
    assert _state(hass).state == "off"

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).state == "off"

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    "mode",
    [SCHEDULE_MODE_GATE, SCHEDULE_MODE_GATE_SWITCH, SCHEDULE_MODE_GATE_KEEP],
)
async def test_occupancy_recovery_applies_a_window_started_during_outage(
    hass: HomeAssistant, freezer, mode: str
) -> None:
    """A gate window starting during the outage read the sensor as clear."""
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(occupancy=OCC, schedule=SCHED, schedule_mode=mode)
    )
    await settle(hass)
    assert _state(hass).state == "off"

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).state == "off"

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_occupancy_recovery_applies_a_profile_switch_during_outage(
    hass: HomeAssistant, freezer
) -> None:
    """A scheduled light switching to a profile without the bright gate while
    occupancy is unavailable lights the room once the sensor is back."""
    hass.states.async_set("binary_sensor.settings_schedule", "off")
    hass.states.async_set(ILLUM, "on")
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_scheduled_light_entry(
            outside={
                CONF_LIGHT_TIMEOUT: 60,
                CONF_OCCUPANCY_ENTITY: OCC,
                CONF_ILLUMINANCE_ENTITY: ILLUM,
            },
            inside={CONF_LIGHT_TIMEOUT: 60, CONF_OCCUPANCY_ENTITY: OCC},
        ),
    )
    await settle(hass)
    assert hass.states.get("light.scheduled_light").state == "off"

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set("binary_sensor.settings_schedule", "on")
    await settle(hass)
    assert hass.states.get("light.scheduled_light").state == "off"

    hass.states.async_set(OCC, "on")
    await settle(hass)
    state = hass.states.get("light.scheduled_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_occupancy_blip_after_a_gate_lifted_earlier_keeps_manual_off(
    hass: HomeAssistant, freezer
) -> None:
    """A gate that lifted before the outage is not applied on recovery."""
    hass.states.async_set(ILLUM, "on")
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).state == "on"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)

    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


async def _turn_off_by(hass: HomeAssistant, off_via: str) -> None:
    if off_via == "virtual":
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
        )
    hass.states.async_set(REAL, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("off_via", ["virtual", "physical"])
async def test_occupancy_recovery_respects_a_manual_off_after_the_gate_lifted(
    hass: HomeAssistant, freezer, off_via: str
) -> None:
    """A light turned off by hand after the gate lifted stays off on recovery."""
    hass.states.async_set(ILLUM, "on")
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    await settle(hass)
    assert _state(hass).state == "off"

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    assert _state(hass).state == "off"

    freezer.tick(timedelta(seconds=5))
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    await _turn_off_by(hass, off_via)

    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_occupancy_recovery_applies_a_gate_lifted_after_a_manual_off(
    hass: HomeAssistant, freezer
) -> None:
    """A manual off before the gate lifted does not stand in for the start."""
    hass.states.async_set(ILLUM, "on")
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    await settle(hass)

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    await _turn_off_by(hass, "virtual")

    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(ILLUM, "off")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_occupancy_blip_keeps_occupied_light_untouched(
    hass: HomeAssistant,
) -> None:
    """An occupied light sees no command and no new attribution on recovery."""
    hass.states.async_set(OCC, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    before = _state(hass)

    calls = _record_calls(hass)
    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)

    assert _real_calls(calls, "turn_on") == []
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    assert (
        _state(hass).attributes["last_on_occupancy"]
        == before.attributes["last_on_occupancy"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("manual_off", [False, True])
async def test_occupancy_sensor_reload_changes_nothing(
    hass: HomeAssistant, manual_off: bool
) -> None:
    """Editing a Virtual Occupancy Sensor's options reloads it; no light reacts."""
    hass.states.async_set("binary_sensor.motion_1", "off")
    hass.states.async_set(REAL, "off")
    occupancy = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Chain Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
    )
    light = make_light_entry(occupancy="binary_sensor.chain_occupancy")
    await setup_entries(hass, occupancy, light)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    if manual_off:
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
        )
        hass.states.async_set(REAL, "off")
        await settle(hass)
    before = (_state(hass).state, _state(hass).attributes["molight_state"])

    calls = _record_calls(hass)
    assert await hass.config_entries.async_reload(occupancy.entry_id)
    await settle(hass)

    assert hass.states.get("binary_sensor.chain_occupancy").state == "on"
    assert (_state(hass).state, _state(hass).attributes["molight_state"]) == before
    assert _real_calls(calls, "turn_on") == []
    assert _real_calls(calls, "turn_off") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", ["trigger", "maintain"])
@pytest.mark.parametrize("manual_off", [False, True])
async def test_combined_occupancy_sensor_reload_changes_nothing(
    hass: HomeAssistant, holder: str, manual_off: bool
) -> None:
    """Reloading the combined sensor a light uses does not re-light the room."""
    trigger, maintain = "binary_sensor.trig", "binary_sensor.maint"
    hass.states.async_set(trigger, "off")
    hass.states.async_set(maintain, "off")
    hass.states.async_set(REAL, "off")
    room = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Room",
            CONF_TRIGGER_SENSORS: [trigger],
            CONF_MAINTAIN_SENSORS: [maintain],
        },
    )
    await setup_entries(hass, room, make_light_entry(occupancy="binary_sensor.room"))
    hass.states.async_set(maintain, "on")
    hass.states.async_set(trigger, "on")
    await settle(hass)
    hass.states.async_set(REAL, "on")
    hass.states.async_set(trigger if holder == "maintain" else maintain, "off")
    await settle(hass)
    assert hass.states.get("binary_sensor.room").state == "on"
    if manual_off:
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
        )
        hass.states.async_set(REAL, "off")
        await settle(hass)
    before = (_state(hass).state, _state(hass).attributes["molight_state"])

    calls = _record_calls(hass)
    assert await hass.config_entries.async_reload(room.entry_id)
    await settle(hass)

    assert hass.states.get("binary_sensor.room").state == "on"
    assert (_state(hass).state, _state(hass).attributes["molight_state"]) == before
    assert _real_calls(calls, "turn_on") == []
    assert _real_calls(calls, "turn_off") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", ["trigger", "maintain"])
@pytest.mark.parametrize("manual_off", [False, True])
async def test_combined_occupancy_constituent_reload_changes_nothing(
    hass: HomeAssistant, holder: str, manual_off: bool
) -> None:
    """Reloading the constituent that holds the room occupied moves no light."""
    hass.states.async_set("binary_sensor.motion_t", "off")
    hass.states.async_set("binary_sensor.motion_m", "off")
    hass.states.async_set(REAL, "off")
    constituents = {
        role: MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
                CONF_NAME: name,
                CONF_OCCUPANCY_SENSOR: source,
                CONF_OCCUPANCY_TIMEOUT: 45,
            },
        )
        for role, name, source in (
            ("trigger", "Trig", "binary_sensor.motion_t"),
            ("maintain", "Maint", "binary_sensor.motion_m"),
        )
    }
    room = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Room",
            CONF_TRIGGER_SENSORS: ["binary_sensor.trig"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.maint"],
        },
    )
    await setup_entries(
        hass,
        *constituents.values(),
        room,
        make_light_entry(occupancy="binary_sensor.room"),
    )
    hass.states.async_set("binary_sensor.motion_m", "on")
    hass.states.async_set("binary_sensor.motion_t", "on")
    await settle(hass)
    hass.states.async_set(REAL, "on")
    hass.states.async_set(
        "binary_sensor.motion_t" if holder == "maintain" else "binary_sensor.motion_m",
        "off",
    )
    await settle(hass)
    if manual_off:
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
        )
        hass.states.async_set(REAL, "off")
        await settle(hass)
    before = (_state(hass).state, _state(hass).attributes["molight_state"])

    calls = _record_calls(hass)
    assert await hass.config_entries.async_reload(constituents[holder].entry_id)
    await settle(hass)

    assert hass.states.get("binary_sensor.room").state == "on"
    assert (_state(hass).state, _state(hass).attributes["molight_state"]) == before
    assert _real_calls(calls, "turn_on") == []
    assert _real_calls(calls, "turn_off") == []


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("same_sensor", [True, False])
async def test_occupancy_outage_across_a_settings_switch(
    hass: HomeAssistant, same_sensor: bool
) -> None:
    """A profile switch re-evaluates standing occupancy, so a sensor unreadable
    at the switch lights the room on its return: a shared sensor's return is a
    replay that applies the switch it missed, and the other side's sensor is
    seen for the first time."""
    settings_schedule = "binary_sensor.settings_schedule"
    scheduled = "light.scheduled_light"
    other = "binary_sensor.other_occ"
    hass.states.async_set(settings_schedule, "off")
    hass.states.async_set(OCC, "off")
    hass.states.async_set(other, "unavailable")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_scheduled_light_entry(
            schedule=settings_schedule,
            outside={CONF_LIGHT_TIMEOUT: 60, CONF_OCCUPANCY_ENTITY: OCC},
            inside={
                CONF_LIGHT_TIMEOUT: 60,
                CONF_OCCUPANCY_ENTITY: OCC if same_sensor else other,
            },
        ),
    )
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert hass.states.get(scheduled).state == "on"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": scheduled}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)

    hass.states.async_set(OCC, "unavailable")
    await settle(hass)
    hass.states.async_set(settings_schedule, "on")
    await settle(hass)
    assert hass.states.get(scheduled).state == "off"

    hass.states.async_set(OCC if same_sensor else other, "on")
    await settle(hass)
    assert hass.states.get(scheduled).state == "on"


@pytest.mark.asyncio
async def test_illuminance_blip_holds_state(hass: HomeAssistant) -> None:
    """Illuminance going unavailable changes nothing; recovery acts on the value."""
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set(ILLUM, "unavailable")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED

    # Recovery straight to bright forces the lights off (control mode).
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_schedule_blip_keeps_window(hass: HomeAssistant) -> None:
    """A follow-mode schedule dropping out mid-window keeps the lights on."""
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED

    # Recovery straight to off applies the window-end boundary.
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_schedule_blip_respects_manual_off(hass: HomeAssistant) -> None:
    """A schedule blip mid-window must not re-light a manually turned-off light.

    The window was already applied; recovering to 'on' with the same
    current_window_start is not a new window start, so a manual off in
    between stands (mirroring the restart-seed behavior).
    """
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    # Manual off mid-window: respected.
    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).state == "off"

    # The schedule entity blips and recovers inside the same window.
    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    "mode",
    [
        SCHEDULE_MODE_FOLLOW,
        SCHEDULE_MODE_GATE,
        SCHEDULE_MODE_GATE_SWITCH,
        SCHEDULE_MODE_GATE_KEEP,
    ],
)
@pytest.mark.parametrize("bad", ["unavailable", "unknown"])
async def test_schedule_blip_outside_window_keeps_manual_light(
    hass: HomeAssistant, freezer, mode: str, bad: str
) -> None:
    """Outside any window, off → outage → off is not a window end."""
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(schedule=SCHED, schedule_mode=mode))

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    calls = _record_calls(hass)
    hass.states.async_set(SCHED, bad)
    await settle(hass)
    hass.states.async_set(SCHED, "off")
    await settle(hass)

    assert _real_calls(calls, "turn_off") == []
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # The timer of the manual turn-on was not restarted either.
    freezer.tick(timedelta(seconds=21))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("bad", ["unavailable", "unknown"])
async def test_schedule_without_marker_blip_respects_manual_off(
    hass: HomeAssistant, bad: str
) -> None:
    """A schedule publishing no window marker: on → outage → on is no start."""
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)

    calls = _record_calls(hass)
    hass.states.async_set(SCHED, bad)
    await settle(hass)
    hass.states.async_set(SCHED, "on")
    await settle(hass)

    assert _real_calls(calls, "turn_on") == []
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_schedule_without_marker_recovery_readopts_relit_light(
    hass: HomeAssistant, freezer
) -> None:
    """A light turned on during the outage is owned by the window again."""
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)

    hass.states.async_set(REAL, "off")
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_schedule_outage_spanning_a_window_end_applies_it(
    hass: HomeAssistant,
) -> None:
    """A new window's marker after the outage is a start, not a replay."""
    later = "2026-07-03T21:00:00+00:00"
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)

    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": later})
    await settle(hass)

    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED
    assert _state(hass).attributes["schedule_window_start"] == later


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    "mode",
    [SCHEDULE_MODE_GATE, SCHEDULE_MODE_GATE_SWITCH, SCHEDULE_MODE_GATE_KEEP],
)
@pytest.mark.parametrize("bad", ["unavailable", "unknown"])
async def test_gate_blip_inside_window_respects_manual_off(
    hass: HomeAssistant, mode: str, bad: str
) -> None:
    """Inside the window, on → outage → on must not re-light an occupied room."""
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(OCC, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(occupancy=OCC, schedule=SCHED, schedule_mode=mode)
    )
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)

    calls = _record_calls(hass)
    hass.states.async_set(SCHED, bad)
    await settle(hass)
    hass.states.async_set(SCHED, "on")
    await settle(hass)

    assert _real_calls(calls, "turn_on") == []
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    "mode",
    [SCHEDULE_MODE_GATE, SCHEDULE_MODE_GATE_SWITCH, SCHEDULE_MODE_GATE_KEEP],
)
@pytest.mark.parametrize("trigger", ["occupancy", "door"])
async def test_gate_recovery_applies_a_start_the_outage_blocked(
    hass: HomeAssistant, freezer, mode: str, trigger: str
) -> None:
    """Occupancy or a door opening during the outage lights the room after it."""
    door = "binary_sensor.door"
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(OCC, "off")
    hass.states.async_set(door, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC,
            door=door,
            door_mode=DOOR_MODE_OPEN_CLOSE,
            schedule=SCHED,
            schedule_mode=mode,
        ),
    )

    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(OCC if trigger == "occupancy" else door, "on")
    await settle(hass)
    assert _state(hass).state == "off"

    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("trigger", ["occupancy", "door"])
async def test_gate_recovery_respects_a_manual_off_after_the_blocked_start(
    hass: HomeAssistant, freezer, trigger: str
) -> None:
    """A light turned off by hand after the blocked start stays off."""
    door = "binary_sensor.door"
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(OCC, "off")
    hass.states.async_set(door, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC,
            door=door,
            door_mode=DOOR_MODE_OPEN_CLOSE,
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
    )

    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(OCC if trigger == "occupancy" else door, "on")
    await settle(hass)
    assert _state(hass).state == "off"

    freezer.tick(timedelta(seconds=5))
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    await _turn_off_by(hass, "virtual")

    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_gate_blip_with_door_standing_open_respects_manual_off(
    hass: HomeAssistant,
) -> None:
    """A door open since before the outage does not re-light the room."""
    door = "binary_sensor.door"
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(door, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_light_entry(
            door=door,
            door_mode=DOOR_MODE_OPEN_CLOSE,
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
    )
    hass.states.async_set(door, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)

    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_gate_recovery_adopts_light_turned_on_during_outage(
    hass: HomeAssistant, freezer
) -> None:
    """A light lit by hand during the outage is held by occupancy after it."""
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
        ),
    )
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)

    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(SCHED, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_schedule_outage_is_not_end_boundary_on_hold_release(
    hass: HomeAssistant,
) -> None:
    """Releasing an auto-off hold during an outage keeps the applied window."""
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(HOLD, "on")
    await setup_entries(
        hass,
        make_light_entry(
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_FOLLOW,
            hold_entities=[HOLD],
        ),
    )
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    hass.states.async_set(SCHED, "unavailable")
    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_manual_off_during_outage_forgets_a_window_that_ended_under_hold(
    hass: HomeAssistant,
) -> None:
    """A held window that ended is forgotten once the light is off, even when
    the off came while the schedule was unavailable: a later manual on is not
    turned off when the hold releases."""
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(HOLD, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_light_entry(
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_FOLLOW,
            hold_entities=[HOLD],
        ),
    )
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["schedule_window_start"] == MARKER

    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).attributes["schedule_window_start"] is None

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_hard_gate_outage_does_not_force_off_on_hold_release(
    hass: HomeAssistant,
) -> None:
    """An unavailable hard gate blocks new starts without forcing an on light off."""
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(HOLD, "on")
    await setup_entries(
        hass,
        make_light_entry(
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_GATE,
            hold_entities=[HOLD],
        ),
    )
    hass.states.async_set(REAL, "on")
    await settle(hass)

    hass.states.async_set(SCHED, "unavailable")
    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    "mode",
    [SCHEDULE_MODE_GATE, SCHEDULE_MODE_GATE_SWITCH, SCHEDULE_MODE_GATE_KEEP],
)
async def test_gate_outage_preserves_on_light_but_blocks_new_activation(
    hass: HomeAssistant, mode: str
) -> None:
    """Every gate mode fails closed without treating an outage as a boundary."""
    hass.states.async_set(SCHED, "on")
    hass.states.async_set(OCC, "off")
    await setup_entries(
        hass,
        make_light_entry(occupancy=OCC, schedule=SCHED, schedule_mode=mode),
    )

    # An already-on light is left in its running state when the gate vanishes.
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE
    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # Once off, the unreadable gate blocks a fresh occupancy activation.
    hass.states.async_set(REAL, "off")
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_source_dropout_releases_light_via_virtual_occupancy(
    hass: HomeAssistant, freezer
) -> None:
    """End-to-end: a dead motion sensor no longer holds the lights on forever.

    The virtual occupancy sensor clears itself after its clear-on-unavailable
    timeout (default 60s), and because the clear is not flagged as a false
    detection, the light runs its normal countdown (anchored to the dropout
    moment) instead of the quick-off.
    """
    occupancy = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Chain Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
    )
    light = make_light_entry(occupancy="binary_sensor.chain_occupancy", timeout=300)
    await setup_entries(hass, occupancy, light)

    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # The motion sensor falls off the network: nothing happens yet.
    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    # 61s later the virtual occupancy clears itself; the light starts the
    # normal countdown: light_timeout (300s) anchored to the dropout, so
    # ~239s remain.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_COUNTDOWN

    # Not the 5s quick-off, and not off yet halfway through the countdown.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_unavailable_real_light_counts_as_off(hass: HomeAssistant) -> None:
    """With one light unavailable, the last real off releases the virtual light.

    Pins current behavior: _all_lights_off treats an unavailable light as off,
    so a dead bulb can't hold the virtual light on forever.
    """
    await setup_entries(hass, make_light_entry(lights=[REAL, REAL2]))

    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(REAL2, "on")
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL2, "unavailable")
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_registered_light_with_no_state_never_counts_as_off(
    hass: HomeAssistant,
) -> None:
    """A registered member that has not reported a state is not assumed off."""
    registered = er.async_get(hass).async_get_or_create(
        "light", "test", "real2-unique", suggested_object_id="real_2"
    )
    assert registered.entity_id == REAL2
    await setup_entries(hass, make_light_entry(lights=[REAL, REAL2]))

    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).state == "on"

    # REAL2 is registered but has no state: its integration has not loaded
    # (yet); turning REAL off must not release the virtual light, since REAL2
    # may well still be burning.
    hass.states.async_set(REAL, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
async def test_deleted_member_light_counts_as_off(hass: HomeAssistant) -> None:
    """A member gone from HA entirely no longer pins the virtual light on.

    A deleted entity loses both its state and its registry entry, and can
    never report again; treating it as "maybe still on" would keep the
    virtual light from ever reaching IDLE on external offs.
    """
    await setup_entries(hass, make_light_entry(lights=[REAL, REAL2]))

    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(REAL2, "on")
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_remove(REAL2)
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_schedule_recovery_readopts_externally_relit_light(
    hass: HomeAssistant, freezer
) -> None:
    """Recovering mid-window re-adopts a light that was cycled during the blip.

    While the schedule was unavailable the real light was switched off and
    back on, leaving the machine ACTIVE with a timer. Recovery to the same
    window re-enters SCHEDULED and cancels that timer, so the light stays on
    until the window ends.
    """
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    hass.states.async_set(REAL, "on")  # the real light confirms the turn-on
    await settle(hass)
    hass.states.async_set(SCHED, "unavailable")
    await settle(hass)

    # The light is cycled externally during the blip: off, then on again.
    hass.states.async_set(REAL, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_IDLE
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # Recovery inside the same window re-adopts it: SCHEDULED, timer gone.
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_illuminance_recovery_to_same_bright_keeps_manual_light(
    hass: HomeAssistant,
) -> None:
    """A bright sensor blipping unavailable must not replay the bright edge.

    A manual turn-on while steadily bright stands (control mode only forces
    off on the bright edge); the sensor recovering to the same value is not
    that edge. The sensor holds through its source's outages, so its blip is
    the placeholder of its entry reloading.
    """
    illum = "binary_sensor.illum"
    hass.states.async_set(illum, "on")  # bright
    entry = make_light_entry(illuminance=illum)
    await setup_entries(hass, entry)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"

    hass.states.async_set(illum, "unavailable", {ATTR_RESTORED: True})
    await settle(hass)
    hass.states.async_set(illum, "on")
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"


# ---------------------------------------------------------------------------
# Follow mode: a member back from unavailable is reconciled with the schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("booted", ["off", "on"])
async def test_follow_member_recovery_mid_window_reasserts_window(
    hass: HomeAssistant, booted: str
) -> None:
    """A member rebooting mid-window is re-lit with the scheduled settings.

    Whatever it boots into (dark, or lit at its own defaults) is not human
    activity: the window still owns the lights, so the auto-on brightness is
    re-sent and the machine stays SCHEDULED.
    """
    calls = _record_calls(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(
        hass,
        make_light_entry(
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_FOLLOW,
            auto_on_brightness=40,  # 40% → 102 of 255
        ),
    )
    await settle(hass)
    hass.states.async_set(REAL, "on", {"brightness": 102})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    calls.clear()
    hass.states.async_set(REAL, booted, {"brightness": 255} if booted == "on" else {})
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["brightness"] == 102
    assert [c["brightness"] for c in _real_calls(calls, "turn_on")] == [102]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_member_recovers_lit_outside_window_is_turned_off(
    hass: HomeAssistant, freezer
) -> None:
    """A member booting lit outside the window is turned straight off.

    Regression: unavailable → on used to be read as a physical turn-on and ran
    a full timer, so a strip that turns its LEDs on at boot lit the room until
    the timeout (or someone) turned it off.
    """
    calls = _record_calls(hass)
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await settle(hass)

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    calls.clear()
    hass.states.async_set(REAL, "on")
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert len(_real_calls(calls, "turn_off")) == 1
    assert _real_calls(calls, "turn_on") == []


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_member_recovers_dark_outside_window_sends_nothing(
    hass: HomeAssistant,
) -> None:
    """A member booting dark outside the window already matches: no command."""
    calls = _record_calls(hass)
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await settle(hass)

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    calls.clear()
    hass.states.async_set(REAL, "off")
    await settle(hass)

    assert _state(hass).state == "off"
    assert _real_calls(calls, "turn_on") == []
    assert _real_calls(calls, "turn_off") == []


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_manual_on_outside_window_is_lost_on_reboot(
    hass: HomeAssistant,
) -> None:
    """A manual turn-on outside the window does not survive a member reboot.

    The reboot edge cannot be told apart from a boot-lit strip, so the
    schedule wins; a manual turn-on while the member stays connected is an
    ordinary off → on edge and is untouched.
    """
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_manual_off_mid_window_is_lost_on_reboot(
    hass: HomeAssistant,
) -> None:
    """A manual off mid-window does not survive a member reboot either.

    Contrast test_schedule_blip_respects_manual_off: there the *schedule*
    blipped and the member's off was observed directly, so it stands.
    """
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    hass.states.async_set(REAL, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    hass.states.async_set(REAL, "off")
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED


async def _start_with_member_loading(
    hass: HomeAssistant, boot: str, restored_light: State | None
) -> None:
    """Start HA with the member still loading: absent, or the registry's
    restored-unavailable placeholder."""
    if restored_light is not None:
        mock_restore_cache(hass, [restored_light])
    if boot == "placeholder":
        hass.states.async_set(REAL, "unavailable", {ATTR_RESTORED: True})
    hass.set_state(CoreState.starting)
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("boot", ["placeholder", "absent"])
async def test_follow_member_loading_after_startup_keeps_manual_off(
    hass: HomeAssistant, boot: str
) -> None:
    """A member still loading at startup is not a reboot: the manual off the
    seed respected survives the member's first real state."""
    calls = _record_calls(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await _start_with_member_loading(
        hass, boot, State(VIRTUAL, "off", {"schedule_window_start": MARKER})
    )
    assert _state(hass).state == "off"

    hass.states.async_set(REAL, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["schedule_window_start"] == MARKER
    assert _real_calls(calls, "turn_on") == []


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("boot", ["placeholder", "absent"])
async def test_follow_member_loading_after_startup_is_adopted_outside_window(
    hass: HomeAssistant, boot: str
) -> None:
    """A member that first reports lit outside the window after startup is
    adopted with a timer, as the startup seed would have done."""
    calls = _record_calls(hass)
    hass.states.async_set(SCHED, "off")
    await _start_with_member_loading(hass, boot, None)
    assert _state(hass).state == "off"

    hass.states.async_set(REAL, "on")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert _real_calls(calls, "turn_off") == []


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("back", ["on", "off"])
async def test_follow_member_reload_after_startup_is_reconciled(
    hass: HomeAssistant, back: str
) -> None:
    """A member's integration reloading writes the same restored placeholder
    as startup does, but after the member was seen it is a reboot: the
    window is re-asserted whatever the member comes back as."""
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    calls = _record_calls(hass)
    hass.states.async_set(REAL, "unavailable", {ATTR_RESTORED: True})
    await settle(hass)
    hass.states.async_set(REAL, back)
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert len(_real_calls(calls, "turn_on")) == 1


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_member_entry_reload_is_reconciled(hass: HomeAssistant) -> None:
    """Reloading the config entry of a member that is itself a Virtual Light
    goes through HA's placeholder and is reconciled to the window."""
    inner_entry = make_light_entry(name="Inner", lights=[REAL])
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        inner_entry,
        make_light_entry(
            lights=["light.inner"], schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW
        ),
    )
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    calls = _record_calls(hass)
    hass.states.async_set(REAL, "off")
    assert await hass.config_entries.async_reload(inner_entry.entry_id)
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert [
        d["service_data"]["entity_id"]
        for d in calls
        if d["domain"] == "light"
        and d["service"] == "turn_on"
        and d["service_data"]["entity_id"] == ["light.inner"]
    ] == [["light.inner"]]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_one_of_two_members_recovering_dark_relights_all(
    hass: HomeAssistant,
) -> None:
    """One member of a group rebooting dark mid-window gets re-lit.

    Previously nothing happened: the sibling still on meant the virtual light
    never saw an off, and the dark member stayed dark until the next window.
    """
    calls = _record_calls(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(
        hass,
        make_light_entry(
            lights=[REAL, REAL2], schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW
        ),
    )
    await settle(hass)
    hass.states.async_set(REAL, "on")
    hass.states.async_set(REAL2, "on")
    await settle(hass)

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    calls.clear()
    hass.states.async_set(REAL, "off")
    await settle(hass)

    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED
    on_calls = _real_calls(calls, "turn_on")
    assert len(on_calls) == 1
    assert set(on_calls[0]["entity_id"]) == {REAL, REAL2}


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_member_recovery_with_schedule_unavailable_falls_through(
    hass: HomeAssistant,
) -> None:
    """With no valid schedule to follow, a member recovering dark is sent the
    light's settings again, as for any light that is on."""
    calls = _record_calls(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    )
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)

    hass.states.async_set(SCHED, "unavailable")
    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    calls.clear()
    hass.states.async_set(REAL, "off")
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["last_off_manual"] is None
    assert len(_real_calls(calls, "turn_on")) == 1


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_member_recovery_outside_window_respects_hold(
    hass: HomeAssistant,
) -> None:
    """A held light is not turned off by a member reboot outside the window."""
    calls = _record_calls(hass)
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(HOLD, "on")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_light_entry(
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_FOLLOW,
            hold_entities=[HOLD],
        ),
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    calls.clear()
    hass.states.async_set(REAL, "on")
    await settle(hass)

    assert _state(hass).state == "on"
    assert _real_calls(calls, "turn_off") == []


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_gate_mode_member_recovery_is_unchanged(hass: HomeAssistant) -> None:
    """Gate modes do not own the lights: a boot-lit member is adopted as before."""
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass, make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE)
    )
    await settle(hass)

    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
