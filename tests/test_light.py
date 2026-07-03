"""Tests for the MoLight Virtual Light."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_MODE,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    CONF_TIME_WINDOWS,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
    ILLUMINANCE_MODE_GATE,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
)
from tests.conftest import settle


def _gated_light_entry() -> MockConfigEntry:
    """A virtual light gated by the fixture occupancy and illuminance sensors."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Gated Light",
            CONF_LIGHTS: ["light.living_room"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
            CONF_ILLUMINANCE_ENTITY: "binary_sensor.test_illuminance",
        },
    )


async def _setup_entries(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    for entry in entries:
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _night_schedule_entry() -> MockConfigEntry:
    """A 21:00 → 07:00 overnight schedule (legacy string edges)."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Night Schedule",
            CONF_TIME_WINDOWS: [{"start": "21:00", "end": "07:00"}],
        },
    )


def _follow_light_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Porch Light",
            CONF_LIGHTS: ["light.porch_real"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            CONF_SCHEDULE_MODE: SCHEDULE_MODE_FOLLOW,
        },
    )


_settle = settle


@pytest.mark.asyncio
async def test_virtual_light_setup(hass: HomeAssistant, light_entry: MockConfigEntry) -> None:
    """Virtual light is created and starts off."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state is not None
    assert state.state == "off"


@pytest.mark.asyncio
async def test_virtual_light_turn_on(hass: HomeAssistant, light_entry: MockConfigEntry) -> None:
    """Turning on the virtual light turns on real lights and starts the timer."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call("light", "turn_on", {"entity_id": "light.test_light"})
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state.state == "on"
    assert state.attributes.get("molight_state") == STATE_ACTIVE


@pytest.mark.asyncio
async def test_virtual_light_auto_off(
    hass: HomeAssistant, light_entry: MockConfigEntry, freezer
) -> None:
    """Virtual light turns off automatically after the configured timeout."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call("light", "turn_on", {"entity_id": "light.test_light"})
    await hass.async_block_till_done()
    assert hass.states.get("light.test_light").state == "on"

    # Fast-forward past the timeout (60s in fixture)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state.state == "off"
    assert state.attributes.get("molight_state") == STATE_IDLE


@pytest.mark.asyncio
async def test_light_restores_last_on_timestamps(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Turn-on attribution timestamps survive a restart via RestoreEntity."""
    ts = "2026-07-01T10:00:00+00:00"
    mock_restore_cache(
        hass,
        [State("light.test_light", "off", {"last_on_virtual": ts})],
    )

    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state.attributes["last_on_virtual"] == ts
    assert state.attributes["last_on_physical"] is None


@pytest.mark.asyncio
async def test_light_restores_brightness(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Virtual brightness survives a restart via RestoreEntity."""
    mock_restore_cache(
        hass,
        [State("light.test_light", "on", {"brightness": 143})],
    )

    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    # Turn on without specifying brightness — the restored value must show.
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.test_light"}
    )
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state.state == "on"
    assert state.attributes["brightness"] == 143


@pytest.mark.asyncio
async def test_occupancy_suppressed_when_bright(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, illuminance_entry: MockConfigEntry
) -> None:
    """Occupancy must not turn the light on while the illuminance sensor reads bright."""
    await _setup_entries(hass, occupancy_entry, illuminance_entry, _gated_light_entry())

    hass.states.async_set("sensor.lux_1", "500")  # bright (threshold 10 in fixture)
    await _settle(hass)

    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE

    # It gets dark while occupancy is still active → light comes on.
    hass.states.async_set("sensor.lux_1", "5")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED


@pytest.mark.asyncio
async def test_occupancy_turns_light_on_when_dark(
    hass: HomeAssistant,
    occupancy_entry: MockConfigEntry,
    illuminance_entry: MockConfigEntry,
    freezer,
) -> None:
    """When dark, occupancy turns the light on; clearing starts the precise countdown."""
    await _setup_entries(hass, occupancy_entry, illuminance_entry, _gated_light_entry())

    hass.states.async_set("sensor.lux_1", "5")  # dark
    await _settle(hass)

    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED

    hass.states.async_set("binary_sensor.motion_1", "off")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_COUNTDOWN

    # Countdown anchors to latest_occupied_time: light_timeout (60s) minus the
    # occupancy timeout (30s) ≈ 30s after the sensor cleared.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_external_light_adoption(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """An externally switched real light is adopted and released."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("light.living_room", "on")
    await _settle(hass)

    state = hass.states.get("light.test_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["last_on_physical"] is not None

    hass.states.async_set("light.living_room", "off")
    await _settle(hass)

    state = hass.states.get("light.test_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_illuminance_dark_resumes_remaining_time(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry, freezer
) -> None:
    """Bright interrupts a manual on-period; dark resumes only the remaining time."""
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Kitchen Light",
            CONF_LIGHTS: ["light.kitchen_real"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_ILLUMINANCE_ENTITY: "binary_sensor.test_illuminance",
        },
    )
    await _setup_entries(hass, illuminance_entry, light)

    # Manual turn-on at T0 (dark by default) — ACTIVE with a 60s timer.
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.kitchen_light"}
    )
    await hass.async_block_till_done()
    assert hass.states.get("light.kitchen_light").state == "on"

    # T0+20: it gets bright — lights forced off.
    freezer.tick(timedelta(seconds=20))
    hass.states.async_set("sensor.lux_1", "500")
    await _settle(hass)
    assert hass.states.get("light.kitchen_light").state == "off"

    # T0+30: dark again — resume with the remaining 30s of the on-period.
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("sensor.lux_1", "5")
    await _settle(hass)

    state = hass.states.get("light.kitchen_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_COUNTDOWN

    # T0+61: the original 60s on-period is exhausted.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await _settle(hass)

    state = hass.states.get("light.kitchen_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_brightness_change_resets_countdown(
    hass: HomeAssistant, light_entry: MockConfigEntry, freezer
) -> None:
    """An external brightness change is activity: timestamp + timer restart."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("light.living_room", "on")
    await _settle(hass)
    assert hass.states.get("light.test_light").state == "on"

    # 50s into the 60s timer someone dims the light.
    freezer.tick(timedelta(seconds=50))
    hass.states.async_set("light.living_room", "on", {"brightness": 128})
    await _settle(hass)

    state = hass.states.get("light.test_light")
    assert state.attributes["last_brightness_change_physical"] is not None
    assert state.attributes["molight_state"] == STATE_ACTIVE

    # 55s after the dim (105s after turn-on): the original timer would have
    # fired at 60s — the reset one has 5s left.
    freezer.tick(timedelta(seconds=55))
    async_fire_time_changed(hass)
    await _settle(hass)
    assert hass.states.get("light.test_light").state == "on"

    # 61s after the dim: the reset timer fires.
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await _settle(hass)
    assert hass.states.get("light.test_light").state == "off"


@pytest.mark.asyncio
async def test_brightness_zero_treated_as_off(
    hass: HomeAssistant, light_entry: MockConfigEntry
) -> None:
    """Brightness 0 with state still 'on' is treated as the light being off."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("light.living_room", "on", {"brightness": 200})
    await _settle(hass)
    assert hass.states.get("light.test_light").state == "on"

    hass.states.async_set("light.living_room", "on", {"brightness": 0})
    await _settle(hass)

    state = hass.states.get("light.test_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["last_brightness_change_physical"] is not None

    # 0 → non-zero is a turn-on in disguise: back to ACTIVE with physical
    # attribution.
    hass.states.async_set("light.living_room", "on", {"brightness": 150})
    await _settle(hass)

    state = hass.states.get("light.test_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_ACTIVE
    assert state.attributes["last_on_physical"] is not None


@pytest.mark.asyncio
async def test_virtual_brightness_change(
    hass: HomeAssistant, light_entry: MockConfigEntry, freezer
) -> None:
    """Brightness set through the virtual entity is tracked and extends the timer."""
    light_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(light_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.test_light"}
    )
    await hass.async_block_till_done()
    state = hass.states.get("light.test_light")
    assert state.attributes["last_brightness_change_virtual"] is None

    # 50s into the 60s timer the user dims via the virtual entity.
    freezer.tick(timedelta(seconds=50))
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.test_light", "brightness": 100},
    )
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state.attributes["last_brightness_change_virtual"] is not None
    assert state.attributes["last_brightness_change_physical"] is None
    assert state.attributes["brightness"] == 100

    # The timer was restarted: still on 55s later, off after the full 60s.
    freezer.tick(timedelta(seconds=55))
    async_fire_time_changed(hass)
    await _settle(hass)
    assert hass.states.get("light.test_light").state == "on"

    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await _settle(hass)
    assert hass.states.get("light.test_light").state == "off"


def _fd_occupancy_entry() -> MockConfigEntry:
    """Occupancy sensor with false-detection classification enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "FD Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )


def _fd_light_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "FD Light",
            CONF_LIGHTS: ["light.fd_real"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.fd_occupancy",
        },
    )


@pytest.mark.asyncio
async def test_false_detection_quick_off(hass: HomeAssistant, freezer) -> None:
    """Lights lit by a false-detection cycle turn off after the short delay."""
    await _setup_entries(hass, _fd_occupancy_entry(), _fd_light_entry())

    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)
    assert hass.states.get("light.fd_light").state == "on"

    # Single-blip clear (31s on vs 30s hold) → false detection.
    freezer.tick(timedelta(seconds=31))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await _settle(hass)
    assert hass.states.get("light.fd_light").state == "on"  # short countdown running

    # The 5s quick-off fires instead of the normal 30s countdown.
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await _settle(hass)

    state = hass.states.get("light.fd_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_false_detection_never_cuts_manual_lights(
    hass: HomeAssistant, freezer
) -> None:
    """A false clear must not shorten lights the user turned on themselves."""
    await _setup_entries(hass, _fd_occupancy_entry(), _fd_light_entry())

    # User turns the light on; occupancy blips on afterwards.
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.fd_light"}
    )
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)
    assert hass.states.get("light.fd_light").attributes["molight_state"] == STATE_OCCUPIED

    # False clear — but occupancy didn't light these lights, so the normal
    # countdown (60s here, lot never advanced) applies, not the 5s quick-off.
    freezer.tick(timedelta(seconds=31))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await _settle(hass)

    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await _settle(hass)

    state = hass.states.get("light.fd_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_COUNTDOWN


@pytest.mark.asyncio
async def test_stale_occupancy_ownership_never_cuts_user_lights(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry, freezer
) -> None:
    """Occupancy ownership must not survive a force-off into a user on-period.

    Regression: the illuminance force-off left the occupancy-ownership flag
    set, so a later user-initiated on-period could be cut short by a false
    occupancy clear.
    """
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "FD Light",
            CONF_LIGHTS: ["light.fd_real"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.fd_occupancy",
            CONF_ILLUMINANCE_ENTITY: "binary_sensor.test_illuminance",
        },
    )
    await _setup_entries(hass, _fd_occupancy_entry(), illuminance_entry, light)

    # Occupancy lights the room (dark by default) — occupancy owns the lights.
    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)
    assert hass.states.get("light.fd_light").state == "on"

    # False-blip clear, then bright forces everything off.
    freezer.tick(timedelta(seconds=31))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await _settle(hass)
    hass.states.async_set("sensor.lux_1", "500")
    await _settle(hass)
    assert hass.states.get("light.fd_light").state == "off"

    # The user physically turns a light on; then it gets dark again.
    hass.states.async_set("light.fd_real", "on")
    await _settle(hass)
    assert hass.states.get("light.fd_light").state == "on"
    hass.states.async_set("sensor.lux_1", "5")
    await _settle(hass)

    # A fresh occupancy blip arrives and false-clears over the user's lights.
    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)
    freezer.tick(timedelta(seconds=31))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await _settle(hass)

    # The quick-off (5s) must NOT fire — the user owns this on-period.
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await _settle(hass)

    state = hass.states.get("light.fd_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_COUNTDOWN


@pytest.mark.asyncio
async def test_illuminance_gate_mode_never_forces_off(
    hass: HomeAssistant,
    occupancy_entry: MockConfigEntry,
    illuminance_entry: MockConfigEntry,
    freezer,
) -> None:
    """Gate mode: bright never turns lights off, but still gates turn-ons."""
    light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Gate Only Light",
            CONF_LIGHTS: ["light.den_real"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
            CONF_ILLUMINANCE_ENTITY: "binary_sensor.test_illuminance",
            CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_GATE,
        },
    )
    await _setup_entries(hass, occupancy_entry, illuminance_entry, light)

    # Dark + occupancy → lights on.
    hass.states.async_set("sensor.lux_1", "5")
    await _settle(hass)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)
    assert hass.states.get("light.gate_only_light").state == "on"

    # It reads bright (e.g. the lights themselves raised the lux) — the
    # lights must stay on instead of oscillating.
    hass.states.async_set("sensor.lux_1", "500")
    await _settle(hass)

    state = hass.states.get("light.gate_only_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED

    # Occupancy clears → normal precise countdown turns them off.
    hass.states.async_set("binary_sensor.motion_1", "off")
    await _settle(hass)
    assert hass.states.get("light.gate_only_light").state == "on"

    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await _settle(hass)
    assert hass.states.get("light.gate_only_light").state == "off"

    # Still bright: a new occupancy trigger stays gated.
    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)

    state = hass.states.get("light.gate_only_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_follow_mode_lifecycle(hass: HomeAssistant, freezer) -> None:
    """Follow-mode light turns on at window start, ignores light_timeout, off at end."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup_entries(hass, _night_schedule_entry(), _follow_light_entry())

    assert hass.states.get("light.porch_light").state == "off"

    # Window starts at 21:00.
    t = datetime(2026, 7, 2, 21, 0, 2, tzinfo=timezone.utc)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await _settle(hass)

    state = hass.states.get("light.porch_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["schedule_window_start"] == "2026-07-02T21:00:00+00:00"

    # Way past light_timeout (60s) — no timer runs in SCHEDULED.
    t = datetime(2026, 7, 2, 23, 0, 0, tzinfo=timezone.utc)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await _settle(hass)
    assert hass.states.get("light.porch_light").state == "on"

    # Window ends at 07:00 next morning.
    t = datetime(2026, 7, 3, 7, 0, 2, tzinfo=timezone.utc)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await _settle(hass)

    state = hass.states.get("light.porch_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
    assert state.attributes["schedule_window_start"] is None


@pytest.mark.asyncio
async def test_follow_mode_restart_catch_up(hass: HomeAssistant, freezer) -> None:
    """A window start missed while HA was down is applied at startup."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 22:00:00+00:00")  # already inside the window
    await _setup_entries(hass, _night_schedule_entry(), _follow_light_entry())
    await _settle(hass)

    state = hass.states.get("light.porch_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED
    assert state.attributes["schedule_window_start"] == "2026-07-02T21:00:00+00:00"


@pytest.mark.asyncio
async def test_follow_mode_respects_manual_off(hass: HomeAssistant, freezer) -> None:
    """A window already applied before restart is not re-asserted at startup."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 22:00:00+00:00")
    await _setup_entries(hass, _night_schedule_entry())

    marker = hass.states.get("binary_sensor.night_schedule").attributes[
        "current_window_start"
    ]
    assert marker == "2026-07-02T21:00:00+00:00"

    # The light had already applied this window's start before the restart —
    # so its being off now means the user turned it off manually.
    mock_restore_cache(
        hass,
        [State("light.porch_light", "off", {"schedule_window_start": marker})],
    )
    await _setup_entries(hass, _follow_light_entry())
    await _settle(hass)

    state = hass.states.get("light.porch_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_follow_mode_manual_re_on_rejoins_window(
    hass: HomeAssistant, freezer
) -> None:
    """Turning the light back on mid-window rejoins SCHEDULED (no auto-off timer)."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 22:00:00+00:00")  # inside the window
    await _setup_entries(hass, _night_schedule_entry(), _follow_light_entry())
    await _settle(hass)
    assert hass.states.get("light.porch_light").state == "on"

    # Manual off mid-window — respected.
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.porch_light"}
    )
    await _settle(hass)
    assert hass.states.get("light.porch_light").state == "off"

    # Manual on again — must rejoin the window, not run the 60s timer.
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.porch_light"}
    )
    await _settle(hass)

    state = hass.states.get("light.porch_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_SCHEDULED

    # Well past light_timeout, still before window end — must stay on.
    t = datetime(2026, 7, 2, 23, 30, 0, tzinfo=timezone.utc)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await _settle(hass)
    assert hass.states.get("light.porch_light").state == "on"


@pytest.mark.asyncio
async def test_gate_mode_blocks_occupancy_outside_window(
    hass: HomeAssistant, freezer, occupancy_entry: MockConfigEntry
) -> None:
    """Gate mode: occupancy is ignored outside the window, honored at window start."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")  # before the 21:00 window
    gate_light = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: "Gate Light",
            CONF_LIGHTS: ["light.hall_real"],
            CONF_LIGHT_TIMEOUT: 60,
            CONF_OCCUPANCY_ENTITY: "binary_sensor.test_occupancy",
            CONF_SCHEDULE_ENTITY: "binary_sensor.night_schedule",
            CONF_SCHEDULE_MODE: SCHEDULE_MODE_GATE,
        },
    )
    await _setup_entries(hass, occupancy_entry, _night_schedule_entry(), gate_light)

    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)
    assert hass.states.get("light.gate_light").state == "off"

    # Window starts — occupancy is still active, so lights come on now.
    t = datetime(2026, 7, 2, 21, 0, 2, tzinfo=timezone.utc)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await _settle(hass)

    state = hass.states.get("light.gate_light")
    assert state.state == "on"
    assert state.attributes["molight_state"] == STATE_OCCUPIED

    # Window ends — lights forced off even though occupancy never cleared.
    t = datetime(2026, 7, 3, 7, 0, 2, tzinfo=timezone.utc)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await _settle(hass)

    state = hass.states.get("light.gate_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE


@pytest.mark.asyncio
async def test_bright_turns_light_off(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, illuminance_entry: MockConfigEntry
) -> None:
    """Illuminance switching to bright turns an occupancy-lit light off."""
    await _setup_entries(hass, occupancy_entry, illuminance_entry, _gated_light_entry())

    hass.states.async_set("sensor.lux_1", "5")
    await _settle(hass)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await _settle(hass)
    assert hass.states.get("light.gated_light").state == "on"

    hass.states.async_set("sensor.lux_1", "500")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "off"
    assert state.attributes["molight_state"] == STATE_IDLE
