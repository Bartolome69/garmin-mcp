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

from . import store
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

# Sign-ins waiting on a multi-factor code. In memory on purpose: a restart
# simply asks the person to start again, and nothing sensitive outlives it.
_PENDING: dict[str, dict[str, Any]] = {}
_PENDING_TTL = 600

# One live session per person, reused across their requests so the token is not
# re-read on every tool call.
_SESSIONS: dict[str, GarminSession] = {}


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
        """
        <h1>Connect Garmin to Claude</h1>
        <p>Sign in once and you'll get a private link to paste into Claude's
        connector settings. No install, and it works on the web and mobile apps
        as well as the desktop one.</p>
        <p class=note>Your Garmin password is used once to sign in and is then
        discarded — only the access token Garmin issues is kept, encrypted.</p>
        <form method=get action=/connect><button>Get started</button></form>
        """,
    )


@mcp.custom_route("/connect", methods=["GET"])
async def connect_form(request: Request) -> Response:
    return page(
        "Sign in to Garmin",
        """
        <h1>Sign in to Garmin</h1>
        <p>These go straight to Garmin. The password is not stored.</p>
        <form method=post action=/connect>
          <label for=email>Garmin email</label>
          <input id=email name=email type=email required autocomplete=username>
          <label for=password>Garmin password</label>
          <input id=password name=password type=password required
                 autocomplete=current-password>
          <button>Connect</button>
        </form>
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


def _finish(request: Request, client: Any, email: str) -> Response:
    """Persist the session and show the person their private URL."""
    blob = client.client.dumps()
    user_token = store.save_user(mask_email(email) or "hidden", blob)
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
        lose it, sign in again and you'll get a new one.</p>
        """,
    )


@mcp.custom_route("/connect", methods=["POST"])
async def connect_submit(request: Request) -> Response:
    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))
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
        _PENDING[pending_id] = {
            "client": client,
            "state": result[1],
            "email": email,
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

    return _finish(request, client, email)


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
    return _finish(request, client, pending["email"])


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return HTMLResponse("ok")


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

        match = _PATH_RE.match(scope.get("path", ""))
        if not match:
            await self.app(scope, receive, send)
            return

        user_token = match.group("token")
        if store.get_user(user_token) is None:
            await Response("Unknown connector URL", status_code=404)(scope, receive, send)
            return

        token = use_session(session_for(user_token))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_session(token)


def build_app() -> Any:
    app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        host=os.environ.get("GARMIN_MCP_HOST", "0.0.0.0"),
    )
    app.add_middleware(SessionBinding)
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        build_app(),
        host=os.environ.get("GARMIN_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
