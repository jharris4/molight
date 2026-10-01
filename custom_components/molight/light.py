"""Virtual Light platform for MoLight.

A VirtualLight controls N real light entities and manages a state machine
that integrates optional occupancy, illuminance, and schedule virtual sensors.

State machine
=============
  IDLE       lights off, no timer
  ACTIVE     lights on, timer running
               → entered on a manual/physical turn-on or an open-mode door
                 trigger, when no occupancy/maintain/door hold applies
  OCCUPIED   lights on, occupancy active, timer suspended
  COUNTDOWN  occupancy just cleared, timer ticking toward lights-off
  SCHEDULED  lights on inside a follow-mode schedule window, no timer
  STANDBY    lights at a scheduled light's standby level, no timer
  EFFECT     auto-off imminent, showing the brief effect/blink warning stage
  WARN       auto-off imminent, grace period before the lights go off

Transitions
  IDLE + (manual/physical on OR open-mode door trigger) [no active hold]
       → ACTIVE  (start timer immediately)

  IDLE/ACTIVE/COUNTDOWN + occupancy becomes active
       → OCCUPIED  (cancel any running timer)

  OCCUPIED + occupancy clears
       → COUNTDOWN  (start timer)

  ACTIVE/COUNTDOWN + timer expires
       → EFFECT → WARN → IDLE  (see "Effect/warn warning" below)

Effect/warn warning
  When the auto-off timer expires the light can flag the impending off before
  going dark, controlled by these options (all default to the feature being
  off, so the light turns straight off exactly as before):
    EFFECT: a brief cue (blink/dip to effect_brightness, 0 = fully off) shown
            for effect_timeout seconds. Skipped when effect_timeout is 0.
    WARN:   a grace period at warn_brightness (absent = the brightness the
            light had before the warning) for warn_timeout seconds, then off.
            Skipped when warn_timeout is 0.
  Each stage can optionally fade into its brightness over effect_transition /
  warn_transition seconds (each validated <= its stage's timeout). Separately,
  auto_on_transition / auto_off_transition fade automatic turn-ons and
  turn-offs; manual/physical turn-ons and a manual off never get a *configured*
  transition (a caller-supplied one is forwarded; see "Transition support").
  Throughout EFFECT and WARN the virtual light stays logically on. Any
  re-trigger (occupancy/maintain becoming active, a manual or physical
  turn-on, an external dim) cancels the sequence and behaves exactly as if
  the pre-off timer were still running. Re-triggers that carry no brightness
  of their own (occupancy/maintain/door, a virtual turn-on without an explicit
  brightness, a gate lifting, auto-off becoming held) restore the pre-warning
  brightness so the warning is transparent; a physical turn-on or an external
  dim brings its own brightness, which is honoured instead of the snapshot.
  Bright-forces-off (control mode) and a hard-gate/follow window ending still turn
  the lights off during the sequence, as they would mid-countdown.
  Each stage can also show an optional color, either a color temperature
  (effect_color_temp / warn_color_temp) or an RGB color (effect_rgb_color /
  warn_rgb_color, e.g. a red warn stage as an unmissable cue); color-capable
  members show it, brightness-only members just show the stage brightness. A
  warn stage without a color of its own undoes an effect-stage recolor, and
  every re-trigger restores the pre-warning color along with the brightness.
  The pre-warning brightness and color are exposed as the pre_warn_brightness
  / pre_warn_color attributes (null outside the sequence) and survive
  restarts: a restart landing mid-warning with the lights still on restores
  them instead of adopting the stage's; lights found off stay off (the
  auto-off completed).

  any + light turned off externally
       → IDLE

Standby (Virtual Scheduled Light inside-schedule settings only)
  A standby brightness (and optional color) makes the light rest there
  instead of turning off while the inside settings are active:
  - An off light comes on at standby when the inside settings take over, at
    startup, or when illuminance in control mode turns dark (control mode
    keeps standby off while bright; gate mode never touches it). A raised
    on-period that brightness cut short is resumed first, as without standby.
  - Occupancy and the door raise it to the auto-on level like any turn-on
    (the maintain entity only holds a raised light); when the timer expires,
    the effect/warn stages run and the last step drops to standby, not off
    (at the pre-warning color when standby has no color of its own).
  - An external dim or a manual turn-on from standby runs a timer that ends
    back at standby. A manual off cancels standby until the next schedule
    boundary; turning the light back on rejoins it.
  - Leaving the inside settings while at standby hands the light to the end
    action: turn off, switch (recalculated), or keep (a fresh timeout).
  - A restart puts a light that was at standby back there, and a member
    back from unavailable is re-sent the standby settings.

Turn-on attribution
  Five timestamps record the last time the virtual light was activated and why:
    last_on_physical:    an underlying real light entity changed to ON from an
                         external source (physical switch, another automation, HA
                         UI acting on the real entity) while this virtual light
                         was IDLE.
    last_on_virtual:     the user toggled this virtual light entity ON via the HA UI
                         (async_turn_on was called directly).
    last_on_occupancy:   occupancy sensor triggered the lights.
    last_on_illuminance: an illuminance→dark change triggered the lights.
    last_on_door:        a door sensor opening triggered the lights.

  All are exposed as extra state attributes (ISO strings or null). They
  decide whether a manual off still stands, not how long an on-period lasts.

  Brightness changes are tracked the same way: last_brightness_change_physical
  records external changes on the real lights (and restarts a running
  ACTIVE/COUNTDOWN timer; brightness 0 counts as off, leaving 0 as on), while
  last_brightness_change_virtual records brightness set through this entity.
  Color changes are tracked identically via last_color_change_physical /
  last_color_change_virtual, and an external recolor restarts a running timer
  exactly like an external dim, since both are human activity.

Color support
  The virtual light derives its color capabilities from the real lights: it
  advertises HS when any member can show a color (hs/rgb/rgbw/rgbww/xy; HA
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
  A transition supplied by the caller (a scene, a script, light.turn_on with a
  fade) is forwarded to the members. HA strips it before async_turn_on unless
  we advertise LightEntityFeature.TRANSITION, so _update_capabilities derives
  it from the members and fails open: withheld only once every member is
  visible and none can fade.

Maintain occupancy (when a maintain occupancy entity is configured)
  The maintain entity holds an already-on light on while it is on; it never
  turns the light on and is ignored while the light is off. Unlike the
  combined sensor's maintain_sensors (which only extend occupancy started by
  a trigger sensor), it holds the light regardless of how it was lit:
  manual, physical, or occupancy.

  - Maintain ON while the light is on (ACTIVE/COUNTDOWN) → OCCUPIED, timer
    cancelled. Illuminance/schedule gating does not apply: it is not a
    turn-on. Forced offs (bright in control mode, hard-gate window end) still win,
    exactly as they do over regular occupancy.
  - Occupancy clearing while maintain is on keeps the light OCCUPIED.
  - The countdown starts only when both the regular occupancy entity and the
    maintain entity are clear, anchored to the max of their
    latest_occupied_time attributes.
  - The false-detection quick off applies on a maintain clear only when both
    sensors flagged their clears false (a genuine presence on either side
    means the light earns its normal countdown).
  - Startup: a light that is already on with the maintain entity on is
    adopted as OCCUPIED (no timer).

Illuminance handling (when an illuminance entity is configured), per
illuminance_mode:
  - Occupancy only turns lights ON when illuminance is OFF (dark), in both modes.
  - Illuminance ON→OFF (bright→dark): if currently occupied, enter OCCUPIED;
    else if brightness forced the lights off and that on-period has time left,
    enter COUNTDOWN for the rest of it. It lasts until the later of
    bright_resume_until, recorded at the forced off, and a timeout after
    latest_occupied_time. The record is when the interrupted countdown would
    have ended, or a timeout from the forced off for a light presence was
    holding (whoever held it was there until then at least); a warning stage
    or a standby rest leaves none. An on-period ended any other way (manual
    off, timer, schedule) is never resumed, nor is one the user turned off
    while brightness had it off.
  - Illuminance OFF→ON (dark→bright):
      control:  go IDLE, turn lights off.
      gate:     no effect; bright never turns lights off. Use when the lux
                sensor can see the controlled lights, which would otherwise
                oscillate (lights on → reads bright → forced off → dark → ...).

Holding auto-off
  Auto-off is *held* while the companion "<name> Auto-off" switch is off OR
  any configured keep-on entity (hold_entities) is on. While held, every
  automatic turn-off is suspended (timer expiry, the false-detection quick
  off, bright-forces-off, and schedule window ends), but turn-ons and manual
  control work exactly as usual (a manual off still turns the lights off).
  State-machine transitions keep happening; they just never arm a timer.

  When the last hold releases, the light re-evaluates its rules from current
  conditions: a follow-mode or hard-gate window that ended while held turns it
  off now (the window marker/pending boundary is kept while held for exactly
  this), as does being bright in illuminance control mode; active
  occupancy keeps it on (OCCUPIED); an active follow window keeps it
  SCHEDULED; otherwise a fresh full timer starts (ACTIVE).

  A keep-on entity going unavailable/unknown holds its last known value, as
  everywhere else in the integration; at startup an unavailable keep-on
  entity counts as not holding.

Schedule handling (when a schedule entity is configured), per schedule_mode:
  - follow: lights turn ON at window start (state SCHEDULED, no timer) and
    OFF at window end. Boundaries are edge-triggered: manual changes between
    them stand. A marker that changes while the schedule stays on (an all-day
    window at midnight) is a window start. schedule_window_start records the
    window whose start we applied; it persists across restarts so a boundary
    missed while HA was down is applied exactly once at startup, while a
    manual off mid-window is respected. Occupancy/illuminance are ignored
    while SCHEDULED.
  - gate: occupancy may only activate lights inside the window; window end
    forces lights off (like illuminance turning bright), window start
    re-evaluates occupancy.
  - gate_switch: the same activation gate for an OFF light. Window end keeps
    an ON light on but recalculates its state and timer from current occupancy
    history; active presence adopts it, while an expired timeout applies the
    configured effect/warn/off behavior.
  - gate_keep: the same gate for turning an OFF light on; once the lights
    are on, occupancy and the door behave as inside the window (adopt, hold,
    re-hold), and window end preserves the current on-period, including its
    sensor hold, countdown or warning.

Door handling (when a door entity is configured), per door_mode:
  Opening the door (state on) is a turn-on trigger, gated by illuminance and
  a gate-mode schedule exactly like occupancy: it only lights the room when
  it is dark (if an illuminance entity is set) and inside a gate window.
  - open:       opening turns the lights on with the normal timeout (ACTIVE);
    the door is otherwise ignored, so closing does nothing and the lights
    time out even if the door stays open. A momentary trigger.
  - open_close: the open door holds the lights on with no timer (OCCUPIED,
    just like an occupancy sensor) for as long as it stays open; closing
    starts the auto-off countdown, but defers to any active occupancy/maintain
    entity or keep-on hold so a closed door never cuts the lights over someone
    still present. A held-open door survives a restart the same way a
    maintained light does: an already-on light with the door open is adopted
    as OCCUPIED. Forced offs (bright in control mode, a hard-gate window ending)
    still win over a held-open door, as they do over occupancy.
    A standing-open door is re-evaluated when a gate lifts, exactly like
    already-active occupancy: illuminance going dark or a gate-mode window
    starting turns the lights on and holds them while the door is open. The
    door's last known state is cached, so a sensor that blips unavailable
    keeps holding until it reports closed.
"""

from __future__ import annotations

import contextlib
import logging
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import color as color_util
from homeassistant.util.percentage import percentage_to_ranged_value

from .const import (
    ACTIVE_SETTINGS_INSIDE,
    ACTIVE_SETTINGS_OUTSIDE,
    ATTR_ACTIVE_SETTINGS,
    ATTR_ACTIVE_SETTINGS_SCHEDULE,
    ATTR_ACTIVE_SETTINGS_WINDOW,
    ATTR_SCHEDULE_END_OFF_PENDING,
    ATTR_STANDBY_SUPPRESSED,
    CONF_AUTO_OFF_TRANSITION,
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_AUTO_ON_COLOR_TEMP,
    CONF_AUTO_ON_RGB_COLOR,
    CONF_AUTO_ON_TRANSITION,
    CONF_DOOR_ENTITY,
    CONF_DOOR_MODE,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_COLOR_TEMP,
    CONF_EFFECT_RGB_COLOR,
    CONF_EFFECT_TIMEOUT,
    CONF_EFFECT_TRANSITION,
    CONF_ENTITY_TYPE,
    CONF_FALSE_OFF_DELAY,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_MODE,
    CONF_INSIDE_SCHEDULE_SETTINGS,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_OUTSIDE_SCHEDULE_SETTINGS,
    CONF_SCHEDULE_END_ACTION,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    CONF_STANDBY_BRIGHTNESS,
    CONF_STANDBY_COLOR_TEMP,
    CONF_STANDBY_RGB_COLOR,
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_TURN_ON_SELECT_SOURCE_ENTITY,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_COLOR_TEMP,
    CONF_WARN_RGB_COLOR,
    CONF_WARN_TIMEOUT,
    CONF_WARN_TRANSITION,
    DATA_AUTO_OFF_ENABLED,
    DEFAULT_DOOR_MODE,
    DEFAULT_EFFECT_BRIGHTNESS,
    DEFAULT_EFFECT_TIMEOUT,
    DEFAULT_FALSE_OFF_DELAY,
    DEFAULT_ILLUMINANCE_MODE,
    DEFAULT_LIGHT_TIMEOUT,
    DEFAULT_SCHEDULE_END_ACTION,
    DEFAULT_SCHEDULE_MODE,
    DEFAULT_WARN_TIMEOUT,
    DOMAIN,
    DOOR_MODE_OPEN_CLOSE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_SCHEDULED_LIGHT,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE,
    SCHEDULE_END_ACTION_SWITCH,
    SCHEDULE_END_ACTION_TURN_OFF,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_KEEP,
    SCHEDULE_MODE_GATE_SWITCH,
    SIGNAL_AUTO_OFF_TOGGLED,
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
    lights_support_brightness,
    lights_support_transition,
    molight_config,
    suggested_entity_id,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, EventStateChangedData, State
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

# Member color modes an hs command can drive (HA converts hs to each member's
# native mode); any of them lets the virtual light advertise HS itself. Shared
# with the config flow so an upstream addition can't split the two.
_HS_CAPABLE_MODES = COLOR_MODES_COLOR


def _opt_transition(value: float | None) -> float | None:
    """Convert a configured transition to float seconds; absent/0 = None."""
    return float(value) if value else None


def _opt_rgb_color(value: list | None) -> dict | None:
    """Convert a configured [r, g, b] to turn-on service data; absent = None."""
    return {ATTR_RGB_COLOR: tuple(int(c) for c in value)} if value else None


def _opt_color(cfg: dict[str, Any], temp_key: str, rgb_key: str) -> dict | None:
    """Convert a stored color-pair to turn-on service data; absent = None.

    The keys are mutually exclusive (enforced by the config/options flows);
    the temp wins if both somehow appear, matching the remote presets.
    """
    if kelvin := cfg.get(temp_key):
        return {ATTR_COLOR_TEMP_KELVIN: int(kelvin)}
    return _opt_rgb_color(cfg.get(rgb_key))


# Single-entity settings references a light subscribes to. A scheduled light
# subscribes to both sides' references; events from the inactive side simply
# match no role in _handle_state_change.
_WATCHED_REFERENCE_KEYS = (
    CONF_OCCUPANCY_ENTITY,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_ILLUMINANCE_ENTITY,
    CONF_SCHEDULE_ENTITY,
    CONF_DOOR_ENTITY,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MoLight light entities from a config entry."""
    if entry.data[CONF_ENTITY_TYPE] in (
        ENTITY_TYPE_LIGHT,
        ENTITY_TYPE_SCHEDULED_LIGHT,
    ):
        entity = VirtualLight(hass, entry)
        if entity_id := suggested_entity_id(hass, entry, ENTITY_ID_FORMAT):
            entity.entity_id = entity_id
        async_add_entities([entity])


# A member write is our echo only if it is consistent with what we asked for,
# allowing for bulbs that reply in two parts, in fade steps, quantised, or in
# another color mode while the command settles, and for slow bulbs whose full
# reply arrives much later; a contradiction is human activity at any age.
# Context alone cannot tell: Home Assistant reuses our service-call context on
# a member for 5 s, and a slow bulb's genuine reply arrives under its own.
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

    A color-temp member also carries a derived hs_color, so its own
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
    """Canonicalise turn-on color service data like a member state would."""
    if not color:
        return None
    if ATTR_COLOR_TEMP_KELVIN in color:
        return (ColorMode.COLOR_TEMP, color[ATTR_COLOR_TEMP_KELVIN])
    if ATTR_HS_COLOR in color:
        return (ColorMode.HS, tuple(color[ATTR_HS_COLOR]))
    if ATTR_RGB_COLOR in color:
        return (ColorMode.HS, color_util.color_RGB_to_hs(*color[ATTR_RGB_COLOR]))
    return None


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
    """Whether a color moved from old toward target: a fade step, not a recolor."""
    if old[0] is new[0] is target[0] is ColorMode.COLOR_TEMP:
        return _toward(old[1], new[1], target[1])
    (old_h, old_s), (new_h, new_s), (target_h, target_s) = (
        _as_hs(old),
        _as_hs(new),
        _as_hs(target),
    )
    # The hue is on the short arc from old to target, the saturation between.
    on_arc = (
        abs(
            _hue_delta(old_h, new_h)
            + _hue_delta(new_h, target_h)
            - _hue_delta(old_h, target_h)
        )
        < HUE_ARC_EPSILON
    )
    return on_arc and (new_s - old_s) * (target_s - new_s) >= 0


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

    def judge(
        self, old_state: State | None, new_state: State, *, settling: bool
    ) -> str:
        """Return "match", "pending" (echo still arriving), or "contradiction".

        While the command is settling a partial reply is "pending"; after that
        only a full match is still our echo.
        """
        attrs = new_state.attributes
        powered = new_state.state == "on"
        if not self.on:
            # On at brightness 0 is off in disguise, as the state machine reads it.
            lit = powered and attrs.get(ATTR_BRIGHTNESS) != 0
            return "match" if not lit else "contradiction"
        if not powered:
            return "contradiction"
        # A member may report on at brightness 0 first (a dimmer with no level
        # yet): only power contradicts an on command; brightness is judged below.
        # Before the member was on we cannot judge its attributes: a bulb that
        # reports power first still carries its previous brightness and color.
        was_on = old_state is not None and old_state.state == "on"
        settled = True
        if self.brightness is not None:
            new_b = attrs.get(ATTR_BRIGHTNESS)
            if not new_b:
                # A power-only reply shows no level: while settling the level
                # may still be coming (two-part repliers), so keep waiting;
                # late, this is an on/off-only member's complete reply.
                if settling:
                    settled = False
            elif abs(new_b - self.brightness) > ECHO_BRIGHTNESS_TOLERANCE:
                old_b = old_state.attributes.get(ATTR_BRIGHTNESS) if was_on else None
                if (
                    was_on
                    and old_b is not None
                    and not (new_b == old_b or _toward(old_b, new_b, self.brightness))
                ):
                    return "contradiction"
                # Without an old level there is no direction to judge: a
                # power-first bulb's fade step must not read as a human dim.
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
                    was_on
                    and (old_color := _state_color(old_state)) is not None
                    and new_color != old_color
                    and not _color_toward(old_color, new_color, self.color)
                ):
                    return "contradiction"
                # Without an old color there is no anchor to judge against:
                # a member showing its color late is not a human recolor.
                settled = False
        if settled:
            return "match"
        return "pending" if settling else "contradiction"


class VirtualLight(LightEntity, RestoreEntity):
    """A virtual light with occupancy/illuminance/schedule/door awareness."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    # Reassigned per-instance by _update_capabilities, never mutated in place.
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}
    # Fail open until the first capability derivation (which at boot waits for
    # EVENT_HOMEASSISTANT_STARTED): a startup caller must not lose their fade.
    _attr_supported_features = LightEntityFeature.TRANSITION
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the virtual light from its config entry."""
        self.hass = hass
        entry_cfg = molight_config(entry)
        self._attr_name = entry_cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self._entry_id = entry.entry_id

        self._lights: list[str] = entry_cfg.get(CONF_LIGHTS, [])
        self._is_scheduled_light = (
            entry_cfg[CONF_ENTITY_TYPE] == ENTITY_TYPE_SCHEDULED_LIGHT
        )
        self._settings_schedule_entity: str | None = (
            entry_cfg.get(CONF_SCHEDULE_ENTITY) if self._is_scheduled_light else None
        )
        self._schedule_end_action = entry_cfg.get(
            CONF_SCHEDULE_END_ACTION, DEFAULT_SCHEDULE_END_ACTION
        )
        self._outside_schedule_settings: dict[str, Any] = entry_cfg.get(
            CONF_OUTSIDE_SCHEDULE_SETTINGS, {}
        )
        self._inside_schedule_settings: dict[str, Any] = entry_cfg.get(
            CONF_INSIDE_SCHEDULE_SETTINGS, {}
        )
        self._inside_schedule = False
        self._restored_inside_schedule: bool | None = None
        # The settings schedule's window marker, last known and as restored:
        # a different one after a restart means a boundary was crossed.
        self._settings_window_start: str | None = None
        self._restored_window_start: str | None = None
        # A scheduled-light off boundary deferred by an Auto-off/keep-on hold.
        # Persisted as a state attribute so a restart cannot lose the pending
        # boundary; returning inside the schedule cancels it.
        self._schedule_end_off_pending = False
        # Ephemeral startup catch-up for a switch-state boundary missed while
        # Home Assistant was down. The restored active side proves whether the
        # configured schedule actually crossed from inside to outside.
        self._schedule_end_switch_pending = False
        # A manual off cancels standby until the next schedule boundary.
        self._standby_suppressed = False
        # Whether the light was resting at standby before a restart, so the
        # seed puts it back there instead of adopting it with a timer.
        self._restored_standby = False
        # Every settings mapping this light may run under (both sides of a
        # scheduled light, or the regular light's own config), so the entity
        # references of all of them can be subscribed to up front.
        self._settings_sets: tuple[dict[str, Any], ...] = (
            (self._outside_schedule_settings, self._inside_schedule_settings)
            if self._is_scheduled_light
            else (entry_cfg,)
        )
        self._apply_light_settings(self._settings_sets[0])

        self._last_turn_on_selection_option: str | None = None
        self._last_turn_on_selection_source: str | None = None
        # True while the current on-period was started by occupancy (not by
        # the user), the only case where a false-detection clear may cut the
        # lights short.
        self._occupancy_lit_lights: bool = False

        # Brightness and color the light had when the warning sequence began,
        # restored on any re-trigger so the effect/warn stages leave no
        # lasting trace. Exposed as the pre_warn_brightness / pre_warn_color
        # attributes so they survive a restart landing mid-warning.
        self._pre_warn_brightness: int | None = None
        self._pre_warn_color: dict | None = None
        # Persisted in its own right: the snapshots above are legitimately
        # null for members reporting no brightness or color.
        self._warning_active: bool = False
        self._effect_sent_color: bool = False

        # Last known open/closed of the door, kept ourselves so a briefly
        # unavailable sensor (battery contact sensors blip) holds its last
        # value instead of reading as closed and dropping its hold.
        self._door_open: bool = False
        # Whether the door has reported open/closed since startup.
        self._door_seen: bool = False
        # Last known on/off of each keep-on entity, kept ourselves so an
        # unavailable entity holds its last value instead of reading as off.
        self._hold_states: dict[str, bool] = {}
        # Last known bright/dark, None until first seen; a recovery matching
        # it must not replay the bright/dark edge actions.
        self._illuminance_last_bright: bool | None = None
        # Last known occupied/clear, None until first seen; a recovery
        # matching it must not re-light a room the user turned off.
        self._occupancy_last_on: bool | None = None
        # When the user last turned the light off, here or at the wall: a
        # start deferred by an unreadable sensor is not applied over it.
        self._last_manual_off: datetime | None = None
        # A settings boundary since that off, which ends it like a turn-on.
        self._manual_off_cleared = False
        # Members that have reported on/off since this light was set up: a
        # placeholder they leave later is a reload, not startup loading.
        self._members_seen: set[str] = set()
        # Last known on/off of the schedule, None until first seen; a
        # recovery matching it crossed no window boundary.
        self._schedule_last_on: bool | None = None
        # True while the light is off because brightness forced it off: only
        # such an on-period is resumed when it turns dark again.
        self._bright_forced_off: bool = False
        # How long that on-period would have lasted at least; None when
        # only presence history can tell.
        self._bright_resume_until: datetime | None = None
        # Effective hold: companion switch off OR any keep-on entity on.
        self._held: bool = False
        # Start marker (ISO string) of the follow-mode window we last turned
        # lights on for and whose end we have not yet applied. Survives
        # restarts so missed boundaries are caught up exactly once while
        # manual overrides mid-window are respected.
        self._schedule_window_applied: str | None = None

        self._machine_state: str = STATE_IDLE
        self._attr_is_on = False
        self._timer_unsub: CALLBACK_TYPE | None = None
        # When the armed timer fires, None while none is armed.
        self._timer_ends: datetime | None = None
        # Context ids of our own light service calls: while a command settles,
        # only a write under one of them can be its echo.
        self._self_context_ids: deque[str] = deque(maxlen=16)
        # What each member should echo for our recent commands, oldest first
        # (see judge()): a reply to the previous one may trail the latest.
        self._echo_expectations: dict[str, deque[_EchoExpectation]] = {}
        # Counts offs and manual turn-ons, so a turn-on that waited for its
        # turn-on selection can tell that it was overtaken; an automatic
        # turn-on defers to a manual one still waiting instead.
        self._command_generation = 0
        self._manual_on_pending = 0
        # Contexts of the select calls that turn-ons are waiting for.
        self._selecting_context_ids: set[str] = set()
        # Counts automatic turn-ons at the auto-on or standby level, so one
        # that waited for its selection yields to the level chosen since.
        self._auto_level_generation = 0

        self._last_on_physical: datetime | None = None
        self._last_on_virtual: datetime | None = None
        self._last_on_occupancy: datetime | None = None
        self._last_on_illuminance: datetime | None = None
        self._last_on_door: datetime | None = None
        # When a schedule end last gave a light resting at standby a fresh
        # timeout: anchors that on-period like a turn-on, without claiming one.
        self._standby_timeout_started: datetime | None = None
        self._last_brightness_change_physical: datetime | None = None
        self._last_brightness_change_virtual: datetime | None = None
        self._last_color_change_physical: datetime | None = None
        self._last_color_change_virtual: datetime | None = None

    def _apply_light_settings(self, cfg: dict[str, Any]) -> None:
        """Load one flat Virtual Light settings mapping.

        A regular Virtual Light applies its entry config once; a Virtual
        Scheduled Light applies its outside- or inside-schedule mapping and
        re-applies the other one on every schedule edge, so everything set
        here must be derivable from the mapping alone.
        """
        self._light_timeout = int(cfg.get(CONF_LIGHT_TIMEOUT, DEFAULT_LIGHT_TIMEOUT))
        self._false_off_delay = int(
            cfg.get(CONF_FALSE_OFF_DELAY, DEFAULT_FALSE_OFF_DELAY)
        )

        # Brightness (0-255) for automatic turn-ons, converted from the stored
        # percentage with HA's own percent→brightness scaling. None leaves
        # automatic turn-ons unqualified.
        pct = cfg.get(CONF_AUTO_ON_BRIGHTNESS)
        self._auto_on_brightness = (
            round(percentage_to_ranged_value((1, 255), int(pct))) if pct else None
        )
        # Optional color for automatic turn-ons, as turn-on service data.
        # None leaves automatic turn-ons uncolored, like auto_on_brightness.
        self._auto_on_color = _opt_color(
            cfg, CONF_AUTO_ON_COLOR_TEMP, CONF_AUTO_ON_RGB_COLOR
        )
        # Level an expired timer drops to instead of off; None disables
        # standby. Only a scheduled light's inside settings can set it.
        standby_pct = cfg.get(CONF_STANDBY_BRIGHTNESS)
        self._standby_brightness = (
            round(percentage_to_ranged_value((1, 255), int(standby_pct)))
            if standby_pct
            else None
        )
        self._standby_color = _opt_color(
            cfg, CONF_STANDBY_COLOR_TEMP, CONF_STANDBY_RGB_COLOR
        )
        self._turn_on_select_entity = cfg.get(CONF_TURN_ON_SELECT_ENTITY)
        self._turn_on_select_option = cfg.get(CONF_TURN_ON_SELECT_OPTION)
        self._turn_on_select_source_entity = cfg.get(CONF_TURN_ON_SELECT_SOURCE_ENTITY)

        # Effect/warn warning sequence run at auto-off instead of an immediate
        # off. Timeouts of 0 disable each stage; effect_brightness is a 0-255
        # value (0 = blink fully off); warn_brightness is None to keep whatever
        # brightness the light had before the warning began.
        self._effect_timeout = int(cfg.get(CONF_EFFECT_TIMEOUT, DEFAULT_EFFECT_TIMEOUT))
        self._effect_brightness = round(
            percentage_to_ranged_value(
                (1, 255),
                int(cfg.get(CONF_EFFECT_BRIGHTNESS, DEFAULT_EFFECT_BRIGHTNESS)),
            )
        )
        self._warn_timeout = int(cfg.get(CONF_WARN_TIMEOUT, DEFAULT_WARN_TIMEOUT))
        warn_pct = cfg.get(CONF_WARN_BRIGHTNESS)
        self._warn_brightness = (
            round(percentage_to_ranged_value((1, 255), int(warn_pct)))
            if warn_pct
            else None
        )
        # Optional stage colors, as turn-on service data. None sends no color:
        # the effect stage then only changes brightness, and the warn stage
        # keeps (or, after a colored effect stage, restores) the pre-warning
        # color.
        self._effect_color = _opt_color(
            cfg, CONF_EFFECT_COLOR_TEMP, CONF_EFFECT_RGB_COLOR
        )
        self._warn_color = _opt_color(cfg, CONF_WARN_COLOR_TEMP, CONF_WARN_RGB_COLOR)
        # Optional fade times (seconds) for the service calls this light makes
        # itself: automatic turn-ons/offs and the effect/warn stage changes.
        # None (absent or 0) sends no transition attribute. Manual/physical
        # turn-ons and a manual off are never given a transition.
        self._auto_on_transition = _opt_transition(cfg.get(CONF_AUTO_ON_TRANSITION))
        self._auto_off_transition = _opt_transition(cfg.get(CONF_AUTO_OFF_TRANSITION))
        self._effect_transition = _opt_transition(cfg.get(CONF_EFFECT_TRANSITION))
        self._warn_transition = _opt_transition(cfg.get(CONF_WARN_TRANSITION))

        # Sensor wiring. The schedule fields only ever come from a regular
        # Virtual Light's config; a Virtual Scheduled Light's settings forms
        # omit them (its schedule selects settings rather than gating them).
        self._occupancy_entity = cfg.get(CONF_OCCUPANCY_ENTITY)
        self._maintain_entity = cfg.get(CONF_MAINTAIN_OCCUPANCY_ENTITY)
        self._illuminance_entity = cfg.get(CONF_ILLUMINANCE_ENTITY)
        self._illuminance_mode = cfg.get(
            CONF_ILLUMINANCE_MODE, DEFAULT_ILLUMINANCE_MODE
        )
        self._schedule_entity = cfg.get(CONF_SCHEDULE_ENTITY)
        self._schedule_mode = cfg.get(CONF_SCHEDULE_MODE, DEFAULT_SCHEDULE_MODE)
        self._door_entity = cfg.get(CONF_DOOR_ENTITY)
        self._door_mode = cfg.get(CONF_DOOR_MODE, DEFAULT_DOOR_MODE)
        self._hold_entities = cfg.get(CONF_HOLD_ENTITIES, [])

    # ------------------------------------------------------------------
    # HA lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Defer state-change subscriptions until HA has fully started.

        During startup, entities are restored and integrations initialize in
        arbitrary order, firing spurious state-change events; reacting to them
        could toggle lights or start countdowns based on incomplete state.
        """
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            if self._is_scheduled_light:
                # A restored choice only belongs to the currently configured
                # schedule; a reload that switched schedules discards it here
                # so no consumer can act on the stale value.
                if (
                    last.attributes.get(ATTR_ACTIVE_SETTINGS_SCHEDULE)
                    == self._settings_schedule_entity
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
                # A boundary off deferred under a previous configuration no
                # longer applies once the end action is anything but "turn off".
                # It deliberately survives a schedule swap, unlike the
                # same-schedule-guarded restores above: the off was already
                # observed, not inferred, so the new schedule can't un-happen
                # it.
                self._schedule_end_off_pending = (
                    self._schedule_end_action == SCHEDULE_END_ACTION_TURN_OFF
                    and bool(last.attributes.get(ATTR_SCHEDULE_END_OFF_PENDING))
                )
            # Restore turn-on attribution: a manual off only stands until a
            # later turn-on.
            for source in ("physical", "virtual", "occupancy", "illuminance", "door"):
                raw = last.attributes.get(f"last_on_{source}")
                if raw:
                    with contextlib.suppress(ValueError, TypeError):
                        setattr(self, f"_last_on_{source}", datetime.fromisoformat(raw))
            self._schedule_window_applied = last.attributes.get("schedule_window_start")
            self._bright_forced_off = bool(last.attributes.get("bright_forced_off"))
            if self._bright_forced_off:
                with contextlib.suppress(ValueError, TypeError):
                    self._bright_resume_until = datetime.fromisoformat(
                        last.attributes.get("bright_resume_until")
                    )
            with contextlib.suppress(ValueError, TypeError):
                self._last_manual_off = datetime.fromisoformat(
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
            # _seed_state re-derives capabilities and legalises the mode, so
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
            # Non-null only when the last state was written mid effect/warn;
            # _seed_state then undoes the interrupted warning stage.
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
            # Pre-warning_active states can only be judged the old way.
            raw_active = last.attributes.get("warning_active")
            self._warning_active = (
                bool(raw_active)
                if raw_active is not None
                else (
                    self._pre_warn_brightness is not None
                    or self._pre_warn_color is not None
                )
            )

        watch = list(self._lights)
        if self._settings_schedule_entity:
            watch.append(self._settings_schedule_entity)
        for settings in self._settings_sets:
            watch.extend(
                entity_id
                for key in _WATCHED_REFERENCE_KEYS
                if (entity_id := settings.get(key))
            )
            watch.extend(settings.get(CONF_HOLD_ENTITIES, []))
        # One entity may serve several roles; subscribe to it only once.
        watch = list(dict.fromkeys(watch))

        unsub_start: CALLBACK_TYPE | None = None

        @callback
        def _subscribe(_event: Event | None = None) -> None:
            # When fired via async_listen_once, HA has already removed this
            # one-time listener; drop our reference so teardown doesn't try to
            # remove it a second time (that logs "unknown job listener").
            nonlocal unsub_start
            unsub_start = None
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, watch, self._handle_state_change
                )
            )
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_AUTO_OFF_TOGGLED.format(self._entry_id),
                    self._on_auto_off_toggled,
                )
            )
            self._select_initial_settings()
            self._seed_state()

        if self.hass.state is CoreState.running:
            _subscribe()
        else:
            unsub_start = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, _subscribe
            )

            @callback
            def _cancel_start() -> None:
                # async_listen_once' remove callback is not idempotent: it
                # auto-removes on fire, so only unsubscribe if it hasn't fired.
                if unsub_start is not None:
                    unsub_start()

            self.async_on_remove(_cancel_start)

    def _select_initial_settings(self) -> None:
        """Choose a scheduled light's settings once startup state is stable."""
        if not self._is_scheduled_light:
            return
        schedule = (
            self.hass.states.get(self._settings_schedule_entity)
            if self._settings_schedule_entity
            else None
        )
        if not self._settings_schedule_entity:
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
            # and a resting standby belonged to the previous window.
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
            # _seed_state applies its selected policy once after restoring the
            # physical/hold state. A reload that changed schedule entity has
            # crossed no boundary of the newly configured schedule.
            if self._schedule_end_action == SCHEDULE_END_ACTION_TURN_OFF:
                self._schedule_end_off_pending = True
            elif self._schedule_end_action == SCHEDULE_END_ACTION_SWITCH:
                self._schedule_end_switch_pending = True
        if not self._settings_schedule_entity:
            _LOGGER.warning(
                "Virtual Scheduled Light %s has no schedule; "
                "using outside-schedule settings",
                self._attr_name,
            )
        self._inside_schedule = inside
        self._apply_light_settings(
            self._inside_schedule_settings
            if inside
            else self._outside_schedule_settings
        )

    def _settings_window(self) -> str | None:
        """Window marker of the settings schedule, the last known if unreadable."""
        state = self.hass.states.get(self._settings_schedule_entity or "")
        if state is not None and state.state in ("on", "off"):
            self._settings_window_start = state.attributes.get("current_window_start")
        return self._settings_window_start

    def _switch_scheduled_settings(self, inside: bool) -> None:
        """Select and reconcile a Virtual Scheduled Light settings mapping."""
        if not self._is_scheduled_light or inside == self._inside_schedule:
            return
        leaving_inside = self._inside_schedule and not inside
        # A manual off only cancels standby until the next boundary.
        self._standby_suppressed = False
        self._manual_off_cleared = True
        if inside:
            # A held end boundary no longer applies once the same schedule
            # window becomes active again.
            self._schedule_end_off_pending = False
            self._schedule_end_switch_pending = False
        self._inside_schedule = inside
        prev_door_entity = self._door_entity
        prev_hold_states = self._hold_states
        prev_illuminance_entity = self._illuminance_entity
        prev_occupancy_entity = self._occupancy_entity
        self._apply_light_settings(
            self._inside_schedule_settings
            if inside
            else self._outside_schedule_settings
        )

        # Seed stateful inputs from their current values. Inactive settings
        # entities remain subscribed but are ignored by _handle_state_change.
        # An entity carried over from the other side keeps its cached value
        # while it reads unavailable/unknown: a blip at the boundary must not
        # read as "closed" or "hold released", the same rule
        # _handle_state_change applies mid-run.
        door = self.hass.states.get(self._door_entity) if self._door_entity else None
        if not (
            door is not None
            and door.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            and self._door_entity == prev_door_entity
        ):
            self._door_open = door is not None and door.state == "on"
            self._door_seen = door is not None and door.state in ("on", "off")
        self._hold_states = {}
        for entity_id in self._hold_entities:
            state = self.hass.states.get(entity_id)
            if (
                state is not None
                and state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                and entity_id in prev_hold_states
            ):
                self._hold_states[entity_id] = prev_hold_states[entity_id]
            else:
                self._hold_states[entity_id] = state is not None and state.state == "on"
        if not (
            self._illuminance_entity == prev_illuminance_entity
            and (s := self.hass.states.get(self._illuminance_entity or "")) is not None
            and s.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            self._illuminance_last_bright = self._live_illuminance_bright()
        if not (
            self._occupancy_entity == prev_occupancy_entity
            and (s := self.hass.states.get(self._occupancy_entity or "")) is not None
            and s.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            self._occupancy_last_on = self._live_occupancy_on()
        old_held = self._held
        self._held = self._compute_held()
        # A turn-on still waiting for its selection is judged as on: the off
        # below overtakes it, and the new profile's rules take it over.
        lit = self._is_lit()

        if (
            leaving_inside
            and self._schedule_end_action == SCHEDULE_END_ACTION_TURN_OFF
            and lit
        ):
            # An already-off light has nothing to turn off: it takes the
            # normal off-light path below so the outside profile's active
            # sensors are checked exactly as with the keep action. After a
            # forced off the room stays dark until the next sensor edge.
            if not self._held:
                self._finish_schedule_end_off()
                return
            self._schedule_end_off_pending = True
            self._cancel_timer()
            if self._in_warning():
                self._machine_state = STATE_ACTIVE
                self._resume_lights()
            elif self._machine_state == STATE_STANDBY:
                self._machine_state = STATE_ACTIVE
            self.async_write_ha_state()
            return

        if (
            leaving_inside
            and self._schedule_end_action == SCHEDULE_END_ACTION_SWITCH
            and lit
        ):
            # The outside profile is now fully active. Recalculate an on
            # light's state and deadline from its sensors/history instead of
            # preserving the timer that belonged to the inside profile.
            self._switch_running_state()
            return

        if self._machine_state == STATE_STANDBY and not self._standby_applies():
            # Standby has no end of its own: the new settings' timeout ends it.
            self._machine_state = STATE_ACTIVE
            self._standby_timeout_started = datetime.now(UTC)
            self._start_timer()

        if not lit:
            self._start_settings_for_off_light()
            return

        if self._held and not old_held:
            self._cancel_timer()
            if self._in_warning():
                self._machine_state = STATE_ACTIVE
                self._resume_lights()
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
            self._go_idle(bright_forced=True)
            return

        if self._occupancy_holds() or self._maintain_active() or self._door_holds():
            self._adopt_active_occupancy()
        elif self._machine_state == STATE_OCCUPIED:
            # The previous settings held the light indefinitely; the new ones
            # do not, so begin their normal timeout now.
            self._machine_state = STATE_COUNTDOWN
            self._start_timer()
        self.async_write_ha_state()

    def _start_settings_for_off_light(self) -> None:
        """Apply a settings boundary to a light that is off."""
        dark = not self._is_illuminance_bright()
        if dark and self._occupancy_active():
            self._on_occupancy_change(occupied=True)
        elif dark and self._door_holds():
            # Not _door_open: in plain open mode the door is momentary,
            # and a profile switch is not an opening.
            self._on_door_change(True)
        elif self._can_rest_at_standby():
            self._enter_standby(self._auto_on_transition, selection=True)
        else:
            self.async_write_ha_state()

    def _on_new_settings_window(self) -> None:
        """Start a window that touches the last one, as the schedule stays on.

        It is a boundary like any other, without leaving the inside settings.
        """
        if not self._inside_schedule:
            return
        # A manual off only cancels standby until the next boundary.
        self._standby_suppressed = False
        self._manual_off_cleared = True
        if self._is_lit():
            self.async_write_ha_state()
        else:
            self._start_settings_for_off_light()

    def _seed_state(self) -> None:
        """Initialise the machine state from current entity states after startup."""
        # Derive color capabilities from the members before anything mirrors
        # a color (mirroring is a no-op outside the supported modes). Also
        # legalises a restored color_mode the members no longer support.
        self._update_capabilities()

        # Unavailable/unknown/missing keep-on entities count as not holding.
        self._hold_states = {
            e: (s := self.hass.states.get(e)) is not None and s.state == "on"
            for e in self._hold_entities
        }
        self._held = self._compute_held()

        # An unavailable/unknown/missing door counts as closed at startup.
        if self._door_entity:
            door = self.hass.states.get(self._door_entity)
            self._door_open = door is not None and door.state == "on"
            self._door_seen = door is not None and door.state in ("on", "off")

        self._illuminance_last_bright = self._live_illuminance_bright()
        self._schedule_last_on = self._live_schedule_on()
        self._occupancy_last_on = self._live_occupancy_on()

        # Brightness 0 counts as off, matching _all_lights_off.
        self._attr_is_on = any(
            (s := self.hass.states.get(e)) is not None
            and s.state == "on"
            and s.attributes.get("brightness") != 0
            for e in self._lights
        )
        self._members_seen = {
            e
            for e in self._lights
            if (s := self.hass.states.get(e)) is not None and s.state in ("on", "off")
        }

        # Match the physical brightness and color at startup too, overriding
        # the values restored from our own last state, so the virtual light
        # always tracks the real lights rather than stale restored figures.
        if self._attr_is_on and (brightness := self._physical_brightness()):
            self._attr_brightness = brightness
        if self._attr_is_on and (color := self._physical_color()):
            self._set_color_state(*color)

        if self._schedule_end_off_pending:
            if not self._attr_is_on:
                self._schedule_end_off_pending = False
            elif self._held:
                if self._warning_active:
                    self._resume_lights()
                # Report the same state the normal seed would: a hold that
                # would otherwise adopt the light keeps it OCCUPIED until the
                # pending boundary applies on release.
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
                    self._resume_lights()
                self._switch_running_state()
                return

        if self._warning_active:
            if self._attr_is_on:
                # The restart landed mid effect/warn with the lights still on:
                # undo the warning stage like any other re-trigger, then seed
                # normally (the warn-stage brightness/color must not be
                # adopted).
                self._resume_lights()
            else:
                # The lights ended up off (e.g. mid blink-off): treat the
                # auto-off as having completed; the room is not re-lit.
                self._warning_active = False
                self._pre_warn_brightness = None
                self._pre_warn_color = None

        if self._standby_seed() or self._follow_schedule_seed():
            return

        # Occupancy only takes over when it's dark (or no illuminance is
        # configured) and inside any gate-mode schedule window.
        if (
            self._occupancy_entity
            and not self._is_illuminance_bright()
            and not self._gate_schedule_inactive()
        ):
            occ_state = self.hass.states.get(self._occupancy_entity)
            if (
                occ_state
                and occ_state.state == "on"
                and not self._may_replay_manual_off(occ_state, observed=False)
            ):
                self._on_occupancy_change(occupied=True)
                return

        if self._attr_is_on and (self._maintain_active() or self._door_holds()):
            # Adopt an already-on light as maintained without gating, since this
            # is not a turn-on; the maintain entity clearing, or the door
            # closing, starts the countdown as usual.
            self._machine_state = STATE_OCCUPIED
            self.async_write_ha_state()
            return

        if self._attr_is_on:
            # Lights are already on (whatever the illuminance), so adopt them
            # and run the normal timer so they still turn off eventually.
            self._machine_state = STATE_ACTIVE
            self._start_timer()

        self.async_write_ha_state()

    def _standby_seed(self) -> bool:
        """Apply standby at startup. Returns True if handled.

        A light that was resting at standby goes back there, re-sent so an
        edited standby level applies; any other lit light is adopted as usual
        and its timer ends at standby. An off light comes on at standby
        unless occupancy or a held-open door lights it at the auto-on level.
        """
        if not self._standby_applies():
            return False
        if self._attr_is_on:
            if not self._restored_standby:
                return False
            if not self._can_rest_at_standby() and not self._held:
                # Bright in control mode: standby waits for darkness.
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle(bright_forced=True)
            elif self._occupancy_holds() or (
                self._door_holds() and not self._is_illuminance_bright()
            ):
                # Presence arrived while Home Assistant was down: boost,
                # gated by brightness like any other rise.
                self._machine_state = STATE_STANDBY
                self._adopt_active_occupancy()
            else:
                self._enter_standby()
            return True
        if not self._can_rest_at_standby():
            return False
        if not self._is_illuminance_bright():
            if self._occupancy_active():
                return False  # the normal seed raises it
            if self._door_holds():
                # No opening edge will come from a door that is already open.
                self._on_door_change(True)
                return True
        self._enter_standby(self._auto_on_transition, selection=True)
        return True

    def _follow_schedule_seed(self) -> bool:
        """Apply follow-mode schedule state at startup. Returns True if handled.

        The stored window marker distinguishes a boundary missed while HA was
        down (apply it now) from one we already handled before the restart
        (leave the lights alone; if they're off, the user turned them off).
        """
        if not self._schedule_entity or self._schedule_mode != SCHEDULE_MODE_FOLLOW:
            return False
        sched = self.hass.states.get(self._schedule_entity)
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
            if marker and marker != self._schedule_window_applied:
                # Window started while HA was down: catch up now.
                self._apply_window_start(marker)
            elif self._attr_is_on:
                # Already handled this window before the restart; adopt.
                self._machine_state = STATE_SCHEDULED
                self.async_write_ha_state()
            else:
                # Already handled, lights off → manual override; respect it.
                self.async_write_ha_state()
            return True

        if self._schedule_window_applied is not None:
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
                self.hass.async_create_task(self._auto_lights_off())
            self.async_write_ha_state()
            return True

        return False

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the countdown timer and any waiting turn-on on removal."""
        await super().async_will_remove_from_hass()
        self._cancel_timer()
        # A turn-on still waiting for its selection must not light the room
        # for an entity that is gone.
        self._command_generation += 1

    # ------------------------------------------------------------------
    # LightEntity API
    # ------------------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on all real lights and transition the state machine.

        A caller-supplied transition is forwarded; auto_on_transition is not
        (this is the manual path). A turn-on that also restores the pre-warning
        brightness/color restores it over that same fade.
        """
        now = datetime.now(UTC)
        self._last_on_virtual = now
        self._occupancy_lit_lights = False  # the user owns this on-period now
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        # HA has already narrowed any color request to our advertised modes,
        # so only the canonical hs/color-temp attributes can arrive here.
        color: dict | None = {
            k: kwargs[k] for k in (ATTR_HS_COLOR, ATTR_COLOR_TEMP_KELVIN) if k in kwargs
        } or None
        if color is not None:
            self._last_color_change_virtual = now
        if brightness is not None:
            self._last_brightness_change_virtual = now
            self._attr_brightness = brightness
        elif self._in_warning():
            # No explicit brightness: restore the pre-warning brightness so
            # the effect/warn stage leaves no trace, like any other re-trigger.
            brightness = self._pre_warn_brightness
            if brightness is not None:
                self._attr_brightness = brightness
        if color is None and self._in_warning():
            # Same for the color: a colored stage must leave no trace either.
            color = self._pre_warn_color
        if await self._set_lights(
            True,
            brightness=brightness,
            transition=kwargs.get(ATTR_TRANSITION),
            color=color,
            apply_turn_on_selection=True,
            manual=True,
        ):
            self._transition_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off all real lights and go idle.

        No configured fade, but a caller-supplied transition is forwarded.
        """
        await self._set_lights(False, transition=kwargs.get(ATTR_TRANSITION))
        # Also ends an on-period that brightness had cut short.
        self._clear_bright_forced_off()
        self._go_idle(manual=True)

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """React to a tracked entity changing state."""
        entity_id: str = event.data["entity_id"]
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        same_state = old_state is not None and old_state.state == new_state.state

        if entity_id in self._lights:
            member_seen = entity_id in self._members_seen
            if new_state.state in ("on", "off"):
                self._members_seen.add(entity_id)
            # Capabilities can appear late (members unavailable at startup):
            # re-derive on every member event, before the echo check; our own
            # service calls still surface a member's first real state.
            self._update_capabilities()
            member_recovered = old_state is None or old_state.state in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            )
            # A first sighting, or the placeholder HA writes for a registered
            # entity at boot, is startup adoption, not a member reboot. The
            # same placeholder written after the member was seen is its
            # integration reloading, which is one.
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
            if self._is_own_echo(
                entity_id,
                old_state,
                new_state,
                own_context=event.context.id in self._self_context_ids,
            ):
                # A member back at the level an unanswered command asked for
                # may still have lost its selection: re-send like any other.
                if resend and self._machine_state in (STATE_STANDBY, STATE_SCHEDULED):
                    self._reconcile_recovered_member()
                return  # echo of our own service call; call sites manage state
            # A member our own select call changed was not changed at the wall.
            claim = event.context.id not in self._selecting_context_ids
            if same_state:
                if new_state.state == "on":
                    self._on_light_attrs_change(old_state, new_state, claim=claim)
                return
            if resend and self._reconcile_recovered_member():
                return
            if (
                member_recovered
                and new_state.state == "on"
                and new_state.attributes.get("brightness") != 0
                and self._machine_state != STATE_IDLE
            ):
                # A member reappearing (first sighting, or recovery from
                # unavailable) while the virtual light is already on is not
                # human activity: mirror its brightness/color but leave the
                # running timer, countdown, or warning sequence untouched;
                # a bulb that blips off the mesh mid-countdown must not win
                # itself a fresh full timer.
                if brightness := new_state.attributes.get("brightness"):
                    self._attr_brightness = brightness
                if color := self._member_color(new_state):
                    self._set_color_state(*color)
                self.async_write_ha_state()
                return
            if new_state.state == "on" and (color := self._member_color(new_state)):
                # Mirror the real light's color on the off→on adoption edge,
                # like the brightness mirror in _on_light_state_change.
                self._set_color_state(*color)
            self._on_light_state_change(
                new_state.state,
                new_state.attributes.get("brightness"),
                # A member reporting in while the light is off was not turned off.
                manual=not (member_recovered and self._machine_state == STATE_IDLE),
                claim=claim,
            )
            return
        if same_state:
            # Attribute-only change (battery, ...). Touching windows are the
            # exception: the schedule stays on while a new window starts.
            if self._is_new_follow_window(entity_id, old_state, new_state):
                self._on_schedule_change(new_state)
            if entity_id == self._settings_schedule_entity and _window_moved(
                old_state, new_state
            ):
                self._on_new_settings_window()
            return
        # A recovery from unavailable/unknown (or a first sighting) that
        # matches the last known value is a replay, not an observed edge.
        # Every role but maintain and keep-on acts on edges, so they skip
        # such replays: a standing-open door's sensor blip must not fire a
        # fresh "opening".
        recovered = old_state is None or old_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        )
        # One entity may serve several roles (e.g. as both the occupancy and
        # the maintain entity), so the role checks are independent, not
        # exclusive. A scheduled light's schedule switches settings first, so
        # any further role it plays is judged against the newly active side
        # (whose seeding in _switch_scheduled_settings already read it).
        if entity_id == self._settings_schedule_entity and new_state.state in (
            "on",
            "off",
        ):
            self._switch_scheduled_settings(new_state.state == "on")
        # A keep-on entity engages before its other roles act, so none of them
        # issues an automatic off it holds; a release is applied after them.
        holding = entity_id in self._hold_entities and new_state.state == "on"
        if holding:
            self._hold_states[entity_id] = True
            self._refresh_hold()
        if entity_id == self._occupancy_entity:
            occupied = new_state.state == "on"
            replay = recovered and occupied == self._occupancy_last_on
            # A first sighting since startup is not an observed edge either.
            observed = not (recovered and self._occupancy_last_on is None)
            self._occupancy_last_on = occupied
            if not replay and not (
                occupied and self._may_replay_manual_off(new_state, observed=observed)
            ):
                self._on_occupancy_change(occupied)
            elif (
                self._attr_is_on
                and self._machine_state not in (STATE_OCCUPIED, STATE_SCHEDULED)
                and self._occupancy_holds()
            ):
                # Lights turned on during the outage could not be adopted.
                self._adopt_active_occupancy()
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
        if entity_id == self._maintain_entity:
            self._on_maintain_change(new_state.state == "on")
        if entity_id == self._illuminance_entity:
            bright = new_state.state == "on"
            level_changed = (
                self._illuminance_last_bright is None
                or bright != self._illuminance_last_bright
            )
            self._illuminance_last_bright = bright
            if not recovered or level_changed:
                self._on_illuminance_change(bright)
        if entity_id == self._schedule_entity and new_state.state in ("on", "off"):
            schedule_on = new_state.state == "on"
            replay = recovered and schedule_on == self._schedule_last_on
            self._schedule_last_on = schedule_on
            self._on_schedule_change(
                new_state,
                replay_since=(old_state or new_state).last_changed if replay else None,
            )
        if entity_id == self._door_entity:
            door_was_open = self._door_open
            self._door_open = new_state.state == "on"
            # An open door first seen since startup may predate a manual off.
            first_open = self._door_open and recovered and not self._door_seen
            self._door_seen = True
            if (not recovered or self._door_open != door_was_open) and not (
                first_open and self._may_replay_manual_off(new_state, observed=False)
            ):
                self._on_door_change(self._door_open)
        if entity_id in self._hold_entities and not holding:
            self._hold_states[entity_id] = False
            self._refresh_hold()

    def _is_new_follow_window(
        self, entity_id: str, old_state: State, new_state: State
    ) -> bool:
        """Return True when a follow schedule that stays on moved its marker."""
        return (
            entity_id == self._schedule_entity
            and self._schedule_mode == SCHEDULE_MODE_FOLLOW
            and _window_moved(old_state, new_state)
        )

    def _on_light_state_change(
        self,
        state: str,
        brightness: int | None = None,
        *,
        manual: bool = True,
        claim: bool = True,
    ) -> None:
        """Handle a real light being turned on/off externally.

        claim is False for a turn-on our own select call caused.
        """
        if state == "on" and brightness == 0:
            # "On" at brightness 0 is an off in disguise, matching the dimming
            # path, _all_lights_off and the startup seed. A later 0 → non-zero
            # dim is then handled as the turn-on (in _on_light_attrs_change).
            if self._all_lights_off():
                self._go_idle(manual=manual)
            return
        if state == "on":
            # Mirror the real light's brightness so the virtual light always
            # matches it, including on this off→on adoption edge, not just on
            # later dims (which _on_light_brightness_change handles).
            if brightness:
                self._attr_brightness = brightness
            if claim:
                self._last_on_physical = datetime.now(UTC)
                self._claim_on_period()
            self._transition_on()
        elif self._all_lights_off():
            self._go_idle(manual=manual)

    def _on_light_attrs_change(
        self, old_state: State, new_state: State, *, claim: bool = True
    ) -> None:
        """Handle an external brightness/color change on an on real light.

        Dimming or recoloring is human activity: record it and restart any
        running countdown with the full timeout. Brightness 0 means off in
        disguise. claim is False for a change our own select call caused.
        """
        old_b = old_state.attributes.get("brightness")
        new_b = new_state.attributes.get("brightness")
        brightness_changed = new_b is not None and new_b != old_b

        new_color = self._member_color(new_state)
        color_changed = new_color is not None and new_color != self._member_color(
            old_state
        )
        if not brightness_changed and not color_changed:
            return  # some other attribute changed (battery, ...)

        now = datetime.now(UTC)
        if color_changed:
            self._last_color_change_physical = now
            self._set_color_state(*new_color)
        if brightness_changed:
            self._last_brightness_change_physical = now
            if new_b:
                self._attr_brightness = new_b

            if new_b == 0:
                if self._all_lights_off():
                    self._go_idle(manual=True)
                else:
                    self.async_write_ha_state()
                return

        if self._machine_state == STATE_IDLE:
            if brightness_changed:
                # 0 → non-zero while we're idle is a turn-on in disguise: run
                # the normal external turn-on logic (last_on_physical,
                # ACTIVE/timer or rejoining an active follow window). A
                # color-only change on a light we consider off is just
                # mirrored.
                self._on_light_state_change("on", claim=claim)
            self.async_write_ha_state()
            return

        if claim:
            self._claim_on_period()
        if self._machine_state in (
            STATE_ACTIVE,
            STATE_COUNTDOWN,
            STATE_EFFECT,
            STATE_WARN,
            STATE_STANDBY,
        ):
            # An external dim or recolor during the warning sequence is a
            # re-trigger like any other: honour the new brightness/color and
            # restart the full timer. Off standby it runs a timer that ends
            # back at standby.
            if self._in_warning():
                # The re-trigger brought only one of brightness and color:
                # restore the other to its pre-warning value so the stage
                # leaves no trace, exactly as a virtual re-trigger does.
                brightness = None if brightness_changed else self._pre_warn_brightness
                color = None if color_changed else self._pre_warn_color
                if brightness is not None or color is not None:
                    self.hass.async_create_task(
                        self._set_lights(True, brightness=brightness, color=color)
                    )
            self._warning_active = False
            self._pre_warn_brightness = None
            self._pre_warn_color = None
            if self._maintain_active() or self._occupancy_holds() or self._door_holds():
                # Presence that left standby alone holds the raised light,
                # as it does a raise through this entity.
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
            else:
                self._machine_state = STATE_ACTIVE
                self._start_timer()
        self.async_write_ha_state()

    def _claim_on_period(self) -> None:
        """Make the on-period the user's after a change at the wall.

        As a change made through this entity does: a false detection no
        longer cuts it short, a manual off's standby pause ends, and an
        automatic turn-on still waiting for its selection yields to it.
        """
        self._occupancy_lit_lights = False
        self._standby_suppressed = False
        self._auto_level_generation += 1

    def _all_lights_off(self) -> bool:
        """Return True when every real light is off (brightness 0 is off)."""
        for entity_id in self._lights:
            state = self.hass.states.get(entity_id)
            if state is None:
                # Registered but stateless: an integration that hasn't loaded
                # (yet); the bulb may still be burning, so don't assume off.
                # No registry entry either means the member is gone from HA
                # entirely and can never report again; counting such a ghost
                # as "maybe on" would pin the virtual light on forever.
                if er.async_get(self.hass).async_get(entity_id) is not None:
                    return False
                continue
            if state.state == "on" and state.attributes.get("brightness") != 0:
                return False
        return True

    def _physical_brightness(self) -> int | None:
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

        Advertises the canonical HS/COLOR_TEMP pair instead of the union of
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
            # A group of plugs can only do on/off, and advertising a slider
            # none of them can move would be a lie. Fail open as ever.
            supported = (
                {ColorMode.ONOFF}
                if lights_support_brightness(self.hass, self._lights) is False
                else {ColorMode.BRIGHTNESS}
            )

        changed = supported != self._attr_supported_color_modes
        # Most permissive envelope; each member clamps to its own range. Track
        # and reset it (to HA's defaults) so a member re-appearing with a
        # different range doesn't leave the virtual light advertising kelvins
        # no member can hit.
        envelope = (
            min(min_kelvins) if min_kelvins else DEFAULT_MIN_KELVIN,
            max(max_kelvins) if max_kelvins else DEFAULT_MAX_KELVIN,
        )
        if envelope != (
            self._attr_min_color_temp_kelvin,
            self._attr_max_color_temp_kelvin,
        ):
            self._attr_min_color_temp_kelvin = envelope[0]
            self._attr_max_color_temp_kelvin = envelope[1]
            changed = True

        # Fail open like the floor above: an unseen member must not cost the
        # caller their fade. Withheld only once every member is judged.
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
        """Adopt a commanded or mirrored color as this light's reported color.

        A no-op for modes we don't advertise, notably everything on a
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

    def _adopt_color_data(self, color: dict) -> None:
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
            kelvin := self._attr_color_temp_kelvin
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
    ) -> None:
        """Record what every member should report back for our own command."""
        expectation = _EchoExpectation(
            on,
            brightness if on else None,
            _service_color(color) if on else None,
            float(transition or 0),
            self.hass.loop.time(),
        )
        for expectations in self._echo_expectations.values():
            for earlier in expectations:
                if earlier.on != on:
                    earlier.overtaken = True
        plain = expectation.brightness is None and expectation.color is None
        for entity_id in self._lights:
            # A member already there has nothing to report, and the unanswered
            # expectation would pass a later human change off as its echo.
            if plain and self._member_is_lit(entity_id) is on:
                continue
            self._echo_expectations.setdefault(
                entity_id, deque(maxlen=ECHO_HISTORY)
            ).append(self._member_expectation(entity_id, expectation))

    def _member_expectation(
        self, entity_id: str, expectation: _EchoExpectation
    ) -> _EchoExpectation:
        """Narrow a kelvin command to the member's own range.

        The virtual light offers the union of its members' ranges; a member
        clamps a kelvin outside its own and reports the clamped value.
        """
        color = expectation.color
        if color is None or color[0] is not ColorMode.COLOR_TEMP:
            return expectation
        state = self.hass.states.get(entity_id)
        if state is None:
            return expectation
        kelvin = color[1]
        if low := state.attributes.get(ATTR_MIN_COLOR_TEMP_KELVIN):
            kelvin = max(kelvin, low)
        if high := state.attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN):
            kelvin = min(kelvin, high)
        if kelvin == color[1]:
            return expectation
        return replace(expectation, color=(ColorMode.COLOR_TEMP, kelvin))

    def _is_lit(self) -> bool:
        """On, or about to be: a turn-on may still be waiting for its selection."""
        return self._attr_is_on or self._machine_state != STATE_IDLE

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
    ) -> bool:
        """Judge a member write against our recent commands to it.

        While a command settles, only a write under our own context that is
        consistent with what we asked for is its echo (Home Assistant reuses
        that context for the member's reply). Later, a slow bulb's reply
        arrives under its own context, so a write fully matching the command
        still counts; anything else (stale, missing, contradicted) is a real
        change. A reply to an earlier command may arrive after a newer one
        was sent, so each command still awaiting its reply is tried, newest
        first. Once a newer command flipped power, the older one's reply only
        counts while it settles, or a manual flip back would pass as it.
        """
        now = self.hass.loop.time()
        waiting: list[_EchoExpectation] = []
        contradicted: list[_EchoExpectation] = []
        matched: _EchoExpectation | None = None
        pending = False
        for expectation in reversed(self._echo_expectations.get(entity_id, ())):
            age = now - expectation.issued
            if age > ECHO_LATE_SECONDS + expectation.transition:
                continue
            settling = age <= ECHO_SETTLE_SECONDS + expectation.transition
            if expectation.overtaken and not settling:
                contradicted.append(expectation)
                continue
            if settling and not own_context:
                waiting.append(expectation)
                continue
            verdict = expectation.judge(old_state, new_state, settling=settling)
            if verdict == "match":
                matched = expectation
                break  # answered, and with it every older command
            if verdict == "pending":
                pending = True
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
        return matched is not None or pending

    def _mirror_plain_echo(
        self, expectation: _EchoExpectation, waiting: list[_EchoExpectation]
    ) -> None:
        """Adopt what the members came on at when our command named no value.

        A value named by a command still awaiting its reply is left alone. A
        named color we could not report when it was sent (a late member
        brings its color modes with its reply) is adopted now.
        """
        if not expectation.on:
            return
        commands = [expectation, *waiting]
        brightness = color = None
        if all(command.brightness is None for command in commands):
            brightness = self._physical_brightness()
        if all(command.color is None for command in waiting) and (
            expectation.color is None or self._current_color() is None
        ):
            color = self._physical_color()
        if brightness is None and color is None:
            return
        if brightness is not None:
            self._attr_brightness = brightness
        if color is not None:
            self._set_color_state(*color)
        self.async_write_ha_state()

    def _physical_color(self) -> tuple[ColorMode, tuple] | None:
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
        if not self._illuminance_entity:
            return False
        state = self.hass.states.get(self._illuminance_entity)
        return state is not None and state.state == "on"

    def _gate_schedule_inactive(self) -> bool:
        """Return True when a gate-mode schedule forbids activating lights.

        gate_switch and gate_keep gate activation only: once the lights are
        on, occupancy and the door behave as inside the window (adopt, hold,
        re-hold). Their difference is whether the window-end boundary
        recalculates or preserves the running state/timer.
        """
        if not self._schedule_entity or self._schedule_mode not in (
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
        state = self.hass.states.get(self._schedule_entity)
        return not (state is not None and state.state == "on")

    def _hard_gate_schedule_inactive(self) -> bool:
        """Return True when a valid hard-gate state explicitly requires off."""
        if self._schedule_mode != SCHEDULE_MODE_GATE or not self._schedule_entity:
            return False
        state = self.hass.states.get(self._schedule_entity)
        return state is not None and state.state == "off"

    def _follow_schedule_state(self) -> State | None:
        """Return the schedule state when in follow mode and ON, else None."""
        if not self._schedule_entity or self._schedule_mode != SCHEDULE_MODE_FOLLOW:
            return None
        state = self.hass.states.get(self._schedule_entity)
        return state if state is not None and state.state == "on" else None

    # ------------------------------------------------------------------
    # Auto-off hold
    # ------------------------------------------------------------------

    def _auto_off_enabled(self) -> bool:
        """State of the companion Auto-off switch (mirrored via hass.data)."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        return entry_data.get(DATA_AUTO_OFF_ENABLED, True)

    def _compute_held(self) -> bool:
        return not self._auto_off_enabled() or any(self._hold_states.values())

    @callback
    def _on_auto_off_toggled(self) -> None:
        self._refresh_hold()

    def _refresh_hold(self) -> None:
        """Re-derive the effective hold and act on engage/release edges."""
        held = self._compute_held()
        if held == self._held:
            return
        self._held = held
        if held:
            # Suspend any pending automatic off; the machine state stays put.
            self._cancel_timer()
            if self._in_warning():
                # Auto-off just became held mid-warning: abort the sequence and
                # restore the light. Held, so ACTIVE arms no timer.
                self._machine_state = STATE_ACTIVE
                self._resume_lights()
        else:
            self._resume_after_hold_release()
        self.async_write_ha_state()

    def _resume_after_hold_release(self) -> None:
        """Return to normal behaviour when the last hold releases.

        Automatic turn-offs suppressed while held are applied from current
        conditions: a follow/hard-gate window that ended, or bright in control
        mode, turns the lights off now; an active follow
        window or active occupancy (gated like any adoption: suppressed when
        bright or outside a gate window) keeps them on without a timer;
        otherwise a fresh full timer starts.
        """
        if not self._attr_is_on:
            # Lights-off transitions were never suppressed.
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
            self.hass.states.get(self._schedule_entity)
            if self._schedule_entity
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
            self._schedule_entity
            and self._schedule_mode == SCHEDULE_MODE_FOLLOW
            and self._schedule_window_applied is not None
        ):
            # The follow window we turned on for ended while held.
            self._schedule_window_applied = None
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle()
            return

        if self._hard_gate_schedule_inactive():
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle()
            return
        resting = self._machine_state == STATE_STANDBY and self._standby_applies()
        if (
            self._is_illuminance_bright()
            and self._illuminance_mode == ILLUMINANCE_MODE_CONTROL
        ):
            if not (resting or self._maintain_active() or self._door_holds()):
                # Darkness resumes the fresh timer this release would start.
                self._start_timer()
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle(bright_forced=True)
            return
        if resting:
            return  # resting at standby has no timer to resume

        # Occupancy adoption is gated exactly like every other adoption path
        # (_occupancy_holds); the maintain entity and an open_close door are
        # never gated once the light is on.
        if self._occupancy_holds() or self._maintain_active() or self._door_holds():
            self._machine_state = STATE_OCCUPIED
            return

        self._machine_state = STATE_ACTIVE
        self._start_timer()

    # ------------------------------------------------------------------
    # Schedule handling
    # ------------------------------------------------------------------

    def _switch_running_state(self) -> None:
        """Reconcile an already-on light and replace its running deadline.

        Used by the explicit switch-state schedule policies. The applicable
        settings have already been selected. Active presence adopts the light;
        otherwise a configured occupancy/maintain sensor contributes its
        latest-occupied timestamp, with a fresh full timeout when no usable
        history exists. With no such sensor there is likewise no trustworthy
        departure anchor, so the new settings receive a fresh full timeout.
        A turn-on still waiting for its selection counts as on.
        """
        if not self._is_lit():
            return

        was_standby = self._machine_state == STATE_STANDBY
        was_warning = self._in_warning()
        self._cancel_timer()
        if was_warning:
            # Discard an outgoing profile's warning presentation before the
            # newly applicable settings decide whether/how auto-off is due.
            self._machine_state = STATE_ACTIVE
            self._resume_lights()

        if (
            self._is_illuminance_bright()
            and self._illuminance_mode == ILLUMINANCE_MODE_CONTROL
            and not self._held
        ):
            # Darkness resumes what these settings would have run.
            if self._maintain_active() or self._door_holds():
                self._machine_state = STATE_OCCUPIED
            else:
                self._start_timer(self._compute_occupancy_countdown())
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle(bright_forced=True)
            return

        if self._occupancy_holds() or self._maintain_active() or self._door_holds():
            self._machine_state = STATE_OCCUPIED
            self.async_write_ha_state()
            return

        has_presence_history = bool(self._occupancy_entity or self._maintain_entity)
        self._machine_state = STATE_COUNTDOWN if has_presence_history else STATE_ACTIVE
        duration = (
            self._compute_occupancy_countdown()
            if has_presence_history
            else self._light_timeout
        )
        if self._held:
            # The state switches now, while the usual hold policy suppresses
            # the newly calculated automatic off until the hold is released.
            self.async_write_ha_state()
        elif duration <= 0:
            # Auto-off is already due. The now-active effect/warn settings
            # decide whether this is immediate or enters a warning sequence.
            self._begin_warning()
        else:
            if was_standby and not self._occupancy_lots():
                # A fresh full timeout, not one that history accounts for.
                self._standby_timeout_started = datetime.now(UTC)
            self._start_timer(duration)
            self.async_write_ha_state()

    def _on_schedule_change(
        self, new_state: State, *, replay_since: datetime | None = None
    ) -> None:
        """Handle the virtual schedule sensor changing.

        replay_since is set when the schedule recovers to the value it had
        before an outage that began then: no boundary was crossed.
        """
        replay = replay_since is not None
        if self._schedule_mode == SCHEDULE_MODE_FOLLOW:
            if new_state.state == "on":
                marker = new_state.attributes.get("current_window_start")
                if (
                    marker == self._schedule_window_applied
                    if marker
                    else replay and self._schedule_window_applied is None
                ):
                    # Same window we already applied: the schedule entity
                    # blipped unavailable and recovered mid-window. A manual
                    # off in between stands, mirroring the restart seed.
                    if self._attr_is_on:
                        if self._in_warning():
                            # A timer that ran while the schedule was
                            # unavailable reached the warning; undo it, as the
                            # recovered window owns the lights again.
                            self._resume_lights()
                        self._machine_state = STATE_SCHEDULED
                        self._cancel_timer()
                        self.async_write_ha_state()
                    return
                self._apply_window_start(marker)
            elif replay:
                # Still outside the window: a manual on stands. A window that
                # ended under a hold and was turned off by hand during the
                # outage is over, now that the schedule reads off again.
                if not self._attr_is_on:
                    self._forget_ended_follow_window()
                    self.async_write_ha_state()
            elif self._held and self._attr_is_on:
                # Auto-off held: keep the window marker so releasing the
                # hold applies this off boundary.
                pass
            else:
                # Window ended: apply the off boundary.
                self._schedule_window_applied = None
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle()
            return

        # All gate modes block automatic activation outside the window and
        # re-evaluate occupancy/a held-open door when it starts. At the end,
        # the original hard gate forces off, gate_switch recalculates an on
        # light from current sensor history, and gate_keep leaves its current
        # state/timer alone.
        if new_state.state != "on":
            if replay:
                return  # still outside the window: nothing ended
            if (
                self._schedule_mode == SCHEDULE_MODE_GATE
                and self._machine_state != STATE_IDLE
                and not self._held
            ):
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle()
            elif self._schedule_mode == SCHEDULE_MODE_GATE_SWITCH and self._is_lit():
                self._switch_running_state()
        else:
            if self._machine_state != STATE_IDLE:
                # Lights already on: the window opening lifts the gate that
                # kept already-active occupancy (or an open door) from holding
                # them.
                if self._occupancy_holds() or self._door_holds():
                    self._adopt_active_occupancy()
                return
            if self._is_illuminance_bright():
                return  # dark-gated, like any automatic turn-on
            occ_state = (
                self.hass.states.get(self._occupancy_entity)
                if self._occupancy_entity
                else None
            )
            occ_active = occ_state is not None and occ_state.state == "on"
            door_holds = self._door_holds()
            if replay_since is not None:
                # Only a start the unreadable gate blocked is applied now; a
                # light turned off by hand in an occupied room stays off.
                replay_since = self._after_manual_off(replay_since)
                occ_active = occ_active and occ_state.last_changed >= replay_since
                door = self.hass.states.get(self._door_entity or "")
                door_holds = (
                    door_holds
                    and door is not None
                    and door.last_changed >= replay_since
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
                self.hass.async_create_task(self._auto_lights_on())
                self.async_write_ha_state()

    def _reconcile_recovered_member(self) -> bool:
        """Make a member back from unavailable match a follow schedule.

        A rebooted (or reloaded) member reports whatever it booted into, which
        is not human activity: the schedule decides on/off and its settings
        are re-sent. Returns False when there is no valid follow schedule to
        follow, or a hold would suppress the off. A light resting at standby
        is re-sent its standby settings the same way.
        """
        if self._machine_state == STATE_STANDBY:
            self._enter_standby(
                self._auto_on_transition, selection=True, force_selection=True
            )
            return True
        if self._schedule_mode != SCHEDULE_MODE_FOLLOW or not self._schedule_entity:
            return False
        sched = self.hass.states.get(self._schedule_entity)
        if sched is None or sched.state not in ("on", "off"):
            return False
        if sched.state == "off":
            if self._held and self._attr_is_on:
                return False
            if not self._all_lights_off():
                self.hass.async_create_task(self._auto_lights_off())
            if self._machine_state != STATE_IDLE:
                self._go_idle()
            return True
        self._schedule_window_applied = sched.attributes.get("current_window_start")
        self._machine_state = STATE_SCHEDULED
        self._cancel_timer()
        self._warning_active = False
        self._pre_warn_brightness = None
        self._pre_warn_color = None
        # The member may already be on (booted lit): re-send the settings and
        # the turn-on selection regardless.
        self.hass.async_create_task(self._auto_lights_on(force_selection=True))
        self.async_write_ha_state()
        return True

    def _forget_ended_follow_window(self) -> None:
        """Drop the marker of a window that ended, once the lights are off.

        A hold keeps the marker past the window end to turn the lights off on
        release; with the lights off nothing is left to apply, and a later
        on-period must not be taken for that window.
        """
        if (
            self._schedule_window_applied is None
            or self._schedule_mode != SCHEDULE_MODE_FOLLOW
            or not self._schedule_entity
        ):
            return
        state = self.hass.states.get(self._schedule_entity)
        if state is not None and state.state == "off":
            self._schedule_window_applied = None

    def _apply_window_start(self, marker: str | None) -> None:
        """Enter the SCHEDULED state and turn the lights on (follow mode)."""
        self._schedule_window_applied = marker
        was_warning = self._in_warning()
        self._machine_state = STATE_SCHEDULED
        self._cancel_timer()
        if was_warning:
            # The window takes over mid-warning: the virtual light is
            # logically on but the real lights are blinked off / dimmed by
            # the effect/warn stage; restore them for the window.
            self._resume_lights()
        elif not self._attr_is_on:
            self.hass.async_create_task(self._auto_lights_on())
        self.async_write_ha_state()

    def _on_occupancy_change(self, occupied: bool) -> None:
        """Handle the virtual occupancy sensor changing."""
        if self._machine_state == STATE_SCHEDULED:
            return  # follow-mode window owns the lights
        if occupied:
            if self._is_illuminance_bright():
                # Bright enough: suppress lights; when illuminance turns off we
                # re-evaluate occupancy from the sensor's current state.
                return
            if self._gate_schedule_inactive():
                # Outside the schedule window: occupancy may not turn lights
                # on; window start re-evaluates occupancy.
                return
            was_warning = self._in_warning()
            was_standby = self._machine_state == STATE_STANDBY
            self._last_on_occupancy = datetime.now(UTC)
            self._machine_state = STATE_OCCUPIED
            self._cancel_timer()
            if was_warning:
                # Occupancy returned mid-warning: undo the effect/warn stage so
                # the light looks exactly as it did while the timer was running.
                self._resume_lights()
            elif not self._attr_is_on or was_standby:
                # Lit from standby counts as lit by occupancy: a false
                # detection drops back to standby quickly.
                self._occupancy_lit_lights = True
                self.hass.async_create_task(self._auto_lights_on())
            self.async_write_ha_state()
        elif self._machine_state == STATE_OCCUPIED:
            if self._maintain_active() or self._door_holds():
                return  # maintain entity / open door holds the light on
            self._machine_state = STATE_COUNTDOWN
            if self._occupancy_lit_lights and self._occupancy_clear_was_false():
                # The whole cycle was a false detection and nobody else
                # asked for these lights, so turn them off quickly.
                self._start_timer(self._false_off_delay)
            else:
                self._start_timer(self._compute_clear_countdown())
            self.async_write_ha_state()

    def _on_maintain_change(self, maintained: bool) -> None:
        """Handle the maintain occupancy entity changing.

        Holds an already-on light on while occupied; never turns lights on.
        """
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
                    self._resume_lights()
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
                self.async_write_ha_state()
        else:
            if self._machine_state != STATE_OCCUPIED:
                return
            if self._occupancy_holds() or self._door_holds():
                return  # regular occupancy / open door still holds the light on
            self._machine_state = STATE_COUNTDOWN
            if (
                self._occupancy_lit_lights
                and self._occupancy_clear_was_false()
                and self._clear_was_false(self._maintain_entity)
            ):
                # Both sensors flagged their clears false: the whole episode
                # was a false detection; a genuine presence on either side
                # earns the normal countdown instead.
                self._start_timer(self._false_off_delay)
            else:
                self._start_timer(self._compute_clear_countdown())
            self.async_write_ha_state()

    def _occupancy_active(self) -> bool:
        """Return True when the regular occupancy entity is configured and on."""
        if not self._occupancy_entity:
            return False
        state = self.hass.states.get(self._occupancy_entity)
        return state is not None and state.state == "on"

    def _occupancy_holds(self) -> bool:
        """Return True when active occupancy may hold an on light as OCCUPIED.

        Adoption is gated exactly like a turn-on (bright or outside a
        gate-mode window suppress it), unlike the maintain entity, which is
        never gated. Without adoption, a light turned on while occupancy is
        already active would run a timer that expires despite presence, and
        the steady-on sensor produces no event that could ever rescue it.
        """
        return (
            self._occupancy_active()
            and not self._is_illuminance_bright()
            and not self._gate_schedule_inactive()
        )

    def _may_replay_manual_off(self, state: State, *, observed: bool) -> bool:
        """Return True when presence may be the visit a manual off ended.

        Only while that off stands: a visit the sensor dates after it may
        light the light, and one it cannot date only when seen to start live.
        """
        if self._is_lit() or not self._manual_off_stands():
            return False
        if "last_on_time" not in state.attributes:
            return not observed
        raw = state.attributes["last_on_time"]
        try:
            started = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return True
        return self._last_manual_off is None or started <= self._last_manual_off

    def _manual_off_stands(self) -> bool:
        """Return True when a manual off is the last thing the light did.

        Standby scopes it to the window; otherwise a recorded turn-on or a
        settings boundary since the off ends it.
        """
        if self._standby_brightness is not None:
            return self._standby_suppressed
        if self._last_manual_off is None or self._manual_off_cleared:
            return False
        return all(
            on is None or on <= self._last_manual_off
            for on in (
                self._last_on_physical,
                self._last_on_virtual,
                self._last_on_occupancy,
                self._last_on_illuminance,
                self._last_on_door,
            )
        )

    def _after_manual_off(self, since: datetime) -> datetime:
        """Push a deferred start's window past the user's last turn-off.

        A start that a gate or an unreadable sensor blocked is only applied
        on recovery when the user has not turned the light off since.
        """
        if self._last_manual_off is not None and self._last_manual_off > since:
            return self._last_manual_off
        return since

    def _gate_lifted_since(self, since: datetime) -> bool:
        """Return True when a turn-on gate opened at or after `since`.

        Illuminance going dark, a gate-mode window starting, or a scheduled
        light switching profiles each re-read occupancy live, and an
        unreadable sensor counted as clear then.
        """
        checks = [(self._illuminance_entity, ("off",))]
        if self._schedule_mode in (
            SCHEDULE_MODE_GATE,
            SCHEDULE_MODE_GATE_SWITCH,
            SCHEDULE_MODE_GATE_KEEP,
        ):
            checks.append((self._schedule_entity, ("on",)))
        checks.append((self._settings_schedule_entity, ("on", "off")))
        for entity_id, lifted_states in checks:
            state = self.hass.states.get(entity_id) if entity_id else None
            if (
                state is not None
                and state.state in lifted_states
                and state.last_changed >= since
            ):
                return True
        return False

    def _adopt_active_occupancy(self) -> None:
        """Move an on light to OCCUPIED when a gate lifts mid-on-period.

        Used when illuminance turns dark or a gate-mode window starts while
        the lights are already on (ACTIVE/COUNTDOWN/EFFECT/WARN) with
        occupancy active. Not a turn-on: attribution and the occupancy-lit
        flag are left untouched, so the user still owns a manual on-period.
        A light resting at standby is brought up to the auto-on level, which
        occupancy then owns like any raise from standby.
        """
        was_warning = self._in_warning()
        was_standby = self._machine_state == STATE_STANDBY
        self._machine_state = STATE_OCCUPIED
        self._cancel_timer()
        if was_warning:
            self._resume_lights()
        elif was_standby:
            self._occupancy_lit_lights = self._occupancy_active()
            self.hass.async_create_task(self._auto_lights_on())
        self.async_write_ha_state()

    def _maintain_active(self) -> bool:
        """Return True when the maintain occupancy entity is configured and on."""
        if not self._maintain_entity:
            return False
        state = self.hass.states.get(self._maintain_entity)
        return state is not None and state.state == "on"

    def _live_occupancy_on(self) -> bool | None:
        """Live on/off of the occupancy entity, None when unknown."""
        if not self._occupancy_entity:
            return None
        state = self.hass.states.get(self._occupancy_entity)
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def _live_schedule_on(self) -> bool | None:
        """Live on/off of the schedule entity, None when unknown."""
        if not self._schedule_entity:
            return None
        state = self.hass.states.get(self._schedule_entity)
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def _live_illuminance_bright(self) -> bool | None:
        """Live bright/dark of the illuminance entity, None when unknown."""
        if not self._illuminance_entity:
            return None
        state = self.hass.states.get(self._illuminance_entity)
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def _door_holds(self) -> bool:
        """Return True when an open_close-mode door is open (holds the light).

        Such a door holds an already-on light on with no timer, exactly like
        active occupancy or the maintain entity, and its closing starts the
        countdown. In plain open mode a door never holds (it is only a
        momentary turn-on trigger), so this is always False there. Reads the
        last known door state, so a sensor that blips unavailable keeps
        holding until it reports closed.
        """
        if not self._door_entity or self._door_mode != DOOR_MODE_OPEN_CLOSE:
            return False
        return self._door_open

    def _on_door_change(self, is_open: bool) -> None:
        """Handle the configured door sensor changing (on = open).

        Opening is a turn-on trigger, gated by illuminance/a gate-mode schedule
        exactly like occupancy. In open_close mode the open door then holds the
        lights on (OCCUPIED, no timer) until it closes, whereupon the countdown
        starts unless occupancy/a keep-on hold still applies; in open mode the
        door is a momentary trigger (ACTIVE, normal timeout) and closing is
        ignored.
        """
        if self._machine_state == STATE_SCHEDULED:
            return  # follow-mode window owns the lights
        if is_open:
            if self._is_illuminance_bright():
                return  # dark-gated, like occupancy
            if self._gate_schedule_inactive():
                return  # outside a gate-mode schedule window
            was_warning = self._in_warning()
            was_standby = self._machine_state == STATE_STANDBY
            self._last_on_door = datetime.now(UTC)
            # An open_close door holds the light (no timer) like the maintain
            # entity; an already-occupied/maintained room holds it too. Only a
            # plain open-mode trigger with no other hold runs the timeout.
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
                # Opening mid-warning is a re-trigger: undo the effect/warn
                # stage so the light looks as it did before the warning.
                self._resume_lights()
            elif not self._attr_is_on or was_standby:
                self._occupancy_lit_lights = False  # the door owns this period
                self.hass.async_create_task(self._auto_lights_on())
            if not holds:
                self._start_timer()
            self.async_write_ha_state()
        else:
            if self._door_mode != DOOR_MODE_OPEN_CLOSE:
                return  # open mode: closing is ignored
            if self._machine_state in (STATE_IDLE, STATE_STANDBY):
                return
            if self._occupancy_holds() or self._maintain_active():
                return  # presence still holds the lights on
            if self._in_warning():
                return  # already winding down toward off; let it finish
            # The door was the reason to be on and it just closed: start the
            # normal countdown toward off.
            self._machine_state = STATE_COUNTDOWN
            self._start_timer()
            self.async_write_ha_state()

    def _occupancy_clear_was_false(self) -> bool:
        """Return True when the occupancy sensor flagged a false detection."""
        return self._clear_was_false(self._occupancy_entity)

    def _clear_was_false(self, entity_id: str | None) -> bool:
        """Return True when the given sensor flagged a false-detection clear."""
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return bool(
            state is not None and state.attributes.get("last_clear_false_detection")
        )

    def _on_illuminance_change(self, is_bright: bool) -> None:
        """Handle the virtual illuminance sensor changing.

        is_bright=True  (illuminance ON  = bright): natural light is sufficient
                        → turn off artificial lights if they were on.
        is_bright=False (illuminance OFF = dark):   need artificial light
                        → turn on if currently occupied, or resume an
                          on-period that brightness cut short.
        """
        if self._machine_state == STATE_SCHEDULED:
            return  # follow-mode window owns the lights
        if is_bright:
            if self._illuminance_mode == ILLUMINANCE_MODE_GATE:
                # Gate-only: bright never forces the lights off (breaks the
                # feedback loop when the lux sensor sees the controlled
                # lights). Occupancy/timeout handle turning off.
                return
            if self._held:
                # Auto-off held: releasing the hold re-checks brightness.
                return
            if self._machine_state != STATE_IDLE:
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle(bright_forced=True)
        else:
            if self._machine_state != STATE_IDLE:
                # Lights already on: going dark lifts the gate that kept
                # already-active occupancy (or an open door) from holding
                # them; adopt so a timer can't expire despite presence.
                if self._occupancy_holds() or self._door_holds():
                    self._adopt_active_occupancy()
                return
            if self._gate_schedule_inactive():
                return  # outside the schedule window, so no activation

            # Check whether we should activate due to occupancy, a held-open
            # door, or recent history.
            occ_state = (
                self.hass.states.get(self._occupancy_entity)
                if self._occupancy_entity
                else None
            )
            occ_active = occ_state is not None and occ_state.state == "on"
            if occ_active or self._door_holds():
                self._last_on_illuminance = datetime.now(UTC)
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
                self._occupancy_lit_lights = occ_active
                self.hass.async_create_task(self._auto_lights_on())
                self.async_write_ha_state()
            elif (
                self._bright_forced_off
                and (countdown := self._compute_illuminance_countdown()) > 0
            ):
                # Resumed ahead of standby: its timer ends there anyway.
                self._last_on_illuminance = datetime.now(UTC)
                self.hass.async_create_task(self._auto_lights_on())
                if self._maintain_active():
                    # Recent history justified the turn-on; the maintain
                    # entity now holds the re-lit light.
                    self._machine_state = STATE_OCCUPIED
                    self._cancel_timer()
                else:
                    self._machine_state = STATE_COUNTDOWN
                    self._start_timer(countdown)
                self.async_write_ha_state()
            elif self._can_rest_at_standby():
                # Control mode kept standby off while bright.
                self._last_on_illuminance = datetime.now(UTC)
                self._enter_standby(self._auto_on_transition, selection=True)

    def _compute_occupancy_countdown(self) -> int:
        """Seconds to wait after occupancy clears before turning lights off.

        Anchors to the latest_occupied_time across the occupancy and maintain
        entities (whichever saw the person last) so that each sub-sensor's
        individual timeout is respected: the lights go off light_timeout
        seconds after the person actually left, i.e. at
        latest_occupied_time + light_timeout, which is why light_timeout must
        be >= the sensor's occupancy_timeout (the flows enforce it).
        """
        base = self._light_timeout
        if lots := self._occupancy_lots():
            now = datetime.now(UTC)
            remaining = (max(lots) - now).total_seconds()
            return max(0, int(base + remaining))
        return base

    def _compute_clear_countdown(self) -> int:
        """Seconds to wait once the last presence hold clears.

        A false detection leaves latest_occupied_time at an earlier visit,
        which must not cut short a light the user turned on since then: the
        countdown never ends before a full timeout after the latest manual,
        physical or door turn-on, a dim or recolor at the wall (which also
        restarts a running timer), or a schedule end's fresh timeout.
        """
        countdown = self._compute_occupancy_countdown()
        user_ons = [
            t
            for t in (
                self._last_on_physical,
                self._last_on_virtual,
                self._last_on_door,
                self._last_brightness_change_physical,
                self._last_color_change_physical,
                self._standby_timeout_started,
            )
            if t is not None
        ]
        if user_ons:
            elapsed = (datetime.now(UTC) - max(user_ons)).total_seconds()
            countdown = max(countdown, int(self._light_timeout - elapsed))
        return countdown

    def _occupancy_lots(self) -> list[datetime]:
        """latest_occupied_time across the occupancy and maintain entities."""
        lots: list[datetime] = []
        for entity_id in (self._occupancy_entity, self._maintain_entity):
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

    def _compute_illuminance_countdown(self) -> int:
        """Seconds left of the on-period that brightness cut short.

        It lasts until the later of the end recorded when brightness forced
        the light off and a timeout after the room was last occupied; neither
        means there is nothing to resume.
        """
        ends = [
            lot + timedelta(seconds=self._light_timeout)
            for lot in self._occupancy_lots()
        ]
        if self._bright_resume_until is not None:
            ends.append(self._bright_resume_until)
        if not ends:
            return 0
        return max(0, int((max(ends) - datetime.now(UTC)).total_seconds()))

    def _transition_on(self) -> None:
        """Move to ACTIVE (or stay OCCUPIED/SCHEDULED) when lights come on."""
        # A manual/physical turn-on ends any warning sequence; the caller has
        # already set the real lights, so just drop the restore snapshot.
        self._warning_active = False
        self._pre_warn_brightness = None
        self._pre_warn_color = None
        self._clear_bright_forced_off()
        # Turning the light back on after a manual off rejoins standby.
        self._standby_suppressed = False
        # Set before the hold checks: activation-only gate modes only gate turning
        # an off light on, so occupancy may hold this turn-on outside it.
        self._attr_is_on = True
        if self._machine_state in (STATE_OCCUPIED, STATE_SCHEDULED):
            # Already managed by occupancy / schedule window; publish the
            # rejoined standby, which a restart would otherwise lose.
            self.async_write_ha_state()
            return

        # Turned back on during an active follow-mode window (after a manual
        # off): rejoin the window instead of running the auto-off timer, so
        # the light stays on until the window ends.
        sched = self._follow_schedule_state()
        if sched is not None:
            self._apply_window_start(sched.attributes.get("current_window_start"))
            return

        # Turned on while the maintain entity, an open_close door, or the
        # regular occupancy entity (when not gated by bright/window) is
        # already holding presence: hold the light immediately instead of
        # running a timer that would expire despite presence.
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
        """Apply a scheduled light's end-boundary off now.

        Fades the lights off with the profile being left and goes idle, which
        also clears the pending flag.
        """
        self.hass.async_create_task(self._scheduled_end_lights_off())
        self._go_idle()

    def _go_idle(self, *, bright_forced: bool = False, manual: bool = False) -> None:
        """Cancel any timer and move to IDLE."""
        resume_until = None
        if bright_forced and not self._in_warning():
            # A countdown resumes until it would have ended; whoever held the
            # light was there until now, so it lasts a timeout at least.
            resume_until = self._timer_ends
            if resume_until is None and self._machine_state == STATE_OCCUPIED:
                resume_until = datetime.now(UTC) + timedelta(
                    seconds=self._light_timeout
                )
        self._cancel_timer()
        self._command_generation += 1
        if manual:
            self._last_manual_off = datetime.now(UTC)
            self._manual_off_cleared = False
            # Standby stays off until the next schedule boundary.
            self._standby_suppressed = self._standby_brightness is not None
        if self._machine_state != STATE_IDLE:
            # Why the on-period ended; an off while idle ends none.
            self._bright_forced_off = bright_forced
            self._bright_resume_until = resume_until
        self._machine_state = STATE_IDLE
        self._attr_is_on = False
        self._occupancy_lit_lights = False
        # A deferred schedule-end off only applies to the on-period it
        # interrupted; any off consumes it.
        self._schedule_end_off_pending = False
        self._warning_active = False
        self._pre_warn_brightness = None
        self._pre_warn_color = None
        self._forget_ended_follow_window()
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Timer helpers
    # ------------------------------------------------------------------

    def _start_timer(self, duration: int | None = None) -> None:
        self._cancel_timer()
        if self._held:
            # Auto-off held: the state machine transitions normally but no
            # timer is armed; releasing the hold starts a fresh one.
            return
        if duration is None:
            duration = self._light_timeout
        self._timer_ends = datetime.now(UTC) + timedelta(seconds=duration)
        self._timer_unsub = async_call_later(self.hass, duration, self._timer_expired)

    async def _timer_expired(self, _now: datetime) -> None:
        self._timer_unsub = None
        self._timer_ends = None
        if self._held:
            return  # engaged in the same loop iteration the timer fired
        state = self._machine_state
        if state in (STATE_ACTIVE, STATE_COUNTDOWN):
            # Normal auto-off: run the warning sequence, or turn straight off.
            self._begin_warning()
        elif state == STATE_EFFECT:
            self._enter_warn()
        elif state == STATE_WARN:
            self._finish_auto_off()
        # any other state: the machine moved on in the same loop iteration the
        # timer fired, so there is nothing to do.

    def _cancel_timer(self) -> None:
        if self._timer_unsub is not None:
            self._timer_unsub()
            self._timer_unsub = None
        self._timer_ends = None

    def _clear_bright_forced_off(self) -> None:
        """Forget an on-period that brightness cut short: it is over."""
        self._bright_forced_off = False
        self._bright_resume_until = None

    # ------------------------------------------------------------------
    # Effect / warn warning sequence
    # ------------------------------------------------------------------

    def _in_warning(self) -> bool:
        """Return True while showing the effect or warn stage before auto-off."""
        return self._machine_state in (STATE_EFFECT, STATE_WARN)

    def _begin_warning(self) -> None:
        """Auto-off is due: start the effect→warn warning sequence.

        Snapshots the current brightness and color (restored if the user
        re-triggers or reused by a warn stage with no brightness/color of its
        own). Falls through to the warn stage (and to a plain off) when the
        earlier stage is disabled, so both timeouts at 0 behaves exactly like
        the old immediate off.
        """
        self._warning_active = True
        self._pre_warn_brightness = self._attr_brightness
        self._pre_warn_color = self._current_color()
        # Recorded rather than re-derived in _enter_warn: a profile switch
        # mid-effect can swap _effect_color out from under the running stage.
        self._effect_sent_color = (
            self._effect_timeout > 0 and self._effect_color is not None
        )
        if self._effect_timeout > 0:
            self._machine_state = STATE_EFFECT
            self.hass.async_create_task(
                self._set_stage_lights(
                    self._effect_brightness,
                    self._effect_transition,
                    self._effect_color,
                )
            )
            self._start_timer(self._effect_timeout)
            self.async_write_ha_state()
            return
        self._enter_warn()

    def _enter_warn(self) -> None:
        """Advance to the WARN grace period, or turn off when it is disabled."""
        if self._warn_timeout > 0:
            # A warn stage without a color of its own undoes an effect-stage
            # recolor, mirroring how its brightness falls back to the
            # pre-warning brightness.
            effect_recolored = (
                self._machine_state == STATE_EFFECT and self._effect_sent_color
            )
            self._machine_state = STATE_WARN
            brightness = (
                self._warn_brightness
                if self._warn_brightness is not None
                else self._pre_warn_brightness
            ) or 255
            color = self._warn_color
            if color is None and effect_recolored:
                color = self._pre_warn_color
            self.hass.async_create_task(
                self._set_stage_lights(brightness, self._warn_transition, color)
            )
            self._start_timer(self._warn_timeout)
            self.async_write_ha_state()
            return
        self._finish_auto_off()

    def _finish_auto_off(self) -> None:
        """End an expired on-period: rest at standby, or turn off."""
        if self._can_rest_at_standby():
            self._enter_standby(self._auto_off_transition)
            return
        # Task + synchronous _go_idle (the idiom used everywhere else):
        # awaiting the service call first would let a re-trigger landing
        # mid-await be stomped back to IDLE when the await returns.
        self.hass.async_create_task(self._auto_lights_off())
        self._go_idle()

    def _standby_applies(self) -> bool:
        """Return True when the active settings rest at standby, not off."""
        return self._standby_brightness is not None and not self._standby_suppressed

    def _can_rest_at_standby(self) -> bool:
        """Return True when an off light should come on at standby now.

        Bright in illuminance control mode makes standby wait for darkness;
        gate mode only gates turn-ons above standby.
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
    ) -> None:
        """Rest at the standby level: lights on, no timer.

        Callers pass the fade: auto-on when coming on from off (with the
        turn-on selection), auto-off when dropping from a higher level.
        Without a standby color, a warning stage's recolor is undone.
        """
        color = self._standby_color
        if (
            color is None
            and self._in_warning()
            and self._pre_warn_color != self._current_color()
        ):
            color = self._pre_warn_color
        self._cancel_timer()
        self._machine_state = STATE_STANDBY
        self._warning_active = False
        self._pre_warn_brightness = None
        self._pre_warn_color = None
        self._occupancy_lit_lights = False
        self.hass.async_create_task(
            self._set_lights(
                True,
                brightness=self._standby_brightness,
                transition=transition,
                color=color,
                apply_turn_on_selection=selection,
                force_selection=force_selection,
                auto_level=True,
            )
        )
        self.async_write_ha_state()

    def _resume_lights(self) -> None:
        """Restore the pre-warning brightness and color after a re-trigger.

        The caller sets the resulting machine state. No transition: the
        restore must be as immediate as the re-trigger that caused it.
        """
        brightness = self._pre_warn_brightness
        color = self._pre_warn_color
        self._warning_active = False
        self._pre_warn_brightness = None
        self._pre_warn_color = None
        self.hass.async_create_task(
            self._set_lights(True, brightness=brightness, color=color)
        )

    async def _set_stage_lights(
        self,
        brightness: int,
        transition: float | None = None,
        color: dict | None = None,
    ) -> None:
        """Drive the real lights for an effect/warn stage.

        The virtual light stays logically on. Brightness 0 blinks the real
        lights off (any stage color is moot then).
        """
        context = Context()
        self._self_context_ids.append(context.id)
        self._expect_echo(bool(brightness), brightness or None, color, transition)
        transition_data = (
            {ATTR_TRANSITION: transition} if transition is not None else {}
        )
        if brightness:
            color_data = color or {}
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {
                    "entity_id": self._lights,
                    ATTR_BRIGHTNESS: brightness,
                    **color_data,
                    **transition_data,
                },
                blocking=False,
                context=context,
            )
            self._attr_brightness = brightness
            if color:
                self._adopt_color_data(color)
        else:
            await self.hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": self._lights, **transition_data},
                blocking=False,
                context=context,
            )
        self._attr_is_on = True
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Real-light control
    # ------------------------------------------------------------------

    def _auto_lights_on(
        self, *, force_selection: bool = False
    ) -> Coroutine[Any, Any, bool]:
        """Turn the real lights on for an automatic trigger.

        Applies the configured auto-on brightness, color and transition.
        """
        return self._set_lights(
            True,
            brightness=self._auto_on_brightness,
            transition=self._auto_on_transition,
            color=self._auto_on_color,
            apply_turn_on_selection=True,
            force_selection=force_selection,
            auto_level=True,
        )

    def _auto_lights_off(self) -> Coroutine[Any, Any, bool]:
        """Turn the real lights off for an automatic turn-off.

        Applies the configured auto-off transition. Manual offs bypass this.
        """
        return self._set_lights(False, transition=self._auto_off_transition)

    def _scheduled_end_lights_off(self) -> Coroutine[Any, Any, bool]:
        """Turn off at a scheduled-light boundary using the outgoing profile."""
        transition = _opt_transition(
            self._inside_schedule_settings.get(CONF_AUTO_OFF_TRANSITION)
        )
        return self._set_lights(False, transition=transition)

    async def _set_lights(
        self,
        on: bool,
        brightness: int | None = None,
        transition: float | None = None,
        color: dict | None = None,
        apply_turn_on_selection: bool = False,
        force_selection: bool = False,
        manual: bool = False,
        auto_level: bool = False,
    ) -> bool:
        """Command the real lights; False when a newer command overtook it.

        Only an off or a newer manual turn-on overtakes a turn-on waiting for
        its selection: a stage the timer reaches meanwhile is simply replaced,
        and an automatic turn-on issued while a manual one waits is dropped,
        as the user's command lights the room with the user's settings. An
        automatic turn-on at the auto-on or standby level (auto_level) also
        overtakes an older automatic one still waiting, so the level chosen
        last is the one the lights end at; so does a change at the wall.
        """
        # A blink-fully-off leaves the light logically on while the members
        # are dark, so this is still off-to-on for them.
        was_off = not self._attr_is_on or self._all_lights_off()
        if on:
            self._clear_bright_forced_off()
        context = Context()
        self._self_context_ids.append(context.id)
        if not on or manual:
            self._command_generation += 1
        generation = self._command_generation
        if on and auto_level:
            self._auto_level_generation += 1
        auto_level_generation = self._auto_level_generation
        if on and apply_turn_on_selection and (was_off or force_selection):
            if not manual and self._manual_on_pending:
                self._occupancy_lit_lights = False  # the user's on-period
                return False
            self._manual_on_pending += manual
            self._selecting_context_ids.add(context.id)
            try:
                await self._apply_turn_on_selection(context)
            finally:
                self._manual_on_pending -= manual
                self._selecting_context_ids.discard(context.id)
            if generation != self._command_generation or (
                not manual and auto_level_generation != self._auto_level_generation
            ):
                # An off, a newer manual command, a newer automatic level or
                # a change at the wall landed while the select call was
                # awaited; it stands.
                return False
        service_data: dict = {"entity_id": self._lights}
        if transition is not None:
            service_data[ATTR_TRANSITION] = transition
        if on and brightness is not None:
            service_data[ATTR_BRIGHTNESS] = brightness
            # Mirror the commanded brightness so the virtual light reports it
            # (the echo is only mirrored for a command that named none).
            self._attr_brightness = brightness
        if on and color:
            # One call carries the color to every member; HA filters/converts
            # it per real light, so mixed-capability members each show what
            # they can.
            service_data.update(color)
            self._adopt_color_data(color)
        self._expect_echo(on, brightness, color, transition)
        await self.hass.services.async_call(
            "light",
            "turn_on" if on else "turn_off",
            service_data,
            blocking=False,
            context=context,
        )
        self._attr_is_on = on
        self.async_write_ha_state()
        return True

    async def _apply_turn_on_selection(self, context: Context) -> None:
        """Apply the configured select option before an off-to-on command."""
        if not self._turn_on_select_entity:
            return
        target = self.hass.states.get(self._turn_on_select_entity)
        if target is None or target.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            # HA only logs a call to such a target, so it would otherwise be
            # recorded as applied.
            _LOGGER.warning(
                "Turn-on selection target %s is %s; turning on the lights without it",
                self._turn_on_select_entity,
                "missing" if target is None else target.state,
            )
            return
        option, source = self._resolve_turn_on_selection()
        if option is None:
            parts = []
            if self._turn_on_select_source_entity:
                parts.append(f"the source {self._turn_on_select_source_entity}")
            if self._turn_on_select_option:
                parts.append(f"the fixed fallback {self._turn_on_select_option!r}")
            _LOGGER.warning(
                "Unable to resolve a turn-on selection option for %s: %s; "
                "turning on the lights without one",
                self._turn_on_select_entity,
                " and ".join(parts) + " yielded no option the target currently offers"
                if parts
                else "no source or fixed fallback is configured",
            )
            return
        try:
            await self.hass.services.async_call(
                "select",
                SERVICE_SELECT_OPTION,
                {
                    "entity_id": self._turn_on_select_entity,
                    ATTR_OPTION: option,
                },
                blocking=True,
                context=context,
            )
        except HomeAssistantError:
            # Turn-on selections are an enhancement; a missing select or a
            # renamed option must never leave the room dark.
            _LOGGER.warning(
                "Unable to apply turn-on selection option %r using %s; turning on the "
                "lights without it",
                option,
                self._turn_on_select_entity,
                exc_info=True,
            )
        else:
            self._last_turn_on_selection_option = option
            self._last_turn_on_selection_source = source

    def _resolve_turn_on_selection(self) -> tuple[str | None, str | None]:
        """Resolve the source state, falling back to the configured option."""
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

        target_state = self.hass.states.get(self._turn_on_select_entity)
        target_options = (
            target_state.attributes.get(ATTR_OPTIONS)
            if target_state is not None
            else None
        )
        for option, source in candidates:
            if (
                not isinstance(target_options, (list, tuple))
                or option in target_options
            ):
                return option, source
        return None, None

    # ------------------------------------------------------------------
    # Extra state attributes
    # ------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state-machine and attribution attributes."""

        def _fmt(t: datetime | None) -> str | None:
            return t.isoformat() if t else None

        attributes = {
            "molight_state": self._machine_state,
            "auto_off_held": self._held,
            "last_on_physical": _fmt(self._last_on_physical),
            "last_on_virtual": _fmt(self._last_on_virtual),
            "last_on_occupancy": _fmt(self._last_on_occupancy),
            "last_on_illuminance": _fmt(self._last_on_illuminance),
            "last_on_door": _fmt(self._last_on_door),
            "last_off_manual": _fmt(self._last_manual_off),
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
            # Non-null only while the effect/warn stage is showing; persisted
            # so a restart mid-warning can restore the pre-warning brightness
            # and color.
            "warning_active": self._warning_active,
            "pre_warn_brightness": self._pre_warn_brightness,
            "pre_warn_color": self._pre_warn_color,
            "schedule_window_start": self._schedule_window_applied,
            "bright_forced_off": self._bright_forced_off,
            "bright_resume_until": _fmt(self._bright_resume_until),
        }
        if self._is_scheduled_light:
            attributes[ATTR_ACTIVE_SETTINGS] = (
                ACTIVE_SETTINGS_INSIDE
                if self._inside_schedule
                else ACTIVE_SETTINGS_OUTSIDE
            )
            attributes[ATTR_SCHEDULE_END_OFF_PENDING] = self._schedule_end_off_pending
            attributes[ATTR_STANDBY_SUPPRESSED] = self._standby_suppressed
            attributes[ATTR_ACTIVE_SETTINGS_SCHEDULE] = self._settings_schedule_entity
            attributes[ATTR_ACTIVE_SETTINGS_WINDOW] = self._settings_window()
        return attributes
