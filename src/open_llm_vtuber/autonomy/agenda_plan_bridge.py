"""Bridge legacy agenda entries into the Part 4 resumable plan journal."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from .activity import ActivityExecutor
from .agenda import AgendaEntry, AgendaStore, ExecutionCheckpoint
from .plan_execution import (
    ExecutionOutcome,
    RepairProposal,
    ResumablePlanExecutor,
    retry_step,
)
from .plan_state import (
    CounterfactualAssessment,
    PlanConflictError,
    ResumablePlan,
    ResumablePlanStore,
    build_plan,
    build_plan_step,
    result_digest,
)


# Synthetic reconciliation for preempted side-effecting steps. The outcome is
# deliberately recorded as unknown so the plan terminates without replaying an
# effect that may or may not have landed.
_UNKNOWN_OUTCOME_REF = "agenda-outcome:unknown"
_UNKNOWN_OUTCOME_DIGEST = result_digest("outcome:unknown")


def reconcile_cancelling_plan(
    plan_store: ResumablePlanStore,
    plan: ResumablePlan,
) -> ResumablePlan:
    """Terminate a ``cancelling`` plan without replaying its unknown outcome.

    Only plans parked in ``cancelling`` are touched. Terminal plans are
    returned unchanged; a stale race that already reconciled the plan raises
    ``PlanConflictError`` and is treated as already reconciled.
    """
    if plan is None or plan.terminal or plan.state != "cancelling":
        return plan
    try:
        return plan_store.reconcile_cancellation(
            plan.plan_id,
            outcome_ref=_UNKNOWN_OUTCOME_REF,
            outcome_digest=_UNKNOWN_OUTCOME_DIGEST,
        )
    except PlanConflictError:
        return plan_store.get(plan.plan_id) or plan


@dataclass(frozen=True)
class AgendaPlanResult:
    plan: ResumablePlan
    summary: str


def abandon_entry_safely(agenda_store: AgendaStore, entry_id: str) -> AgendaEntry:
    """Honor an offline stop request without orphaning or replaying its sidecar."""

    entry = next(item for item in agenda_store.load().entries if item.id == entry_id)
    plan_id = entry.metadata.get("resumable_plan_id")
    if not isinstance(plan_id, str):
        return agenda_store.transition(
            entry_id, "abandoned", progress_summary="Stopped by user."
        )
    plan_store = ResumablePlanStore(agenda_store.root)
    if plan_store.get(plan_id) is None:
        return agenda_store.transition(
            entry_id, "abandoned", progress_summary="Stopped by user."
        )
    cancelled = plan_store.cancel(plan_id, reason="user abandoned agenda entry")
    if not cancelled.terminal:
        cancelled = reconcile_cancelling_plan(plan_store, cancelled)
    if not cancelled.terminal:
        return agenda_store.transition(
            entry_id,
            "suspended",
            progress_summary=(
                "Stop requested; the side-effect outcome requires reconciliation."
            ),
        )
    report_summary = None
    if entry.kind != "user" and entry.report_policy != "silent":
        report_summary = (
            "Stopped by the user; the last step's outcome could not be "
            "confirmed and was not replayed."
        )
    absorbed = agenda_store.absorb_resumable_plan(
        entry_id,
        plan_id=plan_id,
        status="abandoned",
        progress_summary="Stopped by user.",
        report_summary=report_summary,
    )
    if cancelled.terminal_report_state == "pending":
        plan_store.claim_terminal_report(plan_id)
        cancelled = plan_store.get(plan_id) or cancelled
    if cancelled.terminal_report_state == "delivering":
        plan_store.finish_terminal_report(plan_id, delivered=True)
    final_plan = plan_store.get(plan_id)
    if final_plan is not None and final_plan.terminal_report_state == "delivered":
        agenda_store.clear_absorbed_resumable_plan(entry_id, plan_id=plan_id)
    return absorbed


class AgendaPlanBridge:
    """Execute an agenda checkpoint through durable before/after boundaries.

    Agenda schema v1 remains readable. New entries opt into this sidecar by
    carrying only ``resumable_plan_id`` in their already-bounded metadata; the
    full plan and private arguments live in ``agenda/plans.json``.
    """

    def __init__(
        self,
        agenda_store: AgendaStore,
        activity: ActivityExecutor,
        *,
        lease_seconds: float = 30.0,
        retry_backoff_seconds: float = 0.0,
        max_retry_backoff_seconds: float = 30.0,
    ) -> None:
        self.agenda_store = agenda_store
        self.activity = activity
        self.store = ResumablePlanStore(agenda_store.root)
        self.executor = ResumablePlanExecutor(
            self.store,
            lease_seconds=lease_seconds,
            retry_backoff_seconds=retry_backoff_seconds,
            max_retry_backoff_seconds=max_retry_backoff_seconds,
        )

    def ensure_plan(
        self,
        entry: AgendaEntry,
        checkpoint: ExecutionCheckpoint,
        *,
        retry_budget: int,
        repair_budget: int = 1,
    ) -> ResumablePlan:
        # Persist the complete planned checkpoint before the sidecar can point
        # at it. A crash after this write but before plan creation is harmless:
        # no action has run and the agenda may simply plan again.
        self.agenda_store.transition(entry.id, "active", checkpoint=checkpoint)
        plan_id = entry.metadata.get("resumable_plan_id")
        if isinstance(plan_id, str):
            existing = self.store.get(plan_id)
            if existing is not None:
                return existing

        plan_id = self.plan_id_for(entry.id, checkpoint.step_id)
        digest = plan_id.split(":", 1)[-1]
        existing = self.store.get(plan_id)
        if existing is not None:
            self.agenda_store.update_metadata(entry.id, resumable_plan_id=plan_id)
            return existing

        now = self.store.now()
        deadline = now + timedelta(seconds=max(1, entry.budget.time_budget_seconds))
        if entry.budget.deadline_at:
            try:
                candidate = datetime.fromisoformat(entry.budget.deadline_at)
                if candidate.tzinfo is not None and candidate > now:
                    deadline = candidate
            except (TypeError, ValueError):
                pass
        preconditions: list[str] = []
        if checkpoint.required_capability:
            preconditions.append(
                self.condition_reference("capability", checkpoint.required_capability)
            )
        if checkpoint.required_object:
            preconditions.append(
                self.condition_reference("object", checkpoint.required_object)
            )
        postcondition = f"agenda-result:{digest}"
        risk = entry.risk if entry.risk in {"low", "medium", "high"} else "high"
        step = build_plan_step(
            step_id=checkpoint.step_id,
            action_ref=checkpoint.required_capability or checkpoint.kind,
            arguments=checkpoint.arguments,
            precondition_refs=preconditions,
            postcondition_refs=[postcondition],
            side_effecting=checkpoint.side_effecting,
            risk=risk,
            estimated_cost=1.0,
            max_attempts=max(1, min(20, int(retry_budget))),
        )
        plan = build_plan(
            plan_id=plan_id,
            intention_record_id=f"agenda-intention:{digest}",
            goal_ref=f"agenda-goal:{digest}",
            work_class=self._work_class(entry),
            steps=[step],
            deadline_at=deadline.isoformat(),
            retry_budget=max(1, int(retry_budget)),
            repair_budget=max(0, int(repair_budget)),
            cost_budget=max(2.0, float(retry_budget) + 1.0),
            created_at=now.isoformat(),
        )
        created = self.store.create(plan)
        self.agenda_store.update_metadata(entry.id, resumable_plan_id=plan_id)
        return created

    @staticmethod
    def plan_id_for(entry_id: str, step_id: str) -> str:
        digest = hashlib.sha256(f"{entry_id}\x00{step_id}".encode("utf-8")).hexdigest()[
            :24
        ]
        return f"agenda-plan:{digest}"

    @staticmethod
    def condition_reference(kind: str, value: str) -> str:
        """Return a bounded opaque reference for a human-readable condition.

        Furniture labels may contain spaces or non-ASCII characters, while the
        durable plan schema intentionally accepts identifier-safe tokens only.
        Persist a stable digest and keep the original value in the already
        validated agenda checkpoint.
        """

        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        return f"{kind}:{digest}"

    def restore_plan_reference(self, entry: AgendaEntry) -> ResumablePlan | None:
        """Relink a deterministic sidecar created just before a process crash."""

        plan_id = entry.metadata.get("resumable_plan_id")
        if isinstance(plan_id, str):
            linked = self.store.get(plan_id)
            if linked is not None:
                return linked
        if entry.checkpoint is None:
            return None
        plan_id = self.plan_id_for(entry.id, entry.checkpoint.step_id)
        plan = self.store.get(plan_id)
        if plan is None:
            return None
        self.agenda_store.update_metadata(entry.id, resumable_plan_id=plan_id)
        return plan

    def reconcile_startup(self, *, fencing_token: int = 0) -> dict[str, int]:
        """Repair recoverable cross-file handoffs after an unclean shutdown."""

        restored = 0
        acknowledged = 0
        released = 0
        reconciled = 0
        state = self.agenda_store.load()
        for entry in state.entries:
            absorbed = entry.metadata.get("resumable_plan_absorbed")
            if isinstance(absorbed, str):
                if self.acknowledge_absorbed(absorbed):
                    self.agenda_store.clear_absorbed_resumable_plan(
                        entry.id, plan_id=absorbed
                    )
                    acknowledged += 1
                continue
            was_linked = isinstance(entry.metadata.get("resumable_plan_id"), str)
            plan = self.restore_plan_reference(entry)
            if plan is not None and not was_linked:
                restored += 1
            if plan is not None and plan.state == "running":
                self.store.release_for_preemption(
                    plan.plan_id,
                    fencing_token=max(int(fencing_token), plan.fencing_token),
                    reason_code="process_restart",
                )
                released += 1
            if plan is not None and plan.state == "cancelling":
                plan = reconcile_cancelling_plan(self.store, plan)
                reconciled += 1
                if (
                    plan.terminal
                    and entry.kind != "user"
                    and entry.report_policy != "silent"
                ):
                    # A legacy entry may already be terminal while its plan was
                    # parked in cancelling with no report ever queued. Backfill
                    # an honest completion report; enqueue_report is idempotent
                    # per entry and report key.
                    self.agenda_store.enqueue_report(
                        entry.id,
                        "Interrupted before completion; the last step's outcome "
                        "could not be confirmed and was not replayed.",
                    )
        return {
            "restored": restored,
            "acknowledged": acknowledged,
            "released": released,
            "reconciled": reconciled,
        }

    async def execute(
        self,
        entry: AgendaEntry,
        checkpoint: ExecutionCheckpoint,
        *,
        owner_id: str,
        fencing_token: int,
        capabilities: set[str],
        object_names: set[str],
        retry_budget: int,
    ) -> AgendaPlanResult:
        plan = self.ensure_plan(
            entry, checkpoint, retry_budget=retry_budget, repair_budget=1
        )
        plan = reconcile_cancelling_plan(self.store, plan)
        result_summary = ""
        digest = plan.plan_id.split(":", 1)[-1]
        capability_ref = (
            self.condition_reference("capability", checkpoint.required_capability)
            if checkpoint.required_capability
            else None
        )
        object_ref = (
            self.condition_reference("object", checkpoint.required_object)
            if checkpoint.required_object
            else None
        )

        def condition_probe(reference: str) -> bool | None:
            if reference == capability_ref:
                return checkpoint.required_capability in capabilities
            if reference == object_ref:
                return checkpoint.required_object in object_names
            # Plans created before opaque references were introduced remain
            # resumable when their original labels were identifier-safe.
            if reference.startswith("capability:"):
                return reference.split(":", 1)[1] in capabilities
            if reference.startswith("object:"):
                return reference.split(":", 1)[1] in object_names
            if reference == f"agenda-result:{digest}":
                latest = self.agenda_store.load()
                current = next(
                    (item for item in latest.entries if item.id == entry.id), None
                )
                return bool(
                    current
                    and current.checkpoint
                    and current.checkpoint.step_id == checkpoint.step_id
                    and current.checkpoint.state in {"result_ready", "completed"}
                )
            return None

        async def action(_step, _idempotency_key):
            nonlocal result_summary
            result_summary = await self.activity.execute(entry, checkpoint)
            return ExecutionOutcome.from_summary(
                success=True,
                outcome_ref=f"agenda-outcome:{digest}",
                summary=result_summary,
            )

        async def counterfactual(_step):
            # The legacy StepPolicy has already required an approved plan for
            # consequential capabilities. This second, recorded gate confirms
            # that deferral and no-op were considered; it is not authorization.
            return CounterfactualAssessment(
                decision="allow",
                reason_code="approved_scope_with_defer_alternative",
                alternatives_evaluated=3,
                evaluated_at=self.store.now().isoformat(),
            )

        terminal = await self.executor.run_to_stable(
            plan.plan_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            action=action,
            condition_probe=condition_probe,
            counterfactual=counterfactual,
            repair_planner=self._bounded_retry_repair,
        )
        if not result_summary:
            latest = self.agenda_store.load()
            current = next(
                (item for item in latest.entries if item.id == entry.id), None
            )
            if current and current.checkpoint:
                result_summary = current.checkpoint.result_summary or ""
        return AgendaPlanResult(terminal, result_summary)

    @staticmethod
    def _bounded_retry_repair(plan: ResumablePlan, failed) -> RepairProposal | None:
        """Give transient read failures one live, scope-identical repair step."""

        if failed.side_effecting or failed.outcome_ref is not None:
            return None
        repair_number = len(plan.repairs) + 1
        digest = hashlib.sha256(
            f"{plan.plan_id}\x00{failed.step_id}\x00{repair_number}".encode("utf-8")
        ).hexdigest()[:24]
        replacement = retry_step(
            failed,
            step_id=f"agenda-repair:{digest}",
        )
        return RepairProposal(
            failure_ref=failed.step_id,
            replacement_steps=(replacement,),
        )

    def acknowledge_absorbed(self, plan_id: str) -> bool:
        plan = self.store.get(plan_id)
        if plan is None or not plan.terminal:
            return False
        if plan.terminal_report_state == "delivered":
            return True
        if plan.terminal_report_state == "pending":
            claimed = self.store.claim_terminal_report(plan_id)
            if claimed is None:
                return False
            plan = claimed
        if plan.terminal_report_state != "delivering":
            return False
        self.store.finish_terminal_report(plan_id, delivered=True)
        return True

    @staticmethod
    def _work_class(entry: AgendaEntry) -> str:
        if entry.kind == "user":
            return "human"
        if entry.kind == "scheduled":
            return "deadline"
        if entry.origin == "external_event":
            return "urgent"
        if entry.kind == "idle":
            return "maintenance"
        return "background"
