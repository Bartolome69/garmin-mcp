"""The plan engine, on the runners it has to serve.

Each plan it builds must go through create_plan's own preparation untouched,
name every session so get_plan reads its type right, and keep inside the plan
checker's warnings: a plan Daash builds shouldn't be one Daash then nags about.

Run: .venv/bin/python tests/plan_engine_test.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from garmin_mcp import plan_checks, plan_engine as e, training_plan as tp  # noqa: E402

FAILED: list[str] = []
TODAY = date(2026, 11, 7)  # a Saturday: plans start on the Monday after


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


RUNNERS = {
    "a regular half marathoner, 5 days": dict(goal="half", race_date=date(2027, 3, 14), days=5, weekly_km=45,
                                               longest_km=18, predictions={"5k": 1110, "half": 5040}, target="1:25:00"),
    "a new runner's first 5K, 3 days": dict(goal="5k", race_date=date(2027, 1, 16), days=3, weekly_km=15,
                                             longest_km=6, level="new"),
    "a 10K, 4 days, a prediction": dict(goal="10k", race_date=date(2027, 2, 20), days=4, weekly_km=25,
                                         longest_km=10, predictions={"10k": 2700}),
    "an experienced marathoner, 6 days, building more": dict(
        goal="marathon", race_date=date(2027, 4, 24), days=6, weekly_km=60, longest_km=24, level="experienced",
        predictions={"half": 5000, "marathon": 10600}, volume="more"),
    "a marathon from 20 km a week, 4 days": dict(goal="marathon", race_date=date(2027, 2, 28), days=4,
                                                 weekly_km=20, longest_km=10),
    "general fitness from nothing, 3 days": dict(goal="fitness", days=3, weekly_km=0, longest_km=0, level="new"),
    "a half after a niggle, 4 days": dict(goal="half", race_date=date(2027, 1, 24), days=4, weekly_km=30,
                                          longest_km=12, injury="niggle", style="balanced"),
    "a 10K with Tuesday and Thursday out, long run Saturday": dict(
        goal="10k", race_date=date(2027, 1, 30), days=4, days_out=[1, 3], long_day=5, weekly_km=30, longest_km=12),
}


def prepared(plan: dict) -> list:
    _, prep = tp.prepare(plan["goal"], plan["sessions"], plan["label"], TODAY, name=plan["name"],
                         method=plan["method"], milestones=plan["milestones"])
    return prep


def main() -> int:
    for who, answers in RUNNERS.items():
        print(f"\n{who}")
        a = e.Answers(today=TODAY, **answers)
        built = e.build(a)
        plan, preview = built["plan"], built["preview"]
        try:
            prep = prepared(plan)
            check("create_plan prepares every session", len(prep) == len(plan["sessions"]))
        except Exception as exc:  # noqa: BLE001
            check("create_plan prepares every session", False, repr(exc))
            continue
        days = sorted({s["date"] for s in plan["sessions"]})
        check("one session a day, today or later", len(days) == len(plan["sessions"]) and days[0] >= TODAY.isoformat(),
              f"{len(days)} days for {len(plan['sessions'])} sessions")
        out = set(answers.get("days_out") or [])
        check("never on a day they said is out",
              not any(date.fromisoformat(d).weekday() in out for d in days))
        wanted = {"Easy": "easy", "Long": "long", "Tempo": "threshold", "Threshold": "threshold",
                  "Intervals": "intervals", "Race pace": "threshold", "Race:": "race"}
        wrong = []
        for s in plan["sessions"]:
            head = "Race pace" if s["name"].startswith("Race pace") else s["name"].split()[0]
            if tp.session_type(s["name"], "running") != wanted.get(head):
                wrong.append(s["name"])
        check("every name reads as its type on the card", not wrong, ", ".join(wrong[:4]))
        history = [{"starts": "2026-10-26", "run_km": float(answers["weekly_km"]), "runs": 3}] if answers["weekly_km"] else None
        notes = plan_checks.check_prepared(prep, goal=plan["goal"], method=plan["method"],
                                           milestones=plan["milestones"], history=history,
                                           longest_recent_km=answers.get("longest_km") or None)
        check("the plan checker has nothing to say about it", not notes,
              "; ".join(f"{n['rule']}: {n['text'][:80]}" for n in notes[:3]))
        weeks = preview["weeks"]
        if a.goal != "fitness":
            last = plan["sessions"][-1]
            check("it ends with the race, on race day",
                  last["date"] == a.race_date.isoformat() and last["name"].startswith("Race:"), last["name"])
            check("the race is the goal milestone",
                  plan["milestones"] and plan["milestones"][0]["tier"] == "goal")
        check("every week has its phase", all(w.get("phase") for w in weeks))
        check("a reason or two to show", len(preview["reasons"]) >= 2)

    print("\nwhat it won't build")
    for name, answers, words in [
        ("a race 4 weeks away", dict(goal="10k", race_date=TODAY + timedelta(weeks=4)), "too close"),
        ("something that hurts now", dict(goal="10k", race_date=date(2027, 3, 1), injury="hurts"), "physio"),
        ("a race date in the past", dict(goal="half", race_date=date(2026, 10, 1)), "after today"),
        ("only 2 days free", dict(goal="10k", race_date=date(2027, 3, 1), days_out=[0, 1, 2, 3, 4]), "3 days"),
    ]:
        try:
            e.build(e.Answers(today=TODAY, **answers))
            check(f"{name}: refused", False, "built anyway")
        except e.BuildError as exc:
            check(f"{name}: refused, saying why", words in str(exc), str(exc))

    print("\nanswers from the app")
    a = e.Answers.from_json({"goal": "half", "race_date": "2027-03-14", "days": "9", "long_day": 5, "days_out": [2, "x", 9],
                             "weekly_km": "32", "predictions": {"half": "1:30:00", "mile": 300}, "style": "zz"}, TODAY)
    check("days clamped to 6, bad days out dropped, unknown style is Pick for me",
          a.days == 6 and a.days_out == [2] and a.style == "pick" and a.long_day == 5, str(a))
    check("a prediction as a clock time, unknown distances ignored", a.predictions == {"half": 5400.0}, str(a.predictions))
    a = e.Answers.from_json({"goal": "10k", "race_date": "2027-03-14", "predictions": {"5k": "18m 00s", "half": "1h 25m 00s"}}, TODAY)
    check("and as Garmin's predictions are worded", a.predictions == {"5k": 1080.0, "half": 5100.0}, str(a.predictions))
    try:
        e.Answers.from_json({"goal": "ultra"}, TODAY)
        check("an unknown goal is refused", False)
    except e.BuildError:
        check("an unknown goal is refused", True)

    print("\npaces")
    p = e.paces(e.Answers(goal="half", today=TODAY, predictions={"half": 5400}, target="1:25:00", weekly_km=30))
    check("a goal far quicker than the prediction trains from the prediction, and says so",
          abs(p.race - 5400 / 21.0975) < 1 and p.note and "1:30:00" in p.note, str(p.note))
    p = e.paces(e.Answers(goal="half", today=TODAY, predictions={"half": 5400}, target="1:29:00", weekly_km=30))
    check("a goal within 3% is trained at", abs(p.race - 5340 / 21.0975) < 1 and not p.note)
    p = e.paces(e.Answers(goal="10k", today=TODAY))
    check("no predictions: by effort, no targets", not p.known)
    built = e.build(e.Answers(goal="10k", today=TODAY, race_date=date(2027, 2, 20), weekly_km=25, longest_km=10))
    targets = [st for s in built["plan"]["sessions"] for st in s["steps"] if st.get("type") == "repeat"
               for inner in st["steps"] if inner.get("pace")]
    check("and so no pace on any step", not targets)

    print("\nvolume options")
    opts = e.peak_options(e.Answers(goal="half", today=TODAY, race_date=date(2027, 3, 14), weekly_km=30))
    check("keep, build and more, in order, keep at what they run",
          opts["keep"] == 30 and opts["keep"] <= opts["build"] <= opts["more"], str(opts))
    opts = e.peak_options(e.Answers(goal="5k", today=TODAY, race_date=date(2027, 3, 14), weekly_km=80))
    check("someone already over the goal's ceiling keeps their own volume", opts["more"] == 80, str(opts))

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
