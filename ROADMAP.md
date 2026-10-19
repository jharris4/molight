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
