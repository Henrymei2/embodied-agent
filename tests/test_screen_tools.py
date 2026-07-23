from __future__ import annotations

import json

import pytest

from mcp_servers import screen_tools
from src.open_llm_vtuber.mcpp.tool_manager import ToolManager
from src.open_llm_vtuber.mcpp.types import FormattedTool
from src.open_llm_vtuber.stage_state_manager import get_default_stage, save_stage_state


async def test_tool_returns_current_activity(monkeypatch):
    monkeypatch.setattr(
        screen_tools,
        "_get_screen_context_sync",
        lambda: (
            True,
            {
                "available": True,
                "summary": "VS Code — file.py",
                "app": "VS Code",
                "title": "file.py",
                "idle_seconds": 5,
            },
            None,
        ),
    )

    result = json.loads(await screen_tools.get_screen_context())
    assert result["activity"] == "VS Code — file.py"
    assert result["app"] == "VS Code"


async def test_tool_reports_absence_plainly(monkeypatch):
    monkeypatch.setattr(
        screen_tools,
        "_get_screen_context_sync",
        lambda: (True, {"available": False}, None),
    )

    assert "No recent screen context" in await screen_tools.get_screen_context()


async def test_tool_surfaces_bridge_errors(monkeypatch):
    monkeypatch.setattr(
        screen_tools,
        "_get_screen_context_sync",
        lambda: (False, None, "could not reach the screen-context bridge: boom"),
    )

    assert (await screen_tools.get_screen_context()).startswith("Error: ")


def test_get_screen_context_is_gated_on_the_computer(monkeypatch, tmp_path):
    """The tool must be hidden and blocked unless the avatar is at the computer."""
    monkeypatch.chdir(tmp_path)
    stage = get_default_stage("character")
    assert save_stage_state("character", stage)[0]

    manager = ToolManager(
        initial_tools_dict={
            "get_screen_context": FormattedTool(
                input_schema={}, related_server="screen"
            ),
        },
        conf_uid="character",
    )
    assert manager.get_required_object("get_screen_context") == "computer"

    # Standing away from the computer: blocked before execution.
    allowed, reason = manager.authorize_tool("get_screen_context")
    assert allowed is False
    assert "computer" in reason

    # Move the avatar onto the computer: now allowed.
    nodes = {node["id"]: node for node in stage["nodes"]}
    nodes["avatar"]["x"] = nodes["computer"]["x"]
    nodes["avatar"]["y"] = nodes["computer"]["y"]
    assert save_stage_state("character", stage)[0]
    assert manager.authorize_tool("get_screen_context") == (True, None)


@pytest.mark.parametrize("payload", [None, "not-a-dict", 42])
async def test_tool_handles_unexpected_backend_payload(monkeypatch, payload):
    monkeypatch.setattr(
        screen_tools,
        "_get_screen_context_sync",
        lambda: (True, payload, None)
        if isinstance(payload, dict)
        else (True, {}, None),
    )
    # An empty/odd payload is treated as "no context available", never a crash.
    assert "No recent screen context" in await screen_tools.get_screen_context()
