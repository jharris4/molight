"""A virtual light wrapping another virtual light.

The inner light keeps its own timer, stages, sensors and schedule. What it
does by itself reaches the outer light as a member change, which must not
pass for a person at the wall; what a person does to the inner light, at the
wall or through it, still must.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.molight.const import (
    CONF_LIGHT_TIMEOUT,
    CONF_OCCUPANCY_ENTITY,
    CONF_STANDBY_BRIGHTNESS,
    SCHEDULE_MODE_FOLLOW,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_WARN,
)

from .conftest import (
    make_light_entry,
    make_scheduled_light_entry,
    settle,
    setup_entries,
)
from .real_entities import RealLight, add_real

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

REAL = "light.real_1"
INNER = "light.inner"
OUTER = "light.outer"
OCC = "binary_sensor.occ"
INNER_OCC = "binary_sensor.inner_occ"
SCHEDULE = "binary_sensor.settings_schedule"
WINDOW = "binary_sensor.window"
OUTER_KINDS = ["regular", "scheduled", "follow"]


def _outer(kind: str, occupancy: str | None):
    """An outer light of the given kind wrapping the inner light."""
    if kind == "scheduled":
        inside = {CONF_LIGHT_TIMEOUT: 300}
        if occupancy:
            inside[CONF_OCCUPANCY_ENTITY] = occupancy
        return make_scheduled_light_entry(name="Outer", lights=[INNER], inside=inside)
    if kind == "follow":
        return make_light_entry(
            name="Outer",
            lights=[INNER],
            timeout=300,
            schedule=WINDOW,
            schedule_mode=SCHEDULE_MODE_FOLLOW,
        )
    return make_light_entry(
        name="Outer", lights=[INNER], timeout=300, occupancy=occupancy
    )


async def _nested(
    hass: HomeAssistant, kind: str, inner=None, occupancy: str | None = None
):
    """Set up a bulb, the inner light over it and an outer light over that."""
    hass.states.async_set(OCC, "off")
    hass.states.async_set(WINDOW, "off")
    hass.states.async_set(SCHEDULE, "on")
    await add_real(hass, RealLight("real_1"))
    inner = inner or make_light_entry(name="Inner", lights=[REAL], timeout=60)
    await setup_entries(hass, inner, _outer(kind, occupancy))


async def _lit_by_outer(hass: HomeAssistant, kind: str) -> None:
    """Light the room through the outer light's own trigger."""
    if kind == "follow":
        hass.states.async_set(WINDOW, "on")
    else:
        hass.states.async_set(OCC, "on")
    await settle(hass)
    assert hass.states.get(INNER).state == "on"
    assert hass.states.get(OUTER).state == "on"


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


def _attrs(hass: HomeAssistant, entity_id: str) -> dict:
    return hass.states.get(entity_id).attributes


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", OUTER_KINDS)
async def test_inner_timer_off_is_not_a_manual_off(
    hass: HomeAssistant, freezer, kind: str
) -> None:
    """The inner light's own timer ends the on-period as an automatic off."""
    await _nested(hass, kind, occupancy=None if kind == "follow" else OCC)
    await _lit_by_outer(hass, kind)

    await _tick(hass, freezer, 61)

    assert hass.states.get(INNER).state == "off"
    assert hass.states.get(OUTER).state == "off"
    assert _attrs(hass, OUTER)["molight_state"] == STATE_IDLE
    assert _attrs(hass, OUTER)["last_off_manual"] is None
    if kind != "follow":
        # The next visit lights the room again as any other would.
        hass.states.async_set(OCC, "off")
        await settle(hass)
        hass.states.async_set(OCC, "on")
        await settle(hass)
        assert hass.states.get(INNER).state == "on"
        assert _attrs(hass, OUTER)["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_inner_warn_dim_does_not_restart_the_outer_timer(
    hass: HomeAssistant, freezer
) -> None:
    """The inner's warn stage is mirrored, not taken for a dim at the wall."""
    inner = make_light_entry(
        name="Inner", lights=[REAL], timeout=100, warn_timeout=50, warn_brightness=20
    )
    await _nested(hass, "regular", inner=inner)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": OUTER, "brightness": 200}, blocking=True
    )
    await settle(hass)
    assert _attrs(hass, OUTER)["molight_state"] == STATE_ACTIVE

    await _tick(hass, freezer, 101)
    assert _attrs(hass, INNER)["molight_state"] == STATE_WARN
    assert _attrs(hass, OUTER)["brightness"] == 51
    assert _attrs(hass, OUTER)["last_brightness_change_physical"] is None
    assert _attrs(hass, OUTER)["molight_state"] == STATE_ACTIVE

    # The outer's own 300 s timer, not one restarted by the dim, turns it off.
    await _tick(hass, freezer, 200)

    assert hass.states.get(OUTER).state == "off"
    assert hass.states.get(INNER).state == "off"
    assert _attrs(hass, OUTER)["last_off_manual"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["occupancy", "standby"])
async def test_inner_turn_on_by_itself_is_not_a_turn_on_at_the_wall(
    hass: HomeAssistant, freezer, trigger: str
) -> None:
    """The inner's own sensor or standby lights it: the outer adopts the
    on-period without claiming it for a person."""
    hass.states.async_set(INNER_OCC, "off")
    if trigger == "occupancy":
        inner = make_light_entry(
            name="Inner", lights=[REAL], timeout=60, occupancy=INNER_OCC
        )
    else:
        inner = make_scheduled_light_entry(
            name="Inner",
            lights=[REAL],
            schedule=WINDOW,
            inside={CONF_LIGHT_TIMEOUT: 60, CONF_STANDBY_BRIGHTNESS: 20},
        )
    await _nested(hass, "regular", inner=inner)

    hass.states.async_set(INNER_OCC if trigger == "occupancy" else WINDOW, "on")
    await settle(hass)

    assert hass.states.get(INNER).state == "on"
    assert _attrs(hass, OUTER)["molight_state"] == STATE_ACTIVE
    assert _attrs(hass, OUTER)["last_on_physical"] is None
    assert _attrs(hass, OUTER)["brightness"] == _attrs(hass, INNER)["brightness"]


@pytest.mark.asyncio
async def test_inner_schedule_end_is_not_a_manual_off(
    hass: HomeAssistant, freezer
) -> None:
    """A follow window ending on the inner light ends the outer's on-period
    without a manual off."""
    inner = make_light_entry(
        name="Inner",
        lights=[REAL],
        timeout=60,
        schedule=WINDOW,
        schedule_mode=SCHEDULE_MODE_FOLLOW,
    )
    await _nested(hass, "regular", inner=inner)
    hass.states.async_set(WINDOW, "on")
    await settle(hass)
    assert hass.states.get(OUTER).state == "on"

    hass.states.async_set(WINDOW, "off")
    await settle(hass)

    assert hass.states.get(INNER).state == "off"
    assert hass.states.get(OUTER).state == "off"
    assert _attrs(hass, OUTER)["last_off_manual"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", OUTER_KINDS)
async def test_off_at_the_wall_on_the_inner_bulb_is_a_manual_off(
    hass: HomeAssistant, kind: str
) -> None:
    """A person switching the bulb off is one for the inner and the outer."""
    await _nested(hass, kind, occupancy=None if kind == "follow" else OCC)
    await _lit_by_outer(hass, kind)

    hass.states.async_set(REAL, "off")
    await settle(hass)

    assert _attrs(hass, INNER)["last_off_manual"] is not None
    assert hass.states.get(OUTER).state == "off"
    assert _attrs(hass, OUTER)["last_off_manual"] is not None


@pytest.mark.asyncio
async def test_dim_at_the_wall_on_the_inner_bulb_is_physical(
    hass: HomeAssistant, freezer
) -> None:
    """A person dimming the bulb restarts the outer's timer as a wall dim."""
    inner = make_light_entry(name="Inner", lights=[REAL], timeout=1000)
    await _nested(hass, "regular", inner=inner)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": OUTER, "brightness": 200}, blocking=True
    )
    await settle(hass)
    await _tick(hass, freezer, 250)
    assert hass.states.get(OUTER).state == "on"

    hass.states.async_set(REAL, "on", {"brightness": 100})
    await settle(hass)

    assert _attrs(hass, INNER)["last_brightness_change_physical"] is not None
    assert _attrs(hass, OUTER)["last_brightness_change_physical"] is not None
    assert _attrs(hass, OUTER)["brightness"] == 100
    # The dim restarted the outer's full timer: still on past the old end.
    await _tick(hass, freezer, 100)
    assert hass.states.get(OUTER).state == "on"


@pytest.mark.asyncio
async def test_service_call_on_the_inner_light_is_a_persons(
    hass: HomeAssistant,
) -> None:
    """Turning the inner light off through its own entity is a manual off."""
    await _nested(hass, "regular", occupancy=OCC)
    await _lit_by_outer(hass, "regular")

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": INNER}, blocking=True
    )
    await settle(hass)

    assert hass.states.get(OUTER).state == "off"
    assert _attrs(hass, OUTER)["last_off_manual"] is not None


@pytest.mark.asyncio
async def test_outer_commands_still_match_as_echoes(hass: HomeAssistant) -> None:
    """The inner stamps the outer's own commands as virtual changes, which the
    outer recognizes as its echoes, not as a person's."""
    await _nested(hass, "regular", occupancy=OCC)
    await _lit_by_outer(hass, "regular")
    assert _attrs(hass, INNER)["last_on_virtual"] is not None
    assert _attrs(hass, OUTER)["last_on_physical"] is None

    hass.states.async_set(OCC, "off")
    await settle(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": OUTER}, blocking=True
    )
    await settle(hass)

    assert hass.states.get(INNER).state == "off"
    assert _attrs(hass, OUTER)["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
@pytest.mark.allow_warning_log
async def test_load_warns_about_a_light_two_virtual_lights_control(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The light that loads second names the other and the light they fight
    over; at startup, when every entry has loaded first, each names the other."""
    hass.states.async_set(REAL, "off")
    await setup_entries(
        hass,
        make_light_entry(name="Room", lights=[REAL]),
        make_light_entry(name="Other", lights=[REAL]),
    )
    await settle(hass)

    warned = [
        record.args
        for record in caplog.records
        if record.msg.startswith("%s and %s both control %s")
    ]
    assert warned == [("light.other", "light.room", REAL)]


# ---------------------------------------------------------------------------
# A member light group changed to contain a virtual light
# ---------------------------------------------------------------------------

ROOM = "light.room"
BULB = "light.bulb"
GROUP_TOPOLOGIES = ["sole", "mixed", "nested"]


def _at_the_wall(bulb: RealLight, *, on: bool = True, brightness: int = 255) -> None:
    """Change a bulb with no command behind it."""
    bulb._attr_is_on = on
    bulb._attr_brightness = brightness
    bulb.async_write_ha_state()


def _light_group(name: str, members: list[str]) -> MockConfigEntry:
    return MockConfigEntry(
        domain="group",
        title=name,
        options={
            "group_type": "light",
            "name": name,
            "entities": members,
            "hide_members": False,
        },
    )


async def _outer_over_edited_group(
    hass: HomeAssistant, topology: str, inner: MockConfigEntry
) -> dict[str, RealLight]:
    """An outer light over a group of bulbs that is then changed to hold Inner.

    The inner light is alone in the group, next to a bulb, or in a group
    inside it that the outer light does not watch.
    """
    bulbs = {name: RealLight(name) for name in ("real_1", "bulb", "spare")}
    await add_real(hass, *bulbs.values())
    room = _light_group("Room", [BULB])
    below = _light_group("Below", ["light.spare"])
    await setup_entries(hass, inner, below, room)
    member = ROOM
    if topology == "nested":
        await setup_entries(hass, _light_group("Top", [ROOM, "light.below"]))
        member = "light.top"
    await setup_entries(
        hass, make_light_entry(name="Outer", lights=[member], timeout=60)
    )
    await settle(hass)
    members = [INNER, BULB] if topology == "mixed" else [INNER]
    hass.config_entries.async_update_entry(
        room, options={**room.options, "entities": members}
    )
    assert await hass.config_entries.async_reload(room.entry_id)
    await settle(hass)
    assert hass.states.get(ROOM).attributes["entity_id"] == members
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": OUTER, "brightness": 200}, blocking=True
    )
    await settle(hass)
    assert hass.states.get(INNER).state == "on"
    return bulbs


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", GROUP_TOPOLOGIES)
async def test_warn_dim_through_an_edited_group_does_not_restart_the_outer_timer(
    hass: HomeAssistant, freezer, topology: str
) -> None:
    """The group hides the inner light's stamps; its warn stage is still its own."""
    inner = make_light_entry(
        name="Inner", lights=[REAL], timeout=50, warn_timeout=120, warn_brightness=20
    )
    bulbs = await _outer_over_edited_group(hass, topology, inner)
    await _tick(hass, freezer, 51)
    assert _attrs(hass, INNER)["molight_state"] == STATE_WARN
    assert bulbs["real_1"].brightness == 51
    assert _attrs(hass, OUTER)["last_brightness_change_physical"] is None

    await _tick(hass, freezer, 10)
    assert hass.states.get(OUTER).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", GROUP_TOPOLOGIES)
async def test_timer_off_through_an_edited_group_is_not_a_manual_off(
    hass: HomeAssistant, freezer, topology: str
) -> None:
    """Nor is the inner light's own timer turning it off a person's off."""
    inner = make_light_entry(name="Inner", lights=[REAL], timeout=30)
    await _outer_over_edited_group(hass, topology, inner)
    # The inner light is the last one lit under the outer light.
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": [BULB, "light.spare"]}, blocking=True
    )
    await settle(hass)
    before = _attrs(hass, OUTER)["last_off_manual"]
    await _tick(hass, freezer, 31)
    assert hass.states.get(INNER).state == "off"
    assert _attrs(hass, INNER)["last_off_manual"] is None
    assert hass.states.get(OUTER).state == "off"
    assert _attrs(hass, OUTER)["last_off_manual"] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", GROUP_TOPOLOGIES)
@pytest.mark.parametrize("target", ["inner", "wall"])
async def test_a_persons_dim_through_an_edited_group_restarts_the_outer_timer(
    hass: HomeAssistant, freezer, topology: str, target: str
) -> None:
    """A command through the inner light, or a dim at its bulb, is a person's."""
    inner = make_light_entry(name="Inner", lights=[REAL], timeout=300)
    bulbs = await _outer_over_edited_group(hass, topology, inner)
    await _tick(hass, freezer, 51)
    if target == "inner":
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": INNER, "brightness": 150}, blocking=True
        )
    else:
        _at_the_wall(bulbs["real_1"], brightness=150)
    await settle(hass)
    assert _attrs(hass, OUTER)["last_brightness_change_physical"] is not None

    await _tick(hass, freezer, 10)
    assert hass.states.get(OUTER).state == "on"
    await _tick(hass, freezer, 51)
    assert hass.states.get(OUTER).state == "off"


@pytest.mark.asyncio
async def test_a_dim_at_a_bulb_beside_the_inner_light_is_a_persons(
    hass: HomeAssistant, freezer
) -> None:
    """A bulb in the group changing with the inner light is not its automation."""
    inner = make_light_entry(
        name="Inner", lights=[REAL], timeout=50, warn_timeout=120, warn_brightness=20
    )
    bulbs = await _outer_over_edited_group(hass, "mixed", inner)
    await _tick(hass, freezer, 51)
    assert _attrs(hass, OUTER)["last_brightness_change_physical"] is None
    _at_the_wall(bulbs["bulb"], brightness=90)
    await settle(hass)
    assert _attrs(hass, OUTER)["last_brightness_change_physical"] is not None

    await _tick(hass, freezer, 10)
    assert hass.states.get(OUTER).state == "on"


@pytest.mark.asyncio
async def test_a_group_of_bulbs_is_a_person_at_the_wall(
    hass: HomeAssistant, freezer
) -> None:
    """A group without a virtual light reports changes at the wall as before."""
    bulb = RealLight("bulb")
    await add_real(hass, bulb)
    await setup_entries(hass, _light_group("Room", [BULB]))
    await setup_entries(hass, make_light_entry(name="Outer", lights=[ROOM], timeout=60))
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": OUTER, "brightness": 200}, blocking=True
    )
    await settle(hass)
    await _tick(hass, freezer, 51)
    _at_the_wall(bulb, brightness=90)
    await settle(hass)
    assert _attrs(hass, OUTER)["last_brightness_change_physical"] is not None

    await _tick(hass, freezer, 10)
    assert hass.states.get(OUTER).state == "on"
    _at_the_wall(bulb, on=False)
    await settle(hass)
    assert _attrs(hass, OUTER)["last_off_manual"] is not None
