"""Shoes: how far each pair has gone, and how far it has left."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from .formatting import drop_empty, km

# Most running shoes are worn out somewhere between 500 and 800 km. Used when
# the user hasn't set a limit of their own in Garmin.
TYPICAL_LIFE_KM = 650
NEARLY_DONE = 0.85
RECENT_DAYS = 28


def is_shoe(item: Mapping[str, Any]) -> bool:
    return "shoe" in str(item.get("gearTypeName") or "").lower()


# What Garmin fills the make and model with when nobody chose one.
_NO_NAME = {"other", "unknown", "unknown shoes", "unknown shoe"}


def model_of(item: Mapping[str, Any]) -> str | None:
    parts = [str(x) for x in (item.get("gearMakeName"), item.get("gearModelName"))
             if x and str(x).strip().lower() not in _NO_NAME]
    return " ".join(parts) or None


def name_of(item: Mapping[str, Any]) -> str | None:
    custom = item.get("displayName") or item.get("customMakeModel")
    return str(custom) if custom else model_of(item)


def _recent_km(activities: Iterable[Mapping[str, Any]] | None, today: date) -> tuple[float | None, str | None]:
    if activities is None:
        return None, None
    cutoff = (today - timedelta(days=RECENT_DAYS)).isoformat()
    total, last = 0.0, None
    for a in activities:
        day = str(a.get("startTimeLocal") or "")[:10]
        if not day:
            continue
        last = max(last or day, day)
        if day >= cutoff and isinstance(a.get("distance"), (int, float)):
            total += a["distance"]
    return km(total, 1), last


def shape_shoe(item: Mapping[str, Any], stats: Mapping[str, Any] | None,
               activities: Iterable[Mapping[str, Any]] | None, default_for_running: bool,
               today: date) -> dict[str, Any]:
    used = km((stats or {}).get("totalDistance"), 0)
    limit_m = item.get("maximumMeters")
    limit = km(limit_m, 0) if isinstance(limit_m, (int, float)) and limit_m > 0 else None
    life = limit or TYPICAL_LIFE_KM
    recent, last = _recent_km(activities, today)
    worn = used / life if used is not None else None
    return drop_empty({
        "name": name_of(item),
        "model": model_of(item),
        "km": used,
        "runs": (stats or {}).get("totalActivities"),
        "limit_km": limit,
        "km_left": max(0, round(life - used)) if used is not None else None,
        "km_over_limit": round(used - life) if used is not None and used > life else None,
        "limit_is": None if limit else f"typical ({TYPICAL_LIFE_KM} km), none set in Garmin",
        "km_last_4_weeks": recent,
        "last_used": last,
        "since": str(item.get("dateBegin") or "")[:10] or None,
        "default_for_running": default_for_running or None,
        "nearly_done": (worn is not None and worn >= NEARLY_DONE) or None,
    })


def running_default(defaults: Any) -> set[str]:
    """The uuids Garmin adds to new runs automatically."""
    if isinstance(defaults, dict):
        rows = defaults.get("gearActivityTypes") or []
    else:
        rows = defaults if isinstance(defaults, list) else []
    out = set()
    for row in rows:
        if isinstance(row, Mapping) and row.get("activityTypePk") == 1 and row.get("defaultGear", True):
            if row.get("uuid"):
                out.add(str(row["uuid"]))
    return out
