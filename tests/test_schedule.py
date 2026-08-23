"""Tests for the MoLight Virtual Schedule Sensor."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant.core import State
from homeassistant.helpers.sun import get_astral_event_date
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.molight.const import (
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_SCHEDULE_DEFINITION,
    CONF_SCHEDULE_INVERT,
    CONF_SCHEDULE_SOURCE,
    CONF_TIME_WINDOWS,
    DOMAIN,
    ENTITY_TYPE_SCHEDULE,
    SCHEDULE_DEFINITION_BINARY_SENSOR,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _schedule_entry(windows: list) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Night Schedule",
            CONF_TIME_WINDOWS: windows,
        },
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_fixed_overnight_window(hass: HomeAssistant, freezer) -> None:
    """Legacy fixed-time overnight window flips at the exact boundaries."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(hass, _schedule_entry([{"start": "21:00", "end": "07:00"}]))

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["next_transition"] == "2026-07-02T21:00:00+00:00"

    # Window starts at 21:00.
    t = datetime(2026, 7, 2, 21, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T21:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-03T07:00:00+00:00"

    # Window ends at 07:00 the next morning.
    t = datetime(2026, 7, 3, 7, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None


@pytest.mark.asyncio
async def test_sun_anchored_edge(hass: HomeAssistant, freezer) -> None:
    """A sun-anchored edge resolves to the astral event, combined with the time."""
    # Default test location/timezone; sunset is ~20:00 local in July, so the
    # 'earliest of sunset and 23:00' start resolves to sunset.
    day = date(2026, 7, 2)
    sunset = get_astral_event_date(hass, "sunset", day)
    assert sunset is not None

    freezer.move_to(sunset + timedelta(minutes=5))
    await _setup(
        hass,
        _schedule_entry(
            [
                {
                    "start": {"time": "23:00", "sun": "sunset", "combine": "earliest"},
                    "end": {"time": "23:30"},
                }
            ]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == sunset.isoformat()


@pytest.mark.asyncio
async def test_sun_edge_with_offset(hass: HomeAssistant, freezer) -> None:
    """A sun offset shifts the resolved edge (sunset - 15 min)."""
    day = date(2026, 7, 2)
    sunset = get_astral_event_date(hass, "sunset", day)
    assert sunset is not None
    shifted = sunset - timedelta(minutes=15)

    # 5 minutes after (sunset - 15) but still before plain sunset: only the
    # offset edge puts us inside the window.
    freezer.move_to(shifted + timedelta(minutes=5))
    await _setup(
        hass,
        _schedule_entry(
            [
                {
                    "start": {
                        "time": "23:00",
                        "sun": "sunset",
                        "offset": -15,
                        "combine": "earliest",
                    },
                    "end": {"time": "23:30"},
                }
            ]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == shifted.isoformat()


@pytest.mark.asyncio
async def test_polar_day_fixed_time_stands_alone(hass: HomeAssistant, freezer) -> None:
    """A sun anchor whose event doesn't occur (polar day/night) resolves to
    nothing, leaving the edge's fixed time to stand alone."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 21:30:00+00:00")
    with patch(
        "custom_components.molight.binary_sensor.get_astral_event_date",
        return_value=None,
    ):
        await _setup(
            hass,
            _schedule_entry(
                [
                    {
                        "start": {
                            "time": "21:00",
                            "sun": "sunset",
                            "combine": "latest",
                        },
                        "end": "23:00",
                    }
                ]
            ),
        )
        state = hass.states.get("binary_sensor.night_schedule")

    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T21:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-02T23:00:00+00:00"


@pytest.mark.asyncio
async def test_overlapping_windows(hass: HomeAssistant, freezer) -> None:
    """With several simultaneously active windows the latest start wins as
    current_window_start, and the sensor stays on across the seam where one
    window ends inside another."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 21:00:00+00:00")
    await _setup(
        hass,
        _schedule_entry(
            [
                {"start": "18:00", "end": "23:00"},
                {"start": "20:00", "end": "02:00"},
            ]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    # Both windows are active; the later start identifies the window.
    assert state.attributes["current_window_start"] == "2026-07-02T20:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-02T23:00:00+00:00"

    # The first window ends at 23:00 — still inside the second window.
    t = datetime(2026, 7, 2, 23, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T20:00:00+00:00"

    # The overnight window ends at 02:00 — now everything is over.
    t = datetime(2026, 7, 3, 2, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.night_schedule").state == "off"


@pytest.mark.asyncio
async def test_inverted_overlapping_windows_begin_after_merged_interval(
    hass: HomeAssistant, freezer
) -> None:
    """An inner window end is not the start of an inverted effective window."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 21:00:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Inverted Overlap",
            CONF_TIME_WINDOWS: [
                {"start": "18:00", "end": "23:00"},
                {"start": "20:00", "end": "02:00"},
            ],
            CONF_SCHEDULE_INVERT: True,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get("binary_sensor.inverted_overlap")
    assert state.state == "off"
    assert state.attributes["next_transition"] == "2026-07-02T23:00:00+00:00"

    # The first raw window ends, but the overlapping overnight window remains.
    t = datetime(2026, 7, 2, 23, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.inverted_overlap")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None
    assert state.attributes["next_transition"] == "2026-07-03T02:00:00+00:00"

    # Only the end of the merged raw interval starts the inverted on-period.
    t = datetime(2026, 7, 3, 2, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.inverted_overlap")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-03T02:00:00+00:00"


@pytest.mark.asyncio
async def test_inverted_unresolvable_sun_window_uses_stable_marker(
    hass: HomeAssistant, freezer
) -> None:
    """A continuously-on inverted polar schedule has a stable Follow marker."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 12:00:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Inverted Polar",
            CONF_TIME_WINDOWS: [
                {"start": {"sun": "sunset"}, "end": {"sun": "sunrise"}}
            ],
            CONF_SCHEDULE_INVERT: True,
        },
    )
    with patch(
        "custom_components.molight.binary_sensor.get_astral_event_date",
        return_value=None,
    ):
        await _setup(hass, entry)
        state = hass.states.get("binary_sensor.inverted_polar")
        assert state.state == "on"
        assert state.attributes["current_window_start"] == "inverted"
        assert state.attributes["next_transition"] is None

        # The daily polar re-check must not manufacture a new effective window.
        t = datetime(2026, 7, 3, 0, 0, 2, tzinfo=UTC)
        freezer.move_to(t)
        async_fire_time_changed(hass, t)
        await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.inverted_polar")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "inverted"
    assert state.attributes["next_transition"] is None


@pytest.mark.asyncio
async def test_no_windows_stays_off(hass: HomeAssistant, freezer) -> None:
    """With no windows the sensor is off and the daily re-check keeps working."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(hass, _schedule_entry([]))

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["next_transition"] is None

    # No boundaries in sight — the sensor re-evaluates tomorrow without error.
    freezer.tick(timedelta(days=1, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.night_schedule").state == "off"


@pytest.mark.asyncio
async def test_invalid_window_edges_never_activate(
    hass: HomeAssistant, freezer
) -> None:
    """Unresolvable edges (bad times, wrong types, missing end) yield no window."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(
        hass,
        _schedule_entry(
            [
                {"start": "25:99", "end": "23:00"},  # unparsable start time
                {"start": 42, "end": "23:00"},  # wrong edge type
                {"start": "19:00"},  # missing end
            ]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None
    assert state.attributes["next_transition"] is None


@pytest.mark.asyncio
async def test_inverted_time_window_uses_effective_on_period(
    hass: HomeAssistant, freezer
) -> None:
    """Inversion turns the complement into the schedule's effective window."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Night Schedule",
            CONF_TIME_WINDOWS: [{"start": "21:00", "end": "07:00"}],
            CONF_SCHEDULE_INVERT: True,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-02T07:00:00+00:00"

    t = datetime(2026, 7, 2, 21, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None
    assert state.attributes["next_transition"] == "2026-07-03T07:00:00+00:00"

    # The configured window ends, beginning the next effective inverted window.
    t = datetime(2026, 7, 3, 7, 0, 2, tzinfo=UTC)
    freezer.move_to(t)
    async_fire_time_changed(hass, t)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-07-03T07:00:00+00:00"
    assert state.attributes["next_transition"] == "2026-07-03T21:00:00+00:00"


@pytest.mark.asyncio
async def test_source_schedule_mirrors_valid_states_and_holds_across_outage(
    hass: HomeAssistant,
) -> None:
    """A source outage is unavailable, not an effective off transition."""
    hass.states.async_set("binary_sensor.house_mode", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "House Mode",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: "binary_sensor.house_mode",
        },
    )
    await _setup(hass, entry)
    state = hass.states.get("binary_sensor.house_mode_2")
    assert state.state == "off"
    assert state.attributes["source_entity"] == "binary_sensor.house_mode"
    assert state.attributes["inverted"] is False

    hass.states.async_set("binary_sensor.house_mode", "on")
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.house_mode_2")
    assert state.state == "on"
    marker = state.attributes["current_window_start"]
    assert marker is not None
    assert state.attributes["next_transition"] is None

    hass.states.async_set("binary_sensor.house_mode", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.house_mode_2").state == "unavailable"

    hass.states.async_set("binary_sensor.house_mode", "on")
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.house_mode_2")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker

    hass.states.async_set("binary_sensor.house_mode", "off")
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.house_mode_2")
    assert state.state == "off"
    assert state.attributes["current_window_start"] is None


@pytest.mark.asyncio
async def test_inverted_source_schedule(hass: HomeAssistant) -> None:
    """Source inversion applies only to valid on/off values."""
    hass.states.async_set("binary_sensor.away", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "Home",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: "binary_sensor.away",
            CONF_SCHEDULE_INVERT: True,
        },
    )
    await _setup(hass, entry)
    state = hass.states.get("binary_sensor.home")
    assert state.state == "on"
    assert state.attributes["source_entity"] == "binary_sensor.away"
    assert state.attributes["inverted"] is True

    hass.states.async_set("binary_sensor.away", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.home").state == "off"

    hass.states.async_set("binary_sensor.away", "unknown")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.home").state == "unavailable"


@pytest.mark.asyncio
async def test_source_schedule_restores_marker_through_restart_outage(
    hass: HomeAssistant,
) -> None:
    """An effective window marker survives restart while its source is down."""
    source = "binary_sensor.house_mode"
    entity_id = "binary_sensor.house_mode_schedule"
    marker = "2026-07-02T21:00:00+00:00"
    mock_restore_cache(
        hass,
        [
            State(
                entity_id,
                "unavailable",
                {
                    "current_window_start": marker,
                    "source_entity": source,
                    "inverted": False,
                },
            )
        ],
    )
    hass.states.async_set(source, "unavailable")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "House Mode Schedule",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: source,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get(entity_id)
    assert state.state == "unavailable"

    hass.states.async_set(source, "on")
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
async def test_source_schedule_restores_marker_when_source_is_on(
    hass: HomeAssistant,
) -> None:
    """An active source at startup keeps the restored effective-window marker."""
    source = "binary_sensor.house_mode"
    entity_id = "binary_sensor.house_mode_schedule"
    marker = "2026-07-02T21:00:00+00:00"
    mock_restore_cache(
        hass,
        [
            State(
                entity_id,
                "on",
                {
                    "current_window_start": marker,
                    "source_entity": source,
                    "inverted": False,
                },
            )
        ],
    )
    hass.states.async_set(source, "on")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "House Mode Schedule",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: source,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["current_window_start"] == marker


@pytest.mark.asyncio
async def test_source_schedule_does_not_restore_marker_from_previous_source(
    hass: HomeAssistant,
) -> None:
    """Changing the source starts a new effective window instead of reusing one."""
    old_source = "binary_sensor.old_mode"
    source = "binary_sensor.new_mode"
    entity_id = "binary_sensor.house_mode_schedule"
    old_marker = "2026-07-02T21:00:00+00:00"
    mock_restore_cache(
        hass,
        [
            State(
                entity_id,
                "on",
                {
                    "current_window_start": old_marker,
                    "source_entity": old_source,
                    "inverted": False,
                },
            )
        ],
    )
    hass.states.async_set(source, "on")
    source_started = hass.states.get(source).last_changed.isoformat()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "House Mode Schedule",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: source,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["current_window_start"] == source_started
    assert state.attributes["current_window_start"] != old_marker


@pytest.mark.asyncio
async def test_sun_edge_ignores_unparsable_offset(hass: HomeAssistant, freezer) -> None:
    """A sun edge with a non-numeric offset resolves as if it had none."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-07-02 20:00:00+00:00")
    await _setup(
        hass,
        _schedule_entry(
            [{"start": {"sun": "sunset", "offset": "soon"}, "end": "23:00"}]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state in ("on", "off")
    # The window still resolved: a boundary is scheduled.
    assert state.attributes["next_transition"] is not None


@pytest.mark.asyncio
async def test_spring_forward_gap_edge_does_not_shadow_real_next_edge(
    hass: HomeAssistant, freezer
) -> None:
    """Edges are ordered by real instant, not wall clock, across a DST gap.

    On 2026-03-08 in America/New_York, 02:30 does not exist; resolved with
    fold=0 it lands on EST (07:30 UTC) — a *later* real instant than
    03:05 EDT (07:05 UTC) despite the earlier wall time. The 03:05 window's
    start must not be shadowed by the gap edge.
    """
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to("2026-03-08 06:50:00+00:00")  # 01:50 EST, before both edges
    await _setup(
        hass,
        _schedule_entry(
            [
                {"start": "03:05", "end": "06:00"},
                {"start": "02:30", "end": "02:45"},
            ]
        ),
    )

    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "off"
    # 02:30 resolves into the gap (07:30 UTC as EST) — a later real instant
    # than 03:05 EDT (07:05 UTC). The timer must be armed for 03:05.
    assert state.attributes["next_transition"] == "2026-03-08T03:05:00-04:00"

    freezer.move_to("2026-03-08 07:06:00+00:00")  # 03:06 EDT, past the edge
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.night_schedule")
    assert state.state == "on"
    assert state.attributes["current_window_start"] == "2026-03-08T03:05:00-04:00"


@pytest.mark.asyncio
async def test_source_schedule_discards_malformed_restored_marker(
    hass: HomeAssistant,
) -> None:
    """A stored window marker that no longer parses is dropped, not a crash.

    The live source then supplies a fresh marker from its own last change.
    """
    source = "binary_sensor.house_mode"
    entity_id = "binary_sensor.house_mode_schedule"
    mock_restore_cache(
        hass,
        [
            State(
                entity_id,
                "on",
                {
                    "current_window_start": "not-a-timestamp",
                    "source_entity": source,
                    "inverted": False,
                },
            )
        ],
    )
    hass.states.async_set(source, "on")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
            CONF_NAME: "House Mode Schedule",
            CONF_SCHEDULE_DEFINITION: SCHEDULE_DEFINITION_BINARY_SENSOR,
            CONF_SCHEDULE_SOURCE: source,
        },
    )
    await _setup(hass, entry)

    state = hass.states.get(entity_id)
    assert state.state == "on"
    marker = hass.states.get(source).last_changed.isoformat()
    assert state.attributes["current_window_start"] == marker
