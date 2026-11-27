"""Post-run (build prompt, section 5.9): a recorded run, worked out for the app to draw.

Everything a runner reads after a run comes from here, so it is the same with the
Coach on or off and the app never recomputes it:

    planned vs actual   distance, rep pace (or pace) and reps, each marked on plan or not
    series              pace, HR, cadence and elevation by distance, one row per point,
                        the same points for every metric, so one scrub line reads them all
    laps                warm-up, each rep, recoveries, cool-down, by distance
    charts              each metric's axis ticks, a text summary for screen readers, and
                        one rule-based takeaway (takeaways.py): an observation, never a cause
    zones               minutes in each HR zone
    route               the GPS track, scaled to fit a box, for the map

The input is provider-neutral (Run): the server maps Garmin's (or another
provider's) activity details and streams onto it. TODO(server): GET
/app/api/v1/runs/{id} returns view(...) for a recorded run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from . import takeaways
from .config import DEFAULT, DomainConfig
from .model import DAYS, mmss, round_half_up, short_date

POINTS = 160                 # series points: enough for a phone-width chart, small enough to cache
ROUTE_POINTS = 120
SLOWEST_PACE = 12 * 60       # s/km; standing still reads as this, not infinity
TYPE_LABELS = {"easy": "Easy", "recovery": "Recovery", "tempo": "Tempo", "intervals": "Intervals", "long": "Long",
               "race": "Race", "other": "Run"}


@dataclass(frozen=True)
class Sample:
    t: float                         # seconds from the start
    d: float                         # metres from the start
    hr: float | None = None
    cadence: float | None = None     # steps per minute
    elevation: float | None = None   # metres
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True)
class Lap:
    kind: str                        # warmup, work, recovery, cooldown, run, strides
    start_d: float
    end_d: float


@dataclass(frozen=True)
class Planned:
    distance_km: float | None = None
    pace: tuple[int, int] | None = None   # s/km, fast to slow: the reps' target, or the run's
    reps: int | None = None
    rep_words: str | None = None          # "6 × 800 m"


@dataclass(frozen=True)
class Run:
    id: str
    start: datetime                  # local time
    type: str
    title: str
    samples: Sequence[Sample]
    laps: Sequence[Lap] = ()
    zone_seconds: Sequence[float] = ()   # Z1..Z5
    planned: Planned | None = None
    session_id: str | None = None        # the planned session it was, when there was one


@dataclass(frozen=True)
class _Lap:
    kind: str
    start_d: float
    end_d: float
    seconds: float
    hr: float | None
    label: str = ""


def _at(samples: Sequence[Sample], d: float) -> float:
    """Seconds at distance d, interpolated."""
    lo, hi = 0, len(samples) - 1
    if d <= samples[0].d:   # before the first sample: from the start, at zero
        return samples[0].t * (d / samples[0].d) if samples[0].d > 0 else samples[0].t
    if d >= samples[-1].d:
        return samples[-1].t
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if samples[mid].d < d:
            lo = mid
        else:
            hi = mid
    a, b = samples[lo], samples[hi]
    return a.t + (b.t - a.t) * ((d - a.d) / (b.d - a.d) if b.d > a.d else 0)


def _mean(values: Sequence[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return sum(vs) / len(vs) if vs else None


def _laps(run: Run) -> list[_Lap]:
    out = []
    rep = stride = 0
    for lap in run.laps:
        inside = [s for s in run.samples if lap.start_d <= s.d <= lap.end_d]
        seconds = _at(run.samples, lap.end_d) - _at(run.samples, lap.start_d)
        rep += lap.kind == "work"
        stride += lap.kind == "strides"
        label = {"warmup": "Warm-up", "cooldown": "Cool-down", "recovery": "Recovery", "work": f"Rep {rep}",
                 "strides": f"Stride {stride}"}.get(lap.kind, "Run")
        out.append(_Lap(lap.kind, lap.start_d, lap.end_d, seconds, _mean([s.hr for s in inside]), label))
    return out


def _series(run: Run) -> dict[str, list]:
    """POINTS rows by distance: each a bucket's pace (from its own time and distance) and mean HR, cadence, elevation."""
    total = run.samples[-1].d
    edges = [total * i / POINTS for i in range(POINTS + 1)]
    km, pace, hr, cad, ele = [], [], [], [], []
    for a, b in zip(edges, edges[1:]):
        inside = [s for s in run.samples if a <= s.d <= b] or [min(run.samples, key=lambda s: abs(s.d - (a + b) / 2))]
        secs = _at(run.samples, b) - _at(run.samples, a)
        km.append(round((a + b) / 2000, 3))
        pace.append(min(SLOWEST_PACE, round_half_up(secs / (b - a) * 1000)) if b > a else SLOWEST_PACE)
        for out, key in ((hr, "hr"), (cad, "cadence"), (ele, "elevation")):
            m = _mean([getattr(s, key) for s in inside])
            out.append(None if m is None else round(m, 1) if key == "elevation" else round_half_up(m))
    return {"km": km, "pace": pace, "hr": hr, "cadence": cad, "elevation": ele}


def _ticks(lo: float, hi: float, step: float, words) -> list[dict]:
    """Two gridlines inside the data's range, on round values: a finer step when the range is narrow."""
    for s in (step, step / 2, step / 5):
        first, last = math.ceil(lo / s) * s, math.floor(hi / s) * s
        if last > first:
            return [{"value": first, "label": words(first)}, {"value": last, "label": words(last)}]
    return [{"value": round((lo + hi) / 2), "label": words(round((lo + hi) / 2))}]


def _domain(values: Sequence[float | None], pad: float, step: float, words) -> dict | None:
    vs = [v for v in values if v is not None]
    if not vs:
        return None
    lo, hi = min(vs), max(vs)
    return {"min": lo - pad, "max": hi + pad, "ticks": _ticks(lo, hi, step, words)}


def _takeaway(text: str | None) -> dict | None:
    """Split for drawing: the first sentence bold. tone 'good' for an even or steady finding, else 'note'."""
    if not text:
        return None
    first, _, rest = text.partition(". ")
    bold = first if first.endswith(".") or not rest else first + "."
    good = text.startswith(("Even", "Negative split", "HR steady", "Cadence steady", "Flat"))
    return {"bold": bold, "rest": rest.strip(), "text": text, "tone": "good" if good else "note"}


def _km_seconds(run: Run, upto: float | None = None) -> list[float]:
    full = int((upto if upto is not None else run.samples[-1].d) // 1000)
    return [_at(run.samples, (k + 1) * 1000) - _at(run.samples, k * 1000) for k in range(full)]


def _per_km(run: Run, key: str) -> list[float]:
    full = int(run.samples[-1].d // 1000)
    out = []
    for k in range(full):
        m = _mean([getattr(s, key) for s in run.samples if k * 1000 <= s.d < (k + 1) * 1000])
        if m is not None:
            out.append(m)
    return out


def _climb(run: Run) -> tuple[float, float]:
    ele = [s.elevation for s in run.samples if s.elevation is not None]
    up = sum(max(0.0, b - a) for a, b in zip(ele, ele[1:]))
    down = sum(max(0.0, a - b) for a, b in zip(ele, ele[1:]))
    return up, down


def _route(run: Run) -> dict | None:
    pts = [(s.lat, s.lon) for s in run.samples if s.lat is not None and s.lon is not None]
    if len(pts) < 2:
        return None
    step = max(1, math.ceil(len(pts) / ROUTE_POINTS))
    pts = pts[::step] + ([pts[-1]] if (len(pts) - 1) % step else [])
    mean_lat = math.radians(sum(p[0] for p in pts) / len(pts))
    xs = [p[1] * math.cos(mean_lat) for p in pts]
    ys = [-p[0] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    size = max(w, h) or 1
    return {"aspect": round((w or size) / (h or size), 3),
            "points": [[round((x - min(xs)) / size, 4), round((y - min(ys)) / size, 4)] for x, y in zip(xs, ys)]}


def _pace_words(seconds: float) -> str:
    return mmss(seconds)


def view(run: Run, cfg: DomainConfig = DEFAULT) -> dict:
    """The post-run screen's view model. Every number in it is worked out here."""
    laps = _laps(run)
    reps = [lap for lap in laps if lap.kind == "work"]
    total_m = run.samples[-1].d
    total_km = round(total_m / 1000, 1)
    series = _series(run)

    # Planned vs actual: each row on plan or not.
    rows = []
    p = run.planned
    rep_paces = [lap.seconds / (lap.end_d - lap.start_d) * 1000 for lap in reps]
    # A run's own pace leaves out strides: the easy running is what the plan set a range for.
    running = [lap for lap in laps if lap.kind == "run"]
    run_pace = (sum(lap.seconds for lap in running) / sum(lap.end_d - lap.start_d for lap in running) * 1000 if running
                else run.samples[-1].t / total_m * 1000)
    if p:
        if p.distance_km is not None:
            rows.append({"label": "Distance", "plan": f"{p.distance_km:.1f} km", "done": f"{total_km:.1f} km",
                         "ok": abs(total_km - p.distance_km) <= p.distance_km * cfg.postrun.distance_ok_pct / 100})
        if p.pace:
            done = sum(rep_paces) / len(rep_paces) if reps else run_pace
            tol = cfg.postrun.pace_ok_seconds
            rows.append({"label": "Rep pace" if reps else "Pace", "plan": f"{mmss(p.pace[0])}–{mmss(p.pace[1])}",
                         "done": f"{mmss(done)} avg", "ok": p.pace[0] - tol <= done <= p.pace[1] + tol})
        if p.reps:
            rows.append({"label": "Reps", "plan": p.rep_words or f"{p.reps} reps", "done": f"{len(reps)} of {p.reps}",
                         "ok": len(reps) >= p.reps})

    # One takeaway under each chart.
    if reps:
        pace_take = takeaways.reps(rep_paces, cfg)
        hr_means = [lap.hr for lap in reps if lap.hr is not None]
        hr_take = takeaways.rep_hr(hr_means, cfg) if len(hr_means) == len(reps) else None
    else:
        upto = max(lap.end_d for lap in running) if running else None   # strides and their walks aren't splits
        pace_take = takeaways.splits(_km_seconds(run, upto), cfg)
        km_pace = _km_seconds(run, upto)
        km_hr = _per_km(run, "hr")[:len(km_pace)]
        hr_take = takeaways.hr_drift(km_pace, km_hr, cfg) if len(km_hr) == len(km_pace) else None
    cad = _per_km(run, "cadence")
    cad_take = takeaways.cadence(cad, cfg) if cad else None
    up, down = _climb(run)
    ele_take = takeaways.elevation(up, down, cfg) if any(s.elevation is not None for s in run.samples) else None

    avg_pace = sum(rep_paces) / len(rep_paces) if reps else run_pace
    hr_vals = [v for v in series["hr"] if v is not None]
    cad_avg = _mean(cad)
    pace_summary = (f"Pace by distance, {len(reps)} reps around {mmss(avg_pace)} per km" if reps
                    else f"Pace by distance, around {mmss(avg_pace)} per km")
    charts = {
        "pace": {"label": "Pace", "unit": "/km", "inverted": True, "domain": _domain(series["pace"], 10, 30, _pace_words),
                 "summary": pace_summary, "takeaway": _takeaway(pace_take)},
        "hr": {"label": "Heart rate", "unit": "bpm", "inverted": False, "domain": _domain(series["hr"], 4, 10, lambda v: f"{v:.0f}"),
               "summary": f"Heart rate by distance, from {min(hr_vals)} to {max(hr_vals)} bpm" if hr_vals else "No heart rate recorded",
               "takeaway": _takeaway(hr_take)},
        "cadence": {"label": "Cadence", "unit": "spm", "inverted": False, "domain": _domain(series["cadence"], 3, 10, lambda v: f"{v:.0f}"),
                    "summary": f"Cadence by distance, around {cad_avg:.0f} spm" if cad_avg else "No cadence recorded",
                    "takeaway": _takeaway(cad_take)},
        "elevation": {"label": "Elevation", "unit": "m", "inverted": False,
                      "domain": _domain(series["elevation"], 3, 10, lambda v: f"{v:.0f}"),
                      "summary": f"Elevation by distance, {up:.0f} m up and {down:.0f} m down" if ele_take else "No elevation recorded",
                      "takeaway": _takeaway(ele_take)},
    }

    zone_minutes = [round_half_up(s / 60) for s in run.zone_seconds]
    top = max(zone_minutes) if zone_minutes and max(zone_minutes) else 1
    zones = [{"label": f"Z{i + 1}", "minutes": m, "share": round(m / top, 3)} for i, m in enumerate(zone_minutes)]

    d = run.start.date()
    return {
        "id": run.id,
        "sessionId": run.session_id,
        "date": d.isoformat(),
        "when": f"{DAYS[d.weekday()]} {short_date(d)} · {run.start:%H:%M}",
        "type": run.type,
        "typeLabel": TYPE_LABELS.get(run.type, "Run"),
        "title": run.title,
        "totalKm": total_km,
        "duration": mmss(run.samples[-1].t),
        "route": _route(run),
        "planned": rows or None,
        "series": series,
        "laps": [{"kind": lap.kind, "label": lap.label, "fromKm": round(lap.start_d / 1000, 3), "toKm": round(lap.end_d / 1000, 3)}
                 for lap in laps],
        "charts": charts,
        "zones": zones or None,
        # The scrub line starts halfway: in the middle of the rep nearest halfway, when there are reps.
        "scrubKm": round(min(((lap.start_d + lap.end_d) / 2000 for lap in reps), key=lambda k: abs(k - total_km / 2))
                         if reps else total_km / 2, 2),
    }
