"""Constants for the MoLight integration."""

DOMAIN = "molight"

PLATFORMS = ["binary_sensor", "light", "sensor", "switch"]

# Entity type discriminator stored in config entry data
ENTITY_TYPE_OCCUPANCY = "occupancy"
ENTITY_TYPE_COMBINED_OCCUPANCY = "combined_occupancy"
ENTITY_TYPE_ILLUMINANCE = "illuminance"
ENTITY_TYPE_SCHEDULE = "schedule"
ENTITY_TYPE_LIGHT = "light"
ENTITY_TYPE_REMOTE = "remote"

ENTITY_TYPES = [
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_SCHEDULE,
    ENTITY_TYPE_LIGHT,
    ENTITY_TYPE_REMOTE,
]

# --- Shared config keys ---
CONF_ENTITY_TYPE = "entity_type"
CONF_NAME = "name"
# Optional explicit object_id for the entity this entry creates, applied as a
# suggested object_id at first registration (HA still uniquifies with _2 on a
# clash). Absent = derive the entity_id from the name, as usual. Immutable, so
# it lives only in entry.data and is never edited by the options flow — read it
# straight from entry.data (molight_config would drop it once options exist).
CONF_ENTITY_ID = "entity_id"

# Discovery filter step, shown before the entity checklist: optional area and
# label multiselects narrow the candidates (an entity matches through its own
# registry assignment or its device's), and a preselect toggle decides whether
# the checklist starts with everything checked (bulk-add) or nothing (pick a
# few). None of these are stored on created entries.
CONF_FILTER_AREAS = "filter_areas"
CONF_FILTER_LABELS = "filter_labels"
CONF_PRESELECT_ALL = "preselect_all"

# Multiselect field in the discovery config-flow steps: the real entities the
# user picked to wrap in virtual MoLight entities (created with defaults).
CONF_SELECTED_ENTITIES = "selected_entities"
# Optional text prepended/appended to each discovered entity, applied verbatim
# (so include your own separator, e.g. a trailing/leading space). The affix
# target selects what it shapes: the friendly name, or the entity_id only
# (leaving the name identical to the wrapped entity).
CONF_AFFIX_PREFIX = "affix_prefix"
CONF_AFFIX_SUFFIX = "affix_suffix"
CONF_AFFIX_TARGET = "affix_target"
AFFIX_TARGET_ENTITY_ID = "entity_id"
AFFIX_TARGET_NAME = "name"
AFFIX_TARGETS = [AFFIX_TARGET_ENTITY_ID, AFFIX_TARGET_NAME]

# Bulk-assign config-flow steps: attach one virtual sensor to many virtual
# lights at once. The chosen sensor and (for occupancy) its role are picked
# first; the second step lists all virtual lights, pre-selecting those that
# already reference the sensor. The submitted set is the source of truth —
# deselecting a pre-selected light removes the reference.
CONF_ASSIGN_SENSOR = "assign_sensor"  # the virtual sensor entity_id to assign
CONF_ASSIGN_ROLE = "assign_role"  # occupancy only: regular vs maintain
CONF_ASSIGN_LIGHTS = "assign_lights"  # target virtual light entity_ids
ASSIGN_ROLE_REGULAR = "regular"
ASSIGN_ROLE_MAINTAIN = "maintain"
ASSIGN_ROLES = [ASSIGN_ROLE_REGULAR, ASSIGN_ROLE_MAINTAIN]

# --- Virtual Occupancy Binary Sensor (simple — one real sensor) ---
CONF_OCCUPANCY_SENSOR = "occupancy_sensor"  # entity_id of the real binary_sensor
# latest_occupied_time is computed as last_off minus this timeout
CONF_OCCUPANCY_TIMEOUT = "occupancy_timeout"
DEFAULT_OCCUPANCY_TIMEOUT = 120
# A cycle whose on-duration exceeds the timeout by no more than this many
# seconds contained exactly one instantaneous detection (the sensor never
# re-triggered during its hold time) — almost always a false detection.
# Such cycles don't advance latest_occupied_time and are counted in the
# false_detection_count attribute. 0 disables classification.
CONF_FALSE_DETECTION_GRACE = "false_detection_grace"
DEFAULT_FALSE_DETECTION_GRACE = 3
# When the source sensor is unavailable/unknown for this many seconds while
# occupancy is active, treat it as occupancy having cleared (the room may
# still be occupied, so the clear is flagged via last_clear_unavailable and
# never classified as a false detection). latest_occupied_time is advanced
# to the dropout moment immediately. 0 disables — occupancy then holds its
# last value for as long as the source is unavailable.
CONF_CLEAR_ON_UNAVAILABLE_TIMEOUT = "clear_on_unavailable_timeout"
DEFAULT_CLEAR_ON_UNAVAILABLE_TIMEOUT = 60

# --- Virtual Combined Occupancy Binary Sensor ---
# trigger_sensors: any one going ON starts occupancy
CONF_TRIGGER_SENSORS = "trigger_sensors"
# maintain_sensors: keep occupancy alive once started, but cannot start it
CONF_MAINTAIN_SENSORS = "maintain_sensors"

# --- Virtual Illuminance Binary Sensor ---
CONF_ILLUMINANCE_SENSOR = "illuminance_sensor"  # entity_id of a real sensor
CONF_ILLUMINANCE_THRESHOLD = "illuminance_threshold"  # on = value >= threshold (bright)
DEFAULT_ILLUMINANCE_THRESHOLD = 10.0
# Hysteresis band (lx) around the threshold: becomes bright at
# threshold + hysteresis, dark below threshold - hysteresis; readings inside
# the band hold the current state. 0 = plain comparator.
CONF_ILLUMINANCE_HYSTERESIS = "illuminance_hysteresis"
DEFAULT_ILLUMINANCE_HYSTERESIS = 0.0

# --- Virtual Schedule Binary Sensor ---
# List of {"start": <edge>, "end": <edge>} dicts. An edge is either a plain
# "HH:MM" string (legacy) or a dict:
#   {"time": "HH:MM", "sun": "sunset"|"sunrise", "offset": <minutes>,
#    "combine": "latest"|"earliest"}
# with at least one of time/sun present. offset shifts the sun event
# (negative = before it). When both time and sun are present, combine picks
# which wins (e.g. start at the later of sunset-15min and 21:00).
CONF_TIME_WINDOWS = "time_windows"
EDGE_TIME = "time"
EDGE_SUN = "sun"
EDGE_OFFSET = "offset"
EDGE_COMBINE = "combine"
COMBINE_LATEST = "latest"
COMBINE_EARLIEST = "earliest"
SUN_EVENTS = ["sunset", "sunrise"]

# --- Virtual Light ---
CONF_LIGHTS = "lights"  # list of real light entity_ids
CONF_LIGHT_TIMEOUT = "light_timeout"  # seconds; must be >= occupancy_timeout
DEFAULT_LIGHT_TIMEOUT = 300
# Brightness (percent, 1-100) applied when the light is turned on
# *automatically* — by occupancy, a door opening, illuminance going dark, or
# a schedule window. Manual and physical turn-ons are never affected (they
# keep whatever brightness the user/last state set). Absent = don't set a
# brightness on automatic turn-ons either (the real lights use their own
# last/default).
CONF_AUTO_ON_BRIGHTNESS = "auto_on_brightness"
# Optional color applied when the light is turned on *automatically*, exactly
# like auto_on_brightness (manual and physical turn-ons are never affected).
# At most one may be set (the config/options flows enforce it):
#   auto_on_color_temp — white color temperature in Kelvin
#   auto_on_rgb_color  — an [r, g, b] color
# Members that can't show the color get only the brightness — Home Assistant
# filters/converts color parameters per real light.
CONF_AUTO_ON_COLOR_TEMP = "auto_on_color_temp"
CONF_AUTO_ON_RGB_COLOR = "auto_on_rgb_color"
# Optional select entity and option applied immediately before MoLight turns an
# off Virtual Light on. This is intentionally generic rather than WLED-specific:
# WLED presets are exposed as select entities, and the same mechanism works for
# any integration with a select-backed scene/preset. Both keys must be present;
# absent = no turn-on selection. Physical member-light turn-ons are not affected.
CONF_TURN_ON_SELECT_ENTITY = "turn_on_select_entity"
CONF_TURN_ON_SELECT_OPTION = "turn_on_select_option"
# When occupancy clears and the sensor flags the cycle as a false detection,
# lights that were lit BY that cycle turn off after this short delay instead
# of the normal countdown. Lights turned on manually are never affected.
CONF_FALSE_OFF_DELAY = "false_detection_off_delay"
DEFAULT_FALSE_OFF_DELAY = 5
# Effect/warn warning sequence before an automatic turn-off. When the auto-off
# timer expires the light can flag the impending off before going dark:
#   EFFECT — a brief attention cue (a blink/dip to effect_brightness), shown
#            for effect_timeout seconds. effect_brightness is a percent 0-100
#            where 0 blinks the real lights fully off.
#   WARN   — a grace period at warn_brightness for warn_timeout seconds, then
#            the lights turn off. warn_brightness is an optional percent 1-100;
#            absent = keep whatever brightness was in effect before the warning.
# Each stage is skipped when its timeout is 0 (both default 0 = feature off,
# so the light turns straight off exactly as before). Any re-trigger during
# either stage (occupancy, manual/physical on, a dim, ...) cancels it and
# behaves as if the pre-off timer were still running, restoring the brightness.
CONF_EFFECT_TIMEOUT = "effect_timeout"
DEFAULT_EFFECT_TIMEOUT = 0
CONF_EFFECT_BRIGHTNESS = "effect_brightness"
DEFAULT_EFFECT_BRIGHTNESS = 0
CONF_WARN_TIMEOUT = "warn_timeout"
DEFAULT_WARN_TIMEOUT = 0
CONF_WARN_BRIGHTNESS = "warn_brightness"
# Optional [r, g, b] colors for the warning stages — e.g. a red warn stage is
# a much clearer "lights about to go off" cue than a dim. Color-capable
# members show the color; brightness-only members just show the stage
# brightness. effect_rgb_color requires effect_brightness > 0 (a blink fully
# off has no color to show; enforced by the config/options flows). Any
# re-trigger restores the pre-warning brightness AND color.
CONF_EFFECT_RGB_COLOR = "effect_rgb_color"
CONF_WARN_RGB_COLOR = "warn_rgb_color"
# Optional fade times (seconds) sent as the transition of the service calls
# the virtual light makes itself. Absent/0 = no transition attribute is sent
# (the real lights use their own default).
#   auto_on_transition  — automatic turn-ons only (occupancy, a door opening,
#                         illuminance going dark, a window start); manual and
#                         physical turn-ons are untouched.
#   auto_off_transition — automatic turn-offs (timer expiry, false-detection
#                         quick off, bright-forces-off, window end); a manual
#                         off is untouched.
#   effect_transition   — fade into the effect stage's brightness.
#   warn_transition     — fade into the warn stage's brightness.
# A stage fade must fit inside its stage: effect_transition <= effect_timeout
# and warn_transition <= warn_timeout (enforced by the config/options flows,
# which also rejects a fade on a disabled stage since its timeout is 0).
CONF_AUTO_ON_TRANSITION = "auto_on_transition"
CONF_AUTO_OFF_TRANSITION = "auto_off_transition"
CONF_EFFECT_TRANSITION = "effect_transition"
CONF_WARN_TRANSITION = "warn_transition"
# Optional references to virtual entities (entity_ids)
CONF_OCCUPANCY_ENTITY = "occupancy_entity"
# Maintain occupancy entity: keeps an already-on light on while it is on, but
# never turns the light on. Unlike the combined sensor's maintain_sensors
# (which only extend occupancy started by a trigger sensor), this holds the
# light regardless of how it was lit — manual, physical, or occupancy.
CONF_MAINTAIN_OCCUPANCY_ENTITY = "maintain_occupancy_entity"
CONF_ILLUMINANCE_ENTITY = "illuminance_entity"
CONF_SCHEDULE_ENTITY = "schedule_entity"
# Keep-on entities: while ANY of these entities is "on", every automatic
# turn-off (timer expiry, false-detection quick off, bright-forces-off,
# schedule window end) is suspended. Turn-ons and manual control are never
# affected. When the last hold releases the light re-evaluates its rules.
CONF_HOLD_ENTITIES = "hold_entities"

# Each Virtual Light entry also creates a companion "<name> Auto-off" switch;
# turning it OFF holds the light the same way a keep-on entity does. The
# switch mirrors its state into hass.data[DOMAIN][entry_id] and notifies the
# light via this dispatcher signal (formatted with the entry_id).
DATA_AUTO_OFF_ENABLED = "auto_off_enabled"
SIGNAL_AUTO_OFF_TOGGLED = DOMAIN + "_auto_off_toggled_{}"

# How a referenced illuminance entity affects the light:
#   control — dark gates turn-ons AND turning bright forces the lights off
#   gate    — dark gates turn-ons only; bright never turns the lights off.
#             Use when the lux sensor can see the controlled lights, which
#             would otherwise oscillate (lights on → bright → forced off →
#             dark → on ...).
CONF_ILLUMINANCE_MODE = "illuminance_mode"
ILLUMINANCE_MODE_CONTROL = "control"
ILLUMINANCE_MODE_GATE = "gate"
ILLUMINANCE_MODES = [ILLUMINANCE_MODE_CONTROL, ILLUMINANCE_MODE_GATE]
DEFAULT_ILLUMINANCE_MODE = ILLUMINANCE_MODE_CONTROL

# How a referenced schedule entity affects the light:
#   follow — lights turn on at window start and off at window end (porch lights)
#   gate   — occupancy may only activate lights inside the window
CONF_SCHEDULE_MODE = "schedule_mode"
SCHEDULE_MODE_FOLLOW = "follow"
SCHEDULE_MODE_GATE = "gate"
SCHEDULE_MODES = [SCHEDULE_MODE_FOLLOW, SCHEDULE_MODE_GATE]
DEFAULT_SCHEDULE_MODE = SCHEDULE_MODE_FOLLOW

# Optional real door/contact binary_sensor (on = open) that drives the light.
# Opening the door is a turn-on trigger, gated by illuminance/schedule exactly
# like occupancy (only lights up when dark and inside a gate-mode window).
# door_mode controls what the door state does after that:
#   open       — opening turns the lights on with the normal timeout; the door
#                is otherwise ignored (closing does nothing). A momentary
#                trigger, like a light switch that pops back out.
#   open_close — the open door holds the lights on (no timer) while it stays
#                open, and closing starts the auto-off countdown, deferring to
#                occupancy/keep-on holds (a closed door never cuts the lights
#                over someone the occupancy sensor still sees).
CONF_DOOR_ENTITY = "door_entity"
CONF_DOOR_MODE = "door_mode"
DOOR_MODE_OPEN = "open"
DOOR_MODE_OPEN_CLOSE = "open_close"
DOOR_MODES = [DOOR_MODE_OPEN, DOOR_MODE_OPEN_CLOSE]
DEFAULT_DOOR_MODE = DOOR_MODE_OPEN

# --- Virtual Remote ---
# A Virtual Remote entry drives one or more lights from the buttons of a
# remote control (a Lutron Pico, an IKEA Bilresa, ...). Buttons are the
# `event` entities Home Assistant creates per physical button; every binding
# is a (button, click) -> action pair, stored as the flat keys below. Presses
# execute through the light domain's public services, so a bound press on a
# MoLight virtual light gets full manual-control semantics (never gated,
# cancels a warning sequence, restarts the timer).
#
# Which event_type string means a single vs. a double click differs per
# ecosystem and is resolved per button from the event entity's advertised
# event_types (see remote.py).
CONF_TARGET_LIGHTS = "target_lights"  # light entity_ids the buttons control
# Percent step applied per brightness up/down press (one step per click; a
# step below the minimum turns the light off, per the light domain's own
# brightness_step handling — and a step on an off light turns it on dim).
CONF_DIM_STEP = "dim_step"
DEFAULT_DIM_STEP = 10

CONF_ON_BUTTONS_SINGLE = "on_buttons_single"
CONF_ON_BUTTONS_DOUBLE = "on_buttons_double"
CONF_OFF_BUTTONS_SINGLE = "off_buttons_single"
CONF_OFF_BUTTONS_DOUBLE = "off_buttons_double"
CONF_TOGGLE_BUTTONS_SINGLE = "toggle_buttons_single"
CONF_TOGGLE_BUTTONS_DOUBLE = "toggle_buttons_double"
CONF_BRIGHTNESS_UP_BUTTONS_SINGLE = "brightness_up_buttons_single"
CONF_BRIGHTNESS_UP_BUTTONS_DOUBLE = "brightness_up_buttons_double"
CONF_BRIGHTNESS_DOWN_BUTTONS_SINGLE = "brightness_down_buttons_single"
CONF_BRIGHTNESS_DOWN_BUTTONS_DOUBLE = "brightness_down_buttons_double"
# Presets turn the lights on at a fixed brightness and/or color — the Pico's
# favorite button. The color temperature and RGB color are mutually
# exclusive, like the virtual light's auto-on color pair.
CONF_PRESET_1_BUTTONS_SINGLE = "preset_1_buttons_single"
CONF_PRESET_1_BUTTONS_DOUBLE = "preset_1_buttons_double"
CONF_PRESET_1_BRIGHTNESS = "preset_1_brightness"
CONF_PRESET_1_COLOR_TEMP = "preset_1_color_temp"
CONF_PRESET_1_RGB_COLOR = "preset_1_rgb_color"
CONF_PRESET_2_BUTTONS_SINGLE = "preset_2_buttons_single"
CONF_PRESET_2_BUTTONS_DOUBLE = "preset_2_buttons_double"
CONF_PRESET_2_BRIGHTNESS = "preset_2_brightness"
CONF_PRESET_2_COLOR_TEMP = "preset_2_color_temp"
CONF_PRESET_2_RGB_COLOR = "preset_2_rgb_color"

# Bindable actions. The values double as the form's section keys and
# translation keys, so they must never collide with a flat CONF_* key.
REMOTE_ACTION_ON = "turn_on"
REMOTE_ACTION_OFF = "turn_off"
REMOTE_ACTION_TOGGLE = "toggle"
REMOTE_ACTION_BRIGHTNESS_UP = "brightness_up"
REMOTE_ACTION_BRIGHTNESS_DOWN = "brightness_down"
REMOTE_ACTION_PRESET_1 = "preset_1"
REMOTE_ACTION_PRESET_2 = "preset_2"

# (single-click key, double-click key, action) per bindable action slot —
# the one table the config flow, the runtime, and the cleanup all iterate.
REMOTE_ACTION_FIELDS = (
    (CONF_ON_BUTTONS_SINGLE, CONF_ON_BUTTONS_DOUBLE, REMOTE_ACTION_ON),
    (CONF_OFF_BUTTONS_SINGLE, CONF_OFF_BUTTONS_DOUBLE, REMOTE_ACTION_OFF),
    (CONF_TOGGLE_BUTTONS_SINGLE, CONF_TOGGLE_BUTTONS_DOUBLE, REMOTE_ACTION_TOGGLE),
    (
        CONF_BRIGHTNESS_UP_BUTTONS_SINGLE,
        CONF_BRIGHTNESS_UP_BUTTONS_DOUBLE,
        REMOTE_ACTION_BRIGHTNESS_UP,
    ),
    (
        CONF_BRIGHTNESS_DOWN_BUTTONS_SINGLE,
        CONF_BRIGHTNESS_DOWN_BUTTONS_DOUBLE,
        REMOTE_ACTION_BRIGHTNESS_DOWN,
    ),
    (
        CONF_PRESET_1_BUTTONS_SINGLE,
        CONF_PRESET_1_BUTTONS_DOUBLE,
        REMOTE_ACTION_PRESET_1,
    ),
    (
        CONF_PRESET_2_BUTTONS_SINGLE,
        CONF_PRESET_2_BUTTONS_DOUBLE,
        REMOTE_ACTION_PRESET_2,
    ),
)

# Each Virtual Remote entry also creates a diagnostic "<name> Last Action"
# sensor. The runtime announces every executed binding on this dispatcher
# signal (formatted with the entry_id); the payload is a dict with the
# executed action, the source button entity_id, the resolved click, the raw
# event_type, and the time.
SIGNAL_REMOTE_ACTION = DOMAIN + "_remote_action_{}"

# Preset action -> its (brightness, color temp, rgb color) value keys.
REMOTE_PRESET_VALUE_KEYS = {
    REMOTE_ACTION_PRESET_1: (
        CONF_PRESET_1_BRIGHTNESS,
        CONF_PRESET_1_COLOR_TEMP,
        CONF_PRESET_1_RGB_COLOR,
    ),
    REMOTE_ACTION_PRESET_2: (
        CONF_PRESET_2_BRIGHTNESS,
        CONF_PRESET_2_COLOR_TEMP,
        CONF_PRESET_2_RGB_COLOR,
    ),
}

# --- Virtual Light state machine states ---
STATE_IDLE = "idle"
STATE_ACTIVE = "active"  # lights on, timer running (no occupancy / not occupied)
STATE_OCCUPIED = "occupied"  # lights on, occupancy active — no countdown
STATE_COUNTDOWN = "countdown"  # occupancy cleared, timer ticking before lights-off
STATE_SCHEDULED = "scheduled"  # lights on, inside a follow-mode window — no timer
STATE_EFFECT = "effect"  # auto-off imminent — brief effect/blink stage
STATE_WARN = "warn"  # auto-off imminent — grace period before lights-off
