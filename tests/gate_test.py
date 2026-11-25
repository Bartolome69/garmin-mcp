"""The limits every Garmin call runs under (garmin_mcp/gate.py).

    python tests/gate_test.py
"""

from __future__ import annotations

import contextvars
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from garmin_mcp import telemetry  # noqa: E402
from garmin_mcp.gate import (  # noqa: E402
    BUSY, SLOW, YOURS_BUSY, Gate, GarminBusy, GarminRateLimited, GarminSlow, is_rate_limited,
)

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


class TooMany(Exception):
    pass


TooMany.__name__ = "GarminConnectTooManyRequestsError"


def raised(fn) -> BaseException | None:
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001
        return exc
    return None


print("a call that answers")
g = Gate(workers=4, timeout=2)
check("returns what Garmin gave", g.call("a", lambda: 42) == 42)
check("frees the person's slot afterwards", g._in_flight == {})

print("\na call that hangs")
telemetry.take()
g = Gate(workers=2, timeout=0.3)
started = time.monotonic()
release = threading.Event()
exc = raised(lambda: g.call("a", lambda: release.wait(5)))
took = time.monotonic() - started
check("gives up at the time limit, not when Garmin does", isinstance(exc, GarminSlow) and took < 1.5, f"{took:.2f}s")
check("with a clear message", str(exc) == SLOW, str(exc))
check("and counts it", telemetry.peek("garmin_timeouts") == 1)
check("the person's slot stays held while the hung call still runs", g.requests_in_flight("a") == 1, str(g._in_flight))
release.set()
time.sleep(0.2)
check("and is freed when it ends", g._in_flight == {}, str(g._in_flight))

print("\none person, many calls")
g = Gate(workers=8, timeout=3, per_user=2)
gate_open = threading.Event()
results: list = []


def slow_call() -> None:
    try:
        results.append(g.call("a", lambda: gate_open.wait(2) and "done"))
    except BaseException as exc:  # noqa: BLE001
        results.append(exc)


threads = [threading.Thread(target=slow_call) for _ in range(2)]
for t in threads:
    t.start()
time.sleep(0.1)
check("two calls in flight for one person", g.requests_in_flight("a") == 2, str(g._in_flight))
third = raised(lambda: g.call("a", lambda: "third", timeout=0.3))
check("a third waits for a slot, then is turned away kindly",
      isinstance(third, GarminBusy) and str(third) == YOURS_BUSY, repr(third))
check("someone else isn't held up by them", g.call("b", lambda: "other") == "other")
gate_open.set()
for t in threads:
    t.join()
check("both of the first two finished", results.count("done") == 2, str(results))
check("then the person can call again", g.call("a", lambda: "again") == "again")

print("\none tool's reads side by side")
from garmin_mcp.gate import REQUEST  # noqa: E402

g = Gate(workers=8, timeout=3, per_user=2)
token = REQUEST.set("tool-1")
began = time.monotonic()
outs: list = []
readers = [threading.Thread(target=lambda ctx=contextvars.copy_context(): outs.append(
    ctx.run(g.call, "a", lambda: time.sleep(0.3) or "read"))) for _ in range(5)]
for t in readers:
    t.start()
time.sleep(0.1)
check("five reads of one tool share one of the person's slots", g.requests_in_flight("a") == 1,
      str(g._in_flight))
for t in readers:
    t.join()
REQUEST.reset(token)
check("so they all run at once (about 0.3 s, not 0.9)", outs.count("read") == 5 and time.monotonic() - began < 0.6,
      f"{time.monotonic() - began:.2f}s")

print("\nGarmin says slow down")
telemetry.take()
naps: list[float] = []
g = Gate(workers=2, timeout=2, sleep=naps.append)
tries = {"n": 0}


def limited_twice():
    tries["n"] += 1
    if tries["n"] <= 2:
        raise TooMany("Rate limit exceeded: 429")
    return "ok"


check("retried after backoff, and the third try answers", g.call("a", limited_twice) == "ok")
check("two pauses, growing, with jitter (0.5-1 s, then 1-2 s)",
      len(naps) == 2 and 0.5 <= naps[0] <= 1.0 and 1.0 <= naps[1] <= 2.0, str(naps))
check("each 429 counted", telemetry.peek("garmin_429s") == 2)

naps.clear()
clock = {"t": 1000.0}
g = Gate(workers=2, timeout=2, sleep=naps.append, clock=lambda: clock["t"], cooldown=300)
exc = raised(lambda: g.call("a", lambda: (_ for _ in ()).throw(TooMany("429 too many"))))
check("three 429s in a row: given up, saying so", isinstance(exc, GarminRateLimited) and "rate limit" in str(exc),
      repr(exc))
check("and the person rests five minutes", abs(g.cooling("a") - 300) < 1)
calls = {"n": 0}
exc = raised(lambda: g.call("a", lambda: calls.__setitem__("n", 1)))
check("during the rest, nothing is sent to Garmin", isinstance(exc, GarminRateLimited) and calls["n"] == 0,
      repr(exc))
check("the message says how long", "5 minutes" in str(exc), str(exc))
check("someone else isn't rested", g.call("b", lambda: "fine") == "fine")
clock["t"] += 301
check("after five minutes they can ask again", g.call("a", lambda: "back") == "back")
check("other errors aren't retried", isinstance(raised(lambda: g.call("a", lambda: 1 / 0)), ZeroDivisionError))

print("\nthe pool is swamped")
g = Gate(workers=1, timeout=1, max_queue=2, per_user=10)
hold = threading.Event()
blockers = [threading.Thread(target=lambda: raised(lambda: g.call(f"u{i}", lambda: hold.wait(3)))) for i in range(3)]
for t in blockers:
    t.start()
time.sleep(0.15)
check("one running, two waiting", g.busy_workers() == 1 and g.queue_depth() == 2,
      f"busy {g.busy_workers()} waiting {g.queue_depth()}")
started = time.monotonic()
exc = raised(lambda: g.call("late", lambda: "never"))
check("a fourth is turned away at once, not queued", isinstance(exc, GarminBusy) and str(exc) == BUSY
      and time.monotonic() - started < 0.2, repr(exc))
hold.set()
for t in blockers:
    t.join()
time.sleep(0.1)
check("the queue drains back to nothing", g.queue_depth() == 0 and g.busy_workers() == 0)

print("\ncalls inside calls, and context")
g = Gate(workers=1, timeout=2)
check("a call made from inside a call runs in place (no waiting on itself)",
      g.call("a", lambda: g.call("a", lambda: "inner")) == "inner")
who: contextvars.ContextVar[str] = contextvars.ContextVar("who", default="nobody")
who.set("this request")
check("the caller's context goes with the call", g.call("a", lambda: who.get()) == "this request")

print("\nrecognising a rate limit")
check("garminconnect's TooManyRequests", is_rate_limited(TooMany("x")))
err = RuntimeError("Garmin is limiting how often Daash can ask.")
err.status = 429
check("the official API's refusal", is_rate_limited(err))
check("not a 404", not is_rate_limited(RuntimeError("API Error 404")))

print("\nthrough a Garmin session")
from garmin_mcp.session import GarminError, GarminSession  # noqa: E402
import garmin_mcp.session as session_mod  # noqa: E402

slow = Gate(workers=1, timeout=0.2)
session_mod.gate = slow
s = GarminSession(tokenstore="blob", owner="someone")
s._client = object()
exc = raised(lambda: s.run(lambda c: time.sleep(1)))
check("a slow Garmin reaches the tool as a GarminError with the friendly words",
      isinstance(exc, GarminError) and str(exc) == SLOW, repr(exc))

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
