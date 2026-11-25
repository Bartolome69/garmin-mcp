"""The background jobs, in their own process (Nov 25).

Until now the weekly email, phone notifications, plan checks, the hourly test
sign-in, the keep-alive and the official-history backfill all ran as threads
inside the web server, so a heavy pass competed with people's requests for
the same process. supervise.py now starts this beside the web process on the
same machine (one volume, so one machine); it runs at a lower CPU priority.

Each kind of work is a job in the jobs table (jobs.py). The recurring ones
schedule their own next run when they finish, at the same intervals as the
threads they replace; the web process adds one-off jobs (Garmin pushing new
activities). A dispatcher claims due jobs and runs them on a small pool, never
two of the same type at once. Every job is safe to run twice.

The backfill runs only while the web process's Garmin pool is quiet (the note
it writes every few seconds, hosted.LOAD_NOTE): people waiting on Garmin come
first.

    python -m garmin_mcp.worker
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import jobs

log = logging.getLogger("garmin_mcp.worker")

POLL_S = 1.0
RUNNERS = 8
BACKFILL_IDLE_S = 15
BACKFILL_BUSY_RETRY_S = 30
LOAD_NOTE_FRESH_S = 30
HEARTBEAT_EVERY = 60
# The worker's own memory ceiling: past it the worker stops gracefully and
# supervise.py starts a fresh one; the web process carries on. Lower than the
# web process's 1,400 MB: both share the machine's 2 GB.
WORKER_GUARD_MB = int(os.environ.get("GARMIN_MCP_WORKER_GUARD_MB", "700"))
GUARD_EXIT = 75
_guard_tripped = threading.Event()

_stop = threading.Event()
_running: set[str] = set()
_running_lock = threading.Lock()
_counts = {"done": 0, "failed": 0}
_counts_lock = threading.Lock()


def web_busy(note_path: Any = None, now: float | None = None) -> bool:
    """Whether people are waiting on Garmin in the web process right now."""
    from . import hosted
    path = note_path or hosted.LOAD_NOTE
    try:
        note = json.loads(path.read_text())
    except (OSError, ValueError):
        return False  # no note: the web process isn't saying it's busy
    now = time.time() if now is None else now
    if now - float(note.get("at", 0)) > LOAD_NOTE_FRESH_S:
        return False
    return note.get("queue", 0) > 0 or note.get("busy", 0) >= max(1, int(note.get("workers", 16) * 0.75))


def _backfill(_payload: Any) -> dict[str, Any]:
    from . import backfill
    if web_busy():
        return {"next_in": BACKFILL_BUSY_RETRY_S}
    happened = backfill.run_once()
    if happened is None:
        return {"next_in": BACKFILL_IDLE_S}
    if "wait" in happened:
        return {"next_in": happened["wait"]}
    return {"next_in": happened.get("days", 0) * 60 / backfill.DAYS_PER_MINUTE}


def _signin_check(payload: Any) -> dict[str, Any]:
    from . import hosted
    rechecking = bool((payload or {}).get("rechecking"))
    ok = True  # a check that couldn't run has nothing to confirm
    try:
        ok = hosted.signin_check_once()["ok"]
    except Exception:  # noqa: BLE001
        log.exception("Test sign-in could not run")
    wait = hosted._signin_check_wait(ok, rechecking)
    return {"next_in": wait, "payload": {"rechecking": wait == hosted.SIGNIN_CHECK_RECHECK}}


def _activities_arrived(payload: Any) -> None:
    from . import hosted
    hosted.activities_arrived_job(payload["garmin_user_id"], payload.get("activities") or [])


def recurring() -> dict[str, tuple[float, Callable[[Any], Any], float]]:
    """type -> (every, handler, first run after start). Only what this server runs."""
    from . import analytics, backfill, hosted, official, support_mail

    every: dict[str, tuple[float, Callable[[Any], Any], float]] = {
        "keep_alive": (hosted.KEEP_ALIVE_EVERY, lambda _p: hosted.keep_alive_pass(), 0),
        "notify": (hosted.NOTIFY_EVERY, lambda _p: hosted.notify_pass(), 0),
        "signup_guard": (hosted.GUARD_EVERY, lambda _p: hosted.guard_pass(), 0),
        "plan_checks": (hosted.PLAN_CHECK_EVERY, lambda _p: hosted.plan_checks_pass(), 0),
    }
    if hosted.WEEKLY_EMAIL and support_mail.can_send():
        every["weekly_email"] = (hosted.WEEKLY_EVERY, lambda _p: hosted.weekly_email_pass(), 0)
    if analytics.enabled():
        every["signin_check"] = (hosted.SIGNIN_CHECK_EVERY, _signin_check, hosted.SIGNIN_CHECK_FIRST)
    if official.configured() and backfill.ENABLED:
        every["backfill"] = (BACKFILL_IDLE_S, _backfill, 0)
    return every


ONE_OFF: dict[str, Callable[[Any], Any]] = {"activities_arrived": _activities_arrived}


def schedule(every: dict[str, tuple[float, Callable[[Any], Any], float]], now: float | None = None) -> int:
    """Make sure each recurring job has its next run queued. Returns how many were added."""
    now = time.time() if now is None else now
    return sum(jobs.enqueue(kind, run_at=now + first, dedupe=kind) for kind, (_e, _h, first) in every.items())


def run_job(job: jobs.Job, every: dict[str, tuple[float, Callable[[Any], Any], float]]) -> str:
    """Run one claimed job, record how it went, and queue a recurring one's next run."""
    handler = every[job.type][1] if job.type in every else ONE_OFF.get(job.type)
    started = time.monotonic()
    if handler is None:
        status = jobs.finish(job, False, f"no handler for {job.type}")
        return status
    try:
        result = handler(job.payload)
        status = jobs.finish(job, True)
        with _counts_lock:
            _counts["done"] += 1
    except Exception as exc:  # noqa: BLE001 - one job never stops the others
        log.exception("job %s failed", job.type)
        result = None
        status = jobs.finish(job, False, f"{type(exc).__name__}: {exc}")
        with _counts_lock:
            _counts["failed"] += 1
    if job.type in every and status != "queued":
        interval = every[job.type][0]
        next_in = interval
        payload = None
        if isinstance(result, dict):
            next_in = result.get("next_in", interval)
            payload = result.get("payload")
        jobs.enqueue(job.type, payload, run_at=time.time() + max(0.0, float(next_in)), dedupe=job.type)
    log.debug("job %s %s in %.1fs", job.type, status, time.monotonic() - started)
    return status


def _dispatch(every: dict[str, tuple[float, Callable[[Any], Any], float]], pool: ThreadPoolExecutor) -> bool:
    """Start the next due job if a runner is free. True if one started."""
    with _running_lock:
        if len(_running) >= RUNNERS:
            return False
        busy = set(_running)
    job = jobs.claim(skip=busy)
    if job is None:
        return False
    with _running_lock:
        _running.add(job.type)

    def go() -> None:
        try:
            run_job(job, every)
        finally:
            with _running_lock:
                _running.discard(job.type)

    pool.submit(go)
    return True


def heartbeat_once() -> dict[str, Any]:
    """worker_heartbeat: what the last minute did and what's waiting."""
    from . import analytics
    with _counts_lock:
        done, failed = _counts["done"], _counts["failed"]
        _counts["done"] = _counts["failed"] = 0
    from . import hosted
    rss = None
    try:
        rss = hosted.memory_figures().get("rss_mb")
    except Exception:  # noqa: BLE001
        pass
    props = {"jobs_done_1m": done, "jobs_failed_1m": failed, "jobs_queued": jobs.pending(),
             "rss_mb": rss, "version": hosted.version(), "$process_person_profile": False}
    analytics.capture("worker_heartbeat", "daash-server", props)
    return props


def guard_check(rss_mb: float | None) -> bool:
    """Past the worker's ceiling: stop taking jobs and leave for a fresh start."""
    if rss_mb is None or rss_mb < WORKER_GUARD_MB or _guard_tripped.is_set():
        return False
    from . import analytics
    log.error("worker: memory %s MB is past %s MB; restarting the worker", rss_mb, WORKER_GUARD_MB)
    _guard_tripped.set()
    try:
        analytics.capture_now("memory_guard", "daash-server", {"process": "worker", "rss_mb": rss_mb,
                                                               "$process_person_profile": False})
    except Exception:  # noqa: BLE001
        pass
    _stop.set()
    return True


def _heartbeat_forever() -> None:
    while not _stop.wait(HEARTBEAT_EVERY - (time.time() % HEARTBEAT_EVERY) + 4):
        try:
            beat = heartbeat_once()
            guard_check(beat.get("rss_mb"))
        except Exception:  # noqa: BLE001
            log.exception("worker heartbeat failed")


def main() -> None:
    from . import hosted, official, official_data, backfill, store

    logging.basicConfig(level=logging.INFO)
    for handler in logging.getLogger().handlers:
        handler.addFilter(hosted.RedactUserTokens())
    from . import loadfake
    loadfake.install_if_asked()  # load tests only; refuses in production
    with_nice = os.environ.get("DAASH_WORKER_NICE", "10")
    try:
        os.nice(int(with_nice))  # background work gives way to people's requests
    except (OSError, ValueError):
        pass
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())

    back = jobs.requeue_stale()
    if back:
        log.info("worker: %d job(s) a stopped worker left running are queued again", back)
    jobs.tidy()
    hosted.signup_guard.on_pause = hosted._signups_paused
    if hosted.WEEKLY_EMAIL:
        hosted.weekly_email_seed()
    if official.configured():
        try:
            official_data.tidy()
        except Exception:  # noqa: BLE001
            log.exception("official data tidy failed")
        if backfill.ENABLED:
            try:
                store.reopen_backfills(backfill.VERSION)
                store.queue_backfill_for_all_links()
            except Exception:  # noqa: BLE001
                log.exception("could not queue history for linked accounts")

    every = recurring()
    schedule(every)
    log.info("worker: running %s", ", ".join(sorted(every)))
    threading.Thread(target=_heartbeat_forever, name="worker-heartbeat", daemon=True).start()
    with ThreadPoolExecutor(max_workers=RUNNERS, thread_name_prefix="job") as pool:
        while not _stop.is_set():
            try:
                started = _dispatch(every, pool)
            except Exception:  # noqa: BLE001 - a locked database for a moment, say
                log.exception("worker: dispatch failed")
                started = False
            if not started:
                _stop.wait(POLL_S)
        log.info("worker: stopping; jobs in progress finish first")
    if _guard_tripped.is_set():
        raise SystemExit(GUARD_EXIT)


if __name__ == "__main__":
    main()
