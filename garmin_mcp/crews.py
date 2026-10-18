"""Daash Crews: a few friends who see each other's easy and long runs.

Each member's Daash already knows their week. A crew pools the part of it that
suits company (easy runs, long runs and the club's standing run) so Daash can
point out who else is running a similar run on the same day, or the day next
to it, and suggest doing it together.

Only the hosted server has crews: it alone knows who is asking. It sets the
member for each request here, the same way it binds the Garmin session, and
every function below works on that member. Locally nobody is set and the crew
tools stay hidden.

What is kept, and only for members, is the minimum a crew card needs: the
crew, each member's chosen name, rough running times, whether they go to the
club run and their easy pace, and the days, kinds and distances of their easy
and long runs for the next two weeks. A hard session is kept as "hard" on its
day and nothing more. Runs that have been done, and anything else from Garmin,
are never kept. Days drop off once they have passed, and leaving the crew or
disconnecting deletes the lot.
"""

from __future__ import annotations

import json
import re
import secrets
import statistics
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from garmin_mcp import store

MAX_MEMBERS = 20
# How far ahead a member's runs are kept, counted from today.
HORIZON_DAYS = 14
# Easy paces this close (seconds per km) run together comfortably: the faster
# runner eases off, which on an easy day is no loss.
PACE_GAP = 30

TIMES = (
    "weekday_morning", "weekday_lunch", "weekday_evening",
    "weekend_morning", "weekend_afternoon", "weekend_evening",
)
_TIME_WORDS = {
    "weekday_morning": "weekday mornings", "weekday_lunch": "weekday lunchtimes",
    "weekday_evening": "weekday evenings", "weekend_morning": "weekend mornings",
    "weekend_afternoon": "weekend afternoons", "weekend_evening": "weekend evenings",
}
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
    pace_s      INTEGER,                    -- easy pace, seconds per km
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

def _connect():
    conn = store._connect()
    conn.executescript(SCHEMA)
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

def _seconds(text: Any) -> int | None:
    """'38m 30s', '1h 02m', '2:05:10' or '38:30' as seconds."""
    if not isinstance(text, str):
        return None
    parts = dict((unit, int(n)) for n, unit in re.findall(r"(\d+)\s*([hms])", text))
    if parts:
        return parts.get("h", 0) * 3600 + parts.get("m", 0) * 60 + parts.get("s", 0)
    clock = re.fullmatch(r"\s*(?:(\d+):)?(\d+):(\d{2})\s*", text)
    if clock:
        h, m, s = clock.groups()
        return int(h or 0) * 3600 + int(m) * 60 + int(s)
    return None


def _pace_seconds(text: Any) -> int | None:
    """'5:48 /km' or '5:48/km' as seconds per km."""
    if not isinstance(text, str):
        return None
    found = re.match(r"\s*(\d+):(\d{2})\s*/\s*km", text)
    return int(found.group(1)) * 60 + int(found.group(2)) if found else None


def _is_run(session: dict[str, Any]) -> bool:
    sport = session.get("sport") or "run"
    kind = session.get("kind") or "run"
    return sport == "run" and kind == "run" and session.get("type") not in _NOT_RUNS


def _kind(session: dict[str, Any]) -> str:
    kind = session.get("type")
    if kind in HARD:
        return "hard"
    return "long" if kind == "long" else "easy"


def from_plan(result: Any, today: date) -> tuple[set[str], list[tuple[str, str, float | None]], int | None]:
    """What a crew needs from a get_plan or create_plan answer.

    Returns the days the answer covers (within the next two weeks), the runs
    ahead on those days as (day, kind, km), and the member's easy pace from the
    easy runs already done in it, or None when there are none to go on.
    """
    if not isinstance(result, dict) or not isinstance(result.get("weeks"), list):
        return set(), [], None
    first, last = today, today + timedelta(days=HORIZON_DAYS - 1)
    covered: set[str] = set()
    runs: list[tuple[str, str, float | None]] = []
    paces: list[int] = []
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
                if _kind(session) != "hard":
                    pace = _pace_seconds(session.get("actual_pace") or session.get("pace"))
                    km, secs = session.get("actual_km"), _seconds(session.get("actual"))
                    if pace is None and isinstance(km, (int, float)) and km >= 3 and secs:
                        pace = round(secs / km)
                    if pace and 150 <= pace <= 600:
                        paces.append(pace)
                continue
            if status == "missed" or day_text not in covered:
                continue
            kind = _kind(session)
            km = session.get("planned_km")
            runs.append((day_text, kind, round(float(km), 1) if isinstance(km, (int, float)) and kind != "hard" else None))
    pace = round(statistics.median(paces) / 5) * 5 if paces else None
    return covered, runs, pace


def capture(key: str, result: Any, today: date | None = None) -> bool:
    """Keep a member's coming runs from a plan answer they were just shown.

    Only for someone in a crew, and only the days that answer covers: a plan
    that ends next week leaves the week after as it was. Returns whether
    anything was kept.
    """
    today = today or date.today()
    covered, runs, pace = from_plan(result, today)
    if not covered:
        return False
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM crew_members WHERE member = ?", (key,)).fetchone() is None:
            return False
        conn.execute("DELETE FROM crew_runs WHERE day < ?", (today.isoformat(),))
        conn.executemany("DELETE FROM crew_runs WHERE member = ? AND day = ?", [(key, d) for d in covered])
        conn.executemany(
            "INSERT INTO crew_runs (member, day, kind, km) VALUES (?, ?, ?, ?)",
            [(key, d, k, km) for d, k, km in runs],
        )
        conn.execute(
            "UPDATE crew_members SET updated_on = ?, pace_s = COALESCE(?, pace_s) WHERE member = ?",
            (today.isoformat(), pace, key),
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
    wanted = [str(t).strip().lower().replace(" ", "_").replace("-", "_") for t in (times or [])]
    unknown = [t for t in wanted if t not in TIMES]
    if unknown:
        raise CrewError(f"Usual times are any of {', '.join(TIMES)}; not {', '.join(unknown)}.")
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
    crew_name = _clean_name(crew_name, "the crew is called", 40)
    name = _clean_name(name, "the crew calls you", 24)
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

def _fmt_pace(seconds: int | None) -> str | None:
    return f"{seconds // 60}:{seconds % 60:02d} /km" if seconds else None


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


_SLOTS = ("morning", "lunch", "afternoon", "evening")


def _best_group(day: date, people: list[dict[str, Any]], must: dict[str, Any] | None = None
                ) -> tuple[list[dict[str, Any]], str | None]:
    """The biggest set of these people who can run together that day.

    Together means a usual time they share (someone who hasn't said when they
    run fits any) and easy paces within PACE_GAP (an unknown pace fits any).
    Someone who can't make it drops out rather than sinking the rest. With
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
        unknown = [p for p in free if not p["pace_s"]]
        known = sorted((p for p in free if p["pace_s"]), key=lambda p: p["pace_s"])
        windows = [known[i:j] for i in range(len(known)) for j in range(i + 1, len(known) + 1)
                   if known[j - 1]["pace_s"] - known[i]["pace_s"] <= PACE_GAP] or [[]]
        for window in windows:
            group = window + unknown
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
    anchor = week_of or today
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
            "SELECT member, name, times, club, pace_s, updated_on FROM crew_members WHERE crew_id = ? ORDER BY joined_at",
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
    for key, name, times, goes, pace_s, updated_on in members:
        people.append({
            "key": key, "name": name, "you": key == me.key, "times": json.loads(times) if times else [],
            "club": bool(goes), "pace_s": pace_s, "updated_on": updated_on, "runs": {},
        })
    by_key = {p["key"]: p for p in people}
    for key, day_text, kind, km in runs:
        by_key[key]["runs"].setdefault(day_text, []).append({"kind": kind, "km": km})

    def social(person: dict[str, Any], day: date) -> list[dict[str, Any]]:
        return [r for r in person["runs"].get(day.isoformat(), []) if r["kind"] in ("easy", "long")]

    def hard(person: dict[str, Any], day: date) -> bool:
        return any(r["kind"] == "hard" for r in person["runs"].get(day.isoformat(), []))

    together: list[dict[str, Any]] = []
    # The club run comes first, every week, whoever has said they go.
    if club and club_day and club_day >= today:
        going = [p for p in people if p["club"]]
        together.append({
            "date": club_day.isoformat(), "kind": "club", "who": [p["name"] for p in going],
            "you_in": any(p["you"] for p in going), "km_lo": club.get("km"), "km_hi": club.get("km"),
            "when": _fmt_time(club.get("time")), "place": club.get("place"),
        })

    # Same day, same kind of run, paces and times that meet.
    for day in days:
        if day < today:
            continue
        for kind in ("easy", "long"):
            group, when = _best_group(day, [
                p for p in people
                if any(r["kind"] == kind for r in social(p, day))
                and not (club_day == day and p["club"] and kind == "easy")
            ])
            if not group:
                continue
            lo, hi = _km_range([r["km"] for p in group for r in social(p, day) if r["kind"] == kind])
            together.append({
                "date": day.isoformat(), "kind": kind, "who": [p["name"] for p in group],
                "you_in": any(p["you"] for p in group), "km_lo": lo, "km_hi": hi, "when": when,
            })

    # Your run a day away from others doing the same kind: move it to join them.
    me_person = by_key[me.key]
    for day in days:
        if day < today:
            continue
        for run in social(me_person, day):
            if club_day == day and me_person["club"]:
                continue
            # A run that already has company stays where it is.
            if any(t["date"] == day.isoformat() and t["kind"] == run["kind"] and t["you_in"] for t in together):
                continue
            for shift in (-1, 1):
                target = day + timedelta(days=shift)
                if target < today or not (days[0] <= target <= days[-1]):
                    continue
                if me_person["runs"].get(target.isoformat()) or hard(me_person, target):
                    continue
                # A long run never lands the day before your own hard session.
                if run["kind"] == "long" and hard(me_person, target + timedelta(days=1)):
                    continue
                group, when = _best_group(target, [me_person] + [
                    p for p in people if not p["you"]
                    and any(r["kind"] == run["kind"] for r in social(p, target))
                ], must=me_person)
                if not group:
                    continue
                others = [p for p in group if not p["you"]]
                lo, hi = _km_range([r["km"] for p in others for r in social(p, target) if r["kind"] == run["kind"]] + [run["km"]])
                # Others already running together that day: that run gains
                # the offer to join it, rather than appearing twice.
                there = next((t for t in together if t["date"] == target.isoformat() and t["kind"] == run["kind"]
                              and not t.get("move_from")), None)
                if there is not None:
                    there.update({"move_from": day.isoformat(), "km_lo": lo, "km_hi": hi, "when": there["when"] or when})
                    break
                together.append({
                    "date": target.isoformat(), "kind": run["kind"], "who": [p["name"] for p in others],
                    "you_in": False, "km_lo": lo, "km_hi": hi, "when": when,
                    "move_from": day.isoformat(),
                })
                break
    together.sort(key=lambda t: (t["date"], t["kind"] != "club"))

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
            "name": p["name"], "you": p["you"], "pace": _fmt_pace(p["pace_s"]),
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
        "group_message": group_message(crew_name, together, club, code),
    }


def _together_line(t: dict[str, Any], club: dict[str, Any] | None) -> str:
    d = date.fromisoformat(t["date"])
    lo, hi = t.get("km_lo"), t.get("km_hi")
    km = f"{_fmt_km(lo)} km" if lo and (hi is None or hi == lo) else (f"{_fmt_km(lo)} to {_fmt_km(hi)} km" if lo else "")
    if t["kind"] == "club":
        what = " ".join(x for x in ("club run", km) if x)
        if club and club.get("place"):
            what += f" from {club['place']}"
    else:
        what = " ".join(x for x in (f"{t['kind']} run", km) if x)
    when = t.get("when")
    head = f"{_SHORT[d.weekday()]}" + (f" {when}" if when else "")
    who = ", ".join(t["who"]) if t["who"] else "nobody yet"
    return f"{head} · {what} · {who}"


def group_message(crew_name: str, together: list[dict[str, Any]], club: dict[str, Any] | None, code: str) -> str:
    """The week's crew runs as a message for the group chat people already use."""
    lines = [f"This week's {crew_name} runs"]
    # Runs two or more are already doing, and the club run: an offer to one
    # person to move their run is theirs, not the group's.
    shown = [t for t in together if t["kind"] == "club" or len(t["who"]) >= 2]
    if not shown:
        lines.append("Nothing matched up yet. Check your plan in Daash so it can see your week.")
    for t in shown:
        lines.append(_together_line(t, club))
    lines.append("")
    lines.append(f"In? Tell your Daash. New to the crew? The code is {code}.")
    return "\n".join(lines)
