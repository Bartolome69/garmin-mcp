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
heads = [x["head"] for x in t.get("insights") or []]
texts = " ".join(x["text"] for x in t.get("insights") or [])
check("lines on pacing, heart rate and recoveries", heads == ["Even pacing.", "Heart rate settled.", "Recoveries."], str(heads))
check("pacing: within 1 s, inside the target", "4:03, 4:03 and 4:04: all three within 1 s of each other, inside the target." in texts,
      texts)
check("heart rate: each rep's, and that it levelled off", "On the reps: 157, 163 and 163." in texts and "levelled off" in texts)
check("recoveries: their heart rate against the reps'", "In the 90 s recoveries heart rate averaged 148–149, against 157–163 on the reps."
      in texts, texts)
check("compared on its reps", t.get("figures", {}).get("reps") == 3 and t["figures"]["km"] == 9.0 and t["figures"]["hr"] == 161,
      str(t.get("figures")))

print("A rep off target")
slow = {"lapDTOs": [dict(x) for x in THRESHOLD_LAPS["lapDTOs"]]}
slow["lapDTOs"][5]["duration"] = 765  # rep 3 at 4:15
t2 = analysis.analyse(THRESHOLD, slow, "threshold", story=True)
check("the caption counts it out", (t2["chart"].get("caption") or "").startswith("Two of three reps inside the target"),
      t2["chart"].get("caption"))
check("and the line names it", t2["insights"][0]["head"] == "Off target." and "Rep 3 missed 3:58–4:08" in t2["insights"][0]["text"],
      str(t2["insights"][0]))

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
texts = [x["text"] for x in e.get("insights") or []]
check("easy stayed easy, with the warm-up km said", texts and texts[0] ==
      "Heart rate was in 120–145 from km 2 on, peaking at 133. Km 1 was 111 while you warmed up.", str(texts[:1]))
check("pace settled after two km", "5:20 and 5:03 for the first two km, then 4:52–4:58 for the rest." in texts, str(texts))
check("strides: the fastest and the slow one", "Fastest 3:16, on the fourth. The fifth was slower, at 4:24." in texts, str(texts))
check("compared on the run, strides left out", e.get("figures", {}).get("km") == 8.9 and e["figures"].get("hr") == 126
      and not e["figures"].get("reps"), str(e.get("figures")))

print("Nothing made up")
plain = analysis.analyse(None, THRESHOLD_LAPS, "threshold", story=True)
check("a hard session whose laps aren't matched to a workout gets no lines", not plain.get("insights"), str(plain.get("insights")))
check("but still its km chart", (plain.get("chart") or {}).get("mode") == "km")
check("the screen without story has none of it", "chart" not in analysis.analyse(THRESHOLD, THRESHOLD_LAPS, "threshold"))

print("Against the last one")
line = run_view.last_time({"reps": 3, "km": 9.0, "p": 243.2, "hr": 161}, {"reps": 8, "km": 8.0, "p": 244, "hr": 159},
                          "threshold session", "Sub-T 8 × 1 km", "22 Oct")
check("reps against reps, both figures said",
      line and line["head"] == "Against your last threshold session (Sub-T 8 × 1 km, 22 Oct)."
      and line["text"] == "Then 4:04 at 159 bpm over 8 km of reps. Today 4:03 at 161 bpm over 9 km of reps.", str(line))
line = run_view.last_time({"km": 8.9, "p": 298, "hr": 126}, {"km": 11.4, "p": 305, "hr": 131}, "easy run", None, "7 Nov")
check("quicker for a lower heart rate says so", line and line["text"].endswith("Today 4:58 at 126 bpm over 8.9 km: quicker, for a lower heart rate."),
      str(line))
check("reps aren't compared with a whole run", run_view.last_time({"reps": 3, "km": 9, "p": 243, "hr": 161},
                                                                  {"km": 10, "p": 300, "hr": 130}, "x", None, "1 Nov") is None)

print()
if FAILED:
    print(f"{len(FAILED)} failed: " + "; ".join(FAILED))
    sys.exit(1)
print("all checks passed")
