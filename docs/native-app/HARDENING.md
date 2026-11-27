# Native app: hardening (27–28 Nov)

The native app after milestone 8, checked for tests, accessibility, screen
states and hygiene.

- **Branch:** `native/hardening`, based on `claude/native-app-m8`. `main` has no
  `mobile/` yet: the native app exists only on the stacked milestone PRs (#24 to
  #34). See "Needs a decision" below.
- **Rules:**
  - nothing here touches the server, `garmin_mcp/providers/`, Intervals.icu work
    or milestone 9;
  - anything that overlaps milestone 9 or needs a design decision is listed here
    instead of built.

## How it's checked

`npm run check` in `mobile/` runs the token sync, the colour lint, the
typecheck (now with `noUnusedLocals` and `noUnusedParameters`) and every test,
on iOS and Android. New in this pass:

| Test | What it holds |
|---|---|
| `src/api/client.test.ts` | Auth: the bearer token, no token, 401, an expired Garmin login. Retries: reads back off on offline, 429 and 5xx, and give up after their tries; writes never retry; timeouts. Error mapping. Sign-out clears the runner's data and keeps the device's. |
| `src/data/cache.test.ts` | The offline cache on real SQLite: written, read back first next launch, a corrupt value, staleness at its 30-minute edge, a clock behind the copy, nothing cached after sign-out. Why a refresh failed. |
| `src/theme/tokens.source.test.ts` | Every token against `garmin_mcp/app/app.css`, read independently of the generator, in dark and light, plus contrast (below). |
| `src/smoke.test.tsx` | All 22 routes built so far, light and dark, iOS and Android, with fixture data: no errors, and four automated accessibility checks on every screen. |
| `src/states.test.tsx` | Today, Plan, post-run and Connections: slow first answer, offline, expired Garmin login, signed out, empty data, cached content when the login expires, and nothing reaching the watch without the confirm, retries and restarts included. |

## Accessibility findings

| Area | Found | Done |
|---|---|---|
| Tap targets | Android controls hard-coded at 44, under 48 dp: the date and since chips, metric tabs, thumbs, links, Edit steps rows, week arrows. "Ask why" on Today was a link inside a line of text, about 18 pt tall. The session card title (28 pt). The debrief toggle (24). Header Edit, Cancel and Share relied on hit slop. | **Fixed.** Every control uses `MIN_TOUCH` (44 pt iOS / 48 dp Android). "Ask why" is its own control. The title and "Ask why" get hit slop sized from the type ramp, so Today's above-the-fold layout doesn't move. Header actions get full-size boxes. The smoke test checks every control on every screen. |
| Text scaling | Text in fixed-height boxes, which clips at large sizes: badges (22), metric tab pills (32), Source priority rows (64), the FAB (56). | **Fixed.** They grow with their text (`minHeight`). The smoke test checks that no text sits in a fixed-height box. The readiness ring's number and the week strip are capped at 1.4–1.5× on purpose; the same values are said in full nearby. |
| Text scaling, largest sizes | All text is capped at 200% (`MAX_FONT_SCALE`), as the build prompt asks (section 9). iOS's largest accessibility sizes go to about 310% for body text. | **Deferred: needs a decision** (below). |
| Labels | Every icon-only control has a spoken name. Every chart has a one-line summary: load charts, the prediction trend, the 14-night signal chart, post-run charts (as one adjustable element), the route map and the structure bar. | Checked on every screen by the smoke test, and it confirms it saw the charts (14 drawings). The week strip said its abbreviations ("Int, 25, done"); it now says them in full ("Intervals, 25, done"). |
| Contrast | Every text colour pairing the screens use was checked at 4.5:1, and every mark (outlines, type bars, icons, chart lines) at 3:1, in both modes. | **All pass.** The test fails if a token change breaks one. |
| Colour alone | HR zones are labelled Z1–Z5 with minutes, on a labelled opacity ramp. "Back off" is a word in the warning colour on its tint card. The intervals colour always comes with its word (chip, row and strip labels). Planned vs actual has a tick or a warning icon plus "on plan" or "off plan". Takeaways have a tick or an info mark. Merges say "Kept Garmin". Capability chips are ticked, or crossed and struck through. | No colour-only meaning found. |
| Focus order | Every screen is a single column whose code order matches what's drawn. The only floating elements (FAB, send toast) come last. | Looks right by construction. **Needs a device walkthrough** with VoiceOver and TalkBack, which this environment can't run. |

## States

| Screen | Slow | Offline | Garmin login expired | Signed out | Empty |
|---|---|---|---|---|---|
| Today | skeleton | cached day + "Updated …"; with nothing cached, "isn't on this phone yet" + Try again | banner over the cached day; with nothing cached, Reconnect | Sign in | rest day card |
| Plan | skeleton | cached plan + "Updated …" | banner; Reconnect | Sign in | **new:** "No plan yet" + Or plan week by week (was a blank screen) |
| Post-run | skeleton | cached run, or "isn't on this phone yet" | Reconnect | Sign in | n/a |
| Connections | skeleton | cached + "Updated …" (new) | Reconnect | Sign in | **new:** "Nothing connected yet" + Connect Garmin |

- **No infinite spinners.** The only spinners are the inline "sending" marks, and
  the watch queue ends those with a state (sent, failed, waiting for sync).
- **Confirm before anything reaches the watch.** Tested three ways:
  - staged changes never send, however often the queue runs;
  - a resend sends the same confirmed operation again, never a new one, and a
    restart resumes only what was confirmed;
  - a source scan checks that only the queue calls the transport, only the
    confirm creates operations, and only the transport knows the write endpoint.

## Fixed along the way

- **The API client** now says why a request failed:
  - `NotSignedIn`;
  - `ReconnectNeeded` (TODO(server): `{code: "reconnect_needed"}` for an
    expired Garmin login);
  - `Offline`;
  - `TimedOut`;
  - `ApiError` with status and retryability.

  Reads retry with backoff (0.4 s, then 1.2 s). Writes don't retry in the
  client; the watch queue owns that, after the confirm.
- **`signOut()`** removes the token and everything the phone keeps for the
  runner (cached screens, the watch queue, runs, plans). The device's
  appearance setting stays. Milestone 9 wires it to a button.
- **The watch transport** now sends a builder's workout (`daash.workout.v1`).
  M6 put it on the operation but never sent it. A send refused because the
  login expired, or because of a 4xx, now waits for Resend with words saying
  what to do, instead of retrying for nothing.
- **Plan's error screen** said "Couldn't load today". It now says what it's
  loading, and which failure it was.
- **Sheet routes** opened from a link (`/today/readiness`, `/plan/move`, …) now
  sit over their tab's first screen, via `unstable_settings.anchor`.
- **Unused code** removed; the compiler now refuses unused locals and parameters.

## Deferred: overlaps milestone 9

- **"Create a plan" on the empty Plan.** It belongs to onboarding.
  `EmptyPlan` takes `onCreate`; milestone 9 passes it. For now the card offers
  "Or plan week by week".
- **Signing in.** Every Sign in, Reconnect and Connect Garmin button opens the
  server's `/connect` (Garmin's own sign-in). Handing the app a token back
  (TODO(server)) and a Sign out button in You come with milestone 9.
  `setAccessToken` and `signOut` are ready for it.
- **Analytics events** (section 11). The app has no analytics layer yet.

## Needs a decision

1. **Where the native app's PRs land.**
   - `main` has no `mobile/`. The native app is eight stacked PRs (#24 → #34),
     and this branch sits on M8.
   - Milestone 9 and this PR both branch from M8, so they'll need merging
     together.
   - It touches `ReadinessStrip`, `BuilderView`, `Button` users, `client.ts`
     and the watch transport; milestone 9 also changes `BuilderView` (its try
     mode).
   - Suggest: land M1–M8 into `main` in order, then this, then M9, rebasing M9
     on top.
2. **Text size above 200%.** The prompt says 200%; iOS's largest accessibility
   sizes are bigger. Allowing them means dense rows (the week list, step rows,
   chart labels) need stacked layouts at those sizes. Today does this at 200%;
   the others would need design. Suggest: keep 200% for launch, and design
   stacked layouts for the accessibility pass (milestone 12).
3. **Coach wording.** A development-only Coach stand-in for "niggle" says
   "without loading the sore spot", which edges toward injury advice. It isn't
   shipped (the server writes the real Coach text), but the server's prompt
   guards (milestone 11) should rule this kind of line out.
4. **Inline links.** WCAG allows small inline text links. This pass made
   "Ask why" its own control so it meets 44 / 48. If you'd rather keep it inline
   as on the board, say so; the cost is a small target.
