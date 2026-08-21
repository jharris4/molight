"""Transition (fade) tests for the MoLight Virtual Light.

The four optional transition settings shape the service calls the light makes
itself: auto_on/auto_off fade automatic turn-ons/offs, effect/warn fade the
warning stages. Manual turn-ons/offs through the virtual entity never carry a
*configured* transition.

A caller-supplied transition is separate: it is forwarded to the members, which
requires advertising LightEntityFeature.TRANSITION. That feature fails open —
withheld only once every member is visible and none can fade.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.light import LightEntityFeature
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


# ---------------------------------------------------------------------------
# Caller-supplied transitions — arrive on the service call itself, and only
# reach async_turn_on when the entity advertises LightEntityFeature.TRANSITION.
# ---------------------------------------------------------------------------


def _features(hass: HomeAssistant) -> int:
    return hass.states.get(VIRTUAL).attributes["supported_features"]


@pytest.mark.asyncio
async def test_caller_transition_forwarded_on_turn_on(hass: HomeAssistant) -> None:
    """A transition on the turn-on service call reaches the real lights."""
    entry = make_light_entry()
    await setup_entries(hass, entry)
    calls = _record_service_calls(hass)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "transition": 4}
    )
    await hass.async_block_till_done()

    on_calls = _real_calls(calls, "turn_on")
    assert on_calls
    assert on_calls[-1]["service_data"]["transition"] == 4.0


@pytest.mark.asyncio
async def test_caller_transition_forwarded_on_turn_off(hass: HomeAssistant) -> None:
    """A transition on the turn-off service call reaches the real lights."""
    entry = make_light_entry()
    await setup_entries(hass, entry)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await hass.async_block_till_done()
    calls = _record_service_calls(hass)

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL, "transition": 2.5}
    )
    await hass.async_block_till_done()

    off_calls = _real_calls(calls, "turn_off")
    assert off_calls
    assert off_calls[-1]["service_data"]["transition"] == 2.5


@pytest.mark.asyncio
async def test_caller_transition_used_instead_of_configured_auto_fade(
    hass: HomeAssistant,
) -> None:
    """The manual path still ignores auto_on_transition; the caller's own fade
    is what gets sent."""
    entry = make_light_entry(auto_on_transition=2.5)
    await setup_entries(hass, entry)
    calls = _record_service_calls(hass)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "transition": 7}
    )
    await hass.async_block_till_done()

    assert _real_calls(calls, "turn_on")[-1]["service_data"]["transition"] == 7.0


@pytest.mark.asyncio
async def test_caller_transition_carries_the_pre_warning_restore(
    hass: HomeAssistant, freezer
) -> None:
    """A turn-on mid-warning restores the pre-warning brightness over the
    caller's fade rather than snapping to it."""
    entry = make_light_entry(effect_timeout=10, effect_brightness=50)
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}
    )
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT
    calls = _record_service_calls(hass)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "transition": 3}
    )
    await hass.async_block_till_done()

    restore = _real_calls(calls, "turn_on")[-1]["service_data"]
    assert restore["brightness"] == 200  # the pre-warning brightness
    assert restore["transition"] == 3.0


@pytest.mark.asyncio
async def test_transition_advertised_while_a_member_is_unaccounted_for(
    hass: HomeAssistant,
) -> None:
    """Fail open: a member with no state yet must not cost the caller a fade."""
    entry = make_light_entry()
    await setup_entries(hass, entry)

    assert hass.states.get(REAL) is None
    assert _features(hass) & LightEntityFeature.TRANSITION


@pytest.mark.asyncio
async def test_transition_advertised_when_a_member_can_fade(
    hass: HomeAssistant,
) -> None:
    """A member advertising TRANSITION makes the virtual light advertise it."""
    hass.states.async_set(
        REAL, "off", {"supported_features": LightEntityFeature.TRANSITION}
    )
    entry = make_light_entry()
    await setup_entries(hass, entry)

    assert _features(hass) & LightEntityFeature.TRANSITION


@pytest.mark.asyncio
async def test_transition_withheld_when_every_member_cannot_fade(
    hass: HomeAssistant,
) -> None:
    """Once every member is visible and none can fade, the feature is dropped
    and Home Assistant strips the caller's transition before it reaches us."""
    hass.states.async_set(REAL, "off", {"supported_features": 0})
    entry = make_light_entry()
    await setup_entries(hass, entry)
    assert not _features(hass) & LightEntityFeature.TRANSITION

    calls = _record_service_calls(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "transition": 4}
    )
    await hass.async_block_till_done()

    on_calls = _real_calls(calls, "turn_on")
    assert on_calls
    assert "transition" not in on_calls[-1]["service_data"]


@pytest.mark.asyncio
async def test_transition_stays_advertised_until_the_last_member_is_judged(
    hass: HomeAssistant,
) -> None:
    """One visible non-fading member is not proof: the feature is withheld only
    once the other member reports in as unable to fade too."""
    other = "light.real_2"
    hass.states.async_set(REAL, "off", {"supported_features": 0})
    entry = make_light_entry(lights=[REAL, other])
    await setup_entries(hass, entry)

    # real_2 has no state yet — still unknown, so still advertised.
    assert _features(hass) & LightEntityFeature.TRANSITION

    hass.states.async_set(other, "off", {"supported_features": 0})
    await settle(hass)
    assert not _features(hass) & LightEntityFeature.TRANSITION

    # A member that can fade re-enables it.
    hass.states.async_set(
        other, "off", {"supported_features": LightEntityFeature.TRANSITION}
    )
    await settle(hass)
    assert _features(hass) & LightEntityFeature.TRANSITION
