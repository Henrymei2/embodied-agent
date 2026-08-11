"""Runtime coverage shared by conversations and the global agenda."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.open_llm_vtuber.agent.output_types import (
    Actions,
    DisplayText,
    SentenceOutput,
)
from src.open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from src.open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource
from src.open_llm_vtuber.autonomy.agenda import create_entry
from src.open_llm_vtuber.autonomy.driver import (
    _agenda_step_metadata,
    _agenda_step_text,
    _ensure_tool_proximity,
    _execute_validated_tool,
    _publish_completion,
    _run_tool_calls,
)
from src.open_llm_vtuber.config_manager.tts_preprocessor import (
    TTSPreprocessorConfig,
    TranslatorConfig,
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


class _TextLLM:
    def get_tool_mode(self):
        return "OpenAI"

    async def chat_completion(self, messages, system=None, tools=None):
        del messages, system, tools
        yield "Hello there."


class _RecordingMemory:
    def __init__(self):
        self.compact_calls = 0
        self.user_messages: list[str] = []
        self.assistant_messages: list[str] = []

    async def maybe_compact_context(self, next_user_text=None):
        del next_user_text
        self.compact_calls += 1

    def add_user_message(self, text: str) -> None:
        self.user_messages.append(text)

    def add_assistant_message(self, text: str) -> None:
        self.assistant_messages.append(text)

    def build_memory_context(self) -> str:
        return ""


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


async def test_validated_tool_step_rejects_schema_mismatch_before_dispatch():
    class NeverCalledExecutor:
        async def execute_tools(self, calls, caller_mode):
            del calls, caller_mode
            raise AssertionError("schema-invalid step must not reach execution")

    checkpoint = SimpleNamespace(
        step_id="step-3",
        required_capability="get_email",
        arguments={
            "email_id": "94bff83f9ff5270b5bff233944be828d98b2b61442d2c4aa652c74428ce08997"
        },
    )

    with pytest.raises(RuntimeError, match="message_id"):
        await _execute_validated_tool(
            NeverCalledExecutor(),
            checkpoint,
            tool_schemas={
                "get_email": {
                    "required": ["message_id"],
                    "properties": {"message_id": "string"},
                    "additionalProperties": False,
                }
            },
        )


async def test_run_tool_calls_forwards_sanitized_updates_without_inputs():
    class FakeToolExecutor:
        async def execute_tools(self, calls, caller_mode):
            del caller_mode
            assert calls == [
                {"id": "agenda_tool_t1", "name": "search_emails", "args": {}}
            ]
            yield {
                "type": "agent_activity",
                "phase": "tools",
                "tools": [
                    {
                        "tool_id": "t1",
                        "tool_name": "search_emails",
                        "status": "pending",
                    }
                ],
            }
            yield {
                "type": "tool_call_status",
                "tool_id": "t1",
                "tool_name": "search_emails",
                "status": "running",
                "content": "Input: {\"query\": \"secret-criteria\"}",
            }
            yield {
                "type": "final_tool_results",
                "results": [
                    {"tool_id": "t1", "content": "Found messages.", "is_error": False}
                ],
            }

    received: list[dict] = []

    async def on_update(update: dict) -> None:
        received.append(update)

    results = await _run_tool_calls(
        FakeToolExecutor(),
        [{"id": "agenda_tool_t1", "name": "search_emails", "args": {}}],
        on_update=on_update,
    )

    assert results == [
        {"tool_id": "t1", "content": "Found messages.", "is_error": False}
    ]
    assert [item["type"] for item in received] == [
        "agent_activity",
        "tool_call_status",
    ]
    assert received[1]["status"] == "running"
    assert "content" not in received[1]
    assert "secret-criteria" not in str(received)


async def test_run_tool_calls_forwards_without_on_update_callback():
    class FakeToolExecutor:
        async def execute_tools(self, calls, caller_mode):
            del calls, caller_mode
            yield {"type": "tool_call_status", "status": "running"}
            yield {
                "type": "final_tool_results",
                "results": [{"content": "ok", "is_error": False}],
            }

    results = await _run_tool_calls(FakeToolExecutor(), [{"id": "t", "name": "x"}])
    assert results == [{"content": "ok", "is_error": False}]


async def test_ensure_tool_proximity_moves_before_gated_tool_when_far():
    class FakeToolManager:
        def get_required_object(self, capability):
            return {"list_todos": "todo_list"}.get(capability)

        def get_reachable_objects(self):
            return ["mailbox"]

    class FakeToolExecutor:
        def __init__(self):
            self.calls = []

        async def execute_tools(self, calls, caller_mode):
            self.calls.append(calls)
            yield {
                "type": "final_tool_results",
                "results": [
                    {
                        "tool_id": calls[0]["id"],
                        "content": "Arrived at todo_list.",
                        "is_error": False,
                    }
                ],
            }

    executor = FakeToolExecutor()
    await _ensure_tool_proximity(executor, FakeToolManager(), "list_todos", "step-9")

    assert [call["name"] for call in executor.calls[0]] == ["move_to"]
    assert executor.calls[0][0]["args"] == {"target": "todo_list"}


async def test_ensure_tool_proximity_skips_move_when_object_reachable():
    class FakeToolManager:
        def get_required_object(self, capability):
            return {"get_email": "mailbox"}.get(capability)

        def get_reachable_objects(self):
            return ["mailbox"]

    class NeverCalledExecutor:
        async def execute_tools(self, calls, caller_mode):
            del calls, caller_mode
            raise AssertionError("reachable tool must not trigger a move")

    await _ensure_tool_proximity(
        NeverCalledExecutor(),
        FakeToolManager(),
        "get_email",
        "step-10",
    )


async def test_ensure_tool_proximity_propagates_move_failure():
    class FakeToolManager:
        def get_required_object(self, capability):
            return "todo_list"

        def get_reachable_objects(self):
            return []

    class FailingExecutor:
        async def execute_tools(self, calls, caller_mode):
            del calls, caller_mode
            yield {
                "type": "final_tool_results",
                "results": [{"content": "todo_list is unreachable", "is_error": True}],
            }

    with pytest.raises(RuntimeError, match="todo_list is unreachable"):
        await _ensure_tool_proximity(
            FailingExecutor(),
            FakeToolManager(),
            "list_todos",
            "step-11",
        )


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
    assert metadata["synthetic_user"] is True
    assert metadata["skip_history"] is True
    assert metadata["skip_memory"] is True


@pytest.mark.asyncio
async def test_autonomous_step_metadata_skips_memory_compaction_and_recording():
    memory = _RecordingMemory()
    agent = BasicMemoryAgent(
        llm=_TextLLM(),
        system="base",
        use_mcpp=False,
        faster_first_response=False,
        tts_preprocessor_config=TTSPreprocessorConfig(
            remove_special_char=False,
            translator_config=TranslatorConfig(
                translate_audio=False,
                translate_provider="deeplx",
            ),
        ),
        memory_manager=memory,
    )
    metadata = _agenda_step_metadata(
        SimpleNamespace(id="agenda-entry"),
        SimpleNamespace(step_id="agenda-step"),
    )

    outputs = [
        item
        async for item in agent.chat(
            BatchInput(
                texts=[TextData(TextSource.INPUT, "Execute the step.", "User")],
                metadata=metadata,
            )
        )
    ]

    assert outputs
    assert memory.compact_calls == 0
    assert memory.user_messages == []


@pytest.mark.asyncio
async def test_normal_user_turn_still_compacts_and_records_memory():
    memory = _RecordingMemory()
    agent = BasicMemoryAgent(
        llm=_TextLLM(),
        system="base",
        use_mcpp=False,
        faster_first_response=False,
        tts_preprocessor_config=TTSPreprocessorConfig(
            remove_special_char=False,
            translator_config=TranslatorConfig(
                translate_audio=False,
                translate_provider="deeplx",
            ),
        ),
        memory_manager=memory,
    )

    outputs = [
        item
        async for item in agent.chat(
            BatchInput(
                texts=[TextData(TextSource.INPUT, "A real user turn.", "User")],
                metadata={"autonomous": True},
            )
        )
    ]

    assert outputs
    assert memory.compact_calls == 1
    assert memory.user_messages == ["A real user turn."]


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
