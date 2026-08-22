"""Config flow for the MoLight end-to-end testbed."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries

from .const import DOMAIN


class TestbedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the singleton testbed config entry."""

    VERSION = 1

    async def async_step_import(
        self, _import_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Import the testbed from configuration.yaml."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="MoLight E2E Testbed", data={})

    async def async_step_user(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Allow manual setup when debugging the fixture."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="MoLight E2E Testbed", data={})
