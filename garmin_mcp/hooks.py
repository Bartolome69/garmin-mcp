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

_hook: ContextVar["Hook | None"] = ContextVar("garmin_tool_hook", default=None)


def use(hook: Hook | None):
    """Set the hook for the current context; returns a token for resetting."""
    return _hook.set(hook)


def reset(token) -> None:
    _hook.reset(token)


def after_tool(name: str, result: Any) -> Any:
    """Pass a tool's result through the hook, if one is set. Never fails the tool."""
    hook = _hook.get()
    if hook is None:
        return result
    try:
        return hook(name, result)
    except Exception:  # noqa: BLE001 - a reporting hiccup must not lose an answer
        log.debug("tool hook failed for %s", name, exc_info=True)
        return result
