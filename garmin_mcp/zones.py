"""The user's own heart rate and power zones, as Garmin has them set.

Read for get_fitness, and to turn a workout step's "z2" into the bpm (or
watts) of the person's zone 2, so a plan built on zones runs on the same
zones as their watch. Garmin keeps a set per sport (running, cycling, a
default for the rest); each zone is given by its floor, and the top of one is
the floor of the next, less one.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping, Sequence

from .formatting import drop_empty
from .workouts import WorkoutError

# "z2", "Z2", "zone 2", "zone2", and ranges such as "z2-z3", "zone 3 to 4".
_ZONE = re.compile(r"^\s*z(?:one)?\s*([1-9])\s*(?:(?:-|–|to)\s*(?:z(?:one)?\s*)?([1-9]))?\s*$", re.IGNORECASE)
_SPORTS = {"running": "RUNNING", "cycling": "CYCLING"}


def _rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        payload = payload.get("zones") or [payload]
    return [r for r in payload or [] if isinstance(r, Mapping)]


def _pick(rows: list[Mapping[str, Any]], sport: str) -> Mapping[str, Any] | None:
    want = _SPORTS.get(sport, "DEFAULT")
    by_sport = {str(r.get("sport") or "DEFAULT").upper(): r for r in rows}
    return by_sport.get(want) or by_sport.get("DEFAULT") or (rows[0] if rows else None)


def _floors(row: Mapping[str, Any]) -> list[float]:
    floors = []
    for i in range(1, 11):
        value = row.get(f"zone{i}Floor")
        if not isinstance(value, (int, float)) or value <= 0:
            break
        floors.append(float(value))
    return floors


def _bands(floors: list[float], top: float | None) -> list[dict[str, Any]]:
    bands = []
    for i, floor in enumerate(floors):
        high = floors[i + 1] - 1 if i + 1 < len(floors) else top
        bands.append({"zone": i + 1, "range": [int(floor), int(high) if high else None]})
    return bands


def heart_rate(payload: Any, sport: str = "running") -> dict[str, Any] | None:
    """The person's heart rate zones for a sport, in bpm."""
    row = _pick(_rows(payload), sport)
    floors = _floors(row) if row else []
    if not floors:
        return None
    top = row.get("maxHeartRateUsed")
    method = str(row.get("trainingMethod") or "").upper()
    return drop_empty({
        "sport": str(row.get("sport") or "DEFAULT").lower(),
        "based_on": {"HR_RESERVE": "heart rate reserve", "PERCENT_MAX_HR": "% of max heart rate",
                     "LACTATE_THRESHOLD": "lactate threshold"}.get(method),
        "max_hr": top,
        "resting_hr": row.get("restingHeartRateUsed"),
        "threshold_hr": row.get("lactateThresholdHeartRateUsed"),
        "zones": [{"zone": b["zone"], "bpm": b["range"]} for b in _bands(floors, top)],
    })


def power(payload: Any, sport: str = "cycling") -> dict[str, Any] | None:
    """The person's power zones for a sport, in watts."""
    row = _pick(_rows(payload), sport)
    floors = _floors(row) if row else []
    if not floors:
        return None
    return drop_empty({
        "sport": str(row.get("sport") or "DEFAULT").lower(),
        "ftp": row.get("functionalThresholdPower"),
        "zones": [{"zone": b["zone"], "watts": b["range"]} for b in _bands(floors, None)],
    })


def is_zone(value: Any) -> bool:
    if isinstance(value, (list, tuple)):
        return len(value) == 2 and all(isinstance(v, str) and _ZONE.match(v) for v in value)
    return isinstance(value, str) and bool(_ZONE.match(value))


def _span(value: Any) -> tuple[int, int]:
    if isinstance(value, (list, tuple)):
        a, b = (int(_ZONE.match(v).group(1)) for v in value)
    else:
        m = _ZONE.match(value)
        a = int(m.group(1))
        b = int(m.group(2) or a)
    return min(a, b), max(a, b)


def uses(steps: Sequence[Mapping[str, Any]] | None, key: str) -> bool:
    """Whether any step, repeats included, sets heart rate ("hr") or "power" as a zone."""
    for step in steps or []:
        if not isinstance(step, Mapping):
            continue
        if uses(step.get("steps"), key) or is_zone(step.get(key)):
            return True
    return False


def resolve(steps: Any, hr: Mapping[str, Any] | None = None, watts: Mapping[str, Any] | None = None) -> Any:
    """The steps with each zone target ("z2") turned into the person's own range."""
    if not isinstance(steps, list):
        return steps
    out = copy.deepcopy(steps)

    def fill(step: dict[str, Any], key: str, zones: Mapping[str, Any] | None, unit: str, what: str) -> None:
        value = step.get(key)
        if not is_zone(value):
            return
        bands = (zones or {}).get("zones") or []
        if not bands:
            raise WorkoutError(
                f"Garmin has no {what} zones set for this person, so {value!r} can't be turned into "
                f"{unit}. Give the target as {unit} instead."
            )
        low_zone, high_zone = _span(value)
        if high_zone > len(bands):
            raise WorkoutError(f"Their {what} zones go up to zone {len(bands)}; {value!r} is past that.")
        low = bands[low_zone - 1][unit][0]
        high = bands[high_zone - 1][unit][1] or (low + (30 if unit == "watts" else 10))
        step[key] = [low, high]

    def walk(items: list[Any]) -> None:
        for step in items:
            if not isinstance(step, dict):
                continue
            fill(step, "hr", hr, "bpm", "heart rate")
            fill(step, "power", watts, "watts", "power")
            if isinstance(step.get("steps"), list):
                walk(step["steps"])

    walk(out)
    return out
