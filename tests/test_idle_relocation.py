from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from src.open_llm_vtuber.autonomy.agenda import (
    AgendaStore,
    ExecutionCheckpoint,
    create_entry,
)
from src.open_llm_vtuber.autonomy.agenda_context import AgendaContext
from src.open_llm_vtuber.autonomy.agenda_planner import StructuredAgendaPlanner
from src.open_llm_vtuber.autonomy.executive import AgendaExecutive
from src.open_llm_vtuber.autonomy.idle_relocation import (
    IdleObject,
    IdlePlacement,
    IdleScene,
    choose_idle_placement,
    idle_scene_snapshot,
    relocate_for_idle,
)
from src.open_llm_vtuber.autonomy.observation import (
    clear_client_presence,
    update_client_presence,
)
from src.open_llm_vtuber.autonomy.presence import PresenceLedger
from src.open_llm_vtuber.autonomy.reporting import AgendaReporter
from src.open_llm_vtuber.autonomy.settings import AutonomySettings
from src.open_llm_vtuber.stage_state_manager import get_default_stage
from src.open_llm_vtuber.world_model import world_from_stage


def _pursuit(**overrides):
    values = {
        "kind": "pursuit",
        "title": "Review a grounded source",
        "description": "Review one source and keep a concise note.",
        "origin": "autonomous",
        "evidence_refs": ["memory:today.md"],
        "completion_condition": "One source was reviewed.",
        "time_budget_seconds": 300,
        "status": "queued",
    }
    values.update(overrides)
    return create_entry(**values)


def _scene(*object_ids: str) -> IdleScene:
    return IdleScene(
        current_position=(10.0, 20.0),
        objects=tuple(
            IdleObject(
                object_id=object_id,
                label=object_id.replace("_", " "),
                x=float(index * 100),
                y=200.0,
                distance=float(index * 100),
                posture=(
                    "sitting" if object_id in {"sofa", "reading_chair"} else "standing"
                ),
            )
            for index, object_id in enumerate(object_ids, start=1)
        ),
    )


def _context() -> AgendaContext:
    return AgendaContext(
        payload={"recent_entries": [], "presence": {}},
        evidence_refs=set(),
        capabilities=set(),
        object_names=set(),
    )


def test_local_idle_choice_uses_seated_weight_and_excludes_previous():
    captured: dict[str, object] = {}

    class ChoiceRecorder:
        def choices(self, population, *, weights, k):
            captured.update(population=population, weights=weights, k=k)
            return [population[0]]

    placement = choose_idle_placement(
        _scene("sofa", "reading_chair", "bed"),
        previous_object_id="sofa",
        rng=ChoiceRecorder(),
    )

    assert placement.object_id == "reading_chair"
    assert [obj.object_id for obj in captured["population"]] == [
        "reading_chair",
        "bed",
    ]
    assert captured["weights"] == [3.0, 1.0]
    assert captured["k"] == 1


async def test_intention_planner_preserves_companion_motivation():
    async def complete(prompt: str) -> str:
        assert "caring virtual companion" in prompt
        return """{
          "kind": "pursuit",
          "title": "Check in about the current book",
          "description": "Ask what stood out in the current chapter.",
          "motivation_type": "care",
          "evidence_refs": ["furniture:reading"],
          "novelty": "The reading session changed.",
          "completion_condition": "A grounded check-in is prepared.",
          "time_budget_seconds": 60,
          "preferred_object": "reading_chair",
          "required_capabilities": [],
          "report_policy": "on_completion",
          "risk": "low",
          "requires_approval": false
        }"""

    context = AgendaContext(
        payload={"furniture_state": {"reading": {"session": {"title": "Book"}}}},
        evidence_refs={"furniture:reading"},
        capabilities=set(),
        object_names={"reading_chair"},
    )
    entry = await StructuredAgendaPlanner(complete).propose_intention(context)

    assert entry.motivation.type == "care"
    assert entry.report_policy == "on_completion"


def test_local_idle_choice_handles_empty_and_single_object_scenes():
    assert choose_idle_placement(_scene()).object_id is None
    assert (
        choose_idle_placement(_scene("bed"), previous_object_id="bed").object_id
        == "bed"
    )


def test_room_snapshot_exposes_every_non_avatar_world_object(monkeypatch):
    stage = get_default_stage("character")
    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.idle_relocation.load_world",
        lambda _conf_uid: world_from_stage(stage),
    )

    scene = idle_scene_snapshot("character")

    ids = {obj.object_id for obj in scene.objects}
    assert "avatar" not in ids
    assert {"sofa", "reading_chair", "bed", "computer"}.issubset(ids)
    posture = {obj.object_id: obj.posture for obj in scene.objects}
    assert posture["sofa"] == posture["reading_chair"] == "sitting"
    assert posture["bed"] == posture["computer"] == "standing"


@pytest.mark.parametrize(
    ("destination", "expected_actions"),
    [
        ("sofa", ["move_to", "stage_patch_state"]),
        ("reading_chair", ["move_to", "stage_patch_state"]),
        ("bed", ["move_to"]),
        ("computer", ["move_to"]),
    ],
)
async def test_relocation_verifies_arrival_and_only_sits_at_known_seats(
    monkeypatch,
    destination,
    expected_actions,
):
    state = deepcopy(get_default_stage("character"))
    actions: list[str] = []

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.idle_relocation.load_world",
        lambda _conf_uid: world_from_stage(state),
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.idle_relocation.load_stage_state",
        lambda _conf_uid: deepcopy(state),
    )

    async def request(action, args, **_routing):
        actions.append(action)
        avatar = next(node for node in state["nodes"] if node["id"] == "avatar")
        if action == "move_to":
            avatar.update(x=args["x"], y=args["y"])
            avatar.pop("pose", None)
        else:
            avatar["pose"] = args["patch"]["nodes"][0]["pose"]
        return True, {"state": deepcopy(state)}, None

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.idle_relocation.request_frontend_action",
        request,
    )

    assert await relocate_for_idle(
        client_uid="client",
        conf_uid="character",
        placement=IdlePlacement(destination, "A sensible place to wait."),
    )
    assert actions == expected_actions
    avatar = next(node for node in state["nodes"] if node["id"] == "avatar")
    if destination in {"sofa", "reading_chair"}:
        assert avatar["pose"] == "sitting"
    else:
        assert avatar.get("pose") is None


async def test_removed_destination_is_skipped_until_the_next_local_choice(monkeypatch):
    state = deepcopy(get_default_stage("character"))
    actions: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.idle_relocation.load_world",
        lambda _conf_uid: world_from_stage(state),
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.idle_relocation.load_stage_state",
        lambda _conf_uid: deepcopy(state),
    )

    async def request(action, args, **_routing):
        actions.append((action, args))
        avatar = next(node for node in state["nodes"] if node["id"] == "avatar")
        if action == "move_to":
            avatar.update(x=args["x"], y=args["y"])
        else:
            avatar["pose"] = "sitting"
        return True, {"state": deepcopy(state)}, None

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.idle_relocation.request_frontend_action",
        request,
    )

    assert not await relocate_for_idle(
        client_uid="client",
        conf_uid="character",
        placement=IdlePlacement("removed_chair", "It existed a moment ago."),
    )
    assert actions == []


async def test_relocation_timeout_returns_without_blocking(monkeypatch):
    state = deepcopy(get_default_stage("character"))
    request_cancelled = asyncio.Event()
    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.idle_relocation.load_world",
        lambda _conf_uid: world_from_stage(state),
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.idle_relocation.IDLE_RELOCATION_TIMEOUT_SECONDS",
        0.01,
    )

    async def request(_action, _args, **_routing):
        try:
            await asyncio.Event().wait()
        finally:
            request_cancelled.set()

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.idle_relocation.request_frontend_action",
        request,
    )

    assert not await relocate_for_idle(
        client_uid="client",
        conf_uid="character",
        placement=IdlePlacement("sofa", "Wait comfortably."),
    )
    assert request_cancelled.is_set()


class _LifecyclePlanner:
    def __init__(self, proposal, *, generation_gate: asyncio.Event | None = None):
        self.proposal = proposal
        self.generation_gate = generation_gate
        self.generation_started = asyncio.Event()

    async def propose_intention(self, _context):
        self.generation_started.set()
        if self.generation_gate is not None:
            await self.generation_gate.wait()
        return self.proposal

    async def next_step(self, _entry, _context):
        return ExecutionCheckpoint(
            step_id="test-step",
            kind="reflect",
            state="planned",
        )

    async def evaluate_completion(self, _entry, _evidence):
        return True, "Completed the queued work."

    async def is_noteworthy(self, _entry, _summary, _context):
        return True


def _executive(
    tmp_path,
    planner,
    relocate,
    *,
    scene: IdleScene | None = None,
    emitted: list[dict] | None = None,
    report_result=None,
):
    store = AgendaStore(tmp_path)

    async def emit(_payload):
        if emitted is not None:
            emitted.append(_payload)
        return None

    async def run_step(_entry, _checkpoint):
        return "done"

    return store, AgendaExecutive(
        client_uid="client",
        store=store,
        planner=planner,
        settings=AutonomySettings(planner_retry_backoff_seconds=0),
        run_step=run_step,
        emit=emit,
        room_snapshot=lambda: scene or _scene("sofa", "bed"),
        relocate_idle=relocate,
        report_result=report_result,
    )


async def _complete_entry(executive, store, entry_id: str) -> None:
    for _ in range(10):
        await executive._advance()
        task = executive._entry_task
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=1)
        entry = next(item for item in store.load().entries if item.id == entry_id)
        if entry.status == "completed":
            return
    raise AssertionError(f"entry {entry_id} did not complete")


async def test_explicit_idle_entry_starts_one_reusable_relocation_loop(tmp_path):
    proposal = _pursuit(title="Unused successor", status="proposed")
    planner = _LifecyclePlanner(proposal)
    relocations: list[IdlePlacement] = []

    async def relocate(placement):
        relocations.append(placement)
        return True

    store, executive = _executive(tmp_path, planner, relocate)
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

    assert await executive._advance_entry(idle) is False
    await asyncio.sleep(0)
    assert len(relocations) == 1
    task = executive._idle_relocation_task
    assert task is not None and not task.done()
    executive._ensure_idle_relocation()
    assert executive._idle_relocation_task is task
    await executive._stop_idle_relocation()


async def test_successor_planning_runs_alongside_idle_relocation(tmp_path):
    generation_gate = asyncio.Event()
    completed = _pursuit(status="completed", title="Finished earlier work")
    proposal = _pursuit(
        title="Choose the next useful pursuit",
        evidence_refs=[f"agenda:{completed.id}"],
        status="proposed",
    )
    planner = _LifecyclePlanner(proposal, generation_gate=generation_gate)

    relocations: list[IdlePlacement] = []

    async def relocate(placement):
        relocations.append(placement)
        return True

    emitted: list[dict] = []
    store, executive = _executive(tmp_path, planner, relocate, emitted=emitted)
    store.add(completed)

    advance = asyncio.create_task(executive._advance())
    await asyncio.wait_for(planner.generation_started.wait(), timeout=1)
    for _ in range(10):
        if relocations:
            break
        await asyncio.sleep(0)
    assert len(relocations) == 1
    generation_gate.set()
    assert await asyncio.wait_for(advance, timeout=1) is True
    assert [entry.kind for entry in store.load().entries] == ["pursuit", "pursuit"]
    phases = [
        event["phase"] for event in emitted if event.get("type") == "agent_activity"
    ]
    assert phases == ["planning", "idle"]
    assert executive._idle_relocation_task is not None

    assert await executive._advance() is True  # queued pursuit becomes active
    assert await executive._advance() is True  # execution takes ownership
    assert executive._idle_relocation_task is None
    await executive._cancel_entry_work()


async def test_pausing_stops_an_active_idle_relocation_loop(tmp_path):
    planner = _LifecyclePlanner(_pursuit(status="proposed"))
    relocation_started = asyncio.Event()

    async def relocate(_placement):
        relocation_started.set()
        await asyncio.Event().wait()

    store, executive = _executive(tmp_path, planner, relocate)
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

    assert await executive._advance_entry(idle) is False
    await asyncio.wait_for(relocation_started.wait(), timeout=1)
    await executive.pause(True)

    assert executive._idle_relocation_task is None


async def test_runnable_queued_work_does_not_trigger_idle_relocation(tmp_path):
    proposal = _pursuit(title="Unused successor", status="proposed")
    planner = _LifecyclePlanner(proposal)
    relocations: list[IdlePlacement] = []

    async def relocate(placement):
        relocations.append(placement)
        return True

    store, executive = _executive(tmp_path, planner, relocate)
    queued = _pursuit(title="Already queued")
    store.add(queued)

    await _complete_entry(executive, store, queued.id)
    assert relocations == []
    assert store.load().entries[0].status == "completed"


async def test_successor_generation_uses_local_idle_relocation_without_planner_call(
    tmp_path,
):
    completed = _pursuit(status="completed", title="Finished autonomous work")
    proposal = _pursuit(
        title="Continue with grounded work",
        evidence_refs=[f"agenda:{completed.id}"],
        status="proposed",
    )
    planner = _LifecyclePlanner(proposal)
    placements: list[IdlePlacement] = []

    async def relocate(placement):
        placements.append(placement)
        return True

    store, executive = _executive(tmp_path, planner, relocate)
    store.add(completed)

    assert await asyncio.wait_for(executive._advance(), timeout=1) is True
    for _ in range(10):
        if placements:
            break
        await asyncio.sleep(0)
    assert len(placements) == 1
    assert any(entry.title == proposal.title for entry in store.load().entries)
    await executive._stop_idle_relocation()


async def test_held_state_does_not_start_idle_relocation(tmp_path):
    proposal = _pursuit(status="blocked_approval", requires_approval=True)
    planner = _LifecyclePlanner(proposal)

    async def relocate(_placement):
        return True

    store, executive = _executive(tmp_path, planner, relocate)
    store.add(proposal)

    assert await executive._advance() is False
    assert await executive._advance() is False

    await executive.pause(True)
    assert await executive._advance() is False
    assert await executive._advance() is False

    await executive.pause(False)
    assert await executive._advance() is False
    assert await executive._advance() is False


async def test_user_preempts_successor_generation_and_idle_relocation(tmp_path):
    generation_gate = asyncio.Event()
    completed = _pursuit(status="completed", title="Finished autonomous work")
    proposal = _pursuit(
        title="Future work",
        evidence_refs=[f"agenda:{completed.id}"],
        status="proposed",
    )
    planner = _LifecyclePlanner(proposal, generation_gate=generation_gate)

    relocations: list[IdlePlacement] = []

    async def relocate(placement):
        relocations.append(placement)
        return True

    emitted: list[dict] = []
    store, executive = _executive(tmp_path, planner, relocate, emitted=emitted)
    store.add(completed)

    advance = asyncio.create_task(executive._advance())
    await asyncio.wait_for(planner.generation_started.wait(), timeout=1)
    for _ in range(10):
        if relocations:
            break
        await asyncio.sleep(0)
    user = await executive.preempt_for_user("Help now")

    assert await asyncio.wait_for(advance, timeout=1) is True
    assert len(relocations) == 1
    assert executive._idle_relocation_task is None
    state = store.load()
    assert (
        next(entry for entry in state.entries if entry.id == user.id).status == "active"
    )
    assert all(entry.kind != "idle" for entry in state.entries)
    phases = [
        event["phase"] for event in emitted if event.get("type") == "agent_activity"
    ]
    assert phases == ["planning"]


async def test_user_preempts_active_agenda_work_without_stopping_executive(tmp_path):
    step_started = asyncio.Event()
    step_cancelled = asyncio.Event()
    proposal = _pursuit(title="Unused successor", status="proposed")
    planner = _LifecyclePlanner(proposal)
    store = AgendaStore(tmp_path)

    async def emit(_payload):
        return None

    async def run_step(_entry, _checkpoint):
        step_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            step_cancelled.set()

    executive = AgendaExecutive(
        client_uid="client",
        store=store,
        planner=planner,
        settings=AutonomySettings(),
        run_step=run_step,
        emit=emit,
    )
    autonomous = _pursuit(status="active")
    store.add(autonomous)

    advance = asyncio.create_task(executive._advance())
    await asyncio.wait_for(step_started.wait(), timeout=1)
    user = await executive.preempt_for_user("Urgent user request")

    assert await asyncio.wait_for(advance, timeout=1) is True
    assert step_cancelled.is_set()
    state = store.load()
    assert next(
        entry for entry in state.entries if entry.id == autonomous.id
    ).status == ("suspended")
    assert (
        next(entry for entry in state.entries if entry.id == user.id).status == "active"
    )


@pytest.mark.parametrize(
    ("report_policy", "noteworthy", "expected_reports"),
    [
        ("silent", True, 1),
        ("if_noteworthy", False, 1),
        ("if_noteworthy", True, 1),
        ("on_completion", False, 1),
        ("ask_first", False, 1),
    ],
)
async def test_business_completion_reporting_overrides_entry_policy(
    tmp_path,
    report_policy,
    noteworthy,
    expected_reports,
):
    proposal = _pursuit(title="Unused successor", status="proposed")
    planner = _LifecyclePlanner(proposal)

    reports: list[tuple[str, str, str]] = []
    emitted: list[dict] = []

    async def relocate(_placement):
        return True

    async def report(entry, summary):
        reports.append((entry.title, entry.status, summary))

    async def emit(payload):
        emitted.append(payload)

    async def evaluate_completion(_entry, _evidence):
        if noteworthy:
            return True, "Completed a substantial, useful autonomous result."
        return True, "Done."

    planner.evaluate_completion = evaluate_completion
    settings = AutonomySettings()
    reporter = AgendaReporter(
        "client",
        PresenceLedger.load(tmp_path, settings),
        emit,
        report,
    )
    update_client_presence(
        "client",
        {"idle_seconds": 0, "user_present": True, "focus_mode": False},
    )
    store, executive = _executive(
        tmp_path,
        planner,
        relocate,
        emitted=emitted,
        report_result=reporter.deliver,
    )
    queued = _pursuit(
        title="Reportable autonomous work",
        report_policy=report_policy,
    )
    store.add(queued)

    try:
        await _complete_entry(executive, store, queued.id)
        assert await executive._deliver_due_report() is True
        assert len(reports) == expected_reports
        assert store.load().reports[0].status == "delivered"
        if reports:
            assert reports[0] == (
                "Reportable autonomous work",
                "completed",
                (
                    "Completed a substantial, useful autonomous result."
                    if noteworthy
                    else "Done."
                ),
            )
    finally:
        clear_client_presence("client")
