"""Put accounts that signed in with a password back on Garmin Connect for their reads.

Someone who approved Daash on Garmin's own page and then signed in with their
Garmin password kept the official-only setting from the first, so their tools
read only what Garmin had pushed since they connected and none of their history.
Signing in now clears it (store.save_user); this is for the ones it already caught.

Runs on the hosted machine, where GARMIN_MCP_DB and GARMIN_MCP_SECRET are set,
and uses only the standard library, so it can be copied there on its own:

    python3 clear_official_only.py                       # list who is caught, change nothing
    python3 clear_official_only.py --email joe@example.com            # that one, change nothing
    python3 clear_official_only.py --email joe@example.com --apply    # clear that one
    python3 clear_official_only.py --apply                            # clear everyone listed

An account is caught when it has a Garmin Connect session (its route isn't
"official") and its official link is still set official-only. Only that one
setting changes: the link, what Garmin pushed and the session stay as they are,
and `garmin_official` with action "official_only" turns it back on. Prints
masked addresses and the first characters of each token, never anything secret.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sqlite3
import sys

CAUGHT = (
    "SELECT u.user_token, u.email_masked, l.garmin_user_id, l.created_at, u.email_hash "
    "FROM official_links l JOIN users u ON u.user_token = l.user_token "
    "WHERE l.official_only = 1 AND COALESCE(u.route, '') != 'official'"
)


def fingerprint(email: str) -> str:
    """The same handle store.email_fingerprint makes."""
    key = os.environ.get("GARMIN_MCP_SECRET", "").encode()
    return hmac.new(key, email.strip().lower().encode(), hashlib.sha256).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", help="only the account with this Garmin email")
    parser.add_argument("--apply", action="store_true", help="make the change; without it nothing is written")
    args = parser.parse_args()

    path = os.environ.get("GARMIN_MCP_DB", "/data/garmin-mcp.sqlite3")
    if not os.path.exists(path):
        print(f"No database at {path}.")
        return 1
    if args.email and not os.environ.get("GARMIN_MCP_SECRET"):
        print("GARMIN_MCP_SECRET isn't set here, so an address can't be matched.")
        return 1

    conn = sqlite3.connect(path)
    rows = conn.execute(CAUGHT).fetchall()
    if args.email:
        wanted = fingerprint(args.email)
        rows = [r for r in rows if r[4] == wanted]
        if not rows:
            seen = conn.execute(
                "SELECT l.official_only, COALESCE(u.route, '') FROM official_links l "
                "JOIN users u ON u.user_token = l.user_token WHERE u.email_hash = ?", (wanted,)).fetchall()
            print("Not caught. " + (f"Their account: official_only={seen[0][0]!r}, route={seen[0][1]!r}."
                                    if seen else "No account or official link for that address."))
            return 1

    if not rows:
        print("Nobody is caught.")
        return 0
    for token, masked, garmin_user, created, _ in rows:
        print(f"{token[:6]}…  {masked}  garmin user {garmin_user}  linked {created}")
    print(f"{len(rows)} caught.")

    if not args.apply:
        print("Nothing changed. Run again with --apply to clear the setting for these.")
        return 0
    with conn:
        for token, *_ in rows:
            conn.execute("UPDATE official_links SET official_only = NULL WHERE user_token = ?", (token,))
    left = [r for r in conn.execute(CAUGHT).fetchall() if r[0] in {t[0] for t in rows}]
    print(f"Cleared {len(rows) - len(left)}.")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
