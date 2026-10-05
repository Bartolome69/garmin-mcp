"""MCP server exposing read-only Garmin Connect data over stdio.

Every tool is read-only: nothing here writes to Garmin, and nothing returns the
password or the cached token.
"""

from __future__ import annotations

import functools
import json
import hashlib
import logging
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

import anyio

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x called the same thing FastMCP
    from mcp.server.fastmcp import FastMCP as MCPServer

from .formatting import (
    DateError,
    drop_empty,
    duration,
    first_present,
    hr_zones,
    km,
    local_timestamp,
    minutes,
    pace_per_km,
    pace_per_mile,
    parse_date,
    rounded,
)
from . import conditions, cycling, gear, metrics, plan, preview, progress, recovery, stream, terrain, training_plan
from .session import GarminError, session
from .workouts import (
    SPORTS,
    WorkoutError,
    build_strength_workout,
    build_workout,
    find_exercises as search_exercises,
)

log = logging.getLogger(__name__)

SITE = "https://garmin.daash.run"

_INSTRUCTIONS = (
    "The user's own Garmin Connect account. Reads activities and splits, daily "
    "health, sleep, today's readiness and training load (get_readiness), and "
    "fitness markers such as race predictions and lactate threshold "
    "(get_fitness). Writes only to the workout library: create, update, "
    "schedule, unschedule and delete structured workouts; scheduling is what "
    "sends one to the watch. A whole training block is create_plan, read back "
    "with get_plan. Dates are YYYY-MM-DD and also accept 'today', "
    "'yesterday', or a signed day offset such as '-7'. If a tool returns an "
    "'error' key, show it to the user rather than retrying blindly."
)


class _Server(MCPServer):
    """The SDK's server, with preview features hidden from whoever isn't trying them.

    Only the listings change. A host that has not been told a tool has a view
    never asks for it, so nothing else needs gating.
    """

    async def list_tools(self):
        tools = await super().list_tools()
        if preview.enabled():
            return [_with_preview_description(tool) for tool in tools if tool.name not in _REPLACED_BY_VIEWS]
        return [_without_view(tool) for tool in tools if tool.name not in _PREVIEW_ONLY]

    async def list_resources(self):
        resources = await super().list_resources()
        if preview.enabled():
            return resources
        return [r for r in resources if not str(r.uri).startswith("ui://")]

    async def list_resource_templates(self):
        templates = await super().list_resource_templates()
        if preview.enabled():
            return templates
        return [t for t in templates if not str(t.uri_template).startswith("ui://")]


# Tools a preview account doesn't see, because a view does the job better and
# offering both lets the model pick the weaker one.
_REPLACED_BY_VIEWS = {"get_plan_chart"}

# Tools only preview accounts are offered yet.
_PREVIEW_ONLY = {"get_shoes", "get_recovery_trends"}


# What a tool is for, as a preview account's model should read it. The model
# chooses a tool by its description, and get_plan's everyday one reads as if
# it were only for plans made with create_plan.
_PREVIEW_DESCRIPTIONS = {
    "get_plan": (
        "The user's training plan, week by week: every session marked done, "
        "missed or ahead, with the next one.\n\n"
        "Use this first for any question about how their plan or training is "
        "going, what's next this week, or whether they're keeping up, wherever "
        "the plan came from: a coach, an app, Garmin Coach or create_plan. With "
        "no plan made by create_plan it reads the workouts scheduled on the "
        "Garmin calendar around this week. Each session carries its workout_id "
        "and schedule_id so it can be moved or retuned. A session counts as "
        "done if it was run within a day either side of its date; a strength "
        "session counts if a strength activity was recorded any day that week, "
        "whatever its length, and shows as moved if not on its own day.\n\n"
        "When the user says they have just done a session, call this before "
        "saying whether Garmin has it: don't guess. A strength activity in "
        "Garmin is the session done, even when its exercises were logged in a "
        "gym app rather than on the watch; the sets and weights aren't needed "
        "to count it. If the user hasn't said what they did and it matters for "
        "what comes next, ask for a line. If a session isn't in Garmin at all "
        "but the user says they did it, trust them rather than calling it "
        "missed, and suggest recording it on the watch next time.\n\n"
        "Call it again each time the plan is asked about, even if it was "
        "fetched earlier in the conversation: runs sync through the day, so an "
        "earlier answer goes stale. Its result is drawn for the user as an "
        "interactive plan card, so there is no need to draw the plan yourself; "
        "add what the card can't say, such as how the week is going.\n\n"
        "Args:\n"
        "    label: The code of a plan made with create_plan, e.g. \"HM\". Omit "
        "it for the plan running now, or the calendar."
    ),
}

# Added to a tool's own description, for preview accounts.
_PREVIEW_NOTES = {
    "get_fitness": (
        "\n\nAlso returns the cycling FTP set in Garmin, with watts per kilo "
        "when the user's weight is set."
    ),
    "get_activity_details": (
        "\n\nAlso returns the weather during it (temperature, dew point, wind, "
        "and what that does to pace), the shoes worn, the terrain (climbs, "
        "pace on uphill, flat and downhill, the pace the effort was worth on "
        "the flat, heart-rate decoupling and where it faded) and earlier runs "
        "of the same route with their pace and heart rate. Use these to "
        "explain a run rather than guessing why it was slow or hard. For a "
        "ride: normalised, average and max power, intensity factor and "
        "training stress against FTP, best efforts from 5 seconds to an hour, "
        "variability, time in power zones, and climbs with speed, VAM and power."
    ),
    "get_progress": (
        "\n\nFetch it fresh each time it is asked for; runs sync through the "
        "day. Its result is drawn for the user as an interactive plan card, so "
        "there is no need to draw the weeks yourself. For \"how is my plan "
        "going\", get_plan is the better first call."
    ),
}


def _with_preview_description(tool):
    text = _PREVIEW_DESCRIPTIONS.get(tool.name)
    if text is None and tool.name in _PREVIEW_NOTES:
        text = (tool.description or "").rstrip() + _PREVIEW_NOTES[tool.name]
    return tool.model_copy(update={"description": text}) if text else tool


def _without_view(tool):
    meta = getattr(tool, "meta", None)
    if not meta or not ({"ui", "ui/resourceUri"} & set(meta)):
        return tool
    rest = {k: v for k, v in meta.items() if k not in ("ui", "ui/resourceUri")}
    return tool.model_copy(update={"meta": rest or None})


def _server() -> MCPServer:
    """The server, introduced to clients with a name, a site and an icon.

    Without these a connector list shows a grey initial where the icon should
    be. Older SDKs don't take the extra fields, so they are dropped rather than
    letting a cosmetic detail stop the server starting.
    """
    try:
        from mcp.types import Icon

        return _Server(
            "garmin",
            title="Garmin",
            instructions=_INSTRUCTIONS,
            website_url=SITE,
            icons=[
                Icon(src=f"{SITE}/icon-512.png", mime_type="image/png", sizes=["512x512"]),
                Icon(src=f"{SITE}/favicon.svg", mime_type="image/svg+xml", sizes=["any"]),
            ],
        )
    except (ImportError, TypeError):
        return _Server("garmin", instructions=_INSTRUCTIONS)


mcp = _server()


# Anything that fetches an icon by convention, rather than by asking the
# server, asks the host it was given. Only the hosted server answers over
# HTTP; over stdio these are inert.
def _icon_redirects() -> None:
    from starlette.responses import RedirectResponse

    for name in ("favicon.ico", "favicon.svg", "apple-touch-icon.png"):
        async def redirect(request, _name=name):
            return RedirectResponse(f"{SITE}/{_name}", status_code=301)

        mcp.custom_route(f"/{name}", methods=["GET"])(redirect)


try:
    _icon_redirects()
except Exception:  # noqa: BLE001 - an SDK without custom routes still serves tools
    log.debug("icon routes not registered", exc_info=True)

# --------------------------------------------------------------------------
# Apps: views a host can draw inline, beside a tool's result
# --------------------------------------------------------------------------
#
# Hosts that support MCP Apps (Claude, ChatGPT, VS Code) read the resource a
# tool points at and render it in a sandboxed frame, handing it the tool's
# result. Every other host ignores the pointer and shows the JSON, so the text
# answer stays complete on its own. Views are a preview feature: see preview.py.

APP_MIME = "text/html;profile=mcp-app"
_UI = Path(__file__).parent / "ui"


def _versioned(name: str, filename: str) -> str:
    """The view's URI, with a digest of its contents at the end.

    Hosts may keep a view they have already fetched. A changed view gets a new
    address, so the next chat fetches it fresh and nobody has to reconnect.
    """
    try:
        digest = hashlib.sha256((_UI / filename).read_bytes()).hexdigest()[:10]
    except OSError:
        digest = "missing"
    return f"ui://garmin/{name}/{digest}"


PLAN_VIEW = _versioned("plan", "plan.html")


# Said to the model ahead of the data whenever a view is drawing it. Read at
# the moment the model decides how to answer, which a tool description, read
# once at the start of a chat, isn't: without it the model sometimes drew its
# own version of the plan from an older answer in the chat.
VIEW_NOTE = (
    "The user is looking at this as an interactive plan card, drawn from the "
    "data below and fetched just now, so it is current. Don't draw the plan "
    "again or list it day by day: answer in a few sentences about what matters, "
    "such as how the week is going and what's next. Use the ids below for any change."
)


def _for_view(result: Any) -> Any:
    """A tool's result with the note to the model as its first field, for preview accounts.

    In the data itself rather than a separate text block, because some hosts
    hand the model the structured content and others the text: this way the
    note reaches it either way. The view ignores the field.
    """
    if not preview.enabled() or not isinstance(result, dict) or "error" in result:
        return result
    return {"for_the_assistant": VIEW_NOTE, **result}


def _app_tool(view: str):
    """mcp.tool() that also names a view, on SDKs that know about tool meta.

    The function stays importable as written, returning its plain dict; what
    the server registers adds the note to the model in front of it.
    """
    meta = {"ui": {"resourceUri": view}, "ui/resourceUri": view}

    def register(fn):
        @functools.wraps(fn)
        async def answered(*args, **kwargs):
            return _for_view(await fn(*args, **kwargs))

        try:
            mcp.tool(meta=meta)(answered)
        except TypeError:
            mcp.tool()(answered)
        return fn

    return register


def _app_view(uri: str, filename: str, description: str) -> None:
    def read() -> str:
        return (_UI / filename).read_text(encoding="utf-8")

    read.__name__ = filename.split(".")[0] + "_view"
    try:
        mcp.resource(uri, name=read.__name__, description=description, mime_type=APP_MIME)(read)
    except TypeError:
        return

    # A chat that kept an older tool list asks for an older address. It gets
    # the view as it is now rather than nothing, so a change never breaks one.
    def read_any(version: str) -> str:
        return read()

    read_any.__name__ = read.__name__ + "_any_version"
    try:
        mcp.resource(
            uri.rsplit("/", 1)[0] + "/{version}",
            name=read_any.__name__,
            description=description,
            mime_type=APP_MIME,
        )(read_any)
    except (TypeError, ValueError):
        log.debug("view template not registered", exc_info=True)


try:
    _app_view(
        PLAN_VIEW,
        "plan.html",
        "The training plan as an interactive view: progress, the next session, and each week's days.",
    )
except Exception:  # noqa: BLE001 - a view that can't register must not stop the tools
    log.debug("plan view not registered", exc_info=True)

MAX_ACTIVITIES = 50


async def _call(fn: Callable[[Any], Any]) -> Any:
    """Run a blocking Garmin call on a worker thread, reauthenticating if needed."""
    return await anyio.to_thread.run_sync(functools.partial(session.run, fn))


# How long a read may be answered from memory, for preview accounts. Anything
# changed through these tools clears it at once; something changed in the
# Garmin app shows after this long at most.
CALENDAR_MEMORY = 90
WORKOUT_MEMORY = 600


async def _read(key: Any, ttl: float, fn: Callable[[Any], Any]) -> Any:
    """_call, answered from the session's memory where preview allows it."""
    if not preview.enabled():
        return await _call(fn)
    return await anyio.to_thread.run_sync(functools.partial(session.remembered, key, ttl, fn))


# Tools that change something in Garmin. After any of them, success or not,
# nothing remembered can be trusted.
WRITES = {
    "create_workout", "update_workout", "create_strength_workout", "schedule_workout",
    "unschedule_workout", "delete_workout", "create_plan", "remove_plan",
}


def tool_errors(fn):
    """Return a clear error payload instead of letting an exception escape.

    Anything unexpected is reported by type and message: enough for the user to
    act on, without a traceback going back through the transport.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            if fn.__name__ in WRITES:
                try:
                    return await fn(*args, **kwargs)
                finally:
                    session.forget()
            return await fn(*args, **kwargs)
        except (GarminError, DateError, WorkoutError, training_plan.PlanError) as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - a tool must never crash the server
            log.exception("Tool %s failed", fn.__name__)
            return {
                "error": (
                    f"Garmin request failed ({type(exc).__name__}: {exc}). "
                    "If this persists, check connection status with "
                    "get_connection_status."
                )
            }

    return wrapper


def _method(client: Any, *names: str) -> Callable[..., Any]:
    """Pick whichever name this version of garminconnect uses."""
    for name in names:
        fn = getattr(client, name, None)
        if callable(fn):
            return fn
    raise GarminError(
        f"Installed garminconnect has none of: {', '.join(names)}. "
        "Try upgrading it with `uv pip install -U garminconnect`."
    )


# --------------------------------------------------------------------------
# Daily summary
# --------------------------------------------------------------------------


@mcp.tool()
@tool_errors
async def get_daily_summary(date: str | None = None) -> dict[str, Any]:
    """Daily health summary: steps, calories, resting heart rate and body battery.

    Args:
        date: Day to report on. YYYY-MM-DD, 'today', 'yesterday' or an offset
            like '-3'. Defaults to today.
    """
    day = parse_date(date)
    stats = await _call(lambda c: _method(c, "get_stats", "get_user_summary")(day))
    if not stats:
        return {"date": day, "note": "Garmin returned no summary for this day."}

    return drop_empty(
        {
            "date": day,
            "steps": stats.get("totalSteps"),
            "step_goal": stats.get("dailyStepGoal"),
            "floors_climbed": stats.get("floorsAscended"),
            "distance_km": km(stats.get("totalDistanceMeters")),
            "calories": drop_empty(
                {
                    "total": stats.get("totalKilocalories"),
                    "active": stats.get("activeKilocalories"),
                    "resting_bmr": stats.get("bmrKilocalories"),
                }
            ),
            "heart_rate": drop_empty(
                {
                    "resting_bpm": stats.get("restingHeartRate"),
                    "min_bpm": stats.get("minHeartRate"),
                    "max_bpm": stats.get("maxHeartRate"),
                    "resting_7day_avg_bpm": stats.get(
                        "lastSevenDaysAvgRestingHeartRate"
                    ),
                }
            ),
            "body_battery": drop_empty(
                {
                    "most_recent": stats.get("bodyBatteryMostRecentValue"),
                    "highest": stats.get("bodyBatteryHighestValue"),
                    "lowest": stats.get("bodyBatteryLowestValue"),
                    "charged": stats.get("bodyBatteryChargedValue"),
                    "drained": stats.get("bodyBatteryDrainedValue"),
                    "gained_during_sleep": stats.get("bodyBatteryDuringSleep"),
                }
            ),
            "stress": drop_empty(
                {
                    "average": stats.get("averageStressLevel"),
                    "max": stats.get("maxStressLevel"),
                    "rest_minutes": minutes(stats.get("restStressDuration")),
                    "high_minutes": minutes(stats.get("highStressDuration")),
                }
            ),
            "intensity_minutes": drop_empty(
                {
                    "moderate": stats.get("moderateIntensityMinutes"),
                    "vigorous": stats.get("vigorousIntensityMinutes"),
                    "goal": stats.get("intensityMinutesGoal"),
                }
            ),
            "spo2_average": stats.get("averageSpo2"),
            "respiration_avg": stats.get("avgWakingRespirationValue"),
        }
    )


# --------------------------------------------------------------------------
# Sleep
# --------------------------------------------------------------------------


def _sleep_score(scores: dict[str, Any]) -> dict[str, Any]:
    overall = scores.get("overall") or {}
    qualifiers = {
        key: (value or {}).get("qualifierKey")
        for key, value in scores.items()
        if isinstance(value, dict) and key != "overall"
    }
    return drop_empty(
        {
            "overall": overall.get("value"),
            "rating": overall.get("qualifierKey"),
            "qualifiers": drop_empty(qualifiers),
        }
    )


@mcp.tool()
@tool_errors
async def get_sleep_data(date: str | None = None) -> dict[str, Any]:
    """Sleep stages and sleep score for a night.

    Args:
        date: The date you woke up on. YYYY-MM-DD, 'today', 'yesterday' or an
            offset like '-3'. Defaults to today.
    """
    day = parse_date(date)
    raw = await _call(lambda c: c.get_sleep_data(day))
    if not raw:
        return {"date": day, "note": "Garmin returned no sleep data for this night."}

    dto = raw.get("dailySleepDTO") or {}
    total = dto.get("sleepTimeSeconds")
    if not dto or total is None:
        return {
            "date": day,
            "note": "No sleep recorded for this night (watch not worn, or not synced).",
        }

    stages = {
        "deep": dto.get("deepSleepSeconds"),
        "light": dto.get("lightSleepSeconds"),
        "rem": dto.get("remSleepSeconds"),
        "awake": dto.get("awakeSleepSeconds"),
    }

    return drop_empty(
        {
            "date": day,
            "total_sleep": duration(total),
            "total_sleep_hours": rounded(total / 3600, 2),
            "asleep_at": local_timestamp(dto.get("sleepStartTimestampLocal")),
            "awake_at": local_timestamp(dto.get("sleepEndTimestampLocal")),
            "stages": drop_empty(
                {
                    name: drop_empty(
                        {
                            "time": duration(secs),
                            "minutes": minutes(secs),
                            "percent": (
                                round(secs / total * 100, 1)
                                if secs is not None and total
                                else None
                            ),
                        }
                    )
                    for name, secs in stages.items()
                }
            ),
            "score": _sleep_score(dto.get("sleepScores") or {}),
            "resting_heart_rate": raw.get("restingHeartRate"),
            "average_respiration": dto.get("averageRespirationValue"),
            "average_spo2": raw.get("averageSpO2Value"),
            "average_hrv_ms": raw.get("avgOvernightHrv"),
            "body_battery_change": raw.get("bodyBatteryChange"),
            "awake_count": raw.get("awakeCount"),
        }
    )


# --------------------------------------------------------------------------
# Activities
# --------------------------------------------------------------------------


def _running_dynamics(data: Mapping[str, Any]) -> dict[str, Any]:
    """Strap and watch dynamics, if the device recorded them.

    Field names differ between the activity list and the detail endpoint, so
    both spellings are tried. Everything here is absent for people without a
    compatible strap or watch, and drop_empty removes it rather than reporting
    a row of nulls.
    """
    return drop_empty(
        {
            "ground_contact_ms": rounded(
                first_present(data, "avgGroundContactTime", "groundContactTime"), 0
            ),
            "ground_contact_balance_left_pct": rounded(
                first_present(
                    data, "avgGroundContactBalance", "groundContactBalanceLeft"
                ),
                1,
            ),
            "vertical_oscillation_cm": rounded(
                first_present(data, "avgVerticalOscillation", "verticalOscillation"), 1
            ),
            "vertical_ratio_pct": rounded(
                first_present(data, "avgVerticalRatio", "verticalRatio"), 1
            ),
            "stride_length_cm": rounded(
                first_present(data, "avgStrideLength", "strideLength"), 1
            ),
        }
    )


def _power(data: Mapping[str, Any]) -> dict[str, Any]:
    return cycling.power(data)


def _inline_hr_zones(activity: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Activity list rows often carry hrTimeInZone_1..5 already — use them free."""
    raw = [
        {"zoneNumber": n, "secsInZone": activity.get(f"hrTimeInZone_{n}")}
        for n in range(1, 6)
        if activity.get(f"hrTimeInZone_{n}") is not None
    ]
    return hr_zones(raw)


def _summarise_activity(activity: dict[str, Any]) -> dict[str, Any]:
    distance = activity.get("distance")
    secs = first_present(activity, "duration", "elapsedDuration", "movingDuration")
    if cycling.is_ride((activity.get("activityType") or {}).get("typeKey")):
        return _summarise_ride(activity, distance, secs)
    return drop_empty(
        {
            "activity_id": activity.get("activityId"),
            "name": activity.get("activityName"),
            "type": (activity.get("activityType") or {}).get("typeKey"),
            "start_local": activity.get("startTimeLocal"),
            "location": activity.get("locationName"),
            "distance_km": km(distance),
            "duration": duration(secs),
            "duration_seconds": rounded(secs, 0),
            "moving_time": duration(activity.get("movingDuration")),
            "pace_per_km": pace_per_km(distance, activity.get("movingDuration") or secs),
            "pace_per_mile": pace_per_mile(
                distance, activity.get("movingDuration") or secs
            ),
            "avg_speed_kmh": rounded(
                (activity.get("averageSpeed") or 0) * 3.6 or None, 2
            ),
            "heart_rate": drop_empty(
                {
                    "average_bpm": rounded(activity.get("averageHR"), 0),
                    "max_bpm": rounded(activity.get("maxHR"), 0),
                }
            ),
            "hr_zones": _inline_hr_zones(activity),
            "calories": rounded(activity.get("calories"), 0),
            "elevation_gain_m": rounded(activity.get("elevationGain"), 0),
            "avg_cadence_spm": rounded(
                first_present(
                    activity,
                    "averageRunningCadenceInStepsPerMinute",
                    "averageBikingCadenceInRevPerMinute",
                ),
                0,
            ),
            "running_dynamics": _running_dynamics(activity),
            "power": _power(activity),
            "training_effect": drop_empty(
                {
                    "aerobic": rounded(activity.get("aerobicTrainingEffect"), 1),
                    "anaerobic": rounded(activity.get("anaerobicTrainingEffect"), 1),
                }
            ),
            "vo2max": rounded(activity.get("vO2MaxValue"), 1),
        }
    )


def _summarise_ride(activity: dict[str, Any], distance: Any, secs: Any) -> dict[str, Any]:
    """A ride by speed, cadence in rpm and power, rather than a run's pace and steps."""
    return drop_empty(
        {
            "activity_id": activity.get("activityId"),
            "name": activity.get("activityName"),
            "type": (activity.get("activityType") or {}).get("typeKey"),
            "start_local": activity.get("startTimeLocal"),
            "location": activity.get("locationName"),
            "distance_km": km(distance),
            "duration": duration(secs),
            "duration_seconds": rounded(secs, 0),
            "moving_time": duration(activity.get("movingDuration")),
            "avg_speed_kmh": rounded((activity.get("averageSpeed") or 0) * 3.6 or None, 1),
            "max_speed_kmh": rounded((activity.get("maxSpeed") or 0) * 3.6 or None, 1),
            "heart_rate": drop_empty(
                {
                    "average_bpm": rounded(activity.get("averageHR"), 0),
                    "max_bpm": rounded(activity.get("maxHR"), 0),
                }
            ),
            "hr_zones": _inline_hr_zones(activity),
            "power": cycling.power(activity),
            "avg_cadence_rpm": rounded(
                first_present(activity, "averageBikingCadenceInRevPerMinute", "averageBikeCadence"), 0
            ),
            "max_cadence_rpm": rounded(
                first_present(activity, "maxBikingCadenceInRevPerMinute", "maxBikeCadence"), 0
            ),
            "calories": rounded(activity.get("calories"), 0),
            "elevation_gain_m": rounded(activity.get("elevationGain"), 0),
            "elevation_loss_m": rounded(activity.get("elevationLoss"), 0),
            "training_effect": drop_empty(
                {
                    "aerobic": rounded(activity.get("aerobicTrainingEffect"), 1),
                    "anaerobic": rounded(activity.get("anaerobicTrainingEffect"), 1),
                }
            ),
            "training_load": rounded(activity.get("activityTrainingLoad"), 0),
            "vo2max": rounded(activity.get("vO2MaxValue"), 1),
        }
    )


@mcp.tool()
@tool_errors
async def get_activities(
    limit: int = 10,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """List recent runs and workouts with distance, duration, pace and HR zones.

    Args:
        limit: Maximum activities to return (1-50). Defaults to 10.
        start_date: Optional first day of a date range, YYYY-MM-DD.
        end_date: Optional last day of a date range. Defaults to today when
            start_date is given.
    """
    limit = max(1, min(int(limit or 10), MAX_ACTIVITIES))

    if start_date:
        start = parse_date(start_date)
        end = parse_date(end_date) if end_date else parse_date("today")
        activities = await _call(lambda c: c.get_activities_by_date(start, end))
        window: dict[str, Any] = {"from": start, "to": end}
    else:
        activities = await _call(lambda c: c.get_activities(0, limit))
        window = {}

    activities = activities or []
    shown = [_summarise_activity(a) for a in activities[:limit]]
    return drop_empty(
        {
            "count": len(shown),
            "total_matching": len(activities) if start_date else None,
            "window": window or None,
            "truncated": len(activities) > len(shown) or None,
            "activities": shown,
            "note": (
                "HR zone detail is included when Garmin returns it on the list row; "
                "call get_activity_details for full zones and splits."
            ),
        }
    )


def _summarise_lap(lap: dict[str, Any], index: int, ride: bool = False) -> dict[str, Any]:
    distance = lap.get("distance")
    secs = first_present(lap, "duration", "movingDuration", "elapsedDuration")
    if ride:
        return drop_empty({
            "split": lap.get("lapIndex") or index,
            "distance_km": km(distance),
            "duration": duration(secs),
            "avg_speed_kmh": rounded(distance / secs * 3.6, 1) if distance and secs else None,
            "avg_hr": rounded(lap.get("averageHR"), 0),
            "max_hr": rounded(lap.get("maxHR"), 0),
            "elevation_gain_m": rounded(lap.get("elevationGain"), 0),
            "avg_cadence_rpm": rounded(
                first_present(lap, "averageBikeCadence", "averageBikingCadenceInRevPerMinute"), 0
            ),
            "power": _power(lap),
        })
    return drop_empty(
        {
            "split": lap.get("lapIndex") or index,
            "distance_km": km(distance),
            "duration": duration(secs),
            "pace_per_km": pace_per_km(distance, secs),
            "avg_hr": rounded(lap.get("averageHR"), 0),
            "max_hr": rounded(lap.get("maxHR"), 0),
            "elevation_gain_m": rounded(lap.get("elevationGain"), 0),
            "avg_cadence_spm": rounded(
                first_present(
                    lap,
                    "averageRunCadence",
                    "averageRunningCadenceInStepsPerMinute",
                    "averageBikingCadenceInRevPerMinute",
                ),
                0,
            ),
            "calories": rounded(lap.get("calories"), 0),
            "running_dynamics": _running_dynamics(lap),
            "power": _power(lap),
        }
    )


@mcp.tool()
@tool_errors
async def get_activity_details(activity_id: int | str) -> dict[str, Any]:
    """Splits and heart-rate detail for one activity.

    Args:
        activity_id: The activityId from get_activities.
    """
    try:
        activity_id = int(str(activity_id).strip())
    except ValueError:
        return {"error": f"activity_id must be numeric, got {activity_id!r}."}

    warnings: list[str] = []

    async def _optional(label: str, fn: Callable[[Any], Any]) -> Any:
        """One weak endpoint should degrade the response, not fail the tool."""
        try:
            return await _call(fn)
        except GarminError:
            raise
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{label} unavailable ({type(exc).__name__})")
            return None

    summary = await _optional(
        "summary",
        lambda c: _method(c, "get_activity", "get_activity_evaluation")(activity_id),
    )
    splits = await _optional("splits", lambda c: c.get_activity_splits(activity_id))
    zones = await _optional(
        "hr zones", lambda c: c.get_activity_hr_in_timezones(activity_id)
    )
    # The recording itself, for what the lap averages cannot say. Polyline off:
    # a route is thousands of points nobody here needs.
    recording = await _optional(
        "recording", lambda c: c.get_activity_details(activity_id, 2000, 0)
    )

    if summary is None and splits is None and zones is None:
        return {
            "error": (
                f"Garmin returned nothing for activity {activity_id}. Check the id "
                "from get_activities."
            )
        }

    summary = summary or {}
    laps = (splits or {}).get("lapDTOs") or []

    # The detail endpoint nests what the list endpoint keeps flat.
    flat = dict(summary)
    for key in ("summaryDTO", "activityTypeDTO"):
        nested = summary.get(key)
        if isinstance(nested, dict):
            flat.update(nested)
    if isinstance(summary.get("activityTypeDTO"), dict):
        flat["activityType"] = {"typeKey": summary["activityTypeDTO"].get("typeKey")}

    try:
        inside = stream.analyse(recording, laps)
    except Exception as exc:  # noqa: BLE001 - an odd recording must not lose the splits
        inside = None
        warnings.append(f"recording could not be analysed ({type(exc).__name__})")

    ride = cycling.is_ride((flat.get("activityType") or {}).get("typeKey"))
    around = await _around_the_run(activity_id, flat, recording, _optional, warnings) if preview.enabled() else {}
    if ride and preview.enabled():
        around.setdefault("first", {}).update(await _ride_power(activity_id, flat, _optional))

    return drop_empty(
        {
            "activity_id": activity_id,
            "summary": _with_power(_summarise_activity(flat), around.get("first", {}).pop("power", None)) or None,
            **around.get("first", {}),
            "hr_zones": hr_zones(zones),
            "splits_count": len(laps) or None,
            "splits": [
                _summarise_lap(lap, i, ride) for i, lap in enumerate(laps, start=1)
            ]
            or None,
            "inside": inside,
            **around.get("last", {}),
            "warnings": warnings or None,
        }
    )


SAME_ROUTE_LOOKBACK = timedelta(weeks=26)


def _with_power(summary: dict[str, Any], richer: dict[str, Any] | None) -> dict[str, Any]:
    if richer:
        summary["power"] = richer
    return summary


async def _ride_power(activity_id: int, flat: dict[str, Any], optional: Callable[..., Any]) -> dict[str, Any]:
    """FTP, weight and power zones beside a ride's power, for preview accounts."""
    ftp_raw = await optional("ftp", lambda c: c.get_cycling_ftp())
    profile = await optional("weight", lambda c: c.get_user_profile())
    kg = cycling.weight_kg(profile)
    ftp = cycling.shape_ftp(ftp_raw, kg)
    zones = await optional("power zones", lambda c: c.get_activity_power_in_timezones(activity_id))
    watts = cycling.power(flat, (ftp or {}).get("watts"), kg)
    if not watts:
        return drop_empty({"ftp": ftp})
    return drop_empty({
        "power": watts,
        "ftp": ftp,
        "power_zones": cycling.power_zones(zones),
        "power_how_to_read": cycling.how_to_read(),
    })


async def _around_the_run(activity_id: int, flat: dict[str, Any], recording: Any,
                          optional: Callable[..., Any], warnings: list[str]) -> dict[str, dict[str, Any]]:
    """Weather, shoes, terrain and earlier runs of the same route, for preview accounts."""
    found = await optional(
        "weather",
        lambda c: (c.get_activity_weather(activity_id), getattr(c, "unit_system", None)),
    )
    weather = conditions.shape_weather(*found) if found else None

    worn = await optional("shoes", lambda c: c.get_activity_gear(activity_id))
    shoes = [gear.name_of(g) for g in (worn or []) if isinstance(g, Mapping) and gear.is_shoe(g)]

    columns = stream.parse_stream(recording)
    try:
        ride = cycling.is_ride((flat.get("activityType") or {}).get("typeKey"))
        ground = terrain.analyse(columns, ride=ride) if columns else None
    except Exception as exc:  # noqa: BLE001 - an odd recording must not lose the rest
        ground = None
        warnings.append(f"terrain could not be analysed ({type(exc).__name__})")

    same = None
    day = str(flat.get("startTimeLocal") or "")[:10]
    this = dict(flat, activityId=activity_id)
    if this.get("startLatitude") is None:
        track = [(la, lo) for la, lo in zip(columns.get("directLatitude") or [], columns.get("directLongitude") or [])
                 if la is not None and lo is not None]
        if track:
            this.update(startLatitude=track[0][0], startLongitude=track[0][1],
                        endLatitude=track[-1][0], endLongitude=track[-1][1])
    if day and this.get("startLatitude") is not None:
        try:
            until = date_cls.fromisoformat(day)
        except ValueError:
            until = None
        if until:
            since = (until - SAME_ROUTE_LOOKBACK).isoformat()
            earlier = await optional(
                "earlier runs", lambda c: c.get_activities_by_date(since, (until - timedelta(days=1)).isoformat())
            )
            same = terrain.same_route(this, earlier or [])

    return {
        "first": drop_empty({"weather": weather, "shoes": ", ".join(s for s in shoes if s) or None}),
        "last": drop_empty({"terrain": ground, "same_route": same}),
    }


# --------------------------------------------------------------------------
# Workouts. These create, schedule, unschedule and delete — but only workouts.
# Nothing here can touch a recorded activity, so training history cannot be
# lost through this server however wrong a tool call goes.
# --------------------------------------------------------------------------


@mcp.tool()
@tool_errors
async def list_workouts(limit: int = 20) -> dict[str, Any]:
    """List structured workouts saved in the Garmin account.

    Args:
        limit: Maximum workouts to return (1-100). Defaults to 20.
    """
    limit = max(1, min(int(limit or 20), 100))
    workouts = await _call(lambda c: c.get_workouts(0, limit)) or []
    return {
        "count": len(workouts),
        "workouts": [
            drop_empty(
                {
                    "workout_id": w.get("workoutId"),
                    "name": w.get("workoutName"),
                    "sport": (w.get("sportType") or {}).get("sportTypeKey"),
                    "estimated_duration": duration(w.get("estimatedDurationInSecs")),
                    "updated": w.get("updateDate"),
                }
            )
            for w in workouts
        ],
    }


@mcp.tool()
@tool_errors
async def create_workout(
    name: str,
    steps: list[dict[str, Any]],
    sport: str = "running",
    description: str | None = None,
) -> dict[str, Any]:
    """Create a structured workout in Garmin Connect.

    Adds a new workout; it never edits or replaces an existing one. Use
    schedule_workout afterwards to put it on a date so it syncs to the watch.

    Args:
        name: Name shown in Garmin Connect and on the watch.
        steps: Ordered list of steps. Each step is an object:
            - "type": warmup, interval, recovery, rest, cooldown, or repeat
            - exactly one of "duration_seconds" or "distance_meters"
            - optional target, either "pace" ("4:05", or ["4:00","4:10"] for a
              range, minutes per km) or "hr" ([150, 165] in bpm)
            A repeat looks like {"type": "repeat", "times": 5, "steps": [...]}
            and cannot contain another repeat.
            Example — 15 min warmup, 5x1km at 4:05 with 90s recoveries, 10 min
            cooldown:
                [{"type": "warmup", "duration_seconds": 900},
                 {"type": "repeat", "times": 5, "steps": [
                     {"type": "interval", "distance_meters": 1000, "pace": "4:05"},
                     {"type": "recovery", "duration_seconds": 90}]},
                 {"type": "cooldown", "duration_seconds": 600}]
        sport: running, cycling, swimming, walking or hiking. Defaults to running.
        description: Optional note stored with the workout.
    """
    workout, summary, estimated = build_workout(name, sport, steps, description)
    payload = workout.to_dict()

    result = await _call(lambda c: c.upload_workout(payload)) or {}
    workout_id = first_present(result, "workoutId", "id")
    if workout_id is None:
        return {
            "error": "Garmin accepted the request but returned no workout id.",
            "raw_response": str(result)[:300],
        }

    return {
        "workout_id": workout_id,
        "name": name,
        "sport": str(sport).lower(),
        "estimated_duration": duration(estimated),
        "summary": summary,
        "next_step": (
            "Call schedule_workout with this workout_id and a date to put it on "
            "the Garmin calendar so it reaches the watch."
        ),
    }


@mcp.tool()
@tool_errors
async def update_workout(
    workout_id: int | str,
    name: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    sport: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Change an existing workout in place: its name, its steps, or both.

    The workout keeps its id, so any dates it is already scheduled on stay
    scheduled and the watch picks up the new version at the next sync. This is
    how a plan adapts: retune the paces on next week's sessions rather than
    deleting and recreating them. Give `steps` in the same shape create_workout
    takes; leave it out to change only the name or description.

    Args:
        workout_id: The workoutId from list_workouts or create_workout.
        name: New name. Omit to keep the current one.
        steps: New step list, replacing the old one entirely. Omit to keep it.
        sport: Only needed with steps, and only to change the sport.
        description: New note. Omit to keep the current one.
    """
    try:
        workout_id = int(str(workout_id).strip())
    except ValueError:
        return {"error": f"workout_id must be numeric, got {workout_id!r}."}
    if name is None and steps is None and description is None:
        return {"error": "Nothing to change: give a new name, new steps, or a description."}

    existing = await _call(lambda c: c.get_workout_by_id(workout_id)) or {}
    current_name = existing.get("workoutName") if isinstance(existing, dict) else None
    if not current_name:
        return {"error": f"No workout with id {workout_id}. Check list_workouts."}
    current_sport = (
        (existing.get("sportType") or {}).get("sportTypeKey") or "running"
    )
    # A session renamed without its plan code would silently leave the plan.
    code = training_plan.label_of(current_name)
    if name and code and not training_plan.label_of(name):
        name = training_plan.tagged(name, code)

    summary = None
    if steps is not None:
        new_name = (name or current_name).strip()
        workout, summary, estimated = build_workout(
            new_name,
            sport or current_sport,
            steps,
            description if description is not None else existing.get("description"),
        )
        payload = workout.to_dict()
    else:
        payload = dict(existing)
        if name:
            payload["workoutName"] = name.strip()
        if description is not None:
            payload["description"] = description
        new_name = payload["workoutName"]
        estimated = existing.get("estimatedDurationInSecs")

    await _call(lambda c: c.update_workout(workout_id, payload))
    return drop_empty(
        {
            "workout_id": workout_id,
            "name": new_name,
            "sport": str(sport or current_sport).lower(),
            "estimated_duration": duration(estimated),
            "summary": summary,
            "changed": [
                k for k, v in (("name", name), ("steps", steps), ("description", description))
                if v is not None
            ],
            "note": (
                "Same workout id, so it stays on every date it was scheduled for; "
                "the watch gets the new version at its next sync."
            ),
        }
    )


@mcp.tool()
@tool_errors
async def find_exercises(query: str, limit: int = 25) -> dict[str, Any]:
    """Search Garmin's exercise catalogue by name.

    Use this before create_strength_workout when unsure what an exercise is
    called, or when it reports that a name was ambiguous. Matching ignores
    hyphens and spacing, so "pull up" and "pull-up" both work.

    Args:
        query: Part of an exercise name, e.g. "bench press" or "row".
        limit: How many matches to return. Defaults to 25.
    """
    hits = search_exercises(query, limit=limit)
    if not hits:
        return {
            "query": query,
            "matches": [],
            "note": "Nothing matched. Try a shorter or more common term.",
        }
    return {
        "query": query,
        "matches": [
            {"name": h["name"], "category": h["category"], "exercise": h["exercise"]}
            for h in hits
        ],
        "note": (
            "Pass one of these 'name' values as 'exercise' to "
            "create_strength_workout."
        ),
    }


@mcp.tool()
@tool_errors
async def create_strength_workout(
    name: str,
    exercises: list[dict[str, Any]],
    description: str | None = None,
) -> dict[str, Any]:
    """Create a strength training workout in Garmin Connect.

    Adds a new workout; it never edits or replaces an existing one. Use
    schedule_workout afterwards to put it on a date so it syncs to the watch.

    Exercise names come from Garmin's own catalogue. Common names usually work
    as typed; if one is ambiguous this says so and lists the candidates rather
    than guessing, since a wrong guess is the wrong movement on a watch. Call
    find_exercises to search.

    Args:
        name: Name shown in Garmin Connect and on the watch.
        exercises: Ordered list of blocks. Each block is an object:
            - "exercise": catalogue name, e.g. "Barbell Bench Press"
            - "sets": number of sets (default 3)
            - "reps": repetitions per set (default 10)
            - "rest_seconds": rest after each set (default 90)
            - "weight_kg": optional target weight
            Example — a push day:
                [{"exercise": "Barbell Bench Press", "sets": 4, "reps": 8,
                  "rest_seconds": 120, "weight_kg": 70},
                 {"exercise": "Barbell Overhead Press", "sets": 3, "reps": 10,
                  "rest_seconds": 90},
                 {"exercise": "Cable Triceps Pushdown", "sets": 3, "reps": 12,
                  "rest_seconds": 60}]
        description: Optional note stored with the workout.
    """
    workout, summary, estimated = build_strength_workout(name, exercises, description)
    payload = workout.to_dict()

    result = await _call(lambda c: c.upload_workout(payload)) or {}
    workout_id = first_present(result, "workoutId", "id")
    if workout_id is None:
        return {
            "error": "Garmin accepted the request but returned no workout id.",
            "raw_response": str(result)[:300],
        }

    return {
        "workout_id": workout_id,
        "name": name,
        "sport": "strength_training",
        "estimated_duration": duration(estimated),
        "summary": summary,
        "note": (
            "The estimate is rough — a repetition is not a fixed length of time."
        ),
        "next_step": (
            "Call schedule_workout with this workout_id and a date to put it on "
            "the Garmin calendar so it reaches the watch."
        ),
    }


@mcp.tool()
@tool_errors
async def schedule_workout(workout_id: int | str, date: str) -> dict[str, Any]:
    """Put an existing workout on a date in the Garmin calendar.

    Scheduling is what makes a workout sync to the watch.

    Args:
        workout_id: Id from create_workout or list_workouts.
        date: The day to schedule it on. YYYY-MM-DD, 'today', 'tomorrow', or an
            offset like '+3'.
    """
    try:
        workout_id = int(str(workout_id).strip())
    except ValueError:
        return {"error": f"workout_id must be numeric, got {workout_id!r}."}

    day = parse_date(date)
    result = await _call(lambda c: c.schedule_workout(workout_id, day)) or {}
    return {
        "workout_id": workout_id,
        "scheduled_for": day,
        "schedule_id": first_present(result, "workoutScheduleId", "id"),
        "note": "Sync the watch (or open Garmin Connect on your phone) to pick it up.",
    }


def _scheduled_in_month(calendar: Any) -> list[dict[str, Any]]:
    """The workout entries from a month of Garmin's calendar.

    The calendar carries races and other item types alongside workouts, and the
    payload has been seen both as a bare list and wrapped in calendarItems.
    """
    items = calendar if isinstance(calendar, list) else (calendar or {}).get(
        "calendarItems", []
    )
    return [i for i in items or [] if (i.get("itemType") or "workout") == "workout"]


@mcp.tool()
@tool_errors
async def unschedule_workout(
    date: str = "", schedule_id: int | str = 0
) -> dict[str, Any]:
    """Take a workout off a day in the Garmin calendar.

    This is the reversible one: the workout itself is kept and can be
    scheduled again. Use it to clear a session the watch should no longer show.
    Deleting the workout outright is delete_workout.

    Args:
        date: The day to clear. YYYY-MM-DD, 'today', 'tomorrow', or an offset
            like '+3'. If more than one workout sits on that day, they are
            listed back rather than guessed between.
        schedule_id: Remove one specific entry, from a previous listing or from
            what schedule_workout returned. Takes precedence over date.
    """
    if schedule_id:
        try:
            schedule_id = int(str(schedule_id).strip())
        except ValueError:
            return {"error": f"schedule_id must be numeric, got {schedule_id!r}."}
        await _call(lambda c: c.unschedule_workout(schedule_id))
        return {"unscheduled": schedule_id, "note": "The workout itself is kept."}

    if not date:
        return {"error": "Give either a date or a schedule_id."}

    day = parse_date(date)
    year, month = day.split("-")[0], day.split("-")[1]
    calendar = await _call(lambda c: c.get_scheduled_workouts(int(year), int(month)))
    on_day = [i for i in _scheduled_in_month(calendar) if i.get("date") == day]

    if not on_day:
        return {"date": day, "unscheduled": None, "note": "Nothing was scheduled then."}

    if len(on_day) > 1:
        # Two sessions on one day is normal enough (a double day). Picking one
        # would be a guess, and the wrong guess silently clears the wrong thing.
        return {
            "date": day,
            "error": "More than one workout is scheduled that day. "
            "Call again with the schedule_id of the one to remove.",
            "candidates": [
                drop_empty(
                    {
                        "schedule_id": first_present(i, "id", "workoutScheduleId"),
                        "name": first_present(i, "title", "workoutName"),
                        "workout_id": i.get("workoutId"),
                    }
                )
                for i in on_day
            ],
        }

    entry = on_day[0]
    found = first_present(entry, "id", "workoutScheduleId")
    await _call(lambda c: c.unschedule_workout(found))
    return {
        "date": day,
        "unscheduled": found,
        "name": first_present(entry, "title", "workoutName"),
        "note": "Off the calendar. The workout is kept and can be scheduled again.",
    }


@mcp.tool()
@tool_errors
async def delete_workout(workout_id: int | str, confirm: str = "") -> dict[str, Any]:
    """Delete a workout from the Garmin account. Permanent.

    This asks before it acts, and the check is real rather than advisory: the
    first call never deletes. It reads the workout back and returns its name,
    and only a second call passing that name as `confirm` goes through. Show
    the person what is about to go and let them answer before confirming.

    Recorded activities are untouchable here. This removes a workout from the
    workout library — a plan for a session, not a session you ran.

    Args:
        workout_id: Id from list_workouts or create_workout.
        confirm: The workout's exact name, which the first call returns.
    """
    try:
        workout_id = int(str(workout_id).strip())
    except ValueError:
        return {"error": f"workout_id must be numeric, got {workout_id!r}."}

    detail = await _call(lambda c: c.get_workout_by_id(workout_id)) or {}
    name = first_present(detail, "workoutName", "name")
    if not name:
        return {"error": f"No workout {workout_id} in this account."}

    # Compared loosely so a retyped name is not rejected over spacing or case,
    # but it still has to be *this* workout's name — which means it has been
    # read back and seen before anything is destroyed.
    if " ".join(str(confirm).split()).casefold() != " ".join(name.split()).casefold():
        return {
            "workout_id": workout_id,
            "name": name,
            "sport": (detail.get("sportType") or {}).get("sportTypeKey"),
            "confirmation_required": True,
            "note": (
                f"Nothing has been deleted. Check with the person first, then "
                f"call delete_workout again with confirm={name!r} to remove it. "
                f"This cannot be undone."
            ),
        }

    await _call(lambda c: c.delete_workout(workout_id))
    return {"deleted": workout_id, "name": name, "note": "Permanently removed."}


@_app_tool(PLAN_VIEW)
@tool_errors
async def get_progress(weeks: int = 6) -> dict[str, Any]:
    """Which planned sessions actually got done, week by week.

    Answers "am I keeping up" rather than "what did I run". A session counts if
    it happened within a day either side of its scheduled day and ran at least
    roughly as long as planned — people move sessions around, and a week where
    everything got done on shifted days is a good week, not a failed one.

    Each completed session carries a plain-English reason. Read those back when
    something looks wrong: a mismatch is visible there rather than hidden.

    Effort is not judged here. This says whether a session happened; whether it
    was run at the right intensity is yours to assess from get_activity_details.

    Args:
        weeks: How many weeks back to report, including this one (1-12).
    """
    weeks = max(1, min(int(weeks or 6), 12))

    data = await anyio.to_thread.run_sync(plan.collect)
    planned, actual = progress.from_collected(data)
    result = progress.match(planned, actual, data["today"])

    weekly = progress.by_week(result, data["today"], weeks)
    earliest = weekly[0]["week_of"] if weekly else None
    # collect() reaches further back than the report does, so trim rather than
    # showing sessions from outside the window the caller asked for.
    in_window = lambda rows: [r for r in rows if not earliest or r["date"] >= earliest]

    done, missed = in_window(result.done), in_window(result.missed)
    return {
        "weeks": weekly,
        "completed": len(done),
        "planned": len(done) + len(missed),
        "sessions": done,
        "missed": missed,
        "unplanned": in_window(result.extra),
        "upcoming": result.upcoming,
    }


# --------------------------------------------------------------------------
# Profile and personal records
# --------------------------------------------------------------------------

# Garmin identifies personal records by a numeric type. Only the running ones
# are labelled here; anything else is passed through with its raw id rather
# than guessed at.
RUNNING_RECORDS = {
    1: ("fastest_1k", "time"),
    2: ("fastest_1_mile", "time"),
    3: ("fastest_5k", "time"),
    4: ("fastest_10k", "time"),
    5: ("fastest_half_marathon", "time"),
    6: ("fastest_marathon", "time"),
    7: ("longest_run", "distance"),
}


@mcp.tool()
@tool_errors
async def get_profile() -> dict[str, Any]:
    """Fitness profile: VO2 max and personal records.

    Use this instead of asking the user for their PBs. Note that Garmin only
    knows records it has recorded itself — a race run without the watch, or
    before they owned it, will be missing.
    """
    warnings: list[str] = []

    async def _optional(label: str, fn: Callable[[Any], Any]) -> Any:
        try:
            return await _call(fn)
        except GarminError:
            raise
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{label} unavailable ({type(exc).__name__})")
            return None

    today = parse_date("today")
    metrics = await _optional("vo2 max", lambda c: c.get_max_metrics(today))
    records = await _optional("personal records", lambda c: c.get_personal_record())

    vo2, fitness_age = None, None
    if isinstance(metrics, list) and metrics:
        generic = (metrics[0] or {}).get("generic") or {}
        vo2 = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
        fitness_age = generic.get("fitnessAge")

    running: dict[str, Any] = {}
    other: list[dict[str, Any]] = []
    for record in records or []:
        type_id = record.get("typeId")
        value = record.get("value")
        if value is None:
            continue
        known = RUNNING_RECORDS.get(type_id)
        if known and record.get("activityType") == "running":
            key, kind = known
            running[key] = (
                duration(value) if kind == "time" else f"{km(value)} km"
            )
        else:
            other.append(
                drop_empty(
                    {
                        "type_id": type_id,
                        "activity_type": record.get("activityType"),
                        "value": rounded(value, 1),
                    }
                )
            )

    return drop_empty(
        {
            "vo2max": rounded(vo2, 1),
            "fitness_age": fitness_age,
            "running_records": running or None,
            "other_records": other or None,
            "note": (
                "Personal records only cover activities recorded on the watch. "
                "Garmin's heart-rate zones depend on a max heart rate the user "
                "sets in their profile, which is often an age-based estimate — "
                "ask them to confirm it before leaning on zone percentages."
            ),
            "warnings": warnings or None,
        }
    )


# --------------------------------------------------------------------------
# Training plans
# --------------------------------------------------------------------------

# How far the calendar is read to find a plan. Plans are capped at 30 weeks
# ahead when created, so this always covers one that is still running.
PLAN_SCAN_BACK = timedelta(weeks=16)
PLAN_SCAN_AHEAD = timedelta(weeks=training_plan.MAX_WEEKS_AHEAD + 1)
# Past sessions whose planned length is looked up for matching; beyond this a
# session counts on existence alone, which is the matcher's fallback anyway.
PLAN_LOOKUPS = 40


async def _plan_calendar(start: date_cls, end: date_cls) -> list[dict[str, Any]]:
    payloads = []
    for year, month in training_plan.months_between(start, end):
        try:
            payloads.append(await _read(
                ("calendar", year, month), CALENDAR_MEMORY,
                lambda c, y=year, m=month: c.get_scheduled_workouts(y, m),
            ))
        except GarminError:
            raise
        except Exception:  # noqa: BLE001 - one missing month should not hide the plan
            log.debug("calendar %s-%s unavailable", year, month, exc_info=True)
    return training_plan.calendar_items(payloads)


@mcp.tool()
@tool_errors
async def create_plan(
    goal: str,
    sessions: list[dict[str, Any]],
    label: str | None = None,
) -> dict[str, Any]:
    """Create a whole training block at once: every session built and put on its date.

    You design the plan, from the user's goal, their current fitness
    (get_fitness), their readiness and history, and a methodology; this builds
    each session as a structured workout and schedules it, so the whole block is
    on the watch in one go. Show the user the plan and get their agreement
    before calling this, because it writes to their real calendar.

    Every workout is named with a short plan code after a middle dot, like
    "Threshold 5x1k · HM", which is how get_plan finds the block again. Nothing
    about the plan is stored anywhere but Garmin.

    Every session is validated before anything is created, so a mistake in
    session 40 fails the call without leaving half a plan behind.

    Args:
        goal: What the block is for, e.g. "Half marathon, 1:40, 15 November".
        sessions: One object per session, in any order:
            - "date": YYYY-MM-DD (today or later, at most 30 weeks out)
            - "name": short, as it should read on the watch, e.g. "Long run 18k"
            - "steps": exactly as for create_workout
            - optional "sport" (running by default) and "description"
            Rest days are simply days with no session. At most 150 sessions.
        label: 2 to 8 letters or digits used as the plan code, e.g. "HM" or
            "MARA26". Derived from the goal when omitted.
    """
    today = date_cls.today()
    code, prepared = training_plan.prepare(goal, sessions, label, today)
    first, last = prepared[0]["date"], prepared[-1]["date"]

    existing = training_plan.plans_in(await _plan_calendar(first, last))
    if code in existing:
        return {
            "error": (
                f"Plan code {code} is already on the calendar between "
                f"{first.isoformat()} and {last.isoformat()}. Pass a different "
                "label, or remove the old plan first with remove_plan."
            )
        }

    created: list[dict[str, Any]] = []
    for item in prepared:
        payload = item["workout"].to_dict()
        try:
            uploaded = await _call(lambda c, p=payload: c.upload_workout(p)) or {}
            workout_id = first_present(uploaded, "workoutId", "id")
            if workout_id is None:
                raise RuntimeError("Garmin returned no workout id")
            scheduled = await _call(
                lambda c, w=workout_id, d=item["date"].isoformat(): c.schedule_workout(w, d)
            ) or {}
        except GarminError:
            raise
        except Exception as exc:  # noqa: BLE001
            return {
                "error": (
                    f"Stopped at {item['date'].isoformat()} ({item['name']}): "
                    f"{type(exc).__name__}: {exc}. The sessions before it were "
                    "created and scheduled; retry the remaining ones with the same "
                    "label, or remove_plan to start again."
                ),
                "label": code,
                "created": created,
                "not_created": len(prepared) - len(created),
            }
        created.append(
            drop_empty(
                {
                    "date": item["date"].isoformat(),
                    "name": item["name"],
                    "workout_id": workout_id,
                    "schedule_id": first_present(scheduled, "workoutScheduleId", "id"),
                    "estimated": duration(item["estimated"]),
                }
            )
        )

    weeks = (plan.monday_of(last) - plan.monday_of(first)).days // 7 + 1
    return {
        "label": code,
        "goal": goal.strip(),
        "starts": first.isoformat(),
        "ends": last.isoformat(),
        "weeks": weeks,
        "sessions_created": len(created),
        "first_sessions": created[:7],
        "next_step": (
            "Every session is on the Garmin calendar and syncs to the watch. "
            f"Use get_plan (label {code}) to see the block and how it is going."
        ),
    }


@_app_tool(PLAN_VIEW)
@tool_errors
async def get_plan(label: str | None = None) -> dict[str, Any]:
    """The training plan, week by week, with every session marked done, missed or ahead.

    Answers "how is my plan going" and "what's next" in one call: completion so
    far, the next session, what was missed in the last week, and each week's
    sessions with their workout_id and schedule_id so any of them can be moved
    or retuned. Start a coaching conversation with this when the user has a plan.

    A session counts as done if it was run within a day either side of its
    date, because people move sessions, and a week done on shifted days is a
    good week.

    Args:
        label: The plan code, e.g. "HM". Omit to get the plan that is running
            now, or the most recent one.
    """
    today = date_cls.today()
    items = await _plan_calendar(today - PLAN_SCAN_BACK, today + PLAN_SCAN_AHEAD)
    plans = training_plan.plans_in(items)
    code: str | None = None
    context: dict[str, Any] | None = None
    calendar_first = not label and preview.enabled()
    window = training_plan.around(items, today) if calendar_first else []
    if window:
        # For preview accounts the calendar is the plan: everything scheduled,
        # from a coach, an app, Garmin Coach or create_plan, so nothing that is
        # on the watch is missing from the card. A plan made here is named on
        # it, as running or coming up, rather than shown in place of it.
        sessions = window
        context = training_plan.plan_context(plans, window, today)
    elif not plans:
        if calendar_first:
            # Nothing scheduled at all: what was run is still worth seeing, and
            # it's where a plan would start from.
            return await _training_log(today)
        return {
            "error": (
                "No plan found on the Garmin calendar. create_plan builds one; "
                "single scheduled workouts are in get_progress."
            )
        }
    else:
        code = (training_plan.normalise_label(label, "") if label else None) or training_plan.pick(plans, today)
        if code not in plans:
            return {"error": f"No plan with code {code}.", "plans_found": sorted(plans)}
        sessions = plans[code]
    first = min(date_cls.fromisoformat(i["date"][:10]) for i in sessions)
    last = max(date_cls.fromisoformat(i["date"][:10]) for i in sessions)
    # Weekly distance, planned against run, is a preview feature. It needs every
    # run in each week, and a lookup for the sessions ahead as well as behind.
    weekly_km = preview.enabled()
    since = training_plan.monday_of(first) if weekly_km else first - timedelta(days=1)

    activities: list[dict[str, Any]] = []
    if first <= today:
        activities = await _call(
            lambda c: c.get_activities_by_date(
                since.isoformat(), min(today, last + timedelta(days=1)).isoformat()
            )
        ) or []

    if weekly_km:
        # Nearest to today first, so the weeks that matter get their distance
        # if the plan is longer than the lookups allowed.
        lookup_ids = []
        for item in sorted(sessions, key=lambda i: abs((date_cls.fromisoformat(i["date"][:10]) - today).days)):
            wid = item.get("workoutId")
            if wid and wid not in lookup_ids:
                lookup_ids.append(wid)
    else:
        # Planned length for the past sessions, newest first, and the goal from
        # whichever workout is read first.
        lookup_ids = []
        for item in sorted(sessions, key=lambda i: i["date"], reverse=True):
            wid = item.get("workoutId")
            if item["date"][:10] <= today.isoformat() and wid and wid not in lookup_ids:
                lookup_ids.append(wid)
    goal, seconds, metres = None, {}, {}
    goal_code = code or (context or {}).get("label")
    for wid in lookup_ids[:PLAN_LOOKUPS] or [sessions[0].get("workoutId")]:
        try:
            detail = await _read(("workout", wid), WORKOUT_MEMORY, lambda c, w=wid: c.get_workout_by_id(w)) or {}
        except GarminError:
            raise
        except Exception:  # noqa: BLE001
            continue
        seconds[wid] = float(detail.get("estimatedDurationInSecs") or 0)
        if weekly_km:
            metres[wid] = training_plan.planned_metres(detail)
        if goal_code:
            goal = goal or training_plan.goal_from_description(detail.get("description"), goal_code)

    if code is None:
        result = training_plan.summarise_calendar(
            sessions, activities, today, planned_seconds=seconds,
            planned_metres=metres, weekly_km=weekly_km,
        )
        if context:
            key = "current_plan" if context.pop("running") else "upcoming_plan"
            result[key] = drop_empty({**context, "goal": goal})
        return result
    others = sorted(p for p in plans if p != code)
    extra = {"planned_metres": metres, "weekly_km": True} if weekly_km else {}
    result = training_plan.summarise(
        code, sessions, activities, today, goal=goal, planned_seconds=seconds, **extra
    )
    if others:
        result["other_plans"] = others
    return result


async def _training_log(today: date_cls) -> dict[str, Any]:
    first = training_plan.monday_of(today) - timedelta(weeks=training_plan.LOG_WEEKS - 1)
    activities = await _call(
        lambda c: c.get_activities_by_date(first.isoformat(), today.isoformat())
    ) or []
    if not activities:
        return {
            "error": (
                "Nothing is scheduled on the Garmin calendar, and nothing was recorded "
                f"in the last {training_plan.LOG_WEEKS} weeks."
            )
        }
    return training_plan.summarise_log(activities, today)


@mcp.tool()
@tool_errors
async def remove_plan(label: str, confirm: str = "") -> dict[str, Any]:
    """Take the rest of a plan off the calendar and out of the workout library.

    For starting again or abandoning a block. Only sessions from today onward
    are removed; past ones stay, so the record of what was planned against
    what was run is kept. The first call only reports what would go. Removing
    it needs a second call with confirm set to the plan code, after the user
    has agreed.

    Args:
        label: The plan code, e.g. "HM".
        confirm: The plan code again, to go ahead.
    """
    code = training_plan.normalise_label(label, "")
    today = date_cls.today()
    items = await _plan_calendar(today - PLAN_SCAN_BACK, today + PLAN_SCAN_AHEAD)
    sessions = training_plan.plans_in(items).get(code) or [
        i for i in items if training_plan.label_of(i.get("title")) == code
    ]
    ahead = [i for i in sessions if i["date"][:10] >= today.isoformat()]
    if not ahead:
        return {"label": code, "removed": 0, "note": "Nothing from today onward carries that plan code."}

    if (confirm or "").strip().upper() != code:
        return {
            "label": code,
            "confirmation_required": True,
            "would_remove": len(ahead),
            "from": min(i["date"][:10] for i in ahead),
            "to": max(i["date"][:10] for i in ahead),
            "kept": len(sessions) - len(ahead),
            "note": f"Nothing has changed yet. Call again with confirm='{code}' to remove them.",
        }

    # A workout also scheduled on a kept date is only unscheduled, never deleted.
    kept_workouts = {i.get("workoutId") for i in sessions if i not in ahead}
    removed, failed = 0, []
    for item in ahead:
        try:
            sid = first_present(item, "id", "workoutScheduleId")
            if sid:
                await _call(lambda c, s=sid: c.unschedule_workout(s))
            wid = item.get("workoutId")
            if wid and wid not in kept_workouts:
                await _call(lambda c, w=wid: c.delete_workout(w))
            removed += 1
        except GarminError:
            raise
        except Exception as exc:  # noqa: BLE001
            failed.append({"date": item["date"][:10], "error": type(exc).__name__})
    return drop_empty(
        {"label": code, "removed": removed, "kept": len(sessions) - len(ahead), "failed": failed or None}
    )


# --------------------------------------------------------------------------
# Readiness and fitness
# --------------------------------------------------------------------------


async def _gather(labels_and_calls: list[tuple[str, Callable[[Any], Any]]]) -> tuple[list[Any], list[str]]:
    """Run several Garmin reads; one weak endpoint degrades the answer, not the tool."""
    results: list[Any] = []
    warnings: list[str] = []
    for label, fn in labels_and_calls:
        try:
            results.append(await _call(fn))
        except GarminError:
            raise
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{label} unavailable ({type(exc).__name__})")
            results.append(None)
    return results, warnings


@mcp.tool()
@tool_errors
async def get_readiness(date: str | None = None) -> dict[str, Any]:
    """How ready the user is to train today, and where their load sits.

    One call for the recovery picture: Garmin's training readiness score and
    the factors behind it, training status (productive, strained, recovery...),
    acute against chronic load, the four-week load focus, overnight HRV against
    its baseline, body battery, stress, resting heart rate and sleep. Use it
    before prescribing a hard session, or when the user asks whether to push
    today or hold back.

    Args:
        date: YYYY-MM-DD, 'today', 'yesterday' or an offset like '-1'. Defaults to today.
    """
    day = parse_date(date)
    (readiness, status, hrv, stats, sleep), warnings = await _gather(
        [
            ("readiness", lambda c: c.get_training_readiness(day)),
            ("training status", lambda c: c.get_training_status(day)),
            ("hrv", lambda c: c.get_hrv_data(day)),
            ("daily stats", lambda c: c.get_stats(day)),
            ("sleep", lambda c: c.get_sleep_data(day)),
        ]
    )
    if all(x is None for x in (readiness, status, hrv, stats, sleep)):
        return {"error": f"Garmin returned nothing for {day}.", "warnings": warnings}
    return metrics.shape_readiness(day, readiness, status, hrv, stats, sleep, warnings)


@mcp.tool()
@tool_errors
async def get_fitness() -> dict[str, Any]:
    """Garmin's read on the user's current fitness, as markers rather than history.

    VO2 max and fitness age, predicted race times for 5k to marathon, lactate
    threshold heart rate and pace, endurance score, hill score and running
    tolerance, each with its own classification where Garmin gives one. Use it
    to set goals and training paces from evidence: "Garmin thinks you are a
    1:42 half today" is a better starting point than asking.
    """
    today = parse_date("today")
    month_ago = parse_date("-28")
    (vo2, race, lactate, endurance, hill, tolerance), warnings = await _gather(
        [
            ("vo2 max", lambda c: c.get_max_metrics(today)),
            ("race predictions", lambda c: c.get_race_predictions()),
            ("lactate threshold", lambda c: c.get_lactate_threshold(latest=True)),
            ("endurance score", lambda c: c.get_endurance_score(today)),
            ("hill score", lambda c: c.get_hill_score(today)),
            ("running tolerance", lambda c: c.get_running_tolerance(month_ago, today)),
        ]
    )
    ride = None
    if preview.enabled():
        (ftp_raw, profile), more = await _gather(
            [("cycling ftp", lambda c: c.get_cycling_ftp()), ("weight", lambda c: c.get_user_profile())]
        )
        ftp = cycling.shape_ftp(ftp_raw, cycling.weight_kg(profile))
        ride = {"cycling_ftp": ftp} if ftp else None
        warnings = warnings + [w for w in more if not w.startswith("weight")]
    if all(x is None for x in (vo2, race, lactate, endurance, hill, tolerance)) and not ride:
        return {"error": "Garmin returned no fitness data.", "warnings": warnings}
    return {**metrics.shape_fitness(vo2, race, lactate, endurance, hill, tolerance, warnings), **(ride or {})}


RECOVERY_WEEKS = 4
MAX_RECOVERY_WEEKS = 12
# Garmin's daily ranges answer a month at a time.
RANGE_CHUNK_DAYS = 28


def _chunked(fetch: Callable[[Any, str, str], Any], start: date_cls, end: date_cls) -> Callable[[Any], list[Any]]:
    def run(c: Any) -> list[Any]:
        rows: list[Any] = []
        lo = start
        while lo <= end:
            hi = min(lo + timedelta(days=RANGE_CHUNK_DAYS - 1), end)
            got = fetch(c, lo.isoformat(), hi.isoformat())
            if isinstance(got, dict):
                got = got.get("hrvSummaries") or []
            rows.extend(got or [])
            lo = hi + timedelta(days=1)
        return rows

    return run


@mcp.tool()
@tool_errors
async def get_recovery_trends(weeks: int = RECOVERY_WEEKS) -> dict[str, Any]:
    """How the user is recovering across weeks, beside the training that caused it.

    Week by week: overnight HRV, resting heart rate, sleep hours and score,
    Body Battery peak and stress, next to training load, run km and sessions,
    plus the last two weeks day by day and any marker that has moved off its
    usual range (resting HR up, HRV down, short sleep, a jump in load). Use it
    for "am I recovering well", "is this block too much" or "why do I feel
    flat". For whether to train hard today, get_readiness is the one.

    Args:
        weeks: Weeks to look back, including this one (1-12). Defaults to 4.
    """
    weeks = max(1, min(int(weeks or RECOVERY_WEEKS), MAX_RECOVERY_WEEKS))
    today = date_cls.fromisoformat(parse_date("today"))
    start = training_plan.monday_of(today) - timedelta(weeks=weeks - 1)
    s, e = start.isoformat(), today.isoformat()
    (hrv, rhr, sleep, battery, stress, activities), warnings = await _gather(
        [
            ("hrv", _chunked(lambda c, a, b: c.get_hrv_data_range(a, b), start, today)),
            ("resting heart rate", lambda c: c.get_rhr_daily(s, e)),
            ("sleep", lambda c: c.get_sleep_daily(s, e)),
            ("body battery", _chunked(lambda c, a, b: c.get_body_battery(a, b), start, today)),
            ("stress", lambda c: c.get_weekly_stress(e, weeks + 1)),
            ("activities", lambda c: c.get_activities_by_date(s, e)),
        ]
    )
    if all(not x for x in (hrv, rhr, sleep, battery, stress)):
        return {"error": f"Garmin returned no recovery data from {s} to {e}.", "warnings": warnings}
    return recovery.shape_trends(start, today, hrv, rhr, sleep, battery, stress, activities, warnings)


MAX_SHOE_LOOKUPS = 10


@mcp.tool()
@tool_errors
async def get_shoes() -> dict[str, Any]:
    """The user's running shoes and how far each pair has gone.

    For each active pair: km so far, runs, the limit set in Garmin (or a
    typical 650 km when none is set) and km left, km in the last four weeks,
    when it was last worn, and whether Garmin adds it to new runs by default.
    Pairs near the end of their life are flagged. Retired pairs are listed
    by name and km. Use it for "do I need new shoes" or "which shoes do I run
    in most".
    """
    number = await _call(
        lambda c: getattr(c, "profile_id", None) or (c.get_device_last_used() or {}).get("userProfileNumber")
    )
    if not number:
        return {"error": "Garmin didn't say which profile the shoes belong to. Try again shortly."}
    (items, defaults), warnings = await _gather(
        [("gear", lambda c: c.get_gear(number)), ("default gear", lambda c: c.get_gear_defaults(number))]
    )
    shoes = [g for g in (items or []) if isinstance(g, Mapping) and gear.is_shoe(g)]
    if not shoes:
        return {"shoes": [], "note": "No shoes are set up in Garmin Connect. They're added under Gear in the app."}
    active = [g for g in shoes if str(g.get("gearStatusName") or "active").lower() != "retired"]
    retired = [g for g in shoes if g not in active]
    by_default = gear.running_default(defaults)
    today = date_cls.fromisoformat(parse_date("today"))

    shown = []
    for item in active[:MAX_SHOE_LOOKUPS]:
        uuid = str(item.get("uuid") or "")
        (stats, runs), more = await _gather(
            [("shoe stats", lambda c: c.get_gear_stats(uuid)),
             ("shoe runs", lambda c: c.get_gear_activities(uuid, 50))]
        ) if uuid else ((None, None), [])
        warnings.extend(more)
        shown.append(gear.shape_shoe(item, stats, runs, uuid in by_default, today))

    retired_shown = []
    for item in retired[:MAX_SHOE_LOOKUPS]:
        uuid = str(item.get("uuid") or "")
        (stats,), more = await _gather([("shoe stats", lambda c: c.get_gear_stats(uuid))]) if uuid else ((None,), [])
        warnings.extend(more)
        retired_shown.append(drop_empty({"name": gear.name_of(item), "km": km((stats or {}).get("totalDistance"), 0),
                                         "retired": str(item.get("dateEnd") or "")[:10] or None}))

    return drop_empty({
        "shoes": sorted(shown, key=lambda x: -(x.get("km_last_4_weeks") or 0)),
        "retired": retired_shown or None,
        "warnings": sorted(set(warnings)) or None,
        "note": "Most running shoes last 500 to 800 km; lighter racing shoes less.",
    })


# --------------------------------------------------------------------------
# Plan view
# --------------------------------------------------------------------------


@mcp.tool()
@tool_errors
async def get_plan_chart(weeks_back: int = 1, weeks_forward: int = 1) -> Any:
    """Draw the training plan as an image, to show in the conversation.

    Returns a picture of the weeks around today: each day's completed sessions
    beside what was planned, coloured by activity, sized by time. Use it when
    the user wants to see their plan rather than read a list.

    Args:
        weeks_back: Completed weeks to include before this one. Defaults to 1.
        weeks_forward: Weeks to show ahead. Defaults to 1.
    """
    from mcp.server.mcpserver import Image

    from .chart import build

    png = await anyio.to_thread.run_sync(
        functools.partial(build, max(0, min(int(weeks_back), 6)),
                          max(0, min(int(weeks_forward), 4)))
    )
    return Image(data=png, format="png")


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


@mcp.tool()
@tool_errors
async def get_connection_status() -> dict[str, Any]:
    """Check whether the server is logged in to Garmin Connect.

    Reports which credentials are present and whether the cached session is
    usable. Never returns the password or the cached token itself.
    """
    status = await anyio.to_thread.run_sync(session.status)
    if preview.enabled():
        status["preview_features"] = "on"
    return status


def main() -> None:
    # stdout belongs to the MCP protocol; every log line goes to stderr.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
