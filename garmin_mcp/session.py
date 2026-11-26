"""Owns the single authenticated Garmin Connect client used by the MCP tools.

Credentials only ever come from the environment. Token caching, atomic 0600
writes and stale-token recovery are delegated to garminconnect, which already
does all of it carefully; this module's job is to keep exactly one session
alive, keep MFA from ever reading stdin, and turn failures into messages that
are safe and useful to hand back through a tool.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
import weakref
from collections import OrderedDict
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from . import garmin_cache, tracing
from .gate import GateError, gate

log = logging.getLogger(__name__)

# Remembered answers per session. Past this many the one remembered longest
# ago goes (Nov 25: least recently written, not the whole lot at once).
MEMO_LIMIT = 400

# Every Garmin client still alive in this process, counted for the heartbeat
# (garmin_clients_live): sessions dropped from memory should take theirs with
# them, and a number that only climbs means something still holds them.
_LIVE_CLIENTS: "weakref.WeakSet[Any]" = weakref.WeakSet()


def live_clients() -> int:
    return len(_LIVE_CLIENTS)


def close_client(client: Any) -> None:
    """Close a Garmin client's HTTP sessions (its pooled connections, and the
    curl session it signs in with, which holds native memory)."""
    inner = getattr(client, "client", None)
    for holder in (inner, client):
        for name in ("_api_session", "cs", "session", "_session"):
            sess = getattr(holder, name, None) if holder is not None else None
            close = getattr(sess, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

TOKEN_FILE = Path(
    os.environ.get("GARMIN_MCP_TOKENS") or Path.home() / ".garmin-mcp" / "tokens.json"
)

# Wherever this checkout happens to live, so the advice is runnable as printed.
LOGIN_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "login.sh"

CREDS_HINT = (
    "Alternatively, set GARMIN_EMAIL and GARMIN_PASSWORD in the environment this "
    'server runs in (for Claude Desktop, the "env" block of the server entry in '
    "claude_desktop_config.json) so it can sign in by itself."
)
MFA_MESSAGE = (
    "Garmin is asking for a multi-factor code, and this server has no way to "
    "prompt for one over stdio. Run `python -m garmin_mcp.login` in a terminal "
    "once to complete the multi-factor login and cache the session, then retry."
)


class GarminError(RuntimeError):
    """An error whose message is safe and useful to hand back through a tool."""


class GarminAuthError(GarminError):
    pass


class GarminNotFound(GarminError):
    """Garmin answered 404: the workout, schedule entry or activity isn't there."""


class GarminMFARequired(GarminAuthError):
    pass


def mask_email(email: str | None) -> str | None:
    """b***@example.com — enough to confirm which account, not enough to leak it."""
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return f"{local[0]}{'*' * max(len(local) - 1, 1)}@{domain}"


def credentials() -> tuple[str, str]:
    return (
        os.environ.get("GARMIN_EMAIL", "").strip(),
        os.environ.get("GARMIN_PASSWORD", ""),
    )


def token_cache_info() -> dict[str, Any]:
    """Describe the cache file. Never reads or returns its contents."""
    info: dict[str, Any] = {"path": str(TOKEN_FILE), "exists": TOKEN_FILE.exists()}
    if info["exists"]:
        stat = TOKEN_FILE.stat()
        info["age_hours"] = round((time.time() - stat.st_mtime) / 3600, 1)
        info["permissions"] = oct(stat.st_mode & 0o777)
    return info


def _token_mtime() -> float | None:
    try:
        return TOKEN_FILE.stat().st_mtime
    except OSError:
        return None


def _mfa_unavailable() -> str:
    """Stands in for the library's interactive MFA prompt.

    Without this garminconnect calls input(), which would read the MCP client's
    JSON-RPC stream and hang the server.
    """
    raise GarminMFARequired(MFA_MESSAGE)


def _is_auth_failure(exc: BaseException) -> bool:
    """Is this Garmin rejecting our credentials, or just a bad connection?

    garminconnect raises GarminConnectConnectionError for network trouble as
    well as for auth trouble. Re-authenticating over a dropped connection
    achieves nothing and, with no password stored, produces a misleading
    "your session is invalid" message for what was a momentary blip.
    """
    if type(exc).__name__ == "GarminConnectAuthenticationError":
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("401", "unauthorized", "authentication failed", "invalid_grant")
    )


def _is_mfa(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "multi-factor" in text or "mfa" in text


def _auth_error_types() -> tuple[type[BaseException], ...]:
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
    )

    return (GarminConnectAuthenticationError, GarminConnectConnectionError)


def build_client(
    *,
    prompt_mfa: Callable[[], str],
    email: str | None = None,
    password: str | None = None,
) -> Any:
    """Construct a Garmin client; `prompt_mfa` decides how MFA is handled.

    Credentials default to the environment, which is how this server works.
    They can be passed explicitly by a caller that collects them another way.
    """
    try:
        from garminconnect import Garmin
    except ImportError as exc:  # pragma: no cover - install problem
        raise GarminError(
            "The `garminconnect` package is not installed. Install dependencies "
            "with `uv pip install -r requirements.txt`."
        ) from exc

    env_email, env_password = credentials()
    client = Garmin(
        email=email or env_email or None,
        password=password or env_password or None,
        prompt_mfa=prompt_mfa,
    )
    tracing.watch(client)
    with contextlib.suppress(TypeError):
        _LIVE_CLIENTS.add(client)
    return client


def login_error(exc: BaseException, *, had_cache: bool = False) -> GarminError:
    """Turn a garminconnect failure into something worth reading."""
    if _is_mfa(exc):
        return GarminMFARequired(MFA_MESSAGE)

    name = type(exc).__name__
    text = str(exc)
    if name == "GarminConnectTooManyRequestsError" or "429" in text:
        return GarminAuthError(
            "Garmin is rate-limiting logins from this IP address (HTTP 429). This "
            "is not a problem with your email or password — Garmin blocked the "
            "request before checking them.\n"
            "  - Wait before trying again. Half an hour is usually enough; a "
            "flagged IP can take several hours.\n"
            "  - Retrying in a loop extends the block, so leave it alone in "
            "between.\n"
            "  - If you are on a VPN or a shared connection, turn it off and "
            "retry: Garmin rate-limits those ranges hard.\n"
            "  - Signing in at connect.garmin.com in a browser confirms whether "
            "the block is your IP or your account.\n"
            f"  ({name}: {exc})"
        )

    email, password = credentials()
    if not (email and password):
        return GarminAuthError(
            "The cached Garmin session is no longer valid. The simplest fix is to "
            "sign in again in a terminal:\n"
            f"    {LOGIN_SCRIPT}\n"
            "then restart Claude Desktop. Alternatively, add GARMIN_PASSWORD to the "
            "server's environment so it can re-authenticate by itself. "
            f"({name}: {exc})"
        )
    stale = " The cached session was also rejected." if had_cache else ""
    return GarminAuthError(
        f"Could not log in to Garmin Connect.{stale} Re-check GARMIN_EMAIL and "
        "GARMIN_PASSWORD, and confirm you can sign in at connect.garmin.com. "
        f"({name}: {exc})"
    )


class GarminSession:
    """Lazily authenticates, prefers the cached token, re-logs in when it dies.

    Every Garmin call is blocking, so all of this is synchronous and the server
    runs it on a worker thread. The lock stops two tool calls racing into two
    simultaneous logins.
    """

    def __init__(
        self,
        tokenstore: str | Callable[[], str | None] | None = None,
        on_refresh: Callable[[str], None] | None = None,
        reconnect_hint: str | None = None,
        on_lapsed: Callable[[], None] | None = None,
        owner: str | None = None,
    ) -> None:
        """
        tokenstore: where the Garmin session comes from. None means the local
            token file, which is all this server uses. A caller embedding this
            module can pass a string, or a callable returning one, to supply the
            token from somewhere else — garminconnect accepts it inline.
        on_refresh: called with a fresh token blob after a password login, for
            callers storing it themselves. Unused here, where garminconnect
            writes the file.
        reconnect_hint: when hosted there is no terminal and no password, so a
            session Garmin has refused is fixed by connecting again in Claude.
            This replaces the local-install advice in that one case.
        on_lapsed: called when Garmin has refused the stored session for good.
        owner: whose session this is, for the per-person limits in gate.py.
        """
        self._lock = threading.RLock()
        self._memo: "OrderedDict[Any, tuple[float, Any]]" = OrderedDict()
        self._client: Any = None
        self._source: str | None = None
        self._connected_at: float | None = None
        self._tokenstore = tokenstore
        self._on_refresh = on_refresh
        self._saved_blob: str | None = None
        self._reconnect_hint = reconnect_hint
        self._on_lapsed = on_lapsed
        self._owner = owner

    def _resolve_tokenstore(self) -> tuple[str | None, bool]:
        """Return (what to hand garminconnect, whether a session already existed)."""
        if self._tokenstore is None:
            return str(TOKEN_FILE), TOKEN_FILE.exists()
        blob = self._tokenstore() if callable(self._tokenstore) else self._tokenstore
        return blob, bool(blob)

    def _connect(self) -> Any:
        email, password = credentials()
        tokenstore, had_session = self._resolve_tokenstore()
        if not had_session and self._reconnect_hint:
            self._lapsed()
            raise GarminAuthError(self._reconnect_hint)
        if not had_session and not (email and password):
            raise GarminAuthError(
                "Not signed in to Garmin yet. Sign in once in a terminal:\n"
                f"    {LOGIN_SCRIPT}\n"
                "then restart Claude Desktop. " + CREDS_HINT
            )

        client = build_client(prompt_mfa=_mfa_unavailable)
        before = _token_mtime()

        try:
            # garminconnect resumes from this (a file path locally, an inline
            # token blob when hosted), refreshes when it is close to expiry,
            # falls back to the password, and rewrites the file on success —
            # all of which we would otherwise reimplement.
            client.login(tokenstore=tokenstore)
        except GarminMFARequired:
            raise
        except Exception as exc:
            if self._reconnect_hint and _is_auth_failure(exc) and "429" not in str(exc):
                self._lapsed()
                raise GarminAuthError(self._reconnect_hint) from exc
            raise login_error(exc, had_cache=had_session) from exc

        after = _token_mtime()
        # A rewritten (or newly created) token file means the password was used;
        # an untouched one means the cached session was good.
        resumed = had_session if self._tokenstore is not None else (
            before is not None and after == before
        )
        self._client = client
        self._source = "cached token" if resumed else "password login"

        if self._on_refresh is not None:
            with contextlib.suppress(Exception):
                blob = client.client.dumps()
                self._on_refresh(blob)
                self._saved_blob = blob
        self._connected_at = time.time()
        log.info("Garmin session established via %s", self._source)
        return client

    def reset(self) -> None:
        # Not closed here: other reads in flight may still be using it after
        # Garmin refused the session; it goes when the last of them lets go.
        with self._lock:
            self._client = None
            self._source = None
            self._connected_at = None

    def close(self) -> None:
        """Let go of everything: the client's connections, and what's remembered.
        For a session dropped from memory (the idle sweep, the cap)."""
        with self._lock:
            client, self._client = self._client, None
            self._memo.clear()
        if client is not None:
            close_client(client)

    def client(self) -> Any:
        with self._lock:
            if self._client is None:
                self._connect()
            return self._client

    def run(self, fn: Callable[[Any], Any]) -> Any:
        """Run `fn` against a live client, retrying once through a fresh login.

        Garmin expires sessions server-side without warning, so one auth failure
        means "reauthenticate", not "report an error".

        The lock covers connecting and reconnecting, not the request itself, so
        a tool's several reads can be in flight at once: garminconnect keeps a
        pool of connections and its own lock around renewing its token.
        """
        try:
            # The pool, time limit and per-person cap (gate.py) cover the
            # whole call, sign-in renewal included.
            result = gate.call(self._owner, lambda: self._call(fn))
        except GateError as exc:
            raise GarminError(str(exc)) from None
        with self._lock:
            self._save_if_renewed()
        return result

    def _lapsed(self) -> None:
        if self._on_lapsed is not None:
            with contextlib.suppress(Exception):
                self._on_lapsed()

    def _save_if_renewed(self) -> None:
        """Store the Garmin tokens again if a call renewed them.

        garminconnect renews its access token in place when it nears expiry, and
        Garmin can hand back a new refresh token with it. Saving only at sign-in
        left the stored copy holding the old one, so the next restart resumed
        from a token Garmin had already retired and the person had to sign in
        again. Comparing first keeps this to one write per renewal.
        """
        if self._on_refresh is None or self._client is None:
            return
        with contextlib.suppress(Exception):
            blob = self._client.client.dumps()
            if blob != self._saved_blob:
                self._on_refresh(blob)
                self._saved_blob = blob

    @property
    def owner(self) -> str | None:
        return self._owner

    def _view(self, client: Any) -> Any:
        """The client a call sees: its reads answered from the SQLite cache
        first (garmin_cache.py) when this is someone's hosted session."""
        return garmin_cache.view(client, self._owner)

    def _call(self, fn: Callable[[Any], Any]) -> Any:
        client = self.client()
        try:
            return fn(self._view(client))
        except _auth_error_types() as exc:
            if _is_mfa(exc):
                raise GarminMFARequired(MFA_MESSAGE) from exc
            if type(exc).__name__ == "GarminConnectNotFoundError":
                # Nov 26: a 404 is Garmin saying the thing is gone, not a
                # network problem: asking again can't help, and it was told
                # to the user as "could not reach Garmin".
                raise GarminNotFound(
                    "Garmin has nothing with that id (NotFound): it was already removed, "
                    "or the id is wrong."
                ) from exc
            if not _is_auth_failure(exc):
                # A connection problem, not a rejected session. Try once more
                # on the same client rather than throwing the session away.
                log.info("Call failed (%s); retrying", type(exc).__name__)
                try:
                    return fn(self._view(client))
                except Exception as retry_exc:  # noqa: BLE001
                    raise GarminError(
                        "Could not reach Garmin Connect. This looks like a "
                        f"network problem rather than a sign-in one "
                        f"({type(retry_exc).__name__}). Try again shortly."
                    ) from retry_exc
            log.info("Session rejected (%s); re-authenticating", type(exc).__name__)
            with self._lock:
                # Several reads in flight can be refused together; only the
                # first replaces the client, the rest use the one it made.
                if self._client is client:
                    self.reset()
                client = self.client()
            return fn(self._view(client))

    def remembered(self, key: Any, ttl: float, fn: Callable[[Any], Any]) -> Any:
        """run(fn), keeping the answer for ttl seconds under key.

        For reads that rarely change and are asked for again within minutes:
        a workout's steps, a month of the calendar. Kept per session, so one
        person's answers are never another's.
        """
        now = time.monotonic()
        hit = self._memo.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
        value = self.run(fn)
        self._keep(key, (now, value))
        return value

    def _keep(self, key: Any, entry: tuple[float, Any]) -> None:
        with contextlib.suppress(Exception):
            self._memo.pop(key, None)
            self._memo[key] = entry
            while len(self._memo) > MEMO_LIMIT:
                self._memo.popitem(last=False)

    def knows(self, key: Any, ttl: float) -> bool:
        """Whether an answer under key is remembered and still fresh, without fetching it."""
        hit = self._memo.get(key)
        return hit is not None and time.monotonic() - hit[0] < ttl

    def forget(self) -> None:
        """Drop everything remembered, after anything that writes to Garmin."""
        self._memo.clear()

    def peek(self, key: Any) -> Any:
        """What's remembered under key, however old, or None: never a read."""
        hit = self._memo.get(key)
        return hit[1] if hit is not None else None

    def remember(self, key: Any, value: Any) -> None:
        """Keep an answer already in hand, such as a workout just written, so
        the next read of it doesn't go back to Garmin for the same thing."""
        self._keep(key, (time.monotonic(), value))

    def status(self) -> dict[str, Any]:
        """Describe the connection without touching the password or the token."""
        email, password = credentials()
        info: dict[str, Any] = {
            "authenticated": False,
            # Only meaningful for a local install: hosted, these are the
            # operator's environment and nothing to do with this person.
            "account": mask_email(email) if self._tokenstore is None else None,
            "credentials_present": (
                {"GARMIN_EMAIL": bool(email), "GARMIN_PASSWORD": bool(password)}
                if self._tokenstore is None
                else None
            ),
            # A session backed by something other than the local file has no
            # path worth reporting.
            "token_cache": (
                token_cache_info()
                if self._tokenstore is None
                else {"stored": "server-side, encrypted"}
            ),
        }
        info = {k: v for k, v in info.items() if v is not None}
        try:
            with self._lock:
                client = self.client()
                # A cheap authenticated round-trip, so this reflects reality
                # rather than just what we cached in memory.
                client.get_user_profile()
            info["authenticated"] = True
            info["authenticated_via"] = self._source
            # No name: the check needs none, and a status call that hands the
            # chat someone's full name was why ChatGPT's directory review
            # turned Daash down (Nov 5, "returns personal identifiers that
            # aren't required for the user's request").
        except GarminError as exc:
            info["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"{type(exc).__name__}: {exc}"
        return info


_local_session = GarminSession()

# Nothing sets this in normal use: the single local session is used. It exists
# so a caller embedding this module can scope a session to a request.
_current_session: ContextVar["GarminSession | None"] = ContextVar(
    "garmin_current_session", default=None
)


def use_session(target: "GarminSession"):
    """Bind a session to the current context; returns a token for resetting."""
    return _current_session.set(target)


def reset_session(token) -> None:
    _current_session.reset(token)


def current_session() -> "GarminSession":
    """The request's session, or the local one."""
    return _current_session.get() or _local_session


class _ActiveSession:
    """Delegates to the request's session, or the local one."""

    def _target(self) -> GarminSession:
        return _current_session.get() or _local_session

    def run(self, fn: Callable[[Any], Any]) -> Any:
        return self._target().run(fn)

    def status(self) -> dict[str, Any]:
        return self._target().status()

    def client(self) -> Any:
        return self._target().client()

    def reset(self) -> None:
        self._target().reset()

    def remembered(self, key: Any, ttl: float, fn: Callable[[Any], Any]) -> Any:
        return self._target().remembered(key, ttl, fn)

    def knows(self, key: Any, ttl: float) -> bool:
        return self._target().knows(key, ttl)

    def forget(self) -> None:
        self._target().forget()

    def peek(self, key: Any) -> Any:
        return self._target().peek(key)

    def remember(self, key: Any, value: Any) -> None:
        self._target().remember(key, value)


session = _ActiveSession()
