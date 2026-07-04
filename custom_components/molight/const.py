"""Constants for the MoLight integration."""

DOMAIN = "molight"

PLATFORMS = ["binary_sensor", "light", "switch"]

# Entity type discriminator stored in config entry data
ENTITY_TYPE_OCCUPANCY = "occupancy"
ENTITY_TYPE_COMBINED_OCCUPANCY = "combined_occupancy"
ENTITY_TYPE_ILLUMINANCE = "illuminance"
ENTITY_TYPE_SCHEDULE = "schedule"
ENTITY_TYPE_LIGHT = "light"

ENTITY_TYPES = [
    ENTITY_TYPE_OCCUPANCY,
    ENTITY_TYPE_COMBINED_OCCUPANCY,
    ENTITY_TYPE_ILLUMINANCE,
    ENTITY_TYPE_SCHEDULE,
    ENTITY_TYPE_LIGHT,
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

# --- Virtual Occupancy Binary Sensor (simple — one real sensor) ---
CONF_OCCUPANCY_SENSOR = "occupancy_sensor"   # entity_id of the real binary_sensor
# latest_occupied_time = last_off - timeout
CONF_OCCUPANCY_TIMEOUT = "occupancy_timeout"
# A cycle whose on-duration exceeds the timeout by no more than this many
# seconds contained exactly one instantaneous detection (the sensor never
# re-triggered during its hold time) — almost always a false detection.
# Such cycles don't advance latest_occupied_time and are counted in the
# false_detection_count attribute. 0 disables classification.
CONF_FALSE_DETECTION_GRACE = "false_detection_grace"
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
CONF_ILLUMINANCE_SENSOR = "illuminance_sensor"   # entity_id of a real sensor
CONF_ILLUMINANCE_THRESHOLD = "illuminance_threshold"  # on = value >= threshold (bright)
# Hysteresis band (lx) around the threshold: becomes bright at
# threshold + hysteresis, dark below threshold - hysteresis; readings inside
# the band hold the current state. 0 = plain comparator.
CONF_ILLUMINANCE_HYSTERESIS = "illuminance_hysteresis"

# --- Virtual Schedule Binary Sensor ---
# List of {"start": <edge>, "end": <edge>} dicts. An edge is either a plain
# "HH:MM" string (legacy) or a dict:
#   {"time": "HH:MM", "sun": "sunset"|"sunrise", "offset": <minutes>,
#    "combine": "latest"|"earliest"}
# with at least one of time/sun present. offset shifts the sun event
# (negative = before it). When both time and sun are present, combine picks
# which wins (e.g. start at the later of sunset−15min and 21:00).
CONF_TIME_WINDOWS = "time_windows"
EDGE_TIME = "time"
EDGE_SUN = "sun"
EDGE_OFFSET = "offset"
EDGE_COMBINE = "combine"
COMBINE_LATEST = "latest"
COMBINE_EARLIEST = "earliest"
SUN_EVENTS = ["sunset", "sunrise"]

# --- Virtual Light ---
CONF_LIGHTS = "lights"                             # list of real light entity_ids
CONF_LIGHT_TIMEOUT = "light_timeout"  # seconds; must be >= occupancy_timeout
# When occupancy clears and the sensor flags the cycle as a false detection,
# lights that were lit BY that cycle turn off after this short delay instead
# of the normal countdown. Lights turned on manually are never affected.
CONF_FALSE_OFF_DELAY = "false_detection_off_delay"
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

# How a referenced schedule entity affects the light:
#   follow — lights turn on at window start and off at window end (porch lights)
#   gate   — occupancy may only activate lights inside the window
CONF_SCHEDULE_MODE = "schedule_mode"
SCHEDULE_MODE_FOLLOW = "follow"
SCHEDULE_MODE_GATE = "gate"
SCHEDULE_MODES = [SCHEDULE_MODE_FOLLOW, SCHEDULE_MODE_GATE]

# --- Virtual Light state machine states ---
STATE_IDLE = "idle"
STATE_ACTIVE = "active"        # lights on, timer running (no occupancy / not occupied)
STATE_OCCUPIED = "occupied"    # lights on, occupancy active — no countdown
STATE_COUNTDOWN = "countdown"  # occupancy cleared, timer ticking before lights-off
STATE_SCHEDULED = "scheduled"  # lights on, inside a follow-mode window — no timer
