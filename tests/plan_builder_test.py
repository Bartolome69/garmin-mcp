"""The plan builder's rules, on the plans that decide whether it's safe to hand out.

The same answers always give the same plan; weeks run Monday to Sunday and
the plan ends on race day; the build is in small steps with easier weeks;
hard days never touch; a niggle starts easy; and every session is one
create_plan accepts, so nothing the preview shows can fail on the way to
the watch.

    .venv/bin/python tests/plan_builder_test.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from garmin_mcp import plan_builder as pb  # noqa: E402
from garmin_mcp import training_plan  # noqa: E402

FAILED: list[str] = []
TODAY = date(2026, 11, 6)  # a Friday: the plan starts on Monday 9 November
FIT = pb.Fitness(predictions={"5k": 1450, "10k": 3020, "half": 6720, "marathon": 14280},
                 threshold_pace=290, threshold_hr=168)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    if not ok:
        FAILED.append(name)


def answers(**over) -> pb.Answers:
    raw = {"avg_km": 26, "longest_km": 12, "level": "regular", "days": 4, "out": [4], "long_day": 6,
           "amount": "build", "injury": "none", "style": "balanced", "goal_time": "1:48",
           "races": [{"name": "Riverside 10K", "date": "2027-02-14", "km": 10, "tier": "B"},
                     {"name": "Cambridge Half", "date": "2027-03-21", "km": 21.1, "tier": "A"}]}
    raw.update(over)
    return pb.Answers.from_json(raw)


def hard_days(built: pb.Built) -> list[date]:
    return sorted(r["date"] for w in built.weeks for r in w["rows"] if r["kind"] in ("hard", "long", "race"))


def shape_checks() -> None:
    a = answers()
    s = pb.shape(a, TODAY)
    check("a plan starts on the Monday after today", s.start == date(2026, 11, 9), str(s.start))
    check("and runs 19 weeks to a Sunday race", s.weeks == 19, str(s.weeks))
    check("its phases run base, build, peak, taper, in that order",
          s.phases == sorted(s.phases, key=["Base", "Build", "Peak", "Taper"].index) and s.phases[-1] == "Taper", str(s.phases))
    check("a half tapers for two weeks", s.phases.count("Taper") == 2, str(s.phases))
    far = pb.shape(answers(races=[{"name": "Autumn Marathon", "date": "2027-09-26", "km": 42.2, "tier": "A"}]), TODAY)
    check("a race more than 20 weeks off starts the plan 20 weeks out",
          far.weeks == 20 and far.start == date(2027, 5, 10), f"{far.weeks} {far.start}")
    try:
        pb.shape(answers(races=[{"name": "Soon 10K", "date": "2026-11-22", "km": 10, "tier": "A"}]), TODAY)
        check("a race under three weeks off has no plan", False, "built one")
    except pb.BuildError:
        check("a race under three weeks off has no plan", True)
    short = pb.phases("race", "half", 5)
    check("a short plan loses its base first and keeps its taper",
          "Base" not in short and short[-2:] == ["Taper", "Taper"], str(short))
    open_goal = pb.shape(answers(races=[]), TODAY)
    check("no race is a 12-week plan", open_goal.weeks == 12 and open_goal.race is None, str(open_goal.weeks))


def plan_checks() -> None:
    a = answers()
    one, two = pb.build(a, FIT, TODAY), pb.build(a, FIT, TODAY)
    check("the same answers give the same plan", one.sessions == two.sessions)
    last = max(x["date"] for x in one.sessions)
    check("the plan ends on race day", last == "2027-03-21", last)
    race = [x for x in one.sessions if x["name"] == "Race: Cambridge Half"]
    check("with the race as its last session", len(race) == 1 and race[0]["date"] == "2027-03-21", str(race))
    in_week = all(w["starts"] <= r["date"] <= w["starts"] + timedelta(days=6) for w in one.weeks for r in w["rows"])
    check("every session is in its Monday-to-Sunday week", in_week)
    check("and every week starts on a Monday", all(w["starts"].weekday() == 0 for w in one.weeks))
    check("the first week is no bigger than now", one.weeks[0]["km"] <= a.avg_km + 1, str(one.weeks[0]["km"]))
    full = [w for w in one.weeks if not w["recovery"] and not w["b_race"] and w["phase"] != "Taper" and not w["a_race"]]
    steps = [(p["n"], w["n"], w["km"] - p["km"]) for p, w in zip(full, full[1:]) if w["n"] == p["n"] + 1]
    too_big = [s for s in steps if s[2] > max(3, round(0.1 * full[0]["km"])) + 2]
    check("full weeks grow in small steps", not too_big, str(too_big))
    easy = [w["n"] for w in one.weeks if w["recovery"]]
    check("with an easier week every fourth", easy[:2] == [4, 8], str(easy))
    days = hard_days(one)
    touching = [(x, y) for x, y in zip(days, days[1:]) if (y - x).days < 2]
    check("hard days never touch", not touching, str(touching[:3]))
    longs = [r["km"] for w in one.weeks for r in w["rows"] if r["kind"] == "long"]
    check("the long run stays inside the half's 20 km", max(longs) <= 20, str(max(longs)))
    jumps = [(x, y) for x, y in zip(longs, longs[1:]) if y - x > 3]
    check("and grows by small steps", not jumps or all(x < y for x, y in jumps), str(jumps))
    b = next(w for w in one.weeks if w["b_race"])
    check("the B race is that week's harder session", not any(r["kind"] == "hard" for r in b["rows"])
          and any(r["kind"] == "race" for r in b["rows"]), str([r["name"] for r in b["rows"]]))
    taper = [w["km"] for w in one.weeks if w["phase"] == "Taper"]
    check("the taper comes down", taper == sorted(taper, reverse=True) and taper[-1] < max(w["km"] for w in one.weeks), str(taper))


def style_checks() -> None:
    gentle = pb.build(answers(injury="niggle"), FIT, TODAY)
    first = [r for w in gentle.weeks[:3] for r in w["rows"] if r["kind"] == "hard"]
    check("a niggle means easy running only for 3 weeks", not first, str([r["name"] for r in first]))
    check("and an easier week every third", [w["n"] for w in gentle.weeks if w["recovery"]][:2] == [3, 6])
    steady = pb.build(answers(style="steady"), FIT, TODAY)
    check("steady has at most one harder session a week",
          all(sum(r["kind"] == "hard" for r in w["rows"]) <= 1 for w in steady.weeks))
    faster = pb.build(answers(style="faster", days=5, out=[]), FIT, TODAY)
    subs = [s for s in faster.sessions if s["name"].startswith("Threshold")]
    hr = [st for s in subs for x in s["steps"] if x.get("type") == "repeat" for st in x["steps"] if st.get("hr")]
    check("faster's threshold sessions are kept under threshold heart rate",
          subs and all(st["hr"][1] < FIT.threshold_hr for st in hr), str(hr[:1]))
    no_hr = pb.build(answers(style="faster"), pb.Fitness(predictions=FIT.predictions), TODAY)
    check("without a threshold heart rate, faster falls back to balanced", no_hr.style == "balanced", no_hr.style)
    try:
        pb.build(answers(injury="hurts"), FIT, TODAY)
        check("something that hurts now has no plan", False)
    except pb.BuildError:
        check("something that hurts now has no plan", True)
    picked, why = pb.pick_style(answers(level="new"), FIT)
    check("pick for me gives a new runner steady, and says why", picked == "steady" and why, f"{picked} {why}")


def goal_checks() -> None:
    check('"1:48" for a half is an hour and 48 minutes', pb.goal_secs("1:48", "half") == 6480)
    check('"48:30" for a 10K is minutes', pb.goal_secs("48:30", "10k") == 2910)
    check("Garmin's own durations read", pb.parse_time("1h 52m 00s") == 6720 and pb.parse_time("4:50 /km") == 290)
    chips = pb.goal_chips("half", 21.1, FIT)
    check("three goal times, quickest last", len(chips) == 3 and chips[0]["secs"] > chips[1]["secs"] > chips[2]["secs"], str(chips))
    check("1:52 is realistic", pb.goal_verdict(6720, "half", 21.1, FIT)["kind"] == "realistic")
    check("1:48 is a stretch", pb.goal_verdict(6480, "half", 21.1, FIT)["kind"] == "stretch")
    check("1:40 is very ambitious", pb.goal_verdict(6000, "half", 21.1, FIT)["kind"] == "ambitious")
    quick = pb.Paces.of(FIT, "half", 21.1, 26, 6000)
    check("a very ambitious goal doesn't set the race pace", round(quick.race) == round(6720 / 21.1), str(quick.race))


def output_checks() -> None:
    a = answers()
    args = pb.create_args(a, FIT, TODAY)
    check("the goal names the race, the time and the day", args["goal"] == "Cambridge Half, 1:48, 21 March", args["goal"])
    check("the method names the style", args["method"] == "Daash plan, Balanced (Daniels style)", args["method"])
    check("the races are milestones, the goal with its target",
          [(m["label"], m["tier"]) for m in args["milestones"]] == [("Riverside 10K", "key"), ("Cambridge Half", "goal")]
          and args["milestones"][-1].get("target") == "1:48", str(args["milestones"]))
    for style in ("steady", "balanced", "faster"):
        for dist in (("5K", 5), ("Half", 21.1), ("Marathon", 42.2)):
            b = answers(style=style, days=5, out=[], races=[{"name": f"Big {dist[0]}", "date": "2027-03-21", "km": dist[1], "tier": "A"}])
            made = pb.create_args(b, FIT, TODAY)
            try:
                code, prepared = training_plan.prepare(made["goal"], made["sessions"], None, TODAY, name=made["name"],
                                                       method=made["method"], milestones=made["milestones"])
                check(f"create_plan takes the {style} {dist[0]} plan", len(prepared) == len(made["sessions"]))
            except Exception as exc:  # noqa: BLE001
                check(f"create_plan takes the {style} {dist[0]} plan", False, f"{type(exc).__name__}: {exc}")
    pv = pb.preview(a, FIT, TODAY)
    check("the preview's sub line runs Monday to race Sunday", pv["sub"] == "19 weeks · 9 Nov to 21 Mar · 4 days a week", pv["sub"])
    check("it has a week a bar", len(pv["weeks"]) == 19)
    check("race weeks are marked A and B", [w["race"] for w in pv["weeks"] if w["race"]] == ["B", "A"])
    check("the peak week is the biggest", pv["weeks"][pv["peak_week"] - 1]["km"] == max(w["km"] for w in pv["weeks"]))
    a_week = pv["weeks"][-1]["sessions"]
    check("the A race week shows the race, then short easy runs",
          a_week[0]["type"] == "Race · A" and a_week[-1]["name"] == "Short easy runs, legs fresh", str(a_week))
    check("each week's dates run Monday to Sunday", pv["weeks"][0]["dates"] == "9 Nov – 15 Nov", pv["weeks"][0]["dates"])
    check("each choice has its reason", [y["k"] for y in pv["whys"]] ==
          ["RACES", "GOAL TIME", "DAYS", "HOW MUCH", "LONG RUN", "INJURIES", "STYLE"], str([y["k"] for y in pv["whys"]]))
    check("no reason says the level", not any(w in y["t"].lower() for y in pv["whys"] for w in ("regular", "experienced", "new runner")))


def main() -> int:
    shape_checks()
    plan_checks()
    style_checks()
    goal_checks()
    output_checks()
    print()
    if FAILED:
        print(f"{len(FAILED)} failed: {', '.join(FAILED)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
