# The Daash companion app

The calendar is the home, the watch is the output, and the AI is a door. The
app is the daily place to see, check and lightly adjust the plan that Claude
or ChatGPT built. It has no chatbot and makes no model calls: "Ask Claude"
opens Claude with the context filled in. One deliberate, temporary exception:
the chat pilot below, for the people in `CHAT_PILOT_USER_IDS` only.

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
| Not feeling 100% | for the session being viewed (today or later, never a past one), three or four choices by its kind: a hard session offers make it easy (an easy run of the same time), a lighter run (same pace, a fifth shorter), swap with a later day this week, or skip this run; an easy or long run offers the last three (an easy run swaps onto a free day). A quieter "Need a few days off?" link under them opens 2, 3 or 5 days off; each is the writes above, by the rules in `garmin_mcp/feeling.py`, and answers what reached the watch | the week as changed; one Undo puts it back |
| Mark done | not built: Garmin decides done from a recorded run | |

Not feeling 100% is worded for the session being looked at ("Sunday's 12 km
becomes 9.6 km"), not the calendar's today. It keeps the plan checker's rules: no two hard days in a row,
nothing hard in the two days after a race, the long run never next to a hard
day, races never moved, nothing moved out of its week. It is offered for
today's session and later ones, never a past one, stands out on a day Garmin
reads readiness as low, and never changes anything until a choice is
confirmed. Days off that reach into an A race's taper or race week carry a
gentle note to ask the assistant, and stay allowed. Its words are
for a busy, tired or flat day and say what changes: nothing in it is about
injury or medical.

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
  - `GET /app/api/not100/{date}`: what each Not feeling 100% choice would do to
    that day's session, said before anything changes, plus a "Talk it through"
    link that opens Claude or ChatGPT with the week filled in (the app calls no
    model). `POST` the same path with `choice` (original, lighter, easy, swap,
    skip, days_off) and `days` to make it; the answer says what reached
    the watch (`watch`, `watch_said`) and carries the steps that undo it, which
    `POST /app/api/not100-undo` takes back, so nothing is kept on the server.
    If Garmin refuses a step, the change is put back and the answer is a 502
    with `watch_failed`, which the app shows as "Couldn't update your watch"
    with Retry.
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

1. **A link from a chat.** The plan and session cards' Open in Daash, and the
   app card after a workout push, carry a single-use code (15 minutes, stored
   as a hash). Tapping it opens the app already signed in as the person who was
   talking to the assistant: nothing to type, no email. A used or expired code
   falls back to the sign-in below, on the screen the link was opening.
   Counted as `app_signed_in` with `method=deep_link` (`magic_link` for a link
   in the weekly email, `email` for the code below).
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

`#/build`: seven steps (your running lately, races, the goal, days, how much,
injuries, style) and a preview with every week's distance, then "Put it on my
Garmin", which calls `create_plan` with what `plan_builder.create_args`
returns. No model calls, and nothing is written before that tap, which needs
"I'm 18 or over" ticked once on the phone. Weeks run Monday to Sunday and a
goal race falls on the last Sunday. A plan left part way is kept on the phone
for 14 days and Plan offers to pick it up. Today's empty state, Plan's empty
state and the next-block card open it; so does Today's card for a race added
to the Garmin calendar, with that race as the goal. daash.run's "Just the
app" opens `/app/#/build`, whose sign-in screen leads with Sign in with
Garmin.

- `GET /app/api/build`: what Garmin knows (4 weeks of running, the longest
  run, how long they've run, predictions, races on the calendar in the
  plan's window), kept 15 minutes. `?replace=1` starts it next Monday in
  place of the plan running now.
- `POST /app/api/build/plan`: goal times, the amounts and the preview from
  the answers so far. Writes nothing.
- `POST /app/api/build/send`, `GET` to follow it: puts the plan on the
  calendar in the background, removing the replaced plan's sessions from its
  start first; a retry sends only what's missing.
- `POST /app/api/plan/remove`: takes the plan running now off from today.
- `GET`/`POST /app/api/race-prompt`: Today's race card, its "Not now" and
  "Not a goal race".

Counted in PostHog: `app_build_opened` (where from), `app_build_step` (each
step the first time), `app_build_action` (back, jumps, the chart, blocked
steps, send, retry, where they land), `app_plan_built` (with why it failed),
`app_plan_removed` and the `race_prompt_*` events. Choices and counts only,
never a race's name, date or time.

On for preview accounts; `GARMIN_MCP_BUILDER=1` opens it to everyone. The
rules and what isn't built yet are in docs/plan-engine-spec.md.

## The doors between app and chat

- **Ask Claude / Ask ChatGPT** open `claude.ai/new?q=` or `chatgpt.com/?q=`
  with a prompt naming the session (date, name, workout id) or the run
  (activity id), and asking it to use Daash. The answer happens in the chat.
- **Get your plan** (Nov 23): with nothing planned (any sport) from today
  through the next 13 days, Today and Plan show two equal choices: "Build it
  with Claude or ChatGPT" (three steps here: race and date, weekly km from
  Garmin, days and long-run day; then a link that opens the assistant with the
  answers written out, `plan_chat_prompt`) and "Quick plan in the app" (the
  builder, `from=choice_today|choice_plan`). Which comes first is drawn once
  per person, 50/50, and kept (`store.plan_choice_first`). The app asks no
  model anything: it builds the link. Events: `plan_choice_shown` (first,
  screen), `plan_choice_picked` (option, position), `plan_form_step` (step),
  `plan_handoff_opened` (assistant), `app_plan_built` (from), and
  `plan_active_7d` (option, position, active, done_7d, ahead_14d), sent by a
  background check a week after the pick that reads the plan the way the
  weekly email does (`hosted.plan_checks_once`).
- **The app card** (Nov 23): when a workout reaches someone's watch, the
  tool's answer carries `app_card` (its text, a signed-in link to that week,
  a button label) for the assistant to show: on the first push for that
  person, and once more on a push 7 or more days later if they still haven't
  opened the app; never after that (`store.claim_app_push`). Someone never
  offered it gets it on their next push. Counted as `app_link_offered` with
  `reason` `first_push` or `reminder`. Not for an account on the official
  API alone, whose answers carry nothing past Garmin's data.
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

## The chat pilot (Nov 27)

A private, flag-gated Chat tab, to see whether a chat view is useful before
any Plus or iPhone work. For the people listed in `CHAT_PILOT_USER_IDS` only
(Bart); off for everyone else and off for everyone when the variable is unset.
Code in `garmin_mcp/chat_pilot.py`; the tab in `app.js` under "Chat (pilot)".

- **The flag** is server-side. `CHAT_PILOT_USER_IDS` is a comma list of
  PostHog distinct ids (the same ids `ANALYTICS_INTERNAL_IDS` takes) or email
  fingerprints. `GET /app/api/features` answers `{"chat": true}` for them and
  `{}` for everyone else; every chat route answers anyone else with the same
  404 an unknown `/app/api/` path gives.
- **Routes** (signed in like the rest of `/app/api/`): `GET /app/api/chat`
  (the thread and the starter chips), `POST /app/api/chat/send` (the answer as
  server-sent events: `start`, `tool`, `delta`, `pending`, then `done` or
  `error`), `POST /app/api/chat/action` (`confirm` or `decline` one proposed
  change), `POST /app/api/chat/feedback` (`up` or `down`), `POST
  /app/api/chat/new` (a fresh thread; the old one is kept, anything it still
  proposes expires).
- **Reads** are the MCP tool functions themselves (`server.get_plan`,
  `get_activities`, `get_readiness`, `get_sleep_data`...), offered to the model
  with the MCP tools' own descriptions and schemas. Nothing is registered as a
  tool, so MCP clients see no change. A tool's answer is cut to what the model
  needs (no card fields, a plan cut to last week through three weeks ahead,
  at most `CHAT_TOOL_RESULT_CHARS`) and never stored.
- **Writes never run while the model answers.** A write the model asks for is
  stored as a proposed change (a `chat_messages` row, `pending`) with a
  plain-English summary worked out by rule from the stored call, and shown as
  a card. Only Confirm runs it: one UPDATE moves it from pending to running, so
  it runs once, for its owner, within a day of being proposed, with no model
  involved. Decline cancels it. `delete_workout` and `remove_plan` keep their
  MCP preview: the first call (a read) names what would go, and the stored call
  carries the confirm it asked for. Moving a session is `app.move`, counting a
  run is `app.match_apply` (the Match buttons' own code).
- **The prompt** is short: today in Europe/London, this week's and next
  week's sessions (with their ids), the last six activities, today's readiness.
  The model is told to be brief, use tools for figures, propose changes rather
  than claim them, and give no medical or injury advice.
- **Storage**: `chat_threads` and `chat_messages` (store.py, `CHAT_SCHEMA`),
  one active thread a person, text and a change's arguments encrypted like the
  Garmin tokens. Deleted with the account. The model sees the last
  `CHAT_CONTEXT_MESSAGES` (20) as plain text.
- **Guards**: `CHAT_DAILY_MESSAGE_CAP` (500, a 429 with a clear message),
  `CHAT_MAX_OUTPUT_TOKENS` per model call (1,024), `CHAT_MAX_REQUEST_TOKENS`
  per message across its tool loop (60,000), `CHAT_MAX_TOOL_CALLS` per message
  (8; the same call twice in a message is refused, as the MCP loop guard does
  across calls), timeouts on the provider (`CHAT_PROVIDER_TIMEOUT_S`, 30 s
  between chunks), each read (`CHAT_TOOL_TIMEOUT_S`, 25), the whole message
  (`CHAT_TURN_TIMEOUT_S`, 90) and a confirmed write (`CHAT_WRITE_TIMEOUT_S`,
  120). A stream that dies, or a phone that goes, saves what was said as
  `partial` (or `failed`) rather than leaving it hanging.
- **Memory**: the reply streams as it's written; nothing lives between
  requests but the rows. No caches, no per-person objects.
- **Provider**: `CHAT_PROVIDER` `anthropic` (default, `claude-haiku-5-5`),
  `openai` (`gpt-4o-mini`) or `gemini` (`gemini-2.5-flash`, through Google's
  OpenAI-compatible endpoint); `CHAT_MODEL`, `CHAT_API_KEY` (or the provider's
  own `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`), `CHAT_BASE_URL`,
  `CHAT_EFFORT` (Claude only, `low`; empty sends none). Raw HTTP over the
  `httpx2` the MCP SDK already brings, so no SDK is added. Cost is estimated
  from list prices in `chat_pilot.PRICES`, or `CHAT_PRICE_INPUT_PER_M` and
  `CHAT_PRICE_OUTPUT_PER_M`.

Counted in PostHog: `chat_opened`, `chat_message_sent` (`starter_used`,
`matched_tool`: the first tool the model called, to set beside MCP's
`tool_called`), `chat_tool_called` (`tool`, `ok`), `chat_write_confirmed`,
`chat_write_declined`, `chat_response` (`latency_ms`, `input_tokens`,
`output_tokens`, `est_cost_usd`, `model`), `chat_feedback` (`value`). Never
what was said. The same tokens, model, cost and time are logged on the server
and kept on each answer's row. The website sent a different
`chat_message_sent` until Oct 1: count the pilot's with `$lib =
'garmin-mcp-hosted'`.

`scripts/chat_replay.py` sends one prompt for each of the six most-used MCP
tools through the real endpoint as the allowlisted account and prints tools,
tokens, latency and cost per request; proposed changes are declined unless
`--confirm`, and name dates ten weeks out. Locally:
`DEMO_CHAT=1 CHAT_API_KEY=... .venv/bin/python scripts/app_dev.py`.

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
