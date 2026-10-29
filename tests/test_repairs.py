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
from tests.conftest import make_light_entry, molight_issues, settle, setup_entries

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

REAL = "light.real_1"
TV = "media_player.tv"


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
    """The light that loads second names the fight; removing the first clears it."""
    hass.states.async_set(REAL, "off")
    room = make_light_entry(name="Room", lights=[REAL])
    other = make_light_entry(name="Other", lights=[REAL])
    await setup_entries(hass, room, other)
    await settle(hass)
    assert molight_issues(hass, room.entry_id) == {}
    assert molight_issues(hass, other.entry_id) == {
        f"light_shared_member_{REAL}": {
            "entity": "light.other",
            "other": "light.room",
            "light": REAL,
        }
    }

    await hass.config_entries.async_remove(room.entry_id)
    await settle(hass)
    assert molight_issues(hass, other.entry_id) == {}


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
