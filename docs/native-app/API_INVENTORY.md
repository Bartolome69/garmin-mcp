# Daash API inventory, for the native app

Every endpoint the companion PWA (`garmin_mcp/app/app.js`) calls today, as the
server (`garmin_mcp/app.py`, `garmin_mcp/hosted.py`, `garmin_mcp/oauth.py`)
defines it, on `main` at 3c5b42e (27 Nov 2026). Shapes were taken from real
answers against the demo account (`tests/demo_garmin.py`), not from memory.

The chat pilot ([#22](https://github.com/Bartolome69/garmin-mcp/pull/22), not
merged) adds `GET /app/api/features` and `/app/api/chat*`; they are listed at the
end because the AI Coach module would build on them.

## How to read this

- **Base URL**: `https://mcp.garmin.daash.run` (the hosted server; `GARMIN_MCP_BASE_URL`).
- **Auth**: `Bearer` = `Authorization: Bearer <access token>` from Daash's own
  OAuth (see "Auth today" below). Every `/app/api/*` route needs it except
  `/app/api/sign-in/*`. Missing or unknown token: `401 {"error": "Signed out."}`.
- **Errors**: always JSON `{"error": "<a sentence a person can read>"}`, often with
  extra keys. A `200` whose body carries `error` is turned into a `502` by the
  server (`app._json`). Writes refused for a reason the person can act on are
  `409`; a Garmin write that failed is `502` with `watch_failed: true`.
- **Every answer** carries `X-Daash-Build` (the PWA's build hash) and
  `Cache-Control: no-store`.
- **Request headers the PWA sends** (all optional, analytics only):
  `X-Daash-Layout: phone|desktop`, `X-Daash-Home: 1` (opened from the home
  screen), `X-Daash-Ahead: 1` (prefetch, not counted as a screen view),
  `X-Daash-From` / `X-Daash-Link` (where a link came from), `X-Daash-Fresh: 1`
  (pull to refresh: skip the server's memory of Garmin's answers).
- **Shape notes**: answers are *screen-shaped*. They carry finished wording
  (`kicker`, `headline`, `verdict`, `type_label`, `sub`) and a colour **name**
  (`colour: "easy" | "hard" | "long" | "strength" | "race" | "rec"`), never a hex.
  The native app should draw these as given, not recompute them. Every number
  is worked out by rule on the server (`analysis.py`, `feeling.py`,
  `plan_checks.py`); no model is involved.
- `ask` objects (`{claude, chatgpt, claude_app}`) are deep links that open an
  assistant with a prompt filled in. `credit` is Garmin's required attribution
  ("Garmin Forerunner 265").

## Sign-in and tokens

| Method | Path | Auth | Request | Response | Used by |
| --- | --- | --- | --- | --- | --- |
| GET | `/authorize` | none | OAuth 2.1 query: `response_type=code`, `client_id=daash-app`, `redirect_uri=<base>/app/callback`, `code_challenge` (S256), `state`, `resource=<base>/mcp` | Redirects through Daash's Garmin page (`/connect`), then Garmin's own approval for new accounts, then back to `redirect_uri?code=…&state=…`. No consent screen for `daash-app` sent to its own callback (`oauth.first_party`). | Sign in with Garmin |
| POST | `/token` | none (public client, PKCE) | form: `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`, `code_verifier`; or `grant_type=refresh_token`, `refresh_token`, `client_id` | `{access_token, token_type:"Bearer", expires_in:3600, refresh_token, scope}` | sign-in, every refresh |
| POST | `/revoke` | none | form: `token`, `client_id` | `200` | Sign out |
| POST | `/app/api/sign-in/handoff` | none | `{code}` (single-use, 15 min, from a chat card's Open in Daash link `…?login=<code>`) | token answer as `/token`, or `401 {error, result}` | Open in Daash links |
| POST | `/app/api/sign-in/email` | none | `{email}` | `{message}` whether or not the address has an account; `400` bad address, `429` too many, `502/503` mail down | "Already use Daash? Your email" |
| POST | `/app/api/sign-in/verify` | none | `{email, code}` (6 digits) | token answer as `/token`; `401` wrong/expired code or no account | email sign-in |
| GET | `/app/callback` | none | `?code&state` | the PWA's `index.html` (the page finishes the exchange) | Garmin sign-in return |

Lifetimes (`oauth.py`): authorization code 10 min, access token **1 hour**,
refresh token **1 year**, rotated on use (the old one keeps working until the
new one is used, `REFRESH_REUSE_GRACE`). The same tokens and account serve the
Claude/ChatGPT connectors (`_finish` keeps one identity per email).

## Today and sessions

| Method | Path | Request | Response (top-level keys) | Screen |
| --- | --- | --- | --- | --- |
| GET | `/app/api/today` | — | `today` (date), `kicker`, `headline`, `next` {date, kicker, name, type, type_label, colour, stats[], segments[], on_watch, schedule_id, easier, not_100}, `done_today`, `crew` {line, sub, faces[], date}, `readiness` {score, low, head, sub, tiles[], source}, `week` {days[], done, planned, unit, verdict}, `calendar[]` (desktop: three weeks), `empty`, `get_plan` / `build` (empty states), `initial`, `assistant`, `ask`, `credit` | Today |
| GET | `/app/api/session/{date}` | `?schedule_id=` when a day has two | `date`, `today`, `status`, `kicker`, `name`, `type`, `type_label`, `colour`, `km`, `minutes`, `estimated`, `why`, `segments[]`, `structure_title`, `steps[]`, `watch` {head, sub, changed}, `easier` {applied, label, preview}, `not_100` {prominent}, `workout_id`, `schedule_id`, `also[]`, `assistant`, `ask`, `credit` | Session preview |
| GET | `/app/api/run/{activity_id}` | — | `activity_id`, `date`, `kicker`, `name`, `type`, `type_label`, `colour`, `verdict`, `stats[]`, `planned_and_run[]`, `matched`, `splits[]`, `km`, `seconds`, `chart` {mode, parts[], band, caption}, `insights[]`, `effect` {head, sub}, `share_url`, `credit`, `assistant`, `ask` | Run analysis |
| POST | `/app/api/session/{date}/move` | `{to: "YYYY-MM-DD", schedule_id?}` | `{date, schedule_id, message}`; `409 {error}` if not movable | Move sheet, drag on Plan / desktop |
| POST | `/app/api/session/{date}/easier` | `{schedule_id?}` | `{message, …}`; `409` | Easier |
| POST | `/app/api/session/{date}/undo` | `{schedule_id?}` | `{message}`; `409` | Undo easier |
| POST | `/app/api/session/{date}/resend` | `{schedule_id?}` | `{message}`; `409` | Send again |
| GET | `/app/api/not100/{date}` | `?schedule_id&from=today\|session&prominent=1` | `date`, `name`, `schedule_id`, `type`, `type_label`, `colour`, `day`, `title`, `sub`, `options[]` (each: `choice`, `label`, `said` or `unavailable`, `spans[]` for days off, `ask`) — **what each choice would change, before anything changes** | Not feeling 100% sheet |
| POST | `/app/api/not100/{date}` | `{choice: original\|lighter\|easy\|swap\|skip\|days_off, days?, schedule_id?, retry?}` | `{message, watch, watch_said, sessions, undo[]}`; `502 {error, watch_failed:true, detail}` (nothing changed) | Not feeling 100% |
| POST | `/app/api/not100-undo` | `{undo: [{remove?, delete?, put?: {workout_id, date}}]}` (the steps the change answered with; at most 30) | `{message, watch_said}` | Undo toast |
| POST | `/app/api/match` | `{action: session\|extra\|undo, activity_id, schedule_id?}` | `{message, watch_said?}`; `404` when match-v2 is off for the person | Run counted as… |
| POST | `/app/api/partial` | `{choice: leave\|add\|skip\|reopen, schedule_id}` | `{message, watch_said?, undo?}` | After a partial run |
| GET | `/app/api/race-prompt` | — | `{}` or a card: `key`, `case` (no_plan\|close\|after), `weeks_out`, `name`, `when`, `title`, `sub`, `facts[]`, `cta`, `row_sub`, `mode` (card\|row) | Today's race card |
| POST | `/app/api/race-prompt` | `{key, action: cta\|snooze\|not_goal, case}` | `{message}` or `{ok}` | Race card buttons |

## Plan and plan builder

| Method | Path | Request | Response (top-level keys) | Screen |
| --- | --- | --- | --- | --- |
| GET | `/app/api/plan` | `?week=YYYY-MM-DD` (a Monday) | `today`, `week` {starts, title, range, done, planned, current, hard, days[], previous, next, ask_next, ask, rest_note}, `next_week` {starts, title, planned, range, sessions[]}, `block` {title, name, dates, bars[], phases[], phase_lines[], verdict, stats[], blocks[], ask}, `manage` {count, from, to}, `assistant`, `credit`; or the empty/`get_plan` states | Plan (week and block) |
| POST | `/app/api/plan/remove` | `{confirm: "remove"}` | `{message}`; `409` | Remove plan |
| POST | `/app/api/plan-choice` | `{option, position, screen}` | `{ok}` (records which of the two "Get your plan" choices was picked) | Get your plan |
| GET | `/app/api/plan-choice/form` | — | `{km, days[], long_day}` (prefill from Garmin) | Plan with Claude/ChatGPT form |
| POST | `/app/api/plan-choice/link` | the form's answers | `{prompt, claude, chatgpt, claude_app}` (links only; no model call) | same |
| GET | `/app/api/build` | `?replace=1` | `avg_km`, `longest_km`, `longest_on`, `bars[]`, `days`, `level`, `level_line`, `predictions[]`, `races[]`, `usual_days[]`, `long_day`, `faster_ok`, `starts`, `after`, `after_said`, `thin`, `plan_goal`, `window` {from, to, said}, `replacing`, `today`, `assistant`, `ask`; `404` when the builder is off | Builder, step 1 |
| POST | `/app/api/build/plan` | `{answers…}` (the seven steps) | goal times, amounts and a week-by-week preview; writes nothing | Builder preview |
| POST | `/app/api/build/send` | `{answers, retry, replace}` | `{state: running\|done\|failed, sent, total, said…}`; starts a background job that puts the plan on Garmin | "Put it on my Garmin" |
| GET | `/app/api/build/send` | — | the job's state (`none` when there's none) — polled every couple of seconds | Sending progress |

The builder is on for preview accounts, or everyone with `GARMIN_MCP_BUILDER=1`.

## Crew and You

| Method | Path | Request | Response (top-level keys) | Screen |
| --- | --- | --- | --- | --- |
| GET | `/app/api/crew` | `?week=next` | `kicker`, `headline`, `showing`, `hero` {date, dow, n, slot, type_label, colour, tag, names, faces[], you_in, kicker, line, whatsapp}, `also[]`, `days[]`, `people[]`, `invite` {url, shown, code}, `none` (not in a crew), `assistant`, `ask`, `credit` | Crew |
| GET | `/app/api/you` | — | `name`, `initial`, `since`, `watch` {name, synced, line}, `groups[]` (rows: assistants with a live sign-in, units, zones, threshold pace, goal race…), `times` {options[], chosen[]}, `assistant`, `ask`, `privacy` | You |
| POST | `/app/api/you/times` | `{times: [...]}` | `{message}` | Usual run times |
| POST | `/app/api/you/delete` | `{confirm: "delete"}` | `{message}`; deletes the account, the same as `/disconnect` | Delete account |

## Notifications (Web Push today)

| Method | Path | Request | Response | Notes |
| --- | --- | --- | --- | --- |
| GET | `/app/api/push` | — | `{key (VAPID public key), on_somewhere, heads_up?, pwa?, kinds?}` | `pwa` = PostHog flag `pwa-v2` |
| POST | `/app/api/push` | one of: `{subscription, tz}` (register this browser); `{off: true, endpoint}`; `{kind: today\|plan\|weekly, on, where}`; `{heads_up: <time>}` | `{ok, kinds?, heads_up?, message?}`; `400` | Stored in `push_subs` (endpoint, p256dh, auth): **Web Push only**, no APNs/FCM |

## Analytics

| Method | Path | Request | Response | Notes |
| --- | --- | --- | --- | --- |
| POST | `/app/api/event` | `{name, properties}` | `{ok}` | Only allowlisted names (`app.APP_EVENTS`, `app.PWA_EVENTS`) and keys (`app._EVENT_KEYS`) pass; the server sends them to PostHog as `app_<name>` (or the PWA event's own name) with the person's hashed id. The page never talks to PostHog itself. |

## Pages the app links out to (not API)

`/app/` (the PWA), `/connect` (Daash's Garmin page), `/official/*` (Garmin's
official OAuth), `/disconnect`, `/crew/{link}` (a crew's public page),
`/share/#…` (a run's share image), `/garmin-export` and `/history` (adding past
runs), `/email/*` (weekly email links).

## From the chat pilot (#22, not on main)

| Method | Path | Request | Response | Notes |
| --- | --- | --- | --- | --- |
| GET | `/app/api/features` | — | `{}` or `{"chat": true}` | The seed of a features endpoint (`coach_enabled`). |
| GET | `/app/api/chat` | — | `{messages[], starters[]}` | 404 unless allowlisted |
| POST | `/app/api/chat/send` | `{text, starter}` | server-sent events: `start`, `tool`, `delta`, `pending`, `done`/`error` | |
| POST | `/app/api/chat/action` | `{id, decision: confirm\|decline}` | `{id, status, message, refresh}` | Runs a stored write once; the pattern for confirm-before-watch-write |
| POST | `/app/api/chat/feedback` | `{id, value: up\|down}` | `{ok}` | |
| POST | `/app/api/chat/new` | — | `{ok}` | |

## Auth today, and what a native client needs

**Today (PWA).** Daash runs its own OAuth 2.1 authorization server
(`oauth.py`, on the MCP SDK). The PWA is a first-party public client,
`daash-app`, registered at start-up with one redirect URI,
`<base>/app/callback`. Sign-in is authorization code + PKCE (S256) through
the browser; the PWA keeps the access and refresh tokens in `localStorage`
(`daash.access`, `daash.refresh`), sends `Authorization: Bearer` on every
call, refreshes on a `401` and retries once, and signs out with `/revoke`. No
cookie is involved in `/app/api/*` (cookies exist only on the Garmin
connect pages, for an A/B arm and a same-browser check). Two shortcuts mint
tokens directly: a single-use handoff code from a chat card, and an emailed
6-digit code.

**What native needs.**

1. **A redirect URI the OS hands back to the app.** `daash-app` only accepts
   `https://…/app/callback`, which on a phone opens the PWA, not the app. Add
   a native first-party client (or a second redirect on `daash-app`) with a
   private-use scheme, `run.daash.app:/oauth/callback` (RFC 8252), and treat
   it as first-party (no consent page) in `oauth.first_party` and
   `recognised_redirect`. The app opens `/authorize` in
   `ASWebAuthenticationSession` (iOS) / Custom Tabs (Android) via
   `expo-auth-session` / `expo-web-browser`, never a WebView, so the Garmin
   pages run in the system browser and Daash never sees a Garmin password typed into the app.
2. **Secure token storage.** Refresh token in the Keychain / Android Keystore
   (`expo-secure-store`), access token in memory. A widget extension needs
   the token or a snapshot: share through an App Group (iOS) / app-private
   storage (Android).
3. **Refresh.** Same `/token` refresh grant. Serialize refreshes (one in
   flight) because rotation retires the old token once the new one is used.
4. **Email code and handoff** work as they are from native: they return tokens
   in the JSON body.
5. **Deep links.** For "Open in Daash" from a chat card or the weekly email to
   open the app instead of the PWA: Universal Links / App Links need
   `/.well-known/apple-app-site-association` and `/.well-known/assetlinks.json`
   served by the hosted server, covering `/app/*`. The existing `?login=`
   handoff code then signs the app in too.
6. **Sign in with Apple / Google.** Not present. Daash's identity is an email
   address (from Garmin or the email code). See PLAN.md for when Apple's
   guideline 4.8 would require Sign in with Apple, and OPEN_QUESTIONS.md.

## Gaps for native

| Gap | Today | Native needs |
| --- | --- | --- |
| Push | Web Push (VAPID) only, `push_subs` keyed by browser endpoint | Device registration for APNs/FCM (or Expo push tokens), a `device_tokens` table, a sender branch in `push.py`, same kinds (`today`, `plan`, `weekly`) and heads-up time |
| API versioning | None. `X-Daash-Build` makes an open PWA reload after a deploy; a store app can't | `/app/api/v1/…` (aliases of today's handlers), a client header (`X-Daash-Client: ios/0.1.0 (12)`), and a `GET /app/api/v1/meta` with the minimum supported app version, so an old build can be told to update instead of breaking |
| Contract | Screen-shaped dicts built by hand, no schema | A recorded contract (snapshot tests of each answer's shape) now; typed models and an OpenAPI file later, to generate the TS client |
| Offline | PWA keeps each answer in `localStorage` and draws it first | The same idea with a persisted query cache; writes never queue offline (they must be confirmed and run by the server) |
| Background refresh / widgets | None | A small, cheap `GET /app/api/v1/glance` (today's session, readiness, week ticks) served from what the server already holds, for widgets and background fetch |
| Rate limits | Per-person: at most 2 Garmin requests in flight (`gate.py`); Garmin's 429s back off. No limit on `/app/api/*` itself | A soft per-person cap for background/widget requests so a widget can't spend someone's Garmin allowance |
| CORS | Not needed (PWA is same-origin); a few public JSON routes send `*` | Not needed: native requests aren't browser requests |
| Cookies | None on `/app/api/*` | None needed |
| Confirm before a watch write | The PWA shows what a change does before the tap (Not feeling 100%'s `said`, the Move sheet), but the POST runs at once: the confirm is in the UI only | A server-enforced two-step change (preview, then confirm by id), as #22 does for chat; see PLAN.md |
| Feature flags | Scattered: PostHog flags (`pwa-v2`, `tools-v2`, `match-v2`), env (`GARMIN_MCP_BUILDER`), preview accounts, #22's `/features` | One `GET /app/api/v1/features` answering every flag the app needs (`coach`, `plus`, `builder`, `match_v2`, providers) |
