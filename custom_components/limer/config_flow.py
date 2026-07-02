"""Config flow for Limer."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, selector

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
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_SCHEDULE_ENTITY,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DOMAIN,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
)


def _limer_cfg(entry: config_entries.ConfigEntry) -> dict[str, Any]:
    return {**entry.data, **entry.options}


def _effective_occupancy_timeout(hass: HomeAssistant, entity_id: str) -> int | None:
    """Resolve the occupancy timeout (seconds) behind a Limer occupancy entity.

    For a simple occupancy sensor this is its configured timeout; for a
    combined sensor it is the max across all constituent sensors (the
    countdown math anchors to the constituent that clears last).
    """
    reg_entry = er.async_get(hass).async_get(entity_id)
    if reg_entry is None or reg_entry.config_entry_id is None:
        return None
    entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None

    cfg = _limer_cfg(entry)
    entity_type = cfg.get(CONF_ENTITY_TYPE)
    if entity_type == ENTITY_TYPE_OCCUPANCY:
        return int(cfg.get(CONF_OCCUPANCY_TIMEOUT, 0))
    if entity_type == ENTITY_TYPE_COMBINED_OCCUPANCY:
        constituents = cfg.get(CONF_TRIGGER_SENSORS, []) + cfg.get(
            CONF_MAINTAIN_SENSORS, []
        )
        timeouts = [
            t
            for e in constituents
            if (t := _effective_occupancy_timeout(hass, e)) is not None
        ]
        return max(timeouts, default=None)
    return None


def _min_dependent_light_timeout(
    hass: HomeAssistant, occupancy_entry_id: str
) -> int | None:
    """Smallest light_timeout among virtual lights depending on an occupancy entry.

    A light depends on the entry when it references the entry's entity
    directly, or references a combined sensor that includes it.
    """
    registry = er.async_get(hass)
    dependent_ids = {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, occupancy_entry_id)
    }
    if not dependent_ids:
        return None

    for entry in hass.config_entries.async_entries(DOMAIN):
        cfg = _limer_cfg(entry)
        if cfg.get(CONF_ENTITY_TYPE) != ENTITY_TYPE_COMBINED_OCCUPANCY:
            continue
        constituents = cfg.get(CONF_TRIGGER_SENSORS, []) + cfg.get(
            CONF_MAINTAIN_SENSORS, []
        )
        if dependent_ids.intersection(constituents):
            dependent_ids.update(
                e.entity_id
                for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            )

    timeouts = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        cfg = _limer_cfg(entry)
        if (
            cfg.get(CONF_ENTITY_TYPE) == ENTITY_TYPE_LIGHT
            and cfg.get(CONF_OCCUPANCY_ENTITY) in dependent_ids
        ):
            timeouts.append(int(cfg.get(CONF_LIGHT_TIMEOUT, 0)))
    return min(timeouts, default=None)


def _validate_light_timeout(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, str]:
    """Check light_timeout >= the referenced occupancy entity's timeout."""
    occupancy_entity = user_input.get(CONF_OCCUPANCY_ENTITY)
    if not occupancy_entity:
        return {}
    occ_timeout = _effective_occupancy_timeout(hass, occupancy_entity)
    if occ_timeout is not None and int(user_input[CONF_LIGHT_TIMEOUT]) < occ_timeout:
        return {CONF_LIGHT_TIMEOUT: "light_timeout_too_short"}
    return {}


class LimerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Limer."""

    VERSION = 1

    def __init__(self) -> None:
        self._entity_type: str | None = None

    @staticmethod
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> "LimerOptionsFlow":
        return LimerOptionsFlow(entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1 — choose which kind of virtual entity to create."""
        if user_input is not None:
            self._entity_type = user_input[CONF_ENTITY_TYPE]
            return await {
                ENTITY_TYPE_OCCUPANCY: self.async_step_occupancy,
                ENTITY_TYPE_COMBINED_OCCUPANCY: self.async_step_combined_occupancy,
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
                                ENTITY_TYPE_COMBINED_OCCUPANCY,
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
    # Occupancy (simple — one sensor, one timeout)
    # ------------------------------------------------------------------

    async def async_step_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure a Virtual Occupancy Sensor."""
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY, **user_input},
            )

        return self.async_show_form(
            step_id="occupancy",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_OCCUPANCY_SENSOR): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="binary_sensor", multiple=False
                        )
                    ),
                    vol.Required(
                        CONF_OCCUPANCY_TIMEOUT, default=120
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=3600, unit_of_measurement="s", mode="box"
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Combined Occupancy (trigger + maintain VirtualOccupancySensors)
    # ------------------------------------------------------------------

    async def async_step_combined_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure a Virtual Combined Occupancy Sensor."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_TRIGGER_SENSORS):
                errors[CONF_TRIGGER_SENSORS] = "trigger_sensors_required"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
                        **user_input,
                    },
                )

        return self.async_show_form(
            step_id="combined_occupancy",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_TRIGGER_SENSORS): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            integration=DOMAIN,
                            device_class="occupancy",
                            multiple=True,
                        )
                    ),
                    vol.Optional(CONF_MAINTAIN_SENSORS): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            integration=DOMAIN,
                            device_class="occupancy",
                            multiple=True,
                        )
                    ),
                }
            ),
            errors=errors,
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
                errors = _validate_light_timeout(self.hass, user_input)
            if not errors:
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


# ---------------------------------------------------------------------------
# Options flow — edit an existing Limer entity
# ---------------------------------------------------------------------------


class LimerOptionsFlow(config_entries.OptionsFlow):
    """Allow editing a Limer entity's settings after creation."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._cfg = {**entry.data, **entry.options}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        return await {
            ENTITY_TYPE_OCCUPANCY: self.async_step_occupancy,
            ENTITY_TYPE_COMBINED_OCCUPANCY: self.async_step_combined_occupancy,
            ENTITY_TYPE_ILLUMINANCE: self.async_step_illuminance,
            ENTITY_TYPE_SCHEDULE: self.async_step_schedule,
            ENTITY_TYPE_LIGHT: self.async_step_light,
        }[self._cfg[CONF_ENTITY_TYPE]]()

    # ------------------------------------------------------------------
    # Occupancy
    # ------------------------------------------------------------------

    async def async_step_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            # Raising this sensor's timeout must not outgrow any virtual light
            # that depends on it (directly or through a combined sensor).
            min_light = _min_dependent_light_timeout(self.hass, self._entry.entry_id)
            if (
                min_light is not None
                and int(user_input[CONF_OCCUPANCY_TIMEOUT]) > min_light
            ):
                errors[CONF_OCCUPANCY_TIMEOUT] = "occupancy_timeout_too_long"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )

        cfg = self._cfg
        return self.async_show_form(
            step_id="occupancy",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
                    vol.Required(
                        CONF_OCCUPANCY_SENSOR, default=cfg.get(CONF_OCCUPANCY_SENSOR)
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="binary_sensor", multiple=False
                        )
                    ),
                    vol.Required(
                        CONF_OCCUPANCY_TIMEOUT,
                        default=cfg.get(CONF_OCCUPANCY_TIMEOUT, 120),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=3600, unit_of_measurement="s", mode="box"
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Combined Occupancy
    # ------------------------------------------------------------------

    async def async_step_combined_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_TRIGGER_SENSORS):
                errors[CONF_TRIGGER_SENSORS] = "trigger_sensors_required"
            else:
                # The new constituent set must not outgrow any dependent light:
                # the combined sensor's effective timeout is its max constituent.
                constituents = user_input.get(CONF_TRIGGER_SENSORS, []) + user_input.get(
                    CONF_MAINTAIN_SENSORS, []
                )
                timeouts = [
                    t
                    for e in constituents
                    if (t := _effective_occupancy_timeout(self.hass, e)) is not None
                ]
                min_light = _min_dependent_light_timeout(
                    self.hass, self._entry.entry_id
                )
                if timeouts and min_light is not None and max(timeouts) > min_light:
                    errors["base"] = "occupancy_timeout_too_long"
                else:
                    return self.async_create_entry(
                        title=user_input[CONF_NAME], data=user_input
                    )

        cfg = self._cfg
        return self.async_show_form(
            step_id="combined_occupancy",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
                    vol.Required(
                        CONF_TRIGGER_SENSORS,
                        default=cfg.get(CONF_TRIGGER_SENSORS, []),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            integration=DOMAIN,
                            device_class="occupancy",
                            multiple=True,
                        )
                    ),
                    vol.Optional(
                        CONF_MAINTAIN_SENSORS,
                        default=cfg.get(CONF_MAINTAIN_SENSORS, []),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            integration=DOMAIN,
                            device_class="occupancy",
                            multiple=True,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Illuminance
    # ------------------------------------------------------------------

    async def async_step_illuminance(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        cfg = self._cfg
        return self.async_show_form(
            step_id="illuminance",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
                    vol.Required(
                        CONF_ILLUMINANCE_SENSOR,
                        default=cfg.get(CONF_ILLUMINANCE_SENSOR),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            device_class="illuminance", multiple=False
                        )
                    ),
                    vol.Required(
                        CONF_ILLUMINANCE_THRESHOLD,
                        default=cfg.get(CONF_ILLUMINANCE_THRESHOLD, 10.0),
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
        )

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
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
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        cfg = self._cfg
        first = (cfg.get(CONF_TIME_WINDOWS) or [{}])[0]
        schema_fields: dict = {
            vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
        }
        if first.get("start"):
            schema_fields[vol.Optional("window_start", default=first["start"])] = (
                selector.TimeSelector()
            )
        else:
            schema_fields[vol.Optional("window_start")] = selector.TimeSelector()
        if first.get("end"):
            schema_fields[vol.Optional("window_end", default=first["end"])] = (
                selector.TimeSelector()
            )
        else:
            schema_fields[vol.Optional("window_end")] = selector.TimeSelector()

        return self.async_show_form(
            step_id="schedule",
            data_schema=vol.Schema(schema_fields),
        )

    # ------------------------------------------------------------------
    # Virtual Light
    # ------------------------------------------------------------------

    async def async_step_light(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_LIGHTS):
                errors[CONF_LIGHTS] = "lights_required"
            else:
                errors = _validate_light_timeout(self.hass, user_input)
            if not errors:
                # Drop None values so absent optional entity fields are simply
                # missing from entry.options rather than stored as None.
                clean = {k: v for k, v in user_input.items() if v is not None}
                return self.async_create_entry(title=clean[CONF_NAME], data=clean)

        cfg = self._cfg
        schema: dict = {
            vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
            vol.Required(
                CONF_LIGHTS, default=cfg.get(CONF_LIGHTS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            ),
            vol.Required(
                CONF_LIGHT_TIMEOUT,
                default=cfg.get(CONF_LIGHT_TIMEOUT, 300),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=3600, unit_of_measurement="s", mode="box"
                )
            ),
        }
        # Only pre-fill optional entity fields when a value actually exists;
        # passing default=None to EntitySelector raises a validation error.
        for key in (CONF_OCCUPANCY_ENTITY, CONF_ILLUMINANCE_ENTITY, CONF_SCHEDULE_ENTITY):
            current = cfg.get(key)
            marker = (
                vol.Optional(key, default=current) if current else vol.Optional(key)
            )
            schema[marker] = selector.EntitySelector(
                selector.EntitySelectorConfig(integration=DOMAIN, multiple=False)
            )

        return self.async_show_form(
            step_id="light",
            data_schema=vol.Schema(schema),
            errors=errors,
        )
