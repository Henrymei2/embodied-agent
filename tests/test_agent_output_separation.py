from __future__ import annotations

import pytest

from src.open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from src.open_llm_vtuber.agent.output_types import SentenceOutput
from src.open_llm_vtuber.agent.stateless_llm.stateless_llm_interface import (
    StatelessLLMInterface,
)
from src.open_llm_vtuber.agent.transformers import (
    actions_extractor,
    display_processor,
    sentence_divider,
    tts_filter,
)
from src.open_llm_vtuber.config_manager.tts_preprocessor import (
    TTSPreprocessorConfig,
    TranslatorConfig,
)
from src.open_llm_vtuber.mcpp.tool_manager import ToolManager
from src.open_llm_vtuber.mcpp.types import (
    FormattedTool,
    ToolCallFunctionObject,
    ToolCallObject,
)
from src.open_llm_vtuber.workspace.workspace_manager import (
    LEGACY_SPEAK_OUTPUT_RULES,
    TTS_OUTPUT_RULES,
    ensure_workspace,
)


async def _collect_async(iterator):
    return [item async for item in iterator]


class _DummyLLM(StatelessLLMInterface):
    async def chat_completion(self, messages, system=None, tools=None):
        if False:
            yield ""


class _ToolThenTextLLM:
    def __init__(self):
        self.calls = 0

    async def chat_completion(self, messages, system=None, tools=None):
        self.calls += 1
        if self.calls == 1:
            yield [
                ToolCallObject(
                    id="call-1",
                    function=ToolCallFunctionObject(
                        name="lookup", arguments='{"query": "weather"}'
                    ),
                )
            ]
        else:
            yield "It is sunny."


class _FakeToolExecutor:
    async def execute_tools(self, tool_calls, caller_mode):
        yield {
            "type": "final_tool_results",
            "results": [
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "sunny",
                }
            ],
        }


def _tts_preprocessor_config() -> TTSPreprocessorConfig:
    return TTSPreprocessorConfig(
        remove_special_char=False,
        translator_config=TranslatorConfig(
            translate_audio=False,
            translate_provider="deeplx",
        ),
    )


def test_workspace_rule_template_has_tts_guidance(tmp_path):
    workspace = ensure_workspace(tmp_path / "workspace")
    rule_text = workspace.rule_file.read_text(encoding="utf-8")

    assert TTS_OUTPUT_RULES in rule_text
    assert "reserved `speak` tool" not in rule_text
    assert "Always answer the user's request completely." in rule_text


def test_legacy_workspace_rule_is_migrated_without_losing_custom_content(tmp_path):
    workspace = ensure_workspace(tmp_path / "workspace")
    legacy_text = f"# Custom prefix\n\n{LEGACY_SPEAK_OUTPUT_RULES}\n\n# Custom suffix\n"
    workspace.rule_file.write_text(legacy_text, encoding="utf-8")

    ensure_workspace(workspace.root)
    migrated_once = workspace.rule_file.read_text(encoding="utf-8")
    ensure_workspace(workspace.root)

    assert "# Custom prefix" in migrated_once
    assert "# Custom suffix" in migrated_once
    assert LEGACY_SPEAK_OUTPUT_RULES not in migrated_once
    assert TTS_OUTPUT_RULES in migrated_once
    assert workspace.rule_file.read_text(encoding="utf-8") == migrated_once


def test_modified_workspace_rule_is_not_overwritten(tmp_path):
    workspace = ensure_workspace(tmp_path / "workspace")
    workspace.rule_file.write_text("custom rules", encoding="utf-8")

    ensure_workspace(workspace.root)

    assert workspace.rule_file.read_text(encoding="utf-8") == "custom rules"


def test_prompt_mode_uses_only_configured_mcp_tools():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent._mcp_prompt_string = "Server: external\n    Tools:\n        lookup:"

    prompt = agent._build_prompt_mode_system_prompt("base")

    assert "Server: external" in prompt
    assert "lookup:" in prompt
    assert "Server: builtin" not in prompt
    assert "speak:" not in prompt


def test_agent_tool_catalogs_are_not_augmented_with_speak():
    manager = ToolManager(
        formatted_tools_openai=[
            {"type": "function", "function": {"name": "lookup", "parameters": {}}}
        ],
        formatted_tools_claude=[{"name": "lookup", "input_schema": {"type": "object"}}],
    )

    agent = BasicMemoryAgent(
        llm=_DummyLLM(),
        system="base",
        tool_manager=manager,
    )

    assert [tool["function"]["name"] for tool in agent._formatted_tools_openai] == [
        "lookup"
    ]
    assert [tool["name"] for tool in agent._formatted_tools_claude] == ["lookup"]


def test_system_prompt_has_concise_but_complete_tts_guidance():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent.interrupt_method = "system"

    agent.set_system("base")

    assert "## Text-to-Speech Output Guidance" in agent._system
    assert "Keep responses concise, conversational" in agent._system
    assert "Always answer the user's request completely" in agent._system
    assert "built-in tool named `speak`" not in agent._system


def test_system_prompt_advertises_tools_hidden_by_location(monkeypatch):
    manager = ToolManager(
        initial_tools_dict={
            "browser_navigate": FormattedTool(
                input_schema={},
                related_server="playwright",
                description="Open a web page.",
            )
        },
        conf_uid="character",
    )
    monkeypatch.setattr(manager, "get_reachable_objects", lambda: [])
    agent = BasicMemoryAgent(
        llm=_DummyLLM(),
        system="base",
        tool_manager=manager,
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.agent.agents.basic_memory_agent.build_workspace_prompt_context",
        lambda **_kwargs: "",
    )

    prompt = agent._build_system_prompt_with_workspace_context([], agent._system)

    assert "Additional configured tools may be hidden" in prompt
    assert "`browser_navigate` (playwright): Open a web page." in prompt
    assert "Access: near computer; currently unavailable" in prompt


@pytest.mark.asyncio
async def test_normal_assistant_text_becomes_sentence_output_for_tts():
    @tts_filter(_tts_preprocessor_config())
    @display_processor()
    @actions_extractor()
    @sentence_divider(faster_first_response=False)
    async def stream():
        yield "Hello there."

    outputs = await _collect_async(stream())

    assert len(outputs) == 1
    assert isinstance(outputs[0], SentenceOutput)
    assert outputs[0].display_text.text == "Hello there."
    assert outputs[0].tts_text == "Hello there."


@pytest.mark.asyncio
async def test_openai_tool_loop_continues_to_normal_text_after_tool_result():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent._llm = _ToolThenTextLLM()
    agent._tool_executor = _FakeToolExecutor()
    agent._active_system = None
    agent._system = "base"
    agent._memory = []
    agent._memory_manager = None
    agent.prompt_mode_flag = False
    agent._build_system_prompt_with_workspace_context = lambda messages, system: system

    outputs = await _collect_async(
        agent._openai_tool_interaction_loop(
            initial_messages=[{"role": "user", "content": "Weather?"}],
            tools=[{"type": "function", "function": {"name": "lookup"}}],
        )
    )

    assert outputs == ["It is sunny."]
    assert agent._llm.calls == 2
