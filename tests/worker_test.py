"""Background jobs in their own process: jobs.py, worker.py, supervise.py.

    python tests/worker_test.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

tmp = tempfile.mkdtemp()
os.environ["GARMIN_MCP_DB"] = str(Path(tmp) / "worker-test.sqlite3")
from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()

from garmin_mcp import store  # noqa: E402

store.DB_PATH = Path(os.environ["GARMIN_MCP_DB"])
from garmin_mcp import jobs, worker  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


def rows(sql: str, *args):
    return jobs._conn().execute(sql, args).fetchall()


def wipe() -> None:
    jobs._conn().execute("DELETE FROM jobs")


print("the jobs table")
wipe()
now = time.time()
check("a job goes in", jobs.enqueue("email", {"to": "x"}, run_at=now))
check("one pending per dedupe key: a second is refused",
      jobs.enqueue("notify", dedupe="notify") and not jobs.enqueue("notify", dedupe="notify"))
later = jobs.enqueue("later", run_at=now + 3600)
first = jobs.claim(now + 1)
check("the earliest due job is claimed, payload and all", first is not None and first.type == "email"
      and first.payload == {"to": "x"} and first.attempts == 1, str(first))
check("and marked running", rows("SELECT status FROM jobs WHERE id = ?", first.id) == [("running",)])
check("a job not yet due waits", all(j.type != "later" for j in filter(None, [jobs.claim(now + 1), jobs.claim(now + 1)])))
wipe()
jobs.enqueue("a", run_at=now)
jobs.enqueue("b", run_at=now)
got = jobs.claim(now, skip={"a"})
check("a type already running is skipped, the next one is taken", got is not None and got.type == "b", str(got))

print("\nfailures and retries")
wipe()
jobs.enqueue("flaky", run_at=now)
job = jobs.claim(now)
status = jobs.finish(job, False, "Garmin fell over", now=now)
run_at, err = rows("SELECT run_at, last_error FROM jobs WHERE id = ?", job.id)[0]
check("a failure goes back in the queue, later", status == "queued" and run_at == int(now) + jobs.RETRY_BASE_S
      and err == "Garmin fell over", f"{status} {run_at - int(now)} {err}")
for n in range(2, jobs.MAX_ATTEMPTS + 1):
    job = jobs.claim(run_at + 1)
    status = jobs.finish(job, False, "again", now=run_at)
    run_at = rows("SELECT run_at FROM jobs WHERE id = ?", job.id)[0][0]
check(f"after {jobs.MAX_ATTEMPTS} attempts it's failed for good", status == "failed" and job.attempts == jobs.MAX_ATTEMPTS,
      f"{status} {job.attempts}")

print("\na worker that stopped mid-job")
wipe()
jobs.enqueue("weekly_email", run_at=now, dedupe="weekly_email")
jobs.claim(now)
check("its job is running", rows("SELECT status FROM jobs")[0][0] == "running")
check("the next worker puts it back in the queue (every job is safe to run twice)",
      jobs.requeue_stale() == 1 and rows("SELECT status FROM jobs")[0][0] == "queued")

print("\nrecurring jobs")
wipe()
calls: list[str] = []
every = {
    "notify": (180, lambda p: calls.append("notify"), 0),
    "signin_check": (3600, lambda p: calls.append("signin") or {"next_in": 900, "payload": {"rechecking": True}}, 600),
    "boom": (60, lambda p: (_ for _ in ()).throw(RuntimeError("down")), 0),
}
check("each is queued once at start", worker.schedule(every, now) == 3)
check("and a restart doesn't double them", worker.schedule(every, now) == 0
      and rows("SELECT COUNT(*) FROM jobs")[0][0] == 3)
check("a first run can wait (the test sign-in, 10 minutes after a deploy)",
      rows("SELECT run_at FROM jobs WHERE type = 'signin_check'")[0][0] == int(now) + 600)
job = jobs.claim(now + 1, skip={"boom"})
worker.run_job(job, every)
nxt = rows("SELECT run_at, status FROM jobs WHERE type = 'notify' ORDER BY id DESC LIMIT 1")[0]
check("a finished job queues its next run at its interval", calls == ["notify"] and nxt[1] == "queued"
      and 170 <= nxt[0] - time.time() <= 181, str(nxt))
job = jobs.claim(now + 601, skip={"boom", "notify"})
worker.run_job(job, every)
nxt = rows("SELECT run_at, payload FROM jobs WHERE type = 'signin_check' AND status = 'queued'")[0]
check("or when it says, carrying what it needs to remember", 890 <= nxt[0] - time.time() <= 901
      and json.loads(nxt[1]) == {"rechecking": True}, str(nxt))
job = jobs.claim(now + 1, skip={"notify", "signin_check"})
status = worker.run_job(job, every)
pending = rows("SELECT COUNT(*) FROM jobs WHERE type = 'boom' AND status = 'queued'")[0][0]
check("a failing job is retried, not doubled by its schedule", status == "queued" and pending == 1, f"{status} {pending}")

print("\nGarmin's push, handed to the worker")
from garmin_mcp import hosted  # noqa: E402

wipe()
hosted._enqueue_activities_arrived("garmin-1", [{"summaryId": "a1"}])
job = jobs.claim()
seen: list = []
real = hosted.activities_arrived_job
hosted.activities_arrived_job = lambda g, a: seen.append((g, a))
try:
    worker.run_job(job, {})
    worker.run_job(job, {})  # a second run, as after a restart mid-job
finally:
    hosted.activities_arrived_job = real
check("the web process only queues it; the worker runs it", seen and seen[0] == ("garmin-1", [{"summaryId": "a1"}]),
      str(seen))
check("running it twice is harmless here: the email and the phone check record what they sent",
      len(seen) == 2)

print("\nthe backfill gives way to people")
note = Path(tmp) / "load.json"
note.write_text(json.dumps({"queue": 3, "busy": 16, "workers": 16, "at": time.time()}))
check("people waiting on Garmin: busy", worker.web_busy(note))
note.write_text(json.dumps({"queue": 0, "busy": 2, "workers": 16, "at": time.time()}))
check("a quiet pool: not busy", not worker.web_busy(note))
note.write_text(json.dumps({"queue": 9, "busy": 16, "workers": 16, "at": time.time() - 120}))
check("an old note says nothing", not worker.web_busy(note))
check("no note at all: not busy", not worker.web_busy(Path(tmp) / "missing.json"))
real_busy = worker.web_busy
worker.web_busy = lambda *_a, **_k: True
try:
    asked = worker._backfill(None)
finally:
    worker.web_busy = real_busy
check("while busy, the backfill doesn't ask Garmin and tries again in 30 s", asked == {"next_in": 30}, str(asked))

print("\nthe web process with a worker beside it")
started: list[str] = []
saved = (hosted._start_background_threads, hosted.refuse_wrong_key)
hosted._start_background_threads = lambda: started.append("threads")
hosted.refuse_wrong_key = lambda: None
fake_uvicorn = type(sys)("uvicorn")
fake_uvicorn.run = lambda *a, **k: started.append("serving")
sys.modules["uvicorn"] = fake_uvicorn
os.environ["GARMIN_MCP_WATCHDOG"] = "off"
try:
    os.environ["DAASH_WORKER"] = "separate"
    hosted.main()
    check("runs no background jobs of its own, only answers requests", started == ["serving"], str(started))
    started.clear()
    os.environ.pop("DAASH_WORKER")
    hosted.main()
    check("without one (GARMIN_MCP_WORKER=inline), the jobs run on its threads as before",
          started == ["threads", "serving"], str(started))
finally:
    hosted._start_background_threads, hosted.refuse_wrong_key = saved

print("\nthe supervisor")
fake = Path(tmp) / "fakes"
(fake / "fakepkg").mkdir(parents=True)
(fake / "fakepkg" / "__init__.py").write_text("")
log = Path(tmp) / "events"
(fake / "fakepkg" / "web.py").write_text(textwrap.dedent(f"""
    import os, signal, sys, time
    open({str(log)!r}, "a").write(f"web {{os.environ.get('DAASH_WORKER')}}\\n")
    signal.signal(signal.SIGTERM, lambda *a: (open({str(log)!r}, "a").write("web got TERM\\n"), sys.exit(0)))
    end = float(os.environ.get("WEB_ENDS_AFTER", "60"))
    time.sleep(end)
    sys.exit(70)
"""))
(fake / "fakepkg" / "crashy.py").write_text(textwrap.dedent(f"""
    open({str(log)!r}, "a").write("worker started\\n")
    raise SystemExit(1)
"""))
(fake / "fakepkg" / "calm.py").write_text(textwrap.dedent(f"""
    import signal, sys, time
    signal.signal(signal.SIGTERM, lambda *a: (open({str(log)!r}, "a").write("worker got TERM\\n"), sys.exit(0)))
    time.sleep(60)
"""))
runner = textwrap.dedent(f"""
    import sys
    sys.path[:0] = [{str(fake)!r}, {str(ROOT)!r}]
    from garmin_mcp import supervise
    supervise.WORKER_BACKOFF = (0.2,)
    sys.exit(supervise.main(sys.argv[1], sys.argv[2], poll=0.05))
""")
env = {**os.environ, "WEB_ENDS_AFTER": "1.5", "PYTHONPATH": f"{fake}:{ROOT}"}
log.write_text("")
done = subprocess.run([sys.executable, "-c", runner, "fakepkg.web", "fakepkg.crashy"], env=env, timeout=30)
events = log.read_text().splitlines()
check("both run with DAASH_WORKER=separate", "web separate" in events, str(events))
check("a worker that dies is started again, the web process untouched",
      events.count("worker started") >= 3 and events.count("web separate") == 1, str(events))
check("when the web process ends, the supervisor ends with its code (the watchdog's 70)", done.returncode == 70,
      str(done.returncode))

log.write_text("")
env["WEB_ENDS_AFTER"] = "60"
proc = subprocess.Popen([sys.executable, "-c", runner, "fakepkg.web", "fakepkg.calm"], env=env)
time.sleep(1.5)
proc.send_signal(signal.SIGTERM)
code = proc.wait(timeout=30)
events = log.read_text().splitlines()
check("a deploy's SIGTERM reaches both, and everything stops", "web got TERM" in events and "worker got TERM" in events
      and code == 0, f"{events} {code}")

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
