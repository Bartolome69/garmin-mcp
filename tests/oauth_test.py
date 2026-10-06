"""Drive the whole OAuth 2.1 flow against the hosted server.

Registers a client, runs authorize -> Garmin sign-in -> code -> token, then
calls a tool with the bearer token. No network: the Garmin client is stubbed,
as in hosted_test.

    .venv/bin/python tests/oauth_test.py
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
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "tests" / "oauth-test.sqlite3"
for suffix in ("", "-wal", "-shm"):
    Path(str(DB) + suffix).unlink(missing_ok=True)

from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_DB"] = str(DB)
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()
os.environ["GARMIN_MCP_OAUTH"] = "1"
os.environ["GARMIN_MCP_BASE_URL"] = "http://127.0.0.1:8932"
os.environ.setdefault("GARMIN_EMAIL", "test@example.com")
os.environ.setdefault("GARMIN_PASSWORD", "hunter2")

from tests.fake_garmin import PROFILE, FakeGarmin  # noqa: E402

import garmin_mcp.hosted as hosted  # noqa: E402
from garmin_mcp import store  # noqa: E402


class _Garth:
    def __init__(self, marker: str) -> None:
        self._marker = marker

    def dumps(self) -> str:
        return json.dumps({"di_token": self._marker})


def fake_build_client(*, prompt_mfa, email=None, password=None):
    class Stub(FakeGarmin):
        _marker = f"{email}-token"

        @property
        def client(self):
            return _Garth(self._marker)

        def login(self, tokenstore=None):
            if email == "wrongpw@example.com":
                raise GarminConnectAuthenticationError("Authentication failed (401 Unauthorized).")
            if tokenstore:
                try:
                    self._marker = json.loads(tokenstore).get("di_token", "none")
                except ValueError:
                    pass
            self.full_name = f"account:{self._marker}"
            return (None, None)

        def get_user_profile(self):
            return {**PROFILE, "fullName": self.full_name}

    stub = Stub()
    stub.password = password
    return stub


hosted.build_client = fake_build_client
import garmin_mcp.session as session_mod  # noqa: E402
from garminconnect import GarminConnectAuthenticationError  # noqa: E402

session_mod.build_client = fake_build_client

BASE = "http://127.0.0.1:8932"
REDIRECT = "http://localhost:9999/callback"


def http(method: str, path: str, body=None, headers=None, allow_redirect=False,
         as_json=False):
    """Registration speaks JSON; the token endpoint speaks form encoding."""
    url = path if path.startswith("http") else f"{BASE}{path}"
    data = None
    head = dict(headers or {})
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

    opener = urllib.request.build_opener(
        *( [] if allow_redirect else [NoRedirect] )
    )
    req = urllib.request.Request(url, data=data, headers=head, method=method)
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


async def main() -> int:
    import uvicorn

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
        if not ok:
            failures.append(name)

    app = hosted.build_app()
    routes = sorted({getattr(r, "path", "") for r in app.routes})
    for needed in ("/authorize", "/token", "/register", "/revoke", "/mcp"):
        check(f"{needed} is mounted", needed in routes)
    check(
        "metadata document is served",
        "/.well-known/oauth-authorization-server" in routes,
    )

    config = uvicorn.Config(app, host="127.0.0.1", port=8932, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    check("server started", server.started)

    def run(fn, *a):
        return asyncio.get_event_loop().run_in_executor(None, fn, *a)

    try:
        # -- 1. discovery ------------------------------------------------
        code, body, _ = await run(
            lambda: http("GET", "/.well-known/oauth-authorization-server")
        )
        meta = json.loads(body) if code == 200 else {}
        check("metadata advertises the endpoints", code == 200
              and "authorization_endpoint" in meta and "token_endpoint" in meta,
              str(code))

        # -- 2. dynamic client registration ------------------------------
        code, body, _ = await run(
            lambda: http("POST", "/register", {
                "client_name": "Test Client",
                "redirect_uris": [REDIRECT],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }, as_json=True)
        )
        registered = json.loads(body) if code in (200, 201) else {}
        client_id = registered.get("client_id", "")
        check("client can register itself", bool(client_id), f"status {code} {body[:120]}")

        # -- 3. authorize -> redirected to the Garmin sign-in ------------
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        query = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz-state",
        })
        code, body, headers = await run(lambda: http("GET", f"/authorize?{query}"))
        location = headers.get("location", "")
        check("authorize sends the person to sign in",
              code in (302, 303, 307) and "/connect?" in location and "flow=" in location,
              f"{code} {location[:90]}")
        flow = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("flow", [""])[0]

        # -- 3b. a failed sign-in keeps Claude's request attached ----------
        # A "Try again" that dropped it sent people to the form as if they had
        # arrived on their own, and that road ends in a link this mode never
        # serves. Both halves of that are checked here.
        code, body, headers = await run(lambda: http("POST", "/connect", {
            "email": "wrongpw@example.com", "password": "pw", "flow": flow,
        }))
        check("a failed sign-in's retry link keeps the flow",
              code == 400 and f"flow={urllib.parse.quote(flow, safe='')}" in body,
              f"{code} {body[body.find('Try again') - 120 : body.find('Try again')]}")
        code, body, headers = await run(lambda: http("POST", "/connect", {
            "email": "direct@example.com", "password": "pw",
        }))
        check("a sign-in without a flow is sent back to Claude, not given a link",
              code == 200 and "/mcp" in body and "/u/" not in body
              and "Add custom connector" in body,
              f"{code} {body[:160]}")
        code, body, headers = await run(lambda: http("GET", "/"))
        check("the landing page points at Claude, not at a private link",
              code == 200 and "/mcp" in body and "private link" not in body
              and "Get started" not in body)

        # -- 4. sign in; expect the consent page, not a code -------------
        # Registration is open, so a signed-in person is not evidence that they
        # wanted *this* client. Nothing is issued until they say so.
        code, body, headers = await run(lambda: http("POST", "/connect", {
            "email": "oauth@example.com", "password": "pw", "flow": flow,
        }))
        check("sign-in asks before handing anything over",
              code == 200 and "Allow access?" in body and "location" not in headers,
              f"{code} {body[:120]}")
        check("the consent page names where the code goes", REDIRECT in body)
        check("the consent page cannot be framed",
              headers.get("x-frame-options", "").upper() == "DENY",
              str(dict(headers))[:120])
        consent = re.search(r'name=consent value="([^"]+)"', body)
        check("a consent token is issued", bool(consent))
        consent_token = consent.group(1) if consent else ""

        # A loopback callback is an ordinary desktop client, so no alarm.
        check("a recognised destination is not flagged",
              "not an app we recognise" not in body)

        # -- 4b. approve ------------------------------------------------
        code, body, headers = await run(lambda: http("POST", "/oauth/consent", {
            "consent": consent_token, "decision": "allow",
        }))
        back = headers.get("location", "")
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
        auth_code = parsed.get("code", [""])[0]
        check("approving returns to the client with a code",
              code in (302, 303) and back.startswith(REDIRECT) and bool(auth_code),
              f"{code} {back[:90]}")
        check("state is handed back untouched", parsed.get("state", [""])[0] == "xyz-state")
        check("no connector URL is shown anywhere",
              "/u/" not in body and "/u/" not in back, back[:90])

        # -- 4c. the consent token is single use ------------------------
        code, body, headers = await run(lambda: http("POST", "/oauth/consent", {
            "consent": consent_token, "decision": "allow",
        }))
        check("a consent token cannot be replayed",
              code != 303 or not headers.get("location", "").startswith(REDIRECT),
              f"{code} {headers.get('location', '')[:80]}")

        # -- 5. exchange the code ----------------------------------------
        code, body, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
        }))
        tokens = json.loads(body) if code == 200 else {}
        access = tokens.get("access_token", "")
        refresh = tokens.get("refresh_token", "")
        check("code exchanges for a token", bool(access), f"status {code} {body[:140]}")
        check("a refresh token comes with it", bool(refresh))
        check("the token expires", bool(tokens.get("expires_in")))

        # -- 6. the code is single use -----------------------------------
        code, body, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "authorization_code", "code": auth_code,
            "redirect_uri": REDIRECT, "client_id": client_id,
            "code_verifier": verifier,
        }))
        check("the same code cannot be used twice", code != 200, f"status {code}")

        # -- 7. nothing stored is usable as a credential -----------------
        with __import__("sqlite3").connect(DB) as conn:
            rows = conn.execute("SELECT token_hash FROM oauth_tokens").fetchall()
        check("tokens are stored hashed, not in the clear",
              bool(rows) and all(access not in r[0] and refresh not in r[0] for r in rows))

        # -- 7b. the attack the consent page exists to stop ---------------
        # Anyone may register a client. Registering one that points at a site
        # you control, then sending somebody a crafted /authorize link, gets
        # them a sign-in page that is genuinely ours on a domain that is
        # genuinely ours. PKCE is no help: the attacker is the client.
        evil_redirect = "https://evil.example/collect"
        code, body, _ = await run(
            lambda: http("POST", "/register", {
                "client_name": "<img src=x onerror=alert(1)>Garmin Sync",
                "redirect_uris": [evil_redirect],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }, as_json=True)
        )
        evil_id = (json.loads(body) if code in (200, 201) else {}).get("client_id", "")
        check("an attacker can still register (registration is open)", bool(evil_id))

        evil_query = urllib.parse.urlencode({
            "response_type": "code", "client_id": evil_id,
            "redirect_uri": evil_redirect, "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "victim-state",
        })
        _, _, headers = await run(lambda: http("GET", f"/authorize?{evil_query}"))
        evil_flow = urllib.parse.parse_qs(
            urllib.parse.urlparse(headers.get("location", "")).query
        ).get("flow", [""])[0]
        code, body, headers = await run(lambda: http("POST", "/connect", {
            "email": "victim@example.com", "password": "pw", "flow": evil_flow,
        }))
        check("signing in does not hand the attacker a code",
              code == 200 and "location" not in headers,
              f"{code} {headers.get('location', '')[:80]}")
        check("the page warns the destination is unrecognised",
              "not an app we recognise" in body, body[:160])
        check("the attacker's address is shown in full",
              evil_redirect in body)
        check("a hostile client name cannot inject markup",
              "<img src=x" not in body and "&lt;img src=x" in body,
              body[body.find("Garmin Sync") - 90 : body.find("Garmin Sync") + 12])

        evil_consent = re.search(r'name=consent value="([^"]+)"', body)
        code, body, headers = await run(lambda: http("POST", "/oauth/consent", {
            "consent": evil_consent.group(1) if evil_consent else "", "decision": "deny",
        }))
        denied = urllib.parse.parse_qs(
            urllib.parse.urlparse(headers.get("location", "")).query
        )
        check("cancelling tells the client it was refused",
              denied.get("error", [""])[0] == "access_denied", str(denied)[:120])
        check("cancelling issues no code", "code" not in denied)
        check("cancelling keeps the client's state",
              denied.get("state", [""])[0] == "victim-state")

        # -- 8. refresh ---------------------------------------------------
        code, body, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": client_id,
        }))
        refreshed = json.loads(body) if code == 200 else {}
        check("refresh token yields a new access token",
              bool(refreshed.get("access_token")), f"status {code} {body[:120]}")
        # Claude can send the same refresh twice, from two requests that both
        # found the access token expired. The second must not end the session.
        code, body, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": client_id,
        }))
        check("a duplicate refresh moments later still works",
              code == 200 and bool(json.loads(body).get("access_token")), f"status {code} {body[:120]}")
        with __import__("sqlite3").connect(DB) as conn:
            remaining = conn.execute(
                "SELECT expires_at FROM oauth_tokens WHERE token_hash = ?",
                (hashlib.sha256(refresh.encode()).hexdigest(),),
            ).fetchone()
        check("an exchanged refresh token expires within minutes, not a month",
              remaining is not None and remaining[0] <= time.time() + 180, str(remaining))
        with __import__("sqlite3").connect(DB) as conn:
            conn.execute("UPDATE oauth_tokens SET expires_at = ? WHERE token_hash = ?",
                         (int(time.time()) - 1, hashlib.sha256(refresh.encode()).hexdigest()))
        code, _, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": client_id,
        }))
        check("the used refresh token is dead once the grace has passed", code != 200, f"status {code}")

        # -- 9. the token actually gates the MCP endpoint ----------------
        # The point of all of the above: /mcp is one public path, and who is
        # asking comes from the header rather than the URL.
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        import httpx2

        async def call_tool(bearer: str | None, tool: str):
            head = {"Authorization": f"Bearer {bearer}"} if bearer else {}
            async with httpx2.AsyncClient(headers=head) as http_client:
                async with streamable_http_client(
                    f"{BASE}/mcp", http_client=http_client
                ) as (r, w):
                    async with ClientSession(r, w) as sess:
                        await sess.initialize()
                        result = await sess.call_tool(tool, {})
                        return json.loads(result.content[0].text)

        fresh = refreshed["access_token"]
        try:
            payload = await call_tool(fresh, "get_connection_status")
            ok = payload.get("authenticated") is True
            check("a bearer token reaches that person's Garmin session", ok,
                  str(payload)[:140])
            check("it is the right person",
                  "oauth@example.com-token" in json.dumps(payload)
                  or payload.get("garmin_display_name", "").endswith("oauth@example.com-token"),
                  str(payload.get("garmin_display_name"))[:80])
        except Exception as exc:  # noqa: BLE001
            check("a bearer token reaches that person's Garmin session", False,
                  f"{type(exc).__name__}: {exc}"[:160])
            check("it is the right person", False, "not reached")

        # Assert the status directly. Going through the MCP client wraps the
        # refusal in an ExceptionGroup, which hid a genuine 401 on a valid token
        # behind the same message as a correct rejection.
        probe = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        accept = "application/json, text/event-stream"
        for label, bearer in (("no token", None), ("a made-up token", "not-a-real-token")):
            head = {"accept": accept}
            if bearer:
                head["Authorization"] = f"Bearer {bearer}"
            status, body, _ = await run(
                lambda h=head: http("POST", "/mcp", probe, headers=h, as_json=True)
            )
            check(f"{label} is refused with 401", status == 401, f"got {status} {body[:90]}")

        head = {"accept": accept, "Authorization": f"Bearer {fresh}"}
        status, body, _ = await run(
            lambda: http("POST", "/mcp", probe, headers=head, as_json=True)
        )
        check("a valid token is not refused", status != 401, f"got {status} {body[:90]}")

        # -- 10. a second app does not disconnect the first --------------
        # Connecting ChatGPT, or Claude on a second account, signs in to
        # Garmin again. That used to retire the first app's connection.
        def subject_of(token: str) -> str:
            row = store.load_oauth_token(token, "access")
            return row["subject"] if row else ""

        first_subject = subject_of(fresh)
        code, body, _ = await run(lambda: http("POST", "/register", {
            "client_name": "Second App", "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none",
        }, as_json=True))
        second_client = json.loads(body).get("client_id", "") if code in (200, 201) else ""
        verifier2 = secrets.token_urlsafe(48)
        challenge2 = base64.urlsafe_b64encode(
            hashlib.sha256(verifier2.encode()).digest()
        ).decode().rstrip("=")
        query2 = urllib.parse.urlencode({
            "response_type": "code", "client_id": second_client, "redirect_uri": REDIRECT,
            "code_challenge": challenge2, "code_challenge_method": "S256", "state": "s2",
        })
        _, _, headers = await run(lambda: http("GET", f"/authorize?{query2}"))
        flow2 = urllib.parse.parse_qs(
            urllib.parse.urlparse(headers.get("location", "")).query
        ).get("flow", [""])[0]
        _, body, _ = await run(lambda: http("POST", "/connect", {
            "email": "oauth@example.com", "password": "pw", "flow": flow2,
        }))
        consent2 = re.search(r'name=consent value="([^"]+)"', body)
        _, _, headers = await run(lambda: http("POST", "/oauth/consent", {
            "consent": consent2.group(1) if consent2 else "", "decision": "allow",
        }))
        code2 = urllib.parse.parse_qs(
            urllib.parse.urlparse(headers.get("location", "")).query
        ).get("code", [""])[0]
        code, body, _ = await run(lambda: http("POST", "/token", {
            "grant_type": "authorization_code", "code": code2, "redirect_uri": REDIRECT,
            "client_id": second_client, "code_verifier": verifier2,
        }))
        second_access = json.loads(body).get("access_token", "") if code == 200 else ""
        check("a second app connects", bool(second_access), f"status {code} {body[:120]}")
        check("both apps share one identity", subject_of(second_access) == first_subject != "")
        for label, bearer in (("the first app", fresh), ("the second app", second_access)):
            head = {"accept": accept, "Authorization": f"Bearer {bearer}"}
            status, body, _ = await run(
                lambda h=head: http("POST", "/mcp", probe, headers=h, as_json=True)
            )
            check(f"{label} still works after the second connects", status == 200,
                  f"got {status} {body[:90]}")

        # -- 11. a connection whose Garmin session is gone asks to reconnect
        # A 401 is what Claude turns into a Reconnect button; a 404 read as a
        # broken server.
        store.delete_user(first_subject)
        head = {"accept": accept, "Authorization": f"Bearer {fresh}"}
        status, body, _ = await run(
            lambda: http("POST", "/mcp", probe, headers=head, as_json=True)
        )
        check("a connection with no Garmin session behind it gets 401", status == 401,
              f"got {status} {body[:90]}")

        # -- 12. expired rows are cleared, live ones are not ---------------
        now = int(time.time())
        with __import__("sqlite3").connect(DB) as conn:
            conn.executemany(
                "INSERT INTO oauth_tokens (token_hash, kind, client_id, subject, scopes, "
                "resource, expires_at, created_at) VALUES (?, 'access', 'c', 's', '', NULL, ?, ?)",
                [("stale", now - 10, now - 3700), ("live", now + 3600, now)],
            )
        removed = store.sweep_expired_oauth()
        with __import__("sqlite3").connect(DB) as conn:
            left = dict(conn.execute(
                "SELECT token_hash, COUNT(*) FROM oauth_tokens "
                "WHERE token_hash IN ('stale', 'live') GROUP BY token_hash"
            ).fetchall())
        check("expired tokens are swept", removed >= 1 and "stale" not in left, str(removed))
        check("a live token survives the sweep", left.get("live") == 1)

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
