"""Garmin's official API standing in for the Garmin Connect client, for the tools that write.

The tools talk to a garminconnect client: upload a workout, schedule it,
read the calendar, list activities. For an account on the official API this
object answers the same calls from Garmin's Training API and from what
Garmin has pushed. The Training API can't list a person's workouts or their
calendar beyond what this app put there, so the store keeps what Daash
creates, and the calendar is those schedules. Anything it can't answer
raises NotOnOfficial, which is an AttributeError, so `getattr(client, name,
None)` says no the way it would for an old garminconnect.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from . import official, official_data, store


class NotOnOfficial(AttributeError):
    """The official API has no answer for this call."""


class OfficialClient:
    def __init__(self, user_token: str, garmin_user_id: str, alone: bool = False) -> None:
        self.user_token = user_token
        self.garmin_user_id = garmin_user_id
        # No Garmin Connect session behind the account to fall back on.
        self.alone = alone

    def __getattr__(self, name: str) -> Any:
        raise NotOnOfficial(f"Garmin's official API has no {name}.")

    def run(self, fn: Any) -> Any:
        return fn(self)

    # ---------------------------------------------------------------- workouts
    def _may_write(self) -> None:
        if not official.can_write(self.user_token):
            raise official.OfficialError(
                "Putting workouts on the watch was left off when you approved Daash in Garmin. Turn on "
                "workout import for Daash in Garmin Connect's connected apps settings, then ask again.")

    def upload_workout(self, payload: dict) -> dict:
        """Create the workout on Garmin and keep it here, as the tools shaped it."""
        self._may_write()
        created = official.call(self.user_token, "POST", "/workoutportal/workout/v2", official.training_workout(payload))
        workout_id = created.get("workoutId") if isinstance(created, dict) else None
        if workout_id is None:
            raise official.OfficialError("Garmin accepted the workout but returned no id.")
        self._keep(int(workout_id), payload, created)
        return {"workoutId": int(workout_id)}

    def update_workout(self, workout_id: int, payload: dict) -> dict:
        self._may_write()
        kept = store.official_workout(self.garmin_user_id, int(workout_id)) or {}
        training = official.training_workout(payload)
        training["workoutId"] = int(workout_id)
        owner = (kept.get("created") or {}).get("ownerId")
        if owner is not None:
            training["ownerId"] = owner
        answer = official.call(self.user_token, "PUT", f"/training-api/workout/v2/{int(workout_id)}", training)
        self._keep(int(workout_id), payload, answer if isinstance(answer, dict) and answer else kept.get("created") or {})
        return {"workoutId": int(workout_id)}

    def get_workout_by_id(self, workout_id: int) -> dict | None:
        kept = store.official_workout(self.garmin_user_id, int(workout_id))
        if kept:
            return kept["connect"]
        fetched = official.call(self.user_token, "GET", f"/training-api/workout/v2/{int(workout_id)}")
        if not isinstance(fetched, dict) or not fetched:
            return None
        connect = official.connect_workout(fetched)
        self._keep(int(workout_id), connect, fetched)
        return connect

    def get_workouts(self, start: int = 0, limit: int = 20) -> list[dict]:
        rows = [kept["connect"] for kept in store.official_workouts(self.garmin_user_id, start + limit)]
        return [{"workoutId": w.get("workoutId"), "workoutName": w.get("workoutName"), "sportType": w.get("sportType"),
                 "estimatedDurationInSecs": w.get("estimatedDurationInSecs"), "updateDate": w.get("updateDate")}
                for w in rows[start:start + limit]]

    def delete_workout(self, workout_id: int) -> None:
        official.call(self.user_token, "DELETE", f"/training-api/workout/v2/{int(workout_id)}")
        store.delete_official_workout(self.garmin_user_id, int(workout_id))

    def _keep(self, workout_id: int, connect: dict, created: Any) -> None:
        connect = dict(connect)
        connect["workoutId"] = workout_id
        if isinstance(created, dict):
            for key in ("estimatedDurationInSecs", "estimatedDistanceInMeters", "updatedDate", "createdDate"):
                if created.get(key) is not None:
                    connect[key] = created[key]
            connect["updateDate"] = created.get("updatedDate") or created.get("createdDate")
        store.save_official_workout(self.garmin_user_id, workout_id,
                                    {"connect": connect, "created": created if isinstance(created, dict) else {}})

    # ---------------------------------------------------------------- the calendar
    def schedule_workout(self, workout_id: int, day: str) -> dict:
        self._may_write()
        answer = official.call(self.user_token, "POST", "/training-api/schedule/", {"workoutId": int(workout_id), "date": day})
        schedule_id = answer.get("scheduleId") if isinstance(answer, dict) else answer
        if schedule_id is None:
            raise official.OfficialError("Garmin scheduled the workout but returned no schedule id.")
        store.save_official_schedule(self.garmin_user_id, int(schedule_id), int(workout_id), day)
        return {"workoutScheduleId": int(schedule_id), "workoutId": int(workout_id), "date": day}

    def unschedule_workout(self, schedule_id: int) -> None:
        official.call(self.user_token, "DELETE", f"/training-api/schedule/{int(schedule_id)}")
        store.delete_official_schedule(self.garmin_user_id, int(schedule_id))

    def get_scheduled_workouts(self, year: int, month: int) -> list[dict]:
        """A month of the calendar as Garmin Connect lists it: what Daash scheduled."""
        first = date(int(year), int(month), 1)
        last = date(int(year) + (int(month) == 12), int(month) % 12 + 1, 1)
        rows = store.official_schedules(self.garmin_user_id, first.isoformat(), last.isoformat())
        items = []
        for schedule_id, workout_id, day in rows:
            if day >= last.isoformat():
                continue
            workout = (store.official_workout(self.garmin_user_id, workout_id) or {}).get("connect") or {}
            items.append({"id": schedule_id, "workoutScheduleId": schedule_id, "workoutId": workout_id, "date": day,
                          "itemType": "workout", "title": workout.get("workoutName"), "workoutName": workout.get("workoutName"),
                          "sportTypeKey": (workout.get("sportType") or {}).get("sportTypeKey")})
        return items

    # ---------------------------------------------------------------- activities
    def get_activities_by_date(self, start: str, end: str | None = None, *_: Any) -> list[dict]:
        rows = official_data.activity_rows(self.garmin_user_id, str(start)[:10], str(end or date.today())[:10])
        return official_data.connect_activities(self.garmin_user_id, rows)

    def get_activities(self, start: int = 0, limit: int = 20) -> list[dict]:
        rows = official_data.activity_rows(self.garmin_user_id)[start:start + limit]
        return official_data.connect_activities(self.garmin_user_id, rows)
