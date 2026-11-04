# Coaching: spec

Draft, November 2026. Spec only, nothing built.

## Summary

A runner can give a coach access to their Daash. The coach sees all their runners on one list, sorted by who needs attention, and sets plans with the plan engine (docs/plan-engine-spec.md), with Claude or ChatGPT, or by hand. Sessions reach the runner's watch through the runner's own Garmin connection; the coach never touches the runner's Garmin login.

- **The runner decides.** They choose what the coach sees and whether the coach can change their plan, see who did what, and can remove the coach in one tap.
- **Not a marketplace.** Daash doesn't find coaches for runners; coaches bring their own. PRODUCT.md's "No coaching marketplace" still holds.
- **Why.** Each coach brings 10 to 40 runners, and every one connects Garmin to Daash. It's the cheapest growth Daash can get.
- **Three phases.** A: the coach sees (no new plan storage needed). B: the coach sets plans (needs PRODUCT.md's plan store). C: templates for groups and clubs.
- **First:** Garmin's written confirmation (section 7).

## 1. Who it's for

- **Independent coaches** with 5 to 40 runners, today on spreadsheets, WhatsApp, TrainingPeaks or Final Surge.
- **Run club coaches** setting one block for a group, each runner at their own paces.
- **A friend coaching a friend**: the clubmate who's run ten marathons.

The pitch: "Tell Claude what you want for Alex, check it, and it's on their watch tonight." The incumbents are calendars a coach types into.

## 2. Linking and permissions

**Linking.** The coach sends a link or code (crews' code alphabet, `crews._new_code`), or the runner enters the coach's code in the app. Before accepting, the runner sees exactly what the coach will get, the way `join_crew` spells out what a crew sees, and sets each switch:

| Permission | The coach gets | Default |
| --- | --- | --- |
| Plan | the plan and its sessions: done, missed, moved; planned and actual km | on, required |
| Runs | run analysis: laps, reps hit or soft, heart rate; session notes | on |
| Health | readiness, sleep, HRV, resting heart rate | off |
| Set plan | create, change and remove sessions on the runner's Garmin calendar | off until the coach asks |

The consent screen also says the coach may use Claude or ChatGPT with what's shared.

- **One coach per runner** in v1. A coach has any number of runners.
- **Removing.** One tap in You, either side. Access stops at once. Sessions the coach set stay on the runner's calendar: they're the runner's.
- **History.** The coach sees from 8 weeks before the link, enough for the plan engine's inputs, and nothing older.
- **Activity log.** The runner sees what the coach did: "Jo looked at your runs, 3 Nov", "Jo changed Thursday's session".
- **Crew and coach are separate.** A crew sees days and distances only. A coach on the runner's crew gets nothing extra through it.

**Store** (small tables in `store.py`, keyed by `crews.member_key` so a fresh sign-in keeps the link):

```
coaches       (coach_id, name, email_hash, garmin_key NULL, created_at)
coach_links   (coach_id, runner_key, scopes, invited_at, accepted_at, removed_at)
coach_log     (runner_key, coach_id, action, target, at)
session_notes (runner_key, day, author, text, at)
```

## 3. Coach accounts

Coaches sign in with email (`email_codes` already exists). Garmin is optional. A coach who runs links their own Garmin and is a runner too, with their own plan.

## 4. The coach's view

Web app first (PRODUCT.md), in the same app with a "Coaching" tab.

**Runners list.** One row per runner: name, this week done of planned (3 of 5, 28 of 40 km), next race with its tier and weeks to go, and flags. Runners with flags come first. The flags are rules over what Daash already computes:

- a missed key session (`KEY_SESSION_TYPES`, status missed)
- a run that felt hard twice running (plan engine section 7)
- readiness under `LOW_READINESS` for 3+ days (Health only)
- checker warnings on the plan (plan engine section 8)
- no run in 5 days
- the plan ends within 2 weeks with nothing after it
- a race this week

**Runner page.** The screens the runner sees (Plan, Today, session, run), read through the runner's grant, under a "Alex's Daash" header, with the coach's actions added.

**Monday email** (alongside `weekly.py`): "3 runners need a look", with their flags.

## 5. Setting plans (phase B)

**Three ways in:**

- **The builder, for the runner.** The plan engine's six steps, filled in from the runner's Garmin. The coach can change any step, style included, and go past the builder's limits; the checker then warns.
- **Claude or ChatGPT.** The coach's connector gets an `athlete` argument on `get_plan`, `create_plan`, `check_plan`, `get_activity_details`, `get_session`, `update_workout`, `schedule_workout` and `unschedule_workout`, and a new `get_athletes` tool with the runners list and flags. "Build Alex a half block, 4 days, Faster style" works as it does for the coach's own plan.
- **Week to week:** move, make easier, swap, add, remove.

**Writes** go through the runner's official link and the Training API (`official_workouts`, `official_schedules`), so the session is on the runner's watch at its next sync. Each write goes in `coach_log`. The plan and each session show "Set by Jo".

**Checks** on a coach-set plan go to the coach, who can dismiss them (recorded). The runner doesn't see amber warnings on their coach's plan (open question 3).

**Locks.** The coach can lock a session. The runner's Move or Easier on a locked session says "Ask Jo first" and sends Jo a note. Unlocked sessions move freely, and the coach is told.

**The runner's own Claude.** The calendar is the runner's, so their own `create_plan` and `update_workout` still work on a coached plan. Tool answers say the plan is Jo's and suggest asking first; the coach is told of any change.

**Notes.** One short thread per session ("legs heavy", "ease off tomorrow"). Email at first; push with the native app.

## 6. Templates and groups (phase C)

- **Save a block** as a template: sessions placed by week before race day and weekday, with paces as kinds (easy, marathon pace, threshold, intervals), not numbers.
- **Apply it** to several runners. For each, the engine fills in their paces (plan engine section 6), caps the long run to their history, moves sessions to their long-run day and shows their checks. One tap writes them all.
- **Change it later.** Offered to everyone whose copy hasn't been changed by hand, from today forward.
- **Clubs.** A crew with a coach: the club run stays, and the coach's template is the club's block.

## 7. Garmin's agreement

Read from the Garmin Connect Developer Program Agreement Garmin publishes (Rev. B, file dated March 2026). That copy names Garmin China as the Garmin party, so Daash's signed copy may differ. Not legal advice.

- **4.1(b).** Data may be used to display it in Daash's own apps, "only as allowed by law and in compliance with each End User's consent". A coach seeing a runner's data inside Daash, with that runner's consent, looks within this.
- **5.2(o).** No making Garmin data available to third-party apps or services Garmin hasn't approved in writing. So no export to TrainingPeaks and no coach API: the coach works inside Daash.
- **5.2(e).** No deriving income from use of the API without Garmin's prior written permission. This covers charging for Daash at all, coaches included.
- **6.4.** Garmin device data shown to third parties needs attribution per Garmin's API brand guidelines. The coach view carries it.
- **15.10.** A privacy policy AI transparency statement when user data is processed by AI. Daash needs it anyway; the coach's Claude reading a runner's data must be in it.

**Before phase A:** email Garmin's developer program describing the coach view, ask them to confirm it in writing, and ask about 5.2(e) for billing.

## 8. Privacy and safety

- Health data is special category data under UK GDPR: explicit consent for each permission, Health off by default, the activity log, removal in one tap.
- Removing a coach deletes what Daash keeps for them about that runner except the log, which stays with the runner. Deleting the runner's account (`delete_user`) deletes the links, log and notes.
- No runners under 18 (plan engine section 10). Junior coaching is out of scope.
- The plan engine's safety copy shows on coach-set plans too, and a runner's "something hurts now" (if they set it) is a flag for their coach, never a plan.

## 9. Growth and measures

**The loop:** a coach invites runners, each connects Garmin, each sees the app and their crew, and their friends see Daash.

**Pricing** (after 5.2(e) is settled): coaches free in phases A and B to win them, runners on Daash's normal plan; per-runner pricing for coaches above a few runners, and club deals, decided with real numbers.

**Measures** (PostHog, no names or emails): `coach_invited`, `coach_linked`, `coach_viewed`, `coach_plan_set`, `coach_removed`. Watch coaches with 3+ active runners, runners added per coach, and whether coached runners stay longer than others.

## 10. Phases

- **A: the coach sees.** Coach accounts, links and permissions, the runners list with flags, read-only runner pages, notes, the Monday email. Built on what `get_plan` and the app read today, plus the small tables in section 2. Pilot with 2 or 3 coaches, once Garmin confirms.
- **B: the coach sets plans.** Needs PRODUCT.md Phase 1's plan store (who set what, locks) and Training API writes. The builder for coaches, the connector's `athlete` argument, locks, change notices.
- **C: templates and clubs.** Needs Phase 3's accounts and billing if coaches pay.

## 11. Out of scope

Finding a coach; payments between coach and runner; chat beyond session notes; more than one coach per runner; coaches seeing crew data; under 18s; the plan builder for other sports (a coach can still schedule anything `create_workout` takes).

## 12. Open questions

1. **Garmin.** Will they confirm the coach view in writing, and allow charging (5.2(e))?
2. **Several coaches.** Clubs have a head coach and assistants sharing runners. Is one coach per runner enough for the pilot?
3. **Checks on coach-set plans.** Should the runner see them too, or only the coach?
4. **The runner's own changes.** Notify only, as above, or block locked sessions from the runner's own Claude too?
5. **Plan store timing.** Phase A works without it. Does phase B wait for PRODUCT.md Phase 1, or bring it forward?
6. **History.** Is 8 weeks before the link right, or should the runner choose?
7. **Price.** Free for coaches long-term, or per runner?
