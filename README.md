# MoLight: Smart Virtual Light Control for Home Assistant

A [HACS](https://hacs.xyz) custom integration that provides composable virtual building blocks for lighting automation: wrap your real sensors and lights in virtual entities, wire them together from the UI, and get occupancy-, daylight-, and schedule-aware lighting without writing a single automation.

Writing these automations by hand is tedious, and the complexity grows fast once you account for real-world behavior: sensors with different hold timeouts, sensors that are good at *triggering* occupancy but not *maintaining* it (or vice versa), schedules that mix fixed times with sun events, Home Assistant restarts, and sources that drop to `unavailable` at the worst moment. MoLight handles all of that in a small set of reusable entities.

**The building blocks** (each is its own config entry, so create as many as you like):

| Entity | What it does |
|---|---|
| [Virtual Occupancy Sensor](#virtual-occupancy-binary-sensor) | Wraps one motion/presence sensor; estimates when the person *actually left* |
| [Virtual Combined Occupancy Sensor](#virtual-combined-occupancy-binary-sensor) | Merges several occupancy sensors with trigger/maintain roles |
| [Virtual Illuminance Sensor](#virtual-illuminance-binary-sensor) | Turns a lux reading into a steady bright/dark signal |
| [Virtual Schedule Sensor](#virtual-schedule-binary-sensor) | Reusable schedule signal from a time/sun window or another binary sensor, optionally inverted |
| [Virtual Combined Schedule Sensor](#virtual-combined-schedule-binary-sensor) | Combines schedules with any/all logic, e.g. a morning and an evening window for one lamp |
| [Virtual Light](#virtual-light) | Controls N real lights with an occupancy/illuminance/schedule-aware state machine |
| [Virtual Scheduled Light](#virtual-scheduled-light) | Uses a complete set of Virtual Light settings inside a schedule and another outside it |
| [Virtual Remote](#virtual-remote) | Binds remote-control buttons (Pico, Bilresa, and others) to light actions, with no automations |

**Highlights** (everything below is covered by the automated test suite):

- Lights turn off a configurable time after the person *actually left*: countdowns anchor to each sensor's own hold time, not the moment it happens to clear.
- Occupancy takes over manually turned-on lights, so they still turn off after the room empties, but false detections (a fly, a heat blip) are classified and never cut short lights the user turned on.
- Turn-ons can be gated on darkness and/or a schedule window; getting bright can force lights off (or not, for lux sensors that can see the lights they control).
- Follow-mode schedules give porch-light behavior (on at window start, off at window end) while respecting manual overrides mid-window.
- Optional effect/warn warning: blink or dim before an automatic turn-off, then a grace period to re-trigger, instead of sudden darkness.
- Every virtual light gets a companion **Auto-off switch**, and any on/off entity can act as a **keep-on hold** (guest mode, movie night) that suspends automatic turn-offs.
- Restarts and `unavailable` sources are handled everywhere: missed follow-mode and Virtual Scheduled Light boundaries are applied exactly once, sensor blips are never misread as state changes, and a dead motion sensor can't hold lights on forever.
- Virtual Remotes replace hand-written button automations: map single/double clicks of any remote whose buttons appear as `event` entities (IKEA Bilresa, Hue dimmer, and others, plus Lutron Picos via [lutron-caseta-events](https://github.com/jharris4/lutron-caseta-events)) to on/off/toggle/dim/preset actions, with the single-vs-double vocabulary read from each button itself.

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

Everything is configured from the UI, with no YAML. Adding an entry (the first via **Add Integration → MoLight**, later ones via **Add Entry** on the MoLight card) opens a menu with four ways to proceed:

- **Create a single entity**: pick a type and fill in its form.
- **Discover**: scan existing entities and bulk-create virtual ones, with one menu item per discoverable type (see [Bulk discovery](#bulk-discovery)).
- **Assign a sensor to several lights**: wire one sensor into many lights at once (see [Bulk assignment](#bulk-assignment)).
- **Convert virtual lights**: promote existing gated lights to two schedule profiles, or return scheduled lights to one gated profile (see [Light conversion](#light-conversion)).

The usual order:

1. Create the virtual **sensors** you want lights to react to (all optional): an occupancy sensor per real motion/presence sensor, a combined sensor to merge several, an illuminance sensor, a schedule sensor. When you create an occupancy sensor, take care to set its **occupancy timeout** to match the real sensor's own hold time. It's the anchor for everything downstream, and MoLight can't read it for you (see [the note in the reference](#virtual-occupancy-binary-sensor)).
2. Create a **Virtual Light** per room or light group, pointing it at the real `light` entities and referencing any of the sensors from step 1. Use a **Virtual Scheduled Light** instead when every setting may differ inside and outside a schedule. A virtual light with no sensors is still useful: it turns its lights off on a timer.
3. Optionally create a **Virtual Remote** entry per remote to drive lights from its buttons (see [Virtual Remote](#virtual-remote)).
4. Use the virtual light in dashboards and voice assistants instead of the real lights.

Order matters only in that a virtual entity must exist before another can reference it. Sensors are reusable: one occupancy or illuminance sensor can serve many lights. Every entry can be edited later via its **Configure** button, or removed independently.

### Choosing the entity ID

Every create form except the Virtual Remote's ends with an optional **Entity ID** field, handy when you name virtual entities after the real ones they wrap and don't want HA's `_2` suffix behavior. (A remote entry's only entity is its diagnostic sensor, whose ID derives from the name.) On the sensor forms this field sits in a collapsed **Advanced** section, along with less-common options like the false-detection grace:

- **Leave it blank** to derive the ID from the name. If that ID is already taken, the flow warns you and offers to proceed (HA appends `_2`) or go back, prefilled, and set one yourself.
- **Type one** to pin it. A domain prefix is tolerated and stripped (`light.kitchen` → `kitchen`), the rest is slugified. A conflicting ID re-shows the form with an error. A virtual light's pinned ID also shapes its companion switch: `light.kitchen` → `switch.kitchen_auto_off`.

The field only appears when creating. Renaming later works through each entry's **Configure** button (every edit form has a **Name** field, and the entry title follows it), while the entity ID stays put (change that via HA's own entity settings). MoLight follows an entity ID changed there, of a virtual entity or of a real one it uses: see [Restarts and unavailability](#restarts-and-unavailability).

### Bulk discovery

The three **Discover** actions scan your existing entities and create a virtual wrapper for each pick:

- **Discover occupancy sensors**: every `binary_sensor` with device class `occupancy`, `motion`, or `presence`.
- **Discover illuminance sensors**: every `sensor` with device class `illuminance`.
- **Discover lights**: every `light` entity.

Each starts with an optional filter form: pick **areas** and/or **labels** to narrow the scan (an entity matches through its own assignment or its device's; with both filters set, an entity must match an area *and* carry a label), and choose whether the checklist starts with everything **pre-selected** (bulk-add, the default) or empty (handy when you only want a few). Leave the filters blank to see everything.

The next form shows the checklist of matching entities. Only useful candidates appear: MoLight's own entities, disabled entities, and anything already wrapped are hidden, so re-running discovery later only offers what's new.

An optional **prefix**/**suffix** distinguishes the virtual entities from the real ones, applied verbatim to the source's friendly name (you control the spacing), with a target of your choice:

- **Entity ID** (default): only the entity ID gets the affix, as the slug of the composed name (`v_` on a sensor named "Hallway Motion" → `binary_sensor.v_hallway_motion`); the friendly name stays identical to the source.
- **Name**: the friendly name gets the affix, and the entity ID derives from the composed name.

A final form then lets you adjust the default settings applied to every pick; for discovered lights that includes the occupancy/illuminance/schedule references. A pick that stopped being a candidate while the forms were open (wrapped from another tab, disabled or removed) is skipped, and the summary says how many. Each created entity can still be edited individually afterwards via **Configure**. Discovery creates regular Virtual Lights; eligible gated lights can then be promoted through **Convert virtual lights**.

### Bulk assignment

**Assign a sensor to several lights** wires one shared sensor into many virtual lights in a single pass. Pick the kind of sensor, the sensor, and how the lights should use it:

- **Occupancy**, with a **role**: *regular* (turns lights on and off) or *maintain* (only holds an already-on light on).
- **Illuminance**, with its **mode** (`control` or `gate`).
- **Schedule**, with its **mode** (`follow`, gate-and-turn-off, gate-and-switch-state, or gate-and-keep-state).

The final step lists your virtual lights with current users of that sensor **pre-selected**, so the checklist doubles as an audit of the wiring. The submitted set is authoritative: ticked lights get the reference and mode, unticked pre-selected lights have it removed, and the summary reports how many were newly wired and how many had the reference removed (lights that already had the exact sensor and mode are left untouched and not counted).

Because occupancy feeds the turn-off countdown, the `light_timeout >= occupancy_timeout` guard applies here too: lights whose turn-off timeout is shorter than the sensor's effective timeout are skipped and named in the summary, so you can raise their timeouts and re-run.

Bulk assignment currently applies only to regular Virtual Lights. Configure the sensors for a Virtual Scheduled Light in its outside- and inside-schedule settings instead.

### Light conversion

**Convert virtual lights** changes existing entries in place, preserving their config entry, entity IDs, history, dashboard references, remote targets, and other entity references. A **Configure** form of a light that was opened before its conversion can no longer be saved; open it again.

- **Gated → scheduled**: available for Virtual Lights with any gate behavior and a schedule sensor. Each light retains its own schedule. Its current settings become the inside-schedule profile; the outside profile keeps its timing, appearance, warnings, turn-on selection, and keep-on entities but starts without occupancy, maintain, illuminance, or door inputs. Turn-off, switch-state, and keep-state gates map to the same-named schedule-end actions. This creates a useful starting profile rather than promising identical runtime behavior: add any automatic inputs you want outside the schedule afterward.
- **Scheduled → gated**: every Virtual Scheduled Light that still has a schedule remains eligible. The inside-schedule profile becomes the regular Virtual Light settings, the shared schedule is retained as its gate, and the corresponding turn-off, switch-state, or keep-state behavior is selected. The outside-schedule profile and any [standby](#standby) settings are permanently discarded after an explicit confirmation warning.

Follow-mode Virtual Lights are not offered for conversion because their schedule directly owns the lights rather than selecting a settings policy.

## Examples

For worked examples with the exact field values to enter, see [EXAMPLES.md](EXAMPLES.md). It covers a plain turn-off timer, a single-sensor room, a living room with occupancy, maintain, illuminance, a warning blink and a turn-on selection, a porch light, home-only lighting from an inverted away-mode schedule, a night-only stairs light, a door-driven storage room light, a day/night hallway light, a Pico and a Bilresa remote, and a bedside lamp on for a morning and an evening window.

## Entity reference

### Virtual Occupancy Binary Sensor

Wraps a single real binary sensor (motion, presence, or occupancy). `on` mirrors the source directly; the value the rest of the system runs on is the `latest_occupied_time` attribute, the best estimate of when the person actually left.

| Config | Description |
|---|---|
| **Source sensor** | The real `binary_sensor` to wrap (device class `occupancy`, `motion`, or `presence`). MoLight's own occupancy entities are excluded; wrap the real sensor, or combine virtual ones with a [combined sensor](#virtual-combined-occupancy-binary-sensor) |
| **Occupancy timeout (s)** | Default `120`, range 1 to 3600. The source's own hold time; **set this to match the real sensor** (see the note below). When it clears, `latest_occupied_time` is back-dated to `clear time - timeout` |
| **False-detection grace (s)** | `0` disables, default `3`, range 0 to 60. A cycle whose on-duration is at most `timeout + grace` contained exactly one instantaneous detection: the sensor never re-triggered during its hold time, so it was almost certainly a fly/heat blip. Such cycles don't advance `latest_occupied_time`, are counted in `false_detection_count`, and flag the clear via `last_clear_false_detection` so lights can turn off quickly |
| **Clear after unavailable (s)** | `0` disables, default `60`, range 0 to 3600. If the source goes `unavailable`/`unknown` while occupancy is active, `latest_occupied_time` advances to the dropout moment immediately, and if the source hasn't recovered after this many seconds the occupancy clears, flagged via `last_clear_unavailable`. Never classified as a false detection, since the room may still be occupied, so dependent lights run their normal gentle countdown. A recovery cancels the pending clear |

> [!IMPORTANT]
> **Set the occupancy timeout to match the real sensor's actual hold time.** MoLight can't read this from the source. It's a value you supply, and everything downstream is anchored to it: when MoLight decides the person *actually left*, the turn-off countdown, and false-detection classification. Set it too high and genuine occupancy can be misread as a false detection (`on_duration <= timeout + grace`), sending the lights off early via the quick-off path; set it wrong in either direction and turn-off timing drifts from reality.
>
> This value **has no effect on the source sensor**. It doesn't change the real sensor's hold time; it only tells MoLight what that hold time is. The two are not linked, so if you ever change the source sensor's own timeout, update this to match by hand.

Classification needs a real on-time. `last_on_time` survives restarts, but if motion began while HA was down there is nothing to restore: the source's `last_changed` is then just the restart moment, so the cycle in progress at boot is deliberately left unclassified and takes the normal countdown. A mid-run reload, where `last_changed` is genuine, still uses it.

Changing the source sensor leaves the old source's presence behind: the sensor follows the new source from its first valid reading, and a new source that is `unavailable` at the time does not keep it `on`.

Attributes: `latest_occupied_time`, `occupancy_timeout`, `last_on_time`, `last_clear_false_detection`, `false_detection_count`, `last_clear_unavailable`.

### Virtual Combined Occupancy Binary Sensor

Combines multiple MoLight occupancy sensors into one. Constituents are usually simple Virtual Occupancy Sensors, but other combined sensors can be nested too; the flows reject self-references and cycles, and a sensor can't hold both roles at once.

| Config | Description |
|---|---|
| **Trigger sensors** | Any one going `on` starts occupancy |
| **Maintain sensors** *(optional)* | Keep occupancy alive once started, but cannot start it alone |

`latest_occupied_time` is the max across all constituents, so each sub-sensor's individual timeout is respected. A combined cycle during which no constituent advanced `latest_occupied_time` was made up entirely of false cycles and is flagged/counted the same way as on the simple sensor. History the constituents already carry when the combined sensor is created, or when a reload picks up a newly added constituent, is the visit the next cycle is measured against. A cycle that is running through a restart or reload keeps the detection it already contained, and a clear keeps its false-detection flag until the next one, unless the trigger or maintain sensors were edited.

After a restart, if the sensor's restored state was `on` and a *maintain* sensor still shows presence, occupancy is seeded `on` (the one exception to "maintain sensors never start occupancy", because the restored state is direct evidence it was already triggered before HA went down). A restored `on` that is still waiting for a maintain sensor to load is saved as waiting, so it is carried across even when HA restarts again before that sensor reports.

If the last constituent still `on` drops out of the state machine (it goes `unavailable`, or the entity is removed), occupancy clears immediately rather than holding forever. This is the combined-sensor counterpart of the simple sensor's *clear after unavailable* timeout. As there, the person is assumed present up to the dropout: `latest_occupied_time` advances to that moment so dependent lights run their normal gentle countdown, and the clear is never classified as a false detection. The one exception is a reload: editing a constituent's options unloads its entry and brings it back a moment later, so a constituent that was `on` when its entry unloaded keeps counting as `on` for up to 10 seconds, and occupancy clears only if it has not reported by then.

Attributes: `latest_occupied_time` (max across all constituents), `last_clear_false_detection`, `false_detection_count`.

### Virtual Illuminance Binary Sensor

`on` = at or above the threshold (bright enough, no artificial lighting needed)
`off` = below the threshold (dark enough to warrant lighting)

| Config | Description |
|---|---|
| **Source sensor** | Any real `sensor` with `device_class: illuminance` |
| **Threshold (lx)** | Default `10`, at most 100000. The lux level at which the sensor reports `on`. Must be above `0`, since dark means a reading below it |
| **Hysteresis (lx)** | `0` disables (the default), at most 10000. Becomes bright at `threshold + hysteresis`, dark below `threshold - hysteresis`; readings inside the band hold the current state, suppressing flapping when the light level hovers around the threshold. Must be smaller than the threshold: the form rejects a band whose dark edge would sit below `0 lx`, which no sensor can ever report, latching the state bright forever |

An unavailable or unparsable source holds the last known value, since a lux sensor dropping out must not read as "it got dark". The state also survives restarts. Until a reading has been parsed (or restored) the sensor is `unavailable` rather than `off`, so a source that has never reported doesn't assert darkness; consumers treat that as "not bright". The first-ever reading is judged against the bare threshold rather than the hysteresis band, since there is no held state yet for the band to preserve. A held value belongs to the source it was read from: choosing another **Source sensor** drops it, so the sensor is `unavailable` until the new source reports and that reading is judged against the bare threshold too. Editing the threshold or hysteresis does not drop it, and nor does changing the source's entity ID.

Attributes: none beyond the standard bright/dark (`on`/`off`) state. The entity carries `device_class: light`, so HA's UI shows it as "Light detected" / "No light".

### Virtual Schedule Binary Sensor

A Virtual Schedule Sensor provides a reusable on/off schedule signal. Choose its definition when creating or configuring it:

- **Time window**: `on` while the current time is within a fixed-time and/or sun-based window. Transitions are event-scheduled (no polling) and fire within a second of the boundary. Overnight windows (e.g. 22:00 → 06:00) are supported. The form accepts one window per entry; to follow several windows (say a morning and an evening), combine schedules with a [Virtual Combined Schedule Sensor](#virtual-combined-schedule-binary-sensor).
- **Binary sensor**: mirrors any existing `binary_sensor`. This promotes a helper, template, mode, or integration-provided sensor into MoLight's short schedule picker without exposing every binary sensor in every Virtual Light form. MoLight's own schedules are excluded as sources to prevent chains and cycles; combine them with a Virtual Combined Schedule Sensor instead.

**Invert output** is available for both definitions. A time-window schedule is then `on` outside its configured window; a source-backed schedule is `on` while its source is `off`. An unknown, unavailable, or missing source makes the Virtual Schedule Sensor unavailable and is never inverted to `on`.

The form has a **Window start** and a **Window end** section; each edge is a fixed time, a sun event, or both:

| Field | Description |
|---|---|
| **Time** | Fixed local time for this edge |
| **Sun event** | Anchor the edge to `sunset` or `sunrise` either instead of the fixed time or alongside it |
| **Sun offset (min)** | Minutes to shift the sun event; negative is before it (`-15` = 15 min before). Range -720 to 720 |
| **Time vs. sun** | When both are set, whichever this picks wins: `latest` (the default) or `earliest` |

e.g. *start at the later of 15 min before sunset and 21:00*. On polar days where the sun event doesn't occur, the fixed time stands alone. On the nights the clocks change, a fixed time in the spring-forward gap fires an hour later on the clock (02:30 fires at 03:30), and the window is skipped that night if that puts its start after its end; a fixed time in the repeated autumn hour fires at its first occurrence.

Attributes: `current_window_start` (identifies the effective `on` period, where overlapping windows count as one; used by follow-mode lights for restart catch-up; the literal `inverted` when an inverted schedule has no boundary to date it from), `next_transition`, `source_entity`, `inverted`.

Source-backed schedules preserve their effective state and window marker across a temporary source outage. Virtual Lights do not treat that outage as a schedule boundary: gate modes block new automatic activation while the schedule is unavailable but leave already-on lights alone, and follow mode waits for the next valid schedule state. As with any generic binary sensor, a complete off/on cycle that happens entirely while Home Assistant is stopped cannot be reconstructed reliably; when startup is ambiguous, the restored window marker is preserved rather than re-triggering Follow mode.

### Virtual Combined Schedule Binary Sensor

Combines MoLight schedules into one schedule that any light can use. Inputs can be Virtual Schedule Sensors of either definition or other Virtual Combined Schedule Sensors, so mixed logic nests, e.g. *(Morning **any** Evening) **all** Workday*. The flows reject a combined schedule that would include itself, directly or through another.

| Config | Description |
|---|---|
| **Schedules** | The schedules to combine (at least one). To use another binary sensor, such as a workday sensor, wrap it in a source-backed Virtual Schedule Sensor first |
| **Combine with** | **Any** (the default): `on` while at least one schedule is `on`. **All**: `on` only while every schedule is `on` |
| **Invert output** | `on` while the combination is `off`. This is also how to invert a combined schedule, since a source-backed schedule can't mirror a MoLight one |

Nested combined schedules are expanded down to the schedules they're built from: time-window schedules are read from their config and source-backed ones from their state. The result never depends on another combined schedule's state or on the order entities start up, and a sensor reached through two routes (two mirrors of one sensor, or the same schedule in two branches) is never seen half-updated. Editing any schedule underneath rebuilds the combination.

Where one window ends as another begins, or windows overlap, the combination stays `on` without a blip and `current_window_start` is the start of the whole period, so a follow-mode light treats it as one window. Each separate period is a new window, so a light turned off manually in the morning still comes on in the evening. Back-to-back days of one input merge too: an all-day (00:00 → 00:00) schedule is one window per day on its own, but one unbroken window inside a combination.

An input that is `unavailable` makes the combination `unavailable` only when it could change the result: with **Any**, another input that is `on` keeps it `on`; with **All**, another that is `off` keeps it `off`. A disabled input counts as `unavailable` too, whether its entry or only its entity is disabled. As with source-backed schedules, an outage is never a boundary: the window marker is kept unless an input is known to have been `off` since the period began, and while Home Assistant starts the restored state is held until inputs report. That includes restarts: a source-backed input's history while Home Assistant was down is unknown, so if it could have kept the combination `on` through the downtime, a window that started meanwhile keeps the old marker rather than re-triggering follow mode. Time-window inputs alone are always caught up. With no schedules left (every input deleted), it is `off`.

Attributes: `current_window_start` (the literal `always_on` for an inverted combination with no inputs), `next_transition`, `operator`, `inverted`, `resolved_schedules` (the plain schedules it was built from, for tracing why it's `on`).

### Virtual Light

Controls N real lights with an occupancy-aware state machine.

The form keeps the name, the lights, and the timeout at the top level and groups everything else into collapsible sections: *Sensors & triggers* (expanded), *Turn-on & turn-off behavior*, *Off warning sequence*, and *Advanced* (collapsed):

| Config | Description |
|---|---|
| **Lights to control** | The `light` entities to control: usually real lights, but another MoLight virtual light works too; deleting a member cleans up the reference like any other |
| **Turn-off timeout (s)** | Default `300`, range 1 to 14400 (4 h). Must be >= the occupancy timeout of the referenced occupancy and maintain occupancy sensors |
| **False-detection off delay (s)** | Default `5`, range 0 to 300. When occupancy clears flagged as a false detection, lights that were lit *by that cycle* turn off after this short delay instead of the normal countdown. Lights turned on manually are never affected, nor is a light that was dimmed, recoloured or turned on at the wall after that cycle lit it |
| **Auto-on brightness (%)** *(optional)* | Brightness applied when the light turns on *automatically* (by occupancy, a door opening, illuminance going dark, or a schedule window). Manual and physical turn-ons keep their own brightness. Blank = automatic turn-ons use the real lights' own last/default brightness. Range 1 to 100 |
| **Auto-on color mode** *(optional)* | Whether automatic turn-ons apply the color temperature below, the color below, or (**None**) neither. Only the selected field is used, and choosing **None** is how a previously set color is cleared, since the color fields themselves can't be blanked once set. Left blank (as it starts while no color is set), both fields are kept as entered, so the form rejects setting both |
| **Auto-on color temperature (K)** *(optional)* | White color temperature applied on automatic turn-ons, for members that support it (a warm hallway at night). Manual and physical turn-ons keep their own color. Range 2000 to 6500 K |
| **Auto-on color** *(optional)* | RGB color applied on automatic turn-ons, for members that can show it. Manual and physical turn-ons keep their own color |
| **Turn-on selection entity** *(optional)* | The target `select` entity to set immediately before MoLight turns the lights on, for example the preset select exposed by WLED. Choosing it opens a second step where the fixed option is selected from the target's currently offered options |
| **Option source entity** *(optional)* | An `input_select` or a different `select` whose current state supplies the target option at each off-to-on transition. The target itself is excluded. This lets Home Assistant automations, calendars, seasons, or any other logic decide the selection without duplicating that logic in MoLight |
| **Fixed/fallback option** *(required when a target is selected)* | The option to apply when no source is configured, or when the source is missing, unavailable, unknown, or does not match an option offered by the target. It can represent a preset, theme, mood, mode, or any integration-specific choice |
| **Auto-on fade (s)** *(optional)* | Fade time for automatic turn-ons. Blank or `0` sends no transition. Manual and physical turn-ons never get one. Range 0 to 300, in 0.1 s steps |
| **Auto-off fade (s)** *(optional)* | Fade time for automatic turn-offs (timer expiry, bright forcing off, a window ending). A manual off is always immediate. Range 0 to 300, in 0.1 s steps |
| **Effect warning duration (s)** | Default `0` (disabled), range 0 to 3600. When the turn-off timer expires, first show a brief *effect* cue for this long instead of going dark (see [Effect / warn warning](#effect--warn-warning)) |
| **Effect brightness (%)** | Brightness during the effect stage. Default `0`, range 0 to 100. `0` blinks the real lights fully off for a distinct "about to turn off" flash |
| **Effect color mode** *(optional)* | Like the auto-on color mode: picks the effect color temperature, the effect color, or **None** (which also clears a previously set effect color) |
| **Effect color temperature (K)** *(optional)* | White color temperature during the effect stage, for members that support it, giving temp-only bulbs a warning cue too. Requires an effect brightness above `0`. Range 2000 to 6500 K |
| **Effect color** *(optional)* | RGB color during the effect stage, for members that can show it. Requires an effect brightness above `0` (a blink fully off has no color to show) |
| **Effect fade (s)** *(optional)* | Fade into the effect brightness. Must fit within the effect duration (a fade on a disabled stage is rejected too). Range 0 to 300, in 0.1 s steps |
| **Warning grace period (s)** | Default `0` (disabled), range 0 to 3600. After the effect, the light stays on this long before finally turning off, giving you time to re-trigger |
| **Warning brightness (%)** *(optional)* | Brightness during the grace period. Blank keeps whatever brightness the light had before the warning began (full brightness if it never reported one). Range 1 to 100 |
| **Warning color mode** *(optional)* | Like the auto-on color mode: picks the warning color temperature, the warning color, or **None** (which also clears a previously set warning color) |
| **Warning color temperature (K)** *(optional)* | White color temperature during the grace period, for members that support it. Blank keeps the color the lights already had. Range 2000 to 6500 K |
| **Warning color** *(optional)* | RGB color during the grace period, e.g. red as an unmissable "about to turn off" cue. Blank keeps the color the lights already had |
| **Warning fade (s)** *(optional)* | Fade into the warning brightness. Must fit within the grace period. Range 0 to 300, in 0.1 s steps |
| **Occupancy sensor** *(optional)* | A MoLight occupancy sensor (simple or combined) |
| **Maintain occupancy sensor** *(optional)* | Keeps an already-on light on while occupied but never turns it on (see [Maintain occupancy sensor](#maintain-occupancy-sensor)) |
| **Illuminance sensor** *(optional)* | A MoLight Virtual Illuminance Binary Sensor |
| **Illuminance mode** | Default `control`: dark gates turn-ons AND turning bright forces the lights off. `gate`: dark gates turn-ons only; bright never turns lights off. Use `gate` when the lux sensor can see the controlled lights, which would otherwise oscillate |
| **Schedule sensor** *(optional)* | A MoLight schedule: a Virtual Schedule Sensor or a Virtual Combined Schedule Sensor. The picker offers only MoLight schedules; a legacy non-schedule reference from before this narrowing stays selectable until changed |
| **Schedule mode** | Default `follow`: the window turns the lights on at its start and off at its end (porch lights). The three **Gate** modes let occupancy and the door turn the lights on inside the window only, and differ in what the window's end does to a light that is still on (see [Schedule modes](#schedule-modes)) |
| **Door sensor** *(optional)* | A real door/contact binary sensor (`on` = open). Opening it turns the lights on, gated by darkness and a gate-mode window exactly like occupancy (see [Door sensor](#door-sensor)) |
| **Door mode** | Default `open`: opening turns the lights on with the normal timeout; the door is otherwise ignored. `open_close`: the lights stay on while the door is open and start the countdown when it closes |
| **Keep-on entities** *(optional)* | Any entities with an on/off state. While any is `on`, auto-off is held (see [Holding auto-off](#holding-auto-off)) |

#### Attributes

| Attribute | Description |
|---|---|
| `molight_state` | Current state-machine state, as a lowercase value: `idle`, `active`, `occupied`, `countdown`, `scheduled`, `standby`, `effect`, `warn` |
| `auto_off_held` | Whether auto-off is currently held (Auto-off switch off or a keep-on entity on) |
| `last_on_physical` / `last_on_virtual` | Timestamp of the last turn-on at the wall vs. via the virtual light |
| `last_on_occupancy` / `last_on_illuminance` | Timestamp of the last turn-on caused by occupancy vs. going dark |
| `last_on_door` | Timestamp of the last turn-on caused by the door opening |
| `last_off_manual` | Timestamp of the last turn-off by hand, through the virtual light or at the wall; a restart uses it to keep a manual off over presence the light already had |
| `last_brightness_change_physical` / `last_brightness_change_virtual` | Timestamp of the last brightness change from each source |
| `last_color_change_physical` / `last_color_change_virtual` | Timestamp of the last color change from each source |
| `last_turn_on_selection_option` / `last_turn_on_selection_source` | The last successfully applied turn-on selection and where the value came from: the option source entity's ID, or `fixed`. Both start empty after a restart (see [Brightness, color, and fades](#brightness-color-and-fades)) |
| `warning_active` | Whether an effect/warn warning sequence is currently running; a restart mid-warning uses it to undo the interrupted warning and restore the pre-warning brightness and color |
| `pre_warn_brightness` / `pre_warn_color` | Brightness and color saved before an effect/warn stage, so a restart mid-warning can restore them; null except mid-sequence |
| `schedule_window_start` | Follow-mode window marker used for restart catch-up |
| `schedule_window_schedule` | The follow schedule that marker was saved for; a marker saved for another schedule is not restored |
| `bright_forced_off` | Whether the light is off because brightness forced it off; only then can going dark resume the on-period |
| `bright_resume_until` | While `bright_forced_off`: when the countdown that brightness interrupted would have ended, or one timeout after the off for a light presence was holding. Going dark before then resumes the on-period; null when only a sensor's `latest_occupied_time` can |
| `active_settings` / `active_settings_schedule` / `schedule_end_off_pending` | Virtual Scheduled Light only: which settings profile is live, the schedule it was derived from, and whether an end-boundary off is waiting on an auto-off hold to release (see [Virtual Scheduled Light](#virtual-scheduled-light)) |
| `standby_suppressed` | Virtual Scheduled Light only: whether a manual off has turned [standby](#standby) off until the next schedule boundary |
| `manual_off_cleared` | Virtual Scheduled Light only: whether a schedule boundary has ended the last manual off, so a restart does not bring it back |
| `active_settings_window_start` | Virtual Scheduled Light only: the schedule window the inside settings are running in, so a restart in a later window is treated as a boundary |

#### State machine

```
IDLE       lights off, no timer
ACTIVE     lights on, timer running (manual/external turn-on, no occupancy)
OCCUPIED   lights on, occupancy active, timer suspended
COUNTDOWN  occupancy cleared, timer ticking toward lights-off
SCHEDULED  lights on inside a follow-mode window, no timer
STANDBY    lights at a Virtual Scheduled Light's standby level, no timer
EFFECT     auto-off imminent, showing the brief effect/blink warning stage
WARN       auto-off imminent, grace period before the lights go off
```

- `IDLE` + manual/external turn-on → `ACTIVE` (timer starts)
- `IDLE`/`ACTIVE`/`COUNTDOWN` + occupancy becomes active (and it's dark / in-window) → `OCCUPIED`
- Already-active occupancy is adopted the same way: turning the light on (manually or at the wall) while the occupancy sensor is on goes straight to `OCCUPIED`, as does illuminance turning dark or a gate-mode window opening while the light is on, so a timer never expires despite presence. **Gate and switch state** and **Gate and keep state** only gate turning an off light on, so they do not block adoption while the light remains on
- `OCCUPIED` + occupancy clears → `COUNTDOWN`; the timer is anchored to the sensor's `latest_occupied_time`, so each sensor's hold time is respected: the lights go off at `latest_occupied_time + turn-off timeout`. The wall-clock wait after the sensor clears is therefore `turn-off timeout - occupancy timeout`, which is why the former must be the larger of the two (the flows enforce it). A false detection leaves `latest_occupied_time` at an earlier visit, so a light turned on manually, at the wall, or by the door since then keeps the full turn-off timeout from that turn-on instead, as does one dimmed or recoloured at the wall, also while occupancy had it on
- `ACTIVE`/`COUNTDOWN` + timer expires → `EFFECT` → `WARN` → `IDLE` (with both stages disabled this collapses to going straight to `IDLE`)
- any state + all real lights turned off externally → `IDLE`
- follow-mode window start → `SCHEDULED`; occupancy and illuminance are ignored until the window ends. Boundaries are edge-triggered, so manual changes mid-window stand, including turning the light back on, which rejoins the window instead of starting a timer. An all-day (00:00 → 00:00) schedule starts a new window every midnight without turning `off` in between, so a light turned off manually comes back on at midnight

Manual control is never gated: the user can always turn the virtual light on, even when it's bright or outside a schedule window. The current state is exposed as the `molight_state` attribute.

**Precedence when sources conflict.** With several sources configured on one light, control resolves top-down:

1. **Manual / physical control**: always wins and is never gated; a manual off turns the light off from any state. A manual off *mid follow-window* drops to `IDLE` and hands control back to the sensors until the next window boundary.
2. **[Holding auto-off](#holding-auto-off)**: while the Auto-off switch is off or a keep-on entity is on, every *automatic* turn-off below (timers, forced offs, window ends) is suspended; only a manual off still turns the light off.
3. **Follow-mode schedule window**: while `SCHEDULED`, the window owns the light, and occupancy, maintain, illuminance, and door changes are ignored entirely (window start forces on, window end forces off).
4. **Forced offs**: bright in illuminance `control` mode and a **Gate and turn off** window ending both turn the light off even while occupancy or a held-open door is active.
5. **Occupancy and door opening**: turn the light on only when it's dark (illuminance off) *and* inside a gate-mode window; otherwise lowest priority. An `open_close` door then holds the light like occupancy until it closes.

**Going dark can re-light the room.** Illuminance is mostly a gate, but its `on → off` (bright → dark) edge is also a trigger while the lights are off: if occupancy is active (or an `open_close` door is open), the lights come on and are held; otherwise, if brightness forced the lights off and that on-period still has time left, the lights come back on for just that remainder. The on-period lasts until the later of two moments: when the countdown that brightness interrupted would have ended, and one turn-off timeout after the room was last occupied (the `latest_occupied_time` of the occupancy or maintain sensor). The interrupted countdown is the one running at that moment, however it was started or restarted: a turn-on, a dim or recolor at the wall, a released keep-on entity or Auto-off switch, a sensor clearing, a door, or a schedule boundary. Its end is saved as `bright_resume_until`, so a restart keeps it. A light that presence was holding had no countdown running: someone was there when brightness turned it off, so it counts as lasting one timeout from that moment, or longer if the sensors then report a later departure. A light in its effect/warn warning had no countdown left either, and only a recent `latest_occupied_time` brings it back. This covers the "lights forced off by morning brightness, then a dark storm rolls in" case without re-lighting long-empty rooms. An on-period that ended any other way (a manual off, the timer, a schedule window ending) is never resumed, and one that brightness cut short is over as soon as such an end comes while the light is off: a manual off, or a Virtual Scheduled Light's *Turn off* schedule end, also one crossed while Home Assistant was down. *Keep state* and *Switch state* carry an on-period past the end, so they still resume it. Such turn-ons are stamped in `last_on_illuminance`.

#### Effect / warn warning

By default the light turns off the instant its timer expires. Setting an **effect** and/or **warn** duration flags the impending turn-off first, so a room isn't dropped into darkness without notice:

1. **Effect**: a brief cue for *effect warning duration* seconds, during which the real lights are driven to the *effect brightness* (`0` blinks them fully off). Skipped when its duration is `0`.
2. **Warn**: a grace period of *warning grace period* seconds at the *warning brightness* (or the brightness the light already had, if blank), then the lights turn off. Skipped when its duration is `0`.

Each stage can also show an optional **color**: a white color temperature *or* an RGB color, so temp-only bulbs get a cue too, and a red warn stage is a much clearer "about to turn off" cue than a dim. Color-capable members show it; brightness-only members just show the stage brightness. A warn stage without a color of its own undoes an effect-stage recolor. One caveat: most lights restore their last color on the next turn-on, so after an auto-off that ended at the warning color, the *real* lights' next manual turn-on may come back in that color (the same already applies to the warning brightness).

Each stage can fade into its brightness over its optional *fade* time; a fade must fit inside its stage (a fade, brightness, or color on a disabled stage is rejected rather than silently ignored).

Throughout both stages the virtual light stays on. **Any re-trigger during the sequence behaves exactly as if the pre-off timer were still running**: occupancy or maintain becoming active, a manual or physical turn-on, or an external dim cancels the warning. A re-trigger that carries no brightness or color of its own (occupancy, a turn-on without an explicit brightness) restores the pre-warning brightness and color, so the interruption leaves no trace; a physical turn-on or an external dim/recolor brings its own values, which are honored instead. The restore is deliberately immediate (no fade). Holding auto-off mid-sequence aborts it the same way, and the forced-off rules (bright in `control` mode, a hard-gate/follow window ending) still turn the lights off during the sequence, just as they would mid-countdown.

#### Maintain occupancy sensor

The maintain occupancy sensor holds an already-on light on while it shows presence, but never turns the light on. Unlike a combined sensor's *maintain sensors* (which only extend occupancy started by a trigger sensor), it holds the light regardless of how it was lit: manual, wall switch, or occupancy. Typical use: an over-sensitive presence sensor (mmWave) that would false-trigger as an occupancy source but is perfect for keeping a room lit while someone sits still.

- The light being on with the maintain sensor on means `OCCUPIED`, whether the sensor turns on later, was already on at turn-on time, or both were already on at startup. Illuminance/schedule gating doesn't apply, since this is not a turn-on.
- The countdown starts only when the regular occupancy sensor *and* the maintain sensor are both clear, anchored to the latest `latest_occupied_time` of the two.
- Forced offs still win, exactly as they do over regular occupancy: bright in `control` mode, a hard-gate window ending, and a manual off all turn the light off immediately; a follow-mode window owns the light entirely.
- The false-detection quick off fires on a maintain clear only when *both* sensors flagged their clears false; genuine presence on either side earns the normal countdown.

#### Schedule modes

A schedule window has two edges: the **start**, when the schedule sensor goes `off → on` (22:00 for a 22:00 to 06:00 window), and the **end**, when it goes `on → off` (06:00). The **schedule mode** decides what each edge does and whether the window gates the sensors:

- **`follow`**: the window owns the light. The start turns it on, the end turns it off, and while it is on inside the window (`SCHEDULED`) occupancy, maintain, illuminance, and door changes are ignored. Outside the window nothing is gated: the sensors are fully live, so a motion sensor attached to a dusk-to-dawn porch light still lights it at 2pm. A real light coming back from `unavailable` (a reboot, a power cut, an integration reload) is made to match the schedule: inside the window it is re-lit with the window's settings and turn-on selection, outside it is turned off. Whatever it booted into is not treated as a manual change, and a manual dim or color change mid-window is replaced along with it. Only while the light stays connected does a manual on or off stand. A member that is still loading when Home Assistant starts is not a reboot: its first state is handled by the startup rules below.
- **The three Gate modes** behave identically outside the window and at the start, and differ only at the end:
  - *Outside the window*, occupancy and the door cannot turn the light on. Manual control still works.
  - *At the start*, the gate lifts and presence that is already standing is re-evaluated: if the light is off and it is dark, occupancy already being `on` (or an `open_close` door already open) turns it on; if the light is already on, that presence is adopted as `OCCUPIED`. Nothing is turned off at the start. This is why a hallway light can come on at 22:00 with nobody walking in: its motion sensor was already on.
  - *At the end*, for a light that is still on: **Gate and turn off** (`gate`) forces it off. **Gate and switch state** (`gate_switch`) recalculates its state and timer from current illuminance and presence plus occupancy/maintain history, which can leave it on, start a countdown, or find it already due. **Gate and keep state** (`gate_keep`) leaves its state, hold, countdown, warning, and timer untouched.

Take a hallway light gated 22:00 to 06:00 with a 5-minute timeout, and someone walking in at 05:58: under `gate` the light goes out at 06:00; under `gate_keep` its timer finishes normally at 06:03; under `gate_switch` the deadline is recomputed at 06:00 from when occupancy last saw them. `gate_switch` and `gate_keep` only gate turning an off light on, so while the light is on outside the window occupancy and an `open_close` door can still hold or re-hold it; under `gate`, a light that is on outside the window (turned on by hand, or held) ignores them.

The three Gate modes are the same three choices as a Virtual Scheduled Light's **At schedule end** action (`turn_off`, `switch`, `keep`); see [Virtual Scheduled Light](#virtual-scheduled-light).

#### Door sensor

A door sensor drives the light straight from a real door/contact `binary_sensor` (`on` = open), such as a pantry, closet, wardrobe, or garage light. Opening the door is a turn-on trigger, gated by illuminance and a gate-mode schedule exactly like occupancy: it only lights the room when it's dark (if an illuminance sensor is set) and inside a gate window. What happens next depends on the **door mode**:

- **`open`**: opening turns the lights on with the normal turn-off timeout (`ACTIVE`), then the door is ignored. Closing does nothing and the lights time out even if the door stays open. Re-opening re-triggers the timer. Use it as a momentary "someone came through here" trigger. Because only the opening counts, a door already standing open when a gate lifts (the room going dark or a gate-mode window starting) does nothing in this mode; close and re-open it (contrast `open_close` below).
- **`open_close`**: the open door *holds* the lights on with no timer (`OCCUPIED`, just like occupancy) for as long as it stays open, and closing starts the auto-off countdown. The close **defers to presence**: if a regular occupancy or maintain sensor is still active, the lights stay `OCCUPIED`, because a closed door never cuts the lights over someone the room still sees. (A [keep-on hold](#holding-auto-off) also keeps them on: the countdown state is entered but, as with every hold, no timer runs until the hold releases.) An already-on light with the door open is adopted as `OCCUPIED` at startup, and forced offs (bright in `control` mode, a hard-gate window ending, a manual off) still win over a held-open door, just as they do over occupancy. A standing-open door is also re-evaluated when a gate lifts: the room going dark or a gate-mode window starting lights the room and holds it while the door stays open. The door's last known state is cached too, so a sensor that blips `unavailable` keeps holding until it reports closed. A door sensor that is deleted or disabled is no blip: it counts as closed.

The door sensor is a plain real sensor, so its picker is narrowed to door-ish device classes (door, garage door, opening, window) rather than to MoLight virtual sensors. The last door-driven turn-on is exposed as the `last_on_door` attribute.

#### Holding auto-off

Each virtual light also creates a companion **`<name> Auto-off` switch**. Auto-off is *held* while that switch is off **or** any configured keep-on entity is on:

- Every automatic turn-off is suspended: the timer, the false-detection quick off, bright-forces-off, and schedule window ends. The state machine keeps transitioning; it just never arms a timer. A keep-on entity that also has another role holds before that role acts, so a sensor that is both the illuminance input and a keep-on entity holds the light as it gets bright instead of turning it off.
- Turn-ons are unaffected (occupancy, going dark, and window starts still light the room), and a manual off always works.
- When the last hold releases, the light re-evaluates its rules: a follow or hard-gate window that ended while held turns it off now, as does being bright in `control` mode; active occupancy keeps it on (when it's dark / in-window, like any adoption; a **Gate and keep state** window is not required once the light is on); an active follow window keeps it `SCHEDULED`; otherwise a **fresh full timer** starts.
- A keep-on entity dropping to `unavailable`/`unknown` holds its last known value (a dead toggle never reads as "hold released", or as engaged). The one exception is startup, where there is no last known value to hold: a keep-on entity that is unavailable, unknown, or missing counts as not holding. The switch state survives restarts, and a held light adopted at startup won't start a timer. Disabling the Auto-off switch entity releases its hold straight away, since a disabled switch can't be turned back on; re-enabling it brings back the state it had. A keep-on entity that is deleted or disabled stops holding in the same way.

Share one keep-on entity (e.g. `input_boolean.guest_mode`) across all your virtual lights for a global "don't touch the lights" toggle, or give a single room its own. The current hold status is exposed as the `auto_off_held` attribute.

#### Brightness, color, and fades

The virtual light supports brightness when its real lights do. Like color and fades, that is derived from the members, so a virtual light wrapping only smart plugs or non-dimmable bulbs advertises on/off rather than offering a slider none of them can move. External brightness changes on the real lights count as human activity and restart a running timer; brightness `0` is treated as off (and `0 → non-zero` as a turn-on). Physical and virtual changes are tracked separately (`last_brightness_change_physical` / `_virtual`), as are the reasons the light last activated (`last_on_physical` / `_virtual` / `_occupancy` / `_illuminance` / `_door`).

Color works the same way, and its capabilities come from the real lights: the virtual light offers a color wheel when any member can show a color and a color-temperature slider when any member supports one, and falls back to brightness, or to plain on/off when no member dims at all. Mixed setups need no configuration: a color command goes to *all* members in one call and Home Assistant filters/converts it per light, so the bulbs that can go red go red and the rest just dim. The virtual light mirrors the first lit member's color, and an external recolor restarts a running timer exactly like an external dim (`last_color_change_physical` / `_virtual`).

An optional **turn-on selection entity** lets MoLight choose a preset, theme, mood, mode, or other integration-specific setting through a `select` entity before turning on. For example, WLED exposes its presets this way. After choosing the target, the next form shows a **fixed/fallback option** picker populated from that target instead of requiring an exact value to be typed. If the target is unavailable during setup its options cannot be inspected, so any entered value is accepted (like every capability check in the forms, this only blocks a provable mismatch), and the value is instead verified at each turn-on, with the fallback behavior described below.

For conditional behavior, choose an **option source entity**: an `input_select` or another `select` whose current state is copied to the target at turn-on time. Home Assistant can then change that helper from a calendar or automation. For example, it can select a holiday theme during a date range, a game-night theme when the local team is playing, and the normal theme otherwise. When both entities advertise their option lists during setup, MoLight requires them to have at least one option in common; the source may still be a subset or superset of the target. If the source is unusable or its current value is not offered by the target, MoLight uses the required fixed fallback. The source is read only for each MoLight-commanded off-to-on transition; changing it while the light is already on does not reapply the selection.

MoLight selects the resolved option first, waits for that service call to finish, and then turns on the member lights. This happens for both manual and automatic Virtual Light commands. The virtual light itself reports `on`, at the brightness and colour asked for, as soon as it accepts the turn-on, so a toggle or a brightness step made while the select call runs acts on a light that is on: two quick brightness-up clicks on a remote add up, and a second toggle turns the light off again. If something cancels the turn-on before it is sent, the virtual light reports `off` again and the member lights are never lit. A physical member-light turn-on is left alone because applying a selection afterward could overwrite an intentional external choice. For the same reason, a real light turned on, dimmed or recoloured at the wall while a turn-on, automatic or made through the virtual light, is still waiting for the select call keeps what was set at the wall: the later action wins, the waiting turn-on is dropped, and the on-period is the user's. A real light that only reports in already lit during the wait (it was still loading) was not changed at the wall, and the turn-on is still sent. A real light that the select call itself turns on (a preset or a scene script that lights it) is not taken for a change at the wall. That holds when the change is reported under the select call, and, while the call is in progress, for any real light on the same device as the select entity (a WLED preset that includes "on" reports the light by a push of its own). A change made at that device itself during those moments cannot be told apart, and the waiting turn-on is sent over it. Nor is a real light that loads, or comes back from `unavailable`, as off while the select call is in progress taken for a turn-off: the turn-on is still sent, and no manual off is recorded. While the select call is in progress the turn-on already counts as an on light: a schedule end or a gate window end that would turn an on light off cancels it, manual or automatic, instead of letting it light the room afterwards, and releasing a keep-on hold then applies whatever the hold was keeping from it, an end's off or the normal timeout. The last successfully applied value and where it came from are exposed as `last_turn_on_selection_option` and `last_turn_on_selection_source`. The source is the option source entity's ID, or `fixed` when the fixed/fallback option was applied. Neither attribute is restored, so both are empty after a restart or an options edit until the next selection is applied.

The target's option list can change after configuration. If at turn-on time neither the source's value nor the fixed option is offered anymore, the target select is missing or unavailable, or the call fails, MoLight logs a warning and turns the lights on without applying any selection: a renamed preset must never leave the room dark. The two attributes keep the last *successful* selection in that case.

An optional **auto-on brightness** forces a level whenever the light comes on *automatically*; manual and physical turn-ons are left alone, so you can dim the room by hand without it snapping back. An **auto-on color temperature** *or* **auto-on color** does the same for color, such as warm white for the night-time hallway. Likewise, the **auto-on** and **auto-off fades** apply only to automatic actions; flipping the switch always responds immediately. A blank fade sends no `transition` attribute at all, so lights keep their integration's default behavior.

Those configured fades are separate from a `transition` you pass on the service call yourself. A virtual light forwards that to its real lights, so `light.turn_on`/`light.turn_off` with a fade, or a scene applied with one, works as it would against the real lights. Home Assistant drops the fade per member for any bulb that can't do one. Transition support is advertised whenever any member can fade, and stays advertised while a member hasn't reported in yet, so a bulb that is slow to appear at startup can't silently cost you a fade.

#### Restarts and unavailability

- Until Home Assistant has finished starting, a virtual light reports the state it had when Home Assistant stopped (on or off, `molight_state` and, on a Virtual Scheduled Light, `active_settings`), then works out its real state. A light that was on does not flick to `off` and back at startup, a virtual light that wraps another one reads its saved state, and a crash or power cut shortly after a restart restores the state the light really had. A service call made before startup finishes is acted on at once and stands.
- Real lights already on at startup are adopted (`ACTIVE` with a fresh timer); active occupancy (when dark / in-window) is claimed as `OCCUPIED`.
- A light whose last change was a manual off stays off over presence it already had, as it would without the restart: occupancy still on lights it only when its sensor dates the visit after the off, as a Virtual Occupancy Sensor does with `last_on_time`. With another kind of sensor, or a visit whose start the sensor did not see, it stays off until the next visit, and a door first seen open after startup does not light it either. Any turn-on since the off ends this, and so does a Virtual Scheduled Light's settings boundary, also one missed while HA was down, and it stays ended across later restarts (`manual_off_cleared`); with standby, the manual off holds for its window as described under [Standby](#standby).
- Gate modes keep no window marker, so a gate-mode window that ended while HA was down is not applied at startup, even under **Gate and turn off**: a light still on is adopted like any other, with a fresh timer.
- Editing a virtual light's options reloads it. If its real lights are on, it adopts them as the startup rules would: an active follow window claims them as `SCHEDULED`, a keep-on hold keeps them on without a timer, and otherwise they get a fresh timer, so a running countdown restarts in full with the new timeout.
- Changing an entity ID in Home Assistant's entity settings is followed. Every entry that references the entity (as a real light, sensor, schedule, door, keep-on entity, select, button or target light) is rewritten to the new ID as soon as the entity reports under it, or after 10 seconds if it does not, and reloads like after an options edit. The change is not taken for something happening in the room: a light turned off by hand stays off, presence that was already there is not a new visit, an occupancy sensor whose source was renamed keeps its running cycle, and a follow-mode window marker or a Virtual Scheduled Light's active settings stay with the renamed schedule. A MoLight entity whose own ID is changed keeps its state as well: an Auto-off switch that is off stays off, and a light, sensor or schedule carries on from what it knew, as it does through a reload. The IDs are followed while Home Assistant runs with at least one MoLight entry loaded.
- Follow-mode windows use `schedule_window_start` as a marker: a boundary missed while HA was down is applied exactly once at startup, while a manual off mid-window is respected. A schedule that is `unavailable` or missing when startup finishes is not a window end: the marker is kept until the schedule reads again. The marker belongs to the schedule that set it (`schedule_window_schedule`): an options edit that picks another schedule, or another schedule mode, drops it, and the light is adopted as it is instead of being turned off for a window end that never happened.
- A restart landing mid effect/warn restores the pre-warning brightness and colour on real lights that are still lit. Real lights found off, as during an effect stage that blinks them off, stay off: the automatic off counts as done, and the room is only lit again if the ordinary startup rules light it.
- A real light that cannot be read when startup finishes (still loading, `unavailable` or `unknown`) is not taken to be off, so what the restart had to settle stays owed to it until it reports. If it reports lit, a missed follow-mode or *Turn off* end turns it off (under a keep-on hold it stays on and the end applies when the hold releases), a missed *Switch state* end recalculates it, and an interrupted warning restores its pre-warning brightness and colour instead of adopting the warning's. If it reports off there is nothing left to do. A turn-on in the meantime, or the next schedule window starting, replaces a missed end, so a light lit on purpose is never turned off by it; a turn-off does not. What is owed is kept in the saved attributes and survives another restart, with two exceptions. A missed *Switch state* end is forgotten: after a second restart that real light is adopted with a fresh timeout. A warning restore is only kept while the virtual light is off: if another real light of the group was readable and has the virtual light on again, a second restart before the late one loads leaves it at the warning's brightness and colour, which the virtual light then reports, until the next command.
- Entities dropping to `unavailable`/`unknown` are never read as state changes, at any layer; a recovery to a different value is processed as a real event (except a real light, covered next). A recovery to the value the entity had before the outage is not an event: an occupancy sensor that comes back still occupied does not re-light a room that was turned off manually, and a schedule that comes back still `off` does not turn off a light that was turned on manually. That is also what happens when a MoLight sensor or schedule reloads after its options are edited. A light turned on during the outage is still picked up by the occupancy or window that returns, and a start the outage blocked (occupancy arriving while the gate was unreadable, or a gate lifting while the occupancy sensor was) is applied when it returns, unless the light was turned off by hand after that. A source sensor that stays unavailable is handled by the occupancy sensor's *clear after unavailable* timeout, so a dead motion sensor can't hold lights on forever.
- An entity that is deleted or disabled while Home Assistant runs, in its entity settings or along with its integration entry, can never report again, so it is treated as startup treats a missing one and not as an outage: a keep-on entity stops holding, an `open_close` door counts as closed, and an occupancy or maintain sensor counts as clear. A light it was holding gets a fresh full timeout, unless other presence still holds it; a sensor that comes back is read again as when first seen. A real light that is deleted or disabled no longer counts as possibly on: turning the remaining real lights off turns the virtual light off, and when the only lit real light goes, the virtual light reports off without recording a manual off or commanding the others. A disabled real light is owed nothing after a restart either. A schedule or illuminance sensor that goes missing stays as described above: no window end, and not bright.
- A real light that loads late, or comes back from `unavailable` (a reboot, a power cut, an integration reload), as off or at brightness 0 is never taken for a manual off. While the virtual light is on, the real light missed its command: the real lights are sent the light's current settings and turn-on selection again, and the running timer or warning carries on. While the virtual light is off, nothing happens. A real light that comes back on while the virtual light is on has its brightness and colour mirrored without restarting the timer. A follow-mode real light that reboots is reconciled with its schedule instead, and a light at standby is sent the standby settings.

### Virtual Scheduled Light

A Virtual Scheduled Light controls the same kinds of real lights and has the same automation settings as a regular Virtual Light, but stores two complete settings sets. The chosen MoLight schedule (a Virtual Schedule Sensor or a Virtual Combined Schedule Sensor) selects **outside-schedule settings** while it is off and **inside-schedule settings** while it is on. This can change the timeout, occupancy/maintain/illuminance/door/keep-on entities, automatic brightness and color, fades, warnings, and the generic turn-on selection. The inside-schedule settings can also rest at a [standby](#standby) level instead of turning off.

Creation uses three main forms:

1. Choose the name, lights, required schedule sensor, what happens when that schedule ends, and optional Entity ID.
2. Configure the outside-schedule settings.
3. Configure the inside-schedule settings, initially copied from the completed outside-schedule settings.

If either settings set uses a turn-on selection entity, its usual selection form appears immediately after that settings form. Editing uses the same sequence, but preserves the two saved settings sets independently.

Crossing the schedule boundary in either direction switches which settings are used and immediately re-checks the light against the newly active profile: its sensors are re-read, newly selected active occupancy or a held-open `open_close` door can turn an off light on, a newly selected illuminance sensor that is bright in `control` mode forces an on light off, and keep-on holds are picked up. The start boundary (`off → on`) always does exactly this. Only the end boundary (`on → off`) is configurable, through **At schedule end**, which has the same three choices as a Virtual Light's Gate modes (see [Schedule modes](#schedule-modes)):

**Keep state** (the default) makes the end boundary behave exactly like the start. The only thing it preserves is a countdown or warning already running, which keeps its original duration and deadline; if the old settings held the light but the new settings do not, a fresh timeout starts.

**Switch state** differs from **Keep state** in that one respect only: on the `on → off` boundary it replaces an already-on light's running state and deadline using the outside profile. An outgoing effect/warning presentation is cancelled first and its pre-warning appearance restored. Bright outside illuminance in `control` mode forces the light off; active occupancy, maintain occupancy, or an open-and-close door adopts it as `OCCUPIED`. Otherwise the remaining timeout is calculated from the outside occupancy/maintain history. With no configured presence sensor, or with no usable history, the outside profile receives a fresh full timeout. If the resulting deadline is already due, the outside profile's effect/warning/automatic-off settings apply immediately. While Auto-off is held, the state is still recalculated but its timer or off is suppressed; releasing the hold follows the normal hold-release rules, including a fresh full timeout when no other current rule keeps or turns the light off. An end boundary missed during a restart is caught up exactly once at startup, with the same recalculation.

**Turn off** uses the inside profile's automatic-off fade as the outside profile takes over. This automatic off respects Auto-off and the newly active outside profile's keep-on entities; a held boundary is applied when the hold releases (unless the light is turned off first, which discards it), and a boundary missed during a restart is caught up exactly once. A light that is already off at the boundary simply takes the outside profile's sensor check like the other actions, while a light the boundary turned off stays off until the outside profile's next sensor edge. Manual and automatic operation under the outside profile remain allowed.

The `active_settings` attribute reports `outside_schedule` or `inside_schedule`; `schedule_end_off_pending` reports whether an end-boundary off is waiting for an Auto-off/keep-on hold to release (changing **At schedule end** away from *Turn off* drops a waiting boundary; changing the schedule does not, because the off was already observed and still applies when the hold releases); `active_settings_schedule` records the schedule those came from, so a missed end boundary is only caught up after a restart when the same schedule is still selected. On restart, a valid schedule state wins. On a first start with no usable state, outside-schedule settings are used. While that same schedule is unavailable or unknown, its last restored choice is kept; after changing to an unavailable schedule, outside-schedule settings are used until it reports a valid state. If the schedule entry is deleted, the reference is removed, outside-schedule settings are used, and the light remains manually usable until a new schedule is chosen in **Configure**.

This settings selector does not include a separate follow mode. Create it through the normal **Add entry** flow or promote an existing gated light through **Convert virtual lights**. Bulk discovery and bulk sensor assignment do not directly create or modify Virtual Scheduled Lights.

#### Standby

The inside-schedule settings have one extra, collapsed section: **Standby**. Setting a **standby brightness** (and optionally a standby colour temperature or colour) makes the light rest at that level instead of turning off while the inside settings are active. Take a porch with a dusk-to-dawn schedule, standby at 1%, auto-on at 100%, a 30 s timeout, and **At schedule end** set to *Turn off*: it glows at 1% all night, comes up to 100% when someone walks up, drops back to 1% 30 s after they leave, and turns off at dawn. To turn standby off again, clear the standby brightness and set the standby colour mode to **None**: the form refuses a standby colour without a brightness.

- **Coming on:** an off light comes on at standby when the inside settings take over (the schedule starting, or startup inside it), with the auto-on fade and the turn-on selection. A light that is already on keeps its level, and its running timer ends at standby.
- **Rising and falling:** occupancy and the door raise the light to the auto-on brightness and colour like any automatic turn-on (the maintain sensor only holds a raised light). Set the auto-on brightness above the standby level: with it left blank, occupancy and the door hold the light but send no brighter level, so it stays where standby put it, and a blank auto-on colour likewise leaves the standby colour. When the timer runs out, the effect and warn stages run as usual, and their last step drops to standby with the auto-off fade instead of turning off. A standby with no colour of its own puts back the colour the light had before a coloured effect or warn stage. A false detection drops back to standby after the short false-detection delay.
- **Manual control:** a manual off turns standby off until the next schedule boundary, so occupancy then ends its visits in off. A window that starts as the last one ends is a boundary too, as is midnight for an all-day (00:00 → 00:00) schedule, though the schedule stays `on`; a Virtual Combined Schedule joins such windows into one, so its join is not. Turning the light back on, by hand or at the wall, rejoins standby, also while someone's presence has it raised, as does dimming or recolouring the raised light at the wall, and a restart keeps that. Dimming or recolouring a light at standby runs the normal timeout back to standby; the configured standby level is the only level the light rests at.
- **Illuminance:** in `gate` mode, brightness only blocks the rise above standby. In `control` mode, it also keeps standby off: bright turns the light off, and going dark inside the window brings standby back (or the auto-on level, if someone is present). A raised on-period that brightness cut short resumes at the auto-on level for the time it had left, as it does without standby, and a maintain sensor that is still on holds it there.
- **Holding auto-off:** a hold keeps the light at its current level. Releasing it starts a fresh timeout, which ends at standby; a light already resting at standby simply stays there.
- **The end boundary:** standby has no timeout of its own, so **At schedule end** decides. *Turn off* turns it off. *Keep state* starts the outside settings' turn-off timeout from the boundary: with a 5-minute outside timeout and a window ending at 07:30, a light at standby turns off at 07:35. Like any countdown, it resumes until that same 07:35 if brightness in `control` mode turns the light off and it gets dark again before then, whatever the outside occupancy history says and also after a restart, and a false detection does not cut it short. *Switch state* recalculates from the outside occupancy history, so if the last motion was at 03:00 the light turns off at 07:30. A light that someone's presence is holding at the auto-on level is handed over like any other on light: *Turn off* turns it off (unless Auto-off or an outside keep-on entity holds it), while *Keep state* and *Switch state* keep it on until they leave only if the outside settings watch a sensor that still sees them; otherwise the outside timeout runs.
- **Restarts and reboots:** a light resting at standby before a restart goes back to standby (an edited standby level applies then), a manual off inside the same window is respected (a restart in a later window brings standby back), and a member that comes back from `unavailable` is sent the standby settings again. The manual off also stands against someone still there: occupancy still on at the restart raises the light only when its sensor dates the visit after the off, as a Virtual Occupancy Sensor does with `last_on_time`. With another kind of sensor, or a visit whose start the sensor did not see, the light stays off until the next visit.

Converting a Virtual Scheduled Light to a gated Virtual Light drops its standby settings, since a Virtual Light has no standby.

### Virtual Remote

Drives lights from the buttons of a remote control: a Lutron Pico, an IKEA Bilresa, or any remote whose buttons Home Assistant exposes as `event` entities. One entry replaces the pile of hand-written `automation:` blocks that dispatch on button events: pick the target lights, then bind each button's single and/or double click to an action.

> **Lutron Caséta Picos and keypads:** Home Assistant's `lutron_caseta` integration doesn't create `event` entities for its buttons, so out of the box Picos won't appear in the pickers. Install the companion [lutron-caseta-events](https://github.com/jharris4/lutron-caseta-events) integration. It exposes every Caséta button as an `event` entity on the remote's own device page, and they work here like any other button.

Presses execute through the light domain's public services, so a bound press on a MoLight virtual light gets full **manual-control semantics**: it is never gated by darkness or a schedule window, it cancels a running effect/warn off-warning (restoring the pre-warning brightness), and it restarts the turn-off timer. Targets are usually MoLight virtual lights, but any `light` entity works.

Each entry creates one diagnostic **`<name> Last Action` sensor**. Its state is the last action the remote executed: `turn_on`, `turn_off`, `toggle`, `brightness_up`, `brightness_down`, `preset_1` or `preset_2`. Its attributes are `button` (the event entity that fired), `click` (the resolved `single` or `double`), `event_type` (the raw event type) and `time` (when the action ran). It's the link between "a button fired" (visible on the source event entity) and "a light changed" (visible on the virtual light): watch it while setting up bindings to confirm they do what you meant, and check its logbook history to answer "why did that light turn on?". It deliberately starts `unknown` after a restart, since a pre-restart action shown as current would be misleading.

| Config | Description |
|---|---|
| **Lights to control** | The `light` entities every bound button drives (at least one is required) |
| **Brightness step (%)** | Percent added/removed per brightness up/down click (default 10, range 1 to 50). Stepping up from off turns the lights on dim; stepping below the minimum turns them off |
| **Turn on / Turn off / Toggle** | Per action: the buttons whose **single click** and/or **double click** fire it |
| **Brightness up / Brightness down** | Same single/double pickers; each click steps the brightness once |
| **Preset 1 / Preset 2** | Same pickers, plus the values the preset applies: a **brightness** (1 to 100 %), and a **color temperature** (2000 to 6500 K) *or* an **RGB color**; its **color mode** dropdown picks which one is used, and its **None** choice clears a previously set color. Left blank, the form rejects setting both. Think of the Pico's favorite button |

Each button may appear in several actions, as long as no *(button, click)* pair is bound twice, e.g. a Bilresa button whose single click toggles and whose double click turns on. The form requires at least one binding overall, and a preset with buttons but no values is rejected (it would just be a turn-on pretending to be a preset).

**How clicks are recognized.** Ecosystems spell "single click" differently, so the discriminating event is resolved per button from the event entity's advertised `event_types`:

- Buttons that announce `multi_press_1`/`multi_press_2` (Matter multi-press, e.g. the Bilresa): single = `multi_press_1`, double = `multi_press_2`. The constituent `initial_press`/`short_release` of the same physical click never fire a binding twice.
- Zigbee2MQTT-style buttons with literal `single`/`double` map directly.
- Lutron Caséta buttons (via [lutron-caseta-events](https://github.com/jharris4/lutron-caseta-events)) announce `press`/`multi_tap`: single = `press`, double = `multi_tap`. Note classic Caséta bridges may never report multi-taps; a double-click binding is accepted but only fires if the bridge does.
- Hue-style buttons (and Matter without multi-press): single = `short_release`, falling back to `initial_press` for buttons that announce nothing better (it is last in priority because it also precedes a long press).

Two things worth knowing:

- On a multi-press-capable button, the device only confirms a *single* click after its multi-press window (~half a second) closes, so single clicks on a Bilresa have inherent latency. That's a device property, not something software can fix; Pico presses are instant.
- A button's first sighting after startup, and its recovery from `unavailable`, both carry the last (stale) event and are never replayed as a fresh press. The guards you'd otherwise write as `trigger.from_state` template conditions are built in.

Deleting a virtual light strips it from every remote's target list, like any other reference. For worked examples (a 5-button Pico and a 2-button Bilresa), see [EXAMPLES.md](EXAMPLES.md).

## Troubleshooting

MoLight writes no debug logs. It logs a warning only when it cannot do what it was asked: a turn-on selection it could not apply, a Virtual Scheduled Light with no schedule, or a combined schedule that includes itself, and an error when discovery fails to create an entry. To see why a light did what it did, read these attributes in **Developer tools → States**:

- `molight_state` on the virtual light: its [state-machine](#state-machine) state.
- `last_on_physical`, `last_on_virtual`, `last_on_occupancy`, `last_on_illuminance` and `last_on_door` on the virtual light: when each source last turned it on.
- `auto_off_held` on the virtual light: whether the Auto-off switch or a keep-on entity is holding automatic turn-offs.
- `schedule_window_start`, `bright_forced_off` with `bright_resume_until` and, on a Virtual Scheduled Light, `active_settings`: the follow-mode window marker, whether brightness forced the light off and until when going dark resumes it, and which settings profile is live.
- `latest_occupied_time`, `last_clear_false_detection` and `last_clear_unavailable` on an occupancy sensor: when the person was last seen, and whether the last clear was a false detection or an unavailable source.
- `resolved_schedules` on a Virtual Combined Schedule Sensor: the plain schedules it was built from.
- A Virtual Remote's **Last Action** sensor: the last binding it ran, and the button and click that fired it. Its logbook history answers "why did that light turn on?".

## Development

Local development uses **two independent containers**, each with a distinct job. They are unrelated (no shared network or startup dependency), but both bind port `8123`, so only one can run at a time.

| Container | Image | What it's for |
| --- | --- | --- |
| **Dev container** (`dev:*`) | generic Debian + Python 3.14 | Your toolchain: editing, `pytest`, `ruff`. VS Code attaches here. HA is pip-installed into a venv (`/opt/molight-venv`) as a library. |
| **HA runtime** (`hass:*`) | official `home-assistant:stable` | The real Home Assistant app, for manual/UI testing. Your integration is mounted read-only. |

The npm scripts prefixed `dev:` and `hass:` act on these two containers. `test`, `lint` and `format` run inside the dev container, while `release`, `lint:sh` and the `test:e2e` scripts run on the host, the last two in throwaway containers of their own.

### Running the tests

```bash
npm install         # install tooling
npm run hass:down   # free port 8123 (devcontainer and HA runtime can't coexist)
npm run dev:up      # start the devcontainer (fast after first build)
npm test
```

`npm test` runs the suite on the Home Assistant version pinned in `requirements_test.txt`. CI also fails when coverage of lines and branches together drops below 98 %; `npm test -- --cov` checks that locally. Besides the pinned version, CI runs the suite on the minimum Home Assistant version in `hacs.json` (with Python 3.13), so a test that passes locally can still fail there, and, as an advisory leg, on the newest version `pytest-homeassistant-custom-component` targets, which is often a beta.

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
writes nothing on its own; it lists the changes a release would make (stamping
the changelog, bumping the manifest version, updating the compare links) and
prints the command that applies them. Applying then prints the git commands to
commit, tag and push. Before either previewing or applying, the script also
checks that the worktree is clean, `main` contains `develop`, both branches
match their live `origin` refs, the version moves forward, and the tag does not
already exist locally or on GitHub.

```bash
git commit -am 'release 1.x.0'
git push
git tag v1.x.0
git push origin v1.x.0
```

Pushing the tag starts the **Release** GitHub Actions workflow. The workflow
validates that the tag is on `main` and that the tag, manifest and changelog
versions agree, runs the lint, unit test and validation jobs and every live
E2E suite, and once all of them pass puts the release notes extracted from
the changelog in the summary of its **Release notes** job. It does **not**
create the GitHub Release. After the workflow succeeds:

1. Open the **Release notes** job summary and copy the generated notes.
2. In the repository's **Releases** page, choose **Draft a new release** and
   select the tag you just pushed.
3. Give the release a descriptive title, paste the generated notes into its
   body, and publish it as a normal release (not a draft or prerelease). The
   published body is what HACS shows users in its update dialog.

Creating only the tag is not a complete release: the corresponding published
GitHub Release is required for users and HACS to see the version and its notes.

Finally, fast-forward `develop` to include the release commit too, so
development resumes with the stamped changelog and new manifest version:

```bash
git switch develop
git merge --ff-only main
git push
```

Do not start new work on `develop` between the first merge and this final sync;
keeping the release window short ensures both updates remain fast-forwards.

### Working in the dev container

`dev:up`, `dev:stop`, `dev:down` and `dev:rebuild` manage the dev container from the host, and `lint:sh` runs on the host too. The rest run *inside* the dev container via `devcontainer exec`:

```bash
npm run dev:up        # create/start the dev container (fast after first build)
npm test              # pytest
npm run lint          # ruff check
npm run lint:fix      # ruff check --fix
npm run format        # ruff format
npm run format:check  # ruff format --check (what CI runs)
npm run lint:sh       # shellcheck at the version CI pins (runs on the host, in Docker)
npm run dev:shell     # open a bash shell inside the container
npm run dev:stop      # stop the container, keeping it for a fast dev:up next time
npm run dev:down      # stop and remove the container (dev:up recreates it)
npm run dev:rebuild   # tear down and rebuild from scratch (e.g. after changing devcontainer.json)
```

The container's virtual environment is rebuilt when `requirements_test.txt` or the setup scripts change: checked at every container start and before `npm test`, `lint` and `format`, so a dependency bump needs no `dev:rebuild`.

If you open the repository locally rather than in the container, Pylance can't see the container's Python environment and flags every Home Assistant import as unresolved; see [LOCAL_DEV_NOTES.md](LOCAL_DEV_NOTES.md) for the editor-only fix.

### Running Home Assistant

There are two ways to get a live HA instance, depending on what you're testing:

```bash
npm run hass:up      # run stock HA (stable image) with the integration mounted read-only
npm run hass:down    # stop it and free port 8123
npm run hass:pull    # update the HA stable image

npm run dev:hass     # run the pip-installed HA inside the dev container against your working tree
```

Use `hass:up` to confirm behavior against a real, released HA build; use `dev:hass` for active development, where HA loads the integration from your working tree and can be restarted in place. Remember to `hass:down` before starting the dev container (or vice versa) so port 8123 is free.

### Running the live acceptance tests

The end-to-end suite starts an isolated official Home Assistant container and
a short-lived API runner on a private Docker network. It does not use the
stateful manual-development `config/` directory or publish port 8123, so it can
run alongside the dev container. A test-only `molight_testbed` integration
provides persistent simulated lights, sensors, a door, a schedule and selects
(lights can be switched into slow, piecewise, stepwise, quantised or XY-reporting
behaviour to emulate real bulbs);
it is mounted only into the disposable acceptance environment and is never
part of a MoLight release.

```bash
npm run test:e2e              # core, restarts, and scenario shards a/b/c, concurrently
npm run test:e2e:core         # the sequential restart chain
npm run test:e2e:restarts     # independent restart scenarios on their own fixtures
npm run test:e2e:scenarios-a  # behaviour scenario shard a (b and c likewise; one fresh HA each)
npm run test:e2e:browser      # browser smoke tests (not part of test:e2e)
```

The suite performs automated onboarding, creates and edits MoLight entries
through Home Assistant's backend config-flow API, switches both scheduled
profiles, exercises selection source and fallback behavior, converts a light
in both directions without changing its entity ID, restarts Home Assistant
core, restarts the full container with the same temporary `/config`, and
checks the resulting logs. It also checks bright suppression, dark-arrival
activation, and the distinct gate/control illuminance and open/open-close door
behavior configured on the two scheduled profiles. A dedicated short-lived
light runs a real countdown through effect and warning stages to final off,
then proves an occupancy retrigger cancels the sequence. Additional cold-start
phases hold the physical light, occupancy source, and schedule source
unavailable across restarts, recover the sensors in both orders without false
transitions, and verify a late-reporting light updates the virtual light's
capabilities without turning it on. A mixed on/off, dimmable, and RGB group
also verifies that brightness and color commands reach only capable members,
and that its advertised capabilities remain accurate when the RGB member is
unavailable across a restart and later recovers. A removal scenario deletes a
referenced virtual sensor through Home Assistant's config-entry API, confirms
the surviving light drops that reference, and verifies neither the sensor nor
its stored reference returns after core and container restarts. A current-flow
Virtual Remote scenario exercises single/double event bindings, edits them to
brightness/toggle actions, checks Last Action diagnostics across restarts, and
confirms removing its target leaves the surviving remote inert. Virtual
Combined Schedule scenarios check any/all/inverted/nested combinations of
mirrored schedules, a gate-mode light using one, unavailable inputs,
rebuilding after a time-window input is edited, and input deletion, then
restart with a follow light manually off mid-window while an input and the
member light load late, verifying the window is kept, the late member's
first state doesn't re-light it, and a new window does.

The default image is pinned, in `tests/e2e/env.sh`, to the Home Assistant
release used by the current test dependencies; CI fails if the two drift
apart. Override it to exercise another release:

```bash
MOLIGHT_E2E_HA_IMAGE=ghcr.io/home-assistant/home-assistant:stable npm run test:e2e
MOLIGHT_E2E_HA_IMAGE=floor npm run test:e2e   # the minimum release hacs.json declares
```

Some races only show on slow hardware: a busy CI runner caught two that a fast
laptop always won. `MOLIGHT_E2E_HA_CPUS=0.5` caps the Home Assistant
container's CPU to approximate that locally (the suites take about twice as
long); `npm run test:e2e` also loads the machine by running all lanes at once.

A successful run removes its temporary configuration. On failure the runner
prints a retained run directory under the system temporary directory
containing the isolated HA configuration and Compose logs. That directory
contains the disposable test account, so remove it after debugging. The
browser lane also keeps its failure artifacts (Playwright output, a copy of
the HA configuration, and Compose logs) in `tests/e2e/artifacts/browser` in
the repository, and clears that directory at the start of each run.

CI runs the live suite against the pinned current Home Assistant image on
pushes and pull requests. Nightly and manually dispatched E2E workflows run
every suite (API lanes and the browser smoke tests) on a
minimum-supported/current matrix plus an advisory floating `stable` canary;
the nightly run covers both `main` and `develop`. Release tags run every
suite on the current and minimum-supported images, and write their release
notes only after all of them pass. Every CI job uploads what its lane
retained (the run directory with the HA config and Compose logs, plus
`tests/e2e/artifacts/browser` for the browser suite) as a workflow artifact
when it fails or is cancelled; locally, `MOLIGHT_E2E_RUN_ROOT` sets the
directory that each lane run creates its own run directory in.

## Design notes

- MoLight tells its own commands' echoes from physical changes by comparing each real light's report with what it asked for, not by Home Assistant's context alone. HA reuses a command's context on the target light for five seconds, so a wall switch or dimmer used in that window would otherwise look like MoLight's own echo; a report that contradicts the command is treated as human activity, while two-part, stepwise, quantised, or colour-converted replies are still recognised as echoes while the command settles, and a slow bulb's full reply is recognised up to 30 s later under any context, also when MoLight has sent a newer command in the meantime, unless that command switched the light the other way: then only a report within a few seconds of the earlier command still counts. A colour reported partway through a fade is a step of that fade when it lies on the way from the previous colour to the requested one, whether the light blends round the hue wheel or straight through RGB or CIE xy, which passes through paler colours between far-apart hues; a colour that backs away from the request, or lies beside the way there, is a recolour.
- Each virtual entity is its own config entry, so they can be created, edited, and removed independently. A Virtual Remote is mostly wiring between button event entities and target lights; its only entity is the diagnostic Last Action sensor.
- A virtual entity must exist before another can reference it (sensors before the lights that use them).
- Removing an entry strips references to its entities from the entries that survive it, so a light whose schedule sensor is deleted loses the reference instead of keeping a gate that can never open. References are stored as entity IDs, so an entity ID changed in Home Assistant is rewritten in every entry that uses it, for the same reason.
- The `light_timeout >= occupancy_timeout` constraint is validated in both directions: creating/editing a light checks its referenced occupancy and maintain occupancy entities (including through a combined sensor), and raising an occupancy sensor's timeout checks every light that depends on it.
- Settings no chosen light could apply are rejected at the form rather than stored and silently ignored: a fade when none of the lights support transitions, a color temperature or a color when none can show one, a brightness when none of them dim (an effect brightness of `0` is exempt, since a blink fully off is sent as a plain turn-off, which every light can do). One capable light is enough (mixed groups are the point), and a light that hasn't reported its capabilities yet is never taken as proof, so the check can't block on incomplete information.
- Reference graphs stay sane by construction: an occupancy sensor can't wrap another MoLight occupancy entity, a combined sensor can't reference itself or form a cycle through other combined sensors, a constituent can't be both a trigger and a maintain sensor, a Virtual Light can't include itself or a Virtual Light that includes it, and its keep-on entities can't be its own light or Auto-off switch.
- Once an entry's options have been edited, the options fully replace the original data (so cleared optional fields stay cleared).
