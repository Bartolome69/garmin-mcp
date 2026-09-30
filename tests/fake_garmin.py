"""A stand-in for garminconnect.Garmin, returning realistic fixed payloads.

Field names mirror what Garmin Connect actually returns, so the shaping code
gets exercised the same way it would against the real API.
"""

from __future__ import annotations

from typing import Any

PROFILE = {"displayName": "bart-7f3a", "fullName": "Bart T"}

DAILY = {
    "totalSteps": 12345,
    "dailyStepGoal": 10000,
    "totalDistanceMeters": 9200,
    "floorsAscended": 12,
    "totalKilocalories": 2450,
    "activeKilocalories": 820,
    "bmrKilocalories": 1630,
    "restingHeartRate": 48,
    "minHeartRate": 44,
    "maxHeartRate": 165,
    "lastSevenDaysAvgRestingHeartRate": 50,
    "bodyBatteryMostRecentValue": 71,
    "bodyBatteryHighestValue": 95,
    "bodyBatteryLowestValue": 22,
    "bodyBatteryChargedValue": 73,
    "bodyBatteryDrainedValue": 60,
    "averageStressLevel": 28,
    "maxStressLevel": 88,
    "restStressDuration": 21600,
    "highStressDuration": 3600,
    "moderateIntensityMinutes": 30,
    "vigorousIntensityMinutes": 22,
    "intensityMinutesGoal": 150,
    "averageSpo2": 96,
}

SLEEP = {
    "dailySleepDTO": {
        "sleepTimeSeconds": 25920,  # 7h 12m
        "deepSleepSeconds": 4500,
        "lightSleepSeconds": 13000,
        "remSleepSeconds": 5500,
        "awakeSleepSeconds": 900,
        "sleepStartTimestampLocal": 1758500000000,
        "sleepEndTimestampLocal": 1758525920000,
        "averageRespirationValue": 14.2,
        "sleepScores": {
            "overall": {"value": 82, "qualifierKey": "GOOD"},
            "duration": {"qualifierKey": "GOOD"},
            "deep": {"qualifierKey": "FAIR"},
            "remPercentage": {"qualifierKey": "EXCELLENT"},
        },
    },
    "restingHeartRate": 48,
    "avgOvernightHrv": 61,
    "bodyBatteryChange": 54,
    "awakeCount": 2,
}

ACTIVITIES = [
    {
        "activityId": 1111,
        "activityName": "Morning Run",
        "activityType": {"typeKey": "running"},
        "startTimeLocal": "2026-09-22 07:14:03",
        "locationName": "London",
        "distance": 10050.0,
        "duration": 3200.0,
        "movingDuration": 3136.0,
        "averageSpeed": 3.2,
        "averageHR": 152,
        "maxHR": 171,
        "calories": 720,
        "elevationGain": 45.2,
        "averageRunningCadenceInStepsPerMinute": 178.4,
        "aerobicTrainingEffect": 3.4,
        "anaerobicTrainingEffect": 0.8,
        "vO2MaxValue": 52.0,
        "avgGroundContactTime": 218.0,
        "avgGroundContactBalance": 49.7,
        "avgVerticalOscillation": 9.5,
        "avgVerticalRatio": 7.93,
        "avgStrideLength": 118.38,
        "avgPower": 394.0,
        "maxPower": 525.0,
        "normPower": 400.0,
        "hrTimeInZone_1": 600.0,
        "hrTimeInZone_2": 1200.0,
        "hrTimeInZone_3": 1400.0,
    },
    {
        "activityId": 2222,
        "activityName": "Easy Spin",
        "activityType": {"typeKey": "cycling"},
        "startTimeLocal": "2026-09-21 18:02:00",
        "distance": 24000.0,
        "duration": 3900.0,
        "movingDuration": 3800.0,
        "averageSpeed": 6.3,
        "averageHR": 121,
        "maxHR": 148,
        "calories": 610,
        "elevationGain": 210.0,
        "averageBikingCadenceInRevPerMinute": 82.0,
    },
    {
        "activityId": 3333,
        "activityName": "Long Run",
        "activityType": {"typeKey": "running"},
        "startTimeLocal": "2026-09-20 09:00:00",
        "distance": 21100.0,
        "duration": 7200.0,
        "movingDuration": 7100.0,
        "averageHR": 148,
        "maxHR": 166,
        "calories": 1450,
    },
]

SPLITS = {
    "lapDTOs": [
        {
            "lapIndex": 1,
            "distance": 1000.0,
            "duration": 300.0,
            "averageHR": 148,
            "maxHR": 158,
            "elevationGain": 4.0,
            "averageRunCadence": 176.0,
            "groundContactTime": 229.8,
            "verticalOscillation": 9.66,
            "averagePower": 367.0,
            "calories": 72,
        },
        {
            "lapIndex": 2,
            "distance": 1000.0,
            "duration": 296.0,
            "averageHR": 154,
            "maxHR": 163,
            "elevationGain": 6.0,
            "averageRunCadence": 179.0,
            "calories": 74,
        },
    ]
}

HR_ZONES = [
    {"zoneNumber": 1, "secsInZone": 800.0, "zoneLowBoundary": 93},
    {"zoneNumber": 2, "secsInZone": 800.0, "zoneLowBoundary": 112},
    {"zoneNumber": 3, "secsInZone": 1000.0, "zoneLowBoundary": 131},
    {"zoneNumber": 4, "secsInZone": 600.0, "zoneLowBoundary": 150},
]


READINESS = [{
    "calendarDate": "2026-09-30", "score": 58, "level": "MODERATE",
    "feedbackShort": "MODERATE_READINESS_1", "sleepScore": 82,
    "sleepScoreFactorFeedback": "GOOD", "recoveryTime": 14,
    "recoveryTimeFactorFeedback": "MODERATE", "acwrFactorFeedback": "OPTIMAL",
    "hrvFactorFeedback": "BALANCED", "stressHistoryFactorFeedback": "LOW",
    "sleepHistoryFactorFeedback": "GOOD", "primaryTrainingDevice": True,
}]

TRAINING_STATUS = {
    "mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": 60.9, "vo2MaxValue": 61}},
    "mostRecentTrainingLoadBalance": {"metricsTrainingLoadBalanceDTOMap": {"3400": {
        "primaryTrainingDevice": True, "monthlyLoadAerobicLow": 402.0,
        "monthlyLoadAerobicHigh": 188.0, "monthlyLoadAnaerobic": 41.0,
        "monthlyLoadAerobicLowTargetMin": 360.0, "monthlyLoadAerobicLowTargetMax": 720.0,
        "monthlyLoadAerobicHighTargetMin": 180.0, "monthlyLoadAerobicHighTargetMax": 360.0,
        "monthlyLoadAnaerobicTargetMin": 90.0, "monthlyLoadAnaerobicTargetMax": 180.0,
        "trainingBalanceFeedbackPhrase": "ANAEROBIC_SHORTAGE",
    }}},
    "mostRecentTrainingStatus": {"latestTrainingStatusData": {"3400": {
        "primaryTrainingDevice": True, "sinceDate": "2026-09-14",
        "weeklyTrainingLoad": 512, "loadTunnelMin": 430, "loadTunnelMax": 690,
        "trainingStatus": 4, "trainingStatusFeedbackPhrase": "PRODUCTIVE_1",
        "trainingPaused": False,
        "acuteTrainingLoadDTO": {
            "acwrStatus": "OPTIMAL", "dailyTrainingLoadAcute": 350,
            "dailyTrainingLoadChronic": 330, "minTrainingLoadChronic": 280,
            "maxTrainingLoadChronic": 420, "dailyAcuteChronicWorkloadRatio": 1.06,
        },
    }}},
}

HRV = {"hrvSummary": {"weeklyAvg": 58, "lastNightAvg": 61, "lastNight5MinHigh": 78,
                      "baseline": {"lowUpper": 50, "balancedLow": 53, "balancedUpper": 66},
                      "status": "BALANCED"}}

RACE_PREDICTIONS = {"calendarDate": "2026-09-30", "time5K": 1080, "time10K": 2280,
                    "timeHalfMarathon": 5100, "timeMarathon": 10800}

LACTATE = {"speed_and_heart_rate": {"calendarDate": "2026-09-21", "speed": 3.8,
                                    "heartRate": 172}, "power": {}}

ENDURANCE = {"calendarDate": "2026-09-30", "overallScore": 6512,
             "classificationLowerLimitIntermediate": 4900,
             "classificationLowerLimitTrained": 6100,
             "classificationLowerLimitWellTrained": 7300,
             "classificationLowerLimitExpert": 8200,
             "classificationLowerLimitSuperior": 8900,
             "classificationLowerLimitElite": 9400}

HILL = {"calendarDate": "2026-09-30", "overallScore": 46, "strengthScore": 42,
        "enduranceScore": 51, "classificationLowerLimitIntermediate": 30,
        "classificationLowerLimitTrained": 45, "classificationLowerLimitWellTrained": 60}

TOLERANCE = [{"calendarDate": "2026-09-22", "userProfilePK": 1, "weeklyLoad": 48.2,
              "toleranceLimit": 61.0}]


def _stream(laps: list[tuple[float, float, float, float]], step: float = 10.0) -> dict[str, Any]:
    """A recording matching a list of laps: (metres, seconds, hr_start, hr_end).

    Heart rate climbs linearly through each lap so drift is measurable, and
    pace is even, which is the case that makes drift meaningful.
    """
    rows: list[dict[str, Any]] = []
    dist = time = 0.0
    for metres, seconds, hr0, hr1 in laps:
        n = int(seconds // step)
        for i in range(n):
            frac = i / max(n - 1, 1)
            rows.append({"metrics": [time, dist, metres / seconds, hr0 + (hr1 - hr0) * frac]})
            time += step
            dist += metres / n
    rows.append({"metrics": [time, dist, 0.0, laps[-1][3]]})
    return {
        "metricDescriptors": [
            {"metricsIndex": 0, "key": "sumDuration", "unit": {"key": "second"}},
            {"metricsIndex": 1, "key": "sumDistance", "unit": {"key": "meter"}},
            {"metricsIndex": 2, "key": "directSpeed", "unit": {"key": "mps"}},
            {"metricsIndex": 3, "key": "directHeartRate", "unit": {"key": "bpm"}},
        ],
        "activityDetailMetrics": rows,
    }


# Matches SPLITS: two 1 km laps of 300 s and 296 s, HR climbing through each.
RECORDING = _stream([(1000.0, 300.0, 140.0, 156.0), (1000.0, 296.0, 150.0, 158.0)])


class FakeGarmin:
    def __init__(self, **_: Any) -> None:
        self.display_name = None
        self.full_name = None
        self.unscheduled: list[int] = []
        self.deleted: list[int] = []
        # What upload_workout and schedule_workout have written, so a plan that
        # was just created can be read back through the calendar like the real
        # thing. The fixed library and calendar entries below are untouched.
        self.uploaded: dict[int, dict[str, Any]] = {}
        self.scheduled: list[dict[str, Any]] = []
        self._next_workout = 555002
        self._next_schedule = 777001
        self.workouts: list[dict[str, Any]] = [
            {
                "workoutId": 555001,
                "workoutName": "Thursday Threshold",
                "sportType": {"sportTypeKey": "running"},
                "estimatedDurationInSecs": 3175,
                "updateDate": "2026-09-22T19:00:00.0",
            }
        ]

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        self.display_name = PROFILE["displayName"]
        self.full_name = PROFILE["fullName"]
        return (None, None)

    def get_user_profile(self) -> dict[str, Any]:
        return dict(PROFILE)

    def get_stats(self, day: str) -> dict[str, Any]:
        return dict(DAILY)

    def get_sleep_data(self, day: str) -> dict[str, Any]:
        return dict(SLEEP)

    def get_activities(self, start: int, limit: int) -> list[dict[str, Any]]:
        return ACTIVITIES[start : start + limit]

    def get_activities_by_date(self, start: str, end: str) -> list[dict[str, Any]]:
        return list(ACTIVITIES)

    def get_activity(self, activity_id: int) -> dict[str, Any]:
        return {
            "activityId": activity_id,
            "activityName": "Morning Run",
            "activityTypeDTO": {"typeKey": "running"},
            "summaryDTO": {
                "distance": 10050.0,
                "duration": 3200.0,
                "movingDuration": 3136.0,
                "averageHR": 152,
                "maxHR": 171,
                "calories": 720,
                "elevationGain": 45.2,
            },
        }

    def get_activity_splits(self, activity_id: int) -> dict[str, Any]:
        return SPLITS

    def get_activity_hr_in_timezones(self, activity_id: int) -> list[dict[str, Any]]:
        return list(HR_ZONES)

    def get_activity_details(self, activity_id: int, maxchart: int = 2000, maxpoly: int = 4000) -> dict[str, Any]:
        return RECORDING

    # -- readiness and fitness ---------------------------------------------

    def get_training_readiness(self, day: str) -> list[dict[str, Any]]:
        return [dict(r) for r in READINESS]

    def get_training_status(self, day: str) -> dict[str, Any]:
        return TRAINING_STATUS

    def get_hrv_data(self, day: str) -> dict[str, Any]:
        return HRV

    def get_race_predictions(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(RACE_PREDICTIONS)

    def get_lactate_threshold(self, **kwargs: Any) -> dict[str, Any]:
        return LACTATE

    def get_endurance_score(self, start: str, end: str | None = None) -> dict[str, Any]:
        return dict(ENDURANCE)

    def get_hill_score(self, start: str, end: str | None = None) -> dict[str, Any]:
        return dict(HILL)

    def get_running_tolerance(self, start: str, end: str, aggregation: str = "weekly") -> list[dict[str, Any]]:
        return [dict(t) for t in TOLERANCE]

    # -- workouts ----------------------------------------------------------

    def get_workouts(self, start: int, limit: int) -> list[dict[str, Any]]:
        # Reads the mutable library rather than a literal, so a delete is
        # visible through the same path a person would look down. A stub that
        # returned a fixed list would report success for a delete that never
        # happened.
        return list(self.workouts)[start : start + limit]

    def get_scheduled_workouts(self, year: int, month: int) -> dict[str, Any]:
        # Garmin's month view spills into the neighbouring months, so the same
        # session is returned by more than one call. The collector must key by
        # id or it counts each one twice.
        prefix = f"{int(year):04d}-{int(month):02d}"
        return {"calendarItems": [
            {"id": 900001, "itemType": "workout", "date": "2026-09-24",
             "workoutId": 555001, "title": "Thursday Threshold",
             "sportTypeKey": "running"},
            *[dict(s) for s in self.scheduled if s["date"].startswith(prefix)],
        ]}

    def get_workout_by_id(self, workout_id: int) -> dict[str, Any]:
        # Honours the id: delete_workout has to be able to tell an unknown
        # workout from a real one, and a stub that answers to anything would
        # let that branch pass untested. 555002 is what upload_workout mints.
        if int(workout_id) in self.uploaded:
            up = self.uploaded[int(workout_id)]
            return {"workoutId": int(workout_id), "workoutName": up.get("workoutName"),
                    "description": up.get("description"),
                    "estimatedDurationInSecs": up.get("estimatedDurationInSecs"),
                    "sportType": {"sportTypeKey": "running"},
                    "workoutSegments": up.get("workoutSegments") or []}
        known = {w["workoutId"] for w in self.workouts} | {555002}
        if int(workout_id) not in known:
            return {}
        return {
            "workoutName": "Thursday Threshold",
            "estimatedDurationInSecs": 3175,
            "workoutSegments": [{"workoutSteps": [
                {"endCondition": {"conditionTypeKey": "time"}, "endConditionValue": 900},
                {"type": "RepeatGroupDTO", "numberOfIterations": 5, "workoutSteps": [
                    {"endCondition": {"conditionTypeKey": "distance"},
                     "endConditionValue": 1000},
                ]},
            ]}],
        }

    def upload_workout(self, workout_json: Any) -> dict[str, Any]:
        self.last_upload = workout_json
        workout_id = self._next_workout
        self._next_workout += 1
        if isinstance(workout_json, dict):
            self.uploaded[workout_id] = dict(workout_json)
        return {"workoutId": workout_id}

    def update_workout(self, workout_id: int, workout_json: Any) -> dict[str, Any]:
        # Replaces in place, like the real PUT: the id survives, the name in
        # the library changes, and a caller can prove which happened.
        known = {w["workoutId"] for w in self.workouts} | set(self.uploaded)
        if int(workout_id) not in known:
            raise ValueError(f"404 for workout {workout_id}")
        if int(workout_id) in self.uploaded and isinstance(workout_json, dict):
            self.uploaded[int(workout_id)].update(workout_json)
            for s in self.scheduled:
                if s["workoutId"] == int(workout_id) and workout_json.get("workoutName"):
                    s["title"] = workout_json["workoutName"]
        self.last_update = (int(workout_id), workout_json)
        for w in self.workouts:
            if int(w["workoutId"]) == int(workout_id) and isinstance(workout_json, dict):
                if workout_json.get("workoutName"):
                    w["workoutName"] = workout_json["workoutName"]
        return {"workoutId": int(workout_id)}

    def get_max_metrics(self, day: str) -> list[dict[str, Any]]:
        return [{"generic": {"vo2MaxPreciseValue": 60.9, "fitnessAge": None}}]

    def get_personal_record(self) -> list[dict[str, Any]]:
        return [
            {"typeId": 3, "activityType": "running", "value": 1054.48},
            {"typeId": 5, "activityType": "running", "value": 4779.09},
            {"typeId": 8, "activityType": "road_biking", "value": 163883.9},
        ]

    def schedule_workout(self, workout_id: int, date_str: str) -> dict[str, Any]:
        self.last_schedule = (workout_id, date_str)
        schedule_id = self._next_schedule
        self._next_schedule += 1
        title = (self.uploaded.get(int(workout_id)) or {}).get("workoutName") or next(
            (w["workoutName"] for w in self.workouts if w["workoutId"] == int(workout_id)), "Workout")
        self.scheduled.append({"id": schedule_id, "itemType": "workout", "date": date_str,
                               "workoutId": int(workout_id), "title": title,
                               "sportTypeKey": "running"})
        return {"workoutScheduleId": schedule_id}

    def unschedule_workout(self, scheduled_workout_id: int) -> dict[str, Any]:
        self.unscheduled.append(int(scheduled_workout_id))
        self.scheduled = [s for s in self.scheduled if s["id"] != int(scheduled_workout_id)]
        return {}

    def delete_workout(self, workout_id: int) -> dict[str, Any]:
        # Actually removes it, so a test can prove the unconfirmed call left
        # the library alone. Asserting only the response would pass even if the
        # workout had been destroyed on the way to producing it.
        self.deleted.append(int(workout_id))
        self.uploaded.pop(int(workout_id), None)
        self.workouts = [
            w for w in self.workouts if int(w["workoutId"]) != int(workout_id)
        ]
        return {}
