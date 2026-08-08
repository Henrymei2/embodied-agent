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


async def _noop():
    return None
