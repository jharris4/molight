"""Black-box acceptance runner for a live Home Assistant instance."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib import error, parse, request

HA_URL = os.environ.get("MOLIGHT_E2E_HA_URL", "http://homeassistant:8123").rstrip("/")
CLIENT_ID = "http://homeassistant:8123/"
USERNAME = "molight-e2e"
PASSWORD = "molight-e2e-only"
REQUEST_TIMEOUT = 10
WAIT_TIMEOUT = 90

RAW_LIGHT = "light.e2e_main"
RAW_TIMER_LIGHT = "light.e2e_timer_target"
RAW_MULTI_ON_OFF = "light.e2e_multi_on_off"
RAW_MULTI_DIMMER = "light.e2e_multi_dimmer"
RAW_MULTI_RGB = "light.e2e_multi_rgb"
RAW_MOTION = "binary_sensor.e2e_motion"
RAW_REMOVAL_MOTION = "binary_sensor.e2e_removal_motion"
RAW_DOOR = "binary_sensor.e2e_door"
RAW_ILLUMINANCE = "sensor.e2e_illuminance"
RAW_SCHEDULE = "binary_sensor.e2e_schedule_source"
EVENT_BUTTON = "event.e2e_button"
TARGET_SELECT = "select.e2e_target_mode"
SOURCE_SELECT = "select.e2e_source_mode"
VIRTUAL_OCCUPANCY = "binary_sensor.e2e_occupancy"
RAW_TIMER_MOTION = "binary_sensor.e2e_raw_occupancy"
VIRTUAL_TIMER_OCCUPANCY = "binary_sensor.e2e_timer_occupancy"
VIRTUAL_ILLUMINANCE = "binary_sensor.e2e_illuminance"
COLD_ILLUMINANCE = "binary_sensor.e2e_cold_illuminance"
VIRTUAL_SCHEDULE = "binary_sensor.e2e_schedule"
INVERTED_SCHEDULE = "binary_sensor.e2e_inverted_schedule"
END_SCHEDULE = "binary_sensor.e2e_end_schedule"
END_ACTION_LIGHT = "light.e2e_end_action"
GATE_MODE_LIGHT = "light.e2e_gate_mode"
TRIGGER_OCCUPANCY = "binary_sensor.e2e_trigger_occupancy"
COMBINED_OCCUPANCY = "binary_sensor.e2e_combined"
MAINTAIN_LIGHT = "light.e2e_maintain"
GRACE_OCCUPANCY = "binary_sensor.e2e_grace_occupancy"
GRACE_LIGHT = "light.e2e_grace"
HOLD_LIGHT = "light.e2e_hold"
LATE_OCCUPANCY = "binary_sensor.e2e_late_occupancy"
LATE_LIGHT = "light.e2e_late"
VIRTUAL_LIGHT = "light.e2e_scheduled"
VIRTUAL_TIMER_LIGHT = "light.e2e_timer"
VIRTUAL_MULTI_LIGHT = "light.e2e_multi"
PHYSICAL_LIGHT = "light.e2e_physical"
VIRTUAL_AUTO_OFF_LIGHT = "light.e2e_auto_off"
AUTO_OFF_SWITCH = "switch.e2e_auto_off_auto_off"
VIRTUAL_RESTART_WARNING_LIGHT = "light.e2e_restart_warning"
RESTART_WARNING_SWITCH = "switch.e2e_restart_warning_auto_off"
REMOVAL_OCCUPANCY = "binary_sensor.e2e_removed_occupancy"
REMOVAL_LIGHT = "light.e2e_removal_light"
REMOTE_LAST_ACTION = "sensor.e2e_remote_last_action"
PRESET_REMOTE_LAST_ACTION = "sensor.e2e_preset_remote_last_action"
PROFILE_OUTSIDE = "outside_schedule"
PROFILE_INSIDE = "inside_schedule"

UPGRADE_OCCUPANCY = "binary_sensor.upgrade_occupancy"
UPGRADE_COMBINED = "binary_sensor.upgrade_combined"
UPGRADE_ILLUMINANCE = "binary_sensor.upgrade_illuminance"
UPGRADE_SCHEDULE = "binary_sensor.upgrade_schedule"
UPGRADE_LIGHT = "light.upgrade_gated"
UPGRADE_REMOTE_SENSOR = "sensor.upgrade_remote_last_action"
UPGRADE_LEGACY_LIGHT = "light.upgrade_colored_on_off"
UPGRADE_LEGACY_ILLUMINANCE = "binary_sensor.upgrade_wide_hysteresis"
UPGRADE_SNAPSHOT = Path("/ha-config/e2e-upgrade-snapshot.json")
FIXTURES_SNAPSHOT = Path("/ha-config/e2e-fixtures-snapshot.json")
AUTO_OFF_SNAPSHOT = Path("/ha-config/e2e-auto-off-snapshot.json")
RESTART_WARNING_SNAPSHOT = Path("/ha-config/e2e-restart-warning-snapshot.json")
FALSE_DETECTION_SNAPSHOT = Path("/ha-config/e2e-false-detection-snapshot.json")
LATE_SOURCE_SNAPSHOT = Path("/ha-config/e2e-late-source-snapshot.json")
CONFIG_ENTRIES_STORAGE = Path("/ha-config/.storage/core.config_entries")

EMPTY_LIGHT_SECTIONS = {"sensors": {}, "behavior": {}, "warning": {}}


def pct(percent: int) -> int:
    """Convert a MoLight percent setting to the 0-255 brightness it sends."""
    return round(percent * 255 / 100)


LIGHT_ENTRY_KEYS = (
    "name",
    "lights",
    "schedule_entity",
    "schedule_mode",
    "schedule_end_action",
    "entity_id",
)
# Inputs conversion strips from the outside profile when promoting a light.
CONVERSION_INPUT_KEYS = (
    "occupancy_entity",
    "maintain_occupancy_entity",
    "illuminance_entity",
    "door_entity",
)
EMPTY_REMOTE_SECTIONS = {
    "turn_on": {},
    "turn_off": {},
    "toggle": {},
    "brightness_up": {},
    "brightness_down": {},
    "preset_1": {},
    "preset_2": {},
}


class ApiError(RuntimeError):
    """Describe a failed Home Assistant API request."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def poll_tolerates(err: Exception, *, missing_ok: bool) -> bool:
    """Whether a polling loop should ride out this request error."""
    if not isinstance(err, ApiError):
        return True  # connection refused or timed out while HA restarts
    if err.status is not None and err.status >= 500:
        return True
    return missing_ok and err.status == 404


class HomeAssistantClient:
    """Small standard-library client for HA REST and config-flow APIs."""

    def __init__(self) -> None:
        """Initialize an unauthenticated client."""
        self.token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        form: bool = False,
    ) -> Any:
        """Make one JSON or form request and decode its response."""
        headers = {"Accept": "application/json"}
        body = None
        if data is not None:
            if form:
                body = parse.urlencode(data).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = request.Request(
            f"{HA_URL}{path}", data=body, headers=headers, method=method
        )
        try:
            with request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                payload = response.read()
        except error.HTTPError as err:
            detail = err.read().decode(errors="replace")
            raise ApiError(
                f"{method} {path} returned {err.code}: {detail}", err.code
            ) from err
        if not payload:
            return None
        return json.loads(payload)

    def wait_ready(self, timeout: float = WAIT_TIMEOUT) -> None:
        """Wait until Home Assistant's HTTP API is serving requests."""
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.request("GET", "/api/onboarding")
                return
            except (ApiError, error.URLError, TimeoutError) as err:
                last_error = err
                time.sleep(0.5)
        raise TimeoutError(f"Home Assistant did not become ready: {last_error}")

    def boot_marker(self) -> str:
        """Return a value that changes whenever Home Assistant core boots."""
        # zone.home is written fresh at startup, so its last_changed is the boot time.
        return self.state("zone.home")["last_changed"]

    def restart_core(self) -> None:
        """Restart core and wait until a new boot is serving requests."""
        marker = self.boot_marker()
        self.call_service("homeassistant", "restart", {})
        self.wait_state(
            "zone.home",
            lambda state: state["last_changed"] != marker,
            "rebooted after the core restart",
            timeout=WAIT_TIMEOUT,
        )

    def authenticate(self) -> None:
        """Create the isolated owner or log back in after a restart."""
        onboarding = self.request("GET", "/api/onboarding")
        user_done = next(item["done"] for item in onboarding if item["step"] == "user")
        if not user_done:
            result = self.request(
                "POST",
                "/api/onboarding/users",
                {
                    "name": "MoLight E2E",
                    "username": USERNAME,
                    "password": PASSWORD,
                    "client_id": CLIENT_ID,
                    "language": "en",
                },
            )
            code = result["auth_code"]
        else:
            flow = self.request(
                "POST",
                "/auth/login_flow",
                {
                    "client_id": CLIENT_ID,
                    "handler": ["homeassistant", None],
                    "redirect_uri": CLIENT_ID,
                },
            )
            result = self.request(
                "POST",
                f"/auth/login_flow/{flow['flow_id']}",
                {
                    "client_id": CLIENT_ID,
                    "username": USERNAME,
                    "password": PASSWORD,
                },
            )
            if result.get("type") != "create_entry":
                raise AssertionError(f"Authentication flow did not finish: {result}")
            code = result["result"]
        token = self.request(
            "POST",
            "/auth/token",
            {
                "client_id": CLIENT_ID,
                "grant_type": "authorization_code",
                "code": code,
            },
            form=True,
        )
        self.token = token["access_token"]

    def finish_onboarding(self) -> None:
        """Complete non-user onboarding steps for browser-driven scenarios."""
        onboarding = {
            item["step"]: item["done"]
            for item in self.request("GET", "/api/onboarding")
        }
        if not onboarding.get("core_config", False):
            self.request("POST", "/api/onboarding/core_config", {})
        if not onboarding.get("integration", False):
            self.request(
                "POST",
                "/api/onboarding/integration",
                {"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID},
            )
        if not onboarding.get("analytics", False):
            self.request("POST", "/api/onboarding/analytics", {})

    def state(self, entity_id: str) -> dict[str, Any]:
        """Return one entity's current state object."""
        return self.request("GET", f"/api/states/{entity_id}")

    def wait_state(
        self,
        entity_id: str,
        predicate: Callable[[dict[str, Any]], bool],
        description: str,
        timeout: float = 20,
    ) -> dict[str, Any]:
        """Poll an entity until a semantic assertion becomes true."""
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                last = self.state(entity_id)
                if predicate(last):
                    return last
            except (ApiError, error.URLError, TimeoutError) as err:
                if not poll_tolerates(err, missing_ok=True):
                    raise
                last_error = err
            time.sleep(0.2)
        raise AssertionError(
            f"Timed out waiting for {entity_id} to be {description}; last={last}"
            + (f"; last error: {last_error}" if last_error else "")
        )

    def call_service(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        """Call a Home Assistant service and wait for its blocking response."""
        return self.request("POST", f"/api/services/{domain}/{service}", data)

    def set_state(
        self,
        entity_id: str,
        state: str | float | bool,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Drive a simulated entity through the testbed's public service."""
        self.call_service(
            "molight_testbed",
            "set_state",
            {
                "entity_id": entity_id,
                "state": state,
                "attributes": attributes or {},
            },
        )

    def set_available(self, entity_id: str, available: bool) -> None:
        """Drive simulated availability through the testbed service."""
        self.call_service(
            "molight_testbed",
            "set_available",
            {"entity_id": entity_id, "available": available},
        )

    def set_behavior(self, entity_id: str, **behavior: Any) -> None:
        """Change how a simulated light reports back after commands."""
        self.call_service(
            "molight_testbed",
            "set_behavior",
            {"entity_id": entity_id, "behavior": behavior},
        )

    def set_startup_delay(self, entity_id: str, seconds: float) -> None:
        """Hold a simulated sensor back for this long at the next boot."""
        self.call_service(
            "molight_testbed",
            "set_startup_delay",
            {"entity_id": entity_id, "seconds": seconds},
        )

    def fire_event(
        self,
        entity_id: str,
        event_type: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event from a simulated event entity."""
        self.call_service(
            "molight_testbed",
            "fire_event",
            {
                "entity_id": entity_id,
                "event_type": event_type,
                "attributes": attributes or {},
            },
        )

    def start_flow(self, *, options_entry_id: str | None = None) -> dict[str, Any]:
        """Start a MoLight config flow or an entry's options flow."""
        if options_entry_id:
            return self.request(
                "POST",
                "/api/config/config_entries/options/flow",
                {"handler": options_entry_id},
            )
        return self.request(
            "POST", "/api/config/config_entries/flow", {"handler": "molight"}
        )

    def continue_flow(
        self,
        result: dict[str, Any],
        data: dict[str, Any],
        *,
        options: bool = False,
    ) -> dict[str, Any]:
        """Submit the current config or options flow form."""
        kind = "options/" if options else ""
        return self.request(
            "POST",
            f"/api/config/config_entries/{kind}flow/{result['flow_id']}",
            data,
        )

    def abort_flow(self, result: dict[str, Any], *, options: bool = False) -> None:
        """Abandon an in-progress config or options flow left on a rejected form."""
        kind = "options/" if options else ""
        self.request(
            "DELETE", f"/api/config/config_entries/{kind}flow/{result['flow_id']}"
        )

    def molight_entries(self) -> list[dict[str, Any]]:
        """Return live MoLight config entries from Home Assistant."""
        return self.request("GET", "/api/config/config_entries/entry?domain=molight")

    def remove_entry(self, entry_id: str) -> Any:
        """Remove a temporary config entry through Home Assistant's API."""
        return self.request("DELETE", f"/api/config/config_entries/entry/{entry_id}")


def checkpoint(message: str) -> None:
    """Narrate one completed step so a failure is easy to place."""
    print(f"  ok: {message}", flush=True)


def expect_step(result: dict[str, Any], step_id: str) -> None:
    """Assert that a backend config flow reached the expected step."""
    if result.get("step_id") != step_id:
        raise AssertionError(f"Expected flow step {step_id!r}, got {result}")


def start_create(client: HomeAssistantClient, entity_type: str) -> dict[str, Any]:
    """Start the manual-create branch and choose one entity type."""
    result = client.start_flow()
    expect_step(result, "user")
    result = client.continue_flow(result, {"next_step_id": "create"})
    expect_step(result, "create")
    return client.continue_flow(result, {"entity_type": entity_type})


def create_entry(
    client: HomeAssistantClient,
    entity_type: str,
    payload: dict[str, Any],
    description: str,
) -> str:
    """Create one single-form MoLight entry through the backend config flow."""
    result = start_create(client, entity_type)
    expect_step(result, entity_type)
    return finish_creation(client.continue_flow(result, payload), description)


def create_virtual_schedule(
    client: HomeAssistantClient,
    name: str = "E2E Schedule",
    entity_id: str = "e2e_schedule",
    invert: bool = False,
    source: str = RAW_SCHEDULE,
) -> str:
    """Create a source-backed MoLight Virtual Schedule through its API flow."""
    result = start_create(client, "schedule")
    expect_step(result, "schedule")
    result = client.continue_flow(result, {"schedule_definition": "binary_sensor"})
    expect_step(result, "schedule_source")
    result = client.continue_flow(
        result,
        {
            "name": name,
            "schedule_source": source,
            "schedule_invert": invert,
            "advanced": {"entity_id": entity_id},
        },
    )
    return finish_creation(result, name)


def wait_inverted_schedule(client: HomeAssistantClient, raw_state: str) -> None:
    """Assert the inverted schedule mirrors the opposite of its source."""
    expected = "off" if raw_state == "on" else "on"
    client.wait_state(
        INVERTED_SCHEDULE,
        lambda state: (
            state["state"] == expected
            and state["attributes"].get("inverted") is True
            and state["attributes"].get("source_entity") == RAW_SCHEDULE
        ),
        f"{expected} while its source is {raw_state}, reporting inversion",
    )


def run_inverted_schedule_scenario(client: HomeAssistantClient) -> None:
    """An inverted source-backed schedule is on exactly when its source is off."""
    entry_id = create_virtual_schedule(
        client, "E2E Inverted Schedule", "e2e_inverted_schedule", invert=True
    )
    assert_entry_loaded(client, entry_id)
    client.wait_state(RAW_SCHEDULE, lambda state: state["state"] == "off", "off")
    wait_inverted_schedule(client, "off")
    client.set_state(RAW_SCHEDULE, "on")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    wait_inverted_schedule(client, "on")
    client.set_state(RAW_SCHEDULE, "off")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "off", "off")
    wait_inverted_schedule(client, "off")
    wait_profile(client, PROFILE_OUTSIDE)
    client.remove_entry(entry_id)
    wait_entity_absent(client, INVERTED_SCHEDULE)
    wait_entry_removed(client, entry_id, "Temporary inverted schedule")
    checkpoint("inverted source-backed schedule mirrors the opposite of its source")


def create_virtual_occupancy(
    client: HomeAssistantClient,
    name: str = "E2E Occupancy",
    source: str = RAW_MOTION,
    entity_id: str = "e2e_occupancy",
    timeout: int = 1,
) -> str:
    """Create a MoLight occupancy sensor wrapping a simulated motion sensor."""
    return create_entry(
        client,
        "occupancy",
        {
            "name": name,
            "occupancy_sensor": source,
            "occupancy_timeout": timeout,
            "advanced": {
                "false_detection_grace": 0,
                "clear_on_unavailable_timeout": 1,
                "entity_id": entity_id,
            },
        },
        name,
    )


def create_virtual_illuminance(
    client: HomeAssistantClient,
    name: str = "E2E Illuminance",
    entity_id: str = "e2e_illuminance",
) -> str:
    """Create a MoLight illuminance threshold wrapping the simulated lux sensor."""
    return create_entry(
        client,
        "illuminance",
        {
            "name": name,
            "illuminance_sensor": RAW_ILLUMINANCE,
            "illuminance_threshold": 10,
            "illuminance_hysteresis": 1,
            "advanced": {"entity_id": entity_id},
        },
        name,
    )


def run_cold_illuminance_scenario(client: HomeAssistantClient) -> None:
    """A new illuminance sensor stays unavailable until its source reports.

    The first reading is judged against the bare threshold; only later ones
    use the hysteresis band.
    """
    client.set_available(RAW_ILLUMINANCE, False)
    client.wait_state(
        RAW_ILLUMINANCE, lambda state: state["state"] == "unavailable", "unavailable"
    )
    entry_id = create_virtual_illuminance(
        client, "E2E Cold Illuminance", "e2e_cold_illuminance"
    )
    assert_entry_loaded(client, entry_id)
    assert_state_stays(
        client,
        COLD_ILLUMINANCE,
        lambda state: state["state"] == "unavailable",
        "unavailable before its source has ever reported",
    )
    client.set_state(RAW_ILLUMINANCE, 10)  # inside the band, at the bare threshold
    client.set_available(RAW_ILLUMINANCE, True)
    client.wait_state(
        COLD_ILLUMINANCE,
        lambda state: state["state"] == "on",
        "bright: the first reading is judged against the bare threshold",
    )
    client.set_state(RAW_ILLUMINANCE, 9.5)
    assert_state_stays(
        client,
        COLD_ILLUMINANCE,
        lambda state: state["state"] == "on",
        "bright inside the hysteresis band",
    )
    client.set_state(RAW_ILLUMINANCE, 8.9)
    client.wait_state(COLD_ILLUMINANCE, lambda state: state["state"] == "off", "dark")
    client.set_state(RAW_ILLUMINANCE, 10.5)
    assert_state_stays(
        client,
        COLD_ILLUMINANCE,
        lambda state: state["state"] == "off",
        "dark inside the hysteresis band",
    )
    client.set_state(RAW_ILLUMINANCE, 11)
    client.wait_state(COLD_ILLUMINANCE, lambda state: state["state"] == "on", "bright")
    client.set_state(RAW_ILLUMINANCE, 5)
    client.remove_entry(entry_id)
    wait_entity_absent(client, COLD_ILLUMINANCE)
    wait_entry_removed(client, entry_id, "Temporary cold illuminance")
    checkpoint(
        "illuminance sensor starts unavailable, first reading uses the bare threshold"
    )


def light_settings(brightness: int, *, inside: bool) -> dict[str, Any]:
    """Build one profile with deliberately distinct illuminance/door modes."""
    return {
        **EMPTY_LIGHT_SECTIONS,
        "light_timeout": 30,
        "sensors": {
            "occupancy_entity": VIRTUAL_OCCUPANCY,
            "illuminance_entity": VIRTUAL_ILLUMINANCE,
            "illuminance_mode": "control" if inside else "gate",
            "door_entity": RAW_DOOR,
            "door_mode": "open_close" if inside else "open",
        },
        "behavior": {
            "auto_on_brightness": brightness,
            "auto_on_transition": 1,
            "turn_on_select_entity": TARGET_SELECT,
        },
    }


def flat_light_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten a sectioned light form into the per-profile settings MoLight stores."""
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return {key: value for key, value in flat.items() if key not in LIGHT_ENTRY_KEYS}


SCHEDULED_INSIDE_SETTINGS = {
    **flat_light_settings(light_settings(80, inside=True)),
    "turn_on_select_option": "Cozy",
    "turn_on_select_source_entity": SOURCE_SELECT,
}


def submit_selection(
    client: HomeAssistantClient, result: dict[str, Any]
) -> dict[str, Any]:
    """Finish a scheduled profile's target-dependent selection page."""
    expect_step(result, "scheduled_light_selection")
    return client.continue_flow(
        result,
        {
            "turn_on_select_option": "Cozy",
            "turn_on_select_source_entity": SOURCE_SELECT,
        },
    )


def create_scheduled_light(client: HomeAssistantClient) -> str:
    """Create a two-profile scheduled light through the backend config flow."""
    result = start_create(client, "scheduled_light")
    expect_step(result, "scheduled_light")
    result = client.continue_flow(
        result,
        {
            "name": "E2E Scheduled",
            "lights": [RAW_LIGHT],
            "schedule_entity": VIRTUAL_SCHEDULE,
            "schedule_end_action": "keep",
            "advanced": {"entity_id": "e2e_scheduled"},
        },
    )
    expect_step(result, "scheduled_light_outside")
    result = submit_selection(
        client, client.continue_flow(result, light_settings(30, inside=False))
    )
    expect_step(result, "scheduled_light_inside")
    result = submit_selection(
        client, client.continue_flow(result, light_settings(80, inside=True))
    )
    return finish_creation(result, "Scheduled light")


def create_timeout_light(
    client: HomeAssistantClient, light_timeout: int = 4, stage: int = 1
) -> str:
    """Create one short-lived light for a real timeout/warning sequence."""
    return create_entry(
        client,
        "light",
        {
            "name": "E2E Timer",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": light_timeout,
            "sensors": {"occupancy_entity": VIRTUAL_TIMER_OCCUPANCY},
            "behavior": {
                "false_detection_off_delay": 0,
                "auto_on_brightness": 60,
            },
            "warning": {
                "effect_timeout": stage,
                "effect_brightness": 10,
                "warn_timeout": stage,
                "warn_brightness": 20,
            },
            "advanced": {"entity_id": "e2e_timer"},
        },
        "Timer light",
    )


def create_auto_off_light(client: HomeAssistantClient) -> str:
    """Create a short-timeout light dedicated to switch persistence."""
    return create_entry(
        client,
        "light",
        {
            "name": "E2E Auto-off",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 3,
            "sensors": {"occupancy_entity": VIRTUAL_TIMER_OCCUPANCY},
            "behavior": {"auto_on_brightness": 60},
            "warning": {},
            "advanced": {"entity_id": "e2e_auto_off"},
        },
        "Auto-off light",
    )


def create_restart_warning_light(client: HomeAssistantClient) -> str:
    """Create a light whose live warning stage spans a container restart."""
    return create_entry(
        client,
        "light",
        {
            "name": "E2E Restart Warning",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 10,
            "sensors": {"occupancy_entity": VIRTUAL_TIMER_OCCUPANCY},
            "behavior": {"auto_on_brightness": 60},
            "warning": {
                "effect_timeout": 0,
                "warn_timeout": 10,
                "warn_brightness": 20,
            },
            "advanced": {"entity_id": "e2e_restart_warning"},
        },
        "Restart-warning light",
    )


def create_multi_light(client: HomeAssistantClient) -> str:
    """Create an isolated mixed-capability virtual light."""
    return create_entry(
        client,
        "light",
        {
            "name": "E2E Multi",
            "lights": [RAW_MULTI_ON_OFF, RAW_MULTI_DIMMER, RAW_MULTI_RGB],
            "light_timeout": 30,
            **EMPTY_LIGHT_SECTIONS,
            "advanced": {"entity_id": "e2e_multi"},
        },
        "Multi-light",
    )


def create_remote(client: HomeAssistantClient) -> str:
    """Create a current-release remote with single and double bindings."""
    return create_entry(
        client,
        "remote",
        {
            "name": "E2E Remote",
            "target_lights": [VIRTUAL_MULTI_LIGHT],
            "dim_step": 20,
            **EMPTY_REMOTE_SECTIONS,
            "turn_on": {"on_buttons_single": [EVENT_BUTTON]},
            "turn_off": {"off_buttons_double": [EVENT_BUTTON]},
        },
        "Remote",
    )


def edit_remote(client: HomeAssistantClient, entry_id: str) -> None:
    """Replace the current remote's bindings through its options flow."""
    result = client.start_flow(options_entry_id=entry_id)
    expect_step(result, "remote")
    result = client.continue_flow(
        result,
        {
            "name": "E2E Remote Edited",
            "target_lights": [VIRTUAL_MULTI_LIGHT],
            "dim_step": 20,
            **EMPTY_REMOTE_SECTIONS,
            "toggle": {"toggle_buttons_double": [EVENT_BUTTON]},
            "brightness_up": {"brightness_up_buttons_single": [EVENT_BUTTON]},
        },
        options=True,
    )
    if result.get("type") != "create_entry":
        raise AssertionError(f"Remote options failed: {result}")


def create_removal_light(client: HomeAssistantClient) -> str:
    """Create a surviving light that references the removable sensor."""
    return create_entry(
        client,
        "light",
        {
            "name": "E2E Removal Light",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 30,
            "sensors": {"occupancy_entity": REMOVAL_OCCUPANCY},
            "behavior": {"auto_on_brightness": 50},
            "warning": {},
            "advanced": {"entity_id": "e2e_removal_light"},
        },
        "Removal light",
    )


def edit_scheduled_light(client: HomeAssistantClient, entry_id: str) -> None:
    """Complete the live options flow and prove the entry reloads in place."""
    result = client.start_flow(options_entry_id=entry_id)
    expect_step(result, "scheduled_light")
    result = client.continue_flow(
        result,
        {
            "name": "E2E Scheduled Edited",
            "lights": [RAW_LIGHT],
            "schedule_entity": VIRTUAL_SCHEDULE,
            "schedule_end_action": "keep",
        },
        options=True,
    )
    expect_step(result, "scheduled_light_outside")
    result = client.continue_flow(
        result, light_settings(30, inside=False), options=True
    )
    expect_step(result, "scheduled_light_selection")
    result = client.continue_flow(
        result,
        {
            "turn_on_select_option": "Cozy",
            "turn_on_select_source_entity": SOURCE_SELECT,
        },
        options=True,
    )
    expect_step(result, "scheduled_light_inside")
    result = client.continue_flow(result, light_settings(80, inside=True), options=True)
    expect_step(result, "scheduled_light_selection")
    result = client.continue_flow(
        result,
        {
            "turn_on_select_option": "Cozy",
            "turn_on_select_source_entity": SOURCE_SELECT,
        },
        options=True,
    )
    if result.get("type") != "create_entry":
        raise AssertionError(f"Scheduled light options failed: {result}")


def convert_light(
    client: HomeAssistantClient,
    direction: str,
    expected_step: str,
    entity_id: str = VIRTUAL_LIGHT,
) -> None:
    """Convert the live entry in place using MoLight's backend flow."""
    result = client.start_flow()
    result = client.continue_flow(result, {"next_step_id": "convert_lights"})
    expect_step(result, "convert_lights")
    result = client.continue_flow(result, {"next_step_id": direction})
    expect_step(result, direction)
    result = client.continue_flow(result, {"convert_lights": [entity_id]})
    expect_step(result, expected_step)
    result = client.continue_flow(result, {"confirm_conversion": True})
    if result.get("type") != "abort" or result.get("reason") != "conversion_done":
        raise AssertionError(f"Light conversion failed: {result}")


def wait_profile(client: HomeAssistantClient, profile: str) -> dict[str, Any]:
    """Wait for a scheduled light to expose the selected settings profile."""
    return client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: (
            state["attributes"].get("active_settings") == profile
            and state["attributes"].get("active_settings_schedule") == VIRTUAL_SCHEDULE
            and state["attributes"].get("schedule_end_off_pending") is False
        ),
        f"using the {profile} profile of {VIRTUAL_SCHEDULE}, no boundary off pending",
    )


def reset_trigger(client: HomeAssistantClient) -> None:
    """Return occupancy, door, illuminance, and lights to a dark/off baseline."""
    client.set_state(RAW_MOTION, "off")
    client.set_state(RAW_DOOR, "off")
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "off", "off")
    client.set_state(RAW_ILLUMINANCE, 5)
    client.wait_state(
        VIRTUAL_ILLUMINANCE, lambda state: state["state"] == "off", "dark"
    )
    client.call_service("light", "turn_off", {"entity_id": VIRTUAL_LIGHT})
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "off", "off")


def set_timer_motion(client: HomeAssistantClient, on: bool) -> None:
    """Drive the occupancy source dedicated to the timer-style lights."""
    state = "on" if on else "off"
    client.set_state(RAW_TIMER_MOTION, state)
    client.wait_state(
        VIRTUAL_TIMER_OCCUPANCY, lambda current: current["state"] == state, state
    )


def trigger_and_assert(
    client: HomeAssistantClient,
    brightness: int,
    selection: str,
    source: str = SOURCE_SELECT,
) -> None:
    """Trigger occupancy and assert routed light/select service effects."""
    client.set_state(RAW_MOTION, "on")
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "on", "on")
    client.wait_state(
        RAW_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("brightness") == brightness
            and command_data(state).get("transition") == 1
        ),
        f"on at brightness {brightness} with the automatic fade",
    )
    client.wait_state(
        TARGET_SELECT,
        lambda state: state["state"] == selection,
        f"selected as {selection}",
    )
    client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: (
            state["attributes"].get("last_turn_on_selection_option") == selection
            and state["attributes"].get("last_turn_on_selection_source") == source
        ),
        f"reporting the {selection!r} selection from {source}",
    )


def wait_machine_state(
    client: HomeAssistantClient,
    machine_state: str,
    entity_id: str = VIRTUAL_LIGHT,
) -> dict[str, Any]:
    """Wait for the virtual light's internal state-machine diagnostic."""
    return client.wait_state(
        entity_id,
        lambda state: state["attributes"].get("molight_state") == machine_state,
        f"in {machine_state} state",
    )


def expect_rejection(
    client: HomeAssistantClient,
    result: dict[str, Any],
    code: str | tuple[str, ...],
    *,
    options: bool = False,
) -> None:
    """Assert a submitted form came back with an expected error, then abandon it."""
    codes = (code,) if isinstance(code, str) else code
    errors = result.get("errors") or {}
    if result.get("type") != "form" or not any(c in errors.values() for c in codes):
        raise AssertionError(f"Expected the form to reject with {codes!r}: {result}")
    client.abort_flow(result, options=options)


def submit_create(
    client: HomeAssistantClient, entity_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Submit one single-form creation and return the raw flow result."""
    result = start_create(client, entity_type)
    expect_step(result, entity_type)
    return client.continue_flow(result, payload)


def run_config_flow_rejections(client: HomeAssistantClient) -> None:
    """The forms reject settings the runtime could not honour."""
    on_off_light = {
        "name": "E2E Rejected",
        "lights": [RAW_MULTI_ON_OFF],
        "light_timeout": 30,
        **EMPTY_LIGHT_SECTIONS,
        "advanced": {},
    }
    for behavior, code in (
        ({"auto_on_rgb_color": [255, 0, 0]}, "color_unsupported"),
        ({"auto_on_brightness": 50}, "brightness_unsupported"),
        ({"auto_on_transition": 1}, "transition_unsupported"),
    ):
        result = submit_create(client, "light", {**on_off_light, "behavior": behavior})
        expect_rejection(client, result, code)

    result = submit_create(
        client,
        "illuminance",
        {
            "name": "E2E Rejected Illuminance",
            "illuminance_sensor": RAW_ILLUMINANCE,
            "illuminance_threshold": 10,
            "illuminance_hysteresis": 10,
            "advanced": {},
        },
    )
    expect_rejection(client, result, "hysteresis_too_large")

    slow_entry_id = create_virtual_occupancy(
        client, "E2E Slow Occupancy", RAW_REMOVAL_MOTION, "e2e_slow_occupancy", 30
    )
    assert_entry_loaded(client, slow_entry_id)
    result = submit_create(
        client,
        "light",
        {
            "name": "E2E Rejected",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 10,
            **EMPTY_LIGHT_SECTIONS,
            "sensors": {"occupancy_entity": "binary_sensor.e2e_slow_occupancy"},
            "advanced": {},
        },
    )
    expect_rejection(client, result, "light_timeout_too_short")
    client.remove_entry(slow_entry_id)
    wait_entry_removed(client, slow_entry_id, "Temporary slow occupancy")

    result = submit_create(
        client,
        "light",
        {
            "name": "E2E Rejected",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 30,
            **EMPTY_LIGHT_SECTIONS,
            "advanced": {"entity_id": "e2e_scheduled"},
        },
    )
    expect_rejection(client, result, "entity_id_conflict")

    result = submit_create(
        client,
        "light",
        {
            "name": "E2E Rejected",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 30,
            **EMPTY_LIGHT_SECTIONS,
            "sensors": {"schedule_entity": RAW_DOOR, "schedule_mode": "gate"},
            "advanced": {},
        },
    )
    expect_rejection(client, result, "schedule_entity_not_schedule")

    remote = {
        "name": "E2E Rejected Remote",
        "target_lights": [VIRTUAL_LIGHT],
        "dim_step": 20,
        **EMPTY_REMOTE_SECTIONS,
    }
    for bindings, code in (
        ({}, "buttons_required"),
        (
            {"preset_1": {"preset_1_buttons_single": [EVENT_BUTTON]}},
            "preset_values_required",
        ),
        (
            {
                "turn_on": {"on_buttons_single": [EVENT_BUTTON]},
                "turn_off": {"off_buttons_single": [EVENT_BUTTON]},
            },
            "button_click_conflict",
        ),
    ):
        result = submit_create(client, "remote", {**remote, **bindings})
        expect_rejection(client, result, code)
    checkpoint(
        "forms reject unusable color/brightness/fade, wide hysteresis, short "
        "timeouts, id clashes, non-schedule pickers, and bad remote bindings"
    )


def start_menu(client: HomeAssistantClient, *steps: str) -> dict[str, Any]:
    """Walk the Add-integration menus to a named step."""
    result = client.start_flow()
    expect_step(result, "user")
    for step in steps:
        result = client.continue_flow(result, {"next_step_id": step})
    return result


def expect_abort(result: dict[str, Any], reason: str) -> dict[str, str]:
    """Assert a flow finished with one abort reason; return its placeholders."""
    if result.get("type") != "abort" or result.get("reason") != reason:
        raise AssertionError(f"Expected the flow to abort with {reason!r}: {result}")
    return result.get("description_placeholders") or {}


def entry_id_by_title(client: HomeAssistantClient, title: str) -> str:
    matches = [e for e in client.molight_entries() if e.get("title") == title]
    if len(matches) != 1:
        raise AssertionError(f"Expected one entry titled {title!r}: {matches}")
    return matches[0]["entry_id"]


def remove_entry_and_entity(
    client: HomeAssistantClient, entry_id: str, entity_id: str
) -> None:
    client.remove_entry(entry_id)
    wait_entity_absent(client, entity_id)
    wait_entry_removed(client, entry_id, f"Temporary {entity_id}")


def run_discovery_scenarios(client: HomeAssistantClient) -> None:
    """Discovery offers only unwrapped sources, applies affixes, reports its count."""
    result = start_menu(client, "discover_occupancy")
    expect_step(result, "discover_occupancy")
    result = client.continue_flow(
        result, {"filter_areas": [], "filter_labels": [], "preselect_all": True}
    )
    expect_step(result, "discover_occupancy_select")
    result = client.continue_flow(
        result,
        {
            "selected_entities": [RAW_REMOVAL_MOTION],
            "affix_prefix": "v_",
            "affix_suffix": "",
            "affix_target": "entity_id",
        },
    )
    expect_step(result, "discover_occupancy_defaults")
    result = client.continue_flow(
        result,
        {
            "occupancy_timeout": 2,
            "advanced": {"false_detection_grace": 0, "clear_on_unavailable_timeout": 1},
        },
    )
    placeholders = expect_abort(result, "discovery_done")
    if placeholders.get("count") != "1":
        raise AssertionError(f"Discovery did not report one created entry: {result}")
    client.wait_state(
        "binary_sensor.v_e2e_removal_motion",
        lambda state: state["attributes"].get("friendly_name") == "E2E Removal Motion",
        "created with the prefixed entity id and the source's name",
    )
    # Every candidate is now wrapped, so a second discovery has nothing to offer.
    expect_abort(start_menu(client, "discover_occupancy"), "no_candidates")
    remove_entry_and_entity(
        client,
        entry_id_by_title(client, "E2E Removal Motion"),
        "binary_sensor.v_e2e_removal_motion",
    )

    result = start_menu(client, "discover_light")
    expect_step(result, "discover_light")
    result = client.continue_flow(
        result, {"filter_areas": [], "filter_labels": [], "preselect_all": False}
    )
    expect_step(result, "discover_light_select")
    result = client.continue_flow(
        result,
        {
            "selected_entities": [RAW_MULTI_DIMMER],
            "affix_prefix": "",
            "affix_suffix": " V",
            "affix_target": "name",
        },
    )
    expect_step(result, "discover_light_defaults")
    result = client.continue_flow(result, {"light_timeout": 30, **EMPTY_LIGHT_SECTIONS})
    placeholders = expect_abort(result, "discovery_done")
    if placeholders.get("count") != "1":
        raise AssertionError(f"Light discovery did not report one entry: {result}")
    client.wait_state(
        "light.e2e_multi_dimmer_v",
        lambda state: state["attributes"].get("friendly_name") == "E2E Multi Dimmer V",
        "created with the suffixed name and the derived entity id",
    )
    remove_entry_and_entity(
        client,
        entry_id_by_title(client, "E2E Multi Dimmer V"),
        "light.e2e_multi_dimmer_v",
    )
    checkpoint(
        "discovery wraps only unwrapped sources, applies affixes, reports its count"
    )


def run_assign_scenarios(client: HomeAssistantClient) -> None:
    """Bulk assignment wires, unwires, and skips lights per the timeout guard."""
    light_a = create_entry(
        client,
        "light",
        {
            "name": "E2E Assign A",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 30,
            **EMPTY_LIGHT_SECTIONS,
            "advanced": {"entity_id": "e2e_assign_a"},
        },
        "Assign light A",
    )
    light_b = create_entry(
        client,
        "light",
        {
            "name": "E2E Assign B",
            "lights": [RAW_MULTI_ON_OFF],
            "light_timeout": 10,
            **EMPTY_LIGHT_SECTIONS,
            "advanced": {"entity_id": "e2e_assign_b"},
        },
        "Assign light B",
    )
    slow = create_virtual_occupancy(
        client, "E2E Slow Occupancy", RAW_REMOVAL_MOTION, "e2e_slow_occupancy", 20
    )
    for entry_id in (light_a, light_b, slow):
        assert_entry_loaded(client, entry_id)

    def assign(step: str, form: dict[str, Any], lights: list[str]) -> dict[str, str]:
        result = start_menu(client, "assign_sensor", step)
        expect_step(result, step)
        result = client.continue_flow(result, form)
        expect_step(result, "assign_lights")
        result = client.continue_flow(result, {"assign_lights": lights})
        reason = (
            "assign_done_skipped"
            if result.get("reason") == "assign_done_skipped"
            else "assign_done"
        )
        return expect_abort(result, reason)

    summary = assign(
        "assign_occupancy",
        {"assign_sensor": "binary_sensor.e2e_slow_occupancy", "assign_role": "regular"},
        ["light.e2e_assign_a", "light.e2e_assign_b"],
    )
    if summary.get("assigned") != "1" or "E2E Assign B" not in summary.get(
        "skipped", ""
    ):
        raise AssertionError(f"Occupancy assignment summary unexpected: {summary}")
    wait_stored_entry(
        light_a,
        lambda cfg: cfg.get("occupancy_entity") == "binary_sensor.e2e_slow_occupancy",
        "wired to the slow occupancy sensor",
    )
    wait_stored_entry(
        light_b,
        lambda cfg: not cfg.get("occupancy_entity"),
        "left unwired (timeout guard)",
    )
    summary = assign(
        "assign_occupancy",
        {"assign_sensor": "binary_sensor.e2e_slow_occupancy", "assign_role": "regular"},
        [],
    )
    if summary.get("removed") != "1" or summary.get("assigned") != "0":
        raise AssertionError(f"Occupancy unassignment summary unexpected: {summary}")
    wait_stored_entry(light_a, lambda cfg: not cfg.get("occupancy_entity"), "unwired")

    summary = assign(
        "assign_illuminance",
        {"assign_sensor": VIRTUAL_ILLUMINANCE, "illuminance_mode": "gate"},
        ["light.e2e_assign_a"],
    )
    if summary.get("assigned") != "1":
        raise AssertionError(f"Illuminance assignment summary unexpected: {summary}")
    wait_stored_entry(
        light_a,
        lambda cfg: (
            cfg.get("illuminance_entity") == VIRTUAL_ILLUMINANCE
            and cfg.get("illuminance_mode") == "gate"
        ),
        "wired to the illuminance sensor in gate mode",
    )
    summary = assign(
        "assign_schedule",
        {"assign_sensor": VIRTUAL_SCHEDULE, "schedule_mode": "gate_keep"},
        ["light.e2e_assign_a"],
    )
    if summary.get("assigned") != "1":
        raise AssertionError(f"Schedule assignment summary unexpected: {summary}")
    wait_stored_entry(
        light_a,
        lambda cfg: (
            cfg.get("schedule_entity") == VIRTUAL_SCHEDULE
            and cfg.get("schedule_mode") == "gate_keep"
        ),
        "wired to the schedule in gate_keep mode",
    )
    for entry_id, entity_id in (
        (light_a, "light.e2e_assign_a"),
        (light_b, "light.e2e_assign_b"),
        (slow, "binary_sensor.e2e_slow_occupancy"),
    ):
        remove_entry_and_entity(client, entry_id, entity_id)
    checkpoint("bulk assignment wires, unwires, skips by the timeout guard, sets modes")


def set_hold(client: HomeAssistantClient, on: bool) -> None:
    """Drive the keep-on entity (the otherwise idle removal-motion sensor)."""
    state = "on" if on else "off"
    client.set_state(RAW_REMOVAL_MOTION, state)
    client.wait_state(
        HOLD_LIGHT,
        lambda current: current["attributes"].get("auto_off_held") is on,
        f"{'held' if on else 'released'} by the keep-on entity",
    )


def run_hold_entity_scenarios(client: HomeAssistantClient) -> None:
    """A keep-on entity suspends automatic offs; release re-evaluates the rules."""
    entry_id = create_entry(
        client,
        "light",
        {
            "name": "E2E Hold",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 4,
            **EMPTY_LIGHT_SECTIONS,
            "sensors": {
                "occupancy_entity": VIRTUAL_TIMER_OCCUPANCY,
                "hold_entities": [RAW_REMOVAL_MOTION],
            },
            "behavior": {"auto_on_brightness": 60},
            "advanced": {"entity_id": "e2e_hold"},
        },
        "Hold light",
    )
    assert_entry_loaded(client, entry_id)
    client.wait_state(HOLD_LIGHT, lambda state: state["state"] == "off", "off")

    # Held: turn-ons still work, the countdown never arms, manual off still works.
    set_hold(client, True)
    set_timer_motion(client, True)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on")
    wait_machine_state(client, "occupied", HOLD_LIGHT)
    set_timer_motion(client, False)
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on past the timeout while the keep-on entity holds auto-off",
        duration=6,
    )
    client.call_service("light", "turn_off", {"entity_id": HOLD_LIGHT})
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")
    wait_machine_state(client, "idle", HOLD_LIGHT)

    # Release with nobody there: a fresh full timer starts, then the light goes off.
    set_timer_motion(client, True)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on")
    set_timer_motion(client, False)
    assert_state_stays(
        client, RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on", duration=5
    )
    set_hold(client, False)
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on: the release started a fresh full timer rather than turning off at once",
        duration=2.5,
    )
    client.wait_state(
        RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off", timeout=10
    )
    wait_machine_state(client, "idle", HOLD_LIGHT)

    # Release while occupied: active occupancy keeps the light on.
    set_hold(client, True)
    set_timer_motion(client, True)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on")
    set_hold(client, False)
    assert_state_stays(
        client,
        HOLD_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("molight_state") == "occupied"
        ),
        "occupied after the release while occupancy is active",
        duration=5,
    )
    set_timer_motion(client, False)
    wait_machine_state(client, "countdown", HOLD_LIGHT)
    client.wait_state(
        RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off", timeout=10
    )

    # A keep-on entity dropping to unavailable holds its last known value.
    set_hold(client, True)
    set_timer_motion(client, True)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on")
    set_timer_motion(client, False)
    client.set_available(RAW_REMOVAL_MOTION, False)
    client.wait_state(
        RAW_REMOVAL_MOTION, lambda state: state["state"] == "unavailable", "unavailable"
    )
    assert_state_stays(
        client,
        HOLD_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("auto_off_held") is True
        ),
        "held while the keep-on entity is unavailable",
        duration=5,
    )
    client.set_available(RAW_REMOVAL_MOTION, True)
    client.wait_state(RAW_REMOVAL_MOTION, lambda state: state["state"] == "on", "on")
    set_hold(client, False)
    client.wait_state(
        RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off", timeout=10
    )
    wait_machine_state(client, "idle", HOLD_LIGHT)
    remove_entry_and_entity(client, entry_id, HOLD_LIGHT)
    print(
        "PASS: keep-on entity holds auto-off, survives a blip, and release re-evaluates"
    )


def run_illuminance_and_door_scenarios(client: HomeAssistantClient) -> None:
    """Exercise profile-specific illuminance and door behavior live."""
    reset_trigger(client)
    wait_profile(client, PROFILE_OUTSIDE)

    # Outside profile: gate-mode illuminance suppresses the initial occupancy
    # activation and going dark admits it, but returning bright does not force
    # an already-on light off.
    client.set_state(RAW_ILLUMINANCE, 50)
    client.wait_state(
        VIRTUAL_ILLUMINANCE, lambda state: state["state"] == "on", "bright"
    )
    client.set_state(RAW_MOTION, "on")
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "on", "on")
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "off",
        "off while bright occupancy is gated",
    )
    client.set_state(RAW_ILLUMINANCE, 5)
    client.wait_state(
        VIRTUAL_ILLUMINANCE, lambda state: state["state"] == "off", "dark"
    )
    client.wait_state(
        RAW_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(30)
        ),
        "on at the outside-profile brightness after becoming dark",
    )
    wait_machine_state(client, "occupied")
    client.set_state(RAW_ILLUMINANCE, 50)
    client.wait_state(
        VIRTUAL_ILLUMINANCE, lambda state: state["state"] == "on", "bright"
    )
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "on",
        "on under the outside profile's gate-only illuminance mode",
    )

    reset_trigger(client)
    client.set_state(RAW_DOOR, "on")
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "on", "door-lit")
    wait_machine_state(client, "active")
    assert_state_stays(
        client,
        VIRTUAL_LIGHT,
        lambda state: state["attributes"].get("molight_state") == "active",
        "timer-driven while the outside-profile door remains open",
    )
    reset_trigger(client)

    # Inside profile: control-mode illuminance forces an occupied light off
    # when it becomes bright, and open_close holds until closing starts the
    # countdown. These contrasts prove the profile settings switch together.
    client.set_state(RAW_SCHEDULE, "on")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    wait_profile(client, PROFILE_INSIDE)
    client.set_state(RAW_ILLUMINANCE, 50)
    client.wait_state(
        VIRTUAL_ILLUMINANCE, lambda state: state["state"] == "on", "bright"
    )
    client.set_state(RAW_MOTION, "on")
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "on", "on")
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "off",
        "off while inside-profile occupancy is bright",
    )
    client.set_state(RAW_ILLUMINANCE, 5)
    client.wait_state(
        VIRTUAL_ILLUMINANCE, lambda state: state["state"] == "off", "dark"
    )
    client.wait_state(
        RAW_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(80)
        ),
        "on at the inside-profile brightness after becoming dark",
    )
    wait_machine_state(client, "occupied")
    client.set_state(RAW_ILLUMINANCE, 50)
    client.wait_state(
        VIRTUAL_ILLUMINANCE, lambda state: state["state"] == "on", "bright"
    )
    client.wait_state(
        RAW_LIGHT,
        lambda state: state["state"] == "off",
        "forced off by inside-profile control illuminance",
    )

    reset_trigger(client)
    client.set_state(RAW_DOOR, "on")
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "on", "door-lit")
    wait_machine_state(client, "occupied")
    assert_state_stays(
        client,
        VIRTUAL_LIGHT,
        lambda state: state["attributes"].get("molight_state") == "occupied",
        "held while the inside-profile door remains open",
    )
    client.set_state(RAW_DOOR, "off")
    wait_machine_state(client, "countdown")
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "on", "counting down")
    reset_trigger(client)

    client.set_state(RAW_SCHEDULE, "off")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "off", "off")
    wait_profile(client, PROFILE_OUTSIDE)


def run_sensor_blip_scenarios(client: HomeAssistantClient) -> None:
    """A door or lux source blipping unavailable and back unchanged is no event."""
    reset_trigger(client)
    client.set_state(RAW_SCHEDULE, "on")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    wait_profile(client, PROFILE_INSIDE)  # inside: door_mode open_close, control
    client.set_state(RAW_DOOR, "on")
    held = client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("last_on_door") is not None
        ),
        "on and held by the open door",
    )
    last_on_door = held["attributes"]["last_on_door"]
    last_on_illuminance = held["attributes"].get("last_on_illuminance")
    machine_state = held["attributes"]["molight_state"]

    client.set_available(RAW_DOOR, False)
    client.wait_state(
        RAW_DOOR, lambda state: state["state"] == "unavailable", "unavailable"
    )
    client.set_available(RAW_DOOR, True)
    client.wait_state(RAW_DOOR, lambda state: state["state"] == "on", "recovered open")
    assert_state_stays(
        client,
        VIRTUAL_LIGHT,
        lambda state: (
            state["attributes"].get("last_on_door") == last_on_door
            and state["attributes"].get("molight_state") == machine_state
        ),
        "unchanged: the door blip was not read as a fresh opening",
    )

    client.set_available(RAW_ILLUMINANCE, False)
    client.wait_state(
        RAW_ILLUMINANCE, lambda state: state["state"] == "unavailable", "unavailable"
    )
    assert_state_stays(
        client,
        VIRTUAL_ILLUMINANCE,
        lambda state: state["state"] == "off",
        "dark: the illuminance sensor holds its last reading while its source is out",
    )
    client.set_available(RAW_ILLUMINANCE, True)
    client.wait_state(
        RAW_ILLUMINANCE, lambda state: state["state"] == "5.0", "recovered"
    )
    assert_state_stays(
        client,
        VIRTUAL_LIGHT,
        lambda state: (
            state["attributes"].get("last_on_illuminance") == last_on_illuminance
            and state["attributes"].get("molight_state") == machine_state
        ),
        "unchanged: the lux blip was not read as a fresh dark edge",
    )

    client.set_state(RAW_DOOR, "off")
    reset_trigger(client)
    client.set_state(RAW_SCHEDULE, "off")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "off", "off")
    wait_profile(client, PROFILE_OUTSIDE)
    checkpoint("door and lux blips to unavailable and back are not read as changes")


def wait_timer_stage(
    client: HomeAssistantClient, stage: str, target_brightness: int
) -> None:
    """Observe one warning stage on both the virtual and physical lights."""
    client.wait_state(
        VIRTUAL_TIMER_LIGHT,
        lambda state: (
            state["attributes"].get("molight_state") == stage
            and state["attributes"].get("warning_active") is True
            and state["attributes"].get("pre_warn_brightness") == pct(60)
        ),
        f"in the {stage} stage with its pre-warning snapshot",
    )
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("brightness") == target_brightness
        ),
        f"showing the {stage} brightness {target_brightness}",
    )


def run_timeout_warning_scenario(
    client: HomeAssistantClient,
    timer_entry_id: str,
    bulb: str = "instant bulb",
    light_timeout: int = 4,
) -> None:
    """Run one real countdown/effect/warn/off sequence and cancel another."""
    client.call_service("light", "turn_off", {"entity_id": VIRTUAL_TIMER_LIGHT})
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")

    set_timer_motion(client, True)
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(60)
        ),
        "on at the configured automatic brightness",
    )
    set_timer_motion(client, False)
    wait_machine_state(client, "countdown", VIRTUAL_TIMER_LIGHT)
    wait_timer_stage(client, "effect", pct(10))
    wait_timer_stage(client, "warn", pct(20))
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")
    final = wait_machine_state(client, "idle", VIRTUAL_TIMER_LIGHT)
    if final["attributes"].get("warning_active") is not False:
        raise AssertionError(f"Warning flag remained set after final turn-off: {final}")

    set_timer_motion(client, True)
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(60)
        ),
        "retrigger test initially on",
    )
    set_timer_motion(client, False)
    wait_timer_stage(client, "effect", pct(10))
    set_timer_motion(client, True)
    restored = wait_machine_state(client, "occupied", VIRTUAL_TIMER_LIGHT)
    if (
        restored["attributes"].get("warning_active") is not False
        or restored["attributes"].get("pre_warn_brightness") is not None
    ):
        raise AssertionError(f"Retrigger did not clear warning state: {restored}")
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(60)
        ),
        "restored to its pre-warning brightness",
    )
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(60)
        ),
        "on at restored brightness past the cancelled warning deadline",
        duration=light_timeout,
    )

    set_timer_motion(client, False)
    client.call_service("light", "turn_off", {"entity_id": VIRTUAL_TIMER_LIGHT})
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")
    client.remove_entry(timer_entry_id)
    wait_entry_removed(client, timer_entry_id, "Temporary timer-light")
    print(f"PASS: countdown, warning stages, final off, and retrigger cancel ({bulb})")


def create_physical_light(client: HomeAssistantClient) -> str:
    """Create a light whose RGB member is driven like a wall switch."""
    return create_entry(
        client,
        "light",
        {
            "name": "E2E Physical",
            "lights": [RAW_MULTI_RGB],
            "light_timeout": 10,
            "sensors": {},
            "behavior": {"auto_on_brightness": 60, "auto_off_transition": 1},
            "warning": {
                "effect_timeout": 0,
                "warn_timeout": 8,
                "warn_brightness": 20,
                "warn_rgb_color": [255, 0, 0],
            },
            "advanced": {"entity_id": "e2e_physical"},
        },
        "Physical light",
    )


def run_physical_change_scenarios(client: HomeAssistantClient) -> None:
    """Prove wall-switch changes to the real light drive the state machine."""
    entry_id = create_physical_light(client)
    assert_entry_loaded(client, entry_id)
    client.wait_state(PHYSICAL_LIGHT, lambda state: state["state"] == "off", "off")

    client.set_state(
        RAW_MULTI_RGB, "on", {"brightness": pct(60), "rgb_color": [0, 0, 255]}
    )
    started = wait_machine_state(client, "active", PHYSICAL_LIGHT)
    if (
        started["attributes"].get("last_on_physical") is None
        or started["attributes"].get("last_on_virtual") is not None
    ):
        raise AssertionError(
            f"Physical turn-on was not attributed to the wall: {started}"
        )

    # A physical dim two seconds in restarts the ten-second timer.
    assert_state_stays(
        client, RAW_MULTI_RGB, lambda state: state["state"] == "on", "on", duration=2
    )
    client.set_state(RAW_MULTI_RGB, "on", {"brightness": 100})
    client.wait_state(
        PHYSICAL_LIGHT,
        lambda state: (
            state["attributes"].get("last_brightness_change_physical") is not None
            and state["attributes"].get("last_brightness_change_virtual") is None
        ),
        "recording the physical dim",
    )
    # The warning follows the restarted 10 s timer; a physical dim during it
    # cancels the warning and restores the pre-warning color.
    client.wait_state(
        PHYSICAL_LIGHT,
        lambda state: (
            state["attributes"].get("warning_active") is True
            and state["attributes"].get("pre_warn_color") is not None
        ),
        "in its warning with the pre-warning color saved",
    )
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: (
            state["attributes"].get("brightness") == pct(20)
            and list(state["attributes"].get("rgb_color") or []) == [255, 0, 0]
        ),
        "showing the warning brightness and color",
    )
    # Outlast the original deadline, and the five seconds during which HA keeps
    # MoLight's command context on the member's state writes (a physical change
    # inside that window reads as MoLight's own echo).
    assert_state_stays(
        client,
        RAW_MULTI_RGB,
        lambda state: state["state"] == "on",
        "on past the original deadline after the physical dim restarted the timer",
        duration=6.5,
    )
    client.set_state(RAW_MULTI_RGB, "on", {"brightness": 120})
    client.wait_state(
        PHYSICAL_LIGHT,
        lambda state: (
            state["attributes"].get("warning_active") is False
            and state["attributes"].get("pre_warn_color") is None
            and state["attributes"].get("molight_state") == "active"
        ),
        "out of its warning with the timer running again",
    )
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: (
            state["attributes"].get("brightness") == 120
            and list(state["attributes"].get("rgb_color") or []) == [0, 0, 255]
        ),
        "restored to the pre-warning color at the physically dimmed brightness",
    )
    assert_state_stays(
        client,
        RAW_MULTI_RGB,
        lambda state: state["state"] == "on",
        "on after the dim restarted the timer",
        duration=3,
    )
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: (
            state["state"] == "off" and command_data(state).get("transition") == 1
        ),
        "off with the automatic-off fade",
        timeout=25,  # the restarted 10 s timer plus the 8 s warning
    )
    wait_machine_state(client, "idle", PHYSICAL_LIGHT)
    # Again outlast HA's context window after MoLight's own turn-off, or the
    # wall-switch turn-on below would read as MoLight's echo.
    assert_state_stays(
        client,
        RAW_MULTI_RGB,
        lambda state: state["state"] == "off",
        "off after the timer ran out",
        duration=5.5,
    )

    # A member outage while on is not a state change; recovery is a real event.
    client.set_state(RAW_MULTI_RGB, "on", {"brightness": pct(60)})
    wait_machine_state(client, "active", PHYSICAL_LIGHT)
    client.set_available(RAW_MULTI_RGB, False)
    client.wait_state(
        RAW_MULTI_RGB, lambda state: state["state"] == "unavailable", "unavailable"
    )
    assert_state_stays(
        client,
        PHYSICAL_LIGHT,
        lambda state: state["attributes"].get("molight_state") == "active",
        "in active state while its member is unavailable",
    )
    client.set_available(RAW_MULTI_RGB, True)
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: (
            state["state"] == "on"
            and state["attributes"]["testbed_last_command"]["service"] != "turn_off"
        ),
        "back on without having been commanded off during the outage",
    )
    client.wait_state(
        PHYSICAL_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("molight_state") == "active"
        ),
        "on and active after its member recovered",
    )

    client.call_service("light", "turn_off", {"entity_id": PHYSICAL_LIGHT})
    client.wait_state(RAW_MULTI_RGB, lambda state: state["state"] == "off", "off")
    client.remove_entry(entry_id)
    wait_entry_removed(client, entry_id, "Temporary physical-change light")
    print("PASS: physical on, dim, dim-during-warning, and member outage handled")


def run_fast_physical_scenarios(client: HomeAssistantClient) -> None:
    """Physical changes inside HA's 5 s command-context window must still count.

    Home Assistant stamps MoLight's service-call context on a member for 5 s,
    so a wall-switch change in that window arrives under MoLight's own context.
    MoLight judges such writes against what it asked for, so a contradicting
    one still counts as a real change.
    """
    entry_id = create_physical_light(client)
    assert_entry_loaded(client, entry_id)
    client.wait_state(PHYSICAL_LIGHT, lambda state: state["state"] == "off", "off")

    # Wall-switch on one second after MoLight turned the light off.
    client.set_state(RAW_MULTI_RGB, "on", {"brightness": pct(60)})
    wait_machine_state(client, "active", PHYSICAL_LIGHT)
    client.call_service("light", "turn_off", {"entity_id": PHYSICAL_LIGHT})
    client.wait_state(RAW_MULTI_RGB, lambda state: state["state"] == "off", "off")
    wait_machine_state(client, "idle", PHYSICAL_LIGHT)
    assert_state_stays(
        client, RAW_MULTI_RGB, lambda state: state["state"] == "off", "off", duration=1
    )
    client.set_state(RAW_MULTI_RGB, "on", {"brightness": pct(60)})
    client.wait_state(
        PHYSICAL_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("molight_state") == "active"
        ),
        "on and active after a wall-switch on 1 s after MoLight's own turn-off",
        timeout=10,
    )

    # Physical dim one second into the warning, while MoLight's warn command
    # context is still fresh on the member.
    client.wait_state(
        PHYSICAL_LIGHT,
        lambda state: state["attributes"].get("warning_active") is True,
        "in its warning",
    )
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: state["attributes"].get("brightness") == pct(20),
        "showing the warning brightness",
    )
    assert_state_stays(
        client,
        RAW_MULTI_RGB,
        lambda state: state["attributes"].get("brightness") == pct(20),
        "at the warning brightness",
        duration=1,
    )
    client.set_state(RAW_MULTI_RGB, "on", {"brightness": 120})
    client.wait_state(
        PHYSICAL_LIGHT,
        lambda state: (
            state["attributes"].get("warning_active") is False
            and state["attributes"].get("molight_state") == "active"
        ),
        "out of its warning after a physical dim 1 s into it",
        timeout=10,
    )

    client.call_service("light", "turn_off", {"entity_id": PHYSICAL_LIGHT})
    client.wait_state(RAW_MULTI_RGB, lambda state: state["state"] == "off", "off")
    client.remove_entry(entry_id)
    wait_entry_removed(client, entry_id, "Temporary physical-change light")
    print("PASS: physical changes inside the command-context window were honoured")


def create_end_action_light(client: HomeAssistantClient, end_action: str) -> str:
    """Create a scheduled light whose inside profile is occupied, outside inert."""
    result = start_create(client, "scheduled_light")
    expect_step(result, "scheduled_light")
    result = client.continue_flow(
        result,
        {
            "name": "E2E End Action",
            "lights": [RAW_TIMER_LIGHT],
            "schedule_entity": END_SCHEDULE,
            "schedule_end_action": end_action,
            "advanced": {"entity_id": "e2e_end_action"},
        },
    )
    expect_step(result, "scheduled_light_outside")
    result = client.continue_flow(
        result,
        {
            **EMPTY_LIGHT_SECTIONS,
            "light_timeout": 4,
            "behavior": {"auto_on_brightness": 50},
        },
    )
    expect_step(result, "scheduled_light_inside")
    result = client.continue_flow(
        result,
        {
            **EMPTY_LIGHT_SECTIONS,
            "light_timeout": 30,
            "sensors": {"occupancy_entity": VIRTUAL_TIMER_OCCUPANCY},
            "behavior": {"auto_on_brightness": 60},
        },
    )
    return finish_creation(result, f"End-action {end_action} light")


def run_schedule_end_action_scenarios(client: HomeAssistantClient) -> None:
    """Leave the schedule window with the light on under each end action."""
    schedule_entry_id = create_virtual_schedule(
        client, "E2E End Schedule", "e2e_end_schedule", source=RAW_REMOVAL_MOTION
    )
    assert_entry_loaded(client, schedule_entry_id)
    for end_action in ("turn_off", "switch", "keep"):
        entry_id = create_end_action_light(client, end_action)
        assert_entry_loaded(client, entry_id)
        client.set_state(RAW_REMOVAL_MOTION, "on")
        client.wait_state(END_SCHEDULE, lambda state: state["state"] == "on", "on")
        client.wait_state(
            END_ACTION_LIGHT,
            lambda state: state["attributes"].get("active_settings") == PROFILE_INSIDE,
            "using the inside profile",
        )
        set_timer_motion(client, True)
        client.wait_state(
            RAW_TIMER_LIGHT,
            lambda state: (
                state["state"] == "on"
                and state["attributes"].get("brightness") == pct(60)
            ),
            "on at the inside profile's brightness",
        )
        wait_machine_state(client, "occupied", END_ACTION_LIGHT)
        # Leave the window during the inside profile's 30 s countdown, so keep
        # and switch are told apart by which timer the light runs on.
        set_timer_motion(client, False)
        wait_machine_state(client, "countdown", END_ACTION_LIGHT)

        client.set_state(RAW_REMOVAL_MOTION, "off")
        client.wait_state(END_SCHEDULE, lambda state: state["state"] == "off", "off")
        client.wait_state(
            END_ACTION_LIGHT,
            lambda state: state["attributes"].get("active_settings") == PROFILE_OUTSIDE,
            "using the outside profile after the window ended",
        )
        if end_action == "turn_off":
            client.wait_state(
                RAW_TIMER_LIGHT,
                lambda state: state["state"] == "off",
                "off at the window end",
            )
            wait_machine_state(client, "idle", END_ACTION_LIGHT)
        elif end_action == "switch":
            # Recalculated against the outside profile: its 4 s timeout, not
            # the inside countdown, now decides when the light goes off.
            client.wait_state(
                RAW_TIMER_LIGHT,
                lambda state: state["state"] == "off",
                "off after the outside profile's timer",
                timeout=10,
            )
            wait_machine_state(client, "idle", END_ACTION_LIGHT)
        else:
            assert_state_stays(
                client,
                RAW_TIMER_LIGHT,
                lambda state: state["state"] == "on",
                "on: keep preserves the inside countdown past the outside timeout",
                duration=6,
            )
            wait_machine_state(client, "countdown", END_ACTION_LIGHT)

        client.call_service("light", "turn_off", {"entity_id": END_ACTION_LIGHT})
        client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")
        client.remove_entry(entry_id)
        wait_entity_absent(client, END_ACTION_LIGHT)
        wait_entry_removed(client, entry_id, f"Temporary end-action {end_action} light")
    client.remove_entry(schedule_entry_id)
    wait_entity_absent(client, END_SCHEDULE)
    wait_entry_removed(client, schedule_entry_id, "Temporary end schedule")
    print(
        "PASS: schedule end actions turn_off, switch, and keep behaved at the boundary"
    )


def create_gate_mode_light(client: HomeAssistantClient, schedule_mode: str) -> str:
    """Create a regular light gated by the dedicated end schedule."""
    return create_entry(
        client,
        "light",
        {
            "name": "E2E Gate Mode",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 12,
            **EMPTY_LIGHT_SECTIONS,
            "sensors": {
                "occupancy_entity": VIRTUAL_TIMER_OCCUPANCY,
                "schedule_entity": END_SCHEDULE,
                "schedule_mode": schedule_mode,
            },
            "behavior": {"auto_on_brightness": 60},
            "advanced": {"entity_id": "e2e_gate_mode"},
        },
        f"Gate-mode {schedule_mode} light",
    )


def set_end_schedule(client: HomeAssistantClient, on: bool) -> None:
    """Open or close the dedicated schedule window."""
    state = "on" if on else "off"
    client.set_state(RAW_REMOVAL_MOTION, state)
    client.wait_state(END_SCHEDULE, lambda current: current["state"] == state, state)


def run_follow_mode_scenario(client: HomeAssistantClient) -> None:
    """A follow-mode window owns the light; outside it the sensors are live."""
    entry_id = create_gate_mode_light(client, "follow")
    assert_entry_loaded(client, entry_id)
    set_end_schedule(client, True)
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(60)
        ),
        "on at the window start",
    )
    wait_machine_state(client, "scheduled", GATE_MODE_LIGHT)
    set_timer_motion(client, True)
    set_timer_motion(client, False)
    assert_state_stays(
        client,
        GATE_MODE_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("molight_state") == "scheduled"
        ),
        "scheduled while occupancy changes inside the window are ignored",
    )
    set_end_schedule(client, False)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")
    wait_machine_state(client, "idle", GATE_MODE_LIGHT)

    set_timer_motion(client, True)
    client.wait_state(
        RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on from motion outside"
    )
    wait_machine_state(client, "occupied", GATE_MODE_LIGHT)
    set_timer_motion(client, False)
    wait_machine_state(client, "countdown", GATE_MODE_LIGHT)
    client.call_service("light", "turn_off", {"entity_id": GATE_MODE_LIGHT})
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")
    client.remove_entry(entry_id)
    wait_entity_absent(client, GATE_MODE_LIGHT)
    wait_entry_removed(client, entry_id, "Temporary follow-mode light")


def run_gate_mode_scenario(client: HomeAssistantClient, schedule_mode: str) -> None:
    """Gate, adopt at the start, then apply one end behaviour to an on light."""
    entry_id = create_gate_mode_light(client, schedule_mode)
    assert_entry_loaded(client, entry_id)
    set_timer_motion(client, True)
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "off",
        f"off: {schedule_mode} gates occupancy outside the window",
    )
    set_end_schedule(client, True)
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on: standing presence is adopted at the window start",
    )
    wait_machine_state(client, "occupied", GATE_MODE_LIGHT)
    set_timer_motion(client, False)
    wait_machine_state(client, "countdown", GATE_MODE_LIGHT)
    # Six seconds into the 12 s countdown a manual turn-on restarts the timer,
    # so the old occupancy history and the live timer now disagree.
    assert_state_stays(
        client, RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on", duration=6
    )
    client.call_service("light", "turn_on", {"entity_id": GATE_MODE_LIGHT})
    wait_machine_state(client, "active", GATE_MODE_LIGHT)
    assert_state_stays(
        client, RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on", duration=1
    )
    set_end_schedule(client, False)
    if schedule_mode == "gate":
        client.wait_state(
            RAW_TIMER_LIGHT,
            lambda state: state["state"] == "off",
            "off at the window end",
        )
        wait_machine_state(client, "idle", GATE_MODE_LIGHT)
    elif schedule_mode == "gate_keep":
        assert_state_stays(
            client,
            RAW_TIMER_LIGHT,
            lambda state: state["state"] == "on",
            "on: gate_keep leaves the restarted timer untouched past the old history",
            duration=6,
        )
        client.call_service("light", "turn_off", {"entity_id": GATE_MODE_LIGHT})
    else:
        # gate_switch recomputes the deadline from when occupancy last saw
        # someone, which is already nearly due, not from the manual restart.
        client.wait_state(
            RAW_TIMER_LIGHT,
            lambda state: state["state"] == "off",
            "off soon: gate_switch recomputed the deadline from occupancy history",
            timeout=8,
        )
        wait_machine_state(client, "idle", GATE_MODE_LIGHT)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")
    client.remove_entry(entry_id)
    wait_entity_absent(client, GATE_MODE_LIGHT)
    wait_entry_removed(client, entry_id, f"Temporary {schedule_mode} light")


def run_schedule_mode_scenarios(client: HomeAssistantClient) -> None:
    """Exercise follow and the three gate modes on a regular light."""
    schedule_entry_id = create_virtual_schedule(
        client, "E2E End Schedule", "e2e_end_schedule", source=RAW_REMOVAL_MOTION
    )
    assert_entry_loaded(client, schedule_entry_id)
    set_end_schedule(client, False)
    run_follow_mode_scenario(client)
    for schedule_mode in ("gate", "gate_keep", "gate_switch"):
        run_gate_mode_scenario(client, schedule_mode)
    client.remove_entry(schedule_entry_id)
    wait_entity_absent(client, END_SCHEDULE)
    wait_entry_removed(client, schedule_entry_id, "Temporary end schedule")
    print("PASS: follow, gate, gate_keep, and gate_switch behaved at both window edges")


def set_trigger_motion(client: HomeAssistantClient, on: bool) -> None:
    """Drive the occupancy source wrapped by the temporary trigger sensor."""
    state = "on" if on else "off"
    client.set_state(RAW_REMOVAL_MOTION, state)
    client.wait_state(
        TRIGGER_OCCUPANCY, lambda current: current["state"] == state, state
    )


def run_combined_and_maintain_scenarios(client: HomeAssistantClient) -> None:
    """Combined trigger/maintain occupancy, a maintain-held light, and dropout."""
    trigger_entry_id = create_virtual_occupancy(
        client, "E2E Trigger Occupancy", RAW_REMOVAL_MOTION, "e2e_trigger_occupancy"
    )
    combined_entry_id = create_entry(
        client,
        "combined_occupancy",
        {
            "name": "E2E Combined",
            "trigger_sensors": [TRIGGER_OCCUPANCY],
            "maintain_sensors": [VIRTUAL_TIMER_OCCUPANCY],
            "advanced": {"entity_id": "e2e_combined"},
        },
        "Combined occupancy",
    )
    light_entry_id = create_entry(
        client,
        "light",
        {
            "name": "E2E Maintain",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 4,
            **EMPTY_LIGHT_SECTIONS,
            "sensors": {
                "occupancy_entity": TRIGGER_OCCUPANCY,
                "maintain_occupancy_entity": VIRTUAL_TIMER_OCCUPANCY,
            },
            "behavior": {"auto_on_brightness": 60},
            "advanced": {"entity_id": "e2e_maintain"},
        },
        "Maintain light",
    )
    for entry_id in (trigger_entry_id, combined_entry_id, light_entry_id):
        assert_entry_loaded(client, entry_id)

    # A maintain sensor alone neither starts occupancy nor turns the light on.
    set_timer_motion(client, True)
    assert_state_stays(
        client,
        COMBINED_OCCUPANCY,
        lambda state: state["state"] == "off",
        "off: a maintain sensor cannot start occupancy",
    )
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "off",
        "off: a maintain sensor never turns the light on",
    )

    # The trigger starts both; clearing it leaves the maintain sensor holding.
    set_trigger_motion(client, True)
    client.wait_state(COMBINED_OCCUPANCY, lambda state: state["state"] == "on", "on")
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on")
    wait_machine_state(client, "occupied", MAINTAIN_LIGHT)
    set_trigger_motion(client, False)
    assert_state_stays(
        client,
        COMBINED_OCCUPANCY,
        lambda state: state["state"] == "on",
        "on: the maintain sensor extends occupancy",
    )
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on past the timeout while the maintain sensor holds it",
        duration=5,
    )
    wait_machine_state(client, "occupied", MAINTAIN_LIGHT)

    # Both clear: the combined sensor clears and the light runs its countdown.
    set_timer_motion(client, False)
    combined = client.wait_state(
        COMBINED_OCCUPANCY, lambda state: state["state"] == "off", "off"
    )
    if (
        combined["attributes"].get("latest_occupied_time") is None
        or combined["attributes"].get("last_clear_false_detection") is not False
    ):
        raise AssertionError(f"Combined clear was not a genuine occupancy: {combined}")
    wait_machine_state(client, "countdown", MAINTAIN_LIGHT)
    client.wait_state(
        RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off", timeout=10
    )
    wait_machine_state(client, "idle", MAINTAIN_LIGHT)

    # Dropout: the only on constituent leaves the state machine.
    set_trigger_motion(client, True)
    client.wait_state(COMBINED_OCCUPANCY, lambda state: state["state"] == "on", "on")
    before = client.state(COMBINED_OCCUPANCY)["attributes"]["latest_occupied_time"]
    client.remove_entry(trigger_entry_id)
    wait_entity_absent(client, TRIGGER_OCCUPANCY)
    cleared = client.wait_state(
        COMBINED_OCCUPANCY,
        lambda state: (
            state["state"] == "off"
            and state["attributes"].get("latest_occupied_time") != before
            and state["attributes"].get("last_clear_false_detection") is False
        ),
        "cleared with latest_occupied_time advanced to the dropout",
    )
    if cleared["attributes"].get("latest_occupied_time") is None:
        raise AssertionError(f"Dropout clear lost latest_occupied_time: {cleared}")

    client.set_state(RAW_REMOVAL_MOTION, "off")
    for entry_id, entity_id in (
        (light_entry_id, MAINTAIN_LIGHT),
        (combined_entry_id, COMBINED_OCCUPANCY),
    ):
        client.remove_entry(entry_id)
        wait_entity_absent(client, entity_id)
        wait_entry_removed(client, entry_id, f"Temporary {entity_id}")
    print("PASS: combined trigger/maintain occupancy, maintain hold, and dropout clear")


def command_data(state: dict[str, Any]) -> dict[str, Any]:
    """Return the payload most recently received by one testbed light."""
    command = state["attributes"].get("testbed_last_command") or {}
    return command.get("data") or {}


def has_color_command(data: dict[str, Any]) -> bool:
    """Return whether a native or canonical color field reached a member."""
    return any(
        key in data
        for key in (
            "hs_color",
            "rgb_color",
            "rgbw_color",
            "rgbww_color",
            "xy_color",
            "color_temp_kelvin",
        )
    )


def assert_multi_light_routing(client: HomeAssistantClient) -> None:
    """Prove brightness, color, and transition reach only capable members."""
    client.call_service(
        "light",
        "turn_on",
        {
            "entity_id": VIRTUAL_MULTI_LIGHT,
            "brightness": 128,
            "hs_color": [120, 50],
            "transition": 2,
        },
    )
    client.wait_state(
        RAW_MULTI_ON_OFF,
        lambda state: (
            state["state"] == "on"
            and command_data(state).get("brightness") is None
            and not has_color_command(command_data(state))
            and command_data(state).get("transition") is None
        ),
        "on without unsupported brightness, color, or transition",
    )
    client.wait_state(
        RAW_MULTI_DIMMER,
        lambda state: (
            state["state"] == "on"
            and command_data(state).get("brightness") == 128
            and not has_color_command(command_data(state))
            and command_data(state).get("transition") is None
        ),
        "on with brightness but no unsupported color or transition",
    )
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: (
            state["state"] == "on"
            and command_data(state).get("brightness") == 128
            and list(command_data(state).get("rgb_color") or []) == [128, 255, 128]
            and command_data(state).get("transition") == 2
        ),
        "on with brightness, the converted color, and the transition",
    )

    client.call_service(
        "light", "turn_off", {"entity_id": VIRTUAL_MULTI_LIGHT, "transition": 1}
    )
    for entity_id in (RAW_MULTI_ON_OFF, RAW_MULTI_DIMMER):
        client.wait_state(
            entity_id,
            lambda state: (
                state["state"] == "off"
                and command_data(state).get("transition") is None
            ),
            "off without an unsupported transition",
        )
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: (
            state["state"] == "off" and command_data(state).get("transition") == 1
        ),
        "off with the forwarded transition",
    )


def assert_fuzzy_member_reporting(client: HomeAssistantClient) -> None:
    """Prove quantised, XY-reported member echoes are not read as human changes."""
    client.set_behavior(RAW_MULTI_RGB, brightness_levels=100, xy_color=True)
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: "xy" in state["attributes"].get("supported_color_modes", []),
        "advertising XY color",
    )
    before = client.state(VIRTUAL_MULTI_LIGHT)["attributes"]
    client.call_service(
        "light",
        "turn_on",
        {"entity_id": VIRTUAL_MULTI_LIGHT, "brightness": 100, "hs_color": [120, 50]},
    )
    member = client.wait_state(
        RAW_MULTI_RGB,
        lambda state: (
            state["state"] == "on"
            and abs(int(state["attributes"].get("brightness") or 0) - 100) <= 2
            and "xy_color" in command_data(state)
        ),
        "on near the requested brightness with the color delivered as XY",
    )
    rgb = list(member["attributes"].get("rgb_color") or [])
    if len(rgb) != 3 or any(
        abs(a - b) > 6 for a, b in zip(rgb, [128, 255, 128], strict=True)
    ):
        raise AssertionError(f"XY member did not report the requested color: {member}")
    assert_state_stays(
        client,
        VIRTUAL_MULTI_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("molight_state") == "active"
            and state["attributes"].get("last_brightness_change_physical")
            == before.get("last_brightness_change_physical")
            and state["attributes"].get("last_color_change_physical")
            == before.get("last_color_change_physical")
        ),
        "active without reading the fuzzy echo as a human change",
        duration=2,
    )
    client.call_service("light", "turn_off", {"entity_id": VIRTUAL_MULTI_LIGHT})
    wait_multi_members_off(client)
    client.set_behavior(RAW_MULTI_RGB, brightness_levels=0, xy_color=False)
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: "rgb" in state["attributes"].get("supported_color_modes", []),
        "advertising RGB again",
    )


def prepare_multi_light_restart(client: HomeAssistantClient) -> None:
    """Verify full capabilities, then persist one unavailable RGB member."""
    client.wait_state(
        VIRTUAL_MULTI_LIGHT,
        lambda state: (
            set(state["attributes"].get("supported_color_modes", [])) == {"hs"}
            and int(state["attributes"].get("supported_features", 0)) > 0
        ),
        "advertising the mixed group's color and transition capabilities",
    )
    assert_multi_light_routing(client)
    assert_fuzzy_member_reporting(client)

    client.set_available(RAW_MULTI_RGB, False)
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: state["state"] == "unavailable",
        "unavailable",
    )
    client.wait_state(
        VIRTUAL_MULTI_LIGHT,
        lambda state: (
            state["state"] == "off"
            and set(state["attributes"].get("supported_color_modes", [])) == {"hs"}
            and int(state["attributes"].get("supported_features", 0)) > 0
        ),
        "off while retaining last-known capabilities before restart",
    )


def verify_and_remove_multi_light(client: HomeAssistantClient) -> None:
    """Check cold-start capabilities, recover routing, and remove the fixture."""
    entries = client.molight_entries()
    matches = [entry for entry in entries if entry.get("title") == "E2E Multi"]
    if len(matches) != 1 or matches[0].get("state") != "loaded":
        raise AssertionError(f"Expected one loaded E2E Multi entry: {matches}")
    entry_id = matches[0]["entry_id"]

    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: state["state"] == "unavailable",
        "persisted unavailable",
    )
    client.wait_state(
        VIRTUAL_MULTI_LIGHT,
        lambda state: (
            state["state"] == "off"
            and set(state["attributes"].get("supported_color_modes", []))
            == {"brightness"}
            and int(state["attributes"].get("supported_features", 0)) == 0
        ),
        "settled off with the available members' brightness-only capabilities",
        timeout=WAIT_TIMEOUT,
    )

    client.set_available(RAW_MULTI_RGB, True)
    client.wait_state(RAW_MULTI_RGB, lambda state: state["state"] == "off", "recovered")
    client.wait_state(
        VIRTUAL_MULTI_LIGHT,
        lambda state: (
            state["state"] == "off"
            and set(state["attributes"].get("supported_color_modes", [])) == {"hs"}
            and int(state["attributes"].get("supported_features", 0)) > 0
        ),
        "advertising recovered color and transition capabilities",
    )
    assert_multi_light_routing(client)

    client.remove_entry(entry_id)
    wait_entry_removed(client, entry_id, "Temporary multi-light")
    print("PASS: mixed light capabilities, routing, restart, and recovery")


def wait_remote_action(
    client: HomeAssistantClient,
    action: str,
    click: str,
    event_type: str,
    sensor: str = REMOTE_LAST_ACTION,
) -> dict[str, Any]:
    """Wait for the remote's diagnostic sensor to record one binding."""
    return client.wait_state(
        sensor,
        lambda state: (
            state["state"] == action
            and state["attributes"].get("button") == EVENT_BUTTON
            and state["attributes"].get("click") == click
            and state["attributes"].get("event_type") == event_type
        ),
        f"recording {action} from a {click} click",
    )


def wait_multi_members_off(client: HomeAssistantClient) -> None:
    """Wait for the remote target and its available members to report off."""
    client.wait_state(VIRTUAL_MULTI_LIGHT, lambda state: state["state"] == "off", "off")
    for entity_id in (RAW_MULTI_ON_OFF, RAW_MULTI_DIMMER, RAW_MULTI_RGB):
        state = client.state(entity_id)
        if state["state"] != "unavailable":
            client.wait_state(entity_id, lambda item: item["state"] == "off", "off")


def turn_off_multi_members(client: HomeAssistantClient) -> None:
    """Return the temporary remote target and its available members to off."""
    client.call_service("light", "turn_off", {"entity_id": VIRTUAL_MULTI_LIGHT})
    wait_multi_members_off(client)


def create_preset_remote(
    client: HomeAssistantClient, bindings: dict[str, dict[str, Any]]
) -> str:
    """Create a temporary remote on the multi light with the given bindings."""
    return create_entry(
        client,
        "remote",
        {
            "name": "E2E Preset Remote",
            "target_lights": [VIRTUAL_MULTI_LIGHT],
            "dim_step": 20,
            **EMPTY_REMOTE_SECTIONS,
            **bindings,
        },
        "Preset remote",
    )


def wait_multi_brightness(client: HomeAssistantClient, brightness: int) -> None:
    """Wait for the multi light and its dimmer member to show one brightness."""
    for entity_id in (VIRTUAL_MULTI_LIGHT, RAW_MULTI_DIMMER):
        client.wait_state(
            entity_id,
            lambda state: (
                state["state"] == "on"
                and state["attributes"].get("brightness") == brightness
            ),
            f"on at brightness {brightness}",
        )


def remove_preset_remote(client: HomeAssistantClient, entry_id: str) -> None:
    client.remove_entry(entry_id)
    wait_entity_absent(client, PRESET_REMOTE_LAST_ACTION)
    wait_entry_removed(client, entry_id, "Temporary preset remote")


def run_remote_step_and_preset_scenarios(client: HomeAssistantClient) -> None:
    """Brightness steps by the configured percent; presets apply their values."""
    entry_id = create_preset_remote(
        client,
        {
            "brightness_down": {"brightness_down_buttons_single": [EVENT_BUTTON]},
            "preset_1": {
                "preset_1_buttons_double": [EVENT_BUTTON],
                "preset_1_brightness": 40,
                "preset_1_rgb_color": [0, 0, 255],
            },
        },
    )
    assert_entry_loaded(client, entry_id)
    turn_off_multi_members(client)
    client.call_service(
        "light", "turn_on", {"entity_id": VIRTUAL_MULTI_LIGHT, "brightness": 128}
    )
    wait_multi_brightness(client, 128)
    # HA steps in whole percent of the current level: 50 % -> 30 % -> 10 % -> off.
    for expected in (pct(30), pct(10)):
        client.fire_event(EVENT_BUTTON, "short_release")
        wait_remote_action(
            client,
            "brightness_down",
            "single",
            "short_release",
            PRESET_REMOTE_LAST_ACTION,
        )
        wait_multi_brightness(client, expected)
    client.fire_event(EVENT_BUTTON, "short_release")
    wait_multi_members_off(client)  # stepping below the minimum turns it off

    client.fire_event(EVENT_BUTTON, "multi_press_2")
    wait_remote_action(
        client, "preset_1", "double", "multi_press_2", PRESET_REMOTE_LAST_ACTION
    )
    wait_multi_brightness(client, pct(40))
    client.wait_state(
        RAW_MULTI_RGB,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("brightness") == pct(40)
            and list(state["attributes"].get("rgb_color") or []) == [0, 0, 255]
        ),
        "on at the preset brightness and RGB color",
    )
    turn_off_multi_members(client)
    remove_preset_remote(client, entry_id)

    entry_id = create_preset_remote(
        client,
        {
            "preset_2": {
                "preset_2_buttons_single": [EVENT_BUTTON],
                "preset_2_brightness": 70,
                "preset_2_color_temp": 3000,
            },
            "brightness_up": {"brightness_up_buttons_double": [EVENT_BUTTON]},
        },
    )
    assert_entry_loaded(client, entry_id)
    client.fire_event(EVENT_BUTTON, "short_release")
    wait_remote_action(
        client, "preset_2", "single", "short_release", PRESET_REMOTE_LAST_ACTION
    )
    wait_multi_brightness(client, pct(70))
    member = client.wait_state(
        RAW_MULTI_RGB,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("brightness") == pct(70)
            and state["attributes"].get("rgb_color") is not None
        ),
        "on at the preset brightness with a converted color temperature",
    )
    red, _green, blue = member["attributes"]["rgb_color"]
    if not red > blue:
        raise AssertionError(f"3000 K preset did not read as a warm color: {member}")
    client.fire_event(EVENT_BUTTON, "multi_press_2")
    wait_remote_action(
        client, "brightness_up", "double", "multi_press_2", PRESET_REMOTE_LAST_ACTION
    )
    wait_multi_brightness(client, pct(90))
    turn_off_multi_members(client)
    client.fire_event(EVENT_BUTTON, "multi_press_2")
    wait_multi_brightness(client, 51)  # stepping up from off turns on one step dim
    turn_off_multi_members(client)
    remove_preset_remote(client, entry_id)
    print(
        "PASS: remote brightness steps are exact and presets apply brightness and color"
    )


def exercise_created_remote(client: HomeAssistantClient) -> None:
    """Exercise the remote's initial on-single and off-double bindings."""
    turn_off_multi_members(client)
    client.fire_event(EVENT_BUTTON, "short_release")
    wait_remote_action(client, "turn_on", "single", "short_release")
    client.wait_state(VIRTUAL_MULTI_LIGHT, lambda state: state["state"] == "on", "on")

    client.fire_event(EVENT_BUTTON, "multi_press_2")
    wait_remote_action(client, "turn_off", "double", "multi_press_2")
    wait_multi_members_off(client)


def exercise_edited_remote(client: HomeAssistantClient) -> None:
    """Exercise edited brightness-single and toggle-double bindings."""
    turn_off_multi_members(client)
    client.fire_event(EVENT_BUTTON, "short_release")
    wait_remote_action(client, "brightness_up", "single", "short_release")
    client.wait_state(
        VIRTUAL_MULTI_LIGHT,
        lambda state: (
            state["state"] == "on"
            and 1 <= int(state["attributes"].get("brightness", 0)) <= 52
        ),
        "on at one 20-percent brightness step",
    )
    client.wait_state(
        RAW_MULTI_DIMMER,
        lambda state: (
            state["state"] == "on"
            and 1 <= int(state["attributes"].get("brightness", 0)) <= 52
        ),
        "receiving the remote brightness step",
    )

    client.fire_event(EVENT_BUTTON, "multi_press_2")
    wait_remote_action(client, "toggle", "double", "multi_press_2")
    wait_multi_members_off(client)
    client.fire_event(EVENT_BUTTON, "multi_press_2")
    client.wait_state(
        VIRTUAL_MULTI_LIGHT,
        lambda state: state["state"] == "on",
        "on again after a second toggle double click",
    )
    turn_off_multi_members(client)


def prepare_current_remote(client: HomeAssistantClient) -> str:
    """Create, exercise, edit, and persist the current-release remote."""
    entry_id = create_remote(client)
    wait_entry_loaded(client, entry_id)
    client.wait_state(REMOTE_LAST_ACTION, lambda _state: True, "available")
    exercise_created_remote(client)

    edit_remote(client, entry_id)
    wait_entry_loaded(client, entry_id)
    client.wait_state(
        REMOTE_LAST_ACTION,
        lambda state: (
            state["state"] == "unknown"
            and state["attributes"].get("friendly_name")
            == "E2E Remote Edited Last Action"
        ),
        "reloaded with its edited name and a fresh diagnostic state",
    )
    exercise_edited_remote(client)
    print("PASS: current remote creation, editing, events, and diagnostics")
    return entry_id


def verify_current_remote_after_restart(
    client: HomeAssistantClient, entry_id: str, restart_kind: str
) -> None:
    """Verify edited bindings rebuild without replaying a stale action."""
    wait_entry_loaded(client, entry_id)
    client.wait_state(
        REMOTE_LAST_ACTION,
        lambda state: state["state"] == "unknown",
        f"starting without a stale action after {restart_kind}",
        timeout=WAIT_TIMEOUT,
    )
    assert_state_stays(
        client,
        VIRTUAL_MULTI_LIGHT,
        lambda state: state["state"] == "off",
        f"off without a replayed button event after {restart_kind}",
    )
    exercise_edited_remote(client)


def finish_current_remote_target_cleanup(
    client: HomeAssistantClient, entry_id: str
) -> None:
    """Prove target removal makes the surviving remote inert, then remove it."""
    wait_entry_loaded(client, entry_id)
    wait_entity_absent(client, VIRTUAL_MULTI_LIGHT)
    client.wait_state(
        REMOTE_LAST_ACTION,
        lambda state: state["state"] == "unknown",
        "reloaded without its removed target",
    )

    client.fire_event(EVENT_BUTTON, "short_release")
    assert_state_stays(
        client,
        REMOTE_LAST_ACTION,
        lambda state: state["state"] == "unknown",
        "inert after its target is removed",
    )
    for entity_id in (RAW_MULTI_ON_OFF, RAW_MULTI_DIMMER, RAW_MULTI_RGB):
        assert_state_stays(
            client,
            entity_id,
            lambda state: state["state"] == "off",
            "off after an event from the targetless remote",
            duration=0.5,
        )

    client.remove_entry(entry_id)
    wait_entity_absent(client, REMOTE_LAST_ACTION)
    wait_entry_removed(client, entry_id, "Temporary current-release remote")
    print("PASS: remote bindings survived restarts and target cleanup was clean")


def prepare_removal_reference_cleanup(client: HomeAssistantClient) -> dict[str, str]:
    """Remove a referenced sensor and prove its surviving light reloads cleanly."""
    client.set_state(RAW_REMOVAL_MOTION, "off")
    client.call_service("light", "turn_off", {"entity_id": RAW_TIMER_LIGHT})
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")

    sensor_entry_id = create_virtual_occupancy(
        client, "E2E Removed Occupancy", RAW_REMOVAL_MOTION, "e2e_removed_occupancy"
    )
    light_entry_id = create_removal_light(client)
    wait_entry_loaded(client, sensor_entry_id)
    wait_entry_loaded(client, light_entry_id)

    client.set_state(RAW_REMOVAL_MOTION, "on")
    client.wait_state(REMOVAL_OCCUPANCY, lambda state: state["state"] == "on", "on")
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(50)
        ),
        "on through the sensor reference",
    )
    client.set_state(RAW_REMOVAL_MOTION, "off")
    client.wait_state(REMOVAL_OCCUPANCY, lambda state: state["state"] == "off", "off")
    client.call_service("light", "turn_off", {"entity_id": REMOVAL_LIGHT})
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")

    client.remove_entry(sensor_entry_id)
    wait_entity_absent(client, REMOVAL_OCCUPANCY)
    wait_entry_loaded(client, light_entry_id)
    client.wait_state(REMOVAL_LIGHT, lambda state: state["state"] == "off", "reloaded")
    wait_entry_removed(client, sensor_entry_id, "Removed sensor")

    client.set_state(RAW_REMOVAL_MOTION, "on")
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "off",
        "off after the removed sensor's source turns on",
    )

    snapshot = {
        "sensor_entry_id": sensor_entry_id,
        "light_entry_id": light_entry_id,
    }
    print("PASS: sensor removal cleaned its surviving light's live reference")
    return snapshot


def verify_removal_reference_cleanup(
    client: HomeAssistantClient, snapshot: dict[str, str], restart_kind: str
) -> None:
    """Prove a removed sensor and its reference stay gone after a restart."""
    entries = client.molight_entries()
    if any(entry["entry_id"] == snapshot["sensor_entry_id"] for entry in entries):
        raise AssertionError(
            f"Removed sensor resurrected after {restart_kind}: {entries}"
        )
    wait_entry_loaded(client, snapshot["light_entry_id"])
    wait_entity_absent(client, REMOVAL_OCCUPANCY)
    client.wait_state(
        REMOVAL_LIGHT,
        lambda state: state["state"] == "off",
        f"off after {restart_kind}",
        timeout=WAIT_TIMEOUT,
    )
    client.wait_state(
        RAW_REMOVAL_MOTION,
        lambda state: state["state"] == "on",
        f"persisted on after {restart_kind}",
    )
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "off",
        f"off without a resurrected reference after {restart_kind}",
    )
    assert_removal_storage_clean(snapshot)


def finish_removal_reference_cleanup(
    client: HomeAssistantClient, snapshot: dict[str, str]
) -> None:
    """Verify cold-start cleanup, then remove the surviving temporary light."""
    verify_removal_reference_cleanup(client, snapshot, "a full container restart")

    client.set_state(RAW_REMOVAL_MOTION, "off")
    client.set_state(RAW_REMOVAL_MOTION, "on")
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "off",
        "off after the removed source is toggled post-restart",
    )
    client.set_state(RAW_REMOVAL_MOTION, "off")

    client.remove_entry(snapshot["light_entry_id"])
    wait_entity_absent(client, REMOVAL_LIGHT)
    wait_entry_removed(
        client, snapshot["light_entry_id"], "Temporary removal-test light"
    )
    print("PASS: removed sensor and reference stayed absent across storage restart")


def assert_entry_loaded(client: HomeAssistantClient, entry_id: str) -> None:
    """Assert a particular MoLight config entry is still loaded."""
    entries = client.molight_entries()
    matches = [entry for entry in entries if entry["entry_id"] == entry_id]
    if len(matches) != 1 or matches[0].get("state") != "loaded":
        raise AssertionError(f"MoLight entry is not loaded: {matches}")


def save_fixtures(fixtures: dict[str, Any]) -> None:
    """Record the fixture entries a phase leaves behind for later phases."""
    FIXTURES_SNAPSHOT.write_text(json.dumps(fixtures, indent=2, sort_keys=True) + "\n")


def load_fixtures() -> dict[str, Any]:
    """Read the fixture entries recorded by the previous phase."""
    return json.loads(FIXTURES_SNAPSHOT.read_text())


def expect_fixtures_loaded(client: HomeAssistantClient) -> dict[str, Any]:
    """Require exactly the recorded fixture entries to be loaded."""
    fixtures = load_fixtures()
    wait_entries_loaded(client, set(fixtures["entries"].values()))
    return fixtures


def wait_entries_loaded(
    client: HomeAssistantClient, expected_ids: set[str], timeout: float = WAIT_TIMEOUT
) -> list[dict[str, Any]]:
    """Wait for startup to finish loading an exact set of MoLight entries."""
    deadline = time.monotonic() + timeout
    entries: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        entries = client.molight_entries()
        if {entry["entry_id"] for entry in entries} == expected_ids and all(
            entry.get("state") == "loaded" for entry in entries
        ):
            return entries
        time.sleep(0.2)
    raise AssertionError(
        f"MoLight entries did not load as the expected set {sorted(expected_ids)}:"
        f" {entries}"
    )


def wait_entry_loaded(
    client: HomeAssistantClient, entry_id: str, timeout: float = WAIT_TIMEOUT
) -> dict[str, Any]:
    """Wait for one config entry to finish a reload without constraining peers."""
    deadline = time.monotonic() + timeout
    matches: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        matches = [
            entry for entry in client.molight_entries() if entry["entry_id"] == entry_id
        ]
        if len(matches) == 1 and matches[0].get("state") == "loaded":
            return matches[0]
        time.sleep(0.2)
    raise AssertionError(f"MoLight entry did not finish loading: {matches}")


def wait_entity_absent(
    client: HomeAssistantClient, entity_id: str, timeout: float = 20
) -> None:
    """Wait until Home Assistant returns 404 for a removed entity."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            last = client.state(entity_id)
        except ApiError as err:
            if err.status == 404:
                return
            raise
        time.sleep(0.2)
    raise AssertionError(f"Removed entity remained in Home Assistant: {last}")


def wait_entry_removed(
    client: HomeAssistantClient, entry_id: str, description: str, timeout: float = 20
) -> None:
    """Wait until a removed config entry disappears from the live API."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(entry["entry_id"] != entry_id for entry in client.molight_entries()):
            return
        time.sleep(0.2)
    raise AssertionError(f"{description} config entry was not removed")


def stored_config_entries() -> list[dict[str, Any]]:
    """Read Home Assistant's persisted config-entry records.

    HA rewrites the file on every change, so a read can land on a missing or
    half-written file; retry briefly rather than fail on that instant.
    """
    deadline = time.monotonic() + 5
    while True:
        try:
            payload = json.loads(CONFIG_ENTRIES_STORAGE.read_text())
            return payload["data"]["entries"]
        except (FileNotFoundError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def assert_removal_storage_clean(snapshot: dict[str, str]) -> None:
    """Assert the removed entry and dependent reference are absent on disk."""
    entries = stored_config_entries()
    if any(entry["entry_id"] == snapshot["sensor_entry_id"] for entry in entries):
        raise AssertionError("Removed sensor config entry remained in storage")
    matches = [
        entry for entry in entries if entry["entry_id"] == snapshot["light_entry_id"]
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"Surviving removal-test light missing from storage: {matches}"
        )
    # HA retains the entry's immutable original data; non-empty options fully
    # replace it at runtime and are where reference cleanup is persisted.
    effective = matches[0]["options"] or matches[0]["data"]
    if REMOVAL_OCCUPANCY in json.dumps(effective, sort_keys=True):
        raise AssertionError(
            f"Removed sensor reference remained in effective storage: {effective}"
        )


def wait_stored_entry(
    entry_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    description: str,
    timeout: float = 20,
) -> dict[str, Any]:
    """Poll persisted config entries until one's effective config matches."""
    deadline = time.monotonic() + timeout
    effective: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        for entry in stored_config_entries():
            if entry["entry_id"] == entry_id:
                effective = entry["options"] or entry["data"]
        if effective is not None and predicate(effective):
            return effective
        time.sleep(0.2)
    raise AssertionError(
        f"Stored entry {entry_id} never became {description}; last={effective}"
    )


def assert_subset(actual: dict[str, Any], expected: dict[str, Any], what: str) -> None:
    """Assert every expected key is stored with the expected value."""
    mismatched = {
        key: (value, actual.get(key))
        for key, value in expected.items()
        if actual.get(key) != value
    }
    if mismatched:
        raise AssertionError(f"{what} differ (expected, stored): {mismatched}")


def assert_converted_to_regular(
    entry_id: str, schedule_entity: str, schedule_mode: str, settings: dict[str, Any]
) -> None:
    """Assert a demoted entry stores the inside profile as a gated light."""
    effective = wait_stored_entry(
        entry_id, lambda cfg: cfg.get("entity_type") == "light", "a regular light"
    )
    assert_subset(
        effective,
        {
            **settings,
            "schedule_entity": schedule_entity,
            "schedule_mode": schedule_mode,
        },
        "Demoted light settings",
    )
    leftovers = [
        key
        for key in ("outside_schedule_settings", "inside_schedule_settings")
        if key in effective
    ]
    if leftovers:
        raise AssertionError(f"Demoted light kept profile sections: {leftovers}")


def assert_converted_to_scheduled(
    entry_id: str, schedule_entity: str, end_action: str, settings: dict[str, Any]
) -> None:
    """Assert a promoted entry keeps old settings inside and strips inputs outside."""
    effective = wait_stored_entry(
        entry_id,
        lambda cfg: cfg.get("entity_type") == "scheduled_light",
        "a scheduled light",
    )
    assert_subset(
        effective,
        {"schedule_entity": schedule_entity, "schedule_end_action": end_action},
        "Promoted light schedule settings",
    )
    assert_subset(
        effective.get("inside_schedule_settings", {}),
        settings,
        "Promoted inside profile",
    )
    outside = effective.get("outside_schedule_settings", {})
    retained = {
        key: value
        for key, value in settings.items()
        if key not in CONVERSION_INPUT_KEYS
    }
    assert_subset(outside, retained, "Promoted outside profile")
    inputs = [key for key in CONVERSION_INPUT_KEYS if key in outside]
    if inputs:
        raise AssertionError(
            f"Promoted outside profile kept automatic inputs: {inputs}"
        )


def assert_state_stays(
    client: HomeAssistantClient,
    entity_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    description: str,
    duration: float = 1.5,
) -> None:
    """Assert an entity continuously avoids an unwanted delayed transition."""
    deadline = time.monotonic() + duration
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            last = client.state(entity_id)
        except (ApiError, error.URLError, TimeoutError) as err:
            if not poll_tolerates(err, missing_ok=False):
                raise
            time.sleep(0.2)
            continue
        if not predicate(last):
            raise AssertionError(
                f"Expected {entity_id} to stay {description}; observed {last}"
            )
        time.sleep(0.2)


def finish_creation(result: dict[str, Any], description: str) -> str:
    """Return a newly-created entry id or fail with the whole flow result."""
    if result.get("type") != "create_entry":
        raise AssertionError(f"{description} creation failed: {result}")
    return result["result"]["entry_id"]


def create_upgrade_occupancy(client: HomeAssistantClient) -> str:
    """Create a representative v1.5.0 occupancy entry."""
    return create_entry(
        client,
        "occupancy",
        {
            "name": "Upgrade Occupancy",
            "occupancy_sensor": RAW_MOTION,
            "occupancy_timeout": 2,
            "advanced": {
                "false_detection_grace": 0,
                "clear_on_unavailable_timeout": 1,
                "entity_id": "upgrade_occupancy",
            },
        },
        "Upgrade occupancy",
    )


def create_upgrade_combined(client: HomeAssistantClient) -> str:
    """Create a v1.5.0 combined occupancy entry referencing another entry."""
    return create_entry(
        client,
        "combined_occupancy",
        {
            "name": "Upgrade Combined",
            "trigger_sensors": [UPGRADE_OCCUPANCY],
            "advanced": {"entity_id": "upgrade_combined"},
        },
        "Upgrade combined occupancy",
    )


def create_upgrade_illuminance(client: HomeAssistantClient) -> str:
    """Create a v1.5.0 illuminance threshold entry."""
    return create_entry(
        client,
        "illuminance",
        {
            "name": "Upgrade Illuminance",
            "illuminance_sensor": RAW_ILLUMINANCE,
            "illuminance_threshold": 10,
            "illuminance_hysteresis": 1,
            "advanced": {"entity_id": "upgrade_illuminance"},
        },
        "Upgrade illuminance",
    )


def create_upgrade_schedule(client: HomeAssistantClient) -> str:
    """Create the time-window schedule format supported by v1.5.0."""
    return create_entry(
        client,
        "schedule",
        {
            "name": "Upgrade Schedule",
            "start": {"time": "00:00:00"},
            "end": {"time": "23:59:59"},
            "advanced": {"entity_id": "upgrade_schedule"},
        },
        "Upgrade schedule",
    )


def upgrade_light_payload(name: str, brightness: int) -> dict[str, Any]:
    """Build the sectioned light form shared by v1.5.0 create and edit."""
    return {
        "name": name,
        "lights": [RAW_LIGHT],
        "light_timeout": 10,
        "sensors": {
            "occupancy_entity": UPGRADE_COMBINED,
            "illuminance_entity": UPGRADE_ILLUMINANCE,
            "illuminance_mode": "gate",
            "schedule_entity": UPGRADE_SCHEDULE,
            "schedule_mode": "gate",
            "door_entity": RAW_DOOR,
            "door_mode": "open_close",
        },
        "behavior": {
            "false_detection_off_delay": 0,
            "auto_on_brightness": brightness,
        },
        "warning": {},
    }


UPGRADE_LIGHT_SETTINGS = flat_light_settings(upgrade_light_payload("", 60))


def create_and_edit_upgrade_light(client: HomeAssistantClient) -> str:
    """Create a gated v1.5.0 light and store representative options."""
    payload = upgrade_light_payload("Upgrade Gated", 40)
    payload["advanced"] = {"entity_id": "upgrade_gated"}
    entry_id = create_entry(client, "light", payload, "Upgrade gated light")

    result = client.start_flow(options_entry_id=entry_id)
    expect_step(result, "light")
    result = client.continue_flow(
        result,
        upgrade_light_payload("Upgrade Gated Edited", 60),
        options=True,
    )
    if result.get("type") != "create_entry":
        raise AssertionError(f"Upgrade light options failed: {result}")
    return entry_id


def create_upgrade_remote(client: HomeAssistantClient) -> str:
    """Create a v1.5.0 remote bound to the simulated event button."""
    return create_entry(
        client,
        "remote",
        {
            "name": "Upgrade Remote",
            "target_lights": [UPGRADE_LIGHT],
            "dim_step": 15,
            "turn_on": {"on_buttons_single": [EVENT_BUTTON]},
            "turn_off": {},
            "toggle": {},
            "brightness_up": {},
            "brightness_down": {},
            "preset_1": {},
            "preset_2": {},
        },
        "Upgrade remote",
    )


def reset_upgrade_light(client: HomeAssistantClient) -> None:
    """Return upgrade fixtures to a stable off/dark/unoccupied baseline."""
    client.set_state(RAW_MOTION, "off")
    client.set_state(RAW_DOOR, "off")
    client.set_state(RAW_ILLUMINANCE, 5)
    client.wait_state(UPGRADE_OCCUPANCY, lambda state: state["state"] == "off", "off")
    client.wait_state(UPGRADE_COMBINED, lambda state: state["state"] == "off", "off")
    client.wait_state(
        UPGRADE_ILLUMINANCE, lambda state: state["state"] == "off", "dark"
    )
    client.call_service("light", "turn_off", {"entity_id": UPGRADE_LIGHT})
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "off", "off")


def assert_upgrade_turn_on(client: HomeAssistantClient) -> None:
    """Assert that the upgraded light uses the saved 60 percent brightness."""
    client.wait_state(
        RAW_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(60)
        ),
        "on at the saved 60 percent brightness",
    )


LEGACY_LIGHT_SETTINGS = {
    "name": "Upgrade Colored On-Off",
    "lights": [RAW_MULTI_ON_OFF],
    "light_timeout": 30,
    **EMPTY_LIGHT_SECTIONS,
    "behavior": {"auto_on_brightness": 50, "auto_on_rgb_color": [255, 0, 0]},
}
LEGACY_ILLUMINANCE_SETTINGS = {
    "name": "Upgrade Wide Hysteresis",
    "illuminance_sensor": RAW_ILLUMINANCE,
    "illuminance_threshold": 10,
    "illuminance_hysteresis": 10,
}


def create_legacy_entries(client: HomeAssistantClient) -> dict[str, str]:
    """Create, on the previous release, entries the new forms would reject."""
    return {
        "legacy_light": create_entry(
            client,
            "light",
            {
                **LEGACY_LIGHT_SETTINGS,
                "advanced": {"entity_id": "upgrade_colored_on_off"},
            },
            "Upgrade colored on/off light",
        ),
        "legacy_illuminance": create_entry(
            client,
            "illuminance",
            {
                **LEGACY_ILLUMINANCE_SETTINGS,
                "advanced": {"entity_id": "upgrade_wide_hysteresis"},
            },
            "Upgrade wide-hysteresis illuminance",
        ),
    }


def verify_legacy_entries(
    client: HomeAssistantClient, entry_ids: dict[str, str]
) -> None:
    """Rejected-by-new-forms entries still load and run; their next edit is gated."""
    client.call_service("light", "turn_on", {"entity_id": UPGRADE_LEGACY_LIGHT})
    client.wait_state(
        RAW_MULTI_ON_OFF,
        lambda state: state["state"] == "on",
        "on: the legacy entry with an unusable color still drives its light",
    )
    client.call_service("light", "turn_off", {"entity_id": UPGRADE_LEGACY_LIGHT})
    client.wait_state(RAW_MULTI_ON_OFF, lambda state: state["state"] == "off", "off")
    client.wait_state(
        UPGRADE_LEGACY_ILLUMINANCE,
        lambda state: state["state"] in ("on", "off"),
        "loaded despite its now-rejected hysteresis",
    )

    result = client.start_flow(options_entry_id=entry_ids["legacy_light"])
    expect_step(result, "light")
    result = client.continue_flow(result, LEGACY_LIGHT_SETTINGS, options=True)
    # An on/off light can apply neither the brightness nor the color it kept.
    expect_rejection(
        client, result, ("brightness_unsupported", "color_unsupported"), options=True
    )
    result = client.start_flow(options_entry_id=entry_ids["legacy_light"])
    result = client.continue_flow(
        result, {**LEGACY_LIGHT_SETTINGS, "behavior": {}}, options=True
    )
    if result.get("type") != "create_entry":
        raise AssertionError(f"Corrected legacy light options did not save: {result}")

    result = client.start_flow(options_entry_id=entry_ids["legacy_illuminance"])
    expect_step(result, "illuminance")
    result = client.continue_flow(result, LEGACY_ILLUMINANCE_SETTINGS, options=True)
    expect_rejection(client, result, "hysteresis_too_large", options=True)
    result = client.start_flow(options_entry_id=entry_ids["legacy_illuminance"])
    result = client.continue_flow(
        result,
        {**LEGACY_ILLUMINANCE_SETTINGS, "illuminance_hysteresis": 1},
        options=True,
    )
    if result.get("type") != "create_entry":
        raise AssertionError(
            f"Corrected legacy illuminance options did not save: {result}"
        )
    print(
        "PASS: legacy entries the new forms reject still load; edits gate until fixed"
    )


def run_upgrade_prepare() -> None:
    """Create and exercise entries while the previous release is installed."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    client.wait_state(EVENT_BUTTON, lambda _state: True, "available")

    entry_ids = {
        "occupancy": create_upgrade_occupancy(client),
        "combined": create_upgrade_combined(client),
        "illuminance": create_upgrade_illuminance(client),
        "schedule": create_upgrade_schedule(client),
        "light": create_and_edit_upgrade_light(client),
        "remote": create_upgrade_remote(client),
        **create_legacy_entries(client),
    }
    for entry_id in entry_ids.values():
        assert_entry_loaded(client, entry_id)

    client.wait_state(UPGRADE_SCHEDULE, lambda state: state["state"] == "on", "on")
    reset_upgrade_light(client)
    client.set_state(RAW_MOTION, "on")
    client.wait_state(UPGRADE_COMBINED, lambda state: state["state"] == "on", "on")
    assert_upgrade_turn_on(client)
    reset_upgrade_light(client)

    UPGRADE_SNAPSHOT.write_text(
        json.dumps({"entry_ids": entry_ids}, indent=2, sort_keys=True) + "\n"
    )
    print("PASS: previous release created, edited, and exercised upgrade fixtures")


def run_upgrade_verification() -> None:
    """Verify old entries and behavior after installing the candidate release."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    snapshot = json.loads(UPGRADE_SNAPSHOT.read_text())
    entry_ids: dict[str, str] = snapshot["entry_ids"]

    client.wait_state(RAW_LIGHT, lambda _state: True, "testbed available")
    wait_entries_loaded(client, set(entry_ids.values()))

    for entity_id in (
        UPGRADE_OCCUPANCY,
        UPGRADE_COMBINED,
        UPGRADE_ILLUMINANCE,
        UPGRADE_SCHEDULE,
        UPGRADE_LIGHT,
        UPGRADE_REMOTE_SENSOR,
    ):
        client.wait_state(
            entity_id, lambda _state: True, "present with its original id"
        )

    client.wait_state(UPGRADE_SCHEDULE, lambda state: state["state"] == "on", "on")
    reset_upgrade_light(client)

    client.set_state(RAW_MOTION, "on")
    client.wait_state(UPGRADE_COMBINED, lambda state: state["state"] == "on", "on")
    assert_upgrade_turn_on(client)
    reset_upgrade_light(client)

    client.set_state(RAW_ILLUMINANCE, 50)
    client.wait_state(
        UPGRADE_ILLUMINANCE, lambda state: state["state"] == "on", "bright"
    )
    client.set_state(RAW_MOTION, "on")
    client.wait_state(UPGRADE_COMBINED, lambda state: state["state"] == "on", "on")
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "off",
        "off while bright: the saved illuminance gate suppresses automatic turn-on",
    )
    reset_upgrade_light(client)

    client.set_state(RAW_DOOR, "on")
    assert_upgrade_turn_on(client)
    reset_upgrade_light(client)

    client.fire_event(EVENT_BUTTON, "short_release")
    assert_upgrade_turn_on(client)
    client.wait_state(
        UPGRADE_REMOTE_SENSOR,
        lambda state: state["state"] == "turn_on",
        "recording the restored turn-on binding",
    )

    convert_light(
        client,
        "convert_to_scheduled",
        "confirm_convert_to_scheduled",
        UPGRADE_LIGHT,
    )
    assert_entry_loaded(client, entry_ids["light"])
    assert_converted_to_scheduled(
        entry_ids["light"], UPGRADE_SCHEDULE, "turn_off", UPGRADE_LIGHT_SETTINGS
    )
    client.wait_state(
        UPGRADE_LIGHT,
        lambda state: "active_settings" in state["attributes"],
        "scheduled after conversion without changing its id",
    )
    convert_light(
        client,
        "convert_to_regular",
        "confirm_convert_to_regular",
        UPGRADE_LIGHT,
    )
    assert_entry_loaded(client, entry_ids["light"])
    assert_converted_to_regular(
        entry_ids["light"], UPGRADE_SCHEDULE, "gate", UPGRADE_LIGHT_SETTINGS
    )
    client.wait_state(
        UPGRADE_LIGHT,
        lambda state: "active_settings" not in state["attributes"],
        "regular after conversion round trip",
    )
    verify_legacy_entries(client, entry_ids)
    print("PASS: previous-release entries, ids, options, behavior, and conversion")


def run_primary() -> None:
    """Run creation, behavior, editing, conversion, and core-restart checks."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "off", "available")

    fixtures: dict[str, str] = {
        "schedule": create_virtual_schedule(client),
        "occupancy": create_virtual_occupancy(client),
        "timer_occupancy": create_virtual_occupancy(
            client, "E2E Timer Occupancy", RAW_TIMER_MOTION, "e2e_timer_occupancy"
        ),
        "illuminance": create_virtual_illuminance(client),
    }
    light_entry_id = create_scheduled_light(client)
    fixtures["light"] = light_entry_id
    assert_entry_loaded(client, light_entry_id)
    wait_profile(client, PROFILE_OUTSIDE)
    checkpoint("schedule, occupancy, illuminance, and scheduled-light fixtures created")
    run_inverted_schedule_scenario(client)
    run_config_flow_rejections(client)
    run_discovery_scenarios(client)
    run_assign_scenarios(client)
    run_illuminance_and_door_scenarios(client)
    checkpoint("illuminance gate/control and door open/open-close behavior per profile")
    run_sensor_blip_scenarios(client)

    reset_trigger(client)
    client.call_service(
        "select",
        "select_option",
        {"entity_id": SOURCE_SELECT, "option": "Focus"},
    )
    trigger_and_assert(client, pct(30), "Focus")

    reset_trigger(client)
    client.set_available(SOURCE_SELECT, False)
    trigger_and_assert(client, pct(30), "Cozy", source="fixed")
    reset_trigger(client)
    client.set_available(SOURCE_SELECT, True)
    checkpoint("turn-on selection follows its source and falls back when unavailable")
    client.call_service(
        "select",
        "select_option",
        {"entity_id": SOURCE_SELECT, "option": "Night"},
    )

    client.set_state(RAW_SCHEDULE, "on")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    wait_profile(client, PROFILE_INSIDE)
    trigger_and_assert(client, pct(80), "Night")
    client.set_state(RAW_SCHEDULE, "off")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "off", "off")
    wait_profile(client, PROFILE_OUTSIDE)
    checkpoint("profiles switch with the schedule source")

    edit_scheduled_light(client, light_entry_id)
    assert_entry_loaded(client, light_entry_id)
    client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: (
            state["attributes"].get("friendly_name") == "E2E Scheduled Edited"
        ),
        "renamed without changing entity id",
    )
    checkpoint("options edit renamed the light without changing its entity id")

    convert_light(client, "convert_to_regular", "confirm_convert_to_regular")
    assert_entry_loaded(client, light_entry_id)
    assert_converted_to_regular(
        light_entry_id, VIRTUAL_SCHEDULE, "gate_keep", SCHEDULED_INSIDE_SETTINGS
    )
    client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: "active_settings" not in state["attributes"],
        "a regular light after conversion",
    )
    reset_trigger(client)
    client.set_state(RAW_MOTION, "on")
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "on", "on")
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "off",
        "off outside the window: the demoted light kept its schedule gate",
    )
    reset_trigger(client)
    client.set_state(RAW_SCHEDULE, "on")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    trigger_and_assert(client, pct(80), "Night")
    reset_trigger(client)
    client.set_state(RAW_SCHEDULE, "off")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "off", "off")

    checkpoint(
        "demoted to a gate_keep light: settings stored, gated outside, lit inside"
    )

    convert_light(client, "convert_to_scheduled", "confirm_convert_to_scheduled")
    assert_entry_loaded(client, light_entry_id)
    assert_converted_to_scheduled(
        light_entry_id, VIRTUAL_SCHEDULE, "keep", SCHEDULED_INSIDE_SETTINGS
    )
    wait_profile(client, PROFILE_OUTSIDE)
    client.set_state(RAW_MOTION, "on")
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "on", "on")
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "off",
        "off: promotion left the outside profile without an occupancy input",
    )

    client.set_behavior(RAW_LIGHT, latency=0.5, report_steps=True, transition_steps=3)
    reset_trigger(client)
    client.set_state(RAW_SCHEDULE, "on")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    wait_profile(client, PROFILE_INSIDE)
    before = client.state(VIRTUAL_LIGHT)["attributes"]
    trigger_and_assert(client, pct(80), "Night")
    assert_state_stays(
        client,
        VIRTUAL_LIGHT,
        lambda state: (
            state["attributes"].get("molight_state") == "occupied"
            and state["attributes"].get("last_brightness_change_physical")
            == before.get("last_brightness_change_physical")
        ),
        "occupied without reading its own stepwise fade as a human dim",
        duration=2.5,
    )
    client.set_behavior(RAW_LIGHT, latency=0, report_steps=False, transition_steps=0)
    reset_trigger(client)
    client.set_state(RAW_SCHEDULE, "off")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "off", "off")
    checkpoint("automatic fade on a slow, stepwise-reporting bulb")

    reset_trigger(client)
    client.set_state(RAW_SCHEDULE, "on")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    wait_profile(client, PROFILE_INSIDE)
    trigger_and_assert(client, pct(80), "Night")

    checkpoint(
        "promoted back to a scheduled light: settings stored, outside profile inert"
    )

    removal_snapshot = prepare_removal_reference_cleanup(client)
    fixtures["removal_light"] = removal_snapshot["light_entry_id"]
    multi_entry_id = create_multi_light(client)
    assert_entry_loaded(client, multi_entry_id)
    fixtures["multi"] = multi_entry_id
    run_remote_step_and_preset_scenarios(client)
    remote_entry_id = prepare_current_remote(client)
    fixtures["remote"] = remote_entry_id
    prepare_multi_light_restart(client)

    client.restart_core()
    client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("active_settings") == PROFILE_INSIDE
        ),
        "restored inside the active schedule after a core restart",
        timeout=WAIT_TIMEOUT,
    )
    assert_entry_loaded(client, light_entry_id)
    assert_entry_loaded(client, multi_entry_id)
    client.wait_state(
        VIRTUAL_MULTI_LIGHT,
        lambda state: (
            state["state"] == "off"
            and set(state["attributes"].get("supported_color_modes", []))
            == {"brightness"}
        ),
        "restored with its RGB member unavailable after a core restart",
        timeout=WAIT_TIMEOUT,
    )
    verify_current_remote_after_restart(
        client, remote_entry_id, "a Home Assistant core restart"
    )
    verify_removal_reference_cleanup(
        client, removal_snapshot, "a Home Assistant core restart"
    )
    checkpoint(
        "core restart restored the scheduled light, multi light, remote, and cleanup"
    )
    save_fixtures(
        {"entries": fixtures, "removed_sensor": removal_snapshot["sensor_entry_id"]}
    )
    print("PASS: live creation, behavior, options, conversion, and core restart")


def run_browser_prepare() -> None:
    """Prepare the minimal persisted fixture needed by the browser smoke test."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    client.finish_onboarding()
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "off", "available")
    create_virtual_schedule(client)
    client.wait_state(
        VIRTUAL_SCHEDULE,
        lambda state: state["state"] in {"on", "off"},
        "available",
    )
    print("PASS: browser fixture and owner account are ready")


def run_container_restart_verification() -> None:
    """Verify persisted fixture and MoLight state after a container restart."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    fixtures = expect_fixtures_loaded(client)
    entries: dict[str, str] = fixtures["entries"]
    verify_current_remote_after_restart(
        client, entries["remote"], "a full container restart"
    )
    verify_and_remove_multi_light(client)
    finish_current_remote_target_cleanup(client, entries["remote"])
    finish_removal_reference_cleanup(
        client,
        {
            "sensor_entry_id": fixtures["removed_sensor"],
            "light_entry_id": entries["removal_light"],
        },
    )
    client.wait_state(
        RAW_SCHEDULE, lambda state: state["state"] == "on", "persisted on"
    )
    client.wait_state(RAW_MOTION, lambda state: state["state"] == "on", "persisted on")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "on", "on")
    client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("active_settings") == PROFILE_INSIDE
        ),
        "on with the inside profile",
    )
    client.wait_state(
        RAW_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(80)
        ),
        f"persisted on at brightness {pct(80)}",
    )
    removed = {"multi", "remote", "removal_light"}
    fixtures["entries"] = {
        name: entry_id for name, entry_id in entries.items() if name not in removed
    }
    wait_entries_loaded(client, set(fixtures["entries"].values()))
    save_fixtures(fixtures)
    print("PASS: state and entries survived a full container restart")


def run_unavailable_light_prepare() -> None:
    """Persist an unavailable physical light for the next cold startup."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    expect_fixtures_loaded(client)
    client.wait_state(VIRTUAL_LIGHT, lambda _state: True, "loaded")

    reset_trigger(client)
    client.set_available(RAW_LIGHT, False)
    client.wait_state(
        RAW_LIGHT, lambda state: state["state"] == "unavailable", "unavailable"
    )
    assert_state_stays(
        client,
        VIRTUAL_LIGHT,
        lambda state: state["state"] == "off",
        "off when its physical member disappears",
    )
    print("PASS: unavailable physical-light startup fixture prepared")


def run_unavailable_light_recovery() -> None:
    """Recover a late-reporting light, then prepare unavailable sensors."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    expect_fixtures_loaded(client)
    client.wait_state(
        RAW_LIGHT, lambda state: state["state"] == "unavailable", "unavailable"
    )
    client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: state["state"] == "off",
        "off after unavailable-member startup",
        timeout=WAIT_TIMEOUT,
    )
    assert_state_stays(
        client,
        VIRTUAL_LIGHT,
        lambda state: (
            state["state"] == "off"
            and "hs" not in state["attributes"].get("supported_color_modes", [])
        ),
        "off without advertising color before its member reports",
    )

    client.set_available(RAW_LIGHT, True)
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "off", "recovered off")
    client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: (
            "hs" in state["attributes"].get("supported_color_modes", [])
            and int(state["attributes"].get("supported_features", 0)) > 0
        ),
        "advertising recovered color and transition capabilities",
    )
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "off",
        "off after capability recovery",
    )

    client.set_state(RAW_MOTION, "off")
    client.set_state(RAW_SCHEDULE, "on")
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "off", "off")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    wait_profile(client, PROFILE_INSIDE)
    client.set_available(RAW_MOTION, False)
    client.set_available(RAW_SCHEDULE, False)
    client.wait_state(
        RAW_MOTION, lambda state: state["state"] == "unavailable", "unavailable"
    )
    client.wait_state(
        VIRTUAL_SCHEDULE,
        lambda state: state["state"] == "unavailable",
        "unavailable",
    )
    print("PASS: late light capabilities recovered without false activation")


def run_unavailable_sensors_motion_first() -> None:
    """Recover occupancy before schedule, then prepare the reverse order."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    expect_fixtures_loaded(client)
    client.wait_state(
        RAW_MOTION, lambda state: state["state"] == "unavailable", "unavailable"
    )
    client.wait_state(
        VIRTUAL_SCHEDULE,
        lambda state: state["state"] == "unavailable",
        "unavailable",
    )
    wait_profile(client, PROFILE_INSIDE)
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "off",
        "off during unavailable-sensor startup",
    )

    client.set_available(RAW_MOTION, True)
    client.wait_state(
        RAW_MOTION, lambda state: state["state"] == "off", "recovered off"
    )
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "off", "off")
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "off",
        "off after occupancy recovers off",
    )

    client.set_available(RAW_SCHEDULE, True)
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    wait_profile(client, PROFILE_INSIDE)
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "off",
        "off after the same schedule window recovers",
    )

    client.set_state(RAW_MOTION, "on")
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "on", "on")
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "on", "on")
    client.set_available(RAW_MOTION, False)
    client.set_available(RAW_SCHEDULE, False)
    client.set_state(RAW_MOTION, "off")
    client.set_state(RAW_SCHEDULE, "off")
    client.wait_state(
        RAW_MOTION, lambda state: state["state"] == "unavailable", "unavailable"
    )
    client.wait_state(
        VIRTUAL_SCHEDULE,
        lambda state: state["state"] == "unavailable",
        "unavailable",
    )
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "on",
        "on while both sources drop out again (no false turn-off)",
    )
    print("PASS: motion-first recovery caused no replay or false schedule boundary")


def run_unavailable_sensors_schedule_first() -> None:
    """Recover a missed schedule end before occupancy without turning off."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    expect_fixtures_loaded(client)
    client.wait_state(
        RAW_MOTION, lambda state: state["state"] == "unavailable", "unavailable"
    )
    client.wait_state(
        VIRTUAL_SCHEDULE,
        lambda state: state["state"] == "unavailable",
        "unavailable",
    )
    client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("active_settings") == PROFILE_INSIDE
        ),
        "restored on with its last known profile",
        timeout=WAIT_TIMEOUT,
    )

    client.set_available(RAW_SCHEDULE, True)
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "off", "off")
    wait_profile(client, PROFILE_OUTSIDE)
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "on",
        "on across the keep-policy schedule boundary",
    )

    client.set_available(RAW_MOTION, True)
    client.wait_state(
        RAW_MOTION, lambda state: state["state"] == "off", "recovered off"
    )
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "off", "off")
    assert_state_stays(
        client,
        RAW_LIGHT,
        lambda state: state["state"] == "on",
        "on after occupancy recovers off",
    )
    client.call_service("light", "turn_off", {"entity_id": VIRTUAL_LIGHT})
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "off", "off")
    print("PASS: schedule-first recovery preserved profile and keep-on behavior")


def run_auto_off_prepare() -> None:
    """Disable auto-off and prove it holds a cleared occupancy timeout."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    expect_fixtures_loaded(client)

    set_timer_motion(client, False)
    client.call_service("light", "turn_off", {"entity_id": RAW_TIMER_LIGHT})
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")

    entry_id = create_auto_off_light(client)
    assert_entry_loaded(client, entry_id)
    client.wait_state(VIRTUAL_AUTO_OFF_LIGHT, lambda _state: True, "loaded")
    client.wait_state(AUTO_OFF_SWITCH, lambda state: state["state"] == "on", "on")

    client.call_service("switch", "turn_off", {"entity_id": AUTO_OFF_SWITCH})
    client.wait_state(AUTO_OFF_SWITCH, lambda state: state["state"] == "off", "off")
    client.wait_state(
        VIRTUAL_AUTO_OFF_LIGHT,
        lambda state: state["attributes"].get("auto_off_held") is True,
        "holding automatic turn-offs",
    )

    set_timer_motion(client, True)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on")
    set_timer_motion(client, False)
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on past the disabled three-second auto-off timeout",
        duration=5,
    )

    AUTO_OFF_SNAPSHOT.write_text(json.dumps({"entry_id": entry_id}))
    print("PASS: disabled auto-off held the light past occupancy timeout")


def run_auto_off_restart() -> None:
    """Verify disabled state restoration, then re-enable and observe turn-off."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    snapshot: dict[str, str] = json.loads(AUTO_OFF_SNAPSHOT.read_text())

    wait_entry_loaded(client, snapshot["entry_id"])
    client.wait_state(AUTO_OFF_SWITCH, lambda state: state["state"] == "off", "off")
    client.wait_state(
        VIRTUAL_AUTO_OFF_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("auto_off_held") is True
        ),
        "on with automatic turn-off still held after restart",
        timeout=WAIT_TIMEOUT,
    )
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on while the restored switch remains disabled",
        duration=4,
    )

    client.call_service("switch", "turn_on", {"entity_id": AUTO_OFF_SWITCH})
    client.wait_state(AUTO_OFF_SWITCH, lambda state: state["state"] == "on", "on")
    client.wait_state(
        VIRTUAL_AUTO_OFF_LIGHT,
        lambda state: state["attributes"].get("auto_off_held") is False,
        "running auto-off after re-enable",
    )
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "off",
        "off after the fresh countdown",
        timeout=10,
    )
    wait_machine_state(client, "idle", VIRTUAL_AUTO_OFF_LIGHT)

    client.remove_entry(snapshot["entry_id"])
    wait_entity_absent(client, VIRTUAL_AUTO_OFF_LIGHT)
    wait_entity_absent(client, AUTO_OFF_SWITCH)
    print("PASS: auto-off switch persisted disabled and resumed countdown when enabled")


def run_restart_warning_prepare() -> None:
    """Enter a live warning stage and leave it active for a container restart."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    expect_fixtures_loaded(client)

    set_timer_motion(client, False)
    client.call_service("light", "turn_off", {"entity_id": RAW_TIMER_LIGHT})
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")

    entry_id = create_restart_warning_light(client)
    wait_entry_loaded(client, entry_id)
    client.wait_state(VIRTUAL_RESTART_WARNING_LIGHT, lambda _state: True, "loaded")

    set_timer_motion(client, True)
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(60)
        ),
        "on at the configured automatic brightness",
    )
    set_timer_motion(client, False)
    wait_machine_state(client, "countdown", VIRTUAL_RESTART_WARNING_LIGHT)
    warning = client.wait_state(
        VIRTUAL_RESTART_WARNING_LIGHT,
        lambda state: (
            state["attributes"].get("molight_state") == "warn"
            and state["attributes"].get("warning_active") is True
            and state["attributes"].get("pre_warn_brightness") == pct(60)
        ),
        "in warning with its pre-warning snapshot",
    )
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(20)
        ),
        f"showing warning brightness {pct(20)}",
    )
    if warning["state"] != "on":
        raise AssertionError(f"Warning-stage virtual light was not on: {warning}")

    RESTART_WARNING_SNAPSHOT.write_text(json.dumps({"entry_id": entry_id}))
    print("PASS: live warning stage prepared for a container restart")


def run_restart_warning_verify() -> None:
    """Verify a restarted warning safely resumes and completes without relighting."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    snapshot: dict[str, str] = json.loads(RESTART_WARNING_SNAPSHOT.read_text())

    wait_entry_loaded(client, snapshot["entry_id"])
    restored = client.wait_state(
        VIRTUAL_RESTART_WARNING_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("molight_state") == "active"
            and state["attributes"].get("warning_active") is False
            and state["attributes"].get("pre_warn_brightness") is None
        ),
        "restored active with warning state cleared",
        timeout=WAIT_TIMEOUT,
    )
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: (
            state["state"] == "on" and state["attributes"].get("brightness") == pct(60)
        ),
        "restored to its pre-warning brightness",
    )
    if restored["attributes"].get("brightness") != pct(60):
        raise AssertionError(f"Virtual brightness was not safely restored: {restored}")

    warning = client.wait_state(
        VIRTUAL_RESTART_WARNING_LIGHT,
        lambda state: (
            state["attributes"].get("molight_state") == "warn"
            and state["attributes"].get("warning_active") is True
            and state["attributes"].get("pre_warn_brightness") == pct(60)
        ),
        "running a fresh warning after the restored timeout",
        timeout=20,
    )
    if warning["state"] != "on":
        raise AssertionError(f"Restored warning unexpectedly turned off: {warning}")
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "off",
        "off after the restored timeout and warning",
        timeout=15,
    )
    final = wait_machine_state(client, "idle", VIRTUAL_RESTART_WARNING_LIGHT)
    if (
        final["state"] != "off"
        or final["attributes"].get("warning_active") is not False
        or final["attributes"].get("pre_warn_brightness") is not None
    ):
        raise AssertionError(f"Restored warning did not finish cleanly: {final}")
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "off",
        "off without relighting after the restored warning",
        duration=2.5,
    )
    assert_state_stays(
        client,
        VIRTUAL_RESTART_WARNING_LIGHT,
        lambda state: (
            state["state"] == "off"
            and state["attributes"].get("molight_state") == "idle"
        ),
        "idle after the restored warning",
        duration=2.5,
    )

    client.remove_entry(snapshot["entry_id"])
    wait_entity_absent(client, VIRTUAL_RESTART_WARNING_LIGHT)
    wait_entity_absent(client, RESTART_WARNING_SWITCH)
    print("PASS: restarted warning restored safely, completed, and did not relight")


LOG_RECORD = re.compile(
    r"^\d{4}-\d{2}-\d{2} \S+ (?P<level>[A-Z]+) \(.*?\) \[(?P<logger>[^\]]+)\] "
)
MOLIGHT_LOG = re.compile(r"molight", re.IGNORECASE)
MOLIGHT_TRACEBACK = re.compile(r"custom_components/molight")
# Known-benign warnings that mention MoLight; anything else at WARNING fails.
ALLOWED_WARNINGS = ("We found a custom integration molight",)


def log_records(content: str) -> list[tuple[str, str, str]]:
    """Group a log file into (level, first line, full record) entries."""
    records: list[tuple[str, str, list[str]]] = []
    for line in content.splitlines():
        match = LOG_RECORD.match(line)
        if match:
            records.append((match.group("level"), line, [line]))
        elif records:
            records[-1][2].append(line)
    return [(level, first, "\n".join(lines)) for level, first, lines in records]


def log_failures(content: str) -> list[str]:
    """Return the first line of every MoLight error, warning, or traceback."""
    failures: list[str] = []
    for level, first, record in log_records(content):
        if level in ("ERROR", "CRITICAL"):
            bad = MOLIGHT_LOG.search(first) or MOLIGHT_TRACEBACK.search(record)
        elif level == "WARNING":
            bad = MOLIGHT_LOG.search(first) and not any(
                allowed in first for allowed in ALLOWED_WARNINGS
            )
        else:
            bad = MOLIGHT_TRACEBACK.search(record)
        if bad:
            failures.append(first)
    return failures


def set_grace_motion(client: HomeAssistantClient, on: bool) -> None:
    """Drive the source wrapped by the false-detection occupancy sensor."""
    state = "on" if on else "off"
    client.set_state(RAW_REMOVAL_MOTION, state)
    client.wait_state(GRACE_OCCUPANCY, lambda current: current["state"] == state, state)


def run_false_detection_prepare() -> None:
    """Classify a blip as false and a real stay as genuine, then leave occupied."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    expect_fixtures_loaded(client)
    client.set_state(RAW_REMOVAL_MOTION, "off")
    sensor_entry_id = create_entry(
        client,
        "occupancy",
        {
            "name": "E2E Grace Occupancy",
            "occupancy_sensor": RAW_REMOVAL_MOTION,
            "occupancy_timeout": 2,
            "advanced": {
                "false_detection_grace": 1,
                "clear_on_unavailable_timeout": 1,
                "entity_id": "e2e_grace_occupancy",
            },
        },
        "Grace occupancy",
    )
    light_entry_id = create_entry(
        client,
        "light",
        {
            "name": "E2E Grace",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 10,
            **EMPTY_LIGHT_SECTIONS,
            "sensors": {"occupancy_entity": GRACE_OCCUPANCY},
            "behavior": {"auto_on_brightness": 60, "false_detection_off_delay": 1},
            "advanced": {"entity_id": "e2e_grace"},
        },
        "Grace light",
    )
    wait_entry_loaded(client, sensor_entry_id)
    wait_entry_loaded(client, light_entry_id)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")

    # A blip shorter than timeout + grace is a false detection: not counted as
    # presence, and the light it lit goes off after the short delay.
    set_grace_motion(client, True)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on")
    set_grace_motion(client, False)
    client.wait_state(
        GRACE_OCCUPANCY,
        lambda state: (
            state["attributes"].get("last_clear_false_detection") is True
            and state["attributes"].get("false_detection_count") == 1
            and state["attributes"].get("latest_occupied_time") is None
        ),
        "flagging the blip as a false detection without advancing occupancy",
    )
    client.wait_state(
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "off",
        "off after the false-detection delay, well before the timeout",
        timeout=4,
    )
    wait_machine_state(client, "idle", GRACE_LIGHT)

    # A stay longer than timeout + grace is genuine: the normal countdown runs.
    set_grace_motion(client, True)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on")
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on",
        duration=3.5,
    )
    set_grace_motion(client, False)
    client.wait_state(
        GRACE_OCCUPANCY,
        lambda state: (
            state["attributes"].get("last_clear_false_detection") is False
            and state["attributes"].get("false_detection_count") == 1
            and state["attributes"].get("latest_occupied_time") is not None
        ),
        "clearing a genuine stay",
    )
    wait_machine_state(client, "countdown", GRACE_LIGHT)
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on past the false-detection delay: a genuine clear gets the countdown",
        duration=4,
    )
    client.call_service("light", "turn_off", {"entity_id": GRACE_LIGHT})
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")

    # Leave the room occupied across the restart.
    set_grace_motion(client, True)
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on")
    wait_machine_state(client, "occupied", GRACE_LIGHT)
    FALSE_DETECTION_SNAPSHOT.write_text(
        json.dumps(
            {"sensor_entry_id": sensor_entry_id, "light_entry_id": light_entry_id}
        )
    )
    print("PASS: false-detection quick-off, genuine countdown, occupied for restart")


def run_false_detection_verify() -> None:
    """Occupancy in progress at boot is not a false detection after a restart."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    snapshot: dict[str, str] = json.loads(FALSE_DETECTION_SNAPSHOT.read_text())
    wait_entry_loaded(client, snapshot["sensor_entry_id"])
    wait_entry_loaded(client, snapshot["light_entry_id"])
    client.wait_state(GRACE_OCCUPANCY, lambda state: state["state"] == "on", "on")
    client.wait_state(
        GRACE_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("molight_state") == "occupied"
        ),
        "restored on and occupied",
        timeout=WAIT_TIMEOUT,
    )
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on: occupancy in progress at boot is not a false detection",
        duration=5,
    )
    set_grace_motion(client, False)
    client.wait_state(
        GRACE_OCCUPANCY,
        lambda state: state["attributes"].get("last_clear_false_detection") is False,
        "clearing without a false-detection flag",
    )
    wait_machine_state(client, "countdown", GRACE_LIGHT)
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on past the false-detection delay after the post-restart clear",
        duration=4,
    )
    client.wait_state(
        RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off", timeout=12
    )
    wait_machine_state(client, "idle", GRACE_LIGHT)
    for entry_id, entity_id in (
        (snapshot["light_entry_id"], GRACE_LIGHT),
        (snapshot["sensor_entry_id"], GRACE_OCCUPANCY),
    ):
        client.remove_entry(entry_id)
        wait_entity_absent(client, entity_id)
        wait_entry_removed(client, entry_id, f"Temporary {entity_id}")
    print("PASS: restart while occupied took the countdown, not a false-detection off")


def run_late_source_prepare() -> None:
    """Leave a room occupied and make its occupancy source load late next boot."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    expect_fixtures_loaded(client)
    client.set_state(RAW_REMOVAL_MOTION, "off")
    sensor_entry_id = create_entry(
        client,
        "occupancy",
        {
            "name": "E2E Late Occupancy",
            "occupancy_sensor": RAW_REMOVAL_MOTION,
            "occupancy_timeout": 2,
            "advanced": {
                "false_detection_grace": 1,
                "clear_on_unavailable_timeout": 1,
                "entity_id": "e2e_late_occupancy",
            },
        },
        "Late occupancy",
    )
    light_entry_id = create_entry(
        client,
        "light",
        {
            "name": "E2E Late",
            "lights": [RAW_TIMER_LIGHT],
            "light_timeout": 30,
            **EMPTY_LIGHT_SECTIONS,
            "sensors": {"occupancy_entity": LATE_OCCUPANCY},
            "behavior": {"auto_on_brightness": 60, "false_detection_off_delay": 1},
            "advanced": {"entity_id": "e2e_late"},
        },
        "Late light",
    )
    wait_entry_loaded(client, sensor_entry_id)
    wait_entry_loaded(client, light_entry_id)
    client.set_state(RAW_REMOVAL_MOTION, "on")
    client.wait_state(LATE_OCCUPANCY, lambda state: state["state"] == "on", "on")
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "on", "on")
    wait_machine_state(client, "occupied", LATE_LIGHT)
    client.set_startup_delay(RAW_REMOVAL_MOTION, 20)
    LATE_SOURCE_SNAPSHOT.write_text(
        json.dumps(
            {"sensor_entry_id": sensor_entry_id, "light_entry_id": light_entry_id}
        )
    )
    print("PASS: occupied room prepared with an occupancy source that loads late")


def run_late_source_verify() -> None:
    """A source that appears well after boot is adopted, not misread as a blip."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    snapshot: dict[str, str] = json.loads(LATE_SOURCE_SNAPSHOT.read_text())
    wait_entry_loaded(client, snapshot["sensor_entry_id"])
    wait_entry_loaded(client, snapshot["light_entry_id"])
    client.wait_state(
        LATE_LIGHT,
        lambda state: state["state"] == "on",
        "restored on",
        timeout=WAIT_TIMEOUT,
    )
    # The source is still absent (HA shows a restored-placeholder unavailable
    # state for a registered entity its platform has not provided yet); the
    # light rides its own timer meanwhile.
    try:
        before = client.state(RAW_REMOVAL_MOTION)
    except ApiError as err:
        if err.status != 404:
            raise
    else:
        if before["state"] != "unavailable":
            raise AssertionError(
                f"The delayed source was already live after boot: {before}"
            )
    client.wait_state(
        RAW_REMOVAL_MOTION,
        lambda state: state["state"] == "on",
        "loaded late with its pre-restart detection",
        timeout=WAIT_TIMEOUT,
    )
    client.wait_state(LATE_OCCUPANCY, lambda state: state["state"] == "on", "on")
    client.wait_state(
        LATE_LIGHT,
        lambda state: (
            state["state"] == "on"
            and state["attributes"].get("molight_state") == "occupied"
        ),
        "occupied by the late-loading source",
    )
    # Clearing soon after the late arrival is a person leaving, not a blip:
    # the cycle's real start is unknown, so it must not be classified false.
    client.set_state(RAW_REMOVAL_MOTION, "off")
    client.wait_state(
        LATE_OCCUPANCY,
        lambda state: (
            state["state"] == "off"
            and state["attributes"].get("last_clear_false_detection") is False
        ),
        "cleared without a false-detection flag",
    )
    wait_machine_state(client, "countdown", LATE_LIGHT)
    assert_state_stays(
        client,
        RAW_TIMER_LIGHT,
        lambda state: state["state"] == "on",
        "on past the false-detection delay after a late-source clear",
        duration=4,
    )
    client.call_service("light", "turn_off", {"entity_id": LATE_LIGHT})
    client.wait_state(RAW_TIMER_LIGHT, lambda state: state["state"] == "off", "off")
    for entry_id, entity_id in (
        (snapshot["light_entry_id"], LATE_LIGHT),
        (snapshot["sensor_entry_id"], LATE_OCCUPANCY),
    ):
        client.remove_entry(entry_id)
        wait_entity_absent(client, entity_id)
        wait_entry_removed(client, entry_id, f"Temporary {entity_id}")
    print("PASS: a late-loading occupancy source was adopted and cleared normally")


def run_scenarios() -> None:
    """Self-contained behaviour scenarios on a fresh Home Assistant.

    Everything here builds its own fixtures, so it runs alongside the
    restart-chain suite rather than lengthening it.
    """
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "off", "available")
    run_cold_illuminance_scenario(client)
    timer_occupancy_id = create_virtual_occupancy(
        client, "E2E Timer Occupancy", RAW_TIMER_MOTION, "e2e_timer_occupancy"
    )
    assert_entry_loaded(client, timer_occupancy_id)
    timer_entry_id = create_timeout_light(client)
    assert_entry_loaded(client, timer_entry_id)
    run_timeout_warning_scenario(client, timer_entry_id)
    client.set_behavior(RAW_TIMER_LIGHT, latency=0.8, report_steps=True)
    # Stages must outlast the bulb's 1.1 s reporting delay to be observable.
    timer_entry_id = create_timeout_light(client, light_timeout=10, stage=3)
    assert_entry_loaded(client, timer_entry_id)
    run_timeout_warning_scenario(
        client, timer_entry_id, "slow two-part bulb", light_timeout=10
    )
    client.set_behavior(RAW_TIMER_LIGHT, latency=0, report_steps=False)
    run_physical_change_scenarios(client)
    run_schedule_end_action_scenarios(client)
    run_schedule_mode_scenarios(client)
    run_combined_and_maintain_scenarios(client)
    run_hold_entity_scenarios(client)
    run_fast_physical_scenarios(client)
    print("PASS: behaviour scenarios completed on a fresh Home Assistant")


def check_logs() -> None:
    """Fail for MoLight errors, warnings, or tracebacks in all HA logs."""
    paths = sorted(Path("/ha-config").glob("home-assistant.log*"))
    if not paths:
        raise AssertionError("Home Assistant produced no log file")
    bad_lines = [
        f"{path.name}: {line}"
        for path in paths
        for line in log_failures(path.read_text(errors="replace"))
    ]
    if bad_lines:
        raise AssertionError(
            "Unexpected MoLight log failures:\n" + "\n".join(bad_lines)
        )
    print(f"PASS: no MoLight errors or warnings in {len(paths)} log file(s)")


def main() -> None:
    """Dispatch the phase selected by the host orchestrator."""
    commands = {
        "primary": run_primary,
        "browser-prepare": run_browser_prepare,
        "scenarios": run_scenarios,
        "false-detection-prepare": run_false_detection_prepare,
        "false-detection-verify": run_false_detection_verify,
        "late-source-prepare": run_late_source_prepare,
        "late-source-verify": run_late_source_verify,
        "restart": run_container_restart_verification,
        "unavailable-light-prepare": run_unavailable_light_prepare,
        "unavailable-light-recover": run_unavailable_light_recovery,
        "unavailable-sensors-motion-first": run_unavailable_sensors_motion_first,
        "unavailable-sensors-schedule-first": (run_unavailable_sensors_schedule_first),
        "auto-off-prepare": run_auto_off_prepare,
        "auto-off-restart": run_auto_off_restart,
        "restart-warning-prepare": run_restart_warning_prepare,
        "restart-warning-verify": run_restart_warning_verify,
        "upgrade-prepare": run_upgrade_prepare,
        "upgrade-verify": run_upgrade_verification,
        "logs": check_logs,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        choices = ", ".join(commands)
        raise SystemExit(f"usage: runner.py [{choices}]")
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
