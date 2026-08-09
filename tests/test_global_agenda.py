from datetime import timedelta

import pytest

from src.open_llm_vtuber.autonomy.agenda import (
    AgendaStore,
    ExecutionCheckpoint,
    ScheduleSpec,
    create_entry,
    utc_now,
)
from src.open_llm_vtuber.autonomy.agenda_policy import AgendaPolicy
from src.open_llm_vtuber.autonomy.agenda_context import AgendaContext
from src.open_llm_vtuber.autonomy.agenda_planner import (
    PlannerResponseError,
    StructuredAgendaPlanner,
    normalize_generated_kind,
)
from src.open_llm_vtuber.autonomy.activity import (
    ActivityExecutor,
    ActivityRunResult,
    ActivityStepTimeout,
)


def pursuit(**overrides):
    values = {
        "kind": "pursuit",
        "title": "Read a grounded article",
        "description": "Read one relevant source and save a note.",
        "origin": "autonomous",
        "evidence_refs": ["memory:today.md"],
        "completion_condition": "One source was reviewed and a note saved.",
        "time_budget_seconds": 300,
        "status": "queued",
    }
    values.update(overrides)
    return create_entry(**values)


@pytest.mark.asyncio
async def test_structured_planner_normalizes_object_completion_condition():
    async def complete(_prompt):
        return """{
            "kind": "pursuit",
            "title": "Inspect the room",
            "description": "Inspect one grounded room object.",
            "evidence_refs": ["observation:room"],
            "completion_condition": {"description": "One object is inspected."},
            "time_budget_seconds": 60
        }"""

    context = AgendaContext(
        payload={},
        evidence_refs={"observation:room"},
        capabilities=set(),
        object_names=set(),
    )
    entry = await StructuredAgendaPlanner(complete).propose_intention(context)

    assert entry.completion_condition.description == "One object is inspected."


@pytest.mark.asyncio
async def test_structured_planner_allows_capability_health_checks():
    async def complete(_prompt):
        return """{
            "kind": "pursuit",
            "title": "Check the timer capability",
            "description": "Run a bounded read-only timer health check.",
            "evidence_refs": ["observation:idle"],
            "completion_condition": "The timer API responds without error.",
            "time_budget_seconds": 60,
            "required_capabilities": ["list_timers"]
        }"""

    context = AgendaContext(
        payload={},
        evidence_refs={"observation:idle"},
        capabilities={"list_timers"},
        object_names={"hourglass"},
    )

    entry = await StructuredAgendaPlanner(complete).propose_intention(context)

    assert entry.title == "Check the timer capability"
    assert entry.required_capabilities == ["list_timers"]


@pytest.mark.asyncio
async def test_structured_planner_classifies_empty_reconsideration_response():
    async def complete(_prompt):
        return ""

    context = AgendaContext(
        payload={}, evidence_refs=set(), capabilities=set(), object_names=set()
    )
    with pytest.raises(PlannerResponseError, match="empty response"):
        await StructuredAgendaPlanner(complete).reconsider({}, context)


def test_store_enforces_one_foreground_and_suspends_previous(tmp_path):
    store = AgendaStore(tmp_path)
    first = pursuit(status="active")
    second = pursuit(title="Second pursuit", status="active")
    store.add(first)
    store.add(second)

    state = store.load()
    assert [entry.status for entry in state.entries] == ["suspended", "active"]


def test_restart_recovery_marks_side_effect_uncertain(tmp_path):
    store = AgendaStore(tmp_path)
    entry = pursuit(status="active")
    entry.checkpoint = ExecutionCheckpoint(
        step_id="step-1",
        kind="invoke_tool",
        state="running",
        side_effecting=True,
    )
    store.add(entry)

    assert store.recover_interrupted() == [entry.id]
    recovered = store.load().entries[0]
    assert recovered.status == "suspended"
    assert recovered.checkpoint.state == "uncertain"


def test_due_schedule_is_claimed_once_and_recurrence_advances(tmp_path):
    store = AgendaStore(tmp_path)
    due = utc_now() - timedelta(seconds=1)
    entry = pursuit(
        kind="scheduled",
        title="Recurring review",
        origin="scheduled",
        schedule=ScheduleSpec(
            next_run=due.isoformat(),
            interval_seconds=60,
            prompt="Review",
        ),
    )
    store.add(entry)

    claimed = store.claim_due()
    assert len(claimed) == 1
    assert claimed[0].parent_id == entry.id
    assert store.claim_due() == []
    template = next(item for item in store.load().entries if item.id == entry.id)
    assert template.schedule.run_count == 1


def test_policy_rejects_unseen_grounding_and_duplicates():
    policy = AgendaPolicy()
    proposal = pursuit()
    verdict = policy.validate(
        proposal,
        supplied_evidence_refs=set(),
        capabilities=set(),
        object_names=set(),
        comparison_entries=[],
    )
    assert not verdict.accepted
    assert "outside planner context" in verdict.reason

    verdict = policy.validate(
        proposal,
        supplied_evidence_refs={"memory:today.md"},
        capabilities=set(),
        object_names=set(),
        comparison_entries=[pursuit()],
    )
    assert not verdict.accepted
    assert "duplicate" in verdict.reason


def test_terminal_entries_cannot_be_reactivated(tmp_path):
    store = AgendaStore(tmp_path)
    entry = pursuit()
    store.add(entry)
    store.transition(entry.id, "completed")
    with pytest.raises(ValueError, match="terminal"):
        store.transition(entry.id, "active")


def test_pruning_deletes_terminal_entries_without_writing_memory(tmp_path):
    store = AgendaStore(tmp_path)
    entry = pursuit()
    store.add(entry)
    store.transition(entry.id, "completed", progress_summary="Saved a useful note.")
    assert store.prune_terminal_history(retention_entries=0) == 1
    assert store.load().entries == []


def test_pruning_keeps_recent_terminal_entries(tmp_path):
    store = AgendaStore(tmp_path)
    entry = pursuit()
    store.add(entry)
    store.transition(entry.id, "completed")

    assert store.prune_terminal_history(retention_entries=1) == 0
    assert store.load().entries[0].id == entry.id


@pytest.mark.parametrize(
    ("generated", "expected"),
    [
        ("pursuit", "pursuit"),
        ("intention", "pursuit"),
        ("task", "pursuit"),
        ("plan", "pursuit"),
        ("system optimization", "pursuit"),
        ("object_arrangement", "pursuit"),
        ("book_reading_session", "pursuit"),
        ("idle", "idle"),
    ],
)
def test_generated_kind_aliases_are_normalized(generated, expected):
    assert normalize_generated_kind(generated) == expected


@pytest.mark.asyncio
async def test_activity_timeout_is_descriptive_and_retryable(tmp_path):
    store = AgendaStore(tmp_path)
    entry = pursuit(status="active")
    store.add(entry)
    checkpoint = ExecutionCheckpoint(
        step_id="slow-step",
        kind="reflect",
        state="planned",
        timeout_seconds=0.01,
    )

    async def never_finishes(_entry, _checkpoint):
        import asyncio

        await asyncio.sleep(10)
        return "unreachable"

    executor = ActivityExecutor(store, never_finishes, lambda _payload: _noop())
    with pytest.raises(ActivityStepTimeout, match="timed out after"):
        await executor.execute(entry, checkpoint)

    updated = store.load().entries[0]
    assert updated.status == "queued"
    assert updated.checkpoint.state == "planned"
    assert "timed out" in updated.progress_summary


@pytest.mark.asyncio
async def test_default_pacing_limit_disables_model_requested_sleep(
    tmp_path, monkeypatch
):
    store = AgendaStore(tmp_path)
    entry = pursuit(status="active")
    store.add(entry)
    checkpoint = ExecutionCheckpoint(
        step_id="unpaced-step",
        kind="reflect",
        state="planned",
        plausible_duration_seconds=60,
    )
    sleeps: list[float] = []

    async def immediate(_entry, _checkpoint):
        return ActivityRunResult("done", 4.0, 0.25)

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.activity.asyncio.sleep", record_sleep
    )
    executor = ActivityExecutor(store, immediate, lambda _payload: _noop())

    assert await executor.execute(entry, checkpoint) == "done"
    assert sleeps == []


@pytest.mark.asyncio
async def test_configured_pacing_uses_execution_time_not_queue_wait(
    tmp_path, monkeypatch
):
    store = AgendaStore(tmp_path)
    entry = pursuit(status="active")
    store.add(entry)
    checkpoint = ExecutionCheckpoint(
        step_id="bounded-paced-step",
        kind="reflect",
        state="planned",
        plausible_duration_seconds=60,
    )
    sleeps: list[float] = []

    async def immediate(_entry, _checkpoint):
        return ActivityRunResult("done", 10.0, 0.5)

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.activity.asyncio.sleep", record_sleep
    )
    executor = ActivityExecutor(
        store,
        immediate,
        lambda _payload: _noop(),
        max_plausible_duration_seconds=2,
    )

    assert await executor.execute(entry, checkpoint) == "done"
    assert sleeps == [pytest.approx(1.5)]


@pytest.mark.asyncio
async def test_activity_trace_records_content_free_latency_breakdown(
    tmp_path, monkeypatch
):
    class Span:
        def __init__(self):
            self.attributes = {}

        def set_attribute(self, key, value):
            self.attributes[key] = value

    class TraceContext:
        def __init__(self, span):
            self.span = span

        def __enter__(self):
            return self.span

        def __exit__(self, *_args):
            return False

    class Tracer:
        def __init__(self):
            self.last_span = Span()

        def span(self, *_args, **_kwargs):
            return TraceContext(self.last_span)

    store = AgendaStore(tmp_path)
    entry = pursuit(status="active")
    store.add(entry)
    checkpoint = ExecutionCheckpoint(
        step_id="traced-step",
        kind="invoke_tool",
        state="planned",
        required_capability="read_file",
        plausible_duration_seconds=9,
        timeout_seconds=30,
    )

    async def immediate(_entry, _checkpoint):
        return ActivityRunResult("private result", 3.0, 0.5)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("src.open_llm_vtuber.autonomy.activity.asyncio.sleep", no_sleep)
    tracer = Tracer()
    executor = ActivityExecutor(
        store,
        immediate,
        lambda _payload: _noop(),
        tracer=tracer,
        max_plausible_duration_seconds=2,
    )

    await executor.execute(entry, checkpoint)

    attributes = tracer.last_span.attributes
    assert attributes["agenda.queue_wait_seconds"] == 3.0
    assert attributes["agenda.execution_seconds"] == 0.5
    assert attributes["agenda.requested_pacing_seconds"] == 9
    assert attributes["agenda.applied_pacing_seconds"] == 2
    assert 0 <= attributes["agenda.pacing_sleep_seconds"] < 0.01
    assert attributes["agenda.execution_timeout_seconds"] == 30
    assert "private result" not in repr(attributes)


async def _noop():
    return None
