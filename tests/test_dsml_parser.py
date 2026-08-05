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

    async def create(self, **_kwargs):
        return _FakeStream(self.chunks)


class _FakeStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            content, tool_calls = next(self.chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=content, tool_calls=tool_calls)
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
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(chunks)))
    return llm


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

    assert "".join(event for event in events if isinstance(event, str)) == "Before native."
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
