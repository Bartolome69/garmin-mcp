"""Signing up by approving Daash on Garmin's own page, against the hosted server.

Runs the connector's OAuth sign-in with the Garmin route switched on for one
browser: Connect your Garmin, Garmin's page (stubbed), the Allow page asking
for an email, and the code by email when that address already has an
account. Then the tools, for an account with no Garmin Connect behind it.
No network: Garmin and the email sender are stubbed.

    .venv/bin/python tests/signup_test.py
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "tests" / "signup-test.sqlite3"
for suffix in ("", "-wal", "-shm"):
    Path(str(DB) + suffix).unlink(missing_ok=True)

from cryptography.fernet import Fernet  # noqa: E402

PORT = 8934
os.environ["GARMIN_MCP_DB"] = str(DB)
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()
os.environ["GARMIN_MCP_OAUTH"] = "1"
os.environ["GARMIN_MCP_BASE_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["GARMIN_OFFICIAL_CLIENT_ID"] = "client-123"
os.environ["GARMIN_OFFICIAL_CLIENT_SECRET"] = "shh-secret"
os.environ.pop("GARMIN_MCP_SIGNUP", None)
os.environ.setdefault("GARMIN_EMAIL", "test@example.com")
os.environ.setdefault("GARMIN_PASSWORD", "hunter2")

from garminconnect import GarminConnectAuthenticationError  # noqa: E402,F401

from tests.fake_garmin import PROFILE, FakeGarmin  # noqa: E402

import garmin_mcp.hosted as hosted  # noqa: E402
import garmin_mcp.session as session_mod  # noqa: E402
from garmin_mcp import official, store, support_mail  # noqa: E402
import garmin_mcp.server as server_mod  # noqa: E402

BASE = f"http://127.0.0.1:{PORT}"
REDIRECT = "http://localhost:9999/callback"


class _Garth:
    def __init__(self, marker: str) -> None:
        self._marker = marker

    def dumps(self) -> str:
        return json.dumps({"di_token": self._marker})


def fake_build_client(*, prompt_mfa, email=None, password=None):
    """The password route's Garmin sign-in, stubbed: anyone gets in."""
    class Stub(FakeGarmin):
        @property
        def client(self):
            return _Garth(f"{email}-token")

        def login(self, tokenstore=None):
            return (None, None)

        def get_user_profile(self):
            return PROFILE

    return Stub()


hosted.build_client = fake_build_client
session_mod.build_client = fake_build_client


def http(method: str, path: str, body=None, headers=None, as_json=False):
    url = path if path.startswith("http") else f"{BASE}{path}"
    data, head = None, dict(headers or {})
    if body is not None:
        if as_json:
            data = json.dumps(body).encode()
            head.setdefault("content-type", "application/json")
        else:
            data = urllib.parse.urlencode(body).encode()
            head.setdefault("content-type", "application/x-www-form-urlencoded")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    request = urllib.request.Request(url, data=data, headers=head, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


# Garmin, stubbed: which Garmin account the next approval is, and what it allows.
NEXT_GARMIN = {"id": "g-new-1", "permissions": ["ACTIVITY_EXPORT", "HEALTH_EXPORT", "WORKOUT_IMPORT"]}


def fake_post_form(url, fields):
    return {"access_token": f"access-{secrets.token_hex(4)}", "refresh_token": "refresh",
            "expires_in": 86400, "refresh_token_expires_in": 7775998}


def fake_call(access_token, method, path, body=None):
    if path.endswith("/user/id"):
        return {"userId": NEXT_GARMIN["id"]}
    if path.endswith("/user/permissions"):
        if NEXT_GARMIN["permissions"] is None:
            raise official.OfficialError("Garmin doesn't allow this for Daash's app.")
        return {"permissions": NEXT_GARMIN["permissions"]}
    if path == "/workoutportal/workout/v2":
        return {"workoutId": 501, "workoutName": body["workoutName"], "ownerId": 7}
    return {}


official._post_form = fake_post_form
official._call = fake_call

SENT_CODES: list[tuple[str, str]] = []
support_mail.send_code = lambda to, code: SENT_CODES.append((to, code))


async def main() -> int:
    import uvicorn

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
        if not ok:
            failures.append(name)

    reported: list[tuple[str, dict]] = []
    hosted.analytics.capture = lambda event, subject=None, props=None: reported.append((event, props or {}))

    app = hosted.build_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)

    def run(fn):
        return asyncio.get_event_loop().run_in_executor(None, fn)

    _, body, _ = await run(lambda: http("POST", "/register", {
        "client_name": "Test Client", "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none"}, as_json=True))
    client_id = json.loads(body)["client_id"]

    async def start(resource: str | None = None) -> tuple[str, str]:
        """A fresh connector sign-in: (flow, PKCE verifier)."""
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query = urllib.parse.urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
                                        "code_challenge": challenge, "code_challenge_method": "S256", "state": "st",
                                        **({"resource": resource} if resource else {})})
        _, _, headers = await run(lambda: http("GET", f"/authorize?{query}"))
        location = headers.get("location", "")
        return urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["flow"][0], verifier

    async def through_garmin(flow: str, same_browser: bool = False) -> tuple[int, str]:
        """Continue with Garmin, approve on Garmin's (stubbed) page, land back on Daash.

        Back in another browser unless same_browser, which carries the cookie set on the way out.
        """
        code, _, headers = await run(lambda: http("GET", f"/official/signup?flow={urllib.parse.quote(flow)}"))
        garmin_page = headers.get("location", "")
        state = urllib.parse.parse_qs(urllib.parse.urlparse(garmin_page).query).get("state", [""])[0]
        if code != 302 or not garmin_page.startswith(official.AUTHORIZE_URL) or not state:
            return code, f"not sent to Garmin: {code} {garmin_page[:80]}"
        cookie = (headers.get("set-cookie") or headers.get("Set-Cookie") or "").split(";")[0]
        code, body, _ = await run(lambda: http(
            "GET", f"/official/callback?code=garmin-code&state={urllib.parse.quote(state)}",
            headers={"Cookie": cookie} if same_browser and cookie else None))
        return code, body

    def consent_of(body: str) -> str:
        found = re.search(r'name=consent value="([^"]+)"', body)
        return found.group(1) if found else ""

    async def token_for(back: str, verifier: str) -> str:
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(back).query).get("code", [""])[0]
        _, body, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "authorization_code", "code": auth_code, "redirect_uri": REDIRECT,
            "client_id": client_id, "code_verifier": verifier}))
        return json.loads(body).get("access_token", "")

    def subject_of(access: str) -> str:
        row = store.load_oauth_token(access, "access")
        return row["subject"] if row else ""

    async def call_tool(access: str, tool: str, args: dict | None = None) -> dict:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        import httpx2
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {access}"}) as client:
            async with streamable_http_client(f"{BASE}/mcp", http_client=client) as (r, w):
                async with ClientSession(r, w) as sess:
                    await sess.initialize()
                    result = await sess.call_tool(tool, args or {})
                    return json.loads(result.content[0].text)

    async def listing(access: str, path: str = "/mcp") -> tuple[dict, str]:
        """({tool name: description}, instructions) as a client at `path` is told them."""
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        import httpx2
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {access}"}) as client:
            async with streamable_http_client(f"{BASE}{path}", http_client=client) as (r, w):
                async with ClientSession(r, w) as sess:
                    hello = await sess.initialize()
                    names = {t.name: t.description or "" for t in (await sess.list_tools()).tools}
                    return names, hello.instructions or ""

    from types import SimpleNamespace
    parked = {"params": SimpleNamespace(redirect_uri="https://claude.ai/api/mcp/auth_callback"),
              "client_name": "Claude", "client_id": "c"}
    claude_page = bytes(hosted._consent_page("tok", parked, garmin=True, ask_email=True).body).decode()
    check("for Claude, the Allow page says Garmin approved and asks for the email above the chips",
          "Garmin approved. Claude will be able" in claude_page
          and claude_page.index("name=email") < claude_page.index("name=heard")
          and "formnovalidate>Cancel" in claude_page)
    plain = bytes(hosted._consent_page("tok", parked).body).decode()
    check("after a password sign-in it's unchanged: no email field",
          "name=email" not in plain and "You started this from Claude" in plain)

    try:
        print("choosing the route")
        flow, verifier = await start()
        _, body, _ = await run(lambda: http("GET", f"/connect?flow={flow}"))
        check("by default the connector still asks for the Garmin password",
              "Garmin password" in body and "Continue with Garmin" not in body)
        _, body, headers = await run(lambda: http("GET", f"/connect?flow={flow}&route=official"))
        check("?route=official shows Connect your Garmin, with Garmin's note explained calmly",
              "Continue with Garmin" in body and "reviewed" in body and "unverified" not in body and "Garmin password" not in body
              and "Sign in the usual way" in body, body[:200])
        check("and says up front that only what syncs from now on will show",
              "syncs from now on" in body and "last night" in body)
        check("and remembers the choice for this browser",
              "daash_route=official" in headers.get("set-cookie", ""), headers.get("set-cookie", ""))
        _, body, _ = await run(lambda: http("GET", f"/connect?flow={flow}", headers={"Cookie": "daash_route=official"}))
        check("a browser on the official route gets it without asking again", "Continue with Garmin" in body)
        _, body, _ = await run(lambda: http("GET", f"/connect?flow={flow}&usual=1",
                                            headers={"Cookie": "daash_route=official"}))
        check("'Sign in the usual way' is today's password form", "Garmin password" in body)
        shown = [p for e, p in reported if e == "sign_in_shown"]
        check("each showing says which route it was", [p.get("route") for p in shown[-4:]]
              == ["password", "official", "official", "password"], str(shown[-4:]))
        check("with no split running, nothing is tagged as one", not any("split" in p for p in shown))

        hosted.SPLIT = True
        try:
            arms = []
            for _ in range(40):
                f, _v = await start()
                _, body, headers = await run(lambda: http("GET", f"/connect?flow={f}"))
                cookie = headers.get("set-cookie", "")
                arm = "official" if "Continue with Garmin" in body else "password"
                arms.append((arm, cookie))
            check("the split puts new browsers on both routes",
                  {a for a, _c in arms} == {"official", "password"}, str([a for a, _c in arms]))
            check("and each browser keeps the side it drew",
                  all(f"daash_route={a}" in c for a, c in arms), str(arms[:3]))
            f, _v = await start()
            _, body, headers = await run(lambda: http("GET", f"/connect?flow={f}", headers={"Cookie": "daash_route=password"}))
            check("a browser already on a side isn't drawn again",
                  "Garmin password" in body and "daash_route" not in headers.get("set-cookie", ""))
            tagged = [p for e, p in reported if e == "sign_in_shown"][-41:]
            check("every showing in the split says the split and the side",
                  all(p.get("split") == hosted.SPLIT_NAME and p.get("split_arm") == p.get("route") for p in tagged),
                  str(tagged[-2:]))
            iphone = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                      "Version/18.0 Mobile/15E148 Safari/604.1")
            phone_bodies = []
            for cookie in ("", "daash_route=official"):
                f, _v = await start()
                _, body, headers = await run(lambda: http("GET", f"/connect?flow={f}", headers={
                    "User-Agent": iphone, **({"Cookie": cookie} if cookie else {})}))
                phone_bodies.append((body, headers.get("set-cookie", "")))
            check("phones get the password sign-in, even ones the split put on Garmin's page, and aren't drawn",
                  all("Garmin password" in b and "Continue with Garmin" not in b and "daash_route" not in c
                      for b, c in phone_bodies), str([c for _b, c in phone_bodies]))
            f, _v = await start()
            _, body, _ = await run(lambda: http("GET", f"/connect?flow={f}&route=official", headers={"User-Agent": iphone}))
            check("unless the link asks for Garmin's page by name", "Continue with Garmin" in body)
            f, _v = await start()
            _, body, _ = await run(lambda: http("GET", f"/connect?flow={f}", headers={
                "User-Agent": "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) Safari/604.1", "Cookie": "daash_route=official"}))
            check("tablets keep their side of the split", "Continue with Garmin" in body)
        finally:
            hosted.SPLIT = False

        print("\na new person")
        code, body = await through_garmin(flow)
        check("after Garmin's page, the Allow page asks for an email",
              code == 200 and 'name=email' in body and "Your email" in body
              and 'name=heard' in body, body[:300])
        consent = consent_of(body)
        approved = [p for e, p in reported if e == "garmin_approved"]
        check("the approval is counted, new, with workouts allowed",
              approved and {k: approved[-1].get(k) for k in ("new", "workouts")} == {"new": True, "workouts": True},
              str(approved[-1:]))
        check("and it says the person came back in a different browser than they left from",
              approved[-1].get("same_browser") is False, str(approved[-1:]))
        new_account = official.store.official_link_for_garmin_user("g-new-1")
        check("the account exists already, on the official route alone, with no Connect session",
              new_account is not None and not store.has_connect(new_account.user_token)
              and store.official_only(new_account.user_token) == "g-new-1"
              and store.idle_users(-10) == [], str(new_account))
        code, body, _ = await run(lambda: http("POST", "/oauth/consent", {
            "consent": consent, "decision": "allow", "email": "not-an-email"}))
        check("an address that isn't one is sent back with a note", code == 200
              and "look like an email address" in body and consent_of(body) == consent)
        code, _, headers = await run(lambda: http("POST", "/oauth/consent", {
            "consent": consent, "decision": "allow", "email": "sam@example.com", "heard": "friend"}))
        back = headers.get("location", "")
        check("with an email, Allow goes back to the app", code == 303 and back.startswith(REDIRECT), f"{code} {back[:80]}")
        connected = [p for e, p in reported if e == "connector_connected"]
        check("and is reported as an official sign-up, with the address",
              connected and connected[-1].get("route") == "official" and connected[-1].get("email") == "sam@example.com"
              and connected[-1].get("returning") is False, str(connected[-1:]))
        check("the chip goes with it", [p for e, p in reported if e == "heard_from"][-1].get("answer") == "friend")
        check("Reddit is a choice, Search no longer is, and somewhere else can say where",
              'value="reddit"' in body and 'value="search"' not in body and 'name=heard_where' in body, body[-1200:])
        check("somewhere else keeps a few words of where",
              hosted._heard_from({"heard": "other", "heard_where": "  Strava\nclub  " + "x" * 60}) == ("other", "Strava club " + "x" * 28))
        check("where is only kept for somewhere else, and a made-up answer is dropped",
              hosted._heard_from({"heard": "reddit", "heard_where": "r/running"}) == ("reddit", "")
              and hosted._heard_from({"heard": "tiktok"}) == ("", ""))
        check("a page opened before the change can still say Search", hosted._heard_from({"heard": "search"}) == ("search", ""))
        check("and where goes to PostHog next to the answer",
              hosted._heard_properties("other", "Strava") == {"answer": "other", "where": "Strava"}
              and hosted._heard_properties("claude", "") == {"answer": "claude"})
        check("the address is kept on the account", store.email_for(new_account.user_token) == "sam@example.com")
        sam = await token_for(back, verifier)
        check("and the token belongs to that account", subject_of(sam) == new_account.user_token)

        print("\nthe tools, with no Garmin Connect behind them")
        status = await call_tool(sam, "get_connection_status")
        check("connection status says it's Garmin's official API", status.get("via") == "Garmin's official API", str(status))
        ready = await call_tool(sam, "get_readiness")
        check("readiness is Daash's own recovery check, from what Garmin has sent, without trying Connect",
              str(ready.get("source", "")).startswith("Garmin's official API") and "error" not in ready, str(ready)[:200])
        shoes = await call_tool(sam, "get_shoes")
        check("a tool the official API can't answer says so plainly, without trying Connect",
              "Not available on this connection" in str(shoes.get("error", "")), str(shoes)[:200])
        made = await call_tool(sam, "create_workout", {"name": "Easy 5k", "steps": [
            {"type": "interval", "distance_meters": 5000}]})
        check("a workout goes through the Training API", isinstance(made.get("workout_id"), int), str(made)[:200])
        plan = await call_tool(sam, "get_plan")
        check("get_plan says on day one that Garmin sends from today, not that nothing was run",
              "error" not in plan and "Garmin sends training from then on" in json.dumps(plan), str(plan)[:300])
        check("and that before a plan it asks about their running before then",
              "usual weekly distance, longest recent run" in json.dumps(plan), str(plan)[:400])
        night = await call_tool(sam, "get_sleep_data", {"date": "today"})
        check("last night, slept before connecting, is said to be from before, not still coming",
              "from before the account connected Daash" in str(night.get("note")), str(night)[:300])
        day = await call_tool(sam, "get_daily_summary", {"date": "today"})
        check("while today, still being recorded, is just not synced yet",
              "hasn't sent this yet" in str(day.get("note")), str(day)[:300])
        before = await call_tool(sam, "get_daily_summary", {"date": "yesterday"})
        check("and yesterday is from before", "from before the account connected Daash" in str(before.get("note")),
              str(before)[:300])
        check("and offers what works from day one instead",
              "a workout on their watch for tomorrow" in str(before.get("note")), str(before)[:400])
        runs = await call_tool(sam, "get_activities")
        check("an empty activity list says runs come from connecting on, and offers a workout",
              "No activities have synced since the account connected" in str(runs.get("note"))
              and "a workout on their watch" in str(runs.get("note")), str(runs)[:400])
        status = await call_tool(sam, "get_connection_status")
        check("the connection check says it's working and why a new account looks empty",
              "Connected and working" in str(status.get("what_to_expect")), str(status)[:400])

        print("\nthe same person, reconnecting")
        flow2, verifier2 = await start()
        code, body = await through_garmin(flow2)
        check("Garmin's approval recognises them: no email asked",
              code == 200 and "Allow" in body and "name=email" not in body, body[:200])
        code, _, headers = await run(lambda: http("POST", "/oauth/consent", {
            "consent": consent_of(body), "decision": "allow"}))
        again = await token_for(headers.get("location", ""), verifier2)
        check("and they get their own account back", subject_of(again) == new_account.user_token)
        check("reported as returning", [p for e, p in reported if e == "connector_connected"][-1].get("returning") is True)

        print("\nsomeone already on Daash, coming through Garmin")
        flow3, verifier3 = await start()
        _, _, _ = await run(lambda: http("POST", "/connect", {"email": "runner@example.com", "password": "pw", "flow": flow3}))
        existing = store.tokens_for(store.email_fingerprint("runner@example.com"))
        check("they have an account from the password route", len(existing) == 1 and store.has_connect(existing[0]))
        flow4, verifier4 = await start()
        NEXT_GARMIN["id"] = "g-runner"
        code, body = await through_garmin(flow4)
        consent4 = consent_of(body)
        code, body, _ = await run(lambda: http("POST", "/oauth/consent", {
            "consent": consent4, "decision": "allow", "email": "Runner@Example.com "}))
        sealed = re.search(r'name=s value="([^"]+)"', body)
        check("their address has an account, so a code is emailed to it",
              "Check your email" in body and SENT_CODES and SENT_CODES[-1][0] == "Runner@Example.com" and sealed,
              body[:200])
        sealed = sealed.group(1) if sealed else ""
        code, body, _ = await run(lambda: http("POST", "/oauth/confirm", {"s": sealed, "action": "resend"}))
        check("asking for another straight away says one just went", "under a minute ago" in body and len(SENT_CODES) == 1)
        wrong = "000000" if SENT_CODES[-1][1] != "000000" else "111111"
        code, body, _ = await run(lambda: http("POST", "/oauth/confirm", {"s": sealed, "code": wrong}))
        check("a wrong code is refused", "code isn" in body and "t right" in body)
        code, _, headers = await run(lambda: http("POST", "/oauth/confirm", {
            "s": sealed, "code": SENT_CODES[-1][1][:3] + " " + SENT_CODES[-1][1][3:]}))
        joined_back = headers.get("location", "")
        check("the right code goes back to the app", code == 303 and joined_back.startswith(REDIRECT), f"{code} {joined_back[:80]}")
        runner = await token_for(joined_back, verifier4)
        check("joined to the account they already had, which keeps reading through Connect",
              subject_of(runner) == existing[0] and store.official_link(existing[0]) is not None
              and store.official_link(existing[0]).garmin_user_id == "g-runner"
              and store.official_only(existing[0]) is None and store.has_connect(existing[0]))
        check("the account the approval made a moment ago is gone",
              store.tokens_for(store.email_fingerprint("runner@example.com")) == existing
              and store.official_link_for_garmin_user("g-runner").user_token == existing[0])
        check("reported as a join", [p for e, p in reported if e == "connector_connected"][-1].get("joined") is True)
        code, body, _ = await run(lambda: http("POST", "/oauth/confirm", {"s": sealed, "code": SENT_CODES[-1][1]}))
        check("the code and its request can't be used twice", "expired" in body)

        names, said = await listing(sam)
        check("a Garmin-only account isn't offered the tools the official API can't answer",
              "get_fitness" not in names and "get_shoes" not in names and "get_readiness" in names and "get_plan" in names
              and "create_workout" in names and "through Garmin's official API" in said, str(sorted(names))[:300])
        names, said = await listing(runner)
        check("an account with Garmin Connect behind it keeps every tool and the usual instructions",
              "get_readiness" in names and "get_shoes" in names and "through Garmin's official API" not in said)

        check("when Claude or ChatGPT collects its sign-in, that's recorded too",
              any(e == "connector_token_issued" for e, _p in reported))
        from starlette.requests import Request as _Rq
        def _dev(agent):
            return hosted._device(_Rq({"type": "http", "headers": [(b"user-agent", agent.encode())], "query_string": b"", "path": "/"}))
        check("sign-ins say phone or computer and the kind of browser, never the browser string",
              _dev("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1")
              == {"device": "phone", "browser": "safari", "os": "ios"}
              and _dev("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/141.0 Safari/537.36")
              == {"device": "computer", "browser": "chrome", "os": "mac"}
              and _dev("Mozilla/5.0 (Linux; Android 15; Pixel 9; wv) AppleWebKit/537.36 Chrome/141.0 Mobile Safari/537.36")
              == {"device": "phone", "browser": "in-app", "os": "android"})

        print("\nsign-in diagnostics")
        flow_sb, _vsb = await start()
        await through_garmin(flow_sb, same_browser=True)
        approved = [p for e, p in reported if e == "garmin_approved"]
        check("back from Garmin in the browser that left, it says so",
              approved and approved[-1].get("same_browser") is True, str(approved[-1:]))
        code, _, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "authorization_code", "code": "never-issued", "redirect_uri": REDIRECT,
            "client_id": client_id, "code_verifier": "x" * 43}))
        refused = [p for e, p in reported if e == "connector_token_failed"]
        check("an app turned away collecting its sign-in is counted, with why, never the code",
              code == 400 and refused and refused[-1].get("error") == "invalid_grant"
              and refused[-1].get("grant_type") == "authorization_code" and "never-issued" not in str(refused[-1]),
              str(refused[-1:]))
        import base64 as _b64
        basic = _b64.b64encode(f"{urllib.parse.quote(client_id, safe='')}:".encode()).decode()
        code, body, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "authorization_code", "code": "never-issued", "redirect_uri": REDIRECT,
            "code_verifier": "x" * 43}, headers={"Authorization": f"Basic {basic}"}))
        check("a client naming itself only in the Basic header is read, not refused as missing",
              code == 400 and json.loads(body).get("error") == "invalid_grant", body)
        flow_hb, verifier_hb = await start()
        _, body, _ = await run(lambda: http("POST", "/connect", {"flow": flow_hb, "email": "basic@example.com",
                                                                 "password": "pw"}))
        back_hb = ""
        consent_hb = consent_of(body)
        if consent_hb:
            _, _, headers = await run(lambda: http("POST", "/oauth/consent", {"consent": consent_hb, "decision": "allow"}))
            back_hb = headers.get("location", "")
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(back_hb).query).get("code", [""])[0]
        code, body, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "authorization_code", "code": auth_code, "redirect_uri": REDIRECT,
            "code_verifier": verifier_hb}, headers={"Authorization": f"Basic {basic}"}))
        check("and gets its token", code == 200 and "access_token" in body, f"{code} {body[:200]} {back_hb[:80]}")
        # Glama registers without saying how it sends its secret, so the SDK
        # files it as sending it in the form; it sends it in the Basic header.
        _, body, _ = await run(lambda: http("POST", "/register", {
            "client_name": "Glama", "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}, as_json=True))
        glama = json.loads(body)
        verifier_g = secrets.token_urlsafe(48)
        challenge_g = base64.urlsafe_b64encode(hashlib.sha256(verifier_g.encode()).digest()).decode().rstrip("=")
        query_g = urllib.parse.urlencode({"response_type": "code", "client_id": glama["client_id"], "redirect_uri": REDIRECT,
                                          "code_challenge": challenge_g, "code_challenge_method": "S256", "state": "st"})
        _, _, headers = await run(lambda: http("GET", f"/authorize?{query_g}"))
        flow_g = urllib.parse.parse_qs(urllib.parse.urlparse(headers.get("location", "")).query)["flow"][0]
        _, body, _ = await run(lambda: http("POST", "/connect", {"flow": flow_g, "email": "glama@example.com",
                                                                 "password": "pw"}))
        _, _, headers = await run(lambda: http("POST", "/oauth/consent", {"consent": consent_of(body), "decision": "allow"}))
        code_g = urllib.parse.parse_qs(urllib.parse.urlparse(headers.get("location", "")).query).get("code", [""])[0]
        pair = _b64.b64encode(f"{urllib.parse.quote(glama['client_id'], safe='')}:"
                              f"{urllib.parse.quote(glama.get('client_secret', ''), safe='')}".encode()).decode()
        code, body, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "authorization_code", "code": code_g, "redirect_uri": REDIRECT,
            "code_verifier": verifier_g}, headers={"Authorization": f"Basic {pair}"}))
        check("a client that registered without a method and sends its secret in the Basic header gets its token",
              bool(glama.get("client_secret")) and code == 200 and "access_token" in body, f"{code} {body[:200]}")
        wrong = _b64.b64encode(f"{urllib.parse.quote(glama['client_id'], safe='')}:not-the-secret".encode()).decode()
        code, body, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "refresh_token", "refresh_token": "x"}, headers={"Authorization": f"Basic {wrong}"}))
        check("but a wrong secret there is still refused", code == 401 and "invalid_client" in body, f"{code} {body[:200]}")

        print("\nthe directory address")
        accept = {"accept": "application/json, text/event-stream"}
        hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "directory-check", "version": "1"}}}
        code, body, _ = await run(lambda: http("POST", "/directory/mcp", hello, as_json=True, headers=accept))
        said = json.loads(body) if code == 200 else {}
        check("a directory can introduce itself without signing in",
              said.get("result", {}).get("serverInfo", {}).get("title") == "Daash"
              and said["result"]["protocolVersion"] == "2025-06-18" and "/mcp" in said["result"]["instructions"], body)
        code, body, _ = await run(lambda: http("POST", "/directory/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                               as_json=True, headers=accept))
        check("its notifications are taken quietly", code == 202, str(code))
        code, body, _ = await run(lambda: http("POST", "/directory/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                               as_json=True, headers=accept))
        tools = [t["name"] for t in json.loads(body)["result"]["tools"]] if code == 200 else []
        check("and list the tools everyone gets, preview ones left out",
              "get_plan" in tools and "create_workout" in tools and "garmin_official" not in tools
              and all("inputSchema" in t for t in json.loads(body)["result"]["tools"]), str(tools))
        code, body, _ = await run(lambda: http("POST", "/directory/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                                                          "params": {"name": "get_plan", "arguments": {}}},
                                               as_json=True, headers=accept))
        called = json.loads(body).get("result", {}) if code == 200 else {}
        check("but a tool call reaches nobody's Garmin: it says where to connect",
              called.get("isError") is True and f"{BASE}/mcp" in called["content"][0]["text"], body)
        code, _, headers = await run(lambda: http("POST", "/mcp", hello, as_json=True, headers=accept))
        check("while the real address still asks for sign-in", code == 401, str(code))

        print("\nthe password address")
        pw_address = f"{BASE}/password/mcp"
        code, body, _ = await run(lambda: http("GET", "/.well-known/oauth-protected-resource/password/mcp"))
        meta = json.loads(body) if code == 200 else {}
        check("the password address has its own resource metadata, on the same authorization server",
              meta.get("resource") == pw_address and meta.get("authorization_servers") == [f"{BASE}/"], str(meta))
        probe = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        code, _, headers = await run(lambda: http("POST", "/password/mcp", probe, as_json=True,
                                                  headers={"accept": "application/json, text/event-stream"}))
        challenge_header = headers.get("www-authenticate", "") or headers.get("WWW-Authenticate", "")
        check("asked without a token, it points clients at its own metadata",
              code == 401 and "oauth-protected-resource/password/mcp" in challenge_header, f"{code} {challenge_header}")
        flow_p, _vp = await start(resource=pw_address)
        _, body, _ = await run(lambda: http("GET", f"/connect?flow={flow_p}", headers={"Cookie": "daash_route=official"}))
        check("a sign-in for it gets the password form, even in a browser on Garmin's route",
              "Garmin password" in body and "Continue with Garmin" not in body)
        hosted.SPLIT = True
        try:
            _, body, headers = await run(lambda: http("GET", f"/connect?flow={flow_p}"))
            shown_p = [p for e, p in reported if e == "sign_in_shown"][-1]
            check("and during the split it's neither drawn nor counted in it",
                  "Garmin password" in body and "daash_route" not in headers.get("set-cookie", "")
                  and shown_p.get("password_link") is True and "split" not in shown_p, str(shown_p))
        finally:
            hosted.SPLIT = False
        names, said = await listing(runner, "/password/mcp")
        check("and it serves the same tools as the usual address", "get_fitness" in names and "get_plan" in names,
              str(sorted(names))[:200])

        print("\nthe listed address")
        code, body, _ = await run(lambda: http("GET", "/.well-known/openai-apps-challenge"))
        check("OpenAI's domain check finds its token alone, as plain text",
              code == 200 and body == hosted.OPENAI_APPS_CHALLENGE, f"{code} {body[:80]}")
        listed = f"{BASE}/listed/mcp"
        code, body, _ = await run(lambda: http("GET", "/.well-known/oauth-protected-resource/listed/mcp"))
        meta = json.loads(body) if code == 200 else {}
        check("it has its own resource metadata, on the same authorization server",
              meta.get("resource") == listed and meta.get("authorization_servers") == [f"{BASE}/"], str(meta))
        probe = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        code, _, headers = await run(lambda: http("POST", "/listed/mcp", probe, as_json=True,
                                                  headers={"accept": "application/json, text/event-stream"}))
        challenge_header = headers.get("www-authenticate", "") or headers.get("WWW-Authenticate", "")
        check("asked without a token, it points clients at that metadata",
              code == 401 and "oauth-protected-resource/listed/mcp" in challenge_header, f"{code} {challenge_header}")
        flow_l, verifier_l = await start(resource=listed)
        _, body, _ = await run(lambda: http("GET", f"/connect?flow={flow_l}"))
        check("a sign-in for it goes through Garmin, with no password route offered, whatever the switch says",
              "Continue with Garmin" in body and "Sign in the usual way" not in body and "Garmin password" not in body)
        _, body, _ = await run(lambda: http("GET", f"/connect?flow={flow_l}&usual=1"))
        check("not even by asking for the usual way", "Garmin password" not in body)
        _, body, _ = await run(lambda: http("POST", "/connect", {"email": "x@example.com", "password": "pw", "flow": flow_l}))
        check("a password sent anyway goes nowhere", "Continue with Garmin" in body and "Allow" not in body)
        NEXT_GARMIN["id"] = "g-listed"
        code, body = await through_garmin(flow_l)
        _, _, headers = await run(lambda: http("POST", "/oauth/consent", {
            "consent": consent_of(body), "decision": "allow", "email": "listed@example.com"}))
        listed_token = await token_for(headers.get("location", ""), verifier_l)
        names, said = await listing(listed_token, "/listed/mcp")
        check("and the listed address serves the official tools, with the official instructions",
              bool(listed_token) and "get_plan" in names and "get_fitness" not in names and "get_readiness" in names
              and "through Garmin's official API" in said, str(sorted(names))[:200])
        check("its sign-up is reported as listed",
              [p for e, p in reported if e == "connector_connected"][-1].get("listed") is True)
        was_preview = os.environ.get("GARMIN_MCP_PREVIEW")
        os.environ["GARMIN_MCP_PREVIEW"] = "on"
        try:
            on_listed, _said = await listing(listed_token, "/listed/mcp")
            on_main, _said = await listing(listed_token, "/mcp")
        finally:
            if was_preview is None:
                os.environ.pop("GARMIN_MCP_PREVIEW", None)
            else:
                os.environ["GARMIN_MCP_PREVIEW"] = was_preview
        check("a tool still in review stays off the listed address, with the same sign-in on the usual one",
              "get_session" not in on_listed and "get_session" in on_main, f"{sorted(on_listed)[:5]} {sorted(on_main)[:5]}")
        naming = {n: [o for o in names if o != n and re.search(rf"\b{o}\b", d)] for n, d in names.items()}
        check("each listed tool's description names no other tool",
              not any(naming.values()), str({n: o for n, o in naming.items() if o}))
        check("and gives the model no instructions on how to answer",
              not any(re.search(r"\b(call it|call this|don't|never guess|ask once|answer from|show the)\b", d, re.I)
                      for d in names.values()), "")
        check("every listed tool has its own plain description",
              set(names) <= set(server_mod.OFFICIAL_DESCRIPTIONS), str(set(names) - set(server_mod.OFFICIAL_DESCRIPTIONS)))
        listed_user = store.get_user(listed_token) or store.get_user(subject_of(listed_token))
        after = hosted._after_tool_for(subject_of(listed_token), listed_user, alone=True)
        check("its answers carry no tip, welcome or news for the model",
              after("get_activities", {"activities": []}) == {"activities": []}
              and after("get_plan", {"weeks": []}) == {"weeks": []})
        check("nor is a read sent alongside get_plan held back",
              hosted._before_tool_for(subject_of(listed_token), alone=True)("get_sleep_data") == 0.0)

        print("\nGarmin's page first, then a password on another device")
        # Oct 30: approved on Garmin's page on a tablet, then signed in with a
        # password on a phone, and kept reading from the official API alone.
        both = store.save_official_user("g-both", {"access_token": "t"}, ["ACTIVITY_EXPORT"])
        both_hash = store.set_user_email(both, "both@example.com", "b***@example.com")
        check("an account from Garmin's page reads from the official API alone",
              not store.has_connect(both) and store.official_only(both) == "g-both")
        again = store.save_user("b***@example.com", json.dumps({"di_token": "both"}), user_token=both,
                                email_hash=both_hash, email="both@example.com")
        check("signing in with a password on top reads Garmin Connect, keeping the Garmin link",
              again == both and store.has_connect(both) and store.official_only(both) is None
              and store.official_link(both) is not None)
        stuck = store.save_official_user("g-stuck", {"access_token": "t"}, ["ACTIVITY_EXPORT"])
        with store._connect() as conn:  # as the bug left them: a session stored, the mark kept
            conn.execute("UPDATE users SET token_blob = ? WHERE user_token = ?",
                         (store._cipher().encrypt(b'{"di_token": "stuck"}'), stuck))
        alone = store.save_official_user("g-alone", {"access_token": "t"}, ["ACTIVITY_EXPORT"])
        repaired = store.repair_password_on_official()
        check("accounts already stuck that way are repaired, and true official-only ones left alone",
              repaired == 1 and store.has_connect(stuck) and store.official_only(stuck) is None
              and not store.has_connect(alone) and store.official_only(alone) == "g-alone", str(repaired))

        print("\ncounting against Garmin's user limit")
        counted = hosted.report_official_users()
        sent = [p for e, p in reported if e == "official_users"][-1:]
        check("the linked accounts are counted against the limit, with the official sign-ups among them",
              counted and counted["linked"] >= 3 and 1 <= counted["official_signups"] <= counted["linked"]
              and sent and sent[0]["limit"] == 1000 and sent[0]["linked"] == counted["linked"], f"{counted} {sent}")

        print("\nwhen Garmin turns a password sign-in away")
        flow_b, _ = await start()
        refused = hosted._signin_error(Exception("403 Forbidden"), flow_b, paused=False)
        check("the refusal offers Garmin's own page for the same request",
              "Continue with Garmin" in refused and f"/official/signup?flow={urllib.parse.quote(flow_b, safe='')}&amp;via=blocked" in refused)
        check("and so does Garmin asking for a pause",
              "Continue with Garmin" in hosted._signin_error(Exception("429 Too Many Requests"), flow_b, paused=True))
        # Oct 29: a wrong password, then Garmin's lock, then gone. Garmin's
        # page needs no password from someone signed in to Garmin already.
        wrong = hosted._signin_error(Exception("401 Unauthorized"), flow_b)
        locked = hosted._signin_error(Exception("403"), flow_b, account_paused=True)
        check("so do a wrong password and Garmin's lock after one, each counted as such",
              "Continue with Garmin" in wrong and "via=password" in wrong and "reset your password" in wrong
              and "Continue with Garmin" in locked and "via=paused" in locked and "no wait" not in locked)
        check("but not a page with no request behind it",
              "Continue with Garmin" not in hosted._signin_error(Exception("403"), "", paused=False))
        _, _, headers = await run(lambda: http("GET", f"/official/signup?flow={urllib.parse.quote(flow_b)}&via=blocked"))
        check("taking it goes to Garmin, and is counted as coming from a refusal",
              "garmin" in headers.get("location", "")
              and [p for e, p in reported if e == "garmin_sign_in_started"][-1].get("via") == "blocked",
              str([p for e, p in reported if e == "garmin_sign_in_started"][-1:]))
        flow_w, _ = await start()
        await run(lambda: http("GET", f"/official/signup?flow={urllib.parse.quote(flow_w)}&via=paused"))
        check("and from a lock, as a lock",
              [p for e, p in reported if e == "garmin_sign_in_started"][-1].get("via") == "paused")

        print("\nwhen Garmin won't list what the account shared")
        official.time.sleep = lambda _s: None
        flow_p, _ = await start()
        NEXT_GARMIN.update(id="g-no-perms", permissions=None)
        code, body = await through_garmin(flow_p)
        check("the sign-up carries on to the Allow page", code == 200 and "name=email" in body, body[:200])
        held = store.official_link_for_garmin_user("g-no-perms")
        check("with the permissions left unknown, and writing allowed until Garmin says otherwise",
              held is not None and held.permissions is None and official.can_write(held.user_token))
        approved = [p for e, p in reported if e == "garmin_approved"]
        check("reported as unknown", approved[-1].get("workouts") == "unknown", str(approved[-1:]))
        NEXT_GARMIN["permissions"] = ["ACTIVITY_EXPORT", "HEALTH_EXPORT", "WORKOUT_IMPORT"]

        print("\nturning back")
        flow5, _ = await start()
        NEXT_GARMIN["id"] = "g-other"
        _, _, headers = await run(lambda: http("GET", f"/official/signup?flow={urllib.parse.quote(flow5)}"))
        state = urllib.parse.parse_qs(urllib.parse.urlparse(headers.get("location", "")).query).get("state", [""])[0]
        code, body, _ = await run(lambda: http(
            "GET", f"/official/callback?error=access_denied&state={urllib.parse.quote(state)}"))
        check("declining on Garmin's page offers to try again, request still attached",
              "Not connected" in body and f"/official/signup?flow={urllib.parse.quote(flow5, safe='')}" in body
              and "Sign in the usual way" in body, body[:300])
        code, body = await through_garmin(flow5)
        consent5 = consent_of(body)
        _, body, _ = await run(lambda: http("POST", "/oauth/consent", {
            "consent": consent5, "decision": "allow", "email": "sam@example.com"}))
        sealed5 = re.search(r'name=s value="([^"]+)"', body).group(1)
        _, body, _ = await run(lambda: http("POST", "/oauth/confirm", {"s": sealed5, "action": "change"}))
        check("'Use a different email' goes back to the Allow page", consent_of(body) == consent5 and "name=email" in body)
        code, _, headers = await run(lambda: http("POST", "/oauth/consent", {
            "consent": consent5, "decision": "deny"}))
        check("Cancel still works without an email", code == 303 and "error=access_denied" in headers.get("location", ""))
        _, body, headers = await run(lambda: http("GET", "/connect?route=official"))
        check("opening ?route=official by hand says what it did",
              "Sign-in route set" in body and "daash_route=official" in headers.get("set-cookie", ""))
        import sqlite3
        sends = []
        for _ in range(store.CODE_SENDS_PER_DAY + 1):
            sends.append(store.new_email_code("cap-test"))
            with sqlite3.connect(DB) as conn:
                conn.execute("UPDATE email_codes SET sent_at = sent_at - 120 WHERE email_hash = 'cap-test'")
        check("an address gets a few codes a day, then no more",
              all(sends[:-1]) and sends[-1] is None and store.code_wait("cap-test") == "day")
        _, body, _ = await run(lambda: http("GET", "/official/signup?flow=made-up"))
        check("a sign-up link with no request behind it says start again", "Start again" in body)
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
