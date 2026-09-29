"""Render the training plan as a page: what was scheduled, what was run.

Garmin is the source of truth. Sessions the coach schedules through
create_workout land on the Garmin calendar, and completed runs land as
activities — both keyed to a date, so planned against actual falls out without
a second database.

The only thing kept locally is what Garmin has no idea about: a weekly mileage
target, and the random path segment the page is published under.

    .venv/bin/python -m garmin_mcp.plan
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .formatting import duration, km, pace_per_km
from .session import session

PROJECT = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("GARMIN_MCP_PLAN_CONFIG") or PROJECT / "plan-config.json")

WEEKS_BACK = 5
WEEKS_FORWARD = 2
WEEK_STRIP = 14
MONTH_STRIP = 6
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Used to turn a time-based planned step into a distance when the step carries
# no pace target. Roughly an easy pace; only affects the height of a planned bar.
DEFAULT_MPS = 3.2


def category(type_key: str) -> str:
    """Runs, strength, and everything else — the three colours on the chart."""
    key = (type_key or "").lower()
    if "running" in key:
        return "run"
    if "strength" in key or "training" in key and "running" not in key:
        return "strength"
    return "other"


def planned_distance_m(workout: dict[str, Any]) -> float:
    """Estimate how far a scheduled workout is, by walking its steps.

    Garmin's calendar carries no distance for a planned session, so it has to be
    derived. Distance steps count directly; time steps are converted using their
    own pace target where they have one.
    """
    total = 0.0

    def walk(steps: list[dict[str, Any]]) -> None:
        nonlocal total
        for step in steps or []:
            if step.get("type") == "RepeatGroupDTO":
                iterations = int(step.get("numberOfIterations") or 1)
                before = total
                walk(step.get("workoutSteps") or [])
                total += (total - before) * (iterations - 1)
                continue
            condition = (step.get("endCondition") or {}).get("conditionTypeKey")
            value = float(step.get("endConditionValue") or 0)
            if condition == "distance":
                total += value
            elif condition == "time":
                one, two = step.get("targetValueOne"), step.get("targetValueTwo")
                target = (step.get("targetType") or {}).get("workoutTargetTypeKey")
                speed = (one + two) / 2 if target == "pace.zone" and one and two else DEFAULT_MPS
                total += value * speed

    segments = workout.get("workoutSegments") or []
    for segment in segments:
        walk(segment.get("workoutSteps") or [])
    return total


def load_config() -> dict[str, Any]:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    config = {"weekly_target_km": 50}
    CONFIG.write_text(json.dumps(config, indent=2) + "\n")
    return config


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def collect() -> dict[str, Any]:
    """Pull the calendar and the activities covering the window."""
    today = date.today()
    history_start = monday_of(today) - timedelta(weeks=max(WEEK_STRIP, MONTH_STRIP * 5))
    start = monday_of(today) - timedelta(weeks=WEEKS_BACK)
    end = monday_of(today) + timedelta(weeks=WEEKS_FORWARD + 1) - timedelta(days=1)

    activities = session.run(
        lambda c: c.get_activities_by_date(history_start.isoformat(), today.isoformat())
    ) or []

    # The calendar is fetched per month, so cover every month the window touches.
    months, cursor = [], start.replace(day=1)
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    # Garmin's month view includes the days either side of the month, so a
    # session near a boundary comes back from two calls. Keyed by calendar id,
    # or every such session would be counted twice.
    seen: dict[Any, dict[str, Any]] = {}
    for year, month in months:
        try:
            payload = session.run(lambda c, y=year, m=month: c.get_scheduled_workouts(y, m))
        except Exception:
            continue
        items = payload if isinstance(payload, list) else (payload or {}).get("calendarItems", [])
        for item in items:
            if item.get("itemType") != "workout":
                continue
            key = item.get("id") or (item.get("date"), item.get("workoutId"))
            seen.setdefault(key, item)
    planned = list(seen.values())

    # Planned sessions need their distance looked up once each.
    distances: dict[int, float] = {}
    durations: dict[int, float] = {}
    for item in planned:
        workout_id = item.get("workoutId")
        if not workout_id or workout_id in distances:
            continue
        try:
            detail = session.run(lambda c, w=workout_id: c.get_workout_by_id(w)) or {}
            distances[workout_id] = planned_distance_m(detail)
            durations[workout_id] = float(detail.get("estimatedDurationInSecs") or 0)
        except Exception:
            distances[workout_id] = durations[workout_id] = 0.0

    return {"start": start, "history_start": history_start, "end": end,
            "today": today, "activities": activities,
            "planned": planned, "planned_distance": distances,
            "planned_duration": durations}


def organise(data: dict[str, Any]) -> dict[str, Any]:
    """Shape into sport totals, a week progression, and days of paired bars.

    Bars are sized by duration, not distance. Strength work has no distance, so
    a mileage bar could never show it honestly; time is the one unit every kind
    of session has.
    """
    actual: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    metres: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)

    for item in data["activities"]:
        day = (item.get("startTimeLocal") or "")[:10]
        if not day:
            continue
        kind = category((item.get("activityType") or {}).get("typeKey"))
        actual[day][kind] += float(item.get("duration") or 0)
        if kind == "run":
            metres[day] += float(item.get("distance") or 0)
            counts[day] += 1

    planned_secs: dict[str, float] = defaultdict(float)
    planned_m: dict[str, float] = defaultdict(float)
    for item in data["planned"]:
        day = item.get("date")
        if not day:
            continue
        planned_secs[day] += data["planned_duration"].get(item.get("workoutId"), 0.0)
        planned_m[day] += data["planned_distance"].get(item.get("workoutId"), 0.0)

    today = data["today"]
    this_monday = monday_of(today)
    week_keys = [(this_monday + timedelta(days=d)).isoformat() for d in range(7)]

    sports = []
    for kind, label in (("run", "Running"), ("strength", "Strength"), ("other", "Other")):
        done = sum(actual[k].get(kind, 0.0) for k in week_keys)
        plan = sum(planned_secs[k] for k in week_keys) if kind == "run" else 0.0
        if done or plan:
            sports.append({"kind": kind, "label": label,
                           "done": duration(done) or "0s",
                           "planned": duration(plan) if plan else None})

    # -- week progression -------------------------------------------------
    progression = []
    week_start = this_monday - timedelta(weeks=WEEK_STRIP - 1)
    while week_start <= this_monday + timedelta(weeks=WEEKS_FORWARD):
        keys = [(week_start + timedelta(days=d)).isoformat() for d in range(7)]
        by_kind = {
            kind: sum(actual[k].get(kind, 0.0) for k in keys)
            for kind in ("run", "strength", "other")
        }
        progression.append({
            "start": week_start,
            "km": round(sum(metres[k] for k in keys) / 1000, 1),
            "planned_km": round(sum(planned_m[k] for k in keys) / 1000, 1),
            "planned_secs": sum(planned_secs[k] for k in keys),
            "by_kind": {k: v for k, v in by_kind.items() if v > 0},
            "secs": sum(by_kind.values()),
            "current": week_start == this_monday,
            "future": week_start > this_monday,
        })
        week_start += timedelta(weeks=1)

    # -- day detail -------------------------------------------------------
    detail = []
    cursor = data["start"]
    while cursor <= data["end"]:
        days, done_m, plan_m, done_s, plan_s = [], 0.0, 0.0, 0.0, 0.0
        for offset in range(7):
            day = cursor + timedelta(days=offset)
            key = day.isoformat()
            bars = {k: v for k, v in actual.get(key, {}).items() if v > 0}
            days.append({
                "date": day,
                "is_today": day == today,
                "bars": bars,
                "actual_secs": sum(bars.values()),
                "planned_secs": planned_secs.get(key, 0.0),
            })
            done_m += metres[key]
            plan_m += planned_m[key]
            done_s += sum(bars.values())
            plan_s += planned_secs[key]
        detail.append({
            "start": cursor, "days": days,
            "actual_km": round(done_m / 1000, 1), "planned_km": round(plan_m / 1000, 1),
            "actual_time": duration(done_s) or "—", "planned_time": duration(plan_s),
            "future": cursor > this_monday,
            "current": cursor == this_monday,
        })
        cursor += timedelta(weeks=1)

    summary = {
        "km": round(sum(metres[k] for k in week_keys) / 1000, 1),
        "time": duration(sum(sum(actual[k].values()) for k in week_keys)) or "0s",
        "runs": sum(counts[k] for k in week_keys),
    }
    return {"sports": sports, "progression": progression, "detail": detail,
            "summary": summary}
