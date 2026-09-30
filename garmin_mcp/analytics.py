"""The few product events the hosted server reports, with nothing personal in them.

The website already tells PostHog when someone copies the connector address.
That is the last thing a web page can see. Whether they then finished signing
in, and whether the connector was ever used, happens here, so the funnel had a
hole exactly where it mattered.

Three events close it: a sign-in succeeded, a sign-in failed (and roughly
why), and a connection was used for the first time. Each carries a handful of
flags and no more: no email, no token, no Garmin data. The distinct id is an
HMAC of the user's token under the server secret, so the same person counts
once without PostHog being able to name them, and person profiles are turned
off so nothing accumulates against that id.

Off unless GARMIN_MCP_POSTHOG_KEY is set, so a self-hosted copy reports to
nobody by default. Sending happens on a background thread and swallows every
error: analytics must never slow down or fail a sign-in.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

KEY = os.environ.get("GARMIN_MCP_POSTHOG_KEY", "").strip()
HOST = (
    os.environ.get("GARMIN_MCP_POSTHOG_HOST", "").strip().rstrip("/")
    or "https://eu.i.posthog.com"
)
_TIMEOUT = 5


def enabled() -> bool:
    return bool(KEY)


def distinct_id(user_token: str | None) -> str:
    """One stable, unreadable id per person; a fixed one for nobody in particular.

    Keyed on the server secret so the id cannot be recomputed from a leaked
    token, and prefixed so it can never collide with the email fingerprint the
    store keeps for the same person.
    """
    if not user_token:
        return "anonymous"
    key = os.environ.get("GARMIN_MCP_SECRET", "").encode()
    return hmac.new(key, b"posthog:" + user_token.encode(), hashlib.sha256).hexdigest()[:32]


def capture(event: str, user_token: str | None = None, properties: dict[str, Any] | None = None) -> None:
    """Record one event. Returns at once; the request goes out on its own thread."""
    if not KEY:
        return
    payload = {
        "api_key": KEY,
        "event": event,
        "distinct_id": distinct_id(user_token),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "properties": {
            **(properties or {}),
            # No person is built up from these; the id only lets uniq() work.
            "$process_person_profile": False,
            "$lib": "garmin-mcp-hosted",
        },
    }
    threading.Thread(target=_post, args=(payload,), daemon=True).start()


def _post(payload: dict[str, Any]) -> None:
    try:
        request = urllib.request.Request(
            f"{HOST}/i/v0/e/",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            response.read()
    except Exception:  # noqa: BLE001 - analytics never gets to break anything
        log.debug("analytics event not delivered", exc_info=True)
