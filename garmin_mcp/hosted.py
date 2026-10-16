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
from typing import Any
from urllib.parse import quote, urlparse

import anyio
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import analytics, hooks, oauth, preview, store
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
# before the refusal shows, so a refusal arrives after 25 to 35 seconds; the
# budget lets one more address follow it (the server's test sign-ins get a
# proper answer from the second address more often than not), where a
# refusal used to be the end.
SIGNIN_TRIES = 4
SIGNIN_BUDGET = 35


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
# The web sign-in's API can also say 429 inside a 200 answer, as JSON. The
# library reads that as a refusal too, but it reached it only after waiting 10
# to 20 seconds once per method and browser it tries, so it counts here too.
def _note_refusal(url: str, status: int, response: Any = None) -> None:
    url = str(url)
    if status == 200 and response is not None and "/api/" in url and _body_status(response) == 429:
        status = 429
    if status in (403, 429) and _egress_proxy() and "//sso." in url and "/mobile/" not in url:
        if not getattr(_EGRESS, "refused", None):
            log.info("sign-in address refused (HTTP %s); moving on", status)
        _EGRESS.refused = status


def _body_status(response: Any) -> int:
    """The HTTP status Garmin's sign-in API put inside a JSON answer, if any."""
    try:
        text = response.text
        if '"status-code"' not in text:
            return 0
        error = json.loads(text).get("error") or {}
        return int(error.get("status-code") or 0)
    except Exception:  # noqa: BLE001 - not JSON, or not that shape
        return 0


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
_METHODS = {
    "_mobile_login_cffi": "mobile",
    "_mobile_login_requests": "mobile_plain",
    "_widget_web_login": "widget",
    "_portal_web_login_cffi": "portal",
    "_portal_web_login_requests": "portal_plain",
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


def _after_tool_for(user_token: str, user: store.User) -> hooks.Hook:
    """The hook for one person's request: report the tool, carry any news."""
    def after(name: str, result: Any) -> Any:
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
            return {"tip": PERMISSION_TIP, **result}
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


def _keep_alive_forever() -> None:
    while True:
        with contextlib.suppress(Exception):
            store.sweep_expired_oauth()
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
_STYLE = """
:root { color-scheme: light; --ground:#F8F9FA; --surface:#fff; --ink:#1C212B;
  --muted:#6A7180; --line:#E2E4E9; --accent:#E63023; --accent-ink:#B8261C; }
* { box-sizing:border-box }
body { margin:0; background:var(--ground); color:var(--ink); font:16px/1.6
  Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing:antialiased; }
.top { border-bottom:1px solid var(--line); background:var(--surface) }
.top div { max-width:480px; margin:0 auto; padding:14px 20px }
.logo { font:italic 900 22px/1 "Inter Tight", Inter, ui-sans-serif, system-ui,
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
          <p class="note wait" role=status hidden
             data-later="Still working. Garmin can be slow to answer, and Daash keeps
             trying for you. Please stay on this page.">Signing you in to Garmin.
          This can take up to a minute, so please don&rsquo;t press again or leave
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
        return (
            "<div class=err><strong>Garmin has paused sign-ins to your account for "
            "now.</strong> That happens after a few wrong passwords. Wait about 30 "
            "minutes, check your password by signing in at connect.garmin.com (or "
            "reset it there with &ldquo;Forgot password&rdquo;), then try again "
            "here.</div>"
            + typed
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
                + _try_again(flow)
            )
        return (
            "<div class=err><strong>Garmin is asking this server to slow down.</strong> "
            "It is not your password. Garmin limits how many sign-ins it takes "
            "from one address, and several people have signed in through here "
            f"recently. Sign-ins are paused for {GARMIN_PAUSE // 60} minutes so the "
            "limit can clear; try again after that.</div>"
            + _try_again(flow)
        )
    if reason == "blocked_by_garmin":
        if not paused:
            return (
                "<div class=err><strong>Garmin turned this sign-in away, sorry.</strong> "
                "It is not your password: Garmin refused the request before checking "
                f"it{tried}. Please try again in a few minutes, or later today.</div>"
                + _try_again(flow)
            )
        return (
            "<div class=err><strong>Garmin turned this sign-in away, sorry.</strong> "
            "It is not your password: Garmin refused the request before checking "
            "it, because of where Daash connects from, not anything you did. "
            f"Sign-ins are paused for {GARMIN_PAUSE // 60} minutes; please try "
            "again after that, or later today.</div>"
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


def _consent_page(consent_token: str, parked: dict[str, Any]) -> Response:
    """Ask, by name, before handing a client access to this Garmin session.

    Everything variable here is escaped: the client name and the redirect come
    from an open registration endpoint, so both are attacker-controlled text.
    """
    params = parked["params"]
    destination = str(params.redirect_uri)
    name = parked.get("client_name") or parked["client_id"]
    known = oauth.recognised_redirect(destination)
    form = f"""
        <form method=post action=/oauth/consent style="margin-top:8px">
          <input type=hidden name=consent value="{html.escape(consent_token)}">
          <button name=decision value=allow>Allow</button>
          <button name=decision value=deny class=secondary>Cancel</button>
        </form>
        <p class=note style="margin-top:20px">You stay signed in to Garmin
        either way. Cancelling refuses this app, it does not disconnect you.</p>
    """

    # Claude or ChatGPT, known by the exact address the access goes back to.
    # The person started this from that app a minute ago, so the page says so
    # plainly instead of showing them a raw address to inspect.
    app = oauth.app_for_redirect(destination) if known else None
    if app in ("Claude", "ChatGPT"):
        host = urlparse(destination).hostname or ""
        body = f"""
        <h1>Connect Daash to {app}?</h1>
        <p class=secure>{_LOCK}<span>You started this from {app}. It will be able
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

    destination = (
        OAUTH_PROVIDER.approve(consent_token)
        if allow
        else OAUTH_PROVIDER.deny(consent_token)
    )
    if destination:
        return RedirectResponse(destination, status_code=303)
    return page(
        "Request expired",
        "<div class=err>That request has expired or was already answered. "
        "Start again from the connector in Claude.</div>",
        400,
    )


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
        {
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
        },
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
        form = await request.form()
        fingerprint, signature = str(form.get("u", "")), str(form.get("s", ""))
    genuine = bool(fingerprint) and hmac.compare_digest(signature, store.unsubscribe_signature(fingerprint))
    if not genuine:
        return page("Unsubscribe", "<div class=err>That unsubscribe link isn&rsquo;t valid. "
                    "Reply to any Daash email and you&rsquo;ll be taken off by hand.</div>", 400)
    if request.method == "GET":
        return page(
            "Unsubscribe",
            f"""
            <h1>Stop update emails?</h1>
            <p>You won&rsquo;t get emails about new Daash features. Your connection
            keeps working exactly as before.</p>
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
        "<h1>You&rsquo;re unsubscribed</h1><p>No more update emails. Daash keeps "
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
    removed = store.delete_user(token)
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

    key = _attempt_key(email, password, flow)
    running = _IN_FLIGHT.get(key)
    if running is not None:
        return await asyncio.shield(running)
    task = asyncio.ensure_future(_connect_attempt(request, form, email, password, flow))
    _IN_FLIGHT[key] = task
    task.add_done_callback(lambda _: _IN_FLIGHT.pop(key, None))
    # Shielded so the login carries on for the press that's still waiting when
    # the browser abandons this one.
    return await asyncio.shield(task)


async def _connect_attempt(request: Request, form: Any, email: str, password: str, flow: str) -> Response:
    if _too_many_attempts(_client_address(request)):
        _failed("throttled", email)
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
        _failed("paused", email)
        minutes = max(1, -(-left // 60))
        return page(
            "Sign in to Garmin",
            "<div class=err><strong>Garmin asked this server to slow down, so "
            "sign-ins are paused for a little while.</strong> It is not your "
            f"password, and nothing was sent to Garmin. Try again in about {minutes} "
            f"minute{'s' if minutes != 1 else ''}; trying sooner would only keep "
            "Garmin's limit in place for longer.</div>" + _try_again(flow),
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
        _failed(reason, email, **took(), **_error_detail(exc))
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
        hook_token = hooks.use(_after_tool_for(user_token, user))
        try:
            await self.app(scope, receive, send)
        finally:
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
    if analytics.enabled():
        threading.Thread(target=_signin_check_forever, name="signin-check", daemon=True).start()

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
