"""The ground a run covered: its hills, what its pace was worth on the flat, and where it faded.

Built from the same recording stream.py reads, cut into short stretches of
road. Elevation from a watch is noisy second to second, so nothing here looks
at a single sample: grades come from stretches of SEGMENT_M, climbs from the
smoothed profile.

No coordinates leave this module. The route's shape is described in words and
distances from the start, so nothing in the answer says where someone lives.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any, Mapping, Sequence

from .formatting import drop_empty, km, pace_per_km, rounded

SEGMENT_M = 100.0
SMOOTH_SAMPLES = 5

# Slower than this between samples is standing still: a stop at lights or a
# run club regroup with the watch still running. Left out of every pace here.
MOVING_SPEED = 0.8

# A grade inside this band, either way, counts as flat.
FLAT_GRADE = 2.0

# What counts as a climb worth naming: enough rise, steep enough on average,
# and over once the road has dropped this far below the top.
CLIMB_MIN_GAIN = 10.0
CLIMB_MIN_GRADE = 3.0
CLIMB_END_DROP = 5.0
CLIMB_EDGE_GRADE = 1.5
MAX_CLIMBS = 5

# Decoupling and fade only mean something on a run long and steady enough.
STEADY_MIN_SECONDS = 1800
STEADY_MAX_VARIATION = 0.08
FADE_PCT = 5.0

LOOP_METRES = 300.0
SAME_ROUTE_START_M = 300.0
SAME_ROUTE_DISTANCE = 0.08
SAME_ROUTE_SHOWN = 6


def _smooth(values: Sequence[float | None], width: int = SMOOTH_SAMPLES) -> list[float | None]:
    half = width // 2
    out: list[float | None] = []
    for i in range(len(values)):
        window = [v for v in values[max(0, i - half) : i + half + 1] if v is not None]
        out.append(sum(window) / len(window) if window else None)
    return out


def gap_factor(grade_pct: float) -> float:
    """How much faster the same effort would be on the flat, at this grade.

    An estimate for when Garmin gives no grade-adjusted speed: uphill costs
    about 3% per 1% of grade; downhill helps up to about -10%, then the braking
    costs it back.
    """
    g = max(-25.0, min(25.0, grade_pct))
    if g >= 0:
        return 1.0 + 0.03 * g
    return 1.0 + 0.018 * g + 0.0009 * g * g


def _metres_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371000.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _points(columns: Mapping[str, Sequence[float | None]]) -> list[dict[str, Any]]:
    """The run every SEGMENT_M: distance, moving time, smoothed elevation, and what happened in between."""
    distance = columns.get("sumDistance")
    seconds = columns.get("sumDuration") or columns.get("sumElapsedDuration")
    elevation = columns.get("directElevation")
    if not distance or not seconds or not elevation:
        return []
    elevation = _smooth(elevation)
    hr = columns.get("directHeartRate") or []
    gap = columns.get("directGradeAdjustedSpeed") or []

    points: list[dict[str, Any]] = []
    moving = hr_sum = hr_time = gap_dist = 0.0
    prev_t = prev_d = None
    for i, (d, t, e) in enumerate(zip(distance, seconds, elevation)):
        if d is None or t is None or e is None:
            continue
        if prev_t is not None:
            dt = max(0.0, t - prev_t)
            if dt and (d - prev_d) / dt >= MOVING_SPEED:
                moving += dt
                h = hr[i] if i < len(hr) else None
                if h:
                    hr_sum += h * dt
                    hr_time += dt
                g = gap[i] if i < len(gap) else None
                if g is not None:
                    gap_dist += g * dt
        prev_t, prev_d = t, d
        if not points or d - points[-1]["d"] >= SEGMENT_M:
            points.append({"d": d, "t": moving, "e": e, "hr_sum": hr_sum, "hr_time": hr_time, "gap_dist": gap_dist})
    return points


def _segments(points: list[dict[str, Any]], garmin_gap: bool) -> list[dict[str, Any]]:
    out = []
    for a, b in zip(points, points[1:]):
        dist, secs = b["d"] - a["d"], b["t"] - a["t"]
        if dist <= 0 or secs <= 0:
            continue
        grade = (b["e"] - a["e"]) / dist * 100.0
        hr_time = b["hr_time"] - a["hr_time"]
        flat_dist = b["gap_dist"] - a["gap_dist"] if garmin_gap else dist * gap_factor(grade)
        out.append({
            "d0": a["d"], "d1": b["d"], "dist": dist, "secs": secs, "grade": grade,
            "rise": b["e"] - a["e"], "hr_sum": b["hr_sum"] - a["hr_sum"], "hr_time": hr_time,
            "flat_dist": flat_dist if flat_dist > 0 else dist,
        })
    return out


def _sum(segments: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    total = {k: sum(s[k] for s in segments) for k in ("dist", "secs", "hr_sum", "hr_time", "flat_dist")}
    total["hr"] = total["hr_sum"] / total["hr_time"] if total["hr_time"] else None
    return total


def _bands(segments: list[dict[str, Any]]) -> dict[str, Any] | None:
    groups = {"uphill": [], "flat": [], "downhill": []}
    for s in segments:
        name = "uphill" if s["grade"] >= FLAT_GRADE else "downhill" if s["grade"] <= -FLAT_GRADE else "flat"
        groups[name].append(s)
    out = {}
    for name, group in groups.items():
        if not group:
            continue
        t = _sum(group)
        out[name] = drop_empty({
            "km": km(t["dist"]),
            "pace_per_km": pace_per_km(t["dist"], t["secs"]),
            "flat_equivalent_pace": pace_per_km(t["flat_dist"], t["secs"]) if name != "flat" else None,
            "avg_hr": rounded(t["hr"], 0),
        })
    return out if len(out) > 1 else None


def _climbs(points: list[dict[str, Any]], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[tuple[int, int]] = []
    low = top = 0
    for j in range(1, len(points)):
        e = points[j]["e"]
        if e > points[top]["e"]:
            top = j
        if points[top]["e"] - e >= CLIMB_END_DROP:
            found.append((low, top))
            low = top = j
        elif e < points[low]["e"]:
            low = top = j
    found.append((low, top))
    climbs = []
    for a, b in found:
        # Trim the flat road either side of the hill, so its grade is the hill's.
        while a < b - 1 and segments[a]["grade"] < CLIMB_EDGE_GRADE:
            a += 1
        while b > a + 1 and segments[b - 1]["grade"] < CLIMB_EDGE_GRADE:
            b -= 1
        if b <= a:
            continue
        gain = points[b]["e"] - points[a]["e"]
        length = points[b]["d"] - points[a]["d"]
        if gain < CLIMB_MIN_GAIN or length <= 0 or gain / length * 100 < CLIMB_MIN_GRADE:
            continue
        t = _sum(segments[a:b])
        climbs.append({
            "gain": gain,
            "summary": drop_empty({
                "starts_at_km": km(points[a]["d"], 1),
                "length_m": round(length),
                "gain_m": round(gain),
                "avg_grade_pct": round(gain / length * 100, 1),
                "time_s": round(t["secs"]),
                "pace_per_km": pace_per_km(t["dist"], t["secs"]),
                "flat_equivalent_pace": pace_per_km(t["flat_dist"], t["secs"]),
                "avg_hr": rounded(t["hr"], 0),
            }),
        })
    biggest = sorted(climbs, key=lambda c: -c["gain"])[:MAX_CLIMBS]
    return [c["summary"] for c in sorted(biggest, key=lambda c: c["summary"].get("starts_at_km") or 0)]


def _by_km(segments: list[dict[str, Any]]) -> list[dict[str, float]]:
    kms: dict[int, list[dict[str, Any]]] = {}
    for s in segments:
        kms.setdefault(int(s["d0"] // 1000), []).append(s)
    return [_sum(group) for _, group in sorted(kms.items()) if sum(s["dist"] for s in group) >= 800]


def _effort(segments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Decoupling and fade, on hill-adjusted pace so a climb is never read as tiring."""
    total = _sum(segments)
    if total["secs"] < STEADY_MIN_SECONDS:
        return None
    kms = _by_km(segments)
    if len(kms) < 4:
        return None
    speeds = [k["flat_dist"] / k["secs"] for k in kms]
    first_half = speeds[: len(speeds) // 2]
    spread = (max(first_half) - min(first_half)) / median(first_half) if first_half else 1.0
    if spread > STEADY_MAX_VARIATION * 2:
        # Intervals or a fartlek: half against half would compare reps with recoveries.
        return {"note": "Pace varied too much (intervals or surges) for drift or fade to mean anything."}

    out: dict[str, Any] = {}
    half_time = total["secs"] / 2
    elapsed, first, second = 0.0, [], []
    for s in segments:
        (first if elapsed < half_time else second).append(s)
        elapsed += s["secs"]
    a, b = _sum(first), _sum(second)
    if a["hr"] and b["hr"]:
        eff_a = a["flat_dist"] / a["secs"] / a["hr"]
        eff_b = b["flat_dist"] / b["secs"] / b["hr"]
        out["decoupling_pct"] = round((eff_a - eff_b) / eff_a * 100, 1)
    out["first_half"] = drop_empty({"flat_equivalent_pace": pace_per_km(a["flat_dist"], a["secs"]), "avg_hr": rounded(a["hr"], 0)})
    out["second_half"] = drop_empty({"flat_equivalent_pace": pace_per_km(b["flat_dist"], b["secs"]), "avg_hr": rounded(b["hr"], 0)})

    baseline = median(speeds[1 : max(2, len(speeds) // 2)])
    for i in range(2, len(speeds)):
        rest = speeds[i:]
        if all(v < baseline * (1 - FADE_PCT / 100) for v in rest[:3]) and median(rest) < baseline * (1 - FADE_PCT / 100):
            out["slowed_from_km"] = i + 1
            out["slowed_by_pct"] = round((1 - median(rest) / baseline) * 100, 1)
            break
    return drop_empty(out)


def _shape(columns: Mapping[str, Sequence[float | None]]) -> dict[str, Any] | None:
    lat, lon = columns.get("directLatitude"), columns.get("directLongitude")
    distance = columns.get("sumDistance")
    if not lat or not lon or not distance:
        return None
    track = [((la, lo), d) for la, lo, d in zip(lat, lon, distance) if None not in (la, lo, d) and (la, lo) != (0.0, 0.0)]
    if len(track) < 10:
        return None
    start, end = track[0][0], track[-1][0]
    total = track[-1][1] - track[0][1]
    far_point, far_d = max(track, key=lambda p: _metres_between(start, p[0]))
    far = _metres_between(start, far_point)

    def at(fraction: float) -> tuple[float, float]:
        goal = track[0][1] + total * fraction
        return min(track, key=lambda p: abs(p[1] - goal))[0]

    gap = _metres_between(start, end)
    if gap > LOOP_METRES:
        kind = "point to point"
    elif _metres_between(at(0.25), at(0.75)) < LOOP_METRES and abs(far_d - track[0][1] - total / 2) < total * 0.15:
        kind = "out and back"
    else:
        kind = "loop"
    return drop_empty({
        "kind": kind,
        "finish_from_start_m": round(gap) if kind == "point to point" else None,
        "furthest_from_start_km": km(far, 1),
        "furthest_at_km": km(far_d - track[0][1], 1),
    })


def analyse(columns: Mapping[str, Sequence[float | None]]) -> dict[str, Any] | None:
    """Hills, flat-equivalent pace, drift and the route's shape, from parsed columns."""
    garmin_gap = any(v for v in (columns.get("directGradeAdjustedSpeed") or []))
    points = _points(columns)
    segments = _segments(points, garmin_gap) if len(points) > 2 else []
    elevations = [p["e"] for p in points]
    total = _sum(segments) if segments else None
    result = drop_empty({
        "flat_equivalent_pace": pace_per_km(total["flat_dist"], total["secs"]) if total else None,
        "flat_equivalent_source": ("garmin" if garmin_gap else "estimated from grade") if total else None,
        "lowest_m": round(min(elevations)) if elevations else None,
        "highest_m": round(max(elevations)) if elevations else None,
        "by_gradient": _bands(segments) if segments else None,
        "climbs": _climbs(points, segments) if segments else None,
        "effort": _effort(segments) if segments else None,
        "route": _shape(columns),
    })
    if not result:
        return None
    result["how_to_read"] = (
        "flat_equivalent_pace is what the effort was worth on level ground. "
        "Climbs are the biggest hills, in order. decoupling_pct compares "
        "flat-equivalent pace per heartbeat across the two halves: under 5 is "
        "good aerobic durability on a steady run, over 10 means the effort "
        "cost more as it went on (heat, fuel, fatigue or going out too fast). "
        "slowed_from_km is where hill-adjusted pace dropped and stayed down."
    )
    return result


def _start_end(row: Mapping[str, Any]) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    def pair(lat_key: str, lon_key: str) -> tuple[float, float] | None:
        la, lo = row.get(lat_key), row.get(lon_key)
        return (float(la), float(lo)) if isinstance(la, (int, float)) and isinstance(lo, (int, float)) else None

    return pair("startLatitude", "startLongitude"), pair("endLatitude", "endLongitude")


def same_route(this: Mapping[str, Any], earlier: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Earlier runs that started and finished in the same places over about the same distance.

    Not a match on the line itself: two different loops of the same length
    from the same door would count as one. Good enough for "my usual loop".
    """
    start, end = _start_end(this)
    distance = this.get("distance")
    if start is None or not isinstance(distance, (int, float)) or distance <= 0:
        return None
    sport = ((this.get("activityType") or {}).get("typeKey") or "").lower()

    matches = []
    for row in earlier:
        if row.get("activityId") == this.get("activityId"):
            continue
        if sport and ((row.get("activityType") or {}).get("typeKey") or "").lower() != sport:
            continue
        d = row.get("distance")
        if not isinstance(d, (int, float)) or abs(d - distance) > distance * SAME_ROUTE_DISTANCE:
            continue
        s, e = _start_end(row)
        if s is None or _metres_between(start, s) > SAME_ROUTE_START_M:
            continue
        if end is not None and e is not None and _metres_between(end, e) > SAME_ROUTE_START_M:
            continue
        matches.append(row)
    if not matches:
        return None

    def seconds(row: Mapping[str, Any]) -> float | None:
        v = row.get("movingDuration") or row.get("duration")
        return float(v) if isinstance(v, (int, float)) and v > 0 else None

    def line(row: Mapping[str, Any]) -> dict[str, Any]:
        return drop_empty({
            "date": str(row.get("startTimeLocal") or "")[:10] or None,
            "activity_id": row.get("activityId"),
            "distance_km": km(row.get("distance")),
            "pace_per_km": pace_per_km(row.get("distance"), seconds(row)),
            "avg_hr": rounded(row.get("averageHR"), 0),
            "elevation_gain_m": rounded(row.get("elevationGain"), 0),
        })

    def speed(row: Mapping[str, Any]) -> float:
        s = seconds(row)
        return (row.get("distance") or 0) / s if s else 0.0

    everyone = [*matches, this]
    ranked = sorted(everyone, key=speed, reverse=True)
    rank = ranked.index(this) + 1
    recent = sorted(matches, key=lambda r: str(r.get("startTimeLocal") or ""), reverse=True)
    return drop_empty({
        "earlier_runs": len(matches),
        "this_run_rank": f"{rank} of {len(everyone)} by pace",
        "fastest": line(ranked[0]) if ranked[0] is not this else None,
        "recent": [line(r) for r in recent[:SAME_ROUTE_SHOWN]],
        "note": "Matched on start, finish and distance, so a different loop of the same length from the same door also counts.",
    })
