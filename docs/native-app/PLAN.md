# Daash native app: plan

Prep for a native iOS and Android app, before the designs land. No screens are
built here. Companion files: [API_INVENTORY.md](API_INVENTORY.md) (every
endpoint the PWA uses, with shapes) and [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md)
(things only Bart can answer).

Written 27 Nov 2026, against `main` at 3c5b42e.

## Decisions in one place

| Question | Decision |
| --- | --- |
| Stack | **Expo (React Native) SDK 58**, TypeScript, with Expo Router's native tabs and `@expo/ui` (real SwiftUI and Jetpack Compose components) where native feel matters most |
| Repo | A `mobile/` folder in this repo (monorepo), not a separate repo |
| Data | TanStack Query over a small typed client, cache persisted on the device; read offline, never write offline |
| API | Keep today's screen-shaped `/app/api/*` answers as the contract; add `/app/api/v1/` aliases, a client-version header and a minimum-version check |
| Auth | Daash's existing OAuth 2.1 (PKCE) through the system browser, plus the existing email code; a native redirect URI is the one server change needed |
| Watch writes | Server-enforced two steps: preview a change (nothing written), confirm it by id (written once). Same pattern as the chat pilot's confirm cards (#22) |
| Tokens | `garmin_mcp/app/app.css` stays the source of truth; `scripts/build_tokens.py` generates the app's tokens and CI fails if they drift (done in this PR) |
| Push | Expo's push service first (no new server dependency, one integration for both platforms), direct APNs/FCM later if needed |
| Analytics | No PostHog SDK in the app: events go through `POST /app/api/event`, like the PWA's, so names, ids and privacy rules stay the same |

---

## 1. The backend the app will use

Full table: [API_INVENTORY.md](API_INVENTORY.md). The short version:

- **27 routes (30 method and path pairs) under `/app/api/`**, plus `/authorize`, `/token` and `/revoke`.
  All JSON, all `Bearer` tokens, no cookies.
- **Answers are screen-shaped.** Today, Session, Run, Plan, Crew and You each
  have one GET that returns finished wording (`headline`, `verdict`,
  `type_label`, `sub`) and colour *names*. All the training logic (week
  verdicts, readiness wording, Not feeling 100% options, plan checks, the run
  analysis) runs on the server by rule. That is good for a native app: both
  platforms and the PWA show the same words and numbers, and there is nothing
  to port. The cost: the JSON follows the PWA's layout, so the native designs
  may need a few extra fields. Those get added to the same endpoints, never
  computed in the app.
- **Writes** are small POSTs: move, easier, undo, resend, Not feeling 100%
  (with an undo list in the answer), match, partial, plan remove, build/send
  (a background job, polled), usual times, delete account, push settings.
- **No AI anywhere** in these answers. The chat pilot (#22) is the only model
  call, behind its own flag.

### Auth today, and for native

Today the PWA is a first-party OAuth 2.1 public client (`daash-app`). It signs
in with an authorization code and PKCE through the browser, keeps the tokens in
`localStorage`, refreshes on `401`, and revokes on sign-out. There are two
shortcuts: a single-use handoff code from a chat card, and a 6-digit email code.
The access token lasts 1 hour; the refresh token lasts 1 year and rotates.

Native keeps all of it, and needs:

1. **A native redirect URI.** Register `run.daash.app:/oauth/callback`
   (RFC 8252 private-use scheme) for a first-party native client, with no
   consent screen, like `daash-app`. Open `/authorize` with
   `expo-auth-session` (ASWebAuthenticationSession on iOS, Custom Tabs on
   Android). Never use a WebView, and never ask for a Garmin password inside
   the app.
2. **Secure storage.** Keep the refresh token in Keychain or Keystore
   (`expo-secure-store`) and the access token in memory. Only one refresh may
   be in flight at a time.
3. **Email code and handoff** work unchanged: they return tokens as JSON.
4. **Universal Links / App Links.** Serve `apple-app-site-association` and
   `assetlinks.json` from the hosted server for `/app/*`. Then "Open in Daash"
   from Claude, ChatGPT or the weekly email opens the app, signed in through
   the existing `?login=` code.
5. **Sign in with Apple / Google: not at first.** Apple's guideline 4.8 asks
   for Sign in with Apple only when an app offers a third-party or social login
   for its *own* account. Daash's sign-ins are its own email code and Garmin,
   where the app is a client of the Garmin account whose data it shows. That
   case is listed as an exception. Adding Google sign-in later would bring in
   Sign in with Apple too. (Open question 4.)

### Gaps for native

Push (Web Push only today), API versioning, a recorded contract, offline
caching, a cheap glance endpoint for widgets, a soft rate limit for background
requests, server-enforced confirm, and one features endpoint. The details and
the fix for each are in [API_INVENTORY.md § Gaps](API_INVENTORY.md#gaps-for-native)
and section 4 below. CORS and cookies are not a problem: native requests aren't
browser requests, and `/app/api/*` uses no cookies.

---

## 2. The stack

### Trade-offs

| Criterion | (a) Expo / React Native | (b) Flutter | (c) SwiftUI + Jetpack Compose |
| --- | --- | --- | --- |
| Solo founder, both platforms | One codebase, TypeScript, close to the PWA's JS | One codebase, Dart (new language) | Two codebases, two languages, every feature twice |
| Native feel on each platform | Real UIKit/SwiftUI and Android views: native tab bars (Expo Router native tabs), native stacks, sheets, `@expo/ui` gives actual SwiftUI and Compose controls. Feels native when you use the platform components, not when you hand-draw one look | Draws its own widgets. Material 3 is good; Cupertino widgets are imitations and lag iOS releases (Liquid Glass, new controls) | Best possible on each |
| Widgets (WidgetKit, Glance) | `expo-widgets` (SDK 58): iOS widgets and Live Activities from Expo UI components; Android widgets. Fall back to a native Swift/Kotlin target via a config plugin | Needs native Swift/Kotlin targets anyway | First class |
| Live Activities / ongoing notifications | `expo-widgets` (iOS); Android ongoing notifications through a native module | Native code | First class |
| Apple Health / Health Connect (later) | Community modules (react-native-health, react-native-health-connect), or a small Expo module | Community plugins | First class |
| Push | `expo-notifications` + Expo push service, or APNs/FCM directly | firebase_messaging | APNs + FCM, two integrations |
| Offline-first | TanStack Query persisted cache, `expo-sqlite` | Hive/Drift | SwiftData/Room, twice |
| OTA updates | EAS Update: JS fixes without store review | Shorebird (third party) | None |
| App Review risk (4.2 minimum functionality) | Low, if it uses native navigation, widgets and notifications. A React Native app is not a "web wrapper" | Low | Lowest |
| Build / CI cost | EAS Build: free tier is slow; ~$19–99/month for priority builds. No Mac needed | Needs a Mac (or CI with macOS) for iOS | Needs a Mac, two pipelines |
| Reuse with the existing JS | Some: API client, wording helpers, the share of `app.js` logic that's display-only. The PWA isn't React, so not screens | None | None |

### Pick: Expo (React Native), SDK 58

One person can ship both platforms with it, and it no longer forces one look
across both. SDK 58 has native tabs in Expo Router (a UITabBarController on iOS,
a Material bottom navigation bar on Android), `@expo/ui` with SwiftUI and
Jetpack Compose components, and `expo-widgets` for iOS widgets and Live
Activities. That covers the "native on both" rule with platform components
instead of a lookalike. EAS Update lets a wording or layout fix ship the same
day. The app stays TypeScript, next to a JavaScript PWA, and the server already
does all the logic, so the app is mostly drawing. That is React Native's
strength.

The scaffold in `mobile/` pins `expo` **58.0.24** (11 Nov; 58.1.0 came out on
26 Nov and is too new to pin) with React Native 0.88.1 and React 19.3.0, the
versions Expo 58 bundles.

**What would change my mind:**

- **The designs lean on widgets, Live Activities or Health as the core**, not
  extras. Then SwiftUI + Compose earns its double cost, and probably iOS first.
- **The designs want one custom look on both platforms**, with heavy animation.
  That contradicts the brief, but if it happened, Flutter.
- **`expo-widgets` or native tabs aren't solid on SDK 58** when the first
  widget or tab bar is built. Then use the native-target fallback (a Swift or
  Kotlin widget in a config plugin) or `react-native-bottom-tabs`; it doesn't
  change the stack.
- **A second developer joins who is native-first.**

---

## 3. Architecture

### Repo layout

A `mobile/` folder in this repo:

```
mobile/                    Expo app (this PR: empty shell)
  app.json                 name, scheme, bundle ids, light and dark
  App.tsx                  empty root; becomes src/app/ (Expo Router) with the designs
  src/
    theme/tokens.ts        generated from app.css (do not edit)
    theme/design-tokens.json  the same, for native widget code
    api/                   typed client, auth, refresh            (later)
    features/              today, plan, crew, you, coach          (later)
    lib/                   flags, analytics, storage              (later)
scripts/build_tokens.py    app.css -> mobile/src/theme/tokens.*
tests/tokens_test.py       tokens in step with app.css
.github/workflows/mobile.yml  tokens check + typecheck on mobile/ changes
docs/native-app/           this plan
```

Why one repo: the API contract, the tokens and the app change in the same PR as
the server. One person reviews one place. The server's Docker images copy only
`garmin_mcp/` and a few files, so `mobile/` never reaches Fly. The deploy
workflow doesn't watch `mobile/` either. A separate repo would need the
contract and the tokens published between the two.

### App structure and navigation

- **Expo Router**, file-based, routes in `mobile/src/app/`.
- **Tabs:** Today, Plan, Crew, You. With `coach_enabled` there's a fifth tab,
  Coach, before You, as the chat pilot places it. Built with Expo Router's
  native tabs: SF Symbols, the system tab bar and large titles on iOS; a
  Material 3 navigation bar, top app bar and predictive back on Android.
- **Stacks per tab**: session and run screens push onto the tab's stack.
  Sheets use the platform's own: iOS detents, Android modal bottom sheets.
  That covers Move, Not feeling 100%, and the confirm sheet for every watch
  write.
- **Platform files** (`Component.ios.tsx` / `Component.android.tsx`) wherever
  the two platforms should differ, rather than one component with `if`s.
- **Deep links:** `daash://` and Universal/App Links for `/app/#/session/…`,
  `/app/#/run/…`, `/app/#/plan`, matching the PWA's routes. Then every link the
  server already hands out (chat cards, emails, notifications) maps to a screen.

### Data layer, offline cache, API client

- **TanStack Query** for every GET. Each screen is one query keyed like the
  PWA's paths (`today`, `plan?week=…`, `session/<date>`), so the PWA's
  "draw the last copy, then catch up" carries over.
- **Persisted cache** with `@tanstack/query-async-storage-persister` over
  `expo-sqlite`'s key-value store. The app opens on its last copy offline and
  says so, like the PWA does.
- **Writes are never queued offline.** A change to the watch must be confirmed
  and run by the server, so offline means "you're offline, try when you're
  back".
- **API client:** a small `fetch` wrapper with the bearer token, a single
  refresh in flight, one retry on `401`, and the client headers.
  - **Typed in three steps.** (1) Now: hand-written TypeScript types for the six
    screen answers, checked against recorded JSON fixtures. (2) A contract test
    on the server records each endpoint's shape from the demo account, like the
    inventory did, and fails when a field disappears. (3) Later: Pydantic models
    for the answers (Pydantic is already installed with the MCP SDK, so no new
    dependency), an exported OpenAPI file, and `openapi-typescript` generating
    the app's types.
  - Generating a schema on day one would mean rewriting 4,000 lines of
    hand-built dicts first. That's not worth it before the designs say which
    fields the app needs.

### Design tokens (done in this PR)

`garmin_mcp/app/app.css` is the source of truth. `scripts/build_tokens.py`
reads its dark `:root` block and its light block, and writes
`mobile/src/theme/tokens.ts` and `design-tokens.json` (50 colours in each mode, plus
radius and fonts).

The script fails if:

- the two light blocks in `app.css` (the media query and `[data-theme="light"]`) differ;
- a colour has no partner in the other mode;
- a value isn't a colour it understands.

`tests/tokens_test.py` and the new **Mobile app** workflow run `--check` on
every PR that touches `app.css`, the script or `mobile/`. A colour can't change
on the web without the app.

**The new `intervals` token** (`#E07AA8` dark, `#B23A78` light) is added to
`app.css` itself, so there is still one source. The PWA doesn't use it yet
(intervals still draw as `--hard`), so the PWA looks the same. Both values read
at 4.5:1 or better on the ground and surface colours, like the other type
colours (the test checks this).

**Fonts:** the PWA bundles Geist and Geist Mono. The app should bundle the same
files (`expo-font`). Whether iOS body text should use SF Pro instead, as
platform convention suggests, is a design call (open question 10).

### Feature flags

One endpoint, `GET /app/api/v1/features`, answered by the server per person.
It grows from #22's `/app/api/features`:

```json
{ "coach": false, "plus": false, "builder": true, "match_v2": false,
  "providers": { "garmin": true, "coros": false } }
```

- `coach` (`coach_enabled`): off means four tabs and no gaps. Every number in
  the app comes from the server's rules, so nothing goes missing. On means the
  Coach tab, built on #22's chat routes.
- `plus` (`plus_enabled`): off for everyone until Plus exists. Core stays free:
  plans, Today, sending workouts to the watch.
- `providers.*`: one per provider from the adapter layer. The app shows only
  connected or available providers. Wording stays neutral ("Connected to
  Garmin"), with no logos.
- `builder`, `match_v2`: the flags the PWA already reads, passed through.

The server reads its existing sources: env allowlists, PostHog flags (already
asked once an hour per person, `analytics.flag_on`) and preview accounts. The
app caches the answer and asks again on open.

### Analytics

**No PostHog SDK in the app.** Events go through `POST /app/api/event`, the way
the PWA's do. That way the server keeps one rule set: an HMAC'd distinct id, no
person profiles, an allowlist of event names and keys, and never a race's name,
a date or a time typed in. The app sends `X-Daash-Layout: ios|android` (new
values) so every event carries the platform.

Events the app should send with the PWA's existing names (the server prefixes
some with `app_`):

| Event | When |
| --- | --- |
| `viewed` → `app_viewed` | a screen opened (`screen`) |
| `ready` → `app_ready` | time from open to first screen |
| `app_opened` | open, with `source` (push, chat, email, …) |
| `signed_in` → `app_signed_in` | sign-in (`method`: garmin, email, deep_link) |
| `ask_clicked`, `ask_chosen` | Ask Claude / ChatGPT |
| `share_clicked` | share a run |
| `build_opened`, `build_step`, `build_action` | plan builder |
| `plan_choice_shown`, `plan_form_step`, `plan_handoff_opened` | Get your plan |
| `block_week_opened`, `edit_hint_shown`, `edit_hint_dismissed` | Plan |
| `push_permission_prompt_shown`, `push_permission_result`, `push_clicked` | notifications (`platform: ios\|android`, already allowed) |

Events the server already sends when the app calls it, unchanged:
`app_edit` (every move, easier, Not feeling 100%), `app_not_100_opened`,
`app_plan_built`, `app_plan_removed`, `app_push_on/off/time`,
`push_kind_switched`, `race_prompt_*`, `app_failed`, `app_account_deleted`,
`workout_sent_to_watch`, `push_sent`, `push_failed`, and the coach's `chat_*`
events from #22.

New, native only (to add to the allowlist): `device_registered` (platform),
`widget_added` / `widget_removed` (kind), `change_previewed`,
`change_confirmed`, `change_cancelled` (kind; see below), `watch_send_state`
(state: sending, on_watch, failed, resent), `update_required` (from the version
check).

**`tool_called` with `provider`:** `tool_called` (server, once per MCP call:
`tool`, `ok`, error fields) has no `provider` yet, because there is no provider
layer on `main`. When it lands, add `provider` (default `garmin`) to
`tool_called`, `workout_sent_to_watch`, `app_edit` and `app_viewed`, so the
reports split by provider from day one. (Open question 1.)

### Confirm before a watch write: on the server

Today the PWA shows what a change will do before the tap: Not feeling 100%
lists each choice's effect, Move shows the days. But the POST itself writes to
Garmin at once. The confirm lives only in the UI. For the native app the rule
should be enforced by the server:

1. **Preview.** `POST /app/api/v1/changes` with `{kind, params}` (kinds: `move`,
   `easier`, `undo_easier`, `resend`, `not_100`, `match`, `partial`,
   `plan_remove`, `plan_send`). The server works out exactly what would change,
   using the code that already does (`feeling.py`'s options, `move`'s checks,
   `plan_builder`'s preview). It writes **nothing** to Garmin. It stores the
   change and answers
   `{change_id, title, items: [{what, from, to}], watch_note, expires_at}`.
   The confirm sheet shows `items` as they are: "Sunday's 12 km long run →
   Saturday 6 Dec". The app never builds that list itself.
2. **Confirm.** `POST /app/api/v1/changes/{id}/confirm`. One SQL update moves
   it from `pending` to `running`, so it runs exactly once, for its owner,
   within its expiry. It runs the stored call, not a recomputed one. The answer
   is the send state.
3. **States.** `GET /app/api/v1/changes/{id}` returns `sending` → `on_watch` |
   `failed` (with Garmin's reason in Daash's words), and
   `POST …/{id}/resend` retries a failed one. Undo is a new change, previewed
   and confirmed like any other.

This is the chat pilot's pattern (#22: `chat_messages` rows in `pending`,
`running` and `done`, confirmed by id), made general. The PWA's current
endpoints stay as they are until the PWA moves over, and the MCP connectors
don't change at all. In a chat, the assistant asks first, and `delete_workout`
and `remove_plan` already take two calls.

**"On your watch", honestly:** Garmin tells Daash a workout is on the calendar,
not when the watch collected it (APP.md: "Scheduled. Arrives at its next
sync."). The PWA already labels this `on_watch`. The native `on_watch` state
should mean the same thing, worded as on the calendar and arriving at the next
sync. (Open question 7.)

---

## 4. Backend prep, smallest and safest first

Every item is additive. The PWA's routes and the MCP connectors don't change.
Nothing adds long-lived memory per person.

| # | Change | Size | Risk | Memory |
| --- | --- | --- | --- | --- |
| 1 | **Native OAuth client.** Register a first-party client with redirect `run.daash.app:/oauth/callback`, no consent page; recognise it in `oauth.first_party` / `recognised_redirect`; app name "Daash app (iOS/Android)" in reports | S | Low: a new client id; `daash-app` untouched | none |
| 2 | **`/app/api/v1/` aliases** of every `/app/api/` route (same handlers), `X-Daash-Client` read into events, `GET /app/api/v1/meta` → `{api: 1, min_client: {ios, android}}` | S | Low | none |
| 3 | **`GET /app/api/v1/features`**: one answer for coach, plus, builder, match_v2, providers. Grows from #22's `/features` | S | Low | none (uses the existing hour-long flag memory) |
| 4 | **Well-known files**: `apple-app-site-association` and `assetlinks.json` for `/app/*` | S | Low: static JSON | none |
| 5 | **Contract snapshot test**: records each GET's shape from the demo account and fails on a missing field | S | None (test only) | none |
| 6 | **Device registration + native push**: `device_tokens` table (token, owner, platform, kinds, heads-up, tz, failures); `POST/DELETE /app/api/v1/devices`; a sender in `push.py` for Expo push tokens over plain HTTPS (urllib, no new dependency), sending the same kinds as Web Push. Prune dead tokens on failure | M | Medium: touches the notification jobs; Web Push keeps working beside it | rows in SQLite; sends batched in the worker process |
| 7 | **`GET /app/api/v1/glance`**: today's session, readiness score, the week's ticks, for widgets and background refresh, from what the server already has (`garmin_cache`), never a fresh Garmin read on its own | M | Low | none beyond the existing cache |
| 8 | **Soft rate limit for background requests** (`X-Daash-Background: 1`): a few an hour per person, answered from cache past that | S | Low | a bounded per-person counter (like `_CALLS`, `BoundedDict`) |
| 9 | **Server-enforced changes** (`/app/api/v1/changes`, preview → confirm → state): a `changes` table, a preview function per kind that writes nothing, the confirm runner. The biggest piece; build one kind at a time (move first, then easier, Not feeling 100%, plan send) | L | Medium: must reproduce exactly what today's POSTs do; the PWA's POSTs stay as they are | rows in SQLite |
| 10 | **Typed answers + OpenAPI** (Pydantic models for the six screen answers, exported schema) | L | Low if done screen by screen behind the contract test | none |
| 11 | **`provider` on events** once the provider layer exists | S | Low | none |

**Fly memory (under 1,200 MB).** The app adds requests, not state.

- Push sends run in the worker process, which already sends Web Push.
- The glance endpoint reads the existing cache.
- The rate-limit counter is bounded.
- Change rows live in SQLite.

The risk to watch is load, not memory: widgets and background fetches from many
phones. Items 7 and 8 are there so a widget never triggers a Garmin read.
Check `memory_profile` and `server_heartbeat` in PostHog after each step, as
the deploy-check skill does.

---

## 5. Release plan

### Accounts and identifiers

- **Apple Developer Program**: $99/year. Enrol as an organisation, which needs a
  D-U-N-S number. The seller name shown in the App Store is then the company,
  not a person.
- **Google Play Console**: $25 one-off. New personal accounts must run a closed
  test with at least 12 testers for 14 days before production. An
  organisation account skips that and needs a D-U-N-S number.
- **Expo / EAS** account. Free to start; a paid plan for faster builds.
- **Bundle ID / application ID**: `run.daash.app` on both (reverse of daash.run).
  It's permanent once published, so confirm it first (open question 2).
- **URL scheme** `daash`; associated domain `mcp.garmin.daash.run` (and
  daash.run if links move there).

### Testing tracks

- **iOS:** TestFlight internal (up to 100 testers on the team, no review), then
  external (up to 10,000, light review).
- **Android:** internal testing (up to 100), then a closed track. That is also
  the 12-testers-for-14-days rule if the account is personal.
- **EAS Update channels:** `preview` for testers, `production` for the stores.
  JS-only fixes go over the air; anything native needs a store build.

### Privacy labels and data safety (health and fitness data)

Declare, for both stores:

- **Health & fitness:** workouts, heart rate, sleep, HRV, readiness, read from
  Garmin through Daash's server.
- **Contact info:** the email address.
- **Identifiers:** the account id and push tokens.
- **Usage data:** the product events.
- **Diagnostics:** crash logs, if a crash reporter is added.

All of it is linked to the person, none of it is used for tracking or
advertising, and it is encrypted in transit and at rest on the server. Account
deletion is in the app (You → Delete account, already built). Both stores
require that.

If the coach is on, add that messages and training data are sent to the
configured AI provider. The chat pilot's privacy wording (#22) covers this, and
must be public before any wider rollout.

With Apple Health or Health Connect later: HealthKit data can't be used for
advertising or sold. Google Play's health apps declaration and the Health
Connect permissions form are required.

### App Review notes

- **Demo account.** Reviewers can't sign in to a Garmin account. Provide a demo
  Daash account that signs in by email code, with the code sent to a mailbox
  the reviewer can read or fixed for that address only. It must be backed by
  the demo Garmin data (`tests/demo_garmin.py`) or a real test Garmin account
  (open question 5).
- **Explain:** Daash shows the person's own Garmin data and writes workouts to
  their Garmin calendar, only after they confirm on a screen listing exactly
  what changes. There is no medical advice.
- **Garmin wording:** "Connected to Garmin". No implied endorsement, no Garmin
  logo, no "Garmin" in the app name or subtitle.
- **Minimum functionality (4.2):** native navigation, widgets, notifications
  and the confirm flow make it more than a website in a frame. Ship the first
  release with at least notifications and one widget for that reason, as well
  as for the product.

### Minimum first release (the smallest that's worth reviewing)

Phase 1 below, with:

- Sign in with Garmin and by email code.
- Today, Session and Run screens.
- Plan: week and block.
- Move and Not feeling 100%, through the server-enforced confirm, with the send
  states.
- Crew, read-only, with the WhatsApp invite.
- You: notifications, usual times, delete account, sign out.
- Push for the three kinds.
- One home-screen widget: today's session.
- Light and dark mode.
- Four tabs (coach off).

### Phases

0. **Prep (this PR):** plan, inventory, tokens generator, empty Expo app, CI.
1. **Backend items 1–6, 9 (move first)** and the app shell: auth, the typed
   client, the persisted cache, tabs, Today/Session/Plan read-only. TestFlight
   and internal testing.
2. **Writes through confirm:** move, easier, Not feeling 100%, send states,
   resend. Push. The Today widget. Run analysis, Crew, You. Store submission.
3. **Builder and Get your plan** (plan send through confirm), the race card,
   deep links from chat cards and emails (Universal/App Links).
4. **Coach module** behind `coach_enabled`, built on #22. Its privacy wording
   goes public first.
5. **Platform extras:** Live Activity for a session today, more widgets,
   Android ongoing notification while sending. Apple Health / Health Connect.
   More providers (COROS) through the adapter layer.
6. **Plus**, behind `plus_enabled`: in-app purchase through StoreKit and Play
   Billing (RevenueCat to save time). Core stays free.

---

## Risks (ranked)

1. **Garmin terms and sign-in route for a store app.** The password route
   (`garminconnect`, unofficial) must never be offered in the app. Apple and
   Google may treat collecting third-party credentials as a violation, and
   Garmin's terms may too. The app should sign up only through Garmin's
   official approval (`/official/*`). Whether the official API's production
   access covers a published app, and whether official-only accounts have
   everything the app shows (history, readiness), decides what the app can do
   at launch. (Open questions 3 and 6.)
2. **App Review with a sign-in reviewers can't complete**, and health data. Needs
   a working demo account and clear review notes, or the first submission
   bounces.
3. **Server-enforced confirm (item 9) is the largest backend change** and has to
   match today's writes exactly. Build it one kind at a time behind the
   contract tests, and keep the PWA's routes as they are.
4. **Load from widgets and background refresh** on one 2 GB machine that froze
   in November under Garmin call pile-ups. The glance endpoint must never read
   Garmin itself, and background requests need their own cap.
5. **Screen-shaped API coupling.** The designs may want data the PWA's answers
   don't carry, or carry differently. Additions go on the server, versioned,
   and the contract test stops the PWA and the app from breaking each other.

Smaller: no provider layer on `main` yet (the plan assumes Garmin only until it
lands); "on your watch" can't be confirmed from Garmin; Expo SDK churn (one
upgrade a quarter, budget a day each); these docs are public once merged,
because `docs/` is the garmin.daash.run site.
