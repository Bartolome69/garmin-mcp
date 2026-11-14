"""Put each connected person's account email on their PostHog profile, once.

Since Nov 14 the connector sets the email on the person with one $identify
when someone connects, and never on an event. This does the same for everyone
who connected before that, from the store, matched by the same distinct id the
connector's events use (analytics.distinct_id of the email fingerprint, or of
the token for the oldest rows that have none).

Runs on the hosted machine, where GARMIN_MCP_DB, GARMIN_MCP_SECRET and
GARMIN_MCP_POSTHOG_KEY are set. Standard library plus cryptography, so it can be
copied there on its own (.github/workflows/posthog-email-backfill.yml does):

    python3 posthog_backfill_emails.py            # dry run: counts only, sends nothing
    python3 posthog_backfill_emails.py --apply    # send the $identify calls

Prints counts only, never an address, a token or an id: the workflow's log is
public. Sets one person property, email, and nothing else. Running it twice
does no harm: the second run sets the same value again.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
import urllib.request

from cryptography.fernet import Fernet, InvalidToken

HOST = (os.environ.get("GARMIN_MCP_POSTHOG_HOST", "").strip().rstrip("/") or "https://eu.i.posthog.com")
BATCH = 100


def distinct_id(secret: str, subject: str) -> str:
    """Exactly garmin_mcp.analytics.distinct_id, so the profile is the one the events belong to."""
    return hmac.new(secret.encode(), b"posthog:" + subject.encode(), hashlib.sha256).hexdigest()[:32]


def people(db: str, secret: str) -> tuple[dict[str, str], dict[str, int]]:
    """{distinct id: account email} for every connected person, newest row winning, and the counts."""
    cipher = Fernet(secret.encode())
    counts = {"rows": 0, "without_email": 0, "unreadable": 0, "no_fingerprint": 0}
    found: dict[str, tuple[int, str]] = {}
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT user_token, email_hash, email_enc, COALESCE(last_seen_at, created_at) FROM users"
        ).fetchall()
    for token, fingerprint, encrypted, at in rows:
        counts["rows"] += 1
        if not encrypted:
            counts["without_email"] += 1
            continue
        try:
            email = cipher.decrypt(encrypted).decode().strip()
        except (InvalidToken, ValueError):
            counts["unreadable"] += 1
            continue
        if not fingerprint:
            counts["no_fingerprint"] += 1
        who = distinct_id(secret, fingerprint or token)
        if who not in found or (at or 0) > found[who][0]:
            found[who] = (at or 0, email)
    return {who: email for who, (_, email) in found.items()}, counts


def send(key: str, batch: list[dict]) -> bool:
    request = urllib.request.Request(
        f"{HOST}/batch/", data=json.dumps({"api_key": key, "batch": batch}).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return 200 <= response.status < 300
    except Exception as exc:  # noqa: BLE001 - counted, and the run carries on
        print(f"a batch failed: {type(exc).__name__}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="send the $identify calls (default: dry run)")
    args = parser.parse_args()
    db = os.environ.get("GARMIN_MCP_DB") or "/data/garmin-mcp.sqlite3"
    secret = os.environ.get("GARMIN_MCP_SECRET", "").strip()
    key = os.environ.get("GARMIN_MCP_POSTHOG_KEY", "").strip()
    if not secret:
        print("GARMIN_MCP_SECRET is not set: run this on the hosted machine.")
        return 1
    found, counts = people(db, secret)
    print(f"accounts in the store: {counts['rows']}")
    print(f"  without a kept email: {counts['without_email']}")
    print(f"  email unreadable: {counts['unreadable']}")
    print(f"  oldest rows matched by token (no fingerprint): {counts['no_fingerprint']}")
    print(f"people to set (one per PostHog id, newest row wins): {len(found)}")
    print(f"distinct email domains: {len({e.rsplit('@', 1)[-1].lower() for e in found.values()})}")
    if not args.apply:
        print("dry run: nothing sent. Run with --apply to set the email on these profiles.")
        return 0
    if not key:
        print("GARMIN_MCP_POSTHOG_KEY is not set: nothing sent.")
        return 1
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    events = [{"event": "$identify", "distinct_id": who, "timestamp": now,
               "properties": {"$set": {"email": email}, "$process_person_profile": True,
                              "$lib": "daash-email-backfill"}}
              for who, email in found.items()]
    sent = failed = 0
    for start in range(0, len(events), BATCH):
        chunk = events[start:start + BATCH]
        if send(key, chunk):
            sent += len(chunk)
        else:
            failed += len(chunk)
    print(f"set: {sent}, failed: {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
