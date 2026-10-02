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
  standby off until the next schedule boundary. A turn-on without a
  brightness, such as a voice "turn on", raises it as presence would.

### Fixed

- A virtual light reports the state it had before a restart until Home
  Assistant has finished starting, instead of `off` and `idle`. A crash or
  power cut shortly after a restart no longer brings standby back on after a
  manual off, loses a missed *Turn off* end or forgets that the light was
  resting at standby, a light that was on no longer flicks off and on at
  startup, and a virtual light that wraps another one reads its real state.
- A Virtual Combined Occupancy Sensor that is still waiting for a maintain
  sensor to load keeps that wait through another restart.
- A schedule end missed while Home Assistant was down is applied to a real
  light that loads late and reports lit, instead of adopting it with a fresh
  timeout: follow mode, *Turn off* and *Switch state* ends, also under a
  keep-on hold. A turn-on in the meantime, or the next schedule window
  starting, replaces the missed end.
- A restart during an effect or warn stage restores the pre-warning brightness
  and colour on a real light that loads late, instead of keeping the
  warning's.
- A manual off that a Virtual Scheduled Light's schedule boundary had ended
  no longer comes back with a restart; the new `manual_off_cleared` attribute
  records it.
- Changing a follow-mode light's schedule, or its schedule mode, no longer
  turns it off as if the old schedule's window had ended. The new
  `schedule_window_schedule` attribute records which schedule the window
  marker belongs to.
- Changing an entity ID in Home Assistant's entity settings is now followed.
  Every MoLight entry that referenced the entity, real or virtual, kept the
  old ID and quietly stopped working: a light no longer reacted to its
  renamed occupancy sensor, a renamed gate schedule read as a gate that never
  opens, and a remote lost its buttons or its lights. The entries are
  rewritten and reload, without the rename being taken for a manual off, a
  new visit or a new schedule window.
- Changing the entity ID of a MoLight entity itself no longer makes it forget
  its state. An Auto-off switch that was off came back on and released its
  hold, a Virtual Scheduled Light could replay a schedule end it had already
  applied and turn off a light lit by hand, a Virtual Occupancy Sensor lost
  its `latest_occupied_time`, and a schedule that mirrors a sensor dated its
  window anew.
- An entity a virtual light uses that is deleted or disabled while Home
  Assistant runs is treated as it would be at startup, instead of keeping its
  last value until the next restart. A keep-on entity that was on stops
  holding, an open `open_close` door counts as closed, and an occupancy or
  maintain sensor that was on counts as clear, so the light gets its timeout
  instead of staying on indefinitely. A disabled real light no longer stops
  the virtual light from turning off when the remaining real lights are
  turned off, and a light whose only lit real light goes reports off.
- Changing a Virtual Illuminance Sensor's source no longer keeps the old
  source's reading. The sensor stayed bright or dark, on the old source's
  side of the hysteresis band, until the new source crossed the far edge of
  the band, and stayed bright with a new source that was `unavailable`. It
  is now `unavailable` until the new source reports, and that first reading
  is judged against the bare threshold.
- A fade longer than five seconds is no longer taken for a change at the wall
  when a real light reports it in steps. Home Assistant stops tagging the
  light's reports with MoLight's command after five seconds, so the later
  steps of a 10 s warning fade cancelled the warning and restarted the
  timeout, and a long auto-on fade was recorded as a turn-on at the wall. The
  same goes for effect, standby and caller fades, and for the fade back to
  the pre-warning brightness. A fade to off of any length that reports dimmer
  levels on its way no longer turns the virtual light back on and then
  records a manual off.
- With colour lights and colour-temperature-only lights in one virtual light,
  a slow reply from the latter to a colour is no longer taken for a change at
  the wall. Home Assistant sends such a light the nearest colour temperature,
  which MoLight did not expect back: an automatic turn-on with a colour was
  recorded as a turn-on at the wall and lost the false-detection quick off,
  and a coloured warning stage cancelled itself and restarted the timeout.
- A colour temperature outside the range a virtual light's real lights span,
  such as an auto-on, stage or standby colour temperature of 2000 K on lights
  that start at 2700 K, is reported as the nearest one in the range, which is
  what the real lights show. The virtual light reported 2000 K, below its own
  `min_color_temp_kelvin`, and remembered that through a warning.
- Cancelling a warning at the wall on one of several real lights restores
  the others too. Turning one light back on during a blink-off effect left
  the rest off for the whole new on-period, and dimming or recolouring one
  during a stage left the rest at the stage's brightness and colour.
- A command sent while a replaced turn-on's select call is still in progress
  is no longer undone by the preset. A real light that came back from
  `unavailable` was being re-sent its settings and preset when the light
  dropped to standby, or was given a new brightness: the preset then set its
  own brightness on the device, which stayed there, and the virtual light
  took it for a dim at the wall, left standby and restarted its timer. The
  newest command is now sent again when the select call finishes.
- A preset that lights its own device's light in the last moment of the
  select call is no longer taken for a turn-on at the wall when the light's
  report reaches MoLight just after the call has returned.
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
- A false-detection off delay longer than the turn-off timeout no longer keeps
  a false detection's light on past that timeout. With a 60 s timeout and a
  300 s delay, the light stayed on for 300 s; it now turns off after 60 s.
- Outside a *Gate and turn off* schedule window, a light that is on ignores an
  `open_close` door, as the README says. A door already standing open held a
  light turned on by hand or at the wall, or found on at startup, with no
  timer, and kept it on after the maintain sensor cleared; closing the door
  restarted the timer. A schedule that blips `unavailable` inside the window
  still leaves the door holding.
- An `open_close` door whose sensor goes `unavailable` while the door is open,
  such as a battery contact sensor that dies, no longer holds the light on
  until Home Assistant restarts. After 60 seconds it counts as closed, as it
  would at startup, and the countdown starts; a shorter blip still keeps the
  hold. A door counted closed this way no longer lights the room when it gets
  dark or a gate schedule window starts, and an open door it reports later is
  treated like one first seen at startup.
- A false detection only turns off quickly a light that its own cycle lit. A
  blip during the countdown after a genuine visit, or after the maintain
  sensor saw someone, turned the light off 5 s later instead of when the
  visit's countdown ended; it now keeps that countdown. This also covers a
  light lit by the room going dark or a gate schedule window starting. A door
  opened while a blip has the light on also stops the quick off, and the light
  gets the full timeout from the opening.
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
- Home Assistant and HACS list MoLight as *Calculated* instead of *Local
  Push*, since it only works from other entities' states.
- A turn-on select with no option currently chosen (state `unknown`, as a
  WLED preset select reports after any change in the WLED app) gets the
  turn-on selection, instead of being skipped like an `unavailable` one.
- A Virtual Illuminance Sensor treats a `nan` or `inf` reading as no reading
  and holds its last value, instead of reading `nan` as dark and `inf` as
  bright.
- A Virtual Illuminance Sensor whose source is deleted or disabled, also
  along with its integration, is `unavailable` instead of keeping its last
  reading for good, across restarts too. A light gated by a sensor stuck
  bright that way never came on for presence or a door again. A source whose
  entity ID changes, or whose integration reloads, still keeps the reading.
  A light the sensor kept dark reacts at once, as to the room going dark: an
  occupied room lights, and an on-period brightness cut short resumes. So
  does a light whose illuminance sensor is itself deleted or disabled, or
  switched to a source that has not reported yet.
- A turn-on selection option with leading or trailing spaces (WLED preset
  names are free text) can be chosen. It was rejected as not offered, or, when
  the target also offered the option without the spaces, silently replaced by
  that one. Spaces typed around an option are still ignored when that leaves
  a single option, also at turn-on for a value entered while the target was
  unavailable.
- Every create and Configure form rejects a blank Name, which gave an entry
  with no title and an entity ID such as `light.unknown`. Spaces around a
  name are dropped.
- The Configure forms judge effect and warn stage timeouts in whole seconds,
  as the light runs them. A fractional timeout could pass a check for a stage
  the light then ran shorter or not at all, such as a 0.5 s effect stage with
  a brightness the light ignored, or a 2.5 s fade on a stage that ran 2 s.
- A schedule window with a sun event on one edge and a fixed time on the
  other no longer runs round the clock once the sun event passes the fixed
  time. *sunset → 21:00* was `on` almost all day through the weeks the sun
  sets after 21:00, and *06:00 → sunrise* all summer, so a follow light stayed
  lit and a gate never closed. Whether a window runs overnight is now decided
  from how its edges are set (README, "Overnight windows"), and the window is
  empty on such a day, as with Home Assistant's own sun and time conditions.
  A window from a sun event to a fixed time in the same half of the next day,
  such as *sunrise → 01:00*, is now always empty; invert *01:00 → sunrise*
  instead. The form rejects a window that never opens at the home location,
  and a schedule already saved with one logs a warning when it loads.
- The nights next to a polar period are no longer skipped: the first short
  night after the midnight sun, and an evening window that ends at the first
  sunrise after the polar night.
- A window edge that combines a sun event with a fixed time just past
  midnight can pick the fixed time. *End at the later of 4 h after sunset and
  00:00* always ended 4 h after sunset, because the 00:00 was taken as the
  midnight that began the day; it is now the midnight that follows.
- In time zones a day ahead of their longitude (Samoa, Tonga, Kiritimati, the
  Chatham Islands) a window pairs a fixed time with the same day's sunrise or
  sunset. It used the next day's, so *05:00 → sunrise* ran for about 26 hours.
- An inverted sun schedule is one window for the whole of a polar period.
  Three days into the midnight sun an inverted *sunset → sunrise* schedule
  changed its `current_window_start`, which turned a follow-mode light back
  on after a manual off and gave a Virtual Scheduled Light a new settings
  window.
- Changing Home Assistant's time zone or home location re-evaluates
  time-window and combined schedules at once. They kept the old boundary
  until its timer fired, hours later, and a schedule still `on` then moved
  its `current_window_start`, which follow-mode lights and Virtual Scheduled
  Lights took for a new window. A schedule that stays `on` through the change
  now keeps its window, also across a restart.
- Virtual Remote buttons from HomeKit controller, native Lutron, Shelly Gen2
  and later, and Z-Wave scene controllers work: their `single_press`,
  `single_push` and `KeyPressed` clicks were ignored. Double clicks on those
  and on BTHome and Xiaomi BLE buttons are accepted and fire instead of being
  rejected at setup, and a single click on a button that has none, such as a
  rotary dial or a doorbell, is now rejected at setup instead of saving a
  binding that never fires.
- A Virtual Remote's *Brightness up* and *Brightness down* during a MoLight
  light's effect or warn stage step from the brightness the light had before
  the warning. They stepped from the warning level, so a light at 80% with a
  10% warning went to 20% on *Brightness up* and off on *Brightness down*.
- On Home Assistant 2026.7 and earlier, a Virtual Remote click that a button
  reports in the same millisecond as its previous event, such as a Shelly
  `single_push` arriving with its `btn_up`, runs its binding. It was ignored,
  because Home Assistant gave both events the same state.
- A virtual light no longer reports `off` for a moment when it is turned on
  and a real light replies before the turn-on call has returned, as
  LightwaveRF lights, a light group of them and another MoLight light do. An
  automation watching the light saw on, off, on, and a virtual light wrapping
  that one recorded a manual off.
- A turn-on waits at most 10 seconds for its turn-on selection. A select that
  never answered, such as a template select whose action waits or an
  integration stuck on an unreachable device, left the virtual light
  reporting `on` with the room dark for as long as the call hung. The call is
  now cancelled, a warning is logged and the lights come on without the
  selection.
- A Virtual Scheduled Light that switches settings while a turn-on selection
  is still being applied no longer takes the preset lighting its own device's
  light for a turn-on at the wall. It read the device from the settings now
  active, so the waiting turn-on was dropped, the light stayed at the
  preset's brightness and `last_on_physical` was stamped. A failed selection
  is also logged under the select entity that was called.
- A real light that comes back from `unavailable` shortly before a warning no
  longer hides it. The settings sent to it again waited for the turn-on
  selection and then went out as they were before the warning: the lights
  jumped back to full brightness while the light reported the effect or warn
  stage, a stage that blinks the lights off was relit, and a stage's colour
  was replaced. The stage the light is in when the select call finishes is
  now what is sent, and after a warning that occupancy ended meanwhile, the
  restored brightness and colour. An automatic turn-on that waits into a
  warning does the same; a manual turn-on still wins over the stage.
- In illuminance *Gate* mode, brightness only stops an off light turning on.
  When the lux sensor saw the lamp, someone moving again during the countdown
  was ignored as "bright", and the light went off over them and came back on
  once the room read dark; occupancy did not cancel a warning either. An on
  light is now held and re-triggered by occupancy and the door as in the
  dark: on a turn-on by hand or at the wall, a hold's release, a door or
  maintain sensor clearing, at startup and at a *Gate and switch state* end.
  Raising standby still waits for the dark.
- Releasing an Auto-off or keep-on hold on a light under *Gate and turn off*
  turns it off only when a window ended while it was held. A light turned on
  by hand outside the window was turned off at once on release instead of
  getting a fresh timer, also after being turned off and on again by hand
  since the end. The held end is kept across a restart and reported in
  `schedule_end_off_pending`, and a window starting again drops it.
- A *Gate and turn off* window that ends while brightness has the light off
  ends the on-period brightness cut short. The room going dark in a later
  window, after someone merely walked past while it was bright, resumed it
  and lit an empty room.
- A create or Configure form left open while a sensor, schedule or light it
  offers is deleted no longer saves that reference again, which brought back
  the reference the deletion had just removed. The save is refused, also in
  bulk assignment, the Discover lights defaults and a Virtual Scheduled
  Light's later forms. An entity renamed meanwhile is saved under its new ID,
  and the turn-off timeout and schedule checks still apply to it.
- Two Discover flows submitted at the same moment no longer both wrap the
  same light, occupancy sensor or illuminance sensor, which left two entries
  fighting over it. The second skips the pick, as it does one wrapped earlier.
- An inverted Virtual Combined Schedule Sensor whose last input is deleted is
  `off`, as the README says, instead of `on` for good. A follow-mode light
  on a "Not Day" combination came on and stayed on when "Day" was deleted.
- A Virtual Light can no longer include a Home Assistant light group that
  includes the virtual light, directly or through another Virtual Light. Such
  a light never lit its bulbs, and turning it off called itself until Home
  Assistant gave up with an error.

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
