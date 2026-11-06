"""What the plan checker says, rule by rule, and what it leaves alone.

Each rule gets a plan that should set it off and a near miss that shouldn't.
The near misses matter as much: a checker that nags a sensible plan gets
ignored, and the realistic half-marathon block at the end must come back
with nothing to say.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from garmin_mcp import plan_checks, training_plan  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


TODAY = date(2026, 11, 5)
MONDAY = date(2026, 11, 9)
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


# --------------------------------------------------------------------------
# Plans as get_plan's weeks, for the rules one at a time
# --------------------------------------------------------------------------

def session(day: date, name: str, km: float, work: float = 0.0, kind: str = "run",
            minutes: int | None = None) -> dict:
    shape = [seg for seg in ({"k": "easy", "km": km - work}, {"k": "work", "km": work}) if seg["km"]]
    return {"date": day.isoformat(), "name": name, "kind": kind,
            "type": training_plan.session_type(name, "running" if kind == "run" else "strength_training"),
            "planned_km": km if kind == "run" else None, "planned_min": minutes, "shape": shape}


def weeks(*plan: list[tuple], start: date = MONDAY) -> list[dict]:
    """One list per week of (day, name, km[, work]) tuples."""
    out = []
    for n, items in enumerate(plan, start=1):
        monday = start + timedelta(weeks=n - 1)
        out.append({"week": n, "starts": monday.isoformat(), "sessions": [
            session(monday + timedelta(days=DAYS[d]), name, km, *rest) for d, name, km, *rest in items]})
    return out


def easy_week(easy: float, long: float, tempo: bool = True) -> list[tuple]:
    """A sensible four-day week: two easy runs, a tempo, a long run."""
    quality = ("thu", "Tempo 3x8min", 10.0, 4.8) if tempo else ("thu", "Easy 7k + strides", 7.0)
    return [("tue", f"Easy {easy:g}k", easy), quality, ("sat", f"Easy {easy:g}k", easy), ("sun", f"Long {long:g}k", long)]


def rules(found: list[dict]) -> list[str]:
    return [c["rule"] for c in found]


def run(plan: list[dict], **kwargs) -> list[dict]:
    return plan_checks.check_weeks(plan, **kwargs)


# --------------------------------------------------------------------------
# A whole plan as create_plan takes it
# --------------------------------------------------------------------------

def easy_steps(km: float) -> list[dict]:
    return [{"type": "interval", "distance_meters": round(km * 1000)}]


def strides_steps(km: float) -> list[dict]:
    return easy_steps(km) + [{"type": "repeat", "times": 6, "steps": [
        {"type": "interval", "duration_seconds": 20}, {"type": "recovery", "until_lap": True}]}]


def tempo_steps(reps: int, minutes: int = 8) -> list[dict]:
    return [{"type": "warmup", "duration_seconds": 900},
            {"type": "repeat", "times": reps, "steps": [
                {"type": "interval", "duration_seconds": minutes * 60, "pace": ["4:50", "5:10"]},
                {"type": "recovery", "duration_seconds": 120}]},
            {"type": "cooldown", "duration_seconds": 600}]


def half_plan() -> list[dict]:
    """Twelve weeks to a half marathon, four days a week, the way a careful
    coach would write it: cutbacks in weeks 4 and 8, long run 10 to 18 km,
    one quality session a week, a taper."""
    shape = [  # easy, quality, long, phase
        (6, "strides", 10, "Base"), (6, "strides", 11, "Base"), (7, "strides", 12, "Base"),
        (5, "strides", 9, "Base"),
        (7, 3, 13, "Build"), (8, 3, 14, "Build"), (8, 4, 15, "Build"), (6, "strides", 11, "Build"),
        (8, 4, 16, "Peak"), (8, 4, 18, "Peak"), (6, 2, 12, "Taper"),
    ]
    sessions = []
    for n, (easy, quality, long, phase) in enumerate(shape):
        monday = MONDAY + timedelta(weeks=n)
        sessions.append({"date": (monday + timedelta(days=1)).isoformat(), "name": f"Easy {easy}k",
                         "phase": phase, "steps": easy_steps(easy)})
        if quality == "strides":
            sessions.append({"date": (monday + timedelta(days=3)).isoformat(), "name": f"Easy {easy}k + strides",
                             "phase": phase, "steps": strides_steps(easy)})
        else:
            sessions.append({"date": (monday + timedelta(days=3)).isoformat(), "name": f"Tempo {quality}x8min",
                             "phase": phase, "steps": tempo_steps(quality)})
        sessions.append({"date": (monday + timedelta(days=5)).isoformat(), "name": f"Easy {easy}k",
                         "phase": phase, "steps": easy_steps(easy)})
        sessions.append({"date": (monday + timedelta(days=6)).isoformat(), "name": f"Long {long}k",
                         "phase": phase, "steps": easy_steps(long)})
    race_week = MONDAY + timedelta(weeks=11)
    sessions += [
        {"date": (race_week + timedelta(days=1)).isoformat(), "name": "Easy 5k", "phase": "Taper",
         "steps": easy_steps(5)},
        {"date": (race_week + timedelta(days=3)).isoformat(), "name": "Easy 5k + strides", "phase": "Taper",
         "steps": strides_steps(5)},
        {"date": (race_week + timedelta(days=6)).isoformat(), "name": "Race: Half marathon", "phase": "Taper",
         "steps": easy_steps(21.1)},
    ]
    return sessions


def prepared(sessions: list[dict], goal: str = "Half marathon, 1:45, 31 January"):
    return training_plan.prepare(goal, sessions, "HM", TODAY)[1]


HISTORY = [{"starts": "2026-10-12", "run_km": 26.0, "runs": 4}, {"starts": "2026-10-19", "run_km": 28.0, "runs": 4},
           {"starts": "2026-10-26", "run_km": 25.0, "runs": 3}, {"starts": "2026-11-02", "run_km": 29.0, "runs": 4}]


def main() -> int:
    print("\na realistic plan has nothing to say")
    half = prepared(half_plan())
    found = plan_checks.check_prepared(
        half, goal="Half marathon, 1:45, 31 January", method="Daniels",
        milestones=[{"label": "Half marathon", "date": "2027-01-31", "target": "1:45"}],
        history=HISTORY, longest_recent_km=12.0,
    )
    check("twelve careful weeks to a half: no checks", found == [], str(found))
    built = plan_checks.weeks_from_prepared(half)
    check("create_plan's sessions read as get_plan's weeks",
          len(built) == 12 and built[0]["starts"] == MONDAY.isoformat()
          and [s["type"] for s in built[4]["sessions"]] == ["easy", "threshold", "easy", "long"]
          and built[11]["sessions"][-1]["type"] == "race", str([s["type"] for s in built[4]["sessions"]]))
    check("each run has its planned distance", built[0]["sessions"][-1]["planned_km"] == 10.0,
          str(built[0]["sessions"][-1]))
    check("a tempo's reps are its fast running", 4.0 < plan_checks._work_km(built[4]["sessions"][1]) < 6.0,
          str(built[4]["sessions"][1]["shape"]))

    print("\nstarting week")
    low = [{"starts": h["starts"], "run_km": 20.0, "runs": 3} for h in HISTORY]
    hit = plan_checks.check_prepared(half, history=low, method="Daniels")
    check("a first week well over the last 4 weeks' best is worth a look",
          "start_volume" in rules(hit), str(rules(hit)))
    start = next(c for c in hit if c["rule"] == "start_volume")
    check("it says the two numbers and the week", "28.4 km" in start["text"] and "20 km" in start["text"]
          and start["week"] == 1, start["text"])
    check("within 120% of the best recent week: nothing",
          "start_volume" not in rules(plan_checks.check_prepared(half, history=HISTORY, method="Daniels")))
    check("no running history to go on: nothing",
          "start_volume" not in rules(plan_checks.check_prepared(
              half, history=[{"starts": h["starts"], "run_km": 0.0, "runs": 0} for h in HISTORY])))

    print("\nwhen Garmin has too little history")
    run_on = lambda d, km: {"startTimeLocal": f"{d} 07:00:00", "activityType": {"typeKey": "running"},  # noqa: E731
                            "distance": km * 1000}
    steady = [run_on(MONDAY - timedelta(days=n), 8.0) for n in (2, 9, 16, 23)] + [run_on(MONDAY - timedelta(days=5), 14.0)]
    history, longest, missing = plan_checks.starting_point(steady, MONDAY)
    check("4 weeks of running is a starting point", history is not None and longest == 14.0 and missing is None,
          str((history, longest, missing)))
    new_watch = [run_on(MONDAY - timedelta(days=2), 6.0)]
    history, longest, missing = plan_checks.starting_point(new_watch, MONDAY)
    check("one week of it isn't: more often a new watch than a runner starting from nothing",
          history is None and longest is None and missing == plan_checks.TOO_LITTLE, str((history, missing)))
    check("which says what wasn't checked and to ask", "ask" in missing and "check_plan" in missing)
    check("and the rest of the plan is still checked",
          plan_checks.check_prepared(half, history=history, longest_recent_km=longest, method="Daniels") == [])
    history, longest, missing = plan_checks.starting_point(None, MONDAY)
    check("Garmin not read at all says so", history is None and missing == plan_checks.NOT_READ, str(missing))
    history, longest, missing = plan_checks.starting_point(new_watch, MONDAY, weekly_km=20, longest_km=9)
    check("what the user says stands in for Garmin", missing is None and longest == 9.0
          and "start_volume" in rules(plan_checks.check_prepared(half, history=history, longest_recent_km=longest,
                                                                 method="Daniels")), str(history))
    history, _, _ = plan_checks.starting_point(steady, MONDAY, weekly_km=40)
    check("and beats Garmin's numbers", [h["run_km"] for h in history] == [40.0], str(history))

    print("\nweekly increase")
    jump = weeks(easy_week(7, 12), easy_week(7, 12), easy_week(12, 14))
    found = run(jump)
    check("a week 12 km up on a 36 km week is worth a look", rules(found) == ["weekly_jump"], str(found))
    check("it names the week and both totals", found and found[0]["week"] == 3
          and "48 km" in found[0]["text"] and "36 km" in found[0]["text"], str(found))
    check("4 km up is within the looser limit",
          "weekly_jump" not in rules(run(weeks(easy_week(7, 12), easy_week(8, 14)))))
    back = weeks(easy_week(8, 14), easy_week(5, 10), easy_week(8, 15))
    check("back up after a cutback is measured against the week before it",
          "weekly_jump" not in rules(run(back)), str(rules(run(back))))
    partial = weeks([("fri", "Easy 5k", 5.0)], easy_week(8, 14))
    check("a first week that starts on a Friday isn't a week to compare with",
          "weekly_jump" not in rules(run(partial)), str(rules(run(partial))))

    print("\nlong-run share")
    three_days = weeks([("tue", "Easy 6k", 6.0), ("thu", "Easy 6k", 6.0), ("sun", "Long 14k", 14.0)])
    found = run(three_days)
    check("a long run over 40% of the week is worth a look", rules(found) == ["long_run_share"], str(found))
    check("it gives the share", found and "54%" in found[0]["text"], str(found))
    check("38% of the week: nothing",
          "long_run_share" not in rules(run(weeks([("tue", "Easy 8k", 8.0), ("thu", "Easy 7k", 7.0),
                                                   ("sun", "Long 9k", 9.4)]))))
    check("race week doesn't count",
          "long_run_share" not in rules(run(weeks([("tue", "Easy 5k", 5.0), ("sat", "Long 10k", 10.0),
                                                   ("sun", "Race: 10K", 10.0)]))))

    print("\nlong-run growth")
    found = run(weeks(easy_week(8, 13), easy_week(8, 18)))
    check("13 km to 18 km in a week is worth a look", "long_run_jump" in rules(found), str(rules(found)))
    check("it says from and to", any("13 km to 18 km" in c["text"] for c in found), str(found))
    check("2 km more: nothing", "long_run_jump" not in rules(run(weeks(easy_week(8, 13), easy_week(8, 15)))))
    after_cutback = weeks(easy_week(8, 15), easy_week(6, 10), easy_week(8, 16))
    check("back after a cutback week is measured against the long run before it",
          "long_run_jump" not in rules(run(after_cutback)), str(rules(run(after_cutback))))
    check("a timed long run 20 minutes longer is fine even if the km estimate jumps",
          "long_run_jump" not in rules(run(weeks(
              [("tue", "Easy 8k", 8.0), ("sun", "Long 100min", 16.0, 0.0, "run", 100)],
              [("tue", "Easy 8k", 8.0), ("sun", "Long 120min", 19.5, 0.0, "run", 120)]))))
    check("the first long run against the longest recent run",
          "long_run_jump" in rules(run(weeks(easy_week(8, 14)), longest_recent_km=8.0)))
    check("the first long run near the longest recent run: nothing",
          "long_run_jump" not in rules(run(weeks(easy_week(8, 14)), longest_recent_km=12.0)))

    print("\nlong-run cap")
    big = weeks(easy_week(12, 22))
    found = run(big, goal="Half marathon, 1:40")
    check("a 22 km long run in a half plan is over its cap", "long_run_cap" in rules(found), str(rules(found)))
    check("it says what's usually enough", any("20 km is usually enough for a half marathon" in c["text"]
                                               for c in found), str(found))
    check("22 km for a marathon is fine", "long_run_cap" not in rules(run(big, goal="Marathon, 3:30")))
    check("the goal race's distance stands in for the goal's words",
          "long_run_cap" in rules(run(big, races=[{"name": "City 10K", "date": "2026-11-15", "tier": "goal",
                                                    "distance_km": 10.0}])))
    check("over 2 h 30 is worth a look even with no distance cap",
          "long_run_cap" in rules(run(weeks([("tue", "Easy 8k", 8.0),
                                             ("sun", "Long 160min", 26.0, 0.0, "run", 160)]))))

    print("\nquality sessions, by style")
    three = weeks([("tue", "Tempo 3x8min", 10.0, 4.8), ("thu", "Intervals 5x1k", 9.0, 5.0),
                   ("sat", "Threshold 3x10min", 10.0, 6.0), ("sun", "Long 16k", 16.0),
                   ("mon", "Easy 12k", 12.0), ("wed", "Easy 12k", 12.0), ("fri", "Easy 12k", 12.0)])
    check("three quality sessions is worth a look on Balanced", "quality_sessions" in rules(run(three)))
    check("Norwegian singles allows three", "quality_sessions" not in rules(run(three, method="Norwegian singles")))
    check("so does a Daash Faster plan", "quality_sessions" not in rules(
        run(three, method="Daash plan, Faster (Norwegian singles style)")))
    two = weeks(easy_week(8, 14) + [("mon", "Intervals 6x800m", 8.0, 4.8)])
    check("two on Steady is worth a look", "quality_sessions" in rules(run(two, method="Daash plan, Steady (80/20)")))
    check("two on Balanced is fine", "quality_sessions" not in rules(run(two)))
    mp = weeks(easy_week(8, 14) + [("mon", "Long 26k w 10k MP", 26.0)])
    check("a long run with marathon-pace work counts as quality", "quality_sessions" in rules(run(mp, method="Steady")))
    check("the style comes from the Built: line first",
          plan_checks.style("Norwegian singles", "Built: 4 days, long run Sun, regular, Steady") == "steady")

    print("\nhard volume")
    fast = weeks([("tue", "Easy 6k", 6.0), ("thu", "Intervals 10x1k", 14.0, 10.0),
                  ("sat", "Easy 6k", 6.0), ("sun", "Long 12k", 12.0)])
    found = run(fast)
    check("10 km of reps in a 38 km week is worth a look on Balanced", rules(found) == ["hard_volume"], str(found))
    check("it says how much", found and "26%" in found[0]["text"], str(found))
    check("Faster allows up to 30%", "hard_volume" not in rules(run(fast, method="Norwegian singles")))
    check("a session whose steps weren't read leaves the week alone",
          "hard_volume" not in rules(run([{**fast[0], "sessions": [
              {**s, "shape": None} if s["type"] == "intervals" else s for s in fast[0]["sessions"]]}])))

    print("\nhard days in a row")
    back_to_back = weeks([("tue", "Easy 12k", 12.0), ("sat", "Tempo 3x8min", 10.0, 4.8), ("sun", "Long 14k", 14.0)])
    found = run(back_to_back)
    check("a long run the day after a tempo is worth a look", rules(found) == ["hard_days_in_a_row"], str(found))
    check("it names both sessions", found and "Long 14k" in found[0]["text"] and "Tempo 3x8min" in found[0]["text"],
          str(found))
    check("on Faster an easy long run isn't a hard day",
          "hard_days_in_a_row" not in rules(run(back_to_back, method="Norwegian singles")))
    check("a day between them: nothing", "hard_days_in_a_row" not in rules(run(weeks(easy_week(8, 14)))))

    print("\nrest days")
    daily = weeks([(d, f"Easy {6}k", 6.0) for d in ("mon", "tue", "wed", "thu", "fri", "sat")] +
                  [("sun", "Long 12k", 12.0)])
    found = run(daily)
    check("a session every day for a week is worth a look", "no_rest_day" in rules(found), str(rules(found)))
    gym = weeks([(d, "Easy 6k", 6.0) for d in ("mon", "tue", "wed", "thu", "fri", "sat")]
                + [("sun", "Gym", 0.0, 0.0, "strength")])
    check("a gym day is rest from running", "no_rest_day" not in rules(run(gym)), str(rules(run(gym))))
    ride = weeks([(d, "Easy 6k", 6.0) for d in ("mon", "tue", "wed", "thu", "fri", "sat")]
                 + [("sun", "Ride 40k", 0.0, 0.0, "ride")])
    check("a ride day isn't", "no_rest_day" in rules(run(ride)), str(rules(run(ride))))
    check("six days on, one off: nothing", "no_rest_day" not in rules(run(weeks(
        [(d, "Easy 6k", 6.0) for d in ("mon", "tue", "wed", "thu", "fri", "sat")],
        [(d, "Easy 6k", 6.0) for d in ("tue", "wed")]))))

    print("\nrecovery weeks")
    climbing = weeks(*[easy_week(6 + n, 10 + n) for n in range(6)])
    found = run(climbing)
    check("six weeks building without a lighter one is worth a look",
          rules(found) == ["no_recovery_week"], str(found))
    check("it covers weeks 1 to 5", found and "Weeks 1 to 5" in found[0]["text"], str(found))
    check("a cutback in week 4: nothing", "no_recovery_week" not in rules(run(weeks(
        easy_week(6, 10), easy_week(7, 11), easy_week(8, 12), easy_week(5, 9), easy_week(8, 13), easy_week(9, 14)))))
    # Week 3 races and is a little lighter; week 4 is lighter still. Against
    # week 3 alone week 4 is 90%, but it's well under the week before the dip.
    dip = weeks(easy_week(10, 16), easy_week(11, 17), easy_week(9, 12), easy_week(8, 12),
                easy_week(11, 17), easy_week(12, 18), easy_week(12, 19), easy_week(13, 19))
    check("a dip over two weeks counts as the lighter week", "no_recovery_week" not in rules(run(dip)),
          str(run(dip)))

    print("\ntaper")
    goal = [{"name": "Half marathon", "date": (MONDAY + timedelta(weeks=3, days=6)).isoformat(), "tier": "goal",
             "distance_km": 21.1}]
    heavy = weeks(easy_week(8, 14), easy_week(9, 15), easy_week(9, 16),
                  [("tue", "Easy 9k", 9.0), ("wed", "Tempo 3x8min", 10.0, 4.8), ("thu", "Easy 9k", 9.0),
                   ("sun", "Race: Half marathon", 21.1)])
    found = run(heavy, races=goal)
    check("race week at 68% of the biggest week before the race... is fine", "taper" not in rules(found),
          str(rules(found)))
    heavier = weeks(easy_week(8, 14), easy_week(9, 15), easy_week(9, 16),
                    [("mon", "Easy 9k", 9.0), ("tue", "Easy 9k", 9.0), ("wed", "Tempo 3x8min", 10.0, 4.8),
                     ("fri", "Easy 6k", 6.0), ("sun", "Race: Half marathon", 21.1)])
    found = run(heavier, races=goal)
    check("34 km before the race against a 44 km peak is worth a look", "taper" in rules(found), str(found))
    check("it says race week", any(c["text"].startswith("Race week has 34 km") for c in found), str(found))

    print("\nraces")
    b = lambda name, d, km=None: {"name": name, "date": d.isoformat(), "tier": "key", "distance_km": km}  # noqa: E731
    sun1 = MONDAY + timedelta(days=6)
    after = weeks([("tue", "Easy 8k", 8.0), ("sun", "Race: 10K", 10.0)],
                  [("tue", "Tempo 3x8min", 10.0, 4.8), ("sun", "Long 14k", 14.0)])
    found = run(after, races=[b("Town 10K", sun1, 10.0)])
    check("a tempo two days after a race is worth a look", "hard_after_race" in rules(found), str(rules(found)))
    check("it names the race", any("Town 10K" in c["text"] for c in found), str(found))
    later = weeks([("tue", "Easy 8k", 8.0), ("sun", "Race: 10K", 10.0)],
                  [("wed", "Tempo 3x8min", 10.0, 4.8), ("sun", "Long 14k", 14.0)])
    check("three days after: nothing", "hard_after_race" not in rules(run(later, races=[b("Town 10K", sun1, 10.0)])))
    check("a long run the day after a race is that, not hard days in a row", rules(run(
        weeks([("sat", "Race: parkrun", 5.0), ("sun", "Long 11k", 11.0)]),
        races=[b("parkrun", sun1 - timedelta(days=1), 5.0)])) == ["hard_after_race"])
    check("a parkrun run as training (a C race) is a hard day like a tempo, not a race to recover from", rules(run(
        weeks([("sat", "Race: parkrun", 5.0), ("sun", "Long 11k", 11.0)], [("wed", "Easy 6k", 6.0)]),
        races=[{**b("parkrun", sun1 - timedelta(days=1), 5.0), "tier": "tune"}])) == ["hard_days_in_a_row"])

    two_b = [b("Town 10K", sun1, 10.0), b("City 10K", sun1 + timedelta(days=7), 10.0)]
    plain = weeks(easy_week(8, 14, tempo=False), easy_week(8, 14, tempo=False), easy_week(8, 14, tempo=False))
    check("B races on back-to-back weekends are worth a look", "b_races_close" in rules(run(plain, races=two_b)))
    check("three weeks apart: nothing", "b_races_close" not in rules(run(plain, races=[
        two_b[0], b("City 10K", sun1 + timedelta(days=21), 10.0)])))

    goal_day = sun1 + timedelta(weeks=2)
    a_race = {"name": "Big Half", "date": goal_day.isoformat(), "tier": "goal", "distance_km": 21.1}
    found = run(plain, races=[b("Town 10K", goal_day - timedelta(days=7), 10.0), a_race])
    check("a race a week before the goal race is worth a look", "race_before_goal" in rules(found), str(found))
    check("two weeks before: nothing", "race_before_goal" not in rules(run(plain, races=[
        b("Town 10K", goal_day - timedelta(days=14), 10.0), a_race])))
    check("the last race in the plan's last week is the goal when none is marked",
          "race_before_goal" in rules(run(plain, races=[
              {**b("Town 10K", goal_day - timedelta(days=7), 10.0), "tier": "tune"},
              {**a_race, "tier": "key"}])))

    long_b = weeks(easy_week(8, 16), [("tue", "Easy 8k", 8.0), ("thu", "Tempo 3x8min", 10.0, 4.8),
                                       ("sat", "Easy 8k", 8.0), ("sun", "Race: Trail 25k", 25.0)],
                    easy_week(8, 16))
    trail = [b("Trail 25k", MONDAY + timedelta(weeks=1, days=6), 25.0)]
    found = run(long_b, races=trail)
    check("a B race longer than any long run, in a heavier week, is worth a look",
          "b_race_long" in rules(found), str(rules(found)))
    eased = weeks(easy_week(8, 16), [("tue", "Easy 6k", 6.0), ("thu", "Easy 5k", 5.0),
                                      ("sun", "Race: Trail 25k", 25.0)], easy_week(8, 16))
    check("the same race in a lighter week: nothing", "b_race_long" not in rules(run(eased, races=trail)))

    two_a = lambda gap, km: [{"name": "Spring Half", "date": sun1.isoformat(), "tier": "goal", "distance_km": 21.1},  # noqa: E731
                             {"name": "Summer race", "date": (sun1 + timedelta(weeks=gap)).isoformat(),
                              "tier": "goal", "distance_km": km}]
    long_plan = weeks(*[easy_week(8, 14, tempo=False)] * 10)
    check("two goal halves 3 weeks apart are worth a look", "goal_races_close" in rules(run(long_plan,
                                                                                            races=two_a(3, 21.1))))
    check("6 weeks apart: fine", "goal_races_close" not in rules(run(long_plan, races=two_a(6, 21.1))))
    check("but not when one is a marathon", "goal_races_close" in rules(run(long_plan, races=two_a(6, 42.2))))

    print("\nget_plan's answer")
    answer = {"goal": "Half marathon", "weeks": climbing, "races": []}
    check("checks over get_plan's weeks", rules(plan_checks.check_answer(answer)) == ["no_recovery_week"])
    check("only from this week on: what's past is past",
          plan_checks.check_answer(answer, today=MONDAY + timedelta(weeks=5)) == [])
    calendar = weeks(easy_week(8, 16), easy_week(8, 16) + [("fri", "Race: parkrun", 5.0)])
    parkrun = [{"name": "parkrun", "date": (MONDAY + timedelta(weeks=1, days=4)).isoformat(), "tier": "tune",
                "distance_km": 5.0}]
    check("the calendar's last week ending on a parkrun doesn't make it the goal race",
          "long_run_cap" not in rules(plan_checks.check_answer({"weeks": calendar, "races": parkrun})))
    check("a plan's own last race is its goal",
          "long_run_cap" in rules(plan_checks.check_answer({"label": "5K", "weeks": calendar, "races": parkrun})))
    # get_plan's answer has no method or goal of its own: both come from the
    # plan running now, whose goal can name a B race before the A race.
    london = [{"name": "London", "date": "2027-04-25", "tier": "goal", "distance_km": 42.2}]
    block = {"weeks": big, "races": london, "current_plan": {
        "label": "B1", "goal": "Base block (Norwegian singles style): building to the half check, London sub-2:40"}}
    check("the A race sets the cap, not a half the goal mentions",
          "long_run_cap" not in rules(plan_checks.check_answer(block)), str(plan_checks.check_answer(block)))
    check("a goal naming Norwegian singles gets Faster limits",
          plan_checks.style(None, None, block["current_plan"]["goal"]) == "faster")
    unread = [{**w, "unread": 1} if w["week"] == 3 else w for w in climbing]
    check("a week with a workout not read yet isn't sized",
          "no_recovery_week" not in rules(plan_checks.check_answer({"weeks": unread})))
    check("each check has its rule, week, date and text",
          all(set(c) == {"rule", "week", "date", "text"} for c in run(heavier, races=goal) + run(three)))
    words = " ".join(c["text"] for c in run(heavier, races=goal) + run(three) + run(daily) + run(after, races=[
        b("Town 10K", sun1, 10.0)])).lower()
    check("the tone is worth a look, never injury or error", "injur" not in words and "error" not in words
          and "risk" not in words, words[:120])

    print()
    if FAILED:
        print(f"{len(FAILED)} failed: {', '.join(FAILED)}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
