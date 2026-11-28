"""The provider layer (garmin_mcp/providers/): Garmin behind it, unchanged.

    .venv/bin/python tests/providers_test.py

The before-and-after checks run server.py's _run and _remembered exactly as
they were before the layer (copied below from main at 4f5974f) next to the
GarminProvider that replaced them, over every route a call can take, and
require the same answer, the same error and the same client reached.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_DB"] = str(Path(tempfile.mkdtemp()) / "providers-test.sqlite3")
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()

import anyio  # noqa: E402

from garmin_mcp import providers, server, store, tools_v2  # noqa: E402
from garmin_mcp.official_client import NotOnOfficial  # noqa: E402
from garmin_mcp.providers import Capability, NotSupported, Provider  # noqa: E402
from garmin_mcp.providers import workout as daash_workout  # noqa: E402
from garmin_mcp.providers.garmin import NOT_ON_OFFICIAL, GarminProvider  # noqa: E402
from garmin_mcp.session import GarminError  # noqa: E402
from garmin_mcp.workouts import WorkoutError, build_workout  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


def outcome(fn):
    """What a call did: ("ok", value) or ("raised", type, message)."""
    try:
        return ("ok", fn())
    except Exception as exc:  # noqa: BLE001
        return ("raised", type(exc).__name__, str(exc))


# --------------------------------------------------------------------------
# Fakes for the two Garmin routes
# --------------------------------------------------------------------------


class FakeConnect:
    """session.session: Garmin Connect through the private sign-in."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, fn):
        self.calls.append("run")
        return fn("connect-client")

    def remembered(self, key, ttl, fn):
        self.calls.append(f"remembered:{key}:{ttl}")
        return fn("connect-client")

    def forget(self) -> None:
        self.calls.append("forget")


class FakeOfficial:
    """OfficialClient for an official-only account."""

    def __init__(self, alone: bool, answers: bool) -> None:
        self.alone = alone
        self.answers = answers
        self.calls = 0

    def run(self, fn):
        self.calls += 1
        if not self.answers:
            raise NotOnOfficial("readiness")
        return fn("official-client")


# server.py's _run and _remembered before the provider layer, verbatim but for
# the two module globals they read, passed in.
def old_run(fn, _official_client, session):
    client = _official_client()
    if client is not None:
        try:
            return client.run(fn)
        except NotOnOfficial:
            if client.alone:
                raise GarminError(NOT_ON_OFFICIAL) from None
    return session.run(fn)


def old_remembered(key, ttl, fn, _official_client, session):
    client = _official_client()
    if client is not None:
        try:
            return client.run(fn)
        except NotOnOfficial:
            if client.alone:
                raise GarminError(NOT_ON_OFFICIAL) from None
    return session.remembered(key, ttl, fn)


ROUTES = {
    "password sign-in (no official link)": lambda: None,
    "official-only, the official API answers": lambda: FakeOfficial(alone=False, answers=True),
    "official-only with Connect too, the official API has no answer": lambda: FakeOfficial(alone=False, answers=False),
    "official-only, no Connect, the official API has no answer": lambda: FakeOfficial(alone=True, answers=False),
    "official-only, no Connect, the official API answers": lambda: FakeOfficial(alone=True, answers=True),
}
FNS = {
    "a read": lambda c: f"profile from {c}",
    "a read Garmin refuses": lambda c: (_ for _ in ()).throw(GarminError("Garmin said no")),
}


print("before and after: Garmin's routes through GarminProvider")
for route, make_official in ROUTES.items():
    for what, fn in FNS.items():
        for kind in ("run", "remembered"):
            before_official, after_official = make_official(), make_official()
            before_connect, after_connect = FakeConnect(), FakeConnect()
            provider = GarminProvider(lambda o=after_official: o, after_connect)
            if kind == "run":
                before = outcome(lambda: old_run(fn, lambda: before_official, before_connect))
                after = outcome(lambda: provider.run(fn))
            else:
                before = outcome(lambda: old_remembered("k", 90, fn, lambda: before_official, before_connect))
                after = outcome(lambda: provider.remembered("k", 90, fn))
            same_route = (before_connect.calls == after_connect.calls
                          and getattr(before_official, "calls", None) == getattr(after_official, "calls", None))
            check(f"{route}, {what}, {kind}: same answer and same client",
                  before == after and same_route, f"{before} vs {after}; {before_connect.calls} vs {after_connect.calls}")

connect = FakeConnect()
GarminProvider(lambda: None, connect).forget()
check("forget() clears the Connect session's memory", connect.calls == ["forget"])
check("server.NOT_ON_OFFICIAL is still there, with the same words",
      server.NOT_ON_OFFICIAL == NOT_ON_OFFICIAL and NOT_ON_OFFICIAL.startswith("Not available on this connection"))


print("\nthe registry")
check("Garmin is registered, and is the default", providers.names() == ["garmin"] and providers.DEFAULT == "garmin")
check("outside a request the current provider is Garmin", providers.current() is server.GARMIN)
check("Garmin says it can do everything Daash does", server.GARMIN.capabilities == frozenset(Capability))
for name in (None, "", "intervals", "nonsense"):
    token = providers.use(name)
    try:
        check(f"a stored choice of {name!r} with nothing registered by that name is Garmin",
              providers.current() is server.GARMIN and providers.current_name() == "garmin")
    finally:
        providers.reset(token)


class FakeProvider(Provider):
    name = "fake"
    label = "Fake Service"
    capabilities = frozenset({Capability.ACTIVITIES, Capability.SLEEP})

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, fn):
        self.calls.append("run")
        return fn("fake-client")

    def remembered(self, key, ttl, fn):
        self.calls.append("remembered")
        return fn("fake-client")

    def forget(self) -> None:
        self.calls.append("forget")


fake = providers.register(FakeProvider())
token = providers.use("fake")
try:
    check("a registered provider, once chosen, answers the request", providers.current() is fake)
    check("server._run hands the call to it", server._run(lambda c: c) == "fake-client" and fake.calls == ["run"])
    check("server._remembered too", server._remembered("k", 90, lambda c: c) == "fake-client")
    got = asyncio.run(server._call(lambda c: c))
    check("and on the worker thread, where the calls really run (the choice carries over)", got == "fake-client", got)
    check("it can do what it says", fake.supports(Capability.SLEEP) and not fake.supports(Capability.SCHEDULE))
    try:
        fake.require(Capability.SCHEDULE)
        raised = None
    except NotSupported as exc:
        raised = exc
    check("and says plainly what it can't, naming nothing but itself",
          raised is not None and str(raised) == "Fake Service doesn't share scheduled workouts with Daash on this connection."
          and raised.provider == "fake" and raised.capability is Capability.SCHEDULE, str(raised))
finally:
    providers.reset(token)
check("the choice ends with the request", providers.current() is server.GARMIN)


print("\ntools: a provider that can't is an answer, not a failure; a write forgets")


@server.tool_errors
async def get_readiness_probe():
    providers.current().require(Capability.READINESS)
    return {"readiness": 1}


@server.tool_errors
async def schedule_workout():  # named as a write, so tool_errors forgets after it
    return {"scheduled": True}


token = providers.use("fake")
try:
    fake.calls.clear()
    answer = asyncio.run(get_readiness_probe())
    check("NotSupported reaches the person as an error in words",
          answer == {"error": "Fake Service doesn't share a readiness score with Daash on this connection."}, str(answer))
    asyncio.run(schedule_workout())
    check("after a write, the current provider forgets what it remembered", fake.calls[-1:] == ["forget"], str(fake.calls))
finally:
    providers.reset(token)
del providers.registry._PROVIDERS["fake"]


print("\nthe store")
person = store.save_user("p***@example.com", "{}", email_hash="fp-provider-1127", email="provider1127@example.com")
other = store.save_user("o***@example.com", "{}", email_hash="fp-provider-other", email="other1127@example.com")
check("someone's provider is the default (NULL) until they choose", store.get_user(person).default_provider is None)
check("the rest of get_user is as it was",
      store.get_user(person).email_hash == "fp-provider-1127" and store.get_user(person).email_masked == "p***@example.com")
check("a choice is kept", store.set_default_provider(person, "intervals") and store.get_user(person).default_provider == "intervals")
check("and while nothing by that name is registered, they still get Garmin",
      providers.get(store.get_user(person).default_provider) is server.GARMIN)

store.save_provider_connection(person, "intervals", "api_key", {"api_key": "s3cret-key-1127"}, external_id="i12345")
link = store.provider_connection(person, "intervals")
check("a provider link is saved, connected, with its credentials",
      link is not None and link.credentials == {"api_key": "s3cret-key-1127"} and link.status == "connected"
      and link.auth == "api_key" and link.external_id == "i12345")
raw = sqlite3.connect(os.environ["GARMIN_MCP_DB"]).execute(
    "SELECT credentials_enc FROM provider_connections WHERE user_token = ?", (person,)).fetchone()[0]
check("the credentials are encrypted at rest", b"s3cret-key-1127" not in bytes(raw))
check("only that person's links are theirs",
      [c.provider for c in store.provider_connections(person)] == ["intervals"] and store.provider_connections(other) == [])
check("an error is noted for the connection card", store.mark_provider_error(person, "intervals", "Key refused")
      and store.provider_connection(person, "intervals").status == "error"
      and store.provider_connection(person, "intervals").last_error == "Key refused")
store.save_provider_connection(person, "intervals", "oauth", {"access_token": "a", "refresh_token": "r"})
check("connecting again replaces the credentials and clears the error",
      store.provider_connection(person, "intervals").status == "connected"
      and store.provider_connection(person, "intervals").auth == "oauth"
      and store.provider_connection(person, "intervals").last_error is None)
check("a refresh swaps the tokens", store.update_provider_credentials(person, "intervals", {"access_token": "b"})
      and store.provider_connection(person, "intervals").credentials == {"access_token": "b"})
try:
    store.save_provider_connection(person, "intervals", "password", {})
    bad_auth = False
except ValueError:
    bad_auth = True
check("a password is never stored for a provider (oauth or an API key only)", bad_auth)
check("unlinking the provider they chose takes them back to the default",
      store.delete_provider_connection(person, "intervals") and store.provider_connection(person, "intervals") is None
      and store.get_user(person).default_provider is None)
store.save_provider_connection(other, "intervals", "api_key", {"api_key": "k"})
store.delete_user(other)
left = sqlite3.connect(os.environ["GARMIN_MCP_DB"]).execute(
    "SELECT COUNT(*) FROM provider_connections WHERE user_token = ?", (other,)).fetchone()[0]
check("forgetting someone forgets their provider links too", left == 0)


print("\ndaash.workout.v1")
FIVE_BY_1K = [
    {"type": "warmup", "duration_seconds": 900},
    {"type": "repeat", "times": 5, "steps": [
        {"type": "interval", "distance_meters": 1000, "pace": "4:05"},
        {"type": "recovery", "duration_seconds": 90}]},
    {"type": "cooldown", "until_lap": True},
]
doc = daash_workout.from_tool_args("5 x 1 km", FIVE_BY_1K, description="Easy between.")
check("create_workout's arguments are a daash.workout.v1 as they are",
      doc == {"schema": "daash.workout.v1", "name": "5 x 1 km", "sport": "running", "description": "Easy between.",
              "steps": FIVE_BY_1K}, str(doc))
check("normalising is idempotent", daash_workout.normalise(doc) == doc)
chatty = daash_workout.normalise({"name": "x", "steps": [
    {"type": "warm up", "minutes": 15},
    {"type": "set", "reps": 5, "steps": [{"type": "rep", "distance_km": 1, "target_pace": "4:05"},
                                          {"type": "jog", "seconds": 90}]},
    {"type": "cool-down", "until_lap": True}]})
check("the spellings create_workout accepts come out canonical", chatty["steps"] == FIVE_BY_1K, str(chatty["steps"]))
check("a step with no type is an interval, as the Garmin builder reads it",
      daash_workout.normalise({"name": "x", "steps": [{"duration_seconds": 60}]})["steps"][0]["type"] == "interval")
check("a repeat's iterations reads as times, as the Garmin builder reads it",
      daash_workout.normalise({"name": "x", "steps": [{"type": "repeat", "iterations": 2, "steps": [
          {"type": "interval", "duration_seconds": 60}]}]})["steps"][0]["times"] == 2)
swim = daash_workout.normalise({"name": "Swim", "sport": "swimming", "pool_length_meters": 25, "steps": [
    {"type": "interval", "distance_meters": 100, "stroke": "freestyle", "note": "Smooth"}]})
check("a swim keeps its pool, stroke and note",
      swim["pool_length_meters"] == 25 and swim["steps"][0] == {"type": "interval", "distance_meters": 100,
                                                               "stroke": "freestyle", "note": "Smooth"})
BAD = {
    "no name": {"steps": FIVE_BY_1K},
    "no steps": {"name": "x", "steps": []},
    "an unknown sport": {"name": "x", "sport": "rowing", "steps": FIVE_BY_1K},
    "another schema": {"schema": "daash.workout.v2", "name": "x", "steps": FIVE_BY_1K},
    "an unknown step type": {"name": "x", "steps": [{"type": "sprint-ish", "duration_seconds": 60}]},
    "a step with no end": {"name": "x", "steps": [{"type": "interval"}]},
    "a step with two ends": {"name": "x", "steps": [{"type": "interval", "duration_seconds": 60, "distance_meters": 200}]},
    "two targets": {"name": "x", "steps": [{"type": "interval", "duration_seconds": 60, "pace": "4:00", "hr": [150, 160]}]},
    "a repeat in a repeat": {"name": "x", "steps": [{"type": "repeat", "times": 2, "steps": [
        {"type": "repeat", "times": 2, "steps": [{"type": "interval", "duration_seconds": 60}]}]}]},
    "a repeat of zero": {"name": "x", "steps": [{"type": "repeat", "times": 0, "steps": FIVE_BY_1K[:1]}]},
    "a negative length": {"name": "x", "steps": [{"type": "interval", "duration_seconds": -5}]},
}
for what, bad in BAD.items():
    result = outcome(lambda: daash_workout.normalise(bad))
    check(f"refuses {what}, in words", result[0] == "raised" and result[1] == "WorkoutError" and result[2], str(result))

print("\ndaash.workout.v1 builds the same Garmin workout as the steps it came from")
SAMPLES = {
    "intervals": ("5 x 1 km", "running", FIVE_BY_1K),
    "hr capped easy run": ("Easy", "running", [{"type": "interval", "duration_seconds": 2700, "hr": {"max": 145}}]),
    "a ride on power": ("Sweet spot", "cycling", [{"type": "warmup", "duration_seconds": 600},
                                                 {"type": "interval", "duration_seconds": 1200, "power": [240, 260]},
                                                 {"type": "cooldown", "duration_seconds": 600}]),
    "chat spellings": ("Tempo", "running", [{"type": "warm up", "minutes": 10},
                                            {"type": "tempo", "distance_km": 5, "pace": ["4:20", "4:30"]},
                                            {"type": "cool down", "minutes": 10}]),
}
for what, (name, sport, steps) in SAMPLES.items():
    v2 = tools_v2.ON.set(True)  # the spellings are read on the Nov 26 tool fixes
    try:
        direct = build_workout(name, sport, steps)
    finally:
        tools_v2.ON.reset(v2)
    via = daash_workout.from_tool_args(name, steps, sport)
    built = build_workout(via["name"], via["sport"], via["steps"])
    check(f"{what}: identical Garmin JSON, summary and estimate",
          direct[0].model_dump() == built[0].model_dump() and direct[1:] == built[1:])

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
