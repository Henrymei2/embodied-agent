"""Runtime coverage shared by conversations and the global agenda."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.open_llm_vtuber.agent.output_types import (
    Actions,
    DisplayText,
    SentenceOutput,
)
from src.open_llm_vtuber.conversations.llm_request_queue import (
    PRIORITY_PASSIVE,
    PRIORITY_USER,
    ClientRequestQueue,
)
from src.open_llm_vtuber.conversations.single_conversation import (
    process_single_conversation,
)


class FakeAgent:
    async def chat(self, _batch_input):
        yield SentenceOutput(
            display_text=DisplayText("background result"),
            tts_text="this must never be spoken",
            actions=Actions(),
        )


def _context():
    return SimpleNamespace(
        agent_engine=FakeAgent(),
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
            work_class="autonomous",
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


async def test_agenda_completion_callback_receives_failure_state():
    queue = ClientRequestQueue("agenda-callback-test")
    queue.start()
    failures: list[bool] = []

    async def failing_work() -> None:
        raise RuntimeError("expected failure")

    async def on_finished(failed: bool) -> None:
        failures.append(failed)

    try:
        await queue.enqueue(
            coro=failing_work(),
            priority=PRIORITY_USER,
            label="agenda user task",
            entry_id="user-entry",
            work_class="user",
            on_finished=on_finished,
        )
        await asyncio.wait_for(queue.join(), timeout=1)
        assert failures == [True]
    finally:
        queue.stop()
        await asyncio.sleep(0)
