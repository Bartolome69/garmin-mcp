"""Background jobs, kept in SQLite (Nov 25).

The worker process (worker.py) runs these; the web process only adds one-off
jobs (a push from Garmin, say). A job is a row: id, type, payload, run_at,
status (queued, running, done, failed), attempts, last_error. The worker
polls for due rows, claims one at a time, and marks it done, or queued again
with a growing delay, or failed after MAX_ATTEMPTS.

Every job type must be safe to run twice: a worker killed mid-job (a deploy,
a restart) leaves the row "running", and the next worker puts it back in the
queue. The jobs that send things already record what they've sent (claims in
the store), so a second run sends nothing new.

`dedupe` keeps one queued or running row per key: a recurring job's type,
so a restart never doubles the schedule.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

MAX_ATTEMPTS = 5
RETRY_BASE_S = 30
KEEP_DONE_S = 7 * 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    payload     TEXT,
    run_at      INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    dedupe      TEXT,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, run_at);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_pending ON jobs(dedupe)
    WHERE dedupe IS NOT NULL AND status IN ('queued', 'running');
"""

_local = threading.local()
_ready: set[str] = set()


@dataclass
class Job:
    id: int
    type: str
    payload: Any
    attempts: int


def _conn() -> sqlite3.Connection:
    from . import store

    path = str(store.DB_PATH)
    held = getattr(_local, "conns", None)
    if held is None:
        held = _local.conns = {}
    conn = held.get(path)
    if conn is None:
        store.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        if path not in _ready:
            conn.executescript(SCHEMA)
            _ready.add(path)
        held[path] = conn
    return conn


def enqueue(type_: str, payload: Any = None, *, run_at: float | None = None, dedupe: str | None = None) -> bool:
    """Add a job. False when one with the same dedupe key is already pending."""
    now = int(time.time())
    try:
        _conn().execute(
            "INSERT INTO jobs (type, payload, run_at, dedupe, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (type_, json.dumps(payload) if payload is not None else None,
             int(now if run_at is None else run_at), dedupe, now, now))
        return True
    except sqlite3.IntegrityError:
        return False


def claim(now: float | None = None, skip: set[str] | None = None) -> Job | None:
    """Take the next due job (not of a type in skip), marking it running."""
    now = int(time.time() if now is None else now)
    conn = _conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        skip = skip or set()
        marks = ",".join("?" * len(skip))
        row = conn.execute(
            "SELECT id, type, payload, attempts FROM jobs WHERE status = 'queued' AND run_at <= ? "
            + (f"AND type NOT IN ({marks}) " if skip else "")
            + "ORDER BY run_at, id LIMIT 1", (now, *sorted(skip))).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ?",
                     (now, row[0]))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return Job(row[0], row[1], json.loads(row[2]) if row[2] else None, row[3] + 1)


def finish(job: Job, ok: bool, error: str | None = None, now: float | None = None) -> str:
    """Done, queued again for a retry, or failed for good. Returns the new status."""
    now = int(time.time() if now is None else now)
    if ok:
        status, run_at = "done", None
    elif job.attempts < MAX_ATTEMPTS:
        status, run_at = "queued", now + RETRY_BASE_S * (2 ** (job.attempts - 1))
    else:
        status, run_at = "failed", None
    _conn().execute(
        "UPDATE jobs SET status = ?, run_at = COALESCE(?, run_at), last_error = ?, updated_at = ? WHERE id = ?",
        (status, run_at, (error or "")[:500] or None, now, job.id))
    return status


def requeue_stale() -> int:
    """At start: jobs a stopped worker left running go back in the queue."""
    return _conn().execute(
        "UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'", (int(time.time()),)).rowcount


def pending(type_: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM jobs WHERE status = 'queued'"
    return _conn().execute(sql + (" AND type = ?" if type_ else ""), ((type_,) if type_ else ())).fetchone()[0]


def tidy(now: float | None = None) -> int:
    now = int(time.time() if now is None else now)
    return _conn().execute("DELETE FROM jobs WHERE status IN ('done', 'failed') AND updated_at < ?",
                           (now - KEEP_DONE_S,)).rowcount
