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
VIRTUAL_SCHEDULE = "binary_sensor.e2e_schedule"
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
PROFILE_OUTSIDE = "outside_schedule"
PROFILE_INSIDE = "inside_schedule"

UPGRADE_OCCUPANCY = "binary_sensor.upgrade_occupancy"
UPGRADE_COMBINED = "binary_sensor.upgrade_combined"
UPGRADE_ILLUMINANCE = "binary_sensor.upgrade_illuminance"
UPGRADE_SCHEDULE = "binary_sensor.upgrade_schedule"
UPGRADE_LIGHT = "light.upgrade_gated"
UPGRADE_REMOTE_SENSOR = "sensor.upgrade_remote_last_action"
UPGRADE_SNAPSHOT = Path("/ha-config/e2e-upgrade-snapshot.json")
FIXTURES_SNAPSHOT = Path("/ha-config/e2e-fixtures-snapshot.json")
AUTO_OFF_SNAPSHOT = Path("/ha-config/e2e-auto-off-snapshot.json")
RESTART_WARNING_SNAPSHOT = Path("/ha-config/e2e-restart-warning-snapshot.json")
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


def create_virtual_schedule(client: HomeAssistantClient) -> str:
    """Create a source-backed MoLight Virtual Schedule through its API flow."""
    result = start_create(client, "schedule")
    expect_step(result, "schedule")
    result = client.continue_flow(result, {"schedule_definition": "binary_sensor"})
    expect_step(result, "schedule_source")
    result = client.continue_flow(
        result,
        {
            "name": "E2E Schedule",
            "schedule_source": RAW_SCHEDULE,
            "schedule_invert": False,
            "advanced": {"entity_id": "e2e_schedule"},
        },
    )
    return finish_creation(result, "Schedule")


def create_virtual_occupancy(
    client: HomeAssistantClient,
    name: str = "E2E Occupancy",
    source: str = RAW_MOTION,
    entity_id: str = "e2e_occupancy",
) -> str:
    """Create a MoLight occupancy sensor wrapping a simulated motion sensor."""
    return create_entry(
        client,
        "occupancy",
        {
            "name": name,
            "occupancy_sensor": source,
            "occupancy_timeout": 1,
            "advanced": {
                "false_detection_grace": 0,
                "clear_on_unavailable_timeout": 1,
                "entity_id": entity_id,
            },
        },
        name,
    )


def create_virtual_illuminance(client: HomeAssistantClient) -> str:
    """Create a MoLight illuminance threshold wrapping the simulated lux sensor."""
    return create_entry(
        client,
        "illuminance",
        {
            "name": "E2E Illuminance",
            "illuminance_sensor": RAW_ILLUMINANCE,
            "illuminance_threshold": 10,
            "illuminance_hysteresis": 1,
            "advanced": {"entity_id": "e2e_illuminance"},
        },
        "Illuminance",
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
        lambda state: state["attributes"].get("active_settings") == profile,
        f"using the {profile} profile",
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
    client: HomeAssistantClient, brightness: int, selection: str
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
            "behavior": {"auto_on_brightness": 60},
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
    # The restarted timer reaches its warning two seconds later; a physical dim
    # during it cancels the warning and restores the pre-warning color.
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
        RAW_MULTI_RGB, lambda state: state["state"] == "off", "off", timeout=15
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
    client: HomeAssistantClient, action: str, click: str, event_type: str
) -> dict[str, Any]:
    """Wait for the remote's diagnostic sensor to record one binding."""
    return client.wait_state(
        REMOTE_LAST_ACTION,
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
    """Read Home Assistant's persisted config-entry records."""
    payload = json.loads(CONFIG_ENTRIES_STORAGE.read_text())
    return payload["data"]["entries"]


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
    run_illuminance_and_door_scenarios(client)
    checkpoint("illuminance gate/control and door open/open-close behavior per profile")
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

    reset_trigger(client)
    client.call_service(
        "select",
        "select_option",
        {"entity_id": SOURCE_SELECT, "option": "Focus"},
    )
    trigger_and_assert(client, pct(30), "Focus")

    reset_trigger(client)
    client.set_available(SOURCE_SELECT, False)
    trigger_and_assert(client, pct(30), "Cozy")
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
