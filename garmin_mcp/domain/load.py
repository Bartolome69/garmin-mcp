"""Weekly load, the target band and the forward projection (build prompt, sections 5.5 and 6).

A session's load is Garmin's training load where Garmin gave one, otherwise its
duration times an intensity factor for its type. The target band is 10% either
side of the plan's intended load for that week. Weeks already run show what was
done; the next five show what's planned. A week over its band is flagged with
"Trim week N".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping

from .config import DEFAULT, DomainConfig
from .model import Session, monday


def session_load(s: Session, cfg: DomainConfig = DEFAULT) -> float:
    if s.load is not None:
        return float(s.load)
    minutes = s.minutes
    if minutes is None and s.distance_km is not None:
        minutes = s.distance_km * cfg.load.est_min_per_km
    factor = cfg.load.intensity.get(s.type, cfg.load.intensity["other"])
    return round((minutes or 0) * factor, 1)


def weekly_loads(sessions: Iterable[Session], cfg: DomainConfig = DEFAULT) -> dict[date, float]:
    out: dict[date, float] = {}
    for s in sessions:
        out[monday(s.date)] = round(out.get(monday(s.date), 0.0) + session_load(s, cfg), 1)
    return out


def band(intended: float, cfg: DomainConfig = DEFAULT) -> tuple[float, float]:
    pct = cfg.load.band_pct / 100
    return round(intended * (1 - pct), 1), round(intended * (1 + pct), 1)


@dataclass(frozen=True)
class LoadWeek:
    starts: date
    number: int | None          # the plan's week number, for "Trim week N"
    load: float
    intended: float | None
    low: float | None
    high: float | None
    state: str                  # done | current | planned
    over: bool
    trim: str | None            # "Trim week 6"


def chart(*, today: date, done: Iterable[Session], planned: Iterable[Session],
          intended: Mapping[date, float], numbers: Mapping[date, int] | None = None,
          weeks_back: int = 4, cfg: DomainConfig = DEFAULT) -> list[LoadWeek]:
    """Weeks from `weeks_back` ago through the next five.

    The current week counts what's done plus what's still planned from today on.
    """
    this = monday(today)
    done_load = weekly_loads(done, cfg)
    planned_list = list(planned)
    ahead = weekly_loads([s for s in planned_list if s.date >= today], cfg)
    rows = []
    for i in range(-weeks_back, cfg.load.weeks_ahead + 1):
        start = this + timedelta(weeks=i)
        state = "done" if i < 0 else "current" if i == 0 else "planned"
        load = done_load.get(start, 0.0) if i < 0 else ahead.get(start, 0.0) + (done_load.get(start, 0.0) if i == 0 else 0)
        goal = intended.get(start)
        lo, hi = band(goal, cfg) if goal is not None else (None, None)
        over = hi is not None and load > hi
        number = (numbers or {}).get(start)
        rows.append(LoadWeek(start, number, round(load, 1), goal, lo, hi, state, over,
                             f"Trim week {number}" if over and state != "done" and number else None))
    return rows
