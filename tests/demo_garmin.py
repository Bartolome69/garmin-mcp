"""A demo Garmin account for running the app locally: a half-marathon block around today.

FakeGarmin's fixtures sit in September 2026, so the plan card finds nothing
current in them. This one builds a 12-week block whose fourth week is this
week, with the workouts made by the server's own builder, so their steps,
targets and descriptions are the shapes create_plan writes. Every session
before today was run, as an activity whose laps follow the workout's steps.

Set DEMO_RAN=1 to have today's session run too, and DEMO_READINESS=low for a
poor night. Nothing here reaches Garmin.
"""

from __future__ import annotations

import copy
import os
from datetime import date, datetime, timedelta
from typing import Any

from garmin_mcp import training_plan
from garmin_mcp.workouts import build_strength_workout, build_workout
from tests.fake_garmin import FakeGarmin, _stream

LABEL = "HM"
GOAL = "Half marathon under 1:35"
WEEKS = 12
THIS_WEEK = 4
EASY = ["5:15", "5:40"]
SUB_T = ["4:00", "4:08"]
HM_PACE = ["4:18", "4:25"]

# What each day of a week holds, Monday first, the hard session on Monday so
# that this week opens on it. Reps per week of the block for the hard session.
SUB_T_REPS = [3, 3, 3, 3, 4, 4, 3, 4, 4, 3, 2, 0]
LONG_KM = [12, 13, 14, 16, 15, 17, 18, 15, 19, 18, 14, 0]
PHASES = [{"label": "Base", "from_week": 1, "to_week": 4}, {"label": "Build", "from_week": 5, "to_week": 8},
          {"label": "Peak", "from_week": 9, "to_week": 10}, {"label": "Taper", "from_week": 11, "to_week": 12}]


def _phase(week: int) -> str:
    return next(p["label"] for p in PHASES if p["from_week"] <= week <= p["to_week"])


def _sessions(week: int) -> list[tuple[int, str, str, list[dict[str, Any]], str]]:
    """(weekday, name, sport, steps, note) for one week of the block."""
    reps = SUB_T_REPS[week - 1]
    out: list[tuple[int, str, str, list[dict[str, Any]], str]] = []
    if reps:
        out.append((0, f"3 x {reps} km" if reps in (2, 3) else f"{reps} x 2 km", "running", [
            {"type": "warmup", "distance_meters": 2000, "hr": [120, 145]},
            {"type": "repeat", "times": 3 if reps in (2, 3) else reps, "steps": [
                {"type": "interval", "distance_meters": reps * 1000 if reps in (2, 3) else 2000, "pace": SUB_T},
                {"type": "recovery", "duration_seconds": 90}]},
            {"type": "cooldown", "distance_meters": 2000, "hr": [120, 145]},
        ], "Builds the pace you'll hold from 10 km on, with easy days either side."))
    out += [
        (1, "Easy run 8 km", "running", [{"type": "interval", "distance_meters": 8000, "pace": EASY}],
         "Aerobic volume. If it feels like work, slow down."),
        (2, "Easy run 6 km", "running", [{"type": "interval", "distance_meters": 6000, "pace": EASY}],
         "Short and easy, the day after a run."),
        (2, "Full body (runner)", "strength", [], "Strength on an easy day keeps hard days hard."),
        (3, "Easy run 8 km", "running", [{"type": "interval", "distance_meters": 8000, "pace": EASY}],
         "Aerobic volume. If it feels like work, slow down."),
        (5, "Shakeout with strides", "running", [
            {"type": "interval", "distance_meters": 4000, "pace": ["5:30", "6:00"]},
            {"type": "repeat", "times": 4, "steps": [
                {"type": "interval", "duration_seconds": 20}, {"type": "recovery", "until_lap": True}]},
        ], "Wakes the legs up for Sunday without tiring them."),
    ]
    long_km = LONG_KM[week - 1]
    if long_km:
        out.append((6, f"Long run {long_km} km, last 3 at HM pace", "running", [
            {"type": "interval", "distance_meters": (long_km - 3) * 1000, "pace": ["5:05", "5:30"]},
            {"type": "interval", "distance_meters": 3000, "pace": HM_PACE},
        ], "Goal pace on tired legs, the most race-specific thing you can do this early."))
    else:
        out.append((6, "Half marathon", "running", [{"type": "interval", "distance_meters": 21100, "pace": HM_PACE}],
                    "Race day."))
    return out


def _flat(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A built workout's steps with repeats unrolled, in the order the watch runs them."""
    out: list[dict[str, Any]] = []
    for step in sorted(steps, key=lambda s: s.get("stepOrder") or 0):
        if step.get("type") == "RepeatGroupDTO" or step.get("numberOfIterations"):
            inner = _flat(step.get("workoutSteps") or [])
            for _ in range(int(step.get("numberOfIterations") or 1)):
                out.extend(copy.deepcopy(inner))
        else:
            out.append(step)
    return out


# How the demo runner ran each kind of step: seconds per km, and heart rate
# at the start and end of the lap. Reps drift up a little each time.
_RUN = {"warmup": (324, 128, 140), "cooldown": (330, 140, 142), "recovery": (300, 150, 146), "easy": (321, 130, 144)}


def _laps_for(workout: dict[str, Any], seed: int, today_run: bool) -> list[dict[str, Any]]:
    laps: list[dict[str, Any]] = []
    rep = 0
    for step in _flat([s for seg in workout.get("workoutSegments") or [] for s in seg.get("workoutSteps") or []]):
        kind = ((step.get("stepType") or {}).get("stepTypeKey") or "interval").lower()
        cond = ((step.get("endCondition") or {}).get("conditionTypeKey") or "").lower()
        value = float(step.get("endConditionValue") or 0)
        one, two = step.get("targetValueOne"), step.get("targetValueTwo")
        paced = ((step.get("targetType") or {}).get("workoutTargetTypeKey") or "") == "pace.zone" and one and two
        if kind == "interval" and paced:
            quick, slow = sorted((1000 / float(one), 1000 / float(two)))
            if slow - quick > 20 or cond == "time":
                pace, hr0, hr1 = (quick + slow) / 2 + (seed % 5) - 2, 132, 146
            else:
                # The reps of a hard session, on target, the last one working harder.
                pace = quick + [3, 1, 6, 4][rep % 4]
                hr0, hr1 = [(150, 168), (154, 171), (158, 178), (156, 174)][rep % 4]
                rep += 1
        elif kind == "interval":
            pace, hr0, hr1 = 230, 140, 158
        else:
            pace, hr0, hr1 = _RUN.get(kind, _RUN["easy"])
        if cond == "distance":
            metres, seconds = value, value / 1000 * pace
        elif cond == "time":
            seconds, metres = value, value * 1000 / pace
        else:
            seconds, metres = 60.0, 60 * 1000 / 330
        laps.append({"metres": round(metres), "seconds": round(seconds), "hr0": hr0, "hr1": hr1,
                     "intensity": {"warmup": "WARMUP", "cooldown": "COOLDOWN", "recovery": "RECOVERY"}.get(kind, "ACTIVE")})
    return laps


class DemoGarmin(FakeGarmin):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.today = date.today()
        self.ran_today = os.environ.get("DEMO_RAN") == "1"
        self.low = os.environ.get("DEMO_READINESS") == "low"
        monday = self.today - timedelta(days=self.today.weekday())
        first = monday - timedelta(weeks=THIS_WEEK - 1)
        last = first + timedelta(weeks=WEEKS - 1, days=6)
        race = {"label": "Half marathon", "date": last.isoformat(), "target": "under 1:35", "tier": "goal"}
        self.scheduled = []
        self.demo_activities: dict[int, dict[str, Any]] = {}
        self.demo_laps: dict[int, list[dict[str, Any]]] = {}
        built: dict[tuple[str, str], int] = {}
        next_activity = 880001
        for week in range(1, WEEKS + 1):
            for weekday, name, sport, steps, note in _sessions(week):
                day = first + timedelta(weeks=week - 1, days=weekday)
                description = training_plan.describe(
                    GOAL, LABEL, first, last, note, name="12-week half", phase=_phase(week),
                    milestones=[race], phases=PHASES)
                title = training_plan.tagged(name, LABEL)
                key = (title, _phase(week))
                if key not in built:
                    if sport == "strength":
                        model, _, _ = build_strength_workout(title, [
                            {"exercise": "Dumbbell Split Squat", "sets": 3, "reps": 8},
                            {"exercise": "Romanian Deadlift", "sets": 3, "reps": 8},
                            {"exercise": "Calf Raise", "sets": 3, "reps": 15},
                            {"exercise": "Plank", "sets": 3, "duration_seconds": 40},
                        ], description)
                    else:
                        model, _, _ = build_workout(title, sport, steps, description)
                    wid = self.upload_workout(model.to_dict())["workoutId"]
                    built[key] = wid
                wid = built[key]
                self.schedule_workout(wid, day.isoformat())
                if day < self.today or (day == self.today and self.ran_today):
                    self._record(next_activity, day, wid, sport, name)
                    next_activity += 1

    # -- recorded runs ------------------------------------------------------

    def _record(self, activity_id: int, day: date, workout_id: int, sport: str, name: str) -> None:
        workout = self.uploaded[workout_id]
        if sport == "strength":
            laps = [{"metres": 0, "seconds": 2100, "hr0": 92, "hr1": 104, "intensity": "ACTIVE"}]
        else:
            laps = _laps_for(workout, activity_id, day == self.today)
        metres = sum(l["metres"] for l in laps)
        seconds = sum(l["seconds"] for l in laps)
        hr = round(sum((l["hr0"] + l["hr1"]) / 2 * l["seconds"] for l in laps) / seconds)
        start = datetime.combine(day, datetime.min.time()).replace(hour=18 if day == self.today else 7, minute=5)
        type_key = "strength_training" if sport == "strength" else "running"
        self.demo_activities[activity_id] = {
            "activityId": activity_id, "activityName": name,
            "activityType": {"typeKey": type_key}, "startTimeLocal": start.strftime("%Y-%m-%d %H:%M:%S"),
            "distance": float(metres), "duration": float(seconds), "movingDuration": float(seconds),
            "averageSpeed": metres / seconds if metres else None, "averageHR": hr,
            "maxHR": max(l["hr1"] for l in laps), "workoutId": workout_id,
        }
        self.demo_laps[activity_id] = laps

    def get_activities(self, start: int, limit: int) -> list[dict[str, Any]]:
        newest = sorted(self.demo_activities.values(), key=lambda a: a["startTimeLocal"], reverse=True)
        return [dict(a) for a in newest[start:start + limit]]

    def get_activities_by_date(self, start: str, end: str) -> list[dict[str, Any]]:
        return [dict(a) for a in self.demo_activities.values() if start <= a["startTimeLocal"][:10] <= end]

    def get_activity(self, activity_id: int) -> dict[str, Any]:
        a = self.demo_activities.get(int(activity_id))
        if not a:
            return super().get_activity(activity_id)
        summary = {k: a[k] for k in ("distance", "duration", "movingDuration", "averageHR", "maxHR", "startTimeLocal")}
        return {"activityId": a["activityId"], "activityName": a["activityName"],
                "activityTypeDTO": {"typeKey": a["activityType"]["typeKey"]}, "summaryDTO": summary}

    def get_activity_splits(self, activity_id: int) -> dict[str, Any]:
        laps = self.demo_laps.get(int(activity_id))
        if laps is None:
            return super().get_activity_splits(activity_id)
        return {"lapDTOs": [
            {"lapIndex": i + 1, "intensityType": l["intensity"], "distance": float(l["metres"]),
             "duration": float(l["seconds"]), "movingDuration": float(l["seconds"]),
             "averageHR": round((l["hr0"] + l["hr1"]) / 2), "maxHR": l["hr1"]}
            for i, l in enumerate(laps)]}

    def get_activity_details(self, activity_id: int, maxchart: int = 2000, maxpoly: int = 4000) -> dict[str, Any]:
        laps = self.demo_laps.get(int(activity_id))
        if laps is None:
            return super().get_activity_details(activity_id, maxchart, maxpoly)
        return _stream([(float(l["metres"]), float(l["seconds"]), float(l["hr0"]), float(l["hr1"]))
                        for l in laps if l["metres"]], step=5.0)

    def get_activity_hr_in_timezones(self, activity_id: int) -> list[dict[str, Any]]:
        return []

    def get_activity_weather(self, activity_id: Any) -> dict[str, Any]:
        return {}

    def get_activity_gear(self, activity_id: Any) -> list[dict[str, Any]]:
        return []

    # -- the calendar --------------------------------------------------------

    def get_scheduled_workouts(self, year: int, month: int) -> dict[str, Any]:
        prefix = f"{int(year):04d}-{int(month):02d}"
        return {"calendarItems": [dict(s) for s in self.scheduled if s["date"].startswith(prefix)]}

    def get_workout_by_id(self, workout_id: int) -> dict[str, Any]:
        up = self.uploaded.get(int(workout_id))
        return {**copy.deepcopy(up), "workoutId": int(workout_id)} if up else {}

    def update_workout(self, workout_id: int, workout_json: Any) -> dict[str, Any]:
        if int(workout_id) not in self.uploaded:
            raise ValueError(f"404 for workout {workout_id}")
        self.uploaded[int(workout_id)] = copy.deepcopy(dict(workout_json))
        for s in self.scheduled:
            if s["workoutId"] == int(workout_id) and workout_json.get("workoutName"):
                s["title"] = workout_json["workoutName"]
        return {"workoutId": int(workout_id)}

    def schedule_workout(self, workout_id: int, date_str: str) -> dict[str, Any]:
        result = super().schedule_workout(workout_id, date_str)
        sport = ((self.uploaded.get(int(workout_id)) or {}).get("sportType") or {}).get("sportTypeKey")
        if sport:
            self.scheduled[-1]["sportTypeKey"] = sport
        return result

    def get_device_last_used(self) -> dict[str, Any]:
        synced = datetime.combine(self.today, datetime.min.time()).replace(hour=7, minute=12)
        return {"userProfileNumber": self.profile_id, "lastUsedDeviceName": "Forerunner 265",
                "lastUsedDeviceUploadTime": int(synced.timestamp() * 1000)}

    # -- the morning ---------------------------------------------------------

    def get_training_readiness(self, day: str) -> list[dict[str, Any]]:
        rows = super().get_training_readiness(day)
        for r in rows:
            r["calendarDate"] = self.today.isoformat()
            if self.low:
                r.update(score=38, level="LOW", feedbackShort="LOW_READINESS", sleepScore=54,
                         sleepScoreFactorFeedback="POOR", hrvFactorFeedback="UNBALANCED")
            else:
                r.update(score=72, level="HIGH", feedbackShort="HIGH_READINESS")
        return rows

    def get_stats(self, day: str) -> dict[str, Any]:
        stats = dict(super().get_stats(day))
        stats["restingHeartRate"] = 53 if self.low else 46
        stats["lastSevenDaysAvgRestingHeartRate"] = 48
        return stats

    def get_sleep_data(self, day: str) -> dict[str, Any]:
        sleep = copy.deepcopy(super().get_sleep_data(day))
        dto = sleep["dailySleepDTO"]
        if self.low:
            dto["sleepTimeSeconds"] = 21000
            dto["sleepScores"]["overall"] = {"value": 54, "qualifierKey": "POOR"}
        else:
            dto["sleepTimeSeconds"] = 27720
            dto["sleepScores"]["overall"] = {"value": 81, "qualifierKey": "GOOD"}
        return sleep


# -- a crew to run with ------------------------------------------------------------

# Friends' weeks, by weekday: (day, type). Their usual times are what places
# each run in the morning, afternoon or evening.
FRIENDS = [
    ("Tom", ["weekday_evening", "weekend_morning"], [(1, "easy"), (2, "easy"), (3, "threshold"), (6, "long")]),
    ("Priya", ["weekday_morning", "weekend_morning"], [(0, "easy"), (2, "easy"), (3, "easy"), (4, "easy"), (6, "long")]),
    ("Nic", ["weekday_morning", "weekend_afternoon"], [(1, "threshold"), (3, "easy"), (5, "easy"), (6, "long")]),
    ("Jo", ["weekday_evening", "weekend_evening"], [(0, "easy"), (2, "threshold"), (4, "easy")]),
]


def _friend_plan(monday: date, runs: list[tuple[int, str]]) -> dict[str, Any]:
    sessions = [{"date": (monday + timedelta(days=d, weeks=w)).isoformat(), "type": t, "sport": "run", "kind": "run",
                 "status": "ahead", "planned_km": 16.0 if t == "long" else 8.0} for w in (0, 1) for d, t in runs]
    return {"weeks": [{"starts": (monday + timedelta(weeks=w)).isoformat(),
                       "sessions": [s for s in sessions if s["date"][:10] >= (monday + timedelta(weeks=w)).isoformat()
                                    and s["date"][:10] < (monday + timedelta(weeks=w + 1)).isoformat()]} for w in (0, 1)]}


def seed_crew(member_key: str, today: date | None = None) -> str:
    """Start HCRC with the demo runner in it, four friends on Daash and one added by hand. Returns its code."""
    from garmin_mcp import crews

    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    token = crews.use(crews.Member(member_key))
    try:
        code = crews.start("HCRC", "Sam", ["weekday_morning", "weekend_morning"])["code"]
        link = crews.set_link(True)
    finally:
        crews.reset(token)
    for name, times, runs in FRIENDS:
        key = f"demo:{name.lower()}"
        token = crews.use(crews.Member(key))
        try:
            crews.join(code, name, times)
        finally:
            crews.reset(token)
        crews.capture(key, _friend_plan(monday, runs), today)
    crews.add_runner(link, "Ellie", "2468", ["tue_morning", "sat_morning"], today)
    return code
