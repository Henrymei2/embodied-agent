from __future__ import annotations

from src.open_llm_vtuber.mcpp.tool_executor import ToolExecutor
from src.open_llm_vtuber.mcpp.tool_manager import ToolManager
from src.open_llm_vtuber.mcpp.types import FormattedTool
from src.open_llm_vtuber.stage_state_manager import (
    get_default_stage,
    get_reachable_objects,
    save_stage_state,
)


def _openai_tool(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _tool_names(tools: list[dict]) -> list[str]:
    return [tool["function"]["name"] for tool in tools]


def _move_avatar(stage: dict, target_id: str) -> None:
    nodes = {node["id"]: node for node in stage["nodes"]}
    nodes["avatar"]["x"] = nodes[target_id]["x"]
    nodes["avatar"]["y"] = nodes[target_id]["y"]


def test_reachable_objects_uses_character_specific_persisted_scene(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    stage = get_default_stage("character-a")
    _move_avatar(stage, "computer")
    assert save_stage_state("character-a", stage)[0]

    assert "computer" in get_reachable_objects("character-a")
    assert "computer" not in get_reachable_objects("character-b")


def test_tool_schema_filter_fails_closed_without_scene_identity():
    manager = ToolManager(
        formatted_tools_openai=[
            _openai_tool("move_to"),
            _openai_tool("read_file"),
        ]
    )

    assert _tool_names(manager.get_formatted_tools("OpenAI")) == ["move_to"]


def test_tool_schema_filter_updates_after_avatar_moves(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    stage = get_default_stage("character")
    assert save_stage_state("character", stage)[0]
    manager = ToolManager(
        formatted_tools_openai=[
            _openai_tool("move_to"),
            _openai_tool("read_file"),
            _openai_tool("download_file"),
        ],
        conf_uid="character",
    )

    assert _tool_names(manager.get_formatted_tools("OpenAI")) == ["move_to"]

    _move_avatar(stage, "computer")
    assert save_stage_state("character", stage)[0]
    assert _tool_names(manager.get_formatted_tools("OpenAI")) == [
        "move_to",
        "read_file",
        "download_file",
    ]


def test_all_playwright_tool_schemas_require_computer_proximity(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    stage = get_default_stage("character")
    assert save_stage_state("character", stage)[0]
    manager = ToolManager(
        formatted_tools_openai=[
            _openai_tool("move_to"),
            _openai_tool("browser_navigate"),
            _openai_tool("browser_snapshot"),
        ],
        initial_tools_dict={
            name: FormattedTool(input_schema={}, related_server="playwright")
            for name in ("browser_navigate", "browser_snapshot")
        },
        conf_uid="character",
    )

    assert _tool_names(manager.get_formatted_tools("OpenAI")) == ["move_to"]

    _move_avatar(stage, "computer")
    assert save_stage_state("character", stage)[0]
    assert _tool_names(manager.get_formatted_tools("OpenAI")) == [
        "move_to",
        "browser_navigate",
        "browser_snapshot",
    ]


def test_tool_access_prompt_lists_all_tools_and_locations(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    stage = get_default_stage("character")
    assert save_stage_state("character", stage)[0]
    manager = ToolManager(
        initial_tools_dict={
            "move_to": FormattedTool(
                input_schema={},
                related_server="scene",
                description="Move the avatar to an object in the scene.",
            ),
            "browser_navigate": FormattedTool(
                input_schema={},
                related_server="playwright",
                description="Open a web page in the browser.",
            ),
            "list_calendar_events": FormattedTool(
                input_schema={},
                related_server="calendar",
                description="List events from the local calendar.",
            ),
        },
        conf_uid="character",
    )

    prompt = manager.build_tool_access_prompt()

    assert "`move_to` (scene): Move the avatar" in prompt
    assert "Access: anywhere; available now" in prompt
    assert "`browser_navigate` (playwright): Open a web page" in prompt
    assert "Access: near computer; currently unavailable" in prompt
    assert "`list_calendar_events` (calendar): List events" in prompt
    assert "Access: near calendar;" in prompt

    _move_avatar(stage, "computer")
    assert save_stage_state("character", stage)[0]
    assert "Access: near computer; available now" in (
        manager.build_tool_access_prompt()
    )


class FakeMCPClient:
    def __init__(self):
        self.calls = []

    async def call_tool(self, server_name, tool_name, tool_args):
        self.calls.append((server_name, tool_name, tool_args))
        return {
            "metadata": {},
            "content_items": [{"type": "text", "text": "ok"}],
        }


async def test_execution_gate_cannot_be_bypassed_by_stale_tool_call(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    stage = get_default_stage("character")
    assert save_stage_state("character", stage)[0]
    manager = ToolManager(
        initial_tools_dict={
            "read_file": FormattedTool(
                input_schema={},
                related_server="bash",
            )
        },
        conf_uid="character",
    )
    client = FakeMCPClient()
    executor = ToolExecutor(client, manager)

    is_error, text, _metadata, _content = await executor.run_single_tool(
        "read_file",
        "tool-1",
        {"path": "notes.txt"},
    )

    assert is_error is True
    assert "requires the agent to be near the computer" in text
    assert client.calls == []

    _move_avatar(stage, "computer")
    assert save_stage_state("character", stage)[0]
    is_error, text, _metadata, _content = await executor.run_single_tool(
        "read_file",
        "tool-2",
        {"path": "notes.txt"},
    )

    assert (is_error, text) == (False, "ok")
    assert client.calls == [("bash", "read_file", {"path": "notes.txt"})]


async def test_download_execution_requires_computer_proximity(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    stage = get_default_stage("character")
    assert save_stage_state("character", stage)[0]
    manager = ToolManager(
        initial_tools_dict={
            "download_file": FormattedTool(
                input_schema={},
                related_server="bash",
            )
        },
        conf_uid="character",
    )
    client = FakeMCPClient()
    executor = ToolExecutor(client, manager)
    arguments = {"url": "https://example.com/file.pdf"}

    is_error, text, _metadata, _content = await executor.run_single_tool(
        "download_file", "tool-1", arguments
    )
    assert is_error is True
    assert "requires the agent to be near the computer" in text
    assert client.calls == []

    _move_avatar(stage, "computer")
    assert save_stage_state("character", stage)[0]
    is_error, text, _metadata, _content = await executor.run_single_tool(
        "download_file", "tool-2", arguments
    )
    assert (is_error, text) == (False, "ok")
    assert client.calls == [("bash", "download_file", arguments)]


async def test_playwright_execution_gate_cannot_be_bypassed(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    stage = get_default_stage("character")
    assert save_stage_state("character", stage)[0]
    manager = ToolManager(
        initial_tools_dict={
            "browser_navigate": FormattedTool(
                input_schema={},
                related_server="playwright",
            )
        },
        conf_uid="character",
    )
    client = FakeMCPClient()
    executor = ToolExecutor(client, manager)

    is_error, text, _metadata, _content = await executor.run_single_tool(
        "browser_navigate",
        "tool-1",
        {"url": "https://example.com"},
    )

    assert is_error is True
    assert "requires the agent to be near the computer" in text
    assert client.calls == []

    _move_avatar(stage, "computer")
    assert save_stage_state("character", stage)[0]
    is_error, text, _metadata, _content = await executor.run_single_tool(
        "browser_navigate",
        "tool-2",
        {"url": "https://example.com"},
    )

    assert (is_error, text) == (False, "ok")
    assert client.calls == [
        ("playwright", "browser_navigate", {"url": "https://example.com"})
    ]
