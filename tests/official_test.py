"""Daash on Garmin's official API (official.py), against a stubbed Garmin.

Covers the sign-in (PKCE), refreshing, what Garmin pushes, disconnecting, and
turning Daash's workouts into the Training API's shape. Nothing leaves the test.

    .venv/bin/python tests/official_test.py
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(os.environ.get("TMPDIR", "/tmp")) / "garmin-official-test"
SCRATCH.mkdir(parents=True, exist_ok=True)
DB = SCRATCH / f"test-{int(time.time() * 1000)}.sqlite3"

from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_DB"] = str(DB)
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()
os.environ["GARMIN_OFFICIAL_CLIENT_ID"] = "client-123"
os.environ["GARMIN_OFFICIAL_CLIENT_SECRET"] = "shh-secret"
os.environ["GARMIN_MCP_BASE_URL"] = "https://daash.test"

from garmin_mcp import official, store  # noqa: E402
from garmin_mcp.workouts import build_workout  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


# Garmin, stubbed: what was asked of it, and what it answers.
POSTED: list[tuple[str, dict]] = []
CALLED: list[tuple[str, str, object]] = []
TOKEN_N = [0]


def fake_post_form(url: str, fields: dict) -> dict:
    POSTED.append((url, dict(fields)))
    TOKEN_N[0] += 1
    return {"access_token": f"access-{TOKEN_N[0]}", "refresh_token": f"refresh-{TOKEN_N[0]}",
            "expires_in": 86400, "refresh_token_expires_in": 7775998}


def fake_call(access_token: str, method: str, path: str, body=None):
    CALLED.append((method, path, body))
    if path.endswith("/user/id"):
        return {"userId": "garmin-user-1"}
    if path.endswith("/user/permissions"):
        return {"permissions": ["ACTIVITY_EXPORT", "HEALTH_EXPORT", "WORKOUT_IMPORT"]}
    if path == "/workoutportal/workout/v2":
        return {"workoutId": 987, "workoutName": body["workoutName"]}
    if path == "/training-api/schedule/":
        return {"scheduleId": 654, "workoutId": body["workoutId"], "date": body["date"]}
    return {}


REAL_POST_FORM = official._post_form
official._post_form = fake_post_form
official._call = fake_call


def main() -> int:
    print("\nsigning in")
    bart = store.save_user("b***@example.com", "{}", email_hash="hash-bart")
    link = official.connect_link(bart)
    check("the connect link is on this server and carries no readable account",
          link.startswith("https://daash.test/official/start?t=") and bart not in link)
    sealed = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)["t"][0]
    target = official.authorize_url(sealed)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(target).query)
    check("it sends the person to Garmin's own sign-in",
          target.startswith(official.AUTHORIZE_URL) and query["client_id"] == ["client-123"]
          and query["code_challenge_method"] == ["S256"]
          and query["redirect_uri"] == ["https://daash.test/official/callback"])
    state = query["state"][0]
    opened = official._open(state)
    expected = base64.urlsafe_b64encode(hashlib.sha256(opened["v"].encode()).digest()).decode().rstrip("=")
    check("the challenge is the hashed verifier the state carries back",
          query["code_challenge"] == [expected] and 43 <= len(opened["v"]) <= 128 and opened["u"] == bart)
    check("a made-up or tampered link starts nothing", official.authorize_url("nonsense") is None
          and official.authorize_url(sealed[:-4] + "AAAA") is None)

    garmin_id = official.finish("the-code", state)
    url, fields = POSTED[-1]
    check("the code is exchanged with the verifier, from the server",
          url == official.TOKEN_URL and fields["code"] == "the-code" and fields["code_verifier"] == opened["v"]
          and "client_secret" not in fields and fields["grant_type"] == "authorization_code")
    linked = store.official_link(bart)
    check("the account is linked with its Garmin id and permissions",
          garmin_id == "garmin-user-1" and linked is not None and linked.garmin_user_id == "garmin-user-1"
          and "WORKOUT_IMPORT" in linked.permissions)
    import sqlite3
    raw = sqlite3.connect(DB).execute("SELECT tokens_enc FROM official_links").fetchone()[0]
    check("Garmin's tokens are encrypted at rest", b"access-1" not in raw and b"refresh-1" not in raw)
    try:
        official.finish("again", "not-a-state")
        refused = False
    except official.OfficialError:
        refused = True
    check("a forged state is refused", refused)

    print("\nrefreshing")
    token, _ = official._access_token(bart)
    check("a fresh token is used as it is", token == "access-1" and len(POSTED) == 1)
    tokens = dict(store.official_link(bart).tokens, access_expires_at=int(time.time()) + 60)
    store.update_official_tokens(bart, tokens)
    token, _ = official._access_token(bart)
    check("one about to lapse is refreshed with the latest refresh token",
          token == "access-2" and POSTED[-1][1]["grant_type"] == "refresh_token"
          and POSTED[-1][1]["refresh_token"] == "refresh-1")
    check("and the new refresh token is kept, the old one being spent",
          store.official_link(bart).tokens["refresh_token"] == "refresh-2")

    print("\nwhat Garmin pushes")
    day = {"userId": "garmin-user-1", "summaryId": "d-1", "calendarDate": "2026-10-21",
           "startTimeInSeconds": 1792540800, "steps": 9000, "restingHeartRateInBeatsPerMinute": 48}
    stranger = {"userId": "somebody-else", "summaryId": "d-9", "startTimeInSeconds": 1792540800, "steps": 1}
    kept = official.handle_push(json.dumps({"dailies": [day, stranger]}).encode())
    check("a linked account's summaries are kept; a stranger's are dropped", kept == {"dailies": 1}, str(kept))
    later = dict(day, steps=12000, durationInSeconds=86400)
    official.handle_push(json.dumps({"dailies": [later]}).encode())
    stored = store.official_summaries("garmin-user-1", "dailies")
    check("a summary sent again replaces the earlier one", len(stored) == 1 and stored[0]["steps"] == 12000)
    official.handle_push(json.dumps({"sleeps": [{"userId": "garmin-user-1", "summaryId": "s-1",
                                                 "calendarDate": "2026-10-21", "durationInSeconds": 27000}]}).encode())
    status = official.status(bart)
    check("the status says what has arrived",
          status["connected"] and status["received"]["dailies"]["count"] == 1
          and status["received"]["sleeps"]["count"] == 1 and status["push_url"].startswith("https://daash.test/official/push/"))
    check("the status gives Garmin's user id, for the portal's tools", status["garmin_user_id"] == "garmin-user-1")
    raw = sqlite3.connect(DB).execute("SELECT data_enc FROM official_summaries").fetchone()[0]
    check("what Garmin sent is encrypted at rest", b"12000" not in raw)
    check("rubbish is ignored", official.handle_push(b"not json") == {} and official.handle_push(b"[1,2]") == {})

    print("\nkept small, read back")
    now = int(time.time())
    run_start = now - 3 * 3600
    samples = [{"startTimeInSeconds": run_start + i * 10, "totalDistanceInMeters": i * 30.0,
                "timerDurationInSeconds": i * 10, "heartRate": 140 + (i % 5), "latitudeInDegree": 51.5,
                "longitudeInDegree": -0.1} for i in range(0, 81)]
    detail = {"userId": "garmin-user-1", "summaryId": "77-detail", "activityId": 77,
              "summary": {"startTimeInSeconds": run_start, "durationInSeconds": 800, "distanceInMeters": 2400.0},
              "samples": samples, "laps": [{"startTimeInSeconds": run_start}, {"startTimeInSeconds": run_start + 400}]}
    run = {"userId": "garmin-user-1", "summaryId": "77", "activityId": 77, "activityName": "Morning run",
           "activityType": "RUNNING", "startTimeInSeconds": run_start, "startTimeOffsetInSeconds": 3600,
           "durationInSeconds": 800, "distanceInMeters": 2400.0, "averageHeartRateInBeatsPerMinute": 142}
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    sleep = {"userId": "garmin-user-1", "summaryId": "sl-2", "calendarDate": today, "startTimeInSeconds": now - 10 * 3600,
             "durationInSeconds": 27000, "deepSleepDurationInSeconds": 5400, "remSleepInSeconds": 6000,
             "overallSleepScore": {"value": 84, "qualifierKey": "GOOD"}, "sleepLevelsMap": {"deep": [{"x": 1}]},
             "timeOffsetSleepSpo2": {"0": 95}}
    stress = {"userId": "garmin-user-1", "summaryId": "st-2", "calendarDate": today, "startTimeInSeconds": now - 8 * 3600,
              "timeOffsetBodyBatteryValues": {"0": 40, "180": 85, "360": 62},
              "timeOffsetStressLevelValues": {"0": 20, "180": 30, "360": -1}}
    hrv = {"userId": "garmin-user-1", "summaryId": "h-2", "calendarDate": today, "startTimeInSeconds": now - 9 * 3600,
           "lastNightAvg": 61, "lastNight5MinHigh": 90, "hrvValues": {"300": 58}}
    metrics = {"userId": "garmin-user-1", "summaryId": "m-2", "calendarDate": today, "vo2Max": 54.0}
    epoch = {"userId": "garmin-user-1", "summaryId": "e-1", "startTimeInSeconds": now - 900, "steps": 40}
    kept = official.handle_push(json.dumps({
        "activities": [run], "activityDetails": [detail], "sleeps": [sleep], "stressDetails": [stress], "hrv": [hrv],
        "userMetrics": [metrics], "epochs": [epoch], "activityFiles": [{"userId": "garmin-user-1", "summaryId": "f-1",
                                                                       "callbackURL": "https://x/file?token=t"}]}).encode())
    check("streams and files Daash doesn't use aren't kept", "epochs" not in kept and "activityFiles" not in kept, str(kept))
    blobs = [store._cipher().decrypt(r[0]) for r in sqlite3.connect(DB).execute("SELECT data_enc FROM official_summaries")]
    check("no GPS, sleep stages map or heart rate curve is kept",
          not any(b"latitudeInDegree" in b or b"sleepLevelsMap" in b or b"hrvValues" in b or b"timeOffset" in b
                  for b in blobs))
    check("an activity's details start where its summary says", store.official_summary_counts("garmin-user-1")
          ["activityDetails"]["latest_start"] == run_start)
    acts = official.read(bart, "activities")
    first = acts[0] if acts else {}
    check("activities read back with kilometre splits and laps",
          first.get("name") == "Morning run" and first.get("km") == 2.4 and first.get("pace") == "5:33 /km"
          and len(first.get("splits", [])) == 3 and round(sum(s["km"] for s in first["splits"]), 1) == 2.4
          and len(first.get("laps", [])) == 2, json.dumps(first)[:400])
    days = official.read(bart, "days")
    top = days[0] if days else {}
    check("each day brings sleep, HRV, Body Battery and VO2 max together",
          top.get("date") == today and top["sleep"]["score"] == 84 and top["sleep"]["deep_min"] == 90
          and top["hrv"]["last_night_avg"] == 61 and top["body_battery"] == {"start": 40, "end": 62, "high": 85, "low": 40}
          and top["vo2max"] == 54.0, json.dumps(top)[:400])
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT INTO official_summaries (garmin_user_id, kind, summary_id, start_time, data_enc, received_at) "
                     "VALUES ('garmin-user-1', 'epochs', 'old', 0, ?, 0)", (store._cipher().encrypt(b"{}"),))
        conn.execute("UPDATE official_summaries SET data_enc = ?, start_time = NULL WHERE kind = 'activityDetails'",
                     (store._cipher().encrypt(json.dumps(detail).encode()),))
    from garmin_mcp import official_data
    official_data.tidy()
    blobs = [store._cipher().decrypt(r[0]) for r in sqlite3.connect(DB).execute("SELECT data_enc FROM official_summaries")]
    check("what was kept before trimming is tidied away on start",
          "epochs" not in store.official_summary_counts("garmin-user-1") and not any(b"latitudeInDegree" in b for b in blobs))
    check("and details kept without a start get one, so they're read back with their activity",
          official.read(bart, "activities")[0].get("splits") is not None)
    official.handle_push(json.dumps({"userPermissionsChange": [
        {"userId": "garmin-user-1", "permissions": ["ACTIVITY_EXPORT"]}]}).encode())
    check("a permission change is recorded", store.official_link(bart).permissions == ["ACTIVITY_EXPORT"])
    check("the push key is fixed and secret-derived",
          official.push_key() == official.push_key() and "shh-secret" not in official.push_key()
          and len(official.push_key()) == 32)

    print("\nsigning in to Daash again")
    again = store.save_user("b***@example.com", "{}", email_hash="hash-bart")
    check("the official link moves to the new session",
          again != bart and store.official_link(again) is not None and store.official_link(bart) is None)

    print("\nworkouts")
    model, _, _ = build_workout("Threshold", "running", [
        {"type": "warmup", "duration_seconds": 600},
        {"type": "repeat", "times": 3, "steps": [
            {"type": "interval", "distance_meters": 2000, "pace": "4:10"},
            {"type": "recovery", "until_lap": True}]},
        {"type": "cooldown", "duration_seconds": 600, "hr": [120, 140]}])
    workout = official.training_workout(model, "running")
    steps = workout["segments"][0]["steps"]
    rep = steps[1]
    interval = rep["steps"][0]
    check("a run keeps its shape: warm-up, a repeat of 3, a cool-down",
          workout["sport"] == "RUNNING" and [s["type"] for s in steps] == ["WorkoutStep", "WorkoutRepeatStep", "WorkoutStep"]
          and rep["repeatType"] == "REPEAT_UNTIL_STEPS_CMPLT" and rep["repeatValue"] == 3.0)
    check("steps are numbered in order, the repeat's children included",
          [steps[0]["stepOrder"], rep["stepOrder"], interval["stepOrder"], rep["steps"][1]["stepOrder"],
           steps[2]["stepOrder"]] == [1, 2, 3, 4, 5])
    check("a paced rep is a distance with a speed range",
          interval["durationType"] == "DISTANCE" and interval["durationValue"] == 2000.0
          and interval["durationValueType"] == "METER" and interval["targetType"] == "SPEED"
          and interval["targetValueLow"] < 1000 / 250 < interval["targetValueHigh"] and interval["intensity"] == "INTERVAL")
    check("a lap-button recovery is open-ended; a heart-rate cool-down keeps its range",
          rep["steps"][1]["durationType"] == "OPEN" and rep["steps"][1]["intensity"] == "RECOVERY"
          and steps[2]["targetType"] == "HEART_RATE" and steps[2]["targetValueLow"] == 120.0
          and steps[2]["targetValueHigh"] == 140.0 and steps[0]["durationType"] == "TIME")
    check("Daash is named as the provider", workout["workoutProvider"] == "Daash" and workout["workoutSourceId"] == "Daash")
    try:
        official.training_workout(model, "walking")
        walked = False
    except official.OfficialError:
        walked = True
    check("sports the API has no place for are refused, not guessed", walked)
    sent = official.send_workout(again, workout, "2026-10-24")
    check("sending creates the workout and schedules it",
          sent == {"workout_id": 987, "name": "Threshold", "schedule_id": 654, "scheduled_for": "2026-10-24"}
          and CALLED[-2][1] == "/workoutportal/workout/v2" and CALLED[-1][2] == {"workoutId": 987, "date": "2026-10-24"})

    print("\ndisconnecting")
    check("disconnecting tells Garmin and deletes everything kept",
          official.disconnect(again) and CALLED[-1][:2] == ("DELETE", "/partner-gateway/rest/user/registration")
          and store.official_link(again) is None and store.official_summaries("garmin-user-1", "dailies") == [])
    check("with nothing linked, there's nothing to disconnect", official.disconnect(again) is False)

    official.finish("code-2", official._seal({"u": again, "v": "v" * 50}))
    official.handle_push(json.dumps({"dailies": [day]}).encode())
    official.handle_push(json.dumps({"deregistrations": [{"userId": "garmin-user-1"}]}).encode())
    check("a deregistration from Garmin deletes the link and its data",
          store.official_link(again) is None and store.official_summaries("garmin-user-1", "dailies") == [])

    print("\nthe server's routes")
    from starlette.testclient import TestClient
    import garmin_mcp.hosted as hosted
    client = TestClient(hosted.build_app())
    check("the push address refuses a wrong key",
          client.post("/official/push/wrong", content=b"{}").status_code == 404)
    official.finish("code-3", official._seal({"u": again, "v": "v" * 50}))
    answer = client.post(f"/official/push/{official.push_key()}", content=json.dumps({"dailies": [day]}).encode())
    check("the right key gets a 200 and the data is kept",
          answer.status_code == 200 and len(store.official_summaries("garmin-user-1", "dailies")) == 1)
    started = client.get("/official/start?t=" + urllib.parse.quote(sealed), follow_redirects=False)
    check("a good link sends the person straight to Garmin's sign-in",
          started.status_code == 302 and started.headers["location"].startswith(official.AUTHORIZE_URL))
    check("a lapsed link says so", "expired" in client.get("/official/start?t=bogus").text)
    declined = client.get("/official/callback")
    check("declining at Garmin changes nothing", "Not connected" in declined.text)

    print("\nwhen Garmin says no")

    def refusal(code: int, body: bytes):
        return urllib.error.HTTPError(official.TOKEN_URL, code, "no", {}, io.BytesIO(body))

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    sent_headers: list[dict] = []
    direct: list[str] = []
    relayed: list[str] = []
    real_urlopen, real_build = urllib.request.urlopen, urllib.request.build_opener

    def fake_urlopen(request, timeout=None):
        direct.append(request.full_url)
        sent_headers.append(dict(request.header_items()))
        raise next(DIRECT)

    class FakeOpener:
        def open(self, request, timeout=None):
            relayed.append(request.full_url)
            return Answer(b'{"access_token": "a", "refresh_token": "r", "expires_in": 60}')

    urllib.request.urlopen = fake_urlopen
    urllib.request.build_opener = lambda *handlers: FakeOpener()
    try:
        DIRECT = iter([refusal(400, b'{"error": "invalid_grant", "error_description": "Code expired"}')])
        try:
            REAL_POST_FORM(official.TOKEN_URL, {"grant_type": "authorization_code"})
            said = ""
        except official.OfficialError as exc:
            said = str(exc)
        expected_auth = "Basic " + base64.b64encode(b"client-123:shh-secret").decode()
        check("the app's credentials go in a Basic header, the way Garmin's examples send them",
              sent_headers[-1].get("Authorization") == expected_auth, str(sent_headers[-1].keys()))
        check("Garmin's own refusal is passed on with its code, not as 'couldn't be reached'",
              "400, invalid_grant: Code expired" in said and not relayed, said)

        os.environ.pop("GARMIN_MCP_LOGIN_PROXY", None)
        DIRECT = iter([refusal(403, b"<html>Access denied</html>")])
        try:
            REAL_POST_FORM(official.TOKEN_URL, {})
            said = ""
        except official.OfficialError as exc:
            said = str(exc)
        check("a wall in front of Garmin is named as one", "403, web page" in said, said)

        os.environ["GARMIN_MCP_LOGIN_PROXY"] = "http://relay.test:8000"
        DIRECT = iter([refusal(403, b"<html>Access denied</html>")])
        got = REAL_POST_FORM(official.TOKEN_URL, {})
        check("with the relay set, a blocked request goes again through it",
              got.get("access_token") == "a" and len(relayed) == 1)
        DIRECT = iter([refusal(403, b'{"error": "access_denied"}')])
        try:
            REAL_POST_FORM(official.TOKEN_URL, {})
            relayed_again = True
        except official.OfficialError:
            relayed_again = False
        check("but an answer from Garmin itself is not retried elsewhere", not relayed_again and len(relayed) == 1)
    finally:
        urllib.request.urlopen, urllib.request.build_opener = real_urlopen, real_build
        os.environ.pop("GARMIN_MCP_LOGIN_PROXY", None)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
