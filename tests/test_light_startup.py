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
    ATTR_ACTIVE_SETTINGS,
    ATTR_ACTIVE_SETTINGS_SCHEDULE,
    ATTR_ACTIVE_SETTINGS_WINDOW,
    ATTR_STANDBY_SUPPRESSED,
    CONF_LIGHT_TIMEOUT,
    CONF_OCCUPANCY_ENTITY,
    CONF_STANDBY_BRIGHTNESS,
    SCHEDULE_END_ACTION_SWITCH,
    SCHEDULE_END_ACTION_TURN_OFF,
    SCHEDULE_MODE_FOLLOW,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_STANDBY,
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
