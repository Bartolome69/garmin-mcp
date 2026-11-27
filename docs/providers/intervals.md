# Intervals.icu

A bridge for COROS, Suunto, Polar and Wahoo users until direct connections are
approved: their watch syncs to Intervals.icu, and Daash reads it from there.
Behind the provider layer (`garmin_mcp/providers/`, see README.md) and the
`provider_intervals_enabled` flag. Garmin stays the default and behaves exactly
as before.

## Status

**Built (the parts that don't depend on Intervals.icu's API):**

- **The card.** `providers/catalog.py` lists Intervals.icu: its label, the
  neutral "Via Intervals.icu" badge, the flag, the explainer ("Connect
  Intervals.icu to use Daash with COROS, Suunto, Polar or Wahoo. Your watch
  must already sync to Intervals.icu.") and the sign-in routes to confirm.
- **The connection lifecycle** (`provider_routes.py`):
  - `GET /app/api/providers` lists the cards this person may see.
  - `POST .../{name}/connect` checks the credentials with the provider's
    connector before storing anything, then stores them encrypted.
  - `POST .../{name}/disconnect` revokes the grant where the provider can,
    then deletes it.
  - People without the flag get the plain 404.
- **The You screen.** A Connections group appears only for people the flag is
  on for. The app's sheet shows the state, the explainer, and Connect,
  Reconnect or Disconnect (two taps, because it deletes).
- **States.** Not connected, Live ("Connected to Intervals.icu") and Reconnect
  needed. Adapters call `provider_routes.provider_failed()` with a kind, and
  `auth_revoked` or `auth_expired` mark the card Reconnect needed.
- **Analytics.** `provider_connected` (provider, auth, reconnect),
  `provider_disconnected`, and `provider_error` (provider, error_type:
  refused, unreachable, revoke_failed, auth_revoked, rate_limited and so on).
  Never credentials. `tool_called` already carries `provider`.
- **One list of runs** (`providers/merge.py`). The same run from both
  providers shows once, Garmin's copy: it matches when the starts are within
  a minute and the distance and time are within 2%. The other copy is hidden,
  never deleted, and "These are different runs" keeps a pair apart.
- **Tests:** `tests/provider_connect_test.py`, against a stand-in connector,
  not Intervals.icu's API.

**Waiting for step 0 (reading Intervals.icu's API documentation):**

- `https://intervals.icu` and `https://forum.intervals.icu` are blocked by
  this environment's network policy, from the container and from the fetch
  tool alike. Nothing below is filled in from memory.
- **Sign-in:** OAuth for third-party apps (registration, scopes, token
  lifetime and refresh) against a personal API key. Connect stays off
  (`can_connect: false`) until an adapter registers a connector.
- **Endpoints and shapes:** athlete, activities with laps, streams and zones,
  wellness (sleep, HRV, resting HR), fitness (CTL, ATL, form), and calendar
  events or planned workouts (create, update, delete), plus the workout
  format.
- **Limits:** rate limits and 429 behaviour, pagination, units, time zones,
  webhooks.
- **Watch delivery:** whether a planned workout reaches the watch, and for
  which brands. Daash won't claim "On your watch" without that being
  confirmed.
- **Terms:** anything on AI use or showing the data.
- **The adapter itself:** `providers/intervals.py` with honest capability
  flags; mapping to the shapes of `get_activities`, `get_activity_details`,
  `get_daily_summary`, `get_sleep_data` and `get_readiness` with
  `provider: "intervals"`; translating `daash.workout.v1` with round-trip
  golden tests; cached reads with 429 backoff; writes behind
  confirm-before-write.

## Capability matrix

To be filled in from the docs (step 0). Until then every capability is
**unconfirmed**.

## What's needed from Bart

1. **Allow the domains.** Add `intervals.icu` and `forum.intervals.icu` to the
   cloud environment's allowed domains: the session title bar → Edit →
   Network access.
2. **The flag.** Create `provider_intervals_enabled` in PostHog with
   evaluation runtime "all" (the 26 Nov lesson), released to your own ID.
   Daash's own accounts in `ANALYTICS_INTERNAL_IDS` see the card without it.
3. **OAuth registration.** If step 0 confirms Intervals.icu supports OAuth for
   third-party apps, register Daash's app with them. The client ID and secret
   become Fly secrets, not table rows.
4. **A test account** connected to Intervals.icu, for checks against real data.
