"""After a restart nobody has to connect again (Nov 25).

Each person's Garmin session lives in SQLite, encrypted with the server key;
the session in memory is only a cache of it. This starts a "server", makes
calls, throws every in-memory session away as a restart (or the 30-minute
idle sweep) does, and checks the next calls carry on from the stored token,
including a token Garmin renewed before the restart.

    python tests/restart_test.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = tempfile.mkdtemp()
os.environ["GARMIN_MCP_DB"] = str(Path(tmp) / "restart-test.sqlite3")
from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()
os.environ.pop("GARMIN_EMAIL", None)
os.environ.pop("GARMIN_PASSWORD", None)

from garmin_mcp import store  # noqa: E402

store.DB_PATH = Path(os.environ["GARMIN_MCP_DB"])
from garmin_mcp import hosted  # noqa: E402
from garmin_mcp import session as session_mod  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


GOOD = {"v1", "v2"}  # tokens Garmin accepts
logins: list[str] = []


class Inner:
    def __init__(self, outer):
        self.outer = outer

    def dumps(self):
        return self.outer.token


class FakeGarmin:
    """Resumes only from a token Garmin would accept; never has a password."""

    def __init__(self, **_):
        self.token = None
        self.client = Inner(self)

    def login(self, tokenstore=None):
        logins.append(tokenstore)
        if tokenstore not in GOOD:
            from garminconnect import GarminConnectAuthenticationError
            raise GarminConnectAuthenticationError("401 Unauthorized")
        self.token = tokenstore

    def get_user_profile(self):
        return {"ok": True}

    def renew(self):
        self.token = "v2"  # Garmin hands back a new token mid-session
        return {"renewed": True}


session_mod.build_client = lambda **kw: FakeGarmin(**kw)

token = store.save_user("r***@example.com", "v1", email_hash="fp-restart")

print("stored at rest")
raw = sqlite3.connect(os.environ["GARMIN_MCP_DB"]).execute(
    "SELECT token_blob FROM users WHERE user_token = ?", (token,)).fetchone()[0]
check("the Garmin session is encrypted in the database", b"v1" not in bytes(raw) and store.load_blob(token) == "v1")

print("\nbefore the restart")
check("a call works", hosted.session_for(token).run(lambda c: c.get_user_profile()) == {"ok": True})
hosted.session_for(token).run(lambda c: c.renew())
check("a token Garmin renews mid-session is saved at once", store.load_blob(token) == "v2", store.load_blob(token))

print("\nthe restart")
hosted._SESSIONS.clear()
hosted._SESSION_USED.clear()
logins.clear()
answer = hosted.session_for(token).run(lambda c: c.get_user_profile())
check("the next call carries on, with no sign-in asked of the person", answer == {"ok": True}, str(answer))
check("resumed from the renewed token in the database", logins == ["v2"], str(logins))

print("\nthe 30-minute idle sweep")
hosted.session_for(token)
hosted._SESSION_USED[token] = time.monotonic() - hosted.SESSION_IDLE_S - 5
check("drops the session from memory", hosted.sweep_sessions() >= 1 and token not in hosted._SESSIONS)
logins.clear()
check("and the next call rebuilds it the same way", hosted.session_for(token).run(lambda c: c.get_user_profile())
      == {"ok": True} and logins == ["v2"], str(logins))

print("\nofficial API links")
store.save_official_link(token, "garmin-user-1", {"access_token": "acc-secret", "refresh_token": "ref-secret",
                                                  "access_expires_at": time.time() + 3600,
                                                  "refresh_expires_at": time.time() + 86400 * 90}, ["ACTIVITY_EXPORT"])
raw = sqlite3.connect(os.environ["GARMIN_MCP_DB"]).execute(
    "SELECT tokens_enc FROM official_links WHERE user_token = ?", (token,)).fetchone()[0]
check("its tokens are encrypted in the database too", b"acc-secret" not in bytes(raw) and b"ref-secret" not in bytes(raw))
link = store.official_link(token)
check("and read back whole after a restart (nothing about them is held only in memory)",
      link is not None and link.tokens["access_token"] == "acc-secret", json.dumps(link.tokens if link else None)[:80])

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
