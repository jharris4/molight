"""Simulated lights for live MoLight acceptance tests."""

from __future__ import annotations

from copy import deepcopy
from functools import partial
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import color as color_util

from . import controller_for_entry
from .const import (
    DEFAULT_BEHAVIOR,
    LIGHT_CT,
    LIGHT_MAIN,
    LIGHT_MULTI_DIMMER,
    LIGHT_MULTI_ON_OFF,
    LIGHT_MULTI_RGB,
    LIGHT_TIMER,
)
from .entity import TestbedEntity

REPORT_STEP_GAP = 0.3  # seconds between a piecewise power and attribute report


class TestbedLight(TestbedEntity, LightEntity):
    """A persistent simulated light with configurable reporting behavior.

    The record holds the truth a command produced; ``_reported`` is what Home
    Assistant has been told so far, which lags or fuzzes per the behavior.
    """

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
        if ColorMode.COLOR_TEMP in color_modes:
            self._attr_min_color_temp_kelvin = 2000
            self._attr_max_color_temp_kelvin = 6500
        self._pending: list[CALLBACK_TYPE] = []
        self._reported: dict[str, Any] = {}
        self._sync_reported()
        self._set_reported_capabilities(self.available)

    @property
    def behavior(self) -> dict[str, Any]:
        """Return the effective reporting behavior."""
        return {**DEFAULT_BEHAVIOR, **self.record.get("behavior", {})}

    def _color_modes(self) -> set[ColorMode]:
        """Return the advertised color modes, swapping RGB for XY on request."""
        modes = set(self._testbed_color_modes)
        if self.behavior["xy_color"] and ColorMode.RGB in modes:
            modes = (modes - {ColorMode.RGB}) | {ColorMode.XY}
        return modes

    def _set_reported_capabilities(self, reported: bool) -> None:
        """Expose only a valid minimal capability set until the light reports."""
        self._attr_supported_color_modes = (
            self._color_modes() if reported else {ColorMode.ONOFF}
        )
        self._attr_supported_features = (
            self._testbed_features if reported else LightEntityFeature(0)
        )

    def _sync_reported(self) -> None:
        """Make the reported view match the record (an instant, exact report)."""
        self._reported = {
            "state": self.record["state"],
            "attributes": deepcopy(self.record["attributes"]),
            "last_command": deepcopy(self.record.get("last_command")),
        }

    def _cancel_pending(self) -> None:
        for cancel in self._pending:
            cancel()
        self._pending.clear()

    @property
    def is_on(self) -> bool:
        """Return the reported power state."""
        return self._reported["state"] == "on"

    @property
    def color_mode(self) -> ColorMode:
        """Return a stable current color mode."""
        for mode in (
            ColorMode.XY,
            ColorMode.RGB,
            ColorMode.COLOR_TEMP,
            ColorMode.BRIGHTNESS,
            ColorMode.ONOFF,
        ):
            if mode in self._attr_supported_color_modes:
                return mode
        return ColorMode.UNKNOWN

    @property
    def brightness(self) -> int | None:
        """Return the reported brightness, quantised to the device's levels."""
        if self.color_mode not in (
            ColorMode.XY,
            ColorMode.RGB,
            ColorMode.COLOR_TEMP,
            ColorMode.BRIGHTNESS,
        ):
            return None
        value = int(self._reported["attributes"].get(ATTR_BRIGHTNESS, 0))
        levels = int(self.behavior["brightness_levels"])
        if levels and value:
            value = round(round(value / 255 * levels) / levels * 255)
        return value

    def _reported_rgb(self) -> tuple[int, int, int]:
        return tuple(self._reported["attributes"].get(ATTR_RGB_COLOR, [255, 255, 255]))

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the reported RGB color when that is the advertised mode."""
        if self.color_mode is not ColorMode.RGB:
            return None
        return self._reported_rgb()

    @property
    def xy_color(self) -> tuple[float, float] | None:
        """Return the reported XY color when that is the advertised mode."""
        if self.color_mode is not ColorMode.XY:
            return None
        if xy := self._reported["attributes"].get(ATTR_XY_COLOR):
            return (float(xy[0]), float(xy[1]))
        return color_util.color_RGB_to_xy(*self._reported_rgb())

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the reported colour temperature when that is the advertised mode."""
        if self.color_mode is not ColorMode.COLOR_TEMP:
            return None
        return int(self._reported["attributes"].get(ATTR_COLOR_TEMP_KELVIN, 3000))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the last command for black-box service-routing assertions."""
        return {"testbed_last_command": self._reported.get("last_command")}

    def _reject_if_asked(self) -> None:
        if self.behavior["reject"]:
            raise HomeAssistantError(f"{self.entity_id} rejected the command (testbed)")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Apply and persist a turn-on command, then report it per behavior."""
        self._reject_if_asked()
        previous = int(self.record["attributes"].get(ATTR_BRIGHTNESS, 0) or 0)
        self.record["state"] = "on"
        if ATTR_BRIGHTNESS in kwargs:
            self.record["attributes"][ATTR_BRIGHTNESS] = kwargs[ATTR_BRIGHTNESS]
        if ATTR_RGB_COLOR in kwargs:
            self.record["attributes"][ATTR_RGB_COLOR] = list(kwargs[ATTR_RGB_COLOR])
            self.record["attributes"].pop(ATTR_XY_COLOR, None)
        if ATTR_XY_COLOR in kwargs:
            self.record["attributes"][ATTR_XY_COLOR] = list(kwargs[ATTR_XY_COLOR])
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            self.record["attributes"][ATTR_COLOR_TEMP_KELVIN] = int(
                kwargs[ATTR_COLOR_TEMP_KELVIN]
            )
        self.record["last_command"] = {"service": "turn_on", "data": kwargs}
        await self.controller.async_save()
        self._schedule_reports(previous, kwargs.get(ATTR_TRANSITION))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Apply and persist a turn-off command, then report it per behavior."""
        self._reject_if_asked()
        self.record["state"] = "off"
        self.record["last_command"] = {"service": "turn_off", "data": kwargs}
        await self.controller.async_save()
        self._schedule_reports(None, None)

    def _schedule_reports(
        self, previous_brightness: int | None, transition: float | None
    ) -> None:
        """Publish the record now, or as the delayed/piecewise reports behavior asks."""
        self._cancel_pending()
        behavior = self.behavior
        if behavior["silent"]:
            return  # applied, but Home Assistant never hears about it
        latency = float(behavior["latency"])
        steps = int(behavior["transition_steps"]) if transition else 0
        piecewise = bool(behavior["report_steps"]) and self.record["state"] == "on"
        if latency <= 0 and not piecewise and steps <= 0:
            self._sync_reported()
            self.async_write_ha_state()
            return
        reports: list[tuple[float, dict[str, Any] | None]] = []
        due = latency
        if piecewise:
            reports.append((due, {"state": "on"}))
            due += REPORT_STEP_GAP
        target = int(self.record["attributes"].get(ATTR_BRIGHTNESS, 0) or 0)
        if steps and previous_brightness is not None and target != previous_brightness:
            for index in range(1, steps + 1):
                fraction = index / (steps + 1)
                level = round(
                    previous_brightness + (target - previous_brightness) * fraction
                )
                reports.append(
                    (
                        due + float(transition) * fraction,
                        {"attributes": {ATTR_BRIGHTNESS: level}},
                    )
                )
            due += float(transition)
        reports.append((due, None))
        for delay, partial_report in reports:
            self._pending.append(
                async_call_later(
                    self.hass, delay, partial(self._report, partial_report)
                )
            )

    @callback
    def _report(self, partial_report: dict[str, Any] | None, _now: Any) -> None:
        """Publish one delayed report: a partial update or the full record."""
        if partial_report is None:
            self._sync_reported()
        else:
            if "state" in partial_report:
                self._reported["state"] = partial_report["state"]
            self._reported["attributes"].update(partial_report.get("attributes", {}))
            self._reported["last_command"] = deepcopy(self.record.get("last_command"))
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
        self._cancel_pending()
        self._sync_reported()

    def set_test_available(self, available: bool) -> None:
        """Publish this light's real capabilities when it becomes available."""
        super().set_test_available(available)
        self._cancel_pending()
        self._sync_reported()
        self._set_reported_capabilities(available)

    def set_test_behavior(self, behavior: dict[str, Any]) -> None:
        """Merge and persist reporting behavior, re-advertising capabilities."""
        unknown = set(behavior) - set(DEFAULT_BEHAVIOR)
        if unknown:
            raise ValueError(f"Unknown light behavior keys: {sorted(unknown)}")
        merged = {**self.record.get("behavior", {}), **behavior}
        self.record["behavior"] = {
            key: value
            for key, value in merged.items()
            if value != DEFAULT_BEHAVIOR[key]
        }
        self._cancel_pending()
        self._sync_reported()
        self._set_reported_capabilities(self.available)


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
                LIGHT_TIMER,
                "light.e2e_timer_target",
                "E2E Timer Target",
                color_modes={ColorMode.BRIGHTNESS},
            ),
            TestbedLight(
                controller,
                LIGHT_CT,
                "light.e2e_ct",
                "E2E CT Light",
                color_modes={ColorMode.COLOR_TEMP},
                features=LightEntityFeature.TRANSITION,
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
