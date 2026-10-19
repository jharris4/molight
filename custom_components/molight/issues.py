"""Repairs issues for settings MoLight cannot act on.

Each is raised by the entity that finds the problem and cleared when its
entry unloads, so a corrected entry starts clean and one still broken raises
it again. Issue IDs start with the entry ID so an entry's issues can be found.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

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
    per refused keep-on entity.
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
