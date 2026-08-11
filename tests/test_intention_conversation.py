import asyncio
import time
from types import SimpleNamespace

import pytest

from src.open_llm_vtuber.agent.stateless_llm.stateless_llm_interface import (
    StatelessLLMInterface,
)
from src.open_llm_vtuber.autonomy.agenda import AgendaStore, create_entry
from src.open_llm_vtuber.autonomy.executive import AgendaExecutive
from src.open_llm_vtuber.autonomy.intention_conversation import (
    COMPLETE_INTENTION_TOOL,
    IntentionConversationRunner,
    safe_tool_schemas,
)
from src.open_llm_vtuber.autonomy.settings import AutonomySettings
from src.open_llm_vtuber.mcpp.types import FormattedTool, ToolCallObject


def _entry(status="active"):
    return create_entry(
        kind="pursuit",
        title="Inspect the new message",
        description="Read the grounded message and handle it safely.",
        origin="autonomous",
        evidence_refs=["event:test"],
        completion_condition="The message is inspected and the outcome is known.",
        time_budget_seconds=300,
        status=status,
        required_capabilities=["search_emails"],
        report_policy="silent",
    )


def _schema(name):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


class _Tools:
    def __init__(self):
        names = ["search_emails", "archive_email", "send_email", "mystery_action"]
        self.tools = {
            name: FormattedTool(input_schema={}, related_server="gmail")
            for name in names
        }
        self._schemas = [_schema(name) for name in names]

    def get_tool(self, name):
        return self.tools.get(name)

    def get_required_object(self, _name):
        return None

    def get_reachable_objects(self):
        return []

    def get_formatted_tools(self, mode, _reachable=None):
        assert mode == "OpenAI"
        return list(self._schemas)


class _LLM(StatelessLLMInterface):
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def get_tool_mode(self):
        return "OpenAI"

    async def chat_completion(self, messages, system=None, tools=None):
        self.requests.append((messages, system, tools))
        for event in self.turns.pop(0):
            yield event


def _call(name, arguments="{}"):
    return ToolCallObject.from_dict(
        {
            "id": "provider-call",
            "type": "function",
            "index": 0,
            "function": {"name": name, "arguments": arguments},
        }
    )


def test_safe_tool_schemas_exclude_confirmation_and_unknown_tools():
    names = {
        schema["function"]["name"] for schema in safe_tool_schemas(_Tools(), "OpenAI")
    }
    assert names == {"search_emails", "archive_email", COMPLETE_INTENTION_TOOL}


@pytest.mark.asyncio
async def test_private_chain_keeps_tool_result_and_finishes_without_planner():
    llm = _LLM(
        [
            [
                {"type": "reasoning_content", "text": "private-thought"},
                [_call("search_emails")],
            ],
            ["The message needs no action."],
        ]
    )
    calls = []

    async def run_tool(entry_id, name, args, tool_id, mode):
        calls.append((entry_id, name, args, tool_id, mode))
        return {"role": "tool", "tool_call_id": tool_id, "content": "no matches"}

    runner = IntentionConversationRunner(
        llm=llm,
        tool_manager=_Tools(),
        run_tool=run_tool,
    )
    entry = _entry()
    result = await runner.run(entry)

    assert result.status == "completed"
    assert result.model_turns == 2
    assert result.tool_calls == 1
    assert calls[0][1] == "search_emails"
    assert llm.requests[1][0][-1]["role"] == "tool"
    assert llm.requests[1][0][-2]["reasoning_content"] == "private-thought"
    assert runner.has_session(entry.id)


@pytest.mark.asyncio
async def test_complete_intention_maps_no_action_to_completed():
    llm = _LLM(
        [
            [
                [
                    _call(
                        COMPLETE_INTENTION_TOOL,
                        '{"status":"no_action","summary":"Nothing changed."}',
                    )
                ]
            ]
        ]
    )

    async def unused(*_args):
        raise AssertionError("control tools must not reach ToolExecutor")

    result = await IntentionConversationRunner(
        llm=llm,
        tool_manager=_Tools(),
        run_tool=unused,
    ).run(_entry())

    assert result.status == "completed"
    assert result.outcome == "no_action"
    assert result.summary == "Nothing changed."


@pytest.mark.asyncio
async def test_hallucinated_unsafe_tool_never_reaches_executor():
    llm = _LLM([[[_call("send_email")]], ["I did not send anything."]])
    executed = []

    async def run_tool(*args):
        executed.append(args)
        raise AssertionError("unsafe tools must be rejected before ToolExecutor")

    result = await IntentionConversationRunner(
        llm=llm,
        tool_manager=_Tools(),
        run_tool=run_tool,
    ).run(_entry())

    assert result.status == "completed"
    assert executed == []
    assert llm.requests[1][0][-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_model_timeout_does_not_wait_for_provider_cancellation():
    release = asyncio.Event()

    class CancellationIgnoringLLM(StatelessLLMInterface):
        def get_tool_mode(self):
            return "OpenAI"

        async def chat_completion(self, *_args, **_kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
            if False:
                yield "unreachable"

    async def unused(*_args):
        raise AssertionError

    runner = IntentionConversationRunner(
        llm=CancellationIgnoringLLM(),
        tool_manager=_Tools(),
        run_tool=unused,
        model_timeout_seconds=0.01,
    )
    started = time.perf_counter()
    with pytest.raises(asyncio.TimeoutError):
        await runner.run(_entry())
    elapsed = time.perf_counter() - started
    release.set()
    await asyncio.sleep(0)

    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_structured_model_error_is_not_mistaken_for_completion():
    llm = _LLM([[{"type": "error", "message": "The provider rejected the request."}]])

    async def unused(*_args):
        raise AssertionError

    runner = IntentionConversationRunner(
        llm=llm,
        tool_manager=_Tools(),
        run_tool=unused,
    )
    with pytest.raises(RuntimeError, match="provider rejected"):
        await runner.run(_entry())


@pytest.mark.asyncio
async def test_executive_chain_path_never_calls_step_or_completion_planner(tmp_path):
    class ForbiddenPlanner:
        async def next_step(self, *_args):
            raise AssertionError("next-step planner must not run")

        async def evaluate_completion(self, *_args):
            raise AssertionError("completion planner must not run")

    result = SimpleNamespace(
        status="completed",
        summary="Finished in one chain.",
        outcome="completed",
        session_id="session-1",
        model_turns=2,
        tool_calls=1,
        model_seconds=0.1,
        tool_seconds=0.2,
        queue_wait_seconds=0.0,
        execution_seconds=0.3,
        relocation_seconds=0.0,
    )

    async def run_intention(_entry):
        return result

    async def unused_step(*_args):
        raise AssertionError("legacy activity execution must not run")

    async def emit(_payload):
        return None

    store = AgendaStore(tmp_path)
    entry = _entry()
    store.add(entry)
    executive = AgendaExecutive(
        client_uid="client",
        store=store,
        planner=ForbiddenPlanner(),
        settings=AutonomySettings(),
        run_step=unused_step,
        run_intention=run_intention,
        emit=emit,
    )

    assert await executive._advance_entry(entry) is True
    current = next(item for item in store.load().entries if item.id == entry.id)
    assert current.status == "completed"
    assert current.progress_summary == "Finished in one chain."


@pytest.mark.asyncio
async def test_late_chain_result_cannot_revive_terminal_entry(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    discarded = []

    async def run_intention(_entry):
        entered.set()
        await release.wait()
        return SimpleNamespace(
            status="completed",
            summary="Late result.",
            outcome="completed",
            session_id="late-session",
            model_turns=1,
            tool_calls=0,
            model_seconds=0.1,
            tool_seconds=0.0,
            queue_wait_seconds=0.0,
            execution_seconds=0.1,
            relocation_seconds=0.0,
        )

    async def emit(_payload):
        return None

    store = AgendaStore(tmp_path)
    entry = _entry()
    store.add(entry)
    executive = AgendaExecutive(
        client_uid="client",
        store=store,
        planner=SimpleNamespace(),
        settings=AutonomySettings(),
        run_step=lambda *_args: None,
        run_intention=run_intention,
        discard_intention=discarded.append,
        emit=emit,
    )
    task = asyncio.create_task(executive._advance_entry(entry))
    await entered.wait()
    store.transition(entry.id, "abandoned", progress_summary="Scenario stopped.")
    release.set()

    assert await task is True
    current = next(item for item in store.load().entries if item.id == entry.id)
    assert current.status == "abandoned"
    assert current.progress_summary == "Scenario stopped."
    assert discarded == [entry.id]
