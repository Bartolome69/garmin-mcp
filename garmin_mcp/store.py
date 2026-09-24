"""Encrypted store of Garmin sessions, one row per person.

Only used by the hosted server. The local stdio server keeps its single token
file and never touches this.

What is stored is the token blob Garmin issues, encrypted at rest. The password
is never written: it is exchanged for tokens during sign-in and discarded.
"""

from __future__ import annotations

import base64
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
    return conn


def new_user_token() -> str:
    """The URL is the credential, so it has to be unguessable."""
    return secrets.token_urlsafe(32)


def save_user(email_masked: str, token_blob: str, user_token: str | None = None) -> str:
    """Store (or replace) someone's Garmin session. Returns their URL token."""
    user_token = user_token or new_user_token()
    encrypted = _cipher().encrypt(token_blob.encode())
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (user_token, email_masked, token_blob, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_token) DO UPDATE SET "
            "  token_blob = excluded.token_blob, email_masked = excluded.email_masked",
            (user_token, email_masked, encrypted, now),
        )
    return user_token


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
