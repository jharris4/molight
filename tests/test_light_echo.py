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
from homeassistant.setup import async_setup_component
from homeassistant.util import color as color_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.molight.const import (
    CONF_AUTO_OFF_TRANSITION,
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_AUTO_ON_RGB_COLOR,
    CONF_AUTO_ON_TRANSITION,
    CONF_ENTITY_TYPE,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_STANDBY_BRIGHTNESS,
    CONF_STANDBY_RGB_COLOR,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_RGB_COLOR,
    CONF_WARN_TIMEOUT,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    STATE_ACTIVE,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_STANDBY,
    STATE_WARN,
)
from custom_components.molight.light import _color_toward, _colors_close

from .conftest import (
    make_light_entry,
    make_scheduled_light_entry,
    settle,
    setup_entries,
)
from .real_entities import FadingLight, InstantLight, RealLight, add_real

if TYPE_CHECKING:
    from homeassistant.helpers.entity import Entity

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
async def test_kelvin_fade_step_toward_target_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A bulb reporting a kelvin between its old color and the command during
    the requested transition is fading, not being recolored."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, color_temp_kelvin=4000)
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="color_temp",
        color_temp_kelvin=4000,
        **HS_TEMP_CAPS,
    )
    await _virtual(hass, "turn_on", color_temp_kelvin=3000, transition=4)

    for kelvin in (3500, 3200, 3000):
        await _write(
            hass,
            "on",
            contexts[-1],
            brightness=153,
            color_mode="color_temp",
            color_temp_kelvin=kelvin,
            **HS_TEMP_CAPS,
        )

    assert _attrs(hass)["last_color_change_physical"] is None
    assert _attrs(hass)["color_temp_kelvin"] == 3000


@pytest.mark.asyncio
async def test_kelvin_step_away_from_target_while_settling_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A kelvin moving away from the command is a human recolor."""
    await _setup_color(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153, color_temp_kelvin=4000)
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="color_temp",
        color_temp_kelvin=4000,
        **HS_TEMP_CAPS,
    )
    await _virtual(hass, "turn_on", color_temp_kelvin=3000, transition=4)

    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="color_temp",
        color_temp_kelvin=4500,
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["last_color_change_physical"] is not None
    assert _attrs(hass)["color_temp_kelvin"] == 4500


@pytest.mark.asyncio
async def test_hue_fade_step_along_the_arc_is_not_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """A hue on the short arc from the old color to the command is a fade step."""
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
    await _virtual(hass, "turn_on", hs_color=[240, 80], transition=4)

    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="hs",
        hs_color=[170, 80],
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["last_color_change_physical"] is None
    assert tuple(_attrs(hass)["hs_color"]) == (240, 80)


@pytest.mark.asyncio
async def test_member_clamped_kelvin_reply_is_not_physical(
    hass: HomeAssistant,
) -> None:
    """A member whose range is narrower than the virtual light's reports the
    kelvin it clamped our command to; that reply is still its echo."""
    warmer = "light.warmer"
    entry = make_light_entry(name="Test Light", lights=[MEMBER, warmer])
    await _setup(hass, entry)
    hass.states.async_set(MEMBER, "off", HS_TEMP_CAPS)
    hass.states.async_set(
        warmer, "off", {**HS_TEMP_CAPS, "min_color_temp_kelvin": 2000}
    )
    await settle(hass)
    assert _attrs(hass)["min_color_temp_kelvin"] == 2000
    contexts = _member_contexts(hass)

    await _virtual(hass, "turn_on", brightness=153, color_temp_kelvin=2050)
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=153,
        color_mode="color_temp",
        color_temp_kelvin=2202,
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["last_color_change_physical"] is None
    assert _attrs(hass)["color_temp_kelvin"] == 2050


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("commanded", "reply", "ours"),
    [(2050, 2202, True), (2050, 2500, False), (6500, 6000, True), (6500, 5700, False)],
)
async def test_late_kelvin_reply_is_judged_against_the_member_range(
    hass: HomeAssistant, commanded: int, reply: int, ours: bool
) -> None:
    """Past the settle window only a full match is an echo. For a member
    whose range ends short of the command, that is the nearest kelvin it has;
    one further in is someone's choice."""
    wider = "light.wider"
    entry = make_light_entry(name="Test Light", lights=[MEMBER, wider])
    await _setup(hass, entry)
    narrow = {**HS_TEMP_CAPS, "max_color_temp_kelvin": 6000}
    hass.states.async_set(MEMBER, "off", narrow)
    hass.states.async_set(wider, "off", {**HS_TEMP_CAPS, "min_color_temp_kelvin": 2000})
    await settle(hass)

    await _virtual(hass, "turn_on", brightness=153, color_temp_kelvin=commanded)
    _age_expectations(hass, 10)
    await _write(
        hass,
        "on",
        Context(),
        brightness=153,
        color_mode="color_temp",
        color_temp_kelvin=reply,
        **narrow,
    )

    assert (_attrs(hass)["last_on_physical"] is None) is ours
    assert _attrs(hass)["color_temp_kelvin"] == (commanded if ours else reply)


@pytest.mark.asyncio
async def test_effect_kelvin_fade_reply_keeps_the_sequence(
    hass: HomeAssistant, freezer
) -> None:
    """A bulb reporting mid-fade during the effect's transition does not
    re-trigger the light."""
    entry = make_light_entry(
        name="Test Light",
        lights=[MEMBER],
        timeout=60,
        effect_timeout=8,
        effect_brightness=80,  # percent: 204
        effect_color_temp=3000,
        effect_transition=4,
        warn_timeout=0,
    )
    await _setup(hass, entry)
    hass.states.async_set(MEMBER, "off", HS_TEMP_CAPS)
    await settle(hass)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=200, color_temp_kelvin=4000)
    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=200,
        color_mode="color_temp",
        color_temp_kelvin=4000,
        **HS_TEMP_CAPS,
    )

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_EFFECT

    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=204,
        color_mode="color_temp",
        color_temp_kelvin=3500,
        **HS_TEMP_CAPS,
    )
    assert _attrs(hass)["molight_state"] == STATE_EFFECT

    freezer.tick(timedelta(seconds=9))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _attrs(hass)["molight_state"] == STATE_IDLE


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
@pytest.mark.parametrize(("age", "brightness"), [(3, 50), (4, 100)])
async def test_partial_reply_counts_up_to_the_settle_edge(
    hass: HomeAssistant, light_entry: MockConfigEntry, freezer, age, brightness
) -> None:
    """A partial reply is an echo up to exactly the end of the settle window."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    await _write(hass, "on", contexts[-1], brightness=153)
    await _virtual(hass, "turn_on", brightness=50)
    _age_expectations(hass, age)

    await _write(hass, "on", contexts[-1], brightness=100)

    assert _attrs(hass)["brightness"] == brightness


@pytest.mark.asyncio
@pytest.mark.parametrize(("age", "brightness"), [(30, 153), (31, 151)])
async def test_matching_reply_counts_up_to_the_late_edge(
    hass: HomeAssistant, light_entry: MockConfigEntry, freezer, age, brightness
) -> None:
    """A matching reply is an echo up to exactly the end of the late window."""
    await _setup(hass, light_entry)
    contexts = _member_contexts(hass)
    await _virtual(hass, "turn_on", brightness=153)
    _age_expectations(hass, age)

    await _write(hass, "on", contexts[-1], brightness=151)

    assert _attrs(hass)["brightness"] == brightness


@pytest.mark.asyncio
async def test_foreign_write_while_settling_is_physical(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """While the command settles, a write under another context is a real change
    even if it would pass as an echo; HA reuses our context for the reply."""
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


def _rgb_blend(start: tuple, goal: tuple, fraction: float) -> tuple:
    """A straight line through RGB, reported in 8-bit channels."""
    channels = zip(
        color_util.color_hs_to_RGB(*start),
        color_util.color_hs_to_RGB(*goal),
        strict=True,
    )
    return color_util.color_RGB_to_hs(
        *(round(a + (b - a) * fraction) for a, b in channels)
    )


def _xy_blend(start: tuple, goal: tuple, fraction: float) -> tuple:
    """A straight line through CIE xy."""
    points = zip(
        color_util.color_hs_to_xy(*start), color_util.color_hs_to_xy(*goal), strict=True
    )
    return color_util.color_xy_to_hs(*(a + (b - a) * fraction for a, b in points))


def _hue_blend(start: tuple, goal: tuple, fraction: float) -> tuple:
    """The short way round the hue wheel, the saturation in step."""
    turn = (goal[0] - start[0] + 180) % 360 - 180
    return (
        (start[0] + turn * fraction) % 360,
        start[1] + (goal[1] - start[1]) * fraction,
    )


BLENDS = {"rgb": _rgb_blend, "xy": _xy_blend, "hue": _hue_blend}
WARM_WHITE = color_util.color_RGB_to_hs(*color_util.color_temperature_to_rgb(2700))
RED, ORANGE, YELLOW, GREEN = (0, 100), (30, 100), (60, 100), (120, 100)
CYAN, BLUE, PURPLE = (180, 100), (240, 100), (280, 100)


@pytest.mark.parametrize("blend", BLENDS)
@pytest.mark.parametrize(
    ("start", "goal"),
    [
        (WARM_WHITE, BLUE),
        (WARM_WHITE, RED),
        (RED, CYAN),
        (BLUE, YELLOW),
        (BLUE, ORANGE),
        (PURPLE, GREEN),
        (ORANGE, PURPLE),
        (RED, ORANGE),
        (RED, GREEN),
        (RED, BLUE),
        ((0, 80), (30, 80)),
        ((0, 80), (60, 80)),
        ((0, 80), (120, 80)),
        ((0, 80), (240, 80)),
    ],
)
def test_fade_step_counts_however_the_member_blends(
    blend: str, start: tuple, goal: tuple
) -> None:
    """A member cutting across the wheel passes through paler colors; each
    step is progress, judged from the start and from the step before."""
    steps = [BLENDS[blend](start, goal, fraction) for fraction in (0.25, 0.5, 0.75)]

    for before, step in zip([start, *steps], steps, strict=False):
        assert _color_toward(_hs(*start), _hs(*step), _hs(*goal))
        assert _color_toward(_hs(*before), _hs(*step), _hs(*goal))


def test_fade_step_from_a_color_temperature_counts() -> None:
    """A member leaving its color temperature mode reports the fade in hs."""
    for blend in BLENDS.values():
        step = blend(WARM_WHITE, BLUE, 0.5)
        assert _color_toward(_kelvin(2700), _hs(*step), _hs(*BLUE))


@pytest.mark.parametrize(
    ("old", "new", "target"),
    [
        ((100, 80), (95, 80), (240, 80)),  # backs away from the target
        ((100, 80), (10, 80), (240, 80)),  # an unrelated hue
        ((240, 80), (240, 10), (240, 80)),  # no fade to be a step of
        (RED, (300, 50), CYAN),  # beside every way from red to cyan
        (RED, (300, 30), BLUE),  # paler than any blend through magenta
        (RED, (80, 100), (40, 100)),  # past the target
        (_rgb_blend(BLUE, YELLOW, 0.5), (240, 50), YELLOW),  # back toward blue
    ],
)
def test_color_off_the_fade_is_not_a_fade_step(
    old: tuple, new: tuple, target: tuple
) -> None:
    """A color away from the target, or beside the way there, is a recolor."""
    assert not _color_toward(_hs(*old), _hs(*new), _hs(*target))


class _FadingMember:
    """A member that reports each fade in steps, blending colors its own way."""

    def __init__(self, hass: HomeAssistant, blend: str) -> None:
        self._hass = hass
        self._blend = BLENDS[blend]
        self._commands: list[Event] = []
        self._brightness = 255
        self._hs: tuple | None = None
        hass.bus.async_listen(EVENT_CALL_SERVICE, self._record)

    @callback
    def _record(self, event: Event) -> None:
        targets = event.data.get("service_data", {}).get("entity_id", [])
        if event.data.get("domain") == "light" and MEMBER in targets:
            self._commands.append(event)

    async def answer(self) -> None:
        """Report the fade of every command received since the last answer."""
        commands, self._commands = self._commands, []
        for command in commands:
            if command.data["service"] == "turn_off":
                await _write(self._hass, "off", command.context, **HS_TEMP_CAPS)
                continue
            data = command.data["service_data"]
            was_on = self._hass.states.get(MEMBER).state == "on"
            start_hs, start_brightness = self._hs, self._brightness
            goal_hs = self._commanded_hs(data) or start_hs
            goal_brightness = data.get("brightness", start_brightness)
            for fraction in (0.25, 0.5, 0.75, 1) if was_on else (1,):
                self._hs = goal_hs
                if fraction < 1 and start_hs and goal_hs != start_hs:
                    self._hs = self._blend(start_hs, goal_hs, fraction)
                self._brightness = round(
                    start_brightness + (goal_brightness - start_brightness) * fraction
                )
                color = {"color_mode": "hs", "hs_color": self._hs} if self._hs else {}
                await _write(
                    self._hass,
                    "on",
                    command.context,
                    brightness=self._brightness,
                    **color,
                    **HS_TEMP_CAPS,
                )

    @staticmethod
    def _commanded_hs(data: dict) -> tuple | None:
        if "hs_color" in data:
            return tuple(data["hs_color"])
        if "rgb_color" in data:
            return color_util.color_RGB_to_hs(*data["rgb_color"])
        return None


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


async def _lit_red(hass: HomeAssistant, blend: str, **entry_kwargs) -> _FadingMember:
    """A light turned on red at 200 whose member has reported it."""
    entry = make_light_entry(
        name="Test Light", lights=[MEMBER], timeout=60, **entry_kwargs
    )
    await _setup_color(hass, entry)
    member = _FadingMember(hass, blend)
    await _virtual(hass, "turn_on", brightness=200, hs_color=list(RED))
    await member.answer()
    return member


@pytest.mark.asyncio
@pytest.mark.parametrize("blend", BLENDS)
@pytest.mark.parametrize("stage", [STATE_EFFECT, STATE_WARN])
async def test_stage_color_fade_across_the_wheel_keeps_the_sequence(
    hass: HomeAssistant, freezer, blend: str, stage: str
) -> None:
    """A stage fading from red to cyan is not cancelled by its own fade."""
    stages = {"effect_timeout": 0, "warn_timeout": 0} | {
        f"{stage}_timeout": 10,
        f"{stage}_brightness": 80,
        f"{stage}_rgb_color": [0, 255, 255],
        f"{stage}_transition": 4,
    }
    member = await _lit_red(hass, blend, **stages)

    await _tick(hass, freezer, 61)
    await member.answer()

    assert _attrs(hass)["molight_state"] == stage
    assert _attrs(hass)["last_color_change_physical"] is None
    assert tuple(_attrs(hass)["hs_color"]) == CYAN
    await _tick(hass, freezer, 11)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("blend", BLENDS)
async def test_far_apart_effect_and_warn_colors_still_turn_the_light_off(
    hass: HomeAssistant, freezer, blend: str
) -> None:
    """Blue then yellow: neither stage's fade restarts the timeout, which
    would otherwise repeat on every sequence and keep the light on."""
    member = await _lit_red(
        hass,
        blend,
        effect_timeout=5,
        effect_brightness=80,
        effect_rgb_color=[0, 0, 255],
        effect_transition=2,
        warn_timeout=5,
        warn_brightness=50,
        warn_rgb_color=[255, 255, 0],
        warn_transition=2,
    )

    seen = set()
    for _ in range(75):
        await _tick(hass, freezer, 1)
        await member.answer()
        seen.add(_attrs(hass)["molight_state"])

    assert {STATE_EFFECT, STATE_WARN} <= seen
    assert hass.states.get(VIRTUAL).state == "off"
    assert _attrs(hass)["last_color_change_physical"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("blend", BLENDS)
async def test_warn_undoing_the_effect_color_keeps_the_sequence(
    hass: HomeAssistant, freezer, blend: str
) -> None:
    """A warn stage with no color fades the effect's cyan back to red."""
    member = await _lit_red(
        hass,
        blend,
        effect_timeout=5,
        effect_brightness=80,
        effect_rgb_color=[0, 255, 255],
        warn_timeout=10,
        warn_brightness=50,
    )
    await _tick(hass, freezer, 61)
    await member.answer()
    assert _attrs(hass)["molight_state"] == STATE_EFFECT

    await _tick(hass, freezer, 6)
    await member.answer()

    assert _attrs(hass)["molight_state"] == STATE_WARN
    assert _attrs(hass)["last_color_change_physical"] is None
    assert tuple(_attrs(hass)["hs_color"]) == RED
    await _tick(hass, freezer, 11)
    assert hass.states.get(VIRTUAL).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("blend", BLENDS)
async def test_restoring_the_pre_warning_color_is_not_a_recolor(
    hass: HomeAssistant, freezer, blend: str
) -> None:
    """A re-trigger fades the warning's cyan back to red; that is no recolor."""
    member = await _lit_red(
        hass, blend, warn_timeout=10, warn_brightness=50, warn_rgb_color=[0, 255, 255]
    )
    await _tick(hass, freezer, 61)
    await member.answer()
    assert _attrs(hass)["molight_state"] == STATE_WARN

    await _virtual(hass, "turn_on")
    await member.answer()

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_color_change_physical"] is None
    assert _attrs(hass)["last_brightness_change_physical"] is None
    assert tuple(_attrs(hass)["hs_color"]) == RED
    assert _attrs(hass)["brightness"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("blend", BLENDS)
async def test_recolor_beside_a_stage_fade_still_cancels_the_warning(
    hass: HomeAssistant, freezer, blend: str
) -> None:
    """Partway through the fade to cyan, a color off its way is still human."""
    await _lit_red(
        hass,
        blend,
        warn_timeout=10,
        warn_brightness=80,
        warn_rgb_color=[0, 255, 255],
        warn_transition=4,
    )
    contexts = _member_contexts(hass)
    await _tick(hass, freezer, 61)
    assert _attrs(hass)["molight_state"] == STATE_WARN
    step = {"color_mode": "hs", "hs_color": BLENDS[blend](RED, CYAN, 0.25)}
    await _write(hass, "on", contexts[-1], brightness=201, **step, **HS_TEMP_CAPS)
    assert _attrs(hass)["molight_state"] == STATE_WARN

    await _write(
        hass,
        "on",
        contexts[-1],
        brightness=201,
        color_mode="hs",
        hs_color=(300, 50),
        **HS_TEMP_CAPS,
    )

    assert _attrs(hass)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass)["last_color_change_physical"] is not None
    assert tuple(_attrs(hass)["hs_color"]) == (300, 50)


@pytest.mark.asyncio
@pytest.mark.parametrize("blend", BLENDS)
async def test_standby_and_auto_on_color_fades_are_not_recolors(
    hass: HomeAssistant, freezer, blend: str
) -> None:
    """Standby red, raised to blue, warned in green, back to red at standby:
    each fade leaves the on-period with whoever had it."""
    schedule, occupancy = "binary_sensor.settings_schedule", "binary_sensor.motion"
    entry = make_scheduled_light_entry(
        name="Test Light",
        lights=[MEMBER],
        inside={
            CONF_LIGHT_TIMEOUT: 30,
            CONF_OCCUPANCY_ENTITY: occupancy,
            CONF_AUTO_ON_BRIGHTNESS: 100,
            CONF_AUTO_ON_RGB_COLOR: [51, 51, 255],
            CONF_STANDBY_BRIGHTNESS: 20,
            CONF_STANDBY_RGB_COLOR: [255, 51, 51],
            CONF_WARN_TIMEOUT: 10,
            CONF_WARN_BRIGHTNESS: 50,
            CONF_WARN_RGB_COLOR: [51, 255, 51],
        },
    )
    hass.states.async_set(schedule, "on")
    hass.states.async_set(occupancy, "off")
    hass.states.async_set(MEMBER, "off", HS_TEMP_CAPS)
    member = _FadingMember(hass, blend)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    await member.answer()
    assert _attrs(hass)["molight_state"] == STATE_STANDBY

    hass.states.async_set(occupancy, "on")
    await settle(hass)
    await member.answer()
    assert _attrs(hass)["molight_state"] == STATE_OCCUPIED
    assert _attrs(hass)["last_color_change_physical"] is None
    assert tuple(_attrs(hass)["hs_color"]) == (240, 80)

    hass.states.async_set(occupancy, "off")
    await settle(hass)
    await _tick(hass, freezer, 31)
    await member.answer()
    assert _attrs(hass)["molight_state"] == STATE_WARN
    assert tuple(_attrs(hass)["hs_color"]) == (120, 80)

    await _tick(hass, freezer, 11)
    await member.answer()
    assert _attrs(hass)["molight_state"] == STATE_STANDBY
    assert _attrs(hass)["last_color_change_physical"] is None
    assert _attrs(hass)["brightness"] == 51
    assert tuple(_attrs(hass)["hs_color"]) == (0, 80)


# ---------------------------------------------------------------------------
# Real lights that reply inside the service call
#
# Home Assistant starts a service call at once, so a light that writes its
# new state without waiting for anything (LightwaveRF, the demo light, a
# light group of those, a virtual light) has replied before the call returns.
# ---------------------------------------------------------------------------

INSTANT = "light.matrix_light"


def _reports_of(hass: HomeAssistant, entity_id: str) -> list[tuple[str, int | None]]:
    """Record each (state, brightness) the entity publishes from here on."""
    seen: list[tuple[str, int | None]] = []

    @callback
    def record(event: Event) -> None:
        state = event.data["new_state"]
        if event.data["entity_id"] == entity_id and state is not None:
            seen.append((state.state, state.attributes.get("brightness")))

    hass.bus.async_listen("state_changed", record)
    return seen


async def _light_group(hass: HomeAssistant, member: Entity) -> str:
    """Wrap the member in Home Assistant's own light group."""
    assert await async_setup_component(
        hass,
        "light",
        {"light": [{"platform": "group", "name": "Grp", "entities": ["light.real_1"]}]},
    )
    await add_real(hass, member)
    return "light.grp"


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("member", ["instant", "instant_group", "after_the_call"])
@pytest.mark.parametrize("trigger", ["manual", "manual_level", "occupancy", "level"])
async def test_reply_inside_the_turn_on_call_never_reports_off(
    hass: HomeAssistant, member: str, trigger: str
) -> None:
    """The virtual light counts as on before its command is sent, so a reply
    mirrored inside the call is published as on, never as off."""
    lights = ["light.real_1"]
    if member == "instant_group":
        lights = [await _light_group(hass, InstantLight("real_1", kelvin=3000))]
    elif member == "instant":
        await add_real(hass, InstantLight("real_1", kelvin=3000))
    else:
        await add_real(hass, RealLight("real_1"))
    hass.states.async_set("binary_sensor.occ", "off")
    await setup_entries(
        hass,
        make_light_entry(
            lights=lights,
            occupancy="binary_sensor.occ",
            auto_on_brightness=40 if trigger == "level" else None,
        ),
    )
    seen = _reports_of(hass, INSTANT)

    if trigger in ("manual", "manual_level"):
        data = {"brightness": 200} if trigger == "manual_level" else {}
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": INSTANT, **data}, blocking=True
        )
    else:
        hass.states.async_set("binary_sensor.occ", "on")
    await settle(hass)

    assert seen
    assert [state for state, _ in seen if state != "on"] == []
    assert hass.states.get(lights[0]).state == "on"
    assert _attrs_of(hass, INSTANT)["last_on_physical"] is None

    seen.clear()
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": INSTANT}, blocking=True
    )
    await settle(hass)
    assert seen
    assert [state for state, _ in seen if state != "off"] == []
    assert _attrs_of(hass, INSTANT)["last_off_manual"] is not None


def _attrs_of(hass: HomeAssistant, entity_id: str) -> dict:
    return hass.states.get(entity_id).attributes


@pytest.mark.asyncio
async def test_reply_inside_the_standby_call_never_reports_off(
    hass: HomeAssistant,
) -> None:
    """Coming on at standby is a turn-on from off like any other."""
    await add_real(hass, InstantLight("real_1", kelvin=3000))
    hass.states.async_set("binary_sensor.settings_schedule", "off")
    await setup_entries(
        hass,
        make_scheduled_light_entry(
            name="Matrix Light",
            inside={CONF_LIGHT_TIMEOUT: 60, CONF_STANDBY_BRIGHTNESS: 20},
        ),
    )
    seen = _reports_of(hass, INSTANT)

    hass.states.async_set("binary_sensor.settings_schedule", "on")
    await settle(hass)

    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_STANDBY
    assert seen
    assert set(seen) == {("on", 51)}


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("stage", ["effect", "warn"])
async def test_reply_inside_a_stage_call_reports_the_stage_level_only(
    hass: HomeAssistant, freezer, stage: str
) -> None:
    """A stage's level is adopted before its command is sent, so a color
    mirrored from a reply inside the call is not published at the old level."""
    member = InstantLight("real_1", kelvin=3000)
    await add_real(hass, member)
    await setup_entries(
        hass,
        make_light_entry(
            warn_timeout=10,
            warn_brightness=20,
            effect_timeout=10 if stage == "effect" else None,
            effect_brightness=20 if stage == "effect" else None,
        ),
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": INSTANT, "brightness": 200}, blocking=True
    )
    await settle(hass)
    # The bulb drifts warmer: its next reply carries a color to mirror.
    member._attr_color_temp_kelvin = 2700
    seen = _reports_of(hass, INSTANT)

    await _tick(hass, freezer, 61)

    assert _attrs_of(hass, INSTANT)["molight_state"] == stage
    assert _attrs_of(hass, INSTANT)["color_temp_kelvin"] == 2700
    assert seen
    assert set(seen) == {("on", 51)}


@pytest.mark.asyncio
@pytest.mark.parametrize("member", ["instant", "after_the_call"])
async def test_wrapping_light_sees_no_off_from_the_light_it_wraps(
    hass: HomeAssistant, member: str
) -> None:
    """A virtual light replies inside the call itself, whatever it wraps; the
    light wrapping it must not take a blink for an off at the wall."""
    cls = InstantLight if member == "instant" else RealLight
    await add_real(hass, cls("real_1"))
    outer = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Outer",
            CONF_LIGHTS: [INSTANT],
            CONF_LIGHT_TIMEOUT: 60,
        },
    )
    await setup_entries(hass, make_light_entry(timeout=600), outer)
    inner_seen = _reports_of(hass, INSTANT)
    outer_seen = _reports_of(hass, "light.outer")

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.outer"}, blocking=True
    )
    await settle(hass)

    for seen in (inner_seen, outer_seen):
        assert seen
        assert [state for state, _ in seen if state != "on"] == []
    assert _attrs_of(hass, "light.outer")["last_off_manual"] is None
    assert _attrs_of(hass, "light.outer")["molight_state"] == STATE_ACTIVE


# ---------------------------------------------------------------------------
# Fades longer than Home Assistant keeps a command's context
#
# Five seconds after a command, Home Assistant stops tagging the member's
# reports with it, so the rest of a longer fade arrives under contexts of the
# member's own. A fade of 4 s is the control: all of it is under ours.
# ---------------------------------------------------------------------------

FADES = [4, 10]
PHYSICAL = (
    "last_on_physical",
    "last_brightness_change_physical",
    "last_color_change_physical",
    "last_off_manual",
)


def _stamps(hass: HomeAssistant) -> dict:
    """The marks a change at the wall leaves on the virtual light."""
    attrs = _attrs_of(hass, INSTANT)
    return {name: attrs[name] for name in PHYSICAL if attrs[name] is not None}


async def _run(hass: HomeAssistant, freezer, seconds: int) -> None:
    """Let time pass a second at a time, as a fade's reports come in."""
    for _ in range(seconds):
        await _tick(hass, freezer, 1)


async def _fading_light(hass: HomeAssistant, entry: MockConfigEntry, **member):
    """Set up the light over a fading member that is on at 200."""
    light = FadingLight("real_1", on=True, brightness=200, **member)
    await add_real(hass, light)
    await setup_entries(hass, entry)
    return light


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("fade", FADES)
@pytest.mark.parametrize("stage", ["effect", "warn"])
async def test_stage_fade_is_not_a_dim_at_the_wall(
    hass: HomeAssistant, freezer, fade: int, stage: str
) -> None:
    """A stage's fade, reported in steps, runs to its end and the light goes off."""
    stages = {f"{stage}_timeout": 30, f"{stage}_brightness": 20}
    member = await _fading_light(
        hass, make_light_entry(**stages, **{f"{stage}_transition": fade})
    )
    await _run(hass, freezer, 61)
    assert _attrs_of(hass, INSTANT)["molight_state"] == stage

    await _run(hass, freezer, fade + 1)

    assert member.brightness == 51
    assert _attrs_of(hass, INSTANT)["molight_state"] == stage
    assert _attrs_of(hass, INSTANT)["brightness"] == 51
    assert _stamps(hass) == {}
    await _run(hass, freezer, 30)
    assert hass.states.get(INSTANT).state == "off"
    assert _stamps(hass) == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("fade", FADES)
async def test_blink_off_fade_is_not_an_off_at_the_wall(
    hass: HomeAssistant, freezer, fade: int
) -> None:
    """A blink that fades out reports dimmer, then off: the stage stands."""
    member = await _fading_light(
        hass,
        make_light_entry(
            effect_timeout=30, effect_brightness=0, effect_transition=fade
        ),
    )
    await _run(hass, freezer, 61)

    await _run(hass, freezer, fade + 1)

    assert not member.is_on
    assert hass.states.get(INSTANT).state == "on"
    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_EFFECT
    assert _stamps(hass) == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("fade", FADES)
async def test_auto_on_fade_is_not_a_turn_on_at_the_wall(
    hass: HomeAssistant, freezer, fade: int
) -> None:
    """An automatic turn-on fading in stays the sensor's on-period."""
    hass.states.async_set("binary_sensor.occ", "off")
    member = FadingLight("real_1", brightness=200)
    await add_real(hass, member)
    await setup_entries(
        hass,
        make_light_entry(
            occupancy="binary_sensor.occ",
            auto_on_brightness=80,
            auto_on_transition=fade,
        ),
    )

    hass.states.async_set("binary_sensor.occ", "on")
    await settle(hass)
    await _run(hass, freezer, fade + 1)

    assert member.brightness == 204
    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_OCCUPIED
    assert _attrs_of(hass, INSTANT)["brightness"] == 204
    assert _stamps(hass) == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("fade", FADES)
async def test_auto_off_fade_is_not_a_change_at_the_wall(
    hass: HomeAssistant, freezer, fade: int
) -> None:
    """An automatic off fading out neither relights the light nor counts as
    an off at the wall."""
    member = await _fading_light(hass, make_light_entry(auto_off_transition=fade))
    seen = _reports_of(hass, INSTANT)
    await _run(hass, freezer, 61)
    assert hass.states.get(INSTANT).state == "off"

    await _run(hass, freezer, fade + 1)

    assert not member.is_on
    assert [state for state, _ in seen if state != "off"] == []
    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_IDLE
    assert _stamps(hass) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("fade", FADES)
@pytest.mark.parametrize("start", ["off", "active"])
async def test_standby_fade_is_not_a_change_at_the_wall(
    hass: HomeAssistant, freezer, fade: int, start: str
) -> None:
    """Coming on at standby, or dropping back to it, over a fade."""
    schedule = "binary_sensor.settings_schedule"
    hass.states.async_set(schedule, "off" if start == "off" else "on")
    member = FadingLight("real_1", on=start == "active", brightness=200)
    await add_real(hass, member)
    await setup_entries(
        hass,
        make_scheduled_light_entry(
            name="Matrix Light",
            inside={
                CONF_LIGHT_TIMEOUT: 60,
                CONF_STANDBY_BRIGHTNESS: 20,
                CONF_AUTO_ON_TRANSITION: fade,
                CONF_AUTO_OFF_TRANSITION: fade,
            },
        ),
    )
    if start == "off":
        hass.states.async_set(schedule, "on")
        await settle(hass)
    else:
        assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_ACTIVE
        await _run(hass, freezer, 61)
    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_STANDBY

    await _run(hass, freezer, fade + 1)

    assert member.is_on
    assert member.brightness == 51
    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_STANDBY
    assert _attrs_of(hass, INSTANT)["brightness"] == 51
    assert _stamps(hass) == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("fade", FADES)
@pytest.mark.parametrize("service", ["turn_on", "turn_off"])
async def test_caller_fade_is_not_a_change_at_the_wall(
    hass: HomeAssistant, freezer, fade: int, service: str
) -> None:
    """A fade the caller asked for is the caller's command all the way."""
    member = await _fading_light(hass, make_light_entry())
    data = {"brightness": 50} if service == "turn_on" else {}
    seen = _reports_of(hass, INSTANT)

    await hass.services.async_call(
        "light", service, {"entity_id": INSTANT, "transition": fade, **data}
    )
    await settle(hass)
    await _run(hass, freezer, fade + 1)

    stamps = _stamps(hass)
    if service == "turn_on":
        assert member.brightness == 50
        assert _attrs_of(hass, INSTANT)["brightness"] == 50
        assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_ACTIVE
    else:
        assert not member.is_on
        assert [state for state, _ in seen if state != "off"] == []
        assert stamps.pop("last_off_manual") is not None  # the caller's off
    assert stamps == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("fade", FADES)
async def test_warning_restore_fade_is_not_a_dim_at_the_wall(
    hass: HomeAssistant, freezer, fade: int
) -> None:
    """A turn-on during the warning restores the old level over its own fade."""
    member = await _fading_light(
        hass, make_light_entry(warn_timeout=30, warn_brightness=20)
    )
    await _run(hass, freezer, 62)
    assert member.brightness == 51

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": INSTANT, "transition": fade}
    )
    await settle(hass)
    await _run(hass, freezer, fade + 1)

    assert member.brightness == 200
    assert _attrs_of(hass, INSTANT)["brightness"] == 200
    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_ACTIVE
    assert _stamps(hass) == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("fade", FADES)
async def test_stage_color_fade_is_not_a_recolor_at_the_wall(
    hass: HomeAssistant, freezer, fade: int
) -> None:
    """A stage fading from red to cyan and dimmer, reported in steps."""
    member = await _fading_light(
        hass,
        make_light_entry(
            warn_timeout=30,
            warn_brightness=20,
            warn_rgb_color=[0, 255, 255],
            warn_transition=fade,
        ),
        hs=RED,
        steps=(0.3, 0.6, 0.9),
    )
    await _run(hass, freezer, 61)

    await _run(hass, freezer, fade + 1)

    assert member.hs_color == CYAN
    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_WARN
    assert tuple(_attrs_of(hass, INSTANT)["hs_color"]) == CYAN
    assert _stamps(hass) == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("change", ["brighter", "off", "recolor", "command"])
async def test_change_at_the_wall_late_in_a_stage_fade_still_cancels_it(
    hass: HomeAssistant, freezer, change: str
) -> None:
    """Seven seconds into a 10 s fade to the warning level, the member's
    reports carry no context of ours; one that is no step of the fade is
    still a person's."""
    member = await _fading_light(
        hass,
        make_light_entry(warn_timeout=30, warn_brightness=20, warn_transition=10),
        hs=RED,
    )
    await _run(hass, freezer, 61 + 7)
    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_WARN
    assert _stamps(hass) == {}

    if change == "brighter":
        member.wall(brightness=230)
    elif change == "off":
        member.wall(on=False)
    elif change == "recolor":
        member.wall(hs_color=GREEN)
    else:
        # Dimmer, as the fade is, but by an automation's command to the member.
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.real_1", "brightness": 100},
            blocking=True,
            context=Context(parent_id=Context().id),
        )
    await settle(hass)

    attrs = _attrs_of(hass, INSTANT)
    stamp = {
        "brighter": "last_brightness_change_physical",
        "off": "last_off_manual",
        "recolor": "last_color_change_physical",
        "command": "last_brightness_change_physical",
    }[change]
    assert list(_stamps(hass)) == [stamp]
    assert attrs["warning_active"] is False
    if change == "off":
        assert hass.states.get(INSTANT).state == "off"
    else:
        assert attrs["molight_state"] == STATE_ACTIVE
    if change == "recolor":
        assert tuple(attrs["hs_color"]) == GREEN
        assert attrs["brightness"] == 200  # the pre-warning level, restored
    if change in ("brighter", "command"):
        assert attrs["brightness"] == member.brightness


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
@pytest.mark.parametrize("when", ["during", "after"])
async def test_turn_on_at_the_wall_late_in_an_off_fade_is_a_turn_on(
    hass: HomeAssistant, freezer, when: str
) -> None:
    """Brighter during a 10 s fade to off, or back on just after it."""
    member = await _fading_light(hass, make_light_entry(auto_off_transition=10))
    await _run(hass, freezer, 61 + (7 if when == "during" else 11))
    assert hass.states.get(INSTANT).state == "off"
    assert member.is_on is (when == "during")
    assert _stamps(hass) == {}

    member.wall(brightness=180)
    await settle(hass)

    assert hass.states.get(INSTANT).state == "on"
    assert _attrs_of(hass, INSTANT)["brightness"] == 180
    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_ACTIVE
    assert _attrs_of(hass, INSTANT)["last_on_physical"] is not None


@pytest.mark.asyncio
@pytest.mark.usefixtures("virtual_light_behavior_variant")
async def test_recolor_off_the_way_late_in_a_color_fade_is_a_recolor(
    hass: HomeAssistant, freezer
) -> None:
    """Late in a 10 s fade from red to cyan, green by way of blue is a step;
    purple, the other way round the wheel, is someone's choice."""
    member = await _fading_light(
        hass,
        make_light_entry(
            warn_timeout=30,
            warn_brightness=20,
            warn_rgb_color=[0, 255, 255],
            warn_transition=10,
        ),
        hs=RED,
    )
    await _run(hass, freezer, 61 + 7)
    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_WARN
    assert _stamps(hass) == {}

    member.wall(hs_color=PURPLE)
    await settle(hass)

    assert _attrs_of(hass, INSTANT)["molight_state"] == STATE_ACTIVE
    assert list(_stamps(hass)) == ["last_color_change_physical"]
    assert tuple(_attrs_of(hass, INSTANT)["hs_color"]) == PURPLE


# ---------------------------------------------------------------------------
# A member with a color temperature but no color
#
# Home Assistant turns a color into the nearest color temperature for such a
# member, which clamps it to its own range. Its reply here comes six seconds
# late, when only a full match of what was asked is still our echo.
# ---------------------------------------------------------------------------

RED_RGB, BLUE_RGB = [255, 0, 0], [0, 0, 255]
COLOR_SOURCES = ["auto_on", "effect", "warn", "standby", "caller"]


async def _mixed_light(
    hass: HomeAssistant, source: str, rgb: list[int]
) -> tuple[FadingLight, str]:
    """A color member and a slow color-temperature one; source sends rgb.

    Returns the color-temperature member and the state the light is then in.
    """
    occupancy, schedule = "binary_sensor.occ", "binary_sensor.settings_schedule"
    lights = ["light.color", "light.white"]
    lit = source in ("effect", "warn", "caller")
    white = FadingLight("white", on=lit, brightness=200, kelvin=3000, latency=6)
    await add_real(hass, FadingLight("color", on=lit, brightness=200, hs=GREEN), white)
    hass.states.async_set(occupancy, "off")
    hass.states.async_set(schedule, "off")
    if source == "standby":
        entry = make_scheduled_light_entry(
            name="Matrix Light",
            lights=lights,
            inside={
                CONF_LIGHT_TIMEOUT: 60,
                CONF_STANDBY_BRIGHTNESS: 20,
                CONF_STANDBY_RGB_COLOR: rgb,
            },
        )
    else:
        stage = {
            f"{source}_timeout": 30,
            f"{source}_brightness": 50,
            f"{source}_rgb_color": rgb,
        }
        entry = make_light_entry(
            lights=lights,
            occupancy=occupancy,
            auto_on_rgb_color=rgb if source == "auto_on" else None,
            **(stage if source in ("effect", "warn") else {}),
        )
    await setup_entries(hass, entry)
    if source == "auto_on":
        hass.states.async_set(occupancy, "on")
    elif source == "standby":
        hass.states.async_set(schedule, "on")
    elif source == "caller":
        hs = list(color_util.color_RGB_to_hs(*rgb))
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": INSTANT, "hs_color": hs}
        )
    await settle(hass)
    return white, {
        "auto_on": STATE_OCCUPIED,
        "standby": STATE_STANDBY,
        "caller": STATE_ACTIVE,
    }.get(source, source)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", COLOR_SOURCES)
@pytest.mark.parametrize(("rgb", "kelvin"), [(RED_RGB, 6279), (BLUE_RGB, 2202)])
async def test_late_color_temperature_reply_to_a_color_is_not_physical(
    hass: HomeAssistant, freezer, source: str, rgb: list[int], kelvin: int
) -> None:
    """Red is nearest 6279 K; blue is nearest a kelvin below the member's
    range, so it shows its warmest. Either reply is what we asked for."""
    white, machine_state = await _mixed_light(hass, source, rgb)
    await _run(hass, freezer, 61 if source in ("effect", "warn") else 0)
    assert _attrs_of(hass, INSTANT)["molight_state"] == machine_state

    await _run(hass, freezer, 7)

    assert white.is_on
    assert white.color_temp_kelvin == kelvin
    assert _attrs_of(hass, INSTANT)["molight_state"] == machine_state
    assert tuple(_attrs_of(hass, INSTANT)["hs_color"]) == color_util.color_RGB_to_hs(
        *rgb
    )
    assert _stamps(hass) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", COLOR_SOURCES)
async def test_late_other_color_temperature_after_a_color_is_physical(
    hass: HomeAssistant, freezer, source: str
) -> None:
    """Asked for red, nearest 6279 K, the member shows 4000 K four seconds
    later: that is someone at the wall, not its reply."""
    white, machine_state = await _mixed_light(hass, source, RED_RGB)
    await _run(hass, freezer, (61 if source in ("effect", "warn") else 0) + 4)
    assert _attrs_of(hass, INSTANT)["molight_state"] == machine_state
    assert _stamps(hass) == {}

    white.wall(color_temp_kelvin=4000)
    await settle(hass)

    attrs = _attrs_of(hass, INSTANT)
    if source in ("auto_on", "standby"):
        # The member was dark until then: a turn-on at the wall.
        assert list(_stamps(hass)) == ["last_on_physical"]
        assert attrs["color_temp_kelvin"] == 4000
    else:
        assert list(_stamps(hass)) == ["last_color_change_physical"]
        assert attrs["molight_state"] == STATE_ACTIVE
        assert attrs["warning_active"] is False
    await _run(hass, freezer, 7)  # the member's reply to a restore after a stage
