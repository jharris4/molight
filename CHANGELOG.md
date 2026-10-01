# Changelog

All notable changes to MoLight are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Entries for 1.5.0 and earlier were reconstructed from the commit history after
> the fact and are deliberately coarse: they summarise each release rather than
> enumerate it. Later entries are written as the work lands.

## [Unreleased]

### Added

- **Standby for Virtual Scheduled Lights**: the inside-schedule settings can
  rest at a low brightness and colour instead of turning off. A dusk-to-dawn
  porch can glow at 1%, come up to 100% when someone walks up, drop back to 1%
  once they leave, and turn off when the schedule ends. A manual off turns
  standby off until the next schedule boundary.

### Fixed

- A *Switch state* schedule end also recalculates a turn-on still waiting on
  a slow select, instead of letting it light the room afterwards.
- A manual off while brightness already has the light off stops it coming
  back on when it gets dark.
- A follow-mode real light returning from `unavailable` gets its turn-on
  selection again even when it comes back at the level last sent.
- False detections: a light dimmed or recoloured at the wall keeps the full
  timeout that change restarted.
- A real light that loads late now fills in the colour of the turn-on it
  missed reporting.
- Changing an occupied Virtual Occupancy Sensor's source to a sensor that is
  `unavailable` no longer keeps it `on` with the old source's presence.
- A Virtual Combined Occupancy Sensor keeps its last clear's false-detection
  flag through a reload or restart, so the lights it cleared still get the
  quick false-detection off.
- A Virtual Combined Schedule Sensor treats an input whose entity (not just
  its entry) is disabled as `unavailable`, instead of still following its
  windows.
- Disabling a Virtual Light's Auto-off switch while it is off releases the
  hold straight away, instead of only after the next reload or restart.
- Discovery skips a pick that another MoLight entity started wrapping while
  its forms were open, and says how many it skipped.
- Turning a Virtual Scheduled Light back on by hand while someone's presence
  has it raised shows standby rejoined at once, so a restart keeps it.
- A keep-on entity that is also the illuminance sensor holds the light as it
  gets bright, instead of letting brightness turn it off first.
- A manual off of a Virtual Scheduled Light ends at the next window when one
  window starts as another ends, or at midnight for an all-day schedule, so
  standby comes back as it does after any other boundary.
- A restart no longer turns a light back on for presence, or a door left
  open, that it was turned off by hand during; a visit that began after the
  off still lights it. The new `last_off_manual` attribute records that off;
  a member light that reports in, or comes back from `unavailable`, while the
  light is off is not counted as one.
- Going dark after brightness turned a light off brings it back until its
  countdown would have ended, also when that countdown had been restarted by
  a dim or recolour at the wall, a released keep-on entity or Auto-off
  switch, or a standby light's *Keep state* schedule end. An old visit in
  the occupancy history no longer cancels it, and the new
  `bright_resume_until` attribute keeps it through a restart.
- A maintain occupancy sensor's `latest_occupied_time` counts toward that
  remaining time like an occupancy sensor's, and a light that presence was
  holding comes back within one timeout of brightness turning it off.
- Dimming, recolouring or turning on a real light at the wall while motion
  has the light on makes the on-period yours, as the same change through the
  virtual light does: a false detection no longer turns it off after the
  short delay, and a Virtual Scheduled Light that was turned off by hand
  rejoins standby.
- A real light turned on, dimmed or recoloured at the wall while a turn-on is
  still waiting on a slow select keeps the level set at the wall, instead of
  being overwritten when the select call finishes. This holds for an
  automatic turn-on (occupancy, a door, a schedule start, standby) and for
  one made through the virtual light.
- A schedule end also takes over a manual turn-on still waiting on a slow
  select, as it does an automatic one: *Turn off*, or a regular light's
  turn-off gate window ending, stops it lighting the room afterwards, *Switch
  state* recalculates it, and a keep-on hold keeps the end for it. An
  automatic turn-on no longer waits for a manual one that was turned off
  again before its select call finished, which left the room dark.
- Releasing a keep-on hold while a turn-on is still waiting on a slow select
  applies what the hold was keeping from that light: a *Turn off* schedule
  end it had deferred now turns the light off instead of being lost, and a
  light with no timer running gets its normal timeout instead of staying on.
- A *Turn off* schedule end crossed while brightness has a Virtual Scheduled
  Light off ends that on-period, so going dark shortly after no longer brings
  the light back on under the outside settings. *Keep state* and *Switch
  state* still resume it.
- A Virtual Light reports `on`, at the brightness and colour asked for, as
  soon as it accepts a turn-on, instead of only once a slow turn-on select
  call has finished. Two quick brightness-up clicks on a remote now add up
  instead of the second replacing the first, and a second toggle turns the
  light off again. A select call that fails in any way, not only with a Home
  Assistant error, still turns the lights on without the selection.
- A colour fade between far-apart colours (red to cyan, warm white to blue)
  is no longer taken for a recolour at the wall when a real light reports its
  colour partway through and blends through paler colours on the way. This
  covers the effect and warn stages, standby, auto-on colours and the
  restored pre-warning colour. A warning could cancel itself this way, and
  far-apart effect and warn colours could keep the light on indefinitely.
- A real light that loads, or comes back from `unavailable`, as off while a
  turn-on is still waiting on a slow select no longer cancels that turn-on
  and leaves the room dark, and is no longer recorded as a manual off, which
  also paused standby until the next schedule boundary.
- A real light that loads, or comes back from `unavailable`, as off while the
  light is on is sent the light's settings and turn-on selection again. It
  was recorded as a manual off: the light went off, standby paused until the
  next schedule boundary, and presence still there did not light it after a
  restart.
- A turn-on select on the same device as a real light (a WLED preset that
  includes "on") no longer has that light's own report, arriving while the
  select call is still in progress, taken for a turn-on at the wall, which
  dropped the automatic brightness and colour. A preset that lights the light
  while standby waits on the select no longer takes the light out of standby.

## [1.8.0] - 2026-09-30

This release adds the **Virtual Combined Schedule Sensor**, so one light can
follow several schedules at once. It also carries a round of fixes for
schedules, sensors and lights going `unavailable` and coming back, for options
edited part way through a visit or a schedule window, and for slow bulbs
replying late.

### Upgrade notes

- **No existing entry stops working after upgrading.** A Virtual Light that
  refers to itself as a member or keep-on entity is now rejected, but only the
  next time its options are saved.

### Added

- **Virtual Combined Schedule Sensor**: combines Virtual Schedule Sensors with
  any/all logic, e.g. a bedside lamp on in the morning and again in the
  evening. Combined schedules nest, and can be inverted like any other
  schedule.

### Changed

- An all-day schedule in follow mode re-lights a manually-off light at each
  midnight, not just at the next restart or options edit.
- The forms reject an illuminance threshold of 0 and a discovered hysteresis
  at or above the threshold, and recheck a light's timeout against its
  sensors when the last page of a multi-page form is saved.

### Fixed

- A schedule or occupancy sensor that goes `unavailable` and comes back
  unchanged, including at startup and whenever its options are edited, is no
  longer read as a schedule window starting or ending, or as someone arriving
  or leaving; a light turned off manually stays off.
- Schedule window markers survive restarts, windows chained around the clock,
  sun offsets that push an end past midnight or before its start, and the
  spring-forward clock change.
- False detections: a combined sensor keeps its cycle's genuine detection
  across a reload and inherits its constituents' `latest_occupied_time`, and a
  light turned on by hand or by a door keeps its full timeout.
- A maintain sensor clearing or a door closing while brightness or a gate-mode
  schedule window blocks occupancy starts the countdown instead of leaving the
  light on with no timer.
- Slow and chatty bulbs: late replies, mid-fade colors and clamped color
  temperatures are no longer read as manual changes, so a warning no longer
  restarts the timer. A dim right after a command that changed nothing is
  read as one.
- A follow-mode real light returning from `unavailable` is made to match its
  schedule instead of being read as a manual change.
- Turn-on selection: a missing target select is skipped with a warning, a
  turn-on with no brightness or color reports what the light came on at, and a
  turn-on waiting on a slow select is dropped when the light is turned off,
  its schedule ends or its entry is unloaded.
- Illuminance going dark resumes only an on-period that brightness cut short;
  the new `bright_forced_off` attribute carries this across a restart.
- Editing a light's options while its Auto-off switch is off no longer applies
  the turn-off it was holding; disabling the switch while off releases it.
- Options flows: a changed occupancy source times its cycle from that sensor's
  own start, a changed turn-on selection entity drops the old fallback, an
  emptied checklist stays empty, and a stale **Configure** form can't
  overwrite a converted light.

## [1.7.0] - 2026-08-27

### Added

- The effect and warn stages accept a **white color temperature** as their
  color, not just an RGB color, so temperature-only bulbs can have a pre-off
  color cue too.

### Fixed

- A configured color can now be un-set: each RGB color / color temperature
  pair (auto-on, effect, warn, and the remote presets) gained a **color
  mode** dropdown whose **None** choice clears it. Previously a color that
  the chosen lights couldn't show could leave the form impossible to save.
  The dropdown also chooses which of the pair applies; the "not both"
  errors now appear only when it is left blank.

## [1.6.0] - 2026-08-23

A release focused on scheduling: the new **Virtual Scheduled Light** switches
between two complete settings profiles as a schedule opens and closes, and
ordinary Virtual Lights gain two more schedule-end behaviours. Plus a round of
restart, occupancy, sensor and slow-bulb edge-case fixes.

MoLight is also now in the HACS default repository list, so installing it no
longer needs a custom repository.

### Upgrade notes

- **Existing schedule-gated lights behave exactly as before.** This release
  adds two more gate modes (see *Added*), so the original behaviour now sits
  beside them under the label *Gate and turn off*; only the name is new.
- **No existing entry stops working after upgrading.** The stricter settings
  validation added to the config forms (see *Changed*) never runs at startup,
  so entries configured under earlier versions keep running as they are, but
  the next time one is edited, the form won't save until any newly rejected
  setting is corrected.

### Added

- **Virtual Scheduled Light**: a new entity type holding two complete Virtual
  Light settings sets, switched by a Virtual Schedule Sensor; **At schedule
  end** decides what the window's end does to a light that is still on.
- **Convert virtual lights**: promote a gated Virtual Light to a scheduled
  one, or back, in place, keeping entity IDs and history.
- **Turn-on selection**: drive a `select` entity (a preset, theme, or mode)
  whenever the light turns on, with an optional source entity supplying the
  option.
- **Source-backed and inverted schedules**: a Virtual Schedule Sensor can
  mirror any existing binary sensor, and either definition can be inverted.
- **Two new schedule gate modes**: *Gate and switch state* and *Gate and keep
  state* choose what the window's end does to an already-on light.
- **Transition pass-through**: a `transition` in a virtual light's
  `turn_on`/`turn_off` is forwarded to the real lights.
- A `warning_active` attribute reporting an in-progress pre-off warning,
  restored across restarts.
- Discovery now reports how many entries it actually created.

### Changed

- Light schedule pickers offer only MoLight schedule sensors.
- The forms reject settings none of the chosen lights could apply (colors,
  fades, brightness), an unreachable illuminance hysteresis, and stage values
  on a disabled warning stage.
- A Virtual Illuminance Sensor starts `unavailable` rather than asserting
  darkness before its source has ever reported.
- Every form field now carries a description; advanced sensor settings are
  collapsed by default.

### Fixed

- Slow and chatty bulbs: replies that arrive late, in parts (power first, then
  level or color), quantised, or as fade steps are recognised as MoLight's own
  echo, while a wall switch or dimmer used right after a MoLight command is no
  longer mistaken for one.
- Restarts: occupancy already in progress at boot is no longer misread as a
  false detection, and maintained occupancy carries across regardless of which
  entry loads first.
- Combined occupancy clears instead of holding forever when its last active
  constituent disappears, including when that constituent is deleted.
- A door or illuminance sensor that drops to unavailable and recovers unchanged
  no longer registers as a real change.
- A long-empty or never-lit room stays dark at dusk instead of turning on with
  nothing to resume.
- An external dim during the pre-off warning restores the color the warning
  replaced.
- Schedules switch in the right order on the night the clocks change.
- Options flows: clearing a combined sensor's maintain list now sticks, a
  rename reloads the entry once instead of twice, and an entity ID with no
  usable characters is rejected instead of silently dropped.
- A member light deleted from Home Assistant outright no longer pins its
  virtual light on.

## [1.5.0] - 2026-07-28

### Added

- **Virtual Remote**: binds remote-control buttons to light actions with no
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
- Door sensor support: an `open_close` entity can turn a light on and hold it.
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

- Effect/warn warning before an automatic turn-off: blink or dim, with
  configurable warning and grace durations, instead of sudden darkness.
- Transition (fade) options for the on, off, effect and warning stages.
- Automatic-on brightness; leaving it blank keeps the light's existing
  brightness.

### Changed

- The maximum light timeout is raised from 1 hour to 4 hours.
- A virtual light's brightness always tracks the real lights it controls.

## [1.1.0] - 2026-07-04

### Added

- Bulk discovery: create many virtual lights in one pass, with an optional name
  prefix and suffix.
- Bulk assignment: attach one sensor to several existing virtual lights at once.
- Entity ID overrides in the config flow's create steps.
- Apache-2.0 license, required by HACS.

### Fixed

- Removing a virtual light config entry no longer errors.

## [1.0.0] - 2026-07-03

Initial release.

### Added

- **Virtual Occupancy Sensor**: wraps one real motion/presence sensor and
  estimates when the person actually left, with a configurable source hold
  timeout, false-detection classification, and a clear-after-unavailable
  timeout.
- **Virtual Combined Occupancy Sensor**: merges several occupancy sensors with
  distinct trigger and maintain roles.
- **Virtual Illuminance Sensor**: turns a lux reading into a steady bright/dark
  signal, with hysteresis.
- **Virtual Schedule Sensor**: a reusable schedule signal from a fixed-time
  and/or sun-based window, with sun offsets.
- **Virtual Light**: controls N real lights from an occupancy-, illuminance- and
  schedule-aware state machine (including the illuminance control-vs-gate and
  schedule follow-vs-gate modes), with virtual brightness controls, an optional
  maintain-occupancy sensor, and a companion Auto-off switch for holding
  automatic turn-offs.
- State restored across Home Assistant restarts throughout, including timestamps,
  with startup-specific handling for lights, occupancy and illuminance.
- HACS and hassfest validation, CI, and a brand icon.

[Unreleased]: https://github.com/jharris4/molight/compare/v1.8.0...HEAD
[1.8.0]: https://github.com/jharris4/molight/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/jharris4/molight/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/jharris4/molight/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/jharris4/molight/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/jharris4/molight/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/jharris4/molight/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/jharris4/molight/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/jharris4/molight/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/jharris4/molight/releases/tag/v1.0.0
