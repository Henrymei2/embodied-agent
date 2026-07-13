"""Autonomous behavior engine for the embodied agent.

Implements the scope's daily-planner / routines loop: the agent periodically
decides, on its own initiative, whether to act along a behavioral direction
(curiosity, care, housekeeping, growth, social presence, work) and how to
deliver it (silently, by asking permission, by speaking briefly, or not at all),
subject to configurable interruption controls.
"""

from .directions import Direction
from .driver import autonomy_loop
from .observation import Observation
from .planner import (
    ACT_ASK,
    ACT_NOTHING,
    ACT_SILENT,
    ACT_SPEAK,
    AutonomyPlanner,
    PlannerDecision,
)
from .presence import (
    DISMISSED,
    HELPFUL,
    SNOOZED,
    PresenceLedger,
    PresenceReceipt,
    attention_cost,
)
from .settings import AutonomySettings
from .state import AutonomyState

__all__ = [
    "ACT_ASK",
    "ACT_NOTHING",
    "ACT_SILENT",
    "ACT_SPEAK",
    "AutonomyPlanner",
    "AutonomySettings",
    "AutonomyState",
    "Direction",
    "Observation",
    "PlannerDecision",
    "PresenceLedger",
    "PresenceReceipt",
    "HELPFUL",
    "DISMISSED",
    "SNOOZED",
    "attention_cost",
    "autonomy_loop",
]
