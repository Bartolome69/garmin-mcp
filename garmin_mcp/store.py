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
)

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
        news_seen = opted_out = None
        if email_hash:
            # The person is the same, so what they have been told and what they
            # asked for outlives the row being replaced.
            news_seen, opted_out = conn.execute(
                "SELECT MAX(news_seen), MAX(email_opt_out) FROM users WHERE email_hash = ?",
                (email_hash,),
            ).fetchone()
            conn.execute(
                "DELETE FROM users WHERE email_hash = ? AND user_token != ?",
                (email_hash, user_token),
            )
        conn.execute(
            "INSERT INTO users (user_token, email_masked, token_blob, created_at, email_hash, email_enc, "
            "news_seen, email_opt_out) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_token) DO UPDATE SET "
            "  token_blob = excluded.token_blob, email_masked = excluded.email_masked, "
            "  email_hash = excluded.email_hash, "
            "  email_enc = COALESCE(excluded.email_enc, users.email_enc)",
            (user_token, email_masked, encrypted, now, email_hash, email_enc, news_seen, opted_out),
        )
    return user_token


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
    """Connections neither used nor kept alive in the last `idle_for` seconds."""
    cutoff = int(time.time()) - idle_for
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_token FROM users "
            "WHERE COALESCE(last_seen_at, created_at) < ? AND COALESCE(kept_alive_at, 0) < ? "
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
