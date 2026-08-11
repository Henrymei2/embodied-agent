from __future__ import annotations

import asyncio

import pytest

from src.open_llm_vtuber.agent.stateless_llm.stateless_llm_interface import (
    StatelessLLMInterface,
)
from src.open_llm_vtuber.memory.llm_adapter import make_llm_complete


class _StreamingLLM(StatelessLLMInterface):
    def __init__(self, events):
        self.events = list(events)

    async def chat_completion(self, _messages, system=None, tools=None):
        for event in self.events:
            yield event


@pytest.mark.asyncio
async def test_completion_ignores_reasoning_and_keeps_visible_text():
    complete = make_llm_complete(
        _StreamingLLM(
            [
                {"type": "reasoning_content", "text": "consider {invalid}"},
                {"type": "text_delta", "text": '{"kind":'},
                '"idle"}',
            ]
        ),
        raise_errors=True,
    )

    assert await complete([{"role": "user", "content": "plan"}]) == ('{"kind":"idle"}')


@pytest.mark.asyncio
async def test_strict_completion_propagates_structured_provider_error():
    complete = make_llm_complete(
        _StreamingLLM([{"type": "error", "message": "provider rejected request"}]),
        raise_errors=True,
    )

    with pytest.raises(RuntimeError, match="provider rejected request"):
        await complete([{"role": "user", "content": "plan"}])


@pytest.mark.asyncio
async def test_default_completion_preserves_memory_error_fallback():
    complete = make_llm_complete(
        _StreamingLLM([{"type": "error", "message": "provider rejected request"}])
    )

    assert await complete([{"role": "user", "content": "summarize"}]) == ""


@pytest.mark.asyncio
async def test_json_completion_returns_before_provider_stream_finishes():
    class HangingAfterJson(StatelessLLMInterface):
        async def chat_completion(self, _messages, system=None, tools=None):
            yield {"type": "reasoning_content", "text": "hidden"}
            yield '{"kind":"idle","metadata":'
            yield '{"reason":"done"}}'
            await asyncio.Event().wait()

    complete = make_llm_complete(
        HangingAfterJson(),
        raise_errors=True,
        json_object=True,
    )

    result = await asyncio.wait_for(
        complete([{"role": "user", "content": "plan"}]),
        timeout=0.1,
    )

    assert result == '{"kind":"idle","metadata":{"reason":"done"}}'
