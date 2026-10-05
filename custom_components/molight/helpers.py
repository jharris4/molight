"""Shared helpers for the MoLight integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_SUPPORTED_COLOR_MODES,
    COLOR_MODES_BRIGHTNESS,
    COLOR_MODES_COLOR,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_RESTORED,
    ATTR_SUPPORTED_FEATURES,
    STATE_UNAVAILABLE,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, restore_state
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_LIGHTS,
    CONF_MAINTAIN_SENSORS,
    CONF_OCCUPANCY_SENSOR,
    CONF_SCHEDULE_DEFINITION,
    CONF_SCHEDULE_INPUTS,
    CONF_SCHEDULE_SOURCE,
    CONF_TRIGGER_SENSORS,
    DATA_RENAMED,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_SCHEDULED_LIGHT,
    SCHEDULE_DEFINITION_BINARY_SENSOR,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State


def molight_config(entry: ConfigEntry) -> dict:
    """Return the effective configuration for a MoLight entry.

    Options flows store the complete edited form, so once options exist they
    fully replace the original data; merging the two would resurrect
    optional fields the user cleared (e.g. removing a light's occupancy
    reference). entity_type is authoritative in data (and is changed only by
    the explicit light-conversion flow), never by ordinary options edits.
    """
    cfg = dict(entry.options) if entry.options else dict(entry.data)
    cfg[CONF_ENTITY_TYPE] = entry.data[CONF_ENTITY_TYPE]
    return cfg


def suggested_entity_id(
    hass: HomeAssistant,
    entry: ConfigEntry,
    entity_id_format: str,
    suffix: str = "",
) -> str | None:
    """Suggested entity_id from an entry's stored object_id, or None to derive.

    Honored only at first registration: HA uniquifies with _2 on a clash and
    the registry pins the id thereafter. The object_id is read straight from
    entry.data (the entity id is immutable and absent from molight_config once
    options exist). `suffix` lets the companion switch parallel its light, e.g.
    light.kitchen -> switch.kitchen_auto_off.
    """
    obj = entry.data.get(CONF_ENTITY_ID)
    if not obj:
        return None
    return async_generate_entity_id(entity_id_format, f"{obj}{suffix}", hass=hass)


def renamed_to(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the ID an entity was renamed to since Home Assistant started."""
    return hass.data.get(DATA_RENAMED, {}).get(entity_id)


def same_entity(hass: HomeAssistant, saved: str | None, current: str | None) -> bool:
    """Return whether a saved entity ID names the current entity.

    A state saved before a rename carries the former ID.
    """
    if saved == current:
        return True
    return (
        saved is not None
        and current is not None
        and (renamed_to(hass, saved) == current)
    )


def match_option(value: str, options: Sequence[Any]) -> str | None:
    """Return the advertised select option a value names, or None.

    An exact match wins, so an option's own padding is kept; padding typed
    around an option is ignored when that leaves a single option.
    """
    if value in options:
        return value
    stripped = value.strip()
    if stripped in options:
        return stripped
    found = [o for o in options if isinstance(o, str) and o.strip() == stripped]
    return found[0] if len(found) == 1 else None


def entity_gone(hass: HomeAssistant, entity_id: str) -> bool:
    """Return True for an entity that is deleted or disabled.

    It has no state, or only the placeholder its disabled entry left,
    and can never report one. A registered, enabled entity without a
    state may yet load.
    """
    state = hass.states.get(entity_id)
    if state is not None and not (
        state.state == STATE_UNAVAILABLE and state.attributes.get(ATTR_RESTORED)
    ):
        return False
    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is None:
        return state is None
    return registry_entry.disabled


@callback
def run_unless_renamed(
    hass: HomeAssistant, entity_id: str, action: Callable[[], None]
) -> CALLBACK_TYPE:
    """Run action for an entity whose state was removed, unless it was renamed.

    A rename removes the state under the former ID too, possibly before the
    registry has finished announcing it: an ID the registry no longer knows
    is judged one loop pass later. Returns what cancels that judgment.
    """
    if er.async_get(hass).async_get(entity_id) is not None:
        action()
        return lambda: None

    @callback
    def _judge() -> None:
        if hass.states.get(entity_id) is None and renamed_to(hass, entity_id) is None:
            action()

    return hass.loop.call_soon(_judge).cancel


class RenamableRestoreEntity(RestoreEntity):
    """A RestoreEntity that keeps its saved state when its entity ID changes.

    Home Assistant removes a renamed entity, saving its state under the ID it
    leaves, and adds the same object again under the new one.
    """

    async def async_will_remove_from_hass(self) -> None:
        """Move the state just saved to the ID the entity is renamed to."""
        await super().async_will_remove_from_hass()
        entry = self.registry_entry
        if entry is None or entry.entity_id == self.entity_id:
            return
        saved = restore_state.async_get(self.hass).last_states
        if (stored := saved.pop(self.entity_id, None)) is not None:
            saved[entry.entity_id] = stored


def _judge_lights(
    hass: HomeAssistant,
    entity_ids: Sequence[str],
    capable: Callable[[State], bool | None],
) -> bool | None:
    """Tri-state capability judgment across a set of real lights.

    True once any member is capable, False only once every member is judged and
    none is, None while any can't be judged. Shared so the runtime and the
    config flow can't drift apart.
    """
    all_judged = True
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        verdict = capable(state) if state is not None else None
        if verdict is None:
            all_judged = False
        elif verdict:
            return True
    return False if all_judged else None


def _color_modes(state: State) -> set[str] | None:
    """Return a light's advertised color modes, or None if it hasn't said yet.

    A light that reports nothing but "unknown" is explicitly undecided, which
    is not the same as being incapable.
    """
    modes = state.attributes.get(ATTR_SUPPORTED_COLOR_MODES)
    if not modes:
        return None
    modes = set(modes)
    return None if modes <= {ColorMode.UNKNOWN} else modes


def lights_support_transition(
    hass: HomeAssistant, entity_ids: Sequence[str]
) -> bool | None:
    """Whether any of these real lights can fade; None when it can't be judged.

    A light always publishes supported_features, even as 0 and even while
    unavailable, so an absent one is genuinely unknown rather than incapable.
    """

    def _capable(state: State) -> bool | None:
        features = state.attributes.get(ATTR_SUPPORTED_FEATURES)
        if features is None:
            return None
        return bool(features & LightEntityFeature.TRANSITION)

    return _judge_lights(hass, entity_ids, _capable)


def lights_support_color_temp(
    hass: HomeAssistant, entity_ids: Sequence[str]
) -> bool | None:
    """Whether any of these real lights can show a white color temperature."""

    def _capable(state: State) -> bool | None:
        modes = _color_modes(state)
        return None if modes is None else ColorMode.COLOR_TEMP in modes

    return _judge_lights(hass, entity_ids, _capable)


def lights_support_color(hass: HomeAssistant, entity_ids: Sequence[str]) -> bool | None:
    """Whether any of these real lights can show a color.

    Uses Home Assistant's own color-capable mode set, which is also what the
    virtual light checks before advertising HS.
    """

    def _capable(state: State) -> bool | None:
        modes = _color_modes(state)
        return None if modes is None else not COLOR_MODES_COLOR.isdisjoint(modes)

    return _judge_lights(hass, entity_ids, _capable)


def lights_support_brightness(
    hass: HomeAssistant, entity_ids: Sequence[str]
) -> bool | None:
    """Whether any of these real lights is dimmable.

    Every color-bearing mode implies brightness, so this only decides the
    floor: brightness-only versus on/off-only.
    """

    def _capable(state: State) -> bool | None:
        modes = _color_modes(state)
        return None if modes is None else not COLOR_MODES_BRIGHTNESS.isdisjoint(modes)

    return _judge_lights(hass, entity_ids, _capable)


def molight_light_entries(
    hass: HomeAssistant, entity_types: tuple[str, ...] = (ENTITY_TYPE_LIGHT,)
) -> dict[str, ConfigEntry]:
    """Map each virtual light's entity_id to its config entry.

    Only entries that have actually registered a light entity appear: the
    entity_id is what the bulk-assign light picker stores, and what a light's
    sensor references are keyed against.
    """
    registry = er.async_get(hass)
    result: dict[str, ConfigEntry] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if molight_config(entry).get(CONF_ENTITY_TYPE) not in entity_types:
            continue
        for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
            if ent.domain == "light":
                result[ent.entity_id] = entry
                break
    return result


MEMBER_LIGHT_TYPES = (ENTITY_TYPE_LIGHT, ENTITY_TYPE_SCHEDULED_LIGHT)


def light_member_ids(
    hass: HomeAssistant,
    entity_id: str,
    lights: dict[str, ConfigEntry],
) -> list[str]:
    """Return the members of a virtual light or a light group."""
    if (entry := lights.get(entity_id)) is not None:
        return molight_config(entry).get(CONF_LIGHTS, [])
    return group_member_ids(hass, entity_id)


def group_member_ids(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Return the members a group names; none for any other entity."""
    if (state := hass.states.get(entity_id)) is not None:
        for key in (ATTR_ENTITY_ID, "group_entities"):
            if isinstance(members := state.attributes.get(key), (list, tuple)):
                return [m for m in members if isinstance(m, str)]
    # An unavailable or unloaded group helper still names them in its options.
    reg_entry = er.async_get(hass).async_get(entity_id)
    if reg_entry is None or reg_entry.platform != "group":
        return []
    group = hass.config_entries.async_get_entry(reg_entry.config_entry_id or "")
    return list(group.options.get("entities", [])) if group is not None else []


# The settings that name the sensors a MoLight binary sensor is computed from.
_SENSOR_INPUT_KEYS = (
    CONF_SCHEDULE_SOURCE,
    CONF_SCHEDULE_INPUTS,
    CONF_OCCUPANCY_SENSOR,
    CONF_TRIGGER_SENSORS,
    CONF_MAINTAIN_SENSORS,
)


def sensor_input_ids(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Return the entities a MoLight binary sensor or a group is computed from.

    What any other entity reads, such as a template, cannot be told.
    """
    reg_entry = er.async_get(hass).async_get(entity_id)
    entry = (
        hass.config_entries.async_get_entry(reg_entry.config_entry_id)
        if reg_entry is not None and reg_entry.config_entry_id is not None
        else None
    )
    if entry is None or entry.domain != DOMAIN:
        return group_member_ids(hass, entity_id)
    if reg_entry.domain != "binary_sensor":
        return []
    cfg = molight_config(entry)
    inputs: list[str] = []
    for key in _SENSOR_INPUT_KEYS:
        if (
            key == CONF_SCHEDULE_SOURCE
            and cfg.get(CONF_SCHEDULE_DEFINITION) != SCHEDULE_DEFINITION_BINARY_SENSOR
        ):
            continue  # left over from before a change to a time window
        value = cfg.get(key) or []
        inputs.extend([value] if isinstance(value, str) else value)
    return inputs


def sensors_depend_on(
    hass: HomeAssistant, entity_ids: Iterable[str], targets: Iterable[str]
) -> bool:
    """Whether any of these sensors is computed from one of the targets.

    Followed through groups and MoLight's own sensors, at any depth.
    """
    targets = set(targets)
    found: set[str] = set()
    pending = list(entity_ids)
    while pending:
        entity_id = pending.pop()
        entity_id = renamed_to(hass, entity_id) or entity_id
        if entity_id in targets:
            return True
        if entity_id not in found:
            found.add(entity_id)
            pending.extend(sensor_input_ids(hass, entity_id))
    return False


def light_descendants(
    hass: HomeAssistant,
    entity_ids: Sequence[str],
    lights: dict[str, ConfigEntry],
) -> set[str]:
    """Return the given lights and every light under them, through any nesting."""
    found: set[str] = set()
    pending = list(entity_ids)
    while pending:
        entity_id = pending.pop()
        entity_id = renamed_to(hass, entity_id) or entity_id
        if entity_id not in found:
            found.add(entity_id)
            pending.extend(light_member_ids(hass, entity_id, lights))
    return found


def lights_commanded(
    hass: HomeAssistant, members: Sequence[str], lights: dict[str, ConfigEntry]
) -> set[str]:
    """Return the lights a virtual light with these members commands itself.

    A light group stands for itself and the lights it contains; a virtual
    light stands for itself alone, since it commands its own members.
    """
    found: set[str] = set()
    pending = list(members)
    while pending:
        entity_id = pending.pop()
        entity_id = renamed_to(hass, entity_id) or entity_id
        if entity_id in found:
            continue
        found.add(entity_id)
        if entity_id not in lights:
            pending.extend(light_member_ids(hass, entity_id, lights))
    return found


def group_leaves(
    hass: HomeAssistant, group_id: str, lights: dict[str, ConfigEntry]
) -> set[str]:
    """Return the lights a light group ends at, through any nested groups.

    A virtual light is one of them: it answers for the lights under it.
    """
    return {
        entity_id
        for entity_id in lights_commanded(hass, [group_id], lights)
        if entity_id in lights or not light_member_ids(hass, entity_id, lights)
    }


def shared_lights(
    hass: HomeAssistant, members: Sequence[str], *, own_entry_id: str | None = None
) -> dict[str, str]:
    """Return lights among these members another virtual light commands.

    Maps each such light to that virtual light's entity id, or its entry's
    title while it has no light entity. Two virtual lights commanding one
    light fight over it, so each light belongs to one; a light under a
    wrapped virtual light is that light's, not the wrapper's.
    """
    lights = molight_light_entries(hass, MEMBER_LIGHT_TYPES)
    names = {entry.entry_id: entity_id for entity_id, entry in lights.items()}
    mine = lights_commanded(hass, members, lights)
    shared: dict[str, str] = {}
    # An entry whose light entity was deleted or not yet set up still has its lights.
    owners = sorted(
        (
            (names.get(entry.entry_id, entry.title), entry)
            for entry in hass.config_entries.async_entries(DOMAIN)
            if molight_config(entry).get(CONF_ENTITY_TYPE) in MEMBER_LIGHT_TYPES
            and entry.entry_id != own_entry_id
        ),
        key=lambda owner: owner[0],
    )
    for name, entry in owners:
        theirs = lights_commanded(
            hass, molight_config(entry).get(CONF_LIGHTS, []), lights
        )
        for light in sorted(mine & theirs):
            shared.setdefault(light, name)
    return shared


# A keep-on entity holds while it is "on", so only these domains can hold.
HOLD_ENTITY_DOMAINS = [
    "alert",
    "automation",
    "binary_sensor",
    "calendar",
    "fan",
    "group",
    "humidifier",
    "input_boolean",
    "light",
    "remote",
    "schedule",
    "script",
    "siren",
    "switch",
    "update",
]


def lights_lit_with(
    hass: HomeAssistant,
    own_entities: Sequence[str],
    members: Sequence[str],
    candidates: Iterable[str],
) -> set[str]:
    """Of the candidates, the lights that are on whenever this light is lit.

    That is a light sharing a member with it, at any depth, or one that
    includes it.
    """
    lights = molight_light_entries(hass, MEMBER_LIGHT_TYPES)
    lit = light_descendants(hass, members, lights).union(own_entities)
    return {
        entity_id
        for entity_id in candidates
        if entity_id.startswith("light.")
        and not lit.isdisjoint(light_descendants(hass, [entity_id], lights))
    }
