"""Providers besides Garmin, before any adapter: the flag, the card, the connection lifecycle,
the events, and one list of runs with Garmin first (provider_routes.py, providers/catalog.py,
providers/merge.py).

What has to hold:
- the flag: only a person provider_intervals_enabled is on for sees Intervals.icu; for
  everyone else every route is the plain 404 and the You screen is exactly as before;
- no adapter yet: Connect stores nothing and says so;
- with a connector (a stand-in, not Intervals.icu's API): credentials checked first,
  stored encrypted, a refusal stores nothing, Disconnect revokes and deletes, and a grant
  gone bad reads Reconnect needed; provider_connected / _disconnected / _error carry no
  credentials;
- the merge: a run both providers have shows once, Garmin's copy; "different runs" stay apart.

    .venv/bin/python tests/provider_connect_test.py
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(os.environ.get("TMPDIR", "/tmp")) / "garmin-provider-connect-test"
SCRATCH.mkdir(parents=True, exist_ok=True)

from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_DB"] = str(SCRATCH / f"test-{secrets.token_hex(6)}.sqlite3")
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()
os.environ["GARMIN_MCP_OAUTH"] = "1"
os.environ["GARMIN_MCP_BASE_URL"] = "http://localhost"
os.environ["GARMIN_MCP_POSTHOG_KEY"] = "phc_test_key"
os.environ.pop("ANALYTICS_INTERNAL_IDS", None)

from garmin_mcp import session as session_mod  # noqa: E402
from tests.demo_garmin import DemoGarmin  # noqa: E402

DEMO = DemoGarmin()
session_mod.build_client = lambda **_: DEMO

import garmin_mcp.hosted as hosted  # noqa: E402
from garmin_mcp import analytics, provider_routes, store  # noqa: E402
from garmin_mcp.providers import catalog, merge  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

SENT: list[dict] = []
analytics._post = SENT.append
FLAGGED: set[str] = set()


def flags_answer(who: str) -> dict:
    return {"flags": {"provider_intervals_enabled": {"enabled": who in FLAGGED}}}


analytics._fetch_flags = flags_answer

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


def posted(event: str) -> list[dict]:
    time.sleep(0.3)
    return [e for e in SENT if e["event"] == event]


def person(email: str) -> tuple[str, dict]:
    user_token = store.save_user(session_mod.mask_email(email), '{"di_token": "x"}',
                                 email_hash=store.email_fingerprint(email), email=email)
    token = secrets.token_urlsafe(24)
    store.save_oauth_token(token, kind="access", client_id="daash-app", subject=user_token, scopes="",
                           resource=None, expires_at=int(time.time()) + 3600)
    return user_token, {"Authorization": f"Bearer {token}"}


class StandIn:
    """A connector with Intervals.icu's name and nothing of its API: it accepts the word 'good'."""

    def __init__(self) -> None:
        self.verified: list[tuple[str, dict]] = []
        self.revoked: list[dict] = []
        self.revoke_fails = False

    def verify(self, auth, credentials):
        self.verified.append((auth, credentials))
        if credentials.get("key") == "unreachable":
            raise OSError("no route")
        if credentials.get("key") != "good":
            raise catalog.ConnectError("That key wasn't accepted.")
        return "athlete-42"

    def revoke(self, credentials):
        self.revoked.append(credentials)
        if self.revoke_fails:
            raise OSError("revoke failed")


def flow_checks() -> None:
    client = TestClient(hosted.build_app(), base_url="http://localhost")
    bart_token, bart = person("bart@example.com")
    other_token, other = person("someone@example.com")
    FLAGGED.add(analytics.distinct_id(store.email_fingerprint("bart@example.com")))
    analytics._FLAGS.clear()

    unknown = client.get("/app/api/no-such-thing", headers=other)
    gated = [("GET", "/app/api/providers", None),
             ("POST", "/app/api/providers/intervals/connect", {"auth": "api_key", "credentials": {"key": "good"}}),
             ("POST", "/app/api/providers/intervals/disconnect", {})]

    def all_hidden(headers: dict) -> bool:
        return all(client.request(m, p, headers=headers, json=b).status_code == 404
                   and client.request(m, p, headers=headers, json=b).json() == unknown.json() for m, p, b in gated)

    check("signed out: 401 like every app route", client.get("/app/api/providers").status_code == 401)
    check("without the flag every route is the same 404 as an unknown path", all_hidden(other))
    you_other = client.get("/app/api/you", headers=other).json()
    check("without the flag the You screen has no Connections group",
          not any(g["title"] == "Connections" for g in you_other["groups"]), str([g["title"] for g in you_other["groups"]]))

    you = client.get("/app/api/you", headers=bart).json()
    group = next((g for g in you["groups"] if g["title"] == "Connections"), None)
    check("with the flag: a Connections group with Intervals.icu, Connect, and the explainer",
          group is not None and group["rows"] == [{"l": "Intervals.icu", "action": "provider-intervals", "v": "Connect",
                                                    "tone": "red", "sub": catalog.CATALOG["intervals"].explainer}], str(group))
    cards = client.get("/app/api/providers", headers=bart).json()["providers"]
    check("the card: neutral words, not connected, Connect off until an adapter exists",
          cards and cards[0]["label"] == "Intervals.icu" and cards[0]["via"] == "Via Intervals.icu"
          and cards[0]["state"] == "connect" and cards[0]["can_connect"] is False, str(cards))

    r = client.post("/app/api/providers/intervals/connect", headers=bart, json={"auth": "api_key", "credentials": {"key": "good"}})
    check("no adapter: Connect says it isn't ready and stores nothing",
          r.status_code == 409 and "isn't ready yet" in r.json()["error"] and store.provider_connection(bart_token, "intervals") is None,
          r.text)

    stand_in = StandIn()
    catalog.register_connector("intervals", stand_in)
    try:
        r = client.post("/app/api/providers/intervals/connect", headers=bart, json={"auth": "carrier_pigeon", "credentials": {"key": "good"}})
        check("an auth the provider doesn't offer is refused", r.status_code == 400 and not stand_in.verified, r.text)
        r = client.post("/app/api/providers/intervals/connect", headers=bart, json={"auth": "api_key", "credentials": {}})
        check("nothing to connect with is refused", r.status_code == 400 and not stand_in.verified, r.text)

        before = len(SENT)
        r = client.post("/app/api/providers/intervals/connect", headers=bart, json={"auth": "api_key", "credentials": {"key": "nope"}})
        errors = [e for e in posted("provider_error") if SENT.index(e) >= before]
        check("a refused key: the provider's words, nothing stored, provider_error 'refused'",
              r.status_code == 400 and r.json()["error"] == "That key wasn't accepted."
              and store.provider_connection(bart_token, "intervals") is None
              and errors and errors[-1]["properties"]["error_type"] == "refused" and errors[-1]["properties"]["provider"] == "intervals",
              r.text)
        r = client.post("/app/api/providers/intervals/connect", headers=bart, json={"auth": "api_key", "credentials": {"key": "unreachable"}})
        check("the provider unreachable: a plain error, nothing stored",
              r.status_code == 502 and store.provider_connection(bart_token, "intervals") is None, r.text)

        before = len(SENT)
        r = client.post("/app/api/providers/intervals/connect", headers=bart, json={"auth": "api_key", "credentials": {"key": "good"}})
        conn = store.provider_connection(bart_token, "intervals")
        check("a good key: checked first, stored with the provider's id, the card Live",
              r.status_code == 200 and r.json()["message"] == "Connected to Intervals.icu."
              and conn is not None and conn.external_id == "athlete-42" and conn.credentials == {"key": "good"}
              and r.json()["provider"]["state"] == "live" and r.json()["provider"]["words"] == "Connected to Intervals.icu", r.text)
        raw = sqlite3.connect(os.environ["GARMIN_MCP_DB"]).execute(
            "SELECT credentials_enc FROM provider_connections WHERE user_token = ?", (bart_token,)).fetchone()[0]
        check("stored encrypted", b"good" not in bytes(raw))
        connected = [e for e in posted("provider_connected") if SENT.index(e) >= before]
        check("provider_connected with the provider and the auth, no credentials",
              connected and connected[-1]["properties"].get("provider") == "intervals"
              and connected[-1]["properties"].get("auth") == "api_key"
              and "good" not in str(connected[-1]), str(connected))
        row = next(g for g in client.get("/app/api/you", headers=bart).json()["groups"] if g["title"] == "Connections")["rows"][0]
        check("the You row reads Live, via Intervals.icu", row["v"] == "Live" and row["sub"] == "Via Intervals.icu", str(row))

        provider_routes.provider_failed(bart_token, "intervals", "auth_revoked", "Intervals.icu no longer accepts Daash's access.")
        card = client.get("/app/api/providers", headers=bart).json()["providers"][0]
        check("a grant gone bad: Reconnect needed, with the reason",
              card["state"] == "reconnect" and card["words"] == "Reconnect needed"
              and card["error"] == "Intervals.icu no longer accepts Daash's access.", str(card))
        check("and provider_error says which kind", posted("provider_error")[-1]["properties"]["error_type"] == "auth_revoked")
        provider_routes.provider_failed(bart_token, "intervals", "rate_limited")
        check("a passing failure (rate limited) leaves the connection as it was",
              client.get("/app/api/providers", headers=bart).json()["providers"][0]["state"] == "reconnect")

        r = client.post("/app/api/providers/intervals/connect", headers=bart, json={"auth": "api_key", "credentials": {"key": "good"}})
        check("reconnecting clears the error and says reconnect",
              r.json()["provider"]["state"] == "live" and posted("provider_connected")[-1]["properties"].get("reconnect") is True)

        stand_in.revoke_fails = True
        r = client.post("/app/api/providers/intervals/disconnect", headers=bart, json={})
        check("disconnect: revoked with the provider (even when that fails), then deleted",
              r.status_code == 200 and stand_in.revoked == [{"key": "good"}]
              and store.provider_connection(bart_token, "intervals") is None
              and r.json()["provider"]["state"] == "connect", r.text)
        check("provider_disconnected sent; the revoke failure reported, not shown",
              posted("provider_disconnected")[-1]["properties"]["provider"] == "intervals"
              and posted("provider_error")[-1]["properties"]["error_type"] == "revoke_failed")
        check("someone else can't reach Bart's connection", all_hidden(other))
    finally:
        catalog._CONNECTORS.clear()


def merge_checks() -> None:
    t = datetime(2026, 11, 26, 7, 2, tzinfo=timezone.utc)
    g = {"provider": "garmin", "id": "g1", "start": t.isoformat(), "distance_m": 9200, "duration_s": 2890}
    dup = {"provider": "intervals", "id": "i1", "start": (t + timedelta(seconds=40)).isoformat(), "distance_m": 9230, "duration_s": 2902}
    own = {"provider": "intervals", "id": "i2", "start": (t + timedelta(hours=26)).isoformat(), "distance_m": 6000, "duration_s": 2100}
    kept, merged = merge.merge([dup, g, own])
    check("a run both have shows once, Garmin's copy; order kept", kept == [g, own], str(kept))
    check("the hidden copy is recorded, not deleted", len(merged) == 1 and merged[0].kept is g and merged[0].hidden is dup)
    check("more than a minute apart: two runs", merge.merge([g, {**dup, "start": (t + timedelta(seconds=61)).isoformat()}])[1] == [])
    check("distance more than 2% apart: two runs", merge.merge([g, {**dup, "distance_m": 9400}])[1] == [])
    check("time more than 2% apart: two runs", merge.merge([g, {**dup, "duration_s": 2960}])[1] == [])
    check("'These are different runs' keeps them apart", merge.merge([g, dup], kept_apart={("g1", "i1")})[1] == [])
    check("two runs from the same provider never merge", merge.merge([g, {**g, "id": "g2"}])[1] == [])
    check("a start with no offset isn't guessed at", merge.merge([g, {**dup, "start": "2026-11-26T07:02:40"}])[1] == [])
    check("a missing distance isn't a match", merge.merge([g, {**dup, "distance_m": None}])[1] == [])
    check("priority can put the other provider first",
          merge.merge([g, dup], priority=("intervals", "garmin"))[0] == [dup])


def main() -> int:
    flow_checks()
    merge_checks()
    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
