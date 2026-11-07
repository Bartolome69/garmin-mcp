"""The plan builder: answers in, a whole plan out, by rule.

The app's six steps (docs/plan-engine-spec.md, section 1) give their
answers here, with what Garmin knows about the runner, and get back the
plan as the preview draws it and as create_plan takes it. No model and no
randomness: the same answers always give the same plan and the same notes.

Weeks run Monday to Sunday. A race belongs to the week that holds it, and
the plan ends on race day. Paces come from current fitness, never from the
goal alone. The numbers are Daash's own conservative defaults (the spec's
section 3), to be reviewed by a coach before everyone gets this.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS_LONG = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")

# The four distances a plan is built for, by what a race's distance is nearest.
DISTANCES = {"5k": 5.0, "10k": 10.0, "half": 21.0975, "marathon": 42.195}
DISTANCE_NAMES = {"5k": "5K", "10k": "10K", "half": "half", "marathon": "marathon"}
PREDICTION_KEYS = {"5k": "5k", "10k": "10k", "half": "half_marathon", "marathon": "marathon"}

MIN_WEEKS, MAX_WEEKS, OPEN_WEEKS = 3, 20, 12
# Past a marathon is an ultra. For now it's planned as a marathon build, and the
# preview says so: back-to-back long runs and time on feet aren't in v1.
ULTRA_KM = 43
# The longest the long run gets, by distance, and in time (3 h for a marathon).
LONG_CAP_KM = {"5k": 12, "10k": 16, "half": 20, "marathon": 32}
LONG_CAP_MIN = {"5k": 150, "10k": 150, "half": 150, "marathon": 180}
# The share of the remaining weeks in each phase, after the taper.
PHASE_SHARES = {"5k": (0.4, 0.4, 0.2), "10k": (0.4, 0.4, 0.2), "half": (0.4, 0.35, 0.25), "marathon": (0.35, 0.4, 0.25)}
# Each taper week's distance as a share of the peak, the race week last.
TAPERS = {"5k": (0.6,), "10k": (0.6,), "half": (0.7, 0.5), "marathon": (0.75, 0.6, 0.4)}
RECOVERY_SHARE = 0.75
B_RACE_SHARE = 0.75
# Easy days after a B race before the next harder session, by distance.
EASY_AFTER = {"5k": 2, "10k": 2, "half": 5, "marathon": 7}
MIN_RUN_KM = 3
GENTLE_WEEKS = 3
NO_DATA_EASY = 390  # 6:30 /km, for distances when Garmin has no paces

STYLES = {
    "steady": ("Steady", "80/20"),
    "balanced": ("Balanced", "Daniels style"),
    "faster": ("Faster", "Norwegian singles style"),
}


class BuildError(ValueError):
    """An answer the builder can't make a plan from, in words to show."""


# -- times and paces -------------------------------------------------------------

DURATION_RE = re.compile(r"^(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?$")


def parse_time(text: Any) -> int | None:
    """Seconds from "24:38", "1:52:03", "4:50 /km" or Daash's own "1h 25m 00s"."""
    text = str(text or "").strip().replace(" /km", "").replace("/km", "").strip()
    found = DURATION_RE.match(text) if any(u in text for u in "hms") else None
    if found and any(found.groups()):
        h, m, sec = (int(g or 0) for g in found.groups())
        return h * 3600 + m * 60 + sec or None
    parts = text.split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    secs = 0
    for p in parts:
        secs = secs * 60 + int(p)
    return secs or None


def goal_secs(text: Any, dist: str) -> int | None:
    """A goal time in seconds. "1:48" is an hour and 48 minutes for a half or a marathon."""
    parts = str(text or "").strip().split(":")
    if len(parts) == 2 and dist in ("half", "marathon") and all(p.isdigit() for p in parts) and int(parts[0]) < 10:
        return int(parts[0]) * 3600 + int(parts[1]) * 60
    return parse_time(text)


def clock(secs: float) -> str:
    """A finish time as runners say it: 24:38, 1:52."""
    secs = int(round(secs))
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}:{m:02d}"
    return f"{m}:{s:02d}"


def pace(secs_per_km: float) -> str:
    secs = int(round(secs_per_km))
    return f"{secs // 60}:{secs % 60:02d}"


def riegel(secs: float, km: float, to_km: float) -> float:
    return secs * (to_km / km) ** 1.06


def distance_of(km: float | None) -> str:
    """The distance a race is trained as: the one it's nearest, up the scale."""
    km = float(km or 0)
    if km <= 7:
        return "5k"
    if km <= 15:
        return "10k"
    if km <= 30:
        return "half"
    return "marathon"


@dataclass
class Fitness:
    """What Garmin knows: each distance's predicted time, threshold, a recent race."""

    predictions: dict[str, int] = field(default_factory=dict)  # by DISTANCES key, seconds
    threshold_pace: float | None = None  # seconds per km
    threshold_hr: int | None = None
    recent_race: dict[str, Any] | None = None  # name, date, km, secs

    def predicted(self, dist: str, km: float | None = None) -> float | None:
        """A finish time for a distance: a recent race first, then Garmin's prediction."""
        km = km or DISTANCES[dist]
        race = self.recent_race
        if race and race.get("km") and race.get("secs") and race["km"] >= 3:
            return riegel(race["secs"], race["km"], km)
        own = self.predictions.get(dist)
        if own:
            return own if abs(km - DISTANCES[dist]) < 0.2 else riegel(own, DISTANCES[dist], km)
        for other, secs in self.predictions.items():
            return riegel(secs, DISTANCES[other], km)
        return None

    def source(self) -> str | None:
        race = self.recent_race
        if race and race.get("km") and race.get("secs"):
            return f"recent {race.get('name') or 'race'}"
        if "10k" in self.predictions:
            return f"{clock(self.predictions['10k'])} 10K prediction"
        if self.predictions:
            dist, secs = next(iter(self.predictions.items()))
            return f"{clock(secs)} {DISTANCE_NAMES[dist]} prediction"
        return None


@dataclass
class Paces:
    easy: float  # seconds per km, the middle of the easy range
    marathon: float | None
    threshold: float | None
    interval: float | None
    race: float | None  # the goal distance's, from fitness or a goal near it
    known: bool

    @classmethod
    def of(cls, fitness: Fitness, dist: str, km: float, avg_km: float, goal_secs: int | None) -> "Paces":
        half = fitness.predicted("half")
        if half is None:
            return cls(NO_DATA_EASY, None, None, None, None, False)
        # The marathon prediction flatters anyone not yet running 40 km a week.
        mar = fitness.predictions.get("marathon") if avg_km >= 40 and not fitness.recent_race else None
        mar = mar or riegel(half, DISTANCES["half"], DISTANCES["marathon"])
        mp = mar / DISTANCES["marathon"]
        ten = fitness.predicted("10k") or riegel(half, DISTANCES["half"], 10)
        five = fitness.predicted("5k") or riegel(half, DISTANCES["half"], 5)
        threshold = fitness.threshold_pace or (ten / 10 + half / DISTANCES["half"]) / 2
        predicted = fitness.predicted(dist, km)
        race = predicted / km if predicted else None
        # Riegel flatters an ultra; its race-pace work is at marathon pace.
        if km > ULTRA_KM:
            return cls(mp + 60, mp, threshold, five / 5, mp, True)
        # The goal's pace only when it's within 3% of what fitness says.
        if goal_secs and predicted and goal_secs >= predicted * 0.97:
            race = goal_secs / km
        return cls(mp + 60, mp, threshold, five / 5, race, True)

    def easy_range(self) -> str:
        return f"{pace(self.easy - 15)}-{pace(self.easy + 15)} /km"

    def window(self, secs: float | None, give: int) -> list[str] | None:
        return [pace(secs - give), pace(secs + give)] if secs else None


# -- the answers ------------------------------------------------------------------

@dataclass
class Race:
    name: str
    day: date
    km: float
    tier: str  # A, B or C

    @property
    def dist(self) -> str:
        return distance_of(self.km)


@dataclass
class Answers:
    avg_km: float
    longest_km: float
    level: str = "regular"  # new, regular, exp
    races: list[Race] = field(default_factory=list)
    goal_time: str | None = None
    open_goal: str = "faster"  # with no race: faster, base, fit
    open_distance: str = "half"
    days: int = 4
    out: tuple[int, ...] = ()
    long_day: int = 6
    amount: str = "build"  # keep, build, more
    injury: str = "none"  # none, niggle, back, hurts
    style: str = "balanced"  # steady, balanced, faster, pick
    after_plan: bool = False

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Answers":
        def num(key: str, low: float, high: float, default: float) -> float:
            try:
                return min(high, max(low, float(raw.get(key))))
            except (TypeError, ValueError):
                return default

        races = []
        for r in raw.get("races") or []:
            try:
                day = date.fromisoformat(str(r.get("date"))[:10])
                km = float(r.get("km") or 0)
            except (TypeError, ValueError):
                continue
            tier = str(r.get("tier") or "C").upper()
            if km <= 0 or tier not in ("A", "B", "C"):
                continue
            races.append(Race(str(r.get("name") or "Race").strip()[:60] or "Race", day, km, tier))
        out = tuple(sorted({int(d) for d in raw.get("out") or [] if str(d).isdigit() and 0 <= int(d) <= 6}))
        long_day = int(num("long_day", 0, 6, 6))
        if long_day in out:
            raise BuildError("The long run can't be on a day you can't run.")
        return cls(
            avg_km=round(num("avg_km", 0, 250, 0)), longest_km=round(num("longest_km", 0, 60, 0)),
            level=raw.get("level") if raw.get("level") in ("new", "regular", "exp") else "regular",
            races=sorted(races, key=lambda r: r.day),
            goal_time=str(raw["goal_time"]) if parse_time(raw.get("goal_time")) else None,
            open_goal=raw.get("open_goal") if raw.get("open_goal") in ("faster", "base", "fit") else "faster",
            open_distance=raw.get("open_distance") if raw.get("open_distance") in DISTANCES else "half",
            days=int(num("days", 3, 6, 4)), out=out, long_day=long_day,
            amount=raw.get("amount") if raw.get("amount") in ("keep", "build", "more") else "build",
            injury=raw.get("injury") if raw.get("injury") in ("none", "niggle", "back", "hurts") else "none",
            style=raw.get("style") if raw.get("style") in ("steady", "balanced", "faster", "pick") else "balanced",
            after_plan=bool(raw.get("after_plan")),
        )

    @property
    def gentle(self) -> bool:
        return self.injury in ("niggle", "back")


def first_monday(today: date) -> date:
    """Plans start on a Monday: today if it is one, else the next."""
    return today + timedelta(days=(7 - today.weekday()) % 7)


def faster_allowed(a: Answers, fitness: Fitness) -> bool:
    return a.days >= 4 and bool(fitness.threshold_hr) and a.level != "new" and not a.gentle


def pick_style(a: Answers, fitness: Fitness) -> tuple[str, str]:
    """Pick for me: one style and the line that says why."""
    if a.gentle:
        return "steady", "Steady fits best while you're easing back: one harder day a week at most."
    if a.level == "new":
        return "steady", "Steady fits best for a first plan: mostly easy, so the running itself does the work."
    goal = goal_race(a)
    races = f"a goal {DISTANCE_NAMES[goal.dist]}" if goal else "no race"
    bs = [r for r in a.races if r.tier == "B" and (not goal or r.day < goal.day)]
    extra = f" and a B {DISTANCE_NAMES[bs[0].dist]}" if bs else ""
    if a.level == "exp" and a.days >= 5 and faster_allowed(a, fitness):
        return "faster", f"Faster fits best: {a.days} days a week and your heart-rate data leave room for threshold sessions."
    return "balanced", f"Balanced fits best: {a.days} days a week, {races}{extra} leave room for two harder days."


def goal_race(a: Answers) -> Race | None:
    """The A race: the last one marked A, the others raced as B."""
    found = [r for r in a.races if r.tier == "A"]
    return found[-1] if found else None


def goal_chips(dist: str, km: float, fitness: Fitness) -> list[dict[str, Any]]:
    """The goal times offered for the A race: what fitness says, a stretch, very ambitious."""
    predicted = fitness.predicted(dist, km)
    if not predicted or km > ULTRA_KM:
        return []
    step = 60 if predicted >= 3600 else 30 if predicted >= 1500 else 10
    rounded = lambda s: int(round(s / step) * step)  # noqa: E731
    return [{"time": clock(rounded(predicted * f)), "secs": rounded(predicted * f)} for f in (1.0, 0.96, 0.91)]


def goal_verdict(goal_secs: int | None, dist: str, km: float, fitness: Fitness) -> dict[str, str] | None:
    """Realistic, a stretch, or very ambitious, and the sentence that says why."""
    predicted = fitness.predicted(dist, km)
    if not goal_secs or not predicted or km > ULTRA_KM:
        return None
    under = (predicted - goal_secs) / predicted
    gap = abs(predicted - goal_secs)
    gap_words = f"{round(gap / 60)} minutes" if gap >= 90 else f"{round(gap)} seconds"
    said = clock(predicted)
    if under <= 0.03:
        return {"kind": "realistic", "chip": "Realistic",
                "why": f"Matches Garmin's {said} prediction from your recent runs." if under >= -0.03
                else f"Slower than Garmin's {said} prediction, so there's room to spare."}
    if under <= 0.08:
        return {"kind": "stretch", "chip": "A stretch",
                "why": f"{gap_words} under Garmin's {said}. Doable with steady building."}
    return {"kind": "ambitious", "chip": "Very ambitious",
            "why": f"{gap_words} under your prediction. The plan trains you from where you are, and we'll check again on the way."}


# -- the shape of the plan --------------------------------------------------------

@dataclass
class Shape:
    """What the plan is for and how long it runs."""

    kind: str  # race, faster, base, fit
    dist: str
    km: float
    start: date
    weeks: int
    race: Race | None
    phases: list[str]


def shape(a: Answers, today: date, starts: date | None = None) -> Shape:
    start = starts or first_monday(today)
    race = goal_race(a)
    if race:
        if race.day < start:
            raise BuildError(f"{race.name} is before the plan could start.")
        last_monday = race.day - timedelta(days=race.day.weekday())
        weeks = (last_monday - start).days // 7 + 1
        if weeks < MIN_WEEKS:
            raise BuildError(f"{race.name} is too close for a plan. Run easy and enjoy it.")
        if weeks > MAX_WEEKS:
            start = last_monday - timedelta(weeks=MAX_WEEKS - 1)
            weeks = MAX_WEEKS
        dist, km, kind = race.dist, race.km, "race"
    else:
        weeks, kind = OPEN_WEEKS, a.open_goal
        dist = a.open_distance if kind == "faster" else "10k"
        km = DISTANCES[dist]
    return Shape(kind, dist, km, start, weeks, race, phases(kind, dist, weeks))


def phases(kind: str, dist: str, weeks: int) -> list[str]:
    """Each week's phase. Short plans lose base first, then build; the taper is never cut."""
    if kind == "base":
        return ["Base"] * weeks
    if kind == "fit":
        half = weeks // 2
        return ["Base"] * half + ["Build"] * (weeks - half)
    taper = len(TAPERS[dist])
    if dist == "marathon" and weeks < 16:
        taper = 2
    taper = min(taper, max(1, weeks - 1))
    rest = weeks - taper
    _, build_share, peak_share = PHASE_SHARES[dist]
    if rest <= 3:
        peak, build = min(1, rest), max(0, rest - 1)
    else:
        peak = max(1, round(rest * peak_share))
        build = max(1, round(rest * build_share))
    base = max(0, rest - peak - build)
    build = rest - peak - base
    return ["Base"] * base + ["Build"] * build + ["Peak"] * peak + ["Taper"] * taper


# -- volume ----------------------------------------------------------------------------

def _step(full: float, gentle: bool) -> float:
    rate, floor = (0.05, 2) if gentle else (0.10, 3)
    return max(full * rate, floor)


def volumes(start_km: float, peak_km: float, weeks: Sequence[str], cycle: int, gentle: bool,
            dist: str, first_easy: bool) -> tuple[list[float], list[bool]]:
    """Each week's distance: small steps up to the peak, an easier week every `cycle`th, then the taper.

    The week after an easier one goes back to the last full week, not above it.
    """
    out, recovery = [], []
    full = start_km
    after_easy = False
    building = [p for p in weeks if p != "Taper"]
    for i, _ in enumerate(building):
        if i == 0:
            easy = first_easy
            out.append(round(start_km * RECOVERY_SHARE) if easy else start_km)
            recovery.append(easy)
            after_easy = easy
            continue
        if (i + 1) % cycle == 0:
            out.append(round(full * RECOVERY_SHARE))
            recovery.append(True)
            after_easy = True
            continue
        if not after_easy:
            full = min(peak_km, full + _step(full, gentle))
        after_easy = False
        out.append(round(full))
        recovery.append(False)
    top = max(out) if out else start_km
    tapers = [p for p in weeks if p == "Taper"]
    shares = list(TAPERS.get(dist, (0.6,)))[-len(tapers):] if tapers else []
    for share in shares:
        out.append(round(top * share))
        recovery.append(False)
    return out, recovery


def reach(start_km: float, weeks: int, cycle: int, gentle: bool) -> float:
    """The most the progression rules reach in the weeks there are."""
    full = start_km
    after_easy = False
    for i in range(1, weeks):
        if (i + 1) % cycle == 0:
            after_easy = True
            continue
        if not after_easy:
            full += _step(full, gentle)
        after_easy = False
    return round(full)


def amounts(a: Answers, s: Shape, fitness: Fitness | None = None) -> list[dict[str, Any]]:
    """Step 4's three options, each a peak the rules reach from here.

    The suggested build is gentler for a first plan or after a niggle: about
    a third more than now, not three fifths.
    """
    start = start_km(a)
    building = sum(1 for p in s.phases if p != "Taper")
    most = reach(start, building, cycle(a), a.gentle)
    keep = max(start, round(a.avg_km))
    grow = 1.3 if a.level == "new" or a.gentle else 1.6
    build = max(keep, min(most, max(keep + 4, round(a.avg_km * grow))))
    more = max(build, min(most, round(a.avg_km * 1.9)))
    goal = goal_words(a, s)
    # "Best fit for 1:22" only for a goal the plan can honestly aim at.
    if goal and a.goal_time and fitness is not None and s.race:
        verdict = goal_verdict(goal_secs(a.goal_time, s.dist), s.dist, s.km, fitness)
        if verdict and verdict["kind"] == "ambitious":
            goal = None
    found = [
        {"id": "keep", "peak": keep, "t": f"Keep around {keep} km",
         "d": f"Same as now. Enough to finish the {DISTANCE_NAMES[s.dist]} comfortably." if s.kind in ("race", "faster")
         else "Same as now, with an easier week every few weeks."},
        {"id": "build", "peak": build, "t": f"Build steadily to about {build} km", "rec": True,
         "d": (f"About {'5' if a.gentle else '10'}% a week, with an easier week every {_nth(cycle(a))}."
               + (f" Best fit for {goal}." if goal else ""))},
    ]
    if more >= build + 4 and a.level != "new" and not a.gentle:
        found.append({"id": "more", "peak": more, "t": f"Build more, to about {more} km",
                      "d": "For a faster time. More running on tired legs in the peak weeks."})
    if build <= keep:
        found = [found[0]]
    return found


def _nth(n: int) -> str:
    return {3: "third", 4: "fourth"}.get(n, f"{n}th")


def goal_words(a: Answers, s: Shape) -> str | None:
    if a.goal_time and s.race:
        return a.goal_time
    if s.race:
        return s.race.name
    return None


def start_km(a: Answers) -> float:
    start = max(6.0, float(a.avg_km))
    return round(start * 0.7) if a.injury == "back" else round(start)


def long_share(days: int) -> float:
    """The most of a week the long run takes: more when there are fewer days to spread it over."""
    return {3: 0.45, 4: 0.40}.get(days, 0.33)


def cycle(a: Answers) -> int:
    return 3 if a.level == "new" or a.gentle else 4


def peak_for(a: Answers, s: Shape) -> float:
    options = {o["id"]: o["peak"] for o in amounts(a, s)}
    return options.get(a.amount) or options.get("build") or options["keep"]


# -- the week ----------------------------------------------------------------------------

def run_days(days: int, out: Sequence[int], long_day: int, usual: Sequence[int] = ()) -> list[int]:
    """Which weekdays to run: the long-run day, and the rest as spread out as they go."""
    free = [d for d in range(7) if d not in out and d != long_day]
    need = max(0, min(days, len(free) + 1) - 1)
    best, best_score = None, None
    for combo in itertools.combinations(free, need):
        chosen = sorted((*combo, long_day))
        gaps = [(chosen[(i + 1) % len(chosen)] - chosen[i]) % 7 or 7 for i in range(len(chosen))]
        score = (sum(g * g for g in gaps), -sum(1 for d in combo if d in usual), combo)
        if best_score is None or score < best_score:
            best, best_score = chosen, score
    return best or [long_day]


def _apart(a: int, b: int) -> int:
    d = abs(a - b) % 7
    return min(d, 7 - d)


def quality_days(days: Sequence[int], long_day: int, count: int, hard_long: bool) -> list[int]:
    """Days for the harder sessions: a day between each, and between them and a hard long run."""
    others = [d for d in days if d != long_day]
    gap_to_long = 2 if hard_long else 1
    for n in range(count, 0, -1):
        found = None
        for combo in itertools.combinations(others, n):
            if any(_apart(d, long_day) < gap_to_long for d in combo):
                continue
            if any(_apart(x, y) < 2 for x, y in itertools.combinations(combo, 2)):
                continue
            score = min([_apart(d, long_day) for d in combo] + [_apart(x, y) for x, y in itertools.combinations(combo, 2)] + [7])
            if found is None or score > found[0]:
                found = (score, combo)
        if found:
            return sorted(found[1])
    return []


def quality_count(style: str, level: str, days: int, phase: str) -> int:
    if style == "faster":
        return 1 if phase == "Taper" else (3 if days >= 6 else 2)
    if style == "steady":
        return 0 if phase == "Base" else 1
    if level == "new":
        return 0 if phase == "Base" or days < 4 else 1
    if phase in ("Base", "Taper"):
        return 1
    if days >= 6 or (days == 5 and (level == "exp" or phase in ("Build", "Peak"))):
        return 2
    return 1


# -- sessions --------------------------------------------------------------------------------

def _k(km: float) -> str:
    return f"{km:g}k"


def _km_of(steps: Sequence[Mapping[str, Any]], p: Paces) -> float:
    total = 0.0
    for st in steps:
        if st.get("type") == "repeat":
            total += st["times"] * _km_of(st["steps"], p)
            continue
        if st.get("distance_meters"):
            total += st["distance_meters"] / 1000
            continue
        secs = st.get("duration_seconds") or 0
        speed = p.easy
        if st.get("type") == "interval" and st.get("pace"):
            mid = sum(parse_time(x) or 0 for x in st["pace"]) / 2
            speed = mid or speed
        elif st.get("type") == "interval":
            speed = p.threshold or p.easy - 60
        elif st.get("type") == "recovery":
            speed = p.easy + 30
        total += secs / speed
    return total


def _warm(work: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "warmup", "duration_seconds": 900}, *work, {"type": "cooldown", "duration_seconds": 600}]


def _reps(times: int, work: dict[str, Any], rest_secs: int) -> dict[str, Any]:
    return {"type": "repeat", "times": times, "steps": [{"type": "interval", **work}, {"type": "recovery", "duration_seconds": rest_secs}]}


# Each kind of harder session, in the order a plan works through them.
TEMPO = [(2, 6), (2, 8), (3, 6), (3, 8), (4, 8), (3, 10), (2, 15)]
# Where each phase starts in TEMPO, and how far it goes.
TEMPO_FROM = {"Base": (0, 2), "Build": (3, 6), "Peak": (5, 6), "Taper": (2, 2)}
INTERVALS = [(5, 1000), (6, 1000), (5, 1200), (6, 800), (8, 800)]
SUB = [(10, 3), (6, 6), (3, 10)]


def _tempo(i: int, p: Paces, phase: str) -> tuple[str, list[dict[str, Any]], str]:
    first, last = TEMPO_FROM.get(phase, (0, 2))
    reps, mins = TEMPO[min(first + i, last)]
    target = {"pace": p.window(p.threshold, 10)} if p.known else {"hr": "z4"}
    steps = _warm([_reps(reps, {"duration_seconds": mins * 60, **target}, 120)])
    words = f"Threshold at {pace(p.threshold)} /km, comfortably hard." if p.known else "Comfortably hard."
    return f"Tempo {reps}x{mins}min", steps, words


def _intervals(i: int, p: Paces) -> tuple[str, list[dict[str, Any]], str]:
    reps, metres = INTERVALS[min(i, len(INTERVALS) - 1)]
    if not p.known:
        steps = _warm([_reps(reps, {"duration_seconds": 180, "hr": "z5"}, 120)])
        return f"Intervals {reps}x3min", steps, "Hard but controlled."
    steps = _warm([_reps(reps, {"distance_meters": metres, "pace": p.window(p.interval, 5)}, 120)])
    label = f"{metres // 1000}k" if metres % 1000 == 0 else f"{metres}m" if metres < 1000 else f"{metres / 1000:g}k"
    return f"Intervals {reps}x{label}", steps, f"5K pace, {pace(p.interval)} /km, hard but controlled."


def _sub(i: int, p: Paces, hr: int) -> tuple[str, list[dict[str, Any]], str]:
    reps, mins = SUB[i % len(SUB)]
    steps = _warm([_reps(reps, {"duration_seconds": mins * 60, "hr": [hr - 12, hr - 5]}, 60)])
    return (f"Threshold {reps}x{mins}min", steps,
            f"Just under threshold, {hr - 12}-{hr - 5} bpm. A hot or tired day simply runs slower.")


def _race_pace(i: int, p: Paces, dist: str, taper: bool) -> tuple[str, list[dict[str, Any]], str]:
    if not p.known:
        return _tempo(i, p, "Peak")
    word = {"5k": "5K pace", "10k": "10K pace", "half": "Half pace", "marathon": "Marathon pace"}[dist]
    if taper:
        reps, metres = (4, 1000) if dist in ("5k", "10k") else (3, 2000)
    else:
        options = {"5k": [(6, 800), (5, 1000)], "10k": [(4, 2000), (3, 3000)],
                   "half": [(2, 3000), (2, 4000), (3, 3000)], "marathon": [(2, 5000), (3, 5000)]}[dist]
        reps, metres = options[min(i, len(options) - 1)]
    steps = _warm([_reps(reps, {"distance_meters": metres, "pace": p.window(p.race, 5 if dist in ("5k", "10k") else 10)}, 180)])
    label = f"{metres // 1000}k" if metres % 1000 == 0 else f"{metres}m"
    return f"{word} {reps}x{label}", steps, f"{word}, {pace(p.race)} /km."


def _easy(km: float, p: Paces, strides: bool = False) -> tuple[str, list[dict[str, Any]], str]:
    steps: list[dict[str, Any]] = [{"type": "interval", "distance_meters": int(km * 1000)}]
    words = f"Easy, {p.easy_range()}, you could chat." if p.known else "Easy, you could chat."
    if strides:
        steps.append(_reps(6, {"duration_seconds": 20}, 60))
        return f"Easy {_k(km)} + strides", steps, words + " Then 6 relaxed strides of 20 seconds."
    return f"Easy {_k(km)}", steps, words


def _long(km: float, p: Paces, mp_km: float = 0) -> tuple[str, list[dict[str, Any]], str]:
    words = f"Easy, {p.easy_range()}." if p.known else "Easy, you could chat."
    if mp_km and p.marathon:
        steps = [{"type": "interval", "distance_meters": int((km - mp_km) * 1000)},
                 {"type": "interval", "distance_meters": int(mp_km * 1000), "pace": p.window(p.marathon, 15)}]
        return f"Long {_k(km)} w {_k(mp_km)} MP", steps, f"{words} The last {mp_km:g} km at marathon pace, {pace(p.marathon)} /km."
    return f"Long {_k(km)}", [{"type": "interval", "distance_meters": int(km * 1000)}], words


# -- the plan --------------------------------------------------------------------------------

@dataclass
class Built:
    shape: Shape
    weeks: list[dict[str, Any]]
    sessions: list[dict[str, Any]]
    style: str
    style_why: str | None
    peak_km: float
    long_top: float
    paces: Paces


def build(a: Answers, fitness: Fitness, today: date, starts: date | None = None,
          usual_days: Sequence[int] = ()) -> Built:
    if a.injury == "hurts":
        raise BuildError("No plan while something hurts.")
    s = shape(a, today, starts)
    style, why = (pick_style(a, fitness) if a.style == "pick" else (a.style, None))
    if style == "faster" and not faster_allowed(a, fitness):
        style = "balanced"
    if a.gentle:
        style = "steady" if style == "faster" else style
    p = Paces.of(fitness, s.dist, s.km, a.avg_km, goal_secs(a.goal_time, s.dist))
    peak = peak_for(a, s)
    vols, recovery = volumes(start_km(a), peak, s.phases, cycle(a), a.gentle, s.dist,
                             first_easy=a.after_plan)
    cap = min(LONG_CAP_KM[s.dist], LONG_CAP_MIN[s.dist] * 60 / p.easy)
    share = long_share(a.days)
    days = run_days(a.days, a.out, a.long_day, usual_days)
    end = s.start + timedelta(weeks=s.weeks) - timedelta(days=1)
    if s.race:
        end = s.race.day
    races = [r for r in a.races if s.start <= r.day <= end and r is not s.race]
    if s.race:
        races = [r if r.tier != "A" else Race(r.name, r.day, r.km, "B") for r in races]
    # A B race in base, or in the last 10 days before the A race, is run as training.
    base_end = s.start + timedelta(weeks=s.phases.count("Base"))
    races = [Race(r.name, r.day, r.km, "C") if r.tier == "B" and (
        r.day < base_end or (s.race and (s.race.day - r.day).days <= 10)) else r for r in races]
    # No harder session in the 3 days before a race, or in the easy days after a B race.
    blocked: set[date] = set()
    for r in races + ([s.race] if s.race else []):
        blocked.update(r.day - timedelta(days=n) for n in range(1, 4))
        if r.tier == "B":
            blocked.update(r.day + timedelta(days=n) for n in range(1, EASY_AFTER[r.dist] + 1))

    long_prev = max(float(a.longest_km), MIN_RUN_KM * 2)
    long_top = 0.0
    weeks: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    counters: dict[str, int] = {"intervals": 0, "sub": 0, "pace": 0}
    peak_long = 0.0
    for i, phase in enumerate(s.phases):
        monday = s.start + timedelta(weeks=i)
        target = vols[i]
        week_races = [r for r in races if monday <= r.day <= monday + timedelta(days=6)]
        b_race = next((r for r in week_races if r.tier == "B"), None)
        is_race_week = bool(s.race and monday <= s.race.day <= monday + timedelta(days=6))
        if b_race:
            target = round(target * B_RACE_SHARE)
        # The long run: up to 2 km a week from the last full one, inside its cap and share of the week.
        if phase == "Taper":
            share_of_top = target / max(vols[: len(vols)] or [1])
            long_km = round(peak_long * min(1.0, share_of_top + 0.1))
        elif recovery[i]:
            long_km = round(long_prev * RECOVERY_SHARE)
        else:
            room = max(share * target, min(long_prev, 0.40 * target))
            long_km = round(min(cap, long_prev + 2, room))
            # A B race week has no long run of its own, so the next one carries on from the last.
            if not b_race:
                long_prev = long_km
        long_km = max(MIN_RUN_KM * 2, long_km)
        peak_long = max(peak_long, long_km)

        gentle_now = a.gentle and i < GENTLE_WEEKS
        q = 0 if gentle_now else quality_count(style, a.level, a.days, phase)
        if recovery[i] or b_race:
            q = min(q, 1) if not b_race else 0
        mp_long = style != "faster" and s.dist == "marathon" and phase == "Peak" and p.marathon is not None and not recovery[i]
        if mp_long:
            q = max(0, q - 1)
        week_days = [monday + timedelta(days=d) for d in days]
        long_date = monday + timedelta(days=a.long_day)
        saved = dict(counters)
        # Fewer harder sessions when they and the long run would be most of a small week.
        for q_now in range(q, -1, -1):
            counters.clear()
            counters.update(saved)
            qdays = [monday + timedelta(days=d) for d in quality_days(days, a.long_day, q_now, mp_long or style != "faster")]
            qdays = [d for d in qdays if d not in blocked]
            plan_days: dict[date, dict[str, Any]] = {}

            def add(day: date, name: str, steps: list[dict[str, Any]], words: str, kind: str, race: Race | None = None) -> None:
                plan_days[day] = {"date": day, "name": name, "steps": steps, "description": words, "kind": kind,
                                  "km": round(_km_of(steps, p), 1), "race": race}

            # Races first: the A race, a B race (that week's harder session), C races run as training.
            for r in week_races + ([s.race] if is_race_week else []):
                steps = [{"type": "interval", "distance_meters": int(r.km * 1000)}]
                if r.tier == "C":
                    if qdays and r.km <= 10:
                        qdays = qdays[1:]
                        add(r.day, f"{r.name} at tempo effort", steps, "Run it as a tempo: comfortably hard, not flat out.", "hard")
                    else:
                        add(r.day, f"{r.name}, easy", steps, "Run it as an easy run and enjoy it.", "easy")
                else:
                    add(r.day, f"Race: {r.name}", steps,
                        "Race day. Warm up with 10 minutes easy and a few strides.", "race", r)
            race_long = any(v["kind"] == "race" and v["km"] >= 0.8 * long_km for v in plan_days.values())
            near_race = any(abs((long_date - d).days) <= 2 for d, v in plan_days.items() if v["kind"] == "race")
            if not is_race_week and long_date not in plan_days and not (race_long or near_race):
                add(long_date, *_long(long_km, p, min(10, round(long_km * 0.35)) if mp_long else 0), "long")
            for n, day in enumerate(qdays):
                if day in plan_days or (is_race_week and s.race and day >= s.race.day):
                    continue
                if style == "faster" and fitness.threshold_hr:
                    if phase == "Peak" and s.dist in ("half", "marathon") and n == len(qdays) - 1 and not recovery[i]:
                        made = _race_pace(counters["pace"], p, s.dist, False)
                        counters["pace"] += 1
                    else:
                        made = _sub(counters["sub"], p, fitness.threshold_hr)
                        counters["sub"] += 1
                elif phase == "Taper" or is_race_week:
                    made = _race_pace(0, p, s.dist, True)
                elif phase == "Peak" and n == 0:
                    made = _race_pace(counters["pace"], p, s.dist, False)
                    counters["pace"] += 1
                elif phase == "Build" and s.dist in ("5k", "10k") and (n == 1 or (q_now == 1 and i % 2)):
                    made = _intervals(counters["intervals"], p)
                    counters["intervals"] += 1
                elif phase == "Build" and q_now == 2 and n == 1:
                    made = _intervals(counters["intervals"], p)
                    counters["intervals"] += 1
                else:
                    made = _tempo(counters.get(phase, 0), p, phase)
                    counters[phase] = counters.get(phase, 0) + 1
                add(day, *made, "hard")
            # Easy runs fill what's left of the week, at least 3 km each.
            easy_dates = [d for d in week_days if d not in plan_days and not (is_race_week and s.race and d > s.race.day)]
            if is_race_week and s.race:
                easy_dates = [d for d in easy_dates if d != s.race.day - timedelta(days=1)] or easy_dates
            left = target - sum(v["km"] for v in plan_days.values())
            if easy_dates:
                strides_on = None
                if not gentle_now and style != "faster" and phase in ("Base", "Build") and not recovery[i]:
                    strides_on = easy_dates[len(easy_dates) // 2]
                elif gentle_now and phase != "Taper":
                    strides_on = easy_dates[-1]
                if strides_on:
                    left -= _km_of([_reps(6, {"duration_seconds": 20}, 60)], p)
                # Whole kilometres that add up to the week, the longer ones first.
                n = len(easy_dates)
                total = max(MIN_RUN_KM * n, round(left))
                each = [total // n + (1 if k < total % n else 0) for k in range(n)]
                if is_race_week:
                    each = [max(MIN_RUN_KM, min(x, 5)) for x in each]
                for d, km in zip(easy_dates, each):
                    add(d, *_easy(km, p, strides=d == strides_on), "easy")
            over = sum(v["km"] for v in plan_days.values()) > target * 1.1 + 1
            if not over or q_now == 0 or is_race_week or b_race:
                break
        rows = [plan_days[d] for d in sorted(plan_days)]
        week_km = round(sum(r["km"] for r in rows))
        long_row = next((r for r in rows if r["kind"] == "long"), None)
        long_top = max(long_top, long_row["km"] if long_row else 0)
        weeks.append({"n": i + 1, "starts": monday, "phase": phase, "km": week_km,
                      "recovery": recovery[i], "rows": rows, "b_race": b_race,
                      "a_race": s.race if is_race_week else None})
        for r in rows:
            sessions.append({"date": r["date"].isoformat(), "name": r["name"], "phase": phase,
                             "description": r["description"] + (" Recovery week." if recovery[i] else ""),
                             "steps": r["steps"]})
    return Built(s, weeks, sessions, style, why, max(w["km"] for w in weeks), long_top, p)


# -- what the preview shows -----------------------------------------------------------------

def _d(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def _dow(day: date) -> str:
    return f"{DAY_NAMES[day.weekday()][:3]} {day.day} {MONTHS[day.month - 1]}"


def _join(words: Sequence[str]) -> str:
    return " and ".join(words) if len(words) <= 2 else ", ".join(words[:-1]) + " and " + words[-1]


def session_row(r: Mapping[str, Any]) -> dict[str, Any]:
    kind = r["kind"]
    return {"date": r["date"].isoformat(), "kind": kind, "name": r["name"], "km": round(r["km"]),
            "tier": r["race"].tier if r.get("race") else None}


def preview(a: Answers, fitness: Fitness, today: date, starts: date | None = None,
            usual_days: Sequence[int] = (), parkrun_day: int | None = None) -> dict[str, Any]:
    """Everything step 7 draws, from the generated plan itself."""
    b = build(a, fitness, today, starts, usual_days)
    s = b.shape
    weeks = []
    for w in b.weeks:
        rows = w["rows"]
        race = next((r for r in rows if r["kind"] == "race"), None)
        hard = next((r for r in rows if r["kind"] == "hard"), None)
        long = next((r for r in rows if r["kind"] == "long"), None)
        easy = [r for r in rows if r["kind"] == "easy"]
        cards = []
        if race:
            cards.append({"kind": "race", "type": f"Race · {race['race'].tier}", "name": race["race"].name, "km": round(race["km"])})
        for h in [r for r in rows if r["kind"] == "hard"] if not race else []:
            cards.append({"kind": "hard", "type": "Harder", "name": _plain(h["name"]), "km": round(h["km"])})
        if long:
            cards.append({"kind": "long", "type": "Long", "name": _plain(long["name"]), "km": round(long["km"])})
        others = [r for r in rows if r is not race and r is not long and (race or r["kind"] != "hard")]
        if others:
            km = round(sum(r["km"] for r in others))
            if w["a_race"]:
                name = "Short easy runs, legs fresh"
            else:
                n = len(easy)
                name = f"{n} easy run{'s' if n != 1 else ''}" + (" and more" if len(others) > n else "")
                if n == 0:
                    name = "Other sessions"
            cards.append({"kind": "easy", "type": "Easy", "name": name, "km": km})
        sunday = w["starts"] + timedelta(days=6)
        weeks.append({
            "n": w["n"], "starts": w["starts"].isoformat(), "phase": w["phase"], "km": w["km"],
            "dates": f"{_d(w['starts'])} – {_d(sunday)}", "month": MONTHS[w["starts"].month - 1].upper(),
            "race": "A" if w["a_race"] else "B" if w["b_race"] else None,
            "recovery": w["recovery"] or None, "sessions": cards,
        })
    peak_i = max(range(len(weeks)), key=lambda i: (weeks[i]["km"], -i))
    end = s.race.day if s.race else s.start + timedelta(weeks=s.weeks) - timedelta(days=1)
    title = ("Your ultra plan" if s.km > ULTRA_KM else
             {"5k": "Your 5K plan", "10k": "Your 10K plan", "half": "Your half plan", "marathon": "Your marathon plan"}[s.dist]
             if s.kind in ("race", "faster") else "Your base plan" if s.kind == "base" else "Your fitness plan")
    return {
        "title": title,
        "sub": f"{s.weeks} weeks · {_d(s.start)} to {_d(end)} · {a.days} days a week",
        "weeks": weeks, "peak_week": peak_i + 1, "peak_km": weeks[peak_i]["km"], "start_km": weeks[0]["km"],
        "now_km": round(a.avg_km), "style": b.style, "style_why": b.style_why,
        "count": len(b.sessions), "weeks_n": s.weeks,
        "first": _first_line(s.start, b.weeks[0]["rows"][0]) if b.weeks and b.weeks[0]["rows"] else "",
        "whys": whys(a, b, fitness),
        "checks": checks(a, b, fitness, parkrun_day),
        "phases": [{"label": ph.upper(), "weeks": len(list(g))} for ph, g in itertools.groupby(s.phases)],
    }


def _first_line(start: date, row: Mapping[str, Any]) -> str:
    what = {"easy": f"an easy {round(row['km'])} km", "long": f"a long run of {round(row['km'])} km",
            "race": row["name"].replace("Race: ", "")}.get(row["kind"], _plain(row["name"]))
    if row["date"] == start:
        return f"Week 1 starts {_dow(start)} with {what}."
    return f"Week 1 starts {_dow(start)}, with {what} on {DAY_NAMES[row['date'].weekday()]}."


def _plain(name: str) -> str:
    """A session's watch name as the preview says it: "Long 16k" is "Long run"."""
    if name.startswith("Long "):
        mp = re.search(r"w (\d+)k MP", name)
        return f"Long run, last {mp.group(1)} km at marathon pace" if mp else "Long run"
    return re.sub(r"(\d+)x(\d+)min", r"\1 × \2 min", re.sub(r"(\d+)x(\d+(?:\.\d+)?)(k|m)\b",
                  lambda m: f"{m.group(1)} × {m.group(2)} {'km' if m.group(3) == 'k' else 'm'}", name))


def whys(a: Answers, b: Built, fitness: Fitness) -> list[dict[str, str]]:
    s = b.shape
    out: list[dict[str, str]] = []
    bs = [w for w in b.weeks if w["b_race"]]
    if s.race:
        text = f"{s.race.name} is the goal."
        if bs:
            r = bs[0]["b_race"]
            text += f" The {r.name} in week {bs[0]['n']} gets a short taper, not a full one."
        out.append({"k": "RACES", "t": text, "step": "2"})
        verdict = goal_verdict(goal_secs(a.goal_time, s.dist), s.dist, s.km, fitness)
        if not a.goal_time:
            out.append({"k": "GOAL TIME", "t": "No goal time, so sessions are paced from Garmin's predictions." if b.paces.known
                        else "No goal time, and no paces from Garmin yet, so sessions go by effort.", "step": "2"})
        elif verdict:
            said = clock(fitness.predicted(s.dist, s.km) or 0)
            out.append({"k": "GOAL TIME", "t": f"{a.goal_time} is {verdict['chip'].lower()}: "
                        + ("it matches Garmin's prediction." if verdict["kind"] == "realistic"
                           else f"quicker than Garmin's {said} prediction."), "step": "2"})
    else:
        goal = {"faster": f"a faster {DISTANCE_NAMES[s.dist]}", "base": "a base for a race plan later", "fit": "general fitness"}[s.kind]
        out.append({"k": "GOAL", "t": f"No race, so it builds {goal} over {s.weeks} weeks.", "step": "2"})
    off = [DAY_NAMES[d] for d in a.out]
    out.append({"k": "DAYS", "t": f"{a.days} days a week, long run on {DAY_NAMES[a.long_day]}, "
                f"{_join(off) + ' off' if off else 'no day off'}.", "step": "3"})
    rate = "5" if a.gentle else "10"
    peak_w = max(b.weeks, key=lambda w: w["km"])
    if a.amount == "keep":
        much = f"Stays around {round(a.avg_km)} km a week, with an easier week every {_nth(cycle(a))}."
    else:
        much = (f"Peaks at {peak_w['km']} km in week {peak_w['n']}: about {rate}% a week from your "
                f"{round(a.avg_km)} km, with an easier week every {_nth(cycle(a))}.")
    out.append({"k": "HOW MUCH", "t": much, "step": "4"})
    top = round(b.long_top)
    full = max((w["km"] for w in b.weeks if not w["a_race"]), default=b.peak_km)
    share = round(long_share(a.days) * 100)
    if top >= LONG_CAP_KM[s.dist] - 1:
        long_words = f"Long run tops out at {top} km: enough for a {DISTANCE_NAMES[s.dist]}, in 2 km steps from your {round(a.longest_km)} km."
    elif top < round(a.longest_km):
        long_words = f"Long run stays around {top} km, about {share}% of a {full} km week, so one run isn't most of it."
    else:
        long_words = f"Long run tops out at {top} km: about {share}% of a {full} km week, in 2 km steps from your {round(a.longest_km)} km."
    out.append({"k": "LONG RUN", "t": long_words, "step": "4"})
    out.append({"k": "INJURIES", "t": "None, so the build starts straight away." if not a.gentle
                else "Easy only for the first 3 weeks, then a gentler build.", "step": "5"})
    name = STYLES[b.style][0]
    hard = max((quality_count(b.style, a.level, a.days, ph) for ph in ("Build", "Peak")), default=1)
    if b.style == "steady":
        style = "Steady: one harder day a week, the rest easy."
    elif b.style == "faster":
        style = f"Faster: {hard} threshold sessions a week, kept under {fitness.threshold_hr - 5} bpm."
    else:
        src = fitness.source()
        style = (f"{name}: {'two harder days' if hard >= 2 else 'one harder day'} a week"
                 + (f", paced from your {src}." if src else ", by effort."))
    out.append({"k": "STYLE", "t": style, "step": "6"})
    return out


def checks(a: Answers, b: Built, fitness: Fitness, parkrun_day: int | None = None) -> list[str]:
    """Worth a look: plain notes on the plan as built. They never block it."""
    found: list[str] = []
    weeks = b.weeks
    for k, (prev, w) in enumerate(zip(weeks, weeks[1:])):
        # From an easier week, the step is from the last full one.
        before = max([x["km"] for x in weeks[: k + 1] if not x["recovery"] and not x["b_race"]] or [max(prev["km"], a.avg_km)])
        base_km = before if prev["recovery"] or prev["b_race"] else prev["km"]
        up = w["km"] - base_km
        if w["phase"] != "Taper" and up > max(5, base_km * 0.2):
            after = f", straight after the {prev['b_race'].name}" if prev["b_race"] else ""
            found.append(f"Week {w['n']} is {w['km'] - prev['km']} km up on week {prev['n']}{after}. "
                         f"If your legs feel heavy, run it as week {max(1, prev['n'] - 1)} again.")
            break
    longs = [(w, next((r for r in w["rows"] if r["kind"] == "long"), None)) for w in weeks]
    longs = [(w, r) for w, r in longs if r]
    for (pw, pr), (w, r) in zip(longs, longs[1:]):
        if r["km"] - pr["km"] > 3 and w["n"] == pw["n"] + 1 and not pw["recovery"]:
            found.append(f"The long run goes from {round(pr['km'])} km to {round(r['km'])} km in week {w['n']}. "
                         "Steps of 2 km a week are gentler on the legs.")
            break
    s = b.shape
    if s.km > ULTRA_KM:
        found.append("Ultras are planned as a marathon build for now, so the long run stops at 32 km or 3 hours. "
                     "Back-to-back long runs and time on your feet aren't in it yet.")
    elif s.dist == "marathon" and b.long_top < 28:
        found.append(f"The long run tops out at {round(b.long_top)} km, short of the 28 to 32 km a marathon usually needs. "
                     "More weeks or more running would let it go further.")
    if s.race and s.weeks < 6:
        found.append(f"{s.weeks} weeks is short for a full build: this plan sharpens what you have, then tapers.")
    verdict = goal_verdict(goal_secs(a.goal_time, s.dist), s.dist, s.km, fitness) if s.race else None
    if verdict and verdict["kind"] == "ambitious":
        found.append(f"{a.goal_time} is well under your prediction. Sessions are paced from your current fitness, "
                     "and the next race result will show if it's in reach.")
    if parkrun_day is not None and (parkrun_day + 1) % 7 == a.long_day:
        found.append(f"parkrun on {DAY_NAMES[parkrun_day]} then a long run on {DAY_NAMES[a.long_day]}: "
                     "keep parkrun easy in the peak weeks.")
    if not b.paces.known:
        found.append("Garmin has no race predictions for you yet, so sessions go by effort and heart-rate zone.")
    return found[:3]


def create_args(a: Answers, fitness: Fitness, today: date, starts: date | None = None,
                usual_days: Sequence[int] = ()) -> dict[str, Any]:
    """create_plan's arguments for the plan the preview showed."""
    b = build(a, fitness, today, starts, usual_days)
    s = b.shape
    style, based = STYLES[b.style]
    if s.race:
        dist_word = "Ultra" if s.km > ULTRA_KM else {"5k": "5K", "10k": "10K", "half": "Half marathon", "marathon": "Marathon"}[s.dist]
        goal = ", ".join(x for x in (s.race.name if s.race.name.lower() != dist_word.lower() else dist_word,
                                     a.goal_time, f"{s.race.day.day} {MONTHS_LONG[s.race.day.month - 1]}") if x)
        name = "Ultra build" if s.km > ULTRA_KM else {"5k": "5K build", "10k": "10K build", "half": "Half build", "marathon": "Marathon build"}[s.dist]
    else:
        goal = {"faster": f"Faster {DISTANCE_NAMES[s.dist]}", "base": "Base building", "fit": "General fitness"}[s.kind]
        name = {"faster": f"{DISTANCE_NAMES[s.dist].capitalize() if s.dist in ('half', 'marathon') else DISTANCE_NAMES[s.dist]} block",
                "base": "Base block", "fit": "Fitness block"}[s.kind]
    tiers = {"A": "goal", "B": "key", "C": "tune"}
    end = s.race.day if s.race else None
    marked = [r for r in a.races if s.start <= r.day and (end is None or r.day <= end)]
    marked.sort(key=lambda r: ({"A": 0, "B": 1, "C": 2}[r.tier if r is s.race or r.tier != "A" else "B"], r.day))
    milestones = []
    for r in marked[:4]:
        tier = "A" if r is s.race else ("B" if r.tier == "A" else r.tier)
        milestones.append({"label": r.name[:40], "date": r.day.isoformat(), "tier": tiers[tier],
                           **({"target": a.goal_time} if r is s.race and a.goal_time else {})})
    milestones.sort(key=lambda m: m["date"])
    return {"goal": goal, "name": name, "method": f"Daash plan, {style} ({based})",
            "milestones": milestones or None, "sessions": b.sessions}
