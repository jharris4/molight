"""Restart (seed-state) tests for the MoLight Virtual Light.

Covers every _seed_state / _follow_schedule_seed path: what the light does
right after a Home Assistant restart for each combination of restored
attributes and current occupancy / illuminance / schedule / real-light
states. Watched sensors are plain states set before the entry is loaded,
exactly as they would already exist when the light entity is added.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.const import (
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
)
from tests.conftest import make_light_entry, settle, setup_entries

OCC = "binary_sensor.occ"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
REAL = "light.real_1"
VIRTUAL = "light.matrix_light"
MARKER = "2026-07-02T21:00:00+00:00"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


# ---------------------------------------------------------------------------
# No sensors configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_adopts_burning_lights_with_timer(
    hass: HomeAssistant, freezer
) -> None:
    """Real lights already on at startup are adopted as ACTIVE and still time out."""
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, make_light_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_restart_with_lights_off_stays_idle(hass: HomeAssistant) -> None:
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


# ---------------------------------------------------------------------------
# Occupancy at startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_occupied_and_dark_lights_the_room(
    hass: HomeAssistant, freezer
) -> None:
    """Occupancy already active (and dark) at startup turns the lights on."""
    hass.states.async_set(OCC, "on")
    hass.states.async_set(ILLUM, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED

    # The rest of the cycle works normally after the seed.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_restart_occupied_but_bright_adopts_active(
    hass: HomeAssistant, freezer
) -> None:
    """Bright at startup suppresses occupancy; burning lights get the timer."""
    hass.states.async_set(OCC, "on")
    hass.states.async_set(ILLUM, "on")
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_restart_occupied_with_illuminance_state_missing(
    hass: HomeAssistant,
) -> None:
    """A configured but not-yet-loaded illuminance sensor is treated as dark.

    Pins the startup-order behavior: if the virtual illuminance sensor has not
    produced a state yet, occupancy wins and the lights come on.
    """
    hass.states.async_set(OCC, "on")
    await setup_entries(hass, make_light_entry(occupancy=OCC, illuminance=ILLUM))
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_restart_occupied_outside_gate_window_adopts_active(
    hass: HomeAssistant,
) -> None:
    """Outside a gate-mode window, startup occupancy may not claim the lights."""
    hass.states.async_set(OCC, "on")
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "on")
    await setup_entries(
        hass,
        make_light_entry(occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE),
    )
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
async def test_restart_occupied_inside_gate_window_goes_occupied(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(OCC, "on")
    hass.states.async_set(SCHED, "on")
    await setup_entries(
        hass,
        make_light_entry(occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE),
    )
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_restart_occupancy_unavailable_adopts_active(
    hass: HomeAssistant,
) -> None:
    """An unavailable occupancy sensor at startup is ignored, not read as on."""
    hass.states.async_set(OCC, "unavailable")
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
async def test_restart_mid_countdown_adopts_active_with_full_timer(
    hass: HomeAssistant, freezer
) -> None:
    """A restart during COUNTDOWN loses the anchor and restarts the full timeout.

    Pins current behavior: occupancy is off with a recent latest_occupied_time,
    but the seed only looks at the current on/off state, so the light is
    adopted as ACTIVE with a fresh light_timeout.
    """
    lot = datetime.now(timezone.utc) - timedelta(seconds=10)
    hass.states.async_set(OCC, "off", {"latest_occupied_time": lot.isoformat()})
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# Follow-mode schedule at startup
# ---------------------------------------------------------------------------


def _follow_entry():
    return make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)


@pytest.mark.asyncio
async def test_restart_applies_missed_window_end(hass: HomeAssistant) -> None:
    """Window ended while HA was down → the off boundary is applied at startup."""
    mock_restore_cache(
        hass, [State(VIRTUAL, "on", {"schedule_window_start": MARKER})]
    )
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["schedule_window_start"] is None


@pytest.mark.asyncio
async def test_restart_missed_window_end_with_lights_already_off(
    hass: HomeAssistant,
) -> None:
    """Same boundary catch-up, but nothing to turn off — just clear the marker."""
    mock_restore_cache(
        hass, [State(VIRTUAL, "on", {"schedule_window_start": MARKER})]
    )
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["schedule_window_start"] is None


@pytest.mark.asyncio
async def test_restart_outside_window_no_marker_adopts_active(
    hass: HomeAssistant, freezer
) -> None:
    """Outside the window with no pending marker, normal adoption applies."""
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_restart_readopts_window_already_applied(
    hass: HomeAssistant, freezer
) -> None:
    """Window already applied before the restart and lights still on → SCHEDULED."""
    mock_restore_cache(
        hass, [State(VIRTUAL, "on", {"schedule_window_start": MARKER})]
    )
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED

    # No auto-off timer in SCHEDULED.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_restart_schedule_state_missing_falls_through(
    hass: HomeAssistant,
) -> None:
    """A follow-mode schedule with no state yet doesn't block normal seeding."""
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
