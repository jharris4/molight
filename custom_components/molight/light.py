"""Light platform for MoLight's virtual lights.

A VirtualLight controls N real light entities and manages a state machine
that integrates optional occupancy, illuminance, and schedule virtual sensors.

State machine
=============
  IDLE       lights off, no timer
  ACTIVE     lights on, timer running
               → entered on a manual turn-on or a door opening in On open
                 mode, when no occupancy/maintain/door hold applies
  OCCUPIED   lights on, occupied, timer suspended
  COUNTDOWN  occupancy just cleared, turn-off timer running
  SCHEDULED  lights on inside a follow-mode schedule window, no timer
  STANDBY    lights at a Virtual Scheduled Light's standby brightness, no timer
  EFFECT     automatic turn-off imminent, showing the effect stage
  WARN       automatic turn-off imminent, showing the warn stage

State changes
  IDLE + (manual on OR door opening in On open mode) [no active hold]
       → ACTIVE  (start timer immediately)

  IDLE/ACTIVE/COUNTDOWN + the occupancy sensor becomes occupied
       → OCCUPIED  (cancel any running timer)

  OCCUPIED + occupancy clears
       → COUNTDOWN  (start timer)

  ACTIVE/COUNTDOWN + timer finishes
       → EFFECT → WARN → IDLE  (see "Off warning sequence" below)

Off warning sequence
  When the turn-off timer finishes the light can flag the impending off before
  going dark, controlled by these settings (all default to the feature being
  off, so the light turns straight off exactly as before):
    EFFECT: the effect stage (blink/dip to effect_brightness, 0 = fully off) shown
            for effect_duration seconds. Skipped when effect_duration is 0.
    WARN:   the warn stage, at warn_brightness (blank = the brightness the
            light had before the warning) for warn_duration seconds, then off.
            Skipped when warn_duration is 0.
  Each stage can optionally fade into its brightness over effect_transition /
  warn_transition seconds (each validated <= its stage's duration). Separately,
  auto_on_transition / auto_off_transition fade automatic turn-ons and
  turn-offs; manual turn-ons and a manual off never get a *configured*
  transition (a caller-supplied one is forwarded; see "Transition support").
  Throughout EFFECT and WARN the virtual light stays logically on. Any
  re-trigger (the occupancy or maintain occupancy sensor becoming occupied, a manual
  turn-on, a physical dim) cancels the sequence and behaves exactly as if
  the turn-off timer were still running. Re-triggers that carry no brightness
  of their own (occupancy/maintain/door, a virtual turn-on without an explicit
  brightness, a gate lifting, auto-off becoming held) restore the pre-warning
  brightness so the warning is transparent; a physical turn-on or a physical
  dim brings its own brightness, which is honored instead of the pre-warning look.
  Bright-forces-off (control mode) and a follow or Gate and turn off window end
  still turn the lights off during the sequence, as they would while the timer runs.
  Each stage can also show an optional color, either a color temperature
  (effect_color_temp / warn_color_temp) or an RGB color (effect_rgb_color /
  warn_rgb_color, e.g. a red warn stage to make it hard to miss); color-capable
  members show it, brightness-only members just show the stage brightness. A
  warn stage without a color of its own undoes an effect-stage recolor, and
  every re-trigger restores the pre-warning color along with the brightness.
  The pre-warning brightness and color are exposed as the pre_warn_brightness
  / pre_warn_color attributes (null outside the sequence) and survive
  restarts: a restart landing mid-warning with the lights still on restores
  them instead of mirroring the stage's; lights found off stay off (the
  automatic turn-off completed), and unreadable members keep the pre-warning look
  until they report.

  any + physical turn-off
       → IDLE

Standby (Virtual Scheduled Light inside-schedule settings only)
  A standby brightness (and optional color) makes the light stay there
  instead of turning off while the inside settings are active:
  - An off light comes on at standby when switching to the inside settings, at
    startup, or when illuminance in control mode goes dark (control mode
    keeps standby off while bright; illuminance Gate only mode never touches it).
    A raised on-period that going bright cut short is resumed first, as without
    standby.
  - Occupancy and the door raise it to the auto-on brightness like any turn-on
    (the maintain occupancy sensor only holds a raised light); when the timer finishes,
    the effect and warn stages run and the last step drops to standby, not off
    (at the pre-warning color when standby has no color of its own).
  - A physical dim or a virtual turn-on from standby runs a timer that ends
    back at standby; a turn-on with no brightness first raises it to the
    auto-on brightness and color, as presence would. A manual off keeps standby off
    until the next schedule boundary; turning the light back on rejoins it.
  - Leaving the inside settings while at standby hands the light to the At
    schedule end field: turn off, switch state, or keep state (a fresh timer).
  - A restart puts a light that was at standby back there, and a member
    back from unavailable is re-sent the standby settings.

Occupancy turn-on by the light's own system (occupancy_turn_on)
  - own_system: an occupancy edge on an idle light opens a 1 s wait; a
    physical turn-on from off inside it is an occupancy turn-on (OCCUPIED,
    lit by occupancy, last_on_occupancy, turned_on with reason occupancy),
    and skips the gates. The auto-on brightness, color and selection follow
    at the wait's end. MoLight never turns the light on for occupancy, also
    not when it re-checks presence (startup, a settings or gate-mode window
    start, dark).
  - own_system_then_molight: as own_system, and at the wait's end an idle
    light is turned on the normal, gated way; re-checks turn it on as usual.
  - Standby and follow windows behave as with molight. The wait is not saved.

Turn-on attribution
  Five timestamps record the last time the virtual light was turned on and why:
    last_on_physical:    a member light changed to ON through a
                         physical change (a wall switch, another automation, the HA
                         UI acting on the real entity), whether this virtual
                         light was off or already on.
    last_on_virtual:     the person toggled this virtual light entity ON via the HA UI
                         (async_turn_on was called directly).
    last_on_occupancy:   occupancy sensor turned the lights on.
    last_on_illuminance: going dark turned the lights on.
    last_on_door:        a door sensor opening turned the lights on.

  All are exposed as extra state attributes (ISO strings or null). They
  decide whether a manual off still stands, and after a false detection the
  manual and door ones keep the timer from finishing early.

  Brightness changes are tracked the same way: last_brightness_change_physical
  records physical changes on the real lights (and restarts a running
  ACTIVE/COUNTDOWN timer; brightness 0 counts as off, leaving 0 as on), while
  last_brightness_change_virtual records brightness set through this entity.
  Color changes are tracked identically via last_color_change_physical /
  last_color_change_virtual, and a physical recolor restarts a running timer
  exactly like a physical dim, since both are a person's change.

Color support
  The virtual light derives its color capabilities from the real lights: it
  offers HS when any member can show a color (hs/rgb/rgbw/rgbww/xy; HA
  converts hs to each member's native mode) and COLOR_TEMP when any member
  supports it, falling back to brightness, or to plain on/off when no member
  dims at all. Capabilities are
  re-derived on every member event, so members that are unavailable at startup
  contribute theirs once they appear. Color commands are forwarded to ALL
  members in one service call; HA filters/converts the color per real light,
  so mixed setups (color + brightness-only members) just work: each light
  shows what it can. The reported color mirrors the first on member that has
  one, exactly like brightness.

Transition support
  A transition supplied by the caller (a scene, a script, light.turn_on with
  one) is forwarded to the members. HA strips it before async_turn_on unless
  we offer LightEntityFeature.TRANSITION, so _update_capabilities derives
  it from the members and fails open: withheld only once every member is
  visible and none can fade.

Maintain occupancy sensor (when one is configured)
  The maintain occupancy sensor holds an already-on light on while it is on; it never
  turns the light on and is ignored while the light is off. Unlike the
  combined sensor's maintain_sensors (which only extend occupancy started by
  a trigger sensor), it holds the light regardless of how it was lit:
  manually or by occupancy.

  - Maintain ON while the light is on (ACTIVE/COUNTDOWN) → OCCUPIED, timer
    cancelled. Illuminance/schedule gating does not apply: it is not a
    turn-on. Forced offs (bright in control mode, Gate and turn off window end) still
    win, exactly as they do over the occupancy sensor.
  - Occupancy clearing while maintain is on keeps the light OCCUPIED.
  - The turn-off timer starts only when both the occupancy sensor and the
    maintain occupancy sensor are clear, anchored to the max of their
    latest_occupied_time attributes.
  - The false-detection off delay applies on a maintain clear only when both
    sensors flagged their clears false (a genuine detection on either side
    means the light earns its turn-off timer).
  - Startup: a light that is already on with the maintain occupancy sensor on is
    adopted as OCCUPIED (no timer).

Illuminance handling (when an illuminance sensor is configured), per
illuminance_mode:
  - Occupancy only turns lights ON when illuminance is OFF (dark), in both modes.
    In illuminance Gate only mode bright only gates turning an off light on (or a
    raise from standby): occupancy and the door hold and re-trigger an on light as
    usual.
  - Illuminance ON→OFF (bright→dark): if occupied, enter OCCUPIED;
    else if going bright forced the lights off and that on-period has time left,
    enter COUNTDOWN for the rest of it. It lasts until the later of
    bright_resume_until, recorded at the forced off, and a timeout after
    latest_occupied_time. bright_resume_until is when the interrupted
    timer would have finished, or a timeout from the forced off for a light that
    presence was holding (whoever held it was there until then at least); an
    off warning sequence or standby leaves none. An on-period ended any other
    way (manual off, timer, schedule) is never resumed, nor is one that the person
    turned off, or whose gate-mode window ended, while going bright had it off.
  - Illuminance OFF→ON (dark→bright):
      Control:    go IDLE, turn lights off.
      Gate only:  no effect; bright never turns lights off. Use when the lux
                  sensor can see the virtual light's member lights, which would
                  otherwise oscillate (lights on → reads bright → forced off →
                  dark → ...).

Holding auto-off
  Auto-off is *held* while the companion "<name> Auto-off" switch is off OR
  any configured keep-on entity (keep_on_entities) is on. While held, every
  automatic turn-off is suspended (the timer finishing, the false-detection off
  delay, bright-forces-off, and schedule window ends), but turn-ons and manual
  control work exactly as usual (a manual off still turns the lights off).
  State changes keep happening; they just never arm a timer.

  When the last hold releases, the light re-checks its rules from current
  conditions: a follow-mode or Gate and turn off window that ended while held turns it
  off now (the follow window marker, or schedule_end_off_pending, is kept
  while held for exactly this; any off drops it), as does being bright in
  illuminance control mode; being occupied keeps it on (OCCUPIED); an
  active follow window keeps it SCHEDULED; otherwise a fresh timer
  starts (ACTIVE), also outside a window that did not end while held.

  A keep-on entity going unavailable/unknown holds its last known value, as
  everywhere else in the integration; at startup an unavailable keep-on
  entity counts as not holding.

Schedule handling (when a schedule sensor is configured), per schedule_mode:
  - follow: lights turn ON at window start (state SCHEDULED, no timer) and
    OFF at window end. Boundaries are edge-triggered: manual changes between
    them stand. A marker that changes while the schedule stays on (an all-day
    window at midnight) is a window start. schedule_window_start records the
    window whose start we applied; it is restored across restarts so a boundary
    missed while HA was down is applied exactly once at startup, while a
    manual off mid-window is respected. Occupancy/illuminance are ignored
    while SCHEDULED.
  - gate: occupancy may only turn lights on inside the window; window end
    forces lights off (like illuminance going bright), window start
    re-checks occupancy. Outside the window a light that is on ignores
    occupancy and the door: neither holds it.
  - gate_switch: the same turn-on gate for an OFF light. Window end
    recalculates an ON light's state and timer from the current latest_occupied_time;
    a presence hold adopts it, a bright reading in control mode turns it off,
    and a finished timer runs the configured off warning sequence, then turns
    it off.
  - gate_keep: the same gate for turning an OFF light on; once the lights
    are on, occupancy and the door behave as inside the window (adopt, hold,
    re-trigger), and window end preserves the current on-period, including its
    presence hold, timer or warning.

Door handling (when a door sensor is configured), per door_mode:
  Opening the door (state on) is a turn-on trigger, gated by illuminance and
  a gate-mode schedule exactly like occupancy: it only lights the room when
  it is dark (if an illuminance sensor is set) and inside a gate-mode window.
  - On open:      opening turns the lights on with the turn-off timer (ACTIVE);
    the door is otherwise ignored, so closing does nothing and the
    timer finishes even if the door stays open. A momentary trigger.
  - Open & close: the open door holds the lights on with no timer (OCCUPIED,
    just like an occupancy sensor) for as long as it stays open; closing
    starts the turn-off timer, but defers to an occupied occupancy/maintain occupancy
    sensor or auto-off hold so a closed door never cuts the lights over someone
    still present. A held-open door survives a restart the same way a
    maintained light does: an already-on light with the door open is adopted
    as OCCUPIED. Forced offs (bright in control mode, a Gate and turn off window end)
    still win over a held-open door, as they do over occupancy.
    A standing-open door is re-checked when a gate lifts, exactly like
    an already occupied sensor: illuminance going dark or a gate-mode window
    starting turns the lights on and holds them while the door is open. The
    door sensor keeps its last known value, so a sensor that is briefly unavailable
    keeps holding; one unavailable for 60 s counts as closed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    COLOR_MODES_COLOR,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    ENTITY_ID_FORMAT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.components.select import ATTR_OPTIONS
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_OPTION,
    ATTR_RESTORED,
    EVENT_HOMEASSISTANT_STARTED,
    SERVICE_SELECT_OPTION,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    CoreState,
    HomeAssistant,
    callback,
    split_entity_id,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import CONTEXT_RECENT_TIME_SECONDS
from homeassistant.helpers.event import (
    async_call_later,
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import RestoredExtraData
from homeassistant.util import color as color_util
from homeassistant.util.percentage import percentage_to_ranged_value

from .const import (
    ACTIVE_SETTINGS_INSIDE,
    ACTIVE_SETTINGS_OUTSIDE,
    ATTR_ACTIVE_SETTINGS,
    ATTR_ACTIVE_SETTINGS_SCHEDULE,
    ATTR_ACTIVE_SETTINGS_WINDOW,
    ATTR_MANUAL_OFF_CLEARED,
    ATTR_SCHEDULE_END_OFF_PENDING,
    ATTR_SCHEDULE_WINDOW_SCHEDULE,
    ATTR_STANDBY_SUPPRESSED,
    CONF_AUTO_OFF_TRANSITION,
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_AUTO_ON_COLOR_TEMP,
    CONF_AUTO_ON_RGB_COLOR,
    CONF_AUTO_ON_TRANSITION,
    CONF_DOOR_MODE,
    CONF_DOOR_SENSOR,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_COLOR_TEMP,
    CONF_EFFECT_DURATION,
    CONF_EFFECT_RGB_COLOR,
    CONF_EFFECT_TRANSITION,
    CONF_ENTITY_TYPE,
    CONF_FALSE_OFF_DELAY,
    CONF_ILLUMINANCE_MODE,
    CONF_ILLUMINANCE_SENSOR,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_KEEP_ON_ENTITIES,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_SENSOR,
    CONF_MATCH_SCHEDULE_AFTER_RESTART,
    CONF_NAME,
    CONF_OCCUPANCY_SENSOR,
    CONF_OCCUPANCY_TURN_ON,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_SCHEDULE_END_ACTION,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_SENSOR,
    CONF_STANDBY_BRIGHTNESS,
    CONF_STANDBY_COLOR_TEMP,
    CONF_STANDBY_RGB_COLOR,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_TURN_ON_SELECT_SOURCE_ENTITY,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_COLOR_TEMP,
    CONF_WARN_DURATION,
    CONF_WARN_RGB_COLOR,
    CONF_WARN_TRANSITION,
    DATA_AUTO_OFF_ENABLED,
    DEFAULT_DOOR_MODE,
    DEFAULT_EFFECT_BRIGHTNESS,
    DEFAULT_EFFECT_DURATION,
    DEFAULT_FALSE_OFF_DELAY,
    DEFAULT_ILLUMINANCE_MODE,
    DEFAULT_MATCH_SCHEDULE_AFTER_RESTART,
    DEFAULT_OCCUPANCY_TURN_ON,
    DEFAULT_SCHEDULE_END_ACTION,
    DEFAULT_SCHEDULE_MODE,
    DEFAULT_WARN_DURATION,
    DOMAIN,
    DOOR_MODE_OPEN_CLOSE,
    DOOR_UNAVAILABLE_TIMEOUT,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_SCHEDULED_LIGHT,
    EVENT_ADJUSTED,
    EVENT_OFF_WARNING,
    EVENT_TURN_ON_BLOCKED,
    EVENT_TURNED_OFF,
    EVENT_TURNED_ON,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE_ONLY,
    MATCH_SCHEDULE_AFTER_RESTART_INSIDE,
    MATCH_SCHEDULE_AFTER_RESTART_INSIDE_AND_OUTSIDE,
    MATCH_SCHEDULE_AFTER_RESTART_OUTSIDE,
    OCCUPANCY_TURN_ON_MOLIGHT,
    OCCUPANCY_TURN_ON_OWN_SYSTEM,
    OCCUPANCY_TURN_ON_WAIT,
    REASON_BRIGHT,
    REASON_DOOR,
    REASON_FALSE_DETECTION,
    REASON_GONE,
    REASON_ILLUMINANCE,
    REASON_OCCUPANCY,
    REASON_PHYSICAL,
    REASON_REJECTED,
    REASON_RESUME,
    REASON_SCHEDULE,
    REASON_SCHEDULE_END,
    REASON_SCHEDULE_START,
    REASON_STARTUP,
    REASON_TIMER,
    REASON_VIRTUAL,
    REASON_WRAPPED,
    SCHEDULE_END_ACTION_SWITCH,
    SCHEDULE_END_ACTION_TURN_OFF,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_KEEP,
    SCHEDULE_MODE_GATE_SWITCH,
    SIGNAL_AUTO_OFF_TOGGLED,
    STAGE_CANCELLED,
    STAGE_EFFECT,
    STAGE_WARN,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
    STATE_STANDBY,
    STATE_WARN,
)
from .helpers import (
    KEEP_ON_ENTITY_DOMAINS,
    VIRTUAL_LIGHT_TYPES,
    EntityIdFollowingRestoreEntity,
    GoneJudge,
    entity_gone,
    entry_settings,
    group_leaves,
    light_descendants,
    lights_lit_with,
    lights_support_brightness,
    lights_support_transition,
    match_option,
    same_entity,
    send_event,
    suggested_entity_id,
    turn_off_timeout,
    virtual_light_entries,
)
from .issues import (
    ISSUE_KEEP_ON_ALWAYS_ON,
    ISSUE_KEEP_ON_NEVER_ON,
    ISSUE_LIGHT_MEMBER_CYCLE,
    ISSUE_SCHEDULED_LIGHT_NO_SCHEDULE,
    async_check_shared_members,
    async_raise_issue,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, EventStateChangedData, State
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)


def _is_group(state: State | None) -> bool:
    """Whether a member's state is a light group's, which names its members."""
    return state is not None and (
        ATTR_ENTITY_ID in state.attributes or "group_entities" in state.attributes
    )


def _group_members(state: State | None) -> tuple[str, ...]:
    """Return the members a light group's state names."""
    if state is None:
        return ()
    for key in (ATTR_ENTITY_ID, "group_entities"):
        if key in state.attributes:
            return tuple(state.attributes[key] or ())
    return ()


# bright_resume_until of an on-period with no timer: it resumes whenever dark.
_NO_END = datetime.max.replace(tzinfo=UTC)

# A door binary sensor is open while on; a cover stays open until it is closed.
_DOOR_OPEN_STATES = ("on", "open", "opening", "closing")
_DOOR_KNOWN_STATES = (*_DOOR_OPEN_STATES, "off", "closed")


# What a virtual light records when a person changes it, physically or through it.
_HUMAN_TIMESTAMPS = (
    "last_on_physical",
    "last_on_virtual",
    "last_off_manual",
    "last_brightness_change_physical",
    "last_brightness_change_virtual",
    "last_color_change_physical",
    "last_color_change_virtual",
)


def _wrapped_light_acted_by_itself(old_state: State | None, new_state: State) -> bool:
    """Whether a wrapped virtual light's report shows it acting by itself.

    Its timer, stages, sensors, schedule and standby move none of the timestamps
    a person's change would, so a report that leaves them all as they were
    is not a person's change at this level.
    """
    if old_state is None or "molight_state" not in new_state.attributes:
        return False
    old, new = old_state.attributes, new_state.attributes
    return all(old.get(attr) == new.get(attr) for attr in _HUMAN_TIMESTAMPS)


def _wrapped_light_commanded(old_state: State | None, new_state: State) -> bool:
    """Whether a wrapped virtual light took a person's command that shows nothing.

    A turn-on of a light that is on, or an off of one that is off, moves
    only its timestamps. A timestamp from before its last report is one it restored.
    """
    if old_state is None or "molight_state" not in new_state.attributes:
        return False
    old, new = old_state.attributes, new_state.attributes
    off = new_state.state == "off"
    for attr in _HUMAN_TIMESTAMPS:
        if (attr == "last_off_manual") is not off or old.get(attr) == new.get(attr):
            continue
        with contextlib.suppress(ValueError, TypeError):
            if datetime.fromisoformat(new.get(attr)) >= old_state.last_updated:
                return True
    return False


# Member color modes an hs command can drive (HA converts hs to each member's
# native mode); any of them lets the virtual light offer HS itself. Shared
# with the config and options flows so an upstream addition can't split the two.
_HS_CAPABLE_MODES = COLOR_MODES_COLOR


def _opt_transition(value: float | None) -> float | None:
    """Convert a configured transition to float seconds; blank/0 = None."""
    return float(value) if value else None


def _opt_rgb_color(value: list | None) -> dict | None:
    """Convert a configured [r, g, b] to turn-on service data; blank = None."""
    return {ATTR_RGB_COLOR: tuple(int(c) for c in value)} if value else None


def _opt_color(cfg: dict[str, Any], color_temp_key: str, rgb_key: str) -> dict | None:
    """Convert a stored color-pair to turn-on service data; blank = None.

    The keys are mutually exclusive (enforced by the config/options flows);
    the color temperature wins if both somehow appear, matching the remote presets.
    """
    if kelvin := cfg.get(color_temp_key):
        return {ATTR_COLOR_TEMP_KELVIN: int(kelvin)}
    return _opt_rgb_color(cfg.get(rgb_key))


# Single-entity sensor references that a light watches. A Virtual Scheduled Light
# watches both settings sets' references; events from the inactive one simply
# match no role in _handle_state_change.
_WATCHED_REFERENCE_KEYS = (
    CONF_OCCUPANCY_SENSOR,
    CONF_MAINTAIN_OCCUPANCY_SENSOR,
    CONF_ILLUMINANCE_SENSOR,
    CONF_SCHEDULE_SENSOR,
    CONF_DOOR_SENSOR,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up virtual light entities from a config entry."""
    if entry.data[CONF_ENTITY_TYPE] in (
        ENTITY_TYPE_LIGHT,
        ENTITY_TYPE_SCHEDULED_LIGHT,
    ):
        entity = VirtualLight(hass, entry)
        if entity_id := suggested_entity_id(hass, entry, ENTITY_ID_FORMAT):
            entity.entity_id = entity_id
        async_add_entities([entity])


# A member report is our echo only if it is consistent with what we asked for,
# allowing for power-first bulbs, fade steps, quantized replies, or
# another color mode while the command settles, and for slow bulbs whose full
# reply arrives much later; a contradiction is a person's change at any age.
# Context alone cannot tell: Home Assistant reuses our service-call context on
# a member for 5 s, and a slow bulb's genuine reply arrives under its own.
TURN_ON_SELECT_TIMEOUT = 10.0  # seconds a turn-on waits for its select call
ECHO_SETTLE_SECONDS = 3.0  # partial replies (power first, fade steps) count
ECHO_LATE_SECONDS = 30.0  # only a reply matching the command still counts
ECHO_HISTORY = 8  # commands per member whose reply may still be on its way
ECHO_BRIGHTNESS_TOLERANCE = 5
ECHO_HUE_TOLERANCE = 10.0
ECHO_SATURATION_TOLERANCE = 10.0
ECHO_KELVIN_TOLERANCE = 150
HUE_ARC_EPSILON = 1e-3  # float drift when summing hue distances along an arc


def _window_moved(old_state: State, new_state: State) -> bool:
    """Return True when a schedule that stays on moved its window marker."""
    marker = new_state.attributes.get("current_window_start")
    return (
        new_state.state == "on"
        and bool(marker)
        and marker != old_state.attributes.get("current_window_start")
    )


def _state_color(state: State) -> tuple[ColorMode, tuple] | None:
    """Return the color a real light's state reports, in canonical terms.

    A color-temperature member also carries a derived hs_color, so its own
    color_mode decides which attribute is authoritative.
    """
    attrs = state.attributes
    if attrs.get(ATTR_COLOR_MODE) == ColorMode.COLOR_TEMP and (
        kelvin := attrs.get(ATTR_COLOR_TEMP_KELVIN)
    ):
        return (ColorMode.COLOR_TEMP, kelvin)
    if hs := attrs.get(ATTR_HS_COLOR):
        return (ColorMode.HS, tuple(hs))
    return None


def _service_color(color: dict | None) -> tuple[ColorMode, tuple] | None:
    """Canonicalize turn-on color service data like a member state would."""
    if not color:
        return None
    if ATTR_COLOR_TEMP_KELVIN in color:
        return (ColorMode.COLOR_TEMP, color[ATTR_COLOR_TEMP_KELVIN])
    if ATTR_HS_COLOR in color:
        return (ColorMode.HS, tuple(color[ATTR_HS_COLOR]))
    if ATTR_RGB_COLOR in color:
        return (ColorMode.HS, color_util.color_RGB_to_hs(*color[ATTR_RGB_COLOR]))
    return None


def _color_data(color: tuple[ColorMode, tuple] | None) -> dict | None:
    """Turn a canonical color into turn-on service data."""
    if color is None:
        return None
    mode, value = color
    if mode is ColorMode.COLOR_TEMP:
        return {ATTR_COLOR_TEMP_KELVIN: value}
    return {ATTR_HS_COLOR: list(value)}


def _event_color(color: dict | None) -> dict | None:
    """Turn turn-on color service data into the shape events report."""
    return _color_data(_service_color(color))


# A member's brightness and color; in a per-member map, None leaves it out.
_Look = tuple[int | None, dict | None]


def _look_groups(
    lights: list[str],
    brightness: int | None,
    color: dict | None,
    looks: dict[str, _Look | None] | None,
) -> list[tuple[list[str], int | None, dict | None]]:
    """Split member lights into one group per look; unmapped ones get the given one."""
    groups: dict[tuple, tuple[list[str], int | None, dict | None]] = {}
    for entity_id in lights:
        look = (
            (brightness, color)
            if looks is None
            else looks.get(entity_id, (brightness, color))
        )
        if look is None:
            continue
        key = (look[0], _service_color(look[1]))
        groups.setdefault(key, ([], *look))[0].append(entity_id)
    return list(groups.values())


def _restored_looks(saved: Any) -> dict[str, _Look | None]:
    """Read per-member looks back from saved data; anything unparsable is dropped."""
    looks: dict[str, _Look | None] = {}
    if not isinstance(saved, dict):
        return looks
    for entity_id, look in saved.items():
        if look is None:
            looks[entity_id] = None
        elif (
            isinstance(look, (list, tuple))
            and len(look) == 2  # noqa: PLR2004 a (brightness, color) pair
            and (look[0] is None or isinstance(look[0], int))
            and (look[1] is None or isinstance(look[1], dict))
        ):
            color = look[1] and {
                k: look[1][k]
                for k in (ATTR_COLOR_TEMP_KELVIN, ATTR_HS_COLOR)
                if k in look[1]
            }
            looks[entity_id] = (look[0], color or None)
    return looks


def _as_hs(color: tuple[ColorMode, tuple]) -> tuple[float, float]:
    mode, value = color
    if mode is ColorMode.COLOR_TEMP:
        return color_util.color_RGB_to_hs(*color_util.color_temperature_to_rgb(value))
    return (float(value[0]), float(value[1]))


def _hue_delta(a: float, b: float) -> float:
    delta = abs(a - b) % 360
    return min(delta, 360 - delta)


def _colors_close(a: tuple[ColorMode, tuple], b: tuple[ColorMode, tuple]) -> bool:
    """Whether two canonical colors are the same allowing for conversion drift."""
    if a[0] is ColorMode.COLOR_TEMP and b[0] is ColorMode.COLOR_TEMP:
        return abs(a[1] - b[1]) <= ECHO_KELVIN_TOLERANCE
    (hue_a, sat_a), (hue_b, sat_b) = _as_hs(a), _as_hs(b)
    if abs(sat_a - sat_b) > ECHO_SATURATION_TOLERANCE:
        return False
    if sat_a <= ECHO_SATURATION_TOLERANCE and sat_b <= ECHO_SATURATION_TOLERANCE:
        return True  # both near white: hue is noise
    return _hue_delta(hue_a, hue_b) <= ECHO_HUE_TOLERANCE


def _toward(old: int, new: int, target: int) -> bool:
    """Whether a brightness moved from old toward target without reaching it."""
    return (target - old) * (new - old) > 0 and abs(new - target) < abs(old - target)


def _color_toward(
    old: tuple[ColorMode, tuple],
    new: tuple[ColorMode, tuple],
    target: tuple[ColorMode, tuple],
) -> bool:
    """Whether a color moved from old toward target: a fade step, not a recolor.

    Members blend along the hue arc, or straight through RGB or CIE xy.
    """
    if old[0] is new[0] is target[0] is ColorMode.COLOR_TEMP:
        return _toward(old[1], new[1], target[1])
    old_hs, new_hs, target_hs = _as_hs(old), _as_hs(new), _as_hs(target)
    (old_h, old_s), (new_h, new_s), (target_h, target_s) = old_hs, new_hs, target_hs
    # The hue is on the short arc from old to target, the saturation between.
    on_arc = (
        abs(
            _hue_delta(old_h, new_h)
            + _hue_delta(new_h, target_h)
            - _hue_delta(old_h, target_h)
        )
        < HUE_ARC_EPSILON
    )
    if on_arc and (new_s - old_s) * (target_s - new_s) >= 0:
        return True
    return any(
        _on_blend_line(old_hs, new_hs, target_hs, to_point, to_hs)
        for to_point, to_hs in _BLEND_SPACES
    )


def _rgb_point(hue: float, saturation: float) -> tuple[float, float]:
    """Return a color's red and green shares, the same at any brightness."""
    red, green, blue = color_util.color_hs_to_RGB(hue, saturation)
    total = red + green + blue
    return (red / total, green / total)


def _rgb_point_hs(red: float, green: float) -> tuple[float, float]:
    return color_util.color_RGB_to_hs(red, green, 1 - red - green)


# Where a color sits in each space a member may blend straight through, and
# back: a blend between far-apart hues passes through paler colors there.
_BLEND_SPACES = (
    (_rgb_point, _rgb_point_hs),
    (color_util.color_hs_to_xy, color_util.color_xy_to_hs),
)


def _on_blend_line(
    old: tuple[float, float],
    new: tuple[float, float],
    target: tuple[float, float],
    to_point: Callable[[float, float], tuple[float, float]],
    to_hs: Callable[[float, float], tuple[float, float]],
) -> bool:
    """Whether new is on the straight line from old to target, past old."""
    (old_x, old_y), (new_x, new_y), (target_x, target_y) = (
        to_point(*old),
        to_point(*new),
        to_point(*target),
    )
    line_x, line_y = target_x - old_x, target_y - old_y
    length = line_x**2 + line_y**2
    if not length:
        return False
    along = ((new_x - old_x) * line_x + (new_y - old_y) * line_y) / length
    if along <= 0:
        return False  # moved away from the target
    along = min(along, 1.0)
    nearest_x, nearest_y = old_x + along * line_x, old_y + along * line_y
    aside = (new_x - nearest_x) ** 2 + (new_y - nearest_y) ** 2
    if aside >= along**2 * length:
        return False  # moved more to the side than toward the target
    nearest = to_hs(nearest_x, nearest_y)
    return _colors_close((ColorMode.HS, new), (ColorMode.HS, nearest))


@dataclass
class _EchoExpectation:
    """What a member should report back after one of our commands."""

    on: bool
    brightness: int | None
    color: tuple[ColorMode, tuple] | None
    transition: float
    issued: float
    # A later command flipped power: only a reply while settling is its echo.
    overtaken: bool = False
    # A command to only some members leaves what it did not name as reported.
    mirror: bool = True
    # Age of the member's first report after off, while a command with a transition
    # settled: a bulb that reports power first shows the brightness and color it
    # remembered, and its fade runs on from that report.
    came_on: float | None = None
    # Where a command that named no brightness ends: what the member came on at.
    remembered_brightness: int | None = None
    # The command's context, so a call that fails can take its expectation back.
    context_id: str | None = None

    @property
    def target_brightness(self) -> int | None:
        """The brightness the member should end at, if one is known."""
        if self.brightness is not None:
            return self.brightness
        return self.remembered_brightness

    def moved(self, old_state: State | None, new_state: State) -> bool:
        """Whether a report changed anything this command asked for."""
        if old_state is None or old_state.state != new_state.state:
            return True
        old, new = old_state.attributes, new_state.attributes
        if (self.target_brightness is not None or not self.on) and (
            old.get(ATTR_BRIGHTNESS) != new.get(ATTR_BRIGHTNESS)
        ):
            return True
        return self.color is not None and _state_color(old_state) != _state_color(
            new_state
        )

    def judge(
        self,
        old_state: State | None,
        new_state: State,
        *,
        age: float,
        settling: bool,
    ) -> str:
        """Return "match", "pending" (echo still arriving), or "contradiction".

        While the command is settling a partial reply is "pending"; after that
        only a full match is still our echo. When the command carried a transition,
        a member's first report after off settles nothing, and is no echo
        anchor for the report after it while the fade runs on from it: a bulb that
        reports power first shows what it remembered, not where its fade is,
        so a report at the target may still be followed by steps up from
        off. Once the fade has run its length, the report is judged against
        it like any other.
        """
        attrs = new_state.attributes
        powered = new_state.state == "on"
        was_on = old_state is not None and old_state.state == "on"
        if not self.on:
            # On at brightness 0 counts as off, as the state machine reads it.
            new_b = attrs.get(ATTR_BRIGHTNESS)
            if not powered or new_b == 0:
                return "match"
            # A fade to off reports lower brightness on its way there.
            old_b = old_state.attributes.get(ATTR_BRIGHTNESS) if was_on else None
            if settling and old_b and new_b and new_b < old_b:
                return "pending"
            return "contradiction"
        if not powered:
            return "contradiction"
        # A member may report on at brightness 0 first (a dimmer with no brightness
        # yet): only power contradicts an on command; brightness is judged below.
        # Before the member was on we cannot judge its attributes: a bulb that
        # reports power first still carries its previous brightness and color.
        anchored = was_on and not (
            self.came_on is not None and age <= self.came_on + self.transition
        )
        self.came_on = age if self.transition and settling and not was_on else None
        settled = self.came_on is None
        new_b = attrs.get(ATTR_BRIGHTNESS)
        if self.came_on is not None and self.brightness is None and new_b:
            self.remembered_brightness = new_b
        if (target := self.target_brightness) is not None:
            if not new_b:
                # A power-only reply shows no brightness: while settling the brightness
                # may still be coming (power-first bulbs), so keep waiting;
                # late, this is an on/off-only member's complete reply.
                if settling:
                    settled = False
            elif abs(new_b - target) > ECHO_BRIGHTNESS_TOLERANCE:
                old_b = old_state.attributes.get(ATTR_BRIGHTNESS) if anchored else None
                if old_b is not None and not (
                    new_b == old_b or _toward(old_b, new_b, target)
                ):
                    return "contradiction"
                # Without an old brightness there is no direction to judge: a
                # power-first bulb's fade step must not read as a physical dim.
                settled = False
        if self.color is not None:
            new_color = _state_color(new_state)
            if new_color is None:
                # A color-less reply: while settling the color may still be
                # coming; late, this is a color-incapable member's full echo.
                if settling:
                    settled = False
            elif not _colors_close(new_color, self.color):
                if (
                    anchored
                    and (old_color := _state_color(old_state)) is not None
                    and new_color != old_color
                    and not _color_toward(old_color, new_color, self.color)
                ):
                    return "contradiction"
                # Without an old color there is no echo anchor to judge against:
                # a member showing its color late is not a physical recolor.
                settled = False
        if settled:
            return "match"
        return "pending" if settling else "contradiction"


@dataclass
class _Owed:
    """Saved startup decisions owed to members unreadable when the light seeded.

    The light seeded as off, so each stays open until those members report:
    one that reports lit gets it applied, as startup would have.
    """

    members: set[str]
    # A Virtual Scheduled Light's missed end: its off, or its Switch state.
    end_off: bool = False
    end_switch: bool = False
    # Marker of the follow window whose end was missed.
    window: str | None = None
    # The restart matches the schedule outside its window: a member found lit is
    # turned off.
    outside: bool = False
    # The pre-warning brightness and color of an interrupted warning.
    look: tuple[int | None, dict | None] | None = None
    # Each member's own pre-warning look, as _pre_warn_members.
    member_looks: dict[str, _Look | None] = field(default_factory=dict)

    def ends(self) -> bool:
        """Return True while an off is owed: a missed end, or the match outside."""
        return (
            self.end_off or self.end_switch or self.window is not None or self.outside
        )


@dataclass(frozen=True)
class _WaitingTurnOn:
    """A turn-on waiting for its select call."""

    # The command generation it was issued at.
    generation: int
    # Its brightness generation; None for a virtual turn-on.
    auto_brightness_generation: int | None
    # Device of the select entity it called, whatever the settings are now.
    device_id: str | None


_MACHINE_STATES = frozenset(
    {
        STATE_IDLE,
        STATE_ACTIVE,
        STATE_OCCUPIED,
        STATE_COUNTDOWN,
        STATE_SCHEDULED,
        STATE_STANDBY,
        STATE_EFFECT,
        STATE_WARN,
    }
)
# What a virtual light can offer, as a restored state names it.
_RESTORABLE_COLOR_MODES = (
    {ColorMode.HS, ColorMode.COLOR_TEMP},
    {ColorMode.HS},
    {ColorMode.COLOR_TEMP},
    {ColorMode.BRIGHTNESS},
    {ColorMode.ONOFF},
)


class VirtualLight(LightEntity, EntityIdFollowingRestoreEntity):
    """A virtual light with occupancy/illuminance/schedule/door awareness."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    # Reassigned per-instance by _update_capabilities, never mutated in place.
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}
    # Fail open until the first capability derivation (which at startup waits for
    # EVENT_HOMEASSISTANT_STARTED): a startup caller must not lose their transition.
    _attr_supported_features = LightEntityFeature.TRANSITION
    # Home Assistant 2026.1 defaults these to None and warns of mireds when a
    # restored color_temp light reports before its first derivation.
    _attr_min_color_temp_kelvin = DEFAULT_MIN_KELVIN
    _attr_max_color_temp_kelvin = DEFAULT_MAX_KELVIN
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the virtual light from its config entry."""
        self.hass = hass
        cfg = entry_settings(entry)
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self._entry_id = entry.entry_id

        self._lights: list[str] = cfg.get(CONF_LIGHTS, [])
        self._is_scheduled_light = cfg[CONF_ENTITY_TYPE] == ENTITY_TYPE_SCHEDULED_LIGHT
        self._schedule_sensor: str | None = cfg.get(CONF_SCHEDULE_SENSOR)
        self._schedule_end_action = cfg.get(
            CONF_SCHEDULE_END_ACTION, DEFAULT_SCHEDULE_END_ACTION
        )
        self._outside_schedule_settings: dict[str, Any] = cfg.get(
            CONF_OUTSIDE_SCHEDULE_SETTINGS, {}
        )
        self._inside_schedule_settings: dict[str, Any] = cfg.get(
            CONF_INSIDE_SCHEDULE_SETTINGS, {}
        )
        self._inside_schedule = False
        self._restored_inside_schedule: bool | None = None
        # The schedule sensor's window marker, last known and as restored:
        # a different one after a restart means a boundary was crossed.
        self._settings_window_start: str | None = None
        # The schedule sensor's marker when its last valid state was handled.
        self._settings_window_seen: str | None = None
        self._restored_window_start: str | None = None
        # A schedule end off pending an auto-off hold.
        # Restored as a state attribute so a restart cannot lose it; returning
        # inside the schedule cancels it.
        self._schedule_end_off_pending = False
        # Ephemeral startup catch-up for a switch-state boundary missed while
        # Home Assistant was down. The restored active settings prove whether the
        # schedule sensor actually crossed from inside to outside.
        self._schedule_end_switch_pending = False
        # A manual off keeps standby off until the next schedule boundary.
        self._standby_suppressed = False
        # Whether the light was at standby before a restart, so the
        # seed puts it back there instead of adopting it with a timer.
        self._restored_standby = False
        # Every settings mapping this light may run under (both settings sets of a
        # Virtual Scheduled Light, or a regular Virtual Light's entry settings), so
        # the sensor references and keep-on entities of all of them can be watched
        # up front.
        self._settings_sets: tuple[dict[str, Any], ...] = (
            (self._outside_schedule_settings, self._inside_schedule_settings)
            if self._is_scheduled_light
            else (cfg,)
        )
        self._apply_light_settings(self._settings_sets[0])

        self._last_turn_on_selection_option: str | None = None
        self._last_turn_on_selection_source: str | None = None
        # The (select entity, reason) last warned about, so repeats log at debug.
        self._selection_warned: tuple[str, str] | None = None
        # True while the current on-period was started by occupancy (not by
        # the person), the only case where a false-detection clear may cut the
        # lights short.
        self._occupancy_lit_lights: bool = False

        # Brightness and color the light had when the off warning sequence began,
        # restored on any re-trigger so the effect and warn stages leave no
        # lasting trace. Exposed as the pre_warn_brightness / pre_warn_color
        # attributes so they survive a restart landing mid-warning.
        self._pre_warn_brightness: int | None = None
        self._pre_warn_color: dict | None = None
        # Each member's own look then, None for one that was off; a member
        # that could not be read gets the light's.
        self._pre_warn_members: dict[str, _Look | None] = {}
        # Restored in its own right: the pre-warning looks above are legitimately
        # null for members reporting no brightness or color.
        self._warning_active: bool = False
        self._effect_sent_color: bool = False

        # Last known open/closed of the door, kept ourselves so a briefly
        # unavailable sensor (common with battery contact sensors) keeps its last
        # known value instead of reading as closed and dropping its hold.
        self._door_open: bool = False
        # When it was first seen open; its reload moves last_changed.
        self._door_open_since: datetime | None = None
        # Counts an open door that stays unavailable as closed, None when idle.
        self._door_unavailable_unsub: CALLBACK_TYPE | None = None
        # The wait an occupancy edge opens for the light's own system to turn
        # it on, None when closed; the command generation once it has.
        self._occupancy_wait_unsub: CALLBACK_TYPE | None = None
        self._occupancy_wait_generation: int | None = None
        # Whether the door has reported open/closed since startup.
        self._door_seen: bool = False
        # Last known on/off of each keep-on entity, kept ourselves so an
        # unavailable entity keeps its last known value instead of reading as off.
        self._keep_on_states: dict[str, bool] = {}
        # Last known bright/dark, None until first seen; a recovery matching
        # it must not replay the bright/dark edge actions.
        self._illuminance_last_bright: bool | None = None
        # Last known occupied/clear, None until first seen; a recovery
        # matching it must not turn a light that the person turned off on again.
        self._occupancy_last_on: bool | None = None
        # When its visit was first seen; its reload moves last_changed.
        self._occupancy_on_since: datetime | None = None
        # Last known on/off of the maintain occupancy sensor, None until first seen.
        self._maintain_last_on: bool | None = None
        # The last manual off: a start deferred by an unreadable sensor is
        # not applied over it.
        self._last_off_manual: datetime | None = None
        # A schedule boundary since that off, which ends it like a turn-on.
        self._manual_off_cleared = False
        # Members that have reported on/off since this light was set up: a
        # placeholder they leave later is a reload, not startup loading.
        self._members_seen: set[str] = set()
        # Members last read as lit: a placeholder hides what one was doing.
        self._members_lit: set[str] = set()
        # Power last sent to each member that has not reported since.
        self._commanded_power: dict[str, bool] = {}
        # Judgments of failed group commands waiting for a slow member's reply.
        self._failed_command_unsubs: set[CALLBACK_TYPE] = set()
        # Virtual lights inside member light groups, and what they last did:
        # (context ID, whether it was acting by itself).
        self._grouped_lights: set[str] = set()
        self._grouped_unsub: CALLBACK_TYPE | None = None
        self._grouped_reports: deque[tuple[str, bool]] = deque(maxlen=32)
        # Last known on/off of the schedule, None until first seen; a
        # recovery matching it crossed no window boundary.
        self._schedule_last_on: bool | None = None
        # True while the light is off because going bright forced it off: only
        # such an on-period is resumed when it goes dark again.
        self._bright_forced_off: bool = False
        # How long that on-period would have lasted at least; None when
        # only latest_occupied_time can tell.
        self._bright_resume_until: datetime | None = None
        # Auto-off hold: Auto-off switch off OR any keep-on entity on.
        self._held: bool = False
        # Start marker (ISO string) of the follow-mode window we last turned
        # lights on for and whose end we have not yet applied. Survives
        # restarts so missed boundaries are caught up exactly once while
        # a manual off mid-window is respected.
        self._schedule_window_applied: str | None = None

        self._machine_state: str = STATE_IDLE
        self._attr_is_on = False
        # Until the light seeds it reports the on/off and machine state it
        # restored: Home Assistant saves every state as it starts.
        self._showing_restored = False
        # Restored on, and neither proven off nor lit again since the restart.
        self._restored_on = False
        self._seeded = False
        # The power last commanded through this entity before it seeded: it
        # is newer than anything the seed infers from the restored state.
        self._early_command: bool | None = None
        self._owed: _Owed | None = None
        self._timer_unsub: CALLBACK_TYPE | None = None
        # When the armed timer finishes, None while none is armed.
        self._timer_finishes_at: datetime | None = None
        # Why the armed timer turns the light off: timer, or false_detection.
        self._timer_reason = REASON_TIMER
        # Context ids of our own light service calls: while a command settles,
        # only a report under one of them can be its echo.
        self._self_context_ids: deque[str] = deque(maxlen=16)
        # What each member should echo for our recent commands, oldest first
        # (see judge()): a reply to the previous one may trail the latest.
        self._echo_expectations: dict[str, deque[_EchoExpectation]] = {}
        # Counts offs, virtual turn-ons and physical changes, so a turn-on
        # that waited for its turn-on selection can tell that it was
        # overtaken; an automatic turn-on defers to a virtual one still
        # waiting instead.
        self._command_generation = 0
        # Turn-ons waiting for their selection, by the context of the select
        # call.
        self._waiting_turn_ons: dict[str, _WaitingTurnOn] = {}
        # Counts automatic turn-ons at the auto-on or standby brightness, so one
        # that waited for its selection yields to the brightness chosen since.
        self._auto_brightness_generation = 0
        # Brightness, transition, color and per-member looks of the stage shown
        # last: an automatic turn-on that waited for its selection into a
        # stage sends these.
        self._stage_look: (
            tuple[int, float | None, dict | None, dict[str, _Look | None]] | None
        ) = None
        # The lights were last sent a look while a select call still ran; its
        # preset may land on top, so the look is sent again once it ends.
        self._resend_after_select = False
        # Set on removal: work still in flight then sends nothing.
        self._removed = False

        self._last_on_physical: datetime | None = None
        self._last_on_virtual: datetime | None = None
        self._last_on_occupancy: datetime | None = None
        self._last_on_illuminance: datetime | None = None
        self._last_on_door: datetime | None = None
        # When this on-period last began a fresh timer (a hold released,
        # the door closed, a schedule end): an anchor like a turn-on's.
        self._fresh_timer_started: datetime | None = None
        self._last_brightness_change_physical: datetime | None = None
        self._last_brightness_change_virtual: datetime | None = None
        self._last_color_change_physical: datetime | None = None
        self._last_color_change_virtual: datetime | None = None

    def _apply_light_settings(self, cfg: dict[str, Any]) -> None:
        """Load one flat Virtual Light settings mapping.

        A regular Virtual Light applies its entry settings once; a Virtual
        Scheduled Light applies its outside-schedule or inside-schedule settings and
        re-applies the other settings set on every schedule boundary, so everything set
        here must be derivable from the settings set alone.
        """
        self._turn_off_timeout = turn_off_timeout(cfg)
        self._false_off_delay = int(
            cfg.get(CONF_FALSE_OFF_DELAY, DEFAULT_FALSE_OFF_DELAY)
        )

        # Brightness (0-255) for automatic turn-ons, converted from the stored
        # percentage with HA's own percent→brightness scaling. None leaves
        # automatic turn-ons without a brightness, as does a percentage below 1.
        pct = int(cfg.get(CONF_AUTO_ON_BRIGHTNESS) or 0)
        self._auto_on_brightness = (
            round(percentage_to_ranged_value((1, 255), pct)) if pct else None
        )
        # Optional color for automatic turn-ons, as turn-on service data.
        # None leaves automatic turn-ons uncolored, like auto_on_brightness.
        self._auto_on_color = _opt_color(
            cfg, CONF_AUTO_ON_COLOR_TEMP, CONF_AUTO_ON_RGB_COLOR
        )
        # Brightness a finished timer drops to instead of off; None disables
        # standby. Only a Virtual Scheduled Light's inside settings can set it.
        standby_pct = int(cfg.get(CONF_STANDBY_BRIGHTNESS) or 0)
        self._standby_brightness = (
            round(percentage_to_ranged_value((1, 255), standby_pct))
            if standby_pct
            else None
        )
        self._standby_color = _opt_color(
            cfg, CONF_STANDBY_COLOR_TEMP, CONF_STANDBY_RGB_COLOR
        )
        self._turn_on_select_entity = cfg.get(CONF_TURN_ON_SELECT_ENTITY)
        self._turn_on_select_option = cfg.get(CONF_TURN_ON_SELECT_OPTION)
        self._turn_on_select_source_entity = cfg.get(CONF_TURN_ON_SELECT_SOURCE_ENTITY)

        # Off warning sequence run at an automatic turn-off instead of an immediate
        # off. A duration of 0 disables its stage; effect_brightness is a 0-255
        # value (0 = blink fully off); warn_brightness is None to keep whatever
        # brightness the light had before the warning began.
        self._effect_duration = int(
            cfg.get(CONF_EFFECT_DURATION, DEFAULT_EFFECT_DURATION)
        )
        self._effect_brightness = round(
            percentage_to_ranged_value(
                (1, 255),
                int(cfg.get(CONF_EFFECT_BRIGHTNESS, DEFAULT_EFFECT_BRIGHTNESS)),
            )
        )
        self._warn_duration = int(cfg.get(CONF_WARN_DURATION, DEFAULT_WARN_DURATION))
        warn_pct = int(cfg.get(CONF_WARN_BRIGHTNESS) or 0)
        self._warn_brightness = (
            round(percentage_to_ranged_value((1, 255), warn_pct)) if warn_pct else None
        )
        # Optional stage colors, as turn-on service data. None sends no color:
        # the effect stage then only changes brightness, and the warn stage
        # keeps (or, after a colored effect stage, restores) the pre-warning
        # color.
        self._effect_color = _opt_color(
            cfg, CONF_EFFECT_COLOR_TEMP, CONF_EFFECT_RGB_COLOR
        )
        self._warn_color = _opt_color(cfg, CONF_WARN_COLOR_TEMP, CONF_WARN_RGB_COLOR)
        # Optional transitions (seconds) for the service calls this light makes
        # itself: automatic turn-ons/offs and the effect and warn stages.
        # None (blank or 0) sends no transition attribute. Manual turn-ons
        # and a manual off are never given a transition.
        self._auto_on_transition = _opt_transition(cfg.get(CONF_AUTO_ON_TRANSITION))
        self._auto_off_transition = _opt_transition(cfg.get(CONF_AUTO_OFF_TRANSITION))
        self._effect_transition = _opt_transition(cfg.get(CONF_EFFECT_TRANSITION))
        self._warn_transition = _opt_transition(cfg.get(CONF_WARN_TRANSITION))

        # Sensor references. A Virtual Scheduled Light has no schedule mode: its
        # schedule sensor selects settings rather than being followed or gating.
        self._occupancy_sensor = cfg.get(CONF_OCCUPANCY_SENSOR)
        self._occupancy_turn_on = cfg.get(
            CONF_OCCUPANCY_TURN_ON, DEFAULT_OCCUPANCY_TURN_ON
        )
        self._maintain_occupancy_sensor = cfg.get(CONF_MAINTAIN_OCCUPANCY_SENSOR)
        self._illuminance_sensor = cfg.get(CONF_ILLUMINANCE_SENSOR)
        self._illuminance_mode = cfg.get(
            CONF_ILLUMINANCE_MODE, DEFAULT_ILLUMINANCE_MODE
        )
        self._schedule_mode = (
            None
            if self._is_scheduled_light
            else cfg.get(CONF_SCHEDULE_MODE, DEFAULT_SCHEDULE_MODE)
        )
        self._match_schedule_after_restart = cfg.get(
            CONF_MATCH_SCHEDULE_AFTER_RESTART, DEFAULT_MATCH_SCHEDULE_AFTER_RESTART
        )
        self._door_sensor = cfg.get(CONF_DOOR_SENSOR)
        self._door_mode = cfg.get(CONF_DOOR_MODE, DEFAULT_DOOR_MODE)
        self._keep_on_entities = cfg.get(CONF_KEEP_ON_ENTITIES, [])

    # ------------------------------------------------------------------
    # HA lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Defer watching state changes until HA has fully started.

        During startup, entities are restored and integrations initialize in
        arbitrary order, firing spurious state-change events; reacting to them
        could toggle lights or start timers based on incomplete state.
        """
        await super().async_added_to_hass()
        self._removed = False  # an entity ID change removes and adds the same entity
        self._gone = GoneJudge(self.hass)
        self.async_on_remove(self._gone.cancel_all)
        last = await self.async_get_last_state()
        if last is not None:
            if self._is_scheduled_light:
                # A restored choice only belongs to the currently configured
                # schedule; a reload that switched schedules discards it here
                # so no consumer can act on the stale value.
                if same_entity(
                    self.hass,
                    last.attributes.get(ATTR_ACTIVE_SETTINGS_SCHEDULE),
                    self._schedule_sensor,
                ):
                    active = last.attributes.get(ATTR_ACTIVE_SETTINGS)
                    if active in (ACTIVE_SETTINGS_INSIDE, ACTIVE_SETTINGS_OUTSIDE):
                        self._restored_inside_schedule = (
                            active == ACTIVE_SETTINGS_INSIDE
                        )
                    # Only meaningful while still inside; see
                    # _select_initial_settings.
                    self._restored_standby = (
                        last.attributes.get("molight_state") == STATE_STANDBY
                    )
                    self._standby_suppressed = bool(
                        last.attributes.get(ATTR_STANDBY_SUPPRESSED)
                    )
                    self._restored_window_start = last.attributes.get(
                        ATTR_ACTIVE_SETTINGS_WINDOW
                    )
                    self._settings_window_start = self._restored_window_start
                # A schedule end off pending under earlier entry settings no longer
                # applies once the At schedule end field is anything but "turn off".
                # It deliberately survives a schedule swap, unlike the
                # same-schedule-guarded restores above: the off was already
                # observed, not inferred, so the new schedule can't un-happen
                # it.
                self._schedule_end_off_pending = (
                    self._schedule_end_action == SCHEDULE_END_ACTION_TURN_OFF
                    and bool(last.attributes.get(ATTR_SCHEDULE_END_OFF_PENDING))
                )
                # A boundary that ended a manual off was observed too.
                self._manual_off_cleared = bool(
                    last.attributes.get(ATTR_MANUAL_OFF_CLEARED)
                )
            # Restore turn-on attribution: a manual off only stands until a
            # later turn-on.
            for source in ("physical", "virtual", "occupancy", "illuminance", "door"):
                raw = last.attributes.get(f"last_on_{source}")
                if raw:
                    with contextlib.suppress(ValueError, TypeError):
                        setattr(self, f"_last_on_{source}", datetime.fromisoformat(raw))
            # A marker only belongs to the follow schedule that set it. A
            # save that predates the schedule record is trusted.
            saved_schedule = last.attributes.get(ATTR_SCHEDULE_WINDOW_SCHEDULE)
            if (
                self._schedule_sensor
                and self._schedule_mode == SCHEDULE_MODE_FOLLOW
                and (
                    saved_schedule is None
                    or same_entity(self.hass, saved_schedule, self._schedule_sensor)
                )
            ):
                self._schedule_window_applied = last.attributes.get(
                    "schedule_window_start"
                )
            if not self._is_scheduled_light:
                # A Gate and turn off schedule end off pending the hold's release.
                self._schedule_end_off_pending = (
                    self._schedule_mode == SCHEDULE_MODE_GATE
                    and bool(self._schedule_sensor)
                    and bool(last.attributes.get(ATTR_SCHEDULE_END_OFF_PENDING))
                )
            self._bright_forced_off = bool(last.attributes.get("bright_forced_off"))
            if self._bright_forced_off:
                with contextlib.suppress(ValueError, TypeError):
                    self._bright_resume_until = datetime.fromisoformat(
                        last.attributes.get("bright_resume_until")
                    )
            with contextlib.suppress(ValueError, TypeError):
                self._last_off_manual = datetime.fromisoformat(
                    last.attributes.get("last_off_manual")
                )
            for attr, field in (
                ("last_brightness_change_physical", "_last_brightness_change_physical"),
                ("last_brightness_change_virtual", "_last_brightness_change_virtual"),
                ("last_color_change_physical", "_last_color_change_physical"),
                ("last_color_change_virtual", "_last_color_change_virtual"),
            ):
                raw = last.attributes.get(attr)
                if raw and getattr(self, field) is None:
                    with contextlib.suppress(ValueError, TypeError):
                        setattr(self, field, datetime.fromisoformat(raw))
            raw_brightness = last.attributes.get(ATTR_BRIGHTNESS)
            if isinstance(raw_brightness, int):
                self._attr_brightness = raw_brightness
            # Restore the color keyed on the stored color_mode: a color_temp
            # state also stores a derived hs_color (HA computes it for
            # display), so the mode decides which one was authoritative.
            # _seed_state re-derives capabilities and legalizes the mode, so
            # a restore that no longer matches the members is corrected there.
            raw_mode = last.attributes.get(ATTR_COLOR_MODE)
            raw_kelvin = last.attributes.get(ATTR_COLOR_TEMP_KELVIN)
            raw_hs = last.attributes.get(ATTR_HS_COLOR)
            if raw_mode == ColorMode.COLOR_TEMP and isinstance(raw_kelvin, int):
                self._attr_color_mode = ColorMode.COLOR_TEMP
                self._attr_color_temp_kelvin = raw_kelvin
            elif (
                raw_mode == ColorMode.HS
                and isinstance(raw_hs, (list, tuple))
                and len(raw_hs) == 2  # noqa: PLR2004 hs is a (hue, sat) pair
            ):
                self._attr_color_mode = ColorMode.HS
                self._attr_hs_color = tuple(raw_hs)
            # Non-null only when the last state was written mid-warning;
            # _seed_state then undoes the interrupted warning.
            raw_pre_warn = last.attributes.get("pre_warn_brightness")
            if isinstance(raw_pre_warn, int):
                self._pre_warn_brightness = raw_pre_warn
            raw_pre_warn_color = last.attributes.get("pre_warn_color")
            if isinstance(raw_pre_warn_color, dict):
                self._pre_warn_color = {
                    k: raw_pre_warn_color[k]
                    for k in (ATTR_COLOR_TEMP_KELVIN, ATTR_HS_COLOR)
                    if k in raw_pre_warn_color
                } or None
            # Pre-warning_active states can only be judged the old way, as can
            # one saved while a turn-on ending the warning still waited.
            raw_active = last.attributes.get("warning_active")
            self._warning_active = bool(raw_active) or (
                self._pre_warn_brightness is not None
                or self._pre_warn_color is not None
            )
            if self._warning_active:
                extra = await self.async_get_last_extra_data()
                self._pre_warn_members = _restored_looks(
                    (extra.as_dict() if extra is not None else {}).get(
                        "pre_warn_members"
                    )
                )
            self._show_restored(last)

        watch = list(self._lights)
        if self._schedule_sensor:
            watch.append(self._schedule_sensor)
        for settings in self._settings_sets:
            watch.extend(
                entity_id
                for key in _WATCHED_REFERENCE_KEYS
                if (entity_id := settings.get(key))
            )
            watch.extend(settings.get(CONF_KEEP_ON_ENTITIES, []))
        # One entity may serve several roles; watch it only once.
        watch = list(dict.fromkeys(watch))

        unsub_start: CALLBACK_TYPE | None = None
        unsub_early: CALLBACK_TYPE | None = None

        @callback
        def _subscribe(_event: Event | None = None) -> None:
            # When fired via async_listen_once, HA has already removed this
            # one-time listener; drop our reference so teardown doesn't try to
            # remove it a second time (that logs "unknown job listener").
            nonlocal unsub_start
            unsub_start = None
            if unsub_early is not None:
                unsub_early()
            self._seeded = True
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, watch, self._handle_state_change
                )
            )
            self.async_on_remove(
                async_track_entity_registry_updated_event(
                    self.hass, watch, self._handle_registry_change
                )
            )
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_AUTO_OFF_TOGGLED.format(self._entry_id),
                    self._on_auto_off_toggled,
                )
            )
            self._drop_member_cycles()
            self._watch_grouped_lights()
            async_check_shared_members(self.hass)
            self._warn_rejected_keep_on_entities()
            self._select_initial_settings()
            self._seed_state()

        if self.hass.state is CoreState.running:
            _subscribe()
        else:
            unsub_start = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, _subscribe
            )
            unsub_early = async_track_state_change_event(
                self.hass, self._lights, self._on_early_member_report
            )

            @callback
            def _cancel_start() -> None:
                # async_listen_once' remove callback is not idempotent: it
                # auto-removes on fire, so only unsubscribe if it hasn't fired.
                if unsub_start is not None:
                    unsub_start()
                    unsub_early()

            self.async_on_remove(_cancel_start)

    @callback
    def _on_early_member_report(self, event: Event[EventStateChangedData]) -> None:
        """Stop awaiting a command made before the seed once it is outdated.

        A member that someone else has switched the other way since then no longer
        answers it: the seed reads that member as it is.
        """
        entity_id = event.data["entity_id"]
        old_state, new_state = event.data["old_state"], event.data["new_state"]
        if (
            entity_id not in self._echo_expectations
            or old_state is None
            or new_state is None
            or old_state.state not in ("on", "off")
            or new_state.state not in ("on", "off")
        ):
            return  # reporting in is no change
        context = event.context
        echo = self._is_own_echo(
            entity_id,
            old_state,
            new_state,
            own_context=context.id in self._self_context_ids,
            unprompted=context.user_id is None and context.parent_id is None,
        )
        was_lit, lit = (
            state.state == "on" and state.attributes.get(ATTR_BRIGHTNESS) != 0
            for state in (old_state, new_state)
        )
        awaited = self._awaited_power(entity_id)
        if not echo and lit != was_lit and awaited is not None and lit != awaited:
            del self._echo_expectations[entity_id]

    @callback
    def _drop_member_cycles(self) -> bool:
        """Stop driving a member that includes this light, so it can't call itself.

        The flows reject one, but a light group can change, at any depth, or
        predate the check.
        """
        lights = virtual_light_entries(self.hass, VIRTUAL_LIGHT_TYPES)
        cyclic = [
            member
            for member in self._lights
            if self.entity_id in light_descendants(self.hass, [member], lights)
        ]
        for member in cyclic:
            _LOGGER.warning(
                "%s does not control %s, which includes %s itself; remove it from "
                "the lights in Configure",
                self.entity_id,
                member,
                self.entity_id,
            )
            async_raise_issue(
                self.hass,
                self._entry_id,
                ISSUE_LIGHT_MEMBER_CYCLE,
                {"entity": self.entity_id, "member": member},
                about=member,
            )
        self._lights = [m for m in self._lights if m not in cyclic]
        return bool(cyclic)

    @callback
    def _watch_grouped_lights(self) -> None:
        """Follow the virtual lights that member light groups contain.

        The flows reject such a group, but one can be changed to contain a
        virtual light, at any depth. The group hides that light's timestamps.
        """
        lights = virtual_light_entries(self.hass, VIRTUAL_LIGHT_TYPES)
        grouped = {
            leaf
            for member in self._lights
            if member not in lights
            for leaf in group_leaves(self.hass, member, lights)
            if leaf in lights
        }
        if grouped == self._grouped_lights:
            return
        self._grouped_lights = grouped
        if self._grouped_unsub is not None:
            self._grouped_unsub()
        self._grouped_unsub = (
            async_track_state_change_event(
                self.hass, sorted(grouped), self._on_grouped_light_report
            )
            if grouped
            else None
        )

    @callback
    def _on_grouped_light_report(self, event: Event[EventStateChangedData]) -> None:
        """Note whether a virtual light in a member group acted by itself."""
        if (new_state := event.data.get("new_state")) is not None:
            by_itself = _wrapped_light_acted_by_itself(
                event.data.get("old_state"), new_state
            )
            self._grouped_reports.append((event.context.id, by_itself))

    def _group_acted_by_itself(self, context: Context) -> bool:
        """Whether a member group's report is a virtual light in it acting by itself.

        A group reports under the context of the light that changed it.
        """
        causes = [
            by_itself for id_, by_itself in self._grouped_reports if id_ == context.id
        ]
        return bool(causes) and all(causes)

    @callback
    def _warn_rejected_keep_on_entities(self) -> None:
        """Name keep-on entities the form now rejects; they never act as one.

        A light that is on whenever this one is on holds it for good, or, as a
        member, never; an entity that never reads on never holds.
        """
        keep_on_entities = list(
            dict.fromkeys(
                entity_id
                for settings in self._settings_sets
                for entity_id in settings.get(CONF_KEEP_ON_ENTITIES, [])
            )
        )
        always = lights_lit_with(
            self.hass, [self.entity_id], self._lights, keep_on_entities
        )
        for entity_id in keep_on_entities:
            if entity_id in always:
                reason, key = "is on whenever this light is", ISSUE_KEEP_ON_ALWAYS_ON
            elif split_entity_id(entity_id)[0] not in KEEP_ON_ENTITY_DOMAINS:
                reason, key = "never reads on", ISSUE_KEEP_ON_NEVER_ON
            else:
                continue
            _LOGGER.warning(
                "%s keeps %s as a keep-on entity, but it %s; change it in Configure",
                self.entity_id,
                entity_id,
                reason,
            )
            async_raise_issue(
                self.hass,
                self._entry_id,
                key,
                {"entity": self.entity_id, "keep_on_entity": entity_id},
                about=entity_id,
            )

    def _show_restored(self, last: State) -> None:
        """Report the restored state until the light seeds.

        Home Assistant saves every state as it starts, before the seed, and
        other entities read this one meanwhile.
        """
        if self._restored_inside_schedule:
            self._inside_schedule = True
            self._apply_light_settings(self._inside_schedule_settings)
        # An on light must offer the color mode it reports; the seed
        # derives the real capabilities from the members.
        with contextlib.suppress(ValueError, TypeError):
            modes = {
                ColorMode(mode)
                for mode in last.attributes.get(ATTR_SUPPORTED_COLOR_MODES) or ()
            }
            if modes in _RESTORABLE_COLOR_MODES:
                self._attr_supported_color_modes = modes
        if self._attr_color_mode not in self._attr_supported_color_modes:
            self._attr_supported_color_modes = {self._attr_color_mode}
        self._attr_is_on = last.state == "on"
        self._restored_on = self._attr_is_on
        machine_state = last.attributes.get("molight_state")
        if machine_state in _MACHINE_STATES:
            self._machine_state = machine_state
        self._showing_restored = True

    def _drop_restored(self) -> None:
        """Stop reporting the restored on/off and machine state."""
        if self._showing_restored:
            self._showing_restored = False
            self._attr_is_on = False
            self._machine_state = STATE_IDLE

    def _select_initial_settings(self) -> None:
        """Choose a Virtual Scheduled Light's settings once startup state is stable."""
        if not self._is_scheduled_light:
            return
        schedule = (
            self.hass.states.get(self._schedule_sensor)
            if self._schedule_sensor
            else None
        )
        if not self._schedule_sensor:
            inside = False
        elif schedule is not None and schedule.state in ("on", "off"):
            inside = schedule.state == "on"
        elif self._restored_inside_schedule is not None:
            inside = self._restored_inside_schedule
        else:
            inside = False
        marker = (
            schedule.attributes.get("current_window_start")
            if schedule is not None and schedule.state == "on"
            else None
        )
        if not (inside and self._restored_inside_schedule is True) or (
            marker is not None and marker != self._restored_window_start
        ):
            # A boundary was crossed (or nothing was restored): a manual off
            # and being at standby belonged to the previous schedule window.
            self._standby_suppressed = False
            self._restored_standby = False
        if self._restored_inside_schedule is not None and (
            inside != self._restored_inside_schedule
            or (marker is not None and marker != self._restored_window_start)
        ):
            self._manual_off_cleared = True
        if inside:
            self._schedule_end_off_pending = False
            self._schedule_end_switch_pending = False
        elif (
            schedule is not None
            and schedule.state == "off"
            and self._restored_inside_schedule is True
        ):
            # The schedule we were inside ended while Home Assistant was down.
            # _seed_state applies the At schedule end field once after restoring the
            # physical/hold state. A reload that changed schedule sensor has
            # crossed no boundary of the new schedule sensor. A turn-on
            # made since is a new on-period, which the missed end does not end.
            if self._early_command:
                pass
            elif self._schedule_end_action == SCHEDULE_END_ACTION_TURN_OFF:
                self._schedule_end_off_pending = True
                self._clear_bright_forced_off()
            elif self._schedule_end_action == SCHEDULE_END_ACTION_SWITCH:
                self._schedule_end_switch_pending = True
        if not self._schedule_sensor:
            _LOGGER.warning(
                "Virtual Scheduled Light %s has no schedule; "
                "using outside-schedule settings",
                self._attr_name,
            )
            async_raise_issue(
                self.hass,
                self._entry_id,
                ISSUE_SCHEDULED_LIGHT_NO_SCHEDULE,
                {"entity": self.entity_id},
            )
        self._inside_schedule = inside
        self._settings_window_seen = (
            marker
            if schedule is not None and schedule.state in ("on", "off")
            else self._restored_window_start
        )
        self._apply_light_settings(
            self._inside_schedule_settings
            if inside
            else self._outside_schedule_settings
        )
        if self._early_command is False:
            # Turned off after any boundary Home Assistant missed.
            self._manual_off_cleared = False
            self._standby_suppressed = self._standby_brightness is not None

    def _settings_window(self) -> str | None:
        """Window marker of the schedule sensor (last known value if unreadable)."""
        state = self.hass.states.get(self._schedule_sensor or "")
        # Before the seed, the restored marker is the one the light acted on.
        if self._seeded and state is not None and state.state in ("on", "off"):
            self._settings_window_start = state.attributes.get("current_window_start")
        return self._settings_window_start

    def _switch_scheduled_settings(self, inside: bool) -> None:
        """Select a Virtual Scheduled Light settings set and re-check the light."""
        if not self._is_scheduled_light or inside == self._inside_schedule:
            return
        leaving_inside = self._inside_schedule and not inside
        # A manual off only keeps standby off until the next boundary.
        self._standby_suppressed = False
        self._manual_off_cleared = True
        if inside:
            # A pending schedule end off no longer applies once the same schedule
            # window becomes active again.
            self._schedule_end_off_pending = False
            self._schedule_end_switch_pending = False
            self._close_owed_ends()
        self._inside_schedule = inside
        prev_door_sensor = self._door_sensor
        prev_keep_on_states = self._keep_on_states
        prev_illuminance_sensor = self._illuminance_sensor
        prev_occupancy_sensor = self._occupancy_sensor
        prev_maintain_occupancy_sensor = self._maintain_occupancy_sensor
        self._apply_light_settings(
            self._inside_schedule_settings
            if inside
            else self._outside_schedule_settings
        )

        # Seed stateful inputs from their current values. Inactive settings
        # entities remain watched but are ignored by _handle_state_change.
        # An entity carried over from the other settings set keeps its last known value
        # while it reads unavailable/unknown, which at the boundary must not
        # read as "closed" or "hold released", the same rule
        # _handle_state_change applies mid-run.
        door = self.hass.states.get(self._door_sensor) if self._door_sensor else None
        if not (
            door is not None
            and door.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            and self._door_sensor == prev_door_sensor
        ):
            self._cancel_door_unavailable_timer()
            self._read_door(door, same=self._door_sensor == prev_door_sensor)
        self._keep_on_states = {}
        for entity_id in self._keep_on_entities:
            state = self.hass.states.get(entity_id)
            if (
                state is not None
                and state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                and entity_id in prev_keep_on_states
            ):
                self._keep_on_states[entity_id] = prev_keep_on_states[entity_id]
            else:
                self._keep_on_states[entity_id] = (
                    state is not None and state.state == "on"
                )
        if not (
            self._illuminance_sensor == prev_illuminance_sensor
            and (s := self.hass.states.get(self._illuminance_sensor or "")) is not None
            and s.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            self._illuminance_last_bright = self._live_illuminance_bright()
        if not (
            self._occupancy_sensor == prev_occupancy_sensor
            and (s := self.hass.states.get(self._occupancy_sensor or "")) is not None
            and s.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            self._read_occupancy(same=self._occupancy_sensor == prev_occupancy_sensor)
        if not (
            self._maintain_occupancy_sensor == prev_maintain_occupancy_sensor
            and (s := self.hass.states.get(self._maintain_occupancy_sensor or ""))
            is not None
            and s.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            self._maintain_last_on = self._live_maintain_on()
        old_held = self._held
        self._held = self._compute_held()
        # A turn-on still waiting for its selection is judged as on: the off
        # below overtakes it, and the new settings then apply to it.
        lit = self._is_lit()

        if leaving_inside and self._schedule_end_action == SCHEDULE_END_ACTION_TURN_OFF:
            # Turn off also ends an on-period that going bright had cut short.
            self._clear_bright_forced_off()

        if (
            leaving_inside
            and self._schedule_end_action == SCHEDULE_END_ACTION_TURN_OFF
            and lit
        ):
            # An already-off light has nothing to turn off: it takes the
            # normal off-light path below so the outside settings' active
            # sensors are checked exactly as with Keep state. After a
            # forced off the room stays dark until the next sensor edge.
            if not self._held:
                self._finish_schedule_end_off()
                return
            self._schedule_end_off_pending = True
            self._cancel_timer()
            if self._in_warning():
                self._machine_state = STATE_ACTIVE
                self._restore_pre_warning()
            elif self._machine_state == STATE_STANDBY:
                self._machine_state = STATE_ACTIVE
            self.async_write_ha_state()
            return

        if (
            leaving_inside
            and self._schedule_end_action == SCHEDULE_END_ACTION_SWITCH
            and lit
        ):
            # The outside settings are now fully active. Recalculate an on
            # light's state and finish time from its sensors and
            # latest_occupied_time instead of preserving the timer that belonged
            # to the inside settings.
            self._switch_running_state()
            return

        if self._machine_state == STATE_STANDBY and not self._standby_applies():
            # Standby has no end of its own: the new settings' timer ends it.
            self._machine_state = STATE_ACTIVE
            self._start_timer()

        if not lit:
            self._start_settings_for_off_light()
            return

        if self._held and not old_held:
            self._cancel_timer()
            if self._in_warning():
                self._machine_state = STATE_ACTIVE
                self._restore_pre_warning()
            self.async_write_ha_state()
            return

        if old_held and not self._held:
            self._resume_after_hold_release()
            self.async_write_ha_state()
            return

        if (
            self._is_illuminance_bright()
            and self._illuminance_mode == ILLUMINANCE_MODE_CONTROL
            and not self._held
        ):
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle(bright_forced=True, reason=REASON_BRIGHT)
            return

        if self._occupancy_holds() or self._maintain_active() or self._door_holds():
            self._adopt_occupied()
        elif self._machine_state == STATE_OCCUPIED:
            # The previous settings held the light indefinitely; the new ones
            # do not, so begin their turn-off timer now.
            self._machine_state = self._countdown_state()
            self._start_timer()
        self.async_write_ha_state()

    def _start_settings_for_off_light(self) -> None:
        """Apply a schedule boundary to a light that is off."""
        dark = not self._is_illuminance_bright()
        if dark and self._occupancy_active() and self._molight_turns_on_for_occupancy():
            self._on_occupancy_change(occupied=True)
        elif dark and self._door_holds():
            # Not _door_open: in On open mode the door is momentary,
            # and switching settings is not an opening.
            self._on_door_change(True)
        elif self._can_be_at_standby():
            self._enter_standby(
                self._auto_on_transition, selection=True, reason=REASON_SCHEDULE_START
            )
        else:
            self.async_write_ha_state()

    def _on_new_settings_window(self) -> None:
        """Start a window that touches the last one, as the schedule stays on.

        It is a boundary like any other, without leaving the inside settings.
        """
        if not self._inside_schedule:
            return
        # A manual off only keeps standby off until the next boundary.
        self._standby_suppressed = False
        self._manual_off_cleared = True
        if self._is_lit():
            self.async_write_ha_state()
        else:
            self._start_settings_for_off_light()

    def _seed_state(self) -> None:
        """Initialize the machine state from current entity states after startup."""
        # Derive color capabilities from the members before anything mirrors
        # a color (mirroring is a no-op outside the supported modes). Also
        # legalizes a restored color_mode the members no longer support.
        self._update_capabilities()
        self._drop_restored()
        # A command made before the seed is judged again from here.
        self._cancel_timer()
        self._machine_state = STATE_IDLE

        # Unavailable/unknown/missing keep-on entities count as not holding.
        self._keep_on_states = {
            e: (s := self.hass.states.get(e)) is not None and s.state == "on"
            for e in self._keep_on_entities
        }
        self._held = self._compute_held()

        # An unavailable/unknown/missing door counts as closed at startup.
        if self._door_sensor:
            self._read_door(self.hass.states.get(self._door_sensor))

        self._illuminance_last_bright = self._live_illuminance_bright()
        self._schedule_last_on = self._live_schedule_on()
        if self._schedule_last_on and not self._is_scheduled_light:
            self._schedule_end_off_pending = False  # a schedule window started since
        self._read_occupancy()
        self._maintain_last_on = self._live_maintain_on()
        self._seed_from_members(commanded=True)
        self._early_command = None

    def _read_door(self, door: State | None, *, same: bool = False) -> None:
        """Take the door's open/closed from its state, as at startup.

        With same, a door already known open keeps when it was seen to open.
        """
        was_open = same and self._door_open
        self._door_open = door is not None and door.state in _DOOR_OPEN_STATES
        self._door_seen = door is not None and door.state in _DOOR_KNOWN_STATES
        if not self._door_open:
            self._door_open_since = None
        elif not was_open or self._door_open_since is None:
            self._door_open_since = door.last_changed

    def _read_occupancy(self, *, same: bool = False) -> None:
        """Take occupancy's on/off from its state, as at startup.

        With same, a visit already known keeps when it was first seen.
        """
        was_on = same and self._occupancy_last_on
        self._occupancy_last_on = self._live_occupancy_on()
        if not self._occupancy_last_on:
            self._occupancy_on_since = None
        elif not was_on or self._occupancy_on_since is None:
            state = self.hass.states.get(self._occupancy_sensor)
            self._occupancy_on_since = state.last_changed

    def _seed_from_members(self, *, commanded: bool = False) -> None:
        """Settle the saved decisions against the members' power.

        Members that cannot be read were not proven off: what is settled
        without them stays owed to them (see _settle_owed). With commanded,
        a command made before the seed counts for a member yet to answer it.
        """
        self._owed = None
        end_off = self._schedule_end_off_pending
        end_switch = self._schedule_end_switch_pending
        window = self._schedule_window_applied
        look = (
            (self._pre_warn_brightness, self._pre_warn_color)
            if self._warning_active
            else None
        )
        member_looks = self._pre_warn_members
        self._adopt_members(commanded=commanded)
        unreadable = self._unreadable_members()
        # A missed end is only owed while the light is off: a new on-period
        # supersedes it.
        off = not self._is_lit()
        owed = _Owed(
            unreadable,
            end_off=end_off and off,
            end_switch=end_switch and off,
            window=window if off and self._schedule_window_applied is None else None,
            outside=off and self._restart_matches_outside(),
            look=look,
            member_looks=member_looks,
        )
        if unreadable and (owed.ends() or owed.look is not None):
            self._owed = owed
            self.async_write_ha_state()

    def _unreadable_members(self) -> set[str]:
        """Members that cannot be read, so nothing is proven about them.

        A disabled member never reports, so nothing is owed to it.
        """
        registry = er.async_get(self.hass)
        return {
            e
            for e in self._lights
            if self._member_is_lit(e) is None
            and not ((entry := registry.async_get(e)) and entry.disabled)
        }

    def _end_restored_on_period(self) -> None:
        """Tell consumers that the on-period a restart interrupted ended.

        The lights were found off: switched off while Home Assistant was down.
        """
        self._restored_on = False
        self._send_event(EVENT_TURNED_OFF, reason=REASON_STARTUP, standby=False)

    def _adopt_members(self, *, commanded: bool) -> None:
        """Seed from the members that can be read; the others count as off."""
        # Brightness 0 counts as off, matching _all_lights_off.
        self._attr_is_on = any(
            self._member_is_lit(e)
            if not commanded or (awaited := self._awaited_power(e)) is None
            else awaited
            for e in self._lights
        )
        self._members_seen |= {
            e for e in self._lights if self._member_is_lit(e) is not None
        }
        for entity_id in self._lights:
            self._note_member(entity_id)

        # Match the physical brightness and color at startup too, overriding
        # the values restored from our own last state, so the virtual light
        # always tracks the real lights rather than stale restored figures.
        if self._attr_is_on and (brightness := self._first_on_member_brightness()):
            self._attr_brightness = brightness
        if self._attr_is_on and (color := self._first_on_member_color()):
            self._set_color_state(*color)

        if self._restored_on and (self._attr_is_on or self._early_command is not None):
            self._restored_on = False  # lit, or commanded, since the restart
        elif self._restored_on and not self._unreadable_members():
            self._end_restored_on_period()

        if self._schedule_end_off_pending:
            if not self._attr_is_on:
                self._schedule_end_off_pending = False
            elif self._held:
                if self._warning_active:
                    self._restore_pre_warning(found_lit=True)
                # Report the same state the normal seed would: a hold that
                # would otherwise adopt the light keeps it OCCUPIED until the
                # pending schedule end off applies on release.
                self._machine_state = (
                    STATE_OCCUPIED
                    if self._occupancy_holds()
                    or self._maintain_active()
                    or self._door_holds()
                    else STATE_ACTIVE
                )
                self.async_write_ha_state()
                return
            else:
                self._finish_schedule_end_off()
                return

        if self._schedule_end_switch_pending:
            self._schedule_end_switch_pending = False
            if self._attr_is_on:
                if self._warning_active:
                    self._machine_state = STATE_ACTIVE
                    self._restore_pre_warning(found_lit=True)
                self._switch_running_state()
                return

        if self._warning_active:
            if self._attr_is_on:
                # The restart landed mid-warning with the lights still on:
                # undo the warning like any other re-trigger, then seed
                # normally (the warn-stage brightness/color must not be
                # adopted).
                self._restore_pre_warning(found_lit=True)
            else:
                # The lights ended up off (e.g. mid blink-off): treat the
                # automatic turn-off as completed; the room is not turned on again.
                # The light's own look is the one from before the warning.
                if self._pre_warn_brightness is not None:
                    self._attr_brightness = self._pre_warn_brightness
                if self._pre_warn_color:
                    self._mirror_color_data(self._pre_warn_color)
                self._forget_warning()

        if self._standby_seed() or self._follow_schedule_seed():
            return

        # Occupancy only adopts the light when the bright/dark reading and any
        # gate-mode schedule window allow it.
        if (
            self._occupancy_sensor
            and (self._attr_is_on or self._molight_turns_on_for_occupancy())
            and not self._bright_gates_presence()
            and not self._gate_schedule_inactive()
        ):
            occ_state = self.hass.states.get(self._occupancy_sensor)
            if (
                occ_state
                and occ_state.state == "on"
                and not self._may_replay_manual_off(occ_state, observed=False)
            ):
                self._on_occupancy_change(occupied=True)
                return

        # A door that opened while Home Assistant was down lights the room,
        # as one that reports after the seed would.
        if (
            not self._attr_is_on
            and self._door_holds()
            and not self._bright_gates_presence()
            and not self._gate_schedule_inactive()
            and (door_state := self.hass.states.get(self._door_sensor)) is not None
            and not self._may_replay_manual_off(door_state, observed=False)
        ):
            self._on_door_change(is_open=True)
            return

        if self._attr_is_on and (self._maintain_active() or self._door_holds()):
            # Not a turn-on, so only a Gate and turn off window keeps the door from
            # holding; the maintain occupancy sensor clearing, or the door closing,
            # starts the timer as usual.
            self._machine_state = STATE_OCCUPIED
            self.async_write_ha_state()
            return

        if self._attr_is_on:
            # Lights are already on (whatever the illuminance), so adopt them
            # and run the turn-off timer so they still turn off eventually.
            self._machine_state = STATE_ACTIVE
            self._start_timer()

        self.async_write_ha_state()

    def _standby_seed(self) -> bool:
        """Apply standby at startup. Returns True if handled.

        A light that was at standby goes back there, re-sent so an
        edited standby brightness applies; any other lit light is adopted as usual
        and its timer finishes at standby. An off light comes on at standby
        unless occupancy or a held-open door lights it at the auto-on brightness.
        """
        if not self._standby_applies():
            return False
        if self._attr_is_on:
            if not self._restored_standby or self._early_command:
                # Not at standby: lit manually since, or never at standby.
                return False
            if not self._can_be_at_standby() and not self._held:
                # Bright in control mode: standby waits for darkness.
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle(bright_forced=True, reason=REASON_BRIGHT)
            elif (
                self._occupancy_holds() or self._door_holds()
            ) and not self._is_illuminance_bright():
                # Presence arrived while Home Assistant was down: raise,
                # gated by a bright reading like any other raise.
                self._machine_state = STATE_STANDBY
                self._adopt_occupied()
            else:
                self._enter_standby()
            return True
        if not self._can_be_at_standby():
            return False
        if not self._is_illuminance_bright():
            if self._occupancy_active():
                return False  # the normal seed raises it
            if self._door_holds():
                # No opening edge will come from a door that is already open.
                self._on_door_change(True)
                return True
        self._enter_standby(
            self._auto_on_transition, selection=True, reason=REASON_STARTUP
        )
        return True

    def _follow_schedule_seed(self) -> bool:
        """Apply follow-mode schedule state at startup. Returns True if handled.

        The stored schedule window marker distinguishes a boundary missed while HA was
        down (apply it now) from one we already handled before the restart
        (leave the lights alone; if they're off, the person turned them off).
        """
        if not self._schedule_sensor or self._schedule_mode != SCHEDULE_MODE_FOLLOW:
            return False
        sched = self.hass.states.get(self._schedule_sensor)
        if sched is None or sched.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            if self._schedule_window_applied is None:
                return False
            # Not proof the window ended: keep it until a valid state recovers.
            if self._attr_is_on:
                self._machine_state = STATE_SCHEDULED
            self.async_write_ha_state()
            return True

        if sched.state == "on":
            marker = sched.attributes.get("current_window_start")
            if (
                marker
                and marker != self._schedule_window_applied
                and self._early_command is False
            ):
                # Turned off since that window started: a manual off.
                self._schedule_window_applied = marker
                self.async_write_ha_state()
            elif marker and marker != self._schedule_window_applied:
                # Window started while HA was down: catch up now.
                self._apply_window_start(marker)
            elif self._attr_is_on:
                # Already handled this window before the restart; adopt.
                self._machine_state = STATE_SCHEDULED
                self.async_write_ha_state()
            elif self._early_command is None and self._matches_after_restart(
                inside=True
            ):
                # Found off inside the window: the restart matches the schedule.
                self._apply_window_start(marker, reason=REASON_STARTUP)
            else:
                # Already handled, lights off → a manual off; respect it.
                self.async_write_ha_state()
            return True

        if self._schedule_window_applied is not None:
            if self._early_command and self._attr_is_on:
                # Turned on since the window ended: a new on-period.
                self._schedule_window_applied = None
                return False
            if self._held and self._attr_is_on:
                # Auto-off is held: keep the marker so releasing the hold
                # applies the missed off boundary.
                self._machine_state = STATE_SCHEDULED
                self.async_write_ha_state()
                return True
            # Window ended while HA was down: apply the off boundary.
            self._schedule_window_applied = None
            self._machine_state = STATE_IDLE
            if self._attr_is_on:
                self._send_event(
                    EVENT_TURNED_OFF, reason=REASON_SCHEDULE_END, standby=False
                )
                self.hass.async_create_task(self._auto_lights_off())
            self.async_write_ha_state()
            return True

        if self._attr_is_on and self._restart_matches_outside():
            # Found on outside the window: the restart matches the schedule.
            self._send_event(EVENT_TURNED_OFF, reason=REASON_STARTUP, standby=False)
            self.hass.async_create_task(self._auto_lights_off())
            self.async_write_ha_state()
            return True

        return False

    def _restart_matches_outside(self) -> bool:
        """Return True when a restart turns off a light found on outside its window.

        Judged at the seed and again when a member unreadable then reports lit.
        """
        if not self._schedule_sensor or self._schedule_mode != SCHEDULE_MODE_FOLLOW:
            return False
        sched = self.hass.states.get(self._schedule_sensor)
        return (
            sched is not None
            and sched.state == "off"
            and self._schedule_window_applied is None
            and self._early_command is None
            and not self._held
            and self._matches_after_restart(inside=False)
        )

    def _matches_after_restart(self, *, inside: bool) -> bool:
        """Return True when a restart matches the schedule, inside or outside it."""
        both = MATCH_SCHEDULE_AFTER_RESTART_INSIDE_AND_OUTSIDE
        current = (
            MATCH_SCHEDULE_AFTER_RESTART_INSIDE
            if inside
            else MATCH_SCHEDULE_AFTER_RESTART_OUTSIDE
        )
        return self._match_schedule_after_restart in (current, both)

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the turn-off timer and any waiting turn-on on removal."""
        await super().async_will_remove_from_hass()
        self._cancel_timer()
        self._cancel_door_unavailable_timer()
        self._close_occupancy_wait()
        for unsub in self._failed_command_unsubs:
            unsub()
        self._failed_command_unsubs.clear()
        if self._grouped_unsub is not None:
            self._grouped_unsub()
            self._grouped_unsub = None
        # A turn-on still waiting for its selection must not light the room
        # for an entity that is gone, nor send its look again.
        self._removed = True
        self._command_generation += 1
        self._resend_after_select = False

    # ------------------------------------------------------------------
    # LightEntity API
    # ------------------------------------------------------------------

    @property
    def is_on(self) -> bool:
        """Return True when on, or about to be.

        A turn-on waiting for its selection already reports on, so a toggle
        or a brightness step made meanwhile builds on it. _attr_is_on stays
        "the real lights were commanded".
        """
        return self._attr_is_on or self._turn_on_waiting()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on all real lights and change the state machine's state.

        A caller-supplied transition is forwarded; auto_on_transition is not
        (this is the virtual path). A turn-on that also restores the pre-warning
        brightness/color restores it over that same transition.
        """
        # Read before the restored report is dropped: it may be standby.
        at_standby = self._machine_state == STATE_STANDBY
        was_off = at_standby or not self.is_on
        self._drop_restored()
        if not self._seeded:
            # Set before the selection is awaited: the seed may come first.
            self._early_command = True
        now = datetime.now(UTC)
        self._last_on_virtual = now
        self._occupancy_lit_lights = False  # the person owns this on-period now
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        # HA has already narrowed any color request to the modes we offer,
        # so only the canonical hs/color-temperature attributes can arrive here.
        color: dict | None = {
            k: kwargs[k] for k in (ATTR_HS_COLOR, ATTR_COLOR_TEMP_KELVIN) if k in kwargs
        } or None
        asked_brightness, asked_color = brightness, color
        if color is not None:
            self._last_color_change_virtual = now
        # Each member gets back its own pre-warning look where the command
        # names none; one that was off comes on as it would outside a warning.
        looks = (
            self._own_looks(brightness, color, lit_only=False)
            if self._in_warning()
            else None
        )
        if brightness is not None:
            self._last_brightness_change_virtual = now
            self._attr_brightness = brightness
        elif self._in_warning():
            # No explicit brightness: restore the pre-warning brightness so
            # the warning leaves no trace, like any other re-trigger.
            brightness = self._pre_warn_brightness
            if brightness is not None:
                self._attr_brightness = brightness
        elif at_standby:
            # No brightness: raise standby to the auto-on brightness and color, as
            # presence would.
            brightness = self._auto_on_brightness
            if brightness is not None:
                self._attr_brightness = brightness
            color = color or self._auto_on_color
        if color is None and self._in_warning():
            # Same for the color: a colored warning must leave no trace either.
            color = self._pre_warn_color
        if was_off:
            self._send_event(
                EVENT_TURNED_ON,
                reason=REASON_VIRTUAL,
                standby=False,
                brightness=brightness,
                color=_event_color(color),
                selection=self._selection_to_apply(),
            )
        elif asked_brightness is not None or asked_color is not None:
            self._send_event(
                EVENT_ADJUSTED,
                reason=REASON_VIRTUAL,
                brightness=asked_brightness,
                color=_event_color(asked_color),
            )
        if await self._set_lights(
            True,
            brightness=brightness,
            transition=kwargs.get(ATTR_TRANSITION),
            color=color,
            apply_turn_on_selection=True,
            virtual=True,
            looks=looks,
        ):
            self._go_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off all real lights and go idle.

        No configured transition, but a caller-supplied one is forwarded.
        """
        self._drop_restored()
        if not self._seeded:
            self._early_command = False
        # Recorded before the off is reported, so a light wrapping this one
        # reads the timestamp with the off.
        self._last_off_manual = datetime.now(UTC)
        await self._set_lights(False, transition=kwargs.get(ATTR_TRANSITION))
        # Also ends an on-period that going bright had cut short.
        self._clear_bright_forced_off()
        self._go_idle(manual=True, reason=REASON_VIRTUAL)

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """React to a watched entity changing state."""
        entity_id: str = event.data["entity_id"]
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            # Deleted or disabled, unless only its entity ID is changing.
            self._gone.run_unless_entity_id_changed(
                entity_id, partial(self._on_entity_gone, entity_id, old_state)
            )
            return
        if new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            if entity_id == self._door_sensor and self._door_open:
                self._start_door_unavailable_timer()
            if (
                entity_id == self._illuminance_sensor
                and new_state.state == STATE_UNAVAILABLE
                and not new_state.attributes.get(ATTR_RESTORED)
            ):
                # The sensor holds through outages: it has no reading at all.
                self._illuminance_gone()
            return
        same_state = old_state is not None and old_state.state == new_state.state

        # A group below this one can change without this one's list changing,
        # and then reports this light's own state back to it.
        if entity_id in self._lights and (_is_group(new_state) or _is_group(old_state)):
            if self._drop_member_cycles() and entity_id not in self._lights:
                return
            self._watch_grouped_lights()
            if _group_members(old_state) != _group_members(new_state):
                # Which lights this one controls changed, and so may the shared lights.
                async_check_shared_members(self.hass)
        if entity_id in self._lights:
            group_by_itself = _is_group(new_state) and self._group_acted_by_itself(
                event.context
            )
            member_seen = entity_id in self._members_seen
            if new_state.state in ("on", "off"):
                self._members_seen.add(entity_id)
                self._note_member(entity_id)
                self._commanded_power.pop(entity_id, None)
                if (
                    self._restored_on
                    and not member_seen
                    and not self._member_is_lit(entity_id)
                    and not self._is_lit()
                    and not self._unreadable_members()
                ):
                    # The last unreadable member's report proves the lights were
                    # found off at the restart.
                    self._end_restored_on_period()
            # Capabilities can appear late (members unavailable at startup):
            # re-derive on every member event, before the echo check; our own
            # service calls still surface a member's first real state.
            self._update_capabilities()
            member_recovered = old_state is None or old_state.state in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            )
            # A first sighting, or the placeholder HA writes for a registered
            # entity at startup, is startup adoption, not a member back from
            # unavailable. The same placeholder written after the member was
            # seen is its integration reloading, which is one.
            member_rebooted = (
                member_recovered
                and old_state is not None
                and (member_seen or not old_state.attributes.get(ATTR_RESTORED))
            )
            # Standby was sent while this member was still loading, so its
            # first state is not a manual off either.
            resend = member_rebooted or (
                member_recovered and self._machine_state == STATE_STANDBY
            )
            own_context = event.context.id in self._self_context_ids
            # A report that no user or automation is named for: the member's own.
            unprompted = (
                event.context.user_id is None and event.context.parent_id is None
            )
            lit = (
                new_state.state == "on" and new_state.attributes.get("brightness") != 0
            )
            if (
                self._owed is not None
                and entity_id in self._owed.members
                and self._settle_owed(entity_id, lit=lit)
            ):
                return
            if self._is_own_echo(
                entity_id,
                old_state,
                new_state,
                own_context=own_context,
                unprompted=unprompted,
            ):
                # A member back at the brightness that an unanswered command asked for
                # may still have lost its selection: re-send like any other.
                if resend and self._machine_state in (STATE_STANDBY, STATE_SCHEDULED):
                    self._match_recovered_member()
                elif (
                    member_recovered
                    and not lit
                    and not own_context
                    and self._machine_state != STATE_IDLE
                ):
                    # A late reply on at brightness 0 still counts as off.
                    self._resend_on_settings()
                return  # echo of our own service call; call sites manage state
            # A member that our own select call changed was not physically changed.
            claim = not (
                event.context.id in self._waiting_turn_ons
                or self._selecting_on_device_of(entity_id, event.context)
            )
            by_person = not (
                group_by_itself or _wrapped_light_acted_by_itself(old_state, new_state)
            )
            if not claim and lit and self._turn_on_waiting():
                # Lit by the selection of a turn-on about to be sent, which
                # keeps the state it set: only mirror the member.
                self._mirror_member(new_state)
                return
            if not claim and self._resend_after_select:
                # Changed by the selection of a turn-on that a newer command
                # replaced: that command is sent again when the call ends.
                return
            if same_state:
                # Our own command to it moves its timestamps too.
                commanded = not own_context and _wrapped_light_commanded(
                    old_state, new_state
                )
                if new_state.state == "on":
                    self._on_light_attrs_change(
                        old_state,
                        new_state,
                        claim=claim,
                        by_person=by_person,
                        retrigger=commanded,
                    )
                elif commanded and self._in_warning() and self._all_lights_off():
                    # Turned off physically while a stage had it blinked off.
                    self._go_idle(manual=True, reason=REASON_PHYSICAL)
                return
            if resend and self._match_recovered_member():
                return
            if member_recovered and not lit and self._turn_on_waiting():
                # A member that comes back not lit while a turn-on waits for its
                # selection is no off: the turn-on is about to light this member.
                return
            if member_recovered and not lit and self._machine_state != STATE_IDLE:
                # Nor while the light is on: the member missed its command.
                self._resend_on_settings()
                return
            if (
                member_recovered
                and lit
                and (self._machine_state != STATE_IDLE or self._turn_on_waiting())
            ):
                # A member back from unavailable (or first seen) while the
                # virtual light is already on, or is about to be, is not a
                # person's change: mirror its brightness/color but leave the
                # running timer or off warning sequence untouched; a bulb that
                # briefly drops off the mesh with a timer running must not win
                # itself a fresh timer.
                self._mirror_member(new_state)
                return
            if (
                not by_person
                and lit
                and (self._machine_state != STATE_IDLE or self._turn_on_waiting())
            ):
                # A wrapped light that its own sensor lit while this light is on:
                # shown, but no activity that restarts the timer or a warning.
                self._mirror_member(new_state)
                return
            color = self._member_color(new_state) if new_state.state == "on" else None
            if color:
                # Mirror the real light's color on the off→on adoption edge,
                # like the brightness mirror in _on_light_state_change.
                self._set_color_state(*color)
            # A member first seen lit after a restart is adopted as the seed
            # would have adopted it: nobody is known to have turned it on.
            startup_sighting = member_recovered and not member_rebooted
            if startup_sighting and lit:
                self._restored_on = False
            self._on_light_state_change(
                new_state.state,
                new_state.attributes.get("brightness"),
                color=color,
                # A member reporting in was not turned off.
                manual=by_person and not member_recovered,
                claim=claim and by_person,
                touched=entity_id,
                send_turned_on=claim and not startup_sighting,
                by_person=by_person,
            )
            return
        if same_state:
            # Attribute-only change (battery, ...). Touching windows are the
            # exception: the schedule stays on while a new window starts.
            if self._is_new_follow_window(entity_id, old_state, new_state):
                self._on_schedule_change(new_state)
            if (
                self._is_scheduled_light
                and entity_id == self._schedule_sensor
                and _window_moved(old_state, new_state)
            ):
                self._settings_window_seen = new_state.attributes.get(
                    "current_window_start"
                )
                self._on_new_settings_window()
            return
        # A recovery from unavailable/unknown (or a first sighting) that
        # matches the last known value is a replay, not an observed edge.
        # Every role but maintain and keep-on acts on edges, so they skip
        # such replays: a standing-open door whose sensor is briefly unavailable must
        # not fire a fresh "opening".
        recovered = old_state is None or old_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        )
        # One entity may serve several roles (e.g. as both the occupancy and
        # the maintain occupancy sensor), so the role checks are independent, not
        # exclusive. A Virtual Scheduled Light's schedule switches settings first, so
        # any further role it plays is judged against the newly active settings
        # (whose seeding in _switch_scheduled_settings already read it).
        if (
            self._is_scheduled_light
            and entity_id == self._schedule_sensor
            and new_state.state in ("on", "off")
        ):
            inside = new_state.state == "on"
            marker = (
                new_state.attributes.get("current_window_start") if inside else None
            )
            # A window that started during the schedule's outage, with the
            # schedule back in the same state, is a boundary that was missed.
            missed = (
                recovered
                and inside
                and bool(self._inside_schedule)
                and bool(marker)
                and self._settings_window_seen is not None
                and marker != self._settings_window_seen
            )
            self._settings_window_seen = marker
            if missed:
                self._on_new_settings_window()
            else:
                self._switch_scheduled_settings(inside)
        # A keep-on entity engages before its other roles act, so none of them
        # issues an automatic turn-off that it holds; a release is applied after them.
        holding = entity_id in self._keep_on_entities and new_state.state == "on"
        if holding:
            self._keep_on_states[entity_id] = True
            self._refresh_hold()
        if entity_id == self._occupancy_sensor:
            occupied = new_state.state == "on"
            replay = recovered and occupied == self._occupancy_last_on
            # A first sighting since startup is not an observed edge either.
            observed = not (recovered and self._occupancy_last_on is None)
            self._occupancy_last_on = occupied
            if not occupied:
                self._occupancy_on_since = None
            elif not replay or self._occupancy_on_since is None:
                self._occupancy_on_since = new_state.last_changed
            if not replay and not (
                occupied and self._may_replay_manual_off(new_state, observed=observed)
            ):
                self._on_occupancy_change(occupied, edge=True)
            elif (
                self._attr_is_on
                and self._machine_state not in (STATE_OCCUPIED, STATE_SCHEDULED)
                and self._occupancy_holds()
            ):
                # Lights turned on during the outage could not be adopted.
                self._adopt_occupied()
            elif (
                occupied
                and self._machine_state == STATE_IDLE
                and old_state is not None
                and self._gate_lifted_since(
                    self._after_manual_off(old_state.last_changed)
                )
            ):
                # A gate that lifted during the outage read the sensor as
                # clear, so nothing lit the room: apply that start now.
                self._on_occupancy_change(True)
        if entity_id == self._maintain_occupancy_sensor:
            self._maintain_last_on = new_state.state == "on"
            self._on_maintain_change(self._maintain_last_on)
        if entity_id == self._illuminance_sensor:
            bright = new_state.state == "on"
            # The seed could not read it: its first state is no observed edge.
            first = recovered and self._illuminance_last_bright is None
            level_changed = first or bright != self._illuminance_last_bright
            self._illuminance_last_bright = bright
            if not recovered or level_changed:
                self._on_illuminance_change(bright, first=first)
        if (
            not self._is_scheduled_light
            and entity_id == self._schedule_sensor
            and new_state.state in ("on", "off")
        ):
            schedule_on = new_state.state == "on"
            replay = recovered and schedule_on == self._schedule_last_on
            # The seed could not read it: its first state is no boundary.
            first = recovered and self._schedule_last_on is None
            self._schedule_last_on = schedule_on
            self._on_schedule_change(
                new_state,
                replay_since=(old_state or new_state).last_changed if replay else None,
                first=first,
            )
        if entity_id == self._door_sensor:
            self._cancel_door_unavailable_timer()
            door_was_open = self._door_open
            self._door_open = new_state.state in _DOOR_OPEN_STATES
            if not self._door_open:
                self._door_open_since = None
            elif not door_was_open or self._door_open_since is None:
                self._door_open_since = new_state.last_changed
            # An open door first seen since startup may predate a manual off,
            # and is no opening for an On open door.
            first_open = self._door_open and recovered and not self._door_seen
            self._door_seen = True
            # A cover going from open to closing is still open: no edge.
            edge = (
                self._door_open != door_was_open
                if recovered
                else self._door_open != (old_state.state in _DOOR_OPEN_STATES)
            )
            if edge and not (
                first_open
                and (
                    self._door_mode != DOOR_MODE_OPEN_CLOSE
                    or self._may_replay_manual_off(new_state, observed=False)
                )
            ):
                self._on_door_change(self._door_open)
        if entity_id in self._keep_on_entities and not holding:
            self._keep_on_states[entity_id] = False
            self._refresh_hold()

    @callback
    def _handle_registry_change(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        """Notice an entity disabled along with its entry.

        Its state is not removed then: the placeholder of the unloaded
        entry stays, and reads as an outage with no end.
        """
        if event.data["action"] == "update" and "old_entity_id" not in event.data:
            self._on_entity_gone(event.data["entity_id"], None)

    @callback
    def _on_entity_gone(self, entity_id: str, old_state: State | None) -> None:
        """Apply the startup rules to an entity deleted or disabled while running.

        Startup counts a missing keep-on entity as not holding, a missing
        door as closed, a missing occupancy or maintain occupancy sensor as clear and a
        missing illuminance sensor as dark; the entity cannot report any of that
        itself anymore.
        """
        if not entity_gone(self.hass, entity_id):
            return
        if entity_id in self._lights:
            if self._owed is not None and entity_id in self._owed.members:
                self._settle_owed(entity_id, lit=False)
            # Only the real light that was lit can have left them all off;
            # a stage that blinks them off, or a turn-on on its way, has not.
            # One that went by way of a placeholder is judged as last read.
            was_lit = entity_id in self._members_lit
            self._members_lit.discard(entity_id)
            if (
                was_lit
                and self._machine_state != STATE_IDLE
                and not self._turn_on_waiting()
                and self._all_lights_off()
            ):
                self._go_idle(reason=REASON_GONE)
        if entity_id == self._occupancy_sensor:
            self._occupancy_last_on = None
            self._occupancy_on_since = None
        if entity_id == self._maintain_occupancy_sensor:
            self._maintain_last_on = None
        if entity_id == self._illuminance_sensor:
            self._illuminance_gone()
        if entity_id == self._door_sensor:
            # As at startup: closed, and its next report is a first sighting.
            self._cancel_door_unavailable_timer()
            self._door_open = False
            self._door_open_since = None
            self._door_seen = False
        if entity_id in (
            self._occupancy_sensor,
            self._maintain_occupancy_sensor,
            self._door_sensor,
        ):
            self._release_presence()
        if self._keep_on_states.get(entity_id):
            self._keep_on_states[entity_id] = False
            self._refresh_hold()

    def _illuminance_gone(self) -> None:
        """Count an illuminance sensor left with no reading as going dark."""
        if self._illuminance_last_bright:
            self._illuminance_last_bright = False
            self._on_illuminance_change(False)

    def _release_presence(self) -> None:
        """Give a light whose presence hold went missing a full timeout.

        Whoever it saw may still be there, so the light is adopted as
        startup would adopt it, unless other presence still holds it.
        """
        if self._machine_state != STATE_OCCUPIED:
            return
        if self._occupancy_holds() or self._maintain_active() or self._door_holds():
            return
        self._machine_state = STATE_ACTIVE
        self._start_timer()
        self.async_write_ha_state()

    def _settle_owed(self, entity_id: str, *, lit: bool) -> bool:
        """Settle the saved startup decisions with an unreadable member's report.

        Returns True when that handled the report.
        """
        owed = self._owed
        owed.members.discard(entity_id)
        if lit and not self._is_lit():
            # The on-period the restart interrupted is still running on this
            # member: seed again, as startup would have with it readable.
            self._schedule_end_off_pending = owed.end_off
            self._schedule_end_switch_pending = owed.end_switch
            if owed.window is not None:
                self._schedule_window_applied = owed.window
            if owed.look is not None:
                self._warning_active = True
                self._pre_warn_brightness, self._pre_warn_color = owed.look
                self._pre_warn_members = owed.member_looks
            self._seed_from_members()
            return True
        handled = False
        if lit and owed.look is not None and not self._in_warning():
            # The light is on again and this member missed the restore: it
            # is sent its own pre-warning look, or the light's once a command
            # named another, not mirrored at the warning's.
            if own := owed.member_looks.get(entity_id):
                task = self._set_lights(
                    True,
                    brightness=own[0],
                    color=own[1],
                    members=[entity_id],
                    mirror=False,
                )
            else:
                task = self._set_lights(
                    True,
                    brightness=self._attr_brightness,
                    color=self._current_color() or owed.look[1],
                )
            self.hass.async_create_task(task)
            handled = True
        if not owed.members:
            self._owed = None
            self.async_write_ha_state()
        return handled

    def _close_owed_ends(self) -> None:
        """Drop a missed schedule end still owed: a new on-period replaces it."""
        owed = self._owed
        if owed is None or not owed.ends():
            return
        owed.end_off = owed.end_switch = owed.outside = False
        owed.window = None
        if owed.look is None:
            self._owed = None

    def _owed_look(self) -> tuple[int | None, dict | None] | None:
        """Return the pre-warning look that unreadable members owe.

        Only while the light is off: None once it is lit or nothing is owed.
        """
        if self._owed is None or self._is_lit():
            return None
        return self._owed.look

    def _is_new_follow_window(
        self, entity_id: str, old_state: State, new_state: State
    ) -> bool:
        """Return True when a follow schedule that stays on moved its marker."""
        return (
            entity_id == self._schedule_sensor
            and self._schedule_mode == SCHEDULE_MODE_FOLLOW
            and _window_moved(old_state, new_state)
        )

    def _on_light_state_change(
        self,
        state: str,
        brightness: int | None = None,
        *,
        color: tuple[ColorMode, tuple] | None = None,
        manual: bool = True,
        claim: bool = True,
        touched: str | None = None,
        send_turned_on: bool = True,
        by_person: bool = True,
    ) -> None:
        """Handle a real light being turned on/off physically.

        brightness and color are what the real light reported with its on.
        claim is False for a turn-on that our own select call caused. touched is
        the real light that changed. send_turned_on is False when no person
        or other automation is known to have turned it on: our own select
        call, or a member left on after a failed command. by_person is False for
        a wrapped virtual light acting by itself, which the event says.
        """
        reason = REASON_PHYSICAL if by_person else REASON_WRAPPED
        if state == "on" and brightness == 0:
            # "On" at brightness 0 counts as off, matching the dimming
            # path, _all_lights_off and the startup seed. A later 0 → non-zero
            # dim is then handled as the turn-on (in _on_light_attrs_change).
            if self._all_lights_off():
                self._go_idle(manual=manual, reason=reason)
            return
        if state == "on":
            # Mirror the real light's brightness so the virtual light always
            # matches it, including on this off→on adoption edge, not just on
            # later dims (which _on_light_attrs_change handles).
            if brightness:
                self._attr_brightness = brightness
            if self._occupancy_wait_unsub is not None and claim and by_person:
                if self._occupancy_wait_generation is None:
                    if self._machine_state == STATE_IDLE:
                        self._on_own_system_turn_on(brightness, color)
                        return
                elif self._own_system_turn_on_stands():
                    # Another real light of the same turn-on.
                    self.async_write_ha_state()
                    return
            if send_turned_on and (
                not self._attr_is_on or self._machine_state == STATE_STANDBY
            ):
                self._send_event(
                    EVENT_TURNED_ON,
                    reason=reason,
                    standby=False,
                    brightness=brightness,
                    color=_color_data(color),
                    selection=None,
                )
            if claim:
                if send_turned_on:
                    self._last_on_physical = datetime.now(UTC)
                self._claim_on_period()
            if touched is not None and self._in_warning():
                self._restore_untouched(touched)
            self._go_on(rejoin=claim)
        elif self._all_lights_off():
            self._go_idle(manual=manual, reason=reason)
        elif touched is not None and self._in_warning():
            # Switched off while the warning runs on: a re-trigger leaves it off.
            self._pre_warn_members[touched] = None

    def _on_light_attrs_change(
        self,
        old_state: State,
        new_state: State,
        *,
        claim: bool = True,
        by_person: bool = True,
        retrigger: bool = False,
    ) -> None:
        """Handle a physical brightness/color change on an on real light.

        Dimming or recoloring is a person's change: record it and restart any
        running timer with the full timeout. Brightness 0 counts as off. claim
        is False for a change that our own select call caused;
        by_person is False for a wrapped virtual light's own stage or standby,
        which is shown but not acted on. retrigger is a person's turn-on of
        a wrapped virtual light that was on, which changes neither.
        """
        old_b = old_state.attributes.get("brightness")
        new_b = new_state.attributes.get("brightness")
        brightness_changed = new_b is not None and new_b != old_b

        new_color = self._member_color(new_state)
        color_changed = new_color is not None and new_color != self._member_color(
            old_state
        )
        if not brightness_changed and not color_changed and not retrigger:
            return  # some other attribute changed (battery, ...)

        now = datetime.now(UTC)
        if retrigger:
            self._last_on_physical = now
        if color_changed:
            if by_person:
                self._last_color_change_physical = now
            self._set_color_state(*new_color)
        if brightness_changed:
            if by_person:
                self._last_brightness_change_physical = now
            if new_b:
                self._attr_brightness = new_b

            if new_b == 0:
                if self._all_lights_off():
                    self._go_idle(
                        manual=by_person,
                        reason=REASON_PHYSICAL if by_person else REASON_WRAPPED,
                    )
                    return
                if self._in_warning():
                    # Dimmed out while the warning runs on: it stays off.
                    self._pre_warn_members[new_state.entity_id] = None
                self.async_write_ha_state()
                return

        if self._machine_state == STATE_IDLE:
            if brightness_changed:
                # 0 → non-zero while we're idle is a physical turn-on: run
                # its normal logic (last_on_physical,
                # ACTIVE/timer or rejoining an active follow window). A
                # color-only change on a light we consider off is just
                # mirrored.
                self._on_light_state_change(
                    "on",
                    new_b,
                    color=new_color,
                    claim=claim and by_person,
                    send_turned_on=claim,
                    by_person=by_person,
                )
            self.async_write_ha_state()
            return

        if not by_person:
            self.async_write_ha_state()
            return
        if claim:
            self._claim_on_period()
            if self._machine_state == STATE_STANDBY:
                self._send_event(
                    EVENT_TURNED_ON,
                    reason=REASON_PHYSICAL,
                    standby=False,
                    brightness=self._attr_brightness,
                    color=self._current_color(),
                    selection=None,
                )
            elif brightness_changed or color_changed:
                self._send_event(
                    EVENT_ADJUSTED,
                    reason=REASON_PHYSICAL,
                    brightness=new_b if brightness_changed else None,
                    color=_color_data(new_color) if color_changed else None,
                )
        if self._machine_state in (
            STATE_ACTIVE,
            STATE_COUNTDOWN,
            STATE_EFFECT,
            STATE_WARN,
            STATE_STANDBY,
        ):
            # A physical dim or recolor during the off warning sequence is a
            # re-trigger like any other: honor the new brightness/color and
            # start a fresh timer. Off standby it runs a timer that finishes
            # back at standby.
            if self._in_warning():
                # The re-trigger brought only one of brightness and color:
                # restore the other to its pre-warning value so the stage
                # leaves no trace, exactly as a virtual re-trigger does.
                touched = new_state.entity_id
                own = self._pre_warn_members.get(touched) or (
                    self._pre_warn_brightness,
                    self._pre_warn_color,
                )
                brightness = None if brightness_changed else own[0]
                color = None if color_changed else own[1]
                if brightness is not None or color is not None:
                    self.hass.async_create_task(
                        self._set_lights(
                            True, brightness=brightness, color=color, members=[touched]
                        )
                    )
                self._restore_untouched(touched)
            self._cancel_warning(
                new_b if brightness_changed else self._pre_warn_brightness,
                _color_data(new_color) if color_changed else self._pre_warn_color,
            )
            self._machine_state = STATE_ACTIVE
            if self._maintain_active() or self._occupancy_holds() or self._door_holds():
                # Presence that left standby alone holds the raised light,
                # as it does a raise through this entity.
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
            else:
                self._start_timer()
        self.async_write_ha_state()

    def _claim_on_period(self) -> None:
        """Make the on-period the person's after a physical change.

        As a change made through this entity does: a false detection no
        longer cuts it short, a manual off's standby suppression ends, and a
        turn-on still waiting for its selection yields to it.
        """
        self._occupancy_lit_lights = False
        self._standby_suppressed = False
        self._command_generation += 1
        self._resend_after_select = False  # what the physical change has set stands

    def _restore_untouched(self, touched: str) -> None:
        """Undo the warning on the real lights that a physical change left alone.

        The change cancels the warning for all of them, so the others go
        back to their pre-warning look, as after any other re-trigger. The
        virtual light keeps reporting what the changed one shows.
        """
        others = [entity_id for entity_id in self._lights if entity_id != touched]
        if others:
            self.hass.async_create_task(
                self._set_lights(
                    True,
                    brightness=self._pre_warn_brightness,
                    color=self._pre_warn_color,
                    members=others,
                    mirror=False,
                    looks=self._own_looks(),
                )
            )

    def _mirror_member(self, state: State) -> None:
        """Report a real light's brightness and color without acting on them."""
        if brightness := state.attributes.get("brightness"):
            self._attr_brightness = brightness
        if color := self._member_color(state):
            self._set_color_state(*color)
        self.async_write_ha_state()

    def _selecting_on_device_of(self, entity_id: str, context: Context) -> bool:
        """Return True for a report that a select call on this member's device made.

        A preset that lights its device's light is reported by a push of the
        device's, under a context of its own. Each call speaks for the device
        it was made on, also once the settings name another select entity.
        A report that a user or another automation is named for is theirs.
        """
        if context.user_id is not None or (
            context.parent_id is not None
            and context.parent_id not in self._waiting_turn_ons
        ):
            return False
        member = er.async_get(self.hass).async_get(entity_id)
        if member is None or member.device_id is None:
            return False
        return any(
            waiting.device_id == member.device_id
            for waiting in self._waiting_turn_ons.values()
        )

    def _turn_on_select_device(self) -> str | None:
        """Return the device of the active settings' select entity, if any."""
        if not self._turn_on_select_entity:
            return None
        registry_entry = er.async_get(self.hass).async_get(self._turn_on_select_entity)
        return registry_entry.device_id if registry_entry is not None else None

    def _all_lights_off(self) -> bool:
        """Return True when every real light is off (brightness 0 is off)."""
        for entity_id in self._lights:
            state = self.hass.states.get(entity_id)
            if state is None:
                # Registered but stateless: an integration that hasn't loaded
                # (yet); the bulb may still be lit, so don't assume off.
                # A deleted or disabled member can never report again;
                # counting it as "maybe on" would pin the virtual light on
                # forever.
                if not entity_gone(self.hass, entity_id):
                    return False
                continue
            if state.state == "on" and state.attributes.get("brightness") != 0:
                return False
        return True

    def _first_on_member_brightness(self) -> int | None:
        """Brightness of the first on real light reporting one, else None."""
        for entity_id in self._lights:
            state = self.hass.states.get(entity_id)
            if (
                state is not None
                and state.state == "on"
                and (brightness := state.attributes.get("brightness"))
            ):
                return brightness
        return None

    # ------------------------------------------------------------------
    # Color support
    # ------------------------------------------------------------------

    def _update_capabilities(self) -> None:
        """Derive this light's color capabilities from the real lights.

        Offers the canonical HS/COLOR_TEMP pair instead of the union of
        member modes: HS when any member can show a color (HA converts hs to
        each member's native rgb/rgbw/rgbww/xy on the way out) and COLOR_TEMP
        when any member supports it, so two modes cover every member while
        keeping this entity's own color state simple. With no color capability
        reported it falls back to brightness, or to plain on/off once every
        member has been judged and none of them dims.
        """
        member_modes: set[str] = set()
        min_kelvins: list[int] = []
        max_kelvins: list[int] = []
        for entity_id in self._lights:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            member_modes.update(state.attributes.get(ATTR_SUPPORTED_COLOR_MODES) or ())
            if kelvin := state.attributes.get(ATTR_MIN_COLOR_TEMP_KELVIN):
                min_kelvins.append(kelvin)
            if kelvin := state.attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN):
                max_kelvins.append(kelvin)

        supported: set[ColorMode] = set()
        if member_modes & _HS_CAPABLE_MODES:
            supported.add(ColorMode.HS)
        if ColorMode.COLOR_TEMP in member_modes:
            supported.add(ColorMode.COLOR_TEMP)
        if not supported:
            # A group of plugs can only do on/off, and offering a slider
            # none of them can move would be a lie. Fail open as ever.
            supported = (
                {ColorMode.ONOFF}
                if lights_support_brightness(self.hass, self._lights) is False
                else {ColorMode.BRIGHTNESS}
            )

        changed = supported != self._attr_supported_color_modes
        # Most permissive color temperature range; each member clamps to its own
        # range. Track and reset it (to HA's defaults) so a member back from
        # unavailable with a different range doesn't leave the virtual light
        # offering kelvins no member can hit.
        color_temp_range = (
            min(min_kelvins) if min_kelvins else DEFAULT_MIN_KELVIN,
            max(max_kelvins) if max_kelvins else DEFAULT_MAX_KELVIN,
        )
        if color_temp_range != (
            self._attr_min_color_temp_kelvin,
            self._attr_max_color_temp_kelvin,
        ):
            self._attr_min_color_temp_kelvin = color_temp_range[0]
            self._attr_max_color_temp_kelvin = color_temp_range[1]
            changed = True

        # Fail open like the floor above: an unreadable member must not cost the
        # caller their transition. Withheld only once every member is judged.
        supported_features = (
            LightEntityFeature(0)
            if lights_support_transition(self.hass, self._lights) is False
            else LightEntityFeature.TRANSITION
        )
        if supported_features != self._attr_supported_features:
            self._attr_supported_features = supported_features
            changed = True

        self._attr_supported_color_modes = supported
        if self._attr_color_mode not in supported:
            # Keep the reported mode legal for the new capability set; the
            # value-bearing attributes only survive where they still apply.
            if supported in ({ColorMode.BRIGHTNESS}, {ColorMode.ONOFF}):
                self._attr_hs_color = None
                self._attr_color_temp_kelvin = None
                self._attr_color_mode = next(iter(supported))
            elif self._attr_color_temp_kelvin and ColorMode.COLOR_TEMP in supported:
                self._attr_color_mode = ColorMode.COLOR_TEMP
            elif ColorMode.HS in supported:
                self._attr_color_mode = ColorMode.HS
                self._attr_color_temp_kelvin = None
            else:
                self._attr_color_mode = ColorMode.COLOR_TEMP
                self._attr_hs_color = None
            changed = True
        if changed:
            self.async_write_ha_state()

    def _set_color_state(self, mode: ColorMode, value: float | tuple) -> None:
        """Set a commanded or mirrored color as this light's reported color.

        A no-op for modes we don't offer, notably everything on a
        brightness-only virtual light.
        """
        if mode not in (self._attr_supported_color_modes or ()):
            return
        self._attr_color_mode = mode
        if mode is ColorMode.COLOR_TEMP:
            self._attr_color_temp_kelvin = int(value)
            self._attr_hs_color = None
        else:
            self._attr_hs_color = tuple(value)
            self._attr_color_temp_kelvin = None

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature, within the range the real lights span.

        A kelvin outside it is still sent as configured; no real light shows
        it, and the nearest end of the range is what they come on at.
        """
        kelvin = self._attr_color_temp_kelvin
        if kelvin is None:
            return None
        return min(
            max(kelvin, self._attr_min_color_temp_kelvin),
            self._attr_max_color_temp_kelvin,
        )

    def _mirror_color_data(self, color: dict) -> None:
        """Mirror turn-on color service data into this light's own state."""
        if ATTR_COLOR_TEMP_KELVIN in color:
            self._set_color_state(ColorMode.COLOR_TEMP, color[ATTR_COLOR_TEMP_KELVIN])
        elif ATTR_HS_COLOR in color:
            self._set_color_state(ColorMode.HS, color[ATTR_HS_COLOR])
        elif ATTR_RGB_COLOR in color:
            # Configured stage/auto-on colors are stored as rgb; the members
            # get the rgb verbatim while we report its hs equivalent.
            self._set_color_state(
                ColorMode.HS, color_util.color_RGB_to_hs(*color[ATTR_RGB_COLOR])
            )

    def _current_color(self) -> dict | None:
        """Return this light's current color as turn-on service data, or None.

        The hs value is a list so the dict is JSON-serializable; it is also
        exposed as the pre_warn_color attribute to survive restarts.
        """
        if self._attr_color_mode == ColorMode.COLOR_TEMP and (
            kelvin := self.color_temp_kelvin
        ):
            return {ATTR_COLOR_TEMP_KELVIN: kelvin}
        if self._attr_color_mode == ColorMode.HS and (hs := self._attr_hs_color):
            return {ATTR_HS_COLOR: list(hs)}
        return None

    def _member_color(self, state: State) -> tuple[ColorMode, tuple] | None:
        """Return the color a real light's state reports, in canonical terms."""
        return _state_color(state)

    def _expect_echo(
        self,
        on: bool,
        brightness: int | None,
        color: dict | None,
        transition: float | None,
        members: list[str] | None = None,
        *,
        context_id: str | None = None,
    ) -> None:
        """Record what the members commanded (all, by default) should report."""
        expectation = _EchoExpectation(
            on,
            brightness if on else None,
            _service_color(color) if on else None,
            float(transition or 0),
            self.hass.loop.time(),
            mirror=members is None,
            context_id=context_id,
        )
        for expectations in self._echo_expectations.values():
            for earlier in expectations:
                if earlier.on != on:
                    earlier.overtaken = True
        plain = expectation.brightness is None and expectation.color is None
        for entity_id in self._lights if members is None else members:
            # A member already there has nothing to report, and the unanswered
            # expectation would pass a later person's change off as its echo.
            if plain and self._member_is_lit(entity_id) is on:
                continue
            self._echo_expectations.setdefault(
                entity_id, deque(maxlen=ECHO_HISTORY)
            ).append(self._member_expectation(entity_id, expectation))

    def _awaited_power(self, entity_id: str) -> bool | None:
        """Power asked of a member by a command it may still be answering."""
        expectations = self._echo_expectations.get(entity_id)
        if not expectations:
            return None
        newest = expectations[-1]
        age = self.hass.loop.time() - newest.issued
        if age > ECHO_LATE_SECONDS + newest.transition:
            return None
        return newest.on

    def _member_expectation(
        self, entity_id: str, expectation: _EchoExpectation
    ) -> _EchoExpectation:
        """Expect a color the way this member can show it, in a copy of its own.

        The virtual light offers the union of its members' ranges; a member
        clamps a kelvin outside its own and reports the clamped value. For a
        member with a color temperature but no color, Home Assistant turns a
        color into the nearest color temperature. Each member's copy keeps
        track of that member's replies alone.
        """
        color = expectation.color
        state = self.hass.states.get(entity_id)
        if color is None or state is None:
            return replace(expectation)
        if color[0] is ColorMode.COLOR_TEMP:
            kelvin = color[1]
        else:
            modes = set(state.attributes.get(ATTR_SUPPORTED_COLOR_MODES) or ())
            if ColorMode.COLOR_TEMP not in modes or modes & _HS_CAPABLE_MODES:
                return replace(expectation)
            kelvin = color_util.color_xy_to_temperature(
                *color_util.color_hs_to_xy(*color[1])
            )
        if low := state.attributes.get(ATTR_MIN_COLOR_TEMP_KELVIN):
            kelvin = max(kelvin, low)
        if high := state.attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN):
            kelvin = min(kelvin, high)
        return replace(expectation, color=(ColorMode.COLOR_TEMP, kelvin))

    def _is_lit(self) -> bool:
        """On, or about to be: a turn-on may still be waiting for its selection."""
        return (
            self._attr_is_on
            or self._machine_state != STATE_IDLE
            or self._turn_on_waiting()
        )

    def _overtaken(self, waiting: _WaitingTurnOn) -> bool:
        """Return True when a newer command replaced a waiting turn-on."""
        return waiting.generation != self._command_generation or (
            waiting.auto_brightness_generation is not None
            and waiting.auto_brightness_generation != self._auto_brightness_generation
        )

    def _turn_on_waiting(self, *, virtual: bool = False) -> bool:
        """Return True while a turn-on that nothing overtook waits for its selection.

        With virtual, only one that the person made through this entity counts.
        """
        return any(
            not self._overtaken(waiting)
            for waiting in self._waiting_turn_ons.values()
            if waiting.auto_brightness_generation is None or not virtual
        )

    def _note_member(self, entity_id: str) -> None:
        """Remember whether a real light that can be read is lit."""
        lit = self._member_is_lit(entity_id)
        if lit:
            self._members_lit.add(entity_id)
        elif lit is not None:
            self._members_lit.discard(entity_id)

    def _member_is_lit(self, entity_id: str) -> bool | None:
        """Whether a real light is on (brightness 0 is off), None if unknown."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on" and state.attributes.get(ATTR_BRIGHTNESS) != 0

    def _is_own_echo(
        self,
        entity_id: str,
        old_state: State | None,
        new_state: State,
        *,
        own_context: bool,
        unprompted: bool,
    ) -> bool:
        """Judge a member report against our recent commands to it.

        While a command settles, only a report under our own context that is
        consistent with what we asked for is its echo (Home Assistant reuses
        that context for the member's reply). It stops doing so after five
        seconds, so from then on a longer fade's steps count under a context
        of the member's own (unprompted), when they change something we
        asked for. Later, a slow bulb's reply arrives under its own context,
        so a report fully matching the command still counts, unless it names
        a user or an automation; anything else (stale, missing,
        contradicted) is a real change. A reply to an
        earlier command may arrive after a newer one
        was sent, so each command still awaiting its reply is tried, newest
        first. Once a newer command flipped power, the older one's reply only
        counts while it settles, or a physical flip back would pass as it.
        """
        now = self.hass.loop.time()
        waiting: list[_EchoExpectation] = []
        contradicted: list[_EchoExpectation] = []
        matched: _EchoExpectation | None = None
        # A first report after off, answering a transition: mirrored, not settled.
        lit: _EchoExpectation | None = None
        pending = False
        for expectation in reversed(self._echo_expectations.get(entity_id, ())):
            age = now - expectation.issued
            if age > ECHO_LATE_SECONDS + expectation.transition:
                continue
            settling = age <= ECHO_SETTLE_SECONDS + expectation.transition
            if expectation.overtaken and not settling:
                contradicted.append(expectation)
                continue
            if not settling and not own_context and not unprompted:
                # A user's or automation's command, not a slow bulb's reply.
                contradicted.append(expectation)
                continue
            if settling and not own_context:
                fade_step = (
                    unprompted
                    and age > CONTEXT_RECENT_TIME_SECONDS
                    and expectation.moved(old_state, new_state)
                )
                if not fade_step:
                    waiting.append(expectation)
                    continue
            verdict = expectation.judge(
                old_state, new_state, age=age, settling=settling
            )
            if verdict == "match":
                matched = expectation
                break  # answered, and with it every older command
            if verdict == "pending":
                pending = True
                if expectation.came_on is not None and lit is None:
                    lit = expectation
                waiting.append(expectation)
            else:
                contradicted.append(expectation)
        if matched is not None or pending:
            # Only the command the reply belongs to is settled by it.
            waiting += contradicted
        waiting.sort(key=lambda expectation: expectation.issued)
        if waiting:
            self._echo_expectations[entity_id] = deque(waiting, maxlen=ECHO_HISTORY)
        else:
            self._echo_expectations.pop(entity_id, None)
        if matched is not None:
            self._mirror_plain_echo(matched, waiting)
        elif lit is not None:
            others = [expectation for expectation in waiting if expectation is not lit]
            self._mirror_plain_echo(lit, others)
        return matched is not None or pending

    def _mirror_plain_echo(
        self, expectation: _EchoExpectation, waiting: list[_EchoExpectation]
    ) -> None:
        """Mirror what the members came on at when our command named no value.

        A value named by a command still awaiting its reply is left alone. A
        named color that we could not report when it was sent (an unreadable member
        brings its color modes with its reply) is mirrored now.
        """
        if not expectation.on or not expectation.mirror:
            return
        commands = [expectation, *waiting]
        brightness = color = None
        if all(command.brightness is None for command in commands):
            brightness = self._first_on_member_brightness()
        if all(command.color is None for command in waiting) and (
            expectation.color is None or self._current_color() is None
        ):
            color = self._first_on_member_color()
        if brightness is None and color is None:
            return
        if brightness is not None:
            self._attr_brightness = brightness
        if color is not None:
            self._set_color_state(*color)
        self.async_write_ha_state()

    def _first_on_member_color(self) -> tuple[ColorMode, tuple] | None:
        """Color of the first on real light reporting one, else None."""
        for entity_id in self._lights:
            state = self.hass.states.get(entity_id)
            if (
                state is not None
                and state.state == "on"
                and (color := self._member_color(state))
            ):
                return color
        return None

    def _is_illuminance_bright(self) -> bool:
        """Return True when illuminance is bright enough to suppress lighting."""
        live = self._live_illuminance_bright()
        # Unreadable (an outage, a reload): the last known value stands.
        return bool(self._illuminance_last_bright) if live is None else live

    def _bright_gates_presence(self) -> bool:
        """Return True when brightness keeps occupancy or a door from acting.

        In illuminance Gate only mode it only stops an off light turning on or being
        raised from standby: an on light is held and re-triggered as usual.
        """
        return self._is_illuminance_bright() and (
            self._illuminance_mode != ILLUMINANCE_MODE_GATE_ONLY
            or not self._attr_is_on
            or self._machine_state == STATE_STANDBY
        )

    def _gate_schedule_inactive(self) -> bool:
        """Return True when a gate-mode schedule blocks turning lights on.

        gate_switch and gate_keep gate turn-ons only: once the lights are
        on, occupancy and the door behave as inside the window (adopt, hold,
        re-trigger). Their difference is whether the window end
        recalculates or preserves the running state/timer. An unreadable
        schedule blocks turn-ons, but ends no hold that its window allowed.
        """
        if not self._schedule_sensor or self._schedule_mode not in (
            SCHEDULE_MODE_GATE,
            SCHEDULE_MODE_GATE_SWITCH,
            SCHEDULE_MODE_GATE_KEEP,
        ):
            return False
        if (
            self._schedule_mode in (SCHEDULE_MODE_GATE_SWITCH, SCHEDULE_MODE_GATE_KEEP)
            and self._attr_is_on
        ):
            return False
        live = self._live_schedule_on()
        if live is None and self._presence_held():
            return not self._schedule_last_on
        return not live

    def _gate_and_turn_off_schedule_inactive(self) -> bool:
        """Return True when a Gate and turn off schedule reads off."""
        if self._schedule_mode != SCHEDULE_MODE_GATE or not self._schedule_sensor:
            return False
        state = self.hass.states.get(self._schedule_sensor)
        return state is not None and state.state == "off"

    def _follow_schedule_state(self) -> State | None:
        """Return the schedule state when in follow mode and ON, else None."""
        if not self._schedule_sensor or self._schedule_mode != SCHEDULE_MODE_FOLLOW:
            return None
        state = self.hass.states.get(self._schedule_sensor)
        return state if state is not None and state.state == "on" else None

    # ------------------------------------------------------------------
    # Auto-off hold
    # ------------------------------------------------------------------

    def _auto_off_enabled(self) -> bool:
        """State of the companion Auto-off switch (mirrored via hass.data)."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        return entry_data.get(DATA_AUTO_OFF_ENABLED, True)

    def _compute_held(self) -> bool:
        return not self._auto_off_enabled() or any(self._keep_on_states.values())

    @callback
    def _on_auto_off_toggled(self) -> None:
        self._refresh_hold()

    def _refresh_hold(self) -> None:
        """Re-derive the auto-off hold and act on engage/release edges."""
        held = self._compute_held()
        if held == self._held:
            return
        self._held = held
        if held:
            # Suspend any pending automatic turn-off; the machine state stays put.
            self._cancel_timer()
            if self._in_warning():
                # Auto-off just became held mid-warning: cancel the sequence and
                # restore the light. Held, so ACTIVE arms no timer.
                self._machine_state = STATE_ACTIVE
                self._restore_pre_warning()
        else:
            self._resume_after_hold_release()
        self.async_write_ha_state()

    def _resume_after_hold_release(self) -> None:
        """Return to normal behavior when the last hold releases.

        Automatic turn-offs suppressed while held are applied: a follow or
        Gate and turn off window that ended while held, or bright in control mode,
        turns the lights off now; an active follow window or being occupied
        (gated like any adoption: suppressed outside a gate-mode window) keeps them
        on without a timer; otherwise a fresh timer starts, also outside
        a window that did not end while held. A turn-on still waiting for its
        selection counts as on.
        """
        if not self._is_lit():
            # Lights-off state changes were never suppressed.
            self._forget_ended_follow_window()
            return

        if self._schedule_end_off_pending:
            self._finish_schedule_end_off()
            return

        sched = self._follow_schedule_state()
        if sched is not None:
            # Active follow window owns the lights, with no timer.
            self._apply_window_start(sched.attributes.get("current_window_start"))
            return
        schedule_state = (
            self.hass.states.get(self._schedule_sensor)
            if self._schedule_sensor
            else None
        )
        if (
            self._schedule_mode == SCHEDULE_MODE_FOLLOW
            and self._schedule_window_applied is not None
            and (
                schedule_state is None
                or schedule_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            )
        ):
            # An unreadable schedule is not proof that its active window ended.
            # Keep the applied window until a valid state recovers.
            self._machine_state = STATE_SCHEDULED
            self.async_write_ha_state()
            return
        if (
            self._schedule_sensor
            and self._schedule_mode == SCHEDULE_MODE_FOLLOW
            and self._schedule_window_applied is not None
        ):
            # The follow window that we turned on for ended while held.
            self._schedule_window_applied = None
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle(reason=REASON_SCHEDULE_END)
            return

        at_standby = self._machine_state == STATE_STANDBY and self._standby_applies()
        if (
            self._is_illuminance_bright()
            and self._illuminance_mode == ILLUMINANCE_MODE_CONTROL
        ):
            if not (at_standby or self._maintain_active() or self._door_holds()):
                # Going dark resumes the fresh timer that this release would start.
                self._start_timer()
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle(bright_forced=True, reason=REASON_BRIGHT)
            return
        if at_standby:
            return  # at standby there is no timer to resume

        # Occupancy adoption is gated exactly like every other adoption path
        # (_occupancy_holds); the maintain occupancy sensor is never gated once the
        # light is on, and an Open & close door only by a Gate and turn off window.
        if self._occupancy_holds() or self._maintain_active() or self._door_holds():
            self._machine_state = STATE_OCCUPIED
            return

        self._machine_state = STATE_ACTIVE
        self._start_timer()

    # ------------------------------------------------------------------
    # Schedule handling
    # ------------------------------------------------------------------

    def _switch_running_state(self) -> None:
        """Recalculate an already-on light and replace its running timer's finish time.

        Used by Gate and switch state and the Switch state choice of At schedule end.
        The applicable settings have already been selected. A presence hold adopts the
        light; otherwise a configured occupancy/maintain occupancy sensor contributes
        its latest_occupied_time, with a fresh timer when no usable
        one exists. With no such sensor there is likewise no trustworthy
        timer anchor, so the new settings receive a fresh timer.
        A turn-on still waiting for its selection counts as on.
        """
        if not self._is_lit():
            return

        was_standby = self._machine_state == STATE_STANDBY
        was_warning = self._in_warning()
        self._cancel_timer()
        if was_warning:
            # Cancel the outgoing settings' off warning sequence before the
            # newly applicable settings decide whether/how an automatic turn-off is due.
            self._machine_state = STATE_ACTIVE
            self._restore_pre_warning()

        if (
            self._is_illuminance_bright()
            and self._illuminance_mode == ILLUMINANCE_MODE_CONTROL
            and not self._held
        ):
            # Going dark resumes what these settings would have run.
            if self._maintain_active() or self._door_holds():
                self._machine_state = STATE_OCCUPIED
            else:
                self._start_timer(self._compute_occupancy_timer())
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle(bright_forced=True, reason=REASON_BRIGHT)
            return

        if self._occupancy_holds() or self._maintain_active() or self._door_holds():
            self._machine_state = STATE_OCCUPIED
            self.async_write_ha_state()
            return

        has_occupancy_or_maintain = bool(
            self._occupancy_sensor or self._maintain_occupancy_sensor
        )
        self._machine_state = (
            self._countdown_state() if has_occupancy_or_maintain else STATE_ACTIVE
        )
        duration = (
            self._compute_occupancy_timer()
            if has_occupancy_or_maintain
            else self._turn_off_timeout
        )
        if self._held:
            # The state switches now, while the usual hold policy suppresses
            # the newly calculated automatic turn-off until the hold is released.
            self.async_write_ha_state()
        elif duration is not None and duration <= 0:
            # An automatic turn-off is due. The now-active stage durations decide
            # whether this is immediate or enters an off warning sequence.
            self._begin_warning()
        else:
            if was_standby and not self._occupancy_lots():
                # A fresh timer, not one that latest_occupied_time accounts for.
                self._fresh_timer_started = datetime.now(UTC)
            self._start_timer(duration)
            self.async_write_ha_state()

    def _on_schedule_change(
        self,
        new_state: State,
        *,
        replay_since: datetime | None = None,
        first: bool = False,
    ) -> None:
        """Handle the virtual schedule sensor changing.

        replay_since is set when the schedule recovers to the value it had
        before an outage that began then: no boundary was crossed. Nor by a
        schedule's first state after a seed that could not read it (first),
        which is judged as that seed would have judged it.
        """
        replay = replay_since is not None
        if self._schedule_mode == SCHEDULE_MODE_FOLLOW:
            # Without a window of its own, the light was not following one.
            replay = replay or (first and self._schedule_window_applied is None)
            if new_state.state == "on":
                marker = new_state.attributes.get("current_window_start")
                if (
                    marker == self._schedule_window_applied
                    if marker
                    else replay and self._schedule_window_applied is None
                ):
                    # Same window we already applied: the schedule sensor
                    # was briefly unavailable and recovered mid-window. A manual
                    # off in between stands, mirroring the restart seed.
                    if self._attr_is_on:
                        if self._in_warning():
                            # A timer that ran while the schedule was
                            # unavailable reached the warning; undo it, as the
                            # recovered window owns the lights again.
                            self._restore_pre_warning()
                        self._machine_state = STATE_SCHEDULED
                        self._cancel_timer()
                        self.async_write_ha_state()
                    return
                self._apply_window_start(marker)
            elif replay:
                # Still outside the window: a manual on stands. A window that
                # ended under a hold and was turned off manually during the
                # outage is over, now that the schedule reads off again.
                if not self._attr_is_on:
                    self._forget_ended_follow_window()
                    self.async_write_ha_state()
            elif self._held and self._is_lit():
                # Auto-off held: keep the schedule window marker so releasing the
                # hold applies this off boundary.
                pass
            else:
                # Window ended: apply the off boundary.
                self._schedule_window_applied = None
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle(reason=REASON_SCHEDULE_END)
            return

        # All gate modes block automatic turn-ons outside the window and
        # re-check occupancy/a held-open door when it starts. At the end,
        # Gate and turn off forces off, gate_switch recalculates an on
        # light from the current latest_occupied_time, and gate_keep leaves its current
        # state/timer alone.
        if new_state.state != "on":
            if replay or first:
                return  # still outside the schedule window: nothing ended
            if self._schedule_mode == SCHEDULE_MODE_GATE and self._is_lit():
                if self._held:
                    # Applied when the hold releases.
                    self._schedule_end_off_pending = True
                    self.async_write_ha_state()
                else:
                    self.hass.async_create_task(self._auto_lights_off())
                    self._go_idle(reason=REASON_SCHEDULE_END)
            elif self._schedule_mode == SCHEDULE_MODE_GATE:
                # Ends an on-period that going bright cut short, too.
                self._clear_bright_forced_off()
                self.async_write_ha_state()
            elif self._schedule_mode == SCHEDULE_MODE_GATE_SWITCH and self._is_lit():
                self._switch_running_state()
        else:
            # A pending schedule end off no longer applies once a window starts again.
            self._schedule_end_off_pending = False
            if self._machine_state != STATE_IDLE:
                # Lights already on: the window start lifts the gate that
                # kept an already occupied sensor (or an open door) from holding
                # them.
                if self._occupancy_holds() or self._door_holds():
                    self._adopt_occupied()
                else:
                    self.async_write_ha_state()
                return
            if self._is_illuminance_bright():
                return  # bright-gated, like any automatic turn-on
            occ_state = (
                self.hass.states.get(self._occupancy_sensor)
                if self._occupancy_sensor
                else None
            )
            occ_active = (
                occ_state is not None
                and occ_state.state == "on"
                and self._molight_turns_on_for_occupancy()
            )
            door_holds = self._door_holds()
            if replay_since is not None:
                # Only a start that the unreadable gate blocked is applied now; a
                # light turned off manually in an occupied room stays off.
                replay_since = self._after_manual_off(replay_since)
                occ_active = (
                    occ_active and self._visit_started(occ_state) >= replay_since
                )
                door_holds = (
                    door_holds
                    and self._door_open_since is not None
                    and self._door_open_since >= replay_since
                )
            elif first:
                # As at the seed, presence that a manual off ended lights nothing.
                occ_active = occ_active and not self._may_replay_manual_off(
                    occ_state, observed=False
                )
                door_holds = door_holds and not self._may_replay_manual_off(
                    self.hass.states.get(self._door_sensor), observed=False
                )
            if occ_active or door_holds:
                now = datetime.now(UTC)
                if occ_active:
                    self._last_on_occupancy = now
                else:
                    self._last_on_door = now
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
                self._occupancy_lit_lights = occ_active
                self.hass.async_create_task(
                    self._auto_lights_on(
                        reason=REASON_OCCUPANCY if occ_active else REASON_DOOR
                    )
                )
                self.async_write_ha_state()

    def _match_recovered_member(self) -> bool:
        """Make a member back from unavailable match a follow schedule.

        A member back from unavailable (a reboot or a reload) reports whatever
        it booted into, which is not a person's change: the schedule decides
        on/off and its settings are re-sent. Returns False when there is no
        valid follow schedule to
        follow, or a hold would suppress the off. A light at standby
        is re-sent its standby settings the same way.
        """
        if self._machine_state == STATE_STANDBY:
            self._enter_standby(
                self._auto_on_transition, selection=True, force_selection=True
            )
            return True
        if self._schedule_mode != SCHEDULE_MODE_FOLLOW or not self._schedule_sensor:
            return False
        sched = self.hass.states.get(self._schedule_sensor)
        if sched is None or sched.state not in ("on", "off"):
            return False
        if sched.state == "off":
            if self._held and self._attr_is_on:
                return False
            if not self._all_lights_off():
                self.hass.async_create_task(self._auto_lights_off())
            if self._machine_state != STATE_IDLE:
                self._go_idle(reason=REASON_SCHEDULE_END)
            return True
        self._schedule_window_applied = sched.attributes.get("current_window_start")
        self._machine_state = STATE_SCHEDULED
        self._cancel_timer()
        self._forget_warning()
        # The member may already be on (booted lit): re-send the settings and
        # the turn-on selection regardless.
        self.hass.async_create_task(self._auto_lights_on(force_selection=True))
        self.async_write_ha_state()
        return True

    def _resend_on_settings(self) -> None:
        """Send the current brightness and color again, with the turn-on selection.

        Every member gets them, so one reporting in while the select call
        runs is lit too. A stage that blinks the lights off has none to send.
        """
        # The stage running decides, not the newly active settings.
        if self._in_warning() and self._stage_look and not self._stage_look[0]:
            return
        brightness, color = self._attr_brightness, self._current_color()
        looks = None
        if self._in_warning():
            # During a stage the stage is what is sent; should the warning
            # end while the select call runs, this is the look restored.
            brightness, color = self._pre_warn_brightness, self._pre_warn_color
            looks = self._own_looks()
        self.hass.async_create_task(
            self._set_lights(
                True,
                brightness=brightness,
                transition=self._auto_on_transition,
                color=color,
                apply_turn_on_selection=True,
                force_selection=True,
                looks=looks,
            )
        )

    def _forget_ended_follow_window(self) -> None:
        """Drop the marker of a schedule window that ended, once the lights are off.

        A hold keeps the marker past the window end to turn the lights off on
        release; with the lights off nothing is left to apply, and a later
        on-period must not be taken for that window.
        """
        if (
            self._schedule_window_applied is None
            or self._schedule_mode != SCHEDULE_MODE_FOLLOW
            or not self._schedule_sensor
        ):
            return
        state = self.hass.states.get(self._schedule_sensor)
        if state is not None and state.state == "off":
            self._schedule_window_applied = None

    def _apply_window_start(
        self, marker: str | None, *, reason: str = REASON_SCHEDULE_START
    ) -> None:
        """Enter the SCHEDULED state and turn the lights on (follow mode)."""
        self._schedule_window_applied = marker
        was_warning = self._in_warning()
        self._machine_state = STATE_SCHEDULED
        self._cancel_timer()
        if was_warning:
            # The schedule window adopts the light mid-warning: the virtual light is
            # logically on but the real lights are blinked off / dimmed by
            # the effect or warn stage; restore them for the window.
            self._restore_pre_warning()
        elif not self._attr_is_on:
            self.hass.async_create_task(self._auto_lights_on(reason=reason))
        self.async_write_ha_state()

    def _on_occupancy_change(self, occupied: bool, *, edge: bool = False) -> None:
        """Handle the virtual occupancy sensor changing.

        edge is a change the sensor reported, which the light's own system
        may turn the light on for; the others re-check presence.
        """
        if not occupied and not self._occupancy_clear_was_false():
            # A genuine visit ended: a later false detection did not light the light.
            self._occupancy_lit_lights = False
        if not occupied and self._occupancy_wait_generation is None:
            self._close_occupancy_wait()
        if self._machine_state == STATE_SCHEDULED:
            return  # follow-mode window owns the lights
        if occupied:
            if (
                self._occupancy_turn_on != OCCUPANCY_TURN_ON_MOLIGHT
                and self._machine_state == STATE_IDLE
                and not self._attr_is_on  # a light found on is adopted as usual
            ):
                if edge:
                    # Its own system turns the light on and skips MoLight's gates.
                    self._close_occupancy_wait()
                    self._occupancy_wait_unsub = async_call_later(
                        self.hass, OCCUPANCY_TURN_ON_WAIT, self._occupancy_wait_ended
                    )
                    return
                if not self._molight_turns_on_for_occupancy():
                    return
            if self._bright_gates_presence():
                # Bright enough: suppress lights; when illuminance turns off we
                # re-check occupancy from the sensor's current state.
                self._send_blocked(REASON_OCCUPANCY, REASON_BRIGHT)
                return
            if self._gate_schedule_inactive():
                # Outside the schedule window: occupancy may not turn lights
                # on; window start re-checks occupancy.
                self._send_blocked(REASON_OCCUPANCY, REASON_SCHEDULE)
                return
            was_warning = self._in_warning()
            was_standby = self._machine_state == STATE_STANDBY
            self._last_on_occupancy = datetime.now(UTC)
            self._machine_state = STATE_OCCUPIED
            self._cancel_timer()
            if was_warning:
                # Occupancy returned mid-warning: undo the warning so
                # the light looks exactly as it did while the timer was running.
                self._restore_pre_warning()
            elif not self._attr_is_on or was_standby:
                # Lit from standby counts as lit by occupancy: a false
                # detection drops back to standby after the false-detection
                # off delay.
                self._occupancy_lit_lights = True
                self.hass.async_create_task(
                    self._auto_lights_on(reason=REASON_OCCUPANCY)
                )
            self.async_write_ha_state()
        elif self._machine_state == STATE_OCCUPIED:
            if self._maintain_active() or self._door_holds():
                return  # maintain occupancy sensor / open door holds the light on
            if self._occupancy_lit_lights and self._occupancy_clear_was_false():
                # The whole visit was a false detection and nobody else
                # asked for these lights, so turn them off after the
                # false-detection off delay.
                self._machine_state = STATE_COUNTDOWN
                self._start_timer(
                    self._false_off_timer(), reason=REASON_FALSE_DETECTION
                )
            else:
                self._machine_state = self._countdown_state()
                self._start_timer(self._compute_clear_timer())
            self.async_write_ha_state()

    def _on_own_system_turn_on(
        self, brightness: int | None, color: tuple[ColorMode, tuple] | None
    ) -> None:
        """Take the light's own turn-on inside the wait as an occupancy turn-on.

        The auto-on brightness, color and selection follow at the wait's end,
        once every real light it turns on has reported.
        """
        selection = self._selection_to_apply()
        self._send_event(
            EVENT_TURNED_ON,
            reason=REASON_OCCUPANCY,
            standby=False,
            brightness=(
                brightness
                if self._auto_on_brightness is None
                else self._auto_on_brightness
            ),
            color=_event_color(self._auto_on_color) or _color_data(color),
            selection=selection,
        )
        self._occupancy_wait_generation = self._command_generation
        self._last_on_occupancy = datetime.now(UTC)
        self._machine_state = STATE_OCCUPIED
        self._cancel_timer()
        self._occupancy_lit_lights = True
        self._go_on(rejoin=False)

    def _molight_turns_on_for_occupancy(self) -> bool:
        """Return False when only the light's own system turns it on for occupancy."""
        return self._occupancy_turn_on != OCCUPANCY_TURN_ON_OWN_SYSTEM

    def _own_system_turn_on_stands(self) -> bool:
        """Return True while nothing has changed the light since its own turn-on."""
        return self._occupancy_wait_generation == self._command_generation and (
            self._machine_state in (STATE_OCCUPIED, STATE_COUNTDOWN)
        )

    @callback
    def _occupancy_wait_ended(self, _now: datetime) -> None:
        self._occupancy_wait_unsub = None
        if self._occupancy_wait_generation is None:
            if (
                self._molight_turns_on_for_occupancy()
                and self._machine_state == STATE_IDLE
            ):
                # Its own system did not turn the light on: MoLight does, gated.
                self._on_occupancy_change(True)
            return
        stands = self._own_system_turn_on_stands()
        self._occupancy_wait_generation = None
        if stands and (
            self._auto_on_brightness is not None
            or self._auto_on_color
            or self._turn_on_select_entity
        ):
            self.hass.async_create_task(self._auto_lights_on(force_selection=True))

    def _close_occupancy_wait(self) -> None:
        if self._occupancy_wait_unsub is not None:
            self._occupancy_wait_unsub()
            self._occupancy_wait_unsub = None
        self._occupancy_wait_generation = None

    def _on_maintain_change(self, maintained: bool) -> None:
        """Handle the maintain occupancy sensor changing.

        Holds an already-on light on while occupied; never turns lights on.
        """
        if not maintained and not self._clear_was_false(
            self._maintain_occupancy_sensor
        ):
            self._occupancy_lit_lights = False  # genuine detection since it lit
        if self._machine_state == STATE_SCHEDULED:
            return  # follow-mode window owns the lights
        if maintained:
            # Not a turn-on, so no illuminance/schedule gating: an on light
            # is simply adopted; an off light stays off.
            if self._machine_state in (
                STATE_ACTIVE,
                STATE_COUNTDOWN,
                STATE_EFFECT,
                STATE_WARN,
            ):
                if self._in_warning():
                    self._restore_pre_warning()
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
                self.async_write_ha_state()
        else:
            if self._machine_state != STATE_OCCUPIED:
                return
            if self._occupancy_holds() or self._door_holds():
                return  # occupancy sensor / open door still holds the light on
            if (
                self._occupancy_lit_lights
                and self._occupancy_clear_was_false()
                and self._clear_was_false(self._maintain_occupancy_sensor)
            ):
                # Both sensors flagged their clears false: the whole episode
                # was a false detection; a genuine detection on either side
                # earns the turn-off timer instead.
                self._machine_state = STATE_COUNTDOWN
                self._start_timer(
                    self._false_off_timer(), reason=REASON_FALSE_DETECTION
                )
            else:
                self._machine_state = self._countdown_state()
                self._start_timer(self._compute_clear_timer())
            self.async_write_ha_state()

    def _occupancy_active(self) -> bool:
        """Return True when the light's occupancy sensor is occupied.

        Unreadable (an outage, a reload), it keeps a hold it had: its last
        known value counts for a light that is held, and starts nothing.
        """
        live = self._live_occupancy_on()
        if live is None:
            return bool(self._occupancy_last_on) and self._presence_held()
        return live

    def _occupancy_holds(self) -> bool:
        """Return True when being occupied may hold an on light as OCCUPIED.

        Adoption is gated like a turn-on (bright or outside a gate-mode
        window suppress it, though bright in illuminance Gate only mode only for a
        light off or at standby), unlike the maintain occupancy sensor, which is never
        gated. Without adoption, a light turned on while already occupied
        would run a timer that finishes despite presence, and the steady-on
        sensor produces no event that could ever rescue it.
        """
        return (
            self._occupancy_active()
            and not self._bright_gates_presence()
            and not self._gate_schedule_inactive()
        )

    def _may_replay_manual_off(self, state: State | None, *, observed: bool) -> bool:
        """Return True when presence may be the visit a manual off ended.

        Only while that off stands: a visit that the sensor dates after it may
        light the light, and one that it cannot date only when seen to start live.
        A sensor with no state, such as a door awaiting its reload, dates nothing.
        """
        if self._is_lit() or not self._manual_off_stands():
            return False
        if state is None or "last_on_time" not in state.attributes:
            return not observed
        raw = state.attributes["last_on_time"]
        try:
            started = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return True
        return self._last_off_manual is None or started <= self._last_off_manual

    def _visit_started(self, state: State) -> datetime:
        """When the visit that the occupancy sensor reports began.

        Its own timestamp if it has one, else when this light first saw it on:
        a sensor that reloads mid-visit comes back with a new last_changed.
        """
        try:
            return datetime.fromisoformat(state.attributes.get("last_on_time"))
        except (TypeError, ValueError):
            return self._occupancy_on_since or state.last_changed

    def _manual_off_stands(self) -> bool:
        """Return True when a manual off is the last thing the light did.

        Standby scopes it to the window; otherwise a recorded turn-on or a
        schedule boundary since the off ends it.
        """
        if self._standby_brightness is not None:
            return self._standby_suppressed
        if self._last_off_manual is None or self._manual_off_cleared:
            return False
        return all(
            on is None or on <= self._last_off_manual
            for on in (
                self._last_on_physical,
                self._last_on_virtual,
                self._last_on_occupancy,
                self._last_on_illuminance,
                self._last_on_door,
            )
        )

    def _after_manual_off(self, since: datetime) -> datetime:
        """Push a deferred start's schedule window past the person's last turn-off.

        A start that a gate or an unreadable sensor blocked is only applied
        on recovery when the person has not turned the light off since.
        """
        if self._last_off_manual is not None and self._last_off_manual > since:
            return self._last_off_manual
        return since

    def _gate_lifted_since(self, since: datetime) -> bool:
        """Return True when a turn-on gate lifted at or after `since`.

        Illuminance going dark or losing its reading, a gate-mode window
        starting, or a Virtual Scheduled Light switching settings each re-check
        occupancy live, and an unreadable sensor counted as clear then.
        """
        checks = [(self._illuminance_sensor, ("off", STATE_UNAVAILABLE))]
        if self._schedule_mode in (
            SCHEDULE_MODE_GATE,
            SCHEDULE_MODE_GATE_SWITCH,
            SCHEDULE_MODE_GATE_KEEP,
        ):
            checks.append((self._schedule_sensor, ("on",)))
        if self._is_scheduled_light:
            checks.append((self._schedule_sensor, ("on", "off")))
        for entity_id, lifted_states in checks:
            state = self.hass.states.get(entity_id) if entity_id else None
            if (
                state is not None
                and state.state in lifted_states
                and state.last_changed >= since
                and not state.attributes.get(ATTR_RESTORED)
            ):
                return True
        return False

    def _adopt_occupied(self) -> None:
        """Move an on light to OCCUPIED when a gate lifts mid-on-period.

        Used when illuminance goes dark or a gate-mode window starts while
        the lights are already on (ACTIVE/COUNTDOWN/EFFECT/WARN) with a
        presence hold. Not a turn-on: attribution and the occupancy-lit
        flag are left untouched, so the person still owns a manual on-period.
        A light at standby is raised to the auto-on brightness, which
        occupancy then owns like any raise from standby.
        """
        was_warning = self._in_warning()
        was_standby = self._machine_state == STATE_STANDBY
        self._machine_state = STATE_OCCUPIED
        self._cancel_timer()
        if was_warning:
            self._restore_pre_warning()
        elif was_standby:
            self._occupancy_lit_lights = self._occupancy_active()
            self.hass.async_create_task(
                self._auto_lights_on(
                    reason=REASON_OCCUPANCY if self._occupancy_holds() else REASON_DOOR
                )
            )
        self.async_write_ha_state()

    def _maintain_active(self) -> bool:
        """Return True when the maintain occupancy sensor is on, or holds unreadable."""
        live = self._live_maintain_on()
        if live is None:
            return bool(self._maintain_last_on) and self._presence_held()
        return live

    def _presence_held(self) -> bool:
        """Return True while presence holds the light on with no timer."""
        return self._machine_state == STATE_OCCUPIED

    def _live_occupancy_on(self) -> bool | None:
        """Live on/off of the occupancy sensor, None when unknown."""
        if not self._occupancy_sensor:
            return None
        state = self.hass.states.get(self._occupancy_sensor)
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def _live_maintain_on(self) -> bool | None:
        """Live on/off of the maintain occupancy sensor, None when unknown."""
        if not self._maintain_occupancy_sensor:
            return None
        state = self.hass.states.get(self._maintain_occupancy_sensor)
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def _live_schedule_on(self) -> bool | None:
        """Live on/off of the schedule sensor, None when unknown."""
        if not self._schedule_sensor:
            return None
        state = self.hass.states.get(self._schedule_sensor)
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def _live_illuminance_bright(self) -> bool | None:
        """Live bright/dark of the illuminance sensor, None when unknown."""
        if not self._illuminance_sensor:
            return None
        state = self.hass.states.get(self._illuminance_sensor)
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def _door_holds(self) -> bool:
        """Return True when an Open & close door is open (holds the light).

        Such a door holds an already-on light on with no timer, exactly like
        an occupied occupancy sensor or the maintain occupancy sensor, and its closing
        starts the turn-off timer. In On open mode a door never holds (it is only a
        momentary turn-on trigger), so this is always False there. Nor does
        it hold outside a Gate and turn off window, where the door is ignored. Reads
        the door sensor's last known value, so a sensor that is briefly
        unavailable keeps holding, until it has been unavailable for
        DOOR_UNAVAILABLE_TIMEOUT.
        """
        if not self._door_sensor or self._door_mode != DOOR_MODE_OPEN_CLOSE:
            return False
        return self._door_open and not self._gate_and_turn_off_schedule_inactive()

    def _start_door_unavailable_timer(self) -> None:
        """Count an open door whose sensor stays unavailable as closed.

        A dead battery sensor must not hold the light on forever; one briefly
        unavailable keeps the hold, as on an occupancy sensor.
        """
        if self._door_unavailable_unsub is None:
            self._door_unavailable_unsub = async_call_later(
                self.hass, DOOR_UNAVAILABLE_TIMEOUT, self._door_unavailable_finished
            )

    @callback
    def _door_unavailable_finished(self, _now: datetime) -> None:
        self._door_unavailable_unsub = None
        # As at startup: closed, and its next report is a first sighting.
        self._door_open = False
        self._door_open_since = None
        self._door_seen = False
        self._on_door_change(False)

    def _cancel_door_unavailable_timer(self) -> None:
        if self._door_unavailable_unsub is not None:
            self._door_unavailable_unsub()
            self._door_unavailable_unsub = None

    def _on_door_change(self, is_open: bool) -> None:
        """Handle the configured door sensor changing (on = open).

        Opening is a turn-on trigger, gated by illuminance/a gate-mode schedule
        exactly like occupancy. In Open & close mode the open door then holds the
        lights on (OCCUPIED, no timer) until it closes, whereupon the timer
        starts unless occupancy/an auto-off hold still applies; in On open mode the
        door is a momentary trigger (ACTIVE, turn-off timer) and closing is
        ignored.
        """
        if self._machine_state == STATE_SCHEDULED:
            return  # follow-mode window owns the lights
        if is_open:
            if self._bright_gates_presence():
                self._send_blocked(REASON_DOOR, REASON_BRIGHT)
                return  # bright-gated, like occupancy
            if self._gate_schedule_inactive():
                self._send_blocked(REASON_DOOR, REASON_SCHEDULE)
                return  # outside a gate-mode schedule window
            was_warning = self._in_warning()
            was_standby = self._machine_state == STATE_STANDBY
            self._last_on_door = datetime.now(UTC)
            # A person opened it, so a false-detection clear no longer cuts the light
            # short.
            self._occupancy_lit_lights = False
            # An Open & close door holds the light (no timer) like the maintain
            # occupancy sensor; an already-occupied/maintained room holds it too. Only
            # an opening in On open mode with no other hold runs the timer.
            # (Occupancy needs no bright/window re-check here; the gates
            # above already returned.)
            holds = (
                self._door_holds()
                or self._maintain_active()
                or self._occupancy_active()
            )
            self._machine_state = STATE_OCCUPIED if holds else STATE_ACTIVE
            self._cancel_timer()
            if was_warning:
                # Opening mid-warning is a re-trigger: undo the warning so the
                # light looks as it did before the warning.
                self._restore_pre_warning()
            elif not self._attr_is_on or was_standby:
                self.hass.async_create_task(self._auto_lights_on(reason=REASON_DOOR))
            if not holds:
                self._start_timer()
            self.async_write_ha_state()
        else:
            if self._door_mode != DOOR_MODE_OPEN_CLOSE:
                return  # On open: closing is ignored
            if self._gate_and_turn_off_schedule_inactive():
                return  # outside a Gate and turn off window the door is ignored
            if self._machine_state in (STATE_IDLE, STATE_STANDBY):
                return
            if self._occupancy_holds() or self._maintain_active():
                return  # presence still holds the lights on
            if self._in_warning():
                return  # already winding down toward off; let it finish
            # The door was the reason to be on and it just closed: start the
            # turn-off timer.
            self._machine_state = self._countdown_state()
            self._start_timer()
            self.async_write_ha_state()

    def _occupancy_clear_was_false(self) -> bool:
        """Return True when the occupancy sensor flagged a false detection."""
        return self._clear_was_false(self._occupancy_sensor)

    def _clear_was_false(self, entity_id: str | None) -> bool:
        """Return True when the given sensor flagged a false-detection clear."""
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return bool(
            state is not None and state.attributes.get("last_clear_false_detection")
        )

    def _on_illuminance_change(self, is_bright: bool, *, first: bool = False) -> None:
        """Handle the virtual illuminance sensor changing.

        is_bright=True  (illuminance ON  = bright): natural light is sufficient
                        → turn off artificial lights if they were on.
        is_bright=False (illuminance OFF = dark):   need artificial light
                        → turn on if occupied, or resume an
                          on-period that going bright cut short.
        With first, the seed could not read the sensor and counted it as
        dark, so presence that a manual off ended lights nothing now either.
        """
        if self._machine_state == STATE_SCHEDULED:
            return  # follow-mode window owns the lights
        if is_bright:
            if self._illuminance_mode == ILLUMINANCE_MODE_GATE_ONLY:
                # Gate only: bright never forces the lights off (breaks the
                # feedback loop when the lux sensor sees the controlled
                # lights). Occupancy/timer handle turning off.
                return
            if self._held:
                # Auto-off held: releasing the hold re-checks brightness.
                return
            if self._machine_state != STATE_IDLE:
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle(bright_forced=True, reason=REASON_BRIGHT)
        else:
            if self._machine_state != STATE_IDLE:
                # Lights already on: going dark lifts the gate that kept
                # an already occupied sensor (or an open door) from holding
                # them; adopt so a timer can't finish despite presence.
                if self._occupancy_holds() or self._door_holds():
                    self._adopt_occupied()
                return
            # Outside a gate-mode window presence lights no off room, but an
            # on-period that going bright cut short still resumes: it already
            # exists.
            schedule_gated = self._gate_schedule_inactive()

            # Check whether we should turn on due to occupancy, a held-open
            # door, or time left of an on-period that going bright cut short.
            occ_state = (
                self.hass.states.get(self._occupancy_sensor)
                if self._occupancy_sensor
                else None
            )
            occ_active = occ_state is not None and occ_state.state == "on"
            door_holds = self._door_holds()
            if first:
                occ_active = occ_active and not self._may_replay_manual_off(
                    occ_state, observed=False
                )
                door_holds = door_holds and not self._may_replay_manual_off(
                    self.hass.states.get(self._door_sensor), observed=False
                )
            occ_turns_on = occ_active and self._molight_turns_on_for_occupancy()
            if (occ_turns_on or door_holds) and not schedule_gated:
                self._last_on_illuminance = datetime.now(UTC)
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
                self._occupancy_lit_lights = occ_active
                self.hass.async_create_task(
                    self._auto_lights_on(reason=REASON_ILLUMINANCE)
                )
                self.async_write_ha_state()
            elif self._bright_forced_off and (
                (timer_seconds := self._compute_illuminance_timer()) is None
                or timer_seconds > 0
            ):
                # Resumed ahead of standby: its timer finishes there anyway.
                self._last_on_illuminance = datetime.now(UTC)
                self.hass.async_create_task(self._auto_lights_on(reason=REASON_RESUME))
                if self._maintain_active() or (
                    (occ_active or door_holds)
                    and not self._gate_and_turn_off_schedule_inactive()
                ):
                    # Time left of the on-period justified the turn-on; the maintain
                    # occupancy sensor, or presence that a gate blocking only turn-ons
                    # let through once the light is on, now holds the resumed light.
                    self._machine_state = STATE_OCCUPIED
                    self._cancel_timer()
                else:
                    self._machine_state = STATE_COUNTDOWN
                    if timer_seconds is None:
                        self._machine_state = self._countdown_state()
                    self._start_timer(timer_seconds)
                self.async_write_ha_state()
            elif not schedule_gated and self._can_be_at_standby():
                # Control mode kept standby off while bright.
                self._last_on_illuminance = datetime.now(UTC)
                self._enter_standby(
                    self._auto_on_transition, selection=True, reason=REASON_ILLUMINANCE
                )
            elif occ_turns_on or door_holds:
                self._send_blocked(
                    REASON_OCCUPANCY if occ_turns_on else REASON_DOOR, REASON_SCHEDULE
                )

    def _countdown_state(self) -> str:
        """COUNTDOWN when a turn-off timer can run; a blank timeout runs none."""
        return STATE_ACTIVE if self._turn_off_timeout is None else STATE_COUNTDOWN

    def _false_off_timer(self) -> int:
        """Return the false-detection off delay, capped at the turn-off timeout."""
        if self._turn_off_timeout is None:
            return self._false_off_delay
        return min(self._false_off_delay, self._turn_off_timeout)

    def _compute_occupancy_timer(self) -> int | None:
        """Seconds to wait after occupancy clears before turning lights off.

        Anchors to the latest_occupied_time across the occupancy and maintain
        occupancy sensors (whichever saw the person last) so that each sensor's
        occupancy timeout is respected: the lights go off a turn-off timeout
        after the person actually left, i.e. at latest_occupied_time plus the
        turn-off timeout, which is why the turn-off timeout must be at least
        the sensor's occupancy timeout (the flows enforce it). None
        with a blank timeout: nothing turns the lights off.
        """
        base = self._turn_off_timeout
        if base is None:
            return None
        if lots := self._occupancy_lots():
            now = datetime.now(UTC)
            remaining = (max(lots) - now).total_seconds()
            return max(0, int(base + remaining))
        return base

    def _compute_clear_timer(self) -> int | None:
        """Seconds to wait once the last presence hold clears.

        A false detection leaves latest_occupied_time at an earlier visit,
        which must not cut short a light the person turned on since then: the
        timer never finishes before a full timeout after the latest manual or
        door turn-on, a physical dim or recolor (which also
        restarts a running timer), or any fresh timer started since,
        as by a hold releasing, the door closing or a schedule end.
        """
        timer_seconds = self._compute_occupancy_timer()
        if timer_seconds is None:
            return None
        user_ons = [
            t
            for t in (
                self._last_on_physical,
                self._last_on_virtual,
                self._last_on_door,
                self._last_brightness_change_physical,
                self._last_color_change_physical,
                self._fresh_timer_started,
            )
            if t is not None
        ]
        if user_ons:
            elapsed = (datetime.now(UTC) - max(user_ons)).total_seconds()
            timer_seconds = max(timer_seconds, int(self._turn_off_timeout - elapsed))
        return timer_seconds

    def _occupancy_lots(self) -> list[datetime]:
        """latest_occupied_time across the occupancy and maintain occupancy sensors."""
        lots: list[datetime] = []
        for entity_id in (self._occupancy_sensor, self._maintain_occupancy_sensor):
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            lot_str = state.attributes.get("latest_occupied_time")
            if lot_str:
                with contextlib.suppress(ValueError, TypeError):
                    lots.append(datetime.fromisoformat(lot_str))
        return lots

    def _compute_illuminance_timer(self) -> int | None:
        """Seconds left of the on-period that going bright cut short.

        It lasts until the later of the end recorded when going bright forced
        the light off and a timeout after the room was last occupied; neither
        means there is nothing to resume. None when that on-period has no
        end: it ran under a blank timeout.
        """
        lots = self._occupancy_lots()
        if lots and self._turn_off_timeout is None:
            return None
        ends = [lot + timedelta(seconds=self._turn_off_timeout) for lot in lots]
        if self._bright_resume_until is not None:
            ends.append(self._bright_resume_until)
        if not ends:
            return 0
        if max(ends) == _NO_END:
            return None
        return max(0, int((max(ends) - datetime.now(UTC)).total_seconds()))

    def _go_on(self, *, rejoin: bool = True) -> None:
        """Move to ACTIVE (or stay OCCUPIED/SCHEDULED) when lights come on.

        Without rejoin, a manual off's standby suppression outlasts the turn-on:
        nobody made it (a select call's preset lit the real lights).
        """
        # A manual turn-on cancels any off warning sequence; the caller
        # has already set the real lights, so just drop the pre-warning look.
        self._cancel_warning(self._attr_brightness, self._current_color())
        self._clear_bright_forced_off()
        if rejoin:
            # Turning the light back on after a manual off rejoins standby.
            self._standby_suppressed = False
        self._close_owed_ends()
        # Set before the hold checks: gate modes that only block an off light
        # turning on let occupancy hold this turn-on outside the schedule window.
        self._attr_is_on = True
        if self._machine_state in (STATE_OCCUPIED, STATE_SCHEDULED):
            # Already managed by occupancy / schedule window; report the
            # rejoined standby, which a restart would otherwise lose.
            self.async_write_ha_state()
            return

        # Turned back on during an active follow-mode window (after a manual
        # off): rejoin the window instead of running the turn-off timer, so
        # the light stays on until the window ends.
        sched = self._follow_schedule_state()
        if sched is not None:
            self._apply_window_start(sched.attributes.get("current_window_start"))
            return

        if self._machine_state == STATE_STANDBY:
            self._machine_state = STATE_ACTIVE  # raised manually, so on above standby
        # Turned on while the maintain occupancy sensor, an Open & close door, or the
        # occupancy sensor (when not gated by bright/window) is
        # already holding presence: hold the light immediately instead of
        # running a timer that would finish despite presence.
        if self._maintain_active() or self._occupancy_holds() or self._door_holds():
            self._machine_state = STATE_OCCUPIED
            self._cancel_timer()
            self.async_write_ha_state()
            return

        self._machine_state = STATE_ACTIVE
        # Restart even when already ACTIVE: turning on / dimming again is
        # activity and extends the on-period.
        self._start_timer()
        self.async_write_ha_state()

    def _finish_schedule_end_off(self) -> None:
        """Apply a pending schedule end off now.

        A Virtual Scheduled Light fades off with the settings being left, a Gate and
        turn off light with its own transition; going idle clears the pending flag.
        """
        self.hass.async_create_task(
            self._scheduled_end_lights_off()
            if self._is_scheduled_light
            else self._auto_lights_off()
        )
        self._go_idle(reason=REASON_SCHEDULE_END)

    def _go_idle(
        self,
        *,
        bright_forced: bool = False,
        manual: bool = False,
        reason: str | None = None,
    ) -> None:
        """Cancel any timer and move to IDLE; reason says why the on-period ended."""
        ended = self._machine_state != STATE_IDLE
        resume_until = None
        if bright_forced and not self._in_warning():
            # A timer resumes until it would have finished; whoever held the
            # light was there until now, so it lasts a timeout at least. An
            # on-period with no timer to end it resumes whenever it is dark.
            resume_until = self._timer_finishes_at
            if resume_until is None and self._turn_off_timeout is None:
                if self._machine_state in (
                    STATE_ACTIVE,
                    STATE_COUNTDOWN,
                    STATE_OCCUPIED,
                ):
                    resume_until = _NO_END
            elif resume_until is None and self._machine_state == STATE_OCCUPIED:
                resume_until = datetime.now(UTC) + timedelta(
                    seconds=self._turn_off_timeout
                )
        self._cancel_timer()
        self._command_generation += 1
        self._resend_after_select = False
        if manual:
            self._last_off_manual = datetime.now(UTC)
            self._manual_off_cleared = False
            # Standby stays off until the next schedule boundary.
            self._standby_suppressed = self._standby_brightness is not None
        if self._machine_state != STATE_IDLE:
            # Why the on-period ended; an off while idle ends none.
            self._bright_forced_off = bright_forced
            self._bright_resume_until = resume_until
        self._machine_state = STATE_IDLE
        self._attr_is_on = False
        self._fresh_timer_started = None
        self._occupancy_lit_lights = False
        # A pending schedule end off only applies to the on-period it
        # interrupted; any off consumes it.
        self._schedule_end_off_pending = False
        self._forget_warning()
        self._forget_ended_follow_window()
        if ended and reason is not None:
            self._send_event(EVENT_TURNED_OFF, reason=reason, standby=False)
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Timer helpers
    # ------------------------------------------------------------------

    def _start_timer(
        self, duration: int | None = None, *, reason: str = REASON_TIMER
    ) -> None:
        self._cancel_timer()
        self._timer_reason = reason
        if self._held:
            # Auto-off held: the state changes as usual but no
            # timer is armed; releasing the hold starts a fresh one.
            return
        if duration is None:
            if self._turn_off_timeout is None:
                return  # a blank timeout: the lights stay on
            duration = self._turn_off_timeout
            self._fresh_timer_started = datetime.now(UTC)
        self._timer_finishes_at = datetime.now(UTC) + timedelta(seconds=duration)
        self._timer_unsub = async_call_later(self.hass, duration, self._timer_finished)

    async def _timer_finished(self, _now: datetime) -> None:
        self._timer_unsub = None
        self._timer_finishes_at = None
        if self._held:
            return  # engaged in the same loop iteration the timer finished
        state = self._machine_state
        if state in (STATE_ACTIVE, STATE_COUNTDOWN):
            # Automatic turn-off: run the off warning sequence, or turn straight off.
            self._begin_warning()
        elif state == STATE_EFFECT:
            self._enter_warn()
        elif state == STATE_WARN:
            self._finish_auto_off()
        # any other state: the machine moved on in the same loop iteration the
        # timer finished, so there is nothing to do.

    def _cancel_timer(self) -> None:
        if self._timer_unsub is not None:
            self._timer_unsub()
            self._timer_unsub = None
        self._timer_finishes_at = None
        self._timer_reason = REASON_TIMER

    def _clear_bright_forced_off(self) -> None:
        """Forget an on-period that going bright cut short: it is over."""
        self._bright_forced_off = False
        self._bright_resume_until = None

    # ------------------------------------------------------------------
    # Off warning sequence
    # ------------------------------------------------------------------

    def _in_warning(self) -> bool:
        """Return True while showing the effect or warn stage before turning off."""
        return self._machine_state in (STATE_EFFECT, STATE_WARN)

    def _forget_warning(self) -> None:
        """Clear warning_active and drop the pre-warning look."""
        self._warning_active = False
        self._pre_warn_brightness = None
        self._pre_warn_color = None
        self._pre_warn_members = {}

    def _cancel_warning(self, brightness: int | None, color: dict | None) -> None:
        """Cancel the warning, with the lights staying on at this look."""
        if self._warning_active:
            self._send_event(
                EVENT_OFF_WARNING,
                stage=STAGE_CANCELLED,
                off_in=None,
                standby=self._can_be_at_standby(),
                brightness=brightness,
                color=color,
            )
        self._forget_warning()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _send_event(self, event_type: str, **attributes: Any) -> None:
        """Send what the light did to its Events entity."""
        if event_type in (EVENT_TURNED_ON, EVENT_TURNED_OFF):
            self._restored_on = False  # consumers know what the light did since
        send_event(self.hass, self._entry_id, event_type, **attributes)

    def _send_blocked(self, trigger: str, reason: str) -> None:
        """Send a trigger that a gate blocked, when it would have lit the room."""
        if not self._is_lit():
            self._send_event(EVENT_TURN_ON_BLOCKED, trigger=trigger, reason=reason)

    def _selection_to_apply(self) -> str | None:
        """Return the selection option a turn-on is about to apply."""
        if not self._turn_on_select_entity or (
            self._attr_is_on and not self._all_lights_off()
        ):
            return None
        select_state = self.hass.states.get(self._turn_on_select_entity)
        if select_state is None or select_state.state == STATE_UNAVAILABLE:
            return None
        return self._resolve_turn_on_selection()[0]

    def _member_looks(self) -> dict[str, _Look | None]:
        """Each readable real light's own look, None for one that is off.

        One still answering a command, or that never answered our last
        on or off, is left out: what it reports is not what it shows. So is
        one off while a turn-on waits for its selection to light it.
        """
        looks: dict[str, _Look | None] = {}
        waiting = bool(self._waiting_turn_ons)
        for entity_id in self._lights:
            lit = self._member_is_lit(entity_id)
            if (
                lit is None
                or self._awaited_power(entity_id) is not None
                or self._commanded_power.get(entity_id, lit) != lit
                or (waiting and not lit)
            ):
                continue
            state = self.hass.states.get(entity_id)
            looks[entity_id] = (
                (
                    state.attributes.get(ATTR_BRIGHTNESS),
                    _color_data(_state_color(state)),
                )
                if lit
                else None
            )
        return looks

    def _own_looks(
        self,
        brightness: int | None = None,
        color: dict | None = None,
        *,
        own_color: bool = True,
        lit_only: bool = True,
    ) -> dict[str, _Look | None]:
        """Each member's pre-warning look, for _set_lights.

        A given brightness or color replaces each member's own (a color only
        with own_color). A member that was off is left out, or without
        lit_only sent just the given values.
        """
        looks: dict[str, _Look | None] = {}
        for entity_id, own in self._pre_warn_members.items():
            if own is None:
                looks[entity_id] = None if lit_only else (brightness, color)
            else:
                looks[entity_id] = (
                    own[0] if brightness is None else brightness,
                    color if color is not None or not own_color else own[1],
                )
        return looks

    def _begin_warning(self) -> None:
        """Automatic turn-off is due: start the off warning sequence.

        Saves the brightness and color as the pre-warning look (restored if a
        person re-triggers or reused by a warn stage with no brightness/color
        of its own). Falls through to the warn stage (and to a plain off) when the
        earlier stage is disabled, so both durations at 0 behaves exactly like
        an immediate off.
        """
        self._warning_active = True
        self._pre_warn_brightness = self._attr_brightness
        self._pre_warn_color = self._current_color()
        self._pre_warn_members = self._member_looks()
        # Recorded rather than re-derived in _enter_warn: switching settings
        # mid-effect can swap _effect_color out from under the running stage.
        self._effect_sent_color = (
            self._effect_duration > 0 and self._effect_color is not None
        )
        reason = self._timer_reason
        if self._effect_duration > 0:
            self._machine_state = STATE_EFFECT
            self._send_event(
                EVENT_OFF_WARNING,
                stage=STAGE_EFFECT,
                off_in=self._effect_duration + self._warn_duration,
                standby=self._can_be_at_standby(),
                brightness=self._effect_brightness,
                color=_event_color(self._effect_color),
            )
            self._show_stage(
                self._effect_brightness,
                self._effect_transition,
                self._effect_color,
                self._own_looks(
                    self._effect_brightness, self._effect_color, own_color=False
                ),
            )
            self._start_timer(self._effect_duration, reason=reason)
            self.async_write_ha_state()
            return
        self._enter_warn()

    def _enter_warn(self) -> None:
        """Advance to the warn stage, or turn off when it is disabled."""
        if self._warn_duration > 0:
            # A warn stage without a color of its own undoes an effect-stage
            # recolor, mirroring how its brightness falls back to the
            # pre-warning brightness.
            effect_sent_color = (
                self._machine_state == STATE_EFFECT and self._effect_sent_color
            )
            self._machine_state = STATE_WARN
            brightness = (
                self._warn_brightness
                if self._warn_brightness is not None
                else self._pre_warn_brightness
            ) or 255
            color = self._warn_color
            if color is None and effect_sent_color:
                color = self._pre_warn_color
            # Blank keeps each member's own brightness, and its color unless
            # the effect stage changed it.
            looks = {
                entity_id: look and (look[0] or 255, look[1])
                for entity_id, look in self._own_looks(
                    self._warn_brightness,
                    self._warn_color,
                    own_color=effect_sent_color,
                ).items()
            }
            self._send_event(
                EVENT_OFF_WARNING,
                stage=STAGE_WARN,
                off_in=self._warn_duration,
                standby=self._can_be_at_standby(),
                brightness=brightness,
                color=_event_color(color),
            )
            self._show_stage(brightness, self._warn_transition, color, looks)
            self._start_timer(self._warn_duration, reason=self._timer_reason)
            self.async_write_ha_state()
            return
        self._finish_auto_off()

    def _finish_auto_off(self) -> None:
        """End an on-period whose timer finished: go to standby, or turn off."""
        if self._can_be_at_standby():
            self._enter_standby(self._auto_off_transition, reason=self._timer_reason)
            return
        # Task + synchronous _go_idle (the idiom used everywhere else):
        # awaiting the service call first would let a re-trigger landing
        # mid-await be stomped back to IDLE when the await returns.
        self.hass.async_create_task(self._auto_lights_off())
        self._go_idle(reason=self._timer_reason)

    def _standby_applies(self) -> bool:
        """Return True when the active settings end at standby, not off."""
        return self._standby_brightness is not None and not self._standby_suppressed

    def _can_be_at_standby(self) -> bool:
        """Return True when an off light should come on at standby now.

        Bright in illuminance control mode makes standby wait for darkness;
        illuminance Gate only mode gates only turn-ons above standby.
        """
        return self._standby_applies() and not (
            self._illuminance_mode == ILLUMINANCE_MODE_CONTROL
            and self._is_illuminance_bright()
        )

    def _enter_standby(
        self,
        transition: float | None = None,
        *,
        selection: bool = False,
        force_selection: bool = False,
        reason: str | None = None,
    ) -> None:
        """Go to the standby brightness: lights on, no timer.

        Callers pass the transition: auto-on when coming on from off (with the
        turn-on selection), auto-off when dropping from a higher brightness.
        Without a standby color, an effect or warn stage's recolor is undone. reason
        says why, and is None for a re-send of the brightness the light is at.
        """
        if reason is not None and self._attr_is_on:
            self._send_event(EVENT_TURNED_OFF, reason=reason, standby=True)
        elif reason is not None:
            self._send_event(
                EVENT_TURNED_ON,
                reason=reason,
                standby=True,
                brightness=self._standby_brightness,
                color=_event_color(self._standby_color),
                selection=self._selection_to_apply() if selection else None,
            )
        color = self._standby_color
        looks = None
        if (
            color is None
            and self._in_warning()
            and self._pre_warn_color != self._current_color()
        ):
            color = self._pre_warn_color
            looks = self._own_looks(self._standby_brightness, lit_only=False)
        self._cancel_timer()
        self._machine_state = STATE_STANDBY
        self._forget_warning()
        self._occupancy_lit_lights = False
        self.hass.async_create_task(
            self._set_lights(
                True,
                brightness=self._standby_brightness,
                transition=transition,
                color=color,
                apply_turn_on_selection=selection,
                force_selection=force_selection,
                auto_brightness=True,
                looks=looks,
            )
        )
        self.async_write_ha_state()

    def _restore_pre_warning(self, *, found_lit: bool = False) -> None:
        """Restore the pre-warning brightness and color after a re-trigger.

        The caller sets the resulting machine state. No transition: the
        restore must be as immediate as the re-trigger that caused it. With
        found_lit (a seed), real lights found off stay off.
        """
        brightness = self._pre_warn_brightness
        color = self._pre_warn_color
        looks = self._own_looks()
        members = (
            [e for e in self._lights if self._member_is_lit(e)] if found_lit else None
        )
        self._cancel_warning(brightness, color)
        self.hass.async_create_task(
            self._set_lights(
                True, brightness=brightness, color=color, members=members, looks=looks
            )
        )

    def _show_stage(
        self,
        brightness: int,
        transition: float | None,
        color: dict | None,
        looks: dict[str, _Look | None],
    ) -> None:
        """Show an effect or warn stage, keeping its look for a waiting turn-on."""
        self._stage_look = (brightness, transition, color, looks)
        self.hass.async_create_task(
            self._set_stage_lights(brightness, transition, color, looks)
        )

    async def _set_stage_lights(
        self,
        brightness: int,
        transition: float | None = None,
        color: dict | None = None,
        looks: dict[str, _Look | None] | None = None,
    ) -> None:
        """Drive the real lights for an effect or warn stage.

        The virtual light stays logically on. Brightness 0 blinks the real
        lights off (any stage color is moot then). looks overrides the
        stage per member, as for _set_lights.
        """
        if self._removed:
            return
        # A group below a member can change without the member reporting it.
        self._drop_member_cycles()
        context = Context()
        self._self_context_ids.append(context.id)
        groups = _look_groups(self._lights, brightness, color, looks)
        for group, group_brightness, group_color in groups:
            self._commanded_power.update(dict.fromkeys(group, bool(group_brightness)))
            self._expect_echo(
                bool(group_brightness),
                group_brightness or None,
                group_color,
                transition,
                None if group == self._lights else group,
                context_id=context.id,
            )
        self._resend_after_select = bool(self._waiting_turn_ons)
        transition_data = (
            {ATTR_TRANSITION: transition} if transition is not None else {}
        )
        # Set before the call: a member may answer inside it.
        self._attr_is_on = True
        if brightness:
            self._attr_brightness = brightness
            if color:
                self._mirror_color_data(color)
        for group, group_brightness, group_color in groups:
            if group_brightness:
                self._command_members(
                    "turn_on",
                    {
                        "entity_id": group,
                        ATTR_BRIGHTNESS: group_brightness,
                        **(group_color or {}),
                        **transition_data,
                    },
                    context,
                )
            else:
                self._command_members(
                    "turn_off", {"entity_id": group, **transition_data}, context
                )
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Real-light control
    # ------------------------------------------------------------------

    def _auto_lights_on(
        self, *, force_selection: bool = False, reason: str | None = None
    ) -> Coroutine[Any, Any, bool]:
        """Turn the real lights on for an automatic trigger.

        Applies the configured auto-on brightness, color and transition.
        reason says what lit the room, and is None for a re-send.
        """
        if reason is not None and not self._turn_on_waiting(virtual=True):
            self._send_event(
                EVENT_TURNED_ON,
                reason=reason,
                standby=False,
                brightness=self._auto_on_brightness,
                color=_event_color(self._auto_on_color),
                selection=self._selection_to_apply(),
            )
        return self._set_lights(
            True,
            brightness=self._auto_on_brightness,
            transition=self._auto_on_transition,
            color=self._auto_on_color,
            apply_turn_on_selection=True,
            force_selection=force_selection,
            auto_brightness=True,
        )

    def _auto_lights_off(self) -> Coroutine[Any, Any, bool]:
        """Turn the real lights off for an automatic turn-off.

        Applies the configured auto-off transition. Manual offs bypass this.
        """
        return self._set_lights(False, transition=self._auto_off_transition)

    def _scheduled_end_lights_off(self) -> Coroutine[Any, Any, bool]:
        """Turn off at a schedule boundary using the outgoing settings."""
        transition = _opt_transition(
            self._inside_schedule_settings.get(CONF_AUTO_OFF_TRANSITION)
        )
        return self._set_lights(False, transition=transition)

    async def _set_lights(
        self,
        on: bool,
        *,
        brightness: int | None = None,
        transition: float | None = None,
        color: dict | None = None,
        apply_turn_on_selection: bool = False,
        force_selection: bool = False,
        virtual: bool = False,
        auto_brightness: bool = False,
        members: list[str] | None = None,
        mirror: bool = True,
        looks: dict[str, _Look | None] | None = None,
    ) -> bool:
        """Command the real lights; False when a newer command overtook it.

        Only an off, a newer virtual turn-on or a physical change overtakes
        a turn-on waiting for its selection. A stage that the timer
        reaches meanwhile is replaced by a virtual turn-on, as the person's command
        lights the room with the person's settings; an automatic turn-on, or a
        re-send, sends that stage instead of the look it was issued with. An
        automatic turn-on issued while a virtual one waits is dropped. An
        automatic turn-on at the auto-on or standby brightness (auto_brightness) also
        overtakes an older automatic one still waiting, so the brightness chosen
        last is the one the lights end at. members limits the command to some
        of the real lights; without mirror, the virtual light does not report
        the brightness and color it names. looks gives some real lights a
        brightness and color of their own, or leaves one out (None).
        """
        if self._removed:
            return False
        # A blink-fully-off leaves the light logically on while the members
        # are off, so this is still a turn-on for them.
        was_off = not self._attr_is_on or self._all_lights_off()
        if on:
            self._clear_bright_forced_off()
            self._close_owed_ends()
            if color and self._owed is not None and self._owed.look is not None:
                # Unreadable members owe the color named last, not the look's.
                self._owed.look = (self._owed.look[0], color)
            if (
                self._owed is not None
                and looks is None
                and (brightness is not None or color)
            ):
                # Nor their own looks, once a command named another.
                self._owed.member_looks = {}
        context = Context()
        self._self_context_ids.append(context.id)
        if not on or virtual:
            self._command_generation += 1
        generation = self._command_generation
        if on and auto_brightness:
            self._auto_brightness_generation += 1
        auto_brightness_generation = self._auto_brightness_generation
        if on and apply_turn_on_selection and (was_off or force_selection):
            if not virtual and self._turn_on_waiting(virtual=True):
                self._occupancy_lit_lights = False  # the person's on-period
                return False
            waiting = _WaitingTurnOn(
                generation,
                None if virtual else auto_brightness_generation,
                self._turn_on_select_device(),
            )
            self._waiting_turn_ons[context.id] = waiting
            # Reported on, at the brightness asked for, while the select call
            # runs; a re-send during a stage leaves the stage reported.
            if virtual or not self._in_warning():
                if brightness is not None:
                    self._attr_brightness = brightness
                if color:
                    self._mirror_color_data(color)
            self.async_write_ha_state()
            try:
                await self._apply_turn_on_selection(context)
                # What the call changed in its last moments is still on its
                # way to our listener: let it arrive as the call's doing.
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                del self._waiting_turn_ons[context.id]
                self._on_turn_on_cancelled(waiting)
                raise
            finally:
                self._waiting_turn_ons.pop(context.id, None)
            if self._overtaken(waiting):
                # An off, a newer virtual command, a newer auto-on or standby
                # brightness or a physical change landed while the select call was
                # awaited; it stands.
                await self._resend_look_after_select()
                return False
            if not virtual and self._in_warning() and self._stage_look is not None:
                # The light is at a stage, maybe reached during the wait: the
                # members show it, in the color asked for if it names none.
                stage_brightness, stage_transition, stage_color, stage_looks = (
                    self._stage_look
                )
                stage_looks = dict(stage_looks)
                for entity_id, look in list(stage_looks.items()):
                    if look is not None and look[1] is None:
                        asked = (looks or {}).get(entity_id)
                        stage_looks[entity_id] = (look[0], asked[1] if asked else color)
                await self._set_stage_lights(
                    stage_brightness,
                    stage_transition,
                    stage_color or color,
                    stage_looks,
                )
                return False
        if self._drop_member_cycles() and members is not None:
            members = [m for m in members if m in self._lights]
        lights = self._lights if members is None else members
        # Mirror the commanded brightness and color so the virtual light
        # reports them (the echo is only mirrored for a command that named
        # none).
        if on and brightness is not None and mirror:
            self._attr_brightness = brightness
        if on and color and mirror:
            self._mirror_color_data(color)
        groups = _look_groups(lights, brightness, color, looks if on else None)
        for group, group_brightness, group_color in groups:
            self._commanded_power.update(dict.fromkeys(group, on))
            self._expect_echo(
                on,
                group_brightness,
                group_color,
                transition,
                None if members is None and group == self._lights else group,
                context_id=context.id,
            )
        if members is None:
            self._resend_after_select = on and bool(self._waiting_turn_ons)
        # Set before the call: a member may answer inside it.
        self._attr_is_on = on
        for group, group_brightness, group_color in groups:
            service_data: dict = {"entity_id": group}
            if transition is not None:
                service_data[ATTR_TRANSITION] = transition
            if on and group_brightness is not None:
                service_data[ATTR_BRIGHTNESS] = group_brightness
            if on and group_color:
                # One call carries the color to every member; HA
                # filters/converts it per real light, so mixed-capability
                # members each show what they can.
                service_data.update(group_color)
            self._command_members(
                "turn_on" if on else "turn_off", service_data, context
            )
        self.async_write_ha_state()
        return True

    def _command_members(
        self, service: str, service_data: dict, context: Context
    ) -> None:
        """Send one command to some of the real lights, and notice it failing.

        Home Assistant only logs a member's integration raising, so the call
        is awaited apart from the caller, which goes on as if it were sent.
        """
        self.hass.async_create_task(
            self._await_member_command(service, service_data, context)
        )

    async def _await_member_command(
        self, service: str, service_data: dict, context: Context
    ) -> None:
        on = service == "turn_on"
        group = service_data["entity_id"]
        try:
            await self.hass.services.async_call(
                "light", service, service_data, blocking=True, context=context
            )
        except Exception:
            _LOGGER.warning(
                "Unable to turn %s %s",
                "on" if on else "off",
                ", ".join(group),
                exc_info=True,
            )
            # A call that fails at once fails inside its caller: judge the
            # light once the caller has moved the state machine on.
            await asyncio.sleep(0)
            if self._removed:
                return
            if len(group) == 1:
                self._on_member_command_failed(group, on, context)
                return
            # Home Assistant raises for the group once every member answered,
            # so one whose integration took the command may still report it.
            # Judge once such a reply would have settled.
            delay = ECHO_SETTLE_SECONDS + float(service_data.get(ATTR_TRANSITION) or 0)

            @callback
            def _judge(_now: datetime) -> None:
                self._failed_command_unsubs.discard(unsub)
                if not self._removed:
                    self._on_member_command_failed(group, on, context)

            unsub = async_call_later(self.hass, delay, _judge)
            self._failed_command_unsubs.add(unsub)

    def _on_member_command_failed(
        self, group: list[str], on: bool, context: Context
    ) -> None:
        """Take back a command the members will not answer, and judge the light.

        Called at once for one member, and for a group once a slow member's
        reply would have settled; a reply that came meanwhile stands. A
        member that a newer command has reached since is left to that one. With
        nothing lit, a light reporting on goes idle, as it does when its lit
        member is deleted; one reporting off while a member is still lit
        adopts it, as it would a bulb lit by nobody.
        """
        for entity_id in group:
            expectations = self._echo_expectations.get(entity_id)
            if expectations and expectations[-1].context_id != context.id:
                continue
            if expectations:
                kept = [e for e in expectations if e.context_id != context.id]
                if kept:
                    self._echo_expectations[entity_id] = deque(
                        kept, maxlen=ECHO_HISTORY
                    )
                else:
                    del self._echo_expectations[entity_id]
            self._commanded_power.pop(entity_id, None)
        if self._turn_on_waiting() or any(
            self._awaited_power(entity_id) is not None for entity_id in self._lights
        ):
            return  # another command may still light, or darken, the room
        if on:
            if self._machine_state != STATE_IDLE and self._all_lights_off():
                self._go_idle(reason=REASON_REJECTED)
        elif not self._attr_is_on and not self._all_lights_off():
            if color := self._first_on_member_color():
                self._set_color_state(*color)
            self._on_light_state_change(
                "on",
                self._first_on_member_brightness(),
                manual=False,
                claim=False,
                send_turned_on=False,
            )
        self.async_write_ha_state()

    def _on_turn_on_cancelled(self, waiting: _WaitingTurnOn) -> None:
        """Take back the on that a turn-on reported when its caller was stopped.

        A script or automation stopped while the select call ran sends
        nothing more. A newer command stands; an automatic on-period that
        the turn-on had replaced gets its lights after all.
        """
        if self._removed or self.hass.is_stopping:
            return
        if self._overtaken(waiting):
            self.hass.async_create_task(self._resend_look_after_select())
        elif self._machine_state == STATE_STANDBY:
            self._enter_standby(
                self._auto_on_transition, selection=True, force_selection=True
            )
        elif self._machine_state != STATE_IDLE and not self._in_warning():
            self.hass.async_create_task(self._auto_lights_on(force_selection=True))
        self.async_write_ha_state()

    async def _resend_look_after_select(self) -> None:
        """Send the newest look again, now that the last select call ended.

        A turn-on replaced while its select call ran sends nothing itself,
        but its preset may have landed on top of the command that replaced
        it: the real lights end at the look sent last, not the preset's.
        """
        if self._removed or not self._resend_after_select or self._waiting_turn_ons:
            return
        if self._in_warning() and self._stage_look is not None:
            await self._set_stage_lights(*self._stage_look)
            return
        await self._set_lights(
            True, brightness=self._attr_brightness, color=self._current_color()
        )

    async def _apply_turn_on_selection(self, context: Context) -> None:
        """Apply the configured select option before a turn-on or a re-send."""
        # The settings may name another select entity by the time it answers.
        select_entity = self._turn_on_select_entity
        if not select_entity:
            return
        select_state = self.hass.states.get(select_entity)
        if select_state is None or select_state.state == STATE_UNAVAILABLE:
            # HA only logs a call to such an entity, so it would otherwise be
            # recorded as applied. An unknown one (no current option) accepts it.
            state = "missing" if select_state is None else select_state.state
            _LOGGER.log(
                self._selection_log_level(select_entity, state),
                "Turn-on selection entity %s is %s; turning on the lights without it",
                select_entity,
                state,
            )
            return
        option, source = self._resolve_turn_on_selection()
        if option is None:
            parts = []
            if self._turn_on_select_source_entity:
                parts.append(
                    f"the option source entity {self._turn_on_select_source_entity}"
                )
            if self._turn_on_select_option:
                parts.append(
                    f"the fixed/fallback option {self._turn_on_select_option!r}"
                )
            _LOGGER.log(
                self._selection_log_level(select_entity, "unresolved"),
                "Unable to resolve a turn-on selection option for %s: %s; "
                "turning on the lights without one",
                select_entity,
                (
                    " and ".join(parts)
                    + " yielded no option the turn-on selection entity currently"
                    " offers"
                )
                if parts
                else "no option source entity or fixed/fallback option is configured",
            )
            return
        try:
            async with asyncio.timeout(TURN_ON_SELECT_TIMEOUT):
                await self.hass.services.async_call(
                    "select",
                    SERVICE_SELECT_OPTION,
                    {
                        "entity_id": select_entity,
                        ATTR_OPTION: option,
                    },
                    blocking=True,
                    context=context,
                )
        except Exception:
            # Turn-on selections are an enhancement; a missing select, a
            # renamed option, a failing integration or one that never answers
            # must never leave the room dark, or the light reporting on with
            # nothing lit.
            _LOGGER.log(
                self._selection_log_level(select_entity, "failed"),
                "Unable to apply turn-on selection option %r using %s; turning on the "
                "lights without it",
                option,
                select_entity,
                exc_info=True,
            )
        else:
            self._last_turn_on_selection_option = option
            self._last_turn_on_selection_source = source
            if self._selection_warned is not None:
                self._selection_warned = None
                _LOGGER.info("Turn-on selection using %s applied again", select_entity)

    def _selection_log_level(self, select_entity: str, reason: str) -> int:
        """Return the level for a skipped selection: warn once, then debug."""
        warned = (select_entity, reason)
        level = logging.DEBUG if warned == self._selection_warned else logging.WARNING
        self._selection_warned = warned
        return level

    def _resolve_turn_on_selection(self) -> tuple[str | None, str | None]:
        """Resolve the option source entity's state, else the fixed/fallback option."""
        candidates: list[tuple[str, str]] = []
        if self._turn_on_select_source_entity:
            source_state = self.hass.states.get(self._turn_on_select_source_entity)
            if source_state is not None and source_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
                "",
            ):
                candidates.append(
                    (source_state.state, self._turn_on_select_source_entity)
                )
        if self._turn_on_select_option:
            candidates.append((self._turn_on_select_option, "fixed"))

        select_state = self.hass.states.get(self._turn_on_select_entity)
        select_options = (
            select_state.attributes.get(ATTR_OPTIONS)
            if select_state is not None
            else None
        )
        for option, source in candidates:
            if not isinstance(select_options, (list, tuple)):
                return option, source
            if (matched := match_option(option, select_options)) is not None:
                return matched, source
        return None, None

    # ------------------------------------------------------------------
    # Extra state attributes
    # ------------------------------------------------------------------

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Save each member's pre-warning look while the attributes name one."""
        if self._warning_active:
            looks = self._pre_warn_members
        elif self._owed_look() is not None:
            looks = self._owed.member_looks
        else:
            looks = {}
        return RestoredExtraData({"pre_warn_members": looks})

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state-machine and attribution attributes."""

        def _fmt(t: datetime | None) -> str | None:
            return t.isoformat() if t else None

        owed = self._owed
        look = self._owed_look()
        window = self._schedule_window_applied or (owed.window if owed else None)
        attributes = {
            "molight_state": self._machine_state,
            "auto_off_held": self._held,
            "last_on_physical": _fmt(self._last_on_physical),
            "last_on_virtual": _fmt(self._last_on_virtual),
            "last_on_occupancy": _fmt(self._last_on_occupancy),
            "last_on_illuminance": _fmt(self._last_on_illuminance),
            "last_on_door": _fmt(self._last_on_door),
            "last_off_manual": _fmt(self._last_off_manual),
            "last_brightness_change_physical": _fmt(
                self._last_brightness_change_physical
            ),
            "last_brightness_change_virtual": _fmt(
                self._last_brightness_change_virtual
            ),
            "last_color_change_physical": _fmt(self._last_color_change_physical),
            "last_color_change_virtual": _fmt(self._last_color_change_virtual),
            "last_turn_on_selection_option": self._last_turn_on_selection_option,
            "last_turn_on_selection_source": self._last_turn_on_selection_source,
            # Non-null only while the effect or warn stage is showing; restored
            # so a restart mid-warning can put back the pre-warning brightness
            # and color. A virtual turn-on waiting for its selection already
            # ends the warning, so a brightness step meanwhile builds on it.
            "warning_active": (
                self._warning_active and not self._turn_on_waiting(virtual=True)
            )
            or look is not None,
            "pre_warn_brightness": look[0] if look else self._pre_warn_brightness,
            "pre_warn_color": look[1] if look else self._pre_warn_color,
            "schedule_window_start": window,
            ATTR_SCHEDULE_WINDOW_SCHEDULE: self._schedule_sensor if window else None,
            "bright_forced_off": self._bright_forced_off,
            "bright_resume_until": _fmt(self._bright_resume_until),
        }
        end_off = self._schedule_end_off_pending or bool(owed and owed.end_off)
        if self._schedule_mode == SCHEDULE_MODE_GATE and self._schedule_sensor:
            attributes[ATTR_SCHEDULE_END_OFF_PENDING] = end_off
        if self._is_scheduled_light:
            attributes[ATTR_ACTIVE_SETTINGS] = (
                ACTIVE_SETTINGS_INSIDE
                if self._inside_schedule
                else ACTIVE_SETTINGS_OUTSIDE
            )
            attributes[ATTR_SCHEDULE_END_OFF_PENDING] = end_off
            attributes[ATTR_STANDBY_SUPPRESSED] = self._standby_suppressed
            attributes[ATTR_MANUAL_OFF_CLEARED] = self._manual_off_cleared
            attributes[ATTR_ACTIVE_SETTINGS_SCHEDULE] = self._schedule_sensor
            attributes[ATTR_ACTIVE_SETTINGS_WINDOW] = self._settings_window()
        return attributes
