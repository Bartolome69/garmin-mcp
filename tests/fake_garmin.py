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


class FakeGarmin:
    def __init__(self, **_: Any) -> None:
        self.display_name = None
        self.full_name = None

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

    # -- workouts ----------------------------------------------------------

    def get_workouts(self, start: int, limit: int) -> list[dict[str, Any]]:
        return [
            {
                "workoutId": 555001,
                "workoutName": "Thursday Threshold",
                "sportType": {"sportTypeKey": "running"},
                "estimatedDurationInSecs": 3175,
                "updateDate": "2026-09-22T19:00:00.0",
            }
        ][:limit]

    def upload_workout(self, workout_json: Any) -> dict[str, Any]:
        self.last_upload = workout_json
        return {"workoutId": 555002}

    def schedule_workout(self, workout_id: int, date_str: str) -> dict[str, Any]:
        self.last_schedule = (workout_id, date_str)
        return {"workoutScheduleId": 777001}
