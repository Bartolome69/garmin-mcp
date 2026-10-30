"""What happens after each tool call, set per request by whoever is hosting.

The tools don't know who is asking; the hosted server does. It sets a hook for
the request's duration, the same way it binds the Garmin session, and every
tool's result passes through it on the way out: to note that the feature was
used, and to carry anything owed to that person, such as a one-time note on
what's new. Locally no hook is set and results pass straight through.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Callable

log = logging.getLogger(__name__)

Hook = Callable[[str, Any], Any]
# Told a tool's name as it starts; answers how many seconds, at least, its
# answer should take, so the hook can see what else was sent with it.
Before = Callable[[str], float]

_hook: ContextVar["Hook | None"] = ContextVar("garmin_tool_hook", default=None)
_before: ContextVar["Before | None"] = ContextVar("garmin_tool_before", default=None)
# How long the tool being reported took, for the hook to read.
_seconds: ContextVar["float | None"] = ContextVar("garmin_tool_seconds", default=None)


def use(hook: Hook | None, before: Before | None = None):
    """Set the hooks for the current context; returns a token for resetting."""
    return _hook.set(hook), _before.set(before)


def reset(token) -> None:
    hook_token, before_token = token
    _before.reset(before_token)
    _hook.reset(hook_token)


def before_tool(name: str) -> float:
    """Tell the hook a tool is starting; the seconds its answer should take, at least."""
    before = _before.get()
    if before is None:
        return 0.0
    try:
        return float(before(name) or 0.0)
    except Exception:  # noqa: BLE001 - a reporting hiccup must not hold up an answer
        log.debug("tool start hook failed for %s", name, exc_info=True)
        return 0.0


def seconds() -> float | None:
    """How long the tool now passing through the hook took, where it was timed."""
    return _seconds.get()


def after_tool(name: str, result: Any, seconds: float | None = None) -> Any:
    """Pass a tool's result through the hook, if one is set. Never fails the tool."""
    hook = _hook.get()
    if hook is None:
        return result
    _seconds.set(seconds)
    try:
        return hook(name, result)
    except Exception:  # noqa: BLE001 - a reporting hiccup must not lose an answer
        log.debug("tool hook failed for %s", name, exc_info=True)
        return result


# Told whenever a chat app fetches a card's view, with which view and the
# version it asked for (None for the current address). Set once by the hosted
# server, to see how long after an answer the app asks for its card; it must
# return at once, since the card waits on this read.
on_view: Callable[[str, "str | None"], None] | None = None


def view_read(name: str, version: str | None = None) -> None:
    if on_view is None:
        return
    try:
        on_view(name, version)
    except Exception:  # noqa: BLE001 - counting a read must never stop the card
        log.debug("view read hook failed", exc_info=True)
