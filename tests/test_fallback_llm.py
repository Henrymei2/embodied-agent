"""Demo resilience: the companion keeps answering when its model is unreachable."""

from __future__ import annotations

import json

import pytest

from src.open_llm_vtuber.agent.stateless_llm.fallback_llm import (
    STRICT_ENV,
    FallbackLLM,
    wrap_with_fallback,
)


class Dead:
    async def chat_completion(self, messages, system=None, tools=None):
        raise ConnectionError("model refused the connection")
        yield ""  # pragma: no cover


class Live:
    def __init__(self):
        self.saw_system = None
        self.saw_tools = None

    async def chat_completion(self, messages, system=None, tools=None):
        self.saw_system, self.saw_tools = system, tools
        for token in ("Real ", "model ", "answer."):
            yield token


class DiesMidStream:
    async def chat_completion(self, messages, system=None, tools=None):
        yield "The model started "
        raise ConnectionError("connection dropped")


async def _collect(llm, text="hello there"):
    return "".join(
        [t async for t in llm.chat_completion([{"role": "user", "content": text}])]
    )


async def test_a_working_model_is_never_replaced():
    """The audience must be seeing the system, not a recording."""
    primary = Live()
    llm = FallbackLLM(primary)
    assert (await _collect(llm)).strip() == "Real model answer."
    assert llm.degraded is False


async def test_system_prompt_and_tools_reach_the_real_model():
    primary = Live()
    llm = FallbackLLM(primary)
    async for _ in llm.chat_completion(
        [{"role": "user", "content": "hi"}], system="be brief", tools=[{"name": "t"}]
    ):
        pass
    assert primary.saw_system == "be brief"
    assert primary.saw_tools == [{"name": "t"}]


async def test_an_unreachable_model_still_answers_and_marks_itself_degraded():
    llm = FallbackLLM(Dead())
    reply = await _collect(llm, "read my notes file")
    assert reply.strip()
    assert llm.degraded is True


async def test_the_recorded_reply_matches_on_keyword(tmp_path):
    path = tmp_path / "replies.json"
    path.write_text(
        json.dumps(
            [{"keywords": ["calendar"], "reply": "The calendar is on the wall."}]
        ),
        encoding="utf-8",
    )
    llm = FallbackLLM(Dead(), replies_path=path)
    assert "calendar is on the wall" in await _collect(llm, "check my calendar")


async def test_an_unmatched_prompt_gets_an_honest_generic_reply(tmp_path):
    path = tmp_path / "replies.json"
    path.write_text(
        json.dumps([{"keywords": ["zzz"], "reply": "no"}]), encoding="utf-8"
    )
    llm = FallbackLLM(Dead(), replies_path=path)
    reply = await _collect(llm, "something entirely unrelated")
    assert "recorded" in reply.lower(), "the fallback must admit what it is"


async def test_a_missing_replies_file_still_answers(tmp_path):
    llm = FallbackLLM(Dead(), replies_path=tmp_path / "absent.json")
    assert "recorded" in (await _collect(llm)).lower()


async def test_a_mid_stream_failure_keeps_the_partial_answer():
    """Stitching a recording onto a real reply would be two voices in one turn."""
    llm = FallbackLLM(DiesMidStream())
    reply = await _collect(llm)
    assert reply.strip() == "The model started"
    assert llm.degraded is False, "the model did answer; this is not a fallback"


async def test_strict_mode_refuses_to_hide_a_broken_model(monkeypatch):
    """A rehearsal must be able to prove the real model is being used."""
    monkeypatch.setenv(STRICT_ENV, "1")
    llm = FallbackLLM(Dead())
    with pytest.raises(ConnectionError):
        await _collect(llm)


def test_strict_mode_removes_the_wrapper_entirely(monkeypatch):
    primary = Live()
    assert isinstance(wrap_with_fallback(primary), FallbackLLM)
    monkeypatch.setenv(STRICT_ENV, "1")
    assert wrap_with_fallback(primary) is primary


def test_the_shipped_demo_replies_are_valid_and_admit_what_they_are():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    entries = json.loads((root / "demo" / "fallback_replies.json").read_text("utf-8"))
    assert entries
    for entry in entries:
        assert isinstance(entry["keywords"], list) and entry["keywords"]
        assert isinstance(entry["reply"], str) and entry["reply"].strip()
    # At least the introductory lines must disclose that they are recorded, so
    # nobody demonstrates a canned answer believing it came from the agent.
    disclosing = [e for e in entries if "recorded" in e["reply"].lower()]
    assert len(disclosing) >= 3
