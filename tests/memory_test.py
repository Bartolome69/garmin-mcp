"""Memory: bounded maps, sessions let go of their clients, the alert, the profile and the guard.

    python tests/memory_test.py
"""

from __future__ import annotations

import gc
import os
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = tempfile.mkdtemp()
os.environ["GARMIN_MCP_DB"] = str(Path(tmp) / "memory-test.sqlite3")
from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()

from garmin_mcp import store  # noqa: E402

store.DB_PATH = Path(os.environ["GARMIN_MCP_DB"])
from garmin_mcp import analytics, hosted, memwatch, support_mail  # noqa: E402
from garmin_mcp import session as session_mod  # noqa: E402
from garmin_mcp.bounded import BoundedDict, BoundedSet  # noqa: E402

failures: list[str] = []
sent: list[tuple] = []
emails: list[tuple[str, str]] = []
analytics.capture = lambda event, subject=None, properties=None, **_: sent.append((event, properties or {}))
analytics.capture_now = lambda event, subject=None, properties=None, **_: sent.append((event, properties or {}))
support_mail.send_alert = lambda subject, text: emails.append((subject, text)) or True


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


print("maps with a ceiling")
d = BoundedDict(3)
for i in range(5):
    d[f"k{i}"] = i
check("past the limit, the entry written longest ago goes", list(d) == ["k2", "k3", "k4"], str(list(d)))
d["k2"] = 99
d["k5"] = 5
check("writing an entry again keeps it", list(d) == ["k4", "k2", "k5"], str(list(d)))
d.setdefault("k6", []).append(1)
check("setdefault counts as a write", "k6" in d and len(d) == 3 and d["k6"] == [1], str(dict(d)))
d.update({"a": 1, "b": 2})
check("update too", len(d) == 3 and list(d)[-2:] == ["a", "b"])
s = BoundedSet(2)
for x in "abc":
    s.add(x)
check("a set with a ceiling", "a" not in s and "b" in s and "c" in s and len(s) == 2)
check("the server's per-person maps all have one", all(
    isinstance(m, BoundedDict) for m in (hosted._CALLS, hosted._TOUCHED, hosted._PLAN_CALLS, hosted._HELD,
                                         hosted._ATTEMPTS, analytics._FLAGS, analytics._LINKED)))

print("\nsessions let go of their Garmin clients")


class Http:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class Inner:
    def __init__(self):
        self._api_session, self.cs = Http(), Http()

    def dumps(self):
        return "blob"


class FakeClient:
    def __init__(self):
        self.client = Inner()

    def login(self, tokenstore=None):
        return None, None

    def get_user_profile(self):
        return {"ok": True}


made: list[FakeClient] = []


def build(**_):
    c = FakeClient()
    made.append(c)
    session_mod._LIVE_CLIENTS.add(c)
    return c


session_mod.build_client = build
store.save_user("a***@example.com", "blob", "tok-a" * 4, email_hash="fp-a")
hosted._SESSIONS.clear()
hosted._SESSION_USED.clear()
gs = hosted.session_for("tok-a" * 4)
gs.run(lambda c: c.get_user_profile())
check("a live client is counted", session_mod.live_clients() >= 1)
hosted._SESSION_USED["tok-a" * 4] = time.monotonic() - hosted.SESSION_IDLE_S - 1
hosted.sweep_sessions()
client = made[-1]
check("the idle sweep closes its connections and its sign-in session",
      client.client._api_session.closed and client.client.cs.closed)
del gs, client, made[:]
gc.collect()
check("and the client is gone from memory once nothing holds it", session_mod.live_clients() == 0,
      str(session_mod.live_clients()))

saved_max = hosted.SESSIONS_MAX
hosted.SESSIONS_MAX = 3
for i in range(5):
    hosted.session_for(f"tok-{i}" * 4)
    time.sleep(0.001)
check("past the ceiling, the session used longest ago goes",
      len(hosted._SESSIONS) == 3 and f"tok-0" * 4 not in hosted._SESSIONS and f"tok-4" * 4 in hosted._SESSIONS,
      str(len(hosted._SESSIONS)))
hosted.SESSIONS_MAX = saved_max

gs2 = session_mod.GarminSession(tokenstore="blob", owner="x")
gs2._client = FakeClient()
for i in range(session_mod.MEMO_LIMIT + 50):
    gs2.remember(("k", i), i)
check("remembered answers stop at the limit, oldest first, not all at once",
      len(gs2._memo) == session_mod.MEMO_LIMIT and ("k", 0) not in gs2._memo
      and ("k", session_mod.MEMO_LIMIT + 49) in gs2._memo, str(len(gs2._memo)))

print("\nthe memory email")
w = memwatch.Watch(warn_mb=1200, warn_every=7200, guard_mb=1400)
check("below the line: nothing", not w.should_warn(900, 0))
check("one reading at the line: not yet (could be a blip)", not w.should_warn(1210, 40))
check("two heartbeats in a row at the line: an email", w.should_warn(1210, 100))
check("still high an hour later: nothing yet", not w.should_warn(1300, 3700))
check("still high two hours later: another", w.should_warn(1300, 7300))
check("back down, then up for two readings: at once (a new episode)",
      not w.should_warn(800, 7400) and not w.should_warn(1250, 7460) and w.should_warn(1250, 7520))

print("\nthe latency email")
lw = memwatch.LatencyWatch(limit_ms=15000, min_calls=30, every=7200)
for i in range(40):
    lw.note(2000, now=1000 + i)
check("a normal hour: nothing", lw.should_warn(1100)[0] is False)
for i in range(10):
    lw.note(40000, now=1200 + i)
fired, p95, n = lw.should_warn(1300)
check("p95 over 15 s across the hour: an email", fired and p95 == 40000 and n == 50, f"{fired} {p95} {n}")
check("not again within 2 hours", lw.should_warn(1360)[0] is False)
check("calls older than an hour fall out", lw.p95(1000 + 3600 + 300)[1] == 0)
lw2 = memwatch.LatencyWatch(limit_ms=15000, min_calls=30)
for i in range(5):
    lw2.note(60000, now=1000 + i)
check("too few calls to say (one slow chat at night): nothing", lw2.should_warn(1100)[0] is False)
check("the guard is at 1,400", not w.should_restart(1399) and w.should_restart(1400))

print("\nthe profile")
p = memwatch.Watch(profile_mb=600, profile_every=1800)
was_tracing = tracemalloc.is_tracing()
check("below 600 MB: off", p.profile(400, 0) is None and not tracemalloc.is_tracing())
check("above it: tracing starts, first look taken", p.profile(700, 10) is None and tracemalloc.is_tracing())
hoard = [bytearray(1024) for _ in range(3000)]  # about 3 MB that stays
check("between looks: nothing", p.profile(700, 900) is None)
top = p.profile(720, 1820)
check("30 minutes on: the 20 places that grew most, with file and line",
      bool(top) and len(top) <= 20 and any("memory_test.py" in line for line in top), str((top or [])[:2]))
p.profile(300, 2000)
check("well back down: tracing stops again", not tracemalloc.is_tracing())
del hoard

print("\nthe minute's check in the server")
emails.clear()
sent.clear()
hosted._WATCH = memwatch.Watch(warn_mb=1200, warn_every=7200, guard_mb=1400, profile_mb=10**6)
analytics.enabled = lambda: True
hosted.vitals_once(now=940, figures={"rss_mb": 1250, "machine_available_mb": 500, "machine_total_mb": 1971})
beat = hosted.vitals_once(now=1000, figures={"rss_mb": 1250, "machine_available_mb": 500, "machine_total_mb": 1971})
check("high memory emails, and says it repeats every 2 hours",
      len(emails) == 1 and "every 2 hours" in emails[0][1] and "1250 MB" in emails[0][0], str(emails[:1]))
check("the heartbeat carries free memory, live Garmin clients and the cache's size",
      beat.get("free_mb") == 500 and "garmin_clients_live" in beat and "cache_entries" in beat, str(beat))
hosted.vitals_once(now=1060, figures={"rss_mb": 1260})
check("a minute later: no second email", len(emails) == 1)

killed: list[int] = []
real_kill, real_timer = os.kill, hosted.threading.Timer
hosted.os.kill = lambda pid, sig: killed.append(sig)


class NoTimer:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


hosted.threading.Timer = NoTimer
try:
    hosted.vitals_once(now=1120, figures={"rss_mb": 1450})
finally:
    hosted.os.kill, hosted.threading.Timer = real_kill, real_timer
import signal  # noqa: E402

check("past 1,400 MB: a graceful stop is asked for (SIGTERM to itself)", killed == [signal.SIGTERM], str(killed))
check("with an email saying it restarted itself", any("restarted itself" in s for s, _ in emails))
check("recorded so the next start says memory_guard", store.get_setting(hosted.LAST_EXIT) == "memory_guard")
check("the next start's server_started says memory_guard", hosted.note_start()["reason"] == "memory_guard")
check("a watchdog restart still says so", hosted.start_reason("watchdog", "a", "a") == "watchdog restart")
hosted._GUARD_TRIPPED.clear()

print("\nthe worker's ceiling")
from garmin_mcp import worker  # noqa: E402

check("below 700 MB: carries on", not worker.guard_check(500) and not worker._stop.is_set())
check("past it: stops taking jobs so supervise.py starts a fresh one",
      worker.guard_check(720) and worker._stop.is_set())

print("\ntool errors in the heartbeat")
from garmin_mcp import telemetry  # noqa: E402

telemetry.take()
check("a conflict isn't an error", hosted.error_category("That workout is already on the calendar") == "conflict")

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
