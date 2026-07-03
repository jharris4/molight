i'm wondering how i could improve this integration to allow specifying that certain lights should turn on / off at specific times.

For example, I current have lights that turn on at the later of sunset / 9pm, and turn off at the earliest of sunrise / 7am.





Good news: your data model is already 90% shaped for this — the VirtualScheduleSensor exists and the light already accepts a schedule_entity reference; what's missing is (a) sun-aware window edges and (b) deciding what the schedule does to a light. Here's how I'd design both, using your porch-light example as the target.

1. Sun-aware window edges on the schedule sensor
Right now a window edge is a fixed "HH:MM". Generalize each edge to a small spec with an optional sun anchor and a combinator:


time_windows:
  - start: { time: "21:00", sun: "sunset",  combine: "latest" }    # later of sunset / 9pm
    end:   { time: "07:00", sun: "sunrise", combine: "earliest" }  # earlier of sunrise / 7am
Plain fixed edges stay as they are ({time: "21:00"} with no sun key), so existing configs keep working. Evaluation uses HA's built-in astral helper (homeassistant.helpers.sun.get_astral_event_date) to get today's sunset/sunrise, then applies max()/min() against the fixed time. Two implementation notes that matter:

Overnight windows get date-sensitive. "Later of sunset/21:00 → earlier of sunrise/07:00" spans midnight, so "am I in the window now?" at 3am means checking yesterday's start against today's end. Your _in_window already handles the fixed-time overnight case; the sun version needs to resolve the events for the right calendar day before comparing.
This is the moment to drop the 1-minute polling. Once edges are computed datetimes, you can schedule one async_call_later/async_track_point_in_time for exactly the next transition instead of ticking every minute. That also gives the light crisp on/off at the boundary rather than up-to-59-seconds late, and it's the same time-mockable pattern we used for the light timer, so tests stay instant. (Handle the polar edge case — sun never sets/rises — by falling back to the fixed time alone.)
In the config flow, each edge becomes three fields: a TimeSelector, a SelectSelector for none/sunset/sunrise, and a SelectSelector for earliest/latest (only shown meaningfully when a sun anchor is chosen).

2. What the schedule does to a light: two distinct modes
Your example is not the gating behavior the schedule sensor was originally sketched for — it's schedule-driven lighting. I'd make the mode explicit per light, because both are legitimately useful:

gate (like illuminance today): occupancy may only turn lights on inside the window; the window ending behaves like illuminance going bright (turn off, go idle). Motion-lights-only-at-night wants this.
follow (your porch lights): window start → turn lights on; window end → turn lights off. No occupancy or timer involved — the light mirrors the schedule sensor. This needs a new machine state (say SCHEDULED: lights on, no countdown) so the timeout logic knows not to kill the lights at 9:05pm.
The subtle design decision in follow mode is manual override policy: if you turn the porch light off at 11pm, should the integration turn it back on? I'd say no — apply the schedule only at the boundaries (edge-triggered, not level-enforced): assert ON once at window start, OFF once at window end, and let manual changes stand in between. That composes cleanly with your existing external-off handling (light turned off externally → idle) and avoids the "light fights the user" failure mode. Restart behavior follows from the seeding pattern we already have: if HA boots mid-window and the lights are off, don't re-assert — you can't distinguish "restart lost the state" from "user turned them off," and respecting the user is the safer default. (If you find you want re-assert-on-boot, RestoreEntity can store "last boundary applied" to tell those apart.)

I'd disallow or ignore occupancy/illuminance on a follow-mode light initially — mixing "on all night" with "off when bright" and occupancy countdowns creates precedence questions you don't need to answer for the porch-light case. Gate mode is where those combine naturally.

Worth acknowledging: stock HA can do your example with two automations and a Schedule helper. The reason to build it into MoLight anyway is that your lights then have one owner — the same state machine handles schedule, occupancy, illuminance, and manual events without three automations racing each other, and the molight_state attribute tells you why a light is on.

If you want, I can implement it in that order — sun-aware edges + exact-transition scheduling on the sensor first (independently testable), then follow mode on the light.




i already did this with an automation, but it was tricky to make the automation handle edge cases when home assistant restarts, and it's annoying to edit the automation when i want it to control different lights (ex adding christmas lights in november, removing them in february).




Those two pain points map exactly onto what the integration can do better than an automation — and one of them changes a recommendation I made above.

The restart problem argues for re-asserting at boot, with memory. An automation misses its trigger if HA is down (or reloading) at 9:00pm, and there's no natural place to store "did I already handle tonight's boundary?" — that's why it was tricky. The virtual light can do this properly because it has state: store "last boundary applied" (e.g. "start of 2026-07-01's window") via RestoreEntity. Then boot-time seeding becomes unambiguous:

Inside the window, and the stored marker ≠ this window's start → the boundary happened while HA was down → apply it now (turn the lights on).
Inside the window, marker = this window's start → we already handled it before the restart; if the lights are off now it's because someone turned them off → leave them alone.
Outside the window with a stale marker → apply the off-boundary.
So you get missed-boundary recovery and manual overrides that survive restarts — the combination an automation can't express cleanly. This replaces my earlier "don't re-assert on boot" suggestion, which solved the wrong half of the problem.

The seasonal-lights problem is solved by the existing reusability design. The schedule sensor is a standalone entity, so you'd set up one "Night" schedule (later of sunset/9pm → earlier of sunrise/7am) and point multiple virtual lights at it. For the Christmas lights specifically, don't edit your main light's list in November — create a second virtual light ("Christmas Lights", follow mode, same schedule entity) and just disable/enable that config entry from the Integrations page seasonally. Two clicks in November, two in February, and your permanent lights' config is never touched. Editing the light list is also a normal options-flow edit now rather than digging through automation YAML, but the disable/enable route is cleaner for things that come and go on a calendar.

The implementation order I suggested still holds: sun-aware window edges with exact-transition scheduling on the sensor first, then follow mode with the boundary-marker restart logic on the light. Want me to build it?
