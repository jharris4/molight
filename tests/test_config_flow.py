"""Tests for the MoLight config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, section
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.config_flow import (
    SECTION_ADVANCED,
    SECTION_BEHAVIOR,
    SECTION_SENSORS,
    SECTION_WARNING,
    _validate_turn_on_selection,
)
from custom_components.molight.const import (
    AFFIX_TARGET_ENTITY_ID,
    AFFIX_TARGET_NAME,
    ASSIGN_ROLE_MAINTAIN,
    ASSIGN_ROLE_REGULAR,
    CONF_AFFIX_PREFIX,
    CONF_AFFIX_SUFFIX,
    CONF_AFFIX_TARGET,
    CONF_ASSIGN_LIGHTS,
    CONF_ASSIGN_ROLE,
    CONF_ASSIGN_SENSOR,
    CONF_AUTO_OFF_TRANSITION,
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_AUTO_ON_COLOR_TEMP,
    CONF_AUTO_ON_RGB_COLOR,
    CONF_AUTO_ON_TRANSITION,
    CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    CONF_CONFIRM_CONVERSION,
    CONF_CONVERT_LIGHTS,
    CONF_DOOR_ENTITY,
    CONF_DOOR_MODE,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_RGB_COLOR,
    CONF_EFFECT_TIMEOUT,
    CONF_EFFECT_TRANSITION,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_FALSE_OFF_DELAY,
    CONF_FILTER_AREAS,
    CONF_FILTER_LABELS,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_HYSTERESIS,
    CONF_ILLUMINANCE_MODE,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_PRESELECT_ALL,
    CONF_SCHEDULE_END_ACTION,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    CONF_SELECTED_ENTITIES,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_TURN_ON_SELECT_SOURCE_ENTITY,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_RGB_COLOR,
    CONF_WARN_TIMEOUT,
    CONF_WARN_TRANSITION,
    DOMAIN,
    DOOR_MODE_OPEN_CLOSE,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
    ENTITY_TYPE_SCHEDULED_LIGHT,
    ILLUMINANCE_MODE_GATE,
    SCHEDULE_END_ACTION_KEEP,
    SCHEDULE_END_ACTION_SWITCH,
    SCHEDULE_END_ACTION_TURN_OFF,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_KEEP,
    SCHEDULE_MODE_GATE_SWITCH,
)
from custom_components.molight.helpers import molight_config
from tests.conftest import settle, setup_entries

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# The frontend always submits every section key, even collapsed sections that
# were never opened. The section markers have no defaults, so programmatic
# submissions must do the same; an empty dict makes voluptuous fill in the
# per-field defaults. Spread one of these first so explicit sections override.
EMPTY_LIGHT_SECTIONS = {SECTION_SENSORS: {}, SECTION_BEHAVIOR: {}, SECTION_WARNING: {}}
EMPTY_LIGHT_CREATE_SECTIONS = {**EMPTY_LIGHT_SECTIONS, SECTION_ADVANCED: {}}


async def _start_create(hass: HomeAssistant) -> dict:
    """Init the flow and pick 'Create a single entity' from the menu.

    Returns the flow result at the entity-type picker (the 'create' step).
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "create"}
    )


async def _start_discovery(hass: HomeAssistant, step: str) -> dict:
    """Init the flow and pick a discovery step from the menu.

    Returns the flow result at that discovery's filter step.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def _reach_discovery_select(hass: HomeAssistant, step: str) -> dict:
    """Advance a discovery flow past an unfiltered filter step.

    Returns the flow result at the entity checklist (the '<step>_select'
    step), with every candidate offered and pre-selected.
    """
    result = await _start_discovery(hass, step)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == step
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == f"{step}_select"
    return result


def _offered_candidates(result: dict) -> set[str]:
    """Entity ids the discovery form pre-selects (its default is all candidates)."""
    return set(result["data_schema"]({})[CONF_SELECTED_ENTITIES])


def _suggested_values(schema) -> dict:
    """The suggested values a form schema carries, section values nested."""
    out: dict = {}
    for marker, value in schema.schema.items():
        if isinstance(value, section):
            inner = _suggested_values(value.schema)
            if inner:
                out[marker.schema] = inner
        elif (
            getattr(marker, "description", None)
            and "suggested_value" in marker.description
        ):
            out[marker.schema] = marker.description["suggested_value"]
    return out


def _selector_config(result: dict, key: str) -> dict:
    """Return a form field's selector config."""
    return next(
        value.config
        for marker, value in result["data_schema"].schema.items()
        if str(marker) == key
    )


@pytest.mark.asyncio
async def test_config_flow_occupancy(hass: HomeAssistant) -> None:
    """Full config flow creates an occupancy sensor entry."""
    result = await _start_create(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "create"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY},
    )
    assert result["step_id"] == "occupancy"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.hall_motion",
            CONF_OCCUPANCY_TIMEOUT: 60,
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hall Occupancy"
    assert result["data"][CONF_ENTITY_TYPE] == ENTITY_TYPE_OCCUPANCY
    # Unsubmitted fields fall back to their schema defaults.
    assert result["data"][CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT] == 60


@pytest.mark.asyncio
async def test_occupancy_source_picker_excludes_molight_occupancy_entities(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Simple and combined MoLight occupancy entities cannot be wrapped again."""
    combined = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )
    await setup_entries(hass, occupancy_entry, combined)

    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY}
    )

    assert set(_selector_config(result, CONF_OCCUPANCY_SENSOR)["exclude_entities"]) == {
        "binary_sensor.combined",
        "binary_sensor.test_occupancy",
    }


@pytest.mark.asyncio
async def test_occupancy_create_rejects_molight_source_from_stale_form(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Submission rechecks a MoLight source created after the form was opened."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY}
    )
    assert _selector_config(result, CONF_OCCUPANCY_SENSOR)["exclude_entities"] == []

    await setup_entries(hass, occupancy_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Nested Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.test_occupancy",
            CONF_OCCUPANCY_TIMEOUT: 60,
            SECTION_ADVANCED: {},
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_OCCUPANCY_SENSOR: "occupancy_source_molight"}
    assert _suggested_values(result["data_schema"])[CONF_OCCUPANCY_SENSOR] == (
        "binary_sensor.test_occupancy"
    )


@pytest.mark.asyncio
async def test_config_flow_schedule_with_sun(hass: HomeAssistant) -> None:
    """Schedule flow builds a window with sun-anchored edges."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE}
    )
    assert result["step_id"] == "schedule"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Night",
            "start": {
                "time": "21:00:00",
                "sun": "sunset",
                "offset": -15,
                "combine": "latest",
            },
            "end": {
                "time": "07:00:00",
                "sun": "sunrise",
                "offset": 10,
                "combine": "earliest",
            },
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TIME_WINDOWS] == [
        {
            "start": {
                "time": "21:00:00",
                "sun": "sunset",
                "offset": -15,
                "combine": "latest",
            },
            "end": {
                "time": "07:00:00",
                "sun": "sunrise",
                "offset": 10,
                "combine": "earliest",
            },
        }
    ]


@pytest.mark.asyncio
async def test_config_flow_schedule_rejects_incomplete_window(
    hass: HomeAssistant,
) -> None:
    """A half-filled window is rejected instead of silently dropped."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE}
    )

    # Start edge only — no end.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Night",
            "start": {"time": "21:00:00"},
            "end": {},
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "window_incomplete"}

    # Sun-only edges (no fixed times) are a complete window.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Night",
            "start": {"sun": "sunset"},
            "end": {"sun": "sunrise"},
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TIME_WINDOWS] == [
        {
            "start": {"sun": "sunset", "combine": "latest"},
            "end": {"sun": "sunrise", "combine": "latest"},
        }
    ]


@pytest.mark.asyncio
async def test_config_flow_virtual_light(hass: HomeAssistant) -> None:
    """Full config flow creates a virtual light entry."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT},
    )
    assert result["step_id"] == "light"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Living Room",
            CONF_LIGHTS: ["light.living_room_1", "light.living_room_2"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_SENSORS: {CONF_HOLD_ENTITIES: ["input_boolean.guest_mode"]},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room"
    assert result["data"][CONF_HOLD_ENTITIES] == ["input_boolean.guest_mode"]
    # Unsubmitted effect/warn stages default to disabled.
    assert result["data"][CONF_EFFECT_TIMEOUT] == 0
    assert result["data"][CONF_WARN_TIMEOUT] == 0
    assert CONF_WARN_BRIGHTNESS not in result["data"]


@pytest.mark.asyncio
async def test_config_flow_virtual_scheduled_light(hass: HomeAssistant) -> None:
    """The three main forms store two independent Virtual Light settings maps."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT}
    )
    assert result["step_id"] == "scheduled_light"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            SECTION_ADVANCED: {CONF_ENTITY_ID: "scheduled_hallway"},
        },
    )
    assert result["step_id"] == "scheduled_light_outside"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_SENSORS: {CONF_OCCUPANCY_ENTITY: "binary_sensor.day_occupancy"},
            SECTION_BEHAVIOR: {CONF_AUTO_ON_BRIGHTNESS: 80},
        },
    )
    assert result["step_id"] == "scheduled_light_inside"
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_LIGHT_TIMEOUT] == 300
    assert suggested[SECTION_SENSORS][CONF_OCCUPANCY_ENTITY] == (
        "binary_sensor.day_occupancy"
    )
    assert suggested[SECTION_BEHAVIOR][CONF_AUTO_ON_BRIGHTNESS] == 80

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_LIGHT_TIMEOUT: 60,
            SECTION_SENSORS: {CONF_OCCUPANCY_ENTITY: "binary_sensor.night_occupancy"},
            SECTION_BEHAVIOR: {CONF_AUTO_ON_BRIGHTNESS: 20},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_ENTITY_TYPE] == ENTITY_TYPE_SCHEDULED_LIGHT
    assert data[CONF_ENTITY_ID] == "scheduled_hallway"
    assert data[CONF_SCHEDULE_END_ACTION] == SCHEDULE_END_ACTION_KEEP
    assert "outside_schedule_settings" in data
    assert "inside_schedule_settings" in data
    assert "outside_settings" not in data
    assert "inside_settings" not in data
    assert data[CONF_OUTSIDE_SCHEDULE_SETTINGS][CONF_LIGHT_TIMEOUT] == 300
    assert data[CONF_OUTSIDE_SCHEDULE_SETTINGS][CONF_AUTO_ON_BRIGHTNESS] == 80
    assert data[CONF_INSIDE_SCHEDULE_SETTINGS][CONF_LIGHT_TIMEOUT] == 60
    assert data[CONF_INSIDE_SCHEDULE_SETTINGS][CONF_AUTO_ON_BRIGHTNESS] == 20
    assert CONF_SCHEDULE_ENTITY not in data[CONF_OUTSIDE_SCHEDULE_SETTINGS]
    assert CONF_SCHEDULE_ENTITY not in data[CONF_INSIDE_SCHEDULE_SETTINGS]


@pytest.mark.asyncio
async def test_scheduled_light_turn_on_selection_for_each_side(
    hass: HomeAssistant,
) -> None:
    """Each settings map gets its own generic turn-on selection page."""
    hass.states.async_set("select.mode", "Day", {"options": ["Day", "Night"]})
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            SECTION_ADVANCED: {},
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {CONF_TURN_ON_SELECT_ENTITY: "select.mode"},
        },
    )
    assert result["step_id"] == "scheduled_light_selection"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TURN_ON_SELECT_OPTION: "Day"}
    )
    assert result["step_id"] == "scheduled_light_inside"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {CONF_TURN_ON_SELECT_ENTITY: "select.mode"},
        },
    )
    assert result["step_id"] == "scheduled_light_selection"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TURN_ON_SELECT_OPTION: "Night"}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert (
        result["data"][CONF_OUTSIDE_SCHEDULE_SETTINGS][CONF_TURN_ON_SELECT_OPTION]
        == "Day"
    )
    assert (
        result["data"][CONF_INSIDE_SCHEDULE_SETTINGS][CONF_TURN_ON_SELECT_OPTION]
        == "Night"
    )


@pytest.mark.asyncio
async def test_scheduled_light_options_edit_both_settings(
    hass: HomeAssistant,
) -> None:
    """The options flow uses the same three main forms and preserves each side."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            CONF_OUTSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 300},
            CONF_INSIDE_SCHEDULE_SETTINGS: {
                CONF_LIGHT_TIMEOUT: 60,
                CONF_AUTO_ON_BRIGHTNESS: 20,
            },
        },
    )
    await setup_entries(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "scheduled_light"
    assert (
        result["data_schema"]({})[CONF_SCHEDULE_END_ACTION] == SCHEDULE_END_ACTION_KEEP
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway Updated",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            CONF_SCHEDULE_END_ACTION: SCHEDULE_END_ACTION_TURN_OFF,
        },
    )
    assert result["step_id"] == "scheduled_light_outside"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**EMPTY_LIGHT_SECTIONS, CONF_LIGHT_TIMEOUT: 240},
    )
    assert result["step_id"] == "scheduled_light_inside"
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_LIGHT_TIMEOUT] == 60
    assert suggested[SECTION_BEHAVIOR][CONF_AUTO_ON_BRIGHTNESS] == 20

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_LIGHT_TIMEOUT: 45,
            SECTION_BEHAVIOR: {CONF_AUTO_ON_BRIGHTNESS: 15},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    cfg = molight_config(entry)
    assert cfg[CONF_NAME] == "Hallway Updated"
    assert cfg[CONF_SCHEDULE_END_ACTION] == SCHEDULE_END_ACTION_TURN_OFF
    assert cfg[CONF_OUTSIDE_SCHEDULE_SETTINGS][CONF_LIGHT_TIMEOUT] == 240
    assert cfg[CONF_INSIDE_SCHEDULE_SETTINGS][CONF_LIGHT_TIMEOUT] == 45
    assert cfg[CONF_INSIDE_SCHEDULE_SETTINGS][CONF_AUTO_ON_BRIGHTNESS] == 15


@pytest.mark.asyncio
async def test_scheduled_light_options_requires_replacing_deleted_schedule(
    hass: HomeAssistant,
) -> None:
    """A removed shared schedule cannot be saved as an empty selection."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_OUTSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 300},
            CONF_INSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 60},
        },
    )
    await setup_entries(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schedule_marker = next(
        marker
        for marker in result["data_schema"].schema
        if str(marker) == CONF_SCHEDULE_ENTITY
    )
    assert isinstance(schedule_marker, vol.Required)


@pytest.mark.asyncio
async def test_scheduled_light_validates_each_side_timeout(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Both settings forms enforce the referenced occupancy timeout."""
    await setup_entries(hass, occupancy_entry)
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            SECTION_ADVANCED: {},
        },
    )

    too_short = {
        **EMPTY_LIGHT_SECTIONS,
        CONF_LIGHT_TIMEOUT: 20,
        SECTION_SENSORS: {CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy"},
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], too_short
    )
    assert result["step_id"] == "scheduled_light_outside"
    assert result["errors"] == {CONF_LIGHT_TIMEOUT: "light_timeout_too_short"}

    valid = {**too_short, CONF_LIGHT_TIMEOUT: 60}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], valid)
    assert result["step_id"] == "scheduled_light_inside"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], too_short
    )
    assert result["step_id"] == "scheduled_light_inside"
    assert result["errors"] == {CONF_LIGHT_TIMEOUT: "light_timeout_too_short"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], valid)
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_occupancy_edit_checks_both_scheduled_light_sides(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Raising an occupancy timeout respects the shorter settings side."""
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            CONF_OUTSIDE_SCHEDULE_SETTINGS: {
                CONF_LIGHT_TIMEOUT: 60,
                CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
            },
            CONF_INSIDE_SCHEDULE_SETTINGS: {
                CONF_LIGHT_TIMEOUT: 30,
                CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
            },
        },
    )
    await setup_entries(hass, occupancy_entry, light)

    result = await hass.config_entries.options.async_init(occupancy_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 45,
            SECTION_ADVANCED: {},
        },
    )
    assert result["errors"] == {CONF_OCCUPANCY_TIMEOUT: "occupancy_timeout_too_long"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 25,
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_scheduled_light_derived_id_collision_can_go_back(
    hass: HomeAssistant,
) -> None:
    """The late collision prompt preserves all three completed forms."""
    hass.states.async_set("light.hallway", "off")
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT}
    )
    shared = {
        CONF_NAME: "Hallway",
        CONF_LIGHTS: ["light.real_hallway"],
        CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
        SECTION_ADVANCED: {},
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], shared)
    outside = {
        **EMPTY_LIGHT_SECTIONS,
        CONF_LIGHT_TIMEOUT: 300,
        SECTION_BEHAVIOR: {CONF_AUTO_ON_BRIGHTNESS: 80},
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], outside)
    inside = {
        **EMPTY_LIGHT_SECTIONS,
        CONF_LIGHT_TIMEOUT: 60,
        SECTION_BEHAVIOR: {CONF_AUTO_ON_BRIGHTNESS: 20},
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], inside)
    assert result["step_id"] == "confirm_entity_id"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "entity_id_change"}
    )
    assert result["step_id"] == "scheduled_light"
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_NAME] == "Hallway"
    assert suggested[CONF_LIGHTS] == ["light.real_hallway"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **shared,
            SECTION_ADVANCED: {CONF_ENTITY_ID: "scheduled_hallway"},
        },
    )
    assert result["step_id"] == "scheduled_light_outside"
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_LIGHT_TIMEOUT] == 300
    assert suggested[SECTION_BEHAVIOR][CONF_AUTO_ON_BRIGHTNESS] == 80

    result = await hass.config_entries.flow.async_configure(result["flow_id"], outside)
    assert result["step_id"] == "scheduled_light_inside"
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_LIGHT_TIMEOUT] == 60
    assert suggested[SECTION_BEHAVIOR][CONF_AUTO_ON_BRIGHTNESS] == 20

    result = await hass.config_entries.flow.async_configure(result["flow_id"], inside)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENTITY_ID] == "scheduled_hallway"


@pytest.mark.asyncio
async def test_scheduled_light_inside_selection_prefills_from_outside(
    hass: HomeAssistant,
) -> None:
    """Keeping the outside target on the inside form carries its selection over."""
    hass.states.async_set("select.mode", "Day", {"options": ["Day", "Night"]})
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            SECTION_ADVANCED: {},
        },
    )
    settings = {
        **EMPTY_LIGHT_SECTIONS,
        CONF_LIGHT_TIMEOUT: 300,
        SECTION_BEHAVIOR: {CONF_TURN_ON_SELECT_ENTITY: "select.mode"},
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], settings)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_TURN_ON_SELECT_OPTION: "Day",
            CONF_TURN_ON_SELECT_SOURCE_ENTITY: "input_select.theme",
        },
    )
    assert result["step_id"] == "scheduled_light_inside"
    # The inside form is prefilled with the outside settings, target included.
    suggested = _suggested_values(result["data_schema"])
    assert suggested[SECTION_BEHAVIOR][CONF_TURN_ON_SELECT_ENTITY] == "select.mode"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], settings)
    assert result["step_id"] == "scheduled_light_selection"
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_TURN_ON_SELECT_OPTION] == "Day"
    assert suggested[CONF_TURN_ON_SELECT_SOURCE_ENTITY] == "input_select.theme"

    # Submitting the carried values unchanged stores them on the inside side.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_TURN_ON_SELECT_OPTION: "Day",
            CONF_TURN_ON_SELECT_SOURCE_ENTITY: "input_select.theme",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    inside = result["data"][CONF_INSIDE_SCHEDULE_SETTINGS]
    assert inside[CONF_TURN_ON_SELECT_OPTION] == "Day"
    assert inside[CONF_TURN_ON_SELECT_SOURCE_ENTITY] == "input_select.theme"


@pytest.mark.asyncio
async def test_scheduled_light_options_edit_and_clear_turn_on_selection(
    hass: HomeAssistant,
) -> None:
    """Each side's selection page prefills its own values; clearing drops them."""
    hass.states.async_set("select.mode", "Day", {"options": ["Day", "Night", "Dusk"]})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            CONF_OUTSIDE_SCHEDULE_SETTINGS: {
                CONF_LIGHT_TIMEOUT: 300,
                CONF_TURN_ON_SELECT_ENTITY: "select.mode",
                CONF_TURN_ON_SELECT_OPTION: "Day",
                CONF_TURN_ON_SELECT_SOURCE_ENTITY: "input_select.theme",
            },
            CONF_INSIDE_SCHEDULE_SETTINGS: {
                CONF_LIGHT_TIMEOUT: 60,
                CONF_TURN_ON_SELECT_ENTITY: "select.mode",
                CONF_TURN_ON_SELECT_OPTION: "Night",
            },
        },
    )
    await setup_entries(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
        },
    )
    assert result["step_id"] == "scheduled_light_outside"
    suggested = _suggested_values(result["data_schema"])
    assert suggested[SECTION_BEHAVIOR][CONF_TURN_ON_SELECT_ENTITY] == "select.mode"

    # Outside: keep the target, so the selection page shows the stored values.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {CONF_TURN_ON_SELECT_ENTITY: "select.mode"},
        },
    )
    assert result["step_id"] == "scheduled_light_selection"
    assert _selector_config(result, CONF_TURN_ON_SELECT_OPTION)["entity_id"] == (
        "select.mode"
    )
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_TURN_ON_SELECT_OPTION] == "Day"
    assert suggested[CONF_TURN_ON_SELECT_SOURCE_ENTITY] == "input_select.theme"

    # A bad option is rejected on this page, and the form stays on this side.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TURN_ON_SELECT_OPTION: "Nope"}
    )
    assert result["step_id"] == "scheduled_light_selection"
    assert result["errors"] == {"base": "turn_on_selection_invalid_option"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TURN_ON_SELECT_OPTION: "Dusk"}
    )
    assert result["step_id"] == "scheduled_light_inside"
    suggested = _suggested_values(result["data_schema"])
    assert suggested[SECTION_BEHAVIOR][CONF_TURN_ON_SELECT_ENTITY] == "select.mode"

    # Inside: clear the target — no selection page, and its values are dropped.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**EMPTY_LIGHT_SECTIONS, CONF_LIGHT_TIMEOUT: 60}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    cfg = molight_config(entry)
    outside = cfg[CONF_OUTSIDE_SCHEDULE_SETTINGS]
    assert outside[CONF_TURN_ON_SELECT_ENTITY] == "select.mode"
    assert outside[CONF_TURN_ON_SELECT_OPTION] == "Dusk"
    # The untouched source survives the round trip through the selection page.
    assert outside[CONF_TURN_ON_SELECT_SOURCE_ENTITY] == "input_select.theme"
    inside = cfg[CONF_INSIDE_SCHEDULE_SETTINGS]
    assert CONF_TURN_ON_SELECT_ENTITY not in inside
    assert CONF_TURN_ON_SELECT_OPTION not in inside
    assert CONF_TURN_ON_SELECT_SOURCE_ENTITY not in inside


@pytest.mark.asyncio
async def test_scheduled_light_options_inside_selection_page(
    hass: HomeAssistant,
) -> None:
    """The inside side reaches its own selection page and stores its result."""
    hass.states.async_set("select.mode", "Day", {"options": ["Day", "Night"]})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            CONF_OUTSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 300},
            CONF_INSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 60},
        },
    )
    await setup_entries(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**EMPTY_LIGHT_SECTIONS, CONF_LIGHT_TIMEOUT: 300}
    )
    assert result["step_id"] == "scheduled_light_inside"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_LIGHT_TIMEOUT: 60,
            SECTION_BEHAVIOR: {CONF_TURN_ON_SELECT_ENTITY: "select.mode"},
        },
    )
    assert result["step_id"] == "scheduled_light_selection"
    assert result["description_placeholders"] == {"entity_id": "select.mode"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TURN_ON_SELECT_OPTION: "Night"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    cfg = molight_config(entry)
    assert CONF_TURN_ON_SELECT_ENTITY not in cfg[CONF_OUTSIDE_SCHEDULE_SETTINGS]
    assert cfg[CONF_INSIDE_SCHEDULE_SETTINGS][CONF_TURN_ON_SELECT_OPTION] == "Night"


@pytest.mark.asyncio
async def test_scheduled_light_requires_lights(hass: HomeAssistant) -> None:
    """The shared form rejects an empty lights list when creating and editing."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: [],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "scheduled_light"
    assert result["errors"] == {CONF_LIGHTS: "lights_required"}

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            CONF_OUTSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 300},
            CONF_INSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 60},
        },
    )
    await setup_entries(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: [],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "scheduled_light"
    assert result["errors"] == {CONF_LIGHTS: "lights_required"}


@pytest.mark.asyncio
async def test_scheduled_light_explicit_entity_id_conflict(
    hass: HomeAssistant,
) -> None:
    """A taken explicit id blocks the first form — and a late one returns to it."""
    hass.states.async_set("light.taken", "off")
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            SECTION_ADVANCED: {CONF_ENTITY_ID: "taken"},
        },
    )
    assert result["step_id"] == "scheduled_light"
    # Base error: the entity_id field sits inside a collapsed section.
    assert result["errors"] == {"base": "entity_id_conflict"}

    # A free id passes the first form ...
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            SECTION_ADVANCED: {CONF_ENTITY_ID: "racy"},
        },
    )
    assert result["step_id"] == "scheduled_light_outside"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**EMPTY_LIGHT_SECTIONS, CONF_LIGHT_TIMEOUT: 300}
    )
    assert result["step_id"] == "scheduled_light_inside"
    # ... but is taken by someone else before the last form is submitted.
    hass.states.async_set("light.racy", "off")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**EMPTY_LIGHT_SECTIONS, CONF_LIGHT_TIMEOUT: 60}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "scheduled_light"
    assert result["errors"] == {"base": "entity_id_conflict"}
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_NAME] == "Hallway"
    assert suggested[SECTION_ADVANCED][CONF_ENTITY_ID] == "racy"

    # Picking another id keeps both completed settings forms.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hallway",
            CONF_LIGHTS: ["light.hallway"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            SECTION_ADVANCED: {CONF_ENTITY_ID: "free"},
        },
    )
    assert result["step_id"] == "scheduled_light_outside"
    assert _suggested_values(result["data_schema"])[CONF_LIGHT_TIMEOUT] == 300
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**EMPTY_LIGHT_SECTIONS, CONF_LIGHT_TIMEOUT: 300}
    )
    assert _suggested_values(result["data_schema"])[CONF_LIGHT_TIMEOUT] == 60
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**EMPTY_LIGHT_SECTIONS, CONF_LIGHT_TIMEOUT: 60}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENTITY_ID] == "free"


@pytest.mark.asyncio
async def test_light_flow_stores_turn_on_selection(hass: HomeAssistant) -> None:
    """A select-backed preset round-trips through the create flow."""
    hass.states.async_set(
        "select.wled_preset",
        "Warm White",
        {"options": ["Warm White", "Christmas"]},
    )
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "WLED",
            CONF_LIGHTS: ["light.wled"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {CONF_TURN_ON_SELECT_ENTITY: "select.wled_preset"},
        },
    )
    assert result["step_id"] == "light_selection"
    option_marker = next(
        marker
        for marker in result["data_schema"].schema
        if str(marker) == CONF_TURN_ON_SELECT_OPTION
    )
    assert isinstance(option_marker, vol.Required)
    option_selector = _selector_config(result, CONF_TURN_ON_SELECT_OPTION)
    assert option_selector["entity_id"] == "select.wled_preset"
    assert option_selector["hide_states"] == ["unavailable", "unknown"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TURN_ON_SELECT_OPTION: "  Christmas  "}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TURN_ON_SELECT_ENTITY] == "select.wled_preset"
    assert result["data"][CONF_TURN_ON_SELECT_OPTION] == "Christmas"


@pytest.mark.asyncio
async def test_light_flow_rejects_invalid_turn_on_fallback(
    hass: HomeAssistant,
) -> None:
    """The required fixed fallback must be offered by the target."""
    hass.states.async_set(
        "select.wled_preset",
        "Christmas",
        {"options": ["Christmas", "Warm White"]},
    )
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "WLED",
            CONF_LIGHTS: ["light.wled"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {CONF_TURN_ON_SELECT_ENTITY: "select.wled_preset"},
        },
    )
    assert result["step_id"] == "light_selection"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TURN_ON_SELECT_OPTION: "Missing"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "turn_on_selection_invalid_option"}


@pytest.mark.asyncio
async def test_light_flow_stores_turn_on_selection_source(
    hass: HomeAssistant,
) -> None:
    """A compatible source entity and mandatory fixed fallback are stored."""
    hass.states.async_set(
        "select.wled_preset", "Warm White", {"options": ["Warm White", "Christmas"]}
    )
    hass.states.async_set(
        "input_select.outdoor_theme",
        "Christmas",
        {"options": ["Christmas", "Game Night"]},
    )
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "WLED",
            CONF_LIGHTS: ["light.wled"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {CONF_TURN_ON_SELECT_ENTITY: "select.wled_preset"},
        },
    )
    source_selector = _selector_config(result, CONF_TURN_ON_SELECT_SOURCE_ENTITY)
    assert source_selector["domain"] == ["input_select", "select"]
    assert source_selector["exclude_entities"] == ["select.wled_preset"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_TURN_ON_SELECT_SOURCE_ENTITY: "input_select.outdoor_theme",
            CONF_TURN_ON_SELECT_OPTION: "Warm White",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TURN_ON_SELECT_SOURCE_ENTITY] == (
        "input_select.outdoor_theme"
    )
    assert result["data"][CONF_TURN_ON_SELECT_OPTION] == "Warm White"


@pytest.mark.asyncio
async def test_light_flow_rejects_selection_target_as_source(
    hass: HomeAssistant,
) -> None:
    """Validation rejects stale/bypassed data that uses the target as source."""
    hass.states.async_set(
        "select.wled_preset", "Warm White", {"options": ["Warm White", "Christmas"]}
    )
    errors = _validate_turn_on_selection(
        hass,
        {
            CONF_TURN_ON_SELECT_ENTITY: "select.wled_preset",
            CONF_TURN_ON_SELECT_SOURCE_ENTITY: "select.wled_preset",
            CONF_TURN_ON_SELECT_OPTION: "Warm White",
        },
    )

    assert errors == {
        CONF_TURN_ON_SELECT_SOURCE_ENTITY: ("turn_on_selection_source_same_as_target")
    }


@pytest.mark.asyncio
async def test_light_flow_rejects_source_without_matching_options(
    hass: HomeAssistant,
) -> None:
    """A source whose advertised options can never reach the target is rejected."""
    hass.states.async_set(
        "select.wled_preset", "Warm White", {"options": ["Warm White", "Christmas"]}
    )
    hass.states.async_set(
        "input_select.outdoor_theme",
        "Game Night",
        {"options": ["Game Night", "Playoffs"]},
    )
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "WLED",
            CONF_LIGHTS: ["light.wled"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {CONF_TURN_ON_SELECT_ENTITY: "select.wled_preset"},
        },
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_TURN_ON_SELECT_SOURCE_ENTITY: "input_select.outdoor_theme",
            CONF_TURN_ON_SELECT_OPTION: "Warm White",
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {
        CONF_TURN_ON_SELECT_SOURCE_ENTITY: (
            "turn_on_selection_source_no_matching_options"
        )
    }


@pytest.mark.asyncio
async def test_light_options_can_clear_turn_on_selection(hass: HomeAssistant) -> None:
    """Clearing the target disables both fixed and sourced selection."""
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "WLED",
            CONF_LIGHTS: ["light.wled"],
            CONF_LIGHT_TIMEOUT: 300,
            CONF_TURN_ON_SELECT_ENTITY: "select.wled_preset",
            CONF_TURN_ON_SELECT_OPTION: "Christmas",
        },
    )
    light.add_to_hass(hass)
    await hass.config_entries.async_setup(light.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(light.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_NAME: "WLED",
            CONF_LIGHTS: ["light.wled"],
            CONF_LIGHT_TIMEOUT: 300,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    cfg = molight_config(light)
    assert CONF_TURN_ON_SELECT_ENTITY not in cfg
    assert CONF_TURN_ON_SELECT_OPTION not in cfg
    assert CONF_TURN_ON_SELECT_SOURCE_ENTITY not in cfg


@pytest.mark.asyncio
async def test_light_options_prefill_and_update_turn_on_selection(
    hass: HomeAssistant,
) -> None:
    """Editing offers the target's options and retains source/fallback values."""
    hass.states.async_set(
        "select.wled_preset",
        "Christmas",
        {"options": ["Christmas", "Warm White"]},
    )
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "WLED",
            CONF_LIGHTS: ["light.wled"],
            CONF_LIGHT_TIMEOUT: 300,
            CONF_TURN_ON_SELECT_ENTITY: "select.wled_preset",
            CONF_TURN_ON_SELECT_OPTION: "Christmas",
            CONF_TURN_ON_SELECT_SOURCE_ENTITY: "input_select.seasonal_theme",
        },
    )
    light.add_to_hass(hass)
    await hass.config_entries.async_setup(light.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(light.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_NAME: "WLED",
            CONF_LIGHTS: ["light.wled"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {CONF_TURN_ON_SELECT_ENTITY: "select.wled_preset"},
        },
    )

    assert result["step_id"] == "light_selection"
    assert _selector_config(result, CONF_TURN_ON_SELECT_OPTION)["entity_id"] == (
        "select.wled_preset"
    )
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_TURN_ON_SELECT_OPTION] == "Christmas"
    assert suggested[CONF_TURN_ON_SELECT_SOURCE_ENTITY] == (
        "input_select.seasonal_theme"
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_TURN_ON_SELECT_SOURCE_ENTITY: "input_select.desired_theme",
            CONF_TURN_ON_SELECT_OPTION: "Warm White",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    cfg = molight_config(light)
    assert cfg[CONF_TURN_ON_SELECT_SOURCE_ENTITY] == "input_select.desired_theme"
    assert cfg[CONF_TURN_ON_SELECT_OPTION] == "Warm White"


@pytest.mark.asyncio
async def test_light_flow_stores_effect_warn_options(hass: HomeAssistant) -> None:
    """Effect/warn warning fields round-trip through the create flow."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_WARNING: {
                CONF_EFFECT_TIMEOUT: 10,
                CONF_EFFECT_BRIGHTNESS: 0,
                CONF_WARN_TIMEOUT: 20,
                CONF_WARN_BRIGHTNESS: 50,
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_EFFECT_TIMEOUT] == 10
    assert data[CONF_EFFECT_BRIGHTNESS] == 0
    assert data[CONF_WARN_TIMEOUT] == 20
    assert data[CONF_WARN_BRIGHTNESS] == 50


@pytest.mark.asyncio
async def test_light_options_can_clear_warn_brightness(hass: HomeAssistant) -> None:
    """warn_brightness is optional: omitting it in options removes the override
    (falling back to keeping the current brightness) while the timeouts persist."""
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_WARN_TIMEOUT: 20,
            CONF_WARN_BRIGHTNESS: 50,
        },
    )
    light.add_to_hass(hass)
    await hass.config_entries.async_setup(light.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(light.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            SECTION_WARNING: {
                CONF_WARN_TIMEOUT: 20,
                # warn_brightness intentionally omitted — the user cleared it.
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    cfg = molight_config(light)
    assert cfg[CONF_WARN_TIMEOUT] == 20
    assert CONF_WARN_BRIGHTNESS not in cfg


@pytest.mark.asyncio
async def test_light_flow_stores_transitions(hass: HomeAssistant) -> None:
    """All four transition fields round-trip through the create flow."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {
                CONF_AUTO_ON_TRANSITION: 2,
                CONF_AUTO_OFF_TRANSITION: 3.5,
            },
            SECTION_WARNING: {
                CONF_EFFECT_TIMEOUT: 10,
                CONF_EFFECT_TRANSITION: 1,
                CONF_WARN_TIMEOUT: 20,
                CONF_WARN_TRANSITION: 20,  # equal to the timeout is allowed
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_AUTO_ON_TRANSITION] == 2
    assert data[CONF_AUTO_OFF_TRANSITION] == 3.5
    assert data[CONF_EFFECT_TRANSITION] == 1
    assert data[CONF_WARN_TRANSITION] == 20


@pytest.mark.asyncio
async def test_light_flow_rejects_stage_transition_above_timeout(
    hass: HomeAssistant,
) -> None:
    """A stage transition longer than its stage timeout is rejected — including
    a transition on a disabled (timeout 0) stage."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_WARNING: {
                CONF_EFFECT_TIMEOUT: 10,
                CONF_EFFECT_TRANSITION: 11,
                # warn disabled, so any warn fade is invalid too
                CONF_WARN_TIMEOUT: 0,
                CONF_WARN_TRANSITION: 5,
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    # The fields sit inside a collapsed section, so the first violation is
    # reported as a base error.
    assert result["errors"] == {"base": "effect_transition_too_long"}

    # Fixing both fields lets the entry be created.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_WARNING: {
                CONF_EFFECT_TIMEOUT: 10,
                CONF_EFFECT_TRANSITION: 10,
                CONF_WARN_TIMEOUT: 0,
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_light_flow_stores_colors(hass: HomeAssistant) -> None:
    """The auto-on and stage color fields round-trip through the create flow."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {CONF_AUTO_ON_COLOR_TEMP: 3000},
            SECTION_WARNING: {
                CONF_EFFECT_TIMEOUT: 10,
                CONF_EFFECT_BRIGHTNESS: 50,
                CONF_EFFECT_RGB_COLOR: [0, 0, 255],
                CONF_WARN_TIMEOUT: 20,
                CONF_WARN_RGB_COLOR: [255, 0, 0],
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_AUTO_ON_COLOR_TEMP] == 3000
    assert data[CONF_EFFECT_RGB_COLOR] == [0, 0, 255]
    assert data[CONF_WARN_RGB_COLOR] == [255, 0, 0]


@pytest.mark.asyncio
async def test_light_flow_rejects_auto_on_color_conflict(
    hass: HomeAssistant,
) -> None:
    """A turn-on can only carry one color: temp and rgb together are rejected."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_BEHAVIOR: {
                CONF_AUTO_ON_COLOR_TEMP: 3000,
                CONF_AUTO_ON_RGB_COLOR: [255, 0, 0],
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "auto_on_color_conflict"}


@pytest.mark.asyncio
async def test_light_flow_rejects_effect_color_without_brightness(
    hass: HomeAssistant,
) -> None:
    """An effect color with effect_brightness 0 (blink fully off) is rejected
    — there would be no lit stage to show the color on."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_WARNING: {
                CONF_EFFECT_TIMEOUT: 10,
                CONF_EFFECT_BRIGHTNESS: 0,
                CONF_EFFECT_RGB_COLOR: [255, 0, 0],
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "effect_color_requires_brightness"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("warning_section", "expected_error"),
    [
        (
            {
                CONF_EFFECT_BRIGHTNESS: 30,
                CONF_EFFECT_RGB_COLOR: [255, 0, 0],
            },
            "effect_color_requires_timeout",
        ),
        ({CONF_WARN_BRIGHTNESS: 50}, "warn_values_require_timeout"),
        ({CONF_WARN_RGB_COLOR: [255, 0, 0]}, "warn_values_require_timeout"),
    ],
    ids=["effect_color", "warn_brightness", "warn_color"],
)
async def test_light_flow_rejects_stage_values_on_disabled_stage(
    hass: HomeAssistant, warning_section: dict, expected_error: str
) -> None:
    """A stage brightness/color with the stage's timeout at 0 is rejected
    rather than silently ignored, mirroring the stage-fade rule."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 300,
            SECTION_WARNING: warning_section,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}


@pytest.mark.asyncio
async def test_light_flow_stores_door_and_false_off_delay(
    hass: HomeAssistant,
) -> None:
    """The door fields and the false-detection off delay round-trip through
    the create flow (they were previously only ever set on hand-built
    entries)."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Pantry",
            CONF_LIGHTS: ["light.pantry_real"],
            CONF_LIGHT_TIMEOUT: 120,
            SECTION_SENSORS: {
                CONF_DOOR_ENTITY: "binary_sensor.pantry_door",
                CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
            },
            SECTION_BEHAVIOR: {CONF_FALSE_OFF_DELAY: 30},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_DOOR_ENTITY] == "binary_sensor.pantry_door"
    assert data[CONF_DOOR_MODE] == DOOR_MODE_OPEN_CLOSE
    assert data[CONF_FALSE_OFF_DELAY] == 30

    # The created entity honors the door wiring end-to-end.
    await settle(hass)
    hass.states.async_set("light.pantry_real", "off")
    hass.states.async_set("binary_sensor.pantry_door", "on")
    await settle(hass)
    assert hass.states.get("light.pantry").state == "on"


@pytest.mark.asyncio
async def test_light_options_can_clear_door_entity(hass: HomeAssistant) -> None:
    """Removing the door reference in options clears it (replace, not merge)."""
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Pantry",
            CONF_LIGHTS: ["light.pantry_real"],
            CONF_LIGHT_TIMEOUT: 120,
            CONF_DOOR_ENTITY: "binary_sensor.pantry_door",
            CONF_DOOR_MODE: DOOR_MODE_OPEN_CLOSE,
        },
    )
    light.add_to_hass(hass)
    await hass.config_entries.async_setup(light.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(light.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_NAME: "Pantry",
            CONF_LIGHTS: ["light.pantry_real"],
            CONF_LIGHT_TIMEOUT: 120,
            # Door entity intentionally omitted — the user cleared it.
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert CONF_DOOR_ENTITY not in molight_config(light)


@pytest.mark.asyncio
async def test_light_options_validate_and_clear_transitions(
    hass: HomeAssistant,
) -> None:
    """The options flow enforces transition <= stage timeout, and omitting a
    previously set transition clears it."""
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_WARN_TIMEOUT: 20,
            CONF_WARN_TRANSITION: 5,
            CONF_AUTO_ON_TRANSITION: 2,
        },
    )
    light.add_to_hass(hass)
    await hass.config_entries.async_setup(light.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(light.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            SECTION_WARNING: {
                CONF_WARN_TIMEOUT: 20,
                CONF_WARN_TRANSITION: 21,  # > warn_timeout
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "warn_transition_too_long"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            SECTION_WARNING: {CONF_WARN_TIMEOUT: 20},
            SECTION_BEHAVIOR: {CONF_AUTO_OFF_TRANSITION: 4},
            # warn/auto-on transitions intentionally omitted — cleared.
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    cfg = molight_config(light)
    assert cfg[CONF_AUTO_OFF_TRANSITION] == 4
    assert CONF_WARN_TRANSITION not in cfg
    assert CONF_AUTO_ON_TRANSITION not in cfg


@pytest.mark.asyncio
async def test_light_flow_error_keeps_submitted_values(hass: HomeAssistant) -> None:
    """A rejected create submission comes back prefilled, not reset to defaults."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 90,
            SECTION_WARNING: {
                CONF_WARN_TIMEOUT: 20,
                CONF_WARN_TRANSITION: 21,  # > warn_timeout
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "warn_transition_too_long"}

    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_NAME] == "Hall Light"
    assert suggested[CONF_LIGHTS] == ["light.hall"]
    assert suggested[CONF_LIGHT_TIMEOUT] == 90
    assert suggested[SECTION_WARNING][CONF_WARN_TIMEOUT] == 20
    assert suggested[SECTION_WARNING][CONF_WARN_TRANSITION] == 21


@pytest.mark.asyncio
async def test_light_options_error_keeps_submitted_values(
    hass: HomeAssistant,
) -> None:
    """A rejected options submission comes back as typed, not the stored values."""
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
        },
    )
    light.add_to_hass(hass)
    await hass.config_entries.async_setup(light.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(light.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_NAME: "Hall Light Renamed",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 90,
            SECTION_WARNING: {
                CONF_WARN_TIMEOUT: 20,
                CONF_WARN_TRANSITION: 21,  # > warn_timeout
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "warn_transition_too_long"}

    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_NAME] == "Hall Light Renamed"
    assert suggested[CONF_LIGHT_TIMEOUT] == 90
    assert suggested[SECTION_WARNING][CONF_WARN_TRANSITION] == 21


@pytest.mark.asyncio
async def test_light_flow_rejects_timeout_below_occupancy_timeout(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """light_timeout < the referenced occupancy sensor's timeout is rejected."""
    # Occupancy sensor with a 30s timeout (fixture) must be set up so its
    # entity exists in the registry.
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 20,
            **EMPTY_LIGHT_CREATE_SECTIONS,
            SECTION_SENSORS: {CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy"},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_LIGHT_TIMEOUT: "light_timeout_too_short"}

    # A timeout >= 30s is accepted.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            **EMPTY_LIGHT_CREATE_SECTIONS,
            SECTION_SENSORS: {CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy"},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_occupancy_options_reject_timeout_above_light_timeout(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Raising an occupancy timeout past a dependent light's timeout is rejected."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
        },
    )
    light.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(occupancy_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 120,  # > the light's 60s timeout
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_OCCUPANCY_TIMEOUT: "occupancy_timeout_too_long"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 45,  # fits under 60s
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_occupancy_options_reject_molight_source_from_stale_form(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Options recheck a MoLight source created after the form was opened."""
    await setup_entries(hass, occupancy_entry)
    result = await hass.config_entries.options.async_init(occupancy_entry.entry_id)
    assert _selector_config(result, CONF_OCCUPANCY_SENSOR)["exclude_entities"] == [
        "binary_sensor.test_occupancy"
    ]

    other = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Other Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.other_motion",
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
    )
    await setup_entries(hass, other)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.other_occupancy",
            CONF_OCCUPANCY_TIMEOUT: 30,
            SECTION_ADVANCED: {},
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_OCCUPANCY_SENSOR: "occupancy_source_molight"}


@pytest.mark.asyncio
async def test_occupancy_source_pickers_narrow_to_occupancy_device_classes(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Both the create and options source pickers narrow to occupancy-ish
    device classes, so a door/contact sensor is never offered as a source."""
    device_classes = ["occupancy", "motion", "presence"]

    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY}
    )
    assert _selector_config(result, CONF_OCCUPANCY_SENSOR)["device_class"] == (
        device_classes
    )

    await setup_entries(hass, occupancy_entry)
    result = await hass.config_entries.options.async_init(occupancy_entry.entry_id)
    assert _selector_config(result, CONF_OCCUPANCY_SENSOR)["device_class"] == (
        device_classes
    )


@pytest.mark.asyncio
async def test_light_flow_validates_combined_timeout(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """A combined sensor's effective timeout is its largest constituent's."""
    occupancy2 = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Test Occupancy 2",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_2",
            CONF_OCCUPANCY_TIMEOUT: 45,
        },
    )
    combined = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined Occupancy",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.test_occupancy_2"],
        },
    )
    for entry in (occupancy_entry, occupancy2, combined):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )

    # 40s beats the 30s trigger but not the 45s maintain constituent.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 40,
            **EMPTY_LIGHT_CREATE_SECTIONS,
            SECTION_SENSORS: {
                CONF_OCCUPANCY_ENTITY: "binary_sensor.combined_occupancy"
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_LIGHT_TIMEOUT: "light_timeout_too_short"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 45,
            **EMPTY_LIGHT_CREATE_SECTIONS,
            SECTION_SENSORS: {
                CONF_OCCUPANCY_ENTITY: "binary_sensor.combined_occupancy"
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_light_flow_survives_cyclic_combined_sensors(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Combined sensors referencing each other must not hang timeout resolution.

    The options flow's entity picker lets combined sensors include other
    combined sensors, so a cycle is reachable; resolving the effective
    timeout must skip the back-reference and still find the simple
    constituent's timeout (30s from the fixture).
    """
    combined_a = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined A",
            CONF_TRIGGER_SENSORS: [
                "binary_sensor.test_occupancy",
                "binary_sensor.combined_b",
            ],
        },
    )
    combined_b = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined B",
            CONF_TRIGGER_SENSORS: ["binary_sensor.combined_a"],
        },
    )
    for entry in (occupancy_entry, combined_a, combined_b):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )

    # The 30s simple constituent is still found through the cycle.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 20,
            **EMPTY_LIGHT_CREATE_SECTIONS,
            SECTION_SENSORS: {CONF_OCCUPANCY_ENTITY: "binary_sensor.combined_a"},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_LIGHT_TIMEOUT: "light_timeout_too_short"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            **EMPTY_LIGHT_CREATE_SECTIONS,
            SECTION_SENSORS: {CONF_OCCUPANCY_ENTITY: "binary_sensor.combined_a"},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_light_options_can_clear_occupancy_reference(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Omitting an optional reference in the options flow actually removes it."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
        },
    )
    light.add_to_hass(hass)
    await hass.config_entries.async_setup(light.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(light.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            CONF_NAME: "Hall Light",
            CONF_LIGHTS: ["light.hall"],
            CONF_LIGHT_TIMEOUT: 60,
            # occupancy_entity intentionally omitted — the user cleared it.
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert CONF_OCCUPANCY_ENTITY not in molight_config(light)


@pytest.mark.asyncio
async def test_schedule_options_round_trip(
    hass: HomeAssistant, schedule_entry: MockConfigEntry
) -> None:
    """Editing a legacy string-edge schedule upgrades it to edge dicts."""
    schedule_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(schedule_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(schedule_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Schedule",
            "start": {"time": "20:00:00", "sun": "sunset", "combine": "latest"},
            "end": {"time": "06:00:00"},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert molight_config(schedule_entry)[CONF_TIME_WINDOWS] == [
        {
            "start": {"time": "20:00:00", "sun": "sunset", "combine": "latest"},
            "end": {"time": "06:00:00"},
        }
    ]


@pytest.mark.asyncio
async def test_schedule_options_prefill_dict_edges(hass: HomeAssistant) -> None:
    """The options form prefills a stored modern dict-edge window as-is."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Modern Schedule",
            CONF_TIME_WINDOWS: [
                {
                    "start": {"time": "20:00", "sun": "sunset", "combine": "latest"},
                    "end": {"time": "06:00"},
                }
            ],
        },
    )
    await setup_entries(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    suggested = _suggested_values(result["data_schema"])
    assert suggested["start"] == {
        "time": "20:00",
        "sun": "sunset",
        "combine": "latest",
    }
    assert suggested["end"] == {"time": "06:00"}


@pytest.mark.asyncio
async def test_config_flow_virtual_light_requires_lights(hass: HomeAssistant) -> None:
    """Virtual light config flow rejects an empty lights list."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: "Empty Light",
            CONF_LIGHTS: [],
            CONF_LIGHT_TIMEOUT: 300,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert "lights" in result["errors"]


@pytest.mark.asyncio
async def test_discover_occupancy_filters_and_creates(hass: HomeAssistant) -> None:
    """Discovery lists occupancy sensors, skips wrapped/wrong-class, bulk-creates."""
    hass.states.async_set(
        "binary_sensor.kitchen_motion",
        "off",
        {"device_class": "occupancy", "friendly_name": "Kitchen Motion"},
    )
    hass.states.async_set(
        "binary_sensor.hall_motion",
        "off",
        {"device_class": "occupancy", "friendly_name": "Hall Motion"},
    )
    # 'motion' and 'presence' are also treated as occupancy candidates.
    hass.states.async_set(
        "binary_sensor.porch_pir",
        "off",
        {"device_class": "motion", "friendly_name": "Porch PIR"},
    )
    hass.states.async_set(
        "binary_sensor.study_presence",
        "off",
        {"device_class": "presence", "friendly_name": "Study Presence"},
    )
    # Wrong device_class — must not be offered.
    hass.states.async_set("binary_sensor.front_door", "off", {"device_class": "door"})
    # Kitchen is already wrapped by an existing MoLight entry — must be hidden.
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Kitchen Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.kitchen_motion",
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
    )
    existing.add_to_hass(hass)

    result = await _reach_discovery_select(hass, "discover_occupancy")
    assert result["type"] == FlowResultType.FORM
    assert _offered_candidates(result) == {
        "binary_sensor.hall_motion",
        "binary_sensor.porch_pir",
        "binary_sensor.study_presence",
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SELECTED_ENTITIES: ["binary_sensor.hall_motion"]},
    )
    # Selecting advances to the editable-defaults step; submitting it unchanged
    # keeps the defaults.
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discover_occupancy_defaults"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {SECTION_ADVANCED: {}}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "discovery_done"
    await hass.async_block_till_done()

    created = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if molight_config(e).get(CONF_OCCUPANCY_SENSOR) == "binary_sensor.hall_motion"
    ]
    assert len(created) == 1
    # Defaults applied.
    assert created[0].data[CONF_OCCUPANCY_TIMEOUT] == 120
    assert created[0].title == "Hall Motion"


@pytest.mark.asyncio
async def test_discover_affix_targets_name(
    hass: HomeAssistant,
) -> None:
    """Target=name wraps each discovered entity's name verbatim, no entity_id."""
    hass.states.async_set(
        "binary_sensor.hall_motion",
        "off",
        {"device_class": "occupancy", "friendly_name": "Hall Motion"},
    )

    result = await _reach_discovery_select(hass, "discover_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SELECTED_ENTITIES: ["binary_sensor.hall_motion"],
            CONF_AFFIX_PREFIX: "Auto ",
            CONF_AFFIX_SUFFIX: " (occ)",
            CONF_AFFIX_TARGET: AFFIX_TARGET_NAME,
        },
    )
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {SECTION_ADVANCED: {}}
    )
    assert result["type"] == FlowResultType.ABORT
    await hass.async_block_till_done()

    created = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if molight_config(e).get(CONF_OCCUPANCY_SENSOR) == "binary_sensor.hall_motion"
    ]
    assert len(created) == 1
    assert created[0].title == "Auto Hall Motion (occ)"
    assert created[0].data[CONF_NAME] == "Auto Hall Motion (occ)"
    # Name target never pins an explicit entity_id.
    assert CONF_ENTITY_ID not in created[0].data


@pytest.mark.asyncio
async def test_discover_affix_targets_entity_id(
    hass: HomeAssistant,
) -> None:
    """Target=entity_id leaves the name, pins the affixed id, and it registers."""
    hass.states.async_set(
        "binary_sensor.hall_motion",
        "off",
        {"device_class": "occupancy", "friendly_name": "Hall Motion"},
    )

    result = await _reach_discovery_select(hass, "discover_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SELECTED_ENTITIES: ["binary_sensor.hall_motion"],
            CONF_AFFIX_SUFFIX: " virtual",
            CONF_AFFIX_TARGET: AFFIX_TARGET_ENTITY_ID,
        },
    )
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {SECTION_ADVANCED: {}}
    )
    assert result["type"] == FlowResultType.ABORT
    await hass.async_block_till_done()

    created = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if molight_config(e).get(CONF_OCCUPANCY_SENSOR) == "binary_sensor.hall_motion"
    ]
    assert len(created) == 1
    # Name is untouched; only the entity_id carries the affix.
    assert created[0].data[CONF_NAME] == "Hall Motion"
    assert created[0].data[CONF_ENTITY_ID] == "Hall Motion virtual"

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, created[0].entry_id
    )
    assert entity_id == "binary_sensor.hall_motion_virtual"


@pytest.mark.asyncio
async def test_discover_illuminance_creates_with_defaults(
    hass: HomeAssistant,
) -> None:
    """Illuminance discovery wraps lux sensors with default threshold."""
    hass.states.async_set(
        "sensor.office_lux",
        "42",
        {"device_class": "illuminance", "friendly_name": "Office Lux"},
    )
    # Wrong device_class.
    hass.states.async_set("sensor.office_temp", "21", {"device_class": "temperature"})

    result = await _reach_discovery_select(hass, "discover_illuminance")
    assert _offered_candidates(result) == {"sensor.office_lux"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SELECTED_ENTITIES: ["sensor.office_lux"]},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discover_illuminance_defaults"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.ABORT
    await hass.async_block_till_done()

    created = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if molight_config(e).get(CONF_ILLUMINANCE_SENSOR) == "sensor.office_lux"
    ]
    assert len(created) == 1
    assert created[0].data[CONF_ENTITY_TYPE] == ENTITY_TYPE_ILLUMINANCE
    assert created[0].data[CONF_ILLUMINANCE_THRESHOLD] == 10.0


@pytest.mark.asyncio
async def test_discover_light_creates_with_defaults(hass: HomeAssistant) -> None:
    """Light discovery wraps real lights (stored as a single-item list)."""
    hass.states.async_set("light.desk", "off", {"friendly_name": "Desk Lamp"})
    # Already wrapped by an existing virtual light — must be hidden.
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Ceiling",
            CONF_LIGHTS: ["light.ceiling"],
            CONF_LIGHT_TIMEOUT: 300,
        },
    )
    existing.add_to_hass(hass)
    hass.states.async_set("light.ceiling", "off", {"friendly_name": "Ceiling"})

    result = await _reach_discovery_select(hass, "discover_light")
    assert _offered_candidates(result) == {"light.desk"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SELECTED_ENTITIES: ["light.desk"]},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discover_light_defaults"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(EMPTY_LIGHT_SECTIONS)
    )
    assert result["type"] == FlowResultType.ABORT
    await hass.async_block_till_done()

    created = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if molight_config(e).get(CONF_LIGHTS) == ["light.desk"]
    ]
    assert len(created) == 1
    assert created[0].data[CONF_LIGHT_TIMEOUT] == 300


@pytest.mark.asyncio
async def test_discover_light_creates_with_turn_on_selection(
    hass: HomeAssistant,
) -> None:
    """Discovered lights complete the target-dependent selection subflow."""
    hass.states.async_set("light.desk", "off", {"friendly_name": "Desk Lamp"})
    hass.states.async_set(
        "select.desk_preset",
        "Reading",
        {"options": ["Reading", "Relax"]},
    )

    result = await _reach_discovery_select(hass, "discover_light")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ENTITIES: ["light.desk"]}
    )
    assert result["step_id"] == "discover_light_defaults"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            SECTION_BEHAVIOR: {
                CONF_TURN_ON_SELECT_ENTITY: "select.desk_preset",
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "light_selection"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TURN_ON_SELECT_OPTION: "Relax"}
    )
    assert result["type"] == FlowResultType.ABORT
    await hass.async_block_till_done()

    created = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if molight_config(entry).get(CONF_LIGHTS) == ["light.desk"]
    ]
    assert len(created) == 1
    assert created[0].data[CONF_TURN_ON_SELECT_ENTITY] == "select.desk_preset"
    assert created[0].data[CONF_TURN_ON_SELECT_OPTION] == "Relax"


@pytest.mark.asyncio
async def test_discover_defaults_step_applies_overrides(hass: HomeAssistant) -> None:
    """Edited defaults on the discovery step apply to every created entity."""
    hass.states.async_set(
        "binary_sensor.hall_motion",
        "off",
        {"device_class": "occupancy", "friendly_name": "Hall Motion"},
    )
    hass.states.async_set(
        "binary_sensor.study_presence",
        "off",
        {"device_class": "presence", "friendly_name": "Study Presence"},
    )

    result = await _reach_discovery_select(hass, "discover_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SELECTED_ENTITIES: [
                "binary_sensor.hall_motion",
                "binary_sensor.study_presence",
            ]
        },
    )
    assert result["step_id"] == "discover_occupancy_defaults"
    # A non-default timeout, entered once, applies to both created sensors.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_OCCUPANCY_TIMEOUT: 45, SECTION_ADVANCED: {}}
    )
    assert result["type"] == FlowResultType.ABORT
    await hass.async_block_till_done()

    created = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if molight_config(e).get(CONF_ENTITY_TYPE) == ENTITY_TYPE_OCCUPANCY
    ]
    assert len(created) == 2
    assert all(e.data[CONF_OCCUPANCY_TIMEOUT] == 45 for e in created)


@pytest.mark.asyncio
async def test_discover_light_defaults_validate_stage_transition(
    hass: HomeAssistant,
) -> None:
    """The light defaults step enforces the same stage-transition rule."""
    hass.states.async_set("light.desk", "off", {"friendly_name": "Desk Lamp"})

    result = await _reach_discovery_select(hass, "discover_light")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ENTITIES: ["light.desk"]}
    )
    assert result["step_id"] == "discover_light_defaults"
    # An effect transition longer than the (disabled) effect stage is rejected
    # before any entity is created.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **EMPTY_LIGHT_SECTIONS,
            SECTION_WARNING: {CONF_EFFECT_TIMEOUT: 0, CONF_EFFECT_TRANSITION: 5},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discover_light_defaults"
    assert result["errors"]


@pytest.mark.asyncio
async def test_discover_never_offers_molight_own_entities(
    hass: HomeAssistant,
) -> None:
    """A MoLight virtual entity (its own device_class match) is never suggested."""
    hass.states.async_set(
        "binary_sensor.garage_motion",
        "off",
        {"device_class": "occupancy", "friendly_name": "Garage Motion"},
    )
    # A fully set-up virtual occupancy sensor registers a binary_sensor with
    # device_class 'occupancy' owned by the molight platform.
    virtual = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Virtual Occ",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.some_source",
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
    )
    virtual.add_to_hass(hass)
    await hass.config_entries.async_setup(virtual.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    virtual_ids = {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, virtual.entry_id)
    }
    assert virtual_ids  # the virtual sensor actually created an entity

    result = await _reach_discovery_select(hass, "discover_occupancy")
    offered = _offered_candidates(result)
    # The real sensor is offered; none of MoLight's own entities are.
    assert "binary_sensor.garage_motion" in offered
    assert not (virtual_ids & offered)


@pytest.mark.asyncio
async def test_discover_aborts_when_no_candidates(hass: HomeAssistant) -> None:
    """With nothing eligible to wrap, discovery aborts cleanly."""
    result = await _start_discovery(hass, "discover_illuminance")
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_candidates"


def _motion_registry_entry(hass: HomeAssistant, uid: str, object_id: str, **kwargs):
    """Register a motion binary_sensor discovery candidate."""
    return er.async_get(hass).async_get_or_create(
        "binary_sensor",
        "test",
        uid,
        suggested_object_id=object_id,
        original_device_class="motion",
        **kwargs,
    )


def _helper_device(hass: HomeAssistant) -> dr.DeviceEntry:
    """A registered device for candidates that inherit area/labels from it."""
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", "dev")}
    )


@pytest.mark.asyncio
async def test_discover_filter_by_area(hass: HomeAssistant) -> None:
    """The area filter matches the entity's area or its device's.

    Entities that only exist as a state have neither, so any active filter
    hides them.
    """
    area = ar.async_get(hass).async_create("Kitchen")
    registry = er.async_get(hass)
    in_area = _motion_registry_entry(hass, "uid_in", "kitchen_pir")
    registry.async_update_entity(in_area.entity_id, area_id=area.id)
    _motion_registry_entry(hass, "uid_out", "hall_pir")
    device = _helper_device(hass)
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    via_device = _motion_registry_entry(
        hass, "uid_dev", "kitchen_dev_pir", device_id=device.id
    )
    # State-only candidate — would be offered unfiltered, hidden by any filter.
    hass.states.async_set("binary_sensor.floating", "off", {"device_class": "motion"})

    result = await _start_discovery(hass, "discover_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_FILTER_AREAS: [area.id]}
    )
    assert result["step_id"] == "discover_occupancy_select"
    assert _offered_candidates(result) == {in_area.entity_id, via_device.entity_id}


@pytest.mark.asyncio
async def test_discover_filter_by_label(hass: HomeAssistant) -> None:
    """The label filter matches labels on the entity or on its device."""
    label = lr.async_get(hass).async_create("Automate")
    registry = er.async_get(hass)
    tagged = _motion_registry_entry(hass, "uid_tagged", "tagged_pir")
    registry.async_update_entity(tagged.entity_id, labels={label.label_id})
    _motion_registry_entry(hass, "uid_plain", "plain_pir")
    device = _helper_device(hass)
    dr.async_get(hass).async_update_device(device.id, labels={label.label_id})
    via_device = _motion_registry_entry(hass, "uid_dev", "dev_pir", device_id=device.id)

    result = await _start_discovery(hass, "discover_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_FILTER_LABELS: [label.label_id]}
    )
    assert result["step_id"] == "discover_occupancy_select"
    assert _offered_candidates(result) == {tagged.entity_id, via_device.entity_id}


@pytest.mark.asyncio
async def test_discover_filter_no_matches_reshows(hass: HomeAssistant) -> None:
    """Filters that match nothing re-show the form, keeping the user's input."""
    hass.states.async_set(
        "binary_sensor.hall_motion", "off", {"device_class": "motion"}
    )

    result = await _start_discovery(hass, "discover_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_FILTER_AREAS: ["nowhere"]}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discover_occupancy"
    assert result["errors"] == {"base": "no_filter_matches"}
    assert _suggested_values(result["data_schema"])[CONF_FILTER_AREAS] == ["nowhere"]

    # Clearing the filters proceeds to the full checklist.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "discover_occupancy_select"
    assert _offered_candidates(result) == {"binary_sensor.hall_motion"}


@pytest.mark.asyncio
async def test_discover_preselect_none_and_empty_selection(
    hass: HomeAssistant,
) -> None:
    """Preselect off starts the checklist empty; submitting nothing errors."""
    hass.states.async_set(
        "binary_sensor.hall_motion", "off", {"device_class": "motion"}
    )
    hass.states.async_set("binary_sensor.porch_pir", "off", {"device_class": "motion"})

    result = await _start_discovery(hass, "discover_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PRESELECT_ALL: False}
    )
    assert result["step_id"] == "discover_occupancy_select"
    # Both candidates are offered, but none is pre-selected.
    schema = result["data_schema"].schema
    sel = next(v for k, v in schema.items() if str(k) == CONF_SELECTED_ENTITIES)
    assert {o["value"] for o in sel.config["options"]} == {
        "binary_sensor.hall_motion",
        "binary_sensor.porch_pir",
    }
    assert _offered_candidates(result) == set()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ENTITIES: []}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discover_occupancy_select"
    assert result["errors"] == {"base": "no_entities_selected"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SELECTED_ENTITIES: ["binary_sensor.hall_motion"]}
    )
    assert result["step_id"] == "discover_occupancy_defaults"


# ---------------------------------------------------------------------------
# Manual create — optional explicit entity_id
# ---------------------------------------------------------------------------


async def _reach_occupancy_form(hass: HomeAssistant) -> dict:
    """Advance the manual flow to the occupancy create form."""
    result = await _start_create(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY}
    )


@pytest.mark.asyncio
async def test_manual_explicit_entity_id(hass: HomeAssistant) -> None:
    """An explicit entity_id is stored and used as the registered id."""
    result = await _reach_occupancy_form(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.hall_motion",
            CONF_OCCUPANCY_TIMEOUT: 60,
            SECTION_ADVANCED: {CONF_ENTITY_ID: "hall_presence"},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENTITY_ID] == "hall_presence"
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, result["result"].entry_id
    )
    assert entity_id == "binary_sensor.hall_presence"


@pytest.mark.asyncio
async def test_manual_explicit_entity_id_is_normalized(hass: HomeAssistant) -> None:
    """A typed domain prefix is stripped and the rest slugified."""
    result = await _reach_occupancy_form(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.hall_motion",
            CONF_OCCUPANCY_TIMEOUT: 60,
            SECTION_ADVANCED: {CONF_ENTITY_ID: "binary_sensor.Hall Presence"},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENTITY_ID] == "hall_presence"
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("binary_sensor", DOMAIN, result["result"].entry_id)
        == "binary_sensor.hall_presence"
    )


@pytest.mark.asyncio
async def test_manual_whitespace_entity_id_treated_as_blank(
    hass: HomeAssistant,
) -> None:
    """A whitespace-only entity_id behaves like leaving the field empty."""
    result = await _reach_occupancy_form(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.hall_motion",
            CONF_OCCUPANCY_TIMEOUT: 60,
            SECTION_ADVANCED: {CONF_ENTITY_ID: "   "},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_ENTITY_ID not in result["data"]
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("binary_sensor", DOMAIN, result["result"].entry_id)
        == "binary_sensor.hall_occupancy"  # derived from the name
    )


@pytest.mark.asyncio
async def test_manual_explicit_entity_id_conflict_errors(
    hass: HomeAssistant,
) -> None:
    """An explicit entity_id that is already taken hard-blocks the form."""
    hass.states.async_set("binary_sensor.taken", "off")

    result = await _reach_occupancy_form(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.hall_motion",
            CONF_OCCUPANCY_TIMEOUT: 60,
            SECTION_ADVANCED: {CONF_ENTITY_ID: "taken"},
        },
    )
    assert result["type"] == FlowResultType.FORM
    # Base error: the entity_id field sits inside a collapsed section.
    assert result["errors"] == {"base": "entity_id_conflict"}


@pytest.mark.asyncio
async def test_manual_blank_entity_id_conflict_confirm_proceed(
    hass: HomeAssistant,
) -> None:
    """Blank id whose name-derived id collides prompts, then proceeds to _2."""
    hass.states.async_set("binary_sensor.hall", "off")

    result = await _reach_occupancy_form(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.hall_motion",
            CONF_OCCUPANCY_TIMEOUT: 60,
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "confirm_entity_id"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "entity_id_proceed"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Name-derived path pins nothing; HA does the _2 dedupe at registration.
    assert CONF_ENTITY_ID not in result["data"]
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, result["result"].entry_id
    )
    assert entity_id == "binary_sensor.hall_2"


@pytest.mark.asyncio
async def test_manual_blank_entity_id_conflict_confirm_change(
    hass: HomeAssistant,
) -> None:
    """Choosing 'go back' returns to the create form to set an entity_id."""
    hass.states.async_set("binary_sensor.hall", "off")

    result = await _reach_occupancy_form(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.hall_motion",
            CONF_OCCUPANCY_TIMEOUT: 60,
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "entity_id_change"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "occupancy"
    # The prior input is offered back as suggested values on the markers.
    suggested = {
        marker.schema: marker.description["suggested_value"]
        for marker in result["data_schema"].schema
        if getattr(marker, "description", None)
        and "suggested_value" in marker.description
    }
    assert suggested.get(CONF_NAME) == "Hall"

    # Now setting a distinct id creates the entry cleanly.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.hall_motion",
            CONF_OCCUPANCY_TIMEOUT: 60,
            SECTION_ADVANCED: {CONF_ENTITY_ID: "hall_presence"},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENTITY_ID] == "hall_presence"


@pytest.mark.asyncio
async def test_light_entity_id_parallels_switch(hass: HomeAssistant) -> None:
    """A light's explicit id gives the companion switch a matching _auto_off id."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Kitchen",
            CONF_LIGHTS: ["light.kitchen_real"],
            CONF_LIGHT_TIMEOUT: 300,
            CONF_ENTITY_ID: "kitchen_virtual",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("light", DOMAIN, entry.entry_id)
        == "light.kitchen_virtual"
    )
    assert (
        registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_auto_off")
        == "switch.kitchen_virtual_auto_off"
    )


# ---------------------------------------------------------------------------
# Bulk-assign a virtual sensor to many virtual lights
# ---------------------------------------------------------------------------


async def _reach_assign_kind(hass: HomeAssistant, kind_step: str) -> dict:
    """Init the flow and advance to a bulk-assign sensor form (sensor + mode)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "assign_sensor"}
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "assign_sensor"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": kind_step}
    )


def _light_entry(
    name: str, obj_id: str, *, timeout: int = 60, **refs
) -> MockConfigEntry:
    """A virtual-light entry with a pinned entity_id for predictable ids."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: name,
            CONF_LIGHTS: [f"light.{obj_id}_real"],
            CONF_LIGHT_TIMEOUT: timeout,
            CONF_ENTITY_ID: obj_id,
            **refs,
        },
    )


@pytest.mark.asyncio
async def test_assign_occupancy_adds_and_removes(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Bulk-assign pre-selects current users; the submitted set is authoritative."""
    # Occupancy sensor (30s timeout fixture) registers binary_sensor.test_occupancy.
    already_light = _light_entry(
        "Hall", "hall", occupancy_entity="binary_sensor.test_occupancy"
    )
    fresh_light = _light_entry("Kitchen", "kitchen")
    await setup_entries(hass, occupancy_entry, already_light, fresh_light)

    result = await _reach_assign_kind(hass, "assign_occupancy")
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "assign_occupancy"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ASSIGN_SENSOR: "binary_sensor.test_occupancy",
            CONF_ASSIGN_ROLE: ASSIGN_ROLE_REGULAR,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "assign_lights"
    # The light already wired to this sensor is pre-selected.
    assert result["data_schema"]({})[CONF_ASSIGN_LIGHTS] == ["light.hall"]

    # Swap: add the kitchen light, drop the hall light.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ASSIGN_LIGHTS: ["light.kitchen"]}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "assign_done"
    assert result["description_placeholders"] == {"assigned": "1", "removed": "1"}
    await hass.async_block_till_done()

    assert (
        molight_config(fresh_light)[CONF_OCCUPANCY_ENTITY]
        == "binary_sensor.test_occupancy"
    )
    assert CONF_OCCUPANCY_ENTITY not in molight_config(already_light)


@pytest.mark.asyncio
async def test_assign_occupancy_maintain_uses_maintain_key(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """The maintain role writes the maintain reference, not the regular one."""
    light = _light_entry("Kitchen", "kitchen")
    await setup_entries(hass, occupancy_entry, light)

    result = await _reach_assign_kind(hass, "assign_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ASSIGN_SENSOR: "binary_sensor.test_occupancy",
            CONF_ASSIGN_ROLE: ASSIGN_ROLE_MAINTAIN,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ASSIGN_LIGHTS: ["light.kitchen"]}
    )
    assert result["type"] == FlowResultType.ABORT
    await hass.async_block_till_done()

    cfg = molight_config(light)
    assert cfg[CONF_MAINTAIN_OCCUPANCY_ENTITY] == "binary_sensor.test_occupancy"
    assert CONF_OCCUPANCY_ENTITY not in cfg


@pytest.mark.asyncio
async def test_assign_occupancy_skips_short_timeout(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """A light whose turn-off timeout is below the sensor's is skipped and reported."""
    short = _light_entry("Closet", "closet", timeout=20)  # < 30s sensor timeout
    await setup_entries(hass, occupancy_entry, short)

    result = await _reach_assign_kind(hass, "assign_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ASSIGN_SENSOR: "binary_sensor.test_occupancy",
            CONF_ASSIGN_ROLE: ASSIGN_ROLE_REGULAR,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ASSIGN_LIGHTS: ["light.closet"]}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "assign_done_skipped"
    assert result["description_placeholders"]["assigned"] == "0"
    # Skipped lights are reported by their friendly name for identification.
    assert result["description_placeholders"]["skipped"] == "Closet"
    await hass.async_block_till_done()

    assert CONF_OCCUPANCY_ENTITY not in molight_config(short)


@pytest.mark.asyncio
async def test_assign_illuminance_sets_mode(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """Assigning an illuminance sensor also stamps the chosen mode."""
    light = _light_entry("Kitchen", "kitchen")
    await setup_entries(hass, illuminance_entry, light)

    result = await _reach_assign_kind(hass, "assign_illuminance")
    assert result["step_id"] == "assign_illuminance"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ASSIGN_SENSOR: "binary_sensor.test_illuminance",
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_GATE,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ASSIGN_LIGHTS: ["light.kitchen"]}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "assign_done"
    await hass.async_block_till_done()

    cfg = molight_config(light)
    assert cfg[CONF_ILLUMINANCE_ENTITY] == "binary_sensor.test_illuminance"
    assert cfg[CONF_ILLUMINANCE_MODE] == ILLUMINANCE_MODE_GATE


@pytest.mark.asyncio
async def test_assign_aborts_when_no_lights(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """With no virtual lights, the second step aborts cleanly."""
    await setup_entries(hass, occupancy_entry)

    result = await _reach_assign_kind(hass, "assign_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ASSIGN_SENSOR: "binary_sensor.test_occupancy",
            CONF_ASSIGN_ROLE: ASSIGN_ROLE_REGULAR,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_lights"


# ---------------------------------------------------------------------------
# Manual create steps not covered above
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_combined_occupancy(hass: HomeAssistant) -> None:
    """The combined flow rejects an empty trigger list, then creates the entry."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY}
    )
    assert result["step_id"] == "combined_occupancy"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Combined", CONF_TRIGGER_SENSORS: [], SECTION_ADVANCED: {}},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_TRIGGER_SENSORS: "trigger_sensors_required"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.occ_a"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.occ_a"],
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "occupancy_sensor_role_overlap"}
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_TRIGGER_SENSORS] == ["binary_sensor.occ_a"]
    assert suggested[CONF_MAINTAIN_SENSORS] == ["binary_sensor.occ_a"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.occ_a"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.occ_b"],
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENTITY_TYPE] == ENTITY_TYPE_COMBINED_OCCUPANCY
    assert result["data"][CONF_TRIGGER_SENSORS] == ["binary_sensor.occ_a"]
    assert result["data"][CONF_MAINTAIN_SENSORS] == ["binary_sensor.occ_b"]


@pytest.mark.asyncio
async def test_config_flow_illuminance(hass: HomeAssistant) -> None:
    """Full config flow creates an illuminance sensor entry."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE}
    )
    assert result["step_id"] == "illuminance"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Hall Illuminance",
            CONF_ILLUMINANCE_SENSOR: "sensor.hall_lux",
            CONF_ILLUMINANCE_THRESHOLD: 25.0,
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hall Illuminance"
    assert result["data"][CONF_ILLUMINANCE_SENSOR] == "sensor.hall_lux"
    assert result["data"][CONF_ILLUMINANCE_THRESHOLD] == 25.0
    # Unsubmitted hysteresis falls back to its schema default.
    assert result["data"][CONF_ILLUMINANCE_HYSTERESIS] == 0.0


# ---------------------------------------------------------------------------
# Options flows not covered above
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_illuminance_options_round_trip(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """Illuminance options replace the stored configuration."""
    await setup_entries(hass, illuminance_entry)

    result = await hass.config_entries.options.async_init(illuminance_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "illuminance"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Illuminance",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux_2",
            CONF_ILLUMINANCE_THRESHOLD: 42.0,
            CONF_ILLUMINANCE_HYSTERESIS: 2.5,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    cfg = molight_config(illuminance_entry)
    assert cfg[CONF_ILLUMINANCE_SENSOR] == "sensor.lux_2"
    assert cfg[CONF_ILLUMINANCE_THRESHOLD] == 42.0
    assert cfg[CONF_ILLUMINANCE_HYSTERESIS] == 2.5


@pytest.mark.asyncio
async def test_combined_options_reject_constituent_above_light_timeout(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Adding a slow constituent that outgrows a dependent light is rejected."""
    big = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Big Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_big",
            CONF_OCCUPANCY_TIMEOUT: 120,
        },
    )
    combined = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )
    light = _light_entry("Hall", "hall", occupancy_entity="binary_sensor.combined")
    await setup_entries(hass, occupancy_entry, big, combined, light)

    result = await hass.config_entries.options.async_init(combined.entry_id)
    assert result["step_id"] == "combined_occupancy"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_NAME: "Combined", CONF_TRIGGER_SENSORS: []},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_TRIGGER_SENSORS: "trigger_sensors_required"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: [
                "binary_sensor.test_occupancy",
                "binary_sensor.big_occupancy",  # 120s > the light's 60s
            ],
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "occupancy_timeout_too_long"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_combined_options_reject_direct_self_reference(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """A combined occupancy sensor cannot include its own entity as a source."""
    combined = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )
    await setup_entries(hass, occupancy_entry, combined)

    result = await hass.config_entries.options.async_init(combined.entry_id)
    assert _selector_config(result, CONF_TRIGGER_SENSORS)["exclude_entities"] == [
        "binary_sensor.combined"
    ]
    assert _selector_config(result, CONF_MAINTAIN_SENSORS)["exclude_entities"] == [
        "binary_sensor.combined"
    ]


@pytest.mark.asyncio
async def test_combined_options_reject_indirect_cycle(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Editing an outer sensor cannot close a cycle through another combined."""
    outer = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Outer",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )
    inner = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Inner",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )
    await setup_entries(hass, occupancy_entry, outer, inner)

    result = await hass.config_entries.options.async_init(outer.entry_id)
    assert _selector_config(result, CONF_TRIGGER_SENSORS)["exclude_entities"] == [
        "binary_sensor.outer"
    ]

    # The form was rendered while Inner was safe. Make Inner point back to Outer
    # before submitting to prove the flow-level validation catches stale forms.
    hass.config_entries.async_update_entry(
        inner,
        options={
            CONF_NAME: "Inner",
            CONF_TRIGGER_SENSORS: ["binary_sensor.outer"],
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Outer",
            CONF_TRIGGER_SENSORS: ["binary_sensor.inner"],
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "combined_occupancy_cycle"}


@pytest.mark.asyncio
async def test_combined_options_allow_shared_acyclic_dependency(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Two branches may share a constituent when neither points back upstream."""
    left = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Left",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )
    right = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Right",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )
    outer = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Outer",
            CONF_TRIGGER_SENSORS: ["binary_sensor.left"],
        },
    )
    await setup_entries(hass, occupancy_entry, left, right, outer)

    result = await hass.config_entries.options.async_init(outer.entry_id)
    assert _selector_config(result, CONF_TRIGGER_SENSORS)["exclude_entities"] == [
        "binary_sensor.outer"
    ]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Outer",
            CONF_TRIGGER_SENSORS: ["binary_sensor.left"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.right"],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_combined_options_reject_overlapping_roles(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Options cannot assign one occupancy entity to both constituent roles."""
    combined = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )
    await setup_entries(hass, occupancy_entry, combined)

    result = await hass.config_entries.options.async_init(combined.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "occupancy_sensor_role_overlap"}
    suggested = _suggested_values(result["data_schema"])
    assert suggested[CONF_TRIGGER_SENSORS] == ["binary_sensor.test_occupancy"]
    assert suggested[CONF_MAINTAIN_SENSORS] == ["binary_sensor.test_occupancy"]


@pytest.mark.asyncio
async def test_occupancy_options_validate_through_nested_combined(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Raising a timeout checks lights depending on it through nested combineds."""
    inner = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Inner Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
        },
    )
    outer = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Outer Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.inner_combined"],
        },
    )
    light = _light_entry(
        "Hall", "hall", occupancy_entity="binary_sensor.outer_combined"
    )
    await setup_entries(hass, occupancy_entry, inner, outer, light)

    result = await hass.config_entries.options.async_init(occupancy_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 90,  # > the light's 60s timeout
            SECTION_ADVANCED: {
                CONF_FALSE_DETECTION_GRACE: 0,
                CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT: 60,
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_OCCUPANCY_TIMEOUT: "occupancy_timeout_too_long"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 45,  # fits under 60s
            SECTION_ADVANCED: {
                CONF_FALSE_DETECTION_GRACE: 0,
                CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT: 60,
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_assign_schedule_sets_mode(
    hass: HomeAssistant, schedule_entry: MockConfigEntry
) -> None:
    """Assigning a schedule sensor stamps the schedule reference and mode."""
    light = _light_entry("Kitchen", "kitchen")
    await setup_entries(hass, schedule_entry, light)

    result = await _reach_assign_kind(hass, "assign_schedule")
    assert result["step_id"] == "assign_schedule"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ASSIGN_SENSOR: "binary_sensor.test_schedule",
            CONF_SCHEDULE_MODE: SCHEDULE_MODE_GATE,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ASSIGN_LIGHTS: ["light.kitchen"]}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "assign_done"
    await hass.async_block_till_done()

    cfg = molight_config(light)
    assert cfg[CONF_SCHEDULE_ENTITY] == "binary_sensor.test_schedule"
    assert cfg[CONF_SCHEDULE_MODE] == SCHEDULE_MODE_GATE


# ---------------------------------------------------------------------------
# In-place Virtual Light type conversion
# ---------------------------------------------------------------------------


async def _reach_conversion(hass: HomeAssistant, direction: str) -> dict:
    """Start the add-entry flow and choose one conversion direction."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "convert_lights"}
    )
    assert result["step_id"] == "convert_lights"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": direction}
    )


@pytest.mark.asyncio
async def test_conversion_picker_labels_and_sorts_lights(hass: HomeAssistant) -> None:
    """The picker shows friendly light and schedule names in light-name order."""
    hass.states.async_set(
        "binary_sensor.night", "off", {"friendly_name": "Night Schedule"}
    )
    hass.states.async_set(
        "binary_sensor.dusk", "off", {"friendly_name": "Dusk Schedule"}
    )
    await setup_entries(
        hass,
        _light_entry(
            "Zulu",
            "zulu",
            schedule_entity="binary_sensor.night",
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
        _light_entry(
            "Alpha",
            "alpha",
            schedule_entity="binary_sensor.dusk",
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
    )

    result = await _reach_conversion(hass, "convert_to_scheduled")

    assert _selector_config(result, CONF_CONVERT_LIGHTS)["options"] == [
        {"value": "light.alpha", "label": "Alpha — Dusk Schedule"},
        {"value": "light.zulu", "label": "Zulu — Night Schedule"},
    ]


@pytest.mark.asyncio
async def test_convert_gated_light_to_scheduled_in_place(
    hass: HomeAssistant,
) -> None:
    """Conversion retains identity and maps gate settings into two profiles."""
    source = _light_entry(
        "Kitchen",
        "kitchen",
        schedule_entity="binary_sensor.night",
        schedule_mode=SCHEDULE_MODE_GATE,
        occupancy_entity="binary_sensor.occupancy",
        maintain_occupancy_entity="binary_sensor.maintain",
        illuminance_entity="binary_sensor.illuminance",
        door_entity="binary_sensor.door",
        hold_entities=["input_boolean.guest"],
    )
    # Exercise conversion from the effective options configuration, not just
    # untouched creation data.
    light = MockConfigEntry(
        domain=DOMAIN,
        data=dict(source.data),
        options={
            key: value
            for key, value in source.data.items()
            if key not in (CONF_ENTITY_TYPE, CONF_ENTITY_ID)
        },
    )
    soft_gate = _light_entry(
        "Pantry",
        "pantry",
        schedule_entity="binary_sensor.dusk",
        schedule_mode=SCHEDULE_MODE_GATE_KEEP,
    )
    hass.states.async_set("binary_sensor.night", "off", {"friendly_name": "Night"})
    hass.states.async_set("binary_sensor.dusk", "off", {"friendly_name": "Dusk"})
    await setup_entries(hass, light, soft_gate)
    original_entry_id = light.entry_id
    original_entities = {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), light.entry_id
        )
    }

    result = await _reach_conversion(hass, "convert_to_scheduled")
    assert result["step_id"] == "convert_to_scheduled"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONVERT_LIGHTS: ["light.kitchen", "light.pantry"]},
    )
    assert result["step_id"] == "confirm_convert_to_scheduled"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONFIRM_CONVERSION: False}
    )
    assert result["step_id"] == "confirm_convert_to_scheduled"
    assert result["errors"] == {CONF_CONFIRM_CONVERSION: "confirmation_required"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONFIRM_CONVERSION: True}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "conversion_done"
    assert result["description_placeholders"] == {"count": "2"}
    await hass.async_block_till_done()

    assert light.entry_id == original_entry_id
    assert light.options == {}
    cfg = molight_config(light)
    assert cfg[CONF_ENTITY_TYPE] == ENTITY_TYPE_SCHEDULED_LIGHT
    assert cfg[CONF_ENTITY_ID] == "kitchen"
    assert cfg[CONF_SCHEDULE_ENTITY] == "binary_sensor.night"
    assert cfg[CONF_SCHEDULE_END_ACTION] == SCHEDULE_END_ACTION_TURN_OFF
    inside = cfg[CONF_INSIDE_SCHEDULE_SETTINGS]
    outside = cfg[CONF_OUTSIDE_SCHEDULE_SETTINGS]
    assert inside[CONF_OCCUPANCY_ENTITY] == "binary_sensor.occupancy"
    assert inside[CONF_MAINTAIN_OCCUPANCY_ENTITY] == "binary_sensor.maintain"
    assert inside[CONF_ILLUMINANCE_ENTITY] == "binary_sensor.illuminance"
    assert inside[CONF_DOOR_ENTITY] == "binary_sensor.door"
    for key in (
        CONF_OCCUPANCY_ENTITY,
        CONF_MAINTAIN_OCCUPANCY_ENTITY,
        CONF_ILLUMINANCE_ENTITY,
        CONF_DOOR_ENTITY,
    ):
        assert key not in outside
    assert outside[CONF_HOLD_ENTITIES] == ["input_boolean.guest"]
    assert {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), light.entry_id
        )
    } == original_entities
    soft_cfg = molight_config(soft_gate)
    assert soft_cfg[CONF_ENTITY_TYPE] == ENTITY_TYPE_SCHEDULED_LIGHT
    assert soft_cfg[CONF_SCHEDULE_ENTITY] == "binary_sensor.dusk"
    assert soft_cfg[CONF_SCHEDULE_END_ACTION] == SCHEDULE_END_ACTION_KEEP


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("end_action", "expected_mode"),
    [
        (SCHEDULE_END_ACTION_KEEP, SCHEDULE_MODE_GATE_KEEP),
        (SCHEDULE_END_ACTION_SWITCH, SCHEDULE_MODE_GATE_SWITCH),
        (SCHEDULE_END_ACTION_TURN_OFF, SCHEDULE_MODE_GATE),
    ],
)
async def test_convert_scheduled_light_back_to_gate(
    hass: HomeAssistant, end_action: str, expected_mode: str
) -> None:
    """Reverse conversion keeps the inside profile and maps the end action."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
            CONF_ENTITY_ID: "hall",
            CONF_NAME: "Hall",
            CONF_LIGHTS: ["light.hall_real"],
            CONF_SCHEDULE_ENTITY: "binary_sensor.night",
            CONF_SCHEDULE_END_ACTION: end_action,
            CONF_OUTSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 300},
            CONF_INSIDE_SCHEDULE_SETTINGS: {
                CONF_LIGHT_TIMEOUT: 45,
                CONF_OCCUPANCY_ENTITY: "binary_sensor.hall_occupancy",
            },
        },
    )
    hass.states.async_set("binary_sensor.night", "on")
    await setup_entries(hass, entry)

    result = await _reach_conversion(hass, "convert_to_regular")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONVERT_LIGHTS: ["light.hall"]}
    )
    assert result["step_id"] == "confirm_convert_to_regular"
    assert result["description_placeholders"] == {"lights": "Hall"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONFIRM_CONVERSION: True}
    )
    assert result["reason"] == "conversion_done"
    await hass.async_block_till_done()

    cfg = molight_config(entry)
    assert cfg[CONF_ENTITY_TYPE] == ENTITY_TYPE_LIGHT
    assert cfg[CONF_ENTITY_ID] == "hall"
    assert cfg[CONF_SCHEDULE_ENTITY] == "binary_sensor.night"
    assert cfg[CONF_SCHEDULE_MODE] == expected_mode
    assert cfg[CONF_LIGHT_TIMEOUT] == 45
    assert cfg[CONF_OCCUPANCY_ENTITY] == "binary_sensor.hall_occupancy"
    assert CONF_OUTSIDE_SCHEDULE_SETTINGS not in cfg
    assert CONF_INSIDE_SCHEDULE_SETTINGS not in cfg


async def _convert(hass: HomeAssistant, direction: str, light: str) -> None:
    """Run one light through a conversion direction to completion."""
    result = await _reach_conversion(hass, direction)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONVERT_LIGHTS: [light]}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONFIRM_CONVERSION: True}
    )
    assert result["reason"] == "conversion_done"
    await hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [SCHEDULE_MODE_GATE, SCHEDULE_MODE_GATE_SWITCH, SCHEDULE_MODE_GATE_KEEP],
)
async def test_conversion_round_trip_restores_flat_config(
    hass: HomeAssistant, mode: str
) -> None:
    """Gated → scheduled → gated is lossless and leaks no top-level keys."""
    source = _light_entry(
        "Kitchen",
        "kitchen",
        timeout=45,
        schedule_entity="binary_sensor.night",
        schedule_mode=mode,
        occupancy_entity="binary_sensor.occupancy",
        maintain_occupancy_entity="binary_sensor.maintain",
        illuminance_entity="binary_sensor.illuminance",
        door_entity="binary_sensor.door",
        hold_entities=["input_boolean.guest"],
    )
    original = dict(source.data)
    hass.states.async_set("binary_sensor.night", "off")
    await setup_entries(hass, source)

    await _convert(hass, "convert_to_scheduled", "light.kitchen")
    cfg = molight_config(source)
    assert cfg[CONF_ENTITY_TYPE] == ENTITY_TYPE_SCHEDULED_LIGHT
    assert (
        cfg[CONF_SCHEDULE_END_ACTION]
        == {
            SCHEDULE_MODE_GATE: SCHEDULE_END_ACTION_TURN_OFF,
            SCHEDULE_MODE_GATE_SWITCH: SCHEDULE_END_ACTION_SWITCH,
            SCHEDULE_MODE_GATE_KEEP: SCHEDULE_END_ACTION_KEEP,
        }[mode]
    )
    for profile in (CONF_INSIDE_SCHEDULE_SETTINGS, CONF_OUTSIDE_SCHEDULE_SETTINGS):
        for key in (
            CONF_ENTITY_TYPE,
            CONF_ENTITY_ID,
            CONF_NAME,
            CONF_LIGHTS,
            CONF_SCHEDULE_ENTITY,
            CONF_SCHEDULE_MODE,
            CONF_SCHEDULE_END_ACTION,
            CONF_INSIDE_SCHEDULE_SETTINGS,
            CONF_OUTSIDE_SCHEDULE_SETTINGS,
        ):
            assert key not in cfg[profile], (profile, key)
    assert cfg[CONF_OUTSIDE_SCHEDULE_SETTINGS] == {
        CONF_LIGHT_TIMEOUT: 45,
        CONF_HOLD_ENTITIES: ["input_boolean.guest"],
    }

    await _convert(hass, "convert_to_regular", "light.kitchen")
    assert source.options == {}
    assert molight_config(source) == original


@pytest.mark.asyncio
async def test_conversion_excludes_follow_mode_lights(hass: HomeAssistant) -> None:
    """Follow schedules cannot be reinterpreted as settings selectors."""
    await setup_entries(
        hass,
        _light_entry(
            "Porch",
            "porch",
            schedule_entity="binary_sensor.night",
            schedule_mode="follow",
        ),
    )
    result = await _reach_conversion(hass, "convert_to_scheduled")
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_gated_lights"


@pytest.mark.asyncio
async def test_conversion_requires_at_least_one_selected_light(
    hass: HomeAssistant,
) -> None:
    """Submitting an empty eligible-light selection stays on the form."""
    await setup_entries(
        hass,
        _light_entry(
            "Kitchen",
            "kitchen",
            schedule_entity="binary_sensor.night",
            schedule_mode=SCHEDULE_MODE_GATE,
        ),
    )

    result = await _reach_conversion(hass, "convert_to_scheduled")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONVERT_LIGHTS: []}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "convert_to_scheduled"
    assert result["errors"] == {CONF_CONVERT_LIGHTS: "no_lights_selected"}


@pytest.mark.asyncio
async def test_convert_to_regular_aborts_without_scheduled_lights(
    hass: HomeAssistant,
) -> None:
    """Reverse conversion reports when there are no eligible lights."""
    await setup_entries(hass, _light_entry("Kitchen", "kitchen"))

    result = await _reach_conversion(hass, "convert_to_regular")

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_scheduled_lights"


@pytest.mark.asyncio
async def test_bulk_conversion_is_atomic_when_target_becomes_ineligible(
    hass: HomeAssistant,
) -> None:
    """A stale target aborts before any of the selected entries are rewritten."""
    kitchen = _light_entry(
        "Kitchen",
        "kitchen",
        schedule_entity="binary_sensor.night",
        schedule_mode=SCHEDULE_MODE_GATE,
    )
    pantry = _light_entry(
        "Pantry",
        "pantry",
        schedule_entity="binary_sensor.dusk",
        schedule_mode=SCHEDULE_MODE_GATE,
    )
    await setup_entries(hass, kitchen, pantry)
    kitchen_data = dict(kitchen.data)
    kitchen_options = dict(kitchen.options)

    result = await _reach_conversion(hass, "convert_to_scheduled")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONVERT_LIGHTS: ["light.kitchen", "light.pantry"]},
    )
    assert result["step_id"] == "confirm_convert_to_scheduled"

    # Simulate an options/config edit after selection but before confirmation.
    hass.config_entries.async_update_entry(
        pantry,
        data={**pantry.data, CONF_SCHEDULE_MODE: SCHEDULE_MODE_FOLLOW},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONFIRM_CONVERSION: True}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "conversion_targets_changed"
    assert dict(kitchen.data) == kitchen_data
    assert dict(kitchen.options) == kitchen_options
    assert molight_config(kitchen)[CONF_ENTITY_TYPE] == ENTITY_TYPE_LIGHT
    assert molight_config(pantry)[CONF_ENTITY_TYPE] == ENTITY_TYPE_LIGHT


@pytest.mark.asyncio
async def test_bulk_conversion_is_atomic_when_target_is_removed(
    hass: HomeAssistant,
) -> None:
    """A removed target aborts before any surviving entry is rewritten."""
    kitchen = _light_entry(
        "Kitchen",
        "kitchen",
        schedule_entity="binary_sensor.night",
        schedule_mode=SCHEDULE_MODE_GATE,
    )
    pantry = _light_entry(
        "Pantry",
        "pantry",
        schedule_entity="binary_sensor.dusk",
        schedule_mode=SCHEDULE_MODE_GATE,
    )
    await setup_entries(hass, kitchen, pantry)
    kitchen_data = dict(kitchen.data)
    kitchen_options = dict(kitchen.options)

    result = await _reach_conversion(hass, "convert_to_scheduled")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONVERT_LIGHTS: ["light.kitchen", "light.pantry"]},
    )
    assert result["step_id"] == "confirm_convert_to_scheduled"

    assert await hass.config_entries.async_remove(pantry.entry_id)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONFIRM_CONVERSION: True}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "conversion_targets_changed"
    assert dict(kitchen.data) == kitchen_data
    assert dict(kitchen.options) == kitchen_options
    assert molight_config(kitchen)[CONF_ENTITY_TYPE] == ENTITY_TYPE_LIGHT


# ---------------------------------------------------------------------------
# Options flow — entry title, error paths, and reference-resolution edges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_rename_updates_entry_title(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Renaming via the options flow syncs the config entry's title.

    HA ignores an options flow's title argument, so without the explicit
    sync the integrations page would keep showing the old name forever.
    """
    await setup_entries(hass, occupancy_entry)

    result = await hass.config_entries.options.async_init(occupancy_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NAME: "Renamed Occupancy", SECTION_ADVANCED: {}}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert occupancy_entry.title == "Renamed Occupancy"
    assert molight_config(occupancy_entry)[CONF_NAME] == "Renamed Occupancy"


@pytest.mark.asyncio
async def test_schedule_options_reject_incomplete_window(
    hass: HomeAssistant, schedule_entry: MockConfigEntry
) -> None:
    """The options flow rejects a half-filled window like the create flow."""
    await setup_entries(hass, schedule_entry)

    result = await hass.config_entries.options.async_init(schedule_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_NAME: "Test Schedule", "start": {"time": "20:00:00"}, "end": {}},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "window_incomplete"}


@pytest.mark.asyncio
async def test_light_options_require_lights(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """The options flow rejects clearing the lights list entirely."""
    await setup_entries(hass, light_entry)

    result = await hass.config_entries.options.async_init(light_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**EMPTY_LIGHT_SECTIONS, CONF_NAME: "Test Light", CONF_LIGHTS: []},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_LIGHTS: "lights_required"}


@pytest.mark.asyncio
async def test_occupancy_options_pass_without_registered_entities(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """No registered entities → no dependent lights → any timeout passes."""
    await setup_entries(hass, occupancy_entry)
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, occupancy_entry.entry_id):
        registry.async_remove(ent.entity_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(occupancy_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_TIMEOUT: 3600,
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_light_flow_skips_timeout_check_for_non_occupancy_refs(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """References that resolve to no occupancy timeout impose no constraint.

    Covers a reference to a MoLight entry of a non-occupancy type, one to a
    registered entity without a config entry, and one to another domain's
    entity — none of them can supply a timeout, so a tiny light_timeout is
    accepted.
    """
    await setup_entries(hass, illuminance_entry)
    registry = er.async_get(hass)
    loose = registry.async_get_or_create(
        "binary_sensor", "test", "uid_loose", suggested_object_id="loose_motion"
    )
    foreign_entry = MockConfigEntry(domain="other")
    foreign_entry.add_to_hass(hass)
    foreign = registry.async_get_or_create(
        "binary_sensor",
        "other",
        "uid_foreign",
        suggested_object_id="foreign_motion",
        config_entry=foreign_entry,
    )

    for occupancy_ref, maintain_ref in (
        ("binary_sensor.test_illuminance", loose.entity_id),
        (foreign.entity_id, None),
    ):
        result = await _start_create(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
        )
        sensors = {CONF_OCCUPANCY_ENTITY: occupancy_ref}
        if maintain_ref:
            sensors[CONF_MAINTAIN_OCCUPANCY_ENTITY] = maintain_ref
        user_input = {
            **EMPTY_LIGHT_CREATE_SECTIONS,
            CONF_NAME: f"Loose Light {occupancy_ref}",
            CONF_LIGHTS: ["light.some_real"],
            CONF_LIGHT_TIMEOUT: 1,
            SECTION_SENSORS: sensors,
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_discover_candidates_from_registry(hass: HomeAssistant) -> None:
    """Discovery honors registry device classes and registry-level exclusions.

    Registered entities are matched on their (possibly overridden) registry
    device_class even without a live state; disabled entities and MoLight's
    own registrations are hidden even when a matching state exists.
    """
    registry = er.async_get(hass)
    pir = registry.async_get_or_create(
        "binary_sensor",
        "test",
        "uid_pir",
        suggested_object_id="reg_pir",
        original_device_class="motion",
        original_name="Reg PIR",
    )
    override = registry.async_get_or_create(
        "binary_sensor",
        "test",
        "uid_override",
        suggested_object_id="reg_override",
        original_device_class="door",
    )
    registry.async_update_entity(override.entity_id, device_class="occupancy")
    disabled = registry.async_get_or_create(
        "binary_sensor",
        "test",
        "uid_disabled",
        suggested_object_id="reg_disabled",
        original_device_class="occupancy",
    )
    registry.async_update_entity(
        disabled.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    own = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "uid_own",
        suggested_object_id="molight_own",
        original_device_class="occupancy",
    )
    # A matching state must not re-offer what the registry already excludes.
    hass.states.async_set(own.entity_id, "off", {"device_class": "occupancy"})
    # Registered with a non-matching class — hidden.
    registry.async_get_or_create(
        "sensor", "test", "uid_lux", suggested_object_id="reg_lux"
    )

    result = await _reach_discovery_select(hass, "discover_occupancy")
    assert result["type"] == FlowResultType.FORM
    assert _offered_candidates(result) == {pir.entity_id, override.entity_id}


@pytest.mark.asyncio
async def test_assign_ignores_stale_light_pick(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """A submitted light no longer backed by an entry is skipped, not crashed."""
    light = _light_entry("Kitchen", "kitchen")
    await setup_entries(hass, occupancy_entry, light)

    result = await _reach_assign_kind(hass, "assign_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ASSIGN_SENSOR: "binary_sensor.test_occupancy",
            CONF_ASSIGN_ROLE: ASSIGN_ROLE_REGULAR,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ASSIGN_LIGHTS: ["light.ghost"]}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "assign_done"
    assert result["description_placeholders"] == {"assigned": "0", "removed": "0"}


@pytest.mark.asyncio
async def test_config_flow_schedule_requires_window(hass: HomeAssistant) -> None:
    """An empty schedule form is rejected — it would create a sensor that is
    permanently off, which nothing can ever follow or gate on."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Empty Schedule", "start": {}, "end": {}, SECTION_ADVANCED: {}},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "window_required"}

    # Completing the window creates the entry as usual.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Empty Schedule",
            "start": {"time": "21:00:00"},
            "end": {"time": "23:00:00"},
            SECTION_ADVANCED: {},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TIME_WINDOWS] == [
        {"start": {"time": "21:00:00"}, "end": {"time": "23:00:00"}}
    ]


@pytest.mark.asyncio
async def test_schedule_options_require_window(
    hass: HomeAssistant, schedule_entry: MockConfigEntry
) -> None:
    """The options flow rejects clearing the window entirely, like the create flow."""
    await setup_entries(hass, schedule_entry)

    result = await hass.config_entries.options.async_init(schedule_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NAME: "Test Schedule", "start": {}, "end": {}}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "window_required"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_type", "taken", "user_input"),
    [
        pytest.param(
            ENTITY_TYPE_COMBINED_OCCUPANCY,
            "binary_sensor.taken",
            {CONF_NAME: "X", CONF_TRIGGER_SENSORS: ["binary_sensor.occ_a"]},
            id="combined",
        ),
        pytest.param(
            ENTITY_TYPE_ILLUMINANCE,
            "binary_sensor.taken",
            {CONF_NAME: "X", CONF_ILLUMINANCE_SENSOR: "sensor.lux_a"},
            id="illuminance",
        ),
        pytest.param(
            ENTITY_TYPE_SCHEDULE,
            "binary_sensor.taken",
            {
                CONF_NAME: "X",
                "start": {"time": "21:00:00"},
                "end": {"time": "23:00:00"},
            },
            id="schedule",
        ),
        pytest.param(
            ENTITY_TYPE_LIGHT,
            "light.taken",
            {
                **EMPTY_LIGHT_SECTIONS,
                CONF_NAME: "X",
                CONF_LIGHTS: ["light.real_x"],
            },
            id="light",
        ),
    ],
)
async def test_explicit_entity_id_conflict_all_create_steps(
    hass: HomeAssistant, entity_type: str, taken: str, user_input: dict
) -> None:
    """Every create step re-shows its form on an explicit entity_id clash."""
    hass.states.async_set(taken, "off")

    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: entity_type}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**user_input, SECTION_ADVANCED: {CONF_ENTITY_ID: "taken"}},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "entity_id_conflict"}


@pytest.mark.asyncio
async def test_assign_noop_leaves_entry_untouched(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Re-assigning a sensor a light already carries must not rewrite the entry
    (a rewrite would needlessly reload it)."""
    light = _light_entry(
        "Hall", "hall", occupancy_entity="binary_sensor.test_occupancy"
    )
    await setup_entries(hass, occupancy_entry, light)

    result = await _reach_assign_kind(hass, "assign_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ASSIGN_SENSOR: "binary_sensor.test_occupancy",
            CONF_ASSIGN_ROLE: ASSIGN_ROLE_REGULAR,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ASSIGN_LIGHTS: ["light.hall"]}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "assign_done"
    assert result["description_placeholders"] == {"assigned": "0", "removed": "0"}
    await hass.async_block_till_done()

    # No options were written — the reference still comes from the entry data.
    assert not light.options
    assert (
        molight_config(light)[CONF_OCCUPANCY_ENTITY] == "binary_sensor.test_occupancy"
    )
