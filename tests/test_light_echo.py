"""Member writes under MoLight's own context are judged, not blindly ignored.

Home Assistant keeps a service call's context on the targeted entity for five
seconds, so a human change right after a MoLight command arrives under
MoLight's context. Only writes consistent with the command count as echoes.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, Event, HomeAssistant, callback
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import STATE_ACTIVE, STATE_IDLE, STATE_WARN

from .conftest import make_light_entry, settle

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

MEMBER = "light.living_room"
VIRTUAL = "light.test_light"


def _member_contexts(hass: HomeAssistant) -> list[Context]:
    """Capture the context of every light service call aimed at the member."""
    contexts: list[Context] = []

    @callback
    def _record(event: Event) -> None:
        if event.data.get("domain") != "light":
            return
        targets = event.data.get("service_data", {}).get("entity_id", [])
        if MEMBER in targets:
            contexts.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return contexts


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(MEMBER, "off")
    await settle(hass)


async def _virtual(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        "light", service, {"entity_id": VIRTUAL, **data}, blocking=True
    )
    await settle(hass)


def _attrs(hass: HomeAssistant) -> dict:
    return hass.states.get(VIRTUAL).attributes


async def _write(
    hass: HomeAssistant, state: str, context: Context, **attributes
) -> None:
    hass.states.async_set(MEMBER, state, attributes, context=context)
    await settle(hass)


@pytest.mark.asyncio
async def test_matching_echo_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """The member reporting exactly what was asked is our echo."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_on_physical"] is None
    assert _attrs(hass)["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_on_at_brightness_zero_echo_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A dimmer echoing `on` at brightness 0 must not read as an off in disguise."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on")
    await _write(hass, "on", contexts[-1], brightness=0)

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert hass.states.get(VIRTUAL).state == "on"


@pytest.mark.asyncio
async def test_two_part_and_stepwise_echo_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Power first with stale brightness, then fade steps toward the target."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=204, transition=2)
    for brightness in (40, 90, 150, 204):
        await _write(hass, "on", contexts[-1], brightness=brightness)

    assert _attrs(hass)["last_brightness_change_physical"] is None
    assert _attrs(hass)["brightness"] == 204


@pytest.mark.asyncio
async def test_quantised_echo_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A bulb reporting 151 for 153 is still echoing our command."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=151)

    assert _attrs(hass)["last_brightness_change_physical"] is None
    assert _attrs(hass)["brightness"] == 153  # the echo path keeps what we asked


@pytest.mark.asyncio
async def test_wall_switch_on_after_our_off_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """An `on` under our turn-off context contradicts the command: a human did it."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)
    await _virtual(hass, "turn_off")
    await _write(hass, "off", contexts[-1])
    assert _attrs(hass)["molight_state"] == STATE_IDLE

    await _write(hass, "on", contexts[-1], brightness=100)

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_on_physical"] is not None


@pytest.mark.asyncio
async def test_dim_after_echo_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Once the echo has landed, a different brightness under our context is human."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)

    await _write(hass, "on", contexts[-1], brightness=100)

    assert _attrs(hass)["last_brightness_change_physical"] is not None
    assert _attrs(hass)["brightness"] == 100


@pytest.mark.asyncio
async def test_dim_during_warning_under_our_context_cancels_it(
    hass: HomeAssistant, freezer
) -> None:
    """A human dim right after the warn command still cancels the warning."""
    entry = make_light_entry(
        name="Test Light",
        lights=[MEMBER],
        timeout=60,
        warn_timeout=30,
        warn_brightness=20,
    )
    await _setup(hass, entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)
    # Run the full timeout out so the state machine enters its warning.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_WARN
    await _write(hass, "on", contexts[-1], brightness=51)  # the warn echo

    await _write(hass, "on", contexts[-1], brightness=120)  # a human dim

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["warning_active"] is False
    assert _attrs(hass)["last_brightness_change_physical"] is not None


def _age_expectations(hass: HomeAssistant, seconds: float) -> None:
    virtual = next(
        entity
        for entity in hass.data["entity_components"]["light"].entities
        if entity.entity_id == VIRTUAL
    )
    for expectation in virtual._echo_expectations.values():
        expectation.issued -= seconds


@pytest.mark.asyncio
async def test_late_matching_reply_under_its_own_context_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A slow bulb's full reply, well past HA's context reuse, is still our echo."""
    await _setup(hass, light_entry)
    await _virtual(hass, "turn_on", brightness=153)
    _age_expectations(hass, 10)

    await _write(hass, "on", Context(), brightness=150)

    assert _attrs(hass)["brightness"] == 153  # ours kept, not mirrored
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_on_physical"] is None
    assert _attrs(hass)["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_late_partial_reply_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Past the settle window only a full match counts: a brightness still short
    of the target, even toward it and under our context, is a human dim."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)
    await _virtual(hass, "turn_on", brightness=50)
    _age_expectations(hass, 10)

    await _write(hass, "on", contexts[-1], brightness=100)

    assert _attrs(hass)["last_brightness_change_physical"] is not None
    assert _attrs(hass)["brightness"] == 100


@pytest.mark.asyncio
async def test_foreign_write_while_settling_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """While the command settles, a write under another context is a real change
    even if it would pass as an echo — HA reuses our context for the reply."""
    await _setup(hass, light_entry)
    await _virtual(hass, "turn_on", brightness=153)

    # Within echo tolerance of 153, so only the context tells it apart.
    await _write(hass, "on", Context(), brightness=150)

    assert _attrs(hass)["brightness"] == 150


@pytest.mark.asyncio
async def test_stale_expectation_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A write under our context long after the command is a real change."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    # The member never echoes; age the expectation past the late window.
    _age_expectations(hass, 60)

    await _write(hass, "on", contexts[-1], brightness=151)

    # Treated as a real change: the member's value is mirrored, not ours kept.
    assert _attrs(hass)["brightness"] == 151
