# MoLight roadmap

What is planned, in what order, and why. Nothing here is a promise: an item is built when its turn
comes and the reason still holds. The [CHANGELOG](CHANGELOG.md) records what shipped; this file
records what should be built next, and what was asked for and answered another way.

## Rules

- **Risk is the gate.** The state machine in light.py is where every breakage risk in this codebase
  lives. An item that adds an input to the light's precedence rules (manual, then holds, then a
  follow window, then forced offs, then occupancy and the door) or changes what counts as a change at
  the wall ships alone, with matrix tests, an e2e run and a REFERENCE.md section, as standby did.
  Anything else can share a release.
- **Every new setting starts off**, so an existing entry behaves exactly as before.
- **An ask that existing blocks plus a Home Assistant helper answer is not built.** It goes under
  Already covered, so the reply to the request can link here.
- **Demand is a guess.** With one issue filed, the order below is the maintainer's judgment of what a
  user of the existing blocks still cannot get. A user-filed request gets a line here the day it
  arrives, and an issue is opened for an item only when it is next, labeled `enhancement`, and
  closed with the release.

## Plan

### 1. Manual off holds until the room empties

Ships alone.

- **Problem.** Turn the light off by hand while you are in the room, to watch a film or go to
  sleep, and the motion sensor clears after its hold time, sees you again, and the light comes back
  on. Today a manual off stands only over the visit it ended, and the sensor's next on-cycle is a new
  visit. The only way to stop it is a gate through a helper you flip, not the light. This is the most
  common complaint about motion lighting anywhere.
- **Design.** One option on the light, off by default. When on, after an off by hand (through the
  virtual light or at the wall) occupancy and the door do not turn the light on until the room has
  been empty for the turn-off timeout, by the same rule the countdown uses, so there is no new
  number to set. A turn-on by hand or at the wall ends it, as does a schedule boundary, as they end a
  manual off today. A blocked turn-on fires a `turn_on_blocked` event on the Events entity.
- **Why now.** It is the one gap a remote or a voice assistant cannot paper over: a Turn off then
  means "turn off and stay off", which is what the services idea under Not planned was for.
- **Cost.** It is an input to precedence rule 5, so matrix tests, an e2e run and a REFERENCE.md
  section.

### 2. Three small additions

One release, none touching the precedence rules.

- **Home Assistant's Schedule helper as a schedule source.** "Weekday mornings only", or three schedule
  windows a day, takes a workday sensor plus a combined All schedule, or several schedule entries,
  while the Schedule helper edits all of that on the Helpers page. The source picker of the Virtual
  Schedule Sensor takes `binary_sensor` only. Offer the `schedule` domain too: its state is on/off
  like a binary sensor, and its `next_event` gives `next_transition`. The window logic is untouched.
- **A `timer_ends` attribute on the virtual light.** "When will it turn off" has no answer on the
  entity; the deadline is held in memory only. One timestamp attribute, null when no timer runs,
  gives a template sensor everything a dashboard card needs.
- **A device per entry.** A light's companions ("Hallway Auto-off", "Hallway Events", a remote's
  "Last Action") are tied to it by name alone, and the Events entity carries no attribute naming its
  light. One device per entry groups them as Home Assistant groups a bulb's own entities. Entity IDs
  and names stay as they are; the price is a device page per entry.

### 3. Coexist with Adaptive Lighting

Ships alone.

- **Problem.** Adaptive Lighting adjusts every light it controls every minute or two while it is
  on. MoLight reads an external brightness or color change as a dim at the wall and restarts the
  timer, so a light both control never turns off. Adaptive Lighting is among the most installed
  HACS integrations, and it is the answer this file gives to time-of-day brightness.
- **Design.** A per-light option, off by default: an adjustment to a light that is on does not
  restart the timer; a turn-on or off at the wall still counts. The docs say to leave the auto-on
  brightness and color blank, since Adaptive Lighting takes MoLight's values for manual control and
  stops adapting. Verify the failure with a test before designing.
- **Cost.** It changes what a change at the wall does, so it ships alone with its own tests.

### 4. Occupancy timeout check

- **Problem.** The README calls the occupancy timeout the anchor for everything downstream and says
  MoLight cannot read it. Set too high, a short visit is classified as a false detection and is
  turned off after the false-detection off delay; set wrong either way, turn-off timing drifts.
  Nothing tells the user.
- **Design.** The sensor keeps the shortest on-duration it has seen; a PIR's hold time is that floor.
  Once there are enough cycles to judge and the configured timeout is far from the floor, a Repairs
  issue names both. Diagnostic only.
- **Cost.** No state-machine change; a sensor test and a README line.

## Waiting for a request

Worth building if a user asks, not before.

- **A scene or script as the turn-on action.** Scenes are how Home Assistant sets a multi-light
  look, and the turn-on selection covers a `select` only. Auto-on brightness and color already cover
  a single room, and a scene lights the bulbs itself, so echo detection must learn to ignore it, the
  most delicate code here. Ships alone when it comes.
- **Auto-on brightness and color read from entities.** The option-source pattern of the turn-on
  selection, applied to brightness and color, for lux-aware or custom curves. Adaptive Lighting with
  item 3 covers time-of-day brightness without it.

## Already covered

Asks that existing blocks answer:

- Vacancy-only control (turn off after people leave, never turn on): a maintain occupancy sensor alone.
- Away or sleep gating: a source-backed Virtual Schedule Sensor, inverted, in a gate mode
  (EXAMPLES.md, example 5); to keep the time window too, join both in a Virtual Combined Schedule
  Sensor set to All.
- Only when dark and inside a schedule window, on a plain Virtual Light: a Virtual Combined Schedule
  Sensor set to All.
- Dim before turning off: the effect and warn stages.
- An always-on night light that brightens on motion: a Virtual Scheduled Light on an all-day
  schedule with standby.
- A TV or humidity hold: a Template or Threshold helper as a keep-on entity (EXAMPLES.md, example 14).
- A timed hold, "keep the lights on for two hours": a Timer helper read through a Template helper as
  a keep-on entity.
- Weekdays only: a workday sensor and a combined All schedule, until item 2 lands.
- One remote driving two rooms: two remote entries on different buttons of the same remote.
- Different levels per lamp from one trigger: two Virtual Lights sharing the sensors.
- A notification or spoken warning before an automatic off: an Event received trigger on the Events
  entity.

## Not planned

- Vacation presence simulation.
- YAML configuration.
- Adaptive or circadian lighting computed by MoLight itself: Adaptive Lighting does it, and item 3
  lets the two share a light.
- Controlling `switch` entities directly: Home Assistant's Switch as X helper wraps one as a light.
- Services to extend or hold the timer: a Timer helper does it, and "turn off and stay off" is item 1.
- Remote hold to dim, more remote actions, targets per binding: the remote binds clicks to light
  actions and no more; hold-to-dim doubles the per-ecosystem vocabulary re-checked on every Home
  Assistant bump, and the rest is one automation or a second remote entry.
- A re-trigger cooldown after an automatic off: the sensor's hold time means it has rarely cleared by
  the time the person reaches the door; the off-by-hand case is item 1.
- More than two settings sets on a Virtual Scheduled Light: two sets with combined schedules cover
  most asks, and Adaptive Lighting covers the rest.
- Setup presets and an area wizard: Discover, bulk assignment and EXAMPLES.md cover them.
- Translations: community work, see CONTRIBUTING.md.
