"""Member writes under MoLight's own context are judged, not blindly ignored.

Home Assistant keeps a service call's context on the targeted entity for five
seconds, so a human change right after a MoLight command arrives under
MoLight's context. Only writes consistent with the command count as echoes.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.components.light import ColorMode
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.util import color as color_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.molight.const import (
    STATE_ACTIVE,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_WARN,
)
from custom_components.molight.light import _colors_close

from .conftest import make_light_entry, settle

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

MEMBER = "light.living_room"
VIRTUAL = "light.test_light"

HS_TEMP_CAPS = {
    "supported_color_modes": ["hs", "color_temp"],
    "min_color_temp_kelvin": 2202,
    "max_color_temp_kelvin": 6535,
}


async def _setup_color(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up with a color-capable member so color commands reach the light."""
    await _setup(hass, entry)
    hass.states.async_set(MEMBER, "off", HS_TEMP_CAPS)
    await settle(hass)


def _member_contexts(hass: HomeAssistant) -> list[Context]:
    """Capture the context of every light service call aimed at the member."""
    contexts: list[Context] = []

    @callback
    def _record(event: Event) -> None:
        if event.data.get("domain") != "light":
            return
        targets = event.data.get("service_data", {}).get("entity_id", [])
        if MEMBER in targets:
            contexts.append(event.context)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return contexts


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(MEMBER, "off")
    await settle(hass)


async def _virtual(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        "light", service, {"entity_id": VIRTUAL, **data}, blocking=True
    )
    await settle(hass)


def _attrs(hass: HomeAssistant) -> dict:
    return hass.states.get(VIRTUAL).attributes


async def _write(
    hass: HomeAssistant, state: str, context: Context, **attributes
) -> None:
    hass.states.async_set(MEMBER, state, attributes, context=context)
    await settle(hass)


@pytest.mark.asyncio
async def test_matching_echo_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """The member reporting exactly what was asked is our echo."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_on_physical"] is None
    assert _attrs(hass)["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_on_at_brightness_zero_echo_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A dimmer echoing `on` at brightness 0 must not read as an off in disguise."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on")
    await _write(hass, "on", contexts[-1], brightness=0)

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert hass.states.get(VIRTUAL).state == "on"


@pytest.mark.asyncio
async def test_two_part_and_stepwise_echo_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Power first with stale brightness, then fade steps toward the target."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=204, transition=2)
    for brightness in (40, 90, 150, 204):
        await _write(hass, "on", contexts[-1], brightness=brightness)

    assert _attrs(hass)["last_brightness_change_physical"] is None
    assert _attrs(hass)["brightness"] == 204


@pytest.mark.asyncio
async def test_quantised_echo_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A bulb reporting 151 for 153 is still echoing our command."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=151)

    assert _attrs(hass)["last_brightness_change_physical"] is None
    assert _attrs(hass)["brightness"] == 153  # the echo path keeps what we asked


@pytest.mark.asyncio
async def test_wall_switch_on_after_our_off_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """An `on` under our turn-off context contradicts the command: a human did it."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)
    await _virtual(hass, "turn_off")
    await _write(hass, "off", contexts[-1])
    assert _attrs(hass)["molight_state"] == STATE_IDLE

    await _write(hass, "on", contexts[-1], brightness=100)

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_on_physical"] is not None


@pytest.mark.asyncio
async def test_wall_switch_off_while_our_on_settles_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """An `off` while our turn-on still settles contradicts it: a human did it."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=40)  # partial echo, settling

    await _write(hass, "off", contexts[-1])

    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_dim_away_from_target_while_settling_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A brightness moving away from the commanded target is a human dim."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)
    await _virtual(hass, "turn_on", brightness=200)

    await _write(hass, "on", contexts[-1], brightness=100)  # away from 200

    assert _attrs(hass)["last_brightness_change_physical"] is not None
    assert _attrs(hass)["brightness"] == 100


@pytest.mark.asyncio
async def test_power_only_reply_then_fade_steps_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A bulb replying power first with no level, then fade steps, stays our echo."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=204, transition=2)
    await _write(hass, "on", contexts[-1])  # power-only: no brightness attribute
    for brightness in (90, 150, 204):
        await _write(hass, "on", contexts[-1], brightness=brightness)

    assert _attrs(hass)["last_brightness_change_physical"] is None
    assert _attrs(hass)["brightness"] == 204


@pytest.mark.asyncio
async def test_late_power_only_reply_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """An on/off-only member's late reply carries no level and is its full echo."""
    await _setup(hass, light_entry)
    await _virtual(hass, "turn_on", brightness=153)
    _age_expectations(hass, 10)

    await _write(hass, "on", Context())

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_on_physical"] is None


@pytest.mark.asyncio
async def test_dim_after_echo_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Once the echo has landed, a different brightness under our context is human."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)

    await _write(hass, "on", contexts[-1], brightness=100)

    assert _attrs(hass)["last_brightness_change_physical"] is not None
    assert _attrs(hass)["brightness"] == 100


@pytest.mark.asyncio
async def test_dim_during_warning_under_our_context_cancels_it(
    hass: HomeAssistant, freezer
) -> None:
    """A human dim right after the warn command still cancels the warning."""
    entry = make_light_entry(
        name="Test Light",
        lights=[MEMBER],
        timeout=60,
        warn_timeout=30,
        warn_brightness=20,
    )
    await _setup(hass, entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)
    # Run the full timeout out so the state machine enters its warning.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_WARN
    await _write(hass, "on", contexts[-1], brightness=51)  # the warn echo

    await _write(hass, "on", contexts[-1], brightness=120)  # a human dim

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["warning_active"] is False
    assert _attrs(hass)["last_brightness_change_physical"] is not None


@pytest.mark.asyncio
async def test_kelvin_echo_within_tolerance_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A bulb replying 2900 K for a 3000 K command is still echoing it."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, color_temp_kelvin=3000)
    _age_expectations(hass, 10)  # past settling: only a full match counts
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="color_temp",
        color_temp_kelvin=2900,
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["last_color_change_physical"] is None
    assert _attrs(hass)["color_temp_kelvin"] == 3000  # ours kept, not mirrored


@pytest.mark.asyncio
async def test_hue_wraparound_echo_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Hue 3 echoing hue 358 is 5 degrees away across the wrap, not 355."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, hs_color=[358, 80])
    _age_expectations(hass, 10)  # past settling: only a full match counts
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[3, 75],
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["last_color_change_physical"] is None
    assert tuple(_attrs(hass)["hs_color"]) == (358, 80)  # ours kept


@pytest.mark.asyncio
async def test_near_white_echo_ignores_hue(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """At near-zero saturation the hue is noise: any hue still echoes white."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, hs_color=[30, 4])
    _age_expectations(hass, 10)  # past settling: only a full match counts
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[200, 2],
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["last_color_change_physical"] is None
    assert tuple(_attrs(hass)["hs_color"]) == (30, 4)  # ours kept


@pytest.mark.asyncio
async def test_kelvin_command_echoed_as_hs_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """An RGB bulb showing a color temperature reports its hs equivalent."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    hs = color_util.color_RGB_to_hs(*color_util.color_temperature_to_rgb(3000))
    await _virtual(hass, "turn_on", brightness=153, color_temp_kelvin=3000)
    _age_expectations(hass, 10)  # past settling: only a full match counts
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=list(hs),
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["last_color_change_physical"] is None
    assert _attrs(hass)["color_temp_kelvin"] == 3000  # ours kept


@pytest.mark.asyncio
async def test_recolor_while_settling_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A color far from both the command and the old color is a human recolor."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, hs_color=[100, 80])
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[100, 80],
        **HS_TEMP_CAPS,
    )
    await _virtual(hass, "turn_on", brightness=153, hs_color=[240, 80])

    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[10, 80],
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["last_color_change_physical"] is not None
    assert tuple(_attrs(hass)["hs_color"]) == (10, 80)


@pytest.mark.asyncio
async def test_stale_color_while_recolor_settles_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A reply still showing the previous color is the recolor's echo settling."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, hs_color=[100, 80])
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[100, 80],
        **HS_TEMP_CAPS,
    )
    await _virtual(hass, "turn_on", brightness=153, hs_color=[240, 80])

    for hs in ([100, 80], [240, 80]):  # stale first, then the new color lands
        await _write(
            hass,
            "on",
            contexts[-1],
            brightness=153,
            color_mode="hs",
            hs_color=hs,
            **HS_TEMP_CAPS,
        )

    assert _attrs(hass)["last_color_change_physical"] is None
    assert tuple(_attrs(hass)["hs_color"]) == (240, 80)


@pytest.mark.asyncio
async def test_late_color_mismatch_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Past the settle window only a full match counts, color included."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, hs_color=[240, 80])
    _age_expectations(hass, 10)

    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[100, 80],
        **HS_TEMP_CAPS,
    )

    # Treated as a real change: the member's color is mirrored, not ours kept.
    assert tuple(_attrs(hass)["hs_color"]) == (100, 80)


@pytest.mark.asyncio
async def test_color_after_brightness_two_part_echo_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A bulb replying power and level first, then its color, stays our echo."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, hs_color=[240, 80])
    await _write(hass, "on", contexts[-1], brightness=153, **HS_TEMP_CAPS)
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[240, 80],
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["last_color_change_physical"] is None
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE


def _age_expectations(hass: HomeAssistant, seconds: float) -> None:
    virtual = next(
        entity
        for entity in hass.data["entity_components"]["light"].entities
        if entity.entity_id == VIRTUAL
    )
    for expectations in virtual._echo_expectations.values():
        for expectation in expectations:
            expectation.issued -= seconds


@pytest.mark.asyncio
async def test_late_matching_reply_under_its_own_context_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A slow bulb's full reply, well past HA's context reuse, is still our echo."""
    await _setup(hass, light_entry)
    await _virtual(hass, "turn_on", brightness=153)
    _age_expectations(hass, 10)

    await _write(hass, "on", Context(), brightness=150)

    assert _attrs(hass)["brightness"] == 153  # ours kept, not mirrored
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_on_physical"] is None
    assert _attrs(hass)["last_brightness_change_physical"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_context", ["ours", "own"])
async def test_late_on_after_our_off_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry, reply_context: str
) -> None:
    """A silent member reporting on after our off is a human turn-on, not the
    late reply to the turn-on before it, even under the off's context."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on")
    await _virtual(hass, "turn_off")
    _age_expectations(hass, 10)
    context = contexts[-1] if reply_context == "ours" else Context()

    await _write(hass, "on", context, brightness=180)

    assert hass.states.get(VIRTUAL).state == "on"
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_on_physical"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_context", ["ours", "own"])
async def test_late_off_after_our_on_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry, reply_context: str
) -> None:
    """A silent member reporting off after our turn-on is a human turn-off, not
    the late reply to the turn-off before it, even under the turn-on's context."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=200)
    await _write(hass, "on", contexts[-1], brightness=200)
    await _virtual(hass, "turn_off")
    await _virtual(hass, "turn_on")
    _age_expectations(hass, 10)
    context = contexts[-1] if reply_context == "ours" else Context()

    await _write(hass, "off", context)

    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_late_partial_reply_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Past the settle window only a full match counts: a brightness still short
    of the target, even toward it and under our context, is a human dim."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)
    await _virtual(hass, "turn_on", brightness=50)
    _age_expectations(hass, 10)

    await _write(hass, "on", contexts[-1], brightness=100)

    assert _attrs(hass)["last_brightness_change_physical"] is not None
    assert _attrs(hass)["brightness"] == 100


@pytest.mark.asyncio
async def test_foreign_write_while_settling_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """While the command settles, a write under another context is a real change
    even if it would pass as an echo — HA reuses our context for the reply."""
    await _setup(hass, light_entry)
    await _virtual(hass, "turn_on", brightness=153)

    # Within echo tolerance of 153, so only the context tells it apart.
    await _write(hass, "on", Context(), brightness=150)

    assert _attrs(hass)["brightness"] == 150


@pytest.mark.asyncio
async def test_stale_expectation_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A write under our context long after the command is a real change."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    # The member never echoes; age the expectation past the late window.
    _age_expectations(hass, 60)

    await _write(hass, "on", contexts[-1], brightness=151)

    # Treated as a real change: the member's value is mirrored, not ours kept.
    assert _attrs(hass)["brightness"] == 151


@pytest.mark.asyncio
async def test_desaturation_while_settling_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A saturation far from both the command and the old color is human."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, hs_color=[240, 80])
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[240, 80],
        **HS_TEMP_CAPS,
    )
    await _virtual(hass, "turn_on", brightness=153, hs_color=[240, 80])

    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[240, 10],  # same hue, drained saturation
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["last_color_change_physical"] is not None
    assert tuple(_attrs(hass)["hs_color"]) == (240, 10)


@pytest.mark.asyncio
async def test_plain_turn_on_echo_mirrors_member_brightness(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A turn-on naming no level reports the level the member came on at."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on")
    await _write(hass, "on", contexts[-1], brightness=180)

    assert _attrs(hass)["brightness"] == 180
    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_on_physical"] is None
    assert _attrs(hass)["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_plain_turn_on_echo_replaces_stale_brightness(
    hass: HomeAssistant,
) -> None:
    """An occupancy turn-on naming no level drops the last commanded one."""
    hass.states.async_set("binary_sensor.occ", "off")
    entry = make_light_entry(
        name="Test Light", lights=[MEMBER], occupancy="binary_sensor.occ"
    )
    await _setup(hass, entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=50)
    await _write(hass, "on", contexts[-1], brightness=50)
    await _virtual(hass, "turn_off")
    await _write(hass, "off", contexts[-1])

    hass.states.async_set("binary_sensor.occ", "on")
    await settle(hass)
    await _write(hass, "on", contexts[-1], brightness=200)

    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _attrs(hass)["brightness"] == 200
    assert _attrs(hass)["last_on_physical"] is None


@pytest.mark.asyncio
async def test_plain_turn_on_echo_at_brightness_zero_keeps_brightness(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A dimmer echoing `on` with no level yet leaves the reported level alone."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)
    await _virtual(hass, "turn_off")
    await _write(hass, "off", contexts[-1])

    await _virtual(hass, "turn_on")
    await _write(hass, "on", contexts[-1], brightness=0)

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["brightness"] == 153


@pytest.mark.asyncio
async def test_plain_turn_on_echo_mirrors_member_color(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A turn-on naming no color reports the color the member came on at."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[240, 80],
        **HS_TEMP_CAPS,
    )

    assert tuple(_attrs(hass)["hs_color"]) == (240, 80)
    assert _attrs(hass)["brightness"] == 153
    assert _attrs(hass)["last_color_change_physical"] is None


@pytest.mark.asyncio
async def test_turn_off_echo_mirrors_nothing(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """An off echo in disguise (on at brightness 0) is not read as a level."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)
    await _virtual(hass, "turn_off")
    await _write(hass, "on", contexts[-1], brightness=0)

    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["molight_state"] == STATE_IDLE


async def _into_warn(
    hass: HomeAssistant, freezer, **entry_kwargs
) -> tuple[Context, Context]:
    """Light on at 200, run into EFFECT then WARN with no member reply yet."""
    entry = make_light_entry(
        name="Test Light", lights=[MEMBER], timeout=60, **entry_kwargs
    )
    await _setup(hass, entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=200)
    await _write(hass, "on", contexts[-1], brightness=200)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_EFFECT
    effect_context = contexts[-1]

    freezer.tick(timedelta(seconds=2.5))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_WARN
    assert contexts[-1] is not effect_context
    return effect_context, contexts[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_context", ["effect", "warn", "own"])
async def test_late_effect_dim_reply_does_not_abort_warn(
    hass: HomeAssistant, freezer, reply_context: str
) -> None:
    """The effect dim reported after the warn command is still the effect's echo.

    Home Assistant stamps the newest command's context on a reply, so the late
    reply is tried under the effect's, the warn's and a context of its own.
    """
    effect_context, warn_context = await _into_warn(
        hass,
        freezer,
        effect_timeout=2,
        effect_brightness=10,
        warn_timeout=30,
        warn_brightness=50,
    )
    if reply_context == "own":
        _age_expectations(hass, 10)
    context = {"effect": effect_context, "warn": warn_context, "own": Context()}[
        reply_context
    ]

    await _write(hass, "on", context, brightness=26)
    assert _attrs(hass)["molight_state"] == STATE_WARN
    await _write(hass, "on", warn_context, brightness=128)

    assert _attrs(hass)["molight_state"] == STATE_WARN
    assert _attrs(hass)["brightness"] == 128
    assert _attrs(hass)["last_brightness_change_physical"] is None
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_context", ["effect", "warn"])
async def test_late_effect_blink_off_reply_does_not_abort_warn(
    hass: HomeAssistant, freezer, reply_context: str
) -> None:
    """A blink-off reported after the warn command is not a human turn-off,
    and the warn reply that follows is not a human turn-on."""
    effect_context, warn_context = await _into_warn(
        hass, freezer, effect_timeout=2, warn_timeout=30, warn_brightness=50
    )
    context = effect_context if reply_context == "effect" else warn_context

    await _write(hass, "off", context)
    assert _attrs(hass)["molight_state"] == STATE_WARN
    await _write(hass, "on", warn_context, brightness=128)

    assert _attrs(hass)["molight_state"] == STATE_WARN
    assert _attrs(hass)["last_on_physical"] is None


@pytest.mark.asyncio
async def test_in_order_stage_replies_keep_the_sequence(
    hass: HomeAssistant, freezer
) -> None:
    """Each stage answered before the next begins runs through to the warn."""
    entry = make_light_entry(
        name="Test Light",
        lights=[MEMBER],
        timeout=60,
        effect_timeout=2,
        effect_brightness=10,
        warn_timeout=30,
        warn_brightness=50,
    )
    await _setup(hass, entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=200)
    await _write(hass, "on", contexts[-1], brightness=200)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    await _write(hass, "on", contexts[-1], brightness=26)
    assert _attrs(hass)["molight_state"] == STATE_EFFECT
    freezer.tick(timedelta(seconds=3))
    async_fire_time_changed(hass)
    await settle(hass)
    await _write(hass, "on", contexts[-1], brightness=128)

    assert _attrs(hass)["molight_state"] == STATE_WARN
    assert _attrs(hass)["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_late_reply_to_restore_after_warn_is_not_physical(
    hass: HomeAssistant, freezer
) -> None:
    """The warn reply trailing the restore command is not a human dim."""
    entry = make_light_entry(
        name="Test Light",
        lights=[MEMBER],
        timeout=60,
        warn_timeout=30,
        warn_brightness=20,
    )
    await _setup(hass, entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=200)
    await _write(hass, "on", contexts[-1], brightness=200)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_WARN
    await _virtual(hass, "turn_on")  # re-trigger: restores the 200

    await _write(hass, "on", contexts[-1], brightness=51)
    await _write(hass, "on", contexts[-1], brightness=200)

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["brightness"] == 200
    assert _attrs(hass)["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_reply_to_newest_command_settles_the_older_ones(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Once the newest command is answered, an older level is a human dim."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=100)
    await _virtual(hass, "turn_on", brightness=200)
    await _write(hass, "on", contexts[-1], brightness=200)

    await _write(hass, "on", contexts[-1], brightness=100)

    assert _attrs(hass)["brightness"] == 100
    assert _attrs(hass)["last_brightness_change_physical"] is not None


@pytest.mark.asyncio
async def test_change_contradicting_every_command_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A level matching none of the unanswered commands is a human dim, and
    it settles them all."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=200)
    await _write(hass, "on", contexts[-1], brightness=200)
    await _virtual(hass, "turn_on", brightness=100)
    await _virtual(hass, "turn_on", brightness=150)

    await _write(hass, "on", contexts[-1], brightness=250)
    assert _attrs(hass)["brightness"] == 250
    assert _attrs(hass)["last_brightness_change_physical"] is not None

    await _write(hass, "on", contexts[-1], brightness=100)
    assert _attrs(hass)["brightness"] == 100


@pytest.mark.asyncio
async def test_late_plain_reply_keeps_newer_commanded_brightness(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A plain turn-on's late reply does not overwrite a newer commanded level."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on")
    await _virtual(hass, "turn_on", brightness=200)

    await _write(hass, "on", contexts[-1], brightness=80)
    assert _attrs(hass)["brightness"] == 200
    await _write(hass, "on", contexts[-1], brightness=200)

    assert _attrs(hass)["brightness"] == 200
    assert _attrs(hass)["last_brightness_change_physical"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("context", ["own", "foreign"])
async def test_dim_after_unanswered_plain_turn_on_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry, context: str
) -> None:
    """A plain turn-on changes nothing on a lit member, so no reply is awaited
    and a dim that follows is a human one."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _write(hass, "on", Context(), brightness=200)
    assert _attrs(hass)["brightness"] == 200

    await _virtual(hass, "turn_on")
    _age_expectations(hass, 10)
    await _write(
        hass, "on", contexts[-1] if context == "own" else Context(), brightness=80
    )

    assert _attrs(hass)["brightness"] == 80
    assert _attrs(hass)["last_brightness_change_physical"] is not None


@pytest.mark.asyncio
async def test_turn_on_after_unanswered_turn_off_of_dark_member_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A turn-off changes nothing on a dark member, so a later off in disguise
    is not awaited as its reply."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_off")
    virtual = next(
        entity
        for entity in hass.data["entity_components"]["light"].entities
        if entity.entity_id == VIRTUAL
    )
    assert not virtual._echo_expectations

    await _write(hass, "on", contexts[-1], brightness=120)

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_on_physical"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("member_state", ["unavailable", "unknown", None])
async def test_plain_turn_on_of_unreadable_member_awaits_its_reply(
    hass: HomeAssistant, light_entry: MockConfigEntry, member_state: str | None
) -> None:
    """A member whose power is not known may still answer a plain turn-on."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    if member_state is None:
        hass.states.async_remove(MEMBER)
    else:
        hass.states.async_set(MEMBER, member_state)
    await settle(hass)

    await _virtual(hass, "turn_on")
    await _write(hass, "on", contexts[-1], brightness=180)

    assert _attrs(hass)["brightness"] == 180
    assert _attrs(hass)["last_on_physical"] is None


@pytest.mark.asyncio
async def test_plain_turn_on_of_member_on_at_zero_awaits_its_reply(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A member on at brightness 0 is dark, so a plain turn-on changes it."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _write(hass, "on", Context(), brightness=0)

    await _virtual(hass, "turn_on")
    await _write(hass, "on", contexts[-1], brightness=180)

    assert _attrs(hass)["brightness"] == 180
    assert _attrs(hass)["last_brightness_change_physical"] is None


@pytest.mark.asyncio
async def test_plain_turn_on_awaits_only_the_dark_member(
    hass: HomeAssistant,
) -> None:
    """With one member lit and one dark, only the dark one owes a reply."""
    other = "light.kitchen"
    entry = make_light_entry(name="Test Light", lights=[MEMBER, other])
    await _setup(hass, entry)
    hass.states.async_set(other, "on", {"brightness": 200})
    await settle(hass)
    contexts = _member_contexts(hass)
    adopted = _attrs(hass)["last_on_physical"]

    await _virtual(hass, "turn_on")
    _age_expectations(hass, 10)
    hass.states.async_set(other, "on", {"brightness": 80}, context=contexts[-1])
    await settle(hass)
    assert _attrs(hass)["last_brightness_change_physical"] is not None
    assert _attrs(hass)["brightness"] == 80

    changed = _attrs(hass)["last_brightness_change_physical"]
    await _write(hass, "on", contexts[-1], brightness=150)
    assert _attrs(hass)["last_on_physical"] == adopted
    assert _attrs(hass)["last_brightness_change_physical"] == changed


def _kelvin(value: int) -> tuple:
    return (ColorMode.COLOR_TEMP, value)


def _hs(hue: float, saturation: float) -> tuple:
    return (ColorMode.HS, (hue, saturation))


@pytest.mark.parametrize(
    ("commanded", "reported", "close"),
    [
        (_kelvin(3000), _kelvin(3150), True),
        (_kelvin(3000), _kelvin(2850), True),
        (_kelvin(3000), _kelvin(3151), False),
        (_kelvin(3000), _kelvin(2849), False),
        (_hs(100, 80), _hs(110, 80), True),
        (_hs(100, 80), _hs(90, 80), True),
        (_hs(100, 80), _hs(111, 80), False),
        (_hs(100, 80), _hs(89, 80), False),
        (_hs(358, 80), _hs(8, 80), True),  # 10 degrees across the wrap
        (_hs(8, 80), _hs(358, 80), True),
        (_hs(358, 80), _hs(9, 80), False),
        (_hs(100, 80), _hs(100, 90), True),
        (_hs(100, 80), _hs(100, 70), True),
        (_hs(100, 80), _hs(100, 91), False),
        (_hs(100, 80), _hs(100, 69), False),
        (_hs(30, 10), _hs(200, 10), True),  # both near white: hue is noise
        (_hs(30, 4), _hs(200, 11), False),  # only one near white
        (_hs(30, 11), _hs(200, 4), False),
        (_hs(30, 0), _hs(200, 11), False),  # too far apart in saturation
    ],
)
def test_colors_close_tolerances(
    commanded: tuple, reported: tuple, close: bool
) -> None:
    """Each tolerance holds at its limit and fails just beyond it."""
    assert _colors_close(reported, commanded) is close


def test_colors_close_across_color_modes() -> None:
    """A color temperature is compared with an hs color by its hs equivalent."""
    hs = color_util.color_RGB_to_hs(*color_util.color_temperature_to_rgb(3000))

    assert _colors_close(_hs(*hs), _kelvin(3000))
    assert _colors_close(_kelvin(3000), _hs(*hs))
    assert not _colors_close(_hs(hs[0], hs[1] + 11), _kelvin(3000))
    assert not _colors_close(_kelvin(3000), _hs(hs[0] + 11, hs[1]))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "reply"),
    [
        (
            {"color_temp_kelvin": 3000},
            {"color_mode": "color_temp", "color_temp_kelvin": 2840},
        ),
        ({"hs_color": [358, 80]}, {"color_mode": "hs", "hs_color": [9, 80]}),
        ({"hs_color": [100, 80]}, {"color_mode": "hs", "hs_color": [100, 69]}),
        ({"hs_color": [30, 4]}, {"color_mode": "hs", "hs_color": [200, 11]}),
        ({"color_temp_kelvin": 3000}, {"color_mode": "hs", "hs_color": [240, 80]}),
    ],
)
async def test_late_reply_outside_color_tolerance_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry, command: dict, reply: dict
) -> None:
    """Past the settle window a color just outside the tolerance is a real
    change: the member's color is mirrored, not ours kept."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, **command)
    _age_expectations(hass, 10)

    await _write(hass, "on", contexts[-1], brightness=153, **reply, **HS_TEMP_CAPS)

    if "hs_color" in reply:
        assert tuple(_attrs(hass)["hs_color"]) == tuple(reply["hs_color"])
    else:
        assert _attrs(hass)["color_temp_kelvin"] == reply["color_temp_kelvin"]
