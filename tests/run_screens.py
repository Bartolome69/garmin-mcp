"""The run screen's answer for four runs (Nov 24), from the server's own code.

Writes tests/fixtures/run_screens.json for tests/app_run_strides_test.js, which
draws each in a browser. run_view_test.py checks the file matches this code, so
the browser test can't pass on an answer the server no longer gives.

  a  Bart's 24 Nov run: 8 km easy + strides (7.5 km, 6 x 20 s, each recovery to
     the lap button), run to 10.4 km, the strides lapped by hand after the plan
  b  Bart's 11 Nov run: 10 km easy + strides, the strides run as the workout's steps
  c  an easy run with no plan and no strides
  d  a 12 km easy plan stopped at 9.1 km

Run: python tests/run_screens.py   (rewrites the fixture)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("GARMIN_MCP_SECRET", "x" * 43 + "=")

from garmin_mcp import analysis  # noqa: E402
from garmin_mcp.app import run_header  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "run_screens.json"
HR = {"targetType": {"workoutTargetTypeKey": "heart.rate.zone"}, "targetValueOne": 120, "targetValueTwo": 145}


def step(order, key, cond, value=None, target=None):
    out = {"type": "ExecutableStepDTO", "stepOrder": order, "stepType": {"stepTypeKey": key},
           "endCondition": {"conditionTypeKey": cond}}
    if value is not None:
        out["endConditionValue"] = value
    return {**out, **(target or {})}


def strides_plan(easy_m):
    return {"workoutSegments": [{"workoutSteps": [
        step(1, "interval", "distance", easy_m, HR),
        {"type": "RepeatGroupDTO", "stepOrder": 2, "numberOfIterations": 6, "workoutSteps": [
            step(1, "interval", "time", 20), step(2, "recovery", "lap.button")]}]}]}


def lap(m, s, hr, mark="ACTIVE"):
    return {"distance": m, "duration": s, "averageHR": hr, "intensityType": mark}


LAPS_24 = [lap(m, t, h, "INTERVAL") for m, t, h in (
    (1000, 319, 110), (1000, 313, 118), (1000, 309, 120), (1000, 309, 122), (1000, 304, 125), (1000, 304, 122),
    (1000, 299, 127), (1000, 303, 127), (1000, 305, 130), (140, 43, 117), (80, 18, 117), (140, 47, 133),
    (100, 22, 130), (90, 33, 133), (100, 23, 138), (90, 33, 145), (10, 19, 138), (210, 52, 125), (40, 29, 130),
    (110, 24, 132), (50, 38, 142), (100, 20, 124), (40, 33, 132), (120, 27, 122), (1, 1, 136))]
LAPS_11 = []
for row in [(1000, 320, 111), (1000, 303, 121), (1000, 293, 124), (1000, 296, 129), (1000, 295, 127),
            (1000, 294, 132), (1000, 292, 133), (1000, 293, 132), (930, 277, 130),
            (101.5, 20, 141, "RECOVERY", 190, 62, 140), (90.9, 20, 133, "RECOVERY", 130, 44, 136),
            (90.5, 20, 129, "RECOVERY", 120, 43, 136), (102.0, 20, 134, "RECOVERY", 150, 53, 141),
            (75.8, 20, 135, "RECOVERY", 120, 38, 135), (93.9, 20, 133, "RECOVERY", 10, 4, 145)]:
    LAPS_11.append(lap(*row[:3]))
    if len(row) > 3:
        LAPS_11.append(lap(row[4], row[5], row[6], row[3]))
PLAIN = [lap(1000, s, h) for s, h in ((330, 118), (322, 124), (318, 127), (320, 129), (316, 131), (319, 130), (314, 134))] \
    + [lap(420, 132, 133)]
SHORT = [lap(1000, s, h) for s, h in ((312, 121), (305, 128), (303, 131), (301, 133), (304, 134), (302, 135),
                                     (300, 136), (299, 138), (303, 137))] + [lap(100, 31, 136)]

CASES = {
    "a": ("TUE 24 NOV · 06:33 · EASY", "8km easy + strides", "easy", strides_plan(7500), LAPS_24),
    "b": ("WED 11 NOV · 07:02 · EASY", "10km easy + strides", "easy", strides_plan(9500), LAPS_11),
    "c": ("SAT 21 NOV · 08:10 · RUN", "Tower Hamlets Running", None, None, PLAIN),
    "d": ("THU 19 NOV · 18:20 · EASY", "12km easy", "easy",
          {"workoutSegments": [{"workoutSteps": [step(1, "interval", "distance", 12000, HR)]}]}, SHORT),
}


def screens() -> dict:
    out = {}
    for key, (kicker, name, kind, workout, laps) in CASES.items():
        result = analysis.analyse(workout, {"lapDTOs": laps}, kind, None, story=True)
        title, planned_line = run_header(name, kind, result.pop("extra", None), result.get("planned_and_run"))
        result.pop("figures", None)
        out[key] = {"activity_id": 9000 + ord(key), "date": "2026-11-24", "kicker": kicker, "name": title,
                    "planned_line": planned_line, "type": kind or "run", "type_label": "Easy" if kind else "Run",
                    "colour": "easy", "credit": "Garmin Forerunner 965", "assistant": "Claude",
                    "ask": {"claude": "https://claude.ai/new?q=x", "chatgpt": "https://chatgpt.com/?q=x"}, **result}
    return out


if __name__ == "__main__":
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps(screens(), indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {FIXTURE.relative_to(ROOT)}")
