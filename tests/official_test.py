"""Daash on Garmin's official API (official.py), against a stubbed Garmin.

Covers the sign-in (PKCE), refreshing, what Garmin pushes, disconnecting, and
turning Daash's workouts into the Training API's shape. Nothing leaves the test.

    .venv/bin/python tests/official_test.py
"""

from __future__ import annotations

import base64
from datetime import date, timedelta
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
        IDS[0] += 1
        return {"workoutId": IDS[0], "workoutName": body["workoutName"], "ownerId": 4242,
                "estimatedDurationInSecs": 2400, "createdDate": "2026-10-23T08:00:00.0"}
    if path == "/training-api/schedule/":
        IDS[1] += 1
        return {"scheduleId": IDS[1], "workoutId": body["workoutId"], "date": body["date"]}
    if method == "PUT" and path.startswith("/training-api/workout/v2/"):
        return {**body, "updatedDate": "2026-10-23T09:00:00.0"}
    return {}


# Workout and schedule ids Garmin hands out, in turn.
IDS = [986, 653]


REAL_POST_FORM = official._post_form
official._post_form = fake_post_form
official._call = fake_call


async def _async(fn, *args):
    return fn(*args)


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
    # Three days back, so it stays inside the week the resting heart rate is averaged over.
    days_ago = int(time.time()) // 86400 * 86400 - 3 * 86400
    day = {"userId": "garmin-user-1", "summaryId": "d-1", "calendarDate": time.strftime("%Y-%m-%d", time.gmtime(days_ago)),
           "startTimeInSeconds": days_ago, "steps": 9000, "restingHeartRateInBeatsPerMinute": 48}
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
                "timerDurationInSeconds": i * 10, "movingDurationInSeconds": max(0, i * 10 - 8),
                "heartRate": 140 + (i % 5), "latitudeInDegree": 51.5, "longitudeInDegree": -0.1,
                "elevationInMeters": 10 + (i % 20), "stepsPerMinute": 88 + (i % 3)} for i in range(0, 81)]
    detail = {"userId": "garmin-user-1", "summaryId": "77-detail", "activityId": 77,
              "summary": {"startTimeInSeconds": run_start, "durationInSeconds": 800, "distanceInMeters": 2400.0,
                          "averageRunCadenceInStepsPerMinute": 178, "totalElevationGainInMeters": 30},
              "samples": samples, "laps": [{"startTimeInSeconds": run_start}, {"startTimeInSeconds": run_start + 400}]}
    run = {"userId": "garmin-user-1", "summaryId": "77", "activityId": 77, "activityName": "Morning run",
           "activityType": "RUNNING", "startTimeInSeconds": run_start, "startTimeOffsetInSeconds": 3600,
           "durationInSeconds": 800, "distanceInMeters": 2400.0, "averageHeartRateInBeatsPerMinute": 142}
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    sleep = {"userId": "garmin-user-1", "summaryId": "sl-2", "calendarDate": today, "startTimeInSeconds": now - 10 * 3600,
             "durationInSeconds": 27000, "deepSleepDurationInSeconds": 5400, "remSleepInSeconds": 6000,
             "overallSleepScore": {"value": 84, "qualifierKey": "GOOD"}, "sleepLevelsMap": {"deep": [{"x": 1}], "awake": [{"x": 1}, {"x": 2}]},
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

    print("\nthe figures in the activity file, and the richer day and night")
    import struct
    from garmin_mcp import official_fit

    def fit_file(messages):
        """FIT bytes from (global number, [(field number, base type byte, packed value bytes)]) messages."""
        # One local type (0-15) per message layout, defined once and reused.
        body, locals_ = b"", {}
        for global_num, fields in messages:
            layout = (global_num, tuple((n, b, len(pk)) for n, b, pk in fields))
            if layout not in locals_:
                local = locals_[layout] = len(locals_)
                body += bytes([0x40 | local, 0, 0]) + struct.pack("<HB", global_num, len(fields))
                for number, base, packed in fields:
                    body += bytes([number, len(packed), base])
            body += bytes([locals_[layout]]) + b"".join(packed for _, _, packed in fields)
        head = struct.pack("<BBHI4s", 14, 0x20, 2140, len(body), b".FIT")
        head += struct.pack("<H", fitdecode_crc(head))
        whole = head + body
        return whole + struct.pack("<H", fitdecode_crc(whole))

    def fitdecode_crc(data):
        from fitdecode.utils import compute_crc
        return compute_crc(data)

    u8, u16, u32 = (lambda v: struct.pack("<B", v)), (lambda v: struct.pack("<H", v)), (lambda v: struct.pack("<I", v))
    session = (18, [(24, 0x02, u8(25)), (137, 0x02, u8(4)), (91, 0x84, u16(2470)), (89, 0x84, u16(820)),
                    (132, 0x84, u16(780)), (134, 0x84, u16(10510)), (20, 0x84, u16(403)), (34, 0x84, u16(417)),
                    (65, 0x86, b"".join(u32(v) for v in (0, 2716000, 240000, 7000, 0, 0)))])
    zones_msgs = [(8, [(254, 0x84, u16(i)), (1, 0x02, u8(top))]) for i, top in enumerate((112, 130, 148, 166, 185))]
    fit_bytes = fit_file([session] + zones_msgs)
    figures = official_fit.summary(fit_bytes)
    check("the session's figures come out in Connect's names and units",
          figures.get("aerobicTrainingEffect") == 2.5 and figures.get("anaerobicTrainingEffect") == 0.4
          and figures.get("avgGroundContactTime") == 247 and figures.get("avgVerticalOscillation") == 8.2
          and figures.get("avgVerticalRatio") == 7.8 and figures.get("avgStrideLength") == 105.1
          and figures.get("avgPower") == 403 and figures.get("normPower") == 417, str(figures))
    check("time in each of the person's own zones, with the zone floors",
          figures.get("hrTimeInZone_1") == 2716 and figures.get("hrTimeInZone_2") == 240 and figures.get("hrTimeInZone_3") == 7
          and figures.get("hrZoneLow_2") == 112 and figures.get("hrZoneLow_5") == 166 and "hrZoneLow_1" not in figures,
          str(figures))
    check("rubbish isn't a file", official_fit.summary(b"not a fit file") == {})
    # A file whose session lacks the zone times: they are counted from the records.
    fit_epoch = 631065600  # FIT's 1989-12-31 reference, in Unix seconds
    records = [(20, [(253, 0x86, u32(i)), (3, 0x02, u8(hr))]) for i, hr in enumerate([100] * 5 + [120] * 10 + [140] * 4 + [170] * 2)]
    bare = (18, [(24, 0x02, u8(25))])
    counted = official_fit.summary(fit_file([bare] + zones_msgs + records))
    check("zones counted from the records when the session doesn't say",
          counted.get("hrTimeInZone_1") == 5 and counted.get("hrTimeInZone_2") == 10 and counted.get("hrTimeInZone_3") == 4
          and counted.get("hrTimeInZone_5") == 1, str(counted))
    fetched = []
    real_fetch = official.fetch_file
    official.fetch_file = lambda token, url: (fetched.append(url), fit_bytes)[1]
    try:
        kept = official.handle_push(json.dumps({"activityFiles": [
            {"userId": "garmin-user-1", "summaryId": "77-file", "activityId": 77, "fileType": "FIT",
             "startTimeInSeconds": run_start, "callbackURL": "https://apis.garmin.com/wellness-api/rest/activityFile?id=1&token=t"},
            {"userId": "somebody-else", "summaryId": "9-file", "activityId": 9, "fileType": "FIT",
             "callbackURL": "https://apis.garmin.com/wellness-api/rest/activityFile?id=2&token=t"}]}).encode())
    finally:
        official.fetch_file = real_fetch
    check("a linked account's activity file is fetched and read; a stranger's isn't",
          kept.get("activityFiles") == 1 and len(fetched) == 1 and "id=1" in fetched[0], str(kept))
    blobs = [store._cipher().decrypt(r[0]) for r in sqlite3.connect(DB).execute("SELECT data_enc FROM official_summaries WHERE kind = 'activityFit'")]
    check("only its figures are kept, not the file", len(blobs) == 1 and b".FIT" not in blobs[0] and b"hrTimeInZone_1" in blobs[0])
    try:
        official.fetch_file("x", "https://evil.example/file")
        elsewhere = True
    except official.OfficialError:
        elsewhere = False
    check("a callback address off Garmin's API is refused", not elsewhere)

    official.handle_push(json.dumps({"sleeps": [{**sleep, "summaryId": "sl-3", "timeOffsetSleepRespiration": {"0": 12.2, "60": 14.0, "120": 10.8},
                                                "timeOffsetSleepSpo2": {"0": 96, "60": 93, "120": 95}}],
                                     "pulseOx": [{"userId": "garmin-user-1", "summaryId": "po-1", "calendarDate": today,
                                                  "startTimeInSeconds": now - 7 * 3600, "timeOffsetSpo2Values": {"0": 97, "3600": 95}}]}).encode())
    night = store.official_summaries("garmin-user-1", "sleeps")[-1]
    check("a night keeps its breathing and SpO2 as averages, not curves",
          night.get("breathing") == {"average": 12.3, "lowest": 10.8, "highest": 14.0} and night.get("spo2") == {"average": 95, "lowest": 93}
          and "timeOffsetSleepSpo2" not in night, str(night))
    # The day's stress details again, fuller, as Garmin re-sends them through the day.
    stress_start = now - 8 * 3600
    official.handle_push(json.dumps({"stressDetails": [{
        "userId": "garmin-user-1", "summaryId": "st-2", "calendarDate": today, "startTimeInSeconds": stress_start,
        "startTimeOffsetInSeconds": 3600,
        "timeOffsetStressLevelValues": {str(180 * i): (80 if 20 <= i < 40 else 15) for i in range(60)},
        "timeOffsetBodyBatteryValues": {"0": 40, "180": 85, "360": 62},
        "bodyBatteryActivityEventList": [{"eventType": "SLEEP", "eventStartTimeInSeconds": stress_start, "eventStartTimeOffsetInSeconds": 3600,
                                          "duration": 27000, "bodyBatteryImpact": 48}]}]}).encode())
    kept_stress = next(r for r in store.official_summaries("garmin-user-1", "stressDetails") if r.get("summaryId") == "st-2")
    spell = (kept_stress.get("stressfulSpells") or [{}])[0]
    check("a stressful hour is kept as a spell with local times, the curve dropped",
          spell.get("minutes") == 60 and spell.get("peak") == 80 and spell.get("from") == time.strftime("%H:%M", time.gmtime(stress_start + 3600 + 3600))
          and "timeOffsetStressLevelValues" not in kept_stress, str(kept_stress))
    check("and what charged Body Battery", (kept_stress.get("bodyBatteryEvents") or [{}])[0].get("event", {}).get("bodyBatteryImpact") == 48)
    official.handle_push(json.dumps({"allDayRespiration": [
        {"userId": "garmin-user-1", "summaryId": "r-1", "startTimeInSeconds": now - 5 * 3600, "timeOffsetEpochToBreaths": {"0": 14, "60": 16}},
        {"userId": "garmin-user-1", "summaryId": "r-2", "startTimeInSeconds": now - 4 * 3600, "timeOffsetEpochToBreaths": {"0": 12, "60": -1}}]}).encode())
    breaths = store.official_summaries("garmin-user-1", "allDayRespiration")
    check("daytime breathing is kept as averages with their counts", [r.get("breaths") for r in breaths] == [{"average": 15.0, "n": 2}, {"average": 12.0, "n": 1}], str(breaths))
    # The same loop three days earlier, for "my usual route".
    earlier_start = run_start - 3 * 86400
    official.handle_push(json.dumps({
        "activities": [{**run, "summaryId": "70", "activityId": 70, "activityName": "Same loop", "startTimeInSeconds": earlier_start, "durationInSeconds": 840}],
        "activityDetails": [{**detail, "summaryId": "70-detail", "activityId": 70,
                             "summary": {**detail["summary"], "startTimeInSeconds": earlier_start, "durationInSeconds": 840},
                             "samples": [{**x, "startTimeInSeconds": earlier_start + i * 10} for i, x in enumerate(samples)],
                             "laps": []}]}).encode())

    print("\nthe everyday tools, on the official API alone")
    import anyio
    from garmin_mcp import server
    official.handle_push(json.dumps({"dailies": [{
        "userId": "garmin-user-1", "summaryId": "d-today", "calendarDate": today, "startTimeInSeconds": now - 6 * 3600,
        "steps": 10170, "stepsGoal": 16900, "restingHeartRateInBeatsPerMinute": 43, "activeKilocalories": 694,
        "bmrKilocalories": 2078, "averageStressLevel": 14, "moderateIntensityDurationInSeconds": 120,
        "vigorousIntensityDurationInSeconds": 3480}]}).encode())

    def as_user(token, fn, *args):
        bound = official.use(token)
        try:
            return anyio.run(fn, *args)
        finally:
            official.reset(bound)

    check("off until asked for: nothing changes", as_user(bart, lambda: _async(server._official_only)) is None)
    switched = as_user(bart, server.garmin_official, "official_only")
    check("the preview tool turns it on", switched.get("official_only") is True, str(switched))
    sleep_answer = as_user(bart, server.get_sleep_data, today)
    check("sleep comes from Garmin's official API, in the usual shape",
          sleep_answer.get("source", "").startswith("Garmin's official API") and sleep_answer["score"]["overall"] == 84
          and sleep_answer["stages"]["deep"]["minutes"] == 90 and sleep_answer["average_hrv_ms"] == 61, str(sleep_answer))
    day_answer = as_user(bart, server.get_daily_summary, today)
    check("so does the day, Body Battery from its highs and lows",
          day_answer.get("steps") == 10170 and day_answer["calories"]["total"] == 2772
          and day_answer["body_battery"]["highest"] == 85 and day_answer["intensity_minutes"]["vigorous"] == 58,
          str(day_answer))
    listed = as_user(bart, server.get_activities, 5)
    check("and the activity list, newest first", listed.get("count") == 2 and listed["activities"][0]["name"] == "Morning run"
          and listed["activities"][0]["pace_per_km"] == "5:30 /km", str(listed)[:300])
    detail_answer = as_user(bart, server.get_activity_details, 77)
    check("and a run's laps as the watch took them, with kilometre splits worked out inside",
          detail_answer.get("splits_count") == 2 and detail_answer["splits"][0]["pace_per_km"]
          and len((detail_answer.get("inside") or {}).get("km_splits") or []) == 2, str(detail_answer)[:300])
    check("with the file's zones, dynamics, training effect and power, as Connect's answer has them",
          detail_answer.get("hr_zones", [{}])[0].get("percent") == 91.7 and detail_answer["summary"]["running_dynamics"]["ground_contact_ms"] == 247
          and detail_answer["summary"]["training_effect"] == {"aerobic": 2.5, "anaerobic": 0.4}
          and detail_answer["summary"]["power"]["average_w"] == 403, str(detail_answer)[:600])
    check("and the list shows them too", listed["activities"][0].get("hr_zones") and listed["activities"][0].get("training_effect"), str(listed)[:300])
    check("moving time, the hills and the same loop run before come with it",
          detail_answer["summary"].get("moving_time") == "13m 12s" and isinstance(detail_answer.get("terrain"), dict)
          and detail_answer["splits"][0].get("avg_cadence_spm") == 178
          and sum(x.get("elevation_gain_m") or 0 for x in detail_answer["splits"]) == 30
          and isinstance(detail_answer.get("same_route"), dict), str({k: detail_answer.get(k) for k in ("terrain", "same_route")})[:400])
    sleep_again = as_user(bart, server.get_sleep_data, today)
    check("the night brings breathing and SpO2, how often awake, and what sleep did to Body Battery",
          sleep_again.get("breathing", {}).get("average") == 12.3 and sleep_again.get("spo2", {}).get("lowest") == 93
          and sleep_again.get("spo2", {}).get("usual_7_day") == 96 and sleep_again.get("awake_count") == 2
          and sleep_again.get("body_battery_change") == 48, str(sleep_again))
    day_again = as_user(bart, server.get_daily_summary, today)
    check("the day brings its stressful spells, Body Battery events, SpO2 and the week's resting heart rate",
          (day_again.get("stressful_spells") or [{}])[0].get("minutes") == 60 and (day_again.get("body_battery_events") or [{}])[0].get("impact") == 48
          and day_again.get("spo2_average") == 96 and day_again["heart_rate"].get("resting_7day_avg_bpm") == 46
          and day_again.get("respiration_avg") == 14.0 and day_again["body_battery"].get("gained_during_sleep") == 48, str(day_again)[:700])
    missing = as_user(bart, server.get_sleep_data, "2026-01-01")
    check("a night from before connecting says so, without falling back",
          "from before the account connected" in missing.get("note", ""), str(missing))
    future = as_user(bart, server.get_sleep_data, "2099-01-01")
    check("a night not synced yet says it arrives as the watch syncs", "hasn't sent" in future.get("note", ""), str(future))

    print("\na recovery check, on the official API alone")
    from datetime import date as _date, timedelta as _td
    nights = [(_date.fromisoformat(today) - _td(days=k)).isoformat() for k in range(7, 0, -1)]
    official.handle_push(json.dumps({"hrv": [
        {"userId": "garmin-user-1", "summaryId": f"h-{d}", "calendarDate": d, "startTimeInSeconds": now - (8 - i) * 86400,
         "lastNightAvg": 62} for i, d in enumerate(nights)
    ] + [{"userId": "garmin-user-1", "summaryId": "h-today", "calendarDate": today, "startTimeInSeconds": now - 8 * 3600,
          "lastNightAvg": 50}]}).encode())
    from garmin_mcp import official_data as od
    checked = od.recovery("garmin-user-1", today)
    hrv_signal = next((x for x in checked.get("signals", []) if x["what"] == "hrv"), {})
    check("last night's HRV is set against their own usual, and a low one is flagged",
          checked["hrv"] == {"last_night_ms": 50, "usual_ms": 62, "nights_in_usual": 7, "baseline": "early"} and hrv_signal.get("ok") is False
          and "lower than normal" in hrv_signal.get("said", ""), str(checked)[:500])
    check("sleep and resting heart rate count too, against the week",
          any(x["what"] == "sleep" and x["ok"] for x in checked["signals"])
          and checked["resting_hr"]["today"] == 43, str(checked.get("signals")))
    check("with one thing off it says go carefully, and says plainly whose read it is",
          checked["call"] in ("fine_with_care", "go_easy") and "isn't shared" in checked["what_this_is"], checked["call"])
    check("a day with nothing sent has nothing to say", od.recovery("garmin-user-1", "2026-01-01") is None)
    check("a week of nights is an early baseline, and the answer says so",
          checked["hrv"].get("baseline") == "early" and any("7 of 21" in n for n in checked.get("notes") or []),
          str(checked.get("notes")))
    # Pushes are kept only for Garmin accounts linked here.
    for gid in ("garmin-user-new", "garmin-user-nohrv"):
        official.store.save_official_user(gid, {"access_token": "a", "refresh_token": "r", "expires_in": 3600}, ["HEALTH_EXPORT"])
    few = [(_date.fromisoformat(today) - _td(days=k)).isoformat() for k in range(3, 0, -1)]
    official.handle_push(json.dumps({"hrv": [
        {"userId": "garmin-user-new", "summaryId": f"hn-{d}", "calendarDate": d, "startTimeInSeconds": now - (4 - i) * 86400,
         "lastNightAvg": 70} for i, d in enumerate(few)
    ] + [{"userId": "garmin-user-new", "summaryId": "hn-today", "calendarDate": today, "startTimeInSeconds": now - 8 * 3600,
          "lastNightAvg": 40}], "sleeps": [{"userId": "garmin-user-new", "summaryId": "sn-today", "calendarDate": today,
          "startTimeInSeconds": now - 8 * 3600, "durationInSeconds": 4 * 3600, "overallSleepScore": {"value": 45}}]}).encode())
    early = od.recovery("garmin-user-new", today)
    check("with three nights connected there's no call, only what it can see, and how many nights it still needs",
          early["call"] == "not_enough_data" and early["hrv"].get("baseline") == "building"
          and any("3 of 7" in n for n in early.get("notes") or []) and any(x["what"] == "sleep" for x in early["signals"]),
          str({k: early.get(k) for k in ("call", "hrv", "notes")}))
    official.handle_push(json.dumps({"sleeps": [
        {"userId": "garmin-user-nohrv", "summaryId": f"so-{k}", "startTimeInSeconds": now - (k + 1) * 86400,
         "calendarDate": (_date.fromisoformat(today) - _td(days=k)).isoformat(), "durationInSeconds": 8 * 3600,
         "overallSleepScore": {"value": 80}} for k in range(0, 9)]}).encode())
    no_hrv = od.recovery("garmin-user-nohrv", today)
    check("a watch that never sends HRV doesn't wait for it forever",
          no_hrv["call"] != "not_enough_data" or len([x for x in no_hrv["signals"] if x["what"] != "recent_hard"]) < 2,
          str({k: no_hrv.get(k) for k in ("call", "signals", "notes")}))
    for gid in ("garmin-user-new", "garmin-user-nohrv"):
        official.store.forget_official_user(gid)
    real_has_connect = server.store.has_connect
    server.store.has_connect = lambda _t: False
    try:
        listed_answer = as_user(bart, server.get_readiness, today)
    finally:
        server.store.has_connect = real_has_connect
    check("for an account on the official API alone, get_readiness answers with it",
          listed_answer.get("call") == checked["call"] and listed_answer.get("source", "").startswith("Garmin's official API"),
          str(listed_answer)[:300])
    check("and it's listed for them, with a plain description",
          "get_readiness" not in server.OFFICIAL_MISSING and "Garmin's own readiness score" in server.OFFICIAL_DESCRIPTIONS["get_readiness"])

    print("\nwriting through the Training API")
    from garmin_mcp.official_client import OfficialClient
    client = as_user(bart, lambda: _async(server._official_client))
    check("an official-only account gets the official client, which has no answer for what the API lacks",
          isinstance(client, OfficialClient) and getattr(client, "get_readiness_data", None) is None)
    CALLED.clear()
    made = as_user(bart, server.create_workout, "Threshold Tuesday", [
        {"type": "warmup", "duration_seconds": 600},
        {"type": "repeat", "times": 3, "steps": [
            {"type": "interval", "distance_meters": 1000, "pace": ["3:55", "4:05"]},
            {"type": "recovery", "duration_seconds": 120}]},
        {"type": "cooldown", "duration_seconds": 600}], "running")
    wid = made.get("workout_id")
    posted = [c for c in CALLED if c[:2] == ("POST", "/workoutportal/workout/v2")]
    body = posted[0][2] if posted else {}
    steps = (body.get("segments") or [{}])[0].get("steps") or []
    check("create_workout goes to the Training API, as a running workout with a repeat",
          isinstance(wid, int) and body.get("sport") == "RUNNING" and body.get("workoutName") == "Threshold Tuesday"
          and len(steps) == 3 and steps[1]["type"] == "WorkoutRepeatStep" and steps[1]["repeatValue"] == 3.0
          and steps[1]["steps"][0]["durationType"] == "DISTANCE" and steps[1]["steps"][0]["targetType"] == "SPEED"
          and steps[1]["steps"][0]["targetValueLow"] < steps[1]["steps"][0]["targetValueHigh"]
          and steps[0]["stepOrder"] == 1 and steps[1]["stepOrder"] == 2 and steps[1]["steps"][1]["stepOrder"] == 4
          and steps[2]["stepOrder"] == 5, str(made)[:200] + " " + str(body)[:400])
    kept = store.official_workout("garmin-user-1", wid)
    check("and is kept here, since the API can't list it back",
          kept is not None and kept["connect"]["workoutName"] == "Threshold Tuesday" and kept["created"]["ownerId"] == 4242, str(kept)[:200])
    listed_w = as_user(bart, server.list_workouts, 10)
    check("list_workouts shows it", listed_w.get("count") == 1 and listed_w["workouts"][0]["workout_id"] == wid
          and listed_w["workouts"][0]["sport"] == "running" and listed_w["workouts"][0]["estimated_duration"], str(listed_w))
    read_w = as_user(bart, server.get_workout, wid)
    check("get_workout reads it back with its steps",
          read_w.get("name") == "Threshold Tuesday" and len(read_w.get("steps") or []) >= 3, str(read_w)[:300])
    put = as_user(bart, server.schedule_workout, wid, "2026-11-03")
    check("schedule_workout goes to the Training API and remembers the schedule",
          isinstance(put.get("schedule_id"), int) and CALLED[-1][:2] == ("POST", "/training-api/schedule/")
          and CALLED[-1][2] == {"workoutId": wid, "date": "2026-11-03"}
          and store.official_schedules("garmin-user-1", "2026-11-01", "2026-12-01") == [(put["schedule_id"], wid, "2026-11-03")], str(put))
    cleared = as_user(bart, server.unschedule_workout, "2026-11-03")
    check("unschedule_workout by date finds it in the kept calendar and deletes the schedule",
          cleared.get("unscheduled") == put["schedule_id"] and cleared.get("name") == "Threshold Tuesday"
          and CALLED[-1][:2] == ("DELETE", f"/training-api/schedule/{put['schedule_id']}")
          and store.official_schedules("garmin-user-1", "2026-11-01", "2026-12-01") == [], str(cleared))
    renamed = as_user(bart, server.update_workout, wid, "Threshold Wednesday")
    check("update_workout puts the renamed workout, with Garmin's owner id",
          renamed.get("name") == "Threshold Wednesday" and CALLED[-1][:2] == ("PUT", f"/training-api/workout/v2/{wid}")
          and CALLED[-1][2]["workoutId"] == wid and CALLED[-1][2]["ownerId"] == 4242
          and CALLED[-1][2]["workoutName"] == "Threshold Wednesday"
          and store.official_workout("garmin-user-1", wid)["connect"]["workoutName"] == "Threshold Wednesday", str(renamed) + str(CALLED[-1])[:300])
    gym = as_user(bart, server.create_strength_workout, "Push day", [
        {"exercise": "Barbell Bench Press", "sets": 4, "reps": 8, "rest_seconds": 120, "weight_kg": 70},
        {"exercise": "Push Up", "sets": 3, "reps": 12, "rest_seconds": 60}])
    gym_body = CALLED[-1][2] if CALLED[-1][:2] == ("POST", "/workoutportal/workout/v2") else {}
    gym_steps = (gym_body.get("segments") or [{}])[0].get("steps") or []
    bench = gym_steps[0]["steps"][0] if gym_steps and gym_steps[0].get("steps") else {}
    check("create_strength_workout goes as STRENGTH_TRAINING, sets as repeats, reps and kilograms on the set",
          isinstance(gym.get("workout_id"), int) and gym_body.get("sport") == "STRENGTH_TRAINING"
          and gym_steps[0]["type"] == "WorkoutRepeatStep" and gym_steps[0]["repeatValue"] == 4.0
          and bench.get("durationType") == "REPS" and bench.get("durationValue") == 8.0
          and bench.get("exerciseCategory") == "BENCH_PRESS" and bench.get("exerciseName") and bench.get("weightValue") == 70.0
          and bench.get("weightDisplayUnit") == "KILOGRAM" and gym_steps[0]["steps"][1]["durationType"] == "TIME", str(gym)[:200] + str(gym_steps)[:500])
    asked = as_user(bart, server.delete_workout, gym["workout_id"])
    gone = as_user(bart, server.delete_workout, gym["workout_id"], "Push day")
    check("delete_workout asks first, then deletes at Garmin and here",
          asked.get("confirmation_required") is True and gone.get("deleted") is not None
          and CALLED[-1][:2] == ("DELETE", f"/training-api/workout/v2/{gym['workout_id']}")
          and store.official_workout("garmin-user-1", gym["workout_id"]) is None, str(asked)[:150] + str(gone)[:150])
    monday = date.fromisoformat(today) + timedelta(days=(7 - date.fromisoformat(today).weekday()) % 7 or 7)
    block = as_user(bart, server.create_plan, "10k in 42 minutes", [
        {"date": (monday + timedelta(days=d)).isoformat(), "name": n, "steps": st, "phase": "Build"}
        for d, n, st in (
            (1, "Easy 8k", [{"type": "interval", "distance_meters": 8000}]),
            (3, "Threshold 5x1k", [{"type": "warmup", "duration_seconds": 600},
                                   {"type": "repeat", "times": 5, "steps": [{"type": "interval", "distance_meters": 1000, "pace": "4:05"},
                                                                            {"type": "recovery", "duration_seconds": 90}]},
                                   {"type": "cooldown", "duration_seconds": 600}]),
            (6, "Long 16k", [{"type": "interval", "distance_meters": 16000}]),
            (8, "Easy 8k", [{"type": "interval", "distance_meters": 8000}]),
            (10, "Hills 8x200", [{"type": "warmup", "duration_seconds": 600},
                                 {"type": "repeat", "times": 8, "steps": [{"type": "interval", "distance_meters": 200},
                                                                          {"type": "recovery", "duration_seconds": 90}]},
                                 {"type": "cooldown", "duration_seconds": 600}]),
            (13, "Long 18k", [{"type": "interval", "distance_meters": 18000}]),
        )], "T10")
    check("create_plan writes every session through the Training API and schedules each",
          sum(len(w.get("sessions") or []) for w in (block.get("weeks") or [])) == 6 and block.get("label") == "T10"
          and all(s.get("workout_id") and s.get("schedule_id") for w in block["weeks"] for s in w["sessions"])
          and len([c for c in CALLED if c[:2] == ("POST", "/training-api/schedule/")]) == 7
          and len(store.official_schedules("garmin-user-1", monday.isoformat(), (monday + timedelta(days=14)).isoformat())) == 6,
          str(block)[:400])
    card = as_user(bart, server.get_plan, "T10")
    card_text = json.dumps(card, default=str)
    check("get_plan draws the block from the kept calendar and workouts",
          "T10" in card_text and "Threshold 5x1k" in card_text and "error" not in (card if isinstance(card, dict) else {}), card_text[:300])
    removed = as_user(bart, server.remove_plan, "T10", "T10")
    check("remove_plan unschedules and deletes the block's sessions",
          store.official_schedules("garmin-user-1", monday.isoformat(), (monday + timedelta(days=14)).isoformat()) == []
          and len(store.official_workouts("garmin-user-1")) == 1, str(removed)[:300])
    check("someone without an official link is never switched", as_user("someone-else", lambda: _async(server._official_only)) is None)
    back = as_user(bart, server.garmin_official, "both")
    check("and 'both' goes back to Garmin Connect",
          back.get("official_only") is False and as_user(bart, lambda: _async(server._official_only)) is None)
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

    print("\nsigning in with a password after approving on Garmin's page")
    joe = store.save_official_user("garmin-joe", {"access_token": "a", "refresh_token": "r",
                                                  "access_expires_at": 0, "refresh_expires_at": 0}, ["ACTIVITY_EXPORT"])
    store.set_user_email(joe, "joe@example.com", "j***@example.com")
    check("an approval on Garmin's page alone reads only from the official API",
          store.official_only(joe) == "garmin-joe" and not store.has_connect(joe))
    joe_again = store.save_user("j***@example.com", "{}", email_hash=store.email_fingerprint("joe@example.com"),
                                email="joe@example.com")
    check("the password sign-in keeps the link, so Garmin's pushes still arrive",
          joe_again != joe and store.official_link(joe_again).garmin_user_id == "garmin-joe")
    check("but reads go back to Garmin Connect, which has the history",
          store.has_connect(joe_again) and store.official_only(joe_again) is None)
    store.set_official_only(joe_again, True)
    store.save_user("j***@example.com", "{}", user_token=joe_again,
                    email_hash=store.email_fingerprint("joe@example.com"), email="joe@example.com")
    check("and signing in again with the password clears it again",
          store.official_only(joe_again) is None)
    # The connector's own path: it reuses the account the address already has
    # (hosted._finish passes it as user_token), so the row is the official-route
    # one with a session written into it.
    sam = store.save_official_user("garmin-sam", {"access_token": "a", "refresh_token": "r",
                                                  "access_expires_at": 0, "refresh_expires_at": 0}, None)
    store.set_user_email(sam, "sam@example.com", "s***@example.com")
    sam_hash = store.email_fingerprint("sam@example.com")
    kept = store.save_user("s***@example.com", "{}", user_token=store.tokens_for(sam_hash)[-1],
                           email_hash=sam_hash, email="sam@example.com")
    check("signing in over the account an approval made keeps that account and its link",
          kept == sam and store.official_link(sam).garmin_user_id == "garmin-sam")
    check("and it stops being official-route: a session stands behind it and reads use it",
          store.has_connect(sam) and store.official_only(sam) is None)
    only = store.save_official_user("garmin-only", {"access_token": "a", "refresh_token": "r",
                                                    "access_expires_at": 0, "refresh_expires_at": 0}, None)
    store.save_user("o***@example.com", "{}", email_hash="hash-other")
    check("someone else's password sign-in leaves an official-only account alone",
          store.official_only(only) == "garmin-only")
    # Later checks count the accounts linked, so these go again.
    import sqlite3 as _sqlite
    with _sqlite.connect(DB) as conn:
        for gone in ("garmin-joe", "garmin-sam", "garmin-only"):
            conn.execute("DELETE FROM official_links WHERE garmin_user_id = ?", (gone,))
        conn.execute("DELETE FROM users WHERE user_token IN (?, ?, ?, ?) OR email_hash = 'hash-other'",
                     (joe, joe_again, sam, only))

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
          sent == {"workout_id": IDS[0], "name": "Threshold", "schedule_id": IDS[1], "scheduled_for": "2026-10-24"}
          and CALLED[-2][1] == "/workoutportal/workout/v2" and CALLED[-1][2] == {"workoutId": IDS[0], "date": "2026-10-24"},
          str(sent))

    print("\nasking Garmin for the history")
    from garmin_mcp import backfill
    check("connecting queued the account's history",
          store.backfill_state("garmin-user-1") is not None, str(store.backfill_state("garmin-user-1")))
    import sqlite3 as _sq
    with _sq.connect(DB) as conn:
        conn.execute("DELETE FROM official_backfill")
    anchor = 1_800_000_000
    D = backfill.DAY
    store.queue_backfill("garmin-user-1", anchor)
    check("an account is queued once", store.queue_backfill("garmin-user-1", anchor) is False)
    ASKED: list[tuple[str, int, int]] = []
    ANSWERS: dict[tuple[str, int], int] = {}

    def fake_ask(user_token, kind, start, end):
        ASKED.append((kind, start, end))
        return ANSWERS.get((kind, round((end - start) / D)), 202)

    first = backfill.run_once(fake_ask)
    check("the last month of runs is asked for first, newest window first",
          ASKED[-1] == ("activities", anchor - 30 * D, anchor) and first["status"] == 202, str(ASKED[-1]))
    backfill.run_once(fake_ask)
    check("then the last month of runs' laps and splits", ASKED[-1] == ("activityDetails", anchor - 30 * D, anchor))
    ANSWERS[("sleeps", 30)] = 400
    backfill.run_once(fake_ask)
    backfill.run_once(fake_ask)
    check("a window Garmin calls too wide is asked again at half the width",
          ASKED[-2] == ("sleeps", anchor - 30 * D, anchor) and ASKED[-1] == ("sleeps", anchor - 15 * D, anchor), str(ASKED[-2:]))
    backfill.run_once(fake_ask)
    check("and carries on from where that one reached", ASKED[-1] == ("sleeps", anchor - 30 * D, anchor - 15 * D), str(ASKED[-1]))
    ANSWERS[("dailies", 30)] = 409
    backfill.run_once(fake_ask)
    backfill.run_once(fake_ask)
    check("a window asked for already counts as asked", ASKED[-1][0] == "hrv", str(ASKED[-1]))
    ANSWERS[("userMetrics", 30)] = 429
    waited = backfill.run_once(fake_ask)
    held = store.backfill_state("garmin-user-1")[1]["userMetrics"]
    check("when Garmin asks Daash to slow down, it waits and nothing moves on",
          waited.get("wait") == 60 and "reached" not in held and held["said"] == "429", str(waited))
    ANSWERS[("userMetrics", 30)] = 412
    for _ in range(40):
        if backfill.run_once(fake_ask) in (None, {"done": True}):
            break
    anchor_, progress, done = store.backfill_state("garmin-user-1")
    asked_runs = [a for a in ASKED if a[0] == "activities"]
    check("runs are asked for back a month, never further, as Garmin allows",
          min(a[1] for a in asked_runs) == anchor - 30 * D, str(asked_runs))
    check("a kind the person didn't share is left out, not asked again and again",
          [a for a in ASKED if a[0] == "userMetrics"] == [("userMetrics", anchor - 30 * D, anchor)] * 2
          and progress["userMetrics"]["reached"] == backfill.EXHAUSTED)
    check("once everything is asked for, the account is done", done and backfill.run_once(fake_ask) is None)
    shown = official.status(again)["history"]
    check("the status says how far back each kind was asked for",
          shown["finished"] and shown["asked_for"]["activities"] == "back to " + time.strftime("%Y-%m-%d", time.gmtime(anchor - 30 * D))
          and shown["asked_for"]["userMetrics"] == "not available (Garmin said 412)", str(shown))
    check("an account asked for the old way is asked again from the start",
          store.reopen_backfills(backfill.VERSION + 1) == 1 and not store.backfill_state("garmin-user-1")[2]
          and store.backfill_state("garmin-user-1")[1] == {})

    with _sq.connect(DB) as conn:
        conn.execute("DELETE FROM official_backfill")
    store.queue_backfill("garmin-user-1", anchor)
    ANSWERS.clear()
    ANSWERS[("activities", 30)] = 400
    ANSWERS[("activities", 15)] = 400
    ANSWERS[("activities", 7)] = 400
    ANSWERS[("activities", 3)] = 400
    ANSWERS[("activities", 1)] = 400
    for _ in range(5):
        backfill.run_once(fake_ask)
    check("a one-day window refused means nothing that old, and the next kind comes up",
          store.backfill_state("garmin-user-1")[1]["activities"]["reached"] == backfill.EXHAUSTED
          and backfill.run_once(fake_ask)["kind"] == "activityDetails")

    def unreachable(user_token, kind, start, end):
        return 0
    for _ in range(backfill.MAX_FAILS):
        missed = backfill.run_once(unreachable)
    check("Garmin out of reach is tried again later, then left be",
          missed.get("wait") == 30 and store.backfill_state("garmin-user-1")[2])
    check("the request goes to Garmin's backfill address for the window",
          backfill.path("sleeps", 10, 20) == "/wellness-api/rest/backfill/sleeps?summaryStartTimeInSeconds=10&summaryEndTimeInSeconds=20")
    check("everyone linked before this gets their history queued too",
          store.queue_backfill_for_all_links() == 0)
    with _sq.connect(DB) as conn:
        conn.execute("DELETE FROM official_backfill")
    check("and once the queue is empty, they're added", store.queue_backfill_for_all_links() == 1)

    print("\ndisconnecting")
    check("disconnecting tells Garmin and deletes everything kept",
          official.disconnect(again) and CALLED[-1][:2] == ("DELETE", "/partner-gateway/rest/user/registration")
          and store.official_link(again) is None and store.official_summaries("garmin-user-1", "dailies") == []
          and store.backfill_state("garmin-user-1") is None)
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
