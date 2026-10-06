# MoLight: Smart Virtual Light Control for Home Assistant

A [HACS](https://hacs.xyz) custom integration that provides composable virtual building blocks for lighting automation: wrap your real sensors and lights in virtual entities, wire them together from the UI, and get occupancy-, daylight-, and schedule-aware lighting without writing a single automation.

Writing these automations by hand is tedious, and the complexity grows fast once you account for real-world behavior: sensors with different hold timeouts, sensors that are good at *triggering* occupancy but not *maintaining* it (or vice versa), schedules that mix fixed times with sun events, Home Assistant restarts, and sources that drop to `unavailable` at the worst moment. MoLight handles all of that in a small set of reusable entities.

**The building blocks** (each is its own config entry, so create as many as you like):

| Entity | What it does |
|---|---|
| [Virtual Occupancy Sensor](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-occupancy-sensor) | Wraps one motion/presence sensor; estimates when the person *actually left* |
| [Virtual Combined Occupancy Sensor](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-combined-occupancy-sensor) | Merges several occupancy sensors: trigger sensors start occupancy, maintain sensors only extend it |
| [Virtual Illuminance Sensor](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-illuminance-sensor) | Turns a lux reading into a steady bright/dark signal, with optional hysteresis so it doesn't flap around the threshold |
| [Virtual Schedule Sensor](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-schedule-sensor) | Reusable schedule signal from a time and/or sun-event window (overnight windows and sun offsets included) or another binary sensor, optionally inverted |
| [Virtual Combined Schedule Sensor](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-combined-schedule-sensor) | Combines schedules with any/all logic, optionally inverted, e.g. a morning and an evening window for one lamp |
| [Virtual Light](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-light) | Controls N real lights with an occupancy/illuminance/schedule-aware state machine |
| [Virtual Scheduled Light](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-scheduled-light) | Uses a complete set of Virtual Light settings inside a schedule and another outside it, optionally resting at a standby level |
| [Virtual Remote](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-remote) | Binds remote-control buttons (Pico, Bilresa, and others) to light actions, with no automations |

**Highlights** (everything below is covered by the automated test suite):

- Lights turn off a configurable time after the person *actually left*: countdowns anchor to each sensor's own hold time, not the moment it happens to clear (see [Virtual Occupancy Sensor](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-occupancy-sensor)).
- Occupancy takes over manually turned-on lights, so they still turn off after the room empties, but false detections (a fly, a heat blip) are classified: a light that only a blip lit goes off after a short false-detection off delay, and a light the user turned on is never cut short (see [State machine](https://github.com/jharris4/molight/blob/main/REFERENCE.md#state-machine)).
- A light's own [maintain sensor](https://github.com/jharris4/molight/blob/main/REFERENCE.md#maintain-occupancy-sensor), such as an over-sensitive mmWave sensor, holds a lit room while someone sits still but never turns it on, however the light was lit, even by hand.
- Turn-ons can be gated on [darkness](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-illuminance-sensor) and/or a [schedule window](https://github.com/jharris4/molight/blob/main/REFERENCE.md#schedule-modes); getting bright can force lights off (or not, for lux sensors that can see the lights they control).
- A schedule can own a light or gate it (see [Schedule modes](https://github.com/jharris4/molight/blob/main/REFERENCE.md#schedule-modes)). **Follow** gives porch-light behavior (on at window start, off at window end) while respecting manual overrides mid-window; the three **Gate** modes let motion and the door turn the light on only inside the window, and differ in what the window's end does to a light that is still on.
- A [Virtual Scheduled Light](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-scheduled-light) swaps a whole set of settings inside and outside a schedule, optionally resting at a dim [standby](https://github.com/jharris4/molight/blob/main/REFERENCE.md#standby) level instead of turning off.
- A [door sensor](https://github.com/jharris4/molight/blob/main/REFERENCE.md#door-sensor) lights a pantry or closet when the door opens, or holds it while the door stands open.
- Automatic turn-ons can carry their own [brightness, color and fade](https://github.com/jharris4/molight/blob/main/REFERENCE.md#brightness-color-and-fades), and can apply a preset through a `select` entity first, such as a WLED preset, either fixed or read from an `input_select` helper at each turn-on (see [Turn-on selection](https://github.com/jharris4/molight/blob/main/REFERENCE.md#turn-on-selection)). The virtual light offers brightness, color and fades only when its real lights can, and forwards a `transition` you pass on a service call.
- Optional [effect/warn warning](https://github.com/jharris4/molight/blob/main/REFERENCE.md#effect--warn-warning): blink, dim or recolor before an automatic turn-off, then a grace period to re-trigger, instead of sudden darkness.
- Every virtual light gets a companion **Auto-off switch**, and any on/off entity can act as a **keep-on hold** (guest mode, movie night) that suspends automatic turn-offs (see [Holding auto-off](https://github.com/jharris4/molight/blob/main/REFERENCE.md#holding-auto-off)). A virtual light can also [control other virtual lights](https://github.com/jharris4/molight/blob/main/REFERENCE.md#wrapping-another-virtual-light).
- [Restarts and `unavailable` sources](https://github.com/jharris4/molight/blob/main/REFERENCE.md#restarts-and-unavailability) are handled everywhere: missed follow-mode and Virtual Scheduled Light boundaries are applied exactly once, sensor blips are never misread as state changes, and a dead motion or door sensor can't hold lights on forever.
- [Virtual Remotes](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-remote) replace hand-written button automations: map single/double clicks of any remote whose buttons appear as `event` entities (IKEA Bilresa, Hue dimmer, and others, plus Lutron Picos via [lutron-caseta-events](https://github.com/jharris4/lutron-caseta-events)) to on/off/toggle/dim/preset actions, with the single-vs-double vocabulary read from each button itself.

## Installation

Requires Home Assistant **2026.1.0** or newer.

### HACS (recommended)

MoLight is available in the HACS default repository list.

1. In HACS, search for **MoLight** and install it.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for **MoLight**.

### Manual

Copy `custom_components/molight/` into your HA config `custom_components/` directory and restart.

## Getting started

Everything is configured from the UI, with no YAML. Adding an entry (the first via **Add Integration → MoLight**, later ones via **Add entry** on the MoLight card) opens a menu with four ways to proceed:

- **Create a single entity**: pick a type and fill in its form.
- **Discover**: scan existing entities and bulk-create virtual ones, with one menu item per discoverable type (see [Bulk discovery](#bulk-discovery)).
- **Assign a sensor to several lights**: wire one sensor into many lights at once (see [Bulk assignment](#bulk-assignment)).
- **Convert virtual lights**: promote existing [gated](https://github.com/jharris4/molight/blob/main/REFERENCE.md#schedule-modes) lights to separate inside- and outside-schedule settings, or return scheduled lights to one gated set of settings (see [Light conversion](#light-conversion)).

The usual order:

1. Create the virtual **sensors** you want lights to react to (all optional): an occupancy sensor per real motion/presence sensor, a combined sensor to merge several, an illuminance sensor, a schedule sensor. When you create an occupancy sensor, take care to set its **occupancy timeout** to match the real sensor's own hold time. It's the anchor for everything downstream, and MoLight can't read it for you (see [the note in the reference](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-occupancy-sensor)).
2. Create a **Virtual Light** per room or light group, pointing it at the real `light` entities and referencing any of the sensors from step 1. Use a **Virtual Scheduled Light** instead when every setting may differ inside and outside a schedule. A virtual light with no sensors is still useful: it turns its lights off on a timer.
3. Optionally create a **Virtual Remote** entry per remote to drive lights from its buttons (see [Virtual Remote](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-remote)).
4. Use the virtual light in dashboards and voice assistants instead of the real lights.

Order matters only in that a virtual entity must exist before another can reference it. Sensors are reusable: one occupancy or illuminance sensor can serve many lights. Every entry can be edited later via its **Configure** button, or removed independently.

### A typical first setup

The simplest useful setup is a hallway with one motion sensor, where the light turns off a bit after you leave. It takes two entries, each created via **Add entry → Create a single entity**. Fields not listed keep their defaults, and the sensor's last two fields sit in its collapsed **Advanced** section.

**1. Virtual Occupancy Sensor**: wraps the real motion sensor

```text
Name:                 Hallway Occupancy        # → binary_sensor.hallway_occupancy
Source sensor:        binary_sensor.hallway_motion
Occupancy timeout:    30      # my sensor holds "on" ~30s after last motion
False-detection grace: 5      # a single instantaneous blip = fly/heat, ignored
Clear after unavailable: 60   # if the sensor dies, don't hold lights forever
```

**2. Virtual Light**: points at the real light, references the sensor above

```text
Name:              Hallway                     # → light.hallway  (pin the Entity ID
Lights to control: light.hallway_real          #    field if it clashes with the real one)
Turn-off timeout:  60         # 60s after you actually left
Occupancy sensor:  binary_sensor.hallway_occupancy
```

Walk in → lights on. Room empties → 60s countdown anchored to when you *actually left* (not when the sensor cleared) → off. Turn it on by hand and it still turns off after the room empties, but a false blip never cuts short a manual on.

For more worked examples with the exact field values to enter, see [EXAMPLES.md](https://github.com/jharris4/molight/blob/main/EXAMPLES.md). It covers a plain turn-off timer, a single-sensor room, a living room with occupancy, maintain, illuminance, a warning blink and a turn-on selection, a porch light, home-only lighting from an inverted away-mode schedule, a night-only stairs light, a door-driven storage room light, a day/night hallway light, a Pico and a Bilresa remote, a bedside lamp on for a morning and an evening window, and a porch light that rests at standby all night.

### Choosing the entity ID

Every entity except a Virtual Remote can be given an **Entity ID** when it is created. The field is optional and is the last one on the form that asks for the name (on a Virtual Scheduled Light, the first of its three forms). It is handy when you name virtual entities after the real ones they wrap and don't want HA's `_2` suffix behavior. (A remote entry's only entity is its diagnostic sensor, whose ID derives from the name.) On every form it sits in a collapsed **Advanced** section, which on the occupancy sensor form also holds less-common options like the false-detection grace:

- **Leave it blank** to derive the ID from the name. If that ID is already taken, the flow warns you and offers to proceed (HA appends `_2`) or go back, prefilled, and set one yourself.
- **Type one** to pin it. A domain prefix is tolerated and stripped (`light.kitchen` → `kitchen`), the rest is slugified. A conflicting ID re-shows the form with an error. A virtual light's pinned ID also shapes its companion switch: `light.kitchen` → `switch.kitchen_auto_off`.

The field only appears when creating. Renaming later works through each entry's **Configure** button (every edit form has a **Name** field, and the entry title follows it), while the entity ID stays put (change that via HA's own entity settings). MoLight follows an entity ID changed there, of a virtual entity or of a real one it uses: see [Restarts and unavailability](https://github.com/jharris4/molight/blob/main/REFERENCE.md#restarts-and-unavailability).

### Bulk discovery

The three **Discover** actions scan your existing entities and create a virtual wrapper for each pick:

- **Discover occupancy sensors**: every `binary_sensor` with device class `occupancy`, `motion`, or `presence`.
- **Discover illuminance sensors**: every `sensor` with device class `illuminance`.
- **Discover lights**: every `light` entity.

Each starts with an optional filter form: pick **areas** and/or **labels** to narrow the scan (an entity matches through its own assignment or its device's; with both filters set, an entity must match an area *and* carry a label), and choose whether the checklist starts with everything **pre-selected** (bulk-add, the default) or empty (handy when you only want a few). Leave the filters blank to see everything.

The next form shows the checklist of matching entities. Only useful candidates appear: MoLight's own entities, disabled entities, anything already wrapped, and light groups that contain a virtual light are hidden, so re-running discovery later only offers what's new. Each pick becomes its own virtual light, so two picks that control the same light, such as a light and a light group that contains it, are refused; a light group whose lights are already ticked starts unticked.

An optional **prefix**/**suffix** distinguishes the virtual entities from the real ones, applied verbatim to the source's friendly name (you control the spacing), with a target of your choice:

- **Entity ID** (default): only the entity ID gets the affix, as the slug of the composed name (`v_` on a sensor named "Hallway Motion" → `binary_sensor.v_hallway_motion`); the friendly name stays identical to the source.
- **Name**: the friendly name gets the affix, and the entity ID derives from the composed name.

A final form then lets you adjust the default settings applied to every pick; for discovered lights that includes the occupancy/illuminance/schedule references. Each created entity can still be edited individually afterwards via **Configure**.

- A pick that stopped being a candidate while the forms were open (wrapped from another tab, including by a Discover flow submitted at the same moment, disabled or removed) is skipped, and the summary says how many.
- Discovery creates regular Virtual Lights; eligible gated lights can then be promoted through **Convert virtual lights**.

### Bulk assignment

**Assign a sensor to several lights** wires one shared sensor into many virtual lights in a single pass. Pick the kind of sensor, the sensor, and how the lights should use it:

- **Occupancy**, with a **role**: *regular* (turns lights on and off) or *maintain* (only holds an already-on light on).
- **Illuminance**, with its **mode** (**Control** or **Gate only**).
- **Schedule**, with its **mode** (**Follow**, **Gate and turn off**, **Gate and switch state**, or **Gate and keep state**).

The final step lists your virtual lights with current users of that sensor **pre-selected**, so the checklist doubles as an audit of the wiring. The submitted set is authoritative: ticked lights get the reference and mode, unticked pre-selected lights have it removed, and the summary reports how many were newly wired and how many had the reference removed (lights that already had the exact sensor and mode are left untouched and not counted).

Because occupancy feeds the turn-off countdown, a light's turn-off timeout must be at least its occupancy sensor's timeout (for a combined sensor, the largest of its constituents'; see [Virtual Light](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-light)). Lights that break that rule are skipped and named in the summary, so you can raise their timeouts and re-run.

Bulk assignment currently applies only to regular Virtual Lights. Configure the sensors for a Virtual Scheduled Light in its outside- and inside-schedule settings instead.

### Light conversion

**Convert virtual lights** changes existing entries in place, preserving their config entry, entity IDs, history, dashboard references, remote targets, and other entity references. A **Configure** form of a light that was opened before its conversion can no longer be saved; open it again.

- **Gated → scheduled**: available for Virtual Lights with any gate behavior and a schedule sensor. Each light retains its own schedule. Its current settings become the inside-schedule settings; the outside-schedule settings keep its timing, appearance, warnings, turn-on selection, and keep-on entities but starts without occupancy, maintain, illuminance, or door inputs. The **Gate and turn off**, **Gate and switch state** and **Gate and keep state** modes map to the **Turn off**, **Switch state** and **Keep state** [schedule-end actions](https://github.com/jharris4/molight/blob/main/REFERENCE.md#virtual-scheduled-light). This creates a useful starting point rather than promising identical runtime behavior: add any automatic inputs you want outside the schedule afterward.
- **Scheduled → gated**: every Virtual Scheduled Light that still has a schedule remains eligible. The inside-schedule settings become the regular Virtual Light settings, the shared schedule is retained as its gate, and the corresponding **Gate and turn off**, **Gate and switch state** or **Gate and keep state** mode is selected. The outside-schedule settings and any [standby](https://github.com/jharris4/molight/blob/main/REFERENCE.md#standby) settings are permanently discarded after an explicit confirmation warning.

Follow-mode Virtual Lights are not offered for conversion because their schedule directly owns the lights rather than selecting a settings policy.

## Reference

[REFERENCE.md](https://github.com/jharris4/molight/blob/main/REFERENCE.md) describes every field, attribute and edge case of each entity, and ends with the design notes. [CONTRIBUTING.md](https://github.com/jharris4/molight/blob/main/CONTRIBUTING.md) covers the dev container, the tests and the release process.

## Troubleshooting

MoLight writes no debug logs. It logs an error when discovery fails to create an entry, and a warning only when it cannot do what it was asked:

- a turn-on selection it could not apply;
- a Virtual Scheduled Light with no schedule;
- a combined schedule that includes itself;
- a Virtual Schedule Sensor saved with a window that never opens at the home location;
- a Virtual Schedule Sensor whose source group includes it (it goes `unavailable`);
- a Virtual Occupancy Sensor whose source group includes it (it stops following the source);
- a Virtual Light whose member light group includes it, directly or through a light group inside it (it stops controlling that group);
- two virtual lights, saved before the form refused it, that both control the same light (they fight over it);
- a keep-on entity saved before the form refused it (it stays as it was).

To see why a light did what it did, read these [attributes](https://github.com/jharris4/molight/blob/main/REFERENCE.md#attributes) in **Developer tools → States**:

- `molight_state` on the virtual light: its [state-machine](https://github.com/jharris4/molight/blob/main/REFERENCE.md#state-machine) state.
- `last_on_physical`, `last_on_virtual`, `last_on_occupancy`, `last_on_illuminance` and `last_on_door` on the virtual light: when each source last turned it on.
- `auto_off_held` on the virtual light: whether the Auto-off switch or a keep-on entity is holding automatic turn-offs.
- `schedule_window_start`, `bright_forced_off` with `bright_resume_until` and, on a Virtual Scheduled Light, `active_settings`: the follow-mode window marker, whether brightness forced the light off and until when going dark resumes it, and which settings are live.
- `latest_occupied_time`, `last_clear_false_detection` and `last_clear_unavailable` on an occupancy sensor: when the person was last seen, and whether the last clear was a false detection or an unavailable source.
- `resolved_schedules` on a Virtual Combined Schedule Sensor: the plain schedules it was built from.
- A Virtual Remote's **Last Action** sensor: the last binding it ran, and the button and click that fired it.
