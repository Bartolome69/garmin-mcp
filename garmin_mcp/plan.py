"""Render the training plan as a page: what was scheduled, what was run.

Garmin is the source of truth. Sessions the coach schedules through
create_workout land on the Garmin calendar, and completed runs land as
activities — both keyed to a date, so planned against actual falls out without
a second database.

The only thing kept locally is what Garmin has no idea about: a weekly mileage
target, and the random path segment the page is published under.

    .venv/bin/python -m garmin_mcp.plan
"""

from __future__ import annotations

import html
import json
import os
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .formatting import duration, km, pace_per_km
from .session import session

PROJECT = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("GARMIN_MCP_PLAN_CONFIG") or PROJECT / "plan-config.json")

WEEKS_BACK = 5
WEEKS_FORWARD = 2
WEEK_STRIP = 14
MONTH_STRIP = 6
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Used to turn a time-based planned step into a distance when the step carries
# no pace target. Roughly an easy pace; only affects the height of a planned bar.
DEFAULT_MPS = 3.2


def category(type_key: str) -> str:
    """Runs, strength, and everything else — the three colours on the chart."""
    key = (type_key or "").lower()
    if "running" in key:
        return "run"
    if "strength" in key or "training" in key and "running" not in key:
        return "strength"
    return "other"


def planned_distance_m(workout: dict[str, Any]) -> float:
    """Estimate how far a scheduled workout is, by walking its steps.

    Garmin's calendar carries no distance for a planned session, so it has to be
    derived. Distance steps count directly; time steps are converted using their
    own pace target where they have one.
    """
    total = 0.0

    def walk(steps: list[dict[str, Any]]) -> None:
        nonlocal total
        for step in steps or []:
            if step.get("type") == "RepeatGroupDTO":
                iterations = int(step.get("numberOfIterations") or 1)
                before = total
                walk(step.get("workoutSteps") or [])
                total += (total - before) * (iterations - 1)
                continue
            condition = (step.get("endCondition") or {}).get("conditionTypeKey")
            value = float(step.get("endConditionValue") or 0)
            if condition == "distance":
                total += value
            elif condition == "time":
                one, two = step.get("targetValueOne"), step.get("targetValueTwo")
                target = (step.get("targetType") or {}).get("workoutTargetTypeKey")
                speed = (one + two) / 2 if target == "pace.zone" and one and two else DEFAULT_MPS
                total += value * speed

    segments = workout.get("workoutSegments") or []
    for segment in segments:
        walk(segment.get("workoutSteps") or [])
    return total


def load_config() -> dict[str, Any]:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    config = {"weekly_target_km": 50, "path_token": secrets.token_urlsafe(9)}
    CONFIG.write_text(json.dumps(config, indent=2) + "\n")
    return config


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def collect() -> dict[str, Any]:
    """Pull the calendar and the activities covering the window."""
    today = date.today()
    history_start = monday_of(today) - timedelta(weeks=max(WEEK_STRIP, MONTH_STRIP * 5))
    start = monday_of(today) - timedelta(weeks=WEEKS_BACK)
    end = monday_of(today) + timedelta(weeks=WEEKS_FORWARD + 1) - timedelta(days=1)

    activities = session.run(
        lambda c: c.get_activities_by_date(history_start.isoformat(), today.isoformat())
    ) or []

    # The calendar is fetched per month, so cover every month the window touches.
    months, cursor = [], start.replace(day=1)
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    planned: list[dict[str, Any]] = []
    for year, month in months:
        try:
            payload = session.run(lambda c, y=year, m=month: c.get_scheduled_workouts(y, m))
        except Exception:
            continue
        items = payload if isinstance(payload, list) else (payload or {}).get("calendarItems", [])
        planned.extend(i for i in items if i.get("itemType") == "workout")

    # Planned sessions need their distance looked up once each.
    distances: dict[int, float] = {}
    durations: dict[int, float] = {}
    for item in planned:
        workout_id = item.get("workoutId")
        if not workout_id or workout_id in distances:
            continue
        try:
            detail = session.run(lambda c, w=workout_id: c.get_workout_by_id(w)) or {}
            distances[workout_id] = planned_distance_m(detail)
            durations[workout_id] = float(detail.get("estimatedDurationInSecs") or 0)
        except Exception:
            distances[workout_id] = durations[workout_id] = 0.0

    return {"start": start, "history_start": history_start, "end": end,
            "today": today, "activities": activities,
            "planned": planned, "planned_distance": distances,
            "planned_duration": durations}


def organise(data: dict[str, Any]) -> dict[str, Any]:
    """Shape into sport totals, a week progression, and days of paired bars.

    Bars are sized by duration, not distance. Strength work has no distance, so
    a mileage bar could never show it honestly; time is the one unit every kind
    of session has.
    """
    actual: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    metres: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)

    for item in data["activities"]:
        day = (item.get("startTimeLocal") or "")[:10]
        if not day:
            continue
        kind = category((item.get("activityType") or {}).get("typeKey"))
        actual[day][kind] += float(item.get("duration") or 0)
        if kind == "run":
            metres[day] += float(item.get("distance") or 0)
            counts[day] += 1

    planned_secs: dict[str, float] = defaultdict(float)
    planned_m: dict[str, float] = defaultdict(float)
    for item in data["planned"]:
        day = item.get("date")
        if not day:
            continue
        planned_secs[day] += data["planned_duration"].get(item.get("workoutId"), 0.0)
        planned_m[day] += data["planned_distance"].get(item.get("workoutId"), 0.0)

    today = data["today"]
    this_monday = monday_of(today)
    week_keys = [(this_monday + timedelta(days=d)).isoformat() for d in range(7)]

    sports = []
    for kind, label in (("run", "Running"), ("strength", "Strength"), ("other", "Other")):
        done = sum(actual[k].get(kind, 0.0) for k in week_keys)
        plan = sum(planned_secs[k] for k in week_keys) if kind == "run" else 0.0
        if done or plan:
            sports.append({"kind": kind, "label": label,
                           "done": duration(done) or "0s",
                           "planned": duration(plan) if plan else None})

    # -- week progression -------------------------------------------------
    progression = []
    week_start = this_monday - timedelta(weeks=WEEK_STRIP - 1)
    while week_start <= this_monday + timedelta(weeks=WEEKS_FORWARD):
        keys = [(week_start + timedelta(days=d)).isoformat() for d in range(7)]
        by_kind = {
            kind: sum(actual[k].get(kind, 0.0) for k in keys)
            for kind in ("run", "strength", "other")
        }
        progression.append({
            "start": week_start,
            "km": round(sum(metres[k] for k in keys) / 1000, 1),
            "planned_km": round(sum(planned_m[k] for k in keys) / 1000, 1),
            "planned_secs": sum(planned_secs[k] for k in keys),
            "by_kind": {k: v for k, v in by_kind.items() if v > 0},
            "secs": sum(by_kind.values()),
            "current": week_start == this_monday,
            "future": week_start > this_monday,
        })
        week_start += timedelta(weeks=1)

    # -- day detail -------------------------------------------------------
    detail = []
    cursor = data["start"]
    while cursor <= data["end"]:
        days, done_m, plan_m, done_s, plan_s = [], 0.0, 0.0, 0.0, 0.0
        for offset in range(7):
            day = cursor + timedelta(days=offset)
            key = day.isoformat()
            bars = {k: v for k, v in actual.get(key, {}).items() if v > 0}
            days.append({
                "date": day,
                "is_today": day == today,
                "bars": bars,
                "actual_secs": sum(bars.values()),
                "planned_secs": planned_secs.get(key, 0.0),
            })
            done_m += metres[key]
            plan_m += planned_m[key]
            done_s += sum(bars.values())
            plan_s += planned_secs[key]
        detail.append({
            "start": cursor, "days": days,
            "actual_km": round(done_m / 1000, 1), "planned_km": round(plan_m / 1000, 1),
            "actual_time": duration(done_s) or "—", "planned_time": duration(plan_s),
            "future": cursor > this_monday,
            "current": cursor == this_monday,
        })
        cursor += timedelta(weeks=1)

    summary = {
        "km": round(sum(metres[k] for k in week_keys) / 1000, 1),
        "time": duration(sum(sum(actual[k].values()) for k in week_keys)) or "0s",
        "runs": sum(counts[k] for k in week_keys),
    }
    return {"sports": sports, "progression": progression, "detail": detail,
            "summary": summary}


def render(model: dict[str, Any], target: float) -> str:
    summary = model["summary"]

    sports = "".join(
        f'<div class="sport"><i class="dot {s["kind"]}"></i><div>'
        f'<b>{s["done"]}</b>'
        f'{f"<span>{s['planned']} planned</span>" if s["planned"] else ""}'
        f'<em>{s["label"]}</em></div></div>'
        for s in model["sports"]
    ) or '<p class="sub">Nothing recorded this week yet.</p>'

    # -- weekly volume, stacked by type ------------------------------------
    prog = model["progression"]
    top = max([w["secs"] for w in prog] + [w["planned_secs"] for w in prog] + [3600.0])
    top = ((int(top // 1800) + 1) * 1800)  # round up to the next half hour

    columns = []
    for week in prog:
        if week["future"]:
            height = week["planned_secs"] / top * 100
            stack = f'<i class="wb plan" style="height:100%"></i>' if height else ""
            inner = (
                f'<div class="wstack" style="height:{height:.1f}%">{stack}</div>'
                if height else '<div class="wstack" style="height:0"></div>'
            )
        else:
            segs = "".join(
                f'<i class="wb {kind}" style="flex:{secs:.0f}"></i>'
                for kind, secs in sorted(week["by_kind"].items())
            )
            inner = (
                f'<div class="wstack" style="height:{week["secs"] / top * 100:.1f}%">'
                f'{segs}</div>'
            )
        columns.append(
            f'<div class="wcol{" on" if week["current"] else ""}" '
            f'title="{duration(week["secs"]) or "nothing"} · {week["km"]} km">'
            f'<div class="wtrack">{inner}</div>'
            f'<span>{week["start"].strftime("%-d/%-m")}</span></div>'
        )
    bars = "".join(columns)
    grid = "".join(
        f'<div class="gl" style="bottom:{pct}%"><span>{int(top * pct / 100 // 3600)}h</span></div>'
        for pct in (0, 50, 100)
    )

    # -- day pills, one week at a time -------------------------------------
    peak = max(
        [d["actual_secs"] for w in model["detail"] for d in w["days"]]
        + [d["planned_secs"] for w in model["detail"] for d in w["days"]]
        + [3600.0]
    )
    # Newest first, so the current week is the one you land on.
    weeks_desc = list(reversed(model["detail"]))
    current_index = next(
        (i for i, w in enumerate(weeks_desc) if w["current"]), 0
    )

    panels = []
    for index, week in enumerate(weeks_desc):
        cells = []
        for day in week["days"]:
            pills = ""
            if day["actual_secs"]:
                segments = "".join(
                    f'<i class="seg {k}" style="flex:{v:.0f}"></i>'
                    for k, v in sorted(day["bars"].items())
                )
                pills += (
                    f'<div class="pill" style="height:{day["actual_secs"]/peak*100:.1f}%" '
                    f'title="{duration(day["actual_secs"])}">{segments}</div>'
                )
            if day["planned_secs"]:
                pills += (
                    f'<div class="pill plan" style="height:{day["planned_secs"]/peak*100:.1f}%" '
                    f'title="planned {duration(day["planned_secs"])}"></div>'
                )
            cells.append(
                f'<div class="day{" today" if day["is_today"] else ""}">'
                f'<div class="pills">{pills}</div>'
                f'<b>{day["date"].strftime("%-d")}</b>'
                f'<span>{DAYS[day["date"].weekday()][:2].upper()}</span></div>'
            )
        label = "This week" if week["current"] else week["start"].strftime("%-d %B")
        panels.append(
            f'<div class="panel" data-index="{index}" hidden>'
            f'<div class="phead"><h3>{label}</h3>'
            f'<div class="wsum"><b>{week["actual_km"]} km</b>'
            f'<span>{week["actual_time"]}'
            f'{f" · {week['planned_time']} planned" if week["planned_time"] else ""}'
            f'</span></div></div>'
            f'<div class="days">{"".join(cells)}</div></div>'
        )
    rows = "".join(panels)

    return (TEMPLATE
            .replace("{{KM}}", str(summary["km"]))
            .replace("{{TIME}}", summary["time"])
            .replace("{{RUNS}}", str(summary["runs"]))
            .replace("{{SPORTS}}", sports)
            .replace("{{WEEKBARS}}", bars)
            .replace("{{GRID}}", grid)
            .replace("{{ROWS}}", rows)
            .replace("{{START_INDEX}}", str(current_index))
            .replace("{{TARGET}}", f"{target:g}")
            .replace("{{GENERATED}}", datetime.now().strftime("%-d %B, %H:%M")))


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Training plan</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{color-scheme:light;--ground:#F1F4F1;--surface:#fff;--ink:#17211B;--muted:#67766C;
--line:#DCE3DC;--run:#2F6B4F;--strength:#4A80B8;--other:#D9A320;--plan:#C9D2CA;--accent:#BC5A2A}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--ground:#0D1310;--surface:#161F1A;
--ink:#E3ECE5;--muted:#93A599;--line:#25322A;--run:#63BC8E;--strength:#7FB3E0;--other:#E0B84A;
--plan:#36443A;--accent:#E08A5A}}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font:15px/1.5 Archivo,system-ui,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:36px 18px 70px}
h1{font-size:1.6rem;margin:0 0 3px;letter-spacing:-0.02em}
h2{font-size:0.64rem;font-family:"IBM Plex Mono",monospace;letter-spacing:.15em;
text-transform:uppercase;color:var(--muted);font-weight:500;margin:36px 0 12px}
.sub{color:var(--muted);font-size:0.86rem;margin:0}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px}

.stats{display:flex;gap:38px;flex-wrap:wrap;margin-bottom:18px}
.stats div span{display:block;font-family:"IBM Plex Mono",monospace;font-size:0.6rem;
letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.stats div b{font-size:1.9rem;font-weight:800;letter-spacing:-0.02em;line-height:1.1}
.sports{display:flex;gap:26px;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:16px}
.sport{display:flex;gap:9px;align-items:flex-start}
.dot{width:12px;height:12px;border-radius:4px;margin-top:5px;flex:none;background:var(--run)}
.dot.strength{background:var(--strength)}.dot.other{background:var(--other)}
.sport b{display:block;font-size:1.05rem;font-weight:700;line-height:1.2}
.sport span{display:block;font-family:"IBM Plex Mono",monospace;font-size:0.62rem;color:var(--muted)}
.sport em{display:block;font-style:normal;font-size:0.72rem;color:var(--muted);margin-top:1px}

/* week progression, with a scale so the heights mean something */
.prog{position:relative;padding:12px 44px 10px 14px}
.bars{display:flex;gap:5px;align-items:flex-end;position:relative;z-index:1}
.wcol{flex:1;display:flex;flex-direction:column;align-items:center;gap:7px;min-width:0}
.wtrack{width:100%;height:150px;display:flex;align-items:flex-end}
.wstack{width:100%;display:flex;flex-direction:column-reverse;border-radius:4px 4px 0 0;
overflow:hidden;min-height:2px}
.wb{display:block;width:100%;background:var(--run)}
.wb.strength{background:var(--strength)}
.wb.other{background:var(--other)}
.wb.plan{background:var(--plan)}
.wcol.on .wstack{outline:2px solid var(--accent);outline-offset:1px}
.wcol span{font-family:"IBM Plex Mono",monospace;font-size:0.55rem;color:var(--muted);white-space:nowrap}
.gl{position:absolute;left:14px;right:44px;border-top:1px dashed var(--line);height:0;
margin-bottom:27px}
.gl span{position:absolute;right:-38px;top:-8px;font-family:"IBM Plex Mono",monospace;
font-size:0.58rem;color:var(--muted)}

/* days: coloured actual beside grey planned, Runna-style */
.weeknav{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.weeknav button{width:32px;height:32px;flex:none;border-radius:9px;border:1px solid var(--line);
background:var(--surface);color:var(--ink);cursor:pointer;font-size:1rem;line-height:1}
.weeknav button:disabled{opacity:.35;cursor:default}
.weeknav button:hover:not(:disabled){background:var(--ground)}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:14px;
padding:14px 18px 12px}
.phead{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
margin-bottom:12px;flex-wrap:wrap}
.phead h3{font-size:0.9rem;font-weight:700;margin:0}
.wsum{text-align:right}
.wsum b{font-size:0.95rem;font-weight:700}
.wsum span{display:block;font-family:"IBM Plex Mono",monospace;font-size:0.58rem;color:var(--muted)}
.days{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.day{display:flex;flex-direction:column;align-items:center;gap:4px}
.pills{height:132px;display:flex;align-items:flex-end;justify-content:center;gap:4px;width:100%}
.pill{width:11px;border-radius:6px;overflow:hidden;display:flex;flex-direction:column-reverse;
background:var(--run);min-height:8px}
.pill.plan{background:var(--plan)}
.seg{display:block;width:100%;background:var(--run)}
.seg.strength{background:var(--strength)}
.seg.other{background:var(--other)}
.day b{font-size:0.8rem;font-weight:600}
.day span{font-family:"IBM Plex Mono",monospace;font-size:0.55rem;color:var(--muted)}
.day.today b{color:var(--accent)}
.key{margin-top:24px;font-size:0.78rem;color:var(--muted);display:flex;gap:18px;flex-wrap:wrap}
.key i{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:6px;
vertical-align:-1px}
@media(max-width:640px){.pills{height:104px}.wtrack{height:112px}.stats{gap:24px}
.stats div b{font-size:1.5rem}}
</style></head><body><div class="wrap">
<h1>Training plan</h1>
<p class="sub">Every chart is sized by time and coloured by activity. Grey is planned, colour is what you did. Updated {{GENERATED}}.</p>

<div class="card" style="margin-top:22px">
  <div class="stats">
    <div><span>Kilometres</span><b>{{KM}}</b></div>
    <div><span>Time</span><b>{{TIME}}</b></div>
    <div><span>Runs</span><b>{{RUNS}}</b></div>
  </div>
  <div class="sports">{{SPORTS}}</div>
</div>

<h2>Weekly volume</h2>
<div class="card prog">{{GRID}}<div class="bars">{{WEEKBARS}}</div></div>

<h2>Week by week</h2>
<div class="weeknav">
  <button id="prev" aria-label="Earlier week">&#8249;</button>
  <button id="next" aria-label="Later week">&#8250;</button>
  <span class="sub" style="font-size:0.78rem" id="navhint"></span>
</div>
<div id="panels">{{ROWS}}</div>
<script>
(function(){
  // Panels are newest-first, so "previous" moves back in time.
  var panels = Array.prototype.slice.call(document.querySelectorAll(".panel"));
  var prev = document.getElementById("prev");
  var next = document.getElementById("next");
  var hint = document.getElementById("navhint");
  var at = {{START_INDEX}};

  function show(i){
    at = Math.max(0, Math.min(panels.length - 1, i));
    panels.forEach(function(p, n){ p.hidden = n !== at; });
    prev.disabled = at >= panels.length - 1;
    next.disabled = at <= 0;
    hint.textContent = (at + 1) + " of " + panels.length;
  }
  prev.addEventListener("click", function(){ show(at + 1); });
  next.addEventListener("click", function(){ show(at - 1); });
  document.addEventListener("keydown", function(e){
    if (e.key === "ArrowLeft") show(at + 1);
    if (e.key === "ArrowRight") show(at - 1);
  });
  show(at);
})();
</script>

<div class="key">
  <span><i style="background:var(--run)"></i>running</span>
  <span><i style="background:var(--strength)"></i>strength</span>
  <span><i style="background:var(--other)"></i>other</span>
  <span><i style="background:var(--plan)"></i>planned</span>
</div>
</div></body></html>
"""


def build() -> dict[str, Any]:
    """Regenerate the page. Returns where it went and what it says."""
    config = load_config()
    target = float(config.get("weekly_target_km") or 50)
    token = config["path_token"]

    model = organise(collect())
    out = PROJECT / "docs" / "p" / token / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(model, target))

    planned_days = sum(
        1 for week in model["detail"] for day in week["days"] if day["planned_secs"]
    )
    return {
        "url": f"https://garmin.daash.run/p/{token}/",
        "file": str(out),
        "this_week": model["summary"],
        "weekly_target_km": target,
        "planned_sessions_in_view": planned_days,
    }


def main() -> int:
    result = build()
    print(f"wrote {result['file']}")
    print(f"published at: {result['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
