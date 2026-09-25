"""Check an installation and say exactly what is wrong with it.

When something breaks, Claude reports "I can't access Garmin" and nothing more,
because a server that failed to start looks identical to one that was never
configured. This walks the chain from the interpreter to a live tool call and
names the first broken link.

    .venv/bin/python -m garmin_mcp.doctor
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
TOKENS = Path(os.environ.get("GARMIN_MCP_TOKENS") or Path.home() / ".garmin-mcp/tokens.json")

OK, WARN, BAD = "ok  ", "warn", "FAIL"
problems: list[str] = []


def report(state: str, label: str, detail: str = "", fix: str = "") -> None:
    print(f"  {state}  {label}" + (f" — {detail}" if detail else ""))
    if state == BAD:
        problems.append(f"{label}: {fix or detail}")


def claude_is_running() -> bool:
    try:
        out = subprocess.run(["ps", "-Ao", "comm="], capture_output=True, text=True).stdout
    except OSError:
        return False
    return "/Applications/Claude.app/Contents/MacOS/Claude" in out.split("\n")


def check_python() -> bool:
    interpreter = PROJECT / ".venv/bin/python"
    if not interpreter.exists():
        report(BAD, "Python environment", "missing",
               f"run {PROJECT}/scripts/setup.sh")
        return False
    version = platform.mac_ver()[0] or "unknown"
    report(OK, "Python environment", str(interpreter))
    report(OK, "macOS", version)
    return True


def check_config() -> None:
    if not CONFIG.exists():
        report(BAD, "Claude Desktop config", "file not found",
               "open Claude Desktop once, then run scripts/install-claude-desktop.sh")
        return
    try:
        servers = json.loads(CONFIG.read_text()).get("mcpServers", {})
    except ValueError:
        report(BAD, "Claude Desktop config", "not valid JSON",
               "fix or delete the file, then re-run scripts/install-claude-desktop.sh")
        return

    entry = servers.get("garmin")
    if not entry:
        report(BAD, "Claude Desktop config", "no 'garmin' entry",
               "quit Claude Desktop, then run scripts/install-claude-desktop.sh")
        return

    command = entry.get("command", "")
    if not Path(command).exists():
        report(BAD, "Claude Desktop config", f"command does not exist: {command}",
               "quit Claude Desktop, then run scripts/install-claude-desktop.sh")
        return
    report(OK, "Claude Desktop config", "'garmin' entry points at a real interpreter")


def check_tokens() -> None:
    if not TOKENS.exists():
        report(WARN, "Garmin session", "not signed in yet",
               f"run {PROJECT}/scripts/login.sh")
        return
    age_h = (time.time() - TOKENS.stat().st_mtime) / 3600
    mode = oct(TOKENS.stat().st_mode & 0o777)
    if mode != "0o600":
        report(WARN, "Garmin session", f"file permissions are {mode}, expected 0o600")
    else:
        report(OK, "Garmin session", f"cached {age_h:.0f}h ago, owner-only")


async def check_server() -> None:
    """Start the server exactly as Claude Desktop would, and call a tool."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=str(PROJECT / ".venv/bin/python"),
        args=["-m", "garmin_mcp"],
        env={**os.environ, "PYTHONPATH": str(PROJECT)},
        cwd=str(PROJECT),
    )
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                report(OK, "Server starts", f"{len(tools.tools)} tools offered")

                result = await session.call_tool("get_connection_status", {})
                payload = json.loads(result.content[0].text)
                if payload.get("authenticated"):
                    who = payload.get("garmin_display_name") or "your account"
                    report(OK, "Garmin reachable", f"signed in as {who}")
                else:
                    report(BAD, "Garmin reachable", payload.get("error", "not signed in"),
                           f"run {PROJECT}/scripts/login.sh")
    except Exception as exc:  # noqa: BLE001
        report(BAD, "Server starts", f"{type(exc).__name__}: {exc}",
               f"run {PROJECT}/scripts/setup.sh, then try again")


def main() -> int:
    import logging

    logging.disable(logging.INFO)
    print("\nChecking your Garmin connection\n")
    if check_python():
        check_config()
        check_tokens()
        asyncio.run(check_server())

    print()
    if claude_is_running():
        print("  note  Claude Desktop is running. If you change anything below, quit")
        print("        it fully (Cmd-Q) first — it overwrites its own settings on exit.")
        print()

    if not problems:
        print("Everything checks out. If Claude still can't see Garmin, quit Claude")
        print("Desktop completely, reopen it, and start a NEW chat — an existing chat")
        print("keeps talking to the server it started with.\n")
        return 0

    print("Found a problem:\n")
    for item in problems:
        print(f"  - {item}")
    print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
