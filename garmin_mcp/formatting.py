"""Turn Garmin's raw JSON into small, readable payloads.

Garmin endpoints return a lot of fields we do not care about, with wildly
inconsistent names and units. Everything the tools hand back goes through here
so a response stays small enough to reason about.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping


class DateError(ValueError):
    """Bad date input from a tool caller; the message is shown to the user."""


RELATIVE_DAYS = {"today": 0, "yesterday": -1, "tomorrow": 1}


def parse_date(value: str | None, *, default_today: bool = True) -> str:
    """Accept 'YYYY-MM-DD', a relative day name, or a signed day offset.

    Future dates are allowed: reads just come back empty, and scheduling a
    workout needs them.
    """
    if value is None or value == "":
        if default_today:
            return date.today().isoformat()
        raise DateError("A date is required.")

    text = str(value).strip().lower()
    if text in RELATIVE_DAYS:
        return (date.today() + timedelta(days=RELATIVE_DAYS[text])).isoformat()
    if text[:1] in "+-" and text[1:].isdigit():
        return (date.today() + timedelta(days=int(text))).isoformat()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise DateError(
            f"Could not read {value!r} as a date. Use YYYY-MM-DD, 'today', "
            "'yesterday', 'tomorrow', or an offset like '-7' or '+3'."
        ) from exc


def duration(seconds: float | None) -> str | None:
    """1h 24m 03s / 24m 03s / 43s."""
    if seconds is None:
        return None
    total = int(round(float(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def pace_per_km(distance_m: float | None, seconds: float | None) -> str | None:
    if not distance_m or not seconds or distance_m <= 0:
        return None
    secs_per_km = float(seconds) / (float(distance_m) / 1000.0)
    minutes, secs = divmod(int(round(secs_per_km)), 60)
    return f"{minutes}:{secs:02d} /km"


def pace_per_mile(distance_m: float | None, seconds: float | None) -> str | None:
    if not distance_m or not seconds or distance_m <= 0:
        return None
    secs_per_mile = float(seconds) / (float(distance_m) / 1609.344)
    minutes, secs = divmod(int(round(secs_per_mile)), 60)
    return f"{minutes}:{secs:02d} /mi"


def km(metres: float | None, places: int = 2) -> float | None:
    return None if metres is None else round(float(metres) / 1000.0, places)


def rounded(value: Any, places: int = 1) -> Any:
    return round(float(value), places) if isinstance(value, (int, float)) else value


def minutes(seconds: float | None) -> float | None:
    return None if seconds is None else round(float(seconds) / 60.0, 1)


def drop_empty(data: Mapping[str, Any]) -> dict[str, Any]:
    """Strip keys whose value is None so responses stay compact.

    Nested dicts are kept only when something survived inside them.
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, Mapping):
            nested = drop_empty(value)
            if nested:
                out[key] = nested
            continue
        out[key] = value
    return out


def first_present(data: Mapping[str, Any], *keys: str) -> Any:
    """Garmin renames fields between endpoints; take whichever one showed up."""
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def local_timestamp(value: Any) -> str | None:
    """Garmin mixes epoch-millis and ISO strings for the same concept."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0).isoformat(
                timespec="seconds"
            )
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def hr_zones(raw: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Normalise /activity/{id}/hrTimeInZones into a compact list."""
    if not raw:
        return None
    zones: list[dict[str, Any]] = []
    total = sum(float(z.get("secsInZone") or 0) for z in raw) or 0.0
    for zone in sorted(raw, key=lambda z: z.get("zoneNumber") or 0):
        secs = float(zone.get("secsInZone") or 0)
        zones.append(
            drop_empty(
                {
                    "zone": zone.get("zoneNumber"),
                    "time": duration(secs),
                    "seconds": round(secs),
                    "percent": round(secs / total * 100, 1) if total else None,
                    "low_bpm": rounded(zone.get("zoneLowBoundary"), 0),
                }
            )
        )
    return zones or None
