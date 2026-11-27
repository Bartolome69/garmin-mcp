"""The app as something installed and come back to (Nov 26, PostHog flag pwa-v2).

    python tests/pwa_test.py

Notifications: three kinds, each switched on its own; 7am for someone new;
one a day besides how a run counted; nothing 9pm-7am; the week summed up on
Sunday evening, or with Monday's heads-up when Sunday already had one; each
opening on its own day. Plus the manifest, the app's events, and chat links.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = tempfile.mkdtemp()
os.environ["GARMIN_MCP_DB"] = str(Path(tmp) / "pwa-test.sqlite3")
os.environ["GARMIN_MCP_BASE_URL"] = "https://daash.test"
from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()

from garmin_mcp import store  # noqa: E402

store.DB_PATH = Path(os.environ["GARMIN_MCP_DB"])
from garmin_mcp import analytics, app, hosted, push  # noqa: E402

failures: list[str] = []
events: list[tuple[str, dict]] = []
analytics.capture = lambda event, subject=None, properties=None, **_: events.append((event, dict(properties or {})))


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


LONDON = ZoneInfo("Europe/London")


def at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=LONDON).timestamp()


print("the manifest")
manifest = json.loads((Path(app.APP_DIR) / "manifest.webmanifest").read_text())
check("opens with ?source=pwa, standalone, scoped to the app",
      manifest["start_url"] == "/app/?source=pwa" and manifest["display"] == "standalone" and manifest["scope"] == "/app/")
icons = {(i["sizes"], i.get("purpose", "any")) for i in manifest["icons"]}
check("192 and 512 icons, and maskable ones", {("192x192", "any"), ("512x512", "any"), ("192x192", "maskable"),
                                               ("512x512", "maskable")} <= icons, str(icons))
check("every icon it names is there",
      all((Path(app.APP_DIR) / i["src"].replace("/app/", "")).is_file() for i in manifest["icons"]))
check("its id stays /app/, so phones that have it keep it", manifest["id"] == "/app/")

print("\nwhat someone wants")
store.save_user("b***@example.com", "blob", "tok-bart-0123456789", email_hash="fp-bart")
token = "tok-bart-0123456789"
store.save_push(token, "https://push.example/1", "p", "a", "Europe/London", "7")
check("a first phone under pwa-v2 hears at 7am", store.heads_up_for(token) == "7")
check("all three kinds on to start", store.push_kinds_for(token) == {"today": True, "plan": True, "weekly": True})
store.set_push_kind(token, "weekly", False)
store.save_push(token, "https://push.example/2", "p", "a", "Europe/London", "evening")
check("a second phone takes the first's time and choices", store.heads_up_for(token) == "7"
      and all(store.kinds_from(s["kinds"])["weekly"] is False for s in store.push_subs()))
store.set_push_kind(token, "weekly", True)
store.drop_push("https://push.example/2")
store.save_user("o***@example.com", "blob", "tok-old-0123456789", email_hash="fp-old")
store.save_push("tok-old-0123456789", "https://push.example/old", "p", "a", "Europe/London")
check("someone without the flag keeps the evening before",
      store.heads_up_for("tok-old-0123456789") == store.DEFAULT_HEADS_UP == "evening")
store.drop_push("https://push.example/old")

print("\nthe day's notifications")
owner = store.push_owner(token)
sent: list[dict] = []
statuses = {"next": ("sent", 201)}
push.send_status = lambda sub, message, urgency="normal": (sent.append(message), statuses["next"])[1]
hosted.pwa_on = lambda who: who == owner
hosted.store.token_for_owner = lambda o: token if o == owner else None

PLAN = {"credit": "Garmin Forerunner 265", "weeks": [
    {"starts": "2026-11-23", "run_km": 48.2, "planned_km": 57, "sessions": [
        {"date": "2026-11-26", "name": "Easy 12 km", "status": "done", "completion": "partial", "actual_km": 7.8,
         "activity_id": 77, "match": {"activity_id": 77, "planned_km": 12, "actual_km": 7.8, "reason": "same_day"}},
        {"date": "2026-11-27", "name": "9 km run club", "status": "ahead", "type": "easy", "planned_km": 9}]},
    {"starts": "2026-11-30", "run_km": 0, "planned_km": 60, "sessions": [
        {"date": "2026-12-01", "name": "Tempo 8 km", "type": "threshold", "planned_km": 8},
        {"date": "2026-12-06", "name": "Long run 22 km", "type": "long", "planned_km": 22}]}]}
CARDS: dict[str, dict] = {}


def card_for(day):
    return CARDS.get(day)


def as_person(user_token, fn, *args):
    if fn is hosted.server_mod.get_plan:
        return PLAN
    if fn is hosted.app_routes.heads_up_payload:
        return card_for(args[0])
    raise AssertionError(fn)


hosted._as_person = as_person
hosted._check_run = lambda *a, **k: 0


def run_pass(when: str):
    sent.clear()
    return hosted.notify_once(now=at(when), spacing=0)


CARDS["2026-11-26"] = {"date": "2026-11-26", "name": "Easy 12 km", "stats": [{"v": "12", "u": "km"}, {"v": "120–145", "u": "bpm"}]}
run_pass("2026-11-26T06:30")
check("not before 7am", not sent)
run_pass("2026-11-26T07:05")
check("7am: today's session, with its figures", len(sent) == 1 and sent[0]["title"] == "Today: Easy 12 km"
      and "12 km" in sent[0]["body"], str(sent))
check("and it opens on that day", sent and sent[0]["url"] == "/app/#/session/2026-11-26?from=push_today", str(sent))
check("push_sent says its type", ("push_sent", {"kind": "morning", "result": "sent", "type": "today"}) in events)
run_pass("2026-11-26T08:00")
check("once a day: not again an hour later", not sent)
store.mark_push(owner, morning="", daily="")
store.set_push_kind(token, "today", False)
run_pass("2026-11-26T07:10")
check("switched off: nothing", not sent)
store.set_push_kind(token, "today", True)
store.mark_push(owner, morning="", daily="")

print("\nhow a run counted")
message = hosted.plan_change_message(PLAN, 77, datetime(2026, 11, 26).date())
check("Counted your 7.8 km as today's Easy 12 km", message["title"] == "Counted your 7.8 km as today's Easy 12 km",
      message["title"])
check("says how much, offers the rest, credits Garmin, opens on the week",
      message["body"].startswith("7.8 of 12 km.") and "From your Garmin Forerunner 265" in message["body"]
      and message["url"] == "/app/#/plan?week=2026-11-23&focus=2026-11-26&from=push_plan", str(message))
check("a run the plan didn't count gets nothing from here (its verdict goes instead)",
      hosted.plan_change_message(PLAN, 999, datetime(2026, 11, 26).date()) is None)
# Nov 27: "off your watch" only when matching really took it off (match-v2-watch).
early = {"credit": "Garmin", "weeks": [{"starts": "2026-11-23", "sessions": [
    {"date": "2026-11-27", "name": "Easy 9 km", "status": "done, moved", "done_on": "2026-11-26", "activity_id": 88,
     "actual_km": 9.0, "completion": "done"}]}]}
kept = hosted.plan_change_message(early, 88, datetime(2026, 11, 26).date())
check("done a day early, still on the watch: says so, never 'off your watch'",
      "still on your watch for Friday" in kept["body"] and "off your watch" not in kept["body"], kept["body"])
early["weeks"][0]["sessions"][0]["off_watch"] = True
taken = hosted.plan_change_message(early, 88, datetime(2026, 11, 26).date())
check("and taken off it (match-v2-watch): 'off your watch for Friday'",
      "off your watch for Friday" in taken["body"], taken["body"])
check("quiet from 9pm to 7am", hosted.quiet_now(datetime(2026, 11, 26, 21, 0)) and hosted.quiet_now(datetime(2026, 11, 26, 6, 59))
      and not hosted.quiet_now(datetime(2026, 11, 26, 7, 0)) and not hosted.quiet_now(datetime(2026, 11, 26, 20, 59)))
checked: list = []
hosted._check_run = lambda *a, **k: checked.append(a[2].hour) or 0
run_pass("2026-11-26T22:30")
run_pass("2026-11-26T12:00")
check("runs are only looked at outside quiet hours", checked == [12], str(checked))
hosted._check_run = lambda *a, **k: 0

print("\nthe week, summed up")
store.mark_push(owner, morning="2026-11-29", daily="", weekly="")
run_pass("2026-11-29T18:05")
check("Sunday 6pm, nothing sent that day: the week", len(sent) == 1 and sent[0]["title"] == "Your week: 48 of 57 km"
      and sent[0]["body"] == "Next week's key session: Tuesday, Tempo 8 km.", str(sent))
check("opening on next week", sent and sent[0]["url"] == "/app/#/plan?week=2026-11-30&from=push_weekly", str(sent))
run_pass("2026-11-29T19:00")
check("and only once", not sent)
store.mark_push(owner, morning="", daily="", weekly="")
CARDS["2026-11-29"] = {"date": "2026-11-29", "name": "Easy 8 km", "stats": []}
run_pass("2026-11-29T07:05")
run_pass("2026-11-29T18:05")
check("a Sunday that had its 7am heads-up: no second one that evening", not sent, str(sent))
CARDS["2026-11-30"] = {"date": "2026-11-30", "name": "Rest and mobility", "stats": []}
run_pass("2026-11-30T07:05")
check("it comes with Monday's heads-up instead", len(sent) == 1 and sent[0]["body"].startswith("Last week: 48 of 57 km."),
      str(sent))
run_pass("2026-11-30T09:00")
check("and not again alone", not sent)
store.mark_push(owner, morning="", daily="", weekly="")
store.set_heads_up(token, "evening")
run_pass("2026-11-29T18:05")
check("told the evening before? Sunday's summary waits for the 8pm heads-up", not sent, str(sent))
run_pass("2026-11-29T20:05")
check("and goes with it, as this week", len(sent) == 1 and sent[0]["title"] == "Tomorrow: Rest and mobility"
      and sent[0]["body"].startswith("This week: 48 of 57 km."), str(sent))
store.set_heads_up(token, "7")
store.set_push_kind(token, "weekly", False)
store.mark_push(owner, morning="", daily="", weekly="")
run_pass("2026-11-29T18:05")
check("weekly switched off: no summary", not sent)
store.set_push_kind(token, "weekly", True)

print("\nwhen a phone doesn't take it")
events.clear()
statuses["next"] = ("failed", 500)
store.mark_push(owner, morning="", daily="", weekly="2026-11-23")
run_pass("2026-11-26T07:05")
check("push_failed carries the push service's status and the type",
      any(e == "push_failed" and p.get("status") == 500 and p.get("type") == "today" for e, p in events), str(events[-3:]))
statuses["next"] = ("sent", 201)

print("\nthe app's own events")
check("each install and notification event is known, by name",
      {"pwa_install_prompt_shown", "pwa_install_prompt_result", "pwa_installed", "app_opened",
       "push_permission_prompt_shown", "push_permission_result", "push_clicked"} <= set(app.PWA_EVENTS))
check("app_opened's sources", set(app.PWA_EVENTS["app_opened"]["source"]) >= {"pwa", "chat", "push", "web"})

import asyncio  # noqa: E402

from starlette.requests import Request  # noqa: E402

from garmin_mcp import official  # noqa: E402

official.current = lambda: token


def post_event(body):
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    req = Request({"type": "http", "method": "POST", "path": "/app/api/event", "headers": [], "query_string": b""}, receive)
    res = asyncio.run(app.api_event(req))
    return res.status_code


events.clear()
check("app_opened goes through with its source and whether it's the installed app",
      post_event({"name": "app_opened", "properties": {"source": "chat", "standalone": False, "tool": "create_workout"}}) == 200
      and ("app_opened", {"account": "b***@example.com", "source": "chat", "standalone": False, "tool": "create_workout"})
      in events, str(events))
events.clear()
post_event({"name": "pwa_install_prompt_shown", "properties": {"platform": "ios", "trigger": "plan_views", "note": "x"}})
check("words outside the known ones are dropped", events and "note" not in events[0][1]
      and events[0][1]["trigger"] == "plan_views", str(events))
events.clear()
post_event({"name": "push_permission_result", "properties": {"result": "maybe"}})
check("an unknown result isn't sent as one", events and "result" not in events[0][1], str(events))

print("\nchat links")
hosted.pwa_on = lambda who: who == "fp-bart"
hosted._CHAT_LINKED.clear()
check("one a day under the flag", hosted._chat_link_due("fp-bart") and not hosted._chat_link_due("fp-bart"))
check("none without it", not hosted._chat_link_due("fp-old"))
from garmin_mcp import server as server_mod  # noqa: E402

server_mod.app_link_on = lambda: True
server_mod.official.current = lambda: token
url = server_mod.app_link("plan", "chat", week="2026-11-23", focus="2026-11-26", tool="create_workout")
check("a link names its tool, signs in, and opens on the week", url.startswith("https://daash.test/app/#/plan?login=")
      and url.endswith("&week=2026-11-23&focus=2026-11-26&source=chat&tool=create_workout"), url)
hosted._CHAT_LINKED.clear()
user = store.get_user(token)
out = hosted._with_chat_link("create_workout", {"scheduled": {"scheduled_for": "2026-11-27"}}, user, token)
check("a workout sent to the watch: one short link to its week, and the note to give it once",
      "week=2026-11-23" in out["app"]["url"] and "focus=2026-11-27" in out["app"]["url"]
      and out["app"]["note"] == server_mod.CHAT_LINK_NOTE, str(out))
again = hosted._with_chat_link("get_progress", {"weeks": []}, user, token)
check("not twice the same day", "app" not in again)
check("an answer that already has the app keeps its own", hosted._with_chat_link(
    "create_workout", {"app": {"url": "x"}}, user, token)["app"] == {"url": "x"})

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
