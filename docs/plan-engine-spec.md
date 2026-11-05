# Plan engine: spec

Draft, November 2026. Built so far: the validator (section 8), in `garmin_mcp/plan_checks.py`, for preview accounts; `scripts/check_plan.py` runs it over a saved plan.

## Summary

A small rule-based engine inside Daash that builds running plans without AI and checks plans that Claude or ChatGPT wrote. No model calls: the same inputs always give the same plan and the same warnings.

- **Builder.** Six short steps in the app (section 1) turn what Garmin knows, someone's races, days, volume, injuries and style into the `sessions` list `create_plan` already takes. From there it's an ordinary plan: `training_plan.prepare` and `workouts.build_workout` build it, it lives on the Garmin calendar, and the app, cards and watch need no new data model.
- **Styles.** One engine and one set of paces, with the hard running spread three ways: Steady, Balanced and Faster (section 4), each borrowing a known method's ideas.
- **Several races.** One A race per block, with B races that get a short taper and C races run as training (section 5).
- **Validator.** The same rules over a proposed or existing plan give plain warnings ("The long run jumps from 14 to 20 km in week 6"). It never blocks.
- **v1:** running only; 5K, 10K, half, marathon and general fitness; 6 to 20 weeks; 3 to 6 days a week.

The numbers are Daash's own conservative defaults, not taken from any one study. A qualified running coach should review them before launch.

## 1. Building a plan in the app

Each step is pre-filled where Garmin knows the answer, so most are one tap. Claude can call the same engine with the same answers, as a fallback.

1. **What Garmin knows.** The last 4 to 8 weeks' km a week, the longest recent run, recent races, Garmin's race predictions and the level Daash infers (section 2). "That's not right" edits any of it.
2. **Your races.** Every race on the Garmin calendar in the next 26 weeks (`upcoming_events`, `RACE_WEEKS_AHEAD`), with Garmin's primary event set as the A race. A tap sets each to A, B or C (section 5), and more can be added. No race: "Get faster at a distance", "Build a base" or "General fitness". A goal time is optional, and Daash says how it compares with current fitness: realistic (within 3% of the prediction), a stretch (3 to 8% quicker) or very ambitious (more than 8%).
3. **Days.** How many (3 to 6), days that are out, and the long-run day.
4. **How much.** Three options worked out from step 1 and the progression rules, never a number typed cold: "Keep around 30 km", "Build steadily to about 45 km" (the default) and "Build more, to about 55 km". Each peak is what section 3's rules reach in the weeks available. "Build more" isn't offered to new runners or after a niggle.
5. **Injuries.** None, a niggle now, coming back from injury, or something hurts now.
   - **Niggle or comeback:** weekly increase up to 5% or 2 km, a recovery week every 3rd week, strides only for the first 3 weeks (no intervals or sub-threshold), Steady style for those weeks, and a note that any easy run can be swapped for the same time cross-training.
   - **Something hurts now:** no plan, and the physio pointer from section 10.
   - The plan records "gentle start", never the injury.
6. **Style.** Steady, Balanced, Faster or Pick for me (section 4).

Then a preview: weekly distance as bars with the phases and races marked, each week's key session, and one line of reasoning per choice ("Long run tops out at 20 km: enough for a half, in 2 km steps from your 12 km"). "Put it on my Garmin" calls `create_plan`. Nothing is written before that tap. The safety copy (section 10) shows before step 1.

## 2. Inputs

| Input | Source |
| --- | --- |
| Races, each A, B or C | the Garmin calendar (`upcoming_events`, with `primary` from Garmin's `primaryEvent`), plus any the user adds. No race for general fitness. |
| Finish or target time | user, for the A race; optional for B races |
| Days a week (3 to 6), days out, long-run day | user; Sunday by default |
| Weekly volume, longest recent run | last 4 weeks from `weekly_run_km` (`run_km`, `runs`) and `get_activities`, else user-entered |
| Target volume | step 4's choice |
| Paces | a race in the last 12 weeks (user, or a race on the calendar with a recorded run), else `get_fitness`: `race_predictions` (`5k`, `10k`, `half_marathon`, `marathon`, each `time` and `pace_per_km`) and `lactate_threshold.pace_per_km` |
| Experience | inferred, shown in step 1 to correct: new (under 6 months of regular running, or a first race at the distance), regular, experienced (2+ years of regular running and 40+ km weeks) |
| Injuries | step 5 |
| Style | step 6 |
| 18 or over | Garmin profile birth date where there is one, else asked |

Plan length runs from this week's Monday (`plan.monday_of`) to the A race. Under 6 weeks: no plan, and a suggestion to run easy and enjoy the race. Over 20: the plan starts 20 weeks out, with a general fitness block offered before it.

If someone is a long way from what the goal usually needs (say a marathon from under 20 km a week), the engine still builds the safest plan the rules allow and says plainly that the long run will top out short of the usual distance.

## 3. Structure and progression

**Phases**, written to each session's `phase`, which `phase_by_week` turns into the bands the card and app draw:

| Goal | Taper | Remaining weeks |
| --- | --- | --- |
| 5K, 10K | 1 week | base 40%, build 40%, peak 20% |
| Half | 10 days | base 40%, build 35%, peak 25% |
| Marathon | 2 weeks (3 for 16+ week plans) | base 35%, build 40%, peak 25% |
| General fitness | none | base then build, repeating; optional 5K time trial at the end |

Short plans lose base first, then build. Taper is never cut.

**Recovery weeks** every 4th week (every 3rd for new runners and after a niggle): 75% of the week before, long run about a quarter shorter, no intervals. The week after goes back to the last full week, not above it.

**Session types**, named so `session_type` already reads them right: "Easy 8k" (easy), "Easy 6k + strides" (easy, via `GENTLE_NAME_RE`), "Long 18k" or "Long 26k w 10k MP" (long), "Tempo 3x8min" or "Threshold 6x6min" (threshold), "Intervals 5x1k" (intervals), "Race: Half marathon" (race). Rest is a day with no session, as `create_plan` already treats it.

**Per week** for Balanced, long run always included (section 4 says how Steady and Faster differ):

| Days | New | Regular | Experienced |
| --- | --- | --- | --- |
| 3 | long, 2 easy (one with strides) | long, 1 quality, easy | long, 1 quality, easy |
| 4 | long, 3 easy; 1 quality from build | long, 1 quality, 2 easy | long, 1 quality, 2 easy |
| 5 | as 4, plus easy | long, 1 quality (2 in build and peak), 2-3 easy | long, 2 quality, 2 easy |
| 6 | capped at 5 | long, 2 quality, 3 easy | long, 2 quality, 3 easy |

Quality by phase: base is strides and short tempo (strides only for new runners); build adds threshold, and intervals for 5K and 10K; peak is race-specific (5K-pace reps, marathon-pace sections in the long run); taper keeps one short race-pace session.

**Progression.** The builder works inside the middle column. The validator warns only past the looser right column, so a plan following a known method (such as `skills/half-marathon-pfitzinger`) isn't nagged for small differences.

| Rule | Builder | Validator warns at |
| --- | --- | --- |
| Starting week | at most current weekly volume | over 120% of the last 4 weeks' highest |
| Weekly increase | up to 10% or 3 km, whichever is more, over the last full week (5% or 2 km after a niggle) | over 20% or 5 km |
| Long-run share | at most 30% of the week (35% at 3 days) | over 40% |
| Long-run growth | up to 2 km or 15 min a week, from the longest recent run | over 3 km or 20 min |
| Long-run cap | 5K 12 km, 10K 16 km, half 20 km, marathon 32 km; never over 2 h 30 (3 h marathon) | over the cap |
| Quality sessions | by style (section 4); a long run with a race-pace section counts | by style |
| Hard volume | by style | by style |
| Hard days | an easy or rest day between tempo, intervals, long and race (Faster: an easy long run isn't hard) | two in a row |
| Rest | at least 1 day a week | none in 7 days |
| Recovery | as above | no week under `CUTBACK_SHARE` (85%) of both neighbours in 5 weeks |
| Taper | marathon 75%, 60%, 40% of peak; half 70%, 50%; 5K and 10K 60%. Intensity kept, sessions shorter | race week over 70% of peak |

Why:

- **Start from now.** A plan begins from what the body is used to, not from the goal.
- **Small weekly steps.** Fitness comes faster than tendons and bones adapt. The 3 km floor stops a 10 km week creeping up 1 km at a time.
- **Long-run share and cap.** When one run is most of the week its fatigue isn't spread out. Past the cap, extra time on feet adds more tiredness than fitness, more so for slower runners, hence the time limit.
- **Few hard days, never back to back.** Most of the gain comes from the easy running around hard days, and a hard day works when you arrive fresh.
- **Recovery weeks and taper.** They let the work settle in, so race day starts rested but still sharp.

## 4. Training styles

One engine and one set of paces (section 6). The style only changes how the hard running is spread. Each borrows a known method's ideas and says so on the page, so keen runners know what they're getting. No plan tables are copied from the books these methods come from. The plan's `method` names the style: "Daash plan, Balanced (Daniels style)".

| Style | Hard running | Based on | Offered to |
| --- | --- | --- | --- |
| **Steady** | at most 1 quality session a week, from build; the rest easy, and volume does the work | 80/20, Lydiard-style base building | everyone; the default for new runners and after a niggle |
| **Balanced** | section 3's per-week table: threshold, intervals or race pace, changing by phase | Jack Daniels | regular and experienced |
| **Faster** | 2 sub-threshold sessions a week at 4 to 5 days, 3 at 6; everything else very easy, the long run included; no flat-out intervals | Norwegian singles | regular and experienced, 4+ days, with a lactate threshold heart rate from `get_fitness` |

**Faster sessions** are reps of 3, 6 or 10 minutes with 1 minute easy between ("Threshold 10x3min", "Threshold 6x6min", "Threshold 3x10min"). Each has an `hr` target in bpm just under threshold heart rate, which `create_workout` already takes, rather than a pace, so a hot or tired day simply runs slower. In peak, half and marathon plans swap one of them for a race-pace session.

**Style limits:**

| | Steady | Balanced | Faster |
| --- | --- | --- | --- |
| Quality sessions (builder / warns at) | 0 to 1 / 2+ | 0 to 2 by level / 3+ | 2 to 3 / 4+ |
| Hard volume, work reps share of the week (builder / warns over) | 15% / 20% | 20% / 25% | 25% / 30% |

The validator reads the style from the plan's "Built:" line (section 9), or Faster when `method` names Norwegian or singles. Otherwise it uses Balanced limits.

**Pick for me** gives one style and one line of reasoning:

- New, or a niggle or comeback: Steady.
- More than half of the last 8 weeks' running time in zone 3 or above: Steady. "Most of your runs are harder than easy. More easy running is the quickest win."
- Already running threshold or sub-threshold sessions (by `session_type`) and mostly easy otherwise, 4+ days, threshold heart rate known: Faster.
- Anyone else: Balanced.

A high-mileage style (Pfitzinger's midweek medium-long runs, for marathoners at 70+ km a week) can come later. Canova's method rests on a coach's judgement for each athlete and is aimed at elites, so it isn't a fit for rules.

## 5. Several races

Each race is A, B or C. They map to the tiers plan cards already draw (`RACE_TIERS`): A is `goal`, B is `key`, C is `tune`.

- **A: the goal.** The plan peaks and tapers for it. One per block.
- **B: matters.** A short taper, raced properly.
- **C: for fun.** No taper, run as training.

**Two A races** need room between them: 8 to 12 weeks when either is a marathon, 4 to 6 weeks for a half or shorter. Closer than that, the builder suggests making the earlier one a B. With room, they become two blocks back to back: the first to its race and taper, then recovery (a week after a half or shorter, two after a marathon), then a second block with base cut first. Each block is its own plan code, so the app's season view (`_season_weeks`) shows them in order.

**B races:**

- That week is about 25% lighter. The 3 days before are easy only, so a quality session within 2 days of the race is dropped.
- The race is that week's quality session. If it's at least as long as the planned long run it is the long run; otherwise there's no long run within 2 days after it.
- Easy days after: 2 after a 5K or 10K, 5 after a half, a recovery week after a marathon.
- Only in build and peak. A B race in base is treated as a C, and one in the last 10 to 14 days before the A race is a C at most, with a note.
- A B race as long as the A race needs at least 6 weeks before it; otherwise it's offered as a C at long-run effort.
- At most one B race every 3 weeks. Extras become C with a note: "Racing every fortnight leaves little room for training, so the 12 April 10K is a fun run. Change it if it matters more."
- **Each B result resets paces.** A result that moves them more than 3% is already a recalculation trigger (section 7). With B races in the plan, the target-time note says "we'll check again at the Cambridge Half" rather than "in week 6".

**C races** (parkruns, club races) replace that week's tempo, run at tempo effort, or an easy run, run easy. Nothing else in the plan changes.

**Races after the A race** aren't in this block. The preview lists them with "Plan the next block after race day".

## 6. Paces

Paces come from current fitness, never the goal alone.

1. **Recent race**, converted to other distances with the Riegel formula (T2 = T1 × (D2 / D1)^1.06).
2. **Garmin's predictions** and lactate threshold. The marathon prediction is used only for runners already at 40 km a week or more; otherwise marathon pace comes from the half prediction with Riegel, which is slower and safer.
3. **No data.** Sessions are set by time, with effort and Garmin's own heart-rate zones ("z2", which `create_workout` accepts and `_in_zones` resolves). Weekly volume is then in minutes, which `get_plan` already reports (`measure`: "time").

| Pace | From | Target on the watch | No data |
| --- | --- | --- | --- |
| Easy, long | marathon pace + 45-75 s/km (`create_workout`'s own rule) | none; pace in the description | "Easy, you could chat", or z2 |
| Marathon pace | marathon prediction | ±15 s/km | left out |
| Threshold | `lactate_threshold.pace_per_km`, else between 10K and half pace | ±10 s/km | "Comfortably hard", z4 |
| Sub-threshold (Faster) | lactate threshold heart rate | `hr` just under it | not offered |
| Intervals | 5K pace | ±5 s/km | "Hard but controlled" |
| Strides | by feel | none | by feel |

The windows follow `create_workout` and `PACE_WINDOW_SECONDS`, so the watch doesn't beep at GPS wobble.

**Target time.** Race-pace work uses the goal pace only within 3% of the predicted pace. Otherwise it uses the predicted pace, with a note: "Your goal of 1:40 is quicker than your current fitness suggests (about 1:46). The plan trains you from where you are, and we'll check again in week 6" (or at the next B race).

## 7. Adaptation

State comes from `get_plan`: each session's `status` (done, done moved, missed, skipped, today, ahead), `planned_km` and `actual_km`.

**Never catch up.** Missed volume is gone. It's never added to later sessions or weeks, and a missed long run is never doubled.

- **Missed easy run:** nothing changes.
- **Missed key session** (`KEY_SESSION_TYPES`): if a later day this week is free and keeps a day between hard sessions, offer Move; otherwise drop it.
- **Moved** (the app's Move, or Claude): the validator rechecks the week. If two hard days now touch: "This puts the tempo the day after your long run. Fine once, better with a day between." It still moves if they say so.
- **Felt hard:** the run's `analysis.analyse` verdict has the reps soft, or Garmin's perceived effort is high (open question 1). Once: offer the next quality session as Easier (the app's `easier()`: reps a third shorter, same pace). Twice running, or with readiness under `LOW_READINESS` (50): offer next week as a recovery week.
- **Mark done:** not built and not needed. Garmin decides done from a recorded run (APP.md), and `if_done_elsewhere` covers runs off the watch.

**Recalculate** only with a yes, and only from today forward, offered when: under half the sessions were done in 14 days; 7+ days without a run; the race date, days a week or long-run day changes; a race is added or its tier changes; or new race data, a B race above all, moves paces by more than 3%. A rebuild starts from the last 2 weeks actually run, not from the plan. After 7 to 13 days off: a week at 70% of the last full week with no quality. After 14+: back to base. If the race is now too close, build is cut before taper.

## 8. Validator mode

- **Where.** In `create_plan` after `training_plan.prepare`, and in `get_plan` over the weeks it returns; both answers gain a `checks` list. A new read-only `check_plan` tool takes `create_plan`'s arguments and returns only `checks`, so Claude can check a draft before anything is written.
- **Reads.** Dates, `name` (via `session_type`), `phase`, distance and time (`session_totals`, `planned_distance_m`), work steps, `method` and the "Built:" line for style, races and their tiers (`races`), and `weekly_run_km` for history. Non-running sessions only count as "not rest".
- **Race checks:** a hard session within 2 days after a race; B races on back-to-back weekends; a race other than the A race in its last 10 days; a B race longer than that block's long run with no reshaped week; two A races closer than section 5 allows.
- **Shape.** `{"rule": "long_run_jump", "week": 6, "date": "2026-12-13", "text": "The long run goes from 14 km to 20 km in week 6. Steps of 2 km a week are gentler on the legs."}`
- **Never blocks.** `create_plan` writes the plan whatever the checks say. Its tool description asks Claude to pass the checks on in its own words and change the plan only if the person wants to. The app shows a quiet line under the week, in the amber `analysis.py` uses for "soft", never red.
- **Tone.** "Worth a look", not "error". "A big jump", never "injury risk".

## 9. Output and Garmin mapping

The engine returns exactly `create_plan`'s arguments: `goal` ("Half marathon, 1:45, 15 March"), `label` (via `normalise_label`, e.g. "HM"), `name` ("Half build"), `method` ("Daash plan, Faster (Norwegian singles style)"), `milestones` (one per race, `{"label": "Cambridge Half", "date": ..., "target": "1:45", "tier": "goal"}`, B races `key`, C races `tune`) and `sessions`, each with `date`, `name`, `phase`, `description` (paces in words, "Recovery week" where it is one) and `steps` in `create_workout`'s format:

```json
{"date": "2026-12-10", "name": "Tempo 3x8min", "phase": "Build",
 "description": "Warm up and cool down easy, 5:50-6:20 /km.",
 "steps": [
   {"type": "warmup", "duration_seconds": 900},
   {"type": "repeat", "times": 3, "steps": [
     {"type": "interval", "duration_seconds": 480, "pace": ["4:50", "5:10"]},
     {"type": "recovery", "duration_seconds": 120}]},
   {"type": "cooldown", "duration_seconds": 600}]}
```

`_Builder` turns these into Garmin's `ExecutableStep` and `RepeatGroup`, with `pace.zone` or `heart.rate.zone` targets in `targetValueOne`/`targetValueTwo`.

- **Easy, long:** one `interval` step by `distance_meters`, no target (by `duration_seconds` at "z2" with no pace data). A marathon-pace section is a second step with a pace target.
- **Strides:** the easy step, then `repeat` 6 × [`interval` 20 s, `recovery` `until_lap`].
- **Intervals:** `warmup`, `repeat` × [`interval` by distance, `recovery` by time], `cooldown`.
- **Sub-threshold:** `warmup`, `repeat` × [`interval` by time with `hr` [low, high], `recovery` 60 s], `cooldown`.
- **Race:** one `interval` step at the race distance, no target.

Read back, these are the same `get_plan` sessions (`type`, `phase`, `planned_km`, `status`, `workout_id`, `schedule_id`) and the same `session_card`, `workout_steps` and `session_totals` the app draws today. 20 weeks at 6 days is 120 sessions, inside `MAX_SESSIONS` (150) and `MAX_WEEKS_AHEAD` (30). The engine's inputs ride along as one more line in `describe` ("Built: 4 days, long run Sun, regular, Balanced, build steadily, gentle start"), so a recalculation can read them back and Daash still stores nothing about the plan.

**Gap:** `MAX_MILESTONES` is 4, and someone with 8 races in a block needs more (open question 8).

## 10. Safety copy

Shown before a plan is built and in the plan's notes:

- "This is general guidance for healthy adults, not medical advice."
- "If you're injured, pregnant, or have a heart or other health condition, check with a doctor or physio before starting, and think about a qualified coach. Daash can't take those into account." Something that hurts now means no plan, and a pointer to a physio.
- "If something hurts in a way that changes how you run, stop and rest. See a doctor or physio if it doesn't settle."
- Under 18: no plan. "Daash plans are for adults. A club or coach can set training for younger runners."
- "Sessions sync to your Garmin watch. Daash is independent and not made or endorsed by Garmin."

## 11. Out of scope for v1

Other sports and triathlon; strength sessions in plans; ultra, trail and hills; run-walk plans from zero; 7 days a week and doubles; a high-mileage style; heat and altitude; daily readiness changes beyond "felt hard"; recalculating without asking; miles.

## 12. Open questions

1. **Felt hard.** Can the official API read Garmin's perceived effort on an activity, or does the app need a "Felt hard" tap? If a tap, where does it live, given the app stores nothing about the plan? `update_activity` is one option.
2. **Replace from a date.** `create_plan` refuses a plan code already on the calendar. Recalculating needs a way to replace future sessions under the same code.
3. **Plan store.** When PRODUCT.md's Phase 1 store lands, do inputs, race tiers and checks move there from the descriptions?
4. **Where it's built.** In the web app (the MVP is web app first), through Claude as a fallback, or both? Behind `preview.py` first?
5. **Tuning.** Run the validator over existing AI-written plans before launch to see how often it fires.
6. **Review.** Who reviews the numbers, and do we name them on the page?
7. **Level.** Inferred and shown to correct (section 2). Are the thresholds right, and is "experienced" worth separating from "regular" at all?
8. **More races.** Raise `MAX_MILESTONES` (to 12, say, and check the card's race timeline still reads at that count), or keep milestones for A and B races and mark C races only on their sessions?
9. **Zone history.** Pick for me needs time in heart-rate zones over 8 weeks of runs. Does `get_activities` give it on the official API, and how many calls does it cost?
10. **Riegel or VDOT.** Riegel is simple and already in use. Daniels' VDOT formula fits the Balanced style better and gives the same pace for every distance. Worth switching?
