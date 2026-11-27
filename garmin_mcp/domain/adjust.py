"""Life happened: a reason becomes a list of day operations (build prompt, sections 5.4 and 6).

Each reason has a rule. The output is the list of changes, day by day, that the
shared diff shows and the watch confirm sheet sends: nothing here writes
anything. After the rule runs, any two hard days in a row it leaves are fixed
by making the later one easy, so a generated week never breaks the
back-to-back rule.

TODO(sports-science): the volumes and day counts are placeholders (config.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from . import backtoback
from .config import DEFAULT, DomainConfig
from .model import Session, monday, week_of

REASONS = ("unwell", "niggle", "travelling", "race_moved", "busy")
REASON_WORDS = {
    "unwell": "Not feeling 100%", "niggle": "Niggle or injury", "travelling": "Travelling",
    "race_moved": "Race moved", "busy": "Busy week",
}
MEDICAL = "Daash doesn't give medical advice. If you're unwell or hurt, check with a doctor or physio."
HARD_TO_EASY_TITLE = "Easy run"
TREADMILL_TITLE = "Treadmill tempo"


@dataclass(frozen=True)
class DayOp:
    op: str                     # replace | remove | add
    date: date
    before: Session | None
    after: Session | None


@dataclass(frozen=True)
class Adjustment:
    reason: str
    rule: str                   # the rule used, in words (shown with the Coach off)
    ops: tuple[DayOp, ...]
    km_before: float            # the affected week, before and after
    km_after: float
    summary: str                # "3 sessions change · 54 → 37 km this week"
    disclaimer: str | None


def _km(sessions: list[Session]) -> float:
    return round(sum(s.distance_km or 0 for s in sessions), 1)


def _round_km(km: float) -> float:
    return max(0.0, round(km * 2) / 2)


def _easy_from(s: Session, cfg: DomainConfig) -> Session:
    share = cfg.adjust.easy_from_hard_share
    return s.with_(type="easy", title=HARD_TO_EASY_TITLE, load=None,
                   distance_km=_round_km((s.distance_km or 0) * share) or None,
                   minutes=round((s.minutes or 0) * share) or None)


def _cap_volume(week: list[Session], limit_km: float, keep: set[str] = frozenset()) -> list[Session]:
    """Scale the runs (not the ones in `keep`) down so the week fits under limit_km."""
    total = _km(week)
    if total <= limit_km:
        return week
    fixed = _km([s for s in week if s.id in keep])
    flexible = total - fixed
    if flexible <= 0:
        return week
    share = max(0.0, (limit_km - fixed) / flexible)
    return [s if s.id in keep or not s.distance_km else
            s.with_(distance_km=_round_km(s.distance_km * share),
                    minutes=round(s.minutes * share) if s.minutes else None, load=None)
            for s in week]


def _fix_back_to_back(sessions: list[Session], cfg: DomainConfig) -> list[Session]:
    for _ in range(len(sessions)):
        found = backtoback.clashes(sessions, cfg)
        if not found:
            return sessions
        later = found[0].second
        sessions = [_easy_from(s, cfg) if s.id == later.id else s for s in sessions]
    return sessions


def _rule_unwell(plan, since, cfg, days, volume, long_share=None):
    end = since + timedelta(days=days)
    out = []
    for s in plan:
        if since <= s.date < end and backtoback.is_hard(s, cfg) and s.type != "race":
            s = _easy_from(s, cfg)
        elif long_share and since <= s.date < end and s.type == "long":
            s = s.with_(distance_km=_round_km((s.distance_km or 0) * long_share),
                        minutes=round((s.minutes or 0) * long_share) or None, load=None)
        out.append(s)
    start = monday(since)
    week = week_of(out, start)
    limit = _km(week_of(plan, start)) * volume
    capped = {s.id: s for s in _cap_volume(week, limit, keep={s.id for s in week if s.date < since})}
    return [capped.get(s.id, s) for s in out]


def _rule_travelling(plan, since, cfg):
    end = since + timedelta(days=cfg.adjust.travel_days)
    return [s.with_(type="tempo", title=TREADMILL_TITLE, distance_km=None, load=None)
            if since <= s.date < end and s.type == "intervals" else s for s in plan]


def _rule_busy(plan, since, cfg):
    start = monday(since)
    week = [s for s in week_of(plan, start) if s.date >= since]
    quality = sorted([s for s in week if s.type in ("tempo", "intervals")],
                     key=lambda s: -(s.minutes or (s.distance_km or 0) * cfg.load.est_min_per_km))
    keep = {s.id for s in week if s.type in ("long", "race")} | ({quality[0].id} if quality else set())
    minutes = cfg.adjust.busy_easy_minutes
    out = []
    for s in plan:
        if s in week and s.id not in keep and s.type not in ("rest", "strength"):
            s = s.with_(type="easy", title=f"Easy {minutes} min", minutes=minutes,
                        distance_km=_round_km(minutes / cfg.load.est_min_per_km), load=None)
        out.append(s)
    full = week_of(out, start)
    limit = _km(week_of(plan, start)) * cfg.adjust.busy_volume
    capped = {s.id: s for s in _cap_volume(full, limit, keep=keep | {s.id for s in full if s.date < since})}
    return [capped.get(s.id, s) for s in out]


def _rule_race_moved(plan, since, cfg, new_race):
    races = [s for s in plan if s.type == "race"]
    if not races or new_race is None:
        return plan
    race = races[-1]
    out = [s for s in plan if s.id != race.id and s.date < new_race]
    out.append(race.with_(date=new_race))
    weeks = cfg.adjust.taper_weeks
    race_week = monday(new_race)
    for i, share in enumerate(cfg.adjust.taper_volume[-weeks:]):
        start = race_week - timedelta(weeks=weeks - 1 - i)
        week = [s for s in week_of(out, start) if s.date >= since and s.type != "race"]
        ids = {s.id for s in week}
        scaled = {s.id: s for s in _cap_volume(week, _km(week) * share)}
        out = [scaled.get(s.id, s) if s.id in ids else s for s in out]
    return sorted(out, key=lambda s: (s.date, s.id))


RULES = {
    "unwell": lambda c: f"Not feeling 100%: no hard sessions for {c.unwell_days} days, this week at most "
                        f"{c.unwell_volume:.0%} of planned",
    "niggle": lambda c: f"Niggle: no hard sessions for {c.niggle_days} days, long runs cut to "
                        f"{c.niggle_long_share:.0%}, this week at most {c.niggle_volume:.0%} of planned",
    "travelling": lambda c: f"Travelling: intervals become treadmill tempo for {c.travel_days} days; "
                            "the long run stays",
    "race_moved": lambda c: f"Race moved: the race goes on its new date and the last {c.taper_weeks} weeks "
                            "before it taper again",
    "busy": lambda c: f"Busy week: the long run and one quality session stay, other runs become "
                      f"{c.busy_easy_minutes} min easy, at most {c.busy_volume:.0%} of planned",
}


def adjust(plan: list[Session], reason: str, since: date, *, new_race: date | None = None,
           cfg: DomainConfig = DEFAULT) -> Adjustment:
    """The day-by-day changes for one reason, from `since` on. Past days never change."""
    if reason not in REASONS:
        raise ValueError(f"unknown reason {reason!r}")
    a = cfg.adjust
    if reason == "unwell":
        after = _rule_unwell(plan, since, cfg, a.unwell_days, a.unwell_volume)
    elif reason == "niggle":
        after = _rule_unwell(plan, since, cfg, a.niggle_days, a.niggle_volume, a.niggle_long_share)
    elif reason == "travelling":
        after = _rule_travelling(plan, since, cfg)
    elif reason == "busy":
        after = _rule_busy(plan, since, cfg)
    else:
        after = _rule_race_moved(plan, since, cfg, new_race)
    after = _fix_back_to_back(after, cfg)
    ops = diff(plan, after, since)
    week = monday(since)
    before_km, after_km = _km(week_of(plan, week)), _km(week_of(after, week))
    n = len(ops)
    summary = (f"{n} session{'s' if n != 1 else ''} change{'' if n != 1 else 's'} · "
               f"{before_km:g} → {after_km:g} km this week") if n else "Nothing needs to change"
    return Adjustment(reason, RULES[reason](a), tuple(ops), before_km, after_km, summary,
                      MEDICAL if reason in ("unwell", "niggle") else None)


def diff(before: list[Session], after: list[Session], since: date) -> list[DayOp]:
    """Add, replace and remove operations, by session id, from `since` on, in date order."""
    old = {s.id: s for s in before}
    new = {s.id: s for s in after}
    ops = []
    for sid, s in old.items():
        if s.date < since:
            continue
        if sid not in new:
            ops.append(DayOp("remove", s.date, s, None))
        elif new[sid] != s:
            if new[sid].date != s.date:
                ops.append(DayOp("remove", s.date, s, None))
                ops.append(DayOp("add", new[sid].date, None, new[sid]))
            else:
                ops.append(DayOp("replace", s.date, s, new[sid]))
    for sid, s in new.items():
        if sid not in old and s.date >= since:
            ops.append(DayOp("add", s.date, None, s))
    return sorted(ops, key=lambda o: (o.date, {"remove": 0, "replace": 1, "add": 2}[o.op]))
