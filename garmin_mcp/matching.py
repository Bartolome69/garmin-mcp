"""Matching runs to planned sessions, version 2: who has it, what they've said, and the watch (Nov 26).

progress.match_v2 decides; this keeps what it needs between reads, per person:

- choices: what the person said an activity was (a session, an extra run, or
  "none" for an automatic match undone), each with the history Undo walks back;
- pulled: sessions taken off the watch because a run done earlier counted for
  them, each with the calendar entry as it was, so Undo puts it back exactly;
- followups: what was chosen after a partial (leave it, add the rest, skip it),
  with the steps that undo an added distance;
- logged: which decisions have been sent to PostHog, so a read of the plan
  doesn't send them again.

Behind a flag: the PostHog feature flag match-v2, on first for Bart's own
account, or Daash's own accounts (ANALYTICS_INTERNAL_IDS). GARMIN_MCP_MATCH_V2
overrides it: "all" for everyone, "off" for no one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date
from typing import Any, Awaitable, Callable, Mapping

from . import analytics, store

log = logging.getLogger(__name__)

STATE_PREFIX = "match_v2:"
LOGGED_KEEP = 400
HISTORY_KEEP = 10


# -- who has it ----------------------------------------------------------------

FLAG = "match-v2"


def mode() -> str:
    found = os.environ.get("GARMIN_MCP_MATCH_V2", "flag").strip().lower()
    return found if found in ("flag", "all", "off") else "flag"


def subject_of(user_token: str | None) -> str | None:
    """The PostHog subject other events about this person use: their email fingerprint, else their token."""
    if not user_token or user_token == "local":
        return None
    user = store.get_user(user_token)
    if user is None:
        return None
    return user.email_hash or user_token


def enabled(user_token: str | None) -> bool:
    which = mode()
    if which == "off" or not user_token or user_token == "local":
        return False
    if which == "all":
        return True
    subject = subject_of(user_token)
    if not subject:
        return False
    return analytics.distinct_id(subject) in analytics.internal_ids() or analytics.flag_on(FLAG, subject)


# -- what's kept -----------------------------------------------------------------

def _key(user_token: str) -> str:
    return STATE_PREFIX + hashlib.sha256(user_token.encode()).hexdigest()[:32]


def load(user_token: str) -> dict[str, Any]:
    try:
        raw = json.loads(store.get_setting(_key(user_token)) or "{}")
    except (ValueError, TypeError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    for part in ("choices", "history", "pulled", "followups"):
        raw.setdefault(part, {})
    raw.setdefault("logged", [])
    raw.setdefault("fixed", [])
    return raw


def save(user_token: str, state: Mapping[str, Any]) -> None:
    state = dict(state)
    state["logged"] = list(state.get("logged") or [])[-LOGGED_KEEP:]
    store.set_setting(_key(user_token), json.dumps(state, separators=(",", ":"), default=str))


def context(user_token: str | None) -> dict[str, Any] | None:
    """What summarise needs for this person, or None when they don't have version 2."""
    if not enabled(user_token):
        return None
    state = load(user_token)
    choices = {aid: c["choice"] for aid, c in state["choices"].items() if isinstance(c, Mapping) and c.get("choice")}
    # A session off the watch because a run counted for it stays that run's.
    for sid, entry in state["pulled"].items():
        aid = str(entry.get("activity_id"))
        if aid and aid not in choices:
            choices[aid] = sid
    return {"user_token": user_token, "choices": choices, "fixed": list(state.get("fixed") or []),
            "followups": state["followups"], "pulled": state["pulled"]}


def off_watch_items(ctx: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The calendar entries taken off the watch, as they were, so the plan still shows them."""
    if not ctx:
        return []
    return [{**(entry.get("item") or {}), "id": int(sid) if str(sid).isdigit() else sid, "offWatch": True}
            for sid, entry in (ctx.get("pulled") or {}).items() if entry.get("item")]


# -- what the person says ------------------------------------------------------------

def choose(user_token: str, activity_id: Any, choice: str, *, was: str | None = None) -> dict[str, Any]:
    """Record a choice for an activity: a schedule id, "extra" or "none". Kept with what it replaced."""
    state = load(user_token)
    aid = str(activity_id)
    before = state["choices"].get(aid)
    history = list(state["history"].get(aid) or [])
    history.append({"choice": (before or {}).get("choice"), "auto": was})
    state["history"][aid] = history[-HISTORY_KEEP:]
    state["choices"][aid] = {"choice": str(choice)}
    save(user_token, state)
    return state


def undo(user_token: str, activity_id: Any, *, auto_schedule: str | None = None) -> str:
    """Undo the last thing said about an activity, or an automatic match.

    Returns what the activity now is: "auto" when it's back to being matched
    automatically, "none" when an automatic match was undone, or the earlier
    choice.
    """
    state = load(user_token)
    aid = str(activity_id)
    history = list(state["history"].get(aid) or [])
    if aid in state["choices"] and history:
        previous = history.pop()
        state["history"][aid] = history
        if previous.get("choice"):
            state["choices"][aid] = {"choice": previous["choice"]}
            now = previous["choice"]
        else:
            state["choices"].pop(aid, None)
            now = "auto"
    else:
        # An automatic match: back to how it was before it, the run counted for nothing.
        state["choices"][aid] = {"choice": "none"}
        state["history"][aid] = (history + [{"choice": None, "auto": auto_schedule}])[-HISTORY_KEEP:]
        now = "none"
    save(user_token, state)
    return now


def followup(user_token: str, schedule_id: Any, choice: str, undo_steps: list[Mapping[str, Any]] | None = None,
             onto: Any = None, add_km: float | None = None) -> None:
    state = load(user_token)
    state["followups"][str(schedule_id)] = {"choice": choice, **({"undo": list(undo_steps)} if undo_steps else {}),
                                            **({"onto": str(onto)} if onto else {}),
                                            **({"add_km": round(float(add_km), 1)} if add_km else {})}
    save(user_token, state)


def reopen(user_token: str, schedule_id: Any) -> dict[str, Any] | None:
    """Forget what was decided about a partial run's rest (Nov 27: Change reopens
    the decision). Returns what was decided, with its undo steps, or None."""
    state = load(user_token)
    was = state["followups"].pop(str(schedule_id), None)
    if was is not None:
        save(user_token, state)
    return was if isinstance(was, dict) else ({"choice": str(was)} if was else None)


# Why the plan view shows a line on a matched session (Nov 27). Same day, same
# type, nothing decided: the tick, the Over or Short tag and "7.8 of 12 km"
# say it all, so no line. Shared by the view and activity_matched, so the
# event counts exactly the lines people see.
LINE_REASONS = ("moved", "ambiguous", "extra", "user_decision")
MOVED_REASONS = ("missed_catchup", "forward_pull")


def line_reason(row: Mapping[str, Any]) -> str | None:
    """moved, ambiguous, extra or user_decision for a session or extra run; None when no line shows."""
    if row.get("state") == "extra" or row.get("matched") is not None and not row.get("match"):
        matched = row.get("matched")
        if row.get("question"):
            return None  # the question is its own box
        if matched in ("user_extra", "undone"):
            return "user_decision"
        return "extra" if matched in ("no_session", "too_short") else None
    m = row.get("match") or {}
    if not m:
        return None
    if row.get("followup") in ("leave", "add", "skip") or m.get("reason") == "user_choice":
        return "user_decision"
    if m.get("reason") in MOVED_REASONS:
        return "moved"
    if (m.get("candidates") or 1) > 1:
        return "ambiguous"
    return None


# -- after each read: the watch, and the record -----------------------------------------

def pulled_now(result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Sessions counted done by a run on an earlier day, still ahead of that run: off the watch."""
    found = {}
    for week in result.get("weeks") or []:
        for s in week.get("sessions") or []:
            if s.get("done_on") and s["done_on"] < s.get("date", "") and s.get("schedule_id") is not None:
                found[str(s["schedule_id"])] = s
    return found


async def sync_watch(ctx: Mapping[str, Any], result: Mapping[str, Any], items: list[Mapping[str, Any]],
                     today: date, call: Callable[[Callable[[Any], Any]], Awaitable[Any]]) -> int:
    """Take off the watch what a run already counted for; put back what no longer is. Returns writes made.

    Only sessions still to come are taken off: one whose day has passed is
    history, and changing it would change nothing on the watch. What's put
    back goes back to its own day, whatever that is now.
    """
    user_token = ctx["user_token"]
    state = load(user_token)
    now = pulled_now(result)
    by_id = {str(i.get("id")): i for i in items}
    writes = 0
    for sid, row in now.items():
        if sid in state["pulled"] or row.get("off_watch") or row.get("date", "") < today.isoformat():
            continue
        item = by_id.get(sid)
        if not item or not item.get("workoutId"):
            continue
        try:
            await call(lambda c, s=int(sid): c.unschedule_workout(s))
        except Exception:  # noqa: BLE001 - the plan still reads right; the watch catches up next time
            log.warning("match v2: couldn't take session %s off the watch", sid, exc_info=True)
            continue
        keep = {k: item.get(k) for k in ("date", "workoutId", "title", "workoutName", "sportTypeKey", "itemType")
                if item.get(k) is not None}
        state["pulled"][sid] = {"activity_id": row.get("activity_id"), "item": keep}
        writes += 1
        log.info("match v2: took %s (%s) off the watch, done early by activity %s",
                 sid, keep.get("date"), row.get("activity_id"))
    for sid, entry in list(state["pulled"].items()):
        if sid in now:
            continue
        item = entry.get("item") or {}
        try:
            placed = await call(lambda c, w=int(item["workoutId"]), d=str(item["date"])[:10]: c.schedule_workout(w, d))
        except Exception:  # noqa: BLE001 - kept in the list: the next read tries again
            log.warning("match v2: couldn't put session %s back on the watch", sid, exc_info=True)
            continue
        state["pulled"].pop(sid, None)
        writes += 1
        log.info("match v2: put %s back on the watch for %s (%s)", item.get("workoutId"), item.get("date"), placed)
    if writes:
        save(user_token, state)
    return writes


def record(ctx: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    """activity_matched and match_question_shown, once per decision, with the reason, in the log too."""
    user_token = ctx["user_token"]
    state = load(user_token)
    logged = list(state.get("logged") or [])
    seen = set(logged)
    subject = subject_of(user_token)
    account = (store.get_user(user_token).email_masked if store.get_user(user_token) else None)
    for week in result.get("weeks") or []:
        for s in week.get("sessions") or []:
            m = s.get("match")
            if not m:
                continue
            shown = line_reason(s)
            # A decision about the rest is a new line, so it's a new event (Nov 27).
            mark = (f"m:{m.get('activity_id')}:{s.get('schedule_id')}:{m.get('reason')}:{s.get('completion')}"
                    + (f":{s['followup']}" if s.get("followup") else ""))
            if mark in seen:
                continue
            seen.add(mark)
            logged.append(mark)
            log.info("match v2: activity %s -> session %s (%s), %s, ratio %s, moved %s day(s)",
                     m.get("activity_id"), s.get("schedule_id"), m.get("reason"), s.get("completion"),
                     m.get("ratio"), m.get("moved_days") or 0)
            if subject:
                analytics.capture("activity_matched", subject, {
                    "account": account, "reason": m.get("reason"), "completion": s.get("completion"),
                    "ratio": m.get("ratio"), "moved_days": m.get("moved_days") or 0,
                    "fixed_day": bool(s.get("fixed_day")), "session_type": s.get("type"),
                    "candidates": m.get("candidates") or 1, "line_shown": shown or "none"})
        for x in week.get("extra") or []:
            if x.get("question"):
                mark = f"q:{x.get('activity_id')}"
                if mark not in seen:
                    seen.add(mark)
                    logged.append(mark)
                    log.info("match v2: activity %s left unmatched, asking between %s",
                             x.get("activity_id"), [o.get("schedule") for o in x["question"]])
                    if subject:
                        analytics.capture("match_question_shown", subject, {
                            "account": account, "options": len(x["question"])})
            elif x.get("matched") in ("too_short", "no_session"):
                mark = f"x:{x.get('activity_id')}:{x.get('matched')}"
                if mark not in seen:
                    seen.add(mark)
                    logged.append(mark)
                    log.info("match v2: activity %s is an extra run (%s)", x.get("activity_id"), x.get("matched"))
                    if subject:
                        analytics.capture("activity_matched", subject, {
                            "account": account, "reason": x.get("matched"), "completion": "extra",
                            "ratio": None, "moved_days": 0, "line_shown": line_reason(x) or "none"})
    if len(logged) != len(state.get("logged") or []):
        state["logged"] = logged
        save(user_token, state)


def event(user_token: str | None, name: str, properties: Mapping[str, Any]) -> None:
    subject = subject_of(user_token)
    if not subject:
        return
    user = store.get_user(user_token)
    analytics.capture(name, subject, {"account": user.email_masked if user else None, **dict(properties)})
