"""Behavioral directions and their candidate actions.

The scope frames autonomous behavior as acting along general *directions*
(curiosity, care, housekeeping, growth, social presence, work) rather than a
fixed task list. Each direction contributes one or more candidate actions; the
planner scores them and picks what (if anything) to do.

Each candidate carries intrinsic scores in [0, 1]:
  - usefulness:    how valuable the action tends to be
  - intrusiveness: how much it disrupts the user (0 = invisible, 1 = demanding)
  - risk:          chance of being unwanted / wrong / annoying
and a preferred `delivery` mode plus a `relevance` function that gates it on the
observed context (e.g. "care" only matters when the user seems idle/stuck).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from .observation import Observation


class Direction(str, Enum):
    CURIOSITY = "curiosity"
    CARE = "care"
    HOUSEKEEPING = "housekeeping"
    GROWTH = "growth"
    SOCIAL = "social"
    WORK = "work"


# Delivery modes a candidate can prefer (the planner may downgrade these).
SILENT = "silent"
SPEAK = "speak"
ASK = "ask"


@dataclass(frozen=True)
class Candidate:
    direction: Direction
    prompt: str
    usefulness: float
    intrusiveness: float
    risk: float
    delivery: str
    # relevance(observation) -> multiplier in [0, 1]; 0 removes the candidate.
    relevance: Callable[[Observation], float]
    # Set when this candidate was compiled from a learned habit (Habit Engine),
    # so dispatches and receipts can be traced back to the habit that drove them.
    habit_id: Optional[str] = None


def _idle_boost(obs: Observation) -> float:
    """Higher when the user has been idle for a while (good time for self-work)."""
    if obs.idle_seconds is None:
        return 0.7
    if obs.idle_seconds >= 1800:
        return 1.0
    if obs.idle_seconds >= 300:
        return 0.85
    return 0.5


def _stuck_or_idle(obs: "Observation") -> float:
    """Care is relevant mainly when the user seems idle/stuck but present."""
    if not obs.user_present:
        return 0.0
    if obs.idle_seconds is not None and obs.idle_seconds >= 300:
        return 1.0
    return 0.2


def _present_and_quiet_for_a_while(obs: "Observation") -> float:
    """Social/greeting is relevant when the user is present and we've been quiet."""
    if not obs.user_present:
        return 0.0
    if obs.seconds_since_last_spoke is None:
        return 0.8
    if obs.seconds_since_last_spoke >= 3600:
        return 1.0
    if obs.seconds_since_last_spoke >= 1800:
        return 0.7
    return 0.2


def _always(_obs: "Observation") -> float:
    return 1.0


def _approved_work_available(obs: "Observation") -> float:
    """Only initiate project work when a concrete approved source exists."""
    if obs.approved_plan_count > 0:
        return 1.0
    if obs.open_todo_count > 0:
        return 0.8
    return 0.0


def _active_reading_available(obs: "Observation") -> float:
    return _idle_boost(obs) if obs.has_active_reading else 0.0


def _recent_context_available(obs: "Observation") -> float:
    return _idle_boost(obs) if obs.has_recent_context else 0.0


CANDIDATES: list[Candidate] = [
    Candidate(
        direction=Direction.CURIOSITY,
        prompt=(
            "On your own initiative, explore something interesting related to a "
            "topic the user recently mentioned. Use your tools to look it up, and "
            "save a short note about what you learned in your knowledge folder."
        ),
        usefulness=0.55,
        intrusiveness=0.05,
        risk=0.15,
        delivery=SILENT,
        relevance=lambda o: 0.7 if o.has_recent_context else 0.0,
    ),
    Candidate(
        direction=Direction.CURIOSITY,
        prompt=(
            "Share one brief, genuinely interesting observation or fact with the "
            "user that connects to something they care about. Keep it to a "
            "sentence or two."
        ),
        usefulness=0.5,
        intrusiveness=0.45,
        risk=0.2,
        delivery=SPEAK,
        relevance=lambda o: 1.0 if o.user_present else 0.0,
    ),
    Candidate(
        direction=Direction.CARE,
        prompt=(
            "The user may be idle or stuck. Quietly review the recent context; if "
            "it looks like they could use help, offer it briefly and concretely "
            "rather than interrupting with small talk."
        ),
        usefulness=0.8,
        intrusiveness=0.5,
        risk=0.15,
        delivery=ASK,
        relevance=_stuck_or_idle,
    ),
    Candidate(
        direction=Direction.HOUSEKEEPING,
        prompt=(
            "Tidy or improve your room. Walk to the design board, look at the "
            "layout with `describe_room`, and rearrange or update one object so it "
            "better reflects recent activity. Keep changes small and tasteful."
        ),
        usefulness=0.5,
        intrusiveness=0.05,
        risk=0.2,
        delivery=SILENT,
        relevance=_idle_boost,
    ),
    Candidate(
        # Standing in the middle of the room forever reads as a frozen program,
        # so settling on the sofa is a real (if tiny) thing worth doing.
        direction=Direction.HOUSEKEEPING,
        prompt=(
            "You have nothing to do right now. Walk to the sofa and sit down "
            "rather than standing in the middle of the room. Say nothing."
        ),
        usefulness=0.55,
        intrusiveness=0.0,
        risk=0.05,
        delivery=SILENT,
        relevance=_idle_boost,
    ),
    Candidate(
        # Self-evolution: the agent proposes the change, the user commits it.
        direction=Direction.GROWTH,
        prompt=(
            "Look back at something you recently worked out how to do, and decide "
            "whether it is worth keeping. If it is, walk to the tool rack and "
            "write it down with `draft_skill`: what it is for, when it applies, "
            "and the steps. The user decides whether it becomes a skill, so keep "
            "it concrete and honest about what did not work."
        ),
        usefulness=0.55,
        intrusiveness=0.05,
        risk=0.15,
        delivery=SILENT,
        relevance=_recent_context_available,
    ),
    Candidate(
        direction=Direction.GROWTH,
        prompt=(
            "Continue the book you have open. Walk to the reading chair, read the "
            "next passage with `read_book`, then save a short summary of it with "
            "`save_reading_note`. If no book is open, look at the bookshelf first "
            "and pick something you think the user would find interesting."
        ),
        usefulness=0.5,
        intrusiveness=0.05,
        risk=0.1,
        delivery=SILENT,
        relevance=_active_reading_available,
    ),
    Candidate(
        direction=Direction.GROWTH,
        prompt=(
            "Reflect on your recent interactions and write a short diary entry in "
            "your memory: what you learned about the user, what went well, and one "
            "thing to do better."
        ),
        usefulness=0.6,
        intrusiveness=0.05,
        risk=0.1,
        delivery=SILENT,
        relevance=_recent_context_available,
    ),
    Candidate(
        direction=Direction.SOCIAL,
        prompt=(
            "Greet the user warmly and briefly, acknowledging the time of day and "
            "anything notable you remember about them. One or two sentences."
        ),
        usefulness=0.55,
        intrusiveness=0.4,
        risk=0.1,
        delivery=SPEAK,
        relevance=_present_and_quiet_for_a_while,
    ),
    Candidate(
        direction=Direction.WORK,
        prompt=(
            "Review any user-approved long-term goals in your workspace and make a "
            "small amount of progress on the highest-priority one. Do not start "
            "anything the user has not approved: if the work needs a decision, walk "
            "to the writing desk and draft a plan for them to approve instead."
        ),
        usefulness=0.5,
        intrusiveness=0.05,
        risk=0.25,
        delivery=SILENT,
        relevance=_approved_work_available,
    ),
]
