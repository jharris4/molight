"""Tests for Virtual Light turn-on selections."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import SCHEDULE_MODE_FOLLOW
from tests.conftest import make_light_entry, settle, setup_entries

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant, ServiceCall


def _selection_entry(
    *,
    fixed_option: str | None = "Cozy",
    source_entity: str | None = None,
    **kwargs: Any,
):
    """Build a Virtual Light configured with a turn-on selection."""
    return make_light_entry(
        name="Selection Light",
        lights=["light.ambient"],
        turn_on_select_entity="select.ambient_theme",
        turn_on_select_option=fixed_option,
        turn_on_select_source_entity=source_entity,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_manual_turn_on_applies_selection_first_with_same_context(
    hass: HomeAssistant,
) -> None:
    """An off-to-on command selects the option before driving the light."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    await setup_entries(hass, _selection_entry())

    calls: list[tuple[str, str | list[str], str]] = []

    @callback
    def record_call(event: Event) -> None:
        data = event.data["service_data"]
        entity_id = data.get("entity_id")
        if event.data["domain"] == "select" or entity_id == ["light.ambient"]:
            calls.append((event.data["domain"], entity_id, event.context.id))

    hass.bus.async_listen(EVENT_CALL_SERVICE, record_call)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await hass.async_block_till_done()

    assert selected == ["Cozy"]
    assert [call[0] for call in calls] == ["select", "light"]
    assert calls[0][1] == "select.ambient_theme"
    assert calls[0][2] == calls[1][2]
    assert hass.states.get("light.selection_light").state == "on"


@pytest.mark.asyncio
async def test_selection_is_not_reapplied_while_already_on(
    hass: HomeAssistant,
) -> None:
    """Brightness/color adjustments while on do not reset the selection."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    await setup_entries(hass, _selection_entry())

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    # The member reports on, as a real bulb would after the command.
    hass.states.async_set("light.ambient", "on")
    await settle(hass)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.selection_light", "brightness": 100},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert selected == ["Cozy"]


@pytest.mark.asyncio
async def test_automatic_turn_on_applies_selection(hass: HomeAssistant) -> None:
    """Occupancy-driven turn-ons use the same selection path as manual ones."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("binary_sensor.occupancy", "off")
    await setup_entries(
        hass,
        _selection_entry(occupancy="binary_sensor.occupancy"),
    )

    hass.states.async_set("binary_sensor.occupancy", "on")
    await settle(hass)

    assert selected == ["Cozy"]
    assert hass.states.get("light.selection_light").state == "on"


@pytest.mark.asyncio
async def test_source_entity_is_resolved_at_each_turn_on(hass: HomeAssistant) -> None:
    """The source overrides the fallback and is read fresh for every on-cycle."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set(
        "select.ambient_theme",
        "Cozy",
        {"options": ["Cozy", "Game Night", "Holiday"]},
    )
    hass.states.async_set("input_select.desired_theme", "Game Night")
    await setup_entries(
        hass,
        _selection_entry(source_entity="input_select.desired_theme"),
    )

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await hass.async_block_till_done()

    assert selected == ["Game Night"]

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.selection_light"}, blocking=True
    )
    hass.states.async_set("input_select.desired_theme", "Holiday")
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await hass.async_block_till_done()

    assert selected == ["Game Night", "Holiday"]
    state = hass.states.get("light.selection_light")
    assert state.attributes["last_turn_on_selection_option"] == "Holiday"
    assert state.attributes["last_turn_on_selection_source"] == (
        "input_select.desired_theme"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_state",
    ["unavailable", "Not Offered"],
)
async def test_unusable_source_falls_back_to_fixed_option(
    hass: HomeAssistant, source_state: str
) -> None:
    """Unavailable and target-invalid source values use the fixed fallback."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set(
        "select.ambient_theme", "Cozy", {"options": ["Cozy", "Game Night"]}
    )
    hass.states.async_set("input_select.desired_theme", source_state)
    await setup_entries(
        hass,
        _selection_entry(source_entity="input_select.desired_theme"),
    )

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await hass.async_block_till_done()

    assert selected == ["Cozy"]
    state = hass.states.get("light.selection_light")
    assert state.attributes["last_turn_on_selection_option"] == "Cozy"
    assert state.attributes["last_turn_on_selection_source"] == "fixed"


@pytest.mark.asyncio
async def test_source_without_fallback_can_skip_selection(
    hass: HomeAssistant, caplog
) -> None:
    """An unavailable source without a fallback never blocks the light."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("input_select.desired_theme", "unavailable")
    await setup_entries(
        hass,
        _selection_entry(
            fixed_option=None,
            source_entity="input_select.desired_theme",
        ),
    )

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await hass.async_block_till_done()

    assert selected == []
    assert hass.states.get("light.selection_light").state == "on"
    assert "Unable to resolve a turn-on selection" in caplog.text


@pytest.mark.asyncio
async def test_physical_turn_on_does_not_apply_selection(hass: HomeAssistant) -> None:
    """An underlying member turned on externally retains its chosen state."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("light.ambient", "off")
    await setup_entries(hass, _selection_entry())

    hass.states.async_set("light.ambient", "on", {"brightness": 200})
    await settle(hass)

    assert selected == []
    assert hass.states.get("light.selection_light").state == "on"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_follow_member_reboot_reapplies_selection(hass: HomeAssistant) -> None:
    """A member rebooting lit mid-window gets the selection applied again.

    The strip booted into its own default preset; re-sending the window's
    settings includes the selection even though the member is already on.
    """
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("light.ambient", "off")
    hass.states.async_set("binary_sensor.sched", "on", {"current_window_start": "w1"})
    await setup_entries(
        hass,
        _selection_entry(
            schedule="binary_sensor.sched", schedule_mode=SCHEDULE_MODE_FOLLOW
        ),
    )
    await settle(hass)
    hass.states.async_set("light.ambient", "on")
    await settle(hass)
    assert selected == ["Cozy"]

    hass.states.async_set("light.ambient", "unavailable")
    await settle(hass)
    hass.states.async_set("light.ambient", "on")
    await settle(hass)

    assert selected == ["Cozy", "Cozy"]
    assert hass.states.get("light.selection_light").state == "on"


@pytest.mark.asyncio
async def test_selection_failure_does_not_prevent_turn_on(
    hass: HomeAssistant, caplog
) -> None:
    """A missing/renamed selection is non-fatal to primary light control."""

    async def select_option(_call: ServiceCall) -> None:
        raise HomeAssistantError

    hass.services.async_register("select", "select_option", select_option)
    await setup_entries(hass, _selection_entry())

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await hass.async_block_till_done()

    assert hass.states.get("light.selection_light").state == "on"
    assert "Unable to apply turn-on selection" in caplog.text


@pytest.mark.asyncio
async def test_selection_reapplied_when_turning_on_during_a_blink_off(
    hass: HomeAssistant, freezer
) -> None:
    """An effect stage blinking fully off leaves the virtual light logically on
    while the real lights are dark, so a turn-on there is still an off-to-on
    command for them and must re-apply the selection."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(effect_timeout=10, effect_brightness=0, warn_timeout=30),
    )

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await settle(hass)
    assert selected == ["Cozy"]

    # Run the timer out into the effect stage, which turns the real lights off.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.selection_light").state == "on"
    assert hass.states.get("light.ambient").state == "off"

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await settle(hass)

    assert selected == ["Cozy", "Cozy"]
