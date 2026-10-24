"""MCP server for the user's own Garmin Connect account.

It reads freely; it writes to the workout library and, when asked, a recorded
activity's name, notes, type and shoes, never what it measured. Nothing returns
the password or the cached token.
"""

from __future__ import annotations

import functools
import inspect
import json
import hashlib
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import anyio
from mcp import types
from mcp.types import TextContent, ToolAnnotations

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
from . import conditions, crews, cycling, official, official_data, fitness_trends, gear, heart, hooks, metrics, wellness, zones, plan, preview, progress, recovery, store, stream, swimming, terrain, training_plan
from . import session as session_mod
from .session import GarminError, session
from .official_client import NotOnOfficial, OfficialClient
from .workouts import (
    SPORTS,
    WorkoutError,
    build_strength_workout,
    build_workout,
    find_exercises as search_exercises,
    uses_ftp,
)

log = logging.getLogger(__name__)

SITE = "https://garmin.daash.run"
# Where people read about it. The icons and the privacy pages stay on SITE.
WEBSITE = "https://daash.run"

_INSTRUCTIONS = (
    "The user's own Garmin Connect account. Reads activities and splits, daily "
    "health, sleep, today's readiness and training load (get_readiness), and "
    "fitness markers such as race predictions and lactate threshold "
    "(get_fitness). Writes to the workout library: create, update, "
    "schedule, unschedule and delete structured workouts; scheduling is what "
    "sends one to the watch. And, when asked, a recorded run's name, notes, "
    "type or shoes (update_activity), never what it measured. A whole training block is create_plan, read back "
    "with get_plan. For any general question about how training is going "
    "(\"how's my training going\", \"how am I doing\", \"how's this week\", "
    "\"show my plan\", \"what's next\", \"am I on track\"), call get_plan "
    "first: it draws the user's plan, or with nothing scheduled their recent "
    "running and riding, as a card. Call it alone, not alongside readiness, "
    "fitness, trends or activities: its answer says where the user is (in a "
    "block, about to start one, a race ahead with nothing scheduled, or just "
    "running) and the one next step to offer them at the end, as a question. "
    "Fetch more only when the question calls for it or they take up the "
    "offer. Someone asking where their fitness is at goes to get_fitness, "
    "answered in writing. A specific question "
    "goes to its own tool without the card: a run to get_activity_details, "
    "what a planned session involves (paces, recoveries, cool-down) to "
    "get_workout, "
    "whether to go hard today to get_readiness, recovery to "
    "get_recovery_trends, heart rate through a day or night (gaps, when it "
    "settled, how low it went) to get_heart_rate, today's fitness markers or race predictions to "
    "get_fitness, whether fitness is improving or how it moved with weight to "
    "get_fitness_trends, shoes "
    "to get_shoes. Answer those in "
    "writing. A Daash crew (friends who see each other's easy and long runs) "
    "has its own card, get_crew (friends without Garmin join from the crew's "
    "page link, adding their easy runs by hand): call it only when the user asks about the "
    "crew as a whole (\"who's running this week\", \"show the crew\", "
    "\"post the crew's week\"), never alongside get_plan for a question "
    "about the user's own training, and answer a question about one day or "
    "one person (\"is anyone running Sunday\") from the crew card already in "
    "the chat, in a line, calling get_crew again only if it isn't there or a "
    "day has passed. One card per answer, at most. Dates are YYYY-MM-DD and "
    "also accept 'today', "
    "'yesterday', or a signed day offset such as '-7'. If a tool returns an "
    "'error' key, show it to the user rather than retrying blindly."
)


# For someone who signed up through Garmin's own page, the account Daash reads
# is Garmin's official API alone. These tools need Garmin Connect, so they
# aren't listed for them; called anyway, they say so.
OFFICIAL_MISSING = {
    "get_readiness", "get_fitness", "get_fitness_trends", "get_recovery_trends",
    "get_heart_rate", "get_profile", "get_shoes", "update_activity",
}
_OFFICIAL_INSTRUCTIONS = (
    "The user's own Garmin account, through Garmin's official API. Garmin sends "
    "training as the watch syncs, from the day the account connected Daash, so "
    "nothing from before then is here. Reads activities with their laps and "
    "splits, the day (steps, stress, Body Battery), and sleep. Writes to the "
    "workout library: create, update, schedule, unschedule and delete structured "
    "workouts; scheduling is what sends one to the watch. A whole training block "
    "is create_plan, read back with get_plan. For any general question about how "
    "training is going (\"how's my training going\", \"how am I doing\", \"how's "
    "this week\", \"show my plan\", \"what's next\", \"am I on track\"), call "
    "get_plan first and alone: it draws the plan, or with nothing scheduled the "
    "recent running and riding, as a card, and says the one next step to offer "
    "at the end, as a question. A specific question goes to its own tool without "
    "the card: a run to get_activity_details, what a planned session involves to "
    "get_workout, a night to get_sleep_data, a day to get_daily_summary, whether "
    "planned sessions got done to get_progress. Answer those in writing. "
    "Readiness, race predictions, fitness trends, shoes and all-day heart rate "
    "aren't shared through Garmin's official API: if asked, say so in a sentence "
    "and answer from the activities and sleep. For workout targets use paces, "
    "heart rate in bpm or watts, not zones or a share of FTP. A Daash crew "
    "(friends who see each other's easy and long runs) has its own card, "
    "get_crew: call it only when the user asks about the crew as a whole. One "
    "card per answer, at most. Dates are YYYY-MM-DD and also accept 'today', "
    "'yesterday', or a signed day offset such as '-7'. If a tool returns an "
    "'error' key, show it to the user rather than retrying blindly."
)
# Set per request by the hosted server: whether the person asking has only
# Garmin's official API behind them.
_ALONE: ContextVar[bool] = ContextVar("daash_official_alone", default=False)


def use_alone(value: bool):
    return _ALONE.set(bool(value))


def reset_alone(token) -> None:
    _ALONE.reset(token)


class _Instructions:
    """The instructions a client is given, chosen for whoever is asking."""

    def __get__(self, obj: Any, objtype: Any = None) -> Any:
        if obj is None:
            return self
        return _OFFICIAL_INSTRUCTIONS if _ALONE.get() else obj.__dict__.get("_daash_instructions")

    def __set__(self, obj: Any, value: Any) -> None:
        obj.__dict__["_daash_instructions"] = value


class _Server(MCPServer):
    """The SDK's server, with the preview-only tools hidden from whoever isn't trying them.

    Only the listings change. A host that has not been told a tool has a view
    never asks for it, so nothing else needs gating.
    """

    async def list_tools(self):
        tools = await super().list_tools()
        on = preview.enabled()
        crew_on = crews.available()
        alone = _ALONE.get()
        return [
            _with_description(tool, preview_on=on)
            for tool in tools
            if tool.name not in _REPLACED_BY_VIEWS and (on or tool.name not in _PREVIEW_ONLY)
            and (crew_on or tool.name not in CREW_TOOLS)
            and not (alone and tool.name in OFFICIAL_MISSING)
        ]

    async def list_resources(self):
        return await super().list_resources()

    async def call_tool(self, name, arguments, context=None):
        if context is not None:
            result = await super().call_tool(name, arguments, context)
        else:  # mcp 1.x hands over no context; it keeps the request's itself
            result = await super().call_tool(name, arguments)
            get_context = getattr(self, "get_context", None)
            context = get_context() if get_context else None
        try:
            await _refresh_tool_list(context, _listing())
        except Exception:  # noqa: BLE001 - the hint is extra; the answer must still arrive
            log.debug("could not work out the tool listing", exc_info=True)
        return _for_model(result)


# What the model reads of a tool's answer counts against the person's usage
# limit, and every later message in the chat carries it again: a free plan
# can run out after one good session. The SDK writes the text copy indented,
# about twice the size of the data, so it is rewritten here without the
# spacing. And a plan's per-session detail that only the card draws (each
# session's shape and icon) is left out of the model's copy: the card reads
# the structured copy, which keeps everything. Measured on a 12-week block,
# the text went from about 12,000 tokens to about 3,000.
_CARD_ONLY = {"shape", "kind"}


def _slim_rows(rows: Any) -> Any:
    if not isinstance(rows, list):
        return rows
    return [
        {k: v for k, v in row.items() if k not in _CARD_ONLY and not (k == "sport" and v == "run")}
        if isinstance(row, dict) else row
        for row in rows
    ]


def model_copy(result: Any) -> Any:
    """A plan-shaped answer without what only the card draws; anything else as it is."""
    if not isinstance(result, dict) or not isinstance(result.get("weeks"), list):
        return result
    slim = dict(result)
    slim["weeks"] = [
        {**week, **{key: _slim_rows(week[key]) for key in ("sessions", "extra") if key in week}}
        if isinstance(week, dict) else week
        for week in result["weeks"]
    ]
    if isinstance(result.get("next_session"), dict):
        slim["next_session"] = {k: v for k, v in result["next_session"].items() if k not in _CARD_ONLY}
    return slim


def _for_model(result: Any) -> Any:
    """The SDK's result with its text copy compact, and slimmed for a plan."""
    structured = getattr(result, "structured_content", None)
    content = getattr(result, "content", None)
    if (
        not isinstance(structured, dict) or getattr(result, "is_error", False)
        or not content or len(content) != 1 or getattr(content[0], "type", None) != "text"
    ):
        return result
    text = json.dumps(model_copy(structured), ensure_ascii=False, separators=(",", ":"), default=str)
    return result.model_copy(update={"content": [TextContent(type="text", text=text)]})

    async def list_resource_templates(self):
        return await super().list_resource_templates()


# Tools a preview account doesn't see, because a view does the job better and
# offering both lets the model pick the weaker one.
_REPLACED_BY_VIEWS = {"get_plan_chart"}

# Daash Crews live on the hosted server only, where all four are offered to
# everyone, in a crew or not. Claude keeps a tool list across chats, so a list
# that changed on joining would leave the crew card missing from a new chat
# until Claude re-listed; one that never changes can't go stale.
CREW_TOOLS = {"join_crew", "get_crew", "update_crew", "leave_crew"}
# They change what the crew sees, so they are not reads, though Garmin is untouched.
CREW_WRITES = {"join_crew", "update_crew", "leave_crew"}

# Tools only preview accounts are offered yet. None at the moment: everything
# built so far is everyone's. The mechanism stays for the next new thing.
_PREVIEW_ONLY: set[str] = {"garmin_official"}


# What a tool is for, as the model should read it. The model chooses a tool by
# its description, and get_plan's docstring reads as if it were only for plans
# made with create_plan. Once a preview feature, now everyone's.
_DESCRIPTIONS = {
    "get_plan": (
        "How the user's training is going, drawn as an interactive plan card: their plan week by "
        "week, each session done, missed or ahead, and the next one; with "
        "nothing scheduled, their recent weeks of running and riding.\n\n"
        "Call it first for any general question about training or a plan "
        "(\"how's my training going\", \"how am I doing\", \"this week\", "
        "\"show my plan\", \"what's next\", \"today's session\", \"am I on "
        "track\"), wherever the plan came from: a coach, an app, Garmin Coach or "
        "create_plan. Call it alone: its situation says where the user is (a "
        "block under way, about to start or just done; a race ahead with nothing "
        "scheduled; regular running; or getting going) and offers one next step. "
        "Recovery, readiness or fitness follow only when asked, or when the user "
        "takes up that offer. "
        "Without a create_plan plan it reads the calendar around this week. "
        "Sessions carry workout_id and schedule_id for moving or retuning; the "
        "next one carries its steps and target paces. For any other session's "
        "steps, paces, recoveries or cool-down, call get_workout with its "
        "workout_id: never guess them.\n\n"
        "Done means run within a day either side of its date, or any day that "
        "week if started from its workout on the watch. A strength session "
        "counts if any strength activity is recorded in its own week, early "
        "included, never another week's. Off its own day it shows as moved.\n\n"
        "When the user says they have just done a session, call this before "
        "saying whether Garmin has it: don't guess. A strength activity is the "
        "session done even if it was logged in a gym app; the sets and weights "
        "aren't needed to count it. If a session "
        "isn't in Garmin but the user says they did it, trust them and suggest "
        "recording it on the watch next time. If what they did matters for "
        "what comes next and they haven't said, ask for a line. After a hard "
        "or long session in the last two days, ask once, in one short line, how "
        "it felt, and use the answer; not after an easy run.\n\n"
        "The card draws the plan, so don't list it: add what it can't say, such "
        "as how the week is going. Each call draws it again, so answer "
        "follow-ups from that result; call again only when something may have "
        "changed (a session done, a workout moved or added, a new day) or the "
        "user asks to see it again: runs sync through the day, so after any of "
        "those the earlier answer goes stale. Don't call it for a "
        "specific question about a run, sleep, readiness, recovery, fitness or "
        "shoes.\n\n"
        "Args:\n"
        "  label: The code of a plan made with create_plan, e.g. \"HM\". Omit "
        "it for the plan running now, or the calendar."
    ),
}

# Added to a tool's own description, for everyone.
_NOTES = {
    "get_fitness": (
        "\n\nAlso returns the cycling FTP set in Garmin, with watts per kilo "
        "when the user's weight is set. For a general \"how's my training "
        "going\", get_plan is the first call; this adds the fitness markers. "
        "For whether they are improving over months, get_fitness_trends."
    ),
    "get_recovery_trends": (
        "\n\nFor a general \"how's my training going\", get_plan is the first "
        "call; this adds how recovery is keeping up with it."
    ),
    "get_activity_details": (
        "\n\nFor a run, also returns the weather during it (temperature, dew "
        "point, wind, and what that does to pace), the shoes worn, the terrain "
        "(climbs, pace on uphill, flat and downhill, the pace the effort was "
        "worth on the flat, heart-rate decoupling and where it faded) and "
        "earlier runs of the same route with their pace and heart rate. Use "
        "these to explain a run rather than guessing why it was slow or hard. "
        "For a ride: normalised, average and max power, intensity factor and "
        "training stress against FTP, best efforts from 5 seconds to an hour, "
        "variability, time in power zones, climbs with speed, VAM and power, "
        "the weather during it, and earlier rides of the same route. For a "
        "swim: pace per 100 m, pool length, SWOLF, stroke rate and strokes, and "
        "the set as swum, each interval with its pace, stroke and the rest after "
        "it. Talk about a swim in those terms, never pace per km."
    ),
    "get_progress": (
        "\n\nIts result is drawn for the user as an interactive plan card, so "
        "there is no need to draw the weeks yourself, and calling it again "
        "draws the card again: fetch it fresh only when a session may have "
        "been done since. For \"how is my plan going\", get_plan is the better "
        "first call, and once it has been shown this is rarely needed too."
    ),
}


# Added after those, for preview accounts only. Empty while nothing is in preview.
_PREVIEW_NOTES: dict[str, str] = {}


def _untitled(schema: Any, in_properties: bool = False) -> Any:
    """A JSON schema without the titles the SDK writes on every field.

    "title": "Date" beside a field named date tells the model nothing and is
    sent with every message. A parameter that is itself called title stays.
    """
    if isinstance(schema, list):
        return [_untitled(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: _untitled(value, in_properties=key == "properties" and not in_properties)
        for key, value in schema.items()
        if in_properties or not (key == "title" and isinstance(value, str))
    }


def _compact_doc(text: str) -> str:
    """A description without the indentation it has as a docstring.

    The tool list goes to the model with every message, so its spacing counts
    against the person's usage limit. Nesting keeps half its indent.
    """
    lines = inspect.cleandoc(text).splitlines()
    return "\n".join(" " * ((len(line) - len(line.lstrip(" "))) // 2) + line.strip() for line in lines)


def _with_description(tool, *, preview_on: bool):
    text = _DESCRIPTIONS.get(tool.name)
    if text is None and tool.name in _NOTES:
        text = (tool.description or "").rstrip() + _NOTES[tool.name]
    if preview_on and tool.name in _PREVIEW_NOTES:
        text = (text or tool.description or "").rstrip() + _PREVIEW_NOTES[tool.name]
    text = _compact_doc(text or tool.description or "")
    update = {"annotations": _annotations(tool.name)}
    for field in ("input_schema", "output_schema", "inputSchema", "outputSchema"):
        if isinstance(getattr(tool, field, None), dict):
            update[field] = _untitled(getattr(tool, field))
    if tool.name in _TITLES:
        update["title"] = _TITLES[tool.name]
    if text:
        update["description"] = text
    return tool.model_copy(update=update)


# What a person sees for a tool in Claude's list of steps, where the bare name
# "Get plan" undersells it.
_TITLES = {"get_plan": "Training plan and progress"}


# Tools that take something away: a workout, a plan, a date on the calendar,
# a workout's old steps, or everything a crew kept for the person.
DESTRUCTIVE = {"delete_workout", "remove_plan", "unschedule_workout", "update_workout", "leave_crew", "garmin_official"}

# Each tool's name as a directory listing shows it. Claude's Connectors
# Directory flags any tool without one in its annotations.
_ANNOTATION_TITLES = {
    "find_exercises": "Find exercises",
    "get_activities": "Get activities",
    "get_activity_details": "Get activity details",
    "get_connection_status": "Check Garmin connection",
    "get_crew": "Get crew week",
    "get_daily_summary": "Get daily summary",
    "get_fitness": "Get fitness markers",
    "get_fitness_trends": "Get fitness trends",
    "get_heart_rate": "Get heart rate",
    "get_plan": "Get training plan",
    "get_profile": "Get profile and records",
    "get_progress": "Get plan progress",
    "get_readiness": "Get training readiness",
    "get_recovery_trends": "Get recovery trends",
    "get_shoes": "Get shoes",
    "get_sleep_data": "Get sleep",
    "get_workout": "Get workout",
    "list_workouts": "List workouts",
    "create_plan": "Create training plan",
    "create_strength_workout": "Create strength workout",
    "create_workout": "Create workout",
    "schedule_workout": "Schedule workout",
    "update_activity": "Update activity details",
    "join_crew": "Join or start crew",
    "update_crew": "Update crew",
    "delete_workout": "Delete workout",
    "remove_plan": "Remove training plan",
    "unschedule_workout": "Unschedule workout",
    "update_workout": "Update workout",
    "leave_crew": "Leave crew",
    "garmin_official": "Garmin official API (preview)",
}


def _annotations(name: str) -> ToolAnnotations:
    """Say which tools only read. Claude groups those apart from the ones that
    write, so a person can allow every read at once and still be asked before
    anything changes in their Garmin account."""
    return ToolAnnotations(
        title=_ANNOTATION_TITLES.get(name),
        readOnlyHint=name not in WRITES and name not in CREW_WRITES,
        destructiveHint=name in DESTRUCTIVE,
        # Every tool works on the person's own Garmin account, a closed world,
        # not the open web; saying otherwise invites more caution than it needs.
        openWorldHint=False,
    )


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
            title="Daash",
            instructions=_INSTRUCTIONS,
            website_url=WEBSITE,
            icons=[
                Icon(src=f"{SITE}/icon-512.png", mime_type="image/png", sizes=["512x512"]),
                Icon(src=f"{SITE}/favicon.svg", mime_type="image/svg+xml", sizes=["any"]),
            ],
        )
    except (ImportError, TypeError):
        return _Server("garmin", instructions=_INSTRUCTIONS)


mcp = _server()
# The instructions go out with every initialize: make them follow the person
# asking, the way the tool list does.
_low = getattr(mcp, "_lowlevel_server", None)
if _low is not None and not isinstance(type(_low).__dict__.get("instructions"), _Instructions):
    _kept = _low.instructions
    _low.__class__ = type(type(_low).__name__, (type(_low),), {"instructions": _Instructions()})
    _low.instructions = _kept


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
# answer stays complete on its own.

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
CREW_VIEW = _versioned("crew", "crew.html")


# Said to the model ahead of the data whenever a view is drawing it. Read at
# the moment the model decides how to answer, which a tool description, read
# once at the start of a chat, isn't: without it the model sometimes drew its
# own version of the plan from an older answer in the chat.
VIEW_NOTE = (
    "The user is looking at this as an interactive plan card, drawn from the "
    "data below and fetched just now, so it is current. Don't draw the plan "
    "again or list it day by day: answer in a few sentences about what matters, "
    "such as how the week is going and what's next. If the user asks for the plan "
    "in another form (an artifact, a document, a table, a PDF), make it from the "
    "data below; the card's share icon also saves a scheduled plan whole, as a PDF "
    "or an image. Use the ids below for any change. "
    "If there is a situation below, close with its offer as one short question in "
    "your own words, unless the user already asked for that or it was offered "
    "earlier in this chat; fetch nothing more until they ask or say yes."
)


def _for_view(result: Any) -> Any:
    """A tool's result with the note to the model as its first field.

    In the data itself rather than a separate text block, because some hosts
    hand the model the structured content and others the text: this way the
    note reaches it either way. The view ignores the field.
    """
    if not isinstance(result, dict) or "error" in result:
        return result
    return {"for_the_assistant": VIEW_NOTE, **result}


# Who has been told which tool list: a host keeps the tool list it fetched,
# across chats, and with it each view's address, so a new tool or a changed
# card stays out of sight until it re-lists. tools/list_changed asks it to,
# after whichever tool a person calls first, once per person per listing.
# The listing changes when they try the preview, and the memory empties on
# deploy, which is when tools and view addresses change.
_TOLD: dict[Any, str] = {}


def _listing() -> str:
    """What decides which tools this person is shown, beyond the deploy."""
    return f"preview={preview.enabled()} crews={crews.available()}"


async def _refresh_tool_list(ctx: Any, listing: str) -> None:
    if ctx is None:
        return
    # By person on the hosted server: a session object is dropped when idle,
    # and a new one can reuse its id, which would pass one person's turn on.
    member = crews.current()
    key = member.key if member is not None else id(session_mod.current_session())
    if _TOLD.get(key) == listing:
        return
    _TOLD[key] = listing
    if len(_TOLD) > 20000:
        _TOLD.clear()
    try:
        # Sent as part of the request being answered: the hosted server keeps
        # no stream open between requests, so a notice tied to none is dropped.
        request = ctx.request_context
        for notice in (types.ToolListChangedNotification(), types.ResourceListChangedNotification()):
            await request.session.send_notification(notice, related_request_id=request.request_id)
    except Exception:  # noqa: BLE001 - a host that won't take the hint still gets its answer
        log.debug("could not ask the host to re-list tools", exc_info=True)


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

try:
    _app_view(
        CREW_VIEW,
        "crew.html",
        "A Daash crew's week as an interactive view: who runs when, and which runs to do together.",
    )
except Exception:  # noqa: BLE001 - a view that can't register must not stop the tools
    log.debug("crew view not registered", exc_info=True)

MAX_ACTIVITIES = 50


# A preview account can try Daash on Garmin's official API alone: then the
# everyday reads (a day, a night, activities) are answered only from what
# Garmin has pushed, which shows what someone connected that way would get.
OFFICIAL_SOURCE = "Garmin's official API (preview): only what Garmin has sent since connecting"
OFFICIAL_NOTHING = "Garmin's official API hasn't sent this yet: it arrives as the watch syncs."


def _official_only() -> str | None:
    """The Garmin user id when this account reads only from Garmin's official API."""
    token = official.current()
    if not token or not official.configured():
        return None
    try:
        return store.official_only(token)
    except Exception:  # noqa: BLE001 - a preview switch never stops anyone's reads
        log.exception("official-only check failed")
        return None


def _official_client() -> Any:
    """The official API standing in for the Connect client, for an official-only account."""
    garmin_user = _official_only()
    token = official.current()
    if garmin_user is None or token is None:
        return None
    return OfficialClient(token, garmin_user, alone=not store.has_connect(token))


# Said by any tool that needs Garmin Connect, to someone who signed up through
# Garmin's own page and so has no Connect session for it to fall back on.
NOT_ON_OFFICIAL = (
    "Not available on this connection: Daash reads this account through Garmin's official "
    "API, and Garmin doesn't share that through it. Answer from what Daash can read here "
    "(activities and their laps, sleep, the day, the plan), and for workout targets use "
    "paces, heart rate in bpm or watts rather than zones or a share of FTP."
)


def _run(fn: Callable[[Any], Any]) -> Any:
    """fn against the official API where the account asks for it, else Garmin Connect.

    A call the official API has no answer for (readiness, gear, the all-day
    heart rate) still goes to Connect while the account has both: this is the
    preview, and nothing the person already had should stop working. An
    account that signed up through Garmin's page has no Connect to go to.
    """
    client = _official_client()
    if client is not None:
        try:
            return client.run(fn)
        except NotOnOfficial:
            if client.alone:
                raise GarminError(NOT_ON_OFFICIAL) from None
    return session.run(fn)


async def _call(fn: Callable[[Any], Any]) -> Any:
    """Run a blocking Garmin call on a worker thread, reauthenticating if needed."""
    return await anyio.to_thread.run_sync(functools.partial(_run, fn))


# How long a read may be answered from memory. Anything
# changed through these tools clears it at once; something changed in the
# Garmin app shows after this long at most.
CALENDAR_MEMORY = 90
WORKOUT_MEMORY = 600


def _remembered(key: Any, ttl: float, fn: Callable[[Any], Any]) -> Any:
    client = _official_client()
    if client is not None:
        try:
            return client.run(fn)
        except NotOnOfficial:
            if client.alone:
                raise GarminError(NOT_ON_OFFICIAL) from None
    return session.remembered(key, ttl, fn)


async def _read(key: Any, ttl: float, fn: Callable[[Any], Any]) -> Any:
    """_call, answered from the session's memory where it is fresh enough.

    An official-only account's answers come from the store, so nothing is
    remembered for them: the store is already as quick, and always current.
    """
    return await anyio.to_thread.run_sync(functools.partial(_remembered, key, ttl, fn))


# Tools that change something in Garmin. After any of them, success or not,
# nothing remembered can be trusted.
WRITES = {
    "create_workout", "update_workout", "create_strength_workout", "schedule_workout",
    "unschedule_workout", "delete_workout", "create_plan", "remove_plan", "update_activity",
    "garmin_official",
}


def tool_errors(fn):
    """Return a clear error payload instead of letting an exception escape.

    Anything unexpected is reported by type and message: enough for the user to
    act on, without a traceback going back through the transport.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        started = time.monotonic()
        settle = hooks.before_tool(fn.__name__)
        result = await answer(*args, **kwargs)
        took = time.monotonic() - started
        if took < settle:
            await anyio.sleep(settle - took)
        return hooks.after_tool(fn.__name__, result, took)

    async def answer(*args, **kwargs):
        try:
            if fn.__name__ in WRITES:
                try:
                    return await fn(*args, **kwargs)
                finally:
                    session.forget()
            return await fn(*args, **kwargs)
        except (GarminError, DateError, WorkoutError, training_plan.PlanError, crews.CrewError, ActivityError,
                official.OfficialError) as exc:
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
    """Daily health summary: steps, calories, resting heart rate, body battery and stress.

    With what charged or drained Body Battery and the longest stressful
    spells: "why is my Body Battery low", "when was I stressed".

    Args:
        date: Day to report on. YYYY-MM-DD, 'today', 'yesterday' or an offset
            like '-3'. Defaults to today.
    """
    day = parse_date(date)
    garmin_user = _official_only()
    if garmin_user:
        stats = await anyio.to_thread.run_sync(official_data.connect_stats, garmin_user, day)
        if not stats:
            return {"date": day, "source": OFFICIAL_SOURCE, "note": OFFICIAL_NOTHING}
        events, spells = await anyio.to_thread.run_sync(official_data.day_extras, garmin_user, day)
        return {**_daily_shape(day, stats, events, None), "stressful_spells": spells or None, "source": OFFICIAL_SOURCE}
    stats = await _call(lambda c: _method(c, "get_stats", "get_user_summary")(day))
    if not stats:
        return {"date": day, "note": "Garmin returned no summary for this day."}
    (battery_events, stress_day), _ = await _gather([
        ("body battery events", lambda c: c.get_body_battery_events(day)),
        ("stress", lambda c: c.get_stress_data(day)),
    ])

    return _daily_shape(day, stats, battery_events, stress_day)


def _daily_shape(day: str, stats: dict[str, Any], battery_events: Any, stress_day: Any) -> dict[str, Any]:
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
            "body_battery_events": wellness.body_battery_events(battery_events) or None,
            "stressful_spells": wellness.stress_spells(stress_day) or None,
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
async def get_heart_rate(date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Heart rate through a day or night, from the watch's all-day readings.

    For "was there a gap overnight", "when did my heart rate settle", "how
    low did it go". Answers with gaps (no reading for 10+ min), blocks with
    low, average and high, and the lowest and highest readings. A run's heart
    rate is get_activity_details.

    Args:
        date: The day the window starts. Defaults to today; last night is
            yesterday with an evening start.
        start: Local start time, HH:MM. Defaults to midnight.
        end: Local end time, HH:MM. Before start runs into the next day.
    """
    day = date_cls.fromisoformat(parse_date(date))
    begin, finish = heart.window(day, start, end)
    days = []
    d = begin.date()
    while d <= (finish - timedelta(seconds=1)).date():
        days.append(d.isoformat())
        d += timedelta(days=1)
    payloads = [await _call(lambda c, x=x: c.get_heart_rates(x)) for x in days]
    points = heart.readings(payloads)
    first = payloads[0] if payloads and isinstance(payloads[0], dict) else {}
    # The person's own clock: Garmin's readings are in GMT and carry the offset.
    latest = next((p for p in reversed(payloads) if isinstance(p, dict)), {})
    now = datetime.now(timezone.utc).replace(tzinfo=None) + heart._offset(latest)
    summary = heart.summarise(points, begin, finish, now=now)
    return drop_empty({
        "date": day.isoformat(),
        **summary,
        "resting_hr": first.get("restingHeartRate"),
    })


@mcp.tool()
@tool_errors
async def get_sleep_data(date: str | None = None) -> dict[str, Any]:
    """Sleep stages and sleep score for a night, with overnight breathing rate and SpO2 against usual.

    Args:
        date: The date you woke up on. YYYY-MM-DD, 'today', 'yesterday' or an
            offset like '-3'. Defaults to today.
    """
    day = parse_date(date)
    garmin_user = _official_only()
    if garmin_user:
        raw = await anyio.to_thread.run_sync(official_data.connect_sleep, garmin_user, day)
        if not raw:
            return {"date": day, "source": OFFICIAL_SOURCE, "note": OFFICIAL_NOTHING}
        return {**_sleep_shape(day, raw, raw.pop("spo2Week", {}) or {}), "source": OFFICIAL_SOURCE}
    (raw, spo2), _ = await _gather([
        ("sleep", lambda c: c.get_sleep_data(day)),
        ("spo2", lambda c: c.get_spo2_data(day)),
    ])
    if not raw:
        return {"date": day, "note": "Garmin returned no sleep data for this night."}
    return _sleep_shape(day, raw, spo2 if isinstance(spo2, dict) else {})


def _sleep_shape(day: str, raw: dict[str, Any], spo2: dict[str, Any]) -> dict[str, Any]:
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
            # Breaths a minute, and blood oxygen against its usual: both creep
            # up or down with illness, altitude and hard training.
            "breathing": drop_empty({
                "average": dto.get("averageRespirationValue"),
                "lowest": dto.get("lowestRespirationValue"),
                "highest": dto.get("highestRespirationValue"),
            }),
            "spo2": drop_empty({
                "average": first_present(dto, "averageSpO2Value") or raw.get("averageSpO2Value") or spo2.get("avgSleepSpO2"),
                "lowest": first_present(dto, "lowestSpO2Value") or spo2.get("lowestSpO2"),
                "usual_7_day": spo2.get("lastSevenDaysAvgSpO2"),
            }),
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


def _power(data: Mapping[str, Any], ride: bool = False) -> dict[str, Any]:
    return cycling.power(data, ride=ride)


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
    if swimming.is_swim((activity.get("activityType") or {}).get("typeKey")):
        return swimming.summarise_swim(activity, distance, secs)
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
            "power": cycling.power(activity, ride=True),
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
    """List recent runs, rides, swims and workouts with distance, duration, pace and HR zones.

    Swims come with pace per 100 m, pool length, SWOLF and stroke rate instead
    of pace per km.

    Args:
        limit: Maximum activities to return (1-50). Defaults to 10.
        start_date: Optional first day of a date range, YYYY-MM-DD.
        end_date: Optional last day of a date range. Defaults to today when
            start_date is given.
    """
    limit = max(1, min(int(limit or 10), MAX_ACTIVITIES))

    garmin_user = _official_only()
    if garmin_user:
        start = parse_date(start_date) if start_date else None
        end = (parse_date(end_date) if end_date else parse_date("today")) if start_date else None
        rows = await anyio.to_thread.run_sync(official_data.activity_rows, garmin_user, start, end)
        flat = await anyio.to_thread.run_sync(official_data.connect_activities, garmin_user, rows[:limit])
        shown = [_summarise_activity(a) for a in flat]
        return drop_empty({
            "count": len(shown), "window": {"from": start, "to": end} if start else None,
            "truncated": len(rows) > len(shown) or None, "activities": shown,
            "source": OFFICIAL_SOURCE, "note": None if shown else OFFICIAL_NOTHING,
        })

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
            "power": _power(lap, ride=True),
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

    garmin_user = _official_only()
    if garmin_user:
        found = await anyio.to_thread.run_sync(official_data.activity, garmin_user, activity_id)
        if found is None:
            return {"activity_id": activity_id, "source": OFFICIAL_SOURCE, "note": OFFICIAL_NOTHING}
        flat, laps = found["flat"], found["laps"]
        ride = cycling.is_ride((flat.get("activityType") or {}).get("typeKey"))
        return drop_empty({
            "activity_id": activity_id,
            "summary": _summarise_activity(flat) or None,
            "hr_zones": hr_zones(found["zones"]),
            "splits_count": len(laps) or None,
            "splits": [_summarise_lap(lap, i, ride) for i, lap in enumerate(laps, start=1)] or None,
            "inside": found["inside"],
            "terrain": found["terrain"],
            "same_route": found["same_route"],
            "source": OFFICIAL_SOURCE,
        })

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

    type_key = (flat.get("activityType") or {}).get("typeKey")
    if swimming.is_swim(type_key):
        # A swim is read as its set: intervals by pace per 100, stroke and SWOLF.
        # Shoes, terrain and same-route runs don't apply in the water.
        found = swimming.pool(flat) if swimming.is_pool(type_key) else None
        sets = swimming.intervals(laps, found[1] if found else "m")
        return drop_empty({
            "activity_id": activity_id,
            "summary": _summarise_activity(flat) or None,
            "hr_zones": hr_zones(zones),
            "intervals_count": len(sets) or None,
            "intervals": sets or None,
            "how_to_read": swimming.HOW_TO_READ,
            "warnings": warnings or None,
        })

    ride = cycling.is_ride(type_key)
    try:
        # Per-km pace and lap drift are a run's questions; a ride's are in terrain.
        inside = None if ride else stream.analyse(recording, laps)
    except Exception as exc:  # noqa: BLE001 - an odd recording must not lose the splits
        inside = None
        warnings.append(f"recording could not be analysed ({type(exc).__name__})")

    around = await _around_the_run(activity_id, flat, recording, _optional, warnings)
    if ride:
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
    watts = cycling.power(flat, (ftp or {}).get("watts"), kg, ride=True)
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
    """Weather, shoes, terrain and earlier outings of the same route."""
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
                "earlier runs", lambda c: c.get_activities_by_date(since, until.isoformat())
            )
            # The list row carries what the detail endpoint leaves out, such as
            # a ride's best efforts and 20-minute power.
            own = next((r for r in earlier or [] if r.get("activityId") == activity_id), None)
            for key, value in (own or {}).items():
                flat.setdefault(key, value)
            same = terrain.same_route(this, [r for r in earlier or [] if str(r.get("startTimeLocal") or "")[:10] < day])

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
async def get_workout(workout_id: int) -> dict[str, Any]:
    """The steps of one workout, saved or scheduled: each step with its distance or time and target.

    Use it for any question about what a session involves ("what's the pace
    for Wednesday's workout", "how long is the cool-down", "what are the
    recoveries"), with the workout_id from get_plan's sessions or from
    list_workouts. Never guess a workout's paces, recoveries or warm-up from
    other sessions: read them here. Repeats keep their structure, e.g. 8 x
    [1 km at a target, 60 s recovery].

    Args:
        workout_id: The workout's id, from get_plan or list_workouts.
    """
    wid = int(workout_id)
    detail = await _read(("workout", wid), WORKOUT_MEMORY, lambda c: c.get_workout_by_id(wid)) or {}
    if not detail:
        return {"error": f"No workout {wid} in this Garmin account."}
    steps = training_plan.workout_steps(detail)
    return drop_empty({
        "workout_id": wid,
        "name": detail.get("workoutName"),
        "sport": (detail.get("sportType") or {}).get("sportTypeKey"),
        "estimated_duration": duration(detail.get("estimatedDurationInSecs")),
        "estimated_distance_km": km(detail.get("estimatedDistanceInMeters")),
        "description": (detail.get("description") or "").strip() or None,
        "main_target": training_plan.main_target(steps) if steps else None,
        "steps": steps or None,
    })


async def _ftp_for(*step_lists: Any) -> float | None:
    """The cycling FTP set in Garmin, fetched only when a power target is a percentage."""
    if not any(uses_ftp(steps) for steps in step_lists):
        return None
    shaped = cycling.shape_ftp(await _call(lambda c: c.get_cycling_ftp()))
    return float(shaped["watts"]) if shaped else None


async def _in_zones(steps: Any, sport: str | None = None) -> Any:
    """Steps with any "z2"-style target turned into the person's own zone, read from Garmin only when used."""
    wants_hr, wants_power = zones.uses(steps, "hr"), zones.uses(steps, "power")
    if not (wants_hr or wants_power):
        return steps
    kind = "cycling" if str(sport or "").lower() in ("cycling", "bike", "ride") else "running"
    hr = zones.heart_rate(await _call(lambda c: c.get_heart_rate_zones()), kind) if wants_hr else None
    watts = zones.power(await _call(lambda c: c.get_power_zones()), "cycling") if wants_power else None
    return zones.resolve(steps, hr, watts)


@mcp.tool()
@tool_errors
async def create_workout(
    name: str,
    steps: list[dict[str, Any]],
    sport: str = "running",
    description: str | None = None,
    pool_length_meters: float | None = None,
) -> dict[str, Any]:
    """Create a structured workout in Garmin Connect: always a new one, never an edit.

    schedule_workout then puts it on a date so it syncs to the watch.

    Args:
        name: Shown in Garmin Connect and on the watch.
        steps: Ordered steps, each with "type" (warmup, interval, recovery,
            rest, cooldown or repeat) and exactly one of "duration_seconds",
            "distance_meters" or "until_lap": true (ends on the lap button: a
            walk recovery between strides, a warm-up by feel, a rep to a
            landmark; still its own lap, so recoveries stay out of the rep
            splits). A repeat is {"type": "repeat", "times": 5, "steps": [...]}
            and can't contain another repeat.
            Optional target: "pace" ("4:05" or ["4:00", "4:10"], min/km), "hr"
            ([150, 165] bpm, or their Garmin zone: "z2", "z3-z4") or "power"
            (watts, 250 or [240, 260]; a share of the FTP set in Garmin, "95%"
            or ["88%", "94%"]; or a power zone, "z3").
            By default only the work reps carry a target, pace for a run;
            warm-ups, cool-downs and recoveries carry none, so the watch stays
            quiet, and the chat and the workout's description say what easy
            pace to run them at: marathon pace plus 45-75 s/km, from
            get_fitness. The watch beeps off target, so a pace target is never
            narrower than 5 s/km either side ("4:00" becomes 3:55-4:05;
            tighter is widened around its middle): about 5 either side for
            reps, 10 for tempo and threshold, 15-20 for steady and long runs,
            or heart rate for easy running.
            A ride uses power on the work steps when the rider has a power
            meter or smart trainer (power shows on rides in get_activities)
            and an FTP (get_fitness): sweet spot 88-94%, threshold 95-105%, VO2
            max 106-120%, endurance 56-75%. One value becomes a range 5% either
            side. Without a power meter, heart rate.
            Example, 15 min warm-up, 5 x 1 km at 4:05 with 90 s recoveries, 10
            min cool-down: [{"type": "warmup", "duration_seconds": 900},
            {"type": "repeat", "times": 5, "steps": [{"type": "interval",
            "distance_meters": 1000, "pace": "4:05"}, {"type": "recovery",
            "duration_seconds": 90}]}, {"type": "cooldown", "duration_seconds": 600}]
        sport: running (default), cycling, swimming, walking or hiking.
        description: Optional note stored with the workout.
        pool_length_meters: For a swim, 25 (default) or 50; the watch counts
            lengths with it.

    A swim's steps are whole pool lengths (100, 200...), each with an optional
    "stroke": freestyle, backstroke, breaststroke, butterfly, im, drill or any.
    "pace" is per 100 m ("1:45") and shows on the step as a note, not a
    beeping target. A "rest" with no duration waits for the lap button, as
    swimmers rest at the wall; "duration_seconds" fixes it. A "note" (e.g.
    "kick with board") shows on the step. Set paces from recent swims
    (get_activities, per 100 m) or their CSS. Example, 8 x 100 free at 1:45
    with 15 s rest: [{"type": "warmup", "distance_meters": 400, "stroke":
    "any"}, {"type": "repeat", "times": 8, "steps": [{"type": "interval",
    "distance_meters": 100, "stroke": "freestyle", "pace": "1:45"}, {"type":
    "rest", "duration_seconds": 15}]}, {"type": "cooldown", "distance_meters":
    200, "stroke": "any"}]
    """
    steps = await _in_zones(steps, sport)
    ftp = await _ftp_for(steps)
    workout, summary, estimated = build_workout(name, sport, steps, description, pool_length_meters, ftp)
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
    pool_length_meters: float | None = None,
) -> dict[str, Any]:
    """Change an existing workout in place: its name, its steps, or both.

    The workout keeps its id, so any dates it is already scheduled on stay
    scheduled and the watch picks up the new version at the next sync. This is
    how a plan adapts: retune the paces on next week's sessions rather than
    deleting and recreating them. Give `steps` in the same shape create_workout
    takes; leave it out to change only the name or description.

    New steps replace the old list entirely, so to change part of a workout
    (one pace, the recoveries, the cool-down) read it with get_workout first
    and send back every step, changing only what the user asked for. The
    answer carries the steps it replaced, so say exactly what changed.

    Args:
        workout_id: The workoutId from get_plan, list_workouts or create_workout.
        name: New name. Omit to keep the current one.
        steps: New step list, replacing the old one entirely. Omit to keep it.
        sport: Only needed with steps, and only to change the sport.
        description: New note. Omit to keep the current one.
        pool_length_meters: For a swim, a new pool length. Omit to keep it.
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
        new_sport = sport or current_sport
        pool = pool_length_meters
        if pool is None and new_sport == "swimming":
            kept = swimming.pool(existing)
            pool = kept[0] if kept and kept[1] == "m" else None
        steps = await _in_zones(steps, new_sport)
        workout, summary, estimated = build_workout(
            new_name,
            new_sport,
            steps,
            description if description is not None else existing.get("description"),
            pool,
            await _ftp_for(steps),
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
            # What was there before, so the change can be said exactly.
            "replaced_steps": training_plan.workout_steps(existing) if steps is not None else None,
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


# After a move the user wants to see where things landed, and the card shows
# the whole week at once. Asked for once, at the end: a card per move is
# noise, and a card drawn alongside the last move can read the calendar
# before that move has landed.
AFTER_MOVE = (
    "Once every change the user asked for is made, call get_plan on its own "
    "so they see the week as it now stands: once, after the last change, "
    "not after each one."
)


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
        "note": "Sync the watch (or open Garmin Connect on your phone) to pick it up. " + AFTER_MOVE,
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
        return {"unscheduled": schedule_id, "note": "The workout itself is kept. " + AFTER_MOVE}

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
        "note": "Off the calendar. The workout is kept and can be scheduled again. " + AFTER_MOVE,
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

    data = await anyio.to_thread.run_sync(plan.collect, _run)
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
# Weeks of running shown before this one when no block is running.
HISTORY_WEEKS = 4


async def _plan_calendar(start: date_cls, end: date_cls) -> list[dict[str, Any]]:
    return training_plan.calendar_items(await _calendar_months(start, end))


async def _calendar_months(start: date_cls, end: date_cls) -> list[Any]:
    # Every month at once: a plan scan reads a dozen, which one after another
    # was most of the card's wait.
    months = training_plan.months_between(start, end)
    answers = await _together([
        functools.partial(
            _read, ("calendar", year, month), CALENDAR_MEMORY,
            lambda c, y=year, m=month: c.get_scheduled_workouts(y, m),
        )
        for year, month in months
    ])
    payloads = []
    for (year, month), answer in zip(months, answers):
        if isinstance(answer, GarminError):
            raise answer
        if isinstance(answer, Exception):
            # One missing month should not hide the plan.
            log.debug("calendar %s-%s unavailable (%s)", year, month, type(answer).__name__)
            continue
        payloads.append(answer)
    return payloads


@_app_tool(PLAN_VIEW)
@tool_errors
async def create_plan(
    goal: str,
    sessions: list[dict[str, Any]],
    label: str | None = None,
    name: str | None = None,
    method: str | None = None,
    milestones: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a whole training block at once: every session built and put on its date.

    You design the plan, from the user's goal, current fitness (get_fitness),
    readiness, history and a methodology; this builds each session as a
    structured workout and schedules it, so the whole block is on the watch
    in one go. Show the user the plan and get their agreement first: it
    writes to their real calendar. It answers with the new block drawn as a
    card, the same as get_plan, so no other call is needed to show it.

    Each workout is named with a short plan code after a middle dot,
    "Threshold 5x1k · HM", which is how get_plan finds the block; nothing is
    stored outside Garmin. Every session is checked before anything is
    created, so a mistake in session 40 leaves no half plan behind.

    Args:
        goal: What the block is for, e.g. "Half marathon, 1:40, 15 November".
        sessions: One per session, any order: "date" (YYYY-MM-DD, today to
            30 weeks out), "name" (short, as on the watch: "Long run 18k"),
            "steps" (as for create_workout), and optional "sport" (running by
            default), "description", "pool_length_meters" for a swim (25 by
            default) and "phase": one short word such as "Base", "Build",
            "Peak" or "Taper", the same for every session in that phase; the
            card bands the weeks by it. Rest days are days with no session.
            At most 150.
        label: 2 or 3 letters or digits for the plan code, short because it
            ends every workout name: "HM", "10K", "MAR", "B1" for a base
            block. From the goal when omitted.
        name: A short name for the block, as the card's title: "Base block",
            "Marathon build". Two or three words, not the goal.
        method: The methodology in a few words, shown under the name:
            "Norwegian singles", "80/20", "Pfitzinger 18/55".
        milestones: Up to 4 points on the way to the goal, drawn as a
            timeline: {"label": "Half check", "date": "2026-11-29", "target":
            "1:22"}, date and target optional. The goal race is usually last.
    """
    today = date_cls.today()
    sessions = [
        {**s, "steps": await _in_zones(s.get("steps"), s.get("sport"))} if isinstance(s, dict) and s.get("steps") else s
        for s in sessions or []
    ]
    ftp = await _ftp_for(*[s.get("steps") for s in sessions or [] if isinstance(s, dict)])
    code, prepared = training_plan.prepare(
        goal, sessions, label, today, name=name, method=method, milestones=milestones, ftp=ftp,
    )
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
    written: dict[Any, dict[str, Any]] = {}
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
            written[workout_id] = {**payload, **(uploaded if isinstance(uploaded, dict) else {})}
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
    summary = {
        "label": code,
        "goal": goal.strip(),
        **training_plan.block_from_description(prepared[0]["workout"].description),
        "starts": first.isoformat(),
        "ends": last.isoformat(),
        "weeks": weeks,
        "sessions_created": len(created),
        "first_sessions": created[:7],
        "next_step": (
            "Every session is on the Garmin calendar and syncs to the watch. "
            f"Call get_plan with label {code} to show the user the new block as a card."
        ),
    }
    # Answer with the block as get_plan draws it, so the card shows the new
    # plan whether or not the model asks for it: told to, it sometimes
    # didn't. The calendar read above, before anything was created, is stale
    # now. If the read fails the plan still exists, so the summary stands,
    # and the card fetches the plan itself.
    session.forget()
    # The card reads each session's workout for its shape and effort. They
    # were written a moment ago, so what was sent stands in for that read:
    # a block of 30 sessions is 30 fewer round trips to Garmin before the
    # card shows. The calendar is read again, for the new schedule entries.
    for workout_id, detail in written.items():
        session.remember(("workout", workout_id), detail)
    try:
        card = await get_plan.__wrapped__(label=code)
    except GarminError:
        card = None
    except Exception:  # noqa: BLE001 - the plan is made; showing it is a bonus
        log.warning("create_plan: reading the new plan back failed", exc_info=True)
        card = None
    if isinstance(card, dict) and "error" not in card and isinstance(card.get("weeks"), list):
        return {
            **card,
            "created": {
                "sessions_created": len(created),
                "weeks": weeks,
                "first_sessions": created[:7],
            },
            "next_step": (
                "Every session is on the Garmin calendar and syncs to the watch. "
                "The user sees the new block as a card above: sum it up in a sentence or two."
            ),
        }
    return summary


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
    result = await _plan_answer(label)
    return result if label else await _with_situation(result)


async def _with_situation(result: Any) -> Any:
    """The answer with where the person is and one next step to offer them.

    With nothing scheduled, a race on the calendar is what says they're about
    to start a block, so the months ahead are read for one; they're the ones
    the plan scan just read, so this costs nothing new.
    """
    if not isinstance(result, dict):
        return result
    today = date_cls.today()
    events: list[dict[str, Any]] = []
    if "error" in result or result.get("source") == "log":
        try:
            months = await _calendar_months(today, today + timedelta(weeks=training_plan.RACE_WEEKS_AHEAD))
            events = training_plan.upcoming_events(months, today)
        except Exception:  # noqa: BLE001 - the card stands without a race
            log.debug("could not look for a race ahead", exc_info=True)
    found = training_plan.situation(result, today, events)
    return {**result, "situation": found} if found else result


async def _plan_answer(label: str | None) -> dict[str, Any]:
    today = date_cls.today()
    items = await _plan_calendar(today - PLAN_SCAN_BACK, today + PLAN_SCAN_AHEAD)
    plans = training_plan.plans_in(items)
    code: str | None = None
    context: dict[str, Any] | None = None
    calendar_first = not label
    window = training_plan.around(items, today) if calendar_first else []
    if window:
        # The calendar is the plan: everything scheduled,
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
    # Weekly distance, planned against run. It needs every run in each week, and
    # a lookup for the sessions ahead as well as behind.
    weekly_km = True
    since = training_plan.monday_of(first) if weekly_km else first - timedelta(days=1)
    # With no block running, the card shows the last four weeks' running
    # beside this week, so the one activities call reaches back that far.
    week_mode = code is None and not (context or {}).get("running")
    if week_mode:
        since = min(since, training_plan.monday_of(today) - timedelta(weeks=HISTORY_WEEKS))

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
    shapes: dict[Any, list[dict[str, Any]]] = {}
    efforts: dict[Any, str | None] = {}
    phases: dict[Any, str] = {}
    meta: dict[str, Any] = {}
    goal_code = code or (context or {}).get("label")
    # The block after a running one: its name and goal come from its own
    # workouts, which the nearest-first lookups reach once it starts soon.
    following = (context or {}).pop("upcoming", None)
    following_meta: dict[str, Any] = {}
    # Every workout is read at once, then taken in the same nearest-first
    # order as before, so the goal and block notes still come from the first.
    wanted = lookup_ids[:PLAN_LOOKUPS] or [sessions[0].get("workoutId")]
    details = await _together([
        functools.partial(_read, ("workout", wid), WORKOUT_MEMORY, lambda c, w=wid: c.get_workout_by_id(w))
        for wid in wanted
    ])
    for wid, detail in zip(wanted, details):
        if isinstance(detail, GarminError):
            raise detail
        if isinstance(detail, Exception):
            continue
        detail = detail or {}
        seconds[wid] = float(detail.get("estimatedDurationInSecs") or 0)
        if weekly_km:
            metres[wid] = training_plan.planned_metres(detail)
        shape = training_plan.workout_shape(detail)
        if shape:
            shapes[wid] = shape
        efforts[wid] = training_plan.workout_effort(detail)
        text = detail.get("description")
        if goal_code and f"Plan {goal_code}:" in (text or ""):
            goal = goal or training_plan.goal_from_description(text, goal_code)
            phase = training_plan.phase_from_description(text)
            if phase:
                phases[wid] = phase
            if not meta:
                meta = drop_empty({
                    **training_plan.block_from_description(text),
                    "milestones": training_plan.milestones_from_description(text) or None,
                })
        elif following and not following_meta and f"Plan {following['label']}:" in (text or ""):
            following_meta = drop_empty({
                "goal": training_plan.goal_from_description(text, following["label"]),
                **training_plan.block_from_description(text),
            })

    if code is None:
        result = await _with_next_steps(training_plan.summarise_calendar(
            sessions, activities, today, planned_seconds=seconds,
            planned_metres=metres, weekly_km=weekly_km, shapes=shapes, phases=phases, efforts=efforts,
        ))
        if context:
            key = "current_plan" if context.pop("running") else "upcoming_plan"
            result[key] = drop_empty({**context, "goal": goal, **meta})
            if following:
                result["upcoming_plan"] = drop_empty({**following, **following_meta})
        if week_mode:
            result["history"] = training_plan.weekly_run_km(
                activities, training_plan.monday_of(today), HISTORY_WEEKS
            )
        return result
    others = sorted(p for p in plans if p != code)
    extra = {"planned_metres": metres, "weekly_km": True} if weekly_km else {}
    result = await _with_next_steps(training_plan.summarise(
        code, sessions, activities, today, goal=goal, planned_seconds=seconds,
        shapes=shapes, phases=phases, efforts=efforts, **extra
    ))
    result.update(meta)
    if others:
        result["other_plans"] = others
    return result


async def _with_next_steps(result: dict[str, Any]) -> dict[str, Any]:
    """The next session with its steps, so the card can show the workout itself.

    One lookup, kept in memory with the rest of the workouts; a session whose
    workout can't be read is shown without its steps rather than not at all.
    """
    nxt = result.get("next_session")
    wid = (nxt or {}).get("workout_id")
    if not wid:
        return result
    try:
        detail = await _read(("workout", wid), WORKOUT_MEMORY, lambda c: c.get_workout_by_id(wid)) or {}
    except Exception:  # noqa: BLE001 - the plan matters more than its next step list
        return result
    steps = training_plan.workout_steps(detail)
    if steps:
        nxt["steps"] = steps
        target = training_plan.main_target(steps)
        if target:
            nxt["target"] = target
    return result


async def _training_log(today: date_cls) -> dict[str, Any]:
    first = training_plan.monday_of(today) - timedelta(weeks=training_plan.LOG_WEEKS - 1)
    activities = await _call(
        lambda c: c.get_activities_by_date(first.isoformat(), today.isoformat())
    ) or []
    if not activities:
        client = _official_client()
        link = store.official_link(client.user_token) if client is not None and client.alone else None
        if link is not None and time.time() - link.created_at < training_plan.LOG_WEEKS * 7 * 86400:
            # Garmin's official API sends training from the day someone
            # connects, so an empty log there is new, not idle.
            since = datetime.fromtimestamp(link.created_at, timezone.utc).date().isoformat()
            return {
                "note": (
                    f"Connected through Garmin's official API on {since}. Garmin sends training from "
                    "then on, as the watch syncs, so earlier runs aren't here and nothing has arrived "
                    "yet. Say so in a sentence, then offer to build a plan and put it on their watch."
                )
            }
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


# How many of one tool's reads go to Garmin at once. Each waits on Garmin,
# not on this server, so six at once take about as long as the slowest one.
READS_AT_ONCE = 6


async def _together(calls: list[Callable[[], Any]]) -> list[Any]:
    """Await several coroutine factories at once, at most READS_AT_ONCE in
    flight, keeping their order. Each result is its value or its exception."""
    results: list[Any] = [None] * len(calls)
    limiter = anyio.CapacityLimiter(READS_AT_ONCE)

    async def one(i: int, make: Callable[[], Any]) -> None:
        async with limiter:
            try:
                results[i] = await make()
            except Exception as exc:  # noqa: BLE001 - handed back to the caller
                results[i] = exc

    async with anyio.create_task_group() as group:
        for i, make in enumerate(calls):
            group.start_soon(one, i, make)
    return results


async def _gather(labels_and_calls: list[tuple[str, Callable[[Any], Any]]]) -> tuple[list[Any], list[str]]:
    """Run several Garmin reads at once; one weak endpoint degrades the answer, not the tool."""
    answers = await _together([functools.partial(_call, fn) for _, fn in labels_and_calls])
    results: list[Any] = []
    warnings: list[str] = []
    for (label, _), answer in zip(labels_and_calls, answers):
        if isinstance(answer, GarminError):
            raise answer
        if isinstance(answer, Exception):
            warnings.append(f"{label} unavailable ({type(answer).__name__})")
            results.append(None)
        else:
            results.append(answer)
    return results, warnings


@mcp.tool()
@tool_errors
async def get_readiness(date: str | None = None) -> dict[str, Any]:
    """Whether the user should train hard today or hold back.

    For that question, and for checking today before prescribing a hard
    session. Not for reviewing a week, a run or how training is going: get_plan
    and the activity tools answer those. Has Garmin's readiness score and its
    factors, training status (productive, strained, recovery...), acute against
    chronic load and the four-week load focus ("too much intensity?"), with
    last night's sleep, HRV against baseline, body battery, stress and resting
    heart rate, so get_sleep_data and get_daily_summary aren't needed with it.

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
    shaped = metrics.shape_readiness(day, readiness, status, hrv, stats, sleep, warnings)
    if isinstance(shaped, dict) and not shaped.get("load_focus"):
        # Some accounts' training status leaves the load focus out; Garmin's
        # load-balance endpoint has it in the same shape.
        try:
            focus = metrics.load_focus(await _call(lambda c: c.get_training_four_week_load_balance(day)))
        except GarminError:
            raise
        except Exception:  # noqa: BLE001 - the rest of the answer stands without it
            focus = None
        if focus:
            shaped = {**shaped, "load_focus": focus}
    return shaped


@mcp.tool()
@tool_errors
async def get_fitness() -> dict[str, Any]:
    """Garmin's read on the user's current fitness, as markers rather than history.

    VO2 max and fitness age, predicted race times for 5k to marathon, lactate
    threshold heart rate and pace, endurance score, hill score and running
    tolerance, each with its own classification where Garmin gives one. Use it
    to set goals and training paces from evidence: "Garmin thinks you are a
    1:42 half today" is a better starting point than asking. Also the heart
    rate and power zones set in Garmin, which workouts can target as "z2".
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
    (ftp_raw, profile, hr_raw, power_raw), more = await _gather(
        [
            ("cycling ftp", lambda c: c.get_cycling_ftp()),
            ("weight", lambda c: c.get_user_profile()),
            ("heart rate zones", lambda c: c.get_heart_rate_zones()),
            ("power zones", lambda c: c.get_power_zones()),
        ]
    )
    ftp = cycling.shape_ftp(ftp_raw, cycling.weight_kg(profile))
    ride = {"cycling_ftp": ftp} if ftp else None
    # The zones the watch uses; a workout step can name one ("z2").
    own = drop_empty({
        "heart_rate": zones.heart_rate(hr_raw, "running"),
        "cycling_heart_rate": (lambda z: z if z and z.get("sport") == "cycling" else None)(zones.heart_rate(hr_raw, "cycling")),
        "power": zones.power(power_raw, "cycling"),
    })
    warnings = warnings + [w for w in more if not w.startswith(("weight", "power zones"))]
    if all(x is None for x in (vo2, race, lactate, endurance, hill, tolerance)) and not ride and not own:
        return {"error": "Garmin returned no fitness data.", "warnings": warnings}
    return {
        **metrics.shape_fitness(vo2, race, lactate, endurance, hill, tolerance, warnings),
        **(ride or {}),
        **({"zones": own} if own else {}),
    }


FITNESS_TREND_MONTHS = 6
MAX_FITNESS_TREND_MONTHS = 12


@mcp.tool()
@tool_errors
async def get_fitness_trends(months: int = FITNESS_TREND_MONTHS) -> dict[str, Any]:
    """Whether the user is getting fitter: Garmin's fitness markers month by month.

    VO2 max, predicted 5k, 10k, half and marathon times, lactate threshold
    pace and heart rate, endurance and hill score, and cycling FTP, each as a
    value per month with the change from the first month to now and whether
    that is improving, steady or declining. Alongside them, weight by month
    from the weigh-ins logged in Garmin Connect, with body fat and muscle
    where a scale records them. Use it for "am I getting fitter", "how's my
    fitness trending", "is my VO2 max going up", "has my half prediction come
    down" or "was I faster when I was lighter", and answer in writing: name
    the markers that moved and by how much, in plain terms. For weight, line
    the months up and say how the markers moved as weight changed, as a
    pattern rather than a cause. With no weigh-ins logged, say so and that
    weighing in through Garmin Connect or a Garmin scale is what fills it. For where they stand today, get_fitness
    is enough; for whether recovery is keeping up, get_recovery_trends.

    Args:
        months: Months to look back, including this one (1-12). Defaults to 6.
    """
    months = max(1, min(int(months), MAX_FITNESS_TREND_MONTHS))
    end = parse_date("today")
    end_day = date_cls.fromisoformat(end)
    # Garmin's race predictor refuses a range over a year.
    start = (end_day - timedelta(days=months * 30)).isoformat()
    month_starts = date_cls.fromisoformat(start)
    (vo2, race, lactate, endurance, hill, ftp, weight), warnings = await _gather(
        [
            ("vo2 max", _chunked(lambda c, lo, hi: c.get_max_metrics_range(lo, hi), month_starts, end_day)),
            ("race predictions", lambda c: c.get_race_predictions(start, end, "monthly")),
            ("lactate threshold", lambda c: c.get_lactate_threshold(
                latest=False, start_date=start, end_date=end, aggregation="monthly")),
            ("endurance score", lambda c: c.get_endurance_score(start, end)),
            ("hill score", lambda c: c.get_hill_score(start, end)),
            ("cycling ftp", lambda c: c.get_functional_threshold_power_range(
                start, end, sport="CYCLING", aggregation="monthly")),
            ("weight", lambda c: c.get_body_composition(start, end)),
        ]
    )
    shaped = fitness_trends.shape(
        vo2=vo2, race=race, lactate=lactate, endurance=endurance, hill=hill, ftp=ftp, weight=weight,
        start=start, end=end, warnings=warnings,
    )
    if not set(shaped) - {"period", "how_to_read", "warnings"}:
        return {"error": "Garmin returned no fitness history for that period.", "warnings": warnings}
    return shaped


RECOVERY_WEEKS = 4
MAX_RECOVERY_WEEKS = 12
# Garmin's daily ranges answer a month at a time.
RANGE_CHUNK_DAYS = 28


def _chunked(fetch: Callable[[Any, str, str], Any], start: date_cls, end: date_cls) -> Callable[[Any], list[Any]]:
    """One read of a long range, as the 28-day pieces Garmin allows, fetched
    side by side: six months of VO2 max was seven reads one after another."""
    spans: list[tuple[str, str]] = []
    lo = start
    while lo <= end:
        hi = min(lo + timedelta(days=RANGE_CHUNK_DAYS - 1), end)
        spans.append((lo.isoformat(), hi.isoformat()))
        lo = hi + timedelta(days=1)

    def run(c: Any) -> list[Any]:
        with ThreadPoolExecutor(max_workers=min(READS_AT_ONCE, len(spans) or 1)) as pool:
            pieces = list(pool.map(lambda span: fetch(c, *span), spans))
        rows: list[Any] = []
        for got in pieces:
            if isinstance(got, dict):
                got = got.get("hrvSummaries") or []
            rows.extend(got or [])
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


# Activity types a run or ride can be changed to, by the key Garmin uses.
_ACTIVITY_TYPES = (
    "running", "trail_running", "treadmill_running", "track_running", "indoor_running",
    "cycling", "road_biking", "mountain_biking", "gravel_cycling", "indoor_cycling", "virtual_ride",
    "walking", "hiking", "lap_swimming", "open_water_swimming", "strength_training",
)


class ActivityError(Exception):
    """A change to a recorded activity that can't be made as asked, said plainly."""


def _match_shoe(shoes: list[Mapping[str, Any]], wanted: str) -> Mapping[str, Any]:
    key = wanted.strip().lower()
    named = [(g, (gear.name_of(g) or "").lower(), (gear.model_of(g) or "").lower()) for g in shoes]
    exact = [g for g, n, m in named if key in (n, m)]
    found = exact or [g for g, n, m in named if key in n or key in m]
    if len(found) == 1:
        return found[0]
    names = ", ".join(sorted(gear.name_of(g) or "unnamed" for g in (found or shoes)))
    if found:
        raise ActivityError(f"More than one pair matches {wanted!r}: {names}. Say which.")
    raise ActivityError(f"No active shoes match {wanted!r}. The user's shoes: {names or 'none set up in Garmin'}.")


@mcp.tool()
@tool_errors
async def update_activity(
    activity_id: int,
    name: str | None = None,
    notes: str | None = None,
    shoes: str | None = None,
    activity_type: str | None = None,
) -> dict[str, Any]:
    """Change a recorded run or ride: its name, notes, shoes or type. Pass only what changes; the answer says what each was.

    Args:
        activity_id: From get_activities.
        name: The new title.
        notes: Replaces the notes; read them with get_activity_details to add to them.
        shoes: A pair's name from get_shoes, replacing any on it; "none" takes them off.
        activity_type: e.g. trail_running, treadmill_running, track_running, indoor_cycling, walking.
    """
    if all(v is None for v in (name, notes, shoes, activity_type)):
        return {"error": "Nothing to change: pass a name, notes, shoes or an activity_type."}
    current = await _call(lambda c: c.get_activity(activity_id)) or {}
    if not current:
        return {"error": f"No activity {activity_id} in Garmin. Find its id with get_activities."}
    done: dict[str, Any] = {}

    if name is not None:
        title = name.strip()
        if not title:
            return {"error": "A name can't be empty."}
        await _call(lambda c: c.set_activity_name(activity_id, title[:120]))
        done["name"] = {"was": current.get("activityName"), "now": title[:120]}

    if notes is not None:
        before = first_present(current, "description") or (current.get("summaryDTO") or {}).get("description")
        await _call(lambda c: c.set_activity_description(activity_id, notes[:2000]))
        done["notes"] = {"was": before, "now": notes[:2000]}

    if activity_type is not None:
        key = activity_type.strip().lower().replace(" ", "_").replace("-", "_")
        if key not in _ACTIVITY_TYPES:
            return {"error": f"Activity types: {', '.join(_ACTIVITY_TYPES)}.", **({"done": done} if done else {})}
        types = await _call(lambda c: c.get_activity_types()) or []
        row = next((t for t in types if isinstance(t, Mapping) and t.get("typeKey") == key), None)
        if not row:
            return {"error": f"Garmin doesn't list the type {key}.", **({"done": done} if done else {})}
        await _call(lambda c: c.set_activity_type(activity_id, row["typeId"], key, row.get("parentTypeId")))
        done["activity_type"] = {"was": (current.get("activityTypeDTO") or {}).get("typeKey"), "now": key}

    if shoes is not None:
        on = [g for g in (await _call(lambda c: c.get_activity_gear(activity_id)) or [])
              if isinstance(g, Mapping) and gear.is_shoe(g)]
        target = None
        if shoes.strip().lower() not in ("none", "no shoes", ""):
            number = await _call(
                lambda c: getattr(c, "profile_id", None) or (c.get_device_last_used() or {}).get("userProfileNumber")
            )
            mine = [g for g in (await _call(lambda c: c.get_gear(number)) or [])
                    if isinstance(g, Mapping) and gear.is_shoe(g)
                    and str(g.get("gearStatusName") or "active").lower() != "retired"]
            target = _match_shoe(mine, shoes)
        for g in on:
            if not target or g.get("uuid") != target.get("uuid"):
                await _call(lambda c, u=g.get("uuid"): c.remove_gear_from_activity(u, activity_id))
        if target and not any(g.get("uuid") == target.get("uuid") for g in on):
            await _call(lambda c: c.add_gear_to_activity(target["uuid"], activity_id))
        done["shoes"] = {"was": [gear.name_of(g) for g in on] or None, "now": gear.name_of(target) if target else None}

    return {"activity_id": activity_id, "changed": done}


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


# --------------------------------------------------------------------------
# Daash Crews
# --------------------------------------------------------------------------

CREW_NOTE = (
    "The user is looking at their crew's week as an interactive card. Don't redraw it: "
    "say in a sentence or two which runs they could do together and who with. Answer "
    "follow-up questions about the crew from this result, in a line, without calling "
    "get_crew again unless a day has passed or they ask to see it afresh. Matches "
    "are runs already planned on the same day: don't suggest moving anyone's run to "
    "make one unless the user asks. The crew's club run is a fixture, not a match. "
    "Never share a crew member's details outside the crew."
)


async def _read_my_week() -> None:
    """Bring the asking member's own runs up to date from their plan, at most daily.

    The crew normally learns someone's week when they look at their plan. Right
    after joining, or after days away, that hasn't happened, and their own row
    would say "no week yet" on the very card they're looking at.
    """
    if not crews.available() or crews.my_week_read_on() == date_cls.today().isoformat():
        return
    try:
        plan = await get_plan.__wrapped__()
        member = crews.current()
        if member is not None and isinstance(plan, dict) and "error" not in plan:
            crews.capture(member.key, plan)
    except Exception:  # noqa: BLE001 - the card still draws, with what the crew had
        log.debug("could not read the member's own week for the crew", exc_info=True)


async def _crew_card(week_of: str | None = None) -> dict[str, Any]:
    day = date_cls.fromisoformat(parse_date(week_of)) if week_of else None
    if crews.available() and not crews.in_crew():
        # Said before reading their Garmin week, which nothing would keep.
        raise crews.CrewError("You're not in a crew. Join one with the code a friend sent, or start one.")
    await _read_my_week()
    card = _with_page_url(crews.week(day))
    note = CREW_NOTE
    crew = card.get("crew") or {}
    # Said here as well as in update_crew's description: a chat that kept an
    # older copy of the tool list doesn't know the argument is there.
    if crew.get("page_url"):
        note += (
            f" The crew's page is on, at {crew['page_url']}: give it when the group wants the week as a link. "
            "Friends without Garmin or Daash join from that page: they tap Add your easy runs, pick their usual "
            "slots and a name and PIN, and show as \"by hand\", matched like everyone else. They change "
            "their own runs there, whether they do the club run included: Edit my runs on that page."
        )
    elif crew.get("you_started_it"):
        note += (
            " The user started this crew, so they can turn on a web page of its week for the group: "
            "update_crew with group_link \"on\" (it takes that argument even if an older copy of its "
            "description doesn't show it). It asks first, then call again with confirm=true. That page is "
            "also how friends without Garmin or Daash join the crew's week: they add their easy runs there."
        )
    return {"for_the_assistant": note, **card}


# Where the hosted server answers, for a crew page's address.
CREW_PAGE_BASE = os.environ.get("GARMIN_MCP_BASE_URL", "").strip().rstrip("/") or "https://mcp.garmin.daash.run"


def crew_page_url(link: str) -> str:
    return f"{CREW_PAGE_BASE}/crew/{link}"


def _with_page_url(card: dict[str, Any]) -> dict[str, Any]:
    crew = dict(card.get("crew") or {})
    link = crew.pop("link", None)
    if link:
        crew["page_url"] = crew_page_url(link)
    return {**card, "crew": crew}


@mcp.tool()
@tool_errors
async def garmin_official(action: str = "status", date: str = "tomorrow") -> dict[str, Any]:
    """Preview: Daash on Garmin's official API, tried with your own account first.

    Args:
        action: "status" says whether this account is connected and what
            Garmin has sent so far; "connect" gives a link to approve Daash on
            Garmin's own sign-in page; "test_workout" sends an easy 20-minute
            run through Garmin's Training API, scheduled on `date`;
            "disconnect" ends the official link and deletes what Garmin sent;
            "activities" reads the recent activities Garmin sent, with splits
            and laps; "days" reads each recent day's sleep, HRV, resting heart
            rate, stress, Body Battery, steps and VO2 max; "official_only"
            answers the day, sleep and activity tools from Garmin's official
            API alone, and "both" goes back to Garmin Connect for them;
            "calendar" asks Garmin's Training API what is scheduled from
            `date` for the following six weeks, to see whether it lists only
            what Daash scheduled or everything on the calendar.
        date: For test_workout, the day to schedule it on.
    """
    user_token = official.current()
    if user_token is None or not official.configured():
        return {"error": "Garmin's official API is only on the hosted server, once it's set up there."}
    action = (action or "status").strip().lower()
    if action in ("status", "connect"):
        found = await anyio.to_thread.run_sync(official.status, user_token)
        if action == "connect" and found.get("connected"):
            found["note"] = "Already connected. Say 'disconnect' first to connect a different Garmin account."
        return found
    if action in ("official_only", "both"):
        on = action == "official_only"
        if not await anyio.to_thread.run_sync(store.set_official_only, user_token, on):
            return {"error": "Not connected to Garmin's official API yet. Use action 'connect' first."}
        return {"official_only": on, "note": (
            "get_daily_summary, get_sleep_data, get_activities and get_activity_details now answer only from "
            "Garmin's official API, and workouts, scheduling and plans go through Garmin's Training API. "
            "The calendar and the workout list show what Daash wrote through it; readiness, fitness, "
            "heart rate and gear still use Garmin Connect." if on else
            "Back to Garmin Connect for every tool.")}
    if action == "calendar":
        start = parse_date(date if date != "tomorrow" else "today")
        end = (date_cls.fromisoformat(start) + timedelta(weeks=6)).isoformat()
        try:
            return await anyio.to_thread.run_sync(official.scheduled, user_token, start, end)
        except official.OfficialError as exc:
            return {"error": str(exc)}
    if action in ("activities", "days"):
        found = await anyio.to_thread.run_sync(official.read, user_token, action)
        if found is None:
            return {"error": "Not connected to Garmin's official API yet. Use action 'connect' first."}
        return {action: found, "source": "Garmin's official API: only what Garmin has pushed since connecting.",
                "note": None if found else "Nothing yet: Garmin sends data when the watch syncs."}
    if action == "disconnect":
        gone = await anyio.to_thread.run_sync(official.disconnect, user_token)
        return {"disconnected": gone}
    if action == "test_workout":
        model, summary, _ = build_workout(
            "Daash test: easy 20 min", "running",
            [{"type": "warmup", "duration_seconds": 300}, {"type": "interval", "duration_seconds": 900},
             {"type": "cooldown", "duration_seconds": 300}],
            description="Sent through Garmin's official Training API.",
        )
        workout = official.training_workout(model, "running")
        try:
            sent = await anyio.to_thread.run_sync(official.send_workout, user_token, workout, parse_date(date))
        except official.OfficialError as exc:
            return {"error": str(exc)}
        return {**sent, "summary": summary, "note": "Sync the watch to see it."}
    return {"error": f"Unknown action {action!r}. Use status, connect, activities, days, calendar, official_only, both, test_workout or disconnect."}


@_app_tool(CREW_VIEW)
@tool_errors
async def get_crew(week_of: str | None = None) -> dict[str, Any]:
    """The user's Daash crew this week: who is running when, and which runs they could do together.

    A crew is a few friends, mostly using Daash. Each member's easy and long runs
    (day and distance) and the crew's standing club run are pooled;
    hard sessions show only as "hard". Matches are easy or long runs already
    planned on the same day at a usual time they share, whatever their pace; nobody's run is
    moved to make one. An easy and a long run match too ("mixed"): they share
    the shorter run.

    Call it when the user asks about the crew as a whole: "who's running this
    week", "show the crew", "post the crew's week to the group" (the answer's
    group_message is the week as a calendar, ready to paste into WhatsApp or
    Messenger). It draws a card, so for a question about one day or one
    person ("is anyone running Sunday", "is Tom in on Wednesday") answer in a
    line from the card already in the chat; call again only if there isn't
    one or a day has passed. Never call it together with get_plan: a question
    about the user's own training is get_plan's alone. A member's runs are as of the last time
    they looked at their own plan ("updated_on"). If the user isn't in a crew
    it says so: join_crew is how they join or start one. crew.page_url, when
    the crew's starter has turned its page on (update_crew group_link), is a
    link anyone in the group can open any time to see this week and next, and
    where friends without Garmin or Daash add their easy runs ("by hand").

    Args:
        week_of: Any date in the week to show. Defaults to this week, or on a
            Sunday to the week ahead.
    """
    return await _crew_card(week_of)


@_app_tool(CREW_VIEW)
@tool_errors
async def join_crew(
    your_name: str,
    code: str | None = None,
    start_new_crew: bool = False,
    crew_name: str | None = None,
    usual_times: list[str] | None = None,
    goes_to_club_run: bool | None = None,
    club_run_day: str | None = None,
    club_run_time: str | None = None,
    club_run_km: float | None = None,
    club_run_place: str | None = None,
) -> dict[str, Any]:
    """Join a friend's Daash crew with the invite code they sent, or start a new crew.

    A Daash crew is a few friends: it shows them each other's easy and long
    runs for the week and points out the days they're already running the
    same kind of run, so they can run together. This joins with Daash; a
    friend without Garmin or Daash adds their easy runs on the crew's page
    link instead (see get_crew). Before joining,
    tell the user plainly what the crew will see: their chosen name, usual
    running times, and the day and distance of their easy and long
    runs for the next two weeks; hard sessions show only as "hard", and
    nothing else from Garmin is shared. Ask for anything missing below rather
    than guessing.

    Args:
        your_name: What the crew calls the user, e.g. "Bart".
        code: The invite code, like "DC-7Q4M", to join a friend's crew.
        start_new_crew: True to start a crew instead of joining one.
        crew_name: Optional, when starting: what the card calls the crew. Left
            out, it is named after the user ("Bart's crew"); the crew can
            rename it later.
        usual_times: When the user usually runs: mornings, afternoons or
            evenings, on weekdays or at weekends, as any of weekday_morning,
            weekday_afternoon, weekday_evening, weekend_morning,
            weekend_afternoon, weekend_evening ("mornings" alone means weekday
            and weekend; a lunchtime run is an afternoon). Garmin's calendar
            has days but no times, so this is how runs on the same day are
            matched by time.
        goes_to_club_run: Whether the user usually goes to the crew's club run.
        club_run_day: Optional, when starting: if the crew already runs
            together on a set day every week (a club run), its day, e.g.
            "fri". That run shows on everyone's week but is never offered as
            a match: the crew is for syncing the other days.
        club_run_time: Its start time, "HH:MM", e.g. "06:45".
        club_run_km: Its distance in km, e.g. 4.3.
        club_run_place: Where it starts, a few words.
    """
    club = crews._clean_club(club_run_day, club_run_time, club_run_km, club_run_place)
    if code:
        joined = crews.join(code, your_name, usual_times, goes_to_club_run)
        action = "joined"
    elif start_new_crew or crew_name:
        joined = crews.start(crew_name, your_name, usual_times, club, goes_to_club_run)
        action = "started"
    else:
        raise crews.CrewError("Give the invite code a friend sent, or say the user wants to start a new crew.")
    card = await _crew_card()
    card["just"] = {
        "action": action, "crew": joined["crew"], "code": joined["code"],
        "next": (
            "Their own runs are on the card already, and stay current each time they look at "
            "their plan. " + (
                f"Share the code {joined['code']} with friends to invite them." if action == "started" else ""
            )
        ).strip(),
    }
    return card


@mcp.tool()
@tool_errors
async def update_crew(
    your_name: str | None = None,
    usual_times: list[str] | None = None,
    goes_to_club_run: bool | None = None,
    crew_name: str | None = None,
    club_run_day: str | None = None,
    club_run_time: str | None = None,
    club_run_km: float | None = None,
    club_run_place: str | None = None,
    remove_member: str | None = None,
    group_link: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Change the user's details in their Daash crew, or the crew's own settings.

    Anyone can change their own name, usual times and whether they go to the
    club run. Whoever started the crew can also rename it, set or change its
    club run, and remove a member by name. Only pass what changes.

    Args:
        your_name: A new name for the user in the crew.
        usual_times: When the user usually runs, as for join_crew.
        goes_to_club_run: Whether the user usually goes to the club run.
        crew_name: A new name for the crew.
        club_run_day: The club run's day, e.g. "fri"; with the time, km and
            place below. Pass "none" to drop the club run.
        club_run_time: Its start time, "HH:MM".
        club_run_km: Its distance in km.
        club_run_place: Where it starts.
        remove_member: The name of a member to take out of the crew.
        group_link: The crew's page: "on" for a link the group can open any
            time to see this week and next, "new" to replace it with a fresh
            one (the old stops working), "off" to stop it. Only whoever
            started the crew can change it. Before "on" or "new", tell the
            user that anyone with the link can see members' first names, the
            days they run, the time of day and easy, long or hard, and get
            their yes; then call again with confirm=true. Give them the
            page_url to pin in the group chat's description.
        confirm: True once the user has agreed to the link, as above.
    """
    if group_link is not None:
        choice = str(group_link).strip().lower()
        if choice not in ("on", "new", "off"):
            return {"error": 'group_link is "on", "new" or "off".'}
        if choice != "off" and not confirm:
            return {
                "needs_confirmation": (
                    "Anyone with the crew's link will be able to see members' first names, the days "
                    "they run this week and next, the time of day and easy, long or hard: never pace "
                    "or anything else. Ask the user, then call again with confirm=true."
                )
            }
        link = crews.set_link(choice != "off", renew=choice == "new")
        if not link:
            return {"group_link": "off", "next": "The old link no longer works."}
        return {
            "group_link": "on", "page_url": crew_page_url(link),
            "next": "Give the user the page_url to pin in the group chat's description. It updates as members look at their plans.",
        }
    club: dict[str, Any] | None = None
    if club_run_day and str(club_run_day).strip().lower() == "none":
        club = {}
    else:
        club = crews._clean_club(club_run_day, club_run_time, club_run_km, club_run_place)
    done = crews.update(your_name, usual_times, goes_to_club_run, crew_name, club, remove_member)
    return {**done, "next": "Show the crew with get_crew if the user wants to see it."}


@mcp.tool()
@tool_errors
async def leave_crew(confirm: bool = False) -> dict[str, Any]:
    """Leave the user's Daash crew, deleting what it kept of their runs.

    The crew stops seeing their runs at once. The last person to leave deletes
    the crew. Ask the user before calling it with confirm=true.

    Args:
        confirm: Must be true, after the user has said to leave.
    """
    if not confirm:
        return {"error": "Ask the user to confirm leaving the crew, then call again with confirm=true."}
    return crews.leave()


@mcp.tool()
@tool_errors
async def get_connection_status() -> dict[str, Any]:
    """Check whether the server is logged in to Garmin Connect.

    Reports which credentials are present and whether the cached session is
    usable. Never returns the password or the cached token itself.
    """
    client = _official_client()
    if client is not None and client.alone:
        status = await anyio.to_thread.run_sync(official.status, client.user_token)
        status = {"logged_in": True, "via": "Garmin's official API",
                  **{k: status.get(k) for k in ("since", "permissions")}}
        if not official.can_write(client.user_token):
            status["note"] = ("Putting workouts on the watch was left off when this account approved Daash in "
                              "Garmin. It can be turned on in Garmin Connect's connected apps settings.")
        return status
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
