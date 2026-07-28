"""Diagnostic Last Action sensor for MoLight Virtual Remote entries.

Every Virtual Remote entry gets one "<name> Last Action" sensor. Its state is
the last action the remote executed (turn_on, brightness_down, preset_1, ...),
with the source button, the resolved click kind, the raw event_type, and the
time as attributes — the link between "a button fired" (visible on the source
event entity) and "a light changed" (visible on the virtual light) that would
otherwise only exist in debug logs.

Deliberately not a RestoreEntity: the sensor starts unknown after a restart,
because a stale "last action" from before the restart reads as recent
activity. The runtime announces executions via SIGNAL_REMOTE_ACTION, so the
coupling doesn't depend on entity ids or platform setup order (same pattern
as the Auto-off switch).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    CONF_ENTITY_TYPE,
    CONF_NAME,
    ENTITY_TYPE_REMOTE,
    SIGNAL_REMOTE_ACTION,
)
from .helpers import molight_config

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Last Action sensor for a Virtual Remote entry."""
    if entry.data[CONF_ENTITY_TYPE] == ENTITY_TYPE_REMOTE:
        async_add_entities([RemoteLastActionSensor(hass, entry)])


class RemoteLastActionSensor(SensorEntity):
    """Shows the last action a Virtual Remote executed."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the Last Action sensor."""
        self.hass = hass
        self._entry_id = entry.entry_id
        cfg = molight_config(entry)
        self._attr_name = f"{cfg[CONF_NAME]} Last Action"
        self._attr_unique_id = f"{entry.entry_id}_last_action"
        self._attr_native_value = None
        self._attrs: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        """Subscribe to the runtime's action announcements."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_REMOTE_ACTION.format(self._entry_id),
                self._on_action,
            )
        )

    @callback
    def _on_action(self, payload: dict[str, Any]) -> None:
        """Record an executed binding."""
        self._attr_native_value = payload["action"]
        self._attrs = {
            "button": payload["button"],
            "click": payload["click"],
            "event_type": payload["event_type"],
            "time": payload["when"].isoformat(),
        }
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Details of the last executed binding."""
        return self._attrs
