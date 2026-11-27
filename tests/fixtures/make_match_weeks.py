"""Weeks for tests/app_match_test.js, made by the server's own code (Nov 27).

    .venv/bin/python tests/fixtures/make_match_weeks.py

Bart's week of 23 Nov, two ways, as app._week_view answers it:

- open (Thursday): Tuesday's session run on Monday, a day early (a line);
  Wednesday a run that could be two sessions (a question); Thursday 7.8 of
  12 km, chosen over the strides, with the rest still to decide.
- decided (Saturday): the same week once the rest of Thursday went onto
  Sunday (+2 km), Wednesday's run counted by hand as Wednesday's session,
  and Saturday's easy 6 km run that day (no line).

Writes tests/fixtures/match_week_open.json and match_week_decided.json.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ["GARMIN_MCP_DB"] = str(Path(tempfile.mkdtemp()) / "fixtures.sqlite3")

from garmin_mcp import app, training_plan  # noqa: E402

MON = date(2026, 11, 23)
TUE, WED, THU, FRI, SAT, SUN = (date(2026, 11, d) for d in range(24, 30))


def item(sid, day, title, wid):
    return {"id": sid, "date": day.isoformat(), "workoutId": wid, "title": title, "sportTypeKey": "running",
            "itemType": "workout"}


def run(aid, day, km, name="Lunch run"):
    return {"activityId": aid, "startTimeLocal": f"{day.isoformat()} 12:30:00", "activityName": name,
            "activityType": {"typeKey": "running"}, "distance": km * 1000, "duration": km * 5.5 * 60}


ITEMS = [item(100, TUE, "Easy 8 km", 10), item(104, WED, "Easy 8 km", 14), item(101, THU, "Easy 12 km", 11),
         item(105, THU, "Strides 4 km", 15), item(102, FRI, "9 km run club", 12), item(106, SAT, "Easy 6 km", 16),
         item(103, SUN, "Long run 20 km", 13)]
METRES = {10: 8000, 14: 8000, 11: 12000, 15: 4000, 12: 9000, 16: 6000, 13: 20000}
# Monday's run did Tuesday's session a day early; Wednesday's could be Wednesday's
# session or Friday's run club; Thursday's 7.8 km is a partial of the easy 12 km,
# chosen over the strides; Saturday's run is Saturday's, same day.
RUNS = [run(5, MON, 8.0), run(3, WED, 8.1, "9 km run club"), run(1, THU, 7.8), run(6, SAT, 6.3)]


def week(today, ctx):
    plan = training_plan.summarise_calendar(
        ITEMS, RUNS, today, planned_metres=METRES, planned_seconds={k: v / 1000 * 330 for k, v in METRES.items()},
        weekly_km=True, efforts={}, matching=ctx)
    weeks = {w["starts"]: w for w in plan.get("weeks") or [] if w.get("starts")}
    return {"week": app._week_view(plan, weeks, None, MON, today), "block": None, "assistant": "Claude",
            "ask": {"claude": "https://claude.ai/new?q=x"}}


here = Path(__file__).resolve().parent
open_week = week(THU, {"choices": {}})
decided = week(SAT, {"choices": {"3": "104"},
                     "followups": {"101": {"choice": "add", "onto": "103", "add_km": 2.0}}})
(here / "match_week_open.json").write_text(json.dumps(open_week, indent=1))
(here / "match_week_decided.json").write_text(json.dumps(decided, indent=1))
for name, w in (("open", open_week), ("decided", decided)):
    print(name)
    for d in w["week"]["days"]:
        for r in d["sessions"]:
            print(f"  {d['dow']} {r.get('name'):18} {r.get('state'):6} line={r.get('match_line')!r} "
                  f"of={r.get('of_line')!r} tone={r.get('out_tone')} q={bool(r.get('question'))} "
                  f"follow={bool(r.get('followup'))} from={r.get('from_note')!r}")
