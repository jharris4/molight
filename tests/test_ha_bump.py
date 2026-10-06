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
PACKAGE = "pytest-homeassistant-custom-component"
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


def _pypi(ha: dict[str, list], packages: dict[str, str | None]) -> dict[str, dict]:
    """Build PyPI JSON documents: HA releases and each test package's HA pin."""
    documents: dict[str, dict] = {
        f"{PYPI}/homeassistant/json": {"releases": ha},
        f"{PYPI}/{PACKAGE}/json": {
            "releases": dict.fromkeys(packages, FILES) | {"0.13.367": FILES}
        },
    }
    for version, pin in packages.items():
        requires = ["pytest==9.0.3"] + ([f"homeassistant=={pin}"] if pin else [])
        documents[f"{PYPI}/{PACKAGE}/{version}/json"] = {
            "info": {"requires_dist": requires}
        }
    return documents


@pytest.fixture
def bump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the script against a repository pinned to 2026.9.4."""
    module = _load()
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    requirements = tmp_path / "requirements_test.txt"
    requirements.write_text(f"# Pinned\n{PACKAGE}==0.13.367\nruff==0.16.10\n")
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
    """A preview writes nothing; --write moves both pins and reports them."""
    documents = _pypi(
        {"2026.9.4": FILES, "2026.10.0b0": FILES, "2026.10.0": FILES},
        {"0.13.368": "2026.10.0b0", "0.13.369": "2026.10.0"},
    )
    _serve(bump, monkeypatch, documents)
    before = _run(bump, monkeypatch)
    assert before == (
        f"# Pinned\n{PACKAGE}==0.13.367\nruff==0.16.10\n",
        "# The release\nMOLIGHT_E2E_HA_VERSION=2026.9.4\nexport X\n",
    )
    out = capsys.readouterr().out
    assert "Home Assistant 2026.9.4 -> 2026.10.0" in out
    assert f"{PACKAGE} 0.13.367 -> 0.13.369" in out
    assert "Preview only, nothing written" in out

    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    after = _run(bump, monkeypatch, "--write")
    assert after == (
        f"# Pinned\n{PACKAGE}==0.13.369\nruff==0.16.10\n",
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
        {"0.13.368": "2026.10.0b0"},
    )
    asked = _serve(bump, monkeypatch, documents)
    before = (bump.REQUIREMENTS.read_text(), bump.E2E_ENV.read_text())
    assert _run(bump, monkeypatch, "--write") == before
    assert "2026.9.4 is the newest stable release" in capsys.readouterr().out
    assert asked == [f"{PYPI}/homeassistant/json"]


def test_waits_for_the_test_package(
    bump: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A release no test package pins yet waits, without reading older pins."""
    documents = _pypi(
        {"2026.9.4": FILES, "2026.10.0": FILES},
        {"0.13.368": "2026.10.0b0", "0.13.369": "2026.10.0b1", "0.13.370": None},
    )
    asked = _serve(bump, monkeypatch, documents)
    before = (bump.REQUIREMENTS.read_text(), bump.E2E_ENV.read_text())
    assert _run(bump, monkeypatch, "--write") == before
    assert "2026.10.0 is out, but" in capsys.readouterr().out
    # The newest pin below the target ends the search.
    assert f"{PYPI}/{PACKAGE}/0.13.368/json" not in asked


def test_picks_the_newest_package_for_the_release(
    bump: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skips packages pinning the next beta and takes the newest exact match."""
    documents = _pypi(
        {"2026.9.4": FILES, "2026.10.0": FILES, "2026.11.0b0": FILES},
        {
            "0.13.368": "2026.10.0",
            "0.13.369": "2026.10.0",
            "0.13.370": "2026.11.0b0",
            "0.13.371.post1": "2026.11.0b0",
        },
    )
    _serve(bump, monkeypatch, documents)
    requirements, env = _run(bump, monkeypatch, "--write")
    assert f"{PACKAGE}==0.13.369\n" in requirements
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
