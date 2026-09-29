"""Hosted, multi-user version of the Garmin MCP server.

One process serves several people. Each gets an unguessable URL which is their
credential — Claude's custom connectors accept a bare URL, so no OAuth server
is needed for a handful of friends.

The same nine tools as the local server; only the transport and the session
lookup differ. A request to /u/<token>/mcp binds that person's Garmin session
for the duration, and the tools resolve it through a context variable.

Their Garmin password is exchanged for tokens during sign-in and discarded. Only
the tokens are stored, encrypted.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Any

import anyio
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import oauth, store
from .server import mcp
from .session import (
    GarminError,
    GarminSession,
    build_client,
    mask_email,
    reset_session,
    use_session,
)

log = logging.getLogger(__name__)

MCP_PATH = "/u/{user_token}/mcp"
_PATH_RE = re.compile(r"^/u/(?P<token>[A-Za-z0-9_-]{16,})/mcp/?$")
# Same shape, unanchored, for scrubbing tokens out of anything we log.
_PATH_RE_ANY = re.compile(r"/u/[A-Za-z0-9_-]{16,}/mcp")

# Linked from both pages that ask for a Garmin password, because that is where
# the decision is actually made. Overridable so a self-hosted copy can point at
# its own policy rather than vouching for mine.
PRIVACY_URL = (
    os.environ.get("GARMIN_MCP_PRIVACY_URL", "").strip()
    or "https://garmin.daash.run/privacy/"
)

# Sign-up is closed when this is set: friends get the code along with the link.
# Without it the page is an open door to anyone who finds the host, who can then
# use it to test Garmin credentials and burn the server's IP on Garmin's rate
# limiter — which breaks sign-in for everyone else.
INVITE_CODE = os.environ.get("GARMIN_MCP_INVITE", "").strip()

# OAuth moves the credential out of the URL and into an Authorization header,
# with expiry and per-client revocation. Off by default so the running
# deployment is unchanged until it is turned on deliberately.
OAUTH_ENABLED = os.environ.get("GARMIN_MCP_OAUTH", "").strip() == "1"
OAUTH_PROVIDER = oauth.GarminOAuthProvider() if OAUTH_ENABLED else None

# Crude per-address throttle on sign-in attempts. Not a defence against a
# determined attacker; enough to stop this being a comfortable place to test
# stolen Garmin passwords.
_ATTEMPTS: dict[str, list[float]] = {}
_ATTEMPT_WINDOW = 900
_ATTEMPT_LIMIT = 8

# Sign-ins waiting on a multi-factor code. In memory on purpose: a restart
# simply asks the person to start again, and nothing sensitive outlives it.
_PENDING: dict[str, dict[str, Any]] = {}
_PENDING_TTL = 600

# One live session per person, reused across their requests so the token is not
# re-read on every tool call.
_SESSIONS: dict[str, GarminSession] = {}

# When each person was last marked as seen. A conversation makes a burst of tool
# calls; writing last_seen_at once an hour per person records the same fact
# without a database write on every one of them.
_TOUCHED: dict[str, float] = {}
_TOUCH_EVERY = 3600

# The front page shows how many people used this recently. One number, cached,
# so a busy page never turns into a query per visitor.
_STATS_CACHE: dict[str, Any] = {"at": 0.0, "body": ""}
_STATS_TTL = 300


# Cloudflare in front of Garmin's SSO blocks datacenter addresses — verified
# from a GitHub Actions runner, and reported the same way by other hosted
# gateways. The data API is unaffected, so only sign-in needs a clean egress.
#
# garminconnect exposes no proxy setting, so this goes through curl's
# environment variables. Those are process-wide, hence the lock: sign-ins are
# rare, and serialising them costs nothing.
_LOGIN_PROXY_LOCK = threading.Lock()


@contextlib.contextmanager
def login_egress():
    """Route just this block's requests through the sign-in proxy, if set."""
    proxy = os.environ.get("GARMIN_MCP_LOGIN_PROXY", "").strip()
    if not proxy:
        yield
        return

    keys = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
    with _LOGIN_PROXY_LOCK:
        previous = {k: os.environ.get(k) for k in keys}
        os.environ.update({k: proxy for k in keys})
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def base_url(request: Request) -> str:
    configured = os.environ.get("GARMIN_MCP_BASE_URL", "").strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


def session_for(user_token: str) -> GarminSession:
    existing = _SESSIONS.get(user_token)
    if existing is not None:
        return existing
    created = GarminSession(
        tokenstore=lambda: store.load_blob(user_token),
        on_refresh=lambda blob: store.update_blob(user_token, blob),
    )
    _SESSIONS[user_token] = created
    return created


def _sweep_pending() -> None:
    cutoff = time.time() - _PENDING_TTL
    for key, value in list(_PENDING.items()):
        if value["started"] < cutoff:
            _PENDING.pop(key, None)


def _client_address(request: Request) -> str:
    """Best guess at who is asking, for throttling only.

    Behind Fly the real address arrives in Fly-Client-IP; the socket address is
    the edge. Neither is trustworthy enough for a security decision, which is
    why the invite code carries that weight and this only paces attempts.
    """
    forwarded = request.headers.get("fly-client-ip") or request.headers.get(
        "x-forwarded-for", ""
    )
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _too_many_attempts(address: str) -> bool:
    """Record a sign-in attempt; True once an address has had too many."""
    now = time.time()
    recent = [t for t in _ATTEMPTS.get(address, []) if t > now - _ATTEMPT_WINDOW]
    recent.append(now)
    _ATTEMPTS[address] = recent
    if len(_ATTEMPTS) > 5000:  # Bound the dict; oldest addresses go first.
        for key in sorted(_ATTEMPTS, key=lambda k: _ATTEMPTS[k][-1])[:1000]:
            _ATTEMPTS.pop(key, None)
    return len(recent) > _ATTEMPT_LIMIT


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

_STYLE = """
:root { color-scheme: light; --ground:#F4F6F3; --surface:#fff; --ink:#17211B;
  --muted:#55655B; --line:#D3DBD3; --accent:#2F6B4F; --clay:#BC5A2A; }
@media (prefers-color-scheme: dark) { :root { color-scheme: dark;
  --ground:#0E1512; --surface:#16201A; --ink:#E3ECE5; --muted:#9AAC9F;
  --line:#27352C; --accent:#63BC8E; --clay:#E08A5A; } }
* { box-sizing:border-box }
body { margin:0; background:var(--ground); color:var(--ink); font:16px/1.6
  ui-sans-serif, system-ui, -apple-system, sans-serif; }
.wrap { max-width:600px; margin:0 auto; padding:48px 20px 72px; }
h1 { font-size:2rem; line-height:1.1; margin:0 0 12px; letter-spacing:-0.02em }
h2 { font-size:1.2rem; margin:32px 0 8px }
p { margin:0 0 14px; color:var(--muted) }
label { display:block; font-weight:600; color:var(--ink); margin:16px 0 6px }
input { width:100%; padding:11px 12px; font-size:1rem; border:1px solid var(--line);
  border-radius:8px; background:var(--surface); color:var(--ink) }
button { margin-top:20px; width:100%; padding:12px; font-size:1rem; font-weight:600;
  border:0; border-radius:8px; background:var(--accent); color:#fff; cursor:pointer }
.err { border-left:3px solid var(--clay); background:var(--surface);
  padding:12px 14px; border-radius:0 8px 8px 0; color:var(--ink); margin:16px 0 }
code, .url { font-family:ui-monospace, monospace; font-size:0.85rem;
  background:var(--surface); border:1px solid var(--line); border-radius:8px;
  padding:12px; display:block; word-break:break-all; color:var(--ink) }
.note { font-size:0.9rem }
a { color:var(--accent); text-underline-offset:2px }
"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f'<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{title}</title><style>{_STYLE}</style></head>"
        f"<body><div class=wrap>{body}</div></body></html>",
        status_code=status,
    )


@mcp.custom_route("/", methods=["GET"])
async def index(request: Request) -> Response:
    return page(
        "Garmin for Claude",
        f"""
        <h1>Connect Garmin to Claude</h1>
        <p>Sign in once and you'll get a private link to paste into Claude's
        connector settings. No install, and it works on the web and mobile apps
        as well as the desktop one.</p>
        <p class=note>Your Garmin password goes straight to Garmin, is never
        written to disk, and is dropped from memory as soon as it has been
        exchanged. Only the access token Garmin issues is kept, encrypted.</p>
        <form method=get action=/connect><button>Get started</button></form>
        <p class=note style="margin-top:20px"><a href="{PRIVACY_URL}"
        target=_blank rel=noopener>Privacy</a></p>
        """,
    )


_INVITE_FIELD = """
          <label for=invite>Invite code</label>
          <input id=invite name=invite required autocomplete=off>
"""


@mcp.custom_route("/connect", methods=["GET"])
async def connect_form(request: Request) -> Response:
    # Set when Claude sent them here from /authorize; it ties this sign-in back
    # to the waiting authorization request.
    flow = request.query_params.get("flow", "")
    return page(
        "Sign in to Garmin",
        f"""
        <h1>Sign in to Garmin</h1>
        <p>These go straight to Garmin. The password is never written to disk,
        and is dropped from memory once Garmin has accepted it.</p>
        <form method=post action=/connect>
          <input type=hidden name=flow value="{flow}">
          {_INVITE_FIELD if INVITE_CODE else ""}
          <label for=email>Garmin email</label>
          <input id=email name=email type=email required autocomplete=username>
          <label for=password>Garmin password</label>
          <input id=password name=password type=password required
                 autocomplete=current-password>
          <button>Connect</button>
        </form>
        <p class=note style="margin-top:20px"><a href="{PRIVACY_URL}"
        target=_blank rel=noopener>What&rsquo;s stored, and what isn&rsquo;t</a></p>
        """,
    )


def _signin_error(exc: BaseException) -> str:
    """Explain a failed sign-in in terms that make sense on a web page."""
    text = str(exc).lower()
    if "429" in text or "rate limit" in text or "too many" in text or "cloudflare" in text:
        return (
            "<div class=err><strong>Garmin is blocking sign-ins from this "
            "server's network.</strong> This is not your password — Garmin "
            "refuses the request before checking it. Tell whoever runs this "
            "server; it needs a different sign-in route.</div>"
            "<p><a href=/connect>Try again</a></p>"
        )
    if "401" in text or "unauthorized" in text or "invalid" in text:
        return (
            "<div class=err>Garmin didn't accept that email and password. "
            "Check them at connect.garmin.com and try again.</div>"
            "<p><a href=/connect>Try again</a></p>"
        )
    return (
        f"<div class=err>Sign-in failed: {type(exc).__name__}. "
        "Try again in a moment.</div><p><a href=/connect>Try again</a></p>"
    )


def _finish(request: Request, client: Any, email: str, flow: str = "") -> Response:
    """Persist the session and show the person their private URL."""
    blob = client.client.dumps()
    fingerprint = store.email_fingerprint(email)
    # Retires any URL this person was given before, so signing in again is also
    # how they revoke one they have lost.
    for stale in store.tokens_for(fingerprint):
        _SESSIONS.pop(stale, None)
    user_token = store.save_user(
        mask_email(email) or "hidden", blob, email_hash=fingerprint
    )

    if OAUTH_ENABLED and flow:
        # Came from /authorize: hand the client its code and get out of the way.
        # There is no URL to show, which is the entire point.
        destination = OAUTH_PROVIDER.complete(flow, user_token)
        if destination:
            return RedirectResponse(destination, status_code=303)
        return page(
            "Sign-in expired",
            "<div class=err>That sign-in took too long and the request has "
            "expired. Start again from the connector in Claude.</div>",
            400,
        )

    url = f"{base_url(request)}/u/{user_token}/mcp"
    return page(
        "Connected",
        f"""
        <h1>Connected</h1>
        <p>Add this to Claude &rarr; Settings &rarr; Connectors &rarr; Add custom
        connector. Keep it private: anyone with this link can read your Garmin
        data.</p>
        <div class=url>{url}</div>
        <p class=note style="margin-top:16px">Shown once. Save it now — if you
        lose it, sign in again and you'll get a new one. Signing in again also
        stops the previous link working, so it is how you take back a link you
        have lost.</p>
        <p class=note><a href="/disconnect?t={user_token}">Disconnect and delete
        my stored session</a></p>
        """,
    )


@mcp.custom_route("/disconnect", methods=["GET", "POST"])
async def disconnect(request: Request) -> Response:
    """Let someone destroy their own stored session, using their URL as proof."""
    if request.method == "GET":
        token = request.query_params.get("t", "")
        return page(
            "Disconnect",
            f"""
            <h1>Disconnect</h1>
            <p>This deletes your stored Garmin session. Your link stops working
            straight away, and Claude will no longer see your data. Your Garmin
            account itself is untouched.</p>
            <form method=post action=/disconnect>
              <input type=hidden name=t value="{token}">
              <button>Delete my stored session</button>
            </form>
            """,
        )

    form = await request.form()
    token = str(form.get("t", "")).strip()
    _SESSIONS.pop(token, None)
    removed = store.delete_user(token)
    return page(
        "Disconnected",
        "<h1>Disconnected</h1><p>Your stored session has been deleted and the "
        "link no longer works. Remove the connector in Claude's settings too.</p>"
        if removed
        else "<div class=err>That link isn't one we know about. It may already "
        "have been disconnected.</div>",
    )


@mcp.custom_route("/connect", methods=["POST"])
async def connect_submit(request: Request) -> Response:
    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))

    if _too_many_attempts(_client_address(request)):
        return page(
            "Sign in to Garmin",
            "<div class=err>Too many sign-in attempts. Wait fifteen minutes and "
            "try again.</div>",
            429,
        )

    if INVITE_CODE and not hmac.compare_digest(
        str(form.get("invite", "")).strip(), INVITE_CODE
    ):
        return page(
            "Sign in to Garmin",
            "<div class=err>That invite code isn't right. Ask whoever sent you "
            "the link.</div><p><a href=/connect>Try again</a></p>",
            403,
        )

    if not email or not password:
        return page("Sign in to Garmin", "<div class=err>Email and password required.</div>", 400)

    def _login() -> Any:
        client = build_client(
            prompt_mfa=lambda: (_ for _ in ()).throw(
                RuntimeError("multi-factor code required")
            ),
            email=email,
            password=password,
        )
        # return_on_mfa hands the flow back instead of prompting, so the code can
        # be collected over a second request.
        client.return_on_mfa = True
        with login_egress():
            result = client.login()
        return client, result

    try:
        client, result = await anyio.to_thread.run_sync(_login)
    except Exception as exc:  # noqa: BLE001
        return page("Sign in to Garmin", _signin_error(exc), 400)

    if isinstance(result, tuple) and result and result[0] == "needs_mfa":
        _sweep_pending()
        pending_id = secrets.token_urlsafe(16)
        # garminconnect drops the plaintext password once a login completes, but
        # the MFA path returns early and never reaches that line. Without this,
        # the client parked below would hold the password in memory for the whole
        # ten-minute window — while the sign-in page promises it is not kept.
        # resume_login() works from the MFA state and never reads it.
        client.password = None
        _PENDING[pending_id] = {
            "client": client,
            "state": result[1],
            "email": email,
            "flow": str(form.get("flow", "")),
            "started": time.time(),
        }
        return page(
            "Enter your code",
            f"""
            <h1>Enter your code</h1>
            <p>Garmin sent a multi-factor code to your email or authenticator app.</p>
            <form method=post action=/mfa>
              <input type=hidden name=pending value="{pending_id}">
              <label for=code>Code</label>
              <input id=code name=code inputmode=numeric autocomplete=one-time-code required>
              <button>Continue</button>
            </form>
            """,
        )

    return _finish(request, client, email, str(form.get("flow", "")))


@mcp.custom_route("/mfa", methods=["POST"])
async def mfa_submit(request: Request) -> Response:
    form = await request.form()
    pending_id = str(form.get("pending", ""))
    code = str(form.get("code", "")).strip()
    _sweep_pending()
    pending = _PENDING.pop(pending_id, None)
    if not pending:
        return RedirectResponse("/connect", status_code=303)

    client = pending["client"]
    try:
        def _resume() -> Any:
            with login_egress():
                return client.resume_login(pending["state"], code)

        await anyio.to_thread.run_sync(_resume)
    except Exception as exc:  # noqa: BLE001
        return page(
            "Enter your code",
            f"<div class=err>That code wasn't accepted ({type(exc).__name__}). "
            "Start again from <a href=/connect>the sign-in page</a>.</div>",
            400,
        )
    return _finish(request, client, pending["email"], pending.get("flow", ""))


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return HTMLResponse("ok")


@mcp.custom_route("/stats", methods=["GET"])
async def stats(request: Request) -> Response:
    """How many people used this in the last 30 days, for the front page.

    Public on purpose: it is the number the site quotes, and it is all this
    returns. No emails, no tokens, no per-person anything. The CORS header is
    what lets a page on another host read it.
    """
    now = time.time()
    if now - _STATS_CACHE["at"] > _STATS_TTL:
        _STATS_CACHE["body"] = json.dumps({"active_30d": store.count_active(30)})
        _STATS_CACHE["at"] = now
    return Response(
        _STATS_CACHE["body"],
        media_type="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": f"public, max-age={_STATS_TTL}",
        },
    )


# --------------------------------------------------------------------------
# Binding each MCP request to its owner
# --------------------------------------------------------------------------


class SessionBinding:
    """Resolves /u/<token>/mcp to a Garmin session for the request's duration.

    Runs as middleware rather than inside the route so the context variable is
    set before the MCP machinery starts consuming the request body.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        if OAUTH_ENABLED:
            # Everyone shares one path; who is asking comes from the bearer
            # token the SDK has already verified, not from the URL.
            if scope.get("path", "").rstrip("/") != "/mcp":
                await self.app(scope, receive, send)
                return

            # Read the header rather than the SDK's auth context: add_middleware
            # wraps outermost, so this runs before that context is populated and
            # get_access_token() is always None here. The SDK still does the
            # enforcing — an unrecognised token falls through to its 401 below.
            header = ""
            for key, value in scope.get("headers", []):
                if key.lower() == b"authorization":
                    header = value.decode("latin-1")
                    break
            if not header.lower().startswith("bearer "):
                await self.app(scope, receive, send)
                return
            row = store.load_oauth_token(header[7:].strip(), "access")
            user_token = row["subject"] if row else None
            if not user_token:
                await self.app(scope, receive, send)
                return
        else:
            match = _PATH_RE.match(scope.get("path", ""))
            if not match:
                await self.app(scope, receive, send)
                return
            user_token = match.group("token")

        if store.get_user(user_token) is None:
            # The Garmin session behind this token is gone — disconnected, or
            # signed in again. The token outlived what it pointed at.
            store.delete_tokens_for_subject(user_token)
            _SESSIONS.pop(user_token, None)
            await Response("Unknown connector URL", status_code=404)(scope, receive, send)
            return

        now = time.time()
        if now - _TOUCHED.get(user_token, 0.0) > _TOUCH_EVERY:
            _TOUCHED[user_token] = now
            store.touch_user(user_token)

        token = use_session(session_for(user_token))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_session(token)


def build_app() -> Any:
    """Build the ASGI app, in whichever auth mode is configured.

    Both modes ship in one image so this can be turned on, and off again, with
    an environment variable rather than a rollback. The URL mode stays the
    default until the OAuth one has been run against a real client.
    """
    if not OAUTH_ENABLED:
        app = mcp.streamable_http_app(
            streamable_http_path=MCP_PATH,
            stateless_http=True,
            host=os.environ.get("GARMIN_MCP_HOST", "0.0.0.0"),
        )
        app.add_middleware(SessionBinding)
        return app

    from mcp.server.auth.settings import (
        AuthSettings,
        ClientRegistrationOptions,
        RevocationOptions,
    )
    from pydantic import AnyHttpUrl

    base = os.environ.get("GARMIN_MCP_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "GARMIN_MCP_BASE_URL must be set when GARMIN_MCP_OAUTH=1: it is the "
            "issuer identity in the OAuth metadata, and clients check it."
        )

    from mcp.server.auth.provider import ProviderTokenVerifier

    mcp._auth_server_provider = OAUTH_PROVIDER
    # The constructor derives this from the provider; attaching the provider
    # afterwards skips that, and a server with no verifier authenticates
    # nothing — it served unauthenticated requests happily until a test asked.
    mcp._token_verifier = ProviderTokenVerifier(OAUTH_PROVIDER)
    mcp.settings.auth = AuthSettings(
        issuer_url=AnyHttpUrl(base),
        resource_server_url=AnyHttpUrl(base),
        # Claude registers itself; there is no console to add clients by hand.
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
        # Off by default today, which would accept a token minted for a
        # different resource. Nothing here wants that.
        validate_token_resource=True,
    )

    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        host=os.environ.get("GARMIN_MCP_HOST", "0.0.0.0"),
    )
    app.add_middleware(SessionBinding)
    return app


class RedactUserTokens(logging.Filter):
    """Keep connector URLs out of the logs.

    The path of every MCP request contains the user's token, and that token is
    the credential — the whole auth model is that holding the URL is holding the
    account. Uvicorn's access log wrote one to the log stream on every single
    tool call, where the hosting dashboard and any log drain could read it back.

    Access logging is off below; this is the second line of defence, for
    tracebacks and anything else that quotes a path.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        redacted = _PATH_RE_ANY.sub(r"/u/<redacted>/mcp", str(record.getMessage()))
        if redacted != str(record.getMessage()):
            record.msg, record.args = redacted, ()
        return True


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactUserTokens())

    uvicorn.run(
        build_app(),
        host=os.environ.get("GARMIN_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        # The request path is a credential. There is no way to keep an access
        # log that records paths without logging everyone's key.
        access_log=False,
    )


if __name__ == "__main__":
    main()
