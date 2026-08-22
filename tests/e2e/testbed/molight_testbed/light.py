"""Simulated lights for live MoLight acceptance tests."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import controller_for_entry
from .const import LIGHT_MAIN, LIGHT_ON_OFF
from .entity import TestbedEntity


class TestbedLight(TestbedEntity, LightEntity):
    """A persistent simulated Home Assistant light."""

    def __init__(
        self,
        *args: Any,
        color_modes: set[ColorMode],
        features: LightEntityFeature | None = None,
    ) -> None:
        """Initialize a light with fixed capabilities."""
        super().__init__(*args)
        self._attr_supported_color_modes = color_modes
        self._attr_supported_features = features or LightEntityFeature(0)

    @property
    def is_on(self) -> bool:
        """Return the persisted power state."""
        return self.record["state"] == "on"

    @property
    def color_mode(self) -> ColorMode:
        """Return a stable current color mode."""
        if ColorMode.RGB in self._attr_supported_color_modes:
            return ColorMode.RGB
        if ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def brightness(self) -> int | None:
        """Return the persisted brightness when supported."""
        if self.color_mode is ColorMode.ONOFF:
            return None
        return int(self.record["attributes"].get(ATTR_BRIGHTNESS, 0))

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the persisted RGB color when supported."""
        if self.color_mode is not ColorMode.RGB:
            return None
        value = self.record["attributes"].get(ATTR_RGB_COLOR, [255, 255, 255])
        return tuple(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the last command for black-box service-routing assertions."""
        return {"testbed_last_command": self.record.get("last_command")}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Apply and persist a turn-on command."""
        self.record["state"] = "on"
        if ATTR_BRIGHTNESS in kwargs:
            self.record["attributes"][ATTR_BRIGHTNESS] = kwargs[ATTR_BRIGHTNESS]
        if ATTR_RGB_COLOR in kwargs:
            self.record["attributes"][ATTR_RGB_COLOR] = list(kwargs[ATTR_RGB_COLOR])
        self.record["last_command"] = {"service": "turn_on", "data": kwargs}
        await self.controller.async_save()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Apply and persist a turn-off command."""
        self.record["state"] = "off"
        self.record["last_command"] = {"service": "turn_off", "data": kwargs}
        await self.controller.async_save()
        self.async_write_ha_state()

    def set_test_state(
        self, state: str | float | bool, attributes: dict[str, Any]
    ) -> None:
        """Set power and optional light attributes through the test service."""
        normalized = str(state).lower()
        if normalized not in ("on", "off"):
            raise ValueError(f"Light state must be on or off, got {state!r}")
        self.record["state"] = normalized
        self.record["attributes"].update(attributes)
        self.record["last_command"] = {
            "service": "testbed_set_state",
            "data": {"state": normalized, **attributes},
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add representative lights with deterministic entity ids."""
    controller = controller_for_entry(hass, entry)
    async_add_entities(
        [
            TestbedLight(
                controller,
                LIGHT_MAIN,
                "light.e2e_main",
                "E2E Main Light",
                color_modes={ColorMode.RGB},
                features=LightEntityFeature.TRANSITION,
            ),
            TestbedLight(
                controller,
                LIGHT_ON_OFF,
                "light.e2e_on_off",
                "E2E On/Off Light",
                color_modes={ColorMode.ONOFF},
            ),
        ]
    )
