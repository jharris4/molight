"""Tests for the release preparation script."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELEASE_SCRIPT = PROJECT_ROOT / "scripts" / "release"
GIT = shutil.which("git")


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a command in a temporary test repository."""
    return subprocess.run(  # noqa: S603 - arguments are fixed test inputs
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run Git and require success while arranging a test repository."""
    assert GIT is not None
    result = _run([GIT, *args], repo)
    assert result.returncode == 0, result.stderr
    return result


def _release(repo: Path, version: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the copied release script."""
    return _run([sys.executable, "scripts/release", version, *args], repo)


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    """Create a clean two-branch repository with a local bare origin."""
    assert GIT is not None
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    (repo / "scripts").mkdir(parents=True)
    (repo / "custom_components" / "molight").mkdir(parents=True)
    shutil.copy2(RELEASE_SCRIPT, repo / "scripts" / "release")
    (repo / "custom_components" / "molight" / "manifest.json").write_text(
        json.dumps({"version": "1.5.0"}, indent=2) + "\n"
    )
    (repo / "CHANGELOG.md").write_text(
        """# Changelog

## [Unreleased]

- A new feature.

## [1.5.0] - 2026-07-28

- The previous release.

[Unreleased]: https://github.com/jharris4/molight/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/jharris4/molight/releases/tag/v1.5.0
"""
    )

    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "--initial-branch=main", str(repo))
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "branch", "develop")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "--set-upstream", "origin", "main", "develop")
    return repo


def test_release_preview_and_write(release_repo: Path) -> None:
    """A synchronized main branch previews and applies a forward release."""
    manifest = release_repo / "custom_components" / "molight" / "manifest.json"

    preview = _release(release_repo, "1.6.0")

    assert preview.returncode == 0, preview.stderr
    assert "Preview only — nothing written" in preview.stdout
    assert json.loads(manifest.read_text())["version"] == "1.5.0"

    written = _release(release_repo, "1.6.0", "--write")

    assert written.returncode == 0, written.stderr
    assert json.loads(manifest.read_text())["version"] == "1.6.0"
    changelog = (release_repo / "CHANGELOG.md").read_text()
    assert "## [1.6.0] - " in changelog
    assert (
        "[Unreleased]: https://github.com/jharris4/molight/compare/"
        "v1.6.0...HEAD" in changelog
    )


@pytest.mark.parametrize("version", ["1.5.0", "1.4.9"])
def test_release_rejects_non_increasing_version(
    release_repo: Path, version: str
) -> None:
    """A release version must move forward from the manifest version."""
    result = _release(release_repo, version)

    assert result.returncode != 0
    assert "must be greater than the current version 1.5.0" in result.stderr


def test_release_rejects_wrong_branch(release_repo: Path) -> None:
    """A release cannot be prepared directly on develop."""
    _git(release_repo, "switch", "develop")

    result = _release(release_repo, "1.6.0")

    assert result.returncode != 0
    assert "releases must be prepared on 'main', not 'develop'" in result.stderr


def test_release_rejects_dirty_worktree(release_repo: Path) -> None:
    """Tracked and untracked release-time changes must be reviewed first."""
    (release_repo / "uncommitted.txt").write_text("not ready\n")

    result = _release(release_repo, "1.6.0")

    assert result.returncode != 0
    assert "worktree is not clean" in result.stderr


def test_release_rejects_develop_not_in_main(release_repo: Path) -> None:
    """Main must contain the exact development tip selected for release."""
    _git(release_repo, "switch", "develop")
    (release_repo / "feature.txt").write_text("not merged\n")
    _git(release_repo, "add", "feature.txt")
    _git(release_repo, "commit", "-m", "feature")
    _git(release_repo, "push")
    _git(release_repo, "switch", "main")

    result = _release(release_repo, "1.6.0")

    assert result.returncode != 0
    assert "'main' does not contain the tip of 'develop'" in result.stderr


def test_release_rejects_remote_branch_drift(release_repo: Path) -> None:
    """A clean local commit still cannot be released before it is pushed."""
    (release_repo / "local-only.txt").write_text("not pushed\n")
    _git(release_repo, "add", "local-only.txt")
    _git(release_repo, "commit", "-m", "local only")

    result = _release(release_repo, "1.6.0")

    assert result.returncode != 0
    assert "local 'main' is not synchronized with 'origin/main'" in result.stderr


def test_release_rejects_remote_tag(release_repo: Path) -> None:
    """A tag on origin is detected even when it is absent locally."""
    _git(release_repo, "tag", "v1.6.0")
    _git(release_repo, "push", "origin", "v1.6.0")
    _git(release_repo, "tag", "--delete", "v1.6.0")

    result = _release(release_repo, "1.6.0")

    assert result.returncode != 0
    assert "tag v1.6.0 already exists on 'origin'" in result.stderr
