"""A Virtual Light whose member is a light group that includes the light.

The flows refuse such a member, but a group can be changed afterwards, or the
light saved before the check existed. Driving it would call the light again.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.molight import light as ml
from tests.conftest import (
    light_targets,
    make_light_entry,
    record_service_calls,
    settle,
    setup_entries,
)
from tests.real_entities import RealLight, add_real

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

VIRTUAL = "light.cycle_light"
GROUP = "light.cycle_group"
LAMP = "light.lamp"
BULB = "light.bulb"


def _group_entry(members: list[str], name: str = "Cycle Group") -> MockConfigEntry:
    return MockConfigEntry(
        domain="group",
        title=name,
        options={
            "group_type": "light",
            "name": name,
            "entities": members,
            "hide_members": False,
        },
    )


async def _count_commands(
    hass: HomeAssistant, monkeypatch, service: str, entity_id: str = VIRTUAL
) -> int:
    """Send one command, counting what every virtual light receives (capped)."""
    count = [0]
    method = f"async_{service}"
    original = getattr(ml.VirtualLight, method)

    async def _counting(self, **kwargs):
        count[0] += 1
        if count[0] <= 10:
            await original(self, **kwargs)

    monkeypatch.setattr(ml.VirtualLight, method, _counting)
    await hass.services.async_call(
        "light", service, {"entity_id": entity_id}, blocking=True
    )
    await settle(hass)
    return count[0]


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
@pytest.mark.allow_warning_log
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
@pytest.mark.allow_warning_log
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


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["turn_on", "turn_off"])
@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.allow_warning_log
async def test_a_group_below_a_member_changed_to_include_the_light(
    hass: HomeAssistant,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
    service: str,
    depth: int,
) -> None:
    """The member's own list and state stay as they were, so nothing reports it."""
    lamp, bulb = RealLight("lamp"), RealLight("bulb")
    await add_real(hass, lamp, bulb)
    # Each level keeps the lamp, so the level above stays available throughout.
    levels = [_group_entry([BULB], "Level 0")]
    levels += [
        _group_entry([f"light.level_{level - 1}", LAMP], f"Level {level}")
        for level in range(1, depth + 1)
    ]
    await setup_entries(hass, *levels)
    top = f"light.level_{depth}"
    await setup_entries(hass, make_light_entry(name="Cycle Light", lights=[top]))
    await settle(hass)

    hass.config_entries.async_update_entry(
        levels[0], options={**levels[0].options, "entities": [BULB, VIRTUAL]}
    )
    assert await hass.config_entries.async_reload(levels[0].entry_id)
    await settle(hass)
    assert "does not control" not in caplog.text

    assert await _count_commands(hass, monkeypatch, service) == 1
    assert f"{VIRTUAL} does not control {top}" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["turn_on", "turn_off"])
@pytest.mark.parametrize("entered", ["wrapper", "wrapped"])
@pytest.mark.allow_warning_log
async def test_a_group_changed_to_include_the_light_that_wraps_its_owner(
    hass: HomeAssistant, monkeypatch, service: str, entered: str
) -> None:
    """A cycle through another virtual light is stopped wherever it is entered."""
    lamp, bulb = RealLight("lamp"), RealLight("bulb")
    await add_real(hass, lamp, bulb)
    below = _group_entry([BULB], "Below")
    await setup_entries(hass, below, _group_entry(["light.below", LAMP]))
    await setup_entries(hass, make_light_entry(name="Wrapped", lights=[GROUP]))
    await setup_entries(
        hass, make_light_entry(name="Cycle Light", lights=["light.wrapped"])
    )
    await settle(hass)

    hass.config_entries.async_update_entry(
        below, options={**below.options, "entities": [BULB, VIRTUAL]}
    )
    assert await hass.config_entries.async_reload(below.entry_id)
    await settle(hass)

    target = VIRTUAL if entered == "wrapper" else "light.wrapped"
    # The wrapper commands the wrapped light once before either notices.
    assert await _count_commands(hass, monkeypatch, service, target) <= 2


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_a_warning_stage_does_not_enter_a_new_cycle(
    hass: HomeAssistant, monkeypatch, freezer
) -> None:
    """A stage the timer reaches is sent without a command, and checks too."""
    lamp, bulb = RealLight("lamp"), RealLight("bulb")
    await add_real(hass, lamp, bulb)
    below = _group_entry([BULB], "Below")
    await setup_entries(hass, below, _group_entry(["light.below", LAMP]))
    await setup_entries(
        hass,
        make_light_entry(
            name="Cycle Light",
            lights=[GROUP],
            timeout=60,
            warn_timeout=30,
            warn_brightness=20,
        ),
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    hass.config_entries.async_update_entry(
        below, options={**below.options, "entities": [BULB, VIRTUAL]}
    )
    assert await hass.config_entries.async_reload(below.entry_id)
    await settle(hass)

    count = [0]
    original = ml.VirtualLight.async_turn_on

    async def _counting(self, **kwargs):
        count[0] += 1
        if count[0] <= 10:
            await original(self, **kwargs)

    monkeypatch.setattr(ml.VirtualLight, "async_turn_on", _counting)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert count[0] == 0
    assert hass.states.get(VIRTUAL).attributes["molight_state"] == "warn"


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["turn_on", "turn_off"])
async def test_a_group_below_a_member_changed_without_the_light_is_still_driven(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, service: str
) -> None:
    """A nested group gaining some other light is no cycle."""
    lamp, bulb, other = RealLight("lamp"), RealLight("bulb"), RealLight("other")
    await add_real(hass, lamp, bulb, other)
    below = _group_entry([BULB], "Below")
    await setup_entries(hass, below, _group_entry(["light.below", LAMP]))
    caplog.set_level(logging.WARNING)
    await setup_entries(hass, make_light_entry(name="Cycle Light", lights=[GROUP]))
    await settle(hass)
    hass.config_entries.async_update_entry(
        below, options={**below.options, "entities": [BULB, "light.other"]}
    )
    assert await hass.config_entries.async_reload(below.entry_id)
    await settle(hass)

    calls = record_service_calls(hass)
    await hass.services.async_call(
        "light", service, {"entity_id": VIRTUAL}, blocking=True
    )
    await settle(hass)
    assert [GROUP] in light_targets(calls, service)
    assert "does not control" not in caplog.text
