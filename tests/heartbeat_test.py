"""server_heartbeat, worker_heartbeat and server_started (hosted.py, worker.py).

    python tests/heartbeat_test.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = tempfile.mkdtemp()
os.environ["GARMIN_MCP_DB"] = str(Path(tmp) / "heartbeat-test.sqlite3")
from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()

from garmin_mcp import store  # noqa: E402

store.DB_PATH = Path(os.environ["GARMIN_MCP_DB"])
from garmin_mcp import analytics, hosted, telemetry, worker  # noqa: E402

failures: list[str] = []
sent: list[tuple[str, str, dict]] = []
analytics.capture = lambda event, subject=None, properties=None, **_: sent.append((event, subject, properties or {}))


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


print("why the server started")
cases = [
    (None, None, "abc", "first start"),
    ("clean", "abc", "abc", "restart"),
    ("clean", "abc", "def", "deploy"),
    ("running", "abc", "def", "deploy"),
    ("running", "abc", "abc", "crash"),
    ("watchdog", "abc", "abc", "watchdog restart"),
    ("watchdog", "abc", "def", "watchdog restart"),
]
for last_exit, last_version, now_version, want in cases:
    got = hosted.start_reason(last_exit, last_version, now_version)
    check(f"last exit {last_exit}, version {last_version}->{now_version}: {want}", got == want, got)

print("\none start after another")
os.environ["GARMIN_MCP_VERSION"] = "sha-1"
first = hosted.note_start()
check("the very first start says so, with the version", first["reason"] == "first start" and first["version"] == "sha-1")
check("and is sent as server_started from daash-server",
      sent[-1][0] == "server_started" and sent[-1][1] == "daash-server" and sent[-1][2]["reason"] == "first start")
again = hosted.note_start()
check("started again without a clean stop: a crash (killed for memory, say)", again["reason"] == "crash")
hosted.note_clean_exit()
check("after a clean stop: a restart", hosted.note_start()["reason"] == "restart")
hosted.note_clean_exit()
os.environ["GARMIN_MCP_VERSION"] = "sha-2"
deploy = hosted.note_start()
check("a new version: a deploy, naming the one before", deploy["reason"] == "deploy" and deploy["previous_version"] == "sha-1")
store.set_setting(hosted.LAST_EXIT, "watchdog")
check("after the watchdog's restart: says so", hosted.note_start()["reason"] == "watchdog restart")

print("\nthe heartbeat")
telemetry.take()
for _ in range(3):
    telemetry.count("tool_calls")
    telemetry.observe("tool_ms", 400)
telemetry.count("tool_errors")
telemetry.observe("tool_ms", 9000)
telemetry.count("cache_hits", 5)
telemetry.count("cache_misses", 2)
telemetry.count("garmin_calls", 7)
telemetry.count("garmin_timeouts")
telemetry.count("garmin_429s", 2)
beat = hosted.heartbeat_once()
want = {"rss_mb", "active_sessions", "pool_queue_depth", "pool_busy_workers", "cache_hits_1m", "cache_misses_1m",
        "garmin_calls_1m", "garmin_timeouts_1m", "garmin_429s_1m", "tool_calls_1m", "tool_errors_1m",
        "p95_tool_ms_1m", "version", "uptime_s"}
check("carries every figure the dashboard reads", want <= set(beat), str(sorted(want - set(beat))))
check("the minute's counts", beat["tool_calls_1m"] == 3 and beat["tool_errors_1m"] == 1 and beat["cache_hits_1m"] == 5
      and beat["cache_misses_1m"] == 2 and beat["garmin_calls_1m"] == 7 and beat["garmin_timeouts_1m"] == 1
      and beat["garmin_429s_1m"] == 2, str(beat))
check("p95 of the minute's tool times, in ms", beat["p95_tool_ms_1m"] == 9000, str(beat["p95_tool_ms_1m"]))
check("memory, version and uptime", isinstance(beat["rss_mb"], int) and beat["version"] == "sha-2"
      and beat["uptime_s"] >= 0)
check("sent as server_heartbeat from daash-server, with no person profile",
      sent[-1][0] == "server_heartbeat" and sent[-1][1] == "daash-server"
      and sent[-1][2]["$process_person_profile"] is False)
nxt = hosted.heartbeat_once()
check("the next minute starts from zero", nxt["tool_calls_1m"] == 0 and nxt["p95_tool_ms_1m"] is None, str(nxt))

print("\nCPU and throttling")
hosted._CPU_LAST[0] = None
first_read = hosted.cpu_minute("cpu  100 0 100 700 0 0 0 100 0 0")
check("the first reading has nothing to compare with", first_read == (None, None))
busy, steal = hosted.cpu_minute("cpu  200 0 200 1300 0 0 0 300 0 0")
check("then the minute's busy and steal shares, in percent", busy == 20.0 and steal == 20.0, f"{busy} {steal}")
check("in the heartbeat", "cpu_pct_1m" in nxt and "cpu_steal_pct_1m" in nxt)

print("\nthe worker's heartbeat")
worker._counts.update(done=4, failed=1)
wb = worker.heartbeat_once()
check("jobs done and failed this minute, and what's queued",
      wb["jobs_done_1m"] == 4 and wb["jobs_failed_1m"] == 1 and "jobs_queued" in wb, str(wb))
check("sent as worker_heartbeat", sent[-1][0] == "worker_heartbeat" and sent[-1][1] == "daash-server")
check("and the counts start again", worker.heartbeat_once()["jobs_done_1m"] == 0)

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
