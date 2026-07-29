"""The autonomous decision engine.

Implements the scope's loop as a *pure* function of (observation, state,
settings, rng):

    observe context -> propose candidate actions -> score them by usefulness,
    novelty, intrusiveness and risk -> choose: act silently / ask permission /
    speak briefly / do nothing.

Keeping it pure (no I/O, deterministic given an injected rng) makes the whole
behavior policy unit-testable; the driver supplies the observation and carries
out whatever the planner decides.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .directions import ASK, CANDIDATES, Candidate, Direction, SILENT, SPEAK
from .observation import Observation
from .presence import PresenceLedger, attention_cost
from .settings import AutonomySettings
from .state import AutonomyState

# Decision actions.
ACT_SILENT = "silent"
ACT_ASK = "ask"
ACT_SPEAK = "speak"
ACT_NOTHING = "nothing"

# Scoring weights (sum of positives ~0.75; penalties subtract).
_W_USEFULNESS = 0.5
_W_NOVELTY = 0.25
_W_INTRUSIVENESS = 0.3
_W_RISK = 0.2
_W_FEEDBACK = 0.2

# Chance of exploring a non-top candidate, for the scope's "random direction"
# flavor. Kept small so behavior is mostly sensible.
_EXPLORE_EPSILON = 0.15


@dataclass(frozen=True)
class PlannerDecision:
    action: str  # ACT_SILENT / ACT_ASK / ACT_SPEAK / ACT_NOTHING
    direction: Optional[Direction] = None
    prompt: str = ""
    report: bool = False  # whether the agent should speak the result to the user
    score: float = 0.0
    reason: str = ""
    scores: dict[str, float] = field(default_factory=dict)
    attention_cost: float = 0.0
    # Which learned habit (if any) the chosen candidate was compiled from.
    habit_id: Optional[str] = None

    @property
    def is_act(self) -> bool:
        return self.action != ACT_NOTHING

    @property
    def spoke(self) -> bool:
        return self.action in (ACT_ASK, ACT_SPEAK)


def _score_candidate(
    candidate: Candidate,
    relevance: float,
    state: AutonomyState,
    settings: AutonomySettings,
    presence: Optional[PresenceLedger] = None,
) -> float:
    novelty = state.novelty(candidate.direction)
    base = _W_USEFULNESS * candidate.usefulness + _W_NOVELTY * novelty
    penalty = (
        _W_INTRUSIVENESS * candidate.intrusiveness * settings.intrusion_sensitivity
        + _W_RISK * candidate.risk
    )
    score = (base - penalty) * relevance
    if candidate.delivery in (SPEAK, ASK):
        score += settings.speak_bias
    if presence is not None:
        score += _W_FEEDBACK * presence.affinity(candidate.direction.value)
    return score


class AutonomyPlanner:
    """Stateless policy: given the world, decide what (if anything) to do."""

    def propose_and_choose(
        self,
        observation: Observation,
        state: AutonomyState,
        settings: AutonomySettings,
        rng: Optional[random.Random] = None,
        now=None,
        presence: Optional[PresenceLedger] = None,
        extra_candidates: Optional[list[Candidate]] = None,
    ) -> PlannerDecision:
        rng = rng or random.Random()

        if not settings.enabled:
            return PlannerDecision(ACT_NOTHING, reason="autonomy disabled")

        since_last_action = state.seconds_since_last_action(now)
        if (
            since_last_action is not None
            and since_last_action < settings.min_action_gap_seconds
        ):
            return PlannerDecision(
                ACT_NOTHING,
                reason="autonomous action cooldown is active",
            )

        # --- observe + propose: keep only context-relevant candidates -------
        # Learned habits (Habit Engine) compete on equal footing with the
        # built-in candidates: same scoring, same gates, no extra privilege.
        pool = list(CANDIDATES) + list(extra_candidates or [])
        scored: list[tuple[float, Candidate]] = []
        for candidate in pool:
            relevance = max(0.0, min(1.0, candidate.relevance(observation)))
            if relevance <= 0.0:
                continue
            score = _score_candidate(
                candidate, relevance, state, settings, presence=presence
            )
            scored.append((score, candidate))

        if not scored:
            return PlannerDecision(ACT_NOTHING, reason="no relevant candidates")

        # Deterministic ordering: score desc, then a stable direction order.
        order = {d: i for i, d in enumerate(Direction)}
        scored.sort(key=lambda sc: (-sc[0], order[sc[1].direction]))

        # --- occasional exploration (random direction) ----------------------
        if len(scored) > 1 and rng.random() < _EXPLORE_EPSILON:
            best_score, best = scored[rng.randrange(len(scored))]
        else:
            best_score, best = scored[0]

        breakdown = {c.direction.value: round(s, 4) for s, c in scored}

        # --- confidence + cadence gates -------------------------------------
        if best_score < settings.act_threshold:
            return PlannerDecision(
                ACT_NOTHING,
                best.direction,
                best.prompt,
                score=best_score,
                reason="below confidence threshold",
                scores=breakdown,
                habit_id=best.habit_id,
            )

        # Sometimes simply rest, so the agent isn't relentless.
        if rng.random() > settings.base_act_probability:
            return PlannerDecision(
                ACT_NOTHING,
                best.direction,
                best.prompt,
                score=best_score,
                reason="chose to rest this tick",
                scores=breakdown,
                habit_id=best.habit_id,
            )

        # --- decide delivery ------------------------------------------------
        action = self._decide_delivery(
            best, best_score, observation, state, settings, now
        )
        if action == ACT_NOTHING:
            return PlannerDecision(
                ACT_NOTHING,
                best.direction,
                best.prompt,
                score=best_score,
                reason="too soon to interrupt the user",
                scores=breakdown,
                habit_id=best.habit_id,
            )

        cost = attention_cost(action)
        if presence is not None:
            allowed, gate_reason = presence.can_afford(
                cost,
                user_facing=action in (ACT_ASK, ACT_SPEAK),
                focus_mode=observation.focus_mode,
                presence_fresh=observation.presence_fresh,
                now=now,
            )
            if not allowed:
                return PlannerDecision(
                    ACT_NOTHING,
                    best.direction,
                    best.prompt,
                    score=best_score,
                    reason=gate_reason,
                    scores=breakdown,
                    attention_cost=cost,
                    habit_id=best.habit_id,
                )

        return PlannerDecision(
            action=action,
            direction=best.direction,
            prompt=best.prompt,
            report=action in (ACT_ASK, ACT_SPEAK),
            score=best_score,
            reason=f"acting on {best.direction.value} via {action}",
            scores=breakdown,
            attention_cost=cost,
            habit_id=best.habit_id,
        )

    @staticmethod
    def _decide_delivery(
        candidate: Candidate,
        score: float,
        observation: Observation,
        state: AutonomyState,
        settings: AutonomySettings,
        now,
    ) -> str:
        # Silent actions are invisible to the user — always allowed.
        if candidate.delivery == SILENT:
            return ACT_SILENT

        # Spoken/ask actions must respect the minimum gap between interruptions.
        gap = observation.seconds_since_last_spoke
        if gap is None:
            gap = state.seconds_since_last_spoke(now)
        if gap is not None and gap < settings.min_speak_gap_seconds:
            return ACT_NOTHING  # don't interrupt again so soon

        # Confident enough to interrupt outright, or only enough to ask?
        if score >= settings.speak_threshold:
            return ACT_SPEAK if candidate.delivery == SPEAK else ACT_ASK
        return ACT_ASK
