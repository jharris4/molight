# Changelog

All notable changes to MoLight are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Entries for 1.5.0 and earlier were reconstructed from the commit history after
> the fact and are deliberately coarse: they summarise each release rather than
> enumerate it. Later entries are written as the work lands.

## [Unreleased]

### Added

- **Virtual Combined Schedule**: combines schedules with any/all logic, so
  one light can follow several windows, e.g. a bedside lamp on in the
  morning and again in the evening. Combined schedules nest, and an
  unavailable input only matters when it could change the result.

### Fixed

- Editing a Virtual Occupancy Sensor's options while its source is
  unavailable, or has been removed, no longer clears occupancy at once. It
  stays occupied until the *clear after unavailable* timeout runs out,
  counted from the dropout. Previously a light whose timeout was shorter
  than that could turn off early.
- Changing a Virtual Light's turn-on selection entity in its options no
  longer fills the selection page with the previous entity's fallback and
  source. Previously, if the new entity was unavailable, the old fallback
  could be saved for it.
- A Virtual Light's options no longer accept the light itself, or a
  Virtual Light that already includes it, as a member, and no longer accept
  its own light or Auto-off switch as a keep-on entity. Previously each
  command to such a light set off a burst of nested calls that ended in an
  error in the log. A reference stored before this check is reported the
  same way when the options are next edited, and a multi-page edit repeats
  the check on its last page, in case another light's options were saved
  in the meantime.
- An illuminance threshold of 0 is now rejected on every form. Previously
  it was accepted, and the sensor could never report dark.
- Discovering illuminance sensors now rejects a hysteresis at or above the
  threshold, as creating one by hand already did. Previously the sensors
  were created and, after their first bright reading, never reported dark.
- A schedule window that sun offsets push past the following midnight is
  found again after a restart, and a window whose start falls in the
  spring-forward clock change is skipped that night when the shifted start
  would pass its end. Previously an inverted schedule counted that as a
  boundary.
- A follow-mode light whose schedule is `unavailable` or missing when Home
  Assistant finishes starting no longer treats that as the window ending.
  Previously, when the schedule recovered inside the same window, the light
  turned back on even if it had been turned off manually.
- A follow-mode real light coming back from `unavailable` is now made to
  match its schedule instead of being read as a manual change: inside the
  window it is re-lit with the window's brightness, color and turn-on
  selection, outside the window it is turned off. Previously a strip that
  turns its LEDs on at boot ran a full timer after a daytime power cut, and
  one that boots dark stayed off for the rest of the night. A light that is
  still loading when Home Assistant starts is not a reboot: its first state
  is adopted by the startup rules, so a manual off mid-window still stands.
  Its integration reloading later is one.
- A turn-on selection whose target select is missing or unavailable is now
  skipped with a warning. Previously the call was sent anyway, Home
  Assistant logged it, and `last_turn_on_selection_option` reported a
  selection that was never applied.
- A turn-on that names no brightness or color (occupancy with no auto-on
  brightness, or a plain `light.turn_on`) now reports the brightness and
  color the real light came on at. Previously the virtual light kept its
  earlier values, so the warn stage could jump to full brightness and a
  re-trigger could leave the room at the warn brightness.
- A real light's reply to one command that arrives after the next command
  was sent is no longer read as a manual change, unless the next command
  switched the light the other way and the reply comes more than a few
  seconds after its own command. Previously a slow bulb reporting the end of
  its effect-stage fade just after the warn stage began could restart the
  full timer at every expiry, so the light never turned off.
- A dim or recolor of a real light within 30 s of a command that changed
  nothing on it (such as a remote's "on" press while the light is already
  on) is now recognised as a manual change. Previously the virtual light
  kept reporting the old brightness and the timer was not restarted.
- A schedule that goes `unavailable` and comes back with the value it had
  before (as every MoLight schedule does when its options are edited) is
  no longer treated as a window starting or ending. Previously a light
  turned on manually outside the window was turned off, and in the gate
  modes a light turned off manually in an occupied room was turned back on.
  Occupancy that started, or a door that opened, while the gate was
  unreadable is applied when it returns, unless the light was turned off
  by hand after that.
- An occupancy sensor that goes `unavailable` and comes back still occupied
  (as a Virtual Occupancy Sensor or Virtual Combined Occupancy Sensor does
  when its options are edited) no longer counts as someone entering the
  room. Previously a light turned off manually in an occupied room was
  turned back on. A gate that lifted during the outage (the room going
  dark, a gate window starting, or a scheduled light changing profile) read
  the sensor as clear, so it is applied when the sensor returns, unless the
  light was turned off by hand after the gate lifted.
- Editing the options of a sensor that is part of a Virtual Combined
  Occupancy Sensor no longer clears the combined sensor while that sensor is
  the one holding the room occupied. Previously the reload ended occupancy,
  so lights started their countdown in an occupied room, and a maintain
  sensor could not start it again. A sensor that does not come back within
  10 seconds still clears it.
- Editing the options of a light whose **Auto-off** switch is off no longer
  applies a turn-off that was waiting for the switch: a follow window that
  ended, or a scheduled light's *Turn off* at schedule end. Previously the
  reload turned the light off. Disabling the switch while it is off releases
  the hold, as a restart does, since a disabled switch cannot be turned on.
- A schedule that is `unavailable` when Home Assistant restarts, or when its
  options are edited, keeps its window marker. Previously a source-backed
  schedule started a new window when its source returned, so a follow-mode
  light turned off manually mid-window turned back on. Turning *invert* on
  or off while the source is unavailable starts a new window when it
  returns instead of reusing the old marker.
- Illuminance going dark now resumes only an on-period that brightness cut
  short. Previously a light turned off manually, by its timer or by a
  schedule was turned back on when the room went from bright to dark within
  the turn-off timeout. The new `bright_forced_off` attribute carries this
  across a restart.
- A follow-mode light on an all-day (00:00 → 00:00) schedule now takes each
  midnight as a new window starting: a light turned off manually comes back
  on. Previously the running light ignored the new window, but turned on at
  the next restart or options edit, even after a manual off that day.
- Unticking every light in a sensor's bulk assignment, or every entry in a
  discovery checklist, now stands as an empty selection. Previously the
  form refilled it from its default, so the sensor stayed on those lights.
- A time-window schedule keeps its window marker while its on-period runs
  on, also across a restart. Previously windows chained around the clock
  got a later merged start at each boundary, which re-triggered follow mode
  and re-lit a light turned off manually.
- A follow-mode light turned off manually after its window ended while
  auto-off was held no longer remembers that window, also when the manual
  off came while the schedule was unavailable. Previously, when it was
  turned on again later, the next hold release or restart turned it off.
- Changing only the color of a real light during the effect or warn stage
  now restores the brightness the light had before the warning. Previously
  the light stayed at the stage's brightness for the whole new on-period.
- A light turned off while its turn-on was still waiting for a slow turn-on
  selection now stays off. Previously the waiting turn-on was sent
  afterwards, leaving the light on with no timer to turn it off. A manual
  turn-on that waits while a warning stage ends, or while occupancy asks for
  the light too, is still sent with its own brightness and color.
  Previously it was dropped, so the light finished its warning and turned
  off, or came on with the automatic settings instead.
- A false detection over a light turned on manually, at the wall, or by the
  door now leaves that turn-on its full turn-off timeout. Previously the
  countdown was taken from the sensor's `latest_occupied_time`, which a
  false detection does not advance, so a light turned on after an earlier
  visit could turn off the moment the sensor cleared.
- A Virtual Scheduled Light whose schedule ends while an automatic turn-on
  is still waiting for a slow turn-on selection now treats that turn-on as
  an on light: *Turn off* drops it, and *Keep state* or *Switch state* run
  the outside profile's timeout once it is sent. Previously the turn-on was
  sent after the boundary, and the light stayed on with no timer.
- A light whose entry is unloaded (its options saved, or the entry disabled
  or removed) while an automatic turn-on is still waiting for a slow
  turn-on selection no longer sends that turn-on afterwards. Previously the
  removed entity lit the room once the selection returned.
- A Virtual Combined Occupancy Sensor now takes over the
  `latest_occupied_time` its sensors already carry when it is created, and
  when an options edit adds a sensor with newer history. Previously that
  earlier visit counted as a detection in the next cycle, so a false
  detection was reported as genuine and `false_detection_count` did not
  increase.
- A Virtual Combined Occupancy Sensor that is reloaded or restarted while
  occupied now remembers the genuine detection the running cycle already
  contained. Previously, when the last sensor then cleared without a newer
  detection, the cycle was reported as a false detection, so lights it had
  lit turned off after the false-detection delay.
- A light's **Configure** form that was opened before the light was
  converted through **Convert virtual lights** can no longer be saved.
  Previously the stale form replaced the converted profiles with its own
  settings, so the light ran default settings on both sides of its schedule.

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

[Unreleased]: https://github.com/jharris4/molight/compare/v1.7.0...HEAD
[1.7.0]: https://github.com/jharris4/molight/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/jharris4/molight/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/jharris4/molight/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/jharris4/molight/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/jharris4/molight/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/jharris4/molight/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/jharris4/molight/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/jharris4/molight/releases/tag/v1.0.0
