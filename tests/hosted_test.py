"""End-to-end test of the hosted server, against a stubbed Garmin account.

The thing worth proving here is isolation: two people's connector URLs must
resolve to their own Garmin session. Everything else the local suite covers.

    .venv/bin/python tests/hosted_test.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import contextlib
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(os.environ.get("TMPDIR", "/tmp")) / "garmin-hosted-test"
SCRATCH.mkdir(parents=True, exist_ok=True)
DB = SCRATCH / f"test-{int(time.time())}.sqlite3"

from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_DB"] = str(DB)
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()
# Turn analytics on so the events can be checked; the network call is replaced
# below, so nothing leaves the test.
os.environ["GARMIN_MCP_POSTHOG_KEY"] = "phc_test_key"
os.environ.setdefault("GARMIN_EMAIL", "test@example.com")
os.environ.setdefault("GARMIN_PASSWORD", "hunter2")

from garminconnect import (  # noqa: E402
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from tests.fake_garmin import PROFILE, FakeGarmin  # noqa: E402

import garmin_mcp.hosted as hosted  # noqa: E402
from garmin_mcp import analytics  # noqa: E402

# Every event the server would have sent to PostHog, kept for inspection.
SENT: list[dict] = []
analytics._post = SENT.append
from garmin_mcp import store  # noqa: E402
from garmin_mcp.session import GarminSession  # noqa: E402

# Each stub reports which account it belongs to, so we can prove one person's
# URL never reaches another person's session.
BUILT: list[str] = []
SIGNIN_CREDS: list[tuple] = []
SECRET = "never-show-this-blob-value"
# Signing in as this address makes the stub demand a multi-factor code, which is
# the path where the password used to survive in memory.
MFA_EMAIL = "mfa@example.com"
# Addresses the fake refuses, each the way the real library would: the
# exception class is what the server classifies on first.
WRONG_PASSWORD_EMAIL = "wrongpw@example.com"
RATE_LIMITED_EMAIL = "toomany@example.com"
BLOCKED_EMAIL = "blocked@example.com"


class _Garth:
    """Stands in for garth, whose dumps() is the blob _finish stores.

    Without this the stub had no `.client`, so _finish raised on every sign-in
    and the whole success path went unexercised — while the suite still printed
    "all checks passed".
    """

    def __init__(self, marker: str) -> None:
        self._marker = marker

    def dumps(self) -> str:
        return json.dumps({"di_token": self._marker})


def fake_build_client(*, prompt_mfa, email=None, password=None):
    SIGNIN_CREDS.append((email, password))
    class Stub(FakeGarmin):
        # A fresh sign-in has no tokenstore yet, so it is identified by the email
        # typed into the form; login() below switches this to whichever account
        # the tokenstore belongs to. Getting that wrong silently rewrites one
        # person's stored blob with another's on the next refresh.
        _marker = f"{email}-token"

        @property
        def client(self):
            return _Garth(self._marker)

        def login(self, tokenstore=None):
            BUILT.append(tokenstore or "no-token")
            if email == WRONG_PASSWORD_EMAIL:
                raise GarminConnectAuthenticationError(
                    "Authentication failed (401 Unauthorized). Possible causes: ..."
                )
            if email == RATE_LIMITED_EMAIL:
                raise GarminConnectTooManyRequestsError(
                    "Too many login attempts. Please wait a few minutes before trying again."
                )
            if email == BLOCKED_EMAIL:
                # Cloudflare's bot challenge in front of Garmin's SSO, as the
                # library wraps it: a plain connection error with the 403 inside.
                raise GarminConnectConnectionError(
                    "Login failed: 403 Client Error: Forbidden for url: "
                    "https://sso.garmin.com/sso/signin"
                )
            if email == MFA_EMAIL:
                # The real library returns early here, before the line that
                # drops the plaintext password.
                return ("needs_mfa", "fake-mfa-state")
            super().login(tokenstore)
            # Identify which account answered, without echoing the blob —
            # otherwise the leak assertions below would trip on the harness.
            try:
                marker = json.loads(tokenstore or "{}").get("di_token", "none")
            except ValueError:
                marker = "unparsed"
            if tokenstore:
                self._marker = marker
            self.full_name = f"account:{marker}"
            return (None, None)

        def get_user_profile(self):
            return {**PROFILE, "fullName": self.full_name}

    stub = Stub()
    # The real Garmin client keeps the typed password as an attribute, which is
    # what makes scrubbing it before parking the client meaningful.
    stub.password = password
    return stub


hosted.build_client = fake_build_client
import garmin_mcp.session as session_mod  # noqa: E402

session_mod.build_client = fake_build_client


async def anyio_run(fn, *args):
    import anyio
    return await anyio.to_thread.run_sync(fn, *args)


async def main() -> int:
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
        if not ok:
            failures.append(name)

    # A marker that must never appear in any response.
    # Alice signed in after emails were kept; Bob's row predates that.
    alice = store.save_user(
        "a***@example.com", json.dumps({"di_token": "alice-token", "secret": SECRET}),
        email_hash=store.email_fingerprint("alice@example.com"), email="alice@example.com",
    )
    bob = store.save_user("b***@example.com", json.dumps({"di_token": "bob-token"}))
    check("two users stored", store.count_users() == 2)

    app = hosted.build_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=8931, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.1)
    check("server started", server.started)

    base = "http://127.0.0.1:8931"

    async def call(user_token: str, tool: str, args: dict | None = None):
        async with streamable_http_client(f"{base}/u/{user_token}/mcp") as (r, w):
            async with ClientSession(r, w) as sess:
                await sess.initialize()
                result = await sess.call_tool(tool, args or {})
                if getattr(result, "structuredContent", None):
                    return result.structuredContent
                return json.loads(result.content[0].text)

    try:
        tools_seen = None
        async with streamable_http_client(f"{base}/u/{alice}/mcp") as (r, w):
            async with ClientSession(r, w) as sess:
                await sess.initialize()
                tools_seen = {t.name for t in (await sess.list_tools()).tools}
        check("tools served over http", "get_daily_summary" in (tools_seen or set()),
              f"{len(tools_seen or [])} tools")

        # Preview features, per account. Alice is on the list (written the way
        # a person types it); Bob is not. The plan view is everyone's now; the
        # preview-only tools are what the list still decides.
        async def offered(user_token: str):
            async with streamable_http_client(f"{base}/u/{user_token}/mcp") as (r, w):
                async with ClientSession(r, w) as sess:
                    await sess.initialize()
                    tools = (await sess.list_tools()).tools
                    resources = (await sess.list_resources()).resources
            plan_tool = next(t for t in tools if t.name == "get_plan")
            view = ((getattr(plan_tool, "meta", None) or {}).get("ui") or {}).get("resourceUri")
            listed = [str(r.uri) for r in resources if str(r.uri).startswith("ui://")]
            return view, listed, {t.name for t in tools}

        os.environ["GARMIN_MCP_PREVIEW"] = " Alice@Example.com , carol@example.com"
        try:
            view_a, listed_a, names_a = await offered(alice)
            view_b, listed_b, names_b = await offered(bob)
            check("everyone gets the plan view",
                  bool(view_a) and view_a in listed_a and bool(view_b) and view_b in listed_b,
                  f"{view_a} {view_b}")
            check("an account on the preview list gets the preview tools",
                  {"get_shoes", "get_recovery_trends"} <= names_a, str(sorted(names_a)))
            check("an account not on it does not",
                  not ({"get_shoes", "get_recovery_trends"} & names_b), str(sorted(names_b)))
            os.environ["GARMIN_MCP_PREVIEW"] = "on"
            _, _, names_b_on = await offered(bob)
            check("'on' switches it on for everyone", "get_shoes" in names_b_on)
            os.environ.pop("GARMIN_MCP_PREVIEW")
            _, _, names_a_off = await offered(alice)
            check("unset, nobody gets it", "get_shoes" not in names_a_off)
        finally:
            os.environ.pop("GARMIN_MCP_PREVIEW", None)

        day = await call(alice, "get_daily_summary", {"date": "2026-09-22"})
        check("alice gets data", day.get("steps") == 12345, str(day)[:120])

        BUILT.clear()
        hosted._SESSIONS.clear()  # force a fresh login so each one is observable
        status_a = await call(alice, "get_connection_status")
        status_b = await call(bob, "get_connection_status")
        name_a = str(status_a.get("garmin_display_name"))
        name_b = str(status_b.get("garmin_display_name"))
        check("alice is authenticated", status_a.get("authenticated") is True,
              str(status_a)[:160])
        check("alice's response came from her token", "alice-token" in name_a, name_a)
        check("bob's response came from his token", "bob-token" in name_b, name_b)
        check("neither leaks into the other",
              "bob-token" not in name_a and "alice-token" not in name_b)
        check("token blob never appears in a response",
              SECRET not in json.dumps(status_a) and SECRET not in json.dumps(day))
        check("no server filesystem path exposed",
              "/Users/" not in json.dumps(status_a)
              and "token_cache" in status_a
              and "path" not in json.dumps(status_a["token_cache"]),
              str(status_a.get("token_cache")))

        # stdlib rather than another dependency just for three requests
        import urllib.error
        import urllib.parse
        import urllib.request

        def fetch(path: str, data: bytes | None = None) -> tuple[int, str]:
            req = urllib.request.Request(
                f"{base}{path}",
                data=data,
                headers={"accept": "application/json, text/event-stream",
                         "content-type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return r.status, r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8", "replace")

        code, _ = await anyio_run(fetch, f"/u/{'z' * 40}/mcp",
                                  b'{"jsonrpc":"2.0","id":1,"method":"initialize"}')
        check("unknown connector URL rejected", code == 404, str(code))
        code, body = await anyio_run(fetch, "/")
        check("landing page serves", code == 200 and "Connect Garmin" in body)

        # The front page quotes this number, from another origin, so the body
        # has to be exactly one count and the CORS header has to be there.
        def fetch_stats() -> tuple[int, str, str]:
            with urllib.request.urlopen(f"{base}/stats", timeout=15) as r:
                return (r.status, r.headers.get("Access-Control-Allow-Origin", ""),
                        r.read().decode())

        code, cors, stats_raw = await anyio_run(fetch_stats)
        stats_body = json.loads(stats_raw) if code == 200 else {}
        check("stats serve one public count",
              code == 200 and list(stats_body) == ["active_30d"], stats_raw[:80])
        check("stats count both recent users",
              stats_body.get("active_30d") == 2, stats_raw[:80])
        check("stats readable from the website's origin", cors == "*", repr(cors))
        check("stats reveal nothing else",
              SECRET not in stats_raw and "example.com" not in stats_raw
              and alice not in stats_raw)

        with store._connect() as conn:
            seen = conn.execute(
                "SELECT last_seen_at FROM users WHERE user_token = ?", (alice,)
            ).fetchone()[0]
        check("a tool call marks the person as seen",
              seen is not None and time.time() - seen < 60, str(seen))
        landing = body
        code, body = await anyio_run(fetch, "/connect")
        check("sign-in form serves",
              code == 200 and "type=password" in body.replace('"', ""))

        # Both pages ask for a Garmin password, so both have to say what happens
        # to it. These are f-strings interpolating PRIVACY_URL: a stray brace or
        # a missing prefix renders the placeholder as literal text, which no
        # other check would notice.
        for label, page_body in (("landing page", landing), ("sign-in form", body)):
            check(f"{label} links the privacy policy",
                  hosted.PRIVACY_URL in page_body and "{PRIVACY_URL}" not in page_body)

        # The submitted credentials must reach Garmin. They previously did not:
        # build_client read the environment, so a hosted sign-in used the
        # operator's credentials or none at all.
        SIGNIN_CREDS.clear()
        form = urllib.parse.urlencode(
            {"email": "someone@example.com", "password": "their-own-password"}
        ).encode()
        req = urllib.request.Request(
            f"{base}/connect", data=form,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

        def post_signin():
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code

        await anyio_run(post_signin)
        check("sign-in passes the typed credentials through",
              SIGNIN_CREDS and SIGNIN_CREDS[-1] == ("someone@example.com", "their-own-password"),
              str(SIGNIN_CREDS[-1:]))

        # ---- everything below was unreachable until the stub grew a .client --

        def post(path: str, fields: dict) -> tuple:
            body = urllib.parse.urlencode(fields).encode()
            request = urllib.request.Request(
                f"{base}{path}", data=body,
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()

        def signin(email: str) -> tuple:
            return post("/connect", {"email": email, "password": "pw"})

        code, body = await anyio_run(signin, "repeat@example.com")
        first = re.search(r"/u/([A-Za-z0-9_-]{16,})/mcp", body)
        check("sign-in completes and shows a connector URL", code == 200 and bool(first),
              f"status {code}")

        # Signing in again used to mint a second URL and leave the first one
        # working for ever, with no way to take it back.
        code, body = await anyio_run(signin, "repeat@example.com")
        second = re.search(r"/u/([A-Za-z0-9_-]{16,})/mcp", body)
        check("signing in again issues a different URL",
              bool(second) and first and second.group(1) != first.group(1))
        if first and second:
            check("the previous URL is revoked, not left working",
                  store.get_user(first.group(1)) is None)
            check("the new URL works", store.get_user(second.group(1)) is not None)
            check("one row per person, not one per sign-in",
                  len(store.tokens_for(store.email_fingerprint("repeat@example.com"))) == 1)

            # Someone must be able to destroy their own session.
            code, body = await anyio_run(post, "/disconnect", {"t": second.group(1)})
            check("disconnect deletes the stored session",
                  code == 200 and store.get_user(second.group(1)) is None)

        # A sign-in that stops for a code parks the client in memory for ten
        # minutes. garminconnect only drops the password on the clean path, so
        # without scrubbing it here the plaintext sat in that dict the whole
        # time — while the page promised it was not kept.
        hosted._ATTEMPTS.clear()
        hosted._PENDING.clear()
        code, body = await anyio_run(signin, MFA_EMAIL)
        check("a sign-in needing a code asks for one",
              code == 200 and "multi-factor" in body, f"status {code}")
        check("the pending sign-in was parked", len(hosted._PENDING) == 1)
        check("no plaintext password survives in the pending sign-in",
              all(p["client"].password is None for p in hosted._PENDING.values()),
              str([p["client"].password for p in hosted._PENDING.values()]))
        hosted._PENDING.clear()

        # The credential must not reach the logs.
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            'GET /u/SUPERSECRETTOKENVALUE123456/mcp HTTP/1.1 200', (), None,
        )
        hosted.RedactUserTokens().filter(record)
        check("connector URL is redacted from log records",
              "SUPERSECRETTOKENVALUE123456" not in record.getMessage(),
              record.getMessage())

        # Sign-up is an open door unless an invite code is set.
        hosted.INVITE_CODE = "let-me-in"
        hosted._ATTEMPTS.clear()
        try:
            code, _ = await anyio_run(signin, "gatecrasher@example.com")
            check("sign-in refused without the invite code", code == 403, str(code))
            code, _ = await anyio_run(
                post, "/connect",
                {"email": "friend@example.com", "password": "pw", "invite": "let-me-in"},
            )
            check("sign-in accepted with the invite code", code == 200, str(code))
        finally:
            hosted.INVITE_CODE = ""

        # Each way Garmin says no gets its own page and its own word, because
        # each wants a different response from the person reading it.
        hosted._ATTEMPTS.clear()
        code, body = await anyio_run(signin, WRONG_PASSWORD_EMAIL)
        check("a wrong password is called a wrong password",
              code == 400 and "didn't accept that email and password" in body, f"status {code}")
        hosted._paused_until = 0.0
        code, body = await anyio_run(signin, RATE_LIMITED_EMAIL)
        check("a rate limit says to wait, not to worry",
              code == 400 and "slow down" in body and "paused for 30 minutes" in body, f"status {code}")

        # While Garmin's limit is in force, nobody's attempt is passed on to
        # it: each would count against the limit everyone shares.
        tried_before = len(SIGNIN_CREDS)
        code, body = await anyio_run(signin, "later@example.com")
        check("after a rate limit, the next sign-in is paused, not sent to Garmin",
              code == 429 and "paused" in body and "minutes" in body
              and len(SIGNIN_CREDS) == tried_before, f"status {code}")
        hosted._paused_until = 0.0
        code, body = await anyio_run(signin, "later@example.com")
        check("once the pause is over, sign-in works again",
              code == 200 and "/mcp" in body, f"status {code}")

        code, body = await anyio_run(signin, BLOCKED_EMAIL)
        check("a network block says to tell the operator",
              code == 400 and "blocking sign-ins from this" in body, f"status {code}")
        check("a network block pauses sign-ins too", hosted._pause_left() > 0)
        hosted._paused_until = 0.0

        # And repeated attempts from one address get throttled.
        hosted._ATTEMPTS.clear()
        codes = [(await anyio_run(signin, "spray@example.com"))[0] for _ in range(12)]
        check("repeated sign-in attempts are throttled", 429 in codes, str(codes[-3:]))

        # ---- what the server told PostHog, and what it kept to itself -------
        # Events go out on a thread, so give the last of them a moment to land.
        time.sleep(0.3)
        by_name: dict[str, list[dict]] = {}
        for sent in SENT:
            by_name.setdefault(sent["event"], []).append(sent)
        connected = by_name.get("connector_connected", [])
        first_calls = by_name.get("first_tool_call", [])
        failed = by_name.get("sign_in_failed", [])
        check("a completed sign-in is reported", len(connected) >= 1, str(len(connected)))
        check("a completed sign-in says how, and whether it was a repeat",
              all(e["properties"].get("via") in ("url", "oauth")
                  and isinstance(e["properties"].get("mfa"), bool)
                  and isinstance(e["properties"].get("returning"), bool)
                  for e in connected),
              str([e["properties"] for e in connected][:2]))
        check("the first request on a connection is reported once per person",
              len(first_calls) == 2, str(len(first_calls)))
        used = by_name.get("connector_used", [])
        check("each person's use is reported once a day, however many requests",
              len(used) == 2 and len({e["distinct_id"] for e in used}) == 2, str(len(used)))
        check("daily use carries the day and who, under the same id as first use",
              all(e["properties"].get("day") and e["properties"].get("account") for e in used)
              and {e["distinct_id"] for e in used} == {e["distinct_id"] for e in first_calls},
              str([e["properties"] for e in used][:2]))
        check("first-use ids differ per person and match nobody's token",
              len({e["distinct_id"] for e in first_calls}) == 2
              and not ({alice, bob} & {e["distinct_id"] for e in first_calls}))
        # The same address signing in repeatedly is one person, however many
        # tokens that minted; different addresses are different people.
        ids_by_account: dict[str, set[str]] = {}
        for e in connected:
            ids_by_account.setdefault(e["properties"]["account"], set()).add(e["distinct_id"])
        check("repeat sign-ins keep one id per account",
              ids_by_account and all(len(ids) == 1 for ids in ids_by_account.values())
              and len({next(iter(v)) for v in ids_by_account.values()}) == len(ids_by_account),
              str({k: len(v) for k, v in ids_by_account.items()}))
        reasons = {e["properties"].get("reason") for e in failed}
        check("failed sign-ins carry the right one-word reason",
              {"throttled", "bad_credentials", "rate_limited", "blocked_by_garmin", "paused"} <= reasons
              and reasons <= {"throttled", "blocked_by_garmin", "rate_limited",
                              "bad_credentials", "other", "mfa_rejected", "paused"},
              str(reasons))
        check("failed sign-ins carry the masked address that was tried",
              all(e["properties"].get("account", "").count("*") >= 1 for e in failed),
              str([e["properties"].get("account") for e in failed][:4]))
        later_fail = [e for e in failed if e["properties"].get("reason") == "paused"]
        later_ok = [e for e in connected if e["properties"].get("account", "").startswith("l")
                    and e["properties"]["account"].endswith("@example.com")]
        check("a failure and a later success by the same person share one id",
              later_fail and later_ok and later_fail[0]["distinct_id"] == later_ok[-1]["distinct_id"]
              and later_fail[0]["distinct_id"] != "anonymous",
              f"{[e['distinct_id'][:8] for e in later_fail]} {[e['distinct_id'][:8] for e in later_ok]}")
        # The address travels in its own field, masked and in full, and
        # nowhere else; no token, password or blob ever does. Every address
        # the suite signs in with is listed so a regression on any path shows.
        full_addresses = ("someone@example.com", "friend@example.com",
                          "spray@example.com", "gatecrasher@example.com", MFA_EMAIL,
                          "later@example.com", WRONG_PASSWORD_EMAIL, RATE_LIMITED_EMAIL,
                          BLOCKED_EMAIL)
        check("a sign-in and a failure each name who, in full",
              all("@" in e["properties"].get("email", "") and "*" not in e["properties"]["email"]
                  for e in connected + failed),
              str([e["properties"].get("email") for e in connected + failed][:4]))
        check("a first use names who where the email was kept, and only the masked form where not",
              sorted(e["properties"].get("email", "-") for e in first_calls) == ["-", "alice@example.com"],
              str([e["properties"] for e in first_calls]))
        without_email = json.dumps([
            {**e, "properties": {k: v for k, v in e["properties"].items() if k != "email"}}
            for e in SENT
        ])
        blob = json.dumps(SENT)
        check("the full address is only ever in its own field",
              all(e["properties"].get("account", "").count("*") >= 1
                  for e in connected + first_calls)
              and not any(addr in without_email for addr in full_addresses))
        check("no token, password or Garmin session ever reaches analytics",
              alice not in blob and bob not in blob
              and SECRET not in blob and "their-own-password" not in blob
              and "hunter2" not in blob and "-token" not in without_email)

        # Kept on the server too, so whoever runs it can get in touch, but
        # encrypted like the token: the database file never holds it in clear.
        later_token = next((t for t in store.tokens_for(store.email_fingerprint("later@example.com"))), None)
        check("the email is kept with the connection and can be read back",
              later_token and store.email_for(later_token) == "later@example.com")
        raw = b"".join(p.read_bytes() for p in DB.parent.glob(DB.name + "*"))
        check("the database never holds an email in clear",
              not any(addr.encode() in raw for addr in full_addresses + ("alice@example.com",)))
        check("analytics build no person profile",
              all(e["properties"].get("$process_person_profile") is False for e in SENT))
        check("analytics stay silent without a key",
              analytics.enabled() and (lambda: (
                  setattr(analytics, "KEY", ""), analytics.capture("x", None, {}),
                  setattr(analytics, "KEY", "phc_test_key"), True))()[-1]
              and not any(e["event"] == "x" for e in SENT))

        # -- connect once means it stays connected ------------------------
        # garminconnect renews its token in place mid-session; the renewed one
        # must reach the store, or a restart resumes from a retired token.
        saved: list[str] = []
        renewing = GarminSession(
            tokenstore=json.dumps({"di_token": "renew-token"}), on_refresh=saved.append,
        )
        renewing.run(lambda c: c.get_user_profile())
        after_login = len(saved)
        renewing.run(lambda c: c.get_user_profile())
        check("an unchanged token is not saved again", len(saved) == after_login, str(len(saved)))
        renewing._client._marker = "renewed-token"
        renewing.run(lambda c: c.get_user_profile())
        check("a token renewed mid-session is saved",
              bool(saved) and "renewed-token" in saved[-1], str(saved[-1:]))

        # Someone away for weeks keeps their Garmin session: the keep-alive
        # renews it without counting them as having used the connector.
        idle = store.save_user(
            "i***@example.com", json.dumps({"di_token": "idle-token"}),
            email_hash=store.email_fingerprint("idle@example.com"), email="idle@example.com",
        )
        month_ago = int(time.time()) - 30 * 86400
        with __import__("sqlite3").connect(DB) as conn:
            conn.execute("UPDATE users SET created_at = ?, last_seen_at = ? WHERE user_token = ?",
                         (month_ago, month_ago, idle))
        due = store.idle_users(hosted.KEEP_ALIVE_AFTER)
        check("an idle connection is due a keep-alive", idle in due, str(len(due)))
        check("a connection in use is not", alice not in due)
        counts = await anyio_run(lambda: hosted.keep_alive_once(spacing=0))
        check("the keep-alive renews idle sessions", counts["renewed"] >= 1 and counts["lapsed"] == 0, str(counts))
        with __import__("sqlite3").connect(DB) as conn:
            seen, kept = conn.execute(
                "SELECT last_seen_at, kept_alive_at FROM users WHERE user_token = ?", (idle,)
            ).fetchone()
        check("a keep-alive does not count as use", seen == month_ago, str(seen))
        check("a keep-alive is recorded", kept is not None and kept > month_ago)
        check("a kept-alive connection is not due again for a week",
              idle not in store.idle_users(hosted.KEEP_ALIVE_AFTER))

        # When Garmin refuses, say so: that person will need to sign in again.
        with __import__("sqlite3").connect(DB) as conn:
            conn.execute("UPDATE users SET kept_alive_at = 0 WHERE user_token = ?", (idle,))
        real_session = hosted.GarminSession

        class Refused(real_session):
            def run(self, fn):
                raise GarminConnectAuthenticationError("401")

        hosted.GarminSession = Refused
        try:
            before = len(SENT)
            counts = await anyio_run(lambda: hosted.keep_alive_once(spacing=0))
        finally:
            hosted.GarminSession = real_session
        lapsed = [e for e in SENT[before:] if e["event"] == "garmin_session_lapsed"]
        check("a lapsed Garmin session is counted", counts["lapsed"] >= 1, str(counts))
        check("a lapsed Garmin session is reported with who it was",
              any(e["properties"].get("email") == "idle@example.com" for e in lapsed), str(lapsed)[:200])
        check("a lapsed session is kept, not deleted", store.get_user(idle) is not None)

        # In use, a session Garmin refuses says how to fix it from the phone,
        # not "sign in again in a terminal".
        def refusing_client(*, prompt_mfa, email=None, password=None):
            class Dead(FakeGarmin):
                def login(self, tokenstore=None):
                    raise GarminConnectAuthenticationError("401 Unauthorized")
            return Dead()

        real_build = session_mod.build_client
        session_mod.build_client = refusing_client
        hosted._SESSIONS.pop(idle, None)
        hosted._LAPSE_REPORTED.clear()
        before = len(SENT)
        try:
            message = ""
            try:
                hosted.session_for(idle).run(lambda c: c.get_user_profile())
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
            # A second call the same day must not report again.
            with contextlib.suppress(Exception):
                hosted.session_for(idle).run(lambda c: c.get_user_profile())
        finally:
            session_mod.build_client = real_build
            hosted._SESSIONS.pop(idle, None)
        check("a refused session explains how to reconnect",
              "Connect" in message and "terminal" not in message, message[:120])
        in_use = [e for e in SENT[before:] if e["event"] == "garmin_session_lapsed"]
        check("a session refused in use is reported once",
              len(in_use) == 1 and in_use[0]["properties"].get("source") == "in_use", str(len(in_use)))

    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
