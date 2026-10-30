"""Encrypted store of Garmin sessions, one row per person.

Only used by the hosted server. The local stdio server keeps its single token
file and never touches this.

What is stored is the token blob Garmin issues and the email it was issued to,
both encrypted at rest. The password is never written: it is exchanged for
tokens during sign-in and discarded.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DB_PATH = Path(os.environ.get("GARMIN_MCP_DB") or "/data/garmin-mcp.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_token    TEXT PRIMARY KEY,
    email_masked  TEXT NOT NULL,
    token_blob    BLOB NOT NULL,
    created_at    INTEGER NOT NULL,
    last_seen_at  INTEGER
);
"""

# Added after the first deploy, so it arrives by migration rather than in SCHEMA.
MIGRATIONS = (
    "ALTER TABLE users ADD COLUMN email_hash TEXT",
    "CREATE INDEX IF NOT EXISTS users_email_hash ON users (email_hash)",
    # The address itself, encrypted like the token, so whoever runs this can
    # get in touch about a connection. Rows from before this column are NULL.
    "ALTER TABLE users ADD COLUMN email_enc BLOB",
    # When the server last renewed this Garmin session on its own, for someone
    # who had not used it in a while. Kept apart from last_seen_at, which
    # counts people actually using the connector.
    "ALTER TABLE users ADD COLUMN kept_alive_at INTEGER",
    # The latest what's-new note this person has been shown in a chat.
    "ALTER TABLE users ADD COLUMN news_seen INTEGER",
    # Set when someone asks for no more update emails.
    "ALTER TABLE users ADD COLUMN email_opt_out INTEGER",
    # Set once this person has been told how to stop Claude asking permission
    # for every read.
    "ALTER TABLE users ADD COLUMN tip_seen INTEGER",
    # Set while a preview account tries Daash on Garmin's official API alone:
    # its reads come only from what Garmin has pushed.
    "ALTER TABLE official_links ADD COLUMN official_only INTEGER",
    # "official" for someone who signed up by approving Daash on Garmin's own
    # page: there is no Garmin Connect session behind the row at all.
    "ALTER TABLE users ADD COLUMN route TEXT",
    # The Monday the weekly email last went to this person, as YYYY-MM-DD.
    "ALTER TABLE users ADD COLUMN weekly_sent TEXT",
)

# Bart's friends using Daash, named from a chat (garmin_official's friends
# actions): their PostHog profile is marked friend, so a cohort of them fills
# itself. Fingerprints only, the way sign-in recognises a returning person,
# and the masked address to tell the rows apart.
FRIENDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS friends (
    email_hash    TEXT PRIMARY KEY,
    email_masked  TEXT NOT NULL,
    added_at      INTEGER NOT NULL
);
"""

# OAuth state. Separate from `users`: a person is one row there however many
# clients they authorise, and revoking a client must not touch their Garmin
# session. `subject` on the token rows is the user_token that owns them.
OAUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id     TEXT PRIMARY KEY,
    client_json   TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_codes (
    code          TEXT PRIMARY KEY,
    code_json     TEXT NOT NULL,
    expires_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
    token_hash    TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,          -- 'access' or 'refresh'
    client_id     TEXT NOT NULL,
    subject       TEXT NOT NULL,
    scopes        TEXT NOT NULL,
    resource      TEXT,
    expires_at    INTEGER,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS oauth_tokens_subject ON oauth_tokens (subject);
CREATE INDEX IF NOT EXISTS oauth_tokens_client ON oauth_tokens (client_id);
CREATE TABLE IF NOT EXISTS email_codes (
    email_hash    TEXT PRIMARY KEY,
    code_hash     TEXT NOT NULL,
    sent_at       INTEGER NOT NULL,
    expires_at    INTEGER NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    sends         INTEGER NOT NULL DEFAULT 0,
    first_sent_at INTEGER NOT NULL DEFAULT 0
);
"""


# Garmin's official developer programme (official.py). A person's link to it
# is kept apart from `users`: it holds Garmin's OAuth tokens, not a session
# from the private sign-in, and Garmin identifies them by its own user id.
# Garmin pushes data rather than answering for it, so what it sends is kept
# here, encrypted, until the person disconnects.
OFFICIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS official_links (
    user_token      TEXT PRIMARY KEY,
    garmin_user_id  TEXT NOT NULL UNIQUE,
    tokens_enc      BLOB NOT NULL,
    permissions     TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS official_summaries (
    garmin_user_id  TEXT NOT NULL,
    kind            TEXT NOT NULL,
    summary_id      TEXT NOT NULL,
    start_time      INTEGER,
    data_enc        BLOB NOT NULL,
    received_at     INTEGER NOT NULL,
    PRIMARY KEY (garmin_user_id, kind, summary_id)
);
CREATE INDEX IF NOT EXISTS official_summaries_time
    ON official_summaries (garmin_user_id, kind, start_time);
CREATE TABLE IF NOT EXISTS official_backfill (
    garmin_user_id  TEXT PRIMARY KEY,
    anchor          INTEGER NOT NULL,
    progress        TEXT NOT NULL,
    done            INTEGER NOT NULL DEFAULT 0,
    updated_at      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS official_workouts (
    garmin_user_id  TEXT NOT NULL,
    workout_id      INTEGER NOT NULL,
    data_enc        BLOB NOT NULL,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY (garmin_user_id, workout_id)
);
CREATE TABLE IF NOT EXISTS official_schedules (
    garmin_user_id  TEXT NOT NULL,
    schedule_id     INTEGER NOT NULL,
    workout_id      INTEGER NOT NULL,
    day             TEXT NOT NULL,
    PRIMARY KEY (garmin_user_id, schedule_id)
);
CREATE INDEX IF NOT EXISTS official_schedules_day ON official_schedules (garmin_user_id, day);
"""


class StoreError(RuntimeError):
    """Something is wrong with the store's configuration."""


@dataclass(frozen=True)
class User:
    user_token: str
    email_masked: str
    created_at: int
    # The HMAC of the email, when the row has one. It outlives the token: a
    # fresh sign-in mints a new token but the same fingerprint, which is what
    # makes it the right thing to count a person by.
    email_hash: str | None = None


def _cipher() -> Fernet:
    key = os.environ.get("GARMIN_MCP_SECRET", "").strip()
    if not key:
        raise StoreError(
            "GARMIN_MCP_SECRET is not set. Generate one with:\n"
            "    python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise StoreError(
            "GARMIN_MCP_SECRET is not a valid Fernet key (44 url-safe base64 "
            "characters). Generate a fresh one rather than inventing a string."
        ) from exc


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.executescript(OAUTH_SCHEMA)
    conn.executescript(OFFICIAL_SCHEMA)
    conn.executescript(FRIENDS_SCHEMA)
    for statement in MIGRATIONS:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass  # Already applied.
    return conn


def new_user_token() -> str:
    """The URL is the credential, so it has to be unguessable."""
    return secrets.token_urlsafe(32)


def email_fingerprint(email: str) -> str:
    """A stable, non-reversible handle for one person's email address.

    Signing in again has to be able to find and revoke that person's previous
    URLs, which means recognising them across sign-ins. Storing the address to
    do that would undo the point of only keeping a masked copy, so this keeps an
    HMAC of it instead: enough to match, not enough to read.
    """
    key = os.environ.get("GARMIN_MCP_SECRET", "").encode()
    return hmac.new(key, email.strip().lower().encode(), hashlib.sha256).hexdigest()


def save_user(
    email_masked: str,
    token_blob: str,
    user_token: str | None = None,
    *,
    email_hash: str | None = None,
    email: str | None = None,
) -> str:
    """Store someone's Garmin session, retiring any earlier one. Returns their token.

    Signing in again used to mint a second URL and leave the first one working
    for ever, with no way to take it back. Now the old rows go, so a fresh
    sign-in is also how you revoke a link you have lost.
    """
    user_token = user_token or new_user_token()
    encrypted = _cipher().encrypt(token_blob.encode())
    email_enc = _cipher().encrypt(email.strip().encode()) if email and email.strip() else None
    now = int(time.time())
    with _connect() as conn:
        news_seen = opted_out = tip_seen = None
        if email_hash:
            # The person is the same, so what they have been told and what they
            # asked for outlives the row being replaced.
            news_seen, opted_out, tip_seen = conn.execute(
                "SELECT MAX(news_seen), MAX(email_opt_out), MAX(tip_seen) FROM users WHERE email_hash = ?",
                (email_hash,),
            ).fetchone()
            # A link to Garmin's official API belongs to the person, not the
            # row, so it moves to the new one rather than going with the old.
            conn.execute(
                "UPDATE OR IGNORE official_links SET user_token = ? WHERE user_token IN "
                "(SELECT user_token FROM users WHERE email_hash = ? AND user_token != ?)",
                (user_token, email_hash, user_token),
            )
            conn.execute(
                "DELETE FROM users WHERE email_hash = ? AND user_token != ?",
                (email_hash, user_token),
            )
        conn.execute(
            "INSERT INTO users (user_token, email_masked, token_blob, created_at, email_hash, email_enc, "
            "news_seen, email_opt_out, tip_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_token) DO UPDATE SET "
            "  token_blob = excluded.token_blob, email_masked = excluded.email_masked, "
            "  email_hash = excluded.email_hash, "
            "  email_enc = COALESCE(excluded.email_enc, users.email_enc), "
            "  route = NULL",
            (user_token, email_masked, encrypted, now, email_hash, email_enc, news_seen, opted_out, tip_seen),
        )
        # A Garmin Connect session stands behind the row now, so it reads from
        # Connect: full history, readiness, gear. Oct 30: someone who approved
        # Daash on Garmin's page, then signed in with a password on their phone,
        # kept the official-only mark and so read nothing from before that day.
        conn.execute("UPDATE official_links SET official_only = NULL WHERE user_token = ?", (user_token,))
    return user_token


def repair_password_on_official() -> int:
    """Lift the official-only mark from rows a password sign-in has since joined.

    Before save_user cleared it (Oct 30), such a row kept route 'official' with
    a Connect session stored and unused. Returns how many were repaired.
    """
    cipher = _cipher()
    with _connect() as conn:
        rows = conn.execute("SELECT user_token, token_blob FROM users WHERE route = 'official'").fetchall()
        joined = []
        for user_token, blob in rows:
            try:
                if cipher.decrypt(blob):
                    joined.append(user_token)
            except InvalidToken:
                continue
        for user_token in joined:
            conn.execute("UPDATE users SET route = NULL WHERE user_token = ?", (user_token,))
            conn.execute("UPDATE official_links SET official_only = NULL WHERE user_token = ?", (user_token,))
        # A password sign-in that made a fresh row carried the link over with
        # its mark still on (the same morning, on a phone). Once only, so the
        # preview's own official-only switch isn't undone at every start.
        conn.execute("CREATE TABLE IF NOT EXISTS repairs (name TEXT PRIMARY KEY, done_at INTEGER)")
        if conn.execute("INSERT OR IGNORE INTO repairs VALUES ('official_only_carried', ?)",
                        (int(time.time()),)).rowcount:
            joined += [row[0] for row in conn.execute(
                "SELECT l.user_token FROM official_links l JOIN users u ON u.user_token = l.user_token "
                "WHERE l.official_only = 1 AND COALESCE(u.route, '') != 'official'").fetchall()]
            conn.execute(
                "UPDATE official_links SET official_only = NULL WHERE official_only = 1 AND user_token IN "
                "(SELECT user_token FROM users WHERE COALESCE(route, '') != 'official')")
    return len(joined)


def add_friends(emails: list[tuple[str, str]]) -> int:
    """Name (address, masked address) pairs as Bart's friends. Returns how many were new."""
    now = int(time.time())
    with _connect() as conn:
        return sum(conn.execute("INSERT OR IGNORE INTO friends VALUES (?, ?, ?)",
                                (email_fingerprint(email), masked, now)).rowcount for email, masked in emails)


def remove_friends(emails: list[str]) -> int:
    with _connect() as conn:
        return sum(conn.execute("DELETE FROM friends WHERE email_hash = ?", (email_fingerprint(e),)).rowcount
                   for e in emails)


def friends() -> list[tuple[str, str]]:
    """Every friend as (fingerprint, masked address), oldest first."""
    with _connect() as conn:
        return [(h, m) for h, m in conn.execute("SELECT email_hash, email_masked FROM friends ORDER BY added_at")]


def email_hash_for(user_token: str) -> str | None:
    """The per-person handle behind one URL token, or None for an unknown token."""
    with _connect() as conn:
        row = conn.execute("SELECT email_hash FROM users WHERE user_token = ?", (user_token,)).fetchone()
    return row[0] if row and row[0] else None


def tokens_for(email_hash: str) -> list[str]:
    """Every URL token currently issued to one person, newest last."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_token FROM users WHERE email_hash = ? ORDER BY created_at",
            (email_hash,),
        ).fetchall()
    return [row[0] for row in rows]


def delete_user(user_token: str) -> bool:
    """Forget someone entirely. Their URL stops working immediately."""
    with _connect() as conn:
        return conn.execute(
            "DELETE FROM users WHERE user_token = ?", (user_token,)
        ).rowcount > 0


def load_blob(user_token: str) -> str | None:
    """Decrypt someone's token blob, or None if we don't know them."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT token_blob FROM users WHERE user_token = ?", (user_token,)
        ).fetchone()
    if not row:
        return None
    try:
        return _cipher().decrypt(row[0]).decode()
    except InvalidToken:
        # The encryption key changed; the stored blob is unreadable. Treat it as
        # absent so the person is asked to sign in again.
        return None


def update_blob(user_token: str, token_blob: str, *, in_use: bool = True) -> None:
    """Persist a refreshed token so the next request resumes from it.

    in_use=False is the keep-alive renewing an idle session: the token is
    saved but the person is not counted as having used the connector.
    """
    encrypted = _cipher().encrypt(token_blob.encode())
    with _connect() as conn:
        if in_use:
            conn.execute(
                "UPDATE users SET token_blob = ?, last_seen_at = ? WHERE user_token = ?",
                (encrypted, int(time.time()), user_token),
            )
        else:
            conn.execute(
                "UPDATE users SET token_blob = ? WHERE user_token = ?",
                (encrypted, user_token),
            )


def idle_users(idle_for: int) -> list[str]:
    """Connections neither used nor kept alive in the last `idle_for` seconds.

    Only those with a Garmin Connect session to keep alive: someone who signed
    up through Garmin's own page has none, and Garmin's tokens renew on use.
    """
    cutoff = int(time.time()) - idle_for
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_token FROM users "
            "WHERE COALESCE(last_seen_at, created_at) < ? AND COALESCE(kept_alive_at, 0) < ? "
            "AND COALESCE(route, '') != 'official' "
            "ORDER BY COALESCE(kept_alive_at, 0)",
            (cutoff, cutoff),
        ).fetchall()
    return [row[0] for row in rows]


def claim_news(user_token: str, version: int) -> bool:
    """True once per person per version: the caller shows the note, nobody else.

    One UPDATE that only matches while the note is still unseen, so two tool
    calls landing together can't both show it.
    """
    with _connect() as conn:
        return conn.execute(
            "UPDATE users SET news_seen = ? WHERE user_token = ? AND COALESCE(news_seen, 0) < ?",
            (version, user_token, version),
        ).rowcount > 0


def claim_tip(user_token: str) -> bool:
    """True the first time for each person, like claim_news, for the one-off tip."""
    with _connect() as conn:
        return conn.execute(
            "UPDATE users SET tip_seen = 1 WHERE user_token = ? AND COALESCE(tip_seen, 0) = 0",
            (user_token,),
        ).rowcount > 0


def unsubscribe_signature(email_hash: str) -> str:
    """What makes an unsubscribe link genuine: only this server can sign one."""
    key = os.environ.get("GARMIN_MCP_SECRET", "").encode()
    return hmac.new(key, f"unsubscribe:{email_hash}".encode(), hashlib.sha256).hexdigest()[:24]


def opt_out_of_email(email_hash: str) -> bool:
    """Stop update emails to this person. True if they were connected."""
    with _connect() as conn:
        return conn.execute(
            "UPDATE users SET email_opt_out = 1 WHERE email_hash = ?", (email_hash,)
        ).rowcount > 0


def emailable() -> list[tuple[str, str]]:
    """(email, email_hash) for everyone with an address kept who hasn't opted out."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT email_enc, email_hash FROM users "
            "WHERE email_enc IS NOT NULL AND email_hash IS NOT NULL AND COALESCE(email_opt_out, 0) = 0"
        ).fetchall()
    out = []
    for enc, fingerprint in rows:
        try:
            out.append((_cipher().decrypt(enc).decode(), fingerprint))
        except InvalidToken:
            continue
    return out


def weekly_recipients() -> list[tuple[str, str, str]]:
    """(user_token, email, email_hash) for each person who can be emailed, once each.

    One person can have several connections (Claude, ChatGPT); the email comes
    from the one they used last, and goes once.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_token, email_enc, email_hash FROM users "
            "WHERE email_enc IS NOT NULL AND email_hash IS NOT NULL AND COALESCE(email_opt_out, 0) = 0 "
            "ORDER BY COALESCE(last_seen_at, created_at) DESC"
        ).fetchall()
    out, seen = [], set()
    for user_token, enc, fingerprint in rows:
        if fingerprint in seen:
            continue
        try:
            email = _cipher().decrypt(enc).decode()
        except InvalidToken:
            continue
        seen.add(fingerprint)
        out.append((user_token, email, fingerprint))
    return out


def claim_weekly(email_hash: str, monday: str) -> bool:
    """True once per person per week: whoever claims it sends it, nobody else, restarts included."""
    with _connect() as conn:
        return conn.execute(
            "UPDATE users SET weekly_sent = ? WHERE email_hash = ? AND COALESCE(weekly_sent, '') < ?",
            (monday, email_hash, monday),
        ).rowcount > 0


def mark_kept_alive(user_token: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET kept_alive_at = ? WHERE user_token = ?",
            (int(time.time()), user_token),
        )


def get_user(user_token: str) -> User | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_token, email_masked, created_at, email_hash "
            "FROM users WHERE user_token = ?",
            (user_token,),
        ).fetchone()
    return User(*row) if row else None


def count_users() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def touch_user(user_token: str) -> bool:
    """Note that this person's connector was just used. True the first time.

    last_seen_at used to move only when a token refreshed, which is every few
    weeks at best. It now moves on use, so it can answer the one question the
    front page asks: how many people actually used this recently. The first
    touch is reported separately: it is the moment a sign-in turned into a
    connector that works.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_seen_at FROM users WHERE user_token = ?", (user_token,)
        ).fetchone()
        conn.execute(
            "UPDATE users SET last_seen_at = ? WHERE user_token = ?",
            (int(time.time()), user_token),
        )
    return bool(row) and row[0] is None


def email_for(user_token: str) -> str | None:
    """The address this connection was signed in with, where one was kept."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT email_enc FROM users WHERE user_token = ?", (user_token,)
        ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return _cipher().decrypt(row[0]).decode()
    except InvalidToken:
        return None


def count_active(days: int = 30) -> int:
    """People seen in the last `days` days. A fresh sign-in counts as seen."""
    cutoff = int(time.time()) - days * 86400
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE last_seen_at >= ? OR created_at >= ?",
            (cutoff, cutoff),
        ).fetchone()[0]


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------


def token_hash(token: str) -> str:
    """Tokens are stored hashed, so the database holds nothing usable.

    Unlike the Garmin blob, a bearer token needs no reversing: verifying one
    only means recognising it again. Anyone reading the database gets a list of
    hashes they cannot present to anything.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def save_oauth_client(client_id: str, client_json: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO oauth_clients (client_id, client_json, created_at) "
            "VALUES (?, ?, ?) ON CONFLICT(client_id) DO UPDATE SET "
            "  client_json = excluded.client_json",
            (client_id, client_json, int(time.time())),
        )


def load_oauth_client(client_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT client_json FROM oauth_clients WHERE client_id = ?", (client_id,)
        ).fetchone()
    return row[0] if row else None


def save_oauth_code(code: str, code_json: str, expires_at: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO oauth_codes (code, code_json, expires_at) "
            "VALUES (?, ?, ?)",
            (token_hash(code), code_json, expires_at),
        )


def take_oauth_code(code: str) -> str | None:
    """Read an authorization code and consume it. Single use, by construction."""
    digest = token_hash(code)
    now = int(time.time())
    with _connect() as conn:
        conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (now,))
        row = conn.execute(
            "SELECT code_json FROM oauth_codes WHERE code = ?", (digest,)
        ).fetchone()
        conn.execute("DELETE FROM oauth_codes WHERE code = ?", (digest,))
    return row[0] if row else None


def last_client(subject: str) -> str | None:
    """The client (Claude, ChatGPT…) this account was most recently connected from."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT client_id FROM oauth_tokens WHERE subject = ? ORDER BY created_at DESC LIMIT 1", (subject,)
        ).fetchone()
    return row[0] if row else None


def save_oauth_token(
    token: str,
    *,
    kind: str,
    client_id: str,
    subject: str,
    scopes: str,
    resource: str | None,
    expires_at: int | None,
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO oauth_tokens "
            "(token_hash, kind, client_id, subject, scopes, resource, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                token_hash(token), kind, client_id, subject, scopes,
                resource, expires_at, int(time.time()),
            ),
        )


def load_oauth_token(token: str, kind: str) -> dict | None:
    """Return a token's row, or None if unknown or expired."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT client_id, subject, scopes, resource, expires_at FROM oauth_tokens "
            "WHERE token_hash = ? AND kind = ?",
            (token_hash(token), kind),
        ).fetchone()
    if not row:
        return None
    client_id, subject, scopes, resource, expires_at = row
    if expires_at is not None and expires_at < time.time():
        delete_oauth_token(token)
        return None
    return {
        "client_id": client_id,
        "subject": subject,
        "scopes": scopes.split() if scopes else [],
        "resource": resource,
        "expires_at": expires_at,
    }


def peek_oauth_token(token: str, kind: str) -> dict | None:
    """A token's row even if it has expired, for saying why it was refused.

    Reads only: load_oauth_token is still what decides whether a token works.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT client_id, subject, expires_at, created_at FROM oauth_tokens "
            "WHERE token_hash = ? AND kind = ?",
            (token_hash(token), kind),
        ).fetchone()
    if not row:
        return None
    client_id, subject, expires_at, created_at = row
    return {"client_id": client_id, "subject": subject, "expires_at": expires_at, "created_at": created_at}


def expire_oauth_token_by(token: str, at: int) -> None:
    """Bring a token's expiry forward to `at`, never push it back."""
    with _connect() as conn:
        conn.execute(
            "UPDATE oauth_tokens SET expires_at = ? "
            "WHERE token_hash = ? AND (expires_at IS NULL OR expires_at > ?)",
            (at, token_hash(token), at),
        )


def sweep_expired_oauth() -> int:
    """Delete expired tokens and codes. They are refused already; this is space.

    An hour's access token and a rotated refresh token are only deleted when
    presented again, and most never are, so without this every renewal left
    two rows behind for ever.
    """
    now = int(time.time())
    with _connect() as conn:
        removed = conn.execute(
            "DELETE FROM oauth_tokens WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
        ).rowcount
        conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (now,))
    return removed


def delete_oauth_token(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM oauth_tokens WHERE token_hash = ?", (token_hash(token),))


def delete_tokens_for_subject(subject: str) -> int:
    """Revoke everything issued to one person.

    Disconnecting, or signing in again, has to take the access tokens with it.
    Otherwise deleting the Garmin session would leave live bearer tokens whose
    subject no longer resolves — working credentials pointing at nothing.
    """
    with _connect() as conn:
        return conn.execute(
            "DELETE FROM oauth_tokens WHERE subject = ?", (subject,)
        ).rowcount


# --------------------------------------------------------------------------
# Garmin's official developer programme
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OfficialLink:
    user_token: str
    garmin_user_id: str
    tokens: dict
    # None while Garmin hasn't said what the account agreed to share.
    permissions: list[str] | None
    created_at: int


def _official_link(row: tuple | None) -> OfficialLink | None:
    if not row:
        return None
    try:
        tokens = json.loads(_cipher().decrypt(row[2]).decode())
    except InvalidToken:
        return None
    return OfficialLink(row[0], row[1], tokens, json.loads(row[3]), row[4])


_LINK_COLUMNS = "user_token, garmin_user_id, tokens_enc, permissions, created_at"


def save_official_link(user_token: str, garmin_user_id: str, tokens: dict, permissions: list[str] | None) -> None:
    """Link a person to their Garmin account on the official API, replacing any earlier link.

    A Garmin account can be linked to one person here at a time, so a second
    person linking the same account takes it over.
    """
    now = int(time.time())
    encrypted = _cipher().encrypt(json.dumps(tokens).encode())
    with _connect() as conn:
        conn.execute(
            "DELETE FROM official_links WHERE garmin_user_id = ? AND user_token != ?",
            (garmin_user_id, user_token),
        )
        conn.execute(
            "INSERT INTO official_links (user_token, garmin_user_id, tokens_enc, permissions, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_token) DO UPDATE SET "
            "garmin_user_id = excluded.garmin_user_id, tokens_enc = excluded.tokens_enc, "
            "permissions = excluded.permissions, updated_at = excluded.updated_at",
            (user_token, garmin_user_id, encrypted, json.dumps(permissions), now, now),
        )


def update_official_tokens(user_token: str, tokens: dict) -> None:
    encrypted = _cipher().encrypt(json.dumps(tokens).encode())
    with _connect() as conn:
        conn.execute(
            "UPDATE official_links SET tokens_enc = ?, updated_at = ? WHERE user_token = ?",
            (encrypted, int(time.time()), user_token),
        )


def update_official_permissions(garmin_user_id: str, permissions: list[str]) -> bool:
    with _connect() as conn:
        return conn.execute(
            "UPDATE official_links SET permissions = ?, updated_at = ? WHERE garmin_user_id = ?",
            (json.dumps(permissions), int(time.time()), garmin_user_id),
        ).rowcount > 0


def official_link(user_token: str) -> OfficialLink | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_LINK_COLUMNS} FROM official_links WHERE user_token = ?", (user_token,)
        ).fetchone()
    return _official_link(row)


def official_link_for_garmin_user(garmin_user_id: str) -> OfficialLink | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_LINK_COLUMNS} FROM official_links WHERE garmin_user_id = ?", (garmin_user_id,)
        ).fetchone()
    return _official_link(row)


def forget_official_user(garmin_user_id: str) -> None:
    """Delete a Garmin account's link and everything Garmin sent for it."""
    with _connect() as conn:
        conn.execute("DELETE FROM official_links WHERE garmin_user_id = ?", (garmin_user_id,))
        conn.execute("DELETE FROM official_summaries WHERE garmin_user_id = ?", (garmin_user_id,))
        conn.execute("DELETE FROM official_workouts WHERE garmin_user_id = ?", (garmin_user_id,))
        conn.execute("DELETE FROM official_schedules WHERE garmin_user_id = ?", (garmin_user_id,))
        conn.execute("DELETE FROM official_backfill WHERE garmin_user_id = ?", (garmin_user_id,))


# Asking Garmin for someone's history once they connect (backfill.py): how far
# back each kind has been asked for, counted back from when they connected.
def queue_backfill(garmin_user_id: str, anchor: int | None = None) -> bool:
    """Ask for this account's history, once. False when it was asked for already."""
    now = int(time.time())
    with _connect() as conn:
        return conn.execute(
            "INSERT OR IGNORE INTO official_backfill (garmin_user_id, anchor, progress, done, updated_at) "
            "VALUES (?, ?, '{}', 0, 0)", (garmin_user_id, int(anchor or now)),
        ).rowcount > 0


def queue_backfill_for_all_links() -> int:
    """Everyone linked before history was asked for gets theirs too."""
    with _connect() as conn:
        return conn.execute(
            "INSERT OR IGNORE INTO official_backfill (garmin_user_id, anchor, progress, done, updated_at) "
            "SELECT garmin_user_id, ?, '{}', 0, 0 FROM official_links", (int(time.time()),),
        ).rowcount


def next_backfill() -> tuple[str, int, dict] | None:
    """The account waiting longest for its next window: (Garmin id, anchor, progress)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT garmin_user_id, anchor, progress FROM official_backfill WHERE done = 0 "
            "ORDER BY updated_at, rowid LIMIT 1"
        ).fetchone()
    return (row[0], int(row[1]), json.loads(row[2] or "{}")) if row else None


def save_backfill(garmin_user_id: str, progress: dict, done: bool, keep_turn: bool = False) -> None:
    with _connect() as conn:
        if keep_turn:
            conn.execute("UPDATE official_backfill SET progress = ?, done = ? WHERE garmin_user_id = ?",
                         (json.dumps(progress), int(done), garmin_user_id))
            return
        conn.execute(
            "UPDATE official_backfill SET progress = ?, done = ?, updated_at = ? WHERE garmin_user_id = ?",
            (json.dumps(progress), int(done), int(time.time() * 1000), garmin_user_id),
        )


def reopen_backfills(version: int) -> int:
    """Accounts asked for the old way, asked again from the start."""
    with _connect() as conn:
        rows = conn.execute("SELECT garmin_user_id, progress FROM official_backfill").fetchall()
        stale = [(gid,) for gid, progress in rows if json.loads(progress or "{}").get("v") != version]
        conn.executemany("UPDATE official_backfill SET progress = '{}', done = 0, updated_at = 0 WHERE garmin_user_id = ?", stale)
    return len(stale)


def backfill_state(garmin_user_id: str) -> tuple[int, dict, bool] | None:
    """(anchor, progress, done) for this account, or None if its history was never asked for."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT anchor, progress, done FROM official_backfill WHERE garmin_user_id = ?", (garmin_user_id,)
        ).fetchone()
    return (int(row[0]), json.loads(row[1] or "{}"), bool(row[2])) if row else None


def save_official_summaries(garmin_user_id: str, kind: str, summaries: list[tuple[str, int | None, dict]]) -> int:
    """Keep what Garmin pushed: (summary id, start time, summary) each.

    A summary Garmin sends again, with more of the day in it, replaces the
    earlier one under the same id.
    """
    if not summaries:
        return 0
    cipher, now = _cipher(), int(time.time())
    rows = [
        (garmin_user_id, kind, summary_id, start, cipher.encrypt(json.dumps(data).encode()), now)
        for summary_id, start, data in summaries
    ]
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO official_summaries (garmin_user_id, kind, summary_id, start_time, data_enc, received_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(garmin_user_id, kind, summary_id) DO UPDATE SET "
            "start_time = excluded.start_time, data_enc = excluded.data_enc, received_at = excluded.received_at",
            rows,
        )
    return len(rows)


def official_summary_counts(garmin_user_id: str) -> dict[str, dict]:
    """How many of each kind Garmin has sent, and when the latest one starts and arrived."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT kind, COUNT(*), MAX(start_time), MAX(received_at) FROM official_summaries "
            "WHERE garmin_user_id = ? GROUP BY kind",
            (garmin_user_id,),
        ).fetchall()
    return {kind: {"count": count, "latest_start": latest, "last_received": received}
            for kind, count, latest, received in rows}


def official_summaries(garmin_user_id: str, kind: str, since: int = 0, limit: int = 500) -> list[dict]:
    """The summaries of one kind starting at or after `since`, oldest first."""
    cipher = _cipher()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT data_enc FROM official_summaries WHERE garmin_user_id = ? AND kind = ? "
            "AND COALESCE(start_time, 0) >= ? ORDER BY start_time LIMIT ?",
            (garmin_user_id, kind, since, limit),
        ).fetchall()
    out = []
    for (blob,) in rows:
        try:
            out.append(json.loads(cipher.decrypt(blob).decode()))
        except InvalidToken:
            continue
    return out


def drop_official_kinds(keep: set[str]) -> int:
    """Delete every kept summary whose kind isn't in `keep`. Returns how many went."""
    marks = ", ".join("?" for _ in keep)
    with _connect() as conn:
        return conn.execute(f"DELETE FROM official_summaries WHERE kind NOT IN ({marks})", tuple(keep)).rowcount


def all_official_summaries(kinds: tuple[str, ...]) -> list[tuple[str, str, str, int | None, dict]]:
    """Every kept summary of these kinds, for every account: (garmin user, kind, id, start, summary)."""
    cipher = _cipher()
    marks = ", ".join("?" for _ in kinds)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT garmin_user_id, kind, summary_id, start_time, data_enc FROM official_summaries WHERE kind IN ({marks})",
            kinds,
        ).fetchall()
    out = []
    for garmin_user_id, kind, summary_id, start, blob in rows:
        try:
            out.append((garmin_user_id, kind, summary_id, start, json.loads(cipher.decrypt(blob).decode())))
        except InvalidToken:
            continue
    return out


def set_official_only(user_token: str, on: bool) -> bool:
    """Read only from Garmin's official API for this person, or not. False when they aren't linked."""
    with _connect() as conn:
        return conn.execute("UPDATE official_links SET official_only = ? WHERE user_token = ?",
                            (1 if on else None, user_token)).rowcount > 0


def official_counts() -> dict[str, int]:
    """How many Garmin users have approved Daash through Garmin's official API, and how many of
    those have no Garmin Connect session behind them. Garmin's starter access caps the first."""
    with _connect() as conn:
        linked = conn.execute("SELECT COUNT(*) FROM official_links").fetchone()[0]
        alone = conn.execute("SELECT COUNT(*) FROM users WHERE route = 'official'").fetchone()[0]
    return {"linked": linked, "official_signups": alone}


def official_only(user_token: str) -> str | None:
    """The Garmin user id when this person reads only from the official API, else None."""
    with _connect() as conn:
        row = conn.execute("SELECT garmin_user_id FROM official_links WHERE user_token = ? AND official_only = 1",
                           (user_token,)).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------
# Workouts and schedules made through Garmin's official Training API
# --------------------------------------------------------------------------
# The Training API has no way to list a person's workouts or their calendar
# beyond what this app put there, so what Daash creates is kept here: the
# workout as the tools shaped it, encrypted like everything else, and each
# date it was scheduled on.


def save_official_workout(garmin_user_id: str, workout_id: int, data: dict) -> None:
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO official_workouts (garmin_user_id, workout_id, data_enc, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(garmin_user_id, workout_id) DO UPDATE SET data_enc = excluded.data_enc, updated_at = excluded.updated_at",
            (garmin_user_id, int(workout_id), _cipher().encrypt(json.dumps(data).encode()), now, now),
        )


def official_workout(garmin_user_id: str, workout_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT data_enc FROM official_workouts WHERE garmin_user_id = ? AND workout_id = ?",
                           (garmin_user_id, int(workout_id))).fetchone()
    if not row:
        return None
    try:
        return json.loads(_cipher().decrypt(row[0]).decode())
    except InvalidToken:
        return None


def official_workouts(garmin_user_id: str, limit: int = 100) -> list[dict]:
    """Newest first."""
    cipher = _cipher()
    with _connect() as conn:
        rows = conn.execute("SELECT data_enc FROM official_workouts WHERE garmin_user_id = ? ORDER BY updated_at DESC LIMIT ?",
                            (garmin_user_id, limit)).fetchall()
    out = []
    for (blob,) in rows:
        try:
            out.append(json.loads(cipher.decrypt(blob).decode()))
        except InvalidToken:
            continue
    return out


def delete_official_workout(garmin_user_id: str, workout_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM official_workouts WHERE garmin_user_id = ? AND workout_id = ?", (garmin_user_id, int(workout_id)))
        conn.execute("DELETE FROM official_schedules WHERE garmin_user_id = ? AND workout_id = ?", (garmin_user_id, int(workout_id)))


def save_official_schedule(garmin_user_id: str, schedule_id: int, workout_id: int, day: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO official_schedules (garmin_user_id, schedule_id, workout_id, day) VALUES (?, ?, ?, ?)",
                     (garmin_user_id, int(schedule_id), int(workout_id), day))


def delete_official_schedule(garmin_user_id: str, schedule_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM official_schedules WHERE garmin_user_id = ? AND schedule_id = ?", (garmin_user_id, int(schedule_id)))


def official_schedules(garmin_user_id: str, start: str, end: str) -> list[tuple[int, int, str]]:
    """(schedule id, workout id, day) for every schedule between two dates, inclusive."""
    with _connect() as conn:
        rows = conn.execute("SELECT schedule_id, workout_id, day FROM official_schedules WHERE garmin_user_id = ? "
                            "AND day >= ? AND day <= ? ORDER BY day", (garmin_user_id, start, end)).fetchall()
    return [(int(a), int(b), c) for a, b, c in rows]



# --------------------------------------------------------------------------
# Signing up through Garmin's own page
# --------------------------------------------------------------------------

# Shown where an address would be, until the person gives one.
NO_EMAIL_YET = "Garmin account"


def save_official_user(garmin_user_id: str, tokens: dict, permissions: list[str] | None) -> str:
    """A new Daash account from an approval on Garmin's page alone. Returns its token.

    There is no Garmin Connect session behind it, so the row's session is
    empty and marked as the official route; its reads and writes come only
    from Garmin's official API. The email comes a step later, on the Allow page.
    """
    user_token = new_user_token()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (user_token, email_masked, token_blob, created_at, route) VALUES (?, ?, ?, ?, 'official')",
            (user_token, NO_EMAIL_YET, _cipher().encrypt(b""), int(time.time())),
        )
    save_official_link(user_token, garmin_user_id, tokens, permissions)
    set_official_only(user_token, True)
    return user_token


def has_connect(user_token: str) -> bool:
    """Whether a Garmin Connect session stands behind this account."""
    with _connect() as conn:
        row = conn.execute("SELECT route FROM users WHERE user_token = ?", (user_token,)).fetchone()
    return bool(row) and row[0] != "official"


def set_user_email(user_token: str, email: str, email_masked: str) -> str:
    """Keep the address someone gave on the Allow page. Returns its fingerprint."""
    fingerprint = email_fingerprint(email)
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET email_hash = ?, email_enc = ?, email_masked = ? WHERE user_token = ?",
            (fingerprint, _cipher().encrypt(email.strip().encode()), email_masked, user_token),
        )
    return fingerprint


def join_official_account(source: str, target: str) -> None:
    """Move a Garmin approval made just now onto the account the person already had.

    `source` is the account the approval created a moment ago, `target` the
    one their email belongs to, proven by a code sent to it. Their plans,
    crew and history stay where they were; the new row goes. Someone who
    reads through Garmin Connect keeps doing so: only an account that was
    already on the official route alone stays official-only.
    """
    with _connect() as conn:
        moving = conn.execute(
            "SELECT garmin_user_id FROM official_links WHERE user_token = ?", (source,)
        ).fetchone()
        if not moving:
            return
        held = conn.execute(
            "SELECT garmin_user_id FROM official_links WHERE user_token = ?", (target,)
        ).fetchone()
        if held and held[0] != moving[0]:
            # A different Garmin account was linked before: the person has
            # just approved this one, so it replaces that, and what the old
            # one sent goes with its link.
            for table in ("official_links", "official_summaries", "official_workouts", "official_schedules"):
                conn.execute(f"DELETE FROM {table} WHERE garmin_user_id = ?", (held[0],))
        route = conn.execute("SELECT route FROM users WHERE user_token = ?", (target,)).fetchone()
        alone = bool(route) and route[0] == "official"
        conn.execute(
            "UPDATE official_links SET user_token = ?, official_only = ? WHERE user_token = ?",
            (target, 1 if alone else None, source),
        )
        conn.execute("UPDATE oauth_tokens SET subject = ? WHERE subject = ?", (target, source))
        conn.execute("DELETE FROM users WHERE user_token = ?", (source,))


# A code by email, asked for only when an address already belongs to a Daash
# account: it proves the person typing it owns that account before a Garmin
# approval is joined to it.
CODE_LIFETIME = 10 * 60
CODE_RESEND_AFTER = 60
CODE_TRIES = 5
# Codes to one address in a day. With five tries each, guessing one of a
# million is hopeless, and the address's owner isn't flooded either.
CODE_SENDS_PER_DAY = 8


def _code_digest(email_hash: str, code: str) -> str:
    key = os.environ.get("GARMIN_MCP_SECRET", "").encode()
    return hmac.new(key, f"code:{email_hash}:{code}".encode(), hashlib.sha256).hexdigest()


def new_email_code(email_hash: str) -> str | None:
    """A fresh 6-digit code for this address.

    None if one went out under a minute ago, or the address has had its
    day's codes: the caller says which by asking code_wait.
    """
    now = int(time.time())
    with _connect() as conn:
        row = conn.execute("SELECT sent_at, sends, first_sent_at FROM email_codes WHERE email_hash = ?",
                           (email_hash,)).fetchone()
        if row and now - row[0] < CODE_RESEND_AFTER:
            return None
        sends, first = (row[1], row[2]) if row and now - row[2] < 86400 else (0, now)
        if sends >= CODE_SENDS_PER_DAY:
            return None
        code = f"{secrets.randbelow(1_000_000):06d}"
        conn.execute(
            "INSERT INTO email_codes (email_hash, code_hash, sent_at, expires_at, attempts, sends, first_sent_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?) "
            "ON CONFLICT(email_hash) DO UPDATE SET code_hash = excluded.code_hash, sent_at = excluded.sent_at, "
            "expires_at = excluded.expires_at, attempts = 0, sends = excluded.sends, "
            "first_sent_at = excluded.first_sent_at",
            (email_hash, _code_digest(email_hash, code), now, now + CODE_LIFETIME, sends + 1, first),
        )
        conn.execute("DELETE FROM email_codes WHERE first_sent_at < ?", (now - 2 * 86400,))
    return code


def code_wait(email_hash: str) -> str:
    """Why no code went out: "minute" (one just did) or "day" (the day's are used up)."""
    now = int(time.time())
    with _connect() as conn:
        row = conn.execute("SELECT sent_at FROM email_codes WHERE email_hash = ?", (email_hash,)).fetchone()
    return "minute" if row and now - row[0] < CODE_RESEND_AFTER else "day"


def check_email_code(email_hash: str, code: str) -> str:
    """"ok" (and the code is spent), "wrong", "expired" or "locked"."""
    now = int(time.time())
    code = "".join(ch for ch in str(code) if ch.isdigit())
    with _connect() as conn:
        row = conn.execute(
            "SELECT code_hash, expires_at, attempts FROM email_codes WHERE email_hash = ?", (email_hash,)
        ).fetchone()
        if not row or row[1] < now:
            return "expired"
        if row[2] >= CODE_TRIES:
            return "locked"
        if hmac.compare_digest(row[0], _code_digest(email_hash, code)):
            conn.execute("UPDATE email_codes SET expires_at = 0 WHERE email_hash = ?", (email_hash,))
            return "ok"
        conn.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE email_hash = ?", (email_hash,))
        return "locked" if row[2] + 1 >= CODE_TRIES else "wrong"
