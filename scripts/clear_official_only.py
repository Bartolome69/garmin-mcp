"""Put accounts that signed in with a password back on Garmin Connect for their reads.

Someone who approved Daash on Garmin's own page and then signed in with their
Garmin password kept the official-only setting from the first, so their tools
read only what Garmin had pushed since they connected and none of their history.
The connector signs them in over the account the approval made, writing their
session into it, but the account stayed marked as the official route, which
reads as "no Garmin Connect session behind it". Signing in now fixes that
(store.save_user); this is for the ones it already caught.

Runs on the hosted machine, where GARMIN_MCP_DB and GARMIN_MCP_SECRET are set.
Standard library, plus cryptography (already installed there) to tell a stored
session from an empty one, so it can be copied there on its own:

    python3 clear_official_only.py                       # list who is caught, change nothing
    python3 clear_official_only.py --email joe@example.com            # that one, change nothing
    python3 clear_official_only.py --email joe@example.com --apply    # fix that one
    python3 clear_official_only.py --apply                            # fix everyone listed
    python3 clear_official_only.py --linked 2026-10-28T20:30 2026-10-28T21:00   # who linked then (UTC), any state

An account is caught when its official link is set official-only and a Garmin
session is stored for it: a real one, not the empty one an approval on Garmin's
page leaves. Fixing it takes the account off the official route and clears the
setting. The link, what Garmin pushed and the session stay as they are, and
`garmin_official` with action "official_only" turns the setting back on. Prints
masked addresses and the first characters of each token, never anything secret.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sqlite3
import sys
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

# Every account set official-only; whether it is caught depends on its session.
FLAGGED = (
    "SELECT u.user_token, u.email_masked, l.garmin_user_id, l.created_at, u.email_hash, "
    "COALESCE(u.route, ''), u.token_blob "
    "FROM official_links l JOIN users u ON u.user_token = l.user_token "
    "WHERE l.official_only = 1"
)


def fingerprint(email: str) -> str:
    """The same handle store.email_fingerprint makes."""
    key = os.environ.get("GARMIN_MCP_SECRET", "").encode()
    return hmac.new(key, email.strip().lower().encode(), hashlib.sha256).hexdigest()


def has_session(blob: bytes | str | None) -> bool:
    """Whether a real Garmin session is stored: an approval on Garmin's page alone stores an empty one."""
    if not blob:
        return False
    key = os.environ.get("GARMIN_MCP_SECRET", "").strip().encode()
    try:
        return len(Fernet(key).decrypt(blob if isinstance(blob, bytes) else blob.encode())) > 0
    except (InvalidToken, ValueError):
        return False


def caught(conn: sqlite3.Connection) -> list[tuple]:
    return [r[:6] for r in conn.execute(FLAGGED).fetchall() if has_session(r[6])]


def list_linked(conn: sqlite3.Connection, start: str, end: str) -> int:
    """Everyone whose official link was made in a window: for finding someone without their address."""
    try:
        lo = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
        hi = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        print("Times look like 2026-10-28T20:30, in UTC.")
        return 1
    rows = conn.execute(
        "SELECT u.user_token, u.email_masked, COALESCE(u.route, ''), l.official_only, l.created_at, u.token_blob, "
        "(SELECT COUNT(*) FROM users o WHERE o.email_hash = u.email_hash AND u.email_hash IS NOT NULL) "
        "FROM official_links l JOIN users u ON u.user_token = l.user_token "
        "WHERE l.created_at BETWEEN ? AND ? ORDER BY l.created_at", (lo, hi)).fetchall()
    for token, masked, route, only, created, blob, same in rows:
        when = datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{token[:6]}…  {masked:<28} route={route or 'password':<9} official_only={only!r:<5} "
              f"session={'yes' if has_session(blob) else 'no':<3} accounts_with_this_email={same}  linked {when} UTC")
    print(f"{len(rows)} linked in that window. Nothing changed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", help="only the account with this Garmin email")
    parser.add_argument("--apply", action="store_true", help="make the change; without it nothing is written")
    parser.add_argument("--linked", nargs=2, metavar=("FROM", "TO"),
                        help="list every account whose official link was made between two UTC times "
                             "(2026-10-28T20:30), whatever its state; read only")
    args = parser.parse_args()

    path = os.environ.get("GARMIN_MCP_DB", "/data/garmin-mcp.sqlite3")
    if not os.path.exists(path):
        print(f"No database at {path}.")
        return 1
    if not os.environ.get("GARMIN_MCP_SECRET"):
        print("GARMIN_MCP_SECRET isn't set here, so sessions can't be read and addresses can't be matched.")
        return 1

    conn = sqlite3.connect(path)
    if args.linked:
        return list_linked(conn, *args.linked)
    rows = caught(conn)
    if args.email:
        wanted = fingerprint(args.email)
        rows = [r for r in rows if r[4] == wanted]
        if not rows:
            seen = conn.execute(
                "SELECT l.official_only, COALESCE(u.route, ''), u.token_blob FROM official_links l "
                "JOIN users u ON u.user_token = l.user_token WHERE u.email_hash = ?", (wanted,)).fetchall()
            print("Not caught. " + (f"Their account: official_only={seen[0][0]!r}, route={seen[0][1]!r}, "
                                    f"session={'yes' if has_session(seen[0][2]) else 'no'}."
                                    if seen else "No account or official link for that address."))
            return 1

    if not rows:
        print("Nobody is caught.")
        return 0
    for token, masked, garmin_user, created, _, route in rows:
        print(f"{token[:6]}…  {masked}  garmin user {garmin_user}  route={route or 'password'}  linked {created}")
    print(f"{len(rows)} caught.")

    if not args.apply:
        print("Nothing changed. Run again with --apply to fix these.")
        return 0
    with conn:
        for token, *_ in rows:
            conn.execute("UPDATE users SET route = NULL WHERE user_token = ?", (token,))
            conn.execute("UPDATE official_links SET official_only = NULL WHERE user_token = ?", (token,))
    done = {r[0] for r in rows}
    left = [r for r in caught(conn) if r[0] in done]
    print(f"Fixed {len(rows) - len(left)}.")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
