"""Startup tests: what a light reports and decides before its members are known.

Every test goes through Home Assistant's real save, unload and setup
(restart_entries / crash_entries), with the members reporting each command
they receive while they are reachable. A member that is still loading
receives nothing until it reports in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.components.light import (
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
)
from homeassistant.const import ATTR_RESTORED, EVENT_CALL_SERVICE, EVENT_STATE_CHANGED
from homeassistant.core import (
    Context,
    CoreState,
    Event,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.util import color as color_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.const import (
    ACTIVE_SETTINGS_INSIDE,
    ACTIVE_SETTINGS_OUTSIDE,
    ATTR_ACTIVE_SETTINGS,
    ATTR_ACTIVE_SETTINGS_SCHEDULE,
    ATTR_ACTIVE_SETTINGS_WINDOW,
    ATTR_SCHEDULE_END_OFF_PENDING,
    ATTR_STANDBY_SUPPRESSED,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_MODE,
    CONF_LIGHT_TIMEOUT,
    CONF_OCCUPANCY_ENTITY,
    CONF_STANDBY_BRIGHTNESS,
    ILLUMINANCE_MODE_CONTROL,
    SCHEDULE_END_ACTION_SWITCH,
    SCHEDULE_END_ACTION_TURN_OFF,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
    STATE_STANDBY,
    STATE_WARN,
)
from tests.conftest import (
    crash_entries,
    finish_startup,
    light_targets,
    make_light_entry,
    make_scheduled_light_entry,
    record_service_calls,
    restart_entries,
    settle,
    setup_entries,
    stop_entries,
)

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

REAL = "light.real_1"
VIRTUAL = "light.matrix_light"
SCHED = "binary_sensor.window"
OCC = "binary_sensor.occ"
MARKER = "2026-01-14T09:00:00-08:00"
MARKER2 = "2026-01-14T11:00:00-08:00"
HS_CAPS = {"supported_color_modes": ["hs"]}
CT_CAPS = {
    "supported_color_modes": ["color_temp"],
    "min_color_temp_kelvin": 2700,
    "max_color_temp_kelvin": 6500,
}
BLUE = (240.0, 100.0)


class _Members:
    """Real lights that report each command they receive while reachable."""

    def __init__(self, hass: HomeAssistant, members: list[str], caps: dict) -> None:
        self.hass = hass
        self.caps = caps
        self.reachable = set(members)
        for member in members:
            hass.states.async_set(member, "off", caps)
        hass.bus.async_listen(EVENT_CALL_SERVICE, self._report)

    @callback
    def _report(self, event: Event) -> None:
        data = event.data["service_data"]
        if event.data["domain"] != "light":
            return
        for member in data.get("entity_id", []):
            if member not in self.reachable:
                continue
            if event.data["service"] == "turn_off":
                self.hass.states.async_set(
                    member, "off", self.caps, context=event.context
                )
                continue
            attrs = dict(self.hass.states.get(member).attributes)
            if "brightness" in data:
                attrs["brightness"] = data["brightness"]
            if "rgb_color" in data:
                attrs["hs_color"] = color_util.color_RGB_to_hs(*data["rgb_color"])
            if "hs_color" in data:
                attrs["hs_color"] = tuple(data["hs_color"])
            if "hs_color" in attrs:
                attrs["color_mode"] = "hs"
            self.hass.states.async_set(member, "on", attrs, context=event.context)

    def boot(self, member: str, how: str, **attrs) -> None:
        """Put a member in the state it has when Home Assistant has started."""
        self.reachable.discard(member)
        if how == "placeholder":
            self.hass.states.async_set(member, "unavailable", {ATTR_RESTORED: True})
        elif how == "unavailable":
            self.hass.states.async_set(member, "unavailable")
        elif how == "absent":
            self.hass.states.async_remove(member)
        else:
            self.load(member, how, **attrs)

    def load(self, member: str, state: str, **attrs) -> None:
        """Have a member report its real state, under a context of its own."""
        self.reachable.add(member)
        self.hass.states.async_set(
            member, state, {**self.caps, **attrs}, context=Context()
        )


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


def _attrs(hass: HomeAssistant) -> dict:
    return hass.states.get(VIRTUAL).attributes


def _virtual_states(hass: HomeAssistant) -> list[str]:
    """Record every on/off the virtual light publishes from now on."""
    states: list[str] = []

    @callback
    def _record(event: Event) -> None:
        if event.data["entity_id"] == VIRTUAL and event.data["new_state"]:
            states.append(event.data["new_state"].state)

    hass.bus.async_listen(EVENT_STATE_CHANGED, _record)
    return states


# ---------------------------------------------------------------------------
# What the light reports until it seeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_light_reports_its_restored_state_until_it_seeds(
    hass: HomeAssistant,
) -> None:
    """An occupied light keeps reporting on and occupied while HA starts."""
    real = _Members(hass, [REAL], {})
    hass.states.async_set(OCC, "off")
    entry = make_light_entry(occupancy=OCC)
    await setup_entries(hass, entry)
    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED

    states = _virtual_states(hass)
    await restart_entries(hass, entry, started=False)
    assert _state(hass).state == "on"
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED

    await finish_startup(hass)
    await settle(hass)
    assert _state(hass).state == "on"
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    # Unloading leaves "unavailable"; the light never reports off on the way.
    assert "off" not in states
    assert real.reachable == {REAL}


@pytest.mark.asyncio
async def test_colored_light_reports_its_restored_state_until_it_seeds(
    hass: HomeAssistant,
) -> None:
    """A restored on light reports its color, whatever its members read as."""
    real = _Members(hass, [REAL], HS_CAPS)
    entry = make_light_entry()
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": VIRTUAL, "brightness": 200, "hs_color": BLUE},
        blocking=True,
    )
    await settle(hass)

    await restart_entries(hass, entry, started=False)
    real.boot(REAL, "placeholder")
    assert _state(hass).state == "on"
    assert _attrs(hass)["brightness"] == 200
    assert tuple(_attrs(hass)["hs_color"]) == BLUE

    await finish_startup(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_color_temp_light_reports_a_kelvin_range_until_it_seeds(
    hass: HomeAssistant, caplog
) -> None:
    """Home Assistant 2026.1 has no default range and warns of mireds without one."""
    real = _Members(hass, [REAL], CT_CAPS)
    entry = make_light_entry()
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": VIRTUAL, "color_temp_kelvin": 3000},
        blocking=True,
    )
    await settle(hass)

    await restart_entries(hass, entry, started=False)
    real.boot(REAL, "placeholder")
    assert _attrs(hass)["color_mode"] == "color_temp"
    assert _attrs(hass)["min_color_temp_kelvin"] == DEFAULT_MIN_KELVIN
    assert _attrs(hass)["max_color_temp_kelvin"] == DEFAULT_MAX_KELVIN
    assert not [r for r in caplog.records if r.name == "homeassistant.helpers.frame"]


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["turn_on", "turn_off", "toggle"])
async def test_command_before_the_seed_acts_on_the_restored_state(
    hass: HomeAssistant, service: str
) -> None:
    """A service call while HA starts replaces the restored report at once."""
    _Members(hass, [REAL], {})
    entry = make_light_entry()
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)

    await restart_entries(hass, entry, started=False)
    calls = record_service_calls(hass)
    await hass.services.async_call("light", service, {"entity_id": VIRTUAL})
    await settle(hass)
    on = service == "turn_on"
    assert light_targets(calls, "turn_on" if on else "turn_off")[-1] == [REAL]
    assert _state(hass).state == ("on" if on else "off")
    assert _attrs(hass)["molight_state"] == (STATE_ACTIVE if on else STATE_IDLE)

    await finish_startup(hass)
    await settle(hass)
    assert _state(hass).state == ("on" if on else "off")
    assert _attrs(hass)["molight_state"] == (STATE_ACTIVE if on else STATE_IDLE)


def _standby_entry(end_action: str = SCHEDULE_END_ACTION_TURN_OFF) -> MockConfigEntry:
    return make_scheduled_light_entry(
        name="Matrix Light",
        schedule=SCHED,
        schedule_end_action=end_action,
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={CONF_LIGHT_TIMEOUT: 60, CONF_STANDBY_BRIGHTNESS: 20},
    )


@pytest.mark.asyncio
async def test_scheduled_light_reports_its_restored_settings_until_it_seeds(
    hass: HomeAssistant,
) -> None:
    """The active side and its window marker are the saved ones, not live reads."""
    _Members(hass, [REAL], {})
    # A touching window started while Home Assistant was down.
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER2})
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    "brightness": 51,
                    "molight_state": STATE_STANDBY,
                    ATTR_ACTIVE_SETTINGS: ACTIVE_SETTINGS_INSIDE,
                    ATTR_ACTIVE_SETTINGS_SCHEDULE: SCHED,
                    ATTR_ACTIVE_SETTINGS_WINDOW: MARKER,
                },
            )
        ],
    )
    hass.set_state(CoreState.starting)
    await setup_entries(hass, _standby_entry())
    assert _state(hass).state == "on"
    assert _attrs(hass)["molight_state"] == STATE_STANDBY
    assert _attrs(hass)[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert _attrs(hass)[ATTR_ACTIVE_SETTINGS_WINDOW] == MARKER

    await finish_startup(hass)
    await settle(hass)
    assert _attrs(hass)[ATTR_ACTIVE_SETTINGS_WINDOW] == MARKER2


@pytest.mark.asyncio
async def test_manual_off_in_window_survives_a_crash_after_restart(
    hass: HomeAssistant,
) -> None:
    """Standby turned off by hand stays off for its window, also after a power
    cut that leaves only what Home Assistant saved as it started."""
    _Members(hass, [REAL], {})
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    entry = _standby_entry()
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True

    await restart_entries(hass, entry)
    await settle(hass)
    assert _state(hass).state == "off"

    await crash_entries(hass, entry)
    await settle(hass)
    assert _state(hass).state == "off"
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True
    assert _attrs(hass)[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE


@pytest.mark.asyncio
async def test_standby_rest_survives_a_crash_after_restart(
    hass: HomeAssistant, freezer
) -> None:
    """A light resting at standby goes back there, not to a timeout."""
    _Members(hass, [REAL], {})
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    entry = _standby_entry()
    await setup_entries(hass, entry)
    await settle(hass)

    await restart_entries(hass, entry)
    await crash_entries(hass, entry)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_STANDBY
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"
    assert _attrs(hass)["molight_state"] == STATE_STANDBY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "end_action", [SCHEDULE_END_ACTION_TURN_OFF, SCHEDULE_END_ACTION_SWITCH]
)
async def test_missed_end_survives_a_stop_during_startup(
    hass: HomeAssistant, end_action: str
) -> None:
    """HA stopped again before it finished starting still applies the end."""
    real = _Members(hass, [REAL], {})
    left = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    hass.states.async_set(OCC, "off", {"latest_occupied_time": left})
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    entry = make_scheduled_light_entry(
        name="Matrix Light",
        schedule=SCHED,
        schedule_end_action=end_action,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_OCCUPANCY_ENTITY: OCC},
        inside={CONF_LIGHT_TIMEOUT: 60},
    )
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)

    await restart_entries(hass, entry, started=False)
    hass.states.async_set(SCHED, "off")
    assert _attrs(hass)[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE

    await restart_entries(hass, entry, started=False)
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    assert light_targets(calls, "turn_off") == [[REAL]]
    assert _state(hass).state == "off"
    assert real.reachable == {REAL}


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["outer_first", "inner_first"])
async def test_nested_follow_light_applies_a_missed_end_in_either_order(
    hass: HomeAssistant, order: str
) -> None:
    """An outer light reads the inner light's restored state while HA starts."""
    _Members(hass, [REAL], {})
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    inner = make_light_entry(name="Inner", lights=[REAL])
    outer = make_light_entry(
        lights=["light.inner"], schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW
    )
    entries = (outer, inner) if order == "outer_first" else (inner, outer)
    await setup_entries(hass, inner, outer)
    await settle(hass)
    assert _state(hass).state == "on"
    assert hass.states.get(REAL).state == "on"

    await restart_entries(hass, *entries, started=False)
    hass.states.async_set(SCHED, "off")
    assert hass.states.get("light.inner").state == "on"
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    assert ["light.inner"] in light_targets(calls, "turn_off")
    assert hass.states.get(REAL).state == "off"
    assert hass.states.get("light.inner").state == "off"
    assert _state(hass).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE
    assert _attrs(hass)["last_on_physical"] is None


# ---------------------------------------------------------------------------
# Members whose power is not known when the light seeds
# ---------------------------------------------------------------------------

REAL2 = "light.real_2"
HOLD = "input_boolean.keep_on"
RED = (0.0, 100.0)

# How a member that is not readable when the light seeds can look.
UNKNOWN = ["placeholder", "unavailable", "absent"]


def _turn_ons(calls: list[dict]) -> list[dict]:
    """Service data of each recorded light.turn_on sent to real lights."""
    return [
        call["service_data"]
        for call in calls
        if call["domain"] == "light"
        and call["service"] == "turn_on"
        and call["service_data"]["entity_id"] != VIRTUAL
    ]


# ---------------------------------------------------------------------------
# A missed schedule end: scheduled Turn off and follow mode
# ---------------------------------------------------------------------------


def _end_entry(mode: str, members: list[str], *, hold: bool = False):
    if mode == "follow":
        return make_light_entry(
            lights=members,
            schedule=SCHED,
            schedule_mode=SCHEDULE_MODE_FOLLOW,
            hold_entities=[HOLD] if hold else None,
        )
    settings = {CONF_LIGHT_TIMEOUT: 60}
    if hold:
        settings[CONF_HOLD_ENTITIES] = [HOLD]
    return make_scheduled_light_entry(
        name="Matrix Light",
        lights=members,
        schedule=SCHED,
        schedule_end_action=SCHEDULE_END_ACTION_TURN_OFF,
        outside=settings,
        inside=settings,
    )


async def _lit_in_window(
    hass: HomeAssistant, entry: MockConfigEntry, members: list[str]
) -> _Members:
    """Set the light up inside its window with every member on."""
    real = _Members(hass, members, {})
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(HOLD, "off")
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert _state(hass).state == "on"
    assert all(hass.states.get(member).state == "on" for member in members)
    return real


async def _restart_across_end(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    real: _Members,
    boots: dict[str, str],
    *,
    hold: bool = False,
) -> list[dict]:
    """Restart with the window ended while Home Assistant was down."""
    await restart_entries(hass, entry, started=False)
    hass.states.async_set(SCHED, "off")
    if hold:
        hass.states.async_set(HOLD, "on")
    for member, how in boots.items():
        real.boot(member, how)
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    return calls


def _end_owed(hass: HomeAssistant, mode: str) -> bool:
    """Whether the saved state still carries the missed end."""
    if mode == "follow":
        return _attrs(hass)["schedule_window_start"] is not None
    return _attrs(hass)[ATTR_SCHEDULE_END_OFF_PENDING]


END_MODES = ["turn_off", "follow"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", END_MODES)
@pytest.mark.parametrize("boot", ["on", "off"])
async def test_missed_end_with_the_member_known(
    hass: HomeAssistant, mode: str, boot: str
) -> None:
    """A lit member is turned off once; one found off settles the end."""
    entry = _end_entry(mode, [REAL])
    real = await _lit_in_window(hass, entry, [REAL])
    calls = await _restart_across_end(hass, entry, real, {REAL: boot})

    assert light_targets(calls, "turn_off") == ([[REAL]] if boot == "on" else [])
    assert _state(hass).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE
    assert not _end_owed(hass, mode)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", END_MODES)
@pytest.mark.parametrize("boot", UNKNOWN)
@pytest.mark.parametrize("late", ["on", "off"])
async def test_missed_end_is_owed_to_a_member_that_loads_late(
    hass: HomeAssistant, mode: str, boot: str, late: str
) -> None:
    """An unreadable member was not proven off: the end waits for its report."""
    entry = _end_entry(mode, [REAL])
    real = await _lit_in_window(hass, entry, [REAL])
    calls = await _restart_across_end(hass, entry, real, {REAL: boot})
    assert light_targets(calls, "turn_off") == []
    assert _state(hass).state == "off"
    assert _end_owed(hass, mode)

    states = _virtual_states(hass)
    real.load(REAL, late)
    await settle(hass)
    assert light_targets(calls, "turn_off") == ([[REAL]] if late == "on" else [])
    assert hass.states.get(REAL).state == "off"
    assert "on" not in states
    assert _state(hass).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE
    assert _attrs(hass)["last_on_physical"] is None
    assert not _end_owed(hass, mode)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", END_MODES)
@pytest.mark.parametrize("boot", UNKNOWN)
@pytest.mark.parametrize("known", ["on", "off"])
@pytest.mark.parametrize("late", ["on", "off"])
async def test_missed_end_in_a_mixed_group(
    hass: HomeAssistant, mode: str, boot: str, known: str, late: str
) -> None:
    """The readable member is settled at startup, the other when it reports."""
    members = [REAL, REAL2]
    entry = _end_entry(mode, members)
    real = await _lit_in_window(hass, entry, members)
    calls = await _restart_across_end(hass, entry, real, {REAL: known, REAL2: boot})
    offs = [members] if known == "on" else []
    assert light_targets(calls, "turn_off") == offs
    assert _state(hass).state == "off"
    assert _end_owed(hass, mode)

    states = _virtual_states(hass)
    real.load(REAL2, late)
    await settle(hass)
    if late == "on":
        offs = [*offs, members]
    assert light_targets(calls, "turn_off") == offs
    assert hass.states.get(REAL2).state == "off"
    assert "on" not in states
    assert _attrs(hass)["molight_state"] == STATE_IDLE
    assert not _end_owed(hass, mode)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", END_MODES)
@pytest.mark.parametrize("boot", UNKNOWN)
async def test_held_missed_end_adopts_a_late_member_until_release(
    hass: HomeAssistant, mode: str, boot: str
) -> None:
    """Under a hold the late member stays on, and the release applies the end."""
    entry = _end_entry(mode, [REAL], hold=True)
    real = await _lit_in_window(hass, entry, [REAL])
    calls = await _restart_across_end(hass, entry, real, {REAL: boot}, hold=True)
    assert _state(hass).state == "off"
    assert _end_owed(hass, mode)

    real.load(REAL, "on")
    await settle(hass)
    assert light_targets(calls, "turn_off") == []
    assert _state(hass).state == "on"
    assert _attrs(hass)["molight_state"] == (
        STATE_SCHEDULED if mode == "follow" else STATE_ACTIVE
    )
    assert _end_owed(hass, mode)

    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert light_targets(calls, "turn_off") == [[REAL]]
    assert _state(hass).state == "off"
    assert not _end_owed(hass, mode)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", END_MODES)
async def test_held_missed_end_is_dropped_when_the_late_member_is_off(
    hass: HomeAssistant, mode: str
) -> None:
    """With every member off there is nothing left for the hold to keep."""
    entry = _end_entry(mode, [REAL], hold=True)
    real = await _lit_in_window(hass, entry, [REAL])
    calls = await _restart_across_end(
        hass, entry, real, {REAL: "placeholder"}, hold=True
    )
    real.load(REAL, "off")
    await settle(hass)
    assert not _end_owed(hass, mode)

    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert light_targets(calls, "turn_off") == []
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", END_MODES)
@pytest.mark.parametrize("by", ["virtual", "next_window"])
async def test_new_on_period_replaces_an_owed_end(
    hass: HomeAssistant, mode: str, by: str
) -> None:
    """A turn-on or the next window start is not undone by the old end."""
    entry = _end_entry(mode, [REAL])
    real = await _lit_in_window(hass, entry, [REAL])
    calls = await _restart_across_end(hass, entry, real, {REAL: "placeholder"})
    assert _end_owed(hass, mode)

    if by == "virtual":
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
        )
    else:
        hass.states.async_set(SCHED, "on", {"current_window_start": MARKER2})
    await settle(hass)
    if mode == "turn_off":
        assert not _attrs(hass)[ATTR_SCHEDULE_END_OFF_PENDING]
    else:
        assert _attrs(hass)["schedule_window_start"] == (
            MARKER2 if by == "next_window" else None
        )

    real.load(REAL, "on")
    await settle(hass)
    assert light_targets(calls, "turn_off") == []
    assert _state(hass).state == "on"
    assert hass.states.get(REAL).state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", END_MODES)
async def test_a_manual_off_leaves_the_end_owed(hass: HomeAssistant, mode: str) -> None:
    """An off through the light never reached the member still loading."""
    entry = _end_entry(mode, [REAL])
    real = await _lit_in_window(hass, entry, [REAL])
    calls = await _restart_across_end(hass, entry, real, {REAL: "placeholder"})
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert _end_owed(hass, mode)
    calls.clear()

    real.load(REAL, "on")
    await settle(hass)
    assert light_targets(calls, "turn_off") == [[REAL]]
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", END_MODES)
@pytest.mark.parametrize("how", ["restart", "crash"])
async def test_owed_end_survives_a_second_restart(
    hass: HomeAssistant, mode: str, how: str
) -> None:
    """The end is still applied when HA restarts again before the member loads."""
    entry = _end_entry(mode, [REAL])
    real = await _lit_in_window(hass, entry, [REAL])
    await _restart_across_end(hass, entry, real, {REAL: "placeholder"})
    assert _end_owed(hass, mode)

    again = restart_entries if how == "restart" else crash_entries
    await again(hass, entry, started=False)
    real.boot(REAL, "on")
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    assert light_targets(calls, "turn_off") == [[REAL]]
    assert _state(hass).state == "off"
    assert not _end_owed(hass, mode)


# ---------------------------------------------------------------------------
# A missed Switch state end
# ---------------------------------------------------------------------------


def _switch_entry() -> MockConfigEntry:
    return make_scheduled_light_entry(
        name="Matrix Light",
        schedule=SCHED,
        schedule_end_action=SCHEDULE_END_ACTION_SWITCH,
        outside={CONF_LIGHT_TIMEOUT: 60, CONF_OCCUPANCY_ENTITY: OCC},
        inside={CONF_LIGHT_TIMEOUT: 60},
    )


async def _lit_before_switch_end(hass: HomeAssistant, entry) -> _Members:
    """Lit inside the window; the outside profile's room emptied an hour ago."""
    left = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    hass.states.async_set(OCC, "off", {"latest_occupied_time": left})
    return await _lit_in_window(hass, entry, [REAL])


@pytest.mark.asyncio
@pytest.mark.parametrize("boot", ["on", "off"])
async def test_missed_switch_end_with_the_member_known(
    hass: HomeAssistant, boot: str
) -> None:
    """The outside profile's history says the light is already due off."""
    entry = _switch_entry()
    real = await _lit_before_switch_end(hass, entry)
    calls = await _restart_across_end(hass, entry, real, {REAL: boot})

    assert light_targets(calls, "turn_off") == ([[REAL]] if boot == "on" else [])
    assert _state(hass).state == "off"
    assert _attrs(hass)[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_OUTSIDE


@pytest.mark.asyncio
@pytest.mark.parametrize("boot", UNKNOWN)
@pytest.mark.parametrize("late", ["on", "off"])
async def test_missed_switch_end_is_owed_to_a_member_that_loads_late(
    hass: HomeAssistant, boot: str, late: str
) -> None:
    """A late lit member gets the recalculation, not a fresh full timeout."""
    entry = _switch_entry()
    real = await _lit_before_switch_end(hass, entry)
    calls = await _restart_across_end(hass, entry, real, {REAL: boot})
    assert light_targets(calls, "turn_off") == []
    assert _state(hass).state == "off"

    real.load(REAL, late)
    await settle(hass)
    assert light_targets(calls, "turn_off") == ([[REAL]] if late == "on" else [])
    assert hass.states.get(REAL).state == "off"
    assert _state(hass).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_new_on_period_replaces_an_owed_switch_end(hass: HomeAssistant) -> None:
    """A light turned on under the outside profile runs its normal timeout."""
    entry = _switch_entry()
    real = await _lit_before_switch_end(hass, entry)
    calls = await _restart_across_end(hass, entry, real, {REAL: "placeholder"})
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)

    real.load(REAL, "on")
    await settle(hass)
    assert light_targets(calls, "turn_off") == []
    assert _state(hass).state == "on"
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE


# ---------------------------------------------------------------------------
# A warning snapshot
# ---------------------------------------------------------------------------

WARN = 51  # 20 %


async def _restart_mid_warn(
    hass: HomeAssistant,
    freezer,
    boots: dict[str, str],
    *,
    color: bool = False,
    started: bool = True,
) -> tuple[MockConfigEntry, _Members, list[dict]]:
    """Run a light at 200 into its warn stage, then restart Home Assistant."""
    members = list(boots)
    real = _Members(hass, members, HS_CAPS if color else {})
    entry = make_light_entry(
        lights=members,
        warn_timeout=30,
        warn_brightness=20,
        warn_rgb_color=[255, 0, 0] if color else None,
    )
    await setup_entries(hass, entry)
    data = {"entity_id": VIRTUAL, "brightness": 200}
    if color:
        data["hs_color"] = BLUE
    await hass.services.async_call("light", "turn_on", data, blocking=True)
    await settle(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_WARN
    for member in members:
        assert hass.states.get(member).attributes["brightness"] == WARN

    await restart_entries(hass, entry, started=False)
    stage = {"brightness": WARN}
    if color:
        stage |= {"hs_color": RED, "color_mode": "hs"}
    for member, how in boots.items():
        real.boot(member, how, **(stage if how == "on" else {}))
    calls = record_service_calls(hass)
    if started:
        await finish_startup(hass)
        await settle(hass)
    return entry, real, calls


def _assert_restored(hass: HomeAssistant, call: dict, *, color: bool) -> None:
    """The command restores the pre-warning look, and the light reports it."""
    assert call["brightness"] == 200
    assert _state(hass).state == "on"
    assert _attrs(hass)["brightness"] == 200
    assert _attrs(hass)["warning_active"] is False
    assert _attrs(hass)["pre_warn_brightness"] is None
    if color:
        assert tuple(call["hs_color"]) == BLUE
        assert tuple(_attrs(hass)["hs_color"]) == BLUE


@pytest.mark.asyncio
@pytest.mark.parametrize("color", [False, True], ids=["brightness", "color"])
async def test_warning_restart_with_the_member_lit(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, color: bool
) -> None:
    """A member still showing the warn stage is put back as it was."""
    _, _, calls = await _restart_mid_warn(hass, freezer, {REAL: "on"}, color=color)
    (call,) = _turn_ons(calls)
    _assert_restored(hass, call, color=color)
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE


@pytest.mark.asyncio
async def test_warning_restart_with_the_member_off_stays_off(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """Members really off completed the auto-off: the room is not re-lit."""
    _, _, calls = await _restart_mid_warn(hass, freezer, {REAL: "off"})
    assert _turn_ons(calls) == []
    assert _state(hass).state == "off"
    assert _attrs(hass)["warning_active"] is False
    assert _attrs(hass)["pre_warn_brightness"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("boot", UNKNOWN)
@pytest.mark.parametrize("color", [False, True], ids=["brightness", "color"])
async def test_warning_snapshot_is_owed_to_a_member_that_loads_late(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, boot: str, color: bool
) -> None:
    """An unreadable member keeps the snapshot until it reports lit."""
    _, real, calls = await _restart_mid_warn(hass, freezer, {REAL: boot}, color=color)
    assert _turn_ons(calls) == []
    assert _state(hass).state == "off"
    assert _attrs(hass)["warning_active"] is True
    assert _attrs(hass)["pre_warn_brightness"] == 200

    stage = {"hs_color": RED, "color_mode": "hs"} if color else {}
    real.load(REAL, "on", brightness=WARN, **stage)
    await settle(hass)
    (call,) = _turn_ons(calls)
    _assert_restored(hass, call, color=color)
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert hass.states.get(REAL).attributes["brightness"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("boot", UNKNOWN)
async def test_warning_snapshot_is_dropped_when_the_late_member_is_off(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, boot: str
) -> None:
    """A late member reporting off completes the auto-off like any other."""
    _, real, calls = await _restart_mid_warn(hass, freezer, {REAL: boot})
    real.load(REAL, "off")
    await settle(hass)
    assert _turn_ons(calls) == []
    assert _state(hass).state == "off"
    assert _attrs(hass)["warning_active"] is False
    assert _attrs(hass)["pre_warn_brightness"] is None
    assert _attrs(hass)["last_off_manual"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("boot", UNKNOWN)
@pytest.mark.parametrize("known", ["on", "off"])
@pytest.mark.parametrize("color", [False, True], ids=["brightness", "color"])
async def test_warning_restart_in_a_mixed_group(
    hass: HomeAssistant,
    freezer,
    virtual_light_behavior_variant,
    boot: str,
    known: str,
    color: bool,
) -> None:
    """A late member is sent the restored look, never mirrored at the warn's."""
    _, real, calls = await _restart_mid_warn(
        hass, freezer, {REAL: known, REAL2: boot}, color=color
    )
    sent = 1 if known == "on" else 0
    assert len(_turn_ons(calls)) == sent
    assert _state(hass).state == known

    stage = {"hs_color": RED, "color_mode": "hs"} if color else {}
    real.load(REAL2, "on", brightness=WARN, **stage)
    await settle(hass)
    assert len(_turn_ons(calls)) == sent + 1
    _assert_restored(hass, _turn_ons(calls)[-1], color=color)
    for member in (REAL, REAL2):
        assert hass.states.get(member).attributes["brightness"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("color", [False, True], ids=["brightness", "color"])
async def test_manual_turn_on_while_a_member_loads_sets_what_it_is_sent(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, color: bool
) -> None:
    """The late member gets the new on-period's brightness, not the warn's."""
    _, real, calls = await _restart_mid_warn(
        hass, freezer, {REAL: "placeholder"}, color=color
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 128}, blocking=True
    )
    await settle(hass)
    calls.clear()

    stage = {"hs_color": RED, "color_mode": "hs"} if color else {}
    real.load(REAL, "on", brightness=WARN, **stage)
    await settle(hass)
    (call,) = _turn_ons(calls)
    assert call["brightness"] == 128
    assert _attrs(hass)["brightness"] == 128
    if color:
        assert tuple(call["hs_color"]) == BLUE
        assert tuple(_attrs(hass)["hs_color"]) == BLUE


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["restart", "crash"])
async def test_owed_warning_snapshot_survives_a_second_restart(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, how: str
) -> None:
    """The snapshot is still restored when HA restarts again before the member loads."""
    entry, real, _ = await _restart_mid_warn(hass, freezer, {REAL: "placeholder"})
    assert _attrs(hass)["warning_active"] is True

    again = restart_entries if how == "restart" else crash_entries
    await again(hass, entry, started=False)
    real.boot(REAL, "on", brightness=WARN)
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    (call,) = _turn_ons(calls)
    _assert_restored(hass, call, color=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("boot", ["off", *UNKNOWN])
async def test_restart_during_a_blink_off_stays_off(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, boot: str
) -> None:
    """Members blinked off by the effect stage stay off, readable or late."""
    real = _Members(hass, [REAL], {})
    entry = make_light_entry(effect_timeout=30, effect_brightness=0, warn_timeout=30)
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
    )
    await settle(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(REAL).state == "off"

    await restart_entries(hass, entry, started=False)
    real.boot(REAL, boot)
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    real.load(REAL, "off")
    await settle(hass)
    assert _turn_ons(calls) == []
    assert _state(hass).state == "off"
    assert _attrs(hass)["warning_active"] is False
    assert _attrs(hass)["pre_warn_brightness"] is None


# ---------------------------------------------------------------------------
# A command made before the seed is newer than what the restart missed
# ---------------------------------------------------------------------------


async def _tick(hass: HomeAssistant, freezer, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


async def _restart_then_turn_on(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    real: _Members,
    boot: str,
    *,
    hold: bool = False,
) -> list[dict]:
    """Restart across the window end and turn the light on before it seeds."""
    await restart_entries(hass, entry, started=False)
    hass.states.async_set(SCHED, "off")
    if hold:
        hass.states.async_set(HOLD, "on")
    real.boot(REAL, boot)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
    )
    await settle(hass)
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [*END_MODES, "switch"])
async def test_turn_on_before_the_seed_replaces_a_missed_end(
    hass: HomeAssistant, freezer, mode: str
) -> None:
    """A light lit on purpose while HA starts runs its normal timeout."""
    if mode == "switch":
        entry = _switch_entry()
        real = await _lit_before_switch_end(hass, entry)
    else:
        entry = _end_entry(mode, [REAL])
        real = await _lit_in_window(hass, entry, [REAL])
    calls = await _restart_then_turn_on(hass, entry, real, "off")

    assert light_targets(calls, "turn_off") == []
    assert hass.states.get(REAL).state == "on"
    assert _state(hass).state == "on"
    assert _attrs(hass)["brightness"] == 200
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    if mode != "switch":
        assert not _end_owed(hass, mode)

    await _tick(hass, freezer, 59)
    assert _state(hass).state == "on"
    await _tick(hass, freezer, 2)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", END_MODES)
@pytest.mark.parametrize("boot", UNKNOWN)
@pytest.mark.parametrize("late", ["on", "off"])
async def test_turn_on_before_the_seed_leaves_no_end_owed_to_a_late_member(
    hass: HomeAssistant, mode: str, boot: str, late: str
) -> None:
    """A member that loads later is not turned off for the old window."""
    entry = _end_entry(mode, [REAL])
    real = await _lit_in_window(hass, entry, [REAL])
    calls = await _restart_then_turn_on(hass, entry, real, boot)
    assert _state(hass).state == "on"
    assert not _end_owed(hass, mode)

    real.load(REAL, late)
    await settle(hass)
    # Follow mode matches a member back from unavailable to its schedule.
    rebooted = mode == "follow" and boot == "unavailable"
    assert light_targets(calls, "turn_off") == (
        [[REAL]] if rebooted and late == "on" else []
    )
    assert hass.states.get(REAL).state == ("off" if rebooted else "on")
    assert _state(hass).state == ("off" if rebooted else "on")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", END_MODES)
async def test_turn_on_before_the_seed_replaces_a_missed_end_under_a_hold(
    hass: HomeAssistant, freezer, mode: str
) -> None:
    """Releasing the hold starts the new on-period's timeout, not the old off."""
    entry = _end_entry(mode, [REAL], hold=True)
    real = await _lit_in_window(hass, entry, [REAL])
    calls = await _restart_then_turn_on(hass, entry, real, "on", hold=True)
    assert _state(hass).state == "on"
    assert not _end_owed(hass, mode)

    hass.states.async_set(HOLD, "off")
    await settle(hass)
    assert light_targets(calls, "turn_off") == []
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_turn_off_before_the_seed_stands_over_a_missed_window_start(
    hass: HomeAssistant,
) -> None:
    """A follow window that started while HA was down does not undo the off."""
    _Members(hass, [REAL], {})
    hass.states.async_set(SCHED, "off")
    entry = _end_entry("follow", [REAL])
    await setup_entries(hass, entry)

    await restart_entries(hass, entry, started=False)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    assert _turn_ons(calls) == []
    assert _state(hass).state == "off"
    assert _attrs(hass)["schedule_window_start"] == MARKER

    # The window is handled: turning the light on again rejoins it.
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_SCHEDULED


@pytest.mark.asyncio
async def test_turn_on_before_the_seed_joins_a_missed_window_start(
    hass: HomeAssistant,
) -> None:
    """The window claims the light as it was lit, without sending its own look."""
    _Members(hass, [REAL], {})
    hass.states.async_set(SCHED, "off")
    entry = _end_entry("follow", [REAL])
    await setup_entries(hass, entry)

    await restart_entries(hass, entry, started=False)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL, "brightness": 200}, blocking=True
    )
    await settle(hass)
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    assert _turn_ons(calls) == []
    assert _attrs(hass)["brightness"] == 200
    assert _attrs(hass)["molight_state"] == STATE_SCHEDULED
    assert _attrs(hass)["schedule_window_start"] == MARKER


@pytest.mark.asyncio
async def test_turn_off_before_the_seed_stands_over_a_missed_standby_start(
    hass: HomeAssistant,
) -> None:
    """A window that started while HA was down does not light standby over it."""
    _Members(hass, [REAL], {})
    hass.states.async_set(SCHED, "off")
    entry = _standby_entry()
    await setup_entries(hass, entry)
    assert _state(hass).state == "off"

    await restart_entries(hass, entry, started=False)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    calls = record_service_calls(hass)
    await finish_startup(hass)
    await settle(hass)
    assert _turn_ons(calls) == []
    assert _state(hass).state == "off"
    assert _attrs(hass)[ATTR_ACTIVE_SETTINGS] == ACTIVE_SETTINGS_INSIDE
    assert _attrs(hass)[ATTR_STANDBY_SUPPRESSED] is True

    # As with any manual off, the next boundary brings standby back.
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER2})
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_STANDBY


# ---------------------------------------------------------------------------
# An entry set up once Home Assistant runs, as when it is enabled
# ---------------------------------------------------------------------------

SWITCH = "switch.matrix_light_auto_off"
ILLUM = "binary_sensor.illum"


async def _auto_off(hass: HomeAssistant, on: bool) -> None:
    await hass.services.async_call(
        "switch", "turn_on" if on else "turn_off", {"entity_id": SWITCH}, blocking=True
    )
    await settle(hass)


async def _set_up_late(hass: HomeAssistant, entry: MockConfigEntry) -> list[dict]:
    """Set a stopped entry up with Home Assistant running; it seeds at once."""
    calls = record_service_calls(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [*END_MODES, "switch"])
async def test_late_setup_keeps_a_missed_end_under_the_saved_auto_off_hold(
    hass: HomeAssistant, freezer, mode: str
) -> None:
    """The Auto-off switch left off holds the end until it is turned on."""
    if mode == "switch":
        entry = _switch_entry()
        real = await _lit_before_switch_end(hass, entry)
    else:
        entry = _end_entry(mode, [REAL])
        real = await _lit_in_window(hass, entry, [REAL])
    await _auto_off(hass, on=False)
    await stop_entries(hass, entry)
    hass.states.async_set(SCHED, "off")
    calls = await _set_up_late(hass, entry)

    assert hass.states.get(SWITCH).state == "off"
    assert light_targets(calls, "turn_off") == []
    assert _state(hass).state == "on"
    assert _attrs(hass)["auto_off_held"] is True
    assert real.reachable == {REAL}

    await _auto_off(hass, on=True)
    if mode == "switch":
        # A recalculation the hold suppressed: the release runs a full timeout.
        assert _state(hass).state == "on"
        await _tick(hass, freezer, 61)
    assert light_targets(calls, "turn_off") == [[REAL]]
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_late_setup_keeps_a_held_gate_end_under_the_saved_auto_off_hold(
    hass: HomeAssistant,
) -> None:
    """A Gate and turn off end the switch held stays held when set up late."""
    _Members(hass, [REAL], {})
    hass.states.async_set(SCHED, "on")
    entry = make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_GATE)
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await _auto_off(hass, on=False)
    hass.states.async_set(SCHED, "off")
    await settle(hass)
    assert _attrs(hass)[ATTR_SCHEDULE_END_OFF_PENDING] is True

    await stop_entries(hass, entry)
    calls = await _set_up_late(hass, entry)
    assert light_targets(calls, "turn_off") == []
    assert _state(hass).state == "on"
    assert _attrs(hass)[ATTR_SCHEDULE_END_OFF_PENDING] is True

    await _auto_off(hass, on=True)
    assert light_targets(calls, "turn_off") == [[REAL]]


@pytest.mark.asyncio
async def test_late_setup_keeps_standby_on_while_bright_under_the_saved_hold(
    hass: HomeAssistant,
) -> None:
    """Brightness in Control mode does not turn a held standby light off."""
    _Members(hass, [REAL], {})
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(ILLUM, "off")
    entry = make_scheduled_light_entry(
        name="Matrix Light",
        schedule=SCHED,
        outside={CONF_LIGHT_TIMEOUT: 60},
        inside={
            CONF_LIGHT_TIMEOUT: 60,
            CONF_STANDBY_BRIGHTNESS: 20,
            CONF_ILLUMINANCE_ENTITY: ILLUM,
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
        },
    )
    await setup_entries(hass, entry)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_STANDBY
    await _auto_off(hass, on=False)

    await stop_entries(hass, entry)
    hass.states.async_set(ILLUM, "on")
    calls = await _set_up_late(hass, entry)
    assert light_targets(calls, "turn_off") == []
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_late_setup_with_auto_off_enabled_applies_a_missed_end(
    hass: HomeAssistant,
) -> None:
    """Without a saved hold the end is applied as the entry is set up."""
    entry = _end_entry("follow", [REAL])
    await _lit_in_window(hass, entry, [REAL])
    await stop_entries(hass, entry)
    hass.states.async_set(SCHED, "off")
    calls = await _set_up_late(hass, entry)
    assert light_targets(calls, "turn_off") == [[REAL]]
    assert _state(hass).state == "off"
