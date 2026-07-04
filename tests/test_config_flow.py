"""Tests for the MoLight config flow."""
from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.const import (
    CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_NAME_PREFIX,
    CONF_NAME_SUFFIX,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_SELECTED_ENTITIES,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
)
from custom_components.molight.helpers import molight_config


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
    """Init the flow and pick a discovery step from the menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


def _offered_candidates(result: dict) -> set[str]:
    """Entity ids the discovery form pre-selects (its default is all candidates)."""
    return set(result["data_schema"]({})[CONF_SELECTED_ENTITIES])


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
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hall Occupancy"
    assert result["data"][CONF_ENTITY_TYPE] == ENTITY_TYPE_OCCUPANCY
    # Unsubmitted fields fall back to their schema defaults.
    assert result["data"][CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT] == 60


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
            "start_time": "21:00:00",
            "start_sun": "sunset",
            "start_offset": -15,
            "start_combine": "latest",
            "end_time": "07:00:00",
            "end_sun": "sunrise",
            "end_offset": 10,
            "end_combine": "earliest",
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
        {CONF_NAME: "Night", "start_time": "21:00:00"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "window_incomplete"}

    # Sun-only edges (no fixed times) are a complete window.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Night", "start_sun": "sunset", "end_sun": "sunrise"},
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
            CONF_NAME: "Living Room",
            CONF_LIGHTS: ["light.living_room_1", "light.living_room_2"],
            CONF_LIGHT_TIMEOUT: 300,
            CONF_HOLD_ENTITIES: ["input_boolean.guest_mode"],
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room"
    assert result["data"][CONF_HOLD_ENTITIES] == ["input_boolean.guest_mode"]


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
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
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
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
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
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


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
            CONF_OCCUPANCY_ENTITY: "binary_sensor.combined_occupancy",
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
            CONF_OCCUPANCY_ENTITY: "binary_sensor.combined_occupancy",
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
            CONF_OCCUPANCY_ENTITY: "binary_sensor.combined_a",
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
            CONF_OCCUPANCY_ENTITY: "binary_sensor.combined_a",
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
            "start_time": "20:00:00",
            "start_sun": "sunset",
            "start_combine": "latest",
            "end_time": "06:00:00",
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
async def test_config_flow_virtual_light_requires_lights(hass: HomeAssistant) -> None:
    """Virtual light config flow rejects an empty lights list."""
    result = await _start_create(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Empty Light", CONF_LIGHTS: [], CONF_LIGHT_TIMEOUT: 300},
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
    hass.states.async_set(
        "binary_sensor.front_door", "off", {"device_class": "door"}
    )
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

    result = await _start_discovery(hass, "discover_occupancy")
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discover_occupancy"
    assert _offered_candidates(result) == {
        "binary_sensor.hall_motion",
        "binary_sensor.porch_pir",
        "binary_sensor.study_presence",
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SELECTED_ENTITIES: ["binary_sensor.hall_motion"]},
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
async def test_discover_applies_name_prefix_and_suffix(
    hass: HomeAssistant,
) -> None:
    """A prefix and/or suffix wrap each discovered entity's name verbatim."""
    hass.states.async_set(
        "binary_sensor.hall_motion",
        "off",
        {"device_class": "occupancy", "friendly_name": "Hall Motion"},
    )

    result = await _start_discovery(hass, "discover_occupancy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SELECTED_ENTITIES: ["binary_sensor.hall_motion"],
            CONF_NAME_PREFIX: "Auto ",
            CONF_NAME_SUFFIX: " (occ)",
        },
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
    hass.states.async_set(
        "sensor.office_temp", "21", {"device_class": "temperature"}
    )

    result = await _start_discovery(hass, "discover_illuminance")
    assert _offered_candidates(result) == {"sensor.office_lux"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SELECTED_ENTITIES: ["sensor.office_lux"]},
    )
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
    hass.states.async_set(
        "light.desk", "off", {"friendly_name": "Desk Lamp"}
    )
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

    result = await _start_discovery(hass, "discover_light")
    assert _offered_candidates(result) == {"light.desk"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SELECTED_ENTITIES: ["light.desk"]},
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

    result = await _start_discovery(hass, "discover_occupancy")
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
