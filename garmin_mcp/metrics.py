"""Shape Garmin's training-load, recovery and fitness endpoints into two answers.

Garmin spreads "how ready am I today" across five endpoints and "how fit am I"
across another five, each with its own nesting, device maps and enum spelling.
The two functions here fold each set into one compact dict so a coach can read
the whole picture in a glance, and Claude can answer "threshold today or
tomorrow?" from evidence rather than vibes.

Everything is optional. A field the account does not have, or an endpoint that
failed, is simply absent; nothing here raises on shape.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .formatting import drop_empty, duration, first_present, pace_per_km, rounded


def _phrase(value: Any) -> str | None:
    """PRODUCTIVE_1 -> Productive; AEROBIC_LOW_SHORTAGE -> Aerobic low shortage."""
    if not isinstance(value, str) or not value:
        return None
    words = [w for w in value.split("_") if w and not w.isdigit()]
    if not words:
        return None
    return " ".join(words).capitalize()


def _primary(dto_map: Any) -> dict[str, Any]:
    """Garmin keys these by device id; the primary training device is the one."""
    if not isinstance(dto_map, Mapping) or not dto_map:
        return {}
    entries = [v for v in dto_map.values() if isinstance(v, Mapping)]
    for entry in entries:
        if entry.get("primaryTrainingDevice"):
            return dict(entry)
    return dict(entries[0]) if entries else {}


def _latest(rows: Any) -> dict[str, Any]:
    """Some endpoints return a list per device or per day; take the newest."""
    if isinstance(rows, Mapping):
        return dict(rows)
    if isinstance(rows, list) and rows:
        rows = [r for r in rows if isinstance(r, Mapping)]
        if not rows:
            return {}
        return dict(max(rows, key=lambda r: str(r.get("calendarDate") or r.get("timestamp") or "")))
    return {}


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------


def _readiness_block(raw: Any) -> dict[str, Any] | None:
    row = _latest(raw)
    if not row:
        return None
    factors = drop_empty(
        {
            "sleep": _phrase(row.get("sleepScoreFactorFeedback")),
            "recovery_time": _phrase(row.get("recoveryTimeFactorFeedback")),
            "hrv": _phrase(row.get("hrvFactorFeedback")),
            "load_ratio": _phrase(row.get("acwrFactorFeedback")),
            "stress_history": _phrase(row.get("stressHistoryFactorFeedback")),
            "sleep_history": _phrase(row.get("sleepHistoryFactorFeedback")),
        }
    )
    return drop_empty(
        {
            "score": rounded(row.get("score"), 0),
            "level": _phrase(row.get("level")),
            "message": row.get("feedbackShort") if isinstance(row.get("feedbackShort"), str) else None,
            "recovery_time_hours": rounded(row.get("recoveryTime"), 0),
            "factors": factors or None,
        }
    )


def _status_block(raw: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Any]:
    """Training status, load focus and VO2 max, all from one endpoint."""
    if not isinstance(raw, Mapping):
        return None, None, None
    status = _primary((raw.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData"))
    acute = status.get("acuteTrainingLoadDTO") or {}
    tunnel = [rounded(status.get("loadTunnelMin"), 0), rounded(status.get("loadTunnelMax"), 0)]
    chronic_range = [
        rounded(acute.get("minTrainingLoadChronic"), 0),
        rounded(acute.get("maxTrainingLoadChronic"), 0),
    ]
    status_block = drop_empty(
        {
            "status": _phrase(status.get("trainingStatusFeedbackPhrase")),
            "since": status.get("sinceDate"),
            "weekly_load": rounded(status.get("weeklyTrainingLoad"), 0),
            "weekly_load_target": tunnel if all(v is not None for v in tunnel) else None,
            "acute_load": rounded(acute.get("dailyTrainingLoadAcute"), 0),
            "chronic_load": rounded(acute.get("dailyTrainingLoadChronic"), 0),
            "chronic_load_optimal": chronic_range if all(v is not None for v in chronic_range) else None,
            "acute_chronic_ratio": rounded(acute.get("dailyAcuteChronicWorkloadRatio"), 2),
            "ratio_status": _phrase(acute.get("acwrStatus")),
            "paused": status.get("trainingPaused") or None,
        }
    )

    balance = _primary((raw.get("mostRecentTrainingLoadBalance") or {}).get("metricsTrainingLoadBalanceDTOMap"))

    def _band(key: str) -> dict[str, Any] | None:
        value = balance.get(f"monthlyLoad{key}")
        if value is None:
            return None
        return drop_empty(
            {
                "load": rounded(value, 0),
                "target": [
                    rounded(balance.get(f"monthlyLoad{key}TargetMin"), 0),
                    rounded(balance.get(f"monthlyLoad{key}TargetMax"), 0),
                ]
                if balance.get(f"monthlyLoad{key}TargetMin") is not None
                else None,
            }
        )

    focus_block = drop_empty(
        {
            "verdict": _phrase(balance.get("trainingBalanceFeedbackPhrase")),
            "anaerobic": _band("Anaerobic"),
            "high_aerobic": _band("AerobicHigh"),
            "low_aerobic": _band("AerobicLow"),
        }
    )

    vo2 = ((raw.get("mostRecentVO2Max") or {}).get("generic") or {})
    vo2_value = first_present(vo2, "vo2MaxPreciseValue", "vo2MaxValue") if isinstance(vo2, Mapping) else None
    return status_block or None, focus_block or None, vo2_value


def _hrv_block(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    summary = raw.get("hrvSummary") or {}
    baseline = summary.get("baseline") or {}
    band = [rounded(baseline.get("balancedLow"), 0), rounded(baseline.get("balancedUpper"), 0)]
    return drop_empty(
        {
            "last_night_ms": rounded(summary.get("lastNightAvg"), 0),
            "weekly_avg_ms": rounded(summary.get("weeklyAvg"), 0),
            "balanced_range_ms": band if all(v is not None for v in band) else None,
            "status": _phrase(summary.get("status")),
        }
    ) or None


def shape_readiness(
    date: str,
    readiness: Any,
    status: Any,
    hrv: Any,
    stats: Any,
    sleep: Any,
    warnings: Iterable[str] | None = None,
) -> dict[str, Any]:
    stats = stats if isinstance(stats, Mapping) else {}
    sleep_dto = (sleep or {}).get("dailySleepDTO") if isinstance(sleep, Mapping) else None
    sleep_dto = sleep_dto if isinstance(sleep_dto, Mapping) else {}
    sleep_score = ((sleep_dto.get("sleepScores") or {}).get("overall") or {}).get("value")

    training_status, load_focus, vo2 = _status_block(status)
    readiness_block = _readiness_block(readiness)

    return drop_empty(
        {
            "date": date,
            "readiness": readiness_block,
            "training_status": training_status,
            "load_focus": load_focus,
            "hrv": _hrv_block(hrv),
            "body_battery": drop_empty(
                {
                    "now": stats.get("bodyBatteryMostRecentValue"),
                    "charged": stats.get("bodyBatteryChargedValue"),
                    "drained": stats.get("bodyBatteryDrainedValue"),
                }
            )
            or None,
            "stress": drop_empty(
                {
                    "average": stats.get("averageStressLevel"),
                    "rest": duration(stats.get("restStressDuration")),
                    "high": duration(stats.get("highStressDuration")),
                }
            )
            or None,
            "resting_hr": drop_empty(
                {
                    "today": stats.get("restingHeartRate"),
                    "seven_day_avg": stats.get("lastSevenDaysAvgRestingHeartRate"),
                }
            )
            or None,
            "sleep": drop_empty(
                {
                    "score": sleep_score,
                    "total": duration(sleep_dto.get("sleepTimeSeconds")),
                    "overnight_hrv_ms": rounded((sleep or {}).get("avgOvernightHrv"), 0)
                    if isinstance(sleep, Mapping)
                    else None,
                }
            )
            or None,
            "vo2max": rounded(vo2, 1),
            "how_to_read": (
                "Readiness is Garmin's own 0-100 call for today. Acute/chronic ratio "
                "near 1.0 is steady; above ~1.3 is a spike, below ~0.8 is undertraining. "
                "HRV below the balanced range with high stress and short sleep is the "
                "classic 'make today easy' pattern. Missing fields mean the watch was "
                "not worn overnight or the account's device does not report them."
            ),
            "warnings": list(warnings) or None if warnings else None,
        }
    )


# --------------------------------------------------------------------------
# Fitness
# --------------------------------------------------------------------------

RACE_KEYS = (
    ("time5K", "5k"),
    ("time10K", "10k"),
    ("timeHalfMarathon", "half_marathon"),
    ("timeMarathon", "marathon"),
)

RACE_METRES = {"5k": 5000, "10k": 10000, "half_marathon": 21097.5, "marathon": 42195}


# No runner's lactate threshold is slower than this (11:07/km). Garmin's
# threshold endpoints have been seen sending the speed at a tenth of its real
# value (0.43 for a 3:52/km threshold, which read as 38:43/km), so anything
# under it is taken as that and scaled up.
MIN_THRESHOLD_SPEED_MPS = 1.5

def threshold_speed(speed: Any) -> float | None:
    """Garmin's lactate threshold speed in metres per second, put right if it came a tenth too small."""
    if not isinstance(speed, (int, float)) or isinstance(speed, bool) or speed <= 0:
        return None
    return float(speed) * 10 if speed < MIN_THRESHOLD_SPEED_MPS else float(speed)


def _classify(row: Mapping[str, Any], score: Any) -> str | None:
    """Garmin ships the class boundaries alongside the score; read the label off them.

    Keys look like classificationLowerLimitIntermediate, ...Trained,
    ...WellTrained, ...Expert, ...Superior, ...Elite. Below the lowest is
    Recreational. Falls back to nothing rather than guessing a numeric enum.
    """
    if not isinstance(score, (int, float)):
        return None
    prefix = "classificationLowerLimit"
    bounds = []
    for key, value in row.items():
        if key.startswith(prefix) and isinstance(value, (int, float)):
            name = key[len(prefix):]
            label = "".join(f" {c}" if c.isupper() else c for c in name).strip().capitalize()
            bounds.append((float(value), label))
    if not bounds:
        return None
    bounds.sort()
    label = "Recreational"
    for lower, name in bounds:
        if score >= lower:
            label = name
    return label


def shape_fitness(
    vo2_metrics: Any,
    race: Any,
    lactate: Any,
    endurance: Any,
    hill: Any,
    tolerance: Any,
    warnings: Iterable[str] | None = None,
) -> dict[str, Any]:
    vo2, fitness_age = None, None
    if isinstance(vo2_metrics, list) and vo2_metrics:
        generic = (vo2_metrics[0] or {}).get("generic") or {}
        vo2 = first_present(generic, "vo2MaxPreciseValue", "vo2MaxValue")
        fitness_age = generic.get("fitnessAge")

    race_row = _latest(race)
    predictions = {}
    for key, name in RACE_KEYS:
        secs = race_row.get(key)
        if isinstance(secs, (int, float)) and secs > 0:
            predictions[name] = drop_empty(
                {"time": duration(secs), "pace_per_km": pace_per_km(RACE_METRES[name], secs)}
            )

    lt: dict[str, Any] = {}
    if isinstance(lactate, Mapping):
        shr = lactate.get("speed_and_heart_rate") or {}
        speed = threshold_speed(shr.get("speed"))
        hr = shr.get("heartRate")
        lt = drop_empty(
            {
                "heart_rate_bpm": rounded(hr, 0),
                "pace_per_km": pace_per_km(1000.0, 1000.0 / speed) if speed else None,
                "as_of": shr.get("calendarDate"),
            }
        )

    end_row = _latest(endurance)
    end_block = drop_empty(
        {
            "score": rounded(end_row.get("overallScore"), 0),
            "class": _classify(end_row, end_row.get("overallScore")),
            "as_of": end_row.get("calendarDate"),
        }
    )

    hill_row = _latest(hill)
    hill_block = drop_empty(
        {
            "score": rounded(hill_row.get("overallScore"), 0),
            "strength": rounded(hill_row.get("strengthScore"), 0),
            "endurance": rounded(hill_row.get("enduranceScore"), 0),
            "class": _classify(hill_row, hill_row.get("overallScore")),
            "as_of": hill_row.get("calendarDate"),
        }
    )

    # Running tolerance is new enough that its shape is still moving; pass the
    # newest week's numbers through with the housekeeping fields removed.
    tol_row = _latest(tolerance)
    tol_block = drop_empty(
        {
            k: rounded(v, 1)
            for k, v in tol_row.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
            and not k.lower().endswith(("pk", "id")) and "timestamp" not in k.lower()
        }
    )
    if tol_row.get("calendarDate"):
        tol_block["as_of"] = tol_row["calendarDate"]

    return drop_empty(
        {
            "vo2max": rounded(vo2, 1),
            "fitness_age": fitness_age,
            "race_predictions": predictions or None,
            "race_predictions_as_of": race_row.get("calendarDate") if predictions else None,
            "lactate_threshold": lt or None,
            "endurance_score": end_block or None,
            "hill_score": hill_block or None,
            "running_tolerance": tol_block or None,
            "how_to_read": (
                "Race predictions are Garmin's estimate from VO2 max and recent "
                "training, for a flat course in good conditions; treat them as a "
                "fitness marker, not a promise. Lactate threshold pace is roughly "
                "the effort sustainable for an hour, the anchor for tempo and "
                "threshold sessions."
            ),
            "warnings": list(warnings) or None if warnings else None,
        }
    )
