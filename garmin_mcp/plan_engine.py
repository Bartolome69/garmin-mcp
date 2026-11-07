"""Running plans built by rules, for the app's plan builder (docs/plan-engine-spec.md).

No model calls: the same answers always give the same plan. What it returns is
exactly create_plan's arguments, so a plan built here is an ordinary Daash plan
on the Garmin calendar, read back by get_plan, the cards and the app like any
other. v1 is the spec's first slice: running only, one goal race (or general
fitness), the Steady and Balanced styles, 6 to 20 weeks, 3 to 6 days a week.

The numbers are Daash's own conservative defaults (spec section 3), inside the
looser limits plan_checks warns at, so a plan built here doesn't trip its own
validator.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping

GOALS = {
    # key: (distance km, label, block name, words)
    "5k": (5.0, "5K", "5K build", "5K"),
    "10k": (10.0, "10K", "10K build", "10K"),
    "half": (21.0975, "HM", "Half build", "Half marathon"),
    "marathon": (42.195, "MAR", "Marathon build", "Marathon"),
    "fitness": (0.0, "FIT", "Fitness block", "General fitness"),
}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SHORT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]

MIN_WEEKS, MAX_WEEKS = 6, 20
FITNESS_WEEKS = 8
# Taper weeks, and each one's share of the biggest week (spec section 3).
TAPER = {"5k": [0.6], "10k": [0.6], "half": [0.7, 0.5], "marathon": [0.75, 0.6, 0.4]}
SPLIT = {"5k": (0.4, 0.4, 0.2), "10k": (0.4, 0.4, 0.2), "half": (0.4, 0.35, 0.25),
         "marathon": (0.35, 0.4, 0.25), "fitness": (0.5, 0.5, 0.0)}
LONG_CAP_KM = {"5k": 12.0, "10k": 16.0, "half": 20.0, "marathon": 32.0, "fitness": 14.0}
# The most a week builds to, by goal: the "Build more" ceiling.
PEAK_CAP_KM = {"5k": 55.0, "10k": 65.0, "half": 75.0, "marathon": 95.0, "fitness": 45.0}
NEW_PEAK_CAP_KM = {"5k": 30.0, "10k": 35.0, "half": 45.0, "marathon": 55.0, "fitness": 30.0}
START_FLOOR_KM = 10.0      # the smallest week a plan starts from
EASY_MIN_KM = 3.0
LONG_MIN_KM = 4.0
# The builder's share of fast running in a week, inside plan_checks' warnings.
HARD_SHARE = {"steady": 0.13, "balanced": 0.18}
STRIDES = 6
RECOVERY_SHARE = 0.75
# A pace window wide enough that the watch doesn't beep at GPS wobble.
WINDOW = {"intervals": 5, "threshold": 10, "race": 15}
# When Garmin has no paces: rough ones to size sessions by, never shown as targets.
ASSUMED = {"easy": 390.0, "threshold": 330.0, "intervals": 300.0}


class BuildError(ValueError):
    """An answer the engine can't build from; the text is for the person."""


@dataclass
class Answers:
    goal: str
    today: date
    race_date: date | None = None
    race_name: str | None = None
    target: str | None = None          # "1:45:00", "48:30", for the goal race
    days: int = 4
    long_day: int = 6                  # Monday 0 .. Sunday 6
    days_out: list[int] = field(default_factory=list)
    weekly_km: float = 0.0             # the last 4 weeks, or what they said
    longest_km: float = 0.0
    level: str = "regular"             # new, regular, experienced
    volume: str = "build"              # keep, build, more
    injury: str = "none"               # none, niggle, comeback, hurts
    style: str = "pick"                # steady, balanced, pick
    predictions: dict[str, float] = field(default_factory=dict)  # 5k/10k/half/marathon: seconds
    threshold_pace: float | None = None  # seconds per km
    weeks: int | None = None           # general fitness only

    @classmethod
    def from_json(cls, body: Mapping[str, Any], today: date) -> "Answers":
        def num(key: str, default: float = 0.0) -> float:
            try:
                return max(0.0, float(body.get(key) or default))
            except (TypeError, ValueError):
                return default

        goal = str(body.get("goal") or "").lower()
        if goal not in GOALS:
            raise BuildError("Pick a race distance, or general fitness.")
        race = body.get("race_date")
        try:
            race_day = date.fromisoformat(str(race)[:10]) if race else None
        except ValueError:
            raise BuildError("That race date doesn't look right.") from None
        preds = {}
        for k, v in (body.get("predictions") or {}).items():
            secs = _seconds(v)
            if k in ("5k", "10k", "half", "marathon") and secs:
                preds[k] = secs
        out = sorted({int(d) for d in body.get("days_out") or [] if str(d).isdigit() and 0 <= int(d) <= 6})
        return cls(
            goal=goal, today=today, race_date=race_day,
            race_name=(str(body.get("race_name") or "").strip()[:40] or None),
            target=(str(body.get("target") or "").strip() or None),
            days=int(min(6, max(3, num("days", 4)))),
            long_day=int(body.get("long_day")) if str(body.get("long_day", "")).isdigit() and 0 <= int(body.get("long_day")) <= 6 else 6,
            days_out=out, weekly_km=num("weekly_km"), longest_km=num("longest_km"),
            level=body.get("level") if body.get("level") in ("new", "regular", "experienced") else "regular",
            volume=body.get("volume") if body.get("volume") in ("keep", "build", "more") else "build",
            injury=body.get("injury") if body.get("injury") in ("none", "niggle", "comeback", "hurts") else "none",
            style=body.get("style") if body.get("style") in ("steady", "balanced", "pick") else "pick",
            predictions=preds, threshold_pace=_seconds(body.get("threshold_pace")),
            weeks=int(num("weeks", FITNESS_WEEKS)) if body.get("weeks") else None,
        )


def _seconds(value: Any) -> float | None:
    """'1:45:00', '4:18', 6300 or '6300' as seconds."""
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value or "").strip().lower()
    if not text:
        return None
    # Garmin's predictions as Daash words them: "1h 25m 00s", "18m 00s".
    units = re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", text)
    if units and not ":" in text:
        secs = sum(float(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in units)
        return secs if secs > 0 else None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs if secs > 0 else None


def _pace(secs: float) -> str:
    secs = int(round(secs))
    return f"{secs // 60}:{secs % 60:02d}"


def _clock(secs: float) -> str:
    secs = int(round(secs))
    h, rest = divmod(secs, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


def _km(value: float) -> str:
    return f"{value:g}"


def _half(value: float) -> float:
    return round(value * 2) / 2


def _riegel(secs: float, d1: float, d2: float) -> float:
    return secs * (d2 / d1) ** 1.06


# --------------------------------------------------------------------------
# Paces (spec section 6)
# --------------------------------------------------------------------------

@dataclass
class Paces:
    known: bool
    easy: float          # s/km, the middle of the easy range
    marathon: float | None
    half: float | None
    threshold: float
    intervals: float     # 5K pace
    race: float | None   # the goal race's pace
    note: str | None = None

    def window(self, secs: float, kind: str) -> list[str]:
        w = WINDOW[kind]
        return [_pace(secs - w), _pace(secs + w)]


def paces(a: Answers) -> Paces:
    p = dict(a.predictions)
    dist = {"5k": 5.0, "10k": 10.0, "half": 21.0975, "marathon": 42.195}
    if not p:
        # A target time alone still gives race pace, nothing else.
        race = (_seconds(a.target) / dist[a.goal]) if a.target and a.goal in dist and _seconds(a.target) else None
        return Paces(False, ASSUMED["easy"], None, None, ASSUMED["threshold"], ASSUMED["intervals"], race)
    # Fill the distances Garmin didn't predict from the nearest one it did.
    for k in dist:
        if k not in p:
            src = min(p, key=lambda s: abs(math.log(dist[s] / dist[k])))
            p[k] = _riegel(p[src], dist[src], dist[k])
    # The marathon prediction only for runners already near 40 km a week;
    # otherwise from the half, slower and safer.
    mar_s = p["marathon"] if a.weekly_km >= 40 else _riegel(p["half"], dist["half"], dist["marathon"])
    marathon = mar_s / dist["marathon"]
    half = p["half"] / dist["half"]
    ten = p["10k"] / 10.0
    threshold = a.threshold_pace or (ten + half) / 2
    intervals = p["5k"] / 5.0
    easy = marathon + 60
    race, note = None, None
    if a.goal in dist:
        predicted = {"5k": intervals, "10k": ten, "half": half, "marathon": marathon}[a.goal]
        race = predicted
        goal_s = _seconds(a.target) if a.target else None
        if goal_s:
            wanted = goal_s / dist[a.goal]
            if wanted >= predicted * 0.97:
                race = wanted
            else:
                note = (f"Your goal of {_clock(goal_s)} is quicker than your current fitness suggests "
                        f"(about {_clock(predicted * dist[a.goal])}). The plan trains you from where you are; "
                        "rebuild it in a few weeks as you get fitter.")
    return Paces(True, easy, marathon, half, threshold, intervals, race, note)


# --------------------------------------------------------------------------
# The shape of the block (spec section 3)
# --------------------------------------------------------------------------

@dataclass
class Week:
    n: int
    monday: date
    phase: str
    km: float
    recovery: bool = False
    taper: bool = False
    race_week: bool = False


def _style(a: Answers) -> tuple[str, str]:
    """The style, and one line of why."""
    if a.level == "new" or a.injury in ("niggle", "comeback"):
        return "steady", ("Steady, since you're new or coming back: mostly easy running, "
                          "and one harder session a week once the build starts.")
    if a.style == "steady":
        return "steady", "Steady: mostly easy running, and one harder session a week once the build starts."
    return "balanced", "Balanced: threshold, intervals or race pace, changing as the block goes on."


def _taper(goal: str, weeks: int) -> list[float]:
    """Each taper week's share of the biggest week, the race week last."""
    if goal == "fitness":
        return []
    if goal == "marathon" and weeks < 16:
        return [0.65, 0.4]
    return TAPER[goal]


def _phase_counts(goal: str, weeks: int) -> tuple[int, int, int, int]:
    """Weeks of base, build, peak and taper. Short plans lose base first; taper is never cut."""
    taper = len(_taper(goal, weeks))
    rest = weeks - taper
    _, u, pk = SPLIT[goal]
    peak = max(1, round(rest * pk)) if goal != "fitness" else 0
    build = max(1, round(rest * u))
    base = max(0, rest - build - peak)
    return base, build, peak, taper


def _every(a: Answers) -> int:
    return 3 if (a.level == "new" or a.injury in ("niggle", "comeback")) else 4


def _step(a: Answers, km: float) -> float:
    gentle = a.injury in ("niggle", "comeback")
    return max(km * (0.05 if gentle else 0.10), 2.0 if gentle else 3.0)


def peak_options(a: Answers, weeks: int | None = None) -> dict[str, float]:
    """The three volumes step 4 offers: keep what they run, build steadily, or build
    as far as the progression rules reach in the weeks there are, capped by goal."""
    start = max(START_FLOOR_KM, a.weekly_km)
    weeks = weeks or _weeks(a)[1]
    building = weeks - len(_taper(a.goal, weeks))
    km = start
    for i in range(1, building):
        if (i + 1) % _every(a) == 0:
            continue
        km += _step(a, km)
    cap = (NEW_PEAK_CAP_KM if a.level == "new" else PEAK_CAP_KM)[a.goal]
    more = max(start, min(km, cap))
    build = start + (more - start) * 0.6
    return {"keep": float(round(start)), "build": float(round(build)), "more": float(round(more))}


def _weeks(a: Answers) -> tuple[date, int, list[str]]:
    """The first Monday, how many weeks, and any note about the length."""
    notes = []
    start = _monday(a.today) if a.today.weekday() <= 2 else _monday(a.today) + timedelta(weeks=1)
    if a.goal == "fitness":
        weeks = int(min(MAX_WEEKS, max(MIN_WEEKS, a.weeks or FITNESS_WEEKS)))
        return start, weeks, notes
    if not a.race_date:
        raise BuildError("Pick the race date.")
    if a.race_date <= a.today:
        raise BuildError("The race date has to be after today.")
    weeks = (_monday(a.race_date) - start).days // 7 + 1
    if weeks < MIN_WEEKS:
        raise BuildError(
            f"The race is {weeks} week{'s' if weeks != 1 else ''} away, too close for a plan. "
            "Run easy, rest the last few days, and enjoy it.")
    if weeks > MAX_WEEKS:
        start = _monday(a.race_date) - timedelta(weeks=MAX_WEEKS - 1)
        notes.append(f"The race is {weeks} weeks away, so the plan starts {MAX_WEEKS} weeks out, "
                     f"on {start.day} {MONTHS[start.month - 1]}. Run as you do now until then.")
        weeks = MAX_WEEKS
    return start, weeks, notes


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def block(a: Answers) -> list[Week]:
    """Every week of the block: its phase and how far it runs."""
    start, weeks, _ = _weeks(a)
    base, build, peak, taper = _phase_counts(a.goal, weeks)
    shares = _taper(a.goal, weeks)
    names = ["Base"] * base + ["Build"] * build + ["Peak"] * peak + ["Taper"] * taper
    target = peak_options(a, weeks)[a.volume]
    begin = max(START_FLOOR_KM, a.weekly_km)
    out: list[Week] = []
    last_full = biggest = begin
    for i, phase in enumerate(names):
        monday = start + timedelta(weeks=i)
        if phase == "Taper":
            share = shares[i - (weeks - taper)]
            out.append(Week(i + 1, monday, phase, _half(biggest * share), taper=True, race_week=(i == weeks - 1)))
            continue
        if i > 0 and (i + 1) % _every(a) == 0 and i < weeks - taper - 1:
            # A lighter week; the next goes back to the last full week, not above it.
            out.append(Week(i + 1, monday, phase, _half(last_full * RECOVERY_SHARE), recovery=True))
            continue
        if i > 0 and not out[-1].recovery:
            last_full = min(max(target, last_full), last_full + _step(a, last_full))
        biggest = max(biggest, last_full)
        out.append(Week(i + 1, monday, phase, _half(last_full)))
    return out


# --------------------------------------------------------------------------
# Days of the week
# --------------------------------------------------------------------------

def _run_days(a: Answers) -> tuple[list[int], int]:
    """Which weekdays carry a run, and the long run's day."""
    free = [d for d in range(7) if d not in a.days_out]
    long_day = a.long_day if a.long_day in free else (6 if 6 in free else (free[-1] if free else 6))
    want = min(a.days, 5 if a.level == "new" else 6, len(free))
    if want < 3:
        raise BuildError("Leave at least 3 days free to run.")

    def gaps(days: tuple[int, ...]) -> tuple[int, int]:
        s = sorted(days)
        touching = sum(1 for x, y in zip(s, s[1:] + [s[0] + 7]) if (y - x) % 7 == 1)
        spread = min((y - x) % 7 or 7 for x, y in zip(s, s[1:] + [s[0] + 7]))
        return touching, -spread

    best = min((c for c in itertools.combinations(free, want) if long_day in c), key=lambda c: (gaps(c), c))
    return sorted(best), long_day


def _quality_days(run_days: list[int], long_day: int, count: int) -> list[int]:
    """Days for the harder sessions: never next to the long run or to each other."""
    if count <= 0:
        return []

    def apart(x: int, y: int) -> int:
        return min((x - y) % 7, (y - x) % 7)

    options = [d for d in run_days if d != long_day]
    fits = []
    for c in itertools.combinations(options, min(count, len(options))):
        days = list(c) + [long_day]
        nearest = min(apart(x, y) for x, y in itertools.combinations(days, 2))
        if nearest >= 2:
            fits.append((-nearest, c))
    if fits:
        return sorted(min(fits)[1])
    return _quality_days(run_days, long_day, count - 1) if count > 1 else []


def _quality_count(a: Answers, style: str, week: Week) -> int:
    gentle = a.injury in ("niggle", "comeback") and week.n <= 3
    if week.recovery or gentle:
        return 0
    if week.taper:
        return 1
    if style == "steady":
        return 0 if week.phase == "Base" else 1
    days = min(a.days, 5 if a.level == "new" else 6)
    if a.level == "new":
        return 0 if week.phase == "Base" or days == 3 else 1
    if days <= 4:
        return 1
    if days == 5:
        return 2 if a.level == "experienced" or week.phase in ("Build", "Peak") else 1
    return 2


# --------------------------------------------------------------------------
# Sessions (spec section 9's step shapes)
# --------------------------------------------------------------------------

@dataclass
class Session:
    day: date
    name: str
    km: float
    steps: list[dict[str, Any]]
    description: str
    kind: str          # easy, long, threshold, intervals, race
    work_km: float = 0.0


def _easy(day: date, km: float, p: Paces, strides: bool = False) -> Session:
    km = max(EASY_MIN_KM, _half(km))
    steps: list[dict[str, Any]] = [{"type": "interval", "distance_meters": int(km * 1000)}]
    words = (f"Easy, you could chat: about {_pace(p.easy - 15)}-{_pace(p.easy + 15)} /km."
             if p.known else "Easy, you could chat the whole way.")
    if strides:
        steps.append({"type": "repeat", "times": STRIDES, "steps": [
            {"type": "interval", "duration_seconds": 20},
            {"type": "recovery", "until_lap": True}]})
        words += f" Then {STRIDES} strides: 20 seconds quick and relaxed, walk back, press lap for the next."
    return Session(day, f"Easy {_km(km)}k" + (" + strides" if strides else ""), km, steps, words, "easy")


def _long(day: date, km: float, p: Paces, mp_km: float = 0.0) -> Session:
    km = _half(km)
    if mp_km and p.marathon:
        mp_km = _half(min(mp_km, km - 4))
        steps = [{"type": "interval", "distance_meters": int((km - mp_km) * 1000)},
                 {"type": "interval", "distance_meters": int(mp_km * 1000), "pace": p.window(p.marathon, "race")}]
        made = Session(day, f"Long run {_km(km)}k w {_km(mp_km)}k MP", km, steps,
                       f"Easy for the first {_km(km - mp_km)} km, then {_km(mp_km)} km at marathon pace, "
                       f"about {_pace(p.marathon)} /km.", "long")
        made.work_km = mp_km
        return made
    words = (f"Easy and steady, about {_pace(p.easy - 15)}-{_pace(p.easy + 15)} /km. Take a drink if it's warm."
             if p.known else "Easy and steady, a pace you could keep talking at.")
    return Session(day, f"Long run {_km(km)}k", km, [{"type": "interval", "distance_meters": int(km * 1000)}],
                   words, "long")


def _warm(steps: list[dict[str, Any]], short: bool) -> list[dict[str, Any]]:
    warm, cool = (600, 300) if short else (900, 600)
    return [{"type": "warmup", "duration_seconds": warm}, *steps, {"type": "cooldown", "duration_seconds": cool}]


def _around_km(p: Paces, short: bool) -> float:
    """The warm-up and cool-down's distance, at easy pace."""
    return ((900 if short else 1500)) / p.easy


def _tempo(day: date, reps: int, minutes: int, p: Paces, short: bool, label: str = "Tempo") -> Session:
    target = {"pace": p.window(p.threshold, "threshold")} if p.known else {}
    work = {"type": "interval", "duration_seconds": minutes * 60, **target}
    steps = _warm([{"type": "repeat", "times": reps, "steps": [work, {"type": "recovery", "duration_seconds": 120}]}], short)
    km = _around_km(p, short) + (reps * minutes * 60) / p.threshold + (reps * 120) / (p.easy + 60)
    words = (f"Warm up and cool down easy. Each rep comfortably hard, about {_pace(p.threshold)} /km, "
             "2 minutes easy jog between." if p.known else
             "Warm up and cool down easy. Each rep comfortably hard: you could say a few words, not chat. "
             "2 minutes easy jog between.")
    s = Session(day, f"{label} {reps}x{minutes}min", _half(km), steps, words, "threshold")
    s.work_km = (reps * minutes * 60) / p.threshold
    return s


def _reps(day: date, reps: int, metres: int, p: Paces, short: bool, pace: float | None = None,
          label: str = "Intervals") -> Session:
    pace = pace or p.intervals
    target = {"pace": p.window(pace, "intervals")} if p.known or p.race else {}
    work = {"type": "interval", "distance_meters": metres, **target}
    steps = _warm([{"type": "repeat", "times": reps, "steps": [work, {"type": "recovery", "duration_seconds": 120}]}], short)
    km = _around_km(p, short) + reps * metres / 1000 + (reps * 120) / (p.easy + 60)
    words = (f"Warm up and cool down easy. Reps at about {_pace(pace)} /km, 2 minutes easy jog between."
             if (p.known or p.race) else
             "Warm up and cool down easy. Reps hard but controlled, the last as quick as the first. "
             "2 minutes easy jog between.")
    dist = f"{metres // 1000}k" if metres % 1000 == 0 else f"{metres}m"
    s = Session(day, f"{label} {reps}x{dist}", _half(km), steps, words,
                "threshold" if label == "Race pace" else "intervals")
    s.work_km = reps * metres / 1000
    return s


def _race(day: date, a: Answers) -> Session:
    km = GOALS[a.goal][0]
    title = a.race_name or GOALS[a.goal][3]
    words = "Race day. Start steady, settle into it, and finish strong. Enjoy it."
    if a.target:
        words += f" Aiming for {a.target}."
    return Session(day, f"Race: {title}", round(km, 1), [{"type": "interval", "distance_meters": int(km * 1000)}],
                   words, "race")


def _quality(a: Answers, week: Week, p: Paces, which: int, day: date, scale: float) -> Session:
    """The harder session for a week, by phase and goal (spec section 3), its reps
    scaled down to fit the week's share of fast running."""
    g, short = a.goal, week.km < 25

    def n(reps: int, least: int) -> int:
        return max(least, round(reps * scale))

    if week.taper:
        if g in ("5k", "10k"):
            return _reps(day, n(4, 3), 1000, p, short, pace=p.race, label="Race pace")
        if g == "half":
            return _reps(day, n(3, 2), 2000, p, short, pace=p.race, label="Race pace")
        return _tempo(day, n(2, 2), 10, p, short)
    if week.phase == "Base":
        return _tempo(day, n(2, 2), 8 + 2 * min(2, week.n // 3), p, short)
    if week.phase == "Build":
        if which == 1 and g in ("5k", "10k"):
            return _reps(day, n(5 + min(1, week.n // 6), 3), 800, p, short)
        return _tempo(day, n(3 if week.n % 2 else 4, 2), 8 if week.n % 2 else 6, p, short, label="Threshold")
    # Peak: race-specific.
    if g in ("5k", "10k") and which == 0:
        return _reps(day, n(5 if g == "5k" else 6, 3), 1000, p, short, pace=p.race if g == "10k" else p.intervals)
    if g == "half" and which == 0:
        return _reps(day, n(3, 2), 3000, p, short, pace=p.race, label="Race pace")
    return _tempo(day, n(3, 2), 10, p, short, label="Threshold")


def _long_km(a: Answers, week: Week, prev_long: float, share: float) -> float:
    cap = LONG_CAP_KM[a.goal]
    if week.recovery:
        return _half(max(LONG_MIN_KM, min(prev_long * 0.75, week.km * share)))
    if week.taper:
        return _half(max(LONG_MIN_KM, min(cap, week.km * share)))
    return _half(max(LONG_MIN_KM, min(cap, prev_long + 2.0, week.km * share)))


def _apart(x: date, y: date) -> int:
    return abs((x - y).days)


def sessions_for(a: Answers) -> tuple[list[Session], list[Week], Paces, str, str]:
    if a.injury == "hurts":
        raise BuildError("Something that hurts now needs a physio before a plan. Daash can't take it into account.")
    p = paces(a)
    style, why_style = _style(a)
    weeks = block(a)
    run_days, long_day = _run_days(a)
    share = 0.35 if len(run_days) == 3 else 0.30
    fast_share = HARD_SHARE[style]
    last_full_long = min(LONG_CAP_KM[a.goal], a.longest_km or max(LONG_MIN_KM, a.weekly_km * 0.3))
    out: list[Session] = []
    for w in weeks:
        dates = {d: w.monday + timedelta(days=d) for d in run_days}
        race_week = bool(w.race_week and a.race_date and a.goal != "fitness")
        long_km = _long_km(a, w, last_full_long, share)
        if not (w.recovery or w.taper):
            last_full_long = long_km
        week: list[Session] = []
        # The long run, or the race in its place.
        mp = 0.0
        if race_week:
            week.append(_race(a.race_date, a))
        else:
            if a.goal == "marathon" and w.phase == "Peak" and not w.recovery and style == "balanced" and p.marathon:
                mp = min(14.0, 6.0 + 2 * (w.n % 4))
            week.append(_long(dates[long_day], long_km, p, mp))
        budget = w.km * fast_share - (mp or 0.0)
        # A long run with marathon pace in it is one of the week's harder sessions.
        count = _quality_count(a, style, w) - (1 if mp else 0)
        for i, d in enumerate(_quality_days(run_days, long_day, count)):
            if race_week and _apart(dates[d], a.race_date) <= 2:
                continue
            for scale in (1.0, 0.8, 0.6, 0.4):
                made = _quality(a, w, p, i, dates[d], scale)
                if made.work_km <= budget:
                    break
            else:
                continue
            budget -= made.work_km
            week.append(made)
        used = {s.day for s in week}
        easy = [dates[d] for d in run_days if dates[d] not in used and d != long_day]
        if race_week:
            # Nothing in the 2 days before the race but a short shakeout the day before; nothing after.
            easy = [d for d in easy if d < a.race_date and (_apart(d, a.race_date) > 2 or _apart(d, a.race_date) == 1)]
        left = w.km - sum(s.km for s in week)
        # Fewer easy runs rather than runs too short to be worth it, on a light week.
        while len(easy) > 1 and left / len(easy) < EASY_MIN_KM:
            easy.remove(min(easy, key=lambda d: min((_apart(d, s.day) for s in week), default=7)))
        strides_on = None
        if (style == "steady" or not any(s.kind in ("threshold", "intervals") for s in week)) and not w.taper:
            # Strides on the easy run furthest from the long run keep the legs quick.
            if easy:
                strides_on = max(easy, key=lambda d: min((_apart(d, s.day) for s in week), default=7))
        for d in easy:
            km = left / len(easy)
            if race_week and _apart(d, a.race_date) == 1:
                km = EASY_MIN_KM
            week.append(_easy(d, km, p, strides=(d == strides_on)))
        for s in week:
            if w.recovery:
                s.description = "Recovery week: lighter, to let the training settle in. " + s.description
        out.extend(s for s in week if s.day >= a.today)
    out.sort(key=lambda s: s.day)
    return out, weeks, p, style, why_style


# --------------------------------------------------------------------------
# What create_plan takes, and the preview
# --------------------------------------------------------------------------

def _goal_words(a: Answers) -> str:
    words = GOALS[a.goal][3]
    if a.goal == "fitness":
        return words
    parts = [a.race_name or words]
    if a.target:
        parts.append(a.target)
    d = a.race_date
    parts.append(f"{d.day} {MONTHS[d.month - 1]}")
    return ", ".join(parts)


def built_line(a: Answers, style: str) -> str:
    bits = [f"{a.days} days", f"long run {SHORT_DAYS[a.long_day]}", a.level, style.capitalize(),
            {"keep": "keep volume", "build": "build steadily", "more": "build more"}[a.volume]]
    if a.injury in ("niggle", "comeback"):
        bits.append("gentle start")
    return "Built: " + ", ".join(bits)


METHOD = {"steady": "Daash plan, Steady (80/20 style)", "balanced": "Daash plan, Balanced (Daniels style)"}


def build(a: Answers) -> dict[str, Any]:
    """create_plan's arguments, plus the preview the app shows before anything is written."""
    sessions, weeks, p, style, why_style = sessions_for(a)
    if not sessions:
        raise BuildError("There's nothing left to schedule before the race.")
    phase_of = {w.monday: w.phase for w in weeks}
    line = built_line(a, style)
    plan_sessions = []
    for s in sessions:
        plan_sessions.append({
            "date": s.day.isoformat(), "name": s.name, "phase": phase_of.get(_monday(s.day)),
            "description": f"{s.description}\n\n{line}", "steps": s.steps,
        })
    milestones = []
    if a.goal != "fitness":
        milestones.append({k: v for k, v in {"label": a.race_name or GOALS[a.goal][3], "date": a.race_date.isoformat(),
                                             "target": a.target, "tier": "goal"}.items() if v})
    args = {"goal": _goal_words(a), "label": GOALS[a.goal][1], "name": GOALS[a.goal][2],
            "method": METHOD[style], "milestones": milestones or None, "sessions": plan_sessions}
    return {"plan": args, "preview": preview(a, sessions, weeks, p, why_style)}


def preview(a: Answers, sessions: list[Session], weeks: list[Week], p: Paces, why_style: str) -> dict[str, Any]:
    by_week: dict[date, list[Session]] = {}
    for s in sessions:
        by_week.setdefault(_monday(s.day), []).append(s)
    rows = []
    for w in weeks:
        mine = by_week.get(w.monday, [])
        hard = [s for s in mine if s.kind in ("threshold", "intervals", "race")]
        longest = max((s for s in mine if s.kind == "long"), key=lambda s: s.km, default=None)
        rows.append({k: v for k, v in {
            "week": w.n, "starts": w.monday.isoformat(), "phase": w.phase,
            "km": round(sum(s.km for s in mine) or w.km, 1),
            "long_km": longest.km if longest else None,
            "key": (hard[0].name if hard else (longest.name if longest else None)),
            "recovery": w.recovery or None, "race": any(s.kind == "race" for s in mine) or None,
            "sessions": len(mine),
        }.items() if v is not None})
    full = [r for r in rows if r.get("long_km")]
    top_long = max((r["long_km"] for r in full), default=0)
    top_week = max((r["km"] for r in rows), default=0)
    start_long = a.longest_km or 0
    reasons = [why_style]
    if top_long:
        enough = {"5k": "plenty for a 5K", "10k": "plenty for a 10K", "half": "enough for a half",
                  "marathon": "the usual top for a marathon" if top_long >= 30 else "short of the usual 30-32 km",
                  "fitness": "a comfortable long run"}[a.goal]
        frm = f", in 2 km steps from your {_km(start_long)} km" if start_long and top_long > start_long else ""
        reasons.append(f"Long run tops out at {_km(top_long)} km: {enough}{frm}.")
    first = rows[0]["km"] if rows else 0
    reasons.append(f"Weeks start at {_km(first)} km and build to {_km(top_week)} km, with a lighter week every "
                   f"{3 if (a.level == 'new' or a.injury in ('niggle', 'comeback')) else 4} weeks.")
    if a.goal != "fitness":
        reasons.append({"5k": "One week of taper.", "10k": "One week of taper.", "half": "Two lighter weeks into the race.",
                        "marathon": "A taper of the last weeks into the race."}[a.goal])
    if not p.known:
        reasons.append("Garmin has no race predictions for you yet, so sessions are set by effort, not pace.")
    notes = [n for n in [p.note] + _weeks(a)[2] if n]
    if a.goal == "marathon" and top_long < 30:
        notes.append("From where you are, the long run tops out short of the usual 30-32 km. "
                     "The plan builds as far as is safe in the weeks there are.")
    return {"weeks": rows, "reasons": reasons, "notes": notes, "sessions": len(sessions),
            "first": sessions[0].day.isoformat(), "last": sessions[-1].day.isoformat()}


def level_guess(weekly_km: float, weeks_running: int, longest_km: float) -> str:
    """New, regular or experienced, from the last weeks, shown in step 1 to correct."""
    if weeks_running < 2 or weekly_km < 10:
        return "new"
    if weekly_km >= 40 and weeks_running >= 4 and longest_km >= 15:
        return "experienced"
    return "regular"
