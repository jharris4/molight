"""Virtual Light platform for MoLight.

A VirtualLight controls N real light entities and manages a state machine
that integrates optional occupancy, illuminance, and schedule virtual sensors.

State machine
─────────────
  IDLE       lights off, no timer
  ACTIVE     lights on, timer running
               → entered when light turned on manually, or when occupancy
                 triggers and there is no active occupancy
  OCCUPIED   lights on, occupancy active — timer suspended
  COUNTDOWN  occupancy just cleared, timer ticking toward lights-off
  EFFECT     auto-off imminent — showing the brief effect/blink warning stage
  WARN       auto-off imminent — grace period before the lights go off

Transitions
  IDLE + (manual on OR occupancy trigger [no occupancy sensor / not occupied])
       → ACTIVE  (start timer immediately)

  IDLE/ACTIVE + occupancy becomes active
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
  the pre-off timer were still running, restoring the pre-warning brightness so
  the warning is transparent. Auto-off being held mid-sequence aborts it the
  same way. Bright-forces-off (control mode) and a gate/follow window ending
  still turn the lights off during the sequence, as they would mid-countdown.
  The pre-warning brightness is exposed as the pre_warn_brightness attribute
  (null outside the sequence) and survives restarts: a restart landing
  mid-warning with the lights still on restores that brightness instead of
  adopting the stage's; lights found off stay off (the auto-off completed).

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

  All four are exposed as extra state attributes (ISO strings or null).
  The most-recent value is used when computing the illuminance-dark countdown
  in the absence of an occupancy sensor.

  Brightness changes are tracked the same way: last_brightness_change_physical
  records external changes on the real lights (and restarts a running
  ACTIVE/COUNTDOWN timer; brightness 0 counts as off, leaving 0 as on), while
  last_brightness_change_virtual records brightness set through this entity.

Maintain occupancy (when a maintain occupancy entity is configured)
  The maintain entity holds an already-on light on while it is on; it never
  turns the light on and is ignored while the light is off. Unlike the
  combined sensor's maintain_sensors (which only extend occupancy started by
  a trigger sensor), it holds the light regardless of how it was lit —
  manual, physical, or occupancy.

  • Maintain ON while the light is on (ACTIVE/COUNTDOWN) → OCCUPIED, timer
    cancelled. Illuminance/schedule gating does not apply: it is not a
    turn-on. Forced offs (bright in control mode, gate window end) still win,
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
  conditions: a follow-mode window that ended while held turns it off now
  (the window marker is kept while held for exactly this), as does being
  outside a gate-mode window or bright in illuminance control mode; active
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
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
    ENTITY_ID_FORMAT,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
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
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.percentage import percentage_to_ranged_value

from .const import (
    CONF_AUTO_OFF_TRANSITION,
    CONF_AUTO_ON_BRIGHTNESS,
    CONF_AUTO_ON_TRANSITION,
    CONF_EFFECT_BRIGHTNESS,
    CONF_EFFECT_TIMEOUT,
    CONF_EFFECT_TRANSITION,
    CONF_ENTITY_TYPE,
    CONF_FALSE_OFF_DELAY,
    CONF_HOLD_ENTITIES,
    CONF_ILLUMINANCE_ENTITY,
    CONF_ILLUMINANCE_MODE,
    CONF_LIGHT_TIMEOUT,
    CONF_LIGHTS,
    CONF_MAINTAIN_OCCUPANCY_ENTITY,
    CONF_NAME,
    CONF_OCCUPANCY_ENTITY,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_MODE,
    CONF_WARN_BRIGHTNESS,
    CONF_WARN_TIMEOUT,
    CONF_WARN_TRANSITION,
    DATA_AUTO_OFF_ENABLED,
    DOMAIN,
    ENTITY_TYPE_LIGHT,
    ILLUMINANCE_MODE_CONTROL,
    ILLUMINANCE_MODE_GATE,
    SCHEDULE_MODE_FOLLOW,
    SCHEDULE_MODE_GATE,
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

_LOGGER = logging.getLogger(__name__)


def _opt_transition(value) -> float | None:
    """Configured transition → float seconds; absent/0 = don't send one."""
    return float(value) if value else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MoLight light entities from a config entry."""
    if entry.data[CONF_ENTITY_TYPE] == ENTITY_TYPE_LIGHT:
        entity = VirtualLight(hass, entry)
        if entity_id := suggested_entity_id(hass, entry, ENTITY_ID_FORMAT):
            entity.entity_id = entity_id
        async_add_entities([entity])


class VirtualLight(LightEntity, RestoreEntity):
    """A virtual light with occupancy/illuminance/schedule awareness."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        cfg = molight_config(entry)
        self._attr_name = cfg[CONF_NAME]
        self._attr_unique_id = entry.entry_id
        self._entry_id = entry.entry_id

        self._lights: list[str] = cfg.get(CONF_LIGHTS, [])
        self._light_timeout: int = int(cfg.get(CONF_LIGHT_TIMEOUT, 300))
        self._false_off_delay: int = int(cfg.get(CONF_FALSE_OFF_DELAY, 5))
        # Brightness (0-255) for automatic turn-ons, converted from the stored
        # percentage with HA's own percent→brightness scaling. None leaves
        # automatic turn-ons unqualified, as before.
        pct = cfg.get(CONF_AUTO_ON_BRIGHTNESS)
        self._auto_on_brightness: int | None = (
            round(percentage_to_ranged_value((1, 255), int(pct))) if pct else None
        )
        # True while the current on-period was started by occupancy (not by
        # the user) — the only case where a false-detection clear may cut the
        # lights short.
        self._occupancy_lit_lights: bool = False

        # Effect/warn warning sequence run at auto-off instead of an immediate
        # off. Timeouts of 0 disable each stage; effect_brightness is a 0-255
        # value (0 = blink fully off); warn_brightness is None to keep whatever
        # brightness the light had before the warning began.
        self._effect_timeout: int = int(cfg.get(CONF_EFFECT_TIMEOUT, 0))
        self._effect_brightness: int = round(
            percentage_to_ranged_value(
                (1, 255), int(cfg.get(CONF_EFFECT_BRIGHTNESS, 0))
            )
        )
        self._warn_timeout: int = int(cfg.get(CONF_WARN_TIMEOUT, 0))
        warn_pct = cfg.get(CONF_WARN_BRIGHTNESS)
        self._warn_brightness: int | None = (
            round(percentage_to_ranged_value((1, 255), int(warn_pct)))
            if warn_pct
            else None
        )
        # Optional fade times (seconds) for the service calls this light makes
        # itself: automatic turn-ons/offs and the effect/warn stage changes.
        # None (absent or 0) sends no transition attribute. Manual/physical
        # turn-ons and a manual off are never given a transition.
        self._auto_on_transition = _opt_transition(cfg.get(CONF_AUTO_ON_TRANSITION))
        self._auto_off_transition = _opt_transition(cfg.get(CONF_AUTO_OFF_TRANSITION))
        self._effect_transition = _opt_transition(cfg.get(CONF_EFFECT_TRANSITION))
        self._warn_transition = _opt_transition(cfg.get(CONF_WARN_TRANSITION))
        # Brightness the light had when the warning sequence began, restored on
        # any re-trigger so the effect/warn stages leave no lasting trace.
        # Exposed as the pre_warn_brightness attribute so it survives a
        # restart landing mid-warning.
        self._pre_warn_brightness: int | None = None

        self._occupancy_entity: str | None = cfg.get(CONF_OCCUPANCY_ENTITY)
        self._maintain_entity: str | None = cfg.get(CONF_MAINTAIN_OCCUPANCY_ENTITY)
        self._illuminance_entity: str | None = cfg.get(CONF_ILLUMINANCE_ENTITY)
        self._illuminance_mode: str = cfg.get(
            CONF_ILLUMINANCE_MODE, ILLUMINANCE_MODE_CONTROL
        )
        self._schedule_entity: str | None = cfg.get(CONF_SCHEDULE_ENTITY)
        self._schedule_mode: str = cfg.get(CONF_SCHEDULE_MODE, SCHEDULE_MODE_FOLLOW)
        self._hold_entities: list[str] = cfg.get(CONF_HOLD_ENTITIES, [])
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
        self._last_brightness_change_physical: datetime | None = None
        self._last_brightness_change_virtual: datetime | None = None

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
            # Restore turn-on attribution so the illuminance re-activation
            # countdown keeps working across a restart.
            for source in ("physical", "virtual", "occupancy", "illuminance"):
                raw = last.attributes.get(f"last_on_{source}")
                if raw:
                    try:
                        setattr(self, f"_last_on_{source}", datetime.fromisoformat(raw))
                    except (ValueError, TypeError):
                        pass
            self._schedule_window_applied = last.attributes.get("schedule_window_start")
            for attr, field in (
                # Fall back to the pre-rename attribute name for restores
                # from before the physical/virtual split.
                ("last_brightness_change_physical", "_last_brightness_change_physical"),
                ("last_brightness_change", "_last_brightness_change_physical"),
                ("last_brightness_change_virtual", "_last_brightness_change_virtual"),
            ):
                raw = last.attributes.get(attr)
                if raw and getattr(self, field) is None:
                    try:
                        setattr(self, field, datetime.fromisoformat(raw))
                    except (ValueError, TypeError):
                        pass
            raw_brightness = last.attributes.get(ATTR_BRIGHTNESS)
            if isinstance(raw_brightness, int):
                self._attr_brightness = raw_brightness
            # Non-null only when the last state was written mid effect/warn;
            # _seed_state then undoes the interrupted warning stage.
            raw_pre_warn = last.attributes.get("pre_warn_brightness")
            if isinstance(raw_pre_warn, int):
                self._pre_warn_brightness = raw_pre_warn

        watch = list(self._lights)
        if self._occupancy_entity:
            watch.append(self._occupancy_entity)
        if self._maintain_entity:
            watch.append(self._maintain_entity)
        if self._illuminance_entity:
            watch.append(self._illuminance_entity)
        if self._schedule_entity:
            watch.append(self._schedule_entity)
        watch.extend(self._hold_entities)
        # One entity may serve several roles — subscribe to it only once.
        watch = list(dict.fromkeys(watch))

        unsub_start: CALLBACK_TYPE | None = None

        @callback
        def _subscribe(_event=None) -> None:
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

    def _seed_state(self) -> None:
        """Initialise the machine state from current entity states after startup."""
        # Unavailable/unknown/missing keep-on entities count as not holding.
        self._hold_states = {
            e: (s := self.hass.states.get(e)) is not None and s.state == "on"
            for e in self._hold_entities
        }
        self._held = self._compute_held()

        # Brightness 0 counts as off, matching _all_lights_off.
        self._attr_is_on = any(
            (s := self.hass.states.get(e)) is not None
            and s.state == "on"
            and s.attributes.get("brightness") != 0
            for e in self._lights
        )

        # Match the physical brightness at startup too, overriding the value
        # restored from our own last state, so the virtual light always tracks
        # the real lights rather than a stale restored figure.
        if self._attr_is_on and (brightness := self._physical_brightness()):
            self._attr_brightness = brightness

        if self._pre_warn_brightness is not None:
            if self._attr_is_on:
                # The restart landed mid effect/warn with the lights still on:
                # undo the warning stage like any other re-trigger, then seed
                # normally (the warn-stage brightness must not be adopted).
                self._resume_lights()
            else:
                # The lights ended up off (e.g. mid blink-off) — treat the
                # auto-off as having completed; the room is not re-lit.
                self._pre_warn_brightness = None

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

        if self._attr_is_on and self._maintain_active():
            # Adopt an already-on light as maintained — no gating, since this
            # is not a turn-on; the maintain entity clearing starts the
            # countdown as usual.
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
        self._cancel_timer()

    # ------------------------------------------------------------------
    # LightEntity API
    # ------------------------------------------------------------------

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on all real lights and transition the state machine."""
        now = datetime.now(timezone.utc)
        self._last_on_virtual = now
        self._occupancy_lit_lights = False  # the user owns this on-period now
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is not None:
            self._last_brightness_change_virtual = now
            self._attr_brightness = brightness
        elif self._in_warning():
            # No explicit brightness: restore the pre-warning brightness so
            # the effect/warn stage leaves no trace, like any other re-trigger.
            brightness = self._pre_warn_brightness
            if brightness is not None:
                self._attr_brightness = brightness
        await self._set_lights(True, brightness=brightness)
        self._transition_on()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off all real lights and go idle."""
        await self._set_lights(False)
        self._go_idle()

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    @callback
    def _handle_state_change(self, event) -> None:
        """React to a tracked entity changing state."""
        entity_id: str = event.data["entity_id"]
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        same_state = old_state is not None and old_state.state == new_state.state

        if entity_id in self._lights:
            if event.context.id in self._self_context_ids:
                return  # echo of our own service call; call sites manage state
            if same_state:
                if new_state.state == "on":
                    self._on_light_brightness_change(old_state, new_state)
                return
            self._on_light_state_change(
                new_state.state, new_state.attributes.get("brightness")
            )
            return
        if same_state:
            return  # attribute-only change (battery, ...)
        # One entity may serve several roles (e.g. as both the occupancy and
        # the maintain entity), so the role checks are independent, not
        # exclusive.
        if entity_id == self._occupancy_entity:
            self._on_occupancy_change(new_state.state == "on")
        if entity_id == self._maintain_entity:
            self._on_maintain_change(new_state.state == "on")
        if entity_id == self._illuminance_entity:
            self._on_illuminance_change(new_state.state == "on")
        if entity_id == self._schedule_entity:
            self._on_schedule_change(new_state)
        if entity_id in self._hold_entities:
            self._hold_states[entity_id] = new_state.state == "on"
            self._refresh_hold()

    def _on_light_state_change(self, state: str, brightness: int | None = None) -> None:
        """Handle a real light being turned on/off externally."""
        if state == "on":
            # Mirror the real light's brightness so the virtual light always
            # matches it — including on this off→on adoption edge, not just on
            # later dims (which _on_light_brightness_change handles).
            if brightness:
                self._attr_brightness = brightness
            if self._machine_state == STATE_IDLE:
                self._last_on_physical = datetime.now(timezone.utc)
                self._occupancy_lit_lights = False  # the user owns this on-period
            self._transition_on()
        else:
            if self._all_lights_off():
                self._go_idle()

    def _on_light_brightness_change(self, old_state, new_state) -> None:
        """Handle an external brightness change on a real light (state stays on).

        Dimming is human activity: record it and restart any running
        countdown with the full timeout. Brightness 0 means off in disguise.
        """
        old_b = old_state.attributes.get("brightness")
        new_b = new_state.attributes.get("brightness")
        if new_b is None or new_b == old_b:
            return  # some other attribute changed

        self._last_brightness_change_physical = datetime.now(timezone.utc)
        if new_b:
            self._attr_brightness = new_b

        if new_b == 0:
            if self._all_lights_off():
                self._go_idle()
            else:
                self.async_write_ha_state()
            return

        if self._machine_state == STATE_IDLE:
            # 0 → non-zero while we're idle is a turn-on in disguise: run the
            # normal external turn-on logic (last_on_physical, ACTIVE/timer
            # or rejoining an active follow window).
            self._on_light_state_change("on")
            self.async_write_ha_state()
            return

        if self._machine_state in (
            STATE_ACTIVE,
            STATE_COUNTDOWN,
            STATE_EFFECT,
            STATE_WARN,
        ):
            # An external dim during the warning sequence is a re-trigger like
            # any other: honour the new brightness and restart the full timer.
            self._pre_warn_brightness = None
            self._machine_state = STATE_ACTIVE
            self._start_timer()
        self.async_write_ha_state()

    def _all_lights_off(self) -> bool:
        """True when every real light is off (brightness 0 counts as off)."""
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
            if state is not None and state.state == "on":
                if brightness := state.attributes.get("brightness"):
                    return brightness
        return None

    def _is_illuminance_bright(self) -> bool:
        """Return True when illuminance is bright enough to suppress lighting."""
        if not self._illuminance_entity:
            return False
        state = self.hass.states.get(self._illuminance_entity)
        return state is not None and state.state == "on"

    def _gate_schedule_inactive(self) -> bool:
        """True when a gate-mode schedule forbids activating the lights."""
        if not self._schedule_entity or self._schedule_mode != SCHEDULE_MODE_GATE:
            return False
        state = self.hass.states.get(self._schedule_entity)
        return not (state is not None and state.state == "on")

    def _follow_schedule_state(self):
        """The schedule entity's state when in follow mode and ON, else None."""
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
        conditions: a follow window that ended, being outside a gate window,
        or bright in control mode turn the lights off now; an active follow
        window or active occupancy keeps them on without a timer; otherwise
        a fresh full timer starts.
        """
        if not self._attr_is_on:
            return  # lights-off transitions were never suppressed

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

        if self._gate_schedule_inactive() or (
            self._is_illuminance_bright()
            and self._illuminance_mode == ILLUMINANCE_MODE_CONTROL
        ):
            self.hass.async_create_task(self._auto_lights_off())
            self._go_idle()
            return

        if self._occupancy_active() or self._maintain_active():
            self._machine_state = STATE_OCCUPIED
            return

        self._machine_state = STATE_ACTIVE
        self._start_timer()

    # ------------------------------------------------------------------
    # Schedule handling
    # ------------------------------------------------------------------

    def _on_schedule_change(self, new_state) -> None:
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

        # Gate mode: window end forces lights off; window start re-evaluates
        # occupancy the same way illuminance going dark does.
        if new_state.state != "on":
            if self._machine_state != STATE_IDLE and not self._held:
                self.hass.async_create_task(self._auto_lights_off())
                self._go_idle()
        else:
            if self._machine_state != STATE_IDLE:
                # Lights already on: the window opening lifts the gate that
                # kept already-active occupancy from holding them.
                if self._occupancy_holds():
                    self._adopt_active_occupancy()
                return
            occ_state = (
                self.hass.states.get(self._occupancy_entity)
                if self._occupancy_entity
                else None
            )
            if (
                occ_state
                and occ_state.state == "on"
                and not self._is_illuminance_bright()
            ):
                self._last_on_occupancy = datetime.now(timezone.utc)
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
                self._occupancy_lit_lights = True
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
            self._last_on_occupancy = datetime.now(timezone.utc)
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
        else:
            if self._machine_state == STATE_OCCUPIED:
                if self._maintain_active():
                    return  # maintain entity holds the light on
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
            if self._occupancy_active():
                return  # regular occupancy still holds the light on
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
        """True when the regular occupancy entity is configured and on."""
        if not self._occupancy_entity:
            return False
        state = self.hass.states.get(self._occupancy_entity)
        return state is not None and state.state == "on"

    def _occupancy_holds(self) -> bool:
        """True when already-active occupancy may hold an on light as OCCUPIED.

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
        """True when the maintain occupancy entity is configured and on."""
        if not self._maintain_entity:
            return False
        state = self.hass.states.get(self._maintain_entity)
        return state is not None and state.state == "on"

    def _occupancy_clear_was_false(self) -> bool:
        """True when the occupancy sensor flagged its clear as a false detection."""
        return self._clear_was_false(self._occupancy_entity)

    def _clear_was_false(self, entity_id: str | None) -> bool:
        """True when the given sensor flagged its clear as a false detection."""
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
                # already-active occupancy from holding them — adopt it so a
                # timer can't expire despite presence.
                if self._occupancy_holds():
                    self._adopt_active_occupancy()
                return
            if self._gate_schedule_inactive():
                return  # outside the schedule window — no activation

            # Check whether we should activate due to occupancy or recent history.
            occ_state = (
                self.hass.states.get(self._occupancy_entity)
                if self._occupancy_entity
                else None
            )
            if occ_state and occ_state.state == "on":
                self._last_on_illuminance = datetime.now(timezone.utc)
                self._machine_state = STATE_OCCUPIED
                self._cancel_timer()
                self._occupancy_lit_lights = True
                self.hass.async_create_task(self._auto_lights_on())
                self.async_write_ha_state()
            else:
                countdown = self._compute_illuminance_countdown()
                if countdown > 0:
                    self._last_on_illuminance = datetime.now(timezone.utc)
                    self.hass.async_create_task(
                        self._auto_lights_on()
                    )
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
        individual timeout is respected, then adds light_timeout on top as an
        extra grace period.
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
                try:
                    lots.append(datetime.fromisoformat(lot_str))
                except (ValueError, TypeError):
                    pass
        if lots:
            now = datetime.now(timezone.utc)
            remaining = (max(lots) - now).total_seconds()
            return max(0, int(base + remaining))
        return base

    def _most_recent_on_time(self) -> datetime | None:
        """Return the latest recorded turn-on timestamp across all sources
        except for illuminance (which is only relevant for gating, not attribution)."""
        candidates = [
            t
            for t in (
                self._last_on_physical,
                self._last_on_virtual,
                self._last_on_occupancy,
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
            elapsed = (datetime.now(timezone.utc) - last_on).total_seconds()
            return max(0, int(self._light_timeout - elapsed))
        return self._light_timeout

    def _transition_on(self) -> None:
        """Move to ACTIVE (or stay OCCUPIED/SCHEDULED) when lights come on."""
        # A manual/physical turn-on ends any warning sequence; the caller has
        # already set the real lights, so just drop the restore snapshot.
        self._pre_warn_brightness = None
        if self._machine_state in (STATE_OCCUPIED, STATE_SCHEDULED):
            return  # already managed by occupancy / schedule window

        # Turned back on during an active follow-mode window (after a manual
        # off): rejoin the window instead of running the auto-off timer, so
        # the light stays on until the window ends.
        sched = self._follow_schedule_state()
        if sched is not None:
            self._attr_is_on = True
            self._apply_window_start(sched.attributes.get("current_window_start"))
            return

        # Turned on while the maintain entity — or the regular occupancy
        # entity, when not gated by bright/window — is already occupied: hold
        # the light immediately instead of running a timer that would expire
        # despite presence.
        if self._maintain_active() or self._occupancy_holds():
            self._machine_state = STATE_OCCUPIED
            self._attr_is_on = True
            self._cancel_timer()
            self.async_write_ha_state()
            return

        self._machine_state = STATE_ACTIVE
        self._attr_is_on = True
        # Restart even when already ACTIVE: turning on / dimming again is
        # activity and extends the on-period.
        self._start_timer()
        self.async_write_ha_state()

    def _go_idle(self) -> None:
        """Cancel any timer and move to IDLE."""
        self._cancel_timer()
        self._machine_state = STATE_IDLE
        self._attr_is_on = False
        self._occupancy_lit_lights = False
        self._pre_warn_brightness = None
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
        """True while showing the effect or warn stage before auto-off."""
        return self._machine_state in (STATE_EFFECT, STATE_WARN)

    def _begin_warning(self) -> None:
        """Auto-off is due: start the effect→warn warning sequence.

        Snapshots the current brightness (restored if the user re-triggers or
        reused by a warn stage with no brightness of its own). Falls through to
        the warn stage — and to a plain off — when the earlier stage is
        disabled, so both timeouts at 0 behaves exactly like the old immediate
        off.
        """
        self._pre_warn_brightness = self._attr_brightness
        if self._effect_timeout > 0:
            self._machine_state = STATE_EFFECT
            self.hass.async_create_task(
                self._set_stage_lights(self._effect_brightness, self._effect_transition)
            )
            self._start_timer(self._effect_timeout)
            self.async_write_ha_state()
            return
        self._enter_warn()

    def _enter_warn(self) -> None:
        """Advance to the WARN grace period, or turn the lights off when it is
        disabled."""
        if self._warn_timeout > 0:
            self._machine_state = STATE_WARN
            brightness = (
                self._warn_brightness
                if self._warn_brightness is not None
                else self._pre_warn_brightness
            ) or 255
            self.hass.async_create_task(
                self._set_stage_lights(brightness, self._warn_transition)
            )
            self._start_timer(self._warn_timeout)
            self.async_write_ha_state()
            return
        self.hass.async_create_task(self._auto_lights_off())
        self._go_idle()

    def _resume_lights(self) -> None:
        """Restore the real lights to their pre-warning brightness when a
        re-trigger interrupts the effect/warn sequence. The caller sets the
        resulting machine state. No transition: the restore must be as
        immediate as the re-trigger that caused it."""
        brightness = self._pre_warn_brightness
        self._pre_warn_brightness = None
        self.hass.async_create_task(self._set_lights(True, brightness=brightness))

    async def _set_stage_lights(
        self, brightness: int, transition: float | None = None
    ) -> None:
        """Drive the real lights for an effect/warn stage while the virtual
        light stays logically on. Brightness 0 blinks the real lights off."""
        context = Context()
        self._self_context_ids.append(context.id)
        transition_data = (
            {ATTR_TRANSITION: transition} if transition is not None else {}
        )
        if brightness:
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {
                    "entity_id": self._lights,
                    ATTR_BRIGHTNESS: brightness,
                    **transition_data,
                },
                blocking=False,
                context=context,
            )
            self._attr_brightness = brightness
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

    def _auto_lights_on(self):
        """Coroutine turning the real lights on for an automatic trigger,
        with the configured auto-on brightness and transition."""
        return self._set_lights(
            True,
            brightness=self._auto_on_brightness,
            transition=self._auto_on_transition,
        )

    def _auto_lights_off(self):
        """Coroutine turning the real lights off for an automatic turn-off,
        with the configured auto-off transition. Manual offs bypass this."""
        return self._set_lights(False, transition=self._auto_off_transition)

    async def _set_lights(
        self,
        on: bool,
        brightness: int | None = None,
        transition: float | None = None,
    ) -> None:
        context = Context()
        self._self_context_ids.append(context.id)
        service_data: dict = {"entity_id": self._lights}
        if transition is not None:
            service_data[ATTR_TRANSITION] = transition
        if on and brightness is not None:
            service_data[ATTR_BRIGHTNESS] = brightness
            # Mirror the commanded brightness so the virtual light reports it
            # (the real-light echo is ignored as a self-caused change).
            self._attr_brightness = brightness
        await self.hass.services.async_call(
            "light",
            "turn_on" if on else "turn_off",
            service_data,
            blocking=False,
            context=context,
        )
        self._attr_is_on = on
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Extra state attributes
    # ------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict:
        def _fmt(t: datetime | None) -> str | None:
            return t.isoformat() if t else None

        return {
            "molight_state": self._machine_state,
            "auto_off_held": self._held,
            "last_on_physical": _fmt(self._last_on_physical),
            "last_on_virtual": _fmt(self._last_on_virtual),
            "last_on_occupancy": _fmt(self._last_on_occupancy),
            "last_on_illuminance": _fmt(self._last_on_illuminance),
            "last_brightness_change_physical": _fmt(
                self._last_brightness_change_physical
            ),
            "last_brightness_change_virtual": _fmt(
                self._last_brightness_change_virtual
            ),
            # Non-null only while the effect/warn stage is showing; persisted
            # so a restart mid-warning can restore the pre-warning brightness.
            "pre_warn_brightness": self._pre_warn_brightness,
            "schedule_window_start": self._schedule_window_applied,
        }
