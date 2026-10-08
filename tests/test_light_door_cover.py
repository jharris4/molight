"""Cover-as-door tests for the MoLight Virtual Light.

A cover is open in every state but closed, so its moving states (opening,
closing) are no edge of their own: only closed -> anything lights the room,
and only reaching closed releases an open_close hold. test_light_door.py
runs the shared door behavior against a cover too; these cover what only a
cover can report.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    DOOR_MODE_OPEN,
    DOOR_MODE_OPEN_CLOSE,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_OCCUPIED,
)
from tests.conftest import make_light_entry, settle, setup_entries

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

GARAGE = "cover.garage_door"
VIRTUAL = "light.matrix_light"


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


async def _tick(hass: HomeAssistant, freezer, seconds: int) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


async def _set(hass: HomeAssistant, state: str) -> None:
    hass.states.async_set(GARAGE, state)
    await settle(hass)


@pytest.mark.asyncio
async def test_open_mode_moving_cover_does_not_retrigger(
    hass: HomeAssistant, freezer
) -> None:
    """Opening lights the room once; open and closing keep the first timer."""
    hass.states.async_set(GARAGE, "closed")
    await setup_entries(hass, make_light_entry(door=GARAGE, door_mode=DOOR_MODE_OPEN))

    await _set(hass, "opening")
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_ACTIVE

    await _tick(hass, freezer, 20)
    await _set(hass, "open")
    await _tick(hass, freezer, 20)
    await _set(hass, "closing")
    await _tick(hass, freezer, 19)
    assert _state(hass).state == "on"
    await _tick(hass, freezer, 2)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_open_mode_reversal_mid_close_is_not_an_opening(
    hass: HomeAssistant, freezer
) -> None:
    """A cover that reverses before it shuts never closed, so nothing lights."""
    hass.states.async_set(GARAGE, "closed")
    await setup_entries(hass, make_light_entry(door=GARAGE, door_mode=DOOR_MODE_OPEN))

    await _set(hass, "open")
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"

    await _set(hass, "closing")
    await _set(hass, "opening")
    assert _state(hass).state == "off"

    await _set(hass, "closed")
    await _set(hass, "opening")
    assert _state(hass).state == "on"


@pytest.mark.asyncio
async def test_open_close_holds_until_the_cover_is_closed(
    hass: HomeAssistant, freezer
) -> None:
    """The hold lasts through closing; the countdown starts once closed."""
    hass.states.async_set(GARAGE, "closed")
    await setup_entries(
        hass, make_light_entry(door=GARAGE, door_mode=DOOR_MODE_OPEN_CLOSE)
    )

    await _set(hass, "opening")
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    await _set(hass, "open")
    await _set(hass, "closing")
    await _tick(hass, freezer, 300)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED

    await _set(hass, "closed")
    assert _state(hass).attributes["molight_state"] == STATE_COUNTDOWN
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_open_mode_recovering_in_another_open_state_is_no_opening(
    hass: HomeAssistant, freezer
) -> None:
    """A blip that comes back closing instead of open replays no opening."""
    hass.states.async_set(GARAGE, "closed")
    await setup_entries(hass, make_light_entry(door=GARAGE, door_mode=DOOR_MODE_OPEN))

    await _set(hass, "open")
    await _tick(hass, freezer, 61)
    assert _state(hass).state == "off"

    await _set(hass, "unavailable")
    await _set(hass, "closing")
    assert _state(hass).state == "off"


@pytest.mark.asyncio
async def test_open_close_recovering_in_another_open_state_keeps_holding(
    hass: HomeAssistant, freezer
) -> None:
    """A held-open cover back from a blip as closing still holds the light."""
    hass.states.async_set(GARAGE, "closed")
    await setup_entries(
        hass, make_light_entry(door=GARAGE, door_mode=DOOR_MODE_OPEN_CLOSE)
    )

    await _set(hass, "open")
    await _set(hass, "unavailable")
    await _set(hass, "closing")
    await _tick(hass, freezer, 300)
    assert _state(hass).state == "on"
    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
@pytest.mark.parametrize("moving", ["opening", "closing"])
async def test_open_close_seed_adopts_a_moving_cover(
    hass: HomeAssistant, freezer, moving: str
) -> None:
    """At startup a cover that is moving counts as open, like at runtime."""
    hass.states.async_set("light.real_1", "on")
    hass.states.async_set(GARAGE, moving)
    await setup_entries(
        hass, make_light_entry(door=GARAGE, door_mode=DOOR_MODE_OPEN_CLOSE)
    )

    assert _state(hass).attributes["molight_state"] == STATE_OCCUPIED
    await _tick(hass, freezer, 300)
    assert _state(hass).state == "on"
