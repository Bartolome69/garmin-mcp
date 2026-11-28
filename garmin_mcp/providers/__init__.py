"""Data providers: where a person's training data comes from (docs/providers/).

Garmin is the default and, for now, the only one. See base.py for what a
provider is, registry.py for how a request picks one, garmin.py for Garmin
behind the interface, and workout.py for the provider-neutral workout.
"""

from .base import ALL, Capability, NotSupported, Provider
from .registry import DEFAULT, current, current_name, get, names, register, reset, resolve, use

__all__ = [
    "ALL", "Capability", "DEFAULT", "NotSupported", "Provider",
    "current", "current_name", "get", "names", "register", "reset", "resolve", "use",
]
