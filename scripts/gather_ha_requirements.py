#!/usr/bin/env python3
"""Print the pip requirements Home Assistant would lazy-install at runtime.

Walks the manifest.json dependency closure of the given integration domains
(default: default_config) and prints each integration's pinned `requirements`,
one per line — suitable for `pip install -r`. Pre-installing these avoids
HA stalling or crashing on runtime pip installs inside the devcontainer.
"""

import json
import sys
from pathlib import Path

import homeassistant

COMPONENTS = Path(homeassistant.__file__).parent / "components"


def walk(domain: str, seen: set[str], requirements: set[str]) -> None:
    """Collect the requirements of a domain's manifest dependency closure."""
    if domain in seen:
        return
    seen.add(domain)
    manifest_path = COMPONENTS / domain / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text())
    requirements.update(manifest.get("requirements", []))
    for dep in manifest.get("dependencies", []):
        walk(dep, seen, requirements)


def main() -> None:
    """Print the pinned requirements for the domains given on the CLI."""
    domains = sys.argv[1:] or ["default_config"]
    seen: set[str] = set()
    requirements: set[str] = set()
    for domain in domains:
        walk(domain, seen, requirements)
    print("\n".join(sorted(requirements, key=str.lower)))


if __name__ == "__main__":
    main()
