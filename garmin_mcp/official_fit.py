"""The summary figures inside an activity's FIT file, for Garmin's official API.

Garmin's official API sends an activity's summary and its samples, but not
what the watch worked out about the session: time in the person's own heart
rate zones, running dynamics, training effect, power. Those sit in the FIT
file the watch recorded, which Garmin offers for download when the activity
arrives. This reads that file once, keeps the few figures the tools show,
named the way Garmin Connect names them so the tools need no special case,
and lets the file go.
"""

from __future__ import annotations

import io
import logging
from typing import Any

try:  # Only the hosted server has it; a local install never reads FIT files.
    import fitdecode
except ImportError:  # pragma: no cover
    fitdecode = None

log = logging.getLogger(__name__)

# Enough for a long ride with power; a file past this isn't an activity.
MAX_FILE_BYTES = 25 * 1024 * 1024


def available() -> bool:
    return fitdecode is not None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def summary(data: bytes) -> dict[str, Any]:
    """The session's figures from FIT bytes, in Garmin Connect's field names. Empty when none."""
    if fitdecode is None or not data:
        return {}
    out: dict[str, Any] = {}
    zone_tops: list[int] = []
    power_tops: list[int] = []
    beats: list[tuple[float, int]] = []  # (seconds, heart rate) from the records
    try:
        reader = fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.WARN,
                                     error_handling=fitdecode.ErrorHandling.WARN)
        for frame in reader:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "hr_zone" and frame.has_field("high_bpm"):
                top = _number(frame.get_value("high_bpm"))
                if top is not None:
                    zone_tops.append(int(top))
            elif frame.name == "power_zone" and frame.has_field("high_value"):
                top = _number(frame.get_value("high_value"))
                if top is not None:
                    power_tops.append(int(top))
            elif frame.name == "session":
                out.update(_session(frame))
            elif frame.name == "record" and frame.has_field("heart_rate") and frame.has_field("timestamp"):
                hr, at = _number(frame.get_value("heart_rate")), frame.get_value("timestamp")
                # A real file's timestamps come back as datetimes; a device's
                # relative ones as plain seconds. Either spaces the records.
                when = at.timestamp() if hasattr(at, "timestamp") else _number(at)
                if hr and when is not None:
                    beats.append((when, int(hr)))
    except Exception as exc:  # noqa: BLE001 - an odd file loses its extras, not the activity
        log.warning("FIT file not read: %s", type(exc).__name__)
        return out
    # A file whose session doesn't say the time in each zone still has the
    # zones and the heart rate second by second: count it here.
    if not any(k.startswith("hrTimeInZone_") for k in out) and zone_tops and len(beats) > 1:
        out.update(_zones_from_records(beats, zone_tops))
    # Zone n's floor is the top of the zone below it. Six entries start with
    # the top of "below zone 1"; five start with the top of zone 1.
    below = 1 if len(zone_tops) >= 6 else 2
    for n in range(1, 6):
        if f"hrTimeInZone_{n}" in out and 0 <= n - below < len(zone_tops):
            out[f"hrZoneLow_{n}"] = zone_tops[n - below]
    return out


def _zones_from_records(beats: list[tuple[float, int]], tops: list[int]) -> dict[str, float]:
    """Seconds in zones 1 to 5, each record counting until the next, at most ten seconds."""
    bounds = tops[1:6] if len(tops) >= 6 else tops[:5]  # the top of zones 1..5
    seconds = [0.0] * 6
    for (at, hr), (after, _) in zip(beats, beats[1:]):
        dt = min(max(after - at, 0.0), 10.0)
        zone = next((n + 1 for n, top in enumerate(bounds) if hr <= top), 5)
        if len(tops) >= 6 and hr <= tops[0]:
            zone = 0
        seconds[zone] += dt
    return {f"hrTimeInZone_{n}": round(seconds[n], 1) for n in range(1, 6)}


# Session field -> (Connect name, scale to Connect's unit).
_FIELDS = {
    "total_training_effect": ("aerobicTrainingEffect", 1.0),
    "total_anaerobic_training_effect": ("anaerobicTrainingEffect", 1.0),
    "avg_stance_time": ("avgGroundContactTime", 1.0),           # ms
    "avg_stance_time_balance": ("avgGroundContactBalance", 1.0),  # percent
    "avg_vertical_oscillation": ("avgVerticalOscillation", 0.1),  # mm -> cm
    "avg_vertical_ratio": ("avgVerticalRatio", 1.0),             # percent
    "avg_step_length": ("avgStrideLength", 0.1),                 # mm -> cm
    "avg_power": ("avgPower", 1.0),
    "max_power": ("maxPower", 1.0),
    "normalized_power": ("normPower", 1.0),
    "training_stress_score": ("trainingStressScore", 1.0),
    "intensity_factor": ("intensityFactor", 1.0),
    "total_work": ("totalWork", 1.0),
}


def _session(frame: Any) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name, (connect, scale) in _FIELDS.items():
        if frame.has_field(name):
            value = _number(frame.get_value(name))
            if value is not None:
                found[connect] = round(value * scale, 2)
    if frame.has_field("time_in_hr_zone"):
        zones = frame.get_value("time_in_hr_zone")
        if isinstance(zones, (list, tuple)):
            # Entry 0 is time below zone 1, which Connect doesn't list.
            for n, secs in enumerate(zones[1:6], start=1):
                value = _number(secs)
                if value is not None:
                    found[f"hrTimeInZone_{n}"] = round(value, 1)
    return found
