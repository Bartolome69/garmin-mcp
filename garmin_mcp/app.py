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
from zoneinfo import ZoneInfo
import hashlib
import json
import logging
import re
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import analysis, crews, cycling, easy_check, feeling, plan_builder, plan_checks, preview, run_view, store, training_plan
from . import plan as plan_mod
from . import server as server_mod
from . import share as share_mod
from .formatting import drop_empty
from .bounded import BoundedDict
from .server import mcp

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent / "app"
API_PREFIX = "/app/api/"
# Signing in: no token yet, so SessionBinding lets these through unbound.
SIGN_IN_PREFIX = API_PREFIX + "sign-in/"

# What each session type is called and which colour draws it. The chat cards
# call a hard session "Quality" too, so the two doors agree.
TYPES: dict[str, tuple[str, str]] = {
    "easy": ("Easy", "easy"), "recovery": ("Recovery", "easy"), "long": ("Long", "long"),
    # Hard sessions all read "Quality" (Nov 12): the session's own name says
    # which kind; the label is for skimming a week by colour.
    "threshold": ("Quality", "hard"), "sub": ("Quality", "hard"), "intervals": ("Quality", "hard"),
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
    # Nov 13: the app says Threshold, not the shorthand Sub-T, whoever named the workout.
    text = re.sub(r"\b([Ss])ub-?T\b", lambda m: "Threshold" if m.group(1) == "S" else "threshold", text)
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
        # "Repeat" spelled out: the session screen is where the word first comes up (Nov 13).
        out.append({"v": str(rep["value"]).replace("/km", ""), "u": "",
                    "l": rep["label"].replace("Rep pace", "Repeat pace").replace("Rep target", "Repeat target") if hard
                    else rep["label"].replace("Rep pace", "Pace").replace("Rep target", "Target")})
    return out


def _step_rows(steps: Any, kind: str | None, indent: int = 0) -> list[dict[str, Any]]:
    """The structure list: each step with its target and its length, repeats grouped."""
    rows: list[dict[str, Any]] = []
    for step in steps or []:
        if step.get("kind") == "repeat":
            rows.append({"label": f"{step['times']} ×", "sub": "Repeats", "head": True, "colour": None})
            rows.extend(_step_rows(step.get("steps"), kind, indent + 16))
            continue
        k = step.get("kind")
        effort = step.get("effort")
        if effort and kind != "strength":
            # Classed as the bar is (Nov 14), so the list and the bar agree: a long
            # run's easy running is blue and its faster stretch amber, cool-down included.
            colour = ("rec" if effort == "rec" else "race" if kind == "race" and k not in ("warmup", "cooldown")
                      else "hard" if effort == "work" else "long" if kind == "long" else "easy")
        elif k in ("recovery", "rest"):
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
         if score is not None else "Needs a night of sleep data", "low": low or None},
        {"l": "Sleep", "v": _hours(sleep.get("total")) or "–",
         "note": f"Score {sleep['score']}" if sleep.get("score") else "No score",
         "low": bool(sleep.get("score") and sleep["score"] < 60) or None},
        {"l": "Resting HR", "v": str(rhr["today"]) if rhr.get("today") else "–",
         "note": f"Up {up} on usual" if up and up >= 3 else "HRV in range" if hrv_status == "balanced"
         else f"HRV {hrv_status}" if hrv_status in ("low", "unbalanced", "poor")
         else "No HRV status yet" if hrv_status in ("none", "no_status") else "Usual for you",
         # A warning reads amber in the app (Nov 13).
         "low": bool((up and up >= 3) or hrv_status in ("low", "unbalanced", "poor")) or None},
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


# -- Get your plan (Nov 23) ------------------------------------------------------
# Someone with nothing planned in the next two weeks is offered two ways to a
# plan, side by side: a few questions here that open Claude or ChatGPT with
# them filled in (the app only builds the link), or the app's own builder.
# Which one comes first is drawn once per person, 50/50.
NO_PLAN_DAYS = 14


def no_active_plan(plan: Mapping[str, Any], today: date) -> bool:
    """No planned session, of any sport, from today through the next 13 days."""
    if not isinstance(plan, Mapping) or plan.get("source") in ("log", "none", "waiting"):
        return True
    last = today + timedelta(days=NO_PLAN_DAYS - 1)
    return not any(s.get("status") in ("today", "ahead") and s.get("date")
                   and today <= date.fromisoformat(str(s["date"])[:10]) <= last for s in _sessions(plan))


def _later_session(plan: Mapping[str, Any], today: date) -> dict[str, str] | None:
    """The first session further ahead than that, so the screen can say when the plan starts."""
    if not isinstance(plan, Mapping) or plan.get("source") in ("log", "none", "waiting"):
        return None
    ahead = sorted(str(s["date"])[:10] for s in _sessions(plan)
                   if s.get("status") in ("today", "ahead") and s.get("date") and str(s["date"])[:10] >= today.isoformat())
    if not ahead:
        return None
    day = date.fromisoformat(ahead[0])
    return {"date": ahead[0], "said": _short_date(day), "week": (day - timedelta(days=day.weekday())).isoformat()}


def _get_plan_block(plan: Mapping[str, Any], today: date) -> dict[str, Any] | None:
    """What the Get your plan screen needs: which choice is first for this person, and the assistant they use."""
    from . import official

    user_token = official.current()
    user = store.get_user(user_token) if user_token else None
    if user is None:
        return None
    first = store.plan_choice_first(user.email_hash or user_token, user_token)
    return drop_empty({"first": first, "assistant": assistant(), "later": _later_session(plan, today),
                       "build": builder_on() or None})


RACE_WORDS = {"5k": "a 5K", "10k": "a 10K", "half": "a half marathon", "marathon": "a marathon"}
DAY_WORDS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def plan_chat_prompt(answers: Mapping[str, Any], today: date) -> str:
    """The message that opens Claude or ChatGPT, from the three steps' answers.

    Only what was answered goes in; nothing from Garmin beyond the weekly
    distance they confirmed. The assistant reads the rest through Daash.
    """
    race = str(answers.get("race") or "")
    when = str(answers.get("date") or "")[:10]
    goal = "to get fitter, with no race in mind"
    if race in RACE_WORDS:
        goal = RACE_WORDS[race]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
            day = date.fromisoformat(when)
            weeks = (day - today).days // 7
            goal += f" on {DAY_WORDS[day.weekday()]} {day.day} {MONTHS[day.month - 1]} {day.year}" + (
                f" ({weeks} weeks away)" if weeks > 0 else "")
    elif race == "other" and answers.get("other"):
        goal = "a race: " + re.sub(r"[^\w ,.'-]", "", str(answers["other"]))[:60]
    lines = [f"Use Daash to build me a training plan. My goal: {goal}."]
    km = answers.get("km")
    if isinstance(km, (int, float)) and 0 <= km <= 300:
        lines.append(f"I run about {round(km)} km a week now.")
    days = sorted({int(d) for d in answers.get("days") or [] if str(d).isdigit() and 0 <= int(d) <= 6})
    if days:
        said = [DAY_WORDS[d] for d in days]
        line = "I can run on " + (", ".join(said[:-1]) + " and " + said[-1] if len(said) > 1 else said[0])
        long_day = answers.get("long_day")
        if isinstance(long_day, int) and long_day in days:
            line += f", with my long run on {DAY_WORDS[long_day]}"
        lines.append(line + ".")
    lines.append("Look at my Garmin history first for my paces and recent training. "
                 "Then build the plan and send the first week to my watch.")
    return " ".join(lines)


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
        heading = _week_heading(week, run, planned) if shift == 0 else None
        km = (f"{planned} km planned" if shift > 0 else f"{run} of {planned} km") if planned else (f"{run} km" if run else "")
        if km and heading:
            km += f" · on course for {heading}"
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


DONE_RUN_MEMORY = 6 * 3600


async def _extra_today(plan: Mapping[str, Any], today: date) -> dict[str, Any] | None:
    """Something done today that wasn't on the plan, such as a ride (Nov 15: a
    morning ride was on the Plan tab and nowhere on Today)."""
    extras = [x for w in plan.get("weeks") or [] for x in w.get("extra") or []
              if x.get("date") == today.isoformat() and x.get("activity_id")]
    if not extras:
        return None
    x = extras[-1]
    aid = int(x["activity_id"])
    activity = await server_mod._read(("today_activity", aid), DONE_RUN_MEMORY, lambda c: c.get_activity(aid))
    flat = server_mod._flat_activity(dict(activity or {})) if isinstance(activity, dict) else {}
    summary = server_mod._summarise_activity(flat) if flat else {}
    started = str(flat.get("startTimeLocal") or "")
    ride = x.get("sport") == "ride" or _is_ride(activity if isinstance(activity, dict) else None)
    bits = [f"{x['actual_km']:g} km" if x.get("actual_km") else None,
            f"{summary['avg_speed_kmh']:g} km/h" if ride and summary.get("avg_speed_kmh") else None,
            x.get("actual") if not x.get("actual_km") else None]
    figures = " · ".join(b for b in bits if b)
    return drop_empty({"activity_id": aid, "name": _display(x.get("name")), "at": started[11:16] if len(started) >= 16 else None,
                       "line": f"{_display(x.get('name'))} · {figures}" if figures else _display(x.get("name")),
                       "colour": _type("ride" if ride else "other")["colour"]})


async def _done_today(plan: Mapping[str, Any], today: date) -> dict[str, Any] | None:
    done = [s for s in _sessions(plan) if s.get("date") == today.isoformat()
            and str(s.get("status", "")).startswith("done") and s.get("activity_id")]
    if not done:
        return await _extra_today(plan, today)
    s = min(done, key=training_plan._rank)
    aid = int(s["activity_id"])
    # A finished run doesn't change, so a second look at Today today asks Garmin
    # for neither again (a rename through Daash clears the memory).
    laps, workout, activity = await asyncio.gather(
        server_mod._read(("today_splits", aid), DONE_RUN_MEMORY, lambda c: c.get_activity_splits(aid)),
        _workout(s.get("workout_id")),
        server_mod._read(("today_activity", aid), DONE_RUN_MEMORY, lambda c: c.get_activity(aid)),
        return_exceptions=True)
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
    # Nov 23: nothing planned in the next two weeks is no plan, whatever sits further out.
    if no_active_plan(plan, today):
        return drop_empty({"today": today.isoformat(), "kicker": _short_date(today).upper(), "headline": "No plan on the calendar",
                           "empty": True, "readiness": ready, "build": builder_on() or None, "assistant": assistant(),
                           "get_plan": _get_plan_block(plan, today),
                           "ask": _ask_links("Use Daash to build me a training plan. Ask me my goal and race date first.")})
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
            "not_100": not_100_offer(nxt["date"], nxt.get("type"), nxt.get("status"), today, ready),
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


async def heads_up_payload(day: str) -> dict[str, Any] | None:
    """The session planned on one day, for the notification before it, or None.

    Lighter than Today: the plan and that one workout, nothing else read from Garmin.
    """
    plan = await server_mod.get_plan()
    if not isinstance(plan, Mapping) or _error(plan) or plan.get("source") == "log":
        return None
    lead, on_day = training_plan.day_sessions(plan, day)
    waiting = [s for s in on_day if s.get("status") in ("today", "ahead")]
    if not waiting:
        return None
    session = lead if lead in waiting else min(waiting, key=training_plan._rank)
    workout = await _workout(session.get("workout_id"))
    totals = training_plan.session_totals(workout, training_plan.easy_speed(plan)) if workout else None
    return {"date": day, "name": _display(session.get("name")), "stats": _stats(totals, session.get("type"), workout)}


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
                  "sub": WATCH_UPDATED if (easier_state or {}).get("applied")
                  else "Scheduled. Arrives at its next sync.",
                  "changed": bool((easier_state or {}).get("applied"))},
        "easier": easier_state,
        "not_100": not_100_offer(day, kind, status, today, ready),
        "workout_id": chosen.get("workout_id"), "schedule_id": chosen.get("schedule_id"),
        "also": [{"name": _display(s.get("name")), "schedule_id": s.get("schedule_id"), **_type(s.get("type"))}
                 for s in on_day if s is not chosen] or None,
        "missed": status == "missed" or None,
        "assistant": assistant(),
        "ask": _ask_links(prompt),
    })


# A run longer than planned is titled by what was run, in the session's own word (Nov 22).
# Sessions built on reps keep their name: "3 x 3 km" says more than its distance.
RAN_ON_TITLE = {"easy": "easy", "recovery": "recovery run", "long": "long run"}


def run_header(name: str, kind: str | None, extra: Mapping[str, Any] | None,
               rows: Sequence[Mapping[str, Any]] | None) -> tuple[str, str | None]:
    """The run screen's title and the muted line under it.

    Nov 22: a run that went on past its plan leads with what was run, the plan
    under it; that line is the one place the planned distance is said (Nov 24).
    """
    if not extra:
        return name, None
    word = RAN_ON_TITLE.get(kind or "")
    if word and not any(r.get("rep") for r in rows or []):
        # Nov 24: "8km easy + strides" run to 10.4 reads "10.4 km easy + strides".
        name = f"{extra['ran_km']:g} km {word}" + (" + strides" if "stride" in name.lower() else "")
    return name, (f"Planned {'about ' if extra.get('about') else ''}{extra['planned_km']:g} km"
                  f" \u00b7 ran {extra['extra_km']:g} km extra")


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
    if _is_ride(activity):
        # Nov 15: a ride opened as a run (pace, "Km by km", an easy-running
        # verdict against runs). A ride reads in its own terms.
        return await _ride_payload(activity_id, activity, laps, recording, row)
    # The last session like this one, read while this one's workout is: its
    # line is left out rather than keep the screen waiting.
    like = asyncio.create_task(_last_like(plan, row, activity_id))
    # And the easy runs of 3 to 7 weeks before, for its easy running against them (Nov 14).
    kind_now = (row or {}).get("type")
    run_day = str((row or {}).get("done_on") or (row or {}).get("date")
                  or ((activity or {}).get("summaryDTO") or {}).get("startTimeLocal") or "")[:10]
    then = asyncio.create_task(_easy_before(run_day)) if kind_now in ("easy", "recovery", "long") or not row else None
    try:
        workout = await _workout((row or {}).get("workout_id"))
    except Exception:  # noqa: BLE001 - the run reads without its plan
        workout = None
    try:
        device = await server_mod._device_name(server_mod._flat_activity(activity or {}))
    except Exception:  # noqa: BLE001 - the run shares with "Garmin" alone
        device = None
    details = server_mod.run_brief(activity or {}, laps, recording,
                                   share_mod.plan_context(plan, activity_id, workout), device)
    kind = (row or {}).get("type") or (details.get("summary") or {}).get("type")
    result = analysis.analyse(workout, laps, kind, recording, story=True)
    figures = result.pop("figures", None)
    try:
        before = await asyncio.wait_for(like, 3)
    except Exception:  # noqa: BLE001 - the line is left out, the run still reads
        before = None
    # A hard session is compared on its reps alone: whole runs of reps and jogs say little.
    if figures and (kind in HARD_TYPES) and not figures.get("reps"):
        figures = None
    if before and figures:
        line = run_view.last_time(figures, before["figures"], before["what"], before["name"], before["day"])
        if line:
            result["insights"] = [*(result.get("insights") or []), line]
    try:
        earlier = await asyncio.wait_for(then, 3) if then else None
    except Exception:  # noqa: BLE001 - the line is left out, the run still reads
        earlier = None
    if earlier and figures and kind not in HARD_TYPES:
        line = run_view.easy_trend(figures.get("steady"), earlier, long_run=kind == "long")
        if line:
            result["insights"] = [*(result.get("insights") or []), line]
    summary = details.get("summary") or {}
    started = None
    start_text = str(((activity or {}).get("summaryDTO") or {}).get("startTimeLocal") or "")
    if start_text:
        started = datetime.fromisoformat(start_text.replace(" ", "T").split(".")[0])
    t = _type(kind)
    _row_colours(result.get("planned_and_run") or [], t["colour"])
    name, planned_line = run_header(_display((row or {}).get("name") or summary.get("name") or "Run"),
                                    kind, result.pop("extra", None), result.get("planned_and_run"))
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
        "name": name, "planned_line": planned_line, **t, **{k: v for k, v in result.items() if k != "short"},
        "effect": effect, "share_url": share,
        # The run's own watch, for Garmin's credit by its title (Nov 10).
        "credit": share_mod.attribution(device),
        # Nov 9: an easy session run harder than its target says so here.
        "easy_check": easy_check.card(details.get("easy_check")),
        "assistant": assistant(),
        "ask": _ask_links(f"Use Daash to look at my run {activity_id} ({name}). How did it go against the plan?"),
    })


def _is_ride(activity: Mapping[str, Any] | None) -> bool:
    flat = server_mod._flat_activity(dict(activity or {}))
    return cycling.is_ride((flat.get("activityType") or {}).get("typeKey"))


def _clock(seconds: Any) -> str | None:
    """1:21:24, or 48:05 under the hour: how the run screen writes a time."""
    try:
        secs = int(round(float(seconds)))
    except (TypeError, ValueError):
        return None
    h, rest = divmod(secs, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _ride_stats(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    """A ride's numbers, by speed and power: only those Garmin recorded (none estimated)."""
    power = summary.get("power") or {}
    hr = (summary.get("heart_rate") or {}).get("average_bpm")
    picks = [
        (round(float(summary["distance_km"]), 1) if summary.get("distance_km") else None, "km"),
        (_clock(summary.get("duration_seconds")) or summary.get("duration"), "time"),
        (summary.get("avg_speed_kmh"), "km/h avg"),
        (power.get("average_w"), "W avg"),
        (hr, "bpm avg"),
        (summary.get("elevation_gain_m"), "m climbed"),
        (power.get("normalized_w"), "W normalised"),
        (summary.get("avg_cadence_rpm"), "rpm avg"),
        (summary.get("max_speed_kmh"), "km/h max"),
    ]
    stats = [{"v": f"{v:g}" if isinstance(v, (int, float)) else str(v), "l": label} for v, label in picks if v]
    return stats[:8]  # two rows of four at most


def _ride_chart(splits: list[Mapping[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None, str]:
    """Each lap's speed and heart rate as bars and a line, and the laps as a table."""
    laps = [s for s in splits if s.get("avg_speed_kmh") and (s.get("distance_km") or 0) >= 0.2]
    if len(laps) < 2:
        return None, None, "Laps"
    done, parts, rows = 0.0, [], []
    for lap in laps:
        done += float(lap.get("distance_km") or 0)
        speed = float(lap["avg_speed_kmh"])
        parts.append(drop_empty({"k": "km", "p": round(3600 / speed, 1), "hr": lap.get("avg_hr"), "label": f"{done:.1f}".rstrip("0").rstrip(".")}))
        rows.append(drop_empty({"km": f"{done:.1f}".rstrip("0").rstrip("."), "pace": f"{speed:.1f} km/h", "hr": lap.get("avg_hr"),
                                "w": ((lap.get("power") or {}).get("average_w"))}))
    whole_km = all(0.9 <= float(lap.get("distance_km") or 0) <= 1.1 for lap in laps[:-1])
    chart = {"mode": "km", "unit": "kmh", "bar_label": "Speed", "parts": parts,
             "caption": None}
    return chart, rows, "Km by km" if whole_km else "Lap by lap"


async def _ride_payload(activity_id: int, activity: Mapping[str, Any] | None, laps: Any, recording: Any,
                        row: Mapping[str, Any] | None) -> dict[str, Any]:
    """A ride's screen: speed, power, climbing and heart rate, lap by lap. No running verdicts."""
    flat = server_mod._flat_activity(dict(activity or {}))
    try:
        device = await server_mod._device_name(flat)
    except Exception:  # noqa: BLE001 - the ride reads with "Garmin" alone
        device = None
    details = server_mod.run_brief(dict(activity or {}), laps, recording, None, device)
    summary = details.get("summary") or {}
    started = None
    start_text = str(flat.get("startTimeLocal") or "")
    if start_text:
        started = datetime.fromisoformat(start_text.replace(" ", "T").split(".")[0])
    name = _display((row or {}).get("name") or summary.get("name") or "Ride")
    t = _type("ride")
    kicker = (f"{_short_date(started.date()).upper()} · {started.strftime('%H:%M')} · " if started else "") + "RIDE"
    power = (summary.get("power") or {}).get("average_w")
    hr = (summary.get("heart_rate") or {}).get("average_bpm")
    bits = [f"{round(float(summary['distance_km']), 1):g} km" if summary.get("distance_km") else None,
            f"in {_clock(summary.get('duration_seconds')) or summary['duration']}" if summary.get("duration") else None,
            f"at {summary['avg_speed_kmh']:g} km/h" if summary.get("avg_speed_kmh") else None]
    verdict = " ".join(b for b in bits if b)
    tail = f"{power:g} W average" if power else f"heart rate averaging {hr:g}" if hr else None
    if summary.get("elevation_gain_m"):
        tail = f"{tail} and {summary['elevation_gain_m']:g} m climbed" if tail else f"{summary['elevation_gain_m']:g} m climbed"
    verdict = (f"{verdict}, {tail}." if tail else f"{verdict}.") if verdict else None
    chart, rows, chart_title = _ride_chart(details.get("splits") or [])
    effect = ({"head": "Counts as done", "sub": "It's on the plan, and this ride was it."} if row else
              {"head": "Not on the plan", "sub": "Rides don't count toward your running km. Nothing on the plan changes."})
    share = (details.get("share_image") or {}).get("url")
    return drop_empty({
        "activity_id": activity_id, "date": (row or {}).get("date"), "kicker": kicker, "name": name, **t,
        "ride": True, "verdict": verdict, "stats": _ride_stats(summary),
        "chart": chart, "chart_title": chart_title, "splits": rows,
        "km": round(float(summary["distance_km"]), 1) if summary.get("distance_km") else None,
        "split_cols": ["KM", "SPEED", "HR"],
        "effect": effect, "share_url": share,
        "credit": share_mod.attribution(device),
        "assistant": assistant(),
        "ask": _ask_links(f"Use Daash to look at my ride {activity_id} ({name}). How did it go?"),
    })


# What "the last one like it" is called, by the session's type.
LIKE_NAMES = {"easy": "easy run", "recovery": "recovery run", "long": "long run", "threshold": "threshold session",
              "sub": "threshold session", "intervals": "interval session"}


async def _last_like(plan: Mapping[str, Any], row: Mapping[str, Any] | None, activity_id: int) -> dict[str, Any] | None:
    """The last session of the same type done before this one, on the plan, with
    the figures it's compared on: its reps, or its pace and heart rate."""
    if not row or row.get("type") not in LIKE_NAMES:
        return None
    day = str(row.get("done_on") or row.get("date") or "")
    earlier = [s for s in _sessions(plan) if s.get("type") == row["type"] and s.get("activity_id")
               and s.get("activity_id") != activity_id and str(s.get("done_on") or s.get("date") or "") < day]
    if not earlier:
        return None
    last = max(earlier, key=lambda s: str(s.get("done_on") or s.get("date")))
    aid = int(last["activity_id"])
    laps, workout = await asyncio.gather(server_mod._call(lambda c: c.get_activity_splits(aid)),
                                         _workout(last.get("workout_id")), return_exceptions=True)
    if isinstance(laps, Exception):
        return None
    workout = None if isinstance(workout, Exception) else workout
    figures = analysis.analyse(workout, laps, last.get("type"), story=True).get("figures")
    if not figures:
        return None
    when = date.fromisoformat(str(last.get("done_on") or last["date"])[:10])
    return {"figures": figures, "what": LIKE_NAMES[row["type"]], "name": _display(last.get("name")),
            "day": f"{when.day} {MONTHS[when.month - 1]}"}


async def _easy_before(day: str) -> dict[str, Any] | None:
    """The easy runs of 3 to 7 weeks before a run, as one pace and heart rate, from Garmin's activity list."""
    try:
        when = date.fromisoformat(day)
    except ValueError:
        return None
    since = (when - timedelta(days=run_view.TREND_FROM_DAYS)).isoformat()
    until = (when - timedelta(days=run_view.TREND_TO_DAYS)).isoformat()
    try:
        rows = await server_mod._call(lambda c: c.get_activities_by_date(since, until))
    except Exception:  # noqa: BLE001 - no line rather than a wrong one
        return None
    return run_view.easy_then(rows if isinstance(rows, list) else [])


def _row_colours(rows: list[dict[str, Any]], colour: str) -> None:
    """Warm-up and cool-down easy, reps in the session's colour; of a long run's
    parts, the steady ones long and the quicker ones (a goal-pace finish) hard."""
    for r in rows:
        if r.get("kind") in ("warmup", "cooldown", "extra"):
            # A long run is blue end to end, as its bar and card draw it (Nov 14).
            r["colour"] = "long" if colour == "long" else "easy"
        elif r.get("kind") == "strides":
            # Quick, short and few: the hard colour, whatever the run around them.
            r["colour"] = "hard"
        elif r.get("rep") or colour != "long":
            r["colour"] = colour
        else:
            # The quicker stretch, as analysis marked it (Nov 14: the slowest
            # target was picked by its text, so "125–148 bpm" against "3:46–3:56"
            # coloured the half-marathon-pace 3 km as the steady part).
            r["colour"] = "hard" if r.get("quick") else "long"
        r.pop("quick", None)


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


# A run within this share either side of its planned distance was run as planned (Nov 13).
ON_TARGET = 0.10


def _outcome(s: Mapping[str, Any], done: bool, status: str) -> str | None:
    """How a session went against its plan: on target, over, short or missed. None while it's ahead."""
    if status == "missed":
        return "missed"
    if not done:
        return None
    planned, actual = s.get("planned_km"), s.get("actual_km")
    if not planned or not actual:
        # Strength, or a run with no planned distance: done is done.
        return "on"
    share = float(actual) / float(planned)
    return "over" if share > 1 + ON_TARGET else "short" if share < 1 - ON_TARGET else "on"


def _main_target(workout: Mapping[str, Any] | None) -> str | None:
    """The target of the step a session is mostly made of: the reps' pace in a hard
    session, the steady pace of an easy or long run. A warm-up or cool-down's own
    heart rate isn't the session's target."""
    best: tuple[float, str] | None = None

    def walk(steps: Any, times: int) -> None:
        nonlocal best
        for step in steps or []:
            if step.get("type") == "RepeatGroupDTO" or step.get("numberOfIterations"):
                walk(step.get("workoutSteps"), times * int(step.get("numberOfIterations") or 1))
                continue
            kind = ((step.get("stepType") or {}).get("stepTypeKey") or "").lower()
            target = training_plan._target(step)
            if kind != "interval" or not target:
                continue
            size = float(step.get("endConditionValue") or 0) * times
            # Distance in metres and time in seconds compare well enough at running speeds (~3 m/s).
            if ((step.get("endCondition") or {}).get("conditionTypeKey") or "").lower() == "time":
                size *= 3
            if best is None or size > best[0]:
                best = (size, target)

    for segment in (workout or {}).get("workoutSegments") or []:
        walk(segment.get("workoutSteps"), 1)
    return best[1].replace("/km", " /km").replace("  /km", " /km") if best else None


async def _week_targets(week: Mapping[str, Any], today: date) -> dict[Any, dict[str, Any]]:
    """For a week's sessions: each one's target and planned length, from its workout.

    Read once each, at once, and kept as every workout read is (WORKOUT_MEMORY).
    A workout that can't be read leaves its row as it was.
    """
    # Done ones too, so a run shows the target it was run against (one read per workout, however many days use it).
    ahead = [s for s in week.get("sessions") or [] if s.get("workout_id") and s.get("schedule_id")]
    ids = list(dict.fromkeys(s["workout_id"] for s in ahead))
    read = dict(zip(ids, await asyncio.gather(*(_workout(w) for w in ids), return_exceptions=True)))
    out: dict[Any, dict[str, Any]] = {}
    for s in ahead:
        workout = read.get(s["workout_id"])
        if isinstance(workout, BaseException) or not workout:
            continue
        totals = training_plan.session_totals(workout) or {}
        out[s["schedule_id"]] = drop_empty({"target": _main_target(workout), "minutes": totals.get("minutes")})
    return out


def _row(s: Mapping[str, Any], today: date, next_id: Any, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
    extra = extra or {}
    plan_min = s.get("planned_min") or extra.get("minutes")
    return drop_empty({
        "date": s["date"], "name": _display(s.get("name")), **_type(s.get("type")),
        "state": "done" if done else "next" if is_next else status or "ahead",
        "meta": meta, "missed": status == "missed" or None,
        # Nov 13: what was planned beside what was done, how it went, and the target.
        "plan_km": f"{round(float(s['planned_km']), 1):g}" if s.get("planned_km") else None,
        "plan_min": plan_min or None,
        "done_km": f"{round(float(s['actual_km']), 1):g}" if done and s.get("actual_km") else None,
        "done_pace": str(s["actual_pace"]).replace(" /km", "/km") if done and s.get("actual_pace") else None,
        "done_time": s.get("actual") if done else None,
        "outcome": _outcome(s, done, status),
        "target": extra.get("target"),
        # Scheduled on the Garmin calendar and still to come: it's on the watch (at its next sync).
        "watch": (not done and status not in ("missed", "skipped") and d >= today and bool(s.get("schedule_id"))) or None,
        "optional": s.get("optional") or None,
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
    ride = x.get("sport") == "ride"
    return drop_empty({
        "date": x["date"], "name": _display(x.get("name")), "type": "extra", "type_label": "Ride" if ride else "Extra",
        "colour": "rec",
        "state": "extra", "meta": " · ".join(v for v in ("Not on the plan", x.get("actual")) if v),
        "v": f"{round(float(km), 1):g}" if km else (f"{x['actual_min']}" if x.get("actual_min") else ""),
        "u": "km" if km else "min" if x.get("actual_min") else "",
        "activity_id": x.get("activity_id"),
    })


def _week_view(plan: Mapping[str, Any], weeks: Mapping[str, Mapping[str, Any]], season: Mapping[str, Any] | None,
               monday: date, today: date, targets: Mapping[Any, Mapping[str, Any]] | None = None) -> dict[str, Any]:
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
            "past": d < today, "sessions": [_row(s, today, (nxt or {}).get("schedule_id"), (targets or {}).get(s.get("schedule_id")))
                                             for s in here]
            + [_extra_row(x) for x in extra],
        })
    run, planned = week.get("run_km"), week.get("planned_km")
    heading = (_week_heading(week, round(run or 0), round(planned))
               if planned and monday == today - timedelta(days=today.weekday()) else None)
    earlier, later = monday - timedelta(weeks=1), monday + timedelta(weeks=1)
    within = lambda m: m.isoformat() in weeks or _season_week(season, m) is not None  # noqa: E731
    hard = [s for s in sessions if s.get("type") in HARD_TYPES and not s.get("optional")]
    return drop_empty({
        "starts": monday.isoformat(),
        # The week's hard sessions, and how many are done (Nov 13).
        "hard": {"done": sum(1 for s in hard if str(s.get("status") or "").startswith("done")), "planned": len(hard)}
        if hard else None,
        "title": f"Week {n}" + (f" · {phase}" if phase else "") if n else f"Week of {monday.day} {MONTHS[monday.month - 1]}",
        "done": round(run) if run is not None else None, "planned": round(planned) if planned else None,
        "heading": heading,
        "range": _range(monday), "current": monday == today - timedelta(days=today.weekday()) or None,
        "days": days,
        "previous": earlier.isoformat() if within(earlier) else None,
        "next": later.isoformat() if within(later) else None,
        "ask_next": _ask_links(f"Use Daash to plan next week, the week of {later.isoformat()}, "
                               "and adjust it to how this week has gone."),
        "ask": _ask_links(f"Use Daash to look at my training week of {monday.isoformat()}. "
                          "Is it balanced, and is anything worth changing?"),
    })


def _week_heading(week: Mapping[str, Any], done: int, planned: int) -> int | None:
    """Where this week is heading, beside the plan's figure, never in its place.

    The target is the plan's figure, fixed for the week, and what's done can
    go past it: "71 of 66 km" (Nov 15). From Nov 13 the target itself grew to
    done plus what's left, so a finished week with an extra run read "71 of
    71", saying nothing. Done plus what's still to come (optional sessions
    aside) is shown as its own note while sessions remain, and only when it
    differs from the plan: "49 of 66 km · on course for 69".
    """
    sport = "ride" if "ride_km" in week else "run"
    to_go = sum(s.get("planned_km") or 0 for s in week.get("sessions") or []
                if s.get("status") in ("today", "ahead") and not s.get("optional") and s.get("sport") == sport)
    if not planned or round(to_go) <= 0:
        return None
    heading = done + round(to_go)
    return heading if heading != planned else None


def _rest_note(r: Mapping[str, Any]) -> str | None:
    """Today's rest day, in a line from Garmin (Nov 13): last night's sleep, and HRV against its range."""
    if r.get("error"):
        return None
    sleep, hrv = r.get("sleep") or {}, r.get("hrv") or {}
    status = str(hrv.get("status") or "").lower()
    bits = []
    if _hours(sleep.get("total")):
        bits.append(f"Slept {_hours(sleep.get('total'))}" + (f", score {sleep['score']}" if sleep.get("score") else ""))
    if status == "balanced":
        bits.append("HRV in range")
    elif status in ("low", "unbalanced", "poor"):
        bits.append("HRV below range")
    return " · ".join(bits) + " · from Garmin" if bits else None


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
    # Race weeks (Nov 13): the goal race is the A race, flagged large; key races and
    # tune-ups are B races, marked smaller. The block's own races and its blocks' milestones.
    races: dict[str, dict[str, Any]] = {}
    for r in list(plan.get("races") or []) + [dict(m, name=m.get("label")) for b in season["blocks"]
                                              for m in b.get("milestones") or []]:
        d = _day_of(r.get("date"))
        if not d:
            continue
        tier = "A" if r.get("tier") == "goal" or (season["goal"] and r.get("date") == season["goal"].get("date")) else "B"
        key = (d - timedelta(days=d.weekday())).isoformat()
        if key not in races or (tier == "A" and races[key]["tier"] == "B"):
            races[key] = {"tier": tier, "name": str(r.get("name") or r.get("label") or "Race"), "date": _short_date(d)}
    for b in bars:
        b["race"] = races.get(b["starts"])
    top = max((max(b["planned"], b["run"]) for b in bars), default=0) or 1
    peak = max(bars, key=lambda b: b["planned"])
    many = len(bars) > 16
    for b in bars:
        b["h"] = round(b["planned"] / top, 3)
        # What was run, on the same scale: a past week is drawn solid at this height.
        b["rh"] = round(b["run"] / top, 3) if b["past"] or b["current"] else 0
        b["fill"] = min(1.0, round(b["run"] / b["planned"], 3)) if b["planned"] and (b["past"] or b["current"]) else 0
        b["label"] = str(round(b["planned"])) if b["current"] or b is peak else ""
        # On a long plan every fourth week is numbered, with the first, this week and the last.
        b["show_n"] = (not many or b["n"] == 1 or b["n"] % 4 == 0 or b["current"] or b["n"] == len(bars)) or None
    if many:
        # Two-figure numbers on neighbouring weeks run together on a phone ("2425",
        # Nov 11): this week, the last and the first keep theirs, the other gives way.
        shown: set[int] = set()
        for b in sorted(bars, key=lambda b: (not b["current"], b["n"] != len(bars), b["n"] != 1, b["n"])):
            if not b["show_n"]:
                continue
            if any(abs(b["n"] - m) == 1 and max(b["n"], m) >= 10 for m in shown):
                b["show_n"] = None
            else:
                shown.add(b["n"])
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
    phase_lines = _phase_lines(phases, bars, weeks)
    stats = _block_stats(bars, weeks, season, today)
    name = blocks[0].get("name") or blocks[0].get("goal") or "plan"
    title_name = (f"Your plan to {goal['name']}" if goal and goal.get("name") and len(blocks) > 1
                  else f"Your {name[:1].lower() + name[1:]}" if not name[:2].isupper() else f"Your {name}")
    return drop_empty({
        "title": f"{len(bars)} weeks to race day" if goal else f"{len(bars)}-week plan",
        "bars": bars, "phases": spans or None,
        "verdict": _block_verdict(bars),
        # Nov 13: a line on each phase and the plan's numbers so far, all from the plan and Garmin.
        "phase_lines": phase_lines or None, "stats": stats or None,
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


def _sessions_of(weeks: Mapping[str, Mapping[str, Any]], bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for b in bars for s in (weeks.get(b["starts"]) or {}).get("sessions") or []]


def _phase_lines(phases: list[dict[str, Any]], bars: list[dict[str, Any]],
                 weeks: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each phase in a line, from its own weeks: how many km a week, how many
    hard sessions, how long the long runs. Nothing it doesn't hold is said."""
    out = []
    for ph in phases:
        span = [b for b in bars if ph["from"] <= b["n"] <= ph["to"]]
        kms = [round(b["planned"]) for b in span if b["planned"]]
        sessions = _sessions_of(weeks, span)
        hard = [sum(1 for s in (weeks.get(b["starts"]) or {}).get("sessions") or []
                    if s.get("type") in HARD_TYPES and not s.get("optional")) for b in span
                if (weeks.get(b["starts"]) or {}).get("sessions")]
        longs = [float(s["planned_km"]) for s in sessions if s.get("type") == "long" and s.get("planned_km")]
        bits = []
        if kms:
            bits.append(f"{min(kms)}–{max(kms)} km a week" if min(kms) != max(kms) else f"about {kms[0]} km a week")
        if hard:
            per = round(sum(hard) / len(hard))
            bits.append("no hard sessions" if per == 0 else f"{_word(per)} hard session{'s' if per > 1 else ''} a week")
        if longs:
            lo, hi = round(min(longs)), round(max(longs))
            bits.append(f"long runs {lo}–{hi} km" if lo != hi else f"long runs of {lo} km")
        weeks_said = f"week {ph['from']}" if ph["from"] == ph["to"] else f"weeks {ph['from']}–{ph['to']}"
        out.append({"label": ph["label"], "weeks": weeks_said, "line": _capital(", ".join(bits)) + "." if bits else None,
                    "current": any(b["current"] for b in span) or None})
    return out


def _block_stats(bars: list[dict[str, Any]], weeks: Mapping[str, Mapping[str, Any]],
                 season: Mapping[str, Any], today: date) -> list[dict[str, Any]]:
    """The plan's numbers so far (Nov 13): km run against km planned to date, the
    longest run done and to come, weeks to the race. Only what the plan and Garmin hold."""
    out = []
    done = [b for b in bars if b["past"] or b["current"]]
    if done:
        run = round(sum(b["run"] for b in done))
        to_date = sum(b["planned"] for b in bars if b["past"]) + sum(
            float(s.get("planned_km") or 0) for b in bars if b["current"]
            for s in (weeks.get(b["starts"]) or {}).get("sessions") or []
            if s.get("date", "9999") <= today.isoformat() and not s.get("optional"))
        if to_date:
            out.append({"l": "Run so far", "v": f"{run} km", "sub": f"of {round(to_date)} km planned to date"})
    sessions = _sessions_of(weeks, bars)
    ran = [float(s["actual_km"]) for s in sessions if s.get("actual_km") and s.get("sport", "run") == "run"
           and str(s.get("status") or "").startswith("done")]
    ahead = [float(s["planned_km"]) for s in sessions if s.get("planned_km") and s.get("type") != "race"
             and s.get("sport", "run") == "run" and s.get("date", "") > today.isoformat()]
    if ran or ahead:
        out.append({"l": "Longest run", "v": f"{max(ran):g} km" if ran else "–",
                    "sub": f"{max(ahead):g} km to come" if ahead else None})
    goal = season.get("goal")
    race_day = _day_of((goal or {}).get("date"))
    if race_day and race_day >= today:
        n = ((race_day - timedelta(days=race_day.weekday())) - (today - timedelta(days=today.weekday()))).days // 7
        out.append({"l": "To race day", "v": f"{n} week{'s' if n != 1 else ''}" if n else "This week",
                    "sub": _short_date(race_day)})
    return [drop_empty(x) for x in out]


# The goal race's distance, by Garmin's prediction key.
PREDICTION_KEY = {42.2: ("timeMarathon", "Marathon"), 21.1: ("timeHalfMarathon", "Half marathon"),
                  10.0: ("time10K", "10K"), 5.0: ("time5K", "5K")}


def _race_distance(goal: Mapping[str, Any] | None) -> tuple[str, str] | None:
    if not goal:
        return None
    km = goal.get("distance_km")
    if km:
        return min(PREDICTION_KEY.items(), key=lambda kv: abs(kv[0] - float(km)))[1] if any(
            abs(k - float(km)) < 1 for k in PREDICTION_KEY) else None
    name = str(goal.get("name") or "").lower()
    if "half" in name:
        return PREDICTION_KEY[21.1]
    if "marathon" in name:
        return PREDICTION_KEY[42.2]
    if "10k" in name or "10 k" in name:
        return PREDICTION_KEY[10.0]
    if "5k" in name or "parkrun" in name:
        return PREDICTION_KEY[5.0]
    return None


def _clock_time(secs: float) -> str:
    secs = round(secs)
    h, m, sec = secs // 3600, secs % 3600 // 60, secs % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


async def _prediction_stat(season: Mapping[str, Any] | None, today: date) -> dict[str, Any] | None:
    """Garmin's prediction for the goal race's distance, then and now (Nov 13): when
    the plan started and today. Nothing is worked out here: no prediction, no line."""
    if not season or not season.get("goal"):
        return None
    which = _race_distance(season["goal"])
    first = season["first"]
    if not which or first >= today or (today - first).days > 360:
        return None
    key, label = which
    start, end = first.isoformat(), today.isoformat()
    try:
        raw = await server_mod._read(("race_predictions_daily", start, end), server_mod.PLAN_WORKOUT_MEMORY,
                                     lambda c: c.get_race_predictions(start, end, "daily"))
    except Exception:  # noqa: BLE001 - no prediction, no line
        return None
    from . import fitness_trends
    points = sorted(fitness_trends.series(raw, (key,)))
    if len(points) < 2:
        return None
    then, now = points[0][1], points[-1][1]
    if not then or not now:
        return None
    return {"l": f"{label}, Garmin's prediction", "v": f"{_clock_time(then)} → {_clock_time(now)}",
            "sub": f"{_short_date(date.fromisoformat(points[0][0][:10]))} to now"}


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
    line = f"{head} {first}; {remark}." if remark else f"{head} {first}."
    after = _next_week_line(bars, now)
    return f"{line} {after}" if after else line


def _next_week_line(bars: list[dict[str, Any]], now: Mapping[str, Any] | None) -> str | None:
    """What next week does, from the plan (Nov 13): drops to recover, builds, tapers, or races."""
    if not now:
        return None
    nxt = next((b for b in bars if b["n"] == now["n"] + 1), None)
    if not nxt or not nxt["planned"]:
        return None
    km = round(nxt["planned"])
    if nxt.get("race") and nxt["race"].get("tier") == "A":
        return f"Next week is race week: {nxt['race']['name']}."
    if nxt.get("cutback"):
        return f"Next week drops to {km} km to recover."
    if (nxt.get("phase") or "").lower() == "taper" and now["planned"] and km < now["planned"]:
        return f"Next week tapers to {km} km."
    if now["planned"] and km > now["planned"] * 1.05:
        return f"Next week builds to {km} km."
    if now["planned"] and km < now["planned"] * 0.9:
        return f"Next week drops to {km} km."
    return f"Next week holds at about {km} km."


async def plan_payload(week_of: str | None = None) -> dict[str, Any]:
    today = date.today()
    plan = await server_mod.get_plan()
    # Nothing scheduled and nothing run lately, or just connected through the
    # official API and waiting for Garmin's first runs: no plan yet, not a failure.
    starting = plan.get("source") in ("none", "waiting") if isinstance(plan, dict) else False
    if _error(plan) and not starting:
        return {"error": _error(plan)}
    # Nov 23: nothing in the next two weeks is no plan too, unless a later week was asked for.
    if plan.get("source") == "log" or starting or (not week_of and no_active_plan(plan, today)):
        return drop_empty({"empty": True, "build": builder_on() or None, "fresh": starting or None, "assistant": assistant(),
                           "get_plan": _get_plan_block(plan, today),
                           "ask": _ask_links("Use Daash to build me a training plan. Ask me my goal and race date first.")})
    anchor = _day_of(week_of) or today
    monday = anchor - timedelta(days=anchor.weekday())
    season = _season(plan)
    weeks = await _season_weeks(plan, season)
    # The plan running now, changed in the builder or taken off.
    manage = None
    running = await _running_plan(plan, season, today) if builder_on() else None
    if running and running["labels"]:
        manage = {"count": len(running["dates"]), "from": _short_date(date.fromisoformat(running["dates"][0])),
                  "to": _short_date(date.fromisoformat(running["dates"][-1]))}
    # Three weeks or less to go: the next block can be planned now.
    # Nothing on the calendar from today (a plan finished or removed): a new one.
    next_block = None
    if builder_on() and not running:
        next_block = {"none": True}
    elif builder_on() and season and today <= season["last"] <= today + timedelta(weeks=3):
        next_block = {"ends": _short_date(season["last"]),
                      "ask": _ask_links("Use Daash to plan my next training block, starting the Monday after this one ends.")}
    block = _block_view(plan, weeks, season, today)
    this_monday = today - timedelta(days=today.weekday())
    shown = weeks.get(monday.isoformat()) or {}
    rest_today = monday == this_monday and not any(s.get("date") == today.isoformat() for s in shown.get("sessions") or [])
    targets, prediction, ready = await asyncio.gather(
        _week_targets(shown, today),
        _prediction_stat(season, today) if block else asyncio.sleep(0),
        server_mod.get_readiness() if rest_today else asyncio.sleep(0),
        return_exceptions=True)
    targets = targets if isinstance(targets, dict) else {}
    if block and isinstance(prediction, dict):
        block["stats"] = (block.get("stats") or []) + [prediction]
    rest_note = _rest_note(ready) if rest_today and isinstance(ready, dict) else None
    # Nov 13: next week, in short, under this one.
    later = monday + timedelta(weeks=1)
    preview = None
    if monday == this_monday and later.isoformat() in weeks:
        nw = _week_view(plan, weeks, season, later, today)
        preview = {"starts": later.isoformat(), "title": nw.get("title"), "planned": nw.get("planned"), "range": nw.get("range"),
                   "sessions": [drop_empty({"dow": d["dow"], "name": r["name"], "colour": r["colour"],
                                            "type_label": r["type_label"], "km": r.get("plan_km"),
                                            "min": None if r.get("plan_km") else r.get("plan_min")})
                                for d in nw["days"] for r in d["sessions"]]}
    week = _week_view(plan, weeks, season, monday, today, targets)
    if rest_note:
        week["rest_note"] = rest_note
    return drop_empty({"today": today.isoformat(), "week": week, "next_week": preview,
                       "block": block, "assistant": assistant(), "next_block": next_block, "manage": manage,
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
                     else "Nothing to run together next week yet" if ahead else "No runs together yet"),
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
        "heads_up": store.heads_up_for(user_token) if user_token else None,
    })


async def set_usual_times(times: Any) -> dict[str, Any]:
    crews.update(times=list(times or []))
    return {"message": "Saved. The crew sees your runs at these times."}


async def delete_account() -> dict[str, Any]:
    from . import hosted, official

    _track("app_account_deleted")
    await hosted.forget_person(official.current() or "", via="app")
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


# -- not feeling 100% (Nov 21) ---------------------------------------------------
# One question on a day someone isn't up to the session they're looking at:
# a lighter run, an easier pace, a hard session made easy, a swap with a later
# day, skipping it, or a few days off. Each is worded for that session's own
# day, not the calendar's today. The rules are feeling.py's; here they're read
# against the calendar and written to Garmin, which sends them to the watch.
# Every change answers with the steps that undo it, so Undo needs nothing kept
# here. Words are for a busy, tired or flat day, never injury or anything medical.

WATCH_UPDATED = "Updated on your watch. Arrives at next sync."
WATCH_REMOVED = "Removed from your watch."
WATCH_FAILED = "Couldn't update your watch."


def not_100_offer(day: str, kind: str | None, status: str | None, today: date,
                  ready: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Whether a session's screen offers Not feeling 100%: today's or a later one, not run, not a race.

    A session already past is never offered. It stands out on a day Garmin
    reads readiness as low (today's session only), and never acts by itself.
    """
    d = _day_of(day)
    if not d or kind == "race" or str(status or "").startswith("done") or d < today:
        return None
    return {"prominent": bool(d == today and ready and ready.get("low"))}


async def _feeling_parts(day: str, schedule_id: int | None):
    plan, chosen, _, workout, error = await _session_parts(day, schedule_id)
    if error:
        raise EditError(error)
    if not chosen or not chosen.get("schedule_id"):
        raise EditError(f"Nothing is planned on {day}.")
    if str(chosen.get("status") or "").startswith("done"):
        raise EditError("That session is already done.")
    if chosen.get("type") == "race":
        raise EditError("Races stay where they are.")
    d = date.fromisoformat(day)
    if d < date.today():
        raise EditError("That day has passed.")
    totals = training_plan.session_totals(workout, training_plan.easy_speed(plan)) if workout else None
    return plan, chosen, workout, d, totals or {}


async def _easy_pace(plan: Mapping[str, Any], d: date) -> list[str] | None:
    """The plan's easy pace this week: an easy run's own target, else the runner's usual easy pace."""
    monday = d - timedelta(days=d.weekday())
    easy = next((s for s in _sessions(plan) if s.get("type") == "easy" and s.get("kind", "run") == "run"
                 and (sd := _day_of(s.get("date"))) and monday <= sd <= monday + timedelta(days=6)
                 and s.get("workout_id")), None)
    workout = None
    if easy:
        try:
            workout = await _workout(easy["workout_id"])
        except Exception:  # noqa: BLE001 - the runner's own easy pace stands in
            workout = None
    return feeling.easy_target(workout, training_plan.easy_speed(plan))


def _the(name: str) -> str:
    return name if re.match(r"^(easy|long|recovery)\b", name, re.I) is None else name[:1].lower() + name[1:]


def _what(session: Mapping[str, Any], km: float | None) -> str:
    """How a line names a session: its distance when it has one ("12 km"), else its name."""
    return f"{km:g} km" if km else _the(_display(session.get("name")))


def _week_of(plan: Mapping[str, Any], d: date) -> Mapping[str, Any]:
    monday = (d - timedelta(days=d.weekday())).isoformat()
    return next((w for w in plan.get("weeks") or [] if w.get("starts") == monday), {})


def _km_of(s: Mapping[str, Any]) -> float:
    return float(s.get("planned_km") or 0) if s.get("kind", "run") == "run" else 0.0


def _totals_after(plan: Mapping[str, Any], off: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each touched week's planned km, before and after the sessions that come off."""
    out = []
    for monday in sorted({_day_of(s["date"]) - timedelta(days=_day_of(s["date"]).weekday()) for s in off}):
        week = _week_of(plan, monday)
        planned = float(week.get("planned_km") or 0)
        if not planned:
            continue
        gone = sum(_km_of(s) for s in off if _day_of(s["date"]) - timedelta(days=_day_of(s["date"]).weekday()) == monday)
        this = monday == date.today() - timedelta(days=date.today().weekday())
        out.append({"label": "This week" if this else f"Week of {monday.day} {MONTHS[monday.month - 1]}",
                    "from": round(planned), "to": round(max(0.0, planned - gone))})
    return out


def _race_said(plan: Mapping[str, Any]) -> Mapping[str, Any] | None:
    races = list(plan.get("races") or [])
    goal = _goal_race(plan)
    if goal and not any(r.get("date") == goal.get("date") for r in races):
        races.append(goal)
    return feeling.next_race(races, date.today())


async def not_100_options(day: str, schedule_id: int | None = None) -> dict[str, Any]:
    """What each choice would do to the session being looked at, said before anything changes."""
    today = date.today()
    plan, chosen, workout, d, totals = await _feeling_parts(day, schedule_id)
    kind = chosen.get("type")
    name = _display(chosen.get("name"))
    km, minutes = totals.get("km"), totals.get("minutes")
    whose = feeling.days(d, today)
    sessions = _sessions(plan)
    copied = EASIER_RE.search(str((workout or {}).get("description") or ""))
    options: list[dict[str, Any]] = []
    changed = "Already changed. Back to the original first."

    if copied:
        original = await _workout(int(copied.group(1)))
        options.append({"choice": "original", "label": "Back to the original",
                        "said": f"{whose} {_display((original or {}).get('workoutName') or 'session')} goes back as it was."})

    # Three or four choices, each a different size of change, by the kind of session (Nov 21):
    # a hard one: Make it easy, Lighter run, Swap, Skip; an easy run or anything else: Lighter run, Swap, Skip.
    choices: dict[str, dict[str, Any]] = {}

    # Make it easy: a hard session becomes an easy run of the same time.
    if kind in feeling.QUALITY:
        if copied:
            choices["easy"] = {"choice": "easy", "label": "Make it easy", "unavailable": changed}
        elif minutes:
            pace = await _easy_pace(plan, d)
            choices["easy"] = {"choice": "easy", "label": "Make it easy",
                               "said": f"{whose} {name} becomes an easy {minutes} min run"
                                       + (f" at {pace[0]}\u2013{pace[1]}/km." if pace else ", at an easy pace.")}

    # Lighter run: the same pace, a fifth shorter.
    lighter = None if copied else feeling.lighter_run(workout, EASIER_MARK)
    if copied and kind != "strength":
        choices["lighter"] = {"choice": "lighter", "label": "Lighter run", "unavailable": changed}
    elif lighter:
        after = training_plan.session_totals(lighter, training_plan.easy_speed(plan)) or {}
        said = (f"{whose} {km:g} km becomes {after['km']:g} km, at the same pace." if km and after.get("km")
                else f"{whose} {minutes} min becomes {after['minutes']} min, at the same pace."
                if minutes and after.get("minutes") else f"{whose} {_the(name)} is a fifth shorter, at the same pace.")
        choices["lighter"] = {"choice": "lighter", "label": "Lighter run", "said": said}

    # Swap with a later day of the same week: a hard session or long run with an easy day,
    # an easy run onto a free day.
    if kind in feeling.QUALITY | {"long"} | feeling.EASY:
        found = feeling.swap_day(sessions, chosen)
        if found:
            to, back = found
            choices["swap"] = {"choice": "swap", "label": f"Swap with {feeling.day_word(to, today)}",
                               "said": (f"{feeling.days(to, today)} {_the(_display(back.get('name')))} comes to "
                                        f"{feeling.day_word(d, today)}, and the {_the(name)} moves to {feeling.day_word(to, today)}."
                                        if back else f"The {_the(name)} moves to {feeling.day_word(to, today)}, and "
                                        f"{feeling.day_word(d, today)} becomes a rest day.")}
        else:
            choices["swap"] = {"choice": "swap", "label": "Swap with a later day",
                               "unavailable": "No free day later this week." if kind in feeling.EASY
                               else "No later day this week keeps hard days apart."}

    # Skip this run: it comes off, and the week's total drops by its distance.
    week = _totals_after(plan, [dict(chosen, planned_km=km or chosen.get("planned_km"))])
    choices["skip"] = {"choice": "skip", "label": "Skip this run",
                       "said": f"{whose} {_what(chosen, km)} comes off."
                               + (f" {week[0]['label']}: {week[0]['from']} km \u2192 {week[0]['to']} km." if week and km else "")}

    order = ("easy", "lighter", "swap", "skip") if kind in feeling.QUALITY else ("lighter", "swap", "skip")
    options += [choices[c] for c in order if c in choices]

    # A few days off: a bigger decision than today's run, so the app shows it apart, as a quieter
    # link. Each span says what comes off, each week's new total, and the first run back lighter.
    race = _race_said(plan)
    # The taper note is about the A race, the one the plan builds to, wherever the next race is.
    goal = _goal_race(plan)
    spans = []
    for n in feeling.DAYS_OFF:
        off, back, races = feeling.days_off(sessions, d, n)
        # The session being looked at counts at the distance its own line says, so one sheet never shows two.
        off = [dict(s, planned_km=km) if s is chosen and km else s for s in off]
        last = d + timedelta(days=n - 1)
        touched = feeling.taper_touched(goal, d, last)
        spans.append(drop_empty({
            "days": n, "label": f"{n} days",
            "off": [{"day": feeling.short_day(_day_of(s["date"])), "name": _display(s.get("name")),
                     "km": round(_km_of(s), 1) or None} for s in off] or None,
            "totals": _totals_after(plan, off) or None,
            "back": f"{feeling.days(_day_of(back['date']), today)} {_the(_display(back.get('name')))} becomes a lighter run."
                    if back else None,
            "stays": [f"The {_display(r.get('name'))} stays." for r in races] or None,
            "note": (f"This touches your {_display(goal.get('name'))} {touched}; consider asking {assistant()}."
                     if touched else None),
            "said": f"{len(off)} session{'s' if len(off) != 1 else ''} come{'s' if len(off) == 1 else ''} off.",
        }))
    options.append({"choice": "days_off", "label": "A few days off", "spans": spans})

    # Talk it through: a chat with the week filled in. The app builds the link; it calls no model.
    monday = d - timedelta(days=d.weekday())
    this_week = [s for s in sessions if (sd := _day_of(s.get("date"))) and monday <= sd <= monday + timedelta(days=6)]
    who = assistant()
    options.append({"choice": "ask", "label": f"Talk it through with {who}", "assistant": who,
                    "ask": _ask_links(feeling.talk_it_through(this_week, race, _display))})

    return {"date": day, "name": name, "schedule_id": chosen.get("schedule_id"), **_type(kind),
            "day": feeling.day_word(d, today), "title": "Not feeling 100%?",
            "sub": "Busy, tired or a bit flat? Pick what fits. Nothing changes until you confirm.",
            "options": options}


async def _place_then_lift(workout_id: int, on: str, old_schedule: int) -> Any:
    """Put a workout on a day, then take the old one off: in that order, so a refusal never leaves a day empty.

    If Garmin refuses the placing, nothing changed. If it refuses taking the
    old one off, the new one is taken back off before the error goes up.
    """
    placed = await server_mod._call(lambda c: c.schedule_workout(int(workout_id), on)) or {}
    new_schedule = placed.get("workoutScheduleId") or placed.get("id")
    try:
        await server_mod._call(lambda c: c.unschedule_workout(int(old_schedule)))
    except Exception:
        if new_schedule:
            try:
                await server_mod._call(lambda c: c.unschedule_workout(int(new_schedule)))
            except Exception:  # noqa: BLE001 - the error below is the one to show
                log.exception("not feeling 100: couldn't take back a placed session")
        raise
    return new_schedule


async def _replace(chosen: Mapping[str, Any], made: Mapping[str, Any], day: str) -> dict[str, Any]:
    """Upload a changed copy and put it in the original's place: on the calendar, so on the watch at its next sync."""
    uploaded = await server_mod._call(lambda c: c.upload_workout(dict(made))) or {}
    new_id = uploaded.get("workoutId") or uploaded.get("id")
    if not new_id:
        raise EditError("Garmin returned no workout id.")
    try:
        placed = await _place_then_lift(int(new_id), day, int(chosen["schedule_id"]))
    except Exception:
        try:
            await server_mod._call(lambda c: c.delete_workout(int(new_id)))
        except Exception:  # noqa: BLE001 - a copy left in the library is untidy, not wrong
            log.warning("couldn't delete the copy %s", new_id)
        raise
    return {"remove": placed, "put": {"workout_id": int(chosen["workout_id"]), "date": day}, "delete": int(new_id)}


async def _move_to(s: Mapping[str, Any], to: str) -> dict[str, Any]:
    placed = await _place_then_lift(int(s["workout_id"]), to, int(s["schedule_id"]))
    return {"remove": placed, "put": {"workout_id": int(s["workout_id"]), "date": str(s["date"])}}


async def _take_off(s: Mapping[str, Any]) -> dict[str, Any]:
    await server_mod._call(lambda c: c.unschedule_workout(int(s["schedule_id"])))
    return {"put": {"workout_id": int(s["workout_id"]), "date": str(s["date"])}}


async def run_undo(steps: list[Mapping[str, Any]]) -> int:
    """Put a Not feeling 100% change back: each step takes a session off, puts one back, or deletes a copy.

    A copy is deleted only when it carries the line Daash wrote on it, so a
    step can't remove a workout someone made themselves.
    """
    done = 0
    for step in steps:
        if step.get("remove"):
            await server_mod._call(lambda c: c.unschedule_workout(int(step["remove"])))
        put = step.get("put") or {}
        if put.get("workout_id") and put.get("date"):
            await server_mod._call(lambda c: c.schedule_workout(int(put["workout_id"]), str(put["date"])))
            done += 1
        if step.get("delete"):
            copy = await server_mod._call(lambda c: c.get_workout_by_id(int(step["delete"]))) or {}
            if EASIER_RE.search(str(copy.get("description") or "")):
                try:
                    await server_mod._call(lambda c: c.delete_workout(int(step["delete"])))
                except Exception:  # noqa: BLE001 - a copy left in the library is untidy, not wrong
                    log.warning("couldn't delete the copy %s", step["delete"])
    server_mod.session.forget()
    return done


def _watch_row(s: Mapping[str, Any], status: str, name: str | None = None, on: str | None = None) -> dict[str, Any]:
    """One session the change touched, as the week view marks it: updated on the watch, or removed from it."""
    return {"date": on or str(s["date"]), "name": name or _display(s.get("name")), "status": status}


async def not_100_apply(day: str, schedule_id: int | None, choice: str, days: int | None = None) -> dict[str, Any]:
    """Make the change chosen, write it to Garmin, and say what changed and what the watch gets.

    Half a change is put back if Garmin refuses a step, so a failure leaves
    the week, and the watch, as they were.
    """
    today = date.today()
    plan, chosen, workout, d, totals = await _feeling_parts(day, schedule_id)
    kind = chosen.get("type")
    name = _display(chosen.get("name"))
    km, minutes = totals.get("km"), totals.get("minutes")
    whose = feeling.days(d, today)
    sessions = _sessions(plan)
    undo: list[dict[str, Any]] = []
    watch: list[dict[str, Any]] = []
    copied = EASIER_RE.search(str((workout or {}).get("description") or ""))
    if copied and choice in ("lighter", "easy"):
        raise EditError("Already changed. Back to the original first.")
    try:
        if choice == "original":
            if not copied:
                raise EditError("This is already the original session.")
            original = await _workout(int(copied.group(1)))
            if not original:
                raise EditError("The original workout is no longer in Garmin.")
            await undo_easier(day, chosen.get("schedule_id"))
            watch.append(_watch_row(chosen, "updated", _display(original.get("workoutName"))))
            message = f"{whose} {_display(original.get('workoutName'))} is back as it was."
        elif choice == "lighter":
            made = feeling.lighter_run(workout, EASIER_MARK)
            if not made:
                raise EditError("This session has nothing to shorten.")
            undo.append(await _replace(chosen, made, day))
            after = training_plan.session_totals(made, training_plan.easy_speed(plan)) or {}
            watch.append(_watch_row(chosen, "updated", _display(made["workoutName"])))
            message = (f"{whose} {km:g} km is now {after['km']:g} km." if km and after.get("km")
                       else f"{whose} {_the(name)} is now a fifth shorter.")
        elif choice == "easy":
            if kind not in feeling.QUALITY:
                raise EditError("Only a hard session can be made easy.")
            made = feeling.make_it_easy(workout, minutes, await _easy_pace(plan, d), EASIER_MARK)
            if not made:
                raise EditError("This session's length couldn't be read.")
            undo.append(await _replace(chosen, made, day))
            watch.append(_watch_row(chosen, "updated", _display(made["workoutName"])))
            message = f"{whose} {name} is now an easy {max(10, round(float(minutes)))} min run."
        elif choice == "swap":
            found = feeling.swap_day(sessions, chosen)
            if not found:
                raise EditError("No free day later this week." if kind in feeling.EASY
                                else "No later day this week keeps hard days apart.")
            to, back = found
            undo.append(await _move_to(chosen, to.isoformat()))
            watch.append(_watch_row(chosen, "updated", on=to.isoformat()))
            if back is not None:
                undo.append(await _move_to(back, day))
                watch.append(_watch_row(back, "updated", on=day))
            message = (f"The {_the(name)} moved to {feeling.day_word(to, today)}, and "
                       + (f"the {_the(_display(back.get('name')))} is "
                          f"{'today' if d == today else 'on ' + feeling.day_word(d, today)}."
                          if back is not None else f"{feeling.day_word(d, today)} is a rest day."))
        elif choice == "skip":
            undo.append(await _take_off(chosen))
            watch.append(_watch_row(chosen, "removed"))
            week = _totals_after(plan, [dict(chosen, planned_km=km or chosen.get("planned_km"))])
            message = (f"{whose} {_what(chosen, km)} is off."
                       + (f" {week[0]['label']} is now {week[0]['to']} km." if week and km else ""))
        elif choice == "days_off":
            if days not in feeling.DAYS_OFF:
                raise EditError("Pick 2, 3 or 5 days.")
            off, back, races = feeling.days_off(sessions, d, days)
            for s in off:
                undo.append(await _take_off(s))
                watch.append(_watch_row(s, "removed"))
            server_mod.session.forget()
            bits = [f"{len(off)} session{'s' if len(off) != 1 else ''} off for {days} days"]
            if back:
                back_workout = await _workout(back.get("workout_id"))
                made = feeling.lighter_run(back_workout, EASIER_MARK) if back_workout and not EASIER_RE.search(
                    str(back_workout.get("description") or "")) else None
                if made:
                    undo.append(await _replace(back, made, str(back["date"])))
                    watch.append(_watch_row(back, "updated", _display(made["workoutName"])))
                    bits.append(f"{feeling.days(_day_of(back['date']), today)} "
                                f"{_the(_display(back.get('name')))} is a lighter run")
            if races:
                bits.append(" and ".join(f"the {_display(r.get('name'))}" for r in races) + " stays")
            message = _sentence(". ".join(bits))
        else:
            raise EditError("Unknown choice.")
    except Exception:
        # Half a change is worse than none: put back what was done, then say what went wrong.
        if undo:
            try:
                await run_undo(list(reversed(undo)))
            except Exception:  # noqa: BLE001 - the error below is the one to show
                log.exception("not feeling 100%: couldn't put back a half-made change")
        raise
    finally:
        server_mod.session.forget()
    updated = any(w["status"] == "updated" for w in watch)
    return {"message": message, "watch": watch, "watch_said": WATCH_UPDATED if updated else WATCH_REMOVED,
            "undo": list(reversed(undo)), "sessions": len(watch)}


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

APP_EVENTS = {"ask_clicked", "ask_chosen", "ask_chat_saved", "share_clicked", "signed_in", "installed", "viewed",
              "build_opened", "build_step", "build_action",
              # The home-screen and notifications test (Nov 10).
              "home_prompt_shown", "home_prompt_tapped", "home_prompt_dismissed",
              "push_prompt_shown", "push_prompt_dismissed", "push_denied",
              # Nov 11: the hint that a session can be moved or made easier.
              "edit_hint_shown", "edit_hint_dismissed",
              # Nov 13: a week opened from its bar on the Block view.
              "block_week_opened",
              # Nov 15: how long an open took to its first screen.
              "ready",
              # Nov 23: the Get your plan screen.
              "plan_choice_shown", "plan_form_step", "plan_handoff_opened"}
# The screens a view can name: the app's own GET routes.
APP_SCREENS = {"today", "session", "run", "plan", "crew", "you", "build"}


def _track(event: str, properties: Mapping[str, Any] | None = None, user_token: str | None = None) -> None:
    from . import analytics, official

    token = user_token or official.current()
    user = store.get_user(token) if token else None
    if user is None:
        analytics.capture(event, None, dict(properties or {}))
        return
    analytics.capture(event, user.email_hash or token, {
        "account": user.email_masked, **dict(properties or {})})


def _layout(request: Request) -> str | None:
    found = request.headers.get("x-daash-layout", "")
    return found if found in ("phone", "desktop") else None


def _home(request: Request) -> bool | None:
    """True when the app was opened from the home screen, not a browser tab (Nov 10)."""
    return True if request.headers.get("x-daash-home") == "1" else None


_FROM = re.compile(r"[a-z_]{1,24}")


def _came_link(request: Request) -> str | None:
    """Which link of an email opened the app ("primary_button", "day_row"), on its first screen."""
    found = request.headers.get("x-daash-link", "")
    return found if _FROM.fullmatch(found) else None


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
        pulled = request.method == "GET" and request.headers.get("x-daash-fresh") == "1"
        if pulled:
            # Pulled down to refresh (Nov 13): the person asked for Garmin's
            # latest, so nothing remembered from earlier reads answers for it.
            from . import session as session_mod
            session_mod.session.forget()
        try:
            response = await handler(request)
        except Exception:  # noqa: BLE001 - one screen's failure is reported, never a bare 500
            log.exception("app route %s failed", request.url.path)
            _track("app_failed", {"screen": screen, "layout": _layout(request)})
            return _json({"error": "Daash couldn't load this just now. Try again in a moment."}, 500)
        # A screen fetched ahead of a tap (the app warms the other tabs) isn't one opened.
        polled = request.url.path.endswith(("/build/send", "/race-prompt")) or "/not100/" in request.url.path
        if request.method == "GET" and request.headers.get("x-daash-ahead") != "1" and not polled:
            # How long the screen waited for Daash, and on its own thread: the
            # account lookups are reads a screen shouldn't wait for.
            from . import official
            seen = drop_empty({"screen": screen, "layout": _layout(request), "home_screen": _home(request),
                               "ok": response.status_code < 400,
                               "seconds": round(time.monotonic() - started, 2), "from": _came_from(request),
                               # Nov 15: the same word under the name the setup funnel uses.
                               "source": _came_from(request),
                               # Nov 16: which link of the weekly email opened it.
                               "link": _came_link(request),
                               "utm_source": "weekly_email" if _came_from(request) == "weekly_email" else None,
                               "pulled": pulled or None})
            threading.Thread(target=_track, args=("app_viewed", seen, official.current()), daemon=True).start()
            if str(seen.get("from") or "").startswith("push_"):
                # push_opened (Nov 13): a notification tapped. Only the first
                # screen carries "from", so each tap counts once.
                opened = {"kind": seen["from"][len("push_"):], "screen": screen}
                threading.Thread(target=_track, args=("push_opened", opened, official.current()),
                                 daemon=True).start()
        return response

    return answered


def _build() -> str:
    """The app's build: a short fingerprint of its script and styles. The page
    loads them by it, and every API answer carries it, so an app left open
    across a deploy knows to reload (Nov 15)."""
    digest = hashlib.sha256()
    for name in ("app.js", "app.css"):
        try:
            digest.update((APP_DIR / name).read_bytes())
        except OSError:
            pass
    return digest.hexdigest()[:12]


APP_BUILD = _build()


def _json(body: Any, status: int = 200) -> JSONResponse:
    if isinstance(body, dict) and body.get("error") and status == 200:
        status = 502
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store", "X-Daash-Build": APP_BUILD})


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
    return _json(await _credited(await today_payload()))


async def _credited(body: Any, crew: bool = False) -> Any:
    """A screen's answer with Garmin's credit, "Garmin [device model]", for its
    title: the watch last used, or "Garmin" alone for a crew's runs, which come
    from several people's watches (Garmin's API brand guidelines, Nov 10)."""
    if isinstance(body, dict) and "error" not in body and "credit" not in body:
        try:
            device = None if crew else await server_mod._last_device()
        except Exception:  # noqa: BLE001 - "Garmin" alone is still a credit
            device = None
        body["credit"] = share_mod.attribution(device)
    return body


@mcp.custom_route(API_PREFIX + "session/{day}", methods=["GET"])
@_safe
async def api_session(request: Request) -> Response:
    day = _day(request)
    if not day:
        return _json({"error": "A date like 2026-11-03."}, 400)
    return _json(await _credited(await session_payload(day, _schedule_id(request))))


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
    return _json(await _credited(await plan_payload(week if week and _DAY_RE.match(week) else None)))


@mcp.custom_route(API_PREFIX + "crew", methods=["GET"])
@_safe
async def api_crew(request: Request) -> Response:
    try:
        return _json(await _credited(await crew_payload("next" if request.query_params.get("week") == "next" else None), crew=True))
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
        _track("app_viewed", drop_empty({"screen": screen, "layout": _layout(request), "home_screen": _home(request),
                                         "ok": said.get("ok") is not False, "preloaded": True}))
        return _json({"ok": True})
    if name in ("plan_choice_shown", "plan_form_step", "plan_handoff_opened"):
        # Nov 23: known words and small numbers only.
        screen = said.get("screen") if said.get("screen") in ("today", "plan") else None
        if name == "plan_choice_shown":
            first = said.get("first")
            if first not in store.PLAN_CHOICES:
                return _json({"error": "Unknown order."}, 400)
            _track("plan_choice_shown", drop_empty({"first": first, "screen": screen, "layout": _layout(request)}))
        elif name == "plan_form_step":
            step = said.get("step")
            if step not in (1, 2, 3):
                return _json({"error": "Unknown step."}, 400)
            _track("plan_form_step", drop_empty({"step": step, "screen": screen, "prefilled": said.get("prefilled") is True or None}))
        else:
            to = said.get("assistant")
            if to not in ("claude", "chatgpt"):
                return _json({"error": "Unknown assistant."}, 400)
            _track("plan_handoff_opened", drop_empty({"assistant": to, "screen": screen,
                                                      "race": said.get("race") if said.get("race") in (*RACE_WORDS, "other", "none") else None}))
        return _json({"ok": True})
    if name == "ready":
        # Numbers and a few known words only: what the wait was, and why.
        ms = said.get("ms")
        screen = str(said.get("screen") or "")
        if not isinstance(ms, (int, float)) or not 0 <= ms <= 120000 or screen not in APP_SCREENS:
            return _json({"error": "Unknown wait."}, 400)
        net = str(said.get("net") or "")
        _track("app_ready", drop_empty({
            "ms": int(ms), "seconds": round(ms / 1000, 2), "screen": screen, "saved": said.get("saved") is True,
            "shell": said.get("shell") if said.get("shell") in ("phone", "network") else None,
            "worker": said.get("worker") is True, "net": net if net in ("slow-2g", "2g", "3g", "4g") else None,
            "source": said.get("source") if _FROM.fullmatch(str(said.get("source") or "")) else None,
            "layout": _layout(request), "home_screen": _home(request)}))
        return _json({"ok": True})
    # choice: Back to my chat or New chat; saved: whether a chat was remembered.
    keep = {k: str(v)[:40] for k, v in said.items() if k in _EVENT_KEYS}
    _track(f"app_{name}", drop_empty({**keep, "layout": _layout(request), "home_screen": _home(request)}))
    return _json({"ok": True})


@mcp.custom_route(API_PREFIX + "push", methods=["GET", "POST"])
async def api_push(request: Request) -> Response:
    """Notifications for this phone: GET the key to ask with, POST to turn them on or off (Nov 10).

    Not wrapped in _safe: asking for the key isn't a screen opened.
    """
    try:
        return await _push(request)
    except Exception:  # noqa: BLE001 - reported, never a bare 500
        log.exception("app push route failed")
        return _json({"error": "Daash couldn't set up notifications just now. Try again in a moment."}, 500)


HEADS_UP_SAID = {
    "evening": "You'll hear about a session at 8pm the evening before.",
    "6": "You'll hear about a session at 6am that morning.",
    "7": "You'll hear about a session at 7am that morning.",
    "8": "You'll hear about a session at 8am that morning.",
}


async def _push(request: Request) -> Response:
    from . import official, push

    user_token = official.current()
    if request.method == "GET":
        on = await asyncio.to_thread(store.has_push, user_token) if user_token else False
        when = await asyncio.to_thread(store.heads_up_for, user_token) if on else None
        return _json(drop_empty({"key": await asyncio.to_thread(push.public_key), "on_somewhere": on, "heads_up": when}))
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    if body.get("off"):
        endpoint = str(body.get("endpoint") or "")
        gone = await asyncio.to_thread(store.drop_push, endpoint, user_token) if endpoint and user_token else False
        if gone:
            _track("app_push_off", drop_empty({"layout": _layout(request), "home_screen": _home(request)}))
        return _json({"ok": True})
    if "heads_up" in body:
        when = str(body.get("heads_up") or "")
        if when not in store.HEADS_UP or not user_token:
            return _json({"error": "That isn't one of the times."}, 400)
        if not await asyncio.to_thread(store.set_heads_up, user_token, when):
            return _json({"error": "Turn notifications on first."}, 400)
        _track("app_push_time", {"when": when})
        return _json({"ok": True, "heads_up": when, "message": HEADS_UP_SAID[when]})
    found = push.valid_subscription(body.get("subscription"))
    if not found or not user_token:
        return _json({"error": "This phone's notification address didn't come through. Try again."}, 400)
    tz = str(body.get("tz") or "")[:64] or None
    if tz:
        try:
            ZoneInfo(tz)
        except Exception:  # noqa: BLE001 - an unknown zone is left out, London is assumed
            tz = None
    await asyncio.to_thread(store.save_push, user_token, *found, tz)
    _track("app_push_on", drop_empty({"layout": _layout(request), "home_screen": _home(request), "tz": tz}))
    return _json({"ok": True})


# What the page may say about an event: choices and counts, never a name, a date or a time typed in.
_EVENT_KEYS = ("to", "opens", "where", "method", "choice", "saved",
               # The plan builder: where it was opened from, how far, what was picked, what got in the way.
               "from", "step", "resumed", "action", "reason", "days", "style", "amount", "gentle", "races", "dist",
               "replace", "open", "level", "typed", "goal", "verdict", "weeks")


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
        _track("app_edit", drop_empty({"action": action, "via": _via(body), "ok": False, "layout": _layout(request),
                                       "home_screen": _home(request)}))
        return _json({"error": str(exc)}, 409)
    _track("app_edit", drop_empty({"action": action, "via": _via(body), "ok": True, "layout": _layout(request),
                                   "home_screen": _home(request)}))
    if action in ("move", "easier", "undo", "resend"):
        # Each of these writes the session to the calendar again, so to the watch.
        _watch_sent(f"app_{action}", 1)
    return _json(result)


@mcp.custom_route(API_PREFIX + "not100/{day}", methods=["GET"])
@_safe
async def api_not_100(request: Request) -> Response:
    """What each Not feeling 100% choice would do to this week, before anything changes."""
    day = _day(request)
    if not day:
        return _json({"error": "A date like 2026-11-03."}, 400)
    # Opened, from which screen and whether it stood out on a low-readiness day: a sheet, not a screen view.
    _track("app_not_100_opened", drop_empty({
        "from": request.query_params.get("from") if request.query_params.get("from") in ("today", "session") else None,
        "prominent": request.query_params.get("prominent") == "1" or None,
        "layout": _layout(request), "home_screen": _home(request)}))
    try:
        return _json(await not_100_options(day, _schedule_id(request)))
    except EditError as exc:
        return _json({"error": str(exc)}, 409)


@mcp.custom_route(API_PREFIX + "not100/{day}", methods=["POST"])
@_safe
async def api_not_100_apply(request: Request) -> Response:
    day = _day(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    choice = str(body.get("choice") or "")
    try:
        days = int(body["days"]) if body.get("days") is not None else None
    except (TypeError, ValueError):
        days = None
    if not day or choice not in NOT_100_CHOICES:
        return _json({"error": "Unknown choice."}, 400)
    said = drop_empty({"action": "not_100", "via": "button", "choice": choice,
                       "days": days if choice == "days_off" else None, "retry": body.get("retry") is True or None,
                       "layout": _layout(request), "home_screen": _home(request)})
    try:
        result = await not_100_apply(day, _schedule_id(request, body), choice, days)
    except EditError as exc:
        _track("app_edit", {**said, "ok": False})
        return _json({"error": str(exc)}, 409)
    except Exception:  # noqa: BLE001 - Garmin refused a write: nothing changed, the person can try again
        log.exception("not feeling 100: the change didn't reach Garmin")
        _track("app_edit", {**said, "ok": False, "watch_failed": True})
        return _json({"error": WATCH_FAILED, "watch_failed": True,
                      "detail": "Nothing changed. Try again in a moment."}, 502)
    _track("app_edit", {**said, "ok": True})
    if result.get("sessions"):
        _watch_sent("app_not_100", result["sessions"])
    return _json(result)


NOT_100_CHOICES = ("original", "lighter", "easy", "swap", "skip", "days_off")


def _undo_steps(raw: Any) -> list[dict[str, Any]] | None:
    """The steps a change answered with, checked: ids are numbers, dates are dates, nothing else."""
    if not isinstance(raw, list) or not raw or len(raw) > 30:
        return None
    steps = []
    for step in raw:
        if not isinstance(step, Mapping):
            return None
        clean: dict[str, Any] = {}
        try:
            for key in ("remove", "delete"):
                if step.get(key) is not None:
                    clean[key] = int(step[key])
            put = step.get("put")
            if put is not None:
                if not isinstance(put, Mapping) or not _DAY_RE.match(str(put.get("date") or "")):
                    return None
                clean["put"] = {"workout_id": int(put["workout_id"]), "date": str(put["date"])}
        except (KeyError, TypeError, ValueError):
            return None
        if clean:
            steps.append(clean)
    return steps or None


@mcp.custom_route(API_PREFIX + "not100-undo", methods=["POST"])
@_safe
async def api_not_100_undo(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    steps = _undo_steps((body or {}).get("undo") if isinstance(body, dict) else None)
    if not steps:
        return _json({"error": "Nothing to undo."}, 400)
    put_back = await run_undo(steps)
    _track("app_edit", drop_empty({"action": "not_100_undo", "via": "button", "ok": True, "layout": _layout(request),
                                   "home_screen": _home(request)}))
    if put_back:
        _watch_sent("app_not_100_undo", put_back)
    return _json({"message": "Back as it was.", "watch_said": WATCH_UPDATED})


def _watch_sent(source: str, sessions: int, user_token: str | None = None) -> None:
    """workout_sent_to_watch for a write the app made (Nov 13): the app's edits
    and its plan builder don't pass through the chat's tool hook."""
    from . import hosted, official

    token = user_token or official.current()
    if not token or token == "local":
        return
    try:
        hosted.sent_to_watch(token, store.get_user(token), source, sessions, not store.has_connect(token))
    except Exception:  # noqa: BLE001 - counting never fails the edit
        log.debug("workout_sent_to_watch not recorded", exc_info=True)


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
        asked = await request.json() or {}
    except ValueError:
        asked = {}
    code = str(asked.get("code") or "")
    source = str(asked.get("source") or "")
    source = source if _FROM.fullmatch(source) else None
    # Nov 16: which link of the weekly email, and that it came from it.
    link = str(asked.get("link") or "")
    came = drop_empty({"source": source, "link": link if _FROM.fullmatch(link) else None,
                       "utm_source": "weekly_email" if source == "weekly_email" else None})
    # Nov 23: a link given in a chat (its card, or the app card on a workout push)
    # is a deep link; one in an email is a magic link.
    method = "magic_link" if source == "weekly_email" else "deep_link"
    subject, result = store.redeem_handoff(code) if code else (None, "invalid")
    if result == "success" and store.get_user(subject) is None:
        result = "invalid"  # the account has gone since the link was made
    # Nov 15: what became of each magic link, against the person it was for.
    if asked.get("signed_in") and result == "success":
        # The app was signed in already: the link is spent, nothing changes.
        _track("app_magic_link_opened", {"result": "already_signed_in", **came}, subject)
        return _json({"ok": True})
    _track("app_magic_link_opened", {"result": result, **came}, subject if result != "invalid" else None)
    if result != "success":
        _track("app_sign_in_failed", {"method": method, "reason": result})
        if source == "weekly_email":
            # Email links are often opened hours later, after the code has gone.
            said = ("That link from your email has expired: it signs you in until midnight (UK time) on the day it's sent."
                    if result in ("expired", "invalid") else "That link from your email has already been used.")
        else:
            said = {"expired": "That link from the chat has expired: they last 15 minutes.",
                    "used": "That link from the chat has already been used.",
                    "invalid": "That link from the chat doesn't work."}[result]
        return _json({"error": said, "result": result}, 401)
    _track("app_signed_in", {"method": method, **came}, subject)
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


# -- building a plan: the six steps, the preview, and sending it to Garmin ------------------
# What Garmin knows fills each step in; the plan itself is made by
# plan_builder from the answers, the same way every time, and nothing is
# written until "Put it on my Garmin". See docs/plan-engine-spec.md. Preview
# accounts first; GARMIN_MCP_BUILDER=1 opens it, and Today's race card, to
# everyone.
BUILDER_ENV = "GARMIN_MCP_BUILDER"


def builder_on() -> bool:
    return preview.enabled() or os.environ.get(BUILDER_ENV, "").strip().lower() in ("1", "on", "true", "yes")


# What Garmin knows is read once and kept a few minutes, so each step's answer
# doesn't wait on Garmin again. Sends are kept while they run, by person.
_BUILD_KEPT_SECONDS = 15 * 60
_BUILD_INPUTS: dict[str, tuple[float, dict[str, Any], plan_builder.Fitness]] = BoundedDict(5000)
_SENDS: dict[str, dict[str, Any]] = BoundedDict(5000)
LEVEL_LINES = {"new": "Most weeks, for a few months", "regular": "Most weeks, for over a year",
               "exp": "Most weeks, for years"}
RACE_NAME_RE = re.compile(r"\brace\b|parkrun|\b5k\b|\b10k\b|half|marathon", re.IGNORECASE)
PREDICTION_LABELS = (("5k", "5K"), ("10k", "10K"), ("half_marathon", "Half"), ("marathon", "Marathon"))


class BuildUnavailable(Exception):
    pass


def _runs(activities: Any) -> list[dict[str, Any]]:
    """Runs, newest first, each with its day, km and seconds."""
    found = []
    for a in activities or []:
        day = training_plan._day(a.get("startTimeLocal"))
        key = (a.get("activityType") or {}).get("typeKey")
        if not day or plan_mod.category(key or "") != "run":
            continue
        found.append({"day": day, "km": float(a.get("distance") or 0) / 1000,
                      "secs": float(a.get("movingDuration") or a.get("duration") or 0),
                      "name": str(a.get("activityName") or ""),
                      "race": ((a.get("eventType") or {}).get("typeKey") == "race")
                      or bool(RACE_NAME_RE.search(str(a.get("activityName") or "")))})
    return sorted(found, key=lambda r: r["day"], reverse=True)


def _level(runs: list[dict[str, Any]], today: date) -> str:
    """New, regular or experienced, from how many weeks had a run, and how big the biggest month was."""
    monday = today - timedelta(days=today.weekday())
    weeks = {(monday - (r["day"] - timedelta(days=r["day"].weekday()))).days // 7 for r in runs}
    last_year = sum(1 for w in weeks if 1 <= w <= 52)
    two_years = sum(1 for w in weeks if 1 <= w <= 104)
    by_week: dict[int, float] = {}
    for r in runs:
        w = (monday - (r["day"] - timedelta(days=r["day"].weekday()))).days // 7
        by_week[w] = by_week.get(w, 0) + r["km"]
    best_month = max((sum(by_week.get(w + k, 0) for k in range(4)) / 4 for w in range(1, 101)), default=0)
    if two_years >= 75 and best_month >= 40:
        return "exp"
    if last_year >= 30:
        return "regular"
    return "new"


def _mode(values: list[int]) -> int | None:
    return max(set(values), key=lambda v: (values.count(v), -v)) if values else None


def _race_meta(e: Mapping[str, Any], primary: bool) -> str:
    km = e.get("distance_km")
    words = {5.0: "5 km", 10.0: "10 km", 21.1: "Half marathon", 42.2: "Marathon"}.get(
        round(km, 1) if km else None, f"{km:g} km" if km else "Distance not set")
    return f"{words} · Garmin's main race" if primary else words


async def _build_inputs(today: date) -> tuple[dict[str, Any], plan_builder.Fitness]:
    """Step 1's numbers and step 2's races, from Garmin, kept for a few minutes."""
    from . import official

    who = official.current() or "local"
    kept = _BUILD_INPUTS.get(who)
    if kept and time.time() - kept[0] < _BUILD_KEPT_SECONDS:
        return kept[1], kept[2]
    this_monday = today - timedelta(days=today.weekday())
    first = this_monday - timedelta(weeks=104)
    activities, fitness, months, plan = await asyncio.gather(
        server_mod._call(lambda c: c.get_activities_by_date(first.isoformat(), today.isoformat())),
        server_mod.get_fitness(),
        server_mod._calendar_months(today, today + timedelta(weeks=training_plan.RACE_WEEKS_AHEAD)),
        server_mod.get_plan(),
        return_exceptions=True)
    activities = activities if isinstance(activities, list) else []
    fitness = fitness if isinstance(fitness, dict) and not _error(fitness) else {}
    months = months if isinstance(months, list) else []
    plan = plan if isinstance(plan, dict) and not _error(plan) else {}
    runs = _runs(activities)

    weeks = training_plan.weekly_run_km(activities, this_monday, 4)
    avg = round(sum(w["run_km"] for w in weeks) / 4)
    recent = [r for r in runs if r["day"] >= this_monday - timedelta(weeks=6)]
    longest = max(recent, key=lambda r: r["km"], default=None)
    raced = next((r for r in runs if r["race"] and r["km"] >= 3 and r["secs"]
                  and r["day"] >= today - timedelta(weeks=12)), None)
    per_week = sorted(w["runs"] for w in weeks)
    days = min(6, max(3, per_week[len(per_week) // 2] if per_week else 3))
    eight = [r for r in runs if r["day"] >= this_monday - timedelta(weeks=8)]
    usual = [d for d, _ in sorted(((d, sum(1 for r in eight if r["day"].weekday() == d)) for d in range(7)),
                                  key=lambda x: -x[1]) if _ > 0]
    longs = []
    for w in range(6):
        start = this_monday - timedelta(weeks=w + 1)
        inside = [r for r in runs if start <= r["day"] < start + timedelta(days=7)]
        if inside:
            longs.append(max(inside, key=lambda r: r["km"])["day"].weekday())
    parkruns = [r["day"].weekday() for r in eight if "parkrun" in r["name"].lower()]

    predictions = {}
    for key, _ in PREDICTION_LABELS:
        found = ((fitness.get("race_predictions") or {}).get(key) or {}).get("time")
        if plan_builder.parse_time(found):
            predictions[{"half_marathon": "half"}.get(key, key)] = plan_builder.parse_time(found)
    lt = fitness.get("lactate_threshold") or {}
    fit = plan_builder.Fitness(
        predictions=predictions, threshold_pace=plan_builder.parse_time(lt.get("pace_per_km")),
        threshold_hr=int(lt["heart_rate_bpm"]) if lt.get("heart_rate_bpm") else None,
        recent_race={"name": "parkrun" if "parkrun" in raced["name"].lower() else raced["name"][:40] or "Race",
                     "date": raced["day"].isoformat(), "km": round(raced["km"], 2), "secs": raced["secs"]} if raced else None)

    events = training_plan.calendar_events(months, today, today + timedelta(weeks=training_plan.RACE_WEEKS_AHEAD))
    primary = next((e for e in events if e.get("primary")), None)
    if primary is None:
        big = [e for e in events if (e.get("distance_km") or 0) >= 10]
        primary = big[-1] if big else None
    races = []
    for i, e in enumerate(events):
        km = e.get("distance_km") or 0
        tier = "A" if e is primary else "B" if km >= 10 else "C"
        races.append({"id": f"g{i}", "key": _race_key(e["name"], e["date"]), "name": e["name"], "date": e["date"], "km": km or 5,
                      "meta": _race_meta(e, bool(e.get("primary"))), "tier": tier})

    # A plan running now: the next block starts the Monday after it ends. A plan
    # with nothing left on the calendar (finished, or removed) isn't running.
    season = _season(plan) if plan.get("current_plan") or plan.get("upcoming_plan") else None
    running = await _running_plan(plan, season, today)
    starts = plan_builder.first_monday(today)
    after = None
    if running:
        after = date.fromisoformat(running["ends"])
        if season and season["last"] > after:
            after = season["last"]
        starts = after + timedelta(days=7 - after.weekday())
    inputs = drop_empty({
        "avg_km": avg, "longest_km": round(longest["km"]) if longest else 0,
        "longest_on": _short_date(longest["day"]) if longest else None,
        "bars": [{"l": f"{date.fromisoformat(w['starts']).day} {MONTHS[date.fromisoformat(w['starts']).month - 1].upper()}",
                  "v": round(w["run_km"])} for w in weeks],
        "recent": {"name": fit.recent_race["name"], "on": _short_date(raced["day"]),
                   "time": plan_builder.clock(raced["secs"])} if raced else None,
        "days": days, "level": _level(runs, today),
        "predictions": [{"l": label, "v": plan_builder.clock(predictions[{"half_marathon": "half"}.get(key, key)])}
                        for key, label in PREDICTION_LABELS if {"half_marathon": "half"}.get(key, key) in predictions] or None,
        "races": races, "usual_days": usual[:6],
        "long_day": _mode(longs) if longs and longs.count(_mode(longs)) >= 4 else 6, "parkrun_day": _mode(parkruns) if len(parkruns) >= 3 else None,
        "faster_ok": bool(fit.threshold_hr), "starts": starts.isoformat(),
        "after": after.isoformat() if after else None,
        "after_said": _short_date(after) if after else None,
        "thin": not runs or runs[-1]["day"] > today - timedelta(weeks=4) or None,
        # No running from Garmin at all: step 1 starts from numbers typed in.
        "none": not [r for r in runs if r["day"] >= this_monday - timedelta(weeks=8)] or None,
    })
    inputs["level_line"] = LEVEL_LINES[inputs["level"]]
    goal = _goal_race(plan) if season else None
    if goal and goal.get("date"):
        inputs["plan_goal"] = _race_key(goal.get("name"), str(goal["date"])[:10])
    if running and running["labels"]:
        inputs["plan"] = running
    now = time.time()
    # Kept a few minutes for the next step; anyone's older inputs go (Nov 25).
    for key in [k for k, v in list(_BUILD_INPUTS.items()) if now - v[0] > _BUILD_KEPT_SECONDS]:
        _BUILD_INPUTS.pop(key, None)
    _BUILD_INPUTS[who] = (now, inputs, fit)
    return inputs, fit


async def _running_plan(plan: Mapping[str, Any], season: Mapping[str, Any] | None, today: date) -> dict[str, Any] | None:
    """What's on the calendar from today: its session dates, and the codes of the plans Daash can take off again.

    Read from the calendar (remembered from the plan's own read), not the plan
    answer: a block planned after the next one is a third plan, still on the watch.
    """
    items = await server_mod._plan_calendar(today, today + server_mod.PLAN_SCAN_AHEAD)
    ahead = [i for i in items if str(i.get("date") or "")[:10] >= today.isoformat()]
    if not ahead:
        return None
    coded = training_plan.plans_in(items)
    labels = sorted(code for code, sessions in coded.items()
                    if any(str(x.get("date") or "")[:10] >= today.isoformat() for x in sessions))
    return {"labels": labels, "dates": sorted(str(i["date"])[:10] for i in ahead),
            "ends": max(str(i["date"])[:10] for i in ahead)}


def _starts(inputs: Mapping[str, Any], replace: bool, today: date) -> tuple[date, date | None]:
    """Where a new plan starts: after the one running now, or next Monday in place of it."""
    if replace and inputs.get("plan"):
        return plan_builder.first_monday(today), None
    return date.fromisoformat(inputs["starts"]), date.fromisoformat(inputs["after"]) if inputs.get("after") else None


def _replacing(inputs: Mapping[str, Any], starts: date) -> dict[str, Any] | None:
    running = inputs.get("plan")
    if not running:
        return None
    count = sum(1 for d in running["dates"] if d >= starts.isoformat())
    return {"count": count, "from": _short_date(starts)} if count else None


def _answers(body: Any, inputs: Mapping[str, Any], after: date | None = None) -> plan_builder.Answers:
    raw = (body or {}).get("answers") if isinstance(body, dict) else None
    if not isinstance(raw, dict):
        raise plan_builder.BuildError("Send the answers.")
    return plan_builder.Answers.from_json({**raw, "after_plan": bool(after)})


async def build_payload(replace: bool = False) -> dict[str, Any]:
    if not builder_on():
        raise BuildUnavailable()
    today = date.today()
    inputs, _ = await _build_inputs(today)
    fun = _not_goal(inputs.get("races") or [])
    races = [{**r, "tier": "C"} if r.get("key") in fun else r for r in inputs.get("races") or []]
    starts, after = _starts(inputs, replace, today)
    # The dates a race can be planned for: from the plan's start to Garmin's 30 weeks.
    horizon = today + timedelta(weeks=training_plan.MAX_WEEKS_AHEAD)
    window = {"from": starts.isoformat(), "to": horizon.isoformat(),
              "said": f"Races from {_short_date(starts)} to {_short_date(horizon)} can be planned." if after else None}
    return {**{k: v for k, v in inputs.items() if k != "plan"}, "races": races, "starts": starts.isoformat(), "window": window,
            "after": after.isoformat() if after else None, "replacing": _replacing(inputs, starts) if replace else None,
            "today": today.isoformat(), "assistant": assistant(),
            "ask": _ask_links("Use Daash to build me a training plan. Ask me my goal and race date first.")}


async def build_plan_payload(body: Any) -> dict[str, Any]:
    """What a step needs from the answers so far: goal times, the amounts, and the whole preview."""
    if not builder_on():
        raise BuildUnavailable()
    today = date.today()
    inputs, fit = await _build_inputs(today)
    starts, after = _starts(inputs, bool((body or {}).get("replace")), today)
    a = _answers(body, inputs, after)
    out: dict[str, Any] = {"faster_ok": plan_builder.faster_allowed(a, fit)}
    goal = plan_builder.goal_race(a)
    if goal:
        out["chips"] = [c["time"] for c in plan_builder.goal_chips(goal.dist, goal.km, fit)]
        out["verdict"] = plan_builder.goal_verdict(plan_builder.goal_secs(a.goal_time, goal.dist), goal.dist, goal.km, fit)
        weeks_to = (goal.day - starts).days // 7 + 1
        out["too_close"] = weeks_to < plan_builder.MIN_WEEKS or None
        # After a plan running now, the next block can't reach a race before it starts;
        # and Garmin's calendar takes sessions up to 30 weeks ahead.
        horizon = today + timedelta(weeks=training_plan.MAX_WEEKS_AHEAD)
        if goal.day < starts and after:
            out["race_note"] = f"The next block starts {_short_date(starts)}, after the plan you're on. Pick a race after that."
        elif goal.day > horizon:
            out["race_note"] = (f"Garmin takes sessions up to 30 weeks ahead, so a plan can reach races until "
                                f"{_short_date(horizon)}. Come back nearer the time for this one.")
    if a.style == "pick":
        out["picked"] = plan_builder.pick_style(a, fit)[1]
    if a.injury == "hurts" or (goal and (out.get("too_close") or out.get("race_note"))):
        return out
    try:
        s = plan_builder.shape(a, today, starts)
    except plan_builder.BuildError as exc:
        return {**out, "problem": str(exc)}
    out["amounts"] = plan_builder.amounts(a, s, fit)
    if (body or {}).get("preview"):
        out["preview"] = plan_builder.preview(a, fit, today, starts, inputs.get("usual_days") or (),
                                              inputs.get("parkrun_day"))
    return out


def _free_label(goal: str, taken: set[str]) -> str:
    base = training_plan.normalise_label(None, goal)
    for label in [base, *(base[:2] + str(n) for n in range(2, 10)), *(f"B{n}" for n in range(1, 10))]:
        if label not in taken:
            return label
    return base


async def _send(who: str, args: dict[str, Any], resume: bool) -> None:
    job = _SENDS[who]
    done_before = job.get("created") or []
    # In place of the plan running now: its sessions from the new plan's start
    # come off first, the weeks already run stay. Asked again on a retry, it
    # only finds what's left.
    if job.get("replace"):
        from_token = server_mod.plan_remove_from.set(date.fromisoformat(job["replace"]["from"]))
        try:
            for label in job["replace"]["labels"]:
                gone = await server_mod.remove_plan(label, confirm=label)
                if isinstance(gone, dict) and (gone.get("error") or gone.get("failed")):
                    raise RuntimeError(str(gone.get("error") or gone.get("failed")))
        except Exception as exc:  # noqa: BLE001 - shown as Garmin's answer
            log.warning("plan builder: the old plan didn't come off: %s", exc)
            job.update(state="error", said=_garmin_said(str(exc)))
            _track_built(who, job, resume, "old plan stuck")
            return
        finally:
            server_mod.plan_remove_from.reset(from_token)

    def progress(created: list[dict[str, Any]], total: int) -> None:
        job["sent"] = len(done_before) + len(created)
        job["created"] = done_before + list(created)

    progress_token = server_mod.plan_progress.set(progress)
    resume_token = server_mod.plan_resume.set(resume)
    try:
        # The builder already takes off the Daash plan it replaces; runs from
        # elsewhere stay as before (Nov 18), until it can ask about them.
        result = await server_mod.create_plan(**{"existing": "keep", **args})
    except Exception as exc:  # noqa: BLE001 - shown as Garmin's answer, never a crash
        log.warning("plan builder send failed", exc_info=True)
        result = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        server_mod.plan_resume.reset(resume_token)
        server_mod.plan_progress.reset(progress_token)
    if isinstance(result, dict) and result.get("error"):
        log.warning("plan builder: the plan wasn't taken: %s", result["error"])
        job.update(state="error", said=_garmin_said(str(result["error"])))
        _track_built(who, job, resume, _fail_reason(str(result["error"])))
    else:
        job.update(state="done", sent=job["total"])
        _track_built(who, job, resume, None)
        _watch_sent("app_builder", int(job.get("total") or 0), who)


def _track_built(who: str, job: Mapping[str, Any], resume: bool, reason: str | None) -> None:
    # sent: how far it got before stopping, so a retry's share of the work shows.
    _track("app_plan_built", drop_empty({"ok": reason is None, "reason": reason, "weeks": job.get("weeks"),
                                         "sessions": job["total"], "sent": job.get("sent") or 0 if reason else None,
                                         "resumed": resume or None, "replace": bool(job.get("replace")) or None,
                                         "from": job.get("from")}), who)


def _fail_reason(error: str) -> str:
    """One or two words for why Garmin didn't take the plan: never the message itself."""
    if "429" in error or "Too Many" in error:
        return "garmin busy"
    if re.search(r"\b(401|403)\b", error) or "auth" in error.lower():
        return "signed out"
    if re.search(r"\b5\d\d\b", error) or "timeout" in error.lower() or "Timeout" in error:
        return "garmin down"
    if re.search(r"\b4\d\d\b", error):
        return "garmin refused"
    return "other"


def _garmin_said(error: str) -> str:
    if "429" in error or "Too Many" in error:
        return "GARMIN · 429 TOO MANY REQUESTS"
    found = re.search(r"\b([45]\d\d)\b", error)
    return f"GARMIN · {found.group(1)}" if found else "GARMIN DIDN'T ANSWER"


async def build_send(body: Any) -> dict[str, Any]:
    """Put the plan on the calendar, in the background: the app asks how it's going."""
    from . import official

    if not builder_on():
        raise BuildUnavailable()
    who = official.current() or "local"
    job = _SENDS.get(who)
    if job and job.get("state") == "sending":
        return _send_status(job)
    today = date.today()
    inputs, fit = await _build_inputs(today)
    replace = bool((body or {}).get("replace")) and bool(inputs.get("plan"))
    starts, after = _starts(inputs, replace, today)
    a = _answers(body, inputs, after)
    args = plan_builder.create_args(a, fit, today, starts, inputs.get("usual_days") or ())
    sessions = args["sessions"]
    if not sessions:
        raise plan_builder.BuildError("This plan has no sessions to send.")
    resume = bool((body or {}).get("retry")) and job is not None and job.get("state") == "error" and job.get("label")
    if resume:
        sent = {(c.get("date"), c.get("name")) for c in job.get("created") or []}
        args["sessions"] = [x for x in sessions if (x["date"], training_plan.tagged(x["name"], job["label"])) not in sent
                            and (x["date"], x["name"]) not in sent]
        args["label"] = job["label"]
        job.update(state="sending", said=None)
    else:
        first, last = date.fromisoformat(sessions[0]["date"]), date.fromisoformat(sessions[-1]["date"])
        taken = set(training_plan.plans_in(await server_mod._plan_calendar(first, last)))
        args["label"] = _free_label(args["goal"], taken)
        weeks = (date.fromisoformat(sessions[-1]["date"]) - starts).days // 7 + 1
        first_row = plan_builder.preview(a, fit, today, starts, inputs.get("usual_days") or ())["first"]
        job = _SENDS[who] = {"state": "sending", "sent": 0, "total": len(sessions), "label": args["label"],
                             "weeks": weeks, "first": first_row, "created": [], "first_date": sessions[0]["date"],
                             "replace": {"labels": inputs["plan"]["labels"], "from": starts.isoformat()} if replace else None,
                             "from": _from((body or {}).get("from"))}
    asyncio.create_task(_send(who, args, bool(resume)))
    _BUILD_INPUTS.pop(who, None)
    return _send_status(job)


# Where the builder was opened from, as the page says it: anything else is "other".
BUILD_FROM = {"today", "plan", "next_block", "nothing_ahead", "change", "resume", "race", "direct",
              # Nov 23: from the Get your plan screen.
              "choice_today", "choice_plan"}


def _from(said: Any) -> str | None:
    if not said:
        return None
    return said if said in BUILD_FROM else "other"


async def remove_running_plan(body: Any) -> dict[str, Any]:
    """Take the plan running now off the calendar from today, every block of it. Past runs stay."""
    from . import official

    if not builder_on():
        raise BuildUnavailable()
    if str((body or {}).get("confirm") or "") != "remove":
        raise plan_builder.BuildError("Confirm to remove the plan.")
    today = date.today()
    plan = await server_mod.get_plan()
    if _error(plan):
        return {"error": _error(plan)}
    running = await _running_plan(plan, _season(plan), today)
    if not running or not running["labels"]:
        raise plan_builder.BuildError("There's no plan from Daash on your calendar to remove.")
    removed = 0
    for label in running["labels"]:
        gone = await server_mod.remove_plan(label, confirm=label)
        if isinstance(gone, dict) and gone.get("error"):
            return {"error": str(gone["error"])}
        removed += (gone or {}).get("removed") or 0
    _BUILD_INPUTS.pop(official.current() or "local", None)
    _track("app_plan_removed", {"sessions": removed})
    return {"removed": removed, "message": f"Removed {removed} session{'s' if removed != 1 else ''}. "
            "Your runs so far stay in Garmin."}


def _send_status(job: Mapping[str, Any]) -> dict[str, Any]:
    return drop_empty({"state": job["state"], "sent": job.get("sent", 0), "total": job["total"],
                       "weeks": job.get("weeks"), "first": job.get("first"), "said": job.get("said"),
                       "first_date": job.get("first_date"), "replaced": bool(job.get("replace")) or None})


async def build_send_status() -> dict[str, Any]:
    from . import official

    if not builder_on():
        raise BuildUnavailable()
    job = _SENDS.get(official.current() or "local")
    return _send_status(job) if job else {"state": "none"}


@mcp.custom_route(API_PREFIX + "plan-choice", methods=["POST"])
@_safe
async def api_plan_choice(request: Request) -> Response:
    """Nov 23: which way to a plan someone picked, and where it sat on the screen."""
    from . import official

    try:
        body = await request.json() or {}
    except ValueError:
        body = {}
    option, position = body.get("option"), body.get("position")
    if option not in store.PLAN_CHOICES or position not in (1, 2):
        return _json({"error": "Unknown choice."}, 400)
    screen = body.get("screen") if body.get("screen") in ("today", "plan") else None
    user_token = official.current()
    user = store.get_user(user_token) if user_token else None
    if user is None:
        return _json({"error": "Not signed in."}, 401)
    store.record_plan_pick(user.email_hash or user_token, user_token, option, position)
    _track("plan_choice_picked", drop_empty({"option": option, "position": position, "screen": screen}))
    return _json({"ok": True})


@mcp.custom_route(API_PREFIX + "plan-choice/form", methods=["GET"])
@_safe
async def api_plan_choice_form(request: Request) -> Response:
    """What Garmin can fill in on the three steps: weekly km, usual days, long-run day, a race on the calendar."""
    try:
        inputs, _ = await _build_inputs(date.today())
    except Exception:  # noqa: BLE001 - the steps work blank
        log.debug("plan-choice prefill failed", exc_info=True)
        return _json({})
    race = next((r for r in inputs.get("races") or [] if r.get("tier") == "A"), None)
    word = {5: "5k", 10: "10k", 21: "half", 42: "marathon"}.get(round(float((race or {}).get("km") or 0))) if race else None
    return _json(drop_empty({
        "km": inputs.get("avg_km") if not inputs.get("none") else None,
        "days": inputs.get("usual_days") or None,
        "long_day": inputs.get("long_day"),
        "race": {"race": word or "other", "date": str(race["date"])[:10], "name": race.get("name")} if race else None,
    }))


@mcp.custom_route(API_PREFIX + "plan-choice/link", methods=["POST"])
@_safe
async def api_plan_choice_link(request: Request) -> Response:
    """The chat link from the three steps' answers. Builds a link only: the app asks no model anything."""
    try:
        body = await request.json() or {}
    except ValueError:
        body = {}
    prompt = plan_chat_prompt(body, date.today())
    return _json({"prompt": prompt, **_ask_links(prompt)})


@mcp.custom_route(API_PREFIX + "build", methods=["GET"])
@_safe
async def api_build(request: Request) -> Response:
    try:
        return _json(await build_payload(request.query_params.get("replace") == "1"))
    except BuildUnavailable:
        return _json({"error": "Building a plan here isn't open yet."}, 404)


@mcp.custom_route(API_PREFIX + "plan/remove", methods=["POST"])
@_safe
async def api_plan_remove(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    try:
        return _json(await remove_running_plan(body))
    except BuildUnavailable:
        return _json({"error": "Not open yet."}, 404)
    except plan_builder.BuildError as exc:
        return _json({"error": str(exc)}, 400)


@mcp.custom_route(API_PREFIX + "build/plan", methods=["POST"])
@_safe
async def api_build_plan(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    try:
        return _json(await build_plan_payload(body))
    except BuildUnavailable:
        return _json({"error": "Building a plan here isn't open yet."}, 404)
    except plan_builder.BuildError as exc:
        return _json({"error": str(exc)}, 400)


@mcp.custom_route(API_PREFIX + "build/send", methods=["GET", "POST"])
@_safe
async def api_build_send(request: Request) -> Response:
    try:
        if request.method == "GET":
            return _json(await build_send_status())
        try:
            body = await request.json()
        except ValueError:
            body = {}
        return _json(await build_send(body))
    except BuildUnavailable:
        return _json({"error": "Building a plan here isn't open yet."}, 404)
    except plan_builder.BuildError as exc:
        return _json({"error": str(exc)}, 400)


# -- a race on the Garmin calendar: the card on Today that offers a plan for it ----------
# A preview feature, with the builder. A race of 10K or more that no plan is
# building to gets one card; "Not now" folds it to a row for a week (twice,
# then it stays a row), and "It's not a goal race" ends it and makes the race
# a fun run in any plan built here.

RACE_PROMPT_MIN_KM = 9.5
RACE_PROMPT_FULL_WEEKS = 6
RACE_PROMPT_SNOOZE = 7 * 86400
RACE_PROMPT_REOPENS = 2
RACE_PROMPT_ACTIONS = {"cta", "snooze", "not_goal"}


def _race_key(name: Any, day: Any) -> str:
    """A race by its date and name, hashed: what the store keeps instead of either."""
    import hashlib

    return hashlib.sha256(f"{day}|{str(name or '').strip().lower()}".encode()).hexdigest()[:16]


def _not_goal(races: list[dict[str, Any]]) -> set[str]:
    me = crews.current()
    if not me or not races:
        return set()
    states = store.race_prompts(me.key, [r["key"] for r in races])
    return {k for k, v in states.items() if v["not_goal"]}


async def race_prompt_payload() -> dict[str, Any]:
    """The one race worth a card on Today, or nothing."""
    me = crews.current()
    if not builder_on() or me is None:
        return {}
    today = date.today()
    inputs, fit = await _build_inputs(today)
    starts = date.fromisoformat(inputs["starts"])
    horizon = today + timedelta(weeks=training_plan.MAX_WEEKS_AHEAD)
    after = date.fromisoformat(inputs["after"]) if inputs.get("after") else None
    found = []
    for r in inputs.get("races") or []:
        day = date.fromisoformat(r["date"])
        # 10K and up, inside what Garmin's calendar takes, not what the plan is for,
        # and with a plan running, only races after it ends.
        if (r.get("km") or 0) < RACE_PROMPT_MIN_KM or not today < day <= horizon or r["key"] == inputs.get("plan_goal"):
            continue
        if after and day <= after:
            continue
        found.append(r)
    if not found:
        return {}
    now = int(time.time())
    states = store.race_prompts(me.key, [r["key"] for r in found], now)
    for r in sorted(found, key=lambda x: x["date"]):
        state = states.get(r["key"]) or {}
        if state.get("not_goal"):
            continue
        made = _race_prompt(r, inputs, fit, today, starts, after)
        if not made:
            continue
        snoozes, snoozed_at = state.get("snoozes") or 0, state.get("snoozed_at") or 0
        card = snoozes == 0 or (snoozes <= RACE_PROMPT_REOPENS and now - snoozed_at >= RACE_PROMPT_SNOOZE)
        made["mode"] = "card" if card else "row"
        if card and store.race_prompt_shown(me.key, r["key"], now):
            _track("race_prompt_shown", {"case": made["case"], "weeks_out": made["weeks_out"]})
        return made
    return {}


def _race_prompt(r: Mapping[str, Any], inputs: Mapping[str, Any], fit: plan_builder.Fitness, today: date,
                 starts: date, after: date | None) -> dict[str, Any] | None:
    day = date.fromisoformat(r["date"])
    weeks_out = (day - today).days // 7
    answers = plan_builder.Answers.from_json({
        "avg_km": inputs.get("avg_km") or 0, "longest_km": inputs.get("longest_km") or 0, "level": inputs.get("level"),
        "races": [{"name": r["name"], "date": r["date"], "km": r["km"], "tier": "A"}],
        "days": inputs.get("days") or 4, "long_day": inputs.get("long_day", 6), "after_plan": bool(after)})
    try:
        s = plan_builder.shape(answers, today, starts)
        peak = plan_builder.peak_for(answers, s)
    except plan_builder.BuildError:
        return None
    when = _short_date(day)
    name = r["name"]
    short = lambda d: f"{d.day} {MONTHS[d.month - 1]}"  # noqa: E731
    if after:
        case, title = "after", f"{name} is after your plan ends"
        sub = (f"Your plan finishes {_short_date(after)}. Plan the next block now and it starts the Monday after, "
               "with an easy week first.")
        facts = [(short(after), "plan ends"), (f"{s.weeks} weeks", "next block"), (short(day), "race day")]
        cta, row = "Plan the next block", "After your current plan"
    elif weeks_out >= RACE_PROMPT_FULL_WEEKS:
        case, title = "no_plan", f"{name} is in {weeks_out} weeks"
        sub = "Enough time for a full plan, built from what you're running now."
        facts = [(f"{weeks_out} weeks", "to race day"), (f"{answers.days} days", "a week, like now"), (f"~{round(peak)} km", "peak week")]
        cta, row = "Build a plan", f"{weeks_out} weeks away"
    else:
        taper = s.phases.count("Taper")
        case, title = "close", f"{name} is in {weeks_out} weeks"
        sub = "Too close for a full plan. A short block keeps what you have and arrives fresh."
        facts = [(f"{weeks_out} weeks", "to race day"), (f"{s.weeks - taper} + {taper}", "sharpen, then taper"),
                 (f"~{round(peak)} km", "peak week")]
        cta, row = f"Build a {s.weeks}-week block", f"{weeks_out} weeks away"
    return {"key": r["key"], "case": case, "weeks_out": weeks_out, "name": name, "when": when, "title": title,
            "sub": sub, "facts": [{"v": v, "l": label} for v, label in facts], "cta": cta, "row_sub": row}


def race_prompt_action(body: Any) -> dict[str, Any]:
    me = crews.current()
    if not builder_on() or me is None:
        raise BuildUnavailable()
    key = str((body or {}).get("key") or "") if isinstance(body, dict) else ""
    action = str((body or {}).get("action") or "") if isinstance(body, dict) else ""
    if not re.fullmatch(r"[0-9a-f]{16}", key) or action not in RACE_PROMPT_ACTIONS:
        raise plan_builder.BuildError("Unknown race or action.")
    case = str(body.get("case") or "")[:10] or None
    if action == "snooze":
        store.race_prompt_snooze(me.key, key)
        _track("race_prompt_snoozed", drop_empty({"case": case}))
        return {"message": "Moved out of the way. We'll ask again in a week."}
    if action == "not_goal":
        store.race_prompt_not_goal(me.key, key)
        _track("race_prompt_not_goal", drop_empty({"case": case}))
        return {"message": "Got it. It's a fun run in any plan, and we won't ask again."}
    _track("race_prompt_cta", drop_empty({"case": case}))
    return {"ok": True}


@mcp.custom_route(API_PREFIX + "race-prompt", methods=["GET", "POST"])
@_safe
async def api_race_prompt(request: Request) -> Response:
    try:
        if request.method == "GET":
            return _json(await race_prompt_payload())
        try:
            body = await request.json()
        except ValueError:
            body = {}
        return _json(race_prompt_action(body))
    except BuildUnavailable:
        return _json({"error": "Not open yet."}, 404)
    except plan_builder.BuildError as exc:
        return _json({"error": str(exc)}, 400)


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
    return _file(path or "index.html", request.query_params.get("v"))


# Kept on the phone for good (Nov 15): the script and styles under this
# build's address never change, so a phone needn't ask again. A week for the
# fonts and icons, which change rarely and aren't worth a round trip each open.
FOREVER = "public, max-age=31536000, immutable"
A_WEEK = "public, max-age=604800"


def _file(path: str, version: str | None = None) -> Response:
    target = (APP_DIR / path).resolve()
    if APP_DIR not in target.parents and target != APP_DIR or not target.is_file():
        target = APP_DIR / "index.html"
    headers = {"Cache-Control": "no-cache"}
    if target.name in ("app.js", "app.css") and version and version == APP_BUILD:
        # Only this build's address: an older page asking for its own build
        # gets today's file, and that must not be kept under the old name.
        headers["Cache-Control"] = FOREVER
    elif target.parent.name in ("fonts", "icons"):
        headers["Cache-Control"] = A_WEEK
    if target.name == "index.html":
        # The script and styles by this build, so a new deploy is a new address.
        page = target.read_text().replace('src="/app/app.js"', f'src="/app/app.js?v={APP_BUILD}"') \
            .replace('href="/app/app.css"', f'href="/app/app.css?v={APP_BUILD}"')
        return Response(page, media_type=_TYPES[".html"], headers=headers)
    if target.name == "sw.js":
        headers["Service-Worker-Allowed"] = "/app/"
    return FileResponse(target, media_type=_TYPES.get(target.suffix), headers=headers)
