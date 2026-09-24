"""End-to-end test of the hosted server, against a stubbed Garmin account.

The thing worth proving here is isolation: two people's connector URLs must
resolve to their own Garmin session. Everything else the local suite covers.

    .venv/bin/python tests/hosted_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
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
os.environ.setdefault("GARMIN_EMAIL", "test@example.com")
os.environ.setdefault("GARMIN_PASSWORD", "hunter2")

from tests.fake_garmin import PROFILE, FakeGarmin  # noqa: E402

import garmin_mcp.hosted as hosted  # noqa: E402
from garmin_mcp import store  # noqa: E402

# Each stub reports which account it belongs to, so we can prove one person's
# URL never reaches another person's session.
BUILT: list[str] = []
SIGNIN_CREDS: list[tuple] = []
SECRET = "never-show-this-blob-value"


def fake_build_client(*, prompt_mfa, email=None, password=None):
    SIGNIN_CREDS.append((email, password))
    class Stub(FakeGarmin):
        def login(self, tokenstore=None):
            BUILT.append(tokenstore or "no-token")
            super().login(tokenstore)
            # Identify which account answered, without echoing the blob —
            # otherwise the leak assertions below would trip on the harness.
            try:
                marker = json.loads(tokenstore or "{}").get("di_token", "none")
            except ValueError:
                marker = "unparsed"
            self.full_name = f"account:{marker}"
            return (None, None)

        def get_user_profile(self):
            return {**PROFILE, "fullName": self.full_name}

    return Stub()


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
    alice = store.save_user(
        "a***@example.com", json.dumps({"di_token": "alice-token", "secret": SECRET})
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
        code, body = await anyio_run(fetch, "/connect")
        check("sign-in form serves",
              code == 200 and "type=password" in body.replace('"', ""))

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
