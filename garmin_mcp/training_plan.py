"""A training plan as one thing: created together, read back together, kept in Garmin.

Until now a plan was a string of separate create and schedule calls, and
"how is the plan going" had to be reassembled from the calendar every time.
This makes the block itself the unit: create_plan builds and schedules every
session in one go, and get_plan reads it back week by week with each session
marked done, missed or still to come.

Nothing is stored on the server. A plan lives where the sessions live, on the
Garmin calendar, and its membership is written into each workout's name as a
short code after a middle dot, "Threshold 5x1k · HM". That keeps the hosted
server holding what the privacy page says it holds, one token per person and
nothing else, and it means a plan made on a laptop is the same plan on the
hosted connector. The code is also what shows on the watch, so it is short
and readable rather than an id.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from .cycling import is_ride
from .formatting import DateError, drop_empty, duration, pace_per_km, parse_date
from .plan import DEFAULT_MPS, category, monday_of, planned_distance_m
from .progress import Actual, Planned, match
from .workouts import WorkoutError, build_workout, format_pace

TAG_SEP = " · "
LABEL_RE = re.compile(r"^[A-Z0-9]{2,8}$")
# New plans get a short code, since it sits at the end of every workout name on
# the calendar and the watch. Plans made with longer codes still read back.
NEW_LABEL_RE = re.compile(r"^[A-Z0-9]{2,3}$")
TITLE_RE = re.compile(r" · ([A-Z0-9]{2,8})$")

MAX_SESSIONS = 150
MAX_WEEKS_AHEAD = 30
# A label only counts as a plan once it tags at least this many sessions, so a
# one-off workout someone happened to name "Easy · Z2" is not mistaken for one.
MIN_PLAN_SESSIONS = 2


class PlanError(ValueError):
    """Bad plan input or state; the message is shown to the user."""


# --------------------------------------------------------------------------
# Names and labels
# --------------------------------------------------------------------------


def normalise_label(label: str | None, goal: str, *, new: bool = False) -> str:
    """HM, 10K, B1: capitals and digits, from the goal if not given.

    A new plan's code is two or three characters; looking one up accepts the
    up-to-eight that older plans were made with.
    """
    pattern = NEW_LABEL_RE if new else LABEL_RE
    if label:
        cleaned = re.sub(r"[^A-Za-z0-9]", "", str(label)).upper()
        if not pattern.match(cleaned):
            raise PlanError(
                f"Plan label {label!r} must be 2 or 3 letters or digits, like HM, 10K or B1."
                if new else
                f"Plan label {label!r} must be 2 to 8 letters or digits, like HM or 10K."
            )
        return cleaned
    words = re.findall(r"[A-Za-z0-9]+", goal or "")
    known = {"half": "HM", "marathon": "MAR", "10k": "10K", "5k": "5K", "ultra": "ULT"}
    lowered = [w.lower() for w in words]
    if "half" in lowered:
        return "HM"
    for word in lowered:
        if word in known:
            return known[word]
    initials = "".join(w[0] for w in words[:3]).upper()
    return initials if NEW_LABEL_RE.match(initials) else "PL"


def tagged(name: str, label: str) -> str:
    base = strip_tag(name.strip())
    return f"{base}{TAG_SEP}{label}"


def label_of(title: str | None) -> str | None:
    found = TITLE_RE.search(title or "")
    return found.group(1) if found else None


def strip_tag(title: str) -> str:
    return TITLE_RE.sub("", title or "")


MAX_NAME = 40
MAX_METHOD = 60
MAX_MILESTONES = 4
MAX_PHASE = 12


def describe(
    goal: str,
    label: str,
    first: date,
    last: date,
    note: str | None,
    *,
    name: str | None = None,
    method: str | None = None,
    phase: str | None = None,
    milestones: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """What each workout carries in its description, readable in Garmin Connect.

    The plan's name, method, this session's phase and the milestones ride
    along in plain lines, so the card can read them back from Garmin and
    nothing about the plan is kept anywhere else.
    """
    lines = [f"Plan {label}: {goal.strip()}, {first.isoformat()} to {last.isoformat()}."]
    block = " \u00b7 ".join(x.strip() for x in (name, method) if x and x.strip())
    if block:
        lines.append(f"Block: {block}")
    if phase and phase.strip():
        lines.append(f"Phase: {phase.strip()}")
    if milestones:
        parts = []
        for m in milestones:
            bits = [str(m.get("label", "")).strip()]
            bits += [str(m[k]).strip() for k in ("date", "target") if m.get(k)]
            parts.append(", ".join(b for b in bits if b))
        lines.append("Milestones: " + " | ".join(parts))
    text = "\n".join(lines)
    return f"{note.strip()}\n\n{text}" if note and note.strip() else text


def _line(text: str | None, head: str) -> str | None:
    found = re.search(rf"^{head}: (.+)$", text or "", re.MULTILINE)
    return found.group(1).strip() if found else None


def block_from_description(text: str | None) -> dict[str, str]:
    """{name, method} from a workout's notes, whichever of the two it carries."""
    line = _line(text, "Block")
    if not line:
        return {}
    name, _, method = line.partition(" \u00b7 ")
    return drop_empty({"name": name.strip() or None, "method": method.strip() or None})


def phase_from_description(text: str | None) -> str | None:
    found = _line(text, "Phase")
    return found[:MAX_PHASE] if found else None


def milestones_from_description(text: str | None) -> list[dict[str, str]]:
    line = _line(text, "Milestones")
    out: list[dict[str, str]] = []
    for part in (line or "").split(" | "):
        bits = [b.strip() for b in part.split(",") if b.strip()]
        if not bits:
            continue
        m: dict[str, str] = {"label": bits[0]}
        for bit in bits[1:]:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", bit):
                m["date"] = bit
            else:
                m["target"] = bit
        out.append(m)
    return out


def check_milestones(milestones: Any) -> list[dict[str, str]]:
    """Milestones as given, checked: a label each, an optional date and target."""
    if not milestones:
        return []
    if not isinstance(milestones, (list, tuple)) or len(milestones) > MAX_MILESTONES:
        raise PlanError(f"Give at most {MAX_MILESTONES} milestones, each with a label and an optional date and target.")
    out = []
    for i, m in enumerate(milestones, start=1):
        if not isinstance(m, Mapping) or not str(m.get("label", "")).strip():
            raise PlanError(f"Milestone {i} needs a label, like 'Half check' or 'Race day'.")
        row = {"label": str(m["label"]).strip()[:30].replace("|", "/").replace(",", " ")}
        if m.get("date"):
            try:
                row["date"] = parse_date(m["date"], default_today=False)
            except DateError as exc:
                raise PlanError(f"Milestone {i}: {exc}") from exc
        if m.get("target"):
            row["target"] = str(m["target"]).strip()[:20].replace("|", "/").replace(",", " ")
        out.append(row)
    return out


_STEP_LABELS = {
    "warmup": "Warm up", "cooldown": "Cool down", "interval": "Run", "recovery": "Recover",
    "rest": "Rest", "other": "Run",
}


def _amount(step: Mapping[str, Any]) -> str | None:
    """How long a step lasts, as the watch counts it."""
    kind = ((step.get("endCondition") or {}).get("conditionTypeKey") or "").lower()
    value = float(step.get("endConditionValue") or 0)
    if kind == "time" and value:
        minutes, secs = divmod(int(round(value)), 60)
        if not secs:
            return f"{minutes} min"
        return f"{int(round(value))} s" if value < 120 else f"{minutes}:{secs:02d}"
    if kind == "distance" and value:
        return f"{value / 1000:g} km" if value >= 1000 else f"{value:.0f} m"
    if kind == "reps" and value:
        return f"{value:.0f} reps"
    if kind in ("lap.button", "lap_button"):
        return "until lap"
    return None


def _target(step: Mapping[str, Any]) -> str | None:
    """The step's target, the way a runner reads it: pace per km or heart rate."""
    key = ((step.get("targetType") or {}).get("workoutTargetTypeKey") or "").lower()
    one, two = step.get("targetValueOne"), step.get("targetValueTwo")
    if key == "pace.zone" and one and two:
        # Stored as speeds in m/s: the faster speed is the quicker pace.
        quick, slow = sorted((1000.0 / float(one), 1000.0 / float(two)))
        return f"{format_pace(quick)[:-3]}\u2013{format_pace(slow)}"
    if key == "heart.rate.zone" and one and two:
        low, high = sorted((float(one), float(two)))
        return f"{low:.0f}\u2013{high:.0f} bpm"
    if key == "heart.rate.zone" and step.get("zoneNumber"):
        return f"HR zone {step['zoneNumber']}"
    if key == "power.zone" and one and two:
        low, high = sorted((float(one), float(two)))
        return f"{low:.0f}\u2013{high:.0f} W"
    return None


# A swim's work steps are labelled by stroke, as a swimmer reads a set.
_STROKE_LABELS = {
    "free": "Freestyle", "backstroke": "Backstroke", "breaststroke": "Breaststroke",
    "fly": "Butterfly", "individual_medley": "IM", "drill": "Drill",
}


def workout_steps(workout: Mapping[str, Any] | None) -> list[dict[str, Any]] | None:
    """A workout's steps, readable at a glance: what, how long, at what target.

    Repeats keep their structure (6 x [800 m, 90 s recover]), because that is
    how a session is understood and how the watch runs it. Strength steps name
    the exercise. None when the workout has no steps worth showing.
    """
    sport = ((workout or {}).get("sportType") or {}).get("sportTypeKey") or ""
    work = "Ride" if "cycl" in sport or "bik" in sport else "Swim" if "swim" in sport else "Run"

    def read(steps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for step in sorted(steps or [], key=lambda s: s.get("stepOrder") or 0):
            if step.get("type") == "RepeatGroupDTO" or step.get("numberOfIterations"):
                inner = read(step.get("workoutSteps") or [])
                times = int(step.get("numberOfIterations") or step.get("endConditionValue") or 1)
                if inner:
                    out.append({"kind": "repeat", "times": times, "steps": inner})
                continue
            kind = ((step.get("stepType") or {}).get("stepTypeKey") or "other").lower()
            exercise = step.get("exerciseName") or step.get("category")
            stroke = _STROKE_LABELS.get(((step.get("strokeType") or {}).get("strokeTypeKey") or "").lower())
            label = (str(exercise).replace("_", " ").capitalize() if exercise
                     else stroke if stroke and kind in ("interval", "other")
                     else work if kind in ("interval", "other") else _STEP_LABELS.get(kind, work))
            out.append(drop_empty({
                "kind": kind,
                "label": label,
                "amount": _amount(step),
                "target": _target(step),
                "note": (step.get("description") or "").strip() or None,
            }))
        return out

    steps: list[dict[str, Any]] = []
    for segment in (workout or {}).get("workoutSegments") or []:
        steps.extend(read(segment.get("workoutSteps") or []))
    return steps or None


def goal_from_description(text: str | None, label: str) -> str | None:
    found = re.search(rf"Plan {re.escape(label)}: (.+?), \d{{4}}-\d{{2}}-\d{{2}} to", text or "")
    return found.group(1) if found else None


# --------------------------------------------------------------------------
# Creating
# --------------------------------------------------------------------------


def prepare(
    goal: str,
    sessions: Sequence[Mapping[str, Any]],
    label: str | None,
    today: date,
    *,
    name: str | None = None,
    method: str | None = None,
    milestones: Any = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Validate every session and build its workout before anything is created.

    A plan that fails at session 40 of 60 leaves a half-built block on the
    calendar, so everything that can be checked locally is checked first.
    """
    if not goal or not str(goal).strip():
        raise PlanError("The plan needs a goal, like 'Half marathon, 1:40, 15 November'.")
    if not sessions:
        raise PlanError("The plan has no sessions.")
    if len(sessions) > MAX_SESSIONS:
        raise PlanError(f"{len(sessions)} sessions is more than one plan should hold ({MAX_SESSIONS}).")

    code = normalise_label(label, goal, new=True)
    # Named apart from each session's name below, which used to shadow it.
    plan_name = str(name).strip()[:MAX_NAME].replace("\n", " ") if name and str(name).strip() else None
    plan_method = str(method).strip()[:MAX_METHOD].replace("\n", " ") if method and str(method).strip() else None
    marks = check_milestones(milestones)
    horizon = today + timedelta(weeks=MAX_WEEKS_AHEAD)
    prepared: list[dict[str, Any]] = []
    for index, raw in enumerate(sessions, start=1):
        try:
            day = date.fromisoformat(parse_date(raw.get("date"), default_today=False))
        except DateError as exc:
            raise PlanError(f"Session {index}: {exc}") from exc
        if day < today:
            raise PlanError(f"Session {index} is dated {day.isoformat()}, which has passed.")
        if day > horizon:
            raise PlanError(f"Session {index} is more than 30 weeks out ({day.isoformat()}).")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise PlanError(f"Session {index} needs a name.")
        try:
            workout, summary, estimated = build_workout(
                tagged(name, code), raw.get("sport") or "running", raw.get("steps") or [], None,
                raw.get("pool_length_meters"),
            )
        except WorkoutError as exc:
            raise PlanError(f"Session {index} ({name}): {exc}") from exc
        phase = str(raw.get("phase") or "").strip()[:MAX_PHASE].replace("\n", " ") or None
        prepared.append(
            {"date": day, "name": name, "workout": workout, "summary": summary,
             "estimated": estimated, "note": raw.get("description"), "phase": phase}
        )

    prepared.sort(key=lambda p: p["date"])
    first, last = prepared[0]["date"], prepared[-1]["date"]
    for item in prepared:
        item["workout"].description = describe(
            goal, code, first, last, item["note"],
            name=plan_name, method=plan_method, phase=item["phase"], milestones=marks,
        )
    return code, prepared


# --------------------------------------------------------------------------
# The shape of a session
# --------------------------------------------------------------------------

SHAPE_MAX_SEGMENTS = 60
# A body step this much faster than the slowest body step is work, not easy
# running: about 20 s/km at a 5:00 easy pace.
WORK_SPEED_RATIO = 1.06
RACE_RE = re.compile(r"race|parkrun|marathon|\bhalf\b|\b5k\b|\b10k\b", re.IGNORECASE)
NOT_RACE_RE = re.compile(r"pace|tempo|easy|long run|prep|sim", re.IGNORECASE)
QUALITY_RE = re.compile(
    r"threshold|tempo|interval|reps|\d\s?x\s?\d|hill|vo2|fartlek|progression|marathon pace|\bmp\b|steady|race pace|sub-?t",
    re.IGNORECASE,
)


def _speed(step: Mapping[str, Any]) -> float | None:
    key = ((step.get("targetType") or {}).get("workoutTargetTypeKey") or "").lower()
    one, two = step.get("targetValueOne"), step.get("targetValueTwo")
    if key == "pace.zone" and one and two:
        return (float(one) + float(two)) / 2
    return None


def workout_shape(workout: Mapping[str, Any] | None) -> list[dict[str, Any]] | None:
    """A run as segments the card draws: easy, work and recovery, each by its distance.

    Built from the structured steps, repeats unrolled, time steps converted at
    their pace target (an easy pace when they have none). Work is what sits
    inside a repeat, or a body step clearly faster than the slowest one, so a
    long run with marathon-pace stretches shows them. Runs only: a strength
    session or a swim has no shape worth drawing this way.
    """
    if not workout:
        return None
    sport = ((workout.get("sportType") or {}).get("sportTypeKey") or "running").lower()
    if category(sport) != "run":
        return None
    flat: list[tuple[str, float, float | None, bool]] = []

    def walk(steps: Sequence[Mapping[str, Any]], in_repeat: bool) -> None:
        for step in sorted(steps or [], key=lambda x: x.get("stepOrder") or 0):
            if step.get("type") == "RepeatGroupDTO" or step.get("numberOfIterations"):
                times = int(step.get("numberOfIterations") or step.get("endConditionValue") or 1)
                before = len(flat)
                walk(step.get("workoutSteps") or [], True)
                inner = flat[before:]
                for _ in range(max(0, times - 1)):
                    flat.extend(inner)
                continue
            kind = ((step.get("stepType") or {}).get("stepTypeKey") or "other").lower()
            cond = ((step.get("endCondition") or {}).get("conditionTypeKey") or "").lower()
            value = float(step.get("endConditionValue") or 0)
            speed = _speed(step)
            if cond == "distance":
                metres = value
            elif cond == "time":
                metres = value * (speed or DEFAULT_MPS)
            else:
                metres = 0.0
            flat.append((kind, metres, speed, in_repeat))

    for segment in workout.get("workoutSegments") or []:
        walk(segment.get("workoutSteps") or [], False)
    if not flat:
        return None

    body = [(k, m, sp, rep) for k, m, sp, rep in flat if k not in ("warmup", "cooldown", "recovery", "rest")]
    speeds = [sp for _, _, sp, _ in body if sp]
    slowest = min(speeds) if speeds else None
    any_repeat = any(rep for _, _, _, rep in body)

    def classify(kind: str, speed: float | None, in_repeat: bool) -> str:
        if kind in ("warmup", "cooldown"):
            return "easy"
        if kind in ("recovery", "rest"):
            return "rec"
        if in_repeat:
            return "work"
        if len(body) <= 1 and not any_repeat:
            return "easy"
        if speed and slowest and speed >= slowest * WORK_SPEED_RATIO:
            return "work"
        return "easy"

    segments: list[dict[str, Any]] = []
    for kind, metres, speed, in_repeat in flat:
        km = metres / 1000
        if km < 0.05:
            continue
        k = classify(kind, speed, in_repeat)
        if segments and segments[-1]["k"] == k and k == "easy":
            segments[-1]["km"] = round(segments[-1]["km"] + km, 2)
        else:
            segments.append({"k": k, "km": round(km, 2)})
    return segments[:SHAPE_MAX_SEGMENTS] or None


def session_type(name: str | None, sport_key: str | None, shape: Sequence[Mapping[str, Any]] | None) -> str:
    """easy, sub, long or race for a run, from its shape and its name; the sport otherwise.

    The shape decides easy against sub-threshold, since that is structural.
    Long and race have no structural signal, so the name says.
    """
    sport = (sport_key or "running").lower()
    cat = category(sport)
    if cat == "strength":
        return "strength"
    if is_ride(sport):
        return "ride"
    if "swim" in sport:
        return "swim"
    if cat != "run":
        return "other"
    n = name or ""
    if RACE_RE.search(n) and not NOT_RACE_RE.search(n):
        return "race"
    if re.search(r"\blong\b", n, re.IGNORECASE):
        return "long"
    if shape:
        # Strides are repeats too, so work has to amount to something before
        # a run reads as sub-threshold: 600 m and a tenth of the distance.
        work = sum(float(seg.get("km") or 0) for seg in shape if seg.get("k") == "work")
        total = sum(float(seg.get("km") or 0) for seg in shape)
        if work >= 0.6 and total and work / total >= 0.1:
            return "sub"
    # One steady step at a pace has no structure to read; its name says.
    return "sub" if QUALITY_RE.search(n) else "easy"


def main_target(steps: Sequence[Mapping[str, Any]] | None) -> str | None:
    """The target a session is about: the work reps', else the first step that has one."""
    def in_repeats(items: Sequence[Mapping[str, Any]]) -> str | None:
        for s in items or []:
            if s.get("kind") == "repeat":
                for inner in s.get("steps") or []:
                    if inner.get("kind") in ("interval", "other") and inner.get("target"):
                        return str(inner["target"])
                hit = in_repeats(s.get("steps") or [])
                if hit:
                    return hit
        return None

    hit = in_repeats(steps or [])
    if hit:
        return hit
    for s in steps or []:
        if s.get("kind") not in ("warmup", "cooldown", "recovery", "rest", "repeat") and s.get("target"):
            return str(s["target"])
    for s in steps or []:
        if s.get("target"):
            return str(s["target"])
    return None


def weekly_run_km(activities: Iterable[Mapping[str, Any]], monday: date, weeks: int) -> list[dict[str, Any]]:
    """Kilometres run in each of the `weeks` weeks before `monday`, oldest first."""
    first = monday - timedelta(weeks=weeks)
    totals = [0.0] * weeks
    counts = [0] * weeks
    for a in activities:
        day = _day(a.get("startTimeLocal"))
        if not day or not first <= day < monday:
            continue
        if category((a.get("activityType") or {}).get("typeKey")) != "run":
            continue
        i = (day - first).days // 7
        totals[i] += float(a.get("distance") or 0) / 1000
        counts[i] += 1
    return [
        {"starts": (first + timedelta(weeks=i)).isoformat(), "run_km": round(totals[i], 1), "runs": counts[i]}
        for i in range(weeks)
    ]


def phase_spans(weeks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]] | None:
    """Phases as the chart bands them: each phase with the first and last week it covers.

    A week's phase is the one most of its sessions carry; weeks without one
    take the phase of the week before, so a rest week stays in its phase.
    """
    labels: list[str | None] = []
    for week in weeks:
        counts: dict[str, int] = defaultdict(int)
        for s in week.get("sessions") or []:
            if s.get("phase"):
                counts[str(s["phase"])] += 1
        labels.append(max(counts, key=lambda k: counts[k]) if counts else None)
    if not any(labels):
        return None
    for i in range(1, len(labels)):
        labels[i] = labels[i] or labels[i - 1]
    for i in range(len(labels) - 2, -1, -1):
        labels[i] = labels[i] or labels[i + 1]
    spans: list[dict[str, Any]] = []
    for week, label in zip(weeks, labels):
        if spans and spans[-1]["label"] == label:
            spans[-1]["to_week"] = week["week"]
        else:
            spans.append({"label": label, "from_week": week["week"], "to_week": week["week"]})
    return spans


# --------------------------------------------------------------------------
# Reading back
# --------------------------------------------------------------------------


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    months, cursor = [], start.replace(day=1)
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def calendar_items(payloads: Iterable[Any]) -> list[dict[str, Any]]:
    """Workout entries across several month payloads, each counted once."""
    seen: dict[Any, dict[str, Any]] = {}
    for payload in payloads:
        items = payload if isinstance(payload, list) else (payload or {}).get("calendarItems", [])
        for item in items or []:
            if (item.get("itemType") or "workout") != "workout":
                continue
            key = item.get("id") or (item.get("date"), item.get("workoutId"))
            seen.setdefault(key, item)
    return list(seen.values())


def plans_in(items: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        code = label_of(item.get("title") or item.get("workoutName"))
        if code:
            grouped[code].append(dict(item))
    return {k: v for k, v in grouped.items() if len(v) >= MIN_PLAN_SESSIONS}


def pick(plans: Mapping[str, list[dict[str, Any]]], today: date) -> str | None:
    """The plan someone means when they say 'my plan': the one still running."""
    def key(code: str) -> tuple[int, str]:
        dates = sorted(i.get("date") or "" for i in plans[code])
        upcoming = [d for d in dates if d >= today.isoformat()]
        # Running plans first, soonest next session first; finished ones by recency.
        return (0, upcoming[0]) if upcoming else (1, "".join(chr(255 - ord(c)) for c in dates[-1]))
    return min(plans, key=key) if plans else None


CALENDAR_WEEKS_BACK = 3
# Far enough to reach race day in most blocks. Weeks with nothing scheduled
# aren't shown, so a coach who plans a week at a time gets a short card.
CALENDAR_WEEKS_AHEAD = 16


def around(items: Iterable[Mapping[str, Any]], today: date) -> list[dict[str, Any]]:
    """Scheduled workouts from three weeks back to the last one ahead, whole weeks."""
    start = monday_of(today) - timedelta(weeks=CALENDAR_WEEKS_BACK)
    end = monday_of(today) + timedelta(weeks=CALENDAR_WEEKS_AHEAD + 1)
    return [dict(i) for i in items if (d := _day(i.get("date"))) and start <= d < end]


def covers_week(sessions: Iterable[Mapping[str, Any]], today: date) -> bool:
    """Whether a plan is running this week: begun by Sunday, not over before Monday."""
    days = [d for i in sessions if (d := _day(i.get("date")))]
    monday = monday_of(today)
    return bool(days) and min(days) <= monday + timedelta(days=6) and max(days) >= monday


def plan_context(
    plans: Mapping[str, Sequence[Mapping[str, Any]]],
    window: Iterable[Mapping[str, Any]],
    today: date,
) -> dict[str, Any] | None:
    """The plan made here that the calendar around now belongs to, if any.

    One running this week comes first, with its week number; otherwise the
    soonest one starting later. Only plans with sessions on the calendar
    shown count, so a plan that finished, or one months off, names nothing.
    """
    shown = {label_of(i.get("title") or i.get("workoutName")) for i in window} - {None}
    candidates = {code: plans[code] for code in plans if code in shown}
    if not candidates:
        return None
    running = {code: s for code, s in candidates.items() if covers_week(s, today)}
    ahead = {code: s for code, s in candidates.items() if min(i["date"][:10] for i in s) > today.isoformat()}
    if running:
        code = pick(running, today)
    elif ahead:
        code = min(ahead, key=lambda c: min(i["date"][:10] for i in ahead[c]))
    else:
        return None
    days = sorted(d for i in plans[code] if (d := _day(i.get("date"))))
    first, last = days[0], days[-1]
    return drop_empty({
        "label": code,
        "starts": first.isoformat(),
        "ends": last.isoformat(),
        "week": (monday_of(today) - monday_of(first)).days // 7 + 1 if code in running else None,
        "weeks_total": (monday_of(last) - monday_of(first)).days // 7 + 1,
        "running": code in running,
    }) | {"running": code in running}


LOG_WEEKS = 4
# A run shorter than this doesn't count for "fastest": a 1 km jog to the
# shop would otherwise win.
FASTEST_MIN_METRES = 3000
# A ride under this is a commute or a spin, not a fair "fastest".
FASTEST_MIN_RIDE_METRES = 15000


def summarise_log(activities: Iterable[Mapping[str, Any]], today: date, weeks: int = LOG_WEEKS) -> dict[str, Any]:
    """What was done over the last few weeks, for someone with nothing scheduled.

    Every week is listed, empty ones too, so a gap shows as a gap rather than
    the trend skipping over it. The same shape the plan view already draws
    weeks from, marked as a log.

    Runs and rides are totalled apart, each with its own longest and fastest,
    so a cyclist sees kilometres ridden rather than a row of zeros for running,
    and someone who does both sees both. "primary" is whichever took more of
    the time; the top-level longest and fastest are its.
    """
    monday = monday_of(today)
    first = monday - timedelta(weeks=weeks - 1)
    rows = []
    for a in activities:
        day = _day(a.get("startTimeLocal"))
        if not day or not first <= day <= today:
            continue
        key = (a.get("activityType") or {}).get("typeKey")
        sport = "ride" if is_ride(key) else category(key)
        metres = float(a.get("distance") or 0)
        seconds = float(a.get("duration") or 0)
        # Pace from moving time, as Garmin shows it; a stop at a crossing
        # shouldn't slow an easy run down on paper.
        moving = float(a.get("movingDuration") or 0) or seconds
        rows.append(drop_empty({
            "date": day.isoformat(),
            "day": day.strftime("%a"),
            "name": a.get("activityName") or "Activity",
            "sport": sport,
            "type": sport,
            "status": "logged",
            "actual_km": round(metres / 1000, 2) if metres else None,
            "actual": duration(seconds) if seconds else None,
            "pace": pace_per_km(metres, moving) if sport == "run" else None,
            "speed": f"{metres / moving * 3.6:.1f} km/h" if sport == "ride" and metres and moving else None,
            "activity_id": a.get("activityId"),
            "secs": round(moving) or None,
            "_metres": metres,
            "_seconds": moving,
        }))
    rows.sort(key=lambda r: (r["date"], str(r.get("activity_id"))))

    def km(rs: list[dict[str, Any]]) -> float:
        return round(sum(r["_metres"] for r in rs) / 1000, 1)

    out_weeks = []
    for n in range(weeks):
        start = first + timedelta(weeks=n)
        end = start + timedelta(days=7)
        here = [r for r in rows if start.isoformat() <= r["date"] < end.isoformat()]
        runs = [r for r in here if r["sport"] == "run"]
        rides = [r for r in here if r["sport"] == "ride"]
        out_weeks.append({
            "week": n + 1,
            "starts": start.isoformat(),
            "current": start == monday,
            "run_km": km(runs),
            "runs": len(runs),
            "ride_km": km(rides),
            "rides": len(rides),
            "sessions": [{k: v for k, v in r.items() if not k.startswith("_")} for r in here],
        })

    runs = [r for r in rows if r["sport"] == "run" and r["_metres"] > 0]
    rides = [r for r in rows if r["sport"] == "ride" and r["_metres"] > 0]
    run_secs, ride_secs = sum(r["_seconds"] for r in runs), sum(r["_seconds"] for r in rides)
    primary = "ride" if ride_secs > run_secs else "run"
    # Both sports done: the card shows both, side by side, by time.
    mixed = bool(runs) and bool(rides)
    lead = rides if primary == "ride" else runs
    longest = max(lead, key=lambda r: r["_metres"], default=None)
    if primary == "ride":
        timed = [r for r in rides if r["_metres"] >= FASTEST_MIN_RIDE_METRES and r["_seconds"] > 0]
    else:
        timed = [r for r in runs if r["_metres"] >= FASTEST_MIN_METRES and r["_seconds"] > 0]
    fastest = min(timed, key=lambda r: r["_seconds"] / r["_metres"], default=None)
    clean = lambda r: {k: v for k, v in r.items() if not k.startswith("_")} if r else None
    return drop_empty({
        "source": "log",
        "starts": first.isoformat(),
        "ends": today.isoformat(),
        "primary": primary,
        "mixed": mixed or None,
        "hours_a_week": round((run_secs + ride_secs) / 3600 / weeks, 1) if mixed else None,
        "weeks": out_weeks,
        "run_km": km(runs),
        "runs": len(runs),
        "ride_km": km(rides),
        "rides": len(rides),
        "average_week_km": round(km(lead) / weeks, 1),
        "longest": clean(longest),
        "fastest": clean(fastest),
        "note": (
            "Nothing is scheduled on the Garmin calendar, so this is what was done "
            f"over the last {weeks} weeks, "
            f"{'riding and running alike' if mixed else 'led by riding' if primary == 'ride' else 'led by running'}. "
            "create_plan builds a plan from here; ask the user's goal and the date "
            "of their race or event first."
        ),
    })


def planned_metres(workout: Mapping[str, Any]) -> float:
    """How far a workout is meant to be: Garmin's own figure, else its steps added up."""
    given = float(workout.get("estimatedDistanceInMeters") or 0)
    return given if given > 0 else planned_distance_m(dict(workout))


def summarise_calendar(
    items: Sequence[Mapping[str, Any]],
    activities: Sequence[Mapping[str, Any]],
    today: date,
    *,
    planned_seconds: Mapping[Any, float] | None = None,
    planned_metres: Mapping[Any, float] | None = None,
    weekly_km: bool = False,
    shapes: Mapping[Any, Sequence[Mapping[str, Any]]] | None = None,
    phases: Mapping[Any, str] | None = None,
) -> dict[str, Any]:
    """The calendar read as a plan: what was scheduled around now, against what was run.

    The same shape as summarise, marked as coming from the calendar, so the
    same view draws it. There is no code and no goal; weeks are dated rather
    than numbered, because week 1 would only mean the first week shown.
    """
    result = summarise("", items, activities, today, planned_seconds=planned_seconds,
                       planned_metres=planned_metres, weekly_km=weekly_km, shapes=shapes, phases=phases)
    result.pop("label", None)
    for key in ("current_week", "weeks_total", "finished", "phases"):
        result.pop(key, None)
    result["source"] = "calendar"
    result["note"] = (
        "These are the workouts scheduled on the Garmin calendar, from "
        f"{CALENDAR_WEEKS_BACK} weeks back to up to {CALENDAR_WEEKS_AHEAD} ahead, whoever "
        "put them there. The same ids move or retune them."
    )
    return result


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _add_distance(week: dict[str, Any], actual: Sequence[Actual], extra: Sequence[Mapping[str, Any]]) -> None:
    """Planned km against run km for one week, and the runs nobody planned."""
    start = date.fromisoformat(week["starts"])
    end = start + timedelta(days=7)
    planned = sum(s.get("planned_km") or 0 for s in week["sessions"])
    run = sum(a.metres for a in actual if a.sport == "run" and start <= a.day < end)
    week["planned_km"] = round(planned, 1) if planned else None
    week["run_km"] = round(run / 1000, 1)
    week["extra"] = [
        drop_empty({
            "date": row["date"],
            "day": date.fromisoformat(row["date"]).strftime("%a"),
            "name": row["name"],
            "sport": row.get("sport"),
            "actual_km": row.get("actual_km"),
            "actual": duration(row.get("actual_seconds")),
        })
        for row in extra
        if start <= date.fromisoformat(row["date"]) < end
    ] or None
    for key in ("planned_km", "extra"):
        if week[key] is None:
            del week[key]


def summarise(
    code: str,
    items: Sequence[Mapping[str, Any]],
    activities: Sequence[Mapping[str, Any]],
    today: date,
    *,
    goal: str | None = None,
    planned_seconds: Mapping[Any, float] | None = None,
    planned_metres: Mapping[Any, float] | None = None,
    weekly_km: bool = False,
    shapes: Mapping[Any, Sequence[Mapping[str, Any]]] | None = None,
    phases: Mapping[Any, str] | None = None,
) -> dict[str, Any]:
    """The plan week by week, each session marked done, missed, today or ahead.

    With weekly_km, each week also carries the distance planned against the
    distance run, counting every run that week, and the runs that weren't on
    the plan.
    """
    planned_seconds = planned_seconds or {}
    planned_metres = planned_metres or {}
    shapes = shapes or {}
    phases = phases or {}
    by_activity = {a.get("activityId"): a for a in activities if a.get("activityId") is not None}
    sessions = sorted(
        (i for i in items if _day(i.get("date"))), key=lambda i: (i.get("date"), str(i.get("id")))
    )
    if not sessions:
        raise PlanError(f"No sessions found for plan {code}.")
    first, last = _day(sessions[0]["date"]), _day(sessions[-1]["date"])

    planned = [
        Planned(
            day=_day(i["date"]),
            name=str(i.get("id")),  # the schedule id, so each verdict maps back to its row
            sport=category(i.get("sportTypeKey") or "running"),
            seconds=float(planned_seconds.get(i.get("workoutId"), 0.0)),
            workout_id=i.get("workoutId"),
            title=strip_tag(i.get("title") or i.get("workoutName") or ""),
        )
        for i in sessions
    ]
    actual = [
        Actual(
            day=_day(a.get("startTimeLocal")),
            name=a.get("activityName") or "Activity",
            sport=category((a.get("activityType") or {}).get("typeKey")),
            seconds=float(a.get("duration") or 0),
            metres=float(a.get("distance") or 0),
            activity_id=a.get("activityId"),
        )
        for a in activities
        if _day(a.get("startTimeLocal"))
        and (monday_of(first) if weekly_km else first - timedelta(days=1))
        <= _day(a.get("startTimeLocal")) <= last + timedelta(days=1)
    ]
    verdict = match(planned, actual, today)
    done = {row["name"]: row for row in verdict.done}
    missed = {row["name"] for row in verdict.missed}

    week_one = monday_of(first)
    weeks: dict[int, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for item in sessions:
        day = _day(item["date"])
        schedule_id = item.get("id")
        hit = done.get(str(schedule_id))
        if hit:
            status = "done" if hit["done_on"] == item["date"][:10] else "done, moved"
        elif str(schedule_id) in missed:
            status = "today" if day == today else "missed"
        else:
            status = "today" if day == today else "ahead"
        number = (monday_of(day) - week_one).days // 7 + 1
        wid = item.get("workoutId")
        sport_key = item.get("sportTypeKey") or "running"
        shape = shapes.get(wid)
        name = strip_tag(item.get("title") or item.get("workoutName") or "Workout")
        done_activity = by_activity.get(hit.get("activity_id")) if hit else None
        moving = float((done_activity or {}).get("movingDuration") or (done_activity or {}).get("duration") or 0)
        row = drop_empty(
            {
                "date": day.isoformat(),
                "day": day.strftime("%a"),
                "name": name,
                "type": session_type(name, sport_key, shape),
                "phase": phases.get(wid),
                "shape": list(shape) if shape else None,
                "status": status,
                "actual_pace": (
                    pace_per_km(float(done_activity.get("distance") or 0), moving)
                    if done_activity and category(sport_key) == "run" and moving else None
                ),
                "actual_bpm": (
                    round(float(done_activity["averageHR"]))
                    if done_activity and done_activity.get("averageHR") else None
                ),
                "done_on": hit["done_on"] if hit and hit["done_on"] != item["date"][:10] else None,
                "activity_id": hit.get("activity_id") if hit else None,
                "actual_km": hit.get("actual_km") if hit else None,
                "actual": duration(hit.get("actual_seconds")) if hit else None,
                "workout_id": item.get("workoutId"),
                "schedule_id": schedule_id,
                # Distance only means something for a run: a timed strength
                # session converted at running pace would add phantom km.
                "planned_km": (
                    round(planned_metres[item.get("workoutId")] / 1000, 1)
                    if weekly_km and planned_metres.get(item.get("workoutId"))
                    and category(item.get("sportTypeKey") or "running") == "run"
                    else None
                ),
            }
        )
        rows.append(row)
        week = weeks.setdefault(
            number, {"week": number, "starts": monday_of(day).isoformat(), "sessions": []}
        )
        week["sessions"].append(row)

    for week in weeks.values():
        statuses = [s["status"] for s in week["sessions"]]
        week["done"] = sum(s.startswith("done") for s in statuses)
        week["missed"] = statuses.count("missed")
        week["planned"] = len(statuses)
        week["current"] = week["starts"] == monday_of(today).isoformat()
        if weekly_km:
            _add_distance(week, actual, verdict.extra)

    past = [r for r in rows if r["status"] in ("done", "done, moved", "missed")]
    completed = sum(r["status"].startswith("done") for r in past)
    upcoming = [r for r in rows if r["status"] in ("today", "ahead")]
    week_of_today = (monday_of(today) - week_one).days // 7 + 1
    recent_missed = [
        r for r in rows
        if r["status"] == "missed" and _day(r["date"]) >= today - timedelta(days=7)
    ]

    ordered_weeks = [weeks[k] for k in sorted(weeks)]
    return drop_empty(
        {
            "label": code,
            "goal": goal,
            "phases": phase_spans(ordered_weeks),
            "starts": first.isoformat(),
            "ends": last.isoformat(),
            "weeks_total": (monday_of(last) - week_one).days // 7 + 1,
            "current_week": week_of_today if first <= today <= last else None,
            "finished": today > last or None,
            "sessions_total": len(rows),
            "completed": completed,
            "missed": len(past) - completed,
            "remaining": len(upcoming),
            "completion_percent": round(completed / len(past) * 100) if past else None,
            "next_session": upcoming[0] if upcoming else None,
            "review": drop_empty(
                {
                    "missed_last_7_days": [
                        {"date": r["date"], "name": r["name"], "workout_id": r.get("workout_id"),
                         "schedule_id": r.get("schedule_id")}
                        for r in recent_missed
                    ] or None,
                    "if_done_elsewhere": (
                        "Garmin only knows what a watch or a synced app recorded. If "
                        "the user says they did a missed session anyway, take their "
                        "word for it and treat it as done; ask what they did only if "
                        "it matters for what comes next, and suggest recording it on "
                        "the watch next time so the plan keeps count."
                    ) if recent_missed else None,
                    "this_week_remaining": [
                        r["name"] for r in upcoming if monday_of(_day(r["date"])) == monday_of(today)
                    ] or None,
                }
            ) or None,
            "weeks": ordered_weeks,
            "how_to_adapt": (
                "To move a session: unschedule_workout with its schedule_id, then "
                "schedule_workout with its workout_id on the new date. To change "
                "paces or structure: update_workout with its workout_id; the plan "
                "code in the name is kept. A session counts as done if it was run "
                "within a day either side of its date, or any day that week if started "
                "from its workout on the watch; strength counts any day of its own "
                "week, whatever its length. Check get_readiness before "
                "moving a hard session, and get_fitness before retuning paces."
            ),
        }
    )
