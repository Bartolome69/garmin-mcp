"""Two hard sessions on consecutive days (build prompt, sections 5.3 and 6).

Hard: tempo, intervals, race, and a long run over 25 km. Used when a session is
dragged to another day (the app has a TypeScript twin, so the warning is
instant; this one re-checks before anything is written) and for every week
the engines generate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from .config import DEFAULT, DomainConfig
from .model import DAYS, Session

TYPE_WORDS = {"tempo": "tempo", "intervals": "intervals", "race": "race", "long": "long run"}


def is_hard(s: Session, cfg: DomainConfig = DEFAULT) -> bool:
    if s.type in cfg.backtoback.hard_types:
        return True
    return s.type == "long" and (s.distance_km or 0) > cfg.backtoback.long_hard_km


@dataclass(frozen=True)
class Clash:
    first: Session
    second: Session
    message: str


def _words(s: Session) -> str:
    return f"{DAYS[s.date.weekday()]} {TYPE_WORDS.get(s.type, s.type)}"


def clashes(sessions: Iterable[Session], cfg: DomainConfig = DEFAULT) -> list[Clash]:
    """Every pair of hard sessions on consecutive days, earliest first."""
    hard: dict[date, Session] = {}
    for s in sorted(sessions, key=lambda x: (x.date, x.id)):
        if is_hard(s, cfg):
            hard.setdefault(s.date, s)
    out = []
    for d, s in sorted(hard.items()):
        nxt = hard.get(d + timedelta(days=1))
        if nxt:
            out.append(Clash(s, nxt, f"{_words(s)} and {_words(nxt)} would be back to back"))
    return out


def check_move(sessions: list[Session], session_id: str, to: date, cfg: DomainConfig = DEFAULT) -> list[Clash]:
    """The clashes moving one session to `to` would create that aren't there now."""
    before = {(c.first.id, c.second.id) for c in clashes(sessions, cfg)}
    moved = [s.with_(date=to) if s.id == session_id else s for s in sessions]
    return [c for c in clashes(moved, cfg) if (c.first.id, c.second.id) not in before
            and session_id in (c.first.id, c.second.id)]
