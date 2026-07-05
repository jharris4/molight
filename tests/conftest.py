"""Shared pytest fixtures for MoLight tests."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.const import (
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_TIMEOUT,
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_MODE,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_TIMEOUT,
    DOMAIN,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
)


async def settle(hass: HomeAssistant) -> None:
    """Flush chained state-change dispatches.

    async_track_state_change_event defers each dispatch by one event-loop
    iteration (loop.call_soon), so a motion → virtual occupancy → virtual
    light chain needs several iterations before downstream entities react.
    """
    for _ in range(4):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


def make_light_entry(
    *,
    name: str = "Matrix Light",
    lights: list[str] | None = None,
    timeout: int = 60,
    occupancy: str | None = None,
    maintain: str | None = None,
    illuminance: str | None = None,
    illuminance_mode: str | None = None,
    schedule: str | None = None,
    schedule_mode: str | None = None,
    hold_entities: list[str] | None = None,
    auto_on_brightness: int | None = None,
    effect_timeout: int | None = None,
    effect_brightness: int | None = None,
    warn_timeout: int | None = None,
    warn_brightness: int | None = None,
) -> MockConfigEntry:
    """Build a virtual-light entry wired to arbitrary entity ids.

    The light only reads states/attributes of the entities it watches, so
    tests can drive it with plain states set via hass.states.async_set
    instead of full virtual-sensor entries.
    """
    data = {
        CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
        CONF_NAME: name,
        CONF_LIGHTS: lights if lights is not None else ["light.real_1"],
        CONF_LIGHT_TIMEOUT: timeout,
    }
    if occupancy:
        data[CONF_OCCUPANCY_ENTITY] = occupancy
    if maintain:
        data[CONF_MAINTAIN_OCCUPANCY_ENTITY] = maintain
    if illuminance:
        data[CONF_ILLUMINANCE_ENTITY] = illuminance
    if illuminance_mode:
        data[CONF_ILLUMINANCE_MODE] = illuminance_mode
    if schedule:
        data[CONF_SCHEDULE_ENTITY] = schedule
    if schedule_mode:
        data[CONF_SCHEDULE_MODE] = schedule_mode
    if hold_entities:
        data[CONF_HOLD_ENTITIES] = hold_entities
    if auto_on_brightness is not None:
        data[CONF_AUTO_ON_BRIGHTNESS] = auto_on_brightness
    if effect_timeout is not None:
        data[CONF_EFFECT_TIMEOUT] = effect_timeout
    if effect_brightness is not None:
        data[CONF_EFFECT_BRIGHTNESS] = effect_brightness
    if warn_timeout is not None:
        data[CONF_WARN_TIMEOUT] = warn_timeout
    if warn_brightness is not None:
        data[CONF_WARN_BRIGHTNESS] = warn_brightness
    return MockConfigEntry(domain=DOMAIN, data=data)


async def setup_entries(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    for entry in entries:
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom integrations in tests."""
    yield


@pytest.fixture
def occupancy_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
        },
    )


@pytest.fixture
def illuminance_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
            CONF_NAME: "Test Illuminance",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux_1",
            CONF_ILLUMINANCE_THRESHOLD: 10.0,
        },
    )


@pytest.fixture
def schedule_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Test Schedule",
            "time_windows": [{"start": "07:00", "end": "22:00"}],
        },
    )


@pytest.fixture
def light_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Test Light",
            CONF_LIGHTS: ["light.living_room"],
            CONF_LIGHT_TIMEOUT: 60,
        },
    )
