"""Transient activity messages shown above the visual agent."""

from typing import Any


def tool_batch_activity(tool_calls: list[Any]) -> dict[str, Any]:
    """Build an activity event without exposing tool inputs."""
    tools = []
    for index, call in enumerate(tool_calls):
        if isinstance(call, dict):
            tool_id = call.get("id")
            tool_name = call.get("name") or call.get("function", {}).get("name")
        else:
            tool_id = getattr(call, "id", None)
            function = getattr(call, "function", None)
            tool_name = getattr(function, "name", None)

        tools.append(
            {
                "tool_id": tool_id or f"tool_{index}",
                "tool_name": tool_name or "unknown_task",
                "status": "pending",
            }
        )

    return {"type": "agent_activity", "phase": "tools", "tools": tools}


class AgentActivityTracker:
    """Track one conversation's tool batch for WebSocket activity updates."""

    def __init__(self) -> None:
        self._tools: list[dict[str, str]] = []

    @staticmethod
    def thinking() -> dict[str, Any]:
        return {"type": "agent_activity", "phase": "thinking", "tools": []}

    @staticmethod
    def planning() -> dict[str, Any]:
        return {"type": "agent_activity", "phase": "planning", "tools": []}

    @staticmethod
    def idle() -> dict[str, Any]:
        return {"type": "agent_activity", "phase": "idle", "tools": []}

    def replace_tools(self, tools: list[dict[str, Any]]) -> dict[str, Any]:
        self._tools = [
            {
                "tool_id": str(tool.get("tool_id") or f"tool_{index}"),
                "tool_name": str(tool.get("tool_name") or "unknown_task"),
                "status": str(tool.get("status") or "pending"),
            }
            for index, tool in enumerate(tools)
        ]
        return self.current_tools()

    def update_tool(self, update: dict[str, Any]) -> dict[str, Any]:
        tool_id = str(update.get("tool_id") or "unknown_tool")
        existing = next(
            (tool for tool in self._tools if tool["tool_id"] == tool_id), None
        )
        if existing is None:
            existing = {
                "tool_id": tool_id,
                "tool_name": str(update.get("tool_name") or "unknown_task"),
                "status": "pending",
            }
            self._tools.append(existing)
        existing["tool_name"] = str(update.get("tool_name") or existing["tool_name"])
        existing["status"] = str(update.get("status") or existing["status"])
        return self.current_tools()

    def current_tools(self) -> dict[str, Any]:
        return {
            "type": "agent_activity",
            "phase": "tools",
            "tools": [tool.copy() for tool in self._tools],
        }
