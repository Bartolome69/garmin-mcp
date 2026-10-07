"""How Garmin's fitness markers have moved over the past months.

get_fitness says where someone is today; this says which way they are going:
VO2 max, race predictions, lactate threshold, endurance and hill score, and
cycling FTP, a value per month and the change from first to last.

Garmin's range endpoints don't share a shape. Some return a list of rows with
a date and a value, some a map keyed by date, some nest the rows a level down.
So rather than trust one shape per endpoint, the readers below look for a date
beside the value they want wherever it sits, and a metric whose shape changes
comes back missing rather than wrong.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterable

from .formatting import drop_empty, duration, pace_per_km, rounded
from .metrics import RACE_KEYS, RACE_METRES

DATE_KEYS = ("calendarDate", "fromCalendarDate", "from", "startDate", "date")
# A first-to-last change smaller than this is "steady": Garmin's estimates
# wobble by about this much from week to week without anything changing.
STEADY = {
    "vo2max": 0.5,
    "race": 0.01,  # 1% of the finish time
    "threshold_pace": 0.01,
    "threshold_hr": 2,
    "score": 0.02,  # 2% of the score
    "ftp": 0.02,
}


def _is_date(text: Any) -> bool:
    return isinstance(text, str) and len(text) >= 10 and text[4] == "-" and text[7] == "-"


def series(raw: Any, keys: Iterable[str]) -> list[tuple[str, float]]:
    """(date, value) pairs for the first of `keys` found beside a date, anywhere in `raw`."""
    keys = tuple(keys)
    found: dict[str, float] = {}

    def value_of(row: Mapping[str, Any]) -> float | None:
        for key in keys:
            v = row.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                return float(v)
        return None

    def walk(node: Any, date_hint: str | None = None) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, date_hint)
        elif isinstance(node, Mapping):
            day = next((node[k] for k in DATE_KEYS if _is_date(node.get(k))), None) or date_hint
            v = value_of(node)
            if day and v is not None:
                found[day[:10]] = v
            for key, child in node.items():
                if isinstance(child, (list, Mapping)):
                    walk(child, key if _is_date(key) else day)

    walk(raw)
    return sorted(found.items())


def monthly(points: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """The last value in each month."""
    by_month: dict[str, float] = {}
    for day, value in sorted(points):
        by_month[day[:7]] = value
    return sorted(by_month.items())


def _direction(first: float, last: float, steady: float, *, higher_is_better: bool, relative: bool) -> str:
    change = last - first
    limit = abs(first) * steady if relative else steady
    if abs(change) <= limit:
        return "steady"
    return "improving" if (change > 0) == higher_is_better else "declining"


def _trend(
    points: list[tuple[str, float]],
    show: Any,
    steady: float,
    *,
    higher_is_better: bool,
    relative: bool = False,
    change: Any = None,
) -> dict[str, Any] | None:
    months = monthly(points)
    if not months:
        return None
    first, last = months[0][1], months[-1][1]
    out: dict[str, Any] = {
        "by_month": [{"month": m, "value": show(v)} for m, v in months],
        "now": show(last),
    }
    if len(months) > 1:
        out["since"] = months[0][0]
        out["then"] = show(first)
        out["change"] = (change or (lambda a, b: rounded(b - a, 1)))(first, last)
        out["direction"] = _direction(first, last, steady, higher_is_better=higher_is_better, relative=relative)
    return out


def _signed_duration(first: float, last: float) -> str:
    secs = last - first
    return ("-" if secs < 0 else "+") + duration(abs(secs))


def _pace(speed: float) -> str | None:
    return pace_per_km(1000.0, 1000.0 / speed) if speed > 0 else None


def _signed_pace(first: float, last: float) -> str:
    """Change in seconds per km; negative is faster."""
    secs = 1000.0 / last - 1000.0 / first
    return f"{'-' if secs < 0 else '+'}{abs(round(secs))}s/km"


def shape(
    *,
    vo2: Any = None,
    race: Any = None,
    lactate: Any = None,
    endurance: Any = None,
    hill: Any = None,
    ftp: Any = None,
    start: str,
    end: str,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    run_vo2 = cycling_vo2 = []
    if isinstance(vo2, list):
        run_vo2 = series([r.get("generic") for r in vo2 if isinstance(r, Mapping)],
                         ("vo2MaxPreciseValue", "vo2MaxValue"))
        cycling_vo2 = series([r.get("cycling") for r in vo2 if isinstance(r, Mapping)],
                             ("vo2MaxPreciseValue", "vo2MaxValue"))

    races: dict[str, Any] = {}
    for key, name in RACE_KEYS:
        t = _trend(series(race, (key,)), duration, STEADY["race"],
                   higher_is_better=False, relative=True, change=_signed_duration)
        if t:
            t["pace_per_km_now"] = pace_per_km(RACE_METRES[name], monthly(series(race, (key,)))[-1][1])
            races[name] = t

    lt = lactate if isinstance(lactate, Mapping) else {}
    threshold = drop_empty({
        "pace": _trend(series(lt.get("speed"), ("value", "speed")), _pace, STEADY["threshold_pace"],
                       higher_is_better=True, relative=True, change=_signed_pace),
        "heart_rate_bpm": _trend(series(lt.get("heart_rate"), ("value", "heartRate", "hearRate")),
                                 lambda v: rounded(v, 0), STEADY["threshold_hr"],
                                 # A higher threshold heart rate isn't better or worse on its own.
                                 higher_is_better=True),
    })
    if "heart_rate_bpm" in threshold:
        threshold["heart_rate_bpm"].pop("direction", None)

    score = lambda v: rounded(v, 0)  # noqa: E731
    return drop_empty({
        "period": {"from": start, "to": end},
        "vo2max": _trend(run_vo2, lambda v: rounded(v, 1), STEADY["vo2max"], higher_is_better=True),
        "cycling_vo2max": _trend(cycling_vo2, lambda v: rounded(v, 1), STEADY["vo2max"], higher_is_better=True),
        "race_predictions": races or None,
        "lactate_threshold": threshold or None,
        "endurance_score": _trend(series(endurance, ("overallScore", "groupAverage", "value")), score,
                                  STEADY["score"], higher_is_better=True, relative=True),
        "hill_score": _trend(series(hill, ("overallScore", "value")), score,
                             STEADY["score"], higher_is_better=True, relative=True),
        "cycling_ftp_watts": _trend(series(ftp, ("functionalThresholdPower", "value")), score,
                                    STEADY["ftp"], higher_is_better=True, relative=True),
        "how_to_read": (
            "Each marker is Garmin's own estimate, one value per month (the last "
            "of the month), with the change from the first month to now. "
            "'steady' means the change is within the week-to-week wobble of the "
            "estimate. Race times and threshold pace improve by getting "
            "shorter. Markers only move when Garmin has qualifying runs or "
            "rides to recompute them from, so a flat line can mean few "
            "recordings rather than no change."
        ),
        "warnings": list(warnings) or None,
    })
