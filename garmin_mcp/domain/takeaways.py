"""One rule-based observation under each post-run chart (build prompt, sections 5.9 and 6).

Observations only, never causes: "HR drifted 4 bpm", never "because you slept
badly". Each is a template with a threshold (config.py).
"""

from __future__ import annotations

from typing import Sequence

from .config import DEFAULT, DomainConfig
from .model import mmss


def reps(rep_paces: Sequence[float], cfg: DomainConfig = DEFAULT) -> str | None:
    """Rep paces in seconds per km, in order."""
    if len(rep_paces) < 2:
        return None
    spread = max(rep_paces) - min(rep_paces)
    fade = rep_paces[-1] - rep_paces[0]
    if fade >= cfg.takeaways.fade_seconds and spread > cfg.takeaways.even_reps_seconds:
        return f"Last rep {fade:.0f} s/km slower than the first."
    if spread <= cfg.takeaways.even_reps_seconds:
        return f"Even reps. Fastest and slowest {spread:.0f} s apart."
    return f"Reps varied. Fastest and slowest {spread:.0f} s apart."


def splits(km_seconds: Sequence[float], cfg: DomainConfig = DEFAULT) -> str | None:
    """Seconds for each full km, in order: a negative or positive split, or even."""
    if len(km_seconds) < 4:
        return None
    half = len(km_seconds) // 2
    first, second = km_seconds[:half], km_seconds[-half:]
    a, b = sum(first) / len(first), sum(second) / len(second)
    pct = (a - b) / a * 100
    if pct >= cfg.takeaways.negative_split_pct:
        return f"Negative split. Second half {a - b:.0f} s/km faster."
    if -pct >= cfg.takeaways.negative_split_pct:
        return f"Second half {b - a:.0f} s/km slower."
    return f"Even pace. Halves within {abs(a - b):.0f} s/km, around {mmss((a + b) / 2)} /km."


def hr_drift(pace: Sequence[float], hr: Sequence[float], cfg: DomainConfig = DEFAULT) -> str | None:
    """Pace (s/km) and HR (bpm) samples at the same moments. Compared half against half,
    only when the halves were run at much the same pace."""
    if len(pace) != len(hr) or len(hr) < 4:
        return None
    half = len(hr) // 2
    p1, p2 = sum(pace[:half]) / half, sum(pace[-half:]) / half
    if abs(p1 - p2) / p1 * 100 > cfg.takeaways.hr_drift_pace_pct:
        return None
    h1, h2 = sum(hr[:half]) / half, sum(hr[-half:]) / half
    drift = h2 - h1
    if drift >= cfg.takeaways.hr_drift_bpm:
        return f"HR drifted {drift:.0f} bpm at the same pace."
    return "HR steady at the same pace."


def cadence(spm: Sequence[float], cfg: DomainConfig = DEFAULT) -> str | None:
    if len(spm) < 3:
        return None
    third = len(spm) // 3
    a, b = sum(spm[:third]) / third, sum(spm[-third:]) / third
    change = b - a
    if change <= -cfg.takeaways.cadence_change_spm:
        return f"Cadence dropped {-change:.0f} spm in the last third."
    if change >= cfg.takeaways.cadence_change_spm:
        return f"Cadence rose {change:.0f} spm in the last third."
    return f"Cadence steady, around {sum(spm) / len(spm):.0f} spm."


def rep_hr(rep_hr: Sequence[float], cfg: DomainConfig = DEFAULT) -> str | None:
    """Average HR of each rep, in order: the later half of the reps against the first."""
    if len(rep_hr) < 2:
        return None
    half = len(rep_hr) // 2
    a, b = sum(rep_hr[:half]) / half, sum(rep_hr[-half:]) / half
    drift = b - a
    if drift >= cfg.takeaways.hr_drift_bpm:
        return f"HR drifted {drift:.0f} bpm across the {len(rep_hr)} reps."
    return f"HR steady across the {len(rep_hr)} reps."


def elevation(gain_m: float, loss_m: float, cfg: DomainConfig = DEFAULT) -> str | None:
    """Metres climbed and descended. Flat under the threshold either way."""
    if gain_m < cfg.takeaways.flat_metres and loss_m < cfg.takeaways.flat_metres:
        return "Flat. Under " + f"{cfg.takeaways.flat_metres:.0f} m of climbing."
    return f"{gain_m:.0f} m up, {loss_m:.0f} m down."
