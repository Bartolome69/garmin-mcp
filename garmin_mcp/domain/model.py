"""The plain shapes the engines take and give back."""

from __future__ import annotations

import math

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

# Session types, as the app's colours name them.
SESSION_TYPES = ("easy", "recovery", "tempo", "intervals", "long", "race", "strength", "rest", "other")


@dataclass(frozen=True)
class Session:
    date: date
    type: str
    title: str = ""
    distance_km: float | None = None
    minutes: float | None = None
    load: float | None = None           # Garmin's training load, when it's known
    id: str = ""

    def with_(self, **changes) -> "Session":
        return replace(self, **changes)


@dataclass(frozen=True)
class Activity:
    id: str
    provider: str
    start: datetime                     # timezone-aware
    distance_m: float
    duration_s: float


def monday(d: date) -> date:
    """Weeks run Monday to Sunday, by local calendar date (so DST never shortens one)."""
    return d - timedelta(days=d.weekday())


def week_of(sessions: list[Session], start: date) -> list[Session]:
    end = start + timedelta(days=7)
    return [s for s in sessions if start <= s.date < end]


def round_half_up(x: float) -> int:
    """Rounds .5 up, as JavaScript's Math.round does, so the app's twins agree to the second."""
    return math.floor(x + 0.5)


def mmss(seconds: float) -> str:
    total = round_half_up(seconds)
    if total >= 3600:
        return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
    return f"{total // 60}:{total % 60:02d}"


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def short_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


@dataclass(frozen=True)
class Problem:
    """Something an engine noticed, with words a runner can read."""
    code: str
    message: str
    dates: tuple[date, ...] = field(default_factory=tuple)
