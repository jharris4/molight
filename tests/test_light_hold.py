"""Tests for holding a Virtual Light's auto-off.

Auto-off is held while the companion "<name> Auto-off" switch is off or any
configured keep-on entity is on. While held every automatic turn-off is
suspended; turn-ons and manual control are unaffected. Releasing the hold
re-evaluates the configured rules and otherwise starts a fresh full timer.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.const import (
    ILLUMINANCE_MODE_CONTROL,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
)
from tests.conftest import make_light_entry, settle, setup_entries

OCC = "binary_sensor.occ"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
HOLD = "input_boolean.guest_mode"
HOLD2 = "input_boolean.party_mode"
VIRTUAL = "light.matrix_light"
SWITCH = "switch.matrix_light_auto_off"
MARKER = "2026-07-02T21:00:00+00:00"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


async def _switch(hass: HomeAssistant, on: bool) -> None:
    await hass.services.async_call(
        "switch", "turn_on" if on else "turn_off", {"entity_id": SWITCH}
    )
    await settle(hass)


async def _tick(hass: HomeAssistant, freezer, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


# ---------------------------------------------------------------------------
# Companion switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_companion_switch_created_default_on(hass: HomeAssistant) -> None:
    """Every Virtual Light entry gets an Auto-off switch, on by default."""
    await setup_entries(hass, make_light_entry())

    state = hass.states.get(SWITCH)
    assert state is not None
    assert state.state == "on"
    assert _state(hass).attributes["auto_off_held"] is False


@pytest.mark.asyncio
async def test_switch_off_holds_timer(hass: HomeAssistant, freezer) -> None:
    """With the switch off, the auto-off timer never fires."""
    await setup_entries(hass, make_light_entry())

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    await _switch(hass, on=False)
    assert _state(hass).attributes["auto_off_held"] is True

    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_switch_release_starts_fresh_full_timer(
    hass: HomeAssistant, freezer
) -> None:
    """Turning the switch back on starts a fresh full timer (60s fixture)."""
    await setup_entries(hass, make_light_entry())

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    await _switch(hass, on=False)
    await _tick(hass, freezer, 3600)
    await _switch(hass, on=True)
    assert _state(hass).attributes["auto_off_held"] is False
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    # Full timeout, not the long-elapsed remainder.
    await _tick(hass, freezer, 30)
    assert _state(hass).state == "on"
    await _tick(hass, freezer, 31)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_switch_state_restored_across_restart(
    hass: HomeAssistant, freezer
) -> None:
    """A switch left off before a restart still holds auto-off afterwards."""
    mock_restore_cache(hass, [State(SWITCH, "off")])
    hass.states.async_set("light.real_1", "on")
    await setup_entries(hass, make_light_entry())
    await settle(hass)

    # The adopted on-light must not be turned off by the startup timer.
    assert hass.states.get(SWITCH).state == "off"
    assert _state(hass).state == "on"
    assert _state(hass).attributes["auto_off_held"] is True
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"


# ---------------------------------------------------------------------------
# Keep-on entities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hold_entity_holds_and_release_restarts_timer(
    hass: HomeAssistant, freezer
) -> None:
    """A keep-on entity going on suspends the timer; off starts a fresh one."""
    hass.states.async_set(HOLD, "off")
    await setup_entries(hass, make_light_entry(hold_entities=[HOLD]))

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.states.async_set(HOLD, "on")
    await settle(hass)
    assert _state(hass).attributes["auto_off_held"] is True

    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"

    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert _state(hass).attributes["auto_off_held"] is False
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_hold_entity_on_at_startup(hass: HomeAssistant, freezer) -> None:
    """A keep-on entity already on when the light seeds holds from the start."""
    hass.states.async_set(HOLD, "on")
    hass.states.async_set("light.real_1", "on")
    await setup_entries(hass, make_light_entry(hold_entities=[HOLD]))
    await settle(hass)

    assert _state(hass).attributes["auto_off_held"] is True
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_all_holds_must_release(hass: HomeAssistant, freezer) -> None:
    """Auto-off resumes only when the switch is on and every entity is off."""
    hass.states.async_set(HOLD, "off")
    hass.states.async_set(HOLD2, "off")
    await setup_entries(hass, make_light_entry(hold_entities=[HOLD, HOLD2]))

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.states.async_set(HOLD, "on")
    hass.states.async_set(HOLD2, "on")
    await _switch(hass, on=False)

    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert _state(hass).attributes["auto_off_held"] is True
    await _switch(hass, on=True)
    assert _state(hass).attributes["auto_off_held"] is True
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"

    hass.states.async_set(HOLD2, "off")
    await settle(hass)
    assert _state(hass).attributes["auto_off_held"] is False
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_hold_entity_unavailable_keeps_last_value(
    hass: HomeAssistant, freezer
) -> None:
    """A keep-on entity dropping to unavailable keeps holding."""
    hass.states.async_set(HOLD, "off")
    await setup_entries(hass, make_light_entry(hold_entities=[HOLD]))

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.states.async_set(HOLD, "on")
    await settle(hass)
    hass.states.async_set(HOLD, "unavailable")
    await settle(hass)

    assert _state(hass).attributes["auto_off_held"] is True
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"

    hass.states.async_set(HOLD, "off")
    await settle(hass)
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# Interplay with occupancy / illuminance / schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_ons_unaffected_while_held(hass: HomeAssistant) -> None:
    """Occupancy still turns the lights on while auto-off is held."""
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    await _switch(hass, on=False)

    hass.states.async_set(OCC, "on")
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_manual_off_works_while_held(hass: HomeAssistant) -> None:
    """A manual off is never suppressed by a hold."""
    await setup_entries(hass, make_light_entry())

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    await _switch(hass, on=False)

    await hass.services.async_call("light", "turn_off", {"entity_id": VIRTUAL})
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_external_off_works_while_held(hass: HomeAssistant, freezer) -> None:
    """A wall-switch off is respected while held; releasing while off is a no-op."""
    await setup_entries(hass, make_light_entry())
    await _switch(hass, on=False)

    # External turn-on while held: adopted as ACTIVE but no timer arms.
    hass.states.async_set("light.real_1", "on")
    await settle(hass)
    assert _state(hass).state == "on"
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"

    # Wall switch off — never suppressed by the hold.
    hass.states.async_set("light.real_1", "off")
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    # Releasing the hold with the lights off changes nothing.
    await _switch(hass, on=True)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_occupancy_clear_while_held_then_release(
    hass: HomeAssistant, freezer
) -> None:
    """Occupancy clearing while held enters COUNTDOWN without a timer."""
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC))

    hass.states.async_set(OCC, "on")
    await settle(hass)
    await _switch(hass, on=False)
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN

    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"

    await _switch(hass, on=True)
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_release_while_occupied_stays_occupied(
    hass: HomeAssistant, freezer
) -> None:
    """Releasing the hold under active occupancy keeps OCCUPIED, no timer."""
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC))

    hass.states.async_set(OCC, "on")
    await settle(hass)
    await _switch(hass, on=False)
    await _switch(hass, on=True)

    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_hold_blocks_bright_force_off_release_applies_it(
    hass: HomeAssistant,
) -> None:
    """Bright in control mode can't turn held lights off; release does."""
    hass.states.async_set(ILLUM, "off")  # dark
    await setup_entries(
        hass,
        make_light_entry(illuminance=ILLUM, illuminance_mode=ILLUMINANCE_MODE_CONTROL),
    )

    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    await _switch(hass, on=False)
    hass.states.async_set(ILLUM, "on")  # bright
    await settle(hass)
    assert _state(hass).state == "on"

    await _switch(hass, on=True)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_hold_blocks_follow_window_end_release_applies_it(
    hass: HomeAssistant,
) -> None:
    """A follow-mode window end is suppressed while held, applied at release."""
    hass.states.async_set(SCHED, "off")
    await setup_entries(
        hass,
        make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW),
    )

    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED

    await _switch(hass, on=False)
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).state == "on"

    await _switch(hass, on=True)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE
    assert _state(hass).attributes["schedule_window_start"] is None


@pytest.mark.asyncio
async def test_release_inside_follow_window_stays_scheduled(
    hass: HomeAssistant, freezer
) -> None:
    """Releasing mid-window keeps the light SCHEDULED with no timer."""
    hass.states.async_set(SCHED, "off")
    await setup_entries(
        hass,
        make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW),
    )

    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await settle(hass)
    await _switch(hass, on=False)
    await _switch(hass, on=True)

    assert _state(hass).attributes["molight_state"] == STATE_SCHEDULED
    await _tick(hass, freezer, 3600)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_hold_blocks_gate_window_end_release_applies_it(
    hass: HomeAssistant,
) -> None:
    """A gate-mode window end is suppressed while held, applied at release."""
    hass.states.async_set(OCC, "off")
    hass.states.async_set(SCHED, "on")
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
        ),
    )

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _switch(hass, on=False)
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _state(hass).state == "on"

    await _switch(hass, on=True)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_hold_suppresses_false_detection_quick_off(
    hass: HomeAssistant, freezer
) -> None:
    """The false-detection quick off is an automatic off — held like the rest."""
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC))

    hass.states.async_set(OCC, "on")
    await settle(hass)
    await _switch(hass, on=False)
    hass.states.async_set(OCC, "off", {"last_clear_false_detection": MARKER})
    await settle(hass)

    # Default false-off delay is 5s; held lights must survive far longer.
    await _tick(hass, freezer, 600)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_startup_hold_keeps_missed_window_end_marker(
    hass: HomeAssistant,
) -> None:
    """A follow window that ended while HA was down is not applied while held.

    The light restores as SCHEDULED with the window marker kept; releasing
    the hold applies the missed off boundary.
    """
    mock_restore_cache(
        hass,
        [State(VIRTUAL, "on", {"schedule_window_start": MARKER})],
    )
    hass.states.async_set("light.real_1", "on")
    hass.states.async_set(SCHED, "off")  # the window ended during downtime
    hass.states.async_set(HOLD, "on")
    await setup_entries(
        hass,
        make_light_entry(
            schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW, hold_entities=[HOLD]
        ),
    )
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["schedule_window_start"] == MARKER

    # Releasing the hold applies the missed window end.
    hass.states.async_set(HOLD, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["schedule_window_start"] is None
