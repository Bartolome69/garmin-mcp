"""Target paces from the best recent race (build prompt, sections 5.6 and 6).

Race times at other distances come from Riegel's formula, T2 = T1 x (D2/D1)^1.06,
and each training zone is a range around one of them. Easier / Harder steps the
whole set by 2% a step. Seconds per km throughout. The app has a TypeScript twin
(mobile/src/domain/paces.ts), held to the same answers by fixtures/paces.json.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .config import DEFAULT, DomainConfig
from .model import mmss, round_half_up, short_date

ZONES = ("easy", "steady", "marathon", "threshold", "tempo", "10k", "5k", "vo2")
ZONE_WORDS = {"easy": "Easy", "steady": "Steady", "marathon": "Marathon", "threshold": "Threshold",
              "tempo": "Tempo", "10k": "10K", "5k": "5K", "vo2": "VO2"}
RACE_NAMES = {5.0: "5K", 10.0: "10K", 21.0975: "half marathon", 42.195: "marathon"}


def riegel(seconds: float, from_km: float, to_km: float, k: float = DEFAULT.paces.riegel) -> float:
    return seconds * (to_km / from_km) ** k


def hour_distance(seconds: float, km: float, k: float = DEFAULT.paces.riegel) -> float:
    """The distance this race says you'd cover in an hour flat: threshold."""
    return km * (3600 / seconds) ** (1 / k)


@dataclass(frozen=True)
class Paces:
    zones: dict[str, tuple[int, int]]   # zone -> (fast, slow) seconds per km
    basis: str                          # "Based on your 10K of 43:30 (12 Oct)"
    step: int                           # 0, or Harder +n / Easier -n


def paces(race_km: float, race_seconds: float, *, race_date: date | None = None, step: int = 0,
          cfg: DomainConfig = DEFAULT) -> Paces:
    k = cfg.paces.riegel
    at = lambda km: riegel(race_seconds, race_km, km, k) / km  # noqa: E731 - seconds per km at that race distance
    marathon, half, ten, five, three = at(42.195), at(21.0975), at(10.0), at(5.0), at(3.0)
    threshold = 3600 / hour_distance(race_seconds, race_km, k)
    raw = {
        "easy": (marathon * 1.15, marathon * 1.30),
        "steady": (marathon * 1.05, marathon * 1.12),
        "marathon": (marathon * 0.99, marathon * 1.01),
        "threshold": (threshold * 0.99, threshold * 1.01),
        "tempo": (half * 1.00, half * 1.03),
        "10k": (ten * 0.99, ten * 1.01),
        "5k": (five * 0.99, five * 1.01),
        "vo2": (three, five),
    }
    # Harder makes every pace faster, Easier slower: 2% a step.
    factor = (1 - cfg.paces.step) ** step if step > 0 else (1 + cfg.paces.step) ** (-step)
    zones = {z: (round_half_up(lo * factor), round_half_up(hi * factor)) for z, (lo, hi) in raw.items()}
    name = RACE_NAMES.get(round(race_km, 4), f"{race_km:g} km")
    when = f" ({short_date(race_date)})" if race_date else ""
    return Paces(zones, f"Based on your {name} of {mmss(race_seconds)}{when}", step)


def default_paces(cfg: DomainConfig = DEFAULT) -> Paces:
    """Before a watch is connected: a 10K of 45:00."""
    return paces(cfg.paces.default_race_km, cfg.paces.default_race_seconds, cfg=cfg)


def pace_range_words(zone: tuple[int, int]) -> str:
    return f"{mmss(zone[0])}–{mmss(zone[1])} /km"
