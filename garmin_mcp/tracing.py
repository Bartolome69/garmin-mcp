"""Tool calls as traces in PostHog's AI observability (Nov 16).

Daash makes no LLM calls: the model runs in the person's own chat app, which
sends tools here. So there are no generations, prompts, tokens or costs to
trace, and none are collected. What this adds is the server's side of an
answer: one trace per tool call, with a span for every request it made to
Garmin, so a slow or failing answer shows where its time went.

The MCP transport is stateless, so nothing here knows which chat a call came
from. Calls from one person in one app with less than half an hour between
them share a session ($ai_session_id), which is the nearest thing to a sitting.

Nothing personal goes on a span: the tool's name and how it went, and for
each Garmin request its method, the first few segments of its path (numbers
and the account's display name replaced) and the status Garmin answered.
Never a tool's inputs or answer, a query string or an email. Events go out
through analytics.capture, so the same scrubbing and the same distinct id
apply, and nothing is sent unless PostHog is configured.

PostHog counts every $ai_ event against AI observability's free 100,000 a
month, and Daash makes about 90,000 tool calls a month. So not every call is
traced: every call that failed, took SLOW seconds or more, or came from one
of Daash's own accounts, and SHARE of the rest, with no more than DAILY_CAP
events a day from one machine. GARMIN_MCP_AI_TRACES=0 turns it off.
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from . import analytics
from .bounded import BoundedDict

log = logging.getLogger(__name__)


def _number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


SHARE = _number("GARMIN_MCP_AI_TRACES", 0.1)
DAILY_CAP = int(_number("GARMIN_MCP_AI_TRACES_DAILY", 3000))
SLOW = 3.0
SITTING = 30 * 60
# A plan read can make a dozen requests; past this a trace keeps a count only.
MAX_REQUESTS = 25


@dataclass
class Call:
    tool: str
    started: float = field(default_factory=time.time)
    requests: list[dict[str, Any]] = field(default_factory=list)
    dropped: int = 0


_call: ContextVar["Call | None"] = ContextVar("garmin_traced_call", default=None)
_lock = threading.Lock()
_today: dict[str, Any] = {"day": None, "sent": 0}
_sittings: dict[tuple[str, str], tuple[str, float]] = BoundedDict(20000)


def start(tool: str) -> Any:
    """Begin collecting a tool call's Garmin requests; a token for finish()."""
    return _call.set(Call(tool))


def finish(token: Any) -> None:
    _call.reset(token)


def carried(fn: Callable[..., Any]) -> Callable[..., Any]:
    """fn, run on another thread as part of the call in progress here.

    A plain thread pool starts each task with an empty context, so the
    requests it makes would belong to no call.
    """
    call = _call.get()

    def run(*args: Any, **kwargs: Any) -> Any:
        token = _call.set(call)
        try:
            return fn(*args, **kwargs)
        finally:
            _call.reset(token)

    return run


_SEGMENT = re.compile(r"[A-Za-z][A-Za-z-]{0,39}")


def path_name(url: str, private: tuple[str, ...] = ()) -> str:
    """A request's path, safe to keep: three segments at most, anything with a
    digit, anything unusual and the account's own name replaced, no query."""
    kept = []
    for segment in [s for s in urlsplit(url).path.split("/") if s][:3]:
        segment = unquote(segment)
        if segment in private:
            kept.append("{user}")
        elif _SEGMENT.fullmatch(segment):
            kept.append(segment)
        else:
            kept.append("{id}")
    return "/" + "/".join(kept)


def note_request(method: str, url: str, status: int | None, seconds: float | None,
                 private: tuple[str, ...] = (), failed: bool | None = None) -> None:
    """One request to Garmin, made while a tool call is being collected.

    `failed` where the caller knows; otherwise a status of 400 or more.
    """
    call = _call.get()
    if call is None:
        return
    if len(call.requests) >= MAX_REQUESTS:
        call.dropped += 1
        return
    call.requests.append({
        "name": f"{method.upper()} {path_name(url, private)}",
        "status": status,
        "failed": failed if failed is not None else (status is not None and status >= 400),
        "seconds": round(seconds, 3) if seconds is not None else None,
        "at": time.time(),
    })


def watch(garmin: Any) -> None:
    """Note every request a Garmin Connect client makes, through requests' own
    response hook on garminconnect's API session. Harmless if that changes."""
    session = getattr(getattr(garmin, "client", None), "_api_session", None)
    hooks = getattr(session, "hooks", None)
    if not isinstance(hooks, dict) or any(getattr(h, "_daash", False) for h in hooks.get("response", [])):
        return

    def heard(response: Any, *args: Any, **kwargs: Any) -> None:
        try:
            private = tuple(n for n in (getattr(garmin, "display_name", None), getattr(garmin, "full_name", None))
                            if isinstance(n, str) and n)
            elapsed = getattr(response, "elapsed", None)
            note_request(response.request.method, response.request.url, response.status_code,
                         elapsed.total_seconds() if elapsed is not None else None, private)
        except Exception:  # noqa: BLE001 - a trace must never fail a read
            log.debug("request not traced", exc_info=True)

    heard._daash = True  # type: ignore[attr-defined]
    hooks.setdefault("response", []).append(heard)


def _chosen(ok: bool, seconds: float | None, internal: bool) -> str | None:
    """Why this call is traced, or None when it isn't."""
    if SHARE <= 0 or not analytics.enabled():
        return None
    if internal:
        return "internal"
    if not ok:
        return "error"
    if seconds is not None and seconds >= SLOW:
        return "slow"
    return "sample" if random.random() < SHARE else None


def _room(events: int, now: float) -> bool:
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    with _lock:
        if _today["day"] != day:
            _today.update(day=day, sent=0)
        if _today["sent"] + events > DAILY_CAP:
            return False
        _today["sent"] += events
        return True


def _sitting(subject: str, app: str, now: float) -> str:
    with _lock:
        held = _sittings.get((subject, app))
        sitting = held[0] if held and now - held[1] < SITTING else uuid.uuid4().hex
        _sittings[(subject, app)] = (sitting, now)
        if len(_sittings) > 5000:
            for key, (_, at) in list(_sittings.items()):
                if now - at >= SITTING:
                    _sittings.pop(key, None)
        return sitting


def report(subject: str | None, *, ok: bool, error_kind: str | None, seconds: float | None,
           app: str | None, properties: dict[str, Any] | None = None) -> str | None:
    """Send the call in progress as a trace, if it's chosen; why it was, or None."""
    call = _call.get()
    if call is None:
        return None
    try:
        internal = analytics.distinct_id(subject) in analytics.internal_ids()
        why = _chosen(ok, seconds, internal)
        now = time.time()
        if why is None or not _room(1 + len(call.requests), now):
            return None
        trace = uuid.uuid4().hex
        sitting = _sitting(subject or "", app or "", now)
        common = {"$ai_trace_id": trace, "$ai_session_id": sitting, "tool": call.tool}
        events = [("$ai_trace", {
            **common,
            "$ai_span_name": call.tool,
            "$ai_latency": round(seconds, 3) if seconds is not None else None,
            "$ai_is_error": not ok,
            **({"$ai_error": error_kind} if error_kind else {}),
            **(properties or {}),
            "garmin_requests": len(call.requests) + call.dropped,
            "traced_because": why,
        }, now)]
        for request in call.requests:
            status = request["status"]
            events.append(("$ai_span", {
                **common,
                "$ai_span_id": uuid.uuid4().hex,
                "$ai_parent_id": trace,
                "$ai_span_name": request["name"],
                "$ai_latency": request["seconds"],
                "$ai_is_error": request["failed"],
                **({"http_status": status} if status is not None else {}),
            }, request["at"]))
        analytics.capture_many(subject, events)
        return why
    except Exception:  # noqa: BLE001 - a trace must never fail an answer
        log.debug("trace not sent", exc_info=True)
        return None
