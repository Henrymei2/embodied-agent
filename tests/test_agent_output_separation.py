from __future__ import annotations

import pytest

from src.open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from src.open_llm_vtuber.agent.output_types import AgentTextOutput, SentenceOutput
from src.open_llm_vtuber.agent.transformers import tts_filter
from src.open_llm_vtuber.config_manager.tts_preprocessor import (
    TTSPreprocessorConfig,
    TranslatorConfig,
)
from src.open_llm_vtuber.workspace.workspace_manager import ensure_workspace


async def _collect_async(iterator):
    return [item async for item in iterator]


def _tts_preprocessor_config() -> TTSPreprocessorConfig:
    return TTSPreprocessorConfig(
        remove_special_char=False,
        translator_config=TranslatorConfig(
            translate_audio=False,
            translate_provider="deeplx",
        ),
    )


def test_workspace_rule_template_mentions_speak_tool(tmp_path):
    workspace = ensure_workspace(tmp_path / "workspace")
    rule_text = workspace.rule_file.read_text(encoding="utf-8")

    assert "## Agent Output Rules" in rule_text
    assert "reserved `speak` tool" in rule_text
    assert "Text-to-speech must only use text supplied through `speak`." in rule_text


def test_ensure_workspace_does_not_overwrite_existing_rule(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace = ensure_workspace(workspace_dir)
    workspace.rule_file.write_text("custom rules", encoding="utf-8")

    ensure_workspace(workspace_dir)

    assert workspace.rule_file.read_text(encoding="utf-8") == "custom rules"


def test_prompt_mode_tool_prompt_includes_builtin_speak():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent._mcp_prompt_string = ""

    prompt = agent._build_prompt_mode_system_prompt("base")

    assert "Server: builtin" in prompt
    assert "speak:" in prompt
    assert 'Use mcp_server "builtin"' in prompt


def test_system_prompt_requires_speak_for_user_facing_output():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent.interrupt_method = "system"

    agent.set_system("base")

    assert "## Built-in Speak Tool Requirement" in agent._system
    assert "you must call `speak`" in agent._system
    assert "will not be converted to speech" in agent._system


@pytest.mark.asyncio
async def test_speak_event_becomes_sentence_output_for_tts():
    @tts_filter(_tts_preprocessor_config())
    async def stream():
        yield {"type": "speak_text", "text": "Hello there."}

    outputs = await _collect_async(stream())

    assert len(outputs) == 1
    assert isinstance(outputs[0], SentenceOutput)
    assert outputs[0].display_text.text == "Hello there."
    assert outputs[0].tts_text == "Hello there."


@pytest.mark.asyncio
async def test_agent_text_event_stays_display_only():
    @tts_filter(_tts_preprocessor_config())
    async def stream():
        yield {"type": "agent_text", "content": "Working on it."}

    outputs = await _collect_async(stream())

    assert len(outputs) == 1
    assert isinstance(outputs[0], AgentTextOutput)
    assert outputs[0].display_text.text == "Working on it."
