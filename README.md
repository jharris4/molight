# MoLight — Smart Virtual Light Control for Home Assistant

A [HACS](https://hacs.xyz) custom integration that provides composable virtual building blocks for lighting automation: wrap your real sensors and lights in virtual entities, wire them together from the UI, and get occupancy-, daylight-, and schedule-aware lighting without writing a single automation.

Writing these automations by hand is tedious, and the complexity grows fast once you account for real-world behavior: sensors with different hold timeouts, sensors that are good at *triggering* occupancy but not *maintaining* it (or vice versa), schedules that mix fixed times with sun events, Home Assistant restarts, and sources that drop to `unavailable` at the worst moment. MoLight handles all of that in a small set of reusable entities.

**The building blocks** (each is its own config entry — create as many as you like):

| Entity | What it does |
|---|---|
| [Virtual Occupancy Sensor](#virtual-occupancy-binary-sensor) | Wraps one motion/presence sensor; estimates when the person *actually left* |
| [Virtual Combined Occupancy Sensor](#virtual-combined-occupancy-binary-sensor) | Merges several occupancy sensors with trigger/maintain roles |
| [Virtual Illuminance Sensor](#virtual-illuminance-binary-sensor) | Turns a lux reading into a steady bright/dark signal |
| [Virtual Schedule Sensor](#virtual-schedule-binary-sensor) | On inside time windows defined by fixed times and/or sun events |
| [Virtual Light](#virtual-light) | Controls N real lights with an occupancy/illuminance/schedule-aware state machine |

**Highlights** — everything below is covered by the automated test suite:

- Lights turn off a configurable time after the person *actually left* — countdowns anchor to each sensor's own hold time, not the moment it happens to clear.
- Occupancy takes over manually turned-on lights, so they still turn off after the room empties — but false detections (a fly, a heat blip) are classified and never cut short lights the user turned on.
- Turn-ons can be gated on darkness and/or a schedule window; getting bright can force lights off (or not, for lux sensors that can see the lights they control).
- Follow-mode schedules give porch-light behavior — on at window start, off at window end — while respecting manual overrides mid-window.
- Optional effect/warn warning: blink or dim before an automatic turn-off, then a grace period to re-trigger, instead of sudden darkness.
- Every virtual light gets a companion **Auto-off switch**, and any on/off entity can act as a **keep-on hold** (guest mode, movie night) that suspends automatic turn-offs.
- Restarts and `unavailable` sources are handled everywhere: missed schedule boundaries are applied exactly once, sensor blips are never misread as state changes, and a dead motion sensor can't hold lights on forever.

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS.
2. Install **MoLight**.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and search for **MoLight**.

### Manual

Copy `custom_components/molight/` into your HA config `custom_components/` directory and restart.

## Getting started

Everything is configured from the UI — no YAML. Adding an entry (the first via **Add Integration → MoLight**, later ones via **Add Entry** on the MoLight card) opens a menu with three ways to proceed:

- **Create a single entity** — pick a type and fill in its form.
- **Discover…** — scan existing entities and bulk-create virtual ones (see [Bulk discovery](#bulk-discovery)).
- **Assign a sensor to several lights** — wire one sensor into many lights at once (see [Bulk assignment](#bulk-assignment)).

The usual order:

1. Create the virtual **sensors** you want lights to react to (all optional): an occupancy sensor per real motion/presence sensor, a combined sensor to merge several, an illuminance sensor, a schedule sensor.
2. Create a **Virtual Light** per room or light group, pointing it at the real `light` entities and referencing any of the sensors from step 1. A virtual light with no sensors is still useful — it turns its lights off on a timer.
3. Use the virtual light in dashboards and voice assistants instead of the real lights.

Order matters only in that a virtual entity must exist before another can reference it. Sensors are reusable — one occupancy or illuminance sensor can serve many lights. Every entry can be edited later via its **Configure** button, or removed independently.

### Choosing the entity ID

Every create form ends with an optional **Entity ID** field — handy when you name virtual entities after the real ones they wrap and don't want HA's `_2` suffix behavior:

- **Leave it blank** to derive the ID from the name. If that ID is already taken, the flow warns you and offers to proceed (HA appends `_2`) or go back, prefilled, and set one yourself.
- **Type one** to pin it. A domain prefix is tolerated and stripped (`light.kitchen` → `kitchen`), the rest is slugified. A conflicting ID re-shows the form with an error.

The field only appears when creating; to rename later, use HA's own entity settings.

### Bulk discovery

The three **Discover…** actions scan your existing entities and create a virtual wrapper for each pick:

- **Discover occupancy sensors** — every `binary_sensor` with device class `occupancy`, `motion`, or `presence`.
- **Discover illuminance sensors** — every `sensor` with device class `illuminance`.
- **Discover lights** — every `light` entity.

Each shows a checklist of matching entities (all pre-selected). Only useful candidates appear: MoLight's own entities, disabled entities, and anything already wrapped are hidden — re-running discovery later only offers what's new.

An optional **prefix**/**suffix** distinguishes the virtual entities from the real ones, applied verbatim (you control the spacing) to a target of your choice:

- **Entity ID** (default) — only the entity ID gets the affix (`v_` → `binary_sensor.v_hallway`); the friendly name stays identical to the source.
- **Name** — the friendly name gets the affix, and the entity ID derives from the composed name.

A second form then lets you adjust the default settings applied to every pick — for discovered lights that includes the occupancy/illuminance/schedule references. Each created entity can still be edited individually afterwards via **Configure**.

### Bulk assignment

**Assign a sensor to several lights** wires one shared sensor into many virtual lights in a single pass. Pick the kind of sensor, the sensor, and how the lights should use it:

- **Occupancy** — with a **role**: *regular* (turns lights on and off) or *maintain* (only holds an already-on light on).
- **Illuminance** — with its **mode** (`control` or `gate`).
- **Schedule** — with its **mode** (`follow` or `gate`).

The final step lists your virtual lights with current users of that sensor **pre-selected**, so the checklist doubles as an audit of the wiring. The submitted set is authoritative: ticked lights get the reference and mode, unticked pre-selected lights have it removed, and the summary reports both counts.

Because occupancy feeds the turn-off countdown, the `light_timeout >= occupancy_timeout` guard applies here too: lights whose turn-off timeout is shorter than the sensor's effective timeout are skipped and named in the summary, so you can raise their timeouts and re-run.

## Entity reference

### Virtual Occupancy Binary Sensor

Wraps a single real binary sensor (motion, presence, door…). `on` mirrors the source directly; the value the rest of the system runs on is the `latest_occupied_time` attribute — the best estimate of when the person actually left.

| Config | Description |
|---|---|
| **Source sensor** | The real `binary_sensor` to wrap |
| **Occupancy timeout (s)** | The source's own hold time. When it clears, `latest_occupied_time` is back-dated to `clear time − timeout` |
| **False-detection grace (s)** | `0` disables. A cycle whose on-duration exceeds the timeout by no more than the grace contained exactly one instantaneous detection — almost certainly a fly/heat blip. Such cycles don't advance `latest_occupied_time`, are counted in `false_detection_count`, and flag the clear via `last_clear_false_detection` so lights can turn off quickly |
| **Clear after unavailable (s)** | `0` disables, default `60`. If the source goes `unavailable`/`unknown` while occupancy is active, `latest_occupied_time` advances to the dropout moment immediately, and if the source hasn't recovered after this many seconds the occupancy clears, flagged via `last_clear_unavailable`. Never classified as a false detection — the room may still be occupied, so dependent lights run their normal gentle countdown. A recovery cancels the pending clear |

Attributes: `latest_occupied_time`, `occupancy_timeout`, `last_on_time`, `last_clear_false_detection`, `false_detection_count`, `last_clear_unavailable`.

### Virtual Combined Occupancy Binary Sensor

Combines multiple Virtual Occupancy Sensors into one.

| Config | Description |
|---|---|
| **Trigger sensors** | Any one going `on` starts occupancy |
| **Maintain sensors** *(optional)* | Keep occupancy alive once started, but cannot start it alone |

`latest_occupied_time` is the max across all constituents, so each sub-sensor's individual timeout is respected. A combined cycle during which no constituent advanced `latest_occupied_time` was made up entirely of false cycles and is flagged/counted the same way as on the simple sensor.

After a restart, a *maintain* sensor that has already been `on` for more than 5 seconds is assumed to reflect occupancy triggered before HA went down, and seeds the sensor `on` (the one exception to "maintain sensors never start occupancy").

### Virtual Illuminance Binary Sensor

`on` = at or above the threshold (bright enough, no artificial lighting needed)
`off` = below the threshold (dark enough to warrant lighting)

| Config | Description |
|---|---|
| **Source sensor** | Any real `sensor` with `device_class: illuminance` |
| **Threshold (lx)** | The lux level at which the sensor reports `on` |
| **Hysteresis (lx)** | `0` disables. Becomes bright at `threshold + hysteresis`, dark below `threshold − hysteresis`; readings inside the band hold the current state, suppressing flapping when the light level hovers around the threshold |

An unavailable or unparsable source holds the last known value — a lux sensor dropping out must not read as "it got dark". The state also survives restarts.

### Virtual Schedule Binary Sensor

`on` = current time is within an active window. Transitions fire at the exact boundary (no polling). Overnight windows (e.g. 22:00 → 06:00) are supported.

Each window edge is a fixed time, a sun event, or both:

```yaml
start:
  time: "21:00"        # fixed local time
  sun: sunset          # or sunrise
  offset: -15          # minutes relative to the sun event
  combine: latest      # latest | earliest — which of time/sun wins
end: "07:00"           # plain string = fixed time
```

e.g. *start at the later of sunset − 15 min and 21:00*. On polar days where the sun event doesn't occur, the fixed time stands alone.

Attributes: `current_window_start` (identifies the active window; used by follow-mode lights for restart catch-up), `next_transition`.

### Virtual Light

Controls N real lights with an occupancy-aware state machine.

| Config | Description |
|---|---|
| **Lights** | Real `light` entities to control |
| **Turn-off timeout (s)** | Must be >= the occupancy timeout of any referenced occupancy entity |
| **False-detection off delay (s)** | When occupancy clears flagged as a false detection, lights that were lit *by that cycle* turn off after this short delay instead of the normal countdown. Lights turned on manually are never affected |
| **Auto-on brightness (%)** *(optional)* | Brightness applied when the light turns on *automatically* — by occupancy, illuminance going dark, or a follow-mode window. Manual and physical turn-ons keep their own brightness. Blank = automatic turn-ons use the real lights' own last/default brightness |
| **Auto-on fade (s)** *(optional)* | Fade time for automatic turn-ons. Blank or `0` sends no transition. Manual and physical turn-ons never get one |
| **Auto-off fade (s)** *(optional)* | Fade time for automatic turn-offs — timer expiry, bright forcing off, a window ending. A manual off is always immediate |
| **Effect warning duration (s)** | `0` disables. When the turn-off timer expires, first show a brief *effect* cue for this long instead of going dark (see [Effect / warn warning](#effect--warn-warning)) |
| **Effect brightness (%)** | Brightness during the effect stage. `0` blinks the real lights fully off — a distinct "about to turn off" flash |
| **Effect fade (s)** *(optional)* | Fade into the effect brightness. Must fit within the effect duration (a fade on a disabled stage is rejected too) |
| **Warning grace period (s)** | `0` disables. After the effect, the light stays on this long before finally turning off, giving you time to re-trigger |
| **Warning brightness (%)** *(optional)* | Brightness during the grace period. Blank keeps whatever brightness the light had before the warning began |
| **Warning fade (s)** *(optional)* | Fade into the warning brightness. Must fit within the grace period |
| **Occupancy sensor** *(optional)* | A MoLight occupancy sensor (simple or combined) |
| **Maintain occupancy sensor** *(optional)* | Keeps an already-on light on while occupied but never turns it on (see [Maintain occupancy sensor](#maintain-occupancy-sensor)) |
| **Illuminance sensor** *(optional)* | A MoLight Virtual Illuminance Binary Sensor |
| **Illuminance mode** | `control` — dark gates turn-ons AND turning bright forces the lights off. `gate` — dark gates turn-ons only; bright never turns lights off. Use `gate` when the lux sensor can see the controlled lights, which would otherwise oscillate |
| **Schedule sensor** *(optional)* | A MoLight Virtual Schedule Binary Sensor |
| **Schedule mode** | `follow` — lights turn on at window start and off at window end (porch lights). `gate` — occupancy may only activate lights inside the window; window end forces lights off |
| **Keep-on entities** *(optional)* | Any entities with an on/off state. While any is `on`, auto-off is held (see [Holding auto-off](#holding-auto-off)) |

#### State machine

```
IDLE       lights off, no timer
ACTIVE     lights on, timer running (manual/external turn-on, no occupancy)
OCCUPIED   lights on, occupancy active — timer suspended
COUNTDOWN  occupancy cleared, timer ticking toward lights-off
SCHEDULED  lights on inside a follow-mode window — no timer
EFFECT     auto-off imminent — showing the brief effect/blink warning stage
WARN       auto-off imminent — grace period before the lights go off
```

- `IDLE` + manual/external turn-on → `ACTIVE` (timer starts)
- `IDLE`/`ACTIVE` + occupancy becomes active (and it's dark / in-window) → `OCCUPIED`
- Already-active occupancy is adopted the same way: turning the light on (manually or at the wall) while the occupancy sensor is on goes straight to `OCCUPIED`, as does illuminance turning dark or a gate-mode window opening while the light is on — a timer never expires despite presence
- `OCCUPIED` + occupancy clears → `COUNTDOWN`; the timer is the turn-off timeout anchored to the sensor's `latest_occupied_time`, so each sensor's hold time is respected
- `ACTIVE`/`COUNTDOWN` + timer expires → `EFFECT` → `WARN` → `IDLE` (with both stages disabled this collapses to going straight to `IDLE`)
- any state + all real lights turned off externally → `IDLE`
- follow-mode window start → `SCHEDULED`; occupancy and illuminance are ignored until the window ends. Boundaries are edge-triggered, so manual changes mid-window stand — including turning the light back on, which rejoins the window instead of starting a timer

Manual control is never gated: the user can always turn the virtual light on, even when it's bright or outside a schedule window. The current state is exposed as the `molight_state` attribute.

#### Effect / warn warning

By default the light turns off the instant its timer expires. Setting an **effect** and/or **warn** duration flags the impending turn-off first, so a room isn't dropped into darkness without notice:

1. **Effect** — a brief cue for *effect warning duration* seconds: the real lights are driven to the *effect brightness* (`0` blinks them fully off). Skipped when its duration is `0`.
2. **Warn** — a grace period of *warning grace period* seconds at the *warning brightness* (or the brightness the light already had, if blank), then the lights turn off. Skipped when its duration is `0`.

Each stage can fade into its brightness over its optional *fade* time; a fade must fit inside its stage (a fade on a disabled stage is rejected rather than silently ignored).

Throughout both stages the virtual light stays on. **Any re-trigger during the sequence behaves exactly as if the pre-off timer were still running** — occupancy or maintain becoming active, a manual or physical turn-on, or an external dim cancels the warning and restores the pre-warning brightness, so the interruption leaves no trace. The restore is deliberately immediate (no fade). Holding auto-off mid-sequence aborts it the same way, and the forced-off rules (bright in `control` mode, a gate/follow window ending) still turn the lights off during the sequence, just as they would mid-countdown.

#### Maintain occupancy sensor

The maintain occupancy sensor holds an already-on light on while it shows presence — it never turns the light on. Unlike a combined sensor's *maintain sensors* (which only extend occupancy started by a trigger sensor), it holds the light regardless of how it was lit: manual, wall switch, or occupancy. Typical use: an over-sensitive presence sensor (mmWave) that would false-trigger as an occupancy source but is perfect for keeping a room lit while someone sits still.

- The light being on with the maintain sensor on means `OCCUPIED` — whether the sensor turns on later, was already on at turn-on time, or both were already on at startup. Illuminance/schedule gating doesn't apply, since this is not a turn-on.
- The countdown starts only when the regular occupancy sensor *and* the maintain sensor are both clear, anchored to the latest `latest_occupied_time` of the two.
- Forced offs still win, exactly as they do over regular occupancy: bright in `control` mode, a gate-mode window ending, and a manual off all turn the light off immediately; a follow-mode window owns the light entirely.
- The false-detection quick off fires on a maintain clear only when *both* sensors flagged their clears false — genuine presence on either side earns the normal countdown.

#### Holding auto-off

Each virtual light also creates a companion **`<name> Auto-off` switch**. Auto-off is *held* while that switch is off **or** any configured keep-on entity is on:

- Every automatic turn-off is suspended — the timer, the false-detection quick off, bright-forces-off, and schedule window ends. The state machine keeps transitioning; it just never arms a timer.
- Turn-ons are unaffected (occupancy, going dark, and window starts still light the room), and a manual off always works.
- When the last hold releases, the light re-evaluates its rules: a follow window that ended while held turns it off now, as does being outside a gate window or bright in `control` mode; active occupancy keeps it on; an active follow window keeps it `SCHEDULED`; otherwise a **fresh full timer** starts.
- A keep-on entity dropping to `unavailable`/`unknown` holds its last known value (a dead toggle never reads as "hold released" — or engaged). The switch state survives restarts, and a held light adopted at startup won't start a timer.

Share one keep-on entity (e.g. `input_boolean.guest_mode`) across all your virtual lights for a global "don't touch the lights" toggle, or give a single room its own. The current hold status is exposed as the `auto_off_held` attribute.

#### Brightness and fades

The virtual light supports brightness. External brightness changes on the real lights count as human activity and restart a running timer; brightness `0` is treated as off (and `0 → non-zero` as a turn-on). Physical and virtual changes are tracked separately (`last_brightness_change_physical` / `_virtual`), as are the reasons the light last activated (`last_on_physical` / `_virtual` / `_occupancy` / `_illuminance`).

An optional **auto-on brightness** forces a level whenever the light comes on *automatically*; manual and physical turn-ons are left alone, so you can dim the room by hand without it snapping back. Likewise, the **auto-on** and **auto-off fades** apply only to automatic actions — flipping the switch always responds immediately. A blank fade sends no `transition` attribute at all, so lights keep their integration's default behavior.

#### Restarts and unavailability

- Real lights already on at startup are adopted (`ACTIVE` with a fresh timer); active occupancy (when dark / in-window) is claimed as `OCCUPIED`.
- Follow-mode windows use `schedule_window_start` as a marker: a boundary missed while HA was down is applied exactly once at startup, while a manual off mid-window is respected. A restart landing mid effect/warn restores the pre-warning brightness.
- Entities dropping to `unavailable`/`unknown` are never read as state changes, at any layer; recovery transitions are processed as real events. A source sensor that stays unavailable is handled by the occupancy sensor's *clear after unavailable* timeout, so a dead motion sensor can't hold lights on forever.

## Development

This repository uses a devcontainer to test & run the integration locally.

Tests run inside the devcontainer:

```bash
npm run up      # start the devcontainer
npm test        # pytest inside the container
npm run lint    # ruff inside the container
```

`npm run hass` starts a docker-compose HA instance for manual testing. It binds the same port (8123) as the devcontainer, so only one can run at a time.

## Design notes

- Each virtual entity is its own config entry, so they can be created, edited, and removed independently.
- A virtual entity must exist before another can reference it (sensors before the lights that use them).
- The `light_timeout >= occupancy_timeout` constraint is validated in both directions: creating/editing a light checks its referenced occupancy entity (including through a combined sensor), and raising an occupancy sensor's timeout checks every light that depends on it.
- Once an entry's options have been edited, the options fully replace the original data (so cleared optional fields stay cleared).
