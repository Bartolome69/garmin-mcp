"""Which provider answers the current request.

Providers register once at import. Each request names one (the hosted
server's SessionBinding sets it from users.default_provider); anything that
runs outside a request (jobs, the local stdio server, tests) gets the default.
A name nobody registered, or no name at all, means the default: a stored
choice can never leave someone with no provider at all.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from .base import Provider

DEFAULT = "garmin"

_PROVIDERS: dict[str, Provider] = {}
_CURRENT: ContextVar[str | None] = ContextVar("daash_provider", default=None)


def register(provider: Provider) -> Provider:
    """Add a provider under its name, replacing any earlier one with that name."""
    _PROVIDERS[provider.name] = provider
    return provider


def names() -> list[str]:
    return sorted(_PROVIDERS)


def get(name: str | None) -> Provider:
    """The provider called `name`, or the default when there's none by that name."""
    if name and name in _PROVIDERS:
        return _PROVIDERS[name]
    try:
        return _PROVIDERS[DEFAULT]
    except KeyError:
        raise LookupError(f"no provider registered as the default ({DEFAULT!r})") from None


def resolve(name: str | None) -> str:
    """The name that `name` resolves to: itself when registered, else the default."""
    return name if name and name in _PROVIDERS else DEFAULT


def use(name: str | None) -> Token:
    """Make `name` the current request's provider. Pass the token to reset()."""
    return _CURRENT.set(resolve(name))


def reset(token: Token) -> None:
    _CURRENT.reset(token)


def current() -> Provider:
    """The provider for the current request (the default outside one)."""
    return get(_CURRENT.get())


def current_name() -> str:
    return resolve(_CURRENT.get())
