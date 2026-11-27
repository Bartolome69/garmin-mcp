"""Readiness: Push, Hold or Back off (build prompt, sections 5.2 and 6).

Three signals, each against the person's own history:
    sleep   last night against the median of the previous 28 nights:
            low when it's at least an hour short
    hrv     last night against the previous 60 nights' range (10th to 90th
            percentile): low when it's below the range
    load    7-day against 28-day load: in range from 0.8 to 1.3, low above 1.3

2 or 3 low: Back off. 1 low: Hold. None low: Push. A signal without enough
history (HRV via Intervals.icu, a new watch) is missing: the decision uses the
others and says which are missing. The number shown is Garmin's own readiness,
labelled as Garmin's; Daash's decision never changes it.

Nights are keyed by the local date they end on (the morning you wake), so
travel across time zones and DST change nothing: a night is one date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Mapping

from .config import DEFAULT, DomainConfig

DECISION_WORDS = {"push": "Push", "hold": "Hold", "back_off": "Back off"}
SIGNAL_NAMES = {"sleep": "Sleep", "hrv": "HRV", "load": "Load ratio"}
SAME_EITHER_WAY = "Readiness is worked out the same way with or without the Coach. Not medical advice."


@dataclass(frozen=True)
class Signal:
    key: str                    # sleep | hrv | load
    status: str                 # low | usual | missing
    value: float | None         # minutes, ms, or the ratio
    baseline: tuple[float, ...] # (median,) for sleep; (low, high) for HRV and load
    series: tuple[tuple[str, float | None], ...]   # the last 14 nights (or days), oldest first
    words: str


@dataclass(frozen=True)
class Readiness:
    decision: str | None        # push | hold | back_off; None when no signal could be read
    word: str | None
    low: int
    known: int
    signals: tuple[Signal, ...]
    missing: tuple[str, ...]
    summary: str                # "2 of 3 below usual" (+ "· HRV missing")
    garmin_label: str | None    # "Garmin readiness 26"


def percentile(values: list[float], pct: float) -> float:
    """Linear interpolation between the closest ranks, as spreadsheets do."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100
    lo, hi = int(rank), min(int(rank) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def _before(series: Mapping[date, float], today: date, n: int) -> list[float]:
    return [series[d] for d in (today - timedelta(days=i) for i in range(1, n + 1)) if series.get(d) is not None]


def _chart(series: Mapping[date, float], today: date, n: int) -> tuple[tuple[str, float | None], ...]:
    return tuple(((today - timedelta(days=i)).isoformat(), series.get(today - timedelta(days=i)))
                 for i in range(n - 1, -1, -1))


def _hm(minutes: float) -> str:
    m = int(round(abs(minutes)))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def sleep_signal(sleep: Mapping[date, float], today: date, cfg: DomainConfig) -> Signal:
    c = cfg.readiness
    history = _before(sleep, today, c.sleep_median_nights)
    last = sleep.get(today)
    chart = _chart(sleep, today, c.chart_nights)
    if last is None or len(history) < c.sleep_min_nights:
        return Signal("sleep", "missing", last, (), chart, "No sleep data for last night" if last is None
                      else "Not enough nights yet to know your usual")
    usual = median(history)
    short = usual - last
    low = short >= c.sleep_short_hours * 60
    words = (f"{_hm(last)}, {_hm(short)} under your usual {_hm(usual)}" if short > 0
             else f"{_hm(last)}, at or above your usual {_hm(usual)}")
    return Signal("sleep", "low" if low else "usual", last, (usual,), chart, words)


def hrv_signal(hrv: Mapping[date, float], today: date, cfg: DomainConfig) -> Signal:
    c = cfg.readiness
    history = _before(hrv, today, c.hrv_range_nights)
    last = hrv.get(today)
    chart = _chart(hrv, today, c.chart_nights)
    if last is None or len(history) < c.hrv_min_nights:
        return Signal("hrv", "missing", last, (), chart, "No HRV for last night" if last is None
                      else "Not enough nights yet to know your range")
    lo, hi = percentile(history, c.hrv_low_percentile), percentile(history, c.hrv_high_percentile)
    low = last < lo
    words = (f"{last:g} ms, below your range of {lo:.0f} to {hi:.0f}" if low
             else f"{last:g} ms, within your range of {lo:.0f} to {hi:.0f}" if last <= hi
             else f"{last:g} ms, above your range of {lo:.0f} to {hi:.0f}")
    return Signal("hrv", "low" if low else "usual", last, (lo, hi), chart, words)


def load_signal(load: Mapping[date, float], today: date, cfg: DomainConfig) -> Signal:
    """Days ending yesterday: readiness is read in the morning, before today's session."""
    c = cfg.readiness
    days28 = [load.get(today - timedelta(days=i)) for i in range(1, 29)]
    chart = _chart(load, today - timedelta(days=1), c.chart_nights)
    known = [v for v in days28 if v is not None]
    if len(known) < c.load_min_days or sum(known) <= 0:
        return Signal("load", "missing", None, (c.load_ratio_low, c.load_ratio_high), chart,
                      "Not enough training history yet")
    acute = sum(v or 0 for v in days28[:7]) / 7
    chronic = sum(known) / 28
    ratio = round(acute / chronic, 2)
    low = ratio > c.load_ratio_high
    words = (f"{ratio:.2f}, above the {c.load_ratio_low:g} to {c.load_ratio_high:g} range" if low
             else f"{ratio:.2f}, below the {c.load_ratio_low:g} to {c.load_ratio_high:g} range" if ratio < c.load_ratio_low
             else f"{ratio:.2f}, within the {c.load_ratio_low:g} to {c.load_ratio_high:g} range")
    return Signal("load", "low" if low else "usual", ratio, (c.load_ratio_low, c.load_ratio_high), chart, words)


def decide(*, today: date, sleep: Mapping[date, float], hrv: Mapping[date, float], load: Mapping[date, float],
           garmin_score: float | None = None, cfg: DomainConfig = DEFAULT) -> Readiness:
    """Today's readiness from nightly sleep (minutes), nightly HRV (ms) and daily load.

    TODO(sports-science): "none low" is Push even when the load ratio is under
    0.8 (the prompt's table says "load in range"; under-loaded isn't a reason
    to hold back). Confirm with the sign-off.
    """
    signals = (sleep_signal(sleep, today, cfg), hrv_signal(hrv, today, cfg), load_signal(load, today, cfg))
    known = [s for s in signals if s.status != "missing"]
    missing = tuple(SIGNAL_NAMES[s.key] for s in signals if s.status == "missing")
    low = sum(1 for s in known if s.status == "low")
    decision = None if not known else "back_off" if low >= 2 else "hold" if low == 1 else "push"
    summary = f"{low} of {len(known)} below usual" if known else "Not enough data yet"
    if known and missing:
        summary += " · " + " and ".join(missing) + " missing"
    return Readiness(
        decision=decision, word=DECISION_WORDS.get(decision or ""), low=low, known=len(known), signals=signals,
        missing=missing, summary=summary,
        garmin_label=f"Garmin readiness {garmin_score:.0f}" if garmin_score is not None else None,
    )
