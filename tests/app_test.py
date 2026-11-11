"""The companion app's API and analysis, against the demo Garmin account.

The things worth proving: the app and Claude read one plan (an edit made in
the app is what get_plan answers next), an easier session changes only its
own day, the analysis numbers add up, and nobody reaches the API without a
token.

    .venv/bin/python tests/app_test.py
"""

from __future__ import annotations

import os
import re
import secrets
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(os.environ.get("TMPDIR", "/tmp")) / "garmin-app-test"
SCRATCH.mkdir(parents=True, exist_ok=True)

# The demo's week falls on fixed weekdays around today, and these checks
# follow its Monday interval session, so the clock is set to this week's
# Monday morning before anything reads it (Nov 3: run on a Tuesday, seven
# checks failed). time-machine is a test-only package, in the "test" extra:
#     uv pip install --python .venv/bin/python time-machine
# Without it, a run late in the week failed six checks (Nov 6).
try:
    import time_machine
    from datetime import date as _date, datetime as _datetime, timedelta as _td, timezone as _tz

    _monday = _date.today() - _td(days=_date.today().weekday())
    time_machine.travel(_datetime(_monday.year, _monday.month, _monday.day, 9, 0, tzinfo=_tz.utc), tick=True).start()
except ImportError:
    print("time-machine isn't installed, so this runs on today's date, and some checks only hold on a Monday:\n"
          "    uv pip install --python .venv/bin/python time-machine")

from cryptography.fernet import Fernet  # noqa: E402

# Named at random, not by the time: the clock above stands still on Monday.
os.environ["GARMIN_MCP_DB"] = str(SCRATCH / f"test-{secrets.token_hex(6)}.sqlite3")
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()
os.environ["GARMIN_MCP_OAUTH"] = "1"
os.environ["GARMIN_MCP_BASE_URL"] = "http://localhost"
# Analytics on, so the app's events can be checked; the network call is replaced below.
os.environ["GARMIN_MCP_POSTHOG_KEY"] = "phc_test_key"
os.environ.pop("DEMO_RAN", None)
os.environ.pop("DEMO_READINESS", None)

from garmin_mcp import session as session_mod  # noqa: E402
from tests.demo_garmin import DemoGarmin  # noqa: E402

DEMO = DemoGarmin()
session_mod.build_client = lambda **_: DEMO

import garmin_mcp.hosted as hosted  # noqa: E402
from garmin_mcp import analysis, analytics, oauth, server, store  # noqa: E402

SENT: list[dict] = []
analytics._post = SENT.append


def posted(event: str) -> list[dict]:
    """The events of one name sent so far; they go out on threads, so wait for them a moment."""
    time.sleep(0.3)
    return [e for e in SENT if e["event"] == event]
from garmin_mcp import app as app_routes  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


def plain(text: str) -> bool:
    """The copy rules: no exclamation marks, no em dashes, no congratulations."""
    return "!" not in text and "—" not in text and not re.search(r"congrat|well done|great job", text, re.I)


def analysis_checks() -> None:
    wid = next(w for w, up in DEMO.uploaded.items() if up["workoutName"].startswith("3 x 3 km"))
    aid = next(a for a, act in DEMO.demo_activities.items() if act["workoutId"] == wid)
    result = analysis.analyse(DEMO.get_workout_by_id(wid), DEMO.get_activity_splits(aid), "threshold")
    total = sum(s["seconds"] for s in result["splits"])
    check("splits add up to the total time", total == result["seconds"], f"{total} != {result['seconds']}")
    check("the time stat is the total", result["stats"][1]["v"] == analysis.clock(result["seconds"]))
    reps = [r for r in result["planned_and_run"] if r.get("rep")]
    laps = analysis._laps(DEMO.get_activity_splits(aid))
    rep_laps = [l for l in laps if l["mark"] == "active"]
    avg = analysis.pace(sum(l["seconds"] for l in rep_laps) / sum(l["metres"] for l in rep_laps) * 1000)
    check("three rep rows", len(reps) == 3, str(len(reps)))
    check("the rep pace stat is the reps' own average", result["stats"][2]["v"] == avg, f"{result['stats'][2]['v']} vs {avg}")
    check("the verdict is plain", plain(result["verdict"]), result["verdict"])
    check("the verdict counts the reps", "all three reps on target" in result["verdict"], result["verdict"])

    # A slow rep is a soft miss: amber, said in plain words, never red.
    slow = DEMO.get_activity_splits(aid)
    rep = next(l for l in slow["lapDTOs"] if l["intensityType"] == "ACTIVE")
    rep["duration"] = rep["movingDuration"] = rep["distance"] / 1000 * 260
    missed = analysis.analyse(DEMO.get_workout_by_id(wid), slow, "threshold")
    first = next(r for r in missed["planned_and_run"] if r.get("rep") == 1)
    check("a slow rep is a soft miss", first.get("soft") and first.get("miss") == "slow", str(first))
    check("its note says by how much", "slower than range" in first["note"], first["note"])
    check("the verdict says two of three", missed["verdict"].startswith("Two of three reps on target"), missed["verdict"])

    # Stopped before the cool-down: the steps run are compared, the rest say so.
    short = {"lapDTOs": DEMO.get_activity_splits(aid)["lapDTOs"][:-1]}
    stopped = analysis.analyse(DEMO.get_workout_by_id(wid), short, "threshold")
    rows = stopped.get("planned_and_run") or []
    check("a run stopped early still has its reps compared", sum(1 for r in rows if r.get("rep") and r.get("pace")) == 3)
    check("and says the cool-down wasn't run", rows and rows[-1]["label"] == "Cool down" and rows[-1]["note"] == "Not run",
          str(rows[-1:]))
    check("and its splits still add up", sum(s["seconds"] for s in stopped["splits"]) == stopped["seconds"])

    # A lap pressed by hand mid-rep shifts every lap after it: not guessed at.
    pressed = DEMO.get_activity_splits(aid)["lapDTOs"]
    rep = dict(pressed[1])
    halves = [dict(rep, distance=rep["distance"] / 2, duration=rep["duration"] / 2, movingDuration=rep["duration"] / 2)] * 2
    shifted = {"lapDTOs": pressed[:1] + halves + pressed[2:]}
    check("laps that don't line up put nothing against a target",
          not analysis.analyse(DEMO.get_workout_by_id(wid), shifted, "threshold").get("planned_and_run"))

    # Running on after the last step adds a lap; the steps still pair, and the
    # extra shows as its own row with no target against it.
    on = DEMO.get_activity_splits(aid)
    on = {"lapDTOs": on["lapDTOs"] + [{**on["lapDTOs"][-1], "distance": 460.0, "duration": 139.0, "movingDuration": 139.0}]}
    ran_on = analysis.analyse(DEMO.get_workout_by_id(wid), on, "threshold")
    rows = ran_on.get("planned_and_run") or []
    check("a run on past the workout still pairs its steps", len([r for r in rows if r.get("rep")]) == 3, str(rows))
    check("and the extra is its own row, untargeted",
          rows and rows[-1]["label"] == "Ran on" and not rows[-1].get("target") and "0.5 km" in rows[-1]["note"], str(rows[-1:]))
    check("and the splits still add up", sum(s["seconds"] for s in ran_on["splits"]) == ran_on["seconds"])

    real_run_checks()


def real_run_checks() -> None:
    """A real long run (Nov 1): 11 laps for a 10-step workout, and a recording whose kilometres differ."""
    from garmin_mcp.workouts import build_workout
    from tests.fake_garmin import _stream

    model, _, _ = build_workout("Long 19km w 4x2km sub-T", "running", [
        {"type": "warmup", "distance_meters": 5500},
        {"type": "repeat", "times": 4, "steps": [
            {"type": "interval", "distance_meters": 2000, "pace": ["4:00", "4:08"]},
            {"type": "recovery", "duration_seconds": 60}]},
        {"type": "cooldown", "distance_meters": 4500},
    ])
    workout = model.to_dict()
    laps = [("WARMUP", 5500, 1661, 127), ("ACTIVE", 2000, 485, 158), ("RECOVERY", 160, 60, 155),
            ("ACTIVE", 2000, 484, 163), ("RECOVERY", 160, 60, 161), ("ACTIVE", 2000, 484, 165),
            ("RECOVERY", 180, 60, 137), ("ACTIVE", 2000, 484, 164), ("RECOVERY", 170, 60, 161),
            ("COOLDOWN", 4500, 1340, 140), ("COOLDOWN", 460, 139, 144)]
    raw = {"lapDTOs": [{"lapIndex": i + 1, "intensityType": m, "distance": float(d), "duration": float(t),
                        "averageHR": h} for i, (m, d, t, h) in enumerate(laps)]}
    # The warm-up's kilometres at their own paces, as the watch recorded them.
    warm = [(1000.0, 313.0, 110.0, 118.0), (1000.0, 300.0, 124.0, 128.0), (1000.0, 299.0, 128.0, 130.0),
            (1000.0, 300.0, 129.0, 131.0), (1000.0, 298.0, 130.0, 132.0), (500.0, 151.0, 132.0, 134.0)]
    rest = [(float(d), float(t), float(h), float(h)) for _, d, t, h in laps[1:]]
    result = analysis.analyse(workout, raw, "threshold", _stream(warm + rest, step=1.0))
    rows = result.get("planned_and_run") or []
    check("a run that went on after the last step is still compared", len(rows) == 7, str([r["label"] for r in rows]))
    ran_on = rows[-1] if rows else {}
    check("the run on is its own row after the cool-down",
          ran_on.get("label") == "Ran on" and rows[-2]["label"] == "Cool down" and "0.5 km" in ran_on.get("note", ""),
          str(rows[-2:]))
    check("all four reps on target", result["verdict"].startswith("Hit all four reps on target"), result["verdict"])
    km = [s for s in result["splits"] if not s.get("rest") and not s.get("partial")]
    check("the warm-up's kilometres are each their own pace", len({s["pace"] for s in km[:5]}) > 1,
          str([s["pace"] for s in km[:5]]))
    check("the first is the slow one, as recorded", km[0]["pace"] == "5:13", km[0]["pace"])
    check("and the splits still add up to the run", sum(s["seconds"] for s in result["splits"]) == result["seconds"],
          f"{sum(s['seconds'] for s in result['splits'])} vs {result['seconds']}")



def easier_checks() -> None:
    wid = next(w for w, up in DEMO.uploaded.items() if up["workoutName"].startswith("3 x 3 km"))
    made = app_routes.easier(DEMO.get_workout_by_id(wid))
    check("easier keeps the plan code", made["workoutName"] == "3 x 2 km · HM", made["workoutName"])
    check("easier names the original", f"{app_routes.EASIER_MARK} {wid}." in made["description"])
    check("easier keeps the plan's own lines", "Plan HM:" in made["description"])
    steps = analysis.planned_steps(made)
    reps = [s for s in steps if s["rep"]]
    check("each rep is 2 km", all(s["amount"] == "2 km" for s in reps), str([s["amount"] for s in reps]))
    check("at the same pace", reps[0]["target"] == analysis.planned_steps(DEMO.get_workout_by_id(wid))[1]["target"])
    gym = next(w for w, up in DEMO.uploaded.items() if (up.get("sportType") or {}).get("sportTypeKey") == "strength_training")
    check("strength has no easier", app_routes.easier(DEMO.get_workout_by_id(gym)) is None)


def api_checks() -> None:
    email = "runner@example.test"
    user_token = store.save_user(session_mod.mask_email(email), '{"di_token": "x"}',
                                 email_hash=store.email_fingerprint(email), email=email)
    token = secrets.token_urlsafe(32)
    store.save_oauth_token(token, kind="access", client_id="test", subject=user_token, scopes="",
                           resource=None, expires_at=int(time.time()) + 3600)
    client = TestClient(hosted.build_app(), base_url="http://localhost")
    auth = {"Authorization": f"Bearer {token}"}

    check("no token, no plan", client.get("/app/api/today").status_code == 401)
    check("a wrong token, no plan", client.get("/app/api/today", headers={"Authorization": "Bearer nope"}).status_code == 401)
    check("the app's page is served", client.get("/app").status_code == 200)
    # Nothing this server answers belongs in search: the app, its API, the sign-in page.
    check("every page says noindex", all(client.get(path).headers.get("x-robots-tag") == "noindex, nofollow"
                                         for path in ("/app", "/app/api/today", "/")),
          str([client.get(path).headers.get("x-robots-tag") for path in ("/app", "/app/api/today", "/")]))
    check("so is its script", "text/javascript" in client.get("/app/app.js").headers.get("content-type", ""))
    check("a path can't climb out", all(Path(app_routes._file(bad).path).name == "index.html"
                                        for bad in ("../store.py", "../../../etc/passwd", "fonts/../../app.py")))
    check("the app's callback is a recognised redirect", oauth.recognised_redirect("http://localhost/app/callback"))

    today = client.get("/app/api/today", headers=auth).json()
    check("today counts down to the goal race, in the block after this one",
          today.get("headline", "").startswith("London Marathon in"), str(today.get("headline") or today.get("error")))
    check("and numbers the week across the whole plan", today.get("kicker", "").endswith("WEEK 4 OF 28"), today.get("kicker"))
    # The demo's sessions fall on fixed weekdays, so what's next depends on
    # the day the test runs (Nov 3: these checks only held on a Monday).
    on_today = any(d.get("today") and d.get("sessions") for d in today["week"]["days"])
    check("today's next session is today when there is one",
          (today.get("next") or {}).get("kicker") == "NEXT · TODAY" if on_today
          else (today.get("next") or {}).get("kicker", "").startswith("NEXT"), (today.get("next") or {}).get("kicker"))
    check("the week has seven days", len(today["week"]["days"]) == 7)
    check("ask opens Claude with a prompt", today["ask"]["claude"].startswith("https://claude.ai/new?q="))
    check("and offers the same chat in Claude's desktop app",
          today["ask"].get("claude_app", "").startswith("claude://claude.ai/new?q=")
          and today["ask"]["claude_app"].split("?q=")[1] == today["ask"]["claude"].split("?q=")[1])
    cal = today.get("calendar") or []
    check("the desktop calendar has last week, this week and next", len(cal) == 3 and cal[1].get("current") and cal[0].get("past"))
    check("each with seven days", all(len(w["days"]) == 7 for w in cal))
    chips = [c for w in cal for d in w["days"] for c in d["chips"]]
    check("done runs can't be dragged", not any(c.get("movable") for c in chips if c["state"] == "done"))
    check("days ahead can", any(c.get("movable") for c in cal[2]["days"][0]["chips"]))
    check("a run's distance has one decimal at most",
          all(len(c["v"].split(" ")[0].split(".")[-1]) <= 1 for c in chips if c.get("v") and "." in c["v"]))
    day = today["next"]["date"]
    norm = lambda n: str(n).replace("\u00d7", "x")

    def claude_reads(on: str) -> list[str]:
        """What get_plan, the tool Claude calls, has on a day."""
        import anyio
        from garmin_mcp import server

        async def read():
            with_session = session_mod.use_session(hosted.session_for(user_token))
            try:
                return await server.get_plan()
            finally:
                session_mod.reset_session(with_session)
        plan = anyio.run(read)
        return [s["name"] for w in plan["weeks"] for s in w["sessions"] if s["date"] == on]

    next_week = (__import__("datetime").date.fromisoformat(day) + __import__("datetime").timedelta(weeks=1)).isoformat()
    before_next_week = claude_reads(next_week)
    original = claude_reads(day)
    r = client.post(f"/app/api/session/{day}/easier", headers=auth, json={})
    check("easier answers", r.status_code == 200 and plain(r.json().get("message", "")), r.text[:200])
    eased = claude_reads(day)
    shown = client.get(f"/app/api/session/{day}", headers=auth).json()
    check("Claude reads the easier session, the one the app shows",
          eased and eased != original and norm(shown.get("name") or shown.get("title")) in [norm(n) for n in eased],
          f"{original} -> {eased}, app {shown.get('name') or shown.get('title')}")
    check("other weeks are untouched", claude_reads(next_week) == before_next_week)
    again = client.post(f"/app/api/session/{day}/easier", headers=auth, json={})
    check("easier twice is refused", again.status_code == 409)
    r = client.post(f"/app/api/session/{day}/undo", headers=auth, json={})
    check("undo answers", r.status_code == 200, r.text[:200])
    check("Claude reads the original again", claude_reads(day) == original, str(claude_reads(day)))

    later = (__import__("datetime").date.fromisoformat(day) + __import__("datetime").timedelta(days=4)).isoformat()
    r = client.post(f"/app/api/session/{day}/move", headers=auth, json={"to": later})
    check("move answers", r.status_code == 200, r.text[:200])
    moved_name = original[0]
    check("Claude reads it on the new day", moved_name in claude_reads(later) and moved_name not in claude_reads(day),
          f"{claude_reads(later)} / {claude_reads(day)}")
    r = client.post(f"/app/api/session/{day}/move", headers=auth, json={"to": "2020-01-01"})
    check("a move into the past is refused", r.status_code == 409)

    session = client.get(f"/app/api/session/{later}", headers=auth).json()
    check("the session screen has its steps", len(session.get("steps") or []) >= 1, str(session.get("error")))
    notes = [s[4] for w in range(1, 13) for s in __import__("tests.demo_garmin", fromlist=["_sessions"])._sessions(w)]
    check("its why line is the workout's note", (session.get("why") or "") in notes and session.get("why"), session.get("why"))

    plan = client.get("/app/api/plan", headers=auth).json()
    week, block = plan.get("week") or {}, plan.get("block") or {}
    check("the plan's week has seven days", len(week.get("days") or []) == 7, str(plan.get("error")))
    check("it is named by the block's week and phase", week.get("title") == "Week 4 \u00b7 Base", week.get("title"))
    check("the moved session is on its new day there",
          any(norm(r["name"]) == norm(moved_name) for d in week["days"] if d["date"] == later for r in d["sessions"]))
    check("done sessions can't be moved", not any(r.get("movable") for d in week["days"] for r in d["sessions"]
                                                    if r["state"] == "done"))
    earlier = client.get(f"/app/api/plan?week={week['previous']}", headers=auth).json()["week"]
    check("the week before is all done", all(r["state"] == "done" for d in earlier["days"] for r in d["sessions"]))
    last = client.get(f"/app/api/plan?week={block['bars'][-1]['starts']}", headers=auth).json()["week"]
    race = next((r for d in last["days"] for r in d["sessions"] if r["type"] == "race"), {})
    check("a race is red, as on the chat's plan card", race.get("colour") == "race", str(race)[:120])
    check("and so is its shape", all(g["colour"] in ("race", "easy", "rec") for g in race.get("segments") or [])
          and any(g["colour"] == "race" for g in race.get("segments") or []), str(race.get("segments"))[:120])
    check("and the raced part stands as tall as a hard rep, above the warm-up",
          all(g["h"] == 20 for g in race.get("segments") or [] if g["colour"] == "race")
          and all(g["h"] < 20 for g in race.get("segments") or [] if g["colour"] != "race"), str(race.get("segments"))[:160])
    check("the block chart runs the whole plan to race day, both blocks", len(block.get("bars") or []) == 28,
          str(len(block.get("bars") or [])))
    check("its title counts to race day", block.get("title") == "28 weeks to race day", block.get("title"))
    check("both blocks are named under it", len(block.get("blocks") or []) == 2)
    # Nov 6: Edit the plan took you to Today: its ask is the block's own.
    check("the block carries its own ask for Edit the plan", "claude.ai/new?q=" in (block.get("ask") or {}).get("claude", ""),
          str(block.get("ask"))[:120])
    far = client.get(f"/app/api/plan?week={block['bars'][24]['starts']}", headers=auth).json()["week"]
    check("a week past the calendar's horizon has its sessions", any(d["sessions"] for d in far["days"]), far.get("title"))
    check("and its number in the whole plan", far.get("title", "").startswith("Week 25"), far.get("title"))
    check("the block's verdict is plain", plain(block.get("verdict", "")) and block["verdict"].startswith("On track."),
          block.get("verdict"))
    check("the block's phases fill its weeks", sum(p["weeks"] for p in block["phases"]) == len(block["bars"]))
    shown = [b["n"] for b in block["bars"] if b.get("show_n")]
    check("no two-figure week numbers side by side", not any(b - a == 1 and b >= 10 for a, b in zip(shown, shown[1:]))
          and len(block["bars"]) in shown, str(shown))

    before = len(posted("app_viewed"))
    client.get("/app/api/crew", headers={**auth, "X-Daash-Ahead": "1"})
    check("a screen fetched ahead of a tap isn't counted as opened", len(posted("app_viewed")) == before)
    client.post("/app/api/event", headers=auth, json={"name": "viewed", "properties": {"screen": "crew", "ok": True}})
    late = posted("app_viewed")[before:]
    check("the tap that lands on it counts it, once, as preloaded",
          len(late) == 1 and late[0]["properties"].get("screen") == "crew" and late[0]["properties"].get("preloaded") is True,
          str([e["properties"] for e in late])[:160])
    check("a view naming no real screen is refused",
          client.post("/app/api/event", headers=auth, json={"name": "viewed", "properties": {"screen": "anything"}}).status_code == 400)
    # Opened from a link in a chat: where it was given, on the first screen.
    client.get("/app/api/plan", headers={**auth, "X-Daash-From": "chat_offer"})
    client.get("/app/api/plan", headers={**auth, "X-Daash-From": "<b>Not a source</b>"})
    time.sleep(0.5)
    viewed = posted("app_viewed")
    froms = [e["properties"].get("from") for e in viewed if e["properties"].get("from")]
    check("a screen opened from a chat's link says where the link was given", "chat_offer" in froms, str(froms))
    check("and nothing else passes for a source", froms == ["chat_offer"], str(froms))
    check("opening a screen is recorded, by name", any(e["properties"].get("screen") == "today" for e in viewed),
          str([e["properties"].get("screen") for e in viewed])[:120])
    check("with how long the screen waited", all(isinstance(e["properties"].get("seconds"), (int, float))
                                                 for e in viewed if not e["properties"].get("preloaded")),
          str([e["properties"].get("seconds") for e in viewed])[:120])
    check("against the same person as the connector's events",
          viewed and viewed[0]["properties"].get("account") == session_mod.mask_email(email))
    check("never with what the screen showed", not any(k in e["properties"] for e in viewed
                                                       for k in ("plan", "sessions", "pace", "headline")))
    edits = posted("app_edit")
    check("each edit is recorded, with how it was made",
          {e["properties"]["action"] for e in edits} >= {"easier", "undo", "move"}
          and all(e["properties"].get("via") == "button" for e in edits), str([e["properties"] for e in edits])[:160])
    client.post(f"/app/api/session/{later}/move", headers={**auth, "X-Daash-Layout": "desktop"},
                json={"to": later, "via": "drag"})
    check("a drag on the calendar says so, and the layout",
          any(e["properties"].get("via") == "drag" and e["properties"].get("layout") == "desktop" for e in posted("app_edit")))
    # Nov 10: opened from the home screen or a browser tab, on views, edits and page events.
    before_home = len(posted("app_viewed"))
    client.get("/app/api/today", headers={**auth, "X-Daash-Home": "1"})
    client.get("/app/api/today", headers={**auth, "X-Daash-Home": "yes"})
    client.post(f"/app/api/session/{later}/move", headers={**auth, "X-Daash-Home": "1"}, json={"to": later, "via": "hold"})
    client.post("/app/api/event", headers={**auth, "X-Daash-Home": "1"}, json={"name": "share_clicked", "properties": {"where": "run"}})
    time.sleep(0.5)
    homes = [e["properties"].get("home_screen") for e in posted("app_viewed")[before_home:]]
    check("a screen opened from the home screen says so, and only on the app's own header",
          homes == [True, None], str(homes))
    check("so does an edit made there, and a page event",
          posted("app_edit")[-1]["properties"].get("home_screen") is True
          and posted("app_share_clicked")[-1]["properties"].get("home_screen") is True)
    check("and a screen opened in a browser tab carries nothing for it",
          all("home_screen" not in e["properties"] for e in viewed), str([e["properties"] for e in viewed])[:120])
    r = client.post("/app/api/event", headers=auth, json={"name": "ask_clicked", "properties": {"to": "claude", "where": "today", "secret": "x"}})
    asked = posted("app_ask_clicked")
    check("Ask Claude pressed is recorded", r.status_code == 200 and asked and asked[-1]["properties"].get("to") == "claude")
    check("and only the properties it may carry", asked and "secret" not in asked[-1]["properties"])
    # Nov 6: Ask asks Back to my chat or New chat, and can remember a chat.
    client.post("/app/api/event", headers=auth, json={"name": "ask_chosen", "properties":
                {"to": "claude", "where": "today", "choice": "my_chat", "saved": "yes", "url": "https://claude.ai/chat/x"}})
    client.post("/app/api/event", headers=auth, json={"name": "ask_chat_saved", "properties": {"to": "chatgpt", "where": "plan"}})
    chosen, kept = posted("app_ask_chosen"), posted("app_ask_chat_saved")
    check("which way Ask went is recorded, never the chat's address",
          chosen and {k: chosen[-1]["properties"].get(k) for k in ("choice", "saved")} == {"choice": "my_chat", "saved": "yes"}
          and "url" not in chosen[-1]["properties"] and kept and kept[-1]["properties"].get("to") == "chatgpt",
          str([e["properties"] for e in chosen + kept])[:200])
    check("an unknown event is refused", client.post("/app/api/event", headers=auth, json={"name": "anything"}).status_code == 400)
    check("and nothing is recorded without a token", client.post("/app/api/event", json={"name": "installed"}).status_code == 401)

    alone = client.get("/app/api/crew", headers=auth).json()
    check("no crew, an offer to start one", alone.get("none") is True and "claude" in alone.get("ask", {}), str(alone)[:120])
    from garmin_mcp import crews
    from tests.demo_garmin import seed_crew

    seed_crew(crews.member_key(user_token, store.email_fingerprint(email)))
    crew = client.get("/app/api/crew", headers=auth).json()
    check("the crew screen answers", not crew.get("error") and crew.get("people"), str(crew.get("error")))
    check("a row for each of the six", len(crew["people"]) == 6)
    check("you first", crew["people"][0]["name"] == "You")
    check("someone added by hand is marked", any(p.get("by_hand") for p in crew["people"]))
    check("the main match has you in it", (crew.get("hero") or {}).get("names", "").startswith("You"))
    check("WhatsApp opens with the question and the link",
          "wa.me/?text=" in crew["hero"]["whatsapp"] and "What%20time%3F" in crew["hero"]["whatsapp"]
          and "%2Fcrew%2F" in crew["hero"]["whatsapp"])
    text = __import__("json").dumps(crew)
    check("the crew never shows pace, heart rate or distance",
          not re.search(r"/km|bpm|\"km\"|pace|heart", text.replace("never pace, heart rate", "")), text[:80])
    check("a hard run is never a match", not any(s.get("match") and s.get("kind") == "hard"
                                              for p in crew["people"] for c in p["cells"] for s in c["segs"]))
    # Nov 6: next week too, the other week a crew keeps.
    nxt = client.get("/app/api/crew?week=next", headers=auth).json()
    from datetime import date as _d, timedelta as _t
    next_monday = _d.today() - _t(days=_d.today().weekday()) + _t(weeks=1)
    check("the crew's next week, on asking", not nxt.get("error") and nxt.get("showing") == "next"
          and crew.get("showing") == "this" and len(nxt.get("people") or []) == 6
          and f"{next_monday.day} {app_routes.MONTHS[next_monday.month - 1]}".upper() in nxt.get("kicker", ""),
          str({k: nxt.get(k) for k in ("error", "showing", "kicker", "headline")}))
    check("Today's crew line agrees", client.get("/app/api/today", headers=auth).json().get("crew", {}).get("date")
          == crew["hero"]["date"])

    done = next(a for a in DEMO.demo_activities.values() if a["activityName"] == "3 x 3 km")
    run = client.get(f"/app/api/run/{done['activityId']}", headers=auth).json()
    check("the run screen has a verdict", bool(run.get("verdict")), str(run.get("error")))
    check("it counts as done", (run.get("effect") or {}).get("head") == "Counts as done")
    check("it offers the share image", (run.get("share_url") or "").startswith("http"))

    # Each read asked for once, and none of the chat's extras (Nov 3: the run
    # screen made about eleven calls, mostly one after another).
    asked: list[tuple[str, tuple]] = []
    reads = ("get_activity_splits", "get_activity_details", "get_activity", "get_activity_hr_in_timezones",
             "get_activity_gear", "get_activity_weather")
    for name in reads:
        def counted(*a, _real=getattr(DEMO, name), _name=name, **k):
            asked.append((_name, a))
            return _real(*a, **k)
        setattr(DEMO, name, counted)
    try:
        again = client.get(f"/app/api/run/{done['activityId']}", headers=auth).json()
    finally:
        for name in reads:
            delattr(DEMO, name)
    names = sorted(n for n, _ in asked)
    check("the run screen asks Garmin for each thing once",
          names == ["get_activity", "get_activity_details", "get_activity_splits"], str(names))
    check("and not for the route", any(n == "get_activity_details" and a[-1:] == (0,) for n, a in asked), str(asked)[:160])
    check("with the same breakdown and share image as before",
          again.get("verdict") == run.get("verdict") and again.get("share_url") == run.get("share_url")
          and again.get("planned_and_run") == run.get("planned_and_run") and again.get("splits") == run.get("splits"))

    # -- signing in without Garmin again --------------------------------
    from garmin_mcp import support_mail
    from tests import fake_garmin

    handoff = store.new_handoff(user_token)
    r = client.post("/app/api/sign-in/handoff", json={"code": handoff})
    handed = r.json()
    check("Open in Daash's code signs the app in", r.status_code == 200 and handed.get("client_id") == "daash-app", r.text[:120])
    check("as the same person", client.get("/app/api/today", headers={"Authorization": f"Bearer {handed.get('access_token')}"})
          .status_code == 200)
    check("the code works once", client.post("/app/api/sign-in/handoff", json={"code": handoff}).status_code == 401)
    late = store.new_handoff(user_token, lifetime=-1)
    check("and not after it expires", client.post("/app/api/sign-in/handoff", json={"code": late}).status_code == 401)
    refreshed = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": handed.get("refresh_token", ""),
                                             "client_id": "daash-app"})
    check("the app's sign-in refreshes like any other", refreshed.status_code == 200 and refreshed.json().get("access_token"),
          refreshed.text[:120])

    sent: list[tuple[str, str]] = []
    support_mail.can_send = lambda: True
    support_mail.send_code = lambda to, code: sent.append((to, code))
    r = client.post("/app/api/sign-in/email", json={"email": "nobody@example.test"})
    check("an unknown email gets the same answer, and no mail",
          r.status_code == 200 and "If that email" in r.json().get("message", "") and not sent, r.text[:120])
    r = client.post("/app/api/sign-in/email", json={"email": email})
    check("an account's email gets a code", r.status_code == 200 and len(sent) == 1 and sent[0][0] == email, r.text[:120])
    code = sent[-1][1] if sent else ""
    wrong = "000000" if code != "000000" else "111111"
    check("a wrong code is refused", client.post("/app/api/sign-in/verify", json={"email": email, "code": wrong}).status_code == 401)
    r = client.post("/app/api/sign-in/verify", json={"email": email, "code": code})
    check("the right code signs in", r.status_code == 200 and r.json().get("access_token"), r.text[:120])
    check("as the account under that email",
          store.load_oauth_token(r.json().get("access_token", ""), "access")["subject"] == user_token)
    check("a code works once", client.post("/app/api/sign-in/verify", json={"email": email, "code": code}).status_code == 401)
    methods = {e["properties"].get("method") for e in posted("app_signed_in")}
    check("signing in is recorded, by how", methods >= {"chat link", "email"}, str(methods))
    failed = {(e["properties"].get("method"), e["properties"].get("reason")) for e in posted("app_sign_in_failed")}
    check("and failed sign-ins, with why", ("email", "wrong code") in failed and ("chat link", "used or expired") in failed,
          str(failed))

    # Signing in with Garmin from the app: Daash's own client goes straight back, no consent page.
    fake_garmin._FakeHttp.dumps = lambda self: '{"di_token": "x"}'
    hosted._ATTEMPTS.clear()  # the email checks above count as sign-in attempts from this address
    import base64, hashlib, urllib.parse
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    callback = "http://localhost/app/callback"
    r = client.get("/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": "daash-app", "redirect_uri": callback, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "s1", "resource": "http://localhost/mcp"}), follow_redirects=False)
    flow = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers.get("location", "")).query).get("flow", [""])[0]
    check("the app's Garmin sign-in starts at Daash's page", bool(flow), f"{r.status_code} {r.headers.get('location', '')[:80]}")
    r = client.post("/connect", data={"email": email, "password": "pw", "flow": flow}, follow_redirects=False)
    back = r.headers.get("location", "")
    check("and comes straight back with a code, no consent page",
          r.status_code in (302, 303) and back.startswith(callback + "?code="), f"{r.status_code} {back[:80]} {r.text[:80]}")
    got = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
    r = client.post("/token", data={"grant_type": "authorization_code", "code": got.get("code", [""])[0],
                                    "redirect_uri": callback, "client_id": "daash-app", "code_verifier": verifier})
    check("which exchanges for the same account", r.status_code == 200
          and store.load_oauth_token(r.json().get("access_token", ""), "access")["subject"] == user_token, r.text[:120])
    other = client.post("/register", json={"client_name": "x", "redirect_uris": [callback], "grant_types": ["authorization_code"],
                                           "response_types": ["code"], "token_endpoint_auth_method": "none"}).json()
    r = client.get("/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": other.get("client_id", ""), "redirect_uri": callback, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "s2", "resource": "http://localhost/mcp"}), follow_redirects=False)
    flow = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers.get("location", "")).query).get("flow", [""])[0]
    r = client.post("/connect", data={"email": email, "password": "pw", "flow": flow}, follow_redirects=False)
    check("another client pointing at the app's callback still gets the consent page",
          r.status_code == 200 and "Allow access?" in r.text, f"{r.status_code} {r.text[:80]}")

    you = client.get("/app/api/you", headers=auth).json()
    rows = {r["l"]: r for g in you.get("groups") or [] for r in g["rows"]}
    check("You names the watch and when it synced", you.get("watch", {}).get("synced", "").startswith("Synced 07:12"),
          str(you.get("error") or you.get("watch")))
    check("the usual time reads plainly", rows.get("Usual run time", {}).get("v") == "Mornings", str(rows.get("Usual run time")))
    check("the goal race comes from the plan", rows.get("London Marathon", {}).get("sub") == "Target sub 3:00",
          str([r for r in rows if "arathon" in r]))
    check("the crew row has its size", rows.get("HCRC", {}).get("v") == "6 people")
    check("You's copy is plain", plain(__import__("json").dumps(you)))
    r = client.post("/app/api/you/times", headers=auth, json={"times": ["weekday_evening", "weekend_morning"]})
    check("usual times save", r.status_code == 200, r.text[:120])
    grid = client.get("/app/api/crew", headers=auth).json()["people"][0]["cells"]
    # Any weekday with a run on it, whichever the earlier checks moved.
    days_shown = client.get("/app/api/crew", headers=auth).json()["days"]
    weekday = next((c for c, d in list(zip(grid, days_shown))[:5] if any(x.get("kind") for x in c["segs"])), {"segs": []})
    check("the crew sees a weekday run in the evening now", [bool(x.get("kind")) for x in weekday["segs"]] == [False, False, True],
          str(weekday))
    check("an unknown time is refused", client.post("/app/api/you/times", headers=auth, json={"times": ["noon"]}).status_code == 400)

    # Notifications (Nov 11): when the session heads-up goes, from You.
    r = client.post("/app/api/push", headers=auth, json={"heads_up": "6"})
    check("no heads-up time without notifications on", r.status_code == 400, r.text[:120])
    store.save_push(user_token, "https://push.example.com/runner", "p256dh-key", "auth-key", "Europe/London")
    got = client.get("/app/api/push", headers=auth).json()
    you = client.get("/app/api/you", headers=auth).json()
    check("with notifications on, the heads-up goes the evening before", got.get("heads_up") == "evening"
          and you.get("heads_up") == "evening", f"{got} / {you.get('heads_up')}")
    r = client.post("/app/api/push", headers=auth, json={"heads_up": "6"})
    check("it moves to 6am", r.status_code == 200 and r.json().get("message") == "You'll hear about a session at 6am that morning."
          and client.get("/app/api/push", headers=auth).json().get("heads_up") == "6", r.text[:160])
    check("a time not offered is refused", client.post("/app/api/push", headers=auth, json={"heads_up": "5"}).status_code == 400)
    check("the time change is counted", any(e["properties"].get("when") == "6" for e in posted("app_push_time")), str(posted("app_push_time")))
    # Read as the server's notification pass reads it: as this person, outside any request.
    plan = hosted._as_person(user_token, server.get_plan)
    days = sorted({s["date"] for w in plan.get("weeks") or [] for s in w.get("sessions") or []
                   if s.get("status") in ("today", "ahead")})
    ahead = hosted._as_person(user_token, app_routes.heads_up_payload, days[0])
    check("the heads-up names the day's session and its numbers", ahead and ahead["date"] == days[0] and ahead["name"]
          and ahead.get("stats"), str(ahead))
    rest = next(d for d in (str(date.fromisoformat(days[0]) + timedelta(days=i)) for i in range(1, 14)) if d not in days)
    check("a rest day has nothing to say", hosted._as_person(user_token, app_routes.heads_up_payload, rest) is None, rest)

    check("delete needs saying", client.post("/app/api/you/delete", headers=auth, json={}).status_code == 400)
    check("a wrong word doesn't delete", client.post("/app/api/you/delete", headers=auth, json={"confirm": "yes"}).status_code == 400
          and store.get_user(user_token) is not None)
    r = client.post("/app/api/you/delete", headers=auth, json={"confirm": " Delete "})
    check("delete answers", r.status_code == 200, r.text[:120])
    check("the account is gone", store.get_user(user_token) is None)
    check("deleting the account is recorded", bool(posted("app_account_deleted")))
    check("its sign-ins stop working", client.get("/app/api/today", headers=auth).status_code == 401)
    check("and it has left the crew", not crews.in_crew(crews.member_key(user_token, store.email_fingerprint(email))))


def assistant_checks() -> None:
    """Ask ChatGPT for someone on ChatGPT, though the app's own sign-in is newer (Nov 3)."""
    import json as json_mod

    from garmin_mcp import oauth

    store.save_oauth_client("gpt", json_mod.dumps({"redirect_uris": ["https://chatgpt.com/connector/oauth/abc123"]}))
    store.save_oauth_client("cld", json_mod.dumps({"redirect_uris": [oauth.CLAUDE_CALLBACK]}))
    oauth.ensure_app_client()

    def person(*signed_in: tuple[str, int]) -> tuple[str, dict]:
        email = f"{secrets.token_hex(4)}@example.test"
        subject = store.save_user(session_mod.mask_email(email), '{"di_token": "x"}',
                                  email_hash=store.email_fingerprint(email), email=email)
        token = ""
        for client_id, ago in signed_in:
            token = secrets.token_urlsafe(32)
            store.save_oauth_token(token, kind="access", client_id=client_id, subject=subject, scopes="",
                                   resource=None, expires_at=int(time.time()) + 3600)
            with store._connect() as conn:
                conn.execute("UPDATE oauth_tokens SET created_at = ? WHERE token_hash = ?",
                             (int(time.time()) - ago, store.token_hash(token)))
        return subject, {"Authorization": f"Bearer {token}"}

    gpt, gpt_auth = person(("gpt", 7200), (oauth.APP_CLIENT_ID, 0))
    check("someone on ChatGPT only is sent to ChatGPT, though the app signed in since",
          oauth.app_for_user(gpt) == "ChatGPT", str(oauth.app_for_user(gpt)))
    both, _ = person(("cld", 86400), ("gpt", 600), (oauth.APP_CLIENT_ID, 0))
    check("with both, the one used last", oauth.app_for_user(both) == "ChatGPT")
    back, _ = person(("gpt", 86400), ("cld", 600), (oauth.APP_CLIENT_ID, 0))
    check("and Claude when that was", oauth.app_for_user(back) == "Claude")
    app_only, _ = person((oauth.APP_CLIENT_ID, 0))
    check("only the app: nobody to say", oauth.app_for_user(app_only) is None)

    client = TestClient(hosted.build_app(), base_url="http://localhost")
    today = client.get("/app/api/today", headers=gpt_auth).json()
    check("the app says ChatGPT to them", today.get("assistant") == "ChatGPT", str(today.get("assistant")))
    check("and its ask opens a new ChatGPT chat", (today.get("ask") or {}).get("chatgpt", "").startswith("https://chatgpt.com/?q="))


def extra_checks() -> None:
    """A run not on the plan is in the week's list, not only its total (Nov 6: a top-up run)."""
    from datetime import date as _d, timedelta as _td
    from garmin_mcp import training_plan as tp
    from garmin_mcp.progress import Actual

    monday = _d.today() - _td(days=_d.today().weekday())
    day = monday.isoformat()
    week = {"starts": day, "sessions": [{"date": day, "name": "6km easy + strides · B1", "type": "easy", "sport": "run",
                                         "status": "done", "planned_km": 7.5, "actual_km": 7.45, "activity_id": 1}]}
    tp._add_distance(week, [Actual(monday, "a", "run", 2292, 7450, 1), Actual(monday, "b", "run", 377, 1190, 2)],
                     [{"date": day, "name": "Tower Hamlets Running", "sport": "run", "kind": "run",
                       "actual_seconds": 377, "actual_km": 1.19, "activity_id": 2}], "run")
    check("the week's total counts the extra run", week.get("run_km") == 8.6, str(week.get("run_km")))
    check("and the extra run keeps its Garmin id", (week.get("extra") or [{}])[0].get("activity_id") == 2, str(week.get("extra")))
    view = app_routes._week_view({"weeks": [week]}, {day: week}, None, monday, _d.today())
    rows = view["days"][0]["sessions"]
    check("it's listed on its day after the planned run, as extra, not on the plan, opening the run",
          [r.get("state") for r in rows] == ["done", "extra"] and rows[1].get("v") == "1.2" and rows[1].get("u") == "km"
          and rows[1].get("type_label") == "Extra" and rows[1].get("meta", "").startswith("Not on the plan")
          and rows[1].get("activity_id") == 2, str(rows))
    other = app_routes._week_view({"weeks": [week]}, {day: week}, None, monday, _d.today())["days"][1]["sessions"]
    check("and on no other day", other == [], str(other))


def verdict_checks() -> None:
    """A race is named in the week's line (Nov 6: a threshold session and a parkrun said "One hard day")."""
    days = [{"date": f"2026-11-0{i + 2}", "state": "session",
             "colour": c} for i, c in enumerate(["strength", "easy", "easy", "hard", "easy", "race", "easy"])]
    sessions = [{"date": "2026-11-05", "type": "threshold", "name": "Benchmark 5x1km"},
                {"date": "2026-11-07", "type": "race", "name": "Parkrun tune-up"},
                {"date": "2026-11-03", "type": "easy", "name": "10km easy"}]
    said = app_routes._week_verdict(days, sessions)
    check("the week's line names the parkrun beside the hard day", said == "No rest day this week. One hard day and a parkrun on Saturday.", said)
    other = app_routes._week_verdict(days, [{"date": "2026-11-08", "type": "race", "name": "Goal half"}])
    check("and any other race as a race", other == "No rest day this week. A race on Sunday.", other)
    rest = [dict(d, state="rest") if d["date"] == "2026-11-02" else d for d in days]
    heavy = app_routes._week_verdict(rest, sessions + [{"date": "2026-11-04", "type": "intervals", "name": "VO2"}])
    check("a race counts towards a heavy week", heavy.startswith("A heavy week.") and "a parkrun on Saturday" in heavy, heavy)


def builder_checks() -> None:
    """The plan builder: closed unless switched on, its prefill from Garmin, a preview that
    writes nothing, and a plan put on the calendar that get_plan then reads."""
    email = "builder@example.test"
    user_token = store.save_user(session_mod.mask_email(email), '{"di_token": "x"}',
                                 email_hash=store.email_fingerprint(email), email=email)
    token = secrets.token_urlsafe(32)
    store.save_oauth_token(token, kind="access", client_id="test", subject=user_token, scopes="",
                           resource=None, expires_at=int(time.time()) + 3600)
    client = TestClient(hosted.build_app(), base_url="http://localhost")
    auth = {"Authorization": f"Bearer {token}"}
    os.environ.pop(app_routes.BUILDER_ENV, None)
    check("the builder is closed unless switched on", client.get("/app/api/build", headers=auth).status_code == 404)
    os.environ[app_routes.BUILDER_ENV] = "1"
    try:
        check("and still needs a token", client.get("/app/api/build").status_code == 401)
        start = client.get("/app/api/build", headers=auth).json()
        check("it says which plans are already in the weeks ahead", "HM" in (start.get("existing") or []), str(start.get("existing")))
        check("it starts from what Garmin knows: weekly km and the longest run",
              start.get("enough_history") and start.get("weekly_km") and start.get("longest_km"), str(start)[:300])
        check("and a level to correct", start.get("level") in ("new", "regular", "experienced"), str(start.get("level")))
        from datetime import date as d_, timedelta as td_
        race = (d_.today() + td_(weeks=10)).isoformat()
        answers = {"goal": "10k", "race_date": race, "race_name": "Riverside 10K", "days": 4, "long_day": 6,
                   "weekly_km": start.get("weekly_km") or 30, "longest_km": start.get("longest_km") or 10,
                   "level": "regular", "volume": "build", "injury": "none", "style": "pick"}
        opts = client.post("/app/api/build/options", headers=auth, json=answers).json()
        check("step 4 offers keep, build and more", set((opts.get("options") or {})) == {"keep", "build", "more"}, str(opts))
        before = len(DEMO.scheduled)
        seen = client.post("/app/api/build/preview", headers=auth, json=answers).json()
        check("the preview has every week, with reasons", seen.get("weeks") and len(seen.get("reasons") or []) >= 2,
              str(seen)[:300])
        check("and writes nothing", len(DEMO.scheduled) == before)
        too_close = client.post("/app/api/build/preview", headers=auth,
                                json={**answers, "race_date": (d_.today() + td_(weeks=3)).isoformat()})
        check("a race too close is refused, saying why", too_close.status_code == 400
              and "too close" in too_close.json().get("error", ""), too_close.text[:200])
        clash = client.post("/app/api/build", headers=auth, json=answers)
        check("a plan over the weeks of one already there is refused, naming it",
              clash.status_code == 400 and "HM" in clash.json().get("error", ""), clash.text[:200])
        # The demo's own plans out of the way, as for someone with an empty calendar.
        DEMO.scheduled = []
        hosted.session_for(user_token).forget()
        made = client.post("/app/api/build", headers=auth, json=answers)
        body = made.json()
        check("Put it on my Garmin writes the plan", made.status_code == 200 and body.get("label") == "10K", made.text[:300])
        check("every session scheduled", len(DEMO.scheduled) == body.get("sessions"),
              f"{len(DEMO.scheduled)} of {body.get('sessions')}")
        import anyio
        from garmin_mcp import server

        async def read():
            with_session = session_mod.use_session(hosted.session_for(user_token))
            try:
                return await server.get_plan(label="10K")
            finally:
                session_mod.reset_session(with_session)

        plan = anyio.run(read)
        check("get_plan, what Claude reads, has the new plan with its race",
              plan.get("label") == "10K" and any(r.get("name") == "Riverside 10K" for r in plan.get("races") or []),
              str({k: plan.get(k) for k in ("label", "races", "error")})[:300])
        check("it was counted", any(e["properties"].get("ok") for e in posted("plan_built")))
        again = client.post("/app/api/build", headers=auth, json=answers)
        check("building the same plan twice is refused, not doubled",
              again.status_code == 400 and "10K" in again.json().get("error", "")
              and len(DEMO.scheduled) == body.get("sessions"), again.text[:200])
    finally:
        os.environ.pop(app_routes.BUILDER_ENV, None)


# Bart's 10 km easy + strides, Nov 11, lap by lap as Garmin recorded it: the
# 9.5 km easy step lapped every km (eight 1 km laps and a 0.93), then six
# 20 s strides, each with its jog, the last jog a 4 s stub.
STRIDES_RUN = [(1000, 320, 111), (1000, 303, 121), (1000, 293, 124), (1000, 296, 129), (1000, 295, 127),
               (1000, 294, 132), (1000, 292, 133), (1000, 293, 132), (930, 277, 130),
               (101.5, 20, 141, "RECOVERY", 190, 62, 140), (90.9, 20, 133, "RECOVERY", 130, 44, 136),
               (90.5, 20, 129, "RECOVERY", 120, 43, 136), (102.0, 20, 134, "RECOVERY", 150, 53, 141),
               (75.8, 20, 135, "RECOVERY", 120, 38, 135), (93.9, 20, 133, "RECOVERY", 10, 4, 145)]


def strides_checks() -> None:
    laps = []
    for row in STRIDES_RUN:
        laps.append({"distance": row[0], "duration": row[1], "averageHR": row[2], "intensityType": "ACTIVE"})
        if len(row) > 3:
            laps.append({"distance": row[4], "duration": row[5], "averageHR": row[6], "intensityType": row[3]})
    workout = {"workoutSegments": [{"workoutSteps": [
        {"stepOrder": 1, "stepType": {"stepTypeKey": "interval"}, "endCondition": {"conditionTypeKey": "distance"},
         "endConditionValue": 9500, "targetType": {"workoutTargetTypeKey": "heart.rate.zone"}, "targetValueOne": 120, "targetValueTwo": 145},
        {"stepOrder": 2, "type": "RepeatGroupDTO", "numberOfIterations": 6, "workoutSteps": [
            {"stepOrder": 1, "stepType": {"stepTypeKey": "interval"}, "endCondition": {"conditionTypeKey": "time"}, "endConditionValue": 20},
            {"stepOrder": 2, "stepType": {"stepTypeKey": "recovery"}, "endCondition": {"conditionTypeKey": "lap.button"}}]}]}]}
    result = analysis.analyse(workout, {"lapDTOs": laps}, "easy")
    rows = result.get("planned_and_run") or []
    run = next((r for r in rows if r["label"] == "Run"), {})
    check("a long step the watch lapped every km reads as one: the whole 8.9 km easy part against its target",
          run.get("pace") == "4:58" and run.get("hr") == 126 and not run.get("soft"), str(run))
    stride = next((r for r in rows if r.get("kind") == "strides"), {})
    check("six 20 s strides are one row, with their pace range and each one's pace",
          stride.get("note") == "6 \u00d7 20 s \u00b7 3:16\u20134:24" and stride.get("pace") == "3:36"
          and len(stride.get("spark") or []) == 6 and not any(r.get("rep") for r in rows), str(stride))
    check("nothing is left over as run on after the workout", not any(r["label"] == "Ran on" for r in rows), str(rows))
    check("the numbers at the top are the whole run's, not the strides'",
          [s["l"] for s in result["stats"]] == ["km", "time", "/km avg", "bpm avg"] and result["stats"][2]["v"] == "4:57", str(result["stats"]))
    check("the verdict is about the easy run first, then the strides in a few words",
          result["verdict"] == "On target. Easy 8.9 km at 4:58, heart rate 126. Six strides, fastest 3:16.", result["verdict"])
    split_labels = [s["km"] for s in result["splits"]]
    stride_split = next((s for s in result["splits"] if s.get("strides")), {})
    check("the splits: the km, then the strides as one row, then the jogs",
          split_labels[:9] == [str(i) for i in range(1, 9)] + ["0.9"] and split_labels[9:] == ["Strides", "Recoveries"]
          and stride_split.get("pace") == "3:36 (6 \u00d7 20 s)" and stride_split.get("fastest") == "3:16", str(result["splits"][8:]))
    total = sum(s["seconds"] for s in result["splits"])
    check("and they still add up to the run's time", total == result["seconds"], f"{total} != {result['seconds']}")
    # Reps long enough to be the session stay reps, row by row.
    import copy
    long_reps = copy.deepcopy(workout)
    long_reps["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][0]["endConditionValue"] = 180
    check("three-minute reps are still reps, not strides",
          not analysis._stride_steps([(s, None) for s in analysis.planned_steps(long_reps)]))


def main() -> int:
    extra_checks()
    verdict_checks()
    analysis_checks()
    strides_checks()
    easier_checks()
    api_checks()
    assistant_checks()
    builder_checks()
    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
