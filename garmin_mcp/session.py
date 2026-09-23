"""Owns the single authenticated Garmin Connect client used by the MCP tools.

Credentials only ever come from the environment. Token caching, atomic 0600
writes and stale-token recovery are delegated to garminconnect, which already
does all of it carefully; this module's job is to keep exactly one session
alive, keep MFA from ever reading stdin, and turn failures into messages that
are safe and useful to hand back through a tool.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

TOKEN_FILE = Path(
    os.environ.get("GARMIN_MCP_TOKENS") or Path.home() / ".garmin-mcp" / "tokens.json"
)

CREDS_HINT = (
    "Set GARMIN_EMAIL and GARMIN_PASSWORD in the environment this server runs in "
    '(for Claude Desktop that is the "env" block of the server entry in '
    "claude_desktop_config.json), then restart the server."
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


def _is_mfa(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "multi-factor" in text or "mfa" in text


def _auth_error_types() -> tuple[type[BaseException], ...]:
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
    )

    return (GarminConnectAuthenticationError, GarminConnectConnectionError)


def build_client(*, prompt_mfa: Callable[[], str]) -> Any:
    """Construct a Garmin client; `prompt_mfa` decides how MFA is handled."""
    try:
        from garminconnect import Garmin
    except ImportError as exc:  # pragma: no cover - install problem
        raise GarminError(
            "The `garminconnect` package is not installed. Install dependencies "
            "with `uv pip install -r requirements.txt`."
        ) from exc

    email, password = credentials()
    return Garmin(
        email=email or None,
        password=password or None,
        prompt_mfa=prompt_mfa,
    )


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
            "The cached Garmin session is no longer valid and there are no "
            "credentials to create a new one. " + CREDS_HINT + f" ({name}: {exc})"
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

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._client: Any = None
        self._source: str | None = None
        self._connected_at: float | None = None

    def _connect(self) -> Any:
        email, password = credentials()
        if not TOKEN_FILE.exists() and not (email and password):
            raise GarminAuthError("No cached Garmin session and no credentials. " + CREDS_HINT)

        client = build_client(prompt_mfa=_mfa_unavailable)
        before = _token_mtime()

        try:
            # garminconnect resumes from this file, refreshes the token when it
            # is close to expiry, falls back to the password, and rewrites the
            # file on success — all of which we would otherwise reimplement.
            client.login(tokenstore=str(TOKEN_FILE))
        except GarminMFARequired:
            raise
        except Exception as exc:
            raise login_error(exc, had_cache=before is not None) from exc

        after = _token_mtime()
        # A rewritten (or newly created) token file means the password was used;
        # an untouched one means the cached session was good.
        resumed = before is not None and after == before
        self._client = client
        self._source = "cached token" if resumed else "password login"
        self._connected_at = time.time()
        log.info("Garmin session established via %s", self._source)
        return client

    def reset(self) -> None:
        with self._lock:
            self._client = None
            self._source = None
            self._connected_at = None

    def client(self) -> Any:
        with self._lock:
            if self._client is None:
                self._connect()
            return self._client

    def run(self, fn: Callable[[Any], Any]) -> Any:
        """Run `fn` against a live client, retrying once through a fresh login.

        Garmin expires sessions server-side without warning, so one auth failure
        means "reauthenticate", not "report an error".
        """
        with self._lock:
            client = self.client()
            try:
                return fn(client)
            except _auth_error_types() as exc:
                if _is_mfa(exc):
                    raise GarminMFARequired(MFA_MESSAGE) from exc
                log.info("Call failed (%s); re-authenticating", type(exc).__name__)
                self.reset()
                client = self.client()
                return fn(client)

    def status(self) -> dict[str, Any]:
        """Describe the connection without touching the password or the token."""
        email, password = credentials()
        info: dict[str, Any] = {
            "authenticated": False,
            "account": mask_email(email),
            "credentials_present": {
                "GARMIN_EMAIL": bool(email),
                "GARMIN_PASSWORD": bool(password),
            },
            "token_cache": token_cache_info(),
        }
        try:
            with self._lock:
                client = self.client()
                # A cheap authenticated round-trip, so this reflects reality
                # rather than just what we cached in memory.
                profile = client.get_user_profile() or {}
            info["authenticated"] = True
            info["authenticated_via"] = self._source
            info["garmin_display_name"] = (
                getattr(client, "full_name", None)
                or getattr(client, "display_name", None)
                or profile.get("displayName")
            )
        except GarminError as exc:
            info["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"{type(exc).__name__}: {exc}"
        return info


session = GarminSession()
