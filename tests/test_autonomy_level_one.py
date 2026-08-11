from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from src.open_llm_vtuber.autonomy.agenda import (
    AgendaStore,
    ExecutionCheckpoint,
    ScheduleSpec,
    create_entry,
    utc_now,
)
from src.open_llm_vtuber.autonomy.activity import ActivityExecutor
from src.open_llm_vtuber.autonomy.driver import _agenda_step_instruction
from src.open_llm_vtuber.autonomy.executive import (
    AgendaExecutive,
    PlannerCallTimeout,
)
from src.open_llm_vtuber.autonomy.idle_relocation import IdleObject, IdleScene
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


class _TraceSpan:
    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


class _TraceContext:
    def __init__(self, span):
        self.span = span

    def __enter__(self):
        return self.span

    def __exit__(self, *_args):
        return False


class _Tracer:
    def __init__(self):
        self.spans = []

    def span(self, *_args, **_kwargs):
        span = _TraceSpan()
        self.spans.append(span)
        return _TraceContext(span)


def _executive(store, planner, settings=None, run_step=None, **kwargs):
    async def default_run_step(_entry, _checkpoint):
        return "done"

    emit = kwargs.pop("emit", _emit)

    return AgendaExecutive(
        client_uid="client",
        store=store,
        planner=planner,
        settings=settings or AutonomySettings(restart_recovery_grace_seconds=0),
        run_step=run_step or default_run_step,
        emit=emit,
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


def test_conditional_transition_cannot_revive_terminal_entry(tmp_path):
    store = AgendaStore(tmp_path)
    entry = _pursuit(status="suspended")
    store.add(entry)
    store.transition(entry.id, "abandoned", progress_summary="Stopped by user.")
    revision = store.load().revision

    applied = store.transition_if_status(
        entry.id,
        "suspended",
        "queued",
        metadata_updates={"reconsider_failures": 0},
    )

    current = store.load().entries[0]
    assert applied is None
    assert current.status == "abandoned"
    assert "reconsider_failures" not in current.metadata
    assert store.load().revision == revision


def test_conditional_terminal_transition_preserves_completion_report(tmp_path):
    store = AgendaStore(tmp_path)
    entry = _pursuit(status="suspended", report_policy="on_completion")
    entry.metadata["trigger_foreground"] = True
    store.add(entry)

    applied = store.transition_if_status(
        entry.id,
        "suspended",
        "failed",
        progress_summary="Planner reconsider timed out.",
    )

    state = store.load()
    assert applied is not None
    assert state.entries[0].status == "failed"
    assert len(state.reports) == 1
    assert state.reports[0].summary == "Planner reconsider timed out."


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
        capabilities={"read_file", "execute_bash", "delete_path"},
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

    # Sandboxed commands are routine and run directly without an approved plan.
    shell = ExecutionCheckpoint(
        step_id="shell",
        kind="invoke_tool",
        state="planned",
        required_capability="execute_bash",
    )
    assert policy.validate(entry, shell).accepted
    assert shell.side_effecting is True
    assert shell.interruptible is False

    destructive = ExecutionCheckpoint(
        step_id="destructive",
        kind="invoke_tool",
        state="planned",
        required_capability="delete_path",
    )
    assert not policy.validate(entry, destructive).accepted

    approved = _pursuit(
        requires_approval=True,
        plan_id="plan-1",
        metadata={"approval_status": "approved"},
    )
    assert policy.validate(approved, destructive).accepted
    assert destructive.side_effecting is True
    assert destructive.interruptible is False


def test_approval_gate_covers_only_irreversible_and_communicative_capabilities():
    from src.open_llm_vtuber.autonomy.step_policy import capability_requires_approval

    for gated in (
        "delete_path",
        "send_email",
        "remove_calendar_event",
        "remove_todo",
        "cancel_timer",
        "remove_task",
        "withdraw_plan",
        "retire_skill",
        "remove_from_shelf",
        "save_reading_note",
        "finish_book",
        "remove_object",
    ):
        assert capability_requires_approval(gated), gated

    for direct in (
        "archive_email",
        "mark_email_read",
        "mark_email_unread",
        "execute_bash",
        "edit_file",
        "write_file",
        "append_to_file",
        "create_directory",
        "move_path",
        "download_file",
        "add_calendar_event",
        "update_calendar_event",
        "add_todo",
        "complete_todo",
        "set_timer",
        "schedule_task",
        "draft_skill",
        "draw_image",
        "move_object",
        "place_object",
    ):
        assert not capability_requires_approval(direct), direct


def test_browser_tabs_is_an_audited_read_only_capability():
    policy = StepPolicy(
        capabilities={"browser_tabs"},
        object_names={"computer"},
        capability_objects={"browser_tabs": "computer"},
    )
    checkpoint = ExecutionCheckpoint(
        step_id="tabs",
        kind="invoke_tool",
        state="planned",
        required_capability="browser_tabs",
    )

    assert policy.validate(_pursuit(), checkpoint).accepted
    assert checkpoint.required_object == "computer"
    assert checkpoint.side_effecting is False
    assert checkpoint.interruptible is True


def test_step_policy_rejects_arguments_that_mismatch_tool_schema():
    policy = StepPolicy(
        capabilities={"get_email", "search_emails", "move_to"},
        object_names={"mailbox"},
        capability_objects={
            "get_email": "mailbox",
            "search_emails": "mailbox",
        },
        tool_schemas={
            "get_email": {
                "required": ["message_id"],
                "properties": {"message_id": "string"},
                "additionalProperties": False,
            },
            "search_emails": {
                "required": [],
                "properties": {"query": "string", "limit": "integer"},
                "additionalProperties": False,
            },
        },
    )
    entry = _pursuit()

    bad = ExecutionCheckpoint(
        step_id="bad-args",
        kind="invoke_tool",
        state="planned",
        required_capability="get_email",
        arguments={
            "email_id": "94bff83f9ff5270b5bff233944be828d98b2b61442d2c4aa652c74428ce08997"
        },
    )
    verdict = policy.validate(entry, bad)
    assert not verdict.accepted
    assert "message_id" in verdict.reason

    good = ExecutionCheckpoint(
        step_id="good-args",
        kind="invoke_tool",
        state="planned",
        required_capability="get_email",
        arguments={
            "message_id": "94bff83f9ff5270b5bff233944be828d98b2b61442d2c4aa652c74428ce08997"
        },
    )
    assert policy.validate(entry, good).accepted

    bad_search = ExecutionCheckpoint(
        step_id="bad-search",
        kind="invoke_tool",
        state="planned",
        required_capability="search_emails",
        arguments={"max_results": 5},
    )
    assert not policy.validate(entry, bad_search).accepted

    good_search = ExecutionCheckpoint(
        step_id="good-search",
        kind="invoke_tool",
        state="planned",
        required_capability="search_emails",
        arguments={"query": "is:unread", "limit": 5},
    )
    assert policy.validate(entry, good_search).accepted

    # Non-tool steps keep their advisory free-form arguments.
    move = ExecutionCheckpoint(
        step_id="move",
        kind="move_to_object",
        state="planned",
        required_capability="move_to",
        required_object="mailbox",
        arguments={"object": "mailbox"},
    )
    assert policy.validate(entry, move).accepted


def test_agenda_execution_instruction_includes_tool_and_furniture():
    checkpoint = ExecutionCheckpoint(
        step_id="tabs",
        kind="invoke_tool",
        state="planned",
        purpose="Inspect open tabs.",
        required_object="computer",
        required_capability="browser_tabs",
        arguments={"window": "current"},
    )

    instruction = _agenda_step_instruction(_pursuit(), checkpoint)

    assert '"kind": "invoke_tool"' in instruction
    assert '"required_object": "computer"' in instruction
    assert '"required_capability": "browser_tabs"' in instruction
    assert '"window": "current"' in instruction


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
async def test_user_preemption_terminates_side_effecting_step_and_reports(tmp_path):
    started = asyncio.Event()
    canceled = asyncio.Event()

    class Planner:
        async def next_step(self, _entry, _context):
            return ExecutionCheckpoint(
                step_id="write-step",
                kind="invoke_tool",
                state="planned",
                required_capability="execute_bash",
                required_object="computer",
                side_effecting=True,
                timeout_seconds=60,
            )

        async def evaluate_completion(self, _entry, _evidence):
            raise AssertionError("cancelled plans must not be evaluated")

        async def reconsider(self, _entry, _context):
            raise AssertionError("terminal plans must absorb without reconsideration")

    async def run_step(_entry, _checkpoint):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    store = AgendaStore(tmp_path)
    pursuit = _pursuit(status="active", report_policy="on_completion")
    store.add(pursuit)
    executive = _executive(
        store,
        Planner(),
        settings=AutonomySettings(
            human_preemption_timeout_seconds=1.0,
            restart_recovery_grace_seconds=0,
        ),
        run_step=run_step,
    )
    assert await executive._advance() is True
    await asyncio.wait_for(started.wait(), timeout=1)

    user = await executive.preempt_for_user("User request")
    await asyncio.wait_for(canceled.wait(), timeout=1)

    state = store.load()
    original = next(item for item in state.entries if item.id == pursuit.id)
    assert original.status == "suspended"
    plan_id = original.metadata["resumable_plan_id"]
    plan = executive.plan_bridge.store.get(plan_id)
    assert plan.state == "cancelled"
    assert plan.current_step.state == "cancelled"

    await executive.finish_user(user.id)

    state = store.load()
    original = next(item for item in state.entries if item.id == pursuit.id)
    assert original.status == "failed"
    assert "not replayed" in original.progress_summary
    assert any("not replayed" in report.summary for report in state.reports)


@pytest.mark.asyncio
async def test_entry_execution_emits_plan_then_idle_activity(tmp_path):
    class Planner:
        async def next_step(self, _entry, _context):
            return ExecutionCheckpoint(
                step_id="quick-step",
                kind="reflect",
                state="planned",
                timeout_seconds=60,
            )

        async def evaluate_completion(self, _entry, _evidence):
            return True, "Quick step finished."

    async def run_step(_entry, _checkpoint):
        return "done"

    emitted: list[dict] = []

    async def emit(payload: dict) -> None:
        emitted.append(payload)

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="queued", title="Archive old emails")
    store.add(entry)
    store.transition(entry.id, "active")
    executive = _executive(store, Planner(), run_step=run_step, emit=emit)
    await executive._advance_entry(store.load().entries[0])

    activity = [item for item in emitted if item.get("type") == "agent_activity"]
    assert activity[0]["phase"] == "plan"
    assert activity[0]["label"] == "Archive old emails"
    assert any(item.get("phase") == "idle" for item in activity[1:])


@pytest.mark.asyncio
async def test_due_schedule_preempts_an_intention_still_being_planned(tmp_path):
    planning_started = asyncio.Event()

    class Planner:
        async def next_step(self, _entry, _context):
            planning_started.set()
            await asyncio.Event().wait()

    store = AgendaStore(tmp_path)
    pursuit = _pursuit(status="active")
    store.add(pursuit)
    executive = _executive(store, Planner())
    assert await executive._advance() is True
    await asyncio.wait_for(planning_started.wait(), timeout=1)

    scheduled = _pursuit(
        kind="scheduled",
        title="Due while planning",
        origin="scheduled",
        schedule=ScheduleSpec(next_run=utc_now().isoformat()),
    )
    store.add(scheduled)

    assert await executive._advance() is True
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

    executor = ActivityExecutor(
        store,
        immediate_result,
        _emit,
        max_plausible_duration_seconds=60,
    )
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
@pytest.mark.parametrize(
    "phase",
    ["propose", "next_step", "evaluate_completion", "reconsider", "idle_placement"],
)
async def test_every_planner_phase_has_a_content_free_hard_timeout(tmp_path, phase):
    store = AgendaStore(tmp_path)
    executive = _executive(store, object())
    object.__setattr__(executive.settings, "planner_timeout_seconds", 0.01)
    span = _TraceSpan()

    async def hangs():
        await asyncio.Event().wait()

    with pytest.raises(PlannerCallTimeout) as caught:
        await executive._run_planner_call(hangs(), phase=phase, span=span)

    assert caught.value.phase == phase
    assert span.attributes["agenda.planner_phase"] == phase
    assert span.attributes["agenda.planner_outcome"] == "timed_out"
    assert span.attributes["agenda.planner_timeout_seconds"] == 0.01
    assert "prompt" not in repr(span.attributes).lower()


@pytest.mark.asyncio
async def test_proposal_timeout_ends_pass_without_repeating_model_call(tmp_path):
    class Planner:
        calls = 0

        async def propose_intention(self, _context):
            self.calls += 1
            await asyncio.Event().wait()

    planner = Planner()
    store = AgendaStore(tmp_path)
    executive = _executive(store, planner)
    object.__setattr__(executive.settings, "planner_timeout_seconds", 0.01)

    await executive._generate_successor()

    assert planner.calls == 1
    assert store.load().entries[-1].kind == "idle"


@pytest.mark.asyncio
async def test_idle_placement_timeout_safely_selects_no_movement(tmp_path):
    class Planner:
        async def choose_idle_placement(self, _context, _scene):
            await asyncio.Event().wait()

    store = AgendaStore(tmp_path)
    executive = _executive(store, Planner())
    object.__setattr__(executive.settings, "planner_timeout_seconds", 0.01)
    scene = IdleScene(
        current_position=(0.0, 0.0),
        objects=(IdleObject("sofa", "Sofa", 1.0, 1.0, 1.4, "sitting"),),
    )

    placement = await executive._choose_idle_placement(
        executive._agenda_context(), scene
    )

    assert placement.object_id is None


@pytest.mark.asyncio
async def test_next_step_timeout_suspends_without_execution_failure(tmp_path):
    class Planner:
        started = asyncio.Event()

        async def next_step(self, _entry, _context):
            self.started.set()
            await asyncio.Event().wait()

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="active")
    store.add(entry)
    planner = Planner()
    executive = _executive(store, planner)
    object.__setattr__(executive.settings, "planner_timeout_seconds", 0.01)

    assert await executive._advance() is True
    await planner.started.wait()
    await asyncio.sleep(0.02)
    assert await executive._advance() is True

    current = store.load().entries[0]
    assert current.status == "suspended"
    assert current.metadata["planner_timeout_failures"] == 1
    assert "execution_failures" not in current.metadata


@pytest.mark.asyncio
async def test_short_generated_pursuit_gets_a_complete_execution_window(tmp_path):
    (tmp_path / "MEMORY.md").write_text("A grounded item to check.", encoding="utf-8")

    class Planner:
        async def propose_intention(self, _context):
            return _pursuit(
                title="Short but grounded check",
                evidence_refs=["memory:MEMORY.md"],
                time_budget_seconds=30,
                status="proposed",
            )

    store = AgendaStore(tmp_path)
    settings = AutonomySettings(
        step_timeout_seconds=120,
        restart_recovery_grace_seconds=0,
    )
    executive = _executive(store, Planner(), settings=settings)

    await executive._generate_successor()

    entry = store.load().entries[0]
    assert entry.status == "queued"
    assert entry.budget.time_budget_seconds == 240


@pytest.mark.asyncio
async def test_short_execution_window_reduces_timeout_and_still_dispatches(tmp_path):
    class Planner:
        async def next_step(self, _entry, _context):
            return ExecutionCheckpoint(
                step_id="too-late",
                kind="reflect",
                state="planned",
                timeout_seconds=120,
            )

        async def evaluate_completion(self, _entry, _evidence):
            return True, "completed inside the remaining window"

    dispatched = False
    applied_timeout = None

    async def run_step(_entry, checkpoint):
        nonlocal applied_timeout, dispatched
        dispatched = True
        applied_timeout = checkpoint.timeout_seconds
        return "should not run"

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="queued", time_budget_seconds=60)
    store.add(entry)
    store.transition(entry.id, "active")
    executive = _executive(store, Planner(), run_step=run_step)

    assert await executive._advance_entry(store.load().entries[0]) is True

    current = store.load().entries[0]
    assert current.status == "completed"
    assert dispatched is True
    assert applied_timeout == 30


@pytest.mark.asyncio
async def test_expired_uncertain_step_is_not_reconsidered_or_replayed(tmp_path):
    class Planner:
        reconsider_calls = 0

        async def reconsider(self, _entry, _context):
            self.reconsider_calls += 1
            return "resume"

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="suspended", report_policy="on_completion")
    entry.budget.deadline_at = (utc_now() - timedelta(seconds=1)).isoformat()
    entry.checkpoint = ExecutionCheckpoint(
        step_id="uncertain-step",
        kind="invoke_tool",
        state="uncertain",
        required_capability="read_file",
        side_effecting=True,
    )
    store.add(entry)
    planner = Planner()
    executive = _executive(store, planner)

    await executive._reconsider_suspended()

    current = store.load().entries[0]
    assert current.status == "failed"
    assert "not replayed" in current.progress_summary
    assert planner.reconsider_calls == 0
    assert store.load().dead_letters[-1].category == "activity_reconciliation"
    reports = store.load().reports
    assert len(reports) == 1
    assert "not replayed" in reports[0].summary


@pytest.mark.asyncio
async def test_safe_planned_step_resumes_without_reconsideration_llm(tmp_path):
    class Planner:
        reconsider_calls = 0

        async def reconsider(self, _entry, _context):
            self.reconsider_calls += 1
            raise AssertionError("safe planned work should not call the planner")

        async def next_step(self, _entry, _context):
            raise AssertionError("the durable planned checkpoint should be reused")

        async def evaluate_completion(self, _entry, _evidence):
            return True, "Recovered planned read completed."

    dispatched: list[str] = []

    async def run_step(_entry, checkpoint):
        dispatched.append(checkpoint.step_id)
        return "read completed"

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="suspended", time_budget_seconds=300)
    entry.checkpoint = ExecutionCheckpoint(
        step_id="planned-read",
        kind="invoke_tool",
        state="planned",
        required_object="computer",
        required_capability="read_file",
        side_effecting=False,
    )
    store.add(entry)
    planner = Planner()
    executive = _executive(store, planner, run_step=run_step)

    await executive._reconsider_suspended()

    assert store.load().entries[0].status == "queued"
    assert planner.reconsider_calls == 0

    store.transition(entry.id, "active")
    await executive._advance_entry(store.load().entries[0])

    assert dispatched == ["planned-read"]
    assert store.load().entries[0].status == "completed"


@pytest.mark.asyncio
async def test_reconsideration_failure_is_deferred_instead_of_hot_looping(tmp_path):
    class Planner:
        reconsider_calls = 0

        async def reconsider(self, _entry, _context):
            self.reconsider_calls += 1
            raise ValueError("empty planner response")

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="suspended")
    entry.checkpoint = ExecutionCheckpoint(
        step_id="uncertain-read",
        kind="invoke_tool",
        state="uncertain",
        required_capability="read_file",
        side_effecting=False,
    )
    store.add(entry)
    planner = Planner()
    executive = _executive(
        store,
        planner,
        settings=AutonomySettings(
            planner_retry_limit=2,
            planner_retry_backoff_seconds=0,
            restart_recovery_grace_seconds=0,
        ),
    )

    await executive._reconsider_suspended()
    first = store.load().entries[0]
    assert first.status == "suspended"
    assert first.metadata["reconsider_failures"] == 1
    assert first.metadata["reconsider_deferred_until"]

    await executive._reconsider_suspended()
    assert planner.reconsider_calls == 1
    assert store.load().entries[0].status == "suspended"


@pytest.mark.asyncio
async def test_reconsider_timeout_uses_bounded_failure_backoff(tmp_path):
    class Planner:
        async def reconsider(self, _entry, _context):
            await asyncio.Event().wait()

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="suspended")
    store.add(entry)
    executive = _executive(store, Planner())
    object.__setattr__(executive.settings, "planner_timeout_seconds", 0.01)

    await executive._reconsider_suspended()

    current = store.load().entries[0]
    assert current.status == "suspended"
    assert current.metadata["reconsider_failures"] == 1
    assert current.metadata["reconsider_deferred_until"]
    assert "PlannerCallTimeout" in current.metadata["reconsider_deferred_reason"]


@pytest.mark.asyncio
async def test_abandon_cancels_inflight_suspended_reconsideration_promptly(tmp_path):
    started = asyncio.Event()
    cancellation_seen = asyncio.Event()
    release_provider = asyncio.Event()

    class Planner:
        async def reconsider(self, _entry, _context):
            started.set()
            try:
                await release_provider.wait()
            except asyncio.CancelledError:
                cancellation_seen.set()
                await release_provider.wait()
            return "resume"

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="suspended")
    store.add(entry)
    executive = _executive(store, Planner())
    object.__setattr__(executive.settings, "human_preemption_timeout_seconds", 0.01)

    assert await executive._advance() is True
    await started.wait()
    loop = asyncio.get_running_loop()
    abandon_started = loop.time()
    await executive.abandon(entry.id)

    assert loop.time() - abandon_started < 0.1
    assert cancellation_seen.is_set()
    assert store.load().entries[0].status == "abandoned"
    release_provider.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert store.load().entries[0].status == "abandoned"


@pytest.mark.asyncio
async def test_new_runnable_work_preempts_background_reconsideration(tmp_path):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Planner:
        async def reconsider(self, _entry, _context):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    store = AgendaStore(tmp_path)
    suspended = _pursuit(status="suspended")
    store.add(suspended)
    executive = _executive(store, Planner())

    assert await executive._advance() is True
    await started.wait()
    runnable = _pursuit(status="queued", title="New foreground work")
    runnable.metadata["trigger_foreground"] = True
    store.add(runnable)

    assert await executive._advance() is True

    assert cancelled.is_set()
    current = next(item for item in store.load().entries if item.id == runnable.id)
    assert current.status == "active"


@pytest.mark.asyncio
async def test_late_reconsider_result_cannot_revive_terminal_entry(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    class Planner:
        async def reconsider(self, _entry, _context):
            started.set()
            await release.wait()
            return "resume"

    store = AgendaStore(tmp_path)
    entry = _pursuit(status="suspended")
    store.add(entry)
    executive = _executive(store, Planner())
    tracer = _Tracer()
    executive.tracer = tracer

    reconsider = asyncio.create_task(executive._reconsider_suspended())
    await started.wait()
    store.transition(entry.id, "abandoned", progress_summary="Stopped by user.")
    terminal_metadata = dict(store.load().entries[0].metadata)
    release.set()
    await reconsider

    current = store.load().entries[0]
    assert current.status == "abandoned"
    assert current.metadata == terminal_metadata
    assert tracer.spans[-1].attributes["agenda.planner_outcome"] == ("stale_discarded")


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
