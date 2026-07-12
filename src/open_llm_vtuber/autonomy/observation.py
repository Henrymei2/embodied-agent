"""The context snapshot the planner reasons over.

Kept deliberately small and all-optional: the driver fills in whatever signals
it can cheaply gather, and the planner degrades gracefully when a field is
unknown (None).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Observation:
    # Seconds since the user last did anything (typed/spoke). None if unknown.
    idle_seconds: Optional[float] = None
    # Whether a client/user is currently connected/present.
    user_present: bool = True
    # Seconds since the agent last spoke to the user. None if it never has.
    seconds_since_last_spoke: Optional[float] = None
    # Local hour of day (0-23), if known — lets greetings be time-aware.
    hour_of_day: Optional[int] = None
