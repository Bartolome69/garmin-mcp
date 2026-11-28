# Native app: decisions

What was settled before and during the build, against the build prompt
(`daash-native-build-prompt.md`, 27 Nov 2026). Where the prompt and the repo
disagreed, this says which way it went and why. Newest last.

## Section 0

| Question | Decision | Why |
| --- | --- | --- |
| Stack | Expo SDK 58, TypeScript, expo-router, native tabs; native extensions for widgets and live notifications | The prompt's default, and the repo already had the scaffold (#23) |
| Backend | The existing Daash server and its `/app/api/*`, same sign-in; no second backend | The prompt says reuse it; it exists |
| Garmin access | **Password-access accounts, for now.** Garmin has locked Daash out of the official API, so the app works with accounts on Daash's existing Garmin sign-in (the private API the server already uses) | Bart, 27 Nov. Overrides the prompt's "official only" until access returns |
| Coach model | Server-side only, built on the chat pilot (#22) | The prompt; #22 already does it |

How the password route is handled, so it can be swapped for the official API
later without touching the app:

- The app never sees a Garmin password. Sign-in opens Daash's own Garmin page
  (`/authorize` → `/connect`, MFA included) in the system browser
  (`ASWebAuthenticationSession` / Custom Tabs); the app only receives Daash's
  OAuth tokens.
- Every Garmin read and every watch write stays on the server, behind the
  existing tool functions. The app's watch-operation queue calls Daash, never
  Garmin.
- TODO(official-api): when Garmin restores access, switch sign-in to
  `/official/*` and reads to Garmin's push data on the server; no app release
  needed. App Review notes must explain the current Garmin sign-in honestly.

## Defaults taken where the question wasn't answered

| Question | Default | Change it by |
| --- | --- | --- |
| Where the deterministic engines live | **The server is the one source** (Python, unit-tested), read by the app, the PWA and the Coach, so numbers match everywhere and Coach ON/OFF parity holds by construction. The app's `domain/` keeps what must run on the device: the text-to-workout parser (works before sign-up and offline), the instant back-to-back warning while dragging, and default paces for try-before-signup. The server re-checks before any write | Telling me to put all engines in the app |
| "On your watch · 20:14" | Shown only when Garmin reports the watch synced after the change; the time is that sync. Until then: "Changed · arrives at next sync" | — |
| The new readiness rule | Runs on the server behind a flag; when on, the PWA's readiness card uses it too | — |

## Conflicts between the prompt and the repo

- **Tokens.** All 23 approved values already existed in `app.css`, except
  `on-brand`, added as `--on-brand: #FFFFFF` (the PWA had it as a literal).
  `scripts/build_tokens.py` emits only the approved names;
  `tests/tokens_test.py` holds the prompt's table and fails on any drift.
- **Scrim.** `app.css` has black at 55% in dark; the prompt says bg at 70%.
  The app follows the prompt (derived in `theme/derived.ts`).
- **Fonts.** The PWA uses Geist; the app uses the system font for UI, Geist
  Mono for kickers and step targets, and Source Serif 4 for Coach prose, as
  the prompt says.
- **Widgets** are Plus (the prompt), not the free widget #23's plan suggested.
- **Providers.** Only Garmin exists on the server. Intervals.icu, Apple Health
  and Health Connect show as "Coming soon · Tell me when" until built.
- **Coach.** The prompt wants 10 free messages a day and no "New chat"; #22
  has a 500 cap and a New chat button. #22 changes to match in milestone 11.

## Milestone 1 (foundation), 27 Nov

- Tokens: `src/theme/tokens.ts` (generated, approved set only),
  `derived.ts` (zones, scrim, badge tints computed from tokens),
  `ThemeProvider` (follows the system, override kept on the device).
- Type: system font, Geist Mono, Source Serif 4; iOS text styles and the
  Material 3 scale; tabular numbers; text scales to 200%.
- Icons: `ui/Icon.tsx`, SF Symbols / Material Symbols by one name, at 17/22/25
  pt (iOS) and 24 dp (Android); filled symbol for the selected tab.
- Navigation: expo-router native tabs, Today · Plan · (Coach) · Crew · You;
  Coach hidden and unreachable without `coach_enabled`. Each tab is a stack
  with large titles on iOS.
- Local DB: every section 7 entity, migrations, expo-sqlite adapter;
  `watch_operations` only accepts add/replace/remove and the six send states.
- Flags: server-driven with a device cache; defaults coach off, limit 10,
  readiness and merge thresholds from the prompt (TODO: sports-science sign-off).
- Checks: `npm run check` (tokens in step, colour lint, typecheck, 54 tests run
  as iOS and as Android) and an offline Metro bundle for both platforms, all in
  `.github/workflows/mobile.yml`.

## Milestone 2 (domain engines), 27 Nov

Where they live, as decided above: the server's `garmin_mcp/domain/` is the one
source; the app's `mobile/src/domain/` holds only what runs on the device.

| Engine | Server (Python) | App (TypeScript) |
| --- | --- | --- |
| Readiness | `readiness.py` | reads the server's answer |
| Load and band, forward projection | `load.py` | reads |
| Life happened rules → day operations | `adjust.py` | reads |
| Back-to-back | `backtoback.py` | `backToBack.ts` (instant drag warning) |
| Pace targets, Easier / Harder | `paces.py` | `paces.ts` (try-before-signup) |
| Prediction and 12-week trend | `predict.py` | reads |
| De-duplication and source priority | `dedupe.py` | reads |
| Post-run takeaways | `takeaways.py` | reads |
| Text-to-workout parser | — | `parser.ts` (before sign-up, offline) |

- The twins are held to the server's answers by `garmin_mcp/domain/fixtures/`
  (106 back-to-back and 35 pace cases, written by `scripts/domain_fixtures.py`;
  CI fails if either side drifts). Both round halves up, as JavaScript does.
- Nothing is wired to a route yet: no server or PWA behaviour changes. The
  screens' milestones expose them through the API.
- Thresholds are in `garmin_mcp/domain/config.py`, overridable by
  `DAASH_DOMAIN_CONFIG` (JSON), with the same defaults as the app's flags.
- Choices made where the prompt left room (all TODO for the sports-science
  sign-off): "none low" is Push even when the load ratio is under 0.8; the
  intensity factors (easy 1.0 … race 1.8); the Life happened volumes; a
  recovery after repeats attaches to them only if it's 5 min / 800 m or less;
  the parser adds a 10 min warm-up and cool-down to any session with quality.
- `backtoback.py` follows the prompt (a long run is hard only over 25 km);
  the PWA's existing `plan_checks.py` treats every long run as hard. Left as
  is until the screens decide which the plan views use.

## Milestone 3 (Today), 27 Nov

- **Fixtures until the server exists.** Today reads `GET /app/api/v1/today`,
  which isn't built yet, and there's no sign-in until M9. Development builds
  (and the layout check's web build, `EXPO_PUBLIC_DAASH_FIXTURE=1`) fall back
  to the persona in `features/today/fixture.ts`; a store build shows the error
  state with Try again. The server endpoint is a separate backend PR.
- **Offline first.** Every screen-shaped response is cached in SQLite (`kv`)
  and shown at once on launch, then refreshed. A failed refresh keeps the
  cached data. Past 30 minutes the header says "Updated 2h ago" (iOS) or shows
  a stale chip in the app bar (Android).
- **Conflict, prompt wins: "On your watch" is text2, not success.** The Pass 3
  board draws it green; the prompt says the watch line is quiet unless it
  needs action (amber when changed, brand text when failed).
- **Conflict, prompt wins: no explanation line with Coach off.** The 200% (XL)
  board shows a one-line explanation under the readiness signals even with
  Coach off. The prompt says that with `coach_enabled` off nothing reads like
  a Coach, so Coach off shows the signals and their notes only. With Coach on,
  the line is the Coach's, clamped to 2 lines at 200%, with "Ask why".
- **Brand fill.** "Make it easier" (or whatever the recommended action is) is
  the screen's one brand fill. It loses the fill whenever Today isn't focused,
  so the readiness and options sheets sit over a screen with none.
- **"Next" is a placeholder.** Choosing "Make it easier", "Swap a day" or
  "Rest and recover" opens `/today/next`, which says the step arrives with the
  diff and confirm sheet (M5) or the plan (M4). Nothing is written yet.
- **Above the fold, checked on a web build.** No simulators here, so
  `mobile/scripts/layout-check.js` measures a `react-native-web` export at
  390×844: the session card ends 428 pt and This week 599 pt below the top of
  the header, against 702 pt of room above the iPhone tab bar and home
  indicator. On web the native tabs render as a top bar over the title; on
  iOS and Android they're the system bar at the bottom. The check runs locally
  (it needs Chromium), not in CI.

## Milestone 4 (Plan), 27 Nov

- **Fixtures until the server exists.** Plan reads `GET /app/api/v1/plan` (a
  window of weeks around this one, the block and the trends, each planned
  session with its steps so the detail opens offline). It isn't built yet;
  development builds and the layout check's web build show the persona, as
  Today does. Cached in SQLite and drawn at once; pull to refresh; "Updated 2h
  ago" when stale.
- **Pace numbers come from the engine, not the board.** The boards' sample
  targets (4:22–4:30, 3:55–4:05, 5:30–5:50) aren't what the pace engine gives
  for the persona's 10K of 43:30. The fixtures use the engine's answers
  (threshold 4:23–4:28, VO2 4:03–4:10, easy 5:27–6:10), and Today's fixture
  now matches, so the two screens agree.
- **Block has no Pass 1 or Pass 3 board.** Built from the canvas's first-page
  Block board with the neutral phase bar from section 5.13 (steps of the text
  colour's opacity, labelled, never session colours; this week stands
  taller). The persona has 21 weeks (Today's "Week 4 of 21"), not the board's
  25. The board's "Pfitzinger-style" label is left out: a named method reads
  as an endorsement.
- **Conflict, prompt and privacy: the Plus preview is faded, not blurred.**
  The prompt asks for a blurred preview of the prediction trend. A blur over
  the person's own numbers would still send them to a Free account, and
  React Native has no blur without another native module. Free gets a faded
  chart drawn from placeholder data, with no figures, under "See Plus". The
  server leaves the trend out for Free.
- **Moving a session.** Hold and drag, or the "a day earlier / later"
  accessibility actions, or Move on the session detail (a sheet of the
  week's days). All three run the same check on the device (the shared
  back-to-back engine): a clash warns first with "Move anyway" and "Keep it
  on <day>"; a pass becomes a proposal drawn as "Waiting for your confirm",
  with Undo and "Review and send". Nothing is written. The confirm sheet and
  the watch queue are milestone 5; "Review and send" leads to a placeholder.
  Runs already done, rest days and moves into the past stay put, with words.
- **The drag uses React Native's PanResponder, not the gesture handler.** A
  long press lifts the row; the pan responder takes the moves that follow.
  The gesture handler needs Reanimated, whose Jest mock doesn't load with
  this SDK, and the drag needs no worklets. The test drives the real
  PanResponder (long press, grant, moves with touch history, release) and
  caught a bug a phone would have shown: the press ending as the pan took
  the touch put the row back down mid-drag.
- **Brand fill.** iOS Plan has none ("Life happened" and "New workout" are
  outlines, as the board). Android's extended FAB is the one, and loses it
  behind a sheet. "Keep it on Fri", "Send to watch" and "Review and send" use
  a new text-colour fill (Button kind "emphasis"), as the boards do, not
  brand.
- **Status icons.** Done is the filled tick in success; On your watch is the
  outline watch in text2 (prompt, over the board's success); a session not
  yet sent shows no icon, and its row reads "Not on your watch".
- **Session detail.** The board's Edit (top right) opens the builder
  (milestone 6). Easier and Harder step pace targets 2% with the shared pace
  engine; a session already on the watch then offers "Send changes". With
  the Coach on, one "Coach · why this session" card; every number is the
  same.
- **Checked on a web build.** `mobile/scripts/plan-check.js` at 390×844: the
  seven rows end 519 pt below the switch (room 606 pt on an iPhone under the
  large title, 636 pt on Android), the FAB starts at 564 pt, after the last
  row, and 10 × 400 m ends at 640 of 658 pt. The web build draws the Android
  layout (Material 3 tabs, the FAB). Symbols don't draw on web (expo-symbols
  is native only), and the web version of native tabs stacks the other tabs'
  screens over the one shown, so the browser can't drag; the drag is tested
  in Jest.
- **New dependency.** `@react-native-segmented-control/segmented-control`
  2.5.7 (the SDK's pinned version) for the standard iOS segmented control.
  Its typings predate React 19's JSX types, so PlanTabs casts the component.

## Milestone 5 (diff, watch confirm, watch queue, Life happened, Remove from watch), 27 Nov

- **Correction to milestone 4: the boards' primary pill is a brand fill.** In
  the canvas CSS a later rule (`daash.css`, line 339) makes `.pill.primary`
  brand red, not the text-colour fill I read in M4. The text-filled "emphasis"
  button is gone. "Keep it on Fri", "Review and send", "Send to watch",
  "Review watch changes", "Confirm and send", "Remove from watch" and "Resend"
  are each their screen's one brand fill. On Android, Plan's FAB steps aside
  while a move waits for a decision, so the banner holds the brand fill
  alone.
- **One queue writes to the watch** (`features/watch/`). WatchProvider is the
  only code that calls a transport, and only for operations confirmed on the
  sheet. Operations are stored in `watch_operations` (M1's table) and sent in
  order. They move through sending, then "Changed · arrives at next sync", then
  "On your watch", or to failed. "On your watch" shows only once the watch
  syncs, the section 0 default. A failure that may pass (Garmin didn't answer,
  no connection) retries on its own after 30 s, 1, 2, 4, 8, 15 and 30 min,
  then waits for Resend; a refusal waits for Resend at once. The screen always
  shows the current state, in words.
- **Where proposals come from.** The engine's changes come from the server:
  Life happened, Make it easier, Rest and recover, Swap a day and Trim week N,
  made by the new `garmin_mcp/domain/proposal.py` view. The simple ones are
  built on the device and need no new number: a move, a skip, a swap of two
  days, sending one session, new targets, and Remove from watch. Every one
  goes through the same confirm sheet. Changes to the plan also go through
  the shared diff ("New week"), Today's "Make it easier" included.
- **Engine changes (pure, tested).**
  - `adjust()` takes `today`. A rule's window counts from `since`, but a day
    before today never changes: it's done. Before, "since Wednesday" could
    rewrite Wednesday's run.
  - New rules: `easier`, `rest`, `swap` and `trim`. Trim scales only the easy
    runs; the long run and quality sessions stay.
  - Titles follow distances: "Long 18 km easy" scaled reads "Long 9 km easy",
    and a hard session made easy reads "Easy 6 km".
  - "N sessions change" counts sessions, not watch operations: a move is a
    remove and an add of one session.
  - `tests/domain_test.py` has 94 checks.
- **Fixtures from the engine.** `scripts/domain_fixtures.py` writes
  `proposals.json` for the persona's weeks 4 to 7: every reason × "since"
  over the past 7 days, Make it easier, Rest, Swap and Trim week 7. It writes
  the same file into `mobile/src/features/watch/` because Metro bundles only
  what's inside `mobile/`. `--check` in CI keeps both in step. The persona's
  Python plan uses the app fixture's session ids.
- **The diff's chart is kilometres, not load.** The board labels its bars in
  km ("54", "37 km") with "Shaded: your target band". So the bars are planned
  kilometres a week, and the band is the engine's ±10% around the week as
  planned. Unchanged weeks have identical bars, and value labels appear only
  where a week changes. A week with nothing planned either way is left out.
- **Conflict: no "Edit days" yet.** The diff board has "Edit days", and its
  rule text ends "Edit any day before you confirm." Editing single days of a
  proposed week has no design beyond that button, and arrives with the
  builder (M6). Until then the button is left out, and so is that sentence of
  the rule.
- **Device-built summaries say only what changes.** A move reads "This week's
  kilometres stay the same"; a skip reads "10 km less this week". Adding up
  done runs at their actual distance gave 56.5 km against the week header's
  54 km.
- **Session detail's Swap** trades days with a session on a chosen day of
  the week, checked with the back-to-back engine. "Trouble connecting?" leads
  to Connections (M8).
- **E2E.** Maestro and Detox need a simulator; there's none here. The four
  flows run as Jest integration tests over the real routes, screens and queue
  against a scripted Garmin (`mobile/src/flows.test.tsx`), on iOS and Android.
  The same flows are written as Maestro files in `mobile/e2e/`, not run.
  Contract tests cover the send with success, timeout and failure, and the
  store runs against real SQLite.
- **Web.** The web version of native tabs draws the wrong screen inside a
  nested stack. The diff and confirm can't be viewed there; their structure
  is covered by the Jest snapshots and tests.
- **TODO(server), for the nightly deploys:**
  - `POST /app/api/v1/plan/propose`: `{kind: life|easier|rest|swap|trim,
    reason?, since?, week?}` returns `proposal.view(...)`.
  - `POST /app/api/v1/watch/ops`: one confirmed operation, `{id, kind,
    session_id, date}`. It must be idempotent by id, because a timed-out send
    can arrive late; it answers `{synced}` or `{refused: "<reason>"}`.
  - `POST /app/api/v1/watch/synced`: `{ids}` returns the ids the watch has.
  - These sit on the server's existing Garmin writes (schedule, unschedule,
    update). Until then a development build sends to a stand-in Garmin
    (`EXPO_PUBLIC_DAASH_WATCH=fail` makes its first send fail).

## Milestone 6 (builder and parser), 27 Nov

- **The parser reads more than the prompt's minimum, and refuses what it can't
  know.** On top of section 5.7's grammar it reads:
  - coach shorthand (`'` for minutes, `"` for seconds);
  - a zone without "at" ("5x1km threshold");
  - a recovery in the same clause ("6x800m at 3:50/km 400m jog");
  - the word first ("tempo 20 min", "jog 2 min");
  - strides, as reps with no pace target;
  - long runs ("16 km long run", easy);
  - "28 km with the last 8 at marathon", read as 20 km easy then 8 km at
    marathon;
  - "hr 150 for 40 min";
  - bracketed repeats ("4x(1km at 10k, 400m jog)").

  It refuses these, each with what to write instead:
  - "hard", "fast" and "quick";
  - "mile pace", 3K and half-marathon pace (5K pace is the fastest zone);
  - "parkrun" and "race";
  - fartlek;
  - a run with no length ("easy run");
  - a number with no unit ("Did you mean "200 m"?");
  - a unit one letter off ("1kn": "Did you mean "1 km"?");
  - repeats inside repeats (not yet).

  The corpus is `mobile/src/domain/parser.corpus.test.ts`: 31 readings and
  10 refusals. M2's 16 still pass, with one change below.
- **Added warm-up and cool-down.** Strides no longer count as quality. An easy
  run written first or last already does the warm-up's or cool-down's job, so
  nothing is added there. That changes one M2 reading: "20 min easy + 6 x 20 s
  at 5k + 10 min easy" no longer gets an added warm-up and cool-down. The added
  ones stay at 10 minutes. The board's "15 min easy (added)" is illustration;
  the prompt names no length.
- **The builder is the parser's reader, all the way down.**
  - Edit steps shows one text row per step. The rows become the builder's text
    and are read by the same parser, with the same fix-its. There is no second
    step editor to keep in step.
  - A planned session's Edit turns its step rows back into that text ("15 min
    warm-up, 3 x 2 km at threshold with 2 min jog, 10 min cool-down"). A
    session without rows uses its title when the title reads.
  - Strength, rest days and recorded runs have no Edit.
- **A "Did you mean …?" fix-it is a button** ("Use "threshold""). It replaces
  only the marked text, and the runner presses it. Other fix-its are words
  only, because the choice is the runner's.
- **Conflict: the board's numbers.** The board says "9.4 km · about 46 min"
  for 5 × 1 km with a 15 min warm-up. The estimate here is worked out:
  - zones at their middle;
  - a jog at the slow end of easy;
  - a walk at 10:00 /km;
  - a rest covers no ground;
  - the recovery after every rep.

  For the persona that comes to 10.1 km · about 52 min.
- **The type chip** follows the work:
  - 10K, 5K or VO2 (or repeats with no target) is Intervals;
  - steady, marathon, threshold, tempo or a heart rate is Tempo, which matches
    the board's "Tempo" for threshold reps;
  - a long run, or 18 km and over, is Long;
  - anything else is Easy.

  Editing a planned long run that reads as easy keeps it Long.
- **Targets.** The zones come from the runner's basis race, through the shared
  pace engine. Before a watch is connected they come from a 10K of 45:00, and
  the adjust row says so ("Threshold from a 10K of 45:00 until a watch is
  connected"). Easier and Harder re-step the zones by 2%. A pace or heart rate
  written out is kept as written, and the row doesn't show.
- **Save and send** stages one proposal, `source: 'build'`, and opens the
  watch confirm, which has a one-session shape for it. The shapes are:
  - a new workout is an Add;
  - Edit is a Replace;
  - Edit onto another day is a Remove from the old day plus an Add.

  Each op carries the `daash.workout.v1` it sends, as
  `garmin_mcp/providers/workout.py` defines it (PR #27). Its tests hold every
  corpus session to that file's rules. Once confirmed, Plan shows the session
  with its steps before the server has it, and the builder closes.
- **A day that already has a session** says "8 km easy is already planned that
  day. This adds a second session." Saving adds the new one alongside; it
  doesn't replace. Replacing is Edit's job, so a new workout never silently
  takes a planned one's place. Two hard days in a row are said with the
  back-to-back engine and never blocked.
- **Disabled primary buttons lose the brand fill.** A greyed "Save and send"
  in brand red looked pressable. This applies to every primary button; no
  earlier screen's snapshot changed.
- **The steps card is shared** (`features/plan/StepsCard.tsx`) by session
  detail and the builder. Session detail's snapshots are unchanged.
- **Coach on** drops "parsed on your phone" and adds the Coach card, and every
  number stays the same (tested). The card's text is the server's.
  TODO(server): a Coach note for a built session. A development build shows a
  stand-in from the days around it ("Fits Friday. …").
- **Not yet:**
  - The diff's "Edit days" (M5) still has no design beyond its button, so it
    stays out. The builder edits sessions from the plan.
  - The "try-builder used" analytics event waits for the app's analytics layer.
  - Trying the builder before sign-up needs onboarding (M9). The builder already
    works with no plan and no account.
- **E2E.** Five builder flows run as Jest integration tests over the real
  routes and queue (`mobile/src/builder.flows.test.tsx`), on iOS and Android:
  - new workout to the watch;
  - a typo and its fix-it;
  - a refusal;
  - Edit steps;
  - editing a planned session.
- **TODO(server):** `POST /app/api/v1/watch/ops` takes the op's `workout`
  (daash.workout.v1) for an add or replace from the builder. The server builds
  it with `providers.current()`, so Garmin keeps building it as before.

## Milestone 7 (post-run), 27 Nov

- **The server works the run out; the app draws it.**
  `garmin_mcp/domain/postrun.py` `view()` turns a provider-neutral `Run` into
  what the screen shows:
  - planned vs actual, each row marked on plan or not;
  - pace, HR, cadence and elevation on the same 160 points by distance;
  - laps;
  - each chart's ticks, its screen-reader summary and its takeaway;
  - HR zones;
  - the route scaled to a box.

  Nothing in it takes a coach flag (tested), so every number is the same with
  the Coach on or off (also tested on the app's side). The app reads values at
  the scrub line and formats them; it computes nothing else.
- **Takeaways: two new templates in `takeaways.py`.** HR across reps ("HR
  drifted 4 bpm across the 6 reps.") and elevation ("48 m up, 46 m down.", or
  "Flat." under 15 m). Their thresholds are in `config.py`, alongside new
  on-plan tolerances: within 10% of the distance, and within 3 s of the pace
  range.

  Which template applies:
  - runs with reps use the rep templates;
  - steady runs use splits and HR drift at the same pace;
  - strides are their own laps ("Stride 1"), never reps, and the run's pace and
    splits leave them and their recoveries out.

  The first sentence is bold, as on the board. "Even", "Negative split" and
  "steady" carry a tick; the rest carry an info mark. Neither is colour alone.
- **The scrub line** is one line across every metric. It starts in the middle of
  the rep nearest halfway, which is "Rep 4 · 4:00 /km" for the persona, as on
  the board, and halfway on a run without reps. A finger on either chart moves
  it. For screen readers the charts are one adjustable element: its label is
  each chart's summary, its value is what the line reads, and swiping up or
  down moves the line lap by lap (or a kilometre at a time).
- **Conflict: Android's board** (Pass 1) shows one chart with Pace, HR, Cadence
  and Elev as pages. The prompt says stacked Pace and HR with Cadence and
  Elevation as further tabs, so both platforms use Pass 3's layout: "Pace +
  HR", "Cadence" and "Elev". The tabs are the board's pills.
- **Conflict: Pass 1's light debrief** says rep 6 was "in line with this week's
  short sleep", which states a cause. Pass 3 replaced it with an observation;
  that's the line followed here. The debrief is the server's (TODO(server)). A
  development build shows a stand-in whose numbers are all the run's own, which
  is tested.
- **The route map is the GPS line on the sunken tone**, with a hollow dot for
  the start and a solid one for the finish, and it says so to screen readers.
  Map tiles need a maps SDK and a key: an open question for the accessibility
  and polish pass, not a blocker.
- **Where post-run opens.** It opens from a done row on Plan and from a done day
  in Today's week strip. That makes those days buttons ("Opens the run"); the
  rest of the strip stays information only. Today's snapshots didn't change.
  Share sends the run in words: title, date, distance and time, and the two
  takeaways. "Ask about this run" opens the Coach with the run as its context,
  which the Coach milestone (M11) will read.
- **Thumbs up and down** are held on the screen for now. TODO(server): store
  them with the debrief.
- **Offline.** A run is cached on the device (`run:<id>`) and drawn from there
  first. A run doesn't change once recorded, so it shows no stale label.
- **Persona runs.** The development fixture (`runs.fixture.json`, written by
  `scripts/domain_fixtures.py` from the real engine and checked in CI) has this
  week's three runs, built so their numbers match the plan fixture's rows:
  - Tue: 8 km easy + strides;
  - Wed: 6 × 800 m, 11.2 km, reps 4:01 avg;
  - Thu: 9 km run club, 5:14 /km, faster than easy, so it's marked off plan.
- **TODO(server):** `GET /app/api/v1/runs/{sessionId}` returns
  `postrun.view(...)`, built from the provider's activity details and streams
  (Garmin first), with `coach: {debrief}` when coach_enabled.

## Milestone 8 (Connections, source priority, de-dupe), 27 Nov

- **The server words the connections.** `garmin_mcp/domain/connections.py`
  `view()` takes the runner's connections, source order, this month's runs from
  every source and any pairs kept apart, and returns each provider's card:
  - the state in words ("Connected to Garmin · Forerunner 965 · synced 07:02",
    "Reconnect needed · last synced Tue", "Reads only · synced 06:58");
  - capability chips, each on or off;
  - a reconnect link only where one is needed;
  - the merges `dedupe.merge()` made.

  It takes no coach flag (tested).
- **De-dupe has a twin on the device**, `mobile/src/domain/dedupe.ts`, held to
  the server's answers by `fixtures/dedupe.json`. That fixture covers every
  source order, a pair kept apart, the 60 s / 2% edges, one source never merging
  with itself, and a London run against its UTC copy. Reordering sources or
  pressing "These are different runs" re-merges at once on the phone, the same
  way back-to-back answers at once while dragging; the server stores the change
  and re-merges with the same rule. The device's merge rows are tested equal to
  the server's.
- **Hidden, not deleted.** A merge hides the lower source's copy. "These are
  different runs" keeps that pair apart, and a "Kept apart" list offers "Merge
  them again", so nothing done here is one-way.
- **The phone's health store has the phone's name.** The server sends the route
  `health`, and the app words it: "Via Apple Health" on iOS, "Via Health
  Connect" on Android. The development fixture has one persona per phone (Apple
  Watch, Pixel Watch).
- **Reconnect opens the provider's sign-in on the server.** The server names the
  URL per provider; Garmin's is the existing `/connect`. A provider needing a
  reconnect also comes first on You, in a card whose Reconnect is that screen's
  one brand fill ("Your plan is safe. Runs from here wait until it's back.").
  On Connections the button is secondary, as on the board.
- **Source priority** is a numbered list, reordered by dragging the handle
  (one row's height is one place). For screen readers each row has "Move up"
  and "Move down". The rule's numbers ("within 1 minute and ±2%") come from the
  server's config, so a flag change shows here without an app update. Advanced
  merge rules are Plus: shown, locked, never hidden.
- **Trust.**
  - "What Daash can and can't see" lists, per source, what it can and can't do,
    from its capabilities.
  - "Trouble connecting?" gives four steps and a Reconnect for anything needing
    one; session detail's "Trouble connecting?" opens it too.
  - "Don't see your device? Request it" takes a name.
  - "Tell me when" remembers it was asked.
  - The board's independence line closes the screen.
- **Offline.** The runner's order, kept-apart pairs and requests are kept on the
  device until the server has them, and re-sent with the next change.
- **You** shows only Connections for now. Coach memory or Training preferences,
  Plus, Notifications and Privacy arrive with their milestones.
- **TODO(server):**
  - `GET /app/api/v1/connections` returns `connections.view(...)` from the
    provider registry and `provider_connections` (PR #27 / #30);
  - `POST .../priority {order}`, `.../keep-apart {pairs}` and
    `.../request {device}` store the runner's changes;
  - a reconnect page for providers other than Garmin
    (`/app/connect/intervals`, with the Intervals.icu adapter once its API docs
    are reachable).

## Milestone 9: onboarding (try first, import or four questions, plan reveal, first workout lands), 28 Nov

- **The server works the paces and the plan out; the app shows them.**
  `garmin_mcp/domain/onboarding.py`:
  - `import_summary()` reads 12 weeks of runs: best recent race (by its Riegel
    10K), easy pace (the middle half of runs slower than steady), threshold
    from the race through the pace engine, the usual week over the last 8
    weeks, and the number of runs read;
  - `questions()` does the same from the four answers;
  - `reveal()` runs the existing plan builder (`plan_builder.py`, the PWA's) and
    returns the phases, predicted against target, week 1 and each session's
    `daash.workout.v1`.

  No function takes a coach flag (tested with the other engines). The Coach's
  "why this shape" note comes from the server and never carries a number of its
  own; the rule's line ("Phases follow from your 20 weeks…") always shows,
  because it carries numbers, and the Coach's note is added under it (the
  prompt's "Coach ON adds"). The numbers, the rule's line included, are tested
  equal either way.
- **The four questions work on the phone**, before sign-up, with the shared
  pace engine. `features/onboarding/model.ts` `questions()` is held to the
  server's answers by `onboarding.json` (no race, and a recent half marathon).
- **Nothing to read is said, not guessed.** An import with no runs in 12 weeks
  says "No runs in the last 12 weeks", uses a 10K of 45:00 and says so, shows
  no "0 km · 0 runs" usual week, and the result explains that Adjust goes to the
  four questions. Too few easy runs to read a range takes the easy zone from the
  race, or from the default 10K, and the note says which (it said "from the
  race" with no race before).
- **The goal step reads what people type.** A race day as "24 Apr 2027",
  "24/04/2027" (day first) or "2027-04-24", read back as "Sat 24 Apr 2027";
  days that don't exist and days not after today are refused in words. Targets
  are h:mm for a half or a marathon and mm:ss for shorter races, as
  `plan_builder.goal_secs` reads them, and must be a pace someone could run
  (2:30 to 15:00 per km), so "45:00" for a half isn't read as 45 hours. No date
  picker dependency was added; a native picker is a polish item.
- **Week 1 goes to the watch only through the confirm sheet.** "Send week 1 to
  my watch" stages one Add per session (each carrying its workout) and opens the
  same watch confirm as every other write. The reveal moves on to the landing
  only when the queue has a new batch for that proposal: Back on the sheet
  sends nothing and stays on the plan, and a batch left from an earlier attempt
  can't skip the sheet. The plan itself is saved by the server when it's made
  (`changesPlan: false`); the watch only gets the week.
- **The landing follows the send state**, from the watch queue:
  - sending, then "arrives at the next sync" once Garmin has it;
  - on the watch: the filled 56 pt success check (labelled "Done") and, only
    now, the notification ask (section 8). "Allow notifications" asks the
    system; the answer, or "Not now", is kept on the device, and the landing
    reads it: a runner who answered once (Plan's "Create a plan" later) gets
    "Go to Plan" instead of the ask;
  - Not sent: the send state's own card (words, Resend, Trouble connecting?,
    "Nothing changed on your watch").

  "Go to Today" leaves before it lands; the runner is onboarded either way.
  TODO(M12 or notifications): when the runner left before it landed, ask the
  first time a workout lands later (Today owns that; not built here to keep out
  of Today while M10 and M11 are in flight).
- **The app's first screen** (`src/app/index.tsx`) reads `onboarded` from the
  device's store: not set goes to the welcome, set goes to Today. It's runner
  data, so `signOut()` clears it and a signed-out phone starts at the welcome
  (with Sign in). A phone whose database fails to open starts at the welcome.
- **Garmin not connected.** Connecting is the account (sign-in is Garmin's own
  page, on the server). The import button reads "Connect Garmin" until the app
  has a token, then "Import my runs". Not connected (or an expired Garmin
  login), it opens Garmin's sign-in and says to come back and import. The four
  questions need no account, but making the plan does: the reveal says
  "Connect Garmin to make your plan", with "I've signed in" to try again.
  Offline at either step says so; the answers are kept and the four questions
  still work.
- **Plan's "Create a plan"** (the empty Plan) opens onboarding's goal step with
  `?from=plan`: Cancel goes back to Plan, and the landing ends on Plan ("Go to
  Plan") instead of Today.
- **Back on every step**, none before the welcome and none on the landing.
  Changing the goal or the paces drops a plan made from the old ones; coming
  back to a plan already made from the same answers doesn't remake it. A step
  opened with nothing before it (a link to `/onboarding/reveal`) goes back to
  the step it needs.
- **Copy.** The board's landing line "The rest of week 1 follows each evening
  before" is replaced by what actually happens: all of week 1 is sent at the
  confirm, so the landing says "The other 3 sessions of week 1 follow as the
  watch syncs", or "The rest of week 1 is on it too". Sessions that weren't
  sent are never in that count: they're said ("2 sessions of week 1 weren't
  sent. Nothing changed on your watch for those.") with Resend. Errors use the error
  colour with an icon and words, never colour alone, and no exclamation marks.
- **Conflicts with the boards** (the prompt wins):
  - the Pass 0 boards' "How do you train?" asks for a long run day; the prompt's
    four questions are experience, days a week, recent distance and a recent
    race, so no long run day (the plan builder's default, Sunday, is used);
  - the boards' welcome and try card go straight to the import; the prompt's
    goal is step 1 of 4, so both go to the goal first;
  - the Pass 0 "Connect your watch" and "Stay in the loop" steps are gone:
    connecting happens at the import (or at the plan), and notifications are
    asked only after the first workout lands. So the counter is "Step 1 of 3"
    (goal), "Step 2 of 3" (paces, the questions and the result) and "Step 3 of
    3" on the reveal (with the server's kicker), not the boards' "of 4";
  - the removed Connect board carried section 1's "Daash is independent and not
    endorsed by any watch maker."; it now sits wherever onboarding sends the
    runner to Garmin's sign-in: the paces step, the reveal's Connect and
    Reconnect states, and the welcome after Sign in;
  - the boards' try card says "Like it? Send it to your watch.", but the
    session tried isn't kept (Create free account goes to the goal), so it now
    says "Like it? Make a plan that goes to your watch." and "This try isn't
    saved".
- **Analytics (section 11): deferred.** The app still has no analytics layer
  (HARDENING.md). The events onboarding should send when it has one: each step
  viewed (welcome, try, goal, paces, questions, result, reveal, landed),
  try-builder used, import chosen (import or questions, and import failed by
  kind), first workout landed, notification permission result (granted,
  denied, not now). No health values (paces, races, kilometres) in payloads.
- **Not done here, to keep to M9:** the Sign out button in You (HARDENING.md
  put it with M9; `signOut()` is ready, but You is shared with M10 and M11), and
  keeping onboarding's answers across the app being closed during Garmin's
  sign-in (needed once the sign-in hands the app back a token).
- **TODO(server):**
  - `POST /app/api/v1/onboarding/import` returns `onboarding.import_summary()`
    for the signed-in runner's last 12 weeks;
  - `POST /app/api/v1/onboarding/plan {answers, basis, goal, coach}` returns
    `onboarding.reveal()` and saves the plan (with the Coach's note when
    `coach`);
  - Garmin's sign-in hands the app its token back (a `daash://` link after
    `/connect?app=native`), for Sign in, Connect Garmin and Reconnect.
- **Needs a device build:** `expo-notifications` 58.1.8 (the permission ask). It
  loads only when the ask happens; Jest mocks it. No config plugin is needed for
  the permission alone.
- **Tests:**
  - `features/onboarding/onboarding.test.tsx`: the twin against the fixture,
    race times, race days, the goal check, week 1's proposal, where week 1 is,
    the provider, and every step's states;
  - `src/onboarding.flows.test.tsx` over the real routes and watch queue: the
    first screen both ways, welcome → try → goal → paces → result → reveal →
    confirm → landed → Today, Back on the sheet, Not now, the Coach on, the four
    questions with Adjust and back, nothing to import, offline, not connected
    (import and plan), leaving before it lands, a failed send with Resend, and
    Plan's Create a plan both ways;
  - every onboarding route is in the smoke test's a11y checks, light and dark,
    with the landing on the watch as well;
  - `mobile/e2e/5-onboarding.yaml` for a device; flows 1 to 4 now open
    `daash://today` to start past onboarding.
- **Review fixes (28 Nov).**
  - *The easy pace the runner confirmed is the plan's.* The import's easy
    range (read from their runs) now travels in `basis.easy`, and `reveal()`
    uses it for week 1's easy and long runs, so the result and the reveal show
    the same range (tested in the domain checks and against the fixture).
  - *No race, no prediction.* With nothing imported, or no race answered,
    `basis.assumed` is true: the reveal shows the target but no "Predicted
    now", and its line says "No prediction without a recent race; paces use a
    10K of 45:00 for now." instead of "your 10K". An import with no runs says
    "Phases follow from your 20 weeks." rather than "recent 0 km weeks". New
    fixture cases `reveal_empty` and `reveal_questions`.
  - *Sentence case:* "from your half marathon" (race names inside a sentence
    are lower case, 5K and 10K as they are), in the engine and the twin.
  - *Targets of 10 hours or more* need h:mm:ss, as `plan_builder.goal_secs`
    reads two parts of 10 or more as mm:ss.
  - *Slow replies.* A plan made for paces or a goal that have changed since
    (Back, Adjust) is dropped by the provider (`revealFor`), never shown or
    sent; choosing the four questions, or leaving the paces step, makes a slow
    import stale so it can't push its result over them.
  - *Plan after "Create a plan".* `data/planChanged.ts`: the landing tells Plan
    and Today the plan changed and they refresh, so Plan shows the new plan,
    not "No plan yet".
  - *No back on the landing* on Android either: a `BackHandler` consumes it, so
    week 1 can't be staged twice from the steps.
  - *Sending* has the muted spinner and says "Sending to Garmin"; "next time it
    syncs" is only said once Garmin has it. The reveal's Reconnect state has
    its own words (no plan exists yet, so not "Your plan is safe") and "I've
    signed in", like the signed-out state. The days-a-week chips are
    MIN_TOUCH wide. The empty import's note uses the basis the server sent.
  - *Sign in.* After Sign in the welcome shows "I've signed in": with a token,
    the phone is marked onboarded and goes to Today; without one it says "Not
    signed in yet". The first screen also sends a phone that has a token
    (signed in before onboarding existed, or after Sign in) to Today.
    TODO(server): the token hand-back is still needed for any of this to work
    on a device.
  - *Flow tests and Maestro* type a race day five months from the real clock,
    not 24 Apr 2027.
- **Open questions for the owner (defaults taken):**
  - *Sign in for a runner with no plan on the server.* Default: Today, with
    Plan's "Create a plan"; the alternative is to ask the server whether
    there's a plan and send a runner without one to the goal step. Likewise a
    runner who connected Garmin mid-onboarding and closed the app lands on
    Today next launch (onboarding's answers aren't kept across closing yet).
  - *A prediction from an assumed 10K.* Default: none shown. The alternative is
    to show it labelled "from a 10K of 45:00", which is still a guess.
  - *The session tried before sign-up.* Default: not kept (the card says so).
    The alternative is to carry it in onboarding and offer it after the
    landing, through the watch confirm.
- **Needs a device build (added):** on Android the default notification
  channel is made before the permission ask (Android 13+ shows no prompt
  without one); check the prompt appears on a real Android 13+ phone.

## Milestone 10: Plus, entitlements and widgets

- **Free, trial or Plus, in one pure model.** `features/plus/entitlement.ts`
  holds the tier, when it ends, where it came from (the server, the App Store,
  Google Play or the web) and whether it renews. `resolve()` picks the best
  active answer from the server's, the features flags' `plus` (still honoured,
  so the server can switch someone on) and the purchases the phone's store
  holds: Plus over a trial, then the later end, then the more trusted source.
  An answer that has run out is Free, offline included; the last one is kept as
  `lapsed` so You can say "Your free week ended". A trial counts as Plus for
  every lock.
- **The provider.** `EntitlementsProvider` (in the root layout) opens on the
  answer cached in the kv table, then asks the server and the store, on launch
  and on coming back to the foreground. A purchase unlocks at once and is handed
  to the server to check (once per transaction); the server's checked answer
  then replaces it and is cached. `usePlus()` is what screens read; outside the
  provider (a screen's own test) it falls back to the flags' `plus`. Plan asks
  the server again when Plus changes, so the trend appears straight after the
  free week starts.
- **Plus isn't known until the cached answer is read (review fix).** Before,
  every Plus runner was Free for the first render after a launch: Plan fetched
  the Free plan (no trend) and saved it under the Plus key, and `WidgetSync`
  wrote the Plus message over the widgets. The provider now has `ready` (the
  cached answer read, found or not, or anything that unlocks already known);
  Plan doesn't read or fetch and the widgets aren't written until then.
  `useResource` takes the variant `key` (Plan's `plan:plain:plus`) and an
  `enabled` switch: when the key changes, that variant's cached copy draws, a
  fetch for the old one still on its way is saved under the old key only, and
  the new one is asked for as soon as it ends. The store's own purchases now
  count without waiting for a slow server. Source priority's lock can still
  show for a moment on a cold launch (its data and the cached answer are both
  kv reads); not worth a gate.
- **The store, behind an interface, with no library yet.** `features/plus/store.ts`
  is the `Store` interface (products with the store's own localized price
  strings, purchase, restore, current, listen), `FakeStore`
  for development and tests, and `UnavailableStore` for a release build until
  a store library is added: it offers nothing and says "Daash Plus isn't on sale
  in this version yet." No price is written anywhere in the app; a test scans
  the source for currency amounts (the fake store's sample data aside). Product
  ids: `run.daash.plus.monthly` and `run.daash.plus.yearly`.
- **Needs a device build, and the owner:** an in-app purchase library and the
  products in App Store Connect and the Play Console (a monthly subscription
  with a 7-day free trial introductory offer; the yearly is optional). The
  default recommendation is `react-native-iap` (StoreKit 2 and Play Billing,
  open source; the server checks receipts itself with the App Store Server API
  and the Play Developer API). The alternative is RevenueCat
  (`react-native-purchases`), which takes the receipt checking and the web
  subscriptions off the server but sees every purchase. Either one is a new
  class implementing `Store`, mocked in Jest; nothing else changes.
- **The paywall, value first (P3-Paywall-off/on).** Kicker "Daash Plus",
  "See where your training is heading.", a faded preview of the prediction
  trend (placeholder data, no figures, as on Trends), the Plus items with a
  lock, "Always free" with ticks, and "Try Plus free for 7 days" as the one
  brand fill. With the Coach on it adds "Coach without a daily limit · Free has
  10 messages a day", the number from the flags' `coach_daily_limit`; with the
  Coach off nothing mentions a Coach. Restore purchases is on the paywall and in
  You; the footnote says Plus renews until cancelled and where to cancel, which
  the stores require.
- **The free week sheet (P1-PaySheet).** "How your free week works", the
  timeline Today / Day 5 / Day 7 ("Everything in Plus unlocks", "We remind you
  the trial is ending", "<store price> a month starts, unless you cancel"), "No
  payment due today. Cancel anytime.", "Start free week" (brand) and "Not now",
  and "Plans, Today and sending to your watch stay free either way." Until the
  store answers it says it's asking and Start waits; if the store can't answer
  it says so with "Try again"; without the store's price it never makes one up.
  Cancelled at the store: nothing happens. Ask to Buy: "Waiting for approval.
  Plus unlocks as soon as it's approved." Failed: "The purchase didn't go
  through. You haven't been charged." Someone who has had the free week sees
  "Get Plus" and the price from today instead. Started: the sheet and the
  paywall close, back on the feature that was locked, now open.
- **The day 5 reminder is Daash's (review fix).** The sheet says "We remind
  you", and the stores don't reliably send one. `features/plus/reminder.ts`
  sets a local notification (expo-notifications, already a dependency) two
  days before a renewing free week ends: "Your free week ends in 2 days · Plus
  starts Sat 5 Dec unless you cancel in the App Store." It's set when the free
  week starts (from this phone or the server), taken away once Plus is bought
  or the free week is cancelled or over, and never set or cleared before the
  cached answer is read. It never asks for permission (that stays after the
  first workout lands), so it shows only for runners who allowed
  notifications. Needs a device build to see it fire.
- **Open question for the owner: the reminder without notifications.**
  Default taken: a local notification only, plus TODO(server) a day 5 email
  to the account's address, so "We remind you" holds for runners who said no
  to notifications. Until the email exists, someone who declined
  notifications gets no reminder; the copy stays as on the board.
- **Restore says what it found (review fix).** "Purchases restored." only when
  Plus or a running free week is back; purchases that have all run out say
  "Your free week has ended." or "Your Plus has ended."; nothing at all says
  "No purchases found for this account."
- **Never mid-flow.** The paywall is a root screen opened only by
  `openPaywall()`, from a lock (Trends, Block, Source priority, Widgets) or
  You's Plus row. A test lists exactly those callers, that only the free week
  sheet buys, and that Today, the watch confirm and queue, the builder, post-run
  and onboarding never read Plus or open the paywall. A flow test sends a
  session to the watch as a Free runner.
- **Conflict, board and routes: the paywall opens over the tab that asked.**
  The board draws it inside You's stack with the tab bar. A lock on Plan would
  then jump to You, so it's a modal on iOS (a full screen on Android) over
  whichever tab asked, with a close button, and the free week sheet over it.
- **Plus locks, visible but locked.** A shared `LockedPreview` (faded, hidden
  from screen readers, a lock with "See Plus" and the words) and `PlusRow`
  (lock, words, "Plus"; plain once unlocked):
  - the prediction trend and race-day projection on Plan · Trends (as before,
    now through the shared component);
  - advanced plan styles: a new row on Plan · Block;
  - advanced merge rules on Source priority, with the board's words "Pick the
    source per metric, tune match windows" and today's rule;
  - widgets: a Widgets row in You opens the setup screen, whose previews are
    faded behind a lock for Free.
- **Conflict, board and earlier decision: no named training methods.** The
  paywall board says "Pfitzinger, Daniels and Hansons shapes"; milestone 4 left
  out named methods because they read as an endorsement. The paywall says "More
  shapes for your training block".
- **Conflict, prompt and boards: what Plus holds.** P1-PaySheet and Plus-ios
  list "More than one watch" and "Deeper analytics" as Plus; the prompt says
  connecting all watches and apps is free and lists advanced merge rules
  instead. The prompt wins (as on the P3 boards). Plus-ios's "Continue on the
  web · handled on daash.run" gives way to the in-app trial (the prompt asks
  for StoreKit and Play Billing); a web subscription still counts, as source
  `web`.
- **Owner decision: two Plus features don't exist yet.** Advanced plan styles
  (the server's plan builder has steady, balanced and faster) and advanced merge
  rules (per-metric sources, custom windows) have no engine. Free sees them
  locked as the prompt asks; on Plus the rows open "…come with your Plus and
  arrive in an update." Selling them before they exist is the owner's call;
  the default keeps the locks so the designs hold.
- **Review fix: the paywall doesn't sell them as there.** The paywall items,
  Block's row and Source priority's row now say "Coming in an update", so
  nobody pays for something that isn't built (store review also asks that
  what's sold is delivered). Merge rules' placeholder for a Plus runner is
  `/you/coming`, in You's own stack, rather than jumping into Plan's. Leaving
  them off the paywall entirely is the other option; the owner decides when
  the engines are scheduled.
- **Plus in You (You, YouPlus boards).** A Plus section below Connections.
  Free: "Daash Plus · Prediction trend, plan styles, widgets" (plus "Coach
  without a daily limit" with the Coach on) and "Get Plus". Trial: "Free week ·
  Plus starts Sat 5 Dec" with a Trial badge. Plus: "Since Nov · manage in the
  App Store / in Google Play / on daash.run" with an Active badge. Then
  Widgets, and Restore purchases (Free) or Manage subscription.
- **Conflict, prompt and boards: where Plus sits in You.** The Plus-ios and
  YouPlus boards put Plus above Connections; the prompt (section 4) orders You
  as Connections, Coach memory or Training preferences, then Plus. The prompt
  wins: Plus is below Connections (and goes below Training preferences or
  Coach memory when they arrive).
- **Manage goes where the money goes (review fix).** `manageLink()` picks the
  place from the entitlement's source: a web subscription opens the daash.run
  account page, an App Store or Play subscription that store's subscriptions
  page (whichever phone it's opened on). A grant from Daash (the flags'
  `plus`, or a server answer that names no source) has nowhere to manage it:
  no Manage row or button, and You says "Since Nov".
- **Widgets read a snapshot, never the server.** `features/widgets/snapshot.ts`
  builds what every widget draws from the phone's cached Today (with the watch
  queue's state applied) and the cached plan's block: the readiness word,
  number and decision; today's session ("Today · Tempo", "3 × 2 km", "on
  watch"); the countdown ("London", 148 days, "Sat 24 Apr", and the race day,
  so the widget counts down without the app); the week strip ("Week 4 of 21 ·
  Base", "This week · 28 of 54 km"); and the lock screen's circular and
  rectangular lines. Old data says "Updated 07:02", "Updated yesterday" or
  "Updated 26 Nov" (times, not "2h ago": a widget isn't redrawn every minute),
  and a session from an earlier day is named by its day. Free gets "Widgets come
  with Daash Plus. Open Daash to try it free." and no data, or "…Open Daash to
  see Plus." once a free week or Plus has run out (the store won't give the
  free week again); nothing cached gets "Open Daash to load today." Never a
  blank.
- **`WidgetSync`** (under Today's provider) rebuilds the snapshot whenever Today,
  the queue or Plus changes, and every 15 minutes, and writes it through the
  `WidgetBridge` only when it draws something different, then reloads the
  widgets. It doesn't overwrite a good snapshot with "Open Daash" while Today is
  still loading. The setup screen draws its previews from the same builder.
- **Native widgets: written, not built.** `mobile/native/widgets/` holds an Expo
  module (`DaashWidgets`: write the JSON to the App Group's shared defaults or
  to shared preferences, then `reloadAllTimelines` / Glance `updateAll`), the
  WidgetKit extension (Today small and lock screen circular and rectangular,
  Countdown small, Week medium) and the Glance widgets (2×2 Today, 4×2 week,
  countdown). Each reads the same snapshot and works out "Updated" and the
  countdown at draw time with the app's rule. A config plugin adds the App
  Group and writes the widgets' colours from the design tokens at prebuild
  (`DaashTokens.swift`, `DaashTokens.kt`, git-ignored). None of it is in the
  build yet, and all of it is marked UNVERIFIED. Jest checks the plugin's pure
  parts, that every colour the widgets name is a token, that no colour is typed
  by hand, and that both sides use the app's key and 30-minute rule. The app
  side does nothing when the module isn't there.
- **Needs a device build** to switch the widgets on: move `native/widgets` to
  `modules/daash-widgets` (autolinked), add `@bacons/apple-targets` and copy
  `ios-extension/` to `targets/widgets/` for the extension target, add the
  plugin to app.json, and register the App Group `group.run.daash.app` for the
  app and the extension in the Apple developer account. Then check every widget
  in light and dark, at large text, with old data and with none.
- **Conflict, prompt and platform: no At a Glance.** Android's At a Glance has
  no public API for other apps' content, and phones have no lock screen widgets;
  Android gets the three home screen widgets. Glance has no arc, so the 2×2
  shows the number and the word in the decision's colour instead of a ring.
  The setup screen, the paywall and You say so on Android (review fix): "home
  screen" rather than "home and lock screen", no At a Glance instructions, and
  no lock screen preview; a test checks the Android copy names only those.
- **Analytics.** `analytics/analytics.ts` is a small `track()` with two events,
  `paywall_viewed {from, coach}` and `trial_started {product, trial}`; any key
  that looks like a health value is dropped. Nothing is sent yet: a later
  milestone points the sink at PostHog.
- **TODO(server):**
  - `GET /app/api/v1/entitlements` answers `{tier, expires_at, source, since,
    will_renew, product_id}` from checked receipts and web subscriptions, and
    always names the `source` (app_store, play or web) when there's a
    subscription behind it, or Manage has nowhere to go. The features flags'
    `plus` stays a plain boolean (a grant); the flags parser drops anything
    else, so the full answer comes only from this endpoint;
  - the daash.run account page (`https://daash.run/account` in the app) where a
    web subscription is managed, before anything is sold on the web;
  - a day 5 email for a renewing free week (see the open question above);
  - `POST /app/api/v1/entitlements/verify {platform, product_id,
    transaction_id, receipt}` checks a purchase with Apple or Google and answers
    the entitlement it records;
  - App Store Server Notifications and Play real-time developer notifications,
    so renewals, refunds and cancellations reach the server;
  - the plan endpoint keeps leaving the trend out for Free, now for an expired
    trial too;
  - Today could send the race as `{name, date}` instead of the app reading
    "London in 148 days" for the countdown.
- **Development.** A development build is Free (so the locks show) with
  `FakeStore`, whose free week works; `EXPO_PUBLIC_DAASH_PLUS=1` makes it Plus.

## Milestone 11: Coach

The thread, context tags, inline charts, proposals, memory, limits and the reply
guard, 28 Nov. Built on `claude/native-app-m9`, alongside M10 (Plus).

- **Built against the live chat pilot, as it is on `main`** (`chat_pilot.py`,
  not changed and not copied here). `features/coach/api.ts` uses its real
  shapes:
  - `GET /app/api/chat` gives `{messages: [{id, role, text, status, feedback?,
    tool?}], starters}`;
  - `POST /app/api/chat/send` streams server-sent events: `start`, `delta`,
    `tool`, `pending`, `done`, `error`;
  - `POST /action`, `/feedback` and `/new`;
  - `GET /app/api/features` answers `{"chat": true}`, which `parseFlags` reads as
    `coach_enabled`. The root layout now asks it; before, only a dev stub did.

  The client follows `api/client.ts`: the same token and typed errors. Two
  errors are added: `CoachLimitReached` (the 429) and `CoachUnavailable` (the
  404). Reads retry. A send is one try, never retried by the app, because a
  second try could ask the model twice. The stream is parsed by a pure function
  (`sse.ts`), tested with the bytes cut every way, in CRLF, and with a last frame
  that has no blank line after it. It streams over `expo/fetch` (part of `expo`),
  loaded only when a message is sent.
- **The phone never confirms through the pilot's `/action`.** The pilot's
  Confirm runs the stored call on Garmin directly. That would skip the watch
  confirm sheet and the queue. So:
  - "Not now" is the only call to `/action`, as a decline;
  - a Coach proposal opens the shared diff (`/coach/new-week`), then the same
    watch confirm, then the queue;
  - the card follows that batch: Sending, "Changed · arrives at next sync", On
    your watch, or Not sent with Resend and "Do it in Plan".

  The decision type has no `confirm`, and `src/states.test.tsx` checks the
  source for it. A pending event may carry `request` (a `ProposalRequest`:
  easier, rest, swap, trim, life) or a whole `proposal` (`proposal.view`'s
  shape). The pilot sends only `tool` and `text` today. Such a card is finished
  in Plan (the prompt: a proposed change can always be finished in Plan), and
  `inPlanFor(tool)` says where and what to do: a new or changed workout opens
  Plan's builder ("Build it in Plan"); a move, schedule or removal opens Plan
  with "open the session above and use Move, Swap or Skip". It also offers Not
  now.
- **One thread.** There's no New chat. "Clear conversation" is the pilot's
  `/new`, behind a confirm. It leaves memory and the plan alone. The thread is
  cached on the phone (`coach:thread`), so it opens offline, and the phone keeps
  the last 14 days (section 16's default). Days before today collapse into
  "Earlier · N messages since Wed · Show". The pilot sends no time on a
  message, so a message this phone didn't write or see written has none
  (`at: null`, never "now"): it folds into Earlier ("Earlier · 24 messages",
  with no "since" when any earlier message is undated), keeps the server's
  order, and stays while the server sends it (14 days can't be judged without a
  time). Clear conversation clears the phone's copy only once `/new` succeeds;
  offline, the sheet says clearing needs a connection, and a failure keeps the
  thread and says so.
- **Context tags** come from four places:
  - Today's "Ask why": "From Today · Back off";
  - post-run's "Ask about this run": "About · Wed 25 · 6 × 800 m";
  - session detail's new "Ask about this session", in its Coach card;
  - a run with no debrief and a session with no Coach note still show "Ask
    about this run / session" in their own Coach slot (`coach-ask`): no server
    sends debriefs or notes yet, so without it most runs and sessions had no way
    in;
  - the diff's "Ask about this".

  The tag sits on the composer, removable, until sent. It's sent as `context`;
  the pilot ignores keys it doesn't know.
- **Source chips** come from the `tool` events the pilot already streams:
  get_readiness becomes "Readiness · Garmin", get_sleep_data "Sleep · Garmin",
  and so on. The phone keeps them with the message.
- **Inline charts.** The app draws them (`InlineChart.tsx`, a line with the
  runner's range shaded, or bars per rep) from a `chart` that comes with the
  reply. The server builds it from what its tools returned, never from the
  model's words. The summary is what a screen reader says, and marked points are
  said in it too. A marked point is drawn with a shape as well as a colour: a
  ringed point on a line, an outlined bar with a dot above it and a bold value. The pilot sends no chart yet. The development stand-in builds
  its charts from the persona's fixtures: HRV from Today, peak heart rate per rep
  from Wednesday's run's series.
- **Prompt chips** are the server's `starters` (the pilot's five fixed ones). They
  sit docked above the composer, outside the thread's scroll, so they never
  cover it. The send button is the up arrow on iOS and Material send on
  Android. It's neutral (the Coach has no hue) and disabled when the field is
  empty, while a reply streams, offline, or at the limit.
- **The daily limit.** Free gets `flags.coach_daily_limit` (10); the composer
  says "7 left today". Today's sends are counted apart from the thread, in kv
  (`coach:sent`, `{day, count}`), one per server `start` event (the server
  stored the question). Clear conversation keeps the count; signing out clears
  it with the rest of kv. Used today is the larger of that count and the
  thread's messages known to be from today (once the server sends `at`, that
  includes another device's). An undated message never counts. The server's
  429 counts too, for the rest of that day. At the limit:
  - the banner opens a sheet ("That's today's 10", "See Plus", "Tomorrow is
    fine");
  - a proposal already made can still be reviewed and sent.

  Plus, or a running free week, has no limit and no count: the Coach reads
  M10's `useEntitlement()` (see "Integration with milestones 9 and 10" below).
  "See Plus" opens M10's paywall with `from=coach`.
- **Offline.** A banner says "Offline · the Coach needs a connection. Your plan
  and runs still work", with Try again; sending is off and the cached thread
  shows. A send that never left is marked "Not sent. No connection." with "Send
  again". **Chose disabled over queued**: a queued message would go later without
  the runner seeing it go, which is a retry by another name.
- **Errors** use the app's words, by code. The pilot's own words say "The AI
  service isn't answering", and section 12 keeps "AI" to the Coach's name and
  settings. What was written before the error stays, and Try again asks the same
  question again (it's the runner's press, and it counts toward the limit). A
  connection drop or timeout after the server's `start` is the same: the
  question stays sent (the server has it), the words so far stay, marked cut
  off, with Try again. Only a send that failed before `start` is "Not sent"
  with Send again.
- **Signed out, or Garmin needs a new sign-in.** The sign-in banner
  (`FailureBanner`, with Sign in or Reconnect) shows over the thread, cached or
  empty, and sending and Send again are off until it's fixed. Before, a cached
  thread showed nothing and Send again failed in a loop.
- **The reply guard** (`garmin_mcp/domain/coach_guard.py`, with its twin
  `mobile/src/domain/replyGuard.ts`). It flags four kinds:
  - causes ("because your HRV dropped, you are…", "in line with your short
    sleep", "that's why");
  - medical advice ("ice it", "take ibuprofen", "run through the pain");
  - diagnoses ("sounds like shin splints", "you're overtrained");
  - prescriptive certainty ("you must", "definitely", "will be fine").

  `unmatched_numbers` returns the numbers in a reply that aren't in the payload.
  The corpus has 22 good replies, 30 bad ones and 7 number cases, each labelled
  by hand, in `scripts/domain_fixtures.py`. After review the rules were
  narrowed and widened: "treat" is medical only next to a body part or injury;
  "explain" and "leads to" alone aren't causes; "if you need to" is an offer,
  not an order (the match takes the word before "you" with it, as there's no
  lookbehind in the shared subset); "never do" is an order only at the start of
  a sentence. Causal verbs after a body signal ("your short sleep made…", "is
  why"), "from the alcohol" and hedged diagnoses ("probably getting ill", "a
  sign of a stress fracture") are caught. Each example is a labelled case. That script writes
  `fixtures/coach_replies.json` with the rules in it. The app's twin reads those
  rules from its copy (`mobile/src/domain/replies.fixture.json`), so the two
  can't drift; both are tested against the hand labels. The twin is called
  `replyGuard` because `domain/pure.test.ts` keeps the word "coach" out of the
  app's engines.

  The phone runs the check on every reply it shows. It marks a failing reply
  with a note: "Read this as an observation…" for a cause, or the medical line
  for medical advice or a diagnosis. It never rewrites a reply or its numbers.
  The check is conservative, so it will sometimes mark a fine sentence (a
  planning "because" next to a body word); a reply with no finding passes as is.
- **The Coach ON/OFF parity check** (in `src/smoke.test.tsx`, so it uses the one
  list of screens):
  - every screen renders with the Coach on and off, and every number drawn
    (text and screen-reader labels) must be identical once the Coach's blocks
    are set aside;
  - those blocks are `coach`, `coach-line`, `ask-why`, `coach-note`,
    `coach-ask`, `debrief` and `diff-coach`. Native tabs draw every tab, so the
    Coach tab is one of them;
  - every number in a Coach block must be on its screen or in what the server
    sent that screen (its own fixture, not every fixture at once); the Coach
    tab's numbers must be in its thread's data (the run asked about, the plan
    and proposals, charts and tags, the runner's own words), never a reply's
    words. A negative case shows the check fails an invented number;
  - `coach.flows.test.tsx` 13 checks the Coach tab's replies, charts, cards and
    memory notes against the data the stand-in Coach read and the structured
    parts of what it sent; the streamed reply text is not its own payload, and
    a reply with an invented number fails.
- **Changes to earlier milestones, to hold that:**
  - the diff now keeps "Rule used" with the Coach on and adds the Coach's card.
    The prompt says "Coach ON adds an observation card". M5 replaced the rule,
    which dropped its numbers ("about 60% of the time") with the Coach on;
  - post-run's development debrief now uses only the run's own numbers ("Reps
    averaged 4:01, the fastest and slowest 6 s apart…"). Before, it said "3:58 to
    4:04", which aren't in the payload. The two post-run snapshots with the
    Coach on changed for this;
  - You's memory row carries no count.
- **Memory and Training preferences** (`features/coach/memory.ts`, in SQLite's
  `memory_items`): the same items with the Coach on and off. The items are goal,
  training days, injuries you mentioned (on) or niggles to plan around (off),
  preferences, travel, and notes. Tone and "Clear memory" are the Coach's only:
  with it off they're hidden, not lost. With the Coach on, an item saved from
  the chat says "you said in chat". The thread shows "Saved to memory: …" with
  Edit (the `memory` event; TODO(server)). Items can be viewed, edited, deleted
  and added in You, with clearing behind a confirm. Nothing in memory is a number
  any screen uses. Two memory events in one reply both stay (changes build on
  the current items). Training preferences say "Kept on this phone. Edit or
  delete anything." and the row "What you've told Daash": the board's "Daash
  plans around these" isn't true until the server reads them (see For the
  owner).
- **Turning the Coach off.** You has a Coach switch, only when the server offers
  the Coach. It's the one place that says "AI" ("AI, run on Daash's server").
  The choice is kept on the phone (`coach:off`, runner data, so sign-out clears
  it). `FlagsProvider` gives `coach_enabled` as the server's flag unless the
  runner switched it off. Off hides the tab and every Coach block, and You shows
  Training preferences. No number changes; the parity test and coach flow 12
  check that.
- **Conflicts with the boards** (the prompt wins):
  - the Pass 1 thread boards have a "New chat" pill: gone (no New chat);
  - CoachEmpty's Today card (readiness, the run done, tomorrow) isn't built. It
    repeats Today, the prompt doesn't ask for it, and the Coach tab doesn't have
    Today's data. The empty thread says what the Coach reads, with the medical
    line and the chips;
  - CoachThread, CoachOffline and CoachError say "Friday was easy, so your legs
    will be fine", which is a cause and a certainty. The corpus has it as a bad
    reply;
  - P3's "If you go, cap reps at 4:30 /km" uses a number that isn't in the plan
    (4:23–4:28), so the fixtures don't use it;
  - ComponentsCoach says "Red is the Coach's colour on controls", but the prompt
    gives the Coach no hue;
  - YouCoach's "Delete chat history" cleared the thread and memory together. The
    prompt splits these into Clear conversation (in the menu; memory stays) and
    Clear memory (in You);
  - the memory board's footer ("Not medical advice. For pain or injury, see a
    physio or doctor.") is the prompt's sentence instead. The memory boards'
    "sub-2:40" goal is the persona's 3:15;
  - the composer's dictation button isn't built: it needs a speech module, so a
    device build.
- **Analytics (section 11): deferred.** The app still has no analytics layer.
  Events to send when it does:
  - Coach message sent (starter or not, the context kind, no text);
  - thumbs;
  - proposal reviewed, declined or sent;
  - limit reached;
  - Coach switched off.
- **TODO(server), for `chat_pilot.py` on main (not changed here):**
  - run `coach_guard.check` on each finished reply, and `unmatched_numbers`
    against the tool results the model read; regenerate once on a finding;
  - the `pending` event carries `request` or `proposal`, and `/action` takes
    `{decision: "applied", batch}` once the phone has sent it;
  - read `context` from the send body into the model's context, and store it
    with the message;
  - messages carry `at`, `context`, `sources` and `chart`, with a `chart` event
    built from tool results and a `memory` event when something is saved;
  - `starters` generated from today's data;
  - the daily cap is 10 for free and none for Plus (the pilot's
    `CHAT_DAILY_MESSAGE_CAP` is 500 for everyone), with the free limit in the
    features answer;
  - the model reads the last 14 days plus memory (the pilot reads the last 20
    messages);
  - `GET/PUT/DELETE /app/api/v1/memory`, and Training preferences feeding plans;
  - the Coach switch stored per person;
  - the pilot's error words without "AI".
- **Needs a device build:** streaming over `expo/fetch` (tested here with a
  stand-in body that streams), and the keyboard pushing the composer up
  (`KeyboardAvoidingView` on iOS).
- **For the owner** (defaults taken):
  - Clear conversation uses the pilot's `/new`, which keeps the old thread on the
    server. It could delete it instead;
  - the free count runs on the phone's local day; the server's runs on London
    midnight;
  - the guard marks a reply but doesn't hide it;
  - the free count is kept on the phone, so a reinstall or a second phone starts
    at 0 until the server enforces 10 a day (TODO(server) above). Default: the
    phone's count, plus the server's 429;
  - Training preferences: the board says "Daash plans around these"; the app
    says "Kept on this phone" until plans read them. Default: the neutral copy;
    switch back when `GET /app/api/v1/memory` feeds the plan. "Niggles to plan
    around" (the board's category name) is kept;
  - an undated pilot message folds into Earlier on first open (the whole web
    thread, until the server sends `at`). Default: fold, rather than call it
    today.
- **Tests:**
  - `coach.test.ts`: the thread model, the stream, the client against the
    pilot's answers (no retry on send), the stand-in's numbers, memory labels;
  - `coachView.test.tsx`: dark and light snapshots on both platforms, the one
    brand fill, every state;
  - `coach.flows.test.tsx`: 13 flows over the real routes and queue, plus,
    after review: a pilot thread with no times (10 left, folded into Earlier),
    send 10 then clear (still locked), a stream that drops after `start`,
    signed out over a cached thread, a clear the server refused, Ask from a run
    with no debrief and a session with no note, and a workout proposal finished
    in the builder;
  - `replyGuard.test.ts`, and `tests/domain_test.py`'s guard checks;
  - every new route in the smoke test's SCREENS, light and dark, with the a11y
    checks.

### Integration with milestones 9 and 10 (28 Nov)

M10 and M11 were built in parallel off an early M9 commit; M9 was finished
afterwards. The stack is now hardening → M9 → M10 → M11, each branch merged
into the one above it.

- **The Coach's limit reads the real entitlement.** M11's local stub
  (`features/coach/plus.ts`, `isPlus()` over `flags.plus`) is gone;
  `CoachProvider` reads `useEntitlement()`. Plus or a running free week: no
  daily limit and no count. Free, or a free week that has ended: 10 a day (the
  flags' `coach_daily_limit`). Until the cached entitlement is read no limit is
  applied, so a Plus runner never sees the banner flash up; the server's 429
  still stops a free runner's eleventh message.
- **The limit sheet and the paywall point at each other.** "See Plus" on the
  limit sheet opens Daash Plus (`openPaywall(router, 'coach')`, the runner's own
  tap, never mid-flow) with the Coach's row, "Coach without a daily limit · Free
  has 10 messages a day", first; both read the same flags number. The
  paywall's opener check (`src/plus.flows.test.tsx`) now lists the limit sheet.
- **You** has, in the prompt's order: Connections, then the Coach switch and
  Coach memory (or Training preferences), then Plus. `YouView` takes the Coach
  section as children and Plus as `plus`.
- **The root** nests `FlagsProvider` (with the Coach choice store and the live
  features fetcher from M11) → `EntitlementsProvider` → `WatchProvider`.
- **Coach ON/OFF parity:** the paywall's Coach row exists only with the Coach
  (like the tab), so its "10" is set aside in the parity check as chrome, not a
  number that changes.

## Milestone 12: accessibility, performance and polish

Built on `claude/native-app-m11` (which holds every earlier milestone), 28 Nov.

### Expo Go (the owner's next test)

- **The app runs in the store Expo Go app on sample data.** `src/data/sample.ts` decides it:
  sample data is on with `EXPO_PUBLIC_DAASH_FIXTURE=1`, or in Expo Go when no token was on
  the phone at launch (read once, before the providers mount). Every source's fixture
  fallback now asks `fixturesOn()` (development builds, as before, or sample data) instead
  of a constant fixed at import time. A signed-in phone in Expo Go uses the real paths.
- **Sample data says so.** A small neutral "Sample data" label (an outline and an info
  mark, no colour of its own; spoken as "Sample data. These numbers are from a sample
  runner, not yours.") on Today and at the top of You. The first screen skips the sign-in
  wall and goes to Today; You has "Try onboarding" to walk the steps on the persona's
  answers. Sample data has the Coach on, talking to its stand-in, the stand-in Garmin for
  sends (nothing reaches a real watch) and the fake store for Plus.
- **Only Expo Go's native modules.** `src/expoGo.test.ts` checks every dependency and every
  import under `src/` against a list kept in the test (expo-*, react-native-svg, reanimated,
  worklets, gesture-handler, screens, safe-area-context, segmented-control, expo-updates,
  JavaScript-only packages), that native code outside Expo Go is only loaded through
  `requireOptionalNativeModule` (just `DaashWidgets`, which falls back to writing nowhere),
  and that the store is the fake or the unavailable one (no purchase library).
- **Notifications in Expo Go:** permission and local notifications only. Push tokens are
  asked for in one place (`features/settings/push.ts`, not called yet: TODO(server)), which
  returns nothing in Expo Go, because Expo Go on Android has had no remote push since SDK 53
  (asking throws there).
- **Widgets setup in Expo Go** says widgets need the App Store / Google Play build.
- **Publishing from GitHub.** `app.json` has `runtimeVersion: {policy: "appVersion"}`, the
  project's `updates.url` and `extra.eas.projectId`; `expo-updates` 58.0.22 is a dependency
  (Expo Go ships it; nothing in the app calls it). `eas.json` has development (needs
  `expo-dev-client`, not installed), preview (APK) and production profiles.
  `.eas/workflows/expo-go-preview.yml` publishes an update to branch `preview` on each push
  to `claude/native-app-m12`. This container can't reach expo.dev, so the workflow hasn't
  been seen to run. `npx expo config --type public` shows the three fields offline, and
  `npx expo export` for both platforms succeeds.
- **Smaller updates.** The fonts are imported by face, so a bundle carries three font files
  instead of about sixty (the Material Symbols font comes with `expo-symbols`).
- **The slug stays `daash`** (not confirmed: if the Expo project's slug differs, change it in
  `app.json`). No `owner`.
- **Docs:** `docs/native-app/EXPO_GO.md` (from a computer, from the phone, sample data on
  and off, what needs a development build) and `mobile/.env.example`.

### The Coach and the keyboard

- **The screen never moves.** The Coach's composer sits directly on the keyboard; the header
  stays; the thread shrinks and stays on the newest message; the tab bar hides while the
  keyboard is up. `ui/keyboard.ts` keeps one keyboard state from the same events in Expo Go
  and a store build (`keyboardWill*` on iOS, `keyboardDid*` on Android). The dock is padded
  by how far the keyboard actually covers the screen, measured in the window, so it works
  whether Android shrinks the window (`softwareKeyboardLayoutMode: "resize"`, now explicit
  in `app.json`) or draws edge to edge under the keyboard. `KeyboardAvoidingView` (iOS only
  before, and measuring against its parent rather than the window) is gone.
- **The native tab bar can hide:** `NativeTabs` takes `hidden`, set while the keyboard is up
  (with the hide animation off so the composer doesn't jump). Expo Go uses its own Android
  manifest, so `softwareKeyboardLayoutMode` only applies in a build of our own; the overlap
  measurement covers both.
- **Kept scrolled to the end** with a normal `FlatList` that scrolls to the end on layout
  and content changes while the runner is at the bottom, rather than an inverted list:
  inverted lists have had reading-order problems with TalkBack and VoiceOver.
- **Tests:** `src/keyboard.test.tsx` sends the real Keyboard events and checks the dock's
  padding, the tab bar's hidden state, that the screen itself gets no offset, and that the
  thread scrolls to the end when it shrinks. **Needs a device** (WALKTHROUGHS.md, flow 6).

### The snapshot matrix and the walkthroughs

- **Every screen × light/dark × iOS/Android × 100%/200% text × Coach on/off.** `src/matrix/`
  (four suites, split by tab so Jest runs them side by side; the cases are in
  `src/testing/screens.tsx`). Each cell is written as what a screen reader finds, in reading
  order (role, name, value, state, live region), plus the brand fills and any clipped or
  line-capped text, into one file per screen in `src/__matrix__/` (cells that read the same
  are written once, listing every cell they stand for). 55 screens, about 820 cells; the
  files are about 460 KB together. Rewrite with `npx jest src/matrix -u`.
- **What every cell must hold:** one brand fill where the screen has a primary action (pinned
  per screen, Android-only for Plan's FAB), at most one otherwise, none behind a sheet; no
  scalable text in a box of fixed height; every control at least 44 pt / 48 dp; with the
  Coach off, none of its blocks and no mention of it; the same numbers with the Coach on and
  off. **The screen list comes from the routes on disk**: a route with no case fails.
- **The test window is now a phone.** React Native's Jest preset reports a 750 × 1334 window
  at 200% text, so every route test so far drew Today's large-text layout. `testing/setup.ts`
  starts every test at 390 × 844, 100%, in UTC; the matrix sets 200% itself. Times are frozen
  in the matrix (Fri 27 Nov, 12:18) so the files are the same on every run.
- **Focus order:** `src/focus.test.tsx`, per screen: what it is before any action (a modal's
  Close aside), nothing focusable unnamed, no two controls with the same name, the primary
  action reachable, and each sheet a native form sheet (from its stack's options: the system
  keeps VoiceOver and TalkBack inside it and closes it with Escape or back).
- **`docs/native-app/WALKTHROUGHS.md`**: the VoiceOver and TalkBack script for the seven key
  flows, with what a tester should hear, the keyboard check and reduce motion. All of it
  needs a device run.
- **Line caps at 200% are recorded, not failed.** Plan's week rows cap each line at two
  lines (so the week still fits), the Coach card's and the diff's day rows likewise, and
  the matrix lists them under "cut to lines". Everything is said in full to screen readers.

### Accessibility fixes the matrix and walkthroughs found

- The readiness sheet said "…with or without the Coach" with the Coach off. With it off it
  now says "Readiness is worked out by fixed rules from your own data."
- Clear memory was reachable with the Coach off (a link to `/you/memory-clear`); it now goes
  to Training preferences, which has no Clear.
- Section titles (Connections, Always free, Rule used, Steps, Week 1, the Coach's kickers…)
  are headings, so heading navigation works.
- Send status is announced when it changes (sending, accepted, on the watch, not sent), on
  both platforms, once per change, by the watch queue (`WatchProvider`), not by each status
  on screen: Today, a session and the Coach's card can all show the same send, and native
  tabs keep them mounted. The status views carry no live region, so TalkBack doesn't say it
  twice. An automatic retry that fails the same way isn't said again (review fix).
- The Coach's reply had a live region on its text, which would read every streamed word.
  The "Reading your data" / "Writing" status is the live part now, and the finished reply is
  announced once.
- The "Changed your mind? · Remove from watch" toast doesn't time out while a screen reader
  is on (8 seconds wasn't enough to reach it).
- Reduce Motion: the Plan week changes in place instead of sliding.
- The welcome's example structure bar said "Session structure:" with nothing after it.
- Repeats are read as "Repeat 10 times" (the label already there; the matrix now reads Text
  labels as a screen reader does).
- The diff printed a server rule's full stop twice ("…like rest..").
- `ui/motion.ts` (reduce motion, announcements, screen reader on) and `ui/List.tsx`
  (grouped rows, section headings, switch rows) are shared.

### Large layouts

- **The "L" boards are light-mode boards, not landscape.** TodayL, PlanL, SessionL-android and
  YouL-android are 390 × 844 light variants of Today, Plan, Session and You (the matrix covers
  light on every screen). None shows two columns, so none were added. P3-Today-xl is Today at
  200%, which the matrix covers (Today's stacked layout above 150%).
- **Wide windows keep one readable column**: content is capped at 680 pt and centred
  (`READABLE` in `ui/Screen.tsx`) on tablets, foldables and Android in landscape; phones are
  unchanged. iOS is portrait, phone only (`supportsTablet: false`). `src/layout.test.tsx`
  checks nine screens at 1024 × 768, 844 × 390 and 390 × 844. Plan's week pager is full width
  by design (it pages by the window's width). A two-column tablet layout needs design.

### Performance

- **`docs/native-app/PERFORMANCE.md`** has the detail and the device checklist. Pinned by
  `src/perf.test.tsx`: Today doesn't redraw on a watch-queue tick that doesn't touch what it
  shows; a streamed token redraws only its reply; scrubbing a post-run chart doesn't redraw
  the map or rebuild the lines. The Coach thread is virtualised. Nothing was timed on a
  device.

### Polish

- **Sign out** (deferred from M9) is in You, behind a confirm sheet ("Sign out?", what goes,
  "Nothing changes on your watch.", Sign out / Stay signed in). It uses `api/session`
  `signOut` (the token and every runner table and kv key go; appearance stays) and ends at
  the welcome.
- **Appearance** (the prompt's dark-mode override in You, section 3): Same as the phone,
  Light or Dark, kept on the device.
- **You** now reads, after Plus: Runs (Activities), Settings (Units and zones,
  Notifications, Appearance), Privacy (What Daash uses, Help), Sign out, the version and
  "Daash is independent and not endorsed by any watch maker."
- **Notification copy** for the four kinds of section 8 is in
  `features/settings/notifications.ts`, with the boards' examples as tests ("Run done · 9.2 km
  at 5:14 /km", "Sent to your watch", "Plan updated … Nothing sent to the watch yet."). With
  the Coach on, the run summary's line is the debrief; its title and numbers are the same.
- **Conflict, boards and prompt:** the Notifications boards have a "Missed: Full body (runner)
  v4 · Yesterday's strength did not happen" notification. The prompt's four kinds don't
  include it and rule out guilt copy, so there's no missed-session notification.
- **Removed** `today/next`, a placeholder route nothing linked to ("Coming in a later
  milestone").

### Sketched routes

Each is a real screen with the shared UI, the board's title and structure, reachable from
where the Sitemap puts it, and a `// SKETCH:` line at the top. None writes to the watch.
All are in the matrix, the focus test and the smoke list.

| Route | Board | Still needs |
|---|---|---|
| `/crew` | Sitemap (Crew: This week · nudge · invite) | It was an empty screen. TODO(server): `GET /app/api/v1/crew` (this week's easy and long runs, the invite link, nudges); then the This week list, Invite (share the link) and Nudge. The crew itself stays the web feature (out of scope to redesign). |
| `/you/activities` (and `/you/run/[id]`) | Sitemap ("Activities is reached from Today, Plan and You") | TODO(server): `GET /app/api/v1/runs` (12 weeks, each with the session id post-run opens by). Development builds and sample data list the persona's runs. A board for the list. Reached from You only so far; Today's week strip and Plan's done days already open runs. |
| `/you/training` (Units and zones) | You-ios (Training) | TODO(server): `GET /app/api/v1/profile` (units, week start, heart rate zones, goal race from Garmin). Shows where each comes from, never the board's sample values. |
| `/you/notifications` | Notifications-ios / -android, prompt section 8 | The four switches are kept on the phone (`notify:prefs`). TODO(server): the pushes themselves and `POST /app/api/v1/push-token` with these choices; a device build to see them. Doesn't know yet whether the system permission is off (the "off in your phone's settings" line is ready). |
| `/you/privacy` | You-ios (Privacy) | TODO(server): `DELETE /app/api/v1/account` (disconnects Garmin, deletes the crew data too), then a confirm. Export stays Garmin Connect's. |
| `/you/help` | You-ios footer ("Privacy · Help") | No board. Holds the trust sheets, the medical line and the version. Needs design and a support contact the owner chooses. |

Not sketches: `/you/appearance` and `/you/sign-out` are finished.

### Flows (prompt section 13)

- The five end-to-end flows each have a flow test over the real routes and queue
  (`src/flows.test.tsx` 1 to 4, `src/onboarding.flows.test.tsx` 2) and a Maestro flow in
  `mobile/e2e/`; `src/e2e.test.ts` ties them together. **Maestro (or Detox) needs a device
  and a development build**: a follow-up.

### Open questions for the owner (defaults taken)

- *An offline banner.* The Offline and Interaction boards show a banner under the title
  ("Offline · showing Garmin data as of 11:53. Sending to the watch waits for a
  connection."). The prompt says stale data shows "Updated 2h ago". Default: the "Updated"
  label, plus the send state's own words when a send can't go. A banner is small to add.
- *Sync progress.* The Syncing board shows "Syncing with Garmin… 2 of 7 sessions checked".
  The app polls the queue but has no progress count from the server. Default: not shown.
- *You's profile header and Assistants.* The You boards open with the runner's name, device
  and sync line, and an Assistants section (Claude, ChatGPT connected). Neither has an
  endpoint (TODO(server): profile; connected MCP clients). Default: not shown; Connections
  carries the device and sync.
- *Text above 200%.* Still capped at 200% (HARDENING's question 2). The matrix shows every
  screen holds at 200%; larger sizes need the stacked layouts designed.
- *Sample data in Expo Go.* Default: on whenever nobody's signed in, with the label. The
  alternative is only with `EXPO_PUBLIC_DAASH_FIXTURE=1`.

### Needs a device

The VoiceOver and TalkBack walkthroughs (WALKTHROUGHS.md), the keyboard on both platforms,
the performance checklist (PERFORMANCE.md), Maestro, the EAS workflow's first run and
opening its update in Expo Go, and the notification permission on Android 13+.

### Tests

`src/matrix/*` (the matrix), `src/focus.test.tsx`, `src/keyboard.test.tsx`,
`src/perf.test.tsx`, `src/layout.test.tsx`, `src/sample.test.tsx`, `src/expoGo.test.ts`,
`src/settings.test.tsx`, `src/e2e.test.ts`. `src/smoke.test.tsx` now takes its screens and
app from `src/testing/screens.tsx`, whose route map is read from `src/app`. Component
snapshots changed for the header roles, live regions and the readable column (coach, today,
plan, run, connections, plus, watch). Jest restarts a worker over 1.2 GB: the full suite
reached the container's memory limit once.
