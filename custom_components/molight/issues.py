"""Repairs issues for settings MoLight cannot act on.

Each is raised by the entity that finds the problem and cleared when its
entry unloads, so a corrected entry starts clean and one still broken raises
it again. Issue IDs start with the entry ID so an entry's issues can be found.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir

from .const import CONF_ENTITY_TYPE, CONF_LIGHTS, DOMAIN
from .helpers import (
    VIRTUAL_LIGHT_TYPES,
    entry_settings,
    light_owners,
    lights_commanded,
)

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant

ISSUE_COMBINED_SCHEDULE_CYCLE = "combined_schedule_cycle"
ISSUE_HOLD_ALWAYS_ON = "hold_always_on"
ISSUE_HOLD_NEVER_ON = "hold_never_on"
ISSUE_LIGHT_MEMBER_CYCLE = "light_member_cycle"
ISSUE_LIGHT_SHARED_MEMBER = "light_shared_member"
ISSUE_OCCUPANCY_SOURCE_CYCLE = "occupancy_source_cycle"
ISSUE_SCHEDULE_SOURCE_CYCLE = "schedule_source_cycle"
ISSUE_SCHEDULE_WINDOW_NEVER_OPENS = "schedule_window_never_opens"
ISSUE_SCHEDULED_LIGHT_NO_SCHEDULE = "scheduled_light_no_schedule"


@callback
def async_raise_issue(
    hass: HomeAssistant,
    entry_id: str,
    key: str,
    placeholders: dict[str, str],
    *,
    about: str | None = None,
) -> str:
    """Raise a warning issue for an entry and return its ID.

    `about` tells apart several issues of one kind on one entry, such as one
    per rejected keep-on entity.
    """
    issue_id = f"{entry_id}_{key}" if about is None else f"{entry_id}_{key}_{about}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=key,
        translation_placeholders=placeholders,
    )
    return issue_id


@callback
def async_check_shared_members(hass: HomeAssistant) -> None:
    """Raise one issue per light two virtual lights command, and clear the rest.

    A light belongs to the first virtual light by name that commands it
    (see light_owners); every other one commanding it gets the issue,
    naming the owner. Run when a virtual light loads, unloads or is removed
    and when a member light group changes, so an issue clears as soon as
    the fight ends, whichever side ended it.
    """
    lights, owned = light_owners(hass)
    names = {entry.entry_id: entity_id for entity_id, entry in lights.items()}
    wanted: dict[str, tuple[str, str, dict[str, str]]] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        settings = entry_settings(entry)
        if settings.get(CONF_ENTITY_TYPE) not in VIRTUAL_LIGHT_TYPES:
            continue
        name = names.get(entry.entry_id, entry.title)
        for light in lights_commanded(hass, settings.get(CONF_LIGHTS, []), lights):
            owner = owned.get(light)
            if owner is None or owner == name:
                continue
            issue_id = f"{entry.entry_id}_{ISSUE_LIGHT_SHARED_MEMBER}_{light}"
            placeholders = {"entity": name, "other": owner, "light": light}
            wanted[issue_id] = (entry.entry_id, light, placeholders)
    registry = ir.async_get(hass)
    existing = {
        issue_id
        for (domain, issue_id), issue in registry.issues.items()
        if domain == DOMAIN and issue.translation_key == ISSUE_LIGHT_SHARED_MEMBER
    }
    for issue_id in existing - wanted.keys():
        registry.async_delete(DOMAIN, issue_id)
    for issue_id, (entry_id, light, placeholders) in wanted.items():
        if issue_id not in existing:
            _LOGGER.warning(
                "%s and %s both control %s, so they fight over it; give it to one "
                "of them in Configure, or add the other light instead of it",
                placeholders["entity"],
                placeholders["other"],
                light,
            )
        async_raise_issue(
            hass, entry_id, ISSUE_LIGHT_SHARED_MEMBER, placeholders, about=light
        )


@callback
def async_clear_issue(hass: HomeAssistant, issue_id: str) -> None:
    """Clear one issue; a missing one is nothing to clear."""
    ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def async_clear_entry_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Clear every issue an entry raised."""
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id.startswith(f"{entry_id}_"):
            registry.async_delete(DOMAIN, issue_id)


@callback
def async_clear_issues_naming(hass: HomeAssistant, entity_ids: Iterable[str]) -> None:
    """Clear every issue whose placeholders name one of these entities.

    An issue about an entity that no longer exists is stale, however the
    entry that raised it is doing.
    """
    gone = set(entity_ids)
    registry = ir.async_get(hass)
    for (domain, issue_id), issue in list(registry.issues.items()):
        named = set((issue.translation_placeholders or {}).values())
        if domain == DOMAIN and gone & named:
            registry.async_delete(DOMAIN, issue_id)
