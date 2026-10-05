"""Recovery over weeks rather than a single morning.

get_readiness answers "should I train hard today". This answers "am I
absorbing this block": overnight HRV, resting heart rate, sleep, Body
Battery and stress, week by week beside the training load and kilometres
that produced them, with anything that has moved off its usual range named.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import mean
from typing import Any, Iterable, Mapping

from .formatting import drop_empty, km, rounded

DAILY_SHOWN = 14
RHR_RISE = 3
SHORT_SLEEP_HOURS = 6.5
LOAD_JUMP = 1.3
HRV_WORRY = {"UNBALANCED", "LOW", "POOR"}
RUNS = ("run", "treadmill", "track", "trail")


def _day(value: Any) -> str | None:
    text = str(value or "")[:10]
    return text if len(text) == 10 else None


def _avg(values: Iterable[Any], places: int = 0) -> float | None:
    present = [float(v) for v in values if isinstance(v, (int, float))]
    return round(mean(present), places) if present else None


def hrv_by_day(raw: Any) -> dict[str, dict[str, Any]]:
    rows = raw.get("hrvSummaries") if isinstance(raw, Mapping) else raw
    out = {}
    for row in rows or []:
        if isinstance(row, Mapping) and _day(row.get("calendarDate")):
            baseline = row.get("baseline") or {}
            out[_day(row["calendarDate"])] = {
                "ms": row.get("lastNightAvg"),
                "status": row.get("status"),
                "band": (baseline.get("balancedLow"), baseline.get("balancedUpper")),
            }
    return out


def rhr_by_day(raw: Any) -> dict[str, Any]:
    return {_day(r.get("calendarDate")): r.get("value") for r in raw or []
            if isinstance(r, Mapping) and _day(r.get("calendarDate"))}


def sleep_by_day(raw: Any) -> dict[str, dict[str, Any]]:
    out = {}
    for row in raw or []:
        if not isinstance(row, Mapping) or not _day(row.get("calendarDate")):
            continue
        values = row.get("values") if isinstance(row.get("values"), Mapping) else row
        secs = next((values.get(k) for k in ("totalSleepTimeInSeconds", "sleepTimeSeconds", "totalSleepSeconds")
                     if isinstance(values.get(k), (int, float))), None)
        score = next((values.get(k) for k in ("sleepScore", "overallSleepScore", "sleepScoreValue")
                      if isinstance(values.get(k), (int, float))), None)
        if score is None:
            score = ((values.get("sleepScores") or {}).get("overall") or {}).get("value")
        out[_day(row["calendarDate"])] = {"hours": secs / 3600 if secs else None, "score": score}
    return out


def _levels(row: Mapping[str, Any]) -> list[float]:
    samples = row.get("bodyBatteryValuesArray") or []
    index = None
    for d in row.get("bodyBatteryValueDescriptorDTOList") or []:
        if isinstance(d, Mapping) and d.get("bodyBatteryValueDescriptorKey") == "bodyBatteryLevel":
            index = d.get("bodyBatteryValueDescriptorIndex")
    levels = []
    for s in samples:
        if not isinstance(s, (list, tuple)) or len(s) < 2:
            continue
        i = index if isinstance(index, int) else (2 if len(s) >= 4 else 1)
        if i < len(s) and isinstance(s[i], (int, float)) and 0 <= s[i] <= 100:
            levels.append(float(s[i]))
    return levels


def battery_by_day(raw: Any) -> dict[str, dict[str, Any]]:
    out = {}
    for row in raw or []:
        if not isinstance(row, Mapping) or not _day(row.get("date") or row.get("calendarDate")):
            continue
        levels = _levels(row)
        out[_day(row.get("date") or row.get("calendarDate"))] = {
            "peak": max(levels) if levels else None,
            "low": min(levels) if levels else None,
            "charged": row.get("charged"),
            "drained": row.get("drained"),
        }
    return out


def training_by_day(activities: Any) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for a in activities or []:
        if not isinstance(a, Mapping):
            continue
        day = _day(a.get("startTimeLocal"))
        if not day:
            continue
        slot = out.setdefault(day, {"load": 0.0, "run_m": 0.0, "sessions": 0})
        slot["sessions"] += 1
        if isinstance(a.get("activityTrainingLoad"), (int, float)):
            slot["load"] += a["activityTrainingLoad"]
        kind = str((a.get("activityType") or {}).get("typeKey") or "").lower()
        if any(r in kind for r in RUNS) and isinstance(a.get("distance"), (int, float)):
            slot["run_m"] += a["distance"]
    return out


def _stress_for_week(stress: Any, monday: date) -> Any:
    for row in stress or []:
        d = _day(row.get("calendarDate")) if isinstance(row, Mapping) else None
        if d and monday - timedelta(days=1) <= date.fromisoformat(d) <= monday + timedelta(days=6):
            return row.get("value")
    return None


def shape_trends(start: date, today: date, hrv: Any, rhr: Any, sleep: Any, battery: Any,
                 stress: Any, activities: Any, warnings: list[str]) -> dict[str, Any]:
    h, r, s, b, t = hrv_by_day(hrv), rhr_by_day(rhr), sleep_by_day(sleep), battery_by_day(battery), training_by_day(activities)
    days = [start + timedelta(days=i) for i in range((today - start).days + 1)]

    def get(table: Mapping[str, Any], d: date, key: str | None = None) -> Any:
        row = table.get(d.isoformat())
        return row if key is None or row is None else row.get(key)

    weeks = []
    monday = start
    while monday <= today:
        week = [d for d in days if monday <= d < monday + timedelta(days=7)]
        weeks.append(drop_empty({
            "week_of": monday.isoformat(),
            "days_so_far": len(week) if len(week) < 7 else None,
            "hrv_ms": _avg(get(h, d, "ms") for d in week),
            "resting_hr": _avg(get(r, d) for d in week),
            "sleep_hours": _avg((get(s, d, "hours") for d in week), 1),
            "sleep_score": _avg(get(s, d, "score") for d in week),
            "body_battery_peak": _avg(get(b, d, "peak") for d in week),
            "stress": rounded(_stress_for_week(stress, monday), 0),
            "training_load": round(sum(get(t, d, "load") or 0 for d in week)) or None,
            "run_km": km(sum(get(t, d, "run_m") or 0 for d in week), 1) or None,
            "sessions": sum(get(t, d, "sessions") or 0 for d in week) or None,
        }))
        monday += timedelta(days=7)

    last7 = days[-7:]
    before = days[:-7]
    signals = []
    rhr_norm = _avg((get(r, d) for d in before), 1)
    rhr_now = _avg((get(r, d) for d in days[-3:]), 1)
    if rhr_norm and rhr_now and rhr_now - rhr_norm >= RHR_RISE:
        signals.append(f"Resting heart rate {round(rhr_now - rhr_norm)} bpm above its norm over the last 3 days.")
    statuses = [get(h, d, "status") for d in days[-3:] if get(h, d, "status")]
    if statuses and all(str(x).upper() in HRV_WORRY for x in statuses):
        signals.append(f"HRV status {str(statuses[-1]).lower()} for the last {len(statuses)} nights.")
    hrv_norm = _avg(get(h, d, "ms") for d in before)
    hrv_now = _avg(get(h, d, "ms") for d in last7)
    if hrv_norm and hrv_now and (hrv_norm - hrv_now) / hrv_norm >= 0.08:
        signals.append(f"Overnight HRV averaging {round(hrv_now)} ms this week against {round(hrv_norm)} before it.")
    sleep_now = _avg((get(s, d, "hours") for d in last7), 1)
    if sleep_now and sleep_now < SHORT_SLEEP_HOURS:
        signals.append(f"Averaging {sleep_now} hours of sleep over the last 7 nights.")
    load_now = sum(get(t, d, "load") or 0 for d in last7)
    earlier_weeks = [sum(get(t, d, "load") or 0 for d in before[i : i + 7]) for i in range(0, len(before) - 6, 7)]
    load_norm = mean(earlier_weeks) if earlier_weeks else 0
    if load_norm and load_now / load_norm >= LOAD_JUMP:
        signals.append(f"Training load over the last 7 days is {round((load_now / load_norm - 1) * 100)}% above the weeks before.")

    latest_hrv = next((h[d.isoformat()] for d in reversed(days) if d.isoformat() in h), None)
    daily = [drop_empty({
        "date": d.isoformat(),
        "hrv_ms": rounded(get(h, d, "ms"), 0),
        "resting_hr": get(r, d),
        "sleep_hours": rounded(get(s, d, "hours"), 1),
        "sleep_score": get(s, d, "score"),
        "body_battery_peak": rounded(get(b, d, "peak"), 0),
        "training_load": round(get(t, d, "load")) if get(t, d, "load") else None,
        "run_km": km(get(t, d, "run_m"), 1) if get(t, d, "run_m") else None,
    }) for d in days[-DAILY_SHOWN:]]

    return drop_empty({
        "from": start.isoformat(),
        "to": today.isoformat(),
        "hrv_now": drop_empty({
            "status": latest_hrv.get("status"),
            "last_night_ms": rounded(latest_hrv.get("ms"), 0),
            "balanced_range_ms": [rounded(x, 0) for x in latest_hrv["band"]] if all(latest_hrv.get("band") or ()) else None,
        }) if latest_hrv else None,
        "overnight": (
            "No HRV or sleep recorded in this window. Both need the watch worn "
            "overnight, so the trend here is resting heart rate, Body Battery, "
            "stress and load only."
        ) if not h and not any(v.get("hours") or v.get("score") for v in s.values()) else None,
        "signals": signals or ["Nothing has moved off its usual range."],
        "weeks": weeks,
        "daily": [d for d in daily if len(d) > 1] or None,
        "warnings": warnings or None,
        "how_to_read": (
            "Read the weeks top to bottom: load and km against HRV, resting heart "
            "rate, sleep and Body Battery. Rising load with steady or rising HRV "
            "and steady resting HR is a block being absorbed; HRV falling and "
            "resting HR rising for several days together is the usual sign to "
            "ease off. The current week may be partial (days_so_far). For "
            "today's call use get_readiness, which also has the four-week load "
            "focus (easy, threshold, hard) against Garmin's targets."
        ),
    })
