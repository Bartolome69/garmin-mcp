"""How Garmin's fitness markers have moved over the past months.

get_fitness says where someone is today; this says which way they are going:
VO2 max, race predictions, lactate threshold, endurance and hill score, and
cycling FTP, a value per month and the change from first to last, with
weight by month beside them so the two can be lined up.

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
from .metrics import RACE_KEYS, RACE_METRES, threshold_speed

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


def _kg(value: Any) -> float | None:
    """Garmin keeps body masses in grams; take kilograms either way, or nothing implausible."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    kg = value / 1000 if value > 1000 else float(value)
    return kg if 20 <= kg <= 350 else None


def weigh_ins(raw: Any) -> list[dict[str, Any]]:
    """Each weigh-in as {day, kg, body_fat_pct, muscle_kg}, from Garmin's weight list.

    Read from the list itself rather than by walking the whole reply, because
    the reply also carries an average for the period that would otherwise pass
    for a weigh-in on its first day.
    """
    rows = (raw or {}).get("dateWeightList") if isinstance(raw, Mapping) else None
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        day = next((row[k] for k in DATE_KEYS if _is_date(row.get(k))), None)
        kg = _kg(row.get("weight"))
        if not day or kg is None:
            continue
        fat = row.get("bodyFat")
        out.append({
            "day": day[:10],
            "kg": kg,
            "body_fat_pct": float(fat) if isinstance(fat, (int, float)) and 2 <= fat <= 70 else None,
            "muscle_kg": _kg(row.get("muscleMass")),
        })
    return sorted(out, key=lambda r: r["day"])


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def weight_trend(raw: Any) -> dict[str, Any] | None:
    """Weight by month: the average of that month's weigh-ins, since one day's reading wobbles."""
    rows = weigh_ins(raw)
    if not rows:
        return None
    months: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        months.setdefault(row["day"][:7], []).append(row)
    by_month = []
    for month, entries in sorted(months.items()):
        fat = [e["body_fat_pct"] for e in entries if e["body_fat_pct"] is not None]
        muscle = [e["muscle_kg"] for e in entries if e["muscle_kg"] is not None]
        by_month.append(drop_empty({
            "month": month,
            "kg": rounded(_mean([e["kg"] for e in entries]), 1),
            "body_fat_pct": rounded(_mean(fat), 1),
            "muscle_kg": rounded(_mean(muscle), 1),
            "weigh_ins": len(entries),
        }))
    out: dict[str, Any] = {
        "by_month": by_month,
        "latest": {"day": rows[-1]["day"], "kg": rounded(rows[-1]["kg"], 1)},
    }
    if len(by_month) > 1:
        first, last = by_month[0], by_month[-1]
        out["since"] = first["month"]
        # Lighter isn't better or worse on its own, so no direction.
        out["change_kg"] = rounded(last["kg"] - first["kg"], 1)
        if "body_fat_pct" in first and "body_fat_pct" in last:
            out["body_fat_change_pct"] = rounded(last["body_fat_pct"] - first["body_fat_pct"], 1)
    return out


def shape(
    *,
    vo2: Any = None,
    race: Any = None,
    lactate: Any = None,
    endurance: Any = None,
    hill: Any = None,
    ftp: Any = None,
    weight: Any = None,
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
        "pace": _trend([(d, threshold_speed(v)) for d, v in series(lt.get("speed"), ("value", "speed"))],
                       _pace, STEADY["threshold_pace"],
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
        "weight": weight_trend(weight),
        "how_to_read": (
            "Each marker is Garmin's own estimate, one value per month (the last "
            "of the month), with the change from the first month to now. "
            "'steady' means the change is within the week-to-week wobble of the "
            "estimate. Race times and threshold pace improve by getting "
            "shorter. Markers only move when Garmin has qualifying runs or "
            "rides to recompute them from, so a flat line can mean few "
            "recordings rather than no change. Weight is the average of the "
            "month's weigh-ins logged in Garmin Connect, with body fat and "
            "muscle where a scale records them; set it beside the markers month "
            "by month to see how they moved together. That is not cause and "
            "effect: weight usually changes along with training, so don't put "
            "a figure on what each kilo is worth."
        ),
        "warnings": list(warnings) or None,
    })
