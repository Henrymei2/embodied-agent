from __future__ import annotations

import asyncio

import pytest

from src.open_llm_vtuber.autonomy.agenda import (
    AgendaStore,
    ExecutionCheckpoint,
    ScheduleSpec,
    create_entry,
    utc_now,
)
from src.open_llm_vtuber.autonomy.activity import ActivityExecutor
from src.open_llm_vtuber.autonomy.executive import AgendaExecutive
from src.open_llm_vtuber.autonomy.observation import (
    clear_client_presence,
    update_client_presence,
)
from src.open_llm_vtuber.autonomy.presence import PresenceLedger
from src.open_llm_vtuber.autonomy.reporting import AgendaReporter
from src.open_llm_vtuber.autonomy.runtime import WorkspaceAgendaRuntime
from src.open_llm_vtuber.autonomy.settings import AutonomySettings
from src.open_llm_vtuber.autonomy.step_policy import StepPolicy
from src.open_llm_vtuber.conversations.llm_request_queue import (
    PRIORITY_PASSIVE,
    ClientRequestQueue,
)


def _pursuit(**overrides):
    values = {
        "kind": "pursuit",
        "title": "Review a grounded note",
        "description": "Read one note and summarize it.",
        "origin": "autonomous",
        "evidence_refs": ["memory:today"],
        "completion_condition": "One note was summarized.",
        "time_budget_seconds": 60,
        "status": "queued",
    }
    values.update(overrides)
    return create_entry(**values)


async def _emit(_payload: dict) -> None:
    return None


def _executive(store, planner, settings=None, run_step=None, **kwargs):
    async def default_run_step(_entry, _checkpoint):
        return "done"

    return AgendaExecutive(
        client_uid="client",
        store=store,
        planner=planner,
        settings=settings or AutonomySettings(restart_recovery_grace_seconds=0),
        run_step=run_step or default_run_step,
        emit=_emit,
        capabilities={"read_file", "execute_bash"},
        object_names={"computer"},
        capability_objects={
            "read_file": "computer",
            "execute_bash": "computer",
        },
        **kwargs,
    )


def test_agenda_revision_advances_on_every_mutation(tmp_path):
    store = AgendaStore(tmp_path)
    entry = _pursuit()

    store.add(entry)
    first = store.load().revision
    store.transition(entry.id, "active")

    assert first == 1
    assert store.load().revision == 2
    assert store.snapshot()["revision"] == 2


@pytest.mark.asyncio
async def test_workspace_runtime_has_one_owner_and_hands_off(tmp_path):
    started: list[str] = []
    stopped: list[str] = []

    async def runner(client_uid, _settings, _presence):
        started.append(client_uid)
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(client_uid)

    runtime = WorkspaceAgendaRuntime(tmp_path, runner=runner)
    settings = AutonomySettings()
    await runtime.attach("first", settings, None)
    await runtime.attach("second", settings, None)
    await asyncio.sleep(0)

    assert runtime.owner_uid == "first"
    assert started == ["first"]

    await runtime.detach("first")
    await asyncio.sleep(0)

    assert stopped == ["first"]
    assert runtime.owner_uid == "second"
    assert started == ["first", "second"]
    await runtime.close()


@pytest.mark.asyncio
async def test_queue_returns_autonomous_result_and_cancels_by_step_id():
    queue = ClientRequestQueue("level-one")
    queue.start()
    started = asyncio.Event()
    canceled = asyncio.Event()

    async def completed_work():
        return {"ok": True}

    assert await queue.enqueue_and_wait(
        coro_factory=completed_work,
        priority=PRIORITY_PASSIVE,
        label="completed agenda step",
        dedupe_key="agenda:entry:completed",
    ) == {"ok": True}

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    task = asyncio.create_task(
        queue.enqueue_and_wait(
            coro_factory=work,
            priority=PRIORITY_PASSIVE,
            label="agenda step",
            dedupe_key="agenda:entry:step",
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(canceled.wait(), timeout=1)
    queue.stop()
    await asyncio.sleep(0)


def test_step_policy_derives_effects_and_requires_real_approval():
    policy = StepPolicy(
        capabilities={"read_file", "execute_bash"},
        object_names={"computer"},
        capability_objects={
            "read_file": "computer",
            "execute_bash": "computer",
        },
    )
    entry = _pursuit()
    read = ExecutionCheckpoint(
        step_id="read",
        kind="invoke_tool",
        state="planned",
        required_capability="read_file",
        side_effecting=True,
        interruptible=False,
    )

    assert policy.validate(entry, read).accepted
    assert read.required_object == "computer"
    assert read.side_effecting is False
    assert read.interruptible is True

    shell = ExecutionCheckpoint(
        step_id="shell",
        kind="invoke_tool",
        state="planned",
        required_capability="execute_bash",
    )
    assert not policy.validate(entry, shell).accepted

    approved = _pursuit(
        requires_approval=True,
        plan_id="plan-1",
        metadata={"approval_status": "approved"},
    )
    assert policy.validate(approved, shell).accepted
    assert shell.side_effecting is True
    assert shell.interruptible is False


@pytest.mark.asyncio
async def test_idle_entry_waits_without_blocking_the_executive(tmp_path):
    store = AgendaStore(tmp_path)
    idle = create_entry(
        kind="idle",
        title="Wait briefly",
        description="Remain available.",
        origin="recovery",
        evidence_refs=["system:test"],
        completion_condition="The wait ends.",
        time_budget_seconds=60,
        status="active",
    )
    store.add(idle)
    executive = _executive(store, planner=object())

    assert await asyncio.wait_for(executive._advance(), timeout=0.2) is True
    await asyncio.sleep(0)
    assert await asyncio.wait_for(executive._advance(), timeout=0.2) is False
    assert store.load().entries[0].status == "active"
    assert executive._next_wake_seconds() <= executive.settings.store_poll_seconds


@pytest.mark.asyncio
async def test_due_schedule_preempts_an_autonomous_step(tmp_path):
    class Planner:
        async def next_step(self, _entry, _context):
            return ExecutionCheckpoint(
                step_id="long-step",
                kind="reflect",
                state="planned",
                timeout_seconds=60,
            )

    started = asyncio.Event()
    canceled = asyncio.Event()

    async def run_step(_entry, _checkpoint):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    store = AgendaStore(tmp_path)
    pursuit = _pursuit(status="active")
    store.add(pursuit)
    executive = _executive(store, Planner(), run_step=run_step)
    assert await executive._advance() is True
    await asyncio.wait_for(started.wait(), timeout=1)

    scheduled = _pursuit(
        kind="scheduled",
        title="Due commitment",
        origin="scheduled",
        schedule=ScheduleSpec(next_run=utc_now().isoformat()),
    )
    store.add(scheduled)

    assert await executive._advance() is True
    await asyncio.wait_for(canceled.wait(), timeout=1)
    state = store.load()
    active = next(entry for entry in state.entries if entry.status == "active")
    original = next(entry for entry in state.entries if entry.id == pursuit.id)

    assert active.kind == "scheduled"
    assert active.parent_id == scheduled.id
    assert original.status == "suspended"


@pytest.mark.asyncio
async def test_cancel_after_tool_result_preserves_completed_safe_boundary(tmp_path):
    store = AgendaStore(tmp_path)
    entry = _pursuit(status="active")
    store.add(entry)
    checkpoint = ExecutionCheckpoint(
        step_id="paced-step",
        kind="invoke_tool",
        state="planned",
        required_capability="read_file",
        plausible_duration_seconds=60,
        side_effecting=False,
    )

    async def immediate_result(_entry, _checkpoint):
        return "result already consumed"

    executor = ActivityExecutor(store, immediate_result, _emit)
    task = asyncio.create_task(executor.execute(entry, checkpoint))
    for _ in range(20):
        await asyncio.sleep(0)
        current = store.load().entries[0]
        if current.checkpoint and current.checkpoint.state == "result_ready":
            break
    else:
        raise AssertionError("activity never reached its result-ready boundary")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    current = store.load().entries[0]
    assert current.status == "suspended"
    assert current.checkpoint.state == "completed"
    assert "result already consumed" in current.progress_summary


@pytest.mark.asyncio
async def test_planner_failures_are_capped_before_reconsideration(tmp_path):
    class BrokenPlanner:
        calls = 0

        async def propose_intention(self, _context):
            self.calls += 1
            raise ValueError("not json")

    store = AgendaStore(tmp_path)
    planner = BrokenPlanner()
    settings = AutonomySettings(
        planner_retry_limit=2,
        planner_retry_backoff_seconds=0,
        idle_min_seconds=5,
        restart_recovery_grace_seconds=0,
    )
    executive = _executive(store, planner, settings=settings)

    await executive._generate_successor()

    state = store.load()
    assert planner.calls == 2
    assert len(state.rejections) == 2
    assert state.entries[0].kind == "idle"
    assert state.entries[0].status == "queued"


@pytest.mark.asyncio
async def test_focus_mode_blocks_spoken_report_and_keeps_receipt(tmp_path):
    client_uid = "focus-client"
    update_client_presence(
        client_uid,
        {"idle_seconds": 0, "user_present": True, "focus_mode": True},
    )
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    emitted: list[dict] = []
    spoken: list[str] = []

    async def emit(payload):
        emitted.append(payload)

    async def run_report(_entry, summary):
        spoken.append(summary)

    reporter = AgendaReporter(client_uid, ledger, emit, run_report)
    entry = _pursuit(report_policy="on_completion")
    try:
        assert (
            await reporter.report(entry, "A sufficiently useful completed result.")
            is False
        )
    finally:
        clear_client_presence(client_uid)

    assert spoken == []
    assert ledger.receipts[-1].status == "canceled"
    assert ledger.receipts[-1].error == "focus mode is active"
    assert emitted[-1]["type"] == "autonomy-receipt"
