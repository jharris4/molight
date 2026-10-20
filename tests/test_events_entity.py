"""Tests for the "<name> Events" entity itself.

Every Virtual Light, Virtual Scheduled Light and occupancy sensor entry gets
one; it follows a typed Entity ID the way the Auto-off switch does, restores
its last event across a restart, and an event announced while it is disabled
is simply dropped.
"""

from __future__ import annotations

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.const import (
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
    EVENT_FALSE_DETECTION,
    LIGHT_EVENT_TYPES,
    OCCUPANCY_EVENT_TYPES,
    REASON_MANUAL,
)
from tests.conftest import (
    make_light_entry,
    make_scheduled_light_entry,
    restart_entries,
    settle,
    setup_entries,
)

REAL = "light.real_1"


def _entry(entity_type: str, **data) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, data={CONF_ENTITY_TYPE: entity_type, CONF_NAME: "Hall", **data}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("make", "event_types"),
    [
        (lambda: make_light_entry(name="Hall"), LIGHT_EVENT_TYPES),
        (lambda: make_scheduled_light_entry(name="Hall"), LIGHT_EVENT_TYPES),
        (
            lambda: _entry(
                ENTITY_TYPE_OCCUPANCY,
                **{
                    CONF_OCCUPANCY_SENSOR: "binary_sensor.pir",
                    CONF_OCCUPANCY_TIMEOUT: 30,
                },
            ),
            OCCUPANCY_EVENT_TYPES,
        ),
        (
            lambda: _entry(
                ENTITY_TYPE_COMBINED_OCCUPANCY,
                **{CONF_TRIGGER_SENSORS: ["binary_sensor.pir"]},
            ),
            OCCUPANCY_EVENT_TYPES,
        ),
    ],
    ids=["light", "scheduled_light", "occupancy", "combined_occupancy"],
)
async def test_events_entity_is_created_with_its_owner_types(
    hass, make, event_types: list[str]
) -> None:
    """Each light and occupancy entry gets "<name> Events" declaring its types."""
    hass.states.async_set("binary_sensor.settings_schedule", "off")
    await setup_entries(hass, make())

    state = hass.states.get("event.hall_events")
    assert state is not None
    assert state.state == "unknown"
    assert state.attributes["friendly_name"] == "Hall Events"
    assert state.attributes["event_types"] == event_types
    assert state.attributes["event_type"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make",
    [
        lambda: _entry(ENTITY_TYPE_ILLUMINANCE, illuminance_sensor="sensor.lux"),
        lambda: _entry(
            ENTITY_TYPE_SCHEDULE,
            time_windows=[{"start": {"time": "07:00"}, "end": {"time": "22:00"}}],
        ),
    ],
    ids=["illuminance", "schedule"],
)
async def test_other_entries_get_no_events_entity(hass, make) -> None:
    """A sensor that announces nothing gets no entity to announce it on."""
    entry = make()
    await setup_entries(hass, entry)

    assert hass.states.get("event.hall_events") is None
    assert not [
        e
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain == "event"
    ]


@pytest.mark.asyncio
async def test_events_entity_follows_a_typed_entity_id(hass) -> None:
    """light.kitchen gives event.kitchen_events, like switch.kitchen_auto_off."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Hall",
            CONF_LIGHTS: [REAL],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_ENTITY_ID: "kitchen",
        },
    )
    await setup_entries(hass, entry)

    assert hass.states.get("light.kitchen") is not None
    assert hass.states.get("switch.kitchen_auto_off") is not None
    assert hass.states.get("event.kitchen_events") is not None
    assert hass.states.get("event.hall_events") is None


@pytest.mark.asyncio
async def test_last_event_survives_a_restart(hass, freezer) -> None:
    """The entity restores its last event, so a restart does not blank it."""
    entry = make_light_entry(name="Hall", timeout=10)
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.hall"}, blocking=True
    )
    await settle(hass)
    before = hass.states.get("event.hall_events")
    assert before.attributes["event_type"] == "turned_on"
    assert before.attributes["reason"] == REASON_MANUAL

    await restart_entries(hass, entry)

    after = hass.states.get("event.hall_events")
    assert after.state == before.state
    assert after.attributes["event_type"] == "turned_on"
    assert after.attributes["reason"] == REASON_MANUAL
    assert after.attributes["entity_id"] == "light.hall"


@pytest.mark.asyncio
async def test_a_disabled_events_entity_drops_events_without_error(
    hass, freezer
) -> None:
    """Nothing listens while the entity is disabled; the light runs as usual."""
    entry = make_light_entry(name="Hall", timeout=10)
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, entry)
    er.async_get(hass).async_update_entity(
        "event.hall_events", disabled_by=er.RegistryEntryDisabler.USER
    )
    await settle(hass)
    assert hass.states.get("event.hall_events") is None

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.hall"}, blocking=True
    )
    await settle(hass)
    assert hass.states.get("light.hall").state == "on"
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.hall"}, blocking=True
    )
    await settle(hass)
    assert hass.states.get("light.hall").state == "off"
    assert hass.states.get("event.hall_events") is None


@pytest.mark.asyncio
async def test_renamed_events_entity_keeps_its_last_event(hass) -> None:
    """A new entity ID carries the last event over, like the switch's state."""
    entry = make_light_entry(name="Hall", timeout=10)
    hass.states.async_set(REAL, "off")
    await setup_entries(hass, entry)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.hall"}, blocking=True
    )
    await settle(hass)

    er.async_get(hass).async_update_entity(
        "event.hall_events", new_entity_id="event.hall_log"
    )
    await settle(hass)
    await restart_entries(hass, entry)

    assert hass.states.get("event.hall_events") is None
    assert hass.states.get("event.hall_log").attributes["event_type"] == "turned_on"


@pytest.mark.asyncio
async def test_occupancy_sensor_announces_on_its_own_entity(hass, freezer) -> None:
    """The sensor's blip lands on the sensor's entity, not the light's."""
    sensor = _entry(
        ENTITY_TYPE_OCCUPANCY,
        **{
            CONF_OCCUPANCY_SENSOR: "binary_sensor.pir",
            CONF_OCCUPANCY_TIMEOUT: 30,
            "false_detection_grace": 3,
        },
    )
    light = make_light_entry(name="Hall Light", occupancy="binary_sensor.hall")
    hass.states.async_set(REAL, "off")
    hass.states.async_set("binary_sensor.pir", "off")
    await setup_entries(hass, sensor, light)
    await settle(hass)

    hass.states.async_set("binary_sensor.pir", "on")
    await settle(hass)
    freezer.tick(31)
    hass.states.async_set("binary_sensor.pir", "off")
    await settle(hass)

    assert hass.states.get("event.hall_events").attributes["event_type"] == (
        EVENT_FALSE_DETECTION
    )
    assert hass.states.get("event.hall_light_events").attributes["event_type"] != (
        EVENT_FALSE_DETECTION
    )
