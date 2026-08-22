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
RAW_MOTION = "binary_sensor.e2e_motion"
RAW_SCHEDULE = "binary_sensor.e2e_schedule_source"
TARGET_SELECT = "select.e2e_target_mode"
SOURCE_SELECT = "select.e2e_source_mode"
VIRTUAL_OCCUPANCY = "binary_sensor.e2e_occupancy"
VIRTUAL_SCHEDULE = "binary_sensor.e2e_schedule"
VIRTUAL_LIGHT = "light.e2e_scheduled"
PROFILE_OUTSIDE = "outside_schedule"
PROFILE_INSIDE = "inside_schedule"

EMPTY_LIGHT_SECTIONS = {"sensors": {}, "behavior": {}, "warning": {}}


class ApiError(RuntimeError):
    """Describe a failed Home Assistant API request."""


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
            raise ApiError(f"{method} {path} returned {err.code}: {detail}") from err
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
        while time.monotonic() < deadline:
            try:
                last = self.state(entity_id)
                if predicate(last):
                    return last
            except (ApiError, error.URLError, TimeoutError):
                pass
            time.sleep(0.2)
        raise AssertionError(
            f"Timed out waiting for {entity_id} to be {description}; last={last}"
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
    if result.get("type") != "create_entry":
        raise AssertionError(f"Schedule creation failed: {result}")
    return result["result"]["entry_id"]


def create_virtual_occupancy(client: HomeAssistantClient) -> str:
    """Create a MoLight occupancy sensor wrapping the simulated motion sensor."""
    result = start_create(client, "occupancy")
    expect_step(result, "occupancy")
    result = client.continue_flow(
        result,
        {
            "name": "E2E Occupancy",
            "occupancy_sensor": RAW_MOTION,
            "occupancy_timeout": 1,
            "advanced": {
                "false_detection_grace": 0,
                "clear_on_unavailable_timeout": 1,
                "entity_id": "e2e_occupancy",
            },
        },
    )
    if result.get("type") != "create_entry":
        raise AssertionError(f"Occupancy creation failed: {result}")
    return result["result"]["entry_id"]


def light_settings(brightness: int) -> dict[str, Any]:
    """Build one scheduled profile with occupancy and dynamic selection."""
    return {
        **EMPTY_LIGHT_SECTIONS,
        "light_timeout": 5,
        "sensors": {"occupancy_entity": VIRTUAL_OCCUPANCY},
        "behavior": {
            "auto_on_brightness": brightness,
            "turn_on_select_entity": TARGET_SELECT,
        },
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
    result = submit_selection(client, client.continue_flow(result, light_settings(30)))
    expect_step(result, "scheduled_light_inside")
    result = submit_selection(client, client.continue_flow(result, light_settings(80)))
    if result.get("type") != "create_entry":
        raise AssertionError(f"Scheduled light creation failed: {result}")
    return result["result"]["entry_id"]


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
    result = client.continue_flow(result, light_settings(30), options=True)
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
    result = client.continue_flow(result, light_settings(80), options=True)
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
    client: HomeAssistantClient, direction: str, expected_step: str
) -> None:
    """Convert the live entry in place using MoLight's backend flow."""
    result = client.start_flow()
    result = client.continue_flow(result, {"next_step_id": "convert_lights"})
    expect_step(result, "convert_lights")
    result = client.continue_flow(result, {"next_step_id": direction})
    expect_step(result, direction)
    result = client.continue_flow(result, {"convert_lights": [VIRTUAL_LIGHT]})
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
    """Clear occupancy and force both virtual and physical lights off."""
    client.set_state(RAW_MOTION, "off")
    client.wait_state(VIRTUAL_OCCUPANCY, lambda state: state["state"] == "off", "off")
    client.call_service("light", "turn_off", {"entity_id": VIRTUAL_LIGHT})
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "off", "off")


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
        ),
        f"on at brightness {brightness}",
    )
    client.wait_state(
        TARGET_SELECT,
        lambda state: state["state"] == selection,
        f"selected as {selection}",
    )


def assert_entry_loaded(client: HomeAssistantClient, entry_id: str) -> None:
    """Assert a particular MoLight config entry is still loaded."""
    entries = client.molight_entries()
    matches = [entry for entry in entries if entry["entry_id"] == entry_id]
    if len(matches) != 1 or matches[0].get("state") != "loaded":
        raise AssertionError(f"MoLight entry is not loaded: {matches}")


def run_primary() -> None:
    """Run creation, behavior, editing, conversion, and core-restart checks."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
    client.wait_state(RAW_LIGHT, lambda state: state["state"] == "off", "available")

    create_virtual_schedule(client)
    create_virtual_occupancy(client)
    light_entry_id = create_scheduled_light(client)
    assert_entry_loaded(client, light_entry_id)
    wait_profile(client, PROFILE_OUTSIDE)

    client.call_service(
        "select",
        "select_option",
        {"entity_id": SOURCE_SELECT, "option": "Focus"},
    )
    trigger_and_assert(client, 76, "Focus")

    reset_trigger(client)
    client.set_available(SOURCE_SELECT, False)
    trigger_and_assert(client, 76, "Cozy")
    reset_trigger(client)
    client.set_available(SOURCE_SELECT, True)
    client.call_service(
        "select",
        "select_option",
        {"entity_id": SOURCE_SELECT, "option": "Night"},
    )

    client.set_state(RAW_SCHEDULE, "on")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    wait_profile(client, PROFILE_INSIDE)
    trigger_and_assert(client, 204, "Night")
    client.set_state(RAW_SCHEDULE, "off")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "off", "off")
    wait_profile(client, PROFILE_OUTSIDE)

    edit_scheduled_light(client, light_entry_id)
    assert_entry_loaded(client, light_entry_id)
    client.wait_state(
        VIRTUAL_LIGHT,
        lambda state: (
            state["attributes"].get("friendly_name") == "E2E Scheduled Edited"
        ),
        "renamed without changing entity id",
    )

    convert_light(client, "convert_to_regular", "confirm_convert_to_regular")
    assert_entry_loaded(client, light_entry_id)
    client.wait_state(VIRTUAL_LIGHT, lambda _state: True, "present after conversion")
    convert_light(client, "convert_to_scheduled", "confirm_convert_to_scheduled")
    assert_entry_loaded(client, light_entry_id)
    client.wait_state(VIRTUAL_LIGHT, lambda _state: True, "present after round trip")

    reset_trigger(client)
    client.set_state(RAW_SCHEDULE, "on")
    client.wait_state(VIRTUAL_SCHEDULE, lambda state: state["state"] == "on", "on")
    wait_profile(client, PROFILE_INSIDE)
    trigger_and_assert(client, 204, "Night")

    client.call_service("homeassistant", "restart", {})
    time.sleep(1)
    client.wait_ready()
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
    print("PASS: live creation, behavior, options, conversion, and core restart")


def run_container_restart_verification() -> None:
    """Verify persisted fixture and MoLight state after a container restart."""
    client = HomeAssistantClient()
    client.wait_ready()
    client.authenticate()
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
            state["state"] == "on" and state["attributes"].get("brightness") == 204
        ),
        "persisted on at brightness 204",
    )
    entries = client.molight_entries()
    if len(entries) != 3 or any(entry.get("state") != "loaded" for entry in entries):
        raise AssertionError(f"Expected three loaded MoLight entries: {entries}")
    print("PASS: state and entries survived a full container restart")


def check_logs() -> None:
    """Fail for MoLight errors or tracebacks in all current/rotated HA logs."""
    paths = sorted(Path("/ha-config").glob("home-assistant.log*"))
    if not paths:
        raise AssertionError("Home Assistant produced no log file")
    bad_lines: list[str] = []
    combined = ""
    for path in paths:
        content = path.read_text(errors="replace")
        combined += content
        for line in content.splitlines():
            if re.search(r"\b(ERROR|CRITICAL)\b", line) and re.search(
                r"molight|MoLight", line
            ):
                bad_lines.append(f"{path.name}: {line}")
    if "Traceback (most recent call last)" in combined and re.search(
        r"custom_components[/.]molight[/.]", combined
    ):
        bad_lines.append("A traceback references custom_components/molight")
    if bad_lines:
        raise AssertionError(
            "Unexpected MoLight log failures:\n" + "\n".join(bad_lines)
        )
    print(f"PASS: no MoLight errors in {len(paths)} Home Assistant log file(s)")


def main() -> None:
    """Dispatch the phase selected by the host orchestrator."""
    commands = {
        "primary": run_primary,
        "restart": run_container_restart_verification,
        "logs": check_logs,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        choices = ", ".join(commands)
        raise SystemExit(f"usage: runner.py [{choices}]")
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
