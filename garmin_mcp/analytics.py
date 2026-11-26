"""The few product events the hosted server reports, with nothing personal in them.

The website already tells PostHog when someone copies the connector address.
That is the last thing a web page can see. Whether they then finished signing
in, and whether the connector was ever used, happens here, so the funnel had a
hole exactly where it mattered.

Three events close it: a sign-in succeeded, a sign-in failed (and roughly
why), and a connection was used for the first time. Each carries a handful of
flags and the masked form of the account's email (first letter and domain, as
the store keeps it), so the person running this can tell one friend's row from
another's in a report. Never the full address on an event, a token or any
Garmin data. The full account email sits in one place only, on the person's
profile, set by one $identify when they connect (Nov 14). The
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
from .bounded import BoundedDict

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
_LINKED: dict[tuple[str, str, str], float] = BoundedDict(20000)


def visitor_id(value: str | None) -> str | None:
    value = (value or "").strip()
    return value if _VISITOR.fullmatch(value) else None


def _identify(who: str, visitor: str | None = None, email: str | None = None) -> dict[str, Any] | None:
    """$identify for a person who has just connected (Nov 14).

    The account email goes on the person's PostHog profile, once, so Bart can
    tell who is who, and never on an event: events keep the masked form only.
    Where the browser came from daash.run, the same call joins the visit (its
    anonymous PostHog id) to the person, so started_setup and the sign-in are
    one person in a funnel. Only after a successful connection: a failed
    sign-in can carry a typo or somebody else's address.
    """
    email = (email or "").strip()
    if who == "anonymous" or (not visitor and not email) or visitor == who:
        return None
    now = time.time()
    key = (who, visitor or "", hashlib.sha256(email.lower().encode()).hexdigest()[:16] if email else "")
    if _LINKED.get(key, 0) > now - 86400:
        return None
    _LINKED[key] = now
    if len(_LINKED) > 5000:
        for pair, at in list(_LINKED.items()):
            if at < now - 86400:
                _LINKED.pop(pair, None)
    return {
        "api_key": KEY,
        "event": "$identify",
        "distinct_id": who,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "properties": {
            **({"$anon_distinct_id": visitor} if visitor else {}),
            **({"$set": {"email": email}} if email else {}),
            "$process_person_profile": True,
            "$lib": "garmin-mcp-hosted",
        },
    }


def capture(event: str, subject: str | None = None, properties: dict[str, Any] | None = None,
            *, visitor: str | None = None, email: str | None = None) -> None:
    """Record one event. Returns at once; the request goes out on its own thread.

    `visitor` is the website visitor's PostHog id, where this browser came
    from daash.run, and `email` the account email of someone who has just
    connected: both go on the person with one $identify, sent first in the
    same thread so it lands before the event. Neither is ever put on the event.
    """
    if not KEY:
        return
    who = distinct_id(subject)
    payload = _payload(event, subject, who, properties)
    link = _identify(who, visitor_id(visitor), email) if subject else None
    threading.Thread(target=_post_all, args=([link, payload] if link else [payload],), daemon=True).start()


def capture_now(event: str, subject: str | None = None, properties: dict[str, Any] | None = None) -> None:
    """Record one event and wait for it to go: for a process about to exit
    (the watchdog, Nov 24), where a thread of its own would die with it."""
    if not KEY:
        return
    _post(_payload(event, subject, distinct_id(subject), properties))


def capture_many(subject: str | None, events: list[tuple[str, dict[str, Any], float | None]]) -> None:
    """Record several events for one person, in order, from one thread.

    Each is (event, properties, when) with `when` a Unix time or None for now,
    kept to the millisecond: a trace's spans are drawn against each other.
    """
    if not KEY or not events:
        return
    who = distinct_id(subject)
    payloads = [_payload(event, subject, who, properties, when) for event, properties, when in events]
    threading.Thread(target=_post_all, args=(payloads,), daemon=True).start()


def _payload(event: str, subject: str | None, who: str, properties: dict[str, Any] | None,
             when: float | None = None) -> dict[str, Any]:
    friend = bool(subject) and subject in _friends()
    if who in internal_ids():
        properties = {**(properties or {}), "internal": True}
    if when is None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    else:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(when)) + f".{int(when % 1 * 1000):03d}Z"
    return {
        "api_key": KEY,
        "event": event,
        "distinct_id": who,
        "timestamp": stamp,
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


# Feature flags, asked of PostHog for one person and remembered an hour, so a
# card doesn't wait on PostHog each time it's drawn (Nov 15: the card button's
# wording, tested against "Open in Daash app"). Anything going wrong is the
# control: the experiment never stands between someone and their card.
_FLAGS: dict[tuple[str, str], tuple[float, str]] = BoundedDict(20000)
FLAG_MEMORY = 3600
FLAG_TIMEOUT = 1.5


def _fetch_flags(who: str) -> dict[str, Any]:
    """PostHog's flags for one distinct id (the tests put their own here)."""
    request = urllib.request.Request(
        f"{HOST}/flags/?v=2",
        data=json.dumps({"api_key": KEY, "distinct_id": who}).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=FLAG_TIMEOUT) as response:
        answer = json.loads(response.read() or b"{}")
    return answer if isinstance(answer, dict) else {}


def flag_variant(key: str, subject: str | None) -> str:
    """The variant of a multivariate flag for this person: "control" when off,
    unknown, or PostHog doesn't answer in time."""
    if not KEY or not subject:
        return "control"
    who = distinct_id(subject)
    now = time.monotonic()
    held = _FLAGS.get((key, who))
    if held and held[0] > now:
        return held[1]
    variant = "control"
    try:
        answer = _fetch_flags(who)
        flag = (answer.get("flags") or {}).get(key)
        if isinstance(flag, dict) and flag.get("enabled") and isinstance(flag.get("variant"), str):
            variant = flag["variant"]
        elif isinstance((answer.get("featureFlags") or {}).get(key), str):
            variant = answer["featureFlags"][key]
        memory = FLAG_MEMORY
    except Exception:  # noqa: BLE001 - the control, and asked again in five minutes
        log.debug("flag %s not read", key, exc_info=True)
        memory = 300
    if len(_FLAGS) > 20000:
        _FLAGS.clear()
    _FLAGS[(key, who)] = (now + memory, variant)
    return variant


def flag_on(key: str, subject: str | None) -> bool:
    """Whether an on/off flag is on for this person (Nov 26: matching version 2).

    Remembered an hour like a variant; off when PostHog doesn't answer.
    """
    if not KEY or not subject:
        return False
    who = distinct_id(subject)
    now = time.monotonic()
    held = _FLAGS.get(("on:" + key, who))
    if held and held[0] > now:
        return held[1] == "on"
    on = False
    try:
        answer = _fetch_flags(who)
        flag = (answer.get("flags") or {}).get(key)
        on = bool(flag.get("enabled")) if isinstance(flag, dict) else (answer.get("featureFlags") or {}).get(key) is True
        memory = FLAG_MEMORY
        # One answer has every flag (Nov 26: tools-v2 is read on every tool
        # call): the others are kept too, so the person's other on/off flags
        # this hour need no request of their own.
        for other, value in (answer.get("flags") or {}).items():
            if other != key and isinstance(value, dict):
                _FLAGS[("on:" + other, who)] = (now + memory, "on" if value.get("enabled") else "off")
    except Exception:  # noqa: BLE001 - off, and asked again in five minutes
        log.debug("flag %s not read", key, exc_info=True)
        memory = 300
    _FLAGS[("on:" + key, who)] = (now + memory, "on" if on else "off")
    return on


def known_variant(key: str, subject: str | None) -> str | None:
    """The variant already worked out for this person, without asking PostHog."""
    if not subject:
        return None
    held = _FLAGS.get((key, distinct_id(subject)))
    return held[1] if held else None


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
