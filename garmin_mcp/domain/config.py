"""Every threshold the engines use, in one place (build prompt, sections 6 and 10).

TODO(sports-science): the readiness, adjustment and takeaway thresholds are the
build prompt's placeholders, pending sign-off. Overridable without a code change
through DAASH_DOMAIN_CONFIG (JSON), the same values the app gets as flags.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, replace
from typing import Any


@dataclass(frozen=True)
class ReadinessConfig:
    sleep_short_hours: float = 1.0      # low when last night is this far under the median
    sleep_median_nights: int = 28
    sleep_min_nights: int = 7           # fewer nights of history: sleep is "missing"
    hrv_range_nights: int = 60
    hrv_low_percentile: float = 10.0    # low below this percentile of the personal range
    hrv_high_percentile: float = 90.0
    hrv_min_nights: int = 14
    load_ratio_low: float = 0.8         # 7-day / 28-day; in range between these
    load_ratio_high: float = 1.3        # low (a warning signal) above this
    load_min_days: int = 21             # days with a known load in the 28-day window
    chart_nights: int = 14


@dataclass(frozen=True)
class LoadConfig:
    band_pct: float = 10.0              # target band: this % either side of the intended load
    weeks_ahead: int = 5
    est_min_per_km: float = 6.0         # minutes per km when a session has a distance only
    intensity: dict[str, float] = field(default_factory=lambda: {
        "easy": 1.0, "recovery": 0.8, "long": 1.1, "tempo": 1.5, "intervals": 1.7,
        "race": 1.8, "strength": 0.6, "rest": 0.0, "other": 1.0,
    })


@dataclass(frozen=True)
class BackToBackConfig:
    hard_types: tuple[str, ...] = ("tempo", "intervals", "race")
    long_hard_km: float = 25.0          # a long run over this counts as hard


@dataclass(frozen=True)
class AdjustConfig:
    unwell_days: int = 3
    unwell_volume: float = 0.70
    niggle_days: int = 5
    niggle_volume: float = 0.60
    niggle_long_share: float = 0.6
    travel_days: int = 7
    busy_volume: float = 0.75
    busy_easy_minutes: int = 30
    easy_from_hard_share: float = 0.6   # a hard session made easy keeps this share of its time
    taper_weeks: int = 2
    taper_volume: tuple[float, ...] = (0.8, 0.6)   # week before last, last week


@dataclass(frozen=True)
class PaceConfig:
    riegel: float = 1.06
    step: float = 0.02                  # Easier / Harder: 2% per step
    default_race_km: float = 10.0       # try-before-signup default: a 10K of 45:00
    default_race_seconds: int = 45 * 60


@dataclass(frozen=True)
class PredictConfig:
    window_weeks: int = 12
    trend_weeks: int = 12


@dataclass(frozen=True)
class MergeConfig:
    start_within_seconds: int = 60
    distance_within_pct: float = 2.0


@dataclass(frozen=True)
class TakeawayConfig:
    even_reps_seconds: float = 8.0      # fastest and slowest rep this close: even
    fade_seconds: float = 6.0           # last rep this much slower than the first: faded
    negative_split_pct: float = 1.0
    hr_drift_bpm: float = 3.0
    hr_drift_pace_pct: float = 3.0      # halves compared only when their paces are this close
    cadence_change_spm: float = 4.0
    flat_metres: float = 15.0           # less climbing than this either way: flat


@dataclass(frozen=True)
class PostRunConfig:
    distance_ok_pct: float = 10.0       # within this of the planned distance: on plan
    pace_ok_seconds: float = 3.0        # outside the planned range by no more than this: on plan


@dataclass(frozen=True)
class DomainConfig:
    readiness: ReadinessConfig = field(default_factory=ReadinessConfig)
    load: LoadConfig = field(default_factory=LoadConfig)
    backtoback: BackToBackConfig = field(default_factory=BackToBackConfig)
    adjust: AdjustConfig = field(default_factory=AdjustConfig)
    paces: PaceConfig = field(default_factory=PaceConfig)
    predict: PredictConfig = field(default_factory=PredictConfig)
    merge: MergeConfig = field(default_factory=MergeConfig)
    takeaways: TakeawayConfig = field(default_factory=TakeawayConfig)
    postrun: PostRunConfig = field(default_factory=PostRunConfig)


def _apply(section: Any, values: dict[str, Any]) -> Any:
    known = {f.name: f for f in fields(section)}
    changes = {}
    for key, value in (values or {}).items():
        if key not in known:
            continue
        current = getattr(section, key)
        if isinstance(current, tuple) and isinstance(value, list):
            value = tuple(value)
        elif isinstance(current, dict) and isinstance(value, dict):
            value = {**current, **value}
        elif isinstance(current, (int, float)) and not isinstance(current, bool):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            value = type(current)(value)
        changes[key] = value
    return replace(section, **changes)


def load_config(raw: str | None = None) -> DomainConfig:
    """The defaults, with any overrides from DAASH_DOMAIN_CONFIG (or `raw`) applied. Bad JSON keeps the defaults."""
    base = DomainConfig()
    text = raw if raw is not None else os.environ.get("DAASH_DOMAIN_CONFIG", "")
    if not text.strip():
        return base
    try:
        data = json.loads(text)
    except ValueError:
        return base
    if not isinstance(data, dict):
        return base
    return replace(base, **{f.name: _apply(getattr(base, f.name), data.get(f.name) or {})
                            for f in fields(base) if isinstance(data.get(f.name), dict)})


DEFAULT = DomainConfig()
