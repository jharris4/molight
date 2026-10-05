"""A Virtual Occupancy Sensor whose source is computed from the sensor.

An occupancy sensor cannot be its own source, but a group can contain it,
directly or through a nested group or a combined occupancy sensor. Following
such a source holds the sensor on by its own state, and the lights that use
it with it. The forms refuse one, and a group changed afterwards is a source
the sensor stops following.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.molight.config_flow import SECTION_ADVANCED
from custom_components.molight.const import (
    CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_OCCUPANCY,
)
from custom_components.molight.helpers import molight_config
from tests.conftest import make_light_entry, settle, setup_entries
from tests.real_entities import RealBinary, RealLight, add_real
from tests.test_schedule_source_cycle import (
    BELOW,
    GROUP,
    REAL,
    _groups,
    _set_members,
    _start_schedule,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

SEEN = "binary_sensor.seen"
ROOM = "binary_sensor.room"
TOPOLOGIES = ["direct", "nested", "combined"]
WARNING = f"Occupancy sensor {SEEN} does not follow its source"


def _occupancy(
    source: str, *, name: str = "Seen", unavailable_timeout: int | None = None
) -> MockConfigEntry:
    data = {
        CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
        CONF_NAME: name,
        CONF_OCCUPANCY_SENSOR: source,
        CONF_OCCUPANCY_TIMEOUT: 30,
        CONF_FALSE_DETECTION_GRACE: 0,
    }
    if unavailable_timeout is not None:
        data[CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT] = unavailable_timeout
    return MockConfigEntry(domain=DOMAIN, data=data)


def _combined(triggers: list[str]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Room",
            CONF_TRIGGER_SENSORS: triggers,
            CONF_MAINTAIN_SENSORS: [],
        },
    )


async def _close_cycle(
    hass: HomeAssistant, groups: dict[str, MockConfigEntry], topology: str
) -> None:
    """Make the source group lead back to the occupancy sensor."""
    if topology == "direct":
        await _set_members(hass, groups["source"], [REAL, BELOW, SEEN])
    elif topology == "nested":
        await _set_members(hass, groups["below"], [REAL, SEEN])
    else:
        if hass.states.get(ROOM) is None:
            await setup_entries(hass, _combined([SEEN]))
        await _set_members(hass, groups["below"], [REAL, ROOM])


async def _wait(hass: HomeAssistant, freezer, minutes: int) -> None:
    for _ in range(minutes):
        freezer.tick(timedelta(seconds=60))
        async_fire_time_changed(hass)
        await settle(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", TOPOLOGIES)
@pytest.mark.parametrize("occupied", [False, True])
@pytest.mark.parametrize("unavailable_timeout", [None, 0])
@pytest.mark.allow_warning_log
async def test_a_source_group_changed_to_include_the_sensor_is_not_followed(
    hass: HomeAssistant,
    freezer,
    caplog: pytest.LogCaptureFixture,
    topology: str,
    occupied: bool,
    unavailable_timeout: int | None,
) -> None:
    """The sensor clears instead of holding itself, and the room, on for good.

    The group may come to include it while the room is occupied, which the
    source itself, still on, does not report.
    """
    lamp = RealLight("lamp")
    await add_real(hass, lamp)
    groups, real = await _groups(hass)
    seen = _occupancy(GROUP, unavailable_timeout=unavailable_timeout)
    await setup_entries(
        hass, seen, make_light_entry(lights=[lamp.entity_id], occupancy=SEEN)
    )
    await settle(hass)

    if occupied:
        real.set(True)
        await settle(hass)
        assert hass.states.get(SEEN).state == "on"
        assert lamp.is_on
    await _close_cycle(hass, groups, topology)
    assert WARNING in caplog.text
    if unavailable_timeout == 0:
        # No clear-after-unavailable to wait for.
        assert hass.states.get(SEEN).state == "off"
    real.set(True)
    await settle(hass)
    real.set(False)
    await settle(hass)
    await _wait(hass, freezer, 10)
    assert hass.states.get(SEEN).state == "off"
    assert hass.states.get(GROUP).state == "off"
    assert not lamp.is_on

    # Reloading the sensor's entry does not start it following again.
    real.set(True)
    await settle(hass)
    assert await hass.config_entries.async_reload(seen.entry_id)
    await settle(hass)
    assert hass.states.get(SEEN).state == "off"

    # Taking the sensor out again does, with the room still occupied.
    await _set_members(hass, groups["below"], [REAL])
    await _set_members(hass, groups["source"], [REAL, BELOW])
    assert hass.states.get(SEEN).state == "on"
    real.set(False)
    await settle(hass)
    assert hass.states.get(SEEN).state == "off"


@pytest.mark.asyncio
async def test_a_source_group_of_other_sensors_is_followed(
    hass: HomeAssistant, freezer, caplog: pytest.LogCaptureFixture
) -> None:
    """An independent group, with another occupancy sensor in it, is a source."""
    other = RealBinary("other")
    await add_real(hass, other)
    groups, real = await _groups(hass)
    await setup_entries(
        hass,
        _occupancy("binary_sensor.other", name="Elsewhere"),
        _occupancy(GROUP),
    )
    await _set_members(hass, groups["below"], [REAL, "binary_sensor.elsewhere"])

    for sensor in (real, other):
        sensor.set(True)
        await settle(hass)
        assert hass.states.get(SEEN).state == "on"
        sensor.set(False)
        await settle(hass)
        assert hass.states.get(SEEN).state == "off"
    await _wait(hass, freezer, 2)
    assert hass.states.get(SEEN).state == "off"
    assert "does not follow" not in caplog.text


async def _edit_source(hass: HomeAssistant, entry: MockConfigEntry, source: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "occupancy"
    return await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Seen",
            CONF_OCCUPANCY_SENSOR: source,
            CONF_OCCUPANCY_TIMEOUT: 30,
            SECTION_ADVANCED: {},
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", TOPOLOGIES)
async def test_editing_an_occupancy_sensor_refuses_a_source_that_includes_it(
    hass: HomeAssistant, topology: str
) -> None:
    """The options form names the cycle and saves nothing."""
    groups, _real = await _groups(hass)
    entry = _occupancy(REAL)
    await setup_entries(hass, entry)
    await _close_cycle(hass, groups, topology)

    result = await _edit_source(hass, entry, GROUP)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_OCCUPANCY_SENSOR: "occupancy_source_cycle"}
    assert molight_config(entry)[CONF_OCCUPANCY_SENSOR] == REAL


@pytest.mark.asyncio
async def test_editing_an_occupancy_sensor_accepts_a_group_of_other_sensors(
    hass: HomeAssistant,
) -> None:
    """A group that holds another occupancy sensor is a valid source."""
    other = RealBinary("other")
    await add_real(hass, other)
    groups, real = await _groups(hass)
    entry = _occupancy(REAL)
    await setup_entries(
        hass, entry, _occupancy("binary_sensor.other", name="Elsewhere")
    )
    await _set_members(hass, groups["below"], [REAL, "binary_sensor.elsewhere"])

    result = await _edit_source(hass, entry, GROUP)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await settle(hass)
    real.set(True)
    await settle(hass)
    assert hass.states.get(SEEN).state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize("named", ["name", "entity_id", "taken_name"])
@pytest.mark.parametrize("nested", [False, True])
async def test_creating_an_occupancy_sensor_refuses_a_group_that_names_its_id(
    hass: HomeAssistant, named: str, nested: bool
) -> None:
    """A group can name an entity that does not exist yet, including the
    suffixed ID a taken name leads to."""
    groups, _real = await _groups(hass)
    own_id = SEEN
    if named == "taken_name":
        hass.states.async_set(SEEN, "off")
        own_id = f"{SEEN}_2"
    await _set_members(hass, groups["below" if nested else "source"], [REAL, own_id])
    result = await _start_schedule(hass, ENTITY_TYPE_OCCUPANCY)
    values = {
        CONF_NAME: "Something Else" if named == "entity_id" else "Seen",
        CONF_OCCUPANCY_SENSOR: GROUP,
        CONF_OCCUPANCY_TIMEOUT: 30,
        SECTION_ADVANCED: {CONF_ENTITY_ID: "seen"} if named == "entity_id" else {},
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_OCCUPANCY_SENSOR: "occupancy_source_cycle"}

    # Under another ID the same group is a valid source.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**values, CONF_NAME: "Unrelated", SECTION_ADVANCED: {}}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_a_combined_sensor_refuses_a_sensor_whose_source_includes_it(
    hass: HomeAssistant, nested: bool
) -> None:
    """Editing the combined sensor closes the same cycle from its side."""
    other = RealBinary("other")
    await add_real(hass, other)
    groups, _real = await _groups(hass)
    combined = _combined(["binary_sensor.elsewhere"])
    await setup_entries(
        hass,
        _occupancy("binary_sensor.other", name="Elsewhere"),
        _occupancy(GROUP),
        combined,
    )
    result = await hass.config_entries.options.async_init(combined.entry_id)
    assert result["step_id"] == "combined_occupancy"
    # The group changes after the form opened, so its picker still offers it.
    await _set_members(hass, groups["below" if nested else "source"], [REAL, ROOM])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NAME: "Room", CONF_TRIGGER_SENSORS: [SEEN]}
    )
    assert result["errors"] == {"base": "combined_occupancy_cycle"}

    # The picker no longer offers it, and other sensors still save.
    schema = result["data_schema"].schema
    picker = next(v for k, v in schema.items() if k == CONF_TRIGGER_SENSORS)
    assert SEEN in picker.config["exclude_entities"]
    assert "binary_sensor.elsewhere" not in picker.config["exclude_entities"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_NAME: "Room", CONF_TRIGGER_SENSORS: ["binary_sensor.elsewhere"]},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
@pytest.mark.parametrize("taken", [False, True], ids=["free", "taken_name"])
async def test_creating_a_combined_sensor_refuses_a_group_that_names_its_id(
    hass: HomeAssistant, taken: bool
) -> None:
    """A new combined sensor can take an ID its constituent's source names,
    including the suffixed ID a taken name leads to."""
    groups, _real = await _groups(hass)
    await setup_entries(hass, _occupancy(GROUP))
    if taken:
        hass.states.async_set(ROOM, "off")
    await _set_members(hass, groups["below"], [REAL, f"{ROOM}_2" if taken else ROOM])

    result = await _start_schedule(hass, ENTITY_TYPE_COMBINED_OCCUPANCY)
    values = {
        CONF_NAME: "Room",
        CONF_TRIGGER_SENSORS: [SEEN],
        SECTION_ADVANCED: {},
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "combined_occupancy_cycle"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**values, CONF_NAME: "Unrelated"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
