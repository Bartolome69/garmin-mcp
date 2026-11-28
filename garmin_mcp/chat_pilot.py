"""The in-app chat pilot: a private, flag-gated chat view in the companion app.

A deliberate, temporary exception to "the app makes no model calls" (APP.md),
for the people listed in CHAT_PILOT_USER_IDS only (Bart), to see whether a
chat view is worth having before any Plus or iPhone work. Off for everyone
else: no Chat tab, and every chat route answers the same 404 an unknown app
route does. With the variable unset it is off for everyone.

How it fits what's already here:

- Signed in like every other /app/api route (SessionBinding), so every tool
  function runs exactly as it does for the app's screens and for Claude.
- The model reads through the same tool functions the MCP server exposes
  (server.get_plan, get_activities, get_readiness...), with the same
  descriptions and argument schemas, so there is no second Garmin client and
  no second plan model. MCP behaviour for ChatGPT, Claude and the rest is
  untouched: nothing here is registered as a tool.
- Writes never run while the model is answering. A write the model asks for
  is stored as a proposed change (a chat_messages row, status pending) and
  shown as a card; only POST /app/api/chat/action with Confirm runs that exact
  stored call, once, with no model involved. Decline cancels it.
- Memory: the reply streams to the phone as it's written, the conversation
  lives in SQLite, and nothing is kept between requests but those rows. A
  tool's answer is cut down before the model sees it and is never stored.

The provider, model and key come from the environment, so switching between
Claude Haiku, GPT-4o-mini and Gemini Flash needs no code change (see env()).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, AsyncIterator, Mapping
from zoneinfo import ZoneInfo

from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from . import app as app_routes
from . import server as server_mod
from . import store
from .server import mcp

try:  # mcp >= 2.2 ships httpx2; older installs have httpx (as google_oauth.py does)
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx  # type: ignore[no-redef]

log = logging.getLogger(__name__)

LONDON = ZoneInfo("Europe/London")
PREFIX = app_routes.API_PREFIX + "chat"

# -- configuration ---------------------------------------------------------------
# Read when used, not at import, so a test or a restart with new secrets takes effect.

DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openai": "gpt-4o-mini", "gemini": "gemini-2.5-flash"}
DEFAULT_BASE = {
    "anthropic": "https://api.anthropic.com/v1",
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}
KEY_FALLBACK = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
# USD per million tokens (input, output): list prices when this was written, for the
# estimate only. CHAT_PRICE_INPUT_PER_M / CHAT_PRICE_OUTPUT_PER_M override them.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float | None) -> float | None:
    try:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else default
    except ValueError:
        return default


def env() -> dict[str, Any]:
    """The pilot's settings, with their defaults."""
    provider = os.environ.get("CHAT_PROVIDER", "").strip().lower() or "anthropic"
    if provider not in DEFAULT_MODELS:
        provider = "anthropic"
    model = os.environ.get("CHAT_MODEL", "").strip() or DEFAULT_MODELS[provider]
    price_in, price_out = PRICES.get(model, (None, None))
    return {
        "provider": provider,
        "model": model,
        "api_key": (os.environ.get("CHAT_API_KEY", "").strip() or os.environ.get(KEY_FALLBACK[provider], "").strip()),
        "base_url": (os.environ.get("CHAT_BASE_URL", "").strip() or DEFAULT_BASE[provider]).rstrip("/"),
        # Claude only: how hard it thinks. "low" keeps a chat reply cheap and quick; "" sends none.
        "effort": os.environ.get("CHAT_EFFORT", "low").strip(),
        "daily_cap": _int("CHAT_DAILY_MESSAGE_CAP", 500),
        "context_messages": _int("CHAT_CONTEXT_MESSAGES", 20),
        "max_tool_calls": _int("CHAT_MAX_TOOL_CALLS", 8),
        "max_output_tokens": _int("CHAT_MAX_OUTPUT_TOKENS", 1024),
        "max_request_tokens": _int("CHAT_MAX_REQUEST_TOKENS", 60000),
        "provider_timeout": _int("CHAT_PROVIDER_TIMEOUT_S", 30),
        "turn_timeout": _int("CHAT_TURN_TIMEOUT_S", 90),
        "tool_timeout": _int("CHAT_TOOL_TIMEOUT_S", 25),
        "write_timeout": _int("CHAT_WRITE_TIMEOUT_S", 120),
        "tool_result_chars": _int("CHAT_TOOL_RESULT_CHARS", 6000),
        "price_in": _float("CHAT_PRICE_INPUT_PER_M", price_in),
        "price_out": _float("CHAT_PRICE_OUTPUT_PER_M", price_out),
    }


# How long a proposed change can wait for Confirm: after a day the plan may have moved on.
ACTION_TTL = 24 * 3600
STARTERS = (
    "How did this week go?",
    "I'm short on time tomorrow, adjust",
    "Move my long run to Saturday",
    "Am I recovered enough for a hard session today?",
    "What's on my plan this weekend?",
)


# -- the flag --------------------------------------------------------------------

def _allowlist() -> set[str]:
    return {i.strip() for i in os.environ.get("CHAT_PILOT_USER_IDS", "").split(",") if i.strip()}


def enabled_for(user_token: str | None) -> bool:
    """Whether this person has the pilot: their PostHog id or email fingerprint is in
    CHAT_PILOT_USER_IDS. Off when the variable is missing or empty."""
    from . import analytics

    allowed = _allowlist()
    if not allowed or not user_token or user_token == "local":
        return False
    user = store.get_user(user_token)
    if user is None:
        return False
    ids = {analytics.distinct_id(user.email_hash or user_token)}
    if user.email_hash:
        ids.add(user.email_hash)
    return bool(ids & allowed)


def _person(user_token: str) -> str:
    user = store.get_user(user_token)
    return (user.email_hash if user and user.email_hash else None) or user_token


def _who() -> str | None:
    from . import official

    return official.current()


NOT_FOUND = {"error": "Not found"}


def _not_found() -> Response:
    # The same answer as an unknown /app/api/ path (app.app_file), so nothing says the feature exists.
    return app_routes._json(dict(NOT_FOUND), 404)


def _track(event: str, properties: Mapping[str, Any] | None = None) -> None:
    app_routes._track(event, {k: v for k, v in (properties or {}).items() if v is not None})


# -- tools -----------------------------------------------------------------------
# Reads run as the model asks. Writes are proposed and wait for Confirm.

READS = ("get_plan", "get_session", "get_activities", "get_activity_details", "get_readiness", "get_sleep_data",
         "get_daily_summary", "get_workout", "list_workouts", "find_exercises", "get_fitness",
         # Nov 28: how fitness and recovery have moved, weekly volume, heart rate through a day, shoes.
         "get_fitness_trends", "get_recovery_trends", "get_progress", "get_heart_rate", "get_shoes")
WRITES = ("create_workout", "create_strength_workout", "update_workout", "schedule_workout", "unschedule_workout",
          "delete_workout", "create_plan", "remove_plan", "move_session", "count_run")
# The two writes that already ask first in MCP: a call without confirm only reads back what would go.
PREVIEWED = ("delete_workout", "remove_plan")
WRITE_NOTE = (" In this app nothing changes when you call this: the user sees a card with what it will do and "
              "taps Confirm, so describe it as proposed, not done.")
EXTRA_TOOLS = {
    "move_session": {
        "description": "Move a planned session to another day (unschedule then schedule, as the plan's "
                       "how_to_adapt says). Use this to move a session rather than two calls.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "The session's current date, YYYY-MM-DD."},
            "to": {"type": "string", "description": "The new date, YYYY-MM-DD, today or later."},
            "schedule_id": {"type": "integer", "description": "The session's schedule_id, when the day has two."},
        }, "required": ["date", "to"]},
    },
    "count_run": {
        "description": "Change which planned session a recorded run counts as: action 'session' (with that "
                       "session's schedule_id), 'extra' (an extra run), or 'undo' (back to how Daash matched it).",
        "parameters": {"type": "object", "properties": {
            "activity_id": {"type": "integer"},
            "action": {"type": "string", "enum": ["session", "extra", "undo"]},
            "schedule_id": {"type": "integer"},
        }, "required": ["activity_id", "action"]},
    },
}
TOOL_LABELS = {
    "get_plan": "Reading your plan", "get_session": "Reading the session", "get_activities": "Looking at your runs",
    "get_activity_details": "Looking at the run", "get_readiness": "Checking readiness",
    "get_sleep_data": "Checking sleep", "get_daily_summary": "Checking your days", "get_workout": "Reading the workout",
    "list_workouts": "Reading your workouts", "find_exercises": "Finding exercises", "get_fitness": "Checking fitness",
    "get_fitness_trends": "Looking at fitness over time", "get_recovery_trends": "Looking at recovery over time",
    "get_progress": "Looking at your weeks", "get_heart_rate": "Checking heart rate", "get_shoes": "Checking your shoes",
}


def _match_on(user_token: str | None) -> bool:
    try:
        from . import matching

        return bool(matching.enabled(user_token))
    except Exception:  # noqa: BLE001 - no matching, no count_run
        return False


def _clean_schema(schema: Any) -> Any:
    """A tool's argument schema without the titles the SDK adds, which some providers refuse."""
    if isinstance(schema, dict):
        return {k: _clean_schema(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]
    return schema


def tool_specs(user_token: str | None) -> list[dict[str, Any]]:
    """The tools offered to the model: the MCP tools' own descriptions and schemas, built per request."""
    names = set(READS) | set(WRITES)
    out = []
    for tool in mcp._tool_manager.list_tools():
        if tool.name not in names:
            continue
        params = _clean_schema(dict(tool.parameters or {"type": "object", "properties": {}}))
        if tool.name in PREVIEWED:
            # Confirm is the card's job here: the model never passes it.
            params["properties"] = {k: v for k, v in (params.get("properties") or {}).items() if k != "confirm"}
            params["required"] = [r for r in params.get("required") or [] if r != "confirm"]
        description = (tool.description or "").strip()
        out.append({"name": tool.name, "description": description + (WRITE_NOTE if tool.name in WRITES else ""),
                    "parameters": params})
    for name, spec in EXTRA_TOOLS.items():
        if name == "count_run" and not _match_on(user_token):
            continue
        out.append({"name": name, "description": spec["description"] + WRITE_NOTE, "parameters": spec["parameters"]})
    return out


def _fn(name: str) -> Any:
    return getattr(server_mod, name, None)


def _check_args(name: str, args: Mapping[str, Any]) -> str | None:
    """Why these arguments can't be used for this tool, or None."""
    if name in EXTRA_TOOLS:
        need = EXTRA_TOOLS[name]["parameters"]["required"]
        missing = [k for k in need if args.get(k) in (None, "")]
        return f"{name} needs {', '.join(missing)}." if missing else None
    fn = _fn(name)
    if fn is None:
        return f"No tool called {name}."
    try:
        inspect.signature(fn).bind(**args)
    except TypeError as exc:
        return f"Those arguments don't fit {name}: {exc}."
    return None


# Keys only the cards and the app draw from: never worth a model's tokens.
UI_KEYS = {"shape", "segments", "credit", "app_url", "app_card", "card", "chart", "structuredContent", "view",
           "ask", "units_note", "_meta", "share_url",
           # Nov 28: the app link (it signs the person in) and the card's seen-beacon are never the model's.
           "app", "_card"}
# get_plan's answer as the model sees it (Nov 28): what the plan is for first, the weeks last, so a
# long answer cut short loses far-off sessions rather than the goal, the races and their targets.
PLAN_FIRST = ("current_plan", "races", "this_week", "phases", "starts", "ends", "sessions_total", "completed",
              "missed", "remaining", "next_session", "checks", "situation", "how_to_adapt", "source", "note")
PLAN_DROP = ("for_the_assistant", "about_checks", "review", "as_of")


def _strip(value: Any, depth: int = 0) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v, depth + 1) for k, v in value.items() if k not in UI_KEYS and v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_strip(v, depth + 1) for v in value]
    return value


def _compact(name: str, result: Any, limit: int, today: date) -> str:
    """A tool's answer as the model sees it: no card-only fields, a plan cut to the weeks
    around today, and never longer than `limit` characters. Never stored."""
    if name == "get_plan" and isinstance(result, dict) and isinstance(result.get("weeks"), list):
        monday = today - timedelta(days=today.weekday())
        keep_from, keep_to = (monday - timedelta(days=7)).isoformat(), (monday + timedelta(days=21)).isoformat()
        plan = {k: result[k] for k in PLAN_FIRST if k in result}
        # The card's "completion_percent" is done out of the sessions due so far, not of the whole plan.
        if result.get("completion_percent") is not None:
            plan["done_percent_of_sessions_due_so_far"] = result["completion_percent"]
        plan.update({k: v for k, v in result.items() if k not in plan and k not in PLAN_DROP
                     and k not in ("weeks", "completion_percent")})
        plan["weeks"] = [w for w in result["weeks"] if keep_from <= str(w.get("starts") or "") <= keep_to]
        plan["weeks_shown"] = "last week to three weeks ahead; ask with a label for others"
        result = plan
    text = json.dumps(_strip(result), separators=(",", ":"), default=str)
    if len(text) > limit:
        text = text[:limit] + '..."(cut short: ask for a narrower range if more is needed)"'
    return text


# -- plain-English summaries of proposed changes ---------------------------------------
# Worked out by rule from the stored call, not by the model, so the card says what will run.

def _today() -> date:
    return datetime.now(LONDON).date()


def _day_text(value: Any) -> str:
    raw = str(value or "").strip()
    today = _today()
    word = raw.lower()
    if word in ("today", "tomorrow", "yesterday"):
        d = today + timedelta(days={"today": 0, "tomorrow": 1, "yesterday": -1}[word])
    elif re.fullmatch(r"[+-]?\d{1,3}", raw):
        d = today + timedelta(days=int(raw))
    else:
        try:
            d = date.fromisoformat(raw[:10])
        except ValueError:
            return raw or "an unknown day"
    return f"{app_routes.DAY_NAMES[d.weekday()]} {d.day} {app_routes.MONTHS[d.month - 1]}"


def _mins(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m} min" if m < 90 else f"{m // 60} h {m % 60:02d} min"


def _step_text(step: Mapping[str, Any]) -> str:
    kind = str(step.get("type") or "step").lower()
    if kind == "repeat":
        inner = ", ".join(_step_text(s) for s in step.get("steps") or [])
        return f"{step.get('times', '?')} × ({inner})"
    if step.get("duration_seconds"):
        size = _mins(float(step["duration_seconds"]))
    elif step.get("distance_meters"):
        meters = float(step["distance_meters"])
        size = f"{meters / 1000:g} km" if meters >= 1000 else f"{meters:g} m"
    else:
        size = "to the lap button"
    target = step.get("pace") or step.get("hr")
    target = "–".join(map(str, target)) if isinstance(target, list) else target
    label = {"warmup": "warm-up", "cooldown": "cool-down", "interval": "run"}.get(kind, kind)
    return f"{label} {size}" + (f" at {target}" if target else "")


def _plan_session(plan: Mapping[str, Any] | None, day: str | None, schedule_id: Any = None) -> Mapping[str, Any] | None:
    for w in (plan or {}).get("weeks") or []:
        for s in w.get("sessions") or []:
            if schedule_id and str(s.get("schedule_id")) == str(schedule_id):
                return s
            if not schedule_id and day and str(s.get("date"))[:10] == str(day)[:10]:
                return s
    return None


def _named(s: Mapping[str, Any] | None, fallback: str = "the session") -> str:
    return f"“{app_routes._display(s.get('name'))}”" if s else fallback


async def _workout_name(workout_id: Any) -> str:
    """A workout's name for a card, read from Garmin; its id when that can't be read."""
    try:
        workout = await app_routes._workout(workout_id) if str(workout_id or "").strip().isdigit() else None
    except Exception:  # noqa: BLE001 - the card says the id instead
        workout = None
    return app_routes._display((workout or {}).get("workoutName")) if (workout or {}).get("workoutName") \
        else f"workout {workout_id}"


async def summarise(name: str, args: Mapping[str, Any], plan: Mapping[str, Any] | None,
                    preview: Mapping[str, Any] | None = None) -> str:
    """What a proposed change will do, in a sentence a runner can check before tapping Confirm."""
    if name == "create_workout":
        steps = "; ".join(_step_text(s) for s in args.get("steps") or [])
        where = f"put it on {_day_text(args['date'])} (it goes to your watch)" if args.get("date") else \
            "save it to your Garmin workouts (not scheduled yet)"
        return f"Create the workout “{args.get('name', 'Workout')}” and {where}." + (f" Steps: {steps}." if steps else "")
    if name == "create_strength_workout":
        moves = [str(e.get("name") or e.get("exercise") or "") for e in args.get("exercises") or []]
        listed = ", ".join(m for m in moves[:6] if m) + ("…" if len(moves) > 6 else "")
        return (f"Create the strength session “{args.get('name', 'Strength')}” with {len(moves)} exercises"
                f"{f' ({listed})' if listed else ''} and save it to your Garmin workouts (not scheduled yet).")
    if name == "update_workout":
        title = await _workout_name(args.get("workout_id"))
        changed = ", ".join(k for k in args if k != "workout_id") or "nothing"
        return f"Change “{title}” in your Garmin workouts ({changed}). Every day it's scheduled changes with it."
    if name == "schedule_workout":
        title = await _workout_name(args.get("workout_id"))
        return f"Put “{title}” on {_day_text(args.get('date'))}. It goes to your watch at its next sync."
    if name == "unschedule_workout":
        s = _plan_session(plan, args.get("date"), args.get("schedule_id"))
        day = args.get("date") or (s or {}).get("date")
        what = f"“{args['name']}”" if args.get("name") else _named(s)
        return f"Take {what} off {_day_text(day)}. The workout stays in your library."
    if name == "move_session":
        s = _plan_session(plan, args.get("date"), args.get("schedule_id"))
        return f"Move {_named(s)} from {_day_text(args.get('date'))} to {_day_text(args.get('to'))}. The watch follows."
    if name == "delete_workout":
        title = (preview or {}).get("name") or args.get("confirm")
        return f"Delete “{title}” from your Garmin workouts for good. Runs you've recorded aren't touched."
    if name == "remove_plan":
        n = (preview or {}).get("would_remove")
        return (f"Remove plan {args.get('label')}: {n if n is not None else 'its'} sessions from today on come off "
                "your calendar and workouts. Past sessions stay.")
    if name == "create_plan":
        sessions = args.get("sessions") or []
        dates = sorted(str(s.get("date"))[:10] for s in sessions if s.get("date"))
        span = f" from {_day_text(dates[0])} to {_day_text(dates[-1])}" if dates else ""
        return (f"Put a {len(sessions)}-session plan on your Garmin calendar{span}: {args.get('goal', 'training block')}. "
                "Every session goes to your watch.")
    if name == "count_run":
        target = _plan_session(plan, None, args.get("schedule_id")) if args.get("schedule_id") else None
        what = {"session": f"count as {_named(target)}", "extra": "count as an extra run",
                "undo": "go back to how Daash matched it"}.get(str(args.get("action")), "change how it counts")
        return f"Make your run {args.get('activity_id')} {what}."
    return f"Run {name}."


# -- providers -------------------------------------------------------------------
# Two small adapters over raw HTTP (httpx2, already in the image through the MCP SDK),
# so switching provider is an environment change and no SDK is added to the server.
# Each keeps one request's messages and is dropped when the request ends.

class ProviderError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


class Call:
    """What one model call came back with."""

    def __init__(self) -> None:
        self.text = ""
        self.tools: list[dict[str, Any]] = []
        self.raw: Any = None
        self.stop = ""
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0
        self.cache_write = 0


async def _sse_lines(response: Any) -> AsyncIterator[tuple[str, str]]:
    """(event, data) pairs from a server-sent event stream."""
    event, data = "", []
    async for line in response.aiter_lines():
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)


def _http_error(status: int, body: str) -> ProviderError:
    if status in (401, 403):
        return ProviderError("config", f"provider refused the key ({status})")
    if status == 400:
        return ProviderError("bad_request", body[:300])
    return ProviderError("provider", f"provider answered {status}: {body[:200]}")


class Anthropic:
    """Claude's Messages API, streamed."""

    def __init__(self, cfg: Mapping[str, Any], system: str, tools: list[dict[str, Any]], history: list[dict[str, str]]):
        self.cfg, self.system = cfg, system
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        self.messages: list[dict[str, Any]] = [{"role": m["role"], "content": m["text"]} for m in history]

    async def call(self, client: Any, on_text) -> Call:
        body: dict[str, Any] = {
            "model": self.cfg["model"], "max_tokens": self.cfg["max_output_tokens"], "stream": True,
            "system": self.system, "messages": self.messages, "tools": self.tools,
            # The tools and system prompt are the same on every call of a turn: cached after the first.
            "cache_control": {"type": "ephemeral"},
        }
        if self.cfg.get("effort"):
            body["output_config"] = {"effort": self.cfg["effort"]}
        headers = {"x-api-key": self.cfg["api_key"], "anthropic-version": "2026-10-21",
                   "content-type": "application/json"}
        out, blocks = Call(), {}
        async with client.stream("POST", f"{self.cfg['base_url']}/messages", json=body, headers=headers) as response:
            if response.status_code >= 400:
                raise _http_error(response.status_code, (await response.aread()).decode("utf-8", "replace"))
            async for event, data in _sse_lines(response):
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                kind = msg.get("type")
                if kind == "message_start":
                    usage = (msg.get("message") or {}).get("usage") or {}
                    out.input_tokens = int(usage.get("input_tokens") or 0)
                    out.cache_read = int(usage.get("cache_read_input_tokens") or 0)
                    out.cache_write = int(usage.get("cache_creation_input_tokens") or 0)
                elif kind == "content_block_start":
                    block = dict(msg.get("content_block") or {})
                    if block.get("type") == "tool_use":
                        block["_json"] = ""
                    blocks[msg.get("index")] = block
                elif kind == "content_block_delta":
                    block, delta = blocks.get(msg.get("index")), msg.get("delta") or {}
                    if block is None:
                        continue
                    if delta.get("type") == "text_delta":
                        block["text"] = block.get("text", "") + delta.get("text", "")
                        out.text += delta.get("text", "")
                        await on_text(delta.get("text", ""))
                    elif delta.get("type") == "input_json_delta":
                        block["_json"] += delta.get("partial_json", "")
                    elif delta.get("type") == "thinking_delta":
                        block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                    elif delta.get("type") == "signature_delta":
                        block["signature"] = block.get("signature", "") + delta.get("signature", "")
                elif kind == "message_delta":
                    out.stop = (msg.get("delta") or {}).get("stop_reason") or out.stop
                    out.output_tokens = int((msg.get("usage") or {}).get("output_tokens") or out.output_tokens)
                elif kind == "error":
                    detail = (msg.get("error") or {}).get("message", "")
                    raise ProviderError("provider", f"stream error: {detail}")
        content = []
        for _, block in sorted(blocks.items(), key=lambda kv: kv[0] or 0):
            if block.get("type") == "tool_use":
                try:
                    block["input"] = json.loads(block.pop("_json") or "{}")
                except ValueError:
                    block["input"] = {}
                    block["_invalid"] = True
                out.tools.append({"id": block["id"], "name": block["name"], "args": block["input"],
                                  "invalid": block.pop("_invalid", False)})
            content.append({k: v for k, v in block.items() if v is not None})
        # The assistant turn goes back as it came, thinking blocks and all, within this request.
        out.raw = content
        return out

    def add_results(self, call: Call, results: list[tuple[str, str, bool]]) -> None:
        self.messages.append({"role": "assistant", "content": call.raw})
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": text, **({"is_error": True} if err else {})}
            for tid, text, err in results]})


class OpenAICompatible:
    """OpenAI's chat completions, streamed; Gemini through Google's OpenAI-compatible endpoint."""

    def __init__(self, cfg: Mapping[str, Any], system: str, tools: list[dict[str, Any]], history: list[dict[str, str]]):
        self.cfg = cfg
        self.tools = [{"type": "function", "function": t} for t in tools]
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}] + \
            [{"role": m["role"], "content": m["text"]} for m in history]

    async def call(self, client: Any, on_text) -> Call:
        body: dict[str, Any] = {"model": self.cfg["model"], "messages": self.messages, "tools": self.tools,
                                "stream": True, "stream_options": {"include_usage": True}}
        body["max_tokens" if self.cfg["provider"] == "gemini" else "max_completion_tokens"] = self.cfg["max_output_tokens"]
        headers = {"authorization": f"Bearer {self.cfg['api_key']}", "content-type": "application/json"}
        out, calls = Call(), {}
        async with client.stream("POST", f"{self.cfg['base_url']}/chat/completions", json=body,
                                 headers=headers) as response:
            if response.status_code >= 400:
                raise _http_error(response.status_code, (await response.aread()).decode("utf-8", "replace"))
            async for _, data in _sse_lines(response):
                if data.strip() == "[DONE]":
                    break
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                if msg.get("error"):
                    raise ProviderError("provider", f"stream error: {msg['error']}")
                usage = msg.get("usage") or {}
                if usage:
                    out.input_tokens = int(usage.get("prompt_tokens") or 0)
                    out.output_tokens = int(usage.get("completion_tokens") or 0)
                for choice in msg.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        out.text += delta["content"]
                        await on_text(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["name"] = fn.get("name") or slot["name"]
                        slot["args"] += fn.get("arguments") or ""
                    if choice.get("finish_reason"):
                        out.stop = choice["finish_reason"]
        tool_calls = []
        for _, slot in sorted(calls.items()):
            slot["id"] = slot["id"] or f"call_{uuid.uuid4().hex[:12]}"
            try:
                args, invalid = json.loads(slot["args"] or "{}"), False
            except ValueError:
                args, invalid = {}, True
            out.tools.append({"id": slot["id"], "name": slot["name"], "args": args, "invalid": invalid})
            tool_calls.append({"id": slot["id"], "type": "function",
                               "function": {"name": slot["name"], "arguments": slot["args"] or "{}"}})
        out.raw = {"role": "assistant", "content": out.text or None, **({"tool_calls": tool_calls} if tool_calls else {})}
        return out

    def add_results(self, call: Call, results: list[tuple[str, str, bool]]) -> None:
        self.messages.append(call.raw)
        for tid, text, _ in results:
            self.messages.append({"role": "tool", "tool_call_id": tid, "content": text})


def make_provider(cfg: Mapping[str, Any], system: str, tools: list[dict[str, Any]], history: list[dict[str, str]]):
    """The adapter for the configured provider (tests put a scripted one here)."""
    cls = Anthropic if cfg["provider"] == "anthropic" else OpenAICompatible
    return cls(cfg, system, tools, history)


def estimate_cost(cfg: Mapping[str, Any], input_tokens: int, output_tokens: int, cache_read: int = 0,
                  cache_write: int = 0) -> float | None:
    """US dollars, from list prices: cached input read at a tenth, written at 1.25x."""
    pin, pout = cfg.get("price_in"), cfg.get("price_out")
    if pin is None or pout is None:
        return None
    plain = max(0, input_tokens)
    return round((plain * pin + cache_write * pin * 1.25 + cache_read * pin * 0.1 + output_tokens * pout) / 1e6, 6)


# -- the system prompt ------------------------------------------------------------

PROMPT = """You are the training companion inside the Daash app, talking with one runner about their Garmin training.
Today is {today} (Europe/London). Dates for tools are YYYY-MM-DD.

How to answer:
- Short: two to five sentences or a few short lines. Plain words, no exclamation marks, no emoji, no headings.
- Use the tools for any figure, date or plan detail. Never make up numbers. Prefer one well-chosen call over many.
- Tool descriptions mention cards, app links and attribution lines: there are none here, answer in words.
- Changes (creating, scheduling, moving, unscheduling, editing or deleting workouts; creating or removing a plan; how a run counts) never happen when you call those tools. The runner sees a card saying what will change and taps Confirm. So propose, don't claim: say what you've proposed and that it's waiting for their confirm. Never say it's done.
- One change per message where you can. create_workout with a date creates and schedules it in one go. To move a planned session use move_session. Something that needs an id from an earlier change waits until that change is confirmed.
- The plan's goal, block, races (with their targets) and Garmin's fitness markers are below. Use them before saying something isn't known. A session named like a race or a test (a "Half check", a "Tune-up half") is one of the plan's races or milestones: its target, if it has one, is listed with it. "Sessions so far" counts what's done against what was due, not the whole plan.
- No medical or injury advice. If they mention pain, injury or illness, suggest easing off and checking with a physio or doctor, and don't prescribe training through it.

What Daash can see right now (tools have the latest):
{context}"""


def _plan_lines(plan: Any, today: date) -> list[str]:
    if not isinstance(plan, Mapping) or plan.get("error"):
        return ["Plan: couldn't be read just now."]
    if plan.get("source") in ("log", "none", "waiting") or not plan.get("weeks"):
        return ["Plan: nothing planned on the Garmin calendar."]
    monday = today - timedelta(days=today.weekday())
    lines = _plan_head(plan, monday)
    for label, start in (("This week", monday), ("Next week", monday + timedelta(days=7))):
        week = next((w for w in plan["weeks"] if w.get("starts") == start.isoformat()), None)
        if not week:
            continue
        rows = []
        for s in week.get("sessions") or []:
            km = f" {s['planned_km']:g} km" if s.get("planned_km") else ""
            ids = ", ".join(f"{k}={s[k]}" for k in ("schedule_id", "workout_id") if s.get(k))
            rows.append(f"{s.get('day', '')} {str(s.get('date', ''))[:10]} {app_routes._display(s.get('name'))}{km} "
                        f"({s.get('type') or s.get('kind') or 'run'}, {s.get('status') or 'planned'}{', ' + ids if ids else ''})")
        head = f"{label} (from {start.isoformat()}"
        head += f", {week['planned_km']:g} km planned" if week.get("planned_km") else ""
        head += f", {week['run_km']:g} km run so far)" if week.get("run_km") is not None else ")"
        lines.append(head + ":\n  " + ("\n  ".join(rows) if rows else "nothing planned"))
    lines += _race_lines(plan, today) + _check_lines(plan, today)
    return lines or ["Plan: nothing planned this week or next."]


def _short_date(value: Any) -> str:
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)[:10]
    return f"{app_routes.DAY_NAMES[d.weekday()][:3]} {d.day} {datetime(2000, d.month, 1).strftime('%b')} {d.year}"


def _plan_head(plan: Mapping[str, Any], monday: date) -> list[str]:
    """What the plan is for and where the runner is in it (Nov 28): the goal, the method, the
    week and its phase, and the sessions done so far. Without these the model guessed."""
    out = []
    cur = plan.get("current_plan") if isinstance(plan.get("current_plan"), Mapping) else {}
    week_no = next((w.get("week") for w in plan.get("weeks") or [] if w.get("starts") == monday.isoformat()), None)
    phase = next((p.get("label") for p in plan.get("phases") or [] if isinstance(p, Mapping) and week_no
                  and (p.get("from_week") or 0) <= week_no <= (p.get("to_week") or 0)), None)
    if cur:
        head = f"Plan: {app_routes._display(cur.get('name')) or 'the current plan'}"
        if cur.get("starts") and cur.get("ends"):
            head += f", {_short_date(cur['starts'])} to {_short_date(cur['ends'])}"
        if cur.get("week") and cur.get("weeks_total"):
            head += f", week {cur['week']} of {cur['weeks_total']}"
        head += f", {phase} phase" if phase else ""
        out.append(head + ".")
        if cur.get("goal"):
            out.append(f"Goal: {cur['goal']}.")
        if cur.get("method"):
            out.append(f"Method: {cur['method']}.")
    else:
        bits = ["Plan: the sessions on the Garmin calendar"]
        if plan.get("starts") and plan.get("ends"):
            bits.append(f"{_short_date(plan['starts'])} to {_short_date(plan['ends'])}")
        if phase:
            bits.append(f"{phase} phase")
        out.append(", ".join(bits) + ".")
    if plan.get("phases"):
        out.append("Phases (calendar weeks): " + ", ".join(
            f"{p.get('label')} {p.get('from_week')} to {p.get('to_week')}" for p in plan["phases"] if isinstance(p, Mapping))
            + (f"; this is calendar week {week_no}." if week_no else "."))
    if plan.get("completed") is not None and plan.get("missed") is not None:
        out.append(f"Sessions so far: {plan['completed']} done, {plan['missed']} missed"
                   + (f", {plan['remaining']} still to come" if plan.get("remaining") is not None else "")
                   + (f" ({plan['sessions_total']} in all)." if plan.get("sessions_total") else "."))
    tw = plan.get("this_week") if isinstance(plan.get("this_week"), Mapping) else {}
    if tw.get("planned") is not None and tw.get("done") is not None:
        unit = tw.get("unit") or "km"
        out.append(f"This week so far: {tw['done']:g} of {tw['planned']:g} {unit} done"
                   + (f", {tw['to_go']:g} {unit} to go." if tw.get("to_go") is not None else "."))
    return out


def _race_lines(plan: Mapping[str, Any], today: date, limit: int = 6) -> list[str]:
    """Races and milestones from today on, with their targets, the goal race always included."""
    races = [r for r in plan.get("races") or [] if isinstance(r, Mapping) and str(r.get("date", ""))[:10] >= today.isoformat()]
    if not races:
        return []
    shown = races[:limit]
    goal = next((r for r in races if r.get("tier") == "goal"), None)
    if goal and goal not in shown:
        shown.append(goal)
    rows = []
    for r in shown:
        bits = [x for x in (r.get("tier"), f"{r['distance_km']:g} km" if r.get("distance_km") else None,
                            f"target {r['target']}" if r.get("target") else "no target set") if x]
        rows.append(f"{_short_date(r.get('date'))} {r.get('name')} ({', '.join(bits)})")
    more = len(races) - len(shown)
    return ["Races and milestones ahead:\n  " + "\n  ".join(rows) + (f"\n  and {more} more" if more > 0 else "")]


def _check_lines(plan: Mapping[str, Any], today: date, limit: int = 3) -> list[str]:
    """The plan's own checks still ahead (back-to-back races, no lighter week), as worth a look."""
    checks = [c for c in plan.get("checks") or [] if isinstance(c, Mapping) and c.get("text")
              and str(c.get("date", ""))[:10] >= today.isoformat()][:limit]
    if not checks:
        return []
    return ["Plan checks worth a look (mention gently, change nothing unless asked):\n  "
            + "\n  ".join(str(c["text"]) for c in checks)]


def _fitness_lines(found: Any) -> list[str]:
    """Garmin's fitness markers (Nov 28): race predictions, threshold, VO2 max, zones."""
    if not isinstance(found, Mapping) or found.get("error") or found.get("for_the_assistant") and len(found) == 1:
        return ["Fitness: not available just now (get_fitness has it)."]
    bits = []
    preds = found.get("race_predictions") or {}
    names = (("5k", "5k"), ("10k", "10k"), ("half_marathon", "half"), ("marathon", "marathon"))
    said = [f"{label} {preds[k]['time']}" for k, label in names if isinstance(preds.get(k), Mapping) and preds[k].get("time")]
    if said:
        when = found.get("race_predictions_as_of")
        bits.append("race predictions " + ", ".join(said) + (f" (as of {_short_date(when)})" if when else ""))
    lt = found.get("lactate_threshold") or {}
    if lt.get("heart_rate_bpm") or lt.get("pace_per_km"):
        bits.append("lactate threshold " + " at ".join(str(x).replace(" ", "") if i else f"{x:g} bpm"
                                                       for i, x in enumerate((lt.get("heart_rate_bpm"), lt.get("pace_per_km"))) if x))
    vo2 = found.get("vo2_max")
    if isinstance(vo2, Mapping) and vo2.get("value"):
        bits.append(f"VO2 max {vo2['value']:g}")
    elif isinstance(vo2, (int, float)):
        bits.append(f"VO2 max {vo2:g}")
    for key, label in (("endurance_score", "endurance score"), ("hill_score", "hill score")):
        v = found.get(key)
        v = v.get("value") if isinstance(v, Mapping) else v
        if isinstance(v, (int, float)):
            bits.append(f"{label} {v:g}")
    hr = ((found.get("zones") or {}).get("heart_rate") or {})
    if hr.get("zones"):
        bits.append("heart rate zones " + ", ".join(f"Z{z.get('zone')} {z['bpm'][0]}-{z['bpm'][1]}"
                                                    for z in hr["zones"] if isinstance(z.get("bpm"), list) and len(z["bpm"]) == 2)
                    + (f" (max {hr['max_hr']}, resting {hr['resting_hr']})" if hr.get("max_hr") and hr.get("resting_hr") else ""))
    return ["Fitness (Garmin): " + ("; ".join(bits) if bits else "no markers yet") + "."]


def _activity_lines(found: Any) -> list[str]:
    if not isinstance(found, Mapping) or found.get("error"):
        return ["Recent activities: couldn't be read just now."]
    rows = []
    for a in (found.get("activities") or [])[:6]:
        bits = [str(a.get("start_local", ""))[:10], str(a.get("name") or a.get("type") or "activity")]
        if a.get("distance_km"):
            bits.append(f"{a['distance_km']:g} km")
        if a.get("duration"):
            bits.append(str(a["duration"]))
        if a.get("pace_per_km"):
            bits.append(str(a["pace_per_km"]).replace(" ", ""))
        hr = (a.get("heart_rate") or {}).get("average_bpm")
        if hr:
            bits.append(f"{hr:g} bpm")
        rows.append(", ".join(bits) + f" (activity_id={a.get('activity_id')})")
    return ["Recent activities:\n  " + ("\n  ".join(rows) if rows else "none recorded lately")]


def _readiness_line(found: Any) -> str:
    if not isinstance(found, Mapping) or found.get("error"):
        return "Readiness today: not available."
    r = found.get("readiness") or {}
    bits = []
    if r.get("score") is not None:
        bits.append(f"score {r['score']:g}" + (f" ({r['level']})" if r.get("level") else ""))
    if r.get("recovery_time_hours") is not None:
        bits.append(f"recovery time {r['recovery_time_hours']:g} h")
    hrv = found.get("hrv") or {}
    if hrv.get("status"):
        bits.append(f"HRV {str(hrv['status']).lower()}" + (f" ({hrv['last_night_ms']:g} ms)" if hrv.get("last_night_ms") else ""))
    sleep = (r.get("factors") or {}).get("sleep")
    if sleep:
        bits.append(f"sleep {str(sleep).lower()}")
    status = (found.get("training_status") or {}).get("status")
    if status:
        bits.append(f"training status {str(status).lower()}")
    return "Readiness today: " + (", ".join(bits) if bits else "not available") + "."


async def _quietly(coro: Any, seconds: float) -> Any:
    try:
        return await asyncio.wait_for(coro, seconds)
    except Exception as exc:  # noqa: BLE001 - the prompt says what couldn't be read
        log.info("chat context read failed: %s", type(exc).__name__)
        return {"error": type(exc).__name__}


async def build_system(cfg: Mapping[str, Any]) -> tuple[str, Any]:
    """The compact system prompt, and the plan it read (kept for this request's summaries)."""
    today = _today()
    seconds = min(10, cfg["tool_timeout"])
    plan, activities, readiness, fitness = await asyncio.gather(
        _quietly(server_mod.get_plan(), seconds), _quietly(server_mod.get_activities(limit=6), seconds),
        _quietly(server_mod.get_readiness(), seconds), _quietly(server_mod.get_fitness(), seconds))
    context = "\n".join(_plan_lines(plan, today) + _activity_lines(activities) + [_readiness_line(readiness)]
                        + _fitness_lines(fitness))
    when = f"{app_routes.DAY_NAMES[today.weekday()]} {today.day} {datetime(2000, today.month, 1).strftime('%B')} {today.year}"
    return PROMPT.format(today=when, context=context), plan


# -- the conversation as the model sees it --------------------------------------------

def _history(rows: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
    """The last `limit` messages as plain user and assistant text; a proposed change as a short note."""
    out: list[dict[str, str]] = []
    for row in rows[-limit:]:
        text = (row.get("content") or "").strip()
        if row["role"] == "user" and text:
            out.append({"role": "user", "text": text})
        elif row["role"] == "assistant" and text:
            out.append({"role": "assistant", "text": text})
        elif row["role"] == "action":
            said = {"pending": "waiting for the runner to confirm", "running": "being made now",
                    "done": "confirmed and done", "failed": "confirmed but Garmin refused it",
                    "declined": "declined by the runner, nothing changed",
                    "expired": "expired, nothing changed"}.get(row["status"], row["status"])
            out.append({"role": "assistant", "text": f"[Proposed change: {text} Status: {said}.]"})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _sse(event: str, data: Mapping[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


ERRORS = {
    "timeout": "That took too long. Try again?",
    "provider": "The AI service isn't answering right now. Try again in a minute.",
    "config": "Chat isn't set up on the server yet.",
    "bad_request": "The AI service couldn't take that message. Try rewording it.",
    "internal": "Something went wrong on our side. Try again.",
}


def _view(row: Mapping[str, Any]) -> dict[str, Any]:
    """A message as the Chat screen draws it."""
    out: dict[str, Any] = {"id": row["id"], "role": row["role"], "text": row.get("content") or "",
                           "status": row["status"]}
    if row["role"] == "assistant" and row.get("feedback") is not None:
        out["feedback"] = "up" if row["feedback"] > 0 else "down"
    if row["role"] == "action":
        out["tool"] = row.get("tool")
        if row["status"] == "pending" and row.get("created_at", 0) < time.time() - ACTION_TTL:
            out["status"] = "expired"
    return out


# -- one turn ----------------------------------------------------------------------

class Turn:
    """One message and its streamed answer. Lives for one request."""

    def __init__(self, user_token: str, person: str, text: str, starter: bool) -> None:
        self.user_token, self.person, self.text, self.starter = user_token, person, text, starter
        self.cfg = env()
        self.started = time.monotonic()
        self.deadline = self.started + self.cfg["turn_timeout"]
        self.answer = ""
        self.input_tokens = self.output_tokens = self.cache_read = self.cache_write = 0
        self.tool_calls = 0
        self.first_tool: str | None = None
        self.tools_used: list[str] = []
        self.usage: dict[str, Any] = {}
        self.seen: set[str] = set()
        self.assistant_id: int | None = None
        self.closed = False
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def left(self) -> float:
        return self.deadline - time.monotonic()

    async def emit(self, event: str, data: Mapping[str, Any]) -> None:
        await self.queue.put(_sse(event, data))

    async def on_text(self, chunk: str) -> None:
        if chunk:
            self.answer += chunk
            await self.emit("delta", {"text": chunk})

    def close(self, status: str) -> None:
        """Save the answer as it stands: done, partial (cut off) or failed. Once."""
        if self.closed or self.assistant_id is None:
            return
        self.closed = True
        latency = int((time.monotonic() - self.started) * 1000)
        total_in = self.input_tokens + self.cache_read + self.cache_write
        cost = estimate_cost(self.cfg, self.input_tokens, self.output_tokens, self.cache_read, self.cache_write)
        store.chat_finish(self.assistant_id, status, self.answer, model=self.cfg["model"], input_tokens=total_in,
                          output_tokens=self.output_tokens, est_cost_usd=cost, latency_ms=latency)
        # Sent with the last event, for the owner's own replay script (scripts/chat_replay.py).
        self.usage = {"model": self.cfg["model"], "input_tokens": total_in, "cached_input_tokens": self.cache_read,
                      "output_tokens": self.output_tokens, "est_cost_usd": cost, "latency_ms": latency,
                      "tools": list(self.tools_used)}
        log.info("chat turn %s: model=%s in=%d (cached %d) out=%d est_cost=$%s latency=%dms tools=%d",
                 status, self.cfg["model"], total_in, self.cache_read, self.output_tokens,
                 f"{cost:.6f}" if cost is not None else "?", latency, self.tool_calls)
        _track("chat_message_sent", {"starter_used": self.starter, "matched_tool": self.first_tool or "none"})
        _track("chat_response", {"latency_ms": latency, "input_tokens": total_in, "output_tokens": self.output_tokens,
                                 "est_cost_usd": cost, "model": self.cfg["model"], "provider": self.cfg["provider"],
                                 "status": status, "tool_calls": self.tool_calls})

    async def run(self) -> None:
        try:
            await self._run()
        except ProviderError as exc:
            log.warning("chat provider error (%s): %s", exc.code, exc)
            self.close("partial" if self.answer else "failed")
            await self.emit("error", {"code": exc.code, "message": ERRORS.get(exc.code, ERRORS["provider"]),
                                      "usage": self.usage})
        except (asyncio.TimeoutError, httpx.TimeoutException):
            self.close("partial" if self.answer else "failed")
            await self.emit("error", {"code": "timeout", "message": ERRORS["timeout"], "usage": self.usage})
        except httpx.HTTPError as exc:
            log.warning("chat provider unreachable: %s", type(exc).__name__)
            self.close("partial" if self.answer else "failed")
            await self.emit("error", {"code": "provider", "message": ERRORS["provider"], "usage": self.usage})
        except asyncio.CancelledError:
            self.close("partial" if self.answer else "failed")
            raise
        except Exception:  # noqa: BLE001 - one turn's failure is reported, never a hang
            log.exception("chat turn failed")
            self.close("partial" if self.answer else "failed")
            await self.emit("error", {"code": "internal", "message": ERRORS["internal"], "usage": self.usage})
        finally:
            self.queue.put_nowait(None)

    async def _run(self) -> None:
        cfg = self.cfg
        if not cfg["api_key"]:
            raise ProviderError("config", "no API key set for the chat provider")
        thread = store.chat_thread(self.person)
        earlier = store.chat_messages(thread, limit=max(1, cfg["context_messages"]))
        user_id = store.chat_add(thread, self.person, "user", "sent", self.text, starter=self.starter)
        self.assistant_id = store.chat_add(thread, self.person, "assistant", "streaming", "", model=cfg["model"])
        await self.emit("start", {"user_id": user_id, "id": self.assistant_id})
        system, plan = await asyncio.wait_for(build_system(cfg), max(1.0, self.left()))
        history = _history(earlier, max(0, cfg["context_messages"] - 1)) + [{"role": "user", "text": self.text}]
        provider = make_provider(cfg, system, tool_specs(self.user_token), history)
        timeout = httpx.Timeout(cfg["provider_timeout"], connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Each pass is one model call; it ends when the model answers without asking for a tool.
            for _ in range(cfg["max_tool_calls"] + 2):
                if self.left() <= 0:
                    raise asyncio.TimeoutError()
                call = await asyncio.wait_for(provider.call(client, self.on_text), self.left())
                self.input_tokens += call.input_tokens
                self.output_tokens += call.output_tokens
                self.cache_read += call.cache_read
                self.cache_write += call.cache_write
                if call.stop == "refusal" and not call.text:
                    await self.on_text("I can't help with that one.")
                if not call.tools:
                    break
                if self.input_tokens + self.cache_read + self.cache_write + self.output_tokens > cfg["max_request_tokens"]:
                    await self.on_text("\n\n(I stopped there: this answer reached its size limit.)")
                    self.close("partial")
                    await self.emit("done", {"id": self.assistant_id, "status": "partial", "usage": self.usage})
                    return
                results = [await self._tool(thread, t, plan) for t in call.tools]
                provider.add_results(call, results)
                if self.answer and not self.answer.endswith("\n"):
                    await self.on_text("\n\n")
            else:
                await self.on_text("\n\n(I stopped there: too many steps for one message. Ask me to carry on.)")
        self.answer = self.answer.strip()
        cut = call.stop in ("max_tokens", "length")
        self.close("partial" if cut else "done")
        await self.emit("done", {"id": self.assistant_id, "status": "partial" if cut else "done", "usage": self.usage})

    async def _tool(self, thread: int, t: Mapping[str, Any], plan: Any) -> tuple[str, str, bool]:
        """Run one read, or store one proposed write. Returns what the model is told."""
        name, args = str(t.get("name") or ""), t.get("args") if isinstance(t.get("args"), dict) else {}
        self.tool_calls += 1
        self.first_tool = self.first_tool or name
        self.tools_used.append(name)
        if t.get("invalid"):
            return t["id"], json.dumps({"error": "Those arguments weren't valid JSON. Try again."}), True
        # The loop guard (hosted._within_loop_guard, per turn here): a cap on calls, and the same call twice is refused.
        if self.tool_calls > self.cfg["max_tool_calls"]:
            _track("chat_tool_called", {"tool": name, "ok": False, "blocked": "limit"})
            return t["id"], json.dumps({"error": f"That's the limit of {self.cfg['max_tool_calls']} tool calls for one "
                                                 "message. Answer with what you have."}), True
        key = name + json.dumps(args, sort_keys=True, default=str)
        if key in self.seen:
            _track("chat_tool_called", {"tool": name, "ok": False, "blocked": "repeat"})
            return t["id"], json.dumps({"error": "You already made this exact call in this message; use that answer."}), True
        self.seen.add(key)
        if name not in READS and name not in WRITES or (name == "count_run" and not _match_on(self.user_token)):
            _track("chat_tool_called", {"tool": name, "ok": False})
            return t["id"], json.dumps({"error": f"No tool called {name}."}), True
        problem = _check_args(name, args)
        if problem:
            _track("chat_tool_called", {"tool": name, "ok": False})
            return t["id"], json.dumps({"error": problem}), True
        if name in WRITES:
            return await self._propose(thread, t["id"], name, args, plan)
        await self.emit("tool", {"name": name, "label": TOOL_LABELS.get(name, "Checking Garmin")})
        try:
            result = await asyncio.wait_for(_fn(name)(**args), max(1.0, min(self.cfg["tool_timeout"], self.left())))
        except asyncio.TimeoutError:
            result = {"error": "Garmin took too long to answer that. Try a narrower question."}
        ok = not (isinstance(result, dict) and result.get("error"))
        _track("chat_tool_called", {"tool": name, "ok": ok})
        return t["id"], _compact(name, result, self.cfg["tool_result_chars"], _today()), not ok

    async def _propose(self, thread: int, tid: str, name: str, args: dict[str, Any], plan: Any) -> tuple[str, str, bool]:
        """Store a write as a proposed change and show its card. Nothing reaches Garmin here."""
        preview = None
        if name in PREVIEWED:
            # Asking without confirm only reads back what would go (server.delete_workout, remove_plan).
            args = {k: v for k, v in args.items() if k != "confirm"}
            preview = await asyncio.wait_for(_fn(name)(**args), max(1.0, min(self.cfg["tool_timeout"], self.left())))
            if not isinstance(preview, dict) or preview.get("error") or not preview.get("confirmation_required"):
                _track("chat_tool_called", {"tool": name, "ok": False})
                said = preview if isinstance(preview, dict) else {"error": "No answer from Garmin."}
                return tid, _compact(name, said, self.cfg["tool_result_chars"], _today()), True
            # The stored call is the one that will run: with the confirm Garmin's preview asked for.
            if name == "delete_workout":
                args["confirm"] = preview.get("name") or ""
            else:
                args["confirm"] = preview.get("label") or args.get("label")
        summary = await summarise(name, args, plan, preview)
        action_id = store.chat_add(thread, self.person, "action", "pending", summary, tool=name, args=args)
        _track("chat_tool_called", {"tool": name, "ok": True, "proposed": True})
        await self.emit("pending", {"id": action_id, "tool": name, "text": summary, "status": "pending"})
        return tid, json.dumps({"status": "proposed", "summary": summary,
                                "note": "Nothing has changed yet. The runner sees this on a card and taps Confirm or "
                                        "Decline. Tell them briefly what you proposed."}), False


async def _stream(turn: Turn) -> AsyncIterator[bytes]:
    """The answer as it's written. If the phone goes, the turn stops and what was said is saved."""
    task = asyncio.create_task(turn.run())
    try:
        while True:
            chunk = await turn.queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001 - cancelled on purpose; the turn saved itself
                pass


# -- running a confirmed change ------------------------------------------------------

async def execute(name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """Run one stored change exactly as it was proposed. Raises nothing: an error is an answer."""
    try:
        if name == "move_session":
            return await app_routes.move(str(args["date"]), str(args["to"]), args.get("schedule_id"))
        if name == "count_run":
            from . import official

            return await app_routes.match_apply(official.current() or "", str(args["action"]), str(args["activity_id"]),
                                                args.get("schedule_id"))
        fn = _fn(name)
        if name not in WRITES or fn is None:
            return {"error": f"Unknown change {name}."}
        return await fn(**args)
    except app_routes.EditError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - reported on the card, never a crash
        log.exception("chat change %s failed", name)
        return {"error": f"Garmin didn't take that ({type(exc).__name__})."}


def _result_line(name: str, result: Mapping[str, Any]) -> str:
    """What the thread says once a change has run, ids included so the model can use them next."""
    if result.get("error"):
        return f"Garmin didn't take it: {result['error']}"
    if result.get("message"):
        return str(result["message"])
    said = []
    scheduled = result.get("scheduled")
    day = (scheduled.get("scheduled_for") if isinstance(scheduled, dict) else None) or result.get("scheduled_for") \
        or (result.get("date") if name == "schedule_workout" else None)
    if day:
        said.append(f"It's on your calendar for {_day_text(day)} and goes to your watch at its next sync.")
    elif isinstance(scheduled, (list, int)) and not isinstance(scheduled, bool):
        said.append(f"{len(scheduled) if isinstance(scheduled, list) else scheduled} sessions are on your calendar.")
    if result.get("removed") is not None:
        said.append(f"{result['removed']} sessions came off.")
    if result.get("deleted"):
        said.append("Deleted.")
    ids = ", ".join(f"{k.replace('_', ' ')} {result[k]}" for k in ("workout_id", "schedule_id", "label")
                    if isinstance(result.get(k), (int, str)) and result.get(k) != "")
    return " ".join(["Done."] + said) + (f" ({ids})" if ids else "")


# -- routes ----------------------------------------------------------------------
# Registered before app.py's own files, whose catch-all would otherwise answer first.

def _gate() -> tuple[str, str] | None:
    token = _who()
    if not token or not enabled_for(token):
        return None
    return token, _person(token)


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@mcp.custom_route(app_routes.API_PREFIX + "features", methods=["GET"])
async def api_features(request: Request) -> Response:
    """What this person's app shows beyond the usual: empty for everyone but a pilot's people."""
    return app_routes._json({"chat": True} if enabled_for(_who()) else {})


@mcp.custom_route(PREFIX, methods=["GET"])
async def api_chat(request: Request) -> Response:
    """The active thread, for the Chat screen."""
    gate = _gate()
    if gate is None:
        return _not_found()
    _, person = gate
    thread = store.chat_thread(person)
    rows = store.chat_messages(thread, limit=100)
    _track("chat_opened", {"messages": len(rows)})
    return app_routes._json({"messages": [_view(r) for r in rows if r["role"] != "assistant" or r.get("content")
                                          or r["status"] != "streaming"],
                             "starters": list(STARTERS)})


@mcp.custom_route(PREFIX + "/send", methods=["POST"])
async def api_chat_send(request: Request) -> Response:
    """Send a message; the answer streams back as server-sent events."""
    gate = _gate()
    if gate is None:
        return _not_found()
    token, person = gate
    body = await _body(request)
    text = str(body.get("text") or "").strip()[:2000]
    if not text:
        return app_routes._json({"error": "Type a message first.", "code": "empty"}, 400)
    cfg = env()
    start_of_day = int(datetime.now(LONDON).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if store.chat_sent_since(person, start_of_day) >= cfg["daily_cap"]:
        _track("chat_cap_reached", {"cap": cfg["daily_cap"]})
        return app_routes._json({"error": f"That's today's limit of {cfg['daily_cap']} messages. "
                                          "It resets at midnight.", "code": "cap"}, 429)
    starter = body.get("starter") is True and text in STARTERS
    turn = Turn(token, person, text, starter)
    return StreamingResponse(_stream(turn), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no",
                                      "X-Daash-Build": app_routes.APP_BUILD})


@mcp.custom_route(PREFIX + "/action", methods=["POST"])
async def api_chat_action(request: Request) -> Response:
    """Confirm or Decline one proposed change. Confirm runs that exact stored call, once."""
    gate = _gate()
    if gate is None:
        return _not_found()
    _, person = gate
    body = await _body(request)
    decision = str(body.get("decision") or "")
    try:
        action_id = int(body.get("id"))
    except (TypeError, ValueError):
        action_id = 0
    row = store.chat_message(action_id, person) if action_id else None
    if row is None or row["role"] != "action" or decision not in ("confirm", "decline"):
        return app_routes._json({"error": "That change isn't there."}, 404)
    fresh_since = int(time.time()) - ACTION_TTL
    if decision == "decline":
        if not store.chat_claim_action(action_id, person, "declined"):
            return _settled(store.chat_message(action_id, person))
        _track("chat_write_declined", {"tool": row["tool"]})
        return app_routes._json({"id": action_id, "status": "declined", "message": "Cancelled. Nothing changed."})
    if not store.chat_claim_action(action_id, person, "running", not_before=fresh_since):
        current = store.chat_message(action_id, person)
        if current and current["status"] == "pending":
            store.chat_claim_action(action_id, person, "expired")
            return app_routes._json({"error": "That proposal is more than a day old. Ask again.", "status": "expired"}, 409)
        return _settled(current)
    try:
        result = await asyncio.wait_for(execute(row["tool"], row["args"] or {}), env()["write_timeout"])
    except asyncio.TimeoutError:
        # It may still have reached Garmin: said so, never retried on its own.
        result = {"error": "Garmin took too long to answer. Check your calendar before asking again."}
    ok = not (isinstance(result, dict) and result.get("error"))
    line = _result_line(row["tool"], result if isinstance(result, dict) else {"error": "No answer from Garmin."})
    store.chat_finish(action_id, "done" if ok else "failed")
    # What happened goes in the thread too, so the model knows next time (and an id it can use).
    store.chat_add(store.chat_thread(person), person, "assistant", "done", line)
    _track("chat_write_confirmed", {"tool": row["tool"], "ok": ok})
    if ok and row["tool"] in ("create_workout", "schedule_workout", "move_session"):
        app_routes._watch_sent(f"chat_{row['tool']}", 1)
    if ok and row["tool"] == "create_plan":
        app_routes._watch_sent("chat_create_plan", len((row["args"] or {}).get("sessions") or []))
    answer = {"id": action_id, "status": "done" if ok else "failed", "message": line, "refresh": ok}
    return app_routes._json(answer if ok else {**answer, "error": line}, 200 if ok else 502)


def _settled(row: Mapping[str, Any] | None) -> Response:
    said = {"done": "That change has already been made.", "running": "That change is being made now.",
            "failed": "That change was already tried.", "declined": "That change was cancelled.",
            "expired": "That proposal has expired. Ask again."}
    status = (row or {}).get("status", "")
    return app_routes._json({"error": said.get(status, "That change isn't there."), "status": status}, 409)


@mcp.custom_route(PREFIX + "/feedback", methods=["POST"])
async def api_chat_feedback(request: Request) -> Response:
    gate = _gate()
    if gate is None:
        return _not_found()
    _, person = gate
    body = await _body(request)
    value = {"up": 1, "down": -1}.get(str(body.get("value") or ""))
    try:
        message_id = int(body.get("id"))
    except (TypeError, ValueError):
        message_id = 0
    if value is None or not store.chat_feedback(message_id, person, value):
        return app_routes._json({"error": "That message isn't there."}, 404)
    _track("chat_feedback", {"value": "up" if value > 0 else "down"})
    return app_routes._json({"ok": True})


@mcp.custom_route(PREFIX + "/new", methods=["POST"])
async def api_chat_new(request: Request) -> Response:
    """Start a fresh thread; the old one is kept, and anything it still proposes expires."""
    gate = _gate()
    if gate is None:
        return _not_found()
    _, person = gate
    store.chat_new_thread(person)
    _track("chat_new_thread")
    return app_routes._json({"ok": True})

