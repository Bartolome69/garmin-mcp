"""Daash Crews: a few friends who see each other's easy and long runs.

Each member's Daash already knows their week. A crew pools the part of it that
suits company (easy runs, long runs and the club's standing run) so Daash can
point out who else is running on the same day, and suggest doing it together.
It never moves anyone's run to make a match, and a run the crew already does
together every week (a club run) is shown but never matched: the crew is for
syncing up the other days.

Only the hosted server has crews: it alone knows who is asking. It sets the
member for each request here, the same way it binds the Garmin session, and
every function below works on that member. Locally nobody is set and the crew
tools stay hidden.

What is kept, and only for members, is the minimum a crew card needs: the
crew, each member's chosen name, rough running times, whether they go to the
club run, and the days, kinds and distances of their easy
and long runs for the next two weeks. A hard session is kept as "hard" on its
day and nothing more. Runs that have been done, and anything else from Garmin,
are never kept. Days drop off once they have passed, and leaving the crew or
disconnecting deletes the lot.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from garmin_mcp import store

MAX_MEMBERS = 20
# How far ahead a member's runs are kept, counted from today.
HORIZON_DAYS = 14
# When someone usually runs: mornings, afternoons or evenings, on weekdays
# and at weekends, since most people differ between the two (evenings in the
# week, mornings or afternoons at the weekend). Garmin's calendar has days but
# no times, so this is how runs on the same day are told to be at the same time.
TIMES = ("weekday_morning", "weekday_afternoon", "weekday_evening",
         "weekend_morning", "weekend_afternoon", "weekend_evening")
_TIME_WORDS = {
    "weekday_morning": "weekday mornings", "weekday_afternoon": "weekday afternoons",
    "weekday_evening": "weekday evenings", "weekend_morning": "weekend mornings",
    "weekend_afternoon": "weekend afternoons", "weekend_evening": "weekend evenings",
}
# "mornings" alone means weekday and weekend; lunchtime is an afternoon.
_TIME_ALIASES: dict[str, tuple[str, ...]] = {}
for _slot in ("morning", "afternoon", "evening"):
    _TIME_ALIASES[_slot] = _TIME_ALIASES[_slot + "s"] = ("weekday_" + _slot, "weekend_" + _slot)
    _TIME_ALIASES["weekday_" + _slot + "s"] = ("weekday_" + _slot,)
    _TIME_ALIASES["weekend_" + _slot + "s"] = ("weekend_" + _slot,)
for _alias in ("lunch", "lunchtime", "lunchtimes", "midday"):
    _TIME_ALIASES[_alias] = ("weekday_afternoon", "weekend_afternoon")
    _TIME_ALIASES["weekday_" + _alias] = ("weekday_afternoon",)
    _TIME_ALIASES["weekend_" + _alias] = ("weekend_afternoon",)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# Invite codes avoid letters and digits that read alike (0/O, 1/I/L).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# Plan session types that are run hard and alone.
HARD = {"threshold", "intervals", "sub", "race", "tempo"}
_NOT_RUNS = {"strength", "swim", "ride", "other"}


class CrewError(Exception):
    """Something the person asked for that can't be done, said plainly."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS crews (
    crew_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    code        TEXT NOT NULL UNIQUE,
    owner       TEXT NOT NULL,
    club_json   TEXT,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS crew_members (
    member      TEXT PRIMARY KEY,           -- one crew per person
    crew_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    times       TEXT,                       -- JSON list of TIMES
    club        INTEGER NOT NULL DEFAULT 0, -- usually goes to the club run
    pace_s      INTEGER,                    -- no longer used: paces aren't kept
    updated_on  TEXT,                       -- the day their runs were last read
    joined_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS crew_members_crew ON crew_members (crew_id);
CREATE TABLE IF NOT EXISTS crew_runs (
    member      TEXT NOT NULL,
    day         TEXT NOT NULL,              -- YYYY-MM-DD
    kind        TEXT NOT NULL,              -- easy, long or hard
    km          REAL
);
CREATE INDEX IF NOT EXISTS crew_runs_member ON crew_runs (member, day);
"""

_paces_cleared = False


def _connect():
    global _paces_cleared
    conn = store._connect()
    conn.executescript(SCHEMA)
    if not _paces_cleared:
        # Paces were kept for a day in October 2026, then dropped: a crew is
        # for everyone, whatever their pace. Whatever was kept goes, once.
        conn.execute("UPDATE crew_members SET pace_s = NULL WHERE pace_s IS NOT NULL")
        conn.commit()
        _paces_cleared = True
    return conn


# --------------------------------------------------------------------------
# Who is asking
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Member:
    """The person behind a request: the same key whichever connection they use."""
    key: str


_current: ContextVar["Member | None"] = ContextVar("garmin_crew_member", default=None)


def member_key(user_token: str, email_hash: str | None) -> str:
    """Signing in again replaces the connection but keeps the address, so the
    address's fingerprint is what a membership hangs on."""
    return email_hash or f"t:{user_token}"


def use(member: Member | None):
    return _current.set(member)


def reset(token) -> None:
    _current.reset(token)


def current() -> Member | None:
    return _current.get()


def available() -> bool:
    """Whether crews exist where this request is being answered."""
    return _current.get() is not None


def _me() -> Member:
    member = _current.get()
    if member is None:
        raise CrewError("Daash Crews work on the hosted Daash connector, not a local copy.")
    return member


def in_crew(key: str | None = None) -> bool:
    key = key or (_current.get().key if _current.get() else None)
    if not key:
        return False
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM crew_members WHERE member = ?", (key,)).fetchone() is not None


# --------------------------------------------------------------------------
# Reading a member's week out of their plan
# --------------------------------------------------------------------------

def _is_run(session: dict[str, Any]) -> bool:
    sport = session.get("sport") or "run"
    kind = session.get("kind") or "run"
    return sport == "run" and kind == "run" and session.get("type") not in _NOT_RUNS


def _kind(session: dict[str, Any]) -> str:
    kind = session.get("type")
    if kind in HARD:
        return "hard"
    return "long" if kind == "long" else "easy"


def from_plan(result: Any, today: date) -> tuple[set[str], list[tuple[str, str, float | None]]]:
    """What a crew needs from a get_plan or create_plan answer.

    Returns the days the answer covers (within the next two weeks) and the runs
    ahead on those days as (day, kind, km). Nothing about runs already done,
    paces included: a crew matches people by day and time, never by speed.
    """
    if not isinstance(result, dict) or not isinstance(result.get("weeks"), list):
        return set(), []
    first, last = today, today + timedelta(days=HORIZON_DAYS - 1)
    covered: set[str] = set()
    runs: list[tuple[str, str, float | None]] = []
    for week in result["weeks"]:
        if not isinstance(week, dict):
            continue
        try:
            starts = date.fromisoformat(str(week.get("starts"))[:10])
        except ValueError:
            continue
        for offset in range(7):
            day = starts + timedelta(days=offset)
            if first <= day <= last:
                covered.add(day.isoformat())
        for session in week.get("sessions") or []:
            if not isinstance(session, dict) or not _is_run(session):
                continue
            status = str(session.get("status") or "")
            day_text = str(session.get("date") or "")[:10]
            if status.startswith("done") or status == "logged":
                continue
            if status == "missed" or day_text not in covered:
                continue
            kind = _kind(session)
            km = session.get("planned_km")
            runs.append((day_text, kind, round(float(km), 1) if isinstance(km, (int, float)) and kind != "hard" else None))
    return covered, runs


def capture(key: str, result: Any, today: date | None = None) -> bool:
    """Keep a member's coming runs from a plan answer they were just shown.

    Only for someone in a crew, and only the days that answer covers: a plan
    that ends next week leaves the week after as it was. Returns whether
    anything was kept.
    """
    today = today or date.today()
    if not isinstance(result, dict) or not isinstance(result.get("weeks"), list):
        return False
    covered, runs = from_plan(result, today)
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM crew_members WHERE member = ?", (key,)).fetchone() is None:
            return False
        # A plan with nothing in the next two weeks still counts as read: the
        # crew sees an empty week, not "no week yet".
        conn.execute("DELETE FROM crew_runs WHERE day < ?", (today.isoformat(),))
        conn.executemany("DELETE FROM crew_runs WHERE member = ? AND day = ?", [(key, d) for d in covered])
        conn.executemany(
            "INSERT INTO crew_runs (member, day, kind, km) VALUES (?, ?, ?, ?)",
            [(key, d, k, km) for d, k, km in runs],
        )
        conn.execute(
            "UPDATE crew_members SET updated_on = ? WHERE member = ?",
            (today.isoformat(), key),
        )
    return True


# --------------------------------------------------------------------------
# Starting, joining, leaving
# --------------------------------------------------------------------------

def _clean_name(name: Any, what: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    if not text:
        raise CrewError(f"Say what {what} should be.")
    return text[:limit]


def _clean_times(times: Any) -> list[str] | None:
    if times is None:
        return None
    wanted: list[str] = []
    for t in times or []:
        key = str(t).strip().lower().replace(" ", "_").replace("-", "_")
        wanted.extend(_TIME_ALIASES.get(key, (key,)))
    unknown = [t for t in wanted if t not in TIMES]
    if unknown:
        raise CrewError(f"Usual times are mornings, afternoons or evenings, weekday or weekend: any of {', '.join(TIMES)}; not {', '.join(unknown)}.")
    return [t for t in TIMES if t in wanted]


def _clean_club(day: Any, at: Any, km: Any, place: Any) -> dict[str, Any] | None:
    if day is None and at is None and km is None and place is None:
        return None
    day_text = str(day or "").strip().lower()[:3]
    if day_text not in DAYS:
        raise CrewError("The club run needs a day of the week, such as \"fri\".")
    at_text = str(at or "").strip()
    if at_text and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", at_text):
        raise CrewError("The club run's time is HH:MM, such as \"06:45\".")
    club: dict[str, Any] = {"day": day_text}
    if at_text:
        club["time"] = at_text.zfill(5)
    if km is not None:
        try:
            club["km"] = round(float(km), 1)
        except (TypeError, ValueError):
            raise CrewError("The club run's distance is a number of km, such as 4.3.") from None
    if place:
        club["place"] = str(place).strip()[:60]
    return club


def _new_code() -> str:
    return "DC-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))


def start(crew_name: Any, name: Any, times: Any = None, club: dict[str, Any] | None = None,
          goes_to_club: bool | None = None) -> dict[str, Any]:
    me = _me()
    name = _clean_name(name, "the crew calls you", 24)
    # The name is only a label for the card and the invite; without one, the
    # crew is named after whoever started it.
    crew_name = _clean_name(crew_name or f"{name}'s crew", "the crew is called", 40)
    times = _clean_times(times) or []
    with _connect() as conn:
        mine = conn.execute(
            "SELECT c.name FROM crew_members m JOIN crews c ON c.crew_id = m.crew_id WHERE m.member = ?",
            (me.key,),
        ).fetchone()
        if mine:
            raise CrewError(f"You're already in {mine[0]}. Leave it first: a person is in one crew at a time.")
        crew_id = secrets.token_hex(8)
        for _ in range(20):
            code = _new_code()
            if conn.execute("SELECT 1 FROM crews WHERE code = ?", (code,)).fetchone() is None:
                break
        conn.execute(
            "INSERT INTO crews (crew_id, name, code, owner, club_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (crew_id, crew_name, code, me.key, json.dumps(club) if club else None, int(time.time())),
        )
        conn.execute(
            "INSERT INTO crew_members (member, crew_id, name, times, club, joined_at) VALUES (?, ?, ?, ?, ?, ?)",
            (me.key, crew_id, name, json.dumps(times), 1 if (goes_to_club if goes_to_club is not None else bool(club)) else 0,
             int(time.time())),
        )
    return {"crew": crew_name, "code": code}


def join(code: Any, name: Any, times: Any = None, goes_to_club: bool | None = None) -> dict[str, Any]:
    me = _me()
    code_text = re.sub(r"[^A-Z0-9]", "", str(code or "").upper())
    if code_text.startswith("DC"):
        code_text = code_text[2:]
    code_text = "DC-" + code_text
    name = _clean_name(name, "the crew calls you", 24)
    times = _clean_times(times) or []
    with _connect() as conn:
        crew = conn.execute("SELECT crew_id, name, club_json FROM crews WHERE code = ?", (code_text,)).fetchone()
        if crew is None:
            raise CrewError(f"No crew has the code {code_text}. Check it with whoever sent it.")
        crew_id, crew_name, club_json = crew
        mine = conn.execute("SELECT crew_id FROM crew_members WHERE member = ?", (me.key,)).fetchone()
        if mine and mine[0] != crew_id:
            other = conn.execute("SELECT name FROM crews WHERE crew_id = ?", (mine[0],)).fetchone()
            raise CrewError(f"You're in {other[0] if other else 'another crew'}. Leave it first: one crew at a time.")
        if not mine:
            count = conn.execute("SELECT COUNT(*) FROM crew_members WHERE crew_id = ?", (crew_id,)).fetchone()[0]
            if count >= MAX_MEMBERS:
                raise CrewError(f"{crew_name} is full: a crew has up to {MAX_MEMBERS} people.")
        taken = conn.execute(
            "SELECT 1 FROM crew_members WHERE crew_id = ? AND lower(name) = lower(?) AND member != ?",
            (crew_id, name, me.key),
        ).fetchone()
        if taken:
            raise CrewError(f"Someone in {crew_name} is already called {name}. Pick another name, such as with an initial.")
        club_on = 1 if (goes_to_club if goes_to_club is not None else bool(club_json)) else 0
        conn.execute(
            "INSERT INTO crew_members (member, crew_id, name, times, club, joined_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(member) DO UPDATE SET name = excluded.name, times = excluded.times, club = excluded.club",
            (me.key, crew_id, name, json.dumps(times), club_on, int(time.time())),
        )
    return {"crew": crew_name, "code": code_text}


def leave() -> dict[str, Any]:
    me = _me()
    with _connect() as conn:
        row = conn.execute(
            "SELECT c.crew_id, c.name, c.owner FROM crew_members m JOIN crews c ON c.crew_id = m.crew_id WHERE m.member = ?",
            (me.key,),
        ).fetchone()
        if row is None:
            raise CrewError("You're not in a crew.")
        crew_id, crew_name, owner = row
        conn.execute("DELETE FROM crew_members WHERE member = ?", (me.key,))
        conn.execute("DELETE FROM crew_runs WHERE member = ?", (me.key,))
        rest = conn.execute(
            "SELECT member FROM crew_members WHERE crew_id = ? ORDER BY joined_at", (crew_id,)
        ).fetchall()
        if not rest:
            conn.execute("DELETE FROM crews WHERE crew_id = ?", (crew_id,))
        elif owner == me.key:
            conn.execute("UPDATE crews SET owner = ? WHERE crew_id = ?", (rest[0][0], crew_id))
    return {"left": crew_name, "crew_deleted": not rest}


def my_week_read_on() -> str | None:
    """The day the asking member's runs were last read, or None if never or not a member."""
    member = _current.get()
    if member is None:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT updated_on FROM crew_members WHERE member = ?", (member.key,)).fetchone()
    return row[0] if row else None


def purge_past(today: date | None = None) -> int:
    """Delete every crew run on a day before today. Returns how many went."""
    today = today or date.today()
    with _connect() as conn:
        return conn.execute("DELETE FROM crew_runs WHERE day < ?", (today.isoformat(),)).rowcount


def forget(key: str) -> None:
    """Everything a person has in crews, gone: for Disconnect."""
    with _connect() as conn:
        row = conn.execute("SELECT crew_id FROM crew_members WHERE member = ?", (key,)).fetchone()
        conn.execute("DELETE FROM crew_members WHERE member = ?", (key,))
        conn.execute("DELETE FROM crew_runs WHERE member = ?", (key,))
        if row:
            rest = conn.execute(
                "SELECT member FROM crew_members WHERE crew_id = ? ORDER BY joined_at", (row[0],)
            ).fetchall()
            if not rest:
                conn.execute("DELETE FROM crews WHERE crew_id = ?", (row[0],))
            else:
                conn.execute("UPDATE crews SET owner = ? WHERE crew_id = ? AND owner = ?", (rest[0][0], row[0], key))


def update(name: Any = None, times: Any = None, goes_to_club: bool | None = None,
           crew_name: Any = None, club: dict[str, Any] | None = None, remove: Any = None) -> dict[str, Any]:
    me = _me()
    with _connect() as conn:
        row = conn.execute(
            "SELECT c.crew_id, c.name, c.owner FROM crew_members m JOIN crews c ON c.crew_id = m.crew_id WHERE m.member = ?",
            (me.key,),
        ).fetchone()
        if row is None:
            raise CrewError("You're not in a crew. Join one with the code a friend sent, or start one.")
        crew_id, current_name, owner = row
        changed: list[str] = []
        if name is not None:
            new = _clean_name(name, "the crew calls you", 24)
            taken = conn.execute(
                "SELECT 1 FROM crew_members WHERE crew_id = ? AND lower(name) = lower(?) AND member != ?",
                (crew_id, new, me.key),
            ).fetchone()
            if taken:
                raise CrewError(f"Someone in {current_name} is already called {new}.")
            conn.execute("UPDATE crew_members SET name = ? WHERE member = ?", (new, me.key))
            changed.append("your name")
        if times is not None:
            conn.execute("UPDATE crew_members SET times = ? WHERE member = ?", (json.dumps(_clean_times(times)), me.key))
            changed.append("your usual times")
        if goes_to_club is not None:
            conn.execute("UPDATE crew_members SET club = ? WHERE member = ?", (1 if goes_to_club else 0, me.key))
            changed.append("whether you go to the club run")
        if crew_name is not None or club is not None or remove is not None:
            if owner != me.key:
                raise CrewError("Only whoever started the crew can rename it, set its club run or remove someone.")
        if crew_name is not None:
            conn.execute("UPDATE crews SET name = ? WHERE crew_id = ?", (_clean_name(crew_name, "the crew is called", 40), crew_id))
            changed.append("the crew's name")
        if club is not None:
            conn.execute("UPDATE crews SET club_json = ? WHERE crew_id = ?", (json.dumps(club) if club else None, crew_id))
            changed.append("the club run")
        if remove is not None:
            target = conn.execute(
                "SELECT member FROM crew_members WHERE crew_id = ? AND lower(name) = lower(?)",
                (crew_id, str(remove).strip()),
            ).fetchone()
            if target is None:
                raise CrewError(f"Nobody in {current_name} is called {remove}.")
            if target[0] == me.key:
                raise CrewError("To take yourself out, leave the crew instead.")
            conn.execute("DELETE FROM crew_members WHERE member = ?", (target[0],))
            conn.execute("DELETE FROM crew_runs WHERE member = ?", (target[0],))
            changed.append(f"removed {remove}")
    return {"changed": changed}


# --------------------------------------------------------------------------
# The crew's week, and what to run together
# --------------------------------------------------------------------------

def _fmt_km(km: float | None) -> str:
    if km is None:
        return ""
    return f"{km:g}" if float(km).is_integer() else f"{km:.1f}"


def _fmt_time(hhmm: str | None) -> str | None:
    if not hhmm:
        return None
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}{suffix}" if m else f"{h12}{suffix}"


def _short_date(d: date) -> str:
    return f"{_SHORT[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"


def _slot_for(day: date, times: list[str]) -> set[str]:
    prefix = "weekend" if day.weekday() >= 5 else "weekday"
    return {t.split("_", 1)[1] for t in times if t.startswith(prefix)}


_SLOTS = ("morning", "afternoon", "evening")


def _best_group(day: date, people: list[dict[str, Any]], must: dict[str, Any] | None = None
                ) -> tuple[list[dict[str, Any]], str | None]:
    """The biggest set of these people who can run together that day.

    Together means a usual time they share; someone who hasn't said when they
    run fits any. Pace never comes into it: whoever is quicker runs at the
    group's pace, as a club run does. Someone who can't make that time drops
    out rather than sinking the rest. With
    must, only groups that include that person count. Returns the group, two
    or more, and the time it meets at, or an empty group.
    """
    best: tuple[list[dict[str, Any]], str | None] = ([], None)
    options: list[str | None] = list(_SLOTS) + [None]
    for slot in options:
        if slot is None:
            free = [p for p in people if not p["times"]]
        else:
            free = [p for p in people if not p["times"] or slot in _slot_for(day, p["times"])]
        group = free
        if must is not None and must not in group:
            continue
        if len(group) >= 2 and len(group) > len(best[0]):
            timed = any(p["times"] for p in group)
            best = (group, slot if timed else None)
    return best


def _km_range(kms: list[float | None]) -> tuple[float | None, float | None]:
    known = [k for k in kms if k]
    return (min(known), max(known)) if known else (None, None)


def week(week_of: date | None = None, today: date | None = None) -> dict[str, Any]:
    """The crew card's data for the week holding week_of (this week by default)."""
    me = _me()
    today = today or date.today()
    # On a Sunday this week is all but over: the week ahead is the one to plan.
    anchor = week_of or (today + timedelta(days=1) if today.weekday() == 6 else today)
    monday = anchor - timedelta(days=anchor.weekday())
    days = [monday + timedelta(days=i) for i in range(7)]
    with _connect() as conn:
        conn.execute("DELETE FROM crew_runs WHERE day < ?", (today.isoformat(),))
        row = conn.execute(
            "SELECT c.crew_id, c.name, c.code, c.owner, c.club_json FROM crew_members m "
            "JOIN crews c ON c.crew_id = m.crew_id WHERE m.member = ?",
            (me.key,),
        ).fetchone()
        if row is None:
            raise CrewError(
                "You're not in a crew yet. Ask a friend for their crew's invite code (like DC-7Q4M), "
                "or start a crew and share yours."
            )
        crew_id, crew_name, code, owner, club_json = row
        members = conn.execute(
            "SELECT member, name, times, club, updated_on FROM crew_members WHERE crew_id = ? ORDER BY joined_at",
            (crew_id,),
        ).fetchall()
        keys = [m[0] for m in members]
        marks = ",".join("?" * len(keys))
        runs = conn.execute(
            f"SELECT member, day, kind, km FROM crew_runs WHERE member IN ({marks}) AND day BETWEEN ? AND ? ORDER BY day",
            (*keys, days[0].isoformat(), days[-1].isoformat()),
        ).fetchall()
    club = json.loads(club_json) if club_json else None
    club_day = days[DAYS.index(club["day"])] if club else None

    people: list[dict[str, Any]] = []
    for key, name, times, goes, updated_on in members:
        people.append({
            "key": key, "name": name, "you": key == me.key, "times": json.loads(times) if times else [],
            "club": bool(goes), "updated_on": updated_on, "runs": {},
        })
    by_key = {p["key"]: p for p in people}
    for key, day_text, kind, km in runs:
        by_key[key]["runs"].setdefault(day_text, []).append({"kind": kind, "km": km})

    def social(person: dict[str, Any], day: date) -> list[dict[str, Any]]:
        return [r for r in person["runs"].get(day.isoformat(), []) if r["kind"] in ("easy", "long")]

    together: list[dict[str, Any]] = []
    # The club's standing run is already fixed in everyone's week: it shows on
    # the grid for whoever goes, and is left out of the matching, so Daash
    # never announces a run the crew has done together for years.

    # Runs on the same day, easy or long, at times that meet. An
    # easy and a long run match too: the shared part is the shorter run and
    # the long-runner carries on. Nobody's run is ever moved to make a match.
    for day in days:
        if day < today:
            continue
        group, when = _best_group(day, [
            p for p in people
            if social(p, day) and not (club_day == day and p["club"])
        ])
        if not group:
            continue
        runs_there = [r for p in group for r in social(p, day)]
        kinds = {r["kind"] for r in runs_there}
        lo, hi = _km_range([r["km"] for r in runs_there])
        together.append({
            "date": day.isoformat(), "kind": kinds.pop() if len(kinds) == 1 else "mixed",
            "who": [p["name"] for p in group], "you_in": any(p["you"] for p in group),
            "km_lo": lo, "km_hi": hi, "when": when,
        })
    together.sort(key=lambda t: t["date"])

    out_members = []
    for p in people:
        grid = []
        for day in days:
            day_runs = p["runs"].get(day.isoformat(), [])
            cell = [{"kind": r["kind"], **({"km": r["km"]} if r["kind"] != "hard" and r["km"] else {})} for r in day_runs]
            if club_day == day and p["club"] and not any(r["kind"] == "hard" for r in day_runs):
                cell = [{"kind": "club", **({"km": club["km"]} if club.get("km") else {})}] + [
                    r for r in cell if r["kind"] == "long"
                ]
            grid.append(cell)
        out_members.append({
            "name": p["name"], "you": p["you"],
            "times": ", ".join(_TIME_WORDS[t] for t in p["times"]) or None,
            "club": p["club"], "updated_on": p["updated_on"], "days": grid,
        })

    return {
        "crew": {"name": crew_name, "code": code, "members": len(people), "you_started_it": owner == me.key},
        "club_run": ({**club, "day_name": _DAY_NAMES[DAYS.index(club["day"])]} if club else None),
        "week": {"starts": days[0].isoformat(), "ends": days[-1].isoformat()},
        "today": today.isoformat(),
        "members": out_members,
        "together": together,
        "group_message": group_message(crew_name, days, today, people, together, club, club_day, code),
    }


def group_message(crew_name: str, days: list[date], today: date, people: list[dict[str, Any]],
                  together: list[dict[str, Any]], club: dict[str, Any] | None, club_day: date | None,
                  code: str) -> str:
    """The crew's week as a message for the group chat people already use.

    The whole calendar, day by day: who is running what, with the runs that
    line up marked, so the group can coordinate without opening Daash.
    """
    first, last = days[0], days[-1]
    span = (f"{first.day} to {last.day} {_MONTHS[last.month - 1]}" if first.month == last.month
            else f"{first.day} {_MONTHS[first.month - 1]} to {last.day} {_MONTHS[last.month - 1]}")
    lines = [f"{crew_name} \u00b7 {span}"]
    matched = {(t["date"]): t for t in together}
    for day in days:
        key = day.isoformat()
        bits: list[str] = []
        if club and club_day == day:
            going = [p["name"] for p in people if p["club"]]
            if going:
                when = _fmt_time(club.get("time"))
                bits.append("club run" + (f" {when}" if when else "") + " (" + ", ".join(going) + ")")
        for p in people:
            for r in p["runs"].get(key, []):
                if club and club_day == day and p["club"] and r["kind"] == "easy":
                    continue
                if r["kind"] == "hard":
                    bits.append(f"{p['name']} hard")
                else:
                    km = f" {_fmt_km(r['km'])}k" if r["km"] else ""
                    bits.append(f"{p['name']} {r['kind']}{km}")
        if not bits:
            continue
        line = f"{_SHORT[day.weekday()]}: " + ", ".join(bits)
        t = matched.get(key)
        if t and day >= today:
            line += " \u2192 together" + (f", {t['when']}" if t.get("when") else "")
        if day < today:
            line = f"{_SHORT[day.weekday()]}: done"
        lines.append(line)
    if len(lines) == 1:
        lines.append("No runs in yet. Check your plan in Daash so it can see your week.")
    lines.append("")
    lines.append(f"In? Say so here. New to the crew? Tell your Daash the code {code}.")
    return "\n".join(lines)
