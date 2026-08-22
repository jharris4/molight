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
from .const import (
    LIGHT_MAIN,
    LIGHT_MULTI_DIMMER,
    LIGHT_MULTI_ON_OFF,
    LIGHT_MULTI_RGB,
    LIGHT_ON_OFF,
    LIGHT_TIMER,
)
from .entity import TestbedEntity


class TestbedLight(TestbedEntity, LightEntity):
    """A persistent simulated Home Assistant light."""

    def __init__(
        self,
        *args: Any,
        color_modes: set[ColorMode],
        features: LightEntityFeature | None = None,
    ) -> None:
        """Initialize a light whose capabilities arrive with availability."""
        super().__init__(*args)
        self._testbed_color_modes = color_modes
        self._testbed_features = features or LightEntityFeature(0)
        self._set_reported_capabilities(self.available)

    def _set_reported_capabilities(self, reported: bool) -> None:
        """Expose only a valid minimal capability set until the light reports."""
        self._attr_supported_color_modes = (
            self._testbed_color_modes if reported else {ColorMode.ONOFF}
        )
        self._attr_supported_features = (
            self._testbed_features if reported else LightEntityFeature(0)
        )

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
        if ColorMode.ONOFF in self._attr_supported_color_modes:
            return ColorMode.ONOFF
        return ColorMode.UNKNOWN

    @property
    def brightness(self) -> int | None:
        """Return the persisted brightness when supported."""
        if self.color_mode not in (ColorMode.RGB, ColorMode.BRIGHTNESS):
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

    def set_test_available(self, available: bool) -> None:
        """Publish this light's real capabilities when it becomes available."""
        super().set_test_available(available)
        self._set_reported_capabilities(available)


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
            TestbedLight(
                controller,
                LIGHT_TIMER,
                "light.e2e_timer_target",
                "E2E Timer Target",
                color_modes={ColorMode.BRIGHTNESS},
            ),
            TestbedLight(
                controller,
                LIGHT_MULTI_ON_OFF,
                "light.e2e_multi_on_off",
                "E2E Multi On/Off",
                color_modes={ColorMode.ONOFF},
            ),
            TestbedLight(
                controller,
                LIGHT_MULTI_DIMMER,
                "light.e2e_multi_dimmer",
                "E2E Multi Dimmer",
                color_modes={ColorMode.BRIGHTNESS},
            ),
            TestbedLight(
                controller,
                LIGHT_MULTI_RGB,
                "light.e2e_multi_rgb",
                "E2E Multi RGB",
                color_modes={ColorMode.RGB},
                features=LightEntityFeature.TRANSITION,
            ),
        ]
    )
