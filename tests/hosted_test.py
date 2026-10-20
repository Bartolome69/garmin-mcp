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
from garmin_mcp import analytics, hooks  # noqa: E402
from garmin_mcp import server as garmin_server  # noqa: E402

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
# Garmin taking its time, the way a real sign-in does.
SLOW_EMAIL = "slow@example.com"
SLOW_EMAIL_2 = "tardy@example.com"
# Addresses the fake refuses, each the way the real library would: the
# exception class is what the server classifies on first.
WRONG_PASSWORD_EMAIL = "wrongpw@example.com"
RATE_LIMITED_EMAIL = "toomany@example.com"
BLOCKED_EMAIL = "blocked@example.com"
# A wrong password, then Garmin pausing the account: refused outright from
# then on, wherever the attempt comes from (Tom, Oct 13).
LOCKED_EMAIL = "lockedout@example.com"
LOCKED_TRIES: list[str] = []
# Through the sign-in proxy: refused from the first address it is given, fine
# from the next.
REFUSED_ONCE_EMAIL = "refusedonce@example.com"
# Where each sign-in and code step went out from, as the client saw it.
EGRESS: list[tuple[str, str]] = []


def seen_proxy() -> str:
    """The proxy a connection opened here by garminconnect would use.

    Asked of both libraries it connects with; they must agree.
    """
    import garminconnect.client as gcc
    plain = gcc.requests.Session()
    via = plain.proxies.get("https", "") if not plain.trust_env else ""
    cffi = gcc.cffi_requests.Session()
    try:
        assert (cffi.proxies or {}).get("https", "") == via, "curl_cffi and requests disagree"
    finally:
        cffi.close()
    return via


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
            via = seen_proxy()
            if not tokenstore:
                EGRESS.append((email, via))
            if "broken-proxy" in via:
                # How the library reports a proxy that is down: every strategy
                # fails, and the last one's requests error names the proxy.
                raise GarminConnectConnectionError(
                    "All login strategies exhausted: GarminConnectConnectionError: "
                    "HTTPSConnectionPool(host='sso.garmin.com', port=443): Max retries "
                    "exceeded (Caused by ProxyError('Unable to connect to proxy', "
                    "OSError('Tunnel connection failed: 403 Forbidden')))"
                )
            if email == REFUSED_ONCE_EMAIL and [e for e, _ in EGRESS].count(email) == 1:
                raise GarminConnectConnectionError(
                    "Login failed: 403 Client Error: Forbidden for url: "
                    "https://sso.garmin.com/sso/signin"
                )
            if email == LOCKED_EMAIL:
                LOCKED_TRIES.append(via)
                if len(LOCKED_TRIES) == 1:
                    raise GarminConnectAuthenticationError(
                        "Authentication failed (401 Unauthorized). Possible causes: ..."
                    )
                raise GarminConnectConnectionError(
                    "Login failed: 403 Client Error: Forbidden for url: "
                    "https://sso.garmin.com/sso/signin"
                )
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
            if email in (SLOW_EMAIL, SLOW_EMAIL_2):
                time.sleep(1.5)
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

        def resume_login(self, state, code):
            EGRESS.append((f"code:{email}", seen_proxy()))
            self.full_name = f"account:{email}-token"
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
                listed = (await sess.list_tools()).tools
                tools_seen = {t.name for t in listed}
        check("tools served over http", "get_daily_summary" in (tools_seen or set()),
              f"{len(tools_seen or [])} tools")
        # Claude groups read-only tools apart from writing ones, so a person
        # can allow every read at once and still be asked before a change.
        hints = {t.name: t.annotations for t in listed}
        check("every tool says whether it only reads",
              all(a is not None and a.read_only_hint is not None for a in hints.values()))
        check("reads are marked read-only, writes are not",
              hints["get_daily_summary"].read_only_hint and hints["get_plan"].read_only_hint
              and not hints["schedule_workout"].read_only_hint
              and not hints["create_plan"].read_only_hint)
        # "How's my training going" once went to recovery and fitness: Claude
        # searches tools by wording, so get_plan has to carry the words.
        plan_tool = next(t for t in listed if t.name == "get_plan")
        check("a general training question routes to the plan card",
              "how's my training going" in plan_tool.description
              and "get_plan first" in hosted.mcp.instructions
              and plan_tool.title == "Training plan and progress",
              str(plan_tool.title))
        workout_tool = next(t for t in listed if t.name == "create_workout")
        check("only the work reps carry a target by default",
              "only the work reps carry a target" in workout_tool.description
              and "heart-rate ceiling" not in workout_tool.description)
        check("deleting is marked destructive, creating is not",
              hints["delete_workout"].destructive_hint and not hints["create_workout"].destructive_hint)

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
            check("everyone gets shoes and recovery trends, list or not",
                  {"get_shoes", "get_recovery_trends"} <= names_a & names_b, str(sorted(names_b)))
            # Nothing is in preview now, but the list must still be read right
            # for the next thing that is.
            status_a_on = await call(alice, "get_connection_status")
            status_b_on = await call(bob, "get_connection_status")
            check("an account on the preview list is still recognised",
                  status_a_on.get("preview_features") == "on" and "preview_features" not in status_b_on,
                  f"{status_a_on.get('preview_features')} {status_b_on.get('preview_features')}")
            os.environ["GARMIN_MCP_PREVIEW"] = "on"
            check("'on' switches it on for everyone",
                  (await call(bob, "get_connection_status")).get("preview_features") == "on")
            os.environ.pop("GARMIN_MCP_PREVIEW")
            check("unset, nobody gets it",
                  "preview_features" not in await call(alice, "get_connection_status"))
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
        # A slip like "@gmail.con" passes the browser's own check; the form
        # offers the likely address (behaviour checked in a browser by hand).
        check("the sign-in form can suggest a mistyped email domain",
              'id=email-hint' in body and '"gmail.com"' in body and "Did you mean" in body)

        # Both pages ask for a Garmin password, so both have to say what happens
        # to it. These are f-strings interpolating PRIVACY_URL: a stray brace or
        # a missing prefix renders the placeholder as literal text, which no
        # other check would notice.
        for label, page_body in (("landing page", landing), ("sign-in form", body)):
            check(f"{label} links the privacy policy",
                  hosted.PRIVACY_URL in page_body and "{PRIVACY_URL}" not in page_body)
        # Someone sent only the connector address has never seen the website.
        check("the sign-in form links the guide to what to ask",
              hosted.GUIDE_URL in body and "{GUIDE_URL}" not in body)

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

        # Connect pressed three times while Garmin is still answering the first:
        # one login, and every press gets the same result.
        hosted._ATTEMPTS.clear()
        before = len(SIGNIN_CREDS)
        import concurrent.futures as _cf

        def press(_):
            return signin(SLOW_EMAIL)

        with _cf.ThreadPoolExecutor(3) as pool:
            futures = []
            for i in range(3):
                futures.append(pool.submit(press, i))
                time.sleep(0.2)
            presses = [f.result() for f in futures]
        urls = {m.group(1) for _, b in presses if (m := re.search(r"/u/([A-Za-z0-9_-]{16,})/mcp", b))}
        check("pressing Connect again while signing in sends Garmin one login",
              len(SIGNIN_CREDS) - before == 1, str(SIGNIN_CREDS[before:]))
        check("every press gets the same connector URL",
              all(c == 200 for c, _ in presses) and len(urls) == 1, str([c for c, _ in presses]))
        check("nothing is held once the sign-in finishes", not hosted._IN_FLIGHT)
        code, _ = await anyio_run(signin, SLOW_EMAIL)
        check("pressing again after an answer is a fresh sign-in",
              code == 200 and len(SIGNIN_CREDS) - before == 2)
        # A sign-in slower than the hold starts its page early and keeps the
        # connection busy with spaces, so Fly's 60 second limit never cuts it.
        hold, keepalive = hosted.SIGNIN_HOLD, hosted.SIGNIN_KEEPALIVE
        hosted.SIGNIN_HOLD, hosted.SIGNIN_KEEPALIVE = 0.3, 0.2
        try:
            code, body = await anyio_run(signin, SLOW_EMAIL)
        finally:
            hosted.SIGNIN_HOLD, hosted.SIGNIN_KEEPALIVE = hold, keepalive
        check("a slow sign-in starts its page early and still ends on the result",
              code == 200 and body.startswith(" ") and body.lstrip().startswith("<!doctype html>")
              and bool(re.search(r"/u/([A-Za-z0-9_-]{16,})/mcp", body)),
              f"{code} {body[:40]!r}")
        check("a fast sign-in is answered as before, nothing sent ahead of the page",
              hosted.SIGNIN_HOLD >= 15 and (await anyio_run(signin, "fast@example.com"))[1].startswith("<!doctype"))
        code, body = await anyio_run(fetch, "/connect")
        check("the sign-in form says it's working once pressed",
              'data-wait="Connecting to Garmin' in body and "please don&rsquo;t press again" in body
              and "f.dataset.sent" in body and "data-later=" in body)

        # A sign-in that stops for a code parks the client in memory for ten
        # minutes. garminconnect only drops the password on the clean path, so
        # without scrubbing it here the plaintext sat in that dict the whole
        # time — while the page promised it was not kept.
        hosted._ATTEMPTS.clear()
        hosted._PENDING.clear()
        code, body = await anyio_run(signin, MFA_EMAIL)
        check("a sign-in needing a code asks for one",
              code == 200 and "action=/mfa" in body and "text message or email" in body,
              f"status {code}")
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
        check("a wrong password shows the email as typed, to make a slip plain",
              f"Email used: <strong>{WRONG_PASSWORD_EMAIL}</strong>" in body)
        wrong = [e for e in SENT if e["event"] == "sign_in_failed"
                 and e["properties"].get("email") == WRONG_PASSWORD_EMAIL]
        check("a failed sign-in reports what failed, by type, never by message",
              wrong and wrong[-1]["properties"].get("error_type") == "GarminConnectAuthenticationError"
              and "Possible causes" not in json.dumps(wrong[-1]),
              str(wrong[-1]["properties"] if wrong else None))
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
        check("a network block apologises and says when to try again, not to find the operator",
              code == 400 and "turned this sign-in away" in body and "try again" in body
              and "whoever runs" not in body, f"status {code}")
        check("a network block pauses sign-ins too", hosted._pause_left() > 0)
        hosted._paused_until = 0.0

        # Through the sign-in proxy, every attempt gets an address of its own,
        # a refusal is tried once more from a new one, and a proxy that is
        # down falls back to the server's own address rather than failing.
        proxy_user = "http://user:secret_country-gb_session-AAAAAAAA_lifetime-30m"
        # "Directly" leaves whatever proxy settings the process already had.
        proxy_keys = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
        ambient = {k: os.environ.get(k) for k in proxy_keys}
        os.environ["GARMIN_MCP_LOGIN_PROXY"] = proxy_user + "@geo.example.net:12321"
        try:
            sessions = {re.search(r"_session-(\w+)", hosted.login_proxy()).group(1)
                        for _ in range(5)}
            check("each sign-in gets a fresh proxy session",
                  len(sessions) == 5 and "AAAAAAAA" not in sessions, str(sessions))
            check("the rest of the proxy address is kept as written",
                  hosted.login_proxy().endswith("_lifetime-30m@geo.example.net:12321"))

            hosted._ATTEMPTS.clear()
            EGRESS.clear()
            code, body = await anyio_run(signin, REFUSED_ONCE_EMAIL)
            tried = [via for e, via in EGRESS if e == REFUSED_ONCE_EMAIL]
            check("a refusal through the proxy is retried once from a new address",
                  code == 200 and "/mcp" in body and len(tried) == 2
                  and all("geo.example.net" in v for v in tried) and tried[0] != tried[1],
                  f"status {code} {len(tried)}")
            check("a refusal through the proxy pauses nobody", hosted._pause_left() == 0)

            EGRESS.clear()
            code, body = await anyio_run(signin, BLOCKED_EMAIL)
            tried = [via for e, via in EGRESS if e == BLOCKED_EMAIL]
            n = hosted.SIGNIN_TRIES
            check("refusals are tried from each address allowed, then stop",
                  code == 400 and len(tried) == n and len(set(tried)) == n, f"status {code} {len(tried)}")
            check("refused every time through the proxy, the page says so and claims no pause",
                  "turned this sign-in away" in body and f"{n - 1} other addresses" in body
                  and "paused" not in body and hosted._pause_left() == 0)

            # The person is waiting on the page: no new attempt once the time is up.
            budget = hosted.SIGNIN_BUDGET
            hosted.SIGNIN_BUDGET = 0
            try:
                EGRESS.clear()
                code, body = await anyio_run(signin, BLOCKED_EMAIL)
                check("no retry starts once the time budget is spent",
                      code == 400 and len(EGRESS) == 1 and "tried again" not in body,
                      f"status {code} {len(EGRESS)}")
            finally:
                hosted.SIGNIN_BUDGET = budget

            EGRESS.clear()
            code, body = await anyio_run(signin, RATE_LIMITED_EMAIL)
            check("a rate limit through the proxy says when to retry, and pauses nobody",
                  code == 400 and "fifteen minutes" in body and "paused" not in body
                  and hosted._pause_left() == 0 and len(EGRESS) == hosted.SIGNIN_TRIES, f"status {code}")

            EGRESS.clear()
            code, body = await anyio_run(signin, WRONG_PASSWORD_EMAIL)
            check("a wrong password is not retried", code == 400 and len(EGRESS) == 1, str(len(EGRESS)))
            check("a wrong password says how to check or reset it",
                  "Forgot password" in body and "pauses an account" in body)

            # After wrong passwords Garmin pauses the account, and its refusal
            # must read as that: not "it is not your password", not a retry
            # from other addresses (which only prolongs the pause), and not a
            # pause for everyone else.
            LOCKED_TRIES.clear()
            sent_before = len(SENT)
            code, body = await anyio_run(signin, LOCKED_EMAIL)
            code, body = await anyio_run(signin, LOCKED_EMAIL)
            reasons = [e["properties"].get("reason") for e in SENT[sent_before:]
                       if e["event"] == "sign_in_failed"]
            check("a refusal right after a wrong password reads as the account paused",
                  code == 400 and "paused sign-ins to your account" in body
                  and "not your password" not in body.lower(), f"status {code}")
            check("a paused account is not retried from other addresses, and pauses nobody",
                  len(LOCKED_TRIES) == 2 and hosted._pause_left() == 0, str(LOCKED_TRIES))
            check("a paused account is reported as such",
                  reasons == ["bad_credentials", "account_paused"], str(reasons))
            hosted._WRONG_PASSWORD.clear()
            code, body = await anyio_run(signin, BLOCKED_EMAIL)
            check("without a recent wrong password, a refusal still reads as a refusal",
                  "turned this sign-in away" in body)
            hosted._ATTEMPTS.clear()

            # The code step must leave from the address that started the sign-in.
            hosted._PENDING.clear()
            EGRESS.clear()
            code, body = await anyio_run(signin, MFA_EMAIL)
            pending_id = re.search(r'name=pending value="([^"]+)"', body).group(1)
            code, body = await anyio_run(post, "/mfa", {"pending": pending_id, "code": "123456"})
            check("the code step goes out from the same proxy address as the sign-in",
                  code == 200 and len(EGRESS) == 2 and EGRESS[0][1] == EGRESS[1][1]
                  and "geo.example.net" in EGRESS[1][1], f"status {code} {EGRESS}")

            os.environ["GARMIN_MCP_LOGIN_PROXY"] = proxy_user + "@broken-proxy.example.net:12321"
            hosted._ATTEMPTS.clear()
            EGRESS.clear()
            code, body = await anyio_run(signin, "someone@example.com")
            check("a proxy that is down gets a second address, then connects directly",
                  code == 200 and "/mcp" in body and len(EGRESS) == 3
                  and "broken-proxy" in EGRESS[0][1] and "broken-proxy" in EGRESS[1][1]
                  and EGRESS[0][1] != EGRESS[1][1] and EGRESS[-1][1] == "",
                  f"status {code} {EGRESS}")

            # One home connection in the pool being offline is normal: the
            # next address is tried, and the server's own address stays unused.
            os.environ["GARMIN_MCP_LOGIN_PROXY"] = proxy_user + "@geo.example.net:12321"
            real_try_proxy = hosted.login_proxy
            handed = iter([proxy_user.replace("AAAAAAAA", "dead0001") + "@broken-proxy.example.net:12321"])
            hosted.login_proxy = lambda: next(handed, None) or real_try_proxy()
            try:
                EGRESS.clear()
                code, body = await anyio_run(signin, "someone@example.com")
            finally:
                hosted.login_proxy = real_try_proxy
            check("one address that doesn't answer is replaced by another, not by going direct",
                  code == 200 and len(EGRESS) == 2 and "geo.example.net" in EGRESS[1][1],
                  f"status {code} {EGRESS}")
            check("a proxy failure is told apart from Garmin refusing",
                  hosted._failure_reason(GarminConnectConnectionError(
                      "Caused by ProxyError('Unable to connect to proxy', "
                      "OSError('Tunnel connection failed: 403 Forbidden'))")) == "proxy_failed"
                  and hosted._failure_reason(GarminConnectConnectionError(
                      "curl: (56) CONNECT tunnel failed, response 407")) == "proxy_failed")

            # Sign-ins run side by side, each through its own address, and
            # nothing else the server opens meanwhile goes through either.
            os.environ["GARMIN_MCP_LOGIN_PROXY"] = proxy_user + "@geo.example.net:12321"
            hosted._ATTEMPTS.clear()
            EGRESS.clear()
            import concurrent.futures as _cf
            began = time.monotonic()
            with _cf.ThreadPoolExecutor(2) as pool:
                both = [pool.submit(signin, e) for e in (SLOW_EMAIL, SLOW_EMAIL_2)]
                time.sleep(0.5)
                meanwhile = seen_proxy()
                results = [f.result() for f in both]
            took = time.monotonic() - began
            seen = dict(EGRESS)
            check("two sign-ins at once run side by side, not one after the other",
                  all(c == 200 for c, _ in results) and took < 2.8, f"{took:.1f}s")
            check("each of two sign-ins at once goes out from its own address",
                  len(seen) == 2 and all("geo.example.net" in v for v in seen.values())
                  and seen[SLOW_EMAIL] != seen[SLOW_EMAIL_2], str(seen))
            check("a connection opened elsewhere during sign-ins goes direct", meanwhile == "")

            # Once Garmin's sign-in server refuses an address, the attempt
            # stops using it: no more requests, no deliberate waits. James's
            # first sign-in spent 71 seconds on one refused address.
            import garminconnect.client as gcc
            with hosted.login_egress(proxy_user + "@geo.example.net:12321"):
                hosted._note_refusal("https://sso.garmin.com/sso/signin", 403)
                began = time.monotonic()
                gcc.time.sleep(5)
                slept = time.monotonic() - began
                try:
                    gcc.requests.Session().get("http://127.0.0.1:9/never")
                    stopped = False
                except hosted._Refused as exc:
                    stopped = hosted._failure_reason(exc) == "blocked_by_garmin"
            check("a refused address skips the library's waits", slept < 0.5, f"{slept:.2f}s")
            check("a refused address makes no more requests, and reads as a refusal", stopped)
            with hosted.login_egress(proxy_user + "@geo.example.net:12321"):
                fresh = getattr(hosted._EGRESS, "refused", None) is None
                hosted._note_refusal("https://connectapi.garmin.com/x", 403)
                other_host = getattr(hosted._EGRESS, "refused", None) is None
            with hosted.login_egress(""):
                hosted._note_refusal("https://sso.garmin.com/x", 429)
                direct = getattr(hosted._EGRESS, "refused", None) is None
            check("each attempt starts fresh, and only the sign-in server or a proxy counts",
                  fresh and other_host and direct and getattr(hosted._EGRESS, "refused", None) is None)
            # The mobile sign-in is limited by Garmin per app, not per address,
            # and the library falls through to the web sign-in after it. Treating
            # its 429 as a refusal stopped every sign-in on Oct 12 and 13.
            with hosted.login_egress(proxy_user + "@geo.example.net:12321"):
                hosted._note_refusal("https://sso.garmin.com/mobile/api/login", 429)
                hosted._note_refusal("https://sso.garmin.com/mobile/api/login", 403)
                mobile_ok = getattr(hosted._EGRESS, "refused", None) is None
                try:
                    hosted._check_refused()
                except hosted._Refused:
                    mobile_ok = False
                hosted._note_refusal("https://sso.garmin.com/portal/api/login", 429)
                portal_counts = getattr(hosted._EGRESS, "refused", None) == 429
            check("a mobile sign-in limit lets the library go on to the web sign-in", mobile_ok)
            check("a refusal from the web sign-in still counts", portal_counts)
            # The web sign-in can also say 429 inside a 200 answer. The library
            # treats that as a refusal only after a 10 to 20 second wait per
            # method and browser, which is how a refused sign-in took a minute.
            class Answer:
                def __init__(self, text: str) -> None:
                    self.text = text

            limited = Answer(json.dumps({"error": {"status-code": "429"}}))
            with hosted.login_egress(proxy_user + "@geo.example.net:12321"):
                hosted._note_refusal("https://sso.garmin.com/mobile/api/login", 200, limited)
                mobile_body_ok = getattr(hosted._EGRESS, "refused", None) is None
                hosted._note_refusal("https://sso.garmin.com/portal/api/login", 200,
                                     Answer(json.dumps({"responseStatus": {"type": "INVALID_USERNAME_PASSWORD"},
                                                        "error": {"status-code": "401"}})))
                hosted._note_refusal("https://sso.garmin.com/portal/api/login", 200,
                                     Answer(json.dumps({"responseStatus": {"type": "MFA_REQUIRED"}})))
                hosted._note_refusal("https://sso.garmin.com/sso/signin", 200, Answer("<html>"))
                wrong_password_ok = getattr(hosted._EGRESS, "refused", None) is None
                hosted._note_refusal("https://sso.garmin.com/portal/api/login", 200, limited)
                body_counts = getattr(hosted._EGRESS, "refused", None) == 429
            check("a 429 inside the web sign-in's answer counts as a refusal",
                  body_counts and mobile_body_ok)
            check("a wrong password, a code request or the widget's own page is not a refusal",
                  wrong_password_ok)
            # A CAPTCHA demand or a challenge page where the portal's JSON
            # should be turns the address away too: one cost 44 seconds.
            challenged = []
            for body in (json.dumps({"responseStatus": {"type": "CAPTCHA_REQUIRED"}}), "<html>Just a moment</html>"):
                with hosted.login_egress(proxy_user + "@geo.example.net:12321"):
                    hosted._note_refusal("https://sso.garmin.com/portal/api/login", 200, Answer(body))
                    challenged.append(getattr(hosted._EGRESS, "refused", None))
            check("a CAPTCHA demand or a challenge page from the portal is a refusal",
                  challenged == [403, 403], str(challenged))
            # Garmin answers a wrong password with a 401, so the status alone
            # says nothing: a CAPTCHA demand with a 400 still counts, and the
            # wrong password still doesn't.
            with hosted.login_egress(proxy_user + "@geo.example.net:12321"):
                hosted._note_refusal("https://sso.garmin.com/portal/api/login", 401,
                                     Answer(json.dumps({"responseStatus": {"type": "INVALID_USERNAME_PASSWORD"}})))
                wrong_401_ok = getattr(hosted._EGRESS, "refused", None) is None
                hosted._note_refusal("https://sso.garmin.com/portal/api/login", 400,
                                     Answer(json.dumps({"responseStatus": {"type": "CAPTCHA_REQUIRED"}})))
                captcha_400 = getattr(hosted._EGRESS, "refused", None) == 403
            check("the portal's answer counts whatever its HTTP status",
                  wrong_401_ok and captcha_400, f"{wrong_401_ok} {captcha_400}")
            answers = [hosted._portal_answer(Answer(b)) for b in (
                json.dumps({"responseStatus": {"type": "CAPTCHA_REQUIRED"}}), "<html>",
                json.dumps({"responseStatus": {"type": "secret@example.com"}}), json.dumps([1]))]
            check("the portal's answer is read as Garmin's own code and nothing else",
                  answers == ["CAPTCHA_REQUIRED", "not_json", "unknown", "not_json"], str(answers))
            # Each attempt keeps a trace: how each sign-in method ended and how
            # long it took, and how long the library waited on purpose.
            class Methods:
                def _widget_web_login(self, email: str, password: str) -> None:
                    raise sys.modules["garminconnect"].GarminConnectTooManyRequestsError("Widget 429 secret@example.com")

                def _portal_web_login_cffi(self, email: str, password: str) -> None:
                    gcc.time.sleep(0.05)

                def _do_portal_web_login(self, sess: object, email: str, password: str) -> None:
                    hosted._note_refusal("https://sso.garmin.com/portal/api/login", 200,
                                         Answer(json.dumps({"responseStatus": {"type": "CAPTCHA_REQUIRED"}})))
                    raise sys.modules["garminconnect"].GarminConnectConnectionError("Portal login: CAPTCHA required")

            hosted._trace_methods(Methods)
            hosted._trace_methods(Methods)
            with hosted.login_egress(proxy_user + "@geo.example.net:12321") as trace:
                try:
                    Methods()._widget_web_login("secret@example.com", "pw")
                except Exception:
                    pass
                Methods()._portal_web_login_cffi("secret@example.com", "pw")
                try:
                    Methods()._do_portal_web_login(None, "secret@example.com", "pw")
                except Exception:
                    pass
            outcomes = [step.rsplit(":", 1)[0] for step in trace["steps"]]
            traced = hosted._traced([trace])
            check("each attempt's trace says how each method ended, once each",
                  outcomes == ["widget:429", "portal:ok", "portal_try:other/CAPTCHA_REQUIRED@200"], str(trace))
            check("the trace counts the library's waits, and carries nothing typed",
                  0.04 <= trace["waited"] < 0.5 and traced["waited_s"] == [round(trace["waited"], 1)]
                  and "secret" not in json.dumps(traced), str(traced))
            # The same through both libraries garminconnect connects with: a
            # local stand-in proxy answers 429 for the mobile sign-in and 200
            # for the web one. The mobile 429 must not stop the next method;
            # stopping there kept everyone out on Oct 12 and 13. This fails on
            # the code that caused that outage.
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            reached: list[str] = []

            class StandIn(BaseHTTPRequestHandler):
                def _answer(self) -> None:
                    reached.append(self.path)
                    self.send_response(429 if "/mobile/" in self.path else 200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

                do_GET = do_POST = _answer

                def log_message(self, *args: object) -> None:
                    pass

            stand_in = ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
            threading.Thread(target=stand_in.serve_forever, daemon=True).start()
            try:
                with hosted.login_egress(f"http://127.0.0.1:{stand_in.server_port}"):
                    codes = [
                        gcc.requests.Session().post("http://sso.garmin.com/mobile/api/login").status_code,
                        gcc.requests.Session().get("http://sso.garmin.com/portal/sso/en-US/sign-in").status_code,
                    ]
                    cffi = gcc.cffi_requests.Session()
                    try:
                        codes += [
                            cffi.post("http://sso.garmin.com/mobile/api/login").status_code,
                            cffi.get("http://sso.garmin.com/sso/embed").status_code,
                        ]
                    finally:
                        cffi.close()
            finally:
                stand_in.shutdown()
            check("a mobile sign-in 429 never stops the next method, in either library",
                  codes == [429, 200, 429, 200] and len(reached) == 4
                  and all(p.startswith("http://sso.garmin.com/") for p in reached),
                  f"{codes} {reached}")
            began = time.monotonic()
            gcc.time.sleep(0.2)
            check("outside a refused attempt the library still waits as written",
                  time.monotonic() - began >= 0.19)
        finally:
            os.environ.pop("GARMIN_MCP_LOGIN_PROXY", None)
            hosted._paused_until = 0.0
        check("no sign-in ever touches the process's proxy settings",
              {k: os.environ.get(k) for k in proxy_keys} == ambient)

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
                              "bad_credentials", "other", "mfa_rejected", "paused",
                              "account_paused"},
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
                          BLOCKED_EMAIL, REFUSED_ONCE_EMAIL, SLOW_EMAIL_2)
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
              and "hunter2" not in blob and "-token" not in without_email
              and "secret_country" not in blob and "geo.example.net" not in blob)
        retried = by_name.get("sign_in_retried", [])
        check("sign-ins report how many attempts they took and how long",
              any(e["properties"].get("attempts") == 2 for e in connected)
              and any(e["properties"].get("attempts") == hosted.SIGNIN_TRIES for e in failed)
              and all(isinstance(e["properties"].get("seconds"), float)
                      for e in connected + failed if "attempts" in e["properties"]),
              str([(e["properties"].get("attempts"), e["properties"].get("seconds"))
                   for e in connected][-4:]))
        check("a retried sign-in is reported with why and how",
              {(e["properties"].get("reason"), e["properties"].get("then")) for e in retried}
              >= {("blocked_by_garmin", "new_address"), ("proxy_failed", "new_address"),
                  ("proxy_failed", "direct")},
              str([e["properties"].get("reason") for e in retried]))

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

        # What's new: said once per person, on a good answer, and counted.
        news_user = store.save_user(
            "n***@example.com", json.dumps({"di_token": "news-token"}),
            email_hash=store.email_fingerprint("news@example.com"), email="news@example.com",
        )
        hosted._TOOL_REPORTED.clear()
        before = len(SENT)
        first_answer = await call(news_user, "get_connection_status")
        second_answer = await call(news_user, "get_connection_status")
        await call(news_user, "get_daily_summary", {"date": "2026-09-22"})
        await asyncio.sleep(0.3)  # events go out on their own threads
        events = SENT[before:]
        check("the what's-new note rides along with the first answer",
              hosted.NEWS in str(first_answer.get("whats_new")), str(first_answer)[:120])
        check("and is not said twice", "whats_new" not in second_answer)
        check("the permission tip waits for an answer without the what's-new note",
              "tip" not in first_answer and hosted.PERMISSION_TIP in str(second_answer.get("tip")),
              str(second_answer)[:120])
        third_answer = await call(news_user, "get_connection_status")
        check("and the tip is said once", "tip" not in third_answer)
        shown = [e for e in events if e["event"] == "whats_new_shown"]
        check("showing it is counted once, with who it was",
              len(shown) == 1 and shown[0]["properties"].get("email") == "news@example.com"
              and shown[0]["properties"].get("version") == hosted.NEWS_VERSION, str(shown)[:200])
        used = [e["properties"]["tool"] for e in events if e["event"] == "tool_used"]
        check("each tool is counted once a day, not once a call",
              sorted(used) == ["get_connection_status", "get_daily_summary"], str(used))
        timed = [e["properties"] for e in events if e["event"] == "tool_used"]
        check("each tool count says how long the tool took, and whether it was the first since a start",
              timed and all(isinstance(t.get("seconds"), float) and t["seconds"] >= 0
                            and isinstance(t.get("first_since_start"), bool) for t in timed)
              and [t["first_since_start"] for t in timed].count(True) == 1, str(timed))
        # The hour's timings go out once the hour is over, one row per tool,
        # with no one's id or address on them.
        hosted._TIMINGS.clear()
        hosted._TIMING_HOUR[0] = "2000-01-01T00"
        timing_hook = hooks.use(hosted._after_tool_for(news_user, store.get_user(news_user)))
        try:
            for took in (0.5, 1.5, 9.0):
                hooks.after_tool("get_plan", {"ok": 1}, took)
            hosted._TIMING_HOUR[0] = "2000-01-01T01"
            before = len(SENT)
            hooks.after_tool("get_readiness", {"ok": 1}, 0.2)
        finally:
            hooks.reset(timing_hook)
        for _ in range(40):
            rows = [e for e in SENT[before:] if e["event"] == "tool_timing"]
            if rows:
                break
            time.sleep(0.05)
        check("an hour's timings are sent once it is over, per tool, naming no one",
              len(rows) == 1 and rows[0]["properties"]["tool"] == "get_plan"
              and rows[0]["properties"]["calls"] == 3 and rows[0]["properties"]["median_s"] == 1.5
              and rows[0]["properties"]["max_s"] == 9.0 and rows[0]["distinct_id"] == "anonymous"
              and "@" not in json.dumps(rows[0]), str(rows))
        check("the next hour starts with only its own calls",
              list(hosted._TIMINGS) == ["get_readiness"], str(hosted._TIMINGS))
        tips = [e for e in events if e["event"] == "permission_tip_shown"]
        check("showing the tip is counted once", len(tips) == 1, str(len(tips)))
        # A general question gets the plan card alone: reads sent at the same
        # time as get_plan answer with a note, and the same read asked again
        # answers in full.
        async with streamable_http_client(f"{base}/u/{alice}/mcp") as (r, w):
            async with ClientSession(r, w) as sess:
                await sess.initialize()

                async def ask(tool: str) -> dict:
                    result = await sess.call_tool(tool, {})
                    return getattr(result, "structuredContent", None) or json.loads(result.content[0].text)

                started = time.monotonic()
                plan_answer, ready_answer, fit_answer = await asyncio.gather(
                    ask("get_plan"), ask("get_readiness"), ask("get_fitness"))
                together = time.monotonic() - started
                check("reads sent with get_plan answer with a note, not their data",
                      ready_answer == {"for_the_assistant": hosted.WITH_PLAN_NOTE}
                      and fit_answer == {"for_the_assistant": hosted.WITH_PLAN_NOTE},
                      str(ready_answer)[:160])
                check("and get_plan still draws the card",
                      "error" not in plan_answer and "for_the_assistant" in plan_answer,
                      str(plan_answer)[:160])
                check("held reads wait no longer than the settle", together < 10, f"{together:.1f}s")
                again = await ask("get_readiness")
                check("the same read asked again answers in full",
                      "for_the_assistant" not in again and "error" not in again, str(again)[:160])
                alone = await ask("get_fitness")
                check("and a read asked again after get_plan has answered goes through",
                      "for_the_assistant" not in alone, str(alone)[:160])
        for _ in range(40):
            held = [e["properties"]["tool"] for e in SENT if e["event"] == "tool_held"]
            if len(held) >= 2:
                break
            time.sleep(0.05)
        check("holding a read is counted, by tool", sorted(held) == ["get_fitness", "get_readiness"], str(held))
        # A read on its own, and one sent after get_plan has answered, are not held.
        hosted._HELD.clear()
        hosted._PLAN_CALLS[alice] = (time.monotonic() - 5, time.monotonic() - 2)
        check("a read sent after get_plan answered is not held",
              "for_the_assistant" not in await call(alice, "get_readiness"))
        hosted._PLAN_CALLS[alice] = (time.monotonic() - 300, None)
        check("a get_plan that never finished holds nothing",
              "for_the_assistant" not in await call(alice, "get_recovery_trends"))
        hosted._PLAN_CALLS.pop(alice, None)
        check("tool counts carry no Garmin data",
              all("12345" not in json.dumps(e) for e in events if e["event"] == "tool_used"))
        check("a new version is said again",
              store.claim_news(news_user, hosted.NEWS_VERSION + 1)
              and not store.claim_news(news_user, hosted.NEWS_VERSION + 1))
        with store._connect() as conn:
            conn.execute("UPDATE users SET news_seen = 0 WHERE user_token = ?", (news_user,))
        # A tool failing in a way it didn't expect is reported, by type only;
        # a tool answering "that won't work" is the tool working.
        hook = hosted._after_tool_for(news_user, store.get_user(news_user))
        before = len(SENT)
        hook("get_activities", {"error": "Garmin request failed (KeyError: 'secret-run-name'). "
                                         "If this persists, check connection status."})
        hook("schedule_workout", {"error": "workout_id must be numeric, got 'x'."})
        tool_failures = [e for e in SENT[before:] if e["event"] == "tool_failed"]
        check("an unexpected tool failure is reported once, by tool and type",
              len(tool_failures) == 1 and tool_failures[0]["properties"].get("tool") == "get_activities"
              and tool_failures[0]["properties"].get("error_type") == "KeyError"
              and "secret-run-name" not in json.dumps(tool_failures[0]),
              str(tool_failures))
        # The hourly test sign-in the server runs on its own clock: passes on
        # Garmin's "wrong email or password", fails otherwise, reported either
        # way, and never by anything but reasons and timings.
        before = len(SENT)
        passed = hosted.signin_check_once(WRONG_PASSWORD_EMAIL)
        refused = hosted.signin_check_once(BLOCKED_EMAIL)
        checks = [e for e in SENT[before:] if e["event"] == "sign_in_check"]
        check("the server's test sign-in passes on Garmin's password answer",
              passed["ok"] and passed["reasons"] == ["bad_credentials"], str(passed))
        check("the server's test sign-in fails, after three addresses, when Garmin refuses",
              not refused["ok"] and refused["tries"] == 3, str(refused))
        check("each test sign-in is reported, as live, and names no one",
              [e["properties"]["ok"] for e in checks] == [True, False]
              and all(e["properties"]["code"] == "live" for e in checks)
              and WRONG_PASSWORD_EMAIL not in json.dumps(checks), str(checks))
        # A failure is checked again in 15 minutes, once; then hourly again.
        waits = [hosted._signin_check_wait(True, False), hosted._signin_check_wait(False, False),
                 hosted._signin_check_wait(False, True), hosted._signin_check_wait(True, True)]
        check("a failed test sign-in is checked again soon, but only once",
              waits == [3600, 900, 3600, 3600], str(waits))
        check("a failed answer doesn't use the note up",
              hosted._after_tool_for(news_user, store.get_user(news_user))("x", {"error": "no"})
              == {"error": "no"} and store.claim_news(news_user, hosted.NEWS_VERSION))

        # Someone connecting for the first time hears nothing about what's new.
        hosted._ATTEMPTS.clear()
        code, body = await anyio_run(signin, "brandnew@example.com")
        fresh = re.search(r"/u/([A-Za-z0-9_-]{16,})/mcp", body)
        fresh_answer = await call(fresh.group(1), "get_connection_status") if fresh else {}
        check("a first sign-in skips the what's-new note", bool(fresh) and "whats_new" not in fresh_answer)
        check("but hears how to stop the permission prompts",
              hosted.PERMISSION_TIP in str(fresh_answer.get("tip")), str(fresh_answer)[:120])
        check("and where to find ideas of what to ask",
              hosted.GUIDE_URL in str(fresh_answer.get("tip")), str(fresh_answer)[:120])
        check("and how to see their training as a card",
              "How's my training going?" in str(fresh_answer.get("tip")), str(fresh_answer)[:120])
        check("and the model is told to find out where they are with the plan card alone",
              "get_plan alone" in str(fresh_answer.get("welcome")) and "situation" in str(fresh_answer.get("welcome")),
              str(fresh_answer.get("welcome"))[:120])
        check("the welcome is for the model, not to be read out", "not to repeat" in hosted.WELCOME)
        hosted._ATTEMPTS.clear()
        code, body = await anyio_run(signin, "brandnew@example.com")
        again = re.search(r"/u/([A-Za-z0-9_-]{16,})/mcp", body)
        repeat = await call(again.group(1), "get_connection_status") if again else {}
        check("signing in again doesn't repeat the tip, or the welcome",
              bool(again) and "tip" not in repeat and "welcome" not in repeat)

        # Update emails: only to people who haven't opted out, and one click
        # (plus a confirm, so a mail scanner can't do it) stops them.
        fingerprint = store.email_fingerprint("news@example.com")
        check("people with an address kept can be emailed",
              ("news@example.com", fingerprint) in store.emailable())
        link = hosted.unsubscribe_url(base, fingerprint)
        code, body = await anyio_run(fetch, link[len(base):])
        check("the unsubscribe link asks before acting",
              code == 200 and "Stop update emails" in body
              and ("news@example.com", fingerprint) in store.emailable(), str(code))
        code, body = await anyio_run(post, "/email/unsubscribe", {"u": fingerprint, "s": "0" * 24})
        check("a forged unsubscribe is refused",
              code == 400 and ("news@example.com", fingerprint) in store.emailable(), str(code))
        before = len(SENT)
        sig = store.unsubscribe_signature(fingerprint)
        code, body = await anyio_run(post, "/email/unsubscribe", {"u": fingerprint, "s": sig})
        await asyncio.sleep(0.3)
        check("confirming unsubscribes",
              code == 200 and "unsubscribed" in body
              and all(fp != fingerprint for _, fp in store.emailable()), str(code))
        check("unsubscribing is counted",
              any(e["event"] == "email_unsubscribed" for e in SENT[before:]))
        store.save_user("n***@example.com", json.dumps({"di_token": "news-2"}),
                        email_hash=fingerprint, email="news@example.com")
        check("signing in again keeps the opt-out and the note seen",
              all(fp != fingerprint for _, fp in store.emailable())
              and not store.claim_news(store.tokens_for(fingerprint)[-1], hosted.NEWS_VERSION))

        # Daash Crews. Three people of their own, so nothing above is touched:
        # Carol and Dave signed in with addresses kept, Bob's row predates that.
        from datetime import date as _date, timedelta as _td
        from garmin_mcp import crews
        carol = store.save_user("c***@example.com", json.dumps({"di_token": "carol"}),
                                email_hash=store.email_fingerprint("carol@example.com"), email="carol@example.com")
        dave = store.save_user("d***@example.com", json.dumps({"di_token": "dave"}),
                               email_hash=store.email_fingerprint("dave@example.com"), email="dave@example.com")

        async def tool_names(user_token: str) -> set[str]:
            async with streamable_http_client(f"{base}/u/{user_token}/mcp") as (r, w):
                async with ClientSession(r, w) as sess:
                    await sess.initialize()
                    return {t.name for t in (await sess.list_tools()).tools}

        check("the model is told to keep crew cards to crew questions, one card per answer",
              "One card per answer" in hosted.mcp.instructions and "never alongside get_plan" in hosted.mcp.instructions)
        async def notices(user_token: str, tool: str) -> list[str]:
            seen: list[str] = []

            async def note(message) -> None:
                root = getattr(message, "root", message)
                seen.append(type(root).__name__)

            async with streamable_http_client(f"{base}/u/{user_token}/mcp") as (r, w):
                async with ClientSession(r, w, message_handler=note) as sess:
                    await sess.initialize()
                    await sess.call_tool(tool, {})
            return seen

        garmin_server._TOLD.clear()
        first, second = await notices(dave, "get_connection_status"), await notices(dave, "get_connection_status")
        check("over HTTP, a plain tool's answer brings the host a re-list notice",
              any("ToolListChanged" in n for n in first), str(first))
        check("once per person, not on every call", not any("ToolListChanged" in n for n in second), str(second))

        names_before = await tool_names(carol)
        check("everyone on the hosted connector is offered every crew tool, in a crew or not",
              {"join_crew", "get_crew", "update_crew", "leave_crew"} <= names_before, str(sorted(names_before))[:200])
        outsider = await call(carol, "get_crew", {})
        check("asking for a crew before joining one says how to join",
              "not in a crew" in str(outsider.get("error")) and "code" in str(outsider.get("error")), str(outsider)[:160])
        started = await call(carol, "join_crew", {
            "your_name": "Carol", "crew_name": "Friday Run Club",
            "usual_times": ["weekday_evening", "weekend_morning"],
            "club_run_day": "fri", "club_run_time": "06:45", "club_run_km": 4.3,
        })
        code = (started.get("crew") or {}).get("code", "")
        check("starting a crew answers with its card and an invite code",
              re.fullmatch(r"DC-[A-Z2-9]{4}", code) is not None and started["just"]["action"] == "started"
              and started["club_run"]["time"] == "06:45" and started["club_run"]["km"] == 4.3, str(started)[:200])
        check("joining leaves the tool list as it was, so a chat that kept it still has the crew card",
              await tool_names(carol) == names_before)
        read_on = crews._connect().execute("SELECT updated_on FROM crew_members WHERE name = 'Carol'").fetchone()
        check("starting a crew reads your own week straight away, so your row isn't empty",
              read_on is not None and read_on[0] == _date.today().isoformat(), str(read_on))
        again = await call(carol, "join_crew", {"your_name": "Carol", "start_new_crew": True})
        check("one crew per person", "already in Friday Run Club" in str(again.get("error")), str(again)[:120])
        unnamed = await call(alice, "join_crew", {"your_name": "Al", "start_new_crew": True})
        check("a crew needs no name: it takes its starter's", unnamed.get("crew", {}).get("name") == "Al's crew", str(unnamed)[:120])
        await call(alice, "leave_crew", {"confirm": True})
        joined = await call(dave, "join_crew", {"your_name": "Dave", "code": code.lower().replace("-", " "),
                                                "usual_times": ["weekday_evening", "weekend_morning"]})
        check("joining by code, however it's typed", joined.get("just", {}).get("action") == "joined", str(joined)[:160])
        clash = await call(bob, "join_crew", {"your_name": "dave", "code": code})
        check("two members can't share a name", "already called dave" in str(clash.get("error")), str(clash)[:120])
        await call(bob, "join_crew", {"your_name": "Bob", "code": code, "usual_times": ["weekend_morning"]})
        nope = await call(alice, "get_crew")
        check("someone outside a crew is told how to join", "not in a crew" in str(nope.get("error")), str(nope)[:120])
        wrong = await call(alice, "join_crew", {"your_name": "Al", "code": "DC-ZZZZ"})
        check("a wrong code says so", "No crew has the code DC-ZZZZ" in str(wrong.get("error")), str(wrong)[:120])

        # Their weeks reach the crew when they look at their plans: the hook
        # reads each get_plan answer. Next week, so every day is ahead.
        today = _date.today()
        monday = today + _td(days=7 - today.weekday())

        def plan_for(runs, done_pace=None):
            weeks = [{"starts": (monday + _td(days=7 * i)).isoformat(), "sessions": []} for i in (-1, 0)]
            if done_pace:
                weeks[0]["sessions"].append({"date": (monday - _td(days=7)).isoformat(), "type": "easy",
                                             "status": "done", "actual_km": 8.0, "actual": done_pace})
            for offset, kind, km in runs:
                weeks[1]["sessions"].append({"date": (monday + _td(days=offset)).isoformat(), "type": kind,
                                             "status": "ahead", "planned_km": km, "sport": "run", "kind": "run"})
            return {"label": "X", "weeks": weeks}

        hosted._after_tool_for(carol, store.get_user(carol))(
            "get_plan", plan_for([(1, "easy", 8.0), (3, "threshold", 11.0), (6, "long", 18.0)], "45m 20s"))
        hosted._after_tool_for(dave, store.get_user(dave))(
            "get_plan", plan_for([(2, "easy", 10.0), (6, "long", 16.0)], "44m 00s"))
        hosted._after_tool_for(bob, store.get_user(bob))(
            "create_plan", plan_for([(2, "easy", 8.0), (6, "easy", 8.0)], "46m 40s"))
        card = await call(carol, "get_crew", {"week_of": monday.isoformat()})
        tok = crews.use(crews.Member(crews.member_key(carol, store.get_user(carol).email_hash)))
        try:
            on_sunday = crews.week(today=_date(2026, 10, 18))
            on_saturday = crews.week(today=_date(2026, 10, 17))
        finally:
            crews.reset(tok)
        check("on a Sunday the card shows the week ahead",
              on_sunday["week"]["starts"] == "2026-10-19" and on_saturday["week"]["starts"] == "2026-10-12",
              f"{on_sunday['week']} {on_saturday['week']}")
        people = {m["name"]: m for m in card.get("members", [])}
        check("no paces anywhere: a crew matches by day and time, never by speed",
              all("pace" not in m for m in card.get("members", []))
              and crews._connect().execute("SELECT COUNT(*) FROM crew_members WHERE pace_s IS NOT NULL").fetchone()[0] == 0,
              str(card.get("members", [])[:1]))
        def one(session):
            _, runs = crews.from_plan({"weeks": [{"starts": _date.today().isoformat(), "sessions": [
                {"date": _date.today().isoformat(), "status": "ahead", "planned_km": 18.0, **session}]}]}, _date.today())
            return runs[0][1:] if runs else None

        reps = [{"k": "easy", "km": 6.0}, {"k": "work", "km": 2.0}, {"k": "rec", "km": 0.4}, {"k": "work", "km": 2.0},
                {"k": "rec", "km": 0.4}, {"k": "work", "km": 2.0}, {"k": "easy", "km": 5.2}]
        check("a long run with reps in it is a hard run for the crew, run alone",
              one({"type": "long", "shape": reps}) == ("hard", None), str(one({"type": "long", "shape": reps})))
        strides = [{"k": "easy", "km": 8.0}, *[{"k": "work", "km": 0.1}, {"k": "rec", "km": 0.1}] * 6]
        check("but strides after an easy run leave it easy, and a steady long run stays long",
              one({"type": "easy", "shape": strides}) == ("easy", 18.0)
              and one({"type": "long", "shape": [{"k": "easy", "km": 18.0}]}) == ("long", 18.0)
              and one({"type": "long"}) == ("long", 18.0))
        check("a hard session shows only as hard",
              people["Carol"]["days"][3] == [{"kind": "hard"}], str(people["Carol"]["days"][3]))
        together = card.get("together", [])
        check("the club run is on the week for whoever goes, and is not a match",
              people["Bob"]["days"][4][0]["kind"] == "club" and people["Carol"]["days"][4][0]["kind"] == "club"
              and not [t for t in together if t["date"] == (monday + _td(days=4)).isoformat()]
              and card["club_run"]["day_name"] == "Friday", str(together))
        sunday = [t for t in together if t["date"] == (monday + _td(days=6)).isoformat()]
        check("Sunday's long runs and an easy run match, at the time they share",
              sunday and sorted(sunday[0]["who"]) == ["Bob", "Carol", "Dave"] and sunday[0]["kind"] == "mixed"
              and sunday[0]["when"] == "morning" and (sunday[0]["km_lo"], sunday[0]["km_hi"]) == (8.0, 18.0),
              str(sunday))
        check("nobody's run is moved to make a match: Carol's Tuesday stays alone",
              all("move_from" not in t for t in together)
              and not [t for t in together if t["date"] == (monday + _td(days=1)).isoformat()], str(together))
        check("Bob, who only runs weekend mornings, isn't put on a weekday evening",
              all("Bob" not in t["who"] for t in together if t["kind"] == "easy"), str(together))
        msg = card.get("group_message", "")
        check("the week comes as a calendar for the group chat",
              msg.startswith("Friday Run Club · ") and "Tue: Carol easy 8k" in msg and "Thu: Carol hard" in msg
              and "Fri: club run 6:45am (Carol, Dave, Bob)" in msg
              and "Sun: Carol long 18k, Dave long 16k, Bob easy 8k → together, morning" in msg
              and code in msg, msg)
        odd = await call(bob, "update_crew", {"usual_times": ["night"]})
        check("usual times are mornings, afternoons or evenings", "mornings, afternoons or evenings" in str(odd.get("error")), str(odd)[:120])
        both = await call(bob, "update_crew", {"usual_times": ["mornings", "weekend afternoons", "lunch"]})
        check("'mornings' alone means weekday and weekend, and lunch is an afternoon", both.get("changed") == ["your usual times"]
              and (await call(bob, "get_crew", {"week_of": monday.isoformat()}))["members"][2]["times"]
              == "weekday mornings, weekday afternoons, weekend mornings, weekend afternoons")
        # Dave runs Sunday afternoons now: Carol's long run stays in the morning, so they no longer line up.
        await call(dave, "update_crew", {"usual_times": ["weekday_evening", "weekend_afternoon"]})
        later = await call(carol, "get_crew", {"week_of": monday.isoformat()})
        sun_later = [t for t in later["together"] if t["date"] == (monday + _td(days=6)).isoformat()]
        check("an afternoon runner isn't matched with the morning runners",
              sun_later and "Dave" not in sun_later[0]["who"] and sorted(sun_later[0]["who"]) == ["Bob", "Carol"], str(sun_later))
        await call(dave, "update_crew", {"usual_times": ["weekday_evening", "weekend_morning"]})
        check("only easy and long runs, never what was done, are kept",
              {k for k, in crews._connect().execute("SELECT DISTINCT kind FROM crew_runs").fetchall()} <= {"easy", "long", "hard"}
              and crews._connect().execute("SELECT COUNT(*) FROM crew_runs WHERE day < ?", (today.isoformat(),)).fetchone()[0] == 0)
        crews._connect().execute("INSERT INTO crew_runs (member, day, kind, km) VALUES ('x', '2020-01-01', 'easy', 5)").connection.commit()
        check("days that have passed are deleted", crews.purge_past() >= 1)

        # The crew's own page, for anyone with its link.
        def fetch(path):
            req = urllib.request.Request(f"{base}{path}")
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return r.status, dict(r.headers), r.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read().decode()

        before_link = await call(carol, "get_crew", {})
        dave_card = await call(dave, "get_crew", {})
        check("the crew's starter is told, in the card itself, how to turn the page on",
              'group_link "on"' in before_link.get("for_the_assistant", "")
              and "group_link" not in dave_card.get("for_the_assistant", ""), before_link.get("for_the_assistant", "")[-200:])
        not_starter = await call(dave, "update_crew", {"group_link": "on", "confirm": True})
        check("only whoever started the crew turns its page link on", "Only whoever started" in str(not_starter.get("error")), str(not_starter)[:120])
        asked = await call(carol, "update_crew", {"group_link": "on"})
        check("turning the link on asks the user first, saying who can see what",
              "needs_confirmation" in asked and "first names" in asked["needs_confirmation"]
              and crews._connect().execute("SELECT link FROM crews WHERE code = ?", (code,)).fetchone()[0] is None, str(asked)[:160])
        on = await call(carol, "update_crew", {"group_link": "on", "confirm": True})
        page_url = on.get("page_url", "")
        path = "/crew/" + page_url.rsplit("/crew/", 1)[-1]
        check("once agreed, the link is long and random", on.get("group_link") == "on"
              and re.fullmatch(r"/crew/[A-Za-z0-9_-]{24}", path) is not None, str(on))
        status, headers, body = await anyio_run(fetch, path)
        check("the page shows the crew's week", status == 200 and "__CREW_PAGE__" in body and "Carol" in body and "Friday Run Club" in body, str(status))
        headers = {k.lower(): v for k, v in headers.items()}
        check("kept out of search engines, caches and referrers",
              "noindex" in headers.get("x-robots-tag", "") and headers.get("cache-control") == "no-store"
              and headers.get("referrer-policy") == "no-referrer" and "noindex" in body
              and "frame-ancestors 'none'" in headers.get("content-security-policy", ""), str(headers)[:200])
        check("the page doesn't give away the invite code, or call anyone 'you'",
              code not in body and '"you": true' not in body, code)
        # The test's monday is next week's.
        status, _, raw = await anyio_run(fetch, path + "?format=json&week_of=" + monday.isoformat())
        nxt = json.loads(raw) if status == 200 else {}
        check("the page switches to next week by itself",
              nxt.get("week", {}).get("starts") == monday.isoformat() and "code" not in nxt.get("crew", {}), str(nxt)[:160])
        status, _, raw = await anyio_run(fetch, path + "?format=json&week_of=2030-01-07")
        check("and no further than the two weeks the crew keeps",
              status == 200 and json.loads(raw)["week"]["starts"] == monday.isoformat())
        seen = await call(dave, "get_crew", {"week_of": monday.isoformat()})
        check("every member's card shows the page is on, with its link",
              seen.get("crew", {}).get("page_url") == page_url and "link" not in seen.get("crew", {})
              and page_url in seen.get("for_the_assistant", ""), str(seen.get("crew")))
        renewed = await call(carol, "update_crew", {"group_link": "new", "confirm": True})
        old_status = (await anyio_run(fetch, path))[0]
        new_path = "/crew/" + renewed.get("page_url", "").rsplit("/crew/", 1)[-1]
        check("a new link replaces the old, which stops working",
              old_status == 404 and new_path != path and (await anyio_run(fetch, new_path))[0] == 200, f"{old_status} {renewed}")
        off = await call(carol, "update_crew", {"group_link": "off"})
        check("and the link can be turned off, without asking",
              off.get("group_link") == "off" and (await anyio_run(fetch, new_path))[0] == 404
              and "page_url" not in (await call(dave, "get_crew", {}))["crew"], str(off))
        check("a made-up link finds nothing", (await anyio_run(fetch, "/crew/" + "A" * 24))[0] == 404
              and (await anyio_run(fetch, "/crew/short"))[0] == 404)
        await call(carol, "update_crew", {"group_link": "on", "confirm": True})

        # People without Daash add their easy runs on the page.
        link = crews._connect().execute("SELECT link FROM crews WHERE code = ?", (code,)).fetchone()[0]

        def runner_post(body, content_type="application/json"):
            req = urllib.request.Request(f"{base}/crew/{link}/runner", data=json.dumps(body).encode(),
                                         headers={"Content-Type": content_type}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return r.status, json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode() or "{}")

        async def page_week(week=None):
            status, _, raw = await anyio_run(fetch, f"/crew/{link}?format=json" + (f"&week_of={week}" if week else ""))
            return json.loads(raw) if status == 200 else {}

        status, _ = await anyio_run(runner_post, {"action": "add"}, "text/plain")
        check("the page's changes are JSON only", status == 415, str(status))
        # Carol runs Sunday mornings: Sam's usual Sunday morning lines up with her.
        status, sam = await anyio_run(runner_post, {"action": "add", "name": "Sam", "pin": "4821",
                                                    "usual": ["sun_morning", "wed_evening"]})
        check("someone without Daash adds their usual easy runs with a name and PIN",
              status == 200 and len(sam.get("token", "")) >= 30 and sam.get("name") == "Sam", str(sam)[:120])
        status, again = await anyio_run(runner_post, {"action": "add", "name": "carol", "pin": "1111", "usual": []})
        check("a name already in the crew is refused", status == 400 and "already called" in again.get("error", ""), str(again))
        status, bad = await anyio_run(runner_post, {"action": "add", "name": "Pat", "pin": "12", "usual": []})
        check("a PIN is four digits", status == 400 and "four digits" in bad.get("error", ""), str(bad))
        week = await page_week(monday.isoformat())
        sam_row = next((m for m in week.get("members", []) if m["name"] == "Sam"), {})
        sunday = (monday + _td(days=6)).isoformat()
        check("their row is on the page, marked as added by hand, with easy runs in their slots",
              sam_row.get("by_hand") is True and sam_row.get("id") == sam.get("id")
              and sam_row["days"][6] == [{"kind": "easy", "when": ["morning"]}] and "token_hash" not in json.dumps(week),
              str(sam_row)[:200])
        sun = next((t for t in week.get("together", []) if t["date"] == sunday), {})
        check("and they're matched with the crew in the same slot", "Sam" in sun.get("who", []) and sun.get("when") == "morning", str(sun))
        dave_sees = await call(dave, "get_crew", {"week_of": monday.isoformat()})
        check("Daash members see them on their own crew card",
              any(m["name"] == "Sam" and m.get("by_hand") for m in dave_sees.get("members", [])))
        status, mine = await anyio_run(runner_post, {"action": "mine", "token": sam["token"]})
        check("their phone reads back their usual week", status == 200 and mine.get("usual") == ["sun_morning", "wed_evening"], str(mine))
        status, upd = await anyio_run(runner_post, {"action": "update", "token": sam["token"],
                                                    "changes": [{"day": sunday, "slot": "morning", "running": False},
                                                                {"day": (monday + _td(days=5)).isoformat(), "slot": "afternoon", "running": True}]})
        week = await page_week(monday.isoformat())
        sam_row = next((m for m in week.get("members", []) if m["name"] == "Sam"), {})
        check("a change for one week moves their runs that week only",
              status == 200 and sam_row["days"][6] == [] and sam_row["days"][5] == [{"kind": "easy", "when": ["afternoon"]}]
              and json.loads(json.dumps(upd)).get("usual") == ["sun_morning", "wed_evening"], str(sam_row.get("days")))
        status, far = await anyio_run(runner_post, {"action": "update", "token": sam["token"],
                                                    "changes": [{"day": "2030-01-01", "slot": "morning", "running": True}]})
        check("and only this week or next", status == 400, str(far))
        status, nope = await anyio_run(runner_post, {"action": "update", "token": "x" * 32, "usual": []})
        check("without their token nothing changes", status == 400 and "PIN" in nope.get("error", ""), str(nope))
        tries = [await anyio_run(runner_post, {"action": "sign_in", "id": sam["id"], "pin": "0000"}) for _ in range(5)]
        status, locked = await anyio_run(runner_post, {"action": "sign_in", "id": sam["id"], "pin": "4821"})
        check("five wrong PINs lock signing in, even with the right one", all(t[0] == 400 for t in tries) and status == 429
              and "minutes" in locked.get("error", ""), str(locked))
        crews._connect().execute("UPDATE crew_members SET locked_until = 0 WHERE name = 'Sam'").connection.commit()
        status, back = await anyio_run(runner_post, {"action": "sign_in", "id": sam["id"], "pin": "4821"})
        check("on another phone, name and PIN let them back in", status == 200 and back.get("token") and back["token"] != sam["token"], str(back)[:80])
        old = (_date.today() - _td(days=30)).isoformat()
        crews._connect().execute("UPDATE crew_members SET updated_on = ? WHERE name = 'Sam'", (old,)).connection.commit()
        week = await page_week(monday.isoformat())
        sam_row = next((m for m in week.get("members", []) if m["name"] == "Sam"), {})
        check("not back for three weeks, they're shown but not matched",
              sam_row and all(not d for d in sam_row["days"]) and all("Sam" not in t["who"] for t in week.get("together", [])), str(sam_row)[:160])
        status, stale = await anyio_run(runner_post, {"action": "mine", "token": back["token"]})
        status, _ = await anyio_run(runner_post, {"action": "update", "token": back["token"], "still_running": True})
        check("and one tap says they're still running", stale.get("stale") is True
              and next(m for m in (await page_week(monday.isoformat()))["members"] if m["name"] == "Sam")["days"][2] != [], str(stale))
        status, gone = await anyio_run(runner_post, {"action": "remove", "token": back["token"]})
        check("Remove me takes them off, with everything kept for them",
              status == 200 and not any(m["name"] == "Sam" for m in (await page_week()).get("members", []))
              and crews._connect().execute("SELECT COUNT(*) FROM crew_slot_changes").fetchone()[0] == 0, str(gone))
        status, kim = await anyio_run(runner_post, {"action": "add", "name": "Kim", "pin": "1234", "usual": ["mon_morning"]})
        removed = await call(carol, "update_crew", {"remove_member": "Kim"})
        check("whoever started the crew can remove someone added by hand",
              "removed Kim" in removed.get("changed", []) and not any(m["name"] == "Kim" for m in (await page_week()).get("members", [])), str(removed))

        changed = await call(dave, "update_crew", {"club_run_day": "sat"})
        check("only whoever started the crew changes its club run", "Only whoever started" in str(changed.get("error")))
        renamed = await call(carol, "update_crew", {"crew_name": "Friday Flyers", "goes_to_club_run": False})
        check("the crew's starter can rename it", "the crew's name" in renamed.get("changed", []), str(renamed))
        refused = await call(bob, "leave_crew")
        check("leaving asks first", "confirm" in str(refused.get("error")))
        left = await call(bob, "leave_crew", {"confirm": True})
        check("leaving takes your runs with you",
              left.get("left") == "Friday Flyers" and not crews.in_crew(crews.member_key(bob, None))
              and crews._connect().execute("SELECT COUNT(*) FROM crew_runs WHERE member = ?", (f"t:{bob}",)).fetchone()[0] == 0,
              str(left))
        carol_key = crews.member_key(carol, store.get_user(carol).email_hash)
        code_d, _ = await anyio_run(post, "/disconnect", {"t": carol})
        dave_key = crews.member_key(dave, store.get_user(dave).email_hash)
        owner = crews._connect().execute("SELECT owner FROM crews WHERE code = ?", (code,)).fetchone()
        check("Disconnect deletes your place in the crew, and the crew passes on",
              code_d == 200 and not crews.in_crew(carol_key)
              and crews._connect().execute("SELECT COUNT(*) FROM crew_runs WHERE member = ?", (carol_key,)).fetchone()[0] == 0
              and owner is not None and owner[0] == dave_key, str(owner))
        await call(dave, "leave_crew", {"confirm": True})
        check("the last one out deletes the crew",
              crews._connect().execute("SELECT COUNT(*) FROM crews WHERE code = ?", (code,)).fetchone()[0] == 0)

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
