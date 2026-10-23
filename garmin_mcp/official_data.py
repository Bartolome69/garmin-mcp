"""What Garmin's official API sends, kept small and read back for the tools.

Garmin pushes every summary type the app is set up for, raw sensor streams
included. Daash keeps the summaries it answers training questions from, and
trims those: an activity's samples (GPS, second-by-second heart rate) become
its kilometre splits and laps, and a day's heart rate, stress and Body
Battery curves become their highs and lows. Everything else is dropped as it
arrives, as the privacy page says.
"""

from __future__ import annotations

import calendar
import time
from typing import Any, Iterable

from . import store, stream, wellness
from .formatting import drop_empty, duration, pace_per_km

# The kinds Daash keeps, by the key Garmin pushes them under.
KEPT = {"dailies", "sleeps", "hrv", "stressDetails", "userMetrics", "bodyComps", "pulseOx",
        "activities", "manuallyUpdatedActivities", "activityDetails"}
# Kinds Daash makes itself: the figures read from an activity's FIT file.
OWN = {"activityFit"}
# Curves and streams dropped from what is kept, by kind.
_DROP_FIELDS = {
    "dailies": ("timeOffsetHeartRateSamples",),
    "sleeps": ("sleepLevelsMap", "timeOffsetSleepRespiration", "timeOffsetSleepSpo2"),
    "hrv": ("hrvValues",),
    "pulseOx": ("timeOffsetSpo2Values",),
    "stressDetails": ("timeOffsetStressLevelValues", "timeOffsetBodyBatteryValues",
                      "bodyBatteryActivityEventList", "bodyBatteryDynamicFeedbackEvent"),
}
ACTIVITY_KINDS = ("activities", "manuallyUpdatedActivities")


def _values(curve: Any) -> list[float]:
    if not isinstance(curve, dict):
        return []
    return [round(v) for _, v in sorted(curve.items(), key=lambda kv: int(kv[0]))
            if isinstance(v, (int, float)) and v >= 0]


def _splits(samples: list[dict], laps: list[dict]) -> tuple[list[dict], list[dict]]:
    """Kilometre splits and laps from an activity's samples: metres, seconds and average heart rate."""
    points = [s for s in samples if isinstance(s, dict) and isinstance(s.get("totalDistanceInMeters"), (int, float))
              and isinstance(s.get("timerDurationInSeconds"), (int, float))]
    if len(points) < 2:
        return [], []

    def stretch(chunk: list[dict], start: dict) -> dict:
        metres = chunk[-1]["totalDistanceInMeters"] - start["totalDistanceInMeters"]
        seconds = chunk[-1]["timerDurationInSeconds"] - start["timerDurationInSeconds"]
        hr = [s["heartRate"] for s in chunk if isinstance(s.get("heartRate"), (int, float)) and s["heartRate"] > 0]
        cadence = [s["stepsPerMinute"] for s in chunk if isinstance(s.get("stepsPerMinute"), (int, float)) and s["stepsPerMinute"] > 0]
        heights = [s["elevationInMeters"] for s in [start] + chunk if isinstance(s.get("elevationInMeters"), (int, float))]
        climb = sum(b - a for a, b in zip(heights, heights[1:]) if b > a)
        return drop_empty({"m": round(metres, 1), "s": round(seconds, 1),
                           "hr": round(sum(hr) / len(hr)) if hr else None, "mhr": max(hr) if hr else None,
                           "cad": round(sum(cadence) / len(cadence)) if cadence else None,
                           "up": round(climb) if heights else None})

    kms, start, chunk, mark = [], points[0], [], 1000.0
    for point in points[1:]:
        chunk.append(point)
        if point["totalDistanceInMeters"] >= mark:
            kms.append(stretch(chunk, start))
            start, chunk, mark = point, [], mark + 1000
    if chunk and chunk[-1]["totalDistanceInMeters"] - start["totalDistanceInMeters"] >= 100:
        kms.append(stretch(chunk, start))

    starts = sorted(int(lap["startTimeInSeconds"]) for lap in laps
                    if isinstance(lap, dict) and isinstance(lap.get("startTimeInSeconds"), (int, float)))
    out_laps = []
    if len(starts) > 1:
        for i, begin in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else None
            inside = [p for p in points if (p.get("startTimeInSeconds") or 0) >= begin
                      and (end is None or (p.get("startTimeInSeconds") or 0) < end)]
            before = [p for p in points if (p.get("startTimeInSeconds") or 0) < begin]
            if inside:
                out_laps.append(stretch(inside, before[-1] if before else inside[0]))
    return kms, out_laps


def _recording(samples: list[dict]) -> dict:
    """The samples as Garmin Connect's activity recording, for the stream analyser."""
    rows = [{"metrics": [s.get("totalDistanceInMeters"), s.get("timerDurationInSeconds"),
                         s.get("heartRate") if isinstance(s.get("heartRate"), (int, float)) and s["heartRate"] > 0 else None]}
            for s in samples if isinstance(s, dict)]
    return {"metricDescriptors": [{"key": "sumDistance", "metricsIndex": 0, "unit": {"key": "meter"}},
                                  {"key": "sumDuration", "metricsIndex": 1, "unit": {"key": "second"}},
                                  {"key": "directHeartRate", "metricsIndex": 2, "unit": {"key": "bpm"}}],
            "activityDetailMetrics": rows}


def connect_laps(splits: Any) -> list[dict]:
    """Kept splits or laps as Garmin Connect's lap rows."""
    return [{"lapIndex": i, "distance": x["m"], "duration": x["s"], "averageHR": x.get("hr"), "maxHR": x.get("mhr"),
             "averageRunCadence": x.get("cad"), "elevationGain": x.get("up")}
            for i, x in enumerate(raw_splits(splits), start=1)]


def _iso(seconds: Any) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) if isinstance(seconds, (int, float)) else None


def trim_sleep(item: dict) -> dict:
    """A night with its breathing and SpO2 as averages, before the curves go."""
    breaths = [v for v in _curve(item.get("timeOffsetSleepRespiration")) if v > 0]
    spo2 = [v for v in _curve(item.get("timeOffsetSleepSpo2")) if v > 0]
    kept = trim("sleeps", item) or {}
    if breaths:
        kept["breathing"] = {"average": round(sum(breaths) / len(breaths), 1), "lowest": round(min(breaths), 1),
                             "highest": round(max(breaths), 1)}
    if spo2:
        kept["spo2"] = {"average": round(sum(spo2) / len(spo2)), "lowest": round(min(spo2))}
    return kept


def trim_pulse_ox(item: dict) -> dict:
    spo2 = [v for v in _curve(item.get("timeOffsetSpo2Values")) if v > 0]
    kept = trim("pulseOx", item) or {}
    if spo2:
        kept["spo2"] = {"average": round(sum(spo2) / len(spo2)), "lowest": round(min(spo2))}
    return kept


def _curve(curve: Any) -> list[float]:
    if not isinstance(curve, dict):
        return []
    return [float(v) for _, v in sorted(curve.items(), key=lambda kv: int(kv[0])) if isinstance(v, (int, float))]


def trim(kind: str, item: dict) -> dict | None:
    """What Daash keeps of one summary, or None when it keeps none of that kind."""
    if kind not in KEPT:
        return None
    item = {k: v for k, v in item.items() if k not in _DROP_FIELDS.get(kind, ())}
    if kind == "activityDetails":
        samples, laps = item.pop("samples", None), item.pop("laps", None)
        if isinstance(samples, list):
            kms, out_laps = _splits(samples, laps if isinstance(laps, list) else [])
            item["splits"], item["laps"] = kms or None, out_laps or None
            # What the stream says beyond the splits: heart rate drift across
            # each long lap, and kilometre splits the laps don't already give.
            try:
                item["inside"] = stream.analyse(_recording(samples), connect_laps(out_laps))
            except Exception:  # noqa: BLE001 - an odd recording must not lose the splits
                item["inside"] = None
        item = drop_empty(item)
    return item


def trim_stress(item: dict) -> dict:
    """A day's stress details as their highs and lows, before the curves are dropped."""
    battery = _values(item.get("timeOffsetBodyBatteryValues"))
    stress = _values(item.get("timeOffsetStressLevelValues"))
    kept = trim("stressDetails", item) or {}
    if battery:
        kept["bodyBattery"] = {"start": battery[0], "end": battery[-1], "high": max(battery), "low": min(battery)}
    if stress:
        kept["stress"] = {"average": round(sum(stress) / len(stress)), "high": max(stress)}
    start, offset = item.get("startTimeInSeconds"), _offset(item)
    # The stressful spells, worked out now from the curve that isn't kept,
    # the way the daily summary works them out from Garmin Connect's.
    if isinstance(start, (int, float)) and isinstance(item.get("timeOffsetStressLevelValues"), dict):
        pairs = [[(start + int(k)) * 1000, v] for k, v in item["timeOffsetStressLevelValues"].items()
                 if str(k).lstrip("-").isdigit() and isinstance(v, (int, float)) and v >= 0]
        spells = wellness.stress_spells({"startTimestampGMT": _iso(start), "startTimestampLocal": _iso(start + offset),
                                         "stressValuesArray": pairs})
        if spells:
            kept["stressfulSpells"] = spells
    # And what charged or drained Body Battery, in the shape Connect's events
    # come in, so the same reader describes them.
    events = []
    for event in item.get("bodyBatteryActivityEventList") or []:
        if not isinstance(event, dict) or not isinstance(event.get("eventStartTimeInSeconds"), (int, float)):
            continue
        at = event["eventStartTimeInSeconds"]
        off = event.get("eventStartTimeOffsetInSeconds")
        length = event.get("duration") if isinstance(event.get("duration"), (int, float)) else event.get("durationInSeconds")
        events.append({"event": drop_empty({
            "eventType": event.get("eventType"), "eventStartTimeGmt": _iso(at),
            "timezoneOffset": (off if isinstance(off, (int, float)) else offset) * 1000,
            "durationInMilliseconds": length * 1000 if isinstance(length, (int, float)) else None,
            "bodyBatteryImpact": event.get("bodyBatteryImpact")})})
    if events:
        kept["bodyBatteryEvents"] = events
    return kept


def keep(kind: str, item: dict) -> dict | None:
    if kind == "stressDetails":
        return trim_stress(item)
    if kind == "sleeps":
        return trim_sleep(item)
    if kind == "pulseOx":
        return trim_pulse_ox(item)
    return trim(kind, item)


def tidy() -> int:
    """Bring what was kept before trimming into line: drop other kinds, trim the rest."""
    dropped = store.drop_official_kinds(KEPT | OWN)
    for garmin_user_id, kind, summary_id, start, data in store.all_official_summaries(("activityDetails", "dailies",
                                                                                         "sleeps", "hrv", "stressDetails")):
        trimmed = keep(kind, data)
        # Details kept before their start was read from inside the summary.
        if start is None and isinstance(data.get("summary"), dict):
            start = data["summary"].get("startTimeInSeconds")
        if trimmed is not None and (trimmed != data or start is not None):
            store.save_official_summaries(garmin_user_id, kind, [(summary_id, start, trimmed)])
    return dropped


# --------------------------------------------------------------------------
# Reading it back
# --------------------------------------------------------------------------


def _seconds_of(text: str) -> float | None:
    """'1h 02m 03s', '5m 06s' or '43s' back to seconds, for splits kept before they were kept raw."""
    total, found = 0.0, False
    for part in text.split():
        for unit, scale in (("h", 3600), ("m", 60), ("s", 1)):
            if part.endswith(unit) and part[:-1].isdigit():
                total, found = total + int(part[:-1]) * scale, True
    return total if found else None


def raw_splits(splits: Any) -> list[dict]:
    """Splits or laps as metres, seconds and heart rate, whichever way they were kept."""
    out = []
    for x in splits if isinstance(splits, list) else []:
        if not isinstance(x, dict):
            continue
        if "m" in x:
            out.append(x)
        elif isinstance(x.get("km"), (int, float)) and isinstance(x.get("time"), str):
            out.append(drop_empty({"m": x["km"] * 1000, "s": _seconds_of(x["time"]), "hr": x.get("avg_hr")}))
    return [x for x in out if x.get("m") and x.get("s")]


def _split_view(x: dict) -> dict:
    return drop_empty({"km": round(x["m"] / 1000, 2), "time": duration(x["s"]), "pace": pace_per_km(x["m"], x["s"]),
                       "avg_hr": x.get("hr")})


def _local(seconds: Any, offset: Any) -> str | None:
    if not isinstance(seconds, (int, float)):
        return None
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(seconds + (offset if isinstance(offset, (int, float)) else 0)))


def _round(value: Any) -> int | None:
    return round(value) if isinstance(value, (int, float)) else None


def _minutes(seconds: Any) -> int | None:
    return round(seconds / 60) if isinstance(seconds, (int, float)) else None


def activities(garmin_user_id: str, days: int = 14) -> list[dict]:
    """The account's recent activities, newest first, with splits when Garmin sent the details."""
    since = int(time.time()) - days * 86400
    details = {str(d.get("activityId") or d.get("summaryId", "").removesuffix("-detail")): d
               for d in store.official_summaries(garmin_user_id, "activityDetails", since - 86400)}
    seen: dict[str, dict] = {}
    for kind in ACTIVITY_KINDS:
        for a in store.official_summaries(garmin_user_id, kind, since):
            seen[str(a.get("activityId") or a.get("summaryId"))] = a
    out = []
    for activity_id, a in seen.items():
        metres, seconds = a.get("distanceInMeters"), a.get("durationInSeconds")
        detail = details.get(activity_id) or {}
        out.append(drop_empty({
            "activity_id": activity_id,
            "start": _local(a.get("startTimeInSeconds"), a.get("startTimeOffsetInSeconds")),
            "name": a.get("activityName"),
            "type": str(a.get("activityType") or "").lower() or None,
            "km": round(metres / 1000, 2) if isinstance(metres, (int, float)) else None,
            "time": duration(seconds),
            "pace": pace_per_km(metres, seconds) if metres else None,
            "avg_hr": a.get("averageHeartRateInBeatsPerMinute"),
            "max_hr": a.get("maxHeartRateInBeatsPerMinute"),
            "cadence": _round(a.get("averageRunCadenceInStepsPerMinute") or a.get("averageBikeCadenceInRoundsPerMinute")),
            "elevation_gain_m": a.get("totalElevationGainInMeters"),
            "kcal": a.get("activeKilocalories"),
            "device": a.get("deviceName"),
            "splits": [_split_view(x) for x in raw_splits(detail.get("splits"))] or None,
            "laps": [_split_view(x) for x in raw_splits(detail.get("laps"))] or None,
            "_t": a.get("startTimeInSeconds") or 0,
        }))
    out.sort(key=lambda r: r.get("_t", 0), reverse=True)
    for r in out:
        r.pop("_t", None)
    return out


def _by_date(rows: Iterable[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in rows:
        day = row.get("calendarDate")
        if isinstance(day, str):
            found[day] = row
    return found


def days(garmin_user_id: str, count: int = 7) -> list[dict]:
    """Each recent day, newest first: sleep, HRV, resting heart rate, stress, Body Battery, steps, VO2 max."""
    since = int(time.time()) - (count + 1) * 86400
    daily = _by_date(store.official_summaries(garmin_user_id, "dailies", since))
    sleep = _by_date(store.official_summaries(garmin_user_id, "sleeps", since))
    hrv = _by_date(store.official_summaries(garmin_user_id, "hrv", since))
    stress = _by_date(store.official_summaries(garmin_user_id, "stressDetails", since))
    metrics = _by_date(store.official_summaries(garmin_user_id, "userMetrics", since))
    out = []
    for day in sorted(set(daily) | set(sleep) | set(hrv) | set(stress) | set(metrics), reverse=True)[:count]:
        d, s, h, st, m = daily.get(day, {}), sleep.get(day, {}), hrv.get(day, {}), stress.get(day, {}), metrics.get(day, {})
        score = s.get("overallSleepScore") if isinstance(s.get("overallSleepScore"), dict) else {}
        out.append(drop_empty({
            "date": day,
            "sleep": drop_empty({
                "from": _local(s.get("startTimeInSeconds"), s.get("startTimeOffsetInSeconds")),
                "hours": round(s["durationInSeconds"] / 3600, 1) if isinstance(s.get("durationInSeconds"), (int, float)) else None,
                "score": score.get("value"), "quality": (score.get("qualifierKey") or "").lower() or None,
                "deep_min": _minutes(s.get("deepSleepDurationInSeconds")),
                "light_min": _minutes(s.get("lightSleepDurationInSeconds")),
                "rem_min": _minutes(s.get("remSleepInSeconds")),
                "awake_min": _minutes(s.get("awakeDurationInSeconds")),
            }) or None,
            "hrv": drop_empty({"last_night_avg": h.get("lastNightAvg"), "last_night_5min_high": h.get("lastNight5MinHigh")}) or None,
            "resting_hr": d.get("restingHeartRateInBeatsPerMinute"),
            "steps": d.get("steps"),
            "stress": drop_empty({"average": d.get("averageStressLevel") if (d.get("averageStressLevel") or -1) >= 0 else None,
                                  "label": d.get("stressQualifier") if d.get("stressQualifier") != "unknown" else None})
                      or (st.get("stress") or None),
            "body_battery": st.get("bodyBattery") or drop_empty({
                "charged": d.get("bodyBatteryChargedValue"), "drained": d.get("bodyBatteryDrainedValue")}) or None,
            "intensity_min": _minutes((d.get("moderateIntensityDurationInSeconds") or 0)
                                      + 2 * (d.get("vigorousIntensityDurationInSeconds") or 0)) if d else None,
            "vo2max": m.get("vo2Max"), "vo2max_cycling": m.get("vo2MaxCycling"), "fitness_age": m.get("fitnessAge"),
        }))
    return out


# --------------------------------------------------------------------------
# In Garmin Connect's shapes, for the everyday tools
# --------------------------------------------------------------------------
# An account trying Daash on the official API alone has its reads answered
# from here. Each is turned into the shape Garmin Connect answers in, so the
# tools describe it exactly as they describe Connect's.


def _offset(row: dict) -> float:
    value = row.get("startTimeOffsetInSeconds")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _wall(seconds: Any, offset: float) -> str | None:
    if not isinstance(seconds, (int, float)):
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(seconds + offset))


def connect_activity(a: dict) -> dict:
    """An activity summary as Garmin Connect's activity list has it."""
    return {
        "activityId": a.get("activityId") or a.get("summaryId"),
        "activityName": a.get("activityName"),
        "activityType": {"typeKey": str(a.get("activityType") or "other").lower()},
        "startTimeLocal": _wall(a.get("startTimeInSeconds"), _offset(a)),
        "distance": a.get("distanceInMeters"),
        "duration": a.get("durationInSeconds"),
        "averageSpeed": a.get("averageSpeedInMetersPerSecond"),
        "maxSpeed": a.get("maxSpeedInMetersPerSecond"),
        "averageHR": a.get("averageHeartRateInBeatsPerMinute"),
        "maxHR": a.get("maxHeartRateInBeatsPerMinute"),
        "calories": a.get("activeKilocalories"),
        "elevationGain": a.get("totalElevationGainInMeters"),
        "elevationLoss": a.get("totalElevationLossInMeters"),
        "averageRunningCadenceInStepsPerMinute": a.get("averageRunCadenceInStepsPerMinute"),
        "averageBikingCadenceInRevPerMinute": a.get("averageBikeCadenceInRoundsPerMinute"),
        "steps": a.get("steps"),
    }


def activity_rows(garmin_user_id: str, start: str | None = None, end: str | None = None) -> list[dict]:
    """The account's activities, newest first, optionally between two local dates."""
    since = calendar_seconds(start) - 86400 if start else 0
    seen: dict[str, dict] = {}
    for kind in ACTIVITY_KINDS:
        for a in store.official_summaries(garmin_user_id, kind, since, limit=2000):
            seen[str(a.get("activityId") or a.get("summaryId"))] = a
    rows = sorted(seen.values(), key=lambda a: a.get("startTimeInSeconds") or 0, reverse=True)
    if start or end:
        rows = [a for a in rows if (_wall(a.get("startTimeInSeconds"), _offset(a)) or "")[:10] >= (start or "")
                and (_wall(a.get("startTimeInSeconds"), _offset(a)) or "")[:10] <= (end or "9999")]
    return rows


def fit_figures(garmin_user_id: str) -> dict[str, dict]:
    """What was read from each activity's FIT file, by activity id."""
    return {str(f.get("activityId")): f for f in store.official_summaries(garmin_user_id, "activityFit", limit=2000)
            if f.get("activityId") is not None}


def connect_activities(garmin_user_id: str, rows: list[dict]) -> list[dict]:
    """Activity rows as Connect's, each with the figures its FIT file gave."""
    fits = fit_figures(garmin_user_id)
    out = []
    for a in rows:
        row = connect_activity(a)
        row.update(fits.get(str(row.get("activityId")), {}))
        out.append(row)
    return out


def activity(garmin_user_id: str, activity_id: Any) -> tuple[dict | None, list[dict], dict | None, list[dict] | None]:
    """One activity, Connect-shaped, with its laps as Connect's, what its stream showed, and its HR zones."""
    wanted = str(activity_id)
    found = next((a for a in activity_rows(garmin_user_id) if str(a.get("activityId") or a.get("summaryId")) == wanted), None)
    detail = next((d for d in store.official_summaries(garmin_user_id, "activityDetails", limit=2000)
                   if str(d.get("activityId") or "") == wanted), None)
    if found is None and detail and isinstance(detail.get("summary"), dict):
        found = {**detail["summary"], "activityId": detail.get("activityId")}
    if found is None:
        return None, [], None, None
    flat = connect_activities(garmin_user_id, [found])[0]
    detail = detail or {}
    # The watch's own laps when it lapped, else the kilometre splits.
    laps = connect_laps(detail.get("laps")) if len(raw_splits(detail.get("laps"))) > 1 else connect_laps(detail.get("splits"))
    zones = [{"zoneNumber": n, "secsInZone": flat[f"hrTimeInZone_{n}"], "zoneLowBoundary": flat.get(f"hrZoneLow_{n}")}
             for n in range(1, 6) if isinstance(flat.get(f"hrTimeInZone_{n}"), (int, float))]
    return flat, laps, detail.get("inside") or None, zones or None


def calendar_seconds(day: str) -> int:
    return calendar.timegm(time.strptime(day, "%Y-%m-%d"))


def _on(garmin_user_id: str, kind: str, day: str) -> dict:
    rows = store.official_summaries(garmin_user_id, kind, calendar_seconds(day) - 3 * 86400, limit=2000)
    return next((r for r in reversed(rows) if r.get("calendarDate") == day), {})


def connect_stats(garmin_user_id: str, day: str) -> dict | None:
    """A day's summary as Garmin Connect's daily stats have it, or None when Garmin hasn't sent it."""
    d, st = _on(garmin_user_id, "dailies", day), _on(garmin_user_id, "stressDetails", day)
    if not d:
        return None
    battery = st.get("bodyBattery") or {}
    stress = d.get("averageStressLevel")
    week = [r.get("restingHeartRateInBeatsPerMinute") for r in
            store.official_summaries(garmin_user_id, "dailies", calendar_seconds(day) - 7 * 86400, limit=2000)
            if isinstance(r.get("calendarDate"), str) and r["calendarDate"] <= day]
    week = [v for v in week if isinstance(v, (int, float))][-7:]
    spo2 = (_on(garmin_user_id, "pulseOx", day).get("spo2") or {})
    secs = lambda k: d.get(k) if isinstance(d.get(k), (int, float)) else None  # noqa: E731
    active, bmr = d.get("activeKilocalories"), d.get("bmrKilocalories")
    return {
        "totalSteps": d.get("steps"),
        "dailyStepGoal": d.get("stepsGoal"),
        "floorsAscended": d.get("floorsClimbed"),
        "totalDistanceMeters": d.get("distanceInMeters"),
        "totalKilocalories": (active or 0) + (bmr or 0) if active is not None or bmr is not None else None,
        "activeKilocalories": active,
        "bmrKilocalories": bmr,
        "restingHeartRate": d.get("restingHeartRateInBeatsPerMinute"),
        "lastSevenDaysAvgRestingHeartRate": round(sum(week) / len(week)) if week else None,
        "averageSpo2": spo2.get("average"),
        "minHeartRate": d.get("minHeartRateInBeatsPerMinute"),
        "maxHeartRate": d.get("maxHeartRateInBeatsPerMinute"),
        "bodyBatteryMostRecentValue": battery.get("end"),
        "bodyBatteryHighestValue": battery.get("high"),
        "bodyBatteryLowestValue": battery.get("low"),
        "bodyBatteryChargedValue": d.get("bodyBatteryChargedValue"),
        "bodyBatteryDrainedValue": d.get("bodyBatteryDrainedValue"),
        "averageStressLevel": stress if isinstance(stress, (int, float)) and stress >= 0 else None,
        "maxStressLevel": d.get("maxStressLevel"),
        "restStressDuration": secs("restStressDurationInSeconds"),
        "highStressDuration": secs("highStressDurationInSeconds"),
        "moderateIntensityMinutes": _minutes(d.get("moderateIntensityDurationInSeconds")),
        "vigorousIntensityMinutes": _minutes(d.get("vigorousIntensityDurationInSeconds")),
        "intensityMinutesGoal": _minutes(d.get("intensityDurationGoalInSeconds")),
    }


def day_extras(garmin_user_id: str, day: str) -> tuple[list[dict], list[dict]]:
    """A day's Body Battery events (in Connect's shape) and its stressful spells, as kept on arrival."""
    st = _on(garmin_user_id, "stressDetails", day)
    return list(st.get("bodyBatteryEvents") or []), list(st.get("stressfulSpells") or [])


def connect_sleep(garmin_user_id: str, day: str) -> dict | None:
    """A night as Garmin Connect's sleep answer has it, by the day woken on, or None when Garmin hasn't sent it."""
    s = _on(garmin_user_id, "sleeps", day)
    if not s:
        return None
    h, d = _on(garmin_user_id, "hrv", day), _on(garmin_user_id, "dailies", day)
    deep, light, rem = (s.get(k) for k in ("deepSleepDurationInSeconds", "lightSleepDurationInSeconds", "remSleepInSeconds"))
    asleep = sum(v for v in (deep, light, rem) if isinstance(v, (int, float))) or s.get("durationInSeconds")
    start, offset = s.get("startTimeInSeconds"), _offset(s)
    local_ms = (lambda t: (t + offset) * 1000 if isinstance(t, (int, float)) else None)  # noqa: E731
    scores = {k: v for k, v in (s.get("sleepScores") or {}).items() if isinstance(v, dict)}
    if isinstance(s.get("overallSleepScore"), dict):
        scores["overall"] = s["overallSleepScore"]
    return {
        "dailySleepDTO": {
            "sleepTimeSeconds": asleep,
            "sleepStartTimestampLocal": local_ms(start),
            "sleepEndTimestampLocal": local_ms(start + s["durationInSeconds"])
            if isinstance(start, (int, float)) and isinstance(s.get("durationInSeconds"), (int, float)) else None,
            "deepSleepSeconds": deep,
            "lightSleepSeconds": light,
            "remSleepSeconds": rem,
            "awakeSleepSeconds": s.get("awakeDurationInSeconds"),
            "sleepScores": scores,
            "averageRespirationValue": (s.get("breathing") or {}).get("average"),
            "lowestRespirationValue": (s.get("breathing") or {}).get("lowest"),
            "highestRespirationValue": (s.get("breathing") or {}).get("highest"),
            "averageSpO2Value": (s.get("spo2") or {}).get("average"),
            "lowestSpO2Value": (s.get("spo2") or {}).get("lowest"),
        },
        "restingHeartRate": d.get("restingHeartRateInBeatsPerMinute"),
        "avgOvernightHrv": h.get("lastNightAvg"),
    }
