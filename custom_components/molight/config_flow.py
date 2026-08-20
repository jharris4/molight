"""Config flow for MoLight."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT as BINARY_SENSOR_ENTITY_ID_FORMAT,
)
from homeassistant.components.light import (
    ENTITY_ID_FORMAT as LIGHT_ENTITY_ID_FORMAT,
)
from homeassistant.components.select import ATTR_OPTIONS
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr, entity_registry as er, selector
from homeassistant.util import slugify

from .const import (
    AFFIX_TARGET_ENTITY_ID,
    AFFIX_TARGET_NAME,
    AFFIX_TARGETS,
    ASSIGN_ROLE_MAINTAIN,
    ASSIGN_ROLE_REGULAR,
    ASSIGN_ROLES,
    COMBINE_EARLIEST,
    COMBINE_LATEST,
    CONF_AFFIX_PREFIX,
    CONF_AFFIX_SUFFIX,
    CONF_AFFIX_TARGET,
    CONF_ASSIGN_LIGHTS,
    CONF_ASSIGN_ROLE,
    CONF_ASSIGN_SENSOR,
    CONF_AUTO_OFF_TRANSITION,
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_AUTO_ON_COLOR_TEMP,
    CONF_AUTO_ON_RGB_COLOR,
    CONF_AUTO_ON_TRANSITION,
    CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    CONF_CONFIRM_CONVERSION,
    CONF_CONVERT_LIGHTS,
    CONF_DIM_STEP,
    CONF_DOOR_ENTITY,
    CONF_DOOR_MODE,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_RGB_COLOR,
    CONF_EFFECT_TIMEOUT,
    CONF_EFFECT_TRANSITION,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_FALSE_DETECTION_GRACE,
    CONF_FALSE_OFF_DELAY,
    CONF_FILTER_AREAS,
    CONF_FILTER_LABELS,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_HYSTERESIS,
    CONF_ILLUMINANCE_MODE,
    CONF_ILLUMINANCE_SENSOR,
    CONF_ILLUMINANCE_THRESHOLD,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_PRESELECT_ALL,
    CONF_SCHEDULE_END_ACTION,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    CONF_SELECTED_ENTITIES,
    CONF_TARGET_LIGHTS,
    CONF_TIME_WINDOWS,
    CONF_TRIGGER_SENSORS,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_TURN_ON_SELECT_SOURCE_ENTITY,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_RGB_COLOR,
    CONF_WARN_TIMEOUT,
    CONF_WARN_TRANSITION,
    DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    DEFAULT_DIM_STEP,
    DEFAULT_DOOR_MODE,
    DEFAULT_EFFECT_BRIGHTNESS,
    DEFAULT_EFFECT_TIMEOUT,
    DEFAULT_FALSE_DETECTION_GRACE,
    DEFAULT_FALSE_OFF_DELAY,
    DEFAULT_ILLUMINANCE_HYSTERESIS,
    DEFAULT_ILLUMINANCE_MODE,
    DEFAULT_ILLUMINANCE_THRESHOLD,
    DEFAULT_LIGHT_TIMEOUT,
    DEFAULT_OCCUPANCY_TIMEOUT,
    DEFAULT_SCHEDULE_END_ACTION,
    DEFAULT_SCHEDULE_MODE,
    DEFAULT_WARN_TIMEOUT,
    DOMAIN,
    DOOR_MODES,
    EDGE_COMBINE,
    EDGE_OFFSET,
    EDGE_SUN,
    EDGE_TIME,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_REMOTE,
    ENTITY_TYPE_SCHEDULE,
    ENTITY_TYPE_SCHEDULED_LIGHT,
    ILLUMINANCE_MODES,
    REMOTE_ACTION_FIELDS,
    REMOTE_ACTION_OFF,
    REMOTE_ACTION_ON,
    REMOTE_PRESET_VALUE_KEYS,
    SCHEDULE_END_ACTION_KEEP,
    SCHEDULE_END_ACTION_SWITCH,
    SCHEDULE_END_ACTION_TURN_OFF,
    SCHEDULE_END_ACTIONS,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_KEEP,
    SCHEDULE_MODE_GATE_SWITCH,
    SCHEDULE_MODES,
    SUN_EVENTS,
)
from .helpers import molight_config as _molight_cfg
from .remote import CLICK_DOUBLE, CLICK_SINGLE, entity_double_click_supported

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

    # A config-flow step handler; invoked with no arguments to (re-)show its
    # form, since every step defaults user_input to None.
    _StepHandler = Callable[..., Awaitable[config_entries.FlowResult]]

# "none" lets a previously chosen sun anchor be cleared in the options flow —
# a bare SelectSelector can't be un-set once it has a value.
_SUN_OPTIONS = ["none", *SUN_EVENTS]
_COMBINE_OPTIONS = [COMBINE_LATEST, COMBINE_EARLIEST]

# Optional brightness (percent) for automatic turn-ons of a virtual light.
_AUTO_ON_BRIGHTNESS_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=1, max=100, step=1, unit_of_measurement="%", mode="box"
    )
)

# Effect/warn warning-sequence stage durations (0 disables a stage).
_STAGE_TIMEOUT_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, max=3600, step=1, unit_of_measurement="s", mode="box"
    )
)
# Effect-stage brightness allows 0 (blink fully off), unlike auto-on/warn.
_EFFECT_BRIGHTNESS_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, max=100, step=1, unit_of_measurement="%", mode="box"
    )
)
# Optional fade times for the light's own service calls (blank/0 = none).
_TRANSITION_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, max=300, step=0.1, unit_of_measurement="s", mode="box"
    )
)
# Optional colors for automatic turn-ons and the warning stages. A color is
# forwarded to every member light in one call; HA filters/converts it per
# real light, so brightness-only members simply ignore it.
_COLOR_TEMP_SELECTOR = selector.ColorTempSelector(
    selector.ColorTempSelectorConfig(
        unit=selector.ColorTempSelectorUnit.KELVIN, min=2000, max=6500
    )
)
_RGB_COLOR_SELECTOR = selector.ColorRGBSelector()

# ---------------------------------------------------------------------------
# Collapsible form sections
#
# The forms group their fields into collapsible sections, but that grouping is
# presentation only: entries keep storing one flat mapping. Submitted section
# sub-dicts are flattened straight back by _flatten_sections, and stored values
# are re-nested by _nest_sections so add_suggested_values_to_schema (which
# recurses into sections) can prefill the forms. Section keys must therefore
# never collide with a CONF_* key.
#
# Every section is vol.Required with NO marker default. A marker default gets
# serialized onto the expandable field itself, and the frontend's initial-data
# pass prefers a field default over recursing into a section — so a section
# default (even an empty dict) hides every per-field default and suggested
# value inside it. Submissions must therefore include each section key: the
# frontend always submits sections (collapsed or not), and programmatic
# submissions pass an empty dict, which voluptuous validates against the
# section schema, filling in the per-field defaults.
# ---------------------------------------------------------------------------

SECTION_BEHAVIOR = "behavior"
SECTION_WARNING = "warning"
SECTION_SENSORS = "sensors"
SECTION_ADVANCED = "advanced"

# Which flat config keys live in which section of the virtual-light form.
_LIGHT_SECTIONS: dict[str, tuple[str, ...]] = {
    SECTION_BEHAVIOR: (
        CONF_FALSE_OFF_DELAY,
        CONF_AUTO_ON_BRIGHTNESS,
        CONF_AUTO_ON_COLOR_TEMP,
        CONF_AUTO_ON_RGB_COLOR,
        CONF_TURN_ON_SELECT_ENTITY,
        CONF_TURN_ON_SELECT_OPTION,
        CONF_TURN_ON_SELECT_SOURCE_ENTITY,
        CONF_AUTO_ON_TRANSITION,
        CONF_AUTO_OFF_TRANSITION,
    ),
    SECTION_WARNING: (
        CONF_EFFECT_TIMEOUT,
        CONF_EFFECT_BRIGHTNESS,
        CONF_EFFECT_RGB_COLOR,
        CONF_EFFECT_TRANSITION,
        CONF_WARN_TIMEOUT,
        CONF_WARN_BRIGHTNESS,
        CONF_WARN_RGB_COLOR,
        CONF_WARN_TRANSITION,
    ),
    SECTION_SENSORS: (
        CONF_OCCUPANCY_ENTITY,
        CONF_MAINTAIN_OCCUPANCY_ENTITY,
        CONF_ILLUMINANCE_ENTITY,
        CONF_ILLUMINANCE_MODE,
        CONF_SCHEDULE_ENTITY,
        CONF_SCHEDULE_MODE,
        CONF_DOOR_ENTITY,
        CONF_DOOR_MODE,
        CONF_HOLD_ENTITIES,
    ),
    SECTION_ADVANCED: (CONF_ENTITY_ID,),
}

_OCCUPANCY_SECTIONS: dict[str, tuple[str, ...]] = {
    SECTION_ADVANCED: (
        CONF_FALSE_DETECTION_GRACE,
        CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
        CONF_ENTITY_ID,
    ),
}

# Combined occupancy, illuminance and schedule only tuck the optional
# entity_id away; their remaining fields stay top-level.
_ENTITY_ID_SECTIONS: dict[str, tuple[str, ...]] = {
    SECTION_ADVANCED: (CONF_ENTITY_ID,),
}

# One section per bindable remote action (the action name is the section key),
# each holding its single-/double-click button pickers; presets add their
# brightness/color value fields.
_REMOTE_SECTIONS: dict[str, tuple[str, ...]] = {
    action: (single_key, double_key, *REMOTE_PRESET_VALUE_KEYS.get(action, ()))
    for single_key, double_key, action in REMOTE_ACTION_FIELDS
}


def _flatten_sections(
    user_input: dict[str, Any], layout: dict[str, tuple[str, ...]]
) -> dict[str, Any]:
    """Collapse a form's section sub-dicts back into a flat mapping."""
    flat = dict(user_input)
    for key in layout:
        flat.update(flat.pop(key, None) or {})
    return flat


def _nest_sections(
    values: dict[str, Any], layout: dict[str, tuple[str, ...]]
) -> dict[str, Any]:
    """Group flat stored values into section sub-dicts for form prefills."""
    nested = dict(values)
    for section_key, fields in layout.items():
        inner = {f: nested.pop(f) for f in fields if f in nested}
        if inner:
            nested[section_key] = inner
    return nested


def _entity_id_section() -> dict:
    """Collapsed Advanced section holding only the optional entity_id."""
    return {
        vol.Required(SECTION_ADVANCED): section(
            vol.Schema({vol.Optional(CONF_ENTITY_ID): selector.TextSelector()}),
            {"collapsed": True},
        )
    }


def _window_from_input(user_input: dict[str, Any]) -> dict | None:
    """Build a schedule window dict from the start/end sections, or None.

    The section keys match the stored window shape, so a submitted edge dict
    maps straight onto a window edge (minus the "none" sun placeholder and
    zero offset).
    """

    def _edge(data: dict[str, Any]) -> dict | None:
        edge: dict = {}
        if data.get(EDGE_TIME):
            edge[EDGE_TIME] = data[EDGE_TIME]
        sun = data.get(EDGE_SUN)
        if sun and sun != "none":
            edge[EDGE_SUN] = sun
            offset = int(data.get(EDGE_OFFSET) or 0)
            if offset:
                edge[EDGE_OFFSET] = offset
            edge[EDGE_COMBINE] = data.get(EDGE_COMBINE, COMBINE_LATEST)
        return edge or None

    start = _edge(user_input.get("start") or {})
    end = _edge(user_input.get("end") or {})
    if start and end:
        return {"start": start, "end": end}
    return None


def _window_input_provided(user_input: dict[str, Any]) -> bool:
    """Return True when the user filled in any window edge field at all."""
    return any(
        (user_input.get(key) or {}).get(EDGE_TIME)
        or (user_input.get(key) or {}).get(EDGE_SUN) not in (None, "none")
        for key in ("start", "end")
    )


def _window_suggested(window: dict | None) -> dict:
    """Suggested start/end section values from a stored window.

    Legacy plain-string edges ("HH:MM") are upgraded to time-only edge dicts.
    """

    def _edge(edge: str | dict | None) -> dict:
        if isinstance(edge, str):
            return {EDGE_TIME: edge}
        return edge if isinstance(edge, dict) else {}

    window = window or {}
    return {"start": _edge(window.get("start")), "end": _edge(window.get("end"))}


def _schedule_edge_fields() -> dict:
    """Start/end sections for the schedule window (expanded — both required)."""

    def _edge_section() -> section:
        return section(
            vol.Schema(
                {
                    vol.Optional(EDGE_TIME): selector.TimeSelector(),
                    vol.Optional(EDGE_SUN, default="none"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_SUN_OPTIONS, translation_key="sun_event"
                        )
                    ),
                    vol.Optional(EDGE_OFFSET, default=0): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=-720,
                            max=720,
                            step=1,
                            unit_of_measurement="min",
                            mode="box",
                        )
                    ),
                    vol.Optional(
                        EDGE_COMBINE, default=COMBINE_LATEST
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_COMBINE_OPTIONS, translation_key="combine_mode"
                        )
                    ),
                }
            ),
            {"collapsed": False},
        )

    return {
        vol.Required("start"): _edge_section(),
        vol.Required("end"): _edge_section(),
    }


# Pickers for a virtual light's optional entity references. The occupancy,
# maintain, illuminance and schedule sensors are MoLight virtual sensors
# (integration=DOMAIN); the schedule sensor has no device_class (HA offers
# none that fits), so its picker can only narrow to MoLight binary sensors.
# The door sensor is a plain real contact sensor, so its picker is not
# restricted to MoLight — only to door-ish binary_sensor device classes.
# Keep-on entities can be anything with an on/off state (input_boolean,
# switch, binary_sensor, ...), so that picker is not narrowed at all.
_LIGHT_REF_SELECTORS = {
    CONF_OCCUPANCY_ENTITY: selector.EntitySelectorConfig(
        integration=DOMAIN,
        domain="binary_sensor",
        device_class="occupancy",
        multiple=False,
    ),
    CONF_MAINTAIN_OCCUPANCY_ENTITY: selector.EntitySelectorConfig(
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
    CONF_DOOR_ENTITY: selector.EntitySelectorConfig(
        domain="binary_sensor",
        device_class=["door", "garage_door", "opening", "window"],
        multiple=False,
    ),
    CONF_HOLD_ENTITIES: selector.EntitySelectorConfig(multiple=True),
}


def _effective_occupancy_timeout(
    hass: HomeAssistant, entity_id: str, _seen: set[str] | None = None
) -> int | None:
    """Resolve the occupancy timeout (seconds) behind a MoLight occupancy entity.

    For a simple occupancy sensor this is its configured timeout; for a
    combined sensor it is the max across all constituent sensors (the
    countdown math anchors to the constituent that clears last). Combined
    sensors can reference each other (the options flow can even create
    cycles), so entities already being resolved are skipped.
    """
    if _seen is None:
        _seen = set()
    if entity_id in _seen:
        return None
    _seen.add(entity_id)
    reg_entry = er.async_get(hass).async_get(entity_id)
    if reg_entry is None or reg_entry.config_entry_id is None:
        return None
    entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None

    cfg = _molight_cfg(entry)
    entity_type = cfg.get(CONF_ENTITY_TYPE)
    if entity_type == ENTITY_TYPE_OCCUPANCY:
        return int(cfg.get(CONF_OCCUPANCY_TIMEOUT, DEFAULT_OCCUPANCY_TIMEOUT))
    if entity_type == ENTITY_TYPE_COMBINED_OCCUPANCY:
        constituents = cfg.get(CONF_TRIGGER_SENSORS, []) + cfg.get(
            CONF_MAINTAIN_SENSORS, []
        )
        timeouts = [
            t
            for e in constituents
            if (t := _effective_occupancy_timeout(hass, e, _seen)) is not None
        ]
        return max(timeouts, default=None)
    return None


def _molight_occupancy_entity_ids(hass: HomeAssistant) -> list[str]:
    """Entity ids created by MoLight occupancy and combined-occupancy entries."""
    registry = er.async_get(hass)
    entity_ids: list[str] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if _molight_cfg(entry).get(CONF_ENTITY_TYPE) not in (
            ENTITY_TYPE_OCCUPANCY,
            ENTITY_TYPE_COMBINED_OCCUPANCY,
        ):
            continue
        entity_ids.extend(
            entity.entity_id
            for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
            if entity.domain == "binary_sensor"
        )
    return sorted(entity_ids)


def _combined_occupancy_creates_cycle(
    hass: HomeAssistant,
    edited_entry: config_entries.ConfigEntry,
    proposed_constituents: list[str],
) -> bool:
    """Return True when the proposed combined-sensor references form a cycle.

    Resolve entity ids through the registry so the graph is keyed by stable config
    entry ids. Starting from the edited entry's proposed outgoing edges, a path
    back to that entry is either a direct self-reference or an indirect cycle.
    """
    registry = er.async_get(hass)
    target_entry_id = edited_entry.entry_id

    def _referenced_entries(entity_ids: list[str]) -> list[config_entries.ConfigEntry]:
        entries = []
        for entity_id in entity_ids:
            reg_entry = registry.async_get(entity_id)
            if reg_entry is None or reg_entry.config_entry_id is None:
                continue
            entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
            if entry is not None and entry.domain == DOMAIN:
                entries.append(entry)
        return entries

    pending = _referenced_entries(proposed_constituents)
    visited: set[str] = set()
    while pending:
        entry = pending.pop()
        if entry.entry_id == target_entry_id:
            return True
        if entry.entry_id in visited:
            continue
        visited.add(entry.entry_id)

        cfg = _molight_cfg(entry)
        if cfg.get(CONF_ENTITY_TYPE) != ENTITY_TYPE_COMBINED_OCCUPANCY:
            continue
        constituents = cfg.get(CONF_TRIGGER_SENSORS, []) + cfg.get(
            CONF_MAINTAIN_SENSORS, []
        )
        pending.extend(_referenced_entries(constituents))

    return False


def _combined_occupancy_cycle_candidates(
    hass: HomeAssistant, edited_entry: config_entries.ConfigEntry
) -> list[str]:
    """Entity ids that would create a cycle if selected by the edited entry."""
    registry = er.async_get(hass)
    excluded: list[str] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if _molight_cfg(entry).get(CONF_ENTITY_TYPE) != ENTITY_TYPE_COMBINED_OCCUPANCY:
            continue
        excluded.extend(
            entity.entity_id
            for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
            if entity.domain == "binary_sensor"
            and _combined_occupancy_creates_cycle(
                hass, edited_entry, [entity.entity_id]
            )
        )
    return sorted(excluded)


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

    # Combined sensors may nest other combined sensors, so expand to a
    # fixpoint rather than in a single pass.
    changed = True
    while changed:
        changed = False
        for entry in hass.config_entries.async_entries(DOMAIN):
            cfg = _molight_cfg(entry)
            if cfg.get(CONF_ENTITY_TYPE) != ENTITY_TYPE_COMBINED_OCCUPANCY:
                continue
            constituents = cfg.get(CONF_TRIGGER_SENSORS, []) + cfg.get(
                CONF_MAINTAIN_SENSORS, []
            )
            if dependent_ids.intersection(constituents):
                entry_ids = {
                    e.entity_id
                    for e in er.async_entries_for_config_entry(registry, entry.entry_id)
                }
                if not entry_ids <= dependent_ids:
                    dependent_ids.update(entry_ids)
                    changed = True

    timeouts = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        cfg = _molight_cfg(entry)
        settings_sets = []
        if cfg.get(CONF_ENTITY_TYPE) == ENTITY_TYPE_LIGHT:
            settings_sets.append(cfg)
        elif cfg.get(CONF_ENTITY_TYPE) == ENTITY_TYPE_SCHEDULED_LIGHT:
            settings_sets.extend(
                (
                    cfg.get(CONF_OUTSIDE_SCHEDULE_SETTINGS, {}),
                    cfg.get(CONF_INSIDE_SCHEDULE_SETTINGS, {}),
                )
            )
        timeouts.extend(
            int(settings.get(CONF_LIGHT_TIMEOUT, DEFAULT_LIGHT_TIMEOUT))
            for settings in settings_sets
            if settings.get(CONF_OCCUPANCY_ENTITY) in dependent_ids
            or settings.get(CONF_MAINTAIN_OCCUPANCY_ENTITY) in dependent_ids
        )
    return min(timeouts, default=None)


def _validate_light_timeout(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, str]:
    """Check light_timeout >= any referenced occupancy entity's timeout.

    The maintain entity's latest_occupied_time feeds the countdown the same
    way the regular occupancy entity's does, so both are checked.
    """
    for key in (CONF_OCCUPANCY_ENTITY, CONF_MAINTAIN_OCCUPANCY_ENTITY):
        entity_id = user_input.get(key)
        if not entity_id:
            continue
        occ_timeout = _effective_occupancy_timeout(hass, entity_id)
        if (
            occ_timeout is not None
            and int(user_input[CONF_LIGHT_TIMEOUT]) < occ_timeout
        ):
            return {CONF_LIGHT_TIMEOUT: "light_timeout_too_short"}
    return {}


def _validate_turn_on_selection(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, str]:
    """Validate and normalize a Virtual Light's optional turn-on selection."""
    entity_id = user_input.get(CONF_TURN_ON_SELECT_ENTITY)
    option = str(user_input.get(CONF_TURN_ON_SELECT_OPTION) or "").strip()
    source_entity = user_input.get(CONF_TURN_ON_SELECT_SOURCE_ENTITY)

    if not entity_id and not option and not source_entity:
        user_input.pop(CONF_TURN_ON_SELECT_ENTITY, None)
        user_input.pop(CONF_TURN_ON_SELECT_OPTION, None)
        user_input.pop(CONF_TURN_ON_SELECT_SOURCE_ENTITY, None)
        return {}
    if not entity_id or not option:
        return {"base": "turn_on_selection_incomplete"}
    if source_entity == entity_id:
        return {
            CONF_TURN_ON_SELECT_SOURCE_ENTITY: (
                "turn_on_selection_source_same_as_target"
            )
        }

    user_input[CONF_TURN_ON_SELECT_OPTION] = option
    target_state = hass.states.get(entity_id)
    target_options = (
        target_state.attributes.get(ATTR_OPTIONS) if target_state is not None else None
    )
    if isinstance(target_options, (list, tuple)) and option not in target_options:
        return {"base": "turn_on_selection_invalid_option"}

    if source_entity:
        source_state = hass.states.get(source_entity)
        source_options = (
            source_state.attributes.get(ATTR_OPTIONS)
            if source_state is not None
            else None
        )
        if (
            isinstance(target_options, (list, tuple))
            and isinstance(source_options, (list, tuple))
            and not any(value in target_options for value in source_options)
        ):
            return {
                CONF_TURN_ON_SELECT_SOURCE_ENTITY: (
                    "turn_on_selection_source_no_matching_options"
                )
            }
    return {}


def _validate_combined_occupancy_roles(user_input: dict[str, Any]) -> dict[str, str]:
    """Reject constituents assigned to both trigger and maintain roles."""
    triggers = set(user_input.get(CONF_TRIGGER_SENSORS, []))
    maintains = set(user_input.get(CONF_MAINTAIN_SENSORS, []))
    if triggers & maintains:
        return {"base": "occupancy_sensor_role_overlap"}
    return {}


def _validate_occupancy_source(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, str]:
    """Reject wrapping another MoLight-created occupancy entity."""
    if user_input.get(CONF_OCCUPANCY_SENSOR) in _molight_occupancy_entity_ids(hass):
        return {CONF_OCCUPANCY_SENSOR: "occupancy_source_molight"}
    return {}


def _validate_stage_transitions(user_input: dict[str, Any]) -> dict[str, str]:
    """Check each stage fade fits inside its stage: transition <= timeout.

    A disabled stage has timeout 0, so setting a fade for it fails the same
    rule rather than being silently ignored. The offending fields live inside
    a collapsed section, where the frontend can't anchor a field error, so the
    first violation is reported as a base error.
    """
    for transition_key, timeout_key, error in (
        (CONF_EFFECT_TRANSITION, CONF_EFFECT_TIMEOUT, "effect_transition_too_long"),
        (CONF_WARN_TRANSITION, CONF_WARN_TIMEOUT, "warn_transition_too_long"),
    ):
        transition = user_input.get(transition_key)
        if transition is not None and float(transition) > float(
            user_input.get(timeout_key, 0)
        ):
            return {"base": error}
    return {}


def _validate_remote(hass: HomeAssistant, cfg: dict[str, Any]) -> dict[str, str]:
    """Check a Virtual Remote form: targets, binding conflicts, presets.

    The button pickers live inside sections, where the frontend can't anchor
    a field error, so most violations are reported as base errors.
    """
    if not cfg.get(CONF_TARGET_LIGHTS):
        return {CONF_TARGET_LIGHTS: "target_lights_required"}
    seen: set[tuple[str, str]] = set()
    bound = False
    for single_key, double_key, _action in REMOTE_ACTION_FIELDS:
        for key, click in ((single_key, CLICK_SINGLE), (double_key, CLICK_DOUBLE)):
            for entity_id in cfg.get(key) or []:
                bound = True
                if (entity_id, click) in seen:
                    return {"base": "button_click_conflict"}
                seen.add((entity_id, click))
                if (
                    click == CLICK_DOUBLE
                    and entity_double_click_supported(hass, entity_id) is False
                ):
                    return {"base": "double_click_unsupported"}
    if not bound:
        return {"base": "buttons_required"}
    for single_key, double_key, action in REMOTE_ACTION_FIELDS:
        value_keys = REMOTE_PRESET_VALUE_KEYS.get(action)
        if value_keys is None:
            continue
        _brightness_key, color_temp_key, rgb_key = value_keys
        if cfg.get(color_temp_key) and cfg.get(rgb_key):
            return {"base": "preset_color_conflict"}
        has_buttons = cfg.get(single_key) or cfg.get(double_key)
        has_values = any(cfg.get(k) not in (None, []) for k in value_keys)
        if has_buttons and not has_values:
            return {"base": "preset_values_required"}
    return {}


def _validate_colors(user_input: dict[str, Any]) -> dict[str, str]:
    """Check the optional color/brightness stage fields are coherent.

    The auto-on color temp and rgb color are mutually exclusive (a turn-on
    can only carry one color), an effect color needs a visible effect stage
    (effect_brightness > 0 — a blink fully off has no color to show), and a
    stage brightness/color on a disabled stage (timeout 0) is rejected rather
    than silently ignored, mirroring the stage-fade rule. Reported as base
    errors: the fields live inside collapsed sections, where the frontend
    can't anchor a field error.
    """
    if user_input.get(CONF_AUTO_ON_COLOR_TEMP) and user_input.get(
        CONF_AUTO_ON_RGB_COLOR
    ):
        return {"base": "auto_on_color_conflict"}
    if user_input.get(CONF_EFFECT_RGB_COLOR) and not int(
        user_input.get(CONF_EFFECT_BRIGHTNESS) or 0
    ):
        return {"base": "effect_color_requires_brightness"}
    if user_input.get(CONF_EFFECT_RGB_COLOR) and not float(
        user_input.get(CONF_EFFECT_TIMEOUT) or 0
    ):
        return {"base": "effect_color_requires_timeout"}
    if (
        user_input.get(CONF_WARN_BRIGHTNESS) or user_input.get(CONF_WARN_RGB_COLOR)
    ) and not float(user_input.get(CONF_WARN_TIMEOUT) or 0):
        return {"base": "warn_values_require_timeout"}
    return {}


def _validate_light_settings(
    hass: HomeAssistant, settings: dict[str, Any]
) -> dict[str, str]:
    """Validate one flat Virtual Light settings mapping."""
    errors = _validate_light_timeout(hass, settings)
    errors.update(_validate_stage_transitions(settings))
    errors.update(_validate_colors(settings))
    return errors


def _clean_optional_values(values: dict[str, Any]) -> dict[str, Any]:
    """Drop cleared optional form values before storing a settings mapping."""
    return {key: value for key, value in values.items() if value is not None}


def _carry_turn_on_selection(
    values: dict[str, Any], previous: dict[str, Any] | None
) -> None:
    """Keep hidden selection-step values while its target stays unchanged."""
    if not previous or values.get(CONF_TURN_ON_SELECT_ENTITY) != previous.get(
        CONF_TURN_ON_SELECT_ENTITY
    ):
        return
    for key in (CONF_TURN_ON_SELECT_OPTION, CONF_TURN_ON_SELECT_SOURCE_ENTITY):
        if key in previous:
            values[key] = previous[key]


# ---------------------------------------------------------------------------
# Shared option-field schemas
#
# The editable settings of each entity type, with their creation-time defaults
# (optional fields blank). Used both by the manual create steps and by the
# discovery "adjust defaults" steps, so the two stay in sync. Name, entity_id
# and the wrapped source entity are added by each caller, not here.
# ---------------------------------------------------------------------------


def _occupancy_option_fields(*, with_entity_id: bool = False) -> dict:
    """Occupancy settings: the timeout up front, expert knobs under Advanced."""
    advanced: dict = {
        vol.Required(
            CONF_FALSE_DETECTION_GRACE, default=DEFAULT_FALSE_DETECTION_GRACE
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
    if with_entity_id:
        advanced[vol.Optional(CONF_ENTITY_ID)] = selector.TextSelector()
    return {
        vol.Required(
            CONF_OCCUPANCY_TIMEOUT, default=DEFAULT_OCCUPANCY_TIMEOUT
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=3600, unit_of_measurement="s", mode="box"
            )
        ),
        vol.Required(SECTION_ADVANCED): section(
            vol.Schema(advanced), {"collapsed": True}
        ),
    }


def _illuminance_option_fields() -> dict:
    return {
        vol.Required(
            CONF_ILLUMINANCE_THRESHOLD, default=DEFAULT_ILLUMINANCE_THRESHOLD
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=100000, step=0.1, unit_of_measurement="lx", mode="box"
            )
        ),
        vol.Required(
            CONF_ILLUMINANCE_HYSTERESIS, default=DEFAULT_ILLUMINANCE_HYSTERESIS
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=10000, step=0.1, unit_of_measurement="lx", mode="box"
            )
        ),
    }


def _light_option_fields(
    *, with_entity_id: bool = False, with_schedule: bool = True
) -> dict:
    """Sectioned settings of a virtual light.

    Only the turn-off timeout stays top-level; everything else is grouped:
    turn-on/off behavior and the off-warning stages collapsed (defaults are
    fine to start with), the sensor wiring expanded since it is the point of
    a virtual light. Each mode dropdown sits next to its entity picker.
    """
    sensor_fields: dict = {
        vol.Optional(CONF_OCCUPANCY_ENTITY): selector.EntitySelector(
            _LIGHT_REF_SELECTORS[CONF_OCCUPANCY_ENTITY]
        ),
        vol.Optional(CONF_MAINTAIN_OCCUPANCY_ENTITY): selector.EntitySelector(
            _LIGHT_REF_SELECTORS[CONF_MAINTAIN_OCCUPANCY_ENTITY]
        ),
        vol.Optional(CONF_ILLUMINANCE_ENTITY): selector.EntitySelector(
            _LIGHT_REF_SELECTORS[CONF_ILLUMINANCE_ENTITY]
        ),
        vol.Required(
            CONF_ILLUMINANCE_MODE, default=DEFAULT_ILLUMINANCE_MODE
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=ILLUMINANCE_MODES,
                translation_key=CONF_ILLUMINANCE_MODE,
            )
        ),
    }
    if with_schedule:
        sensor_fields.update(
            {
                vol.Optional(CONF_SCHEDULE_ENTITY): selector.EntitySelector(
                    _LIGHT_REF_SELECTORS[CONF_SCHEDULE_ENTITY]
                ),
                vol.Required(
                    CONF_SCHEDULE_MODE, default=DEFAULT_SCHEDULE_MODE
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=SCHEDULE_MODES,
                        translation_key=CONF_SCHEDULE_MODE,
                    )
                ),
            }
        )
    sensor_fields.update(
        {
            vol.Optional(CONF_DOOR_ENTITY): selector.EntitySelector(
                _LIGHT_REF_SELECTORS[CONF_DOOR_ENTITY]
            ),
            vol.Required(CONF_DOOR_MODE, default=DEFAULT_DOOR_MODE): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=DOOR_MODES, translation_key=CONF_DOOR_MODE
                    )
                )
            ),
            vol.Optional(CONF_HOLD_ENTITIES): selector.EntitySelector(
                _LIGHT_REF_SELECTORS[CONF_HOLD_ENTITIES]
            ),
        }
    )

    fields: dict = {
        vol.Required(
            CONF_LIGHT_TIMEOUT, default=DEFAULT_LIGHT_TIMEOUT
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=14400, unit_of_measurement="s", mode="box"
            )
        ),
        vol.Required(SECTION_SENSORS): section(
            vol.Schema(sensor_fields),
            {"collapsed": False},
        ),
        vol.Required(SECTION_BEHAVIOR): section(
            vol.Schema(
                {
                    vol.Required(
                        CONF_FALSE_OFF_DELAY, default=DEFAULT_FALSE_OFF_DELAY
                    ): (
                        selector.NumberSelector(
                            selector.NumberSelectorConfig(
                                min=0, max=300, unit_of_measurement="s", mode="box"
                            )
                        )
                    ),
                    vol.Optional(CONF_AUTO_ON_BRIGHTNESS): _AUTO_ON_BRIGHTNESS_SELECTOR,
                    vol.Optional(CONF_AUTO_ON_COLOR_TEMP): _COLOR_TEMP_SELECTOR,
                    vol.Optional(CONF_AUTO_ON_RGB_COLOR): _RGB_COLOR_SELECTOR,
                    vol.Optional(CONF_TURN_ON_SELECT_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="select", multiple=False)
                    ),
                    vol.Optional(CONF_AUTO_ON_TRANSITION): _TRANSITION_SELECTOR,
                    vol.Optional(CONF_AUTO_OFF_TRANSITION): _TRANSITION_SELECTOR,
                }
            ),
            {"collapsed": True},
        ),
        vol.Required(SECTION_WARNING): section(
            vol.Schema(
                {
                    vol.Required(
                        CONF_EFFECT_TIMEOUT, default=DEFAULT_EFFECT_TIMEOUT
                    ): _STAGE_TIMEOUT_SELECTOR,
                    vol.Required(
                        CONF_EFFECT_BRIGHTNESS, default=DEFAULT_EFFECT_BRIGHTNESS
                    ): _EFFECT_BRIGHTNESS_SELECTOR,
                    vol.Optional(CONF_EFFECT_RGB_COLOR): _RGB_COLOR_SELECTOR,
                    vol.Optional(CONF_EFFECT_TRANSITION): _TRANSITION_SELECTOR,
                    vol.Required(
                        CONF_WARN_TIMEOUT, default=DEFAULT_WARN_TIMEOUT
                    ): _STAGE_TIMEOUT_SELECTOR,
                    vol.Optional(CONF_WARN_BRIGHTNESS): _AUTO_ON_BRIGHTNESS_SELECTOR,
                    vol.Optional(CONF_WARN_RGB_COLOR): _RGB_COLOR_SELECTOR,
                    vol.Optional(CONF_WARN_TRANSITION): _TRANSITION_SELECTOR,
                }
            ),
            {"collapsed": True},
        ),
    }
    if with_entity_id:
        fields.update(_entity_id_section())
    return fields


def _turn_on_selection_fields(target_entity: str) -> dict:
    """Fields shown after a Virtual Light's target select is known."""
    return {
        vol.Optional(CONF_TURN_ON_SELECT_SOURCE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["input_select", "select"],
                exclude_entities=[target_entity],
                multiple=False,
            )
        ),
        vol.Required(CONF_TURN_ON_SELECT_OPTION): selector.StateSelector(
            selector.StateSelectorConfig(
                entity_id=target_entity,
                hide_states=[STATE_UNAVAILABLE, STATE_UNKNOWN],
                multiple=False,
            )
        ),
    }


def _remote_top_fields() -> dict:
    """Top-level Virtual Remote fields: the target lights and the dim step."""
    return {
        vol.Required(CONF_TARGET_LIGHTS): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="light", multiple=True)
        ),
        vol.Required(CONF_DIM_STEP, default=DEFAULT_DIM_STEP): (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=50, step=1, unit_of_measurement="%", mode="box"
                )
            )
        ),
    }


def _remote_option_fields() -> dict:
    """Sectioned button bindings of a Virtual Remote entry.

    One collapsible section per bindable action, each holding a single- and a
    double-click button multi-picker (multiple remotes can drive one room);
    the presets add their brightness/color values. Turn on and turn off start
    expanded as the common case, everything else collapsed.
    """

    def _event_buttons_selector() -> selector.EntitySelector:
        return selector.EntitySelector(
            selector.EntitySelectorConfig(domain="event", multiple=True)
        )

    expanded = {REMOTE_ACTION_ON, REMOTE_ACTION_OFF}
    fields: dict = {}
    for single_key, double_key, action in REMOTE_ACTION_FIELDS:
        inner: dict = {
            vol.Optional(single_key): _event_buttons_selector(),
            vol.Optional(double_key): _event_buttons_selector(),
        }
        if value_keys := REMOTE_PRESET_VALUE_KEYS.get(action):
            brightness_key, color_temp_key, rgb_key = value_keys
            inner[vol.Optional(brightness_key)] = _AUTO_ON_BRIGHTNESS_SELECTOR
            inner[vol.Optional(color_temp_key)] = _COLOR_TEMP_SELECTOR
            inner[vol.Optional(rgb_key)] = _RGB_COLOR_SELECTOR
        fields[vol.Required(action)] = section(
            vol.Schema(inner), {"collapsed": action not in expanded}
        )
    return fields


# ---------------------------------------------------------------------------
# Discovery — scan real entities and bulk-create virtual entities for them
# ---------------------------------------------------------------------------


def _molight_used_entities(hass: HomeAssistant, key: str) -> set[str]:
    """Real entity_ids already wrapped by existing MoLight entries under `key`."""
    used: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        val = _molight_cfg(entry).get(key)
        if isinstance(val, list):
            used.update(val)
        elif val:
            used.add(val)
    return used


def _discovery_candidates(
    hass: HomeAssistant,
    domain: str,
    device_classes: set[str] | None,
    used_key: str,
) -> dict[str, str]:
    """Candidate real entities to wrap, mapped entity_id -> friendly name.

    Includes entities of the given domain whose (registry-overridden) device
    class is in `device_classes` (None matches any), excluding: MoLight's own
    virtual entities, disabled ones, and any already referenced by an existing
    MoLight entry under `used_key`. Registry entries are the source of truth
    for device_class; entities that only exist as a state (never registered)
    are matched on their attribute.
    """
    registry = er.async_get(hass)
    used = _molight_used_entities(hass, used_key)
    candidates: dict[str, str] = {}
    # Track every registered entity of this domain up front — including
    # MoLight's own virtual entities and disabled ones — so the state-based
    # fallback below can't re-offer something the registry already excludes.
    seen: set[str] = set()
    for ent in registry.entities.values():
        if ent.domain != domain:
            continue
        seen.add(ent.entity_id)
        if ent.platform == DOMAIN or ent.disabled:
            continue
        dc = ent.device_class or ent.original_device_class
        if (
            device_classes is None or dc in device_classes
        ) and ent.entity_id not in used:
            state = hass.states.get(ent.entity_id)
            candidates[ent.entity_id] = (
                (state.name if state else None)
                or ent.name
                or ent.original_name
                or ent.entity_id
            )
    for state in hass.states.async_all(domain):
        if state.entity_id in seen or state.entity_id in used:
            continue
        if (
            device_classes is None
            or state.attributes.get("device_class") in device_classes
        ):
            candidates[state.entity_id] = state.name
    return candidates


def _filter_discovery_candidates(
    hass: HomeAssistant,
    candidates: dict[str, str],
    areas: list[str],
    labels: list[str],
) -> dict[str, str]:
    """Narrow discovery candidates to the given areas and/or labels.

    An empty filter matches everything. With both set, a candidate must match
    one of the areas AND carry one of the labels. Area and labels come from
    the entity's registry entry, falling back to (for areas) or merged with
    (for labels) its device's; entities that only exist as a state can't
    carry either, so any active filter excludes them.
    """
    if not areas and not labels:
        return candidates
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    filtered: dict[str, str] = {}
    for entity_id, name in candidates.items():
        ent = registry.async_get(entity_id)
        if ent is None:
            continue
        device = devices.async_get(ent.device_id) if ent.device_id else None
        if areas:
            area = ent.area_id or (device.area_id if device else None)
            if area not in areas:
                continue
        if labels:
            ent_labels = set(ent.labels) | (set(device.labels) if device else set())
            if not ent_labels.intersection(labels):
                continue
        filtered[entity_id] = name
    return filtered


def _occupancy_payload(entity_id: str, name: str) -> dict[str, Any]:
    return {
        CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
        CONF_NAME: name,
        CONF_OCCUPANCY_SENSOR: entity_id,
        CONF_OCCUPANCY_TIMEOUT: DEFAULT_OCCUPANCY_TIMEOUT,
        CONF_FALSE_DETECTION_GRACE: DEFAULT_FALSE_DETECTION_GRACE,
        CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT: DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    }


def _illuminance_payload(entity_id: str, name: str) -> dict[str, Any]:
    return {
        CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
        CONF_NAME: name,
        CONF_ILLUMINANCE_SENSOR: entity_id,
        CONF_ILLUMINANCE_THRESHOLD: DEFAULT_ILLUMINANCE_THRESHOLD,
        CONF_ILLUMINANCE_HYSTERESIS: DEFAULT_ILLUMINANCE_HYSTERESIS,
    }


def _light_payload(entity_id: str, name: str) -> dict[str, Any]:
    # Wraps a single real light with defaults and no entity references; the
    # user wires up occupancy/illuminance/schedule afterwards via options.
    return {
        CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
        CONF_NAME: name,
        CONF_LIGHTS: [entity_id],
        CONF_LIGHT_TIMEOUT: DEFAULT_LIGHT_TIMEOUT,
        CONF_FALSE_OFF_DELAY: DEFAULT_FALSE_OFF_DELAY,
        CONF_ILLUMINANCE_MODE: DEFAULT_ILLUMINANCE_MODE,
        CONF_SCHEDULE_MODE: DEFAULT_SCHEDULE_MODE,
        CONF_DOOR_MODE: DEFAULT_DOOR_MODE,
    }


def _molight_light_entries(
    hass: HomeAssistant, entity_types: tuple[str, ...] = (ENTITY_TYPE_LIGHT,)
) -> dict[str, config_entries.ConfigEntry]:
    """Map each virtual light's entity_id to its config entry.

    Only entries that have actually registered a light entity appear — the
    entity_id is what the bulk-assign light picker stores, and what a light's
    sensor references are keyed against.
    """
    registry = er.async_get(hass)
    result: dict[str, config_entries.ConfigEntry] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if _molight_cfg(entry).get(CONF_ENTITY_TYPE) not in entity_types:
            continue
        for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
            if ent.domain == "light":
                result[ent.entity_id] = entry
                break
    return result


def _conversion_eligible(cfg: dict[str, Any], *, to_scheduled: bool) -> bool:
    """Return True when a light config may be converted in the given direction.

    Gated → scheduled needs a regular light with a schedule in any gate
    mode; scheduled → gated needs a scheduled light with a schedule.
    """
    if not cfg.get(CONF_SCHEDULE_ENTITY):
        return False
    if to_scheduled:
        return cfg.get(CONF_ENTITY_TYPE) == ENTITY_TYPE_LIGHT and cfg.get(
            CONF_SCHEDULE_MODE, DEFAULT_SCHEDULE_MODE
        ) in (
            SCHEDULE_MODE_GATE,
            SCHEDULE_MODE_GATE_SWITCH,
            SCHEDULE_MODE_GATE_KEEP,
        )
    return cfg.get(CONF_ENTITY_TYPE) == ENTITY_TYPE_SCHEDULED_LIGHT


_SCHEDULED_LIGHT_SIDES = (CONF_OUTSIDE_SCHEDULE_SETTINGS, CONF_INSIDE_SCHEDULE_SETTINGS)
# Keys that live at the top level of a light entry (identity, members, the
# schedule and its policy, the two profiles) rather than inside a settings
# profile; conversion strips them when lifting a flat config into a profile
# and when flattening a profile back out.
_LIGHT_ENTRY_KEYS = frozenset(
    {
        CONF_ENTITY_TYPE,
        CONF_ENTITY_ID,
        CONF_NAME,
        CONF_LIGHTS,
        CONF_SCHEDULE_ENTITY,
        CONF_SCHEDULE_MODE,
        CONF_SCHEDULE_END_ACTION,
        *_SCHEDULED_LIGHT_SIDES,
    }
)
_SCHEDULED_LIGHT_STEP_IDS = {
    CONF_OUTSIDE_SCHEDULE_SETTINGS: "scheduled_light_outside",
    CONF_INSIDE_SCHEDULE_SETTINGS: "scheduled_light_inside",
}


class _ScheduledLightSettingsSteps:
    """The Virtual Scheduled Light's outside/inside settings forms.

    Shared by the config and options flows, which differ only in the shared
    first form and in how the flow ends. Each side gets the regular Virtual
    Light settings form (minus the schedule fields) and, when it picks a
    turn-on selection target, the same target-dependent selection page.

    Subclasses provide `_scheduled_light_defaults(side)` — what a side's form
    starts from before anything has been entered for it — and an async
    `_finish_scheduled_light()`.
    """

    hass: HomeAssistant

    def _init_scheduled_light_steps(self) -> None:
        """Reset the per-side settings stash and the pending selection side."""
        self._scheduled_light_settings: dict[str, dict[str, Any] | None] = (
            dict.fromkeys(_SCHEDULED_LIGHT_SIDES)
        )
        self._scheduled_light_pending_side: str | None = None

    def _scheduled_light_defaults(self, side: str) -> dict[str, Any] | None:
        """Return what a side's form starts from before anything was entered."""
        raise NotImplementedError

    async def _finish_scheduled_light(self) -> config_entries.FlowResult:
        """Complete the flow once both sides are stored."""
        raise NotImplementedError

    def _scheduled_light_previous(self, side: str) -> dict[str, Any] | None:
        """Return a side's settings so far: its stash, else its defaults."""
        return self._scheduled_light_settings[side] or self._scheduled_light_defaults(
            side
        )

    async def _scheduled_light_next(self, side: str) -> config_entries.FlowResult:
        """Move on once a side is complete: outside → inside form → finish."""
        if side == CONF_OUTSIDE_SCHEDULE_SETTINGS:
            return await self.async_step_scheduled_light_inside()
        return await self._finish_scheduled_light()

    async def _scheduled_light_settings_step(
        self, side: str, user_input: dict[str, Any] | None
    ) -> config_entries.FlowResult:
        """Show or process one side's Virtual Light settings form."""
        errors: dict[str, str] = {}
        previous = self._scheduled_light_previous(side)
        if user_input is not None:
            flat = _flatten_sections(user_input, _LIGHT_SECTIONS)
            errors = _validate_light_settings(self.hass, flat)
            if not errors:
                _carry_turn_on_selection(flat, previous)
                if flat.get(CONF_TURN_ON_SELECT_ENTITY):
                    self._scheduled_light_settings[side] = _clean_optional_values(flat)
                    self._scheduled_light_pending_side = side
                    return await self.async_step_scheduled_light_selection()
                # No selection target: drop any stale option/source before storing.
                _validate_turn_on_selection(self.hass, flat)
                self._scheduled_light_settings[side] = _clean_optional_values(flat)
                return await self._scheduled_light_next(side)

        return self.async_show_form(
            step_id=_SCHEDULED_LIGHT_STEP_IDS[side],
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_light_option_fields(with_schedule=False)),
                user_input or _nest_sections(previous or {}, _LIGHT_SECTIONS),
            ),
            errors=errors,
        )

    async def async_step_scheduled_light_outside(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure the settings used while the schedule is off."""
        return await self._scheduled_light_settings_step(
            CONF_OUTSIDE_SCHEDULE_SETTINGS, user_input
        )

    async def async_step_scheduled_light_inside(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure the settings used while the schedule is on."""
        return await self._scheduled_light_settings_step(
            CONF_INSIDE_SCHEDULE_SETTINGS, user_input
        )

    async def async_step_scheduled_light_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose the turn-on selection values for the pending side."""
        side = self._scheduled_light_pending_side
        flat = dict(self._scheduled_light_settings[side])
        errors: dict[str, str] = {}
        if user_input is not None:
            flat.update(user_input)
            errors = _validate_turn_on_selection(self.hass, flat)
            if not errors:
                self._scheduled_light_settings[side] = _clean_optional_values(flat)
                return await self._scheduled_light_next(side)

        target = flat[CONF_TURN_ON_SELECT_ENTITY]
        suggested = {
            key: flat[key]
            for key in (
                CONF_TURN_ON_SELECT_OPTION,
                CONF_TURN_ON_SELECT_SOURCE_ENTITY,
            )
            if key in flat
        }
        return self.async_show_form(
            step_id="scheduled_light_selection",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_turn_on_selection_fields(target)),
                user_input or suggested,
            ),
            errors=errors,
            description_placeholders={"entity_id": target},
        )


class MoLightConfigFlow(
    _ScheduledLightSettingsSteps, config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for MoLight."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow's cross-step stash."""
        self._entity_type: str | None = None
        # Set when a manual create step is re-entered from the entity_id
        # confirm step's "change" option, so the form comes back prefilled.
        self._prefill: dict[str, Any] | None = None
        # Stashed create payload while the entity_id confirm step is shown.
        self._pending: dict[str, Any] | None = None
        # Stashed Virtual Light form while its target-dependent turn-on
        # selection step is shown. Selection values are kept separately so a
        # trip through the entity-id collision menu can prefill them again.
        self._light_pending: dict[str, Any] | None = None
        self._light_selection_values: dict[str, Any] = {}
        # Cross-step state for the Virtual Scheduled Light's shared form; the
        # per-side settings stash lives in _ScheduledLightSettingsSteps.
        self._scheduled_light_shared: dict[str, Any] | None = None
        self._scheduled_light_shared_input: dict[str, Any] | None = None
        self._init_scheduled_light_steps()
        # Stashed sensor/role/mode between the two bulk-assign steps.
        self._assign: dict[str, Any] = {}
        # Direction and selected entry ids awaiting a bulk-conversion review.
        self._conversion: dict[str, Any] = {}
        # Stashed discovery state across the three discovery steps: the
        # filter step sets the (narrowed) candidate map and preselect choice,
        # the select step adds the selection and affixes, and the "adjust
        # defaults" step consumes it all in _finish_discovery.
        self._discovery: dict[str, Any] = {}

    @staticmethod
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> MoLightOptionsFlow:
        """Create the options flow for an existing entry."""
        return MoLightOptionsFlow(entry)

    def _create_step(self, entity_type: str) -> _StepHandler:
        """Map an entity type to its manual create step handler."""
        return {
            ENTITY_TYPE_OCCUPANCY: self.async_step_occupancy,
            ENTITY_TYPE_COMBINED_OCCUPANCY: self.async_step_combined_occupancy,
            ENTITY_TYPE_ILLUMINANCE: self.async_step_illuminance,
            ENTITY_TYPE_SCHEDULE: self.async_step_schedule,
            ENTITY_TYPE_LIGHT: self.async_step_light,
            ENTITY_TYPE_SCHEDULED_LIGHT: self.async_step_scheduled_light,
            ENTITY_TYPE_REMOTE: self.async_step_remote,
        }[entity_type]

    # ------------------------------------------------------------------
    # Optional explicit entity_id (manual create steps)
    # ------------------------------------------------------------------

    def _entity_id_taken(self, entity_id: str) -> bool:
        """Return True if entity_id is already registered or has a live state."""
        return (
            er.async_get(self.hass).async_get(entity_id) is not None
            or self.hass.states.get(entity_id) is not None
        )

    def _resolve_entity_id(
        self, name: str, user_input: dict[str, Any], entity_id_format: str
    ) -> tuple[str | None, dict[str, str], bool, str]:
        """Resolve the optional entity_id field of a manual create step.

        Returns (object_id, errors, needs_confirm, candidate):
          object_id     — slug to store in CONF_ENTITY_ID, or None to derive it
          errors        — {"base": "entity_id_conflict"} on an explicit clash
                          (base, not field: the entity_id field sits inside a
                          collapsed section where a field error can't anchor);
                          the caller re-shows the form
          needs_confirm — True when blank and the name-derived id already
                          exists (divert to the confirm step)
          candidate     — the would-be entity_id, for the confirm message
        """
        explicit = (user_input.get(CONF_ENTITY_ID) or "").strip()
        if explicit:
            # Tolerate a typed domain prefix (e.g. "light.kitchen").
            obj = slugify(explicit.split(".")[-1])
            candidate = entity_id_format.format(obj)
            if obj and self._entity_id_taken(candidate):
                return None, {"base": "entity_id_conflict"}, False, candidate
            return (obj or None), {}, False, candidate
        candidate = entity_id_format.format(slugify(name))
        if self._entity_id_taken(candidate):
            return None, {}, True, candidate
        return None, {}, False, candidate

    async def _resolve_and_create(
        self,
        *,
        entity_type: str,
        name: str,
        data: dict[str, Any],
        prefill: dict[str, Any],
        entity_id_format: str,
    ) -> tuple[config_entries.FlowResult | None, dict[str, str]]:
        """Finalize a manual create: create the entry, or divert to confirm.

        `data` is the flat entry payload (sections already flattened);
        `prefill` is the raw, still-nested form input, stashed so the
        "go back and change" path can re-show the form as submitted.
        Returns (result, errors). When errors is non-empty the caller re-shows
        its form; otherwise result is the FlowResult to return.
        """
        obj, errors, needs_confirm, candidate = self._resolve_entity_id(
            name, data, entity_id_format
        )
        if errors:
            return None, errors
        data = {k: v for k, v in data.items() if k != CONF_ENTITY_ID}
        if obj:
            data[CONF_ENTITY_ID] = obj
        if needs_confirm:
            self._pending = {
                "entity_type": entity_type,
                "name": name,
                "data": data,
                "user_input": prefill,
                "candidate": candidate,
            }
            return await self.async_step_confirm_entity_id(), {}
        return self.async_create_entry(title=name, data=data), {}

    async def async_step_confirm_entity_id(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Warn that a blank entity_id will collide; let the user choose."""
        return self.async_show_menu(
            step_id="confirm_entity_id",
            menu_options=["entity_id_proceed", "entity_id_change"],
            description_placeholders={"entity_id": self._pending["candidate"]},
        )

    async def async_step_entity_id_proceed(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Create with the name-derived id (Home Assistant appends _2)."""
        pending = self._pending
        return self.async_create_entry(title=pending["name"], data=pending["data"])

    async def async_step_entity_id_change(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Go back to the create step, prefilled, to set an entity_id."""
        self._prefill = self._pending["user_input"]
        self._entity_type = self._pending["entity_type"]
        return await self._create_step(self._entity_type)()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1 — create one entity manually, or discover many at once."""
        return self.async_show_menu(
            step_id="user",
            menu_options=[
                "create",
                "discover_occupancy",
                "discover_illuminance",
                "discover_light",
                "assign_sensor",
                "convert_lights",
            ],
        )

    async def async_step_create(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose which kind of virtual entity to create manually."""
        if user_input is not None:
            self._entity_type = user_input[CONF_ENTITY_TYPE]
            return await self._create_step(self._entity_type)()

        return self.async_show_form(
            step_id="create",
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
                                ENTITY_TYPE_SCHEDULED_LIGHT,
                                ENTITY_TYPE_REMOTE,
                            ],
                            translation_key=CONF_ENTITY_TYPE,
                        )
                    )
                }
            ),
        )

    # ------------------------------------------------------------------
    # Discovery — bulk-create from scanned real entities
    # ------------------------------------------------------------------

    async def _async_discovery_filter(
        self,
        *,
        step_id: str,
        domain: str,
        device_classes: set[str] | None,
        used_key: str,
        select_step: _StepHandler,
        user_input: dict[str, Any] | None,
    ) -> config_entries.FlowResult:
        """Optionally narrow the discovery candidates before the checklist.

        Areas and labels each match through the entity's own registry
        assignment or its device's; leaving both empty offers every
        candidate. The preselect toggle decides whether the next step's
        checklist starts fully checked (bulk-add) or empty (pick a few).
        """
        candidates = _discovery_candidates(self.hass, domain, device_classes, used_key)
        if not candidates:
            return self.async_abort(reason="no_candidates")

        errors: dict[str, str] = {}
        if user_input is not None:
            filtered = _filter_discovery_candidates(
                self.hass,
                candidates,
                user_input.get(CONF_FILTER_AREAS, []),
                user_input.get(CONF_FILTER_LABELS, []),
            )
            if filtered:
                self._discovery = {
                    "candidates": filtered,
                    "preselect_all": user_input.get(CONF_PRESELECT_ALL, True),
                }
                return await select_step()
            errors["base"] = "no_filter_matches"

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Optional(
                            CONF_FILTER_AREAS, default=[]
                        ): selector.AreaSelector(
                            selector.AreaSelectorConfig(multiple=True)
                        ),
                        vol.Optional(
                            CONF_FILTER_LABELS, default=[]
                        ): selector.LabelSelector(
                            selector.LabelSelectorConfig(multiple=True)
                        ),
                        vol.Required(
                            CONF_PRESELECT_ALL, default=True
                        ): selector.BooleanSelector(),
                    }
                ),
                user_input or {},
            ),
            errors=errors,
        )

    async def _async_discovery_select(
        self,
        *,
        step_id: str,
        defaults_step: _StepHandler,
        user_input: dict[str, Any] | None,
    ) -> config_entries.FlowResult:
        """Show a checklist of candidate entities, then adjust their defaults.

        Candidates and the preselect choice were stashed by the filter step.
        On submit the selection and affix choices are stashed and the flow
        moves to the type's defaults step, which lets the user edit the
        settings applied to every pick before the bulk-create runs.
        """
        candidates = self._discovery["candidates"]

        errors: dict[str, str] = {}
        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_ENTITIES, [])
            if selected:
                self._discovery.update(
                    {
                        "selected": selected,
                        # Affixes are applied verbatim (no separator inserted)
                        # so the user controls spacing; empty strings leave the
                        # name untouched. The target decides whether the affix
                        # shapes the friendly name or the entity_id only (name
                        # identical to the wrapped entity).
                        "prefix": user_input.get(CONF_AFFIX_PREFIX, ""),
                        "suffix": user_input.get(CONF_AFFIX_SUFFIX, ""),
                        "target": user_input.get(
                            CONF_AFFIX_TARGET, AFFIX_TARGET_ENTITY_ID
                        ),
                    }
                )
                return await defaults_step()
            errors["base"] = "no_entities_selected"

        options = [
            selector.SelectOptionDict(value=eid, label=name)
            for eid, name in sorted(candidates.items(), key=lambda kv: kv[1].lower())
        ]
        preselected = (
            list(candidates) if self._discovery.get("preselect_all", True) else []
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(
                            CONF_SELECTED_ENTITIES, default=preselected
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=options,
                                multiple=True,
                                mode=selector.SelectSelectorMode.LIST,
                            )
                        ),
                        vol.Optional(
                            CONF_AFFIX_PREFIX, default=""
                        ): selector.TextSelector(),
                        vol.Optional(
                            CONF_AFFIX_SUFFIX, default=""
                        ): selector.TextSelector(),
                        vol.Required(
                            CONF_AFFIX_TARGET, default=AFFIX_TARGET_ENTITY_ID
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=AFFIX_TARGETS,
                                translation_key=CONF_AFFIX_TARGET,
                            )
                        ),
                    }
                ),
                user_input or {},
            ),
            errors=errors,
        )

    def _finish_discovery(
        self,
        payload: Callable[[str, str], dict[str, Any]],
        overrides: dict[str, Any],
    ) -> config_entries.FlowResult:
        """Bulk-create the stashed selection, layering the chosen settings on top.

        A config flow can only return one entry, so each selected entity is
        created through the import step spawned as a background task, and this
        flow ends with an abort that reports how many were made.
        """
        disc = self._discovery
        selected = disc["selected"]
        candidates = disc["candidates"]
        prefix, suffix, target = disc["prefix"], disc["suffix"], disc["target"]
        # Drop cleared optional fields so they stay absent from the entry
        # rather than being stored as None.
        overrides = {k: v for k, v in overrides.items() if v is not None}
        for entity_id in selected:
            base = candidates.get(entity_id, entity_id)
            composed = f"{prefix}{base}{suffix}"
            if target == AFFIX_TARGET_NAME:
                data = payload(entity_id, composed)
            else:
                data = payload(entity_id, base)
                # Only pin an explicit id when the affix actually changes it;
                # otherwise leave the name-derived default (and its _2 dedupe).
                if composed != base:
                    data[CONF_ENTITY_ID] = composed
            # The defaults form supplies every editable setting; the source
            # entity, name and entity_id it never touches stay as the payload
            # set them.
            data.update(overrides)
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": config_entries.SOURCE_IMPORT},
                    data=data,
                )
            )
        return self.async_abort(
            reason="discovery_done",
            description_placeholders={"count": str(len(selected))},
        )

    async def async_step_discover_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Filter the discoverable occupancy sensors."""
        return await self._async_discovery_filter(
            step_id="discover_occupancy",
            domain="binary_sensor",
            # Occupancy sensors are commonly exposed under any of these classes.
            device_classes={"occupancy", "motion", "presence"},
            used_key=CONF_OCCUPANCY_SENSOR,
            select_step=self.async_step_discover_occupancy_select,
            user_input=user_input,
        )

    async def async_step_discover_occupancy_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Pick which discovered occupancy sensors to wrap."""
        return await self._async_discovery_select(
            step_id="discover_occupancy_select",
            defaults_step=self.async_step_discover_occupancy_defaults,
            user_input=user_input,
        )

    async def async_step_discover_occupancy_defaults(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Adjust the defaults applied to every discovered occupancy sensor."""
        if user_input is not None:
            return self._finish_discovery(
                _occupancy_payload, _flatten_sections(user_input, _OCCUPANCY_SECTIONS)
            )
        return self.async_show_form(
            step_id="discover_occupancy_defaults",
            data_schema=vol.Schema(_occupancy_option_fields()),
        )

    async def async_step_discover_illuminance(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Filter the discoverable illuminance sensors."""
        return await self._async_discovery_filter(
            step_id="discover_illuminance",
            domain="sensor",
            device_classes={"illuminance"},
            used_key=CONF_ILLUMINANCE_SENSOR,
            select_step=self.async_step_discover_illuminance_select,
            user_input=user_input,
        )

    async def async_step_discover_illuminance_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Pick which discovered illuminance sensors to wrap."""
        return await self._async_discovery_select(
            step_id="discover_illuminance_select",
            defaults_step=self.async_step_discover_illuminance_defaults,
            user_input=user_input,
        )

    async def async_step_discover_illuminance_defaults(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Adjust the defaults applied to every discovered illuminance sensor."""
        if user_input is not None:
            return self._finish_discovery(_illuminance_payload, user_input)
        return self.async_show_form(
            step_id="discover_illuminance_defaults",
            data_schema=vol.Schema(_illuminance_option_fields()),
        )

    async def async_step_discover_light(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Filter the discoverable real lights."""
        return await self._async_discovery_filter(
            step_id="discover_light",
            domain="light",
            device_classes=None,
            used_key=CONF_LIGHTS,
            select_step=self.async_step_discover_light_select,
            user_input=user_input,
        )

    async def async_step_discover_light_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Pick which discovered real lights to wrap."""
        return await self._async_discovery_select(
            step_id="discover_light_select",
            defaults_step=self.async_step_discover_light_defaults,
            user_input=user_input,
        )

    async def async_step_discover_light_defaults(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Adjust the defaults applied to every discovered light.

        The same light_timeout/stage-transition checks the manual light form
        enforces apply here, since one setting set is shared by every pick.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = _flatten_sections(user_input, _LIGHT_SECTIONS)
            errors = _validate_light_timeout(self.hass, flat)
            errors.update(_validate_stage_transitions(flat))
            errors.update(_validate_colors(flat))
            if not errors:
                if flat.get(CONF_TURN_ON_SELECT_ENTITY):
                    self._light_pending = {"kind": "discovery", "flat": flat}
                    return await self.async_step_light_selection()
                _validate_turn_on_selection(self.hass, flat)
                return self._finish_discovery(_light_payload, flat)
        return self.async_show_form(
            step_id="discover_light_defaults",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_light_option_fields()), user_input or {}
            ),
            errors=errors,
        )

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> config_entries.FlowResult:
        """Create a single entry from a discovery selection (defaults applied)."""
        return self.async_create_entry(title=import_data[CONF_NAME], data=import_data)

    # ------------------------------------------------------------------
    # Convert existing Virtual Lights in place
    # ------------------------------------------------------------------

    async def async_step_convert_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose the direction of an in-place light conversion."""
        return self.async_show_menu(
            step_id="convert_lights",
            menu_options=["convert_to_scheduled", "convert_to_regular"],
        )

    def _conversion_candidates(
        self, *, to_scheduled: bool
    ) -> dict[str, config_entries.ConfigEntry]:
        """Return entity-id keyed entries eligible for one conversion direction."""
        entity_type = ENTITY_TYPE_LIGHT if to_scheduled else ENTITY_TYPE_SCHEDULED_LIGHT
        return {
            entity_id: entry
            for entity_id, entry in _molight_light_entries(
                self.hass, (entity_type,)
            ).items()
            if _conversion_eligible(_molight_cfg(entry), to_scheduled=to_scheduled)
        }

    def _conversion_option(
        self, entity_id: str, label: str, entry: config_entries.ConfigEntry
    ) -> selector.SelectOptionDict:
        """Build a labeled conversion choice including its retained schedule."""
        schedule_name = self._entity_label(_molight_cfg(entry)[CONF_SCHEDULE_ENTITY])
        return selector.SelectOptionDict(
            value=entity_id, label=f"{label} — {schedule_name}"
        )

    async def _async_conversion_select(
        self, *, to_scheduled: bool, user_input: dict[str, Any] | None
    ) -> config_entries.FlowResult:
        """Choose one or more eligible lights, then show the review step."""
        candidates = self._conversion_candidates(to_scheduled=to_scheduled)
        if not candidates:
            return self.async_abort(
                reason=("no_gated_lights" if to_scheduled else "no_scheduled_lights")
            )

        labels = {entity_id: self._entity_label(entity_id) for entity_id in candidates}
        errors: dict[str, str] = {}
        step_id = "convert_to_scheduled" if to_scheduled else "convert_to_regular"
        if user_input is not None:
            selected = [
                entity_id
                for entity_id in user_input.get(CONF_CONVERT_LIGHTS, [])
                if entity_id in candidates
            ]
            if selected:
                self._conversion = {
                    "to_scheduled": to_scheduled,
                    "entry_ids": [candidates[e].entry_id for e in selected],
                    "lights": ", ".join(labels[e] for e in selected),
                }
                return await self._async_confirm_conversion(user_input=None)
            errors[CONF_CONVERT_LIGHTS] = "no_lights_selected"

        options = [
            self._conversion_option(entity_id, labels[entity_id], candidates[entity_id])
            for entity_id in sorted(candidates, key=lambda e: labels[e].lower())
        ]
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONVERT_LIGHTS): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_convert_to_scheduled(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Select gated Virtual Lights to promote to scheduled lights."""
        return await self._async_conversion_select(
            to_scheduled=True, user_input=user_input
        )

    async def async_step_convert_to_regular(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Select Virtual Scheduled Lights to return to regular gated lights."""
        return await self._async_conversion_select(
            to_scheduled=False, user_input=user_input
        )

    @staticmethod
    def _converted_scheduled_data(cfg: dict[str, Any]) -> dict[str, Any]:
        """Map one gated Virtual Light into two scheduled settings profiles."""
        inside = {
            key: value for key, value in cfg.items() if key not in _LIGHT_ENTRY_KEYS
        }
        # Outside the former gate, retain manual behavior/timing but remove
        # every input that could activate or indefinitely hold the light.
        outside = {
            key: value
            for key, value in inside.items()
            if key
            not in (
                CONF_OCCUPANCY_ENTITY,
                CONF_MAINTAIN_OCCUPANCY_ENTITY,
                CONF_ILLUMINANCE_ENTITY,
                CONF_DOOR_ENTITY,
            )
        }
        return {
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
            CONF_NAME: cfg[CONF_NAME],
            CONF_LIGHTS: cfg.get(CONF_LIGHTS, []),
            CONF_SCHEDULE_ENTITY: cfg[CONF_SCHEDULE_ENTITY],
            CONF_SCHEDULE_END_ACTION: {
                SCHEDULE_MODE_GATE: SCHEDULE_END_ACTION_TURN_OFF,
                SCHEDULE_MODE_GATE_SWITCH: SCHEDULE_END_ACTION_SWITCH,
                SCHEDULE_MODE_GATE_KEEP: SCHEDULE_END_ACTION_KEEP,
            }[cfg.get(CONF_SCHEDULE_MODE, DEFAULT_SCHEDULE_MODE)],
            CONF_OUTSIDE_SCHEDULE_SETTINGS: outside,
            CONF_INSIDE_SCHEDULE_SETTINGS: inside,
        }

    @staticmethod
    def _converted_regular_data(cfg: dict[str, Any]) -> dict[str, Any]:
        """Map a scheduled light's inside profile back to a gated light."""
        inside = {
            key: value
            for key, value in cfg.get(CONF_INSIDE_SCHEDULE_SETTINGS, {}).items()
            if key not in _LIGHT_ENTRY_KEYS
        }
        return {
            **inside,
            CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
            CONF_NAME: cfg[CONF_NAME],
            CONF_LIGHTS: cfg.get(CONF_LIGHTS, []),
            CONF_SCHEDULE_ENTITY: cfg[CONF_SCHEDULE_ENTITY],
            CONF_SCHEDULE_MODE: {
                SCHEDULE_END_ACTION_TURN_OFF: SCHEDULE_MODE_GATE,
                SCHEDULE_END_ACTION_SWITCH: SCHEDULE_MODE_GATE_SWITCH,
                SCHEDULE_END_ACTION_KEEP: SCHEDULE_MODE_GATE_KEEP,
            }[cfg.get(CONF_SCHEDULE_END_ACTION, DEFAULT_SCHEDULE_END_ACTION)],
        }

    async def async_step_confirm_convert_to_scheduled(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Review gated → scheduled conversions."""
        return await self._async_confirm_conversion(user_input=user_input)

    async def async_step_confirm_convert_to_regular(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Review scheduled → gated conversions."""
        return await self._async_confirm_conversion(user_input=user_input)

    async def _async_confirm_conversion(
        self, *, user_input: dict[str, Any] | None
    ) -> config_entries.FlowResult:
        """Review and rewrite the selected config entries in place.

        One step id per direction so the review text is fully translatable.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_CONFIRM_CONVERSION):
                errors[CONF_CONFIRM_CONVERSION] = "confirmation_required"
            else:
                prepared: list[tuple[config_entries.ConfigEntry, dict[str, Any]]] = []
                for entry_id in self._conversion["entry_ids"]:
                    entry = self.hass.config_entries.async_get_entry(entry_id)
                    if entry is None:
                        return self.async_abort(reason="conversion_targets_changed")
                    cfg = _molight_cfg(entry)
                    to_scheduled = self._conversion["to_scheduled"]
                    if not _conversion_eligible(cfg, to_scheduled=to_scheduled):
                        return self.async_abort(reason="conversion_targets_changed")
                    data = (
                        self._converted_scheduled_data(cfg)
                        if to_scheduled
                        else self._converted_regular_data(cfg)
                    )
                    # The chosen entity ID is identity, not settings: it
                    # survives conversion in either direction.
                    if entity_id := entry.data.get(CONF_ENTITY_ID):
                        data[CONF_ENTITY_ID] = entity_id
                    prepared.append((entry, data))

                for entry, data in prepared:
                    # Canonicalize the converted entry in data and clear its
                    # old complete-form options in the same update. Keeping
                    # the config entry preserves all entity registry ids.
                    self.hass.config_entries.async_update_entry(
                        entry, data=data, options={}
                    )
                return self.async_abort(
                    reason="conversion_done",
                    description_placeholders={"count": str(len(prepared))},
                )

        return self.async_show_form(
            step_id=(
                "confirm_convert_to_scheduled"
                if self._conversion.get("to_scheduled")
                else "confirm_convert_to_regular"
            ),
            data_schema=vol.Schema(
                {vol.Required(CONF_CONFIRM_CONVERSION, default=False): bool}
            ),
            errors=errors,
            description_placeholders={"lights": self._conversion.get("lights", "")},
        )

    # ------------------------------------------------------------------
    # Bulk-assign a virtual sensor to many virtual lights at once
    # ------------------------------------------------------------------

    async def async_step_assign_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose which kind of virtual sensor to assign to lights in bulk."""
        return self.async_show_menu(
            step_id="assign_sensor",
            menu_options=[
                "assign_occupancy",
                "assign_illuminance",
                "assign_schedule",
            ],
        )

    async def async_step_assign_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Pick an occupancy sensor and its role, then choose target lights."""
        if user_input is not None:
            role = user_input[CONF_ASSIGN_ROLE]
            self._assign = {
                "sensor": user_input[CONF_ASSIGN_SENSOR],
                "key": (
                    CONF_MAINTAIN_OCCUPANCY_ENTITY
                    if role == ASSIGN_ROLE_MAINTAIN
                    else CONF_OCCUPANCY_ENTITY
                ),
                "mode_key": None,
                "mode": None,
                # Occupancy feeds the countdown, so the light_timeout guard
                # applies to each target the same way the light form enforces it.
                "check_timeout": True,
            }
            return await self.async_step_assign_lights()

        schema = vol.Schema(
            {
                vol.Required(CONF_ASSIGN_SENSOR): selector.EntitySelector(
                    _LIGHT_REF_SELECTORS[CONF_OCCUPANCY_ENTITY]
                ),
                vol.Required(
                    CONF_ASSIGN_ROLE, default=ASSIGN_ROLE_REGULAR
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=ASSIGN_ROLES, translation_key=CONF_ASSIGN_ROLE
                    )
                ),
            }
        )
        return self.async_show_form(step_id="assign_occupancy", data_schema=schema)

    async def async_step_assign_illuminance(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Pick an illuminance sensor and its mode, then choose target lights."""
        if user_input is not None:
            self._assign = {
                "sensor": user_input[CONF_ASSIGN_SENSOR],
                "key": CONF_ILLUMINANCE_ENTITY,
                "mode_key": CONF_ILLUMINANCE_MODE,
                "mode": user_input[CONF_ILLUMINANCE_MODE],
                "check_timeout": False,
            }
            return await self.async_step_assign_lights()

        schema = vol.Schema(
            {
                vol.Required(CONF_ASSIGN_SENSOR): selector.EntitySelector(
                    _LIGHT_REF_SELECTORS[CONF_ILLUMINANCE_ENTITY]
                ),
                vol.Required(
                    CONF_ILLUMINANCE_MODE, default=DEFAULT_ILLUMINANCE_MODE
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=ILLUMINANCE_MODES,
                        translation_key=CONF_ILLUMINANCE_MODE,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="assign_illuminance", data_schema=schema)

    async def async_step_assign_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Pick a schedule sensor and its mode, then choose target lights."""
        if user_input is not None:
            self._assign = {
                "sensor": user_input[CONF_ASSIGN_SENSOR],
                "key": CONF_SCHEDULE_ENTITY,
                "mode_key": CONF_SCHEDULE_MODE,
                "mode": user_input[CONF_SCHEDULE_MODE],
                "check_timeout": False,
            }
            return await self.async_step_assign_lights()

        schema = vol.Schema(
            {
                vol.Required(CONF_ASSIGN_SENSOR): selector.EntitySelector(
                    _LIGHT_REF_SELECTORS[CONF_SCHEDULE_ENTITY]
                ),
                vol.Required(
                    CONF_SCHEDULE_MODE, default=DEFAULT_SCHEDULE_MODE
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=SCHEDULE_MODES, translation_key=CONF_SCHEDULE_MODE
                    )
                ),
            }
        )
        return self.async_show_form(step_id="assign_schedule", data_schema=schema)

    def _entity_label(self, entity_id: str) -> str:
        """Friendly name of an entity, falling back to its entity_id."""
        state = self.hass.states.get(entity_id)
        return state.name if state and state.name else entity_id

    async def async_step_assign_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Pick the lights that reference the sensor, pre-selecting current users.

        The submitted set is authoritative for this sensor+role: selected
        lights get the reference (overwriting any prior one under the same
        key), and pre-selected lights that were deselected have it removed.
        Occupancy targets whose turn-off timeout is shorter than the sensor's
        timeout are skipped and reported rather than silently misconfigured.
        """
        assign = self._assign
        key = assign["key"]
        sensor = assign["sensor"]
        lights = _molight_light_entries(self.hass)
        already = {
            eid
            for eid, entry in lights.items()
            if _molight_cfg(entry).get(key) == sensor
        }

        if user_input is not None:
            selected = set(user_input.get(CONF_ASSIGN_LIGHTS, []))
            occ_timeout = (
                _effective_occupancy_timeout(self.hass, sensor)
                if assign["check_timeout"]
                else None
            )
            assigned: list[str] = []
            skipped: list[str] = []
            for eid in selected:
                entry = lights.get(eid)
                if entry is None:
                    continue  # a stale pick no longer backed by a light entry
                cfg = _molight_cfg(entry)
                if (
                    occ_timeout is not None
                    and int(cfg.get(CONF_LIGHT_TIMEOUT, DEFAULT_LIGHT_TIMEOUT))
                    < occ_timeout
                ):
                    skipped.append(eid)
                    continue
                opts = {
                    k: v
                    for k, v in cfg.items()
                    if k not in (CONF_ENTITY_TYPE, CONF_ENTITY_ID)
                }
                changed = opts.get(key) != sensor
                opts[key] = sensor
                if assign["mode_key"] is not None:
                    changed = changed or opts.get(assign["mode_key"]) != assign["mode"]
                    opts[assign["mode_key"]] = assign["mode"]
                if changed:
                    self.hass.config_entries.async_update_entry(entry, options=opts)
                    assigned.append(eid)

            removed: list[str] = []
            for eid in already - selected:
                entry = lights[eid]
                opts = {
                    k: v
                    for k, v in _molight_cfg(entry).items()
                    if k not in (CONF_ENTITY_TYPE, CONF_ENTITY_ID, key)
                }
                self.hass.config_entries.async_update_entry(entry, options=opts)
                removed.append(eid)

            placeholders = {
                "assigned": str(len(assigned)),
                "removed": str(len(removed)),
            }
            if skipped:
                placeholders["skipped"] = ", ".join(
                    sorted(self._entity_label(eid) for eid in skipped)
                )
                return self.async_abort(
                    reason="assign_done_skipped",
                    description_placeholders=placeholders,
                )
            return self.async_abort(
                reason="assign_done", description_placeholders=placeholders
            )

        if not lights:
            return self.async_abort(reason="no_lights")

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ASSIGN_LIGHTS, default=sorted(already)
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        integration=DOMAIN, domain="light", multiple=True
                    )
                )
            }
        )
        return self.async_show_form(step_id="assign_lights", data_schema=schema)

    # ------------------------------------------------------------------
    # Occupancy (simple — one sensor, one timeout)
    # ------------------------------------------------------------------

    async def async_step_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure a Virtual Occupancy Sensor."""
        errors: dict[str, str] = {}

        if user_input is not None:
            flat = _flatten_sections(user_input, _OCCUPANCY_SECTIONS)
            errors = _validate_occupancy_source(self.hass, flat)
            if not errors:
                result, errors = await self._resolve_and_create(
                    entity_type=ENTITY_TYPE_OCCUPANCY,
                    name=flat[CONF_NAME],
                    data={CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY, **flat},
                    prefill=user_input,
                    entity_id_format=BINARY_SENSOR_ENTITY_ID_FORMAT,
                )
                if result is not None:
                    return result

        source_exclusions = _molight_occupancy_entity_ids(self.hass)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_OCCUPANCY_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="binary_sensor",
                        device_class=["occupancy", "motion", "presence"],
                        exclude_entities=source_exclusions,
                        multiple=False,
                    )
                ),
                **_occupancy_option_fields(with_entity_id=True),
            }
        )
        return self.async_show_form(
            step_id="occupancy",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self._prefill or {}
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
            flat = _flatten_sections(user_input, _ENTITY_ID_SECTIONS)
            if not flat.get(CONF_TRIGGER_SENSORS):
                errors[CONF_TRIGGER_SENSORS] = "trigger_sensors_required"
            elif role_errors := _validate_combined_occupancy_roles(flat):
                errors.update(role_errors)
            else:
                result, errors = await self._resolve_and_create(
                    entity_type=ENTITY_TYPE_COMBINED_OCCUPANCY,
                    name=flat[CONF_NAME],
                    data={
                        CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
                        **flat,
                    },
                    prefill=user_input,
                    entity_id_format=BINARY_SENSOR_ENTITY_ID_FORMAT,
                )
                if result is not None:
                    return result

        schema = vol.Schema(
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
                **_entity_id_section(),
            }
        )
        return self.async_show_form(
            step_id="combined_occupancy",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self._prefill or {}
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
            flat = _flatten_sections(user_input, _ENTITY_ID_SECTIONS)
            result, errors = await self._resolve_and_create(
                entity_type=ENTITY_TYPE_ILLUMINANCE,
                name=flat[CONF_NAME],
                data={CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE, **flat},
                prefill=user_input,
                entity_id_format=BINARY_SENSOR_ENTITY_ID_FORMAT,
            )
            if result is not None:
                return result

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_ILLUMINANCE_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        device_class="illuminance", multiple=False
                    )
                ),
                **_illuminance_option_fields(),
                **_entity_id_section(),
            }
        )
        return self.async_show_form(
            step_id="illuminance",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self._prefill or {}
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
            if window is None:
                # A half-filled window would be silently dropped and an empty
                # form would create a sensor that is permanently off — either
                # way a schedule nothing can ever follow.
                errors["base"] = (
                    "window_incomplete"
                    if _window_input_provided(user_input)
                    else "window_required"
                )
            else:
                data = {
                    CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_TIME_WINDOWS: [window],
                }
                entity_id = (user_input.get(SECTION_ADVANCED) or {}).get(CONF_ENTITY_ID)
                if entity_id:
                    data[CONF_ENTITY_ID] = entity_id
                result, errors = await self._resolve_and_create(
                    entity_type=ENTITY_TYPE_SCHEDULE,
                    name=user_input[CONF_NAME],
                    data=data,
                    prefill=user_input,
                    entity_id_format=BINARY_SENSOR_ENTITY_ID_FORMAT,
                )
                if result is not None:
                    return result

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                **_schedule_edge_fields(),
                **_entity_id_section(),
            }
        )
        return self.async_show_form(
            step_id="schedule",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self._prefill or {}
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
            flat = _flatten_sections(user_input, _LIGHT_SECTIONS)
            if not flat.get(CONF_LIGHTS):
                errors[CONF_LIGHTS] = "lights_required"
            else:
                errors = _validate_light_timeout(self.hass, flat)
            errors.update(_validate_stage_transitions(flat))
            errors.update(_validate_colors(flat))
            if not errors:
                _, entity_errors, _, _ = self._resolve_entity_id(
                    flat[CONF_NAME], flat, LIGHT_ENTITY_ID_FORMAT
                )
                errors.update(entity_errors)
            if not errors:
                if flat.get(CONF_TURN_ON_SELECT_ENTITY):
                    self._light_pending = {
                        "kind": "create",
                        "flat": flat,
                        "prefill": user_input,
                    }
                    return await self.async_step_light_selection()
                _validate_turn_on_selection(self.hass, flat)
                result, errors = await self._resolve_and_create(
                    entity_type=ENTITY_TYPE_LIGHT,
                    name=flat[CONF_NAME],
                    data={CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT, **flat},
                    prefill=user_input,
                    entity_id_format=LIGHT_ENTITY_ID_FORMAT,
                )
                if result is not None:
                    return result

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_LIGHTS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light", multiple=True)
                ),
                **_light_option_fields(with_entity_id=True),
            }
        )
        return self.async_show_form(
            step_id="light",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self._prefill or {}
            ),
            errors=errors,
        )

    async def async_step_light_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure a target-dependent fixed/source turn-on selection."""
        pending = self._light_pending
        flat = dict(pending["flat"])
        errors: dict[str, str] = {}

        if user_input is not None:
            flat.update(user_input)
            errors = _validate_turn_on_selection(self.hass, flat)
            if not errors:
                self._light_selection_values = {
                    key: flat[key]
                    for key in (
                        CONF_TURN_ON_SELECT_OPTION,
                        CONF_TURN_ON_SELECT_SOURCE_ENTITY,
                    )
                    if key in flat
                }
                if pending["kind"] == "discovery":
                    return self._finish_discovery(_light_payload, flat)
                result, errors = await self._resolve_and_create(
                    entity_type=ENTITY_TYPE_LIGHT,
                    name=flat[CONF_NAME],
                    data={CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT, **flat},
                    prefill=pending["prefill"],
                    entity_id_format=LIGHT_ENTITY_ID_FORMAT,
                )
                if result is not None:
                    return result

        target = flat[CONF_TURN_ON_SELECT_ENTITY]
        schema = vol.Schema(_turn_on_selection_fields(target))
        return self.async_show_form(
            step_id="light_selection",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                (
                    user_input
                    if user_input is not None
                    else self._light_selection_values
                ),
            ),
            errors=errors,
            description_placeholders={"entity_id": target},
        )

    # ------------------------------------------------------------------
    # Virtual Scheduled Light
    # ------------------------------------------------------------------

    async def async_step_scheduled_light(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose the shared identity, targets and schedule."""
        errors: dict[str, str] = {}
        if user_input is not None:
            flat = _flatten_sections(user_input, _LIGHT_SECTIONS)
            if not flat.get(CONF_LIGHTS):
                errors[CONF_LIGHTS] = "lights_required"
            if not errors:
                _, entity_errors, _, _ = self._resolve_entity_id(
                    flat[CONF_NAME], flat, LIGHT_ENTITY_ID_FORMAT
                )
                errors.update(entity_errors)
            if not errors:
                self._scheduled_light_shared = flat
                self._scheduled_light_shared_input = user_input
                return await self.async_step_scheduled_light_outside()

        return self._show_scheduled_light_form(user_input, errors)

    def _show_scheduled_light_form(
        self, user_input: dict[str, Any] | None, errors: dict[str, str]
    ) -> config_entries.FlowResult:
        """Render the shared first form, prefilled from input or the stash."""
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_LIGHTS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light", multiple=True)
                ),
                vol.Required(CONF_SCHEDULE_ENTITY): selector.EntitySelector(
                    _LIGHT_REF_SELECTORS[CONF_SCHEDULE_ENTITY]
                ),
                vol.Required(
                    CONF_SCHEDULE_END_ACTION, default=DEFAULT_SCHEDULE_END_ACTION
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=SCHEDULE_END_ACTIONS,
                        translation_key=CONF_SCHEDULE_END_ACTION,
                    )
                ),
                **_entity_id_section(),
            }
        )
        return self.async_show_form(
            step_id="scheduled_light",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self._prefill or {}
            ),
            errors=errors,
        )

    def _scheduled_light_defaults(self, side: str) -> dict[str, Any] | None:
        """On creation the inside form starts as a copy of the outside one."""
        if side == CONF_INSIDE_SCHEDULE_SETTINGS:
            return self._scheduled_light_settings[CONF_OUTSIDE_SCHEDULE_SETTINGS]
        return None

    async def _finish_scheduled_light(self) -> config_entries.FlowResult:
        """Create the entry after both settings mappings are complete."""
        shared = dict(self._scheduled_light_shared)
        data = {
            CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULED_LIGHT,
            **shared,
            **self._scheduled_light_settings,
        }
        result, errors = await self._resolve_and_create(
            entity_type=ENTITY_TYPE_SCHEDULED_LIGHT,
            name=shared[CONF_NAME],
            data=data,
            prefill=self._scheduled_light_shared_input,
            entity_id_format=LIGHT_ENTITY_ID_FORMAT,
        )
        if result is not None:
            return result
        # The explicit entity_id was free on the first step but has been taken
        # since (a race with another flow): send the user back to that form
        # with the collision error, keeping both completed settings forms.
        return self._show_scheduled_light_form(
            self._scheduled_light_shared_input, errors
        )

    # ------------------------------------------------------------------
    # Virtual Remote
    # ------------------------------------------------------------------

    async def async_step_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure a Virtual Remote entry.

        The entry is wiring between button event entities and target lights;
        its only entity is the diagnostic Last Action sensor, whose id
        derives from the name — so there is no entity_id field to resolve.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            flat = _flatten_sections(user_input, _REMOTE_SECTIONS)
            errors = _validate_remote(self.hass, flat)
            if not errors:
                # Drop empty pickers/values so unbound slots stay absent from
                # the entry rather than being stored as [] or None.
                data = {k: v for k, v in flat.items() if v not in (None, [])}
                return self.async_create_entry(
                    title=flat[CONF_NAME],
                    data={CONF_ENTITY_TYPE: ENTITY_TYPE_REMOTE, **data},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                **_remote_top_fields(),
                **_remote_option_fields(),
            }
        )
        return self.async_show_form(
            step_id="remote",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Options flow — edit an existing MoLight entity
# ---------------------------------------------------------------------------


class MoLightOptionsFlow(_ScheduledLightSettingsSteps, config_entries.OptionsFlow):
    """Allow editing a MoLight entity's settings after creation."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow for the given entry."""
        self._entry = entry
        self._cfg = _molight_cfg(entry)
        self._light_pending: dict[str, Any] | None = None
        self._light_selection_values = {
            key: self._cfg[key]
            for key in (
                CONF_TURN_ON_SELECT_OPTION,
                CONF_TURN_ON_SELECT_SOURCE_ENTITY,
            )
            if key in self._cfg
        }
        self._scheduled_light_shared: dict[str, Any] | None = None
        self._init_scheduled_light_steps()

    def _finish(self, data: dict[str, Any]) -> config_entries.FlowResult:
        """Store the edited options, syncing the entry title to the new name.

        HA ignores an options flow's title, so a rename via the Name field
        would otherwise leave the integrations page showing the old title.
        """
        name = data[CONF_NAME]
        if name != self._entry.title:
            self.hass.config_entries.async_update_entry(self._entry, title=name)
        return self.async_create_entry(title=name, data=data)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Route to the edit step matching the entry's entity type."""
        return await {
            ENTITY_TYPE_OCCUPANCY: self.async_step_occupancy,
            ENTITY_TYPE_COMBINED_OCCUPANCY: self.async_step_combined_occupancy,
            ENTITY_TYPE_ILLUMINANCE: self.async_step_illuminance,
            ENTITY_TYPE_SCHEDULE: self.async_step_schedule,
            ENTITY_TYPE_LIGHT: self.async_step_light,
            ENTITY_TYPE_SCHEDULED_LIGHT: self.async_step_scheduled_light,
            ENTITY_TYPE_REMOTE: self.async_step_remote,
        }[self._cfg[CONF_ENTITY_TYPE]]()

    # ------------------------------------------------------------------
    # Occupancy
    # ------------------------------------------------------------------

    async def async_step_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit a Virtual Occupancy Sensor's settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            flat = _flatten_sections(user_input, _OCCUPANCY_SECTIONS)
            errors = _validate_occupancy_source(self.hass, flat)
            if not errors:
                # Raising this sensor's timeout must not outgrow any virtual light
                # that depends on it (directly or through a combined sensor).
                min_light = _min_dependent_light_timeout(
                    self.hass, self._entry.entry_id
                )
                if (
                    min_light is not None
                    and int(flat[CONF_OCCUPANCY_TIMEOUT]) > min_light
                ):
                    errors[CONF_OCCUPANCY_TIMEOUT] = "occupancy_timeout_too_long"
                else:
                    return self._finish(flat)

        cfg = self._cfg
        source_exclusions = _molight_occupancy_entity_ids(self.hass)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
                vol.Required(
                    CONF_OCCUPANCY_SENSOR, default=cfg.get(CONF_OCCUPANCY_SENSOR)
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="binary_sensor",
                        device_class=["occupancy", "motion", "presence"],
                        exclude_entities=source_exclusions,
                        multiple=False,
                    )
                ),
                **_occupancy_option_fields(),
            }
        )
        return self.async_show_form(
            step_id="occupancy",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or _nest_sections(cfg, _OCCUPANCY_SECTIONS)
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Combined Occupancy
    # ------------------------------------------------------------------

    async def async_step_combined_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit a Virtual Combined Occupancy Sensor's settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_TRIGGER_SENSORS):
                errors[CONF_TRIGGER_SENSORS] = "trigger_sensors_required"
            elif role_errors := _validate_combined_occupancy_roles(user_input):
                errors.update(role_errors)
            else:
                # The new constituent set must not outgrow any dependent light:
                # the combined sensor's effective timeout is its max constituent.
                constituents = user_input.get(
                    CONF_TRIGGER_SENSORS, []
                ) + user_input.get(CONF_MAINTAIN_SENSORS, [])
                if _combined_occupancy_creates_cycle(
                    self.hass, self._entry, constituents
                ):
                    errors["base"] = "combined_occupancy_cycle"
                else:
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
                        return self._finish(user_input)

        cfg = self._cfg
        cycle_exclusions = _combined_occupancy_cycle_candidates(self.hass, self._entry)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
                vol.Required(
                    CONF_TRIGGER_SENSORS,
                    default=cfg.get(CONF_TRIGGER_SENSORS, []),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        integration=DOMAIN,
                        device_class="occupancy",
                        exclude_entities=cycle_exclusions,
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
                        exclude_entities=cycle_exclusions,
                        multiple=True,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="combined_occupancy",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Illuminance
    # ------------------------------------------------------------------

    async def async_step_illuminance(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit a Virtual Illuminance Sensor's settings."""
        if user_input is not None:
            return self._finish(user_input)

        cfg = self._cfg
        schema = vol.Schema(
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
                **_illuminance_option_fields(),
            }
        )
        return self.async_show_form(
            step_id="illuminance",
            data_schema=self.add_suggested_values_to_schema(schema, cfg),
        )

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit a Virtual Schedule Sensor's settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            window = _window_from_input(user_input)
            if window is None:
                errors["base"] = (
                    "window_incomplete"
                    if _window_input_provided(user_input)
                    else "window_required"
                )
            else:
                return self._finish(
                    {
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_TIME_WINDOWS: [window],
                    }
                )

        cfg = self._cfg
        first = (cfg.get(CONF_TIME_WINDOWS) or [None])[0]
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
                **_schedule_edge_fields(),
            }
        )
        return self.async_show_form(
            step_id="schedule",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or _window_suggested(first)
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Virtual Light
    # ------------------------------------------------------------------

    async def async_step_light(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit a Virtual Light's settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            flat = _flatten_sections(user_input, _LIGHT_SECTIONS)
            if not flat.get(CONF_LIGHTS):
                errors[CONF_LIGHTS] = "lights_required"
            else:
                errors = _validate_light_timeout(self.hass, flat)
            errors.update(_validate_stage_transitions(flat))
            errors.update(_validate_colors(flat))
            if not errors:
                if flat.get(CONF_TURN_ON_SELECT_ENTITY):
                    self._light_pending = {"flat": flat}
                    return await self.async_step_light_selection()
                _validate_turn_on_selection(self.hass, flat)
                # Drop None values so absent optional entity fields are simply
                # missing from entry.options rather than stored as None.
                clean = {k: v for k, v in flat.items() if v is not None}
                return self._finish(clean)

        cfg = self._cfg
        # Current values are applied as suggested values (not defaults) so the
        # optional fields — sensor references, brightness/fade overrides — can
        # be cleared back to "not set" once given a value.
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
                vol.Required(
                    CONF_LIGHTS, default=cfg.get(CONF_LIGHTS, [])
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light", multiple=True)
                ),
                **_light_option_fields(),
            }
        )
        return self.async_show_form(
            step_id="light",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or _nest_sections(cfg, _LIGHT_SECTIONS)
            ),
            errors=errors,
        )

    async def async_step_light_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit a target-dependent fixed/source turn-on selection."""
        flat = dict(self._light_pending["flat"])
        errors: dict[str, str] = {}

        if user_input is not None:
            flat.update(user_input)
            errors = _validate_turn_on_selection(self.hass, flat)
            if not errors:
                clean = {k: v for k, v in flat.items() if v is not None}
                return self._finish(clean)

        target = flat[CONF_TURN_ON_SELECT_ENTITY]
        schema = vol.Schema(_turn_on_selection_fields(target))
        return self.async_show_form(
            step_id="light_selection",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                (
                    user_input
                    if user_input is not None
                    else self._light_selection_values
                ),
            ),
            errors=errors,
            description_placeholders={"entity_id": target},
        )

    # ------------------------------------------------------------------
    # Virtual Scheduled Light
    # ------------------------------------------------------------------

    async def async_step_scheduled_light(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit the shared identity, targets and schedule."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_LIGHTS):
                errors[CONF_LIGHTS] = "lights_required"
            if not errors:
                self._scheduled_light_shared = dict(user_input)
                return await self.async_step_scheduled_light_outside()

        cfg = self._cfg
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
                vol.Required(
                    CONF_LIGHTS, default=cfg.get(CONF_LIGHTS, [])
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light", multiple=True)
                ),
                vol.Required(
                    CONF_SCHEDULE_ENTITY, default=cfg.get(CONF_SCHEDULE_ENTITY)
                ): selector.EntitySelector(_LIGHT_REF_SELECTORS[CONF_SCHEDULE_ENTITY]),
                vol.Required(
                    CONF_SCHEDULE_END_ACTION,
                    default=cfg.get(
                        CONF_SCHEDULE_END_ACTION, DEFAULT_SCHEDULE_END_ACTION
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=SCHEDULE_END_ACTIONS,
                        translation_key=CONF_SCHEDULE_END_ACTION,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="scheduled_light",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or cfg),
            errors=errors,
        )

    def _scheduled_light_defaults(self, side: str) -> dict[str, Any] | None:
        """Editing starts each side from the entry's stored settings."""
        return self._cfg.get(side, {})

    async def _finish_scheduled_light(self) -> config_entries.FlowResult:
        """Store both settings mappings as one complete options payload."""
        return self._finish(
            {**self._scheduled_light_shared, **self._scheduled_light_settings}
        )

    # ------------------------------------------------------------------
    # Virtual Remote
    # ------------------------------------------------------------------

    async def async_step_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit a Virtual Remote entry's settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            flat = _flatten_sections(user_input, _REMOTE_SECTIONS)
            errors = _validate_remote(self.hass, flat)
            if not errors:
                clean = {k: v for k, v in flat.items() if v not in (None, [])}
                return self._finish(clean)

        cfg = self._cfg
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=cfg[CONF_NAME]): str,
                **_remote_top_fields(),
                **_remote_option_fields(),
            }
        )
        return self.async_show_form(
            step_id="remote",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or _nest_sections(cfg, _REMOTE_SECTIONS)
            ),
            errors=errors,
        )
