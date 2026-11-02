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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(os.environ.get("TMPDIR", "/tmp")) / "garmin-app-test"
SCRATCH.mkdir(parents=True, exist_ok=True)

from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_DB"] = str(SCRATCH / f"test-{int(time.time())}.sqlite3")
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()
os.environ["GARMIN_MCP_OAUTH"] = "1"
os.environ["GARMIN_MCP_BASE_URL"] = "http://localhost"
os.environ.pop("GARMIN_MCP_POSTHOG_KEY", None)
os.environ.pop("DEMO_RAN", None)
os.environ.pop("DEMO_READINESS", None)

from garmin_mcp import session as session_mod  # noqa: E402
from tests.demo_garmin import DemoGarmin  # noqa: E402

DEMO = DemoGarmin()
session_mod.build_client = lambda **_: DEMO

import garmin_mcp.hosted as hosted  # noqa: E402
from garmin_mcp import analysis, oauth, store  # noqa: E402
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

    # Laps that don't line up with the steps aren't guessed at.
    odd = {"lapDTOs": DEMO.get_activity_splits(aid)["lapDTOs"][:-1]}
    unmatched = analysis.analyse(DEMO.get_workout_by_id(wid), odd, "threshold")
    check("unmatched laps put nothing against a target", not unmatched.get("planned_and_run"))
    check("and their splits still add up",
          sum(s["seconds"] for s in unmatched["splits"]) == unmatched["seconds"])


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
    check("so is its script", "text/javascript" in client.get("/app/app.js").headers.get("content-type", ""))
    check("a path can't climb out", all(Path(app_routes._file(bad).path).name == "index.html"
                                        for bad in ("../store.py", "../../../etc/passwd", "fonts/../../app.py")))
    check("the app's callback is a recognised redirect", oauth.recognised_redirect("http://localhost/app/callback"))

    today = client.get("/app/api/today", headers=auth).json()
    check("today has a headline", today.get("headline", "").startswith("Half marathon in"), str(today.get("error")))
    check("today's next session is today", (today.get("next") or {}).get("kicker") == "NEXT · TODAY")
    check("the week has seven days", len(today["week"]["days"]) == 7)
    check("ask opens Claude with a prompt", today["ask"]["claude"].startswith("https://claude.ai/new?q="))
    day = today["next"]["date"]

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
    r = client.post(f"/app/api/session/{day}/easier", headers=auth, json={})
    check("easier answers", r.status_code == 200 and plain(r.json().get("message", "")), r.text[:200])
    check("Claude reads the easier session", claude_reads(day) == ["3 x 2 km"], str(claude_reads(day)))
    check("other weeks are untouched", claude_reads(next_week) == before_next_week)
    again = client.post(f"/app/api/session/{day}/easier", headers=auth, json={})
    check("easier twice is refused", again.status_code == 409)
    r = client.post(f"/app/api/session/{day}/undo", headers=auth, json={})
    check("undo answers", r.status_code == 200, r.text[:200])
    check("Claude reads the original again", claude_reads(day) == ["3 x 3 km"], str(claude_reads(day)))

    later = (__import__("datetime").date.fromisoformat(day) + __import__("datetime").timedelta(days=4)).isoformat()
    r = client.post(f"/app/api/session/{day}/move", headers=auth, json={"to": later})
    check("move answers", r.status_code == 200, r.text[:200])
    check("Claude reads it on the new day", "3 x 3 km" in claude_reads(later) and "3 x 3 km" not in claude_reads(day))
    r = client.post(f"/app/api/session/{day}/move", headers=auth, json={"to": "2020-01-01"})
    check("a move into the past is refused", r.status_code == 409)

    session = client.get(f"/app/api/session/{later}", headers=auth).json()
    check("the session screen has its steps", len(session.get("steps") or []) >= 3, str(session.get("error")))
    check("its why line is the workout's note", (session.get("why") or "").startswith("Builds the pace"))

    done = next(a for a in DEMO.demo_activities.values() if a["activityName"] == "3 x 3 km")
    run = client.get(f"/app/api/run/{done['activityId']}", headers=auth).json()
    check("the run screen has a verdict", bool(run.get("verdict")), str(run.get("error")))
    check("it counts as done", (run.get("effect") or {}).get("head") == "Counts as done")
    check("it offers the share image", (run.get("share_url") or "").startswith("http"))


def main() -> int:
    analysis_checks()
    easier_checks()
    api_checks()
    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
