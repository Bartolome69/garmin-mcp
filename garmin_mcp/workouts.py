"""Build Garmin structured workouts from a plain step description.

Garmin's workout JSON is deeply nested and full of magic ids. This module turns
a simple list of steps — the kind you would describe out loud — into the models
garminconnect uploads, and renders one back as text so you can check it before
it lands in your account.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from garminconnect import workout as gw

# Garmin's own workoutTargetTypeId values. garminconnect's TargetType has never
# had a pace entry and has renumbered the rest between releases, so these are
# pinned here rather than borrowed from it.
TARGET_NONE = 1
TARGET_HEART_RATE_ZONE = 4
TARGET_PACE_ZONE = 6

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

# A single pace is widened into a window this many seconds per km either side,
# because Garmin alerts on a range and an exact target would beep constantly.
PACE_WINDOW_SECONDS = 5.0


class WorkoutError(ValueError):
    """Bad workout description; the message is shown to the user."""


def parse_pace(value: Any) -> float:
    """'4:05', '4:05 /km' or 245 -> seconds per kilometre."""
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip().lower().replace("/km", "").replace("min", "").strip()
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
                    f"Could not read {value!r} as a pace. Use 'M:SS' per km."
                ) from exc
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


NO_TARGET = {
    "workoutTargetTypeId": TARGET_NONE,
    "workoutTargetTypeKey": "no.target",
    "displayOrder": 1,
}


def _end_condition(kind: str) -> dict[str, Any]:
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

    def __init__(self, default_pace: float) -> None:
        self.order = 0
        self.default_pace = default_pace
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
        distance = step.get("distance_meters")
        duration = step.get("duration_seconds")
        if distance is None and duration is None:
            raise WorkoutError(
                f"Step {kind!r} needs either 'duration_seconds' or 'distance_meters'."
            )
        if distance is not None and duration is not None:
            raise WorkoutError(
                f"Step {kind!r} has both 'duration_seconds' and 'distance_meters'; "
                "Garmin steps end on one or the other."
            )

        target, described = NO_TARGET, ""
        values: tuple[float, float] | None = None
        pace_for_estimate = self.default_pace
        if step.get("pace") is not None and step.get("hr") is not None:
            raise WorkoutError("A step can target pace or heart rate, not both.")
        if step.get("pace") is not None:
            target, values, described = _pace_target(step["pace"])
            # values are speeds in m/s; convert back for the duration estimate.
            pace_for_estimate = (1000.0 / values[0] + 1000.0 / values[1]) / 2
        elif step.get("hr") is not None:
            target, values, described = _hr_target(step["hr"])

        if distance is not None:
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


def build_workout(
    name: str,
    sport: str,
    steps: Sequence[Mapping[str, Any]],
    description: str | None = None,
) -> tuple[Any, str, int]:
    """Return (workout model, human-readable summary, estimated seconds)."""
    if not name or not str(name).strip():
        raise WorkoutError("The workout needs a name.")
    sport_key = str(sport or "running").strip().lower()
    if sport_key not in SPORTS:
        raise WorkoutError(
            f"Unknown sport {sport!r}. Use one of: {', '.join(sorted(SPORTS))}."
        )

    model_cls, default_pace = SPORTS[sport_key]
    builder = _Builder(default_pace)
    built = builder.build(list(steps))
    estimated = int(round(builder.estimated_seconds))

    workout = model_cls(
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
    summary = "\n".join([f"{name} ({sport_key}, about {total})", *builder.lines])
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
