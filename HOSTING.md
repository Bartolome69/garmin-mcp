# Running the hosted version

The local install needs none of this. This is for serving a handful of people
who shouldn't have to open a terminal.

Each person signs in once at `/connect` and gets a private URL:

    https://your-host/u/<random>/mcp

They paste that into Claude → Settings → Connectors → Add custom connector. It
works on the web and mobile apps as well as desktop. No OAuth server is needed,
because Claude accepts a bare URL — **so the URL is the credential.** Anyone
holding it can read that person's Garmin data.

## The one real constraint

Cloudflare, in front of Garmin's SSO, blocks datacenter IP addresses. Verified
from a GitHub Actions runner: an actual login attempt gets a `403` bot
challenge, while `connectapi.garmin.com` answers normally with its own
application-level `403` and no challenge.

So **sign-in needs a clean egress IP; everything else does not.** People already
connected keep working, because their token refreshes against the data API.
Only new sign-ups hit the blocked path.

Two ways to deal with it:

- A dedicated outbound IP from your host (Fly, Railway Pro, etc).
- A small VPS on a residential-ish IP, used only for sign-in, via
  `GARMIN_MCP_LOGIN_PROXY`. Around $4/month. Nothing else routes through it.

Without either, expect sign-ups to fail intermittently while your own already-
connected account works perfectly — which is exactly the failure you won't
notice.

Even from a clean address, Garmin limits how many sign-ins it takes from one
place, and every user signs in through the same one. When Garmin answers "too
many" (429) or blocks the address (403), the server pauses all sign-ins for 30
minutes and tells anyone who tries how long to wait, rather than passing their
attempts on: each one would count against the shared limit and push it towards
a block. Someone already entering a two-factor code can still finish. The pause
lives in memory, so a restart lifts it.

## Configuration

| Variable | Purpose |
| --- | --- |
| `GARMIN_MCP_SECRET` | **Required.** Fernet key encrypting stored tokens. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `GARMIN_MCP_DB` | SQLite path. Defaults to `/data/garmin-mcp.sqlite3`; put it on a volume. |
| `GARMIN_MCP_BASE_URL` | Public URL, used when showing someone their connector link. |
| `GARMIN_MCP_LOGIN_PROXY` | Optional. Proxy used **only** for sign-in. |
| `GARMIN_MCP_INVITE` | **Set this.** Sign-up is open to anyone who finds the host without it. Give friends the code along with the link. |
| `GARMIN_MCP_POSTHOG_KEY` | Optional. A PostHog project key. When set, the server reports six events: a sign-in succeeded, a sign-in failed (with a one-word reason), a connection was used for the first time, a connection was used on a given day (once per person per day), Claude was refused a token renewal (with a one-word reason), which it shows as an expired connection, and the weekly keep-alive found a Garmin session it could not renew. Each carries a few flags, the email address (masked and in full) and an unreadable per-person id; never a password, a token or any Garmin data. The store also keeps each connected person's email, encrypted like their token, and deletes it with the row on disconnect. Unset, nothing is sent. |
| `GARMIN_MCP_POSTHOG_HOST` | Optional. PostHog ingestion host; defaults to the EU one. |
| `GARMIN_MCP_PREVIEW` | Optional. Turns preview features (none at the moment: everything built so far is on for everyone) on for some accounts before everyone: a comma separated list of the Garmin sign-in addresses to include, or `on` for everyone. Unset, nobody sees them. Addresses are compared as fingerprints and never stored. Takes effect in the next new chat, with no reconnecting. |
| `PORT` | Defaults to 8000. |

## OAuth (optional, and the better mode)

Set `GARMIN_MCP_OAUTH=1` and the credential stops being the URL. Everyone shares
one public endpoint, `/mcp`, and identity arrives as a bearer token in an
`Authorization` header instead. `GARMIN_MCP_BASE_URL` becomes required — it is
the issuer identity in the metadata, and clients check it.

What changes for the person connecting: they paste `https://your-host/mcp` into
Claude, which sends them here to sign in to Garmin, then returns them with a
token of their own. No link to copy, nothing to keep secret, and Claude's
"anyone with the server URL can use this connector" warning goes away.

What it buys over the URL mode:

- Access tokens expire after an hour and refresh in the background.
- Each client is registered separately and can be revoked on its own.
- The credential is never in a URL, so it cannot leak through browser history,
  access logs, referrer headers or a screenshot.
- Refresh tokens are single use: a stolen one is worth one request.
- Tokens are stored as SHA-256 hashes, so the database holds nothing usable.
- Tokens are bound to this server as their audience and are refused elsewhere.

Both modes ship in the same image. Leave the variable unset and nothing changes;
existing connector URLs keep working. Turning it on does not migrate anyone —
people reconnect once, through Claude.

    fly secrets set GARMIN_MCP_OAUTH=1 -a your-app

## Revoking a link

The URL is the credential, so losing one matters. Two ways to take it back:

- **Sign in again.** A new sign-in retires every earlier URL for that email, so
  the lost one stops working. Signing in used to mint a second URL and leave the
  first live for ever — it no longer does.
- **Disconnect.** The link at the bottom of the connected page deletes the
  stored session outright.

Rotating `GARMIN_MCP_SECRET` is the blunt version: every stored token becomes
undecryptable at once and everybody signs in again. Use it if you think the
server's logs or database have been seen by someone who shouldn't have.

## Access logging is off, deliberately

Every MCP request has the user's token in its path, so an access log is a log of
everyone's credentials. `uvicorn` runs with `access_log=False`, and a logging
filter redacts `/u/<token>/mcp` from anything else that quotes a path. If you
add a log drain or a reverse proxy of your own, check it isn't recording paths.

Losing `GARMIN_MCP_SECRET` makes every stored token unreadable and everyone has
to sign in again. Nothing else breaks.

## Running it

```bash
docker build -t garmin-mcp .
docker run -p 8000:8000 -v garmin-data:/data \
  -e GARMIN_MCP_SECRET="..." -e GARMIN_MCP_BASE_URL="https://your-host" \
  garmin-mcp
```

## What is stored

The Garmin password is exchanged for tokens during sign-in and discarded — it is
never written to disk. Only the token blob is stored, encrypted with AES via
Fernet, alongside a masked email for your own reference.

No tool returns the token or the password. `get_connection_status` reports a
masked address and nothing about the server's filesystem.

## Tests

```bash
.venv/bin/python tests/hosted_test.py
```

Runs the real app over HTTP against a stubbed Garmin account and proves the part
that matters: two people's URLs resolve to their own sessions, an unknown URL is
rejected, and neither the stored token nor any server path appears in a response.

## Deploying

Everything lives on `main`: the connector, and the hosted server's own files
(`garmin_mcp/hosted.py`, `oauth.py`, `store.py`, `analytics.py`, the
Dockerfile and `fly.toml`). Running the connector locally never loads them.

Every push to `main` runs the full test suite. Nothing is deployed until
somebody starts it: Actions -> Deploy hosted server -> Run workflow, on
`main`. That run tests again, deploys to Fly only if the tests pass, and then
checks the live sign-in pages and the public address.

