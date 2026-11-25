"""Garmin's answers, kept in SQLite (Nov 25).

The answers a session remembered lived in memory and went at every restart
and every idle sweep, so the same reads went back to Garmin again and again.
Now a read of one of the kinds below is answered from the `garmin_cache`
table when it's fresh enough, and only goes to Garmin on a miss:

- an activity by id (its splits, zones, recording, weather, sets): kept for
  good. Its summary (get_activity: name, notes, how it felt) can still be
  edited in the Garmin app for a while, so that one is kept by its date like
  the rest until it's two days old, then for good;
- activity lists, sleep, HRV, readiness, body battery and the day's stats, by
  date: today's for 15 minutes, yesterday's for 6 hours, anything older for 7
  days.

"Today" is anyone's today: a date counts as today until it has ended
everywhere (12:00 UTC the next day), so someone in California late in their
evening isn't served a six-hour-old copy of their own today.

It sits in front of the Garmin client a session hands to each call
(CachedClient), so every caller gets it, background jobs included. Writes
are never cached; after one, the person's affected entries are dropped. Each
payload is compressed and encrypted with the server's key, like everything
else stored about someone. Nothing is cached for a session with no owner
(the single-user local install) or for answers that came from the official
API, which are already in the database.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from . import telemetry

log = logging.getLogger(__name__)

ENABLED = os.environ.get("GARMIN_CACHE", "on") != "off"
TODAY_S = int(os.environ.get("GARMIN_CACHE_TODAY_S", 15 * 60))
YESTERDAY_S = int(os.environ.get("GARMIN_CACHE_YESTERDAY_S", 6 * 3600))
OLDER_S = int(os.environ.get("GARMIN_CACHE_OLDER_S", 7 * 86400))
FOREVER = None
# A recording can be large; past this (compressed) it isn't worth the disk
# and the off-site copy of every write.
MAX_PAYLOAD = 256 * 1024
# Kept-for-good entries nobody has read for this long go, so the table only
# holds what people come back to.
UNREAD_DAYS = 30
PRUNE_EVERY_S = 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS garmin_cache (
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    key         TEXT NOT NULL,
    payload     BLOB NOT NULL,
    fetched_at  INTEGER NOT NULL,
    expires_at  INTEGER,
    read_at     INTEGER NOT NULL,
    PRIMARY KEY (user_id, kind, key)
);
CREATE INDEX IF NOT EXISTS garmin_cache_expires ON garmin_cache(expires_at);
"""

# Client method -> (kind, how its arguments give the key, how they give the date).
# by_id kinds are kept for good; dated kinds expire by the date they're for.
_BY_ID = {
    "get_activity_splits": "activity_splits",
    "get_activity_hr_in_timezones": "activity_hr_zones",
    "get_activity_power_in_timezones": "activity_power_zones",
    "get_activity_details": "activity_recording",
    "get_activity_weather": "activity_weather",
    "get_activity_exercise_sets": "activity_sets",
}
_ONE_DAY = {
    "get_sleep_data": "sleep",
    "get_hrv_data": "hrv",
    "get_training_readiness": "readiness",
    "get_morning_training_readiness": "readiness_morning",
    "get_body_battery_events": "body_battery_events",
    "get_stats": "stats",
    "get_user_summary": "stats",
}
_RANGE = {
    "get_activities_by_date": "activities",
    "get_body_battery": "body_battery",
    "get_hrv_data_range": "hrv_range",
    "get_sleep_daily": "sleep_range",
}
# Kinds a write can change: dropped for the person after any write, and the
# activity ones after update_activity.
AFTER_WRITE = {"calendar", "plan"}
AFTER_ACTIVITY_EDIT = {"activity", "activities", "activities_recent"}

_local = threading.local()
_schema_done: dict[str, bool] = {}
_pruned = [0.0]
_cipher_cache: list[Any] = []


def _db_path() -> str:
    from . import store
    return str(store.DB_PATH)


def _conn():
    import sqlite3

    path = _db_path()
    held = getattr(_local, "conns", None)
    if held is None:
        held = _local.conns = {}
    conn = held.get(path)
    if conn is None:
        conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        if not _schema_done.get(path):
            conn.executescript(SCHEMA)
            _schema_done[path] = True
        held[path] = conn
    return conn


def _cipher():
    if not _cipher_cache:
        from . import store
        _cipher_cache.append(store._cipher())
    return _cipher_cache[0]


def _seal(value: Any) -> bytes | None:
    try:
        raw = json.dumps(value, separators=(",", ":"), default=str).encode()
    except (TypeError, ValueError):
        return None
    sealed = _cipher().encrypt(zlib.compress(raw, 6))
    return sealed if len(sealed) <= MAX_PAYLOAD else None


def _open(blob: bytes) -> Any:
    return json.loads(zlib.decompress(_cipher().decrypt(blob)))


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def ttl_for(day: date | None, now: float | None = None) -> int | None:
    """How long an answer for this date stays fresh, in seconds."""
    now = time.time() if now is None else now
    # Still today somewhere until 12:00 UTC the next day (UTC-12 is last).
    today = (datetime.fromtimestamp(now, timezone.utc) - timedelta(hours=12)).date()
    if day is None or day >= today:
        return TODAY_S
    if day == today - timedelta(days=1):
        return YESTERDAY_S
    return OLDER_S


def _describe(method: str, args: tuple, kwargs: dict) -> tuple[str, str, date | None, bool] | None:
    """(kind, key, date, kept for good) for a cacheable read, or None."""
    flat = list(args) + [kwargs[k] for k in sorted(kwargs)]
    key = "|".join(str(a) for a in flat)
    if method in _BY_ID and flat:
        return _BY_ID[method], key, None, True
    if method == "get_activity" and flat:
        return "activity", key, None, False  # dated by what comes back
    if method in _ONE_DAY and flat:
        return _ONE_DAY[method], key, _day(flat[0]), False
    if method in _RANGE and len(flat) >= 2:
        return _RANGE[method], key, _day(flat[1]), False
    if method == "get_activities":
        return "activities_recent", key, None, False
    return None


def _activity_day(value: Any) -> date | None:
    if isinstance(value, dict):
        summary = value.get("summaryDTO") if isinstance(value.get("summaryDTO"), dict) else {}
        for field in ("startTimeLocal", "startTimeGMT"):
            found = _day(value.get(field) or summary.get(field))
            if found:
                return found
    return None


def get(user_id: str, kind: str, key: str, now: float | None = None) -> tuple[bool, Any]:
    """(found, value) for a fresh entry."""
    now = int(time.time() if now is None else now)
    try:
        row = _conn().execute(
            "SELECT payload, expires_at, read_at FROM garmin_cache WHERE user_id = ? AND kind = ? AND key = ?",
            (user_id, kind, key)).fetchone()
        if row is None or (row[1] is not None and row[1] <= now):
            return False, None
        value = _open(row[0])
        if now - row[2] > 86400:  # noted at most once a day, for pruning
            with _conn() as conn:
                conn.execute("UPDATE garmin_cache SET read_at = ? WHERE user_id = ? AND kind = ? AND key = ?",
                             (now, user_id, kind, key))
        return True, value
    except Exception:  # noqa: BLE001 - the cache never stops a read; Garmin answers instead
        log.warning("cache read failed", exc_info=True)
        return False, None


def put(user_id: str, kind: str, key: str, value: Any, ttl: int | None, now: float | None = None) -> bool:
    now = int(time.time() if now is None else now)
    blob = _seal(value)
    if blob is None:
        return False
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO garmin_cache (user_id, kind, key, payload, fetched_at, expires_at, read_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, kind, key, blob, now, None if ttl is None else now + ttl, now))
        _maybe_prune(now)
        return True
    except Exception:  # noqa: BLE001
        log.warning("cache write failed", exc_info=True)
        return False


def forget(user_id: str, kinds: set[str] | None = None) -> int:
    """Drop a person's entries (of these kinds, or all of them)."""
    try:
        with _conn() as conn:
            if kinds is None:
                return conn.execute("DELETE FROM garmin_cache WHERE user_id = ?", (user_id,)).rowcount
            marks = ",".join("?" * len(kinds))
            return conn.execute(f"DELETE FROM garmin_cache WHERE user_id = ? AND kind IN ({marks})",
                                (user_id, *sorted(kinds))).rowcount
    except Exception:  # noqa: BLE001
        log.warning("cache forget failed", exc_info=True)
        return 0


def clear() -> int:
    """Empty the whole cache (tests, or by hand after a bad read was kept)."""
    with _conn() as conn:
        return conn.execute("DELETE FROM garmin_cache").rowcount


def after_write(user_id: str | None, tool: str) -> None:
    """What a write tool may have changed, dropped for that person."""
    if not user_id:
        return
    kinds = set(AFTER_WRITE)
    if tool == "update_activity":
        kinds |= AFTER_ACTIVITY_EDIT
    forget(user_id, kinds)


def _maybe_prune(now: int) -> None:
    if now - _pruned[0] < PRUNE_EVERY_S:
        return
    _pruned[0] = now
    with _conn() as conn:
        gone = conn.execute(
            "DELETE FROM garmin_cache WHERE (expires_at IS NOT NULL AND expires_at <= ?) "
            "OR (expires_at IS NULL AND read_at < ?)", (now, now - UNREAD_DAYS * 86400)).rowcount
    if gone:
        log.info("Garmin cache: pruned %d entries", gone)


def stats() -> dict[str, int]:
    try:
        rows, size = _conn().execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)), 0) FROM garmin_cache").fetchone()
        return {"rows": rows, "bytes": size}
    except Exception:  # noqa: BLE001
        return {"rows": 0, "bytes": 0}


class CachedClient:
    """A Garmin client whose cacheable reads go through the table first."""

    def __init__(self, client: Any, user_id: str) -> None:
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_user_id", user_id)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if not callable(attr) or not name.startswith("get_"):
            return attr
        return self._wrap(name, attr)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._client, name, value)

    def _wrap(self, name: str, method: Callable[..., Any]) -> Callable[..., Any]:
        def read(*args: Any, **kwargs: Any) -> Any:
            described = _describe(name, args, kwargs)
            if described is None:
                return method(*args, **kwargs)
            kind, key, day, forever = described
            found, value = get(self._user_id, kind, key)
            if found:
                telemetry.count("cache_hits")
                return value
            telemetry.count("cache_misses")
            value = method(*args, **kwargs)
            if forever:
                if value:  # an empty answer may just not be there yet
                    put(self._user_id, kind, key, value, FOREVER)
            elif kind == "activity":
                started = _activity_day(value)
                old = started is not None and started <= date.today() - timedelta(days=2)
                if value:
                    put(self._user_id, kind, key, value, FOREVER if old else ttl_for(started))
            else:
                put(self._user_id, kind, key, value, ttl_for(day))
            return value

        return read


def view(client: Any, user_id: str | None) -> Any:
    """The client a call should use: cached for a person's session, as-is otherwise."""
    if not ENABLED or not user_id or client is None or isinstance(client, CachedClient):
        return client
    return CachedClient(client, user_id)
