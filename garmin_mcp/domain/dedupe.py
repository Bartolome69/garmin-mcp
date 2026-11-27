"""The same run from two sources (build prompt, sections 5.12 and 6).

Two activities starting within a minute of each other, with distances within
2%, are one run: the higher-priority source is kept and the other hidden (not
deleted), with a merge record so "These are different runs" can undo it.
Starts are compared as instants, so a watch and an app recording in different
time zones, or either side of a DST change, still match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .config import DEFAULT, DomainConfig
from .model import Activity


@dataclass(frozen=True)
class MergeRecord:
    kept: str
    hidden: str
    reason: str


def same_run(a: Activity, b: Activity, cfg: DomainConfig = DEFAULT) -> bool:
    if abs((a.start - b.start).total_seconds()) > cfg.merge.start_within_seconds:
        return False
    longest = max(a.distance_m, b.distance_m)
    if longest <= 0:
        return False
    return abs(a.distance_m - b.distance_m) <= longest * cfg.merge.distance_within_pct / 100


def merge(activities: Iterable[Activity], priority: list[str], *, not_same: Iterable[frozenset[str]] = (),
          cfg: DomainConfig = DEFAULT) -> tuple[list[Activity], list[MergeRecord]]:
    """(the activities to show, the merges made). Ties in priority keep the earlier id."""
    rank = {p: i for i, p in enumerate(priority)}
    kept_apart = {frozenset(pair) for pair in not_same}
    ordered = sorted(activities, key=lambda a: (rank.get(a.provider, len(rank)), a.start, a.id))
    shown: list[Activity] = []
    records: list[MergeRecord] = []
    for a in ordered:
        twin = next((k for k in shown if k.provider != a.provider and frozenset((k.id, a.id)) not in kept_apart
                     and same_run(k, a, cfg)), None)
        if twin:
            gap = abs((a.start - twin.start).total_seconds())
            diff = abs(a.distance_m - twin.distance_m) / max(a.distance_m, twin.distance_m) * 100
            records.append(MergeRecord(twin.id, a.id, f"Started {gap:.0f} s apart, distances {diff:.1f}% apart"))
        else:
            shown.append(a)
    return sorted(shown, key=lambda a: a.start), records
