# Changelog

All notable changes to MoLight are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Entries for 1.5.0 and earlier were reconstructed from the commit history after
> the fact and are deliberately coarse: they summarize each release rather than
> enumerate it. Later entries are written as the work lands.

## [Unreleased]

This release adds **standby** for Virtual Scheduled Lights and makes sure each
light is controlled by only one virtual light. It also carries a large round of
fixes for restarts, renamed or deleted entities, slow turn-on selects, long and
colored fades, false detections, doors, illuminance, sun schedules and Virtual
Remote buttons.

### Upgrade notes

- **Existing entries keep working.** A light saved before the new checks
  under Changed is refused only the next time its options are saved, and
  overlapping lights and invalid keep-on entities are named in a warning at
  load. The exception is a Virtual Light inside a light group that contains
  it: it never lit its bulbs, and it now stops controlling that group.
- **Schedule windows that pair a sun event with a fixed time** now follow
  Home Assistant's own sun and time conditions (README, "Overnight windows").
  A window from a sun event to a fixed time early the next day, such as
  *sunrise → 01:00*, is now always empty; invert *01:00 → sunrise* instead. A
  schedule saved with a window that never opens logs a warning.

### Added

- **Standby for Virtual Scheduled Lights**: the inside-schedule settings can
  rest at a low brightness and color instead of turning off. A dusk-to-dawn
  porch can glow at 1%, come up to 100% when someone walks up, drop back to 1%
  once they leave, and turn off when the schedule ends. A manual off holds
  until the next schedule boundary.
- **A note on wrapped virtual lights**: when a light form adds another MoLight
  virtual light, one more page explains that the wrapped light keeps its own
  timer, sensors and schedule, and that turning off its Auto-off switch lets
  this light's timeout govern it.

### Changed

- **A light belongs to one virtual light.** The light forms, their pickers and
  Discover lights refuse a light another virtual light already controls, a
  light group that contains a virtual light, and two picks that share a light,
  such as a light and a group that contains it. Two virtual lights commanding
  one light fight over it; add the virtual light that has it instead.
- Keep-on entities can no longer be the light itself, a light or group that
  shares its lights, or an entity that never reads `on`, such as a media
  player or a person.
- Every form rejects a blank Name, and the schedule form rejects a window that
  never opens at the home location.

### Fixed

- **Wrapped virtual lights**: what the wrapped light does by itself, such as
  its timer turning it off, its warn stage or its own sensor lighting it, is
  no longer taken for a change at the wall, also through a light group changed
  to contain it. It recorded a manual off that kept an occupied room dark on
  the next visit. A command to the wrapped light that changes nothing it shows
  now counts for the wrapping light too. A light group changed to include the
  light that controls it no longer makes that light command itself in a loop.
- **Restarts**: a virtual light reports the state it had until Home Assistant
  has finished starting, instead of flicking off and on. A light turned off by
  hand no longer comes back on after a restart for the same visit or open
  door. A schedule end missed while Home Assistant was down, and the
  pre-warning brightness after a restart mid-warning, are applied to a real
  light that loads late.
- **Renamed, deleted or disabled entities**: changing an entity ID in Home
  Assistant is now followed; every entry that used it kept the old ID and
  quietly stopped working. Renaming a MoLight entity no longer makes it forget
  its state. A deleted or disabled sensor, door or keep-on entity counts as
  clear, closed or off, as at startup, so the light gets its timeout instead
  of staying on. A Virtual Illuminance Sensor whose source is deleted goes
  `unavailable` instead of staying bright for good.
- **Long and colored fades**: a fade longer than five seconds, a bulb that
  brightens in steps from the level it remembered, a fade between far-apart
  colors, and a color-temperature-only light answering a color are no longer
  taken for a change at the wall. These cancelled warnings, restarted the
  timer, or kept a light on indefinitely.
- **Slow turn-on selects** (such as WLED presets): a turn-on waits at most 10
  seconds for its select, and the virtual light reports `on` straight away,
  so two quick brightness-up clicks add up and a second toggle turns it off.
  A change at the wall, a schedule end, a keep-on release or a warning during
  the wait is no longer lost or overwritten, and a preset that lights its own
  device is no longer read as a turn-on at the wall. A select with no option
  chosen gets the selection, and options with leading or trailing spaces can
  be picked.
- **Real lights coming back**: a real light that loads late or returns from
  `unavailable` as off while the virtual light is on is sent its settings
  again, instead of being recorded as a manual off. One returning just before
  a warning no longer jumps back to full brightness. Cancelling a warning at
  the wall on one of several real lights restores them all. A light that
  replies before its turn-on call returns, such as LightwaveRF, no longer
  reports `off` for a moment.
- **False detections**: only a light that the false detection's own cycle lit
  gets the quick off; a blip after a genuine visit keeps that visit's
  countdown. A change at the wall during a detection makes the on-period
  yours. An off delay longer than the turn-off timeout no longer keeps the
  light on past the timeout.
- **Doors**: an `open_close` door sensor that stays `unavailable` for 60
  seconds, such as one with a dead battery, counts as closed instead of
  holding the light on until a restart. Outside a *Gate and turn off*
  schedule window, a light that is on ignores an open door, as the README
  says.
- **Illuminance**: in *Gate* mode, brightness only stops an off light turning
  on. When the sensor saw the lamp, the light turned off over people still
  moving and came back once the room read dark. Going dark after brightness
  turned a light off brings it back for the rest of its countdown, also across
  a restart, and a manual off or a schedule end ends that. Changing the
  source no longer keeps the old source's reading.
- **Schedules**: a window with a sun event on one edge and a fixed time on
  the other no longer runs round the clock once the sun event passes the
  fixed time; *sunset → 21:00* was on almost all day in summer. Nights next to
  a polar period, time zones a day ahead of their longitude, and a change to
  Home Assistant's time zone or location are handled. Changing a follow-mode
  light's schedule no longer turns it off. Releasing a hold under *Gate and
  turn off* turns the light off only if a schedule window ended while it was
  held. A source-backed schedule refuses a source that includes the schedule
  itself, such as a group that contains it.
- **Virtual Remote**: buttons from HomeKit controller, native Lutron, Shelly
  Gen2 and later, and Z-Wave scene controllers work, and double clicks on
  those and on BTHome and Xiaomi BLE buttons are accepted. *Brightness up* and
  *Brightness down* during a warning step from the brightness before it.

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
ordinary Virtual Lights gain two more schedule-end behaviors. Plus a round of
restart, occupancy, sensor and slow-bulb edge-case fixes.

MoLight is also now in the HACS default repository list, so installing it no
longer needs a custom repository.

### Upgrade notes

- **Existing schedule-gated lights behave exactly as before.** This release
  adds two more gate modes (see *Added*), so the original behavior now sits
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
  level or color), quantized, or as fade steps are recognized as MoLight's own
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

- Documentation emphasizes setting the occupancy timeout to match the real
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

- Config and options flows are organized into collapsible sections, with
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
