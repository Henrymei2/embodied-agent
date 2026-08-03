"""Part 7 slice 7.2: latency instrumentation on the live conversation path."""

from __future__ import annotations

from types import SimpleNamespace

from src.open_llm_vtuber.agent.output_types import (
    Actions,
    DisplayText,
    SentenceOutput,
)
from src.open_llm_vtuber.conversations.single_conversation import (
    process_single_conversation,
)
from src.open_llm_vtuber.latency import recorder, reset_recorder


class FakeAgent:
    async def chat(self, _batch_input):
        yield SentenceOutput(
            display_text=DisplayText("hello"),
            tts_text="hello",
            actions=Actions(),
        )


class ExplodingAgent:
    async def chat(self, _batch_input):
        raise RuntimeError("model unavailable")
        yield  # pragma: no cover


def _context(agent=None):
    return SimpleNamespace(
        agent_engine=agent or FakeAgent(),
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


async def _run(agent=None, metadata=None):
    async def send(_payload: str) -> None:
        return None

    return await process_single_conversation(
        context=_context(agent),
        websocket_send=send,
        client_uid="latency-client",
        user_input="hello there",
        metadata=metadata or {"report": False, "skip_history": True},
    )


async def test_a_real_turn_records_its_stages(monkeypatch):
    """The instrumentation is useless until it is actually on the path."""
    monkeypatch.setattr(
        "src.open_llm_vtuber.study.log_turn_finished", lambda _length: None
    )
    reset_recorder()
    result = await _run()
    assert result.success is True

    report = recorder().report()
    assert report.turns == 1
    measured = {stage.stage for stage in report.stages}
    # A silent turn runs the model but never synthesizes, so it reaches
    # planning and first output and stops there.
    assert {"asr", "plan", "first_token"} <= measured


async def test_a_failed_turn_still_closes_its_measurement(monkeypatch):
    """A turn that errored must not leave the recorder waiting forever."""
    monkeypatch.setattr(
        "src.open_llm_vtuber.study.log_turn_finished", lambda _length: None
    )
    reset_recorder()
    result = await _run(agent=ExplodingAgent())
    assert result.success is False

    report = recorder().report()
    assert report.turns == 1
    # It never spoke, so there is no end-to-end figure — not a zero.
    assert report.complete_turns == 0
    assert report.speech_to_first_audio_median_ms is None
    assert report.median_verdict == "unavailable"


async def test_measurement_failure_cannot_break_a_conversation(monkeypatch):
    """A broken recorder degrades the metric, never the reply."""
    monkeypatch.setattr(
        "src.open_llm_vtuber.study.log_turn_finished", lambda _length: None
    )
    reset_recorder()

    def explode(*_args, **_kwargs):
        raise RuntimeError("recorder is broken")

    monkeypatch.setattr(recorder(), "record", explode)
    result = await _run()
    assert result.success is True
    assert result.response == "hello"
