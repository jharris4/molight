"""Tests for Virtual Light turn-on selections."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.molight.const import (
    DOMAIN,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_SWITCH,
)
from tests.conftest import (
    make_light_entry,
    record_service_calls,
    settle,
    setup_entries,
)

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant, ServiceCall


@pytest.fixture(autouse=True)
def _ambient_theme(hass: HomeAssistant) -> None:
    """Give the selection target a state; tests that need options set their own."""
    hass.states.async_set("select.ambient_theme", "Normal")


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
@pytest.mark.parametrize("delay", [0, 5], ids=["settling", "unanswered_command"])
async def test_follow_member_reboot_reapplies_selection(
    hass: HomeAssistant, freezer, delay: int
) -> None:
    """A member rebooting lit mid-window gets the selection applied again.

    The strip booted into its own default preset; re-sending the window's
    settings includes the selection even though the member is already on,
    and even when coming back on looks like the late reply to a command.
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

    freezer.tick(timedelta(seconds=delay))
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
@pytest.mark.parametrize("target", ["missing", "unavailable"])
async def test_selection_skipped_when_target_is_missing_or_unavailable(
    hass: HomeAssistant, caplog, target: str
) -> None:
    """HA only logs a call to such a target, so MoLight skips it with a warning
    of its own and does not record the selection as applied."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    if target == "missing":
        hass.states.async_remove("select.ambient_theme")
    else:
        hass.states.async_set("select.ambient_theme", "unavailable")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(hass, _selection_entry())

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert state.state == "on"
    assert selected == []
    assert state.attributes["last_turn_on_selection_option"] is None
    assert state.attributes["last_turn_on_selection_source"] is None
    assert f"Turn-on selection target select.ambient_theme is {target}" in caplog.text


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


class _SlowSelect:
    """A select service that blocks until released."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        hass.services.async_register("select", "select_option", self._select)

    async def _select(self, _call: ServiceCall) -> None:
        self.started.set()
        await self.release.wait()


def _light_calls(hass: HomeAssistant) -> list[tuple[str, int | None]]:
    """Record (service, brightness) of each command to the real light."""
    calls: list[tuple[str, int | None]] = []

    @callback
    def record_call(event: Event) -> None:
        data = event.data["service_data"]
        if event.data["domain"] == "light" and data["entity_id"] == ["light.ambient"]:
            calls.append((event.data["service"], data.get("brightness")))

    hass.bus.async_listen(EVENT_CALL_SERVICE, record_call)
    return calls


async def _park_automatic_turn_on(hass: HomeAssistant, **kwargs: Any) -> _SlowSelect:
    """Occupancy lights the room; the turn-on waits for the select call."""
    select = _SlowSelect(hass)
    hass.states.async_set("binary_sensor.occ", "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(hass, _selection_entry(occupancy="binary_sensor.occ", **kwargs))
    hass.states.async_set("binary_sensor.occ", "on")
    await asyncio.wait_for(select.started.wait(), 2)
    state = hass.states.get("light.selection_light")
    assert state.attributes["molight_state"] == "occupied"
    return select


@pytest.mark.asyncio
async def test_manual_off_during_slow_selection_stands(hass: HomeAssistant) -> None:
    """A turn-on still waiting for its selection does not undo a later off."""
    select = await _park_automatic_turn_on(hass)
    calls = _light_calls(hass)

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.selection_light"}, blocking=True
    )
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_off", None)]
    assert state.state == "off"
    assert state.attributes["molight_state"] == "idle"


@pytest.mark.asyncio
async def test_bright_off_during_slow_selection_stands(hass: HomeAssistant) -> None:
    """An automatic off overtakes a waiting turn-on like a manual one."""
    hass.states.async_set("binary_sensor.illum", "off")
    select = await _park_automatic_turn_on(hass, illuminance="binary_sensor.illum")
    calls = _light_calls(hass)

    hass.states.async_set("binary_sensor.illum", "on")
    for _ in range(4):  # settle() would wait for the select call
        await asyncio.sleep(0)
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_off", None)]
    assert state.state == "off"
    assert state.attributes["molight_state"] == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("teardown", ["unload", "reload"])
async def test_unloading_during_slow_selection_drops_the_waiting_turn_on(
    hass: HomeAssistant, teardown: str
) -> None:
    """An entity that was unloaded no longer lights the room; its successor does."""
    select = await _park_automatic_turn_on(hass)
    calls = _light_calls(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    if teardown == "unload":
        assert await hass.config_entries.async_unload(entry.entry_id)
    else:
        assert await hass.config_entries.async_reload(entry.entry_id)
    select.release.set()
    await settle(hass)

    assert calls == ([] if teardown == "unload" else [("turn_on", None)])


@pytest.mark.asyncio
async def test_manual_on_then_off_during_slow_selection_stays_off(
    hass: HomeAssistant,
) -> None:
    """An overtaken manual turn-on neither lights the room nor starts a timer."""
    select = _SlowSelect(hass)
    hass.states.async_set("light.ambient", "off")
    await setup_entries(hass, _selection_entry())
    calls = _light_calls(hass)

    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.selection_light"}, blocking=True
    )
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_off", None)]
    assert state.state == "off"
    assert state.attributes["molight_state"] == "idle"


@pytest.mark.asyncio
async def test_newer_turn_on_during_slow_selection_replaces_the_waiting_one(
    hass: HomeAssistant,
) -> None:
    """Of two turn-ons waiting for the selection, only the newer is sent."""
    select = await _park_automatic_turn_on(hass, auto_on_brightness=40)
    calls = _light_calls(hass)

    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 200},
            blocking=True,
        )
    )
    await asyncio.sleep(0)
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 200)]
    assert state.state == "on"
    assert state.attributes["brightness"] == 200


@pytest.mark.asyncio
async def test_manual_on_during_blink_off_survives_the_stage_ending(
    hass: HomeAssistant, freezer
) -> None:
    """The effect timer reaching the warn stage while a turn-on waits for its
    selection does not drop the turn-on: the user's command is sent after the
    stage's and the light ends up ACTIVE at the user's brightness."""
    select = _SlowSelect(hass)
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(effect_timeout=10, effect_brightness=0, warn_timeout=30),
    )
    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    select.release.set()
    await turn_on
    await settle(hass)
    select.release.clear()
    select.started.clear()

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    hass.states.async_set("light.ambient", "off")
    await settle(hass)
    assert hass.states.get("light.selection_light").attributes["molight_state"] == (
        "effect"
    )
    calls = _light_calls(hass)

    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 200},
            blocking=True,
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    for _ in range(4):  # settle() would wait for the select call
        await asyncio.sleep(0)
    assert hass.states.get("light.selection_light").attributes["molight_state"] == (
        "warn"
    )
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 255), ("turn_on", 200)]
    assert state.state == "on"
    assert state.attributes["molight_state"] == "active"
    assert state.attributes["brightness"] == 200

    # The stage's timer is gone: nothing turns the light off before the new
    # timeout runs out.
    freezer.tick(timedelta(seconds=59))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.selection_light").state == "on"
    assert calls == [("turn_on", 255), ("turn_on", 200)]


@pytest.mark.asyncio
async def test_occupancy_on_during_a_waiting_manual_on_defers_to_it(
    hass: HomeAssistant, freezer
) -> None:
    """An automatic turn-on arriving while a manual one waits is dropped: the
    user's brightness is kept and the on-period is not counted as lit by
    occupancy."""
    select = _SlowSelect(hass)
    hass.states.async_set("binary_sensor.occ", "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(
            occupancy="binary_sensor.occ", auto_on_brightness=40, false_off_delay=5
        ),
    )
    calls = _light_calls(hass)

    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 200},
            blocking=True,
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    hass.states.async_set("binary_sensor.occ", "on")
    for _ in range(4):  # settle() would wait for the select call
        await asyncio.sleep(0)
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 200)]
    assert state.state == "on"
    assert state.attributes["molight_state"] == "occupied"
    assert state.attributes["brightness"] == 200

    # A quick clear flagged false gets the normal countdown, not the quick
    # off reserved for lights that occupancy lit.
    freezer.tick(timedelta(seconds=2))
    hass.states.async_set(
        "binary_sensor.occ", "off", {"last_clear_false_detection": True}
    )
    await settle(hass)
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.selection_light").state == "on"
    assert calls == [("turn_on", 200)]


async def _drain(hass: HomeAssistant) -> None:
    """Let dispatches run; settle() would wait for the select call."""
    for _ in range(4):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["occupancy", "door"])
async def test_wall_turn_on_during_slow_selection_keeps_its_level(
    hass: HomeAssistant, freezer, trigger: str
) -> None:
    """A turn-on at the wall overtakes an automatic one still waiting for its
    selection: the light stays at the level it was turned on at, and the
    on-period is the user's."""
    select = _SlowSelect(hass)
    sensor = f"binary_sensor.{trigger}"
    hass.states.async_set(sensor, "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass, _selection_entry(auto_on_brightness=40, **{trigger: sensor})
    )
    calls = _light_calls(hass)
    hass.states.async_set(sensor, "on")
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set("light.ambient", "on", {"brightness": 200})
    await _drain(hass)
    state = hass.states.get("light.selection_light")
    assert state.state == "on"
    assert state.attributes["brightness"] == 200
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == []
    assert state.state == "on"
    assert state.attributes["brightness"] == 200
    assert state.attributes["last_on_physical"] is not None
    if trigger == "door":
        return

    # A clear flagged false gets the normal countdown, not the quick off.
    freezer.tick(timedelta(seconds=2))
    hass.states.async_set(sensor, "off", {"last_clear_false_detection": True})
    await settle(hass)
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.selection_light").state == "on"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_wall_turn_on_during_a_waiting_follow_window_start_stands(
    hass: HomeAssistant,
) -> None:
    """The window still owns a light turned on at the wall while its start
    waited for the selection, at the level it was turned on at."""
    select = _SlowSelect(hass)
    hass.states.async_set("binary_sensor.sched", "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(
            schedule="binary_sensor.sched",
            schedule_mode=SCHEDULE_MODE_FOLLOW,
            auto_on_brightness=40,
        ),
    )
    calls = _light_calls(hass)
    hass.states.async_set("binary_sensor.sched", "on", {"current_window_start": "w1"})
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set("light.ambient", "on", {"brightness": 200})
    await _drain(hass)
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == []
    assert state.state == "on"
    assert state.attributes["molight_state"] == "scheduled"
    assert state.attributes["brightness"] == 200


async def _begin_manual_turn_on(hass: HomeAssistant, select: _SlowSelect):
    """Turn the virtual light on at 200; return once the select call waits."""
    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 200},
            blocking=True,
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    return turn_on


@pytest.mark.asyncio
@pytest.mark.parametrize("wall", ["turn_on", "dim_from_0"])
async def test_wall_change_during_a_waiting_manual_on_keeps_its_level(
    hass: HomeAssistant, freezer, wall: str
) -> None:
    """A turn-on or dim at the wall is the later action: it overtakes a manual
    turn-on still waiting for its selection, as it does an automatic one."""
    select = _SlowSelect(hass)
    dark = ("on", {"brightness": 0}) if wall == "dim_from_0" else ("off", {})
    hass.states.async_set("light.ambient", *dark)
    await setup_entries(hass, _selection_entry())
    calls = _light_calls(hass)
    turn_on = await _begin_manual_turn_on(hass, select)

    hass.states.async_set("light.ambient", "on", {"brightness": 100})
    await _drain(hass)
    state = hass.states.get("light.selection_light")
    assert state.state == "on"
    assert state.attributes["brightness"] == 100
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == []
    assert state.state == "on"
    assert state.attributes["brightness"] == 100
    assert state.attributes["molight_state"] == "active"
    assert state.attributes["last_on_physical"] is not None
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert calls == [("turn_off", None)]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_wall_turn_on_during_a_waiting_manual_on_rejoins_the_window(
    hass: HomeAssistant,
) -> None:
    """Inside a follow window the wall turn-on that overtook the manual one
    rejoins the window, at the level set at the wall."""
    select = _SlowSelect(hass)
    select.release.set()  # the window's own start goes straight through
    hass.states.async_set("binary_sensor.sched", "on", {"current_window_start": "w1"})
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(
            schedule="binary_sensor.sched", schedule_mode=SCHEDULE_MODE_FOLLOW
        ),
    )
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.selection_light"}, blocking=True
    )
    select.started.clear()
    select.release.clear()
    calls = _light_calls(hass)
    turn_on = await _begin_manual_turn_on(hass, select)

    hass.states.async_set("light.ambient", "on", {"brightness": 100})
    await _drain(hass)
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == []
    assert state.attributes["molight_state"] == "scheduled"
    assert state.attributes["brightness"] == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("member", ["unavailable", "restored"])
async def test_member_reporting_in_lit_during_a_waiting_manual_on_is_no_wall_change(
    hass: HomeAssistant, member: str
) -> None:
    """A real light that loads already lit while a manual turn-on waits was
    not turned on at the wall: the user's turn-on is still sent."""
    select = _SlowSelect(hass)
    calls = _light_calls(hass)
    turn_on = await _begin_turn_on(hass, select, "manual", member=member)

    hass.states.async_set("light.ambient", "on", {"brightness": 77})
    await _drain(hass)
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 200)]
    assert state.state == "on"
    assert state.attributes["brightness"] == 200
    assert state.attributes["molight_state"] == "active"
    assert state.attributes["last_on_physical"] is None


@pytest.mark.asyncio
async def test_member_lit_by_the_selection_itself_is_not_a_wall_turn_on(
    hass: HomeAssistant, freezer
) -> None:
    """A select call that lights the real light itself (a preset, a scene
    script) does not overtake the turn-on it belongs to, and the on-period
    stays occupancy's."""

    async def select_option(call: ServiceCall) -> None:
        hass.states.async_set(
            "light.ambient", "on", {"brightness": 77}, context=call.context
        )

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("binary_sensor.occ", "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(
            occupancy="binary_sensor.occ", auto_on_brightness=40, false_off_delay=5
        ),
    )
    calls = _light_calls(hass)

    hass.states.async_set("binary_sensor.occ", "on")
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 102)]
    assert state.state == "on"
    assert state.attributes["brightness"] == 102
    assert state.attributes["last_on_physical"] is None

    freezer.tick(timedelta(seconds=2))
    hass.states.async_set(
        "binary_sensor.occ", "off", {"last_clear_false_detection": True}
    )
    await settle(hass)
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.selection_light").state == "off"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_switch_window_end_recalculates_a_waiting_turn_on(
    hass: HomeAssistant,
) -> None:
    """A gate_switch window ending judges a waiting turn-on like an on light."""
    select = _SlowSelect(hass)
    visit = (datetime.now(UTC) - timedelta(hours=4)).isoformat()
    hass.states.async_set("binary_sensor.occ", "off", {"latest_occupied_time": visit})
    hass.states.async_set("binary_sensor.window", "on")
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(
            occupancy="binary_sensor.occ",
            door="binary_sensor.door",
            schedule="binary_sensor.window",
            schedule_mode=SCHEDULE_MODE_GATE_SWITCH,
        ),
    )
    calls = _light_calls(hass)
    hass.states.async_set("binary_sensor.door", "on")
    await asyncio.wait_for(select.started.wait(), 2)
    state = hass.states.get("light.selection_light")
    assert state.attributes["molight_state"] == "active"

    hass.states.async_set("binary_sensor.window", "off")
    for _ in range(4):  # settle() would wait for the select call
        await asyncio.sleep(0)
    select.release.set()
    await settle(hass)

    # The last visit is long past its timeout: off, and never lit.
    state = hass.states.get("light.selection_light")
    assert calls == [("turn_off", None)]
    assert state.state == "off"
    assert state.attributes["molight_state"] == "idle"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("mode", [SCHEDULE_MODE_GATE, SCHEDULE_MODE_GATE_SWITCH])
async def test_window_end_takes_over_a_waiting_manual_turn_on(
    hass: HomeAssistant, mode: str
) -> None:
    """A gate window ending judges a manual turn-on not sent yet as an on
    light: turn-off turns it off, switch-state finds the room long empty."""
    select = _SlowSelect(hass)
    visit = (datetime.now(UTC) - timedelta(hours=4)).isoformat()
    hass.states.async_set("binary_sensor.occ", "off", {"latest_occupied_time": visit})
    hass.states.async_set("binary_sensor.window", "on")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(
            occupancy="binary_sensor.occ",
            schedule="binary_sensor.window",
            schedule_mode=mode,
        ),
    )
    calls = _light_calls(hass)

    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    hass.states.async_set("binary_sensor.window", "off")
    await _drain(hass)
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_off", None)]
    assert state.state == "off"
    assert state.attributes["molight_state"] == "idle"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_held_follow_window_end_keeps_a_waiting_manual_turn_on(
    hass: HomeAssistant,
) -> None:
    """A hold keeps a follow window's end for a manual turn-on not sent yet,
    as it does for an on light: the off applies when the hold releases."""
    select = _SlowSelect(hass)
    select.release.set()  # the window's own turn-on at startup goes through
    hass.states.async_set("binary_sensor.sched", "on", {"current_window_start": "w1"})
    hass.states.async_set("input_boolean.hold", "on")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(
            schedule="binary_sensor.sched",
            schedule_mode=SCHEDULE_MODE_FOLLOW,
            hold_entities=["input_boolean.hold"],
        ),
    )
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.selection_light"}, blocking=True
    )
    await settle(hass)
    select.release.clear()
    select.started.clear()
    calls = _light_calls(hass)

    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    hass.states.async_set("binary_sensor.sched", "off")
    await _drain(hass)
    select.release.set()
    await turn_on
    await settle(hass)
    assert calls == [("turn_on", None)]
    assert hass.states.get("light.selection_light").state == "on"

    hass.states.async_set("input_boolean.hold", "off")
    await settle(hass)
    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", None), ("turn_off", None)]
    assert state.state == "off"
    assert state.attributes["molight_state"] == "idle"


@pytest.mark.asyncio
async def test_occupancy_after_an_overtaken_manual_on_still_lights_the_room(
    hass: HomeAssistant,
) -> None:
    """An automatic turn-on only defers to a manual one that still stands:
    one turned off again while it waited no longer lights the room."""
    select = _SlowSelect(hass)
    hass.states.async_set("binary_sensor.occ", "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass, _selection_entry(occupancy="binary_sensor.occ", auto_on_brightness=40)
    )
    calls = _light_calls(hass)

    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 200},
            blocking=True,
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.selection_light"}, blocking=True
    )
    hass.states.async_set("binary_sensor.occ", "on")
    await _drain(hass)
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_off", None), ("turn_on", 102)]
    assert state.state == "on"
    assert state.attributes["molight_state"] == "occupied"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["door", "occupancy"])
async def test_hold_released_during_slow_selection_still_starts_the_timeout(
    hass: HomeAssistant, freezer, trigger: str
) -> None:
    """A hold released while a turn-on waits for its selection leaves the
    light its normal timeout, not on with no timer."""
    select = _SlowSelect(hass)
    sensor = f"binary_sensor.{trigger}"
    hass.states.async_set(sensor, "off")
    hass.states.async_set("input_boolean.hold", "on")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(hold_entities=["input_boolean.hold"], **{trigger: sensor}),
    )
    calls = _light_calls(hass)

    hass.states.async_set(sensor, "on")
    await asyncio.wait_for(select.started.wait(), 2)
    if trigger == "occupancy":
        # They left again before the light came on: a countdown, held.
        hass.states.async_set(sensor, "off")
        await _drain(hass)
    hass.states.async_set("input_boolean.hold", "off")
    await _drain(hass)
    select.release.set()
    await settle(hass)
    assert calls == [("turn_on", None)]
    assert hass.states.get("light.selection_light").state == "on"

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert calls == [("turn_on", None), ("turn_off", None)]
    assert hass.states.get("light.selection_light").state == "off"


@pytest.mark.asyncio
async def test_uninterrupted_slow_selection_still_turns_on(
    hass: HomeAssistant,
) -> None:
    """With nothing in between, the waiting turn-on is sent."""
    select = await _park_automatic_turn_on(hass, auto_on_brightness=40)
    calls = _light_calls(hass)

    select.release.set()
    await settle(hass)

    assert calls == [("turn_on", 102)]
    assert hass.states.get("light.selection_light").state == "on"


_LEVEL = {"manual": 200, "occupancy": 102, "door": 102}


class _FailingSelect(_SlowSelect):
    """A select service that blocks until released, then fails."""

    def __init__(self, hass: HomeAssistant, error: Exception) -> None:
        super().__init__(hass)
        self._error = error

    async def _select(self, call: ServiceCall) -> None:
        await super()._select(call)
        raise self._error


async def _begin_turn_on(
    hass: HomeAssistant,
    select: _SlowSelect,
    trigger: str,
    *,
    member: str | None = "off",
) -> asyncio.Task | None:
    """Set up, start a turn-on and return once it waits for the select call.

    member is the real light's state meanwhile: None for not loaded yet,
    "restored" for the placeholder Home Assistant writes at boot.
    """
    hass.states.async_set("binary_sensor.occ", "off")
    hass.states.async_set("binary_sensor.door", "off")
    if member == "restored":
        hass.states.async_set("light.ambient", "unavailable", {"restored": True})
    elif member:
        hass.states.async_set("light.ambient", member)
    sensors = {"door": "binary_sensor.door"} if trigger == "door" else {}
    await setup_entries(
        hass,
        _selection_entry(
            occupancy="binary_sensor.occ", auto_on_brightness=40, **sensors
        ),
    )
    task = None
    if trigger in ("occupancy", "door"):
        hass.states.async_set(
            "binary_sensor.occ" if trigger == "occupancy" else "binary_sensor.door",
            "on",
        )
    else:
        task = hass.async_create_task(
            hass.services.async_call(
                "light",
                "toggle" if trigger == "toggle" else "turn_on",
                {"entity_id": "light.selection_light", "brightness": 200},
                blocking=True,
            )
        )
    await asyncio.wait_for(select.started.wait(), 2)
    return task


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "occupancy"])
async def test_waiting_turn_on_reports_on_at_its_level(
    hass: HomeAssistant, trigger: str
) -> None:
    """The virtual light is on, at the level asked for, from the moment it
    accepts a turn-on, not only once the select call has finished."""
    select = _SlowSelect(hass)
    calls = _light_calls(hass)
    turn_on = await _begin_turn_on(hass, select, trigger)

    state = hass.states.get("light.selection_light")
    assert state.state == "on"
    assert state.attributes["brightness"] == _LEVEL[trigger]
    assert calls == []
    select.release.set()
    if turn_on:
        await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", _LEVEL[trigger])]
    assert state.state == "on"
    assert state.attributes["brightness"] == _LEVEL[trigger]


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "occupancy"])
@pytest.mark.parametrize(
    "error",
    [HomeAssistantError("device offline"), RuntimeError("boom")],
    ids=["ha_error", "unexpected_error"],
)
async def test_waiting_turn_on_whose_selection_fails_still_lights_the_room(
    hass: HomeAssistant, caplog, trigger: str, error: Exception
) -> None:
    """A select call that fails, however it fails, leaves the light reporting
    on with the real light turned on."""
    select = _FailingSelect(hass, error)
    calls = _light_calls(hass)
    turn_on = await _begin_turn_on(hass, select, trigger)
    assert hass.states.get("light.selection_light").state == "on"

    select.release.set()
    if turn_on:
        await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", _LEVEL[trigger])]
    assert state.state == "on"
    assert state.attributes["brightness"] == _LEVEL[trigger]
    assert "Unable to apply turn-on selection" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "occupancy"])
async def test_waiting_turn_on_cancelled_by_an_off_reports_off(
    hass: HomeAssistant, trigger: str
) -> None:
    """A newer off takes back the on that the waiting turn-on reported."""
    select = _SlowSelect(hass)
    calls = _light_calls(hass)
    turn_on = await _begin_turn_on(hass, select, trigger)
    assert hass.states.get("light.selection_light").state == "on"

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.selection_light"}, blocking=True
    )
    assert hass.states.get("light.selection_light").state == "off"
    select.release.set()
    if turn_on:
        await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_off", None)]
    assert state.state == "off"
    assert state.attributes["molight_state"] == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["toggle", "occupancy"])
async def test_toggle_during_the_wait_turns_the_light_off(
    hass: HomeAssistant, trigger: str
) -> None:
    """A toggle while a turn-on waits sees an on light: two quick toggles end
    off, as does a toggle right after motion lit the room."""
    select = _SlowSelect(hass)
    calls = _light_calls(hass)
    turn_on = await _begin_turn_on(hass, select, trigger)

    toggle = hass.async_create_task(
        hass.services.async_call(
            "light", "toggle", {"entity_id": "light.selection_light"}, blocking=True
        )
    )
    await _drain(hass)
    assert hass.states.get("light.selection_light").state == "off"
    select.release.set()
    await toggle
    if turn_on:
        await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_off", None)]
    assert state.state == "off"
    assert state.attributes["molight_state"] == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "occupancy"])
async def test_member_reporting_off_after_a_waited_turn_on_turns_the_light_off(
    hass: HomeAssistant, trigger: str
) -> None:
    """Once the command is sent, the real light's own reports decide again."""
    select = _SlowSelect(hass)
    turn_on = await _begin_turn_on(hass, select, trigger)
    select.release.set()
    if turn_on:
        await turn_on
    await settle(hass)
    hass.states.async_set("light.ambient", "on", {"brightness": _LEVEL[trigger]})
    await settle(hass)
    assert hass.states.get("light.selection_light").state == "on"

    hass.states.async_set("light.ambient", "off")
    await settle(hass)
    state = hass.states.get("light.selection_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == "idle"


_NOT_LOADED = [None, "unavailable", "unknown", "restored"]


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "occupancy", "door"])
@pytest.mark.parametrize("member", _NOT_LOADED)
@pytest.mark.parametrize("report", [{}, {"brightness": 0}], ids=["off", "on_at_0"])
async def test_member_reporting_in_off_during_the_wait_gets_the_turn_on(
    hass: HomeAssistant, trigger: str, member: str | None, report: dict
) -> None:
    """A real light that loads, or comes back, as off while a turn-on waits
    for its selection was not turned off: the turn-on is still sent, and no
    manual off is recorded."""
    select = _SlowSelect(hass)
    calls = _light_calls(hass)
    turn_on = await _begin_turn_on(hass, select, trigger, member=member)

    hass.states.async_set("light.ambient", "on" if report else "off", report)
    await _drain(hass)
    assert hass.states.get("light.selection_light").state == "on"
    select.release.set()
    if turn_on:
        await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", _LEVEL[trigger])]
    assert state.state == "on"
    assert state.attributes["brightness"] == _LEVEL[trigger]
    assert state.attributes["molight_state"] == (
        "occupied" if trigger == "occupancy" else "active"
    )
    assert state.attributes["last_off_manual"] is None


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("member", _NOT_LOADED)
async def test_member_reporting_in_off_during_a_waiting_window_start_gets_it(
    hass: HomeAssistant, member: str | None
) -> None:
    """A follow window start still waiting for its selection is not ended by
    a real light that loads as off meanwhile."""
    select = _SlowSelect(hass)
    hass.states.async_set("binary_sensor.sched", "off")
    if member:
        placeholder = {"restored": True} if member == "restored" else {}
        hass.states.async_set(
            "light.ambient",
            "unavailable" if member == "restored" else member,
            placeholder,
        )
    await setup_entries(
        hass,
        _selection_entry(
            schedule="binary_sensor.sched",
            schedule_mode=SCHEDULE_MODE_FOLLOW,
            auto_on_brightness=40,
        ),
    )
    calls = _light_calls(hass)
    hass.states.async_set("binary_sensor.sched", "on", {"current_window_start": "w1"})
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set("light.ambient", "off")
    await _drain(hass)
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls
    assert set(calls) == {("turn_on", 102)}
    assert state.state == "on"
    assert state.attributes["molight_state"] == "scheduled"
    assert state.attributes["last_off_manual"] is None


def _one_device(hass: HomeAssistant, *entity_ids: str) -> None:
    """Register the entities as one device's, like a WLED light and its presets."""
    entry = MockConfigEntry(domain="wled")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("wled", entity_ids[0])}
    )
    for entity_id in entity_ids:
        domain, object_id = entity_id.split(".")
        # A state already there would make the registry pick another id.
        state = hass.states.get(entity_id)
        hass.states.async_remove(entity_id)
        registered = er.async_get(hass).async_get_or_create(
            domain,
            "wled",
            entity_id,
            suggested_object_id=object_id,
            device_id=device.id,
        )
        assert registered.entity_id == entity_id
        if state:
            hass.states.async_set(entity_id, state.state, state.attributes)


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "occupancy", "door"])
async def test_device_push_from_the_selection_is_not_a_wall_turn_on(
    hass: HomeAssistant, freezer, trigger: str
) -> None:
    """A preset that lights its own device's light reports that by a push of
    the device's, not under the select call: the waiting turn-on is still
    sent, and the on-period stays with whoever started it."""
    _one_device(hass, "select.ambient_theme", "light.ambient")
    select = _SlowSelect(hass)
    calls = _light_calls(hass)
    turn_on = await _begin_turn_on(hass, select, trigger)

    # The preset turns the light on, then sets its level.
    for brightness in (77, 90):
        hass.states.async_set("light.ambient", "on", {"brightness": brightness})
        await _drain(hass)
    assert calls == []
    select.release.set()
    if turn_on:
        await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", _LEVEL[trigger])]
    assert state.state == "on"
    assert state.attributes["brightness"] == _LEVEL[trigger]
    assert state.attributes["last_on_physical"] is None
    assert state.attributes["last_brightness_change_physical"] is None
    if trigger != "occupancy":
        return

    # Still lit by occupancy: a false detection gets the quick off.
    freezer.tick(timedelta(seconds=2))
    hass.states.async_set(
        "binary_sensor.occ", "off", {"last_clear_false_detection": True}
    )
    await settle(hass)
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.selection_light").state == "off"


@pytest.mark.asyncio
async def test_device_push_after_the_turn_on_was_cancelled_is_adopted(
    hass: HomeAssistant, freezer
) -> None:
    """A preset that still lights the light after an off cancelled the turn-on
    leaves a lit room: the light reports it and turns it off on its timeout."""
    _one_device(hass, "select.ambient_theme", "light.ambient")
    select = _SlowSelect(hass)
    turn_on = await _begin_turn_on(hass, select, "manual")
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.selection_light"}, blocking=True
    )
    calls = _light_calls(hass)

    hass.states.async_set("light.ambient", "on", {"brightness": 77})
    await _drain(hass)
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == []
    assert state.state == "on"
    assert state.attributes["molight_state"] == "active"
    assert state.attributes["brightness"] == 77
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert calls == [("turn_off", None)]


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_device_push_from_the_selection_does_not_drop_a_window_start(
    hass: HomeAssistant,
) -> None:
    """A follow window start whose preset lights the light still sends its
    own level."""
    _one_device(hass, "select.ambient_theme", "light.ambient")
    select = _SlowSelect(hass)
    hass.states.async_set("binary_sensor.sched", "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(
            schedule="binary_sensor.sched",
            schedule_mode=SCHEDULE_MODE_FOLLOW,
            auto_on_brightness=40,
        ),
    )
    calls = _light_calls(hass)
    hass.states.async_set("binary_sensor.sched", "on", {"current_window_start": "w1"})
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set("light.ambient", "on", {"brightness": 77})
    await _drain(hass)
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 102)]
    assert state.attributes["molight_state"] == "scheduled"
    assert state.attributes["brightness"] == 102
    assert state.attributes["last_on_physical"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["another_device", "select_without_device"])
async def test_wall_turn_on_of_a_light_on_another_device_still_overtakes(
    hass: HomeAssistant, layout: str
) -> None:
    """Only the select target's own device speaks for the selection."""
    if layout == "another_device":
        _one_device(hass, "select.ambient_theme")
    _one_device(hass, "light.ambient")
    select = _SlowSelect(hass)
    hass.states.async_set("binary_sensor.occ", "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass, _selection_entry(occupancy="binary_sensor.occ", auto_on_brightness=40)
    )
    calls = _light_calls(hass)
    hass.states.async_set("binary_sensor.occ", "on")
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set("light.ambient", "on", {"brightness": 200})
    await _drain(hass)
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == []
    assert state.attributes["brightness"] == 200
    assert state.attributes["last_on_physical"] is not None


@pytest.mark.asyncio
async def test_wall_turn_on_of_another_member_during_a_device_selection_overtakes(
    hass: HomeAssistant,
) -> None:
    """A second real light, not on the select target's device, turned on at
    the wall during the wait is still a change at the wall."""
    _one_device(hass, "select.ambient_theme", "light.ambient")
    select = _SlowSelect(hass)
    for entity_id in ("binary_sensor.occ", "light.ambient", "light.lamp"):
        hass.states.async_set(entity_id, "off")
    await setup_entries(
        hass,
        make_light_entry(
            name="Selection Light",
            lights=["light.ambient", "light.lamp"],
            turn_on_select_entity="select.ambient_theme",
            turn_on_select_option="Cozy",
            occupancy="binary_sensor.occ",
            auto_on_brightness=40,
        ),
    )
    calls = record_service_calls(hass)
    hass.states.async_set("binary_sensor.occ", "on")
    await asyncio.wait_for(select.started.wait(), 2)

    hass.states.async_set("light.lamp", "on", {"brightness": 200})
    await _drain(hass)
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert [call for call in calls if call["domain"] == "light"] == []
    assert state.attributes["brightness"] == 200
    assert state.attributes["last_on_physical"] is not None
