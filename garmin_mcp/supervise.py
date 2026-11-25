"""Runs the web server and the worker side by side on one machine (Nov 25).

The database is a file on one volume, so both processes have to live on the
same machine. This is the smallest supervisor that does the job:

- starts `python -m garmin_mcp.hosted` (the web process) and
  `python -m garmin_mcp.worker`, both with DAASH_WORKER=separate;
- passes SIGTERM and SIGINT on to both, so a deploy stops them cleanly;
- if the web process ends, stops the worker and exits with the web process's
  code, so Fly restarts the machine exactly as before (the watchdog's
  restart exits 70);
- if the worker ends, starts it again after a pause that grows (5 s up to
  5 min), without touching the web process.

scripts/start.sh runs this (under Litestream when backup is on), unless
GARMIN_MCP_WORKER=inline, which runs the web process alone with the
background jobs back on its own threads.

    python -m garmin_mcp.supervise
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

WORKER_BACKOFF = (5, 15, 60, 300)
STOP_WAIT_S = 20


def _spawn(module: str) -> subprocess.Popen:
    env = {**os.environ, "DAASH_WORKER": "separate"}
    return subprocess.Popen([sys.executable, "-m", module], env=env)


def _stop(proc: subprocess.Popen | None, sig: int = signal.SIGTERM) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(sig)
        proc.wait(timeout=STOP_WAIT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    except ProcessLookupError:
        pass


def main(web_module: str = "garmin_mcp.hosted", worker_module: str = "garmin_mcp.worker",
         poll: float = 1.0) -> int:
    web = _spawn(web_module)
    worker: subprocess.Popen | None = _spawn(worker_module)
    worker_since = time.monotonic()
    restarts = 0
    next_worker_at = 0.0
    stopping = {"sig": None}

    def forward(sig, _frame):
        stopping["sig"] = sig
        for proc in (web, worker):
            if proc is not None and proc.poll() is None:
                try:
                    proc.send_signal(sig)
                except ProcessLookupError:
                    pass

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    while True:
        code = web.poll()
        if code is not None:
            _stop(worker)
            print(f"supervise: web process ended ({code}); stopping", file=sys.stderr, flush=True)
            return code
        if stopping["sig"] is None:
            if worker is not None and worker.poll() is not None:
                if time.monotonic() - worker_since > 600:
                    restarts = 0  # it had been running fine: start again quickly
                wait = WORKER_BACKOFF[min(restarts, len(WORKER_BACKOFF) - 1)]
                print(f"supervise: worker ended ({worker.returncode}); starting it again in {wait}s",
                      file=sys.stderr, flush=True)
                worker, next_worker_at = None, time.monotonic() + wait
                restarts += 1
            if worker is None and time.monotonic() >= next_worker_at:
                worker = _spawn(worker_module)
                worker_since = time.monotonic()
        time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
