"""Matching runs to planned sessions, version 2 (Nov 26).

    python tests/match_test.py

Bart's own week first: Thursday an easy 12 km, Friday a 9 km run club, and
7.8 km run at lunch on Thursday. The first matcher counted it as Friday's run
club, done early, and left Thursday to do. It's a partial of Thursday now.
"""

from __future__ import annotations

import json

import asyncio
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = tempfile.mkdtemp()
os.environ["GARMIN_MCP_DB"] = str(Path(tmp) / "match-test.sqlite3")
from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()

from garmin_mcp import store  # noqa: E402

store.DB_PATH = Path(os.environ["GARMIN_MCP_DB"])
from garmin_mcp import analytics, feeling, matching, training_plan  # noqa: E402
from garmin_mcp.progress import Actual, Planned, is_fixed_day, match_v2  # noqa: E402

failures: list[str] = []
sent: list[tuple[str, dict]] = []
analytics.capture = lambda event, subject=None, properties=None, **_: sent.append((event, dict(properties or {})))


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


MON = date(2026, 11, 23)
TUE, WED, THU, FRI, SAT, SUN = (date(2026, 11, d) for d in range(24, 30))


def item(sid: int, day: date, title: str, wid: int, sport: str = "running") -> dict:
    return {"id": sid, "date": day.isoformat(), "workoutId": wid, "title": title, "sportTypeKey": sport,
            "itemType": "workout"}


def run(aid: int, day: date, km: float, name: str = "Lunch run", minutes: float | None = None) -> dict:
    return {"activityId": aid, "startTimeLocal": f"{day.isoformat()} 12:30:00", "activityName": name,
            "activityType": {"typeKey": "running"}, "distance": km * 1000,
            "duration": (minutes or km * 5.5) * 60}


def plan(items, activities, today, *, metres, efforts=None, matching_ctx=None):
    return training_plan.summarise_calendar(
        items, activities, today, planned_metres=metres, planned_seconds={k: v / 1000 * 330 for k, v in metres.items()},
        weekly_km=True, efforts=efforts or {}, matching=matching_ctx if matching_ctx is not None else {"choices": {}})


def row(result, sid):
    return next(s for w in result["weeks"] for s in w["sessions"] if s.get("schedule_id") == sid)


def extras(result):
    return [x for w in result["weeks"] for x in w.get("extra") or []]


print("Bart's week: 7.8 km on Thursday's easy 12 km, Friday's 9 km run club ahead")
items = [item(101, THU, "Easy 12 km", 11), item(102, FRI, "9 km run club", 12)]
metres = {11: 12000, 12: 9000}
result = plan(items, [run(1, THU, 7.8)], THU, metres=metres)
thu, fri = row(result, 101), row(result, 102)
check("Thursday's 12 km is a partial: 7.8 of 12 km", thu["status"] == "done" and thu.get("completion") == "partial"
      and thu["match"]["reason"] == "same_day" and thu["match"]["actual_km"] == 7.8 and thu["match"]["planned_km"] == 12,
      str({k: thu.get(k) for k in ("status", "completion", "match")}))
check("nothing moved: Friday's run club is still ahead, on its day", fri["status"] == "ahead" and not fri.get("done_on"))
check("the run club is a fixed-day session", fri.get("fixed_day") is True and is_fixed_day("9 km run club"))
easy_lunch = {**run(1, THU, 7.8), "trainingEffectLabel": "AEROBIC_BASE"}
check("the old matcher got it wrong (why this exists)",
      row(training_plan.summarise_calendar(items, [easy_lunch], THU, planned_metres=metres,
                                          planned_seconds={11: 3960, 12: 2970}, weekly_km=True,
                                          efforts={11: "easy", 12: "easy"}), 102)["status"] == "done, moved")
result = plan(items, [easy_lunch], THU, metres=metres, efforts={11: "easy", 12: "easy"})
check("and the new one doesn't, with Garmin's easy label and efforts read too",
      row(result, 101).get("completion") == "partial" and row(result, 102)["status"] == "ahead")

print("\ncompletion bands")
P = [Planned(THU, "s", "run", metres=10000, title="Easy 10 km")]
for km, want in ((8.5, "done"), (12, "done"), (8.4, "partial"), (4.0, "partial"), (3.9, None)):
    r = match_v2(P, [Actual(THU, "run", "run", metres=km * 1000, activity_id=9)], THU)
    got = r.done[0]["completion"] if r.done else None
    check(f"{km} km of 10 km is {want or 'an extra run, the session left as it was'}", got == want,
          f"{got} {[x.get('reason') for x in r.extra]}")

print("\na true catch-up")
items2 = [item(201, WED, "Easy 10 km", 21)]
result = plan(items2, [run(2, THU, 10.2)], THU, metres={21: 10000})
wed = row(result, 201)
check("yesterday's missed 10 km, run today on a rest day, counts for yesterday",
      wed["status"] == "done, moved" and wed["match"]["reason"] == "missed_catchup" and wed["done_on"] == THU.isoformat())
result = plan(items2, [run(2, THU, 6.0)], THU, metres={21: 10000})
check("but not when it's more than 20% off: then it's an extra run",
      row(result, 201)["status"] == "missed" and any(x.get("matched") == "no_session" for x in extras(result)))
result = plan([item(203, MON, "Easy 10 km", 23)], [run(3, THU, 10)], THU, metres={23: 10000})
check("and not from 3 days back", row(result, 203)["status"] == "missed")

print("\nnever pulled forward past today's own session")
items3 = [item(301, THU, "Easy 12 km", 31), item(302, FRI, "Easy 8 km", 32)]
result = plan(items3, [run(4, THU, 8.0)], THU, metres={31: 12000, 32: 8000})
check("8 km on a 12 km day with an 8 km run tomorrow: today's, partial", row(result, 301).get("completion") == "partial"
      and row(result, 302)["status"] == "ahead")

print("\nthe fixed-day flag")
items4 = [item(401, FRI, "9 km run club", 41)]
result = plan(items4, [run(5, THU, 9.0)], THU, metres={41: 9000})
check("a run on a free Thursday doesn't pull Friday's run club forward",
      row(result, 401)["status"] == "ahead" and any(x.get("matched") == "no_session" for x in extras(result)))
items4b = [item(402, FRI, "Easy 9 km", 42)]
result = plan(items4b, [run(5, THU, 9.0)], THU, metres={42: 9000})
check("an ordinary Friday easy run is pulled forward (within 20%)", row(result, 402)["status"] == "done, moved"
      and row(result, 402)["match"]["reason"] == "forward_pull")
result = plan(items4b, [run(5, THU, 9.0)], THU, metres={42: 9000}, matching_ctx={"choices": {}, "fixed": ["402"]})
check("and isn't once it's marked fixed-day", row(result, 402)["status"] == "ahead")
check("a race and a group run are fixed by name", is_fixed_day("Valencia Marathon race") and is_fixed_day("Group long run")
      and not is_fixed_day("Easy 12 km") and not is_fixed_day("HM pace 3 x 3 km"))

print("\nwhen it's unclear, it asks")
items5 = [item(501, THU, "Easy 8 km", 51), item(502, THU, "Easy 8 km + strides", 52)]
result = plan(items5, [run(6, THU, 8.0)], THU, metres={51: 8000, 52: 8100})
q = [x for x in extras(result) if x.get("question")]
check("two of today's sessions fit about as well: a question, nothing counted",
      len(q) == 1 and len(q[0]["question"]) == 2 and row(result, 501)["status"] == "today"
      and row(result, 502)["status"] == "today", str(q))
items6 = [item(601, THU, "Easy 12 km", 61), item(602, FRI, "Tempo 9 km", 62)]
result = plan(items6, [run(7, THU, 9.0, name="Tempo 9 km")], THU, metres={61: 12000, 62: 9000})
q = [x for x in extras(result) if x.get("question")]
check("started from Friday's workout on a day with its own session: asked, plan unchanged",
      len(q) == 1 and {o["schedule"] for o in q[0]["question"]} == {"601", "602"}
      and row(result, 601)["status"] == "today" and row(result, 602)["status"] == "ahead", str(q))
from garmin_mcp import app  # noqa: E402
from garmin_mcp import server  # noqa: E402

view = app._question(q[0], THU)
check("the card asks it in words, days by name and date", view["text"] == "Was this Thu 26 Easy 12 km or Fri 27 Tempo 9 km?",
      view["text"])

print("\nthe person's choice comes first")
result = plan(items, [run(1, THU, 7.8)], THU, metres=metres, matching_ctx={"choices": {"1": "102"}})
check("counted as Friday's run club when they say so", row(result, 102)["status"] == "done, moved"
      and row(result, 102)["match"]["reason"] == "user_choice" and row(result, 101)["status"] == "today")
result = plan(items, [run(1, THU, 7.8)], THU, metres=metres, matching_ctx={"choices": {"1": "extra"}})
check("or as an extra run", row(result, 101)["status"] == "today"
      and any(x.get("matched") == "user_extra" for x in extras(result)))

print("\nthe line on each match")
result = plan(items, [run(1, THU, 7.8)], THU, metres=metres)
view = app._match_view(row(result, 101), THU)
# Nov 27: a same-day, same-type match says nothing more than its stats.
check("same day, same type: no line, just 7.8 of 12 km and a neutral-or-amber tone",
      view.get("match_line") is None and view.get("of_line") == "7.8 of 12 km" and view.get("out_tone") == "amber"
      and view.get("match_aid"), str(view))
view_next_day = app._match_view(row(result, 101), FRI)
check("viewed the next morning it still says nothing about yesterday (the Thu 26 bug)",
      view_next_day.get("match_line") is None, str(view_next_day.get("match_line")))
result = plan(items4b, [run(5, THU, 9.0)], THU, metres={42: 9000})
view = app._match_view(row(result, 402), THU)
check("a moved one says where from, and nothing else", view.get("match_line") == "Moved from Fri 27"
      and view.get("line_reason") == "moved", str(view))

print("\nundo puts the plan and the watch back")
user_token = "tok-bart-0123456789"
store.save_user("b***@example.com", "blob", user_token, email_hash="fp-bart")
os.environ["ANALYTICS_INTERNAL_IDS"] = analytics.distinct_id("fp-bart")
check("the flag is on for Daash's own accounts", matching.enabled(user_token))
store.save_user("o***@example.com", "blob", "tok-other-0123456789", email_hash="fp-other")
check("and off for everyone else", not matching.enabled("tok-other-0123456789"))
analytics.KEY = "test-key"
analytics._fetch_flags = lambda who: {"flags": {"match-v2": {"enabled": who == analytics.distinct_id("fp-flagged")}}}
store.save_user("f***@example.com", "blob", "tok-flagged-0123456789", email_hash="fp-flagged")
check("the PostHog flag match-v2 turns it on for one account", matching.enabled("tok-flagged-0123456789")
      and not matching.enabled("tok-other-0123456789"))
os.environ["GARMIN_MCP_MATCH_V2"] = "off"
check("GARMIN_MCP_MATCH_V2=off turns it off for everyone", not matching.enabled(user_token))
os.environ["GARMIN_MCP_MATCH_V2"] = "flag"

# Nov 27: matching goes to everyone; taking sessions off the watch stays with
# Bart (its own flag) until it has been seen to be right.
check("the watch: on for Daash's own accounts", matching.watch_enabled(user_token))
check("but not for an account with only match-v2: matching alone never writes to the watch",
      matching.enabled("tok-flagged-0123456789") and not matching.watch_enabled("tok-flagged-0123456789"))
analytics._fetch_flags = lambda who: {"flags": {
    "match-v2": {"enabled": who == analytics.distinct_id("fp-flagged")},
    "match-v2-watch": {"enabled": who == analytics.distinct_id("fp-flagged")}}}
analytics._FLAGS.clear() if hasattr(analytics, "_FLAGS") else None
check("the PostHog flag match-v2-watch turns the watch on for one account",
      matching.watch_enabled("tok-flagged-0123456789"))
os.environ["GARMIN_MCP_MATCH_V2_WATCH"] = "off"
check("GARMIN_MCP_MATCH_V2_WATCH=off turns the watch off for everyone, matching stays",
      not matching.watch_enabled(user_token) and matching.enabled(user_token))
os.environ["GARMIN_MCP_MATCH_V2_WATCH"] = "flag"
analytics._fetch_flags = lambda who: {"flags": {"match-v2": {"enabled": who == analytics.distinct_id("fp-flagged")}}}
analytics._FLAGS.clear() if hasattr(analytics, "_FLAGS") else None
synced: list = []
real_sync = matching.sync_watch


async def _no_sync(*a, **k):
    synced.append(a)
    return 0
matching.sync_watch = _no_sync
try:
    asyncio.run(server._after_matching({"user_token": "tok-flagged-0123456789"}, {"weeks": []}, [], THU))
    check("a plan read for someone without the watch flag never calls the watch sync", synced == [], str(synced))
    asyncio.run(server._after_matching({"user_token": user_token}, {"weeks": []}, [], THU))
    check("and one for Bart does", len(synced) == 1, str(synced))
finally:
    matching.sync_watch = real_sync

calls: list[tuple] = []


class Client:
    def unschedule_workout(self, sid):
        calls.append(("unschedule", sid))

    def schedule_workout(self, wid, day):
        calls.append(("schedule", wid, day))
        return {"workoutScheduleId": 999}


async def call(fn):
    return fn(Client())


def read(today, cal):
    ctx = matching.context(user_token)
    all_items = list(cal) + matching.off_watch_items(ctx)
    res = plan(all_items, [run(5, THU, 9.0)], today, metres={42: 9000}, matching_ctx=ctx)
    asyncio.run(matching.sync_watch(ctx, res, all_items, today, call))
    matching.record(ctx, res)
    return res


calendar = [item(402, FRI, "Easy 9 km", 42)]
result = read(THU, calendar)
check("a forward pull takes tomorrow's session off the watch", calls == [("unschedule", 402)], str(calls))
calendar = []  # Garmin's calendar no longer has it
result = read(THU, calendar)
check("the plan still shows it, done early, off the watch", row(result, 402)["status"] == "done, moved"
      and row(result, 402).get("off_watch") is True and calls == [("unschedule", 402)], str(calls))
matching.undo(user_token, 5, auto_schedule="402")
result = read(THU, calendar)
check("Undo puts it back on the watch on its own day", calls[-1] == ("schedule", 42, FRI.isoformat()), str(calls))
check("and the run counts for nothing, as before the match",
      any(x.get("matched") == "undone" for x in extras(result)) and row(result, 402)["status"] == "ahead",
      str(row(result, 402)["status"]))
calendar = [item(403, FRI, "Easy 9 km", 42)]  # back on Garmin's calendar, with a new id
result = read(THU, calendar)
check("next read: Friday's run is ahead again, nothing more written",
      row(result, 403)["status"] == "ahead" and len(calls) == 2, str(calls))
matching.undo(user_token, 5)
result = read(THU, calendar)
check("Undo again: back to matching it automatically", row(result, 403)["status"] == "done, moved"
      and calls[-1] == ("unschedule", 403), str(calls))

print("\nevery decision recorded once, with its reason")
sent.clear()
state = matching.load(user_token)
state["logged"] = []
matching.save(user_token, state)
ctx = matching.context(user_token)
res = plan(items, [run(1, THU, 7.8)], THU, metres=metres, matching_ctx=ctx)
matching.record(ctx, res)
matching.record(ctx, res)
got = [p for e, p in sent if e == "activity_matched"]
check("activity_matched once, with reason, completion, ratio and moved days",
      len(got) == 1 and got[0]["reason"] == "same_day" and got[0]["completion"] == "partial"
      and got[0]["ratio"] == 0.65 and got[0]["moved_days"] == 0, str(got))
check("line_shown says no line for a same-day partial nobody has decided about",
      got and got[0].get("line_shown") == "none", str(got))

print("\nafter a partial: the rest of it")
week = [item(701, THU, "Easy 12 km", 71), item(702, SAT, "Easy 6 km", 72), item(703, SUN, "Long run 20 km", 73)]
result = plan(week, [run(8, THU, 7.8)], THU, metres={71: 12000, 72: 6000, 73: 20000})
offer = row(result, 701).get("followup_offer")
check("offered onto Sunday's long run, a tenth of it at most (+2 km, not +4.2)",
      offer and offer["schedule_id"] == 703 and offer["add_km"] == 2.0, str(offer))
view = app._match_view(row(result, 701), THU)
check("in words", view["followup"]["text"] == "You did 7.8 of 12 km."
      and view["followup"]["add"] == "Add 2 km to Sun 29 long run 20 km", str(view.get("followup")))
week_hard = [item(711, THU, "Easy 12 km", 81), item(712, SAT, "Tempo 8 km", 82), item(713, SUN, "Easy 10 km", 83)]
result = plan(week_hard, [run(9, THU, 7.8)], THU, metres={81: 12000, 82: 8000, 83: 10000},
              efforts={82: "threshold"})
check("never onto a hard session, nor the day after one", not row(result, 711).get("followup_offer"),
      str(row(result, 711).get("followup_offer")))
result = plan(week, [run(8, THU, 7.8)], THU, metres={71: 12000, 72: 6000, 73: 20000},
              matching_ctx={"choices": {}, "followups": {"701": {"choice": "leave"}}})
check("once answered, it isn't asked again", not app._match_view(row(result, 701), THU).get("followup"))

workout = {"workoutId": 73, "workoutName": "Long run 20 km", "sportType": {"sportTypeKey": "running"},
           "workoutSegments": [{"workoutSteps": [
               {"type": "ExecutableStepDTO", "stepType": {"stepTypeKey": "interval"},
                "endCondition": {"conditionTypeKey": "distance"}, "endConditionValue": 20000}]}]}
made = feeling.longer_run(workout, 2.0, 20.0, app.LONGER_MARK)
check("Add the rest makes Sunday's run 22 km, named so, marked for Undo",
      made["workoutSegments"][0]["workoutSteps"][0]["endConditionValue"] == 22000
      and made["workoutName"] == "Long run 22 km" and app.LONGER_RE.search(made["description"]), str(made.get("workoutName")))

print("\nthe app's buttons")
import json as _json  # noqa: E402

from starlette.requests import Request  # noqa: E402

from garmin_mcp import official  # noqa: E402

official.current = lambda: user_token
state = matching.load(user_token)
state.update({"choices": {}, "history": {}, "pulled": {}, "followups": {}})
matching.save(user_token, state)


async def get_plan():
    ctx = matching.context(user_token)
    return plan(items, [run(1, THU, 7.8)], THU, metres=metres, matching_ctx=ctx)


app.server_mod.get_plan = get_plan
app.date = type("D", (date,), {"today": staticmethod(lambda: THU)})


def post(handler, path, body):
    raw = _json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    req = Request({"type": "http", "method": "POST", "path": "/app/api/" + path, "headers": [], "query_string": b""},
                  receive)
    res = asyncio.run(handler(req))
    return res.status_code, _json.loads(res.body)


sent.clear()
code, body = post(app.api_match, "match", {"action": "session", "activity_id": "1", "schedule_id": "102"})
check("Count as another session: Friday's run club", code == 200 and body["message"] == "Counted as Fri 27 9 km run club."
      and matching.context(user_token)["choices"] == {"1": "102"}, f"{code} {body}")
check("match_corrected says from what to what", ("match_corrected", {"account": "b***@example.com",
      "from_reason": "same_day", "action": "other_session"}) in sent, str(sent))
code, body = post(app.api_match, "match", {"action": "undo", "activity_id": "1"})
check("Undo: back to how it was counted before", code == 200 and matching.context(user_token)["choices"] == {},
      f"{code} {body} {matching.context(user_token)['choices']}")
code, body = post(app.api_partial, "partial", {"choice": "leave", "schedule_id": "101"})
check("Leave it, after a partial", code == 200 and body["message"] == "Left as it is."
      and any(e == "partial_followup_chosen" and p["choice"] == "leave" for e, p in sent), f"{code} {body}")
check("bad input is refused", post(app.api_match, "match", {"action": "delete", "activity_id": "1"})[0] == 400)
official.current = lambda: "tok-other-0123456789"
check("and nobody without the flag can reach it", post(app.api_match, "match",
                                                         {"action": "undo", "activity_id": "1"})[0] == 404)

print("\nNov 27: the line only when it adds something")
from garmin_mcp import matching as _m  # noqa: E402


def week_of(result, today):
    weeks = {w["starts"]: w for w in result.get("weeks") or [] if w.get("starts")}
    return app._week_view(result, weeks, None, MON, today)


def card(view, sid):
    return next((r for d in view["days"] for r in d["sessions"] if str(r.get("schedule_id")) == str(sid)), None)


def day_of_card(view, sid):
    return next((d["date"] for d in view["days"] for r in d["sessions"] if str(r.get("schedule_id")) == str(sid)), None)


two = [item(901, THU, "Easy 12 km", 91), item(902, THU, "Easy 5 km", 92)]
res = plan(two, [run(21, THU, 11.8)], THU, metres={91: 12000, 92: 5000})
v = app._match_view(row(res, 901), THU)
check("chosen between two sessions that day: says so", v.get("match_line") == "Best fit of 2 sessions"
      and v.get("line_reason") == "ambiguous", str(v))

res = plan(items, [run(1, THU, 7.8)], THU, metres=metres, matching_ctx={"choices": {"1": "102"}})
v = app._match_view(row(res, 102), FRI)
check("re-matched by hand to Friday's run club: the decision, not the match",
      v.get("match_line") == "Moved from Fri 27 · your call" and v.get("line_reason") == "user_decision", str(v))
w = week_of(res, FRI)
check("and it sits on Thursday, the day it was run", day_of_card(w, 102) == THU.isoformat(), str(day_of_card(w, 102)))

res = plan(items4b, [run(5, THU, 9.0)], THU, metres={42: 9000})
w = week_of(res, FRI)
check("a session done a day early shows on the day it was run, saying where from",
      day_of_card(w, 402) == THU.isoformat() and card(w, 402).get("match_line") == "Moved from Fri 27",
      str((day_of_card(w, 402), card(w, 402))))

for choice, line in (("leave", "Rest left as is"), ("skip", "Rest skipped")):
    res = plan(week, [run(8, THU, 7.8)], THU, metres={71: 12000, 72: 6000, 73: 20000},
               matching_ctx={"choices": {}, "followups": {"701": {"choice": choice}}})
    v = app._match_view(row(res, 701), THU)
    check(f"after a partial, {choice}: {line}", v.get("match_line") == line and v.get("line_reason") == "user_decision"
          and v.get("decided") == choice and not v.get("followup"), str(v))
res = plan(week, [run(8, THU, 7.8)], THU, metres={71: 12000, 72: 6000, 73: 20000},
           matching_ctx={"choices": {}, "followups": {"701": {"choice": "add", "onto": "703", "add_km": 2.0}}})
w = week_of(res, FRI)
check("+2 km added to Sun 29 long run", card(w, 701).get("match_line") == "+2 km added to Sun 29 long run",
      str(card(w, 701)))
check("and Sunday's run says where it came from: +2 km from Thu", card(w, 703).get("from_note") == "+2 km from Thu",
      str(card(w, 703)))

res = plan(items, [run(1, THU, 7.8)], THU, metres=metres)
w = week_of(res, FRI)
check("the Thu 26 card the next morning: no 'yesterday's', no line, 7.8 of 12 km",
      card(w, 101).get("match_line") is None and card(w, 101).get("of_line") == "7.8 of 12 km"
      and "yesterday" not in json.dumps(w), str(card(w, 101)))

for km, tone in ((12, None), (13.7, "neutral"), (13.9, "amber"), (10.3, "neutral"), (10.1, "amber"), (7.8, "amber")):
    res = plan(items, [run(1, THU, km)], THU, metres=metres)
    got_tone = app._match_view(row(res, 101), THU).get("out_tone")
    want = "neutral" if tone is None else tone
    check(f"{km} km of 12: {want}", got_tone == want, str(got_tone))

extra_auto = app._extra_row({"date": THU.isoformat(), "name": "Shakeout", "actual_km": 3.0, "activity_id": 31,
                             "matched": "no_session"}, THU)
extra_user = app._extra_row({"date": THU.isoformat(), "name": "Shakeout", "actual_km": 3.0, "activity_id": 31,
                             "matched": "user_extra"}, THU)
check("an extra run the app made extra says so", extra_auto.get("match_line") == "No session fit, counted as extra"
      and extra_auto.get("line_reason") == "extra", str(extra_auto))
check("one the person marked extra says it was their call", extra_user.get("line_reason") == "user_decision"
      and "your call" in extra_user.get("match_line", ""), str(extra_user))

sent.clear()
state = matching.load(user_token)
state["logged"] = []
matching.save(user_token, state)
ctx = {**matching.context(user_token), "followups": {"101": {"choice": "leave"}}}
res = plan(items, [run(1, THU, 7.8)], THU, metres=metres, matching_ctx=ctx)
matching.record(ctx, res)
got = [p for e, p in sent if e == "activity_matched"]
check("activity_matched carries line_shown: user_decision once they've decided", got
      and got[-1].get("line_shown") == "user_decision", str(got))

print("\nChange reopens a decision")
state = matching.load(user_token)
state["followups"] = {"101": {"choice": "skip"}}
matching.save(user_token, state)
official.current = lambda: user_token
sent.clear()
code, body = post(app.api_partial, "partial", {"choice": "reopen", "schedule_id": "101"})
check("reopen forgets the decision and says so", code == 200 and "101" not in matching.load(user_token)["followups"]
      and any(e == "partial_followup_reopened" and p.get("was") == "skip" for e, p in sent), f"{code} {body}")
code, body = post(app.api_partial, "partial", {"choice": "reopen", "schedule_id": "101"})
check("reopening twice is a clear no, not a crash", code == 404, f"{code} {body}")
undone = []


async def fake_undo(steps):
    undone.append(steps)
    return 1


real_undo = app.run_undo
app.run_undo = fake_undo
state = matching.load(user_token)
state["followups"] = {"101": {"choice": "add", "onto": "103", "add_km": 2.0,
                              "undo": [{"remove": 555, "put": {"workout_id": 73, "date": SUN.isoformat()}}]}}
matching.save(user_token, state)
code, body = post(app.api_partial, "partial", {"choice": "reopen", "schedule_id": "101"})
app.run_undo = real_undo
check("reopening an add takes the added km back off first", code == 200 and undone and undone[0][0]["remove"] == 555
      and body.get("watch_said"), f"{code} {body} {undone}")

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
