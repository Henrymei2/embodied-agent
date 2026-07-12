"""Tests for embodied-action recording into contextual memory.

The scope treats "what the agent was doing in the embodied environment" as
first-class contextual memory, so scene/navigation tool calls must be captured
as actions. These tests exercise the agent's description + recording helpers in
isolation (via object.__new__ to skip the heavy __init__) and confirm the
actions land in a real MemoryManager's context buffer.
"""

from __future__ import annotations

from src.open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from src.open_llm_vtuber.mcpp.types import ToolCallObject, ToolCallFunctionObject
from src.open_llm_vtuber.memory import MemoryManager


def _bare_agent(memory_manager=None) -> BasicMemoryAgent:
    agent = object.__new__(BasicMemoryAgent)
    agent._memory_manager = memory_manager
    return agent


def test_describe_move_to_dict_claude_shape():
    agent = _bare_agent()
    assert (
        agent._describe_tool_action({"name": "move_to", "input": {"target": "calendar"}})
        == "moved to calendar"
    )


def test_describe_scene_tools():
    agent = _bare_agent()
    assert (
        agent._describe_tool_action({"name": "get_scene_status", "input": {}})
        == "checked its own position in the scene"
    )
    assert (
        agent._describe_tool_action({"name": "list_objects", "input": {}})
        == "looked around at nearby objects"
    )
    assert (
        agent._describe_tool_action(
            {"name": "get_object_position", "input": {"name": "bed"}}
        )
        == "checked where bed is"
    )


def test_describe_openai_toolcallobject_shape():
    agent = _bare_agent()
    call = ToolCallObject(
        id="1",
        function=ToolCallFunctionObject(
            name="move_to", arguments='{"target": "computer"}'
        ),
    )
    assert agent._describe_tool_action(call) == "moved to computer"


def test_speak_tool_is_not_recorded_as_action():
    agent = _bare_agent()
    assert agent._describe_tool_action({"name": "speak", "input": {"text": "hi"}}) is None


def test_generic_tool_gets_fallback_description():
    agent = _bare_agent()
    assert (
        agent._describe_tool_action({"name": "read_file", "input": {"path": "a.txt"}})
        == "used the read_file tool"
    )


def test_record_tool_actions_writes_into_memory(tmp_path):
    mm = MemoryManager(workspace_dir=tmp_path / "ws")
    agent = _bare_agent(mm)

    agent._record_tool_actions(
        [
            {"name": "move_to", "input": {"target": "calendar"}},
            {"name": "get_scene_status", "input": {}},
            {"name": "speak", "input": {"text": "hello"}},  # ignored
        ]
    )

    rendered = mm.render_context()
    assert "[action] moved to calendar" in rendered
    assert "[action] checked its own position in the scene" in rendered
    assert "hello" not in rendered  # speak is not an embodied action


def test_record_tool_actions_no_memory_is_safe():
    agent = _bare_agent(memory_manager=None)
    # Should be a no-op, not raise.
    agent._record_tool_actions([{"name": "move_to", "input": {"target": "bed"}}])
