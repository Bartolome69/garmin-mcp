"""Pull real data from Garmin through the same code path the MCP tools use.

A quick "is this actually working?" before involving the Inspector or Claude
Desktop. Uses the cached session if there is one, so it needs no password once
`python -m garmin_mcp.login` has run.

    .venv/bin/python -m garmin_mcp.check
"""

from __future__ import annotations

import asyncio
import json
import sys

from . import server
from .session import TOKEN_FILE


def show(label: str, data: dict) -> bool:
    """Print a result; return False if it came back as an error."""
    if "error" in data:
        print(f"\n{label}: FAILED\n  {data['error']}")
        return False
    print(f"\n{label}:")
    print("  " + json.dumps(data, indent=2).replace("\n", "\n  "))
    return True


async def main() -> int:
    print(f"token cache: {TOKEN_FILE} ({'present' if TOKEN_FILE.exists() else 'none'})")

    status = await server.get_connection_status()
    if not show("connection", status) or not status.get("authenticated"):
        print(
            "\nNot connected. Either set GARMIN_EMAIL and GARMIN_PASSWORD, or run:"
            "\n  .venv/bin/python -m garmin_mcp.login"
        )
        return 1

    ok = True
    ok &= show("today's summary", await server.get_daily_summary())
    ok &= show("last night's sleep", await server.get_sleep_data())
    ok &= show("recent activities", await server.get_activities(limit=3))

    activities = (await server.get_activities(limit=1)).get("activities") or []
    if activities:
        first = activities[0]
        print(f"\nfetching details for activity {first['activity_id']} ...")
        ok &= show("activity details", await server.get_activity_details(
            first["activity_id"]
        ))

    print("\n" + ("Everything worked." if ok else "Some calls failed — see above."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
