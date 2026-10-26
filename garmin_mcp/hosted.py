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

import asyncio
import contextlib
import functools
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import threading
import time
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlparse

import anyio
from starlette.requests import Request
from starlette.background import BackgroundTask
from starlette.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

from . import analytics, backfill, crews, geo, hooks, oauth, official, official_data, preview, store, support_mail, weekly
from . import server as server_mod
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

# What to ask, by sport, for people who were only ever sent the connector
# address and have never seen the website. Linked from the sign-in page and
# given once in the first reply. Overridable for the same reason as above.
GUIDE_URL = (
    os.environ.get("GARMIN_MCP_GUIDE_URL", "").strip()
    or "https://daash.run/guide"
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

# Which sign-in someone arriving from the connector gets: "password", their
# Garmin email and password on Daash's form as since launch, or "official",
# approving Daash on Garmin's own page. A browser can be put on either with
# ?route=official or ?route=password on /connect, remembered in a cookie,
# which is how the official route is tried before it's everyone's.
SIGNUP_ROUTE = os.environ.get("GARMIN_MCP_SIGNUP", "password").strip().lower()
ROUTE_COOKIE = "daash_route"
ROUTES = ("official", "password")
# "split" puts each new browser on one of the two at random, kept in the same
# cookie, to see which more people finish and keep using (from Oct 26).
SPLIT = SIGNUP_ROUTE == "split"
SPLIT_NAME = "signup-route-oct26"
# Consents waiting after an approval on Garmin's page, by consent token, with
# when they were staged: those are the ones that ask for an email if the
# account has none, and that count as an official sign-up when allowed.
_GARMIN_CONSENTS: dict[str, float] = {}
_EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}")

# Crude per-address throttle on sign-in attempts. Not a defence against a
# determined attacker; enough to stop this being a comfortable place to test
# stolen Garmin passwords.
_ATTEMPTS: dict[str, list[float]] = {}
_ATTEMPT_WINDOW = 900
_ATTEMPT_LIMIT = 8

# A server-wide pause on sign-ins after Garmin says "too many" or blocks the
# address. Everyone signs in through the same address, so Garmin's limit is
# shared, and each retry while it is in force counts against it and pushes it
# towards a block. During the pause nothing is sent to Garmin; people are told
# how long to wait instead. In memory: one machine serves this, and a restart
# that forgets the pause costs at most one more refused attempt. Only a refusal
# of the server's own address pauses anything: through the sign-in proxy each
# attempt has an address of its own, so one refusal says nothing about the next.
GARMIN_PAUSE = 1800
_paused_until = 0.0

# Through the sign-in proxy, a refused sign-in is tried again from another
# address, up to this many in all. A failing Garmin sign-in can take a while
# (the library tries several methods, with pauses between), so no new attempt
# starts once this many seconds have gone: the person is waiting on the page.
# A refused address still costs one of the library's 10 to 20 second waits
# before the refusal shows, so a refusal arrives after about 25 seconds. The
# budget lets two more addresses follow it: on CAPTCHA-heavy evenings the
# server's test sign-ins have needed the third. The page is kept open past
# Fly's 60 second limit by _answer_in_time.
SIGNIN_TRIES = 4
SIGNIN_BUDGET = 60


def _pause_sign_ins(reason: str) -> None:
    global _paused_until
    if reason in ("rate_limited", "blocked_by_garmin"):
        _paused_until = max(_paused_until, time.time() + GARMIN_PAUSE)
        log.warning("sign-ins paused for %ss after %s", GARMIN_PAUSE, reason)


def _pause_left() -> int:
    """Seconds left on the pause, or 0."""
    return max(0, int(_paused_until - time.time()))


# When Garmin last said each person's password was wrong, by email
# fingerprint. Garmin pauses an account after a few wrong passwords, and the
# refusal that follows looks like any other refusal; within this window it is
# read as that pause. Telling that person "it is not your password" (Oct 13)
# sent them straight back to try the same password again.
_WRONG_PASSWORD: dict[str, float] = {}
WRONG_PASSWORD_WINDOW = 30 * 60


def _note_wrong_password(email: str) -> None:
    now = time.time()
    _WRONG_PASSWORD[store.email_fingerprint(email)] = now
    if len(_WRONG_PASSWORD) > 5000:
        for key, at in list(_WRONG_PASSWORD.items()):
            if at < now - WRONG_PASSWORD_WINDOW:
                _WRONG_PASSWORD.pop(key, None)


def _recent_wrong_password(email: str) -> bool:
    at = _WRONG_PASSWORD.get(store.email_fingerprint(email)) if email else None
    return at is not None and at > time.time() - WRONG_PASSWORD_WINDOW


# Where the person came from, as the website remembered it: a cookie set on
# daash.run with the referring site or campaign, readable here because both
# sites live under that domain. A word such as "chatgpt.com" or "search",
# never anything about the person.
SOURCE_COOKIE = "daash_src"
# The one optional question on the consent page, answered by a tap or not.
HEARD_FROM = (("chatgpt", "ChatGPT"), ("claude", "Claude"), ("friend", "A friend"),
              ("search", "Search"), ("instagram", "Instagram"), ("other", "Somewhere else"))


def _source(request: Request) -> str | None:
    raw = request.cookies.get(SOURCE_COOKIE, "")
    clean = re.sub(r"[^a-z0-9._-]", "", raw.lower())[:40]
    return clean or None


def _with_source(request: Request, properties: dict[str, Any]) -> dict[str, Any]:
    source = _source(request)
    return {**properties, **({"source": source} if source else {}), **_where(request), **_split_arm(request),
            **_device(request)}


def _device(request: Request) -> dict[str, str]:
    """Phone or computer, and which kind of browser: never the browser string itself.

    Oct 26: one person approved Daash on Garmin's page five times before
    Claude started using it, and nothing said whether they were on a phone,
    where a sign-in runs in the app's own browser.
    """
    agent = request.headers.get("user-agent", "")
    low = agent.lower()
    if not agent:
        return {}
    device = "tablet" if "ipad" in low or ("android" in low and "mobile" not in low) else \
        "phone" if any(k in low for k in ("iphone", "android", "mobile")) else "computer"
    browser = ("in-app" if any(k in low for k in ("claude", "chatgpt", "openai", "; wv)", "fban", "instagram"))
               else "edge" if "edg/" in low else "firefox" if "firefox" in low or "fxios" in low
               else "chrome" if "chrome" in low or "crios" in low else "safari" if "safari" in low else "other")
    return {"device": device, "browser": browser}


def _split_arm(request: Request) -> dict[str, str]:
    """Which side of the sign-in split this browser is on, while the split runs."""
    arm = request.cookies.get(ROUTE_COOKIE, "")
    return {"split": SPLIT_NAME, "split_arm": arm} if SPLIT and arm in ROUTES else {}


def _where(request: Request) -> dict[str, str]:
    """The visitor's country, looked up here from their address; the address itself is never sent."""
    code = geo.country(_client_address(request))
    return {"country": code} if code else {}


def _failed(reason: str, email: str = "", **extra: Any) -> None:
    """Record a failed sign-in: the reason, and who tried.

    The address tried, masked and in full, and the same per-person id a
    successful sign-in gets, so a failure and a later success by the same
    person line up in the report and whoever got stuck can be helped.
    """
    email = email.strip()
    who = {"account": mask_email(email), "email": email} if email else {}
    analytics.capture(
        "sign_in_failed",
        store.email_fingerprint(email) if email else None,
        {"reason": reason, **who, **extra},
    )


def _retried(reason: str, then: str, email: str = "") -> None:
    """Record a sign-in that failed once and is being tried again.

    `then` is how: "new_address" through a fresh proxy address, or "direct"
    when the proxy itself failed, which needs looking at.
    """
    email = email.strip()
    who = {"account": mask_email(email), "email": email} if email else {}
    analytics.capture(
        "sign_in_retried",
        store.email_fingerprint(email) if email else None,
        {"reason": reason, "then": then, **who},
    )


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

# The UTC day each person was last reported as using the connector. One event
# per person per day is what "who uses this daily" needs; one per request would
# be a conversation's worth of noise. In memory, so a restart may report a day
# twice, which a count of distinct people per day absorbs.
_USED_ON: dict[str, str] = {}

# The front page shows how many people used this recently. One number, cached,
# so a busy page never turns into a query per visitor.
_STATS_CACHE: dict[str, Any] = {"at": 0.0, "body": ""}
_STATS_TTL = 300


# Cloudflare in front of Garmin's SSO blocks datacenter addresses — verified
# from a GitHub Actions runner, and reported the same way by other hosted
# gateways. The data API is unaffected, so only sign-in needs a clean egress.
#
# garminconnect has no proxy setting, so the proxy goes in where it opens its
# connections: the `requests` and `curl_cffi` it imports are swapped for
# versions whose sessions, and one-off posts, take the proxy of the sign-in
# running on that thread. Each sign-in runs on its own worker thread, so
# sign-ins run side by side, each through its own address, and nothing else
# the server does (anyone's data calls) is touched. Environment variables did
# this before, but they are shared by the whole process, so sign-ins had to
# queue behind a lock.
_EGRESS = threading.local()


def _egress_proxy() -> str:
    return getattr(_EGRESS, "proxy", "")


class _Refused(Exception):
    """Garmin's sign-in server turned this address away; stop using it."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Garmin refused this sign-in address (HTTP {status})")


# Once Garmin's sign-in server answers a proxied attempt with 403 or 429, that
# address is spent. garminconnect would carry on through its other sign-in
# methods regardless, some with 10 to 20 seconds of deliberate waiting, which
# made one refused attempt take over a minute and left no time to try another
# address. So the rest of that attempt fails at once and skips its waits, and
# the retry goes out from a fresh address in seconds. Only proxied attempts:
# from the server's own address there is no other address to move to.
#
# The library's first method, the mobile app's sign-in (/mobile/), is often
# turned away for reasons that have nothing to do with the address: Garmin
# limits the app's client ID as a whole. The library expects that and moves on
# to the web sign-in, which is what usually gets people in. So only a refusal
# from the web sign-in counts; counting the mobile one stopped every sign-in.
#
# The web sign-in's API can also turn an address away inside a 200 answer:
# a 429 in its JSON, a CAPTCHA demand, or a challenge page where JSON should
# be. The library falls through to its next browser profile on each, after
# another 10 to 20 second wait, so one turned-away address took 44 seconds
# in the portal alone. Each counts as a refusal here, so that wait comes once.
def _note_refusal(url: str, status: int, response: Any = None) -> None:
    url = str(url)
    # Whatever the HTTP status: Garmin answers a wrong password with a 401
    # and its JSON, so a CAPTCHA demand may not come with a 200 either.
    if response is not None and "/portal/api/" in url:
        answer = _portal_answer(response)
        trace = getattr(_EGRESS, "trace", None)
        if trace is not None:
            trace["answer"] = f"{answer}@{status}"
        if status not in (403, 429) and answer in ("429", "CAPTCHA_REQUIRED", "not_json"):
            status = 429 if answer == "429" else 403
    if status in (403, 429) and _egress_proxy() and "//sso." in url and "/mobile/" not in url:
        if not getattr(_EGRESS, "refused", None):
            log.info("sign-in address refused (HTTP %s); moving on", status)
        _EGRESS.refused = status


_ANSWER_TYPE = re.compile(r"[A-Z_]{1,40}")


def _portal_answer(response: Any) -> str:
    """What the web sign-in's API said, as one word: "429" for a limit in its
    JSON, Garmin's own response type (SUCCESSFUL, MFA_REQUIRED,
    INVALID_USERNAME_PASSWORD, CAPTCHA_REQUIRED...), "not_json" for a page
    where an answer should be. Only Garmin's capitalised codes get through,
    never anything else from the answer."""
    try:
        body = json.loads(response.text)
    except Exception:  # noqa: BLE001 - a challenge page, or nothing
        return "not_json"
    if not isinstance(body, dict):
        return "not_json"
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    if str(error.get("status-code") or "") == "429":
        return "429"
    status = body.get("responseStatus") if isinstance(body.get("responseStatus"), dict) else {}
    kind = str(status.get("type") or "")
    return kind if _ANSWER_TYPE.fullmatch(kind) else "unknown"


def _check_refused() -> None:
    status = getattr(_EGRESS, "refused", None)
    if status:
        raise _Refused(status)


class _Clock:
    """garminconnect's `time`, whose sleeps end early once an address is spent."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def sleep(self, seconds: float) -> None:
        if not getattr(_EGRESS, "refused", None):
            trace = getattr(_EGRESS, "trace", None)
            if trace is not None:
                trace["waited"] += seconds
            self._real.sleep(seconds)


def _proxied(kwargs: dict[str, Any]) -> dict[str, Any]:
    proxy = _egress_proxy()
    if proxy and not kwargs.get("proxies") and not kwargs.get("proxy"):
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return kwargs


class _Via:
    """A stand-in for `requests` or `curl_cffi.requests` inside garminconnect."""

    def __init__(self, real: Any, session: type) -> None:
        self._real = real
        self.Session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def get(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        _check_refused()
        r = self._real.get(url, *args, **_proxied(kwargs))
        _note_refusal(url, getattr(r, "status_code", 0), r)
        return r

    def post(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        _check_refused()
        r = self._real.post(url, *args, **_proxied(kwargs))
        _note_refusal(url, getattr(r, "status_code", 0), r)
        return r


def _route_garmin_sign_ins() -> None:
    import requests
    from garminconnect import client as gc

    if isinstance(getattr(gc, "requests", None), _Via):
        return
    if not isinstance(getattr(gc, "requests", None), type(requests)):
        # A garminconnect release that opens connections differently: say so
        # loudly, since sign-ins would then leave from the server's address.
        log.error("garminconnect has changed; sign-ins will not use the proxy")
        return

    class RequestsSession(requests.Session):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            proxy = _egress_proxy()
            if proxy:
                self.proxies.update({"http": proxy, "https": proxy})
                # Otherwise a proxy in the environment would win over this one.
                self.trust_env = False

        def request(self, method: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
            _check_refused()
            r = super().request(method, url, *args, **kwargs)
            _note_refusal(url, getattr(r, "status_code", 0), r)
            return r

    gc.requests = _Via(requests, RequestsSession)
    gc.time = _Clock(time)

    real_cffi = getattr(gc, "cffi_requests", None)
    if real_cffi is not None:
        class CffiSession(real_cffi.Session):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **_proxied(kwargs))

            def request(self, method: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
                _check_refused()
                r = super().request(method, url, *args, **kwargs)
                _note_refusal(url, getattr(r, "status_code", 0), r)
                return r

        gc.cffi_requests = _Via(real_cffi, CffiSession)

    if getattr(gc, "Client", None) is not None:
        _trace_methods(gc.Client)


# garminconnect's sign-in methods, by the short name a sign-in's trace uses.
# portal_try is one browser profile's go inside the portal method, which
# tries several; its outcome carries what Garmin answered ("other/CAPTCHA_REQUIRED").
_METHODS = {
    "_mobile_login_cffi": "mobile",
    "_mobile_login_requests": "mobile_plain",
    "_widget_web_login": "widget",
    "_portal_web_login_cffi": "portal",
    "_portal_web_login_requests": "portal_plain",
    "_do_portal_web_login": "portal_try",
}


def _outcome(exc: BaseException) -> str:
    """One word for how a sign-in method ended: its kind, never its message."""
    name = type(exc).__name__
    if isinstance(exc, _Refused):
        return "refused"
    if name == "GarminConnectTooManyRequestsError":
        return "429"
    if name == "GarminConnectAuthenticationError":
        return "wrong_password"
    if name == "_MFARequired":
        return "code"
    if name == "GarminConnectConnectionError":
        return "other"
    return name


def _trace_methods(cls: Any) -> None:
    """Note how each sign-in method ended and how long it took, per sign-in.

    Where a refused sign-in spends its minute is otherwise invisible: the
    library tries five methods in turn and only the last one's error comes
    out. Names and timings only, kept on the sign-in's own trace.
    """
    for attr, short in _METHODS.items():
        real = getattr(cls, attr, None)
        if real is None or getattr(real, "_traced", False):
            continue

        def traced(self: Any, *args: Any, _real: Any = real, _short: str = short, **kwargs: Any) -> Any:
            trace = getattr(_EGRESS, "trace", None)
            if trace is not None:
                trace.pop("answer", None)
            began = time.monotonic()
            try:
                result = _real(self, *args, **kwargs)
                outcome = "ok"
                return result
            except BaseException as exc:
                outcome = _outcome(exc)
                raise
            finally:
                if trace is not None:
                    answer = trace.pop("answer", None) if _short == "portal_try" else None
                    if answer:
                        outcome = f"{outcome}/{answer}"
                    # Browser profiles skipped once the address is spent say nothing new.
                    if not (_short == "portal_try" and outcome == "refused"):
                        trace["steps"].append(f"{_short}:{outcome}:{time.monotonic() - began:.1f}")

        traced._traced = True  # type: ignore[attr-defined]
        setattr(cls, attr, functools.wraps(real)(traced))


try:
    _route_garmin_sign_ins()
except ImportError:  # pragma: no cover - install problem, reported at sign-in
    log.error("garminconnect is not installed; sign-ins will fail")


# Residential proxies like IPRoyal pick the exit address by a session id in the
# password ("..._session-ab12cd34_lifetime-30m"): one id, one home address for
# its lifetime. A fresh id per sign-in means a refusal from Garmin sticks to
# that one address, not to everyone who signs in over the next half hour, and
# a retry goes out from somewhere else. A proxy without the marker is used as
# it is.
_PROXY_SESSION = re.compile(r"(_session-)[A-Za-z0-9]+")


def login_proxy() -> str:
    """The sign-in proxy with a fresh session id, or "" to connect directly."""
    proxy = os.environ.get("GARMIN_MCP_LOGIN_PROXY", "").strip()
    return _PROXY_SESSION.sub(lambda m: m.group(1) + secrets.token_hex(4), proxy, count=1)


@contextlib.contextmanager
def login_egress(proxy: str | None = None):
    """Send this thread's Garmin connections through `proxy`, "" for direct.

    Left out, it is the configured proxy as written, if any. A client built
    inside keeps the proxy on its own sessions, so its code step and token
    checks leave from the same address.
    """
    if proxy is None:
        proxy = os.environ.get("GARMIN_MCP_LOGIN_PROXY", "").strip()
    previous = (_egress_proxy(), getattr(_EGRESS, "refused", None), getattr(_EGRESS, "trace", None))
    # What this attempt did: each sign-in method's outcome and time, and how
    # long the library spent waiting on purpose.
    trace: dict[str, Any] = {"steps": [], "waited": 0.0}
    _EGRESS.proxy, _EGRESS.refused, _EGRESS.trace = proxy, None, trace
    try:
        yield trace
    finally:
        _EGRESS.proxy, _EGRESS.refused, _EGRESS.trace = previous


def base_url(request: Request) -> str:
    configured = os.environ.get("GARMIN_MCP_BASE_URL", "").strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


RECONNECT_HINT = (
    "Garmin has signed this connection out, which happens after a Garmin "
    "password change or when Garmin ends a session itself. Nothing is lost. "
    "Daash will ask to be reconnected: choose Connect (in Claude, Settings > "
    "Connectors > Daash) and sign in to Garmin again."
)
# One report per person per day, however many tool calls hit the dead session.
_LAPSE_REPORTED: dict[str, str] = {}


def report_lapsed(user_token: str, *, source: str) -> None:
    """Tell the analytics a Garmin session is gone, so someone can reach out."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if _LAPSE_REPORTED.get(user_token) == today:
        return
    _LAPSE_REPORTED[user_token] = today
    user = store.get_user(user_token)
    if user is None:
        return
    email = store.email_for(user_token)
    analytics.capture(
        "garmin_session_lapsed",
        user.email_hash or user_token,
        {"account": user.email_masked, "source": source, **({"email": email} if email else {})},
    )


def _lapsed_in_use(user_token: str) -> None:
    """Garmin refused the stored session: resuming from it failed.

    Revoking the app's tokens turns the next request into a 401, which Claude
    shows as a Reconnect button, rather than every tool failing with text the
    person has to read. The Garmin row stays, so reconnecting resumes it under
    the same identity. The keep-alive never does this: an idle failure might
    be Garmin having a bad hour, and nobody is waiting on it.
    """
    report_lapsed(user_token, source="in_use")
    if OAUTH_ENABLED:
        store.delete_tokens_for_subject(user_token)


# What's new, said once to each person in their next chat. Raise NEWS_VERSION
# with new text and everyone hears the new note once, on their next request.
NEWS_VERSION = 2
NEWS = (
    "Tell the user this once, in a sentence or two at the end of your reply, "
    "then carry on as normal: Daash now draws their training as a card. Asking "
    "\"How's my training going?\" shows their week, or their plan if they "
    "have one, with each session coloured by effort (easy, threshold, "
    "intervals) and marked with its sport, the next workout's steps, and "
    "buttons to move it or make it easier."
)
# Said once to everyone, new or not, the first time a tool answers. Claude asks
# before each tool it hasn't been told to trust, and "Always allow" covers one
# tool at a time, so with twenty-odd tools it feels like asking every time.
# Claude's connector settings allow the read-only ones in one go.
PERMISSION_TIP = (
    "Tell the user this once, in two or three short sentences at the end of "
    "your reply, then carry on as normal: asking \"How's my training going?\" "
    "shows their week, or their plan, as a card. If Claude keeps asking "
    "permission before it uses Daash, they can stop that in Claude's Settings, "
    "Connectors, Daash, by setting the read-only tools to Always allow; "
    "anything that changes their Garmin calendar or workouts will still ask "
    f"first. For more ideas of what to ask, by sport, there is a short guide at {GUIDE_URL}."
)
# For the model, with the tip, on someone's first answer: their first chat is
# where gathering everything at once is most tempting, and slowest.
WELCOME = (
    "For you, not to repeat: this person has only just connected Daash. Get to "
    "know where they are before anything else. For a question about their "
    "training, get_plan alone shows it (a block under way, one about to start, "
    "a race ahead, or just running) and its situation offers the one next step "
    "to suggest. Leave readiness, fitness and trends until they ask or say yes."
)
# get_plan answers a general question on its own, and the instructions and
# its description say so, but a first chat still sent readiness, fitness,
# trends and activities with it, all at once: the person got a wall of numbers
# in place of the card and its one offer. So these, when sent at the same time
# as get_plan, answer with a note rather than their data. The same read asked
# again goes through, so a question that wanted both still gets both. Only
# reads answered in writing: a held card tool would draw an empty card.
WITH_PLAN_HELD = {
    "get_readiness", "get_fitness", "get_fitness_trends", "get_recovery_trends",
    "get_activities", "get_daily_summary", "get_sleep_data",
}
# Each of them answers no sooner than this, so a get_plan sent in the same
# breath has started by the time it looks.
WITH_PLAN_SETTLE = 1.0
# A get_plan that ended this little before a read started was sent with it.
WITH_PLAN_SLACK = 0.5
# A read held back and asked for again within this goes through.
WITH_PLAN_RETRY = 900
# No get_plan runs longer than this; one still unfinished after it was dropped.
WITH_PLAN_LONGEST = 120
WITH_PLAN_NOTE = (
    "Not fetched: get_plan was called at the same time, and its card answers a "
    "general question about training on its own. Answer from the card and close "
    "with its offer. If the user asked for this too, call this tool again now "
    "and it will answer."
)
# When each person's latest get_plan started and, once it has, ended.
_PLAN_CALLS: dict[str, tuple[float, float | None]] = {}
# The reads held back, by person and tool, and when.
_HELD: dict[tuple[str, str], float] = {}
# When the tool now running started, for its after hook.
_STARTED: ContextVar[float] = ContextVar("hosted_tool_started", default=0.0)


def _before_tool_for(user_token: str, alone: bool = False) -> hooks.Before:
    """The start hook for one person's request: note a get_plan, pace the reads sent with one.

    An account on the official API alone, as the directories list Daash, holds
    nothing back, so nothing is paced.
    """

    def before(name: str) -> float:
        now = time.monotonic()
        _STARTED.set(now)
        if name == "get_plan":
            if len(_PLAN_CALLS) > 20000:
                _PLAN_CALLS.clear()
            _PLAN_CALLS[user_token] = (now, None)
        return WITH_PLAN_SETTLE if name in WITH_PLAN_HELD and not alone else 0.0
    return before


def _sent_with_plan(user_token: str, name: str) -> bool:
    """Whether this read was sent with a get_plan, and so is held back, this once."""
    now = time.monotonic()
    started = _STARTED.get() or now
    if now - _HELD.pop((user_token, name), float("-inf")) < WITH_PLAN_RETRY:
        return False
    plan = _PLAN_CALLS.get(user_token)
    if plan is None:
        return False
    plan_start, plan_end = plan
    if plan_end is None and now - plan_start > WITH_PLAN_LONGEST:
        return False  # a get_plan that never finished, such as a dropped request
    if plan_end is not None and plan_end < started - WITH_PLAN_SLACK:
        return False
    if len(_HELD) > 20000:
        _HELD.clear()
    _HELD[(user_token, name)] = now
    return True


# One report per person per tool per day: enough to see which features are
# used, without a row for every call.
_TOOL_REPORTED: dict[tuple[str, str], str] = {}
# Who has had a tool answer since the server started. The first answer after a
# restart or deploy signs in to Garmin again, so it is slower, and is marked.
_WARM: set[str] = set()
# How long each tool took this hour, sent once the hour is over as one
# anonymous row per tool: how many calls, how long they took, and how many
# were someone's first since a restart. No person, nothing they asked.
_TIMINGS: dict[str, list[tuple[float, bool]]] = {}
_TIMING_HOUR = [""]


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _note_timing(name: str, seconds: float, cold: bool) -> None:
    """Add one call to this hour's timings, sending the last hour's first."""
    hour = time.strftime("%Y-%m-%dT%H", time.gmtime())
    if _TIMING_HOUR[0] != hour:
        _send_timings(_TIMING_HOUR[0])
        _TIMING_HOUR[0] = hour
    _TIMINGS.setdefault(name, []).append((seconds, cold))


def _send_timings(hour: str) -> None:
    for tool, calls in _TIMINGS.items():
        took = [s for s, _ in calls]
        analytics.capture(
            "tool_timing",
            None,
            {"tool": tool, "hour": hour, "calls": len(took),
             "median_s": round(_quantile(took, 0.5), 2),
             "p90_s": round(_quantile(took, 0.9), 2),
             "max_s": round(max(took), 2),
             "cold_calls": sum(1 for _, cold in calls if cold)},
        )
    _TIMINGS.clear()


# How server.tool_errors words an exception it didn't expect: Garmin
# changing an endpoint, or a bug. Answers like "that date has passed" are
# the tool working, and are not counted.
_UNEXPECTED = re.compile(r"Garmin request failed \((\w+)[:)]")


def _after_tool_for(user_token: str, user: store.User, alone: bool = False) -> hooks.Hook:
    """The hook for one person's request: report the tool, carry any news.

    An account on the official API alone gets its answers as they are: no read
    held back, and no tip, welcome or news added for the model to pass on. The
    directories ask that a connector not talk to the model past its own data.
    """
    crew_key = crews.member_key(user_token, user.email_hash)

    def after(name: str, result: Any) -> Any:
        if name == "get_plan" and user_token in _PLAN_CALLS:
            _PLAN_CALLS[user_token] = (_PLAN_CALLS[user_token][0], time.monotonic())
        if (
            not alone and name in WITH_PLAN_HELD and isinstance(result, dict) and "error" not in result
            and _sent_with_plan(user_token, name)
        ):
            email = store.email_for(user_token)
            analytics.capture(
                "tool_held",
                user.email_hash or user_token,
                {"tool": name, "account": user.email_masked, **({"email": email} if email else {})},
            )
            return {"for_the_assistant": WITH_PLAN_NOTE}
        # A crew member's coming runs reach their crew whenever they look at
        # their own plan: no extra Garmin calls, nothing for anyone else.
        if name in ("get_plan", "create_plan") and isinstance(result, dict) and "error" not in result:
            try:
                crews.capture(crew_key, result)
            except Exception:  # noqa: BLE001 - the crew is a side line; the plan must still answer
                log.debug("crew capture failed", exc_info=True)
        failed = (
            _UNEXPECTED.match(result["error"])
            if isinstance(result, dict) and isinstance(result.get("error"), str) else None
        )
        if failed:
            # The type only: the message can hold the person's own data.
            email = store.email_for(user_token)
            analytics.capture(
                "tool_failed",
                user.email_hash or user_token,
                {"tool": name, "error_type": failed.group(1), "account": user.email_masked,
                 **({"email": email} if email else {})},
            )
        seconds = hooks.seconds()
        cold = user_token not in _WARM
        _WARM.add(user_token)
        if seconds is not None:
            _note_timing(name, seconds, cold)
        timing = (
            {"seconds": round(seconds, 2), "first_since_start": cold}
            if seconds is not None else {}
        )
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if _TOOL_REPORTED.get((user_token, name)) != today:
            _TOOL_REPORTED[(user_token, name)] = today
            if len(_TOOL_REPORTED) > 20000:
                _TOOL_REPORTED.clear()
            email = store.email_for(user_token)
            analytics.capture(
                "tool_used",
                user.email_hash or user_token,
                {"tool": name, "account": user.email_masked, **timing,
                 **({"email": email} if email else {})},
            )
        if alone:
            return result
        if (
            isinstance(result, dict) and "error" not in result
            and store.claim_news(user_token, NEWS_VERSION)
        ):
            email = store.email_for(user_token)
            analytics.capture(
                "whats_new_shown",
                user.email_hash or user_token,
                {"version": NEWS_VERSION, "tool": name, "account": user.email_masked,
                 **({"email": email} if email else {})},
            )
            return {"whats_new": NEWS, **result}
        if (
            isinstance(result, dict) and "error" not in result
            and store.claim_tip(user_token)
        ):
            email = store.email_for(user_token)
            analytics.capture(
                "permission_tip_shown",
                user.email_hash or user_token,
                {"tool": name, "account": user.email_masked, **({"email": email} if email else {})},
            )
            return {"tip": PERMISSION_TIP, "welcome": WELCOME, **result}
        return result
    return after


def session_for(user_token: str) -> GarminSession:
    existing = _SESSIONS.get(user_token)
    if existing is not None:
        return existing
    created = GarminSession(
        tokenstore=lambda: store.load_blob(user_token),
        on_refresh=lambda blob: store.update_blob(user_token, blob),
        reconnect_hint=RECONNECT_HINT,
        on_lapsed=lambda: _lapsed_in_use(user_token),
    )
    _SESSIONS[user_token] = created
    return created


# A connection is meant to be made once. Garmin's own sign-in lapses if it goes
# unused for long enough, and when hosted there is no password to fall back
# on, so a long break would quietly cost someone their connection. Anyone idle
# for a week has their Garmin session renewed on a timer instead: one cheap
# read, nothing kept from it, the renewed token saved.
KEEP_ALIVE_AFTER = 7 * 86400
KEEP_ALIVE_EVERY = 6 * 3600
# Between people, so a backlog after downtime is a trickle to Garmin, not a burst.
KEEP_ALIVE_SPACING = 5.0


def keep_alive_once(spacing: float = KEEP_ALIVE_SPACING) -> dict[str, int]:
    """Renew the Garmin session of everyone idle for a week. Returns counts."""
    counts = {"renewed": 0, "lapsed": 0}
    for user_token in store.idle_users(KEEP_ALIVE_AFTER):
        user = store.get_user(user_token)
        if user is None:
            continue
        # Its own session, not the cached one: saving here must not count the
        # person as active, and a cached client would hold the token this
        # replaces. The cached one is dropped so the next real use reloads.
        session = GarminSession(
            tokenstore=lambda t=user_token: store.load_blob(t),
            on_refresh=lambda blob, t=user_token: store.update_blob(t, blob, in_use=False),
        )
        try:
            session.run(lambda c: c.get_user_profile())
            counts["renewed"] += 1
        except Exception as exc:  # noqa: BLE001
            counts["lapsed"] += 1
            log.info("Keep-alive could not renew a session (%s)", type(exc).__name__)
            report_lapsed(user_token, source="keep_alive")
        finally:
            store.mark_kept_alive(user_token)
            _SESSIONS.pop(user_token, None)
        if spacing:
            time.sleep(spacing)
    return counts


# ---------------------------------------------------------------- Monday email
# Off until WEEKLY_EMAIL=on: the sign-up page and privacy notice describe the
# emails people get, and change on the day this starts. Monday mornings,
# London time, once per person per week whatever restarts happen.
WEEKLY_EMAIL = os.environ.get("WEEKLY_EMAIL", "").strip().lower() == "on"
WEEKLY_HOURS = (7, 12)
WEEKLY_EVERY = 15 * 60
# Between people, so the reads behind the emails are a trickle to Garmin.
WEEKLY_SPACING = 5.0


def _plan_answer(user_token: str) -> Any:
    """The plan card's answer for one person, read the way the card reads it.

    Its own Garmin session, as keep-alive does: reading for an email must not
    count the person as active.
    """
    session = GarminSession(
        tokenstore=lambda t=user_token: store.load_blob(t),
        on_refresh=lambda blob, t=user_token: store.update_blob(t, blob, in_use=False),
    )
    token = use_session(session)
    official_token = official.use(user_token)
    alone_token = server_mod.use_alone(not store.has_connect(user_token))
    try:
        return anyio.run(server_mod.get_plan)
    finally:
        server_mod.reset_alone(alone_token)
        official.reset(official_token)
        reset_session(token)


def weekly_email_once(today: date | None = None, send: Callable[..., None] | None = None,
                      spacing: float = WEEKLY_SPACING) -> dict[str, int]:
    """Send this week's email to everyone it hasn't gone to. Returns counts."""
    today = today or datetime.now(ZoneInfo("Europe/London")).date()
    monday = (today - timedelta(days=today.weekday())).isoformat()
    send = send or support_mail.send_weekly
    base = os.environ.get("GARMIN_MCP_BASE_URL", "").strip().rstrip("/")
    counts = {"sent": 0, "nothing_to_say": 0, "failed": 0}
    for user_token, email, fingerprint in store.weekly_recipients():
        if not store.claim_weekly(fingerprint, monday):
            continue
        try:
            said = weekly.summary(_plan_answer(user_token), today)
        except Exception as exc:  # noqa: BLE001 - one person's Garmin never stops the rest
            log.info("weekly email: no plan for one person (%s)", type(exc).__name__)
            said = None
        if not said:
            counts["nothing_to_say"] += 1
            continue
        link = unsubscribe_url(base, fingerprint)
        subject, text, page_html = weekly.compose(said, link)
        try:
            send(email, subject, text, page_html, link)
            counts["sent"] += 1
            analytics.capture("weekly_email_sent", fingerprint, {
                "key_sessions": len(said.get("key_sessions") or []), "no_plan": bool(said.get("no_plan")),
                "$process_person_profile": False})
        except OSError:
            counts["failed"] += 1
        time.sleep(spacing)
    return counts


def _weekly_email_forever() -> None:
    while True:
        now = datetime.now(ZoneInfo("Europe/London"))
        if now.weekday() == 0 and WEEKLY_HOURS[0] <= now.hour < WEEKLY_HOURS[1]:
            try:
                counts = weekly_email_once(now.date())
                if any(counts.values()):
                    log.info("Weekly email: %(sent)d sent, %(nothing_to_say)d with nothing to say, %(failed)d failed", counts)
            except Exception:  # noqa: BLE001
                log.exception("Weekly email pass failed")
        time.sleep(WEEKLY_EVERY)


# Garmin's starter access to its official API allows this many users. Past it
# Daash needs Garmin's paid access, so the count is reported with every
# keep-alive pass, for a chart against the limit and an alert well before it.
GARMIN_USER_LIMIT = 1000


def report_official_users() -> dict[str, int] | None:
    if not official.configured():
        return None
    counts = store.official_counts()
    analytics.capture("official_users", "daash-server", {
        **counts, "limit": GARMIN_USER_LIMIT, "share_of_limit": round(counts["linked"] / GARMIN_USER_LIMIT, 3),
        "$process_person_profile": False})
    return counts


def _keep_alive_forever() -> None:
    while True:
        with contextlib.suppress(Exception):
            store.sweep_expired_oauth()
        # Crew runs on days that have passed, gone even if nobody looks.
        with contextlib.suppress(Exception):
            crews.purge_past()
        with contextlib.suppress(Exception):
            report_official_users()
        try:
            counts = keep_alive_once()
            if counts["renewed"] or counts["lapsed"]:
                log.info("Keep-alive: %(renewed)d renewed, %(lapsed)d lapsed", counts)
        except Exception:  # noqa: BLE001
            log.exception("Keep-alive pass failed")
        time.sleep(KEEP_ALIVE_EVERY)


# The hourly test sign-in, run by the server on its own clock. It ran from
# GitHub's scheduler at first, which on the night of Oct 13 started it three
# times in nine hours, so the alert for checks going quiet woke Bart up while
# sign-in was fine. It signs in as an account that doesn't exist, through the
# proxy like anyone connecting, and passes when Garmin answers that the email
# and password are wrong: the last step of a sign-in, so everything before it
# works. Reported as sign_in_check, which the connection alert reads. Only
# where reports go somewhere: a copy run for one person has no one to tell.
SIGNIN_CHECK_EVERY = 3600
# After a restart, long enough for a deploy's own checks to finish first.
SIGNIN_CHECK_FIRST = 600
# A failure is checked again this soon, once. Garmin's throttling of a check
# has cleared within minutes (Oct 13: failed 11:43, passed 11:51), so a second
# failure means sign-in is really down, and the alert emails on two in a row.
SIGNIN_CHECK_RECHECK = 900
_ADDRESS_REASONS = ("rate_limited", "blocked_by_garmin", "proxy_failed", "other")


def signin_check_once(email: str | None = None) -> dict[str, Any]:
    """One test sign-in, from up to three addresses. Reports and returns it."""
    email = email or f"daash-check-{secrets.token_hex(4)}@example.com"
    began = time.monotonic()
    reasons: list[str] = []
    traces: list[dict[str, Any]] = []
    for _ in range(3):
        with login_egress(login_proxy()) as trace:
            traces.append(trace)
            try:
                client = build_client(
                    prompt_mfa=lambda: (_ for _ in ()).throw(RuntimeError("no code")),
                    email=email,
                    password=secrets.token_urlsafe(12),
                )
                client.return_on_mfa = True
                client.login()
                reason = "signed in"
            except Exception as exc:  # noqa: BLE001 - every failure is a result here
                reason = _failure_reason(exc)
        reasons.append(reason)
        # Only an answer about the address is worth another address.
        if reason not in _ADDRESS_REASONS:
            break
    result = {
        "ok": "bad_credentials" in reasons,
        "kind": "unknown_account",
        "reasons": reasons,
        "tries": len(reasons),
        "seconds": round(time.monotonic() - began, 1),
        "code": "live",
        "source": "server",
        **_traced(traces),
    }
    analytics.capture("sign_in_check", None, result)
    if not result["ok"]:
        log.warning("Test sign-in failed: %s", ", ".join(reasons))
    return result


def _traced(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """A sign-in's attempts as event properties: per address, each method's
    outcome and seconds ("widget:429:4.1 portal:refused:0.0"), and how long
    the library waited on purpose. Method names and timings, nothing typed."""
    if not traces:
        return {}
    return {
        "steps": [" ".join(t["steps"]) for t in traces],
        "waited_s": [round(t["waited"], 1) for t in traces],
    }


def _signin_check_wait(ok: bool, rechecking: bool) -> int:
    """Seconds to the next test sign-in: soon after a failure, but only once,
    so a long outage means two checks an hour against Garmin, not four."""
    return SIGNIN_CHECK_RECHECK if not ok and not rechecking else SIGNIN_CHECK_EVERY


def _signin_check_forever() -> None:
    time.sleep(SIGNIN_CHECK_FIRST)
    rechecking = False
    while True:
        ok = True  # a check that couldn't run has nothing to confirm
        try:
            ok = signin_check_once()["ok"]
        except Exception:  # noqa: BLE001
            log.exception("Test sign-in could not run")
        wait = _signin_check_wait(ok, rechecking)
        rechecking = wait == SIGNIN_CHECK_RECHECK
        time.sleep(wait)


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

# The same look as daash.run: its colours, its type, its wordmark, light only.
# This is the page that asks for a Garmin password, so it has to look like the
# site that sent people here. No web fonts and no outside requests: a sign-in
# page loads nothing it does not need.
# The wordmark's five letters in the site's own Inter Tight at 900, inline:
# the page loads nothing from anywhere else, and without them most computers
# drew "Daash" in their system font, heavier and wider than on daash.run.
# Italic is the browser's slant, as on the site, which loads it upright.
_WORDMARK_FONT = (
    "data:font/woff2;base64,"
    "d09GMgABAAAAAAN0AA8AAAAABewAAAMZAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAGhYbgQocKgZgP1NUQVREAEwRCAqDTIMiATYCJAMUCwwABCAFgR4HIBuvBFFUT+ZB/DyMHRwBUuyVQTNdWb3MCQXPU135fnX1LKlA4g3CxDrpmE0D/PcWTOkRiEpVnf+bcW3RiRkEvAHnxElFY2EvwowCiT5wLz/TIz0APOdmQecOaTqjgt5+IwjgCAAoBEEjCGBPpHSqsrpBC5wHdB0xCGc0wP8IIP17DhuELSCAoFHYItiiiKWBdMUMNPC2hGgVBoIr2yQxDiOMFkv4egBAmxgzMMHM0LeBdWmOmrQXqM0AkOA81qx5w+aco5LhNudq3cHWqhYBAqA7mQ+AUI7I9UDAkwIiqdG9+4wQV/hl87j4I2hcCYD4GTMAA0FJAAACgAIMwLPpKZgmF9hji0JRSVf6MDw8+tG9ra1vKQEAAAT5HxdAMAGwByzAUuApAKABAIVCEG8d7eYT5+JoOM23WmXqkyf24/LdM6Y8fTturOP4rvPUQqZa0be2Wu+Kp7ef9yjUUSoc5j/GEe0o9ucePWq9vpAVJ7CPPHHrAFMe99fROGkHZ4d2NKN9NN5xW9oz5XRVy5I/RdzaL3gfbOW09bFzssrUVq220/nf+m7WmjZzBm+oanpo/YZmh7ZWj5zWZs3nhsdONvz29UTD+kd9H8O0AhMz3frvqG6/eHCHoqmDm61IbGV2tAzxiA7VCWXJmfk1acOHWHoO2/+69+7Rp3Nmdq+e0bB2zCbb4E4NRkfk/ktuEpVendHhb8Hc3cM/bzkTlFKaypvhI5ZhmlbPMarf4TETRh3q3+XU+GvLRjRv1qlhWp34mEZUbvs1JL1NfkFmTWSE3/7vH2IG7Vk+Ycz8Q786rSf0XhfNaJtbkVsTFV4w2fvygvfumDFywtbPLbb88r4GAGAMAhhiaeE5sLizS+FfW8MWgEf9XMoBvqgdY/Hvp9nPfABYUAAIPMncPGAgBZ4goDc19OpfUkevwn+KRnoGgQQwyaEAEHBnoREGou0QvEFZhTPePHHFkUjOD3tSOX8cyX83AHuqqcsgRtCTYYRqQV9604cRNKMnvRnJALoyjNb0LEblct+d/tQVoK5QX6oZgxnYDcIAYrWHWQAAAAA="
)

_STYLE = """
@font-face { font-family:"Daash Wordmark"; font-weight:900; font-style:normal;
  src:url(""" + _WORDMARK_FONT + """) format("woff2"); }
:root { color-scheme: light; --ground:#F8F9FA; --surface:#fff; --ink:#1C212B;
  --muted:#6A7180; --line:#E2E4E9; --accent:#E63023; --accent-ink:#B8261C; }
* { box-sizing:border-box }
body { margin:0; background:var(--ground); color:var(--ink); font:16px/1.6
  Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing:antialiased; }
.top { border-bottom:1px solid var(--line); background:var(--surface) }
.top div { max-width:480px; margin:0 auto; padding:14px 20px }
.logo { font:italic 900 22px/1 "Daash Wordmark", "Inter Tight", Inter, ui-sans-serif, system-ui,
  -apple-system, sans-serif; color:var(--accent); text-decoration:none;
  letter-spacing:-0.02em }
.wrap { max-width:480px; margin:0 auto; padding:36px 20px 64px; }
.card { background:var(--surface); border:1px solid var(--line); border-radius:16px;
  padding:28px 24px; box-shadow:0 1px 2px rgba(16,24,40,0.04) }
h1 { font:600 1.75rem/1.15 "Inter Tight", Inter, ui-sans-serif, system-ui,
  -apple-system, sans-serif; letter-spacing:-0.025em; margin:0 0 10px }
h2 { font-size:1.15rem; margin:28px 0 8px }
p { margin:0 0 14px; color:var(--muted) }
p strong { color:var(--ink) }
.secure { display:flex; gap:10px; align-items:flex-start; background:var(--ground);
  border:1px solid var(--line); border-radius:10px; padding:10px 12px;
  font-size:0.92rem; color:var(--ink); margin:14px 0 6px }
.secure svg { flex:none; margin-top:3px; color:#16A34A }
label { display:block; font-weight:600; font-size:0.92rem; color:var(--ink); margin:18px 0 6px }
input { width:100%; padding:12px 14px; font-size:1rem; border:1px solid var(--line);
  border-radius:10px; background:var(--surface); color:var(--ink) }
input:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px rgba(230,48,35,0.15) }
button { margin-top:22px; width:100%; padding:13px; font-size:1rem; font-weight:600;
  border:0; border-radius:999px; background:var(--accent); color:#fff; cursor:pointer }
button:hover { background:var(--accent-ink) }
button.busy, button.busy:hover { background:var(--accent-ink); cursor:progress }
.spin { display:inline-block; width:1em; height:1em; margin-right:9px; vertical-align:-2px;
  border:2px solid rgba(255,255,255,0.45); border-top-color:#fff; border-radius:50%;
  animation:spin 0.8s linear infinite }
@keyframes spin { to { transform:rotate(360deg) } }
@media (prefers-reduced-motion: reduce) { .spin { animation-duration:2.4s } }
.wait { margin:12px 0 0; font-size:0.9rem; text-align:center }
.fine { margin:10px 0 0; font-size:0.8rem; text-align:center }
a.button { display:block; margin-top:22px; padding:13px; font-size:1rem; font-weight:600; text-align:center;
  border-radius:999px; background:var(--accent); color:#fff; text-decoration:none }
a.button:hover { background:var(--accent-ink) }
.steps { margin:14px 0 0; padding-left:22px; line-height:1.55 } .steps li { margin:6px 0 }
.instead { margin-top:18px; padding:16px; border:1px solid var(--line, #E6E8EC); border-radius:14px; font-size:.95rem; line-height:1.5 }
.instead a.button { margin-top:14px }
.code { letter-spacing:0.3em; font:600 1.4rem ui-monospace, SFMono-Regular, Menlo, monospace; text-align:center }
button.secondary { background:var(--surface); color:var(--ink);
  border:1px solid var(--line); margin-top:10px }
button.secondary:hover { background:var(--ground) }
.err { border:1px solid #F5C2BE; border-left:3px solid var(--accent); background:#FEF3F2;
  padding:12px 14px; border-radius:10px; color:var(--ink); margin:16px 0 }
code, .url { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:0.85rem;
  background:var(--ground); border:1px solid var(--line); border-radius:10px;
  padding:12px; display:block; word-break:break-all; color:var(--ink) }
.note { font-size:0.9rem }
.hint { margin:6px 0 0 }
.foot { text-align:center; margin-top:18px; font-size:0.85rem }
a { color:var(--ink); text-decoration:underline; text-decoration-color:var(--line);
  text-underline-offset:3px }
a:hover { text-decoration-color:var(--ink) }
"""

_LOCK = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>'
)
_TICK = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="20 6 9 17 4 12"/></svg>'
)


# A slip in the domain still looks like an email address, so the browser's
# own check passes it: Tom's first try on Oct 13 was "...@gmail.con", and
# Garmin can only say "wrong email or password". Near a common provider, or
# with a mistyped ".com", the form offers the likely address. It never blocks
# sending: a real address can look unusual. A link, not a button: the busy
# script below takes the form's first button to be the one that submits.
_EMAIL_HINT_SCRIPT = """<script>
(function () {
  var input = document.getElementById("email"), hint = document.getElementById("email-hint");
  if (!input || !hint) return;
  // Suggested when an address is a letter or two away from one of these. Only
  // longer names: short ones (live.com, me.com) sit too close to real domains.
  var known = ["gmail.com", "googlemail.com", "hotmail.com", "hotmail.co.uk", "outlook.com",
    "icloud.com", "yahoo.com", "yahoo.co.uk", "live.co.uk", "btinternet.com",
    "protonmail.com"];
  // Real providers a letter away from those, never "corrected".
  var fine = ["email.com", "mail.com", "ymail.com", "gmx.com", "gmx.co.uk", "mac.com", "me.com",
    "live.com", "aol.com", "msn.com", "sky.com", "proton.me", "hey.com", "fastmail.com",
    "rocketmail.com", "yahoo.co.in", "hotmail.co.za", "outlook.ie"];
  var endings = {con: "com", cmo: "com", cim: "com", vom: "com", xom: "com", comm: "com",
    coom: "com", ocm: "com"};
  function distance(a, b) {
    var prev = [], cur, i, j;
    for (j = 0; j <= b.length; j++) prev.push(j);
    for (i = 1; i <= a.length; i++) {
      cur = [i];
      for (j = 1; j <= b.length; j++) {
        cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)));
      }
      prev = cur;
    }
    return prev[b.length];
  }
  function likely(domain) {
    if (known.indexOf(domain) >= 0 || fine.indexOf(domain) >= 0) return null;
    var best = null, closest = 3;
    known.forEach(function (k) {
      var d = distance(domain, k);
      if (d > 0 && d <= (k.length > 8 ? 2 : 1) && d < closest) { best = k; closest = d; }
    });
    if (best) return best;
    var parts = domain.split("."), last = parts[parts.length - 1];
    if (parts.length > 1 && endings[last]) {
      parts[parts.length - 1] = endings[last];
      return parts.join(".");
    }
    return null;
  }
  function check() {
    var value = input.value.trim(), at = value.lastIndexOf("@");
    var fix = at > 0 ? likely(value.slice(at + 1).toLowerCase()) : null;
    if (!fix) { hint.hidden = true; return; }
    var address = value.slice(0, at + 1) + fix;
    hint.textContent = "Did you mean ";
    var a = document.createElement("a");
    a.href = "#";
    a.textContent = address;
    a.addEventListener("click", function (e) {
      e.preventDefault();
      input.value = address;
      hint.hidden = true;
      input.focus();
    });
    hint.appendChild(a);
    hint.appendChild(document.createTextNode("?"));
    hint.hidden = false;
  }
  input.addEventListener("blur", check);
  input.addEventListener("input", function () { if (!hint.hidden) check(); });
})();
</script>"""


# A sign-in takes Garmin ten seconds or more, and a button that looks idle for
# that long gets pressed again, each press another login Garmin counts against
# its rate limit. A form marked data-wait says it's working after the first
# press and ignores the rest; coming back with the back button resets it.
_BUSY_SCRIPT = """<script>
document.querySelectorAll("form[data-wait]").forEach(function (f) {
  var b = f.querySelector("button"), label = b ? b.innerHTML : "";
  f.addEventListener("submit", function (e) {
    if (f.dataset.sent) { e.preventDefault(); return; }
    f.dataset.sent = "1";
    if (b) {
      b.classList.add("busy");
      b.setAttribute("aria-disabled", "true");
      b.innerHTML = '<span class=spin aria-hidden=true></span>' + f.dataset.wait;
    }
    var n = f.querySelector(".wait");
    if (n) {
      n.hidden = false;
      // A long wait reads as a hang; say it isn't one before anyone reloads.
      if (n.dataset.later) setTimeout(function () {
        if (f.dataset.sent) n.textContent = n.dataset.later;
      }, 20000);
    }
  });
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    delete f.dataset.sent;
    if (b) { b.classList.remove("busy"); b.removeAttribute("aria-disabled"); b.innerHTML = label; }
    var n = f.querySelector(".wait");
    if (n) n.hidden = true;
  });
});
</script>"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f'<meta name=viewport content="width=device-width,initial-scale=1">'
        f'<meta name=theme-color content="#ffffff">'
        f"<title>{title} · Daash</title><style>{_STYLE}</style></head>"
        f'<body><header class=top><div><a class=logo href="https://daash.run/">Daash</a></div></header>'
        f"<main class=wrap><div class=card>{body}</div></main>{_BUSY_SCRIPT}</body></html>",
        status_code=status,
    )


@mcp.custom_route("/", methods=["GET"])
async def index(request: Request) -> Response:
    if OAUTH_ENABLED:
        # There is no link to collect here. The connector is added in Claude,
        # which brings the person back to sign in with the request attached.
        return page(
            "Garmin for Claude",
            f"""
            <h1>Connect Garmin to Claude</h1>
            <p>Add this address in Claude &rarr; Settings &rarr; Connectors &rarr;
            Add custom connector. Claude will bring you back here to sign in to
            Garmin, once.</p>
            <div class=url>{base_url(request)}/mcp</div>
            <p class=note style="margin-top:16px">Your Garmin password goes
            straight to Garmin, is never written to disk, and is dropped from
            memory as soon as it has been exchanged. Only the access token Garmin
            issues is kept, encrypted.</p>
            <p class=note style="margin-top:20px"><a href="{PRIVACY_URL}"
            target=_blank rel=noopener>Privacy</a></p>
            """,
        )
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


def _connect_url(flow: str) -> str:
    """The sign-in form, still attached to Claude's pending request if there was one.

    A "Try again" that dropped the flow sent people back to the form as if
    they had arrived on their own, and a sign-in without a flow ends in the
    wrong place. Every retry link goes through here.
    """
    return f"/connect?flow={quote(flow, safe='')}" if flow else "/connect"


def _try_again(flow: str) -> str:
    return f'<p><a href="{_connect_url(flow)}">Try again</a></p>'


def _fallback_ok(flow: str) -> bool:
    """Whether a refused password sign-in can be offered Garmin's own page instead."""
    return bool(flow) and OAUTH_ENABLED and official.configured() and OAUTH_PROVIDER.pending(flow) is not None


def _garmin_instead(flow: str) -> str:
    """When Garmin turns password sign-ins away, the official route still works.

    Garmin refuses password sign-ins by the address they come from (mid
    October, 4 of 8 people refused never came back). Approving Daash on
    Garmin's own page isn't refused that way, so it is offered on the spot,
    for the same request. Someone who already has an account is matched to it
    by their email on the way back.
    """
    if not _fallback_ok(flow):
        return ""
    return (
        '<div class=instead><strong>Or connect on Garmin&rsquo;s own page, with no wait.</strong> '
        "You approve Daash on Garmin&rsquo;s site instead of typing your password here. "
        "If you already use Daash, enter the same email afterwards and you keep everything."
        f'<a class=button href="/official/signup?flow={quote(flow, safe="")}&amp;via=blocked">Continue with Garmin</a></div>'
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
    route, drawn = _signup_route(request)
    if flow and OAUTH_ENABLED and OAUTH_PROVIDER.password_only(flow):
        route, drawn = "password", False  # the password link: whatever the split or cookie says
    listed = bool(flow) and OAUTH_ENABLED and OAUTH_PROVIDER.listed(flow) and official.configured()
    official_route = bool(flow) and OAUTH_ENABLED and (listed or route == "official")
    # Counted, not attributed: there is no one to attribute it to until they
    # sign in. Shown against submitted and connected says who leaves here.
    analytics.capture("sign_in_shown", None, _with_source(request, {
        "via": "oauth" if flow else "direct", "route": "official" if official_route else "password",
        "listed": listed, **({"password_link": True} if flow and OAUTH_ENABLED and OAUTH_PROVIDER.password_only(flow) else
                             {"split": SPLIT_NAME, "split_arm": route} if SPLIT and flow and not listed else {})}))
    if official_route:
        response = _garmin_intro(flow, usual=not listed)
    else:
        response = _password_form(flow)
    asked = request.query_params.get("route", "")
    if asked in ROUTES and not flow:
        # Opened by hand, to put this browser on a route before connecting.
        response = page("Sign-in route set", f"""<h1>Sign-in route set</h1><p>Next time you connect Daash
            from Claude or ChatGPT in this browser, you&rsquo;ll {"approve Daash on Garmin&rsquo;s own page"
            if asked == "official" else "sign in with your Garmin email and password"}.</p>
            <p class=note><a href="/connect?route={"password" if asked == "official" else "official"}">Switch
            back</a></p>""")
    if asked in ROUTES or (drawn and flow and not listed):
        response.set_cookie(ROUTE_COOKIE, asked if asked in ROUTES else route, max_age=30 * 86400,
                            secure=True, httponly=True, samesite="lax")
    return response


def _signup_route(request: Request) -> tuple[str, bool]:
    """"official" or "password": which sign-in this browser gets from the connector,
    and whether it was drawn just now for the split (so the cookie keeps it)."""
    if not official.configured() or request.query_params.get("usual") == "1":
        return "password", False
    asked = request.query_params.get("route", "")
    chosen = asked if asked in ROUTES else request.cookies.get(ROUTE_COOKIE, "")
    if chosen in ROUTES:
        return chosen, False
    if SPLIT:
        return secrets.choice(ROUTES), True
    return ("official" if SIGNUP_ROUTE == "official" else "password"), False


def _usual_way(flow: str) -> str:
    return f'<p class=fine>Already use Daash? <a href="{_connect_url(flow)}&amp;usual=1">Sign in the usual way</a></p>'


def _garmin_intro(flow: str, usual: bool = True) -> Response:
    """Before Garmin's page: what's about to happen, and Garmin's note about unreviewed apps, played down."""
    return page(
        "Connect your Garmin",
        f"""
        <h1>Connect your Garmin</h1>
        <p>Daash reads your training from Garmin and puts workouts on your watch.
        Garmin asks you to approve that on its own page.</p>
        <p class=note>Garmin shares what your watch syncs from now on, so last night&rsquo;s
        sleep and earlier runs won&rsquo;t show. Each sync after this adds to it.</p>
        <p class=secure>{_LOCK}<span>You sign in on Garmin&rsquo;s site. Daash never sees your password.</span></p>
        <p class=note>Garmin&rsquo;s page shows a grey note saying it hasn&rsquo;t reviewed
        Daash yet. That&rsquo;s normal for new apps.</p>
        <a class=button href="/official/signup?flow={quote(flow, safe='')}">Continue with Garmin</a>
        {_usual_way(flow) if usual else ""}
        <p class=note style="margin-top:20px;text-align:center"><a href="{PRIVACY_URL}"
        target=_blank rel=noopener>What&rsquo;s stored, and what isn&rsquo;t</a></p>
        """,
    )


def _password_form(flow: str) -> Response:
    return page(
        "Sign in to Garmin",
        f"""
        <h1>Sign in to Garmin</h1>
        <p class=secure>{_LOCK}<span>These go straight to Garmin. Your password is never stored.</span></p>
        <form method=post action=/connect data-wait="Connecting to Garmin&hellip;">
          <input type=hidden name=flow value="{flow}">
          {_INVITE_FIELD if INVITE_CODE else ""}
          <label for=email>Garmin email</label>
          <input id=email name=email type=email required autocomplete=username>
          <p class="note hint" id=email-hint role=status hidden></p>
          <label for=password>Garmin password</label>
          <input id=password name=password type=password required
                 autocomplete=current-password>
          <button>Connect</button>
          <p class=fine>Occasional Daash emails, easy to stop.</p>
          <p class="note wait" role=status hidden
             data-later="Still working. Garmin can be slow to answer, and Daash keeps
             trying for you. Please stay on this page.">Signing you in to Garmin.
          This can take a minute or more, so please don&rsquo;t press again or leave
          this page.</p>
        </form>
        <p class=note style="margin-top:20px"><a href="{PRIVACY_URL}"
        target=_blank rel=noopener>What&rsquo;s stored, and what isn&rsquo;t</a>
        &middot; <a href="{GUIDE_URL}" target=_blank rel=noopener>New to Daash?
        See what you can ask it</a></p>
        {_EMAIL_HINT_SCRIPT}
        """,
    )


def _http_status(exc: BaseException) -> int | None:
    """The HTTP status behind a failure, if the library kept one anywhere."""
    for e in (exc, exc.__cause__, exc.__context__):
        status = getattr(getattr(e, "response", None), "status_code", None)
        if isinstance(status, int):
            return status
    return None


def _error_detail(exc: BaseException) -> dict[str, Any]:
    """What went wrong, for the report: exception types and an HTTP status.

    Enough to tell a real password rejection from something misread, without
    any of the message text, which can carry what Garmin sent back.
    """
    detail: dict[str, Any] = {"error_type": type(exc).__name__}
    status = _http_status(exc)
    if status:
        detail["status"] = status
    # garminconnect sums up its whole chain as "...exhausted: <Type>: ...".
    last = re.search(r"exhausted: (\w+)", str(exc))
    if last:
        detail["last_error_type"] = last.group(1)
    return detail


def _failure_reason(exc: BaseException) -> str:
    """Which of the things that go wrong at sign-in this was.

    Told apart by the library's exception type first, then by status code and
    message. The two network failures need opposite responses: a 429 is a
    rate limit that clears on its own, so the person should simply wait; a 403
    is Cloudflare refusing the server's address outright, and no amount of
    waiting helps. The old single word for both hid which one was happening.

    The analytics event carries this word, so the count of people Garmin
    turned away can be told from the count who mistyped a password.
    """
    name = type(exc).__name__
    text = str(exc).lower()
    status = _http_status(exc)
    # The proxy itself failing (down, or refusing its own password) is
    # neither Garmin nor the person, and is checked first: its message can
    # carry a 403 or 407 of the proxy's own.
    if "proxy" in text or "connect tunnel failed" in text:
        return "proxy_failed"
    if name == "GarminConnectTooManyRequestsError" or status == 429 or "429" in text:
        return "rate_limited"
    if name == "GarminConnectAuthenticationError" or status == 401 or "401" in text:
        return "bad_credentials"
    if status == 403 or "403" in text or "cloudflare" in text or "forbidden" in text:
        return "blocked_by_garmin"
    if "unauthorized" in text or "invalid" in text or "password" in text:
        return "bad_credentials"
    return "other"


def _log_signin_failure(exc: BaseException, reason: str) -> None:
    """One line per failed sign-in: what kind, and the status if there was one.

    Type and status only. The message can quote the request, and the request
    is the one place a password could appear.
    """
    log.warning(
        "sign-in failed: reason=%s type=%s status=%s",
        reason, type(exc).__name__, _http_status(exc),
    )


def _signin_error(
    exc: BaseException, flow: str = "", *, paused: bool = True, tries: int = 1,
    account_paused: bool = False, email: str = "",
) -> str:
    """Explain a failed sign-in in terms that make sense on a web page.

    `paused` says whether this failure paused sign-ins for everyone, which
    only happens when the refused attempt went out from the server's own
    address; the page must not claim a pause that isn't there. `tries` is how
    many addresses were tried, so the page claims no retry that didn't happen.
    """
    reason = _failure_reason(exc)
    _log_signin_failure(exc, reason)
    # The address as typed, where a password was the problem: a slip such as
    # ".con" for ".com" (Oct 13) is then plain to see.
    typed = (
        f"<p class=note>Email used: <strong>{html.escape(email.strip())}</strong></p>"
        if email.strip() else ""
    )
    if account_paused:
        # Refused right after wrong passwords from the same person: Garmin
        # pausing their account, not anything about where Daash connects from.
        # Oct 25: someone met this a minute after a wrong password and didn't
        # come back. Whose lock it is, how long, and three steps, in order.
        return (
            "<div class=err><strong>Garmin has locked sign-ins to your account for "
            "about 30 minutes.</strong> It&rsquo;s Garmin&rsquo;s own safety lock after a "
            "wrong password, not Daash, and trying again now restarts the wait.</div>"
            + typed
            + "<ol class=steps>"
            "<li>Check your password by signing in at "
            '<a href="https://connect.garmin.com/" target=_blank rel=noopener>connect.garmin.com</a>. '
            "Forgotten it? Reset it there with &ldquo;Forgot password&rdquo;.</li>"
            "<li>Wait about 30 minutes from your last try.</li>"
            "<li>Come back and sign in here with the password that worked.</li>"
            "</ol>"
            + _try_again(flow)
        )
    others = tries - 1
    tried = (
        f", and Daash tried again from {'another address' if others == 1 else f'{others} other addresses'}"
        if others > 0 else ""
    )
    if reason == "rate_limited":
        if not paused:
            return (
                "<div class=err><strong>Garmin is asking for a pause, sorry.</strong> "
                f"It is not your password. Garmin limits how many sign-ins it takes{tried}. "
                "Please try again in fifteen minutes.</div>"
                + _garmin_instead(flow)
                + _try_again(flow)
            )
        return (
            "<div class=err><strong>Garmin is asking this server to slow down.</strong> "
            "It is not your password. Garmin limits how many sign-ins it takes "
            "from one address, and several people have signed in through here "
            f"recently. Sign-ins are paused for {GARMIN_PAUSE // 60} minutes so the "
            "limit can clear; try again after that.</div>"
            + _garmin_instead(flow)
            + _try_again(flow)
        )
    if reason == "blocked_by_garmin":
        if not paused:
            return (
                "<div class=err><strong>Garmin turned this sign-in away, sorry.</strong> "
                "It is not your password: Garmin refused the request before checking "
                f"it{tried}. Please try again in a few minutes, or later today.</div>"
                + _garmin_instead(flow)
                + _try_again(flow)
            )
        return (
            "<div class=err><strong>Garmin turned this sign-in away, sorry.</strong> "
            "It is not your password: Garmin refused the request before checking "
            "it, because of where Daash connects from, not anything you did. "
            f"Sign-ins are paused for {GARMIN_PAUSE // 60} minutes; please try "
            "again after that, or later today.</div>"
            + _garmin_instead(flow)
            + _try_again(flow)
        )
    if reason == "bad_credentials":
        return (
            "<div class=err><strong>Garmin didn't accept that email and password.</strong> "
            "Check them by signing in at connect.garmin.com, then come back and "
            "try again. Not sure of your Garmin password? Reset it there with "
            "&ldquo;Forgot password&rdquo;. Garmin pauses an account after a few "
            "wrong tries, so it is worth checking there before trying again here. "
            "If Garmin asks you for a code there, you have two-step verification "
            "on, and you'll be asked for the same code here.</div>"
            + typed
            + _try_again(flow)
        )
    return (
        f"<div class=err>Sign-in failed: {type(exc).__name__}. "
        "Try again in a moment.</div>" + _try_again(flow)
    )


def _consent_page(
    consent_token: str, parked: dict[str, Any], *, ask_email: bool = False, garmin: bool = False,
    email: str = "", email_error: str = "",
) -> Response:
    """Ask, by name, before handing a client access to this Garmin session.

    Everything variable here is escaped: the client name and the redirect come
    from an open registration endpoint, so both are attacker-controlled text.

    After an approval on Garmin's page, an account with no email yet is asked
    for one here, the last step: they have just said yes to Garmin, and the
    address is how they're recognised if they connect again.
    """
    params = parked["params"]
    destination = str(params.redirect_uri)
    name = parked.get("client_name") or parked["client_id"]
    known = oauth.recognised_redirect(destination)
    email_field = f"""
          <label for=email style="margin-top:4px">Your email</label>
          <input id=email name=email type=email required autocomplete=email value="{html.escape(email)}">
          <p class="note hint" id=email-hint role=status hidden></p>
          {f'<div class=err role=alert>{html.escape(email_error)}</div>' if email_error else ''}
          <p class="note hint" style="margin-bottom:16px">To sign back in, and the odd Daash update. Easy to stop.</p>
    """ if ask_email else ""
    form = f"""
        <form method=post action=/oauth/consent style="margin-top:8px">
          <input type=hidden name=consent value="{html.escape(consent_token)}">
          {email_field}
          <style>
            .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 18px; }}
            .chips .chip {{ display: inline-block; margin: 0; font-weight: 400; }}
            .chip input {{ position: absolute; opacity: 0; width: 0; height: 0; }}
            .chip span {{ display: inline-block; padding: 6px 12px; border: 1px solid #D9D7CF; border-radius: 999px;
              font-size: 13px; color: #4A4944; cursor: pointer; }}
            .chip input:checked + span {{ border-color: #E0312A; color: #C8261F; background: #FDF4F2; }}
            .chip input:focus-visible + span {{ outline: 2px solid #E0312A; outline-offset: 2px; }}
          </style>
          <p class=note style="margin:0 0 8px">How did you hear about Daash? <span style="opacity:.7">Optional</span></p>
          <div class=chips>{"".join(f'<label class=chip><input type=radio name=heard value="{v}"><span>{t}</span></label>' for v, t in HEARD_FROM)}</div>
          <button name=decision value=allow>Allow</button>
          <button name=decision value=deny class=secondary formnovalidate>Cancel</button>
        </form>
        <p class=note style="margin-top:20px">You stay signed in to Garmin
        either way. Cancelling refuses this app, it does not disconnect you.</p>
        {_EMAIL_HINT_SCRIPT if ask_email else ""}
    """

    # Claude or ChatGPT, known by the exact address the access goes back to.
    # The person started this from that app a minute ago, so the page says so
    # plainly instead of showing them a raw address to inspect.
    app = oauth.app_for_redirect(destination) if known else None
    if app in ("Claude", "ChatGPT"):
        host = urlparse(destination).hostname or ""
        opening = "Garmin approved." if garmin else f"You started this from {app}."
        body = f"""
        <h1>Connect Daash to {app}?</h1>
        <p class=secure>{_TICK if garmin else _LOCK}<span>{opening} {app} will be able
        to read your Garmin data and add or change workouts on your watch.</span></p>
        <p class=note>Access goes back to {html.escape(host)} only.</p>
        {form}
        """
        response = page(f"Connect Daash to {app}?", body)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    warning = (
        ""
        if known
        else "<div class=err><strong>This is not an app we recognise.</strong> "
        "If you did not just try to connect it yourself, press Cancel. "
        "Approving sends access to your Garmin data to the address below.</div>"
    )

    body = f"""
        <h1>Allow access?</h1>
        <p><strong>{html.escape(str(name))}</strong> is asking to read your Garmin
        data and write workouts to your watch.</p>
        {warning}
        <p class=note>It will be sent to:</p>
        <div class=url>{html.escape(destination)}</div>
        {form}
    """
    response = page("Allow access?", body)
    # A consent page that can be framed can be clicked through invisibly.
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    return response


@mcp.custom_route("/oauth/consent", methods=["POST"])
async def oauth_consent(request: Request) -> Response:
    """Act on the answer. The unguessable one-use token is the proof."""
    if not OAUTH_ENABLED:
        return page("Not found", "<div class=err>Unknown page.</div>", 404)
    form = await request.form()
    consent_token = str(form.get("consent", ""))
    allow = str(form.get("decision", "")) == "allow"
    heard = str(form.get("heard", ""))
    subject = OAUTH_PROVIDER.staged_subject(consent_token)
    via_garmin = consent_token in _GARMIN_CONSENTS

    if allow and subject and via_garmin:
        staged = OAUTH_PROVIDER.staged(consent_token)
        if staged is None:
            return _consent_expired()
        parked = staged[1]
        if store.email_for(subject) is None:
            email = str(form.get("email", "")).strip()
            if not _EMAIL_RE.fullmatch(email) or len(email) > 254:
                return _consent_page(consent_token, parked, ask_email=True, garmin=True, email=email,
                                     email_error="That doesn't look like an email address.")
            fingerprint = store.email_fingerprint(email)
            if [t for t in store.tokens_for(fingerprint) if t != subject]:
                # The address already has an account: prove it's theirs
                # before the approval joins it.
                return await _send_code(request, consent_token, email, heard)
            store.set_user_email(subject, email, mask_email(email) or "hidden")
            _official_connected(request, subject, parked, returning=False)
        else:
            _official_connected(request, subject, parked, returning=True)
        _GARMIN_CONSENTS.pop(consent_token, None)

    destination = (
        OAUTH_PROVIDER.approve(consent_token)
        if allow
        else OAUTH_PROVIDER.deny(consent_token)
    )
    if destination:
        # Answered with the Allow press, if at all: the one word, against the
        # same per-person id the connection got, so the two line up.
        if allow and heard in {v for v, _ in HEARD_FROM}:
            analytics.capture("heard_from", store.email_hash_for(subject) if subject else None,
                              _with_source(request, {"answer": heard}))
        return RedirectResponse(destination, status_code=303)
    return _consent_expired()


def _consent_expired() -> Response:
    return page(
        "Request expired",
        "<div class=err>That request has expired or was already answered. "
        "Start again from the connector in Claude.</div>",
        400,
    )


def _official_connected(request: Request, user_token: str, parked: dict[str, Any], *,
                        returning: bool, joined: bool = False) -> None:
    """connector_connected, for someone who came in through Garmin's page."""
    user = store.get_user(user_token)
    email = store.email_for(user_token)
    destination = str(parked["params"].redirect_uri)
    app = oauth.app_for_redirect(destination) if oauth.recognised_redirect(destination) else None
    analytics.capture("connector_connected", (user.email_hash if user else None) or None, _with_source(request, {
        "account": user.email_masked if user else None,
        **({"email": email} if email else {}),
        "via": "oauth",
        "route": "official",
        **({"app": app} if app else {}),
        "returning": returning,
        "joined": joined,
        "listed": bool(parked.get("listed")),
        "workouts": official.can_write(user_token),
    }))


def _code_page(sealed: str, email: str, *, error: str = "", note: str = "") -> Response:
    return page(
        "Check your email",
        f"""
        <h1>Check your email</h1>
        <p>You already use Daash with <strong>{html.escape(mask_email(email) or email)}</strong>.
        We&rsquo;ve sent a 6-digit code, so your plans, crew and history come with you.</p>
        <form method=post action=/oauth/confirm data-wait="Checking&hellip;">
          <input type=hidden name=s value="{html.escape(sealed)}">
          <label for=code>Code</label>
          <input id=code name=code class=code required inputmode=numeric autocomplete=one-time-code
                 pattern="[0-9 ]{{6,7}}" maxlength=7 autofocus>
          {f'<div class=err role=alert>{html.escape(error)}</div>' if error else ''}
          {f'<p class="note hint">{html.escape(note)}</p>' if note else ''}
          <button>Confirm</button>
          <p class=fine>Arrives in a few seconds. Check spam if not.</p>
          <button class=secondary name=action value=resend formnovalidate>Send a new code</button>
          <button class=secondary name=action value=change formnovalidate>Use a different email</button>
        </form>
        """,
    )


async def _send_code(request: Request, consent_token: str, email: str, heard: str) -> Response:
    """Email a code to an address that already has an account, and ask for it."""
    sealed = official._seal({"c": consent_token, "e": email, "h": heard})
    if _too_many_attempts(_client_address(request)):
        return _code_page(sealed, email, error="Too many tries from here. Wait a few minutes, then send a new code.")
    fingerprint = store.email_fingerprint(email)
    code = store.new_email_code(fingerprint)
    if code is None:
        if store.code_wait(fingerprint) == "day":
            return _code_page(sealed, email, error="That address has had its codes for today. Try again tomorrow, "
                              "or use a different email.")
        return _code_page(sealed, email, note="A code went out under a minute ago. Use that one, or send a new one in a minute.")
    try:
        await anyio.to_thread.run_sync(support_mail.send_code, email, code)
    except (OSError, KeyError):
        log.warning("sign-in code not sent")
        return _code_page(sealed, email, error="Daash couldn't send the code just now. Send a new one in a minute.")
    analytics.capture("email_code_sent", fingerprint, _with_source(request, {}))
    return _code_page(sealed, email)


@mcp.custom_route("/oauth/confirm", methods=["POST"])
async def oauth_confirm(request: Request) -> Response:
    """The code from the email: proof the address, and so its account, is theirs."""
    if not OAUTH_ENABLED:
        return page("Not found", "<div class=err>Unknown page.</div>", 404)
    form = await request.form()
    sealed = str(form.get("s", ""))
    opened = official._open(sealed, max_age=store.CODE_LIFETIME + 120)
    staged = OAUTH_PROVIDER.staged(opened["c"]) if opened and opened.get("c") else None
    if not opened or staged is None:
        return _consent_expired()
    consent_token, email, heard = opened["c"], opened["e"], opened.get("h", "")
    subject, parked = staged
    action = str(form.get("action", ""))
    if action == "change":
        return _consent_page(consent_token, parked, ask_email=True, garmin=True)
    if action == "resend":
        return await _send_code(request, consent_token, email, heard)
    fingerprint = store.email_fingerprint(email)
    checked = store.check_email_code(fingerprint, str(form.get("code", "")))
    if checked == "wrong":
        return _code_page(sealed, email, error="That code isn't right. Check the latest email and try again.")
    if checked != "ok":
        return _code_page(sealed, email, error="That code has expired or had too many tries. Send a new one.")
    others = [t for t in store.tokens_for(fingerprint) if t != subject]
    if others:
        target = others[-1]
        store.join_official_account(subject, target)
        OAUTH_PROVIDER.restage(consent_token, target)
        _SESSIONS.pop(subject, None)
    else:
        # The account went in the minutes between: this one keeps the address.
        target = subject
        store.set_user_email(subject, email, mask_email(email) or "hidden")
    _official_connected(request, target, parked, returning=bool(others), joined=bool(others))
    _GARMIN_CONSENTS.pop(consent_token, None)
    destination = OAUTH_PROVIDER.approve(consent_token)
    if destination is None:
        return _consent_expired()
    if heard in {v for v, _ in HEARD_FROM}:
        analytics.capture("heard_from", fingerprint, _with_source(request, {"answer": heard}))
    return RedirectResponse(destination, status_code=303)


def _finish(
    request: Request, client: Any, email: str, flow: str = "", *, mfa: bool = False,
    took: dict[str, Any] | None = None,
) -> Response:
    """Persist the session and show the person their private URL.

    `took` is how many attempts the sign-in needed and how long it ran, so
    the report shows retries rescuing sign-ins, or not.
    """
    blob = client.client.dumps()
    fingerprint = store.email_fingerprint(email)
    # Retires any URL this person was given before, so signing in again is also
    # how they revoke one they have lost.
    previous = store.tokens_for(fingerprint)
    for stale in previous:
        _SESSIONS.pop(stale, None)
    masked = mask_email(email) or "hidden"
    # Through OAuth, a person keeps one identity however many apps they connect.
    # Minting a new one here retired the old, so connecting ChatGPT, or Claude
    # on a second account, silently disconnected the first. The fresh Garmin
    # session replaces the stored one; every app's tokens keep pointing at it.
    # A lost URL is not a risk in this mode: there is no URL to lose.
    keep = previous[-1] if (OAUTH_ENABLED and flow and previous) else None
    user_token = store.save_user(
        masked, blob, user_token=keep, email_hash=fingerprint, email=email
    )
    if not previous:
        # Everything is new to someone just connecting; the note is for those
        # who were here before it.
        store.claim_news(user_token, NEWS_VERSION)
    # The address goes along, so the report says who connected and whoever
    # runs this can get in touch if their connection goes wrong.
    analytics.capture(
        "connector_connected",
        fingerprint,
        _with_source(request, {
            "account": masked,
            "email": email.strip(),
            # "direct" is someone who reached the form without Claude's request
            # attached; the page below sends them back to start from Claude.
            "via": ("oauth" if flow else "direct") if OAUTH_ENABLED else "url",
            # Claude, ChatGPT or another app, from where the sign-in goes back to.
            **({"app": app} if (app := (oauth.app_for_flow(flow) if OAUTH_ENABLED and flow else None)) else {}),
            "mfa": mfa,
            "returning": bool(previous),
            **(took or {}),
        }),
    )

    if OAUTH_ENABLED and flow:
        # Came from /authorize. The Garmin session exists now, but the client
        # asking for it still has to be approved by name, because anyone can
        # register one pointing anywhere. See GarminOAuthProvider.stage.
        staged = OAUTH_PROVIDER.stage(flow, user_token)
        if staged:
            consent_token, parked = staged
            return _consent_page(consent_token, parked)
        return page(
            "Sign-in expired",
            "<div class=err>That sign-in took too long and the request has "
            "expired. Start again from the connector in Claude.</div>",
            400,
        )

    if OAUTH_ENABLED:
        # Signed in, but not on Claude's behalf, so there is nothing to hand
        # back. The old page here showed a /u/<token>/ link, which this mode
        # does not serve: a dead end that looked like success.
        return page(
            "Signed in to Garmin",
            f"""
            <h1>Signed in to Garmin</h1>
            <p>One more step. In Claude, go to Settings &rarr; Connectors &rarr;
            Add custom connector and paste this address:</p>
            <div class=url>{base_url(request)}/mcp</div>
            <p class=note style="margin-top:16px">Claude will send you back here.
            Sign in once more when it does; that is what ties the connector to
            your account. Nothing from this visit needs saving.</p>
            <p class=note><a href="/disconnect?t={user_token}">Disconnect and delete
            my stored session</a></p>
            """,
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


def unsubscribe_url(base: str, email_hash: str) -> str:
    """The link every update email carries: one click to stop them."""
    return f"{base}/email/unsubscribe?u={email_hash}&s={store.unsubscribe_signature(email_hash)}"


@mcp.custom_route("/email/unsubscribe", methods=["GET", "POST"])
async def unsubscribe(request: Request) -> Response:
    """Stop update emails. GET asks, POST does: a mail scanner opening the link
    must not unsubscribe someone by visiting it."""
    if request.method == "GET":
        fingerprint = request.query_params.get("u", "")
        signature = request.query_params.get("s", "")
    else:
        # The page's own button sends u and s in the form; a mail app's
        # one-click unsubscribe (RFC 8058) posts to the link, so they're in
        # its address.
        form = await request.form()
        fingerprint = str(form.get("u", "") or request.query_params.get("u", ""))
        signature = str(form.get("s", "") or request.query_params.get("s", ""))
    genuine = bool(fingerprint) and hmac.compare_digest(signature, store.unsubscribe_signature(fingerprint))
    if not genuine:
        return page("Unsubscribe", "<div class=err>That unsubscribe link isn&rsquo;t valid. "
                    "Reply to any Daash email and you&rsquo;ll be taken off by hand.</div>", 400)
    if request.method == "GET":
        return page(
            "Unsubscribe",
            f"""
            <h1>Stop Daash emails?</h1>
            <p>You won&rsquo;t get the Monday summary of your training or news about
            Daash. Your connection keeps working exactly as before.</p>
            <form method=post action=/email/unsubscribe>
              <input type=hidden name=u value="{html.escape(fingerprint)}">
              <input type=hidden name=s value="{html.escape(signature)}">
              <button>Unsubscribe</button>
            </form>
            """,
        )
    store.opt_out_of_email(fingerprint)
    analytics.capture("email_unsubscribed", fingerprint, {})
    return page(
        "Unsubscribed",
        "<h1>You&rsquo;re unsubscribed</h1><p>No more Daash emails. Daash keeps "
        "working in Claude and ChatGPT as before.</p>",
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
    gone = store.get_user(token)
    if gone is not None and official.configured():
        # Garmin's official link goes too, told to Garmin as well as deleted here.
        await anyio.to_thread.run_sync(official.disconnect, token)
    removed = store.delete_user(token)
    if removed and gone is not None:
        # Disconnect deletes everything, crews included.
        crews.forget(crews.member_key(token, gone.email_hash))
    return page(
        "Disconnected",
        "<h1>Disconnected</h1><p>Your stored session has been deleted and the "
        "link no longer works. Remove the connector in Claude's settings too.</p>"
        if removed
        else "<div class=err>That link isn't one we know about. It may already "
        "have been disconnected.</div>",
    )


# Sign-ins running now, keyed by a keyed hash of what was typed. A second press
# of Connect, an Enter held down, or a phone resending the form arrives while
# the first login is still with Garmin. It waits for that login and gets the
# same answer, rather than sending Garmin another one and counting against the
# rate limit and the per-address throttle. The browser only shows the last
# response it asked for, so that one has to carry the result. Nothing is kept
# once the login finishes: pressing again after an answer is a fresh attempt.
_IN_FLIGHT: dict[str, asyncio.Future[Response]] = {}
_IN_FLIGHT_KEY = secrets.token_bytes(32)


def _attempt_key(email: str, password: str, flow: str) -> str:
    material = f"{email.strip().lower()}\0{password}\0{flow}".encode()
    return hmac.new(_IN_FLIGHT_KEY, material, hashlib.sha256).hexdigest()


@mcp.custom_route("/connect", methods=["POST"])
async def connect_submit(request: Request) -> Response:
    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))
    flow = str(form.get("flow", ""))
    if flow and OAUTH_ENABLED and OAUTH_PROVIDER.listed(flow) and official.configured():
        # This address only connects through Garmin's own page.
        return _garmin_intro(flow, usual=False)

    key = _attempt_key(email, password, flow)
    running = _IN_FLIGHT.get(key)
    if running is not None:
        return await _answer_in_time(running, flow)
    task = asyncio.ensure_future(_connect_attempt(request, form, email, password, flow))
    _IN_FLIGHT[key] = task
    task.add_done_callback(lambda _: _IN_FLIGHT.pop(key, None))
    return await _answer_in_time(task, flow)


# Fly's proxy closes a connection that has carried nothing for 60 seconds, and
# a sign-in that tries three addresses can take longer than that. A sign-in
# that answers within SIGNIN_HOLD seconds is sent as it always was. A longer
# one starts the page early and sends a space every SIGNIN_KEEPALIVE seconds,
# which the browser ignores before the page, until Garmin answers. Its status
# is then 200 whatever the outcome, since the status goes out first.
SIGNIN_HOLD = 20
SIGNIN_KEEPALIVE = 15


async def _answer_in_time(task: "asyncio.Future[Response]", flow: str) -> Response:
    # Shielded throughout, so the login carries on for the press that's still
    # waiting when the browser abandons this one.
    try:
        return await asyncio.wait_for(asyncio.shield(task), SIGNIN_HOLD)
    except asyncio.TimeoutError:
        pass

    async def body() -> Any:
        yield b" "
        while True:
            try:
                response = await asyncio.wait_for(asyncio.shield(task), SIGNIN_KEEPALIVE)
                break
            except asyncio.TimeoutError:
                yield b" "
            except Exception:  # noqa: BLE001 - the page has started; finish it
                log.exception("sign-in failed after the page had started")
                response = page(
                    "Sign in to Garmin",
                    "<div class=err>Something went wrong on our side. Please try "
                    "again.</div>" + _try_again(flow),
                )
                break
        yield bytes(response.body)

    return StreamingResponse(
        body(),
        media_type="text/html; charset=utf-8",
        # Every page a sign-in can end on is safe with these, and the consent
        # page needs them; they have to be chosen before the outcome is known.
        headers={
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "frame-ancestors 'none'",
            "Cache-Control": "no-store",
        },
    )


async def _connect_attempt(request: Request, form: Any, email: str, password: str, flow: str) -> Response:
    if _too_many_attempts(_client_address(request)):
        _failed("throttled", email, **_where(request))
        return page(
            "Sign in to Garmin",
            "<div class=err>Too many sign-in attempts. Wait fifteen minutes and "
            "try again.</div>" + _try_again(flow),
            429,
        )

    if INVITE_CODE and not hmac.compare_digest(
        str(form.get("invite", "")).strip(), INVITE_CODE
    ):
        return page(
            "Sign in to Garmin",
            "<div class=err>That invite code isn't right. Ask whoever sent you "
            "the link.</div>" + _try_again(flow),
            403,
        )

    if not email or not password:
        return page(
            "Sign in to Garmin",
            "<div class=err>Email and password required.</div>" + _try_again(flow),
            400,
        )

    left = _pause_left()
    if left:
        _failed("paused", email, **_where(request), **({"fallback": True} if _fallback_ok(flow) else {}))
        minutes = max(1, -(-left // 60))
        return page(
            "Sign in to Garmin",
            "<div class=err><strong>Garmin asked this server to slow down, so "
            "sign-ins are paused for a little while.</strong> It is not your "
            f"password, and nothing was sent to Garmin. Try again in about {minutes} "
            f"minute{'s' if minutes != 1 else ''}; trying sooner would only keep "
            "Garmin's limit in place for longer.</div>" + _garmin_instead(flow) + _try_again(flow),
            429,
        )

    # Where the last attempt went out from: a proxy address, or "" for the
    # server's own. The code step has to come from the same place, and only
    # a refusal of the server's own address is worth pausing everyone for.
    egress = login_proxy()

    traces: list[dict[str, Any]] = []

    def _try(via: str) -> tuple[Any, Any]:
        # Built inside, so the client's own sessions carry this address too.
        with login_egress(via) as trace:
            traces.append(trace)
            client = build_client(
                prompt_mfa=lambda: (_ for _ in ()).throw(
                    RuntimeError("multi-factor code required")
                ),
                email=email,
                password=password,
            )
            # return_on_mfa hands the flow back instead of prompting, so the
            # code can be collected over a second request.
            client.return_on_mfa = True
            result = client.login()
        return client, result

    started = time.monotonic()
    tries = 0
    wrong_before = _recent_wrong_password(email)

    def _login() -> tuple[Any, Any]:
        # Retries only go through the proxy: from a new address when Garmin
        # refused or stumbled on this one, or when the address itself didn't
        # answer (a home connection in the pool can be offline), while there
        # is time left. Two addresses that don't answer mean the proxy itself
        # is down, and then it's directly, once. A wrong password is final,
        # and so is anything from the server's own address.
        nonlocal egress, tries
        unreachable = 0
        while True:
            tries += 1
            try:
                return _try(egress)
            except Exception as exc:  # noqa: BLE001
                reason = _failure_reason(exc)
                if not egress or reason == "bad_credentials":
                    raise
                if wrong_before and reason in ("blocked_by_garmin", "rate_limited"):
                    # Their account is paused; another address can't help, and
                    # more tries only keep the pause going.
                    raise
                unreachable += reason == "proxy_failed"
                time_left = tries < SIGNIN_TRIES and time.monotonic() - started < SIGNIN_BUDGET
                if reason == "proxy_failed" and (unreachable >= 2 or not time_left):
                    egress, then = "", "direct"
                elif time_left:
                    egress, then = login_proxy(), "new_address"
                else:
                    raise
                log.warning("sign-in retrying %s after %s",
                            "directly" if not egress else "from a new address", reason)
                _retried(reason, then, email)

    def took() -> dict[str, Any]:
        return {"attempts": tries, "seconds": round(time.monotonic() - started, 1),
                **_traced(traces)}

    try:
        client, result = await anyio.to_thread.run_sync(_login)
    except Exception as exc:  # noqa: BLE001
        reason = _failure_reason(exc)
        account_paused = wrong_before and reason in ("blocked_by_garmin", "rate_limited")
        if reason == "bad_credentials":
            _note_wrong_password(email)
        elif account_paused:
            reason = "account_paused"
        paused = not egress and not account_paused
        if paused:
            _pause_sign_ins(reason)
        offered = reason in ("rate_limited", "blocked_by_garmin") and _fallback_ok(flow)
        _failed(reason, email, **took(), **_error_detail(exc), **_where(request),
                **({"fallback": True} if offered else {}))
        return page(
            "Sign in to Garmin",
            _signin_error(exc, flow, paused=paused, tries=tries,
                          account_paused=account_paused, email=email),
            400,
        )

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
            "flow": flow,
            "egress": egress,
            "took": took(),
            "started": time.time(),
        }
        return page(
            "Enter your code",
            f"""
            <h1>Enter your code</h1>
            <p>Garmin has sent you a code by text message or email, whichever you
            chose when you turned on two-step verification. It can take a minute to
            arrive. If it went to email, check your spam folder too.</p>
            <form method=post action=/mfa data-wait="Checking your code&hellip;">
              <input type=hidden name=pending value="{pending_id}">
              <label for=code>Code</label>
              <input id=code name=code inputmode=numeric autocomplete=one-time-code required>
              <button>Continue</button>
              <p class="note wait" role=status hidden>Checking with Garmin. Please
              don&rsquo;t press again.</p>
            </form>
            """,
        )

    return _finish(request, client, email, flow, took=took())


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
            with login_egress(pending.get("egress")):
                return client.resume_login(pending["state"], code)

        await anyio.to_thread.run_sync(_resume)
    except Exception as exc:  # noqa: BLE001
        _failed("mfa_rejected", pending.get("email", ""), **_error_detail(exc))
        return page(
            "Enter your code",
            f"<div class=err>That code wasn't accepted ({type(exc).__name__}). "
            f'Start again from <a href="{_connect_url(pending.get("flow", ""))}">'
            "the sign-in page</a>.</div>",
            400,
        )
    return _finish(request, client, pending["email"], pending.get("flow", ""), mfa=True,
                   took=pending.get("took"))


# Mail to support@daash.run: Resend receives it and posts here, and it goes
# on to the inbox that answers it (support_mail).
@mcp.custom_route("/mail/inbound", methods=["POST"])
async def mail_inbound(request: Request) -> Response:
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    status, outcome = await anyio.to_thread.run_sync(support_mail.handle, body, headers)
    return Response(outcome, status_code=status, media_type="text/plain")


# Garmin's official API (official.py), for preview accounts while it is built.
# The preview tool hands out a sealed link to /official/start; Garmin sends the
# person back to /official/callback; Garmin pushes their data to /official/push.
@mcp.custom_route("/official/start", methods=["GET"])
async def official_start(request: Request) -> Response:
    if not official.configured():
        return page("Not set up", "<div class=err>Garmin's official API isn't set up on this server yet.</div>")
    target = official.authorize_url(request.query_params.get("t", ""))
    if target is None:
        return page("Link expired", "<h1>That link has expired</h1><p>Ask Claude for a new one: "
                    "they last 15 minutes.</p>")
    return RedirectResponse(target, status_code=302)


@mcp.custom_route("/official/signup", methods=["GET"])
async def official_signup(request: Request) -> Response:
    """From Connect your Garmin to Garmin's own approval page, with the connector's request carried along."""
    flow = request.query_params.get("flow", "")
    if not (OAUTH_ENABLED and official.configured()) or OAUTH_PROVIDER.pending(flow) is None:
        return _consent_expired()
    via = request.query_params.get("via", "")
    analytics.capture("garmin_sign_in_started", None, _with_source(request, {"via": via} if via == "blocked" else {}))
    return RedirectResponse(official.signup_url(flow), status_code=302)


async def _signup_callback(request: Request, code: str, state: str, flow: str) -> Response:
    """Back from Garmin's page during a sign-up: make or find the account, then the Allow page."""
    retry = f'<a class=button href="/official/signup?flow={quote(flow, safe="")}">Try again with Garmin</a>'
    if not code:
        analytics.capture("garmin_declined", None, _with_source(request, {}))
        usual = "" if OAUTH_PROVIDER.listed(flow) else _usual_way(flow)
        return page("Not connected", f"""<h1>Not connected</h1><p>Garmin didn't share access, so nothing
                    changed and nothing was kept.</p>{retry}{usual}""")
    try:
        approval = await anyio.to_thread.run_sync(official.finish_signup, code, state)
    except official.OfficialError as exc:
        return page("Not connected", f"<div class=err>{html.escape(str(exc))}</div>{retry}")
    except (OSError, ValueError, KeyError) as exc:
        log.warning("official sign-up failed: %s", type(exc).__name__)
        return page("Not connected", "<div class=err>Garmin couldn't be reached to finish connecting. "
                    f"Try again in a minute.</div>{retry}")
    if approval.new:
        # Everything is new to someone just connecting.
        store.claim_news(approval.user_token, NEWS_VERSION)
    analytics.capture("garmin_approved", None, _with_source(request, {
        "new": approval.new,
        "workouts": "unknown" if approval.permissions is None else "WORKOUT_IMPORT" in approval.permissions}))
    staged = OAUTH_PROVIDER.stage(approval.flow, approval.user_token)
    if not staged:
        return page("Sign-in expired", "<div class=err>That sign-in took too long and the request has "
                    "expired. Start again from the connector in Claude.</div>", 400)
    consent_token, parked = staged
    now = time.time()
    for stale in [t for t, at in _GARMIN_CONSENTS.items() if at < now - 3600]:
        _GARMIN_CONSENTS.pop(stale, None)
    _GARMIN_CONSENTS[consent_token] = now
    return _consent_page(consent_token, parked, garmin=True,
                         ask_email=store.email_for(approval.user_token) is None)


@mcp.custom_route("/official/callback", methods=["GET"])
async def official_callback(request: Request) -> Response:
    if not official.configured():
        return page("Not set up", "<div class=err>Garmin's official API isn't set up on this server yet.</div>")
    code, state = request.query_params.get("code", ""), request.query_params.get("state", "")
    flow = official.signup_flow(state) if state else None
    if flow and OAUTH_ENABLED:
        return await _signup_callback(request, code, state, flow)
    if not code:
        return page("Not connected", "<h1>Not connected</h1><p>Garmin didn't share access, so nothing "
                    "changed. Ask Claude for a new link to try again.</p>")
    try:
        await anyio.to_thread.run_sync(official.finish, code, state)
    except official.OfficialError as exc:
        return page("Not connected", f"<div class=err>{html.escape(str(exc))}</div>")
    except (OSError, ValueError, KeyError) as exc:
        log.warning("official sign-in failed: %s %s", type(exc).__name__, exc if isinstance(exc, KeyError) else "")
        return page("Not connected", "<div class=err>Garmin couldn't be reached to finish connecting. "
                    "Ask Claude for a new link and try again.</div>")
    return page("Connected", "<h1>Connected to Garmin</h1><p>Daash is linked to Garmin's official API. "
                "You can close this page and go back to your chat.</p>")


@mcp.custom_route("/official/push/{key}", methods=["POST"])
async def official_push(request: Request) -> Response:
    if not official.configured() or not hmac.compare_digest(request.path_params["key"], official.push_key()):
        return Response("not found", status_code=404)
    body = await request.body()
    # Garmin wants the 200 before any processing, within 30 seconds, so the
    # data is kept after the answer has gone.
    return Response("ok", media_type="text/plain",
                    background=BackgroundTask(anyio.to_thread.run_sync, official.handle_push, body))


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return HTMLResponse("ok")


# A crew's own page: its week for anyone with the link the crew's starter
# turned on. The same card the chat draws, run as a page of its own, with the
# week written into it so it shows at once. Kept out of search engines,
# caches and referrer headers; no analytics, no cookies, nothing from
# anywhere else.
_UI_DIR = Path(__file__).parent / "ui"
_CREW_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}
CREW_PAGE_PROMO = {
    "text": "Got a Garmin? Daash fills in your runs for you.",
    "link": "Try Daash",
    "url": "https://daash.run/?utm_source=crew-page&utm_medium=link&utm_campaign=crews",
}


def _crew_page_week(request: Request) -> dict[str, Any] | None:
    from datetime import date as _date

    raw = request.query_params.get("week_of") or ""
    try:
        week_of = _date.fromisoformat(raw) if raw else None
    except ValueError:
        week_of = None
    return crews.public_week(request.path_params.get("link", ""), week_of)


@mcp.custom_route("/crew/{link}", methods=["GET"])
async def crew_page(request: Request) -> Response:
    card = await anyio.to_thread.run_sync(_crew_page_week, request)
    if card is None:
        return Response(
            "<!doctype html><meta charset=utf-8><meta name=robots content=noindex>"
            "<meta name=viewport content=\"width=device-width,initial-scale=1\"><title>Daash crew</title>"
            "<body style=\"font:16px/1.5 system-ui,sans-serif;max-width:520px;margin:15vh auto;padding:0 16px\">"
            "<p>This crew page isn't available. The link may have been turned off or replaced: "
            "ask the crew for the new one.</p>",
            status_code=404, media_type="text/html", headers=_CREW_PAGE_HEADERS,
        )
    if request.query_params.get("format") == "json":
        return Response(json.dumps(card), media_type="application/json", headers=_CREW_PAGE_HEADERS)
    config = json.dumps({"card": card, "promo": CREW_PAGE_PROMO}).replace("<", "\\u003c")
    view = (_UI_DIR / "crew.html").read_text(encoding="utf-8")
    view = view.replace(
        "<title>Daash crew</title>",
        "<title>Daash crew</title><meta name=robots content=\"noindex, nofollow\">"
        f"<script>window.__CREW_PAGE__ = {config};</script>",
        1,
    )
    return HTMLResponse(view, headers=_CREW_PAGE_HEADERS)


# People without Daash add their easy runs on the crew's page. JSON only:
# a browser won't send JSON to another site's address without asking it
# first, so another page can't make these requests for someone. Paced per
# address, on top of the PIN's own lock.
_CREW_WRITES: dict[str, list[float]] = {}
_CREW_WRITE_LIMIT = 60          # changes an hour from one address
_CREW_JOIN_LIMIT = 10           # people added an hour from one address


def _crew_paced(address: str, kind: str, limit: int) -> bool:
    now = time.time()
    key = f"{kind}|{address}"
    recent = [t for t in _CREW_WRITES.get(key, []) if t > now - 3600]
    recent.append(now)
    _CREW_WRITES[key] = recent
    if len(_CREW_WRITES) > 5000:
        for old in sorted(_CREW_WRITES, key=lambda k: _CREW_WRITES[k][-1])[:1000]:
            _CREW_WRITES.pop(old, None)
    return len(recent) > limit


def _crew_answer(body: dict[str, Any], status: int = 200) -> Response:
    return Response(json.dumps(body), status_code=status, media_type="application/json", headers=_CREW_PAGE_HEADERS)


@mcp.custom_route("/crew/{link}/runner", methods=["POST"])
async def crew_runner(request: Request) -> Response:
    """Add yourself, sign in, read or change your runs, or take yourself off: one address, an action each."""
    if "application/json" not in request.headers.get("content-type", ""):
        return _crew_answer({"error": "JSON only."}, 415)
    try:
        body = await request.json()
    except ValueError:
        return _crew_answer({"error": "That wasn't JSON."}, 400)
    if not isinstance(body, dict):
        return _crew_answer({"error": "That wasn't JSON."}, 400)
    address = _client_address(request)
    action = str(body.get("action") or "")
    if _crew_paced(address, "write", _CREW_WRITE_LIMIT) or (action == "add" and _crew_paced(address, "add", _CREW_JOIN_LIMIT)):
        return _crew_answer({"error": "Too many changes from here. Try again in an hour."}, 429)
    link = request.path_params.get("link", "")
    token = body.get("token")

    def run() -> dict[str, Any] | None:
        if action == "add":
            return crews.add_runner(link, body.get("name"), body.get("pin"), body.get("usual"), club=body.get("club") is True)
        if action == "sign_in":
            return crews.runner_sign_in(link, body.get("id"), body.get("pin"))
        if action == "mine":
            return crews.runner(link, token)
        if action == "update":
            club = body.get("club")
            return crews.update_runner(link, token, body.get("usual"), body.get("changes"), bool(body.get("still_running")),
                                       club=club if isinstance(club, bool) else None)
        if action == "remove":
            crews.remove_runner(link, token)
            return {"removed": True}
        return None

    try:
        answer = await anyio.to_thread.run_sync(run)
    except crews.Locked as exc:
        return _crew_answer({"error": str(exc)}, 429)
    except crews.CrewError as exc:
        return _crew_answer({"error": str(exc)}, 400)
    if answer is None:
        return _crew_answer({"error": "Unknown action."}, 400)
    return _crew_answer(answer)


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


@functools.lru_cache(maxsize=4)
def _preview_accounts(configured: str) -> frozenset[str]:
    """Fingerprints of the accounts in GARMIN_MCP_PREVIEW.

    The addresses are only ever read from the secret and compared as
    fingerprints, the same way sign-in recognises a returning person; none is
    stored.
    """
    return frozenset(
        store.email_fingerprint(entry)
        for entry in configured.split(",")
        if "@" in entry
    )


def _preview_for(user: store.User) -> bool:
    """Whether this account sees preview features.

    GARMIN_MCP_PREVIEW is either a switch for everyone ("on") or a comma
    separated list of Garmin sign-in addresses. Unset, nobody does.
    """
    if preview.switched_on_for_everyone():
        return True
    configured = os.environ.get("GARMIN_MCP_PREVIEW", "").strip()
    return bool(configured and user.email_hash and user.email_hash in _preview_accounts(configured))


_METADATA_LINK = re.compile(rb'resource_metadata="[^"]*"')


def _named_metadata_url(path: str = oauth.LISTED_PATH) -> str:
    base = os.environ.get("GARMIN_MCP_BASE_URL", "").strip().rstrip("/")
    return f"{base}/.well-known/oauth-protected-resource{path}"


def _pointing_at_named(send: Any, path: str = oauth.LISTED_PATH) -> Any:
    """Answer a 401 at a named address (listed, password) with its own metadata link.

    The SDK's link names the server's root resource. A client that followed it
    would ask for a token for the root, and its sign-in would not be known to
    have come from that address.
    """
    async def wrapped(message: Any) -> None:
        if message.get("type") == "http.response.start" and message.get("status") == 401:
            link = f'resource_metadata="{_named_metadata_url(path)}"'.encode()
            message = {**message, "headers": [
                (k, _METADATA_LINK.sub(link, v) if k.lower() == b"www-authenticate" else v)
                for k, v in message.get("headers", [])]}
        await send(message)
    return wrapped


@mcp.custom_route(f"/.well-known/oauth-protected-resource{oauth.LISTED_PATH}", methods=["GET", "OPTIONS"])
@mcp.custom_route(f"/.well-known/oauth-protected-resource{oauth.PASSWORD_PATH}", methods=["GET", "OPTIONS"])
async def listed_resource_metadata(request: Request) -> Response:
    """RFC 9728 metadata for a named address: the same authorization server, its own resource name."""
    from starlette.responses import JSONResponse

    cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*"}
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=cors)
    base = os.environ.get("GARMIN_MCP_BASE_URL", "").strip().rstrip("/")
    named = oauth.PASSWORD_PATH if request.url.path.endswith(oauth.PASSWORD_PATH) else oauth.LISTED_PATH
    return JSONResponse({
        "resource": f"{base}{named}",
        "authorization_servers": [f"{base}/"],
        "bearer_methods_supported": ["header"],
        "resource_name": "Daash",
    }, headers=cors)


# OpenAI's Apps directory checks Daash owns this host by fetching its token
# here, as plain text. The token is public by design: it proves nothing alone.
OPENAI_APPS_CHALLENGE = "7FrcPNgiDeHE-Sac2sGq2fFmdmHOoiZosLQkUI0q7Nc"


@mcp.custom_route("/.well-known/openai-apps-challenge", methods=["GET"])
async def openai_apps_challenge(request: Request) -> Response:
    """The OpenAI Apps directory's domain check: the token alone, as plain text."""
    return Response(OPENAI_APPS_CHALLENGE, media_type="text/plain")


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

        named = scope.get("path", "").rstrip("/")
        if OAUTH_ENABLED and named in oauth.NAMED_PATHS:
            # The address in the app directories, and the password link, are
            # this same endpoint. Only their sign-in differs, and clients
            # learn that from their metadata.
            scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}
            send = _pointing_at_named(send, named)

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
            app = oauth.app_for_client(row["client_id"]) if row else None
            if not user_token:
                await self.app(scope, receive, send)
                return
        else:
            match = _PATH_RE.match(scope.get("path", ""))
            if not match:
                await self.app(scope, receive, send)
                return
            user_token = match.group("token")
            app = "Connector URL"

        user = store.get_user(user_token)
        if user is None:
            # The Garmin session behind this token is gone — disconnected, or
            # signed in again. The token outlived what it pointed at.
            store.delete_tokens_for_subject(user_token)
            _SESSIONS.pop(user_token, None)
            if OAUTH_ENABLED:
                # With its token now gone the SDK answers 401 invalid_token,
                # which Claude turns into a Reconnect button. A 404 here read
                # as a broken server instead.
                await self.app(scope, receive, send)
                return
            await Response("Unknown connector URL", status_code=404)(scope, receive, send)
            return

        now = time.time()
        if now - _TOUCHED.get(user_token, 0.0) > _TOUCH_EVERY:
            _TOUCHED[user_token] = now
            if store.touch_user(user_token):
                email = store.email_for(user_token)
                analytics.capture(
                    "first_tool_call",
                    user.email_hash or user_token,
                    {"account": user.email_masked, **({"email": email} if email else {})},
                )

        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        if _USED_ON.get(user_token) != today:
            _USED_ON[user_token] = today
            email = store.email_for(user_token)
            analytics.capture(
                "connector_used",
                user.email_hash or user_token,
                {"account": user.email_masked, "day": today, **({"app": app} if app else {}),
                 **({"email": email} if email else {})},
            )

        token = use_session(session_for(user_token))
        preview_token = preview.use(_preview_for(user))
        alone = not store.has_connect(user_token)
        hook_token = hooks.use(_after_tool_for(user_token, user, alone), _before_tool_for(user_token, alone))
        crew_token = crews.use(crews.Member(crews.member_key(user_token, user.email_hash)))
        official_token = official.use(user_token)
        alone_token = server_mod.use_alone(alone)
        try:
            await self.app(scope, receive, send)
        finally:
            server_mod.reset_alone(alone_token)
            official.reset(official_token)
            crews.reset(crew_token)
            hooks.reset(hook_token)
            preview.reset(preview_token)
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

    threading.Thread(target=_keep_alive_forever, name="keep-alive", daemon=True).start()
    if WEEKLY_EMAIL and support_mail.can_send():
        threading.Thread(target=_weekly_email_forever, name="weekly-email", daemon=True).start()
    if analytics.enabled():
        threading.Thread(target=_signin_check_forever, name="signin-check", daemon=True).start()
    if official.configured():
        if backfill.ENABLED:
            threading.Thread(target=backfill.forever, name="official-history", daemon=True).start()
        # What Garmin's official API sent before Daash trimmed it on arrival.
        try:
            official_data.tidy()
        except Exception:  # noqa: BLE001 - a tidy-up never stops the server starting
            log.exception("official data tidy failed")

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
