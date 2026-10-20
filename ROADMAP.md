# MoLight roadmap

Feature ideas and how they score. Nothing here is a promise: a row is an idea until its status says
otherwise, and the scores are the maintainer's estimates until the evidence column links to an issue
or discussion that backs them. Scores are revisited once per release, when the next batch is chosen.
The [CHANGELOG](CHANGELOG.md) records what shipped; this file records what might.

## How rows are scored

| Column | Values | Meaning |
|---|---|---|
| Demand | Low / Med / High | How many users are likely to want it. Estimated from the wider Home Assistant motion-lighting ecosystem until MoLight's own users say so |
| Build | Low / Med / High | Code effort |
| Verify | Low / Med / High | Test and documentation effort. Anything that changes the Virtual Light state machine is High: it needs matrix tests, an e2e run and a REFERENCE.md section |
| Risk | Low / Med / High | Chance of changing how existing entries behave, or of breaking the echo detection that tells MoLight's own commands from a change at the wall |
| Workaround | Yes / Partial / No | Whether existing MoLight blocks plus a Home Assistant helper already do it |
| Fit | Good / Fair / Poor | Whether it keeps MoLight a set of composable blocks, rather than pulling it toward a whole-house automation framework |
| Migration | None / Options / Entities | Whether stored options must be rewritten, or the entity set per entry changes. A new optional field that defaults to the old behavior is None |
| Status | Idea / Scheduled / Shipped x.y.z / Not planned | |

Every new setting starts blank or off, so an existing entry behaves exactly as before the feature.

## Next batch

Chosen 2026-10-19. Order within the batch is the order to build.

1. Row 8, diagnostics and Repairs: every bug report from then on starts with a diagnostics file, and the Repairs half converts warnings that already exist.
2. Row 7, events: a public contract once automations listen for them, so the event names and payload keys are settled before later features need to emit them.
3. Row 1, illuminance from sun elevation: the highest demand at the lowest risk, a new source kind that the lights never see.
4. Row 2, keep-on and maintain from more entity kinds: the picker rules change, the hold logic does not, and a timer helper as a keep-on entity gives "hold for two hours" before row 6 exists.

After that, in order:

1. Rows 6, 11 and 4, with row 12 if there is room. A "turns off at" sensor (row 11) earns its place once a service can extend that countdown (row 6). None touches the precedence rules.
2. Row 3 on its own, with its own matrix tests and e2e run, because it is the one high-demand item that reaches into echo detection.
3. Rows 9, 14, 15, 19 and 20 wait for user evidence: each has a workaround or a high verification cost.

## Features

| # | Feature | Demand | Build | Verify | Risk | Workaround | Fit | Migration | Status |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Virtual Illuminance Sensor from sun elevation, or from any binary sensor | High | Low | Med | Low | Partial | Good | None | Scheduled |
| 2 | Keep-on and maintain from media players, timers and numeric thresholds | High | Med | Med | Med | Yes | Good | None | Scheduled |
| 3 | A scene or script as the turn-on action | High | Med | High | High | Partial | Fair | None | Idea |
| 4 | Weekday selection on time-window schedules | Med | Low | Med | Med | Yes | Good | None | Idea |
| 5 | Virtual Remote: hold to dim | Med | Med | Low | Low | No | Good | None | Idea |
| 6 | Services: extend the timer, hold auto-off for a duration, restart the countdown | High | Med | Med | Med | Partial | Good | None | Idea |
| 7 | Events fired at state transitions | Med | Low | Low | Low | Partial | Good | None | Scheduled |
| 8 | Diagnostics download and Repairs issues | Low | Low | Low | Low | Yes | Good | None | Scheduled |
| 9 | Standby on a plain Virtual Light | Med | Med | High | Med | Yes | Good | None | Idea |
| 10 | Auto-on brightness and color read from entities at each turn-on | Med | Med | Med | Low | No | Good | None | Idea |
| 11 | Countdown and state as entities, disabled by default | Med | Med | Low | Low | Partial | Good | Entities | Idea |
| 12 | Virtual Remote: more actions (toggle auto-off hold, all off, color temperature step, run script) | Med | Low | Low | Low | Partial | Good | None | Idea |
| 13 | Virtual Remote: targets per binding | Med | Med | Med | Med | Yes | Good | Options | Idea |
| 14 | Block entities: while any is on, no automatic turn-ons | Med | Low | High | Med | Yes | Good | None | Idea |
| 15 | Re-trigger cooldown after an automatic off | Med | Med | High | Med | No | Fair | None | Idea |
| 16 | Area wizard: wire an area's sensors and lights in one pass | Med | Med | Med | Low | Partial | Good | None | Idea |
| 17 | Setup presets that prefill the light form (hallway, bathroom, porch, closet) | Med | Low | Med | Low | Yes | Good | None | Idea |
| 18 | Translations beyond English | Med | High | Low | Low | No | Good | None | Idea |
| 19 | More than two settings sets on a Virtual Scheduled Light | Low | High | High | High | Partial | Fair | Options | Idea |
| 20 | Auto-on values per member light | Med | High | High | Med | Yes | Fair | None | Idea |
| 21 | One Home Assistant device per entry, grouping its entities | Low | Med | Low | Low | No | Good | None | Idea |

## Notes and evidence

1. Most homes have no lux sensor, so "only when dark" today spends the light's single schedule slot on a sunset to sunrise window. A sun-elevation source is a new definition on the existing sensor, like the schedule sensor's two definitions, and the lights do not change. Polar days need the same care as the schedule sensor gives them.
2. "Don't turn the lights off while the TV plays" and "hold the bathroom light while the humidity is high". A template binary sensor does it today. The picker rules tightened in 1.9.0 (an entity must read `on`) are what change; a timer helper reading `active` as a hold covers most of row 6.
3. Scenes are the standard way to set a multi-light look. A scene lights the bulbs itself, so echo detection must not read that as a change at the wall. The same-device rule for a WLED preset select is the precedent, but a scene can touch any light.
4. "Weekday mornings only" takes a workday sensor plus a combined All schedule today. An overnight window must decide which day it belongs to, and `next_transition` must skip days.
5. Pico raise/lower and Hue dimmer hold. Release events differ per ecosystem, so this doubles the vocabulary re-checked on every Home Assistant bump.
6. "Keep the lights on for two hours" by voice. A timed hold must survive a restart.
7. Lets users build notifications or a text-to-speech warning with their own automations, so MoLight never has to own those.
8. User demand is low; the value is in triaging issues. The load-time warnings listed under Troubleshooting in the README would become Repairs issues.
9. A night light at 5% at all times. Issue #1 asked for standby on a Virtual Scheduled Light, shipped in 1.9.0. A plain Virtual Light has no schedule boundary to release a manual off, so it needs its own release rule. A Virtual Scheduled Light on an all-day schedule does it today.
10. The option-source pattern of the turn-on selection, applied to brightness and color. The cheap route to time-of-day lighting: Home Assistant computes the value, MoLight reads it at each automatic turn-on.
11. A "turns off at" timestamp sensor for dashboards. The attributes carry it today.
12. A Pico favorite button as a guest-mode toggle.
13. A five-button Pico driving two rooms. Two remote entries on different buttons of the same remote do it today.
14. Sleep mode. An inverted source-backed schedule in a gate mode does it today, but it takes the schedule slot. A new gate sits in the precedence rules, hence the verification cost.
15. Stops the person walking out from re-lighting the room they just left. Interacts with going dark resuming an on-period, adoption at startup and false detections.
16. Discovery already filters by area; this would create the occupancy sensors, the combined sensor and the light together.
17. EXAMPLES.md covers the same ground in prose.
18. strings.json is large. Expected to be community work.
19. Morning, day, evening and night profiles. Two sets already cover most asks when combined with combined schedules.
20. A lamp at 30% and the ceiling at 100% from one trigger. Two Virtual Lights sharing the sensors do it today.
21. A light's companion entities ("<name> Auto-off", "<name> Events") are tied to it by name alone. A device per entry groups them on one page, as Home Assistant does for a bulb and its sensors; entity IDs and friendly names stay as they are.

## Already covered

Not features, but asks that existing blocks answer:

- Vacancy-only control (turn off after people leave, never turn on): a maintain occupancy sensor alone.
- Away or sleep gating: a source-backed Virtual Schedule Sensor, inverted, in a gate mode (EXAMPLES.md, example 5).
- Dim before turning off: the effect and warn stages.
- An always-on night light that brightens on motion: a Virtual Scheduled Light on an all-day schedule with standby.

## Not planned

- Vacation presence simulation.
- YAML configuration.
- Adaptive or circadian lighting computed by MoLight itself (row 10 reads such values from the integrations that compute them).
- Controlling `switch` entities directly: Home Assistant's Switch as X helper wraps one as a light.

## Discussion notes

What was said when the rows were scored and the first batch chosen, on 2026-10-19, kept here so the
intent behind each row survives. The table and the Next batch section summarize this; when they
disagree, this section is the intent.

### Where the scores come from

- Demand is an estimate from what the wider Home Assistant motion-lighting ecosystem asks for
  (the Sensor Light blueprint, Adaptive Lighting, Magic Areas, Entity Controller), not from
  MoLight's own users. With one issue filed so far, that is the only signal available, and it is
  the weakest column in the table.
- Risk follows one rule: the state machine in light.py is where every breakage risk in this codebase
  lives. A feature that only adds a source kind behind an existing sensor, a new platform, a service
  or a remote action does not touch it. A feature that adds an input to the light's precedence rules
  (manual, then holds, then a follow window, then forced offs, then occupancy and the door) or
  changes what counts as a change at the wall does.
- Build and verify are separate columns because the test suite and REFERENCE.md are the expensive
  part. A feature that touches the state machine costs a matrix test pass, an e2e run and a
  reference section even when the code change is small.
- Workaround deflates demand honestly: rows 4, 9 and 14 have one, rows 1 and 3 do not.
- Fit asks whether the feature keeps the "wrap, wire, no automations" promise or pulls MoLight toward
  being Magic Areas or Adaptive Lighting. Row 19 and the non-goals fail it; row 10 passes by
  delegating the clever part to Home Assistant.
- Migration matters because stored options and the entity set are what hurt existing installs if a
  feature is ever reversed.
- Ongoing maintenance is not a column but was weighed: rows 5 and 18 are cheap to build and never
  finished, since the click vocabulary is already re-checked on every Home Assistant bump and
  hold-to-dim doubles that surface.

### Row by row

1. **Illuminance from sun elevation.** Most homes have no lux sensor, so "only when dark" today
   spends the light's single schedule slot on a sunset to sunrise window, which conflates schedule
   and darkness. The feature is a new definition on the existing illuminance sensor, mirroring the
   schedule sensor's two definitions (time window or binary sensor), so a source-backed definition
   comes with it. The lights never see the difference. Highest demand at the lowest risk in the
   table, and no workaround that keeps the schedule slot free.
2. **Keep-on and maintain from more entity kinds.** The asks are "don't turn the lights off while
   the TV plays" and "hold the bathroom light while the humidity is high": a media player playing,
   a timer active, a numeric sensor over a threshold. A template binary sensor does it today. What
   changes is the picker rules tightened in 1.9.0 (an entity must read `on`) and the "reads on"
   mapping per domain; the hold logic itself does not change, which bounds the blast radius. A timer
   helper reading `active` as a hold also delivers "keep the lights on for two hours" before row 6
   exists.
3. **Scene or script as the turn-on action.** Home Assistant scenes are the standard way to set a
   multi-light look; the turn-on selection today covers a `select` entity only, so WLED presets
   work and scenes do not. A scene lights the bulbs itself, so echo detection must not read that as
   a change at the wall. The same-device rule for a WLED preset select is the precedent, but a scene
   can touch any light, and that machinery is the most delicate code here. Highest demand of the
   rows left after the first batch, and the only top-tier row that reaches into echo detection, so
   it ships alone with its own matrix tests and e2e run.
4. **Weekday selection on time-window schedules.** "Weekday mornings only" takes a workday sensor
   plus a combined All schedule today. An overnight window must decide which day it belongs to, and
   `next_transition` must skip days; the overnight and clock-change logic is already intricate,
   hence the medium risk for a small change.
5. **Remote hold to dim.** Continuous dimming while a button is held, stopping on release: Pico
   raise and lower, Hue dimmer hold. Isolated in remote.py, but release events differ per ecosystem,
   so it doubles the vocabulary re-checked on every Home Assistant bump. It also interacts with the
   rule that brightness steps during a warning start from the pre-warning brightness, and with echo
   detection under rapid commands.
6. **Services.** Extend the timer, hold auto-off for a duration, restart the countdown: "keep the
   lights on for two hours" by voice. A timed hold must survive a restart. Pairs with row 11, since a
   countdown is worth showing once something can extend it.
7. **Events at state transitions.** Fire an event when an automatic off is coming (the effect and
   warn stages), when the timer turns a light off, when occupancy cleared as a false detection, and
   at the other transitions. Users then build notifications, a text-to-speech warning or anything
   else with their own automations, so MoLight never has to own those. Cheap and additive, since
   nothing in MoLight reads the events. The one design constraint: it is a public contract. Once an
   automation listens for an event name, renaming it breaks them, so the real work is choosing a
   small, stable set of event types and payload keys, settled before later features need to emit
   them. Scheduled second so every feature after it emits events from the start. The docs must say
   the warning event can drive a notification on its own: for no cue on the light, set the effect
   duration to 0 and leave the warn brightness and color blank. Decided 2026-10-19: delivered as
   event entities, one "<name> Events" per light and per occupancy sensor, rather than bus events.
   The developer docs allow integrations one bus type, `<domain>_event`, and steer new work to
   event entities, whose declared type list enforces the closed contract and whose last event
   restores across restarts. Each event carries the owning entity's ID, reason and the look set.
8. **Diagnostics and Repairs.** Scheduled first because every bug report from then on starts with a
   diagnostics file, saving a round trip per issue, and the Repairs half converts warnings that
   already existed, so there was no design work, only wiring. Landed 2026-10-19 as commit 3e652cd:
   every load-time warning in the README's Troubleshooting section also raises a warning Repairs
   issue naming the entities; issues clear on unload so an edit that fixes the problem leaves none;
   removing an entry clears issues on other entries that name its entities; a source-group cycle
   clears when the group is corrected; and each entry offers a diagnostics download holding its
   settings, its own entities' states, every referenced entity's state with the owning MoLight entry
   named, and the other entries that reference it.
9. **Standby on a plain Virtual Light.** A night light at 5% at all times. Issue #1 asked for
   standby on a Virtual Scheduled Light and got it in 1.9.0; a Virtual Scheduled Light on an all-day
   schedule does the always-on case today. Standby's manual-off release is keyed to schedule
   boundaries, so a plain light needs its own release rule, which is where the risk is.
10. **Auto-on values read from entities.** The option-source pattern of the turn-on selection,
    applied to brightness and color: an input_number or a sensor read at each automatic turn-on.
    The cheap route to time-of-day lighting without MoLight doing adaptive lighting itself: Home
    Assistant computes the value, MoLight reads it. Also the answer to lux-aware brightness.
11. **Countdown and state as entities.** A "turns off at" timestamp sensor for dashboards, disabled
    by default. Additive platform, but more entities per light. On its own it is dashboard polish
    with registry churn, so it did not earn a slot in the first batch; it goes with row 6.
12. **More remote actions.** Toggle the auto-off hold, all off, color temperature step, run a
    script. A Pico favorite button as a guest-mode toggle is the natural fit.
13. **Remote targets per binding.** A five-button Pico driving two rooms. An options schema change
    with a migration. Two remote entries on different buttons of the same remote do it today.
14. **Block entities.** The mirror of keep-on: while any is on, no automatic turn-ons. Sleep mode.
    An inverted source-backed schedule in a gate mode does it today, but it takes the one schedule
    slot. A new gate sits in the precedence rules, hence the verification cost.
15. **Re-trigger cooldown.** Stops the person walking out from re-lighting the room they just left.
    Interacts with going dark resuming an on-period, adoption at startup and false detections.
16. **Area wizard.** Pick an area and get the occupancy sensors, the combined sensor and the light
    wired in one pass. Creation-time only; Discovery already filters by area, so most pieces exist.
17. **Setup presets in the light form.** Hallway, bathroom, porch, closet prefill the fields.
    Onboarding, not capability; EXAMPLES.md covers the same ground in prose.
18. **Translations.** strings.json is large, so this is community work once there is a community.
19. **More than two settings sets.** Morning, day, evening and night profiles. Extends the boundary
    logic that only just got its restart handling right; two sets with combined schedules cover most
    asks.
20. **Auto-on values per member light.** A lamp at 30% and the ceiling at 100% from one trigger.
    Two Virtual Lights sharing the sensors do it today.
21. **A device per entry.** Added 2026-10-19 while naming row 7's entity. MoLight registers no
    devices, so a companion entity carries its whole relationship in its name: "Hallway Auto-off",
    "Hallway Events". Home Assistant's own answer is the device: a bulb's light, power sensor and
    update entity sit under one device, each named by function, and the entity picker shows the
    device beside each. One device per entry would group the light, its switch and its events the
    same way. Registry IDs are kept and the composed names come out as they are today, so nothing
    breaks; the cost is a device page per entry in Settings, which may read as clutter for a
    single-entity sensor entry. Low demand on its own, and it waits on how row 7's entity lands.
    Decided 2026-10-20: the Events entity carries no attribute naming its light or sensor, so this
    device is what links the two.

### Sequencing

- Risk is the gate and cost is the batch: rows that only add a source kind, a platform, a service or
  a remote action can ship several per release; anything that adds an input to the precedence rules
  or changes what counts as a manual change ships alone, with matrix tests and an e2e run, as standby
  did.
- Every new setting starts blank or off, so no feature needs an upgrade note beyond "it exists".
- The first release is rows 8, 7, 1 and 2 in that order: the observability half first because it
  makes every later feature cheaper to support and generates the issue reports that say what to
  build next, then the two high-demand inputs with a bounded blast radius.
- Row 11 was pulled out of the observability trio (7, 8, 11) and paired with row 6 in the second
  release, with rows 4 and 12 if there is room. None of those touches the precedence rules.
- Row 3 is the third release, alone.
- Rows 9, 14, 15, 19 and 20 wait for user evidence: each has a workaround or a high verification
  cost, and a poll should decide them.

### How the plan is maintained

- This file is the single source of plan state. Status and the Next batch section change here;
  the CHANGELOG records what shipped and nothing else from the plan.
- An issue is opened for a feature only when it enters a batch, labeled `enhancement`, linked from
  the row, and closed with the release. A user-filed request gets a row the day it arrives, even at
  "Not planned", so the reply can link here.
- Demand evidence comes from a pinned GitHub Discussion poll on the shortlist and from the
  custom-integration install count on analytics.home-assistant.io, and goes in the row's note.
- Scores are walked once per release, before the next batch is chosen, with a dated note in the
  row when one moves.
