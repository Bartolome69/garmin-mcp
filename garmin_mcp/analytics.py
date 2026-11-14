"""The few product events the hosted server reports, with nothing personal in them.

The website already tells PostHog when someone copies the connector address.
That is the last thing a web page can see. Whether they then finished signing
in, and whether the connector was ever used, happens here, so the funnel had a
hole exactly where it mattered.

Three events close it: a sign-in succeeded, a sign-in failed (and roughly
why), and a connection was used for the first time. Each carries a handful of
flags and the masked form of the account's email (first letter and domain, as
the store keeps it), so the person running this can tell one friend's row from
another's in a report. Never the full address, a token or any Garmin data. The
distinct id is an HMAC of the user's token under the server secret, and person
profiles are turned off so nothing accumulates against it, except for the few
of Bart's friends (named from a chat, or in GARMIN_MCP_FRIENDS), whose profile
only says friend.

Off unless GARMIN_MCP_POSTHOG_KEY is set, so a self-hosted copy reports to
nobody by default. Sending happens on a background thread and swallows every
error: analytics must never slow down or fail a sign-in.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

KEY = os.environ.get("GARMIN_MCP_POSTHOG_KEY", "").strip()
HOST = (
    os.environ.get("GARMIN_MCP_POSTHOG_HOST", "").strip().rstrip("/")
    or "https://eu.i.posthog.com"
)
_TIMEOUT = 5


def enabled() -> bool:
    return bool(KEY)


def distinct_id(subject: str | None) -> str:
    """One stable, unreadable id per person; a fixed one for nobody in particular.

    `subject` is the store's email fingerprint where the row has one, so a
    person who signs in again keeps the same id instead of counting twice; the
    token is the fallback for rows that predate the fingerprint. Either way it
    is HMAC'd again under the server secret with its own prefix, so the id can
    be recomputed from nothing PostHog holds and never collides with what the
    store keeps.
    """
    if not subject:
        return "anonymous"
    key = os.environ.get("GARMIN_MCP_SECRET", "").encode()
    return hmac.new(key, b"posthog:" + subject.encode(), hashlib.sha256).hexdigest()[:32]


_FRIENDS_TTL = 60
# "at" is None until first read: a machine up for less than the TTL (a fresh
# CI runner) would otherwise never read the list at all.
_FRIENDS: dict[str, Any] = {"at": None, "set": frozenset()}


def _friends() -> frozenset[str]:
    """Fingerprints of Bart's friends using Daash: those named from a chat, kept
    in the store, and any in GARMIN_MCP_FRIENDS. Read again each minute."""
    now = time.monotonic()
    if _FRIENDS["at"] is None or now - _FRIENDS["at"] > _FRIENDS_TTL:
        named: set[str] = set()
        try:
            from . import store  # here, not at the top: the store reports nothing itself
            named = {fingerprint for fingerprint, _ in store.friends()}
        except Exception:  # noqa: BLE001 - analytics must never fail what it reports on
            log.debug("friends unreadable", exc_info=True)
        key = os.environ.get("GARMIN_MCP_SECRET", "").encode()
        named |= {hmac.new(key, entry.strip().lower().encode(), hashlib.sha256).hexdigest()
                  for entry in os.environ.get("GARMIN_MCP_FRIENDS", "").split(",") if "@" in entry}
        _FRIENDS.update(at=now, set=frozenset(named))
    return _FRIENDS["set"]


def forget_friends() -> None:
    """Read the friends again at the next event, after the list has changed."""
    _FRIENDS["at"] = None


def internal_ids() -> frozenset[str]:
    """PostHog ids of Daash's own accounts (Bart's, test ones), from ANALYTICS_INTERNAL_IDS.

    Set as a secret on the server, never in the repo: the ids are kept out of
    it. Their events carry internal: true, so a count can leave them out
    without a list of ids pasted into every query (Nov 13).
    """
    return frozenset(i.strip() for i in os.environ.get("ANALYTICS_INTERNAL_IDS", "").split(",") if i.strip())


# A whole email address anywhere in a value. Nov 14: twenty events had been
# sending the full address alongside the masked one; nothing may now.
_ADDRESS = re.compile(r"[^\s@<>\"']+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


def _masked(match: re.Match[str]) -> str:
    local = match.group(0).split("@", 1)[0]
    return f"{local[:1]}{'*' * max(1, len(local) - 1)}@{match.group(1)}"


def _scrub(value: Any, key: str = "") -> Any:
    """No full email address reaches PostHog, whatever a caller passes.

    The masked form (first letter and domain, as the store keeps it) stays;
    a key named email goes altogether; an address inside any other text is
    masked where it stands.
    """
    if isinstance(value, dict):
        return {k: _scrub(v, k) for k, v in value.items() if k.lower() not in ("email", "$email")}
    if isinstance(value, (list, tuple)):
        return [_scrub(v, key) for v in value]
    if isinstance(value, str) and "@" in value:
        return _ADDRESS.sub(_masked, value)
    return value


# A website visitor's PostHog id, as posthog-js makes them: letters, digits
# and dashes. Anything else is not linked.
_VISITOR = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_LINKED: dict[tuple[str, str], float] = {}


def visitor_id(value: str | None) -> str | None:
    value = (value or "").strip()
    return value if _VISITOR.fullmatch(value) else None


def _identify(who: str, visitor: str) -> dict[str, Any] | None:
    """$identify joining a website visitor to the same person here (Nov 14).

    The website's started_setup is sent under the browser's anonymous PostHog
    id; everything here under an HMAC of the person's email fingerprint. One
    $identify with the browser's id as $anon_distinct_id makes them one person,
    so a funnel can run from the website to the watch. It needs a person
    profile, which is all it creates: no properties are set on it.
    """
    if who == "anonymous" or visitor == who:
        return None
    now = time.time()
    if _LINKED.get((who, visitor), 0) > now - 86400:
        return None
    _LINKED[(who, visitor)] = now
    if len(_LINKED) > 5000:
        for pair, at in list(_LINKED.items()):
            if at < now - 86400:
                _LINKED.pop(pair, None)
    return {
        "api_key": KEY,
        "event": "$identify",
        "distinct_id": who,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "properties": {"$anon_distinct_id": visitor, "$process_person_profile": True,
                       "$lib": "garmin-mcp-hosted"},
    }


def capture(event: str, subject: str | None = None, properties: dict[str, Any] | None = None,
            *, visitor: str | None = None) -> None:
    """Record one event. Returns at once; the request goes out on its own thread.

    `visitor` is the website visitor's PostHog id, where this browser came
    from daash.run: the person is joined to it first, in the same thread, so
    the join lands before the event does.
    """
    if not KEY:
        return
    friend = bool(subject) and subject in _friends()
    who = distinct_id(subject)
    if who in internal_ids():
        properties = {**(properties or {}), "internal": True}
    payload = {
        "api_key": KEY,
        "event": event,
        "distinct_id": who,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "properties": {
            **_scrub(properties or {}),
            # No person is built up from these; the id only lets uniq() work.
            # Bart's friends are the exception: a profile marked friend, so
            # the PostHog cohort of them fills itself.
            "$process_person_profile": friend,
            **({"$set": {"friend": True}} if friend else {}),
            "$lib": "garmin-mcp-hosted",
        },
    }
    link = _identify(who, visitor) if (visitor := visitor_id(visitor)) and subject else None
    threading.Thread(target=_post_all, args=([link, payload] if link else [payload],), daemon=True).start()


def _post_all(payloads: list[dict[str, Any]]) -> None:
    for payload in payloads:
        _post(payload)


def disconnected(person: str | None, via: str, login_route: str, what: str,
                 connected_at: int | None = None) -> None:
    """connector_disconnected (Nov 13): a link to someone's Garmin ended, however it ended.

    via: disconnect_page, app (deleted in the app), chat (the official link
    ended from the chat) or garmin (removed at Garmin's end). what: everything
    (the whole account went) or official_link (only Garmin's official link;
    a Garmin Connect sign-in may still stand behind the account).
    """
    days = round((time.time() - connected_at) / 86400, 1) if connected_at else None
    capture("connector_disconnected", person, {
        "via": via, "login_route": login_route, "what": what,
        **({"days_connected": days} if days is not None else {}),
    })


def _post(payload: dict[str, Any]) -> None:
    try:
        request = urllib.request.Request(
            f"{HOST}/i/v0/e/",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            response.read()
    except Exception:  # noqa: BLE001 - analytics never gets to break anything
        log.debug("analytics event not delivered", exc_info=True)
