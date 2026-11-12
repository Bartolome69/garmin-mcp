"""Was an easy run easy?

Only for a run that was an easy session on the plan, and judged against that
session's own target when it has one: a heart-rate range, else a pace range.
Without a target, Garmin's own rating of what the run trained decides. Never
Garmin's heart-rate zones: they move when Garmin re-estimates them, and on
Nov 8 Bart's 127 bpm easy run, inside its 120-140 bpm target, was half in
"zone 3" because zone 3 had dropped to 129 bpm the day before.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .share import _range as pace_range

EASY_TYPES = ("easy", "recovery")
# A quarter of the run over its target is a pattern, not a hill or a gust.
TOO_HARD_SHARE = 0.25
# Room before a lap counts as over: a few beats, a few seconds a km.
HR_SLACK = 2
PACE_SLACK = 10
# Laps shorter than this are strides or a stop, not the run's effort.
MIN_LAP_SECONDS = 90
# What Garmin's training-effect label calls a run that trained more than the base.
HARD_LABELS = {"TEMPO": "tempo", "LACTATE_THRESHOLD": "threshold", "THRESHOLD": "threshold",
               "VO2MAX": "VO2 max", "ANAEROBIC_CAPACITY": "anaerobic", "SPEED": "speed", "SPRINT": "speed"}
EASY_LABELS = ("RECOVERY", "AEROBIC_BASE", "BASE")

NOTE = (
    "Whether this easy session was run easy, against its own target. When the verdict is "
    "too_hard, say so in one plain line after the rest of the answer, with the figure "
    "(how much of it ran over and from where), and why it matters in a few words: easy days "
    "kept easy leave the legs for the hard ones. Don't lecture, and don't change the plan "
    "unless they ask. When it's easy, leave it unsaid unless they ask."
)


def _laps(raw: Sequence[Mapping[str, Any]]) -> list[tuple[float, float, float | None]]:
    """(seconds, metres, average bpm) for each lap long enough to be the run's effort."""
    out = []
    for lap in raw or []:
        secs = lap.get("movingDuration") or lap.get("duration") or lap.get("elapsedDuration")
        metres = lap.get("distance")
        if not isinstance(secs, (int, float)) or not isinstance(metres, (int, float)):
            continue
        if secs < MIN_LAP_SECONDS or metres <= 0:
            continue
        bpm = lap.get("averageHR")
        out.append((float(secs), float(metres), float(bpm) if isinstance(bpm, (int, float)) else None))
    return out


def _from_km(laps: list[tuple[float, float, float | None]], over: list[bool]) -> int | None:
    """The kilometre the run went over and stayed over to the end, if it did."""
    if not over or not over[-1]:
        return None
    i = len(over)
    while i > 0 and over[i - 1]:
        i -= 1
    if i == 0:
        return None
    return int(sum(m for _s, m, _b in laps[:i]) / 1000) + 1


def _result(verdict: str, against: str, share: float, where: str, start: int | None) -> dict[str, Any]:
    pct = round(share * 100)
    detail = f"{pct}% of it ran {where}" + (f", from km {start} on" if start else "")
    return {"verdict": verdict, "against": against, "over_share_pct": pct,
            "from_km": start, "detail": detail if pct else None, "note": NOTE}


def check(planned: Mapping[str, Any] | None, laps: Sequence[Mapping[str, Any]] | None,
          activity: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """{verdict: easy | too_hard, against, detail, ...} for a planned easy run, else None.

    planned is share.plan_context's {n, ty, tg}; laps are Garmin's lapDTOs;
    activity is the run's flat summary, for its training-effect label.
    """
    if not planned or planned.get("ty") not in EASY_TYPES:
        return None
    rows = _laps(laps or [])
    total = sum(s for s, _m, _b in rows)
    target = str(planned.get("tg") or "")

    bpm = re.fullmatch(r"\s*(\d{2,3})\s*[-–]\s*(\d{2,3})\s*bpm\s*", target)
    if bpm and rows and all(b is not None for _s, _m, b in rows):
        low, top = sorted((int(bpm.group(1)), int(bpm.group(2))))
        over = [b > top + HR_SLACK for _s, _m, b in rows]
        share = sum(s for (s, _m, _b), o in zip(rows, over) if o) / total
        return _result("too_hard" if share >= TOO_HARD_SHARE else "easy",
                       f"its {low}–{top} bpm target", share, f"above {top} bpm", _from_km(rows, over))

    window = pace_range(target)
    if window and rows:
        fast, slow = window
        over = [s / (m / 1000) < fast - PACE_SLACK for s, m, _b in rows]
        share = sum(s for (s, _m, _b), o in zip(rows, over) if o) / total
        quick = f"{fast // 60}:{fast % 60:02d}"
        return _result("too_hard" if share >= TOO_HARD_SHARE else "easy",
                       f"its {quick}–{slow // 60}:{slow % 60:02d} /km target", share,
                       f"quicker than {quick} /km", _from_km(rows, over))

    label = str((activity or {}).get("trainingEffectLabel") or "").upper()
    if label in HARD_LABELS:
        return {"verdict": "too_hard", "against": "Garmin's training effect",
                "detail": f"Garmin rated what it trained as {HARD_LABELS[label]}, not base", "note": NOTE}
    if label in EASY_LABELS:
        return {"verdict": "easy", "against": "Garmin's training effect", "note": NOTE}
    return None


def card(found: Mapping[str, Any] | None) -> dict[str, str] | None:
    """The app's line for a too-hard easy run; nothing for one run easy."""
    if not found or found.get("verdict") != "too_hard":
        return None
    return {"head": "Easy run, run harder than planned",
            "sub": f"{str(found.get('detail') or '').capitalize()}. Easy days kept easy leave the legs for the hard ones."}
