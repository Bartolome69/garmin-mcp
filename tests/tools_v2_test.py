"""The Nov 26 tool fixes (flag tools-v2): ranges, the loop guard, writes that
are safe to repeat, create_plan four at a time, and the run answer kept.

Run: .venv/bin/python tests/tools_v2_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["GARMIN_MCP_DB"] = tempfile.mktemp(suffix=".sqlite3")
os.environ.setdefault("GARMIN_EMAIL", "test@example.com")
os.environ.setdefault("GARMIN_PASSWORD", "hunter2")
os.environ["GARMIN_MCP_TOOLS_V2"] = "all"
os.environ["GARMIN_MCP_POSTHOG_KEY"] = "phc_test_key"
from cryptography.fernet import Fernet  # noqa: E402
os.environ.setdefault("GARMIN_MCP_SECRET", Fernet.generate_key().decode())

from garminconnect.exceptions import GarminConnectNotFoundError  # noqa: E402

from garmin_mcp import session as session_mod  # noqa: E402
from tests.demo_garmin import DemoGarmin  # noqa: E402


class GarminLike(DemoGarmin):
    """The demo account, with Garmin's own habits where the tests need them:
    deleting a workout takes every date it was scheduled on with it, anything
    already gone is a 404, and each write can be made to take a while."""

    delay = 0.0
    in_flight = 0
    most_in_flight = 0
    _lock = threading.Lock()

    def _wait(self) -> None:
        with self._lock:
            self.in_flight += 1
            self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            if self.delay:
                time.sleep(self.delay)
        finally:
            with self._lock:
                self.in_flight -= 1

    def upload_workout(self, workout_json):
        self._wait()
        with self._lock:
            return super().upload_workout(workout_json)

    def schedule_workout(self, workout_id, date_str):
        self._wait()
        with self._lock:
            return super().schedule_workout(workout_id, date_str)

    def unschedule_workout(self, scheduled_workout_id):
        if not any(s["id"] == int(scheduled_workout_id) for s in self.scheduled):
            raise GarminConnectNotFoundError("404 Client Error: Not Found")
        return super().unschedule_workout(scheduled_workout_id)

    def delete_workout(self, workout_id):
        known = int(workout_id) in self.uploaded or any(int(w["workoutId"]) == int(workout_id)
                                                        for w in self.workouts)
        if not known:
            raise GarminConnectNotFoundError("404 Client Error: Not Found")
        self.scheduled = [s for s in self.scheduled if s["workoutId"] != int(workout_id)]
        return super().delete_workout(workout_id)


FAKE = GarminLike()
session_mod.build_client = lambda **_: FAKE

from garmin_mcp import analytics, day_ranges, hooks, server, store, tools_v2  # noqa: E402
import garmin_mcp.hosted as hosted  # noqa: E402

SENT: list[dict] = []
analytics._post = SENT.append
analytics._fetch_flags = lambda who: {}

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if not ok and detail else ""))
    if not ok:
        FAILURES.append(name)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# 1. Ranges
# --------------------------------------------------------------------------

def test_days_between() -> None:
    today = date(2026, 11, 26)
    check("days=7 is the week to today", day_ranges.days_between(days=7, today=today)[0] == "2026-11-20"
          and day_ranges.days_between(days=7, today=today)[-1] == "2026-11-26")
    check("start alone runs to today", len(day_ranges.days_between("2026-11-01", today=today)) == 26)
    check("dates the wrong way round still read", day_ranges.days_between("2026-11-10", "2026-11-01", today=today)[0]
          == "2026-11-01")
    check("90 days allowed", len(day_ranges.days_between(days=90, today=today)) == 90)
    try:
        day_ranges.days_between(days=91, today=today)
        check("91 days refused", False)
    except Exception as exc:  # noqa: BLE001
        check("91 days refused, saying the limit", "at most 90" in str(exc))


def test_range_equals_per_day() -> None:
    for tool, one, many in (
        ("get_daily_summary", server._daily_summary_day, server.get_daily_summary),
        ("get_sleep_data", server._sleep_day, server.get_sleep_data),
        ("get_readiness", lambda d: server._readiness_day(d, in_range=True), server.get_readiness),
    ):
        ranged = run(many(days=7))
        days = day_ranges.days_between(days=7)
        singles = [run(one(d)) for d in days]
        expected = [day_ranges.row(tool, d, a) for d, a in zip(days, singles)]
        check(f"{tool}: a 7-day range is the 7 one-day answers, row by row", ranged.get("rows") == expected,
              (ranged.get("rows"), expected))
        check(f"{tool}: a range has one row a day, oldest first", [r["date"] for r in ranged["rows"]] == days)
        check(f"{tool}: one date still answers in full, as before", "rows" not in run(many(date=days[-1])))


def test_args_shape() -> None:
    check("args_shape: a range", day_ranges.shape("get_sleep_data", {"days": 14}) == {"range_days": 14})
    check("args_shape: one day", day_ranges.shape("get_sleep_data", {"date": "2026-11-20"}) == {"single_date": True})
    check("args_shape: activities by one day",
          day_ranges.shape("get_activities", {"start_date": "2026-11-20", "end_date": "2026-11-20"})
          == {"range_days": 1, "single_date": True})
    check("args_shape: nothing for other tools", day_ranges.shape("get_plan", {}) == {})


# --------------------------------------------------------------------------
# 2. The loop guard
# --------------------------------------------------------------------------

def _guard(name: str, args: dict, now: float, user_token: str = "tok-guard", client: str = "Claude") -> bool:
    """One call through the guard; True if it went through."""
    args_token = server.CALL_ARGS.set(args)
    v2_token = tools_v2.ON.set(True)
    try:
        hosted._within_loop_guard(user_token, name, None, client, now=now)
        return True
    except hooks.Paused as said:
        _guard.said = str(said)
        return False
    finally:
        server.CALL_ARGS.reset(args_token)
        tools_v2.ON.reset(v2_token)


def test_loop_guard() -> None:
    SENT.clear()
    t0 = 1000.0
    through = [_guard("get_daily_summary", {"date": f"-{i}"}, t0 + i) for i in range(15)]
    check("guard: 15 single-day calls in 3 minutes all go through", all(through))
    check("guard: the 16th is held", not _guard("get_daily_summary", {"date": "-16"}, t0 + 16))
    blocked = [e for e in SENT if e.get("event") == "tool_loop_blocked"]
    check("guard: tool_loop_blocked logged with tool, count and client",
          blocked and blocked[-1]["properties"].get("tool") == "get_daily_summary"
          and blocked[-1]["properties"].get("count") == 16 and blocked[-1]["properties"].get("client") == "Claude",
          blocked)
    check("guard: a range call is never held", _guard("get_daily_summary", {"days": 30}, t0 + 17))
    check("guard: through again once the window moves on",
          _guard("get_daily_summary", {"date": "-20"}, t0 + 15 + hosted.LOOP_WINDOW))
    check("guard: another person isn't held", _guard("get_daily_summary", {"date": "-1"}, t0 + 18, "tok-other"))
    loose = [_guard("update_workout", {"workout_id": i}, t0 + i, "tok-loose") for i in range(50)]
    check("guard: 50 calls of any other tool go through", all(loose))
    check("guard: the 51st is held", not _guard("update_workout", {"workout_id": 51}, t0 + 51, "tok-loose"))
    # Nov 27: ChatGPT can't ask for a range yet, so its one-day reads get the
    # loose cap, and the note doesn't tell it to use one.
    gpt = [_guard("get_sleep_data", {"date": f"-{i}"}, t0 + i, "tok-gpt", "ChatGPT") for i in range(50)]
    check("guard: ChatGPT, which can't ask for a range yet, reads 50 single days in 3 minutes", all(gpt))
    held = not _guard("get_sleep_data", {"date": "-51"}, t0 + 51, "tok-gpt", "ChatGPT")
    check("guard: the 51st is held, as a runaway (226 in 3 minutes, Nov 27) would be",
          held and "ask before doing more" in _guard.said and "start_date" not in _guard.said, _guard.said)
    # Off the flag, nothing is held.
    args_token = server.CALL_ARGS.set({"date": "-1"})
    os.environ["GARMIN_MCP_TOOLS_V2"] = "off"
    try:
        held = False
        for i in range(40):
            try:
                hosted._within_loop_guard("tok-off", "get_sleep_data", None, None, now=t0 + i)
            except hooks.Paused:
                held = True
        check("guard: off the flag, nothing is held", not held)
    finally:
        os.environ["GARMIN_MCP_TOOLS_V2"] = "all"
        server.CALL_ARGS.reset(args_token)


# --------------------------------------------------------------------------
# 3. Writes safe to repeat, and the failures that are now answers
# --------------------------------------------------------------------------

def _made(answer: dict) -> int | None:
    """Sessions create_plan wrote: under created when it answers with the card."""
    return (answer.get("created") or {}).get("sessions_created") or answer.get("sessions_created")


def _plan_sessions(start: date, weeks: int) -> list[dict]:
    # Rides, not runs: the demo account already has running plans in these weeks.
    easy = [{"type": "warmup", "duration_seconds": 300}, {"type": "interval", "duration_seconds": 3600,
                                                          "hr": [120, 140]}]
    gym = [{"exercise": "Goblet Squat", "sets": 3, "reps": 10}]
    sessions = []
    for w in range(weeks):
        monday = start + timedelta(weeks=w)
        sessions.append({"date": monday.isoformat(), "name": "Gym", "sport": "strength", "exercises": gym})
        sessions.append({"date": (monday + timedelta(days=2)).isoformat(), "name": f"Easy ride {w + 1}",
                         "sport": "cycling", "steps": easy})
        sessions.append({"date": (monday + timedelta(days=5)).isoformat(), "name": "Club ride", "sport": "cycling",
                         "steps": easy})
    return sessions


def test_remove_plan_twice() -> None:
    start = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
    made = run(server.create_plan("Base block", _plan_sessions(start, 4), label="ZZ", existing="keep"))
    check("remove: a plan with repeated sessions is made", _made(made) == 12, made.get("error"))
    asked = run(server.remove_plan("ZZ"))
    check("remove: the first call only asks", asked.get("confirmation_required") is True)
    gone = run(server.remove_plan("ZZ", confirm="ZZ"))
    check("remove: every session goes, though each repeated workout is shared by four dates",
          gone.get("removed") == 12 and not gone.get("failed") and "error" not in gone, gone)
    left = [s for s in FAKE.scheduled if s["title"].endswith("· ZZ")]
    check("remove: nothing of the plan is left on the calendar", not left, left)
    again = run(server.remove_plan("ZZ", confirm="ZZ"))
    check("remove: asked again, it says so and isn't an error", "error" not in again and again.get("removed") == 0,
          again)


def test_remove_plan_after_garmin_app_delete() -> None:
    start = date.today() + timedelta(days=21)
    run(server.create_plan("Base block", _plan_sessions(start, 2), label="YY", existing="keep"))
    # One workout deleted in the Garmin app in between: its dates went with it.
    entry = next(s for s in FAKE.scheduled if s["title"].endswith("· YY"))
    FAKE.delete_workout(entry["workoutId"])
    server.session.forget()
    gone = run(server.remove_plan("YY", confirm="YY"))
    check("remove: a workout already deleted elsewhere doesn't stop the rest", "error" not in gone
          and not gone.get("failed") and gone.get("removed"), gone)


def test_unschedule() -> None:
    day = (date.today() + timedelta(days=60)).isoformat()
    a = FAKE.upload_workout({"workoutName": "Tempo 5k", "sportType": {"sportTypeKey": "running"}})["workoutId"]
    b = FAKE.upload_workout({"workoutName": "Strides", "sportType": {"sportTypeKey": "running"}})["workoutId"]
    sa = FAKE.schedule_workout(a, day)["workoutScheduleId"]
    FAKE.schedule_workout(b, day)
    server.session.forget()
    double = run(server.unschedule_workout(date=day))
    # The demo plan can have its own session that day too, depending on today's date.
    names = {c.get("name") for c in double.get("candidates") or []}
    check("unschedule: a double day answers with its sessions, not an error",
          "error" not in double and double.get("needs_choice") and {"Tempo 5k", "Strides"} <= names, double)
    named = run(server.unschedule_workout(date=day, name="tempo"))
    check("unschedule: the date and part of a name picks one", named.get("unscheduled") == sa, named)
    again = run(server.unschedule_workout(schedule_id=sa))
    check("unschedule: an entry already off is done, not an error", "error" not in again
          and again.get("already_off") is True, again)


def test_session_not_found() -> None:
    calls = []

    class Gone:
        def unschedule_workout(self, sid):
            calls.append(sid)
            raise GarminConnectNotFoundError("404 Client Error: Not Found")

    s = session_mod.GarminSession.__new__(session_mod.GarminSession)
    s.client = lambda: Gone()
    s._view = lambda c: c
    try:
        s._call(lambda c: c.unschedule_workout(5))
        check("a 404 is GarminNotFound", False)
    except session_mod.GarminNotFound as exc:
        check("a 404 is GarminNotFound, not 'could not reach Garmin'", "reach" not in str(exc), str(exc))
    check("a 404 isn't asked again", calls == [5], calls)


def test_create_plan_inputs_and_questions() -> None:
    soon = date.today() + timedelta(days=200)  # past the demo account's running plans
    loose = soon.strftime("%a %-d %b")
    later = (soon + timedelta(days=2)).isoformat()
    sessions = [{"title": "Easy 6k", "day": loose, "sport": "run",
                 "steps": [{"type": "warm-up", "minutes": 5}, {"type": "easy", "distance_km": 6}]},
                {"name": "Easy 8k", "date": later, "steps": [{"type": "easy", "km": 8}]}]
    made = run(server.create_plan("Test", sessions, label="XA"))
    check("create_plan: title, 'Mon 1 Dec', 'run', 'warm-up', minutes and km all read",
          _made(made) == 2, made.get("error"))
    again = run(server.create_plan("Test", sessions, label="XA"))
    check("create_plan: a plan code already on the calendar is a question, not an error",
          "error" not in again and again.get("needs_answer") and "already on the calendar" in again.get("question", ""),
          again)
    run(server.remove_plan("XA", confirm="XA"))
    bad = run(server.create_plan("Test", [{"name": "x", "date": "2020-01-01", "steps": [{"type": "rest",
                                                                                      "duration_seconds": 60}]}],
                                 label="XB"))
    check("create_plan: a past date is still an error that says which session and why",
          "Session 1 is dated 2020-01-01, which has passed" in str(bad.get("error")), bad)
    check("error_check names the check, not the words", hosted.write_check("create_plan", bad["error"]) == "past_date")


def test_strength_and_update() -> None:
    from garmin_mcp import workouts
    for typed, garmin in (("DB row", "Dumbbell Row"), ("RDL", "Romanian Deadlift"), ("KB swing", "Kettlebell Swing"),
                          ("bench press", "Bench Press")):
        try:
            got = workouts.resolve_exercise(typed)["name"]
        except Exception as exc:  # noqa: BLE001
            got = str(exc)
        check(f"strength: {typed!r} is {garmin}", got == garmin, got)
    try:
        workouts.resolve_exercise("split squat")
        check("strength: two equal versions still ask", False)
    except workouts.WorkoutError as exc:
        check("strength: two equal versions still ask", "matches several" in str(exc))
    wid = FAKE.upload_workout({"workoutName": "Tempo", "sportType": {"sportTypeKey": "running"},
                               "workoutSegments": []})["workoutId"]
    sport_only = run(server.update_workout(wid, sport="cycling"))
    check("update_workout: sport alone says to send steps", "send its steps too" in sport_only.get("error", ""),
          sport_only)
    check("error_check: sport_only", hosted.write_check("update_workout", sport_only["error"]) == "sport_only")
    stepped = run(server.update_workout(str(wid), steps=[{"type": "Warm Up", "duration": "10:00"},
                                                         {"type": "tempo", "km": 3, "target_pace": "4:30"},
                                                         {"type": "cool_down", "mins": 5}]))
    check("update_workout: step spellings chats use are read", "error" not in stepped, stepped)


def test_previous_failures_now_answer() -> None:
    """Each kind of failure in the 14 days to Nov 26, against the fixed tools:
    it works now, or the answer says what to do next."""
    day = (date.today() + timedelta(days=70)).isoformat()
    w1 = FAKE.upload_workout({"workoutName": "A", "sportType": {"sportTypeKey": "running"}})["workoutId"]
    w2 = FAKE.upload_workout({"workoutName": "B", "sportType": {"sportTypeKey": "running"}})["workoutId"]
    FAKE.schedule_workout(w1, day)
    FAKE.schedule_workout(w2, day)
    server.session.forget()
    cases = {
        "unschedule_workout conflict (double day)": run(server.unschedule_workout(date=day)),
        "unschedule_workout not_found": run(server.unschedule_workout(schedule_id=99999999)),
        "remove_plan not_found (nothing of that plan left)": run(server.remove_plan("QQ", confirm="QQ")),
        "update_workout invalid_input (step spelling)": run(server.update_workout(
            w1, steps=[{"type": "run", "seconds": 600}])),
    }
    for name, answer in cases.items():
        check(f"previous failure now answers: {name}", "error" not in answer, answer)


# --------------------------------------------------------------------------
# 4. Speed: create_plan four at a time, the run answer kept
# --------------------------------------------------------------------------

BEFORE_START = date.today() + timedelta(days=100)
AFTER_START = BEFORE_START + timedelta(days=7)


def _timed_plan(label: str, flag: str) -> tuple[float, dict]:
    os.environ["GARMIN_MCP_TOOLS_V2"] = flag
    start = AFTER_START if flag == "all" else BEFORE_START
    try:
        t = time.monotonic()
        made = run(server.create_plan("Speed", _plan_sessions(start, 8), label=label, existing="keep"))
        return time.monotonic() - t, made
    finally:
        os.environ["GARMIN_MCP_TOOLS_V2"] = "all"


def test_create_plan_speed() -> None:
    FAKE.delay = 0.05  # each upload or schedule takes 50 ms, as a quick Garmin
    FAKE.most_in_flight = 0
    before, made_before = _timed_plan("SA", "off")
    sequential_peak = FAKE.most_in_flight
    FAKE.most_in_flight = 0
    after, made_after = _timed_plan("SB", "all")
    FAKE.delay = 0.0
    check("create_plan: one after another writes every session", _made(made_before) == 24)
    check("create_plan: four at a time writes every session", _made(made_after) == 24, made_after.get("error"))
    check("create_plan: never more than four writes at once", FAKE.most_in_flight <= 4 and sequential_peak == 1,
          (FAKE.most_in_flight, sequential_peak))
    def shape(made: dict, start: date) -> list:
        rows = (made.get("created") or {}).get("first_sessions") or []
        return [(r["name"], (date.fromisoformat(r["date"]) - start).days) for r in rows]
    check("create_plan: the same sessions on the same days either way",
          shape(made_before, BEFORE_START) and shape(made_before, BEFORE_START) == shape(made_after, AFTER_START),
          (shape(made_before, BEFORE_START), shape(made_after, AFTER_START)))
    check(f"create_plan: faster four at a time ({before:.2f}s → {after:.2f}s for 24 sessions)", after < before * 0.6)
    print(f"     create_plan, 24 sessions at 50 ms a write: {before:.2f}s one at a time, {after:.2f}s four at a time")
    run(server.remove_plan("SA", confirm="SA"))
    run(server.remove_plan("SB", confirm="SB"))


def test_activity_answer_kept() -> None:
    acts = run(server.get_activities(limit=3))["activities"]
    aid = acts[0]["activity_id"]
    first = run(server.get_activity_details(aid))
    reads = []
    original = FAKE.get_activity_splits
    FAKE.get_activity_splits = lambda *a, **k: reads.append(1) or original(*a, **k)
    try:
        second = run(server.get_activity_details(aid))
    finally:
        FAKE.get_activity_splits = original
    check("run details: asked again, the kept answer, with no Garmin reads", second == first and not reads)
    old = {"summary": {"start_local": (date.today() - timedelta(days=30)).isoformat() + " 07:00"},
           "share_image": {}, "strava_caption": {}, "card": {}, "splits": [1]}
    trimmed = server._trimmed(dict(old))
    check("run details: a month-old run leaves out the share image, caption and card",
          set(trimmed) == {"summary", "splits"}, trimmed)
    fresh = {**old, "summary": {"start_local": date.today().isoformat() + " 07:00"}}
    check("run details: this week's run keeps them", set(server._trimmed(dict(fresh))) == set(fresh))


# --------------------------------------------------------------------------
# 5. End to end: a signed-in person, the flag as PostHog answers it, the hooks
# --------------------------------------------------------------------------

def _person(email: str) -> str:
    return store.save_user(session_mod.mask_email(email), '{"di_token": "x"}',
                           email_hash=store.email_fingerprint(email), email=email)


async def _as(user_token: str, client: str, calls):
    """Run calls as this person from this chat app, with hosted's real hooks."""
    user = store.get_user(user_token)
    bound = server.official.use(user_token)
    hook = hooks.use(hosted._after_tool_for(user_token, user, False, client),
                     hosted._before_tool_for(user_token, False, user, client))
    try:
        return [await c() for c in calls]
    finally:
        hooks.reset(hook)
        server.official.reset(bound)


def _wait_for(pred, tries: int = 60):
    for _ in range(tries):
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_end_to_end() -> None:
    flagged, other = _person("flagged@example.test"), _person("other@example.test")
    on_ids = {analytics.distinct_id(store.get_user(flagged).email_hash)}
    analytics._fetch_flags = lambda who: {"flags": {"tools-v2": {"enabled": who in on_ids}}}
    os.environ["GARMIN_MCP_TOOLS_V2"] = "flag"
    hosted._SAME_TOOL.clear()
    hosted._CALLS.clear()
    SENT.clear()
    try:
        days = [f"-{i}" for i in range(16)]
        # A client that can ask for a range (ChatGPT can't yet: it gets the loose cap, tested above).
        got = run(_as(flagged, "Claude", [lambda d=d: server.get_sleep_data(date=d) for d in days]))
        check("e2e flagged: 15 nights one at a time answer", all("error" not in g for g in got[:15]),
              [g.get("error") for g in got[:15]])
        check("e2e flagged: the 16th is held, pointing at a range",
              "start_date and end_date" in str(got[15].get("error")), got[15])
        ranged = run(_as(flagged, "Claude", [lambda: server.get_sleep_data(days=30)]))[0]
        check("e2e flagged: the range call right after goes through with 30 rows",
              len(ranged.get("rows") or []) == 30 and "credit" in ranged, list(ranged)[:8])
        other_got = run(_as(other, "Claude", [lambda d=d: server.get_sleep_data(date=d) for d in days]))
        check("e2e not flagged: 16 one-day calls all answer, as today",
              all("error" not in g for g in other_got), [g.get("error") for g in other_got if "error" in g])
        _wait_for(lambda: len([e for e in SENT if e.get("event") == "tool_called"]) >= 15 + 1 + 16)
        called = [e["properties"] for e in SENT if e.get("event") == "tool_called"]
        blocked = [e["properties"] for e in SENT if e.get("event") == "tool_loop_blocked"]
        check("e2e: tool_loop_blocked once, for the flagged chat",
              len(blocked) == 1 and blocked[0]["tool"] == "get_sleep_data" and blocked[0]["client"] == "Claude"
              and blocked[0]["count"] == 16, blocked)
        check("e2e: held calls aren't tool_called (15 + 1 range + 16 others)", len(called) == 32, len(called))
        check("e2e: tool_called says single_date or range_days, and tools_v2 for the flagged person",
              sum(1 for c in called if c.get("single_date")) == 31
              and [c.get("range_days") for c in called if c.get("range_days")] == [30]
              and sum(1 for c in called if c.get("tools_v2")) == 16, called[-1])
        check("e2e: no email or token in any event",
              "example.test" not in json.dumps([e for e in SENT if e.get("event") in
                                                ("tool_called", "tool_loop_blocked")]))
    finally:
        os.environ["GARMIN_MCP_TOOLS_V2"] = "all"
        analytics._fetch_flags = lambda who: {}


def test_one_flag_request_an_hour() -> None:
    asked = []
    analytics._fetch_flags = lambda who: asked.append(who) or {
        "flags": {"tools-v2": {"enabled": True}, "match-v2": {"enabled": False}}}
    try:
        analytics._FLAGS.clear()
        first = [analytics.flag_on("tools-v2", "someone-hash") for _ in range(20)]
        second = analytics.flag_on("match-v2", "someone-hash")
    finally:
        analytics._fetch_flags = lambda who: {}
    check("flags: twenty calls and a second flag are one request to PostHog an hour",
          all(first) and second is False and len(asked) == 1, asked)


def test_range_edge_cases() -> None:
    original = FAKE.get_sleep_data
    calls = []

    def flaky(day, *a, **k):
        calls.append(day)
        if day == (date.today() - timedelta(days=2)).isoformat():
            raise ValueError("odd payload")
        return original(day, *a, **k)
    FAKE.get_sleep_data = flaky
    try:
        from garmin_mcp import garmin_cache
        garmin_cache.clear()
        server.session.forget()
        got = run(server.get_sleep_data(days=5))
    finally:
        FAKE.get_sleep_data = original
    check("range: one day failing is a missing row, not a failed range",
          "error" not in got and len(got["rows"]) == 5, got)
    stats = FAKE.get_stats
    FAKE.get_stats = lambda day, *a, **k: None if str(day).startswith("2020-") else stats(day, *a, **k)
    try:
        empty = run(server.get_daily_summary(start_date="2020-01-01", end_date="2020-01-03"))
    finally:
        FAKE.get_stats = stats
    check("range: days with nothing recorded say so", [r.get("missing") for r in empty["rows"]]
          == ["nothing recorded"] * 3 and empty["days_with_data"] == 0, empty)
    too_long = run(server.get_readiness(start_date="2026-01-01", end_date="2026-12-31"))
    check("range: over 90 days is a clear error", "at most 90" in str(too_long.get("error")), too_long)
    mixed = run(server.get_sleep_data(date="yesterday", days=3))
    check("range: date with a range reads the range", len(mixed.get("rows") or []) == 3)


if __name__ == "__main__":
    for test in (test_days_between, test_range_equals_per_day, test_args_shape, test_loop_guard,
                 test_session_not_found, test_remove_plan_twice, test_remove_plan_after_garmin_app_delete,
                 test_unschedule, test_create_plan_inputs_and_questions, test_strength_and_update,
                 test_previous_failures_now_answer, test_create_plan_speed, test_activity_answer_kept,
                 test_end_to_end, test_one_flag_request_an_hour, test_range_edge_cases):
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{test.__name__} ran", False, exc)
    print(f"\n{'FAILED ' + str(len(FAILURES)) if FAILURES else 'all passed'}")
    sys.exit(1 if FAILURES else 0)
