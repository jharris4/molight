"""Real, registered entities for tests that go through the entity registry.

Most tests drive MoLight with plain states. A change to an entity's registry
entry (a new entity ID, disabling, deleting) only happens to a real entity:
Home Assistant removes it, and for a new ID adds the same object again.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.event import EventEntity
from homeassistant.components.light import (
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
    MockModule,
    MockPlatform,
    mock_integration,
    mock_platform,
)

from tests.conftest import settle

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


class RealLight(LightEntity):
    """A registered real light that does what it is told."""

    _attr_should_poll = False
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}  # noqa: RUF012

    def __init__(self, object_id: str, *, on: bool = False, brightness: int = 255):
        """Create the light; added_gate holds each add until it is set."""
        self.entity_id = f"light.{object_id}"
        self._attr_unique_id = object_id
        self._attr_is_on = on
        self._attr_brightness = brightness
        self.added_gate: asyncio.Event | None = None

    async def async_added_to_hass(self) -> None:
        """Take as long to come back as the test says."""
        if self.added_gate is not None:
            await self.added_gate.wait()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on at the brightness asked for, replying after the call."""
        await asyncio.sleep(0)
        self._attr_is_on = True
        if "brightness" in kwargs:
            self._attr_brightness = kwargs["brightness"]
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off, replying after the call."""
        await asyncio.sleep(0)
        self._attr_is_on = False
        self.async_write_ha_state()


class InstantLight(RealLight):
    """A registered real light that replies inside the service call.

    With kelvin it also reports a color temperature, so every reply carries
    something a command that named no color has to mirror.
    """

    def __init__(self, object_id: str, *, kelvin: int | None = None, **kwargs: Any):
        """Create the light."""
        super().__init__(object_id, **kwargs)
        if kelvin is not None:
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
            self._attr_color_temp_kelvin = kelvin

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on at the brightness asked for, replying at once."""
        self._attr_is_on = True
        if "brightness" in kwargs:
            self._attr_brightness = kwargs["brightness"]
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off, replying at once."""
        self._attr_is_on = False
        self.async_write_ha_state()


class FadingLight(RealLight):
    """A registered real light that reports a fade as it runs.

    A fade publishes where it has got to at each of steps (fractions of its
    length), then where it ends. With hs it shows a color and blends that
    too; with kelvin it shows a color temperature only, within KELVINS. Every
    report comes latency seconds late.
    """

    _attr_supported_features = LightEntityFeature.TRANSITION
    KELVINS = (2202, 6535)

    def __init__(
        self,
        object_id: str,
        *,
        steps: tuple[float, ...] = (0.6,),
        latency: float = 0,
        hs: tuple[float, float] | None = None,
        kelvin: int | None = None,
        **kwargs: Any,
    ):
        """Create the light."""
        super().__init__(object_id, **kwargs)
        self._steps = steps
        self._latency = latency
        self._reports: list[CALLBACK_TYPE] = []
        if hs is not None:
            self._attr_color_mode = ColorMode.HS
            self._attr_supported_color_modes = {ColorMode.HS}
            self._attr_hs_color = hs
        if kelvin is not None:
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
            self._attr_color_temp_kelvin = kelvin
            self._attr_min_color_temp_kelvin, self._attr_max_color_temp_kelvin = (
                self.KELVINS
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Fade to the brightness and color asked for."""
        kelvin = kwargs.get("color_temp_kelvin", self._attr_color_temp_kelvin)
        if kelvin is not None:
            kelvin = min(max(kelvin, self.KELVINS[0]), self.KELVINS[1])
        self._fade(
            kwargs.get("brightness", self._attr_brightness),
            kwargs.get("hs_color", self._attr_hs_color),
            kelvin,
            kwargs.get("transition"),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Fade out."""
        self._fade(
            0,
            self._attr_hs_color,
            self._attr_color_temp_kelvin,
            kwargs.get("transition"),
        )

    def _fade(
        self,
        brightness: int,
        hs: tuple[float, float] | None,
        kelvin: int | None,
        length: float | None,
    ) -> None:
        self._cancel_reports()
        # Off keeps the level to come back on at.
        rest, start_hs = self._attr_brightness, self._attr_hs_color
        start = rest if self._attr_is_on else 0
        start_kelvin = self._attr_color_temp_kelvin
        for fraction in (*self._steps, 1) if length else (1,):
            level = round(start + (brightness - start) * fraction)
            color = hs
            if hs is not None and start_hs is not None:
                turn = (hs[0] - start_hs[0] + 180) % 360 - 180
                color = (
                    (start_hs[0] + turn * fraction) % 360,
                    start_hs[1] + (hs[1] - start_hs[1]) * fraction,
                )
            warmth = kelvin
            if kelvin is not None and start_kelvin is not None:
                warmth = round(start_kelvin + (kelvin - start_kelvin) * fraction)
            report = partial(self._report, level or rest, color, warmth, on=bool(level))
            delay = self._latency + (length or 0) * fraction
            if not delay:
                report(None)
                return
            self._reports.append(async_call_later(self.hass, delay, report))

    @callback
    def _report(
        self,
        brightness: int,
        hs: tuple[float, float] | None,
        kelvin: int | None,
        _now: datetime | None,
        *,
        on: bool,
    ) -> None:
        self._attr_is_on = on
        self._attr_brightness = brightness
        self._attr_hs_color = hs
        self._attr_color_temp_kelvin = kelvin
        self.async_write_ha_state()

    def _cancel_reports(self) -> None:
        for cancel in self._reports:
            cancel()
        self._reports.clear()

    @callback
    def wall(self, *, on: bool = True, **attributes: Any) -> None:
        """Change the light at the wall, which ends any fade."""
        self._cancel_reports()
        self._attr_is_on = on
        for name, value in attributes.items():
            setattr(self, f"_attr_{name}", value)
        self.async_write_ha_state()


class RealBinary(BinarySensorEntity):
    """A registered real binary sensor (motion, door, schedule source)."""

    _attr_should_poll = False

    def __init__(self, object_id: str, *, on: bool = False):
        """Create the sensor; added_gate holds each add until it is set."""
        self.entity_id = f"binary_sensor.{object_id}"
        self._attr_unique_id = object_id
        self._attr_is_on = on
        self.added_gate: asyncio.Event | None = None

    async def async_added_to_hass(self) -> None:
        """Take as long to come back as the test says."""
        if self.added_gate is not None:
            await self.added_gate.wait()

    @callback
    def set(self, on: bool) -> None:
        """Report a new value."""
        self._attr_is_on = on
        self.async_write_ha_state()


class RealToggle(SwitchEntity):
    """A registered toggle, as a keep-on entity."""

    _attr_should_poll = False

    def __init__(self, object_id: str, *, on: bool = False):
        """Create the toggle."""
        self.entity_id = f"switch.{object_id}"
        self._attr_unique_id = object_id
        self._attr_is_on = on

    @callback
    def set(self, on: bool) -> None:
        """Report a new value."""
        self._attr_is_on = on
        self.async_write_ha_state()


class RealLux(SensorEntity):
    """A registered real illuminance sensor."""

    _attr_should_poll = False

    def __init__(self, object_id: str, lux: float):
        """Create the sensor."""
        self.entity_id = f"sensor.{object_id}"
        self._attr_unique_id = object_id
        self._attr_native_value = lux

    @callback
    def set(self, lux: float) -> None:
        """Report a new reading."""
        self._attr_native_value = lux
        self.async_write_ha_state()


class RealSelect(SelectEntity):
    """A registered select, as a turn-on selection target or source."""

    _attr_should_poll = False
    _attr_options = ["Cozy", "Bright"]  # noqa: RUF012

    def __init__(self, object_id: str, option: str = "Cozy"):
        """Create the select."""
        self.entity_id = f"select.{object_id}"
        self._attr_unique_id = object_id
        self._attr_current_option = option

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
        self._attr_current_option = option
        self.async_write_ha_state()


class RealButton(EventEntity):
    """A registered remote button."""

    _attr_should_poll = False
    _attr_event_types = ["single", "double"]  # noqa: RUF012

    def __init__(self, object_id: str):
        """Create the button."""
        self.entity_id = f"event.{object_id}"
        self._attr_unique_id = object_id

    @callback
    def press(self, event_type: str = "single") -> None:
        """Fire a press."""
        self._trigger_event(event_type)
        self.async_write_ha_state()


async def add_real(hass: HomeAssistant, *entities: Entity) -> None:
    """Add real entities, registered under the "test" platform."""
    by_domain: dict[str, list[Entity]] = {}
    for entity in entities:
        by_domain.setdefault(entity.entity_id.split(".")[0], []).append(entity)
    for domain, members in by_domain.items():
        assert await async_setup_component(hass, domain, {})
        platform = MockEntityPlatform(hass, domain=domain, platform_name="test")
        await platform.async_add_entities(members)
    await hass.async_block_till_done()


class _RealFlow(ConfigFlow, domain="real"):
    """The flow an integration's entry needs to be set up."""


async def add_real_with_entry(
    hass: HomeAssistant, *make: Callable[[], Entity]
) -> ConfigEntry:
    """Add real entities from an entry of their own integration, "real".

    Each loading of the entry adds them afresh from make. Disabling it
    disables them too, and leaves the placeholder state Home Assistant
    writes for an entity whose entry is not loaded.
    """
    domains = list(dict.fromkeys(m().entity_id.split(".")[0] for m in make))

    async def setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        await hass.config_entries.async_forward_entry_setups(entry, domains)
        return True

    async def unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        return await hass.config_entries.async_unload_platforms(entry, domains)

    mock_integration(
        hass,
        MockModule(
            "real", async_setup_entry=setup_entry, async_unload_entry=unload_entry
        ),
    )
    mock_platform(hass, "real.config_flow", None)
    for domain in domains:

        async def setup_platform(
            _hass: HomeAssistant,
            _entry: ConfigEntry,
            add: AddEntitiesCallback,
            domain: str = domain,
        ) -> None:
            entities = (m() for m in make)
            add([e for e in entities if e.entity_id.startswith(f"{domain}.")])

        mock_platform(
            hass, f"real.{domain}", MockPlatform(async_setup_entry=setup_platform)
        )
    entry = MockConfigEntry(domain="real")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def rename(hass: HomeAssistant, entity_id: str, new_entity_id: str) -> None:
    """Change an entity's ID the way the entity settings dialog does."""
    er.async_get(hass).async_update_entity(entity_id, new_entity_id=new_entity_id)
    await settle(hass)


async def start_slow_rename(
    hass: HomeAssistant, entity: RealLight | RealBinary, new_entity_id: str
) -> None:
    """Change an entity's ID and leave it gone: set its added_gate to finish."""
    entity.added_gate = asyncio.Event()
    er.async_get(hass).async_update_entity(
        entity.entity_id, new_entity_id=new_entity_id
    )
    # Not block_till_done, which would wait for the entity to come back.
    for _ in range(10):
        await asyncio.sleep(0)
    assert hass.states.get(new_entity_id) is None
