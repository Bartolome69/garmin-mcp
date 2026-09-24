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

## Configuration

| Variable | Purpose |
| --- | --- |
| `GARMIN_MCP_SECRET` | **Required.** Fernet key encrypting stored tokens. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `GARMIN_MCP_DB` | SQLite path. Defaults to `/data/garmin-mcp.sqlite3`; put it on a volume. |
| `GARMIN_MCP_BASE_URL` | Public URL, used when showing someone their connector link. |
| `GARMIN_MCP_LOGIN_PROXY` | Optional. Proxy used **only** for sign-in. |
| `PORT` | Defaults to 8000. |

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
