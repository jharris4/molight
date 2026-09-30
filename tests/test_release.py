"""Tests for the release preparation script."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELEASE_SCRIPT = PROJECT_ROOT / "scripts" / "release"
GIT = shutil.which("git")
COMPARE = "https://github.com/jharris4/molight/compare"
CHANGELOG_TEXT = f"""# Changelog

## [Unreleased]

- A new feature.

## [1.5.0] - 2026-07-28

- The previous release.

[Unreleased]: {COMPARE}/v1.5.0...HEAD
[1.5.0]: https://github.com/jharris4/molight/releases/tag/v1.5.0
"""


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
    (repo / "CHANGELOG.md").write_text(CHANGELOG_TEXT)

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


def _release_files(repo: Path) -> tuple[str, str]:
    """Return the changelog and manifest the script edits."""
    manifest = repo / "custom_components" / "molight" / "manifest.json"
    return (repo / "CHANGELOG.md").read_text(), manifest.read_text()


def _commit_and_push(repo: Path) -> None:
    """Commit every change on main and push it, so the preflight passes."""
    _git(repo, "commit", "-am", "arrange")
    _git(repo, "push", "origin", "main")


def test_release_preview_and_write(release_repo: Path) -> None:
    """A synchronized main branch previews and applies a forward release."""
    before = _release_files(release_repo)

    preview = _release(release_repo, "1.6.0")

    assert preview.returncode == 0, preview.stderr
    assert "Preview only, nothing written" in preview.stdout
    assert "python3 scripts/release 1.6.0 --write" in preview.stdout
    assert _release_files(release_repo) == before

    earliest = datetime.now().astimezone().date().isoformat()
    written = _release(release_repo, "1.6.0", "--write")
    latest = datetime.now().astimezone().date().isoformat()

    assert written.returncode == 0, written.stderr
    assert "git tag v1.6.0 && git push origin v1.6.0" in written.stdout
    changelog, manifest = _release_files(release_repo)
    stamp = re.search(r"^## \[1\.6\.0\] - (\S+)$", changelog, re.MULTILINE)
    assert stamp is not None
    assert stamp.group(1) in (earliest, latest)
    assert (
        changelog
        == f"""# Changelog

## [Unreleased]

## [1.6.0] - {stamp.group(1)}

- A new feature.

## [1.5.0] - 2026-07-28

- The previous release.

[Unreleased]: {COMPARE}/v1.6.0...HEAD
[1.6.0]: {COMPARE}/v1.5.0...v1.6.0
[1.5.0]: https://github.com/jharris4/molight/releases/tag/v1.5.0
"""
    )
    assert json.loads(manifest) == {"version": "1.6.0"}


def test_release_accepts_a_v_prefix(release_repo: Path) -> None:
    """The version may be given as the tag name."""
    result = _release(release_repo, "v1.6.0")

    assert result.returncode == 0, result.stderr
    assert "python3 scripts/release 1.6.0 --write" in result.stdout


def _edit_changelog(old: str, new: str) -> Callable[[Path], None]:
    """Arrange a committed changelog edit."""

    def arrange(repo: Path) -> None:
        path = repo / "CHANGELOG.md"
        path.write_text(path.read_text().replace(old, new, 1))
        _commit_and_push(repo)

    return arrange


def _set_manifest_version(repo: Path) -> None:
    manifest = repo / "custom_components" / "molight" / "manifest.json"
    manifest.write_text(json.dumps({"version": "1.4.0"}, indent=2) + "\n")
    _commit_and_push(repo)


def _tag_locally(repo: Path) -> None:
    _git(repo, "tag", "v1.6.0")


def _delete_develop(repo: Path) -> None:
    _git(repo, "branch", "-D", "develop")


def _delete_remote_develop(repo: Path) -> None:
    _git(repo, "push", "origin", "--delete", "develop")


def _break_remote(repo: Path) -> None:
    _git(repo, "remote", "set-url", "origin", str(repo.parent / "missing.git"))


@pytest.mark.parametrize(
    ("version", "arrange", "error"),
    [
        ("1.6", None, "'1.6' is not a x.y.z version"),
        ("1.6.0.1", None, "'1.6.0.1' is not a x.y.z version"),
        ("", None, "'' is not a x.y.z version"),
        (
            "1.6.0",
            _edit_changelog("## [Unreleased]", "## Unreleased"),
            "CHANGELOG.md has no '## [Unreleased]' section to release",
        ),
        (
            "1.6.0",
            _edit_changelog("## [1.5.0] - 2026-07-28", "## 1.5.0"),
            "CHANGELOG.md has no previous release heading to compare against",
        ),
        (
            "1.6.0",
            _set_manifest_version,
            "manifest version 1.4.0 does not match the latest changelog release 1.5.0",
        ),
        (
            "1.6.0",
            _edit_changelog("\n[Unreleased]:", "\n## [1.6.0] - 2026-07-29\n\n"),
            "CHANGELOG.md already has a [1.6.0] section",
        ),
        (
            "1.6.0",
            _edit_changelog("- A new feature.\n", ""),
            "the [Unreleased] section is empty, so there is nothing to release",
        ),
        ("1.6.0", _tag_locally, "tag v1.6.0 already exists locally"),
        ("1.6.0", _delete_develop, "local branch 'develop' does not exist"),
        ("1.6.0", _break_remote, "could not query remote 'origin'"),
        (
            "1.6.0",
            _delete_remote_develop,
            "remote branch 'origin/develop' does not exist",
        ),
        (
            "1.6.0",
            _edit_changelog("[Unreleased]: ", "[Next]: "),
            "CHANGELOG.md has no '[Unreleased]:' link at the bottom",
        ),
    ],
)
def test_release_guard_fails_without_writing(
    release_repo: Path,
    version: str,
    arrange: Callable[[Path], None] | None,
    error: str,
) -> None:
    """Each guard stops the release with its message and leaves the files alone."""
    if arrange is not None:
        arrange(release_repo)
    before = _release_files(release_repo)

    result = _release(release_repo, version, "--write")

    assert result.returncode != 0
    assert error in result.stderr
    assert _release_files(release_repo) == before


@pytest.mark.parametrize("args", [[], ["1.6.0", "1.7.0"]])
def test_release_rejects_wrong_argument_count(
    release_repo: Path, args: list[str]
) -> None:
    """Exactly one version is required."""
    result = _run([sys.executable, "scripts/release", *args], release_repo)

    assert result.returncode != 0
    assert "usage: scripts/release <version> [--write]" in result.stderr


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
