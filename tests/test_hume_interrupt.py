from __future__ import annotations

import asyncio

from src.open_llm_vtuber.agent.agents.hume_ai import HumeAIAgent


class FakeWebSocket:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def _bare_agent(tmp_path):
    agent = object.__new__(HumeAIAgent)
    agent._ws = FakeWebSocket()
    agent._connected = True
    agent._current_text = "partial response"
    agent._current_id = "response-id"
    agent._idle_timer = None
    agent._interrupted = False
    agent.cache_dir = tmp_path
    return agent


async def test_hume_interrupt_closes_stream_and_clears_partial_response(tmp_path):
    agent = _bare_agent(tmp_path)
    websocket = agent._ws

    agent.handle_interrupt("heard so far")
    await asyncio.sleep(0)

    assert agent._interrupted is True
    assert agent._connected is False
    assert agent._ws is None
    assert agent._current_text is None
    assert agent._current_id is None
    assert websocket.closed is True


async def test_hume_close_removes_cached_audio(tmp_path):
    agent = _bare_agent(tmp_path)
    cached = tmp_path / "evi_audio_test.wav"
    cached.write_bytes(b"audio")

    await agent.close()

    assert agent._connected is False
    assert cached.exists() is False
