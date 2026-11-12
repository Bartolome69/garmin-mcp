"""Sign-ups on Garmin's page pause themselves when they stop working (Nov 12).

Run: .venv/bin/python tests/signup_guard_test.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_DB"] = str(Path(tempfile.mkdtemp()) / "guard.sqlite3")
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()

from garmin_mcp import signup_guard as g  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


told: list[tuple[str, list]] = []
g.on_pause = lambda why, failures: told.append((why, failures))
SWITCH = "open"
T = 1_800_000_000.0

print("Sent back by Garmin")
for i in range(2):
    g.started(f"m{i}", SWITCH, T + i)
    g.finished(f"m{i}", False, SWITCH, "sent back", T + i + 5)
check("two failures in a row don't pause", not g.paused(SWITCH) and not told)
g.started("ok", SWITCH, T + 10)
g.finished("ok", True, SWITCH, now=T + 20)
check("an approval clears the count", g.state()["failures"] == 0)
for i in range(3):
    g.started(f"n{i}", SWITCH, T + 30 + i)
    paused_now = g.finished(f"n{i}", False, SWITCH, "sent back", T + 35 + i)
check("three in a row pause sign-ups on Garmin's page", g.paused(SWITCH) and paused_now)
check("and say so once, with the three failures", len(told) == 1 and len(told[0][1]) == 3, str(told))
g.started("n3", SWITCH, T + 40)
g.finished("n3", False, SWITCH, "sent back", T + 41)
check("a fourth doesn't say it again", len(told) == 1)
check("the pause is kept in the database, so a restart keeps it", g._load().get("paused_under") == SWITCH)
check("a new 'open' value is a deliberate reopening: not paused under it", not g.paused("open-2026-11-20"))
check("nor while switched off altogether", not g.paused("paused"))

print("Never came back")
g.reset()
told.clear()
for i in range(3):
    g.started(f"p{i}", SWITCH, T + i)
check("waiting less than the limit isn't a failure", not g.sweep(SWITCH, T + g.WAIT_SECONDS - 10) and not g.paused(SWITCH))
check("three that never came back pause it", g.sweep(SWITCH, T + g.WAIT_SECONDS + 10) and g.paused(SWITCH) and len(told) == 1)

print("What isn't counted")
g.reset()
told.clear()
check("a sign-up it didn't send (an 'again' one) is ignored", not g.finished("stranger", False, SWITCH) and g.state()["failures"] == 0)
g.started("old", SWITCH, T)
g.sweep(SWITCH, T + g.FORGET_SECONDS + 1)
check("one left from long ago (the server was down) is dropped, not counted", g.state() == {
    "paused_under": None, "paused_at": None, "failures": 0, "waiting": 0}, str(g.state()))
check("an empty mark is ignored", (g.started("", SWITCH), g.state()["waiting"])[1] == 0)

print()
if FAILED:
    print(f"{len(FAILED)} failed: " + "; ".join(FAILED))
    sys.exit(1)
print("all checks passed")
