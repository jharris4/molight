# MoLight — configuration examples

MoLight is configured entirely from the UI, so these examples show the actual field values you'd type for common scenarios — from the simplest case to the full toolbox. Every create form starts with a **Name** (the entity ID derives from it), and all but the Virtual Remote's end with an optional **Entity ID** override if you want to pin it.

The examples build on each other:

1. [Just a turn-off timer](#example-1--just-a-turn-off-timer-the-simplest-case) — no sensors at all
2. [Simple occupancy](#example-2--simple-occupancy-case) — one motion sensor
3. [Living room](#example-3--living-room-the-whole-toolbox) — occupancy + maintain + illuminance + a warning blink
4. [Porch light](#example-4--porch-light-schedule-follow-mode) — a schedule window
5. [Home-only lighting](#example-5--home-only-lighting-source-backed-inverted-schedule) — an inverted schedule derived from an away-mode binary sensor
6. [Storage room light](#example-6--storage-room-light-door-sensor) — a door/contact sensor
7. [Hallway night light](#example-7--hallway-night-light-virtual-scheduled-light) — different settings inside and outside a schedule
8. [Pico remote](#example-8--pico-remote-for-the-closet-light-virtual-remote) — remote buttons instead of automations
9. [Bilresa remote](#example-9--bilresa-two-button-remote-single-vs-double-click) — single vs. double clicks

See the [README](README.md#entity-reference) for the full field reference.

---

### Example 1 — Just a turn-off timer (the simplest case)

No sensors at all — a virtual light that turns its lights off a set time after they come on. Great for a closet, pantry, or garage where you just want "never left on all day." One entry.

**Virtual Light**

```text
Name:              Pantry                       # → light.pantry
Lights to control: light.pantry_real            # the real light it controls
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
Lights to control: light.hallway_real          #    field if it clashes with the real one)
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
Lights to control:      light.living_lamps
Turn-off timeout:       180
Occupancy sensor:       binary_sensor.living_occupancy    # regular = turns on & off
Maintain occupancy sensor: binary_sensor.living_presence  # only holds an on light on
Illuminance sensor:     binary_sensor.living_dark_enough
Illuminance mode:       control   # dark gates turn-ons AND bright forces off
Auto-on brightness:     60%       # automatic turn-ons come up at 60%; manual left alone
Turn-on selection entity: select.living_wled_preset
Option source entity:      input_select.living_theme  # changed by calendar/automations
Fixed/fallback option:     Warm White Solid           # picked from WLED's offered options
Effect warning duration: 3        # 3s "about to turn off" cue...
Effect brightness:       0%       # ...a blink fully off
Warning grace period:    20       # then 20s at current brightness to re-trigger
Warning color:           255, 0, 0   # red — bulbs that can show color turn red for it
```

Behavior: motion + it's dark → lights on at 60%. Sit still → mmWave keeps them on even after the PIR clears. Leave → countdown starts only once **both** sensors are clear. Before turning off you get a quick blink, then 20s grace to wave and cancel — in red on any color-capable bulb, an unmissable cue (brightness-only bulbs just hold their level). Get up in that window and it's as if nothing happened — original brightness and color restored, no trace.

At each MoLight off-to-on transition, the current value of `input_select.living_theme` is copied into the WLED preset select first. A Home Assistant automation can set that helper to a holiday, game-night, or everyday theme. If the helper is unavailable or its value is not one of WLED's current options, `Warm White Solid` is used instead. Omit the source for a permanently fixed selection.

Use `Illuminance mode: gate` instead if your lux sensor can *see* the lights it controls (otherwise they'd oscillate).

---

### Example 4 — Porch light (schedule follow-mode)

On at the later of sunset−15 and 21:00, off at 07:00. The schedule window is two sections on the form — **Window start** and **Window end**:

**1. Virtual Schedule Sensor**

```text
Name:          Porch Schedule                  # → binary_sensor.porch_schedule

Window start:
  Time:        21:00
  Sun event:   sunset
  Sun offset:  -15         # minutes relative to the sun event
  Time vs. sun: latest     # whichever of time / sun is later wins

Window end:
  Time:        07:00       # (no sun anchor on this edge)
```

**2. Virtual Light**

```text
Name:            Porch                          # → light.porch
Lights to control: light.porch_real
Schedule sensor: binary_sensor.porch_schedule
Schedule mode:   follow    # on at window start, off at window end
```

`follow` = porch-light behavior; the window owns the light but manual changes mid-window still stand. Use one of the **Gate** behaviors instead if occupancy should activate the light *only inside* the window: **Gate and turn off** forces it off at the end, **Gate and switch state** recalculates an on light from current conditions and sensor history, and **Gate and keep state** preserves its existing state and timer.

---

### Example 5 — Home-only lighting (source-backed inverted schedule)

Suppose a template or integration provides `binary_sensor.away_mode`: it is `on` while the house is away and `off` while someone is home. To let occupancy automate a light only while the house is home, promote that sensor into MoLight's schedule picker and invert it.

**1. Virtual Schedule Sensor** — first choose **Binary sensor — mirror an existing sensor** as the schedule definition, then enter:

```text
Name:                 Home Schedule              # → binary_sensor.home_schedule
Source binary sensor: binary_sensor.away_mode
Invert output:        on                         # schedule on while away_mode is off
```

The resulting schedule is `on` while the source is `off` (home) and `off` while the source is `on` (away). If the source is missing, `unknown`, or `unavailable`, the schedule becomes unavailable—it is never inverted into a false `on`.

**2. Virtual Light**

```text
Name:              Entryway                      # → light.entryway
Lights to control: light.entryway_real
Turn-off timeout:  120
Occupancy sensor:  binary_sensor.entryway_occupancy
Schedule sensor:   binary_sensor.home_schedule
Schedule mode:     Gate and turn off
```

While someone is home, occupancy can turn the entryway light on normally. When away mode turns on, the inverted schedule turns off and **Gate and turn off** switches the light off. If the source becomes unavailable, the gate blocks new automatic activation but does not invent a schedule-end transition for a light that is already on.

---

### Example 6 — Storage room light (door sensor)

Open the storage room door → light on; close it → light off. A single entry driven by a real door/contact sensor:

**Virtual Light**

```text
Name:              Storage Room                 # → light.storage_room
Lights to control: light.storage_room_real
Turn-off timeout:  120                          # countdown once the door closes
Door sensor:       binary_sensor.storage_door   # a real contact sensor (on = open)
Door mode:         open_close                   # on while open, off when closed
```

Open the door and the light stays on the whole time it's open — no timeout while you're rummaging. Close it and a countdown (the **Turn-off timeout**) begins.

- Pick **`open`** instead if you only want the *opening* to trigger the light and then leave the normal timeout to turn it off — closing is ignored. Handy for a walk-through door where you don't want the light killed the instant it shuts.
- Add an **Illuminance sensor** and the door only lights the room when it's actually dark, exactly like occupancy — no wasted light opening a storage room in daylight. In `open_close` mode, if the room turns dark while the door is still standing open, the light comes on then.
- In `open_close` mode, closing the door **defers to presence**: if you also wired an occupancy sensor (or a keep-on entity is holding auto-off) and it still sees someone, the lights stay on instead of dropping on a person who just shut the door behind them.

---

### Example 7 — Hallway night light (Virtual Scheduled Light)

Motion lights the hallway at full brightness during the day and evening, but at night it should come on dim and go off quickly. One **Virtual Scheduled Light** holds both behaviours; the schedule sensor decides which set is active. This *replaces* Example 2's Hallway light — delete that entry first (its occupancy sensor stays and is reused below), or two virtual lights would fight over `light.hallway_real`. It's three forms in a row:

**1. Virtual Schedule Sensor** (the "night" window)

```text
Name:          Night                             # → binary_sensor.night

Window start:
  Time:        23:00
Window end:
  Time:        06:30
```

**2. Virtual Scheduled Light — shared form**

```text
Name:              Hallway                       # → light.hallway
Lights to control: light.hallway_real
Schedule sensor:   binary_sensor.night           # required; off = outside, on = inside
At schedule end:   Apply outside settings and keep the running state and timer
```

**3. Outside-schedule settings** (daytime and evening — schedule *off*)

```text
Turn-off timeout:  300
Occupancy sensor:  binary_sensor.hallway_occupancy   # from Example 2
Auto-on brightness: 100
```

**4. Inside-schedule settings** (night — schedule *on*; the form opens prefilled with the values you just entered, so only change what differs)

```text
Turn-off timeout:  60
Occupancy sensor:  binary_sensor.hallway_occupancy
Auto-on brightness: 15
Auto-on color temperature: 2200                  # warm night light
```

At 23:00 the schedule turns on and the light silently switches to the inside settings; the next motion turns it on dim and warm for a minute. At 06:30 it switches back. Things worth knowing:

- With this example's **Keep state** choice and profiles, ending the schedule does not restyle or force off a light that is already on — brightness and color only apply on the *next* automatic turn-on, and a countdown already running keeps its original duration. An incoming profile with bright illuminance in `control` mode can still force off under the normal rules.
- Choose **Switch state** to replace an on light's state and deadline using the outside profile's current sensors/history and 300-second timeout, or **Turn off using the inside settings** when the end of the night window itself should be an automatic off boundary.
- Every setting can differ per side, not just brightness: sensors, illuminance mode, warning blink, fades, keep-on entities and the turn-on selection. Leave the occupancy sensor out of one side and motion simply does nothing there.
- The `active_settings` attribute (`outside_schedule` / `inside_schedule`) shows which set is in force; **Configure** walks the same three forms again to edit either side.

---

### Example 8 — Pico remote for the closet light (Virtual Remote)

A 5-button Pico (on / favorite / raise / lower / off) driving one light, all on single clicks. Home Assistant's own Caséta integration doesn't expose Pico buttons as `event` entities — install [lutron-caseta-events](https://github.com/jharris4/lutron-caseta-events) first, and each button appears as one on the Pico's device page:

**Virtual Remote**

```text
Name:               Closet Pico
Lights to control:  light.master_bedroom_closet   # a MoLight virtual light, or any light
Brightness step:    10

Turn on         → Single-click buttons:  event.closet_pico_on
Turn off        → Single-click buttons:  event.closet_pico_off
Brightness up   → Single-click buttons:  event.closet_pico_raise
Brightness down → Single-click buttons:  event.closet_pico_lower
Preset 1        → Single-click buttons:  event.closet_pico_stop   # the middle "favorite" button
                  Brightness:            60
```

Each raise/lower click steps the brightness by 10%; the favorite button jumps to 60%. Presses count as *manual* control on a MoLight virtual light — they cancel a pending off-warning and restart the turn-off timer, exactly like a dashboard tap.

---

### Example 9 — Bilresa two-button remote (single vs. double click)

An IKEA Bilresa (Matter over Thread) has just two buttons, so single and double clicks carry different actions:

**Virtual Remote**

```text
Name:               Living Room Buttons
Lights to control:  light.living_room             # the Example 3 virtual light

Turn on         → Double-click buttons:  event.living_room_buttons_button_1
Turn off        → Double-click buttons:  event.living_room_buttons_button_2
Brightness up   → Single-click buttons:  event.living_room_buttons_button_1
Brightness down → Single-click buttons:  event.living_room_buttons_button_2
```

Single vs. double is read from each button's own advertised events (`multi_press_1` vs. `multi_press_2` on Matter multi-press buttons, `press` vs. `multi_tap` on Lutron buttons), so there's nothing to configure — and binding a double click to a button that can't do one is rejected with an error. One caveat inherent to multi-press hardware: the remote only confirms a *single* click after its double-click window passes, so single clicks respond with ~half a second of latency.

---

**Global "don't touch my lights" toggle:** make an `input_boolean.guest_mode` and drop it into the **Keep-on entities** field of any virtual light — while it's on, every automatic turn-off is suspended (timers, forced-offs, window ends), but turn-ons and manual off still work. Every light also gets its own companion `... Auto-off` switch for the same thing per-room.
