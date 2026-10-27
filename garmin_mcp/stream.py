"""Look inside an activity: what the per-second recording says that laps cannot.

The splits endpoint returns one row per lap. When a workout step is one 7 km
lap, that row is an average, and the question a coach actually asks, "did the
heart rate climb through the effort at a steady pace?", has no answer in it.
Garmin's details endpoint has the recording itself, a few thousand samples of
time, distance, speed and heart rate. This module turns that into two small
things: kilometre splits when the watch did not lap by kilometre, and, for
every lap long enough to mean anything, heart rate and pace across the first
third against the last.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .formatting import drop_empty, pace_per_km, rounded

# Laps shorter than this are intervals or recoveries; drift across them is
# noise, and reporting it would invite over-reading.
MIN_LAP_SECONDS = 300
MIN_LAP_SAMPLES = 9

# Watches that lap by kilometre already give per-km splits; do not repeat them.
AUTO_LAP_TOLERANCE = 0.05

_SCALE = {
    "meter": 1.0,
    "centimeter": 0.01,
    "kilometer": 1000.0,
    "second": 1.0,
    "millisecond": 0.001,
    "minute": 60.0,
}


def parse_stream(details: Any) -> dict[str, list[float | None]]:
    """Columns keyed by Garmin's metric name, scaled to metres, seconds, m/s, bpm."""
    if not isinstance(details, Mapping):
        return {}
    descriptors = details.get("metricDescriptors") or []
    rows = details.get("activityDetailMetrics") or []
    if not descriptors or not rows:
        return {}

    columns: dict[str, list[float | None]] = {}
    for desc in descriptors:
        key = desc.get("key")
        index = desc.get("metricsIndex")
        if not isinstance(key, str) or not isinstance(index, int):
            continue
        unit = ((desc.get("unit") or {}).get("key") or "").lower()
        scale = _SCALE.get(unit, 1.0)
        values: list[float | None] = []
        for row in rows:
            metrics = row.get("metrics") if isinstance(row, Mapping) else None
            value = metrics[index] if isinstance(metrics, Sequence) and index < len(metrics) else None
            values.append(float(value) * scale if isinstance(value, (int, float)) else None)
        columns[key] = values
    return columns


def _mean(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _pace(distance: Sequence[float | None], seconds: Sequence[float | None], lo: int, hi: int) -> str | None:
    """Pace over sample indices [lo, hi), from the cumulative columns."""
    if hi - lo < 2:
        return None
    d0, d1 = distance[lo], distance[hi - 1]
    t0, t1 = seconds[lo], seconds[hi - 1]
    if None in (d0, d1, t0, t1) or d1 - d0 <= 0 or t1 - t0 <= 0:
        return None
    return pace_per_km(d1 - d0, t1 - t0)


def km_splits(columns: Mapping[str, Sequence[float | None]], limit: int = 60) -> list[dict[str, Any]]:
    """Per-kilometre time and heart rate, interpolated at each kilometre mark."""
    distance = columns.get("sumDistance")
    seconds = columns.get("sumDuration") or columns.get("sumElapsedDuration")
    hr = columns.get("directHeartRate") or []
    if not distance or not seconds:
        return []

    splits: list[dict[str, Any]] = []
    mark = 1000.0
    start_t = 0.0
    start_i = 0
    for i in range(1, len(distance)):
        d, t = distance[i], seconds[i]
        d_prev, t_prev = distance[i - 1], seconds[i - 1]
        if None in (d, t, d_prev, t_prev):
            continue
        while d >= mark - 0.5 and len(splits) < limit:
            # Linear interpolation of the time the kilometre mark was crossed.
            frac = (mark - d_prev) / (d - d_prev) if d > d_prev else 1.0
            t_mark = t_prev + frac * (t - t_prev)
            window_hr = _mean(hr[start_i : i + 1]) if hr else None
            splits.append(
                drop_empty(
                    {
                        "km": len(splits) + 1,
                        "time_s": round(t_mark - start_t),
                        "pace_per_km": pace_per_km(1000.0, t_mark - start_t),
                        "avg_hr": rounded(window_hr, 0),
                    }
                )
            )
            start_t, start_i = t_mark, i
            mark += 1000.0
    return splits


def lap_drift(
    columns: Mapping[str, Sequence[float | None]], laps: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """First third against last third of each long lap, aligned by elapsed time."""
    seconds = columns.get("sumDuration") or columns.get("sumElapsedDuration")
    distance = columns.get("sumDistance")
    hr = columns.get("directHeartRate")
    if not seconds or not distance or not hr:
        return []

    out: list[dict[str, Any]] = []
    cursor = 0.0
    for index, lap in enumerate(laps, start=1):
        length = lap.get("duration") or lap.get("elapsedDuration") or lap.get("movingDuration")
        if not isinstance(length, (int, float)) or length <= 0:
            continue
        lo_t, hi_t = cursor, cursor + float(length)
        cursor = hi_t
        if length < MIN_LAP_SECONDS:
            continue
        idx = [i for i, t in enumerate(seconds) if t is not None and lo_t <= t <= hi_t]
        if len(idx) < MIN_LAP_SAMPLES:
            continue
        third = len(idx) // 3
        first, last = idx[:third], idx[-third:]
        hr_first, hr_last = _mean([hr[i] for i in first]), _mean([hr[i] for i in last])
        out.append(
            drop_empty(
                {
                    "split": lap.get("lapIndex") or index,
                    "hr_first_third": rounded(hr_first, 0),
                    "hr_last_third": rounded(hr_last, 0),
                    "hr_drift_bpm": rounded(hr_last - hr_first, 0)
                    if hr_first is not None and hr_last is not None
                    else None,
                    "pace_first_third": _pace(distance, seconds, first[0], first[-1] + 1),
                    "pace_last_third": _pace(distance, seconds, last[0], last[-1] + 1),
                }
            )
        )
    return out


def _auto_lapped_by_km(laps: Sequence[Mapping[str, Any]]) -> bool:
    lengths = [lap.get("distance") for lap in laps if isinstance(lap.get("distance"), (int, float))]
    if len(lengths) < 2:
        return False
    body = lengths[:-1]  # the last lap is usually the leftover
    return all(abs(d - 1000.0) <= 1000.0 * AUTO_LAP_TOLERANCE for d in body)


def analyse(details: Any, laps: Sequence[Mapping[str, Any]] | None) -> dict[str, Any] | None:
    columns = parse_stream(details)
    if not columns:
        return None
    laps = list(laps or [])
    result = drop_empty(
        {
            "sample_count": len(next(iter(columns.values()))),
            "km_splits": None if _auto_lapped_by_km(laps) else (km_splits(columns) or None),
            "laps": lap_drift(columns, laps) or None,
            "how_to_read": (
                "hr_drift_bpm is the last third of a lap minus the first, at the "
                "paces shown. Rising heart rate at level pace is cardiac drift: "
                "normal after 40 minutes in the heat, a fitness signal in a "
                "short steady effort. km_splits appear only when the watch did "
                "not already lap by kilometre."
            ),
        }
    )
    return result if (result.get("km_splits") or result.get("laps")) else None
