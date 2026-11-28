"""Garmin behind the provider interface, behaving exactly as before it existed.

Two routes reach Garmin, and both stay as they were:

- Garmin Connect through the private sign-in (session.py): the route almost
  everyone is on while Daash has no official API access.
- Garmin's official API (official_client.py), for a preview account set to
  read from it alone. A call the official API has no answer for (readiness,
  gear, the all-day heart rate) still goes to Connect while the account has
  both; an account with no Connect session gets NOT_ON_OFFICIAL.

run() and remembered() are what server.py's _run and _remembered did, moved
here unchanged; server.py now hands every call to the current provider.
Garmin keeps its own tables (users, official_links): provider_connections is
for the providers that come after it.
"""

from __future__ import annotations

from typing import Any, Callable

from ..official_client import NotOnOfficial
from ..session import GarminError
from .base import ALL, Provider

# Said by any tool that needs Garmin Connect, to someone who signed up through
# Garmin's own page and so has no Connect session for it to fall back on.
NOT_ON_OFFICIAL = (
    "Not available on this connection: Daash reads this account through Garmin's official "
    "API, and Garmin doesn't share that through it. Answer from what Daash can read here "
    "(activities and their laps, sleep, the day, the plan), and for workout targets use "
    "paces, heart rate in bpm or watts rather than zones or a share of FTP."
)


class GarminProvider(Provider):
    name = "garmin"
    label = "Garmin"
    # Everything Daash does today, it does with Garmin. The official API's gaps
    # are per account (NOT_ON_OFFICIAL above), not gaps in the provider.
    capabilities = ALL

    def __init__(self, official_client: Callable[[], Any], connect: Any) -> None:
        """official_client() gives the official API's client for an official-only
        account, else None; connect is the Garmin Connect session (session.session)."""
        self._official_client = official_client
        self._connect = connect

    def run(self, fn: Callable[[Any], Any]) -> Any:
        """fn against the official API where the account asks for it, else Garmin Connect.

        A call the official API has no answer for (readiness, gear, the all-day
        heart rate) still goes to Connect while the account has both: this is the
        preview, and nothing the person already had should stop working. An
        account that signed up through Garmin's page has no Connect to go to.
        """
        client = self._official_client()
        if client is not None:
            try:
                return client.run(fn)
            except NotOnOfficial:
                if client.alone:
                    raise GarminError(NOT_ON_OFFICIAL) from None
        return self._connect.run(fn)

    def remembered(self, key: Any, ttl: float, fn: Callable[[Any], Any]) -> Any:
        """run(), from the session's memory where fresh enough.

        An official-only account's answers come from the store, so nothing is
        remembered for them: the store is already as quick, and always current.
        """
        client = self._official_client()
        if client is not None:
            try:
                return client.run(fn)
            except NotOnOfficial:
                if client.alone:
                    raise GarminError(NOT_ON_OFFICIAL) from None
        return self._connect.remembered(key, ttl, fn)

    def forget(self) -> None:
        self._connect.forget()
