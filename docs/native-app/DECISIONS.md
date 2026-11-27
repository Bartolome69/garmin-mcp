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
