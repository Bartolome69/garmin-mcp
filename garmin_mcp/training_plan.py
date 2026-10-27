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
    ftp: float | None = None,
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
                raw.get("pool_length_meters"), ftp,
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

# A hard session is one of two kinds, the same for a run or a ride, so the
# card reads by effort whatever the sport: threshold (tempo, threshold,
# sub-threshold, sweet spot, steady and marathon-pace work) or intervals
# (VO2 max, speed, hills and short reps). The name says it when it can.
VO2_NAME_RE = re.compile(r"vo2|v02|anaerobic|tabata|track|\bspeed\b|\bmax\b", re.IGNORECASE)
SPRINT_NAME_RE = re.compile(r"sprint", re.IGNORECASE)
THRESHOLD_NAME_RE = re.compile(
    r"threshold|tempo|sub-?t\b|cruise|sweet ?spot|\bss\b|\bftp\b|over-?unders?|marathon pace|\bmp\b|\bhmp\b"
    r"|steady|progression|race pace|\blt\b|lactate|norwegian|singles",
    re.IGNORECASE,
)
INTERVALS_NAME_RE = re.compile(
    r"hills?\b|fartlek|\b[2-8]00s\b|\b[2-8]00 ?m\b|\b[35]k pace|mile pace|\b(?:15|20|30|40|45)\s?/\s?(?:15|20|30)\b",
    re.IGNORECASE,
)
REPS_NAME_RE = re.compile(r"intervals?|reps|\d\s?[x\u00d7]\s?\d", re.IGNORECASE)
# Said to be gentle, so no structure makes it hard: run-walks above all,
# and an easy run with strides or a few sprints, "Easy 8k + 6x20s strides".
GENTLE_NAME_RE = re.compile(r"easy|recovery|shake-?out|walk|strides|aerobic|endurance|zone ?[12]\b|\bz[12]\b", re.IGNORECASE)

# Read from the steps when the name doesn't say. Work has to amount to
# something, so strides or a few sprints don't make a run hard.
EFFORT_MIN_WORK_S = 240
EFFORT_MIN_WORK_SHARE = 0.1
# Reps this short with recoveries this long are intervals: 5 x 3 min with
# 2-3 min jogs. Longer reps or short recoveries are threshold, which keeps
# Norwegian singles (10 x 1 km, 60 s jogs) as threshold. A ride's VO2 max
# reps run longer than a run's.
INTERVAL_REP_S = {"run": 300, "ride": 480}
INTERVAL_REST_RATIO = 0.5
SHORT_REP_S = 120
SHORT_REP_REST_RATIO = 0.4
# A body step this much above the gentlest one is work, by target kind.
WORK_LEVEL_RATIO = {"pace": WORK_SPEED_RATIO, "power": 1.12, "hr": 1.06}
WARMUP_LEVEL_RATIO = {"pace": 1.15, "power": 1.25, "hr": 1.12}
# Distance steps without a pace target are timed at these speeds.
RIDE_MPS = 8.0


def _speed(step: Mapping[str, Any]) -> float | None:
    key = ((step.get("targetType") or {}).get("workoutTargetTypeKey") or "").lower()
    one, two = step.get("targetValueOne"), step.get("targetValueTwo")
    if key == "pace.zone" and one and two:
        return (float(one) + float(two)) / 2
    return None


def workout_shape(workout: Mapping[str, Any] | None, easy_mps: float | None = None,
                  timed: bool = False) -> list[dict[str, Any]] | None:
    """A run or ride as segments the card draws: easy, work and recovery, each by its distance.

    Built from the structured steps, repeats unrolled, time steps converted at
    their pace target (an easy pace when they have none; a ride's at a steady
    speed, so its parts are drawn by time). Work is what sits inside a repeat,
    or a body step clearly harder than the gentlest one, so a long run with
    marathon-pace stretches shows them; or a tempo's one block between its
    warm-up and cool-down, when its pace or target says so. The warm-up and
    cool-down are marked "part" and kept apart from the body, so the card can
    tell them from the work in a hard session whose targets don't say. A
    strength session or a swim has no shape worth drawing this way.

    With easy_mps, untargeted steps are converted at that speed (the runner's
    own easy pace) instead of the default; timed adds each segment's seconds,
    and "by": "time" on segments whose step was set by time.
    """
    if not workout:
        return None
    sport = ((workout.get("sportType") or {}).get("sportTypeKey") or "running").lower()
    ride = is_ride(sport)
    if category(sport) != "run" and not ride:
        return None
    flat: list[tuple[str, float, tuple[str, float] | None, bool, float, bool]] = []

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
            level = _level(step)
            speed = RIDE_MPS if ride else (_speed(step) or easy_mps or DEFAULT_MPS)
            if cond == "distance":
                metres, seconds = value, value / speed
            elif cond == "time":
                metres, seconds = value * speed, value
            else:
                metres = seconds = 0.0
            flat.append((kind, metres, level, in_repeat, seconds, cond == "time"))

    for segment in workout.get("workoutSegments") or []:
        walk(segment.get("workoutSteps") or [], False)
    if not flat:
        return None

    ends = ("warmup", "cooldown")
    body = [f for f in flat if f[0] not in ends + ("recovery", "rest")]
    any_repeat = any(f[3] for f in body)
    flanked = any(f[0] in ends for f in flat)
    lowest: dict[str, float] = {}
    for _, _, level, _, _, _ in body:
        if level and level[0] in WORK_LEVEL_RATIO:
            lowest[level[0]] = min(lowest.get(level[0], level[1]), level[1])
    warm = {level[0]: level[1] for kind, _, level, _, _, _ in flat if kind in ends and level}

    def classify(kind: str, level: tuple[str, float] | None, in_repeat: bool) -> str:
        if kind in ends:
            return "easy"
        if kind in ("recovery", "rest"):
            return "rec"
        if in_repeat:
            return "work"
        if level and level[0] == "zone":
            # A heart rate zone: 3 and up is work, in a session built round it.
            return "work" if level[1] >= 3 and flanked and len(body) == 1 and not any_repeat else "easy"
        if not level or level[0] not in WORK_LEVEL_RATIO:
            return "easy"
        unit, value = level
        ratio = WORK_LEVEL_RATIO[unit]
        if len(body) > 1 or any_repeat:
            return "work" if value >= lowest[unit] * ratio else "easy"
        # One block on its own: work when it's run well above an easy pace, or
        # above its own warm-up, with a warm-up or cool-down around it.
        if not flanked:
            return "easy"
        if unit == "pace" and not ride and value >= DEFAULT_MPS * ratio:
            return "work"
        return "work" if unit in warm and value >= warm[unit] * ratio else "easy"

    segments: list[dict[str, Any]] = []
    for kind, metres, level, in_repeat, seconds, by_time in flat:
        km = metres / 1000
        if km < 0.05:
            continue
        k = classify(kind, level, in_repeat)
        part = "wu" if kind == "warmup" else "cd" if kind == "cooldown" else None
        last = segments[-1] if segments else None
        if last and last["k"] == k == "easy" and last.get("part") == part:
            last["km"] = round(last["km"] + km, 2)
            if timed:
                last["secs"] = round(last["secs"] + seconds)
                if not by_time:
                    last.pop("by", None)
        else:
            segments.append({"k": k, "km": round(km, 2), **({"part": part} if part else {}),
                             **({"secs": round(seconds)} if timed else {}),
                             **({"by": "time"} if timed and by_time else {})})
    return segments[:SHAPE_MAX_SEGMENTS] or None


# The sport as the card's small icon shows it, from Garmin's type key. Order
# matters: "trail_running" is a run, "cardio_training" isn't strength.
_KINDS = (
    ("swim", "swim"), ("yoga", "yoga"), ("pilates", "pilates"), ("mobility", "yoga"), ("breath", "yoga"),
    ("hiit", "hiit"), ("cardio", "cardio"), ("strength", "strength"), ("hik", "hike"), ("walk", "walk"),
    ("run", "run"),
)


def activity_kind(type_key: Any) -> str:
    """run, ride, strength, swim, yoga, pilates, hiit, cardio, hike, walk or other."""
    key = str(type_key or "").lower()
    if is_ride(key):
        return "ride"
    return next((kind for word, kind in _KINDS if word in key), "other")


def _level(step: Mapping[str, Any]) -> tuple[str, float] | None:
    """How hard a step's target asks for, as (kind, value): higher is harder."""
    key = ((step.get("targetType") or {}).get("workoutTargetTypeKey") or "").lower()
    one, two = step.get("targetValueOne"), step.get("targetValueTwo")
    if key == "pace.zone" and one and two:
        return "pace", (float(one) + float(two)) / 2
    if key == "power.zone" and one and two:
        return "power", (float(one) + float(two)) / 2
    if key == "heart.rate.zone" and one and two:
        return "hr", (float(one) + float(two)) / 2
    if key == "heart.rate.zone" and step.get("zoneNumber"):
        return "zone", float(step["zoneNumber"])
    return None


def workout_effort(workout: Mapping[str, Any] | None) -> str | None:
    """threshold or intervals for a hard run or ride, from its steps; None when it isn't one.

    Work is what sits inside a repeat, or a body step clearly harder than the
    gentlest one. Enough of it makes the session hard; then the reps decide
    the kind: short reps with long recoveries are intervals, the rest threshold.
    """
    if not workout:
        return None
    sport = ((workout.get("sportType") or {}).get("sportTypeKey") or "running").lower()
    kind_of_sport = "run" if category(sport) == "run" else "ride" if is_ride(sport) else None
    if not kind_of_sport:
        return None
    mps = DEFAULT_MPS if kind_of_sport == "run" else RIDE_MPS
    flat: list[tuple[str, float, tuple[str, float] | None, bool]] = []

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
            level = _level(step)
            if cond == "time":
                seconds = value
            elif cond == "distance":
                speed = level[1] if level and level[0] == "pace" else mps
                seconds = value / speed if speed else 0.0
            else:
                seconds = 0.0
            flat.append((kind, seconds, level, in_repeat))

    for segment in workout.get("workoutSegments") or []:
        walk(segment.get("workoutSteps") or [], False)
    gentle = ("warmup", "cooldown", "recovery", "rest")
    body = [f for f in flat if f[0] not in gentle]
    if not body:
        return None
    # The gentlest target among the body steps, and among the warm-up and
    # cool-down, so one tempo block between them reads as work. A warm-up is
    # often slower than easy running, so beating it takes more.
    lowest: dict[str, float] = {}
    lowest_gentle: dict[str, float] = {}
    for kind, _, level, _ in flat:
        if level and level[0] in WORK_LEVEL_RATIO:
            into = lowest_gentle if kind in ("warmup", "cooldown") else lowest if kind not in gentle else None
            if into is not None:
                into[level[0]] = min(into.get(level[0], level[1]), level[1])

    def is_work(step: tuple[str, float, tuple[str, float] | None, bool]) -> bool:
        kind, _, level, in_repeat = step
        if kind in gentle:
            return False
        if level and level[0] == "zone" and level[1] <= 2:
            return False
        if in_repeat:
            # Reps without any target are a run-walk or drills, not a session.
            return level is not None
        if not level or level[0] not in WORK_LEVEL_RATIO:
            return False
        unit, value = level
        return value >= lowest[unit] * WORK_LEVEL_RATIO[unit] or (
            unit in lowest_gentle and value >= lowest_gentle[unit] * WARMUP_LEVEL_RATIO[unit]
        )

    work = [is_work(f) for f in flat]
    total = sum(f[1] for f in flat)
    work_s = sum(f[1] for f, w in zip(flat, work) if w)
    if work_s < EFFORT_MIN_WORK_S or not total or work_s / total < EFFORT_MIN_WORK_SHARE:
        return None

    # Reps: runs of work steps, each with the recovery that follows it up to
    # the next rep. A warm-up or cool-down isn't a recovery.
    reps: list[tuple[float, float, bool]] = []
    repeated = False
    i = 0
    while i < len(flat):
        if not work[i]:
            i += 1
            continue
        rep, j = 0.0, i
        while j < len(flat) and work[j]:
            rep += flat[j][1]
            repeated = repeated or flat[j][3]
            j += 1
        rest, k, known = 0.0, j, True
        while k < len(flat) and not work[k] and flat[k][0] not in ("warmup", "cooldown"):
            rest += flat[k][1]
            known = known and flat[k][1] > 0
            k += 1
        reps.append((rep, rest, known))
        i = j
    if not repeated or len(reps) < 2:
        return "threshold"
    rep_s = sorted(r for r, _, _ in reps)[len(reps) // 2]
    ratios = sorted(rest / rep for rep, rest, known in reps if rep and known and rest > 0)
    ratio = ratios[len(ratios) // 2] if ratios else None
    if rep_s <= SHORT_REP_S and (ratio is None or ratio >= SHORT_REP_REST_RATIO):
        return "intervals"
    if rep_s <= INTERVAL_REP_S[kind_of_sport] and (ratio is None or ratio >= INTERVAL_REST_RATIO):
        return "intervals"
    return "threshold"


def effort_from_name(name: str | None) -> str | None:
    """What a session's name says about its effort: intervals, threshold, reps, gentle, or nothing."""
    n = name or ""
    if GENTLE_NAME_RE.search(n) and not (VO2_NAME_RE.search(n) or THRESHOLD_NAME_RE.search(n)):
        return "gentle"
    if VO2_NAME_RE.search(n) or SPRINT_NAME_RE.search(n):
        return "intervals"
    if THRESHOLD_NAME_RE.search(n):
        return "threshold"
    if INTERVALS_NAME_RE.search(n):
        return "intervals"
    if REPS_NAME_RE.search(n):
        return "reps"
    return None


def session_type(name: str | None, sport_key: str | None, effort: str | None = None) -> str:
    """What a session is, for its colour: by effort for runs and rides, the sport otherwise.

    easy, long, threshold, intervals or race for a run; the same for a ride,
    with ride for an easy one; strength, swim or other for the rest. The name
    says race and long, and the kind of hard session when it names one; the
    steps (effort, from workout_effort) say it otherwise.
    """
    kind = activity_kind(sport_key or "running")
    if kind in ("strength", "swim"):
        return kind
    ride = kind == "ride"
    # Yoga, cardio, a hike: the card names these by their sport.
    if kind != "run" and not ride:
        return "other"
    base = "ride" if ride else "easy"
    n = name or ""
    if RACE_RE.search(n) and not NOT_RACE_RE.search(n):
        return "race"
    if re.search(r"\blong\b", n, re.IGNORECASE):
        return "long"
    named = effort_from_name(n)
    if named in ("intervals", "threshold"):
        return named
    if named == "gentle":
        return base
    if effort:
        return effort
    return "intervals" if named == "reps" else base


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
    def span(label: str) -> dict[str, Any]:
        days = sorted(d for i in plans[label] if (d := _day(i.get("date"))))
        first, last = days[0], days[-1]
        return {
            "label": label,
            "starts": first.isoformat(),
            "ends": last.isoformat(),
            "weeks_total": (monday_of(last) - monday_of(first)).days // 7 + 1,
        }

    context = span(code)
    if code in running:
        context["week"] = (monday_of(today) - monday_of(date.fromisoformat(context["starts"]))).days // 7 + 1
        # A block that starts once this one is over is named too, so a
        # one-week block ending on Sunday doesn't hide the one from Monday.
        following = {c: s for c, s in ahead.items() if c != code}
        if following:
            context["upcoming"] = span(min(following, key=lambda c: min(i["date"][:10] for i in following[c])))
    return drop_empty(context) | {"running": code in running}


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
            "kind": activity_kind(key),
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
    efforts: Mapping[Any, str | None] | None = None,
) -> dict[str, Any]:
    """The calendar read as a plan: what was scheduled around now, against what was run.

    The same shape as summarise, marked as coming from the calendar, so the
    same view draws it. There is no code and no goal; weeks are dated rather
    than numbered, because week 1 would only mean the first week shown.
    """
    result = summarise("", items, activities, today, planned_seconds=planned_seconds,
                       planned_metres=planned_metres, weekly_km=weekly_km, shapes=shapes, phases=phases,
                       efforts=efforts)
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
            "kind": row.get("kind"),
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
    efforts: Mapping[Any, str | None] | None = None,
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
    efforts = efforts or {}
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
            kind=activity_kind((a.get("activityType") or {}).get("typeKey")),
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
                "type": session_type(name, sport_key, efforts.get(wid)),
                "sport": "ride" if is_ride(sport_key) else category(sport_key),
                "kind": activity_kind(sport_key),
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


# --------------------------------------------------------------------------
# Where someone is with their training, from what the plan card found, and
# the one next step worth offering them. Claude offers it in a line at the end
# of its answer; nothing more is fetched until the person asks or says yes.

# How far ahead a race on the calendar still counts as one to train for.
RACE_WEEKS_AHEAD = 26
# Runs (or rides) in the log's four weeks that make someone a regular: about
# one a week.
REGULAR_IN_LOG = 4


def upcoming_events(payloads: Iterable[Any], today: date, weeks: int = RACE_WEEKS_AHEAD) -> list[dict[str, Any]]:
    """Races and other events on the Garmin calendar in the weeks ahead, soonest first."""
    found: dict[Any, dict[str, Any]] = {}
    last = today + timedelta(weeks=weeks)
    for payload in payloads:
        items = payload if isinstance(payload, list) else (payload or {}).get("calendarItems", [])
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            if item.get("itemType") not in ("event", "race") and not item.get("isRace"):
                continue
            day = _day(item.get("date"))
            if not day or not today <= day <= last:
                continue
            key = item.get("id") or (item.get("date"), item.get("title"))
            found.setdefault(key, {
                "name": str(item.get("title") or "").strip()[:80] or "Your race",
                "date": day.isoformat(),
                "weeks_away": (day - today).days // 7,
            })
    return sorted(found.values(), key=lambda e: e["date"])


def _when(weeks_away: int) -> str:
    if weeks_away <= 0:
        return "this week"
    if weeks_away == 1:
        return "next week"
    return f"in {weeks_away} weeks"


def situation(answer: Any, today: date, events: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any] | None:
    """What the person is up to, and one next step to offer, from a get_plan answer.

    A plan on the calendar is under way, about to start, or just finished.
    With nothing scheduled, a race ahead makes them about to start one;
    regular running (or riding) makes the question where their fitness is;
    little of either, getting going. None for an answer it can't read.
    """
    if not isinstance(answer, dict):
        return None
    race = dict(events[0]) if events else None
    ride = answer.get("primary") == "ride"
    sport = "riding" if ride else "running"

    def found(stage: str, offer: str) -> dict[str, Any]:
        return drop_empty({"stage": stage, "race": race, "offer": offer})

    if "error" in answer or answer.get("source") == "log":
        if race:
            return found(
                "race_ahead",
                f"{race['name']} is {_when(race['weeks_away'])}. Want me to build a block "
                f"for it from where your {sport} is now?",
            )
        regular = (answer.get("rides") if ride else answer.get("runs")) or 0
        if "error" not in answer and regular >= REGULAR_IN_LOG:
            # Someone training regularly with nothing planned is who a plan is
            # for: 9 of the first 61 people built one, and they stay the longest.
            return found(
                "just_training",
                f"Want me to build you a plan for your next race or goal, set from your recent "
                f"{sport} and with every session on your watch?",
            )
        return found("getting_going", f"Want a gentle plan to build your {sport} up from here?")

    weeks = answer.get("weeks")
    if not isinstance(weeks, list):
        return None
    dates = sorted(
        str(s.get("date"))[:10]
        for w in weeks if isinstance(w, Mapping)
        for s in (w.get("sessions") or []) if isinstance(s, Mapping) and s.get("date")
    )
    if not dates:
        return None
    now = today.isoformat()
    ahead = [d for d in dates if d >= now]
    if not ahead:
        return found(
            "block_done",
            "That block is done. Want to see where your fitness has landed, or plan what's next?",
        )
    if len(ahead) == len(dates) and ahead[0] > now:
        start = date.fromisoformat(ahead[0])
        return found(
            "starting",
            f"Your block starts {start.strftime('%A')} {start.day} {start.strftime('%b')}. "
            "Want a quick check of where your fitness is at before it begins?",
        )
    if ahead[0] == now:
        return found("mid_block", "Want me to check today's session against how recovered you are?")
    return found("mid_block", "Want me to check how you're recovering, or move anything around this week?")


# --------------------------------------------------------------------------
# One session, as the workout card draws it
# --------------------------------------------------------------------------

# Sessions the plan is built round, marked on the card as key.
KEY_SESSION_TYPES = ("race", "threshold", "intervals", "long", "sub")
# An easy run this short (a jog to the shops) says little about easy pace.
EASY_PACE_MIN_KM = 3.0


def _pace_seconds(text: Any) -> float | None:
    found = re.match(r"\s*(\d+):(\d{2})", str(text or ""))
    return int(found.group(1)) * 60 + int(found.group(2)) if found else None


def easy_speed(answer: Mapping[str, Any] | None) -> float | None:
    """The runner's own easy pace, in m/s, from the easy runs get_plan has as done.

    The middle of the most recent eight, so one hilly or chatty run doesn't
    move it. None with fewer than two to go on.
    """
    runs = []
    for week in (answer or {}).get("weeks") or []:
        for s in (week or {}).get("sessions") or []:
            pace = _pace_seconds(s.get("actual_pace"))
            if (s.get("status") == "done" and s.get("type") == "easy" and s.get("kind", "run") == "run"
                    and pace and float(s.get("actual_km") or 0) >= EASY_PACE_MIN_KM):
                runs.append((s.get("date") or "", pace))
    paces = sorted(p for _, p in sorted(runs)[-8:])
    if len(paces) < 2:
        return None
    middle = paces[len(paces) // 2] if len(paces) % 2 else (paces[len(paces) // 2 - 1] + paces[len(paces) // 2]) / 2
    return 1000.0 / middle


def session_totals(workout: Mapping[str, Any] | None, easy_mps: float | None = None) -> dict[str, Any] | None:
    """Total distance and time of a workout from its steps, recoveries included, and the reps' target.

    Steps set by distance count as written; steps set by time are converted at
    their pace target, else at easy_mps (the runner's easy pace), else the
    default. Time is the same the other way round. Both are estimates once a
    step has no target or ends on the lap button, and say so.
    """
    if not workout:
        return None
    sport = ((workout.get("sportType") or {}).get("sportTypeKey") or "running").lower()
    ride = is_ride(sport)
    if category(sport) != "run" and not ride:
        secs = float(workout.get("estimatedDurationInSecs") or 0)
        return drop_empty({"minutes": round(secs / 60) if secs else None, "estimated": True}) or None
    metres = seconds = 0.0
    estimated = False
    targets: list[Mapping[str, Any]] = []

    def walk(steps: Sequence[Mapping[str, Any]], in_repeat: bool) -> tuple[float, float]:
        nonlocal estimated
        m = t = 0.0
        for step in sorted(steps or [], key=lambda x: x.get("stepOrder") or 0):
            if step.get("type") == "RepeatGroupDTO" or step.get("numberOfIterations"):
                times = int(step.get("numberOfIterations") or step.get("endConditionValue") or 1)
                im, it = walk(step.get("workoutSteps") or [], True)
                m, t = m + im * times, t + it * times
                continue
            kind = ((step.get("stepType") or {}).get("stepTypeKey") or "other").lower()
            cond = ((step.get("endCondition") or {}).get("conditionTypeKey") or "").lower()
            value = float(step.get("endConditionValue") or 0)
            own = _speed(step)
            speed = RIDE_MPS if ride else (own or easy_mps or DEFAULT_MPS)
            if not own:
                estimated = True
            if cond == "distance":
                m, t = m + value, t + value / speed
            elif cond == "time":
                m, t = m + value * speed, t + value
            else:
                estimated = True
            if kind == "interval" and (in_repeat or own) and _target(step):
                targets.append(step)
        return m, t

    for segment in workout.get("workoutSegments") or []:
        sm, st = walk(segment.get("workoutSteps") or [], False)
        metres, seconds = metres + sm, seconds + st
    if not metres and not seconds:
        return None
    rep = None
    paced = [s for s in targets if ((s.get("targetType") or {}).get("workoutTargetTypeKey") or "").lower() == "pace.zone"]
    if paced:
        speeds = [float(v) for s in paced for v in (s.get("targetValueOne"), s.get("targetValueTwo")) if v]
        quick, slow = 1000.0 / max(speeds), 1000.0 / min(speeds)
        rep = {"label": "Rep pace /km", "value": f"{format_pace(quick)[:-3]}–{format_pace(slow)[:-3]}"
               if round(quick) != round(slow) else format_pace(quick)[:-3]}
    elif targets:
        rep = {"label": "Rep target", "value": _target(targets[0])}
    return drop_empty({
        "km": round(metres / 1000, 1) if metres else None,
        "minutes": round(seconds / 60) if seconds else None,
        "rep": rep,
        "estimated": estimated,
    })


def _rank(s: Mapping[str, Any]) -> tuple[int, int]:
    """Which of a day's sessions leads: a key one, then a run or ride, then the rest; longer first."""
    t = s.get("type")
    return (0 if t in KEY_SESSION_TYPES else 1 if s.get("kind") in ("run", "ride") else 2,
            -int(float(s.get("planned_km") or 0) * 10))


def day_sessions(answer: Mapping[str, Any] | None, day: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """A day's sessions in a get_plan answer, and the one the card leads with."""
    on_day = [s for w in (answer or {}).get("weeks") or [] if isinstance(w, dict)
              for s in w.get("sessions") or [] if isinstance(s, dict) and s.get("date") == day]
    return (min(on_day, key=_rank) if on_day else None), on_day


def session_card(answer: Mapping[str, Any], day: str, today: str, workout: Mapping[str, Any] | None,
                 change: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What the workout card draws for one day, from a get_plan answer and the day's main workout."""
    from .weekly import _name as display_name

    weeks = [w for w in (answer or {}).get("weeks") or [] if isinstance(w, dict)]
    main, on_day = day_sessions(answer, day)
    rank = _rank
    week = next((w for w in weeks if w.get("starts") and w["starts"] <= day < (date.fromisoformat(w["starts"]) + timedelta(days=7)).isoformat()), None)
    starts = date.fromisoformat(week["starts"]) if week else monday_of(date.fromisoformat(day))
    days = []
    for offset in range(7):
        d = (starts + timedelta(days=offset)).isoformat()
        here = [s for s in (week or {}).get("sessions") or [] if s.get("date") == d]
        lead = min(here, key=rank) if here else None
        days.append(drop_empty({"date": d, "type": lead.get("type") if lead else None,
                                "status": lead.get("status") if lead else None}))
    plan = (answer or {}).get("current_plan") or {}
    label = None
    if plan.get("week") and plan.get("weeks_total"):
        this_monday = monday_of(date.fromisoformat(today))
        shift = (starts - this_monday).days // 7
        number = int(plan["week"]) + shift
        if 1 <= number <= int(plan["weeks_total"]):
            label = f"Week {number}"
    easy = easy_speed(answer)
    card = {
        "card": "session",
        "date": day,
        "today": today,
        "week": drop_empty({"label": label, "starts": starts.isoformat(), "done": (week or {}).get("done"),
                            "planned": (week or {}).get("planned"), "days": days}),
    }
    if not main:
        following = min((s for w in weeks for s in w.get("sessions") or [] if s.get("date", "") > day),
                        key=lambda s: (s["date"], _rank(s)), default=None)
        card["rest"] = True
        if following:
            card["next"] = drop_empty({"date": following["date"], "name": display_name(following),
                                       "type": following.get("type")})
        return card
    steps = workout_steps(workout) if workout else None
    card["session"] = drop_empty({
        "name": display_name(main),
        "type": main.get("type"),
        "kind": main.get("kind") or main.get("sport"),
        "status": main.get("status"),
        "key": main.get("type") in KEY_SESSION_TYPES or None,
        "workout_id": main.get("workout_id"),
        "schedule_id": main.get("schedule_id"),
        "planned_km": main.get("planned_km"),
        "actual": drop_empty({k: main.get(k) for k in ("actual_km", "actual_pace", "actual_bpm", "actual")}) or None,
    })
    card["totals"] = session_totals(workout, easy) if workout else None
    card["segments"] = workout_shape(workout, easy_mps=easy, timed=True) if workout else None
    card["steps"] = steps
    card["also"] = [drop_empty({"name": display_name(s), "type": s.get("type"), "kind": s.get("kind"),
                                "km": s.get("planned_km"), "status": s.get("status")})
                    for s in on_day if s is not main] or None
    if change:
        card["change"] = dict(change)
    return drop_empty(card)
