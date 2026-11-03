"""One run against the session it was meant to be: planned and run, splits, and a verdict.

For the app's analysis screen. Everything is worked out from the run's laps
and the workout's steps, by rule, so the same run always reads the same way
and nothing calls a model. The rows, the stats and the splits all come from
the same laps: the splits add up to the total time, and the reps' averages
are the averages of the rep rows.

The wording is plain and supportive, and never medical: it says what
happened against the targets, not what it means for anyone's body. A miss is
"soft", drawn in amber, never red.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import stream
from .formatting import drop_empty

# Within this many seconds per km of a pace range still counts as on it: the
# watch's own pace alert is coarser than that.
PACE_GRACE_S = 2.0
# A rep's heart rate this far above the earlier reps' is worth a sentence.
DRIFT_BPM = 4
# A lap this short at the end is the stub a watch adds when it's saved.
STUB_METRES = 50.0
# Kilometre splits within this spread read as even.
EVEN_SPREAD_S = 20

_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve"]


def _word(n: int) -> str:
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


def clock(seconds: float) -> str:
    """42:48, or 1:01:18 past the hour."""
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pace(seconds_per_km: float | None) -> str | None:
    """4:03, a pace per km without its unit."""
    if not seconds_per_km or seconds_per_km <= 0:
        return None
    m, s = divmod(int(round(seconds_per_km)), 60)
    return f"{m}:{s:02d}"


def _lap_seconds(lap: Mapping[str, Any]) -> float:
    for key in ("duration", "movingDuration", "elapsedDuration"):
        if lap.get(key):
            return float(lap[key])
    return 0.0


def _laps(raw: Any) -> list[dict[str, Any]]:
    """Laps as metres, seconds, heart rate and Garmin's mark, the watch's save stub dropped."""
    items = raw.get("lapDTOs") if isinstance(raw, Mapping) else raw
    laps = [{"metres": float(l.get("distance") or 0), "seconds": _lap_seconds(l),
             "hr": float(l["averageHR"]) if l.get("averageHR") else None,
             "mark": str(l.get("intensityType") or "").lower()}
            for l in items or [] if isinstance(l, Mapping)]
    if len(laps) > 1 and laps[-1]["metres"] < STUB_METRES and laps[-1]["seconds"] < 30:
        laps.pop()
    return laps


def _target(step: Mapping[str, Any]) -> dict[str, Any] | None:
    key = ((step.get("targetType") or {}).get("workoutTargetTypeKey") or "").lower()
    one, two = step.get("targetValueOne"), step.get("targetValueTwo")
    if not (one and two):
        return None
    if key == "pace.zone":
        quick, slow = sorted((1000.0 / float(one), 1000.0 / float(two)))
        return {"kind": "pace", "lo": quick, "hi": slow}
    if key == "heart.rate.zone":
        lo, hi = sorted((float(one), float(two)))
        return {"kind": "hr", "lo": lo, "hi": hi}
    return None


def _amount(step: Mapping[str, Any]) -> str | None:
    cond = ((step.get("endCondition") or {}).get("conditionTypeKey") or "").lower()
    value = float(step.get("endConditionValue") or 0)
    if cond == "distance" and value:
        return f"{value / 1000:g} km" if value >= 1000 else f"{value:.0f} m"
    if cond == "time" and value:
        return f"{int(value)} s" if value < 120 else f"{int(value // 60)} min"
    return None


def planned_steps(workout: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The steps the watch ran, repeats unrolled, each with its kind, target and rep number."""
    out: list[dict[str, Any]] = []
    rep = 0

    def walk(steps: Sequence[Mapping[str, Any]], in_repeat: bool) -> None:
        nonlocal rep
        for step in sorted(steps or [], key=lambda s: s.get("stepOrder") or 0):
            if step.get("type") == "RepeatGroupDTO" or step.get("numberOfIterations"):
                for _ in range(int(step.get("numberOfIterations") or step.get("endConditionValue") or 1)):
                    walk(step.get("workoutSteps") or [], True)
                continue
            kind = ((step.get("stepType") or {}).get("stepTypeKey") or "interval").lower()
            if kind == "other":
                kind = "interval"
            number = None
            if kind == "interval" and in_repeat:
                rep += 1
                number = rep
            out.append({"kind": kind, "target": _target(step), "amount": _amount(step), "rep": number})

    for segment in (workout or {}).get("workoutSegments") or []:
        walk(segment.get("workoutSteps") or [], False)
    return out


# What a lap's mark can stand for. Garmin often leaves a rep unmarked
# ("active"), so an unmarked lap fits any step; a marked one only its own kind.
_FITS = {"warmup": {"warmup"}, "cooldown": {"cooldown"}, "recovery": {"recovery", "rest"},
         "rest": {"recovery", "rest"}, "interval": {"interval"}}
_RESTING = ("recovery", "rest")


def _fits(step: Mapping[str, Any], lap: Mapping[str, Any]) -> bool:
    mark = lap.get("mark") or ""
    return mark in ("", "active") or step["kind"] in _FITS.get(mark, {step["kind"]})


def _joined(laps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Several laps as one: their distance and time together, heart rate by time."""
    return {"metres": sum(l["metres"] for l in laps), "seconds": sum(l["seconds"] for l in laps),
            "hr": _weighted_hr(laps), "mark": laps[0].get("mark", "")}


def _paired(steps: list[dict[str, Any]], laps: list[dict[str, Any]]
            ) -> tuple[list[tuple[dict[str, Any], dict[str, Any] | None]], list[dict[str, Any]]] | None:
    """Each step with the lap the watch recorded for it, step by step, or None when they don't line up.

    A structured workout laps once per step. What the watch adds is handled
    as it happens on a real run: running on after the last step makes one
    more lap (Nov 1, 0.46 km after a 4.5 km cool-down), which joins that step
    when it's the same kind; stopping early leaves steps with no lap, which
    say so. A lap whose mark contradicts its step (a lap pressed by hand mid
    rep shifts every one after it) means the two don't line up, and guessing
    would put the wrong numbers against a target, so nothing is paired.
    """
    if not steps or not laps:
        return None
    pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for i, step in enumerate(steps):
        lap = laps[i] if i < len(laps) else None
        if lap is not None and not _fits(step, lap):
            return None
        pairs.append((step, lap))
    after = laps[len(steps):]
    if after and pairs[-1][1] is not None and all(_fits(pairs[-1][0], l) and (l.get("mark") or "") not in ("", "active")
                                                   for l in after):
        last_step, last_lap = pairs[-1]
        pairs[-1] = (last_step, {**_joined([last_lap, *after]), "after": sum(l["metres"] for l in after)})
        after = []
    return pairs, after


def _weighted_hr(laps: Sequence[Mapping[str, Any]]) -> float | None:
    timed = [(l["hr"], l["seconds"]) for l in laps if l.get("hr") and l.get("seconds")]
    total = sum(t for _, t in timed)
    return sum(h * t for h, t in timed) / total if total else None


def _miss(target: Mapping[str, Any] | None, lap: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(the note, which way) when a lap missed its target; (None, None) on it."""
    if not target:
        return None, None
    if target["kind"] == "pace":
        if not lap["metres"]:
            return None, None
        p = lap["seconds"] / lap["metres"] * 1000
        if p > target["hi"] + PACE_GRACE_S:
            return f"{round(p - target['hi'])} s/km slower than range", "slow"
        if p < target["lo"] - PACE_GRACE_S:
            return f"{round(target['lo'] - p)} s/km quicker than range", "quick"
        return None, None
    hr = lap.get("hr")
    if hr and hr > target["hi"] + 1:
        return f"HR {round(hr - target['hi'])} over range", "high"
    return None, None


def _target_text(target: Mapping[str, Any] | None) -> str | None:
    if not target:
        return None
    if target["kind"] == "pace":
        return f"{pace(target['lo'])}–{pace(target['hi'])}"
    return f"{target['lo']:.0f}–{target['hi']:.0f} bpm"


_LABELS = {"warmup": "Warm up", "cooldown": "Cool down", "interval": "Run"}


def _rows(pairs: list[tuple[dict[str, Any], dict[str, Any] | None]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Planned and run, one row per step that isn't a recovery, then anything run after the last step."""
    body = [s for s, _ in pairs if s["kind"] == "interval" and not s["rep"]]
    rows = []
    for step, lap in pairs:
        if step["kind"] in _RESTING:
            continue
        if step["rep"]:
            label = f"Rep {step['rep']}"
        elif step["kind"] == "interval" and len(body) > 1:
            label = f"Part {body.index(step) + 1}"
        else:
            label = _LABELS.get(step["kind"], "Run")
        if lap is None:
            rows.append(drop_empty({"label": label, "kind": step["kind"], "rep": step["rep"], "note": "Not run",
                                    "target": _target_text(step["target"])}))
            continue
        note, way = _miss(step["target"], lap)
        planned = step["amount"] or f"{lap['metres'] / 1000:.1f} km"
        if lap.get("after"):
            planned += f", then {lap['after'] / 1000:.1f} km more"
        rows.append(drop_empty({
            "label": label,
            "kind": step["kind"],
            "rep": step["rep"],
            "note": note or planned,
            "soft": bool(note) or None,
            "miss": way,
            "target": _target_text(step["target"]),
            "pace": pace(lap["seconds"] / lap["metres"] * 1000) if lap["metres"] else None,
            "hr": round(lap["hr"]) if lap.get("hr") else None,
            "_lap": lap,
            "_target": step["target"],
        }))
    if after:
        lap = _joined(after)
        rows.append(drop_empty({
            "label": "After", "kind": "after", "note": f"{lap['metres'] / 1000:.1f} km after the workout",
            "pace": pace(lap["seconds"] / lap["metres"] * 1000) if lap["metres"] else None,
            "hr": round(lap["hr"]) if lap.get("hr") else None,
        }))
    return rows


def _from_recording(columns: Mapping[str, Any] | None, laps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]] | None:
    """The running between the recoveries, sample by sample, from the watch's recording.

    Pieces are scaled to the laps' own running distance and time, so splits
    made from them add up to the same totals as the laps. None when there is
    no recording or it doesn't cover the run.
    """
    distance = (columns or {}).get("sumDistance")
    seconds = (columns or {}).get("sumDuration") or (columns or {}).get("sumElapsedDuration")
    heart = (columns or {}).get("directHeartRate") or []
    if not distance or not seconds or not laps:
        return None
    bounds, at = [], 0.0
    for lap in laps:
        bounds.append((at, at + lap["metres"], lap["mark"]))
        at += lap["metres"]
    pieces, previous = [], None
    for i, (d, t) in enumerate(zip(distance, seconds)):
        if d is None or t is None:
            continue
        if previous is None:
            previous = (d, t)
            continue
        dd, dt = d - previous[0], t - previous[1]
        middle = (d + previous[0]) / 2
        previous = (d, t)
        if dd <= 0 or dt <= 0:
            continue
        mark = next((m for a, b, m in bounds if a <= middle < b), bounds[-1][2])
        if mark in _RESTING:
            continue
        pieces.append({"metres": dd, "seconds": dt, "hr": heart[i] if i < len(heart) else None, "mark": mark})
    want_m = sum(l["metres"] for l in laps if l["mark"] not in _RESTING)
    want_s = sum(l["seconds"] for l in laps if l["mark"] not in _RESTING)
    got_m, got_s = sum(p["metres"] for p in pieces), sum(p["seconds"] for p in pieces)
    if not pieces or not got_s or not want_m or abs(got_m - want_m) > 0.1 * want_m:
        return None
    for piece in pieces:
        piece["metres"] *= want_m / got_m
        piece["seconds"] *= want_s / got_s
    return pieces


def splits(laps: Sequence[Mapping[str, Any]], columns: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Kilometre splits, with the recoveries as one row of their own.

    From the watch's recording where there is one, so each kilometre is its
    own pace. Without one, pace is taken as even within each lap, which reads
    a long warm-up as five identical kilometres. Times are whole seconds
    that add up exactly: each split is the difference of rounded running
    totals, so rounding never drifts the sum.
    """
    running = _from_recording(columns, laps) or [l for l in laps if l["mark"] not in _RESTING]
    resting = [l for l in laps if l["mark"] in _RESTING]
    out: list[dict[str, Any]] = []
    total_m = sum(l["metres"] for l in running)
    if total_m <= 0:
        return out
    marks: list[float] = []
    mark, walked, clock_s = 1000.0, 0.0, 0.0
    beats: list[list[tuple[float, float]]] = [[]]
    for lap in running:
        start_m, start_t = walked, clock_s
        speed = lap["metres"] / lap["seconds"] if lap["seconds"] else 0
        walked += lap["metres"]
        clock_s += lap["seconds"]
        cursor_t = start_t
        while speed and walked >= mark - 0.01 and mark <= total_m + 0.01:
            at = start_t + (mark - start_m) / speed
            beats[-1].append((lap.get("hr") or 0, at - cursor_t))
            marks.append(at)
            beats.append([])
            cursor_t = at
            mark += 1000.0
        beats[-1].append((lap.get("hr") or 0, clock_s - cursor_t))
    ends = list(marks)
    if total_m - len(marks) * 1000.0 > 1:
        ends.append(clock_s)
    elif ends:
        # A last kilometre a metre or so short still ends where the run did.
        ends[-1] = clock_s
    previous = 0
    for i, end in enumerate(ends):
        this = int(round(end)) - previous
        previous = int(round(end))
        weighted = [(h, t) for h, t in beats[i] if h and t > 0]
        hr = sum(h * t for h, t in weighted) / sum(t for _, t in weighted) if weighted else None
        partial = i == len(marks)
        metres = total_m - len(marks) * 1000.0 if partial else 1000.0
        out.append(drop_empty({
            "km": f"{metres / 1000:.1f}" if partial else str(i + 1),
            "time": clock(this),
            "seconds": this,
            "pace": pace(this / metres * 1000),
            "hr": round(hr) if hr else None,
            "partial": partial or None,
        }))
    if resting:
        secs = int(round(sum(l["seconds"] for l in laps))) - previous
        metres = sum(l["metres"] for l in resting)
        out.append(drop_empty({
            "km": "Recoveries",
            "time": clock(secs),
            "seconds": secs,
            "pace": f"{clock(secs)} for {metres / 1000:.1f} km",
            "hr": round(_weighted_hr(resting)) if _weighted_hr(resting) else None,
            "rest": True,
        }))
    return out


def _verdict(rows: list[dict[str, Any]], laps: list[dict[str, Any]], kind: str | None,
             split_rows: list[dict[str, Any]]) -> str:
    """One or two plain sentences on how the run went against its targets."""
    reps = [r for r in rows if r.get("rep") and r.get("_target")]
    if len(reps) >= 2:
        on = [r for r in reps if not r.get("soft")]
        n = len(reps)
        if len(on) == n:
            first = f"Hit {'both' if n == 2 else 'all ' + _word(n)} reps on target."
        elif not on:
            ways = {r.get("miss") for r in reps}
            way = "quicker" if ways == {"quick"} else "slower" if ways == {"slow"} else None
            first = f"The reps ran {way} than the target range." if way else "The reps landed outside the target range."
        else:
            first = f"{_word(len(on)).capitalize()} of {_word(n)} reps on target."
        hrs = [r["hr"] for r in reps if r.get("hr")]
        if len(hrs) >= 2:
            before = sum(hrs[:-1]) / len(hrs[:-1])
            if hrs[-1] - before >= DRIFT_BPM:
                return f"{first} The last one drifted {round(hrs[-1] - before)} bpm higher."
            return f"{first} Heart rate held steady across them."
        return first
    targeted = [r for r in rows if r.get("_target") and r["kind"] == "interval"]
    if len(targeted) >= 2:
        on = sum(not r.get("soft") for r in targeted)
        n = len(targeted)
        if on == n:
            return f"{'Both' if n == 2 else 'All ' + _word(n)} parts on target."
        return f"{_word(on).capitalize()} of {_word(n)} parts on target."
    total_m = sum(l["metres"] for l in laps)
    total_s = sum(l["seconds"] for l in laps)
    whole = [s["seconds"] for s in split_rows if not s.get("partial") and not s.get("rest")]
    even = len(whole) >= 3 and max(whole) - min(whole) <= EVEN_SPREAD_S
    hr = _weighted_hr(laps)
    if len(targeted) == 1:
        row = targeted[0]
        if not row.get("soft"):
            head = "Easy and even." if kind in ("easy", "recovery") and even else (
                "On target and even from start to finish." if even else "On target overall.")
        elif row.get("miss") == "quick":
            head = f"Quicker than planned, by about {row['note'].split(' ')[0]} s/km."
            if kind in ("easy", "recovery"):
                head += " Easy days do their job best kept easy."
            return head
        elif row.get("miss") == "slow":
            return f"A little slower than planned, by about {row['note'].split(' ')[0]} s/km."
        else:
            return f"On pace, with heart rate {row['note'].replace('HR ', '')}."
        return f"{head} Heart rate averaged {round(hr)}." if hr else head
    if not total_m:
        return f"{clock(total_s)} of training." if total_s else "Recorded."
    line = f"{total_m / 1000:.1f} km at {pace(total_s / total_m * 1000)} /km"
    return f"{line}, heart rate averaging {round(hr)}." if hr else f"{line}."


def _short(rows: list[dict[str, Any]], total_m: float, total_s: float) -> str | None:
    """The verdict in a few words, for the Done today row."""
    reps = [r for r in rows if r.get("rep") and r.get("_target")]
    if len(reps) >= 2:
        on, n = sum(not r.get("soft") for r in reps), len(reps)
        return (f"{'both' if n == 2 else 'all ' + _word(n)} reps on target" if on == n
                else f"{_word(on)} of {_word(n)} reps on target")
    if total_m:
        return f"{total_m / 1000:.1f} km at {pace(total_s / total_m * 1000)} /km"
    return clock(total_s) if total_s else None


def analyse(workout: Mapping[str, Any] | None, raw_laps: Any, kind: str | None = None,
            recording: Any = None) -> dict[str, Any]:
    """The analysis screen's numbers for one run, against its workout when it has one."""
    laps = _laps(raw_laps)
    total_m = sum(l["metres"] for l in laps)
    total_s = sum(l["seconds"] for l in laps)
    paired = _paired(planned_steps(workout), laps) if workout else None
    rows = _rows(*paired) if paired else []
    split_rows = splits(laps, stream.parse_stream(recording) if recording else None)
    reps = [r["_lap"] for r in rows if r.get("rep") and r.get("_lap")]
    if len(reps) >= 2:
        rep_m = sum(l["metres"] for l in reps)
        rep_s = sum(l["seconds"] for l in reps)
        rep_hr = _weighted_hr(reps)
        stats = [{"v": f"{total_m / 1000:.1f}", "l": "km"}, {"v": clock(total_s), "l": "time"},
                 {"v": pace(rep_s / rep_m * 1000), "l": "/km reps"},
                 {"v": str(round(rep_hr)) if rep_hr else "–", "l": "bpm reps"}]
    else:
        hr = _weighted_hr(laps)
        stats = [{"v": f"{total_m / 1000:.1f}", "l": "km"}, {"v": clock(total_s), "l": "time"},
                 {"v": pace(total_s / total_m * 1000) if total_m else "–", "l": "/km avg"},
                 {"v": str(round(hr)) if hr else "–", "l": "bpm avg"}]
    verdict = _verdict(rows, laps, kind, split_rows)
    short = _short(rows, total_m, total_s)
    for r in rows:
        r.pop("_lap", None)
        r.pop("_target", None)
    return drop_empty({
        "verdict": verdict,
        "short": short,
        "stats": stats,
        "planned_and_run": rows or None,
        "matched": bool(paired) or None,
        "splits": split_rows or None,
        "km": round(total_m / 1000, 1),
        "seconds": int(round(total_s)),
    })
