"""Unavailability tests for the MoLight Virtual Light.

Each entity the light watches (real lights, occupancy, illuminance,
schedule) can drop to unavailable/unknown at any time — e.g. a Zigbee
device falling off the mesh or an integration reloading. Those blips must
never be read as state changes, and recovery transitions must behave like
fresh events.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    DOMAIN,
    ENTITY_TYPE_OCCUPANCY,
    SCHEDULE_MODE_FOLLOW,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
)
from tests.conftest import make_light_entry, settle, setup_entries

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

OCC = "binary_sensor.occ"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
REAL = "light.real_1"
REAL2 = "light.real_2"
VIRTUAL = "light.matrix_light"
MARKER = "2026-07-02T21:00:00+00:00"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


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
    replacing an occupancy-anchored countdown with a full ACTIVE timer — a
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

    # The original countdown completes on schedule — 31s later, not 60s.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_real_light_recovery_mirrors_brightness(hass: HomeAssistant) -> None:
    """The recovery adoption still mirrors the member's brightness/color."""
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
async def test_real_light_recovers_directly_to_off(hass: HomeAssistant) -> None:
    """unavailable → off counts as a real off and releases the virtual light."""
    await setup_entries(hass, make_light_entry())

    hass.states.async_set(REAL, "on")
    await settle(hass)
    hass.states.async_set(REAL, "unavailable")
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


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

    # Manual off mid-window — respected.
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
async def test_source_dropout_releases_light_via_virtual_occupancy(
    hass: HomeAssistant, freezer
) -> None:
    """End-to-end: a dead motion sensor no longer holds the lights on forever.

    The virtual occupancy sensor clears itself after its clear-on-unavailable
    timeout (default 60s), and because the clear is not flagged as a false
    detection, the light runs its normal countdown — anchored to the dropout
    moment — instead of the quick-off.
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

    # The motion sensor falls off the network — nothing happens yet.
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
async def test_light_with_no_state_never_counts_as_off(hass: HomeAssistant) -> None:
    """A controlled light that has never reported a state is not assumed off."""
    await setup_entries(hass, make_light_entry(lights=[REAL, REAL2]))

    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).state == "on"

    # REAL2 has no state at all — turning REAL off must not release the
    # virtual light, since REAL2 may well still be burning.
    hass.states.async_set(REAL, "off")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
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
