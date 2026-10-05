"""What the matcher decides, on the cases that decide whether it is usable.

The point of this file is the awkward weeks, not the tidy one. A matcher that
only handles sessions done exactly when scheduled would report failure in most
real weeks, so most of these are about the ways a week goes sideways and still
counts.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from garmin_mcp.progress import (  # noqa: E402
    Actual,
    Planned,
    by_week,
    from_collected,
    match,
)

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


MON = date(2026, 9, 21)
TUE, WED, THU, FRI, SAT, SUN = (date(2026, 9, d) for d in (22, 23, 24, 25, 26, 27))
TODAY = SUN


def run(name: str, sport: str = "run", secs: float = 0.0, km: float = 0.0, day=None):
    return Actual(day=day, name=name, sport=sport, seconds=secs, metres=km * 1000,
                  activity_id=f"{name}-{day}")


def plan(name: str, sport: str = "run", secs: float = 0.0, day=None, titled: bool = False):
    return Planned(day=day, name=name, sport=sport, seconds=secs, title=name if titled else "")


def main() -> int:
    print("\nthe straightforward week")
    r = match([plan("Threshold", secs=2400, day=TUE)],
              [run("Threshold", secs=2500, day=TUE)], TODAY)
    check("session done on the day counts", len(r.done) == 1 and not r.missed)
    check("the reason names the activity", "on the day" in r.done[0]["why"],
          r.done[0]["why"])

    print("\nthe week that moved")
    # Roo's actual message: a friend wanted to train on a different night, so
    # the runs got flipped around. Nothing was missed. A matcher that demands
    # the scheduled day reports two failures here, which is the single fastest
    # way to make somebody stop trusting it.
    r = match(
        [plan("Threshold", secs=2400, day=TUE), plan("Easy", secs=1800, day=WED)],
        [run("Easy", secs=1900, day=TUE), run("Threshold", secs=2450, day=WED)],
        TODAY,
    )
    check("two sessions swapped still both count",
          len(r.done) == 2 and not r.missed and not r.extra,
          f"done={len(r.done)} missed={len(r.missed)} extra={len(r.extra)}")
    # Which planned session each run is credited to is not knowable here — a
    # real Garmin activity is called "Evening Run", not the name of the session
    # it was meant to be. What has to hold is that no run is counted twice.
    check("no activity is used more than once",
          len({d["activity_id"] for d in r.done}) == 2,
          str([d["activity_id"] for d in r.done]))

    # A session genuinely moved to another day says so, rather than appearing
    # to have happened when it did not.
    r = match([plan("Long run", secs=5400, day=SAT)],
              [run("Long run", secs=5500, day=SUN)], TODAY)
    check("a shifted match says when it really happened",
          "late" in r.done[0]["why"] and "Sunday" in r.done[0]["why"],
          r.done[0]["why"])
    check("and records the day it was actually done",
          r.done[0]["done_on"] == SUN.isoformat() and r.done[0]["date"] == SAT.isoformat())

    print("\nhow far a session may drift")
    r = match([plan("Long run", secs=5400, day=SAT)],
              [run("Long run", secs=5600, day=SUN)], TODAY)
    check("one day late counts", len(r.done) == 1, str(r.missed))
    r = match([plan("Long run", secs=5400, day=MON)],
              [run("Long run", secs=5600, day=THU)], TODAY)
    check("three days late does not count", len(r.missed) == 1 and len(r.extra) == 1)

    print("\ndoing less than planned")
    r = match([plan("Intervals", secs=3600, day=TUE)],
              [run("Intervals", secs=3000, day=TUE)], TODAY)
    check("83% of the session counts", len(r.done) == 1, str(r.missed))
    r = match([plan("Intervals", secs=3600, day=TUE)],
              [run("Cut short", secs=900, day=TUE)], TODAY)
    check("a quarter of it does not", len(r.missed) == 1 and len(r.extra) == 1)

    print("\nthe wrong sport")
    r = match([plan("Threshold", sport="run", secs=2400, day=TUE)],
              [run("Upper body", sport="strength", secs=2400, day=TUE)], TODAY)
    check("a gym session does not tick off a run",
          len(r.missed) == 1 and len(r.extra) == 1)

    print("\nstrength")
    # Garmin estimates a strength workout by counting every set and rest, so a
    # real gym session often comes in well under it. Any strength session on the
    # day is the session.
    r = match([plan("Full body", sport="strength", secs=3300, day=TUE)],
              [run("Strength", sport="strength", secs=1200, day=TUE)], TODAY)
    check("a short strength session still counts", len(r.done) == 1, str(r.missed))
    r = match([plan("Full body", sport="strength", secs=3300, day=TUE)],
              [run("Easy", sport="run", secs=3300, day=TUE)], TODAY)
    check("but a run doesn't tick off strength", len(r.missed) == 1)
    r = match([plan("Full body", sport="strength", day=SAT)],
              [run("Strength", sport="strength", secs=2000, day=MON)], TODAY)
    check("strength done earlier in the week counts, moved",
          len(r.done) == 1 and r.done[0]["done_on"] == MON.isoformat()
          and "5 days early" in r.done[0]["why"], str(r.done or r.missed))
    r = match([plan("Full body", sport="strength", day=MON)],
              [run("Strength", sport="strength", secs=2000, day=MON - timedelta(days=2))], TODAY)
    check("but not from the week before", len(r.missed) == 1 and len(r.extra) == 1)
    r = match([plan("Upper", sport="strength", day=MON), plan("Lower", sport="strength", day=THU)],
              [run("Strength", sport="strength", secs=2000, day=THU)], TODAY)
    check("a session on its own day is never taken to cover an earlier one",
          [d["name"] for d in r.done] == ["Lower"] and [m["name"] for m in r.missed] == ["Upper"],
          f"done={[d['name'] for d in r.done]}")
    r = match([plan("Easy", day=SAT)], [run("Easy", secs=1800, day=MON)], TODAY)
    check("runs still have to be within a day", len(r.missed) == 1)

    print("\nstrength stays in its own week")
    NEXT_MON = SUN + timedelta(days=1)
    r = match([plan("Full body", sport="strength", day=SUN),
               plan("Full body", sport="strength", day=NEXT_MON)],
              [run("Strength", sport="strength", secs=1900, day=NEXT_MON)], NEXT_MON)
    check("Monday's gym is Monday's session, not last Sunday's",
          [d["date"] for d in r.done] == [NEXT_MON.isoformat()]
          and [m["date"] for m in r.missed] == [SUN.isoformat()], f"done={r.done} missed={r.missed}")
    r = match([plan("Full body", sport="strength", day=SUN)],
              [run("Strength", sport="strength", secs=1900, day=NEXT_MON)], NEXT_MON)
    check("and never back-dates to cover last week", len(r.missed) == 1 and len(r.extra) == 1)

    print("\nstrength done early")
    r = match([plan("Full body", sport="strength", day=SAT)],
              [run("Strength", sport="strength", secs=1900, day=TUE)], WED)
    check("a session still ahead this week is ticked by one already done",
          len(r.done) == 1 and not r.upcoming and r.done[0]["done_on"] == TUE.isoformat(), str(r.done or r.upcoming))
    r = match([plan("Easy", day=SAT)], [run("Easy", secs=1800, day=TUE)], WED)
    check("a run is not ticked early by type alone", len(r.upcoming) == 1 and len(r.extra) == 1)
    r = match([plan("Full body", sport="strength", day=SAT + timedelta(days=7))],
              [run("Strength", sport="strength", secs=1900, day=TUE)], WED)
    check("nor is next week's strength", len(r.upcoming) == 1)

    print("\nthe workout's own name")
    # Garmin names an activity after the workout it was started from, with the
    # place in front for runs. That says which session it was, wherever it fell.
    r = match([plan("Threshold 5x1km", day=TUE, titled=True)],
              [run("Tower Hamlets - Threshold 5x1km", secs=3000, day=FRI)], SUN)
    check("a run started from its workout counts, moved, anywhere in the week",
          len(r.done) == 1 and r.done[0]["done_on"] == FRI.isoformat(), str(r.done or r.missed))
    r = match([plan("Threshold 5x1km", day=TUE, titled=True)],
              [run("Morning Run", secs=3000, day=FRI)], SUN)
    check("without the name it is still a miss and an extra run", len(r.missed) == 1 and len(r.extra) == 1)
    r = match([plan("Push", sport="strength", day=MON, titled=True),
               plan("Pull", sport="strength", day=WED, titled=True)],
              [run("Pull", sport="strength", secs=1900, day=MON),
               run("Push", sport="strength", secs=1900, day=WED)], SUN)
    check("named strength sessions go to their own workouts, swapped days and all",
          {d["name"]: d["done_on"] for d in r.done} == {"Push": WED.isoformat(), "Pull": MON.isoformat()},
          str(r.done))
    r = match([plan("Push", sport="strength", day=MON, titled=True),
               plan("Pull", sport="strength", day=WED, titled=True)],
              [run("Strength", sport="strength", secs=1900, day=MON),
               run("Strength", sport="strength", secs=1900, day=WED)], SUN)
    check("unnamed ones still count, by day", len(r.done) == 2 and not r.missed)

    print("\none run cannot satisfy two sessions")
    r = match(
        [plan("Easy", secs=1800, day=TUE), plan("Threshold", secs=2400, day=TUE)],
        [run("Easy", secs=1850, day=TUE)],
        TODAY,
    )
    check("only one is ticked", len(r.done) == 1 and len(r.missed) == 1,
          f"done={[d['name'] for d in r.done]} missed={[m['name'] for m in r.missed]}")
    check("it went to the closer match by length",
          r.done[0]["name"] == "Easy", r.done[0]["name"])

    print("\nsessions with no estimate")
    # Garmin does not always carry a duration for a planned session. With
    # nothing to fall short of, existence is the whole test.
    r = match([plan("Club run", day=TUE)], [run("Club run", secs=600, day=TUE)], TODAY)
    check("no planned duration means any run counts", len(r.done) == 1, str(r.missed))

    print("\nruns nobody planned")
    r = match([], [run("Parkrun", secs=1200, km=5, day=SAT)], TODAY)
    check("an unplanned run is extra, not an error",
          len(r.extra) == 1 and not r.missed)
    check("extra carries its distance", r.extra[0]["actual_km"] == 5.0)

    print("\nthe future is not a failure")
    r = match([plan("Long run", secs=5400, day=date(2026, 10, 3))], [], TODAY)
    check("tomorrow's session is upcoming, not missed",
          len(r.upcoming) == 1 and not r.missed)

    print("\nthe weekly roll-up")
    r = match(
        [plan("Threshold", secs=2400, day=TUE), plan("Easy", secs=1800, day=THU),
         plan("Long run", secs=5400, day=SAT)],
        [run("Threshold", secs=2450, day=TUE), run("Easy", secs=1800, day=THU)],
        TODAY,
    )
    weeks = by_week(r, TODAY, weeks=1)
    check("counts the week", weeks[0]["planned"] == 3 and weeks[0]["completed"] == 2,
          str(weeks[0]))
    check("an unfinished week is not complete", weeks[0]["complete"] is False)
    check("names what is missing", weeks[0]["missed"] == ["Long run"])

    r = match([plan("Easy", secs=1800, day=TUE)], [run("Easy", secs=1800, day=TUE)], TODAY)
    check("a finished week is complete", by_week(r, TODAY, 1)[0]["complete"] is True)

    # A week with nothing planned is not a week completed. Showing a full bar
    # for a week nobody trained would make the whole thing meaningless.
    empty = by_week(match([], [], TODAY), TODAY, 1)[0]
    check("a week with no plan is not complete",
          empty["complete"] is False and empty["planned"] == 0, str(empty))

    print("\nreading Garmin's actual shapes")
    planned, actual = from_collected({
        "planned": [{"date": "2026-09-22", "title": "Thursday Threshold",
                     "workoutId": 555001, "sportTypeKey": "running"}],
        "planned_duration": {555001: 2400.0},
        "planned_distance": {555001: 9000.0},
        "activities": [{"startTimeLocal": "2026-09-22 18:30:00",
                        "activityName": "Evening Run", "activityId": 123,
                        "activityType": {"typeKey": "running"},
                        "duration": 2500.0, "distance": 9200.0}],
    })
    check("planned parsed", len(planned) == 1 and planned[0].sport == "run"
          and planned[0].seconds == 2400.0, str(planned))
    check("activity parsed, timestamp trimmed to a date",
          len(actual) == 1 and actual[0].day == date(2026, 9, 22), str(actual))
    r = match(planned, actual, TODAY)
    check("and they match end to end", len(r.done) == 1, str(r.missed))

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: {', '.join(FAILED)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
