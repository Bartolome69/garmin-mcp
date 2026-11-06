# Personal zones: learned from the runner's own running

Status: plan, not built (5 Nov 2026).

## Why

Daash takes zones from Garmin's settings, which for most people are the
age-based default, and judges a run's effort from its name and Garmin's
heart-rate zones. Tools that feel smarter (RunZones, for one) work zones out
from the runner's own history, sort every run by them, and set plans from
them. Everything else here (time in zone, plan paces, fitness trend) depends
on the zones being right, so they come first, and they ship only after they
are checked against real histories.

## What it produces

For each runner, recomputed when a new hard effort or race arrives:

- **Threshold**: a pace (grade-adjusted where hills skew it) and a heart rate.
- **Five zones** in pace and heart rate, numbered like Garmin's so they read
  the same as the watch: 1 recovery, 2 easy, 3 steady, 4 threshold, 5 VO2 and faster.
- **The evidence**: which runs set it, in words ("your 10K on 12 Oct; 5 × 1 km
  at 4:00 on 5 Nov"), and how sure it is: high, medium or low.
- **Where Garmin disagrees**: Garmin's own zones beside them, and a line when
  they're far apart (for example heart-rate zones still set by age).

## How threshold is estimated

From the last 12 weeks of runs, strongest evidence first:

1. **Races** (race or parkrun in the name, or Garmin's race type): the time sets
   threshold pace by a standard race-equivalence table.
2. **Sustained steady efforts** of 20 to 40 minutes (contiguous laps or km
   splits at an even pace): their pace is close to threshold, and their heart
   rate after the first few minutes is threshold heart rate.
3. **Rep sessions** with short recoveries (work at least 4× the rest, reps of
   4 minutes or more): rep pace less a small margin.
4. **Garmin's lactate threshold**, where Garmin has one (password route only),
   as a cross-check rather than the answer.

Heart rate comes from the same efforts, steady state only. Max heart rate is the
highest plausible value seen in six months. With no strong evidence the zones are
**low** confidence: shown as provisional, with an offer to schedule a short test
(a 30-minute time trial or a parkrun) rather than guessed from age.

## Each run, by zone

Every lap and km split is placed in a zone by grade-adjusted pace, with heart
rate as the cross-check (heart rate decides where pace can't: trails, hills,
wind, a treadmill). Each run gets minutes per zone; each week gets totals: easy
share, threshold minutes, time above threshold.

## Where it shows

- **Tools** (no new tool, to keep the list within its size limit):
  `get_fitness` gains "your zones" with the evidence; `get_activity_details`
  gains minutes by zone; `get_plan` gains each week's zone minutes.
- **App**: a Zones section on Fitness; time in zone on a run; the week's
  easy and hard split.
- **Card and share image**: bar colours from the run's zones rather than the
  workout's name. That ends easy runs drawn as intervals and lets one rule
  drive every picture.
- **Plans**: `create_plan` sets paces from the zones when the runner gives
  none. When a benchmark moves the zones, Daash says so and **offers** to
  re-pace the sessions still to come; it never changes a plan unasked.

## Data and cost

- **Password route**: Garmin's details endpoint per run. To spare Garmin's rate
  limits, only runs likely to hold evidence (hard, long or named) are fetched,
  at most about 15 a pass, and each run's result is cached for good (a recorded
  run doesn't change).
- **Official route**: laps and km splits already kept per activity; no Garmin
  lactate threshold or fitness markers, so the evidence is the runs alone. From
  connection day only, so new official accounts start at low confidence.
- **Stored**: one small row per runner (threshold, zones, evidence, date),
  encrypted like the rest. New derived data, so the privacy page gets a line.

## Proving it before anyone sees it

1. Unit tests with fixtures: Bart's history, plus edge cases (no heart rate,
   treadmill, trail, walk-runs, cyclists, a runner with only easy runs).
2. An evaluation run on the server across connected runners, printing
   aggregates only (no names, no accounts): where Garmin has a lactate threshold,
   how far the estimate is from it (target: median within 8 s/km, anything over
   20 s/km examined); how many runners get high, medium and low confidence.
3. Bart's own zones checked by eye against his 5 × 1 km and recent races.

Nothing goes into tools or the app until the evaluation passes.

## Order

1. Engine and evaluation (`garmin_mcp/zones.py`), no user-facing change.
2. Tools: `get_fitness`, `get_activity_details`, `get_plan`.
3. App and share image.
4. Plans: paces from zones; the offer to re-pace after a benchmark.
5. Later: pace at a fixed heart rate week by week (a fitness trend that works
   on the official route), and a short written review in the Monday email.

## Open questions (Bart)

1. Five zones numbered like Garmin's, or six like RunZones? Plan says five.
2. Offer to write the zones back to Garmin so the watch's alerts match?
   Needs checking that Garmin allows it on each route; opt-in only.
3. A new stored row and a privacy page line: OK?
4. The evaluation across connected runners' data, aggregate only, run on the
   server: OK?
