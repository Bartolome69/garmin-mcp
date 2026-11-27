"""Watching the server's memory, minute by minute (Nov 25).

The leak of 15-25 Nov (every person's Garmin session kept until a restart)
grew memory 40-60 MB every 15 minutes until the machine ran out and froze.
It's fixed; this is so the next one can't freeze the server, and says where
the memory goes:

- an email once two heartbeats in a row are at MEMORY_WARN_MB (1,200), then
  every 2 hours while it stays high;
- an email when the slowest 5% of the last hour's tool calls took over 15 s
  (LatencyWatch), then every 2 hours while it stays slow;
- above PROFILE_FROM_MB (600), tracemalloc on, and every 30 minutes the 20
  places that grew most since the last look, logged and sent to PostHog as
  memory_profile (file and line numbers only, nothing about anyone);
- at GUARD_MB (1,400) a graceful restart, before the machine runs out
  (server_started then says memory_guard).
"""

from __future__ import annotations

import os
import time
import tracemalloc
from typing import Callable

WARN_MB = int(os.environ.get("GARMIN_MCP_MEMORY_WARN_MB", "1200"))
WARN_EVERY_S = 2 * 3600
GUARD_MB = int(os.environ.get("GARMIN_MCP_MEMORY_GUARD_MB", "1400"))
PROFILE_FROM_MB = int(os.environ.get("GARMIN_MCP_PROFILE_FROM_MB", "600"))
PROFILE_EVERY_S = 30 * 60
PROFILE_TOP = 20
PROFILE_FRAMES = 15


def _short(path: str) -> str:
    for marker in ("site-packages/", "garmin_mcp/", "lib/python"):
        if marker in path:
            return path.split(marker, 1)[1] if marker != "garmin_mcp/" else "garmin_mcp/" + path.split(marker, 1)[1]
    return path


class Watch:
    def __init__(self, warn_mb: int = WARN_MB, warn_every: float = WARN_EVERY_S, guard_mb: int = GUARD_MB,
                 profile_mb: int = PROFILE_FROM_MB, profile_every: float = PROFILE_EVERY_S,
                 clock: Callable[[], float] = time.time) -> None:
        self.warn_mb, self.warn_every, self.guard_mb = warn_mb, warn_every, guard_mb
        self.profile_mb, self.profile_every = profile_mb, profile_every
        self.clock = clock
        self.warned_at: float | None = None
        self.high_in_a_row = 0
        self._snapshot: tracemalloc.Snapshot | None = None
        self._snapshot_at = 0.0
        self._we_started = False

    def should_warn(self, rss_mb: float, now: float | None = None) -> bool:
        """True once two readings in a row (two minutes) are at or above the line,
        then every 2 hours while it stays there."""
        now = self.clock() if now is None else now
        if rss_mb < self.warn_mb:
            self.warned_at = None  # back down: the next time up is a new episode
            self.high_in_a_row = 0
            return False
        self.high_in_a_row += 1
        if self.high_in_a_row < 2:
            return False
        if self.warned_at is None or now - self.warned_at >= self.warn_every:
            self.warned_at = now
            return True
        return False

    def should_restart(self, rss_mb: float) -> bool:
        return rss_mb >= self.guard_mb

    def profile(self, rss_mb: float, now: float | None = None) -> list[str] | None:
        """Above the line, the 20 places memory grew most in the last 30 minutes (None between looks)."""
        now = self.clock() if now is None else now
        if rss_mb < self.profile_mb * 0.8:
            if self._we_started and tracemalloc.is_tracing():
                tracemalloc.stop()
            self._we_started, self._snapshot = False, None
            return None
        if rss_mb < self.profile_mb and self._snapshot is None:
            return None
        if not tracemalloc.is_tracing():
            tracemalloc.start(PROFILE_FRAMES)
            self._we_started = True
        if self._snapshot is None:
            self._snapshot, self._snapshot_at = self._take(), now
            return None
        if now - self._snapshot_at < self.profile_every:
            return None
        latest = self._take()
        lines = []
        for stat in latest.compare_to(self._snapshot, "lineno")[:PROFILE_TOP]:
            frame = stat.traceback[0]
            lines.append(f"{_short(frame.filename)}:{frame.lineno} {stat.size_diff / 1024:+.0f} KB "
                         f"(now {stat.size / 1024:.0f} KB, {stat.count} blocks)")
        self._snapshot, self._snapshot_at = latest, now
        return lines

    @staticmethod
    def _take() -> tracemalloc.Snapshot:
        return tracemalloc.take_snapshot().filter_traces(
            (tracemalloc.Filter(False, tracemalloc.__file__), tracemalloc.Filter(False, "<frozen importlib._bootstrap>")))


LATENCY_P95_MS = float(os.environ.get("GARMIN_MCP_LATENCY_ALERT_MS", "15000"))
LATENCY_WINDOW_S = 3600
LATENCY_MIN_CALLS = 30
# Slow for Daash, not for one chat (Nov 27): the alert also needs this many
# people with a call over the line in the hour. On Nov 27 one ChatGPT chat
# read 248 runs in 27 minutes; its own calls queued behind the two Garmin
# requests a person may have in flight, the hour's p95 went to 16 s, and
# the alert fired while everyone else's calls took 2-3 s.
LATENCY_MIN_PEOPLE = int(os.environ.get("GARMIN_MCP_LATENCY_ALERT_PEOPLE", "3"))


class LatencyWatch:
    """The last hour's tool call times; an alert when their p95 passes 15 s
    and at least three people had a call that slow."""

    def __init__(self, limit_ms: float = LATENCY_P95_MS, window: float = LATENCY_WINDOW_S,
                 min_calls: int = LATENCY_MIN_CALLS, every: float = WARN_EVERY_S,
                 min_people: int = LATENCY_MIN_PEOPLE) -> None:
        from collections import deque
        import threading
        self.limit_ms, self.window, self.min_calls, self.every = limit_ms, window, min_calls, every
        self.min_people = min_people
        self._calls: "deque[tuple[float, float, str | None]]" = deque(maxlen=50_000)
        self._lock = threading.Lock()
        self.warned_at: float | None = None
        # The last check's slow people and the busiest one's share of the calls, for the email.
        self.slow_people = 0
        self.busiest_share = 0.0

    def note(self, ms: float, now: float | None = None, who: str | None = None) -> None:
        with self._lock:
            self._calls.append((time.time() if now is None else now, ms, who))

    def p95(self, now: float | None = None) -> tuple[float | None, int]:
        now = time.time() if now is None else now
        with self._lock:
            while self._calls and now - self._calls[0][0] > self.window:
                self._calls.popleft()
            times = sorted(ms for _, ms, _ in self._calls)
        if not times:
            return None, 0
        return times[min(len(times) - 1, int(0.95 * len(times)))], len(times)

    def _people(self) -> tuple[int, float]:
        """How many people had a call over the line, and the busiest person's share of all calls.
        Calls with no person count as one."""
        from collections import Counter
        with self._lock:
            calls = list(self._calls)
        slow = {who for _, ms, who in calls if ms > self.limit_ms}
        busiest = Counter(who for _, _, who in calls).most_common(1)
        return len(slow), (busiest[0][1] / len(calls) if busiest else 0.0)

    def should_warn(self, now: float | None = None) -> tuple[bool, float | None, int]:
        now = time.time() if now is None else now
        p95, n = self.p95(now)
        self.slow_people, self.busiest_share = self._people() if p95 is not None else (0, 0.0)
        if p95 is None or n < self.min_calls or p95 <= self.limit_ms or self.slow_people < self.min_people:
            self.warned_at = None
            return False, p95, n
        if self.warned_at is None or now - self.warned_at >= self.every:
            self.warned_at = now
            return True, p95, n
        return False, p95, n
