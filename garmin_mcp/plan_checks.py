"""Plain notes on a plan's shape: the plan engine's validator (docs/plan-engine-spec.md, section 8).

The same rules over a plan Claude or ChatGPT wrote, before it's created
(check_plan, create_plan) or once it's on the calendar (get_plan). Each
note is {"rule", "week", "date", "text"}, worded as worth a look, never as
an error. Nothing here blocks a write or calls Garmin: the same plan always
gives the same notes.

The limits are the spec's "validator warns at" column, looser than what the
builder would write, so a plan that follows a known method isn't nagged for
small differences. The style (Steady, Balanced or Faster) only changes how
much hard running a week can hold before it's worth a look.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from . import training_plan
from .plan import monday_of
from .training_plan import CUTBACK_SHARE, KEY_SESSION_TYPES

# Section 3, the right-hand column.
START_SHARE = 1.2                  # first week over 120% of the last 4 weeks' highest
WEEK_JUMP_SHARE = 0.2              # a week over 20% or 5 km up on the last full week
WEEK_JUMP_KM = 5.0
LONG_SHARE = 0.4                   # long run over 40% of the week
LONG_JUMP_KM = 3.0                 # long run over 3 km or 20 min up
LONG_JUMP_MIN = 20
LONG_CAP_KM = {"5k": 12.0, "10k": 16.0, "half": 20.0, "marathon": 32.0}
LONG_CAP_MIN = {"marathon": 180}
LONG_CAP_MIN_DEFAULT = 150
REST_RUN_DAYS = 7                  # a session every day for 7 days
RECOVERY_WEEKS = 5                 # no lighter week in 5
TAPER_SHARE = 0.7                  # race week over 70% of the biggest week

# Section 4: quality sessions a week, and the work reps' share of the week's
# distance, at which each style warns.
STYLE_QUALITY = {"steady": 2, "balanced": 3, "faster": 4}
STYLE_HARD_SHARE = {"steady": 0.2, "balanced": 0.25, "faster": 0.3}
STYLE_QUALITY_ADVICE = {
    "steady": "One a week suits a steady plan, with the rest easy",
    "balanced": "Two a week is plenty for most runners, with the rest easy",
    "faster": "Three is the most even Norwegian-style training uses, with the rest very easy",
}

# Section 5: room between races.
RACE_EASY_DAYS = 2                 # nothing hard in the 2 days after a race
B_RACE_GAP_DAYS = 9                # B races on back-to-back weekends
GOAL_CLEAR_DAYS = 10               # no other race in the A race's last 10 days
A_RACE_GAP_WEEKS = {"marathon": 8, "other": 4}

QUALITY_TYPES = ("threshold", "intervals", "sub")
# A long run with a race-pace section is a quality session too.
RACE_PACE_RE = re.compile(
    r"\b(?:H?MP|race[- ]pace|marathon[- ]pace|half[- ]pace|goal[- ]pace|fast finish|progression)\b",
    re.IGNORECASE,
)
# Enough work in a long run to count it as quality, not a few strides.
LONG_WORK_MIN_KM = 1.0
LONG_WORK_SHARE = 0.1
# A first week starting this late (Thursday) isn't a full week to compare with.
PARTIAL_FROM_WEEKDAY = 3

_STYLE_RE = re.compile(r"\b(steady|balanced|faster)\b", re.IGNORECASE)
_FASTER_RE = re.compile(r"norwegian|singles", re.IGNORECASE)
_BUILT_RE = re.compile(r"^Built:\s*(.+)$", re.MULTILINE)


# --------------------------------------------------------------------------
# What the rules read
# --------------------------------------------------------------------------

def style(method: str | None, description: str | None = None) -> str:
    """steady, balanced or faster: the "Built:" line's style, else Faster when
    the method names Norwegian or singles, else Balanced."""
    built = _BUILT_RE.search(description or "")
    for text in (built.group(1) if built else None, method):
        found = _STYLE_RE.search(text or "")
        if found:
            return found.group(1).lower()
    return "faster" if _FASTER_RE.search(method or "") else "balanced"


def goal_distance(*texts: Any, km: float | None = None) -> str | None:
    """5k, 10k, half or marathon, from the goal's words or the A race's distance."""
    for text in texts:
        t = str(text or "").lower()
        if re.search(r"half|\b21(?:\.1)?\s*k|\bhm\b", t):
            return "half"
        if "marathon" in t or re.search(r"\b42(?:\.2)?\s*k", t):
            return "marathon"
        if re.search(r"\b10\s*k", t):
            return "10k"
        if re.search(r"\b5\s*k|parkrun", t):
            return "5k"
    if km:
        return "marathon" if km >= 40 else "half" if km >= 20 else "10k" if km >= 9 else "5k" if km >= 4.5 else None
    return None


def _session(name: str, day: date, sport_key: str, workout: Mapping[str, Any] | None) -> dict[str, Any]:
    """One session as get_plan draws it, from the workout create_plan would write."""
    kind = training_plan.activity_kind(sport_key)
    run = kind == "run"
    totals = training_plan.session_totals(workout) or {} if run else {}
    shape = training_plan.workout_shape(workout) if run else None
    return {
        "date": day.isoformat(),
        "name": name,
        "type": training_plan.session_type(name, sport_key, training_plan.workout_effort(workout)),
        "kind": kind,
        "planned_km": round(training_plan.planned_metres(dict(workout or {})) / 1000, 1) if run else None,
        "planned_min": totals.get("minutes"),
        "shape": shape,
    }


def weeks_from_prepared(prepared: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """training_plan.prepare's sessions as get_plan's weeks: week, starts and
    sessions, each session with its type, kind, planned_km, planned_min and shape."""
    if not prepared:
        return []
    first = monday_of(min(p["date"] for p in prepared))
    weeks: dict[int, dict[str, Any]] = {}
    for item in sorted(prepared, key=lambda p: p["date"]):
        workout = item["workout"].to_dict() if hasattr(item["workout"], "to_dict") else item["workout"]
        sport_key = ((workout or {}).get("sportType") or {}).get("sportTypeKey") or "running"
        n = (monday_of(item["date"]) - first).days // 7 + 1
        week = weeks.setdefault(n, {"week": n, "starts": (first + timedelta(weeks=n - 1)).isoformat(), "sessions": []})
        week["sessions"].append(_session(item["name"], item["date"], sport_key, workout))
    last = max(weeks)
    return [weeks.get(n) or {"week": n, "starts": (first + timedelta(weeks=n - 1)).isoformat(), "sessions": []}
            for n in range(1, last + 1)]


def _work_km(s: Mapping[str, Any]) -> float:
    return sum(float(seg.get("km") or 0) for seg in s.get("shape") or [] if seg.get("k") == "work")


def _is_quality(s: Mapping[str, Any]) -> bool:
    if s.get("type") in QUALITY_TYPES:
        return True
    if s.get("type") == "long":
        work = _work_km(s)
        return bool(RACE_PACE_RE.search(s.get("name") or "")) or (
            work >= LONG_WORK_MIN_KM and work >= LONG_WORK_SHARE * float(s.get("planned_km") or 0))
    return False


def _is_hard(s: Mapping[str, Any], how: str) -> bool:
    """Tempo, intervals, long and race; for Faster, an easy long run isn't hard."""
    t = s.get("type")
    if t == "long" and how == "faster":
        return _is_quality(s)
    return t in KEY_SESSION_TYPES


def _runs(week: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [s for s in week.get("sessions") or [] if s.get("kind", "run") == "run"]


def _week_km(week: Mapping[str, Any]) -> float | None:
    """The week's planned running, or None when some of it isn't known (a
    workout get_plan hasn't read)."""
    runs = _runs(week)
    if week.get("unread") or any(s.get("planned_km") is None for s in runs):
        return None
    return round(sum(float(s["planned_km"]) for s in runs), 1)


def _long(week: Mapping[str, Any]) -> dict[str, Any] | None:
    longs = [s for s in _runs(week) if s.get("type") == "long" and s.get("planned_km")]
    return max(longs, key=lambda s: float(s["planned_km"])) if longs else None


def _day(text: str) -> date:
    return date.fromisoformat(str(text)[:10])


def _when(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def _km(value: float) -> str:
    return f"{value:.0f} km" if abs(value - round(value)) < 0.05 else f"{value:.1f} km"


def _note(rule: str, week: Any, day: date | str | None, text: str) -> dict[str, Any]:
    return {"rule": rule, "week": week, "date": day.isoformat() if isinstance(day, date) else day, "text": text}


# --------------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------------

def _full(weeks: Sequence[Mapping[str, Any]], i: int) -> bool:
    """Whether a week is a whole week to compare with: not a first week that
    starts on a Thursday or later."""
    if i:
        return True
    days = [_day(s["date"]) for s in weeks[0].get("sessions") or []]
    return bool(days) and min(days).weekday() < PARTIAL_FROM_WEEKDAY


def _volume(weeks, kms, history, out) -> None:
    # Starting week against the last 4 weeks actually run.
    highest = max((float(h.get("run_km") or 0) for h in history or []), default=0.0)
    start = next((i for i in range(len(weeks)) if _full(weeks, i)), None)
    if highest and start is not None and kms[start] is not None and kms[start] > START_SHARE * highest:
        out.append(_note(
            "start_volume", weeks[start]["week"], weeks[start]["starts"],
            f"Week {weeks[start]['week']} has {_km(kms[start])} of running, and the most in one of the "
            f"last 4 weeks was {_km(highest)}. Starting near what the legs are used to, then building, "
            "is gentler.",
        ))
    # Each week against the last full one: the higher of the two before, so
    # the week after a cutback is measured against the week before it.
    for i in range(1, len(weeks)):
        before = [kms[j] for j in (i - 1, i - 2) if j >= 0 and _full(weeks, j) and kms[j]]
        if kms[i] is None or not before:
            continue
        base = max(before)
        if kms[i] - base > max(WEEK_JUMP_SHARE * base, WEEK_JUMP_KM):
            out.append(_note(
                "weekly_jump", weeks[i]["week"], weeks[i]["starts"],
                f"Week {weeks[i]['week']} goes up to {_km(kms[i])} from {_km(base)}. That's a big jump; "
                "steps of about 10% a week, or 3 km, give the legs time to catch up.",
            ))


def _long_runs(weeks, kms, raced, cap_for, history_long, out) -> None:
    for i, week in enumerate(weeks):
        long = _long(week)
        if not long:
            continue
        km, minutes, day = float(long["planned_km"]), long.get("planned_min"), _day(long["date"])
        n = week["week"]
        # Share of the week, a week holding a race aside.
        if kms[i] and i not in raced and _full(weeks, i) and km > LONG_SHARE * kms[i]:
            out.append(_note(
                "long_run_share", n, day,
                f"The long run is {_km(km)} of week {n}'s {_km(kms[i])}, {round(km / kms[i] * 100)}% of it. "
                "When one run is most of the week, its tiredness isn't spread out; a bit more easy running "
                "around it, or a shorter long run, is worth a look.",
            ))
        # Growth on the longest of the two weeks before, so a cutback week's
        # shorter long run isn't the baseline; week one against the history.
        before = [_long(weeks[j]) for j in (i - 1, i - 2) if j >= 0]
        before = [b for b in before if b]
        if before:
            prev = max(before, key=lambda s: float(s["planned_km"]))
            prev_km, prev_min = float(prev["planned_km"]), prev.get("planned_min")
        elif i == 0 and history_long:
            prev_km, prev_min = float(history_long), None
        else:
            prev_km = None
        if prev_km is not None and km - prev_km > LONG_JUMP_KM and not (
                minutes and prev_min and minutes - prev_min <= LONG_JUMP_MIN):
            since = "your longest recent run" if not before else "the long run before"
            out.append(_note(
                "long_run_jump", n, day,
                f"The long run goes from {_km(prev_km)} to {_km(km)} in week {n}, against {since}. "
                "Steps of 2 km a week are gentler on the legs.",
            ))
        # The cap for the goal, by distance and by time.
        cap_km, cap_min, goal = cap_for
        if (cap_km and km > cap_km) or (minutes and minutes > cap_min):
            over = (f"{_km(km)}" if cap_km and km > cap_km else f"about {minutes // 60} h {minutes % 60:02d}")
            limit = (f"{_km(cap_km)} is usually enough for a {goal}" if cap_km and km > cap_km
                     else f"past {cap_min // 60} h {cap_min % 60:02d} extra time on feet adds more tiredness "
                          "than fitness")
            out.append(_note(
                "long_run_cap", n, day,
                f"The long run in week {n} is {over}. {limit[0].upper()}{limit[1:]}, so it's worth a look.",
            ))


def _hard_running(weeks, kms, how, goal_dates, out) -> None:
    limit, share = STYLE_QUALITY[how], STYLE_HARD_SHARE[how]
    for i, week in enumerate(weeks):
        runs = _runs(week)
        n = week["week"]
        quality = [s for s in runs if _is_quality(s) or (s.get("type") == "race" and s["date"][:10] not in goal_dates)]
        if len(quality) >= limit:
            out.append(_note(
                "quality_sessions", n, week["starts"],
                f"Week {n} has {len(quality)} hard sessions ({', '.join(s['name'] for s in quality)}). "
                f"{STYLE_QUALITY_ADVICE[how]}.",
            ))
        # Work reps' share of the week, when every session that could hold
        # them has its steps read.
        if not kms[i] or not _full(weeks, i) or any(
                s.get("shape") is None and s.get("type") in QUALITY_TYPES + ("long",) for s in runs):
            continue
        work = sum(_work_km(s) for s in runs if s.get("type") != "race")
        if work > share * kms[i]:
            out.append(_note(
                "hard_volume", n, week["starts"],
                f"About {_km(work)} of week {n}'s {_km(kms[i])} is fast running, {round(work / kms[i] * 100)}%. "
                f"Up to {round(share * 100) - 5}% or so leaves the easy running room to do its work.",
            ))


def _days(weeks, how, races, out) -> None:
    sessions = sorted((s for w in weeks for s in w.get("sessions") or []), key=lambda s: s["date"])
    week_of = {s["date"][:10]: w["week"] for w in weeks for s in w.get("sessions") or []}
    hard: dict[date, dict[str, Any]] = {}
    for s in sessions:
        if s.get("kind", "run") == "run" and _is_hard(s, how):
            hard.setdefault(_day(s["date"]), s)
    race_days = {_day(r["date"]): r for r in races}
    after_race: set[date] = set()
    for day, race in sorted(race_days.items()):
        for gap in range(1, RACE_EASY_DAYS + 1):
            s = hard.get(day + timedelta(days=gap))
            if s and s.get("type") != "race":
                after_race.add(day + timedelta(days=gap))
                out.append(_note(
                    "hard_after_race", week_of.get(s["date"][:10]), s["date"][:10],
                    f"{s['name']} on {_when(_day(s['date']))} is {'the day' if gap == 1 else '2 days'} after "
                    f"{race.get('name') or 'a race'}. A couple of easy days after racing let the legs come back.",
                ))
    for day, s in sorted(hard.items()):
        before = hard.get(day - timedelta(days=1))
        if before and day not in after_race:
            out.append(_note(
                "hard_days_in_a_row", week_of.get(s["date"][:10]), s["date"][:10],
                f"{s['name']} on {_when(day)} is the day after {before['name']}. Fine once, better with "
                "an easy day or a rest day between.",
            ))
    # A rest day in every 7: any session at all counts as not rest.
    busy = sorted({_day(s["date"]) for s in sessions})
    streak: list[date] = []
    for day in busy:
        streak = streak + [day] if streak and day - streak[-1] == timedelta(days=1) else [day]
        if len(streak) == REST_RUN_DAYS:
            out.append(_note(
                "no_rest_day", week_of.get(day.isoformat()), day,
                f"There's a session every day from {_when(streak[0])} to {_when(day)}. A day off each week "
                "lets the work sink in.",
            ))


def _recovery(weeks, kms, out) -> None:
    """A lighter week in every 5: under CUTBACK_SHARE of the week before and
    of the week after, or a taper's falling weeks and the last week."""
    light = 0
    for i, week in enumerate(weeks):
        if kms[i] is None or not _full(weeks, i):
            light = 0
            continue
        prev = kms[i - 1] if i and _full(weeks, i - 1) else None
        nxt = kms[i + 1] if i + 1 < len(kms) else None
        lighter = prev is not None and kms[i] < CUTBACK_SHARE * prev and (
            nxt is None or kms[i] < CUTBACK_SHARE * nxt or nxt <= kms[i])
        if lighter:
            light = 0
            continue
        light += 1
        if light == RECOVERY_WEEKS:
            first = weeks[i - RECOVERY_WEEKS + 1]["week"]
            out.append(_note(
                "no_recovery_week", week["week"], week["starts"],
                f"Weeks {first} to {week['week']} have no lighter week. A week at about three quarters of the "
                "one before, every 3 or 4 weeks, lets the training settle in.",
            ))
            light = 0


def _taper(weeks, kms, goals, out) -> None:
    for race in goals:
        day = _day(race["date"])
        i = next((k for k, w in enumerate(weeks) if w["starts"] == monday_of(day).isoformat()), None)
        if i is None or i < 3:
            continue
        peak = max((kms[k] for k in range(i) if kms[k] and _full(weeks, k)), default=0.0)
        before = [s for s in _runs(weeks[i]) if _day(s["date"]) < day and s.get("type") != "race"]
        if not peak or any(s.get("planned_km") is None for s in before):
            continue
        km = sum(float(s["planned_km"]) for s in before)
        if km > TAPER_SHARE * peak:
            out.append(_note(
                "taper", weeks[i]["week"], weeks[i]["starts"],
                f"Race week has {_km(km)} of running before {race.get('name') or 'the race'}, "
                f"{round(km / peak * 100)}% of the biggest week ({_km(peak)}). Most plans ease off to about "
                "half in race week, so the legs arrive fresh.",
            ))


def _races(weeks, kms, races, out) -> None:
    goals = [r for r in races if r.get("tier") == "goal"]
    b_races = [r for r in races if r.get("tier") == "key"]
    for a in goals:
        a_day = _day(a["date"])
        for r in races:
            gap = (a_day - _day(r["date"])).days
            if r is not a and r.get("tier") != "goal" and 0 < gap <= GOAL_CLEAR_DAYS:
                out.append(_note(
                    "race_before_goal", r.get("week"), r["date"],
                    f"{r.get('name') or 'A race'} on {_when(_day(r['date']))} is {gap} days before "
                    f"{a.get('name') or 'the goal race'}. Racing this close can take the edge off the one that "
                    "matters; an easy run or a few strides might serve better.",
                ))
    for one, two in zip(b_races, b_races[1:]):
        gap = (_day(two["date"]) - _day(one["date"])).days
        if 0 < gap <= B_RACE_GAP_DAYS:
            out.append(_note(
                "b_races_close", two.get("week"), two["date"],
                f"{one.get('name') or 'One race'} and {two.get('name') or 'the next'} are {gap} days apart. "
                "Racing on back-to-back weekends leaves little room for training; one could be a fun run.",
            ))
    longest = max((float(s["planned_km"]) for w in weeks for s in _runs(w)
                   if s.get("type") == "long" and s.get("planned_km")), default=0.0)
    for r in b_races:
        km = float(r.get("distance_km") or 0)
        i = next((k for k, w in enumerate(weeks) if w["starts"] == monday_of(_day(r["date"])).isoformat()), None)
        if not (longest and km > longest and i) or kms[i] is None or not kms[i - 1]:
            continue
        if kms[i] > kms[i - 1]:
            out.append(_note(
                "b_race_long", weeks[i]["week"], r["date"],
                f"{r.get('name') or 'The race'} ({_km(km)}) is longer than any long run in the plan "
                f"({_km(longest)}), and week {weeks[i]['week']} has more running than the week before. "
                "A lighter week around it, with the race as the long run, is worth a look.",
            ))
    for one, two in zip(goals, goals[1:]):
        marathon = any(goal_distance(r.get("name"), km=r.get("distance_km")) == "marathon" for r in (one, two))
        need = A_RACE_GAP_WEEKS["marathon" if marathon else "other"]
        gap = (_day(two["date"]) - _day(one["date"])).days
        if gap < need * 7:
            out.append(_note(
                "goal_races_close", two.get("week"), two["date"],
                f"{one.get('name') or 'The first goal race'} and {two.get('name') or 'the second'} are "
                f"{gap // 7} weeks apart. Two goal races want about {need} weeks between them; making the "
                "first a tune-up race is worth a look.",
            ))


def _goal_races(races: Sequence[Mapping[str, Any]], weeks: Sequence[Mapping[str, Any]],
                last_is_goal: bool) -> list[dict[str, Any]]:
    """The races with their tiers; with last_is_goal, the last race in the
    plan's last week is taken as the goal when nothing is marked as one."""
    out = [dict(r) for r in races if r.get("date")]
    if last_is_goal and out and not any(r.get("tier") == "goal" for r in out) and weeks:
        last = out[-1]
        if _day(last["date"]) >= _day(weeks[-1]["starts"]):
            last["tier"] = "goal"
    return out


def check_weeks(
    weeks: Sequence[Mapping[str, Any]],
    *,
    races: Iterable[Mapping[str, Any]] = (),
    method: str | None = None,
    description: str | None = None,
    goal: str | None = None,
    history: Sequence[Mapping[str, Any]] | None = None,
    longest_recent_km: float | None = None,
    since: date | None = None,
    last_race_is_goal: bool = True,
) -> list[dict[str, Any]]:
    """The notes on a plan, given as get_plan's weeks, oldest first.

    races are training_plan.races' list (tier goal, key, tune or other);
    history is weekly_run_km's last 4 weeks before the plan, and
    longest_recent_km the longest run in them. With since, only the notes
    from that week on: what's past can't be changed. last_race_is_goal is
    for a whole plan; a few weeks of calendar can end on a parkrun.
    """
    weeks = [w for w in weeks if w.get("starts")]
    if not weeks:
        return []
    how = style(method, description)
    found = _goal_races(list(races), weeks, last_race_is_goal)
    goals = [r for r in found if r.get("tier") == "goal"]
    distance = goal_distance(goal, *(r.get("name") for r in goals),
                             km=max((float(r.get("distance_km") or 0) for r in goals), default=None))
    cap_for = (LONG_CAP_KM.get(distance or ""), LONG_CAP_MIN.get(distance or "", LONG_CAP_MIN_DEFAULT),
               {"half": "half marathon", "5k": "5K", "10k": "10K"}.get(distance or "", distance))
    kms = [_week_km(w) for w in weeks]
    raced = {i for i, w in enumerate(weeks) for s in w.get("sessions") or [] if s.get("type") == "race"}
    raced |= {i for i, w in enumerate(weeks) for r in found if w["starts"] == monday_of(_day(r["date"])).isoformat()}
    out: list[dict[str, Any]] = []
    _volume(weeks, kms, history, out)
    _long_runs(weeks, kms, raced, cap_for, longest_recent_km, out)
    _hard_running(weeks, kms, how, {r["date"][:10] for r in goals}, out)
    _days(weeks, how, found, out)
    _recovery(weeks, kms, out)
    _taper(weeks, kms, goals, out)
    _races(weeks, kms, found, out)
    if since is not None:
        cutoff = monday_of(since).isoformat()
        out = [c for c in out if not c.get("date") or c["date"] >= cutoff]
    return sorted(out, key=lambda c: (c.get("date") or "", c["rule"]))


def check_prepared(
    prepared: Sequence[Mapping[str, Any]],
    *,
    goal: str | None = None,
    method: str | None = None,
    milestones: Sequence[Mapping[str, Any]] | None = None,
    history: Sequence[Mapping[str, Any]] | None = None,
    longest_recent_km: float | None = None,
) -> list[dict[str, Any]]:
    """The notes on a plan create_plan is about to write, from training_plan.prepare's sessions."""
    weeks = weeks_from_prepared(prepared)
    marks = training_plan.check_milestones(milestones) if milestones else []
    found = training_plan.races({"weeks": weeks}, marks)
    return check_weeks(weeks, races=found, method=method, goal=goal, history=history,
                       longest_recent_km=longest_recent_km)


def check_answer(answer: Mapping[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """The notes on get_plan's answer, from this week on."""
    weeks = answer.get("weeks") or []
    found = answer.get("races")
    if found is None:
        found = training_plan.races(answer, answer.get("milestones"))
    plan = answer.get("current_plan") or answer.get("upcoming_plan") or {}
    return check_weeks(
        weeks, races=found or [], method=answer.get("method") or plan.get("method"),
        goal=answer.get("goal") or plan.get("goal"), since=today,
        # Only a plan's own last week holds its goal race, not the calendar's.
        last_race_is_goal=bool(answer.get("label")),
    )


def longest_run_km(activities: Iterable[Mapping[str, Any]]) -> float | None:
    """The longest run among some activities, in km."""
    runs = [float(a.get("distance") or 0) / 1000 for a in activities
            if training_plan.category((a.get("activityType") or {}).get("typeKey")) == "run"]
    return round(max(runs), 1) if runs and max(runs) else None
