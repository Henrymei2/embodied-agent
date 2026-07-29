"""Runtime coverage for continuous, outcome-tracked autonomous behavior."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace

import pytest

from src.open_llm_vtuber.agent.output_types import (
    Actions,
    DisplayText,
    SentenceOutput,
)
from src.open_llm_vtuber.autonomy import (
    ACT_SILENT,
    AutonomySettings,
    AutonomyState,
    Direction,
    Observation,
    PresenceLedger,
)
from src.open_llm_vtuber.autonomy.directions import CANDIDATES
from src.open_llm_vtuber.autonomy.driver import (
    AutonomySupervisor,
    _dispatch,
    shutdown_autonomy_supervisors,
    start_autonomy_supervisor,
)
from src.open_llm_vtuber.autonomy.observation import (
    clear_client_presence,
    get_client_presence,
)
from src.open_llm_vtuber.autonomy.planner import PlannerDecision
from src.open_llm_vtuber.client_registry import register_client, unregister_client
from src.open_llm_vtuber.conversations.llm_request_queue import (
    PRIORITY_PASSIVE,
    PRIORITY_USER,
    ClientRequestQueue,
    get_queue,
    remove_queue,
)
from src.open_llm_vtuber.conversations.single_conversation import (
    process_single_conversation,
)


class FakeAgent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def chat(self, _batch_input):
        if self.fail:
            raise RuntimeError("background model failed")
        yield SentenceOutput(
            display_text=DisplayText("background result"),
            tts_text="this must never be spoken",
            actions=Actions(),
        )


def _context(*, fail: bool = False):
    return SimpleNamespace(
        agent_engine=FakeAgent(fail=fail),
        asr_engine=None,
        tts_engine=object(),
        translate_engine=None,
        history_uid="",
        character_config=SimpleNamespace(
            conf_uid="test-character",
            human_name="User",
            character_name="Agent",
            avatar="",
        ),
    )


async def test_report_false_turn_is_genuinely_silent(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        "src.open_llm_vtuber.study.log_turn_finished",
        lambda _length: None,
    )

    async def send(payload: str) -> None:
        sent.append(payload)

    result = await process_single_conversation(
        context=_context(),
        websocket_send=send,
        client_uid="silent-client",
        user_input="Do useful private background work.",
        metadata={"report": False, "skip_history": True},
    )

    assert result.success is True
    assert result.reported is False
    assert result.response == "background result"
    assert sent == []


async def test_dispatch_accounts_only_for_successful_execution(tmp_path, monkeypatch):
    client_uid = "successful-autonomy"
    messages: list[dict] = []
    monkeypatch.setattr(
        "src.open_llm_vtuber.study.log_turn_finished",
        lambda _length: None,
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.study.log_event",
        lambda *_args, **_kwargs: None,
    )

    async def send(payload: str) -> None:
        messages.append(json.loads(payload))

    register_client(client_uid, _context(), send)
    settings = AutonomySettings(attention_refill_per_hour=0)
    presence = PresenceLedger.load(tmp_path / "presence", settings)
    state = AutonomyState.load(tmp_path / "state")
    decision = PlannerDecision(
        action=ACT_SILENT,
        direction=Direction.GROWTH,
        prompt="Complete one safe background task.",
        report=False,
        score=0.9,
        reason="runtime test",
    )

    try:
        before = presence.budget_balance
        assert await _dispatch(
            client_uid,
            decision,
            Observation(user_present=False, presence_fresh=False),
            presence,
            state,
            str(tmp_path.resolve()),
        )
        queue = get_queue(client_uid)
        await asyncio.wait_for(queue.join(), timeout=2)

        receipt = presence.receipts[-1]
        assert receipt.status == "succeeded"
        assert receipt.budget_charged is True
        assert presence.budget_balance == pytest.approx(before - 0.25)
        assert state.total_actions == 1
        assert messages
        assert {message["type"] for message in messages} == {"autonomy-receipt"}
    finally:
        remove_queue(client_uid)
        unregister_client(client_uid)
        await asyncio.sleep(0)


async def test_failed_dispatch_refunds_budget_and_does_not_advance_state(
    tmp_path, monkeypatch
):
    client_uid = "failed-autonomy"
    monkeypatch.setattr(
        "src.open_llm_vtuber.study.log_turn_finished",
        lambda _length: None,
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.study.log_event",
        lambda *_args, **_kwargs: None,
    )

    async def send(_payload: str) -> None:
        return None

    register_client(client_uid, _context(fail=True), send)
    settings = AutonomySettings(attention_refill_per_hour=0)
    presence = PresenceLedger.load(tmp_path / "presence", settings)
    state = AutonomyState.load(tmp_path / "state")
    decision = PlannerDecision(
        action=ACT_SILENT,
        direction=Direction.GROWTH,
        prompt="This task fails.",
        report=False,
        score=0.9,
        reason="runtime failure test",
    )

    try:
        before = presence.budget_balance
        assert await _dispatch(
            client_uid,
            decision,
            Observation(user_present=False, presence_fresh=False),
            presence,
            state,
            str(tmp_path.resolve()),
        )
        queue = get_queue(client_uid)
        await asyncio.wait_for(queue.join(), timeout=2)

        receipt = presence.receipts[-1]
        assert receipt.status == "failed"
        assert receipt.budget_charged is False
        assert "background model failed" in (receipt.error or "")
        assert presence.budget_balance == pytest.approx(before)
        assert state.total_actions == 0
    finally:
        remove_queue(client_uid)
        unregister_client(client_uid)
        await asyncio.sleep(0)


async def test_higher_priority_user_work_preempts_background_work():
    queue = ClientRequestQueue("priority-test")
    queue.start()
    background_started = asyncio.Event()
    background_canceled = asyncio.Event()
    user_finished = asyncio.Event()
    outcomes: list[tuple[str, str]] = []

    async def background() -> None:
        background_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            background_canceled.set()

    async def user_work() -> None:
        user_finished.set()

    try:
        await queue.enqueue(
            coro_factory=background,
            priority=PRIORITY_PASSIVE,
            label="background",
            dedupe_key="background",
            on_done=lambda status, _error: outcomes.append(("background", status)),
        )
        await asyncio.wait_for(background_started.wait(), timeout=1)

        await queue.enqueue(
            coro_factory=user_work,
            priority=PRIORITY_USER,
            label="user",
            on_done=lambda status, _error: outcomes.append(("user", status)),
        )
        await asyncio.wait_for(user_finished.wait(), timeout=1)
        await asyncio.wait_for(queue.join(), timeout=1)

        assert background_canceled.is_set()
        assert ("background", "canceled") in outcomes
        assert ("user", "completed") in outcomes
    finally:
        queue.stop()
        await asyncio.sleep(0)


def test_stale_presence_fails_closed_for_user_facing_work(tmp_path):
    client_uid = "missing-presence"
    clear_client_presence(client_uid)
    signal = get_client_presence(client_uid)
    assert signal.user_present is False
    assert signal.fresh is False

    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    assert ledger.can_afford(
        1.25,
        user_facing=True,
        presence_fresh=False,
    ) == (False, "presence signal is stale")
    assert ledger.can_afford(
        0.25,
        user_facing=False,
        presence_fresh=False,
    )[0]


def test_restart_closes_interrupted_work_and_refunds_budget(tmp_path):
    settings = AutonomySettings(attention_refill_per_hour=0)
    ledger = PresenceLedger.load(tmp_path, settings)
    before = ledger.budget_balance
    observation = Observation(user_present=False, presence_fresh=False)
    receipt = ledger.queue_action(
        direction="growth",
        action=ACT_SILENT,
        score=0.8,
        reason="restart test",
        observation=observation,
    )
    assert ledger.start_action(receipt.receipt_id, observation=observation)[0]
    assert ledger.budget_balance == pytest.approx(before - 0.25)

    reloaded = PresenceLedger.load(tmp_path, settings)

    recovered = reloaded.receipts[-1]
    assert recovered.status == "canceled"
    assert recovered.budget_charged is False
    assert "previous server shutdown" in (recovered.error or "")
    assert reloaded.budget_balance == pytest.approx(before)


def test_project_and_reading_candidates_require_concrete_work():
    empty = Observation()
    concrete = Observation(
        approved_plan_count=1,
        open_todo_count=2,
        has_active_reading=True,
    )
    work = [
        candidate for candidate in CANDIDATES if candidate.direction == Direction.WORK
    ]
    reading = [
        candidate for candidate in CANDIDATES if "Continue the book" in candidate.prompt
    ]

    assert work and all(candidate.relevance(empty) == 0 for candidate in work)
    assert all(candidate.relevance(concrete) > 0 for candidate in work)
    assert reading and reading[0].relevance(empty) == 0
    assert reading[0].relevance(concrete) > 0


async def test_supervisor_survives_without_clients_and_hands_off(tmp_path, monkeypatch):
    seen: list[str] = []

    async def fake_loop(client_uid, *, is_attached, **_kwargs):
        seen.append(client_uid)
        while is_attached():
            await asyncio.sleep(0)

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.driver.autonomy_loop",
        fake_loop,
    )
    supervisor = AutonomySupervisor(tmp_path)
    task = supervisor.start()
    settings = AutonomySettings()
    presence = object()

    try:
        await asyncio.sleep(0)
        assert not task.done()

        supervisor.attach("first", settings, presence)
        for _ in range(20):
            if "first" in seen:
                break
            await asyncio.sleep(0)

        supervisor.attach("second", settings, presence)
        for _ in range(20):
            if "second" in seen:
                break
            await asyncio.sleep(0)

        assert seen[:2] == ["first", "second"]
        supervisor.detach("first")
        supervisor.detach("second")
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_server_owned_supervisor_shuts_down_cleanly(tmp_path):
    task = start_autonomy_supervisor(tmp_path)
    await asyncio.sleep(0)
    assert not task.done()

    await asyncio.wait_for(shutdown_autonomy_supervisors(), timeout=1)

    assert task.done()
