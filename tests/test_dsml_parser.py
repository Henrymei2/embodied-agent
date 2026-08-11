from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.open_llm_vtuber.agent.stateless_llm.openai_compatible_llm import AsyncLLM
from src.open_llm_vtuber.mcpp.dsml_parser import (
    DSMLStreamParser,
    parse_dsml_tool_calls,
)


def test_streamed_deepseek_dsml_is_hidden_and_converted_to_tool_call():
    parser = DSMLStreamParser()
    chunks = [
        "I'll head over to the mailbox. One moment.<｜",
        '｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="move_to">\n',
        '<｜｜DSML｜｜parameter name="target" string="true">mail',
        "box</｜｜DSML｜｜parameter>\n</｜｜DSML｜｜invoke>\n",
        "</｜｜DSML｜｜tool_calls>",
    ]
    visible: list[str] = []
    calls = None
    for chunk in chunks:
        text, detected = parser.feed(chunk)
        visible.append(text)
        if detected is not None:
            calls = detected

    assert "".join(visible) == "I'll head over to the mailbox. One moment."
    assert calls is not None and len(calls) == 1
    assert calls[0].function.name == "move_to"
    assert json.loads(calls[0].function.arguments) == {"target": "mailbox"}


def test_dsml_parameters_preserve_declared_scalar_and_json_types():
    calls = parse_dsml_tool_calls(
        """
        <|DSML|tool_calls><|DSML|invoke name="example">
          <|DSML|parameter name="label" string="true">mailbox</|DSML|parameter>
          <|DSML|parameter name="count" number="true">2</|DSML|parameter>
          <|DSML|parameter name="enabled" boolean="true">true</|DSML|parameter>
          <|DSML|parameter name="options" json="true">{"unread": true}</|DSML|parameter>
        </|DSML|invoke></|DSML|tool_calls>
        """
    )

    assert len(calls) == 1
    assert json.loads(calls[0].function.arguments) == {
        "label": "mailbox",
        "count": 2,
        "enabled": True,
        "options": {"unread": True},
    }


def test_incomplete_dsml_is_suppressed_instead_of_leaking_to_tts():
    parser = DSMLStreamParser()
    visible, calls = parser.feed("Before.<｜DSML｜tool_calls><｜DSML｜invoke")
    tail, final_calls = parser.finish()

    assert visible == "Before."
    assert calls is None
    assert tail == ""
    assert final_calls is None


def test_multiple_dsml_invocations_are_converted_with_unique_ids():
    calls = parse_dsml_tool_calls(
        """
        <｜DSML｜tool_calls>
          <｜DSML｜invoke name='move_to'>
            <｜DSML｜parameter name='target'>todo_list</｜DSML｜parameter>
          </｜DSML｜invoke>
          <｜DSML｜invoke name='get_scene_status'></｜DSML｜invoke>
        </｜DSML｜tool_calls>
        """
    )

    assert [call.function.name for call in calls] == ["move_to", "get_scene_status"]
    assert len({call.id for call in calls}) == 2


def test_invalid_dsml_invocation_is_suppressed():
    calls = parse_dsml_tool_calls(
        "<|DSML|tool_calls><|DSML|invoke name='not a tool'>"
        "</|DSML|invoke></|DSML|tool_calls>"
    )

    assert calls == []


class _FakeCompletions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return _FakeStream(self.chunks)


class _FakeStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            values = next(self.chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
        content, tool_calls, *rest = values
        reasoning_content = rest[0] if rest else None
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=content,
                        tool_calls=tool_calls,
                        reasoning_content=reasoning_content,
                    )
                )
            ]
        )

    async def close(self):
        return None


def _fake_llm(chunks):
    llm = object.__new__(AsyncLLM)
    llm.base_url = "https://example.invalid/v1"
    llm.model = "deepseek-test"
    llm.temperature = 0.0
    llm.support_tools = True
    completions = _FakeCompletions(chunks)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    llm.completions = completions
    return llm


@pytest.mark.asyncio
async def test_deepseek_request_repairs_missing_reasoning_on_tool_history():
    llm = _fake_llm([])
    messages = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "done"},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "retained thought",
            "tool_calls": [
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
    ]

    assert [event async for event in llm.chat_completion(messages, tools=[])] == []

    outbound = llm.completions.requests[0]["messages"]
    assert outbound[1]["reasoning_content"] == ""
    assert outbound[3]["reasoning_content"] == "retained thought"
    assert "reasoning_content" not in messages[1]


@pytest.mark.asyncio
async def test_non_deepseek_request_does_not_inject_reasoning_content():
    llm = _fake_llm([])
    llm.base_url = "https://example.invalid/v1"
    llm.model = "ordinary-compatible-model"
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        }
    ]

    assert [event async for event in llm.chat_completion(messages, tools=[])] == []

    outbound = llm.completions.requests[0]["messages"]
    assert "reasoning_content" not in outbound[0]


@pytest.mark.asyncio
async def test_openai_compatible_stream_preserves_reasoning_for_tool_replay():
    native = SimpleNamespace(
        index=0,
        id="thinking-call",
        type="function",
        function=SimpleNamespace(name="get_scene_status", arguments="{}"),
    )
    llm = _fake_llm([(None, None, "first "), (None, [native], "second")])

    events = [
        event
        async for event in llm.chat_completion(
            [{"role": "user", "content": "status"}],
            tools=[{"type": "function", "function": {"name": "get_scene_status"}}],
        )
    ]

    reasoning = "".join(
        event["text"]
        for event in events
        if isinstance(event, dict) and event.get("type") == "reasoning_content"
    )
    assert reasoning == "first second"
    assert next(event for event in events if isinstance(event, list))[0].id == (
        "thinking-call"
    )


@pytest.mark.asyncio
async def test_openai_compatible_stream_converts_dsml_without_exposing_markup():
    llm = _fake_llm(
        [
            ("Going there.<｜DS", None),
            ("ML｜tool_calls><｜DSML｜invoke name='move_to'>", None),
            (
                "<｜DSML｜parameter name='target'>todo_list</｜DSML｜parameter>"
                "</｜DSML｜invoke></｜DSML｜tool_calls>",
                None,
            ),
        ]
    )
    events = [
        event
        async for event in llm.chat_completion(
            [{"role": "user", "content": "move"}],
            tools=[{"type": "function", "function": {"name": "move_to"}}],
        )
    ]

    visible = "".join(event for event in events if isinstance(event, str))
    calls = next(event for event in events if isinstance(event, list))
    assert visible == "Going there."
    assert "DSML" not in visible
    assert calls[0].function.name == "move_to"
    assert json.loads(calls[0].function.arguments) == {"target": "todo_list"}


@pytest.mark.asyncio
async def test_dsml_parser_is_disabled_without_available_tools():
    markup = "<｜DSML｜tool_calls>not actionable</｜DSML｜tool_calls>"
    llm = _fake_llm([(markup, None)])

    events = [
        event
        async for event in llm.chat_completion(
            [{"role": "user", "content": "quote this"}], tools=[]
        )
    ]

    assert events == [markup]


@pytest.mark.asyncio
async def test_native_tool_call_takes_precedence_over_text_compatibility_mode():
    native = SimpleNamespace(
        index=0,
        id="native-1",
        type="function",
        function=SimpleNamespace(name="get_scene_status", arguments="{}"),
    )
    llm = _fake_llm([("Before native.", None), (None, [native])])

    events = [
        event
        async for event in llm.chat_completion(
            [{"role": "user", "content": "status"}],
            tools=[{"type": "function", "function": {"name": "get_scene_status"}}],
        )
    ]

    assert (
        "".join(event for event in events if isinstance(event, str)) == "Before native."
    )
    calls = next(event for event in events if isinstance(event, list))
    assert calls[0].id == "native-1"
    assert calls[0].function.name == "get_scene_status"


@pytest.mark.asyncio
async def test_complete_dsml_is_discarded_if_native_call_arrives_later():
    dsml = (
        "<|DSML|tool_calls><|DSML|invoke name='move_to'>"
        "<|DSML|parameter name='target'>todo_list</|DSML|parameter>"
        "</|DSML|invoke></|DSML|tool_calls>"
    )
    native = SimpleNamespace(
        index=0,
        id="native-2",
        type="function",
        function=SimpleNamespace(name="get_scene_status", arguments="{}"),
    )
    llm = _fake_llm([(dsml, None), (None, [native])])

    events = [
        event
        async for event in llm.chat_completion(
            [{"role": "user", "content": "status"}],
            tools=[{"type": "function", "function": {"name": "get_scene_status"}}],
        )
    ]

    calls = next(event for event in events if isinstance(event, list))
    assert len(calls) == 1
    assert calls[0].id == "native-2"
