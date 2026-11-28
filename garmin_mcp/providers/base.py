"""What every data provider is, and what it may or may not do.

A provider is where a person's training data comes from: Garmin today, others
later (docs/providers/). The tools never talk to one directly. They hand a
call to the person's current provider (registry.current()), which runs it
against its own client. A provider says up front what it can do
(`capabilities`); a tool that needs something it can't do gets NotSupported,
which reaches the person as a plain sentence rather than a failed call.

Capabilities are honest: a flag is set only when the provider really serves
it, read or write. Nothing here is Garmin-specific.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable


class Capability(str, Enum):
    """One thing a provider can do. The value is what analytics and docs show."""

    ACTIVITIES = "activities"              # the list of recorded runs, rides, swims
    ACTIVITY_DETAILS = "activity_details"  # one activity's laps, splits and streams
    ACTIVITY_EDIT = "activity_edit"        # rename an activity, notes, type, shoes
    DAILY = "daily"                        # the day: steps, stress, body battery
    SLEEP = "sleep"                        # nights: duration, stages, score
    HRV = "hrv"                            # overnight heart-rate variability
    HEART_RATE = "heart_rate"              # heart rate through the day and night
    READINESS = "readiness"                # the provider's own readiness score
    FITNESS = "fitness"                    # VO2 max, race predictions, threshold
    GEAR = "gear"                          # shoes and their mileage
    WORKOUTS = "workouts"                  # read and write the workout library
    STRENGTH = "strength"                  # strength workouts from an exercise catalogue
    SCHEDULE = "schedule"                  # put a workout on a day, so on the watch


ALL = frozenset(Capability)


# What each capability is called in a sentence, for NotSupported.
SAID = {
    Capability.ACTIVITIES: "your activities",
    Capability.ACTIVITY_DETAILS: "an activity's laps and splits",
    Capability.ACTIVITY_EDIT: "changes to an activity",
    Capability.DAILY: "your daily summary",
    Capability.SLEEP: "your sleep",
    Capability.HRV: "your HRV",
    Capability.HEART_RATE: "your all-day heart rate",
    Capability.READINESS: "a readiness score",
    Capability.FITNESS: "fitness markers such as VO2 max and race predictions",
    Capability.GEAR: "your shoes",
    Capability.WORKOUTS: "your workout library",
    Capability.STRENGTH: "strength workouts",
    Capability.SCHEDULE: "scheduled workouts",
}


class NotSupported(Exception):
    """The current provider can't do what a tool asked of it.

    The message is shown to the person as it is, so it says what isn't there
    and stays neutral about the provider.
    """

    def __init__(self, provider: "Provider", capability: Capability) -> None:
        self.provider = provider.name
        self.capability = capability
        super().__init__(f"{provider.label} doesn't share {SAID[capability]} with Daash on this connection.")


class Provider(ABC):
    """A source of training data, and the place writes go to."""

    #: The key stored in users.default_provider and sent to analytics.
    name: str
    #: How the provider is named to a person: "Connected to {label}".
    label: str
    #: What it can do. Checked with supports() or require().
    capabilities: frozenset[Capability]

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, capability: Capability) -> None:
        """Raise NotSupported unless this provider can do `capability`."""
        if capability not in self.capabilities:
            raise NotSupported(self, capability)

    @abstractmethod
    def run(self, fn: Callable[[Any], Any]) -> Any:
        """fn(client) against this provider's client for the current person (blocking)."""

    @abstractmethod
    def remembered(self, key: Any, ttl: float, fn: Callable[[Any], Any]) -> Any:
        """run(fn), answered from memory when an answer for `key` is under `ttl` seconds old."""

    @abstractmethod
    def forget(self) -> None:
        """Drop everything remembered for the current person (after any write)."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"
