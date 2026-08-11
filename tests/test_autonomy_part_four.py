from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.open_llm_vtuber.autonomy.activity import ActivityExecutor
from src.open_llm_vtuber.autonomy.agenda import (
    AgendaStore,
    ExecutionCheckpoint,
    create_entry,
)
from src.open_llm_vtuber.autonomy.agenda_plan_bridge import (
    AgendaPlanBridge,
    abandon_entry_safely,
    reconcile_cancelling_plan,
)
from src.open_llm_vtuber.autonomy.dual_loop import (
    DualLoopCoordinator,
    FairPlanScheduler,
)
from src.open_llm_vtuber.autonomy.plan_execution import (
    CounterfactualOption,
    ExecutionOutcome,
    InjectedProcessCrash,
    RepairProposal,
    ResumablePlanExecutor,
    evaluate_counterfactual,
    retry_step,
)
from src.open_llm_vtuber.autonomy.plan_state import (
    CounterfactualAssessment,
    PlanConflictError,
    PlanValidationError,
    ResumablePlanStore,
    build_plan,
    build_plan_step,
)


NOW = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)


def _plan(
    *,
    plan_id: str = "plan-one",
    step_id: str = "step-one",
    side_effecting: bool = True,
    risk: str = "low",
    work_class: str = "background",
    preconditions=(),
    postconditions=(),
    now: datetime = NOW,
):
    return build_plan(
        plan_id=plan_id,
        intention_record_id=f"intention:{plan_id}",
        goal_ref=f"goal:{plan_id}",
        work_class=work_class,
        steps=[
            build_plan_step(
                step_id=step_id,
                action_ref="calendar.write" if side_effecting else "calendar.read",
                arguments={"private_title": "do not expose"},
                precondition_refs=preconditions,
                postcondition_refs=postconditions,
                side_effecting=side_effecting,
                risk=risk,
                estimated_cost=1,
                max_attempts=2,
            )
        ],
        deadline_at=(now + timedelta(hours=1)).isoformat(),
        retry_budget=2,
        repair_budget=1,
        cost_budget=5,
        created_at=now.isoformat(),
    )


def test_plan_store_is_restart_safe_and_operator_snapshot_redacts_arguments(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    created = store.create(_plan())
    assert created.state == "ready"

    restarted = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    assert restarted.get("plan-one") == created
    snapshot = restarted.snapshot()
    assert "private_title" not in json.dumps(snapshot)
    assert snapshot["plans"][0]["step_count"] == 1


def test_plan_store_fails_closed_for_unknown_version_and_tampering(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan())
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["version"] = 99
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PlanValidationError, match="schema version"):
        store.load()


def test_stale_fencing_token_cannot_take_a_plan(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan())
    store.claim("plan-one", owner_id="owner-a", fencing_token=3, lease_seconds=30)
    with pytest.raises(PlanConflictError, match="stale|leased"):
        store.claim("plan-one", owner_id="owner-b", fencing_token=2, lease_seconds=30)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "crash_phase",
    [
        "after_claim",
        "after_before_checkpoint",
        "after_action",
        "after_after_checkpoint",
        "after_commit",
    ],
)
async def test_crash_at_every_transition_resumes_without_duplicate_side_effect(
    tmp_path, crash_phase
):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan())
    effects: dict[str, ExecutionOutcome] = {}
    physical_effects = 0

    async def action(_step, idempotency_key):
        nonlocal physical_effects
        if idempotency_key not in effects:
            physical_effects += 1
            effects[idempotency_key] = ExecutionOutcome.from_summary(
                success=True,
                outcome_ref="outcome-one",
                summary="calendar entry exists",
            )
        return effects[idempotency_key]

    crashed = False

    def crash_hook(phase, _plan):
        nonlocal crashed
        if phase == crash_phase and not crashed:
            crashed = True
            raise InjectedProcessCrash(phase)

    executor = ResumablePlanExecutor(store)
    for _ in range(8):
        try:
            result = await executor.resume_once(
                "plan-one",
                owner_id="owner-a",
                fencing_token=1,
                action=action,
                condition_probe=lambda _ref: True,
                crash_hook=crash_hook,
            )
        except InjectedProcessCrash:
            break
        if result.disposition == "terminal":
            break
    assert crashed

    # A new process reconstructs only from disk. The adapter's idempotency
    # registry models the tool/external endpoint contract used again in Part 5.
    restarted = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    terminal = await ResumablePlanExecutor(restarted).run_to_stable(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        action=action,
        condition_probe=lambda _ref: True,
    )
    assert terminal.state == "completed"
    assert terminal.terminal_report_state == "pending"
    assert physical_effects == 1


@pytest.mark.asyncio
async def test_result_checkpoint_prevents_read_only_replay(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan(side_effecting=False))
    calls = 0

    async def action(_step, _key):
        nonlocal calls
        calls += 1
        return ExecutionOutcome.from_summary(
            success=True, outcome_ref="read-outcome", summary="value"
        )

    executor = ResumablePlanExecutor(store)
    first = await executor.resume_once(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        action=action,
        condition_probe=lambda _ref: True,
    )
    assert first.reason_code == "result_checkpointed"
    restarted = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    terminal = await ResumablePlanExecutor(restarted).run_to_stable(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        action=action,
        condition_probe=lambda _ref: True,
    )
    assert terminal.state == "completed"
    assert calls == 1


@pytest.mark.asyncio
async def test_missing_precondition_fails_closed_and_bounded_repair_recovers(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan(preconditions=["calendar.available"]))
    available = False
    calls = 0

    async def action(_step, _key):
        nonlocal calls
        calls += 1
        return ExecutionOutcome.from_summary(
            success=True, outcome_ref=f"outcome-{calls}", summary="done"
        )

    async def repair(plan, failed):
        nonlocal available
        available = True
        return RepairProposal(
            failure_ref=failed.step_id,
            replacement_steps=(
                retry_step(
                    failed,
                    step_id="step-repaired",
                    precondition_refs=["calendar.available"],
                ),
            ),
        )

    terminal = await ResumablePlanExecutor(store).run_to_stable(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        action=action,
        condition_probe=lambda ref: available if ref == "calendar.available" else None,
        repair_planner=repair,
    )
    assert terminal.state == "completed"
    assert len(terminal.repairs) == 1
    assert terminal.repair_budget == 0
    assert calls == 1


@pytest.mark.asyncio
async def test_repair_cannot_expand_action_scope(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan(preconditions=["missing.evidence"]))

    async def repair(_plan_value, failed):
        return RepairProposal(
            failure_ref=failed.step_id,
            replacement_steps=(
                build_plan_step(
                    step_id="scope-expansion",
                    action_ref="email.send",
                    side_effecting=True,
                ),
            ),
        )

    final = await ResumablePlanExecutor(store).run_to_stable(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        action=lambda *_args: pytest.fail("blocked step must not execute"),
        condition_probe=lambda _ref: False,
        repair_planner=repair,
    )
    assert final.state == "failed"
    assert final.terminal_report_state == "pending"


def test_repair_cannot_inject_a_precompleted_step(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan(preconditions=["missing.evidence"]))
    store.claim("plan-one", owner_id="owner-a", fencing_token=1, lease_seconds=30)
    repairing = store.fail_current(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        reason_code="precondition_failed",
        error="missing",
        retryable=False,
    )
    forged = retry_step(repairing.current_step, step_id="forged-complete")
    forged.state = "completed"
    forged.outcome_ref = "forged-outcome"
    forged.outcome_digest = "a" * 64
    with pytest.raises(PlanValidationError, match="pristine"):
        store.apply_repair(
            "plan-one",
            failure_ref=repairing.current_step.step_id,
            replacement_steps=[forged],
        )


@pytest.mark.asyncio
async def test_unknown_side_effect_outcome_blocks_instead_of_replaying(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan())

    async def action(_step, _key):
        raise ConnectionError("connection lost after request")

    final = await ResumablePlanExecutor(store).run_to_stable(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        action=action,
        condition_probe=lambda _ref: True,
    )
    assert final.state == "blocked"
    assert "side_effect_outcome_unknown" in (final.blocked_reason or "")


@pytest.mark.asyncio
async def test_high_risk_step_requires_and_records_counterfactual(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan(risk="high"))
    final = await ResumablePlanExecutor(store).run_to_stable(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        action=lambda *_args: pytest.fail("missing assessment must block"),
        condition_probe=lambda _ref: True,
    )
    assert final.state == "blocked"
    assert final.blocked_reason.startswith("counterfactual_missing")


def test_counterfactual_prefers_safer_near_equivalent_and_blocks_excess_risk():
    revise = evaluate_counterfactual(
        CounterfactualOption("candidate", 0.8, 0.6, 2),
        [CounterfactualOption("preview", 0.75, 0.1, 1)],
        evaluated_at=NOW,
    )
    assert revise.decision == "revise"
    blocked = evaluate_counterfactual(
        CounterfactualOption("candidate", 1, 0.95, 1),
        [CounterfactualOption("defer", 0.1, 0, 0)],
        evaluated_at=NOW,
    )
    assert blocked.decision == "block"


@pytest.mark.asyncio
async def test_high_risk_allowed_assessment_can_execute(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan(risk="high"))

    async def assess(_step):
        return CounterfactualAssessment(
            decision="allow",
            reason_code="reviewed_alternatives",
            alternatives_evaluated=3,
            evaluated_at=NOW.isoformat(),
        )

    final = await ResumablePlanExecutor(store).run_to_stable(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        action=lambda *_args: ExecutionOutcome.from_summary(
            success=True, outcome_ref="safe-outcome", summary="done"
        ),
        condition_probe=lambda _ref: True,
        counterfactual=assess,
    )
    assert final.state == "completed"
    assert final.steps[0].counterfactual.decision == "allow"


def test_cancelling_executing_side_effect_requires_outcome_reconciliation(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan())
    store.claim("plan-one", owner_id="owner-a", fencing_token=1, lease_seconds=30)
    store.begin_step("plan-one", owner_id="owner-a", fencing_token=1)
    cancelling = store.cancel("plan-one", reason="user interruption")
    assert cancelling.state == "cancelling"
    assert cancelling.terminal_report_state == "none"
    cancelled = store.reconcile_cancellation(
        "plan-one",
        outcome_ref="reconciled-outcome",
        outcome_digest="a" * 64,
    )
    assert cancelled.state == "cancelled"
    assert cancelled.terminal_report_state == "pending"


def test_terminal_report_claim_recovers_after_crash(tmp_path):
    current = NOW
    store = ResumablePlanStore(tmp_path, clock=lambda: current)
    store.create(_plan())
    store.claim("plan-one", owner_id="owner-a", fencing_token=1, lease_seconds=30)
    store.begin_step("plan-one", owner_id="owner-a", fencing_token=1)
    store.record_result(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        outcome_ref="outcome-one",
        outcome_digest="a" * 64,
    )
    store.commit_step("plan-one", owner_id="owner-a", fencing_token=1)
    assert store.claim_terminal_report().terminal_report_state == "delivering"
    current = NOW + timedelta(minutes=6)
    restarted = ResumablePlanStore(tmp_path, clock=lambda: current)
    restarted.recover_expired()
    recovered = restarted.claim_terminal_report()
    assert recovered is not None
    delivered = restarted.finish_terminal_report(recovered.plan_id, delivered=True)
    assert delivered.terminal_report_state == "delivered"


def test_scheduler_human_preempts_and_nonhuman_classes_receive_fair_service():
    scheduler = FairPlanScheduler(starvation_seconds=10_000)
    plans = [
        _plan(plan_id="human", step_id="s-human", work_class="human"),
        _plan(plan_id="urgent", step_id="s-urgent", work_class="urgent"),
        _plan(plan_id="deadline", step_id="s-deadline", work_class="deadline"),
        _plan(plan_id="background", step_id="s-background", work_class="background"),
        _plan(plan_id="maintenance", step_id="s-maint", work_class="maintenance"),
    ]
    assert scheduler.choose(plans, now=NOW).plan_id == "human"
    nonhuman = plans[1:]
    selected = [scheduler.choose(nonhuman, now=NOW).work_class for _ in range(40)]
    assert set(selected) == {"urgent", "deadline", "background", "maintenance"}


@pytest.mark.asyncio
async def test_dual_loop_human_preemption_is_measured_within_budget():
    coordinator = DualLoopCoordinator(preemption_budget_seconds=0.25)
    started = asyncio.Event()

    async def deliberate():
        started.set()
        await asyncio.Event().wait()

    coordinator.start_deliberation(deliberate())
    await started.wait()
    value, receipt = await coordinator.run_reactive(lambda: asyncio.sleep(0, result=7))
    assert value == 7
    assert receipt.task_stopped is True
    assert receipt.within_budget is True
    assert receipt.latency_ms <= receipt.budget_ms * 1.1


def test_nonterminal_plan_requires_deadline_budget_and_block_reason():
    with pytest.raises(PlanValidationError, match="deadline"):
        build_plan(
            plan_id="bad-plan",
            intention_record_id="intention:bad",
            goal_ref="goal:bad",
            work_class="background",
            steps=[build_plan_step(step_id="bad-step", action_ref="wait")],
            deadline_at=NOW.isoformat(),
            created_at=NOW.isoformat(),
        )


@pytest.mark.asyncio
async def test_legacy_agenda_step_executes_through_resumable_sidecar(tmp_path):
    agenda = AgendaStore(tmp_path)
    entry = create_entry(
        kind="pursuit",
        title="Inspect the calendar",
        description="Read one local value.",
        origin="autonomous",
        evidence_refs=["observation:test"],
        completion_condition="The value is read.",
        time_budget_seconds=60,
        status="active",
    )
    agenda.add(entry)
    calls = 0

    async def run_step(_entry, _checkpoint):
        nonlocal calls
        calls += 1
        return "calendar value recorded"

    async def emit(_payload):
        return None

    checkpoint = ExecutionCheckpoint(
        step_id="agenda-step-one",
        kind="read_result",
        state="planned",
        required_capability="calendar.read",
        side_effecting=False,
    )
    bridge = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    result = await bridge.execute(
        entry,
        checkpoint,
        owner_id="agenda-owner",
        fencing_token=1,
        capabilities={"calendar.read"},
        object_names=set(),
        retry_budget=2,
    )
    assert result.plan.state == "completed"
    assert result.plan.terminal_report_state == "pending"
    assert result.summary == "calendar value recorded"
    assert agenda.load().entries[0].checkpoint.state == "completed"
    assert calls == 1

    # The agenda acknowledges only after it has absorbed the result. That
    # durable handoff survives recreating the bridge.
    restarted = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    restarted.acknowledge_absorbed(result.plan.plan_id)
    assert restarted.store.get(result.plan.plan_id).terminal_report_state == (
        "delivered"
    )


@pytest.mark.asyncio
async def test_agenda_plan_accepts_human_readable_furniture_labels(tmp_path):
    agenda = AgendaStore(tmp_path)
    entry = create_entry(
        kind="pursuit",
        title="Inspect installed skills",
        description="Walk to the tool rack and inspect one value.",
        origin="autonomous",
        evidence_refs=["observation:tool-rack"],
        completion_condition="The installed skills are inspected.",
        time_budget_seconds=60,
        status="active",
    )
    agenda.add(entry)

    async def run_step(_entry, _checkpoint):
        return "skills inspected"

    async def emit(_payload):
        return None

    checkpoint = ExecutionCheckpoint(
        step_id="inspect-skills",
        kind="invoke_tool",
        state="planned",
        required_object="tool rack",
        required_capability="list_skills",
        side_effecting=False,
    )
    bridge = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    result = await bridge.execute(
        entry,
        checkpoint,
        owner_id="agenda-owner",
        fencing_token=1,
        capabilities={"list_skills"},
        object_names={"tool rack"},
        retry_budget=2,
    )

    assert result.plan.state == "completed"
    assert result.summary == "skills inspected"
    references = result.plan.steps[0].precondition_refs
    assert len(references) == 2
    assert all(" " not in reference for reference in references)
    assert references[0].startswith("capability:")
    assert references[1].startswith("object:")


@pytest.mark.asyncio
async def test_agenda_absorption_is_atomic_idempotent_and_restart_reconciled(
    tmp_path,
):
    agenda = AgendaStore(tmp_path)
    entry = create_entry(
        kind="pursuit",
        title="Inspect one value",
        description="Read one value through a resumable sidecar.",
        origin="autonomous",
        evidence_refs=["observation:atomic-handoff"],
        completion_condition="The value is read.",
        time_budget_seconds=60,
        status="active",
    )
    agenda.add(entry)

    async def run_step(_entry, _checkpoint):
        return "durable result"

    async def emit(_payload):
        return None

    checkpoint = ExecutionCheckpoint(
        step_id="atomic-step",
        kind="read_result",
        state="planned",
        required_capability="calendar.read",
    )
    bridge = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    result = await bridge.execute(
        entry,
        checkpoint,
        owner_id="agenda-owner",
        fencing_token=1,
        capabilities={"calendar.read"},
        object_names=set(),
        retry_budget=2,
    )

    # This is the crash window: the agenda commits the terminal status and its
    # report, then the process dies before acknowledging the sidecar outbox.
    agenda.absorb_resumable_plan(
        entry.id,
        plan_id=result.plan.plan_id,
        status="completed",
        progress_summary="The value was read.",
        report_summary="The value was read.",
    )
    absorbed_revision = agenda.load().revision
    agenda.absorb_resumable_plan(
        entry.id,
        plan_id=result.plan.plan_id,
        status="completed",
        progress_summary="The value was read.",
        report_summary="The value was read.",
    )
    crashed = agenda.load()
    assert crashed.revision == absorbed_revision
    assert crashed.entries[0].status == "completed"
    assert crashed.entries[0].checkpoint is None
    assert len(crashed.reports) == 1
    assert crashed.entries[0].metadata["resumable_plan_absorbed"] == (
        result.plan.plan_id
    )

    restarted = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    reconciled = restarted.reconcile_startup()
    assert reconciled == {
        "restored": 0,
        "acknowledged": 1,
        "released": 0,
        "reconciled": 0,
    }
    final = agenda.load()
    assert final.entries[0].status == "completed"
    assert final.entries[0].metadata["resumable_plan_absorbed"] is None
    assert restarted.store.get(result.plan.plan_id).terminal_report_state == (
        "delivered"
    )


def test_startup_relinks_plan_created_before_agenda_reference(tmp_path):
    agenda = AgendaStore(tmp_path)
    entry = create_entry(
        kind="pursuit",
        title="Recover a plan link",
        description="Exercise the create-before-link crash boundary.",
        origin="autonomous",
        evidence_refs=["observation:link-recovery"],
        completion_condition="The link is recovered.",
        time_budget_seconds=60,
        status="active",
    )
    agenda.add(entry)
    checkpoint = ExecutionCheckpoint(
        step_id="recover-link-step",
        kind="read_result",
        state="planned",
    )

    async def run_step(_entry, _checkpoint):
        return "done"

    async def emit(_payload):
        return None

    bridge = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    created = bridge.ensure_plan(entry, checkpoint, retry_budget=2)
    agenda.update_metadata(entry.id, resumable_plan_id=None)

    restarted = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    assert restarted.reconcile_startup() == {
        "restored": 1,
        "acknowledged": 0,
        "released": 0,
        "reconciled": 0,
    }
    recovered = agenda.load().entries[0]
    assert recovered.metadata["resumable_plan_id"] == created.plan_id


@pytest.mark.asyncio
async def test_live_agenda_bridge_uses_bounded_scope_identical_read_repair(tmp_path):
    agenda = AgendaStore(tmp_path)
    entry = create_entry(
        kind="pursuit",
        title="Retry a transient read",
        description="Recover one bounded local read.",
        origin="autonomous",
        evidence_refs=["observation:transient-read"],
        completion_condition="The value is read.",
        time_budget_seconds=60,
        status="active",
    )
    agenda.add(entry)
    calls = 0

    async def run_step(_entry, _checkpoint):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("transient read failure")
        return "recovered value"

    async def emit(_payload):
        return None

    checkpoint = ExecutionCheckpoint(
        step_id="transient-read-step",
        kind="read_result",
        state="planned",
        required_capability="calendar.read",
        side_effecting=False,
    )
    bridge = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    result = await bridge.execute(
        entry,
        checkpoint,
        owner_id="agenda-owner",
        fencing_token=1,
        capabilities={"calendar.read"},
        object_names=set(),
        retry_budget=2,
    )
    assert result.plan.state == "completed"
    assert result.summary == "recovered value"
    assert calls == 3
    assert len(result.plan.repairs) == 1
    repaired = result.plan.steps[-1]
    assert repaired.action_ref == result.plan.steps[0].action_ref
    assert repaired.arguments == result.plan.steps[0].arguments
    assert repaired.side_effecting is False


@pytest.mark.asyncio
async def test_invalid_repair_is_terminal_instead_of_stuck_repairing(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan(side_effecting=False, preconditions=["missing.evidence"]))

    async def repair(_plan_value, failed):
        return RepairProposal(
            failure_ref=failed.step_id,
            replacement_steps=(
                build_plan_step(
                    step_id="risk-expanding-repair",
                    action_ref=failed.action_ref,
                    side_effecting=False,
                    risk="high",
                ),
            ),
        )

    final = await ResumablePlanExecutor(store).run_to_stable(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        action=lambda *_args: pytest.fail("precondition failure must not execute"),
        condition_probe=lambda _ref: False,
        repair_planner=repair,
    )
    assert final.state == "failed"
    assert final.transitions[-1].reason_code == "repair_rejected"


@pytest.mark.asyncio
async def test_retryable_failures_back_off_exponentially(monkeypatch, tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(
        build_plan(
            plan_id="backoff-plan",
            intention_record_id="intention:backoff",
            goal_ref="goal:backoff",
            work_class="background",
            steps=[
                build_plan_step(
                    step_id="step-retry",
                    action_ref="calendar.read",
                    arguments={},
                    side_effecting=False,
                    risk="low",
                    estimated_cost=1,
                    max_attempts=3,
                )
            ],
            deadline_at=(NOW + timedelta(hours=1)).isoformat(),
            retry_budget=3,
            repair_budget=1,
            cost_budget=5,
            created_at=NOW.isoformat(),
        )
    )
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.plan_execution.asyncio.sleep",
        fake_sleep,
    )

    async def always_fail(_step, _idempotency_key):
        raise ConnectionError("transient failure")

    async def abandon_repair(_plan_value, _failed):
        return None

    final = await ResumablePlanExecutor(
        store,
        retry_backoff_seconds=0.5,
        max_retry_backoff_seconds=2.0,
    ).run_to_stable(
        "backoff-plan",
        owner_id="owner-a",
        fencing_token=1,
        action=always_fail,
        condition_probe=lambda _ref: True,
        repair_planner=abandon_repair,
    )

    assert sleeps == [0.5, 1.0]
    assert final.state == "failed"
    assert final.transitions[-1].reason_code == "repair_declined"


@pytest.mark.asyncio
async def test_retry_backoff_is_disabled_by_default(monkeypatch, tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan(side_effecting=False))
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.plan_execution.asyncio.sleep",
        fake_sleep,
    )

    async def always_fail(_step, _idempotency_key):
        raise ConnectionError("transient failure")

    await ResumablePlanExecutor(store).run_to_stable(
        "plan-one",
        owner_id="owner-a",
        fencing_token=1,
        action=always_fail,
        condition_probe=lambda _ref: True,
    )

    assert sleeps == []


def test_deadline_sweep_expires_orphans_but_preserves_unknown_effects(tmp_path):
    current = NOW
    store = ResumablePlanStore(tmp_path, clock=lambda: current)
    store.create(_plan(plan_id="read-plan", side_effecting=False, now=NOW))
    store.create(_plan(plan_id="write-plan", side_effecting=True, now=NOW))
    store.claim("write-plan", owner_id="owner-a", fencing_token=1, lease_seconds=30)
    store.begin_step("write-plan", owner_id="owner-a", fencing_token=1)

    current = NOW + timedelta(hours=2)
    recovered = store.recover_expired()
    assert set(recovered) == {"read-plan", "write-plan"}
    assert store.get("read-plan").state == "failed"
    unknown = store.get("write-plan")
    assert unknown.state == "cancelling"
    assert unknown.terminal_report_state == "none"
    assert "outcome_reconciliation" in unknown.blocked_reason


def test_preemption_releases_reads_but_never_replays_unknown_side_effects(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan(plan_id="read-plan", side_effecting=False))
    store.create(_plan(plan_id="write-plan", side_effecting=True))
    for plan_id in ("read-plan", "write-plan"):
        store.claim(plan_id, owner_id="owner-a", fencing_token=1, lease_seconds=30)
        store.begin_step(plan_id, owner_id="owner-a", fencing_token=1)

    released = store.release_for_preemption(
        "read-plan", fencing_token=1, reason_code="human_preemption"
    )
    assert released.state == "ready"
    assert released.owner_id is None
    assert released.current_step.state == "ready"

    unknown = store.release_for_preemption(
        "write-plan", fencing_token=1, reason_code="human_preemption"
    )
    assert unknown.state == "cancelling"
    assert unknown.current_step.state == "executing"
    assert unknown.terminal_report_state == "none"
    assert "outcome_reconciliation" in unknown.blocked_reason


def test_startup_reconciliation_fences_an_interrupted_side_effect(tmp_path):
    agenda = AgendaStore(tmp_path)
    entry = create_entry(
        kind="pursuit",
        title="Reconcile interrupted write",
        description="Do not replay an unreceipted write after restart.",
        origin="autonomous",
        evidence_refs=["observation:restart-write"],
        completion_condition="The write outcome is reconciled.",
        time_budget_seconds=60,
        status="active",
    )
    agenda.add(entry)
    checkpoint = ExecutionCheckpoint(
        step_id="restart-write-step",
        kind="invoke_tool",
        state="planned",
        required_capability="calendar.write",
        side_effecting=True,
    )

    async def run_step(_entry, _checkpoint):
        return "not called"

    async def emit(_payload):
        return None

    bridge = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    plan = bridge.ensure_plan(entry, checkpoint, retry_budget=2)
    bridge.store.claim(
        plan.plan_id,
        owner_id="old-process",
        fencing_token=1,
        lease_seconds=30,
    )
    bridge.store.begin_step(
        plan.plan_id,
        owner_id="old-process",
        fencing_token=1,
    )

    restarted = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    assert restarted.reconcile_startup(fencing_token=2) == {
        "restored": 0,
        "acknowledged": 0,
        "released": 1,
        "reconciled": 0,
    }
    recovered = restarted.store.get(plan.plan_id)
    assert recovered.state == "cancelling"
    assert recovered.fencing_token == 2


def test_startup_reconciliation_heals_legacy_cancelling_plan_and_backfills_report(
    tmp_path,
):
    agenda = AgendaStore(tmp_path)
    entry = create_entry(
        kind="pursuit",
        title="Reconcile interrupted write after restart",
        description="A preempted side-effecting plan must terminate without replay.",
        origin="autonomous",
        evidence_refs=["observation:restart-write"],
        completion_condition="The write outcome is reconciled.",
        time_budget_seconds=60,
        status="suspended",
        report_policy="on_completion",
    )
    agenda.add(entry)
    checkpoint = ExecutionCheckpoint(
        step_id="interrupted-write-step",
        kind="invoke_tool",
        state="planned",
        required_capability="calendar.write",
        side_effecting=True,
    )

    async def run_step(_entry, _checkpoint):
        raise AssertionError("legacy cancelling plans must never replay")

    async def emit(_payload):
        return None

    bridge = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    plan = bridge.ensure_plan(entry, checkpoint, retry_budget=2)
    bridge.store.claim(
        plan.plan_id,
        owner_id="old-process",
        fencing_token=1,
        lease_seconds=30,
    )
    bridge.store.begin_step(
        plan.plan_id,
        owner_id="old-process",
        fencing_token=1,
    )
    bridge.store.release_for_preemption(
        plan.plan_id, fencing_token=1, reason_code="human_preemption"
    )
    assert bridge.store.get(plan.plan_id).state == "cancelling"

    restarted = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    healed = restarted.reconcile_startup(fencing_token=2)
    assert healed["reconciled"] == 1

    final_plan = restarted.store.get(plan.plan_id)
    assert final_plan.state == "cancelled"
    assert final_plan.terminal_report_state == "pending"
    assert final_plan.current_step.state == "cancelled"

    reports = agenda.load().reports
    assert len(reports) == 1
    assert "not replayed" in reports[0].summary


def test_reconcile_cancelling_plan_is_idempotent(tmp_path):
    store = ResumablePlanStore(tmp_path, clock=lambda: NOW)
    store.create(_plan(plan_id="write-plan", side_effecting=True))
    store.claim("write-plan", owner_id="owner-a", fencing_token=1, lease_seconds=30)
    store.begin_step("write-plan", owner_id="owner-a", fencing_token=1)
    parked = store.release_for_preemption(
        "write-plan", fencing_token=1, reason_code="human_preemption"
    )
    assert parked.state == "cancelling"

    healed = reconcile_cancelling_plan(store, parked)
    assert healed.state == "cancelled"
    assert healed.current_step.state == "cancelled"
    assert healed.terminal_report_state == "pending"

    again = reconcile_cancelling_plan(store, store.get("write-plan"))
    assert again.state == "cancelled"


@pytest.mark.asyncio
async def test_bridge_execute_reconciles_cancelling_plan_without_replay(tmp_path):
    agenda = AgendaStore(tmp_path)
    entry = create_entry(
        kind="pursuit",
        title="Finish an interrupted write",
        description="Reconcile the parked plan instead of replaying it.",
        origin="autonomous",
        evidence_refs=["observation:restart-write"],
        completion_condition="The write outcome is reconciled.",
        time_budget_seconds=60,
        status="active",
        report_policy="on_completion",
    )
    agenda.add(entry)
    checkpoint = ExecutionCheckpoint(
        step_id="interrupted-write-step",
        kind="invoke_tool",
        state="planned",
        required_capability="calendar.write",
        side_effecting=True,
    )

    async def run_step(_entry, _checkpoint):
        raise AssertionError("cancelling plans must never be dispatched")

    async def emit(_payload):
        return None

    bridge = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    plan = bridge.ensure_plan(entry, checkpoint, retry_budget=2)
    bridge.store.claim(
        plan.plan_id,
        owner_id="owner-a",
        fencing_token=1,
        lease_seconds=30,
    )
    bridge.store.begin_step(
        plan.plan_id,
        owner_id="owner-a",
        fencing_token=1,
    )
    bridge.store.release_for_preemption(
        plan.plan_id, fencing_token=1, reason_code="human_preemption"
    )

    result = await bridge.execute(
        entry,
        checkpoint,
        owner_id="owner-a",
        fencing_token=1,
        capabilities=set(),
        object_names=set(),
        retry_budget=2,
    )
    assert result.plan.state == "cancelled"
    assert result.plan.current_step.state == "cancelled"


def test_offline_abandon_cancels_sidecar_before_terminal_agenda_transition(tmp_path):
    agenda = AgendaStore(tmp_path)
    entry = create_entry(
        kind="pursuit",
        title="Stop offline work",
        description="Cancel the sidecar before abandoning the agenda entry.",
        origin="autonomous",
        evidence_refs=["observation:offline-stop"],
        completion_condition="The work is stopped.",
        time_budget_seconds=60,
        status="active",
    )
    agenda.add(entry)
    checkpoint = ExecutionCheckpoint(
        step_id="offline-read-step",
        kind="read_result",
        state="planned",
        side_effecting=False,
    )

    async def run_step(_entry, _checkpoint):
        return "not called"

    async def emit(_payload):
        return None

    bridge = AgendaPlanBridge(agenda, ActivityExecutor(agenda, run_step, emit))
    plan = bridge.ensure_plan(entry, checkpoint, retry_budget=2)
    abandoned = abandon_entry_safely(agenda, entry.id)
    assert abandoned.status == "abandoned"
    assert agenda.load().entries[0].metadata["resumable_plan_absorbed"] is None
    cancelled = bridge.store.get(plan.plan_id)
    assert cancelled.state == "cancelled"
    assert cancelled.terminal_report_state == "delivered"


def test_plan_validation_rejects_impossible_state_index_combinations():
    completed_without_progress = _plan()
    completed_without_progress.state = "completed"
    completed_without_progress.terminal_report_state = "pending"
    with pytest.raises(PlanValidationError, match="final step"):
        completed_without_progress.validate()

    blocked_without_blocked_step = _plan()
    blocked_without_blocked_step.state = "blocked"
    blocked_without_blocked_step.blocked_reason = "operator_review_required"
    with pytest.raises(PlanValidationError, match="incompatible"):
        blocked_without_blocked_step.validate()
