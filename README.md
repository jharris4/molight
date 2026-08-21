# MoLight — Smart Virtual Light Control for Home Assistant

A [HACS](https://hacs.xyz) custom integration that provides composable virtual building blocks for lighting automation: wrap your real sensors and lights in virtual entities, wire them together from the UI, and get occupancy-, daylight-, and schedule-aware lighting without writing a single automation.

Writing these automations by hand is tedious, and the complexity grows fast once you account for real-world behavior: sensors with different hold timeouts, sensors that are good at *triggering* occupancy but not *maintaining* it (or vice versa), schedules that mix fixed times with sun events, Home Assistant restarts, and sources that drop to `unavailable` at the worst moment. MoLight handles all of that in a small set of reusable entities.

**The building blocks** (each is its own config entry — create as many as you like):

| Entity | What it does |
|---|---|
| [Virtual Occupancy Sensor](#virtual-occupancy-binary-sensor) | Wraps one motion/presence sensor; estimates when the person *actually left* |
| [Virtual Combined Occupancy Sensor](#virtual-combined-occupancy-binary-sensor) | Merges several occupancy sensors with trigger/maintain roles |
| [Virtual Illuminance Sensor](#virtual-illuminance-binary-sensor) | Turns a lux reading into a steady bright/dark signal |
| [Virtual Schedule Sensor](#virtual-schedule-binary-sensor) | Reusable schedule signal from a time/sun window or another binary sensor, optionally inverted |
| [Virtual Light](#virtual-light) | Controls N real lights with an occupancy/illuminance/schedule-aware state machine |
| [Virtual Scheduled Light](#virtual-scheduled-light) | Uses a complete set of Virtual Light settings inside a schedule and another outside it |
| [Virtual Remote](#virtual-remote) | Binds remote-control buttons (Pico, Bilresa, …) to light actions — no automations |

**Highlights** — everything below is covered by the automated test suite:

- Lights turn off a configurable time after the person *actually left* — countdowns anchor to each sensor's own hold time, not the moment it happens to clear.
- Occupancy takes over manually turned-on lights, so they still turn off after the room empties — but false detections (a fly, a heat blip) are classified and never cut short lights the user turned on.
- Turn-ons can be gated on darkness and/or a schedule window; getting bright can force lights off (or not, for lux sensors that can see the lights they control).
- Follow-mode schedules give porch-light behavior — on at window start, off at window end — while respecting manual overrides mid-window.
- Optional effect/warn warning: blink or dim before an automatic turn-off, then a grace period to re-trigger, instead of sudden darkness.
- Every virtual light gets a companion **Auto-off switch**, and any on/off entity can act as a **keep-on hold** (guest mode, movie night) that suspends automatic turn-offs.
- Restarts and `unavailable` sources are handled everywhere: missed schedule boundaries are applied exactly once, sensor blips are never misread as state changes, and a dead motion sensor can't hold lights on forever.
- Virtual Remotes replace hand-written button automations: map single/double clicks of any remote whose buttons appear as `event` entities (IKEA Bilresa, Hue dimmer, … — and Lutron Picos via [lutron-caseta-events](https://github.com/jharris4/lutron-caseta-events)) to on/off/toggle/dim/preset actions, with the single-vs-double vocabulary read from each button itself.

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

Everything is configured from the UI — no YAML. Adding an entry (the first via **Add Integration → MoLight**, later ones via **Add Entry** on the MoLight card) opens a menu with four ways to proceed:

- **Create a single entity** — pick a type and fill in its form.
- **Discover…** — scan existing entities and bulk-create virtual ones; one menu item per discoverable type (see [Bulk discovery](#bulk-discovery)).
- **Assign a sensor to several lights** — wire one sensor into many lights at once (see [Bulk assignment](#bulk-assignment)).
- **Convert virtual lights** — promote existing gated lights to two schedule profiles, or return scheduled lights to one gated profile (see [Light conversion](#light-conversion)).

The usual order:

1. Create the virtual **sensors** you want lights to react to (all optional): an occupancy sensor per real motion/presence sensor, a combined sensor to merge several, an illuminance sensor, a schedule sensor. When you create an occupancy sensor, take care to set its **occupancy timeout** to match the real sensor's own hold time — it's the anchor for everything downstream, and MoLight can't read it for you (see [the note in the reference](#virtual-occupancy-binary-sensor)).
2. Create a **Virtual Light** per room or light group, pointing it at the real `light` entities and referencing any of the sensors from step 1. Use a **Virtual Scheduled Light** instead when every setting may differ inside and outside a schedule. A virtual light with no sensors is still useful — it turns its lights off on a timer.
3. Optionally create a **Virtual Remote** entry per remote to drive lights from its buttons (see [Virtual Remote](#virtual-remote)).
4. Use the virtual light in dashboards and voice assistants instead of the real lights.

Order matters only in that a virtual entity must exist before another can reference it. Sensors are reusable — one occupancy or illuminance sensor can serve many lights. Every entry can be edited later via its **Configure** button, or removed independently.

### Choosing the entity ID

Every create form except the Virtual Remote's ends with an optional **Entity ID** field (a remote entry's only entity is its diagnostic sensor, whose ID derives from the name) — handy when you name virtual entities after the real ones they wrap and don't want HA's `_2` suffix behavior. On the sensor forms this field — along with less-common options like the false-detection grace — sits in a collapsed **Advanced** section:

- **Leave it blank** to derive the ID from the name. If that ID is already taken, the flow warns you and offers to proceed (HA appends `_2`) or go back, prefilled, and set one yourself.
- **Type one** to pin it. A domain prefix is tolerated and stripped (`light.kitchen` → `kitchen`), the rest is slugified. A conflicting ID re-shows the form with an error. A virtual light's pinned ID also shapes its companion switch: `light.kitchen` → `switch.kitchen_auto_off`.

The field only appears when creating. Renaming later works through each entry's **Configure** button — every edit form has a **Name** field, and the entry title follows it — while the entity ID stays put (change that via HA's own entity settings).

### Bulk discovery

The three **Discover…** actions scan your existing entities and create a virtual wrapper for each pick:

- **Discover occupancy sensors** — every `binary_sensor` with device class `occupancy`, `motion`, or `presence`.
- **Discover illuminance sensors** — every `sensor` with device class `illuminance`.
- **Discover lights** — every `light` entity.

Each starts with an optional filter form: pick **areas** and/or **labels** to narrow the scan (an entity matches through its own assignment or its device's; with both filters set, an entity must match an area *and* carry a label), and choose whether the checklist starts with everything **pre-selected** (bulk-add, the default) or empty (handy when you only want a few). Leave the filters blank to see everything.

The next form shows the checklist of matching entities. Only useful candidates appear: MoLight's own entities, disabled entities, and anything already wrapped are hidden — re-running discovery later only offers what's new.

An optional **prefix**/**suffix** distinguishes the virtual entities from the real ones, applied verbatim to the source's friendly name (you control the spacing), with a target of your choice:

- **Entity ID** (default) — only the entity ID gets the affix, as the slug of the composed name (`v_` on a sensor named "Hallway Motion" → `binary_sensor.v_hallway_motion`); the friendly name stays identical to the source.
- **Name** — the friendly name gets the affix, and the entity ID derives from the composed name.

A final form then lets you adjust the default settings applied to every pick — for discovered lights that includes the occupancy/illuminance/schedule references. Each created entity can still be edited individually afterwards via **Configure**. Discovery creates regular Virtual Lights; eligible gated lights can then be promoted through **Convert virtual lights**.

### Bulk assignment

**Assign a sensor to several lights** wires one shared sensor into many virtual lights in a single pass. Pick the kind of sensor, the sensor, and how the lights should use it:

- **Occupancy** — with a **role**: *regular* (turns lights on and off) or *maintain* (only holds an already-on light on).
- **Illuminance** — with its **mode** (`control` or `gate`).
- **Schedule** — with its **mode** (`follow`, gate-and-turn-off, gate-and-switch-state, or gate-and-keep-state).

The final step lists your virtual lights with current users of that sensor **pre-selected**, so the checklist doubles as an audit of the wiring. The submitted set is authoritative: ticked lights get the reference and mode, unticked pre-selected lights have it removed, and the summary reports how many were newly wired and how many had the reference removed (lights that already had the exact sensor and mode are left untouched and not counted).

Because occupancy feeds the turn-off countdown, the `light_timeout >= occupancy_timeout` guard applies here too: lights whose turn-off timeout is shorter than the sensor's effective timeout are skipped and named in the summary, so you can raise their timeouts and re-run.

Bulk assignment currently applies only to regular Virtual Lights. Configure the sensors for a Virtual Scheduled Light in its outside- and inside-schedule settings instead.

### Light conversion

**Convert virtual lights** changes existing entries in place, preserving their config entry, entity IDs, history, dashboard references, remote targets, and other entity references.

- **Gated → scheduled** — available for Virtual Lights with any gate behavior and a schedule sensor. Each light retains its own schedule. Its current settings become the inside-schedule profile; the outside profile keeps its timing, appearance, warnings, turn-on selection, and keep-on entities but starts without occupancy, maintain, illuminance, or door inputs. Turn-off, switch-state, and keep-state gates map to the same-named schedule-end actions. This creates a useful starting profile rather than promising identical runtime behavior: add any automatic inputs you want outside the schedule afterward.
- **Scheduled → gated** — every Virtual Scheduled Light that still has a schedule remains eligible. The inside-schedule profile becomes the regular Virtual Light settings, the shared schedule is retained as its gate, and the corresponding turn-off, switch-state, or keep-state behavior is selected. The outside-schedule profile is permanently discarded after an explicit confirmation warning.

Follow-mode Virtual Lights are not offered for conversion because their schedule directly owns the lights rather than selecting a settings policy.

## Examples

For worked examples with the exact field values to enter — a plain turn-off timer, a single-sensor room, the full occupancy/illuminance/schedule setup, a porch light, a night-only stairs light, and a day/night hallway light — see [EXAMPLES.md](EXAMPLES.md).

## Entity reference

### Virtual Occupancy Binary Sensor

Wraps a single real binary sensor (motion, presence, occupancy…). `on` mirrors the source directly; the value the rest of the system runs on is the `latest_occupied_time` attribute — the best estimate of when the person actually left.

| Config | Description |
|---|---|
| **Source sensor** | The real `binary_sensor` to wrap (device class `occupancy`, `motion`, or `presence`). MoLight's own occupancy entities are excluded — wrap the real sensor, or combine virtual ones with a [combined sensor](#virtual-combined-occupancy-binary-sensor) |
| **Occupancy timeout (s)** | Default `120`. The source's own hold time — **set this to match the real sensor** (see the note below). When it clears, `latest_occupied_time` is back-dated to `clear time − timeout` |
| **False-detection grace (s)** | `0` disables, default `3`. A cycle whose on-duration is at most `timeout + grace` contained exactly one instantaneous detection — the sensor never re-triggered during its hold time, so it was almost certainly a fly/heat blip. Such cycles don't advance `latest_occupied_time`, are counted in `false_detection_count`, and flag the clear via `last_clear_false_detection` so lights can turn off quickly |
| **Clear after unavailable (s)** | `0` disables, default `60`. If the source goes `unavailable`/`unknown` while occupancy is active, `latest_occupied_time` advances to the dropout moment immediately, and if the source hasn't recovered after this many seconds the occupancy clears, flagged via `last_clear_unavailable`. Never classified as a false detection — the room may still be occupied, so dependent lights run their normal gentle countdown. A recovery cancels the pending clear |

> [!IMPORTANT]
> **Set the occupancy timeout to match the real sensor's actual hold time.** MoLight can't read this from the source — it's a value you supply, and everything downstream is anchored to it: when MoLight decides the person *actually left*, the turn-off countdown, and false-detection classification. Set it too high and genuine occupancy can be misread as a false detection (`on_duration ≤ timeout + grace`), sending the lights off early via the quick-off path; set it wrong in either direction and turn-off timing drifts from reality.
>
> This value **has no effect on the source sensor** — it doesn't change the real sensor's hold time, it only tells MoLight what that hold time is. The two are not linked, so if you ever change the source sensor's own timeout, update this to match by hand.

Classification needs a real on-time. `last_on_time` survives restarts, but if motion began while HA was down there is nothing to restore — the source's `last_changed` is then just the restart moment, so the cycle in progress at boot is deliberately left unclassified and takes the normal countdown. A mid-run reload, where `last_changed` is genuine, still uses it.

Attributes: `latest_occupied_time`, `occupancy_timeout`, `last_on_time`, `last_clear_false_detection`, `false_detection_count`, `last_clear_unavailable`.

### Virtual Combined Occupancy Binary Sensor

Combines multiple MoLight occupancy sensors into one. Constituents are usually simple Virtual Occupancy Sensors, but other combined sensors can be nested too — the flows reject self-references and cycles, and a sensor can't hold both roles at once.

| Config | Description |
|---|---|
| **Trigger sensors** | Any one going `on` starts occupancy |
| **Maintain sensors** *(optional)* | Keep occupancy alive once started, but cannot start it alone |

`latest_occupied_time` is the max across all constituents, so each sub-sensor's individual timeout is respected. A combined cycle during which no constituent advanced `latest_occupied_time` was made up entirely of false cycles and is flagged/counted the same way as on the simple sensor.

After a restart, if the sensor's restored state was `on` and a *maintain* sensor still shows presence, occupancy is seeded `on` (the one exception to "maintain sensors never start occupancy" — the restored state is direct evidence it was already triggered before HA went down).

If the last constituent still `on` drops out of the state machine (its entry unloaded, the entity removed), occupancy clears immediately rather than holding forever — the combined-sensor counterpart of the simple sensor's *clear after unavailable* timeout. As there, the person is assumed present up to the dropout: `latest_occupied_time` advances to that moment so dependent lights run their normal gentle countdown, and the clear is never classified as a false detection. Constituents are MoLight's own sensors, which never blip `unavailable` in normal operation, so no grace period is needed.

Attributes: `latest_occupied_time` (max across all constituents), `last_clear_false_detection`, `false_detection_count`.

### Virtual Illuminance Binary Sensor

`on` = at or above the threshold (bright enough, no artificial lighting needed)
`off` = below the threshold (dark enough to warrant lighting)

| Config | Description |
|---|---|
| **Source sensor** | Any real `sensor` with `device_class: illuminance` |
| **Threshold (lx)** | Default `10`. The lux level at which the sensor reports `on` |
| **Hysteresis (lx)** | `0` disables (the default). Becomes bright at `threshold + hysteresis`, dark below `threshold − hysteresis`; readings inside the band hold the current state, suppressing flapping when the light level hovers around the threshold |

An unavailable or unparsable source holds the last known value — a lux sensor dropping out must not read as "it got dark". The state also survives restarts. Until a reading has been parsed (or restored) the sensor is `unavailable` rather than `off`, so a source that has never reported doesn't assert darkness; consumers treat that as "not bright".

Attributes: none beyond the standard bright/dark (`on`/`off`) state. The entity carries `device_class: light`, so HA's UI shows it as "Light detected" / "No light".

### Virtual Schedule Binary Sensor

A Virtual Schedule Sensor provides a reusable on/off schedule signal. Choose its definition when creating or configuring it:

- **Time window** — `on` while the current time is within a fixed-time and/or sun-based window. Transitions are event-scheduled (no polling) and fire within a second of the boundary. Overnight windows (e.g. 22:00 → 06:00) are supported. The form accepts one window per entry — create additional schedule entries for additional windows.
- **Binary sensor** — mirrors any existing `binary_sensor`. This promotes a helper, template, mode, or integration-provided sensor into MoLight's short schedule picker without exposing every binary sensor in every Virtual Light form. Other Virtual Schedule Sensors are excluded as sources to prevent chains and cycles.

**Invert output** is available for both definitions. A time-window schedule is then `on` outside its configured window; a source-backed schedule is `on` while its source is `off`. An unknown, unavailable, or missing source makes the Virtual Schedule Sensor unavailable and is never inverted to `on`.

The form has a **Window start** and a **Window end** section; each edge is a fixed time, a sun event, or both:

| Field | Description |
|---|---|
| **Time** | Fixed local time for this edge |
| **Sun event** | Anchor the edge to `sunset` or `sunrise` instead of — or as well as — the fixed time |
| **Sun offset (min)** | Minutes to shift the sun event; negative is before it (`−15` = 15 min before) |
| **Time vs. sun** | When both are set, whichever this picks wins: `latest` (the default) or `earliest` |

e.g. *start at the later of sunset − 15 min and 21:00*. On polar days where the sun event doesn't occur, the fixed time stands alone.

Attributes: `current_window_start` (identifies the effective `on` period; used by follow-mode lights for restart catch-up), `next_transition`, `source_entity`, `inverted`.

Source-backed schedules preserve their effective state and window marker across a temporary source outage. Virtual Lights do not treat that outage as a schedule boundary: gate modes block new automatic activation while the schedule is unavailable but leave already-on lights alone, and follow mode waits for the next valid schedule state. As with any generic binary sensor, a complete off/on cycle that happens entirely while Home Assistant is stopped cannot be reconstructed reliably; when startup is ambiguous, the restored window marker is preserved rather than re-triggering Follow mode.

### Virtual Light

Controls N real lights with an occupancy-aware state machine.

The form groups everything but the timeout into collapsible sections — *Sensors & triggers* (expanded), *Turn-on & turn-off behavior*, *Off warning sequence*, and *Advanced* (collapsed):

| Config | Description |
|---|---|
| **Lights to control** | The `light` entities to control — usually real lights, but another MoLight virtual light works too; deleting a member cleans up the reference like any other |
| **Turn-off timeout (s)** | Default `300`. Must be >= the occupancy timeout of any referenced occupancy entity |
| **False-detection off delay (s)** | Default `5`. When occupancy clears flagged as a false detection, lights that were lit *by that cycle* turn off after this short delay instead of the normal countdown. Lights turned on manually are never affected |
| **Auto-on brightness (%)** *(optional)* | Brightness applied when the light turns on *automatically* — by occupancy, a door opening, illuminance going dark, or a schedule window. Manual and physical turn-ons keep their own brightness. Blank = automatic turn-ons use the real lights' own last/default brightness |
| **Auto-on color temperature (K)** *(optional)* | White color temperature applied on automatic turn-ons, for members that support it (a warm hallway at night). Manual and physical turn-ons keep their own color. Mutually exclusive with the auto-on color |
| **Auto-on color** *(optional)* | RGB color applied on automatic turn-ons, for members that can show it. Mutually exclusive with the auto-on color temperature |
| **Turn-on selection entity** *(optional)* | The target `select` entity to set immediately before MoLight turns the lights on — for example, the preset select exposed by WLED. Choosing it opens a second step where the fixed option is selected from the target's currently offered options |
| **Option source entity** *(optional)* | An `input_select` or a different `select` whose current state supplies the target option at each off-to-on transition. The target itself is excluded. This lets Home Assistant automations, calendars, seasons, or any other logic decide the selection without duplicating that logic in MoLight |
| **Fixed/fallback option** *(required when a target is selected)* | The option to apply when no source is configured, or when the source is missing, unavailable, unknown, or does not match an option offered by the target. It can represent a preset, theme, mood, mode, or any integration-specific choice |
| **Auto-on fade (s)** *(optional)* | Fade time for automatic turn-ons. Blank or `0` sends no transition. Manual and physical turn-ons never get one |
| **Auto-off fade (s)** *(optional)* | Fade time for automatic turn-offs — timer expiry, bright forcing off, a window ending. A manual off is always immediate |
| **Effect warning duration (s)** | `0` disables. When the turn-off timer expires, first show a brief *effect* cue for this long instead of going dark (see [Effect / warn warning](#effect--warn-warning)) |
| **Effect brightness (%)** | Brightness during the effect stage. `0` blinks the real lights fully off — a distinct "about to turn off" flash |
| **Effect color** *(optional)* | RGB color during the effect stage, for members that can show it. Requires an effect brightness above `0` (a blink fully off has no color to show) |
| **Effect fade (s)** *(optional)* | Fade into the effect brightness. Must fit within the effect duration (a fade on a disabled stage is rejected too) |
| **Warning grace period (s)** | `0` disables. After the effect, the light stays on this long before finally turning off, giving you time to re-trigger |
| **Warning brightness (%)** *(optional)* | Brightness during the grace period. Blank keeps whatever brightness the light had before the warning began (full brightness if it never reported one) |
| **Warning color** *(optional)* | RGB color during the grace period — e.g. red as an unmissable "about to turn off" cue. Blank keeps the color the lights already had |
| **Warning fade (s)** *(optional)* | Fade into the warning brightness. Must fit within the grace period |
| **Occupancy sensor** *(optional)* | A MoLight occupancy sensor (simple or combined) |
| **Maintain occupancy sensor** *(optional)* | Keeps an already-on light on while occupied but never turns it on (see [Maintain occupancy sensor](#maintain-occupancy-sensor)) |
| **Illuminance sensor** *(optional)* | A MoLight Virtual Illuminance Binary Sensor |
| **Illuminance mode** | Default `control` — dark gates turn-ons AND turning bright forces the lights off. `gate` — dark gates turn-ons only; bright never turns lights off. Use `gate` when the lux sensor can see the controlled lights, which would otherwise oscillate |
| **Schedule sensor** *(optional)* | A MoLight Virtual Schedule Binary Sensor. The picker offers only MoLight schedule sensors; a legacy non-schedule reference from before this narrowing stays selectable until changed |
| **Schedule mode** | Default `follow` — the window turns the lights on at its start and off at its end (porch lights). The three **Gate** modes let occupancy and the door turn the lights on inside the window only, and differ in what the window's end does to a light that is still on (see [Schedule modes](#schedule-modes)) |
| **Door sensor** *(optional)* | A real door/contact binary sensor (`on` = open). Opening it turns the lights on, gated by darkness and a gate-mode window exactly like occupancy (see [Door sensor](#door-sensor)) |
| **Door mode** | Default `open` — opening turns the lights on with the normal timeout; the door is otherwise ignored. `open_close` — the lights stay on while the door is open and start the countdown when it closes |
| **Keep-on entities** *(optional)* | Any entities with an on/off state. While any is `on`, auto-off is held (see [Holding auto-off](#holding-auto-off)) |

#### Attributes

| Attribute | Description |
|---|---|
| `molight_state` | Current state-machine state, as a lowercase value: `idle`, `active`, `occupied`, `countdown`, `scheduled`, `effect`, `warn` |
| `auto_off_held` | Whether auto-off is currently held (Auto-off switch off or a keep-on entity on) |
| `last_on_physical` / `last_on_virtual` | Timestamp of the last turn-on at the wall vs. via the virtual light |
| `last_on_occupancy` / `last_on_illuminance` | Timestamp of the last turn-on caused by occupancy vs. going dark |
| `last_on_door` | Timestamp of the last turn-on caused by the door opening |
| `last_brightness_change_physical` / `last_brightness_change_virtual` | Timestamp of the last brightness change from each source |
| `last_color_change_physical` / `last_color_change_virtual` | Timestamp of the last color change from each source |
| `last_turn_on_selection_option` / `last_turn_on_selection_source` | The last successfully applied turn-on selection and where the value came from (see [Brightness, color, and fades](#brightness-color-and-fades)) |
| `warning_active` | Whether an effect/warn warning sequence is currently running; a restart mid-warning uses it to undo the interrupted warning and restore the pre-warning brightness and color |
| `pre_warn_brightness` / `pre_warn_color` | Brightness and color saved before an effect/warn stage, so a restart mid-warning can restore them; null except mid-sequence |
| `schedule_window_start` | Follow-mode window marker used for restart catch-up |

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
- `IDLE`/`ACTIVE`/`COUNTDOWN` + occupancy becomes active (and it's dark / in-window) → `OCCUPIED`
- Already-active occupancy is adopted the same way: turning the light on (manually or at the wall) while the occupancy sensor is on goes straight to `OCCUPIED`, as does illuminance turning dark or a gate-mode window opening while the light is on — a timer never expires despite presence. **Gate and switch state** and **Gate and keep state** only gate turning an off light on, so they do not block adoption while the light remains on
- `OCCUPIED` + occupancy clears → `COUNTDOWN`; the timer is anchored to the sensor's `latest_occupied_time`, so each sensor's hold time is respected: the lights go off at `latest_occupied_time + turn-off timeout` — i.e. the wall-clock wait after the sensor clears is `turn-off timeout − occupancy timeout`, which is why the former must be the larger of the two (the flows enforce it)
- `ACTIVE`/`COUNTDOWN` + timer expires → `EFFECT` → `WARN` → `IDLE` (with both stages disabled this collapses to going straight to `IDLE`)
- any state + all real lights turned off externally → `IDLE`
- follow-mode window start → `SCHEDULED`; occupancy and illuminance are ignored until the window ends. Boundaries are edge-triggered, so manual changes mid-window stand — including turning the light back on, which rejoins the window instead of starting a timer

Manual control is never gated: the user can always turn the virtual light on, even when it's bright or outside a schedule window. The current state is exposed as the `molight_state` attribute.

**Precedence when sources conflict.** With several sources configured on one light, control resolves top-down:

1. **Manual / physical control** — always wins and is never gated; a manual off turns the light off from any state. A manual off *mid follow-window* drops to `IDLE` and hands control back to the sensors until the next window boundary.
2. **[Holding auto-off](#holding-auto-off)** — while the Auto-off switch is off or a keep-on entity is on, every *automatic* turn-off below (timers, forced offs, window ends) is suspended; only a manual off still turns the light off.
3. **Follow-mode schedule window** — while `SCHEDULED`, the window owns the light: occupancy, maintain, illuminance, and door changes are ignored entirely (window start forces on, window end forces off).
4. **Forced offs** — bright in illuminance `control` mode and a **Gate and turn off** window ending both turn the light off even while occupancy or a held-open door is active.
5. **Occupancy and door opening** — turn the light on only when it's dark (illuminance off) *and* inside a gate-mode window; otherwise lowest priority. An `open_close` door then holds the light like occupancy until it closes.

**Going dark can re-light the room.** Illuminance is mostly a gate, but its `on → off` (bright → dark) edge is also a trigger while the lights are off: if occupancy is active (or an `open_close` door is open), the lights come on and are held; otherwise, if the previous on-period's countdown still has time left, the lights come back on for just that remainder (with an occupancy sensor configured, the remainder is anchored to `latest_occupied_time` as usual; without one, it is the turn-off timeout minus the time since the last manual/physical/occupancy/door turn-on). This covers the "lights forced off by morning brightness, then a dark storm rolls in" case without re-lighting long-empty rooms. Such turn-ons are stamped in `last_on_illuminance`.

#### Effect / warn warning

By default the light turns off the instant its timer expires. Setting an **effect** and/or **warn** duration flags the impending turn-off first, so a room isn't dropped into darkness without notice:

1. **Effect** — a brief cue for *effect warning duration* seconds: the real lights are driven to the *effect brightness* (`0` blinks them fully off). Skipped when its duration is `0`.
2. **Warn** — a grace period of *warning grace period* seconds at the *warning brightness* (or the brightness the light already had, if blank), then the lights turn off. Skipped when its duration is `0`.

Each stage can also show an optional **color** — a red warn stage is a much clearer "about to turn off" cue than a dim. Color-capable members show it; brightness-only members just show the stage brightness. A warn stage without a color of its own undoes an effect-stage recolor. One caveat: most lights restore their last color on the next turn-on, so after an auto-off that ended at the warning color, the *real* lights' next manual turn-on may come back in that color (the same already applies to the warning brightness).

Each stage can fade into its brightness over its optional *fade* time; a fade must fit inside its stage (a fade, brightness, or color on a disabled stage is rejected rather than silently ignored).

Throughout both stages the virtual light stays on. **Any re-trigger during the sequence behaves exactly as if the pre-off timer were still running** — occupancy or maintain becoming active, a manual or physical turn-on, or an external dim cancels the warning. A re-trigger that carries no brightness or color of its own (occupancy, a turn-on without an explicit brightness) restores the pre-warning brightness and color, so the interruption leaves no trace; a physical turn-on or an external dim/recolor brings its own values, which are honored instead. The restore is deliberately immediate (no fade). Holding auto-off mid-sequence aborts it the same way, and the forced-off rules (bright in `control` mode, a hard-gate/follow window ending) still turn the lights off during the sequence, just as they would mid-countdown.

#### Maintain occupancy sensor

The maintain occupancy sensor holds an already-on light on while it shows presence — it never turns the light on. Unlike a combined sensor's *maintain sensors* (which only extend occupancy started by a trigger sensor), it holds the light regardless of how it was lit: manual, wall switch, or occupancy. Typical use: an over-sensitive presence sensor (mmWave) that would false-trigger as an occupancy source but is perfect for keeping a room lit while someone sits still.

- The light being on with the maintain sensor on means `OCCUPIED` — whether the sensor turns on later, was already on at turn-on time, or both were already on at startup. Illuminance/schedule gating doesn't apply, since this is not a turn-on.
- The countdown starts only when the regular occupancy sensor *and* the maintain sensor are both clear, anchored to the latest `latest_occupied_time` of the two.
- Forced offs still win, exactly as they do over regular occupancy: bright in `control` mode, a hard-gate window ending, and a manual off all turn the light off immediately; a follow-mode window owns the light entirely.
- The false-detection quick off fires on a maintain clear only when *both* sensors flagged their clears false — genuine presence on either side earns the normal countdown.

#### Schedule modes

A schedule window has two edges: the **start**, when the schedule sensor goes `off → on` (22:00 for a 22:00–06:00 window), and the **end**, when it goes `on → off` (06:00). The **schedule mode** decides what each edge does and whether the window gates the sensors:

- **`follow`** — the window owns the light. The start turns it on, the end turns it off, and while it is on inside the window (`SCHEDULED`) occupancy, maintain, illuminance, and door changes are ignored. Outside the window nothing is gated: the sensors are fully live, so a motion sensor attached to a dusk-to-dawn porch light still lights it at 2pm.
- **The three Gate modes** behave identically outside the window and at the start, and differ only at the end:
  - *Outside the window*, occupancy and the door cannot turn the light on. Manual control still works.
  - *At the start*, the gate lifts and presence that is already standing is re-evaluated: if the light is off and it is dark, occupancy already being `on` (or an `open_close` door already open) turns it on; if the light is already on, that presence is adopted as `OCCUPIED`. Nothing is turned off at the start. This is why a hallway light can come on at 22:00 with nobody walking in — its motion sensor was already on.
  - *At the end*, for a light that is still on: **Gate and turn off** (`gate`) forces it off. **Gate and switch state** (`gate_switch`) recalculates its state and timer from current illuminance and presence plus occupancy/maintain history, which can leave it on, start a countdown, or find it already due. **Gate and keep state** (`gate_keep`) leaves its state, hold, countdown, warning, and timer untouched.

Take a hallway light gated 22:00–06:00 with a 5-minute timeout, and someone walking in at 05:58: under `gate` the light goes out at 06:00; under `gate_keep` its timer finishes normally at 06:03; under `gate_switch` the deadline is recomputed at 06:00 from when occupancy last saw them. `gate_switch` and `gate_keep` only gate turning an off light on, so while the light is on outside the window occupancy and an `open_close` door can still hold or re-hold it; under `gate`, a light that is on outside the window (turned on by hand, or held) ignores them.

The three Gate modes are the same three choices as a Virtual Scheduled Light's **At schedule end** action (`turn_off`, `switch`, `keep`); see [Virtual Scheduled Light](#virtual-scheduled-light).

#### Door sensor

A door sensor drives the light straight from a real door/contact `binary_sensor` (`on` = open) — a pantry, closet, wardrobe, or garage light. Opening the door is a turn-on trigger, gated by illuminance and a gate-mode schedule exactly like occupancy: it only lights the room when it's dark (if an illuminance sensor is set) and inside a gate window. What happens next depends on the **door mode**:

- **`open`** — opening turns the lights on with the normal turn-off timeout (`ACTIVE`), then the door is ignored: closing does nothing and the lights time out even if the door stays open. Re-opening re-triggers the timer. Use it as a momentary "someone came through here" trigger. Because only the opening counts, a door already standing open when a gate lifts — the room going dark or a gate-mode window starting — does nothing in this mode; close and re-open it (contrast `open_close` below).
- **`open_close`** — the open door *holds* the lights on with no timer (`OCCUPIED`, just like occupancy) for as long as it stays open, and closing starts the auto-off countdown. The close **defers to presence**: if a regular occupancy or maintain sensor is still active, the lights stay `OCCUPIED` — a closed door never cuts the lights over someone the room still sees. (A [keep-on hold](#holding-auto-off) also keeps them on: the countdown state is entered but, as with every hold, no timer runs until the hold releases.) An already-on light with the door open is adopted as `OCCUPIED` at startup, and forced offs (bright in `control` mode, a hard-gate window ending, a manual off) still win over a held-open door, just as they do over occupancy. A standing-open door is also re-evaluated when a gate lifts — the room going dark or a gate-mode window starting lights the room and holds it while the door stays open — and the door's last known state is cached, so a sensor that blips `unavailable` keeps holding until it reports closed.

The door sensor is a plain real sensor, so its picker is narrowed to door-ish device classes (door, garage door, opening, window) rather than to MoLight virtual sensors. The last door-driven turn-on is exposed as the `last_on_door` attribute.

#### Holding auto-off

Each virtual light also creates a companion **`<name> Auto-off` switch**. Auto-off is *held* while that switch is off **or** any configured keep-on entity is on:

- Every automatic turn-off is suspended — the timer, the false-detection quick off, bright-forces-off, and schedule window ends. The state machine keeps transitioning; it just never arms a timer.
- Turn-ons are unaffected (occupancy, going dark, and window starts still light the room), and a manual off always works.
- When the last hold releases, the light re-evaluates its rules: a follow or hard-gate window that ended while held turns it off now, as does being bright in `control` mode; active occupancy keeps it on (when it's dark / in-window, like any adoption — a **Gate and keep state** window is not required once the light is on); an active follow window keeps it `SCHEDULED`; otherwise a **fresh full timer** starts.
- A keep-on entity dropping to `unavailable`/`unknown` holds its last known value (a dead toggle never reads as "hold released" — or engaged). The one exception is startup, where there is no last known value to hold: a keep-on entity that is unavailable, unknown, or missing counts as not holding. The switch state survives restarts, and a held light adopted at startup won't start a timer.

Share one keep-on entity (e.g. `input_boolean.guest_mode`) across all your virtual lights for a global "don't touch the lights" toggle, or give a single room its own. The current hold status is exposed as the `auto_off_held` attribute.

#### Brightness, color, and fades

The virtual light supports brightness when its real lights do. Like color and fades, that is derived from the members, so a virtual light wrapping only smart plugs or non-dimmable bulbs advertises on/off rather than offering a slider none of them can move. External brightness changes on the real lights count as human activity and restart a running timer; brightness `0` is treated as off (and `0 → non-zero` as a turn-on). Physical and virtual changes are tracked separately (`last_brightness_change_physical` / `_virtual`), as are the reasons the light last activated (`last_on_physical` / `_virtual` / `_occupancy` / `_illuminance` / `_door`).

Color works the same way, and its capabilities come from the real lights: the virtual light offers a color wheel when any member can show a color and a color-temperature slider when any member supports one, and falls back to brightness — or to plain on/off when no member dims at all. Mixed setups need no configuration — a color command goes to *all* members in one call and Home Assistant filters/converts it per light, so the bulbs that can go red go red and the rest just dim. The virtual light mirrors the first lit member's color, and an external recolor restarts a running timer exactly like an external dim (`last_color_change_physical` / `_virtual`).

An optional **turn-on selection entity** lets MoLight choose a preset, theme, mood, mode, or other integration-specific setting through a `select` entity before turning on. For example, WLED exposes its presets this way. After choosing the target, the next form shows a **fixed/fallback option** picker populated from that target instead of requiring an exact value to be typed.

For conditional behavior, choose an **option source entity**: an `input_select` or another `select` whose current state is copied to the target at turn-on time. Home Assistant can then change that helper from a calendar or automation — for example, select a holiday theme during a date range, a game-night theme when the local team is playing, and the normal theme otherwise. When both entities advertise their option lists during setup, MoLight requires them to have at least one option in common; the source may still be a subset or superset of the target. If the source is unusable or its current value is not offered by the target, MoLight uses the required fixed fallback. The source is read only for each MoLight-commanded off-to-on transition; changing it while the light is already on does not reapply the selection.

MoLight selects the resolved option first, waits for that service call to finish, and then turns on the member lights. This happens for both manual and automatic Virtual Light commands. A physical member-light turn-on is left alone because applying a selection afterward could overwrite an intentional external choice. The last successfully applied value and where it came from are exposed as `last_turn_on_selection_option` and `last_turn_on_selection_source`.

The target's option list can change after configuration. If at turn-on time neither the source's value nor the fixed option is offered anymore — or the target select is missing, or the call fails — MoLight logs a warning and turns the lights on without applying any selection: a renamed preset must never leave the room dark. The two attributes keep the last *successful* selection in that case.

An optional **auto-on brightness** forces a level whenever the light comes on *automatically*; manual and physical turn-ons are left alone, so you can dim the room by hand without it snapping back. An **auto-on color temperature** *or* **auto-on color** does the same for color — think warm white for the night-time hallway. Likewise, the **auto-on** and **auto-off fades** apply only to automatic actions — flipping the switch always responds immediately. A blank fade sends no `transition` attribute at all, so lights keep their integration's default behavior.

Those configured fades are separate from a `transition` you pass on the service call yourself. A virtual light forwards that to its real lights, so `light.turn_on`/`light.turn_off` with a fade, or a scene applied with one, works as it would against the real lights — Home Assistant drops the fade per member for any bulb that can't do one. Transition support is advertised whenever any member can fade, and stays advertised while a member hasn't reported in yet, so a bulb that is slow to appear at startup can't silently cost you a fade.

#### Restarts and unavailability

- Real lights already on at startup are adopted (`ACTIVE` with a fresh timer); active occupancy (when dark / in-window) is claimed as `OCCUPIED`.
- Follow-mode windows use `schedule_window_start` as a marker: a boundary missed while HA was down is applied exactly once at startup, while a manual off mid-window is respected. A restart landing mid effect/warn restores the pre-warning brightness and color.
- Entities dropping to `unavailable`/`unknown` are never read as state changes, at any layer; recovery transitions are processed as real events. A source sensor that stays unavailable is handled by the occupancy sensor's *clear after unavailable* timeout, so a dead motion sensor can't hold lights on forever.

### Virtual Scheduled Light

A Virtual Scheduled Light controls the same kinds of real lights and has the same automation settings as a regular Virtual Light, but stores two complete settings sets. The chosen Virtual Schedule Sensor selects **outside-schedule settings** while it is off and **inside-schedule settings** while it is on. This can change the timeout, occupancy/maintain/illuminance/door/keep-on entities, automatic brightness and color, fades, warnings, and the generic turn-on selection.

Creation uses three main forms:

1. Choose the name, lights, required schedule sensor, what happens when that schedule ends, and optional Entity ID.
2. Configure the outside-schedule settings.
3. Configure the inside-schedule settings, initially copied from the completed outside-schedule settings.

If either settings set uses a turn-on selection entity, its usual selection form appears immediately after that settings form. Editing uses the same sequence, but preserves the two saved settings sets independently.

Crossing the schedule boundary in either direction switches which settings are used and immediately re-checks the light against the newly active profile: its sensors are re-read, newly selected active occupancy or a held-open `open_close` door can turn an off light on, a newly selected illuminance sensor that is bright in `control` mode forces an on light off, and keep-on holds are picked up. The start boundary (`off → on`) always does exactly this. Only the end boundary (`on → off`) is configurable, through **At schedule end** — the same three choices as a Virtual Light's Gate modes (see [Schedule modes](#schedule-modes)):

**Keep state** (the default) makes the end boundary behave exactly like the start. The only thing it preserves is a countdown or warning already running, which keeps its original duration and deadline; if the old settings held the light but the new settings do not, a fresh timeout starts.

**Switch state** differs from **Keep state** in that one respect only: on the `on → off` boundary it replaces an already-on light's running state and deadline using the outside profile. An outgoing effect/warning presentation is cancelled first and its pre-warning appearance restored. Bright outside illuminance in `control` mode forces the light off; active occupancy, maintain occupancy, or an open-and-close door adopts it as `OCCUPIED`. Otherwise the remaining timeout is calculated from the outside occupancy/maintain history. With no configured presence sensor — or with no usable history — the outside profile receives a fresh full timeout. If the resulting deadline is already due, the outside profile's effect/warning/automatic-off settings apply immediately. While Auto-off is held, the state is still recalculated but its timer or off is suppressed; releasing the hold follows the normal hold-release rules, including a fresh full timeout when no other current rule keeps or turns the light off.

**Turn off** uses the inside profile's automatic-off fade as the outside profile takes over. This automatic off respects Auto-off and the newly active outside profile's keep-on entities; a held boundary is applied when the hold releases — unless the light is turned off first, which discards it — and a boundary missed during a restart is caught up exactly once. A light that is already off at the boundary simply takes the outside profile's sensor check like the other actions, while a light the boundary turned off stays off until the outside profile's next sensor edge. Manual and automatic operation under the outside profile remain allowed.

The `active_settings` attribute reports `outside_schedule` or `inside_schedule`; `schedule_end_off_pending` reports whether an end-boundary off is waiting for an Auto-off/keep-on hold to release (changing **At schedule end** away from *Turn off* drops a waiting boundary; changing the schedule does not — the off was already observed and still applies when the hold releases); `active_settings_schedule` records the schedule those came from, so a missed end boundary is only caught up after a restart when the same schedule is still selected. On restart, a valid schedule state wins. On a first start with no usable state, outside-schedule settings are used. While that same schedule is unavailable or unknown, its last restored choice is kept; after changing to an unavailable schedule, outside-schedule settings are used until it reports a valid state. If the schedule entry is deleted, the reference is removed, outside-schedule settings are used, and the light remains manually usable until a new schedule is chosen in **Configure**.

This settings selector does not include a separate follow mode. Create it through the normal **Add entry** flow or promote an existing gated light through **Convert virtual lights**. Bulk discovery and bulk sensor assignment do not directly create or modify Virtual Scheduled Lights.

### Virtual Remote

Drives lights from the buttons of a remote control — a Lutron Pico, an IKEA Bilresa, or any remote whose buttons Home Assistant exposes as `event` entities. One entry replaces the pile of hand-written `automation:` blocks that dispatch on button events: pick the target lights, then bind each button's single and/or double click to an action.

> **Lutron Caséta Picos and keypads:** Home Assistant's `lutron_caseta` integration doesn't create `event` entities for its buttons, so out of the box Picos won't appear in the pickers. Install the companion [lutron-caseta-events](https://github.com/jharris4/lutron-caseta-events) integration — it exposes every Caséta button as an `event` entity on the remote's own device page, and they work here like any other button.

Presses execute through the light domain's public services, so a bound press on a MoLight virtual light gets full **manual-control semantics**: it is never gated by darkness or a schedule window, it cancels a running effect/warn off-warning (restoring the pre-warning brightness), and it restarts the turn-off timer. Targets are usually MoLight virtual lights, but any `light` entity works.

Each entry creates one diagnostic **`<name> Last Action` sensor** — its state is the last action the remote executed, with the source button, the resolved click (`single`/`double`), the raw `event_type`, and the time as attributes. It's the link between "a button fired" (visible on the source event entity) and "a light changed" (visible on the virtual light): watch it while setting up bindings to confirm they do what you meant, and check its logbook history to answer "why did that light turn on?". It deliberately starts `unknown` after a restart — a pre-restart action shown as current would be misleading.

| Config | Description |
|---|---|
| **Lights to control** | The `light` entities every bound button drives (at least one is required) |
| **Brightness step (%)** | Percent added/removed per brightness up/down click (default 10, range 1–50). Stepping up from off turns the lights on dim; stepping below the minimum turns them off |
| **Turn on / Turn off / Toggle** | Per action: the buttons whose **single click** and/or **double click** fire it |
| **Brightness up / Brightness down** | Same single/double pickers; each click steps the brightness once |
| **Preset 1 / Preset 2** | Same pickers, plus the values the preset applies: a **brightness**, and a **color temperature** *or* an **RGB color** (not both). Think of the Pico's favorite button |

Each button may appear in several actions, as long as no *(button, click)* pair is bound twice — e.g. a Bilresa button whose single click toggles and whose double click turns on. The form requires at least one binding overall, and a preset with buttons but no values is rejected (it would just be a turn-on pretending to be a preset).

**How clicks are recognized.** Ecosystems spell "single click" differently, so the discriminating event is resolved per button from the event entity's advertised `event_types`:

- Buttons that announce `multi_press_1`/`multi_press_2` (Matter multi-press, e.g. the Bilresa): single = `multi_press_1`, double = `multi_press_2`. The constituent `initial_press`/`short_release` of the same physical click never fire a binding twice.
- Zigbee2MQTT-style buttons with literal `single`/`double` map directly.
- Lutron Caséta buttons (via [lutron-caseta-events](https://github.com/jharris4/lutron-caseta-events)) announce `press`/`multi_tap`: single = `press`, double = `multi_tap`. Note classic Caséta bridges may never report multi-taps — a double-click binding is accepted but only fires if the bridge does.
- Hue-style buttons (and Matter without multi-press): single = `short_release`, falling back to `initial_press` for buttons that announce nothing better (it is last in priority because it also precedes a long press).

Two things worth knowing:

- On a multi-press-capable button, the device only confirms a *single* click after its multi-press window (~half a second) closes, so single clicks on a Bilresa have inherent latency. That's a device property, not something software can fix; Pico presses are instant.
- A button's first sighting after startup, and its recovery from `unavailable`, both carry the last (stale) event and are never replayed as a fresh press — the guards you'd otherwise write as `trigger.from_state` template conditions are built in.

Deleting a virtual light strips it from every remote's target list, like any other reference. For worked examples — a 5-button Pico and a 2-button Bilresa — see [EXAMPLES.md](EXAMPLES.md).

## Development

Local development uses **two independent containers**, each with a distinct job. They are unrelated (no shared network or startup dependency), but both bind port `8123`, so only one can run at a time.

| Container | Image | What it's for |
| --- | --- | --- |
| **Dev container** (`dev:*`) | generic Debian + Python 3.14 | Your toolchain — editing, `pytest`, `ruff`. VS Code attaches here. HA is pip-installed into a venv (`/opt/molight-venv`) as a library. |
| **HA runtime** (`hass:*`) | official `home-assistant:stable` | The real Home Assistant app, for manual/UI testing. Your integration is mounted read-only. |

npm scripts follow a `<target>:<action>` naming scheme so the prefix tells you which container you're touching.

### Running the tests

```bash
npm install         # install tooling
npm run hass:down   # free port 8123 (devcontainer and HA runtime can't coexist)
npm run dev:up      # start the devcontainer (fast after first build)
npm test
```

### Releasing

Development is done on the `develop` branch. Start a release by fast-forwarding
`main` to the tested `develop` commit, then prepare, commit and tag the release
on `main`:

```bash
git switch main
git merge --ff-only develop
git push
```

Once `main` is up to date, the rest is scripted. `npm run release <version>`
writes nothing on its own — it lists the changes a release would make (stamping
the changelog, bumping the manifest version, updating the compare links) and
prints the command that applies them. Applying then prints the git commands to
commit, tag and push. After pushing the tag, fast-forward `develop` to include
that release commit too, so development resumes with the stamped changelog and
new manifest version:

```bash
git switch develop
git merge --ff-only main
git push
```

Do not start new work on `develop` between the first merge and this final sync;
keeping the release window short ensures both updates remain fast-forwards.

### Working in the dev container

Every command below runs *inside* the dev container via `devcontainer exec`:

```bash
npm run dev:up        # create/start the dev container (fast after first build)
npm test              # pytest
npm run lint          # ruff check
npm run lint:fix      # ruff check --fix
npm run format        # ruff format
npm run format:check  # ruff format --check (what CI runs)
npm run dev:shell     # open a bash shell inside the container
npm run dev:stop      # stop the container, keeping it for a fast dev:up next time
npm run dev:down      # stop and remove the container (dev:up recreates it)
npm run dev:rebuild   # tear down and rebuild from scratch (e.g. after changing devcontainer.json)
```

If you open the repository locally rather than in the container, Pylance can't see the container's Python environment and flags every Home Assistant import as unresolved — see [LOCAL_DEV_NOTES.md](LOCAL_DEV_NOTES.md) for the editor-only fix.

### Running Home Assistant

There are two ways to get a live HA instance, depending on what you're testing:

```bash
npm run hass:up      # run stock HA (stable image) with the integration mounted read-only
npm run hass:down    # stop it and free port 8123
npm run hass:pull    # update the HA stable image

npm run dev:hass     # run HA from source inside the dev container — code is live-editable,
                     # and debugpy is available for breakpoints
```

Use `hass:up` to confirm behavior against a real, released HA build; use `dev:hass` for active development, where HA runs against your working tree and can be restarted and debugged in place. Remember to `hass:down` before starting the dev container (or vice versa) so port 8123 is free.

## Design notes

- Each virtual entity is its own config entry, so they can be created, edited, and removed independently. A Virtual Remote is mostly wiring between button event entities and target lights — its only entity is the diagnostic Last Action sensor.
- A virtual entity must exist before another can reference it (sensors before the lights that use them).
- Removing an entry strips references to its entities from the entries that survive it — a light whose schedule sensor is deleted loses the reference instead of keeping a gate that can never open.
- The `light_timeout >= occupancy_timeout` constraint is validated in both directions: creating/editing a light checks its referenced occupancy entity (including through a combined sensor), and raising an occupancy sensor's timeout checks every light that depends on it.
- Settings no chosen light could apply are rejected at the form rather than stored and silently ignored: a fade when none of the lights support transitions, a color temperature or a color when none can show one, a brightness when none of them dim. One capable light is enough — mixed groups are the point — and a light that hasn't reported its capabilities yet is never taken as proof, so the check can't block on incomplete information.
- Reference graphs stay sane by construction: an occupancy sensor can't wrap another MoLight occupancy entity, a combined sensor can't reference itself or form a cycle through other combined sensors, and a constituent can't be both a trigger and a maintain sensor.
- Once an entry's options have been edited, the options fully replace the original data (so cleared optional fields stay cleared).
