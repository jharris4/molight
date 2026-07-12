"""Companion Auto-off switch for MoLight Virtual Lights.

Every Virtual Light config entry gets one "<name> Auto-off" switch. ON (the
default) means normal behavior; OFF suspends all of the light's automatic
turn-offs — exactly like a configured keep-on entity being on — until it is
turned back on. Turn-ons and manual control are never affected.

The switch mirrors its state into hass.data[DOMAIN][entry_id] and notifies
the light through the dispatcher, so the coupling doesn't depend on entity
ids or platform setup order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import ENTITY_ID_FORMAT, SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_ENTITY_TYPE,
    CONF_NAME,
    DATA_AUTO_OFF_ENABLED,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    SIGNAL_AUTO_OFF_TOGGLED,
)
from .helpers import molight_config, suggested_entity_id

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the companion switch for a Virtual Light entry."""
    if entry.data[CONF_ENTITY_TYPE] == ENTITY_TYPE_LIGHT:
        entity = AutoOffSwitch(hass, entry)
        # Parallel the light's explicit id: light.kitchen -> switch.kitchen_auto_off.
        if entity_id := suggested_entity_id(
            hass, entry, ENTITY_ID_FORMAT, suffix="_auto_off"
        ):
            entity.entity_id = entity_id
        async_add_entities([entity])


class AutoOffSwitch(SwitchEntity, RestoreEntity):
    """Enables/disables a Virtual Light's automatic turn-offs."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the auto-off switch."""
        self.hass = hass
        self._entry_id = entry.entry_id
        cfg = molight_config(entry)
        self._attr_name = f"{cfg[CONF_NAME]} Auto-off"
        self._attr_unique_id = f"{entry.entry_id}_auto_off"
        self._attr_is_on = True

    async def async_added_to_hass(self) -> None:
        """Restore the previous state and publish it to the light."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_OFF:
            self._attr_is_on = False
            self._publish()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Re-enable the light's automatic turn-offs."""
        self._attr_is_on = True
        self._publish()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Suspend the light's automatic turn-offs."""
        self._attr_is_on = False
        self._publish()
        self.async_write_ha_state()

    def _publish(self) -> None:
        """Mirror the state into hass.data and notify the virtual light."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if entry_data is not None:
            entry_data[DATA_AUTO_OFF_ENABLED] = self._attr_is_on
        async_dispatcher_send(self.hass, SIGNAL_AUTO_OFF_TOGGLED.format(self._entry_id))
