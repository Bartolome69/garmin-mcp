"""Watching the server's memory, minute by minute (Nov 25).

The leak of 15-25 Nov (every person's Garmin session kept until a restart)
grew memory 40-60 MB every 15 minutes until the machine ran out and froze.
It's fixed; this is so the next one can't freeze the server, and says where
the memory goes:

- an email at MEMORY_WARN_MB (1,200), then every 2 hours while it stays high;
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
        self._snapshot: tracemalloc.Snapshot | None = None
        self._snapshot_at = 0.0
        self._we_started = False

    def should_warn(self, rss_mb: float, now: float | None = None) -> bool:
        """True for the first reading at or above the line, then every 2 hours while it stays there."""
        now = self.clock() if now is None else now
        if rss_mb < self.warn_mb:
            self.warned_at = None  # back down: the next time up is a new episode
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
