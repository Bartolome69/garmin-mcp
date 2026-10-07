"""End-to-end smoke test: drives the server over stdio like a real MCP client.

Runs against a stubbed Garmin account (no network, no credentials), so it
checks the wiring and the response shaping, not Garmin itself.

    .venv/bin/python tests/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
EXPECTED_TOOLS = {
    "get_daily_summary",
    "get_sleep_data",
    "get_activities",
    "get_activity_details",
    "get_connection_status",
    "list_workouts",
    "create_workout",
    "schedule_workout",
    "get_profile",
    "find_exercises",
    "create_strength_workout",
    "unschedule_workout",
    "delete_workout",
    "get_progress",
    "get_readiness",
    "get_fitness",
    "update_workout",
    "create_plan",
    "get_plan",
    "remove_plan",
}


def field(obj, *names):
    """The first of these attributes present: SDK 2 renamed them to snake_case."""
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def payload(result) -> dict:
    """Pull the JSON body out of a CallToolResult."""
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def check_target_placement(check) -> None:
    """Garmin silently drops target values nested inside targetType.

    They must sit on the step itself, alongside targetType. Uploading the wrong
    shape succeeds and loses the numbers, so only a read-back caught this —
    this asserts the shape directly.
    """
    from garmin_mcp.workouts import build_workout

    workout, _, _ = build_workout(
        "targets",
        "running",
        [
            {"type": "interval", "distance_meters": 1000, "pace": "4:05"},
            {"type": "interval", "duration_seconds": 300, "hr": [150, 165]},
        ],
    )
    pace_step, hr_step = workout.to_dict()["workoutSegments"][0]["workoutSteps"]

    check(
        "pace values sit on the step",
        pace_step.get("targetValueOne") == 4.0
        and round(pace_step.get("targetValueTwo"), 4) == 4.1667,
    )
    check(
        "pace values are not nested in targetType",
        "targetValueOne" not in pace_step["targetType"],
    )
    check(
        "hr values sit on the step",
        (hr_step.get("targetValueOne"), hr_step.get("targetValueTwo")) == (150.0, 165.0),
    )


def check_plan_summary(check) -> None:
    """A plan midway through, against real runs: the verdicts that matter.

    Two weeks in. Week one: Tuesday done on the day, Thursday done a day late,
    Sunday missed. Week two: Tuesday missed, today's session not yet run, one
    session ahead.
    """
    from datetime import date as _d
    from garmin_mcp import training_plan as tp

    today = _d(2026, 9, 30)  # Wednesday of week two
    cal = [
        {"id": 1, "date": "2026-09-22", "workoutId": 11, "title": "Easy 8k · HM", "sportTypeKey": "running"},
        {"id": 2, "date": "2026-09-24", "workoutId": 12, "title": "Threshold 5x1k · HM", "sportTypeKey": "running"},
        {"id": 3, "date": "2026-09-27", "workoutId": 13, "title": "Long run 16k · HM", "sportTypeKey": "running"},
        {"id": 4, "date": "2026-09-29", "workoutId": 14, "title": "Easy 8k · HM", "sportTypeKey": "running"},
        {"id": 5, "date": "2026-09-30", "workoutId": 15, "title": "Tempo 6k · HM", "sportTypeKey": "running"},
        {"id": 6, "date": "2026-10-04", "workoutId": 16, "title": "Long run 18k · HM", "sportTypeKey": "running"},
        {"id": 7, "date": "2026-09-26", "workoutId": 99, "title": "Parkrun", "sportTypeKey": "running"},
    ]
    runs = [
        {"activityId": 501, "startTimeLocal": "2026-09-22 07:00:00", "activityName": "Easy",
         "activityType": {"typeKey": "running"}, "duration": 2700, "distance": 8100},
        {"activityId": 502, "startTimeLocal": "2026-09-25 07:00:00", "activityName": "Reps",
         "activityType": {"typeKey": "running"}, "duration": 3100, "distance": 10200},
    ]
    plans = tp.plans_in(cal)
    check("only tagged sessions form the plan", set(plans) == {"HM"} and len(plans["HM"]) == 6,
          str({k: len(v) for k, v in plans.items()}))
    s = tp.summarise("HM", plans["HM"], runs, today, goal="Half")
    status = {r["date"]: r["status"] for w in s["weeks"] for r in w["sessions"]}
    check("done on the day, and done a day late, both count",
          status["2026-09-22"] == "done" and status["2026-09-24"] == "done, moved", str(status))
    check("an unrun session in the past is missed",
          status["2026-09-27"] == "missed" and status["2026-09-29"] == "missed", str(status))
    check("today's unrun session is today, not missed", status["2026-09-30"] == "today", str(status))
    check("completion counts only the sessions that are due",
          s["completed"] == 2 and s["missed"] == 2 and s["completion_percent"] == 50, str(s)[:200])
    check("the next session is today's", s["next_session"]["date"] == "2026-09-30")
    check("the review names what slipped this week",
          [m["date"] for m in s["review"]["missed_last_7_days"]] == ["2026-09-27", "2026-09-29"]
          and s["review"]["this_week_remaining"] == ["Tempo 6k", "Long run 18k"], str(s.get("review")))
    check("with something missed, the model is told a session done off the watch still counts",
          "take their word" in s["review"].get("if_done_elsewhere", ""), str(s.get("review")))
    check("weeks are numbered from the plan's first",
          [w["week"] for w in s["weeks"]] == [1, 2] and s["current_week"] == 2
          and s["weeks"][1]["current"] is True, str([(w["week"], w["current"]) for w in s["weeks"]]))


def check_conditions(check) -> None:
    """Units the weather endpoint never states, and the line on what they did."""
    from garmin_mcp import conditions

    us = conditions.shape_weather({"temp": 50, "apparentTemp": 48, "dewPoint": 40, "windSpeed": 20}, "statute_us")
    check("a US account gets Fahrenheit and mph beside Celsius and km/h",
          us.get("temperature_c") == 10.0 and us.get("temperature_f") == 50
          and us.get("wind_mph") == 20 and us.get("wind_kmh") == 32, str(us))
    uk = conditions.shape_weather({"temp": 50, "windSpeed": 20}, "statute_uk")
    check("a UK account's wind is mph, its temperature Celsius only",
          uk.get("wind_kmh") == 32 and "temperature_f" not in uk, str(uk))
    check("mild still weather says nothing", conditions.effect(12, 6, 8) is None)
    check("no weather is no block", conditions.shape_weather({}, "metric") is None)


def check_terrain_ignores_stops(check) -> None:
    """A five-minute regroup with the watch running is not a very slow kilometre."""
    from garmin_mcp import terrain

    d = t = stopped = 0.0
    cols = {"sumDistance": [], "sumDuration": [], "directElevation": [], "directHeartRate": []}
    while d < 9000:
        for key, value in zip(cols, (d, t, 40.0 - min(max(0.0, d - 3500), 1000) * 0.03, 135.0)):
            cols[key].append(value)
        if d >= 3500 and stopped < 300:
            stopped += 5
        else:
            d += 1000 / 300 * 5
        t += 5
    r = terrain.analyse(cols)
    check("a stop is left out of the paces",
          r["flat_equivalent_pace"] in ("5:01 /km", "5:02 /km", "5:03 /km")
          and r["by_gradient"]["downhill"]["pace_per_km"] == "5:00 /km", str(r.get("by_gradient")))
    check("a stop doesn't read as surging", "decoupling_pct" in (r.get("effort") or {}), str(r.get("effort")))


def check_shoes_and_nights(check) -> None:
    """What a real account showed: unnamed models, worn-out pairs, no watch at night."""
    from datetime import date as _d
    from garmin_mcp import gear, recovery

    shoe = gear.shape_shoe({"displayName": "Novas", "gearMakeName": "Unknown", "gearModelName": "Unknown Shoes",
                            "maximumMeters": 644000.0}, {"totalDistance": 688000.0}, [], False, _d(2026, 10, 5))
    check("Garmin's placeholder model isn't shown as a model", "model" not in shoe, str(shoe))
    check("a pair past its limit says by how much, not negative km left",
          shoe.get("km_left") == 0 and shoe.get("km_over_limit") == 44, str(shoe))
    day = _d(2026, 10, 5)
    bare = recovery.shape_trends(_d(2026, 9, 14), day, None, [{"calendarDate": "2026-10-01", "value": 55}],
                                 [], [], [], [], [])
    check("no watch overnight is said, not left out", "worn overnight" in (bare.get("overnight") or ""), str(bare)[:200])


async def check_preview_gate(check) -> None:
    """Views and ride features are everyone's; preview adds two tools and a run's extras."""
    from garmin_mcp import preview, server

    def view_of(tools):
        tool = next(t for t in tools if t.name == "get_plan")
        return (getattr(tool, "meta", None) or {}).get("ui")

    token = preview.use(False)
    try:
        tools = await server.mcp.list_tools()
        resources = await server.mcp.list_resources()
        names = {t.name for t in tools}
        check("without preview, get_plan has its view", view_of(tools) is not None)
        check("without preview, the view is listed",
              any(str(r.uri).startswith("ui://") for r in resources))
        check("without preview, get_plan still has its output schema",
              next(t for t in tools if t.name == "get_plan").output_schema is not None)
        check("without preview, the chart tool the view replaces is not offered", "get_plan_chart" not in names)
        check("without preview, the preview-only tools are hidden",
              not ({"get_shoes", "get_recovery_trends"} & names), str(sorted(names)))
        plain_details = next(t for t in tools if t.name == "get_activity_details").description
        check("without preview, activity details describe a ride's power and climbs",
              "intensity factor" in plain_details and "VAM" in plain_details)
        check("without preview, activity details don't promise a run's weather and shoes",
              "shoes worn" not in plain_details)
        check("without preview, get_fitness mentions FTP",
              "FTP" in next(t for t in tools if t.name == "get_fitness").description)
    finally:
        preview.reset(token)
    token = preview.use(True)
    try:
        listed = await server.mcp.list_tools()
        check("with preview, get_plan has its view", view_of(listed) is not None)
        check("with preview, the preview-only tools are offered",
              {"get_shoes", "get_recovery_trends"} <= {t.name for t in listed})
        check("with preview, activity details describe a run's extras too",
              "shoes worn" in next(t for t in listed if t.name == "get_activity_details").description)
        preview_description = next(t for t in listed if t.name == "get_plan").description
        check("get_plan says it covers any plan on the calendar", "coach" in preview_description,
              preview_description[:80])
        check("get_plan asks to be called fresh and says it draws a card",
              "earlier answer goes stale" in preview_description and "plan card" in preview_description)
        check("get_plan says to check before saying a session isn't in Garmin",
              "don't guess" in preview_description and "gym app" in preview_description
              and "sets and weights aren't needed" in preview_description)
        progress_description = next(t for t in listed if t.name == "get_progress").description
        check("with preview, get_progress says the same, after its own description",
              progress_description.startswith("Which planned sessions") and "plan card" in progress_description,
              progress_description[-120:])
        old_copy = list(await server.mcp.read_resource("ui://garmin/plan/0000000000"))
        current = list(await server.mcp.read_resource(server.PLAN_VIEW))
        check("an older view address still serves the current view",
              old_copy and old_copy[0].content == current[0].content
              and old_copy[0].mime_type == "text/html;profile=mcp-app")
        check("with preview, the picture chart is left to the card",
              not any(t.name == "get_plan_chart" for t in listed))
        progress_tool = next(t for t in listed if t.name == "get_progress")
        check("with preview, get_progress draws the same view",
              ((getattr(progress_tool, "meta", None) or {}).get("ui") or {}) == view_of(listed))
    finally:
        preview.reset(token)


async def check_calendar_fallback(check) -> None:
    """With no plan made here, a preview account sees its calendar as the plan.

    Dates are relative to today, so this doesn't go stale. The calendar is
    stubbed at the one seam get_plan reads it through.
    """
    from datetime import date as _d, timedelta as _td
    from garmin_mcp import preview, server, session as session_mod
    from tests.fake_garmin import FakeGarmin

    today = _d.today()
    monday = today - _td(days=today.weekday())
    items = [
        {"id": 1, "date": (monday - _td(days=6)).isoformat(), "workoutId": 555001,
         "title": "Easy 8k", "sportTypeKey": "running"},
        {"id": 2, "date": (monday + _td(days=9)).isoformat(), "workoutId": 555001,
         "title": "Tempo 6k", "sportTypeKey": "running"},
        {"id": 3, "date": (monday + _td(weeks=20)).isoformat(), "workoutId": 555001,
         "title": "Far off", "sportTypeKey": "running"},
    ]

    async def calendar(start, end):
        return [dict(i) for i in items]

    class RecentRuns(FakeGarmin):
        """Last week's planned run, done, and a run nobody planned the day after."""

        def get_activities_by_date(self, start, end):
            return [
                {"activityId": 901, "startTimeLocal": f"{monday - _td(days=6)} 07:00:00",
                 "activityName": "Easy", "activityType": {"typeKey": "running"},
                 "duration": 3000, "distance": 8000},
                {"activityId": 902, "startTimeLocal": f"{monday - _td(days=5)} 12:30:00",
                 "activityName": "Lunch run", "activityType": {"typeKey": "running"},
                 "duration": 1800, "distance": 5200},
            ]

    saved = server._plan_calendar, session_mod.build_client
    os.environ.setdefault("GARMIN_EMAIL", "test@example.com")
    os.environ.setdefault("GARMIN_PASSWORD", "hunter2")
    server._plan_calendar = calendar
    session_mod.build_client = lambda **_: RecentRuns()
    session_mod.session.reset()  # the next call builds the client above
    try:
        token = preview.use(False)
        try:
            off = await server.get_plan()
            status_off = await server.get_connection_status()
        finally:
            preview.reset(token)
        check("without preview, get_plan reads the calendar too", "error" not in off, str(off)[:120])
        check("without preview, status is unchanged", "preview_features" not in status_off, str(status_off)[:120])

        token = preview.use(True)
        try:
            cal = await server.get_plan()
            named = await server.get_plan(label="HM")
            status_on = await server.get_connection_status()
        finally:
            preview.reset(token)
        check("with preview, the calendar stands in for a plan",
              cal.get("source") == "calendar" and "label" not in cal, str(cal)[:160])
        check("the calendar plan is the weeks around this one",
              cal.get("sessions_total") == 2 and cal.get("weeks")
              and not any(s["name"] == "Far off" for w in cal["weeks"] for s in w["sessions"]),
              str(cal.get("sessions_total")))
        check("the calendar plan names what is next",
              (cal.get("next_session") or {}).get("name") == "Tempo 6k", str(cal.get("next_session")))
        check("asking for a plan by code still says it isn't there",
              "error" in named and named.get("source") != "calendar", str(named)[:120])
        check("status says preview is on", status_on.get("preview_features") == "on", str(status_on)[:120])
        last_week = next((w for w in cal.get("weeks", []) if w["starts"] == (monday - _td(weeks=1)).isoformat()), {})
        # 15 minutes at the default easy pace, then 5 x 1 km: the fake workout's steps.
        check("each week carries the distance planned, from the workout's steps",
              last_week.get("planned_km") == 7.9 and last_week["sessions"][0].get("planned_km") == 7.9,
              str(last_week)[:200])
        check("and the distance run, counting the run nobody planned",
              last_week.get("run_km") == 13.2, str(last_week.get("run_km")))
        check("the unplanned run is listed on its day",
              [x.get("name") for x in last_week.get("extra") or []] == ["Lunch run"], str(last_week.get("extra")))
        check("a session beyond the horizon is left out",
              not any(s["name"] == "Far off" for w in cal["weeks"] for s in w["sessions"]))

        # A plan made here that starts next week, with this week's sessions on
        # the calendar from elsewhere: the week someone is in must not vanish.
        this_week = monday + _td(days=3)
        with_plan = [
            {"id": 11, "date": this_week.isoformat(), "workoutId": 555001,
             "title": "10km easy + strides", "sportTypeKey": "running"},
            {"id": 12, "date": (monday + _td(days=8)).isoformat(), "workoutId": 555001,
             "title": "12km easy · BASE", "sportTypeKey": "running"},
            {"id": 13, "date": (monday + _td(days=13)).isoformat(), "workoutId": 555001,
             "title": "16km long run · BASE", "sportTypeKey": "running"},
        ]

        async def calendar_with_plan(start, end):
            return [dict(i) for i in with_plan]

        server._plan_calendar = calendar_with_plan
        token = preview.use(True)
        try:
            mixed = await server.get_plan()
            by_code = await server.get_plan(label="BASE")
        finally:
            preview.reset(token)
        token = preview.use(False)
        try:
            plain = await server.get_plan()
        finally:
            preview.reset(token)
        names = [s["name"] for w in mixed.get("weeks", []) for s in w["sessions"]]
        check("a plan that starts next week doesn't hide this week",
              mixed.get("source") == "calendar" and "10km easy + strides" in names, str(names))
        check("its sessions follow on, without their code",
              names[1:] == ["12km easy", "16km long run"], str(names))
        check("and it is named as coming up",
              (mixed.get("upcoming_plan") or {}).get("label") == "BASE"
              and mixed["upcoming_plan"].get("starts") == (monday + _td(days=8)).isoformat(),
              str(mixed.get("upcoming_plan")))
        check("asked for by code, the plan alone is shown",
              by_code.get("label") == "BASE" and by_code.get("sessions_total") == 2, str(by_code)[:120])
        check("without preview, the calendar is the plan too",
              plain.get("source") == "calendar" and (plain.get("upcoming_plan") or {}).get("label") == "BASE",
              str(plain)[:120])

        async def scenario(entries):
            async def cal(start, end):
                return [dict(i) for i in entries]
            server._plan_calendar = cal
            token = preview.use(True)
            try:
                return await server.get_plan()
            finally:
                preview.reset(token)

        def row(n, offset, title):
            return {"id": n, "date": (monday + _td(days=offset)).isoformat(), "workoutId": 555001,
                    "title": title, "sportTypeKey": "running"}

        # A plan made here running this week, with a run club session on top.
        running = await scenario([row(21, 1, "Easy 8k · HM"), row(22, 3, "Run club"),
                                  row(23, 5, "Long 16k · HM"), row(24, 9, "Tempo 6k · HM")])
        names = [x["name"] for w in running.get("weeks", []) for x in w["sessions"]]
        check("a running plan heads the card and nothing else scheduled is dropped",
              names == ["Easy 8k", "Run club", "Long 16k", "Tempo 6k"]
              and (running.get("current_plan") or {}).get("label") == "HM"
              and running["current_plan"].get("week") == 1 and running["current_plan"].get("weeks_total") == 2,
              f"{names} {running.get('current_plan')}")

        # A plan that finished a fortnight ago, and a coach's session next week.
        after = await scenario([row(31, -13, "Easy 8k · OLD"), row(32, -11, "Long 16k · OLD"),
                                row(33, 8, "Coach easy 10k")])
        names = [x["name"] for w in after.get("weeks", []) for x in w["sessions"]]
        check("a finished plan names nothing and doesn't hide what's next",
              "Coach easy 10k" in names and "current_plan" not in after and "upcoming_plan" not in after
              and (after.get("next_session") or {}).get("name") == "Coach easy 10k",
              f"{names} {after.get('next_session')}")

        # Asked again within minutes, the workouts aren't fetched again; after
        # anything is changed through the tools, they are.
        lookups = []
        real_lookup = RecentRuns.get_workout_by_id
        def counting(self, workout_id):
            lookups.append(workout_id)
            return real_lookup(self, workout_id)
        RecentRuns.get_workout_by_id = counting
        try:
            session_mod.session.forget()
            await scenario([row(51, 1, "Easy 8k"), row(52, 3, "Tempo 6k")])
            first = len(lookups)
            await scenario([row(51, 1, "Easy 8k"), row(52, 3, "Tempo 6k")])
            again = len(lookups) - first
            token = preview.use(True)
            try:
                await server.unschedule_workout(date=(monday + _td(days=40)).isoformat())
            finally:
                preview.reset(token)
            await scenario([row(51, 1, "Easy 8k"), row(52, 3, "Tempo 6k")])
            after_write = len(lookups) - first - again
        finally:
            RecentRuns.get_workout_by_id = real_lookup
        check("a second look within minutes reads no workouts again", first >= 1 and again == 0,
              f"first={first} again={again}")
        check("after a change through the tools, workouts are read fresh", after_write == first,
              f"after_write={after_write}")

        # A timed strength session has no distance, however its steps add up.
        gym = await scenario([row(61, 1, "Easy 8k"),
                              {**row(62, 2, "Full body (runner)"), "sportTypeKey": "strength_training"}])
        week = next((w for w in gym.get("weeks", []) if w.get("current")), {})
        strength_row = next((x for x in week.get("sessions", []) if x["name"] == "Full body (runner)"), {})
        check("a strength session adds nothing to planned km",
              "planned_km" not in strength_row and week.get("planned_km") == 7.9, str(week)[:200])

        # Nothing scheduled at all: a training log from what was run, every
        # week shown, with the way to a plan.
        log = await scenario([])
        check("with nothing scheduled, the card gets a training log",
              log.get("source") == "log" and len(log.get("weeks", [])) == 4 and log.get("runs") == 2
              and log.get("run_km") == 13.2, str({k: log.get(k) for k in ("source", "runs", "run_km")}))
        check("the log names the longest run and says how to get a plan",
              (log.get("longest") or {}).get("actual_km") == 8.0 and "create_plan" in (log.get("note") or ""),
              str(log.get("longest")))
        check("weeks with no running stay in the log as gaps",
              sum(1 for w in log["weeks"] if w["runs"] == 0) == 3, str([w["runs"] for w in log["weeks"]]))
        token = preview.use(False)
        try:
            plain_empty = await server.get_plan()
        finally:
            preview.reset(token)
        check("without preview, nothing scheduled gets the training log too",
              plain_empty.get("source") == "log", str(plain_empty)[:100])

        # Only next week scheduled, by anyone: still a card.
        nxt = await scenario([row(41, 8, "Easy 10k"), row(42, 10, "Intervals 6x800")])
        check("next week alone is still a plan",
              nxt.get("source") == "calendar" and nxt.get("sessions_total") == 2
              and (nxt.get("next_session") or {}).get("name") == "Easy 10k", str(nxt)[:120])
    finally:
        server._plan_calendar, session_mod.build_client = saved


def check_chart_config_read_only(check) -> None:
    """The chart's settings are read, never written: hosted, the folder is read-only."""
    import stat
    from pathlib import Path as _P
    from garmin_mcp import plan as plan_mod

    saved = plan_mod.CONFIG
    with tempfile.TemporaryDirectory() as tmp:
        folder = _P(tmp) / "readonly"
        folder.mkdir()
        folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            plan_mod.CONFIG = folder / "plan-config.json"
            try:
                config = plan_mod.load_config()
                ok, detail = config.get("weekly_target_km") == 50, str(config)
            except OSError as exc:
                ok, detail = False, repr(exc)
            check("the chart works from a folder it can't write to",
                  ok and not plan_mod.CONFIG.exists(), detail)
            folder.chmod(stat.S_IRWXU)
            plan_mod.CONFIG.write_text('{"weekly_target_km": 62}')
            check("a settings file someone wrote is still read",
                  plan_mod.load_config().get("weekly_target_km") == 62)
        finally:
            folder.chmod(stat.S_IRWXU)
            plan_mod.CONFIG = saved


def check_stream_km_splits(check) -> None:
    """A 7 km steady lap recorded by a watch that did not auto-lap.

    The splits endpoint gives one row; the recording gives seven kilometres
    and the drift across the whole effort. Even pace at 4:30/km, HR 150 to 162.
    """
    from garmin_mcp import stream
    from tests.fake_garmin import _stream

    recording = _stream([(7000.0, 1890.0, 150.0, 162.0)], step=5.0)
    laps = [{"lapIndex": 1, "distance": 7000.0, "duration": 1890.0}]
    inside = stream.analyse(recording, laps) or {}
    splits = inside.get("km_splits") or []
    check("a single long lap yields per-km splits", len(splits) == 7, str(len(splits)))
    check("km split pace is the even pace that was run",
          splits and all(s["pace_per_km"] == "4:30 /km" for s in splits),
          str([s.get("pace_per_km") for s in splits]))
    check("km split HR rises through the effort",
          splits and splits[0]["avg_hr"] < splits[-1]["avg_hr"],
          str([s.get("avg_hr") for s in splits]))
    drift = (inside.get("laps") or [{}])[0]
    check("whole-lap drift is reported", 7 <= (drift.get("hr_drift_bpm") or 0) <= 9, str(drift))


def check_entrypoints_ignore_cwd(check) -> None:
    """The scripts have to work from any directory, not just the project root.

    garmin_mcp is not installed into .venv — only its dependencies are — so a
    bare `python -m garmin_mcp.x` resolves only when the current directory
    happens to be the project. Every script that launches a module therefore
    has to put the project on PYTHONPATH itself.

    Three shipped without it. They worked for anyone who had cd'd into the
    project first, and failed with ModuleNotFoundError for the first person who
    ran one by absolute path from their home directory — which is exactly what
    bootstrap.sh and the doctor's own advice both tell people to do.
    """
    launches_module = re.compile(r"-m\s+garmin_mcp\b")
    # An assignment, not a passing mention: the comments here say "PYTHONPATH"
    # too, and matching those would let the bug back in under its own docs.
    sets_path = re.compile(r"^\s*(export\s+)?PYTHONPATH=", re.M)
    enters_project = re.compile(r'^\s*cd "\$PROJECT"', re.M)
    for script in sorted((ROOT / "scripts").glob("*.sh")):
        body = script.read_text()
        if not launches_module.search(body):
            continue
        resolves = sets_path.search(body) or enters_project.search(body)
        check(f"{script.name} sets the import path", bool(resolves))

    # And prove it end to end, rather than trusting the pattern match. login.sh
    # is the one entry point that is safe to invoke here: with no terminal it
    # stops at its own TTY check, which is already past the import.
    with tempfile.TemporaryDirectory() as elsewhere:
        proc = subprocess.run(
            [str(ROOT / "scripts" / "login.sh")],
            cwd=elsewhere,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    output = (proc.stdout + proc.stderr).strip()
    check(
        "login.sh imports when run from elsewhere",
        "ModuleNotFoundError" not in output,
        output[:160],
    )


def check_prompts_survive_a_pipe(check) -> None:
    """`curl ... | bash` leaves the installer's stdin attached to the download.

    That is the documented way to install this, and it means stdin is the script
    being downloaded, not the keyboard. Every prompt therefore has to read the
    terminal explicitly. The login step didn't: it saw no terminal, printed
    "This command needs a terminal", and exited 2 — which under `set -e` took the
    whole install down before Claude Desktop was ever configured, while the
    person sat looking at a shell prompt typing their email into zsh.

    The redirect has to be per command. `exec < /dev/tty` would also move where
    bash reads the rest of the script from, which a piped install cannot survive.
    """
    def code_only(path: Path) -> str:
        """Drop comments: they discuss `exec < /dev/tty` in order to warn you off
        it, and matching prose would fail the check that guards against it."""
        return "\n".join(
            line for line in path.read_text().splitlines()
            if not line.lstrip().startswith("#")
        )

    boot = code_only(ROOT / "scripts" / "bootstrap.sh")
    check(
        "bootstrap.sh points the login prompt at the terminal",
        bool(re.search(r'-m\s+garmin_mcp\.login\s*<\s*"\$TTY_IN"', boot)),
    )
    check(
        "bootstrap.sh does not move its own stdin",
        not re.search(r"exec\s*<\s*/dev/tty", boot),
    )
    install = code_only(ROOT / "scripts" / "install-claude-desktop.sh")
    check(
        "install-claude-desktop.sh points its read at the terminal",
        bool(re.search(r'read\b[\s\S]{0,200}?<\s*"\$\{GARMIN_MCP_TTY', install)),
    )

    # Prove the mechanism end to end, in the shape that broke: bash is reading
    # the script from stdin, and the prompt still collects an answer because it
    # reads TTY_IN instead. A file stands in for the terminal so this needs no pty.
    with tempfile.TemporaryDirectory() as tmp:
        answers = Path(tmp) / "answers"
        answers.write_text("typed@example.com\n")
        script = Path(tmp) / "piped.sh"
        script.write_text(
            "set -euo pipefail\n"
            f'TTY_IN="{answers}"\n'
            'read -r -p "Garmin email: " got < "$TTY_IN"\n'
            'echo "GOT:$got"\n'
        )
        with open(script) as piped:
            proc = subprocess.run(
                ["bash"], stdin=piped, capture_output=True, text=True, timeout=30
            )
    check(
        "a piped script can still collect an answer",
        "GOT:typed@example.com" in proc.stdout,
        (proc.stdout + proc.stderr).strip()[:160],
    )


async def main() -> int:
    env = dict(os.environ)
    env["GARMIN_MCP_FAKE"] = "1"  # server uses the stub client
    env.pop("GARMIN_EMAIL", None)
    env.pop("GARMIN_PASSWORD", None)
    env["PYTHONPATH"] = str(ROOT)

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests" / "fake_server.py")],
        env=env,
        cwd=str(ROOT),
    )

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
        if not ok:
            failures.append(name)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as sess:
            await sess.initialize()

            tools = await sess.list_tools()
            names = {t.name for t in tools.tools}
            print("\ntools advertised:", ", ".join(sorted(names)))
            check("all tools registered", names == EXPECTED_TOOLS, str(names))
            check(
                "every tool documented",
                all(t.description for t in tools.tools),
            )

            check("an ordinary account is offered the plan view",
                  any(((t.meta or {}).get("ui")) for t in tools.tools if t.name == "get_plan"))
            plain_plan = await sess.call_tool("get_progress", {"weeks": 2})
            check("an ordinary account's answer carries the card note",
                  "for_the_assistant" in plain_plan.content[0].text)

            # Ride features are everyone's now; a run's extras are still preview.
            plain_ride = payload(await sess.call_tool("get_activity_details", {"activity_id": 2222}))
            check("an ordinary account's ride has FTP, power zones and work",
                  (plain_ride.get("ftp") or {}).get("watts") == 250 and plain_ride.get("power_zones")
                  and plain_ride["summary"]["power"].get("work_kj") == 684, str({k: plain_ride.get(k) for k in ("ftp", "power_zones")})[:200])
            plain_run = payload(await sess.call_tool("get_activity_details", {"activity_id": 4444}))
            check("an ordinary account's run has no weather, shoes or route yet",
                  not ({"weather", "shoes", "same_route", "terrain"} & set(plain_run)), str(sorted(plain_run)))
            plain_fit = payload(await sess.call_tool("get_fitness", {}))
            check("an ordinary account's fitness has the cycling FTP",
                  (plain_fit.get("cycling_ftp") or {}).get("watts") == 250, str(plain_fit.get("cycling_ftp")))

            # The picture chart, end to end: it used to fail on the hosted
            # server before drawing anything.
            chart = await sess.call_tool("get_plan_chart", {})
            kinds = [getattr(c, "type", None) for c in chart.content]
            check("the plan chart comes back as an image", "image" in kinds,
                  str(chart.content)[:160])

            print("\nget_connection_status")
            status = payload(await sess.call_tool("get_connection_status"))
            print("   ", json.dumps(status, indent=2)[:400])
            check("reports authenticated", status.get("authenticated") is True)
            check("account is masked", str(status.get("account", "")).count("*") > 0)
            blob = json.dumps(status).lower()
            check("no token in response", "di_refresh_token" not in blob)
            check("no password in response", "hunter2" not in blob)

            print("\nget_daily_summary")
            day = payload(await sess.call_tool("get_daily_summary", {"date": "2026-09-22"}))
            print("   ", json.dumps(day, indent=2)[:400])
            check("steps", day.get("steps") == 12345)
            check("resting hr", day["heart_rate"]["resting_bpm"] == 48)
            check("body battery", day["body_battery"]["most_recent"] == 71)
            check("calories", day["calories"]["active"] == 820)

            print("\nget_sleep_data")
            sleep = payload(await sess.call_tool("get_sleep_data", {"date": "2026-09-22"}))
            print("   ", json.dumps(sleep, indent=2)[:500])
            check("total sleep formatted", sleep.get("total_sleep") == "7h 12m 00s")
            check("score", sleep["score"]["overall"] == 82)
            check("deep stage percent", sleep["stages"]["deep"]["percent"] == 17.4)

            print("\nget_activities")
            acts = payload(await sess.call_tool("get_activities", {"limit": 2}))
            print("   ", json.dumps(acts, indent=2)[:600])
            check("count honours limit", acts.get("count") == 2)
            first = acts["activities"][0]
            check("distance km", first["distance_km"] == 10.05)
            check("pace", first["pace_per_km"] == "5:12 /km")
            check("hr zones from list row", len(first.get("hr_zones") or []) == 3)
            check("running dynamics surfaced",
                  first["running_dynamics"]["ground_contact_ms"] == 218
                  and first["running_dynamics"]["vertical_oscillation_cm"] == 9.5)
            check("running power surfaced", first["power"]["normalized_w"] == 400)

            spin = payload(await sess.call_tool("get_activities", {"limit": 3}))["activities"][1]
            check("a ride reads by speed and rpm, not pace and steps",
                  spin.get("avg_speed_kmh") == 22.7 and "pace_per_km" not in spin
                  and spin.get("avg_cadence_rpm") == 82 and "avg_cadence_spm" not in spin, str(spin))
            check("a ride's power has normalised, max, IF, TSS and best efforts",
                  spin["power"].get("normalized_w") == 195 and spin["power"].get("max_w") == 650
                  and spin["power"].get("intensity_factor") == 0.78
                  and spin["power"].get("training_stress_score") == 72
                  and spin["power"].get("best_efforts_w", {}).get("20min") == 210
                  and spin["power"].get("variability_index") == 1.08, str(spin.get("power")))

            ranged = payload(
                await sess.call_tool(
                    "get_activities",
                    {"limit": 2, "start_date": "2026-09-20", "end_date": "2026-09-22"},
                )
            )
            check("date range honoured", ranged.get("window") == {
                "from": "2026-09-20", "to": "2026-09-22"})
            check("range truncates to limit", ranged.get("count") == 2
                  and ranged.get("truncated") is True)

            print("\nget_activity_details")
            detail = payload(
                await sess.call_tool("get_activity_details", {"activity_id": 1111})
            )
            print("   ", json.dumps(detail, indent=2)[:600])
            check("splits returned", detail.get("splits_count") == 2)
            check("split pace", detail["splits"][0]["pace_per_km"] == "5:00 /km")
            check("per-split dynamics, for watching GCT drift across reps",
                  detail["splits"][0]["running_dynamics"]["ground_contact_ms"] == 230)
            check("hr zones", detail["hr_zones"][0]["percent"] == 25.0)
            inside = detail.get("inside") or {}
            lap1 = (inside.get("laps") or [{}])[0]
            check("the recording gives HR drift inside a lap",
                  9 <= (lap1.get("hr_drift_bpm") or 0) <= 14, str(lap1))
            check("drift comes with the paces it happened at",
                  lap1.get("pace_first_third") == "5:00 /km" and lap1.get("pace_last_third") == "5:00 /km",
                  str(lap1))
            check("no km splits when the watch already lapped by km",
                  "km_splits" not in inside, str(list(inside)))
            check("an ordinary account's run details are as before: no weather, shoes or terrain",
                  not ({"weather", "shoes", "terrain", "same_route"} & set(detail)), str(list(detail)))
            check_stream_km_splits(check)
            check_conditions(check)
            check_terrain_ignores_stops(check)
            check_shoes_and_nights(check)

            print("\nworkouts")
            listed = payload(await sess.call_tool("list_workouts", {"limit": 5}))
            check("workouts listed", listed.get("count") == 1
                  and listed["workouts"][0]["workout_id"] == 555001)

            created = payload(
                await sess.call_tool(
                    "create_workout",
                    {
                        "name": "Thursday Threshold",
                        "sport": "running",
                        "steps": [
                            {"type": "warmup", "duration_seconds": 900},
                            {"type": "repeat", "times": 5, "steps": [
                                {"type": "interval", "distance_meters": 1000,
                                 "pace": "4:05"},
                                {"type": "recovery", "duration_seconds": 90},
                            ]},
                            {"type": "cooldown", "duration_seconds": 600},
                        ],
                    },
                )
            )
            print("   ", json.dumps(created, indent=2)[:500])
            check("workout created", created.get("workout_id") == 555002)
            check("duration estimated", created.get("estimated_duration") == "52m 55s")
            check("summary renders repeat", "5 x" in created.get("summary", ""))

            scheduled = payload(
                await sess.call_tool(
                    "schedule_workout", {"workout_id": 555002, "date": "tomorrow"}
                )
            )
            check("workout scheduled", scheduled.get("schedule_id") == 777001
                  and scheduled.get("scheduled_for"))

            print("\nupdate_workout")
            renamed = payload(await sess.call_tool(
                "update_workout", {"workout_id": 555001, "name": "Thursday Tempo"}))
            check("rename keeps the id", renamed.get("workout_id") == 555001
                  and renamed.get("name") == "Thursday Tempo" and renamed.get("changed") == ["name"],
                  str(renamed)[:200])
            relisted = payload(await sess.call_tool("list_workouts", {"limit": 5}))
            check("the library shows the new name",
                  relisted["workouts"][0]["name"] == "Thursday Tempo", str(relisted)[:200])
            rebuilt = payload(await sess.call_tool("update_workout", {
                "workout_id": 555001,
                "steps": [{"type": "warmup", "duration_seconds": 600},
                          {"type": "interval", "distance_meters": 5000, "pace": ["4:20", "4:30"]},
                          {"type": "cooldown", "duration_seconds": 600}],
            }))
            check("new steps rebuild the workout and report the shape",
                  "5.00 km" in rebuilt.get("summary", "") and rebuilt.get("changed") == ["steps"]
                  and rebuilt.get("estimated_duration"), str(rebuilt)[:300])
            nothing = payload(await sess.call_tool("update_workout", {"workout_id": 555001}))
            check("an update with nothing to change is refused", "error" in nothing, str(nothing))
            missing = payload(await sess.call_tool(
                "update_workout", {"workout_id": 424242, "name": "Ghost"}))
            check("updating an unknown workout errors", "error" in missing, str(missing))

            # -- progress ------------------------------------------------
            # Before the removal checks below, which delete 555001 out from
            # under the scheduled session this reads.
            # The stub's runs have fixed dates (week of 2026-09-14 and the
            # next), so ask for enough weeks to reach back to them from today.
            from datetime import date as _pd, timedelta as _ptd
            this_monday = _pd.today() - _ptd(days=_pd.today().weekday())
            prog_weeks = (this_monday - _pd(2026, 9, 14)).days // 7 + 1
            prog = payload(await sess.call_tool("get_progress", {"weeks": prog_weeks}))
            check("progress reports the planned session",
                  prog.get("planned") == 1, str(prog)[:200])
            # The stub's session is scheduled two days after its nearest run,
            # which is outside the one-day window — so it is missed, and the
            # runs that did happen are unplanned rather than silently credited.
            check("a session two days from any run is missed",
                  prog.get("completed") == 0 and len(prog.get("missed", [])) == 1,
                  str(prog.get("missed"))[:160])
            check("the runs that happened are reported as unplanned",
                  len(prog.get("unplanned", [])) == 3,
                  str(len(prog.get("unplanned", []))))
            check("weeks are rolled up", len(prog.get("weeks", [])) == prog_weeks
                  and prog["weeks"][-1]["current"] is True,
                  str(prog.get("weeks"))[:200])

            # -- training plan -------------------------------------------
            print("\ntraining plan")
            from datetime import date as _d, timedelta as _td
            day = lambda n: (_d.today() + _td(days=n)).isoformat()
            easy = [{"type": "interval", "duration_seconds": 2400, "pace": ["5:20", "5:40"]}]
            thr = [{"type": "warmup", "duration_seconds": 900},
                   {"type": "repeat", "times": 5, "steps": [
                       {"type": "interval", "distance_meters": 1000, "pace": "4:05"},
                       {"type": "recovery", "duration_seconds": 90}]},
                   {"type": "cooldown", "duration_seconds": 600}]
            block = [
                {"date": day(8), "name": "Long run 16k", "steps": easy},
                {"date": day(2), "name": "Easy 8k", "steps": easy},
                {"date": day(4), "name": "Threshold 5x1k", "steps": thr},
                {"date": day(9), "name": "Easy 8k", "steps": easy},
            ]
            bad_block = payload(await sess.call_tool("create_plan", {
                "goal": "Half marathon, 1:40",
                "sessions": block + [{"date": day(10), "name": "Broken", "steps": [{"type": "interval"}]}],
            }))
            check("a bad session fails the whole plan before anything is created",
                  "error" in bad_block and "Session 5" in bad_block["error"], str(bad_block)[:160])
            past = payload(await sess.call_tool("create_plan", {
                "goal": "Half", "sessions": [{"date": day(-3), "name": "Too late", "steps": easy}]}))
            check("a session in the past is refused", "error" in past and "passed" in past["error"], str(past)[:140])

            made = payload(await sess.call_tool("create_plan", {
                "goal": "Half marathon, 1:40, mid November", "sessions": block}))
            print("   ", json.dumps(made, indent=2)[:500])
            check("plan created with a code from the goal",
                  made.get("label") == "HM" and made.get("sessions_created") == 4, str(made)[:200])
            check("sessions come back in date order",
                  [s["date"] for s in made.get("first_sessions", [])] == sorted(s["date"] for s in block),
                  str(made.get("first_sessions"))[:200])
            again = payload(await sess.call_tool("create_plan", {
                "goal": "Half marathon", "sessions": block}))
            check("the same code in the same window is refused",
                  "error" in again and "already" in again["error"], str(again)[:160])

            got = payload(await sess.call_tool("get_plan", {"label": "HM"}))
            print("   ", json.dumps(got, indent=2)[:700])
            check("get_plan finds the plan by its code",
                  got.get("label") == "HM" and got.get("sessions_total") == 4, str(got)[:200])
            check("the goal is read back from Garmin",
                  got.get("goal") == "Half marathon, 1:40, mid November", str(got.get("goal")))
            check("the next session is the earliest ahead",
                  (got.get("next_session") or {}).get("date") == day(2)
                  and (got.get("next_session") or {}).get("name") == "Easy 8k",
                  str(got.get("next_session")))
            check("names come back without the plan code",
                  all(" · " not in s["name"] for w in got.get("weeks", []) for s in w["sessions"]))
            check("every session carries the ids needed to move or retune it",
                  all(s.get("workout_id") and s.get("schedule_id")
                      for w in got.get("weeks", []) for s in w["sessions"]))
            check("nothing is done or missed yet",
                  got.get("completed") == 0 and got.get("missed") == 0 and got.get("remaining") == 4)

            thr_row = next(s for w in got["weeks"] for s in w["sessions"] if s["name"] == "Threshold 5x1k")
            retuned = payload(await sess.call_tool("update_workout", {
                "workout_id": thr_row["workout_id"], "name": "Threshold 6x1k"}))
            check("renaming a plan session keeps its plan code",
                  retuned.get("name") == "Threshold 6x1k · HM", str(retuned)[:160])
            got2 = payload(await sess.call_tool("get_plan", {"label": "hm"}))
            check("the renamed session is still in the plan",
                  any(s["name"] == "Threshold 6x1k" for w in got2["weeks"] for s in w["sessions"])
                  and got2.get("sessions_total") == 4, str(got2)[:200])

            ask = payload(await sess.call_tool("remove_plan", {"label": "HM"}))
            check("remove_plan asks first",
                  ask.get("confirmation_required") is True and ask.get("would_remove") == 4, str(ask))
            still = payload(await sess.call_tool("get_plan", {"label": "HM"}))
            check("an unconfirmed remove changes nothing", still.get("sessions_total") == 4)
            removed = payload(await sess.call_tool("remove_plan", {"label": "HM", "confirm": "hm"}))
            check("confirmed remove takes every future session", removed.get("removed") == 4, str(removed))
            gone_plan = payload(await sess.call_tool("get_plan", {"label": "HM"}))
            check("the plan is gone afterwards", "error" in gone_plan, str(gone_plan)[:140])
            check_plan_summary(check)
            check_chart_config_read_only(check)
            await check_preview_gate(check)
            await check_calendar_fallback(check)

            # -- removal ------------------------------------------------
            # Unscheduling is the reversible one: off the calendar, workout
            # kept. Resolved from the date, which is how a person refers to it.
            cleared = payload(
                await sess.call_tool("unschedule_workout", {"date": "2026-09-24"})
            )
            check("unscheduled by date", cleared.get("unscheduled") == 900001,
                  str(cleared)[:140])
            check("unschedule keeps the workout",
                  "kept" in (cleared.get("note") or ""))

            empty_day = payload(
                await sess.call_tool("unschedule_workout", {"date": "2026-09-25"})
            )
            check("nothing scheduled reports cleanly",
                  empty_day.get("unscheduled") is None and "error" not in empty_day,
                  str(empty_day)[:140])

            # The gate. An unconfirmed call must not delete, and the proof is
            # that the workout is still listed afterwards — not merely that the
            # response said so.
            unconfirmed = payload(
                await sess.call_tool("delete_workout", {"workout_id": 555001})
            )
            check("delete asks first",
                  unconfirmed.get("confirmation_required") is True
                  and unconfirmed.get("name") == "Thursday Threshold",
                  str(unconfirmed)[:160])

            still_there = payload(await sess.call_tool("list_workouts", {}))
            check("unconfirmed delete left the workout alone",
                  any(w.get("workout_id") == 555001
                      for w in still_there.get("workouts", [])),
                  str(still_there)[:160])

            wrong = payload(
                await sess.call_tool(
                    "delete_workout",
                    {"workout_id": 555001, "confirm": "Something Else"},
                )
            )
            check("wrong name does not delete",
                  wrong.get("confirmation_required") is True, str(wrong)[:140])

            gone = payload(
                await sess.call_tool(
                    "delete_workout",
                    # Deliberately different spacing and case: the check has to
                    # be about identity, not transcription.
                    {"workout_id": 555001, "confirm": "thursday   threshold"},
                )
            )
            check("confirmed delete goes through",
                  gone.get("deleted") == 555001, str(gone)[:140])

            after = payload(await sess.call_tool("list_workouts", {}))
            check("deleted workout is gone from the library",
                  not any(w.get("workout_id") == 555001
                          for w in after.get("workouts", [])),
                  str(after)[:160])

            missing = payload(
                await sess.call_tool("delete_workout", {"workout_id": 555001})
            )
            check("deleting an unknown workout errors",
                  "error" in missing, str(missing)[:140])

            bad_step = payload(
                await sess.call_tool(
                    "create_workout",
                    {"name": "Broken", "steps": [{"type": "interval"}]},
                )
            )
            check("step with no duration rejected",
                  "duration_seconds" in bad_step.get("error", ""),
                  str(bad_step)[:120])

            bad_pace = payload(
                await sess.call_tool(
                    "create_workout",
                    {"name": "Broken", "steps": [
                        {"type": "interval", "distance_meters": 1000,
                         "pace": "0:30"}]},
                )
            )
            check("implausible pace rejected", "error" in bad_pace,
                  str(bad_pace)[:120])

            nested = payload(
                await sess.call_tool(
                    "create_workout",
                    {"name": "Broken", "steps": [
                        {"type": "repeat", "times": 2, "steps": [
                            {"type": "repeat", "times": 2, "steps": [
                                {"type": "interval", "duration_seconds": 60}]}]}]},
                )
            )
            check("nested repeat rejected", "error" in nested, str(nested)[:120])

            print("\nreadiness")
            ready = payload(await sess.call_tool("get_readiness", {"date": "today"}))
            print("   ", json.dumps(ready, indent=2)[:700])
            check("readiness score and level", ready["readiness"]["score"] == 58
                  and ready["readiness"]["level"] == "Moderate", str(ready.get("readiness")))
            check("training status read off the phrase",
                  ready["training_status"]["status"] == "Productive", str(ready.get("training_status")))
            check("acute:chronic ratio and its verdict",
                  ready["training_status"]["acute_chronic_ratio"] == 1.06
                  and ready["training_status"]["ratio_status"] == "Optimal")
            check("load focus names the shortage",
                  ready["load_focus"]["verdict"] == "Anaerobic shortage"
                  and ready["load_focus"]["low_aerobic"]["target"] == [360, 720], str(ready.get("load_focus")))
            check("hrv against its baseline", ready["hrv"]["last_night_ms"] == 61
                  and ready["hrv"]["balanced_range_ms"] == [53, 66] and ready["hrv"]["status"] == "Balanced")
            check("body battery, stress and sleep folded in",
                  ready["body_battery"]["now"] == 71 and ready["stress"]["average"] == 28
                  and ready["sleep"]["score"] == 82 and ready["sleep"]["total"] == "7h 12m 00s")
            check("readiness carries no raw device map", "3400" not in json.dumps(ready))

            print("\nfitness")
            fit = payload(await sess.call_tool("get_fitness", {}))
            print("   ", json.dumps(fit, indent=2)[:600])
            check("race predictions as times and paces",
                  fit["race_predictions"]["half_marathon"]["time"] == "1h 25m 00s"
                  and fit["race_predictions"]["5k"]["pace_per_km"] == "3:36 /km", str(fit.get("race_predictions")))
            check("lactate threshold as pace, not m/s",
                  fit["lactate_threshold"]["pace_per_km"] == "4:23 /km"
                  and fit["lactate_threshold"]["heart_rate_bpm"] == 172, str(fit.get("lactate_threshold")))
            check("endurance class read off Garmin's own boundaries",
                  fit["endurance_score"]["class"] == "Trained", str(fit.get("endurance_score")))
            check("hill score class likewise", fit["hill_score"]["class"] == "Trained", str(fit.get("hill_score")))
            check("running tolerance passed through without ids",
                  fit["running_tolerance"].get("toleranceLimit") == 61.0
                  and "userProfilePK" not in fit["running_tolerance"], str(fit.get("running_tolerance")))
            check("vo2max on fitness too", fit.get("vo2max") == 60.9)

            print("\nprofile")
            prof = payload(await sess.call_tool("get_profile", {}))
            print("   ", json.dumps(prof, indent=2)[:400])
            check("vo2max returned", prof.get("vo2max") == 60.9)
            check("5k PB decoded", prof["running_records"]["fastest_5k"] == "17m 34s")
            check("half PB decoded",
                  prof["running_records"]["fastest_half_marathon"] == "1h 19m 39s")
            check("unknown record types passed through, not guessed",
                  prof["other_records"][0]["type_id"] == 8)

            print("\ntarget placement (regression)")
            check_target_placement(check)

            print("\nstrength training")
            found = payload(await sess.call_tool("find_exercises", {"query": "bench press"}))
            names = [m["name"] for m in found.get("matches", [])]
            check("catalogue search finds exercises", "Bench Press" in names, str(names[:4]))
            check("the plainest name comes first", names[:1] == ["Bench Press"], str(names[:2]))

            # A plain substring search misses this; people type it constantly.
            hyphen = payload(await sess.call_tool("find_exercises", {"query": "pull up"}))
            check("hyphens and spacing are forgiven",
                  any(m["name"] == "Pull-up" for m in hyphen.get("matches", [])),
                  str([m["name"] for m in hyphen.get("matches", [])][:3]))

            made = payload(await sess.call_tool("create_strength_workout", {
                "name": "Push Day",
                "exercises": [
                    {"exercise": "Barbell Bench Press", "sets": 4, "reps": 8,
                     "rest_seconds": 120, "weight_kg": 70},
                    {"exercise": "Goblet Squat", "sets": 3, "reps": 12},
                ],
            }))
            check("strength workout is created", bool(made.get("workout_id")), str(made)[:140])
            check("summary reads like a session",
                  "4x8 Barbell Bench Press @ 70kg" in made.get("summary", ""),
                  made.get("summary", "")[:120])

            # A wrong guess here is the wrong movement on someone's watch, so an
            # unrecognised or ambiguous name must stop rather than pick one.
            unknown = payload(await sess.call_tool("create_strength_workout", {
                "name": "Nope",
                "exercises": [{"exercise": "flurble press", "sets": 3, "reps": 5}],
            }))
            check("an unknown exercise is refused, not guessed",
                  "error" in unknown and "find_exercises" in str(unknown),
                  str(unknown)[:140])

            # "row" and "curl" are themselves catalogue entries, so they resolve
            # and should. "press" is a family of movements and must not be
            # silently picked from.
            vague = payload(await sess.call_tool("create_strength_workout", {
                "name": "Vague",
                "exercises": [{"exercise": "press", "sets": 3, "reps": 10}],
            }))
            check("an ambiguous exercise lists the candidates",
                  "error" in vague and "matches several" in str(vague),
                  str(vague)[:140])

            print("\nentry points ignore the current directory (regression)")
            check_entrypoints_ignore_cwd(check)

            print("\nprompts survive `curl | bash` (regression)")
            check_prompts_survive_a_pipe(check)

            print("\nerror handling")
            bad_date = payload(
                await sess.call_tool("get_daily_summary", {"date": "last tuesday"})
            )
            check("bad date returns error", "error" in bad_date, str(bad_date)[:120])
            bad_id = payload(
                await sess.call_tool("get_activity_details", {"activity_id": "abc"})
            )
            check("bad id returns error", "error" in bad_id, str(bad_id)[:120])
            still_up = payload(await sess.call_tool("get_connection_status"))
            check("server still alive after errors", still_up.get("authenticated") is True)

    # The same server for an account trying preview features: the plan view
    # over the wire, as a host reads it.
    print("\npreview account, plan view")
    preview_params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests" / "fake_server.py")],
        env={**env, "GARMIN_MCP_PREVIEW": "1"},
        cwd=str(ROOT),
    )
    async with stdio_client(preview_params) as (read, write):
        async with ClientSession(read, write) as sess:
            await sess.initialize()
            tools = await sess.list_tools()
            # The plan view. A host that draws MCP Apps follows get_plan's
            # pointer to this resource; the rest ignore it.
            plan_tool = next(t for t in tools.tools if t.name == "get_plan")
            view_uri = ((plan_tool.meta or {}).get("ui") or {}).get("resourceUri")
            check("get_plan points at its view, versioned by its contents",
                  re.fullmatch(r"ui://garmin/plan/[0-9a-f]{10}", view_uri or "") is not None, str(plan_tool.meta))
            listed_views = await sess.list_resources()
            check("the view is listed as an app",
                  any(str(r.uri) == view_uri and field(r, "mime_type", "mimeType") == "text/html;profile=mcp-app"
                      for r in listed_views.resources), str(listed_views.resources)[:200])
            view = await sess.read_resource(view_uri)
            html = view.contents[0].text if view.contents else ""
            check("the view is served as html",
                  field(view.contents[0], "mime_type", "mimeType") == "text/html;profile=mcp-app"
                  and "ui/initialize" in html and "tool-result" in html, str(view.contents[0])[:120])
            check("the view loads nothing from outside the frame",
                  not re.search(r"""(src|href)=["']?https?:""", html) and "@import" not in html)

            preview_names = {t.name for t in tools.tools}
            check("a preview account is offered shoes and recovery trends",
                  {"get_shoes", "get_recovery_trends"} <= preview_names, str(sorted(preview_names)))
            details_tool = next(t for t in tools.tools if t.name == "get_activity_details")
            check("a preview account is told what run details now include",
                  "weather" in details_tool.description and "same route" in details_tool.description)

            print("\npreview account, around a run")
            hilly = payload(await sess.call_tool("get_activity_details", {"activity_id": 4444}))
            print("   ", json.dumps({k: hilly.get(k) for k in ("weather", "shoes", "same_route")}, indent=2)[:900])
            weather = hilly.get("weather") or {}
            check("weather comes in Celsius from Garmin's Fahrenheit",
                  weather.get("temperature_c") == 23.9 and weather.get("dew_point_c") == 17.2, str(weather))
            check("a metric account's wind is already km/h", weather.get("wind_kmh") == 10 and "wind_mph" not in weather,
                  str(weather))
            check("weather says what it did to the run", "humid" in (weather.get("effect") or ""), str(weather))
            check("the shoes worn are named", hilly.get("shoes") == "Pegasus", str(hilly.get("shoes")))
            ground = hilly.get("terrain") or {}
            climbs = ground.get("climbs") or []
            check("the hill is found, about the right size and where it was",
                  len(climbs) == 1 and 35 <= climbs[0].get("gain_m", 0) <= 42 and climbs[0].get("starts_at_km") in (1.9, 2.0, 2.1),
                  str(climbs))
            check("uphill pace is worth more on the flat",
                  ground["by_gradient"]["uphill"]["flat_equivalent_pace"] < ground["by_gradient"]["uphill"]["pace_per_km"],
                  str(ground.get("by_gradient")))
            check("an out-and-back is recognised, with no coordinates in the answer",
                  ground.get("route", {}).get("kind") == "out and back" and "51.5" not in json.dumps(hilly),
                  str(ground.get("route")))
            check("rising heart rate at level pace shows as decoupling",
                  5 <= (ground.get("effort") or {}).get("decoupling_pct", 0) <= 12, str(ground.get("effort")))
            same = hilly.get("same_route") or {}
            check("an earlier run of the same route is found and ranked",
                  same.get("earlier_runs") == 1 and same.get("this_run_rank") == "1 of 2 by pace"
                  and same["recent"][0]["activity_id"] == 1111, str(same))

            print("\npreview account, a ride")
            ride = payload(await sess.call_tool("get_activity_details", {"activity_id": 2222}))
            print("   ", json.dumps({k: ride.get(k) for k in ("ftp", "power_zones")}, indent=2)[:500])
            check("a ride's details carry the FTP set in Garmin, with watts per kilo",
                  ride.get("ftp", {}).get("watts") == 250 and ride["ftp"].get("w_per_kg") == 3.57, str(ride.get("ftp")))
            check("and its power per kilo",
                  ride["summary"]["power"].get("normalized_w_per_kg") == 2.79, str(ride["summary"].get("power")))
            check("time in power zones", [z["zone"] for z in ride.get("power_zones") or []] == [1, 2, 3]
                  and ride["power_zones"][1]["percent"] == 53.8, str(ride.get("power_zones")))
            check("a ride's work is power over time pedalled, in kJ",
                  ride["summary"]["power"].get("work_kj") == 684, str(ride["summary"].get("power")))
            check("a ride gets no running pace analysis", "inside" not in ride, str(list(ride)))
            check("ride splits read by speed", "pace_per_km" not in ride["splits"][0]
                  and ride["splits"][0].get("avg_speed_kmh") == 12.0, str(ride["splits"][0]))
            fit = payload(await sess.call_tool("get_fitness", {}))
            check("get_fitness has the cycling FTP", fit.get("cycling_ftp", {}).get("watts") == 250, str(fit.get("cycling_ftp")))

            print("\npreview account, shoes")
            shoes = payload(await sess.call_tool("get_shoes", {}))
            print("   ", json.dumps(shoes, indent=2)[:700])
            pairs = {x["name"]: x for x in shoes.get("shoes") or []}
            check("only shoes, active ones, by name", set(pairs) == {"Pegasus", "Race day flats"}, str(list(pairs)))
            peg = pairs.get("Pegasus") or {}
            check("km used, left and recent",
                  peg.get("km") == 690 and peg.get("km_left") == 110 and peg.get("km_last_4_weeks") == 10.0, str(peg))
            check("a pair near its limit is flagged, and the default for runs marked",
                  peg.get("nearly_done") is True and peg.get("default_for_running") is True, str(peg))
            flats = pairs.get("Race day flats") or {}
            check("with no limit set, a typical life is assumed and said",
                  flats.get("km_left") == 530 and "typical" in flats.get("limit_is", ""), str(flats))
            check("retired pairs are listed by name and km",
                  shoes.get("retired") == [{"name": "Old Ghosts", "km": 803.0, "retired": "2026-02-28"}], str(shoes.get("retired")))

            print("\npreview account, recovery trends")
            trends = payload(await sess.call_tool("get_recovery_trends", {"weeks": 4}))
            print("   ", json.dumps({k: trends.get(k) for k in ("hrv_now", "signals")}, indent=2)[:700])
            signals = " ".join(trends.get("signals") or [])
            check("four weeks, Monday to Monday", len(trends.get("weeks") or []) == 4, str(trends.get("weeks"))[:200])
            check("a resting heart rate rise is named", "Resting heart rate 5 bpm above" in signals, signals)
            check("unbalanced HRV nights are named", "HRV status unbalanced" in signals, signals)
            check("a drop in HRV is named", "averaging 50 ms" in signals, signals)
            check("sleep and Body Battery are read week by week",
                  trends["weeks"][0].get("sleep_hours") == 7.5 and trends["weeks"][0].get("body_battery_peak") == 88,
                  str(trends["weeks"][0]))
            check("today's HRV status with its balanced range",
                  trends.get("hrv_now") == {"status": "UNBALANCED", "last_night_ms": 50, "balanced_range_ms": [53, 66]},
                  str(trends.get("hrv_now")))

            raw_plan = await sess.call_tool("get_plan", {})
            structured = field(raw_plan, "structured_content", "structuredContent")
            texts = [c.text for c in raw_plan.content if getattr(c, "type", "") == "text"]
            check("get_plan hands the view structured content",
                  isinstance(structured, dict) and structured == json.loads(texts[-1]), str(structured)[:120])
            if "error" not in (structured or {}):
                check("the model is told the card is on screen, first, in text and data alike",
                      next(iter(structured)) == "for_the_assistant"
                      and "interactive plan card" in structured["for_the_assistant"]
                      and json.loads(texts[-1]).get("for_the_assistant") == structured["for_the_assistant"],
                      str(list(structured)[:3]))
            else:
                check("an error goes back plain, with no card note", "for_the_assistant" not in structured,
                      str(structured)[:120])

    # The real entry point, with no credentials and no cache: the server must
    # come up and explain itself rather than crash on startup.
    print("\nreal entry point, no credentials")
    bare_env = dict(os.environ)
    for key in ("GARMIN_EMAIL", "GARMIN_PASSWORD"):
        bare_env.pop(key, None)
    bare_env["PYTHONPATH"] = str(ROOT)
    bare_env["GARMIN_MCP_TOKENS"] = str(ROOT / "tests" / "no-such-token.json")
    bare = StdioServerParameters(
        command=sys.executable, args=["-m", "garmin_mcp"], env=bare_env, cwd=str(ROOT)
    )
    async with stdio_client(bare) as (read, write):
        async with ClientSession(read, write) as sess:
            await sess.initialize()
            status = payload(await sess.call_tool("get_connection_status"))
            print("   ", json.dumps(status, indent=2)[:400])
            check("server starts without credentials", True)
            check("reports not authenticated", status.get("authenticated") is False)
            check(
                "error names the env vars",
                "GARMIN_EMAIL" in status.get("error", ""),
                status.get("error", "")[:120],
            )
            summary = payload(await sess.call_tool("get_daily_summary"))
            check(
                "data tool returns error, not a crash",
                "GARMIN_EMAIL" in summary.get("error", ""),
                str(summary)[:160],
            )

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
