"""Sign-ups on Garmin's own page, switched off on their own when they stop working (Nov 12).

On Nov 11 sign-ups were reopened on Garmin's page. Two new people got
through, then Garmin sent every new one straight back without approving
(its cap on new connections was still on), and nobody connected overnight:
sixteen people tried, and no one noticed until the morning.

So the server watches it itself. Each new sign-up sent to Garmin's page is
remembered until it comes back. It fails if Garmin sends it back without a
code, if finishing it fails, or if it hasn't come back within WAIT_SECONDS
(a phone that never returns). An approval resets the count. STREAK failures
in a row, with no approval between them, pause sign-ups on Garmin's page:
new sign-ups get the password form again, as with
GARMIN_OFFICIAL_SIGNUPS=paused, and the owner is emailed at once.

The pause holds until GARMIN_OFFICIAL_SIGNUPS is set to a different "open"
value (for example "open-2026-11-20") and deployed: a deliberate reopening,
never an unrelated deploy that happens to come next.

The state is kept in the database, so a restart or a deploy doesn't forget
a pause or the failures counted towards one. Sign-ups already linked to
Garmin (the "again" link) aren't counted: they say nothing about new ones.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from . import store

log = logging.getLogger(__name__)

STREAK = 3
WAIT_SECONDS = 15 * 60
# A pending sign-up older than this is dropped unread: the server was down, say.
FORGET_SECONDS = 24 * 3600
SETTING = "official_signup_guard"

_lock = threading.Lock()
# Told when sign-ups are paused: (why, the failures that led to it).
on_pause: Callable[[str, list[dict[str, Any]]], None] | None = None


def _load() -> dict[str, Any]:
    raw = store.get_setting(SETTING)
    try:
        state = json.loads(raw) if raw else {}
    except ValueError:
        state = {}
    state.setdefault("pending", {})
    state.setdefault("failures", [])
    return state


def _save(state: dict[str, Any]) -> None:
    store.set_setting(SETTING, json.dumps(state))


def paused(switch: str) -> bool:
    """Whether sign-ups on Garmin's page are paused under this switch's value."""
    return _load().get("paused_under") == switch


def state() -> dict[str, Any]:
    """For a status line: whether it's paused, since when, and the failures counted."""
    found = _load()
    return {"paused_under": found.get("paused_under"), "paused_at": found.get("paused_at"),
            "failures": len(found["failures"]), "waiting": len(found["pending"])}


def started(mark: str, switch: str, now: float | None = None) -> None:
    """A new sign-up has left for Garmin's page."""
    if not mark:
        return
    now = now or time.time()
    with _lock:
        found = _load()
        found["pending"][mark] = now
        _save(found)


def finished(mark: str, ok: bool, switch: str, why: str = "", now: float | None = None) -> bool:
    """A sign-up came back from Garmin's page. True if this paused sign-ups."""
    now = now or time.time()
    with _lock:
        found = _load()
        if found["pending"].pop(mark, None) is None:
            return False  # not one this guard sent: an "again" sign-up, or long ago
        if ok:
            found["failures"] = []
            _save(found)
            return False
        return _failed(found, why or "sent back without approval", now, switch)


def sweep(switch: str, now: float | None = None) -> bool:
    """Count sign-ups that never came back. True if this paused sign-ups."""
    now = now or time.time()
    with _lock:
        found = _load()
        paused_now = False
        for mark, at in list(found["pending"].items()):
            if now - at >= FORGET_SECONDS:
                found["pending"].pop(mark, None)
            elif now - at >= WAIT_SECONDS:
                found["pending"].pop(mark, None)
                paused_now = _failed(found, "never came back from Garmin's page", now, switch, save=False) or paused_now
        _save(found)
        return paused_now


def _failed(found: dict[str, Any], why: str, now: float, switch: str, save: bool = True) -> bool:
    found["failures"] = [*found["failures"], {"at": int(now), "why": why}][-STREAK * 4:]
    paused_now = False
    if len(found["failures"]) >= STREAK and found.get("paused_under") != switch:
        found["paused_under"] = switch
        found["paused_at"] = int(now)
        paused_now = True
    if save:
        _save(found)
    if paused_now:
        log.warning("sign-ups on Garmin's page paused after %d failures in a row", len(found["failures"]))
        if on_pause is not None:
            try:
                on_pause(why, list(found["failures"]))
            except Exception:  # noqa: BLE001 - the pause holds whether or not the alert went
                log.exception("sign-up pause alert failed")
    return paused_now


def reset() -> None:
    """Forget everything, for tests."""
    with _lock:
        store.set_setting(SETTING, "")
