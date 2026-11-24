"""The companion app's API and analysis, against the demo Garmin account.

The things worth proving: the app and Claude read one plan (an edit made in
the app is what get_plan answers next), an easier session changes only its
own day, the analysis numbers add up, and nobody reaches the API without a
token.

    .venv/bin/python tests/app_test.py
"""

from __future__ import annotations

import json
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
from garmin_mcp import analysis, analytics, crews, oauth, server, store, training_plan  # noqa: E402

SENT: list[dict] = []
analytics._post = SENT.append
# No flags asked of PostHog from the tests: every flag reads as its control.
analytics._fetch_flags = lambda who: {}


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


def not_100_checks(client, auth, day, claude_reads, original, today) -> None:
    """Nov 21: Not feeling 100%, worded for the session being looked at. Each choice says what it
    will do, does it on Garmin (so the watch), says what reached the watch, and one Undo puts the week back."""
    from datetime import date as d_, timedelta as td_
    from urllib.parse import unquote
    norm = lambda n: str(n).replace("\u00d7", "x")
    week_days = [(d_.fromisoformat(day) + td_(days=i)).isoformat() for i in range(7)]
    sunday, yesterday = week_days[6], (d_.fromisoformat(day) - td_(days=1)).isoformat()
    medical = re.compile(r"\b(ill|illness|sick|injur\w*|pain|doctor|medical|recover\w*|rest up)\b", re.I)

    def words_of(sheet):
        out = [sheet.get("title", ""), sheet.get("sub", "")]
        for o in sheet.get("options") or []:
            out += [o.get("label", ""), o.get("said") or o.get("unavailable") or ""]
            for s in o.get("spans") or []:
                out += [s.get("said", ""), s.get("back") or "", s.get("note") or ""] + list(s.get("stays") or [])
        return [w for w in out if w]

    check("Today's next session offers Not feeling 100%",
          (today.get("next") or {}).get("not_100") is not None, str((today.get("next") or {}).get("not_100")))
    shown = client.get(f"/app/api/session/{day}", headers=auth).json()
    check("and so does its session screen", shown.get("not_100") is not None, str(shown.get("not_100")))
    check("not standing out on a day readiness isn't low", not (shown.get("not_100") or {}).get("prominent"))

    # -- today's session -------------------------------------------------------
    sheet = client.get(f"/app/api/not100/{day}?from=session", headers=auth)
    check("the sheet answers for today's session", sheet.status_code == 200, sheet.text[:200])
    sheet = sheet.json()
    by = {o["choice"]: o for o in sheet.get("options") or []}
    check("a hard session offers four choices, make it easy, lighter run, swap and skip, then days off and a chat",
          list(by) == ["easy", "lighter", "swap", "skip", "days_off", "ask"], str(list(by)))
    check("there's no Easier pace any more", "pace" not in by and not any(o.get("sub") for o in sheet["options"]))
    check("Lighter run keeps the pace and takes a fifth off the distance",
          by["lighter"]["label"] == "Lighter run"
          and re.fullmatch(r"Today's 13\.8 km becomes 11(\.\d)? km, at the same pace\.", by["lighter"].get("said", "")),
          by["lighter"].get("said"))
    check("Make it easy turns the hard session into an easy run of the same time",
          re.fullmatch(r"Today's 3 \u00d7 3 km becomes an easy \d+ min run at 5:15\u20135:40/km\.", by["easy"].get("said", "")),
          by["easy"].get("said"))
    check("Swap names the later day by its weekday and the easy run coming back",
          by["swap"]["label"] == "Swap with Tuesday"
          and by["swap"].get("said") == "Tuesday's easy run 8 km comes to today, and the 3 \u00d7 3 km moves to Tuesday.",
          str(by["swap"]))
    check("Skip this run says the week's new total",
          by["skip"]["label"] == "Skip this run"
          and re.fullmatch(r"Today's 13\.8 km comes off\. This week: \d+ km \u2192 \d+ km\.", by["skip"].get("said", "")),
          by["skip"].get("said"))
    spans = {s["days"]: s for s in by["days_off"]["spans"]}
    three = spans.get(3) or {}
    check("a few days off lists what comes off, each with its day",
          [(x["day"], norm(x["name"])) for x in three.get("off") or []]
          == [("Mon", "3 x 3 km"), ("Tue", "Easy run 8 km"), ("Wed", "Full body (runner)"), ("Wed", "Easy run 6 km")]
          or [x["day"] for x in three.get("off") or []] == ["Mon", "Tue", "Wed", "Wed"], str(three.get("off")))
    check("and the week's new total and the first run back",
          (three.get("totals") or [{}])[0].get("label") == "This week"
          and three["totals"][0]["to"] < three["totals"][0]["from"]
          and three.get("back") == "Thursday's easy run 8 km becomes a lighter run.", str(three)[:300])
    ask = by["ask"]
    said = unquote(ask["ask"]["claude"].split("?q=")[1])
    check("Talk it through opens a chat with the week and the next race, and nothing more",
          ask["label"] == "Talk it through with Claude" and said.startswith("I'm not feeling 100% today. Here's my week: Mon 3 \u00d7 3 km (planned)")
          and "(done)" not in said.split("Mon")[0] and "priority" in said and said.endswith("Can you adjust my plan?")
          and "@" not in said, said[:300])
    words = words_of(sheet)
    check("every line follows the copy rules, and none is medical",
          all(plain(w) for w in words) and not any(medical.search(w) for w in words),
          str([w for w in words if not plain(w) or medical.search(w)]))

    # An A race 13 days out: days off now fall in its taper, so each says so gently and is still allowed.
    real_goal = app_routes._goal_race
    app_routes._goal_race = lambda plan: {"name": "Valencia Marathon", "tier": "goal",
                                          "date": (d_.fromisoformat(day) + td_(days=13)).isoformat()}
    try:
        tapered = client.get(f"/app/api/not100/{day}", headers=auth).json()
    finally:
        app_routes._goal_race = real_goal
    taper_spans = next(o for o in tapered["options"] if o["choice"] == "days_off")["spans"]
    check("days off in an A race's taper carry a gentle note, and stay allowed",
          all(s.get("note") == "This touches your Valencia Marathon taper; consider asking Claude." and s.get("off")
              for s in taper_spans), str([s.get("note") for s in taper_spans]))
    check("without one, there's no note", not any(s.get("note") for s in spans.values()))

    # -- a later session: worded for its own day ----------------------------------
    later = client.get(f"/app/api/not100/{sunday}?from=session", headers=auth)
    check("the sheet answers for a later session", later.status_code == 200, later.text[:200])
    later = {o["choice"]: o for o in later.json().get("options") or []}
    check("its lines name that session's day, never today",
          later["lighter"].get("said", "").startswith("Sunday's ") and later["skip"].get("said", "").startswith("Sunday's ")
          and not any(re.search(r"\b(today|tomorrow)\b", o.get("said") or "", re.I) for o in later.values()),
          str({k: v.get("said") for k, v in later.items()}))
    check("a long run on Sunday has no later day to swap with", "unavailable" in later.get("swap", {}), str(later.get("swap")))
    check("and isn't offered Make it easy, which is for hard sessions: lighter run, swap and skip",
          list(later) == ["lighter", "swap", "skip", "days_off", "ask"], str(list(later)))

    # -- an easy run: lighter run, swap onto a free day, skip ----------------------
    tue = week_days[1]
    easy_sheet = client.get(f"/app/api/not100/{tue}?from=session", headers=auth)
    check("the sheet answers for an easy run", easy_sheet.status_code == 200, easy_sheet.text[:200])
    easy_by = {o["choice"]: o for o in easy_sheet.json().get("options") or []}
    check("an easy run offers three: lighter run, swap and skip, then days off and a chat",
          list(easy_by) == ["lighter", "swap", "skip", "days_off", "ask"], str(list(easy_by)))
    check("its swap moves it to a free day later in the week",
          re.fullmatch(r"Swap with (Friday|Saturday)", easy_by["swap"]["label"] or "")
          and re.fullmatch(r"The easy run 8 km moves to (Friday|Saturday), and Tuesday becomes a rest day\.",
                           easy_by["swap"].get("said", "")), str(easy_by["swap"]))
    future = client.get(f"/app/api/session/{sunday}", headers=auth).json()
    check("a later session's screen offers the sheet", future.get("not_100") is not None, str(future.get("not_100")))

    # -- a past session: no sheet ---------------------------------------------------
    past = client.get(f"/app/api/session/{yesterday}", headers=auth).json()
    check("a past session's screen doesn't offer the sheet", "not_100" not in past, str(past.get("not_100")))
    gone = client.get(f"/app/api/not100/{yesterday}", headers=auth)
    check("and the sheet refuses a past session", gone.status_code == 409, gone.text[:120])
    check("a missed session in the past isn't offered either",
          app_routes.not_100_offer(yesterday, "easy", "missed", d_.fromisoformat(day), None) is None)

    def apply(**body):
        r = client.post(f"/app/api/not100/{day}", headers=auth, json=body)
        return r.status_code, r.json()

    def undo(result):
        r = client.post("/app/api/not100-undo", headers=auth, json={"undo": result.get("undo")})
        return r.status_code == 200 and r.json().get("watch_said") == app_routes.WATCH_UPDATED

    def week_now():  # a day's sessions in any order: Undo puts them back, not in Garmin's order
        return {d: sorted(norm(n) for n in claude_reads(d)) for d in week_days}

    week_before = week_now()
    first_hard = int(shown["workout_id"])

    planned_before = client.get("/app/api/plan", headers=auth).json()["week"].get("planned")
    code, light = apply(choice="lighter")
    check("Lighter run answers, and says it's on the watch",
          code == 200 and light.get("watch_said") == "Updated on your watch. Arrives at next sync."
          and light.get("watch") == [{"date": day, "name": light["watch"][0]["name"], "status": "updated"}]
          and plain(light.get("message", "")), str(light)[:240])
    check("Claude reads the shorter session, its name in step",
          any(re.match(r"3 x 2\.4 km", norm(n)) for n in claude_reads(day)), str(claude_reads(day)))
    lighter_screen = client.get(f"/app/api/session/{day}", headers=auth).json()
    check("its total distance and time drop on the session screen",
          lighter_screen.get("km") and lighter_screen["km"] < shown["km"] and abs(lighter_screen["km"] - shown["km"] * 0.8) < 0.3
          and lighter_screen.get("minutes") and lighter_screen["minutes"] < shown["minutes"],
          f"{shown.get('km')} km {shown.get('minutes')} min -> {lighter_screen.get('km')} km {lighter_screen.get('minutes')} min")
    planned_after = client.get("/app/api/plan", headers=auth).json()["week"].get("planned")
    check("and the week's planned total drops with it", planned_before and planned_after and planned_after < planned_before,
          f"{planned_before} -> {planned_after}")
    copy = DEMO.get_workout_by_id(int(lighter_screen["workout_id"]))
    check("and Garmin's own time and distance for it, which its app and the watch show, are the shorter ones",
          copy.get("estimatedDurationInSecs") and abs(copy["estimatedDurationInSecs"] / 60 - lighter_screen["minutes"]) <= 1
          and copy.get("estimatedDistanceInMeters") and abs(copy["estimatedDistanceInMeters"] / 1000 - lighter_screen["km"]) < 0.15,
          f"{copy.get('estimatedDurationInSecs')} s, {copy.get('estimatedDistanceInMeters')} m")
    check("Undo puts it back", undo(light) and week_now() == week_before, str(week_now()))

    # A session already changed offers its original back, first, and the other changes wait for it.
    code, light = apply(choice="lighter")
    changed = {o["choice"]: o for o in client.get(f"/app/api/not100/{day}", headers=auth).json().get("options") or []}
    check("a changed session offers Back to the original first, and the others wait",
          list(changed)[0] == "original" and changed["original"].get("said") == "Today's 3 × 3 km goes back as it was."
          and all("unavailable" in changed[c] for c in ("lighter", "easy")), str(changed)[:300])
    code, back = apply(choice="original")
    check("Back to the original puts it back, updated on the watch",
          code == 200 and week_now() == week_before and back.get("watch_said") == app_routes.WATCH_UPDATED, str(back)[:200])

    code, easy = apply(choice="easy")
    check("Make it easy answers", code == 200, str(easy)[:200])
    check("Claude reads an easy run of the same time in its place",
          any(re.match(r"Easy run \d+ min", n) for n in claude_reads(day)), str(claude_reads(day)))
    check("Undo puts the hard session back", undo(easy) and week_now() == week_before, str(week_now()))

    code, swap = apply(choice="swap")
    check("swap answers, both sessions updated on the watch",
          code == 200 and sorted(w["date"] for w in swap.get("watch") or []) == sorted([day, tue])
          and all(w["status"] == "updated" for w in swap["watch"]), str(swap)[:240])
    check("the hard session is on the later day, the easy run today",
          [norm(n) for n in claude_reads(tue)] == [norm(n) for n in original] and "Easy run 8 km" in claude_reads(day),
          f"{claude_reads(day)} / {claude_reads(tue)}")
    check("Undo swaps them back", undo(swap) and week_now() == week_before, str(week_now()))

    r = client.post(f"/app/api/not100/{tue}", headers=auth, json={"choice": "swap"})
    moved = r.json()
    to = (moved.get("watch") or [{}])[0].get("date")
    check("an easy run's swap moves it to the free day, Tuesday now a rest day",
          r.status_code == 200 and claude_reads(tue) == [] and to in week_days[4:6] and "Easy run 8 km" in claude_reads(to)
          and re.fullmatch(r"The easy run 8 km moved to (Friday|Saturday), and Tuesday is a rest day\.", moved.get("message", "")),
          str(moved)[:240])
    check("Undo brings it back to Tuesday", undo(moved) and week_now() == week_before, str(week_now()))

    code, skip = apply(choice="skip")
    check("Skip this run takes it off, removed from the watch",
          code == 200 and claude_reads(day) == [] and skip.get("watch_said") == "Removed from your watch."
          and [w["status"] for w in skip.get("watch") or []] == ["removed"], str(skip)[:200])
    check("Undo puts it back", undo(skip) and week_now() == week_before)

    code, off = apply(choice="days_off", days=3)
    check("three days off take off what's in them",
          code == 200 and all(claude_reads(d) == [] for d in week_days[:3]), str([claude_reads(d) for d in week_days[:3]]))
    check("and the first run back is a lighter run",
          any(re.match(r"Easy run 6\.4 km", n) for n in claude_reads(week_days[3])), str(claude_reads(week_days[3])))
    check("the watch hears of each: removed, and the one updated",
          sorted(w["status"] for w in off.get("watch") or []) == ["removed"] * 4 + ["updated"]
          and off.get("watch_said") == app_routes.WATCH_UPDATED, str(off.get("watch")))
    check("one Undo puts the whole week back", undo(off) and week_now() == week_before, str(week_now()))

    # Garmin refuses a write: the watch isn't said to have it, nothing changed, and trying again works.
    real = DEMO.schedule_workout
    DEMO.schedule_workout = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Garmin said no"))
    try:
        r = client.post(f"/app/api/not100/{day}", headers=auth, json={"choice": "swap"})
    finally:
        DEMO.schedule_workout = real
    failed = r.json()
    check("a change Garmin refuses says the watch wasn't updated, so the app can offer Retry",
          r.status_code == 502 and failed.get("error") == "Couldn't update your watch." and failed.get("watch_failed") is True
          and "watch_said" not in failed, r.text[:200])
    check("and leaves the week as it was", week_now() == week_before, str(week_now()))
    code, again = apply(choice="swap", retry=True)
    check("Retry then goes through", code == 200 and again.get("watch_said") == app_routes.WATCH_UPDATED, str(again)[:200])
    retried = [e["properties"] for e in SENT if e["event"] == "app_edit" and e["properties"].get("choice") == "swap"]
    check("the failure and the retry are both counted",
          any(e.get("watch_failed") for e in retried) and any(e.get("retry") and e.get("ok") for e in retried), str(retried[-2:]))
    check("Undo after the retry puts the week back", undo(again) and week_now() == week_before)

    code, odd = apply(choice="loud")
    check("a choice it doesn't know changes nothing", code == 400 and week_now() == week_before, str(odd))
    bad = client.post("/app/api/not100-undo", headers=auth, json={"undo": [{"put": {"workout_id": "x", "date": "no"}}]})
    check("an undo that isn't one is refused", bad.status_code == 400)
    mine = int(client.get(f"/app/api/session/{week_days[1]}", headers=auth).json()["workout_id"])
    client.post("/app/api/not100-undo", headers=auth, json={"undo": [{"delete": mine}]})
    check("and Undo never deletes a workout Daash didn't copy", mine in DEMO.uploaded)
    check("the week is as it started", week_now() == week_before, str(week_now()))


def feeling_rule_checks() -> None:
    """Nov 21: the week's rules for Not feeling 100%, on made-up weeks, and its session changes."""
    from garmin_mcp import feeling

    mon = date(2026, 11, 16)

    def week(*days):
        out = []
        for i, kinds in enumerate(days):
            for k in kinds.split("+") if kinds else []:
                out.append({"date": (mon + timedelta(days=i)).isoformat(), "type": k, "kind": "run" if k != "strength" else "strength",
                            "status": "ahead", "name": k})
        return out

    def swap(sessions, i=0):
        chosen = next(s for s in sessions if s["date"] == (mon + timedelta(days=i)).isoformat() and s["type"] != "strength")
        found = feeling.swap_day(sessions, chosen)
        return None if not found else ((found[0] - mon).days, (found[1] or {}).get("type"))

    check("a hard day swaps with the first easy day that keeps hard days apart",
          swap(week("threshold", "easy", "easy", "intervals", "easy", "", "long")) == (1, "easy"))
    check("never into the day before another hard session",
          swap(week("threshold", "easy", "intervals", "easy", "easy", "", "long")) == (4, "easy"),
          str(swap(week("threshold", "easy", "intervals", "easy", "easy", "", "long"))))
    check("never next to the long run", swap(week("threshold", "easy", "easy", "easy", "", "easy", "long")) == (1, "easy"))
    check("the long run never lands next to a hard session",
          swap(week("", "", "", "long", "easy", "threshold", ""), 3) is None,
          str(swap(week("", "", "", "long", "easy", "threshold", ""), 3)))
    check("a day with nothing on it is used when no easy day works",
          swap(week("threshold", "", "easy", "intervals", "", "", "")) == (1, None),
          str(swap(week("threshold", "", "easy", "intervals", "", "", ""))))
    check("a day's strength session stays put; the easy run beside it swaps",
          swap(week("threshold", "easy+strength", "", "", "", "", "")) == (1, "easy"))
    check("nothing hard in the two days after a race",
          swap(week("race", "", "threshold", "easy", "easy", "", ""), 2) == (3, "easy")
          and swap(week("", "race", "threshold", "", "", "", ""), 2) == (4, None),
          f"{swap(week('race', '', 'threshold', 'easy', 'easy', '', ''), 2)} {swap(week('', 'race', 'threshold', '', '', '', ''), 2)}")
    check("never past Sunday: a Sunday session has no later day", swap(week("", "", "", "", "", "", "long"), 6) is None)
    check("an easy run moves to a free later day, never onto another easy run",
          swap(week("easy", "easy", "", "", "", "", "")) == (2, None), str(swap(week("easy", "easy", "", "", "", "", ""))))
    check("never onto a day with a hard session or the long run",
          swap(week("easy", "threshold", "long", "", "", "", "")) == (3, None),
          str(swap(week("easy", "threshold", "long", "", "", "", ""))))
    check("with no free later day, it has nowhere to go", swap(week("easy", "easy", "easy", "easy", "easy", "easy", "long")) is None)
    done = week("threshold", "easy", "", "", "", "", "")
    done[1]["status"] = "done"
    check("a day already run is never one", swap(done) != (1, "easy"), str(swap(done)))

    off, back, races = feeling.days_off(week("threshold", "easy", "race", "easy", "easy", "", ""), mon, 3)
    check("days off take off what's in them, never a race",
          [s["type"] for s in off] == ["threshold", "easy"] and [r["type"] for r in races] == ["race"])
    check("and the first run after them comes back lighter", back and back["date"] == (mon + timedelta(days=3)).isoformat())

    check("a session's day is today, else its weekday, never tomorrow",
          feeling.days(mon, mon) == "Today's" and feeling.days(mon + timedelta(days=1), mon) == "Tuesday's"
          and feeling.day_word(mon + timedelta(days=6), mon) == "Sunday")

    marathon = {"name": "Valencia Marathon", "date": "2026-12-06", "tier": "goal"}
    check("days off in the three weeks before a marathon touch its taper",
          feeling.taper_touched(marathon, date(2026, 11, 17), date(2026, 11, 19)) == "taper")
    check("days off in its race week touch race week",
          feeling.taper_touched(marathon, date(2026, 11, 30), date(2026, 12, 1)) == "race week")
    check("days off before the taper, or a B race's, touch nothing",
          feeling.taper_touched(marathon, date(2026, 11, 9), date(2026, 11, 13)) is None
          and feeling.taper_touched(dict(marathon, tier="key"), date(2026, 11, 17), date(2026, 11, 19)) is None)
    check("a 10k's taper is its race week alone",
          feeling.taper_touched({"name": "Brighton 10k", "date": "2026-11-22", "tier": "goal"}, mon - timedelta(days=3), mon - timedelta(days=1)) is None)

    said = feeling.talk_it_through(
        [{"date": "2026-11-16", "name": "3 x 3 km", "status": "missed", "type": "threshold"},
         {"date": "2026-11-15", "name": "Long run 16 km", "status": "done_late", "type": "long"},
         {"date": "2026-11-18", "name": "Easy run 6 km", "status": "ahead", "type": "easy"}], marathon, lambda n: n)
    check("the chat's message says each session's state and the next race with its priority",
          said == "I'm not feeling 100% today. Here's my week: Sun Long run 16 km (done); Mon 3 x 3 km (missed); "
                  "Wed Easy run 6 km (planned), next race Valencia Marathon, 6 Dec 2026, priority A. Can you adjust my plan?", said)

    def run(meters, pace=None, hr=None, kind="interval"):
        step = {"stepType": {"stepTypeKey": kind}, "endCondition": {"conditionTypeKey": "distance"}, "endConditionValue": meters,
                "targetType": {"workoutTargetTypeKey": "pace.zone" if pace else "heart.rate.zone" if hr else "no.target"}}
        if pace:
            step["targetValueOne"], step["targetValueTwo"] = 1000 / pace[1], 1000 / pace[0]
        if hr:
            step["targetValueOne"], step["targetValueTwo"] = hr
        return step

    long = {"workoutId": 9, "workoutName": "Long run 12 km · MAR", "workoutSegments": [{"workoutSteps": [run(12000, pace=(330, 360))]}]}
    made = feeling.lighter_run(long, "Made easier in Daash from workout")
    check("Lighter run takes 12 km to 9.6 km at the same pace, its name and plan code in step",
          made["workoutSegments"][0]["workoutSteps"][0]["endConditionValue"] == 9600
          and made["workoutName"].startswith("Long run 9.6 km") and training_plan.label_of(made["workoutName"]) == "MAR"
          and made["workoutSegments"][0]["workoutSteps"][0]["targetValueOne"] == long["workoutSegments"][0]["workoutSteps"][0]["targetValueOne"]
          and "Made easier in Daash from workout 9." in made["description"], str(made.get("workoutName")))
    easy = feeling.make_it_easy({"workoutId": 8, "workoutName": "3 x 3 km · MAR"}, 63, ["5:15", "5:40"], "Made easier in Daash from workout")
    check("Make it easy is an easy run of the same time, keeping the plan's code",
          easy["workoutName"].startswith("Easy run 63 min") and training_plan.label_of(easy["workoutName"]) == "MAR"
          and easy["workoutSegments"][0]["workoutSteps"][0]["endConditionValue"] == 3780
          and "Made easier in Daash from workout 8." in easy["description"], str(easy.get("workoutName")))


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
    # A path outside the app gets the app's own page (served with its build since Nov 15).
    check("a path can't climb out", all(b"app.js?v=" in app_routes._file(bad).body
                                        for bad in ("../store.py", "../../../etc/passwd", "fonts/../../app.py")))
    check("the app's callback is a recognised redirect", oauth.recognised_redirect("http://localhost/app/callback"))

    today = client.get("/app/api/today", headers=auth).json()
    # Pulled to refresh (Nov 13): the server's memory of earlier reads is
    # dropped for that request, so Garmin's latest answers it; a plain open
    # keeps using what is remembered.
    held = hosted.session_for(user_token)
    held.remember(("calendar", 1999, 1), "kept")
    client.get("/app/api/today", headers=auth)
    check("an ordinary open keeps what the server remembers", held.knows(("calendar", 1999, 1), 3600))
    pulled = client.get("/app/api/today", headers={**auth, "X-Daash-Fresh": "1"})
    check("a pull to refresh asks Garmin afresh, and still answers",
          not held.knows(("calendar", 1999, 1), 3600) and pulled.status_code == 200 and pulled.json().get("headline"))
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

    not_100_checks(client, auth, day, claude_reads, original, today)

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
    client.get("/app/api/today", headers={**auth, "X-Daash-From": "push_run"})
    opened = [e["properties"] for e in posted("push_opened")]
    check("a tapped notification counts as one push opened, with its kind",
          len(opened) == 1 and opened[0].get("kind") == "run" and opened[0].get("screen") == "today", str(opened))
    check("and only a notification's tap: a chat link isn't one",
          not any(o.get("kind") == "offer" for o in opened), str(opened))
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
    # Nov 13: an edit made in the app writes the session to the calendar
    # again, so it counts as a workout sent to the watch, by source.
    sends = posted("workout_sent_to_watch")
    check("app edits are counted as sent to the watch, by source, once ever as the first",
          {e["properties"].get("source") for e in sends} >= {"app_easier", "app_move"}
          and sum(1 for e in sends if e["properties"].get("first_ever")) <= 1
          and all(e["properties"].get("login_route") in ("password", "official") for e in sends),
          str([e["properties"] for e in sends])[:240])
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
    # Nov 11: the session as planned with the run inside it, and what it shows.
    check("it draws the reps against their plan", (run.get("chart") or {}).get("mode") == "steps"
          and sum(1 for p in run["chart"]["parts"] if p["k"] == "work") == 3, str(run.get("chart"))[:160])
    # Nov 14: no lines that restate the table; with no earlier session like it,
    # there's nothing to compare, so nothing is said.
    check("and says nothing the table already shows", not run.get("insights")
          and "figures" not in run, str(run.get("insights"))[:160])

    # Nov 22: a run that went on past its plan leads with what was run. A session of
    # reps keeps its name ("3 x 3 km" says more), with the plan line under it.
    real_analyse = app_routes.analysis.analyse

    def longer(*a, **k):
        out = real_analyse(*a, **k)
        out["extra"] = {"planned_km": 12.0, "ran_km": 14.2, "extra_km": 2.2}
        return out

    app_routes.analysis.analyse = longer
    try:
        reps_run = client.get(f"/app/api/run/{done['activityId']}", headers=auth).json()
        easy_done = next((a for a in DEMO.demo_activities.values() if re.match(r"Easy run", a.get("activityName") or "")), None)
        easy_run = client.get(f"/app/api/run/{easy_done['activityId']}", headers=auth).json() if easy_done else {}
    finally:
        app_routes.analysis.analyse = real_analyse
    check("a session of reps run longer keeps its name, with the plan line under it",
          reps_run.get("name") == run.get("name") and reps_run.get("planned_line") == "Planned 12 km \u00b7 ran 2.2 km extra"
          and "extra" not in reps_run, str({k: reps_run.get(k) for k in ("name", "planned_line")}))
    check("an easy run run longer is titled by what was run",
          easy_run.get("name") == "14.2 km easy" and bool(easy_run.get("planned_line")),
          str({k: easy_run.get(k) for k in ("name", "planned_line", "type", "error")}))
    check("a run that kept to its plan has no plan line", "planned_line" not in run)

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

    # Nov 15: after a morning ride the last device synced was the bike
    # computer, and Today credited the running plan to it. The watch Garmin
    # takes training status from is the one named.
    real_last, real_status = DEMO.get_device_last_used, DEMO.get_training_status
    DEMO.get_device_last_used = lambda: {"lastUsedDeviceName": "Edge 840"}
    DEMO.get_training_status = lambda day: {"mostRecentTrainingStatus": {"latestTrainingStatusData": {
        "3456789": {"deviceId": 3456789, "primaryTrainingDevice": True}}}}
    try:
        credited = client.get("/app/api/today", headers={**auth, "X-Daash-Fresh": "1"}).json()
        DEMO.get_training_status = lambda day: {}
        fallback = client.get("/app/api/today", headers={**auth, "X-Daash-Fresh": "1"}).json()
    finally:
        DEMO.get_device_last_used, DEMO.get_training_status = real_last, real_status
    check("Today names the training watch, not the bike computer that synced last",
          "Forerunner 265" in str(credited.get("credit")) and "Edge" not in str(credited.get("credit")),
          str(credited.get("credit")))
    check("and with no training watch named, the last device synced",
          "Edge 840" in str(fallback.get("credit")), str(fallback.get("credit")))

    # Nov 15: a ride opened as a run, with pace, "Km by km" and an easy-running
    # verdict. A ride reads by speed, power, climbing and heart rate.
    ride = client.get("/app/api/run/2222", headers=auth).json()
    labels = [x["l"] for x in ride.get("stats") or []]
    check("a ride reads as a ride: kicker, speed in km/h, no pace",
          ride.get("ride") is True and ride.get("kicker", "").endswith("RIDE") and "km/h avg" in labels
          and not any("/km" in x for x in labels) and "/km" not in json.dumps(ride.get("splits") or []),
          f"{ride.get('kicker')} {labels}")
    check("its verdict is in cycling terms, and no running verdicts are drawn",
          "km/h" in (ride.get("verdict") or "") and not ride.get("insights") and not ride.get("easy_check")
          and not ride.get("planned_and_run"), str(ride.get("verdict")))
    check("its laps are speed bars and a speed column",
          (ride.get("chart") or {}).get("unit") == "kmh" and (ride.get("chart") or {}).get("bar_label") == "Speed"
          and ride.get("split_cols") == ["KM", "SPEED", "HR"]
          and all(r["pace"].endswith("km/h") for r in ride.get("splits") or []), str(ride.get("splits"))[:160])
    check("and it says rides don't count toward running km",
          "Rides don't count" in ((ride.get("effect") or {}).get("sub") or ""), str(ride.get("effect")))
    page = client.get("/app")
    check("the page loads its script and styles by build, and every answer names the live build",
          f'app.js?v={app_routes.APP_BUILD}' in page.text and f'app.css?v={app_routes.APP_BUILD}' in page.text
          and client.get("/app/api/today", headers=auth).headers.get("x-daash-build") == app_routes.APP_BUILD,
          page.text[page.text.find("app.js") - 20:page.text.find("app.js") + 40])
    # Nov 15: the script and styles compressed, and kept on the phone for good
    # under this build's address only; the chat connection never compressed.
    build = app_routes.APP_BUILD
    script = client.get(f"/app/app.js?v={build}", headers={"Accept-Encoding": "gzip"})
    styles = client.get(f"/app/app.css?v={build}", headers={"Accept-Encoding": "gzip"})
    check("the script and styles come compressed, kept for good under this build",
          script.headers.get("content-encoding") == "gzip" and styles.headers.get("content-encoding") == "gzip"
          and "immutable" in script.headers.get("cache-control", "") and "immutable" in styles.headers.get("cache-control", "")
          and "import" in script.text, f"{dict(script.headers)}")
    stale = client.get("/app/app.js?v=000000000000")
    bare = client.get("/app/app.js")
    check("an older build's address, or none, isn't kept: it gets today's file",
          stale.headers.get("cache-control") == "no-cache" and bare.headers.get("cache-control") == "no-cache", str(stale.headers))
    check("the page itself is asked for each time", client.get("/app").headers.get("cache-control") == "no-cache")
    check("fonts are kept a week", "max-age=604800" in client.get("/app/fonts/Geist.woff2").headers.get("cache-control", ""))
    big = client.get("/app/api/plan", headers={**auth, "Accept-Encoding": "gzip"})
    check("the app's answers come compressed too", big.headers.get("content-encoding") == "gzip" and big.json(),
          str(big.headers))
    mcp_answer = client.post("/mcp", headers={"Accept-Encoding": "gzip"}, json={})
    check("the chat connection isn't compressed", mcp_answer.headers.get("content-encoding") != "gzip", str(mcp_answer.headers))
    # How long an open waited for its first screen: numbers and known words only.
    before = len(posted("app_ready"))
    ok = client.post("/app/api/event", headers=auth, json={"name": "ready", "properties": {
        "ms": 1840.4, "screen": "plan", "saved": False, "shell": "network", "worker": True, "net": "4g", "source": "card"}})
    junk = client.post("/app/api/event", headers=auth, json={"name": "ready", "properties": {"ms": "soon", "screen": "plan"}})
    odd = client.post("/app/api/event", headers=auth, json={"name": "ready", "properties": {
        "ms": 900, "screen": "today", "shell": "<b>", "net": "fiber", "source": "a b"}})
    readies = posted("app_ready")[before:]
    check("app_ready says how long, which screen, and why",
          ok.status_code == 200 and junk.status_code == 400 and len(readies) == 2
          and {k: readies[0]["properties"].get(k) for k in ("ms", "seconds", "screen", "saved", "shell", "worker", "net", "source")}
          == {"ms": 1840, "seconds": 1.84, "screen": "plan", "saved": False, "shell": "network", "worker": True, "net": "4g",
              "source": "card"}
          and not {"shell", "net", "source"} & set(readies[1]["properties"]), str(readies))
    run_again = client.get(f"/app/api/run/{done['activityId']}", headers=auth).json()
    check("a run still reads as a run", not run_again.get("ride") and "RIDE" not in run_again.get("kicker", ""))

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
    # Nov 15: each magic link's fate, against the person it was for, and the
    # app's sign-in counted as magic_link with where the link came from.
    fresh = store.new_handoff(user_token)
    good = client.post("/app/api/sign-in/handoff", json={"code": fresh, "source": "card"})
    reused = client.post("/app/api/sign-in/handoff", json={"code": fresh, "source": "card"})
    bogus = client.post("/app/api/sign-in/handoff", json={"code": "not-a-code"})
    spare = store.new_handoff(user_token)
    already = client.post("/app/api/sign-in/handoff", json={"code": spare, "source": "ai_mention", "signed_in": True})
    check("a spent link and a made-up one say which, so the app can say so",
          good.status_code == 200 and reused.json().get("result") == "used" and "already been used" in reused.json().get("error", "")
          and bogus.json().get("result") == "invalid", f"{reused.text} {bogus.text}")
    check("opened in an app already signed in, the link is spent and nothing else changes",
          already.status_code == 200 and already.json() == {"ok": True} and store.redeem_handoff(spare)[1] == "used", already.text)
    opened = [(e["properties"].get("result"), e["properties"].get("source"), e["distinct_id"]) for e in posted("app_magic_link_opened")]
    me = analytics.distinct_id(store.get_user(user_token).email_hash or user_token)
    check("app_magic_link_opened says what happened to each, against the person where known",
          ("success", "card", me) in opened and ("used", "card", me) in opened and ("expired", None, me) in opened
          and ("already_signed_in", "ai_mention", me) in opened
          and any(r == "invalid" and d != me for r, _s, d in opened), str(opened))
    # Nov 16: from the weekly email, which link it was and that it came from
    # the email, on the link's fate, the sign-in and the first screen; a link
    # opened hours later says so in the email's words.
    mailed = store.new_handoff(user_token)
    by_mail = client.post("/app/api/sign-in/handoff", json={"code": mailed, "source": "weekly_email", "link": "day_row"})
    stale = store.new_handoff(user_token, lifetime=-1)
    late = client.post("/app/api/sign-in/handoff", json={"code": stale, "source": "weekly_email", "link": "primary_button"})
    gone = client.post("/app/api/sign-in/handoff", json={"code": "deleted-after-a-day", "source": "weekly_email"})
    from_mail = [e["properties"] for e in posted("app_magic_link_opened") if e["properties"].get("source") == "weekly_email"]
    check("from the email: app_magic_link_opened carries link and utm_source=weekly_email",
          by_mail.status_code == 200 and any(p.get("result") == "success" and p.get("link") == "day_row"
                                             and p.get("utm_source") == "weekly_email" for p in from_mail)
          and any(p.get("result") == "expired" and p.get("link") == "primary_button" for p in from_mail), str(from_mail))
    check("an email link opened late, expired or long gone, says it in the email's words",
          "link from your email has expired" in late.json().get("error", "")
          and "link from your email has expired" in gone.json().get("error", ""), f"{late.text} {gone.text}")
    check("and the sign-in from the email says which link",
          any(e["properties"].get("method") == "magic_link" and e["properties"].get("link") == "day_row"
              and e["properties"].get("utm_source") == "weekly_email" for e in posted("app_signed_in")))
    mail_auth = {"Authorization": f"Bearer {by_mail.json().get('access_token')}"}
    client.get("/app/api/plan", headers={**mail_auth, "X-Daash-From": "weekly_email", "X-Daash-Link": "day_row"})
    viewed = [e["properties"] for e in posted("app_viewed") if e["properties"].get("source") == "weekly_email"]
    check("the first screen opened from the email says which link and utm_source",
          viewed and viewed[-1].get("link") == "day_row" and viewed[-1].get("utm_source") == "weekly_email"
          and "email" not in viewed[-1], str(viewed[-1:]))
    check("a link given in a chat signs in as app_signed_in, method deep_link, with its source",
          any(e["properties"].get("method") == "deep_link" and e["properties"].get("source") == "card"
              for e in posted("app_signed_in")), str(posted("app_signed_in")))
    # Nov 23: the link on the app card after a workout push. 15 minutes, once,
    # then the app's own sign-in (email code or Garmin), on the screen it was opening.
    import time as _time
    real_time = _time.time
    t0 = real_time()
    in_time, too_late = store.new_handoff(user_token), store.new_handoff(user_token)
    try:
        _time.time = lambda: t0 + 14 * 60 + 50
        at_14 = client.post("/app/api/sign-in/handoff", json={"code": in_time, "source": "first_push"})
        _time.time = lambda: t0 + 15 * 60 + 10
        at_15 = client.post("/app/api/sign-in/handoff", json={"code": too_late, "source": "first_push"})
    finally:
        _time.time = real_time
    check("the app card's link signs in within its 15 minutes", at_14.status_code == 200 and at_14.json().get("access_token"),
          at_14.text[:120])
    check("and not after them: it says the chat link expired, for the app to fall back to its own sign-in",
          at_15.status_code == 401 and at_15.json().get("result") == "expired" and "15 minutes" in at_15.json().get("error", ""),
          at_15.text[:160])
    twice = client.post("/app/api/sign-in/handoff", json={"code": in_time, "source": "first_push"})
    check("tapped again, it's used: no second sign-in from it",
          twice.status_code == 401 and twice.json().get("result") == "used" and "access_token" not in twice.json(), twice.text[:120])
    check("its sign-in counted as deep_link from the card on a push",
          any(e["properties"].get("method") == "deep_link" and e["properties"].get("source") == "first_push"
              for e in posted("app_signed_in")))
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
    check("signing in is recorded, by how: deep link (a chat), magic link (an email), email code",
          methods >= {"deep_link", "magic_link", "email"}, str(methods))
    failed = {(e["properties"].get("method"), e["properties"].get("reason")) for e in posted("app_sign_in_failed")}
    check("and failed sign-ins, with why", ("email", "wrong code") in failed and ("deep_link", "used") in failed and ("deep_link", "expired") in failed
          and ("magic_link", "expired") in failed,
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
    gone_events = [e["properties"] for e in posted("connector_disconnected")]
    check("and counted as a disconnect, from the app, of everything",
          any(g.get("via") == "app" and g.get("what") == "everything" and g.get("login_route") for g in gone_events),
          str(gone_events))
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

    # Nov 15: a ride done today, not on the plan, was on the Plan tab and
    # nowhere on Today.
    import asyncio as _asyncio
    today_iso = _d.today().isoformat()
    ridden = {"weeks": [{"starts": day, "sessions": [], "extra": [
        {"date": today_iso, "name": "Tower Hamlets Road Cycling", "sport": "ride", "kind": "ride",
         "actual_km": 37.9, "actual": "1:21:24", "activity_id": 2222}]}]}
    real_read = app_routes.server_mod._read

    async def fake_read(key, ttl, fn):
        return DEMO.get_activity(2222)

    app_routes.server_mod._read = fake_read
    try:
        shown = _asyncio.run(app_routes._done_today(ridden, _d.today()))
    finally:
        app_routes.server_mod._read = real_read
    check("a ride done today shows on Today, by distance and speed, opening the ride",
          shown and shown.get("activity_id") == 2222 and "37.9 km" in shown.get("line", "")
          and "km/h" in shown.get("line", "") and "/km" not in shown.get("line", ""), str(shown))
    ride_row = app_routes._extra_row(ridden["weeks"][0]["extra"][0])
    check("and on the Plan tab it's called a ride", ride_row.get("type_label") == "Ride", str(ride_row))

    # Nov 15: the plan's figure is the week's target and stays put; what's done
    # can go past it. While sessions remain, where the week is heading is its
    # own note, and only when it differs from the plan.
    tally = {"starts": day, "run_km": 49.3, "planned_km": 65.6, "sessions": [
        {"date": day, "sport": "run", "status": "done", "planned_km": 45.6},
        {"date": day, "sport": "run", "status": "ahead", "planned_km": 5, "optional": True},
        {"date": day, "sport": "run", "status": "ahead", "planned_km": 20}]}
    shown = app_routes._week_view({"weeks": [tally]}, {day: tally}, None, monday, _d.today())
    check("mid-week with an extra run: 49 of 66, on course for 69",
          (shown.get("done"), shown.get("planned"), shown.get("heading")) == (49, 66, 69),
          str({k: shown.get(k) for k in ("done", "planned", "heading")}))
    behind = {**tally, "run_km": 30.0}
    shown = app_routes._week_view({"weeks": [behind]}, {day: behind}, None, monday, _d.today())
    check("a week behind its plan says so: 30 of 66, on course for 50",
          (shown.get("planned"), shown.get("heading")) == (66, 50), str((shown.get("planned"), shown.get("heading"))))
    finished = {**tally, "run_km": 71.2, "sessions": [{**x, "status": "done"} for x in tally["sessions"]]}
    shown = app_routes._week_view({"weeks": [finished]}, {day: finished}, None, monday, _d.today())
    check("a finished week with an extra run reads 71 of 66, no note",
          (shown.get("done"), shown.get("planned")) == (71, 66) and "heading" not in shown,
          str({k: shown.get(k) for k in ("done", "planned", "heading")}))
    on_plan = {**tally, "run_km": 45.6}
    shown = app_routes._week_view({"weeks": [on_plan]}, {day: on_plan}, None, monday, _d.today())
    check("heading where the plan does: no note", "heading" not in shown and shown.get("planned") == 66, str(shown.get("heading")))
    last = monday - _td(weeks=1)
    past = {**tally, "starts": last.isoformat()}
    shown = app_routes._week_view({"weeks": [past]}, {last.isoformat(): past}, None, last, _d.today())
    check("and a past week reads against its plan, no note", shown.get("planned") == 66 and "heading" not in shown,
          str(shown.get("planned")))

    # Nov 13: each row says what was planned, how it went, its target, and whether it's on the watch.
    today = _d.today()
    mon = today - _td(days=today.weekday())
    wk = {"starts": mon.isoformat(), "run_km": 18.0, "planned_km": 30.0, "sessions": [
        {"date": mon.isoformat(), "type": "threshold", "sport": "run", "status": "done", "planned_km": 10, "actual_km": 10.3,
         "actual_pace": "4:30 /km", "actual": "46m 10s", "schedule_id": 11, "workout_id": 1, "activity_id": 5},
        {"date": mon.isoformat(), "type": "strength", "sport": "strength", "status": "missed", "planned_min": 27,
         "schedule_id": 12, "workout_id": 2},
        {"date": mon.isoformat(), "type": "easy", "sport": "run", "status": "done", "planned_km": 8, "actual_km": 10,
         "schedule_id": 13, "workout_id": 3, "activity_id": 6},
        {"date": mon.isoformat(), "type": "easy", "sport": "run", "status": "done", "planned_km": 8, "actual_km": 5,
         "schedule_id": 14, "workout_id": 3, "activity_id": 7},
        {"date": (today + _td(days=1)).isoformat(), "type": "intervals", "sport": "run", "status": "ahead",
         "planned_km": 12, "schedule_id": 15, "workout_id": 4}]}
    targets = {15: {"target": "3:50–3:58 /km", "minutes": 55}}
    view = app_routes._week_view({"weeks": [wk]}, {mon.isoformat(): wk}, None, mon, today, targets)
    rows = {r["schedule_id"]: r for d in view["days"] for r in d["sessions"]}
    check("a run within 10% of its plan is on target, with planned beside done and its pace",
          rows[11].get("outcome") == "on" and rows[11].get("plan_km") == "10" and rows[11].get("done_km") == "10.3"
          and rows[11].get("done_pace") == "4:30/km", str(rows[11]))
    check("longer is over, shorter is short, and a missed session says so",
          rows[13].get("outcome") == "over" and rows[14].get("outcome") == "short" and rows[12].get("outcome") == "missed",
          str([rows[k].get("outcome") for k in (12, 13, 14)]))
    check("a session still to come carries its target, its length and that it's on the watch",
          rows[15].get("target") == "3:50–3:58 /km" and rows[15].get("plan_min") == 55 and rows[15].get("watch") is True
          and not rows[11].get("watch") and not rows[12].get("watch"), str(rows[15]))
    check("the week counts its hard sessions, and how many are done",
          view.get("hard") == {"done": 1, "planned": 2}, str(view.get("hard")))
    check("the app says Threshold for Sub-T, whoever named it",
          app_routes._display("Sub-T 4x2.5km") == "Threshold 4 × 2.5km"
          and app_routes._display("Long 18km w 3x2km sub-T") == "Long 18km w 3 × 2km threshold")
    from garmin_mcp.workouts import build_workout
    model, _, _ = build_workout("3 x 2 km", "running", [
        {"type": "warmup", "distance_meters": 2000, "hr": [120, 145]},
        {"type": "repeat", "times": 3, "steps": [{"type": "interval", "distance_meters": 2000, "pace": ["4:00", "4:08"]},
                                                 {"type": "recovery", "duration_seconds": 90}]},
        {"type": "cooldown", "distance_meters": 2000, "hr": [120, 145]}], "")
    # Nov 13 should-haves: what next week does, each phase in a line, the plan's numbers, today's rest note.
    bars = [{"n": 1, "planned": 50, "phase": "Build"}, {"n": 2, "planned": 38, "phase": "Build", "cutback": True},
            {"n": 3, "planned": 60, "phase": "Build"}, {"n": 4, "planned": 40, "phase": "Taper"},
            {"n": 5, "planned": 30, "phase": "Taper", "race": {"tier": "A", "name": "London"}}]
    check("next week: drops to recover, builds, tapers, or is race week",
          app_routes._next_week_line(bars, bars[0]) == "Next week drops to 38 km to recover."
          and app_routes._next_week_line(bars, bars[1]) == "Next week builds to 60 km."
          and app_routes._next_week_line(bars, bars[2]) == "Next week tapers to 40 km."
          and app_routes._next_week_line(bars, bars[3]) == "Next week is race week: London.",
          str([app_routes._next_week_line(bars, b) for b in bars[:4]]))
    wks = {"2026-11-02": {"sessions": [{"type": "threshold"}, {"type": "long", "planned_km": 16}]},
           "2026-11-09": {"sessions": [{"type": "threshold"}, {"type": "intervals"}, {"type": "long", "planned_km": 18}]}}
    lines = app_routes._phase_lines([{"label": "Build", "from": 1, "to": 2}],
                                    [{"n": 1, "starts": "2026-11-02", "planned": 54, "current": True},
                                     {"n": 2, "starts": "2026-11-09", "planned": 58, "current": False}], wks)
    check("a phase in a line, from its own weeks only",
          lines[0]["line"] == "54–58 km a week, two hard sessions a week, long runs 16–18 km." and lines[0]["weeks"] == "weeks 1–2",
          str(lines))
    check("today's rest note says only what Garmin has",
          app_routes._rest_note({"sleep": {"total": "7h 42m", "score": 81}, "hrv": {"status": "BALANCED"}})
          == "Slept 7:42, score 81 · HRV in range · from Garmin"
          and app_routes._rest_note({"sleep": {}, "hrv": {}}) is None,
          str(app_routes._rest_note({"sleep": {"total": "7h 42m", "score": 81}, "hrv": {"status": "BALANCED"}})))
    check("no goal race distance, no prediction to compare",
          app_routes._race_distance({"name": "Club champs"}) is None
          and app_routes._race_distance({"name": "London", "distance_km": 42.2})[0] == "timeMarathon"
          and app_routes._race_distance({"name": "Big Half"})[0] == "timeHalfMarathon")
    check("a session's target is its reps' pace, not its warm-up's heart rate",
          (app_routes._main_target(model.to_dict()) or "").endswith(" /km")
          and (app_routes._main_target(model.to_dict()) or "")[:3] in ("3:5", "4:0"), str(app_routes._main_target(model.to_dict())))


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


def build_checks() -> None:
    """Building a plan in the app: closed unless switched on, then the steps, the preview and the send."""
    import json as _json
    from datetime import date as _d, timedelta as _td

    email = "builder@example.test"
    user_token = store.save_user(session_mod.mask_email(email), '{"di_token": "x"}',
                                 email_hash=store.email_fingerprint(email), email=email)
    token = secrets.token_urlsafe(32)
    store.save_oauth_token(token, kind="access", client_id="test", subject=user_token, scopes="",
                           resource=None, expires_at=int(time.time()) + 3600)
    auth = {"Authorization": f"Bearer {token}"}
    os.environ.pop("GARMIN_MCP_PREVIEW", None)
    with TestClient(hosted.build_app(), base_url="http://localhost") as client:
        check("building a plan is hidden outside preview", client.get("/app/api/build", headers=auth).status_code == 404)
        check("and Plan doesn't offer it", "next_block" not in client.get("/app/api/plan", headers=auth).json())
        os.environ[app_routes.BUILDER_ENV] = "1"
        try:
            check("GARMIN_MCP_BUILDER opens it to everyone", client.get("/app/api/build", headers=auth).status_code == 200)
            check("and still needs a token", client.get("/app/api/build").status_code == 401)
        finally:
            os.environ.pop(app_routes.BUILDER_ENV, None)
            app_routes._BUILD_INPUTS.clear()
        os.environ["GARMIN_MCP_PREVIEW"] = "on"
        try:
            got = client.get("/app/api/build", headers=auth).json()
            check("step 1 has four weeks of running and an average", len(got.get("bars") or []) == 4 and "avg_km" in got, str(got)[:200])
            check("and how often, never a level label", got.get("level_line", "").startswith("Most weeks"), got.get("level_line"))
            check("Garmin's predictions read as race times", all(":" in p["v"] for p in got.get("predictions") or [{"v": "x"}]),
                  str(got.get("predictions")))
            starts = _d.fromisoformat(got["starts"])
            check("the plan starts on a Monday", starts.weekday() == 0, got["starts"])
            race_day = starts + _td(weeks=4, days=6)
            answers = {"avg_km": got["avg_km"], "longest_km": got.get("longest_km") or 10, "level": "regular",
                       "races": [{"id": "x", "name": "Test 10K", "date": race_day.isoformat(), "km": 10, "tier": "A"}],
                       "days": 4, "out": [], "long_day": 6, "amount": "build", "injury": "none", "style": "balanced"}
            plan = client.post("/app/api/build/plan", headers=auth, content=_json.dumps({"answers": answers, "preview": True})).json()
            pv = plan.get("preview") or {}
            check("the preview has a week a bar to race day", len(pv.get("weeks") or []) == 5, str(plan)[:300])
            check("with the race in its last week", (pv.get("weeks") or [{}])[-1].get("race") == "A")
            check("and goal times to pick from", len(plan.get("chips") or []) == 3, str(plan.get("chips")))
            hurt = client.post("/app/api/build/plan", headers=auth,
                               content=_json.dumps({"answers": {**answers, "injury": "hurts"}, "preview": True})).json()
            check("something that hurts gets no plan", "preview" not in hurt, str(hurt)[:200])
            before = len(DEMO.scheduled)
            sent = client.post("/app/api/build/send", headers=auth, content=_json.dumps({"answers": answers, "from": "plan"})).json()
            check("sending starts at once", sent.get("state") in ("sending", "done") and sent.get("total") == pv.get("count"), str(sent))
            for _ in range(100):
                status = client.get("/app/api/build/send", headers=auth).json()
                if status.get("state") != "sending":
                    break
                time.sleep(0.1)
            check("and puts every session on the calendar", status.get("state") == "done"
                  and len(DEMO.scheduled) - before == pv.get("count"), f"{status} {len(DEMO.scheduled) - before}")
            on_race = [s for s in DEMO.scheduled if s["date"] == race_day.isoformat()]
            check("with the race on race day", any("Test 10K" in str(s.get("title")) for s in on_race), str(on_race)[:200])
            built = posted("app_plan_built")[-1:]
            check("a send is counted, with where the builder was opened from",
                  built and built[0]["properties"].get("ok") is True and built[0]["properties"].get("from") == "plan"
                  and "reason" not in built[0]["properties"], str(built))
            client.post("/app/api/event", headers=auth, content=_json.dumps({"name": "build_step", "properties": {"step": "4", "from": "change", "secret": "x"}}))
            steps = posted("app_build_step")
            check("each step reached is counted, with only the fields it may carry",
                  steps and steps[-1]["properties"].get("step") == "4" and steps[-1]["properties"].get("from") == "change"
                  and "secret" not in steps[-1]["properties"], str(steps[-1:]))
            for name, props in (("build_opened", {"from": "resume", "step": "3", "resumed": "yes"}),
                                ("build_action", {"action": "blocked", "reason": "too close", "step": "2"})):
                ok = client.post("/app/api/event", headers=auth, content=_json.dumps({"name": name, "properties": props})).status_code == 200
                got_props = (posted(f"app_{name}")[-1:] or [{}])[0].get("properties", {})
                check(f"the builder's {name} event is taken as said", ok and all(got_props.get(k) == v for k, v in props.items()), str(got_props))
            from garmin_mcp.app import _fail_reason
            check("a failed send says why in a word or two, never Garmin's message",
                  [_fail_reason(e) for e in ("HTTPError: 429 Too Many Requests", "401 Unauthorized", "503 Service Unavailable",
                                             "400 Bad Request", "something odd")]
                  == ["garmin busy", "signed out", "garmin down", "garmin refused", "other"])

            # Changing the plan: the new one goes in from next Monday, the rest of this week stays.
            plan = client.get("/app/api/plan", headers=auth).json()
            check("Plan offers to change or remove the plan running now", (plan.get("manage") or {}).get("count", 0) > 0, str(plan.get("manage")))
            swap = client.get("/app/api/build?replace=1", headers=auth).json()
            monday = plan_builder_first_monday = _d.today() + _td(days=(7 - _d.today().weekday()) % 7)
            check("changing it starts next Monday", swap.get("starts") == monday.isoformat() and not swap.get("after"), str(swap.get("starts")))
            check("and says how many sessions it replaces", (swap.get("replacing") or {}).get("count", 0) > 0, str(swap.get("replacing")))
            this_week = sorted(x["date"] for x in DEMO.scheduled if _d.today().isoformat() <= x["date"] < monday.isoformat())
            new_race = monday + _td(weeks=7, days=6)
            changed = {**answers, "races": [{"id": "y", "name": "Swap Half", "date": new_race.isoformat(), "km": 21.1, "tier": "A"}]}
            pv2 = client.post("/app/api/build/plan", headers=auth,
                              content=_json.dumps({"answers": changed, "preview": True, "replace": True})).json().get("preview") or {}
            client.post("/app/api/build/send", headers=auth, content=_json.dumps({"answers": changed, "replace": True}))
            for _ in range(200):
                status = client.get("/app/api/build/send", headers=auth).json()
                if status.get("state") != "sending":
                    break
                time.sleep(0.1)
            after_monday = [x for x in DEMO.scheduled if x["date"] >= monday.isoformat() and x.get("itemType") != "event"]
            check("the old plan's sessions from Monday are gone, the new one's are there",
                  status.get("state") == "done" and len(after_monday) == pv2.get("count"), f"{status} {len(after_monday)} {pv2.get('count')}")
            check("and this week's are untouched",
                  sorted(x["date"] for x in DEMO.scheduled if _d.today().isoformat() <= x["date"] < monday.isoformat()) == this_week)
            # Removing it.
            check("removing needs a confirmation", client.post("/app/api/plan/remove", headers=auth, content="{}").status_code == 400)
            gone = client.post("/app/api/plan/remove", headers=auth, content=_json.dumps({"confirm": "remove"})).json()
            ahead = [x for x in DEMO.scheduled if x["date"] >= _d.today().isoformat() and x.get("itemType") != "event"]
            check("Remove this plan takes every session from today off", gone.get("removed", 0) > 0 and not ahead, f"{gone} {len(ahead)}")
            check("and the runs already done stay", any(x["date"] < _d.today().isoformat() for x in DEMO.scheduled))
            plan = client.get("/app/api/plan", headers=auth).json()
            # Nov 23: with nothing in the next two weeks, Plan is the Get your plan offer.
            check("then Plan offers a new plan instead", plan.get("empty") and (plan.get("get_plan") or {}).get("first") in ("chat", "app")
                  and not plan.get("manage"), str(plan)[:200])
            fresh_start = client.get("/app/api/build", headers=auth).json()
            check("which starts next Monday, not after the old race", fresh_start.get("starts") == monday.isoformat(), str(fresh_start.get("starts")))

            # New accounts: nothing on the calendar and nothing run, or connected
            # through the official API and waiting on Garmin's first runs.
            from garmin_mcp import server as server_mod
            real_get_plan, real_activities = server_mod.get_plan, DEMO.get_activities_by_date
            answers_for = {
                "nothing recorded": {"error": "Nothing is scheduled on the Garmin calendar, and nothing was recorded "
                                              "in the last 4 weeks.", "source": "none"},
                "waiting on Garmin": {"source": "waiting", "since": _d.today().isoformat()},
            }
            try:
                for case, answer in answers_for.items():
                    async def given(label=None, answer=answer):
                        return answer
                    server_mod.get_plan = given
                    plan = client.get("/app/api/plan", headers=auth).json()
                    check(f"{case}: Plan offers to build one", plan.get("empty") is True and plan.get("build") is True, str(plan)[:200])

                async def down(label=None):
                    return {"error": "Garmin didn't answer."}
                server_mod.get_plan = down
                check("Garmin not answering is still an error", client.get("/app/api/plan", headers=auth).status_code >= 400)
                server_mod.get_plan = given
                DEMO.get_activities_by_date = lambda start, end: []
                app_routes._BUILD_INPUTS.clear()
                fresh = client.get("/app/api/build", headers=auth).json()
                check("with no runs from Garmin, step 1 asks for the numbers", fresh.get("none") is True and not fresh.get("avg_km"), str(fresh)[:200])
                typed = {**answers, "avg_km": 20, "longest_km": 8, "level": "new"}
                plan = client.post("/app/api/build/plan", headers=auth, content=_json.dumps({"answers": typed, "preview": True})).json()
                check("and the plan starts from what was typed", (plan.get("preview") or {}).get("start_km", 99) <= 20, str(plan)[:200])
            finally:
                server_mod.get_plan, DEMO.get_activities_by_date = real_get_plan, real_activities
                app_routes._BUILD_INPUTS.clear()
        finally:
            os.environ.pop("GARMIN_MCP_PREVIEW", None)


def race_prompt_checks() -> None:
    """A race on the Garmin calendar: one card on Today, Not now, not a goal race, and the builder's A race."""
    import json as _json
    from datetime import date as _d, timedelta as _td
    from garmin_mcp import plan_builder

    email = "racer@example.test"
    user_token = store.save_user(session_mod.mask_email(email), '{"di_token": "x"}',
                                 email_hash=store.email_fingerprint(email), email=email)
    token = secrets.token_urlsafe(32)
    store.save_oauth_token(token, kind="access", client_id="test", subject=user_token, scopes="",
                           resource=None, expires_at=int(time.time()) + 3600)
    auth = {"Authorization": f"Bearer {token}"}
    today = _d.today()
    fit = plan_builder.Fitness(predictions={"10k": 3020, "half": 6720})
    inputs = {"avg_km": 26, "longest_km": 12, "level": "regular", "days": 4, "long_day": 6}
    starts = plan_builder.first_monday(today)
    far = {"key": "a" * 16, "name": "Cambridge Half", "date": (starts + _td(weeks=18, days=6)).isoformat(), "km": 21.1}
    made = app_routes._race_prompt(far, inputs, fit, today, starts, None)
    check("a race 6 weeks or more off, with no plan, offers a plan",
          made["case"] == "no_plan" and made["title"].startswith("Cambridge Half is in ") and made["cta"] == "Build a plan", str(made))
    check("its facts are weeks, days and the peak",
          [f["l"] for f in made["facts"]] == ["to race day", "a week, like now", "peak week"] and made["facts"][2]["v"].startswith("~"),
          str(made["facts"]))
    near = {**far, "key": "b" * 16, "name": "Bedford 10K", "km": 10, "date": (starts + _td(weeks=3, days=6)).isoformat()}
    made = app_routes._race_prompt(near, inputs, fit, today, starts, None)
    check("a closer race offers a short block, sharpen then taper",
          made["case"] == "close" and made["cta"] == "Build a 4-week block" and made["facts"][1]["v"] == "3 + 1", str(made))
    ends = starts + _td(weeks=2, days=6)
    made = app_routes._race_prompt(far, inputs, fit, today, ends + _td(days=1), ends)
    check("with a plan running, a race after it offers the next block",
          made["case"] == "after" and made["cta"] == "Plan the next block" and made["facts"][0]["l"] == "plan ends", str(made))
    soon = {**near, "date": (today + _td(days=9)).isoformat()}
    check("a race too close for any block offers nothing", app_routes._race_prompt(soon, inputs, fit, today, starts, None) is None)

    # The demo's plan runs most of the way to Garmin's horizon, so here the
    # runner has none: the card is the no-plan one.
    from garmin_mcp import server as server_mod
    real_get_plan = server_mod.get_plan

    async def no_plan(label=None):
        return {"source": "log"}

    os.environ["GARMIN_MCP_PREVIEW"] = "on"
    server_mod.get_plan = no_plan
    app_routes._BUILD_INPUTS.clear()
    try:
        with TestClient(hosted.build_app(), base_url="http://localhost") as client:
            race_day = starts + _td(weeks=9, days=6)
            DEMO.scheduled.append({"itemType": "event", "date": race_day.isoformat(), "title": "Riverside Half",
                                   "distance": 21097.5, "primaryEvent": True})
            DEMO.scheduled.append({"itemType": "event", "date": (race_day - _td(days=7)).isoformat(), "title": "Park 5K",
                                   "distance": 5000})
            prompt = client.get("/app/api/race-prompt", headers=auth).json()
            check("Today offers a plan for it", prompt.get("case") == "no_plan" and prompt.get("mode") == "card"
                  and prompt.get("cta") == "Build a plan" and prompt.get("name") == "Riverside Half", str(prompt))
            check("a 5K never asks", "Park 5K" not in str(prompt))
            check("showing it is counted once", len([e for e in posted("race_prompt_shown")]) == 1)
            client.get("/app/api/race-prompt", headers=auth)
            check("and not again on the next visit", len([e for e in posted("race_prompt_shown")]) == 1)
            key = prompt["key"]
            said = client.post("/app/api/race-prompt", headers=auth,
                               content=_json.dumps({"key": key, "action": "snooze", "case": "no_plan"})).json()
            check("Not now says when it'll ask again", "in a week" in said.get("message", ""), str(said))
            check("and folds it to a row", client.get("/app/api/race-prompt", headers=auth).json().get("mode") == "row")
            me = crews.member_key(user_token, store.email_fingerprint(email))
            with store._connect() as conn:
                conn.execute("UPDATE race_prompts SET snoozed_at = snoozed_at - ? WHERE member = ?", (8 * 86400, me))
            check("a week on, it's a card again", client.get("/app/api/race-prompt", headers=auth).json().get("mode") == "card")
            for _ in range(2):
                client.post("/app/api/race-prompt", headers=auth, content=_json.dumps({"key": key, "action": "snooze"}))
                with store._connect() as conn:
                    conn.execute("UPDATE race_prompts SET snoozed_at = snoozed_at - ? WHERE member = ?", (8 * 86400, me))
            check("after the third Not now it stays a row", client.get("/app/api/race-prompt", headers=auth).json().get("mode") == "row")
            built = client.get("/app/api/build", headers=auth).json()
            check("the builder knows the race by the same key", any(r.get("key") == key for r in built.get("races") or []))
            client.post("/app/api/race-prompt", headers=auth, content=_json.dumps({"key": key, "action": "not_goal"}))
            check("not a goal race: no card or row", client.get("/app/api/race-prompt", headers=auth).json() == {})
            built = client.get("/app/api/build", headers=auth).json()
            check("and it's a fun run in the builder", next(r for r in built["races"] if r["key"] == key)["tier"] == "C")
            check("a made-up action is refused", client.post("/app/api/race-prompt", headers=auth,
                  content=_json.dumps({"key": key, "action": "delete"})).status_code == 400)
        os.environ.pop("GARMIN_MCP_PREVIEW", None)
        with TestClient(hosted.build_app(), base_url="http://localhost") as client:
            check("outside preview there's no card", client.get("/app/api/race-prompt", headers=auth).json() == {})
    finally:
        server_mod.get_plan = real_get_plan
        os.environ.pop("GARMIN_MCP_PREVIEW", None)
        DEMO.scheduled[:] = [x for x in DEMO.scheduled if x.get("itemType") != "event"]
        app_routes._BUILD_INPUTS.clear()


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
    # Nov 24: 8 km easy + strides (7.5 km, then 6 x 20 s, each recovery to the lap
    # button) run to 10.4 km, the watch lapping every km to 9.1 and then the strides
    # as short laps, all unmarked. The plan's distance is the plan card's estimate.
    today_laps = [{"distance": d, "duration": t, "averageHR": h, "intensityType": "INTERVAL"} for d, t, h in (
        (1000, 319, 110), (1000, 313, 118), (1000, 309, 120), (1000, 309, 122), (1000, 304, 125), (1000, 304, 122),
        (1000, 299, 127), (1000, 303, 127), (1000, 305, 130), (140, 43, 117), (80, 18, 117), (140, 47, 133),
        (100, 22, 130), (90, 33, 133), (100, 23, 138), (90, 33, 145), (10, 19, 138), (210, 52, 125), (40, 29, 130),
        (110, 24, 132), (50, 38, 142), (100, 20, 124), (40, 33, 132), (120, 27, 122), (1, 1, 136))]
    import copy as copy_
    short_plan = copy_.deepcopy(workout)
    short_plan["workoutSegments"][0]["workoutSteps"][0]["endConditionValue"] = 7500
    ran_on = analysis.analyse(short_plan, {"lapDTOs": today_laps}, "easy", story=True)
    check("strides with lap-button recoveries: the planned distance is the estimate, 7.9 km",
          ran_on.get("extra") == {"planned_km": 7.9, "ran_km": 10.4, "extra_km": 2.5, "about": True}, str(ran_on.get("extra")))
    check("and it shows under the km, marked as an estimate",
          ran_on["stats"][0].get("plan") == "~7.9 planned", str(ran_on["stats"][0]))
    check("the verdict leads with the extra distance, laps that never lined up or not",
          ran_on["verdict"] == "Ran 2.5 km longer than planned. Heart rate on target for the planned 7.9 km.", ran_on["verdict"])
    check("the plan ends after km 8, the nearest km to 7.9",
          (ran_on.get("chart") or {}).get("plan_ends") == 8, str({k: (ran_on.get("chart") or {}).get(k) for k in ("plan_ends", "plan_km")}))
    timed = copy_.deepcopy(short_plan)
    timed["workoutSegments"][0]["workoutSteps"][0]["endCondition"] = {"conditionTypeKey": "time"}
    timed["workoutSegments"][0]["workoutSteps"][0]["endConditionValue"] = 2400
    check("a run set by time never gets a guessed planned distance",
          not analysis.analyse(timed, {"lapDTOs": today_laps}, "easy").get("extra"))
    # Reps long enough to be the session stay reps, row by row.
    import copy
    long_reps = copy.deepcopy(workout)
    long_reps["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][0]["endConditionValue"] = 180
    check("three-minute reps are still reps, not strides",
          not analysis._stride_steps([(s, None) for s in analysis.planned_steps(long_reps)]))


def get_plan_checks() -> None:
    """Nov 23: Get your plan. The no-plan check, the 50/50 order kept per person,
    the three steps' link, the events with their properties, and the 7-day check."""
    from datetime import date as d_, timedelta as td_
    from urllib.parse import unquote

    today = d_.today()
    on = lambda n: (today + td_(days=n)).isoformat()  # noqa: E731

    def plan_with(*sessions):
        return {"source": "plan", "weeks": [{"sessions": [dict(s) for s in sessions]}]}

    # -- the no-plan check: nothing of any sport planned today through 13 days out
    nap = app_routes.no_active_plan
    check("no plan: recent runs alone, or nothing at all", nap({"source": "log", "weeks": []}, today)
          and nap({"source": "none"}, today) and nap({"source": "waiting"}, today) and nap({"source": "plan", "weeks": []}, today))
    check("a session tomorrow, or 13 days out, is a plan",
          not nap(plan_with({"date": on(1), "status": "ahead", "type": "easy"}), today)
          and not nap(plan_with({"date": on(13), "status": "ahead", "type": "long"}), today))
    check("today's session counts, of any sport", not nap(plan_with({"date": on(0), "status": "today", "type": "strength"}), today))
    check("14 days out and beyond isn't: nothing in the next two weeks is no plan",
          nap(plan_with({"date": on(14), "status": "ahead", "type": "easy"}), today))
    check("done and missed sessions aren't a plan ahead",
          nap(plan_with({"date": on(-2), "status": "done", "type": "easy"}), today)
          and nap(plan_with({"date": on(-1), "status": "missed", "type": "easy"}), today))

    # -- the chat way's message, from the three steps
    race_day = today + td_(days=7 * 16 + (6 - today.weekday()))
    said = app_routes.plan_chat_prompt({"race": "half", "date": race_day.isoformat(), "km": 32.4, "days": [6, 0, 2, 4],
                                        "long_day": 6}, today)
    check("the message has the race, its date and weeks away, the weekly km, the days and the long run, and the ask",
          said.startswith("Use Daash to build me a training plan. My goal: a half marathon on Sunday ")
          and f"{race_day.day} " in said and "weeks away" in said
          and "I run about 32 km a week now." in said
          and "I can run on Monday, Wednesday, Friday and Sunday, with my long run on Sunday." in said
          and said.endswith("Then build the plan and send the first week to my watch."), said)
    check("no race says so", "My goal: to get fitter, with no race in mind." in app_routes.plan_chat_prompt({"race": "none"}, today))
    odd = app_routes.plan_chat_prompt({"race": "other", "other": "Trail 30k <script>", "km": 9999, "days": ["x", 9, 1],
                                       "long_day": 3}, today)
    check("odd answers are cleaned: no markup, no impossible km, no unknown days, no long day off the days",
          "<" not in odd and "Trail 30k script" in odd and "km a week" not in odd and "I can run on Tuesday." in odd, odd)

    # -- the order: drawn once per person, 50/50, then kept
    first = store.plan_choice_first("fp-order-a", "tok-a", draw=lambda: 0.2)
    again = store.plan_choice_first("fp-order-a", "tok-a", draw=lambda: 0.9)
    other = store.plan_choice_first("fp-order-b", "tok-b", draw=lambda: 0.7)
    check("a draw under a half shows the chat way first, over it the app's", first == "chat" and other == "app")
    check("and it's kept: the same person always sees the same order", again == "chat")
    drawn = [store.plan_choice_first(f"fp-many-{i}", f"tok-{i}") for i in range(2000)]
    check("over many people it's about half and half", 900 <= drawn.count("chat") <= 1100, str(drawn.count("chat")))

    # -- the screens, the pick and the events, against an account with no plan ahead
    email = "planless@example.test"
    user_token = store.save_user(session_mod.mask_email(email), '{"di_token": "x"}',
                                 email_hash=store.email_fingerprint(email), email=email)
    token = secrets.token_urlsafe(32)
    store.save_oauth_token(token, kind="access", client_id="test", subject=user_token, scopes="",
                           resource=None, expires_at=int(time.time()) + 3600)
    client = TestClient(hosted.build_app(), base_url="http://localhost")
    auth = {"Authorization": f"Bearer {token}"}
    me = analytics.distinct_id(store.email_fingerprint(email))
    real_get_plan = app_routes.server_mod.get_plan
    later_plan = plan_with({"date": on(20), "status": "ahead", "type": "easy", "name": "Easy run 8 km"})

    async def later():
        return later_plan

    app_routes.server_mod.get_plan = later
    os.environ[app_routes.BUILDER_ENV] = "1"  # on for everyone in production since 11 Nov
    try:
        today_d = client.get("/app/api/today", headers=auth).json()
        plan_d = client.get("/app/api/plan", headers=auth).json()
        asked_week = client.get(f"/app/api/plan?week={on(20)}", headers=auth).json()
    finally:
        app_routes.server_mod.get_plan = real_get_plan
    g = today_d.get("get_plan") or {}
    check("Today with nothing in the next two weeks offers Get your plan, in this person's order",
          today_d.get("empty") and g.get("first") in ("chat", "app") and g.get("build") is True, str(today_d)[:300])
    check("and says when the plan further out starts, with its week",
          (g.get("later") or {}).get("date") == on(20)
          and g.get("later", {}).get("week") == (today + td_(days=20) - td_(days=(today + td_(days=20)).weekday())).isoformat(), str(g))
    check("Plan offers it too, the same order", plan_d.get("empty") and (plan_d.get("get_plan") or {}).get("first") == g.get("first"))
    check("asking for a later week shows that week, not the offer", not asked_week.get("empty") and "get_plan" not in asked_week,
          str(asked_week)[:200])
    has = client.get("/app/api/today", headers=auth).json()
    os.environ.pop(app_routes.BUILDER_ENV, None)
    check("with sessions in the next two weeks, Today is the plan as before", not has.get("empty") and "get_plan" not in has,
          str({k: has.get(k) for k in ("empty", "get_plan", "error")}))

    before = len(SENT)
    shown = client.post("/app/api/event", headers=auth, json={"name": "plan_choice_shown", "properties": {"first": g.get("first"), "screen": "today"}})
    bad_shown = client.post("/app/api/event", headers=auth, json={"name": "plan_choice_shown", "properties": {"first": "maybe"}})
    pick = client.post("/app/api/plan-choice", headers=auth, json={"option": "chat", "position": 2, "screen": "plan"})
    bad_pick = client.post("/app/api/plan-choice", headers=auth, json={"option": "chat", "position": 3})
    steps = [client.post("/app/api/event", headers=auth, json={"name": "plan_form_step", "properties": {"step": n, "screen": "plan", **({"prefilled": True} if n == 2 else {})}})
             for n in (1, 2, 3)]
    bad_step = client.post("/app/api/event", headers=auth, json={"name": "plan_form_step", "properties": {"step": 4}})
    link = client.post("/app/api/plan-choice/link", headers=auth, json={"race": "10k", "date": on(60), "km": 25, "days": [1, 3, 5], "long_day": 5}).json()
    opened = client.post("/app/api/event", headers=auth, json={"name": "plan_handoff_opened", "properties": {"assistant": "chatgpt", "screen": "plan", "race": "10k"}})
    bad_open = client.post("/app/api/event", headers=auth, json={"name": "plan_handoff_opened", "properties": {"assistant": "gemini"}})
    check("the events take only known words and steps, and refuse the rest",
          all(r.status_code == 200 for r in (shown, pick, opened, *steps))
          and all(r.status_code == 400 for r in (bad_shown, bad_pick, bad_step, bad_open)),
          str([r.status_code for r in (shown, pick, opened, *steps, bad_shown, bad_pick, bad_step, bad_open)]))
    time.sleep(0.3)
    got = {}
    for e in SENT[before:]:
        if e["distinct_id"] == me and e["event"].startswith("plan_"):
            got.setdefault(e["event"], []).append({k: v for k, v in e["properties"].items() if not k.startswith("$") and k not in ("account", "layout")})
    check("plan_choice_shown says which came first and where",
          got.get("plan_choice_shown") == [{"first": g.get("first"), "screen": "today"}], str(got.get("plan_choice_shown")))
    check("plan_choice_picked says which option and its position",
          got.get("plan_choice_picked") == [{"option": "chat", "position": 2, "screen": "plan"}], str(got.get("plan_choice_picked")))
    check("plan_form_step says each step, and that step 2 came filled in from Garmin",
          got.get("plan_form_step") == [{"step": 1, "screen": "plan"}, {"step": 2, "screen": "plan", "prefilled": True}, {"step": 3, "screen": "plan"}],
          str(got.get("plan_form_step")))
    check("plan_handoff_opened says which assistant",
          got.get("plan_handoff_opened") == [{"assistant": "chatgpt", "screen": "plan", "race": "10k"}], str(got.get("plan_handoff_opened")))
    check("the link is the message, for Claude and ChatGPT both, built here with no model asked",
          link.get("prompt", "").startswith("Use Daash to build me a training plan. My goal: a 10K on ")
          and unquote(link.get("chatgpt", "").split("?q=")[1]) == link["prompt"]
          and unquote(link.get("claude", "").split("?q=")[1]) == link["prompt"] and link.get("claude_app", "").startswith("claude://"), str(link)[:200])
    form = client.get("/app/api/plan-choice/form", headers=auth)
    check("the steps are filled in from Garmin where it can", form.status_code == 200 and isinstance(form.json().get("km"), (int, float))
          and isinstance(form.json().get("days"), list), form.text[:200])

    # -- a week on: plan_active_7d, read in the background
    person = store.email_fingerprint(email)
    with store._connect() as conn:
        conn.execute("UPDATE plan_choices SET picked_at = picked_at - 8 * 86400 WHERE person = ?", (person,))
    gone = store.save_user("g***@example.test", "{}", email_hash="fp-gone-1123", email="gone1123@example.test")
    store.plan_choice_first("fp-gone-1123", gone, draw=lambda: 0.9)
    store.record_plan_pick("fp-gone-1123", gone, "app", 1, now=int(time.time()) - 9 * 86400)
    with store._connect() as conn:
        conn.execute("DELETE FROM users WHERE user_token = ?", (gone,))
    fresh = store.save_user("f***@example.test", "{}", email_hash="fp-fresh-1123", email="fresh1123@example.test")
    store.record_plan_pick("fp-fresh-1123", fresh, "app", 1)
    flaky = store.save_user("k***@example.test", "{}", email_hash="fp-flaky-1123", email="flaky1123@example.test")
    store.record_plan_pick("fp-flaky-1123", flaky, "chat", 1, now=int(time.time()) - 8 * 86400)
    reads = []

    def read(token_):
        reads.append(token_)
        if token_ == flaky:
            raise RuntimeError("Garmin busy")
        return plan_with({"date": on(-3), "status": "done", "type": "easy"}, {"date": on(4), "status": "ahead", "type": "long"})

    before = len(SENT)
    n = hosted.plan_checks_once(read=read, spacing=0)
    time.sleep(0.3)
    weekly = {e["distinct_id"]: e["properties"] for e in SENT[before:] if e["event"] == "plan_active_7d"}
    mine = weekly.get(me) or {}
    check("a week on, the person who picked the chat way still has a plan: active, with the option and position",
          mine.get("active") is True and mine.get("option") == "chat" and mine.get("position") == 2
          and mine.get("done_7d") == 1 and mine.get("ahead_14d") == 1 and mine.get("days_since_pick") == 8, str(mine))
    gone_said = weekly.get(analytics.distinct_id("fp-gone-1123")) or {}
    check("someone who disconnected counts as not active, still with their option",
          gone_said.get("active") is False and gone_said.get("option") == "app" and gone_said.get("disconnected") is True, str(gone_said))
    check("a pick under a week old waits", analytics.distinct_id("fp-fresh-1123") not in weekly and fresh not in reads)
    check("one Garmin won't answer is tried again next time, not counted",
          analytics.distinct_id("fp-flaky-1123") not in weekly and flaky in reads and n == 2, f"{n} {list(weekly)}")
    again_n = hosted.plan_checks_once(read=read, spacing=0)
    check("each person is checked once", again_n == 0 and store.record_plan_pick(person, user_token, "app", 1) is False)
    quiet = plan_with({"date": on(-20), "status": "done", "type": "easy"})
    store.record_plan_pick("fp-quiet-1123", fresh, "app", 2, now=int(time.time()) - 8 * 86400)
    before = len(SENT)
    hosted.plan_checks_once(read=lambda t: quiet if t == fresh else (_ for _ in ()).throw(RuntimeError("busy")), spacing=0)
    time.sleep(0.3)
    q = next((e["properties"] for e in SENT[before:] if e["event"] == "plan_active_7d"
              and e["distinct_id"] == analytics.distinct_id("fp-quiet-1123")), {})
    check("nothing done this week and nothing ahead is not active, sent as active false",
          q.get("active") is False and q.get("done_7d") == 0 and q.get("ahead_14d") == 0, str(q))


def main() -> int:
    extra_checks()
    feeling_rule_checks()
    verdict_checks()
    analysis_checks()
    strides_checks()
    easier_checks()
    api_checks()
    assistant_checks()
    get_plan_checks()
    build_checks()
    race_prompt_checks()
    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
