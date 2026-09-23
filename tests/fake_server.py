"""Run the real MCP server, but against the stubbed Garmin account.

Only the client factory is swapped, so the transport, the tool registry, the
session handling and the response shaping are all the production code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from garmin_mcp import session as session_mod  # noqa: E402
from tests.fake_garmin import FakeGarmin  # noqa: E402

os.environ.setdefault("GARMIN_EMAIL", "test@example.com")
os.environ.setdefault("GARMIN_PASSWORD", "hunter2")

session_mod.build_client = lambda **_: FakeGarmin()

from garmin_mcp.server import main  # noqa: E402

main()
