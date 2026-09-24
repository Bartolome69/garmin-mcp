"""End-to-end smoke test: drives the server over stdio like a real MCP client.

Runs against a stubbed Garmin account (no network, no credentials), so it
checks the wiring and the response shaping, not Garmin itself.

    .venv/bin/python tests/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
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
}


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
