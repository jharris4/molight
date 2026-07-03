# MoLight — Smart Virtual Light Control Entities for Home Assistant

A [HACS](https://hacs.xyz) custom integration that provides composable virtual building blocks for lighting automation.

## Motivation and Use Cases

### Motivation

Writing Home Assistant automations that turn lights on and off based on timers, occupancy, illumination, or a schedule involves a lot of tedious manual work — and the complexity grows fast.

It gets drastically worse once you account for real-world sensor behavior: occupancy sensors have varying hold timeouts, and some are better suited to *triggering* occupancy while others are only reliable for *maintaining* it. Combining several occupancy sensors into one coherent signal is especially painful to do by hand.

Schedules bring their own headaches — lights that should follow windows mixing fixed times and sun state ("the later of sunset − 15 min and 21:00"), or lights that should only turn on when it's actually dark outside.

And on top of all that, it's genuinely hard to write custom automations that gracefully restore correct light state after a brief or prolonged Home Assistant restart, or that don't misbehave when a sensor drops to `unavailable`.

MoLight solves all of these problems with a small set of composable virtual entities and a simple, easy-to-use interface.

### Use cases

Everything below is covered by the automated test suite.

**Occupancy-driven lighting**

- Turn lights on when a room becomes occupied, and off a configurable timeout after the person actually left — the countdown is anchored to the sensor's own hold time (`latest_occupied_time`), not the moment it happens to clear.
- Renewed occupancy during the off-countdown cancels the timer.
- Occupancy takes over a manually turned-on light, so it still turns off after the room empties.
- False detections (a fly or heat blip) are classified and counted; lights lit by a false cycle turn off after a short delay instead of the full countdown — but lights the user turned on manually are never cut short.

**Combining multiple occupancy sensors**

- Merge several motion/presence sensors into one occupancy signal, with each constituent's individual timeout respected.
- Distinguish *trigger* sensors (can start occupancy) from *maintain* sensors (keep it alive but can't start it) — e.g. a PIR triggers while a sensitive mmWave sensor maintains.
- Reuse one occupancy sensor (simple or combined) across multiple virtual lights.

**Only when it's dark**

- Gate turn-ons on ambient light: occupancy only lights the room when the lux sensor says it's dark.
- Getting dark while the room is occupied turns the lights on; getting dark mid-countdown re-lights them for only the remaining portion of the on-period.
- Getting bright can force lights off (`control` mode) or leave turn-off to occupancy/timeout (`gate` mode — for lux sensors that can see the controlled lights and would otherwise oscillate).
- Hysteresis suppresses flapping when the light level hovers around the threshold.

**Schedules**

- Follow mode: porch-light behavior — on at window start, off at window end, with edges defined by fixed times, sun events with offsets, or a combination of both. Overnight windows work.
- Gate mode: occupancy may only activate lights inside the window, and window end forces them off.
- A manual off mid-window is respected; turning the light back on rejoins the window instead of starting a timer.

**Keeping lights on (guest mode, parties, movie night)**

- Every virtual light comes with a companion **Auto-off** switch — flip it off from a dashboard or voice assistant and the light stays on until you flip it back.
- Point one or more *keep-on entities* (an `input_boolean`, a guest-mode switch, anything with an on/off state) at any number of virtual lights: while any of them is on, those lights won't turn off automatically. One shared toggle can hold the whole house; a per-room toggle holds just that room.
- Releasing the hold returns the light to normal behavior: a schedule window that ended or brightness that arrived in the meantime turns it off, active occupancy keeps it on, and otherwise a fresh countdown starts.

**Manual control always works**

- The user can always turn the virtual light on — even when it's bright or outside a schedule window.
- External changes to the real lights (wall switch, another automation) are adopted; the virtual light stays on until *all* of its real lights are off.
- Brightness changes count as human activity and restart a running timer; brightness `0` is treated as off.

**Restarts and unavailable sources**

- After a restart, lights that were left on are adopted with a fresh timer, active occupancy is re-claimed, and a follow-mode window boundary missed while HA was down is applied exactly once — while a manual off from before the restart is respected.
- `unavailable`/`unknown` is never misread as a state change at any layer: an occupancy, illuminance, schedule, or real-light blip recovers cleanly, and an unavailable lux sensor never reads as "it got dark".
- A motion sensor that dies while occupancy is active can't hold the lights on forever — the *clear after unavailable* timeout releases them via the normal gentle countdown.

## How To Use This Integration

Once installed (see [Installation](#installation)), everything is configured from the UI — no YAML required.

1. Go to **Settings → Devices & Services → Add Integration** and search for **MoLight**. Each time you add the integration you create one config entry for one virtual entity, and you pick which kind to create.
2. Create whichever virtual sensors you want your lights to react to — all of them are optional:
   - a **Virtual Occupancy Binary Sensor** for each real motion/presence sensor you want to use,
   - a **Virtual Combined Occupancy Binary Sensor** to merge several of them,
   - a **Virtual Illuminance Binary Sensor** (dark/bright) and/or a **Virtual Schedule Binary Sensor** (time/sun windows).
3. Then create a **Virtual Light** for each room or light group, pointing it at the real `light` entities and optionally referencing any of the virtual sensors from step 2. A virtual light with no sensors at all is still useful — it turns its real lights off on a timer.

The order matters: a virtual entity must already exist before another one can reference it — occupancy, illuminance, and schedule sensors before the virtual light that uses them, and simple occupancy sensors before a combined sensor that merges them. Sensors are reusable, so one occupancy or illuminance sensor can serve several virtual lights.

Finally, use the virtual light instead of the real lights in your dashboards and voice assistants — turning it on and off controls the real lights, and all the automatic behavior comes along for free. Each virtual light also comes with a companion **Auto-off** switch: flip it off to keep the lights on (movie night, guests) and back on to resume normal behavior. Each entry can be edited or removed independently later via its **Configure** button.

See [Entities](#entities) below for the full description of each entity type and its configuration options.

## Entities

### Virtual Occupancy Binary Sensor

Wraps a single real binary sensor (motion, presence, door…). `on` mirrors the source directly; the value the rest of the system runs on is the `latest_occupied_time` attribute — the best estimate of when the person actually left.

| Config | Description |
|---|---|
| **Sensor** | The real `binary_sensor` to wrap |
| **Timeout (s)** | The source's own hold time. When it clears, `latest_occupied_time` is back-dated to `clear time − timeout` |
| **False-detection grace (s)** | `0` disables. A cycle whose on-duration exceeds the timeout by no more than the grace contained exactly one instantaneous detection — almost certainly a fly/heat blip. Such cycles don't advance `latest_occupied_time`, are counted in `false_detection_count`, and flag the clear via `last_clear_false_detection` so lights can turn off quickly |
| **Clear after unavailable (s)** | `0` disables, default `60`. If the source goes `unavailable`/`unknown` while occupancy is active, `latest_occupied_time` advances to the dropout moment immediately, and if the source hasn't recovered after this many seconds the occupancy clears, flagged via `last_clear_unavailable`. Never classified as a false detection — the room may still be occupied, so dependent lights run their normal gentle countdown. A recovery cancels the pending clear |

Attributes: `latest_occupied_time`, `occupancy_timeout`, `last_on_time`, `last_clear_false_detection`, `false_detection_count`, `last_clear_unavailable`.

---

### Virtual Combined Occupancy Binary Sensor

Combines multiple Virtual Occupancy Sensors into one.

| Config | Description |
|---|---|
| **Trigger sensors** | Any one going `on` starts occupancy |
| **Maintain sensors** *(optional)* | Keep occupancy alive once started, but cannot start it alone |

`latest_occupied_time` is the max across all constituents, so each sub-sensor's individual timeout is respected. A combined cycle during which no constituent advanced `latest_occupied_time` was made up entirely of false cycles and is flagged/counted the same way as on the simple sensor.

After a restart, a *maintain* sensor that has already been `on` for more than 5 seconds is assumed to reflect occupancy that was triggered before HA went down, and seeds the sensor `on` (the one exception to "maintain sensors never start occupancy").

Reusable — multiple virtual lights can reference the same occupancy sensor (simple or combined).

---

### Virtual Illuminance Binary Sensor

`on` = at or above the threshold (bright enough, no artificial lighting needed)  
`off` = below the threshold (dark enough to warrant lighting)

| Config | Description |
|---|---|
| **Illuminance sensor** | Any real `sensor` with `device_class: illuminance` |
| **Threshold (lx)** | The lux level at which the sensor reports `on` |
| **Hysteresis (lx)** | `0` disables. Becomes bright at `threshold + hysteresis`, dark below `threshold − hysteresis`; readings inside the band hold the current state, suppressing flapping when the light level hovers around the threshold |

An unavailable or unparsable source holds the last known value — a lux sensor dropping out must not read as "it got dark". The state also survives restarts.

---

### Virtual Schedule Binary Sensor

`on` = current time is within an active window. Transitions fire at the exact boundary (no polling). Overnight windows (e.g. 22:00 → 06:00) are supported.

| Config | Description |
|---|---|
| **Time windows** | One or more `{ start: <edge>, end: <edge> }` windows |

Each edge is a fixed time, a sun event, or both:

```yaml
start:
  time: "21:00"        # fixed local time
  sun: sunset          # or sunrise
  offset: -15          # minutes relative to the sun event
  combine: latest      # latest | earliest — which of time/sun wins
end: "07:00"           # plain string = fixed time
```

e.g. *start at the later of sunset−15 min and 21:00*. On polar days where the sun event doesn't occur, the fixed time stands alone.

Attributes: `current_window_start` (identifies the active window; used by follow-mode lights for restart catch-up), `next_transition`.

---

### Virtual Light

Controls N real lights with an occupancy-aware state machine.

| Config | Description |
|---|---|
| **Lights** | Real `light` entities to control |
| **Turn-off timeout (s)** | Must be >= the occupancy timeout of any referenced occupancy entity |
| **False-detection off delay (s)** | When occupancy clears flagged as a false detection, lights that were lit *by that cycle* turn off after this short delay instead of the normal countdown. Lights turned on manually are never affected |
| **Occupancy sensor** *(optional)* | A MoLight occupancy sensor (simple or combined) |
| **Illuminance sensor** *(optional)* | A MoLight Virtual Illuminance Binary Sensor |
| **Illuminance mode** | `control` — dark gates turn-ons AND turning bright forces the lights off. `gate` — dark gates turn-ons only; bright never turns lights off. Use `gate` when the lux sensor can see the controlled lights, which would otherwise oscillate |
| **Schedule sensor** *(optional)* | A MoLight Virtual Schedule Binary Sensor |
| **Schedule mode** | `follow` — lights turn on at window start and off at window end (porch lights). `gate` — occupancy may only activate lights inside the window; window end forces lights off |
| **Keep-on entities** *(optional)* | Any entities with an on/off state. While any of them is `on`, auto-off is held (see below) |

#### State machine

```
IDLE       lights off, no timer
ACTIVE     lights on, timer running (manual/external turn-on, no occupancy)
OCCUPIED   lights on, occupancy active — timer suspended
COUNTDOWN  occupancy cleared, timer ticking toward lights-off
SCHEDULED  lights on inside a follow-mode window — no timer
```

- `IDLE` + manual/external turn-on → `ACTIVE` (timer starts)
- `IDLE`/`ACTIVE` + occupancy becomes active (and it's dark / in-window) → `OCCUPIED`
- `OCCUPIED` + occupancy clears → `COUNTDOWN`; the timer is the turn-off timeout anchored to the sensor's `latest_occupied_time`, so each sensor's hold time is respected
- `ACTIVE`/`COUNTDOWN` + timer expires → `IDLE` (real lights turned off)
- any state + all real lights turned off externally → `IDLE`
- follow-mode window start → `SCHEDULED`; occupancy and illuminance are ignored until the window ends. Boundaries are edge-triggered, so manual changes mid-window stand — including turning the light back on, which rejoins the window instead of starting a timer

Manual control is never gated: the user can always turn the virtual light on, even when it's bright or outside a schedule window.

#### Holding auto-off

Each virtual light also creates a companion **`<name> Auto-off` switch** entity. Auto-off is *held* while that switch is off **or** any configured keep-on entity is on:

- Every automatic turn-off is suspended — the timer, the false-detection quick off, bright-forces-off, and schedule window ends. The state machine keeps transitioning; it just never arms a timer.
- Turn-ons are unaffected (occupancy, going dark, and window starts still light the room), and a manual off always works.
- When the last hold releases, the light re-evaluates its rules: a follow window that ended while held turns it off now, as does being outside a gate window or bright in `control` mode; active occupancy keeps it on; an active follow window keeps it `SCHEDULED`; otherwise a **fresh full timer** starts.
- A keep-on entity dropping to `unavailable`/`unknown` holds its last known value (a dead toggle never reads as "hold released" — or engaged). The switch state survives restarts, and a held light adopted at startup won't start a timer.

Share one keep-on entity (e.g. `input_boolean.guest_mode`) across all your virtual lights for a global "don't touch the lights" toggle, or give a single room its own. The current hold status is exposed as the `auto_off_held` attribute.

#### Illuminance interplay

- Occupancy only turns lights on while it's dark.
- Dark while occupied turns the lights back on (`OCCUPIED`); dark with countdown time remaining re-lights them for only the *remaining* portion of the on-period.
- Bright forces lights off in `control` mode; in `gate` mode the timeout/occupancy handle turning off.

#### Brightness

The virtual light supports brightness. External brightness changes on the real lights count as human activity and restart a running timer; brightness `0` is treated as off (and `0 → non-zero` as a turn-on). Physical and virtual changes are tracked separately (`last_brightness_change_physical` / `_virtual`).

#### Attribution

`last_on_physical`, `last_on_virtual`, `last_on_occupancy`, `last_on_illuminance` record when and why the light last activated (exposed as attributes, restored across restarts). The current machine state is exposed as `molight_state`.

#### Restarts and unavailability

- Real lights already on at startup are adopted (`ACTIVE` with a fresh timer); active occupancy (when dark / in-window) is claimed as `OCCUPIED`.
- Follow-mode windows use `schedule_window_start` as a marker: a boundary missed while HA was down is applied exactly once at startup, while a manual off mid-window is respected.
- Entities dropping to `unavailable`/`unknown` are never read as state changes, at any layer; recovery transitions are processed as real events. A source sensor that stays unavailable is handled by the occupancy sensor's *clear after unavailable* timeout (see above), so a dead motion sensor can't hold lights on forever.

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS.
2. Install **MoLight**.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and search for **MoLight**.

### Manual

Copy `custom_components/molight/` into your HA config `custom_components/` directory and restart.

## Development

Tests run inside the devcontainer:

```bash
npm run up      # start the devcontainer
npm test        # pytest inside the container
```

`npm run hass` starts a docker-compose HA instance for manual testing. It binds the same port (8123) as the devcontainer, so only one can run at a time.

## Design notes

- Each virtual entity is its own config entry, so they can be created, edited, and removed independently.
- Virtual occupancy sensors must be created before they can be referenced in a virtual light.
- The `light_timeout >= occupancy_timeout` constraint is validated in both directions: creating/editing a light checks its referenced occupancy entity (including through a combined sensor), and raising an occupancy sensor's timeout checks every light that depends on it.
- Once an entry's options have been edited, the options fully replace the original data (so cleared optional fields stay cleared).
