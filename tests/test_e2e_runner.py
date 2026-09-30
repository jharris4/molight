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
