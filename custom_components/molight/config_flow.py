"""Config flow for MoLight."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    COMBINE_EARLIEST,
    COMBINE_LATEST,
    CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_FALSE_OFF_DELAY,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_HYSTERESIS,
    CONF_ILLUMINANCE_MODE,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    DOMAIN,
    EDGE_COMBINE,
    EDGE_OFFSET,
    EDGE_SUN,
    EDGE_TIME,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_SCHEDULE,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODES,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODES,
    SUN_EVENTS,
)
from .helpers import molight_config as _molight_cfg

# "none" lets a previously chosen sun anchor be cleared in the options flow —
# a bare SelectSelector can't be un-set once it has a value.
_SUN_OPTIONS = ["none", *SUN_EVENTS]
_COMBINE_OPTIONS = [COMBINE_LATEST, COMBINE_EARLIEST]


def _window_from_input(user_input: dict[str, Any]) -> dict | None:
    """Build a schedule window dict from the flat form fields, or None."""

    def _edge(prefix: str) -> dict | None:
        edge: dict = {}
        if user_input.get(f"{prefix}_time"):
            edge[EDGE_TIME] = user_input[f"{prefix}_time"]
        sun = user_input.get(f"{prefix}_sun")
        if sun and sun != "none":
            edge[EDGE_SUN] = sun
            offset = int(user_input.get(f"{prefix}_offset") or 0)
            if offset:
                edge[EDGE_OFFSET] = offset
            edge[EDGE_COMBINE] = user_input.get(
                f"{prefix}_combine", COMBINE_LATEST
            )
        return edge or None

    start, end = _edge("start"), _edge("end")
    if start and end:
        return {"start": start, "end": end}
    return None


def _window_input_provided(user_input: dict[str, Any]) -> bool:
    """True when the user filled in any window edge field at all."""
    return any(
        user_input.get(f"{prefix}_time")
        or user_input.get(f"{prefix}_sun") not in (None, "none")
        for prefix in ("start", "end")
    )


def _schedule_edge_fields(window: dict | None) -> dict:
    """Form fields for a schedule window, prefilled from an existing window."""

    def _edge_defaults(edge) -> dict:
        if isinstance(edge, str):
            return {EDGE_TIME: edge}
        return edge if isinstance(edge, dict) else {}

    window = window or {}
    fields: dict = {}
    for prefix in ("start", "end"):
        edge = _edge_defaults(window.get(prefix))
        # suggested_value (not default) so a previously set time can be
        # cleared to make the edge sun-only.
        time_key = (
            vol.Optional(
                f"{prefix}_time",
                description={"suggested_value": edge[EDGE_TIME]},
            )
            if edge.get(EDGE_TIME)
            else vol.Optional(f"{prefix}_time")
        )
        fields[time_key] = selector.TimeSelector()
        fields[
            vol.Optional(f"{prefix}_sun", default=edge.get(EDGE_SUN, "none"))
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_SUN_OPTIONS, translation_key="sun_event"
            )
        )
        fields[
            vol.Optional(
                f"{prefix}_offset", default=edge.get(EDGE_OFFSET, 0)
            )
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-720, max=720, step=1, unit_of_measurement="min", mode="box"
            )
        )
        fields[
            vol.Optional(
                f"{prefix}_combine",
                default=edge.get(EDGE_COMBINE, COMBINE_LATEST),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_COMBINE_OPTIONS, translation_key="combine_mode"
            )
        )
    return fields


# Pickers for a virtual light's optional entity references. The schedule
# sensor has no device_class (HA offers none that fits), so its picker can
# only narrow to MoLight binary sensors. Keep-on entities can be anything
# with an on/off state (input_boolean, switch, binary_sensor, ...), so that
# picker is not narrowed at all.
_LIGHT_REF_SELECTORS = {
    CONF_OCCUPANCY_ENTITY: selector.EntitySelectorConfig(
        integration=DOMAIN,
        domain="binary_sensor",
        device_class="occupancy",
        multiple=False,
    ),
    CONF_ILLUMINANCE_ENTITY: selector.EntitySelectorConfig(
        integration=DOMAIN,
        domain="binary_sensor",
        device_class="light",
        multiple=False,
    ),
    CONF_SCHEDULE_ENTITY: selector.EntitySelectorConfig(
        integration=DOMAIN, domain="binary_sensor", multiple=False
    ),
    CONF_HOLD_ENTITIES: selector.EntitySelectorConfig(multiple=True),
}


def _effective_occupancy_timeout(hass: HomeAssistant, entity_id: str) -> int | None:
    """Resolve the occupancy timeout (seconds) behind a MoLight occupancy entity.

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

    cfg = _molight_cfg(entry)
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
        cfg = _molight_cfg(entry)
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
        cfg = _molight_cfg(entry)
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


class MoLightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MoLight."""

    VERSION = 1

    def __init__(self) -> None:
        self._entity_type: str | None = None

    @staticmethod
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> "MoLightOptionsFlow":
        return MoLightOptionsFlow(entry)

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
                    vol.Required(
                        CONF_FALSE_DETECTION_GRACE, default=3
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=60, unit_of_measurement="s", mode="box"
                        )
                    ),
                    vol.Required(
                        CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
                        default=DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=3600, unit_of_measurement="s", mode="box"
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
                    vol.Required(
                        CONF_ILLUMINANCE_HYSTERESIS, default=0.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=10000,
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

        Each window edge combines an optional fixed time with an optional sun
        event (e.g. start at the later of sunset and 21:00). The HA frontend
        doesn't have a native multi-window editor, so the flow accepts a
        single window for now.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            window = _window_from_input(user_input)
            if window is None and _window_input_provided(user_input):
                # Half-filled window: it would be silently dropped, leaving a
                # sensor that is permanently off.
                errors["base"] = "window_incomplete"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_TIME_WINDOWS: [window] if window else [],
                    },
                )

        return self.async_show_form(
            step_id="schedule",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    **_schedule_edge_fields(None),
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
                    vol.Required(
                        CONF_FALSE_OFF_DELAY, default=5
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=300, unit_of_measurement="s", mode="box"
                        )
                    ),
                    **{
                        vol.Optional(key): selector.EntitySelector(sel_config)
                        for key, sel_config in _LIGHT_REF_SELECTORS.items()
                    },
                    vol.Required(
                        CONF_ILLUMINANCE_MODE, default=ILLUMINANCE_MODE_CONTROL
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=ILLUMINANCE_MODES,
                            translation_key=CONF_ILLUMINANCE_MODE,
                        )
                    ),
                    vol.Required(
                        CONF_SCHEDULE_MODE, default=SCHEDULE_MODE_FOLLOW
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=SCHEDULE_MODES,
                            translation_key=CONF_SCHEDULE_MODE,
                        )
                    ),
                }
            ),
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Options flow — edit an existing MoLight entity
# ---------------------------------------------------------------------------


class MoLightOptionsFlow(config_entries.OptionsFlow):
    """Allow editing a MoLight entity's settings after creation."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._cfg = _molight_cfg(entry)

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
                    vol.Required(
                        CONF_FALSE_DETECTION_GRACE,
                        default=cfg.get(CONF_FALSE_DETECTION_GRACE, 0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=60, unit_of_measurement="s", mode="box"
                        )
                    ),
                    vol.Required(
                        CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
                        default=cfg.get(
                            CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
                            DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=3600, unit_of_measurement="s", mode="box"
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
                    vol.Required(
                        CONF_ILLUMINANCE_HYSTERESIS,
                        default=cfg.get(CONF_ILLUMINANCE_HYSTERESIS, 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=10000,
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
        errors: dict[str, str] = {}

        if user_input is not None:
            window = _window_from_input(user_input)
            if window is None and _window_input_provided(user_input):
                errors["base"] = "window_incomplete"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_TIME_WINDOWS: [window] if window else [],
                    },
                )

        cfg = self._cfg
        first = (cfg.get(CONF_TIME_WINDOWS) or [None])[0]
        return self.async_show_form(
            step_id="schedule",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
                    **_schedule_edge_fields(first),
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
            vol.Required(
                CONF_FALSE_OFF_DELAY,
                default=cfg.get(CONF_FALSE_OFF_DELAY, 5),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=300, unit_of_measurement="s", mode="box"
                )
            ),
        }
        # Pre-fill via suggested_value (not default): a default can never be
        # cleared in the UI, which would make sensor references permanent.
        for key, sel_config in _LIGHT_REF_SELECTORS.items():
            current = cfg.get(key)
            marker = (
                vol.Optional(key, description={"suggested_value": current})
                if current
                else vol.Optional(key)
            )
            schema[marker] = selector.EntitySelector(sel_config)

        schema[
            vol.Required(
                CONF_ILLUMINANCE_MODE,
                default=cfg.get(CONF_ILLUMINANCE_MODE, ILLUMINANCE_MODE_CONTROL),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=ILLUMINANCE_MODES, translation_key=CONF_ILLUMINANCE_MODE
            )
        )
        schema[
            vol.Required(
                CONF_SCHEDULE_MODE,
                default=cfg.get(CONF_SCHEDULE_MODE, SCHEDULE_MODE_FOLLOW),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=SCHEDULE_MODES, translation_key=CONF_SCHEDULE_MODE
            )
        )

        return self.async_show_form(
            step_id="light",
            data_schema=vol.Schema(schema),
            errors=errors,
        )
