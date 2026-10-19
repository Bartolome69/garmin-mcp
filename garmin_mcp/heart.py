"""Heart rate through a day or a night, from Garmin's all-day readings.

Garmin keeps a reading every two minutes or so while the watch is worn. A day
is about 700 of them: too many to hand to the model, and not what anyone asks
about. What they ask is whether the watch caught the night, when the heart
rate settled, how low it went. So the readings in the window asked about are
turned into gaps, blocks of a few minutes with their low, average and high,
and the lowest and highest readings with their times. Nothing is kept.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .formatting import DateError

# No reading for longer than this is a gap: the watch was off, loose or flat.
GAP_MINUTES = 10
# Blocks are the shortest of these that keeps a window to MAX_BLOCKS of them:
# a night comes in 10-minute blocks, a whole day in hourly ones.
BLOCK_SIZES = (10, 15, 20, 30, 60, 120)
MAX_BLOCKS = 36


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "")[:19])
    except ValueError:
        return None


def _offset(payload: Mapping[str, Any]) -> timedelta:
    """Local time minus GMT, from the two timestamps Garmin gives each day."""
    local, gmt = _parse(payload.get("startTimestampLocal")), _parse(payload.get("startTimestampGMT"))
    return (local - gmt) if local and gmt else timedelta(0)


def readings(payloads: Iterable[Mapping[str, Any] | None]) -> list[tuple[datetime, int | None]]:
    """Every reading across the days given, in local time, each once, oldest first.

    A reading Garmin marks empty (the watch was off the wrist) stays in, as
    None, so it can count towards a gap.
    """
    seen: dict[datetime, int | None] = {}
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        shift = _offset(payload)
        for pair in payload.get("heartRateValues") or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2 or not isinstance(pair[0], (int, float)):
                continue
            at = datetime.fromtimestamp(pair[0] / 1000, tz=timezone.utc).replace(tzinfo=None) + shift
            bpm = pair[1] if isinstance(pair[1], (int, float)) and pair[1] > 0 else None
            if at not in seen or seen[at] is None:
                seen[at] = int(bpm) if bpm is not None else None
    return sorted(seen.items())


def _clock(at: datetime, first_day: date) -> str:
    return at.strftime("%H:%M") if at.date() == first_day else at.strftime("%a %H:%M")


def _length(delta: timedelta) -> str:
    mins = int(delta.total_seconds() // 60)
    return f"{mins // 60}h{mins % 60:02d}m" if mins >= 60 else f"{mins}m"


def summarise(
    points: list[tuple[datetime, int | None]], start: datetime, end: datetime, now: datetime | None = None,
) -> dict[str, Any]:
    """Gaps, blocks and the low and high in the window from start to end (local times)."""
    first_day = start.date()
    if now is not None and end > now:
        end = max(start, now)
    window = [(at, bpm) for at, bpm in points if start <= at < end]
    worn = [(at, bpm) for at, bpm in window if bpm is not None]
    span = end - start
    out: dict[str, Any] = {
        "from": _clock(start, first_day),
        "to": _clock(end, first_day),
        "readings": len(worn),
    }
    if not worn:
        out["gaps"] = [{"from": out["from"], "to": out["to"], "length": _length(span)}] if span > timedelta(0) else []
        out["note"] = "No heart rate readings in this window: the watch wasn't worn, or hasn't synced yet."
        return out

    gap = timedelta(minutes=GAP_MINUTES)
    gaps = []
    edges = [start] + [at for at, _ in worn] + [end]
    for before, after in zip(edges, edges[1:]):
        if after - before > gap:
            gaps.append({"from": _clock(before, first_day), "to": _clock(after, first_day), "length": _length(after - before)})
    missing = sum((after - before for before, after in zip(edges, edges[1:]) if after - before > gap), timedelta(0))

    span_minutes = span.total_seconds() / 60
    block_minutes = next((m for m in BLOCK_SIZES if span_minutes / m <= MAX_BLOCKS), BLOCK_SIZES[-1])
    step = timedelta(minutes=block_minutes)
    blocks = []
    at = start
    while at < end:
        here = [bpm for t, bpm in worn if at <= t < at + step]
        blocks.append(
            {"from": _clock(at, first_day), "low": min(here), "avg": round(sum(here) / len(here)), "high": max(here)}
            if here else {"from": _clock(at, first_day), "none": True}
        )
        at += step

    low = min(worn, key=lambda p: (p[1], p[0]))
    high = max(worn, key=lambda p: (p[1], -p[0].timestamp()))
    out.update({
        "covered_percent": round(100 * (1 - missing / span)) if span else None,
        "gaps": gaps,
        "lowest": {"bpm": low[1], "at": _clock(low[0], first_day)},
        "highest": {"bpm": high[1], "at": _clock(high[0], first_day)},
        "average_bpm": round(sum(b for _, b in worn) / len(worn)),
        "block_minutes": block_minutes,
        "blocks": blocks,
    })
    return out


def window(day: date, start: str | None, end: str | None) -> tuple[datetime, datetime]:
    """The window asked for: HH:MM to HH:MM on the day, into the next day if end comes before start."""
    def at(text: str | None, default: datetime) -> datetime:
        if not text:
            return default
        clean = str(text).strip().lower().replace(".", ":")
        for fmt in ("%H:%M", "%I:%M%p", "%I%p", "%H"):
            try:
                t = datetime.strptime(clean.replace(" ", ""), fmt)
                return datetime.combine(day, t.time())
            except ValueError:
                continue
        raise DateError(f"Times are HH:MM, such as 22:20; not {text!r}.")

    begin = at(start, datetime.combine(day, datetime.min.time()))
    finish = at(end, datetime.combine(day + timedelta(days=1), datetime.min.time()))
    if finish <= begin:
        finish += timedelta(days=1)
    return begin, finish
