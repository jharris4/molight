"""A Virtual Light whose member is a light group that includes the light.

The flows refuse such a member, but a group can be changed afterwards, or the
light saved before the check existed. Driving it would call the light again.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight import light as ml
from tests.conftest import (
    light_targets,
    make_light_entry,
    record_service_calls,
    settle,
    setup_entries,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

VIRTUAL = "light.cycle_light"
GROUP = "light.cycle_group"
LAMP = "light.lamp"
BULB = "light.bulb"


def _group_entry(members: list[str]) -> MockConfigEntry:
    return MockConfigEntry(
        domain="group",
        title="Cycle Group",
        options={
            "group_type": "light",
            "name": "Cycle Group",
            "entities": members,
            "hide_members": False,
        },
    )


async def _count_turn_offs(hass: HomeAssistant, monkeypatch) -> list[int]:
    """Turn the virtual light off, counting the turn-offs it receives (capped)."""
    count = [0]
    original = ml.VirtualLight.async_turn_off

    async def _counting(self, **kwargs):
        count[0] += 1
        if count[0] <= 10:
            await original(self, **kwargs)

    monkeypatch.setattr(ml.VirtualLight, "async_turn_off", _counting)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    return count


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["loaded", "unloaded"])
async def test_a_member_group_that_includes_the_light_is_not_driven(
    hass: HomeAssistant, monkeypatch, caplog: pytest.LogCaptureFixture, group: str
) -> None:
    """The group is left alone, with a warning, and the other members still work."""
    for entity_id in (LAMP, BULB):
        hass.states.async_set(entity_id, "off")
    group_entry = _group_entry([VIRTUAL, BULB])
    if group == "loaded":
        await setup_entries(hass, group_entry)
    else:
        group_entry.add_to_hass(hass)
        er.async_get(hass).async_get_or_create(
            "light",
            "group",
            group_entry.entry_id,
            config_entry=group_entry,
            suggested_object_id="cycle_group",
        )
    caplog.set_level(logging.WARNING)
    await setup_entries(
        hass, make_light_entry(name="Cycle Light", lights=[GROUP, LAMP])
    )
    await settle(hass)
    assert f"{VIRTUAL} does not control {GROUP}" in caplog.text

    calls = record_service_calls(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert hass.states.get(VIRTUAL).state == "on"
    assert light_targets(calls, "turn_on")[-1] == [LAMP]

    assert (await _count_turn_offs(hass, monkeypatch))[0] == 1
    assert light_targets(calls, "turn_off")[-1] == [LAMP]
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
async def test_a_member_group_changed_to_include_the_light_is_dropped(
    hass: HomeAssistant, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A group edited to include the light while it runs is let go at once."""
    for entity_id in (LAMP, BULB):
        hass.states.async_set(entity_id, "off")
    group_entry = _group_entry([BULB])
    await setup_entries(hass, group_entry)
    caplog.set_level(logging.WARNING)
    await setup_entries(
        hass, make_light_entry(name="Cycle Light", lights=[GROUP, LAMP])
    )
    await settle(hass)
    assert "does not control" not in caplog.text

    hass.config_entries.async_update_entry(
        group_entry, options={**group_entry.options, "entities": [VIRTUAL, BULB]}
    )
    assert await hass.config_entries.async_reload(group_entry.entry_id)
    await settle(hass)
    assert hass.states.get(GROUP).attributes["entity_id"] == [VIRTUAL, BULB]
    assert f"{VIRTUAL} does not control {GROUP}" in caplog.text

    calls = record_service_calls(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert light_targets(calls, "turn_on")[-1] == [LAMP]
    assert (await _count_turn_offs(hass, monkeypatch))[0] == 1


@pytest.mark.asyncio
async def test_a_member_group_without_the_light_is_still_driven(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Sharing a bulb with other lights is no cycle."""
    hass.states.async_set(BULB, "off")
    await setup_entries(hass, _group_entry([BULB, "light.other"]))
    caplog.set_level(logging.WARNING)
    await setup_entries(hass, make_light_entry(name="Cycle Light", lights=[GROUP]))
    await settle(hass)

    calls = record_service_calls(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert [GROUP] in light_targets(calls, "turn_on")
    assert "does not control" not in caplog.text
