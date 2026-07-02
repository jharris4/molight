"""Tests for the Limer Virtual Light."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.limer.const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_ENTITY,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    CONF_TIME_WINDOWS,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_SCHEDULE,
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
    assert state.attributes.get("limer_state") == STATE_ACTIVE


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
    assert state.attributes.get("limer_state") == STATE_IDLE


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
    assert state.attributes["limer_state"] == STATE_IDLE

    # It gets dark while occupancy is still active → light comes on.
    hass.states.async_set("sensor.lux_1", "5")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "on"
    assert state.attributes["limer_state"] == STATE_OCCUPIED


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
    assert state.attributes["limer_state"] == STATE_OCCUPIED

    hass.states.async_set("binary_sensor.motion_1", "off")
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "on"
    assert state.attributes["limer_state"] == STATE_COUNTDOWN

    # Countdown anchors to latest_occupied_time: light_timeout (60s) minus the
    # occupancy timeout (30s) ≈ 30s after the sensor cleared.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await _settle(hass)

    state = hass.states.get("light.gated_light")
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE


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
    assert state.attributes["limer_state"] == STATE_ACTIVE
    assert state.attributes["last_on_physical"] is not None

    hass.states.async_set("light.living_room", "off")
    await _settle(hass)

    state = hass.states.get("light.test_light")
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE


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
    assert state.attributes["limer_state"] == STATE_COUNTDOWN

    # T0+61: the original 60s on-period is exhausted.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await _settle(hass)

    state = hass.states.get("light.kitchen_light")
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE


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
    assert state.attributes["limer_state"] == STATE_SCHEDULED
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
    assert state.attributes["limer_state"] == STATE_IDLE
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
    assert state.attributes["limer_state"] == STATE_SCHEDULED
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
    assert state.attributes["limer_state"] == STATE_IDLE


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
    assert state.attributes["limer_state"] == STATE_SCHEDULED

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
    assert state.attributes["limer_state"] == STATE_OCCUPIED

    # Window ends — lights forced off even though occupancy never cleared.
    t = datetime(2026, 7, 3, 7, 0, 2, tzinfo=timezone.utc)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await _settle(hass)

    state = hass.states.get("light.gate_light")
    assert state.state == "off"
    assert state.attributes["limer_state"] == STATE_IDLE


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
    assert state.attributes["limer_state"] == STATE_IDLE
