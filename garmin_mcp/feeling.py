"""Not feeling 100% (Nov 21): what each choice would do to the week, by fixed rules.

The app's sheet offers, for the session being looked at: a lighter run, an
easier pace, making a hard session easy, a swap with a later day, skipping
it, or a few days off. This module holds the rules and the session changes;
app.py reads Garmin, applies them and answers the screens. There is no model
here and nothing medical: it says what changes, for a busy, tired or flat
day, never why someone should rest.

The rules are the plan checker's own (plan_checks): no two hard days in a
row, nothing hard in the two days after a race, the long run never next to a
hard day, races never moved, nothing moved out of the week it's in.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from . import plan_checks, training_plan
from .formatting import HR_CAP_FLOOR
from .workouts import TARGET_HEART_RATE_ZONE, build_workout, format_pace

# A hard session, for the rules: the plan checker's quality types and races.
HARD = {"threshold", "sub", "intervals", "race"}
# What "Make it easy" is offered for: intervals and tempo (threshold) sessions.
QUALITY = {"threshold", "sub", "intervals"}
# Lighter run: the same session at the same pace, a fifth shorter.
LIGHTER_SHARE = 0.8
# Easier pace: the pace range 15 to 20 s/km slower (its quick end 15, its
# slow end 20), and heart rate capped at the top of the easy zone (zone 2).
PACE_SLOWER = (15, 20)
EASY_ZONE = 2
# Around a plan's easy pace when the plan's own easy runs have no target to copy.
EASY_WINDOW_SECONDS = 15
DAYS_OFF = (2, 3, 5)
RACE_EASY_DAYS = plan_checks.RACE_EASY_DAYS
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# Taper weeks before an A race, race week included, by distance (plan_builder's TAPERS).
TAPER_WEEKS = ((30, 3), (15, 2), (0, 1))
TIER_LETTER = {"goal": "A", "key": "B", "tune": "C", "other": "C"}


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


def _cond(step: Mapping[str, Any]) -> str:
    return str((step.get("endCondition") or {}).get("conditionTypeKey") or "").lower()


def pace_range(fast_s: float, slow_s: float) -> str:
    """Seconds per km as the app writes a range: 4:00–4:06/km."""
    fast, slow = sorted((fast_s, slow_s))
    return f"{format_pace(fast)[:-3]}\u2013{format_pace(slow)}"


def _paces(step: Mapping[str, Any]) -> tuple[float, float] | None:
    one, two = step.get("targetValueOne"), step.get("targetValueTwo")
    if _target(step) == "pace.zone" and one and two:
        return tuple(sorted((1000 / float(one), 1000 / float(two))))  # type: ignore[return-value]
    return None


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


def _estimates(made: dict[str, Any], original: Mapping[str, Any], share: float | None = None) -> None:
    """Garmin's own time and distance for the copy, which Garmin Connect and the watch show.

    Worked out from the copy's steps where they say (a run or ride), else the
    original's scaled by the share, so a lighter run reads shorter everywhere.
    """
    totals = training_plan.session_totals(made)
    if totals and totals.get("minutes"):
        made["estimatedDurationInSecs"] = int(totals["minutes"] * 60)
        if totals.get("km"):
            made["estimatedDistanceInMeters"] = round(totals["km"] * 1000)
        return
    for key in ("estimatedDurationInSecs", "estimatedDistanceInMeters"):
        if original.get(key) and share:
            made[key] = round(float(original[key]) * share)


def lighter_run(workout: Mapping[str, Any] | None, mark: str) -> dict[str, Any] | None:
    """The same session at the same pace, every part a fifth shorter (12 km becomes 9.6 km).

    Distances go to the nearest 100 m and times to the nearest 15 s. The
    distances in its name follow ("Easy run 12 km" becomes "Easy run 9.6 km").
    None for a session with nothing set by distance or time (strength).
    """
    if not workout:
        return None
    made = _copy(workout, mark)
    changed = False
    for step in _flat(_steps(made)):
        value = float(step.get("endConditionValue") or 0)
        if not value or _cond(step) not in ("distance", "time"):
            continue
        step["endConditionValue"] = (round(value * LIGHTER_SHARE / 100) * 100 if _cond(step) == "distance"
                                     else round(value * LIGHTER_SHARE / 15) * 15)
        changed = changed or step["endConditionValue"] != value
    if not changed:
        return None
    name = str(workout.get("workoutName") or "Workout")
    code = training_plan.label_of(name)
    bare = re.sub(r"(\d+(?:\.\d+)?)(\s*km)\b", lambda m: f"{round(float(m.group(1)) * LIGHTER_SHARE, 1):g}{m.group(2)}",
                  training_plan.strip_tag(name))
    made["workoutName"] = training_plan.tagged(bare, code) if code else bare
    _estimates(made, workout, LIGHTER_SHARE)
    return made


def easy_hr_top(zones: Mapping[str, Any] | None) -> float | None:
    """The top of the easy zone (zone 2) in bpm, from Garmin's heart rate zones."""
    for z in (zones or {}).get("zones") or []:
        if z.get("zone") == EASY_ZONE and isinstance(z.get("bpm"), (list, tuple)) and len(z["bpm"]) == 2:
            return float(z["bpm"][1])
    return None


def easier_pace(workout: Mapping[str, Any] | None, mark: str,
                hr_top: float | None) -> tuple[dict[str, Any], str | None, str | None, float | None] | None:
    """The same distance at an easier pace: pace ranges 15–20 s/km slower, heart rate capped at the easy zone's top.

    Garmin keeps one target per step, so a step paced by pace gets the slower
    pace, a step paced by heart rate gets the cap, and a run or warm-up with
    no target gets the cap as its target. Recoveries and rests keep theirs.
    Returns the copy, the first paced step's range before and after, and the
    cap when one was set; None when nothing could be eased.
    """
    if not workout:
        return None
    made = _copy(workout, mark)
    before = after = None
    capped = None
    for step in _flat(_steps(made)):
        if _kind(step) in ("recovery", "rest"):
            continue
        paces = _paces(step)
        if paces:
            fast, slow = paces[0] + PACE_SLOWER[0], paces[1] + PACE_SLOWER[1]
            step["targetValueOne"], step["targetValueTwo"] = round(1000 / slow, 4), round(1000 / fast, 4)
            if before is None:
                before, after = pace_range(*paces), pace_range(fast, slow)
            continue
        if not hr_top:
            continue
        if _target(step) == "heart.rate.zone" and step.get("zoneNumber") and not step.get("targetValueOne"):
            if int(step["zoneNumber"]) > EASY_ZONE:
                step["zoneNumber"] = EASY_ZONE
                capped = hr_top
            continue
        if _target(step) == "heart.rate.zone":
            one, two = float(step.get("targetValueOne") or 0), float(step.get("targetValueTwo") or 0)
            if two > hr_top:
                step["targetValueTwo"] = hr_top
                step["targetValueOne"] = one if one < hr_top - 5 else HR_CAP_FLOOR
                capped = hr_top
            continue
        if _target(step) in ("", "no.target") and _cond(step) in ("distance", "time") and _kind(step) in ("interval", "warmup", "cooldown", "other"):
            step["targetType"] = {"workoutTargetTypeId": TARGET_HEART_RATE_ZONE,
                                  "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}
            step["targetValueOne"], step["targetValueTwo"] = float(HR_CAP_FLOOR), hr_top
            capped = hr_top
    if before is None and capped is None:
        return None
    _estimates(made, workout)
    return made, before, after, capped


def easy_target(easy_workout: Mapping[str, Any] | None, easy_mps: float | None) -> list[str] | None:
    """The plan's easy pace as a range: an easy run's own target this week, else the runner's usual easy pace."""
    for step in _flat(_steps(easy_workout or {})):
        paces = _paces(step)
        if _kind(step) == "interval" and paces:
            return [format_pace(paces[0])[:-3], format_pace(paces[1])[:-3]]
    if easy_mps:
        centre = 1000 / easy_mps
        return [format_pace(centre - EASY_WINDOW_SECONDS)[:-3], format_pace(centre + EASY_WINDOW_SECONDS)[:-3]]
    return None


def make_it_easy(workout: Mapping[str, Any] | None, minutes: float | None, pace: list[str] | None,
                 mark: str) -> dict[str, Any] | None:
    """An easy run for the same time as the hard session it replaces, tagged with its plan so the plan still counts it."""
    if not workout or not minutes:
        return None
    minutes = max(10, round(float(minutes)))
    code = training_plan.label_of(str(workout.get("workoutName") or ""))
    name = f"Easy run {minutes} min"
    step: dict[str, Any] = {"type": "interval", "duration_seconds": minutes * 60}
    if pace:
        step["pace"] = pace
    built, _, _ = build_workout(training_plan.tagged(name, code) if code else name, "running", [step])
    made = built.to_dict()
    made["description"] = (f"Easy instead of {training_plan.strip_tag(str(workout.get('workoutName') or 'the session'))}."
                           f"\n\n{mark} {workout.get('workoutId')}.")
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


def day_word(d: date, today: date) -> str:
    """The session's own day, as the sheet names it: "today", else its weekday ("Sunday")."""
    return "today" if d == today else DAY_NAMES[d.weekday()]


def days(d: date, today: date) -> str:
    """The possessive that opens a line: "Today's", "Sunday's"."""
    word = day_word(d, today)
    return word[:1].upper() + word[1:] + "'s"


def short_day(d: date) -> str:
    return DAY_NAMES[d.weekday()][:3]


def taper_weeks(race: Mapping[str, Any]) -> int:
    km = race.get("distance_km") or training_plan.km_from_name(str(race.get("name") or "")) or 0
    return next(weeks for floor, weeks in TAPER_WEEKS if float(km) >= floor)


def taper_touched(race: Mapping[str, Any] | None, first: date, last: date) -> str | None:
    """Whether days off from first to last touch an A race's taper or race week: "taper", "race week" or None."""
    if not race or race.get("tier") != "goal":
        return None
    on = _d(race.get("date"))
    if not on or last < first:
        return None
    race_monday = on - timedelta(days=on.weekday())
    taper_from = race_monday - timedelta(weeks=taper_weeks(race) - 1)
    if last < taper_from or first > on:
        return None
    # Days off that start in race week touch only race week; earlier ones touch the taper.
    return "race week" if first >= race_monday else "taper"


def next_race(races: Iterable[Mapping[str, Any]], today: date) -> Mapping[str, Any] | None:
    ahead = [r for r in races if (d := _d(r.get("date"))) and d >= today]
    return min(ahead, key=lambda r: str(r.get("date"))) if ahead else None


def talk_it_through(week: list[Mapping[str, Any]], race: Mapping[str, Any] | None, display) -> str:
    """The message "Talk it through" opens a chat with: the week with each session's state, and the next race.

    Only the plan's own words: the session names, their days and whether
    they're done, missed or still planned. Nothing about the person.
    """
    def state(s: Mapping[str, Any]) -> str:
        status = str(s.get("status") or "")
        return "done" if status.startswith("done") else "missed" if status == "missed" else "planned"

    rows = sorted(week, key=lambda s: (str(s.get("date")), training_plan._rank(s)))
    said = "; ".join(f"{short_day(_d(s['date']))} {display(s.get('name'))} ({state(s)})" for s in rows if _d(s.get("date")))
    if race and _d(race.get("date")):
        on = _d(race["date"])
        race_said = (f"next race {display(race.get('name')) or 'race'}, {on.day} {on.strftime('%b %Y')}, "
                     f"priority {TIER_LETTER.get(str(race.get('tier') or 'other'), 'C')}")
    else:
        race_said = "no race on the calendar"
    return (f"I'm not feeling 100% today. Here's my week: {said or 'nothing planned'}, {race_said}. "
            "Can you adjust my plan?")


def km_said(km: float | None) -> str:
    return f"{km:g} km" if km else ""
