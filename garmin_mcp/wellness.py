"""What drained or charged Body Battery in a day, and when stress ran high.

Garmin's daily summary gives totals: stress minutes, Body Battery charged
and drained. The questions people ask are "why is my Body Battery so low"
and "when was I stressed", which need the events and the spells behind the
totals. Both come out short: a handful of events, the longest few spells.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .formatting import drop_empty

# Garmin's own bands: 26-50 low, 51-75 medium, 76-100 high.
STRESSED = 51
# A spell is at least this long; readings come every three minutes, and a
# short dip below the line doesn't end one.
SPELL_MINUTES = 30
SPELL_BRIDGE = timedelta(minutes=9)
MAX_SPELLS = 5


def _local(ms: Any, offset: timedelta) -> datetime | None:
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None) + offset


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "")[:19])
    except ValueError:
        return None


def body_battery_events(events: Any) -> list[dict[str, Any]]:
    """Each sleep, nap, activity or other event with what it did to Body Battery."""
    out = []
    for row in events if isinstance(events, list) else []:
        if not isinstance(row, Mapping):
            continue
        event = row.get("event") if isinstance(row.get("event"), Mapping) else row
        start = _parse(event.get("eventStartTimeGmt"))
        offset = event.get("timezoneOffset")
        if start and isinstance(offset, (int, float)):
            start = start + timedelta(milliseconds=offset)
        kind = str(event.get("eventType") or "").lower() or None
        out.append(drop_empty({
            "what": row.get("activityName") or (kind.replace("_", " ") if kind else None),
            "kind": kind,
            "at": start.strftime("%H:%M") if start else None,
            "minutes": round(event["durationInMilliseconds"] / 60000) if isinstance(event.get("durationInMilliseconds"), (int, float)) else None,
            "impact": event.get("bodyBatteryImpact"),
            "garmin_says": str(event.get("shortFeedback") or event.get("feedbackType") or "").replace("_", " ").lower() or None,
        }))
    return out


def stress_spells(payload: Any) -> list[dict[str, Any]]:
    """The longest stretches of medium or high stress, longest first."""
    if not isinstance(payload, Mapping):
        return []
    local, gmt = _parse(payload.get("startTimestampLocal")), _parse(payload.get("startTimestampGMT"))
    offset = (local - gmt) if local and gmt else timedelta(0)
    points = []
    for pair in payload.get("stressValuesArray") or []:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            at = _local(pair[0], offset)
            if at is not None and isinstance(pair[1], (int, float)):
                points.append((at, pair[1]))
    points.sort()
    spells, current = [], []
    for at, level in points:
        if level >= STRESSED:
            if current and at - current[-1][0] > SPELL_BRIDGE:
                spells.append(current)
                current = []
            current.append((at, level))
    if current:
        spells.append(current)
    out = []
    for spell in spells:
        length = spell[-1][0] - spell[0][0] + timedelta(minutes=3)
        if length < timedelta(minutes=SPELL_MINUTES):
            continue
        levels = [lv for _, lv in spell]
        out.append({
            "from": spell[0][0].strftime("%H:%M"),
            "to": (spell[-1][0] + timedelta(minutes=3)).strftime("%H:%M"),
            "minutes": round(length.total_seconds() / 60),
            "average": round(sum(levels) / len(levels)),
            "peak": max(levels),
        })
    out.sort(key=lambda s: -s["minutes"])
    return out[:MAX_SPELLS]
