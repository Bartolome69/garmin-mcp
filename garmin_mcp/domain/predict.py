"""Race-time prediction and its 12-week trend (build prompt, sections 5.5 and 6).

From the races of the last 12 weeks, each carried to the goal distance by
Riegel's formula, the best one stands. With no race in that window, the
threshold pace stands in: an hour at threshold is treated as a race of that
distance. The trend is the same prediction as it would have read at the end of
each of the last 12 weeks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping

from .config import DEFAULT, DomainConfig
from .model import monday
from .paces import riegel


@dataclass(frozen=True)
class Race:
    date: date
    km: float
    seconds: float


@dataclass(frozen=True)
class Prediction:
    seconds: float | None
    basis: str                  # race | threshold | none
    from_race: Race | None


def predict(races: Iterable[Race], target_km: float, today: date, *, threshold_s_per_km: float | None = None,
            cfg: DomainConfig = DEFAULT) -> Prediction:
    since = today - timedelta(weeks=cfg.predict.window_weeks)
    recent = [r for r in races if since <= r.date <= today and r.km > 0 and r.seconds > 0]
    if recent:
        best = min(recent, key=lambda r: riegel(r.seconds, r.km, target_km, cfg.paces.riegel))
        return Prediction(round(riegel(best.seconds, best.km, target_km, cfg.paces.riegel)), "race", best)
    if threshold_s_per_km and threshold_s_per_km > 0:
        km = 3600 / threshold_s_per_km
        return Prediction(round(riegel(3600, km, target_km, cfg.paces.riegel)), "threshold", None)
    return Prediction(None, "none", None)


def trend(races: Iterable[Race], target_km: float, today: date, *,
          thresholds: Mapping[date, float] | None = None, cfg: DomainConfig = DEFAULT) -> list[tuple[date, float | None]]:
    """(week ending Sunday, prediction then), oldest first, the current week last."""
    races = list(races)
    out = []
    this_week = monday(today)
    for i in range(cfg.predict.trend_weeks - 1, -1, -1):
        end = min(this_week - timedelta(weeks=i) + timedelta(days=6), today)
        known = [d for d in (thresholds or {}) if d <= end]
        th = thresholds[max(known)] if known else None
        out.append((end, predict([r for r in races if r.date <= end], target_km, end,
                                 threshold_s_per_km=th, cfg=cfg).seconds))
    return out
