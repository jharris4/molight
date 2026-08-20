"""Options-edit (entry reload) tests for the MoLight Virtual Light.

Editing a virtual light's options reloads its config entry, which tears the
entity down and re-creates it mid-flight. These tests pin what happens when
that reload lands while the light is in each running state: the re-created
entity re-seeds from the real lights and sensors exactly like a restart, and
the new option values take effect immediately.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, callback
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_ENTITY_TYPE,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHT_TIMEOUT,
    CONF_OCCUPANCY_ENTITY,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_SCHEDULE_ENTITY,
    ENTITY_TYPE_SCHEDULED_LIGHT,
    SCHEDULE_MODE_FOLLOW,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
    STATE_WARN,
)
from tests.conftest import make_light_entry, settle, setup_entries

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

OCC = "binary_sensor.occ"
SCHED = "binary_sensor.sched"
REAL = "light.real_1"
VIRTUAL = "light.matrix_light"
MARKER = "2026-07-02T21:00:00+00:00"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


def _mstate(hass: HomeAssistant) -> str:
    return _state(hass).attributes["molight_state"]


async def _update_options(hass: HomeAssistant, entry, **overrides) -> None:
    """Simulate the options flow: full config stored as options, then reload."""
    opts = {k: v for k, v in entry.data.items() if k != CONF_ENTITY_TYPE}
    if entry.data[CONF_ENTITY_TYPE] == ENTITY_TYPE_SCHEDULED_LIGHT:
        settings_key = (
            CONF_INSIDE_SCHEDULE_SETTINGS
            if hass.states.is_state(entry.data[CONF_SCHEDULE_ENTITY], "on")
            else CONF_OUTSIDE_SCHEDULE_SETTINGS
        )
        opts[settings_key] = {**opts[settings_key], **overrides}
    else:
        opts.update(overrides)
    # The options flow drops cleared (None) fields entirely.
    opts = {k: v for k, v in opts.items() if v is not None}
    hass.config_entries.async_update_entry(entry, options=opts)
    await hass.async_block_till_done()
    await settle(hass)


@pytest.mark.asyncio
async def test_options_reload_while_active_applies_new_timeout(
    hass: HomeAssistant, freezer
) -> None:
    """Editing options mid-ACTIVE keeps the light on and arms a fresh timer
    with the new timeout."""
    entry = make_light_entry(timeout=60)
    hass.states.async_set(REAL, "on", {"brightness": 200})
    await setup_entries(hass, entry)
    await settle(hass)
    assert _mstate(hass) == STATE_ACTIVE

    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await settle(hass)
    await _update_options(hass, entry, **{CONF_LIGHT_TIMEOUT: 10})

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["brightness"] == 200

    # The new 10s timeout applies from the reload.
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_options_reload_while_occupied_stays_occupied(
    hass: HomeAssistant, freezer
) -> None:
    """Editing options mid-OCCUPIED re-adopts the occupied room, and the new
    auto-on brightness applies to the next automatic turn-on."""
    entry = make_light_entry(occupancy=OCC)
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    assert _mstate(hass) == STATE_OCCUPIED
    # The real light responds (external event; already OCCUPIED, no change).
    hass.states.async_set(REAL, "on")
    await settle(hass)

    await _update_options(hass, entry, **{CONF_AUTO_ON_BRIGHTNESS: 40})

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED

    # The cycle still completes normally after the reload.
    hass.states.async_set(OCC, "off")
    await settle(hass)
    assert _mstate(hass) == STATE_COUNTDOWN
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"

    # The next occupancy turn-on uses the new auto-on brightness (40% → 102).
    hass.states.async_set(OCC, "on")
    await settle(hass)
    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["brightness"] == 102


@pytest.mark.asyncio
async def test_options_reload_removing_occupancy_while_occupied(
    hass: HomeAssistant, freezer
) -> None:
    """Deleting the occupancy reference while OCCUPIED demotes the lit light
    to a plain ACTIVE timer instead of holding it on forever."""
    entry = make_light_entry(occupancy=OCC)
    hass.states.async_set(OCC, "off")
    await setup_entries(hass, entry)

    hass.states.async_set(OCC, "on")
    await settle(hass)
    hass.states.async_set(REAL, "on")
    await settle(hass)
    assert _mstate(hass) == STATE_OCCUPIED

    await _update_options(hass, entry, **{CONF_OCCUPANCY_ENTITY: None})

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE

    # Occupancy is no longer watched; the timer turns the light off.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_options_reload_while_warn_adopts_active(
    hass: HomeAssistant, freezer
) -> None:
    """A reload landing during the warn grace re-adopts the lit lights as
    ACTIVE (the warning is forgotten) and the new timeout applies."""
    entry = make_light_entry(timeout=60, warn_timeout=30, warn_brightness=100)
    hass.states.async_set(REAL, "on", {"brightness": 200})
    await setup_entries(hass, entry)
    await settle(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN

    await _update_options(hass, entry, **{CONF_LIGHT_TIMEOUT: 10})

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE

    # New 10s timer → warn stage again → off.
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_WARN
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_options_reload_while_effect_blink_off_goes_idle(
    hass: HomeAssistant, freezer
) -> None:
    """A reload landing during the effect blink-off finds the real lights off
    and seeds IDLE — the auto-off effectively completed early."""
    contexts = []

    @callback
    def _capture(event) -> None:
        if (
            event.data["domain"] == "light"
            and event.data["service"] == "turn_off"
            and REAL in event.data["service_data"].get("entity_id", [])
        ):
            contexts.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _capture)

    entry = make_light_entry(effect_timeout=30, effect_brightness=0, warn_timeout=30)
    hass.states.async_set(REAL, "on", {"brightness": 200})
    await setup_entries(hass, entry)
    await settle(hass)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _mstate(hass) == STATE_EFFECT

    # The real light confirms the blink-off (same context → not an external
    # off, the virtual light stays logically on in EFFECT).
    assert contexts
    hass.states.async_set(REAL, "off", context=contexts[-1])
    await settle(hass)
    assert _state(hass).state == "on"
    assert _mstate(hass) == STATE_EFFECT

    await _update_options(hass, entry, **{CONF_LIGHT_TIMEOUT: 60})

    state = _state(hass)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_options_reload_while_scheduled_stays_scheduled(
    hass: HomeAssistant, freezer
) -> None:
    """Editing options during an active follow window re-adopts SCHEDULED
    without re-running a timer."""
    entry = make_light_entry(schedule=SCHED, schedule_mode=SCHEDULE_MODE_FOLLOW)
    hass.states.async_set(SCHED, "on", {"current_window_start": MARKER})
    hass.states.async_set(REAL, "on")
    await setup_entries(hass, entry)
    await settle(hass)
    assert _mstate(hass) == STATE_SCHEDULED

    await _update_options(hass, entry, **{CONF_LIGHT_TIMEOUT: 10})

    state = _state(hass)
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["schedule_window_start"] == MARKER

    # No timer runs while SCHEDULED, whatever the new timeout says.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"
