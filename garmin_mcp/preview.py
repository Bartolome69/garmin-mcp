"""Features being tried by a few accounts before everyone gets them.

A preview feature is hidden unless it is switched on for whoever is asking.
Locally that is GARMIN_MCP_PREVIEW=1. The hosted server decides per request,
from its own list of accounts, and sets the answer here for the request's
duration, the same way it binds each request to its Garmin session.
"""

from __future__ import annotations

import os
from contextvars import ContextVar

ON = {"1", "on", "true", "yes", "all", "*"}

_enabled: ContextVar["bool | None"] = ContextVar("garmin_preview", default=None)


def switched_on_for_everyone() -> bool:
    return os.environ.get("GARMIN_MCP_PREVIEW", "").strip().lower() in ON


def enabled() -> bool:
    """Whether preview features are on for the current request."""
    value = _enabled.get()
    return switched_on_for_everyone() if value is None else value


def use(value: bool):
    """Set the answer for the current context; returns a token for resetting."""
    return _enabled.set(bool(value))


def reset(token) -> None:
    _enabled.reset(token)
