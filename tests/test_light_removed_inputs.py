"""Entities a Virtual Light uses that are deleted or disabled while it runs.

Such an entity leaves the state machine and can never report again. Startup
counts a missing keep-on entity as not holding, a missing door as closed,
missing presence as clear and a missing real light as not there; the same
has to hold when the entity goes missing at runtime, or it would keep its
last value until the next restart.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.helpers import entity_registry as er
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
    DOOR_MODE_OPEN_CLOSE,
    ENTITY_TYPE_OCCUPANCY,
    SCHEDULE_MODE_FOLLOW,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_OCCUPIED,
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
from tests.real_entities import (
    RealBinary,
    RealLight,
    RealToggle,
    add_real,
    start_slow_rename,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

VIRTUAL = "light.matrix_light"
MEMBER = "light.real_1"
INPUT = "binary_sensor.input"
# How an entity leaves: its registry entry deleted or disabled, or, for an
# entity that never had one, its state removed.
GONE = ["deleted", "disabled", "unregistered"]


async def provide(
    hass: HomeAssistant, how: str, entity: RealBinary | RealToggle | RealLight
) -> None:
    """Add the entity for real, or as a bare state when it is to be unregistered."""
    if how != "unregistered":
        await add_real(hass, entity)
    elif isinstance(entity, RealLight):
        hass.states.async_set(
            entity.entity_id,
            "on" if entity.is_on else "off",
            {"brightness": entity.brightness} if entity.is_on else {},
        )
    else:
        hass.states.async_set(entity.entity_id, "on" if entity.is_on else "off")


async def remove(hass: HomeAssistant, how: str, entity_id: str) -> None:
    """Make the entity leave Home Assistant."""
    registry = er.async_get(hass)
    if how == "deleted":
        registry.async_remove(entity_id)
    elif how == "disabled":
        registry.async_update_entity(
            entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
    else:
        hass.states.async_remove(entity_id)
    await settle(hass)
    assert hass.states.get(entity_id) is None


async def tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


def attrs(hass: HomeAssistant) -> dict:
    return hass.states.get(VIRTUAL).attributes


async def turn_on(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(MEMBER, "on", {"brightness": 255})
    await settle(hass)


async def expect_fresh_timeout(hass: HomeAssistant, freezer) -> None:
    """The light, no longer held, runs one full timeout of 60 s and turns off."""
    await tick(hass, freezer, 59)
    assert hass.states.get(VIRTUAL).state == "on"
    await tick(hass, freezer, 2)
    assert hass.states.get(VIRTUAL).state == "off"
    assert attrs(hass)["last_off_manual"] is None


@pytest.mark.parametrize("how", GONE)
async def test_keep_on_entity_that_goes_missing_stops_holding(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, how: str
) -> None:
    """A keep-on entity that was on when it left no longer holds the light."""
    await provide(hass, how, RealBinary("input", on=True))
    hass.states.async_set(MEMBER, "off")
    await setup_entries(hass, make_light_entry(hold_entities=[INPUT], timeout=60))
    await turn_on(hass)
    assert attrs(hass)["auto_off_held"] is True
    await tick(hass, freezer, 3600)
    assert hass.states.get(VIRTUAL).state == "on"

    await remove(hass, how, INPUT)
    assert attrs(hass)["auto_off_held"] is False
    assert attrs(hass)["molight_state"] == STATE_ACTIVE
    await expect_fresh_timeout(hass, freezer)


@pytest.mark.parametrize("outage", [False, True], ids=["open", "unavailable"])
@pytest.mark.parametrize("how", GONE)
async def test_open_door_that_goes_missing_counts_as_closed(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, how: str, outage: bool
) -> None:
    """An open_close door that left while open stops holding the light, also
    when it left during an outage that would have counted it closed later."""
    await provide(hass, how, RealBinary("input"))
    hass.states.async_set(MEMBER, "off")
    await setup_entries(
        hass,
        make_light_entry(door=INPUT, door_mode=DOOR_MODE_OPEN_CLOSE, timeout=60),
    )
    hass.states.async_set(INPUT, "on")
    await settle(hass)
    hass.states.async_set(MEMBER, "on", {"brightness": 255})
    await settle(hass)
    assert attrs(hass)["molight_state"] == STATE_OCCUPIED
    if outage:
        hass.states.async_set(INPUT, "unavailable")
        await settle(hass)
        await tick(hass, freezer, 30)
    else:
        await tick(hass, freezer, 3600)

    await remove(hass, how, INPUT)
    assert attrs(hass)["molight_state"] == STATE_ACTIVE
    await expect_fresh_timeout(hass, freezer)


@pytest.mark.parametrize("how", GONE)
@pytest.mark.parametrize("role", ["occupancy", "maintain"])
async def test_presence_sensor_that_goes_missing_counts_as_clear(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, how: str, role: str
) -> None:
    """An occupancy or maintain sensor that left while on stops holding the light.

    The person may still be there, so the light gets a full timeout, as a
    restart would give it.
    """
    await provide(hass, how, RealBinary("input", on=True))
    hass.states.async_set(MEMBER, "off")
    await setup_entries(hass, make_light_entry(**{role: INPUT}, timeout=60))
    await turn_on(hass)
    assert attrs(hass)["molight_state"] == STATE_OCCUPIED
    await tick(hass, freezer, 3600)

    await remove(hass, how, INPUT)
    assert attrs(hass)["molight_state"] == STATE_ACTIVE
    await expect_fresh_timeout(hass, freezer)


async def test_presence_that_remains_keeps_holding_when_another_goes_missing(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """Only the missing sensor stops counting: the maintain sensor still holds."""
    hass.states.async_set(INPUT, "on")
    hass.states.async_set("binary_sensor.desk", "on")
    hass.states.async_set(MEMBER, "off")
    await setup_entries(
        hass,
        make_light_entry(occupancy=INPUT, maintain="binary_sensor.desk", timeout=60),
    )
    await settle(hass)
    assert attrs(hass)["molight_state"] == STATE_OCCUPIED

    await remove(hass, "unregistered", INPUT)
    await tick(hass, freezer, 3600)
    assert attrs(hass)["molight_state"] == STATE_OCCUPIED

    hass.states.async_set("binary_sensor.desk", "off")
    await settle(hass)
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"


async def test_occupancy_sensor_that_comes_back_is_read_again(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """A sensor enabled again, still on, holds the light as when first seen."""
    hass.states.async_set(INPUT, "on")
    hass.states.async_set(MEMBER, "off")
    await setup_entries(hass, make_light_entry(occupancy=INPUT, timeout=60))
    await settle(hass)
    await remove(hass, "unregistered", INPUT)
    assert attrs(hass)["molight_state"] == STATE_ACTIVE

    hass.states.async_set(INPUT, "on")
    await settle(hass)
    assert attrs(hass)["molight_state"] == STATE_OCCUPIED
    await tick(hass, freezer, 3600)
    assert hass.states.get(VIRTUAL).state == "on"


async def test_registered_entity_without_a_state_keeps_its_last_value(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """An enabled, registered entity that lost its state may yet load again."""
    er.async_get(hass).async_get_or_create(
        "binary_sensor", "test", "input", suggested_object_id="input"
    )
    hass.states.async_set(INPUT, "on")
    hass.states.async_set(MEMBER, "off")
    await setup_entries(hass, make_light_entry(hold_entities=[INPUT], timeout=60))
    await turn_on(hass)

    hass.states.async_remove(INPUT)
    await settle(hass)
    assert attrs(hass)["auto_off_held"] is True
    await tick(hass, freezer, 3600)
    assert hass.states.get(VIRTUAL).state == "on"


@pytest.mark.parametrize("role", ["hold", "door", "occupancy", "maintain"])
async def test_renamed_entity_that_is_slow_to_come_back_has_not_gone_missing(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, role: str
) -> None:
    """An entity between its old ID and its new one still holds the light."""
    sensor = RealBinary("input", on=True)
    await add_real(hass, sensor)
    hass.states.async_set(MEMBER, "on", {"brightness": 255})
    await setup_entries(
        hass,
        make_light_entry(
            timeout=60,
            **{
                "hold": {"hold_entities": [INPUT]},
                "door": {"door": INPUT, "door_mode": DOOR_MODE_OPEN_CLOSE},
                "occupancy": {"occupancy": INPUT},
                "maintain": {"maintain": INPUT},
            }[role],
        ),
    )
    await settle(hass)
    before = dict(attrs(hass))
    assert before["auto_off_held"] is (role == "hold")
    assert before["molight_state"] == (
        STATE_ACTIVE if role == "hold" else STATE_OCCUPIED
    )

    await start_slow_rename(hass, sensor, "binary_sensor.renamed")
    assert hass.states.get(INPUT) is None
    assert dict(attrs(hass)) == before

    sensor.added_gate.set()
    await settle(hass)
    assert attrs(hass)["auto_off_held"] is before["auto_off_held"]
    assert attrs(hass)["molight_state"] == before["molight_state"]
    await tick(hass, freezer, 3600)
    assert hass.states.get(VIRTUAL).state == "on"


# ---------------------------------------------------------------------------
# Real lights
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["deleted", "disabled"])
async def test_missing_real_light_does_not_block_an_off_at_the_wall(
    hass: HomeAssistant, virtual_light_behavior_variant, how: str
) -> None:
    """Turning the last real light off turns the light off: the other is gone."""
    await add_real(hass, RealLight("real_2", on=True))
    hass.states.async_set(MEMBER, "on", {"brightness": 200})
    await setup_entries(
        hass, make_light_entry(lights=[MEMBER, "light.real_2"], timeout=600)
    )
    await settle(hass)

    await remove(hass, how, "light.real_2")
    assert hass.states.get(VIRTUAL).state == "on"
    hass.states.async_set(MEMBER, "off")
    await settle(hass)
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes["last_off_manual"] is not None


@pytest.mark.parametrize("how", GONE)
async def test_light_whose_last_lit_real_light_goes_missing_is_off(
    hass: HomeAssistant, virtual_light_behavior_variant, how: str
) -> None:
    """With its only lit real light gone, the light is off, not turned off.

    The other real light is not commanded, and no manual off is recorded.
    """
    await provide(hass, how, RealLight("real_2", on=True))
    hass.states.async_set(MEMBER, "off")
    hass.states.async_set(INPUT, "on")
    await setup_entries(
        hass,
        make_light_entry(lights=[MEMBER, "light.real_2"], maintain=INPUT, timeout=60),
    )
    await settle(hass)
    assert attrs(hass)["molight_state"] == STATE_OCCUPIED

    calls = record_service_calls(hass)
    await remove(hass, how, "light.real_2")
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["last_off_manual"] is None
    assert light_targets(calls, "turn_off") == light_targets(calls, "turn_on") == []


@pytest.mark.parametrize("how", GONE)
async def test_light_stays_on_when_a_dark_or_spare_real_light_goes_missing(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, how: str
) -> None:
    """A real light leaving changes nothing while another is still lit."""
    await provide(hass, how, RealLight("real_2", on=True))
    await provide(hass, how, RealLight("real_3"))
    hass.states.async_set(MEMBER, "on", {"brightness": 200})
    await setup_entries(
        hass,
        make_light_entry(lights=[MEMBER, "light.real_2", "light.real_3"], timeout=60),
    )
    await settle(hass)

    await tick(hass, freezer, 30)
    await remove(hass, how, "light.real_2")
    await remove(hass, how, "light.real_3")
    assert attrs(hass)["molight_state"] == STATE_ACTIVE
    # The running timer was left alone.
    await tick(hass, freezer, 31)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.regular_virtual_light_only
async def test_real_light_going_missing_does_not_cancel_a_blink_off(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """Real lights dark for an effect stage are not a light that went off."""
    first = RealLight("real_1", on=True)
    second = RealLight("real_2", on=True)
    await add_real(hass, first, second)
    await setup_entries(
        hass,
        make_light_entry(
            lights=[MEMBER, "light.real_2"],
            timeout=60,
            effect_timeout=5,
            effect_brightness=0,
            warn_timeout=5,
        ),
    )
    await settle(hass)
    await tick(hass, freezer, 61)
    assert attrs(hass)["molight_state"] == "effect"
    assert not first.is_on
    assert not second.is_on

    await remove(hass, "deleted", "light.real_2")
    assert attrs(hass)["molight_state"] == "effect"
    await tick(hass, freezer, 6)
    assert attrs(hass)["molight_state"] == "warn"
    assert first.is_on


@pytest.mark.regular_virtual_light_only
async def test_follow_light_whose_real_light_goes_missing_stays_off_in_its_window(
    hass: HomeAssistant, virtual_light_behavior_variant
) -> None:
    """Inside a follow window too, and the remaining real light is not re-lit."""
    marker = "2026-01-14T09:00:00-08:00"
    hass.states.async_set("binary_sensor.sched", "on", {"current_window_start": marker})
    hass.states.async_set(MEMBER, "off")
    hass.states.async_set("light.real_2", "on", {"brightness": 200})
    await setup_entries(
        hass,
        make_light_entry(
            lights=[MEMBER, "light.real_2"],
            schedule="binary_sensor.sched",
            schedule_mode=SCHEDULE_MODE_FOLLOW,
        ),
    )
    await settle(hass)
    calls = record_service_calls(hass)

    await remove(hass, "unregistered", "light.real_2")
    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes["schedule_window_start"] == marker
    assert light_targets(calls, "turn_on") == []


async def test_disabled_real_light_is_owed_nothing_after_a_restart(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """A warning interrupted by a restart is not kept open for a disabled light.

    A real light that cannot be read at startup is owed the pre-warning look
    until it reports; one that is disabled never will.
    """
    first = RealLight("real_1", on=True, brightness=200)
    second = RealLight("real_2", on=True, brightness=200)
    await add_real(hass, first, second)
    entry = make_light_entry(
        lights=[MEMBER, "light.real_2"], timeout=60, warn_timeout=30, warn_brightness=20
    )
    await setup_entries(hass, entry)
    await settle(hass)
    await tick(hass, freezer, 61)
    assert attrs(hass)["molight_state"] == "warn"

    # While Home Assistant is down, one real light is turned off and the
    # other leaves for good.
    await restart_entries(hass, entry, started=False)
    assert attrs(hass)["warning_active"] is True
    await first.async_turn_off()
    await remove(hass, "disabled", "light.real_2")
    await finish_startup(hass)
    await settle(hass)

    state = hass.states.get(VIRTUAL)
    assert state.state == "off"
    assert state.attributes["warning_active"] is False
    assert state.attributes["pre_warn_brightness"] is None


async def test_occupancy_sensor_that_comes_back_lights_an_off_light(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """Presence shown by a sensor enabled again is new to a light that went off."""
    member = RealLight("real_1")
    await add_real(hass, member)
    hass.states.async_set(INPUT, "on")
    await setup_entries(hass, make_light_entry(occupancy=INPUT, timeout=60))
    await settle(hass)
    assert member.is_on
    await remove(hass, "unregistered", INPUT)
    await tick(hass, freezer, 61)
    assert hass.states.get(VIRTUAL).state == "off"
    assert not member.is_on

    hass.states.async_set(INPUT, "on")
    await settle(hass)
    assert attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert member.is_on


async def test_real_light_still_owed_a_restore_goes_missing(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant
) -> None:
    """What a restart owes a real light that had not loaded ends when it is deleted."""
    first = RealLight("real_1", on=True, brightness=200)
    second = RealLight("real_2", on=True, brightness=200)
    await add_real(hass, first, second)
    entry = make_light_entry(
        lights=[MEMBER, "light.real_2"], timeout=60, warn_timeout=30, warn_brightness=20
    )
    await setup_entries(hass, entry)
    await settle(hass)
    await tick(hass, freezer, 61)
    assert attrs(hass)["molight_state"] == "warn"

    await restart_entries(hass, entry, started=False)
    await first.async_turn_off()
    second._attr_available = False
    second.async_write_ha_state()
    await finish_startup(hass)
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "off"
    assert attrs(hass)["warning_active"] is True

    await remove(hass, "deleted", "light.real_2")
    assert attrs(hass)["warning_active"] is False
    assert attrs(hass)["pre_warn_brightness"] is None


async def test_real_light_going_missing_does_not_cancel_a_waiting_resend(
    hass: HomeAssistant, virtual_light_behavior_variant
) -> None:
    """A turn-on waiting for its selection still lights the lights that remain.

    One real light comes back dark and is being sent the settings again,
    behind a slow select call, when the lit one is deleted.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def select_option(call) -> None:
        started.set()
        await release.wait()

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("select.preset", "Cozy", {"options": ["Cozy"]})
    first = RealLight("real_1", on=True)
    second = RealLight("real_2", on=True)
    await add_real(hass, first, second)
    await setup_entries(
        hass,
        make_light_entry(
            lights=[MEMBER, "light.real_2"],
            timeout=60,
            turn_on_select_entity="select.preset",
            turn_on_select_option="Cozy",
        ),
    )
    await settle(hass)
    assert attrs(hass)["molight_state"] == STATE_ACTIVE

    # The second real light reboots and comes back dark.
    second._attr_available = False
    second.async_write_ha_state()
    await settle(hass)
    second._attr_available = True
    second._attr_is_on = False
    second.async_write_ha_state()
    await asyncio.wait_for(started.wait(), 1)

    er.async_get(hass).async_remove(MEMBER)
    for _ in range(10):
        await asyncio.sleep(0)
    assert hass.states.get(MEMBER) is None
    assert hass.states.get(VIRTUAL).state == "on"

    release.set()
    await settle(hass)
    assert second.is_on
    assert hass.states.get(VIRTUAL).state == "on"
    assert attrs(hass)["last_off_manual"] is None


@pytest.mark.parametrize("role", ["occupancy", "maintain", "hold", "door"])
async def test_sensor_whose_entry_is_disabled_stops_holding(
    hass: HomeAssistant, freezer, virtual_light_behavior_variant, role: str
) -> None:
    """Disabling a Virtual Occupancy Sensor's entry is no outage either.

    An entry that merely reloads leaves the same placeholder for a moment,
    and that one still holds the light.
    """
    motion = RealBinary("motion", on=True)
    member = RealLight("real_1", on=True)
    await add_real(hass, motion, member)
    sensor = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Room Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion",
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
    )
    presence = "binary_sensor.room_occupancy"
    reference = {
        "hold": {"hold_entities": [presence]},
        "door": {"door": presence, "door_mode": DOOR_MODE_OPEN_CLOSE},
    }.get(role, {role: presence})
    await setup_entries(hass, sensor, make_light_entry(**reference, timeout=60))
    await settle(hass)
    held = dict(attrs(hass))
    assert held["auto_off_held"] is (role == "hold")
    assert held["molight_state"] == (STATE_ACTIVE if role == "hold" else STATE_OCCUPIED)

    await hass.config_entries.async_unload(sensor.entry_id)
    await settle(hass)
    assert hass.states.get(presence).state == "unavailable"
    await hass.config_entries.async_setup(sensor.entry_id)
    await settle(hass)
    for attribute in ("auto_off_held", "molight_state"):
        assert attrs(hass)[attribute] == held[attribute]

    await hass.config_entries.async_set_disabled_by(
        sensor.entry_id, ConfigEntryDisabler.USER
    )
    await settle(hass)
    # The unloaded entry's placeholder stays, and reads like an outage.
    assert hass.states.get(presence).state == "unavailable"
    assert attrs(hass)["auto_off_held"] is False
    assert attrs(hass)["molight_state"] == STATE_ACTIVE
    await expect_fresh_timeout(hass, freezer)
    assert not member.is_on
