"""Tests for the MoLight Virtual Light's color support.

The virtual light derives its color capabilities from the real lights it
wraps (HS when any member can show a color, COLOR_TEMP when any member
supports it, brightness-only when none do), forwards color commands to all
members in one call, mirrors the first on member's color, and threads color
through the auto-on and effect/warn machinery exactly like brightness.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, State, callback
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.const import (
    STATE_ACTIVE,
    STATE_OCCUPIED,
    STATE_WARN,
)
from tests.conftest import make_light_entry, settle, setup_entries

OCC = "binary_sensor.occ"
REAL = "light.real_1"
REAL_2 = "light.real_2"
VIRTUAL = "light.matrix_light"

HS_CAPS = {"supported_color_modes": ["hs"]}
HS_TEMP_CAPS = {
    "supported_color_modes": ["hs", "color_temp"],
    "min_color_temp_kelvin": 2202,
    "max_color_temp_kelvin": 6535,
}


def _state(hass: HomeAssistant):
    return hass.states.get(VIRTUAL)


def _record_service_calls(hass: HomeAssistant) -> list[dict]:
    calls: list[dict] = []

    @callback
    def _record(event) -> None:
        calls.append(event.data)

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)
    return calls


def _real_on_calls(calls: list[dict]) -> list[dict]:
    return [
        d
        for d in calls
        if d["domain"] == "light"
        and d["service"] == "turn_on"
        and REAL in d["service_data"].get("entity_id", [])
    ]


async def _turn_on_virtual(hass: HomeAssistant, **data) -> None:
    await hass.services.async_call("light", "turn_on", {"entity_id": VIRTUAL, **data})
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# Capability derivation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_derived_from_members(hass: HomeAssistant) -> None:
    """A color-capable member gives the virtual light hs/color_temp modes and
    the member's kelvin range."""
    hass.states.async_set(REAL, "off", HS_TEMP_CAPS)
    await setup_entries(hass, make_light_entry())

    attrs = _state(hass).attributes
    assert set(attrs["supported_color_modes"]) == {"hs", "color_temp"}
    assert attrs["min_color_temp_kelvin"] == 2202
    assert attrs["max_color_temp_kelvin"] == 6535


@pytest.mark.asyncio
async def test_brightness_only_members_stay_brightness_only(
    hass: HomeAssistant,
) -> None:
    """Without a color-capable member the virtual light keeps its pre-color
    behavior, and a color request is filtered out by HA before reaching it."""
    hass.states.async_set(REAL, "off", {"supported_color_modes": ["brightness"]})
    await setup_entries(hass, make_light_entry())

    assert _state(hass).attributes["supported_color_modes"] == ["brightness"]

    calls = _record_service_calls(hass)
    await _turn_on_virtual(hass, hs_color=[30, 60])
    forwarded = _real_on_calls(calls)
    assert forwarded
    assert "hs_color" not in forwarded[-1]["service_data"]
    assert _state(hass).attributes.get("hs_color") is None


@pytest.mark.asyncio
async def test_capabilities_appear_when_member_does(hass: HomeAssistant) -> None:
    """A member unavailable at startup contributes its color modes once it
    reports a state."""
    await setup_entries(hass, make_light_entry())
    assert _state(hass).attributes["supported_color_modes"] == ["brightness"]

    hass.states.async_set(REAL, "off", HS_TEMP_CAPS)
    await settle(hass)
    assert set(_state(hass).attributes["supported_color_modes"]) == {
        "hs",
        "color_temp",
    }


@pytest.mark.asyncio
async def test_mixed_members_get_one_call_with_color(hass: HomeAssistant) -> None:
    """A color + brightness-only mix advertises color and sends ONE call
    carrying the color to every member (HA filters it per real light)."""
    hass.states.async_set(REAL, "off", HS_CAPS)
    hass.states.async_set(REAL_2, "off", {"supported_color_modes": ["brightness"]})
    await setup_entries(hass, make_light_entry(lights=[REAL, REAL_2]))

    assert "hs" in _state(hass).attributes["supported_color_modes"]

    calls = _record_service_calls(hass)
    await _turn_on_virtual(hass, hs_color=[30, 60])
    forwarded = _real_on_calls(calls)
    assert len(forwarded) == 1
    data = forwarded[-1]["service_data"]
    assert set(data["entity_id"]) == {REAL, REAL_2}
    assert tuple(data["hs_color"]) == (30.0, 60.0)


# ---------------------------------------------------------------------------
# Pass-through and mirroring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_on_forwards_hs_color(hass: HomeAssistant) -> None:
    hass.states.async_set(REAL, "off", HS_CAPS)
    await setup_entries(hass, make_light_entry())

    calls = _record_service_calls(hass)
    await _turn_on_virtual(hass, hs_color=[100, 50])

    data = _real_on_calls(calls)[-1]["service_data"]
    assert tuple(data["hs_color"]) == (100.0, 50.0)
    attrs = _state(hass).attributes
    assert tuple(attrs["hs_color"]) == (100.0, 50.0)
    assert attrs["color_mode"] == "hs"
    assert attrs["last_color_change_virtual"] is not None
    assert attrs["last_color_change_physical"] is None


@pytest.mark.asyncio
async def test_turn_on_forwards_color_temp(hass: HomeAssistant) -> None:
    hass.states.async_set(REAL, "off", HS_TEMP_CAPS)
    await setup_entries(hass, make_light_entry())

    calls = _record_service_calls(hass)
    await _turn_on_virtual(hass, color_temp_kelvin=3500)

    data = _real_on_calls(calls)[-1]["service_data"]
    assert data["color_temp_kelvin"] == 3500
    attrs = _state(hass).attributes
    assert attrs["color_temp_kelvin"] == 3500
    assert attrs["color_mode"] == "color_temp"


@pytest.mark.asyncio
async def test_seed_adopts_member_color(hass: HomeAssistant) -> None:
    """A member already on at startup hands the virtual light its color."""
    hass.states.async_set(
        REAL,
        "on",
        {**HS_CAPS, "color_mode": "hs", "hs_color": [100, 50], "brightness": 200},
    )
    await setup_entries(hass, make_light_entry())

    attrs = _state(hass).attributes
    assert _state(hass).state == "on"
    assert tuple(attrs["hs_color"]) == (100.0, 50.0)
    assert attrs["brightness"] == 200


@pytest.mark.asyncio
async def test_member_turn_on_adopts_color(hass: HomeAssistant) -> None:
    """An external off→on on a member mirrors its color, like brightness."""
    hass.states.async_set(REAL, "off", HS_CAPS)
    await setup_entries(hass, make_light_entry())

    hass.states.async_set(
        REAL,
        "on",
        {**HS_CAPS, "color_mode": "hs", "hs_color": [50, 60], "brightness": 100},
    )
    await settle(hass)

    attrs = _state(hass).attributes
    assert _state(hass).state == "on"
    assert attrs["molight_state"] == STATE_ACTIVE
    assert tuple(attrs["hs_color"]) == (50.0, 60.0)
    assert attrs["brightness"] == 100


@pytest.mark.asyncio
async def test_external_recolor_mirrors_and_restarts_timer(
    hass: HomeAssistant, freezer
) -> None:
    """Recoloring a real light is human activity: the color is mirrored,
    attributed, and a running countdown restarts with the full timeout."""
    member_attrs = {
        **HS_CAPS,
        "color_mode": "hs",
        "hs_color": [100, 50],
        "brightness": 200,
    }
    hass.states.async_set(REAL, "on", member_attrs)
    await setup_entries(hass, make_light_entry())  # adopted ACTIVE, 60s timer

    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    hass.states.async_set(REAL, "on", {**member_attrs, "hs_color": [200, 50]})
    await settle(hass)

    attrs = _state(hass).attributes
    assert tuple(attrs["hs_color"]) == (200.0, 50.0)
    assert attrs["last_color_change_physical"] is not None

    # 80s after adoption — past the original expiry, inside the restarted one.
    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "on"

    freezer.tick(timedelta(seconds=25))
    async_fire_time_changed(hass)
    await settle(hass)
    assert _state(hass).state == "off"


# ---------------------------------------------------------------------------
# Auto-on color
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_on_color_temp_applied_to_automatic_turn_ons_only(
    hass: HomeAssistant,
) -> None:
    """auto_on_color_temp colors occupancy turn-ons; manual turn-ons are
    untouched, mirroring auto_on_brightness."""
    hass.states.async_set(REAL, "off", HS_TEMP_CAPS)
    await setup_entries(hass, make_light_entry(occupancy=OCC, auto_on_color_temp=3000))

    calls = _record_service_calls(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)

    data = _real_on_calls(calls)[-1]["service_data"]
    assert data["color_temp_kelvin"] == 3000
    attrs = _state(hass).attributes
    assert attrs["molight_state"] == STATE_OCCUPIED
    assert attrs["color_temp_kelvin"] == 3000
    assert attrs["color_mode"] == "color_temp"

    # Manual turn-on: no auto-on color.
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": VIRTUAL}, blocking=True
    )
    hass.states.async_set(OCC, "off")
    await settle(hass)
    calls.clear()
    await _turn_on_virtual(hass)
    assert "color_temp_kelvin" not in _real_on_calls(calls)[-1]["service_data"]


@pytest.mark.asyncio
async def test_auto_on_rgb_color(hass: HomeAssistant) -> None:
    """An rgb auto-on color is forwarded verbatim and reported as hs."""
    hass.states.async_set(REAL, "off", HS_CAPS)
    await setup_entries(
        hass, make_light_entry(occupancy=OCC, auto_on_rgb_color=[0, 0, 255])
    )

    calls = _record_service_calls(hass)
    hass.states.async_set(OCC, "on")
    await settle(hass)

    data = _real_on_calls(calls)[-1]["service_data"]
    assert tuple(data["rgb_color"]) == (0, 0, 255)
    assert tuple(_state(hass).attributes["hs_color"]) == (240.0, 100.0)


# ---------------------------------------------------------------------------
# Effect / warn stage colors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warn_color_shown_and_restored_on_retrigger(
    hass: HomeAssistant, freezer
) -> None:
    """The warn stage shows its color; a re-trigger with no color of its own
    restores the pre-warning color and brightness."""
    hass.states.async_set(REAL, "off", HS_CAPS)
    await setup_entries(
        hass, make_light_entry(warn_timeout=15, warn_rgb_color=[255, 0, 0])
    )

    await _turn_on_virtual(hass, brightness=200, hs_color=[100, 100])

    calls = _record_service_calls(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)

    attrs = _state(hass).attributes
    assert attrs["molight_state"] == STATE_WARN
    data = _real_on_calls(calls)[-1]["service_data"]
    assert tuple(data["rgb_color"]) == (255, 0, 0)
    assert data["brightness"] == 200  # no warn_brightness → pre-warn value
    # Reported color follows the stage; the snapshot remembers the original.
    assert tuple(attrs["hs_color"]) == (0.0, 100.0)
    assert attrs["pre_warn_color"] == {"hs_color": [100.0, 100.0]}

    calls.clear()
    await _turn_on_virtual(hass)  # re-trigger without brightness or color

    attrs = _state(hass).attributes
    assert attrs["molight_state"] == STATE_ACTIVE
    data = _real_on_calls(calls)[-1]["service_data"]
    assert tuple(data["hs_color"]) == (100.0, 100.0)
    assert data["brightness"] == 200
    assert tuple(attrs["hs_color"]) == (100.0, 100.0)
    assert attrs["pre_warn_color"] is None


@pytest.mark.asyncio
async def test_plain_warn_stage_undoes_effect_recolor(
    hass: HomeAssistant, freezer
) -> None:
    """A warn stage without its own color restores the pre-warning color
    after a colored effect stage, like its brightness fallback."""
    hass.states.async_set(REAL, "off", HS_CAPS)
    await setup_entries(
        hass,
        make_light_entry(
            effect_timeout=10,
            effect_brightness=50,
            effect_rgb_color=[0, 0, 255],
            warn_timeout=10,
        ),
    )

    await _turn_on_virtual(hass, brightness=200, hs_color=[100, 100])

    calls = _record_service_calls(hass)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)

    data = _real_on_calls(calls)[-1]["service_data"]
    assert tuple(data["rgb_color"]) == (0, 0, 255)
    assert data["brightness"] == 128  # 50% effect brightness
    assert tuple(_state(hass).attributes["hs_color"]) == (240.0, 100.0)

    calls.clear()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)

    assert _state(hass).attributes["molight_state"] == STATE_WARN
    data = _real_on_calls(calls)[-1]["service_data"]
    assert tuple(data["hs_color"]) == (100.0, 100.0)
    assert data["brightness"] == 200
    assert tuple(_state(hass).attributes["hs_color"]) == (100.0, 100.0)


# ---------------------------------------------------------------------------
# Restart restore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_color_restored_after_restart(hass: HomeAssistant) -> None:
    """The virtual color survives a restart via RestoreEntity, like
    brightness."""
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "off",
                {"color_mode": "hs", "hs_color": [120.0, 40.0], "brightness": 143},
            )
        ],
    )
    hass.states.async_set(REAL, "off", HS_CAPS)
    await setup_entries(hass, make_light_entry())

    # Turn on without specifying a color — the restored value must show.
    await _turn_on_virtual(hass)
    attrs = _state(hass).attributes
    assert tuple(attrs["hs_color"]) == (120.0, 40.0)
    assert attrs["color_mode"] == "hs"
    assert attrs["brightness"] == 143


@pytest.mark.asyncio
async def test_restored_color_dropped_without_color_members(
    hass: HomeAssistant,
) -> None:
    """A restored color the members can no longer show is discarded when the
    capability derivation falls back to brightness-only."""
    mock_restore_cache(
        hass,
        [State(VIRTUAL, "off", {"color_mode": "hs", "hs_color": [120.0, 40.0]})],
    )
    hass.states.async_set(REAL, "off", {"supported_color_modes": ["brightness"]})
    await setup_entries(hass, make_light_entry())

    await _turn_on_virtual(hass)
    attrs = _state(hass).attributes
    assert attrs["supported_color_modes"] == ["brightness"]
    assert attrs["color_mode"] == "brightness"
    assert attrs.get("hs_color") is None


@pytest.mark.asyncio
async def test_color_temp_restored_after_restart(hass: HomeAssistant) -> None:
    """A color-temp state restores by its color_mode: the derived hs_color HA
    stores alongside must not be mistaken for the authoritative color."""
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "off",
                {
                    "color_mode": "color_temp",
                    "color_temp_kelvin": 3200,
                    # HA computes an hs equivalent for display; the mode wins.
                    "hs_color": [27.0, 40.0],
                    "brightness": 143,
                },
            )
        ],
    )
    hass.states.async_set(REAL, "off", HS_TEMP_CAPS)
    await setup_entries(hass, make_light_entry())

    await _turn_on_virtual(hass)
    attrs = _state(hass).attributes
    assert attrs["color_mode"] == "color_temp"
    assert attrs["color_temp_kelvin"] == 3200


@pytest.mark.asyncio
async def test_restart_mid_warning_restores_pre_warn_color(
    hass: HomeAssistant,
) -> None:
    """A restart landing mid-warning with the lights still on undoes the
    stage's color and brightness from the persisted pre_warn attributes."""
    mock_restore_cache(
        hass,
        [
            State(
                VIRTUAL,
                "on",
                {
                    # The warn-stage red was showing when HA went down.
                    "color_mode": "hs",
                    "hs_color": [0.0, 100.0],
                    "brightness": 255,
                    "pre_warn_brightness": 180,
                    "pre_warn_color": {"hs_color": [100.0, 50.0]},
                },
            )
        ],
    )
    hass.states.async_set(
        REAL,
        "on",
        {**HS_CAPS, "color_mode": "hs", "hs_color": [0.0, 100.0], "brightness": 255},
    )
    calls = _record_service_calls(hass)
    await setup_entries(
        hass, make_light_entry(warn_timeout=15, warn_rgb_color=[255, 0, 0])
    )
    await settle(hass)

    attrs = _state(hass).attributes
    assert _state(hass).state == "on"
    assert attrs["molight_state"] == STATE_ACTIVE
    assert attrs["pre_warn_brightness"] is None
    assert attrs["pre_warn_color"] is None
    data = _real_on_calls(calls)[-1]["service_data"]
    assert data["brightness"] == 180
    assert tuple(data["hs_color"]) == (100.0, 50.0)


# ---------------------------------------------------------------------------
# Color-temperature members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_color_temp_mirrored(hass: HomeAssistant) -> None:
    """A member in color_temp mode hands the virtual light its kelvin — at
    startup and on an external recolor — even though it also carries a
    derived hs_color."""
    member = {
        **HS_TEMP_CAPS,
        "color_mode": "color_temp",
        "color_temp_kelvin": 3000,
        "hs_color": [27.0, 47.0],  # derived by HA; the mode decides
        "brightness": 180,
    }
    hass.states.async_set(REAL, "on", member)
    await setup_entries(hass, make_light_entry())

    attrs = _state(hass).attributes
    assert attrs["color_mode"] == "color_temp"
    assert attrs["color_temp_kelvin"] == 3000

    hass.states.async_set(REAL, "on", {**member, "color_temp_kelvin": 4000})
    await settle(hass)
    attrs = _state(hass).attributes
    assert attrs["color_temp_kelvin"] == 4000
    assert attrs["last_color_change_physical"] is not None


@pytest.mark.asyncio
async def test_pre_warn_snapshot_keeps_color_temp(hass: HomeAssistant, freezer) -> None:
    """A color-temp light entering the warning snapshots its kelvin, and a
    re-trigger with no color of its own restores it."""
    hass.states.async_set(REAL, "off", HS_TEMP_CAPS)
    await setup_entries(
        hass, make_light_entry(warn_timeout=15, warn_rgb_color=[255, 0, 0])
    )

    await _turn_on_virtual(hass, brightness=200, color_temp_kelvin=3000)

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)

    attrs = _state(hass).attributes
    assert attrs["molight_state"] == STATE_WARN
    assert attrs["pre_warn_color"] == {"color_temp_kelvin": 3000}

    await _turn_on_virtual(hass)  # re-trigger without brightness or color

    attrs = _state(hass).attributes
    assert attrs["molight_state"] == STATE_ACTIVE
    assert attrs["color_mode"] == "color_temp"
    assert attrs["color_temp_kelvin"] == 3000
    assert attrs["brightness"] == 200
    assert attrs["pre_warn_color"] is None


@pytest.mark.asyncio
async def test_capability_shift_to_color_temp_only_member(
    hass: HomeAssistant,
) -> None:
    """A color-temp-only member appearing moves a brightness-only virtual
    light onto the color_temp mode."""
    await setup_entries(hass, make_light_entry())
    assert _state(hass).attributes["supported_color_modes"] == ["brightness"]

    hass.states.async_set(
        REAL,
        "off",
        {
            "supported_color_modes": ["color_temp"],
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 6500,
        },
    )
    await settle(hass)

    attrs = _state(hass).attributes
    assert attrs["supported_color_modes"] == ["color_temp"]

    # HA only reports color_mode while on — turn on to observe the new mode.
    # (HA derives a display hs_color from the kelvin, so only the mode and
    # kelvin are asserted.)
    await _turn_on_virtual(hass, color_temp_kelvin=3500)
    attrs = _state(hass).attributes
    assert attrs["color_mode"] == "color_temp"
    assert attrs["color_temp_kelvin"] == 3500


@pytest.mark.asyncio
async def test_inconsistent_member_color_ignored_when_brightness_only(
    hass: HomeAssistant,
) -> None:
    """A member reporting a color it doesn't advertise support for is not
    mirrored — the brightness-only virtual light reports no color."""
    hass.states.async_set(
        REAL,
        "on",
        {
            "supported_color_modes": ["brightness"],
            "hs_color": [100.0, 50.0],
            "brightness": 120,
        },
    )
    await setup_entries(hass, make_light_entry())

    attrs = _state(hass).attributes
    assert attrs["color_mode"] == "brightness"
    assert attrs.get("hs_color") is None
