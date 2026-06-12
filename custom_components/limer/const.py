"""Constants for the Limer integration."""

DOMAIN = "limer"

PLATFORMS = ["binary_sensor", "light"]

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

# --- Virtual Occupancy Binary Sensor (simple — one real sensor) ---
CONF_OCCUPANCY_SENSOR = "occupancy_sensor"   # entity_id of the real binary_sensor
# latest_occupied_time = last_off - timeout
CONF_OCCUPANCY_TIMEOUT = "occupancy_timeout"

# --- Virtual Combined Occupancy Binary Sensor ---
# trigger_sensors: any one going ON starts occupancy
CONF_TRIGGER_SENSORS = "trigger_sensors"
# maintain_sensors: keep occupancy alive once started, but cannot start it
CONF_MAINTAIN_SENSORS = "maintain_sensors"

# --- Virtual Illuminance Binary Sensor ---
CONF_ILLUMINANCE_SENSOR = "illuminance_sensor"   # entity_id of a real sensor
CONF_ILLUMINANCE_THRESHOLD = "illuminance_threshold"  # on = below threshold

# --- Virtual Schedule Binary Sensor ---
# List of {"start": "HH:MM", "end": "HH:MM"} dicts
CONF_TIME_WINDOWS = "time_windows"

# --- Virtual Light ---
CONF_LIGHTS = "lights"                             # list of real light entity_ids
CONF_LIGHT_TIMEOUT = "light_timeout"              # seconds; must be >= occupancy_timeout
# Optional references to virtual entities (entity_ids)
CONF_OCCUPANCY_ENTITY = "occupancy_entity"
CONF_ILLUMINANCE_ENTITY = "illuminance_entity"
CONF_SCHEDULE_ENTITY = "schedule_entity"

# --- Virtual Light state machine states ---
STATE_IDLE = "idle"
STATE_ACTIVE = "active"        # lights on, timer running (no occupancy sensor / not occupied)
STATE_OCCUPIED = "occupied"    # lights on, occupancy active — no countdown
STATE_COUNTDOWN = "countdown"  # occupancy cleared, timer ticking before lights-off
