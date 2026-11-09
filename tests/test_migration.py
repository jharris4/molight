"""Config entry migration: version 2 renamed settings keys after their field labels."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.const import (
    CONF_BRIGHTNESS_STEP,
    CONF_CLEAR_AFTER_UNAVAILABLE_TIMEOUT,
    CONF_DOOR_SENSOR,
    CONF_EFFECT_DURATION,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_ILLUMINANCE_MODE,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_SOURCE,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_KEEP_ON_ENTITIES,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_SENSOR,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_SOURCE,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_ON_BUTTONS_SINGLE,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_SENSOR,
    CONF_TARGET_LIGHTS,
    CONF_TURN_OFF_TIMEOUT,
    CONF_TURN_ON_SELECTION_ENTITY,
    CONF_TURN_ON_SELECTION_OPTION,
    CONF_TURN_ON_SELECTION_SOURCE_ENTITY,
    CONF_WARN_DURATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_REMOTE,
    ENTITY_TYPE_SCHEDULED_LIGHT,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE_ONLY,
    SCHEDULE_MODE_GATE_TURN_OFF,
)
from tests.conftest import setup_entries

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

BASE = {
    ENTITY_TYPE_LIGHT: {CONF_NAME: "Hall", CONF_LIGHTS: ["light.hall_real"]},
    ENTITY_TYPE_SCHEDULED_LIGHT: {CONF_NAME: "Hall", CONF_LIGHTS: ["light.hall_real"]},
    ENTITY_TYPE_OCCUPANCY: {
        CONF_NAME: "Hall Occupancy",
        "occupancy_sensor": "binary_sensor.motion_1",
        CONF_OCCUPANCY_TIMEOUT: 30,
        CONF_FALSE_DETECTION_GRACE: 0,
    },
    ENTITY_TYPE_ILLUMINANCE: {
        CONF_NAME: "Hall Lux",
        "illuminance_sensor": "sensor.lux_1",
        CONF_ILLUMINANCE_THRESHOLD: 10.0,
    },
    ENTITY_TYPE_REMOTE: {
        CONF_NAME: "Hall Remote",
        CONF_TARGET_LIGHTS: ["light.hall_real"],
        CONF_ON_BUTTONS_SINGLE: ["event.pico_on"],
    },
}

# entity type, version-1 key, version-2 key, a value
RENAMED = [
    (ENTITY_TYPE_LIGHT, "light_timeout", CONF_TURN_OFF_TIMEOUT, 45),
    (ENTITY_TYPE_LIGHT, "effect_timeout", CONF_EFFECT_DURATION, 10),
    (ENTITY_TYPE_LIGHT, "warn_timeout", CONF_WARN_DURATION, 15),
    (
        ENTITY_TYPE_LIGHT,
        "hold_entities",
        CONF_KEEP_ON_ENTITIES,
        ["input_boolean.guest"],
    ),
    (ENTITY_TYPE_LIGHT, "occupancy_entity", CONF_OCCUPANCY_SENSOR, "binary_sensor.occ"),
    (
        ENTITY_TYPE_LIGHT,
        "maintain_occupancy_entity",
        CONF_MAINTAIN_OCCUPANCY_SENSOR,
        "binary_sensor.maintain",
    ),
    (
        ENTITY_TYPE_LIGHT,
        "illuminance_entity",
        CONF_ILLUMINANCE_SENSOR,
        "binary_sensor.lux",
    ),
    (ENTITY_TYPE_LIGHT, "schedule_entity", CONF_SCHEDULE_SENSOR, "binary_sensor.night"),
    (ENTITY_TYPE_LIGHT, "door_entity", CONF_DOOR_SENSOR, "binary_sensor.door"),
    (
        ENTITY_TYPE_OCCUPANCY,
        "clear_on_unavailable_timeout",
        CONF_CLEAR_AFTER_UNAVAILABLE_TIMEOUT,
        30,
    ),
    (ENTITY_TYPE_REMOTE, "dim_step", CONF_BRIGHTNESS_STEP, 20),
    (
        ENTITY_TYPE_LIGHT,
        "turn_on_select_entity",
        CONF_TURN_ON_SELECTION_ENTITY,
        "select.mode",
    ),
    (ENTITY_TYPE_LIGHT, "turn_on_select_option", CONF_TURN_ON_SELECTION_OPTION, "Dim"),
    (
        ENTITY_TYPE_LIGHT,
        "turn_on_select_source_entity",
        CONF_TURN_ON_SELECTION_SOURCE_ENTITY,
        "input_select.mode",
    ),
    # A sensor entry's source, under the key a light's reference has in version 2.
    (
        ENTITY_TYPE_OCCUPANCY,
        "occupancy_sensor",
        CONF_OCCUPANCY_SOURCE,
        "binary_sensor.motion_2",
    ),
    (
        ENTITY_TYPE_ILLUMINANCE,
        "illuminance_sensor",
        CONF_ILLUMINANCE_SOURCE,
        "sensor.lux_2",
    ),
]


def _entry(
    entity_type: str, settings: dict[str, Any], *, version: int = 1
) -> MockConfigEntry:
    """A saved entry: its settings in data and, as after an edit, in options too."""
    data = {CONF_ENTITY_TYPE: entity_type, **BASE[entity_type], **settings}
    options = {key: value for key, value in data.items() if key != CONF_ENTITY_TYPE}
    return MockConfigEntry(domain=DOMAIN, data=data, options=options, version=version)


def _set_referenced(hass: HomeAssistant, settings: dict[str, Any]) -> None:
    for value in settings.values():
        for entity_id in value if isinstance(value, list) else [value]:
            if isinstance(entity_id, str) and "." in entity_id:
                hass.states.async_set(entity_id, "off")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_type", "old", "new", "value"), RENAMED, ids=[r[1] for r in RENAMED]
)
async def test_version_1_key_is_renamed_in_data_and_options(
    hass: HomeAssistant, entity_type: str, old: str, new: str, value: Any
) -> None:
    entry = _entry(entity_type, {old: value})
    _set_referenced(hass, {old: value})
    await setup_entries(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == CONFIG_ENTRY_VERSION
    for saved in (entry.data, entry.options):
        assert saved[new] == value
        assert old not in saved


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings", [{}, {"light_timeout": None}], ids=["missing", "none"]
)
async def test_turn_off_timeout_is_copied_as_found(
    hass: HomeAssistant, settings: dict[str, Any]
) -> None:
    """Missing means the default and None means no timer; neither becomes a number."""
    entry = _entry(ENTITY_TYPE_LIGHT, settings)
    await setup_entries(hass, entry)

    for saved in (entry.data, entry.options):
        assert "light_timeout" not in saved
        if settings:
            assert saved[CONF_TURN_OFF_TIMEOUT] is None
        else:
            assert CONF_TURN_OFF_TIMEOUT not in saved


@pytest.mark.asyncio
async def test_illuminance_mode_gate_becomes_gate_only(hass: HomeAssistant) -> None:
    entry = _entry(
        ENTITY_TYPE_LIGHT,
        {"illuminance_entity": "binary_sensor.lux", CONF_ILLUMINANCE_MODE: "gate"},
    )
    hass.states.async_set("binary_sensor.lux", "off")
    await setup_entries(hass, entry)

    for saved in (entry.data, entry.options):
        assert saved[CONF_ILLUMINANCE_MODE] == ILLUMINANCE_MODE_GATE_ONLY


@pytest.mark.asyncio
async def test_schedule_mode_gate_becomes_gate_turn_off(hass: HomeAssistant) -> None:
    """Each mode's gate gets its own new value."""
    entry = _entry(
        ENTITY_TYPE_LIGHT,
        {
            "schedule_entity": "binary_sensor.night",
            CONF_SCHEDULE_MODE: "gate",
            "illuminance_entity": "binary_sensor.lux",
            CONF_ILLUMINANCE_MODE: "gate",
        },
    )
    for entity_id in ("binary_sensor.night", "binary_sensor.lux"):
        hass.states.async_set(entity_id, "off")
    await setup_entries(hass, entry)

    for saved in (entry.data, entry.options):
        assert saved[CONF_SCHEDULE_MODE] == SCHEDULE_MODE_GATE_TURN_OFF
        assert saved[CONF_ILLUMINANCE_MODE] == ILLUMINANCE_MODE_GATE_ONLY


@pytest.mark.asyncio
async def test_entry_without_options_keeps_them_empty(hass: HomeAssistant) -> None:
    """An entry never edited keeps its empty options, so data stays what is read."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            **BASE[ENTITY_TYPE_LIGHT],
            "light_timeout": 45,
        },
        version=1,
    )
    await setup_entries(hass, entry)

    assert entry.data[CONF_TURN_OFF_TIMEOUT] == 45
    assert entry.options == {}


@pytest.mark.asyncio
async def test_version_2_entry_is_untouched(hass: HomeAssistant) -> None:
    """A current entry is not walked, whatever its keys happen to be."""
    entry = _entry(ENTITY_TYPE_LIGHT, {"light_timeout": 45}, version=2)
    before = dict(entry.data), dict(entry.options)
    await setup_entries(hass, entry)

    assert entry.version == CONFIG_ENTRY_VERSION
    assert (dict(entry.data), dict(entry.options)) == before


@pytest.mark.asyncio
@pytest.mark.allow_error_log  # HA reports the refused migration
async def test_newer_entry_does_not_load(hass: HomeAssistant) -> None:
    """An entry saved by a later release is left for that release."""
    entry = _entry(ENTITY_TYPE_LIGHT, {}, version=CONFIG_ENTRY_VERSION + 1)
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


@pytest.mark.asyncio
async def test_scheduled_light_is_renamed_in_both_settings_sets(
    hass: HomeAssistant,
) -> None:
    """The top level and each settings set are renamed; values are copied as found."""
    outside = {
        "light_timeout": None,
        "hold_entities": ["input_boolean.guest"],
        "door_entity": "binary_sensor.door",
        "illuminance_entity": "binary_sensor.lux",
        CONF_ILLUMINANCE_MODE: "gate",
    }
    inside = {
        "effect_timeout": 10,
        "warn_timeout": 15,
        "occupancy_entity": "binary_sensor.occ",
        "maintain_occupancy_entity": "binary_sensor.maintain",
        "illuminance_entity": "binary_sensor.lux",
        CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
    }
    entry = _entry(
        ENTITY_TYPE_SCHEDULED_LIGHT,
        {
            "schedule_entity": "binary_sensor.night",
            CONF_OUTSIDE_SCHEDULE_SETTINGS: outside,
            CONF_INSIDE_SCHEDULE_SETTINGS: inside,
        },
    )
    for settings in (outside, inside):
        _set_referenced(hass, settings)
    hass.states.async_set("binary_sensor.night", "off")
    await setup_entries(hass, entry)

    assert entry.version == CONFIG_ENTRY_VERSION
    expected_outside = {
        CONF_TURN_OFF_TIMEOUT: None,
        CONF_KEEP_ON_ENTITIES: ["input_boolean.guest"],
        CONF_DOOR_SENSOR: "binary_sensor.door",
        CONF_ILLUMINANCE_SENSOR: "binary_sensor.lux",
        CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_GATE_ONLY,
    }
    expected_inside = {
        CONF_EFFECT_DURATION: 10,
        CONF_WARN_DURATION: 15,
        CONF_OCCUPANCY_SENSOR: "binary_sensor.occ",
        CONF_MAINTAIN_OCCUPANCY_SENSOR: "binary_sensor.maintain",
        CONF_ILLUMINANCE_SENSOR: "binary_sensor.lux",
        CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
    }
    for saved in (entry.data, entry.options):
        assert saved[CONF_SCHEDULE_SENSOR] == "binary_sensor.night"
        assert "schedule_entity" not in saved
        assert saved[CONF_OUTSIDE_SCHEDULE_SETTINGS] == expected_outside
        assert saved[CONF_INSIDE_SCHEDULE_SETTINGS] == expected_inside
