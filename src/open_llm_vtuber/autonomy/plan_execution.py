"""Bounded execution, recovery, repair, and counterfactual plan evaluation."""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Iterable, Literal, Sequence

from .plan_state import (
    CounterfactualAssessment,
    PlanConflictError,
    PlanStep,
    PlanValidationError,
    ResumablePlan,
    ResumablePlanStore,
    build_plan_step,
    result_digest,
)

ConditionProbe = Callable[[str], bool | None | Awaitable[bool | None]]
ActionRunner = Callable[
    [PlanStep, str], "ExecutionOutcome | Awaitable[ExecutionOutcome]"
]
CounterfactualRunner = Callable[
    [PlanStep], CounterfactualAssessment | Awaitable[CounterfactualAssessment]
]
RepairPlanner = Callable[
    [ResumablePlan, PlanStep],
    "RepairProposal | None | Awaitable[RepairProposal | None]",
]
CrashHook = Callable[[str, ResumablePlan], None]


class InjectedProcessCrash(BaseException):
    """Test-only crash signal that normal execution error handling cannot swallow."""


@dataclass(frozen=True)
class ExecutionOutcome:
    """Content-free durable receipt returned by an action adapter."""

    success: bool
    outcome_ref: str
    outcome_digest: str
    reason_code: str = "action_completed"
    safe_to_retry: bool = False

    @classmethod
    def from_summary(
        cls,
        *,
        success: bool,
        outcome_ref: str,
        summary: str,
        reason_code: str = "action_completed",
        safe_to_retry: bool = False,
    ) -> "ExecutionOutcome":
        return cls(
            success=success,
            outcome_ref=outcome_ref,
            outcome_digest=result_digest(summary),
            reason_code=reason_code,
            safe_to_retry=safe_to_retry,
        )


@dataclass(frozen=True)
class RepairProposal:
    failure_ref: str
    replacement_steps: tuple[PlanStep, ...]


@dataclass(frozen=True)
class ExecutionPass:
    plan_id: str
    plan_state: str
    step_id: str | None
    disposition: Literal[
        "progressed",
        "terminal",
        "blocked",
        "repairing",
        "waiting",
    ]
    reason_code: str


@dataclass(frozen=True)
class CounterfactualOption:
    option_ref: str
    expected_utility: float
    residual_risk: float
    estimated_cost: float = 0.0

    def validate(self) -> None:
        if not self.option_ref or len(self.option_ref) > 200:
            raise ValueError("counterfactual option requires a bounded reference")
        for name, value in (
            ("expected_utility", self.expected_utility),
            ("residual_risk", self.residual_risk),
            ("estimated_cost", self.estimated_cost),
        ):
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"counterfactual {name} must be finite")
        if not 0 <= self.residual_risk <= 1:
            raise ValueError("counterfactual residual risk must be between 0 and 1")
        if self.estimated_cost < 0:
            raise ValueError("counterfactual cost cannot be negative")


def evaluate_counterfactual(
    candidate: CounterfactualOption,
    alternatives: Sequence[CounterfactualOption],
    *,
    utility_margin: float = 0.1,
    maximum_residual_risk: float = 0.85,
    evaluated_at: datetime | None = None,
) -> CounterfactualAssessment:
    """Prefer a materially safer near-equivalent; block excessive residual risk.

    This is a planning aid, not authorization.  Part 5's central policy kernel
    still decides whether an allowed candidate may touch the outside world.
    """

    candidate.validate()
    if not alternatives or len(alternatives) > 15:
        raise ValueError("counterfactual evaluation requires 1 to 15 alternatives")
    for alternative in alternatives:
        alternative.validate()
    # State validation rejects a local non-zero offset, so normalize explicitly.
    timestamp = (
        (evaluated_at or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .isoformat()
    )
    if candidate.residual_risk > maximum_residual_risk:
        return CounterfactualAssessment(
            decision="block",
            reason_code="residual_risk_exceeds_limit",
            alternatives_evaluated=len(alternatives) + 1,
            evaluated_at=timestamp,
        )
    safer = [
        option
        for option in alternatives
        if option.residual_risk < candidate.residual_risk
        and option.expected_utility >= candidate.expected_utility - utility_margin
        and option.estimated_cost <= candidate.estimated_cost
    ]
    if safer:
        return CounterfactualAssessment(
            decision="revise",
            reason_code="safer_near_equivalent_available",
            alternatives_evaluated=len(alternatives) + 1,
            evaluated_at=timestamp,
        )
    return CounterfactualAssessment(
        decision="allow",
        reason_code="no_safer_near_equivalent",
        alternatives_evaluated=len(alternatives) + 1,
        evaluated_at=timestamp,
    )


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class ResumablePlanExecutor:
    """Execute one durable transition at a time through an idempotent adapter."""

    def __init__(
        self,
        store: ResumablePlanStore,
        *,
        lease_seconds: float = 30.0,
        max_passes: int = 512,
    ) -> None:
        self.store = store
        self.lease_seconds = max(1.0, min(300.0, float(lease_seconds)))
        self.max_passes = max(1, min(10_000, int(max_passes)))

    async def resume_once(
        self,
        plan_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        action: ActionRunner,
        condition_probe: ConditionProbe,
        counterfactual: CounterfactualRunner | None = None,
        repair_planner: RepairPlanner | None = None,
        crash_hook: CrashHook | None = None,
    ) -> ExecutionPass:
        plan = self.store.get(plan_id)
        if plan is None:
            raise KeyError(plan_id)
        if plan.terminal:
            return self._pass(plan, "terminal", "already_terminal")
        if datetime.fromisoformat(plan.deadline_at) <= self.store.now():
            expired = self.store.expire(plan_id)
            return self._pass(
                expired,
                "terminal" if expired.terminal else "blocked",
                "deadline_expired",
            )
        if plan.state == "repairing":
            return await self._repair(plan, repair_planner)
        if plan.state in {"blocked", "cancelling"}:
            return self._pass(plan, "blocked", plan.blocked_reason or "blocked")

        if plan.state != "running" or plan.owner_id != owner_id:
            plan = self.store.claim(
                plan_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                lease_seconds=self.lease_seconds,
            )
            self._crash(crash_hook, "after_claim", plan)
        else:
            plan = self.store.heartbeat(
                plan_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                lease_seconds=self.lease_seconds,
            )

        step = plan.current_step
        if step is None:
            raise PlanConflictError("nonterminal plan has no current step")

        if step.state == "result_ready":
            failed_ref = await self._first_unsatisfied(
                step.postcondition_refs, condition_probe
            )
            if failed_ref is not None:
                failed = self.store.fail_current(
                    plan_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    reason_code="postcondition_failed",
                    error=f"postcondition_failed:{failed_ref}",
                    retryable=False,
                )
                if failed.state == "repairing":
                    return await self._repair(failed, repair_planner)
                return self._pass(failed, "terminal", "postcondition_failed")
            committed = self.store.commit_step(
                plan_id, owner_id=owner_id, fencing_token=fencing_token
            )
            self._crash(crash_hook, "after_commit", committed)
            return self._pass(
                committed,
                "terminal" if committed.terminal else "progressed",
                "step_committed",
            )

        if step.state in {"pending", "ready"}:
            failed_ref = await self._first_unsatisfied(
                step.precondition_refs, condition_probe
            )
            if failed_ref is not None:
                failed = self.store.fail_current(
                    plan_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    reason_code="precondition_failed",
                    error=f"precondition_failed:{failed_ref}",
                    retryable=False,
                )
                if failed.state == "repairing":
                    return await self._repair(failed, repair_planner)
                return self._pass(failed, "terminal", "precondition_failed")

            if step.risk == "high" and step.counterfactual is None:
                if counterfactual is None:
                    blocked = self.store.block_current(
                        plan_id,
                        owner_id=owner_id,
                        fencing_token=fencing_token,
                        reason_code="counterfactual_missing",
                        detail="high-risk action has no counterfactual evaluator",
                    )
                    return self._pass(blocked, "blocked", "counterfactual_missing")
                assessment = await _resolve(counterfactual(step))
                step = self.store.record_counterfactual(
                    plan_id,
                    assessment,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                )
                if assessment.decision != "allow":
                    blocked = self.store.block_current(
                        plan_id,
                        owner_id=owner_id,
                        fencing_token=fencing_token,
                        reason_code=f"counterfactual_{assessment.decision}",
                        detail=assessment.reason_code,
                    )
                    return self._pass(
                        blocked,
                        "blocked",
                        f"counterfactual_{assessment.decision}",
                    )

            step = self.store.begin_step(
                plan_id, owner_id=owner_id, fencing_token=fencing_token
            )
            current = self.store.get(plan_id)
            assert current is not None
            self._crash(crash_hook, "after_before_checkpoint", current)

        if step.state != "executing":
            # begin_step returns an executing or already-result-ready step. The
            # latter is handled at the top of the next bounded pass.
            return self._pass(plan, "waiting", "step_not_executable")

        try:
            outcome = await _resolve(action(step, step.idempotency_key))
        except Exception as exc:
            if step.side_effecting:
                blocked = self.store.block_current(
                    plan_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    reason_code="side_effect_outcome_unknown",
                    detail=f"{type(exc).__name__}:{str(exc)[:300]}",
                )
                return self._pass(blocked, "blocked", "side_effect_outcome_unknown")
            failed = self.store.fail_current(
                plan_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                reason_code="action_exception",
                error=f"{type(exc).__name__}:{str(exc)[:300]}",
                retryable=True,
            )
            if failed.state == "repairing":
                return await self._repair(failed, repair_planner)
            return self._pass(
                failed,
                "terminal" if failed.terminal else "progressed",
                "action_exception",
            )

        if not isinstance(outcome, ExecutionOutcome):
            raise TypeError("action runner must return ExecutionOutcome")
        current = self.store.get(plan_id)
        assert current is not None
        self._crash(crash_hook, "after_action", current)
        self.store.record_result(
            plan_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            outcome_ref=outcome.outcome_ref,
            outcome_digest=outcome.outcome_digest,
        )
        result_ready = self.store.get(plan_id)
        assert result_ready is not None
        self._crash(crash_hook, "after_after_checkpoint", result_ready)

        if not outcome.success:
            failed = self.store.fail_current(
                plan_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                reason_code=outcome.reason_code,
                error=outcome.reason_code,
                retryable=outcome.safe_to_retry,
            )
            if failed.state == "repairing":
                return await self._repair(failed, repair_planner)
            return self._pass(
                failed,
                "terminal" if failed.terminal else "progressed",
                outcome.reason_code,
            )

        # The postcondition is intentionally evaluated in the *next* pass.
        # A crash at this exact boundary then follows the same path as a clean
        # run and never needs to replay the action.
        return self._pass(result_ready, "progressed", "result_checkpointed")

    async def run_to_stable(
        self,
        plan_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        action: ActionRunner,
        condition_probe: ConditionProbe,
        counterfactual: CounterfactualRunner | None = None,
        repair_planner: RepairPlanner | None = None,
        crash_hook: CrashHook | None = None,
    ) -> ResumablePlan:
        for _ in range(self.max_passes):
            result = await self.resume_once(
                plan_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                action=action,
                condition_probe=condition_probe,
                counterfactual=counterfactual,
                repair_planner=repair_planner,
                crash_hook=crash_hook,
            )
            plan = self.store.get(plan_id)
            assert plan is not None
            if result.disposition in {
                "terminal",
                "blocked",
                "repairing",
                "waiting",
            }:
                return plan
        raise RuntimeError("plan executor exhausted its bounded transition budget")

    async def _repair(
        self,
        plan: ResumablePlan,
        repair_planner: RepairPlanner | None,
    ) -> ExecutionPass:
        step = plan.current_step
        if step is None:
            failed = self.store.abandon_repair(
                plan.plan_id, reason_code="repair_missing_failure"
            )
            return self._pass(failed, "terminal", "repair_missing_failure")
        if repair_planner is None:
            return self._pass(plan, "repairing", "repair_planner_unavailable")
        try:
            proposal = await _resolve(repair_planner(plan, step))
        except Exception:
            failed = self.store.abandon_repair(
                plan.plan_id, reason_code="repair_planner_failed"
            )
            return self._pass(failed, "terminal", "repair_planner_failed")
        if proposal is None:
            failed = self.store.abandon_repair(
                plan.plan_id, reason_code="repair_declined"
            )
            return self._pass(failed, "terminal", "repair_declined")
        if not isinstance(proposal, RepairProposal):
            failed = self.store.abandon_repair(
                plan.plan_id, reason_code="repair_proposal_invalid"
            )
            return self._pass(failed, "terminal", "repair_proposal_invalid")
        if proposal.failure_ref != step.step_id:
            failed = self.store.abandon_repair(
                plan.plan_id, reason_code="repair_wrong_failure_ref"
            )
            return self._pass(failed, "terminal", "repair_wrong_failure_ref")
        # Fail closed against scope expansion. A repair may retry or reorder
        # known action classes; adding a new capability belongs in a new plan.
        allowed_actions = {item.action_ref for item in plan.steps}
        if any(
            replacement.action_ref not in allowed_actions
            for replacement in proposal.replacement_steps
        ):
            failed = self.store.abandon_repair(
                plan.plan_id, reason_code="repair_scope_expansion"
            )
            return self._pass(failed, "terminal", "repair_scope_expansion")
        try:
            repaired = self.store.apply_repair(
                plan.plan_id,
                failure_ref=proposal.failure_ref,
                replacement_steps=proposal.replacement_steps,
            )
        except (PlanConflictError, PlanValidationError):
            failed = self.store.abandon_repair(
                plan.plan_id, reason_code="repair_rejected"
            )
            return self._pass(failed, "terminal", "repair_rejected")
        return self._pass(repaired, "progressed", "repair_applied")

    @staticmethod
    async def _first_unsatisfied(
        references: Iterable[str], probe: ConditionProbe
    ) -> str | None:
        for reference in references:
            result = await _resolve(probe(reference))
            # Unknown is not approval. Missing evidence fails closed exactly as
            # a known-false condition does, while keeping the ref inspectable.
            if result is not True:
                return reference
        return None

    @staticmethod
    def _crash(hook: CrashHook | None, phase: str, plan: ResumablePlan) -> None:
        if hook is not None:
            hook(phase, plan)

    @staticmethod
    def _pass(
        plan: ResumablePlan,
        disposition: Literal[
            "progressed", "terminal", "blocked", "repairing", "waiting"
        ],
        reason_code: str,
    ) -> ExecutionPass:
        return ExecutionPass(
            plan_id=plan.plan_id,
            plan_state=plan.state,
            step_id=plan.current_step.step_id if plan.current_step else None,
            disposition=disposition,
            reason_code=reason_code,
        )


def retry_step(
    failed: PlanStep,
    *,
    step_id: str,
    precondition_refs: Iterable[str] | None = None,
) -> PlanStep:
    """Small helper for repair planners that retain the original action scope."""

    return build_plan_step(
        step_id=step_id,
        action_ref=failed.action_ref,
        arguments=failed.arguments,
        precondition_refs=(
            failed.precondition_refs
            if precondition_refs is None
            else list(precondition_refs)
        ),
        postcondition_refs=failed.postcondition_refs,
        side_effecting=failed.side_effecting,
        risk=failed.risk,
        estimated_cost=failed.estimated_cost,
        max_attempts=failed.max_attempts,
    )
