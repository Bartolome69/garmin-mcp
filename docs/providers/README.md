# Data providers

Where a person's training data comes from. Garmin is the default and, until
another provider is reviewed and switched on, the only one. The code is in
`garmin_mcp/providers/`.

## How a call reaches a provider

Every tool reaches its data through `server._run` (and `_call`, `_read` and
`_remembered`, which are built on it). Those hand the call to
`providers.current()`, which is:

1. the provider the request named: the hosted server's `SessionBinding` sets
   it from `users.default_provider` on every request, from the row it already
   reads, so it costs no extra query;
2. otherwise the default, `garmin`. That covers a NULL column (everyone
   today), a name nobody registered, and anything outside a request (jobs,
   the local stdio server, tests).

A stored choice can never leave someone with no provider: an unknown name
resolves to Garmin.

## What a provider is (`base.py`)

- `name`: the key stored in `users.default_provider` and sent to analytics.
- `label`: how it's named to a person ("Connected to {label}").
- `capabilities`: what it serves, read or write, from `Capability`
  (activities, activity_details, activity_edit, daily, sleep, hrv,
  heart_rate, readiness, fitness, gear, workouts, strength, schedule). Set a
  flag only when the provider really serves it.
- `run(fn)`, `remembered(key, ttl, fn)`, `forget()`: run a call against the
  provider's client, from memory where fresh, and drop memory after a write.
  `tool_errors` calls `forget()` after every tool in `WRITES`.

A tool that needs something the provider lacks calls
`providers.current().require(Capability.X)`. The `NotSupported` it raises
reaches the person as a sentence ("Intervals.icu doesn't share a readiness
score with Daash on this connection."), not as a failed call.

## Garmin (`garmin.py`)

`GarminProvider` is server.py's old `_run` and `_remembered`, moved over
unchanged: the official API for an official-only preview account, with a
fallback to Connect where the account has both, and Garmin Connect (the
password route) for everyone else. `tests/providers_test.py` runs the old
bodies (copied from main at 4f5974f) next to the provider over every route
and requires the same answer, error and client. Garmin has every capability.

Garmin keeps its own tables: `users` for the Connect session and
`official_links` for the official API.

## Links to other providers (`store.py`)

`provider_connections`, one row per person and provider:

| column | |
|---|---|
| `user_token`, `provider` | primary key |
| `auth` | `oauth` or `api_key`. Never a password. |
| `credentials_enc` | OAuth tokens or the API key, Fernet-encrypted with `GARMIN_MCP_SECRET` like Garmin's |
| `external_id` | the person's id at the provider (for webhooks and dedupe) |
| `status`, `last_error` | `connected` or `error`, and the provider's last refusal in words, for the connection card |
| `created_at`, `updated_at` | |

Functions: `save_provider_connection`, `provider_connection`,
`provider_connections`, `update_provider_credentials`,
`mark_provider_error`, `delete_provider_connection` (which also resets
`default_provider` if it pointed at that provider) and
`set_default_provider`. `delete_user` deletes the person's links too.

A provider's app-level OAuth client (its client id and secret) is a Fly
secret, not a table.

## The provider-neutral workout (`workout.py`)

`daash.workout.v1` is the step list `create_workout` already takes, made a
contract (the module docstring has the shape). Every provider after Garmin
translates to and from it. `normalise()` returns the canonical document or
a `WorkoutError` in words. Garmin still builds straight from the steps
(`workouts.build_workout`), as before; the tests show that a workout passed
through `normalise()` builds byte-identical Garmin JSON.

## Adding a provider

1. Check its API docs first, and link them from `docs/providers/<name>.md`.
   Write nothing from memory.
2. `providers/<name>.py`: a `Provider` subclass with honest capabilities.
   Results take the shapes the existing read tools return, plus
   `provider: "<name>"`.
3. Register it behind a PostHog flag (`provider_<name>_enabled`), so only
   people on the flag can choose it.
4. Connect and disconnect: save and delete a `provider_connections` row, and
   send `provider_connected` / `provider_disconnected` / `provider_error`.
5. Writes follow confirm-before-write, as Garmin's do.

## Analytics

`tool_called` carries `provider` (today always `garmin`).
