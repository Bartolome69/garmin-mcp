# The Daash companion app

The calendar is the home, the watch is the output, and the AI is a door. The
app is the daily place to see, check and lightly adjust the plan that Claude
or ChatGPT built. It has no chatbot and makes no model calls: "Ask Claude"
opens Claude with the context filled in.

Designs: `design_handoff_daash_mvp_v2` (pass 2, with the desktop screens
3a to 3c). This file says how the app plugs into what the server already does.

## One plan, three surfaces

There is no app copy of the plan. Today the plan's source of truth is the same
one every MCP tool reads: the workouts and schedule on the person's Garmin
calendar, read through `get_plan` and `get_session` (see PRODUCT.md, "Plans
are not stored by Daash"). The app reads those same functions and writes
through the same tools Claude uses, so a change from either door is what the
other reads next, and the watch gets it at its next sync.

| App action | What it calls | What Claude sees next |
| --- | --- | --- |
| Today, Session preview | `get_plan`, `get_session`, `get_readiness` | the same answer |
| Session analysis | `get_activity_details`'s inputs (laps, recording) and the workout | the same run |
| Move | `unschedule_workout` then `schedule_workout` (the plan's own `how_to_adapt`) | the session on its new day |
| Easier | a copy of the workout for that day only, each rep a third shorter, same pace, swapped in with `unschedule` and `schedule` | the easier session on that day, every other week unchanged |
| Undo easier | the original workout scheduled back, the copy deleted | the original session |
| Send again | `unschedule` then `schedule` on the same day | unchanged plan |
| Mark done | not built: Garmin decides done from a recorded run | |

Why a copy for Easier: `create_plan` schedules one workout on every date it
repeats, so editing it in place would make every week easier. The copy keeps
the plan code in its name, so the plan still counts it, and carries a line
naming the original ("Made easier in Daash from workout 555002."), so Undo
needs nothing stored by the app.

When PRODUCT.md's Phase 1 plan store lands, it replaces the Garmin calendar
under all three doors at once. The app does not need to change, because it
only knows the API below.

## Where it lives

- `garmin_mcp/app.py`: the app's routes on the hosted server.
  - `GET /app`, `/app/*`: the web app, static files from `garmin_mcp/app/`.
  - `GET /app/api/today`, `/app/api/session/{date}`, `/app/api/run/{activity_id}`,
    `/app/api/plan?week=`, `/app/api/crew`, `/app/api/you`:
    JSON shaped for the screens, built from the tool functions.
  - `POST /app/api/session/{date}/move|easier|undo|resend`: the edits above.
  - `POST /app/api/you/times`: your usual run times, as the crew uses them.
  - `POST /app/api/you/delete`: delete the account, the same as `/disconnect`.
- `garmin_mcp/analysis.py`: planned against run for one session, worked out
  from the raw laps and the workout's steps. No model, so the same run always
  gets the same verdict, and every number comes from the same laps so they add up.
- `garmin_mcp/app/`: the web app. No build step, like the in-chat cards in
  `garmin_mcp/ui/`: plain ES modules, one stylesheet, Geist bundled locally,
  a manifest and a service worker so it installs to the home screen.

## Sign-in

There is one Daash account per person, and every door signs in to it. Three
ways in, the first two without Garmin:

1. **Open in Daash from a chat card.** The session card's link carries a
   single-use code (30 minutes, stored as a hash). Tapping it opens the app
   already signed in as the person who was talking to Claude: nothing to type.
2. **An email code.** "Already use Daash? Your email" sends a 6-digit code to
   the address the account is under, using the same codes, limits and mail as
   the sign-up step (`store.new_email_code`, `support_mail.send_code`). The
   answer is the same whether or not the address has an account.
3. **Sign in with Garmin**, for someone new: the server's own OAuth 2.1 with
   PKCE, through Daash's Garmin page. The app is a client of its own,
   `daash-app` (`oauth.APP_CLIENT_ID`), registered at start-up. Registration
   hands out random ids, so nobody else can be it, and its sign-ins back to
   its own callback skip the consent page. Any other client pointing at the
   same callback still gets consent.

All three issue the same tokens as Claude's connector, against the same
account (`_finish` keeps one identity per email), and a refresh token lasts a
year, so a phone signs in once. Existing connections in Claude and ChatGPT
are untouched.

`SessionBinding` turns a bearer token into that person's Garmin session, crew
membership and official-API context for `/mcp`; it does the same for
`/app/api/*`, so every tool function runs exactly as it does for Claude. The
sign-in routes under `/app/api/sign-in/` are the only ones without a token.

## Build a plan, without the chat

`#/build`: six steps (what Garmin knows, the race, days, how much, injuries,
style) and a preview, then "Put it on my Garmin", which calls `create_plan`
with what `plan_engine.build` returns. No model calls, and nothing is written
before that tap. The answers live in the tab's sessionStorage, so the trip
through Sign in with Garmin comes back to the same step. Today's empty state
opens it when the builder is on; daash.run's "Just the app" opens
`/app/#/build`, whose sign-in screen leads with Sign in with Garmin.

- `GET /app/api/build`: the prefill. The last 4 weeks' km and longest run
  (`plan_checks.starting_point`'s rules: under 2 weeks of running isn't
  enough), Garmin's race predictions and threshold pace, the races on the
  calendar with a distance Daash recognises, and any plan already in the next
  20 weeks.
- `POST /app/api/build/options`: step 4's keep, build and more.
- `POST /app/api/build/preview`: the weeks, the reasons and the plan checker's
  notes. Writes nothing.
- `POST /app/api/build`: refuses a plan over the weeks of one already on the
  calendar (a double tap, or a plan running), then writes it. Counted as
  `plan_built`.

On for preview accounts; `GARMIN_MCP_BUILDER=1` opens it to everyone. The
rules and what isn't built yet are in docs/plan-engine-spec.md.

## The doors between app and chat

- **Ask Claude / Ask ChatGPT** open `claude.ai/new?q=` or `chatgpt.com/?q=`
  with a prompt naming the session (date, name, workout id) or the run
  (activity id), and asking it to use Daash. The answer happens in the chat.
- **Open in Daash**: `get_session` answers carry `app_url`, the matching screen
  with a single-use sign-in code (`/app/#/session/2026-11-03?login=…`), and
  the session card shows an Open in Daash link. Preview accounts only, until
  the app is ready for everyone. The plan card's link is still to do.

## What is real and what is mock

Real: the plan and its sessions, steps and targets, shapes, week strip,
readiness, sleep, resting HR and HRV, the run's laps, splits and share image,
and every edit.

Not shown rather than mocked:
- on the session screen, when the watch collected the workout: Garmin's
  calendar says a workout is scheduled, not when the watch picked it up. The
  app says "Scheduled. Arrives at its next sync." The watch's model and its
  last upload are real, on You, from Garmin's last-used device;
- the "why this session" line when a workout has no note of its own.

Every screen is built; nothing is labelled "Not built yet".

You is real too: the watch and its last upload, which assistants hold a
live sign-in, units, heart rate zones and threshold pace from Garmin
(`get_fitness`), the goal race from the plan, the crew, and the account.

Crew is real, from `crews.week()`: the same matching the chat's crew card
uses (same day, a time of day they share, easy or long, a hard run always
alone). It never carries pace, heart rate, routes or distances. Opening
Today, Plan or Crew in the app refreshes your own runs in the crew from
your plan, as a Claude read of the plan does. Ask the group on WhatsApp
opens WhatsApp with "Easy run Tue morning? Ellie is out too. What time?" and
the crew's page link, or its invite code when the link is off.

## Differences from the designs, so far

- A step shows one target, pace or heart rate, never both: Garmin workouts
  made by Daash carry one target per step. A heart-rate miss can only be
  flagged on a step whose target is heart rate.
- Hard sessions are called "Threshold" or "Intervals", as the chat cards
  call them, not "Sub-T", so the two doors agree.
- Pace ranges are the workout's own, which `create_workout` widens to at
  least 5 s/km either side (4:00-4:08 is stored as 3:59-4:09).
- Readiness says "From Garmin" without a time: the time in the design is the
  watch's sync time, which Daash doesn't have.
- The week verdict on Today, the block verdict and the run verdict are worked
  out by rule, not written per week or per run. A past week counts as close
  to plan at 85% of its planned distance.
- Easier takes a third off each rep's distance (3 x 3 km becomes 3 x 2 km),
  rounded to the nearest 100 m, and leaves the pace alone. With no reps it
  takes a fifth off the main steps. Strength has no Easier.
- Recoveries are one "Jogs" row under the kilometre splits, as in the
  design; the kilometres are of the running only, so it all adds up.
- You:
  - "Sync now" is **Refresh**: Daash can't make the watch sync, only read
    Garmin again.
  - Export says "Not yet", since there's no export. Past runs say what's
    true for the account: read from Garmin as needed, or, on Garmin's
    official connection, a link to add earlier runs from Garmin's export.
  - Delete my training data and Delete account are one row, Delete account:
    Daash keeps no training history of its own apart from what the official
    connection sends, and there's no way yet to delete that alone.
  - Connect for Claude or ChatGPT links to daash.run; there's no page yet
    that adds the connector in one step.
  - No "Running since": Daash doesn't know it. No Terms link: there are none.
  - Week starts is always Monday, as Daash's weeks are.

## Desktop, 1024px and wider

- A top bar replaces the tabs: the wordmark, Today, Plan, Crew and You, Ask
  Claude (or ChatGPT) about this week, and your initial.
- **Today** is last week, this week and next as a calendar, from the same
  `get_plan` answer (`calendar` on `/app/api/today`), with the phone's cards
  on the right: Done today, the next session, readiness, the crew line and
  the week's verdict. Each day says which friends are out with you.
  **Drag a session to another day to move it**: the same move as the Move
  button. Done runs can't be dragged, and days already past refuse the drop.
  The next-session card drops Move there, as the design does.
- **A session or run opens in a 520px panel** over the calendar, closed with
  the cross or Esc, and never leaves the calendar. A run shows planned and
  run beside the splits, open. An edit made in the panel shows on the
  calendar at once.
- **Crew** is two columns: the matches and the invite on the left, the
  view-only calendar on the right with each person's initial and an AM, PM,
  EVE key.
- **Plan** shows the week and the block side by side; **You** sits in a
  720px column. Neither was designed for desktop.
- From 1024 to 1279px the same layout tightens (a 300px side column, smaller
  chips) so session names still wrap by word. Below 1024px it's the phone
  layout, centred.

## Not designed yet, used the nearest pattern

- Move from the session screen: a sheet of the next eight days as pills
  ("Pick a day. The watch follows."). On Plan, hold a row and drag it to
  another day; days already past dim and refuse the drop. A short tap still
  opens the session.
- Plan's week has arrows to the weeks either side, in the header where the
  design has Plan next week; Plan next week sits under the list, and only on
  the current week.
- A missed session: an amber "Not recorded" card on the preview and an amber
  ring on the week strip.
- No plan on the calendar: Today says so and offers Build a plan with Claude.
- A day with two sessions: the preview leads with the main one and links the
  other under "Also this day".
- Sign-in, signed-out and error states.
- Desktop: dragging sessions (its rules as above), Plan and You, and the
  calendar with no plan (the phone's "No sessions" card in the main column).
- Crew with no matches: a card saying runs that line up show there. Not in a
  crew: what a crew is, and Start a crew with Claude (joining with a code
  goes through the assistant too). One crew per person, so several crews
  can't happen.
- Crew's grid places a Daash member's run at their usual time of day for
  weekdays or weekends; a run with no usual time fills all three slots.

## Running it locally

```
.venv/bin/python scripts/app_dev.py
```

starts the hosted server against a demo Garmin account (`tests/demo_garmin.py`,
a 12-week half-marathon block around today) and prints a signed-in link,
an Open in Daash style link, and, for the email sign-in, the code itself.
`DEMO_RAN=1` shows today's session as run, for the analysis screen;
`DEMO_READINESS=low` shows the low-readiness state; `DEMO_CREW=0` leaves the
demo runner out of the demo crew.
