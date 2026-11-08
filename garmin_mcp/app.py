"""The companion app: the web app's files and the JSON its screens read.

A third door onto the same plan, beside the MCP server and its cards. Every
answer here is built from the tool functions Claude calls, inside the same
per-person context SessionBinding sets up from a bearer token, and every
edit goes through the same Garmin writes. Nothing about the plan is kept
here: what the app changes is what Claude reads next, and what the watch
gets at its next sync. See APP.md.

There is no model behind any of it. Wording is by rule, plain, and never
medical advice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import analysis, crews, plan_checks, plan_engine, preview, store, training_plan
from . import server as server_mod
from . import share as share_mod
from .formatting import drop_empty
from .server import mcp

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent / "app"
API_PREFIX = "/app/api/"
# Signing in: no token yet, so SessionBinding lets these through unbound.
SIGN_IN_PREFIX = API_PREFIX + "sign-in/"

# What each session type is called and which colour draws it. The chat cards
# call a threshold session "Threshold" too, so the two doors agree.
TYPES: dict[str, tuple[str, str]] = {
    "easy": ("Easy", "easy"), "recovery": ("Recovery", "easy"), "long": ("Long", "long"),
    "threshold": ("Threshold", "hard"), "sub": ("Threshold", "hard"), "intervals": ("Intervals", "hard"),
    "race": ("Race", "race"), "strength": ("Strength", "strength"), "swim": ("Swim", "long"),
    "ride": ("Ride", "easy"), "other": ("Other", "rec"),
}
HARD_TYPES = {"threshold", "sub", "intervals", "race"}
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# Readiness below this is low: Garmin's own LOW band ends at 49.
LOW_READINESS = 50
# The line an easier copy carries, so undoing it needs nothing kept here.
EASIER_MARK = "Made easier in Daash from workout"
EASIER_RE = re.compile(re.escape(EASIER_MARK) + r" (\d+)\.")
# How much shorter Easier makes each rep, and a single main step.
EASIER_REP_SHARE = 2 / 3
EASIER_STEP_SHARE = 0.8


def _type(key: str | None) -> dict[str, str]:
    label, colour = TYPES.get(key or "", ("Run", "easy"))
    return {"type": key or "run", "type_label": label, "colour": colour}


def _day_of(value: Any) -> date | None:
    """A date from Garmin's date or date-and-time text, or None."""
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _short_date(d: date) -> str:
    return f"{d.strftime('%a')} {d.day} {MONTHS[d.month - 1]}"


def _display(name: str | None) -> str:
    """A session's name as the screens show it: no plan code, a real times sign."""
    text = training_plan.strip_tag(str(name or "Workout"))
    return re.sub(r"(?<=\d)\s*x\s*(?=\d)", " × ", text)


def _when(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return DAY_NAMES[d.weekday()] if 0 < (d - today).days < 7 else _short_date(d)


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _error(result: Any) -> str | None:
    if not isinstance(result, dict):
        return "No answer from Garmin."
    return result.get("error")


async def _workout(workout_id: Any) -> dict[str, Any] | None:
    if not workout_id:
        return None
    wid = int(workout_id)
    return await server_mod._read(("workout", wid), server_mod.WORKOUT_MEMORY,
                                  lambda c: c.get_workout_by_id(wid)) or None


def _note(workout: Mapping[str, Any] | None) -> str | None:
    """Why this session: the note its maker wrote, before the plan's own lines."""
    text = str((workout or {}).get("description") or "").strip()
    text = EASIER_RE.sub("", text).strip()
    head = re.split(r"\n\s*\nPlan [A-Z0-9]{2,8}:|^Plan [A-Z0-9]{2,8}:", text)[0].strip()
    return head or None


def _segments(shape: Any, kind: str | None) -> list[dict[str, Any]] | None:
    """The shape bar: easy, work and recovery, each by its share of the distance."""
    if not shape:
        return None
    out = []
    for seg in shape:
        k = seg.get("k")
        size = seg.get("km") or (seg.get("s") or 0) / 300
        if not size:
            continue
        # A race is red outside its warm-up and cool-down, however its steps are paced.
        if kind == "race" and k != "rec" and seg.get("part") not in ("wu", "cd"):
            colour = "race"
        else:
            colour = "rec" if k == "rec" else "hard" if k == "work" else ("long" if kind == "long" else "easy")
        # Raced or worked, the step stands tall; easy parts stay low and recoveries lower.
        out.append({"w": round(float(size), 2), "h": 20 if k == "work" or colour == "race" else 5 if k == "rec" else 10,
                    "colour": colour, "label": "" if k == "rec" else f"{float(seg.get('km') or 0):g} km"
                    if seg.get("km") else ""})
    return out or None


def _stats(totals: Mapping[str, Any] | None, kind: str | None, workout: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    totals = totals or {}
    if kind == "strength":
        steps = training_plan.workout_steps(workout) or []
        return [s for s in [
            {"v": str(totals["minutes"]), "u": "min", "l": "Time"} if totals.get("minutes") else None,
            {"v": str(len(steps)), "u": "", "l": "Exercises"} if steps else None,
        ] if s]
    out = []
    if totals.get("km"):
        out.append({"v": f"{totals['km']:g}", "u": "km", "l": "Total"})
    if totals.get("minutes"):
        out.append({"v": f"~{totals['minutes']}", "u": "min", "l": "Time"})
    rep = totals.get("rep")
    if rep:
        hard = kind in HARD_TYPES
        out.append({"v": str(rep["value"]).replace("/km", ""), "u": "",
                    "l": rep["label"] if hard else rep["label"].replace("Rep pace", "Pace").replace("Rep target", "Target")})
    return out


def _step_rows(steps: Any, kind: str | None, indent: int = 0) -> list[dict[str, Any]]:
    """The structure list: each step with its target and its length, repeats grouped."""
    rows: list[dict[str, Any]] = []
    for step in steps or []:
        if step.get("kind") == "repeat":
            rows.append({"label": f"{step['times']} ×", "sub": "Repeat", "head": True, "colour": None})
            rows.extend(_step_rows(step.get("steps"), kind, indent + 16))
            continue
        k = step.get("kind")
        if k in ("recovery", "rest"):
            colour = "rec"
        elif k in ("warmup", "cooldown"):
            colour = "easy"
        elif kind == "strength":
            colour = "strength"
        else:
            colour = ("race" if kind == "race" else "hard" if kind in HARD_TYPES
                      else "long" if kind == "long" else "easy")
        rows.append(drop_empty({
            "label": step.get("label") or "Run",
            "target": str(step["target"]).replace("/km", " /km") if step.get("target") else None,
            "sub": step.get("note") or ("Easy, recover" if k in ("recovery", "rest") and not step.get("target") else None),
            "right": step.get("amount") or "",
            "colour": colour,
            "indent": indent or None,
        }))
    return rows


def _ask(app: str, prompt: str) -> str:
    base = "https://claude.ai/new?q=" if app == "claude" else "https://chatgpt.com/?q="
    return base + quote(prompt, safe="")


def _ask_links(prompt: str) -> dict[str, str]:
    # claude_app opens the same new chat in Claude's desktop app, which takes
    # claude://claude.ai/<path> as that page; the app falls back to the web.
    return {"claude": _ask("claude", prompt), "chatgpt": _ask("chatgpt", prompt),
            "claude_app": "claude://claude.ai/new?q=" + quote(prompt, safe="")}


def assistant() -> str:
    """Claude or ChatGPT: whichever of the two the person used Daash from last, Claude if neither."""
    from . import oauth, official

    user_token = official.current()
    return (oauth.app_for_user(user_token) if user_token else None) or "Claude"


def app_url(base: str, route: str) -> str:
    """The app's screen for a route like session/2026-11-03, for "Open in Daash"."""
    return f"{base.rstrip('/')}/app/#/{route.lstrip('/')}"


# -- readiness -----------------------------------------------------------------

def _hours(text: Any) -> str | None:
    """7h 42m 00s as 7:42."""
    m = re.match(r"\s*(\d+)h\s*(\d+)m", str(text or ""))
    return f"{int(m.group(1))}:{int(m.group(2)):02d}" if m else None


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + "."


def _readiness(r: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(r, Mapping) or r.get("error"):
        return None
    score = (r.get("readiness") or {}).get("score")
    sleep = r.get("sleep") or {}
    rhr = r.get("resting_hr") or {}
    hrv = r.get("hrv") or {}
    if score is None and not sleep and not rhr:
        return None
    low = score is not None and score < LOW_READINESS
    up = (rhr.get("today") - rhr.get("seven_day_avg")) if rhr.get("today") and rhr.get("seven_day_avg") else None
    hrv_status = str(hrv.get("status") or "").lower()
    tiles = [
        {"l": "Readiness", "v": str(round(score)) if score is not None else "–",
         "note": ("Take it easier" if low else "Ready for hard work" if score >= 70 else "Fine for steady work")
         if score is not None else "Needs a night of sleep data"},
        {"l": "Sleep", "v": _hours(sleep.get("total")) or "–",
         "note": f"Score {sleep['score']}" if sleep.get("score") else "No score"},
        {"l": "Resting HR", "v": str(rhr["today"]) if rhr.get("today") else "–",
         "note": f"Up {up} on usual" if up and up >= 3 else "HRV in range" if hrv_status == "balanced"
         else f"HRV {hrv_status}" if hrv_status in ("low", "unbalanced", "poor")
         else "No HRV status yet" if hrv_status in ("none", "no_status") else "Usual for you"},
    ]
    reasons = []
    if sleep.get("score") and sleep["score"] < 60:
        reasons.append("Short night" if (_hours(sleep.get("total")) or "9")[0] < "7" else "Poor sleep")
    if up and up >= 3:
        reasons.append(f"resting HR up {up}")
    if hrv_status in ("unbalanced", "low", "poor"):
        reasons.append("HRV below range")
    if low:
        head, sub = "Readiness is low today", (_sentence(_join(reasons)) if reasons else "Garmin reads today as a low day.")
    elif score is not None:
        bits = [f"Slept {_hours(sleep.get('total'))}" if _hours(sleep.get("total")) else None,
                f"resting HR {rhr['today']}" if rhr.get("today") else None,
                "HRV in range" if hrv_status == "balanced" else None]
        head, sub = "Good to go", ", ".join(b for b in bits if b) + "."
    else:
        head, sub = "Readiness needs a night of sleep data", "Wear the watch overnight and it shows here."
    return {"score": round(score) if score is not None else None, "low": low, "head": head, "sub": sub,
            "tiles": tiles, "source": "From Garmin"}


# -- the plan ------------------------------------------------------------------

def _sessions(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [s for w in plan.get("weeks") or [] for s in w.get("sessions") or []]


def _next(plan: Mapping[str, Any], today: date) -> dict[str, Any] | None:
    ahead = sorted({s["date"] for s in _sessions(plan) if s.get("status") in ("today", "ahead") and s.get("date")})
    for day in ahead:
        lead, on_day = training_plan.day_sessions(plan, day)
        waiting = [s for s in on_day if s.get("status") in ("today", "ahead")]
        if waiting:
            return lead if lead in waiting else min(waiting, key=training_plan._rank)
    return None


def _headline(plan: Mapping[str, Any], today: date) -> tuple[str, str]:
    current = plan.get("current_plan") or {}
    kicker = _short_date(today).upper()
    season = _season(plan)
    n = _season_week(season, today - timedelta(days=today.weekday())) if season and plan.get("current_plan") else None
    if n:
        kicker += f" · WEEK {n} OF {season['total']}"
    goal = _goal_race(plan)
    goal = goal if goal and goal.get("date", "") >= today.isoformat() else None
    if goal:
        days = (date.fromisoformat(goal["date"]) - today).days
        name = str(goal.get("name") or "Race")
        name = name[:1].upper() + name[1:]
        return kicker, (f"{name} today" if days == 0 else f"{name} tomorrow" if days == 1 else f"{name} in {days} days")
    if n:
        return kicker, f"Week {n} of {season['total']}"
    return kicker, "This week"


def _week_strip(plan: Mapping[str, Any], today: date, next_day: str | None) -> dict[str, Any] | None:
    week = next((w for w in plan.get("weeks") or [] if w.get("current")), None)
    monday = today - timedelta(days=today.weekday())
    sessions = (week or {}).get("sessions") or []
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        here = [s for s in sessions if s.get("date") == d.isoformat()]
        lead = min(here, key=training_plan._rank) if here else None
        state = ("rest" if not here else "done" if all(str(s.get("status", "")).startswith("done") or
                                                        s.get("status") == "skipped" for s in here)
                 else "next" if d.isoformat() == next_day else "missed" if lead.get("status") == "missed" else "plan")
        days.append({
            "date": d.isoformat(), "letter": "MTWTFSS"[i], "n": d.day, "today": d == today, "state": state,
            "colour": _type(lead.get("type"))["colour"] if lead else None,
            "dots": [_type(s.get("type"))["colour"] for s in sorted(here, key=training_plan._rank)],
        })
    summary = plan.get("this_week") or {}
    return {"days": days, "done": summary.get("done"), "planned": summary.get("planned"),
            "unit": summary.get("unit") or "km", "verdict": _week_verdict(days, sessions)}


def _week_verdict(days: list[dict[str, Any]], sessions: list[dict[str, Any]]) -> str | None:
    """One line on the shape of the week, by rule: hard days, rest, and where the long run sits."""
    if not sessions:
        return None
    # A race is said by name, not folded into the hard days (Nov 6: a week with
    # a threshold session and a parkrun said "One hard day" and nothing else).
    races = sorted(((s["date"], str(s.get("name") or "")) for s in sessions if s.get("type") == "race"))
    race_days = {d for d, _ in races}
    hard = sorted({s["date"] for s in sessions
                   if s.get("type") in HARD_TYPES and s.get("type") != "race" and s["date"] not in race_days})
    rest = [DAY_NAMES[i] for i, d in enumerate(days) if d["state"] == "rest"]
    long_day = next((date.fromisoformat(s["date"]) for s in sessions if s.get("type") == "long"), None)
    parts = []
    if hard:
        parts.append(f"{_word(len(hard))} hard day{'s' if len(hard) > 1 else ''}")
    if races:
        named = [("a parkrun" if "parkrun" in n.lower() else "a race") + f" on {DAY_NAMES[date.fromisoformat(d).weekday()]}"
                 for d, n in races]
        parts.append(_join(named))
    if rest:
        parts.append(f"rest on {_join(rest)}")
    if long_day:
        before = [d for d in days if d["date"] < long_day.isoformat()][-2:]
        easy_before = all(d["state"] == "rest" or d["colour"] in ("easy", "strength") for d in before)
        parts.append(f"the long run on {DAY_NAMES[long_day.weekday()]}"
                     + (f" with {'two easy days' if len(before) == 2 else 'an easy day'} before it" if easy_before and before else ""))
    head = "No rest day this week." if not rest else "A heavy week." if len(hard) + len(races) >= 3 else "Balanced."
    if not parts:
        return head
    line = ", ".join(parts[:-1]) + (", and " if len(parts) > 2 else " and ") + parts[-1] if len(parts) > 1 else parts[0]
    return f"{head} {line[:1].upper() + line[1:]}."


def _word(n: int) -> str:
    return ["no", "one", "two", "three", "four", "five", "six", "seven"][n] if 0 <= n <= 7 else str(n)


def _crew_week(today: date) -> tuple[dict[str, Any], str | None] | None:
    """The crew's week and your name in it, or None for someone not in a crew."""
    if not crews.available() or not crews.in_crew():
        return None
    week = crews.week(today=today)
    return week, next((m["name"] for m in week.get("members") or [] if m.get("you")), None)


def _crew_line(crew: tuple[dict[str, Any], str | None] | None, today: date) -> dict[str, Any] | None:
    """Friends out on the same day as your next easy or long run. Never pace, HR or routes."""
    if not crew:
        return None
    week, me = crew
    for t in week.get("together") or []:
        others = [n for n in t.get("who") or [] if n != me]
        if not t.get("you_in") or not others or t["date"] < today.isoformat():
            continue
        d = date.fromisoformat(t["date"])
        kind = {"long": "Long runs", "easy": "Easy runs"}.get(t.get("kind"), "Easy and long runs")
        return {"line": f"{_join(others)} {'is' if len(others) == 1 else 'are'} out {_when(d, today) if d == today or d == today + timedelta(days=1) else 'on ' + _when(d, today)} too",
                "sub": f"{kind}, no start time yet", "faces": [n[:1].upper() for n in others[:3]], "date": t["date"]}
    return None


def _crew_days(crew: tuple[dict[str, Any], str | None] | None, today: date) -> dict[str, str]:
    """Each day you're running with friends, as the calendar says it: Tom and Priya out too."""
    if not crew:
        return {}
    week, me = crew
    out = {}
    for t in week.get("together") or []:
        others = [n for n in t.get("who") or [] if n != me]
        if t.get("you_in") and others and t["date"] >= today.isoformat():
            out[t["date"]] = f"{_join(others)} out too"
    return out


def _calendar(plan: Mapping[str, Any], today: date, crew_days: Mapping[str, str]) -> list[dict[str, Any]]:
    """Last week, this week and next, a cell a day, for the desktop calendar."""
    by_monday = {w["starts"]: w for w in plan.get("weeks") or [] if w.get("starts")}
    season = _season(plan)
    nxt = _next(plan, today)
    this_monday = today - timedelta(days=today.weekday())
    weeks = []
    for shift in (-1, 0, 1):
        monday = this_monday + timedelta(weeks=shift)
        week = by_monday.get(monday.isoformat()) or {}
        n = _season_week(season, monday)
        run, planned = round(week.get("run_km") or 0), round(week.get("planned_km") or 0)
        km = (f"{planned} km planned" if shift > 0 else f"{run} of {planned} km") if planned else (f"{run} km" if run else "")
        days = []
        for i in range(7):
            d = monday + timedelta(days=i)
            here = sorted((x for x in week.get("sessions") or [] if x.get("date") == d.isoformat()), key=training_plan._rank)
            chips = []
            for x in here:
                row = _row(x, today, (nxt or {}).get("schedule_id"))
                chips.append(drop_empty({
                    "name": row["name"], "colour": row["colour"], "state": row["state"], "missed": row.get("missed"),
                    "v": f"{row['v']} {row['u']}".strip() if row.get("v") else None,
                    "movable": row.get("movable"), "schedule_id": row.get("schedule_id"), "activity_id": row.get("activity_id"),
                }))
            days.append(drop_empty({"date": d.isoformat(), "n": d.day, "today": d == today or None, "past": d < today or None,
                                    "chips": chips, "crew": crew_days.get(d.isoformat())}))
        weeks.append(drop_empty({"label": f"Week {n}" if n else f"{monday.day} {MONTHS[monday.month - 1]}", "km": km or None,
                                 "current": shift == 0 or None, "past": shift < 0 or None, "days": days}))
    return weeks


async def _done_today(plan: Mapping[str, Any], today: date) -> dict[str, Any] | None:
    done = [s for s in _sessions(plan) if s.get("date") == today.isoformat()
            and str(s.get("status", "")).startswith("done") and s.get("activity_id")]
    if not done:
        return None
    s = min(done, key=training_plan._rank)
    aid = int(s["activity_id"])
    laps, workout, activity = await asyncio.gather(
        server_mod._call(lambda c: c.get_activity_splits(aid)), _workout(s.get("workout_id")),
        server_mod._call(lambda c: c.get_activity(aid)), return_exceptions=True)
    short = None
    if not isinstance(laps, Exception):
        result = analysis.analyse(None if isinstance(workout, Exception) else workout, laps, s.get("type"))
        short = result.get("short")
    started = str(((activity if isinstance(activity, dict) else {}).get("summaryDTO") or {}).get("startTimeLocal") or "")
    at = started[11:16] if len(started) >= 16 else None
    return drop_empty({"activity_id": aid, "name": _display(s.get("name")), "at": at,
                       "line": f"{_display(s.get('name'))} · {short}" if short else _display(s.get("name")),
                       "colour": _type(s.get("type"))["colour"]})


async def today_payload() -> dict[str, Any]:
    today = date.today()
    plan, readiness, full_name = await asyncio.gather(
        server_mod.get_plan(), server_mod.get_readiness(),
        server_mod._call(lambda c: getattr(c, "full_name", None)), return_exceptions=True)
    if isinstance(plan, Exception) or _error(plan):
        return {"error": str(plan) if isinstance(plan, Exception) else _error(plan)}
    ready = None if isinstance(readiness, Exception) else _readiness(readiness)
    await asyncio.to_thread(_keep_crew_current, plan, today)
    if plan.get("source") == "log":
        return {"today": today.isoformat(), "kicker": _short_date(today).upper(), "headline": "No plan on the calendar",
                "empty": True, "readiness": ready, "build": builder_on() or None,
                "ask": _ask_links("Use Daash to build me a training plan. Ask me my goal and race date first.")}
    kicker, headline = _headline(plan, today)
    nxt = _next(plan, today)
    card = None
    # The next session's workout and the run done today, at the same time.
    workout, done = await asyncio.gather(
        _workout((nxt or {}).get("workout_id")), _done_today(plan, today), return_exceptions=True)
    if isinstance(done, Exception):
        log.warning("today's run could not be read (%s)", type(done).__name__)
        done = None
    if nxt:
        if isinstance(workout, Exception):
            workout = None
        totals = training_plan.session_totals(workout, training_plan.easy_speed(plan)) if workout else None
        d = date.fromisoformat(nxt["date"])
        card = {
            "date": nxt["date"], "kicker": f"NEXT · {_when(d, today).upper()}", "name": _display(nxt.get("name")),
            **_type(nxt.get("type")), "stats": _stats(totals, nxt.get("type"), workout),
            "segments": _segments(nxt.get("shape"), nxt.get("type")), "on_watch": True,
            "schedule_id": nxt.get("schedule_id"),
            "easier": _easier_state(workout),
        }
    try:
        crew_week = _crew_week(today)
    except Exception:  # noqa: BLE001 - the crew never stops the morning glance
        log.exception("crew week failed")
        crew_week = None
    crew = _crew_line(crew_week, today)
    return drop_empty({
        "today": today.isoformat(), "kicker": kicker, "headline": headline, "done_today": done, "next": card,
        "crew": crew, "readiness": ready, "week": _week_strip(plan, today, (nxt or {}).get("date")),
        # The desktop's three weeks; the phone shows the strip above.
        "calendar": _calendar(plan, today, _crew_days(crew_week, today)),
        "initial": ((crew_week[1] if crew_week else None) or (full_name if isinstance(full_name, str) else "") or "Y")[:1].upper(),
        "assistant": assistant(),
        "ask": _ask_links(f"Use Daash to look at my training week of {today - timedelta(days=today.weekday())}. "
                          "How is it going, and is anything worth changing?"),
    })


# -- one session ---------------------------------------------------------------

def _easier_state(workout: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not workout:
        return None
    if EASIER_RE.search(str(workout.get("description") or "")):
        return {"applied": True, "label": "Undo easier"}
    made = easier(workout)
    if made is None:
        return None
    return {"applied": False, "label": "Easier", "preview": _display(made["workoutName"])}


async def _session_parts(day: str, schedule_id: int | None = None):
    today = date.today()
    plan = await server_mod.get_plan()
    if _error(plan):
        return None, None, None, None, _error(plan)
    lead, on_day = training_plan.day_sessions(plan, day)
    chosen = next((s for s in on_day if schedule_id and s.get("schedule_id") == schedule_id), lead)
    workout = await _workout((chosen or {}).get("workout_id"))
    return plan, chosen, on_day, workout, None


async def session_payload(day: str, schedule_id: int | None = None) -> dict[str, Any]:
    today = date.today()
    # Today's readiness asked for beside the plan, not after it.
    parts, readiness = await asyncio.gather(
        _session_parts(day, schedule_id),
        server_mod.get_readiness() if day == today.isoformat() else asyncio.sleep(0),
        return_exceptions=True)
    if isinstance(parts, BaseException):
        raise parts
    plan, chosen, on_day, workout, error = parts
    if error:
        return {"error": error}
    d = date.fromisoformat(day)
    if not chosen:
        following = _next(plan, d) if plan else None
        return drop_empty({"date": day, "rest": True, "kicker": _short_date(d).upper(),
                           "next": {"date": following["date"], "name": _display(following.get("name"))} if following else None})
    kind = chosen.get("type")
    status = str(chosen.get("status") or "")
    if status.startswith("done") and chosen.get("activity_id"):
        return {"date": day, "done": True, "activity_id": chosen["activity_id"]}
    totals = training_plan.session_totals(workout, training_plan.easy_speed(plan)) if workout else None
    card = training_plan.session_card(plan, day, today.isoformat(), workout)
    is_today = d == today
    ready = None
    if is_today and isinstance(readiness, dict):
        try:
            ready = _readiness(readiness)
        except Exception:  # noqa: BLE001
            ready = None
    t = _type(kind)
    easier_state = _easier_state(workout)
    name = _display(chosen.get("name"))
    prompt = (f"Use Daash to look at my {name} session on {day} (workout {chosen.get('workout_id')}). "
              "Talk me through it, and say if it should change.")
    return drop_empty({
        "date": day, "today": is_today, "status": status,
        "kicker": ("TODAY · " if is_today else "") + f"{_short_date(d).upper()} · {t['type_label'].upper()}",
        "name": name, **t,
        "km": (totals or {}).get("km"), "minutes": (totals or {}).get("minutes"),
        "estimated": (totals or {}).get("estimated"),
        "why": _note(workout),
        "segments": _segments(card.get("segments"), kind),
        "structure_title": "Exercises" if kind == "strength" else "Structure",
        "steps": _step_rows(card.get("steps"), kind),
        "readiness": ready,
        "watch": {"head": "On your watch",
                  "sub": "Changed. Ready to sync." if (easier_state or {}).get("applied")
                  else "Scheduled. Arrives at its next sync.",
                  "changed": bool((easier_state or {}).get("applied"))},
        "easier": easier_state,
        "workout_id": chosen.get("workout_id"), "schedule_id": chosen.get("schedule_id"),
        "also": [{"name": _display(s.get("name")), "schedule_id": s.get("schedule_id"), **_type(s.get("type"))}
                 for s in on_day if s is not chosen] or None,
        "missed": status == "missed" or None,
        "assistant": assistant(),
        "ask": _ask_links(prompt),
    })


async def run_payload(activity_id: int) -> dict[str, Any]:
    # Four reads at once, each asked for once (Nov 3: the chat's whole
    # get_activity_details, then the same laps and recording again, then the
    # activity, took 2-5 seconds). The plan is usually remembered from the
    # screen before, and its workouts with it.
    plan, laps, recording, activity = await asyncio.gather(
        server_mod.get_plan(),
        server_mod._call(lambda c: c.get_activity_splits(activity_id)),
        # The watch's recording, for kilometre splits that are each their own
        # pace. No route: thousands of points nothing here draws.
        server_mod._call(lambda c: c.get_activity_details(activity_id, 2000, 0)),
        server_mod._call(lambda c: c.get_activity(activity_id)), return_exceptions=True)
    if isinstance(laps, Exception):
        return {"error": f"The run's laps couldn't be read: {laps}"}
    plan = {} if isinstance(plan, Exception) or _error(plan) else plan
    recording = None if isinstance(recording, Exception) else recording
    activity = None if isinstance(activity, Exception) else activity
    row = next((s for s in _sessions(plan) if s.get("activity_id") == activity_id), None)
    try:
        workout = await _workout((row or {}).get("workout_id"))
    except Exception:  # noqa: BLE001 - the run reads without its plan
        workout = None
    details = server_mod.run_brief(activity or {}, laps, recording,
                                   share_mod.plan_context(plan, activity_id, workout))
    kind = (row or {}).get("type") or (details.get("summary") or {}).get("type")
    result = analysis.analyse(workout, laps, kind, recording)
    summary = details.get("summary") or {}
    started = None
    start_text = str(((activity or {}).get("summaryDTO") or {}).get("startTimeLocal") or "")
    if start_text:
        started = datetime.fromisoformat(start_text.replace(" ", "T").split(".")[0])
    t = _type(kind)
    _row_colours(result.get("planned_and_run") or [], t["colour"])
    name = _display((row or {}).get("name") or summary.get("name") or "Run")
    kicker = (f"{_short_date(started.date()).upper()} · {started.strftime('%H:%M')}" if started else "") + f" · {t['type_label'].upper()}"
    effect = None
    if row:
        today = date.today()
        nxt = _next(plan, today)
        status = str(row.get("status") or "")
        moved = status == "done, moved" and row.get("done_on")
        week = next((w for w in plan.get("weeks") or [] if row in (w.get("sessions") or [])), {})
        if week.get("current"):
            rest = "The rest of the week stays as planned." + (
                f" Next: {_when(date.fromisoformat(nxt['date']), today)}, {_display(nxt.get('name'))}." if nxt else "")
        else:
            rest = f"{week.get('done', 1)} of {week.get('planned', 1)} sessions done that week."
        effect = {
            "head": "Counts as done",
            "sub": (f"Run on {DAY_NAMES[date.fromisoformat(row['done_on']).weekday()]} instead of "
                    f"{DAY_NAMES[date.fromisoformat(row['date']).weekday()]}, and it still counts. " if moved else "") + rest,
        }
    else:
        effect = {"head": "Not on the plan", "sub": "It counts toward this week's distance. Nothing on the plan changes."}
    share = (details.get("share_image") or {}).get("url")
    return drop_empty({
        "activity_id": activity_id, "date": (row or {}).get("date"), "kicker": kicker.strip(" ·"),
        "name": name, **t, **{k: v for k, v in result.items() if k != "short"},
        "effect": effect, "share_url": share,
        "assistant": assistant(),
        "ask": _ask_links(f"Use Daash to look at my run {activity_id} ({name}). How did it go against the plan?"),
    })


def _row_colours(rows: list[dict[str, Any]], colour: str) -> None:
    """Warm-up and cool-down easy, reps in the session's colour; of a long run's
    parts, the steady ones long and the quicker ones (a goal-pace finish) hard."""
    parts = [r for r in rows if r.get("kind") == "interval" and not r.get("rep")]
    slowest = max((r.get("target") or "" for r in parts), default="")
    for r in rows:
        if r.get("kind") in ("warmup", "cooldown", "extra"):
            r["colour"] = "easy"
        elif r.get("rep") or colour != "long":
            r["colour"] = colour
        else:
            r["colour"] = "long" if len(parts) < 2 or (r.get("target") or "") == slowest else "hard"


# -- the plan: one week, and the block ------------------------------------------

# A past week within this share of its planned distance was run close to plan.
CLOSE_TO_PLAN = 0.85


def _season(plan: Mapping[str, Any]) -> dict[str, Any] | None:
    """The whole plan to race day: the block running now and the one after it, counted as one.

    A long build is often two blocks (a base block, then the build to the
    race), and the person thinks of it as one plan. Week numbers, the block
    chart and its phases run from the first block's start to the goal race.
    """
    current = plan.get("current_plan") or plan.get("upcoming_plan")
    first = _day_of((current or {}).get("starts"))
    if not first:
        return None
    following = plan.get("upcoming_plan") if plan.get("current_plan") else None
    goal = _goal_race(plan)
    last = max(d for d in (_day_of(current.get("ends")), _day_of((following or {}).get("ends")),
                           _day_of((goal or {}).get("date")), first) if d)
    first_monday = first - timedelta(days=first.weekday())
    total = ((last - timedelta(days=last.weekday())) - first_monday).days // 7 + 1
    return {"first": first, "first_monday": first_monday, "last": last, "total": total, "goal": goal,
            "blocks": [b for b in (current, following) if b]}


def _goal_race(plan: Mapping[str, Any]) -> dict[str, Any] | None:
    """The race the whole plan builds to: the goal among its races, or the next block's.

    The plan answer lists the races of the block running now; a marathon at
    the end of the block after it is only in that block's milestones.
    """
    goal = next((r for r in plan.get("races") or [] if r.get("tier") == "goal"), None)
    if goal:
        return goal
    for block in (plan.get("upcoming_plan"), plan.get("current_plan")):
        marked = [m for m in (block or {}).get("milestones") or [] if m.get("date")]
        found = next((m for m in marked if m.get("tier") == "goal"), None) or (max(marked, key=lambda m: m["date"]) if marked else None)
        if found:
            return {"name": found.get("label"), "date": found["date"], "tier": "goal", "target": found.get("target")}
    return None


def _season_week(season: Mapping[str, Any] | None, monday: date) -> int | None:
    """A week's number in the whole plan, counted from its first Monday."""
    if not season:
        return None
    n = (monday - season["first_monday"]).days // 7 + 1
    return n if 1 <= n <= season["total"] else None


async def _season_weeks(plan: Mapping[str, Any], season: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Every week of the plan by its Monday, read past the calendar's horizon where it has to be.

    The calendar answer reaches 16 weeks ahead. A plan to a race further
    off is read whole, block by block, the way Claude reads it by its code.
    """
    weeks = {w["starts"]: w for w in plan.get("weeks") or [] if w.get("starts")}
    if not season:
        return weeks
    last_monday = season["last"] - timedelta(days=season["last"].weekday())
    if weeks and max(weeks) >= last_monday.isoformat():
        return weeks
    labels = [b["label"] for b in season["blocks"] if b.get("label")]
    # Every block at once; one after another, each was a whole plan read's wait.
    found = await asyncio.gather(*(server_mod.get_plan(label) for label in labels), return_exceptions=True)
    for whole in found:
        if isinstance(whole, dict) and not _error(whole):
            for w in whole.get("weeks") or []:
                if w.get("starts"):
                    weeks.setdefault(w["starts"], w)
    return weeks


def _week_phase(week: Mapping[str, Any] | None) -> str | None:
    """The phase most of a week's sessions are in."""
    found = [x.get("phase") for x in (week or {}).get("sessions") or [] if x.get("phase")]
    return max(set(found), key=found.count) if found else None


def _range(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    return f"{monday.day} {MONTHS[monday.month - 1]} to {sunday.day} {MONTHS[sunday.month - 1]}"


def _row(s: Mapping[str, Any], today: date, next_id: Any) -> dict[str, Any]:
    d = date.fromisoformat(s["date"])
    status = str(s.get("status") or "")
    done = status.startswith("done")
    is_next = s.get("schedule_id") == next_id
    if done and s.get("done_on"):
        meta = f"Run on {DAY_NAMES[date.fromisoformat(s['done_on']).weekday()]}"
    elif done:
        meta = " · ".join(x for x in (s.get("actual_pace"), f"{s['actual_bpm']} bpm" if s.get("actual_bpm") else None) if x) or None
    elif is_next and d <= today + timedelta(days=1):
        meta = f"{'Today' if d == today else 'Tomorrow'} · on your watch"
    elif status == "missed":
        meta = "Not recorded"
    elif status == "skipped":
        meta = "Optional, skipped"
    else:
        meta = None
    strength = s.get("type") == "strength" or not (s.get("planned_km") or s.get("actual_km"))
    value = (s.get("actual_km") if done else s.get("planned_km"))
    return drop_empty({
        "date": s["date"], "name": _display(s.get("name")), **_type(s.get("type")),
        "state": "done" if done else "next" if is_next else status or "ahead",
        "meta": meta, "missed": status == "missed" or None,
        "segments": None if done else _segments(s.get("shape"), s.get("type")),
        "v": (f"{s['planned_min']}" if s.get("planned_min") else "") if strength else f"{round(float(value), 1):g}" if value else "",
        "u": "min" if strength and s.get("planned_min") else "" if strength else "km",
        "movable": (not done and d >= today) or None,
        "schedule_id": s.get("schedule_id"), "activity_id": s.get("activity_id") if done else None,
    })


def _extra_row(x: Mapping[str, Any]) -> dict[str, Any]:
    """A run or session not on the plan, as the week shows it: it counts in the week's total,
    so it's in the list too (Nov 6: a 1.2 km top-up was in the total and nowhere else)."""
    km = x.get("actual_km")
    return drop_empty({
        "date": x["date"], "name": _display(x.get("name")), "type": "extra", "type_label": "Extra", "colour": "rec",
        "state": "extra", "meta": " · ".join(v for v in ("Not on the plan", x.get("actual")) if v),
        "v": f"{round(float(km), 1):g}" if km else (f"{x['actual_min']}" if x.get("actual_min") else ""),
        "u": "km" if km else "min" if x.get("actual_min") else "",
        "activity_id": x.get("activity_id"),
    })


def _week_view(plan: Mapping[str, Any], weeks: Mapping[str, Mapping[str, Any]], season: Mapping[str, Any] | None,
               monday: date, today: date) -> dict[str, Any]:
    week = weeks.get(monday.isoformat()) or {}
    sessions = week.get("sessions") or []
    nxt = _next(plan, today)
    n = _season_week(season, monday)
    phase = _week_phase(week)
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        here = sorted((s for s in sessions if s.get("date") == d.isoformat()), key=training_plan._rank)
        extra = [x for x in week.get("extra") or [] if x.get("date") == d.isoformat()]
        days.append({
            "date": d.isoformat(), "dow": DAY_NAMES[i][:3].upper(), "n": d.day, "today": d == today,
            "past": d < today, "sessions": [_row(s, today, (nxt or {}).get("schedule_id")) for s in here]
            + [_extra_row(x) for x in extra],
        })
    run, planned = week.get("run_km"), week.get("planned_km")
    earlier, later = monday - timedelta(weeks=1), monday + timedelta(weeks=1)
    within = lambda m: m.isoformat() in weeks or _season_week(season, m) is not None  # noqa: E731
    return drop_empty({
        "starts": monday.isoformat(),
        "title": f"Week {n}" + (f" · {phase}" if phase else "") if n else f"Week of {monday.day} {MONTHS[monday.month - 1]}",
        "done": round(run) if run is not None else None, "planned": round(planned) if planned else None,
        "range": _range(monday), "current": monday == today - timedelta(days=today.weekday()) or None,
        "days": days,
        "previous": earlier.isoformat() if within(earlier) else None,
        "next": later.isoformat() if within(later) else None,
        "ask_next": _ask_links(f"Use Daash to plan next week, the week of {later.isoformat()}, "
                               "and adjust it to how this week has gone."),
        "ask": _ask_links(f"Use Daash to look at my training week of {monday.isoformat()}. "
                          "Is it balanced, and is anything worth changing?"),
    })


def _block_view(plan: Mapping[str, Any], weeks: Mapping[str, Mapping[str, Any]], season: Mapping[str, Any] | None,
                today: date) -> dict[str, Any] | None:
    """The whole plan a bar a week, to race day: planned against run, the phases, one line on how it's going."""
    if not season:
        return None
    this_monday = today - timedelta(days=today.weekday())
    bars, phase = [], None
    for n in range(1, season["total"] + 1):
        monday = season["first_monday"] + timedelta(weeks=n - 1)
        w = weeks.get(monday.isoformat()) or {}
        # A week with no sessions (a rest week, a week not read) stays in the phase before it.
        phase = _week_phase(w) or phase
        bars.append({"n": n, "starts": monday.isoformat(), "planned": round(w.get("planned_km") or 0, 1),
                     "run": round(w.get("run_km") or 0, 1), "current": monday == this_monday,
                     "past": monday < this_monday, "cutback": w.get("cutback") or None, "phase": phase})
    top = max((b["planned"] for b in bars), default=0) or 1
    peak = max(bars, key=lambda b: b["planned"])
    many = len(bars) > 16
    for b in bars:
        b["h"] = round(b["planned"] / top, 3)
        b["fill"] = min(1.0, round(b["run"] / b["planned"], 3)) if b["planned"] and (b["past"] or b["current"]) else 0
        b["label"] = str(round(b["planned"])) if b["current"] or b is peak else ""
        # On a long plan every fourth week is numbered, with the first, this week and the last.
        b["show_n"] = (not many or b["n"] == 1 or b["n"] % 4 == 0 or b["current"] or b["n"] == len(bars)) or None
    phases: list[dict[str, Any]] = []
    for b in bars:
        if not b["phase"]:
            continue
        if phases and phases[-1]["label"] == b["phase"] and phases[-1]["to"] == b["n"] - 1:
            phases[-1]["to"] = b["n"]
        else:
            phases.append({"label": b["phase"], "from": b["n"], "to": b["n"]})
    spans = []
    covered = 1
    for ph in phases:
        if ph["from"] > covered:
            spans.append({"label": "", "weeks": ph["from"] - covered})
        spans.append(drop_empty({"label": ph["label"], "weeks": ph["to"] - ph["from"] + 1,
                                 "current": any(b["current"] and ph["from"] <= b["n"] <= ph["to"] for b in bars) or None}))
        covered = ph["to"] + 1
    if phases and covered <= len(bars):
        spans.append({"label": "", "weeks": len(bars) - covered + 1})
    goal, blocks, first = season["goal"], season["blocks"], season["first"]
    name = blocks[0].get("name") or blocks[0].get("goal") or "plan"
    title_name = (f"Your plan to {goal['name']}" if goal and goal.get("name") and len(blocks) > 1
                  else f"Your {name[:1].lower() + name[1:]}" if not name[:2].isupper() else f"Your {name}")
    return drop_empty({
        "title": f"{len(bars)} weeks to race day" if goal else f"{len(bars)}-week plan",
        "bars": bars, "phases": spans or None,
        "verdict": _block_verdict(bars),
        "name": title_name,
        "dates": f"{'Started' if first <= today else 'Starts'} {_short_date(first)} · "
                 + (f"race {_short_date(_day_of(goal['date']))}" if goal and _day_of(goal.get("date"))
                    else f"ends {_short_date(season['last'])}"),
        # Two blocks: each named, with its dates, under the chart.
        "blocks": [drop_empty({"name": _capital(str(b.get("name") or b.get("goal") or b.get("label") or "Block")),
                               "dates": f"{_short_date(_day_of(b['starts']))} to {_short_date(_day_of(b['ends']))}"
                               if _day_of(b.get("starts")) and _day_of(b.get("ends")) else None})
                   for b in blocks] if len(blocks) > 1 else None,
        "ask": _ask_links("Use Daash to show my whole training plan to race day. I'd like to change it."),
    })


def _block_verdict(bars: list[dict[str, Any]]) -> str:
    past = [b for b in bars if b["past"] and b["planned"]]
    now = next((b for b in bars if b["current"]), None)
    if not past:
        head, first = "Early days.", "Nothing to compare with the plan yet"
    else:
        close = [b for b in past if b["run"] >= CLOSE_TO_PLAN * b["planned"]]
        n = len(past)
        head = "On track." if len(close) == n else "Mostly on track." if len(close) * 2 >= n else "Behind the plan so far."
        first = (f"{_word(n).capitalize()} week{'s' if n > 1 else ''} done close to plan" if len(close) == n
                 else f"{_word(len(close)).capitalize()} of {_word(n)} weeks done close to plan")
    remark = None
    if now:
        phase = now.get("phase")
        same = [b for b in bars if b.get("phase") == phase] if phase else []
        before = next((b for b in bars if b["n"] == now["n"] - 1), None)
        if now.get("cutback"):
            remark = "this week is a lighter one"
        elif phase and before and before.get("phase") != phase:
            remark = f"this week starts the {phase.lower()} phase"
        elif phase and same and now["planned"] >= max(b["planned"] for b in same):
            remark = f"this week is the biggest of the {phase.lower()} phase"
    return f"{head} {first}; {remark}." if remark else f"{head} {first}."


async def plan_payload(week_of: str | None = None) -> dict[str, Any]:
    today = date.today()
    plan = await server_mod.get_plan()
    if _error(plan):
        return {"error": _error(plan)}
    if plan.get("source") == "log":
        return {"empty": True, "ask": _ask_links("Use Daash to build me a training plan. Ask me my goal and race date first.")}
    anchor = _day_of(week_of) or today
    monday = anchor - timedelta(days=anchor.weekday())
    season = _season(plan)
    weeks = await _season_weeks(plan, season)
    block = _block_view(plan, weeks, season, today)
    return drop_empty({"today": today.isoformat(), "week": _week_view(plan, weeks, season, monday, today),
                       "block": block, "assistant": assistant(),
                       # With no block to race day: what Build a block asks.
                       "ask": None if block else _ask_links(
                           "Use Daash to build me a training block. Ask me my goal and race date first.")})


# -- the crew: who runs when, and the runs that line up -------------------------
# Days and times of easy and long runs only. Never pace, heart rate, routes
# or totals, and nothing here sets a time: that's agreed in the group chat.

SLOT_NAMES = ("morning", "afternoon", "evening")
_KIND_RUNS = {"long": "long runs", "easy": "easy runs", "mixed": "runs"}
_KIND_RUN = {"long": "Long run", "easy": "Easy run", "mixed": "Run"}
_KIND_LABEL = {"long": ("Long", "long"), "easy": ("Easy", "easy"), "mixed": ("Easy and long", "easy")}


def _with_you(names: list[str], me: str | None) -> list[str]:
    return (["You"] if me in names else []) + [n for n in names if n != me]


def _crew_cells(member: Mapping[str, Any], days: list[date], today: date,
                together: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    # Someone on Daash runs at their usual times, said in words on the crew's
    # card; someone added by hand picked the times of each run.
    words = [w.strip() for w in str(member.get("times") or "").split(",") if w.strip()]
    usual = [code for code, said in crews._TIME_WORDS.items() if said in words]
    cells = []
    for day, runs in zip(days, member.get("days") or []):
        segs = []
        for slot in SLOT_NAMES:
            # A run with no known time of day could be at any of them.
            here = next((r for r in runs if slot in (r.get("when") or crews._slot_for(day, usual) or SLOT_NAMES)), None)
            kind = (here or {}).get("kind")
            kind = "easy" if kind == "club" else kind
            match = bool(here) and kind != "hard" and any(
                t["date"] == day.isoformat() and (t.get("when") in (slot, None)) and member["name"] in t.get("who", [])
                for t in together)
            segs.append(drop_empty({"kind": kind, "match": match or None}))
        cells.append({"past": day < today or None, "segs": segs})
    return cells


def _keep_crew_current(plan: Any, today: date) -> None:
    """Your runs in the crew, from the plan just read, as a chat read of it keeps them."""
    member = crews.current()
    if member is None or not isinstance(plan, dict) or _error(plan):
        return
    try:
        crews.capture(member.key, plan, today)
    except Exception:  # noqa: BLE001 - the crew's copy is a convenience, never a reason to fail a screen
        log.exception("crew capture failed")


async def crew_payload(week: str | None = None) -> dict[str, Any]:
    """The crew's week in the app: this week, or next with week="next".

    A crew keeps the two (Nov 6: the app showed only this week, where the chat's
    card and the crew's page show either).
    """
    today = date.today()
    ahead = week == "next"
    if not crews.available() or not crews.in_crew():
        return {"none": True, "assistant": assistant(), "ask": _ask_links(
            "Use Daash to start a crew for me and the friends I run with, or to join one with the code a friend sent me.")}
    await asyncio.to_thread(_keep_crew_current, await server_mod.get_plan(), today)
    this_monday = today - timedelta(days=today.weekday())
    week = crews.week(this_monday + timedelta(weeks=1) if ahead else this_monday, today=today)
    days = [date.fromisoformat(week["week"]["starts"]) + timedelta(days=i) for i in range(7)]
    members = week.get("members") or []
    me = next((m["name"] for m in members if m.get("you")), None)
    colour = {m["name"]: i for i, m in enumerate(m for m in members if not m.get("you"))}
    together = [t for t in week.get("together") or [] if t["date"] >= today.isoformat()]

    def faces(names: list[str]) -> list[dict[str, Any]]:
        return [{"i": "Y" if n == me else n[:1].upper(), "you": n == me or None, "c": None if n == me else colour.get(n, 0) % 4}
                for n in sorted(names, key=lambda n: n != me)]

    def match(t: Mapping[str, Any]) -> dict[str, Any]:
        d = date.fromisoformat(t["date"])
        label, tone = _KIND_LABEL.get(t.get("kind"), _KIND_LABEL["mixed"])
        others = [n for n in t["who"] if n != me]
        return drop_empty({
            "date": t["date"], "dow": DAY_NAMES[d.weekday()][:3].upper(), "n": d.day,
            "slot": (t.get("when") or "").capitalize() or None, "type_label": label, "colour": tone,
            "tag": "Today" if d == today else "Tomorrow" if d == today + timedelta(days=1) else None,
            "names": _join(_with_you(t["who"], me)), "faces": faces(t["who"]), "you_in": t.get("you_in"), "_others": others,
        })

    main = next((t for t in together if t.get("you_in")), together[0] if together else None)
    crew = week.get("crew") or {}
    link = server_mod.crew_page_url(crew["link"]) if crew.get("link") else None
    hero = None
    if main:
        m = match(main)
        d = date.fromisoformat(main["date"])
        when = f"{DAY_NAMES[d.weekday()][:3]} {main['when']}" if main.get("when") else DAY_NAMES[d.weekday()]
        others = m.pop("_others")
        ask = (f"{_KIND_RUN.get(main.get('kind'), 'Run')} {when}? {_join(others)} {'is' if len(others) == 1 else 'are'} out too. What time?"
               if main.get("you_in") else f"{_join(others)} are out {when}. Anyone else?")
        ask += f"\n{link}" if link else (f"\nNew to the crew? Tell your Daash the code {crew['code']}." if crew.get("code") else "")
        hero = {**m, "kicker": f"{_short_date(d).upper()}" + (f" \u00b7 {main['when'].upper()}" if main.get("when") else ""),
                "line": f"{_word(len(main['who'])).capitalize()} {_KIND_RUNS.get(main.get('kind'), 'runs')} on the same "
                        f"{main['when'] if main.get('when') else 'day'}. Agree a start in the group chat.",
                "whatsapp": "https://wa.me/?text=" + quote(ask, safe="")}
    also = []
    for t in together:
        if t is main:
            continue
        m = match(t)
        m.pop("_others", None)
        also.append(m)
    people = [{"name": "You" if m.get("you") else m["name"], "you": m.get("you") or None, "by_hand": m.get("by_hand") or None,
               "c": None if m.get("you") else colour.get(m["name"], 0) % 4, "cells": _crew_cells(m, days, today, together)}
              for m in sorted(members, key=lambda m: not m.get("you"))]
    first, last = days[0], days[-1]
    return drop_empty({
        "kicker": f"{crew.get('name', 'Crew').upper()} \u00b7 {crew.get('members', len(members))} PEOPLE \u00b7 {_range(first).upper()}",
        "headline": (f"Run together {'next' if ahead else 'this'} week" if together
                     else "Nothing lines up next week yet" if ahead else "No runs line up yet"),
        "showing": "next" if ahead else "this",
        "hero": hero, "also": also or None,
        "days": [{"letter": "MTWTFSS"[i], "n": d.day, "today": d == today, "past": d < today} for i, d in enumerate(days)],
        "people": people,
        "invite": {"url": link, "shown": link.split("://", 1)[-1] if link else None, "code": crew.get("code")},
        "assistant": assistant(),
        "ask": _ask_links(f"Use Daash to show my crew {'next' if ahead else 'this'} week."),
    })


# -- You: the watch, the doors, the settings and the data ------------------------

SITE = "https://daash.run"
PRIVACY = "https://garmin.daash.run/privacy/"
USUAL_TIMES = [(code, said[:1].upper() + said[1:]) for code, said in crews._TIME_WORDS.items()]


def _synced(ms: Any, today: date) -> str | None:
    try:
        at = datetime.fromtimestamp(float(ms) / 1000)
    except (TypeError, ValueError, OSError):
        return None
    return f"Synced {at.strftime('%H:%M')}" + (" today" if at.date() == today else f", {_short_date(at.date())}")


def _times_said(words: Any) -> str | None:
    """weekday mornings, weekend mornings as Mornings; anything else as it is, capitalised."""
    picked = [code for code, said in crews._TIME_WORDS.items() if said in str(words or "")]
    if not picked:
        return None
    parts = []
    for slot in SLOT_NAMES:
        both = {f"weekday_{slot}", f"weekend_{slot}"}
        if both <= set(picked):
            parts.append(f"{slot}s")
            picked = [c for c in picked if c not in both]
    parts += [crews._TIME_WORDS[c] for c in picked]
    text = ", ".join(parts)
    return text[:1].upper() + text[1:]


async def you_payload() -> dict[str, Any]:
    from . import oauth, official

    today = date.today()
    user_token = official.current() or ""
    user = store.get_user(user_token)
    device, fitness, plan, miles, full_name = await asyncio.gather(
        server_mod._call(lambda c: c.get_device_last_used()), server_mod.get_fitness(),
        server_mod.get_plan(), server_mod._in_miles(),
        server_mod._call(lambda c: getattr(c, "full_name", None)), return_exceptions=True)
    device = device if isinstance(device, dict) else {}
    fitness = fitness if isinstance(fitness, dict) and not fitness.get("error") else {}
    plan = plan if isinstance(plan, dict) and not _error(plan) else {}

    member = None
    if crews.available() and crews.in_crew():
        week = crews.week(today=today)
        me = next((m for m in week.get("members") or [] if m.get("you")), {})
        member = {"crew": (week.get("crew") or {}).get("name"), "people": (week.get("crew") or {}).get("members"),
                  "name": me.get("name"), "times": me.get("times")}
    name = (member or {}).get("name") or (full_name if isinstance(full_name, str) else "")
    name = str(name).split(" ")[0] if name else (user.email_masked if user else "You")
    since = datetime.fromtimestamp(user.created_at) if user else None

    apps = {oauth.app_for_client(c) for c in store.clients_for(user_token)} if user_token else set()
    threshold = (fitness.get("lactate_threshold") or {})
    zones = ((fitness.get("zones") or {}).get("heart_rate") or {}).get("zones") or []
    race = _goal_race(plan) if plan else None
    race = race if race and race.get("date", "") >= today.isoformat() else None
    history = server_mod._history_link()
    email = store.email_for(user_token) if user_token else None

    def row(label, value=None, **extra):
        return drop_empty({"l": label, "v": value, **extra})

    groups = [
        ("Assistants", [
            row("Claude", "Connected" if "Claude" in apps else "Connect", tone="ok" if "Claude" in apps else "red",
                href=None if "Claude" in apps else SITE),
            row("ChatGPT", "Connected" if "ChatGPT" in apps else "Connect", tone="ok" if "ChatGPT" in apps else "red",
                href=None if "ChatGPT" in apps else SITE),
        ]),
        ("Training", [
            row("Units", "Miles" if miles is True else "km", sub="From Garmin"),
            row("Week starts", "Monday"),
            row("Usual run time", _times_said((member or {}).get("times")) or ("Not set" if member else "Set in a crew"),
                sub="Used on the crew page" if member else None, action="times" if member else None),
            row("Heart rate zones", "From Garmin",
                sub=", ".join(f"Z{z['zone']} {z['bpm'][0]}" for z in zones) + " bpm" if zones else None),
            row("Threshold pace", threshold.get("pace_per_km") or "Not measured yet",
                sub=f"From Garmin, updated {_short_date(_day_of(threshold['as_of']))}" if _day_of(threshold.get("as_of")) else "From Garmin"),
        ]),
        ("Goal race", [
            row(_capital(race.get("name") or "Goal race"), _short_date(_day_of(race["date"])),
                sub=f"Target {race['target']}" if race.get("target") else None,
                ask="change") if race else row("No goal race", "Set one", ask="set", tone="red"),
        ]),
        ("Crew", [
            row(member["crew"], f"{member['people']} people", href="#/crew",
                sub="They see days and times of your easy and long runs. Never pace, heart rate or routes.")
            if member else row("Not in a crew", "Start one", href="#/crew", tone="red"),
        ]),
        ("Your data", [
            row("Past runs", "Add from Garmin", href=history,
                sub="Garmin's official connection sends new runs only. Add earlier ones from Garmin Connect's export.")
            if history else row("Past runs", "Read from Garmin", sub="Daash reads them from Garmin when it needs them and keeps no copy."),
            row("Export", "Not yet", sub="Your runs are in Garmin Connect, which exports them."),
        ]),
        ("Account", [
            row("Email", email or (user.email_masked if user else None)),
            row("Sign out", action="signout"),
            row("Delete account", action="delete", danger=True,
                sub="Disconnects Garmin and deletes everything Daash keeps for you, crew included."),
        ]),
    ]
    goal_prompt = ("Use Daash to change my goal race." if race else "Use Daash to set my goal race and build a plan to it.")
    return drop_empty({
        "name": name, "initial": name[:1].upper() if name else "Y",
        "since": f"On Daash since {MONTHS[since.month - 1]}" + (f" {since.year}" if since.year != today.year else "") if since else None,
        "watch": {"name": device.get("lastUsedDeviceName") or "Your watch",
                  "synced": _synced(device.get("lastUsedDeviceUploadTime"), today),
                  "line": "Workouts go to your watch. Runs, sleep and readiness come back from Garmin."},
        "groups": [{"title": t, "rows": rows} for t, rows in groups],
        "times": {"options": [{"code": c, "label": l} for c, l in USUAL_TIMES],
                  "chosen": [c for c, said in crews._TIME_WORDS.items() if said in str((member or {}).get("times") or "")]} if member else None,
        "assistant": assistant(), "ask": _ask_links(goal_prompt),
        "privacy": PRIVACY,
    })


async def set_usual_times(times: Any) -> dict[str, Any]:
    crews.update(times=list(times or []))
    return {"message": "Saved. The crew sees your runs at these times."}


async def delete_account() -> dict[str, Any]:
    from . import hosted, official

    _track("app_account_deleted")
    await hosted.forget_person(official.current() or "")
    return {"message": "Deleted. Daash no longer has your Garmin session or anything kept for you."}


# -- edits ---------------------------------------------------------------------

def _scaled(steps: list[dict[str, Any]], share: float, only_repeats: bool) -> bool:
    """Shorten work steps by a share, in place. True if anything changed."""
    changed = False
    for step in steps:
        if step.get("type") == "RepeatGroupDTO" or step.get("numberOfIterations"):
            changed |= _scaled(step.get("workoutSteps") or [], share, False)
            continue
        if only_repeats:
            continue
        kind = ((step.get("stepType") or {}).get("stepTypeKey") or "").lower()
        cond = ((step.get("endCondition") or {}).get("conditionTypeKey") or "").lower()
        value = float(step.get("endConditionValue") or 0)
        if kind != "interval" or not value or cond not in ("distance", "time"):
            continue
        step["endConditionValue"] = (round(value * share / 100) * 100) if cond == "distance" else round(value * share / 15) * 15
        changed = changed or step["endConditionValue"] != value
    return changed


def easier(workout: Mapping[str, Any]) -> dict[str, Any] | None:
    """The same session, easier: each rep a third shorter, or the main step a fifth, same pace.

    None for anything it can't sensibly shorten (strength, a workout with no
    work steps set by distance or time).
    """
    sport = ((workout.get("sportType") or {}).get("sportTypeKey") or "running").lower()
    if sport not in ("running", "trail_running", "treadmill_running", "cycling", "road_biking"):
        return None
    made = json.loads(json.dumps(dict(workout)))
    made.pop("workoutId", None)
    steps = [s for seg in made.get("workoutSegments") or [] for s in seg.get("workoutSteps") or []]
    has_repeat = any(s.get("type") == "RepeatGroupDTO" or s.get("numberOfIterations") for s in steps)
    if has_repeat:
        changed = any(_scaled([s], EASIER_REP_SHARE, False) for s in steps
                      if s.get("type") == "RepeatGroupDTO" or s.get("numberOfIterations"))
    else:
        changed = _scaled(steps, EASIER_STEP_SHARE, False)
    if not changed:
        return None
    name = str(workout.get("workoutName") or "Workout")
    code = training_plan.label_of(name)
    bare = training_plan.strip_tag(name)
    first = next((s for s in _flat(steps) if ((s.get("stepType") or {}).get("stepTypeKey") or "") == "interval"
                  and ((s.get("endCondition") or {}).get("conditionTypeKey") or "") == "distance"), None)
    if first is not None:
        km = float(first["endConditionValue"]) / 1000
        bare = re.sub(r"(\d+\s*[x×]\s*)\d+(?:\.\d+)?\s*km", lambda m: f"{m.group(1)}{km:g} km", bare, count=1)
        if not has_repeat:
            total = sum(float(s.get("endConditionValue") or 0) for s in _flat(steps)
                        if ((s.get("endCondition") or {}).get("conditionTypeKey") or "") == "distance")
            bare = re.sub(r"\d+(?:\.\d+)?\s*km", f"{round(total / 100) / 10:g} km", bare, count=1)
    made["workoutName"] = training_plan.tagged(bare, code) if code else bare
    made["description"] = (str(workout.get("description") or "").rstrip()
                           + f"\n\n{EASIER_MARK} {workout.get('workoutId')}.").strip()
    for key in ("estimatedDurationInSecs", "estimatedDistanceInMeters", "workoutProvider", "createdDate", "updatedDate"):
        made.pop(key, None)
    for seg in made.get("workoutSegments") or []:
        _drop_ids(seg.get("workoutSteps") or [])
    return made


def _flat(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in steps:
        if s.get("type") == "RepeatGroupDTO" or s.get("numberOfIterations"):
            out.extend(_flat(s.get("workoutSteps") or []))
        else:
            out.append(s)
    return out


def _drop_ids(steps: list[dict[str, Any]]) -> None:
    for s in steps:
        s.pop("stepId", None)
        _drop_ids(s.get("workoutSteps") or [])


class EditError(Exception):
    pass


async def _chosen(day: str, schedule_id: int | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    plan, chosen, _, workout, error = await _session_parts(day, schedule_id)
    if error:
        raise EditError(error)
    if not chosen or not chosen.get("schedule_id"):
        raise EditError(f"Nothing is planned on {day}.")
    if str(chosen.get("status") or "").startswith("done"):
        raise EditError("That session is already done.")
    return chosen, workout


async def move(day: str, to: str, schedule_id: int | None = None) -> dict[str, Any]:
    """unschedule_workout then schedule_workout, as the plan's how_to_adapt says."""
    chosen, _ = await _chosen(day, schedule_id)
    target = date.fromisoformat(to)
    if target < date.today():
        raise EditError("Sessions move to today or later.")
    gone = await server_mod.unschedule_workout(schedule_id=chosen["schedule_id"])
    if _error(gone):
        raise EditError(_error(gone))
    placed = await server_mod.schedule_workout(chosen["workout_id"], target.isoformat())
    if _error(placed):
        raise EditError(_error(placed))
    return {"date": target.isoformat(), "schedule_id": placed.get("schedule_id"),
            "message": f"Moved to {_when(target, date.today())}. The watch follows at its next sync."}


async def make_easier(day: str, schedule_id: int | None = None) -> dict[str, Any]:
    """A copy of the workout for this one day, easier, in its place on the calendar.

    A plan's sessions share one workout across their dates (create_plan
    schedules the same workout each week), so changing it in place would
    change every week. The copy carries the plan code, so the plan still
    counts it, and a line naming the original, so Undo needs nothing kept here.
    """
    chosen, workout = await _chosen(day, schedule_id)
    if not workout:
        raise EditError("That session's workout couldn't be read.")
    if EASIER_RE.search(str(workout.get("description") or "")):
        raise EditError("It's already the easier version.")
    made = easier(workout)
    if made is None:
        raise EditError("This session has nothing to shorten.")
    uploaded = await server_mod._call(lambda c: c.upload_workout(made)) or {}
    new_id = uploaded.get("workoutId") or uploaded.get("id")
    if not new_id:
        raise EditError("Garmin returned no workout id.")
    await server_mod._call(lambda c: c.unschedule_workout(int(chosen["schedule_id"])))
    placed = await server_mod._call(lambda c: c.schedule_workout(int(new_id), day)) or {}
    server_mod.session.forget()
    return {"workout_id": new_id, "schedule_id": placed.get("workoutScheduleId") or placed.get("id"),
            "message": f"Now {_display(made['workoutName'])} at the same pace. Ready to sync."}


async def undo_easier(day: str, schedule_id: int | None = None) -> dict[str, Any]:
    chosen, workout = await _chosen(day, schedule_id)
    found = EASIER_RE.search(str((workout or {}).get("description") or ""))
    if not found:
        raise EditError("This session hasn't been made easier.")
    original = int(found.group(1))
    back = await server_mod._call(lambda c: c.get_workout_by_id(original)) or {}
    if not back.get("workoutName"):
        raise EditError("The original workout is no longer in Garmin.")
    await server_mod._call(lambda c: c.unschedule_workout(int(chosen["schedule_id"])))
    placed = await server_mod._call(lambda c: c.schedule_workout(original, day)) or {}
    try:
        await server_mod._call(lambda c: c.delete_workout(int(chosen["workout_id"])))
    except Exception:  # noqa: BLE001 - a copy left in the library is untidy, not wrong
        log.warning("couldn't delete the easier copy %s", chosen["workout_id"])
    server_mod.session.forget()
    return {"workout_id": original, "schedule_id": placed.get("workoutScheduleId") or placed.get("id"),
            "message": f"Back to {_display(back['workoutName'])}. Ready to sync."}


async def resend(day: str, schedule_id: int | None = None) -> dict[str, Any]:
    """Put the same workout on the same day again, which sends it to the watch afresh."""
    chosen, _ = await _chosen(day, schedule_id)
    await server_mod._call(lambda c: c.unschedule_workout(int(chosen["schedule_id"])))
    placed = await server_mod._call(lambda c: c.schedule_workout(int(chosen["workout_id"]), day)) or {}
    server_mod.session.forget()
    return {"schedule_id": placed.get("workoutScheduleId") or placed.get("id"),
            "message": "Sent. On your watch at its next sync."}


# -- routes --------------------------------------------------------------------

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".webmanifest": "application/manifest+json",
          ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8"}


# -- analytics: who uses the app, and for what --------------------------------
# Every event names the same person the connector's events do (their email
# fingerprint, the masked address, the address where one is kept), so app
# use sits beside Claude and ChatGPT use in PostHog. What a screen showed is
# never sent: no paces, no sessions, no plan.

APP_EVENTS = {"ask_clicked", "ask_chosen", "ask_chat_saved", "share_clicked", "signed_in", "installed", "viewed"}
# The screens a view can name: the app's own GET routes.
APP_SCREENS = {"today", "session", "run", "plan", "crew", "you"}


def _track(event: str, properties: Mapping[str, Any] | None = None, user_token: str | None = None) -> None:
    from . import analytics, official

    token = user_token or official.current()
    user = store.get_user(token) if token else None
    if user is None:
        analytics.capture(event, None, dict(properties or {}))
        return
    email = store.email_for(token)
    analytics.capture(event, user.email_hash or token, {
        "account": user.email_masked, **({"email": email} if email else {}), **dict(properties or {})})


def _layout(request: Request) -> str | None:
    found = request.headers.get("x-daash-layout", "")
    return found if found in ("phone", "desktop") else None


_FROM = re.compile(r"[a-z_]{1,24}")


def _came_from(request: Request) -> str | None:
    """Where the link that opened the app was given ("chat", "session_card"), on its first screen."""
    found = request.headers.get("x-daash-from", "")
    return found if _FROM.fullmatch(found) else None


def _safe(handler):
    """An app route that answers a readable error instead of crashing, and logs why."""
    import functools

    @functools.wraps(handler)
    async def answered(request: Request) -> Response:
        # Each screen opened, by name: today, session, run, plan, crew, you.
        screen = request.url.path[len(API_PREFIX):].split("/")[0]
        started = time.monotonic()
        try:
            response = await handler(request)
        except Exception:  # noqa: BLE001 - one screen's failure is reported, never a bare 500
            log.exception("app route %s failed", request.url.path)
            _track("app_failed", {"screen": screen, "layout": _layout(request)})
            return _json({"error": "Daash couldn't load this just now. Try again in a moment."}, 500)
        # A screen fetched ahead of a tap (the app warms the other tabs) isn't one opened.
        if request.method == "GET" and request.headers.get("x-daash-ahead") != "1":
            # How long the screen waited for Daash, and on its own thread: the
            # account lookups are reads a screen shouldn't wait for.
            from . import official
            seen = drop_empty({"screen": screen, "layout": _layout(request), "ok": response.status_code < 400,
                               "seconds": round(time.monotonic() - started, 2), "from": _came_from(request)})
            threading.Thread(target=_track, args=("app_viewed", seen, official.current()), daemon=True).start()
        return response

    return answered


def _json(body: Any, status: int = 200) -> JSONResponse:
    if isinstance(body, dict) and body.get("error") and status == 200:
        status = 502
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _day(request: Request) -> str | None:
    day = request.path_params.get("day", "")
    return day if _DAY_RE.match(day) else None


def _schedule_id(request: Request, body: Mapping[str, Any] | None = None) -> int | None:
    raw = (body or {}).get("schedule_id") or request.query_params.get("schedule_id")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


@mcp.custom_route(API_PREFIX + "today", methods=["GET"])
@_safe
async def api_today(request: Request) -> Response:
    return _json(await today_payload())


@mcp.custom_route(API_PREFIX + "session/{day}", methods=["GET"])
@_safe
async def api_session(request: Request) -> Response:
    day = _day(request)
    if not day:
        return _json({"error": "A date like 2026-11-03."}, 400)
    return _json(await session_payload(day, _schedule_id(request)))


@mcp.custom_route(API_PREFIX + "run/{activity_id}", methods=["GET"])
@_safe
async def api_run(request: Request) -> Response:
    try:
        activity_id = int(request.path_params["activity_id"])
    except (KeyError, ValueError):
        return _json({"error": "An activity id."}, 400)
    return _json(await run_payload(activity_id))


@mcp.custom_route(API_PREFIX + "plan", methods=["GET"])
@_safe
async def api_plan(request: Request) -> Response:
    week = request.query_params.get("week")
    return _json(await plan_payload(week if week and _DAY_RE.match(week) else None))


@mcp.custom_route(API_PREFIX + "crew", methods=["GET"])
@_safe
async def api_crew(request: Request) -> Response:
    try:
        return _json(await crew_payload("next" if request.query_params.get("week") == "next" else None))
    except crews.CrewError as exc:
        return _json({"error": str(exc)}, 409)


@mcp.custom_route(API_PREFIX + "you", methods=["GET"])
@_safe
async def api_you(request: Request) -> Response:
    return _json(await you_payload())


@mcp.custom_route(API_PREFIX + "you/times", methods=["POST"])
@_safe
async def api_times(request: Request) -> Response:
    try:
        body = await request.json()
        return _json(await set_usual_times((body or {}).get("times")))
    except (ValueError, crews.CrewError) as exc:
        return _json({"error": str(exc) or "Pick times of day."}, 400)


@mcp.custom_route(API_PREFIX + "you/delete", methods=["POST"])
@_safe
async def api_delete(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    # The word typed in the app, not a flag a stray request could set by accident.
    if str((body or {}).get("confirm") or "").strip().lower() != "delete":
        return _json({"error": "Type delete to confirm."}, 400)
    return _json(await delete_account())


@mcp.custom_route(API_PREFIX + "event", methods=["POST"])
@_safe
async def api_event(request: Request) -> Response:
    """The few things only the page sees: Ask Claude pressed, a run shared, signed in with Garmin, installed."""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    name = str((body or {}).get("name") or "")
    if name not in APP_EVENTS:
        return _json({"error": "Unknown event."}, 400)
    said = (body or {}).get("properties") or {}
    if name == "viewed":
        # A screen opened on a fetch made ahead of the tap, which the server
        # left uncounted: counted here, once, the same as any other view.
        screen = str(said.get("screen") or "")
        if screen not in APP_SCREENS:
            return _json({"error": "Unknown screen."}, 400)
        _track("app_viewed", drop_empty({"screen": screen, "layout": _layout(request),
                                         "ok": said.get("ok") is not False, "preloaded": True}))
        return _json({"ok": True})
    # choice: Back to my chat or New chat; saved: whether a chat was remembered.
    keep = {k: str(v)[:40] for k, v in said.items() if k in ("to", "opens", "where", "method", "choice", "saved")}
    _track(f"app_{name}", {**keep, "layout": _layout(request)})
    return _json({"ok": True})


_EDITS = {"move": move, "easier": make_easier, "undo": undo_easier, "resend": resend}


@mcp.custom_route(API_PREFIX + "session/{day}/{action}", methods=["POST"])
@_safe
async def api_edit(request: Request) -> Response:
    day, action = _day(request), request.path_params.get("action")
    if not day or action not in _EDITS:
        return _json({"error": "Unknown edit."}, 400)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        if action == "move":
            to = str(body.get("to") or "")
            if not _DAY_RE.match(to):
                return _json({"error": "Move needs a date to move to."}, 400)
            result = await move(day, to, _schedule_id(request, body))
        else:
            result = await _EDITS[action](day, _schedule_id(request, body))
    except EditError as exc:
        _track("app_edit", drop_empty({"action": action, "via": _via(body), "ok": False, "layout": _layout(request)}))
        return _json({"error": str(exc)}, 409)
    _track("app_edit", drop_empty({"action": action, "via": _via(body), "ok": True, "layout": _layout(request)}))
    return _json(result)


def _via(body: Mapping[str, Any]) -> str:
    """How an edit was made: a button, a drag on the calendar, or a held row on Plan."""
    via = str(body.get("via") or "button")
    return via if via in ("button", "drag", "hold") else "button"


# -- signing in without signing in to Garmin again -------------------------------
# Someone who already uses Daash is in two ways that don't ask for Garmin:
# the session card's Open in Daash link carries a single-use code, and a
# 6-digit code by email proves the address their account is under. Garmin's
# own sign-in, through /authorize, is for someone new.

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _tokens(subject: str) -> dict[str, Any]:
    from . import hosted, oauth

    issued = hosted.OAUTH_PROVIDER.issue_for_app(subject)
    return {"access_token": issued.access_token, "refresh_token": issued.refresh_token,
            "client_id": oauth.APP_CLIENT_ID}


@mcp.custom_route(SIGN_IN_PREFIX + "handoff", methods=["POST"])
async def sign_in_handoff(request: Request) -> Response:
    # Not paced like the email code: 32 random characters can't be guessed,
    # and someone tapping Open in Daash a few times shouldn't be locked out.
    try:
        code = str((await request.json() or {}).get("code") or "")
    except ValueError:
        code = ""
    subject = store.take_handoff(code) if code else None
    if not subject or store.get_user(subject) is None:
        _track("app_sign_in_failed", {"method": "chat link", "reason": "used or expired"})
        return _json({"error": "That link has been used or has expired."}, 401)
    _track("app_signed_in", {"method": "chat link"}, subject)
    return _json(_tokens(subject))


@mcp.custom_route(SIGN_IN_PREFIX + "email", methods=["POST"])
async def sign_in_email(request: Request) -> Response:
    """Send a code to an address with a Daash account. Says the same either way."""
    from . import hosted, support_mail

    try:
        email = str((await request.json() or {}).get("email") or "").strip()
    except ValueError:
        email = ""
    if not EMAIL_RE.match(email):
        return _json({"error": "That doesn't look like an email address."}, 400)
    if hosted._too_many_attempts(hosted._client_address(request)):
        return _json({"error": "Too many tries from here. Wait a few minutes."}, 429)
    said = {"message": "If that email has a Daash account, a 6-digit code is on its way."}
    fingerprint = store.email_fingerprint(email)
    if not store.tokens_for(fingerprint):
        _track("app_sign_in_failed", {"method": "email", "reason": "no account"})
        return _json(said)
    if not support_mail.can_send():
        return _json({"error": "Daash can't send codes just now. Sign in with Garmin instead."}, 503)
    code = store.new_email_code(fingerprint)
    if code is None:
        if store.code_wait(fingerprint) == "day":
            return _json({"error": "That address has had its codes for today. Sign in with Garmin instead."}, 429)
        return _json({"message": "A code went out under a minute ago. Use that one."})
    try:
        await asyncio.to_thread(support_mail.send_code, email, code)
    except (OSError, KeyError):
        log.warning("app sign-in code not sent")
        return _json({"error": "Daash couldn't send the code just now. Try again in a minute."}, 502)
    _track("app_email_code_sent", {}, store.tokens_for(fingerprint)[-1])
    return _json(said)


@mcp.custom_route(SIGN_IN_PREFIX + "verify", methods=["POST"])
async def sign_in_verify(request: Request) -> Response:
    from . import hosted

    if hosted._too_many_attempts(hosted._client_address(request)):
        return _json({"error": "Too many tries from here. Wait a few minutes."}, 429)
    try:
        body = await request.json() or {}
    except ValueError:
        body = {}
    email, code = str(body.get("email") or "").strip(), re.sub(r"\D", "", str(body.get("code") or ""))
    fingerprint = store.email_fingerprint(email) if email else ""
    checked = store.check_email_code(fingerprint, code) if fingerprint and code else "missing"
    if checked == "wrong":
        _track("app_sign_in_failed", {"method": "email", "reason": "wrong code"})
        return _json({"error": "That code isn't right. Check the latest email."}, 401)
    if checked != "ok":
        return _json({"error": "That code has expired or had too many tries. Send a new one."}, 401)
    accounts = store.tokens_for(fingerprint)
    if not accounts:
        return _json({"error": "That email has no Daash account. Sign in with Garmin instead."}, 401)
    _track("app_signed_in", {"method": "email"}, accounts[-1])
    return _json(_tokens(accounts[-1]))


# --------------------------------------------------------------------------
# The plan builder (docs/plan-engine-spec.md): a running plan from six
# answers, with no chat and no model calls, written by create_plan like any
# other. Preview accounts first; GARMIN_MCP_BUILDER=1 opens it to everyone.
# --------------------------------------------------------------------------

BUILDER_ENV = "GARMIN_MCP_BUILDER"
# Garmin's race predictions by the builder's distance names.
_PREDICTED = {"5k": "5k", "10k": "10k", "half_marathon": "half", "marathon": "marathon"}


def builder_on() -> bool:
    return preview.enabled() or os.environ.get(BUILDER_ENV, "").strip().lower() in ("1", "on", "true", "yes")


def _goal_for_km(km: Any) -> str | None:
    try:
        km = float(km)
    except (TypeError, ValueError):
        return None
    for goal, (dist, lo, hi) in {"5k": (5, 4.5, 5.6), "10k": (10, 9, 11), "half": (21.1, 20, 22.5),
                                 "marathon": (42.2, 40, 44)}.items():
        if lo <= km <= hi:
            return goal
    return None


async def build_start_payload() -> dict[str, Any]:
    """What step 1 and step 2 start from: what Garmin knows, and the races on the calendar."""
    today = date.today()
    monday = training_plan.monday_of(today)

    async def history():
        return await server_mod._call(lambda c: c.get_activities_by_date(
            (monday - timedelta(weeks=training_plan.LOG_WEEKS)).isoformat(), today.isoformat())) or []

    activities, fitness, months = await asyncio.gather(
        history(), server_mod.get_fitness(),
        server_mod._calendar_months(today, today + timedelta(weeks=training_plan.RACE_WEEKS_AHEAD)),
        return_exceptions=True)
    weeks = training_plan.weekly_run_km(activities if isinstance(activities, list) else [], monday,
                                        training_plan.LOG_WEEKS)
    running = sum(1 for w in weeks if w["runs"])
    enough = running >= plan_checks.HISTORY_MIN_WEEKS
    weekly = round(sum(w["run_km"] for w in weeks) / max(1, len(weeks)), 1) if enough else None
    longest = plan_checks.longest_run_km(activities) if enough and isinstance(activities, list) else None
    predictions, threshold, threshold_hr = {}, None, None
    if isinstance(fitness, dict) and "error" not in fitness:
        for key, name in _PREDICTED.items():
            got = (fitness.get("race_predictions") or {}).get(key) or {}
            if got.get("time"):
                predictions[name] = got["time"]
        lt = fitness.get("lactate_threshold") or {}
        threshold, threshold_hr = lt.get("pace_per_km"), lt.get("heart_rate_bpm")
    races = []
    if isinstance(months, list):
        for e in training_plan.calendar_events(months, today + timedelta(days=1),
                                               today + timedelta(weeks=training_plan.RACE_WEEKS_AHEAD)):
            races.append(drop_empty({"name": e["name"], "date": e["date"], "goal": _goal_for_km(e.get("distance_km")),
                                     "primary": e.get("primary")}))
    return drop_empty({
        "today": today.isoformat(),
        "weekly_km": weekly, "longest_km": longest, "enough_history": enough,
        "level": plan_engine.level_guess(weekly or 0, running, longest or 0) if enough else None,
        "predictions": predictions or None, "threshold_pace": threshold,
        "threshold_hr": threshold_hr, "races": races,
        "existing": await _plans_between(today, today + timedelta(weeks=plan_engine.MAX_WEEKS)) or None,
    })


async def _plans_between(first: date, last: date) -> list[str]:
    """The plan codes with sessions on the calendar between two days."""
    items = await server_mod._plan_calendar(first, last)
    inside = [i for i in items if first.isoformat() <= str(i.get("date") or "")[:10] <= last.isoformat()]
    return sorted(training_plan.plans_in(inside))


def _answers(body: Any) -> plan_engine.Answers:
    if not isinstance(body, dict):
        raise plan_engine.BuildError("Those answers didn't come through. Try again.")
    return plan_engine.Answers.from_json(body, date.today())


def build_options(body: Any) -> dict[str, Any]:
    """Step 4's three volumes, and how long the plan will be."""
    a = _answers(body)
    start, weeks, notes = plan_engine._weeks(a)
    opts = plan_engine.peak_options(a, weeks)
    return drop_empty({"weeks": weeks, "starts": start.isoformat(),
                       "starts_words": f"{DAY_NAMES[start.weekday()]} {start.day} {plan_engine.MONTHS[start.month - 1]}",
                       "options": opts, "notes": notes or None})


def build_preview(body: Any) -> dict[str, Any]:
    """The plan as it would be written, with the checker's notes. Nothing is written."""
    a = _answers(body)
    built = plan_engine.build(a)
    plan = built["plan"]
    notes: list[dict[str, Any]] = []
    try:
        _, prep = training_plan.prepare(plan["goal"], plan["sessions"], plan["label"], date.today(),
                                        name=plan["name"], method=plan["method"], milestones=plan["milestones"])
        history = ([{"starts": (training_plan.monday_of(date.today()) - timedelta(weeks=1)).isoformat(),
                     "run_km": a.weekly_km, "runs": 0}] if a.weekly_km else None)
        notes = plan_checks.check_prepared(prep, goal=plan["goal"], method=plan["method"], milestones=plan["milestones"],
                                           history=history, longest_recent_km=a.longest_km or None)
    except Exception:  # noqa: BLE001 - the preview stands without its notes
        log.warning("builder preview checks failed", exc_info=True)
    return {**built["preview"], "goal": plan["goal"], "name": plan["name"], "method": plan["method"],
            "label": plan["label"], "checks": [n["text"] for n in notes]}


async def build_plan(body: Any) -> dict[str, Any]:
    """Put the built plan on the Garmin calendar, through create_plan."""
    a = _answers(body)
    plan = plan_engine.build(a)["plan"]
    first = date.fromisoformat(plan["sessions"][0]["date"])
    last = date.fromisoformat(plan["sessions"][-1]["date"])
    # Never a second plan over the same weeks: a double tap, or a plan already
    # running, would leave two sessions on a day and the cards reading both.
    there = await _plans_between(first, last)
    if there:
        raise EditError(f"Your Garmin calendar already has a plan ({', '.join(there)}) in those weeks. "
                        "Remove it first, then build this one.")
    result = await server_mod.create_plan(**plan)
    if _error(result):
        raise EditError(_error(result))
    first = plan["sessions"][0]["date"]
    return {"label": plan["label"], "first": first, "sessions": len(plan["sessions"]),
            "message": f"Your plan is on your Garmin calendar: {len(plan['sessions'])} sessions, "
                       f"starting {_when(date.fromisoformat(first), date.today())}. It reaches your watch at its next sync."}


@mcp.custom_route(API_PREFIX + "build", methods=["GET", "POST"])
@_safe
async def api_build(request: Request) -> Response:
    if not builder_on():
        return _json({"error": "The plan builder isn't open yet."}, 404)
    if request.method == "GET":
        return _json(await build_start_payload())
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    try:
        result = await build_plan(body)
    except (plan_engine.BuildError, EditError) as exc:
        _track("plan_built", drop_empty({"ok": False, "why": str(exc)[:120], "layout": _layout(request)}))
        return _json({"error": str(exc)}, 400)
    a = _answers(body)
    _track("plan_built", drop_empty({"ok": True, "goal": a.goal, "days": a.days, "level": a.level,
                                     "volume": a.volume, "style": a.style, "injury": a.injury,
                                     "sessions": result["sessions"], "layout": _layout(request)}))
    return _json(result)


@mcp.custom_route(API_PREFIX + "build/{step}", methods=["POST"])
@_safe
async def api_build_step(request: Request) -> Response:
    if not builder_on():
        return _json({"error": "The plan builder isn't open yet."}, 404)
    step = request.path_params.get("step")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    try:
        if step == "options":
            return _json(build_options(body))
        if step == "preview":
            return _json(build_preview(body))
    except plan_engine.BuildError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"error": "Not found"}, 404)


# The web app's own files, registered last so the API's routes match first.

@mcp.custom_route("/app", methods=["GET"])
async def app_index(request: Request) -> Response:
    return _file("index.html")


@mcp.custom_route("/app/callback", methods=["GET"])
async def app_callback(request: Request) -> Response:
    return _file("index.html")


@mcp.custom_route("/app/{path:path}", methods=["GET"])
async def app_file(request: Request) -> Response:
    path = request.path_params.get("path", "")
    if path.startswith("api/"):
        return _json({"error": "Not found"}, 404)
    return _file(path or "index.html")


def _file(path: str) -> Response:
    target = (APP_DIR / path).resolve()
    if APP_DIR not in target.parents and target != APP_DIR or not target.is_file():
        target = APP_DIR / "index.html"
    headers = {"Cache-Control": "no-cache"}
    if target.name == "sw.js":
        headers["Service-Worker-Allowed"] = "/app/"
    return FileResponse(target, media_type=_TYPES.get(target.suffix), headers=headers)
