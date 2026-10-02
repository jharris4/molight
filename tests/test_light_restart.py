"""Restart (seed-state) tests for the MoLight Virtual Light.

Covers every _seed_state / _follow_schedule_seed path: what the light does
right after a Home Assistant restart for each combination of restored
attributes and current occupancy / illuminance / schedule / real-light
states. Watched sensors are plain states set before the entry is loaded,
exactly as they would already exist when the light entity is added.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_restore_state_shutdown_restart,
    mock_restore_cache,
)

from custom_components.molight.const import (
    ATTR_SCHEDULE_WINDOW_SCHEDULE,
    CONF_ENTITY_TYPE,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
    STATE_WARN,
)
from tests.conftest import (
    finish_startup,
    light_targets,
    make_light_entry,
    record_service_calls,
    restart_entries,
    settle,
    setup_entries,
)

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

OCC = "binary_sensor.occ"
DOOR = "binary_sensor.door"
ILLUM = "binary_sensor.illum"
SCHED = "binary_sensor.sched"
SCHED_B = "binary_sensor.sched_b"
HOLD = "input_boolean.keep_on"
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
async def test_restart_light_at_brightness_zero_stays_idle(
    hass: HomeAssistant,
) -> None:
    """A real light 'on' at brightness 0 counts as off when seeding at startup,
    matching the brightness-0-is-off convention used everywhere else."""
    hass.states.async_set(REAL, "on", {"brightness": 0})
    await setup_entries(hass, make_light_entry())
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
@pytest.mark.regular_virtual_light_only
async def test_restart_occupied_outside_gate_window_adopts_active(
    hass: HomeAssistant,
) -> None:
    """Outside a gate-mode window, startup occupancy may not claim the lights."""
    hass.states.async_set(OCC, "on")
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "on")
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
        ),
    )
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_restart_occupied_inside_gate_window_goes_occupied(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(OCC, "on")
    hass.states.async_set(SCHED, "on")
    await setup_entries(
        hass,
        make_light_entry(
            occupancy=OCC, schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE
        ),
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
    lot = datetime.now(UTC) - timedelta(seconds=10)
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
# A manual off over presence at startup
# ---------------------------------------------------------------------------


async def _manual_off_while_present(hass: HomeAssistant, freezer, entry) -> datetime:
    """Light the room from its sensor, turn it off by hand, return when."""
    await setup_entries(hass, entry)
    await settle(hass)
    assert _state(hass).state == "on"
    freezer.tick(timedelta(seconds=5))
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(REAL, "off")
    await settle(hass)
    assert _state(hass).state == "off"
    off = datetime.now(UTC)
    freezer.tick(timedelta(seconds=10))
    return off


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("started", "raised"),
    [("none", False), ("unknown", False), ("before", False), ("after", True)],
)
async def test_restart_keeps_a_manual_off_over_presence_it_already_had(
    hass: HomeAssistant, freezer, started: str, raised: bool
) -> None:
    """Occupancy still on at a restart only lights a light turned off by hand
    when the sensor dates its visit after that off. A plain sensor, or a
    Combined Occupancy Sensor (no last_on_time), can't tell, so it stays off."""
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    entry = make_light_entry(occupancy=OCC)
    off = await _manual_off_while_present(hass, freezer, entry)
    if started != "none":
        times = {
            "unknown": None,
            "before": (off - timedelta(seconds=5)).isoformat(),
            "after": datetime.now(UTC).isoformat(),
        }
        hass.states.async_set(OCC, "on", {"last_on_time": times[started]})

    await restart_entries(hass, entry)
    await settle(hass)
    if raised:
        assert _state(hass).state == "on"
        assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
        return
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE

    # Their next visit lights it.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.parametrize("late", ["placeholder", "unknown_start"])
async def test_restart_keeps_a_manual_off_over_presence_that_loads_late(
    hass: HomeAssistant, freezer, late: str
) -> None:
    """An occupancy sensor that reports only after startup, or reports a visit
    whose start it did not see, does not light a light turned off by hand."""
    hass.states.async_set(OCC, "on")
    hass.states.async_set(REAL, "off")
    entry = make_light_entry(occupancy=OCC)
    await _manual_off_while_present(hass, freezer, entry)
    if late == "placeholder":
        hass.states.async_set(OCC, "unavailable", {"restored": True})
    else:
        hass.states.async_set(OCC, "off", {"last_on_time": None})
    await restart_entries(hass, entry)
    await settle(hass)
    assert _state(hass).state == "off"

    hass.states.async_set(OCC, "on", {"last_on_time": None})
    await settle(hass)
    assert _state(hass).state == "off"
    assert _state(hass).attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("late", ["placeholder", "missing"])
async def test_member_first_seen_off_is_not_a_manual_off(
    hass: HomeAssistant, late: str
) -> None:
    """A member that reports off only after startup was not turned off by
    hand, so a late occupancy sensor still lights the room."""
    hass.states.async_set(OCC, "unavailable", {"restored": True})
    if late == "placeholder":
        hass.states.async_set(REAL, "unavailable", {"restored": True})
    await setup_entries(hass, make_light_entry(occupancy=OCC))
    await settle(hass)
    hass.states.async_set(REAL, "off")
    await settle(hass)
    assert _state(hass).attributes["last_off_manual"] is None

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.parametrize("late", ["placeholder", "missing", "rebooted"])
async def test_member_reporting_in_off_over_presence_is_lit_across_a_restart(
    hass: HomeAssistant, freezer, late: str
) -> None:
    """Occupancy lights the room while the member is still loading (or
    reboots); the member then reporting in as off is no manual off, so the
    presence still there lights the room after a restart."""
    hass.states.async_set(OCC, "on")
    if late == "placeholder":
        hass.states.async_set(REAL, "unavailable", {"restored": True})
    elif late == "rebooted":
        hass.states.async_set(REAL, "on")
    entry = make_light_entry(occupancy=OCC)
    await setup_entries(hass, entry)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    if late == "rebooted":
        hass.states.async_set(REAL, "unavailable")
        await settle(hass)
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(REAL, "off")
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    assert _state(hass).attributes["last_off_manual"] is None

    freezer.tick(timedelta(seconds=10))
    await restart_entries(hass, entry)
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_restart_lights_a_light_turned_on_again_after_its_manual_off(
    hass: HomeAssistant, freezer
) -> None:
    """A turn-on after the manual off ends it: presence that began before the
    off lights the room when it is dark at startup, after a brightness off."""
    hass.states.async_set(OCC, "on")
    hass.states.async_set(ILLUM, "off")
    hass.states.async_set(REAL, "off")
    entry = make_light_entry(occupancy=OCC, illuminance=ILLUM)
    await _manual_off_while_present(hass, freezer, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    hass.states.async_set(ILLUM, "on")
    await settle(hass)
    assert _state(hass).state == "off"

    await async_mock_restore_state_shutdown_restart(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.states.async_set(ILLUM, "off")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.parametrize("seen", [False, True], ids=["late_open", "seen_closed"])
async def test_restart_keeps_a_manual_off_over_a_door_that_loads_late(
    hass: HomeAssistant, freezer, seen: bool
) -> None:
    """A door first seen open after startup may have stood open since before
    the manual off, so it does not light the room; a door seen closed at
    startup that then opens does."""
    hass.states.async_set(DOOR, "off")
    hass.states.async_set(REAL, "off")
    entry = make_light_entry(door=DOOR)
    await setup_entries(hass, entry)
    await settle(hass)
    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    freezer.tick(timedelta(seconds=10))

    hass.states.async_set(DOOR, "off" if seen else "unavailable")
    await restart_entries(hass, entry)
    await settle(hass)
    if seen:
        hass.states.async_set(DOOR, "unavailable")
        await settle(hass)
    hass.states.async_set(DOOR, "on")
    await settle(hass)
    assert _state(hass).state == ("on" if seen else "off")


def _follow_entry():
    return make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_restart_applies_missed_window_end(hass: HomeAssistant) -> None:
    """Window ended while HA was down → the off boundary is applied at startup."""
    mock_restore_cache(hass, [State(VIRTUAL, "on", {"schedule_window_start": MARKER})])
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["schedule_window_start"] is None


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_restart_missed_window_end_with_lights_already_off(
    hass: HomeAssistant,
) -> None:
    """Same boundary catch-up, but nothing to turn off; just clear the marker."""
    mock_restore_cache(hass, [State(VIRTUAL, "on", {"schedule_window_start": MARKER})])
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["schedule_window_start"] is None


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_restart_missed_window_end_held_with_lights_off_drops_the_window(
    hass: HomeAssistant,
) -> None:
    """With the lights off a hold has nothing to keep: the window is over and
    the sensors are live again, as when it ends at runtime."""
    mock_restore_cache(hass, [State(VIRTUAL, "on", {"schedule_window_start": MARKER})])
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "off")
    hass.states.async_set(HOLD, "on")
    hass.states.async_set(OCC, "off")
    entry = make_light_entry(
        schedule=SCHED,
        schedule_mode=SCHEDULE_MODE_FOLLOW,
        hold_entities=[HOLD],
        occupancy=OCC,
    )
    await setup_entries(hass, entry)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["schedule_window_start"] is None

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


async def _edit_options(hass: HomeAssistant, entry, **overrides) -> None:
    options = {k: v for k, v in entry.data.items() if k != CONF_ENTITY_TYPE}
    hass.config_entries.async_update_entry(entry, options={**options, **overrides})
    await hass.async_block_till_done()
    await settle(hass)


async def _lit_in_follow_window(hass: HomeAssistant):
    """A follow light lit by its window, with the member reporting on."""
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(SCHED_B, "off")
    hass.states.async_set(REAL, "off")
    entry = _follow_entry()
    await setup_entries(hass, entry)
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    attrs = _state(hass).attributes
    assert attrs["schedule_window_start"] == MARKER
    assert attrs[ATTR_SCHEDULE_WINDOW_SCHEDULE] == SCHED
    return entry


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("other", ["off", "on", "unavailable"])
async def test_swapping_the_follow_schedule_leaves_the_old_marker_behind(
    hass: HomeAssistant, other: str
) -> None:
    """A lit light switched to another schedule crossed none of its boundaries."""
    entry = await _lit_in_follow_window(hass)
    marker_b = "2026-07-02T22:00:00+00:00"
    attrs = {"current_window_start": marker_b} if other == "on" else {}
    hass.states.async_set(SCHED_B, other, attrs)

    calls = record_service_calls(hass)
    await _edit_options(hass, entry, **{CONF_SCHEDULE_ENTITY: SCHED_B})
    assert light_targets(calls, "turn_off") == []
    state = _state(hass)
    assert state.state == "on"
    # The new schedule's own window claims the light; otherwise it is adopted.
    assert state.attributes["molight_state"] == (
        STATE_SCHEDULED if other == "on" else STATE_ACTIVE
    )
    assert state.attributes["schedule_window_start"] == (
        marker_b if other == "on" else None
    )
    assert state.attributes[ATTR_SCHEDULE_WINDOW_SCHEDULE] == (
        SCHED_B if other == "on" else None
    )


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_marker_does_not_survive_a_spell_in_gate_mode(
    hass: HomeAssistant,
) -> None:
    """A light lit by hand after its window ended in gate mode is not turned
    off when it is switched back to follow mode."""
    entry = await _lit_in_follow_window(hass)
    await _edit_options(hass, entry, **{CONF_SCHEDULE_MODE: SCHEDULE_MODE_GATE})
    assert _state(hass).attributes["schedule_window_start"] is None

    hass.states.async_set(SCHED, "off")  # the gate end turns it off
    await settle(hass)
    hass.states.async_set(REAL, "off")
    await settle(hass)
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL})
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _state(hass).state == "on"

    calls = record_service_calls(hass)
    await _edit_options(hass, entry, **{CONF_SCHEDULE_MODE: SCHEDULE_MODE_FOLLOW})
    assert light_targets(calls, "turn_off") == []
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize(
    ("saved_for", "kept"), [(None, True), (SCHED, True), (SCHED_B, False)]
)
async def test_restored_marker_belongs_to_the_schedule_it_was_saved_for(
    hass: HomeAssistant, saved_for: str | None, kept: bool
) -> None:
    """A save from before the schedule was recorded is trusted; one made for
    another schedule is not a missed end of this one."""
    attrs = {"schedule_window_start": MARKER}
    if saved_for:
        attrs[ATTR_SCHEDULE_WINDOW_SCHEDULE] = saved_for
    mock_restore_cache(hass, [State(VIRTUAL, "on", attrs)])
    hass.states.async_set(SCHED, "off")
    hass.states.async_set(REAL, "on")
    calls = record_service_calls(hass)
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    assert light_targets(calls, "turn_off") == ([[REAL]] if kept else [])
    assert _state(hass).state == ("off" if kept else "on")


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_restart_keeps_the_marker_of_the_same_follow_schedule(
    hass: HomeAssistant,
) -> None:
    """The same schedule across a real restart still gets its missed end."""
    entry = await _lit_in_follow_window(hass)
    await restart_entries(hass, entry, started=False)
    hass.states.async_set(SCHED, "off")
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    assert light_targets(calls, "turn_off") == [[REAL]]
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
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
@pytest.mark.regular_virtual_light_only
async def test_restart_applies_missed_window_start(
    hass: HomeAssistant, freezer
) -> None:
    """Window began while HA was down → the off light is turned on at startup."""
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["schedule_window_start"] == MARKER

    # No auto-off timer in SCHEDULED.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_restart_missed_window_start_with_lights_already_on(
    hass: HomeAssistant,
) -> None:
    """Same boundary catch-up, but nothing to turn on; adopt SCHEDULED and
    stamp the marker, leaving the already-on lights alone."""
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["schedule_window_start"] == MARKER


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_restart_readopts_window_already_applied(
    hass: HomeAssistant, freezer
) -> None:
    """Window already applied before the restart and lights still on → SCHEDULED."""
    mock_restore_cache(hass, [State(VIRTUAL, "on", {"schedule_window_start": MARKER})])
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
@pytest.mark.regular_virtual_light_only
async def test_restart_spanning_into_next_window_applies_its_start(
    hass: HomeAssistant, freezer
) -> None:
    """Down from inside one window into the next: the earlier window's marker is
    stale, so the new window's start is applied and stamped."""
    later = "2026-07-03T21:00:00+00:00"
    mock_restore_cache(hass, [State(VIRTUAL, "on", {"schedule_window_start": MARKER})])
    hass.states.async_set(SCHED, "on", {"current_window_start": later})
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, _follow_entry())
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["schedule_window_start"] == later

    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
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


# ---------------------------------------------------------------------------
# Effect / warn warning sequence at startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_during_effect_blink_off_seeds_idle(
    hass: HomeAssistant,
) -> None:
    """A restart during the effect blink-off finds the real lights off and
    seeds IDLE: the auto-off effectively completed early; the restored
    pre-warn snapshot must not re-light the room."""
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    "brightness": 200,
                    "warning_active": True,
                    "pre_warn_brightness": 200,
                    "pre_warn_color": {"color_temp_kelvin": 3000},
                },
            )
        ],
    )
    hass.states.async_set(REAL, "off")  # blinked off by the effect stage
    await setup_entries(
        hass,
        make_light_entry(effect_timeout=30, effect_brightness=0, warn_timeout=30),
    )
    await settle(hass)

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["warning_active"] is False
    assert state.attributes["pre_warn_brightness"] is None
    assert state.attributes["pre_warn_color"] is None


@pytest.mark.asyncio
async def test_restart_during_warn_restores_pre_warn_brightness(
    hass: HomeAssistant, freezer
) -> None:
    """A restart during the warn grace re-adopts the lit lights as ACTIVE at
    the restored pre-warning brightness (not the warn-stage brightness), with
    a fresh full timer that later runs a new warning."""
    mock_restore_cache(
        hass,
        [State(VIRTUAL, "on", {"brightness": 255, "pre_warn_brightness": 200})],
    )
    hass.states.async_set(REAL, "on", {"brightness": 255})  # warn stage at 100%
    await setup_entries(hass, make_light_entry(warn_timeout=30, warn_brightness=100))
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 200
    assert state.attributes["pre_warn_brightness"] is None

    # A fresh full timer (60s) runs, then the warn stage, then off.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).attributes["molight_state"] == STATE_WARN

    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_restart_during_warn_without_snapshot_keeps_warn_brightness(
    hass: HomeAssistant,
) -> None:
    """A restore from before pre_warn_brightness existed (attribute absent)
    falls back to adopting the physical brightness as-is."""
    mock_restore_cache(hass, [State(VIRTUAL, "on", {"brightness": 255})])
    hass.states.async_set(REAL, "on", {"brightness": 255})
    await setup_entries(hass, make_light_entry(warn_timeout=30, warn_brightness=100))
    await settle(hass)

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 255


@pytest.mark.asyncio
async def test_restart_mid_warning_without_warning_active_uses_the_snapshot(
    hass: HomeAssistant,
) -> None:
    """A state written before warning_active existed is judged the old way, by
    whether a snapshot was taken, so an upgrade landing mid-warning still
    restores the pre-warning brightness instead of adopting the warn stage's."""
    mock_restore_cache(
        hass,
        [State(VIRTUAL, "on", {"brightness": 255, "pre_warn_brightness": 180})],
    )
    hass.states.async_set(REAL, "on", {"brightness": 255})
    await setup_entries(hass, make_light_entry(warn_timeout=30, warn_brightness=100))
    await settle(hass)

    state = _state(hass)
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 180
    assert state.attributes["warning_active"] is False
