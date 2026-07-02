Home Assistant Integration — Project Summary
Virtual Entities (building blocks)
Virtual Occupancy Binary Sensor
N trigger sensors — any on starts and maintains occupancy
N maintain sensors (optional) — only maintain once triggered, never start
Configurable timeout: countdown starts when all sensors are off
Reusable across multiple light groups
Virtual Illuminance Binary Sensor
References a real illuminance sensor + configurable threshold
off = below threshold (dark enough to warrant lighting)
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

#General Description:

this project is a home assistant integration that i'm building to control lights.

The idea is that you can create virtual occupancy sensors to configure the timeouts for real occupancy sensors, so that you can tell how long occupancy has really been clear for. Like if a virtual occupancy sensor clears and has a timeout of 60 seconds, you know the real occupancy seensor cleared 60 seconds ago.

Then, you can configure timeouts for virtual lights that are tied to real lights. Virtual lights can be configured to turn off after a set amount of time either after they were turned on manually, or after occupancy has cleared (using the virtual occupancy sensor timeout to make this more precise).

Additionally, there are virtual illuminance sensors that can be used to control when lights turn on/off based on how dark it is.

There are also virtual schedule sensors, which can be used to only turn lights on/off based on scheduled times, but this hasn't been implemented yet,