"""Scene / spatial MCP server for the embodied agent.

Exposes tools that let the agent query and navigate the 2D stage defined in
`stage_state_manager.py` (bed, computer, calendar, avatar/agent).

get_object_position, list_objects, and get_scene_status read directly from
the persisted stage JSON (no live frontend round-trip needed).

move_to is different: it has to ask the running frontend to actually animate
the avatar and report back when done, so it goes through the live WebSocket
channel (see mcp_servers/bridge_client.py and frontend_bridge.py for the
request/response plumbing). The three tools that change the room itself —
place_object, move_object, and remove_object — work the same way, and are only
offered at the `design board`.

Run standalone for a quick sanity check:
    uv run python mcp_servers/scene_tools.py
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from mcp_servers.bridge_client import get_conf_uid as _get_conf_uid
from mcp_servers.bridge_client import post_frontend_action as _post_frontend_action
from src.open_llm_vtuber.stage_state_manager import load_stage_state, validate_stage

_OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# Removing these would strand the tools that are only reachable at them.
_PROTECTED_OBJECT_IDS = frozenset({"avatar", "computer", "design_board"})

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
    nodes = _get_nodes(_get_conf_uid())
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
    nodes = _get_nodes(_get_conf_uid())
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
        return (
            "(no objects found)"
            if distance is None
            else f"(no objects within {distance})"
        )
    return "\n".join(results)


@mcp.tool()
def get_scene_status() -> str:
    """Get the agent's current position and which objects are currently reachable.

    "Reachable" is currently defined as within 150 units of the agent.

    Returns:
        A string with the agent's position and a list of reachable object labels.
    """
    nodes = _get_nodes(_get_conf_uid())
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
    nodes = _get_nodes(_get_conf_uid())
    node = _find_node(nodes, target)
    if node is None:
        return f"Error: no object found with label '{target}'; cannot move there."

    ok, error = await _post_frontend_action(
        "move_to",
        {"x": node["x"], "y": node["y"]},
    )
    if not ok:
        return f"Error: move_to failed: {error}"
    return f"OK: arrived at {target} (x={node['x']}, y={node['y']})."


@mcp.tool()
def describe_room() -> str:
    """Describe the room layout: every object, its size, and free space.

    Requires standing at the design board, which is where the room is redrawn.
    """
    stage = load_stage_state(_get_conf_uid())
    canvas = stage.get("canvas", {})
    objects = []
    for node in stage.get("nodes", []):
        if node.get("id") == "avatar":
            continue
        objects.append(
            f"{node.get('id')} (label={node.get('label')}, x={node.get('x')}, "
            f"y={node.get('y')}, {node.get('width')}x{node.get('height')}"
            f"{', fixed' if node.get('id') in _PROTECTED_OBJECT_IDS else ''})"
        )
    if not objects:
        return "The room is empty."
    return (
        f"Canvas is {canvas.get('width')}x{canvas.get('height')}. "
        f"{len(objects)} object(s):\n" + "\n".join(objects)
    )


@mcp.tool()
async def place_object(
    name: str,
    x: float,
    y: float,
    width: float = 120.0,
    height: float = 90.0,
    image_url: str | None = None,
) -> str:
    """Place a validated furniture panel in the persistent room.

    Requires standing at the design board.

    Args:
        name: Unique object ID/label using letters, numbers, hyphens, or underscores.
        x: Horizontal center coordinate within the stage canvas.
        y: Vertical center coordinate within the stage canvas.
        width: Object width in scene units (default 120).
        height: Object height in scene units (default 90).
        image_url: Optional safe http(s), data-image, or relative asset URL.
    """
    if not isinstance(name, str) or not _OBJECT_ID_RE.fullmatch(name):
        return "Error: name must match [A-Za-z0-9_-] and contain 1-80 characters."
    dimensions = (x, y, width, height)
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in dimensions
    ):
        return "Error: x, y, width, and height must be numeric."
    if width < 1 or width > 4096 or height < 1 or height > 4096:
        return "Error: width and height must each be between 1 and 4096."

    stage = load_stage_state(_get_conf_uid())
    if _find_node(stage.get("nodes", []), name) is not None:
        return f"Error: an object named '{name}' already exists."
    canvas = stage.get("canvas", {})
    if not 0 <= x <= canvas.get("width", 0) or not 0 <= y <= canvas.get("height", 0):
        return "Error: x and y must be inside the stage canvas."

    node: dict[str, Any] = {
        "id": name,
        "type": "panel",
        "label": name,
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "height": float(height),
        "zIndex": 2,
        "text": name.replace("_", " "),
        "style": {
            "background": "#334155",
            "borderColor": "#cbd5e1",
            "color": "#ffffff",
            "borderRadius": 12,
        },
    }
    if image_url is not None:
        node["texture"] = {"src": image_url, "fit": "contain", "alt": name}

    candidate = {**stage, "nodes": [*stage.get("nodes", []), node]}
    valid, errors = validate_stage(candidate)
    if not valid:
        return "Error: object was rejected: " + "; ".join(errors)

    ok, error = await _post_frontend_action(
        "stage_patch_state",
        {"patch": {"nodes": [node], "metadata": {"updated_by": "place_object"}}},
    )
    if not ok:
        return f"Error: place_object failed: {error}"
    return f"OK: placed {name} at x={x}, y={y}."


@mcp.tool()
async def move_object(name: str, x: float, y: float) -> str:
    """Move an existing room object to a new position.

    Requires standing at the design board. The avatar is not furniture: use
    `move_to` to walk instead.

    Args:
        name: The object's ID or label.
        x: New horizontal center coordinate within the stage canvas.
        y: New vertical center coordinate within the stage canvas.
    """
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in (x, y)
    ):
        return "Error: x and y must be numeric."

    stage = load_stage_state(_get_conf_uid())
    node = _find_node(stage.get("nodes", []), name)
    if node is None:
        return f"Error: no object found with label '{name}'."
    if node.get("id") == "avatar":
        return "Error: use move_to to move the avatar."

    canvas = stage.get("canvas", {})
    if not 0 <= x <= canvas.get("width", 0) or not 0 <= y <= canvas.get("height", 0):
        return "Error: x and y must be inside the stage canvas."

    moved = {**node, "x": float(x), "y": float(y)}
    candidate = {
        **stage,
        "nodes": [
            moved if item.get("id") == node.get("id") else item
            for item in stage.get("nodes", [])
        ],
    }
    valid, errors = validate_stage(candidate)
    if not valid:
        return "Error: the move was rejected: " + "; ".join(errors)

    ok, error = await _post_frontend_action(
        "stage_patch_state",
        {"patch": {"nodes": [moved], "metadata": {"updated_by": "move_object"}}},
    )
    if not ok:
        return f"Error: move_object failed: {error}"
    return f"OK: moved {node.get('id')} to x={x}, y={y}."


@mcp.tool()
async def remove_object(name: str) -> str:
    """Remove a named room object. Requires standing at the design board.

    The avatar, the computer, and the design board itself cannot be removed,
    because the tools reachable at them would become unreachable.
    """
    nodes = _get_nodes(_get_conf_uid())
    node = _find_node(nodes, name)
    if node is None:
        return f"Error: no object found with label '{name}'."
    node_id = node.get("id")
    if node_id == "avatar":
        return "Error: the avatar cannot be removed from its own room."
    if node_id in _PROTECTED_OBJECT_IDS:
        return (
            f"Error: '{node_id}' is fixed furniture; removing it would strand the "
            "tools that are only available there."
        )

    ok, error = await _post_frontend_action(
        "stage_patch_state",
        {
            "patch": {
                "remove_node_ids": [node_id],
                "metadata": {"updated_by": "remove_object"},
            }
        },
    )
    if not ok:
        return f"Error: remove_object failed: {error}"
    return f"OK: removed {node_id}."


if __name__ == "__main__":
    mcp.run()
