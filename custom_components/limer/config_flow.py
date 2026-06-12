"""Config flow for Limer."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_ENTITY_TYPE,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_LIGHTS,
    CONF_LIGHT_TIMEOUT,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_SCHEDULE_ENTITY,
    CONF_SENSOR_TIMEOUTS,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
)


class LimerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Limer."""

    VERSION = 1

    def __init__(self) -> None:
        self._entity_type: str | None = None
        # Occupancy multi-step state
        self._occ_name: str = ""
        self._occ_trigger: list[str] = []
        self._occ_maintain: list[str] = []
        self._occ_pending: list[str] = []   # sensors still awaiting a timeout
        self._occ_timeouts: dict[str, int] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1 — choose which kind of virtual entity to create."""
        if user_input is not None:
            self._entity_type = user_input[CONF_ENTITY_TYPE]
            return await {
                ENTITY_TYPE_OCCUPANCY: self.async_step_occupancy,
                ENTITY_TYPE_ILLUMINANCE: self.async_step_illuminance,
                ENTITY_TYPE_SCHEDULE: self.async_step_schedule,
                ENTITY_TYPE_LIGHT: self.async_step_light,
            }[self._entity_type]()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ENTITY_TYPE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                ENTITY_TYPE_OCCUPANCY,
                                ENTITY_TYPE_ILLUMINANCE,
                                ENTITY_TYPE_SCHEDULE,
                                ENTITY_TYPE_LIGHT,
                            ],
                            translation_key=CONF_ENTITY_TYPE,
                        )
                    )
                }
            ),
        )

    # ------------------------------------------------------------------
    # Occupancy — step 1: name + sensors
    # ------------------------------------------------------------------

    async def async_step_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1 — pick name, trigger sensors, and maintain sensors."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_TRIGGER_SENSORS):
                errors[CONF_TRIGGER_SENSORS] = "trigger_sensors_required"
            else:
                self._occ_name = user_input[CONF_NAME]
                self._occ_trigger = list(user_input[CONF_TRIGGER_SENSORS])
                self._occ_maintain = list(user_input.get(CONF_MAINTAIN_SENSORS) or [])
                self._occ_pending = self._occ_trigger + self._occ_maintain
                self._occ_timeouts = {}
                return await self.async_step_occupancy_sensor_timeout()

        return self.async_show_form(
            step_id="occupancy",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_TRIGGER_SENSORS): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="binary_sensor", multiple=True
                        )
                    ),
                    vol.Optional(CONF_MAINTAIN_SENSORS): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="binary_sensor", multiple=True
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Occupancy — step 2 (repeated): timeout per sensor
    # ------------------------------------------------------------------

    async def async_step_occupancy_sensor_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2 — set a countdown timeout for each selected sensor."""
        if user_input is not None:
            sensor = self._occ_pending[0]
            self._occ_timeouts[sensor] = int(user_input[CONF_OCCUPANCY_TIMEOUT])
            self._occ_pending = self._occ_pending[1:]

        if not self._occ_pending:
            return self.async_create_entry(
                title=self._occ_name,
                data={
                    CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
                    CONF_NAME: self._occ_name,
                    CONF_TRIGGER_SENSORS: self._occ_trigger,
                    CONF_MAINTAIN_SENSORS: self._occ_maintain,
                    CONF_SENSOR_TIMEOUTS: self._occ_timeouts,
                },
            )

        sensor = self._occ_pending[0]
        sensor_type = "trigger" if sensor in self._occ_trigger else "maintain"

        return self.async_show_form(
            step_id="occupancy_sensor_timeout",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_OCCUPANCY_TIMEOUT, default=120): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=3600, unit_of_measurement="s", mode="box"
                        )
                    ),
                }
            ),
            description_placeholders={
                "sensor": sensor,
                "sensor_type": sensor_type,
                "sensor_index": str(
                    (len(self._occ_trigger) + len(self._occ_maintain))
                    - len(self._occ_pending)
                    + 1
                ),
                "sensor_total": str(len(self._occ_trigger) + len(self._occ_maintain)),
            },
        )

    # ------------------------------------------------------------------
    # Illuminance
    # ------------------------------------------------------------------

    async def async_step_illuminance(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure a Virtual Illuminance Binary Sensor."""
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE, **user_input},
            )

        return self.async_show_form(
            step_id="illuminance",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_ILLUMINANCE_SENSOR): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            device_class="illuminance", multiple=False
                        )
                    ),
                    vol.Required(
                        CONF_ILLUMINANCE_THRESHOLD, default=10.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=100000,
                            step=0.1,
                            unit_of_measurement="lx",
                            mode="box",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure a Virtual Schedule Binary Sensor.

        Time windows are a list of {"start": "HH:MM", "end": "HH:MM"} dicts.
        The HA frontend doesn't have a native multi-window time selector, so
        for now we accept a single window in the config flow and plan to add
        an options flow for additional windows later.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            windows = []
            if user_input.get("window_start") and user_input.get("window_end"):
                windows.append(
                    {
                        "start": user_input.pop("window_start"),
                        "end": user_input.pop("window_end"),
                    }
                )
            user_input[CONF_TIME_WINDOWS] = windows
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE, **user_input},
            )

        return self.async_show_form(
            step_id="schedule",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Optional("window_start"): selector.TimeSelector(),
                    vol.Optional("window_end"): selector.TimeSelector(),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Virtual Light
    # ------------------------------------------------------------------

    async def async_step_light(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure a Virtual Light.

        Constraint: light_timeout >= occupancy_timeout of any referenced
        occupancy entity (enforced here and in the options flow).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_LIGHTS):
                errors[CONF_LIGHTS] = "lights_required"
            else:
                # TODO: validate light_timeout >= referenced occupancy timeout
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT, **user_input},
                )

        return self.async_show_form(
            step_id="light",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_LIGHTS): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="light", multiple=True
                        )
                    ),
                    vol.Required(
                        CONF_LIGHT_TIMEOUT, default=300
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=3600, unit_of_measurement="s", mode="box"
                        )
                    ),
                    vol.Optional(CONF_OCCUPANCY_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            integration=DOMAIN, multiple=False
                        )
                    ),
                    vol.Optional(CONF_ILLUMINANCE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            integration=DOMAIN, multiple=False
                        )
                    ),
                    vol.Optional(CONF_SCHEDULE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            integration=DOMAIN, multiple=False
                        )
                    ),
                }
            ),
            errors=errors,
        )
