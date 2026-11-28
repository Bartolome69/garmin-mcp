"""daash.workout.v1: a structured workout that belongs to no provider.

It is the step list create_workout already takes (the grammar documented on
that tool), made a contract: every provider translates to and from this, and
nothing provider-specific (Garmin's step-type ids, another service's workout
text) leaks into it. Garmin keeps building straight from the steps
(workouts.build_workout), as before; the providers after it convert this.

    {
      "schema": "daash.workout.v1",
      "name": "5 x 1 km",
      "sport": "running",                  # running, cycling, swimming, walking, hiking
      "description": "Easy pace 5:30-6:00 between.",    # optional
      "steps": [
        {"type": "warmup", "duration_seconds": 900},
        {"type": "repeat", "times": 5, "steps": [
          {"type": "interval", "distance_meters": 1000, "pace": ["4:00", "4:10"]},
          {"type": "recovery", "duration_seconds": 90}
        ]},
        {"type": "cooldown", "until_lap": true}
      ]
    }

A step is warmup, interval, recovery, rest or cooldown, and ends on exactly
one of duration_seconds, distance_meters or until_lap. It may carry one
target: pace (min/km, "4:05" or a pair), hr (bpm pair, {"max": n} or "z2") or
power (watts, a share of FTP such as "95%", or "z3"); a swim step may also
name a stroke; any step may carry a note. A repeat has times (1 or more) and
steps, and holds no repeat. Targets are kept as given: what a target means in
numbers is for the provider that builds it (workouts.py widens Garmin's).

normalise() takes what a chat or a provider hands over, with the spellings
create_workout accepts for people on the Nov 26 tool fixes (tools_v2), and
returns the canonical document, or raises WorkoutError saying what's wrong.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..workouts import SPORTS, STEP_TYPES, WorkoutError, common_step_keys

SCHEMA = "daash.workout.v1"

ENDS = ("duration_seconds", "distance_meters", "until_lap")
TARGETS = ("pace", "hr", "power")
# Kept on a step as given, after its end and target.
EXTRAS = ("stroke", "note")


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise WorkoutError(f"{what} must be a positive number, got {value!r}.")
    return int(value) if float(value).is_integer() else float(value)


def _step(raw: Any, path: str, in_repeat: bool) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise WorkoutError(f"Step {path} must be an object, got {type(raw).__name__}.")
    step = common_step_keys(raw)
    kind = step.get("type") or "interval"  # as the Garmin builder reads a step with no type
    if kind == "repeat":
        if in_repeat:
            raise WorkoutError(f"Step {path}: a repeat can't contain another repeat.")
        times = step.get("times") if step.get("times") is not None else step.get("iterations")
        if isinstance(times, bool) or not isinstance(times, int) or times < 1:
            raise WorkoutError(f"Step {path}: a repeat needs 'times', a whole number of 1 or more.")
        children = step.get("steps")
        if not isinstance(children, list) or not children:
            raise WorkoutError(f"Step {path}: a repeat needs 'steps', a list of at least one step.")
        return {"type": "repeat", "times": times,
                "steps": [_step(c, f"{path}.{i + 1}", True) for i, c in enumerate(children)]}
    if kind not in STEP_TYPES:
        raise WorkoutError(f"Step {path}: unknown type {kind!r}. Use one of: "
                           f"{', '.join([*STEP_TYPES, 'repeat'])}.")
    ends = [k for k in ENDS if step.get(k) not in (None, False)]
    if len(ends) != 1:
        raise WorkoutError(f"Step {path} needs exactly one of duration_seconds, distance_meters "
                           f"or until_lap: true (has {', '.join(ends) or 'none'}).")
    out: dict[str, Any] = {"type": kind}
    end = ends[0]
    if end == "until_lap":
        if step["until_lap"] is not True:
            raise WorkoutError(f"Step {path}: until_lap must be true.")
        out["until_lap"] = True
    else:
        out[end] = _number(step[end], f"Step {path}'s {end}")
    targets = [k for k in TARGETS if step.get(k) is not None]
    if len(targets) > 1:
        raise WorkoutError(f"Step {path} has more than one target ({', '.join(targets)}); keep one.")
    for key in (*targets, *EXTRAS):
        if step.get(key) not in (None, ""):
            out[key] = step[key]
    return out


def normalise(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The canonical daash.workout.v1 for `doc`, or WorkoutError."""
    if not isinstance(doc, Mapping):
        raise WorkoutError("A workout must be an object.")
    schema = doc.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise WorkoutError(f"Unknown workout schema {schema!r}; this reads {SCHEMA}.")
    name = str(doc.get("name") or "").strip()
    if not name:
        raise WorkoutError("The workout needs a name.")
    sport = str(doc.get("sport") or "running").strip().lower()
    if sport not in SPORTS:
        raise WorkoutError(f"Unknown sport {doc.get('sport')!r}. Use one of: {', '.join(sorted(SPORTS))}.")
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkoutError("The workout needs at least one step.")
    out: dict[str, Any] = {"schema": SCHEMA, "name": name, "sport": sport}
    description = str(doc.get("description") or "").strip()
    if description:
        out["description"] = description
    if sport == "swimming" and doc.get("pool_length_meters") is not None:
        out["pool_length_meters"] = _number(doc["pool_length_meters"], "pool_length_meters")
    out["steps"] = [_step(s, str(i + 1), False) for i, s in enumerate(steps)]
    return out


def from_tool_args(name: str, steps: list[dict[str, Any]], sport: str = "running",
                   description: str | None = None, pool_length_meters: float | None = None) -> dict[str, Any]:
    """create_workout's arguments as a daash.workout.v1."""
    doc: dict[str, Any] = {"name": name, "sport": sport, "steps": steps}
    if description:
        doc["description"] = description
    if pool_length_meters is not None:
        doc["pool_length_meters"] = pool_length_meters
    return normalise(doc)

