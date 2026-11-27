"""One list of runs from two providers: Garmin first, nothing twice.

Intervals.icu often re-imports runs a Garmin watch already recorded, so a person
with both connected would see each of those twice. A pair is the same run when
it starts within a minute and its distance and time are within 2% of each other
(the build prompt's merge rule, section 5.12, and the app's MergeConfig); then
the higher-priority provider's copy stays (Garmin by default) and the other is
hidden, never deleted, with a record of why so it can be undone.

Pure: activities in, activities out. Each activity is the shape get_activities
returns, with `provider` and the provider's own `id`; start times are
timezone-aware datetimes or ISO strings with an offset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

START_WITHIN_S = 60
WITHIN_PCT = 2.0
DEFAULT_PRIORITY = ("garmin", "intervals")


@dataclass(frozen=True)
class Merged:
    kept: Mapping[str, Any]
    hidden: Mapping[str, Any]
    reason: str               # "same start, distance and time"


def _start(a: Mapping[str, Any]) -> datetime | None:
    v = a.get("start")
    if isinstance(v, datetime):
        return v if v.tzinfo else None
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else None   # a time with no offset can't be compared across providers
    return None


def _close(x: Any, y: Any) -> bool:
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or x <= 0 or y <= 0:
        return False
    return abs(x - y) / max(x, y) * 100 <= WITHIN_PCT


def same_run(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    sa, sb = _start(a), _start(b)
    if sa is None or sb is None or abs((sa - sb).total_seconds()) > START_WITHIN_S:
        return False
    return _close(a.get("distance_m"), b.get("distance_m")) and _close(a.get("duration_s"), b.get("duration_s"))


def merge(activities: Iterable[Mapping[str, Any]], priority: tuple[str, ...] = DEFAULT_PRIORITY,
          kept_apart: set[tuple[str, str]] = frozenset()) -> tuple[list[Mapping[str, Any]], list[Merged]]:
    """The activities with duplicates hidden, and what was hidden.

    `kept_apart` holds pairs of ids the person marked "These are different runs";
    they're never merged. Order is kept (newest first, as given).
    """
    rank = {p: i for i, p in enumerate(priority)}
    items = list(activities)
    order = sorted(range(len(items)), key=lambda i: rank.get(items[i].get("provider"), len(rank)))
    kept: list[int] = []
    merged: list[Merged] = []
    hidden: set[int] = set()
    for i in order:
        a = items[i]
        twin = next((k for k in kept if items[k].get("provider") != a.get("provider") and same_run(items[k], a)
                     and (str(items[k].get("id")), str(a.get("id"))) not in kept_apart
                     and (str(a.get("id")), str(items[k].get("id"))) not in kept_apart), None)
        if twin is None:
            kept.append(i)
        else:
            hidden.add(i)
            merged.append(Merged(items[twin], a, "same start, distance and time"))
    return [a for i, a in enumerate(items) if i not in hidden], merged
