"""Config flow for MoLight."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT as BINARY_SENSOR_ENTITY_ID_FORMAT,
)
from homeassistant.components.light import (
    ENTITY_ID_FORMAT as LIGHT_ENTITY_ID_FORMAT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    AFFIX_TARGET_ENTITY_ID,
    AFFIX_TARGET_NAME,
    AFFIX_TARGETS,
    COMBINE_EARLIEST,
    COMBINE_LATEST,
    CONF_AFFIX_PREFIX,
    CONF_AFFIX_SUFFIX,
    CONF_AFFIX_TARGET,
    CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    CONF_ENTITY_ID,
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
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_SENSORS,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TIMEOUT,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    CONF_SELECTED_ENTITIES,
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
        return int(cfg.get(CONF_OCCUPANCY_TIMEOUT, 0))
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
                    for e in er.async_entries_for_config_entry(
                        registry, entry.entry_id
                    )
                }
                if not entry_ids <= dependent_ids:
                    dependent_ids.update(entry_ids)
                    changed = True

    timeouts = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        cfg = _molight_cfg(entry)
        if cfg.get(CONF_ENTITY_TYPE) == ENTITY_TYPE_LIGHT and (
            cfg.get(CONF_OCCUPANCY_ENTITY) in dependent_ids
            or cfg.get(CONF_MAINTAIN_OCCUPANCY_ENTITY) in dependent_ids
        ):
            timeouts.append(int(cfg.get(CONF_LIGHT_TIMEOUT, 0)))
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


def _occupancy_payload(entity_id: str, name: str) -> dict[str, Any]:
    return {
        CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY,
        CONF_NAME: name,
        CONF_OCCUPANCY_SENSOR: entity_id,
        CONF_OCCUPANCY_TIMEOUT: 120,
        CONF_FALSE_DETECTION_GRACE: 3,
        CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT: DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT,
    }


def _illuminance_payload(entity_id: str, name: str) -> dict[str, Any]:
    return {
        CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE,
        CONF_NAME: name,
        CONF_ILLUMINANCE_SENSOR: entity_id,
        CONF_ILLUMINANCE_THRESHOLD: 10.0,
        CONF_ILLUMINANCE_HYSTERESIS: 0.0,
    }


def _light_payload(entity_id: str, name: str) -> dict[str, Any]:
    # Wraps a single real light with defaults and no entity references; the
    # user wires up occupancy/illuminance/schedule afterwards via options.
    return {
        CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT,
        CONF_NAME: name,
        CONF_LIGHTS: [entity_id],
        CONF_LIGHT_TIMEOUT: 300,
        CONF_FALSE_OFF_DELAY: 5,
        CONF_ILLUMINANCE_MODE: ILLUMINANCE_MODE_CONTROL,
        CONF_SCHEDULE_MODE: SCHEDULE_MODE_FOLLOW,
    }


class MoLightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MoLight."""

    VERSION = 1

    def __init__(self) -> None:
        self._entity_type: str | None = None
        # Set when a manual create step is re-entered from the entity_id
        # confirm step's "change" option, so the form comes back prefilled.
        self._prefill: dict[str, Any] | None = None
        # Stashed create payload while the entity_id confirm step is shown.
        self._pending: dict[str, Any] | None = None

    @staticmethod
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> "MoLightOptionsFlow":
        return MoLightOptionsFlow(entry)

    def _create_step(self, entity_type: str):
        """Map an entity type to its manual create step handler."""
        return {
            ENTITY_TYPE_OCCUPANCY: self.async_step_occupancy,
            ENTITY_TYPE_COMBINED_OCCUPANCY: self.async_step_combined_occupancy,
            ENTITY_TYPE_ILLUMINANCE: self.async_step_illuminance,
            ENTITY_TYPE_SCHEDULE: self.async_step_schedule,
            ENTITY_TYPE_LIGHT: self.async_step_light,
        }[entity_type]

    # ------------------------------------------------------------------
    # Optional explicit entity_id (manual create steps)
    # ------------------------------------------------------------------

    def _entity_id_taken(self, entity_id: str) -> bool:
        """True if entity_id is already registered or has a live state."""
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
          errors        — {CONF_ENTITY_ID: "entity_id_conflict"} on an explicit
                          clash; the caller re-shows the form
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
                return None, {CONF_ENTITY_ID: "entity_id_conflict"}, False, candidate
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
        user_input: dict[str, Any],
        entity_id_format: str,
    ) -> tuple[config_entries.FlowResult | None, dict[str, str]]:
        """Finalize a manual create: create the entry, or divert to confirm.

        Returns (result, errors). When errors is non-empty the caller re-shows
        its form; otherwise result is the FlowResult to return.
        """
        obj, errors, needs_confirm, candidate = self._resolve_entity_id(
            name, user_input, entity_id_format
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
                "user_input": user_input,
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
        return self.async_create_entry(
            title=pending["name"], data=pending["data"]
        )

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

    async def _async_discovery(
        self,
        *,
        step_id: str,
        domain: str,
        device_classes: set[str] | None,
        used_key: str,
        payload,
        user_input: dict[str, Any] | None,
    ) -> config_entries.FlowResult:
        """Show a checklist of candidate entities and bulk-create the picks.

        A config flow can only return one entry, so the selected entities are
        each created through the import step spawned as a background task, and
        this flow ends with an abort that reports how many were made.
        """
        candidates = _discovery_candidates(
            self.hass, domain, device_classes, used_key
        )

        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_ENTITIES, [])
            # Applied verbatim (no separator inserted) so the user controls
            # spacing; empty strings leave the discovered name untouched. The
            # target decides whether the affix shapes the friendly name or the
            # entity_id only (leaving the name identical to the wrapped entity).
            prefix = user_input.get(CONF_AFFIX_PREFIX, "")
            suffix = user_input.get(CONF_AFFIX_SUFFIX, "")
            target = user_input.get(CONF_AFFIX_TARGET, AFFIX_TARGET_ENTITY_ID)
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

        if not candidates:
            return self.async_abort(reason="no_candidates")

        options = [
            selector.SelectOptionDict(value=eid, label=name)
            for eid, name in sorted(
                candidates.items(), key=lambda kv: kv[1].lower()
            )
        ]
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SELECTED_ENTITIES, default=list(candidates)
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
        )

    async def async_step_discover_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        return await self._async_discovery(
            step_id="discover_occupancy",
            domain="binary_sensor",
            # Occupancy sensors are commonly exposed under any of these classes.
            device_classes={"occupancy", "motion", "presence"},
            used_key=CONF_OCCUPANCY_SENSOR,
            payload=_occupancy_payload,
            user_input=user_input,
        )

    async def async_step_discover_illuminance(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        return await self._async_discovery(
            step_id="discover_illuminance",
            domain="sensor",
            device_classes={"illuminance"},
            used_key=CONF_ILLUMINANCE_SENSOR,
            payload=_illuminance_payload,
            user_input=user_input,
        )

    async def async_step_discover_light(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        return await self._async_discovery(
            step_id="discover_light",
            domain="light",
            device_classes=None,
            used_key=CONF_LIGHTS,
            payload=_light_payload,
            user_input=user_input,
        )

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> config_entries.FlowResult:
        """Create a single entry from a discovery selection (defaults applied)."""
        return self.async_create_entry(
            title=import_data[CONF_NAME], data=import_data
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
            result, errors = await self._resolve_and_create(
                entity_type=ENTITY_TYPE_OCCUPANCY,
                name=user_input[CONF_NAME],
                data={CONF_ENTITY_TYPE: ENTITY_TYPE_OCCUPANCY, **user_input},
                user_input=user_input,
                entity_id_format=BINARY_SENSOR_ENTITY_ID_FORMAT,
            )
            if result is not None:
                return result

        schema = vol.Schema(
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
                vol.Optional(CONF_ENTITY_ID): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="occupancy",
            data_schema=self.add_suggested_values_to_schema(
                schema, self._prefill or {}
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
                result, errors = await self._resolve_and_create(
                    entity_type=ENTITY_TYPE_COMBINED_OCCUPANCY,
                    name=user_input[CONF_NAME],
                    data={
                        CONF_ENTITY_TYPE: ENTITY_TYPE_COMBINED_OCCUPANCY,
                        **user_input,
                    },
                    user_input=user_input,
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
                vol.Optional(CONF_ENTITY_ID): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="combined_occupancy",
            data_schema=self.add_suggested_values_to_schema(
                schema, self._prefill or {}
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
            result, errors = await self._resolve_and_create(
                entity_type=ENTITY_TYPE_ILLUMINANCE,
                name=user_input[CONF_NAME],
                data={CONF_ENTITY_TYPE: ENTITY_TYPE_ILLUMINANCE, **user_input},
                user_input=user_input,
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
                vol.Optional(CONF_ENTITY_ID): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="illuminance",
            data_schema=self.add_suggested_values_to_schema(
                schema, self._prefill or {}
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
                result, errors = await self._resolve_and_create(
                    entity_type=ENTITY_TYPE_SCHEDULE,
                    name=user_input[CONF_NAME],
                    data={
                        CONF_ENTITY_TYPE: ENTITY_TYPE_SCHEDULE,
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_TIME_WINDOWS: [window] if window else [],
                    },
                    user_input=user_input,
                    entity_id_format=BINARY_SENSOR_ENTITY_ID_FORMAT,
                )
                if result is not None:
                    return result

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                **_schedule_edge_fields(None),
                vol.Optional(CONF_ENTITY_ID): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="schedule",
            data_schema=self.add_suggested_values_to_schema(
                schema, self._prefill or {}
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
                result, errors = await self._resolve_and_create(
                    entity_type=ENTITY_TYPE_LIGHT,
                    name=user_input[CONF_NAME],
                    data={CONF_ENTITY_TYPE: ENTITY_TYPE_LIGHT, **user_input},
                    user_input=user_input,
                    entity_id_format=LIGHT_ENTITY_ID_FORMAT,
                )
                if result is not None:
                    return result

        schema = vol.Schema(
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
                vol.Optional(CONF_ENTITY_ID): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="light",
            data_schema=self.add_suggested_values_to_schema(
                schema, self._prefill or {}
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
                constituents = user_input.get(
                    CONF_TRIGGER_SENSORS, []
                ) + user_input.get(
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
