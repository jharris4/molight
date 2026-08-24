# Changelog

All notable changes to MoLight are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Entries for 1.5.0 and earlier were reconstructed from the commit history after
> the fact and are deliberately coarse — they summarise each release rather than
> enumerate it. Later entries are written as the work lands.

## [Unreleased]

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
  beside them under the label *Gate and turn off* — a rename only.
- **No existing entry stops working after upgrading.** The stricter settings
  validation added to the config forms (see *Changed*) never runs at startup,
  so entries configured under earlier versions keep running as they are — but
  the next time one is edited, the form won't save until any newly rejected
  setting is corrected.

### Added

- **Virtual Scheduled Light** — a new entity type holding two complete Virtual
  Light settings sets, switched by a Virtual Schedule Sensor; **At schedule
  end** decides what the window's end does to a light that is still on.
- **Convert virtual lights** — promote a gated Virtual Light to a scheduled
  one, or back, in place, keeping entity IDs and history.
- **Turn-on selection** — drive a `select` entity (a preset, theme, or mode)
  whenever the light turns on, with an optional source entity supplying the
  option.
- **Source-backed and inverted schedules** — a Virtual Schedule Sensor can
  mirror any existing binary sensor, and either definition can be inverted.
- **Two new schedule gate modes** — *Gate and switch state* and *Gate and keep
  state* choose what the window's end does to an already-on light.
- **Transition pass-through** — a `transition` in a virtual light's
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
  echo — while a wall switch or dimmer used right after a MoLight command is no
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
- Entity ID overrides in the config flow's create steps.
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
  signal, with hysteresis.
- **Virtual Schedule Sensor** — a reusable schedule signal from a fixed-time
  and/or sun-based window, with sun offsets.
- **Virtual Light** — controls N real lights from an occupancy-, illuminance- and
  schedule-aware state machine (including the illuminance control-vs-gate and
  schedule follow-vs-gate modes), with virtual brightness controls, an optional
  maintain-occupancy sensor, and a companion Auto-off switch for holding
  automatic turn-offs.
- State restored across Home Assistant restarts throughout, including timestamps,
  with startup-specific handling for lights, occupancy and illuminance.
- HACS and hassfest validation, CI, and a brand icon.

[Unreleased]: https://github.com/jharris4/molight/compare/v1.6.0...HEAD
[1.6.0]: https://github.com/jharris4/molight/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/jharris4/molight/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/jharris4/molight/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/jharris4/molight/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/jharris4/molight/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/jharris4/molight/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/jharris4/molight/releases/tag/v1.0.0
