"""Date ranges for the one-day health tools (Nov 26).

In the 14 days to Nov 26, 35% of all tool calls came in runs of ten or more
calls to the same tool, mostly get_daily_summary, get_sleep_data and
get_readiness read one day at a time (one chat read 3,238 days of summaries
in a row). Each of those tools now also takes start_date and end_date (or
days) and answers with one compact row a day, so a month is one call.

The days are still read one by one underneath, from the cache where they are
there already, so a range answers exactly what calling day by day would, cut
down to the row.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .formatting import DateError, drop_empty, parse_date

MAX_DAYS = 90

# The fields kept for each day, by tool, as (row key, path into the one-day answer).
ROWS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "get_daily_summary": (
        ("steps", ("steps",)),
        ("resting_hr", ("heart_rate", "resting_bpm")),
        ("body_battery_high", ("body_battery", "highest")),
        ("body_battery_low", ("body_battery", "lowest")),
        ("body_battery_charged", ("body_battery", "charged")),
        ("body_battery_drained", ("body_battery", "drained")),
        ("stress_avg", ("stress", "average")),
        ("stress_high_min", ("stress", "high_minutes")),
        ("intensity_moderate", ("intensity_minutes", "moderate")),
        ("intensity_vigorous", ("intensity_minutes", "vigorous")),
        ("calories_active", ("calories", "active")),
        ("distance_km", ("distance_km",)),
    ),
    "get_sleep_data": (
        ("sleep_hours", ("total_sleep_hours",)),
        ("score", ("score", "overall")),
        ("rating", ("score", "rating")),
        ("deep_min", ("stages", "deep", "minutes")),
        ("rem_min", ("stages", "rem", "minutes")),
        ("light_min", ("stages", "light", "minutes")),
        ("awake_min", ("stages", "awake", "minutes")),
        ("asleep_at", ("asleep_at",)),
        ("awake_at", ("awake_at",)),
        ("hrv_ms", ("average_hrv_ms",)),
        ("resting_hr", ("resting_heart_rate",)),
        ("breathing_avg", ("breathing", "average")),
        ("spo2_avg", ("spo2", "average")),
        ("body_battery_change", ("body_battery_change",)),
    ),
    "get_readiness": (
        ("readiness", ("readiness", "score")),
        ("level", ("readiness", "level")),
        ("recovery_hours", ("readiness", "recovery_time_hours")),
        ("training_status", ("training_status", "status")),
        ("acute_load", ("training_status", "acute_load")),
        ("chronic_load", ("training_status", "chronic_load")),
        ("load_ratio", ("training_status", "acute_chronic_ratio")),
        ("hrv_ms", ("hrv", "last_night_ms")),
        ("hrv_status", ("hrv", "status")),
        ("sleep_score", ("sleep", "score")),
        ("resting_hr", ("resting_hr", "today")),
        ("stress_avg", ("stress", "average")),
    ),
}

RANGE_NOTE = "One row a day, oldest first. Days with nothing recorded say so."
DESCRIPTION_LINE = (
    "For more than one day, pass start_date and end_date (or days) instead of calling once per day: "
    f"one call answers up to {MAX_DAYS} days, one compact row a day."
)


def wants_range(start_date: Any = None, end_date: Any = None, days: Any = None) -> bool:
    return any(v not in (None, "", 0) for v in (start_date, end_date, days))


def days_between(start_date: Any = None, end_date: Any = None, days: Any = None,
                 today: date | None = None) -> list[str]:
    """The days a range covers, oldest first, as YYYY-MM-DD.

    days alone counts back from end_date (today by default); start_date alone
    runs to today; end_date alone is that one day. Either order of the two
    dates is read the right way round. More than MAX_DAYS is refused.
    """
    today = today or date.today()
    end = date.fromisoformat(parse_date(end_date)) if end_date not in (None, "") else today
    if days not in (None, "", 0):
        try:
            count = int(str(days).strip())
        except ValueError:
            raise DateError(f"days must be a whole number of days, got {days!r}.") from None
        if count < 1:
            raise DateError("days must be 1 or more.")
        if start_date not in (None, ""):
            start = date.fromisoformat(parse_date(start_date))
            end = start + timedelta(days=count - 1) if end_date in (None, "") else end
        else:
            start = end - timedelta(days=count - 1)
    elif start_date not in (None, ""):
        start = date.fromisoformat(parse_date(start_date))
    else:
        start = end
    if start > end:
        start, end = end, start
    span = (end - start).days + 1
    if span > MAX_DAYS:
        raise DateError(
            f"That range is {span} days; one call covers at most {MAX_DAYS}. "
            f"Ask for the last {MAX_DAYS} days, or split it into {MAX_DAYS}-day ranges."
        )
    return [(start + timedelta(days=i)).isoformat() for i in range(span)]


def _dig(answer: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(answer, dict):
            return None
        answer = answer.get(key)
    return answer


def row(tool: str, day: str, answer: Any) -> dict[str, Any]:
    """One day of a range: the compact fields from that day's full answer."""
    if isinstance(answer, BaseException):
        return {"date": day, "missing": f"not read ({type(answer).__name__})"}
    if not isinstance(answer, dict):
        return {"date": day, "missing": "nothing recorded"}
    if answer.get("error"):
        return {"date": day, "missing": "not read"}
    kept = drop_empty({key: _dig(answer, path) for key, path in ROWS[tool]})
    if not kept:
        return {"date": day, "missing": "nothing recorded"}
    return {"date": day, **kept}


def averages(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The mean of each number across the days that have it."""
    sums: dict[str, list[float]] = {}
    for r in rows:
        for key, value in r.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                sums.setdefault(key, []).append(float(value))
    return {k: round(sum(v) / len(v), 1) for k, v in sums.items() if v}


def answer(tool: str, days: list[str], answers: list[Any], source: str | None = None) -> dict[str, Any]:
    rows = [row(tool, d, a) for d, a in zip(days, answers)]
    recorded = [r for r in rows if "missing" not in r]
    return drop_empty({
        "start": days[0],
        "end": days[-1],
        "days": len(days),
        "days_with_data": len(recorded),
        "averages": averages(recorded) or None,
        "rows": rows,
        "note": RANGE_NOTE,
        "source": source,
    })


def shape(tool: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """How a call asked for its dates, for tool_called: range_days or single_date."""
    try:
        if tool in ROWS and wants_range(kwargs.get("start_date"), kwargs.get("end_date"), kwargs.get("days")):
            return {"range_days": len(days_between(kwargs.get("start_date"), kwargs.get("end_date"),
                                                   kwargs.get("days")))}
        if tool == "get_activities" and (kwargs.get("start_date") or kwargs.get("end_date")):
            start = date.fromisoformat(parse_date(kwargs.get("start_date") or kwargs.get("end_date")))
            end = date.fromisoformat(parse_date(kwargs.get("end_date"))) if kwargs.get("end_date") else date.today()
            span = abs((end - start).days) + 1
            return drop_empty({"range_days": span, "single_date": span == 1 or None})
    except (DateError, ValueError):
        return {}
    if tool in ROWS or tool == "get_heart_rate":
        return {"single_date": True}
    return {}

