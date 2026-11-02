# The Daash companion app

The calendar is the home, the watch is the output, and the AI is a door. The
app is the daily place to see, check and lightly adjust the plan that Claude
or ChatGPT built. It has no chatbot and makes no model calls: "Ask Claude"
opens Claude with the context filled in.

Designs: `design_handoff_daash_mvp_v2` (pass 2). This file says how the app
plugs into what the server already does.

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
  - `GET /app/api/today`, `/app/api/session/{date}`, `/app/api/run/{activity_id}`:
    JSON shaped for the screens, built from the tool functions.
  - `POST /app/api/session/{date}/move|easier|undo|resend`: the edits above.
- `garmin_mcp/analysis.py`: planned against run for one session, worked out
  from the raw laps and the workout's steps. No model, so the same run always
  gets the same verdict, and every number comes from the same laps so they add up.
- `garmin_mcp/app/`: the web app. No build step, like the in-chat cards in
  `garmin_mcp/ui/`: plain ES modules, one stylesheet, Geist bundled locally,
  a manifest and a service worker so it installs to the home screen.

## Sign-in

The app is an OAuth 2.1 client of the server's own authorisation server, the
same one Claude and ChatGPT use: it registers itself, sends the person to
`/authorize` with PKCE, and gets a bearer token on `/app/callback`. Someone
who already uses the connector signs in to the same Daash account through the
same Garmin step, and lands on their plan with no new setup. Someone new
connects Garmin there and adds Claude or ChatGPT later.

`SessionBinding` already turns a bearer token into that person's Garmin
session, crew membership and official-API context for `/mcp`; it does the same
for `/app/api/*`, so every tool function runs exactly as it does for Claude.

## The doors between app and chat

- **Ask Claude / Ask ChatGPT** open `claude.ai/new?q=` or `chatgpt.com/?q=`
  with a prompt naming the session (date, name, workout id) or the run
  (activity id), and asking it to use Daash. The answer happens in the chat.
- **Open in Daash**: `get_session` answers carry `app_url`, the matching screen
  (`/app/#/session/2026-11-03`), and the session card shows an Open in Daash
  link. Preview accounts only, until the app is ready for everyone. The plan
  card's link is still to do.

## What is real and what is mock

Real: the plan and its sessions, steps and targets, shapes, week strip,
readiness, sleep, resting HR and HRV, the run's laps, splits and share image,
and every edit.

Not shown rather than mocked:
- the watch's model and last sync time: Garmin's calendar says a workout is
  scheduled, not when the watch collected it. The app says "Scheduled.
  Arrives at its next sync." instead of a time it doesn't have;
- the "why this session" line when a workout has no note of its own.

Labelled "Not built yet" in the app: Plan, Crew and You. The crew line on
Today is real, from `crews.week()`, and only shows for someone in a crew.

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
- The week verdict on Today and the run verdict are worked out by rule, not
  written per week or per run.
- Easier takes a third off each rep's distance (3 x 3 km becomes 3 x 2 km),
  rounded to the nearest 100 m, and leaves the pace alone. With no reps it
  takes a fifth off the main steps. Strength has no Easier.
- Recoveries are one "Jogs" row under the kilometre splits, as in the
  design; the kilometres are of the running only, so it all adds up.
- Signing in shows Daash's existing consent page ("Daash app is asking to
  read your Garmin data"). For Daash's own app that step could be skipped.

## Not designed yet, used the nearest pattern

- Move: a sheet of the next eight days as pills ("Pick a day. The watch
  follows."). Hold to move on Plan is still to build.
- A missed session: an amber "Not recorded" card on the preview and an amber
  ring on the week strip.
- No plan on the calendar: Today says so and offers Build a plan with Claude.
- A day with two sessions: the preview leads with the main one and links the
  other under "Also this day".
- Sign-in, signed-out and error states.

## Running it locally

```
.venv/bin/python scripts/app_dev.py
```

starts the hosted server against a demo Garmin account (`tests/demo_garmin.py`,
a 12-week half-marathon block around today) and prints a signed-in link.
`DEMO_RAN=1` shows today's session as run, for the analysis screen;
`DEMO_READINESS=low` shows the low-readiness state.
