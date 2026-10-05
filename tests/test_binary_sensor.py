"""Tests for MoLight binary sensor entities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.core import CoreState, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er, restore_state
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from custom_components.molight.const import (
    CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_ILLUMINANCE_HYSTERESIS,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_OCCUPANCY,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE,
)
from custom_components.molight.helpers import molight_config
from tests.conftest import (
    crash_entries,
    finish_startup,
    make_light_entry,
    restart_entries,
    settle,
    setup_entries,
)
from tests.real_entities import (
    RealBinary,
    RealLight,
    RealLux,
    add_real,
    add_real_with_entry,
    rename,
)


def _occupancy2_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Test Occupancy 2",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_2",
            CONF_OCCUPANCY_TIMEOUT: 45,
        },
    )


def _combined_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined Occupancy",
            CONF_TRIGGER_SENSORS: ["binary_sensor.test_occupancy"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.test_occupancy_2"],
        },
    )


@pytest.mark.asyncio
async def test_occupancy_sensor_setup(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Occupancy sensor is created and starts off."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state is not None
    assert state.state == "off"


@pytest.mark.asyncio
async def test_occupancy_mirrors_source_on(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Virtual occupancy turns on when the real sensor turns on."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "on"


@pytest.mark.asyncio
async def test_occupancy_clear_records_latest_occupied_time(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """Clearing mirrors the source immediately and back-dates latest_occupied_time.

    latest_occupied_time = clear time - occupancy_timeout (30s in fixture),
    estimating when the person actually left before the real sensor's own
    hardware delay elapsed.
    """
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_occupancy").state == "on"

    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "off"

    expected = datetime.now(UTC) - timedelta(seconds=30)
    assert state.attributes["latest_occupied_time"] == expected.isoformat()
    assert state.attributes["occupancy_timeout"] == 30


@pytest.mark.asyncio
async def test_occupancy_holds_forever_when_unavailable_clear_disabled(
    hass: HomeAssistant, freezer
) -> None:
    """With clear_on_unavailable disabled, a dropout never reads as a clear."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Test Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT: 0,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await hass.async_block_till_done()

    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "on"
    assert state.attributes["latest_occupied_time"] is None
    assert state.attributes["last_clear_unavailable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["unavailable", "unknown"])
async def test_occupancy_clears_after_unavailable_timeout(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer, bad: str
) -> None:
    """A prolonged dropout while occupied clears occupancy after the timeout.

    latest_occupied_time advances to the dropout moment immediately, and the
    clear is flagged via last_clear_unavailable, never as a false detection.
    """
    occupancy_entry.add_to_hass(hass)  # default clear-on-unavailable: 60s
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    dropout = datetime.now(UTC)
    hass.states.async_set("binary_sensor.motion_1", bad)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "on"  # not cleared yet
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_unavailable"] is True
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["false_detection_count"] == 0
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()


@pytest.mark.asyncio
async def test_occupancy_dropout_recovery_to_on_cancels_clear(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """A source recovering to 'on' before the timeout continues the occupancy."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=30))
    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    # The pending clear was cancelled: still on long after it would have fired.
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "on"
    assert state.attributes["last_clear_unavailable"] is False


@pytest.mark.asyncio
async def test_occupancy_dropout_recovery_to_off_is_real_clear(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """A source recovering straight to 'off' clears immediately and normally."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    dropout = datetime.now(UTC)
    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_unavailable"] is False
    # The dropout-time estimate is later than the back-dated clear estimate
    # (now - occupancy_timeout), so it stands.
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()

    # No stray timer fires later.
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_occupancy").state == "off"


@pytest.mark.asyncio
async def test_occupancy_recovery_to_off_after_unavailable_clear_is_noop(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """A recovery straight to 'off' after the unavailable clear fired is a no-op.

    The clear was already processed when the timeout expired; re-processing it
    would advance latest_occupied_time far past the dropout (as if someone had
    been present the whole time the sensor was dead) and wipe the
    last_clear_unavailable classification.
    """
    occupancy_entry.add_to_hass(hass)  # default clear-on-unavailable: 60s
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    dropout = datetime.now(UTC)
    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_occupancy").state == "off"

    # Hours later the source finally comes back, reporting 'off'.
    freezer.tick(timedelta(hours=2))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "off"
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()
    assert state.attributes["last_clear_unavailable"] is True
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["false_detection_count"] == 0


@pytest.mark.asyncio
async def test_occupancy_dropout_while_off_is_ignored(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """A dropout while not occupied must not advance lot or start a timer."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()
    lot = hass.states.get("binary_sensor.test_occupancy").attributes[
        "latest_occupied_time"
    ]

    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "off"
    assert state.attributes["latest_occupied_time"] == lot


@pytest.mark.asyncio
async def test_occupancy_ignores_attribute_only_updates(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """Attribute-only source updates must not advance latest_occupied_time."""
    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    lot_before = hass.states.get("binary_sensor.test_occupancy").attributes[
        "latest_occupied_time"
    ]
    assert lot_before is not None

    # Real sensors push battery/lux attribute updates while their state is off.
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_1", "off", {"battery": 50})
    await hass.async_block_till_done()

    lot_after = hass.states.get("binary_sensor.test_occupancy").attributes[
        "latest_occupied_time"
    ]
    assert lot_after == lot_before


@pytest.mark.asyncio
async def test_early_clear_is_false_detection(hass: HomeAssistant, freezer) -> None:
    """A clear before the sensor timeout is still a false detection."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Early Clear Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.early_clear_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is True
    assert state.attributes["false_detection_count"] == 1
    assert state.attributes["latest_occupied_time"] is None


@pytest.mark.asyncio
async def test_late_loading_source_clear_is_not_false_detection(
    hass: HomeAssistant, freezer
) -> None:
    """A source first provided after boot has an unknown start: no false flag.

    Until a slow integration provides it, HA shows the source as a restored
    placeholder; a detection arriving then may be a replay of one that began
    long before, so an early clear must take the normal countdown.
    """
    hass.states.async_set("binary_sensor.motion_1", "unavailable", {"restored": True})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Late Source Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.late_source_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["false_detection_count"] == 0
    assert state.attributes["latest_occupied_time"] is not None


@pytest.mark.asyncio
async def test_detection_after_a_plain_outage_is_still_classified(
    hass: HomeAssistant, freezer
) -> None:
    """Only the restored placeholder marks a first sighting: a source back
    from an ordinary outage starts a cycle that can still be flagged false."""
    hass.states.async_set("binary_sensor.motion_1", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Outage Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=31))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.outage_occupancy")
    assert state.attributes["last_clear_false_detection"] is True
    assert state.attributes["false_detection_count"] == 1


@pytest.mark.asyncio
async def test_false_detection_classification_and_count(
    hass: HomeAssistant, freezer
) -> None:
    """A single-blip cycle is counted and doesn't advance latest_occupied_time."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "FD Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    # The counter survives restarts.
    mock_restore_cache(
        hass,
        [State("binary_sensor.fd_occupancy", "off", {"false_detection_count": 5})],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # False cycle: one detection, cleared right after the 30s hold (31s on).
    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=31))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.fd_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is True
    assert state.attributes["false_detection_count"] == 6
    assert state.attributes["latest_occupied_time"] is None  # not advanced

    # Real cycle: re-triggered, on well past the hold time (45s).
    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=45))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.fd_occupancy")
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["false_detection_count"] == 6
    assert state.attributes["latest_occupied_time"] is not None


async def _edit_options(hass: HomeAssistant, entry: MockConfigEntry, **changes) -> None:
    options = {k: v for k, v in entry.data.items() if k != CONF_ENTITY_TYPE}
    hass.config_entries.async_update_entry(entry, options={**options, **changes})
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_occupancy_edit_mid_cycle_keeps_cycle_start_and_applies_new_timeout(
    hass: HomeAssistant, freezer
) -> None:
    """An edit while occupied keeps the cycle start; the clear uses the new timeout."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Edited Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    start = datetime.now(UTC)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=20))
    await _edit_options(hass, entry, **{CONF_OCCUPANCY_TIMEOUT: 40})
    state = hass.states.get("binary_sensor.edited_occupancy")
    assert state.state == "on"
    assert state.attributes["occupancy_timeout"] == 40
    assert state.attributes["last_on_time"] == start.isoformat()

    # 45 s on is a real cycle under the new 40 s timeout; 25 s from the edit is not.
    freezer.tick(timedelta(seconds=25))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.edited_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["latest_occupied_time"] == (
        (datetime.now(UTC) - timedelta(seconds=40)).isoformat()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("on_for", "false"), [(33, True), (34, False)])
async def test_false_detection_grace_edge(
    hass: HomeAssistant, freezer, on_for: int, false: bool
) -> None:
    """A cycle exactly the grace past the timeout is still a false detection."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Edge Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=on_for))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.edge_occupancy")
    assert state.attributes["last_clear_false_detection"] is false


def _dropout_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Dropout Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT: 60,
        },
    )


async def _occupied_then_dropped(hass: HomeAssistant, *entries) -> datetime:
    await setup_entries(hass, *entries)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await settle(hass)
    return datetime.now(UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("new_timeout", "clears_after"), [(45, 45), (5, 10), (0, None)]
)
async def test_occupancy_edit_during_dropout_keeps_the_clear_deadline(
    hass: HomeAssistant, freezer, new_timeout: int, clears_after: int | None
) -> None:
    """An edit mid-dropout keeps occupancy until the (new) deadline, not at once."""
    entry = _dropout_entry()
    dropout = await _occupied_then_dropped(hass, entry)
    freezer.tick(timedelta(seconds=10))
    await _edit_options(hass, entry, **{CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT: new_timeout})
    await settle(hass)

    state = hass.states.get("binary_sensor.dropout_occupancy")
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()
    if clears_after == 10:  # the shortened deadline has already passed
        assert state.state == "off"
        assert state.attributes["last_clear_unavailable"] is True
        return
    assert state.state == "on"

    freezer.move_to(dropout + timedelta(seconds=(clears_after or 3600) - 1))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("binary_sensor.dropout_occupancy").state == "on"
    if clears_after is None:
        return
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    state = hass.states.get("binary_sensor.dropout_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_unavailable"] is True
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flap", [("unavailable", "unknown"), ("unknown", "unavailable")]
)
@pytest.mark.parametrize(
    ("new_timeout", "clears_after"), [(60, 60), (90, 90), (45, 45), (35, 40)]
)
@pytest.mark.parametrize("via", ["edit", "reload"])
async def test_occupancy_edit_during_a_dropout_flap_keeps_the_first_deadline(
    hass: HomeAssistant,
    freezer,
    flap: tuple[str, str],
    new_timeout: int,
    clears_after: int,
    via: str,
) -> None:
    """A second invalid report is no recovery: an edit after it still counts
    the clear timeout, as edited, from the first dropout."""
    entry = _dropout_entry()
    await setup_entries(hass, entry)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    hass.states.async_set("binary_sensor.motion_1", flap[0])
    await settle(hass)
    dropout = datetime.now(UTC)
    freezer.tick(timedelta(seconds=30))
    hass.states.async_set("binary_sensor.motion_1", flap[1])
    await settle(hass)
    freezer.tick(timedelta(seconds=10))
    if via == "edit":
        await _edit_options(
            hass, entry, **{CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT: new_timeout}
        )
    else:
        assert await hass.config_entries.async_reload(entry.entry_id)
        clears_after = 60
    await settle(hass)

    state = hass.states.get("binary_sensor.dropout_occupancy")
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()
    if clears_after > 40:
        assert state.state == "on"
        freezer.move_to(dropout + timedelta(seconds=clears_after - 1))
        async_fire_time_changed(hass)
        await settle(hass)
        assert hass.states.get("binary_sensor.dropout_occupancy").state == "on"
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass)
        await settle(hass)
    state = hass.states.get("binary_sensor.dropout_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_unavailable"] is True
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()


@pytest.mark.asyncio
async def test_occupancy_new_dropout_after_a_recovery_gets_its_own_deadline(
    hass: HomeAssistant, freezer
) -> None:
    """A source that recovered and dropped out again starts a new outage,
    and an edit keeps that one's deadline."""
    entry = _dropout_entry()
    await _occupied_then_dropped(hass, entry)
    freezer.tick(timedelta(seconds=30))
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set("binary_sensor.motion_1", "unknown")
    await settle(hass)
    dropout = datetime.now(UTC)
    freezer.tick(timedelta(seconds=10))
    await _edit_options(hass, entry, **{CONF_OCCUPANCY_TIMEOUT: 40})
    await settle(hass)

    state = hass.states.get("binary_sensor.dropout_occupancy")
    assert state.state == "on"
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()
    freezer.move_to(dropout + timedelta(seconds=59))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("binary_sensor.dropout_occupancy").state == "on"
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("binary_sensor.dropout_occupancy").state == "off"


@pytest.mark.asyncio
async def test_occupancy_dropout_during_a_reload_counts_from_its_report(
    hass: HomeAssistant, freezer
) -> None:
    """An outage that began while the sensor was not loaded has no saved
    start: the source's own report dates it."""
    entry = _dropout_entry()
    await setup_entries(hass, entry)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=30))
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    dropout = datetime.now(UTC)
    freezer.tick(timedelta(seconds=5))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    state = hass.states.get("binary_sensor.dropout_occupancy")
    assert state.state == "on"
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()
    freezer.move_to(dropout + timedelta(seconds=61))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("binary_sensor.dropout_occupancy").state == "off"


@pytest.mark.asyncio
async def test_occupancy_edit_after_source_removal_keeps_the_clear_deadline(
    hass: HomeAssistant, freezer
) -> None:
    """A source with no state at all is a dropout too: an edit keeps its deadline."""
    entry = _dropout_entry()
    await setup_entries(hass, entry)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    hass.states.async_remove("binary_sensor.motion_1")
    await settle(hass)
    dropout = datetime.now(UTC)
    assert hass.states.get("binary_sensor.dropout_occupancy").state == "on"

    freezer.tick(timedelta(seconds=10))
    await _edit_options(hass, entry, **{CONF_OCCUPANCY_TIMEOUT: 40})
    await settle(hass)
    state = hass.states.get("binary_sensor.dropout_occupancy")
    assert state.state == "on"
    assert state.attributes["last_clear_unavailable"] is False
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()

    freezer.move_to(dropout + timedelta(seconds=59))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("binary_sensor.dropout_occupancy").state == "on"
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    state = hass.states.get("binary_sensor.dropout_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_unavailable"] is True
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()


@pytest.mark.asyncio
async def test_occupancy_recovery_after_a_dropout_edit_continues(
    hass: HomeAssistant, freezer
) -> None:
    """A source recovering to on after the edit cancels the resumed clear."""
    entry = _dropout_entry()
    await _occupied_then_dropped(hass, entry)
    freezer.tick(timedelta(seconds=10))
    await _edit_options(hass, entry, **{CONF_OCCUPANCY_TIMEOUT: 40})
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)

    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    state = hass.states.get("binary_sensor.dropout_occupancy")
    assert state.state == "on"
    assert state.attributes["last_clear_unavailable"] is False


@pytest.mark.asyncio
async def test_light_outlives_an_occupancy_edit_during_dropout(
    hass: HomeAssistant, freezer
) -> None:
    """A light whose timeout is under the dropout timeout is not turned off early."""
    occupancy = _dropout_entry()
    light = make_light_entry(occupancy="binary_sensor.dropout_occupancy", timeout=30)
    hass.states.async_set("light.real_1", "on")
    await _occupied_then_dropped(hass, occupancy, light)
    freezer.tick(timedelta(seconds=10))
    await _edit_options(hass, occupancy, **{CONF_OCCUPANCY_TIMEOUT: 20})
    await settle(hass)

    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.matrix_light").state == "on"
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("binary_sensor.dropout_occupancy").state == "off"
    # The countdown anchored to the dropout is already due.
    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.matrix_light").state == "off"


@pytest.mark.asyncio
async def test_recovery_to_on_preserves_false_detection_clock(
    hass: HomeAssistant, freezer
) -> None:
    """An unavailable→on recovery mid-cycle must not restart the on-duration clock.

    Otherwise a long, genuine occupancy whose source blips just before the
    clear measures its duration from the recovery moment and gets
    misclassified as a false detection (skipping the latest_occupied_time
    advance and quick-off'ing dependent lights).
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Blip Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Long, genuine occupancy...
    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=100))

    # ...with a brief source dropout near the end...
    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    # ...clearing 31s after the recovery (within timeout+grace of it).
    freezer.tick(timedelta(seconds=31))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.blip_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["false_detection_count"] == 0
    # latest_occupied_time advanced to clear - timeout, past the dropout mark.
    expected = datetime.now(UTC) - timedelta(seconds=30)
    assert state.attributes["latest_occupied_time"] == expected.isoformat()


@pytest.mark.asyncio
async def test_second_dropout_event_does_not_rearm_clear_timer(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """More unavailable/unknown events while the clear countdown is armed
    must neither restart it nor re-advance latest_occupied_time."""
    occupancy_entry.add_to_hass(hass)  # default clear-on-unavailable: 60s
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    dropout = datetime.now(UTC)
    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    await hass.async_block_till_done()

    # 30s in, the source flaps to unknown; the countdown is already armed.
    freezer.tick(timedelta(seconds=30))
    hass.states.async_set("binary_sensor.motion_1", "unknown")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "on"
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()

    # The original 60s countdown expires on schedule (30+31), not 30+60.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_unavailable"] is True
    assert state.attributes["latest_occupied_time"] == dropout.isoformat()


@pytest.mark.asyncio
async def test_combined_counts_false_cycles(hass: HomeAssistant, freezer) -> None:
    """A combined cycle made up only of false constituent cycles is flagged."""
    occupancy = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "FD Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    combined = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "FD Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.fd_occupancy"],
        },
    )
    for entry in (occupancy, combined):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.fd_combined").state == "on"

    freezer.tick(timedelta(seconds=31))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await settle(hass)

    state = hass.states.get("binary_sensor.fd_combined")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is True
    assert state.attributes["false_detection_count"] == 1


@pytest.mark.asyncio
async def test_illuminance_hysteresis_holds_state_in_band(
    hass: HomeAssistant,
) -> None:
    """Readings inside the hysteresis band never flip the state."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
            CONF_NAME: "Hyst Illuminance",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux_1",
            CONF_ILLUMINANCE_THRESHOLD: 10.0,
            CONF_ILLUMINANCE_HYSTERESIS: 2.0,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sensor = lambda: hass.states.get("binary_sensor.hyst_illuminance")  # noqa: E731

    # Establish dark with a first reading clearly below the threshold.
    hass.states.async_set("sensor.lux_1", "5")
    await hass.async_block_till_done()
    assert sensor().state == "off"

    # A reading above the threshold but inside the band stays dark.
    hass.states.async_set("sensor.lux_1", "11")
    await hass.async_block_till_done()
    assert sensor().state == "off"

    # Crossing threshold + hysteresis flips to bright.
    hass.states.async_set("sensor.lux_1", "12")
    await hass.async_block_till_done()
    assert sensor().state == "on"

    # Below the threshold but inside the band stays bright, down to its edge.
    hass.states.async_set("sensor.lux_1", "9")
    await hass.async_block_till_done()
    assert sensor().state == "on"
    hass.states.async_set("sensor.lux_1", "8")
    await hass.async_block_till_done()
    assert sensor().state == "on"

    # Dropping below threshold - hysteresis flips to dark.
    hass.states.async_set("sensor.lux_1", "7.9")
    await hass.async_block_till_done()
    assert sensor().state == "off"


@pytest.mark.asyncio
async def test_illuminance_edit_rejudges_a_held_reading_against_the_new_band(
    hass: HomeAssistant,
) -> None:
    """An edit keeps the held state for a reading still in band, else flips it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
            CONF_NAME: "Edited Illuminance",
            CONF_ILLUMINANCE_SENSOR: "sensor.lux_1",
            CONF_ILLUMINANCE_THRESHOLD: 10.0,
            CONF_ILLUMINANCE_HYSTERESIS: 5.0,
        },
    )
    hass.states.async_set("sensor.lux_1", "20")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.lux_1", "8")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.edited_illuminance").state == "on"

    # 8 is under the new bare threshold of 12 but still inside its 7-17 band.
    await _edit_options(hass, entry, **{CONF_ILLUMINANCE_THRESHOLD: 12.0})
    assert hass.states.get("binary_sensor.edited_illuminance").state == "on"

    # The narrower 11-13 band no longer holds 8.
    await _edit_options(hass, entry, **{CONF_ILLUMINANCE_HYSTERESIS: 1.0})
    assert hass.states.get("binary_sensor.edited_illuminance").state == "off"


def _lux_entry(source: str = "sensor.old_lux") -> MockConfigEntry:
    """A threshold of 10 lx with a 3 lx band: 7 to 13 holds the current state."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
            CONF_NAME: "Swapped Lux",
            CONF_ILLUMINANCE_SENSOR: source,
            CONF_ILLUMINANCE_THRESHOLD: 10.0,
            CONF_ILLUMINANCE_HYSTERESIS: 3.0,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("new_source", ["unavailable", "unknown", "dim", None])
async def test_illuminance_source_swap_drops_the_old_sources_reading(
    hass: HomeAssistant, new_source: str | None
) -> None:
    """A sensor switched to a source with no reading is unavailable, not bright.

    Holding the last value is for an outage of the same source. The new
    source's first reading is then judged on the bare threshold.
    """
    hass.states.async_set("sensor.old_lux", "100")
    if new_source is not None:
        hass.states.async_set("sensor.new_lux", new_source)
    entry = _lux_entry()
    await setup_entries(hass, entry)
    assert hass.states.get("binary_sensor.swapped_lux").state == "on"

    await _edit_options(hass, entry, **{CONF_ILLUMINANCE_SENSOR: "sensor.new_lux"})
    assert hass.states.get("binary_sensor.swapped_lux").state == "unavailable"

    # 8 lx is inside the band, where the old source's bright would have held.
    hass.states.async_set("sensor.new_lux", "8")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.swapped_lux").state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("old_reading", "new_reading", "expected"),
    [("100", "8", "off"), ("0", "12", "on"), ("100", "12", "on"), ("0", "8", "off")],
)
async def test_illuminance_source_swap_judges_the_new_source_on_its_own(
    hass: HomeAssistant, old_reading: str, new_reading: str, expected: str
) -> None:
    """The new source starts on neither side of the hysteresis band.

    Its reading gives what a sensor created for it would report.
    """
    hass.states.async_set("sensor.old_lux", old_reading)
    hass.states.async_set("sensor.new_lux", new_reading)
    entry = _lux_entry()
    await setup_entries(hass, entry)

    await _edit_options(hass, entry, **{CONF_ILLUMINANCE_SENSOR: "sensor.new_lux"})
    assert hass.states.get("binary_sensor.swapped_lux").state == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True], ids=["reload", "restart"])
async def test_illuminance_keeps_its_reading_through_an_outage_of_the_same_source(
    hass: HomeAssistant, restart: bool
) -> None:
    """A reload or restart with the same source down still holds the last value."""
    hass.states.async_set("sensor.old_lux", "100")
    entry = _lux_entry()
    await setup_entries(hass, entry)
    hass.states.async_set("sensor.old_lux", "8")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.swapped_lux").state == "on"

    hass.states.async_set("sensor.old_lux", "unavailable")
    if restart:
        await restart_entries(hass, entry)
    else:
        await _edit_options(hass, entry, **{CONF_NAME: "Swapped Lux"})
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.swapped_lux").state == "on"

    # Back inside the band, on the side it was on.
    hass.states.async_set("sensor.old_lux", "8")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.swapped_lux").state == "on"


@pytest.mark.asyncio
async def test_illuminance_swap_is_remembered_across_a_restart(
    hass: HomeAssistant,
) -> None:
    """A swap to a silent source stays unavailable after a restart too."""
    hass.states.async_set("sensor.old_lux", "100")
    entry = _lux_entry()
    await setup_entries(hass, entry)
    await _edit_options(hass, entry, **{CONF_ILLUMINANCE_SENSOR: "sensor.new_lux"})
    await restart_entries(hass, entry)
    assert hass.states.get("binary_sensor.swapped_lux").state == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("saved_source", "expected"),
    [(None, "on"), ("sensor.old_lux", "on"), ("sensor.other_lux", "unavailable")],
    ids=["no-record", "same", "other"],
)
async def test_illuminance_restore_checks_the_saved_source(
    hass: HomeAssistant, saved_source: str | None, expected: str
) -> None:
    """A save made before the source was recorded is trusted, as it was."""
    extra = {} if saved_source is None else {"source": saved_source}
    mock_restore_cache_with_extra_data(
        hass, [(State("binary_sensor.swapped_lux", "on"), extra)]
    )
    # Registered and still loading, so not gone.
    er.async_get(hass).async_get_or_create(
        "sensor", "test", "old_lux", suggested_object_id="old_lux"
    )
    await setup_entries(hass, _lux_entry())
    assert hass.states.get("binary_sensor.swapped_lux").state == expected


LUX = "sensor.lux"
LUX_GONE = ["deleted", "disabled", "unregistered", "entry_disabled"]


async def _provide_lux(hass: HomeAssistant, how: str, lux: float) -> ConfigEntry | None:
    """Add sensor.lux as a bare state, or registered from its own entry."""
    if how == "unregistered":
        hass.states.async_set(LUX, str(lux))
        return None
    return await add_real_with_entry(hass, lambda: RealLux("lux", lux))


async def _remove_lux(
    hass: HomeAssistant, how: str, source: ConfigEntry | None
) -> None:
    """Make sensor.lux leave Home Assistant for good."""
    registry = er.async_get(hass)
    if how == "deleted":
        registry.async_remove(LUX)
    elif how == "disabled":
        registry.async_update_entity(LUX, disabled_by=er.RegistryEntryDisabler.USER)
    elif how == "entry_disabled":
        await hass.config_entries.async_set_disabled_by(
            source.entry_id, ConfigEntryDisabler.USER
        )
    else:
        hass.states.async_remove(LUX)
    await settle(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("how", LUX_GONE)
@pytest.mark.parametrize("lux", [100, 8, 0])
async def test_illuminance_source_that_goes_missing_drops_its_reading(
    hass: HomeAssistant, how: str, lux: float
) -> None:
    """A deleted or disabled source can never report again: unavailable, not held.

    A source that comes back reports on the bare threshold, as a new one does.
    """
    source = await _provide_lux(hass, how, lux)
    entry = _lux_entry(LUX)
    await setup_entries(hass, entry)
    assert hass.states.get("binary_sensor.swapped_lux").state == (
        "on" if lux >= 10 else "off"
    )

    await _remove_lux(hass, how, source)
    assert hass.states.get("binary_sensor.swapped_lux").state == "unavailable"
    # Nor does a reload or restart bring the reading back.
    await restart_entries(hass, entry)
    assert hass.states.get("binary_sensor.swapped_lux").state == "unavailable"

    # 12 lx is inside the band, where a held dark would have stayed off.
    hass.states.async_set(LUX, "12")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.swapped_lux").state == "on"


@pytest.mark.asyncio
async def test_illuminance_source_whose_entry_reloads_keeps_its_reading(
    hass: HomeAssistant,
) -> None:
    """An entry reloading leaves the same placeholder for a moment: an outage."""
    source = await _provide_lux(hass, "entry_disabled", 100)
    await setup_entries(hass, _lux_entry(LUX))

    await hass.config_entries.async_unload(source.entry_id)
    await settle(hass)
    assert hass.states.get(LUX).state == "unavailable"
    assert hass.states.get("binary_sensor.swapped_lux").state == "on"
    await hass.config_entries.async_setup(source.entry_id)
    await settle(hass)
    assert hass.states.get("binary_sensor.swapped_lux").state == "on"


@pytest.mark.asyncio
async def test_illuminance_renamed_source_keeps_its_reading(
    hass: HomeAssistant,
) -> None:
    """A source whose entity ID changes is followed, not dropped."""
    lux = RealLux("lux", 100)
    await add_real(hass, lux)
    entry = _lux_entry(LUX)
    await setup_entries(hass, entry)

    await rename(hass, LUX, "sensor.hall_lux")
    assert molight_config(entry)[CONF_ILLUMINANCE_SENSOR] == "sensor.hall_lux"
    assert hass.states.get("binary_sensor.swapped_lux").state == "on"
    # Still on the bright side of the band, so the hold carried over.
    lux.set(8)
    await settle(hass)
    assert hass.states.get("binary_sensor.swapped_lux").state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize("how", LUX_GONE)
@pytest.mark.parametrize("mode", [ILLUMINANCE_MODE_CONTROL, ILLUMINANCE_MODE_GATE])
async def test_light_gated_by_a_sensor_whose_source_went_missing_comes_on(
    hass: HomeAssistant, how: str, mode: str
) -> None:
    """The light no longer reads bright from a reading that can never change."""
    source = await _provide_lux(hass, how, 500)
    hass.states.async_set("light.real_1", "off")
    hass.states.async_set("binary_sensor.occ", "off")
    await setup_entries(
        hass,
        _lux_entry(LUX),
        make_light_entry(
            occupancy="binary_sensor.occ",
            illuminance="binary_sensor.swapped_lux",
            illuminance_mode=mode,
        ),
    )
    hass.states.async_set("binary_sensor.occ", "on")
    await settle(hass)
    assert hass.states.get("light.matrix_light").state == "off"
    hass.states.async_set("binary_sensor.occ", "off")
    await settle(hass)

    await _remove_lux(hass, how, source)
    hass.states.async_set("binary_sensor.occ", "on")
    await settle(hass)
    assert hass.states.get("light.matrix_light").state == "on"


@pytest.mark.asyncio
async def test_illuminance_first_reading_in_band_uses_bare_threshold(
    hass: HomeAssistant,
) -> None:
    """A first-ever reading has no held state, so the band must not apply."""
    for name, source, reading, expected in (
        ("Band Bright", "sensor.lux_a", "11", "on"),
        ("Band Dark", "sensor.lux_b", "9", "off"),
        ("Band Edge", "sensor.lux_c", "10", "on"),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
                CONF_NAME: name,
                CONF_ILLUMINANCE_SENSOR: source,
                CONF_ILLUMINANCE_THRESHOLD: 10.0,
                CONF_ILLUMINANCE_HYSTERESIS: 2.0,
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        hass.states.async_set(source, reading)
        await hass.async_block_till_done()
        entity_id = f"binary_sensor.{name.lower().replace(' ', '_')}"
        assert hass.states.get(entity_id).state == expected


@pytest.mark.asyncio
async def test_combined_trigger_maintain_and_lot(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """Trigger starts, maintain sustains but never starts, lot is the max."""
    for entry in (occupancy_entry, _occupancy2_entry(), _combined_entry()):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Maintain sensor alone must not start occupancy.
    hass.states.async_set("binary_sensor.motion_2", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.combined_occupancy").state == "off"

    # Trigger sensor starts it.
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.combined_occupancy").state == "on"

    # Trigger clears: the maintain sensor keeps occupancy alive.
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await settle(hass)
    assert hass.states.get("binary_sensor.combined_occupancy").state == "on"

    # Maintain clears: occupancy ends; latest_occupied_time is the max of
    # the constituents': trigger cleared at T+10 with timeout 30 (→ T-20),
    # maintain at T+20 with timeout 45 (→ T-25). The trigger's wins.
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set("binary_sensor.motion_2", "off")
    await settle(hass)

    combined = hass.states.get("binary_sensor.combined_occupancy")
    assert combined.state == "off"
    trigger_lot = hass.states.get("binary_sensor.test_occupancy").attributes[
        "latest_occupied_time"
    ]
    assert combined.attributes["latest_occupied_time"] == trigger_lot


def _raw_combined_entry() -> MockConfigEntry:
    """Combined sensor over raw entity ids (no virtual constituents needed)."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Seed Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.m1"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.m2"],
        },
    )


@pytest.mark.asyncio
async def test_combined_seeds_on_from_trigger_at_startup(hass: HomeAssistant) -> None:
    """A trigger sensor already on at startup seeds the combined sensor on."""
    hass.states.async_set("binary_sensor.m1", "on")
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.seed_combined").state == "on"


@pytest.mark.asyncio
async def test_combined_seeds_on_from_restored_state_and_maintain(
    hass: HomeAssistant,
) -> None:
    """A restored 'on' plus a maintain sensor still showing presence seeds on."""
    mock_restore_cache(hass, [State("binary_sensor.seed_combined", "on")])
    hass.states.async_set("binary_sensor.m2", "on")
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.seed_combined").state == "on"


@pytest.mark.asyncio
async def test_combined_restored_on_waits_for_a_late_maintain_constituent(
    hass: HomeAssistant,
) -> None:
    """A maintain constituent still loading at seed time decides on its first report.

    Constituents are separate config entries that set up concurrently, so the
    restored "on" must not be lost just because the maintain sensor was still
    HA's restored placeholder when the combined sensor seeded.
    """
    mock_restore_cache(hass, [State("binary_sensor.seed_combined", "on")])
    hass.states.async_set("binary_sensor.m2", "unavailable", {"restored": True})
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"

    hass.states.async_set("binary_sensor.m2", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "on"

    # Held by the maintain sensor like any carried-over occupancy.
    hass.states.async_set("binary_sensor.m2", "off")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"

    # The restored evidence is spent: a later maintain on cannot start.
    hass.states.async_set("binary_sensor.m2", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"


@pytest.mark.asyncio
async def test_combined_restored_on_carried_by_maintain_during_startup(
    hass: HomeAssistant,
) -> None:
    """A maintain sensor that seeds off (its own source still loading) and turns
    on before startup finishes carries a restored "on"; after startup it cannot."""
    mock_restore_cache(hass, [State("binary_sensor.seed_combined", "on")])
    hass.states.async_set("binary_sensor.m2", "off")
    hass.set_state(CoreState.starting)
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"

    hass.states.async_set("binary_sensor.m2", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "on"

    hass.states.async_set("binary_sensor.m2", "off")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"


@pytest.mark.asyncio
async def test_combined_restored_on_not_carried_by_maintain_after_startup(
    hass: HomeAssistant,
) -> None:
    """Once HA has started, a reported maintain sensor turning on cannot start."""
    mock_restore_cache(hass, [State("binary_sensor.seed_combined", "on")])
    hass.states.async_set("binary_sensor.m2", "off")
    hass.set_state(CoreState.starting)
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.m2", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"


class _LateRoom:
    """A combined sensor whose maintain sensor's source has its own entry."""

    combined = "binary_sensor.room"

    def __init__(self) -> None:
        self.presence_on = False
        self.presence: RealBinary | None = None
        self.motion = RealBinary("room_motion")
        self.hardware: ConfigEntry | None = None
        self.entries: list[MockConfigEntry] = []

    def _make_presence(self) -> RealBinary:
        self.presence = RealBinary("room_presence", on=self.presence_on)
        return self.presence

    async def setup(
        self,
        hass: HomeAssistant,
        *,
        maintain_first: bool = True,
        nested: bool = False,
    ) -> None:
        await add_real(hass, self.motion)
        self.hardware = await add_real_with_entry(hass, self._make_presence)

        def occupancy(name: str, source: str) -> MockConfigEntry:
            return MockConfigEntry(
                domain=DOMAIN,
                data={
                    CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
                    CONF_NAME: name,
                    CONF_OCCUPANCY_SENSOR: source,
                    CONF_OCCUPANCY_TIMEOUT: 1,
                    CONF_FALSE_DETECTION_GRACE: 0,
                },
            )

        hold = occupancy("Room Hold", "binary_sensor.room_presence")
        inner = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
                CONF_NAME: "Room Inner",
                CONF_TRIGGER_SENSORS: ["binary_sensor.room_hold"],
            },
        )
        maintain = "binary_sensor.room_inner" if nested else "binary_sensor.room_hold"
        room = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
                CONF_NAME: "Room",
                CONF_TRIGGER_SENSORS: ["binary_sensor.room_trigger"],
                CONF_MAINTAIN_SENSORS: [maintain],
            },
        )
        held = [hold, *([inner] if nested else [])]
        self.entries = [
            occupancy("Room Trigger", self.motion.entity_id),
            *([*held, room] if maintain_first else [room, *held]),
        ]
        await setup_entries(hass, *self.entries)

    async def visit(self, hass: HomeAssistant, freezer) -> None:
        """Trigger a visit that the maintain sensor then holds alone."""
        self.motion.set(True)
        self.presence.set(True)
        self.presence_on = True
        await settle(hass)
        freezer.tick(timedelta(seconds=30))
        self.motion.set(False)
        await settle(hass)
        assert hass.states.get(self.combined).state == "on"

    async def load_presence(self, hass: HomeAssistant, on: bool) -> None:
        self.presence_on = on
        assert await hass.config_entries.async_setup(self.hardware.entry_id)
        await settle(hass)

    async def replace_presence(self, hass: HomeAssistant, freezer, on: bool) -> None:
        """Disable the source's integration and bring it back reporting anew."""
        await hass.config_entries.async_set_disabled_by(
            self.hardware.entry_id, ConfigEntryDisabler.USER
        )
        await settle(hass)
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await settle(hass)
        self.presence_on = on
        await hass.config_entries.async_set_disabled_by(self.hardware.entry_id, None)
        await settle(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("maintain_first", [True, False])
@pytest.mark.parametrize("restarts", [1, 2])
async def test_combined_restored_on_carried_by_a_late_loading_maintain_source(
    hass: HomeAssistant, freezer, maintain_first: bool, restarts: int
) -> None:
    """After startup, a maintain sensor whose own source loads late reports a
    start it did not witness (last_on_time None) and still carries, also when
    HA restarted again while it waited."""
    room = _LateRoom()
    await room.setup(hass, maintain_first=maintain_first)
    await room.visit(hass, freezer)
    assert await hass.config_entries.async_unload(room.hardware.entry_id)
    await settle(hass)
    for _ in range(restarts):
        await restart_entries(hass, *room.entries, started=False)
        await finish_startup(hass)
        assert hass.states.get(room.combined).state == "off"
        freezer.tick(timedelta(seconds=30))

    await room.load_presence(hass, on=True)
    hold = hass.states.get("binary_sensor.room_hold")
    assert hold.state == "on"
    assert hold.attributes["last_on_time"] is None
    assert hass.states.get(room.combined).state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize("maintain_first", [True, False])
async def test_combined_restored_on_carried_through_a_nested_maintain_sensor(
    hass: HomeAssistant, freezer, maintain_first: bool
) -> None:
    """A combined maintain sensor passes on that its visit is undated, so the
    sensor above it carries the restored visit too."""
    room = _LateRoom()
    await room.setup(hass, maintain_first=maintain_first, nested=True)
    await room.visit(hass, freezer)
    assert await hass.config_entries.async_unload(room.hardware.entry_id)
    await settle(hass)
    await restart_entries(hass, *room.entries)
    assert hass.states.get(room.combined).state == "off"
    freezer.tick(timedelta(seconds=30))

    await room.load_presence(hass, on=True)
    inner = hass.states.get("binary_sensor.room_inner")
    assert inner.state == "on"
    assert inner.attributes["last_on_time"] is None
    assert hass.states.get(room.combined).state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attributes", "dated"),
    [
        ({"last_on_time": "2026-07-02T21:00:00+00:00"}, "2026-07-02T21:00:00+00:00"),
        ({"last_on_time": None}, None),
        ({}, "now"),
    ],
    ids=["dated", "undated", "no-stamp"],
)
@pytest.mark.parametrize("nested", [False, True])
async def test_combined_dates_a_visit_as_its_trigger_does(
    hass: HomeAssistant, freezer, attributes: dict, dated: str | None, nested: bool
) -> None:
    """last_on_time is the trigger's own, through any depth: unknown when the
    trigger could not date the visit, now for a sensor that keeps no stamp.
    It survives a reload of the combined sensor."""
    hass.states.async_set("binary_sensor.m1", "off")
    hass.states.async_set("binary_sensor.m2", "off")
    inner = _raw_combined_entry()
    outer = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Outer",
            CONF_TRIGGER_SENSORS: ["binary_sensor.seed_combined"],
        },
    )
    await setup_entries(hass, inner, outer)
    entity_id = "binary_sensor.outer" if nested else "binary_sensor.seed_combined"
    assert hass.states.get(entity_id).attributes["last_on_time"] is None

    freezer.tick(timedelta(seconds=30))
    expected = datetime.now(UTC).isoformat() if dated == "now" else dated
    hass.states.async_set("binary_sensor.m1", "on", attributes)
    await settle(hass)
    assert hass.states.get(entity_id).attributes["last_on_time"] == expected

    freezer.tick(timedelta(seconds=30))
    assert await hass.config_entries.async_reload(outer.entry_id)
    assert await hass.config_entries.async_reload(inner.entry_id)
    await settle(hass)
    assert hass.states.get(entity_id).state == "on"
    assert hass.states.get(entity_id).attributes["last_on_time"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("maintain_first", [True, False])
async def test_combined_late_maintain_source_loading_off_ends_the_wait(
    hass: HomeAssistant, freezer, maintain_first: bool
) -> None:
    """A late source that loads clear settles the restored visit as over:
    neither its next detection nor a later undated return can start one."""
    room = _LateRoom()
    await room.setup(hass, maintain_first=maintain_first)
    await room.visit(hass, freezer)
    assert await hass.config_entries.async_unload(room.hardware.entry_id)
    await settle(hass)
    await restart_entries(hass, *room.entries)

    await room.load_presence(hass, on=False)
    room.presence.set(True)
    await settle(hass)
    assert hass.states.get("binary_sensor.room_hold").state == "on"
    assert hass.states.get(room.combined).state == "off"
    room.presence.set(False)
    await settle(hass)

    await room.replace_presence(hass, freezer, on=True)
    hold = hass.states.get("binary_sensor.room_hold")
    assert hold.state == "on"
    assert hold.attributes["last_on_time"] is None
    assert hass.states.get(room.combined).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("ended_during", ["reload", "restart"])
async def test_combined_visit_that_ended_cannot_be_carried_later(
    hass: HomeAssistant, freezer, ended_during: str
) -> None:
    """A restored visit every constituent reported over is finished: a
    maintain source coming back much later, undated, does not revive it."""
    room = _LateRoom()
    await room.setup(hass)
    await room.visit(hass, freezer)
    room_entry = room.entries[-1]
    if ended_during == "reload":
        assert await hass.config_entries.async_unload(room_entry.entry_id)
        room.presence.set(False)
        await settle(hass)
        assert await hass.config_entries.async_setup(room_entry.entry_id)
    else:
        await restore_state.async_get(hass).async_dump_states()
        room.presence.set(False)
        await settle(hass)
        await crash_entries(hass, *room.entries)
    await settle(hass)
    assert hass.states.get(room.combined).state == "off"
    freezer.tick(timedelta(seconds=90))

    await room.replace_presence(hass, freezer, on=True)
    hold = hass.states.get("binary_sensor.room_hold")
    assert hold.state == "on"
    assert hold.attributes["last_on_time"] is None
    assert hass.states.get(room.combined).state == "off"


@pytest.mark.asyncio
async def test_combined_late_maintain_reporting_off_does_not_carry(
    hass: HomeAssistant,
) -> None:
    """A late constituent whose first report is off ends the restored occupancy."""
    mock_restore_cache(hass, [State("binary_sensor.seed_combined", "on")])
    hass.states.async_set("binary_sensor.m2", "unavailable", {"restored": True})
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.m2", "off")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"
    hass.states.async_set("binary_sensor.m2", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"


@pytest.mark.asyncio
async def test_combined_late_maintain_without_restored_on_cannot_start(
    hass: HomeAssistant,
) -> None:
    """Without restored evidence a late maintain sensor still cannot start."""
    hass.states.async_set("binary_sensor.m2", "unavailable", {"restored": True})
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.m2", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"


@pytest.mark.asyncio
async def test_combined_maintain_alone_never_seeds_occupancy(
    hass: HomeAssistant,
) -> None:
    """Without restored evidence, a maintain sensor cannot start occupancy."""
    hass.states.async_set("binary_sensor.m2", "on")
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.seed_combined").state == "off"


@pytest.mark.asyncio
async def test_combined_midrun_reload_does_not_seed_from_maintain(
    hass: HomeAssistant,
) -> None:
    """An options reload must not let a maintain sensor start occupancy."""
    hass.states.async_set("binary_sensor.m2", "on")
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"


def _combined_lot(hass: HomeAssistant) -> str | None:
    return hass.states.get("binary_sensor.seed_combined").attributes[
        "latest_occupied_time"
    ]


async def _false_cycle(hass: HomeAssistant, freezer, entity_id: str, attrs: dict):
    """Run one single-blip cycle of a constituent, which leaves its attributes."""
    hass.states.async_set(entity_id, "on", attrs)
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"
    freezer.tick(timedelta(seconds=31))
    hass.states.async_set(entity_id, "off", attrs)
    await settle(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("seeded_on", [False, True])
async def test_new_combined_sensor_measures_its_first_cycle_against_history(
    hass: HomeAssistant, freezer, seeded_on: bool
) -> None:
    """A visit a constituent saw before the sensor existed is not a detection."""
    old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    attrs = {"latest_occupied_time": old, "last_clear_false_detection": True}
    hass.states.async_set("binary_sensor.m1", "on" if seeded_on else "off", attrs)
    hass.states.async_set("binary_sensor.m2", "off")
    await setup_entries(hass, _raw_combined_entry())
    assert _combined_lot(hass) == old

    if seeded_on:
        freezer.tick(timedelta(seconds=31))
        hass.states.async_set("binary_sensor.m1", "off", attrs)
        await settle(hass)
    else:
        await _false_cycle(hass, freezer, "binary_sensor.m1", attrs)
    state = hass.states.get("binary_sensor.seed_combined")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is True
    assert state.attributes["false_detection_count"] == 1


@pytest.mark.asyncio
async def test_combined_reload_takes_over_a_new_constituents_history(
    hass: HomeAssistant, freezer
) -> None:
    """A constituent added by an options edit brings its history along."""
    hass.states.async_set("binary_sensor.m1", "off")
    hass.states.async_set("binary_sensor.m2", "off")
    entry = _raw_combined_entry()
    await setup_entries(hass, entry)

    recent = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    attrs = {"latest_occupied_time": recent, "last_clear_false_detection": True}
    hass.states.async_set("binary_sensor.m3", "off", attrs)
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_NAME: "Seed Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.m1", "binary_sensor.m3"],
            CONF_MAINTAIN_SENSORS: ["binary_sensor.m2"],
        },
    )
    await settle(hass)
    assert _combined_lot(hass) == recent

    await _false_cycle(hass, freezer, "binary_sensor.m3", attrs)
    state = hass.states.get("binary_sensor.seed_combined")
    assert state.attributes["last_clear_false_detection"] is True
    assert state.attributes["false_detection_count"] == 1


async def _genuine_trigger_then_maintain_hold(hass: HomeAssistant, freezer) -> str:
    """Start a combined cycle whose trigger clears genuinely; return its lot."""
    hass.states.async_set("binary_sensor.m1", "on")
    hass.states.async_set("binary_sensor.m2", "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=60))
    lot = datetime.now(UTC).isoformat()
    hass.states.async_set("binary_sensor.m1", "off", {"latest_occupied_time": lot})
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"
    return lot


async def _false_maintain_clear(hass: HomeAssistant, lot: str) -> None:
    """The maintain sensor's false clear adds no newer detection than lot."""
    attrs = {"last_clear_false_detection": True}
    hass.states.async_set("binary_sensor.m2", "off", attrs)
    await settle(hass)
    state = hass.states.get("binary_sensor.seed_combined")
    assert state.state == "off"
    assert state.attributes["latest_occupied_time"] == lot
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["false_detection_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["reload", "restart"])
async def test_genuine_combined_cycle_survives_a_reload(
    hass: HomeAssistant, freezer, via: str
) -> None:
    """A reload mid-cycle keeps the genuine detection the cycle already had."""
    hass.states.async_set("binary_sensor.m1", "off")
    hass.states.async_set("binary_sensor.m2", "off")
    entry = _raw_combined_entry()
    await setup_entries(hass, entry)
    lot = await _genuine_trigger_then_maintain_hold(hass, freezer)

    if via == "reload":
        assert await hass.config_entries.async_reload(entry.entry_id)
    else:
        await restart_entries(hass, entry)
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"
    await _false_maintain_clear(hass, lot)


@pytest.mark.asyncio
async def test_genuine_combined_cycle_survives_a_reload_with_a_late_maintain(
    hass: HomeAssistant, freezer
) -> None:
    """A cycle a late maintain sensor carries across keeps its detection too."""
    hass.states.async_set("binary_sensor.m1", "off")
    hass.states.async_set("binary_sensor.m2", "off")
    entry = _raw_combined_entry()
    await setup_entries(hass, entry)
    lot = await _genuine_trigger_then_maintain_hold(hass, freezer)

    # The maintain sensor's own entry reloads, then the combined one does.
    hass.states.async_set("binary_sensor.m2", "unavailable", {"restored": True})
    await settle(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "off"
    hass.states.async_set("binary_sensor.m2", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"
    await _false_maintain_clear(hass, lot)


@pytest.mark.asyncio
@pytest.mark.parametrize("again", ["stop_during_startup", "crash"])
async def test_combined_carry_survives_a_second_restart(
    hass: HomeAssistant, freezer, again: str
) -> None:
    """An "on" still waiting for its maintain sensor is saved as waiting: HA
    restarting again before the sensor loads can still carry it across."""
    combined = "binary_sensor.seed_combined"
    hass.states.async_set("binary_sensor.m1", "off")
    hass.states.async_set("binary_sensor.m2", "off")
    entry = _raw_combined_entry()
    await setup_entries(hass, entry)
    lot = await _genuine_trigger_then_maintain_hold(hass, freezer)

    # The maintain sensor has not loaded yet when the combined one seeds.
    hass.states.async_set("binary_sensor.m2", "unavailable", {"restored": True})
    await settle(hass)
    await restart_entries(hass, entry, started=False)
    assert hass.states.get(combined).state == "off"  # waiting to carry

    restart = crash_entries if again == "crash" else restart_entries
    await restart(hass, entry, started=False)
    assert hass.states.get(combined).state == "off"
    hass.states.async_set("binary_sensor.m2", "on")
    await settle(hass)
    assert hass.states.get(combined).state == "on"
    await finish_startup(hass)
    await _false_maintain_clear(hass, lot)


@pytest.mark.asyncio
async def test_combined_carry_is_not_saved_once_startup_has_settled(
    hass: HomeAssistant, freezer
) -> None:
    """A wait that ended unanswered is over: at a later restart a maintain
    sensor cannot start occupancy from it."""
    combined = "binary_sensor.seed_combined"
    hass.states.async_set("binary_sensor.m1", "off")
    hass.states.async_set("binary_sensor.m2", "off")
    entry = _raw_combined_entry()
    await setup_entries(hass, entry)
    await _genuine_trigger_then_maintain_hold(hass, freezer)

    await restart_entries(hass, entry, started=False)
    hass.states.async_set("binary_sensor.m2", "off")  # the room emptied while down
    await finish_startup(hass)
    await settle(hass)
    assert hass.states.get(combined).state == "off"

    await restart_entries(hass, entry, started=False)
    hass.states.async_set("binary_sensor.m2", "on")
    await settle(hass)
    await finish_startup(hass)
    assert hass.states.get(combined).state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("via", "kept"),
    [("reload", True), ("restart", True), ("added", False), ("roles", False)],
)
async def test_combined_false_clear_survives_a_reload(
    hass: HomeAssistant, freezer, via: str, kept: bool
) -> None:
    """A combined clear's classification stands across a reload or restart;
    editing the constituents leaves it behind."""
    hass.states.async_set("binary_sensor.m1", "off")
    hass.states.async_set("binary_sensor.m2", "off")
    hass.states.async_set("binary_sensor.m3", "off")
    entry = _raw_combined_entry()
    await setup_entries(hass, entry)
    await _false_cycle(hass, freezer, "binary_sensor.m1", {})
    state = hass.states.get("binary_sensor.seed_combined")
    assert state.attributes["last_clear_false_detection"] is True

    if via == "reload":
        assert await hass.config_entries.async_reload(entry.entry_id)
    elif via == "restart":
        await restart_entries(hass, entry)
    else:
        triggers = ["binary_sensor.m1", "binary_sensor.m3"]
        maintain = ["binary_sensor.m2"]
        if via == "roles":
            triggers, maintain = maintain, ["binary_sensor.m1"]
        hass.config_entries.async_update_entry(
            entry,
            options={
                CONF_NAME: "Seed Combined",
                CONF_TRIGGER_SENSORS: triggers,
                CONF_MAINTAIN_SENSORS: maintain,
            },
        )
    await settle(hass)
    state = hass.states.get("binary_sensor.seed_combined")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is kept
    assert state.attributes["false_detection_count"] == 1


@pytest.mark.asyncio
async def test_combined_clears_when_last_constituent_drops_out(
    hass: HomeAssistant, freezer
) -> None:
    """A constituent going unavailable must not hold the combined sensor on."""
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"

    hass.states.async_set("binary_sensor.m1", "unavailable")
    await settle(hass)
    combined = hass.states.get("binary_sensor.seed_combined")
    assert combined.state == "off"
    assert combined.attributes["last_clear_false_detection"] is False
    assert combined.attributes["false_detection_count"] == 0
    # The person is assumed present up to the dropout: the lot advances so
    # dependent lights run their normal countdown instead of snapping off.
    assert combined.attributes["latest_occupied_time"] == datetime.now(UTC).isoformat()


@pytest.mark.asyncio
async def test_combined_dropout_advances_lot_for_nesting_parent(
    hass: HomeAssistant,
) -> None:
    """A dropout-clear's advanced lot keeps a nesting parent from calling it false."""
    inner = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Inner Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.m1"],
        },
    )
    outer = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Outer Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.inner_combined"],
        },
    )
    for entry in (inner, outer):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.outer_combined").state == "on"

    hass.states.async_set("binary_sensor.m1", "unavailable")
    await settle(hass)
    outer_state = hass.states.get("binary_sensor.outer_combined")
    assert outer_state.state == "off"
    assert outer_state.attributes["last_clear_false_detection"] is False
    assert outer_state.attributes["false_detection_count"] == 0


@pytest.mark.asyncio
async def test_combined_clears_when_last_constituent_is_removed(
    hass: HomeAssistant,
) -> None:
    """A constituent removed from the state machine releases the combined sensor."""
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"

    hass.states.async_remove("binary_sensor.m1")
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "off"


@pytest.mark.asyncio
async def test_combined_holds_through_dropout_while_maintain_on(
    hass: HomeAssistant,
) -> None:
    """A dropped trigger doesn't clear occupancy a maintain sensor still sees."""
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.m1", "on")
    hass.states.async_set("binary_sensor.m2", "on")
    await settle(hass)
    hass.states.async_set("binary_sensor.m1", "unavailable")
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"

    # The surviving maintain sensor clearing still ends occupancy normally.
    hass.states.async_set("binary_sensor.m2", "off")
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "off"


def _room_states(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Record every state the entity takes from now on."""
    seen: list[str] = []

    @callback
    def _record(event) -> None:
        if event.data["entity_id"] == entity_id:
            seen.append(event.data["new_state"].state)

    hass.bus.async_listen(EVENT_STATE_CHANGED, _record)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", ["trigger", "maintain"])
async def test_combined_holds_through_constituent_reload(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, holder: str
) -> None:
    """Reloading the constituent that holds occupancy leaves the room occupied."""
    hass.states.async_set("binary_sensor.motion_1", "off")
    hass.states.async_set("binary_sensor.motion_2", "off")
    maintain = _occupancy2_entry()
    for entry in (occupancy_entry, maintain, _combined_entry()):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.motion_2", "on")
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    hass.states.async_set(
        "binary_sensor.motion_1" if holder == "maintain" else "binary_sensor.motion_2",
        "off",
    )
    await settle(hass)
    before = hass.states.get("binary_sensor.combined_occupancy")
    assert before.state == "on"

    seen = _room_states(hass, "binary_sensor.combined_occupancy")
    reloaded = maintain if holder == "maintain" else occupancy_entry
    assert await hass.config_entries.async_reload(reloaded.entry_id)
    await settle(hass)

    after = hass.states.get("binary_sensor.combined_occupancy")
    assert after.state == "on"
    assert "off" not in seen
    assert after.attributes == before.attributes

    # The grace left no timer behind that could clear the room later.
    async_fire_time_changed(hass, datetime.now(UTC) + timedelta(seconds=11))
    await settle(hass)
    assert hass.states.get("binary_sensor.combined_occupancy").state == "on"


async def _unloaded_constituent(hass: HomeAssistant, entity_id: str) -> None:
    """Leave the placeholder an unloaded entry writes for its entity."""
    hass.states.async_set(entity_id, "unavailable", {"restored": True})
    await settle(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_id", ["binary_sensor.m1", "binary_sensor.m2"])
async def test_combined_clears_when_unloaded_constituent_stays_away(
    hass: HomeAssistant, freezer, entity_id: str
) -> None:
    """An entry that is unloaded for good holds occupancy only for the grace."""
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.m2", "on")
    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)
    other = (
        "binary_sensor.m2" if entity_id == "binary_sensor.m1" else "binary_sensor.m1"
    )
    hass.states.async_set(other, "off")
    await settle(hass)

    dropout = datetime.now(UTC)
    await _unloaded_constituent(hass, entity_id)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"

    freezer.tick(timedelta(seconds=9))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await settle(hass)
    combined = hass.states.get("binary_sensor.seed_combined")
    assert combined.state == "off"
    assert combined.attributes["last_clear_false_detection"] is False
    assert combined.attributes["latest_occupied_time"] == dropout.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("after", ["off", "unavailable", "removed"])
async def test_combined_reload_grace_ends_when_constituent_reports_gone(
    hass: HomeAssistant, after: str
) -> None:
    """A clear, a real outage or a removal after the unload clears at once."""
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)

    await _unloaded_constituent(hass, "binary_sensor.m1")
    assert hass.states.get("binary_sensor.seed_combined").state == "on"

    if after == "removed":
        hass.states.async_remove("binary_sensor.m1")
    else:
        hass.states.async_set("binary_sensor.m1", after)
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "off"

    # The cancelled grace does not fire later.
    seen = _room_states(hass, "binary_sensor.seed_combined")
    async_fire_time_changed(hass, datetime.now(UTC) + timedelta(seconds=11))
    await settle(hass)
    assert seen == []


@pytest.mark.asyncio
async def test_combined_reload_grace_survives_another_constituent_clearing(
    hass: HomeAssistant,
) -> None:
    """A reloading maintain sensor still holds when the trigger clears."""
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.m2", "on")
    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)

    await _unloaded_constituent(hass, "binary_sensor.m2")
    hass.states.async_set("binary_sensor.m1", "off")
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"

    hass.states.async_set("binary_sensor.m2", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "on"


@pytest.mark.asyncio
async def test_combined_unloaded_idle_constituent_gets_no_grace(
    hass: HomeAssistant,
) -> None:
    """Only a constituent that was on is held: an idle one cannot keep the room."""
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.m2", "off")
    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)

    await _unloaded_constituent(hass, "binary_sensor.m2")
    hass.states.async_set("binary_sensor.m1", "off")
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "off"


@pytest.mark.asyncio
async def test_combined_clears_at_once_when_holding_constituent_is_removed(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """Removing the constituent's entry is a dropout, not a reload to wait for.

    The removal also strips the constituent from the combined entry, which
    reloads it before any grace could end, so the clear must land first.
    """
    hass.states.async_set("binary_sensor.motion_1", "off")
    hass.states.async_set("binary_sensor.motion_2", "off")
    combined = _combined_entry()
    for entry in (occupancy_entry, _occupancy2_entry(), combined):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    before = hass.states.get("binary_sensor.combined_occupancy")
    assert before.state == "on"

    dropout = datetime.now(UTC)
    await hass.config_entries.async_remove(occupancy_entry.entry_id)
    await settle(hass)

    after = hass.states.get("binary_sensor.combined_occupancy")
    assert after.state == "off"
    assert after.attributes["last_clear_false_detection"] is False
    assert after.attributes["latest_occupied_time"] == dropout.isoformat()
    assert molight_config(combined)[CONF_TRIGGER_SENSORS] == []


@pytest.mark.asyncio
async def test_combined_unload_during_reload_grace_drops_the_timer(
    hass: HomeAssistant,
) -> None:
    """Unloading the combined sensor mid-grace leaves no timer to fire."""
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)
    await _unloaded_constituent(hass, "binary_sensor.m1")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, datetime.now(UTC) + timedelta(seconds=11))
    await settle(hass)
    assert hass.states.get("binary_sensor.seed_combined").state == "unavailable"


@pytest.mark.asyncio
async def test_combined_keeps_newest_lot_over_older_clear(
    hass: HomeAssistant, freezer
) -> None:
    """A constituent clearing with an older latest_occupied_time must not
    regress the combined sensor's own, and a cycle that advanced nothing is
    flagged as a false detection."""
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    newer = datetime.now(UTC).isoformat()
    older = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()

    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)
    hass.states.async_set("binary_sensor.m1", "off", {"latest_occupied_time": newer})
    await settle(hass)
    combined = hass.states.get("binary_sensor.seed_combined")
    assert combined.attributes["latest_occupied_time"] == newer
    assert combined.attributes["last_clear_false_detection"] is False

    # A second cycle whose clear carries only an older lot advances nothing.
    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)
    hass.states.async_set("binary_sensor.m1", "off", {"latest_occupied_time": older})
    await settle(hass)
    combined = hass.states.get("binary_sensor.seed_combined")
    assert combined.attributes["latest_occupied_time"] == newer
    assert combined.attributes["last_clear_false_detection"] is True
    assert combined.attributes["false_detection_count"] == 1


@pytest.mark.asyncio
async def test_combined_retriggers_on_constituent_recovery(
    hass: HomeAssistant,
) -> None:
    """A constituent recovering straight to on is a real trigger event."""
    entry = _raw_combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.m1", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "on"

    hass.states.async_set("binary_sensor.m1", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "off"

    hass.states.async_set("binary_sensor.m1", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.seed_combined").state == "on"


@pytest.mark.asyncio
async def test_occupancy_discards_last_on_time_restored_with_off(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """An anchor restored alongside 'off' belongs to a finished cycle.

    Keeping it would let a fresh post-restart cycle measure its on-duration
    from the previous cycle's start, skewing classification.
    """
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    mock_restore_cache(
        hass,
        [State("binary_sensor.test_occupancy", "off", {"last_on_time": stale})],
    )
    hass.states.async_set("binary_sensor.motion_1", "on")
    hass.set_state(CoreState.starting)

    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "on"
    assert state.attributes["last_on_time"] is None


def _grace_occupancy_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Boot Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )


@pytest.mark.asyncio
async def test_occupancy_restores_last_on_time_with_on(
    hass: HomeAssistant, freezer
) -> None:
    """A cycle still running at restart keeps its pre-restart anchor.

    The post-restart clear then measures the full on-duration: a long-running
    occupancy is a real one, not a false detection timed from the restart.
    """
    anchor = (datetime.now(UTC) - timedelta(seconds=100)).isoformat()
    mock_restore_cache(
        hass,
        [State("binary_sensor.boot_occupancy", "on", {"last_on_time": anchor})],
    )
    hass.set_state(CoreState.starting)
    hass.states.async_set("binary_sensor.motion_1", "on")

    entry = _grace_occupancy_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "on"
    assert state.attributes["last_on_time"] == anchor

    freezer.tick(timedelta(seconds=2))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["latest_occupied_time"] is not None


@pytest.mark.asyncio
async def test_occupancy_classifies_short_cycle_from_restored_anchor(
    hass: HomeAssistant, freezer
) -> None:
    """A brief cycle spanning the restart is still classified as false.

    Without the restored anchor the cycle would be unclassifiable and the
    clear would pass as genuine.
    """
    anchor = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    mock_restore_cache(
        hass,
        [State("binary_sensor.boot_occupancy", "on", {"last_on_time": anchor})],
    )
    hass.set_state(CoreState.starting)
    hass.states.async_set("binary_sensor.motion_1", "on")

    entry = _grace_occupancy_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=2))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is True
    assert state.attributes["false_detection_count"] == 1
    assert state.attributes["latest_occupied_time"] is None


@pytest.mark.asyncio
async def test_occupancy_discards_malformed_restored_last_on_time(
    hass: HomeAssistant,
) -> None:
    """A stored anchor that no longer parses is dropped, not a crash."""
    mock_restore_cache(
        hass,
        [
            State(
                "binary_sensor.boot_occupancy",
                "on",
                {"last_on_time": "not-a-timestamp"},
            )
        ],
    )
    hass.set_state(CoreState.starting)
    hass.states.async_set("binary_sensor.motion_1", "on")

    entry = _grace_occupancy_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "on"
    assert state.attributes["last_on_time"] is None


@pytest.mark.asyncio
async def test_occupancy_seeds_last_on_time_from_source(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry, freezer
) -> None:
    """A mid-run reload without a restored on-time uses the source's last_changed."""
    hass.states.async_set("binary_sensor.motion_1", "on")
    freezer.tick(timedelta(seconds=10))

    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "on"
    source_changed = hass.states.get("binary_sensor.motion_1").last_changed
    assert state.attributes["last_on_time"] == source_changed.isoformat()


@pytest.mark.asyncio
async def test_occupancy_does_not_seed_last_on_time_at_startup(
    hass: HomeAssistant, freezer
) -> None:
    """At startup last_changed is the restart moment, so the cycle isn't classified."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Boot Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    hass.set_state(CoreState.starting)
    hass.states.async_set("binary_sensor.motion_1", "on")

    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "on"
    assert state.attributes["last_on_time"] is None

    # Motion that began before the restart clears moments later: not a false
    # detection, so the countdown gets a real latest_occupied_time.
    freezer.tick(timedelta(seconds=2))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["false_detection_count"] == 0
    assert state.attributes["latest_occupied_time"] is not None


@pytest.mark.asyncio
async def test_occupancy_does_not_stamp_on_time_for_late_loading_source(
    hass: HomeAssistant, freezer
) -> None:
    """A source first appearing 'on' during startup gets no on-time anchor.

    A source entity that loads after the MoLight entry (a retained MQTT
    state, a slow coordinator) delivers its pre-restart 'on' as an event;
    stamping the startup moment would misclassify the first clear.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Late Occupancy",
            CONF_OCCUPANCY_SENSOR: "binary_sensor.motion_1",
            CONF_OCCUPANCY_TIMEOUT: 30,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )
    hass.set_state(CoreState.starting)

    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # The source appears for the first time, already on, mid-startup.
    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.late_occupancy")
    assert state.state == "on"
    assert state.attributes["last_on_time"] is None

    freezer.tick(timedelta(seconds=2))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.late_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["false_detection_count"] == 0
    assert state.attributes["latest_occupied_time"] is not None


@pytest.mark.asyncio
async def test_occupancy_seeds_off_when_source_unavailable_at_startup(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """A source unavailable at startup seeds occupancy off (no grace timer).

    Consistent with the clear-on-unavailable behavior: after a restart we
    can't know how long the source has been gone, so the safe read is 'not
    occupied'; a recovery to 'on' re-triggers normally.
    """
    lot = "2026-07-01T10:00:00+00:00"
    mock_restore_cache(
        hass,
        [State("binary_sensor.test_occupancy", "on", {"latest_occupied_time": lot})],
    )
    hass.states.async_set("binary_sensor.motion_1", "unavailable")
    hass.set_state(CoreState.starting)

    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "off"
    assert state.attributes["latest_occupied_time"] == lot

    hass.states.async_set("binary_sensor.motion_1", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_occupancy").state == "on"


@pytest.mark.asyncio
async def test_occupancy_restores_latest_occupied_time(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """latest_occupied_time survives a restart via RestoreEntity."""
    lot = "2026-07-01T10:00:00+00:00"
    mock_restore_cache(
        hass,
        [
            State(
                "binary_sensor.test_occupancy",
                "off",
                {"latest_occupied_time": lot, "occupancy_timeout": 30},
            )
        ],
    )

    occupancy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.attributes["latest_occupied_time"] == lot


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["loading", "deleted", "disabled"])
async def test_illuminance_restores_state(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry, source: str
) -> None:
    """A restored 'bright' reading survives a restart with the source missing.

    Once Home Assistant has started, a source that is not registered, or is
    disabled, can never report: its reading is dropped.
    """
    mock_restore_cache(hass, [State("binary_sensor.test_illuminance", "on")])
    if source != "deleted":
        er.async_get(hass).async_get_or_create(
            "sensor",
            "test",
            "lux_1",
            suggested_object_id="lux_1",
            disabled_by=er.RegistryEntryDisabler.USER if source == "disabled" else None,
        )

    hass.set_state(CoreState.starting)
    await setup_entries(hass, illuminance_entry)
    # sensor.lux_1 may just not have loaded yet; the restored value must hold.
    assert hass.states.get("binary_sensor.test_illuminance").state == "on"

    await finish_startup(hass)
    assert hass.states.get("binary_sensor.test_illuminance").state == (
        "on" if source == "loading" else "unavailable"
    )


@pytest.mark.asyncio
async def test_illuminance_unavailable_before_first_reading(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """Without a reading the sensor must not claim 'dark'; off opens the gates."""
    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.test_illuminance").state == "unavailable"

    hass.states.async_set("sensor.lux_1", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == "unavailable"

    hass.states.async_set("sensor.lux_1", "5")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == "off"


@pytest.mark.asyncio
async def test_illuminance_seeds_from_existing_source(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """A source already reporting at setup makes the sensor available at once."""
    hass.states.async_set("sensor.lux_1", "500")

    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.test_illuminance").state == "on"


@pytest.mark.asyncio
async def test_illuminance_holds_value_when_source_unavailable(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """An unavailable lux sensor must not read as 'dark'."""
    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.lux_1", "500")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == "on"

    hass.states.async_set("sensor.lux_1", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == "on"


@pytest.mark.asyncio
async def test_illuminance_sensor_dark(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """Illuminance sensor is OFF (no light detected) when lux is below the threshold."""
    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.lux_1", "5")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_illuminance")
    assert state.state == "off"


@pytest.mark.asyncio
async def test_illuminance_sensor_bright(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """Illuminance sensor is ON (light detected) when lux meets the threshold."""
    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.lux_1", "500")
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_illuminance")
    assert state.state == "on"


@pytest.mark.asyncio
async def test_illuminance_holds_state_on_unparsable_reading(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry
) -> None:
    """A non-numeric source reading holds the last known state."""
    illuminance_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(illuminance_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.lux_1", "500")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == "on"

    hass.states.async_set("sensor.lux_1", "broken")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == "on"

    hass.states.async_set("sensor.lux_1", "5")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("reading", ["nan", "NaN", "inf", "-inf", "Infinity"])
async def test_illuminance_non_finite_first_reading_is_no_reading(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry, reading: str
) -> None:
    """nan or inf as a first reading asserts neither darkness nor brightness."""
    hass.states.async_set("sensor.lux_1", reading)
    await setup_entries(hass, illuminance_entry)
    assert hass.states.get("binary_sensor.test_illuminance").state == "unavailable"

    hass.states.async_set("sensor.lux_1", "5")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("reading", ["nan", "inf", "-inf"])
@pytest.mark.parametrize("held", ["5", "500"])
async def test_illuminance_non_finite_reading_holds_state(
    hass: HomeAssistant, illuminance_entry: MockConfigEntry, reading: str, held: str
) -> None:
    """A later nan or inf holds the last known state, like an unparsable one."""
    await setup_entries(hass, illuminance_entry)
    hass.states.async_set("sensor.lux_1", held)
    await hass.async_block_till_done()
    expected = hass.states.get("binary_sensor.test_illuminance").state

    hass.states.async_set("sensor.lux_1", reading)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.test_illuminance").state == expected


# ---------------------------------------------------------------------------
# Restore robustness: corrupt attributes must never break setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_occupancy_restore_ignores_corrupt_attributes(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """Garbage restored attributes are dropped instead of failing setup."""
    mock_restore_cache(
        hass,
        [
            State(
                "binary_sensor.test_occupancy",
                "off",
                {
                    "latest_occupied_time": "not-a-timestamp",
                    "last_on_time": "also-not-a-timestamp",
                    "false_detection_count": "many",
                },
            )
        ],
    )
    occupancy_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(occupancy_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.test_occupancy")
    assert state.state == "off"
    assert state.attributes["latest_occupied_time"] is None
    assert state.attributes["last_on_time"] is None
    assert state.attributes["false_detection_count"] == 0


@pytest.mark.asyncio
async def test_combined_restores_false_detection_count(hass: HomeAssistant) -> None:
    """The combined sensor's false-detection count survives a restart."""
    mock_restore_cache(
        hass,
        [
            State(
                "binary_sensor.combined_occupancy",
                "off",
                {"false_detection_count": 7},
            )
        ],
    )
    entry = _combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.combined_occupancy")
    assert state.attributes["false_detection_count"] == 7


@pytest.mark.asyncio
async def test_combined_ignores_corrupt_constituent_lot(hass: HomeAssistant) -> None:
    """A constituent clearing with a garbage latest_occupied_time is ignored."""
    entry = _combined_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.test_occupancy", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.combined_occupancy").state == "on"

    hass.states.async_set(
        "binary_sensor.test_occupancy",
        "off",
        {"latest_occupied_time": "garbage"},
    )
    await settle(hass)

    state = hass.states.get("binary_sensor.combined_occupancy")
    assert state.state == "off"
    assert state.attributes["latest_occupied_time"] is None


@pytest.mark.asyncio
async def test_combined_restore_ignores_corrupt_false_count(
    hass: HomeAssistant, occupancy_entry: MockConfigEntry
) -> None:
    """A corrupt restored false_detection_count falls back to zero."""
    mock_restore_cache(
        hass,
        [
            State(
                "binary_sensor.combined_occupancy",
                "off",
                {"false_detection_count": "garbage"},
            )
        ],
    )
    for entry in (occupancy_entry, _occupancy2_entry(), _combined_entry()):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.combined_occupancy")
    assert state is not None
    assert state.attributes["false_detection_count"] == 0


@pytest.mark.asyncio
async def test_combined_ignores_attribute_only_constituent_update(
    hass: HomeAssistant,
) -> None:
    """An attribute-only write on a constituent cannot change the combined state."""
    combined = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.m1"],
        },
    )
    hass.states.async_set("binary_sensor.m1", "on")
    combined.add_to_hass(hass)
    await hass.config_entries.async_setup(combined.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.combined").state == "on"

    hass.states.async_set("binary_sensor.m1", "on", {"battery": 42})
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.combined")
    assert state.state == "on"
    assert state.attributes["last_clear_false_detection"] is False


@pytest.mark.asyncio
async def test_occupancy_source_swap_drops_the_old_sources_anchor(
    hass: HomeAssistant, freezer
) -> None:
    """Switching the source to a sensor that is already on measures the next
    clear from that sensor's own start, not the old source's anchor."""
    hass.set_state(CoreState.running)
    hass.states.async_set("binary_sensor.presence", "on")
    presence_on = datetime.now(UTC)
    freezer.tick(timedelta(seconds=300))
    hass.states.async_set("binary_sensor.motion_1", "off")
    entry = _grace_occupancy_entry()
    await setup_entries(hass, entry)

    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=5))
    await _edit_options(
        hass, entry, **{CONF_OCCUPANCY_SENSOR: "binary_sensor.presence"}
    )
    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "on"
    assert state.attributes["last_on_time"] == presence_on.isoformat()

    hass.states.async_set("binary_sensor.presence", "off")
    await settle(hass)
    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is False
    assert state.attributes["false_detection_count"] == 0
    assert state.attributes["latest_occupied_time"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("new_source", ["unavailable", "unknown", "missing"])
@pytest.mark.parametrize("unavailable_timeout", [0, 60])
async def test_occupancy_source_swap_leaves_the_old_sources_presence_behind(
    hass: HomeAssistant, freezer, new_source: str, unavailable_timeout: int
) -> None:
    """Switching an occupied sensor to a source with no reading yet does not
    carry the old source's presence as a dropout of the new one."""
    hass.set_state(CoreState.running)
    hass.states.async_set("binary_sensor.motion_1", "off")
    if new_source != "missing":
        hass.states.async_set("binary_sensor.presence", new_source)
    entry = _grace_occupancy_entry()
    await setup_entries(hass, entry)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.boot_occupancy").state == "on"

    await _edit_options(
        hass,
        entry,
        **{
            CONF_OCCUPANCY_SENSOR: "binary_sensor.presence",
            CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT: unavailable_timeout,
        },
    )
    await settle(hass)
    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_unavailable"] is False

    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("binary_sensor.boot_occupancy").state == "off"
    hass.states.async_set("binary_sensor.presence", "on")
    await settle(hass)
    assert hass.states.get("binary_sensor.boot_occupancy").state == "on"


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["reload", "restart", "source_swap"])
async def test_occupancy_false_clear_survives_a_reload(
    hass: HomeAssistant, freezer, via: str
) -> None:
    """A clear's classification stands until the next clear, across a reload
    or restart of the entry; a source swap leaves the old source's behind."""
    hass.set_state(CoreState.running)
    hass.states.async_set("binary_sensor.motion_1", "off")
    hass.states.async_set("binary_sensor.presence", "off")
    entry = _grace_occupancy_entry()
    await setup_entries(hass, entry)

    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=31))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await settle(hass)
    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.attributes["last_clear_false_detection"] is True
    assert state.attributes["false_detection_count"] == 1

    if via == "reload":
        assert await hass.config_entries.async_reload(entry.entry_id)
    elif via == "restart":
        await restart_entries(hass, entry)
    else:
        await _edit_options(
            hass, entry, **{CONF_OCCUPANCY_SENSOR: "binary_sensor.presence"}
        )
    await settle(hass)
    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is (via != "source_swap")
    assert state.attributes["false_detection_count"] == 1


@pytest.mark.asyncio
async def test_light_false_off_delay_survives_an_occupancy_reload(
    hass: HomeAssistant, freezer
) -> None:
    """A regular sensor's false clear reloaded away while the maintain sensor
    still held the light would cost the light its quick false-off."""
    hass.set_state(CoreState.running)

    def occupancy(name: str, source: str) -> MockConfigEntry:
        return MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
                CONF_NAME: name,
                CONF_OCCUPANCY_SENSOR: source,
                CONF_OCCUPANCY_TIMEOUT: 30,
                CONF_FALSE_DETECTION_GRACE: 3,
            },
        )

    regular = occupancy("Regular", "binary_sensor.motion_1")
    maintain = occupancy("Maintain", "binary_sensor.presence")
    light = make_light_entry(
        occupancy="binary_sensor.regular",
        maintain="binary_sensor.maintain",
        timeout=60,
        false_off_delay=5,
    )
    hass.states.async_set("light.real_1", "off")
    hass.states.async_set("binary_sensor.motion_1", "off")
    hass.states.async_set("binary_sensor.presence", "off")
    await setup_entries(hass, regular, maintain, light)

    hass.states.async_set("binary_sensor.motion_1", "on")
    hass.states.async_set("binary_sensor.presence", "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=30))
    hass.states.async_set("binary_sensor.motion_1", "off")
    await settle(hass)
    assert hass.states.get("binary_sensor.regular").attributes[
        "last_clear_false_detection"
    ]
    assert hass.states.get("light.matrix_light").state == "on"

    assert await hass.config_entries.async_reload(regular.entry_id)
    await settle(hass)
    hass.states.async_set("binary_sensor.presence", "off")
    await settle(hass)

    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.matrix_light").state == "off"


@pytest.mark.asyncio
async def test_light_false_off_delay_survives_a_combined_occupancy_reload(
    hass: HomeAssistant, freezer
) -> None:
    """The same for a combined sensor's false clear reloaded away."""
    hass.set_state(CoreState.running)
    combined = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Combined",
            CONF_TRIGGER_SENSORS: ["binary_sensor.m1"],
        },
    )
    light = make_light_entry(
        occupancy="binary_sensor.combined",
        maintain="binary_sensor.presence",
        timeout=60,
        false_off_delay=5,
    )
    hass.states.async_set("light.real_1", "off")
    hass.states.async_set("binary_sensor.m1", "off")
    hass.states.async_set("binary_sensor.presence", "off")
    await setup_entries(hass, combined, light)

    hass.states.async_set("binary_sensor.m1", "on")
    hass.states.async_set("binary_sensor.presence", "on")
    await settle(hass)
    hass.states.async_set("binary_sensor.m1", "off")
    await settle(hass)
    assert hass.states.get("binary_sensor.combined").attributes[
        "last_clear_false_detection"
    ]
    assert hass.states.get("light.matrix_light").state == "on"

    assert await hass.config_entries.async_reload(combined.entry_id)
    await settle(hass)
    hass.states.async_set(
        "binary_sensor.presence", "off", {"last_clear_false_detection": True}
    )
    await settle(hass)

    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get("light.matrix_light").state == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("occupied_for", "false"), [(120, False), (31, True)], ids=["genuine", "false"]
)
async def test_occupancy_records_a_clear_missed_during_a_reload(
    hass: HomeAssistant, freezer, occupied_for: int, false: bool
) -> None:
    """A source that clears while the sensor is not loaded still ends its
    cycle: the clear is classified and dated when the source reported it."""
    hass.states.async_set("binary_sensor.motion_1", "off")
    entry = _grace_occupancy_entry()
    await setup_entries(hass, entry)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=occupied_for))

    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.states.async_set("binary_sensor.motion_1", "off")
    cleared = datetime.now(UTC)
    freezer.tick(timedelta(seconds=2))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is false
    assert state.attributes["false_detection_count"] == int(false)
    assert state.attributes["latest_occupied_time"] == (
        None if false else (cleared - timedelta(seconds=30)).isoformat()
    )


@pytest.mark.asyncio
async def test_occupancy_does_not_date_a_clear_from_a_restart(
    hass: HomeAssistant, freezer
) -> None:
    """At startup the source's state only dates the restart, not a departure."""
    hass.states.async_set("binary_sensor.motion_1", "off")
    entry = _grace_occupancy_entry()
    await setup_entries(hass, entry)
    hass.states.async_set("binary_sensor.motion_1", "on")
    await settle(hass)
    freezer.tick(timedelta(seconds=120))

    await restore_state.async_get(hass).async_dump_states()
    hass.states.async_set("binary_sensor.motion_1", "off")
    await crash_entries(hass, entry)

    state = hass.states.get("binary_sensor.boot_occupancy")
    assert state.state == "off"
    assert state.attributes["latest_occupied_time"] is None
    assert state.attributes["false_detection_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", ["plain", "combined", "nested"])
@pytest.mark.parametrize(
    ("occupied_for", "false"), [(120, False), (2, True)], ids=["genuine", "false"]
)
async def test_light_countdown_after_a_clear_missed_during_an_occupancy_reload(
    hass: HomeAssistant, freezer, topology: str, occupied_for: int, false: bool
) -> None:
    """A light gets the normal countdown after a genuine visit whose clear the
    reloading sensor missed, and the quick off after a false one, whether it
    uses the sensor directly or through combined sensors."""
    lamp, motion = RealLight("lamp"), RealBinary("motion")
    await add_real(hass, lamp, motion)
    occupancy = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
            CONF_NAME: "Plain",
            CONF_OCCUPANCY_SENSOR: motion.entity_id,
            CONF_OCCUPANCY_TIMEOUT: 1,
            CONF_FALSE_DETECTION_GRACE: 3,
        },
    )

    def combined(name: str, trigger: str) -> MockConfigEntry:
        return MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
                CONF_NAME: name,
                CONF_TRIGGER_SENSORS: [trigger],
            },
        )

    light = make_light_entry(
        lights=[lamp.entity_id],
        occupancy=f"binary_sensor.{topology}",
        timeout=60,
        false_off_delay=5,
    )
    await setup_entries(
        hass,
        occupancy,
        combined("Combined", "binary_sensor.plain"),
        combined("Nested", "binary_sensor.combined"),
        light,
    )
    motion.set(True)
    await settle(hass)
    assert lamp.is_on
    freezer.tick(timedelta(seconds=occupied_for))
    async_fire_time_changed(hass)
    await settle(hass)

    assert await hass.config_entries.async_unload(occupancy.entry_id)
    await settle(hass)
    motion.set(False)
    await settle(hass)
    assert await hass.config_entries.async_setup(occupancy.entry_id)
    await settle(hass)
    state = hass.states.get(f"binary_sensor.{topology}")
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is false

    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await settle(hass)
    assert lamp.is_on is not false
    freezer.tick(timedelta(seconds=55))
    async_fire_time_changed(hass)
    await settle(hass)
    assert not lamp.is_on


def _nesting_entry() -> MockConfigEntry:
    """A combined sensor triggered by the raw combined one."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Outer",
            CONF_TRIGGER_SENSORS: ["binary_sensor.seed_combined"],
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    ("cleared", "false"),
    [
        ({"latest_occupied_time": "old", "last_clear_false_detection": False}, False),
        ({"latest_occupied_time": "old"}, False),
        ({"last_clear_false_detection": True}, True),
    ],
    ids=["genuine", "genuine-no-verdict", "false"],
)
async def test_combined_judges_a_clear_by_the_constituent_that_cleared(
    hass: HomeAssistant, freezer, nested: bool, cleared: dict, false: bool
) -> None:
    """A genuine clear counts even when its back-dated departure is older than
    history another constituent brought along, which hides it in the max. A
    false clear stays false."""
    newer = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    older = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    hass.states.async_set("binary_sensor.m1", "on")
    hass.states.async_set("binary_sensor.m2", "off", {"latest_occupied_time": newer})
    await setup_entries(hass, _raw_combined_entry(), _nesting_entry())
    entity_id = "binary_sensor.outer" if nested else "binary_sensor.seed_combined"
    assert hass.states.get(entity_id).state == "on"

    freezer.tick(timedelta(seconds=100))
    if "latest_occupied_time" in cleared:
        cleared = {**cleared, "latest_occupied_time": older}
    hass.states.async_set("binary_sensor.m1", "off", cleared)
    await settle(hass)
    state = hass.states.get(entity_id)
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is false
    assert state.attributes["false_detection_count"] == int(false)
    assert state.attributes["latest_occupied_time"] == newer


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("role", [CONF_TRIGGER_SENSORS, CONF_MAINTAIN_SENSORS])
@pytest.mark.parametrize("genuine_before", [False, True])
async def test_combined_constituent_added_mid_cycle_brings_only_history(
    hass: HomeAssistant, freezer, nested: bool, role: str, genuine_before: bool
) -> None:
    """History a constituent added during a cycle already carries is no
    detection in that cycle; a detection the cycle did contain is kept."""
    old = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    hass.states.async_set("binary_sensor.m1", "off")
    hass.states.async_set("binary_sensor.m2", "off")
    hass.states.async_set(
        "binary_sensor.m3",
        "off",
        {"latest_occupied_time": old, "last_clear_false_detection": False},
    )
    inner = _raw_combined_entry()
    await setup_entries(hass, inner, _nesting_entry())
    entity_id = "binary_sensor.outer" if nested else "binary_sensor.seed_combined"

    hass.states.async_set("binary_sensor.m1", "on")
    await settle(hass)
    if genuine_before:
        hass.states.async_set("binary_sensor.m2", "on")
        await settle(hass)
        freezer.tick(timedelta(seconds=40))
        hass.states.async_set(
            "binary_sensor.m2",
            "off",
            {
                "latest_occupied_time": datetime.now(UTC).isoformat(),
                "last_clear_false_detection": False,
            },
        )
        await settle(hass)
    freezer.tick(timedelta(seconds=5))
    await _edit_options(
        hass, inner, **{role: [*molight_config(inner)[role], "binary_sensor.m3"]}
    )
    await settle(hass)
    assert hass.states.get(entity_id).state == "on"

    freezer.tick(timedelta(seconds=26))
    hass.states.async_set("binary_sensor.m1", "off")
    await settle(hass)
    state = hass.states.get(entity_id)
    assert state.state == "off"
    assert state.attributes["last_clear_false_detection"] is not genuine_before
    assert state.attributes["false_detection_count"] == int(not genuine_before)


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("survivor", [CONF_TRIGGER_SENSORS, CONF_MAINTAIN_SENSORS])
@pytest.mark.parametrize("gone", ["unloaded", "removed", "idle"])
async def test_combined_keeps_the_presence_of_a_constituent_that_dropped_out(
    hass: HomeAssistant, freezer, nested: bool, survivor: str, gone: str
) -> None:
    """A constituent that disappears while showing presence was present up
    to then, even though another keeps the sensor on: the survivor's false
    clear ends a genuine cycle and the light counts down from the dropout.
    One that was idle when it disappeared adds nothing."""
    lamp, motion_a, motion_b = RealLight("lamp"), RealBinary("a"), RealBinary("b")
    await add_real(hass, lamp, motion_a, motion_b)

    def occupancy(name: str, source: RealBinary) -> MockConfigEntry:
        return MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
                CONF_NAME: name,
                CONF_OCCUPANCY_SENSOR: source.entity_id,
                CONF_OCCUPANCY_TIMEOUT: 120,
                CONF_FALSE_DETECTION_GRACE: 3,
            },
        )

    first = occupancy("First", motion_a)
    triggers = ["binary_sensor.first"]
    maintain = []
    (triggers if survivor == CONF_TRIGGER_SENSORS else maintain).append(
        "binary_sensor.second"
    )
    together = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Together",
            CONF_TRIGGER_SENSORS: triggers,
            CONF_MAINTAIN_SENSORS: maintain,
        },
    )
    above = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
            CONF_NAME: "Above",
            CONF_TRIGGER_SENSORS: ["binary_sensor.together"],
        },
    )
    entity_id = "binary_sensor.above" if nested else "binary_sensor.together"
    light = make_light_entry(
        lights=[lamp.entity_id], occupancy=entity_id, timeout=300, false_off_delay=5
    )
    await setup_entries(
        hass, first, occupancy("Second", motion_b), together, above, light
    )

    motion_a.set(True)
    await settle(hass)
    freezer.tick(timedelta(seconds=300))
    async_fire_time_changed(hass)
    motion_b.set(True)
    await settle(hass)
    if gone == "idle":
        # A false cycle of its own, over before it disappears.
        motion_a.set(False)
        motion_b.set(False)
        await settle(hass)
        freezer.tick(timedelta(seconds=400))
        async_fire_time_changed(hass)
        await settle(hass)
        assert not lamp.is_on
        motion_a.set(True)
        await settle(hass)
        freezer.tick(timedelta(seconds=121))
        motion_b.set(True)
        motion_a.set(False)
        await settle(hass)
        assert hass.states.get(entity_id).state == "on"
    dropout = datetime.now(UTC)
    if gone == "removed":
        assert await hass.config_entries.async_remove(first.entry_id)
    else:
        assert await hass.config_entries.async_unload(first.entry_id)
    await settle(hass)
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert hass.states.get(entity_id).state == "on"

    motion_b.set(False)
    await settle(hass)
    state = hass.states.get(entity_id)
    assert state.state == "off"
    present = gone != "idle"
    assert state.attributes["last_clear_false_detection"] is not present
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await settle(hass)
    assert lamp.is_on is present
    if present:
        assert state.attributes["latest_occupied_time"] == dropout.isoformat()
        freezer.move_to(dropout + timedelta(seconds=299))
        async_fire_time_changed(hass)
        await settle(hass)
        assert lamp.is_on
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass)
        await settle(hass)
        assert not lamp.is_on
