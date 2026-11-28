"""Onboarding (build prompt, section 5.13): paces from the watch or four answers, then the plan.

    import_summary   12 weeks of runs read from the watch: best recent race, easy pace,
                     threshold, usual week, as rows to confirm (Looks right / Adjust),
                     and what the plan builder needs from them
    questions        the same from four answers: experience, days a week, recent
                     distance and a recent race (optional)
    reveal           the plan itself, from the server's plan builder (plan_builder.py,
                     the PWA's): the phase bar, predicted against target, week 1 and its
                     workouts as daash.workout.v1, ready for the watch confirm

Every number here is worked out by rule; the Coach's "why this shape" is the
server's and is never one of them. TODO(server): POST /app/api/v1/onboarding/import,
/questions and /plan return these.
"""

from __future__ import annotations

import itertools
import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Sequence

from .. import plan_builder
from . import paces as pace_engine
from .model import DAYS, mmss, short_date

WEEKS_READ = 12
USUAL_WEEKS = 8
# Before a race is known: a 10K of 45:00 (section 5.13). For the four questions with no
# race, a 10K by experience. TODO(sports-science): placeholders, as config.py's are.
DEFAULT_10K = 45 * 60
EXPERIENCE_10K = {"new": 60 * 60, "regular": 52 * 60, "exp": 45 * 60}
WEEKLY_KM = {"under30": 20, "30to50": 40, "50to70": 60, "70plus": 75}
MONTHS_LONG = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")
RACE_NAMES = {5.0: "5K", 10.0: "10K", 21.0975: "Half marathon", 42.195: "Marathon"}


@dataclass(frozen=True)
class RecentRun:
    day: date
    km: float
    secs: float
    race: bool = False
    name: str = ""


def _race_name(km: float, mid: bool = False) -> str:
    """"10K", "Half marathon"; `mid` for inside a sentence ("from your half marathon")."""
    near = min(RACE_NAMES, key=lambda k: abs(k - km))
    name = RACE_NAMES[near] if abs(near - km) / near < 0.03 else f"{km:g} km"
    return name.lower() if mid and not name[0].isdigit() else name


def _range(values: list[float], step: int = 5) -> tuple[int, int]:
    """The middle half, rounded to `step` seconds."""
    q = statistics.quantiles(sorted(values), n=4) if len(values) >= 2 else [values[0]] * 3
    return int(round(q[0] / step) * step), int(round(q[2] / step) * step)


def _rows(race: tuple[float, float, date | None, str] | None, basis: tuple[float, float], easy: tuple[int, int] | None,
          easy_note: str, week: tuple[float, float] | None, week_note: str, source: str) -> list[dict]:
    zones = pace_engine.paces(basis[0], basis[1]).zones
    rows = [{"key": "race", "label": "Best recent race",
             "value": f"{_race_name(race[0])} · {mmss(race[1])}" if race else ("None in 12 weeks" if source == "import" else "None given"),
             "note": short_date(race[2]) if race and race[2] else ("from your answer" if race else f"using a 10K of {mmss(basis[1])}")}]
    e = easy or zones["easy"]
    rows.append({"key": "easy", "label": "Easy pace", "value": f"{mmss(e[0])}–{mmss(e[1])} /km", "note": easy_note})
    t = zones["threshold"]
    rows.append({"key": "threshold", "label": "Threshold", "value": f"{mmss(t[0])}–{mmss(t[1])} /km",
                 "note": f"from your {_race_name(basis[0], mid=True)}" if race else "from the 10K above"})
    if week:
        rows.append({"key": "week", "label": "Usual week", "value": f"{week[0]:.0f} km · {week[1]:.0f} runs", "note": week_note})
    return rows


def import_summary(runs: Sequence[RecentRun], today: date) -> dict[str, Any]:
    """What 12 weeks of runs say, to confirm before the plan is made from it."""
    recent = sorted((r for r in runs if today - timedelta(weeks=WEEKS_READ) <= r.day <= today), key=lambda r: r.day)
    races = [r for r in recent if r.race and r.km >= 3]
    best = min(races, key=lambda r: plan_builder.riegel(r.secs, r.km, 10)) if races else None
    basis = (best.km, best.secs) if best else (10.0, float(DEFAULT_10K))
    zones = pace_engine.paces(*basis).zones
    # Easy runs: slower than the fast end of steady, so tempo and race-pace runs stay out.
    easy_runs = [r.secs / r.km for r in recent if not r.race and r.km > 0 and r.secs / r.km >= zones["steady"][0]]
    monday = today - timedelta(days=today.weekday())
    weeks = [[r for r in recent if monday - timedelta(weeks=i + 1) <= r.day < monday - timedelta(weeks=i)] for i in range(USUAL_WEEKS)]
    km = sum(sum(r.km for r in w) for w in weeks) / USUAL_WEEKS
    n = sum(len(w) for w in weeks) / USUAL_WEEKS
    longest = max((r.km for w in weeks for r in w), default=0)
    first = recent[0].day if recent else today
    enough = len(easy_runs) >= 3
    # Too few easy runs to read a range: the race's easy zone, or the default 10K's, said as such.
    easy_note = f"from {len(easy_runs)} runs" if enough else (f"from your {_race_name(best.km, mid=True)}" if best else "from the 10K above")
    return {
        "source": "import",
        "title": f"Found {len(recent)} runs since {MONTHS_LONG[first.month - 1]}" if recent else "No runs in the last 12 weeks",
        "runs": len(recent),
        "rows": _rows((best.km, best.secs, best.day, best.name) if best else None, basis,
                      _range(easy_runs) if enough else None, easy_note,
                      # No runs in the last 8 weeks: no usual week to show, rather than "0 km · 0 runs".
                      (km, n) if n else None, f"last {USUAL_WEEKS} weeks", "import"),
        # `easy`: the range read from the runs, which the plan's easy runs use (the runner
        # confirmed it); `assumed`: no race to go on, so no prediction is made from it.
        "basis": {"raceKm": basis[0], "raceSeconds": basis[1], "raceDate": best.day.isoformat() if best else None,
                  "easy": list(_range(easy_runs)) if enough else None, "assumed": best is None},
        "answers": {"avg_km": round(km, 1), "longest_km": round(longest, 1), "days": max(3, min(7, round(n)))},
    }


def questions(experience: str, days: int, weekly: str, race_km: float | None = None, race_secs: float | None = None) -> dict[str, Any]:
    """The four questions: experience, days a week, recent weekly distance, a recent race (optional)."""
    level = experience if experience in EXPERIENCE_10K else "regular"
    race = (race_km, race_secs, None, "") if race_km and race_secs else None
    basis = (race_km, float(race_secs)) if race else (10.0, float(EXPERIENCE_10K[level]))
    km = WEEKLY_KM.get(weekly, 40)
    return {
        "source": "questions",
        "title": "From your answers",
        "runs": None,
        "rows": _rows(race, basis, None, "from the race" if race else "from the 10K above", (km, days), "from your answers", "questions"),
        "basis": {"raceKm": basis[0], "raceSeconds": basis[1], "raceDate": None, "easy": None, "assumed": race is None},
        "answers": {"avg_km": km, "longest_km": round(km * 0.3, 1), "days": max(3, min(7, days)), "level": level},
    }


_TYPE = {"easy": "easy", "long": "long", "race": "race"}


def _session_type(kind: str, name: str) -> str:
    if kind == "hard":
        return "tempo" if any(w in name.lower() for w in ("tempo", "threshold", "mp", "marathon")) else "intervals"
    return _TYPE.get(kind, "easy")


def reveal(answers: dict[str, Any], basis: dict[str, Any], today: date, goal: dict[str, Any] | None = None,
           level: str = "regular") -> dict[str, Any]:
    """The plan reveal: phases, predicted against target, week 1, and week 1's workouts for the watch."""
    raw = {**answers, "level": answers.get("level", level), "long_day": 6, "amount": "build", "style": "balanced"}
    if goal:
        raw["races"] = [{"name": goal["name"], "date": goal["date"], "km": goal["km"], "tier": "A"}]
        raw["goal_time"] = goal.get("target")
    else:
        raw["open_goal"] = "fit"
    a = plan_builder.Answers.from_json(raw)
    fitness = plan_builder.Fitness(recent_race={"name": "", "date": basis.get("raceDate"), "km": basis["raceKm"], "secs": basis["raceSeconds"]})
    b = plan_builder.build(a, fitness, today)
    s = b.shape
    zones = pace_engine.paces(basis["raceKm"], basis["raceSeconds"]).zones
    # The easy range the runner confirmed on the result, when it was read from their runs.
    easy = tuple(basis["easy"]) if basis.get("easy") else zones["easy"]
    # A 10K assumed for want of a race (none in 12 weeks, or none answered) makes no prediction.
    assumed = bool(basis.get("assumed"))
    predicted = fitness.predicted(s.dist, s.km) if goal and not assumed else None
    target = plan_builder.goal_secs(goal.get("target"), s.dist) if goal and goal.get("target") else None
    week1 = []
    for r in b.weeks[0]["rows"]:
        kind = _session_type(r["kind"], r["name"])
        km = round(r["km"], 1)
        title = plan_builder._plain(r["name"])
        title = f"Long run {km:g} km" if title == "Long run" else re.sub(r"(\d+(?:\.\d+)?)k\b", r"\1 km", title)
        z = easy if kind in ("easy", "long") else None
        label = {"easy": "Easy", "long": "Long", "tempo": "Tempo", "intervals": "Intervals", "race": "Race"}[kind]
        d = r["date"]
        week1.append({
            "id": f"ob-{d.isoformat()}", "date": d.isoformat(), "dow": DAYS[d.weekday()].upper(), "day": d.day,
            "type": kind, "typeLabel": label, "title": title, "distanceKm": km,
            "sub": " · ".join([label, f"{km:g} km", *([f"{mmss(z[0])}–{mmss(z[1])}"] if z else [])]),
            "workout": {"schema": "daash.workout.v1", "name": title, "sport": "running", "steps": r["steps"]},
        })
    race_name = _race_name(basis["raceKm"], mid=True)
    avg = answers.get("avg_km") or 0
    because = f"Phases follow from your {s.weeks} weeks" + (f" and recent {avg:.0f} km weeks." if avg >= 1 else ".")
    if goal:
        because += (f" No prediction without a recent race; paces use a {race_name} of {mmss(basis['raceSeconds'])} for now."
                    if assumed else f" Prediction uses your {race_name} of {mmss(basis['raceSeconds'])}.")
    return {
        "kicker": f"Your plan · {s.weeks} weeks",
        "title": f"{goal['name']}, {DAYS[s.race.day.weekday()]} {short_date(s.race.day)}" if goal and s.race else
                 "Your fitness plan",
        "phases": [{"label": ph, "weeks": len(list(g))} for ph, g in itertools.groupby(s.phases)],
        "predicted": plan_builder.clock(predicted) if predicted else None,
        "target": plan_builder.clock(target) if target else None,
        "startKm": round(b.weeks[0]["km"]),
        "peakKm": round(b.peak_km),
        "starts": s.start.isoformat(),
        "because": because,
        "week1": week1,
    }
