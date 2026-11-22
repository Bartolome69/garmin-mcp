"""The run screen after a run: the chart of the session as planned and as run,
and the lines on what it shows (Nov 11). Checked against two of Bart's real
runs: the Sub-T 3 x 3 km of 28 Oct and the 10 km easy + strides of 11 Nov.

Run: .venv/bin/python tests/run_view_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from garmin_mcp import analysis, run_view  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def lap(m, s, hr, mark="active"):
    return {"distance": m, "duration": s, "averageHR": hr, "intensityType": mark.upper()}


def step(order, key, cond, value=None, target=None):
    out = {"type": "ExecutableStepDTO", "stepOrder": order, "stepType": {"stepTypeKey": key},
           "endCondition": {"conditionTypeKey": cond}}
    if value is not None:
        out["endConditionValue"] = value
    return {**out, **(target or {})}


PACE = {"targetType": {"workoutTargetTypeKey": "pace.zone"}, "targetValueOne": 1000 / 238, "targetValueTwo": 1000 / 248}
HR = {"targetType": {"workoutTargetTypeKey": "heart.rate.zone"}, "targetValueOne": 120, "targetValueTwo": 145}

THRESHOLD = {"workoutSegments": [{"workoutSteps": [
    step(1, "warmup", "distance", 3000),
    {"type": "RepeatGroupDTO", "stepOrder": 2, "numberOfIterations": 3, "workoutSteps": [
        step(1, "interval", "distance", 3000, PACE), step(2, "recovery", "time", 90)]},
    step(3, "cooldown", "lap.button")]}]}
THRESHOLD_LAPS = {"lapDTOs": [lap(3000, 928, 122, "warmup"), lap(3000, 728, 157, "interval"), lap(240, 90, 149, "recovery"),
                              lap(3000, 730, 163, "interval"), lap(240, 90, 148, "recovery"), lap(3000, 731, 163, "interval"),
                              lap(300, 90, 133, "recovery"), lap(1350, 410, 145, "cooldown")]}

EASY = {"workoutSegments": [{"workoutSteps": [
    step(1, "interval", "distance", 9500, HR),
    {"type": "RepeatGroupDTO", "stepOrder": 2, "numberOfIterations": 6, "workoutSteps": [
        step(1, "interval", "time", 20), step(2, "recovery", "lap.button")]}]}]}
RUN = [(1000, 320, 111), (1000, 303, 121), (1000, 293, 124), (1000, 296, 129), (1000, 295, 127),
       (1000, 294, 132), (1000, 292, 133), (1000, 293, 132), (930, 277, 130),
       (101.5, 20, 141, 190, 62, 140), (90.9, 20, 133, 130, 44, 136), (90.5, 20, 129, 120, 43, 136),
       (102.0, 20, 134, 150, 53, 141), (75.8, 20, 135, 120, 38, 135), (93.9, 20, 133, 10, 4, 145)]
EASY_LAPS = []
for r in RUN:
    EASY_LAPS.append(lap(r[0], r[1], r[2]))
    if len(r) > 3:
        EASY_LAPS.append(lap(r[3], r[4], r[5], "recovery"))

print("A threshold session: its reps against their target")
t = analysis.analyse(THRESHOLD, THRESHOLD_LAPS, "threshold", story=True)
c = t.get("chart") or {}
check("drawn step by step, with the reps' shared target", c.get("mode") == "steps"
      and c.get("band") == {"kind": "pace", "lo": 238, "hi": 248, "text": "3:58–4:08"}, str(c.get("band")))
check("warm-up, three reps, the jogs and the cool-down, in order",
      [p["k"] for p in c.get("parts", [])] == ["easy", "work", "rec", "work", "rec", "work", "rec", "easy"],
      str([p["k"] for p in c.get("parts", [])]))
check("each rep marked on target", [p.get("on") for p in c["parts"] if p["k"] == "work"] == [True, True, True])
check("the caption says so, with each rep's pace", c.get("caption") == "All three reps inside the target: 4:03, 4:03 and 4:04.",
      c.get("caption"))
check("no lines that restate the table (Nov 14)", not t.get("insights"), str(t.get("insights")))
check("compared on its reps", t.get("figures", {}).get("reps") == 3 and t["figures"]["km"] == 9.0 and t["figures"]["hr"] == 161,
      str(t.get("figures")))

print("A rep off target")
slow = {"lapDTOs": [dict(x) for x in THRESHOLD_LAPS["lapDTOs"]]}
slow["lapDTOs"][5]["duration"] = 765  # rep 3 at 4:15
t2 = analysis.analyse(THRESHOLD, slow, "threshold", story=True)
check("the caption counts it out", (t2["chart"].get("caption") or "").startswith("Two of three reps inside the target"),
      t2["chart"].get("caption"))

print("An easy run with strides: km by km, heart rate against its target")
e = analysis.analyse(EASY, {"lapDTOs": EASY_LAPS}, "easy", story=True)
c = e.get("chart") or {}
kms = [p for p in c.get("parts", []) if p["k"] == "km"]
check("km by km, then the six strides", c.get("mode") == "km" and len(kms) == 9
      and len([p for p in c["parts"] if p["k"] == "stride"]) == 6, str(c.get("mode")))
check("the last part-km keeps its own pace (0.93 km at 4:58)", kms[-1]["p"] == 298, str(kms[-1]))
check("the heart-rate target is the band", c.get("band", {}).get("text") == "120–145")
check("caption: in the target from km 2, all strides done",
      c.get("caption") == "Heart rate in the target from km 2 on. All six strides done.", c.get("caption"))
check("no lines that restate the table", not e.get("insights"), str(e.get("insights")))
check("compared on the run, strides left out", e.get("figures", {}).get("km") == 8.9 and e["figures"].get("hr") == 126
      and not e["figures"].get("reps") and e["figures"].get("steady", {}).get("km") == 8.9, str(e.get("figures")))

print("A cap alone (Nov 17): written as 60-140, read as up to 140")
CAP = {"targetType": {"workoutTargetTypeKey": "heart.rate.zone"}, "targetValueOne": 60, "targetValueTwo": 140}
EASY_CAP = {"workoutSegments": [{"workoutSteps": [
    step(1, "interval", "distance", 9500, CAP),
    {"type": "RepeatGroupDTO", "stepOrder": 2, "numberOfIterations": 6, "workoutSteps": [
        step(1, "interval", "time", 20), step(2, "recovery", "lap.button")]}]}]}
capped = (analysis.analyse(EASY_CAP, {"lapDTOs": EASY_LAPS}, "easy", story=True).get("chart") or {})
check("the band reads up to 140, not 60-140", capped.get("band", {}).get("text") == "up to 140", str(capped.get("band")))
check("and the caption says it stayed under, with no floor to climb into",
      capped.get("caption") == "Heart rate stayed under 140 the whole way. All six strides done.", capped.get("caption"))

print("Nothing made up")
plain = analysis.analyse(None, THRESHOLD_LAPS, "threshold", story=True)
check("a hard session whose laps aren't matched to a workout gets no lines", not plain.get("insights"), str(plain.get("insights")))
check("but still its km chart", (plain.get("chart") or {}).get("mode") == "km")
check("the screen without story has none of it", "chart" not in analysis.analyse(THRESHOLD, THRESHOLD_LAPS, "threshold"))

print("A long run with a half-marathon-pace finish (Bart's of 14 Nov)")
HM = {"targetType": {"workoutTargetTypeKey": "pace.zone"}, "targetValueOne": 1000 / 226, "targetValueTwo": 1000 / 236}
LONG_HR = {"targetType": {"workoutTargetTypeKey": "heart.rate.zone"}, "targetValueOne": 125, "targetValueTwo": 148}
LONG = {"workoutSegments": [{"workoutSteps": [
    step(1, "interval", "distance", 16000, LONG_HR), step(2, "interval", "distance", 3000, HM),
    step(3, "cooldown", "distance", 1000)]}]}
LONG_LAPS = {"lapDTOs": [lap(16000, 4720, 138, "interval"), lap(3000, 693, 173, "interval"),
                         lap(1000, 295, 146, "cooldown"), lap(800, 242, 151)]}
lr = analysis.analyse(LONG, LONG_LAPS, "long", story=True)
rows = lr.get("planned_and_run") or []
check("the table marks the 3 km as the quicker stretch, not the 16 km",
      [bool(r.get("quick")) for r in rows][:3] == [False, True, False], str([(r.get("kind"), r.get("quick")) for r in rows]))
f = lr.get("figures") or {}
check("compared on its quicker stretch, with its steady 16 km kept apart",
      f.get("part") and f.get("km") == 3.0 and f.get("hr") == 173 and round(f["p"]) == 231
      and f.get("steady", {}).get("km") == 16.0 and f["steady"].get("hr") == 138, str(f))
splits = [{"km": str(i + 1), "seconds": 295, "pace": "4:55", "hr": 138} for i in range(16)] + \
         [{"km": str(17 + i), "seconds": 231, "pace": "3:51", "hr": 172} for i in range(3)] + \
         [{"km": "20", "seconds": 295, "pace": "4:55", "hr": 146}, {"km": "0.8", "partial": True, "seconds": 242, "pace": "5:02", "hr": 151}]
pairs = analysis._paired(analysis.planned_steps(LONG), analysis._laps(LONG_LAPS))[0]
c = run_view.chart(pairs, [], analysis._laps(LONG_LAPS), splits)
check("its heart-rate caption judges only the 16 km set by heart rate",
      c and c.get("caption") == "Heart rate in the target the whole way.", str(c and c.get("caption")))

print("Against the last one")
line = run_view.last_time({"part": True, "km": 3.0, "p": 231, "hr": 173}, {"part": True, "km": 3.0, "p": 235, "hr": 175},
                          "long run", "Long 18 km w 3 km HM", "31 Oct")
check("a long run's quicker stretch against the last one's",
      line and line["text"] == "Then 3:55 at 175 bpm over 3 km at the quicker pace. Today 3:51 at 173 bpm over 3 km "
      "at the quicker pace: quicker, for a lower heart rate.", str(line))
check("a whole run isn't compared: route and weather move it more than fitness",
      run_view.last_time({"km": 8.9, "p": 298, "hr": 126}, {"km": 11.4, "p": 305, "hr": 131}, "easy run", None, "7 Nov") is None)
check("nor a quicker stretch with a whole run",
      run_view.last_time({"part": True, "km": 3, "p": 231, "hr": 173}, {"km": 18, "p": 290, "hr": 140}, "long run", None, "1 Nov") is None)

print("Easy running against a month ago")
def act(km, pace_s, hr, label="AEROBIC_BASE", key="running", day="2026-10-10"):
    return {"activityType": {"typeKey": key}, "distance": km * 1000, "movingDuration": km * pace_s, "averageHR": hr,
            "trainingEffectLabel": label, "startTimeLocal": day + " 07:00:00"}
then = run_view.easy_then([act(8, 305, 140), act(10, 300, 139, day="2026-10-04"), act(6, 310, 141),
                           act(8, 240, 165, label="LACTATE_THRESHOLD"), act(3, 300, 135), act(10, 300, 138, key="treadmill_running")])
check("only easy outdoor runs of 4 km or more, as Garmin read them", then and then["runs"] == 3 and then["km"] == 24, str(then))
line = run_view.easy_trend({"km": 16.0, "p": 295, "hr": 138}, then, long_run=True)
check("quicker for a lower heart rate says so, with both sets of numbers",
      line and line["head"] == "Easy running is getting quicker."
      and line["text"] == "Your steady 16 km: 4:55 at 138 bpm. Your 3 easy runs 3 to 7 weeks ago averaged 5:04 at 140 bpm: "
      "9 s/km quicker, for a lower heart rate.", str(line))
check("level, or quicker for a higher heart rate, says nothing",
      run_view.easy_trend({"km": 10, "p": then["p"] - 2, "hr": then["hr"]}, then) is None
      and run_view.easy_trend({"km": 10, "p": then["p"] - 10, "hr": then["hr"] + 6}, then) is None)
check("slower at the same heart rate is said too",
      (run_view.easy_trend({"km": 10, "p": then["p"] + 8, "hr": then["hr"]}, then) or {}).get("head")
      == "Easy running is costing more than a month ago.")
check("fewer than three easy runs then says nothing", run_view.easy_then([act(8, 305, 140), act(8, 300, 139)]) is None)
check("a short easy run today says nothing", run_view.easy_trend({"km": 3, "p": 280, "hr": 130}, then) is None)

line = run_view.last_time({"reps": 3, "km": 9.0, "p": 243.2, "hr": 161}, {"reps": 8, "km": 8.0, "p": 244, "hr": 159},
                          "threshold session", "Sub-T 8 × 1 km", "22 Oct")
check("reps against reps, both figures said",
      line and line["head"] == "Against your last threshold session (Sub-T 8 × 1 km, 22 Oct)."
      and line["text"] == "Then 4:04 at 159 bpm over 8 km of reps. Today 4:03 at 161 bpm over 9 km of reps.", str(line))
check("reps aren't compared with a whole run", run_view.last_time({"reps": 3, "km": 9, "p": 243, "hr": 161},
                                                                  {"km": 10, "p": 300, "hr": 130}, "x", None, "1 Nov") is None)

# -- Nov 22: a 12 km easy run taken on to 14.2 km, Bart's of that day, 15 km laps --------------
print("a run longer than its plan")
EASY12 = {"workoutSegments": [{"workoutSteps": [step(1, "interval", "distance", 12000, HR)]}]}
KM_S = [305, 310, 311, 316, 314, 306, 303, 303, 286, 302, 292, 291, 286, 264, 45]
KM_HR = [120, 126, 133, 136, 136, 134, 138, 135, 143, 144, 144, 147, 144, 149, 159]
ON = {"lapDTOs": [lap(1000 if i < 14 else 160, sec, hr, "interval") for i, (sec, hr) in enumerate(zip(KM_S, KM_HR))]}
r = analysis.analyse(EASY12, ON, "easy", None, story=True)
rows = r.get("planned_and_run") or []
check("the 12 km step takes its own 12 km laps, not the 2.2 km run after it",
      [x["label"] for x in rows] == ["Run", "Ran on"] and rows[0]["hr"] == 136 and rows[0]["pace"] == "5:03"
      and rows[1]["note"] == "2.2 km after the workout", str(rows))
check("the extra is said from the rounded figures, so they add up",
      r.get("extra") == {"planned_km": 12.0, "ran_km": 14.2, "extra_km": 2.2}, str(r.get("extra")))
check("the verdict leads with it, then how the planned part kept to its target",
      r["verdict"] == "Ran 2.2 km longer than planned. Heart rate on target until km 12.", r["verdict"])
check("what was planned under the numbers the plan set: distance and heart rate, not time or pace",
      [x.get("plan") for x in r["stats"]] == ["12 planned", None, None, "120\u2013145 planned"], str(r["stats"]))
c = r.get("chart") or {}
check("the chart marks where the plan ended, after the 12th km bar, and ends at 14.2",
      c.get("plan_ends") == 12 and c.get("total_km") == 14.2, str({k: v for k, v in c.items() if k != "parts"}))
check("and its heart rate line is about the planned 12 km only", c.get("caption") == "Heart rate went over 145 in km 12.", str(c.get("caption")))

near = {"lapDTOs": [lap(1000, 305, 130, "interval") for _ in range(12)] + [lap(300, 90, 132, "interval")]}
r = analysis.analyse(EASY12, near, "easy", None, story=True)
check("300 m past the plan isn't extra: no line, no marker, the old verdict",
      not r.get("extra") and "plan_ends" not in (r.get("chart") or {}) and not r["verdict"].startswith("Ran "), r["verdict"])
check("but its axis still ends at the whole distance", (r.get("chart") or {}).get("total_km") == 12.3)
check("what was planned shows under the numbers on every run with a plan", r["stats"][0].get("plan") == "12 planned")

flat = {"lapDTOs": [lap(1000, 300, 150, "interval") for _ in range(14)]}
r = analysis.analyse(EASY12, flat, "easy", None, story=True)
check("over the target from the first km says so, not 'until km 1'",
      r["verdict"] == "Ran 2 km longer than planned. Heart rate over the target from the start.", r["verdict"])
calm = {"lapDTOs": [lap(1000, 300, 130, "interval") for _ in range(14)]}
r = analysis.analyse(EASY12, calm, "easy", None, story=True)
check("on target the whole plan says so", r["verdict"] == "Ran 2 km longer than planned. Heart rate on target for the planned 12 km.", r["verdict"])

untargeted = {"workoutSegments": [{"workoutSteps": [step(1, "interval", "distance", 12000)]}]}
r = analysis.analyse(untargeted, calm, "easy", None, story=True)
check("a plan with no target gives the averages instead",
      r["verdict"] == "Ran 2 km longer than planned. Averaged 5:00 /km at 130 bpm." and r["stats"][3].get("plan") is None, r["verdict"])

r = analysis.analyse(None, ON, "easy", None, story=True)
check("a run with no workout has no plan to compare: no extra, nothing planned under the numbers",
      not r.get("extra") and not any(x.get("plan") for x in r["stats"]), str(r["stats"]))

r = analysis.analyse(THRESHOLD, THRESHOLD_LAPS, "sub", None, story=True)
check("a session with a lap-button step has no planned distance to compare against", not r.get("extra"))
check("its reps' pace target shows under their pace", r["stats"][2].get("plan") == "3:58\u20134:08 planned", str(r["stats"]))

print()
if FAILED:
    print(f"{len(FAILED)} failed: " + "; ".join(FAILED))
    sys.exit(1)
print("all checks passed")
