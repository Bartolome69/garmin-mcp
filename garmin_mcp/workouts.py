"""Build Garmin structured workouts from a plain step description.

Garmin's workout JSON is deeply nested and full of magic ids. This module turns
a simple list of steps — the kind you would describe out loud — into the models
garminconnect uploads, and renders one back as text so you can check it before
it lands in your account.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from garminconnect import workout as gw

# Garmin's own workoutTargetTypeId values. Pinned rather than borrowed from
# garminconnect: TargetType only gained these names in 0.3.x (0.2.x has no
# PACE_ZONE and numbers the rest differently), and requirements still allow
# 0.2.25. They match TargetType in current releases.
TARGET_NONE = 1
TARGET_POWER_ZONE = 2
TARGET_HEART_RATE_ZONE = 4
TARGET_PACE_ZONE = 6

from . import swimming

# Sport name -> (workout model, default pace seconds per km used for estimates)
SPORTS: dict[str, tuple[type, float]] = {
    "running": (gw.RunningWorkout, 300.0),
    "cycling": (gw.CyclingWorkout, 120.0),
    "swimming": (gw.SwimmingWorkout, 1500.0),
    "walking": (gw.WalkingWorkout, 720.0),
    "hiking": (gw.HikingWorkout, 900.0),
}

STEP_TYPES = {
    "warmup": (gw.StepType.WARMUP, "warmup", 1),
    "cooldown": (gw.StepType.COOLDOWN, "cooldown", 2),
    "interval": (gw.StepType.INTERVAL, "interval", 3),
    "recovery": (gw.StepType.RECOVERY, "recovery", 4),
    "rest": (gw.StepType.REST, "rest", 5),
}

# A pace target is never narrower than this many seconds per km either side of
# its centre: a single pace is widened into that window, and a tighter range is
# widened to it. Garmin alerts whenever the pace leaves the range, and anything
# tighter beeps constantly on a GPS pace that wobbles by a few seconds anyway.
PACE_WINDOW_SECONDS = 5.0

# A single power target becomes a range this share either side of it, and no
# range is narrower than POWER_MIN_HALF_WATTS either side. Power wobbles from
# second to second far more than pace does, and the head unit alerts every
# time it leaves the range.
POWER_WINDOW = 0.05
POWER_MIN_HALF_WATTS = 5.0
POWER_WATTS = (30.0, 2000.0)
POWER_PERCENT_FTP = (30.0, 200.0)


class WorkoutError(ValueError):
    """Bad workout description; the message is shown to the user."""


def parse_pace(value: Any) -> float:
    """'4:05', '4:05 /km', '6:35 /mi' or 245 -> seconds per kilometre."""
    per_mile = False
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip().lower()
        # A runner in miles says their pace per mile.
        per_mile = bool(re.search(r"/\s*(mi|mile)\b", text))
        text = re.sub(r"/\s*(km|mi|mile)\b", "", text).replace("min", "").strip()
        if ":" in text:
            minutes, _, secs = text.partition(":")
            try:
                seconds = int(minutes) * 60 + float(secs)
            except ValueError as exc:
                raise WorkoutError(f"Could not read {value!r} as a pace.") from exc
        else:
            try:
                seconds = float(text) * 60
            except ValueError as exc:
                raise WorkoutError(
                    f"Could not read {value!r} as a pace. Use 'M:SS' per km, or 'M:SS/mi'."
                ) from exc
    if per_mile:
        seconds /= 1.609344
    if not 90 <= seconds <= 1800:
        raise WorkoutError(
            f"Pace {value!r} works out as {seconds:.0f} s/km, which is outside the "
            "plausible 1:30-30:00 range."
        )
    return seconds


def format_pace(seconds_per_km: float) -> str:
    minutes, secs = divmod(int(round(seconds_per_km)), 60)
    return f"{minutes}:{secs:02d}/km"


# Garmin keeps targetValueOne/Two as fields on the step itself, NOT inside the
# targetType object. Nesting them there uploads cleanly and silently loses the
# numbers, so each target returns its type and its values separately.


def _pace_target(pace: Any) -> tuple[dict[str, Any], tuple[float, float], str]:
    """Garmin stores pace targets as a metres-per-second range."""
    if isinstance(pace, (list, tuple)):
        if len(pace) != 2:
            raise WorkoutError(
                "A pace range needs exactly two values, e.g. ['4:00','4:10']."
            )
        bounds = sorted(parse_pace(p) for p in pace)
        if bounds[1] - bounds[0] < 2 * PACE_WINDOW_SECONDS:
            centre = (bounds[0] + bounds[1]) / 2
            bounds = [centre - PACE_WINDOW_SECONDS, centre + PACE_WINDOW_SECONDS]
    else:
        centre = parse_pace(pace)
        bounds = [centre - PACE_WINDOW_SECONDS, centre + PACE_WINDOW_SECONDS]

    speeds = sorted(1000.0 / b for b in bounds)
    target = {
        "workoutTargetTypeId": TARGET_PACE_ZONE,
        "workoutTargetTypeKey": "pace.zone",
        "displayOrder": 6,
    }
    described = f" @ {format_pace(bounds[0])}-{format_pace(bounds[1])}"
    return target, (speeds[0], speeds[1]), described


def _hr_target(hr: Any) -> tuple[dict[str, Any], tuple[float, float], str]:
    if not isinstance(hr, (list, tuple)) or len(hr) != 2:
        raise WorkoutError("A heart-rate target needs two values, e.g. [150, 165].")
    low, high = sorted(float(v) for v in hr)
    if not 60 <= low <= 230 or not 60 <= high <= 230:
        raise WorkoutError(f"Heart-rate target {hr!r} is outside 60-230 bpm.")
    target = {
        "workoutTargetTypeId": TARGET_HEART_RATE_ZONE,
        "workoutTargetTypeKey": "heart.rate.zone",
        "displayOrder": 4,
    }
    return target, (low, high), f" @ {low:.0f}-{high:.0f} bpm"


def _is_percent(value: Any) -> bool:
    return isinstance(value, str) and value.strip().endswith("%")


def uses_ftp(steps: Sequence[Mapping[str, Any]] | None) -> bool:
    """Whether any step, repeats included, sets power as a share of FTP."""
    for step in steps or []:
        if not isinstance(step, Mapping):
            continue
        if str(step.get("type") or "").strip().lower() == "repeat" and uses_ftp(step.get("steps")):
            return True
        power = step.get("power")
        values = power if isinstance(power, (list, tuple)) else [power]
        if any(_is_percent(v) for v in values):
            return True
    return False


def parse_power(value: Any, ftp: float | None) -> float:
    """250, '250', '250 W' -> watts; '95%' -> 95% of the FTP set in Garmin."""
    if _is_percent(value):
        try:
            percent = float(str(value).strip().rstrip("%").strip())
        except ValueError as exc:
            raise WorkoutError(f"Could not read {value!r} as a share of FTP, e.g. '95%'.") from exc
        low, high = POWER_PERCENT_FTP
        if not low <= percent <= high:
            raise WorkoutError(f"{value!r} of FTP is outside the plausible {low:.0f}-{high:.0f}%.")
        if not ftp:
            raise WorkoutError(
                "No cycling FTP is set in Garmin Connect, so a power target can't be a "
                "percentage. Give watts instead, or set FTP in Garmin Connect first."
            )
        return ftp * percent / 100
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        watts = float(value)
    else:
        text = str(value).strip().lower().replace("watts", "").replace("w", "").strip()
        try:
            watts = float(text)
        except ValueError as exc:
            raise WorkoutError(
                f"Could not read {value!r} as power. Use watts (250) or a share of FTP ('95%')."
            ) from exc
    low, high = POWER_WATTS
    if not low <= watts <= high:
        raise WorkoutError(f"Power {value!r} is outside the plausible {low:.0f}-{high:.0f} W.")
    return watts


def _power_target(power: Any, ftp: float | None) -> tuple[dict[str, Any], tuple[float, float], str]:
    """Garmin stores a power target as a range in watts."""
    if isinstance(power, (list, tuple)):
        if len(power) != 2:
            raise WorkoutError("A power range needs exactly two values, e.g. [240, 260] or ['88%', '94%'].")
        low, high = sorted(parse_power(p, ftp) for p in power)
        if high - low < 2 * POWER_MIN_HALF_WATTS:
            centre = (low + high) / 2
            low, high = centre - POWER_MIN_HALF_WATTS, centre + POWER_MIN_HALF_WATTS
    else:
        centre = parse_power(power, ftp)
        half = max(centre * POWER_WINDOW, POWER_MIN_HALF_WATTS)
        low, high = centre - half, centre + half
    # Halves round up: round() goes to even, so 250 W ± 5% read 238-262.
    low, high = float(int(low + 0.5)), float(int(high + 0.5))
    target = {
        "workoutTargetTypeId": TARGET_POWER_ZONE,
        "workoutTargetTypeKey": "power.zone",
        "displayOrder": 2,
    }
    share = f" ({low / ftp * 100:.0f}-{high / ftp * 100:.0f}% FTP)" if ftp else ""
    return target, (low, high), f" @ {low:.0f}-{high:.0f} W{share}"


NO_TARGET = {
    "workoutTargetTypeId": TARGET_NONE,
    "workoutTargetTypeKey": "no.target",
    "displayOrder": 1,
}


LAP_BUTTON = {
    "conditionTypeId": gw.ConditionType.LAP_BUTTON,
    "conditionTypeKey": "lap.button",
    "displayOrder": 1,
    "displayable": True,
}

# A pool that isn't said is the commonest one.
DEFAULT_POOL_METRES = 25.0


def _end_condition(kind: str) -> dict[str, Any]:
    if kind == "lap":
        return dict(LAP_BUTTON)
    if kind == "distance":
        return {
            "conditionTypeId": gw.ConditionType.DISTANCE,
            "conditionTypeKey": "distance",
            "displayOrder": 3,
            "displayable": True,
        }
    return {
        "conditionTypeId": gw.ConditionType.TIME,
        "conditionTypeKey": "time",
        "displayOrder": 2,
        "displayable": True,
    }


class _Builder:
    """Walks the step list, assigning the sequential order ids Garmin expects."""

    def __init__(self, default_pace: float, pool_metres: float | None = None, ftp: float | None = None) -> None:
        self.order = 0
        self.default_pace = default_pace
        # Set for a swim: steps are then counted in pool lengths, paced per 100 m.
        self.pool = pool_metres
        # The FTP set in Garmin, for power targets given as a percentage.
        self.ftp = ftp
        self.estimated_seconds = 0.0
        self.lines: list[str] = []

    def _next_order(self) -> int:
        self.order += 1
        return self.order

    def build(self, steps: Sequence[Mapping[str, Any]], depth: int = 0) -> list[Any]:
        if not steps:
            raise WorkoutError("A workout needs at least one step.")
        return [self._build_one(step, depth) for step in steps]

    def _build_one(self, step: Mapping[str, Any], depth: int) -> Any:
        if not isinstance(step, Mapping):
            raise WorkoutError(f"Each step must be an object, got {step!r}.")
        kind = str(step.get("type", "interval")).strip().lower()

        if kind == "repeat":
            return self._build_repeat(step, depth)
        if kind not in STEP_TYPES:
            raise WorkoutError(
                f"Unknown step type {kind!r}. Use one of: "
                f"{', '.join(sorted(STEP_TYPES))}, repeat."
            )
        return self._build_step(kind, step, depth)

    def _build_repeat(self, step: Mapping[str, Any], depth: int) -> Any:
        if depth >= 1:
            raise WorkoutError("Repeat groups cannot be nested inside other repeats.")
        try:
            times = int(step.get("times") or step.get("iterations") or 0)
        except (TypeError, ValueError) as exc:
            raise WorkoutError("A repeat needs a whole number of 'times'.") from exc
        if times < 2:
            raise WorkoutError("A repeat needs 'times' of 2 or more.")

        order = self._next_order()
        self.lines.append(f"{times} x")
        before = self.estimated_seconds
        children = self.build(step.get("steps") or [], depth + 1)
        # The children were counted once; charge for the remaining iterations.
        self.estimated_seconds += (self.estimated_seconds - before) * (times - 1)

        return gw.RepeatGroup(
            stepOrder=order,
            stepType={
                "stepTypeId": gw.StepType.REPEAT,
                "stepTypeKey": "repeat",
                "displayOrder": 6,
            },
            numberOfIterations=times,
            workoutSteps=children,
            endCondition={
                "conditionTypeId": gw.ConditionType.ITERATIONS,
                "conditionTypeKey": "iterations",
                "displayOrder": 7,
                "displayable": False,
            },
            endConditionValue=float(times),
        )

    def _build_step(self, kind: str, step: Mapping[str, Any], depth: int) -> Any:
        if self.pool is not None:
            return self._build_swim_step(kind, step, depth)
        distance = step.get("distance_meters")
        duration = step.get("duration_seconds")
        # Asked for, never assumed: a step left open by mistake would run
        # until someone thought to press lap.
        until_lap = step.get("until_lap") is True
        if until_lap and (distance is not None or duration is not None):
            raise WorkoutError(
                f"Step {kind!r} ends either at the lap button or after a time or "
                "distance, not both."
            )
        if distance is None and duration is None and not until_lap:
            raise WorkoutError(
                f"Step {kind!r} needs either 'duration_seconds' or 'distance_meters', "
                'or "until_lap": true to end it with the lap button.'
            )
        if distance is not None and duration is not None:
            raise WorkoutError(
                f"Step {kind!r} has both 'duration_seconds' and 'distance_meters'; "
                "Garmin steps end on one or the other."
            )

        target, described = NO_TARGET, ""
        values: tuple[float, float] | None = None
        pace_for_estimate = self.default_pace
        given = [k for k in ("pace", "hr", "power") if step.get(k) is not None]
        if len(given) > 1:
            raise WorkoutError(f"A step can have one target, not {' and '.join(given)}.")
        if step.get("pace") is not None:
            target, values, described = _pace_target(step["pace"])
            # values are speeds in m/s; convert back for the duration estimate.
            pace_for_estimate = (1000.0 / values[0] + 1000.0 / values[1]) / 2
        elif step.get("hr") is not None:
            target, values, described = _hr_target(step["hr"])
        elif step.get("power") is not None:
            target, values, described = _power_target(step["power"], self.ftp)

        value: float | None
        if until_lap:
            # Walk or jog until ready, then press lap: a typical minute for a
            # recovery, a few for anything longer, for the time estimate.
            value, condition, amount = None, "lap", "until lap pressed"
            self.estimated_seconds += 60 if kind in ("recovery", "rest") else 300
        elif distance is not None:
            value = float(distance)
            if value <= 0:
                raise WorkoutError("'distance_meters' must be positive.")
            condition = "distance"
            self.estimated_seconds += value / 1000.0 * pace_for_estimate
            amount = f"{value / 1000:.2f} km".rstrip("0").rstrip(".")
        else:
            value = float(duration)
            if value <= 0:
                raise WorkoutError("'duration_seconds' must be positive.")
            condition = "time"
            self.estimated_seconds += value
            minutes, secs = divmod(int(value), 60)
            amount = f"{minutes}m {secs:02d}s" if secs else f"{minutes}m"

        indent = "  " * (depth + 1) if depth else "  "
        self.lines.append(f"{indent}{kind}: {amount}{described}")

        type_id, type_key, display = STEP_TYPES[kind]
        target_values = (
            {"targetValueOne": values[0], "targetValueTwo": values[1]}
            if values
            else {}
        )
        return gw.ExecutableStep(
            stepOrder=self._next_order(),
            stepType={
                "stepTypeId": type_id,
                "stepTypeKey": type_key,
                "displayOrder": display,
            },
            endCondition=_end_condition(condition),
            endConditionValue=value,
            targetType=target,
            **target_values,
        )


    def _build_swim_step(self, kind: str, step: Mapping[str, Any], depth: int) -> Any:
        """A swim step: lengths of the pool, a stroke, and a pace said rather than beeped.

        A rest with neither time nor distance waits for the lap button, the way
        swimmers rest at the wall. A pace is per 100 m and goes on the step as a
        note the watch shows, not as a target: a pace alarm can't be heard or
        acted on mid-length.
        """
        distance = step.get("distance_meters")
        seconds = step.get("duration_seconds")
        if distance is not None and seconds is not None:
            raise WorkoutError(
                f"Step {kind!r} has both 'duration_seconds' and 'distance_meters'; "
                "Garmin steps end on one or the other."
            )
        if distance is None and seconds is None and kind not in ("rest", "recovery"):
            raise WorkoutError(
                f"Swim step {kind!r} needs 'distance_meters' (or 'duration_seconds'). "
                "Only a rest can be left open, ended by pressing lap."
            )
        if step.get("pace") is not None and step.get("hr") is not None:
            raise WorkoutError("A step can carry a pace or a heart-rate target, not both.")
        if step.get("power") is not None:
            raise WorkoutError("A swim step can't have a power target. Use pace or heart rate.")

        notes: list[str] = []
        pace_100 = None
        if step.get("pace") is not None:
            pace_100 = swimming.parse_pace_per_100(step["pace"])
            notes.append(f"{format_pace(pace_100).replace('/km', '')} /100m")
        stroke = None
        if step.get("stroke") is not None:
            stroke = swimming.stroke_type(step["stroke"])
            if stroke is None:
                raise WorkoutError(
                    f"Unknown stroke {step['stroke']!r}. Use freestyle, backstroke, "
                    "breaststroke, butterfly, im, drill or any."
                )
        if step.get("note"):
            notes.append(str(step["note"]).strip())

        target, values, described = NO_TARGET, None, ""
        if step.get("hr") is not None:
            target, values, described = _hr_target(step["hr"])

        if distance is not None:
            value = float(distance)
            if value <= 0:
                raise WorkoutError("'distance_meters' must be positive.")
            lengths = value / self.pool
            if abs(lengths - round(lengths)) > 1e-6:
                raise WorkoutError(
                    f"{value:g} m isn't a whole number of {self.pool:g} m lengths; "
                    "swim distances have to be."
                )
            condition = "distance"
            per_100 = pace_100 or self.default_pace / 10
            self.estimated_seconds += value / 100 * per_100
            amount = f"{value:g} m"
        elif seconds is not None:
            value = float(seconds)
            if value <= 0:
                raise WorkoutError("'duration_seconds' must be positive.")
            condition = "time"
            self.estimated_seconds += value
            minutes, secs = divmod(int(value), 60)
            amount = f"{minutes}m {secs:02d}s" if secs else f"{minutes}m"
        else:
            value, condition, amount = None, "lap", "until lap pressed"
            self.estimated_seconds += 20  # a typical rest at the wall

        stroke_word = f" {step['stroke']}" if stroke else ""
        note_text = f" ({'; '.join(notes)})" if notes else ""
        indent = "  " * (depth + 1) if depth else "  "
        self.lines.append(f"{indent}{kind}: {amount}{stroke_word}{note_text}{described}")

        type_id, type_key, display = STEP_TYPES[kind]
        extra: dict[str, Any] = {}
        if values:
            extra.update(targetValueOne=values[0], targetValueTwo=values[1])
        if stroke:
            extra["strokeType"] = stroke
        if notes:
            extra["description"] = "; ".join(notes)
        return gw.ExecutableStep(
            stepOrder=self._next_order(),
            stepType={"stepTypeId": type_id, "stepTypeKey": type_key, "displayOrder": display},
            endCondition=_end_condition(condition),
            endConditionValue=value,
            targetType=target,
            **extra,
        )


def build_workout(
    name: str,
    sport: str,
    steps: Sequence[Mapping[str, Any]],
    description: str | None = None,
    pool_length_meters: float | None = None,
    ftp: float | None = None,
) -> tuple[Any, str, int]:
    """Return (workout model, human-readable summary, estimated seconds).

    ftp is the cycling FTP set in Garmin, in watts, for power targets given as
    a percentage; the caller fetches it only when uses_ftp(steps) says so.
    """
    if not name or not str(name).strip():
        raise WorkoutError("The workout needs a name.")
    sport_key = str(sport or "running").strip().lower()
    if sport_key not in SPORTS:
        raise WorkoutError(
            f"Unknown sport {sport!r}. Use one of: {', '.join(sorted(SPORTS))}."
        )

    model_cls, default_pace = SPORTS[sport_key]
    pool = None
    if sport_key == "swimming":
        try:
            pool = float(pool_length_meters) if pool_length_meters is not None else DEFAULT_POOL_METRES
        except (TypeError, ValueError) as exc:
            raise WorkoutError("'pool_length_meters' must be a number, e.g. 25 or 50.") from exc
        if not 10 <= pool <= 100:
            raise WorkoutError(f"A {pool:g} m pool is outside the plausible 10-100 m.")
    elif pool_length_meters is not None:
        raise WorkoutError("'pool_length_meters' only applies to swimming workouts.")
    builder = _Builder(default_pace, pool, ftp)
    built = builder.build(list(steps))
    estimated = int(round(builder.estimated_seconds))

    workout = model_cls(
        **(swimming.pool_fields(pool) if pool is not None else {}),
        workoutName=str(name).strip(),
        description=description,
        estimatedDurationInSecs=estimated,
        workoutSegments=[
            gw.WorkoutSegment(
                segmentOrder=1,
                sportType=model_cls.model_fields["sportType"].default_factory(),
                workoutSteps=built,
            )
        ],
    )

    hours, rem = divmod(estimated, 3600)
    mins, secs = divmod(rem, 60)
    total = f"{hours}h {mins:02d}m" if hours else f"{mins}m {secs:02d}s"
    where = f", {pool:g} m pool" if pool is not None else ""
    summary = "\n".join([f"{name} ({sport_key}{where}, about {total})", *builder.lines])
    return workout, summary, estimated


# --------------------------------------------------------------------------
# Strength training
# --------------------------------------------------------------------------
#
# Strength workouts are shaped differently from the endurance ones above: a
# block is "4 sets of 10 bench press, 120s rest" rather than a sequence of
# timed or measured steps, and each block names an exercise from Garmin's own
# catalogue. garminconnect ships that catalogue (1527 exercises across 47
# categories) and a helper that builds one block, so the work here is turning
# what somebody said into a catalogue entry, and saying so clearly when it
# cannot be done.


def _normalise(term: str) -> str:
    """Fold the spelling differences people actually type.

    The catalogue search is a plain substring match, so "pull up" finds nothing
    while "pull-up" finds twenty-one. Nobody should have to know that.
    """
    return " ".join(str(term).replace("-", " ").replace("_", " ").lower().split())


def find_exercises(term: str, limit: int = 25) -> list[dict[str, str]]:
    """Search Garmin's exercise catalogue, tolerant of hyphens and spacing."""
    from garminconnect import exercises as catalogue

    wanted = _normalise(term)
    if not wanted:
        return []
    hits = [
        entry for entry in catalogue.EXERCISES
        if wanted in _normalise(entry["name"])
    ]
    # Prefer the plainest name: an exact match, then the shortest, so "bench
    # press" offers "Bench Press" before "Close-grip Barbell Bench Press".
    hits.sort(key=lambda e: (_normalise(e["name"]) != wanted, len(e["name"])))
    return hits[:limit]


def resolve_exercise(name: str) -> dict[str, str]:
    """Turn what somebody called an exercise into a catalogue entry.

    Raises WorkoutError naming the near misses rather than guessing, because a
    wrong guess here is a workout on someone's watch with the wrong movement
    in it.
    """
    from garminconnect import exercises as catalogue

    exact = catalogue.resolve(str(name).strip())
    if exact:
        return exact

    hits = find_exercises(name)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise WorkoutError(
            f"No exercise in Garmin's catalogue matches {name!r}. "
            "Call find_exercises to search it."
        )
    # Several matches, but one is exactly what was asked for.
    for entry in hits:
        if _normalise(entry["name"]) == _normalise(name):
            return entry
    names = ", ".join(e["name"] for e in hits[:6])
    raise WorkoutError(
        f"{name!r} matches several exercises: {names}"
        f"{' ...' if len(hits) > 6 else ''}. Use one of those names exactly."
    )


def build_strength_workout(
    name: str,
    exercises: list[dict[str, Any]],
    description: str | None = None,
) -> tuple[Any, str, int]:
    """Build a strength workout from a list of exercise blocks."""
    import garminconnect.workout as gw

    if not exercises:
        raise WorkoutError("A strength workout needs at least one exercise.")

    steps: list[Any] = []
    lines: list[str] = []
    order = 1
    estimated = 0

    for position, raw in enumerate(exercises, start=1):
        if not isinstance(raw, dict):
            raise WorkoutError(f"Exercise {position} should be an object, not {type(raw).__name__}.")
        wanted = raw.get("exercise") or raw.get("name")
        if not wanted:
            raise WorkoutError(f"Exercise {position} is missing an 'exercise' name.")

        entry = resolve_exercise(wanted)
        try:
            sets = int(raw.get("sets", 3))
            reps = int(raw.get("reps", 10))
            rest = float(raw.get("rest_seconds", 90))
        except (TypeError, ValueError) as exc:
            raise WorkoutError(
                f"Exercise {position} ({entry['name']}): sets, reps and "
                "rest_seconds must be numbers."
            ) from exc
        if sets < 1 or reps < 1:
            raise WorkoutError(
                f"Exercise {position} ({entry['name']}): sets and reps must be at least 1."
            )

        weight = raw.get("weight_kg")
        weight_kg = float(weight) if weight not in (None, "") else None

        steps.append(
            gw.create_strength_set(
                entry["category"],
                step_order=order,
                sets=sets,
                reps=reps,
                rest_seconds=rest,
                exercise_name=entry["exercise"],
                weight_kg=weight_kg,
            )
        )
        # The helper documents this: the block takes three step orders.
        order += 3

        # Rough, and deliberately so — a rep is not a fixed length of time.
        estimated += int(sets * (reps * 3 + rest))
        load = f" @ {weight_kg:g}kg" if weight_kg is not None else ""
        lines.append(f"  {sets}x{reps} {entry['name']}{load}, {int(rest)}s rest")

    workout = gw.StrengthWorkout(
        workoutName=str(name).strip(),
        description=description,
        estimatedDurationInSecs=estimated,
        workoutSegments=[
            gw.WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 5, "sportTypeKey": "strength_training"},
                workoutSteps=steps,
            )
        ],
    )

    mins = estimated // 60
    summary = "\n".join([f"{name} (strength, about {mins}m)", *lines])
    return workout, summary, estimated
