"""The weather a run was done in, as Garmin recorded it from the nearest station.

Garmin's weather endpoint gives temperatures in Fahrenheit whatever the
account's units, with nothing in the payload to say so, and wind in the
account's own units (mph for statute accounts, km/h for metric). Both are
put into Celsius and km/h here, with Fahrenheit and mph alongside for
accounts that read them.
"""

from __future__ import annotations

from typing import Any, Mapping

from .formatting import drop_empty, rounded

MPH_TO_KMH = 1.609344


def _celsius(f: Any) -> float | None:
    return round((float(f) - 32) * 5 / 9, 1) if isinstance(f, (int, float)) else None


def effect(temp_c: float | None, dew_c: float | None, wind_kmh: float | None) -> str | None:
    """What the conditions would do to a run, in a line, when they'd do anything."""
    notes = []
    if dew_c is not None and dew_c >= 18:
        notes.append("very humid: expect heart rate well up or pace clearly down for the same effort")
    elif (dew_c is not None and dew_c >= 15) or (temp_c is not None and temp_c >= 22):
        notes.append("warm or humid enough to raise heart rate or slow the pace a little")
    elif temp_c is not None and temp_c <= 0:
        notes.append("below freezing: slower warm-up, and ice may have slowed it")
    if wind_kmh is not None and wind_kmh >= 25:
        notes.append("windy enough to cost time into the wind")
    return "; ".join(notes).capitalize() if notes else None


def shape_weather(raw: Any, unit_system: str | None) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or raw.get("temp") is None:
        return None
    statute = (unit_system or "").lower().startswith("statute")
    us = (unit_system or "").lower() == "statute_us"

    temp_c, feels_c, dew_c = _celsius(raw.get("temp")), _celsius(raw.get("apparentTemp")), _celsius(raw.get("dewPoint"))
    wind = raw.get("windSpeed")
    gust = raw.get("windGust")
    to_kmh = MPH_TO_KMH if statute else 1.0
    wind_kmh = round(float(wind) * to_kmh) if isinstance(wind, (int, float)) else None
    gust_kmh = round(float(gust) * to_kmh) if isinstance(gust, (int, float)) else None

    return drop_empty({
        "conditions": ((raw.get("weatherTypeDTO") or {}).get("desc")),
        "temperature_c": temp_c,
        "feels_like_c": feels_c,
        "dew_point_c": dew_c,
        "temperature_f": rounded(raw.get("temp"), 0) if us else None,
        "feels_like_f": rounded(raw.get("apparentTemp"), 0) if us else None,
        "humidity_pct": rounded(raw.get("relativeHumidity"), 0),
        "wind_kmh": wind_kmh,
        "wind_mph": rounded(wind, 0) if statute else None,
        "gusts_kmh": gust_kmh,
        "wind_from": (raw.get("windDirectionCompassPoint") or "").upper() or None,
        "station": ((raw.get("weatherStationDTO") or {}).get("name")),
        "effect": effect(temp_c, dew_c, wind_kmh),
    })
