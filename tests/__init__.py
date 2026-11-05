"""Tests for the MoLight HA integration."""

import importlib.util
import sys

# Home Assistant 2026.11 replaced the test package, and the replacement only
# goes back to 2026.11; the tests keep the old name, aliased here when needed.
if importlib.util.find_spec("pytest_homeassistant_custom_component") is None:
    for suffix in ("", ".common"):
        sys.modules[f"pytest_homeassistant_custom_component{suffix}"] = (
            importlib.import_module(f"pytest_homeassistant{suffix}")
        )
