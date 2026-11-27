"""Connections and source priority (build prompt, section 5.12), worked out for the app.

    providers   one card each: a badge (Live / Via Intervals.icu / via the phone's
                health store), the state in words ("Connected to Garmin · Forerunner
                965 · synced 07:02", "Reconnect needed · last synced Tue", "Reads only
                · synced 06:58") and capability chips, each on or off
    priority    the source order the runner set, highest first
    activities  this month's runs from every source, as the merge needs them, so the
                app can re-merge at once when the order changes (domain/dedupe.ts is
                dedupe.py's twin, held to the same answers by fixtures/dedupe.json)
    merges      what dedupe.merge() made of them: kept, hidden, how far apart

The health store's name depends on the phone (Apple Health, Health Connect), so
it's sent as a route and the app words it. TODO(server): GET
/app/api/v1/connections returns view(...); POST .../priority and
.../keep-apart store the runner's changes and answer with a fresh view.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from . import dedupe
from .config import DEFAULT, DomainConfig
from .model import DAYS, Activity, short_date

ROUTES = ("direct", "intervals", "health")


@dataclass(frozen=True)
class Connection:
    key: str                         # garmin, intervals, health
    name: str                        # what the runner wears or uses: "Garmin", "COROS", "Apple Watch"
    route: str                       # direct, intervals, health
    state: str                       # live, reconnect
    activities: bool = True
    sleep: bool = False
    hrv: bool = False
    send: bool = False               # can Daash put workouts on the watch this way
    device: str | None = None
    last_sync: datetime | None = None
    reconnect_url: str | None = None


def _when(t: datetime | None, now: datetime) -> str:
    if t is None:
        return "never"
    if t.date() == now.date():
        return f"{t:%H:%M}"
    if (now.date() - t.date()).days < 7:
        return DAYS[t.weekday()]
    return short_date(t.date())


def _status(c: Connection, now: datetime) -> str:
    if c.state == "reconnect":
        return f"Reconnect needed · last synced {_when(c.last_sync, now)}"
    parts = [f"Connected to {c.name}" if c.route == "direct" else "Reads only" if not c.send else "Connected"]
    if c.device and c.route == "direct":
        parts.append(c.device)
    parts.append(f"synced {_when(c.last_sync, now)}")
    return " · ".join(parts)


def _caps(c: Connection) -> list[dict]:
    return [{"label": "Activities", "on": c.activities},
            {"label": "Sleep and HRV" if c.hrv else "Sleep", "on": c.sleep},
            {"label": "Send workouts", "on": c.send}]


def _reads(c: Connection) -> str:
    words = [w for w, on in (("activities", c.activities), ("sleep", c.sleep), ("HRV", c.hrv)) if on]
    text = ", ".join(words)
    return text[:1].upper() + text[1:] if text else "Nothing yet"


def activity_json(a: Activity) -> dict:
    return {"id": a.id, "provider": a.provider, "start": a.start.isoformat(), "distanceM": a.distance_m,
            "durationS": a.duration_s}


def merges_json(records: Sequence[dedupe.MergeRecord], activities: Sequence[Activity]) -> list[dict]:
    by_id = {a.id: a for a in activities}
    out = []
    for r in records:
        kept, hidden = by_id[r.kept], by_id[r.hidden]
        d = kept.start.date()
        out.append({
            "id": f"{kept.id}|{hidden.id}", "kept": kept.id, "hidden": hidden.id,
            "keptProvider": kept.provider, "hiddenProvider": hidden.provider, "date": d.isoformat(),
            "title": f"{DAYS[d.weekday()]} {d.day} · {kept.distance_m / 1000:.1f} km",
            "gapSeconds": round(abs((kept.start - hidden.start).total_seconds())),
            "distancePct": round(abs(kept.distance_m - hidden.distance_m) / max(kept.distance_m, hidden.distance_m) * 100, 1),
        })
    return sorted(out, key=lambda m: m["date"], reverse=True)


def view(connections: Sequence[Connection], priority: Sequence[str], activities: Iterable[Activity], now: datetime, *,
         kept_apart: Iterable[tuple[str, str]] = (), coming: Sequence[str] = (), cfg: DomainConfig = DEFAULT) -> dict:
    """The Connections and Source priority screens. Every merge in it is dedupe.merge()'s."""
    month = [a for a in activities if a.start >= now - timedelta(days=30)]
    order = [k for k in priority if any(c.key == k for c in connections)]
    order += [c.key for c in connections if c.key not in order]
    apart = [tuple(p) for p in kept_apart]
    _, records = dedupe.merge(month, order, not_same=[frozenset(p) for p in apart], cfg=cfg)
    return {
        "providers": [{
            "key": c.key, "name": c.name, "route": c.route, "state": c.state, "status": _status(c, now),
            "caps": _caps(c), "reads": _reads(c), "reconnectUrl": c.reconnect_url if c.state == "reconnect" else None,
        } for c in connections],
        "priority": order,
        "activities": [activity_json(a) for a in sorted(month, key=lambda a: a.start)],
        "keptApart": [list(p) for p in apart],
        "merges": merges_json(records, month),
        "coming": list(coming),
        "match": {"startWithinSeconds": cfg.merge.start_within_seconds, "distanceWithinPct": cfg.merge.distance_within_pct},
    }
