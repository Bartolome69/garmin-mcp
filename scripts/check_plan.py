"""Run the plan checker over a plan saved as JSON, to see what it says about real AI-written plans.

    .venv/bin/python scripts/check_plan.py plan.json
    .venv/bin/python scripts/check_plan.py plan.json --history 26,28,25,29 --longest 12
    .venv/bin/python scripts/check_plan.py plans/*.json --json

A plan is either create_plan's arguments ({"goal", "sessions", "method",
"milestones", ...}, as Claude or ChatGPT wrote them) or a get_plan answer
(with "weeks"). Nothing reaches Garmin: the sessions are built the way
create_plan builds them, with the first session's date taken as today so an
old plan still checks, and a "z2"-style target is dropped rather than looked
up, since it changes nothing the checker reads. --history is the last 4
weeks' km before the plan, oldest first, and --longest the longest recent
run; without them the starting checks are skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from garmin_mcp import plan_checks, training_plan, zones  # noqa: E402
from garmin_mcp.formatting import DateError, parse_date  # noqa: E402


def _without_zones(steps: Any) -> Any:
    if not isinstance(steps, list):
        return steps
    out = []
    for step in steps:
        if isinstance(step, dict):
            step = {k: v for k, v in step.items() if not (k in ("hr", "power") and zones.is_zone(v))}
            if isinstance(step.get("steps"), list):
                step["steps"] = _without_zones(step["steps"])
        out.append(step)
    return out


def _first_day(sessions: list[Any]) -> date:
    days = []
    for s in sessions:
        try:
            days.append(date.fromisoformat(parse_date((s or {}).get("date"), default_today=False)))
        except (DateError, AttributeError, TypeError, ValueError):
            continue
    return min(days) if days else date.today()


def check(plan: dict[str, Any], history: list[float] | None, longest: float | None) -> list[dict[str, Any]]:
    if "weeks" in plan and "sessions" not in plan:
        return plan_checks.check_answer(plan)
    sessions = [{**s, "steps": _without_zones(s.get("steps"))} if isinstance(s, dict) else s
                for s in plan.get("sessions") or []]
    today = _first_day(sessions)
    _, prepared = training_plan.prepare(
        plan.get("goal") or "Plan", sessions, plan.get("label"), today,
        name=plan.get("name"), method=plan.get("method"), milestones=plan.get("milestones"),
    )
    monday = training_plan.monday_of(today)
    weeks = [{"starts": (monday - timedelta(weeks=len(history) - i)).isoformat(), "run_km": km, "runs": 0}
             for i, km in enumerate(history)] if history else None
    return plan_checks.check_prepared(
        prepared, goal=plan.get("goal"), method=plan.get("method"), milestones=plan.get("milestones"),
        history=weeks, longest_recent_km=longest,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the plan checker over plans saved as JSON.")
    parser.add_argument("plans", nargs="+", type=Path, help="create_plan arguments or a get_plan answer, as JSON")
    parser.add_argument("--history", help="the last 4 weeks' km before the plan, oldest first: 26,28,25,29")
    parser.add_argument("--longest", type=float, help="the longest recent run, in km")
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    args = parser.parse_args()
    history = [float(x) for x in args.history.split(",")] if args.history else None

    failed = 0
    results: dict[str, Any] = {}
    for path in args.plans:
        try:
            plan = json.loads(path.read_text())
            # A tool call as logged: the arguments under "input" or "arguments".
            plan = plan.get("input") or plan.get("arguments") or plan
            found = check(plan, history, args.longest)
        except (OSError, ValueError, training_plan.PlanError) as exc:
            failed += 1
            results[str(path)] = {"error": str(exc)}
            if not args.json:
                print(f"{path}: couldn't check it: {exc}\n")
            continue
        results[str(path)] = found
        if args.json:
            continue
        print(f"{path}: {len(found)} check{'s' if len(found) != 1 else ''}"
              f" ({plan_checks.style(plan.get('method'))} limits)")
        for c in found:
            print(f"  week {c['week']}, {c['date']}  [{c['rule']}]  {c['text']}")
        print()
    if args.json:
        print(json.dumps(results if len(args.plans) > 1 else next(iter(results.values())), indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
