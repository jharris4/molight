"""Tests for the Home Assistant bump script."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPI = "https://pypi.org/pypi"
PACKAGE = "pytest-homeassistant"
OLD_PACKAGE = "pytest-homeassistant-custom-component"
FILES = [{"filename": "x.whl", "yanked": False}]
YANKED = [{"filename": "x.whl", "yanked": True}]


def _load() -> ModuleType:
    """Import the extensionless script as a module."""
    path = str(PROJECT_ROOT / "scripts" / "ha-bump")
    loader = importlib.machinery.SourceFileLoader("ha_bump", path)
    spec = importlib.util.spec_from_loader("ha_bump", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _pypi(ha: dict[str, list], packages: dict[str, list]) -> dict[str, dict]:
    """Build PyPI JSON documents: the HA releases and the test package releases."""
    return {
        f"{PYPI}/homeassistant/json": {"releases": ha},
        f"{PYPI}/{PACKAGE}/json": {"releases": packages},
    }


@pytest.fixture
def bump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the script against a repository pinned to 2026.9.4, on the old package."""
    module = _load()
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    requirements = tmp_path / "requirements_test.txt"
    requirements.write_text(f"# Pinned\n{OLD_PACKAGE}==0.13.367\nruff==0.16.10\n")
    env = tmp_path / "tests" / "e2e" / "env.sh"
    env.write_text("# The release\nMOLIGHT_E2E_HA_VERSION=2026.9.4\nexport X\n")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "REQUIREMENTS", requirements)
    monkeypatch.setattr(module, "E2E_ENV", env)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    return module


def _serve(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, documents: dict[str, dict]
) -> list[str]:
    """Answer the script's PyPI requests from documents; return the URLs asked."""
    asked: list[str] = []

    def fetch(url: str) -> dict:
        asked.append(url)
        return documents[url]

    monkeypatch.setattr(module, "fetch_json", fetch)
    return asked


def _run(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, *args: str
) -> tuple[str, str]:
    """Run the script's main and return both pinned files."""
    monkeypatch.setattr("sys.argv", ["scripts/ha-bump", *args])
    module.main()
    return module.REQUIREMENTS.read_text(), module.E2E_ENV.read_text()


def test_preview_then_write(
    bump: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A preview writes nothing; --write moves both pins, onto the new package."""
    documents = _pypi(
        {"2026.9.4": FILES, "2026.10.0b0": FILES, "2026.10.0": FILES},
        {"2026.10.0b0": FILES, "2026.10.0": FILES},
    )
    _serve(bump, monkeypatch, documents)
    before = _run(bump, monkeypatch)
    assert before == (
        f"# Pinned\n{OLD_PACKAGE}==0.13.367\nruff==0.16.10\n",
        "# The release\nMOLIGHT_E2E_HA_VERSION=2026.9.4\nexport X\n",
    )
    out = capsys.readouterr().out
    assert "Home Assistant 2026.9.4 -> 2026.10.0" in out
    assert f"{OLD_PACKAGE}==0.13.367 -> {PACKAGE}==2026.10.0" in out
    assert "Preview only, nothing written" in out

    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    after = _run(bump, monkeypatch, "--write")
    assert after == (
        f"# Pinned\n{PACKAGE}==2026.10.0\nruff==0.16.10\n",
        "# The release\nMOLIGHT_E2E_HA_VERSION=2026.10.0\nexport X\n",
    )
    assert output.read_text() == "previous=2026.9.4\nversion=2026.10.0\n"


def test_betas_and_yanked_releases_are_not_bumps(
    bump: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only a newer stable release that is still on PyPI counts."""
    documents = _pypi(
        {
            "2026.9.4": FILES,
            "2026.9.5": YANKED,
            "2026.9.6": [],
            "2026.10.0b0": FILES,
            "2026.10.0.dev0": FILES,
        },
        {"2026.10.0b0": FILES},
    )
    asked = _serve(bump, monkeypatch, documents)
    before = (bump.REQUIREMENTS.read_text(), bump.E2E_ENV.read_text())
    assert _run(bump, monkeypatch, "--write") == before
    assert "2026.9.4 is the newest stable release" in capsys.readouterr().out
    assert asked == [f"{PYPI}/homeassistant/json"]


@pytest.mark.parametrize(
    "packages",
    [{"2026.10.0b1": FILES}, {"2026.10.0": YANKED}, {"2026.10.0": []}],
    ids=["beta only", "yanked", "no files"],
)
def test_waits_for_the_test_package(
    bump: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    packages: dict[str, list],
) -> None:
    """A release whose test package is not on PyPI yet waits."""
    documents = _pypi({"2026.9.4": FILES, "2026.10.0": FILES}, packages)
    _serve(bump, monkeypatch, documents)
    before = (bump.REQUIREMENTS.read_text(), bump.E2E_ENV.read_text())
    assert _run(bump, monkeypatch, "--write") == before
    assert "2026.10.0 is out, but" in capsys.readouterr().out


def test_moves_a_pin_already_on_the_new_package(
    bump: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new package's pin moves to the release's own version."""
    bump.REQUIREMENTS.write_text(f"# Pinned\n{PACKAGE}==2026.9.4\nruff==0.16.10\n")
    documents = _pypi(
        {"2026.9.4": FILES, "2026.10.0": FILES, "2026.11.0b0": FILES},
        {"2026.9.4": FILES, "2026.10.0": FILES, "2026.11.0b0": FILES},
    )
    _serve(bump, monkeypatch, documents)
    requirements, env = _run(bump, monkeypatch, "--write")
    assert requirements == f"# Pinned\n{PACKAGE}==2026.10.0\nruff==0.16.10\n"
    assert "MOLIGHT_E2E_HA_VERSION=2026.10.0\n" in env


def test_rejects_other_arguments(
    bump: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anything but --write is refused before PyPI is asked."""
    asked = _serve(bump, monkeypatch, {})
    with pytest.raises(SystemExit, match="usage"):
        _run(bump, monkeypatch, "--apply")
    assert asked == []


def test_requires_exactly_one_pin(
    bump: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing or doubled pin aborts instead of guessing."""
    _serve(bump, monkeypatch, {})
    bump.E2E_ENV.write_text("MOLIGHT_E2E_HA_VERSION=1\nMOLIGHT_E2E_HA_VERSION=2\n")
    with pytest.raises(SystemExit, match=r"expected one pin in tests/e2e/env.sh"):
        _run(bump, monkeypatch)


def test_release_sorts_after_its_betas(bump: ModuleType) -> None:
    """Betas sort before their release and after the previous one."""
    versions = ["2026.10.0", "2026.9.4", "2026.10.0b1", "2026.10.0b0", "2026.10.1"]
    assert sorted(versions, key=bump.ha_key) == [
        "2026.9.4",
        "2026.10.0b0",
        "2026.10.0b1",
        "2026.10.0",
        "2026.10.1",
    ]
    assert bump.ha_key("2026.10.0.dev0") is None
