"""Encrypted store of Garmin sessions, one row per person.

Only used by the hosted server. The local stdio server keeps its single token
file and never touches this.

What is stored is the token blob Garmin issues, encrypted at rest. The password
is never written: it is exchanged for tokens during sign-in and discarded.
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
)


class StoreError(RuntimeError):
    """Something is wrong with the store's configuration."""


@dataclass(frozen=True)
class User:
    user_token: str
    email_masked: str
    created_at: int


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
) -> str:
    """Store someone's Garmin session, retiring any earlier one. Returns their token.

    Signing in again used to mint a second URL and leave the first one working
    for ever, with no way to take it back. Now the old rows go, so a fresh
    sign-in is also how you revoke a link you have lost.
    """
    user_token = user_token or new_user_token()
    encrypted = _cipher().encrypt(token_blob.encode())
    now = int(time.time())
    with _connect() as conn:
        if email_hash:
            conn.execute(
                "DELETE FROM users WHERE email_hash = ? AND user_token != ?",
                (email_hash, user_token),
            )
        conn.execute(
            "INSERT INTO users (user_token, email_masked, token_blob, created_at, email_hash) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_token) DO UPDATE SET "
            "  token_blob = excluded.token_blob, email_masked = excluded.email_masked, "
            "  email_hash = excluded.email_hash",
            (user_token, email_masked, encrypted, now, email_hash),
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


def update_blob(user_token: str, token_blob: str) -> None:
    """Persist a refreshed token so the next request resumes from it."""
    encrypted = _cipher().encrypt(token_blob.encode())
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET token_blob = ?, last_seen_at = ? WHERE user_token = ?",
            (encrypted, int(time.time()), user_token),
        )


def get_user(user_token: str) -> User | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_token, email_masked, created_at FROM users WHERE user_token = ?",
            (user_token,),
        ).fetchone()
    return User(*row) if row else None


def count_users() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
