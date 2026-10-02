"""Tests for the e2e runner's own assertion helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.e2e.runner import runner


class _Clock:
    """A monotonic clock that only advances when the runner sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Client:
    """Answers each state read with the next reply, repeating the last one."""

    def __init__(self, *replies: dict[str, Any] | Exception) -> None:
        self._replies = list(replies)

    def state(self, entity_id: str) -> dict[str, Any]:
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(
        runner, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
    )
    return clock


def _stays_off(client: _Client) -> None:
    runner.assert_state_stays(
        client, "light.example", lambda state: state["state"] == "off", "off", 1
    )


@pytest.mark.parametrize(
    "failure", [OSError("connection refused"), runner.ApiError("busy", 503)]
)
def test_state_stays_fails_when_every_read_fails(clock: _Clock, failure) -> None:
    """A window with no successful read proves nothing, so it must not pass."""
    with pytest.raises(AssertionError, match="Never observed"):
        _stays_off(_Client(failure))
    assert clock.now >= 1


def test_state_stays_rides_out_a_failed_read(clock: _Clock) -> None:
    _stays_off(_Client(OSError("connection refused"), {"state": "off"}))
    assert clock.now >= 1


def test_state_stays_fails_on_an_unwanted_state(clock: _Clock) -> None:
    with pytest.raises(AssertionError, match="to stay off"):
        _stays_off(_Client({"state": "off"}, {"state": "on"}))


def test_state_stays_raises_an_error_polling_does_not_tolerate(clock: _Clock) -> None:
    with pytest.raises(runner.ApiError):
        _stays_off(_Client(runner.ApiError("missing", 404)))


def _record(level: str, logger: str, message: str) -> str:
    """Format one line the way Home Assistant's console logger does."""
    return f"2026-10-01 19:37:40.123 {level} (MainThread) [{logger}] {message}"


BOOT = _record(
    "WARNING",
    "homeassistant.loader",
    "We found a custom integration molight which has not been tested by Home "
    "Assistant. This component might cause stability problems, be sure to "
    "disable it if you experience issues with Home Assistant",
)
TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/config/custom_components/molight/light.py", line 1, in x\n'
    "ValueError"
)


def test_log_records_groups_continuation_lines() -> None:
    """A traceback belongs to the record above it; text before any record is dropped."""
    first = _record("ERROR", "homeassistant.core", "Error doing job")
    second = _record("INFO", "homeassistant.setup", "Setting up light")
    preamble = "s6-rc: info: service legacy-services successfully started"
    content = f"{preamble}\n{first}\n{TRACEBACK}\n{second}\n"

    assert runner.log_records(content) == [
        ("ERROR", first, f"{first}\n{TRACEBACK}"),
        ("INFO", second, second),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "2026-10-01T19:37:40.123 ERROR (MainThread) [custom_components.molight] x",
        "ERROR (MainThread) [custom_components.molight] x",
        "2026-10-01 19:37:40.123 ERROR [custom_components.molight] x",
    ],
)
def test_log_records_skips_lines_in_another_format(line: str) -> None:
    assert runner.log_records(line) == []


@pytest.mark.parametrize(
    "record",
    [
        _record("ERROR", "custom_components.molight.light", "boom"),
        _record("CRITICAL", "custom_components.molight", "boom"),
        _record("ERROR", "homeassistant.core", "Error doing job") + "\n" + TRACEBACK,
        _record("WARNING", "custom_components.molight.light", "Something odd"),
        _record("WARNING", "homeassistant.helpers.entity", "Slow") + "\n" + TRACEBACK,
        _record("INFO", "homeassistant.core", "Stopped") + "\n" + TRACEBACK,
        _record("DEBUG", "homeassistant.core", "Stopped") + "\n" + TRACEBACK,
    ],
    ids=[
        "molight-error",
        "molight-critical",
        "error-with-molight-traceback",
        "molight-warning",
        "warning-with-molight-traceback",
        "info-with-molight-traceback",
        "debug-with-molight-traceback",
    ],
)
def test_log_failures_flags_molight_problems(record: str) -> None:
    assert runner.log_failures(record) == [record.splitlines()[0]]


@pytest.mark.parametrize(
    "record",
    [
        BOOT,
        *(
            _record("WARNING", "custom_components.molight.light", f"x {allowed} y")
            for allowed in runner.ALLOWED_WARNINGS
        ),
        *(
            _record("ERROR", "custom_components.molight.light", f"x {allowed} y")
            for allowed in runner.ALLOWED_ERRORS
        ),
        _record("ERROR", "homeassistant.core", "Error doing job") + "\nValueError",
        _record("WARNING", "homeassistant.components.http", "Login attempt failed"),
        _record("INFO", "custom_components.molight", "Set up molight"),
    ],
)
def test_log_failures_passes_allowed_and_unrelated_records(record: str) -> None:
    assert runner.log_failures(record) == []


def test_log_failures_reports_every_failure_once() -> None:
    error = _record("ERROR", "custom_components.molight.light", "boom")
    warning = _record("WARNING", "custom_components.molight.binary_sensor", "odd")
    content = f"{BOOT}\n{error}\n{TRACEBACK}\n{warning}\n"

    assert runner.log_failures(content) == [error, warning]


def test_log_boots_counts_only_parsed_boot_records() -> None:
    unparsed = BOOT.replace("2026-10-01 ", "2026-10-01T")
    info = _record("INFO", "homeassistant.loader", runner.BOOT_LINE)

    assert runner.log_boots(f"{BOOT}\n{BOOT}\n{unparsed}\n{info}\n") == 2


@pytest.fixture
def container_log(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "e2e-container.log"
    monkeypatch.setattr(runner, "CONTAINER_LOG", path)
    return path


def test_check_logs_passes_a_clean_log_with_every_boot(container_log, capsys) -> None:
    info = _record("INFO", "homeassistant.setup", "Setting up molight")
    # The container's output keeps Home Assistant's colours.
    container_log.write_text(f"\x1b[33m{BOOT}\x1b[0m\n{info}\n{BOOT}\n")

    runner.check_logs(2)

    assert "logs of 2 boot(s)" in capsys.readouterr().out


@pytest.mark.parametrize("boots", [0, 1, 3])
def test_check_logs_fails_on_another_boot_count(container_log, boots: int) -> None:
    """Fewer boots leave part of the run unchecked; more mean an unplanned restart."""
    container_log.write_text("".join(f"{BOOT}\n" for _ in range(boots)))

    with pytest.raises(AssertionError, match=f"Parsed {boots} Home Assistant boot"):
        runner.check_logs(2)


def test_check_logs_fails_a_log_it_cannot_parse(container_log) -> None:
    """With no record parsed nothing could be flagged, so the gate must fail."""
    error = _record("ERROR", "custom_components.molight.light", "boom")
    content = f"{BOOT}\n{error}\n".replace("2026-10-01 ", "2026-10-01T")
    container_log.write_text(content)

    with pytest.raises(AssertionError, match="Parsed 0 Home Assistant boot"):
        runner.check_logs(1)


def test_check_logs_fails_on_a_molight_error(container_log) -> None:
    error = _record("ERROR", "custom_components.molight.light", "boom")
    container_log.write_text(f"{BOOT}\n{error}\n")

    with pytest.raises(AssertionError, match="boom"):
        runner.check_logs(1)


def test_check_logs_fails_without_the_saved_log(container_log) -> None:
    with pytest.raises(AssertionError, match="is missing"):
        runner.check_logs(1)


def test_main_runs_the_log_check_with_the_boot_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[int] = []
    monkeypatch.setattr(runner, "check_logs", checked.append)
    monkeypatch.setattr(runner.sys, "argv", ["runner.py", "logs", "12"])

    runner.main()

    assert checked == [12]


@pytest.mark.parametrize("args", [["logs"], ["logs", "x"], ["logs", "1", "2"]])
def test_main_needs_one_boot_count_for_the_log_check(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.setattr(runner, "check_logs", lambda _: pytest.fail("ran"))
    monkeypatch.setattr(runner.sys, "argv", ["runner.py", *args])

    with pytest.raises(SystemExit, match="logs <expected boots>"):
        runner.main()


class _SelectionHass(runner.HomeAssistantClient):
    """Fakes the states one turn-on selection call reads, after a previous call.

    The previous call left the light reporting a Cozy turn-on from the fixed
    option; an occupancy turn-on applies the selection only when asked to.
    """

    def __init__(self, *, applies: bool, stamps: bool = True) -> None:
        super().__init__()
        self.applies = applies
        self.stamps = stamps
        self.states: dict[str, dict[str, Any]] = {
            runner.VIRTUAL_OCCUPANCY: {"state": "off", "attributes": {}},
            runner.RAW_LIGHT: {"state": "off", "attributes": {}},
            runner.TARGET_SELECT: {
                "state": "Cozy",
                "attributes": {"options": ["Cozy", "Focus", "Night"]},
            },
            runner.VIRTUAL_LIGHT: {
                "state": "off",
                "attributes": {
                    "last_turn_on_selection_option": "Cozy",
                    "last_turn_on_selection_source": "fixed",
                    "last_on_occupancy": "2026-10-01T00:00:00+00:00",
                },
            },
        }

    def state(self, entity_id: str) -> dict[str, Any]:
        return self.states[entity_id]

    def call_service(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        assert (domain, service) == ("select", "select_option")
        self.states[data["entity_id"]]["state"] = data["option"]

    def set_state(
        self,
        entity_id: str,
        state: str | float | bool,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        assert (entity_id, state) == (runner.RAW_MOTION, "on")
        self.states[runner.VIRTUAL_OCCUPANCY]["state"] = "on"
        self.states[runner.RAW_LIGHT] = {
            "state": "on",
            "attributes": {
                "brightness": runner.pct(30),
                "testbed_last_command": {"data": {"transition": 1}},
            },
        }
        light = self.states[runner.VIRTUAL_LIGHT]["attributes"]
        if self.stamps:
            light["last_on_occupancy"] = "2026-10-01T00:01:00+00:00"
        if self.applies:
            self.states[runner.TARGET_SELECT]["state"] = "Cozy"
            light["last_turn_on_selection_option"] = "Cozy"
            light["last_turn_on_selection_source"] = "fixed"


def test_trigger_and_assert_passes_when_the_selection_is_applied(
    clock: _Clock,
) -> None:
    runner.trigger_and_assert(
        _SelectionHass(applies=True), runner.pct(30), "Cozy", source="fixed"
    )


def test_trigger_and_assert_fails_when_no_selection_is_applied(clock: _Clock) -> None:
    """The target and attributes already show Cozy from the previous call."""
    with pytest.raises(AssertionError, match=runner.TARGET_SELECT):
        runner.trigger_and_assert(
            _SelectionHass(applies=False), runner.pct(30), "Cozy", source="fixed"
        )


def test_trigger_and_assert_fails_without_a_new_occupancy_turn_on(
    clock: _Clock,
) -> None:
    with pytest.raises(AssertionError, match="a new occupancy turn-on"):
        runner.trigger_and_assert(
            _SelectionHass(applies=True, stamps=False),
            runner.pct(30),
            "Cozy",
            source="fixed",
        )
