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
    idle_scene_snapshot,
    relocate_for_idle,
    validate_idle_placement,
)
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


async def test_idle_planner_receives_bounded_room_and_returns_structured_choice():
    prompts: list[str] = []

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        return '{"object_id":"reading_chair","reason":"A quiet place to reflect."}'

    placement = await StructuredAgendaPlanner(complete).choose_idle_placement(
        _context(),
        _scene("sofa", "reading_chair", "bed"),
    )

    assert placement == IdlePlacement(
        "reading_chair",
        "A quiet place to reflect.",
    )
    assert '"object_id": "reading_chair"' in prompts[0]
    assert "choose where an embodied agent should wait" in prompts[0]


def test_invalid_idle_choice_falls_back_to_sofa_then_staying_put():
    invalid = IdlePlacement("invented_corner", "It sounded comfortable.")

    assert validate_idle_placement(invalid, _scene("bed", "sofa")).object_id == "sofa"
    assert validate_idle_placement(invalid, _scene("bed")).object_id is None


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


async def test_removed_destination_falls_back_to_live_sofa(monkeypatch):
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

    assert await relocate_for_idle(
        client_uid="client",
        conf_uid="character",
        placement=IdlePlacement("removed_chair", "It existed a moment ago."),
    )
    sofa = next(node for node in state["nodes"] if node["id"] == "sofa")
    assert actions[0][1]["x"] == sofa["x"]
    assert actions[0][1]["y"] == sofa["y"]


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
        self.choose_calls = 0
        self.generation_started = asyncio.Event()

    async def choose_idle_placement(self, _context, _scene):
        self.choose_calls += 1
        return IdlePlacement("sofa", "A comfortable place to think.")

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


def _executive(
    tmp_path,
    planner,
    relocate,
    *,
    scene: IdleScene | None = None,
):
    store = AgendaStore(tmp_path)

    async def emit(_payload):
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
    )


async def test_planning_overlaps_relocation_but_advance_waits_for_arrival(tmp_path):
    generation_gate = asyncio.Event()
    relocation_gate = asyncio.Event()
    relocation_started = asyncio.Event()
    completed = _pursuit(status="completed", title="Finished earlier work")
    proposal = _pursuit(
        title="Choose the next useful pursuit",
        evidence_refs=[f"agenda:{completed.id}"],
        status="proposed",
    )
    planner = _LifecyclePlanner(proposal, generation_gate=generation_gate)

    async def relocate(_placement):
        relocation_started.set()
        await relocation_gate.wait()
        return True

    store, executive = _executive(tmp_path, planner, relocate)
    store.add(completed)

    advance = asyncio.create_task(executive._advance())
    await asyncio.wait_for(relocation_started.wait(), timeout=1)
    await asyncio.wait_for(planner.generation_started.wait(), timeout=1)
    generation_gate.set()
    await asyncio.sleep(0)

    assert not advance.done()
    relocation_gate.set()
    assert await asyncio.wait_for(advance, timeout=1) is True
    assert [entry.kind for entry in store.load().entries] == ["pursuit", "pursuit"]


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

    assert await executive._advance() is True
    assert planner.choose_calls == 0
    assert relocations == []
    assert store.load().entries[0].status == "completed"


async def test_invalid_planner_output_and_relocation_failure_do_not_block_successor(
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

    async def invalid_choice(_context, _scene):
        raise ValueError("malformed placement JSON")

    planner.choose_idle_placement = invalid_choice

    async def relocate(placement):
        placements.append(placement)
        raise RuntimeError("frontend disconnected")

    store, executive = _executive(tmp_path, planner, relocate)
    store.add(completed)

    assert await asyncio.wait_for(executive._advance(), timeout=1) is True
    assert placements[0].object_id == "sofa"
    assert any(entry.title == proposal.title for entry in store.load().entries)


async def test_held_and_paused_states_settle_once_per_episode(tmp_path):
    proposal = _pursuit(status="blocked_approval", requires_approval=True)
    planner = _LifecyclePlanner(proposal)

    async def relocate(_placement):
        return True

    store, executive = _executive(tmp_path, planner, relocate)
    store.add(proposal)

    assert await executive._advance() is False
    assert await executive._advance() is False
    assert planner.choose_calls == 1

    store.pause(True)
    assert await executive._advance() is False
    assert await executive._advance() is False
    assert planner.choose_calls == 2

    await executive.pause(False)
    store.pause(True)
    assert await executive._advance() is False
    assert planner.choose_calls == 3


async def test_user_preempts_settling_without_creating_an_idle_entry(tmp_path):
    generation_gate = asyncio.Event()
    relocation_started = asyncio.Event()
    relocation_cancelled = asyncio.Event()
    completed = _pursuit(status="completed", title="Finished autonomous work")
    proposal = _pursuit(
        title="Future work",
        evidence_refs=[f"agenda:{completed.id}"],
        status="proposed",
    )
    planner = _LifecyclePlanner(proposal, generation_gate=generation_gate)

    async def relocate(_placement):
        relocation_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            relocation_cancelled.set()

    store, executive = _executive(tmp_path, planner, relocate)
    store.add(completed)

    advance = asyncio.create_task(executive._advance())
    await asyncio.wait_for(relocation_started.wait(), timeout=1)
    user = await executive.preempt_for_user("Help now")

    assert await asyncio.wait_for(advance, timeout=1) is True
    assert relocation_cancelled.is_set()
    state = store.load()
    assert (
        next(entry for entry in state.entries if entry.id == user.id).status == "active"
    )
    assert all(entry.kind != "idle" for entry in state.entries)


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
