"""Cases the app's TypeScript engines must answer exactly as the server's Python ones do.

Writes garmin_mcp/domain/fixtures/*.json from the Python engines; the app's
tests (mobile/src/domain/*.test.ts) read the same files. --check fails when the
Python engines no longer produce what's committed, so a change on one side
can't land without the other.

    python3 scripts/domain_fixtures.py          # rewrite the fixtures
    python3 scripts/domain_fixtures.py --check  # CI
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import math  # noqa: E402
from datetime import datetime  # noqa: E402

from garmin_mcp.domain import adjust, backtoback, paces, postrun, proposal  # noqa: E402
from garmin_mcp.domain.load import band  # noqa: E402
from garmin_mcp.domain.model import Session  # noqa: E402

OUT = ROOT / "garmin_mcp" / "domain" / "fixtures"


def paces_cases() -> list[dict]:
    races = [(10, 2610, "2026-10-12"), (10, 2700, None), (5, 1230, "2026-09-05"), (21.0975, 5400, "2026-04-26"),
             (42.195, 11700, "2026-04-24"), (15, 3900, None), (10, 3600, None)]
    out = []
    for km, secs, when in races:
        for step in (-2, -1, 0, 1, 2):
            p = paces.paces(km, secs, race_date=date.fromisoformat(when) if when else None, step=step)
            out.append({"race_km": km, "race_seconds": secs, "race_date": when, "step": step,
                        "zones": {z: list(v) for z, v in p.zones.items()}, "basis": p.basis})
    return out


def _week(monday: date, spec, prefix):
    return [Session(monday + timedelta(days=d), t, t, km, None, id=f"{prefix}{d}") for d, t, km in spec]


def backtoback_cases() -> list[dict]:
    monday = date(2026, 11, 23)
    plans = {
        "spread": _week(monday, [(1, "tempo", 10), (3, "easy", 8), (4, "intervals", 9), (6, "long", 22)], "a"),
        "long_hard": _week(monday, [(1, "easy", 8), (3, "tempo", 10), (5, "intervals", 9), (6, "long", 28)], "b"),
        "race_week": _week(monday, [(0, "easy", 6), (2, "tempo", 8), (4, "easy", 5), (6, "race", 21.1)], "c"),
        "five_day": _week(monday, [(0, "easy", 8), (1, "intervals", 10), (3, "tempo", 10), (4, "easy", 6),
                                   (6, "long", 26)], "d"),
    }
    out = []
    for name, plan in plans.items():
        sessions = [{"id": s.id, "date": s.date.isoformat(), "type": s.type, "distance_km": s.distance_km} for s in plan]
        out.append({"plan": name, "sessions": sessions, "move": None,
                    "clashes": [c.message for c in backtoback.clashes(plan)]})
        for s in plan:
            for d in range(7):
                to = monday + timedelta(days=d)
                if to == s.date:
                    continue
                out.append({"plan": name, "sessions": sessions, "move": {"id": s.id, "to": to.isoformat()},
                            "clashes": [c.message for c in backtoback.check_move(plan, s.id, to)]})
    return out


# The design's persona (build prompt, section 16), as the app's Plan fixture has it
# (mobile/src/features/plan/fixture.ts, same session ids): week 4 to week 7, today
# Fri 27 Nov. Rest days have no session.
TODAY = date(2026, 11, 27)
NUMBERS = {date(2026, 11, 23): 4, date(2026, 11, 30): 5, date(2026, 12, 7): 6, date(2026, 12, 14): 7}


def _s(day: str, kind: str, title: str, km: float | None = None, minutes: float | None = None) -> Session:
    return Session(date.fromisoformat(day), kind, title, km, minutes, id=f"{day}-{kind}")


PERSONA = [
    _s("2026-11-24", "easy", "8 km easy + strides", 8), _s("2026-11-25", "intervals", "6 × 800 m", 10),
    _s("2026-11-26", "easy", "9 km run club", 8), _s("2026-11-27", "tempo", "3 × 2 km at threshold", 10),
    _s("2026-11-28", "strength", "Full body (runner)", None, 27), _s("2026-11-29", "long", "Long 18 km easy", 18),
    _s("2026-12-01", "easy", "8 km easy", 8), _s("2026-12-02", "intervals", "10 × 400 m", 9.5),
    _s("2026-12-03", "easy", "10 km easy", 10), _s("2026-12-05", "easy", "8 km easy", 8),
    _s("2026-12-06", "long", "Long 20 km easy", 20),
    _s("2026-12-08", "easy", "8 km easy", 8), _s("2026-12-09", "tempo", "4 × 2 km at threshold", 12),
    _s("2026-12-10", "easy", "10 km easy", 10), _s("2026-12-12", "easy", "8 km easy", 8),
    _s("2026-12-13", "long", "Long 22 km easy", 22),
    _s("2026-12-15", "easy", "10 km easy", 10), _s("2026-12-16", "intervals", "6 × 1 km", 12),
    _s("2026-12-17", "easy", "10 km easy", 10), _s("2026-12-19", "easy", "10 km easy", 10),
    _s("2026-12-20", "long", "Long 26 km with 10 at marathon pace", 26),
]
# Week 7's planned volume before the extra kilometres crept in: its band's top is the trim's limit.
WEEK7_INTENDED_KM = 58


def proposal_cases() -> dict:
    """What the server proposes for the persona: the app's development fixture, from the real engines."""
    out = {}
    for reason in ("unwell", "niggle", "travelling", "busy", "race_moved"):
        for back in range(7):
            since = TODAY - timedelta(days=back)
            a = adjust.adjust(PERSONA, reason, since, today=TODAY)
            out[f"life:{reason}:{since.isoformat()}"] = proposal.view(a, PERSONA, TODAY, since=since, numbers=NUMBERS)
    for name, make in (("easier", adjust.easier), ("rest", adjust.rest), ("swap", adjust.swap)):
        out[name] = proposal.view(make(PERSONA, TODAY), PERSONA, TODAY, numbers=NUMBERS)
    week7 = date(2026, 12, 14)
    limit = band(WEEK7_INTENDED_KM)[1]
    out["trim:2026-12-14"] = proposal.view(adjust.trim(PERSONA, week7, limit, TODAY, number=7), PERSONA, TODAY,
                                           focus=week7, numbers=NUMBERS, id="trim-2026-12-14")
    return out


# The persona's recorded runs, as a watch would have them: 2 s samples. Built so the
# numbers match the plan fixture's rows ("Intervals · 11.2 km · reps 4:01 /km").
ZONE_TOPS = (130, 145, 155, 165)        # bpm: Z1 under 130 ... Z5 from 165


def _record(segments, start_lat=51.5045, start_lon=-0.1700, turns=(1.3, 3)):
    """segments: (kind, metres, s/km, hr_from, hr_to, hr_shape, cadence). Returns samples, laps and zone seconds."""
    samples, laps, zones = [], [], [0.0] * 5
    t = d = 0.0
    total = sum(seg[1] for seg in segments)
    for kind, metres, pace, h0, h1, shape, cad in segments:
        start_d, start_t, end_d = d, t, d + metres
        while d < end_d - 1e-6:
            dt = min(2.0, (end_d - d) * pace / 1000)   # the last step of a lap only as long as it needs
            d = min(end_d, d + dt / pace * 1000)
            t += dt
            x = t - start_t
            if shape == "rise":
                hr = h0 + (h1 - h0) * (1 - math.exp(-x / 20))
            elif shape == "fall":
                hr = h1 + (h0 - h1) * math.exp(-x / 25)
            else:
                hr = h0 + (h1 - h0) * (d - start_d) / metres
            u = d / total
            samples.append(postrun.Sample(
                t=round(t, 1), d=round(d, 1), hr=round(hr), cadence=cad,
                elevation=round(22 + 6 * math.sin(d / 900) + 3 * math.sin(d / 250), 1),
                lat=round(start_lat + 0.006 * math.sin(2 * math.pi * u * turns[0]), 6),
                lon=round(start_lon + 0.03 * u + 0.004 * math.sin(2 * math.pi * u * turns[1]), 6)))
            zones[sum(hr >= top for top in ZONE_TOPS)] += dt
        laps.append(postrun.Lap(kind, start_d, end_d))
    return samples, laps, zones


def postrun_cases() -> dict:
    """The post-run screen for the persona's runs this week: the app's development fixture, from the real engine."""
    reps = [(238, 164), (241, 165), (240, 166), (240, 168), (244, 168), (242, 172)]
    segs = [("warmup", 2000, 340, 118, 138, "line", 168)]
    for i, (pace, peak) in enumerate(reps):
        segs.append(("work", 800, pace, 128 if i else 138, peak, "rise", 184 if i < 5 else 182))
        if i < len(reps) - 1:
            segs.append(("recovery", 400, 380, peak, 124, "fall", 162))
    segs.append(("cooldown", 2400, 345, 130, 138, "line", 166))
    samples, laps, zones = _record(segs)
    intervals = postrun.Run(id="2026-11-25-run", start=datetime(2026, 11, 25, 6, 30), type="intervals", title="6 × 800 m",
                            samples=samples, laps=laps, zone_seconds=zones, session_id="2026-11-25-intervals",
                            planned=postrun.Planned(distance_km=11.0, pace=(238, 248), reps=6, rep_words="6 × 800 m"))
    km_paces = [318, 317, 316, 315, 314, 313, 312, 310, 309]
    segs = [("run", 1000 if i < 8 else 1200, p, 136 + i, 137 + i, "line", 172 + (i > 5)) for i, p in enumerate(km_paces)]
    samples, laps, zones = _record(segs, start_lat=51.5310, start_lon=-0.1540, turns=(0.6, 1.5))
    club = postrun.Run(id="2026-11-26-run", start=datetime(2026, 11, 26, 18, 45), type="easy", title="9 km run club",
                       samples=samples, laps=(), zone_seconds=zones, session_id="2026-11-26-easy",
                       planned=postrun.Planned(distance_km=9.0, pace=(327, 370)))
    easy = [("run", 1000 if i < 7 else 300, p, 134 + i // 2, 135 + i // 2, "line", 170) for i, p in enumerate([314, 313, 312, 312, 311, 312, 311, 310])]
    easy += [("strides", 100, 200, 140, 158, "rise", 192) if k % 2 == 0 else ("recovery", 100, 600, 158, 132, "fall", 150) for k in range(7)]
    samples, laps, zones = _record(easy, start_lat=51.5190, start_lon=-0.1880, turns=(2.1, 0.8))
    strides = postrun.Run(id="2026-11-24-run", start=datetime(2026, 11, 24, 7, 10), type="easy", title="8 km easy + strides",
                          samples=samples, laps=[postrun.Lap("run", 0, 7300)] + [lap for lap in laps if lap.kind != "run"],
                          zone_seconds=zones, session_id="2026-11-24-easy", planned=postrun.Planned(distance_km=8.0, pace=(327, 370)))
    return {r.session_id: postrun.view(r) for r in (strides, intervals, club)}


FILES = {"paces.json": paces_cases, "backtoback.json": backtoback_cases, "proposals.json": proposal_cases,
         "postrun.json": postrun_cases}
# The app's development build reads its own copy: Metro bundles only what's inside mobile/.
COPIES = {"proposals.json": ROOT / "mobile" / "src" / "features" / "watch" / "proposals.fixture.json",
          "postrun.json": ROOT / "mobile" / "src" / "features" / "run" / "runs.fixture.json"}


def main(argv: list[str]) -> int:
    stale = []
    for name, make in FILES.items():
        text = json.dumps({"_generated": "scripts/domain_fixtures.py; do not edit", "cases": make()}, indent=1) + "\n"
        for path in (OUT / name, *([COPIES[name]] if name in COPIES else [])):
            if "--check" in argv:
                if not path.exists() or path.read_text() != text:
                    stale.append(str(path.relative_to(ROOT)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
    if stale:
        print("domain_fixtures: out of date, run python3 scripts/domain_fixtures.py: " + ", ".join(stale), file=sys.stderr)
        return 1
    print("domain_fixtures: " + ("in step" if "--check" in argv else "written") + ": " + ", ".join(FILES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
