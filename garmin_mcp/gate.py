"""Every Garmin call goes through here (Nov 25).

From 15 to 25 Nov the server froze about 20 times. Nothing capped how many
Garmin calls could be waiting at once, how long one could take, or how hard
Daash kept asking after Garmin said slow down. This is that cap:

- one fixed pool of GARMIN_POOL_WORKERS threads (16) runs every call;
- a call gets GARMIN_CALL_TIMEOUT_S (30) seconds, then the person is told
  Garmin is slow instead of waiting on a hang;
- one person has at most GARMIN_PER_USER (2) requests in flight. A request
  is one tool call: the reads a tool makes side by side (get_readiness makes
  five) share its slot, so the cap stops a person piling up work without
  making each answer slower. A call outside any tool counts as its own;
- Garmin's rate limit (429) is retried with backoff and jitter, three
  attempts in all, then that person rests for five minutes;
- past GARMIN_POOL_MAX_QUEUE (50) calls waiting, a new one is turned away at
  once rather than queued behind them.

A call that times out keeps running on its thread until garminconnect's own
request timeout ends it (Python can't stop a thread); the person's slot is
held until then, so a hang can't pile up behind them.
"""

from __future__ import annotations

import contextvars
import logging
import os
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from . import telemetry

log = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, default)))
    except ValueError:
        return default


WORKERS = _env_int("GARMIN_POOL_WORKERS", 16)
CALL_TIMEOUT_S = _env_float("GARMIN_CALL_TIMEOUT_S", 30.0)
PER_USER = _env_int("GARMIN_PER_USER", 2)
MAX_QUEUE = _env_int("GARMIN_POOL_MAX_QUEUE", 50)
RATE_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 8.0
COOLDOWN_S = _env_float("GARMIN_RATE_COOLDOWN_S", 300.0)

SLOW = "Garmin is slow right now, try again in a minute."
BUSY = "Daash is very busy right now, try again in a minute."
YOURS_BUSY = "Daash is still waiting on Garmin for your last request, try again in a minute."


def _rate_message(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return (f"Garmin is limiting how often Daash can ask for this account (rate limit). "
            f"Try again in {minutes} minute{'s' if minutes != 1 else ''}.")


# The tool call in progress, set by server.tool_errors, so the reads one tool
# makes side by side count as one of the person's requests.
REQUEST: contextvars.ContextVar[str | None] = contextvars.ContextVar("garmin_request", default=None)


class GateError(RuntimeError):
    """A call turned away or given up on here; the message is for the person."""


class GarminSlow(GateError):
    pass


class GarminBusy(GateError):
    pass


class GarminRateLimited(GateError):
    pass


def is_rate_limited(exc: BaseException) -> bool:
    """Garmin's "slow down", from either route."""
    if type(exc).__name__ == "GarminConnectTooManyRequestsError":
        return True
    if getattr(exc, "status", None) == 429 or getattr(exc, "code", None) == 429:
        return True
    text = str(exc)
    return "429" in text and ("rate" in text.lower() or "too many" in text.lower())


class Gate:
    def __init__(self, workers: int = WORKERS, timeout: float = CALL_TIMEOUT_S, per_user: int = PER_USER,
                 max_queue: int = MAX_QUEUE, cooldown: float = COOLDOWN_S,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.workers, self.timeout, self.per_user = workers, timeout, per_user
        self.max_queue, self.cooldown_s = max_queue, cooldown
        self._sleep, self._clock = sleep, clock
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="garmin")
        self._cond = threading.Condition()
        # owner -> {request -> calls in flight}
        self._in_flight: dict[str, dict[str, int]] = {}
        self._own = 0
        self._cooldown: dict[str, float] = {}
        self._waiting = 0
        self._busy = 0
        self._inside = threading.local()

    # -- what the heartbeat reads ------------------------------------------
    def queue_depth(self) -> int:
        with self._cond:
            return self._waiting

    def busy_workers(self) -> int:
        with self._cond:
            return self._busy

    def cooling(self, owner: str) -> float:
        """Seconds left of this person's rest after a rate limit, or 0."""
        with self._cond:
            until = self._cooldown.get(owner)
            left = (until - self._clock()) if until else 0.0
            if until and left <= 0:
                self._cooldown.pop(owner, None)
            return max(0.0, left)

    # -- the call ----------------------------------------------------------
    def call(self, owner: str | None, fn: Callable[[], Any], *, timeout: float | None = None) -> Any:
        """fn() on the pool, within the limits above. Raises GateError subclasses."""
        if getattr(self._inside, "on", False):
            # Already on one of the pool's threads (a call made by a call):
            # running it here keeps the pool from waiting on itself.
            return fn()
        owner = owner or "local"
        left = self.cooling(owner)
        if left > 0:
            telemetry.count("garmin_rate_limited_rejects")
            raise GarminRateLimited(_rate_message(left))
        limit = self.timeout if timeout is None else timeout
        request = REQUEST.get() or self._one_off()
        self._take_slot(owner, request, limit)
        released = [False]

        def release() -> None:
            if not released[0]:
                released[0] = True
                self._give_slot(owner, request)

        try:
            for attempt in range(RATE_ATTEMPTS):
                future = self._submit(fn)
                try:
                    result = future.result(timeout=limit)
                except FutureTimeout:
                    telemetry.count("garmin_timeouts")
                    if future.cancel():
                        release()
                    else:
                        # Still running: the slot is freed when it ends, not now.
                        released[0] = True
                        future.add_done_callback(lambda _f: self._give_slot(owner, request))
                    log.warning("Garmin call took over %.0f s; told the person Garmin is slow", limit)
                    raise GarminSlow(SLOW) from None
                except BaseException as exc:
                    if not is_rate_limited(exc):
                        raise
                    telemetry.count("garmin_429s")
                    if attempt + 1 >= RATE_ATTEMPTS:
                        with self._cond:
                            self._cooldown[owner] = self._clock() + self.cooldown_s
                        log.warning("Garmin rate-limited one account %d times; resting it %.0f s",
                                    RATE_ATTEMPTS, self.cooldown_s)
                        raise GarminRateLimited(_rate_message(self.cooldown_s)) from None
                    base = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** attempt))
                    self._sleep(base * (0.5 + random.random() * 0.5))
                    continue
                return result
            raise GarminRateLimited(_rate_message(self.cooldown_s))  # pragma: no cover - loop always returns or raises
        finally:
            release()

    def _one_off(self) -> str:
        with self._cond:
            self._own += 1
            return f"call-{self._own}"

    def requests_in_flight(self, owner: str) -> int:
        with self._cond:
            return len(self._in_flight.get(owner, {}))

    def _take_slot(self, owner: str, request: str, limit: float) -> None:
        deadline = self._clock() + limit
        with self._cond:
            mine = self._in_flight.setdefault(owner, {})
            while request not in mine and len(mine) >= self.per_user:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    if not mine:
                        self._in_flight.pop(owner, None)
                    telemetry.count("garmin_user_capped")
                    raise GarminBusy(YOURS_BUSY)
                self._cond.wait(timeout=remaining)
                mine = self._in_flight.setdefault(owner, {})
            mine[request] = mine.get(request, 0) + 1

    def _give_slot(self, owner: str, request: str) -> None:
        with self._cond:
            mine = self._in_flight.get(owner, {})
            n = mine.get(request, 0) - 1
            if n > 0:
                mine[request] = n
            else:
                mine.pop(request, None)
            if not mine:
                self._in_flight.pop(owner, None)
            self._cond.notify_all()

    def _submit(self, fn: Callable[[], Any]) -> Future:
        with self._cond:
            if self._waiting >= self.max_queue:
                telemetry.count("garmin_queue_full")
                raise GarminBusy(BUSY)
            self._waiting += 1
        telemetry.count("garmin_calls")
        context = contextvars.copy_context()
        started = [False]

        def run() -> Any:
            with self._cond:
                self._waiting -= 1
                self._busy += 1
            started[0] = True
            self._inside.on = True
            try:
                return context.run(fn)
            finally:
                self._inside.on = False
                with self._cond:
                    self._busy -= 1

        future = self._pool.submit(run)

        def cancelled(f: Future) -> None:
            if f.cancelled() and not started[0]:
                with self._cond:
                    self._waiting -= 1

        future.add_done_callback(cancelled)
        return future


gate = Gate()
