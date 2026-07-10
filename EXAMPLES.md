# MoLight — configuration examples

MoLight is configured entirely from the UI, so these examples show the actual field values you'd type for common scenarios — from the simplest case to the full toolbox. Every create form starts with a **Name** (the entity ID derives from it) and ends with an optional **Entity ID** override if you want to pin it.

The examples build on each other:

1. [Just a turn-off timer](#example-1--just-a-turn-off-timer-the-simplest-case) — no sensors at all
2. [Simple occupancy](#example-2--simple-occupancy-case) — one motion sensor
3. [Living room](#example-3--living-room-the-whole-toolbox) — occupancy + maintain + illuminance + a warning blink
4. [Porch light](#example-4--porch-light-schedule-follow-mode) — a schedule window
5. [Pantry light](#example-5--pantry-light-door-sensor) — a door/contact sensor

See the [README](README.md#entity-reference) for the full field reference.

---

### Example 1 — Just a turn-off timer (the simplest case)

No sensors at all — a virtual light that turns its lights off a set time after they come on. Great for a closet, pantry, or garage where you just want "never left on all day." One entry.

**Virtual Light**

```text
Name:              Pantry                       # → light.pantry
Lights:            light.pantry_real            # the real light it controls
Turn-off timeout:  300                          # off 5 min after it's turned on
```

Turn it on (app, voice, or wall switch) → 5 minutes later it turns itself off. That's the whole thing.

---

### Example 2 — Simple occupancy case

One motion sensor, lights off a bit after you leave. Two entries.

**1. Virtual Occupancy Sensor** — wraps the real motion sensor

```text
Name:                 Hallway Occupancy        # → binary_sensor.hallway_occupancy
Source sensor:        binary_sensor.hallway_motion
Occupancy timeout:    30      # my sensor holds "on" ~30s after last motion
False-detection grace: 5      # a single instantaneous blip = fly/heat, ignored
Clear after unavailable: 60   # if the sensor dies, don't hold lights forever
```

**2. Virtual Light** — points at the real light, references the sensor above

```text
Name:              Hallway                     # → light.hallway  (pin the Entity ID
Lights:            light.hallway_real          #    field if it clashes with the real one)
Turn-off timeout:  60         # 60s after you actually left
Occupancy sensor:  binary_sensor.hallway_occupancy
```

Walk in → lights on. Room empties → 60s countdown anchored to when you *actually left* (not when the sensor cleared) → off. Turn it on by hand and it still turns off after the room empties, but a false blip never cuts short a manual on.

> Note the guard: **Turn-off timeout (60) must be ≥ the occupancy timeout (30)**. MoLight enforces this both ways.

---

### Example 3 — Living room (the whole toolbox)

Motion for triggering, an mmWave sensor to *hold* while you sit still, only when it's dark, and a warning blink before it drops you into darkness.

**1. Virtual Occupancy Sensor** — the *trigger* (regular PIR)

```text
Name:               Living Occupancy           # → binary_sensor.living_occupancy
Source sensor:      binary_sensor.living_motion
Occupancy timeout:  30
```

**2. Virtual Occupancy Sensor** — the mmWave, used only to *maintain*

```text
Name:               Living Presence            # → binary_sensor.living_presence
Source sensor:      binary_sensor.living_mmwave
Occupancy timeout:  120
```

(mmWave is too twitchy to *start* occupancy, but perfect for keeping the room lit while you're still.)

**3. Virtual Illuminance Sensor**

```text
Name:           Living Dark Enough             # → binary_sensor.living_dark_enough
Source sensor:  sensor.living_lux
Threshold:      40
Hysteresis:     10     # bright at 50lx, dark below 30lx — stops flapping
```

**4. Virtual Light**

```text
Name:                   Living Room            # → light.living_room
Lights:                 light.living_lamps
Turn-off timeout:       180
Occupancy sensor:       binary_sensor.living_occupancy    # regular = turns on & off
Maintain occupancy:     binary_sensor.living_presence     # only holds an on light on
Illuminance sensor:     binary_sensor.living_dark_enough
Illuminance mode:       control   # dark gates turn-ons AND bright forces off
Auto-on brightness:     60%       # automatic turn-ons come up at 60%; manual left alone
Effect warning duration: 3        # 3s "about to turn off" cue...
Effect brightness:       0%       # ...a blink fully off
Warning grace period:    20       # then 20s at current brightness to re-trigger
```

Behavior: motion + it's dark → lights on at 60%. Sit still → mmWave keeps them on even after the PIR clears. Leave → countdown starts only once **both** sensors are clear. Before turning off you get a quick blink, then 20s grace to wave and cancel. Get up in that window and it's as if nothing happened — original brightness restored, no trace.

Use `Illuminance mode: gate` instead if your lux sensor can *see* the lights it controls (otherwise they'd oscillate).

---

### Example 4 — Porch light (schedule follow-mode)

On at the later of sunset−15 and 21:00, off at 07:00. The schedule window is just a set of fields on the form — one row for the start edge, one for the end:

**1. Virtual Schedule Sensor**

```text
Name:          Porch Schedule                  # → binary_sensor.porch_schedule
Start time:    21:00
Start sun:     sunset
Start offset:  -15         # minutes relative to the sun event
Start combine: latest      # whichever of time / sun is later wins
End time:      07:00       # (no sun anchor on this edge)
```

**2. Virtual Light**

```text
Name:            Porch                          # → light.porch
Lights:          light.porch_real
Schedule sensor: binary_sensor.porch_schedule
Schedule mode:   follow    # on at window start, off at window end
```

`follow` = porch-light behavior; the window owns the light but manual changes mid-window still stand. Use `gate` instead if you want occupancy to control the light *only inside* the window.

---

### Example 5 — Pantry light (door sensor)

Open the pantry door → light on; close it → light off. A single entry driven by a real door/contact sensor:

**Virtual Light**

```text
Name:              Pantry                       # → light.pantry
Lights:            light.pantry_real
Door sensor:       binary_sensor.pantry_door    # a real contact sensor (on = open)
Door mode:         open_close                   # on while open, off when closed
```

Open the door and the light stays on the whole time it's open — no timeout while you're rummaging. Close it and a countdown (the **Turn-off timeout**) begins.

- Pick **`open`** instead if you only want the *opening* to trigger the light and then leave the normal timeout to turn it off — closing is ignored. Handy for a walk-through door where you don't want the light killed the instant it shuts.
- Add an **Illuminance sensor** and the door only lights the room when it's actually dark, exactly like occupancy — no wasted light opening a pantry in daylight. In `open_close` mode, if the room turns dark while the door is still standing open, the light comes on then.
- In `open_close` mode, closing the door **defers to presence**: if you also wired an occupancy sensor (or a keep-on entity is holding auto-off) and it still sees someone, the lights stay on instead of dropping on a person who just shut the door behind them.

---

**Global "don't touch my lights" toggle:** make an `input_boolean.guest_mode` and drop it into the **Keep-on entities** field of any virtual light — while it's on, every automatic turn-off is suspended (timers, forced-offs, window ends), but turn-ons and manual off still work. Every light also gets its own companion `... Auto-off` switch for the same thing per-room.