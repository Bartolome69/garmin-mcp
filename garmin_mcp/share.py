"""A link that draws one run as an image to post, from get_activity_details.

The plan card's "Share this run" button builds the same link in the browser
(runPayload in ui/plan.html), and garmin.daash.run/share/ draws it. Building
it here too means a chat that asked "how was my run" can offer the image
without the plan card. The page reads a run as { v, run, t, s, k, c, f, l, a,
n }: the title, the day and place, a kicker, the session's colour, up to four
[figure, words] facts, the laps as [seconds, pace s/km, work, km], the
average pace in s/km, and up to three [colour, line] notes. Everything stays
in the link's #fragment, which the browser never sends to a server.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import date
from typing import Any

SHARE_PAGE = "https://garmin.daash.run/share/"

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# What a run's name says it was, for its colour, checked in this order.
_COLOUR_WORDS = (
    ("race", r"\b(race|parkrun|marathon)\b"),
    ("intervals", r"\b(intervals?|reps?|vo2)\b"),
    ("threshold", r"\b(threshold|tempo|sub-?t\b|sub-?threshold|cruise)"),
    ("long", r"\blong\b"),
)


def _secs(text: Any) -> int | None:
    """'4:03 /km' or '4:03' in seconds."""
    m = re.search(r"(\d+):(\d{2})", str(text or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _clock_secs(text: Any) -> int | None:
    """'1h 03m 17s', '12m 09s' or '43s' in seconds."""
    m = re.fullmatch(r"\s*(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?\s*", str(text or ""))
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + s


def _clock(secs: float | None) -> str | None:
    if not secs:
        return None
    total = int(round(secs))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_km(value: float) -> str:
    r = round(float(value), 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def _title(name: str) -> str:
    """Garmin's name for the run, without the plan code create_plan adds."""
    t = re.sub(r"\s*·\s*[A-Z0-9]{1,6}$", "", name.strip())
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*k(?:m)?\b", r"\1 km", t, flags=re.I)
    t = re.sub(r"(\d)\s*[x×]\s*(\d)", "\\1\u00a0×\u00a0\\2", t, flags=re.I)
    t = re.sub(r"(\d) (km|mi|min)\b", "\\1\u00a0\\2", t)
    return (t[:1].upper() + t[1:]) if t else "Run"


def _colour(name: str) -> str:
    low = name.lower()
    for colour, pattern in _COLOUR_WORDS:
        if re.search(pattern, low):
            return colour
    return "easy"


def _work_laps(splits: list[dict[str, Any]], secs: list[int | None]) -> list[bool]:
    """Which laps were the work: what a structured workout says lap by lap, or
    else the fast ones when the laps split clearly into fast and slow."""
    if any(x.get("intensity") for x in splits):
        reps = any(x.get("intensity") == "interval" for x in splits)
        return [x.get("intensity") == "interval" if reps else not x.get("intensity") for x in splits]
    known = [v for v in secs if v is not None]
    lo, hi = min(known), max(known)
    if hi - lo < 40:
        return [True] * len(splits)
    mid = (lo + hi) / 2
    return [v is not None and v < mid for v in secs]


def run_payload(details: dict[str, Any]) -> dict[str, Any] | None:
    """The share page's run, from a get_activity_details answer; None for
    anything that isn't a run with a distance."""
    summary = details.get("summary") or {}
    if "running" not in str(summary.get("type") or "") or not summary.get("distance_km"):
        return None
    name = str(summary.get("name") or "Run")

    sub = ""
    start = str(summary.get("start_local") or "")[:10]
    try:
        d = date.fromisoformat(start)
        sub = f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"
    except ValueError:
        pass
    if summary.get("location"):
        sub = f"{sub} · {summary['location']}" if sub else str(summary["location"])

    pace = summary.get("pace_per_km")
    bpm = (summary.get("heart_rate") or {}).get("average_bpm")
    facts = [
        [_fmt_km(summary["distance_km"]), "km"],
        [_clock(summary.get("duration_seconds") or _clock_secs(summary.get("duration"))), "time"],
        [re.sub(r"\s*/km$", "", pace), "/km average"] if pace else None,
        [str(int(bpm)), "bpm average"] if bpm else None,
    ]
    facts = [f for f in facts if f and f[0]]

    splits = details.get("splits") or []
    if len(splits) < 2:
        splits = (details.get("inside") or {}).get("km_splits") or splits
    splits = [x for x in splits if isinstance(x, dict) and x.get("pace_per_km")]
    laps: list[list[Any]] = []
    notes: list[list[str]] = []
    if 1 < len(splits) <= 60:
        secs = [_secs(x.get("pace_per_km")) for x in splits]
        work = _work_laps(splits, secs)
        for x, s, w in zip(splits, secs, work):
            dist = x.get("distance_km")
            took = _clock_secs(x.get("duration")) or (round(dist * (s or 300)) if dist else 0) or 1
            laps.append([took, s or 0, 1 if w else 0, round(dist, 2) if dist is not None else None])
        reps = [x for x, w in zip(splits, work) if w]
        if 1 < len(reps) < len(splits) and len(reps) <= 8:
            line = "Reps at " + ", ".join(re.sub(r"\s*/km$", "", x["pace_per_km"]) for x in reps) + " /km"
            if all(x.get("avg_hr") for x in reps):
                line += " · heart rate " + ", ".join(str(int(x["avg_hr"])) for x in reps)
            notes.append(["c", line])

    return {
        "v": 1, "run": 1, "t": _title(name)[:80], "s": sub[:60], "k": "Run recap", "c": _colour(name),
        "f": facts, "l": laps, "a": _secs(pace), "n": notes,
    }


def link(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return SHARE_PAGE + "#" + base64.urlsafe_b64encode(text).decode("ascii").rstrip("=")


def run_link(details: dict[str, Any]) -> str | None:
    payload = run_payload(details)
    return link(payload) if payload else None
