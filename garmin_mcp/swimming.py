"""Swims, read and built the way swimmers think about them.

A swim is not a slow run. Swimmers pace per 100 (metres, or yards in a yard
pool), count strokes and SWOLF, and think in sets: "8 x 100 free on 1:45",
with rests ended by pressing lap at the wall. This module reads a swim that
way and turns a swim set into the workout fields Garmin expects.

Garmin doesn't document its swim fields, and they differ between the activity
list, the detail endpoint and the laps. Every reader below tries the names
seen in the wild and leaves a value out rather than guess, so a swim whose
shape is unfamiliar comes back with less, never with something wrong.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any, Iterable

from .formatting import drop_empty, duration, rounded


def is_swim(type_key: Any) -> bool:
    return "swim" in str(type_key or "").lower()


def is_pool(type_key: Any) -> bool:
    key = str(type_key or "").lower()
    return "swim" in key and "open_water" not in key


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


# --------------------------------------------------------------------------
# Pool length and pace
# --------------------------------------------------------------------------

YARD_IN_METRES = 0.9144


def pool(row: Mapping[str, Any]) -> tuple[float, str] | None:
    """(pool length, "m" or "yd") from Garmin's poolLength and its unit.

    Garmin stores the length scaled by the unit's factor (2500.0 with a factor
    of 100 is a 25 m pool), so the factor is applied when it is given and the
    result checked against lengths a pool can plausibly have.
    """
    raw = _first(row, "poolLength")
    if raw is None or raw <= 0:
        return None
    unit = row.get("unitOfPoolLength") or row.get("poolLengthUnit") or {}
    key = str(unit.get("unitKey") or "meter").lower() if isinstance(unit, Mapping) else "meter"
    factor = unit.get("factor") if isinstance(unit, Mapping) else None
    # Activities have been seen as 2500.0 with a factor of 100, workouts as 25.0
    # with the same factor, so take the first reading a pool can have.
    candidates = [float(raw)]
    if isinstance(factor, (int, float)) and factor > 0:
        candidates.insert(0, float(raw) / float(factor))
    candidates.append(float(raw) / 100)
    length = next((c for c in candidates if 10 <= c <= 100), None)
    if length is None:
        return None
    return (round(length, 1), "yd" if key.startswith("yard") else "m")


def pace_per_100(distance_m: Any, seconds: Any, unit: str = "m") -> str | None:
    """'1:45 /100m' (or /100yd in a yard pool)."""
    if not isinstance(distance_m, (int, float)) or not isinstance(seconds, (int, float)):
        return None
    if distance_m <= 0 or seconds <= 0:
        return None
    per = 100 * YARD_IN_METRES if unit == "yd" else 100.0
    secs = seconds / (distance_m / per)
    minutes, s = divmod(int(round(secs)), 60)
    return f"{minutes}:{s:02d} /100{unit}"


# --------------------------------------------------------------------------
# Reading a swim
# --------------------------------------------------------------------------

STROKES = {
    "FREESTYLE": "freestyle", "FREE": "freestyle",
    "BACKSTROKE": "backstroke", "BACK": "backstroke",
    "BREASTSTROKE": "breaststroke", "BREAST": "breaststroke",
    "BUTTERFLY": "butterfly", "FLY": "butterfly",
    "IM": "individual medley", "INDIVIDUAL_MEDLEY": "individual medley",
    "MIXED": "mixed", "DRILL": "drill",
}


def _stroke_name(value: Any) -> str | None:
    if not value:
        return None
    key = str(value).upper().replace(" ", "_")
    return STROKES.get(key, str(value).lower().replace("_", " "))


def swim_metrics(row: Mapping[str, Any]) -> dict[str, Any]:
    """SWOLF, stroke rate and strokes from an activity or a lap, whichever names it uses."""
    return drop_empty({
        "swolf": rounded(_first(row, "averageSwolf", "avgSwolf", "averageSWOLF", "avgSWOLF"), 0),
        "stroke_rate_spm": rounded(_first(row, "averageSwimCadenceInStrokesPerMinute",
                                          "avgSwimCadence", "averageSwimCadence"), 0),
        "strokes_per_length": rounded(_first(row, "avgStrokes", "averageStrokes",
                                             "avgStrokesPerLength"), 1),
        "total_strokes": rounded(_first(row, "strokes", "totalNumberOfStrokes", "totalStrokes"), 0),
        "lengths": rounded(_first(row, "activeLengths", "numberOfActiveLengths"), 0),
    })


def summarise_swim(activity: Mapping[str, Any], distance: Any, secs: Any) -> dict[str, Any]:
    """A swim by pace per 100, pool and stroke count, rather than a run's pace per km."""
    type_key = (activity.get("activityType") or {}).get("typeKey")
    found = pool(activity) if is_pool(type_key) else None
    unit = found[1] if found else "m"
    moving = _first(activity, "movingDuration") or secs
    return drop_empty({
        "activity_id": activity.get("activityId"),
        "name": activity.get("activityName"),
        "type": type_key,
        "start_local": activity.get("startTimeLocal"),
        "location": activity.get("locationName"),
        "distance_m": rounded(distance, 0),
        "pool": f"{found[0]:g} {found[1]}" if found else None,
        "duration": duration(secs),
        "duration_seconds": rounded(secs, 0),
        # Pool swims rest at the wall; moving time is the swimming.
        "swimming_time": duration(moving) if moving != secs else None,
        "pace_per_100": pace_per_100(distance, moving, unit),
        **swim_metrics(activity),
        "heart_rate": drop_empty({
            "average_bpm": rounded(activity.get("averageHR"), 0),
            "max_bpm": rounded(activity.get("maxHR"), 0),
        }),
        "calories": rounded(activity.get("calories"), 0),
        "training_effect": drop_empty({
            "aerobic": rounded(activity.get("aerobicTrainingEffect"), 1),
            "anaerobic": rounded(activity.get("anaerobicTrainingEffect"), 1),
        }),
    })


def _lap_stroke(lap: Mapping[str, Any]) -> str | None:
    named = _stroke_name(lap.get("swimStroke") or lap.get("swimStrokeType"))
    if named:
        return named
    lengths = [l for l in lap.get("lengthDTOs") or [] if isinstance(l, Mapping)]
    strokes = Counter(_stroke_name(l.get("swimStroke")) for l in lengths if l.get("swimStroke"))
    if not strokes:
        return None
    (top, n), = strokes.most_common(1)
    return top if n == sum(strokes.values()) else "mixed"


def _is_rest(lap: Mapping[str, Any]) -> bool:
    if str(lap.get("intensityType") or "").upper() == "REST":
        return True
    distance = _first(lap, "distance")
    lengths = _first(lap, "numberOfActiveLengths", "activeLengths")
    return (distance is not None and distance <= 0) or lengths == 0


def intervals(laps: Iterable[Any], unit: str = "m") -> list[dict[str, Any]]:
    """The set as swum: each interval with its pace, stroke and SWOLF, and the rest after it."""
    out: list[dict[str, Any]] = []
    for lap in laps or []:
        if not isinstance(lap, Mapping):
            continue
        secs = _first(lap, "movingDuration", "duration", "elapsedDuration")
        if _is_rest(lap):
            if out and secs:
                out[-1]["rest_after"] = duration(_first(lap, "duration", "elapsedDuration") or secs)
            continue
        distance = _first(lap, "distance")
        metrics = swim_metrics(lap)
        metrics.pop("total_strokes", None)
        out.append(drop_empty({
            "interval": len(out) + 1,
            "distance_m": rounded(distance, 0),
            "time": duration(secs),
            "pace_per_100": pace_per_100(distance, secs, unit),
            "stroke": _lap_stroke(lap),
            **metrics,
            "average_bpm": rounded(lap.get("averageHR"), 0),
        }))
    return out


HOW_TO_READ = (
    "Pace is per 100 (metres, or yards in a yard pool), on swimming time, rests "
    "at the wall left out. SWOLF is strokes plus seconds for one length: lower "
    "is more efficient, and it only compares within the same pool length. "
    "Stroke rate is strokes per minute. Each interval is one block between lap "
    "presses, with the rest that followed it."
)


# --------------------------------------------------------------------------
# Building a swim workout
# --------------------------------------------------------------------------

# Garmin's own stroke ids, as its workout editor writes them.
STROKE_TYPES = {
    "any": (1, "any_stroke"),
    "backstroke": (2, "backstroke"),
    "breaststroke": (3, "breaststroke"),
    "drill": (4, "drill"),
    "butterfly": (5, "fly"),
    "freestyle": (6, "free"),
    "im": (7, "individual_medley"),
}

STROKE_ALIASES = {
    "any": "any", "any_stroke": "any", "choice": "any", "mixed": "any",
    "back": "backstroke", "backstroke": "backstroke",
    "breast": "breaststroke", "breaststroke": "breaststroke",
    "drill": "drill", "drills": "drill",
    "fly": "butterfly", "butterfly": "butterfly",
    "free": "freestyle", "freestyle": "freestyle", "front crawl": "freestyle", "crawl": "freestyle",
    "im": "im", "medley": "im", "individual medley": "im", "individual_medley": "im",
}


def stroke_type(name: Any) -> dict[str, Any] | None:
    """Garmin's strokeType for a stroke as people say it, or None if it isn't one."""
    key = STROKE_ALIASES.get(str(name or "").strip().lower())
    if key is None:
        return None
    stroke_id, stroke_key = STROKE_TYPES[key]
    return {"strokeTypeId": stroke_id, "strokeTypeKey": stroke_key, "displayOrder": stroke_id}


def pool_fields(length_m: float) -> dict[str, Any]:
    """The workout-level pool length Garmin needs to count lengths on the watch."""
    return {
        "poolLength": float(length_m),
        "poolLengthUnit": {"unitId": 1, "unitKey": "meter", "factor": 100.0},
    }


def parse_pace_per_100(value: Any) -> float:
    """'1:45', '1:45/100m' or 105 -> seconds per 100 m."""
    from .workouts import WorkoutError  # one error type for every workout mistake

    text = str(value).strip().lower().replace("/100m", "").replace("/100", "").strip()
    try:
        if ":" in text:
            minutes, _, secs = text.partition(":")
            seconds = int(minutes) * 60 + float(secs)
        else:
            seconds = float(text)
    except ValueError as exc:
        raise WorkoutError(f"Could not read {value!r} as a swim pace. Use 'M:SS' per 100 m.") from exc
    if not 40 <= seconds <= 300:
        raise WorkoutError(
            f"Swim pace {value!r} works out as {seconds:.0f} s per 100 m, outside the "
            "plausible 0:40-5:00 range."
        )
    return seconds
