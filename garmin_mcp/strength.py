"""A gym session as Garmin recorded it: the exercises, their sets, reps and
weights, from the exercise sets the watch logged (or the person corrected
in Garmin Connect afterwards)."""

from __future__ import annotations

from typing import Any, Mapping

from .formatting import drop_empty

STRENGTH_TYPES = {"strength_training", "indoor_cardio", "hiit", "pilates"}


def is_strength(type_key: str | None) -> bool:
    return str(type_key or "").lower() in STRENGTH_TYPES


def _exercise_name(exercises: Any) -> str:
    """The exercise the watch named: its own name if it has one, else its
    category, in words ("DUMBBELL_BENCH_PRESS" is "Dumbbell bench press")."""
    best = None
    for e in exercises or []:
        if isinstance(e, Mapping) and (best is None or (e.get("probability") or 0) > (best.get("probability") or 0)):
            best = e
    raw = (best or {}).get("name") or (best or {}).get("category") or ""
    words = str(raw).replace("_", " ").strip().lower()
    return words[:1].upper() + words[1:] if words else "Unnamed exercise"


def _kg(weight: Any) -> float | None:
    """Garmin keeps a set's weight in grams; a figure small enough to be kg
    already is taken as kg."""
    try:
        value = float(weight)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    kg = value / 1000 if value > 500 else value
    return round(kg * 2) / 2


def sets(payload: Any) -> dict[str, Any] | None:
    """The session's exercises in order, each with its sets (reps and kg),
    and the totals. None when the watch logged no sets."""
    rows = (payload or {}).get("exerciseSets") if isinstance(payload, Mapping) else None
    exercises: list[dict[str, Any]] = []
    total_sets = total_reps = 0
    volume = 0.0
    for row in rows or []:
        if not isinstance(row, Mapping) or str(row.get("setType") or "ACTIVE").upper() != "ACTIVE":
            continue
        name = _exercise_name(row.get("exercises"))
        reps = row.get("repetitionCount")
        kg = _kg(row.get("weight"))
        secs = row.get("duration")
        one = drop_empty({"reps": int(reps) if reps else None, "kg": kg,
                          "seconds": round(secs) if secs and not reps else None})
        # Grouped by exercise in the order first done, so a superset's two
        # exercises each read as one.
        same = next((e for e in exercises if e["exercise"] == name), None)
        if same is not None:
            same["sets"].append(one)
        else:
            exercises.append({"exercise": name, "sets": [one]})
        total_sets += 1
        total_reps += int(reps or 0)
        volume += (kg or 0) * int(reps or 0)
    if not exercises:
        return None
    return drop_empty({
        "exercises": exercises,
        "total_sets": total_sets,
        "total_reps": total_reps or None,
        "volume_kg": round(volume) or None,
        "how_to_read": ("Exercises in the order done, each with its sets. kg missing means body weight or "
                        "not entered; the watch guesses exercises it isn't told, so a name can be wrong."),
    })
