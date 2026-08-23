"""Constants for the MoLight end-to-end testbed."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "molight_testbed"
PLATFORMS: Final = ["binary_sensor", "event", "light", "select", "sensor"]

SERVICE_SET_STATE: Final = "set_state"
SERVICE_SET_AVAILABLE: Final = "set_available"
SERVICE_FIRE_EVENT: Final = "fire_event"
SERVICE_SET_BEHAVIOR: Final = "set_behavior"
SERVICE_SET_STARTUP_DELAY: Final = "set_startup_delay"
ATTR_SECONDS: Final = "seconds"
ATTR_BEHAVIOR: Final = "behavior"
ATTR_AVAILABLE: Final = "available"
ATTR_EVENT_TYPE: Final = "event_type"

DATA_CONTROLLER: Final = "controller"
STORAGE_KEY: Final = f"{DOMAIN}.states"
STORAGE_VERSION: Final = 1

# How a simulated light reports back after a command; defaults are instant and
# exact, the knobs emulate slow, piecewise, stepwise, or fuzzy real bulbs.
DEFAULT_BEHAVIOR: Final = {
    "latency": 0.0,  # seconds before the first report after a command
    "report_steps": False,  # report power first, attributes 0.3 s later
    "transition_steps": 0,  # intermediate brightness reports across a fade
    "brightness_levels": 0,  # quantise reported brightness to N device levels
    "xy_color": False,  # advertise and report XY instead of RGB
}

LIGHT_MAIN: Final = "light_main"
LIGHT_TIMER: Final = "light_timer"
LIGHT_CT: Final = "light_ct"
LIGHT_MULTI_ON_OFF: Final = "light_multi_on_off"
LIGHT_MULTI_DIMMER: Final = "light_multi_dimmer"
LIGHT_MULTI_RGB: Final = "light_multi_rgb"
MOTION: Final = "motion"
MOTION_REMOVAL: Final = "motion_removal"
OCCUPANCY: Final = "occupancy"
DOOR: Final = "door"
SCHEDULE: Final = "schedule"
ILLUMINANCE: Final = "illuminance"
TARGET_SELECT: Final = "target_select"
SOURCE_SELECT: Final = "source_select"
EVENT_BUTTON: Final = "event_button"

DEFAULT_STATES: Final = {
    LIGHT_MAIN: {
        "state": "off",
        "available": True,
        "attributes": {"brightness": 0, "rgb_color": [255, 255, 255]},
    },
    LIGHT_TIMER: {
        "state": "off",
        "available": True,
        "attributes": {"brightness": 0},
    },
    LIGHT_CT: {
        "state": "off",
        "available": True,
        "attributes": {"brightness": 0, "color_temp_kelvin": 3000},
    },
    LIGHT_MULTI_ON_OFF: {"state": "off", "available": True, "attributes": {}},
    LIGHT_MULTI_DIMMER: {
        "state": "off",
        "available": True,
        "attributes": {"brightness": 0},
    },
    LIGHT_MULTI_RGB: {
        "state": "off",
        "available": True,
        "attributes": {"brightness": 0, "rgb_color": [255, 255, 255]},
    },
    MOTION: {"state": "off", "available": True, "attributes": {}},
    MOTION_REMOVAL: {"state": "off", "available": True, "attributes": {}},
    OCCUPANCY: {"state": "off", "available": True, "attributes": {}},
    DOOR: {"state": "off", "available": True, "attributes": {}},
    SCHEDULE: {"state": "off", "available": True, "attributes": {}},
    ILLUMINANCE: {"state": 5.0, "available": True, "attributes": {}},
    TARGET_SELECT: {"state": "Cozy", "available": True, "attributes": {}},
    SOURCE_SELECT: {"state": "Focus", "available": True, "attributes": {}},
    EVENT_BUTTON: {"state": None, "available": True, "attributes": {}},
}
