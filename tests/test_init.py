"""Entry lifecycle tests: setup/unload plumbing and per-type platform guards."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_SENSOR,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_ON_BUTTONS_SINGLE,
    CONF_TARGET_LIGHTS,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DATA_AUTO_OFF_ENABLED,
    DATA_AUTO_OFF_KEPT,
    DATA_PLATFORMS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_REMOTE,
    ENTITY_TYPE_SCHEDULE,
    PLATFORMS_BY_ENTITY_TYPE,
    SCHEDULE_MODE_FOLLOW,
)
from tests.conftest import (
    finish_startup,
    make_light_entry,
    make_scheduled_light_entry,
    restart_entries,
    settle,
    setup_entries,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Each entry type registers exactly these entity domains, which must also be
# the platforms PLATFORMS_BY_ENTITY_TYPE forwards for it.
TYPE_ENTRIES: dict[str, tuple[dict, dict[str, int]]] = {
    ENTITY_TYPE_OCCUPANCY: (
        {
            CONF_NAME: "Init Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_init",
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
        {"binary_sensor": 1},
    ),
    ENTITY_TYPE_COMBINED_OCCUPANCY: (
        {
            CONF_NAME: "Init Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.trigger_init"],
        },
        {"binary_sensor": 1},
    ),
    ENTITY_TYPE_ILLUMINANCE: (
        {
            CONF_NAME: "Init Illuminance",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux_init",
        },
        {"binary_sensor": 1},
    ),
    ENTITY_TYPE_SCHEDULE: (
        {
            CONF_NAME: "Init Schedule",
            CONF_TIME_WINDOWS: [{"start": {"time": "07:00"}, "end": {"time": "22:00"}}],
        },
        {"binary_sensor": 1},
    ),
    ENTITY_TYPE_LIGHT: (
        {
            CONF_NAME: "Init Light",
            CONF_LIGHTS: ["light.real_init"],
            CONF_LIGHT_TIMEOUT: 60,
        },
        {"light": 1, "switch": 1},
    ),
    ENTITY_TYPE_REMOTE: (
        {
            CONF_NAME: "Init Remote",
            CONF_TARGET_LIGHTS: ["light.real_init"],
            CONF_ON_BUTTONS_SINGLE: ["event.button_init"],
        },
        {"sensor": 1},
    ),
}


@pytest.mark.parametrize("entity_type", list(TYPE_ENTRIES), ids=list(TYPE_ENTRIES))
def test_forwarded_platforms_match_registered_domains(entity_type: str) -> None:
    """Only the platforms an entry actually uses are forwarded to it."""
    assert sorted(PLATFORMS_BY_ENTITY_TYPE[entity_type]) == sorted(
        TYPE_ENTRIES[entity_type][1]
    )


def _make_entry(entity_type: str) -> MockConfigEntry:
    data, _expected = TYPE_ENTRIES[entity_type]
    return MockConfigEntry(domain=DOMAIN, data={CONF_ENTITY_TYPE: entity_type, **data})


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_type", list(TYPE_ENTRIES), ids=list(TYPE_ENTRIES))
async def test_entry_registers_exactly_its_own_entities(
    hass: HomeAssistant, entity_type: str
) -> None:
    """An entry registers its own entities and no others (no stray Auto-off
    switch on a sensor entry, no Last Action sensor on a light entry, ...)."""
    entry = _make_entry(entity_type)
    await setup_entries(hass, entry)

    registry = er.async_get(hass)
    domains = Counter(
        e.domain for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    )
    assert domains == Counter(TYPE_ENTRIES[entity_type][1])


@pytest.mark.asyncio
@pytest.mark.parametrize("running", [False, True], ids=["startup", "running"])
async def test_light_entry_forwards_in_one_call_at_startup(
    hass: HomeAssistant, running: bool
) -> None:
    """At startup every platform goes in one call: HA bills each earlier call's
    import wait to MoLight's startup time. Once running, the switch goes first."""
    entry = _make_entry(ENTITY_TYPE_LIGHT)
    if not running:
        hass.set_state(CoreState.starting)
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        wraps=hass.config_entries.async_forward_entry_setups,
    ) as forward:
        await setup_entries(hass, entry)
    calls = [sorted(call.args[1]) for call in forward.call_args_list]
    assert calls == ([["switch"], ["light"]] if running else [["light", "switch"]])
    if not running:
        await finish_startup(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_type", list(TYPE_ENTRIES), ids=list(TYPE_ENTRIES))
async def test_unload_entry_succeeds_for_every_type(
    hass: HomeAssistant, entity_type: str
) -> None:
    entry = _make_entry(entity_type)
    await setup_entries(hass, entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)

    # Unloaded (not removed) entities are left as restored "unavailable"
    # placeholders; the live entity objects are gone.
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        state = hass.states.get(reg_entry.entity_id)
        assert state is not None
        assert state.state == "unavailable"


@pytest.mark.asyncio
async def test_light_entry_seeds_and_clears_hass_data(hass: HomeAssistant) -> None:
    """hass.data is seeded (auto-off enabled) at setup and dropped at unload."""
    entry = make_light_entry(name="Data Light")
    await setup_entries(hass, entry)
    assert hass.data[DOMAIN][entry.entry_id] == {
        DATA_AUTO_OFF_ENABLED: True,
        DATA_PLATFORMS: ["light", "switch"],
    }

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_auto_off_switch_state_survives_reload(hass: HomeAssistant) -> None:
    """A disabled auto-off survives an entry reload via the switch's restore."""
    entry = make_light_entry(name="Reload Light")
    hass.states.async_set("light.real_1", "off")
    await setup_entries(hass, entry)

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.reload_light_auto_off"},
        blocking=True,
    )
    await settle(hass)
    assert hass.data[DOMAIN][entry.entry_id][DATA_AUTO_OFF_ENABLED] is False

    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    assert hass.states.get("switch.reload_light_auto_off").state == "off"
    assert hass.data[DOMAIN][entry.entry_id][DATA_AUTO_OFF_ENABLED] is False
    light = hass.states.get("light.reload_light")
    assert light.attributes["auto_off_held"] is True


@pytest.mark.asyncio
async def test_auto_off_flag_is_kept_while_unloaded_and_dropped_on_removal(
    hass: HomeAssistant,
) -> None:
    """The flag outlives an unload so a reload can seed from it, not a removal."""
    entry = make_light_entry(name="Kept Light")
    hass.states.async_set("light.real_1", "off")
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.kept_light_auto_off"}, blocking=True
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert hass.data[DATA_AUTO_OFF_KEPT] == {entry.entry_id: False}

    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    assert hass.data[DATA_AUTO_OFF_KEPT] == {}
    assert hass.data[DOMAIN][entry.entry_id][DATA_AUTO_OFF_ENABLED] is False

    assert await hass.config_entries.async_remove(entry.entry_id)
    await settle(hass)
    assert hass.data[DATA_AUTO_OFF_KEPT] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("reload", [False, True])
async def test_disabling_the_auto_off_switch_while_off_releases_the_hold(
    hass: HomeAssistant, freezer, scheduled: bool, reload: bool
) -> None:
    """A disabled switch is removed and could never turn back on, so its
    removal releases the hold, and a later reload or restart keeps it released."""
    entry = (
        make_scheduled_light_entry(
            name="Kept Light",
            inside={CONF_LIGHT_TIMEOUT: 10},
            outside={CONF_LIGHT_TIMEOUT: 10},
        )
        if scheduled
        else make_light_entry(name="Kept Light", timeout=10)
    )
    hass.states.async_set("binary_sensor.settings_schedule", "on")
    hass.states.async_set("light.real_1", "on")
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.kept_light_auto_off"}, blocking=True
    )
    await settle(hass)
    assert hass.states.get("light.kept_light").attributes["auto_off_held"] is True

    er.async_get(hass).async_update_entity(
        "switch.kept_light_auto_off", disabled_by=er.RegistryEntryDisabler.USER
    )
    await settle(hass)
    if reload:
        assert await hass.config_entries.async_reload(entry.entry_id)
        await settle(hass)

    assert hass.states.get("switch.kept_light_auto_off") is None
    assert hass.data[DOMAIN][entry.entry_id][DATA_AUTO_OFF_ENABLED] is True
    assert hass.states.get("light.kept_light").attributes["auto_off_held"] is False
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.kept_light").state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("meanwhile", ["nothing", "reload", "restart"])
@pytest.mark.parametrize("switch", ["off", "on"])
async def test_reenabling_the_auto_off_switch_brings_back_its_state(
    hass: HomeAssistant, freezer, scheduled: bool, meanwhile: str, switch: str
) -> None:
    """Enabling the switch again restores the state it had when disabled, and
    an off switch holds the light again."""
    entry = (
        make_scheduled_light_entry(
            name="Kept Light",
            inside={CONF_LIGHT_TIMEOUT: 10},
            outside={CONF_LIGHT_TIMEOUT: 10},
        )
        if scheduled
        else make_light_entry(name="Kept Light", timeout=10)
    )
    hass.states.async_set("binary_sensor.settings_schedule", "on")
    hass.states.async_set("light.real_1", "on")
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "switch", f"turn_{switch}", {"entity_id": "switch.kept_light_auto_off"}
    )
    await settle(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(
        "switch.kept_light_auto_off", disabled_by=er.RegistryEntryDisabler.USER
    )
    await settle(hass)
    if meanwhile == "reload":
        assert await hass.config_entries.async_reload(entry.entry_id)
        await settle(hass)
    elif meanwhile == "restart":
        await restart_entries(hass, entry)
    assert hass.states.get("light.kept_light").attributes["auto_off_held"] is False

    registry.async_update_entity("switch.kept_light_auto_off", disabled_by=None)
    await settle(hass)
    # Home Assistant reloads the entry to add the entity back after a delay.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)

    held = switch == "off"
    assert hass.states.get("switch.kept_light_auto_off").state == switch
    assert hass.data[DOMAIN][entry.entry_id][DATA_AUTO_OFF_ENABLED] is not held
    assert hass.states.get("light.kept_light").attributes["auto_off_held"] is held
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.kept_light"}, blocking=True
    )
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.kept_light").state == ("on" if held else "off")


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("reload", [False, True])
async def test_deleting_the_auto_off_switch_while_off_releases_the_hold(
    hass: HomeAssistant, freezer, scheduled: bool, reload: bool
) -> None:
    """A deleted switch can never turn back on either, so the hold goes with
    it; the switch a reload adds back is on."""
    entry = (
        make_scheduled_light_entry(
            name="Kept Light",
            inside={CONF_LIGHT_TIMEOUT: 10},
            outside={CONF_LIGHT_TIMEOUT: 10},
        )
        if scheduled
        else make_light_entry(name="Kept Light", timeout=10)
    )
    hass.states.async_set("binary_sensor.settings_schedule", "on")
    hass.states.async_set("light.real_1", "on")
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.kept_light_auto_off"}, blocking=True
    )
    await settle(hass)
    assert hass.states.get("light.kept_light").attributes["auto_off_held"] is True

    er.async_get(hass).async_remove("switch.kept_light_auto_off")
    await settle(hass)
    assert hass.states.get("switch.kept_light_auto_off") is None
    if reload:
        assert await hass.config_entries.async_reload(entry.entry_id)
        await settle(hass)
        assert hass.states.get("switch.kept_light_auto_off").state == "on"

    assert hass.data[DOMAIN][entry.entry_id][DATA_AUTO_OFF_ENABLED] is True
    assert hass.states.get("light.kept_light").attributes["auto_off_held"] is False
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.kept_light").state == "off"


@pytest.mark.asyncio
async def test_deleting_the_auto_off_switch_applies_a_window_end_it_held(
    hass: HomeAssistant,
) -> None:
    """A follow window that ended under the hold turns the light off now."""
    hass.states.async_set("binary_sensor.window", "off")
    hass.states.async_set("light.real_1", "off")
    entry = make_light_entry(
        name="Kept Light",
        schedule="binary_sensor.window",
        schedule_mode=SCHEDULE_MODE_FOLLOW,
    )
    await setup_entries(hass, entry)
    hass.states.async_set(
        "binary_sensor.window", "on", {"current_window_start": "2026-07-02T21:00"}
    )
    await settle(hass)
    assert hass.states.get("light.kept_light").state == "on"
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.kept_light_auto_off"}, blocking=True
    )
    hass.states.async_set("binary_sensor.window", "off")
    await settle(hass)
    assert hass.states.get("light.kept_light").state == "on"

    er.async_get(hass).async_remove("switch.kept_light_auto_off")
    await settle(hass)
    assert hass.states.get("light.kept_light").state == "off"


@pytest.mark.asyncio
async def test_renaming_the_auto_off_switch_keeps_its_hold(
    hass: HomeAssistant, freezer
) -> None:
    """A new entity ID removes the switch for a moment; it is still there."""
    entry = make_light_entry(name="Kept Light", timeout=10)
    hass.states.async_set("light.real_1", "on")
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.kept_light_auto_off"}, blocking=True
    )
    await settle(hass)

    er.async_get(hass).async_update_entity(
        "switch.kept_light_auto_off", new_entity_id="switch.hallway_auto_off"
    )
    await settle(hass)
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("switch.hallway_auto_off").state == "off"
    assert hass.states.get("light.kept_light").attributes["auto_off_held"] is True
    assert hass.states.get("light.kept_light").state == "on"


@pytest.mark.asyncio
async def test_failed_platform_unload_keeps_entry_data(hass: HomeAssistant) -> None:
    """A failed platform unload leaves the entry's shared data in place."""
    entry = make_light_entry(name="Hall", lights=["light.real_1"])
    await setup_entries(hass, entry)
    assert entry.entry_id in hass.data[DOMAIN]

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(return_value=False),
    ):
        assert not await hass.config_entries.async_unload(entry.entry_id)

    assert entry.entry_id in hass.data[DOMAIN]
