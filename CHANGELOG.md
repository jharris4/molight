# Changelog

All notable changes to MoLight are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Entries for 1.5.0 and earlier were reconstructed from the commit history after
> the fact and are deliberately coarse — they summarise each release rather than
> enumerate it. Later entries are written as the work lands.

## [Unreleased]

A release focused on scheduling. The new **Virtual Scheduled Light** holds two
complete settings profiles and switches between them as a schedule opens and
closes, and ordinary Virtual Lights gain two more ways to treat the end of a
schedule window. Plus a round of restart, occupancy and sensor edge-case fixes.

MoLight is also now in the HACS default repository list, so installing it no
longer needs a custom repository.

### Upgrade notes

- **Lights that already gate on a schedule are unchanged** — only the label
  moved, to *Gate and turn off*, now that two more gate modes sit beside it.
- **The new form validation never stops an existing entry from loading.** The
  capability and hysteresis checks under *Changed* run in the forms, not at
  startup, so an entry configured under an earlier version keeps running as it
  is — but its next edit won't save until the rejected setting is corrected.

### Added

- **Virtual Scheduled Light** — a new entity type that holds two complete Virtual
  Light settings sets and switches between them as a Virtual Schedule Sensor
  turns on and off. Everything a Virtual Light configures can differ inside and
  outside the schedule window: timeout, occupancy/maintain/illuminance/door/keep-on
  entities, automatic brightness and color, fades, warnings, and the turn-on
  selection. A required **At schedule end** setting decides what the end of the
  window does to a light that is still on — *Keep state* (the default) preserves
  a running countdown or warning (a light held on by inputs the outside profile
  lacks starts the outside timeout instead), *Switch state* recalculates the
  light against the outside profile, *Turn off* fades it off. New `active_settings`,
  `active_settings_schedule` and `schedule_end_off_pending` attributes report
  which profile is live and whether a boundary off is waiting on a hold.
- **Convert virtual lights** — an entry flow that promotes an existing gated
  Virtual Light to a Virtual Scheduled Light, or demotes one back, in place, so
  entity IDs and history survive. Settings carry over as a starting point rather
  than identically, and demoting permanently discards the outside-schedule
  profile after a confirmation warning.
- **Turn-on selection** — a Virtual Light can drive a `select` entity whenever it
  turns on (a scene, a mode, a media preset). The option is picked from a list on
  a second form page, with an optional separate entity to source the option list
  from. The applied value and its origin are exposed as
  `last_turn_on_selection_option` and `last_turn_on_selection_source`.
- **Source-backed and inverted Virtual Schedule Sensors** — a schedule can now
  mirror any existing `binary_sensor` instead of defining a time/sun window,
  which promotes a helper, template or integration sensor into the schedule
  picker. **Invert output** is available for both definitions.
- **Two new Schedule mode choices for Virtual Lights.** Gating previously meant
  one thing — restrict automatic turn-ons to the schedule window and turn off at
  its end, now labelled *Gate and turn off*. *Gate and switch state* instead
  recalculates an already-on light from current sensors when the window ends,
  and *Gate and keep state* leaves its countdown, warning and timer untouched.
  Both still block a fresh automatic turn-on once the light is off.
- **Transition pass-through** — a `transition` passed to a virtual light's
  `turn_on`/`turn_off` is now forwarded to the real lights, so service calls and
  scenes that carry a fade work against a virtual light as they would against
  the real ones.
- **`warning_active` attribute** — reports whether a light is part-way through
  its pre-off effect/warn sequence. The value now survives a restart, so MoLight
  can undo an interrupted warning and put the light back to its pre-warning
  brightness and color. It previously inferred this from the saved brightness
  and color, which are legitimately empty for lights that report neither.
- Discovery now reports how many entries it actually created.

### Changed

- Light schedule pickers are restricted to MoLight schedule sensors, rather than
  offering every binary sensor.
- The config and options flows now reject settings that none of the chosen
  lights could apply — colors, fades, brightness, and brightness or color set on
  a disabled effect stage — sharing one capability rule with the runtime.
  Discovery checks this per selected light rather than against the pooled
  selection.
- An illuminance hysteresis wide enough to make the dark state unreachable is
  rejected at configuration time.
- A Virtual Illuminance Sensor starts `unavailable` rather than `off` until it
  has parsed or restored a reading, so a source that has never reported doesn't
  assert darkness.
- Every Virtual Remote and turn-on selection form field now carries a
  description, and the sensor forms' advanced settings are collapsed by default.

### Fixed

- A wall switch or dimmer used within five seconds of a MoLight command on the same
  light — turning it back on right after an automatic off, dimming it early in a
  warning — is no longer ignored as the echo of MoLight's own command. Home Assistant
  reuses a command's context on that light for five seconds, so MoLight now compares
  each report against what it asked for instead of trusting the context alone.
- Combined occupancy no longer holds forever when its last `on` constituent
  drops out of the state machine; it clears, advancing `latest_occupied_time` to
  the dropout moment so dependent lights run a normal countdown.
- Maintained occupancy is seeded from the restored state rather than inferred
  from an on-duration guess, and a `last_on_time` restored alongside an `off`
  state is discarded.
- Lights no longer snap off seconds after a restart while someone is still in
  the room. Occupancy in progress at boot — or first provided by a source
  integration that loads late, even well after Home Assistant reports running —
  was timed from the moment it appeared, so it was misread as a false detection;
  it now takes the normal countdown.
- A door or illuminance sensor that briefly drops to unavailable and comes back
  unchanged no longer counts as a real change — a standing-open door recovering
  from a blip was read as someone opening it.
- When the light level drops, a room whose lights have no recent history of
  being on (and no occupancy sensor to anchor to) is no longer turned on — there
  is nothing to resume, so a long-empty or never-lit room now stays dark at dusk.
  The first illuminance reading after startup is also judged against the plain
  threshold rather than the hysteresis band.
- Dimming a real light directly during its pre-off warning now restores the
  color the warning replaced. Such an external dim cancels the warning and
  restarts the timer but brings no color of its own, so the warning's color was
  being left behind.
- On the night the clocks change, a schedule's start and end times are now
  compared by real elapsed time instead of by wall clock, so a window spanning
  the change no longer switches in the wrong order.

## [1.5.0] - 2026-07-28

### Added

- **Virtual Remote** — binds remote-control buttons to light actions with no
  hand-written automations. Single and double clicks of any remote whose buttons
  appear as `event` entities map to on/off/toggle/brightness-step/preset actions,
  with the single-vs-double vocabulary resolved per button from its advertised
  event types.
- A diagnostic **Last Action** sensor per Virtual Remote, recording the last
  action executed with its source button, resolved click, raw event type and
  time.
- Double-press support for Lutron Caséta Picos via the companion
  [lutron-caseta-events](https://github.com/jharris4/lutron-caseta-events)
  integration.

### Changed

- Documentation emphasises setting the occupancy timeout to match the real
  sensor's own hold time, which everything downstream is anchored to.

## [1.4.0] - 2026-07-12

### Added

- Color support for virtual lights, including automatic-on, effect and warning
  colors.
- Door sensor support — an `open_close` entity can turn a light on and hold it.
- Area and label filters plus a select-all toggle as the first step of bulk
  discovery.
- [EXAMPLES.md](EXAMPLES.md), with worked end-to-end configurations.

### Changed

- Config and options flows are organised into collapsible sections, with
  defaults consolidated in one place.
- Virtual occupancy sources are restricted to `occupancy`, `motion` and
  `presence` device classes, and MoLight's own occupancy sensors are excluded as
  sources.
- Form input is preserved when a validation error re-shows a step.

### Fixed

- Combined occupancy sensors reject self-references, cycles, and a constituent
  holding both the trigger and maintain roles.

## [1.3.0] - 2026-07-05

### Added

- Per-field descriptions throughout the config and options flows.
- Discovery steps allow tweaking the defaults applied to the discovered entries.

### Changed

- README reworked to be cleaner and more concise, with the test instructions
  moved into it.

## [1.2.0] - 2026-07-05

### Added

- Effect/warn warning before an automatic turn-off — blink or dim, with
  configurable warning and grace durations, instead of sudden darkness.
- Transition (fade) options for the on, off, effect and warning stages.
- Automatic-on brightness; leaving it blank keeps the light's existing
  brightness.

### Changed

- The maximum light timeout is raised from 1 hour to 4 hours.
- A virtual light's brightness always tracks the real lights it controls.

## [1.1.0] - 2026-07-04

### Added

- Bulk discovery — create many virtual lights in one pass, with an optional name
  prefix and suffix.
- Bulk assignment — attach one sensor to several existing virtual lights at once.
- Entity ID overrides in the config and options flows.
- Apache-2.0 license, required by HACS.

### Fixed

- Removing a virtual light config entry no longer errors.

## [1.0.0] - 2026-07-03

Initial release.

### Added

- **Virtual Occupancy Sensor** — wraps one real motion/presence sensor and
  estimates when the person actually left, with a configurable source hold
  timeout, false-detection classification, and a clear-after-unavailable
  timeout.
- **Virtual Combined Occupancy Sensor** — merges several occupancy sensors with
  distinct trigger and maintain roles.
- **Virtual Illuminance Sensor** — turns a lux reading into a steady bright/dark
  signal, with hysteresis and a gate-only mode.
- **Virtual Schedule Sensor** — a reusable schedule signal from a fixed-time
  and/or sun-based window, with sun offsets and follow-vs-gate behaviour.
- **Virtual Light** — controls N real lights from an occupancy-, illuminance- and
  schedule-aware state machine, with virtual brightness controls, an optional
  maintain-occupancy sensor, and a companion Auto-off switch for holding
  automatic turn-offs.
- State restored across Home Assistant restarts throughout, including timestamps,
  with startup-specific handling for lights, occupancy and illuminance.
- HACS and hassfest validation, CI, and a brand icon.

[Unreleased]: https://github.com/jharris4/molight/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/jharris4/molight/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/jharris4/molight/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/jharris4/molight/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/jharris4/molight/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/jharris4/molight/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/jharris4/molight/releases/tag/v1.0.0
