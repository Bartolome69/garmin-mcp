"""A link that draws one run as an image to post, from get_activity_details.

The plan card's "Share this run" button builds the same link in the browser
(runPayload in ui/plan.html), and garmin.daash.run/share/ draws it. Building
it here too means a chat that asked "how was my run" can offer the image
without the plan card. The page reads a run as { v, run, t, s, k, c, f, l, a,
n }: the title, the day and place, a kicker, the session's colour, up to four
[figure, words] facts, the laps as [seconds, pace s/km, work, km, bpm], the
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


def plan_context(plan: dict[str, Any] | None, activity_id: Any,
                 workout: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The planned session a run was, for its caption: { n, ty, k, w, b, tg }.

    The session's name and type, its planned km, [week, of weeks] and the
    block's name where get_plan knows them, and the reps' target when the
    workout was read. None for a run that wasn't on the plan.
    """
    if not isinstance(plan, dict) or activity_id is None:
        return None
    row, n = None, None
    for i, week in enumerate(plan.get("weeks") or [], start=1):
        for s in week.get("sessions") or []:
            if s.get("activity_id") == activity_id:
                row, n = s, i
    if not row or row.get("kind", "run") != "run":
        return None
    day = str(row.get("date") or "")[:10]
    block, week, total = None, None, None
    if plan.get("label"):
        block, week, total = plan.get("name"), n, plan.get("weeks_total") or len(plan.get("weeks") or [])
    else:
        for key in ("current_plan", "upcoming_plan"):
            p = plan.get(key) or {}
            if p.get("starts") and p.get("ends") and p["starts"] <= day <= p["ends"]:
                try:
                    first, d = date.fromisoformat(p["starts"]), date.fromisoformat(day)
                    # Weeks run Monday to Sunday from the block's first Monday.
                    week = ((d.toordinal() - d.weekday()) - (first.toordinal() - first.weekday())) // 7 + 1
                except ValueError:
                    week = None
                block, total = p.get("name"), p.get("weeks_total")
                break
    if not block:
        # A block with no name of its own goes by its goal, as the plan card's
        # heading does: "Base block (Norwegian singles style): 4 weeks" is "Base block".
        p = plan if plan.get("label") else next(
            (plan.get(k) or {} for k in ("current_plan", "upcoming_plan")
             if (plan.get(k) or {}).get("starts", "9") <= day <= (plan.get(k) or {}).get("ends", "0")), {})
        block = re.sub(r"\s*[(:,].*$", "", str(p.get("goal") or "")).strip() or None
    target = None
    if workout:
        from . import training_plan
        target = training_plan.main_target(training_plan.workout_steps(workout))
    out = {"n": _title(str(row.get("name") or "Run")).replace("\u00a0", " ")[:60], "ty": row.get("type"),
           "k": row.get("planned_km"), "w": [week, total] if week and total else None,
           "b": str(block)[:40] if block else None, "tg": str(target)[:30] if target else None}
    return {k: v for k, v in out.items() if v is not None}


def run_payload(details: dict[str, Any], planned: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The share page's run, from a get_activity_details answer; None for
    anything that isn't a run with a distance. With planned (plan_context),
    the caption says what was planned against what was run."""
    summary = details.get("summary") or {}
    if "running" not in str(summary.get("type") or "") or not summary.get("distance_km"):
        return None
    name = str(summary.get("name") or "Run")
    # A run started from a workout on the watch is named "<place> - <workout>";
    # the place is said under the title already.
    place = str(summary.get("location") or "").strip()
    if place and name.lower().startswith(place.lower() + " - ") and len(name) > len(place) + 3:
        name = name[len(place) + 3:]

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
            hr = x.get("avg_hr")
            laps.append([took, s or 0, 1 if w else 0, round(dist, 2) if dist is not None else None,
                         int(hr) if hr else None])
        reps = [x for x, w in zip(splits, work) if w]
        if 1 < len(reps) < len(splits) and len(reps) <= 8:
            line = "Reps at " + ", ".join(re.sub(r"\s*/km$", "", x["pace_per_km"]) for x in reps) + " /km"
            if all(x.get("avg_hr") for x in reps):
                line += " · heart rate " + ", ".join(str(int(x["avg_hr"])) for x in reps)
            notes.append(["c", line])

    out = {
        "v": 1, "run": 1, "t": _title(name)[:80], "s": sub[:60], "k": "Run recap", "c": _colour(name),
        "f": facts, "l": laps, "a": _secs(pace), "n": notes, "g": attribution(summary.get("device")),
    }
    if planned:
        out["pl"] = planned
    return out


def attribution(device: Any = None) -> str:
    """Garmin's credit for data from a Garmin device, "Garmin [device model]",
    by the title of anything shown to other people (Garmin Connect Developer
    Program Agreement 6.4 and Garmin's API brand guidelines, Nov 10). Just
    "Garmin" when the model isn't known."""
    name = re.sub(r"\s+", " ", str(device or "")).strip()[:32]
    if not name:
        return "Garmin"
    return name if name.lower().startswith("garmin") else f"Garmin {name}"


# The caption's last line, back to Daash: "Planned by" when the run was a
# session on a Daash plan, "via" otherwise (Nov 8, Bart's wording).
CAPTION_SITE = "daash.run"
# A session's type, as a caption names it.
_KIND_WORDS = {"easy": "Easy run", "recovery": "Recovery run", "long": "Long run", "race": "Race",
               "threshold": "Quality session", "sub": "Quality session", "intervals": "Quality session"}
_HARD = ("threshold", "sub", "intervals")
# A rep is on target within this many seconds a km either side of the range,
# about what the watch's own pace alert allows.
REP_SLACK = 3


def _pace(secs: float) -> str:
    secs = int(round(secs))
    return f"{secs // 60}:{secs % 60:02d}"


def _reps(laps: list[Any]) -> list[list[Any]]:
    """The work laps, when the run had reps: 2 to 8 of them, among easier laps."""
    work = [l for l in laps or [] if isinstance(l, list) and len(l) > 2 and l[2]]
    return work if 1 < len(work) < len(laps or []) and len(work) <= 8 else []


def _range(target: str | None) -> tuple[int, int] | None:
    found = re.findall(r"(\d+):(\d{2})", str(target or ""))
    if len(found) == 2 and "km" in str(target):
        a, b = (int(m) * 60 + int(s) for m, s in found)
        return min(a, b), max(a, b)
    return None


def caption(payload: dict[str, Any]) -> str | None:
    """A run's caption to paste into Strava.

    A run that was a session on the plan says what was planned against what
    was run (Nov 8): the type, the week and the block, "Planned: ..." and
    "Ran: ...", and "On plan" when the distance came out near the plan. An
    interval session goes rep by rep, against the target when there is one.
    Any other run is its name, its figures and its reps. Each ends with a
    line back to Daash.

    Strava's API can't be used for this (its 2026 policy bars Strava data in
    any AI application), so the person pastes it. The share page builds the
    same text in the browser (caption() in docs/share/index.html); keep the
    two in step: tests/hosted_test.py and tests/card_test.js check both.
    """
    if not payload or not payload.get("run"):
        return None
    facts = payload.get("f") or []
    figures = []
    for value, words in facts:
        unit = str(words).split(" ")[0]
        figures.append(str(value) if unit == "time" else f"{value} {unit}")
    plan = payload.get("pl") if isinstance(payload.get("pl"), dict) else None
    # No "Garmin [device]" line: the caption goes into the Strava activity, which
    # names the device itself; the image keeps Garmin's credit (Nov 12, Bart).
    if not plan:
        lines = [str(payload.get("t") or "Run").replace("\u00a0", " ")]
        if figures:
            lines.append(" · ".join(figures))
        lines += [str(n[1])[:90] for n in payload.get("n") or [] if len(n) > 1 and n[1]][:1]
        return "\n".join(l for l in lines if l) + f"\n\nvia {CAPTION_SITE}"

    week = plan.get("w") or []
    week_words = f"week {week[0]} of {week[1]}" if len(week) == 2 else None
    reps = _reps(payload.get("l") or [])
    distance = next((f for f in facts if str(f[1]).split(" ")[0] in ("km", "mi")), None)
    if plan.get("ty") in _HARD and reps:
        lines = [" · ".join(x for x in (plan.get("n"), week_words) if x)]
        target = plan.get("tg")
        unit = distance[1].split(" ")[0] if distance else "km"
        shown = re.sub(r"\s*/(km|mi)$", "", target) if target else None
        lines.append("Reps: " + " · ".join(_pace(r[1]) for r in reps) + f" /{unit}"
                     + (f" (target {shown})" if shown else ""))
        bpm = [r[4] for r in reps if len(r) > 4 and r[4]]
        average = next((f[0] for f in facts if str(f[1]).startswith("bpm")), None)
        third = [f"{distance[0]} {unit} total" if distance else None,
                 f"{round(sum(bpm) / len(bpm))} bpm on the reps" if len(bpm) == len(reps)
                 else (f"{average} bpm average" if average else None)]
        lines.append(" · ".join(x for x in third if x))
        window = _range(target)
        if window:
            hit = sum(1 for r in reps if window[0] - REP_SLACK <= r[1] <= window[1] + REP_SLACK)
            lines.append("Every rep on target ✓" if hit == len(reps) else f"{hit} of {len(reps)} reps on target")
    else:
        kind = _KIND_WORDS.get(str(plan.get("ty")), "Run")
        lines = [" · ".join(x for x in (kind, week_words, plan.get("b")) if x)]
        lines.append(f"Planned: {plan.get('n')}")
        lines.append("Ran: " + " · ".join(figures))
        try:
            ran, meant = float(distance[0]), float(plan.get("k"))
            if distance[1].startswith("mi"):
                ran *= 1.609344
            if meant and 0.9 <= ran / meant <= 1.15:
                lines.append("On plan ✓")
        except (TypeError, ValueError, IndexError):
            pass
    return "\n".join(l for l in lines if l) + f"\n\nPlanned by {CAPTION_SITE}"


def link(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return SHARE_PAGE + "#" + base64.urlsafe_b64encode(text).decode("ascii").rstrip("=")


def run_link(details: dict[str, Any], planned: dict[str, Any] | None = None) -> str | None:
    payload = run_payload(details, planned)
    return link(payload) if payload else None


def run_caption(details: dict[str, Any], planned: dict[str, Any] | None = None) -> str | None:
    return caption(run_payload(details, planned) or {})
