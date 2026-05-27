# Limer — Smart Virtual Entities for Home Assistant

A [HACS](https://hacs.xyz) custom integration that provides composable virtual building blocks for lighting automation.

## Entities

### Virtual Occupancy Binary Sensor

Synthesises occupancy from multiple real sensors.

| Config | Description |
|---|---|
| **Trigger sensors** | Any one going `on` starts occupancy |
| **Maintain sensors** *(optional)* | Keep occupancy alive once started, but cannot start it alone |
| **Timeout (s)** | Countdown begins when all sensors are `off`; occupancy clears when it expires |

Reusable — multiple virtual lights can reference the same occupancy sensor.

---

### Virtual Illuminance Binary Sensor

`on` = below threshold (dark enough to warrant lighting)  
`off` = above threshold (bright enough, no lighting needed)

| Config | Description |
|---|---|
| **Illuminance sensor** | Any real `sensor` with `device_class: illuminance` |
| **Threshold (lx)** | The lux level below which the sensor reports `on` |

---

### Virtual Schedule Binary Sensor

`on` = current time is within an active window.  
Overnight windows (e.g. 22:00 → 06:00) are supported.

| Config | Description |
|---|---|
| **Time windows** | One or more `{ start: HH:MM, end: HH:MM }` windows |

---

### Virtual Light

Controls N real lights with an occupancy-aware state machine.

| Config | Description |
|---|---|
| **Lights** | Real `light` entities to control |
| **Turn-off timeout (s)** | Must be >= the occupancy timeout of any referenced occupancy entity |
| **Occupancy sensor** *(optional)* | A Limer Virtual Occupancy Binary Sensor |
| **Illuminance sensor** *(optional)* | A Limer Virtual Illuminance Binary Sensor |
| **Schedule sensor** *(optional)* | A Limer Virtual Schedule Binary Sensor |

#### State machine

```
IDLE
  |  light on OR occupancy triggered (no active occupancy)
  v
ACTIVE  <-- occupancy clears while OCCUPIED -- OCCUPIED
  |  (timer running)                              |  (no timer)
  |                                     occupancy active
  |  timer expires                               |
  v                                              |
IDLE <--------------------------------------------+
  ^
  +---- light turned off externally (any state)
```

> **Note:** Illuminance and schedule gating behaviour (hands-off vs force-off
> when state changes while lights are on and occupied) is TBD.

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS.
2. Install **Limer**.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and search for **Limer**.

### Manual

Copy `custom_components/limer/` into your HA config `custom_components/` directory and restart.

## Development

```bash
# Install test dependencies
pip install -r requirements_test.txt

# Run tests
pytest
```

## Design notes

- Each virtual entity is its own config entry, so they can be created, edited, and removed independently.
- Virtual occupancy sensors must be created before they can be referenced in a virtual light.
- The virtual light validates that `light_timeout >= occupancy_timeout` at creation and edit time (config flow + options flow).
