"""Whether a person gets the Nov 26 tool fixes: flag `tools-v2`.

Behind it: the loop guard (hosted.py), remove_plan and unschedule_workout
treating "already gone" as done, unschedule_workout answering a double day
with its sessions instead of an error, create_plan's sessions written four at
a time, and the trimmed get_activity_details. The range parameters and the
new tool descriptions are not behind it: every chat reads one tool list.

GARMIN_MCP_TOOLS_V2: "flag" (default) asks PostHog, "all" turns it on for
everyone (tests, the local server), "off" for no one.
"""

from __future__ import annotations

import os
from contextvars import ContextVar

from . import analytics

FLAG = "tools-v2"

# Whether the call being answered is under the flag, set once per call by the
# tool wrapper (server.tool_errors), so the hooks and the tools read it free.
ON: ContextVar[bool] = ContextVar("daash_tools_v2", default=False)


def mode() -> str:
    return (os.environ.get("GARMIN_MCP_TOOLS_V2") or "flag").strip().lower()


def on_for(subject: str | None) -> bool:
    """Whether the flag is on for this person (their email fingerprint or token).

    May ask PostHog (remembered an hour), so call it off the event loop.
    """
    chosen = mode()
    if chosen == "all":
        return True
    if chosen == "off" or not subject:
        return False
    return analytics.distinct_id(subject) in analytics.internal_ids() or analytics.flag_on(FLAG, subject)


def on() -> bool:
    """The flag for the call now being answered."""
    return mode() == "all" or ON.get()
