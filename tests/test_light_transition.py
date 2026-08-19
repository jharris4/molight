"""Transition (fade) tests for the MoLight Virtual Light.

The four optional transition settings shape the service calls the light makes
itself: auto_on/auto_off fade automatic turn-ons/offs, effect/warn fade the
warning stages. Manual turn-ons/offs through the virtual entity must never
carry a transition.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, callback
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    STATE_EFFECT,
    STATE_IDLE,
    STATE_WARN,
)
from tests.conftest import make_light_entry, settle, setup_entries

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

OCC = "binary_sensor.occ"
REAL = "light.real_1"
VIRTUAL = "light.matrix_light"


def _record_service_calls(hass: HomeAssistant) -> list[dict]:
    calls: list[dict] = []

    @callback
    def _record(event) -> None:
        calls.append(event.data)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return calls


def _real_calls(calls: list[dict], service: str) -> list[dict]:
    return [
        d
        for d in calls
        if d["domain"] == "light"
        and d["service"] == service
        and REAL in d["service_data"].get("entity_id", [])
    ]


def _mstate(hass: HomeAssistant) -> str:
    return hass.states.get(VIRTUAL).attributes["molight_state"]


@pytest.mark.asyncio
async def test_occupancy_turn_on_carries_auto_on_transition(
    hass: HomeAssistant,
) -> None:
    """An occupancy-triggered turn-on fades over auto_on_transition."""
    entry = make_light_entry(occupancy=OCC, auto_on_transition=2.5)
    await setup_entries(hass, entry)
    calls = _record_service_calls(hass)

    hass.states.async_set(OCC, "on")
    await settle(hass)

    on_calls = _real_calls(calls, "turn_on")
    assert on_calls
    assert on_calls[-1]["service_data"]["transition"] == 2.5


@pytest.mark.asyncio
async def test_manual_turn_on_has_no_transition(hass: HomeAssistant) -> None:
    """Turning the virtual light on by hand never fades, even when the
    automatic transition is configured."""
    entry = make_light_entry(occupancy=OCC, auto_on_transition=2.5)
    await setup_entries(hass, entry)
    calls = _record_service_calls(hass)

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()

    on_calls = _real_calls(calls, "turn_on")
    assert on_calls
    assert "transition" not in on_calls[-1]["service_data"]


@pytest.mark.asyncio
async def test_timer_expiry_off_carries_auto_off_transition(
    hass: HomeAssistant, freezer
) -> None:
    """The automatic turn-off at timer expiry fades over auto_off_transition."""
    entry = make_light_entry(auto_off_transition=3)
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()
    calls = _record_service_calls(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)

    assert _mstate(hass) == STATE_IDLE
    off_calls = _real_calls(calls, "turn_off")
    assert off_calls
    assert off_calls[-1]["service_data"]["transition"] == 3.0


@pytest.mark.asyncio
async def test_manual_turn_off_has_no_transition(hass: HomeAssistant) -> None:
    """A manual off is immediate even with auto_off_transition configured."""
    entry = make_light_entry(auto_off_transition=3)
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()
    calls = _record_service_calls(hass)

    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()

    off_calls = _real_calls(calls, "turn_off")
    assert off_calls
    assert "transition" not in off_calls[-1]["service_data"]


@pytest.mark.asyncio
async def test_stage_and_final_off_transitions(hass: HomeAssistant, freezer) -> None:
    """The effect dip, the warn brightness, and the final off each fade with
    their own configured transition."""
    entry = make_light_entry(
        effect_timeout=10,
        effect_brightness=50,
        warn_timeout=15,
        warn_brightness=80,
        effect_transition=1.5,
        warn_transition=4,
        auto_off_transition=2,
    )
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}
    )
    await hass.async_block_till_done()
    calls = _record_service_calls(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT
    effect_call = _real_calls(calls, "turn_on")[-1]["service_data"]
    assert effect_call["brightness"] == 128  # 50%
    assert effect_call["transition"] == 1.5

    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN
    warn_call = _real_calls(calls, "turn_on")[-1]["service_data"]
    assert warn_call["transition"] == 4.0

    freezer.tick(timedelta(seconds=16))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_IDLE
    off_call = _real_calls(calls, "turn_off")[-1]["service_data"]
    assert off_call["transition"] == 2.0


@pytest.mark.asyncio
async def test_effect_blink_off_uses_effect_transition(
    hass: HomeAssistant, freezer
) -> None:
    """An effect brightness of 0 blinks the real lights off via turn_off — that
    call still fades with the effect transition."""
    entry = make_light_entry(
        effect_timeout=10, effect_brightness=0, warn_timeout=0, effect_transition=1
    )
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()
    calls = _record_service_calls(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)

    assert _mstate(hass) == STATE_EFFECT
    off_calls = _real_calls(calls, "turn_off")
    assert off_calls
    assert off_calls[-1]["service_data"]["transition"] == 1.0


@pytest.mark.asyncio
async def test_no_transition_attribute_when_unconfigured(
    hass: HomeAssistant, freezer
) -> None:
    """Without any transition settings the service calls carry no transition,
    exactly as before the feature existed."""
    entry = make_light_entry(occupancy=OCC)
    await setup_entries(hass, entry)
    calls = _record_service_calls(hass)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    on_calls = _real_calls(calls, "turn_on")
    assert on_calls
    assert "transition" not in on_calls[-1]["service_data"]

    hass.states.async_set(OCC, "off")
    await settle(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)

    assert _mstate(hass) == STATE_IDLE
    off_calls = _real_calls(calls, "turn_off")
    assert off_calls
    assert "transition" not in off_calls[-1]["service_data"]
