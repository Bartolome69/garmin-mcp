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
    check("today counts down to the goal race, in the block after this one",
          today.get("headline", "").startswith("London Marathon in"), str(today.get("headline") or today.get("error")))
    check("and numbers the week across the whole plan", today.get("kicker", "").endswith("WEEK 4 OF 28"), today.get("kicker"))
    check("today's next session is today", (today.get("next") or {}).get("kicker") == "NEXT · TODAY")
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

    plan = client.get("/app/api/plan", headers=auth).json()
    week, block = plan.get("week") or {}, plan.get("block") or {}
    check("the plan's week has seven days", len(week.get("days") or []) == 7, str(plan.get("error")))
    check("it is named by the block's week and phase", week.get("title") == "Week 4 \u00b7 Base", week.get("title"))
    check("the moved session is on its new day there",
          any(r["name"] == "3 \u00d7 3 km" for d in week["days"] if d["date"] == later for r in d["sessions"]))
    check("done sessions can't be moved", not any(r.get("movable") for d in week["days"] for r in d["sessions"]
                                                    if r["state"] == "done"))
    earlier = client.get(f"/app/api/plan?week={week['previous']}", headers=auth).json()["week"]
    check("the week before is all done", all(r["state"] == "done" for d in earlier["days"] for r in d["sessions"]))
    last = client.get(f"/app/api/plan?week={block['bars'][-1]['starts']}", headers=auth).json()["week"]
    race = next((r for d in last["days"] for r in d["sessions"] if r["type"] == "race"), {})
    check("a race is red, as on the chat's plan card", race.get("colour") == "race", str(race)[:120])
    check("and so is its shape", all(g["colour"] in ("race", "easy", "rec") for g in race.get("segments") or [])
          and any(g["colour"] == "race" for g in race.get("segments") or []), str(race.get("segments"))[:120])
    check("the block chart runs the whole plan to race day, both blocks", len(block.get("bars") or []) == 28,
          str(len(block.get("bars") or [])))
    check("its title counts to race day", block.get("title") == "28 weeks to race day", block.get("title"))
    check("both blocks are named under it", len(block.get("blocks") or []) == 2)
    far = client.get(f"/app/api/plan?week={block['bars'][24]['starts']}", headers=auth).json()["week"]
    check("a week past the calendar's horizon has its sessions", any(d["sessions"] for d in far["days"]), far.get("title"))
    check("and its number in the whole plan", far.get("title", "").startswith("Week 25"), far.get("title"))
    check("the block's verdict is plain", plain(block.get("verdict", "")) and block["verdict"].startswith("On track."),
          block.get("verdict"))
    check("the block's phases fill its weeks", sum(p["weeks"] for p in block["phases"]) == len(block["bars"]))

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
    check("Today's crew line agrees", client.get("/app/api/today", headers=auth).json().get("crew", {}).get("date")
          == crew["hero"]["date"])

    done = next(a for a in DEMO.demo_activities.values() if a["activityName"] == "3 x 3 km")
    run = client.get(f"/app/api/run/{done['activityId']}", headers=auth).json()
    check("the run screen has a verdict", bool(run.get("verdict")), str(run.get("error")))
    check("it counts as done", (run.get("effect") or {}).get("head") == "Counts as done")
    check("it offers the share image", (run.get("share_url") or "").startswith("http"))

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
    tuesday = next(c for c, d in zip(grid, client.get("/app/api/crew", headers=auth).json()["days"]) if d["letter"] == "T")
    check("the crew sees a weekday run in the evening now", [bool(x.get("kind")) for x in tuesday["segs"]] == [False, False, True],
          str(tuesday))
    check("an unknown time is refused", client.post("/app/api/you/times", headers=auth, json={"times": ["noon"]}).status_code == 400)

    check("delete needs saying", client.post("/app/api/you/delete", headers=auth, json={}).status_code == 400)
    r = client.post("/app/api/you/delete", headers=auth, json={"confirm": "delete"})
    check("delete answers", r.status_code == 200, r.text[:120])
    check("the account is gone", store.get_user(user_token) is None)
    check("its sign-ins stop working", client.get("/app/api/today", headers=auth).status_code == 401)
    check("and it has left the crew", not crews.in_crew(crews.member_key(user_token, store.email_fingerprint(email))))


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
