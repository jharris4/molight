Home Assistant Integration — Project Summary
Virtual Entities (building blocks)
Virtual Occupancy Binary Sensor
N trigger sensors — any on starts and maintains occupancy
N maintain sensors (optional) — only maintain once triggered, never start
Configurable timeout: countdown starts when all sensors are off
Reusable across multiple light groups
Virtual Illuminance Binary Sensor
References a real illuminance sensor + configurable threshold
on = below threshold (dark enough to warrant lighting)
Reusable across multiple light groups
Virtual Schedule Binary Sensor
Configurable time windows
on = currently within an active window
Reusable across multiple light groups
Virtual Light Group
Controls N real lights directly
Optionally references virtual occupancy, illuminance, and/or schedule entities
Configurable turn-off timeout
Constraint: timeout ≥ occupancy timeout (enforced in config flow both ways)
State Machine
Illuminance and schedule gating behavior TBD — needs further design (hands off vs
force off, behavior when state changes while lights are on/occupied)
Light turned on or occupancy triggered (no active occupancy) → start timer immediately
Occupancy active → no timer, cancel any running timer
Occupancy clears → start countdown
Timer expires → turn off real lights → idle
Light turned off externally → idle
Config Flow Rules
Virtual occupancy must exist before referencing it in a light group
Light timeout validated against occupancy timeout at creation and edit (both directions)