"""The providers a person can connect besides Garmin, as the Connections card shows them.

Kept apart from the adapters: a listing is words, a flag and the ways to sign
in. It needs nothing from the provider's API, so the card, the flag and the
connection lifecycle (provider_routes.py) exist before the adapter does. An
adapter, once written against the provider's verified docs, registers a
Connector here; until then Connect says plainly that it isn't ready.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class ConnectError(Exception):
    """The provider refused the sign-in. The message is shown to the person as it is."""


class Connector(Protocol):
    """What an adapter supplies to connect and disconnect a person."""

    def verify(self, auth: str, credentials: dict[str, Any]) -> str | None:
        """Check the credentials with the provider; the person's id there, or raise ConnectError."""

    def revoke(self, credentials: dict[str, Any]) -> None:
        """Tell the provider to forget the grant, where it can (OAuth revoke). Best effort."""


@dataclass(frozen=True)
class Listing:
    name: str                 # the key in provider_connections and analytics: "intervals"
    label: str                # "Intervals.icu"
    via: str                  # the neutral badge: "Via Intervals.icu"
    flag: str                 # PostHog flag that shows the card to a person
    explainer: str            # one or two plain sentences on the card
    auth: tuple[str, ...]     # sign-in routes the provider offers, preferred first


CATALOG: dict[str, Listing] = {
    "intervals": Listing(
        name="intervals",
        label="Intervals.icu",
        via="Via Intervals.icu",
        flag="provider_intervals_enabled",
        explainer=("Connect Intervals.icu to use Daash with COROS, Suunto, Polar or Wahoo. "
                   "Your watch must already sync to Intervals.icu."),
        # TODO(step 0): confirm from Intervals.icu's API docs which of these it offers third-party apps.
        auth=("oauth", "api_key"),
    ),
}

_CONNECTORS: dict[str, Connector] = {}


def register_connector(name: str, connector: Connector) -> None:
    """An adapter's way to connect people; replaces any earlier one for that provider."""
    if name not in CATALOG:
        raise KeyError(f"no listing for provider {name!r}")
    _CONNECTORS[name] = connector


def connector(name: str) -> Connector | None:
    return _CONNECTORS.get(name)


def listing(name: str) -> Listing | None:
    return CATALOG.get(name)


def visible(subject: str | None, flag_on: Callable[[str, str | None], bool]) -> list[Listing]:
    """The listings this person may see, by their flags."""
    return [l for l in CATALOG.values() if subject and flag_on(l.flag, subject)]
