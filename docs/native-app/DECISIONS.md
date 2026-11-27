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
