# Self-hosting over HTTP

The normal install needs none of this: Claude Desktop starts the server as a
local process and talks to it over stdio. This page is for running it as a
long-lived service instead, on a machine at home, so every Claude client on
your account can use it: web, desktop, mobile and Claude Code.

It's for **one person's own Garmin account**, on a home connection. Garmin's
sign-in sits behind bot protection that blocks datacenter IP addresses, so a
cloud host or a VPN exit won't work. A home server does.

## How access is protected

Claude's servers make the connection, not your device, so the server has to be
reachable at a public HTTPS address. Access is therefore gated by OAuth, and
the server refuses to start in HTTP mode without it:

1. You add the server's URL to Claude as a custom connector.
2. Claude registers itself and sends you to sign in. The sign-in is Google's,
   so no password is held here and your Google 2FA applies.
3. Only the addresses in `GARMIN_MCP_ALLOWED_EMAILS` get past that point.
4. A consent page then shows which client is asking and where the approval is
   going, and you approve it.
5. Claude gets a one-hour access token and a refresh token, and sends the
   access token on every request.

Details worth knowing:

- Tokens are stored only as SHA-256 hashes, in `oauth.json` next to the Garmin
  session. Refresh tokens rotate on every use, and a reused one revokes the
  whole grant, because that only happens if a token was copied.
- Codes are only ever sent to Claude's callback
  (`https://claude.ai/api/mcp/auth_callback`) or to a loopback `/callback`
  for Claude Code. Registrations asking for anything else are refused.
- Requests addressed to any other hostname are rejected.
- Removing an address from `GARMIN_MCP_ALLOWED_EMAILS` cuts off its tokens at
  once.
- Your Garmin password and session never leave the server. OAuth only decides
  who may call the tools.
- Anyone signed in to your Claude account can use the connector, like any
  other connector, so keep 2FA on that account too.

## Setup

### 1. A public HTTPS address, on port 443

Use anything that terminates TLS and forwards to the container's port without
opening ports on your router: Tailscale Funnel or Cloudflare Tunnel.

**It must be port 443.** Claude's connectors don't connect to other ports: a
`:8443` address that answered from anywhere on the internet still got
"Couldn't reach this address" from Claude.

The simplest way to get 443 without affecting anything else on the host is a
Tailscale sidecar that joins your tailnet as its own machine and runs Funnel
there (the compose file in step 4 does this). Or, if nothing else on the host
uses Funnel or `tailscale serve` on 443:

```bash
tailscale funnel --bg 8000
```

Either way you get `https://<machine>.<tailnet>.ts.net`. That origin, with no
path, is your `GARMIN_MCP_PUBLIC_URL`.

### 2. A Google OAuth client

In the [Google Cloud console](https://console.cloud.google.com/):

1. **APIs & Services → OAuth consent screen**: choose *External*, fill in the
   app name, and add your own address under *Test users*. Only the `openid`
   and `email` scopes are used, so the app never needs Google's review.
2. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   choose *Web application*, and add this authorized redirect URI:
   `https://<your public address>/oauth/google/callback`
3. Copy the client ID and client secret.

### 3. Configuration

| Variable | |
| --- | --- |
| `GARMIN_MCP_TRANSPORT` | `http`. The Docker image sets it already. |
| `GARMIN_MCP_PUBLIC_URL` | The public origin from step 1, e.g. `https://mini.example.ts.net` |
| `GARMIN_MCP_GOOGLE_CLIENT_ID` | From step 2 |
| `GARMIN_MCP_GOOGLE_CLIENT_SECRET` | From step 2 |
| `GARMIN_MCP_ALLOWED_EMAILS` | Comma-separated Google addresses allowed in. Usually just yours. |
| `GARMIN_MCP_HOST` / `GARMIN_MCP_PORT` | Where to listen. Defaults to `127.0.0.1:8000`; the image uses `0.0.0.0` |
| `GARMIN_EMAIL` / `GARMIN_PASSWORD` | Optional, as for the local install: lets it sign in to Garmin again by itself when the session expires |

`GARMIN_MCP_AUTH=none` turns OAuth off, but only when the server listens on a
loopback address. It's meant for trying things out locally.

### 4. Run it

This runs the server plus a Tailscale sidecar that shares its network
namespace, joins the tailnet as its own machine called `garmin`, and serves
it publicly on 443:

```yaml
services:
  garmin-mcp:
    image: garmin-mcp          # docker build -t garmin-mcp .
    env_file: .env
    ports:
      - "127.0.0.1:8000:8000"  # local only, for health checks
    volumes:
      - ./garmin-data:/data
    restart: unless-stopped

  garmin-funnel:
    image: ghcr.io/tailscale/tailscale:latest
    network_mode: "service:garmin-mcp"
    environment:
      - TS_AUTHKEY=${GARMIN_TS_AUTHKEY:-}   # only used on first start
      - TS_AUTH_ONCE=true
      - TS_HOSTNAME=garmin
      - TS_EXTRA_ARGS=--advertise-tags=tag:garmin
      - TS_USERSPACE=true
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_SERVE_CONFIG=/config/serve.json
    volumes:
      - ./funnel-state:/var/lib/tailscale
      - ./funnel-config:/config:ro
    restart: unless-stopped
```

`funnel-config/serve.json` (the container fills in `${TS_CERT_DOMAIN}`):

```json
{
  "TCP": { "443": { "HTTPS": true } },
  "Web": {
    "${TS_CERT_DOMAIN}:443": {
      "Handlers": { "/": { "Proxy": "http://127.0.0.1:8000" } }
    }
  },
  "AllowFunnel": { "${TS_CERT_DOMAIN}:443": true }
}
```

In the tailnet policy, own the tag and give Funnel to it alone, with no grants,
so the machine can reach nothing on your tailnet:

```jsonc
"tagOwners": { "tag:garmin": ["autogroup:admin"] },
"nodeAttrs": [ { "target": ["tag:garmin"], "attr": ["funnel"] } ],
```

Then generate an auth key (Settings → Keys: pre-approved, tag `tag:garmin`) and
put it in `.env` as `GARMIN_TS_AUTHKEY`. On its first start the sidecar spends
about 40 seconds getting its certificate.

Sign in to Garmin once, interactively, so a multi-factor prompt can be
answered. The session is cached in the volume:

```bash
docker compose run --rm garmin-mcp python -m garmin_mcp.login
docker compose up -d
```

### 5. Connect Claude

- **Claude web, desktop and mobile:** Settings → Connectors → Add custom
  connector, with the URL `https://<your public address>/mcp`. Sign in when
  asked. It then works in every Claude app on your account.
- **Claude Code:**
  `claude mcp add --transport http garmin https://<your public address>/mcp`,
  then run `/mcp` to sign in.

## Managing access

```bash
docker compose exec garmin-mcp python -m garmin_mcp.google_oauth status      # who holds a grant
docker compose exec garmin-mcp python -m garmin_mcp.google_oauth revoke-all  # sign everything out
```

Both take effect on the running server. Every tool call is logged with the
address it was made for.
