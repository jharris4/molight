"""A source-backed Virtual Schedule whose source is computed from the schedule.

A schedule cannot be its own source, but a group can contain it, directly or
through a nested group or a combined schedule. Mirroring such a source feeds
the schedule its own state: inverted, it flips without end. The forms refuse
one, and a group changed afterwards makes the schedule unavailable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight import binary_sensor as bs
from custom_components.molight.config_flow import SECTION_ADVANCED
from custom_components.molight.const import (
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_SCHEDULE_DEFINITION,
    CONF_SCHEDULE_INPUTS,
    CONF_SCHEDULE_INVERT,
    CONF_SCHEDULE_OPERATOR,
    CONF_SCHEDULE_SOURCE,
    DOMAIN,
    ENTITY_TYPE_COMBINED_SCHEDULE,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
    SCHEDULE_DEFINITION_BINARY_SENSOR,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_OPERATOR_ANY,
)
from custom_components.molight.helpers import molight_config
from tests.conftest import make_light_entry, settle, setup_entries
from tests.real_entities import RealBinary, RealLight, add_real

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

SCHEDULE = "binary_sensor.mode"
REAL = "binary_sensor.real"
GROUP = "binary_sensor.source_group"
BELOW = "binary_sensor.below"
COMBINED = "binary_sensor.both"
OCCUPANCY = "binary_sensor.seen"
TOPOLOGIES = ["direct", "nested", "combined", "occupancy"]
# The callbacks one settled source change may take; a loop passes it at once.
CAP = 10


def _schedule(source: str, *, invert: bool, name: str = "Mode") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: name,
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: source,
            CONF_SCHEDULE_INVERT: invert,
        },
    )


def _combined(inputs: list[str]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_SCHEDULE,
            CONF_NAME: "Both",
            CONF_SCHEDULE_INPUTS: inputs,
            CONF_SCHEDULE_OPERATOR: SCHEDULE_OPERATOR_ANY,
            CONF_SCHEDULE_INVERT: False,
        },
    )


def _occupancy(source: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Seen",
            CONF_OCCUPANCY_SENSOR: source,
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 0,
        },
    )


def _group(name: str, members: list[str], *, every: bool = False) -> MockConfigEntry:
    return MockConfigEntry(
        domain="group",
        title=name,
        options={
            "group_type": "binary_sensor",
            "name": name,
            "entities": members,
            "hide_members": False,
            "all": every,
        },
    )


async def _set_members(
    hass: HomeAssistant, group: MockConfigEntry, members: list[str]
) -> None:
    """Change a group helper's members, as its options form does."""
    hass.config_entries.async_update_entry(
        group, options={**group.options, "entities": members}
    )
    assert await hass.config_entries.async_reload(group.entry_id)
    await settle(hass)


async def _groups(
    hass: HomeAssistant, *, every: bool = False
) -> tuple[dict[str, MockConfigEntry], RealBinary]:
    """A real sensor in a group inside the source group, which also has it.

    The real sensor keeps both groups available whatever else they contain.
    With every, the groups are on only when all their members are.
    """
    real = RealBinary("real")
    await add_real(hass, real)
    groups = {
        "below": _group("Below", [REAL], every=every),
        "source": _group("Source Group", [REAL, BELOW], every=every),
    }
    await setup_entries(hass, *groups.values())
    return groups, real


async def _close_cycle(
    hass: HomeAssistant, groups: dict[str, MockConfigEntry], topology: str
) -> None:
    """Make the source group lead back to the schedule."""
    if topology == "direct":
        await _set_members(hass, groups["source"], [REAL, BELOW, SCHEDULE])
    elif topology == "nested":
        await _set_members(hass, groups["below"], [REAL, SCHEDULE])
    elif topology == "combined":
        await setup_entries(hass, _combined([SCHEDULE]))
        await _set_members(hass, groups["below"], [REAL, COMBINED])
    else:
        await setup_entries(hass, _occupancy(SCHEDULE))
        await _set_members(hass, groups["below"], [REAL, OCCUPANCY])


def _count_source_changes(monkeypatch) -> list[str]:
    """Record the source states the schedule is told of, stopping a loop."""
    seen: list[str] = []
    original = bs.VirtualScheduleSensor._handle_source_change

    @callback
    def _bounded(self, event):
        seen.append(event.data["new_state"].state)
        if len(seen) <= CAP:
            original(self, event)

    monkeypatch.setattr(bs.VirtualScheduleSensor, "_handle_source_change", _bounded)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", TOPOLOGIES)
@pytest.mark.parametrize("invert", [True, False])
@pytest.mark.allow_warning_log
async def test_a_source_group_changed_to_include_the_schedule_stops_it(
    hass: HomeAssistant,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
    topology: str,
    invert: bool,
) -> None:
    """The schedule goes unavailable instead of following its own state."""
    lamp = RealLight("lamp")
    await add_real(hass, lamp)
    groups, real = await _groups(hass)
    await setup_entries(hass, _schedule(GROUP, invert=invert))
    await setup_entries(
        hass,
        make_light_entry(
            lights=[lamp.entity_id],
            schedule=SCHEDULE,
            schedule_mode=SCHEDULE_MODE_FOLLOW,
        ),
    )
    await settle(hass)
    assert hass.states.get(SCHEDULE).state == ("on" if invert else "off")
    lamp_states: list[str] = []

    @callback
    def _record(event) -> None:
        new = event.data.get("new_state")
        if event.data["entity_id"] == lamp.entity_id and new is not None:
            lamp_states.append(new.state)

    hass.bus.async_listen("state_changed", _record)
    seen = _count_source_changes(monkeypatch)

    await _close_cycle(hass, groups, topology)
    # Not inverted, the schedule would latch on at the source's next report.
    real.set(True)
    await settle(hass)
    assert len(seen) < CAP, seen
    assert hass.states.get(SCHEDULE).state == "unavailable"
    real.set(False)
    await settle(hass)
    assert len(seen) < CAP, seen
    assert hass.states.get(SCHEDULE).state == "unavailable"
    assert f"Schedule {SCHEDULE} is unavailable" in caplog.text
    # No flapping reaches the lamp once the schedule stops.
    assert len(lamp_states) <= 2, lamp_states

    # Restarting the schedule's entry does not start it again.
    seen.clear()
    schedule = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_reload(schedule.entry_id)
    await settle(hass)
    assert len(seen) < CAP, seen
    assert hass.states.get(SCHEDULE).state == "unavailable"

    # Taking the schedule out again brings it back.
    await _set_members(hass, groups["below"], [REAL])
    await _set_members(hass, groups["source"], [REAL, BELOW])
    assert hass.states.get(SCHEDULE).state == ("on" if invert else "off")


def _count_input_changes(monkeypatch) -> list[str]:
    """Record the reports of what the source is computed from, stopping a loop."""
    seen: list[str] = []
    original = bs.VirtualScheduleSensor._handle_input_change

    @callback
    def _bounded(self, event):
        seen.append(event.data["entity_id"])
        if len(seen) <= 5 * CAP:
            original(self, event)

    monkeypatch.setattr(bs.VirtualScheduleSensor, "_handle_input_change", _bounded)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", ["nested", "combined", "occupancy"])
@pytest.mark.parametrize("invert", [True, False])
@pytest.mark.parametrize("real_on", [True, False])
@pytest.mark.allow_warning_log
async def test_a_group_under_the_source_changed_to_include_the_schedule_stops_it(
    hass: HomeAssistant,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
    topology: str,
    invert: bool,
    real_on: bool,
) -> None:
    """The schedule stops at once, though its source may report nothing new.

    A schedule that is on keeps the group under its source on, so the source
    never changes and the schedule would hold itself on for good.
    """
    groups, real = await _groups(hass)
    await setup_entries(hass, _schedule(GROUP, invert=invert))
    real.set(real_on)
    await settle(hass)
    assert hass.states.get(SCHEDULE).state == ("on" if real_on != invert else "off")
    seen = _count_source_changes(monkeypatch)
    inputs = _count_input_changes(monkeypatch)

    await _close_cycle(hass, groups, topology)
    assert hass.states.get(SCHEDULE).state == "unavailable"
    assert f"Schedule {SCHEDULE} is unavailable" in caplog.text
    real.set(not real_on)
    await settle(hass)
    assert hass.states.get(SCHEDULE).state == "unavailable"
    real.set(False)
    await settle(hass)
    assert hass.states.get(SCHEDULE).state == "unavailable"

    # Taking the schedule out of that group alone brings it back.
    await _set_members(hass, groups["below"], [REAL])
    assert hass.states.get(SCHEDULE).state == ("on" if invert else "off")
    real.set(True)
    await settle(hass)
    assert hass.states.get(SCHEDULE).state == ("off" if invert else "on")
    assert len(seen) < CAP, seen
    assert len(inputs) < 5 * CAP, inputs


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_an_all_group_under_the_source_changed_to_include_the_schedule_stops_it(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Groups that need every member on hide the change from the source too."""
    groups, real = await _groups(hass, every=True)
    await setup_entries(hass, _schedule(GROUP, invert=False))
    real.set(True)
    await settle(hass)
    assert hass.states.get(SCHEDULE).state == "on"
    seen = _count_source_changes(monkeypatch)
    inputs = _count_input_changes(monkeypatch)

    await _close_cycle(hass, groups, "nested")
    assert hass.states.get(SCHEDULE).state == "unavailable"
    await _set_members(hass, groups["below"], [REAL])
    assert hass.states.get(SCHEDULE).state == "on"
    assert len(seen) < CAP, seen
    assert len(inputs) < 5 * CAP, inputs


@pytest.mark.asyncio
@pytest.mark.parametrize("invert", [True, False])
async def test_a_source_group_without_the_schedule_is_mirrored(
    hass: HomeAssistant, monkeypatch, caplog: pytest.LogCaptureFixture, invert: bool
) -> None:
    """A group of other sensors, nested or changed, stays a valid source."""
    other = RealBinary("other")
    await add_real(hass, other)
    groups, _real = await _groups(hass)
    caplog.set_level(logging.WARNING)
    await setup_entries(
        hass,
        _schedule("binary_sensor.other", invert=False, name="Elsewhere"),
        _schedule(GROUP, invert=invert),
    )
    seen = _count_source_changes(monkeypatch)
    # Another schedule in the group is a chain, not a cycle.
    await _set_members(hass, groups["below"], [REAL, "binary_sensor.elsewhere"])
    assert hass.states.get(SCHEDULE).state == ("on" if invert else "off")

    other.set(True)
    await settle(hass)
    assert hass.states.get(SCHEDULE).state == ("off" if invert else "on")
    other.set(False)
    await settle(hass)
    assert hass.states.get(SCHEDULE).state == ("on" if invert else "off")
    assert len(seen) < CAP, seen
    assert "is unavailable" not in caplog.text


async def _edit_source(hass: HomeAssistant, entry: MockConfigEntry, source: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR},
    )
    assert result["step_id"] == "schedule_source"
    return await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_NAME: "Mode", CONF_SCHEDULE_SOURCE: source, CONF_SCHEDULE_INVERT: True},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", TOPOLOGIES)
async def test_editing_a_schedule_refuses_a_source_that_includes_it(
    hass: HomeAssistant, monkeypatch, topology: str
) -> None:
    """The options form names the cycle and saves nothing."""
    groups, _real = await _groups(hass)
    entry = _schedule(REAL, invert=True)
    await setup_entries(hass, entry)
    seen = _count_source_changes(monkeypatch)
    await _close_cycle(hass, groups, topology)

    result = await _edit_source(hass, entry, GROUP)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_SCHEDULE_SOURCE: "schedule_source_cycle"}
    assert molight_config(entry)[CONF_SCHEDULE_SOURCE] == REAL
    await settle(hass)
    assert len(seen) < CAP, seen


@pytest.mark.asyncio
async def test_editing_a_schedule_accepts_a_group_of_other_sensors(
    hass: HomeAssistant,
) -> None:
    """An independent group, with another schedule in it, is a valid source."""
    other = RealBinary("other")
    await add_real(hass, other)
    groups, _real = await _groups(hass)
    entry = _schedule(REAL, invert=True)
    await setup_entries(
        hass, entry, _schedule("binary_sensor.other", invert=False, name="Elsewhere")
    )
    await _set_members(hass, groups["below"], [REAL, "binary_sensor.elsewhere"])

    result = await _edit_source(hass, entry, GROUP)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await settle(hass)
    assert hass.states.get(SCHEDULE).state == "on"


async def _start_schedule(hass: HomeAssistant, entity_type: str) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "create"}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTITY_TYPE: entity_type}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("named", ["name", "entity_id", "taken_name"])
@pytest.mark.parametrize("nested", [False, True])
async def test_creating_a_schedule_refuses_a_group_that_names_its_id(
    hass: HomeAssistant, named: str, nested: bool
) -> None:
    """A group can name an entity that does not exist yet, including the
    suffixed ID a taken name leads to."""
    groups, _real = await _groups(hass)
    own_id = SCHEDULE
    if named == "taken_name":
        hass.states.async_set(SCHEDULE, "off")
        own_id = f"{SCHEDULE}_2"
    await _set_members(hass, groups["below" if nested else "source"], [REAL, own_id])
    result = await _start_schedule(hass, ENTITY_TYPE_SCHEDULE)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR},
    )
    values = {
        CONF_NAME: "Something Else" if named == "entity_id" else "Mode",
        CONF_SCHEDULE_SOURCE: GROUP,
        CONF_SCHEDULE_INVERT: True,
        SECTION_ADVANCED: {CONF_ENTITY_ID: "mode"} if named == "entity_id" else {},
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_SCHEDULE_SOURCE: "schedule_source_cycle"}

    # Under another ID the same group is a valid source.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**values, CONF_NAME: "Unrelated", SECTION_ADVANCED: {}}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_a_combined_schedule_refuses_a_schedule_whose_source_includes_it(
    hass: HomeAssistant, nested: bool
) -> None:
    """Editing the combined schedule closes the same cycle from its side."""
    groups, _real = await _groups(hass)
    combined = _combined(["binary_sensor.elsewhere"])
    await setup_entries(
        hass,
        _schedule(REAL, invert=False, name="Elsewhere"),
        _schedule(GROUP, invert=True),
        combined,
    )
    result = await hass.config_entries.options.async_init(combined.entry_id)
    assert result["step_id"] == "combined_schedule"
    # The group changes after the form opened, so its picker still offers it.
    await _set_members(hass, groups["below" if nested else "source"], [REAL, COMBINED])
    edit = {
        CONF_NAME: "Both",
        CONF_SCHEDULE_OPERATOR: SCHEDULE_OPERATOR_ANY,
        CONF_SCHEDULE_INVERT: False,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**edit, CONF_SCHEDULE_INPUTS: [SCHEDULE]}
    )
    assert result["errors"] == {"base": "combined_schedule_cycle"}

    # The picker no longer offers it, and other schedules still save.
    schema = result["data_schema"].schema
    picker = next(v for k, v in schema.items() if k == CONF_SCHEDULE_INPUTS)
    assert SCHEDULE in picker.config["exclude_entities"]
    assert "binary_sensor.elsewhere" not in picker.config["exclude_entities"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**edit, CONF_SCHEDULE_INPUTS: ["binary_sensor.elsewhere"]},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
@pytest.mark.parametrize("taken", [False, True], ids=["free", "taken_name"])
async def test_creating_a_combined_schedule_refuses_a_group_that_names_its_id(
    hass: HomeAssistant, taken: bool
) -> None:
    """A new combined schedule can take an ID its input's source group names,
    including the suffixed ID a taken name leads to."""
    groups, _real = await _groups(hass)
    await setup_entries(hass, _schedule(GROUP, invert=True))
    if taken:
        hass.states.async_set(COMBINED, "off")
    await _set_members(
        hass, groups["below"], [REAL, f"{COMBINED}_2" if taken else COMBINED]
    )

    result = await _start_schedule(hass, ENTITY_TYPE_COMBINED_SCHEDULE)
    values = {
        CONF_NAME: "Both",
        CONF_SCHEDULE_INPUTS: [SCHEDULE],
        CONF_SCHEDULE_OPERATOR: SCHEDULE_OPERATOR_ANY,
        CONF_SCHEDULE_INVERT: False,
        SECTION_ADVANCED: {},
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "combined_schedule_cycle"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**values, CONF_NAME: "Unrelated"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
