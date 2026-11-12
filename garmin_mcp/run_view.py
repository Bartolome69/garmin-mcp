"""A run in the app, after it's done: the session as planned with what was run
inside it (the chart), and a few plain lines on what it shows (Nov 11).

Before a run the app draws the session's shape; after it, until now, only a
table. The chart keeps that shape and fills it: each rep inside its target
band, or for an easy run each km with heart rate against its target. The lines
say what the numbers mean: how even the reps were, how heart rate moved, how
far the recoveries brought it down, and how it compares with the last session
of the same kind. Every line is worked out from the run's own laps and splits;
a line with nothing behind it isn't written.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .analysis import PACE_GRACE_S, _RESTING, _stride_steps, _weighted_hr, pace

ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
            "eleventh", "twelfth")
WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")
# Within this many seconds per km of each other, reps (or km) read as even.
EVEN_S = 5
# A first km this much slower than the rest is the warm-up into the run.
SETTLING_S = 8
# Km splits of a steady run this close are even: crossings, bends and GPS
# move a km by more than a lap of reps (Nov 12: 5:36-5:49 read as "varied").
KM_EVEN_S = 15


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
    body = [(s, l) for s, l in pairs or [] if s["kind"] in ("interval", "warmup", "cooldown") and not s.get("rep") and l]
    if not body:
        return None
    step, _ = max(body, key=lambda p: p[1]["seconds"])
    target = step.get("target")
    return target if target and target.get("kind") == "hr" else None


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
        out["caption"] = _hr_caption(kms, target, len(strides), planned_strides)
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

# Sessions whose story is their reps: km by km they read as noise.
HARD = {"threshold", "sub", "intervals", "race", "tempo"}


def insights(pairs, laps: Sequence[Mapping[str, Any]], split_rows: Sequence[Mapping[str, Any]],
             kind: str | None = None) -> list[dict[str, Any]]:
    """A few lines on what the run shows, each worked out from its laps and splits."""
    work = _work(pairs) if pairs else []
    if len(work) >= 2:
        lines = [_pacing(work), _rep_hr(work), _recoveries(pairs)]
    elif kind in HARD:
        # A hard session whose laps didn't line up with its workout: its km
        # splits mix reps and jogs, so there's nothing honest to say about them.
        lines = []
    else:
        target = _main_hr_target(pairs)
        kms = _km_rows(split_rows)
        lines = [_easy_hr(kms, target) if target else None, _km_pacing(kms)]
    lines.append(_strides(laps))
    return [x for x in lines if x]


def _pacing(work) -> dict[str, Any] | None:
    paces = [_secs(l) for _, l in work]
    spread = round(max(paces)) - round(min(paces))
    target = _shared_target(work)
    shown = _list([pace(p) for p in paces]) if len(paces) <= 6 else f"{pace(min(paces))} to {pace(max(paces))}"
    if target:
        off = [s["rep"] for s, l in work if not _on(target, _secs(l))]
        if off:
            which = _list([str(r) for r in off])
            return {"icon": "!", "tone": "hard", "head": "Off target.",
                    "text": f"{shown}. Rep{'s' if len(off) > 1 else ''} {which} missed {pace(target['lo'])}–{pace(target['hi'])}."}
    if spread <= EVEN_S:
        tail = " of each other" + (", inside the target." if target else ".")
        within = "within 1 s" if spread <= 1 else f"within {spread} s"
        return {"icon": "✓", "tone": "easy", "head": "Even pacing.",
                "text": f"{shown}: all {_word(len(paces))} {within}{tail}"}
    quicker = paces[-1] < paces[0] - EVEN_S
    slower = paces[-1] > paces[0] + EVEN_S
    head = "Got quicker." if quicker else "Faded." if slower else "Pace varied."
    return {"icon": "→", "tone": "long", "head": head, "text": f"{shown}."}


def _rep_hr(work) -> dict[str, Any] | None:
    hrs = [round(l["hr"]) for _, l in work if l.get("hr")]
    if len(hrs) < 2 or len(hrs) != len(work):
        return None
    shown = _list([str(h) for h in hrs]) if len(hrs) <= 6 else f"{hrs[0]} on the first, {hrs[-1]} on the last"
    later = hrs[1:]
    if max(later) - min(later) <= 3 and hrs[1] > hrs[0]:
        head, tail = "Heart rate settled.", " It levelled off after the first rep rather than climbing rep on rep."
    elif hrs[-1] - hrs[0] >= 8:
        head, tail = "Heart rate climbed.", f" Up {hrs[-1] - hrs[0]} bpm from the first rep to the last."
    else:
        head, tail = "Heart rate steady.", ""
    return {"icon": "♥", "tone": "hard", "head": head, "text": f"On the reps: {shown}.{tail}"}


def _recoveries(pairs) -> dict[str, Any] | None:
    """Heart rate in the recoveries between reps, against the reps either side."""
    recs = []
    for i, (step, lap) in enumerate(pairs):
        if step["kind"] not in _RESTING or not lap or not lap.get("hr"):
            continue
        before = next((l for s, l in reversed(pairs[:i]) if s.get("rep") and l), None)
        later = next((l for s, l in pairs[i + 1:] if s.get("rep") and l), None)
        if before and later:  # only the recoveries between two reps
            recs.append(lap)
    if not recs:
        return None
    hrs = sorted(round(l["hr"]) for l in recs)
    work_hr = [round(l["hr"]) for s, l in _work(pairs) if l.get("hr")]
    secs = round(sum(l["seconds"] for l in recs) / len(recs))
    length = f"{secs} s" if secs < 120 else f"{secs // 60} min" + (f" {secs % 60} s" if secs % 60 else "")
    span = f"{hrs[0]}" if hrs[0] == hrs[-1] else f"{hrs[0]}–{hrs[-1]}"
    versus = f", against {min(work_hr)}–{max(work_hr)} on the reps" if work_hr and min(work_hr) != max(work_hr) else (
        f", against {work_hr[0]} on the reps" if work_hr else "")
    return {"icon": "↓", "tone": "long", "head": "Recoveries.",
            "text": f"In the {length} recoveries heart rate averaged {span}{versus}."}


def _easy_hr(kms, target) -> dict[str, Any] | None:
    hrs = [(k["km"], k["hr"]) for k in kms if k.get("hr")]
    if len(hrs) < 2:
        return None
    lo, hi = target["lo"], target["hi"]
    peak = max(h for _, h in hrs)
    band = f"{lo:.0f}–{hi:.0f}"
    over = [km for km, h in hrs if h > hi + 1]
    if over:
        return {"icon": "!", "tone": "hard", "head": "Harder than planned.",
                "text": f"Heart rate went over {hi:.0f} in km {_list(over)}, peaking at {peak}."}
    start = next((i for i in range(len(hrs)) if all(h >= lo - 1 for _, h in hrs[i:])), None)
    if start == 0:
        text = f"Heart rate stayed in {band} the whole way, peaking at {peak}."
    elif start is not None:
        warm = _list([str(h) for _, h in hrs[:start]])
        text = (f"Heart rate was in {band} from km {hrs[start][0]} on, peaking at {peak}. "
                f"Km {_list([km for km, _ in hrs[:start]])} {'was' if start == 1 else 'were'} {warm} while you warmed up.")
    else:
        text = f"Heart rate stayed under {hi:.0f} the whole way, peaking at {peak}."
    return {"icon": "✓", "tone": "easy", "head": "Easy stayed easy.", "text": text}


def _km_pacing(kms) -> dict[str, Any] | None:
    if len(kms) < 3:
        return None
    paces = [k["p"] for k in kms]
    lead = 0
    while lead < min(2, len(paces) - 2):
        rest = sorted(paces[lead + 1:])
        if paces[lead] > rest[len(rest) // 2] + SETTLING_S:
            lead += 1
        else:
            break
    rest = paces[lead:]
    lo, hi = pace(min(rest)), pace(max(rest))
    span = lo if lo == hi else f"{lo}–{hi}"
    if lead and round(max(rest)) - round(min(rest)) <= KM_EVEN_S:
        first = _list([pace(p) for p in paces[:lead]])
        which = "the first km" if lead == 1 else f"the first {_word(lead)} km"
        return {"icon": "→", "tone": "long", "head": "Settled pace.", "text": f"{first} for {which}, then {span} for the rest."}
    if round(max(rest)) - round(min(rest)) <= KM_EVEN_S:
        return {"icon": "→", "tone": "long", "head": "Even pace.", "text": f"Every km between {lo} and {hi}." if lo != hi else f"Every km at {lo}."}
    quickest, slowest = pace(min(paces)), pace(max(paces))
    if paces[-1] < paces[0] - EVEN_S * 2 and min(paces[-2:]) == min(paces):
        return {"icon": "→", "tone": "long", "head": "Got quicker.",
                "text": f"{pace(paces[0])} for the first km, {pace(paces[-1])} for the last."}
    return {"icon": "→", "tone": "long", "head": "Pace varied.", "text": f"Km splits between {quickest} and {slowest}."}


def _strides(laps) -> dict[str, Any] | None:
    run = [l for l in laps if l.get("stride") and _secs(l)]
    if len(run) < 2:
        return None
    paces = [_secs(l) for l in run]
    best = min(range(len(paces)), key=lambda i: paces[i])
    middle = sorted(paces)[len(paces) // 2]
    slow = [i for i, p in enumerate(paces) if p > middle + 30]
    text = f"Fastest {pace(paces[best])}, on the {ORDINALS[best] if best < len(ORDINALS) else f'number {best + 1}'}."
    if slow:
        names = _list([ORDINALS[i] if i < len(ORDINALS) else f"number {i + 1}" for i in slow])
        text += f" The {names} {'was' if len(slow) == 1 else 'were'} slower, at {_list([pace(paces[i]) for i in slow])}."
    return {"icon": "⚡", "tone": "hard", "head": "Strides.", "text": text}


# -- C: against the last one --------------------------------------------------------

def figures(pairs, laps: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """What a run is compared on: its reps (pace, heart rate, km of reps) when it
    had them, otherwise the whole run's pace and heart rate."""
    work = _work(pairs) if pairs else []
    if len(work) >= 2:
        metres = sum(l["metres"] for _, l in work)
        secs = sum(l["seconds"] for _, l in work)
        hr = _weighted_hr([l for _, l in work])
        return {"reps": len(work), "km": round(metres / 1000, 1), "p": secs / metres * 1000, "hr": round(hr) if hr else None}
    running = [l for l in laps if l["mark"] not in _RESTING and not l.get("stride") and l.get("metres")]
    metres = sum(l["metres"] for l in running)
    if metres < 1000:
        return None
    hr = _weighted_hr(running)
    return {"km": round(metres / 1000, 1), "p": sum(l["seconds"] for l in running) / metres * 1000,
            "hr": round(hr) if hr else None}


def last_time(this: Mapping[str, Any] | None, before: Mapping[str, Any] | None, what: str, name: str | None,
              day: str) -> dict[str, Any] | None:
    """This run against the last session of the same kind, in one line."""
    if not this or not before or bool(this.get("reps")) != bool(before.get("reps")):
        return None
    on = "of reps" if this.get("reps") else ""

    def said(f: Mapping[str, Any]) -> str:
        hr = f" at {f['hr']} bpm" if f.get("hr") else ""
        return f"{pace(f['p'])}{hr} over {f['km']:g} km" + (f" {on}" if on else "")

    head = f"Against your last {what}"
    detail = f" ({name}, {day})" if name else f" ({day})"
    quicker = this["p"] < before["p"] - 2
    calmer = this.get("hr") and before.get("hr") and this["hr"] < before["hr"] - 1
    tail = ""
    if quicker and calmer:
        tail = ": quicker, for a lower heart rate"
    elif quicker:
        tail = ": quicker"
    elif calmer:
        tail = ": a lower heart rate"
    return {"icon": "↺", "tone": "muted", "head": head + detail + ".",
            "text": f"Then {said(before)}. Today {said(this)}{tail}."}
