"""Decide which planned sessions actually got done.

plan.py pairs planned against actual per day, in aggregate: "45 minutes done
against 40 planned". That draws a chart, but it cannot answer "is Tuesday's
threshold ticked off", because it never matches one workout to one activity.
This does.

The whole thing turns on being forgiving in the right direction. People move
sessions around — a friend wants to train on Thursday, so the runs shuffle —
and a matcher that insists on the scheduled day reports a week of failures in
a week where nothing was missed. That is the failure that makes somebody stop
opening the page, so a session counts if it happened within a day either side,
a strength session if it happened any day that week, and any session started
from its workout on the watch wherever in the week it landed.

It is deliberately generous about intensity and strict about existence. Whether
a session was run at the right effort is a coaching judgement, made in the
conversation with the data in front of it. Whether it happened at all is a
fact, and that is all this decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from .cycling import is_ride
from .plan import category, monday_of

# How far a session may drift from its scheduled day and still count.
SHIFT_DAYS = 1

# How much of the planned duration has to be there. Below this it reads as a
# different, shorter session rather than the planned one — but the bar is low,
# because plenty of sessions come in under their estimate without being a
# failure, and Garmin's own estimates are not exact.
MIN_FRACTION = 0.7

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]


@dataclass(frozen=True)
class Planned:
    day: date
    name: str
    sport: str
    seconds: float = 0.0
    metres: float = 0.0
    workout_id: Any = None
    # The workout's name as scheduled, when name differs from it (get_plan
    # keys sessions by schedule id). Used only as a hint for matching.
    title: str = ""
    # A threshold or interval session, which a plainly easy run isn't; None
    # when the workout wasn't read, so its effort isn't known.
    hard: bool | None = None
    # The workout was read and Garmin gives it neither a time nor a distance,
    # as with an easier copy (Nov 7). False when it simply wasn't read.
    no_size: bool = False


@dataclass(frozen=True)
class Actual:
    day: date
    name: str
    sport: str
    seconds: float = 0.0
    metres: float = 0.0
    activity_id: Any = None
    # Rides sit in "other" for matching; the training log still needs to tell
    # them apart, to total a cyclist's kilometres.
    ride: bool = False
    # The sport as the plan card's icon shows it: yoga, a hike, a swim.
    kind: str = ""
    # What the run was, by Garmin's own measures (looks_easy): True easy,
    # False hard, None when Garmin didn't say.
    easy: bool | None = None


@dataclass
class Result:
    done: list[dict[str, Any]] = field(default_factory=list)
    missed: list[dict[str, Any]] = field(default_factory=list)
    upcoming: list[dict[str, Any]] = field(default_factory=list)
    extra: list[dict[str, Any]] = field(default_factory=list)


# Garmin's training-effect labels for a run that trained nothing but the base.
EASY_LABELS = ("RECOVERY", "AEROBIC_BASE", "BASE")
# Below this share of the time in heart-rate zones 3 to 5, a run was easy.
EASY_HARD_ZONE_SHARE = 0.05


def looks_easy(activity: Mapping[str, Any]) -> bool | None:
    """Whether a run was plainly easy, by Garmin's measures of it: its
    training-effect label, its time in heart-rate zones, its training effect.

    True only when what Garmin gives all says easy, False when any of it says
    harder, None when it gives nothing. Nov 4: a 7.7 km run, all in zone 1,
    anaerobic effect 0, was ticked as the 7.8 km of intervals planned that day.
    """
    says: list[bool] = []
    label = str(activity.get("trainingEffectLabel") or "").upper()
    if label:
        says.append(label in EASY_LABELS)
    zones = [activity.get(f"hrTimeInZone_{n}") for n in range(1, 6)]
    if all(isinstance(z, (int, float)) for z in zones) and sum(zones) > 0:
        says.append(sum(zones[2:]) / sum(zones) < EASY_HARD_ZONE_SHARE)
    aerobic, anaerobic = activity.get("aerobicTrainingEffect"), activity.get("anaerobicTrainingEffect")
    if isinstance(aerobic, (int, float)) and isinstance(anaerobic, (int, float)):
        says.append(aerobic < 3.0 and anaerobic < 1.0)
    return all(says) if says else None


def _why(planned: Planned, actual: Actual) -> str:
    """Say how the match was made, so a wrong one is obviously wrong.

    A tick nobody can account for is worse than no tick: when the matcher gets
    it wrong the person needs to see why at a glance, not wonder whether the
    data is broken.
    """
    delta = (actual.day - planned.day).days
    if delta == 0:
        return f"{actual.name} on the day."
    when = DAY_NAMES[actual.day.weekday()]
    days = "day" if abs(delta) == 1 else "days"
    return f"{actual.name} on {when}, {abs(delta)} {days} {'late' if delta > 0 else 'early'}."


def _same_name(planned: Planned, actual: Actual) -> bool:
    """Whether the activity was started from this workout on the watch.

    Garmin names an activity after the workout it was started from, often with
    the place in front ("Alcudia - 14km long run easy"). A name is only ever a
    hint: a session started some other way still counts by sport and day.
    """
    title = (planned.title or "").split(" · ")[0].strip().lower()
    name = (actual.name or "").strip().lower()
    if len(title) < 4 or not name:
        return False
    return name == title or name.endswith(" - " + title) or name.endswith(title)


def match(
    planned: Iterable[Planned],
    actual: Iterable[Actual],
    today: date,
    *,
    shift_days: int = SHIFT_DAYS,
    min_fraction: float = MIN_FRACTION,
) -> Result:
    """Pair each planned session with the activity that satisfied it.

    In three passes, strongest evidence first:

    1. An activity named after the workout, any day of the same week: it was
       that session, wherever it landed.
    2. The same sport within a day either side (a strength session only
       within its own week, so Monday's gym never covers last Sunday's). A
       hard session isn't covered by a run Garmin says was plainly easy.
    3. A run left over counts as this week's session a day ahead, done early,
       when it plainly matches its kind: an easy run for an easy session, a
       hard one for a hard session (an easy run brought forward, Nov 4).
    4. Strength by sport alone, any day of the same week, including a session
       still ahead this week: done early is done.
    """
    result = Result()
    unused = sorted(actual, key=lambda a: (a.day, str(a.activity_id)))
    taken: set[int] = set()
    sessions = sorted(planned, key=lambda p: (p.day, p.name))
    this_week = monday_of(today)

    def long_enough(session: Planned, candidate: Actual) -> bool:
        # Strength is existence alone: a gym session runs as long as the gym
        # allows, and Garmin's estimate for one counts every rest to the
        # second. Otherwise Garmin's time estimate, else the planned distance
        # (an easier copy carries no estimate, Nov 7); with neither there is
        # nothing to be short of, and unsized keeps it to its own day.
        if session.sport == "strength":
            return True
        if session.seconds:
            return candidate.seconds >= session.seconds * min_fraction
        if session.metres and candidate.metres:
            return candidate.metres >= session.metres * min_fraction
        return True

    def unsized(session: Planned) -> bool:
        # A session read and found to have no size can't tell a short top-up
        # from the real thing, so it takes only a run on its own day, never
        # one from the day before (Nov 7: an easier parkrun tune-up claimed
        # Friday's 1.2 km). One not read yet keeps the day's grace.
        return (session.no_size and session.sport != "strength"
                and not session.seconds and not session.metres)

    def pick(session: Planned, allowed) -> int | None:
        best, best_rank = None, None
        for index, candidate in enumerate(unused):
            if index in taken or candidate.sport != session.sport or candidate.day > today:
                continue
            if not allowed(candidate) or not long_enough(session, candidate):
                continue
            # Nearest day wins; then the closest length, so two runs a day apart
            # land on the sessions they most resemble.
            rank = (abs((candidate.day - session.day).days), abs(candidate.seconds - session.seconds))
            if best_rank is None or rank < best_rank:
                best, best_rank = index, rank
        return best

    def same_week(session: Planned):
        return lambda c: monday_of(c.day) == monday_of(session.day)

    def near(session: Planned):
        def allowed(c: Actual) -> bool:
            if abs((c.day - session.day).days) > (0 if unsized(session) else shift_days):
                return False
            if session.hard and c.easy:
                return False
            return session.sport != "strength" or monday_of(c.day) == monday_of(session.day)
        return allowed

    def ahead(session: Planned):
        def allowed(c: Actual) -> bool:
            early = (session.day - c.day).days
            return (not unsized(session) and 0 < early <= shift_days and monday_of(c.day) == monday_of(session.day)
                    and session.hard is not None and c.easy is (not session.hard))
        return allowed

    # Which sessions can still be matched: everything up to today, and anything
    # later this week, which only an activity already done can tick off early.
    def open_to_early(session: Planned) -> bool:
        return session.day <= today or monday_of(session.day) == this_week

    hits: dict[int, int] = {}

    def run_pass(eligible, allowed_for) -> None:
        for i, session in enumerate(sessions):
            if i in hits or not eligible(session):
                continue
            best = pick(session, allowed_for(session))
            if best is not None:
                taken.add(best)
                hits[i] = best

    run_pass(open_to_early,
             lambda s: (lambda c: same_week(s)(c) and _same_name(s, c)))
    run_pass(lambda s: s.day <= today, near)
    run_pass(lambda s: s.day > today and s.sport != "strength" and monday_of(s.day) == this_week, ahead)
    run_pass(lambda s: s.sport == "strength" and open_to_early(s), same_week)

    for i, session in enumerate(sessions):
        if i in hits:
            hit = unused[hits[i]]
            result.done.append({
                "date": session.day.isoformat(),
                "name": session.name,
                "sport": session.sport,
                "planned_seconds": session.seconds or None,
                "actual_seconds": hit.seconds or None,
                "actual_km": round(hit.metres / 1000, 2) if hit.metres else None,
                "done_on": hit.day.isoformat(),
                "activity_id": hit.activity_id,
                "why": _why(session, hit),
            })
            continue
        row = {
            "date": session.day.isoformat(),
            "name": session.name,
            "sport": session.sport,
            "planned_seconds": session.seconds or None,
        }
        (result.upcoming if session.day > today else result.missed).append(row)

    for index, leftover in enumerate(unused):
        if index in taken or leftover.day > today:
            continue
        result.extra.append({
            "date": leftover.day.isoformat(),
            "name": leftover.name,
            "sport": leftover.sport,
            "actual_seconds": leftover.seconds or None,
            "actual_km": round(leftover.metres / 1000, 2) if leftover.metres else None,
            **({"activity_id": leftover.activity_id} if leftover.activity_id else {}),
            **({"ride": True} if leftover.ride else {}),
            **({"kind": leftover.kind} if leftover.kind else {}),
        })

    return result


def by_week(result: Result, today: date, weeks: int) -> list[dict[str, Any]]:
    """Roll the verdict up per week, which is the unit progress is felt in."""
    this_monday = monday_of(today)
    out = []
    for back in range(weeks - 1, -1, -1):
        start = this_monday - timedelta(weeks=back)
        end = start + timedelta(days=6)

        def within(rows):
            return [r for r in rows if start.isoformat() <= r["date"] <= end.isoformat()]

        done, missed = within(result.done), within(result.missed)
        planned_count = len(done) + len(missed)
        out.append({
            "week_of": start.isoformat(),
            "current": start == this_monday,
            "planned": planned_count,
            "completed": len(done),
            # Nothing planned is not 100% — it is no plan, and calling it
            # complete would show a full bar for a week nobody trained.
            "complete": bool(planned_count) and not missed,
            "missed": [m["name"] for m in missed],
            "extra": len(within(result.extra)),
        })
    return out


# --------------------------------------------------------------------------
# Adapting what Garmin actually returns
# --------------------------------------------------------------------------


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def from_collected(data: dict[str, Any]) -> tuple[list[Planned], list[Actual]]:
    """Turn plan.collect()'s payload into the matcher's inputs."""
    planned: list[Planned] = []
    for item in data.get("planned") or []:
        day = _day(item.get("date"))
        if not day:
            continue
        workout_id = item.get("workoutId")
        planned.append(
            Planned(
                day=day,
                name=item.get("title") or item.get("workoutName") or "Workout",
                # The calendar labels a planned session with its own sport key,
                # which is the same vocabulary the activity carries.
                sport=category(item.get("sportTypeKey") or ""),
                seconds=float((data.get("planned_duration") or {}).get(workout_id, 0.0)),
                metres=float((data.get("planned_distance") or {}).get(workout_id, 0.0)),
                workout_id=workout_id,
                title=item.get("title") or item.get("workoutName") or "",
            )
        )

    actual: list[Actual] = []
    for item in data.get("activities") or []:
        day = _day(item.get("startTimeLocal"))
        if not day:
            continue
        actual.append(
            Actual(
                day=day,
                name=item.get("activityName") or "Activity",
                sport=category((item.get("activityType") or {}).get("typeKey")),
                seconds=float(item.get("duration") or 0),
                metres=float(item.get("distance") or 0),
                activity_id=item.get("activityId"),
                ride=is_ride((item.get("activityType") or {}).get("typeKey")),
                easy=looks_easy(item),
            )
        )
    return planned, actual
