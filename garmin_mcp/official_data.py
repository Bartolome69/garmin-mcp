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

from . import store, stream, terrain, wellness
from .formatting import drop_empty, duration, pace_per_km

# The kinds Daash keeps, by the key Garmin pushes them under.
KEPT = {"dailies", "sleeps", "hrv", "stressDetails", "userMetrics", "bodyComps", "pulseOx", "allDayRespiration",
        "activities", "manuallyUpdatedActivities", "activityDetails"}
# Kinds Daash makes itself: the figures read from an activity's FIT file.
OWN = {"activityFit"}
# Curves and streams dropped from what is kept, by kind.
_DROP_FIELDS = {
    "dailies": ("timeOffsetHeartRateSamples",),
    "sleeps": ("sleepLevelsMap", "timeOffsetSleepRespiration", "timeOffsetSleepSpo2"),
    "hrv": ("hrvValues",),
    "pulseOx": ("timeOffsetSpo2Values",),
    "allDayRespiration": ("timeOffsetEpochToBreaths",),
    "stressDetails": ("timeOffsetStressLevelValues", "timeOffsetBodyBatteryValues",
                      "bodyBatteryActivityEventList", "bodyBatteryDynamicFeedbackEvent"),
}
ACTIVITY_KINDS = ("activities", "manuallyUpdatedActivities")


def _values(curve: Any) -> list[float]:
    if not isinstance(curve, dict):
        return []
    return [round(v) for _, v in sorted(curve.items(), key=lambda kv: int(kv[0]))
            if isinstance(v, (int, float)) and v >= 0]


def _smoothed(points: list[dict], width: int = 7) -> None:
    """Elevation smoothed over a few samples, so GPS wobble doesn't read as climbing."""
    heights = [s.get("elevationInMeters") for s in points]
    half = width // 2
    for i, s in enumerate(points):
        window = [h for h in heights[max(0, i - half):i + half + 1] if isinstance(h, (int, float))]
        s["_elev"] = sum(window) / len(window) if window else None


def _splits(samples: list[dict], laps: list[dict], summary: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Kilometre splits and laps from an activity's samples: metres, seconds, heart rate, cadence and climb."""
    points = [s for s in samples if isinstance(s, dict) and isinstance(s.get("totalDistanceInMeters"), (int, float))
              and isinstance(s.get("timerDurationInSeconds"), (int, float))]
    if len(points) < 2:
        return [], []
    _smoothed(points)
    summary = summary or {}
    # The samples' cadence is per leg for a run (about half the summary's
    # steps a minute); the summary says which, so the splits read like it.
    cadences = [s["stepsPerMinute"] for s in points if isinstance(s.get("stepsPerMinute"), (int, float)) and s["stepsPerMinute"] > 0]
    whole = summary.get("averageRunCadenceInStepsPerMinute")
    cad_scale = 2 if cadences and isinstance(whole, (int, float)) and 1.7 <= whole / (sum(cadences) / len(cadences)) <= 2.3 else 1

    def stretch(chunk: list[dict], start: dict) -> dict:
        metres = chunk[-1]["totalDistanceInMeters"] - start["totalDistanceInMeters"]
        seconds = chunk[-1]["timerDurationInSeconds"] - start["timerDurationInSeconds"]
        hr = [s["heartRate"] for s in chunk if isinstance(s.get("heartRate"), (int, float)) and s["heartRate"] > 0]
        cadence = [s["stepsPerMinute"] * cad_scale for s in chunk if isinstance(s.get("stepsPerMinute"), (int, float)) and s["stepsPerMinute"] > 0]
        heights = [s["_elev"] for s in [start] + chunk if isinstance(s.get("_elev"), (int, float))]
        climb = sum(b - a for a, b in zip(heights, heights[1:]) if b > a)
        watts = [s["powerInWatts"] for s in chunk if isinstance(s.get("powerInWatts"), (int, float))]
        return drop_empty({"m": round(metres, 1), "s": round(seconds, 1),
                           "hr": round(sum(hr) / len(hr)) if hr else None, "mhr": max(hr) if hr else None,
                           "cad": round(sum(cadence) / len(cadence)) if cadence else None,
                           "up": round(climb) if heights else None,
                           "pw": round(sum(watts) / len(watts)) if watts else None})

    kms, start, chunk, mark = [], points[0], [], 1000.0
    for point in points[1:]:
        chunk.append(point)
        if point["totalDistanceInMeters"] >= mark:
            kms.append(stretch(chunk, start))
            start, chunk, mark = point, [], mark + 1000
    if chunk and chunk[-1]["totalDistanceInMeters"] - start["totalDistanceInMeters"] >= 100:
        kms.append(stretch(chunk, start))
    # Garmin's own total for the climb is the one the summary shows; the
    # splits are scaled to add up to it rather than to their noisier sum.

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
    total_up = summary.get("totalElevationGainInMeters")
    for group in (kms, out_laps):
        raw_up = sum(x.get("up") or 0 for x in group)
        if isinstance(total_up, (int, float)) and raw_up > 0:
            for x in group:
                if x.get("up") is not None:
                    x["up"] = round(x["up"] * total_up / raw_up)
    return kms, out_laps


def _recording(samples: list[dict]) -> dict:
    """The samples as Garmin Connect's activity recording, for the stream analyser."""
    rows = [{"metrics": [s.get("totalDistanceInMeters"), s.get("timerDurationInSeconds"),
                         s.get("heartRate") if isinstance(s.get("heartRate"), (int, float)) and s["heartRate"] > 0 else None,
                         s.get("elevationInMeters"), s.get("powerInWatts")]}
            for s in samples if isinstance(s, dict)]
    return {"metricDescriptors": [{"key": "sumDistance", "metricsIndex": 0, "unit": {"key": "meter"}},
                                  {"key": "sumDuration", "metricsIndex": 1, "unit": {"key": "second"}},
                                  {"key": "directHeartRate", "metricsIndex": 2, "unit": {"key": "bpm"}},
                                  {"key": "directElevation", "metricsIndex": 3, "unit": {"key": "meter"}},
                                  {"key": "directPower", "metricsIndex": 4, "unit": {"key": "watt"}}],
            "activityDetailMetrics": rows}


def connect_laps(splits: Any) -> list[dict]:
    """Kept splits or laps as Garmin Connect's lap rows."""
    return [{"lapIndex": i, "distance": x["m"], "duration": x["s"], "averageHR": x.get("hr"), "maxHR": x.get("mhr"),
             "averageRunCadence": x.get("cad"), "elevationGain": x.get("up"), "avgPower": x.get("pw")}
            for i, x in enumerate(raw_splits(splits), start=1)]


def _iso(seconds: Any) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) if isinstance(seconds, (int, float)) else None


def trim_sleep(item: dict) -> dict:
    """A night with its breathing and SpO2 as averages, and how often awake, before the curves go."""
    breaths = [v for v in _curve(item.get("timeOffsetSleepRespiration")) if v > 0]
    spo2 = [v for v in _curve(item.get("timeOffsetSleepSpo2")) if v > 0]
    kept = trim("sleeps", item) or {}
    levels = item.get("sleepLevelsMap")
    if isinstance(levels, dict) and isinstance(levels.get("awake"), list):
        kept["awakeCount"] = len(levels["awake"])
    if breaths:
        kept["breathing"] = {"average": round(sum(breaths) / len(breaths), 1), "lowest": round(min(breaths), 1),
                             "highest": round(max(breaths), 1)}
    if spo2:
        kept["spo2"] = {"average": round(sum(spo2) / len(spo2)), "lowest": round(min(spo2))}
    return kept


def trim_respiration(item: dict) -> dict:
    """A spell of daytime breathing as its average and how many readings made it."""
    breaths = [v for v in _curve(item.get("timeOffsetEpochToBreaths")) if v > 0]
    kept = trim("allDayRespiration", item) or {}
    if breaths:
        kept["breaths"] = {"average": round(sum(breaths) / len(breaths), 1), "n": len(breaths)}
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
            kms, out_laps = _splits(samples, laps if isinstance(laps, list) else [], item.get("summary"))
            item["splits"], item["laps"] = kms or None, out_laps or None
            points = [s for s in samples if isinstance(s, dict)]
            last = points[-1] if points else {}
            if isinstance(last.get("movingDurationInSeconds"), (int, float)):
                item["movingDurationInSeconds"] = last["movingDurationInSeconds"]
            # Where it started and finished, for "my usual loop": two points, not the track.
            placed = [s for s in points if isinstance(s.get("latitudeInDegree"), (int, float))
                      and isinstance(s.get("longitudeInDegree"), (int, float))]
            if placed:
                item["startLatitude"], item["startLongitude"] = placed[0]["latitudeInDegree"], placed[0]["longitudeInDegree"]
                item["endLatitude"], item["endLongitude"] = placed[-1]["latitudeInDegree"], placed[-1]["longitudeInDegree"]
            # What the stream says beyond the splits: heart rate drift across
            # each long lap, kilometre splits the laps don't already give, and
            # the hills, all worked out now, before the samples go.
            recording = _recording(samples)
            sport = str((item.get("summary") or {}).get("activityType") or "").lower()
            try:
                item["inside"] = stream.analyse(recording, connect_laps(out_laps))
            except Exception:  # noqa: BLE001 - an odd recording must not lose the splits
                item["inside"] = None
            try:
                item["terrain"] = terrain.analyse(stream.parse_stream(recording), ride="cycl" in sport or "bik" in sport)
            except Exception:  # noqa: BLE001
                item["terrain"] = None
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
    if kind == "allDayRespiration":
        return trim_respiration(item)
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
        "startLatitude": a.get("startingLatitudeInDegree"),
        "startLongitude": a.get("startingLongitudeInDegree"),
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


def _details(garmin_user_id: str) -> dict[str, dict]:
    return {str(d.get("activityId")): d for d in store.official_summaries(garmin_user_id, "activityDetails", limit=2000)
            if d.get("activityId") is not None}


def connect_activities(garmin_user_id: str, rows: list[dict], details: dict[str, dict] | None = None) -> list[dict]:
    """Activity rows as Connect's, each with the figures its FIT file and details gave."""
    fits = fit_figures(garmin_user_id)
    details = _details(garmin_user_id) if details is None else details
    out = []
    for a in rows:
        row = connect_activity(a)
        detail = details.get(str(row.get("activityId")), {})
        row.update({k: v for k, v in fits.get(str(row.get("activityId")), {}).items() if k != "activityId"})
        row.update(drop_empty({"movingDuration": detail.get("movingDurationInSeconds"),
                               "endLatitude": detail.get("endLatitude"), "endLongitude": detail.get("endLongitude")}))
        for key in ("startLatitude", "startLongitude"):
            if row.get(key) is None and detail.get(key) is not None:
                row[key] = detail[key]
        out.append(row)
    return out


def activity(garmin_user_id: str, activity_id: Any) -> dict | None:
    """One activity as get_activity_details needs it: Connect-shaped, with laps, zones, stream, terrain and earlier same routes."""
    wanted = str(activity_id)
    rows = activity_rows(garmin_user_id)
    details = _details(garmin_user_id)
    found = next((a for a in rows if str(a.get("activityId") or a.get("summaryId")) == wanted), None)
    detail = details.get(wanted) or {}
    if found is None and isinstance(detail.get("summary"), dict):
        found = {**detail["summary"], "activityId": detail.get("activityId")}
    if found is None:
        return None
    flat = connect_activities(garmin_user_id, [found], details)[0]
    # The watch's own laps when it lapped, else the kilometre splits.
    laps = connect_laps(detail.get("laps")) if len(raw_splits(detail.get("laps"))) > 1 else connect_laps(detail.get("splits"))
    zones = [{"zoneNumber": n, "secsInZone": flat[f"hrTimeInZone_{n}"], "zoneLowBoundary": flat.get(f"hrZoneLow_{n}")}
             for n in range(1, 6) if isinstance(flat.get(f"hrTimeInZone_{n}"), (int, float))]
    day = str(flat.get("startTimeLocal") or "")[:10]
    earlier = connect_activities(garmin_user_id, [a for a in rows if str(a.get("activityId") or a.get("summaryId")) != wanted], details)
    try:
        same = terrain.same_route(flat, [r for r in earlier if str(r.get("startTimeLocal") or "")[:10] < day])
    except Exception:  # noqa: BLE001
        same = None
    return {"flat": flat, "laps": laps, "zones": zones or None, "inside": detail.get("inside") or None,
            "terrain": detail.get("terrain") or None, "same_route": same}


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
    begins = d.get("startTimeInSeconds")
    breaths = [r["breaths"] for r in store.official_summaries(garmin_user_id, "allDayRespiration", (begins or 0) - 1, limit=2000)
               if isinstance(begins, (int, float)) and isinstance(r.get("startTimeInSeconds"), (int, float))
               and begins <= r["startTimeInSeconds"] < begins + 86400 and isinstance(r.get("breaths"), dict)]
    counted = sum(b.get("n", 0) for b in breaths)
    waking = round(sum(b["average"] * b.get("n", 0) for b in breaths) / counted, 1) if counted else None
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
        "avgWakingRespirationValue": waking,
        "bodyBatteryDuringSleep": sleep_charge(garmin_user_id, day),
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


def sleep_charge(garmin_user_id: str, day: str) -> int | None:
    """What the night's sleep did to Body Battery, from the day's sleep event."""
    for event in _on(garmin_user_id, "stressDetails", day).get("bodyBatteryEvents") or []:
        inner = event.get("event") if isinstance(event, dict) else None
        if isinstance(inner, dict) and str(inner.get("eventType") or "").upper() == "SLEEP":
            return inner.get("bodyBatteryImpact")
    return None


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
        "awakeCount": s.get("awakeCount"),
        "bodyBatteryChange": sleep_charge(garmin_user_id, day),
        "spo2Week": drop_empty({"lastSevenDaysAvgSpO2": _spo2_week(garmin_user_id, day)}),
    }


def _spo2_week(garmin_user_id: str, day: str) -> int | None:
    rows = [r["spo2"]["average"] for r in
            store.official_summaries(garmin_user_id, "pulseOx", calendar_seconds(day) - 7 * 86400, limit=2000)
            if isinstance(r.get("calendarDate"), str) and r["calendarDate"] <= day
            and isinstance(r.get("spo2"), dict) and isinstance(r["spo2"].get("average"), (int, float))]
    return round(sum(rows[-7:]) / len(rows[-7:])) if rows else None


# --------------------------------------------------------------------------
# A recovery check, for an account on the official API alone
# --------------------------------------------------------------------------
# Garmin's own readiness score isn't shared through the official API, and
# "should I go hard today?" was the third most asked question of Daash. The
# pieces it is made from are: last night's sleep, overnight HRV, resting heart
# rate, Body Battery and the training behind them. Each is set against the
# person's own recent normal, never a population's, and the answer says
# plainly that this is Daash's read, not Garmin's score.

# Nights of HRV before last night is compared with a usual, and before that
# usual is settled; and how far back it looks. Garmin's own HRV status takes
# about three weeks to set a baseline. On Garmin's route the data starts the
# day someone connects, so for the first week there's no usual to compare
# with, and no call is made on the rest alone (asked for on Reddit, Oct 27).
HRV_BASELINE_NIGHTS = 7
HRV_SETTLED_NIGHTS = 21
HRV_BASELINE_DAYS = 28
# How far off their normal counts as a signal.
HRV_LOW = 0.92          # last night under 92% of the baseline
RESTING_HR_UP = 5       # beats above the week's average
SLEEP_SCORE_POOR = 60
SLEEP_HOURS_SHORT = 6.0
SLEEP_CHARGE_LOW = 25   # Body Battery gained overnight
LOAD_HIGH = 1.3         # this week's training time against the three weeks before


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _training(garmin_user_id: str, day: str) -> dict[str, Any]:
    """Training time and distance over the last 7 days against the 21 before, and the latest hard day."""
    end = calendar_seconds(day) + 86400
    seen: dict[str, dict] = {}
    for kind in ACTIVITY_KINDS:
        for a in store.official_summaries(garmin_user_id, kind, end - 28 * 86400, limit=2000):
            seen[str(a.get("activityId") or a.get("summaryId"))] = a
    week, before, km_week, hard = 0.0, 0.0, 0.0, None
    # The month's highest heart rate stands in for their maximum.
    top = max((a.get("maxHeartRateInBeatsPerMinute") or 0 for a in seen.values()), default=0)
    for a in seen.values():
        start, secs = a.get("startTimeInSeconds"), a.get("durationInSeconds")
        if not isinstance(start, (int, float)) or not isinstance(secs, (int, float)) or start >= end:
            continue
        if start >= end - 7 * 86400:
            week += secs
            km_week += (a.get("distanceInMeters") or 0) / 1000
        else:
            before += secs
        local = time.strftime("%Y-%m-%d", time.gmtime(start + (a.get("startTimeOffsetInSeconds") or 0)))
        long_one = secs >= 90 * 60
        hard_hr = bool(top) and (a.get("averageHeartRateInBeatsPerMinute") or 0) >= 0.82 * top
        if (long_one or hard_hr) and start >= end - 3 * 86400 and (hard is None or local > hard):
            hard = local
    usual = before / 3 if before else None
    return drop_empty({
        "last_7_days": drop_empty({"hours": round(week / 3600, 1), "km": round(km_week, 1) if km_week else None}),
        "usual_week_hours": round(usual / 3600, 1) if usual else None,
        "ratio": round(week / usual, 2) if usual else None,
        "hard_or_long_session_on": hard,
    })


def recovery(garmin_user_id: str, day: str) -> dict[str, Any] | None:
    """How recovered the person looks on a day, from the official API's own figures. None with nothing to go on."""
    since = calendar_seconds(day) - (HRV_BASELINE_DAYS + 1) * 86400
    hrv = {r["calendarDate"]: r for r in store.official_summaries(garmin_user_id, "hrv", since, limit=2000)
           if isinstance(r.get("calendarDate"), str) and r["calendarDate"] <= day}
    night = (hrv.get(day) or {}).get("lastNightAvg")
    baseline_nights = [r.get("lastNightAvg") for d, r in hrv.items() if d < day and isinstance(r.get("lastNightAvg"), (int, float))]
    baseline = _mean(baseline_nights) if len(baseline_nights) >= HRV_BASELINE_NIGHTS else None
    sleep = _on(garmin_user_id, "sleeps", day)
    score = sleep.get("overallSleepScore") if isinstance(sleep.get("overallSleepScore"), dict) else {}
    hours = round(sleep["durationInSeconds"] / 3600, 1) if isinstance(sleep.get("durationInSeconds"), (int, float)) else None
    stats = connect_stats(garmin_user_id, day) or {}
    resting, resting_week = stats.get("restingHeartRate"), stats.get("lastSevenDaysAvgRestingHeartRate")
    charge = sleep_charge(garmin_user_id, day)
    load = _training(garmin_user_id, day)
    if not any((night, sleep, stats, (load.get("last_7_days") or {}).get("hours"), load.get("usual_week_hours"))):
        return None

    signals: list[dict[str, Any]] = []

    def signal(what: str, good: bool, said: str) -> None:
        signals.append({"what": what, "ok": good, "said": said})

    if isinstance(night, (int, float)) and baseline:
        low = night < baseline * HRV_LOW
        signal("hrv", not low, f"HRV {round(night)} ms last night against {round(baseline)} ms usual"
               + (", lower than normal" if low else ", in line with normal"))
    if isinstance(resting, (int, float)) and isinstance(resting_week, (int, float)):
        up = resting >= resting_week + RESTING_HR_UP
        signal("resting_hr", not up, f"Resting heart rate {resting} against {resting_week} this week"
               + (", raised" if up else ""))
    sleep_value = score.get("value")
    if isinstance(sleep_value, (int, float)) or hours is not None:
        poor = (isinstance(sleep_value, (int, float)) and sleep_value < SLEEP_SCORE_POOR) or (hours is not None and hours < SLEEP_HOURS_SHORT)
        signal("sleep", not poor, "Sleep " + ", ".join(x for x in (
            f"score {sleep_value}" if isinstance(sleep_value, (int, float)) else "", f"{hours} hours" if hours is not None else "") if x)
               + (", short or poor" if poor else ""))
    if isinstance(charge, (int, float)):
        low_charge = charge < SLEEP_CHARGE_LOW
        signal("body_battery", not low_charge, f"Body Battery up {charge} overnight" + (", a weak recharge" if low_charge else ""))
    if isinstance(load.get("ratio"), (int, float)):
        heavy = load["ratio"] > LOAD_HIGH
        signal("load", not heavy, f"{load['last_7_days'].get('hours')} hours of training this week against "
               f"{load.get('usual_week_hours')} usual" + (", a big jump" if heavy else ""))
    if load.get("hard_or_long_session_on"):
        signal("recent_hard", False, f"A hard or long session on {load['hard_or_long_session_on']}")

    flags = sum(1 for s in signals if not s["ok"])
    measured = sum(1 for s in signals if s["what"] != "recent_hard")
    # A watch that never records HRV shouldn't wait for a baseline it won't
    # get: a week of nights slept without any HRV says that's the watch.
    nights_slept = len({r.get("calendarDate") for r in store.official_summaries(garmin_user_id, "sleeps", since, limit=2000)
                        if isinstance(r.get("calendarDate"), str) and r["calendarDate"] < day})
    no_hrv_watch = not hrv and nights_slept >= HRV_BASELINE_NIGHTS
    building = not baseline and not no_hrv_watch
    notes: list[str] = []
    if building:
        notes.append(f"Not enough nights yet for an HRV baseline: {len(baseline_nights)} of {HRV_BASELINE_NIGHTS}. "
                     "Garmin shares data from the day the account connected, so there's no usual to compare "
                     "last night with until then. No call is made without it: say what the figures show, and "
                     "that a recovery call comes once a week of nights has synced.")
    elif baseline and len(baseline_nights) < HRV_SETTLED_NIGHTS:
        notes.append(f"Early HRV baseline: {len(baseline_nights)} of {HRV_SETTLED_NIGHTS} nights. Garmin's own "
                     "HRV status takes about three weeks to settle, so give the call as provisional.")
    elif no_hrv_watch:
        notes.append("No HRV has arrived from this watch, which may not record it overnight: the call rests on "
                     "sleep, resting heart rate, Body Battery and training.")
    if not load.get("usual_week_hours") and (load.get("last_7_days") or {}).get("hours"):
        notes.append("Not enough weeks yet to say whether this week's training is more than usual.")
    if building or measured < 2:
        call = "not_enough_data"
    elif flags == 0:
        call = "ready_for_hard"
    elif flags == 1:
        call = "fine_with_care"
    else:
        call = "go_easy"
    return drop_empty({
        "date": day,
        "call": call,
        "what_this_is": (
            "Daash's read of how recovered you look, from your own sleep, overnight HRV, resting heart rate, "
            "Body Battery and recent training, each against your recent normal. Garmin's own readiness score "
            "isn't shared through its official API."
        ),
        "signals": signals,
        "sleep": drop_empty({"score": sleep_value, "hours": hours,
                             "quality": (score.get("qualifierKey") or "").lower() or None}) or None,
        "hrv": drop_empty({"last_night_ms": night, "usual_ms": round(baseline) if baseline else None,
                           "nights_in_usual": len(baseline_nights) or None,
                           "baseline": ("building" if building else "early" if baseline and len(baseline_nights) < HRV_SETTLED_NIGHTS
                                        else "settled" if baseline else None)}) or None,
        "notes": notes or None,
        "resting_hr": drop_empty({"today": resting, "seven_day_avg": resting_week}) or None,
        "body_battery": drop_empty({"gained_overnight": charge, "now": stats.get("bodyBatteryMostRecentValue"),
                                    "highest": stats.get("bodyBatteryHighestValue")}) or None,
        "training": load or None,
    })
