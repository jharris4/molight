"""Helpers for the Events entity tests: what an entity recorded, in order."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import callback

if TYPE_CHECKING:
    from homeassistant.core import Event, EventStateChangedData, HomeAssistant


def record_events(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]]:
    """Record every event an Events entity fires from now on.

    Each record is the entity's attributes less the capability list and the
    friendly name, so a test compares exactly what an automation reads.
    """
    records: list[dict[str, Any]] = []

    @callback
    def _record(event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        old_state = event.data["old_state"]
        if (
            new_state is None
            or new_state.state in ("unknown", "unavailable")
            or (old_state is not None and old_state.state == new_state.state)
        ):
            return
        records.append(
            {
                k: v
                for k, v in new_state.attributes.items()
                if k not in ("event_types", "friendly_name")
            }
        )

    @callback
    def _ours(data: EventStateChangedData) -> bool:
        return data["entity_id"] == entity_id

    hass.bus.async_listen("state_changed", _record, event_filter=_ours)
    return records


async def tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    """Move the clock on and let every timer that came due run."""
    from datetime import timedelta  # noqa: PLC0415

    from pytest_homeassistant_custom_component.common import (  # noqa: PLC0415
        async_fire_time_changed,
    )

    from tests.conftest import settle  # noqa: PLC0415

    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)
