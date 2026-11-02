"""Run the companion app locally, against a demo Garmin account.

    .venv/bin/python scripts/app_dev.py            # http://localhost:8787
    DEMO_RAN=1 .venv/bin/python scripts/app_dev.py  # today's session already run
    DEMO_READINESS=low ...                          # a poor night

It is the real hosted server, with OAuth on, and only the Garmin client
swapped for tests/demo_garmin.py, so the app's sign-in, its API and every
edit run through production code. It prints a link that is already signed in
as the demo runner; signing in through the app's own button works too, with
any email and password, and lands on the same demo account. Nothing here
reaches Garmin.
"""

from __future__ import annotations

import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORT = int(os.environ.get("PORT", "8787"))
SCRATCH = Path(os.environ.get("TMPDIR", "/tmp")) / "daash-app-dev"
SCRATCH.mkdir(parents=True, exist_ok=True)

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("GARMIN_MCP_DB", str(SCRATCH / f"dev-{int(time.time())}.sqlite3"))
os.environ.setdefault("GARMIN_MCP_SECRET", Fernet.generate_key().decode())
os.environ["GARMIN_MCP_OAUTH"] = "1"
os.environ["GARMIN_MCP_BASE_URL"] = f"http://localhost:{PORT}"
os.environ["PORT"] = str(PORT)
os.environ.setdefault("GARMIN_MCP_HOST", "127.0.0.1")
os.environ.pop("GARMIN_MCP_POSTHOG_KEY", None)

from garmin_mcp import session as session_mod  # noqa: E402
from tests import fake_garmin  # noqa: E402
from tests.demo_garmin import DemoGarmin  # noqa: E402

DEMO = DemoGarmin()
# What a sign-in stores as the Garmin session, for the app's own sign-in button.
fake_garmin._FakeHttp.dumps = lambda self: '{"di_token": "demo"}'
session_mod.build_client = lambda **_: DEMO

import garmin_mcp.hosted as hosted  # noqa: E402
from garmin_mcp import store  # noqa: E402


def main() -> None:
    email = "demo@daash.run"
    user_token = store.save_user(session_mod.mask_email(email), '{"di_token": "demo"}',
                                 email_hash=store.email_fingerprint(email),
                                 email=email)
    token = secrets.token_urlsafe(32)
    store.save_oauth_token(token, kind="access", client_id="daash-app-dev", subject=user_token,
                           scopes="", resource=None, expires_at=int(time.time()) + 30 * 86400)
    print(f"\n  Daash app, signed in as the demo runner:\n  http://localhost:{PORT}/app/#dev-token={token}\n", flush=True)
    hosted.main()


if __name__ == "__main__":
    main()
