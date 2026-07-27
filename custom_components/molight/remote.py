"""Virtual Remote — drive lights from remote-control button event entities.

A Virtual Remote config entry's runtime lives here rather than in an entity
(its only entity is the diagnostic Last Action sensor, see sensor.py). It
subscribes to the state changes of the `event` entities Home Assistant
exposes per physical button (a Lutron Pico button, an IKEA Bilresa button,
...) and turns each configured (button, click) pair into one service call on
the target lights, announcing each execution on SIGNAL_REMOTE_ACTION.
Routing through the light domain's public services means a press on a
MoLight virtual light behaves exactly like a dashboard tap: never gated by
darkness or a schedule, cancels an effect/warn sequence, restarts the timer.

Single vs. double click is resolved per button from the event entity's
advertised `event_types`, because the same physical click is spelled
differently per ecosystem — and a button that supports multi-press announces
one click with several events (initial_press, short_release, multi_press_1),
of which only one may fire the binding:

  - multi-press capable (Matter with MSM, e.g. Bilresa): a completed single
    click is `multi_press_1`, a double is `multi_press_2`; the constituent
    initial_press/short_release events are ignored.
  - Zigbee2MQTT-style: literal `single` / `double`.
  - Lutron Pico: `press` (its `release` is ignored). No double click.
  - Hue-style / Matter without MSM: `short_release` (preferred over
    `initial_press`, which also precedes a long press). No double click.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.event import ATTR_EVENT_TYPE, ATTR_EVENT_TYPES
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CoreState, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DIM_STEP,
    CONF_TARGET_LIGHTS,
    DEFAULT_DIM_STEP,
    REMOTE_ACTION_BRIGHTNESS_DOWN,
    REMOTE_ACTION_BRIGHTNESS_UP,
    REMOTE_ACTION_FIELDS,
    REMOTE_ACTION_OFF,
    REMOTE_ACTION_TOGGLE,
    REMOTE_PRESET_VALUE_KEYS,
    SIGNAL_REMOTE_ACTION,
)
from .helpers import molight_config

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
    )

CLICK_SINGLE = "single"
CLICK_DOUBLE = "double"

# Event types announcing a completed click, in priority order: only the first
# type a button advertises counts, so a multi-press button's short_release
# never double-fires alongside its multi_press_1.
_SINGLE_EVENT_TYPES = (
    "multi_press_1",
    "single",
    "press",
    "short_release",
    "initial_press",
)
_DOUBLE_EVENT_TYPES = ("multi_press_2", "double")


def single_click_event_type(event_types: list[str]) -> str | None:
    """Return the event_type meaning a completed single click on this button."""
    return next((t for t in _SINGLE_EVENT_TYPES if t in event_types), None)


def double_click_event_type(event_types: list[str]) -> str | None:
    """Return the event_type meaning a double click, or None if unsupported."""
    return next((t for t in _DOUBLE_EVENT_TYPES if t in event_types), None)


def entity_double_click_supported(hass: HomeAssistant, entity_id: str) -> bool | None:
    """Whether this event entity can report a double click; None if unknown.

    Resolved from the advertised event_types of the entity's current state,
    so the config flow can reject a double-click binding on a button that
    will never emit one (a Pico). An absent state can't be judged — the
    binding is allowed and simply never fires until the entity proves itself.
    """
    state = hass.states.get(entity_id)
    if state is None:
        return None
    event_types = state.attributes.get(ATTR_EVENT_TYPES) or []
    return double_click_event_type(event_types) is not None


def _action_call(
    action: str, cfg: dict[str, Any], dim_step: int
) -> tuple[str, dict[str, Any]] | None:
    """Map an action slot to its light-domain (service, extra data) call.

    Returns None for a preset with no values configured — such a binding
    would be a plain turn-on pretending to be a preset, so it stays inert
    (the config flow rejects the combination anyway).
    """
    if action == REMOTE_ACTION_OFF:
        return ("turn_off", {})
    if action == REMOTE_ACTION_TOGGLE:
        return ("toggle", {})
    if action == REMOTE_ACTION_BRIGHTNESS_UP:
        return ("turn_on", {"brightness_step_pct": dim_step})
    if action == REMOTE_ACTION_BRIGHTNESS_DOWN:
        return ("turn_on", {"brightness_step_pct": -dim_step})
    if action in REMOTE_PRESET_VALUE_KEYS:
        brightness_key, color_temp_key, rgb_key = REMOTE_PRESET_VALUE_KEYS[action]
        data: dict[str, Any] = {}
        if cfg.get(brightness_key) is not None:
            data["brightness_pct"] = int(cfg[brightness_key])
        if cfg.get(color_temp_key):
            data["color_temp_kelvin"] = int(cfg[color_temp_key])
        elif cfg.get(rgb_key):
            data["rgb_color"] = [int(c) for c in cfg[rgb_key]]
        if not data:
            return None
        return ("turn_on", data)
    return ("turn_on", {})  # REMOTE_ACTION_ON


@callback
def async_setup_remote(hass: HomeAssistant, entry: ConfigEntry) -> CALLBACK_TYPE:
    """Wire up a Virtual Remote entry; returns its teardown callback.

    Subscribing is deferred until Home Assistant has fully started, exactly
    like the virtual light's sensor subscriptions: at startup event entities
    are restored carrying their last pre-shutdown event, which must never be
    replayed as a fresh press.
    """
    cfg = molight_config(entry)
    targets: list[str] = cfg.get(CONF_TARGET_LIGHTS, [])
    dim_step = int(cfg.get(CONF_DIM_STEP, DEFAULT_DIM_STEP))

    # (button entity_id, click) -> (action, service, service data)
    bindings: dict[tuple[str, str], tuple[str, str, dict[str, Any]]] = {}
    for single_key, double_key, action in REMOTE_ACTION_FIELDS:
        call = _action_call(action, cfg, dim_step)
        if call is None:
            continue
        for entity_id in cfg.get(single_key) or []:
            bindings[(entity_id, CLICK_SINGLE)] = (action, *call)
        for entity_id in cfg.get(double_key) or []:
            bindings[(entity_id, CLICK_DOUBLE)] = (action, *call)

    watch = sorted({entity_id for entity_id, _ in bindings})

    @callback
    def _noop() -> None:
        return

    if not watch or not targets:
        return _noop

    @callback
    def _handle_event(event: Event[EventStateChangedData]) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        if old_state is None or old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            # The entity just appeared or recovered — its state carries its
            # last (possibly restored, certainly stale) event; never replay it.
            return
        if old_state.state == new_state.state:
            return  # attribute-only write, not a new button event
        event_type = new_state.attributes.get(ATTR_EVENT_TYPE)
        supported = new_state.attributes.get(ATTR_EVENT_TYPES) or []
        if event_type == single_click_event_type(supported):
            click = CLICK_SINGLE
        elif event_type == double_click_event_type(supported):
            click = CLICK_DOUBLE
        else:
            return
        binding = bindings.get((event.data["entity_id"], click))
        if binding is None:
            return
        action, service, data = binding
        hass.async_create_task(
            hass.services.async_call(
                "light", service, {"entity_id": targets, **data}, blocking=False
            )
        )
        # Announce the executed binding to the entry's Last Action sensor.
        async_dispatcher_send(
            hass,
            SIGNAL_REMOTE_ACTION.format(entry.entry_id),
            {
                "action": action,
                "button": event.data["entity_id"],
                "click": click,
                "event_type": event_type,
                "when": dt_util.utcnow(),
            },
        )

    unsub_state: CALLBACK_TYPE | None = None
    unsub_start: CALLBACK_TYPE | None = None

    @callback
    def _subscribe(_event: Event | None = None) -> None:
        # When fired via async_listen_once, HA has already removed that
        # one-time listener; drop our reference so teardown doesn't try to
        # remove it a second time.
        nonlocal unsub_start, unsub_state
        unsub_start = None
        unsub_state = async_track_state_change_event(hass, watch, _handle_event)

    if hass.state is CoreState.running:
        _subscribe()
    else:
        unsub_start = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED, _subscribe
        )

    @callback
    def _teardown() -> None:
        nonlocal unsub_start, unsub_state
        if unsub_start is not None:
            unsub_start()
            unsub_start = None
        if unsub_state is not None:
            unsub_state()
            unsub_state = None

    return _teardown
