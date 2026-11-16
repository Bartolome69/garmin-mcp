"""Tool calls as traces for PostHog's AI observability (tracing.py).

What is sent, what never is, which calls are chosen, and that a Garmin request
lands in the call it was made for, including from another thread. Nothing
leaves the test: PostHog's endpoint is replaced with a list.

    .venv/bin/python tests/tracing_test.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(os.environ.get("TMPDIR", "/tmp")) / "garmin-tracing-test"
SCRATCH.mkdir(parents=True, exist_ok=True)
os.environ["GARMIN_MCP_DB"] = str(SCRATCH / f"test-{int(time.time() * 1000)}.sqlite3")
os.environ.setdefault("GARMIN_MCP_SECRET", "tracing-test-secret")
os.environ.pop("ANALYTICS_INTERNAL_IDS", None)

import requests  # noqa: E402

from garmin_mcp import analytics, hooks, official, tracing  # noqa: E402
from garmin_mcp import server as garmin_server  # noqa: E402

SENT: list[dict] = []
analytics.KEY = "phc_test"
analytics._post = SENT.append
analytics._friends = lambda: frozenset()


class _Now:
    """analytics' threads, run where they're started, so SENT is complete at once."""

    def __init__(self, target, args=(), daemon=None, **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class _Threading:
    Thread = _Now


analytics.threading = _Threading  # type: ignore[assignment]  # analytics' only, not everyone's

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"\n     {detail}"))
    if not ok:
        failures.append(name)


def traced_call(tool: str, requests_made: list[tuple], *, ok=True, error_kind=None, seconds=0.4,
                subject="fp-person-1", app="Claude"):
    """One tool call as the wrapper and the hosted hook make it."""
    token = tracing.start(tool)
    try:
        for method, url, status, took in requests_made:
            tracing.note_request(method, url, status, took)
        return tracing.report(subject, ok=ok, error_kind=error_kind, seconds=seconds, app=app,
                              properties={"client": app, "login_route": "password"})
    finally:
        tracing.finish(token)


def main() -> int:
    # --- Paths: kept short, nothing that names a person or a thing ---------
    cases = [
        ("https://connectapi.garmin.com/usersummary-service/usersummary/daily/bart.l?calendarDate=2026-11-16",
         (), "/usersummary-service/usersummary/daily"),
        ("/activity-service/activity/21345678901/splits", (), "/activity-service/activity/{id}"),
        ("/userprofile-service/socialProfile/runnerbart", ("runnerbart",), "/userprofile-service/socialProfile/{user}"),
        ("/userprofile-service/x/someone%40example.com", (), "/userprofile-service/x/{id}"),
        ("/wellness-api/rest/dailies?uploadStartTimeInSeconds=1", (), "/wellness-api/rest/dailies"),
    ]
    got = [tracing.path_name(url, private) for url, private, _ in cases]
    check("a request's path keeps three segments, with ids, names and queries gone",
          got == [want for *_, want in cases], str(got))

    # --- A traced call: one $ai_trace, a span per Garmin request -----------
    tracing.SHARE, tracing.DAILY_CAP = 1.0, 3000
    before = len(SENT)
    why = traced_call("get_plan", [
        ("GET", "/calendar-service/year/2026/month/10?x=1", 200, 0.31),
        ("GET", "/workout-service/workout/998877", 404, 0.12),
    ], seconds=0.52)
    events = SENT[before:]
    trace = [e for e in events if e["event"] == "$ai_trace"]
    spans = [e for e in events if e["event"] == "$ai_span"]
    check("a chosen call is one $ai_trace and one $ai_span per Garmin request",
          why == "sample" and len(trace) == 1 and len(spans) == 2, str([e["event"] for e in events]))
    t = trace[0]["properties"] if trace else {}
    check("the trace is named for the tool, with its time, outcome and request count",
          t.get("$ai_span_name") == "get_plan" and t.get("$ai_latency") == 0.52 and t.get("$ai_is_error") is False
          and t.get("garmin_requests") == 2 and t.get("traced_because") == "sample"
          and t.get("client") == "Claude" and t.get("login_route") == "password", str(t))
    check("each span hangs off the trace, in the same session, with its status",
          all(s["properties"]["$ai_parent_id"] == t.get("$ai_trace_id") == s["properties"]["$ai_trace_id"]
              and s["properties"]["$ai_session_id"] == t.get("$ai_session_id") for s in spans)
          and [s["properties"]["$ai_span_name"] for s in spans]
          == ["GET /calendar-service/year/{id}", "GET /workout-service/workout/{id}"]
          and [s["properties"]["$ai_is_error"] for s in spans] == [False, True]
          and [s["properties"].get("http_status") for s in spans] == [200, 404], str([s["properties"] for s in spans]))
    check("timestamps are kept to the millisecond, so the waterfall lines up",
          all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", e["timestamp"]) for e in events),
          str([e["timestamp"] for e in events]))
    check("the person is the usual hashed id, with no profile built up",
          all(e["distinct_id"] == analytics.distinct_id("fp-person-1") and e["distinct_id"] != "fp-person-1"
              and e["properties"]["$process_person_profile"] is False for e in events), str(events[0]))
    check("no inputs, outputs, query strings or AI content go out",
          not any(k in json.dumps(events) for k in ("$ai_input", "$ai_output", "?x=1", "998877", "2026/month")),
          json.dumps(events)[:400])

    # --- Which calls are chosen --------------------------------------------
    tracing.SHARE = 0.000001
    picks = [traced_call("get_sleep_data", [], seconds=0.2),
             traced_call("get_sleep_data", [], ok=False, error_kind="expected"),
             traced_call("get_sleep_data", [], seconds=4.0)]
    me = analytics.distinct_id("fp-bart")
    os.environ["ANALYTICS_INTERNAL_IDS"] = me
    picks.append(traced_call("get_sleep_data", [], subject="fp-bart"))
    os.environ.pop("ANALYTICS_INTERNAL_IDS")
    check("a quick call that worked is sampled; failed, slow and internal ones always go",
          picks == [None, "error", "slow", "internal"], str(picks))
    tracing.SHARE = 0
    check("GARMIN_MCP_AI_TRACES=0 turns it off, failures included",
          traced_call("get_sleep_data", [], ok=False, error_kind="unexpected") is None)
    tracing.SHARE = 1.0
    real_key, analytics.KEY = analytics.KEY, ""
    check("nothing is traced while PostHog isn't set up", traced_call("get_plan", []) is None)
    analytics.KEY = real_key

    # --- The daily cap counts every event ----------------------------------
    tracing._today.update(day=None, sent=0)
    tracing.DAILY_CAP = 5
    first = traced_call("get_plan", [("GET", "/a/b", 200, 0.1)] * 3)
    second = traced_call("get_plan", [("GET", "/a/b", 200, 0.1)])
    third = traced_call("get_plan", [])
    check("a call whose events would pass the day's cap isn't sent; a smaller one still fits",
          (first, second, third) == ("sample", None, "sample"), str((first, second, third)))
    tracing.DAILY_CAP = 3000

    # --- A long call keeps a count past MAX_REQUESTS -----------------------
    before = len(SENT)
    traced_call("get_fitness_trends", [("GET", "/a/b", 200, 0.01)] * (tracing.MAX_REQUESTS + 4))
    many = SENT[before:]
    check("past MAX_REQUESTS a trace keeps the count, not the spans",
          len([e for e in many if e["event"] == "$ai_span"]) == tracing.MAX_REQUESTS
          and many[0]["properties"]["garmin_requests"] == tracing.MAX_REQUESTS + 4, str(len(many)))

    # --- Sessions: a sitting per person and app ----------------------------
    tracing._sittings.clear()
    before = len(SENT)
    traced_call("get_plan", [], subject="fp-a")
    traced_call("get_workout", [], subject="fp-a")
    traced_call("get_plan", [], subject="fp-b")
    key = ("fp-a", "Claude")
    tracing._sittings[key] = (tracing._sittings[key][0], time.time() - tracing.SITTING - 1)
    traced_call("get_plan", [], subject="fp-a")
    ids = [e["properties"]["$ai_session_id"] for e in SENT[before:] if e["event"] == "$ai_trace"]
    check("calls close together share a session; another person, or half an hour later, doesn't",
          ids[0] == ids[1] and ids[2] != ids[0] and ids[3] not in (ids[0], ids[2]), str(ids))

    # --- Requests from another thread land in the right call ---------------
    token = tracing.start("get_hrv")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(tracing.carried(lambda i: tracing.note_request("GET", f"/hrv-service/hrv/{i}", 200, 0.1)),
                      range(3)))
        list(pool.map(lambda i: tracing.note_request("GET", "/lost/one", 200, 0.1), range(2)))
    collected = [r["name"] for r in tracing._call.get().requests]
    tracing.finish(token)
    check("a thread pool's requests count only when carried into the call",
          collected == ["GET /hrv-service/hrv/{id}"] * 3 and tracing._call.get() is None, str(collected))

    # --- garminconnect's API session: a real request through requests ------
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if "ok" in self.path else 500)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    class Client:
        _api_session = requests.Session()

    class FakeGarmin:
        client = Client()
        display_name = "runnerbart"

    garmin = FakeGarmin()
    garmin.client._api_session.trust_env = False
    tracing.watch(garmin)
    tracing.watch(garmin)
    check("watching a client twice hooks it once", len(garmin.client._api_session.hooks["response"]) == 1)
    token = tracing.start("get_profile")
    garmin.client._api_session.get(f"{base}/userprofile-service/socialProfile/runnerbart?ok=1")
    garmin.client._api_session.get(f"{base}/metrics-service/metrics/fail")
    heard = tracing._call.get().requests
    tracing.finish(token)
    garmin.client._api_session.get(f"{base}/outside/any/call?ok=1")
    httpd.shutdown()
    check("every request the client makes is heard, by status, with the account's name replaced",
          [(r["name"], r["status"], r["failed"]) for r in heard]
          == [("GET /userprofile-service/socialProfile/{user}", 200, False),
              ("GET /metrics-service/metrics/fail", 500, True)]
          and all(r["seconds"] is not None for r in heard), str(heard))
    tracing.watch(object())  # a client of another shape is left alone
    check("a request outside any tool call is ignored", tracing._call.get() is None)

    # --- The official API's requests ---------------------------------------
    real_send = official._send
    try:
        token = tracing.start("get_activities")
        official._send = lambda request: b"{}"
        official._call("tok", "GET", "/wellness-api/rest/dailies?uploadStartTimeInSeconds=17")

        def refuse(request):
            raise urllib.error.HTTPError(request.full_url, 429, "slow down", {}, io.BytesIO(b"{}"))

        official._send = refuse
        try:
            official._call("tok", "GET", "/wellness-api/rest/activities?x=1")
        except official.OfficialError:
            pass
        heard = [(r["name"], r["status"], r["failed"]) for r in tracing._call.get().requests]
        tracing.finish(token)
    finally:
        official._send = real_send
    check("official API requests are heard too, a refusal as failed with Garmin's status",
          heard == [("GET /wellness-api/rest/dailies", None, False), ("GET /wellness-api/rest/activities", 429, True)],
          str(heard))

    # --- Through the real tool wrapper -------------------------------------
    reported: list = []

    def hook(name, result):
        reported.append(tracing.report("fp-person-1", ok="error" not in result, error_kind=None,
                                       seconds=hooks.seconds(), app="Claude"))
        return result

    @garmin_server.tool_errors
    async def demo_tool() -> dict:
        tracing.note_request("GET", "/device-service/deviceregistration/devices", 200, 0.05)
        return await inner_tool()

    @garmin_server.tool_errors
    async def inner_tool() -> dict:
        return {"fine": True}

    before = len(SENT)
    token = hooks.use(hook, None)
    try:
        asyncio.run(demo_tool())
    finally:
        hooks.reset(token)
    names = [(e["event"], e["properties"].get("$ai_span_name")) for e in SENT[before:]]
    check("a tool run through the wrapper is one trace, a tool it calls isn't another",
          reported == ["sample"] and names == [("$ai_trace", "demo_tool"),
                                               ("$ai_span", "GET /device-service/deviceregistration/devices")],
          str((reported, names)))
    check("and the call is closed once it's answered", tracing._call.get() is None)

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
