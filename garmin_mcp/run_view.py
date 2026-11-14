"""A run in the app, after it's done: the session as planned with what was run
inside it (the chart), and a few plain lines on what it shows (Nov 11).

Before a run the app draws the session's shape; after it, until now, only a
table. The chart keeps that shape and fills it: each rep inside its target
band, or for an easy run each km with heart rate against its target. The lines
(Nov 14) say how the run compares: with the last session of the same kind, on
its reps or its quicker stretch, and its easy running with the easy runs of a
month before. A line with nothing real behind it isn't written.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .analysis import PACE_GRACE_S, _RESTING, _stride_steps, _weighted_hr, pace

WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")


def _word(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def _list(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _secs(lap: Mapping[str, Any] | None) -> float | None:
    """A lap's pace, seconds per km."""
    if not lap or not lap.get("metres") or not lap.get("seconds"):
        return None
    return lap["seconds"] / lap["metres"] * 1000


def _on(target: Mapping[str, Any], p: float) -> bool:
    return target["lo"] - PACE_GRACE_S <= p <= target["hi"] + PACE_GRACE_S


def _clock_secs(text: Any) -> int | None:
    """ "4:58" as 298."""
    try:
        m, sec = str(text).split(":")
        return int(m) * 60 + int(sec)
    except (ValueError, AttributeError):
        return None


def _km_rows(split_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The run's km splits as numbers: (label, pace s/km, hr), the strides and recoveries left out."""
    out = []
    for s in split_rows or []:
        if s.get("strides") or s.get("rest") or not s.get("seconds"):
            continue
        if s.get("partial") and float(s["km"]) < 0.2:
            continue  # a few metres at the end say nothing about pace
        # The split's own pace: its km label is rounded (0.93 km reads "0.9").
        p = _clock_secs(s.get("pace")) or s["seconds"]
        out.append({"km": s["km"], "p": p, "hr": s.get("hr"), "s": s["seconds"]})
    return out


# A pace part this much quicker than the slowest paced part is the quicker stretch.
QUICK_GAP_S = 5


def quick_parts(parts: Sequence[tuple[str, bool, Mapping[str, Any] | None]]) -> list[bool]:
    """Which of a run's parts were its quicker stretch, from (kind, rep, target) for each step.

    A long run of 16 km by heart rate and 3 km at half-marathon pace has two
    "interval" steps and no reps: the 3 km is the quicker one (Nov 14: the run
    table coloured it as the steady part, and the share image called both
    "reps"). A paced step is quick when the run's other parts are set by heart
    rate or nothing, or when it's clearly quicker than the slowest paced one.
    """
    body = [i for i, (kind, rep, _) in enumerate(parts) if kind == "interval" and not rep]
    out = [False] * len(parts)
    if len(body) < 2:
        return out
    paced = [i for i in body if (parts[i][2] or {}).get("kind") == "pace"]
    steady = [i for i in body if (parts[i][2] or {}).get("kind") != "pace"]
    if not paced:
        return out
    slowest = max((parts[i][2]["lo"] + parts[i][2]["hi"]) / 2 for i in paced)
    for i in paced:
        if steady or (parts[i][2]["lo"] + parts[i][2]["hi"]) / 2 < slowest - QUICK_GAP_S:
            out[i] = True
    return out


def _quick_pairs(pairs) -> list[bool]:
    return quick_parts([(s.get("kind") or "", bool(s.get("rep")), s.get("target")) for s, _ in pairs or []])


def _work(pairs: Sequence[tuple[dict[str, Any], dict[str, Any] | None]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The session's reps that were run, strides apart."""
    strides = _stride_steps(pairs)
    return [(s, l) for s, l in pairs if s.get("rep") and s not in strides and l is not None and _secs(l)]


def _shared_target(work: Sequence[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any] | None:
    """The reps' pace target, when every rep has the same one."""
    targets = [s.get("target") for s, _ in work]
    if not targets or any(not t or t.get("kind") != "pace" for t in targets):
        return None
    first = targets[0]
    if any(abs(t["lo"] - first["lo"]) > 1 or abs(t["hi"] - first["hi"]) > 1 for t in targets):
        return None
    return first


def _main_hr_target(pairs) -> dict[str, Any] | None:
    """An easy session's heart-rate target: the target of its longest running step, when that's heart rate."""
    span = _main_hr_span(pairs)
    return span[0] if span else None


def _main_hr_span(pairs) -> tuple[dict[str, Any], float, float] | None:
    """That target, with the km of the run its step covered (from, to).

    Nov 14: a long run's 16 km set by heart rate, then 3 km at half-marathon
    pace, read as "heart rate went over 148 in km 17, 18, 19": the 148 was
    only ever the first 16 km's.
    """
    body = [(s, l) for s, l in pairs or [] if s["kind"] in ("interval", "warmup", "cooldown") and not s.get("rep") and l]
    if not body:
        return None
    step, lap = max(body, key=lambda p: p[1]["seconds"])
    target = step.get("target")
    if not target or target.get("kind") != "hr":
        return None
    start = 0.0
    for s, l in pairs:
        if s is step:
            break
        start += (l or {}).get("metres") or 0
    return target, start / 1000, (start + (lap.get("metres") or 0)) / 1000


def _within(kms, span: tuple[Any, float, float] | None):
    """The km rows inside the step's stretch, by where each km's middle falls."""
    if not span:
        return kms
    _, start, end = span
    out, done = [], 0.0
    for k in kms:
        try:
            length = min(1.0, float(k["km"]))
        except (TypeError, ValueError):
            length = 1.0
        if start - 0.05 <= done + length / 2 <= end + 0.05:
            out.append(k)
        done += length
    return out


# -- B: the chart -----------------------------------------------------------------

def chart(pairs, after: Sequence[Mapping[str, Any]], laps: Sequence[Mapping[str, Any]],
          split_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """What the chart draws: the session step by step with its reps against their
    target ("steps"), or the run km by km with heart rate against an easy
    session's target ("km"). None when there's nothing to draw."""
    work = _work(pairs) if pairs else []
    if len(work) >= 2:
        return _steps_chart(pairs, after, work)
    kms = _km_rows(split_rows)
    if len(kms) < 2:
        return None
    strides = [l for l in laps if l.get("stride")]
    target = _main_hr_target(pairs)
    parts = [{"k": "km", "s": round(k["s"]), "p": round(k["p"]), "hr": k["hr"], "label": k["km"]} for k in kms]
    planned_strides = len(_stride_steps(pairs)) if pairs else 0
    parts += [{"k": "stride", "s": round(l["seconds"]), "p": round(_secs(l)), "hr": round(l["hr"]) if l.get("hr") else None}
              for l in strides if _secs(l)]
    out: dict[str, Any] = {"mode": "km", "parts": parts}
    if planned_strides > len(strides):
        out["missing_strides"] = planned_strides - len(strides)
    if target:
        out["band"] = {"kind": "hr", "lo": round(target["lo"]), "hi": round(target["hi"]),
                       "text": f"{target['lo']:.0f}–{target['hi']:.0f}"}
        out["caption"] = _hr_caption(_within(kms, _main_hr_span(pairs)), target, len(strides), planned_strides)
    return out


def _steps_chart(pairs, after, work) -> dict[str, Any]:
    strides = _stride_steps(pairs)
    target = _shared_target(work)
    parts = []
    for step, lap in pairs:
        if lap is None or not lap.get("seconds"):
            continue
        p = _secs(lap)
        if step in strides:
            k = "stride"
        elif step.get("rep"):
            k = "work"
        elif step["kind"] in _RESTING:
            k = "rec"
        else:
            k = "easy"
        label = None
        if k == "work":
            label = f"Rep {step['rep']}"
        elif k == "easy" and lap["seconds"] >= 240:
            label = {"warmup": "Warm-up", "cooldown": "Cool-down"}.get(step["kind"], "Run")
        parts.append({"k": k, "s": round(lap["seconds"]), "p": round(p) if p else None,
                      "hr": round(lap["hr"]) if lap.get("hr") else None, "label": label,
                      "on": _on(step["target"], p) if k == "work" and p and (step.get("target") or {}).get("kind") == "pace" else None})
    for lap in after or []:
        if lap.get("seconds") and _secs(lap):
            parts.append({"k": "easy", "s": round(lap["seconds"]), "p": round(_secs(lap)),
                          "hr": round(lap["hr"]) if lap.get("hr") else None, "label": None})
    out: dict[str, Any] = {"mode": "steps", "parts": parts}
    paces = [pace(_secs(l)) for _, l in work]
    if target:
        out["band"] = {"kind": "pace", "lo": round(target["lo"]), "hi": round(target["hi"]),
                       "text": f"{pace(target['lo'])}–{pace(target['hi'])}"}
        inside = sum(1 for _, l in work if _on(target, _secs(l)))
        n = len(work)
        lead = (f"All {_word(n)} reps inside the target" if inside == n else
                f"{_word(inside).capitalize()} of {_word(n)} reps inside the target")
        out["caption"] = f"{lead}: {_list(paces)}."
    return out


def _hr_caption(kms, target, strides_run: int, strides_planned: int) -> str | None:
    hrs = [(k["km"], k["hr"]) for k in kms if k.get("hr")]
    if not hrs:
        return None
    hi = target["hi"]
    over = [km for km, h in hrs if h > hi + 1]
    if over:
        line = f"Heart rate went over {hi:.0f} in km {_list(over)}."
    else:
        start = next((i for i in range(len(hrs)) if all(h >= target["lo"] - 1 for _, h in hrs[i:])), None)
        if start == 0:
            line = "Heart rate in the target the whole way."
        elif start is not None:
            line = f"Heart rate in the target from km {hrs[start][0]} on."
        else:
            line = f"Heart rate stayed under {hi:.0f} the whole way."
    if strides_planned and strides_run >= strides_planned:
        line += f" All {_word(strides_planned)} strides done."
    return line


# -- C: what it shows -------------------------------------------------------------

# Nov 14: the lines that said what the table already showed ("Pace varied",
# "Even pacing", heart rate rep by rep) are gone. What it shows now is how this
# run compares: with the last session like it, and its easy running with a
# month ago. A line is only written when there's something real to compare.


def insights(pairs, laps: Sequence[Mapping[str, Any]], split_rows: Sequence[Mapping[str, Any]],
             kind: str | None = None) -> list[dict[str, Any]]:
    """Nothing from the run on its own: the comparisons are added where the earlier runs are read."""
    return []


# -- C: against the last one --------------------------------------------------------

def _sum(laps: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    metres = sum(l.get("metres") or 0 for l in laps)
    if not metres:
        return None
    hr = _weighted_hr(list(laps))
    return {"km": round(metres / 1000, 1), "p": sum(l["seconds"] for l in laps) / metres * 1000,
            "hr": round(hr) if hr else None}


def figures(pairs, laps: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """What a run is compared on: its reps (pace, heart rate, km of reps) when it
    had them; a long run's quicker stretch ("part") when it had one; and its
    steady running ("steady"), for its easy pace against earlier weeks."""
    work = _work(pairs) if pairs else []
    if len(work) >= 2:
        metres = sum(l["metres"] for _, l in work)
        secs = sum(l["seconds"] for _, l in work)
        hr = _weighted_hr([l for _, l in work])
        return {"reps": len(work), "km": round(metres / 1000, 1), "p": secs / metres * 1000, "hr": round(hr) if hr else None}
    quick = _quick_pairs(pairs)
    if any(quick):
        fast = [l for (s, l), q in zip(pairs, quick) if q and l and l.get("metres")]
        calm = [l for (s, l), q in zip(pairs, quick) if not q and l and l.get("metres") and s["kind"] == "interval"]
        part, steady = _sum(fast), _sum(calm)
        if not part:
            return None
        return {**part, "part": True, **({"steady": steady} if steady and steady["km"] >= 4 else {})}
    running = [l for l in laps if l["mark"] not in _RESTING and not l.get("stride") and l.get("metres")]
    whole = _sum(running)
    if not whole or whole["km"] < 1:
        return None
    return {**whole, "steady": whole} if whole["km"] >= 4 else whole


def last_time(this: Mapping[str, Any] | None, before: Mapping[str, Any] | None, what: str, name: str | None,
              day: str) -> dict[str, Any] | None:
    """This session against the last one like it, on what they were built round:
    the reps, or a long run's quicker stretch. Whole runs aren't compared: a
    route, a hill or a warm day moves a whole run's pace more than fitness does."""
    if not this or not before:
        return None
    if this.get("reps") and before.get("reps"):
        on = " of reps"
    elif this.get("part") and before.get("part"):
        on = " at the quicker pace"
    else:
        return None

    def said(f: Mapping[str, Any]) -> str:
        hr = f" at {f['hr']} bpm" if f.get("hr") else ""
        return f"{pace(f['p'])}{hr} over {f['km']:g} km{on}"

    quicker, slower = this["p"] < before["p"] - 2, this["p"] > before["p"] + 2
    lower = bool(this.get("hr") and before.get("hr") and this["hr"] < before["hr"] - 1)
    higher = bool(this.get("hr") and before.get("hr") and this["hr"] > before["hr"] + 1)
    tail = (": quicker, for a lower heart rate" if quicker and lower else ": quicker, at the same heart rate" if quicker and not higher
            else ": the same pace, for a lower heart rate" if lower and not slower else ": quicker" if quicker else "")
    detail = f" ({name}, {day})" if name else f" ({day})"
    return {"icon": "↺", "tone": "muted", "head": f"Against your last {what}{detail}.",
            "text": f"Then {said(before)}. Today {said(this)}{tail}."}


# -- C: easy running, against a month ago -----------------------------------------

# The weeks easy running is compared with: 3 to 7 weeks before this run.
TREND_FROM_DAYS, TREND_TO_DAYS = 49, 21
# At least this many easy runs then, each at least this long, to say anything.
TREND_MIN_RUNS, TREND_MIN_KM = 3, 4
# Garmin's own reading of a run as easy: what it trained.
EASY_LABELS = ("RECOVERY", "AEROBIC_BASE", "BASE")
# Without a label: easy when under this share of the run was in zone 3 or above.
EASY_ZONE_SHARE = 0.1


def easy_then(activities: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Easy runs from Garmin's activity list, as one pace and heart rate: runs
    Garmin read as base or recovery training (or, without its label, run mostly
    under heart-rate zone 3), with a heart rate, of 4 km or more."""
    runs = []
    for a in activities or []:
        key = str(((a.get("activityType") or {}).get("typeKey")) or "").lower()
        label = str(a.get("trainingEffectLabel") or "").upper()
        metres, secs, hr = float(a.get("distance") or 0), float(a.get("movingDuration") or a.get("duration") or 0), a.get("averageHR")
        zones = [a.get(f"hrTimeInZone_{n}") for n in range(1, 6)]
        timed = all(isinstance(z, (int, float)) for z in zones) and sum(zones) > 0
        # Garmin's label when it gives one; else time in heart-rate zones, mostly under zone 3.
        easy = label in EASY_LABELS if label else (timed and sum(zones[2:]) / sum(zones) < EASY_ZONE_SHARE)
        if "run" not in key or "trail" in key or "treadmill" in key or not easy:
            continue
        if metres < TREND_MIN_KM * 1000 or not secs or not hr:
            continue
        runs.append((metres, secs, float(hr), str(a.get("startTimeLocal") or "")[:10]))
    if len(runs) < TREND_MIN_RUNS:
        return None
    metres = sum(r[0] for r in runs)
    secs = sum(r[1] for r in runs)
    return {"runs": len(runs), "km": round(metres / 1000), "p": secs / metres * 1000,
            "hr": round(sum(r[2] * r[1] for r in runs) / secs), "first": min(r[3] for r in runs), "last": max(r[3] for r in runs)}


def easy_trend(steady: Mapping[str, Any] | None, then: Mapping[str, Any] | None, long_run: bool = False) -> dict[str, Any] | None:
    """Today's easy running against the easy runs of 3 to 7 weeks ago, in one line,
    only when it moved clearly one way: quicker for the same heart rate or less,
    or the same pace for a lower one (or the reverse). Mixed or level says nothing."""
    if not steady or not then or not steady.get("hr") or steady.get("km", 0) < TREND_MIN_KM:
        return None
    dp, dh = steady["p"] - then["p"], steady["hr"] - then["hr"]
    better = (dp <= -5 and dh <= 1) or (dh <= -3 and dp <= 3)
    worse = (dp >= 5 and dh >= -1) or (dh >= 3 and dp >= -3)
    if better == worse:
        return None
    if better:
        tail = (f"{abs(round(dp))} s/km quicker, for a lower heart rate" if dp <= -5 and dh <= -2
                else f"{abs(round(dp))} s/km quicker, at the same heart rate" if dp <= -5
                else f"the same pace, {abs(dh)} bpm lower")
        head = "Easy running is getting quicker." if dp <= -5 else "Easy running is costing less."
    else:
        tail = (f"{round(dp)} s/km slower, for a higher heart rate" if dp >= 5 and dh >= 2
                else f"{round(dp)} s/km slower, at the same heart rate" if dp >= 5
                else f"the same pace, {dh} bpm higher")
        head = "Easy running is costing more than a month ago."
    what = f"Your steady {steady['km']:g} km" if long_run else f"Today's {steady['km']:g} km"
    return {"icon": "↗" if better else "↘", "tone": "easy" if better else "muted", "head": head,
            "text": f"{what}: {pace(steady['p'])} at {steady['hr']} bpm. Your {then['runs']} easy runs 3 to 7 weeks ago "
                    f"averaged {pace(then['p'])} at {then['hr']} bpm: {tail}."}
