"""A slow, sometimes rate-limited stand-in for Garmin, for load tests only (Nov 25).

GARMIN_MCP_FAKE_GARMIN=load makes every Garmin session use this instead of
Garmin: each read waits a random 1-30 seconds (GARMIN_FAKE_LATENCY="1,30")
and 2% answer as Garmin's rate limit does (GARMIN_FAKE_429=0.02). Answers
come from tests/fake_garmin.py when the checkout has it (realistic shapes),
otherwise a small generic answer. scripts/loadtest.py drives a server running
this.

It refuses to switch on in the production app, whatever the environment says.
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Any

log = logging.getLogger(__name__)

PRODUCTION_APP = "garmin-mcp-1b4387"


def _latency() -> tuple[float, float]:
    try:
        lo, hi = (float(x) for x in os.environ.get("GARMIN_FAKE_LATENCY", "1,30").split(","))
        return lo, max(lo, hi)
    except ValueError:
        return 1.0, 30.0


class _Http:
    def dumps(self) -> str:
        return "fake-token"


class _Generic:
    """Any get_* answers something small; enough to keep a tool going."""

    def __init__(self) -> None:
        self.display_name = "load-test"
        self.full_name = "Load Test"
        self.unit_system = "metric"

    def __getattr__(self, name: str) -> Any:
        if name.startswith("get_"):
            return lambda *a, **k: [] if "activities" in name else {}
        raise AttributeError(name)


def build(**_kwargs: Any) -> Any:
    try:
        from tests.fake_garmin import FakeGarmin  # the checkout's realistic answers
        inner: Any = FakeGarmin()
    except Exception:  # noqa: BLE001 - not in the image: generic answers
        inner = _Generic()
    return SlowGarmin(inner)


class SlowGarmin:
    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "client", getattr(inner, "client", None) or _Http())

    def login(self, tokenstore: Any = None) -> tuple[None, None]:
        return None, None

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr) or not name.startswith("get_"):
            return attr
        lo, hi = _latency()
        share_429 = float(os.environ.get("GARMIN_FAKE_429", "0.02"))

        def slow(*a: Any, **k: Any) -> Any:
            time.sleep(random.uniform(lo, hi))
            if random.random() < share_429:
                from garminconnect import GarminConnectTooManyRequestsError
                raise GarminConnectTooManyRequestsError("Rate limit exceeded: API Error 429 (load test)")
            return attr(*a, **k)

        return slow


def install_if_asked() -> bool:
    """Swap Garmin for the stand-in when GARMIN_MCP_FAKE_GARMIN=load. Never in production."""
    if os.environ.get("GARMIN_MCP_FAKE_GARMIN") != "load":
        return False
    if os.environ.get("FLY_APP_NAME") == PRODUCTION_APP:
        log.error("GARMIN_MCP_FAKE_GARMIN=load ignored: this is the production app")
        return False
    from . import session
    session.build_client = build
    log.warning("LOAD TEST: Garmin is a stand-in (latency %s s, %s%% 429s)",
                os.environ.get("GARMIN_FAKE_LATENCY", "1,30"),
                round(100 * float(os.environ.get("GARMIN_FAKE_429", "0.02")), 1))
    return True
