"""Counts and timings for the server's heartbeat (Nov 25).

Anything can count something (`count("cache_hits")`) or time something
(`observe("tool_ms", 812)`); the heartbeat takes the last minute's figures
with `take()`, which starts the next minute from zero. Names and numbers only:
nothing about anyone. Cheap and thread-safe, so it can sit on every call.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_counts: dict[str, int] = {}
_samples: dict[str, list[float]] = {}
# A minute at peak is a few dozen tool calls; this only stops a runaway.
_MAX_SAMPLES = 5000


def count(name: str, n: int = 1) -> None:
    with _lock:
        _counts[name] = _counts.get(name, 0) + n


def observe(name: str, value: float) -> None:
    with _lock:
        values = _samples.setdefault(name, [])
        if len(values) < _MAX_SAMPLES:
            values.append(float(value))


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def take() -> dict[str, Any]:
    """The counts since the last take, and each timing's p95; then start again."""
    with _lock:
        counts, samples = dict(_counts), {k: list(v) for k, v in _samples.items()}
        _counts.clear()
        _samples.clear()
    out: dict[str, Any] = dict(counts)
    for name, values in samples.items():
        out[f"p95_{name}"] = _p95(values)
    return out


def peek(name: str) -> int:
    with _lock:
        return _counts.get(name, 0)
