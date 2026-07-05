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

Once installed (see [Installation](#installation)), everything is configured from the UI — no YAML required. Each virtual entity is its own **config entry**, and you can add as many as you like.

1. Add an entity. The first one comes from **Settings → Devices & Services → Add Integration → MoLight**; after that, add more from the **MoLight** card on the **Devices & Services** page via **Add Entry** (searching **Add Integration → MoLight** again works too). Either way, a menu offers two ways to proceed:
   - **Create a single entity** — pick which kind of virtual entity to create and fill in its form (the flow for each type is described below).
   - **Discover…** — scan your existing entities and bulk-create several at once (see [Bulk discovery](#bulk-discovery)).
2. Create whichever virtual sensors you want your lights to react to — all of them are optional:
   - a **Virtual Occupancy Binary Sensor** for each real motion/presence sensor you want to use,
   - a **Virtual Combined Occupancy Binary Sensor** to merge several of them,
   - a **Virtual Illuminance Binary Sensor** (dark/bright) and/or a **Virtual Schedule Binary Sensor** (time/sun windows).
3. Then create a **Virtual Light** for each room or light group, pointing it at the real `light` entities and optionally referencing any of the virtual sensors from step 2. A virtual light with no sensors at all is still useful — it turns its real lights off on a timer.

The order matters: a virtual entity must already exist before another one can reference it — occupancy, illuminance, and schedule sensors before the virtual light that uses them, and simple occupancy sensors before a combined sensor that merges them. Sensors are reusable, so one occupancy or illuminance sensor can serve several virtual lights.

Finally, use the virtual light instead of the real lights in your dashboards and voice assistants — turning it on and off controls the real lights, and all the automatic behavior comes along for free. Each virtual light also comes with a companion **Auto-off** switch: flip it off to keep the lights on (movie night, guests) and back on to resume normal behavior. Each entry can be edited or removed independently later via its **Configure** button.

See [Entities](#entities) below for the full description of each entity type and its configuration options.

### Choosing the entity ID

Every manual create form ends with an optional **Entity ID** field:

This field is particularly useful if you often name your virtual entities the same as the entities they are controlling, and don't want to simply rely on home assistant's built-in logic for handling duplicate entity_ids which appends a number to them.

- **Leave it blank** and the ID is derived from the name. If that name-derived ID is already taken, the flow doesn't silently mangle it — it warns you and offers to either **proceed** (Home Assistant appends `_2`) or **go back** to the form, prefilled, and set one yourself.
- **Type one** to pin it explicitly. A domain prefix is tolerated and stripped (`light.kitchen` → `kitchen`) and the rest is slugified, so you don't have to get the format exactly right. If the ID you type already exists you're told, and the form is re-shown.

This field only appears when **creating** an entity — the **Configure** (edit) forms omit it, so an entity's ID is fixed once created. To rename one afterwards, use Home Assistant's own entity settings.

### Bulk discovery

Rather than adding entities one form at a time, the **Add Integration** or **Add Entry** menu offers three discovery actions that scan your Home Assistant entities and let you create many virtual entities in one pass:

- **Discover occupancy sensors** — finds every `binary_sensor` with a `device_class` of `occupancy`, `motion`, or `presence`.
- **Discover illuminance sensors** — finds every `sensor` with `device_class: illuminance`.
- **Discover lights** — finds every entity in the `light` domain.

Each action presents a checklist of the matching entities (all pre-selected); untick any you don't want, submit, and a Virtual Occupancy Sensor / Virtual Illuminance Sensor / Virtual Light is created for each pick named after the source entity.

The same form also offers an optional **prefix** and **suffix** to distinguish the virtual entities from the real ones they wrap, plus a target choosing what the affix shapes:

- **Entity ID** (default) — the prefix/suffix is applied to the entity ID only (e.g. a `v_` prefix gives `binary_sensor.v_hallway`), leaving each virtual entity's friendly name identical to the source it wraps.
- **Name** — the prefix/suffix is applied to the friendly name instead (e.g. a ` (virtual)` suffix), and the entity ID is derived from that composed name.

Both are applied verbatim with no separator inserted, so you control the spacing; leaving both blank keeps the discovered name untouched.

The list only shows entities you can usefully add: MoLight's own virtual entities are never suggested, disabled entities are hidden, and anything already wrapped by an existing MoLight entry is skipped — so re-running discovery after adding more sensors only offers the new ones.

Once you submit the first form, a second form is shown that allows you the option to change any of the default settings that will be applied to all of the Virtual Entities created by the discovery process.

You can also edit any of them individually afterwards via its **Configure** button — for discovered virtual lights, that's where you wire up the occupancy, illuminance, and schedule references.


### Assign a sensor to several lights at once

Wiring one shared sensor into many virtual lights one options form at a time is tedious, so the **Add Integration** or **Add Entry** menu also offers **Assign a sensor to several lights**. Pick the kind of sensor, choose one, set how the lights should use it, then tick the lights — every pick is updated in a single pass.

The three sensor kinds map to a virtual light's reference fields:

- **Assign an occupancy sensor** — choose any MoLight occupancy sensor (simple or combined) and a **role**: *regular* wires it in as the light's **Occupancy sensor** (can turn lights on), *maintain* as its **Maintain occupancy sensor** (keeps an already-on light on but never turns it on).
- **Assign an illuminance sensor** — choose a Virtual Illuminance Binary Sensor and its **Illuminance mode** (`control` or `gate`).
- **Assign a schedule sensor** — choose a Virtual Schedule Binary Sensor and its **Schedule mode** (`follow` or `gate`).

The final step lists your virtual lights with the ones **already using that sensor pre-selected**, so the checklist doubles as an audit of the current wiring. The submitted set is authoritative for that sensor-and-role: ticked lights get the reference (along with the chosen mode, replacing whatever they had under the same field), and any pre-selected light you untick has that reference *removed*. When it finishes it reports how many lights were assigned and how many cleared.

Because occupancy feeds the turn-off countdown, the same `light_timeout >= occupancy_timeout` guard from the light form applies here: a target whose turn-off timeout is shorter than the sensor's effective timeout is skipped rather than silently misconfigured, and the skipped lights are named in the summary so you can raise their timeouts and re-run. The illuminance and schedule assignments have no such constraint.

If you don't have any virtual lights yet, the flow tells you so instead of showing an empty checklist.


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
| **Auto-on brightness (%)** *(optional)* | Brightness applied when the light turns on *automatically* — by occupancy, illuminance going dark, or a follow-mode window. Manual and physical turn-ons keep their own brightness. Leave blank to let automatic turn-ons use the real lights' own last/default brightness |
| **Auto-on transition (s)** *(optional)* | Fade time when the light turns on *automatically*. Blank or `0` sends no transition. Manual and physical turn-ons never get a transition |
| **Auto-off transition (s)** *(optional)* | Fade time when the light turns off *automatically* — timer expiry, bright forcing off, a window ending. Blank or `0` sends no transition. A manual off is always immediate |
| **Effect warning duration (s)** | `0` disables. When the turn-off timer expires, instead of going dark the light first shows a brief *effect* cue for this long (see below) |
| **Effect brightness (%)** | Brightness during the effect stage. `0` blinks the real lights fully off — a distinct "about to turn off" flash |
| **Effect transition (s)** *(optional)* | Fade time into the effect brightness. Must not exceed the effect warning duration (setting one while the effect stage is disabled is rejected the same way) |
| **Warn grace period (s)** | `0` disables. After the effect stage, the light stays on for this long as a grace period before finally turning off, giving you time to re-trigger it |
| **Warn brightness (%)** *(optional)* | Brightness during the warn grace period. Leave blank to keep whatever brightness the light had before the warning began |
| **Warn transition (s)** *(optional)* | Fade time into the warn brightness. Must not exceed the warn grace period (setting one while the warn stage is disabled is rejected the same way) |
| **Occupancy sensor** *(optional)* | A MoLight occupancy sensor (simple or combined) |
| **Maintain occupancy sensor** *(optional)* | A MoLight occupancy sensor that keeps an already-on light on while occupied but never turns it on (see below) |
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
EFFECT     auto-off imminent — showing the brief effect/blink warning stage
WARN       auto-off imminent — grace period before the lights go off
```

- `IDLE` + manual/external turn-on → `ACTIVE` (timer starts)
- `IDLE`/`ACTIVE` + occupancy becomes active (and it's dark / in-window) → `OCCUPIED`
- `OCCUPIED` + occupancy clears → `COUNTDOWN`; the timer is the turn-off timeout anchored to the sensor's `latest_occupied_time`, so each sensor's hold time is respected
- `ACTIVE`/`COUNTDOWN` + timer expires → `EFFECT` → `WARN` → `IDLE` (see the warning sequence below); with both stages disabled this collapses to going straight to `IDLE`
- any state + all real lights turned off externally → `IDLE`
- follow-mode window start → `SCHEDULED`; occupancy and illuminance are ignored until the window ends. Boundaries are edge-triggered, so manual changes mid-window stand — including turning the light back on, which rejoins the window instead of starting a timer

Manual control is never gated: the user can always turn the virtual light on, even when it's bright or outside a schedule window.

#### Effect / warn warning

By default the light turns off the instant its timer expires. Setting an **effect** and/or **warn** timeout flags the impending turn-off first, so a room isn't dropped into darkness without notice:

1. **Effect** — a brief cue for *effect warning duration* seconds: the real lights are driven to the *effect brightness* (`0` blinks them fully off — a hard-to-miss flash). Skipped when its timeout is `0`.
2. **Warn** — a grace period for *warn grace period* seconds at the *warn brightness* (or the brightness the light already had, if left blank), then the lights turn off. Skipped when its timeout is `0`.

Each stage can optionally fade into its brightness over the *effect transition* / *warn transition* seconds. A stage's transition must fit inside the stage — the form rejects a transition longer than its stage's duration, and since a disabled stage has duration `0`, setting a transition for a disabled stage is rejected the same way rather than silently ignored.

Throughout both stages the virtual light stays on. **Any re-trigger during the sequence behaves exactly as if the pre-off timer were still running** — occupancy or maintain becoming active, a manual or physical turn-on, or an external dim cancels the warning and restores the pre-warning brightness, so the interruption leaves no trace. The restore is deliberately immediate (no fade), so the room snaps back the moment you re-trigger. Holding auto-off mid-sequence aborts it the same way, and the forced-off rules (bright in `control` mode, a gate/follow window ending) still turn the lights off during the sequence, just as they would mid-countdown.

#### Maintain occupancy sensor

The maintain occupancy sensor holds an already-on light on while it shows presence — it never turns the light on. Unlike a combined sensor's *maintain sensors* (which only extend occupancy started by a trigger sensor), it holds the light regardless of how it was lit: manual, wall switch, or occupancy. Typical use: an over-sensitive presence sensor (mmWave) that would false-trigger as an occupancy source but is perfect for keeping a room lit while someone sits still.

- The light being on with the maintain sensor on means `OCCUPIED` — whether the sensor turns on later, was already on at turn-on time, or both were already on at startup. Illuminance/schedule gating doesn't apply, since this is not a turn-on.
- The countdown starts only when the regular occupancy sensor *and* the maintain sensor are both clear, anchored to the latest `latest_occupied_time` of the two.
- Forced offs still win, exactly as they do over regular occupancy: bright in `control` mode, a gate-mode window ending, and a manual off all turn the light off immediately; a follow-mode window owns the light entirely.
- The false-detection quick off fires on a maintain clear only when *both* sensors flagged their clears false — genuine presence on either side earns the normal countdown.

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

Set an optional **auto-on brightness** to force a level whenever the light comes on *automatically* — occupancy, illuminance going dark, or a follow-mode window start. Manual turn-ons (via the virtual entity) and physical turn-ons (at the real light) are left alone, so you can dim the room by hand without it snapping back. Leave it blank to keep the previous behaviour, where automatic turn-ons don't command a brightness at all.

#### Transitions

Automatic actions can fade instead of switching instantly. The optional **auto-on transition** fades automatic turn-ons (occupancy, going dark, a follow-mode window start), and the **auto-off transition** fades automatic turn-offs (timer expiry, bright forcing off in `control` mode, a window ending). Manual and physical turn-ons, and a manual off, are never given a transition — when you flip the switch, it responds immediately. Leaving a transition blank (or `0`) sends no `transition` attribute at all, so lights keep whatever their integration's default behaviour is.

The effect/warn warning stages have their own fades (**effect transition** / **warn transition**) — see [Effect / warn warning](#effect--warn-warning).

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
