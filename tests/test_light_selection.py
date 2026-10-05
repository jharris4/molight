"""Tests for Virtual Light turn-on selections."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, CoreState, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.molight.const import (
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_LIGHT_TIMEOUT,
    CONF_OCCUPANCY_ENTITY,
    CONF_STANDBY_BRIGHTNESS,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_TIMEOUT,
    DOMAIN,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_SWITCH,
)
from tests.conftest import (
    finish_startup,
    light_targets,
    make_light_entry,
    make_scheduled_light_entry,
    record_service_calls,
    settle,
    setup_entries,
)
from tests.real_entities import FadingLight, RealSelect, add_real

pytestmark = pytest.mark.usefixtures("virtual_light_behavior_variant")

if TYPE_CHECKING:
    from homeassistant.auth.models import User
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
@pytest.mark.allow_warning_log
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
@pytest.mark.allow_warning_log
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
@pytest.mark.allow_warning_log
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
@pytest.mark.parametrize("trigger", ["manual", "occupancy", "door", "resend"])
async def test_selection_applied_when_target_has_no_current_option(
    hass: HomeAssistant, caplog, trigger: str
) -> None:
    """A select with no current option (a WLED preset select after a change
    in the WLED app) is available and still gets the option."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("select.ambient_theme", "unknown", {"options": ["Cozy"]})
    hass.states.async_set("binary_sensor.occ", "off")
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(occupancy="binary_sensor.occ", door="binary_sensor.door"),
    )

    if trigger in ("manual", "resend"):
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
        )
    else:
        sensor = "binary_sensor.occ" if trigger == "occupancy" else "binary_sensor.door"
        hass.states.async_set(sensor, "on")
    await settle(hass)
    if trigger == "resend":
        hass.states.async_set("light.ambient", "on")
        await settle(hass)
        hass.states.async_set("light.ambient", "unavailable")
        await settle(hass)
        hass.states.async_set("light.ambient", "off")
        await settle(hass)

    state = hass.states.get("light.selection_light")
    assert state.state == "on"
    assert selected == ["Cozy"] * (2 if trigger == "resend" else 1)
    assert state.attributes["last_turn_on_selection_option"] == "Cozy"
    assert state.attributes["last_turn_on_selection_source"] == "fixed"
    assert "Turn-on selection target" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "fixed", "source", "sent"),
    [
        ([" Cozy ", "Cozy"], " Cozy ", None, " Cozy "),
        ([" Cozy ", "Cozy"], "Cozy", None, "Cozy"),
        (["Cozy"], "  Cozy  ", None, "Cozy"),
        (["Cozy "], "Cozy", None, "Cozy "),
        ([" Cozy ", " Cozy"], "Cozy", None, None),
        (["Cozy ", "Night"], "Night", "Cozy", "Cozy "),
    ],
    ids=[
        "padded",
        "plain_beside_padded",
        "typed_padding",
        "stored_without_padding",
        "names_two_options",
        "source_without_padding",
    ],
)
@pytest.mark.allow_warning_log
async def test_selection_prefers_the_exact_option(
    hass: HomeAssistant,
    options: list[str],
    fixed: str,
    source: str | None,
    sent: str | None,
) -> None:
    """The option sent is the one the target offers, padding and all.

    A value stored as typed while the target was unavailable, or stripped
    before options kept their padding, still finds its single option.
    """
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("select.ambient_theme", options[0], {"options": options})
    if source is not None:
        hass.states.async_set("input_select.desired_theme", source)
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        _selection_entry(
            fixed_option=fixed,
            source_entity="input_select.desired_theme" if source else None,
        ),
    )

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert state.state == "on"
    assert selected == ([sent] if sent is not None else [])
    assert state.attributes["last_turn_on_selection_option"] == sent


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_window_start_applies_selection_to_a_target_with_no_current_option(
    hass: HomeAssistant,
) -> None:
    """A follow window start applies the option to an unknown select too."""
    selected: list[str] = []

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("select.ambient_theme", "unknown", {"options": ["Cozy"]})
    hass.states.async_set("light.ambient", "off")
    hass.states.async_set("binary_sensor.sched", "off")
    await setup_entries(
        hass,
        _selection_entry(
            schedule="binary_sensor.sched", schedule_mode=SCHEDULE_MODE_FOLLOW
        ),
    )

    hass.states.async_set("binary_sensor.sched", "on", {"current_window_start": "w1"})
    await settle(hass)

    assert selected == ["Cozy"]
    assert hass.states.get("light.selection_light").state == "on"


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
        _selection_entry(effect_timeout=5, effect_brightness=0, warn_timeout=30),
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
    freezer.tick(timedelta(seconds=6))  # short of the select call's time limit
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
@pytest.mark.allow_warning_log
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


class _HangingSelect(_SlowSelect):
    """A select service that never answers, and records being given up on."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        self.cancelled = 0

    async def _select(self, call: ServiceCall) -> None:
        try:
            await super()._select(call)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


async def _pass(hass: HomeAssistant, freezer, seconds: float) -> None:
    """Let time pass while a select call may be parked."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await _drain(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "occupancy", "door"])
@pytest.mark.allow_warning_log
async def test_select_call_that_never_answers_still_lights_the_room(
    hass: HomeAssistant, freezer, caplog, trigger: str
) -> None:
    """A turn-on waits for its select call for ten seconds, then gives up on
    it and lights the room without the selection."""
    select = _HangingSelect(hass)
    calls = _light_calls(hass)
    turn_on = await _begin_turn_on(hass, select, trigger)

    await _pass(hass, freezer, 9)
    assert calls == []
    assert select.cancelled == 0
    assert hass.states.get("light.selection_light").state == "on"

    await _pass(hass, freezer, 2)
    if turn_on:
        await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", _LEVEL[trigger])]
    assert select.cancelled == 1
    assert state.state == "on"
    assert state.attributes["brightness"] == _LEVEL[trigger]
    assert state.attributes["molight_state"] == (
        "occupied" if trigger == "occupancy" else "active"
    )
    assert state.attributes["last_turn_on_selection_option"] is None
    assert "Unable to apply turn-on selection" in caplog.text
    assert "TimeoutError" in caplog.text


@pytest.mark.asyncio
async def test_select_call_answering_within_the_limit_is_applied(
    hass: HomeAssistant, freezer, caplog
) -> None:
    """Nine seconds is slow, not stuck: the selection counts as applied."""
    select = _HangingSelect(hass)
    calls = _light_calls(hass)
    turn_on = await _begin_turn_on(hass, select, "manual")

    await _pass(hass, freezer, 9)
    select.release.set()
    await turn_on
    await settle(hass)
    await _pass(hass, freezer, 2)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 200)]
    assert select.cancelled == 0
    assert state.attributes["last_turn_on_selection_option"] == "Cozy"
    assert "Unable to apply turn-on selection" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("overtaker", ["off", "wall"])
@pytest.mark.allow_warning_log
async def test_select_call_given_up_on_after_an_off_lights_nothing(
    hass: HomeAssistant, freezer, overtaker: str
) -> None:
    """Giving up on the select call does not revive a turn-on that an off or
    a change at the wall overtook while it waited."""
    select = _HangingSelect(hass)
    turn_on = await _begin_turn_on(hass, select, "manual")
    if overtaker == "off":
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": "light.selection_light"}, blocking=True
        )
    else:
        hass.states.async_set("light.ambient", "on", {"brightness": 77})
        await _drain(hass)
    calls = _light_calls(hass)

    await _pass(hass, freezer, 11)
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == []
    assert select.cancelled == 1
    assert state.state == ("off" if overtaker == "off" else "on")
    if overtaker == "wall":
        assert state.attributes["brightness"] == 77


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_resend_whose_select_call_never_answers_is_still_sent(
    hass: HomeAssistant, freezer
) -> None:
    """A real light back from an outage as off gets the light's settings
    again even when the select call of that re-send never answers."""
    select = _SlowSelect(hass)
    hass.states.async_set("light.ambient", "off")
    await setup_entries(hass, _selection_entry(timeout=600))
    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 200},
            blocking=True,
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    select.release.set()
    await turn_on
    await settle(hass)
    hass.states.async_set("light.ambient", "on", {"brightness": 200})
    await settle(hass)
    select.started.clear()
    select.release.clear()
    calls = _light_calls(hass)

    hass.states.async_set("light.ambient", "unavailable")
    await _drain(hass)
    hass.states.async_set("light.ambient", "off")
    await asyncio.wait_for(select.started.wait(), 2)
    await _pass(hass, freezer, 9)
    assert calls == []
    await _pass(hass, freezer, 2)
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 200)]
    assert state.state == "on"
    assert state.attributes["molight_state"] == "active"
    assert state.attributes["last_off_manual"] is None


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.allow_warning_log
async def test_window_start_whose_select_call_never_answers_still_lights(
    hass: HomeAssistant, freezer
) -> None:
    """A follow window start is a turn-on like any other."""
    select = _HangingSelect(hass)
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

    await _pass(hass, freezer, 11)
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 102)]
    assert select.cancelled == 1
    assert state.attributes["molight_state"] == "scheduled"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.allow_warning_log
async def test_standby_whose_select_call_never_answers_still_comes_on(
    hass: HomeAssistant, freezer
) -> None:
    """Coming on at standby waits for its select call no longer either."""
    select = _HangingSelect(hass)
    hass.states.async_set("binary_sensor.settings_schedule", "off")
    hass.states.async_set("light.ambient", "off")
    await setup_entries(
        hass,
        make_scheduled_light_entry(
            name="Selection Light",
            lights=["light.ambient"],
            inside={
                CONF_LIGHT_TIMEOUT: 60,
                CONF_STANDBY_BRIGHTNESS: 20,
                CONF_TURN_ON_SELECT_ENTITY: "select.ambient_theme",
                CONF_TURN_ON_SELECT_OPTION: "Cozy",
            },
        ),
    )
    calls = _light_calls(hass)
    hass.states.async_set("binary_sensor.settings_schedule", "on")
    await asyncio.wait_for(select.started.wait(), 2)

    await _pass(hass, freezer, 11)
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 51)]
    assert select.cancelled == 1
    assert state.attributes["molight_state"] == "standby"


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


async def _run_script(hass: HomeAssistant, level: int) -> None:
    """Start a script that turns the virtual light on at a level."""
    if not hass.services.has_service("script", "lights_on"):
        turn_on = {
            "action": "light.turn_on",
            "target": {"entity_id": "light.selection_light"},
            "data": {"brightness": "{{ level }}"},
        }
        config = {"lights_on": {"mode": "restart", "sequence": [turn_on]}}
        assert await async_setup_component(hass, "script", {"script": config})
    await hass.services.async_call(
        "script",
        "turn_on",
        {"entity_id": "script.lights_on", "variables": {"level": level}},
        blocking=True,
    )
    await _drain(hass)
    assert hass.states.get("light.selection_light").attributes["brightness"] == level


async def _stop_script(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "script", "turn_off", {"entity_id": "script.lights_on"}, blocking=True
    )
    await _drain(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["stop", "restart"])
async def test_script_stopped_during_the_wait_takes_back_its_on(
    hass: HomeAssistant, freezer, how: str
) -> None:
    """A script stopped while its turn-on waits for the select call lights
    nothing, and the light reports off again. Run again instead, the script
    stops its first turn-on and the second one lights the room."""
    select = _SlowSelect(hass)
    hass.states.async_set("light.ambient", "off")
    await setup_entries(hass, _selection_entry())
    calls = _light_calls(hass)
    await _run_script(hass, 200)
    await asyncio.wait_for(select.started.wait(), 2)
    assert hass.states.get("light.selection_light").state == "on"

    if how == "stop":
        await _stop_script(hass)
        state = hass.states.get("light.selection_light")
        assert (state.state, state.attributes["molight_state"]) == ("off", "idle")
    else:
        await _run_script(hass, 100)
    select.release.set()
    await settle(hass)
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await settle(hass)

    state = hass.states.get("light.selection_light")
    if how == "stop":
        assert calls == []
        assert (state.state, state.attributes["molight_state"]) == ("off", "idle")
    else:
        assert calls == [("turn_on", 100)]
        assert (state.state, state.attributes["brightness"]) == ("on", 100)
        assert state.attributes["molight_state"] == "active"


@pytest.mark.asyncio
async def test_script_stopped_during_the_wait_leaves_a_newer_turn_on(
    hass: HomeAssistant,
) -> None:
    """A turn-on made after the script's replaces it and is not stopped with
    the script: it lights the room at its own level."""
    select = _SlowSelect(hass)
    hass.states.async_set("light.ambient", "off")
    await setup_entries(hass, _selection_entry())
    calls = _light_calls(hass)
    await _run_script(hass, 200)
    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 50},
            blocking=True,
        )
    )
    await _drain(hass)

    await _stop_script(hass)
    assert hass.states.get("light.selection_light").state == "on"
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 50)]
    assert (state.state, state.attributes["brightness"]) == ("on", 50)


@pytest.mark.asyncio
async def test_script_stopped_during_the_wait_leaves_occupancy_its_lights(
    hass: HomeAssistant,
) -> None:
    """The script's turn-on replaced one occupancy had waiting. Stopped, it
    sends nothing, and the room someone is in is lit at the auto-on level."""
    select = await _park_automatic_turn_on(hass, auto_on_brightness=40)
    calls = _light_calls(hass)
    await _run_script(hass, 200)

    await _stop_script(hass)
    assert hass.states.get("light.selection_light").state == "on"
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 102)]
    assert (state.state, state.attributes["brightness"]) == ("on", 102)
    assert state.attributes["molight_state"] == "occupied"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_script_stopped_during_the_wait_leaves_standby_its_lights(
    hass: HomeAssistant,
) -> None:
    """The script raises a light that was coming on at standby. Stopped, it
    sends nothing, and the light comes on at standby after all."""
    select = _SlowSelect(hass)
    hass.states.async_set("light.ambient", "off")
    hass.states.async_set(_SCHEDULE, "off")
    await setup_entries(
        hass,
        make_scheduled_light_entry(
            name="Selection Light",
            lights=["light.ambient"],
            inside={**_PRESET, CONF_STANDBY_BRIGHTNESS: 20},
        ),
    )
    hass.states.async_set(_SCHEDULE, "on")
    await asyncio.wait_for(select.started.wait(), 2)
    assert _molight_state(hass) == "standby"
    calls = _light_calls(hass)
    await _run_script(hass, 200)

    await _stop_script(hass)
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == [("turn_on", 51)]
    assert (state.state, state.attributes["brightness"]) == ("on", 51)
    assert state.attributes["molight_state"] == "standby"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_script_stopped_during_startup_is_no_command_for_the_seed(
    hass: HomeAssistant,
) -> None:
    """The script runs while Home Assistant starts and is stopped during the
    select call. The light then reads its real light, which is off, and
    sends nothing."""
    select = _SlowSelect(hass)
    hass.states.async_set("light.ambient", "off")
    hass.set_state(CoreState.starting)
    await setup_entries(hass, _selection_entry())
    calls = _light_calls(hass)
    await _run_script(hass, 200)

    await _stop_script(hass)
    select.release.set()
    await finish_startup(hass)
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == []
    assert (state.state, state.attributes["molight_state"]) == ("off", "idle")
    assert state.attributes["last_off_manual"] is None


@pytest.mark.asyncio
async def test_script_stopped_during_a_blink_leaves_the_warning_running(
    hass: HomeAssistant, freezer
) -> None:
    """A stage has the real light blinked off when the script turns the
    light on, and the script is stopped during the select call: the warning
    runs on and ends in off."""
    select = _SlowSelect(hass)
    select.release.set()
    lamp = FadingLight("ambient", brightness=200)
    await add_real(hass, lamp)
    await setup_entries(
        hass, _selection_entry(timeout=5, effect_timeout=8, effect_brightness=0)
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light"}, blocking=True
    )
    await settle(hass)
    await _pass(hass, freezer, 6)
    await settle(hass)
    assert (_molight_state(hass), lamp.is_on) == ("effect", False)
    select.release.clear()
    commands = record_service_calls(hass)
    await _run_script(hass, 150)

    await _stop_script(hass)
    select.release.set()
    await settle(hass)
    assert (_molight_state(hass), lamp.is_on) == ("effect", False)
    await _pass(hass, freezer, 9)
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert ["light.ambient"] not in light_targets(commands, "turn_on")
    assert (state.state, state.attributes["molight_state"]) == ("off", "idle")
    assert not lamp.is_on


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
@pytest.mark.parametrize("trigger", ["manual", "occupancy"])
async def test_members_reporting_in_off_after_the_turn_on_get_it_again(
    hass: HomeAssistant, trigger: str
) -> None:
    """Members reporting in as off after the turn-on was sent missed it, or
    lost their preset: the selection is applied again and the settings are
    re-sent to every member, so one reporting in while that select call runs
    is lit too."""
    members = ["light.ambient", "light.ambient_2"]
    selected: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def select_option(call: ServiceCall) -> None:
        selected.append(call.data["option"])
        if len(selected) == 2:
            started.set()
            await release.wait()

    hass.services.async_register("select", "select_option", select_option)
    hass.states.async_set("binary_sensor.occ", "off")
    for member in members:
        hass.states.async_set(member, "off")
    await setup_entries(
        hass,
        make_light_entry(
            name="Selection Light",
            lights=members,
            occupancy="binary_sensor.occ",
            auto_on_brightness=40,
            turn_on_select_entity="select.ambient_theme",
            turn_on_select_option="Cozy",
        ),
    )
    if trigger == "manual":
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 200},
            blocking=True,
        )
    else:
        hass.states.async_set("binary_sensor.occ", "on")
    await settle(hass)
    for member in members:
        hass.states.async_set(member, "on", {"brightness": _LEVEL[trigger]})
    await settle(hass)
    calls = record_service_calls(hass)

    for member in members:
        hass.states.async_set(member, "unavailable")
        await _drain(hass)
        hass.states.async_set(member, "off")
        await asyncio.wait_for(started.wait(), 2)
        await _drain(hass)
    release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert selected == ["Cozy", "Cozy"]
    assert light_targets(calls, "turn_on") == [members]
    assert light_targets(calls, "turn_off") == []
    assert state.state == "on"
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


def _registered(hass: HomeAssistant, *entity_ids: str) -> None:
    """Register the entities with no device, like a template select or a group."""
    for entity_id in entity_ids:
        domain, object_id = entity_id.split(".")
        state = hass.states.get(entity_id)
        hass.states.async_remove(entity_id)
        registered = er.async_get(hass).async_get_or_create(
            domain, "template", entity_id, suggested_object_id=object_id
        )
        assert registered.entity_id == entity_id
        assert registered.device_id is None
        if state:
            hass.states.async_set(entity_id, state.state, state.attributes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "layout", ["neither_has_a_device", "light_without_device", "neither_registered"]
)
async def test_wall_turn_on_of_a_light_without_a_device_still_overtakes(
    hass: HomeAssistant, layout: str
) -> None:
    """Having no device is not having the same device: a template select and
    a light group speak for nothing but themselves."""
    if layout == "neither_has_a_device":
        _registered(hass, "select.ambient_theme", "light.ambient")
    elif layout == "light_without_device":
        _one_device(hass, "select.ambient_theme")
        _registered(hass, "light.ambient")
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
@pytest.mark.parametrize("earlier_call", [False, True], ids=["no_call", "call_done"])
async def test_wall_turn_on_on_the_select_device_with_no_call_running_is_claimed(
    hass: HomeAssistant, earlier_call: bool
) -> None:
    """Sharing the select entity's device only matters while a select call
    runs: before any, and once it has finished, a turn-on of that light is a
    turn-on at the wall."""
    _one_device(hass, "select.ambient_theme", "light.ambient")
    select = _SlowSelect(hass)
    select.release.set()
    hass.states.async_set("light.ambient", "off")
    await setup_entries(hass, _selection_entry())
    contexts: list[Context] = []

    @callback
    def record_context(event: Event) -> None:
        if event.data["service_data"]["entity_id"] == ["light.ambient"]:
            contexts.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, record_context)
    if earlier_call:
        for service, reply in (("turn_on", "on"), ("turn_off", "off")):
            await hass.services.async_call(
                "light", service, {"entity_id": "light.selection_light"}, blocking=True
            )
            await settle(hass)
            hass.states.async_set("light.ambient", reply, context=contexts[-1])
            await settle(hass)
        assert select.started.is_set()
    state = hass.states.get("light.selection_light")
    assert state.state == "off"
    assert state.attributes["last_on_physical"] is None
    calls = _light_calls(hass)

    hass.states.async_set("light.ambient", "on", {"brightness": 150})
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert calls == []
    assert state.state == "on"
    assert state.attributes["brightness"] == 150
    assert state.attributes["last_on_physical"] is not None


_SCHEDULE = "binary_sensor.settings_schedule"
_PRESET = {
    CONF_LIGHT_TIMEOUT: 60,
    CONF_TURN_ON_SELECT_ENTITY: "select.ambient_theme",
    CONF_TURN_ON_SELECT_OPTION: "Cozy",
}
_OTHER_SELECT = {
    CONF_LIGHT_TIMEOUT: 60,
    CONF_TURN_ON_SELECT_ENTITY: "select.other_theme",
    CONF_TURN_ON_SELECT_OPTION: "Cozy",
}
_OTHER_SETTINGS = {
    "no_selection": {CONF_LIGHT_TIMEOUT: 60},
    "another_device": _OTHER_SELECT,
    "no_device": _OTHER_SELECT,
}


async def _begin_scheduled_turn_on(
    hass: HomeAssistant,
    select: _SlowSelect,
    *,
    inside: bool,
    active: dict,
    other: dict,
) -> asyncio.Task:
    """Start a manual turn-on of a Virtual Scheduled Light under its active
    settings, and return once it waits for the select call."""
    hass.states.async_set("select.other_theme", "Normal")
    hass.states.async_set("light.ambient", "off")
    hass.states.async_set(_SCHEDULE, "on" if inside else "off")
    await setup_entries(
        hass,
        make_scheduled_light_entry(
            lights=["light.ambient"],
            inside=active if inside else other,
            outside=other if inside else active,
        ),
    )
    task = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.scheduled_light", "brightness": 200},
            blocking=True,
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)
    return task


async def _switch_settings(hass: HomeAssistant, *, inside: bool) -> None:
    """Cross the settings schedule's boundary while a select call is parked."""
    hass.states.async_set(_SCHEDULE, "on" if inside else "off")
    await _drain(hass)
    state = hass.states.get("light.scheduled_light")
    assert state.attributes["active_settings"] == (
        "inside_schedule" if inside else "outside_schedule"
    )


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("inside", [False, True], ids=["outside", "inside"])
@pytest.mark.parametrize("other", ["no_selection", "another_device", "no_device"])
@pytest.mark.parametrize("switch", [False, True], ids=["stay", "switch"])
async def test_device_push_after_a_settings_switch_is_still_the_selection(
    hass: HomeAssistant, inside: bool, other: str, switch: bool
) -> None:
    """A select call in progress speaks for the device it was made on, also
    after the schedule switched to settings that select nothing, or something
    on another device: the preset lighting the light is no change at the
    wall, and the turn-on is still sent."""
    _one_device(hass, "select.ambient_theme", "light.ambient")
    if other == "another_device":
        _one_device(hass, "select.other_theme")
    select = _SlowSelect(hass)
    calls = _light_calls(hass)
    turn_on = await _begin_scheduled_turn_on(
        hass, select, inside=inside, active=_PRESET, other=_OTHER_SETTINGS[other]
    )

    if switch:
        await _switch_settings(hass, inside=not inside)
    for brightness in (77, 100):
        hass.states.async_set("light.ambient", "on", {"brightness": brightness})
        await _drain(hass)
    assert calls == []
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.scheduled_light")
    assert calls == [("turn_on", 200)]
    assert state.attributes["brightness"] == 200
    assert state.attributes["molight_state"] == "active"
    assert state.attributes["last_on_physical"] is None
    assert state.attributes["last_brightness_change_physical"] is None
    assert state.attributes["last_turn_on_selection_option"] == "Cozy"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("inside", [False, True], ids=["outside", "inside"])
@pytest.mark.parametrize("active", ["another_device", "no_device"])
@pytest.mark.parametrize("switch", [False, True], ids=["stay", "switch"])
async def test_wall_turn_on_after_a_switch_to_a_device_selection_overtakes(
    hass: HomeAssistant, inside: bool, active: str, switch: bool
) -> None:
    """Settings that select on the light's own device speak for no select
    call they did not make: a call in progress on another device leaves a
    turn-on of the light a change at the wall."""
    _one_device(hass, "select.ambient_theme", "light.ambient")
    if active == "another_device":
        _one_device(hass, "select.other_theme")
    select = _SlowSelect(hass)
    calls = _light_calls(hass)
    turn_on = await _begin_scheduled_turn_on(
        hass, select, inside=inside, active=_OTHER_SELECT, other=_PRESET
    )

    if switch:
        await _switch_settings(hass, inside=not inside)
    hass.states.async_set("light.ambient", "on", {"brightness": 77})
    await _drain(hass)
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.scheduled_light")
    assert calls == []
    assert state.attributes["brightness"] == 77
    assert state.attributes["last_on_physical"] is not None


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
@pytest.mark.parametrize("other", ["no_selection", "another_device"])
@pytest.mark.allow_warning_log
async def test_failed_select_call_is_named_after_a_settings_switch(
    hass: HomeAssistant, caplog, other: str
) -> None:
    """The warning names the select entity that was called, not the one the
    settings switched to meanwhile."""
    select = _FailingSelect(hass, HomeAssistantError("device offline"))
    calls = _light_calls(hass)
    turn_on = await _begin_scheduled_turn_on(
        hass, select, inside=False, active=_PRESET, other=_OTHER_SETTINGS[other]
    )

    await _switch_settings(hass, inside=True)
    select.release.set()
    await turn_on
    await settle(hass)

    assert calls == [("turn_on", 200)]
    assert "'Cozy' using select.ambient_theme;" in caplog.text


# ---------------------------------------------------------------------------
# A re-send or an automatic turn-on waiting for its selection across a stage
# ---------------------------------------------------------------------------

_COLOR_CAPS = {
    "supported_color_modes": ["hs", "color_temp"],
    "min_color_temp_kelvin": 2000,
    "max_color_temp_kelvin": 6500,
}


def _light_commands(hass: HomeAssistant) -> list[dict]:
    """Record each command to the real light, without its target."""
    commands: list[dict] = []

    @callback
    def record_call(event: Event) -> None:
        data = dict(event.data["service_data"])
        if event.data["domain"] == "light" and data.pop("entity_id") == [
            "light.ambient"
        ]:
            commands.append({"service": event.data["service"], **data})

    hass.bus.async_listen(EVENT_CALL_SERVICE, record_call)
    return commands


async def _lit_light(
    hass: HomeAssistant, select: _SlowSelect, *, kelvin: int | None = None, **kwargs
) -> None:
    """Turn the light on at 200 through the virtual light; its member replies."""
    hass.states.async_set("binary_sensor.occ", "off")
    hass.states.async_set("light.ambient", "off", _COLOR_CAPS if kelvin else {})
    await setup_entries(hass, _selection_entry(occupancy="binary_sensor.occ", **kwargs))
    contexts: list[Context] = []

    @callback
    def record_context(event: Event) -> None:
        if event.data["service_data"]["entity_id"] == ["light.ambient"]:
            contexts.append(event.context)

    unsub = hass.bus.async_listen(EVENT_CALL_SERVICE, record_context)
    select.release.set()
    color = {"color_temp_kelvin": kelvin} if kelvin else {}
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.selection_light", "brightness": 200, **color},
        blocking=True,
    )
    await settle(hass)
    reply = {"brightness": 200}
    if kelvin:
        reply |= {**_COLOR_CAPS, "color_mode": "color_temp", **color}
    hass.states.async_set("light.ambient", "on", reply, context=contexts[-1])
    await settle(hass)
    unsub()
    state = hass.states.get("light.selection_light")
    assert state.attributes["molight_state"] == "active"
    assert state.attributes["last_on_physical"] is None


async def _park_resend(hass: HomeAssistant, select: _SlowSelect) -> None:
    """The member drops out and comes back off; the re-send waits for its
    select call."""
    select.started.clear()
    select.release.clear()
    attributes = dict(hass.states.get("light.ambient").attributes)
    capabilities = {k: v for k, v in attributes.items() if k in _COLOR_CAPS}
    hass.states.async_set("light.ambient", "unavailable")
    await _drain(hass)
    hass.states.async_set("light.ambient", "off", capabilities)
    await asyncio.wait_for(select.started.wait(), 2)


def _molight_state(hass: HomeAssistant) -> str:
    return hass.states.get("light.selection_light").attributes["molight_state"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["effect", "warn"])
async def test_resend_waiting_into_a_stage_sends_the_stage(
    hass: HomeAssistant, freezer, stage: str
) -> None:
    """A re-send that waited for its select call while the timer reached a
    stage sends that stage, not the brightness the light had before, and the
    warning runs on to the off."""
    select = _SlowSelect(hass)
    stages = {"effect_timeout": 5, "effect_brightness": 20} if stage == "effect" else {}
    await _lit_light(hass, select, warn_timeout=10, warn_brightness=20, **stages)
    await _pass(hass, freezer, 57)
    await _park_resend(hass, select)
    commands = _light_commands(hass)

    await _pass(hass, freezer, 4)
    assert _molight_state(hass) == stage
    assert commands == [{"service": "turn_on", "brightness": 51}]
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert commands == [{"service": "turn_on", "brightness": 51}] * 2
    assert state.attributes["molight_state"] == stage
    assert state.attributes["brightness"] == 51
    assert state.attributes["pre_warn_brightness"] == 200
    assert state.attributes["last_turn_on_selection_option"] == "Cozy"

    for seconds in (6, 11):
        await _pass(hass, freezer, seconds)
        await settle(hass)
    assert hass.states.get("light.selection_light").state == "off"
    assert commands[-1] == {"service": "turn_off"}


@pytest.mark.asyncio
async def test_resend_waiting_into_a_blink_off_does_not_relight_it(
    hass: HomeAssistant, freezer
) -> None:
    """A stage that blinks the lights off is sent again as an off."""
    select = _SlowSelect(hass)
    await _lit_light(
        hass, select, effect_timeout=5, effect_brightness=0, warn_timeout=10
    )
    await _pass(hass, freezer, 57)
    await _park_resend(hass, select)
    commands = _light_commands(hass)

    await _pass(hass, freezer, 4)
    assert _molight_state(hass) == "effect"
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert commands == [{"service": "turn_off"}] * 2
    assert state.state == "on"
    assert state.attributes["molight_state"] == "effect"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage_color", [False, True], ids=["plain", "colored"])
async def test_resend_waiting_into_a_stage_sends_the_stage_color(
    hass: HomeAssistant, freezer, stage_color: bool
) -> None:
    """A stage's own color is sent, not the color the light had before; a
    stage that names none is sent in the light's color, so the real light
    that came back shows what the others show."""
    select = _SlowSelect(hass)
    color = {"warn_rgb_color": [255, 0, 0]} if stage_color else {}
    await _lit_light(
        hass, select, kelvin=3000, warn_timeout=10, warn_brightness=20, **color
    )
    await _pass(hass, freezer, 57)
    await _park_resend(hass, select)
    commands = _light_commands(hass)

    await _pass(hass, freezer, 4)
    assert _molight_state(hass) == "warn"
    select.release.set()
    await settle(hass)

    stage = {"service": "turn_on", "brightness": 51}
    if stage_color:
        assert commands == [{**stage, "rgb_color": (255, 0, 0)}] * 2
    else:
        assert commands == [stage, {**stage, "color_temp_kelvin": 3000}]
    state = hass.states.get("light.selection_light")
    assert state.attributes["brightness"] == 51
    assert state.attributes["pre_warn_color"] == {"color_temp_kelvin": 3000}


@pytest.mark.asyncio
async def test_resend_started_in_a_stage_sends_the_stage_reached_since(
    hass: HomeAssistant, freezer
) -> None:
    """Effect at 20% when the re-send starts, warn at 40% when it is sent."""
    select = _SlowSelect(hass)
    await _lit_light(
        hass,
        select,
        effect_timeout=5,
        effect_brightness=20,
        warn_timeout=10,
        warn_brightness=40,
    )
    await _pass(hass, freezer, 61)
    await settle(hass)
    assert _molight_state(hass) == "effect"
    await _park_resend(hass, select)
    commands = _light_commands(hass)
    assert hass.states.get("light.selection_light").attributes["brightness"] == 51

    await _pass(hass, freezer, 6)
    assert _molight_state(hass) == "warn"
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert commands == [{"service": "turn_on", "brightness": 102}] * 2
    assert state.attributes["brightness"] == 102
    assert state.attributes["pre_warn_brightness"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["stays", "occupancy", "expires"])
async def test_resend_started_in_a_stage_follows_how_the_stage_ends(
    hass: HomeAssistant, freezer, ending: str
) -> None:
    """A re-send that starts during a warning reports the warning while it
    waits. It sends the warning if that still shows, the brightness restored
    if occupancy ended the warning meanwhile, and nothing after the off."""
    select = _SlowSelect(hass)
    await _lit_light(hass, select, warn_timeout=5, warn_brightness=20)
    await _pass(hass, freezer, 61)
    await settle(hass)
    assert _molight_state(hass) == "warn"
    await _park_resend(hass, select)
    commands = _light_commands(hass)
    state = hass.states.get("light.selection_light")
    assert state.attributes["brightness"] == 51
    assert state.attributes["molight_state"] == "warn"

    if ending == "occupancy":
        hass.states.async_set("binary_sensor.occ", "on")
        await _drain(hass)
        assert commands == [{"service": "turn_on", "brightness": 200}]
    elif ending == "expires":
        await _pass(hass, freezer, 6)
        assert commands == [{"service": "turn_off"}]
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    if ending == "stays":
        assert commands == [{"service": "turn_on", "brightness": 51}]
        assert state.attributes["molight_state"] == "warn"
        assert state.attributes["brightness"] == 51
        assert state.attributes["pre_warn_brightness"] == 200
    elif ending == "occupancy":
        assert commands == [{"service": "turn_on", "brightness": 200}] * 2
        assert state.attributes["molight_state"] == "occupied"
        assert state.attributes["brightness"] == 200
        assert state.attributes["warning_active"] is False
    else:
        assert commands == [{"service": "turn_off"}]
        assert state.state == "off"


@pytest.mark.asyncio
async def test_preset_push_during_a_resend_into_a_stage_keeps_the_warning(
    hass: HomeAssistant, freezer
) -> None:
    """The preset of a re-send lighting its own device's light during a
    warning is no change at the wall: the warning carries on, and its
    brightness is sent over the preset's."""
    _one_device(hass, "select.ambient_theme", "light.ambient")
    select = _SlowSelect(hass)
    await _lit_light(hass, select, warn_timeout=10, warn_brightness=20)
    await _pass(hass, freezer, 57)
    await _park_resend(hass, select)
    commands = _light_commands(hass)

    await _pass(hass, freezer, 4)
    assert _molight_state(hass) == "warn"
    hass.states.async_set("light.ambient", "on", {"brightness": 128})
    await _drain(hass)
    assert _molight_state(hass) == "warn"
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert commands == [{"service": "turn_on", "brightness": 51}] * 2
    assert state.attributes["molight_state"] == "warn"
    assert state.attributes["brightness"] == 51
    assert state.attributes["pre_warn_brightness"] == 200
    assert state.attributes["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_automatic_turn_on_waiting_into_a_stage_sends_the_stage(
    hass: HomeAssistant, freezer
) -> None:
    """Motion lights the room, a false detection ends it and its quick off
    reaches the warning while the select call still runs: the warning is
    what the lights show, not the level motion asked for."""
    select = await _park_automatic_turn_on(
        hass,
        auto_on_brightness=40,
        false_off_delay=5,
        warn_timeout=10,
        warn_brightness=20,
    )
    commands = _light_commands(hass)
    hass.states.async_set(
        "binary_sensor.occ", "off", {"last_clear_false_detection": True}
    )
    await _drain(hass)

    await _pass(hass, freezer, 6)
    assert _molight_state(hass) == "warn"
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert commands == [{"service": "turn_on", "brightness": 51}] * 2
    assert state.attributes["molight_state"] == "warn"
    assert state.attributes["brightness"] == 51
    assert state.attributes["pre_warn_brightness"] == 102

    await _pass(hass, freezer, 11)
    await settle(hass)
    assert hass.states.get("light.selection_light").state == "off"


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_resend_started_in_a_stage_yields_to_standby(
    hass: HomeAssistant, freezer
) -> None:
    """A warning that ends at standby while a re-send waits leaves the lights
    at standby: the level chosen last stands, and is sent again once the
    select call ends, in case its preset landed on top."""
    select = _SlowSelect(hass)
    select.release.set()
    hass.states.async_set(_SCHEDULE, "on")
    hass.states.async_set("light.ambient", "off")
    contexts: list[Context] = []

    @callback
    def record_context(event: Event) -> None:
        if event.data["service_data"]["entity_id"] == ["light.ambient"]:
            contexts.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, record_context)
    await setup_entries(
        hass,
        make_scheduled_light_entry(
            name="Selection Light",
            lights=["light.ambient"],
            inside={
                **_PRESET,
                CONF_STANDBY_BRIGHTNESS: 20,
                CONF_WARN_TIMEOUT: 5,
                CONF_WARN_BRIGHTNESS: 40,
            },
        ),
    )
    await settle(hass)
    assert _molight_state(hass) == "standby"
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.selection_light", "brightness": 200},
        blocking=True,
    )
    await settle(hass)
    hass.states.async_set(
        "light.ambient", "on", {"brightness": 200}, context=contexts[-1]
    )
    await settle(hass)
    await _pass(hass, freezer, 61)
    await settle(hass)
    assert _molight_state(hass) == "warn"
    await _park_resend(hass, select)
    commands = _light_commands(hass)

    await _pass(hass, freezer, 6)
    assert _molight_state(hass) == "standby"
    select.release.set()
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert commands == [{"service": "turn_on", "brightness": 51}] * 2
    assert state.attributes["molight_state"] == "standby"
    assert state.attributes["brightness"] == 51


# ---------------------------------------------------------------------------
# A command sent while a replaced turn-on's select call still runs
#
# The preset of that call lands on its device's light afterwards, on top of
# the command. These use a real light and select on one device.
# ---------------------------------------------------------------------------

PRESET_LEVEL = 128
WALL_STAMPS = (
    "last_on_physical",
    "last_brightness_change_physical",
    "last_color_change_physical",
    "last_off_manual",
)


class _PresetSelect(RealSelect):
    """A registered select whose options are presets of its device's light.

    A select call waits until released, then sets the light's level, which
    the light reports itself, as a WLED preset does.
    """

    def __init__(self, light: FadingLight) -> None:
        super().__init__("ambient_theme")
        self._light = light
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def async_select_option(self, option: str) -> None:
        self.started.set()
        await self.release.wait()
        await super().async_select_option(option)
        if self._light.available:
            self._light.wall(brightness=PRESET_LEVEL)


async def _preset_strip(
    hass: HomeAssistant, *, on: bool = False
) -> tuple[FadingLight, _PresetSelect]:
    """Add a light and its preset select, registered as one device."""
    hass.states.async_remove("select.ambient_theme")
    light = FadingLight("ambient", on=on, brightness=200)
    select = _PresetSelect(light)
    await add_real(hass, light, select)
    entry = MockConfigEntry(domain="wled")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("wled", "strip")}
    )
    for entity_id in ("light.ambient", "select.ambient_theme"):
        er.async_get(hass).async_update_entity(entity_id, device_id=device.id)
    return light, select


async def _park_real_resend(
    hass: HomeAssistant, light: FadingLight, select: _PresetSelect, *, on: bool = False
) -> None:
    """The strip drops out and comes back; its re-send waits for the select."""
    select.started.clear()
    select.release.clear()
    light.wall(on=on, available=False)
    await _drain(hass)
    light.wall(on=on, available=True)
    await asyncio.wait_for(select.started.wait(), 2)


def _wall_stamps(hass: HomeAssistant) -> dict:
    attrs = hass.states.get("light.selection_light").attributes
    return {name: attrs[name] for name in WALL_STAMPS if attrs[name] is not None}


async def _land_preset(hass: HomeAssistant, select: _PresetSelect) -> None:
    """Let the select call finish: its preset sets the strip's level."""
    select.release.set()
    await settle(hass)


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_standby_sent_during_a_replaced_select_call_outlasts_its_preset(
    hass: HomeAssistant, freezer
) -> None:
    """A warning ends at standby while a re-send's select call still runs.
    The preset then sets the strip to its own level: standby is sent again,
    and the preset is no dim at the wall that would leave standby."""
    light, select = await _preset_strip(hass)
    hass.states.async_set(_SCHEDULE, "on")
    await setup_entries(
        hass,
        make_scheduled_light_entry(
            name="Selection Light",
            lights=["light.ambient"],
            inside={
                **_PRESET,
                CONF_STANDBY_BRIGHTNESS: 20,
                CONF_WARN_TIMEOUT: 5,
                CONF_WARN_BRIGHTNESS: 40,
            },
        ),
    )
    await settle(hass)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.selection_light", "brightness": 200},
        blocking=True,
    )
    await settle(hass)
    await _pass(hass, freezer, 61)
    await settle(hass)
    assert _molight_state(hass) == "warn"
    before = _wall_stamps(hass)
    await _park_real_resend(hass, light, select)

    await _pass(hass, freezer, 6)
    assert _molight_state(hass) == "standby"
    assert light.brightness == 51
    await _land_preset(hass, select)

    state = hass.states.get("light.selection_light")
    assert light.brightness == 51
    assert state.attributes["molight_state"] == "standby"
    assert state.attributes["brightness"] == 51
    assert _wall_stamps(hass) == before


async def _lit_pair(
    hass: HomeAssistant, **kwargs: Any
) -> tuple[FadingLight, FadingLight, _PresetSelect]:
    """A light over the preset strip and a second real light, both at 200."""
    light, select = await _preset_strip(hass, on=True)
    other = FadingLight("other", on=True, brightness=200)
    await add_real(hass, other)
    entry = make_light_entry(
        name="Selection Light",
        lights=["light.ambient", "light.other"],
        turn_on_select_entity="select.ambient_theme",
        turn_on_select_option="Cozy",
        **kwargs,
    )
    await setup_entries(hass, entry)
    assert _molight_state(hass) == "active"
    return light, other, select


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["level", "restore", "stage", "blink"])
async def test_command_sent_during_a_replaced_select_call_outlasts_its_preset(
    hass: HomeAssistant, freezer, command: str
) -> None:
    """The strip's re-send waits for its select call when a turn-on through
    the light replaces it: a new level, or the pre-warning one restored. A
    stage the timer then reaches is sent during the call too, and may blink
    the lights off. Whichever was sent last is what the strip shows once the
    preset has landed."""
    stage = {"warn_timeout": 8, "warn_brightness": 20}
    if command == "blink":
        stage = {"effect_timeout": 8, "effect_brightness": 0}
    light, other, select = await _lit_pair(hass, timeout=5, **stage)
    if command == "restore":
        await _pass(hass, freezer, 6)
        await settle(hass)
        assert _molight_state(hass) == "warn"
    before = _wall_stamps(hass)
    await _park_real_resend(hass, light, select)

    data = {} if command == "restore" else {"brightness": 100}
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light", **data}
    )
    await _drain(hass)
    level = 200 if command == "restore" else 100
    assert (light.brightness, other.brightness) == (level, level)
    machine_state = "active"
    if command in ("stage", "blink"):
        await _pass(hass, freezer, 6)
        machine_state = "warn" if command == "stage" else "effect"
    assert _molight_state(hass) == machine_state
    await _land_preset(hass, select)

    state = hass.states.get("light.selection_light")
    assert state.attributes["molight_state"] == machine_state
    assert _wall_stamps(hass) == before
    if command == "blink":
        assert (light.is_on, other.is_on, state.state) == (False, False, "on")
        return
    if command == "stage":
        level = 51
    assert (light.brightness, other.brightness) == (level, level)
    assert state.attributes["brightness"] == level

    # The strip dimmed at the wall afterwards is a person again.
    light.wall(brightness=77)
    await settle(hass)
    assert "last_brightness_change_physical" in _wall_stamps(hass)
    assert hass.states.get("light.selection_light").attributes["brightness"] == 77


@pytest.mark.asyncio
@pytest.mark.regular_virtual_light_only
async def test_raise_sent_during_a_replaced_standby_select_outlasts_its_preset(
    hass: HomeAssistant,
) -> None:
    """The strip reboots lit at standby, so standby and its preset are sent
    again. Someone walks in while that select call runs: the raise is sent
    at once, replaces it, and is what the strip shows after the preset."""
    light, select = await _preset_strip(hass)
    hass.states.async_set(_SCHEDULE, "on")
    hass.states.async_set("binary_sensor.occ", "off")
    await setup_entries(
        hass,
        make_scheduled_light_entry(
            name="Selection Light",
            lights=["light.ambient"],
            inside={
                **_PRESET,
                CONF_OCCUPANCY_ENTITY: "binary_sensor.occ",
                CONF_AUTO_ON_BRIGHTNESS: 80,
                CONF_STANDBY_BRIGHTNESS: 20,
            },
        ),
    )
    await settle(hass)
    assert _molight_state(hass) == "standby"
    assert light.brightness == 51
    await _park_real_resend(hass, light, select, on=True)

    hass.states.async_set("binary_sensor.occ", "on")
    await _drain(hass)
    assert _molight_state(hass) == "occupied"
    assert light.brightness == 204
    await _land_preset(hass, select)

    state = hass.states.get("light.selection_light")
    assert light.brightness == 204
    assert state.attributes["molight_state"] == "occupied"
    assert state.attributes["brightness"] == 204
    assert _wall_stamps(hass) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [None, "level"], ids=["alone", "after_a_level"])
async def test_wall_change_during_a_replaced_select_call_is_not_sent_again(
    hass: HomeAssistant, first: str | None
) -> None:
    """The other real light dimmed at the wall replaces the re-send too, also
    after a new level did. Real lights are not kept in step, so nothing is
    sent after the preset."""
    light, other, select = await _lit_pair(hass)
    await _park_real_resend(hass, light, select)
    if first:
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 100},
        )
        await _drain(hass)
    commands = record_service_calls(hass)

    other.wall(brightness=150)
    await _drain(hass)
    await _land_preset(hass, select)

    state = hass.states.get("light.selection_light")
    assert light_targets(commands, "turn_on") == []
    assert other.brightness == 150
    assert state.attributes["last_brightness_change_physical"] is not None


@pytest.mark.asyncio
async def test_stage_after_a_wall_change_during_a_select_call_is_sent_again(
    hass: HomeAssistant, freezer
) -> None:
    """What the wall set stands, but a stage the timer reaches afterwards,
    still during the call, is the light's own command again."""
    light, other, select = await _lit_pair(
        hass, timeout=5, warn_timeout=8, warn_brightness=20
    )
    await _park_real_resend(hass, light, select)
    other.wall(brightness=150)
    await _drain(hass)

    await _pass(hass, freezer, 6)
    assert _molight_state(hass) == "warn"
    await _land_preset(hass, select)

    assert (light.brightness, other.brightness) == (51, 51)
    assert _molight_state(hass) == "warn"


@pytest.mark.asyncio
async def test_off_at_the_wall_after_a_command_during_a_select_call_stands(
    hass: HomeAssistant,
) -> None:
    """A new level replaces the re-send, then both real lights are switched
    off at the wall: the level is not sent again over that. The preset still
    lights the strip, which the light reports as any lit room."""
    light, other, select = await _lit_pair(hass)
    await _park_real_resend(hass, light, select)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light", "brightness": 100}
    )
    await _drain(hass)
    commands = record_service_calls(hass)

    for member in (light, other):
        member.wall(on=False)
    await _drain(hass)
    assert hass.states.get("light.selection_light").state == "off"
    await _land_preset(hass, select)

    state = hass.states.get("light.selection_light")
    assert light_targets(commands, "turn_on") == []
    assert not other.is_on
    assert (light.is_on, light.brightness) == (True, PRESET_LEVEL)
    assert (state.state, state.attributes["brightness"]) == ("on", PRESET_LEVEL)


def _select_contexts(hass: HomeAssistant) -> list[Context]:
    """Record the context of each select call."""
    contexts: list[Context] = []

    @callback
    def record_context(event: Event) -> None:
        if event.data["domain"] == "select":
            contexts.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, record_context)
    return contexts


async def _command_strip(
    hass: HomeAssistant,
    light: FadingLight,
    by: str,
    user: User,
    select_context: Context,
) -> None:
    """Set the strip to 77: a named command to it, or a push of its own."""
    if by == "push":
        light.wall(brightness=77)
    else:
        context = {
            "user": Context(user_id=user.id),
            "automation": Context(parent_id="01JAUTOMATIONTRIGGER000000"),
            # An automation the select entity's new option triggered.
            "select_automation": Context(parent_id=select_context.id),
        }[by]
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.ambient", "brightness": 77},
            blocking=True,
            context=context,
        )
    await _drain(hass)
    assert light.brightness == 77


_STRIP_COMMANDS = pytest.mark.parametrize(
    ("by", "theirs"),
    [
        ("user", True),
        ("automation", True),
        ("select_automation", False),
        ("push", False),
    ],
)


@pytest.mark.asyncio
@_STRIP_COMMANDS
async def test_named_command_to_the_strip_replaces_a_waiting_turn_on(
    hass: HomeAssistant, hass_admin_user: User, by: str, theirs: bool
) -> None:
    """A turn-on waits for the select call on the strip's own device. A
    command to the strip that names a user or another automation is theirs
    and replaces the turn-on, like a change at any other real light. A push
    of the strip's own, or an automation the select call set off, is taken
    for the preset, and the turn-on is still sent."""
    light, select = await _preset_strip(hass)
    select.release.clear()
    contexts = _select_contexts(hass)
    await setup_entries(hass, _selection_entry())
    turn_on = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 102},
            blocking=True,
        )
    )
    await asyncio.wait_for(select.started.wait(), 2)

    await _command_strip(hass, light, by, hass_admin_user, contexts[0])
    commands = record_service_calls(hass)
    select.release.set()
    await turn_on
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == "active"
    if theirs:
        assert light_targets(commands, "turn_on") == []
        assert state.attributes["last_on_physical"] is not None
        assert (light.brightness, state.attributes["brightness"]) == (
            PRESET_LEVEL,
            PRESET_LEVEL,
        )
    else:
        assert light_targets(commands, "turn_on") == [["light.ambient"]]
        assert state.attributes["last_on_physical"] is None
        assert (light.brightness, state.attributes["brightness"]) == (102, 102)


@pytest.mark.asyncio
@_STRIP_COMMANDS
async def test_named_command_to_the_strip_replaces_a_waiting_re_send(
    hass: HomeAssistant, hass_admin_user: User, by: str, theirs: bool
) -> None:
    """The same while the strip's re-send waits for its select call: a named
    command to the strip stands, and the re-send is dropped."""
    contexts = _select_contexts(hass)
    light, other, select = await _lit_pair(hass)
    await _park_real_resend(hass, light, select)

    await _command_strip(hass, light, by, hass_admin_user, contexts[-1])
    commands = record_service_calls(hass)
    await _land_preset(hass, select)

    state = hass.states.get("light.selection_light")
    assert state.attributes["molight_state"] == "active"
    if theirs:
        assert light_targets(commands, "turn_on") == []
        assert state.attributes["last_on_physical"] is not None
        assert (light.brightness, other.brightness) == (PRESET_LEVEL, 200)
    else:
        assert light_targets(commands, "turn_on") == [["light.ambient", "light.other"]]
        assert state.attributes["last_on_physical"] is None
        assert (light.brightness, other.brightness) == (200, 200)


async def _tear_down(hass: HomeAssistant, teardown: str) -> None:
    """Take the light's entry away the way the test names."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    if teardown == "unload":
        assert await hass.config_entries.async_unload(entry.entry_id)
    elif teardown == "reload":
        assert await hass.config_entries.async_reload(entry.entry_id)
    elif teardown == "disable":
        assert await hass.config_entries.async_set_disabled_by(
            entry.entry_id, ConfigEntryDisabler.USER
        )
    else:
        assert await hass.config_entries.async_remove(entry.entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("teardown", ["unload", "reload", "disable", "remove"])
@pytest.mark.parametrize("armed_by", ["level", "stage", "blink"])
async def test_torn_down_light_sends_nothing_once_its_select_call_ends(
    hass: HomeAssistant, freezer, teardown: str, armed_by: str
) -> None:
    """A new level or a stage sent during a select call is sent again when
    the call ends, but not by a light that was unloaded, reloaded, disabled
    or removed meanwhile: an off at the wall after that stands."""
    stage = {"warn_timeout": 8, "warn_brightness": 20}
    if armed_by == "blink":
        stage = {"effect_timeout": 8, "effect_brightness": 0}
    light, other, select = await _lit_pair(hass, timeout=5, **stage)
    await _park_real_resend(hass, light, select)
    if armed_by == "level":
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 100},
        )
        await _drain(hass)
    else:
        await _pass(hass, freezer, 6)
        assert _molight_state(hass) == ("warn" if armed_by == "stage" else "effect")

    await _tear_down(hass, teardown)
    await _drain(hass)
    other.wall(on=False)
    await _drain(hass)
    commands = record_service_calls(hass)
    await _land_preset(hass, select)

    assert light_targets(commands, "turn_on") == []
    assert not other.is_on
    state = hass.states.get("light.selection_light")
    if teardown == "reload":
        assert state.state == "on"  # the strip the preset lit
    elif teardown == "remove":
        assert state is None
    else:
        assert state.state == "unavailable"


@pytest.mark.asyncio
async def test_reloaded_light_keeps_its_level_over_the_old_select_call(
    hass: HomeAssistant,
) -> None:
    """The light set up in place of an unloaded one sends a level of its own.
    The old one's select call then ends: nothing is sent over that level."""
    light, other, select = await _lit_pair(hass)
    await _park_real_resend(hass, light, select)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light", "brightness": 100}
    )
    await _drain(hass)
    await _tear_down(hass, "reload")
    await _drain(hass)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.selection_light", "brightness": 60}
    )
    await _drain(hass)
    assert other.brightness == 60
    commands = record_service_calls(hass)
    await _land_preset(hass, select)

    assert light_targets(commands, "turn_on") == []
    assert other.brightness == 60
    assert hass.states.get("light.selection_light").state == "on"


class _QueuedSelect:
    """A select service whose calls are each released on their own."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.started = [asyncio.Event(), asyncio.Event()]
        self.release = [asyncio.Event(), asyncio.Event()]
        self._count = 0
        hass.services.async_register("select", "select_option", self._select)

    async def _select(self, _call: ServiceCall) -> None:
        index = self._count
        self._count += 1
        self.started[index].set()
        await self.release[index].wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("teardown", ["unload", "reload", "disable", "remove"])
@pytest.mark.parametrize("first_done", [1, 0], ids=["newer_first", "older_first"])
async def test_torn_down_light_drops_both_of_two_waiting_turn_ons(
    hass: HomeAssistant, teardown: str, first_done: int
) -> None:
    """Two turn-ons wait for their select calls and one call ends, in either
    order. The light is then torn down and its real light switched off: the
    call still running lights nothing when it ends."""
    lamp = FadingLight("ambient", brightness=255)
    await add_real(hass, lamp)
    select = _QueuedSelect(hass)
    await setup_entries(hass, _selection_entry())
    turn_ons = []
    for index, brightness in enumerate((100, 200)):
        turn_ons.append(
            hass.async_create_task(
                hass.services.async_call(
                    "light",
                    "turn_on",
                    {"entity_id": "light.selection_light", "brightness": brightness},
                    blocking=True,
                )
            )
        )
        await asyncio.wait_for(select.started[index].wait(), 2)
    select.release[first_done].set()
    await turn_ons[first_done]
    await _drain(hass)
    assert lamp.is_on is bool(first_done)

    await _tear_down(hass, teardown)
    await _drain(hass)
    lamp.wall(on=False)
    await _drain(hass)
    commands = record_service_calls(hass)
    select.release[1 - first_done].set()
    await turn_ons[1 - first_done]
    await settle(hass)

    assert light_targets(commands, "turn_on") == []
    assert not lamp.is_on


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "occupancy"])
async def test_preset_landing_as_the_select_call_returns_is_no_wall_turn_on(
    hass: HomeAssistant, trigger: str
) -> None:
    """A preset that lights the strip in the last moment of the select call:
    the strip's report reaches the light just after the call has returned,
    and is still the call's doing."""
    light, _ = await _preset_strip(hass)
    hass.states.async_set("binary_sensor.occ", "off")
    await setup_entries(
        hass, _selection_entry(occupancy="binary_sensor.occ", auto_on_brightness=40)
    )

    if trigger == "manual":
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.selection_light", "brightness": 200},
            blocking=True,
        )
    else:
        hass.states.async_set("binary_sensor.occ", "on")
    await settle(hass)

    state = hass.states.get("light.selection_light")
    assert light.brightness == _LEVEL[trigger]
    assert state.attributes["brightness"] == _LEVEL[trigger]
    assert state.attributes["last_turn_on_selection_option"] == "Cozy"
    assert _wall_stamps(hass) == {}
