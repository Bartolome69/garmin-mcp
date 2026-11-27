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
# Today's other options and the Trends warning: the same shape of answer, one day or one week.
OPTIONS = ("easier", "rest", "swap", "trim")
REASON_WORDS = {
    "unwell": "Not feeling 100%", "niggle": "Niggle or injury", "travelling": "Travelling",
    "race_moved": "Race moved", "busy": "Busy week",
    "easier": "Make it easier", "rest": "Rest and recover", "swap": "Swap a day", "trim": "Trim the week",
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


def _easy_title(km: float | None, minutes: float | None) -> str:
    return f"Easy {km:g} km" if km else f"Easy {minutes:g} min" if minutes else HARD_TO_EASY_TITLE


def _scaled_title(title: str, old_km: float | None, new_km: float | None) -> str:
    """"Long 18 km easy" scaled to 9 km reads "Long 9 km easy"; a title without the distance stays."""
    if old_km and new_km is not None and f"{old_km:g} km" in title:
        return title.replace(f"{old_km:g} km", f"{new_km:g} km", 1)
    return title


def _easy_from(s: Session, cfg: DomainConfig) -> Session:
    share = cfg.adjust.easy_from_hard_share
    km = _round_km((s.distance_km or 0) * share) or None
    minutes = round((s.minutes or 0) * share) or None
    return s.with_(type="easy", title=_easy_title(km, minutes), load=None, distance_km=km, minutes=minutes)


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
            s.with_(distance_km=_round_km(s.distance_km * share), title=_scaled_title(s.title, s.distance_km, _round_km(s.distance_km * share)),
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


def _rule_unwell(plan, since, cfg, days, volume, long_share=None, start=None):
    end = since + timedelta(days=days)
    out = []
    for s in plan:
        if since <= s.date < end and backtoback.is_hard(s, cfg) and s.type != "race":
            s = _easy_from(s, cfg)
        elif long_share and since <= s.date < end and s.type == "long":
            km = _round_km((s.distance_km or 0) * long_share)
            s = s.with_(distance_km=km, title=_scaled_title(s.title, s.distance_km, km),
                        minutes=round((s.minutes or 0) * long_share) or None, load=None)
        out.append(s)
    frozen = start or since
    first = monday(since)
    week = week_of(out, first)
    limit = _km(week_of(plan, first)) * volume
    capped = {s.id: s for s in _cap_volume(week, limit, keep={s.id for s in week if s.date < frozen})}
    return [capped.get(s.id, s) for s in out]


def _rule_travelling(plan, since, cfg):
    end = since + timedelta(days=cfg.adjust.travel_days)
    return [s.with_(type="tempo", title=TREADMILL_TITLE, distance_km=None, load=None)
            if since <= s.date < end and s.type == "intervals" else s for s in plan]


def _rule_busy(plan, since, cfg, start=None):
    frozen = start or since
    start = monday(since)
    week = [s for s in week_of(plan, start) if s.date >= frozen]
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
    capped = {s.id: s for s in _cap_volume(full, limit, keep=keep | {s.id for s in full if s.date < frozen})}
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
           today: date | None = None, cfg: DomainConfig = DEFAULT) -> Adjustment:
    """The day-by-day changes for one reason, from `since` on.

    `since` is when it started, which can be days ago: a rule's window ("no hard
    sessions for 3 days") counts from then. Days before `today` never change:
    they're done.
    """
    if reason not in REASONS:
        raise ValueError(f"unknown reason {reason!r}")
    a = cfg.adjust
    start = max(since, today) if today else since
    if reason == "unwell":
        after = _rule_unwell(plan, since, cfg, a.unwell_days, a.unwell_volume, start=start)
    elif reason == "niggle":
        after = _rule_unwell(plan, since, cfg, a.niggle_days, a.niggle_volume, a.niggle_long_share, start=start)
    elif reason == "travelling":
        after = _rule_travelling(plan, since, cfg)
    elif reason == "busy":
        after = _rule_busy(plan, since, cfg, start=start)
    else:
        after = _rule_race_moved(plan, since, cfg, new_race)
    return _finish(plan, after, reason, RULES[reason](a), start, monday(start), cfg,
                   MEDICAL if reason in ("unwell", "niggle") else None)


def _finish(plan, after, reason, rule, start, week, cfg, disclaimer=None) -> Adjustment:
    old = {s.id: s for s in plan}
    # Done days stay as they were, whatever the rule did to them.
    after = [old[s.id] if s.id in old and old[s.id].date < start else s for s in after]
    after = _fix_back_to_back(after, cfg)
    after = [old[s.id] if s.id in old and old[s.id].date < start else s for s in after]
    ops = diff(plan, after, start)
    before_km, after_km = _km(week_of(plan, week)), _km(week_of(after, week))
    n = sessions_changed(ops)
    summary = (f"{n} session{'s' if n != 1 else ''} change{'' if n != 1 else 's'} · "
               f"{before_km:g} → {after_km:g} km this week") if n else "Nothing needs to change"
    return Adjustment(reason, rule, tuple(ops), before_km, after_km, summary, disclaimer)


def sessions_changed(ops) -> int:
    """Sessions, not watch operations: a move is a remove and an add of one session."""
    return len({(o.before or o.after).id for o in ops})


def _today_session(plan: list[Session], today: date) -> Session | None:
    return next((s for s in plan if s.date == today and s.type != "rest"), None)


def easier(plan: list[Session], today: date, cfg: DomainConfig = DEFAULT) -> Adjustment:
    """Make it easier: today's hard session becomes an easy run, the rest of the week stays."""
    s = _today_session(plan, today)
    after = [_easy_from(x, cfg) if s and x.id == s.id and backtoback.is_hard(x, cfg) else x for x in plan]
    rule = (f"Make it easier: today's {s.type} becomes an easy run, about "
            f"{cfg.adjust.easy_from_hard_share:.0%} of the time; the rest of the week stays") if s else \
        "Make it easier: nothing hard today"
    return _finish(plan, after, "easier", rule, today, monday(today), cfg)


def rest(plan: list[Session], today: date, cfg: DomainConfig = DEFAULT) -> Adjustment:
    """Rest and recover: today's session comes off; a rest day counts toward the active week."""
    s = _today_session(plan, today)
    after = [x for x in plan if not s or x.id != s.id]
    return _finish(plan, after, "rest", "Rest and recover: today becomes a rest day, which counts toward "
                   "your active week; the rest of the week stays", today, monday(today), cfg)


def swap(plan: list[Session], today: date, cfg: DomainConfig = DEFAULT) -> Adjustment:
    """Swap a day: today's session trades places with a later easy run this week,
    the first one that leaves no two hard days in a row."""
    s = _today_session(plan, today)
    end = monday(today) + timedelta(days=7)
    for other in sorted((x for x in plan if today < x.date < end and x.type in ("easy", "recovery", "strength")),
                        key=lambda x: x.date):
        if not s:
            break
        after = [x.with_(date=other.date) if x.id == s.id else x.with_(date=today) if x.id == other.id else x
                 for x in plan]
        if not backtoback.clashes([x for x in after if x.date >= today], cfg):
            day = other.date.strftime("%a")
            what = "strength session" if other.type == "strength" else "easy run"
            return _finish(plan, after, "swap", f"Swap a day: today's {s.type} moves to {day} and {day}'s "
                           f"{what} comes to today, with no two hard days in a row", today, monday(today), cfg)
    return _finish(plan, list(plan), "swap", "Swap a day: no easier day left this week to swap with without "
                   "two hard days in a row", today, monday(today), cfg)


def trim(plan: list[Session], week: date, limit_km: float, today: date, number: int | None = None,
         cfg: DomainConfig = DEFAULT) -> Adjustment:
    """Trim week N: its easy runs scale down to `limit_km` (the top of the target band); the long run
    and the quality sessions stay as they are."""
    start = max(week, today)
    days = week_of(plan, week)
    keep = {s.id for s in days if s.type in ("long", "race", "tempo", "intervals") or s.date < start}
    scaled = {s.id: s for s in _cap_volume(days, limit_km, keep=keep)}
    after = [scaled.get(s.id, s) for s in plan]
    label = f"week {number}" if number else "the week"
    return _finish(plan, after, "trim", f"Trim {label}: the easy runs scale down so it sits at the top of your target "
                   f"band, {limit_km:g} km; the long run and the quality sessions stay", start, week, cfg)


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
