"""Rides: power, and the numbers a cyclist reads a ride by.

A ride carries the same activity record as a run, so without this it reads
like one: a "pace" of 2:13 per km and cadence in steps. Here speed replaces
pace, and power gets what a power meter is bought for: normalised power,
intensity against FTP, training stress, the best efforts across durations,
and time in each power zone.
"""

from __future__ import annotations

from typing import Any, Mapping

from .formatting import drop_empty, duration, first_present, rounded

RIDE_WORDS = ("cycling", "biking", "ride", "bike")

# Best average power over these durations, as Garmin keeps them on the
# activity (maxAvgPower_<seconds>).
BEST_EFFORTS = [(5, "5s"), (60, "1min"), (300, "5min"), (1200, "20min"), (3600, "60min")]


def is_ride(type_key: Any) -> bool:
    key = str(type_key or "").lower()
    return any(word in key for word in RIDE_WORDS)


def _positive(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def power(data: Mapping[str, Any], ftp: float | None = None, weight_kg: float | None = None,
          ride: bool = False) -> dict[str, Any]:
    """The power block for one activity or lap, richest where Garmin gives the most."""
    avg = _positive(first_present(data, "avgPower", "averagePower"))
    norm = _positive(first_present(data, "normPower", "normalizedPower"))
    # Garmin's own intensity and stress use the FTP it had on the day; work
    # them out from today's only when it gives none.
    intensity = _positive(data.get("intensityFactor"))
    if intensity is None and norm and ftp:
        intensity = norm / ftp
    tss = _positive(data.get("trainingStressScore"))
    secs = _positive(first_present(data, "movingDuration", "duration", "elapsedDuration"))
    if tss is None and intensity and norm and ftp and secs:
        tss = secs * norm * intensity / (ftp * 3600) * 100
    # Work is average power over the time pedalled. Garmin's totalWork is in
    # kilocalories (a 1,490 kJ ride reads 358), so it is only the fallback.
    work = _positive(data.get("totalWork"))
    work_kj = None
    if ride:
        work_kj = avg * secs / 1000 if avg and secs else (work * 4.184 if work else None)
    best = {
        label: rounded(data.get(f"maxAvgPower_{seconds}"), 0)
        for seconds, label in BEST_EFFORTS
        if _positive(data.get(f"maxAvgPower_{seconds}"))
    }
    left = _positive(first_present(data, "avgLeftBalance", "leftBalance", "avgLeftRightBalance"))
    return drop_empty({
        "average_w": rounded(avg, 0),
        "normalized_w": rounded(norm, 0),
        "max_w": rounded(_positive(data.get("maxPower")), 0),
        "max_20min_w": rounded(_positive(data.get("max20MinPower")), 0),
        "intensity_factor": rounded(intensity, 2),
        "training_stress_score": rounded(tss, 0),
        "variability_index": rounded(norm / avg, 2) if norm and avg else None,
        "work_kj": rounded(work_kj, 0),
        "normalized_w_per_kg": rounded(norm / weight_kg, 2) if norm and weight_kg else None,
        "best_efforts_w": best or None,
        "left_right_balance": f"{round(left)}/{round(100 - left)}" if left and left < 100 else None,
    })


def shape_ftp(raw: Any, weight_kg: float | None = None) -> dict[str, Any] | None:
    """The cycling FTP set in Garmin, from latestFunctionalThresholdPower."""
    row = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(row, Mapping):
        return None
    watts = _positive(first_present(row, "functionalThresholdPower", "ftp", "value"))
    if watts is None:
        return None
    return drop_empty({
        "watts": round(watts),
        "w_per_kg": round(watts / weight_kg, 2) if weight_kg else None,
        "as_of": str(first_present(row, "calendarDate", "updatedDate", "createDate") or "")[:10] or None,
        "source": first_present(row, "origin", "source", "biometricSourceType"),
    })


def power_zones(raw: Any) -> list[dict[str, Any]] | None:
    """Time in each power zone, from /activity/{id}/powerTimeInZones."""
    rows = [z for z in (raw or []) if isinstance(z, Mapping)] if isinstance(raw, list) else []
    if not rows:
        return None
    total = sum(float(z.get("secsInZone") or 0) for z in rows)
    if not total:
        return None
    return [
        drop_empty({
            "zone": z.get("zoneNumber"),
            "low_w": rounded(z.get("zoneLowBoundary"), 0),
            "time": duration(float(z.get("secsInZone") or 0)),
            "percent": round(float(z.get("secsInZone") or 0) / total * 100, 1),
        })
        for z in sorted(rows, key=lambda z: z.get("zoneNumber") or 0)
    ]


def weight_kg(profile: Any) -> float | None:
    """Body weight from the user settings Garmin keeps, in grams."""
    data = (profile or {}).get("userData") if isinstance(profile, Mapping) else None
    grams = _positive((data or {}).get("weight"))
    if grams is None:
        return None
    return round(grams / 1000, 1) if grams > 500 else grams


def how_to_read() -> str:
    return (
        "normalized_w is the steady power the ride's effort was worth, allowing "
        "for surges. intensity_factor is that against FTP: under 0.75 easy, "
        "0.75-0.85 endurance or tempo, 0.85-0.95 a hard sustained ride, about "
        "1.0 a race-pace hour. training_stress_score of 100 is an hour at FTP. "
        "variability_index over 1.1 means a surgy ride (group, hills, "
        "intervals); near 1.0 is steady."
    )

