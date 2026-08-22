"""Constants for the MoLight end-to-end testbed."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "molight_testbed"
PLATFORMS: Final = ["binary_sensor", "event", "light", "select", "sensor"]

SERVICE_SET_STATE: Final = "set_state"
SERVICE_SET_AVAILABLE: Final = "set_available"
SERVICE_FIRE_EVENT: Final = "fire_event"
ATTR_AVAILABLE: Final = "available"
ATTR_EVENT_TYPE: Final = "event_type"

DATA_CONTROLLER: Final = "controller"
STORAGE_KEY: Final = f"{DOMAIN}.states"
STORAGE_VERSION: Final = 1

LIGHT_MAIN: Final = "light_main"
LIGHT_ON_OFF: Final = "light_on_off"
MOTION: Final = "motion"
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
    LIGHT_ON_OFF: {"state": "off", "available": True, "attributes": {}},
    MOTION: {"state": "off", "available": True, "attributes": {}},
    OCCUPANCY: {"state": "off", "available": True, "attributes": {}},
    DOOR: {"state": "off", "available": True, "attributes": {}},
    SCHEDULE: {"state": "off", "available": True, "attributes": {}},
    ILLUMINANCE: {"state": 5.0, "available": True, "attributes": {}},
    TARGET_SELECT: {"state": "Cozy", "available": True, "attributes": {}},
    SOURCE_SELECT: {"state": "Focus", "available": True, "attributes": {}},
    EVENT_BUTTON: {"state": None, "available": True, "attributes": {}},
}
