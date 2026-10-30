"""Repairs issues for settings MoLight cannot act on, and when they clear."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_HOLD_ENTITIES,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    DOMAIN,
    ENTITY_TYPE_SCHEDULED_LIGHT,
)
from custom_components.molight.helpers import entry_settings
from tests.conftest import (
    make_light_entry,
    molight_issues,
    restart_entries,
    settle,
    setup_entries,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

REAL = "light.real_1"
REAL2 = "light.real_2"
GROUP = "light.group_1"
TV = "media_player.tv"


def _fight(entity: str, other: str, light: str = REAL) -> dict[str, dict[str, str]]:
    return {
        f"light_shared_member_{light}": {
            "entity": entity,
            "other": other,
            "light": light,
        }
    }


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_rejected_keep_on_entities_raise_one_issue_each(
    hass: HomeAssistant,
) -> None:
    """Each names the light, the entity, and why it never holds."""
    hass.states.async_set(REAL, "off")
    entry = make_light_entry(name="Room", lights=[REAL], hold_entities=[REAL, TV])
    await setup_entries(hass, entry)
    await settle(hass)

    assert molight_issues(hass, entry.entry_id) == {
        f"hold_always_on_{REAL}": {"entity": "light.room", "hold": REAL},
        f"hold_never_on_{TV}": {"entity": "light.room", "hold": TV},
    }


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_issues_clear_on_unload_and_return_until_corrected(
    hass: HomeAssistant,
) -> None:
    """An edit that keeps the problem raises it again; one that fixes it does not."""
    hass.states.async_set(REAL, "off")
    entry = make_light_entry(name="Room", lights=[REAL], hold_entities=[TV])
    await setup_entries(hass, entry)
    await settle(hass)
    assert list(molight_issues(hass, entry.entry_id)) == [f"hold_never_on_{TV}"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert molight_issues(hass, entry.entry_id) == {}

    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    assert list(molight_issues(hass, entry.entry_id)) == [f"hold_never_on_{TV}"]

    hass.config_entries.async_update_entry(
        entry, options={**entry_settings(entry), CONF_HOLD_ENTITIES: []}
    )
    await settle(hass)
    assert molight_issues(hass, entry.entry_id) == {}


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_a_shared_light_is_an_issue_until_the_other_light_goes(
    hass: HomeAssistant,
) -> None:
    """The light first by name owns a shared light and the other names the
    fight, whichever loaded first; removing the owner ends it."""
    hass.states.async_set(REAL, "off")
    room = make_light_entry(name="Room", lights=[REAL])
    other = make_light_entry(name="Other", lights=[REAL])
    await setup_entries(hass, room, other)
    await settle(hass)
    assert molight_issues(hass, room.entry_id) == _fight("light.room", "light.other")
    assert molight_issues(hass, other.entry_id) == {}

    await hass.config_entries.async_remove(other.entry_id)
    await settle(hass)
    assert molight_issues(hass, room.entry_id) == {}


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
@pytest.mark.parametrize("edited", ["owner", "named"])
async def test_a_shared_light_issue_clears_when_either_light_gives_it_up(
    hass: HomeAssistant, edited: str
) -> None:
    """Ending the fight through either entry clears the issue, though only
    the edited entry reloads."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(REAL2, "off")
    room = make_light_entry(name="Room", lights=[REAL])
    other = make_light_entry(name="Other", lights=[REAL])
    await setup_entries(hass, room, other)
    await settle(hass)
    assert molight_issues(hass, room.entry_id) == _fight("light.room", "light.other")

    entry = other if edited == "owner" else room
    hass.config_entries.async_update_entry(
        entry, options={**entry_settings(entry), CONF_LIGHTS: [REAL2]}
    )
    await settle(hass)

    assert molight_issues(hass, room.entry_id) == {}
    assert molight_issues(hass, other.entry_id) == {}


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_a_shared_light_is_one_issue_after_a_restart(hass: HomeAssistant) -> None:
    """With both entries known before either loads, the fight is still one issue."""
    hass.states.async_set(REAL, "off")
    room = make_light_entry(name="Room", lights=[REAL])
    other = make_light_entry(name="Other", lights=[REAL])
    await setup_entries(hass, room, other)
    await settle(hass)

    await restart_entries(hass, room, other)
    await settle(hass)

    assert molight_issues(hass, room.entry_id) == _fight("light.room", "light.other")
    assert molight_issues(hass, other.entry_id) == {}


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_a_light_shared_through_a_group_clears_when_the_group_changes(
    hass: HomeAssistant,
) -> None:
    """A fight through a light group ends when the group drops the light,
    which reloads neither entry."""
    hass.states.async_set(REAL, "off")
    hass.states.async_set(REAL2, "off")
    hass.states.async_set(GROUP, "off", {"entity_id": [REAL]})
    room = make_light_entry(name="Room", lights=[GROUP])
    other = make_light_entry(name="Other", lights=[REAL])
    await setup_entries(hass, room, other)
    await settle(hass)
    assert molight_issues(hass, room.entry_id) == _fight("light.room", "light.other")
    assert molight_issues(hass, other.entry_id) == {}

    hass.states.async_set(GROUP, "off", {"entity_id": [REAL2]})
    await settle(hass)

    assert molight_issues(hass, room.entry_id) == {}
    assert molight_issues(hass, other.entry_id) == {}


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_an_owner_named_by_title_is_named_by_its_light_once_it_loads(
    hass: HomeAssistant,
) -> None:
    """An entry with no light entity yet is named by title; once its light
    exists the fight is judged again by entity ID, and the issue follows."""
    hass.states.async_set(REAL, "off")
    room = make_light_entry(name="Room", lights=[REAL])
    other = make_light_entry(name="Other", lights=[REAL])
    await setup_entries(hass, other)
    room.add_to_hass(hass)  # known, not loaded: no light entity yet
    await hass.config_entries.async_reload(other.entry_id)
    await settle(hass)
    assert molight_issues(hass, other.entry_id) == _fight("light.other", room.title)

    assert await hass.config_entries.async_setup(room.entry_id)
    await settle(hass)

    assert molight_issues(hass, other.entry_id) == {}
    assert molight_issues(hass, room.entry_id) == _fight("light.room", "light.other")


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_a_scheduled_light_with_no_schedule_raises_an_issue(
    hass: HomeAssistant,
) -> None:
    """The light runs on its outside settings and says so."""
    hass.states.async_set(REAL, "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
            CONF_NAME: "Porch",
            CONF_LIGHTS: [REAL],
            CONF_OUTSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 60},
            CONF_INSIDE_SCHEDULE_SETTINGS: {CONF_LIGHT_TIMEOUT: 60},
        },
    )
    await setup_entries(hass, entry)
    await settle(hass)

    assert molight_issues(hass, entry.entry_id) == {
        "scheduled_light_no_schedule": {"entity": "light.porch"}
    }
    assert hass.states.get("light.porch").attributes["active_settings"] == (
        "outside_schedule"
    )
