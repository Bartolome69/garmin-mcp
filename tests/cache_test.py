"""Garmin's answers kept in SQLite (garmin_mcp/garmin_cache.py).

    python tests/cache_test.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = tempfile.mkdtemp()
os.environ["GARMIN_MCP_DB"] = str(Path(tmp) / "cache-test.sqlite3")
from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()

from garmin_mcp import garmin_cache as cache  # noqa: E402
from garmin_mcp import store, telemetry  # noqa: E402

store.DB_PATH = Path(os.environ["GARMIN_MCP_DB"])

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


class Garmin:
    """Counts what reaches 'Garmin'."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.display_name = "someone"
        self.fail = False

    def _note(self, *call):
        self.calls.append(call)
        if self.fail:
            raise RuntimeError("Garmin fell over")

    def get_sleep_data(self, day):
        self._note("sleep", day)
        return {"dailySleepDTO": {"calendarDate": day, "sleepTimeSeconds": 25200}}

    def get_activity_splits(self, activity_id):
        self._note("splits", activity_id)
        return {"lapDTOs": [{"distance": 1000}]}

    def get_activity_hr_in_timezones(self, activity_id):
        self._note("zones", activity_id)
        return []

    def get_activity(self, activity_id):
        self._note("activity", activity_id)
        return {"activityId": activity_id, "summaryDTO": {"startTimeLocal": self.started}}

    def get_activities_by_date(self, start, end, *rest):
        self._note("list", start, end)
        return [{"activityId": 1}]

    def get_activities(self, start, limit):
        self._note("recent", start, limit)
        return [{"activityId": 1}]

    def get_user_profile(self):
        self._note("profile")
        return {"ok": True}


def ts(utc: str) -> float:
    return datetime.fromisoformat(utc).replace(tzinfo=timezone.utc).timestamp()


print("how long a date stays fresh")
noon = ts("2026-11-25T12:00:00")
check("today: 15 minutes", cache.ttl_for(date(2026, 11, 25), noon) == 15 * 60)
check("yesterday: 6 hours", cache.ttl_for(date(2026, 11, 24), noon) == 6 * 3600)
check("two days ago and older: 7 days", cache.ttl_for(date(2026, 11, 23), noon) == 7 * 86400
      and cache.ttl_for(date(2026, 1, 1), noon) == 7 * 86400)
check("a date ahead (someone already in tomorrow): 15 minutes", cache.ttl_for(date(2026, 11, 26), noon) == 15 * 60)
late = ts("2026-11-26T04:00:00")  # 20:00 on the 25th in California
check("California's evening still counts the 25th as today, though UTC is on the 26th",
      cache.ttl_for(date(2026, 11, 25), late) == 15 * 60)
check("from 12:00 UTC on the 26th the 25th is yesterday everywhere",
      cache.ttl_for(date(2026, 11, 25), ts("2026-11-26T12:30:00")) == 6 * 3600)

print("\nreads through a person's client")
g = Garmin()
c = cache.view(g, "person-a")
telemetry.take()
today = date.today().isoformat()
first = c.get_sleep_data(today)
second = c.get_sleep_data(today)
check("the second read of the same night comes from the cache", len(g.calls) == 1 and first == second, str(g.calls))
counts = telemetry.take()
check("one miss, then one hit, counted", counts.get("cache_misses") == 1 and counts.get("cache_hits") == 1, str(counts))
other = cache.view(g, "person-b")
other.get_sleep_data(today)
check("never another person's answer", len(g.calls) == 2)
c.get_activity_splits(99)
c.get_activity_splits(99)
check("an activity's splits by id: fetched once", [x for x in g.calls if x[0] == "splits"] == [("splits", 99)])
row = sqlite3.connect(os.environ["GARMIN_MCP_DB"]).execute(
    "SELECT expires_at, payload FROM garmin_cache WHERE kind = 'activity_splits'").fetchone()
check("and kept for good", row[0] is None)
check("stored encrypted, not readable as it came from Garmin", b"lapDTOs" not in row[1] and b"1000" not in row[1])
check("the profile isn't cached (not one of the kinds)",
      (c.get_user_profile(), c.get_user_profile()) and [x for x in g.calls if x[0] == "profile"] == [("profile",), ("profile",)])
check("attributes pass straight through", c.display_name == "someone")

print("\nan activity's summary, which can be edited in the Garmin app")
g.started = (date.today() - timedelta(days=5)).isoformat() + "T07:00:00"
c.get_activity(1)
old = sqlite3.connect(os.environ["GARMIN_MCP_DB"]).execute(
    "SELECT expires_at FROM garmin_cache WHERE kind = 'activity' AND key = '1'").fetchone()[0]
check("five days old: kept for good", old is None)
g.started = date.today().isoformat() + "T07:00:00"
c.get_activity(2)
new = sqlite3.connect(os.environ["GARMIN_MCP_DB"]).execute(
    "SELECT expires_at - fetched_at FROM garmin_cache WHERE kind = 'activity' AND key = '2'").fetchone()[0]
check("this morning's: 15 minutes, so a note or a rename made in Garmin shows", new == 15 * 60, str(new))

print("\nlists and recent activities")
week_ago = (date.today() - timedelta(days=7)).isoformat()
c.get_activities_by_date(week_ago, today)
c.get_activities_by_date(week_ago, today)
check("a list up to today: cached", len([x for x in g.calls if x[0] == "list"]) == 1)
ttl = sqlite3.connect(os.environ["GARMIN_MCP_DB"]).execute(
    "SELECT expires_at - fetched_at FROM garmin_cache WHERE kind = 'activities'").fetchone()[0]
check("for 15 minutes, as it ends today", ttl == 15 * 60, str(ttl))
c.get_activities(0, 10)
c.get_activities(0, 10)
check("the latest-N list too", len([x for x in g.calls if x[0] == "recent"]) == 1)

print("\nwhat isn't cached")
g.fail = True
try:
    c.get_sleep_data("2026-01-01")
except RuntimeError:
    pass
g.fail = False
c.get_sleep_data("2026-01-01")
check("a failed read isn't kept: the next one goes to Garmin", [x for x in g.calls if x == ("sleep", "2026-01-01")]
      == [("sleep", "2026-01-01"), ("sleep", "2026-01-01")])
check("no owner (the local install): the client is used as it is", cache.view(g, None) is g)


class Empty(Garmin):
    def get_activity_hr_in_timezones(self, activity_id):
        self._note("zones", activity_id)
        return []


e = Empty()
ec = cache.view(e, "person-a")
ec.get_activity_hr_in_timezones(5)
ec.get_activity_hr_in_timezones(5)
check("an empty answer by id isn't kept for good (it may not be there yet)", len(e.calls) == 2)


class Huge(Garmin):
    def get_activity_splits(self, activity_id):
        self._note("splits", activity_id)
        return {"noise": os.urandom(400_000).hex()}


h = Huge()
hc = cache.view(h, "person-a")
hc.get_activity_splits(7)
hc.get_activity_splits(7)
check("an answer too big to be worth storing is skipped", len(h.calls) == 2)

print("\nafter a write")
c.get_sleep_data(today)
before = len(g.calls)
cache.after_write("person-a", "create_workout")
c.get_sleep_data(today)
check("a workout write leaves sleep alone", len(g.calls) == before)
cache.put("person-a", "calendar", "2026-11", {"x": 1}, 600)
cache.after_write("person-a", "schedule_workout")
check("but drops the person's calendar and plan entries", cache.get("person-a", "calendar", "2026-11") == (False, None))
c.get_activity(2)
calls = len(g.calls)
cache.after_write("person-a", "update_activity")
c.get_activity(2)
c.get_activities_by_date(week_ago, today)
check("editing an activity drops its summary and the lists", len(g.calls) == calls + 2)
check("someone else's entries are untouched", cache.get("person-b", "sleep", today)[0])

print("\nwhen someone leaves")
cache.put("token-x", "sleep", today, {"x": 1}, 600)
store.delete_user("token-x")
check("deleting their account deletes what Garmin said about them", cache.get("token-x", "sleep", today) == (False, None))

print("\npruning")
now = int(datetime.now(timezone.utc).timestamp())
cache.put("p", "sleep", "old", {"x": 1}, 10, now=now - 100)
cache.put("p", "activity_splits", "unread", {"x": 1}, None, now=now - 40 * 86400)
cache.put("p", "activity_splits", "read", {"x": 1}, None, now=now - 2 * 86400)
cache._pruned[0] = 0
cache._maybe_prune(now)
left = {k for (k,) in sqlite3.connect(os.environ["GARMIN_MCP_DB"]).execute(
    "SELECT key FROM garmin_cache WHERE user_id = 'p'")}
check("expired entries and long-unread kept ones go; recent ones stay", left == {"read"}, str(left))

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
