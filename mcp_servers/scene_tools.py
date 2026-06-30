"""Scene / spatial MCP server for the embodied agent.

Exposes tools that let the agent query and navigate the 2D stage defined in
`stage_state_manager.py` (bed, computer, calendar, avatar/agent).

get_object_position, list_objects, and get_scene_status read directly from
the persisted stage JSON (no live frontend round-trip needed).

move_to is different: it has to ask the running frontend to actually animate
the avatar and report back when done, so it goes through the live WebSocket
channel (see websocket_handler.py / frontend_bridge.py for the request/
response plumbing).

Run standalone for a quick sanity check:
    uv run python mcp_servers/scene_tools.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.stage_state_manager import load_stage_state
from src.open_llm_vtuber.frontend_bridge import request_move_to

# TODO: replace with however conf_uid actually gets resolved for the current
# session once that's wired up end-to-end. Hardcoded to the default character
# for now so this server is testable standalone.
_DEFAULT_CONF_UID = "mao_pro_001"

mcp = FastMCP("scene")


def _get_nodes(conf_uid: str) -> list[dict[str, Any]]:
    stage = load_stage_state(conf_uid)
    return stage.get("nodes", [])


def _find_node(nodes: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    for node in nodes:
        if node.get("id") == label or node.get("label") == label:
            return node
    return None


@mcp.tool()
def get_object_position(name: str) -> str:
    """Get the (x, y) position of a named object in the scene.

    Args:
        name: The string label of the object (e.g. "bed", "computer", "calendar").

    Returns:
        A string describing the object's position, or that it wasn't found.
    """
    nodes = _get_nodes(_DEFAULT_CONF_UID)
    node = _find_node(nodes, name)
    if node is None:
        return f"Error: no object found with label '{name}'."
    return f"{name} is at x={node['x']}, y={node['y']}."


@mcp.tool()
def list_objects(distance: float | None = None) -> str:
    """List objects in the scene, optionally filtered to within a distance of the agent.

    Args:
        distance: Only return objects within this distance of the agent's
            current position. Omit to return all objects.

    Returns:
        A string listing each object's label and position.
    """
    nodes = _get_nodes(_DEFAULT_CONF_UID)
    avatar = _find_node(nodes, "avatar")
    ax, ay = (avatar["x"], avatar["y"]) if avatar else (0, 0)

    results = []
    for node in nodes:
        if node.get("id") == "avatar":
            continue
        label = node.get("id") or node.get("label")
        x, y = node.get("x"), node.get("y")
        if distance is not None:
            d = math.dist((ax, ay), (x, y))
            if d > distance:
                continue
        results.append(f"{label} (x={x}, y={y})")

    if not results:
        return "(no objects found)" if distance is None else f"(no objects within {distance})"
    return "\n".join(results)


@mcp.tool()
def get_scene_status() -> str:
    """Get the agent's current position and which objects are currently reachable.

    "Reachable" is currently defined as within 150 units of the agent.

    Returns:
        A string with the agent's position and a list of reachable object labels.
    """
    nodes = _get_nodes(_DEFAULT_CONF_UID)
    avatar = _find_node(nodes, "avatar")
    if avatar is None:
        return "Error: no avatar node found in the current scene."

    ax, ay = avatar["x"], avatar["y"]
    reach_radius = 150.0
    reachable = []
    for node in nodes:
        if node.get("id") == "avatar":
            continue
        label = node.get("id") or node.get("label")
        x, y = node.get("x"), node.get("y")
        if math.dist((ax, ay), (x, y)) <= reach_radius:
            reachable.append(label)

    reachable_str = ", ".join(reachable) if reachable else "(none)"
    return f"Agent is at x={ax}, y={ay}. Reachable objects: {reachable_str}."


@mcp.tool()
async def move_to(target: str) -> str:
    """Move the agent to a named object's location and wait until it arrives.

    Args:
        target: The label of the object to move to (e.g. "calendar").

    Returns:
        A string confirming arrival, or describing the error.
    """
    nodes = _get_nodes(_DEFAULT_CONF_UID)
    node = _find_node(nodes, target)
    if node is None:
        return f"Error: no object found with label '{target}'; cannot move there."

    ok, error = await request_move_to(
        conf_uid=_DEFAULT_CONF_UID, x=node["x"], y=node["y"]
    )
    if not ok:
        return f"Error: move_to failed: {error}"
    return f"OK: arrived at {target} (x={node['x']}, y={node['y']})."


if __name__ == "__main__":
    mcp.run()