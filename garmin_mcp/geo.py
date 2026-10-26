"""Which country a visitor is in, from their network address, looked up here.

For knowing where people sign up from, and nothing else: the country alone is
kept, never the address, and the lookup never leaves this server. The table is
DB-IP's free IP to Country Lite (CC BY 4.0, https://db-ip.com), fetched when
the server image is built. Without it, every answer is None and nothing else
changes.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from functools import lru_cache

log = logging.getLogger(__name__)

DB_PATH = os.environ.get("GARMIN_MCP_COUNTRY_DB", "/app/geo/dbip-country-lite.mmdb")


@lru_cache(maxsize=1)
def _reader():
    try:
        import maxminddb

        return maxminddb.open_database(DB_PATH)
    except Exception as exc:  # noqa: BLE001 - no table means no countries, never a failure
        log.info("No country table (%s): sign-ups are recorded without a country", type(exc).__name__)
        return None


def country(address: str | None) -> str | None:
    """The two-letter country code for a public address, or None."""
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return None
    if not ip.is_global:
        return None
    reader = _reader()
    if reader is None:
        return None
    try:
        found = reader.get(str(ip)) or {}
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(found, dict):
        return None
    # db-ip.com's own table nests it; the npm copy has it flat.
    code = (found.get("country") or {}).get("iso_code") or found.get("country_code")
    return code if isinstance(code, str) and len(code) == 2 else None
