"""Ask Garmin for someone's recent history when they connect through the official API.

Garmin's official API sends data as the watch syncs, from the moment someone
approves Daash, so a new account started empty: no runs for the plan card, no
month of nights for the recovery check to compare against. Garmin sends the
past too when asked for it, a window of time at a time ("backfill"). What it
sends arrives at the same push address as everything else and is kept the same
way (official.handle_push), so nothing else changes.

Asked newest first and the most useful kinds first, so last month's runs and
nights land in the first minute and the older months follow.

Garmin's rules for it sit in its developer portal rather than in public: each
request covers one window; a window asked for twice answers 409; one too wide
or too far back answers 400; and there is a budget of days asked for per
minute. So a window starts at CHUNK_DAYS and halves on a 400, and a one-day
window refused means Garmin has nothing that old, so that kind is done.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from . import official, store

log = logging.getLogger(__name__)

DAY = 86400
# What to ask for and how far back, most useful first. A kind can come back
# for a further stretch once the more useful ones are in.
PLAN: tuple[tuple[str, int], ...] = (
    ("activities", 30),
    ("activityDetails", 30),
    ("sleeps", 30),
    ("dailies", 30),
    ("hrv", 30),
    ("activities", 182),
    ("userMetrics", 182),
    ("stressDetails", 30),
    ("bodyComps", 182),
    ("activityDetails", 60),
)
CHUNK_DAYS = int(os.environ.get("GARMIN_BACKFILL_CHUNK_DAYS", "30"))
# Garmin's budget is days asked for per minute, across everyone.
DAYS_PER_MINUTE = float(os.environ.get("GARMIN_BACKFILL_DAYS_PER_MINUTE", "300"))
# Errors that aren't Garmin's answer (unreachable, 5xx) before an account is left be.
MAX_FAILS = 8
# A kind Garmin won't give: nothing that old, or not shared.
EXHAUSTED = -1
# Bumped when the asking changes, so accounts asked the old way are asked again.
VERSION = 2


def path(kind: str, start: int, end: int) -> str:
    return (f"/wellness-api/rest/backfill/{kind}?"
            + urllib.parse.urlencode({"summaryStartTimeInSeconds": start, "summaryEndTimeInSeconds": end}))


def ask_garmin(user_token: str, kind: str, start: int, end: int) -> tuple[int, str]:
    """Ask Garmin to send one window of one kind: its HTTP status (0 when unreachable) and its reason."""
    access_token, _ = official._access_token(user_token)
    request = urllib.request.Request(
        official.API + path(kind, start, end), method="GET",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json",
                 "User-Agent": "daash-official"},
    )
    try:
        official._send(request)
        return 202, ""
    except urllib.error.HTTPError as exc:
        return exc.code, official._oauth_reason(exc)
    except (urllib.error.URLError, OSError) as exc:
        return 0, type(exc).__name__


def _next(anchor: int, progress: dict) -> tuple[str, int, int] | None:
    """The next window to ask for: (kind, start, end), or None when everything's been asked."""
    for kind, days in PLAN:
        state = progress.get(kind) or {}
        reached = state.get("reached", anchor)
        oldest = anchor - days * DAY
        if reached == EXHAUSTED or reached <= oldest:
            continue
        chunk = max(1, int(state.get("chunk", CHUNK_DAYS)))
        return kind, max(oldest, reached - chunk * DAY), reached
    return None


def run_once(ask: Callable[[str, str, int, int], "int | tuple[int, str]"] = ask_garmin) -> dict | None:
    """Ask for one window for whoever has waited longest. None when nobody is waiting.

    Returns what happened: the kind, the window's days, Garmin's answer, and
    "wait" in seconds when Garmin asked Daash to slow down.
    """
    waiting = store.next_backfill()
    if waiting is None:
        return None
    garmin_user_id, anchor, progress = waiting
    if progress.get("v") != VERSION:
        progress = {"v": VERSION}
    link = store.official_link_for_garmin_user(garmin_user_id)
    window = _next(anchor, progress)
    if link is None or window is None:
        store.save_backfill(garmin_user_id, progress, True)
        return {"done": True}
    kind, start, end = window
    try:
        answer = ask(link.user_token, kind, start, end)
    except official.OfficialError as exc:  # the connection itself has lapsed
        log.info("history not asked for: %s", exc)
        store.save_backfill(garmin_user_id, progress, True)
        return {"done": True, "kind": kind}
    status, reason = answer if isinstance(answer, tuple) else (answer, "")
    # What Garmin last said about this kind, for the status tool: its rules aren't public.
    state = {**(progress.get(kind) or {}), "said": f"{status} {reason}".strip()[:160]}
    happened = {"kind": kind, "days": round((end - start) / DAY), "status": status}
    if status in (200, 202, 409):  # asked for, or asked for already
        state["reached"] = start
    elif status == 400:  # too wide, or older than Garmin keeps
        chunk = max(1, int(state.get("chunk", CHUNK_DAYS)))
        if chunk > 1:
            state["chunk"] = chunk // 2
        else:
            state["reached"] = EXHAUSTED
    elif status in (401, 403, 404, 412):  # not shared, or not offered to Daash's app
        state["reached"] = EXHAUSTED
        log.info("history for %s not available: %s %s", kind, status, reason)
    elif status == 429:
        happened["wait"] = 60
        store.save_backfill(garmin_user_id, {**progress, kind: state}, False, keep_turn=True)
        return happened  # nobody's turn moves on; try again in a minute
    else:  # unreachable or Garmin's own error: try later, then give up
        fails = int(progress.get("fails", 0)) + 1
        progress = {**progress, kind: state, "fails": fails}
        store.save_backfill(garmin_user_id, progress, fails >= MAX_FAILS)
        happened["wait"] = 30
        return happened
    progress = {**progress, kind: state}
    store.save_backfill(garmin_user_id, progress, _next(anchor, progress) is None)
    return happened


def summary(garmin_user_id: str) -> dict | None:
    """How far back each kind has been asked for, for the status tool."""
    found = store.backfill_state(garmin_user_id)
    if found is None:
        return None
    anchor, progress, done = found
    day = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t))  # noqa: E731
    asked = {}
    for kind in dict.fromkeys(k for k, _ in PLAN):
        state = progress.get(kind) or {}
        reached, said = state.get("reached"), state.get("said")
        if reached == EXHAUSTED:
            asked[kind] = "not available" + (f" (Garmin said {said})" if said else "")
        elif reached is not None:
            asked[kind] = f"back to {day(reached)}"
        elif said:
            asked[kind] = f"waiting (Garmin said {said})"
    return {"finished": done, "connected": day(anchor), "asked_for": asked}


def forever(ask: Callable[[str, str, int, int], int] = ask_garmin) -> None:
    """Work through everyone waiting, within Garmin's budget of days per minute."""
    try:
        store.reopen_backfills(VERSION)
        queued = store.queue_backfill_for_all_links()
        if queued:
            log.info("history to ask for: %d linked account(s)", queued)
    except Exception:  # noqa: BLE001
        log.exception("could not queue history for linked accounts")
    while True:
        try:
            happened = run_once(ask)
        except Exception:  # noqa: BLE001 - one bad account never stops the rest
            log.exception("history request failed")
            happened = {"wait": 60}
        if happened is None:
            time.sleep(15)
        elif "wait" in happened:
            time.sleep(happened["wait"])
        else:
            time.sleep(happened.get("days", 0) * 60 / DAYS_PER_MINUTE)
