"""Runtime coverage shared by conversations and the global agenda."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.open_llm_vtuber.agent.output_types import (
    Actions,
    DisplayText,
    SentenceOutput,
)
from src.open_llm_vtuber.autonomy.agenda import create_entry
from src.open_llm_vtuber.autonomy.driver import (
    _agenda_step_metadata,
    _agenda_step_text,
    _execute_validated_tool,
    _publish_completion,
)
from src.open_llm_vtuber.conversations.llm_request_queue import (
    PRIORITY_PASSIVE,
    PRIORITY_USER,
    ClientRequestQueue,
    QueueExecutionTimeout,
)
import pytest
from src.open_llm_vtuber.conversations.single_conversation import (
    process_single_conversation,
)
from src.open_llm_vtuber.conversations.types import AgentTurnResult


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


async def test_synthetic_companion_turn_persists_only_assistant_history(monkeypatch):
    stored = []
    context = _context()
    context.history_uid = "history"
    monkeypatch.setattr(
        "src.open_llm_vtuber.study.log_turn_finished",
        lambda _length: None,
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.conversations.single_conversation.store_message",
        lambda **kwargs: stored.append(kwargs),
    )

    async def collect_output(**_kwargs):
        return "Welcome back—what would feel good to do next?"

    monkeypatch.setattr(
        "src.open_llm_vtuber.conversations.single_conversation.process_agent_output",
        collect_output,
    )

    async def send(_payload: str) -> None:
        return None

    result = await process_single_conversation(
        context=context,
        websocket_send=send,
        client_uid="welcome-client",
        user_input="internal context that must not be stored",
        metadata={"synthetic_user": True},
    )

    assert result.success is True
    assert [message["role"] for message in stored] == ["ai"]
    assert stored[0]["content"] == "Welcome back—what would feel good to do next?"


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


async def test_execution_timeout_starts_after_queue_wait():
    queue = ClientRequestQueue("dequeue-timeout-test")
    queue.start()
    blocker_started = asyncio.Event()
    release_blocker = asyncio.Event()

    async def blocker() -> None:
        blocker_started.set()
        await release_blocker.wait()

    async def quick_work() -> str:
        return "done"

    try:
        await queue.enqueue(
            coro_factory=blocker,
            priority=PRIORITY_USER,
            label="blocker",
        )
        await asyncio.wait_for(blocker_started.wait(), timeout=1)
        queued = asyncio.create_task(
            queue.enqueue_and_wait(
                coro_factory=quick_work,
                priority=PRIORITY_PASSIVE,
                label="timed-after-dequeue",
                dedupe_key="timed-after-dequeue",
                execution_timeout_seconds=0.01,
            )
        )
        await asyncio.sleep(0.03)
        assert not queued.done()
        release_blocker.set()
        assert await asyncio.wait_for(queued, timeout=1) == "done"
    finally:
        queue.stop()
        await asyncio.sleep(0)


async def test_execution_timeout_reports_queue_and_execution_timings():
    queue = ClientRequestQueue("execution-timeout-test")
    queue.start()

    async def slow_work() -> None:
        await asyncio.Event().wait()

    try:
        with pytest.raises(QueueExecutionTimeout) as caught:
            await queue.enqueue_and_wait(
                coro_factory=slow_work,
                priority=PRIORITY_PASSIVE,
                label="slow-work",
                dedupe_key="slow-work",
                execution_timeout_seconds=0.01,
            )
        assert caught.value.timeout_stage == "execution"
        assert caught.value.execution_seconds >= 0.01
        assert caught.value.queue_wait_seconds >= 0
    finally:
        queue.stop()
        await asyncio.sleep(0)


async def test_timed_out_work_is_disposed_before_caller_can_retry():
    queue = ClientRequestQueue("execution-timeout-retry-test")
    queue.start()

    async def slow_work() -> None:
        await asyncio.Event().wait()

    async def quick_work() -> str:
        return "retried"

    try:
        with pytest.raises(QueueExecutionTimeout) as caught:
            await queue.enqueue_and_wait(
                coro_factory=slow_work,
                priority=PRIORITY_PASSIVE,
                label="slow-work",
                dedupe_key="stable-step",
                execution_timeout_seconds=0.01,
                timeout_stage="tool_execution",
            )
        assert caught.value.timeout_stage == "tool_execution"

        assert (
            await queue.enqueue_and_wait(
                coro_factory=quick_work,
                priority=PRIORITY_PASSIVE,
                label="retried-work",
                dedupe_key="stable-step",
                execution_timeout_seconds=1,
            )
            == "retried"
        )
    finally:
        queue.stop()
        await asyncio.sleep(0)


async def test_validated_tool_step_executes_exact_call_without_agent_turn():
    class FakeToolExecutor:
        def __init__(self):
            self.calls = []

        async def execute_tools(self, calls, caller_mode):
            self.calls.append((calls, caller_mode))
            yield {"type": "tool_call_status", "status": "running"}
            yield {
                "type": "final_tool_results",
                "results": [
                    {
                        "tool_id": calls[0]["id"],
                        "content": "Found the unread message.",
                        "is_error": False,
                    }
                ],
            }

    executor = FakeToolExecutor()
    checkpoint = SimpleNamespace(
        step_id="step-1",
        required_capability="search_emails",
        arguments={"query": "is:unread", "max_results": 5},
    )

    result = await _execute_validated_tool(executor, checkpoint)

    assert result == "Found the unread message."
    assert executor.calls == [
        (
            [
                {
                    "id": "agenda_tool_step-1",
                    "name": "search_emails",
                    "args": {"query": "is:unread", "max_results": 5},
                }
            ],
            "Prompt",
        )
    ]


async def test_validated_tool_step_propagates_tool_failure():
    class FailingToolExecutor:
        async def execute_tools(self, calls, caller_mode):
            del calls, caller_mode
            yield {
                "type": "final_tool_results",
                "results": [
                    {
                        "content": "mailbox unavailable",
                        "is_error": True,
                    }
                ],
            }

    checkpoint = SimpleNamespace(
        step_id="step-2",
        required_capability="search_emails",
        arguments={},
    )

    with pytest.raises(RuntimeError, match="mailbox unavailable"):
        await _execute_validated_tool(FailingToolExecutor(), checkpoint)


def test_agenda_step_extracts_text_from_agent_turn_result():
    result = AgentTurnResult(
        success=True,
        response="Step completed with tools.",
        reported=False,
    )

    assert _agenda_step_text(result) == "Step completed with tools."


def test_agenda_step_stays_out_of_user_memory():
    metadata = _agenda_step_metadata(
        SimpleNamespace(id="agenda-entry"),
        SimpleNamespace(step_id="agenda-step"),
    )

    assert metadata["autonomous"] is True
    assert metadata["skip_history"] is True
    assert metadata["skip_memory"] is True


def test_agenda_step_propagates_agent_turn_failure():
    result = AgentTurnResult(
        success=False,
        response="partial response",
        error="tool execution failed",
        reported=False,
    )

    try:
        _agenda_step_text(result)
    except RuntimeError as exc:
        assert str(exc) == "tool execution failed"
    else:
        raise AssertionError("failed agent turns must fail the agenda step")


async def test_autonomous_completion_reaches_live_and_persisted_conversation(
    monkeypatch,
):
    import json

    sent: list[dict] = []
    stored: list[dict] = []
    context = _context()
    context.history_uid = "current-history"
    entry = create_entry(
        kind="pursuit",
        title="Finished background research",
        description="Review one useful source.",
        origin="autonomous",
        evidence_refs=["memory:today.md"],
        completion_condition="One source was reviewed.",
        time_budget_seconds=60,
        report_policy="on_completion",
    )

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    monkeypatch.setattr(
        "src.open_llm_vtuber.autonomy.driver.store_message",
        lambda **kwargs: stored.append(kwargs),
    )

    await _publish_completion(
        send=send,
        context=context,
        conf_uid="test-character",
        entry=entry,
        summary="Found a useful source and saved the key point.",
    )

    assert sent[0]["type"] == "autonomous-message"
    assert sent[0]["message_id"] == f"agenda-report:{entry.id}"
    assert sent[0]["display_text"]["text"] == (
        "Finished background research\n\nFound a useful source and saved the key point."
    )
    assert stored[0]["history_uid"] == "current-history"
    assert stored[0]["role"] == "ai"
    assert stored[0]["content"] == sent[0]["display_text"]["text"]
    assert stored[0]["message_id"] == sent[0]["message_id"]
