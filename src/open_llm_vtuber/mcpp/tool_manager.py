from typing import Any, Dict, List, Literal, Optional

from loguru import logger

from .types import FormattedTool

# Tools that require the agent to be physically close to a specific object.
# Key = tool name, value = object label the agent must be near.
PROXIMITY_REQUIREMENTS: Dict[str, str] = {
    "execute_bash": "computer",
    "read_file": "computer",
    "write_file": "computer",
    "append_to_file": "computer",
    "edit_file": "computer",
    "list_directory": "computer",
    "create_directory": "computer",
    "delete_path": "computer",
    "move_path": "computer",
    "list_calendar_events": "calendar",
    "add_calendar_event": "calendar",
    "update_calendar_event": "calendar",
    "remove_calendar_event": "calendar",
}

PROXIMITY_THRESHOLD = 150.0


class ToolManager:
    """Manage tool schemas and enforce spatial availability."""

    def __init__(
        self,
        formatted_tools_openai: List[Dict[str, Any]] = None,
        formatted_tools_claude: List[Dict[str, Any]] = None,
        initial_tools_dict: Dict[str, FormattedTool] = None,
        conf_uid: str | None = None,
    ) -> None:
        self.tools: Dict[str, FormattedTool] = initial_tools_dict or {}
        self.conf_uid = conf_uid
        self._formatted_tools_openai: List[Dict[str, Any]] = (
            formatted_tools_openai or []
        )
        self._formatted_tools_claude: List[Dict[str, Any]] = (
            formatted_tools_claude or []
        )
        logger.info(
            f"ToolManager initialized with {len(self._formatted_tools_openai)} "
            f"OpenAI tools and {len(self._formatted_tools_claude)} Claude tools."
        )
        logger.info(f"ToolManager available tool names: {sorted(self.tools.keys())}")

    def get_tool(self, tool_name: str) -> FormattedTool | None:
        """Return a tool's raw schema and server association."""
        tool = self.tools.get(tool_name)
        if isinstance(tool, FormattedTool):
            return tool
        logger.warning(
            f"TM: Raw tool info for '{tool_name}' not found "
            "(was initial_tools_dict provided?)."
        )
        return None

    def get_reachable_objects(self) -> List[str]:
        """Resolve reachability from this session's scene, failing closed."""
        if not self.conf_uid:
            logger.warning(
                "TM: Missing character config for proximity checks; "
                "object-gated tools will remain blocked."
            )
            return []
        try:
            from ..stage_state_manager import get_reachable_objects

            return get_reachable_objects(
                self.conf_uid,
                distance=PROXIMITY_THRESHOLD,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"TM: Could not resolve scene proximity; failing closed: {exc}"
            )
            return []

    def get_formatted_tools(
        self,
        mode: Literal["OpenAI", "Claude"],
        reachable_objects: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Return schemas filtered by current agent-to-object proximity."""
        if mode == "OpenAI":
            all_tools = self._formatted_tools_openai
        elif mode == "Claude":
            all_tools = self._formatted_tools_claude
        else:
            return []

        if reachable_objects is None:
            reachable_objects = self.get_reachable_objects()

        filtered = []
        blocked = []
        for tool in all_tools:
            if mode == "OpenAI":
                tool_name = tool.get("function", {}).get("name", "")
            else:
                tool_name = tool.get("name", "")

            required_object = PROXIMITY_REQUIREMENTS.get(tool_name)
            if required_object is None or required_object in reachable_objects:
                filtered.append(tool)
            else:
                blocked.append(tool_name)

        if blocked:
            logger.info(
                f"TM: Blocked {len(blocked)} tool(s) due to proximity "
                f"requirements: {blocked}"
            )
        return filtered

    def authorize_tool(self, tool_name: str) -> tuple[bool, str | None]:
        """Enforce an object gate immediately before tool execution."""
        required_object = PROXIMITY_REQUIREMENTS.get(tool_name)
        if required_object is None:
            return True, None
        if required_object in self.get_reachable_objects():
            return True, None
        return (
            False,
            f"Tool '{tool_name}' requires the agent to be near the {required_object}.",
        )
