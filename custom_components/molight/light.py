"""Virtual Light platform for MoLight.

A VirtualLight controls N real light entities and manages a state machine
that integrates optional occupancy, illuminance, and schedule virtual sensors.

State machine
─────────────
  IDLE       lights off, no timer
  ACTIVE     lights on, timer running
               → entered on a manual/physical turn-on or an open-mode door
                 trigger, when no occupancy/maintain/door hold applies
  OCCUPIED   lights on, occupancy active — timer suspended
  COUNTDOWN  occupancy just cleared, timer ticking toward lights-off
  SCHEDULED  lights on inside a follow-mode schedule window — no timer
  EFFECT     auto-off imminent — showing the brief effect/blink warning stage
  WARN       auto-off imminent — grace period before the lights go off

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
    EFFECT — a brief cue (blink/dip to effect_brightness, 0 = fully off) shown
             for effect_timeout seconds. Skipped when effect_timeout is 0.
    WARN   — a grace period at warn_brightness (absent = the brightness the
             light had before the warning) for warn_timeout seconds, then off.
             Skipped when warn_timeout is 0.
  Each stage can optionally fade into its brightness over effect_transition /
  warn_transition seconds (each validated <= its stage's timeout). Separately,
  auto_on_transition / auto_off_transition fade automatic turn-ons and
  turn-offs; manual/physical turn-ons and a manual off never get a transition.
  Throughout EFFECT and WARN the virtual light stays logically on. Any
  re-trigger — occupancy/maintain becoming active, a manual or physical
  turn-on, an external dim — cancels the sequence and behaves exactly as if
  the pre-off timer were still running. Re-triggers that carry no brightness
  of their own (occupancy/maintain/door, a virtual turn-on without an explicit
  brightness, a gate lifting, auto-off becoming held) restore the pre-warning
  brightness so the warning is transparent; a physical turn-on or an external
  dim brings its own brightness, which is honoured instead of the snapshot.
  Bright-forces-off (control mode) and a hard-gate/follow window ending still turn
  the lights off during the sequence, as they would mid-countdown.
  Each stage can also show an optional color (effect_rgb_color /
  warn_rgb_color — e.g. a red warn stage as an unmissable cue); color-capable
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

Turn-on attribution
  Four timestamps record the last time the virtual light was activated and why:
    last_on_physical   — an underlying real light entity changed to ON from an
                         external source (physical switch, another automation, HA
                         UI acting on the real entity) while this virtual light
                         was IDLE.
    last_on_virtual    — the user toggled this virtual light entity ON via the HA UI
                         (async_turn_on was called directly).
    last_on_occupancy  — occupancy sensor triggered the lights.
    last_on_illuminance— an illuminance→dark change triggered the lights.
    last_on_door       — a door sensor opening triggered the lights.

  All are exposed as extra state attributes (ISO strings or null).
  In the absence of an occupancy sensor, the illuminance-dark countdown is
  computed from the most recent of the physical/virtual/occupancy/door
  timestamps (last_on_illuminance is deliberately excluded: a previous dark
  re-activation is not fresh human activity, so repeated dark/bright cycles
  can't extend the on-period forever).

  Brightness changes are tracked the same way: last_brightness_change_physical
  records external changes on the real lights (and restarts a running
  ACTIVE/COUNTDOWN timer; brightness 0 counts as off, leaving 0 as on), while
  last_brightness_change_virtual records brightness set through this entity.
  Color changes are tracked identically via last_color_change_physical /
  last_color_change_virtual, and an external recolor restarts a running timer
  exactly like an external dim — both are human activity.

Color support
  The virtual light derives its color capabilities from the real lights: it
  advertises HS when any member can show a color (hs/rgb/rgbw/rgbww/xy — HA
  converts hs to each member's native mode) and COLOR_TEMP when any member
  supports it, falling back to brightness-only when none do. Capabilities are
  re-derived on every member event, so members that are unavailable at startup
  contribute theirs once they appear. Color commands are forwarded to ALL
  members in one service call; HA filters/converts the color per real light,
  so mixed setups (color + brightness-only members) just work — each light
  shows what it can. The reported color mirrors the first on member that has
  one, exactly like brightness.

Maintain occupancy (when a maintain occupancy entity is configured)
  The maintain entity holds an already-on light on while it is on; it never
  turns the light on and is ignored while the light is off. Unlike the
  combined sensor's maintain_sensors (which only extend occupancy started by
  a trigger sensor), it holds the light regardless of how it was lit —
  manual, physical, or occupancy.

  • Maintain ON while the light is on (ACTIVE/COUNTDOWN) → OCCUPIED, timer
    cancelled. Illuminance/schedule gating does not apply: it is not a
    turn-on. Forced offs (bright in control mode, hard-gate window end) still win,
    exactly as they do over regular occupancy.
  • Occupancy clearing while maintain is on keeps the light OCCUPIED.
  • The countdown starts only when both the regular occupancy entity and the
    maintain entity are clear, anchored to the max of their
    latest_occupied_time attributes.
  • The false-detection quick off applies on a maintain clear only when both
    sensors flagged their clears false (a genuine presence on either side
    means the light earns its normal countdown).
  • Startup: a light that is already on with the maintain entity on is
    adopted as OCCUPIED (no timer).

Illuminance handling (when an illuminance entity is configured), per
illuminance_mode:
  • Occupancy only turns lights ON when illuminance is OFF (dark) — both modes.
  • Illuminance ON→OFF (bright→dark): if currently occupied, enter OCCUPIED;
    else if recent occupancy (countdown > 0), enter COUNTDOWN with adjusted
    timer — both modes.
  • Illuminance OFF→ON (dark→bright):
      control — go IDLE, turn lights off.
      gate    — no effect; bright never turns lights off. Use when the lux
                sensor can see the controlled lights, which would otherwise
                oscillate (lights on → reads bright → forced off → dark → …).

Holding auto-off
  Auto-off is *held* while the companion "<name> Auto-off" switch is off OR
  any configured keep-on entity (hold_entities) is on. While held, every
  automatic turn-off is suspended — timer expiry, the false-detection quick
  off, bright-forces-off, and schedule window ends — but turn-ons and manual
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
  • follow — lights turn ON at window start (state SCHEDULED, no timer) and
    OFF at window end. Boundaries are edge-triggered: manual changes between
    them stand. schedule_window_start records the window whose start we
    applied; it persists across restarts so a boundary missed while HA was
    down is applied exactly once at startup, while a manual off mid-window
    is respected. Occupancy/illuminance are ignored while SCHEDULED.
  • gate — occupancy may only activate lights inside the window; window end
    forces lights off (like illuminance turning bright), window start
    re-evaluates occupancy.
  • gate_keep — the same gate for turning an OFF light on; once the lights
    are on, occupancy and the door behave as inside the window (adopt, hold,
    re-hold), and window end preserves the current on-period, including its
    sensor hold, countdown or warning.

Door handling (when a door entity is configured), per door_mode:
  Opening the door (state on) is a turn-on trigger, gated by illuminance and
  a gate-mode schedule exactly like occupancy — it only lights the room when
  it is dark (if an illuminance entity is set) and inside a gate window.
  • open       — opening turns the lights on with the normal timeout (ACTIVE);
    the door is otherwise ignored, so closing does nothing and the lights
    time out even if the door stays open. A momentary trigger.
  • open_close — the open door holds the lights on with no timer (OCCUPIED,
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
from datetime import UTC, datetime
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
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    ENTITY_ID_FORMAT,
    ColorMode,
    LightEntity,
)
from homeassistant.components.select import ATTR_OPTIONS
from homeassistant.const import (
    ATTR_OPTION,
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
    ATTR_SCHEDULE_END_OFF_PENDING,
    CONF_AUTO_OFF_TRANSITION,
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_AUTO_ON_COLOR_TEMP,
    CONF_AUTO_ON_RGB_COLOR,
    CONF_AUTO_ON_TRANSITION,
    CONF_DOOR_ENTITY,
    CONF_DOOR_MODE,
    CONF_EFFECT_BRIGHTNESS,
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
    CONF_TURN_ON_SELECT_ENTITY,
    CONF_TURN_ON_SELECT_OPTION,
    CONF_TURN_ON_SELECT_SOURCE_ENTITY,
    CONF_WARN_BRIGHTNESS,
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
    SCHEDULE_END_ACTION_TURN_OFF,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
    SCHEDULE_MODE_GATE_KEEP,
    SIGNAL_AUTO_OFF_TOGGLED,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_EFFECT,
    STATE_IDLE,
    STATE_OCCUPIED,
    STATE_SCHEDULED,
    STATE_WARN,
)
from .helpers import molight_config, suggested_entity_id

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, EventStateChangedData, State
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

# Member color modes an hs command can drive (HA converts hs to each member's
# native mode); any of them lets the virtual light advertise HS itself.
_HS_CAPABLE_MODES = {
    ColorMode.HS,
    ColorMode.RGB,
    ColorMode.RGBW,
    ColorMode.RGBWW,
    ColorMode.XY,
}


def _opt_transition(value: float | None) -> float | None:
    """Convert a configured transition to float seconds; absent/0 = None."""
    return float(value) if value else None


def _opt_rgb_color(value: list | None) -> dict | None:
    """Convert a configured [r, g, b] to turn-on service data; absent = None."""
    return {ATTR_RGB_COLOR: tuple(int(c) for c in value)} if value else None


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


class VirtualLight(LightEntity, RestoreEntity):
    """A virtual light with occupancy/illuminance/schedule/door awareness."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    # Reassigned per-instance by _update_capabilities, never mutated in place.
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}
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
        # A scheduled-light off boundary deferred by an Auto-off/keep-on hold.
        # Persisted as a state attribute so a restart cannot lose the pending
        # boundary; returning inside the schedule cancels it.
        self._schedule_end_off_pending = False
        # Every settings mapping this light may run under — both sides of a
        # scheduled light, or the regular light's own config — so the entity
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
        # the user) — the only case where a false-detection clear may cut the
        # lights short.
        self._occupancy_lit_lights: bool = False

        # Brightness and color the light had when the warning sequence began,
        # restored on any re-trigger so the effect/warn stages leave no
        # lasting trace. Exposed as the pre_warn_brightness / pre_warn_color
        # attributes so they survive a restart landing mid-warning.
        self._pre_warn_brightness: int | None = None
        self._pre_warn_color: dict | None = None

        # Last known open/closed of the door — kept ourselves so a briefly
        # unavailable sensor (battery contact sensors blip) holds its last
        # value instead of reading as closed and dropping its hold.
        self._door_open: bool = False
        # Last known on/off of each keep-on entity — kept ourselves so an
        # unavailable entity holds its last value instead of reading as off.
        self._hold_states: dict[str, bool] = {}
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
        # Context ids of our own light service calls, used to tell self-caused
        # state echoes apart from genuinely external changes.
        self._self_context_ids: deque[str] = deque(maxlen=16)

        self._last_on_physical: datetime | None = None
        self._last_on_virtual: datetime | None = None
        self._last_on_occupancy: datetime | None = None
        self._last_on_illuminance: datetime | None = None
        self._last_on_door: datetime | None = None
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
        # Optional color for automatic turn-ons, as turn-on service data
        # (mutually exclusive keys, enforced by the config/options flows).
        # None leaves automatic turn-ons uncolored, like auto_on_brightness.
        kelvin = cfg.get(CONF_AUTO_ON_COLOR_TEMP)
        self._auto_on_color = (
            {ATTR_COLOR_TEMP_KELVIN: int(kelvin)}
            if kelvin
            else _opt_rgb_color(cfg.get(CONF_AUTO_ON_RGB_COLOR))
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
        self._effect_color = _opt_rgb_color(cfg.get(CONF_EFFECT_RGB_COLOR))
        self._warn_color = _opt_rgb_color(cfg.get(CONF_WARN_RGB_COLOR))
        # Optional fade times (seconds) for the service calls this light makes
        # itself: automatic turn-ons/offs and the effect/warn stage changes.
        # None (absent or 0) sends no transition attribute. Manual/physical
        # turn-ons and a manual off are never given a transition.
        self._auto_on_transition = _opt_transition(cfg.get(CONF_AUTO_ON_TRANSITION))
        self._auto_off_transition = _opt_transition(cfg.get(CONF_AUTO_OFF_TRANSITION))
        self._effect_transition = _opt_transition(cfg.get(CONF_EFFECT_TRANSITION))
        self._warn_transition = _opt_transition(cfg.get(CONF_WARN_TRANSITION))

        # Sensor wiring. The schedule fields only ever come from a regular
        # Virtual Light's config — a Virtual Scheduled Light's settings forms
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
                # A boundary off deferred under a previous configuration no
                # longer applies once the end action is "keep".
                self._schedule_end_off_pending = (
                    self._schedule_end_action == SCHEDULE_END_ACTION_TURN_OFF
                    and bool(last.attributes.get(ATTR_SCHEDULE_END_OFF_PENDING))
                )
            # Restore turn-on attribution so the illuminance re-activation
            # countdown keeps working across a restart.
            for source in ("physical", "virtual", "occupancy", "illuminance", "door"):
                raw = last.attributes.get(f"last_on_{source}")
                if raw:
                    with contextlib.suppress(ValueError, TypeError):
                        setattr(self, f"_last_on_{source}", datetime.fromisoformat(raw))
            self._schedule_window_applied = last.attributes.get("schedule_window_start")
            for attr, field in (
                # Fall back to the pre-rename attribute name for restores
                # from before the physical/virtual split.
                ("last_brightness_change_physical", "_last_brightness_change_physical"),
                ("last_brightness_change", "_last_brightness_change_physical"),
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
                and len(raw_hs) == 2  # noqa: PLR2004 — an hs pair is (hue, sat)
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
        # One entity may serve several roles — subscribe to it only once.
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
        if inside:
            self._schedule_end_off_pending = False
        elif (
            schedule is not None
            and schedule.state == "off"
            and self._restored_inside_schedule is True
            and self._schedule_end_action == SCHEDULE_END_ACTION_TURN_OFF
        ):
            # The schedule we were inside ended while Home Assistant was down.
            # _seed_state applies this once after it has restored the
            # physical/hold state. A reload that switched to a different
            # schedule entity has crossed no boundary of that schedule.
            self._schedule_end_off_pending = True
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

    def _switch_scheduled_settings(self, inside: bool) -> None:
        """Select and reconcile a Virtual Scheduled Light settings mapping."""
        if not self._is_scheduled_light or inside == self._inside_schedule:
            return
        leaving_inside = self._inside_schedule and not inside
        if inside:
            # A held end boundary no longer applies once the same schedule
            # window becomes active again.
            self._schedule_end_off_pending = False
        self._inside_schedule = inside
        self._apply_light_settings(
            self._inside_schedule_settings
            if inside
            else self._outside_schedule_settings
        )

        # Seed stateful inputs from their current values. Inactive settings
        # entities remain subscribed but are ignored by _handle_state_change.
        door = self.hass.states.get(self._door_entity) if self._door_entity else None
        self._door_open = door is not None and door.state == "on"
        self._hold_states = {
            entity_id: (state := self.hass.states.get(entity_id)) is not None
            and state.state == "on"
            for entity_id in self._hold_entities
        }
        old_held = self._held
        self._held = self._compute_held()

        if (
            leaving_inside
            and self._schedule_end_action == SCHEDULE_END_ACTION_TURN_OFF
            and self._attr_is_on
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
            self.async_write_ha_state()
            return

        if not self._attr_is_on:
            if self._is_illuminance_bright():
                self.async_write_ha_state()
                return
            if self._occupancy_active():
                self._on_occupancy_change(occupied=True)
            elif self._door_open:
                self._on_door_change(True)
            else:
                self.async_write_ha_state()
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
            self._go_idle()
            return

        if self._occupancy_holds() or self._maintain_active() or self._door_holds():
            self._adopt_active_occupancy()
        elif self._machine_state == STATE_OCCUPIED:
            # The previous settings held the light indefinitely; the new ones
            # do not, so begin their normal timeout now.
            self._machine_state = STATE_COUNTDOWN
            self._start_timer()
        self.async_write_ha_state()

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

        # Brightness 0 counts as off, matching _all_lights_off.
        self._attr_is_on = any(
            (s := self.hass.states.get(e)) is not None
            and s.state == "on"
            and s.attributes.get("brightness") != 0
            for e in self._lights
        )

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
                if self._pre_warn_brightness is not None or self._pre_warn_color:
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

        if self._pre_warn_brightness is not None or self._pre_warn_color is not None:
            if self._attr_is_on:
                # The restart landed mid effect/warn with the lights still on:
                # undo the warning stage like any other re-trigger, then seed
                # normally (the warn-stage brightness/color must not be
                # adopted).
                self._resume_lights()
            else:
                # The lights ended up off (e.g. mid blink-off) — treat the
                # auto-off as having completed; the room is not re-lit.
                self._pre_warn_brightness = None
                self._pre_warn_color = None

        if self._follow_schedule_seed():
            return

        # Occupancy only takes over when it's dark (or no illuminance is
        # configured) and inside any gate-mode schedule window.
        if (
            self._occupancy_entity
            and not self._is_illuminance_bright()
            and not self._gate_schedule_inactive()
        ):
            occ_state = self.hass.states.get(self._occupancy_entity)
            if occ_state and occ_state.state == "on":
                self._on_occupancy_change(occupied=True)
                return

        if self._attr_is_on and (self._maintain_active() or self._door_holds()):
            # Adopt an already-on light as maintained — no gating, since this
            # is not a turn-on; the maintain entity clearing, or the door
            # closing, starts the countdown as usual.
            self._machine_state = STATE_OCCUPIED
            self.async_write_ha_state()
            return

        if self._attr_is_on:
            # Lights are already on (whatever the illuminance) — adopt them
            # and run the normal timer so they still turn off eventually.
            self._machine_state = STATE_ACTIVE
            self._start_timer()

        self.async_write_ha_state()

    def _follow_schedule_seed(self) -> bool:
        """Apply follow-mode schedule state at startup. Returns True if handled.

        The stored window marker distinguishes a boundary missed while HA was
        down (apply it now) from one we already handled before the restart
        (leave the lights alone — if they're off, the user turned them off).
        """
        if not self._schedule_entity or self._schedule_mode != SCHEDULE_MODE_FOLLOW:
            return False
        sched = self.hass.states.get(self._schedule_entity)
        if sched is None:
            return False

        if sched.state == "on":
            marker = sched.attributes.get("current_window_start")
            if marker and marker != self._schedule_window_applied:
                # Window started while HA was down — catch up now.
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
                # Auto-off is held — keep the marker so releasing the hold
                # applies the missed off boundary.
                self._machine_state = STATE_SCHEDULED
                self.async_write_ha_state()
                return True
            # Window ended while HA was down — apply the off boundary.
            self._schedule_window_applied = None
            self._machine_state = STATE_IDLE
            if self._attr_is_on:
                self.hass.async_create_task(self._auto_lights_off())
            self.async_write_ha_state()
            return True

        return False

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the running countdown timer on removal."""
        await super().async_will_remove_from_hass()
        self._cancel_timer()

    # ------------------------------------------------------------------
    # LightEntity API
    # ------------------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on all real lights and transition the state machine."""
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
        await self._set_lights(
            True,
            brightness=brightness,
            color=color,
            apply_turn_on_selection=True,
        )
        self._transition_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off all real lights and go idle."""
        await self._set_lights(False)
        self._go_idle()

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
            # Capabilities can appear late (members unavailable at startup):
            # re-derive on every member event, before the echo check — our own
            # service calls still surface a member's first real state.
            self._update_capabilities()
            if event.context.id in self._self_context_ids:
                return  # echo of our own service call; call sites manage state
            if same_state:
                if new_state.state == "on":
                    self._on_light_attrs_change(old_state, new_state)
                return
            if (
                (
                    old_state is None
                    or old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                )
                and new_state.state == "on"
                and new_state.attributes.get("brightness") != 0
                and self._machine_state != STATE_IDLE
            ):
                # A member reappearing (first sighting, or recovery from
                # unavailable) while the virtual light is already on is not
                # human activity: mirror its brightness/color but leave the
                # running timer, countdown, or warning sequence untouched —
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
                new_state.state, new_state.attributes.get("brightness")
            )
            return
        if same_state:
            return  # attribute-only change (battery, ...)
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
        if entity_id == self._occupancy_entity:
            self._on_occupancy_change(new_state.state == "on")
        if entity_id == self._maintain_entity:
            self._on_maintain_change(new_state.state == "on")
        if entity_id == self._illuminance_entity:
            self._on_illuminance_change(new_state.state == "on")
        if entity_id == self._schedule_entity:
            self._on_schedule_change(new_state)
        if entity_id == self._door_entity:
            self._door_open = new_state.state == "on"
            self._on_door_change(self._door_open)
        if entity_id in self._hold_entities:
            self._hold_states[entity_id] = new_state.state == "on"
            self._refresh_hold()

    def _on_light_state_change(self, state: str, brightness: int | None = None) -> None:
        """Handle a real light being turned on/off externally."""
        if state == "on" and brightness == 0:
            # "On" at brightness 0 is an off in disguise, matching the dimming
            # path, _all_lights_off and the startup seed. A later 0 → non-zero
            # dim is then handled as the turn-on (in _on_light_attrs_change).
            if self._all_lights_off():
                self._go_idle()
            return
        if state == "on":
            # Mirror the real light's brightness so the virtual light always
            # matches it — including on this off→on adoption edge, not just on
            # later dims (which _on_light_brightness_change handles).
            if brightness:
                self._attr_brightness = brightness
            if self._machine_state == STATE_IDLE:
                self._last_on_physical = datetime.now(UTC)
                self._occupancy_lit_lights = False  # the user owns this on-period
            self._transition_on()
        elif self._all_lights_off():
            self._go_idle()

    def _on_light_attrs_change(self, old_state: State, new_state: State) -> None:
        """Handle an external brightness/color change on an on real light.

        Dimming or recoloring is human activity: record it and restart any
        running countdown with the full timeout. Brightness 0 means off in
        disguise.
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
                    self._go_idle()
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
                self._on_light_state_change("on")
            self.async_write_ha_state()
            return

        if self._machine_state in (
            STATE_ACTIVE,
            STATE_COUNTDOWN,
            STATE_EFFECT,
            STATE_WARN,
        ):
            # An external dim or recolor during the warning sequence is a
            # re-trigger like any other: honour the new brightness/color and
            # restart the full timer.
            self._pre_warn_brightness = None
            self._pre_warn_color = None
            self._machine_state = STATE_ACTIVE
            self._start_timer()
        self.async_write_ha_state()

    def _all_lights_off(self) -> bool:
        """Return True when every real light is off (brightness 0 is off)."""
        for entity_id in self._lights:
            state = self.hass.states.get(entity_id)
            if state is None:
                return False  # unknown entity — don't assume it's off
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
        keeping this entity's own color state simple. Falls back to
        brightness-only — the pre-color behavior — when no member reports a
        color capability, including while members are still unavailable.
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
            supported = {ColorMode.BRIGHTNESS}

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
        self._attr_supported_color_modes = supported
        if self._attr_color_mode not in supported:
            # Keep the reported mode legal for the new capability set; the
            # value-bearing attributes only survive where they still apply.
            if supported == {ColorMode.BRIGHTNESS}:
                self._attr_hs_color = None
                self._attr_color_temp_kelvin = None
                self._attr_color_mode = ColorMode.BRIGHTNESS
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

        A no-op for modes we don't advertise — notably everything on a
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

        The hs value is a list so the dict is JSON-serializable — it is also
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

        gate_keep gates activation only: once the lights are on, occupancy
        and the door behave as inside the window (adopt, hold, re-hold), so
        an on-period preserved past the window end keeps its sensor hold.
        """
        if not self._schedule_entity or self._schedule_mode not in (
            SCHEDULE_MODE_GATE,
            SCHEDULE_MODE_GATE_KEEP,
        ):
            return False
        if self._schedule_mode == SCHEDULE_MODE_GATE_KEEP and self._attr_is_on:
            return False
        state = self.hass.states.get(self._schedule_entity)
        return not (state is not None and state.state == "on")

    def _hard_gate_schedule_inactive(self) -> bool:
        """Return True when the original hard gate currently requires off."""
        return (
            self._schedule_mode == SCHEDULE_MODE_GATE and self._gate_schedule_inactive()
        )

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
        window or active occupancy (gated like any adoption — suppressed when
        bright or outside a gate window) keeps them on without a timer;
        otherwise a fresh full timer starts.
        """
        if not self._attr_is_on:
            return  # lights-off transitions were never suppressed

        if self._schedule_end_off_pending:
            self._finish_schedule_end_off()
            return

        sched = self._follow_schedule_state()
        if sched is not None:
            # Active follow window owns the lights — no timer.
            self._apply_window_start(sched.attributes.get("current_window_start"))
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

        if self._hard_gate_schedule_inactive() or (
            self._is_illuminance_bright()
            and self._illuminance_mode == ILLUMINANCE_MODE_CONTROL
        ):
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle()
            return

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

    def _on_schedule_change(self, new_state: State) -> None:
        """Handle the virtual schedule sensor changing."""
        if self._schedule_mode == SCHEDULE_MODE_FOLLOW:
            if new_state.state == "on":
                marker = new_state.attributes.get("current_window_start")
                if marker and marker == self._schedule_window_applied:
                    # Same window we already applied — the schedule entity
                    # blipped unavailable and recovered mid-window. A manual
                    # off in between stands, mirroring the restart seed.
                    if self._attr_is_on:
                        if self._in_warning():
                            # A timer that ran while the schedule was
                            # unavailable reached the warning — undo it, the
                            # recovered window owns the lights again.
                            self._resume_lights()
                        self._machine_state = STATE_SCHEDULED
                        self._cancel_timer()
                        self.async_write_ha_state()
                    return
                self._apply_window_start(marker)
            elif self._held:
                # Auto-off held — keep the window marker so releasing the
                # hold applies this off boundary.
                pass
            else:
                # Window ended — apply the off boundary.
                self._schedule_window_applied = None
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle()
            return

        # Both gate modes block automatic activation outside the window and
        # re-evaluate occupancy/a held-open door when it starts. The original
        # hard gate also forces off at the end; gate_keep leaves the current
        # on-period and its timer/holds alone.
        if new_state.state != "on":
            if (
                self._schedule_mode == SCHEDULE_MODE_GATE
                and self._machine_state != STATE_IDLE
                and not self._held
            ):
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle()
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
            if occ_active or self._door_holds():
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

    def _apply_window_start(self, marker: str | None) -> None:
        """Enter the SCHEDULED state and turn the lights on (follow mode)."""
        self._schedule_window_applied = marker
        was_warning = self._in_warning()
        self._machine_state = STATE_SCHEDULED
        self._cancel_timer()
        if was_warning:
            # The window takes over mid-warning: the virtual light is
            # logically on but the real lights are blinked off / dimmed by
            # the effect/warn stage — restore them for the window.
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
                # Bright enough — suppress lights; when illuminance turns off we
                # re-evaluate occupancy from the sensor's current state.
                return
            if self._gate_schedule_inactive():
                # Outside the schedule window — occupancy may not turn lights
                # on; window start re-evaluates occupancy.
                return
            was_warning = self._in_warning()
            self._last_on_occupancy = datetime.now(UTC)
            self._machine_state = STATE_OCCUPIED
            self._cancel_timer()
            if was_warning:
                # Occupancy returned mid-warning: undo the effect/warn stage so
                # the light looks exactly as it did while the timer was running.
                self._resume_lights()
            elif not self._attr_is_on:
                self._occupancy_lit_lights = True
                self.hass.async_create_task(self._auto_lights_on())
            self.async_write_ha_state()
        elif self._machine_state == STATE_OCCUPIED:
            if self._maintain_active() or self._door_holds():
                return  # maintain entity / open door holds the light on
            self._machine_state = STATE_COUNTDOWN
            if self._occupancy_lit_lights and self._occupancy_clear_was_false():
                # The whole cycle was a false detection and nobody else
                # asked for these lights — turn them off quickly.
                self._start_timer(self._false_off_delay)
            else:
                self._start_timer(self._compute_occupancy_countdown())
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
            if self._occupancy_active() or self._door_holds():
                return  # regular occupancy / open door still holds the light on
            self._machine_state = STATE_COUNTDOWN
            if (
                self._occupancy_lit_lights
                and self._occupancy_clear_was_false()
                and self._clear_was_false(self._maintain_entity)
            ):
                # Both sensors flagged their clears false — the whole episode
                # was a false detection; a genuine presence on either side
                # earns the normal countdown instead.
                self._start_timer(self._false_off_delay)
            else:
                self._start_timer(self._compute_occupancy_countdown())
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
        already active would run a timer that expires despite presence — and
        the steady-on sensor produces no event that could ever rescue it.
        """
        return (
            self._occupancy_active()
            and not self._is_illuminance_bright()
            and not self._gate_schedule_inactive()
        )

    def _adopt_active_occupancy(self) -> None:
        """Move an on light to OCCUPIED when a gate lifts mid-on-period.

        Used when illuminance turns dark or a gate-mode window starts while
        the lights are already on (ACTIVE/COUNTDOWN/EFFECT/WARN) with
        occupancy active. Not a turn-on: attribution and the occupancy-lit
        flag are left untouched, so the user still owns a manual on-period.
        """
        was_warning = self._in_warning()
        self._machine_state = STATE_OCCUPIED
        self._cancel_timer()
        if was_warning:
            self._resume_lights()
        self.async_write_ha_state()

    def _maintain_active(self) -> bool:
        """Return True when the maintain occupancy entity is configured and on."""
        if not self._maintain_entity:
            return False
        state = self.hass.states.get(self._maintain_entity)
        return state is not None and state.state == "on"

    def _door_holds(self) -> bool:
        """Return True when an open_close-mode door is open (holds the light).

        Such a door holds an already-on light on with no timer, exactly like
        active occupancy or the maintain entity, and its closing starts the
        countdown. In plain open mode a door never holds — it is only a
        momentary turn-on trigger — so this is always False there. Reads the
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
            self._last_on_door = datetime.now(UTC)
            # An open_close door holds the light (no timer) like the maintain
            # entity; an already-occupied/maintained room holds it too. Only a
            # plain open-mode trigger with no other hold runs the timeout.
            # (Occupancy needs no bright/window re-check here — the gates
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
            elif not self._attr_is_on:
                self._occupancy_lit_lights = False  # the door owns this period
                self.hass.async_create_task(self._auto_lights_on())
            if not holds:
                self._start_timer()
            self.async_write_ha_state()
        else:
            if self._door_mode != DOOR_MODE_OPEN_CLOSE:
                return  # open mode: closing is ignored
            if self._machine_state == STATE_IDLE:
                return
            if self._occupancy_active() or self._maintain_active():
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
                        → turn on if currently occupied, or if the occupancy
                          countdown still has time remaining.
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
                # Auto-off held — releasing the hold re-checks brightness.
                return
            if self._machine_state != STATE_IDLE:
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle()
        else:
            if self._machine_state != STATE_IDLE:
                # Lights already on: going dark lifts the gate that kept
                # already-active occupancy (or an open door) from holding
                # them — adopt so a timer can't expire despite presence.
                if self._occupancy_holds() or self._door_holds():
                    self._adopt_active_occupancy()
                return
            if self._gate_schedule_inactive():
                return  # outside the schedule window — no activation

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
            else:
                countdown = self._compute_illuminance_countdown()
                if countdown > 0:
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

    def _compute_occupancy_countdown(self) -> int:
        """Seconds to wait after occupancy clears before turning lights off.

        Anchors to the latest_occupied_time across the occupancy and maintain
        entities (whichever saw the person last) so that each sub-sensor's
        individual timeout is respected: the lights go off light_timeout
        seconds after the person actually left, i.e. at
        latest_occupied_time + light_timeout — which is why light_timeout must
        be >= the sensor's occupancy_timeout (the flows enforce it).
        """
        base = self._light_timeout
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
        if lots:
            now = datetime.now(UTC)
            remaining = (max(lots) - now).total_seconds()
            return max(0, int(base + remaining))
        return base

    def _most_recent_on_time(self) -> datetime | None:
        """Return the latest recorded turn-on timestamp across all sources.

        Illuminance is excluded — it is only relevant for gating, not
        attribution.
        """
        candidates = [
            t
            for t in (
                self._last_on_physical,
                self._last_on_virtual,
                self._last_on_occupancy,
                self._last_on_door,
            )
            if t is not None
        ]
        return max(candidates) if candidates else None

    def _compute_illuminance_countdown(self) -> int:
        """Countdown (seconds) to use when illuminance going dark re-activates lights.

        When an occupancy entity is configured, delegates to the occupancy-based
        countdown which is already anchored to latest_occupied_time.

        When there is no occupancy entity, subtracts elapsed time since the light
        was last on from light_timeout, so the re-activation uses only the
        remaining portion of the original on-period.
        """
        if self._occupancy_entity:
            return self._compute_occupancy_countdown()

        last_on = self._most_recent_on_time()
        if last_on is not None:
            elapsed = (datetime.now(UTC) - last_on).total_seconds()
            return max(0, int(self._light_timeout - elapsed))
        return self._light_timeout

    def _transition_on(self) -> None:
        """Move to ACTIVE (or stay OCCUPIED/SCHEDULED) when lights come on."""
        # A manual/physical turn-on ends any warning sequence; the caller has
        # already set the real lights, so just drop the restore snapshot.
        self._pre_warn_brightness = None
        self._pre_warn_color = None
        if self._machine_state in (STATE_OCCUPIED, STATE_SCHEDULED):
            return  # already managed by occupancy / schedule window
        # Set before the hold checks: a gate_keep schedule only gates turning
        # an off light on, so occupancy may hold this turn-on outside it.
        self._attr_is_on = True

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

    def _go_idle(self) -> None:
        """Cancel any timer and move to IDLE."""
        self._cancel_timer()
        self._machine_state = STATE_IDLE
        self._attr_is_on = False
        self._occupancy_lit_lights = False
        # A deferred schedule-end off only applies to the on-period it
        # interrupted; any off consumes it.
        self._schedule_end_off_pending = False
        self._pre_warn_brightness = None
        self._pre_warn_color = None
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Timer helpers
    # ------------------------------------------------------------------

    def _start_timer(self, duration: int | None = None) -> None:
        self._cancel_timer()
        if self._held:
            # Auto-off held — the state machine transitions normally but no
            # timer is armed; releasing the hold starts a fresh one.
            return
        self._timer_unsub = async_call_later(
            self.hass,
            duration if duration is not None else self._light_timeout,
            self._timer_expired,
        )

    async def _timer_expired(self, _now: datetime) -> None:
        self._timer_unsub = None
        if self._held:
            return  # engaged in the same loop iteration the timer fired
        state = self._machine_state
        if state in (STATE_ACTIVE, STATE_COUNTDOWN):
            # Normal auto-off: run the warning sequence, or turn straight off.
            self._begin_warning()
        elif state == STATE_EFFECT:
            self._enter_warn()
        elif state == STATE_WARN:
            # Task + synchronous _go_idle (the idiom used everywhere else):
            # awaiting the service call first would let a re-trigger landing
            # mid-await be stomped back to IDLE when the await returns.
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle()
        # any other state: the machine moved on in the same loop iteration the
        # timer fired — nothing to do.

    def _cancel_timer(self) -> None:
        if self._timer_unsub is not None:
            self._timer_unsub()
            self._timer_unsub = None

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
        own). Falls through to the warn stage — and to a plain off — when the
        earlier stage is disabled, so both timeouts at 0 behaves exactly like
        the old immediate off.
        """
        self._pre_warn_brightness = self._attr_brightness
        self._pre_warn_color = self._current_color()
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
                self._machine_state == STATE_EFFECT and self._effect_color is not None
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
        self.hass.async_create_task(self._auto_lights_off())
        self._go_idle()

    def _resume_lights(self) -> None:
        """Restore the pre-warning brightness and color after a re-trigger.

        The caller sets the resulting machine state. No transition: the
        restore must be as immediate as the re-trigger that caused it.
        """
        brightness = self._pre_warn_brightness
        color = self._pre_warn_color
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

    def _auto_lights_on(self) -> Coroutine[Any, Any, None]:
        """Turn the real lights on for an automatic trigger.

        Applies the configured auto-on brightness, color and transition.
        """
        return self._set_lights(
            True,
            brightness=self._auto_on_brightness,
            transition=self._auto_on_transition,
            color=self._auto_on_color,
            apply_turn_on_selection=True,
        )

    def _auto_lights_off(self) -> Coroutine[Any, Any, None]:
        """Turn the real lights off for an automatic turn-off.

        Applies the configured auto-off transition. Manual offs bypass this.
        """
        return self._set_lights(False, transition=self._auto_off_transition)

    def _scheduled_end_lights_off(self) -> Coroutine[Any, Any, None]:
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
    ) -> None:
        was_off = not self._attr_is_on
        context = Context()
        self._self_context_ids.append(context.id)
        if on and was_off and apply_turn_on_selection:
            await self._apply_turn_on_selection(context)
        service_data: dict = {"entity_id": self._lights}
        if transition is not None:
            service_data[ATTR_TRANSITION] = transition
        if on and brightness is not None:
            service_data[ATTR_BRIGHTNESS] = brightness
            # Mirror the commanded brightness so the virtual light reports it
            # (the real-light echo is ignored as a self-caused change).
            self._attr_brightness = brightness
        if on and color:
            # One call carries the color to every member; HA filters/converts
            # it per real light, so mixed-capability members each show what
            # they can.
            service_data.update(color)
            self._adopt_color_data(color)
        await self.hass.services.async_call(
            "light",
            "turn_on" if on else "turn_off",
            service_data,
            blocking=False,
            context=context,
        )
        self._attr_is_on = on
        self.async_write_ha_state()

    async def _apply_turn_on_selection(self, context: Context) -> None:
        """Apply the configured select option before an off-to-on command."""
        if not self._turn_on_select_entity:
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
            "pre_warn_brightness": self._pre_warn_brightness,
            "pre_warn_color": self._pre_warn_color,
            "schedule_window_start": self._schedule_window_applied,
        }
        if self._is_scheduled_light:
            attributes[ATTR_ACTIVE_SETTINGS] = (
                ACTIVE_SETTINGS_INSIDE
                if self._inside_schedule
                else ACTIVE_SETTINGS_OUTSIDE
            )
            attributes[ATTR_SCHEDULE_END_OFF_PENDING] = self._schedule_end_off_pending
            attributes[ATTR_ACTIVE_SETTINGS_SCHEDULE] = self._settings_schedule_entity
        return attributes
