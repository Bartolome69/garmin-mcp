"""Not feeling 100% (Nov 21): what each choice would do to the week, by fixed rules.

The app's sheet offers four things on a day someone isn't up to the session:
lighter today, swap with a later day, rest today, or a few days off. This
module holds the rules and the session changes; app.py reads Garmin, applies
them and answers the screens. There is no model here and nothing medical:
it says what changes, never why someone should rest.

The rules are the plan checker's own (plan_checks): no two hard days in a
row, nothing hard in the two days after a race, the long run never next to a
hard day, races never moved, nothing moved out of the week it's in.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from . import plan_checks, training_plan
from .workouts import build_workout, format_pace

# A hard session, for the rules: the plan checker's quality types and races.
HARD = {"threshold", "sub", "intervals", "race"}
# What "Lighter today" asks about: a hard session can lose some intensity or all of it.
QUALITY = {"threshold", "sub", "intervals"}
# Less intensity: each rep about 5% slower (about 15 s/km at 5:00/km), heart rate 5 bpm lower.
EASE_SHARE = 0.05
EASE_BPM = 5
# No intensity: an easy run of about two-thirds of the session's distance, never under 3 km.
NONE_SHARE = 2 / 3
MIN_EASY_KM = 3.0
# Around a plan's easy pace when the plan's own easy runs have no target to copy.
EASY_WINDOW_SECONDS = 15
DAYS_OFF = (2, 3, 5)
RACE_EASY_DAYS = plan_checks.RACE_EASY_DAYS
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


# -- the session changes ----------------------------------------------------------

def _steps(workout: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [s for seg in workout.get("workoutSegments") or [] for s in seg.get("workoutSteps") or []]


def _flat(steps: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in steps:
        if s.get("type") == "RepeatGroupDTO" or s.get("numberOfIterations"):
            out.extend(_flat(s.get("workoutSteps") or []))
        else:
            out.append(s)
    return out


def _kind(step: Mapping[str, Any]) -> str:
    return str((step.get("stepType") or {}).get("stepTypeKey") or "").lower()


def _target(step: Mapping[str, Any]) -> str:
    return str((step.get("targetType") or {}).get("workoutTargetTypeKey") or "").lower()


def _pace_range(one: float, two: float) -> str:
    """A metres-per-second range as a pace range, faster first, as the app writes it: 4:00–4:06/km."""
    fast, slow = sorted((1000 / one, 1000 / two))
    return f"{format_pace(fast)[:-3]}\u2013{format_pace(slow)}"


def _said(step: Mapping[str, Any]) -> str | None:
    """A work step's target as the sheet says it."""
    one, two = step.get("targetValueOne"), step.get("targetValueTwo")
    target = _target(step)
    if target == "pace.zone" and one and two:
        return _pace_range(float(one), float(two))
    if target == "heart.rate.zone" and one and two:
        return f"{float(one):.0f}\u2013{float(two):.0f} bpm"
    if target == "heart.rate.zone" and step.get("zoneNumber"):
        return f"heart rate zone {int(step['zoneNumber'])}"
    if target.startswith("power") and one and two:
        return f"{float(one):.0f}\u2013{float(two):.0f} W"
    return None


def _ease(step: dict[str, Any]) -> bool:
    """One work step's target eased, in place. True if it had one to ease."""
    one, two = step.get("targetValueOne"), step.get("targetValueTwo")
    target = _target(step)
    if target == "pace.zone" and one and two:
        # Speeds, so slower is smaller: each bound's pace about 5% slower.
        step["targetValueOne"] = round(float(one) / (1 + EASE_SHARE), 4)
        step["targetValueTwo"] = round(float(two) / (1 + EASE_SHARE), 4)
        return True
    if target == "heart.rate.zone" and one and two:
        step["targetValueOne"] = float(one) - EASE_BPM
        step["targetValueTwo"] = float(two) - EASE_BPM
        return True
    if target == "heart.rate.zone" and step.get("zoneNumber"):
        if int(step["zoneNumber"]) <= 1:
            return False
        step["zoneNumber"] = int(step["zoneNumber"]) - 1
        return True
    if target.startswith("power") and one and two:
        step["targetValueOne"] = round(float(one) * (1 - EASE_SHARE))
        step["targetValueTwo"] = round(float(two) * (1 - EASE_SHARE))
        return True
    return False


def _copy(workout: Mapping[str, Any], mark: str) -> dict[str, Any]:
    """A copy to upload in the original's place, carrying a line naming the original for Undo."""
    made = json.loads(json.dumps(dict(workout)))
    made["description"] = (str(workout.get("description") or "").rstrip()
                           + f"\n\n{mark} {workout.get('workoutId')}.").strip()
    for key in ("workoutId", "estimatedDurationInSecs", "estimatedDistanceInMeters", "workoutProvider",
                "createdDate", "updatedDate", "ownerId", "author"):
        made.pop(key, None)

    def drop_ids(steps: list[dict[str, Any]]) -> None:
        for s in steps:
            s.pop("stepId", None)
            drop_ids(s.get("workoutSteps") or [])

    for seg in made.get("workoutSegments") or []:
        drop_ids(seg.get("workoutSteps") or [])
    return made


def less_intense(workout: Mapping[str, Any] | None, mark: str) -> tuple[dict[str, Any], str, str] | None:
    """The same session with every rep eased about 5%, and the first rep's target before and after.

    Only the work steps change: warm-up, recoveries and cool-down keep theirs.
    None when no work step has a target to ease (some coach-made workouts).
    """
    if not workout:
        return None
    made = _copy(workout, mark)
    before = after = None
    for step in _flat(_steps(made)):
        if _kind(step) != "interval":
            continue
        was = _said(step)
        if _ease(step):
            if before is None:
                before, after = was, _said(step)
    if before is None or after is None:
        return None
    return made, before, after


def easy_km(planned_km: float | None) -> float | None:
    """No intensity's distance: about two-thirds, to the half kilometre, never under 3 km."""
    if not planned_km:
        return None
    return max(MIN_EASY_KM, round(float(planned_km) * NONE_SHARE * 2) / 2)


def easy_target(easy_workout: Mapping[str, Any] | None, easy_mps: float | None) -> list[str] | None:
    """The plan's easy pace as a range: an easy run's own target this week, else the runner's usual easy pace."""
    for step in _flat(_steps(easy_workout or {})):
        one, two = step.get("targetValueOne"), step.get("targetValueTwo")
        if _kind(step) == "interval" and _target(step) == "pace.zone" and one and two:
            fast, slow = sorted((1000 / float(one), 1000 / float(two)))
            return [format_pace(fast)[:-3], format_pace(slow)[:-3]]
    if easy_mps:
        centre = 1000 / easy_mps
        return [format_pace(centre - EASY_WINDOW_SECONDS)[:-3], format_pace(centre + EASY_WINDOW_SECONDS)[:-3]]
    return None


def no_intensity(workout: Mapping[str, Any] | None, km: float | None, pace: list[str] | None,
                 mark: str) -> dict[str, Any] | None:
    """An easy run in the hard session's place, tagged with its plan so the plan still counts it."""
    if not workout or not km:
        return None
    code = training_plan.label_of(str(workout.get("workoutName") or ""))
    name = f"Easy run {km:g} km"
    step: dict[str, Any] = {"type": "interval", "distance_meters": km * 1000}
    if pace:
        step["pace"] = pace
    built, _, _ = build_workout(training_plan.tagged(name, code) if code else name, "running", [step])
    made = built.to_dict()
    made["description"] = f"Easy instead of {training_plan.strip_tag(str(workout.get('workoutName') or 'the session'))}." \
                          f"\n\n{mark} {workout.get('workoutId')}."
    return made


# -- the week's rules ------------------------------------------------------------

def _d(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def week_end(day: date) -> date:
    """The Sunday of a day's week: changes never move a session out of its week."""
    return day + timedelta(days=6 - day.weekday())


def _types_by_day(sessions: Iterable[Mapping[str, Any]]) -> dict[date, list[str]]:
    by_day: dict[date, list[str]] = {}
    for s in sessions:
        d = _d(s.get("date"))
        if d and s.get("type"):
            by_day.setdefault(d, []).append(str(s["type"]))
    return by_day


def _breaks_rules(by_day: Mapping[date, list[str]], changed: Iterable[date]) -> bool:
    """Whether the days that changed now break a rule against their neighbours.

    Only the changed days are judged, so a plan that already had two hard
    days together doesn't stop every change near them.
    """
    def hard(d: date) -> bool:
        return any(t in HARD for t in by_day.get(d, []))

    def long(d: date) -> bool:
        return "long" in by_day.get(d, [])

    def race(d: date) -> bool:
        return "race" in by_day.get(d, [])

    for d in set(changed):
        for n in (d - timedelta(days=1), d + timedelta(days=1)):
            if hard(d) and hard(n):
                return True
            if (hard(d) and long(n) and not race(d)) or (long(d) and hard(n) and not race(n)):
                return True
        if hard(d) and not race(d) and any(race(d - timedelta(days=k)) for k in range(1, RACE_EASY_DAYS + 1)):
            return True
        if race(d) and any(hard(d + timedelta(days=k)) and not race(d + timedelta(days=k))
                           for k in range(1, RACE_EASY_DAYS + 1)):
            return True
    return False


def _open(s: Mapping[str, Any]) -> bool:
    return not str(s.get("status") or "").startswith("done")


def swap_day(sessions: list[Mapping[str, Any]], chosen: Mapping[str, Any]) -> tuple[date, Mapping[str, Any] | None] | None:
    """The later day this week a hard session or long run can swap with, and the session coming back, or None.

    A day with an easy run is swapped with first (the easy run comes to
    today); then a day with nothing on it. A day with its own hard session,
    long run or race is never one, and the swap must keep the rules.
    """
    day = _d(chosen.get("date"))
    if not day or chosen.get("type") not in (QUALITY | {"long"}):
        return None
    on: dict[date, list[Mapping[str, Any]]] = {}
    for s in sessions:
        d = _d(s.get("date"))
        if d:
            on.setdefault(d, []).append(s)
    easy_days, empty_days = [], []
    d = day + timedelta(days=1)
    while d <= week_end(day):
        there = on.get(d, [])
        d, step = d + timedelta(days=1), d
        if any(not _open(s) for s in there) or any(s.get("type") in HARD | {"long"} for s in there):
            continue
        runs = [s for s in there if s.get("type") in ("easy", "recovery", "ride")]
        if runs:
            easy_days.append((step, min(runs, key=training_plan._rank)))
        else:  # nothing on it, or strength or a swim, which stay put
            empty_days.append((step, None))
    for d, back in easy_days + empty_days:
        moved = [dict(s, date=d.isoformat()) if s is chosen else
                 dict(s, date=day.isoformat()) if back is not None and s is back else s for s in sessions]
        if not _breaks_rules(_types_by_day(moved), (day, d)):
            return d, back
    return None


def days_off(sessions: list[Mapping[str, Any]], day: date, n: int) -> tuple[list[Mapping[str, Any]], Mapping[str, Any] | None, list[Mapping[str, Any]]]:
    """What n days off takes off, the first run back (made easier), and the races left where they are."""
    last = day + timedelta(days=n - 1)
    inside = [s for s in sessions if _open(s) and (d := _d(s.get("date"))) and day <= d <= last]
    off = [s for s in inside if s.get("type") != "race"]
    races = [s for s in inside if s.get("type") == "race"]
    after = sorted((s for s in sessions if _open(s) and (d := _d(s.get("date"))) and d > last
                    and s.get("type") != "race" and s.get("kind", "run") in ("run", "ride")),
                   key=lambda s: (str(s.get("date")), training_plan._rank(s)))
    return off, (after[0] if after else None), races


def when(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return DAY_NAMES[d.weekday()]


def km_said(km: float | None) -> str:
    return f"{km:g} km" if km else ""
