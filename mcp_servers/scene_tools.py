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
import json
import os
import sys
import urllib.error
import urllib.request
import asyncio
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.stage_state_manager import load_stage_state

# TODO: replace with however conf_uid actually gets resolved for the current
# session once that's wired up end-to-end. Hardcoded to the default character
# for now so this server is testable standalone.
_DEFAULT_CONF_UID = "mao_pro_001"
_DEFAULT_BACKEND_URL = "http://localhost:12393"
_FRONTEND_ACTION_TIMEOUT_SECONDS = 15.0

mcp = FastMCP("scene")


def _get_nodes(conf_uid: str) -> list[dict[str, Any]]:
    stage = load_stage_state(conf_uid)
    return stage.get("nodes", [])


def _find_node(nodes: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    for node in nodes:
        if node.get("id") == label or node.get("label") == label:
            return node
    return None


def _post_frontend_action_sync(action: str, args: dict[str, Any]) -> tuple[bool, str | None]:
    backend_url = os.environ.get("EMBODIED_AGENT_BACKEND_URL", _DEFAULT_BACKEND_URL)
    url = f"{backend_url.rstrip('/')}/internal/frontend-action"
    body = json.dumps(
        {
            "action": action,
            "args": args,
            "timeout": _FRONTEND_ACTION_TIMEOUT_SECONDS,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    # The backend injects this per-run secret into our subprocess env; the
    # bridge endpoint rejects requests without it.
    bridge_token = os.environ.get("EMBODIED_AGENT_BRIDGE_TOKEN")
    if bridge_token:
        headers["X-Bridge-Token"] = bridge_token
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=_FRONTEND_ACTION_TIMEOUT_SECONDS + 2.0,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return False, f"backend returned HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"could not reach backend frontend-action bridge: {exc}"

    if payload.get("ok"):
        return True, None
    return False, payload.get("error") or "frontend action failed"


async def _post_frontend_action(action: str, args: dict[str, Any]) -> tuple[bool, str | None]:
    return await asyncio.to_thread(_post_frontend_action_sync, action, args)


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

    ok, error = await _post_frontend_action(
        "move_to",
        {"x": node["x"], "y": node["y"]},
    )
    if not ok:
        return f"Error: move_to failed: {error}"
    return f"OK: arrived at {target} (x={node['x']}, y={node['y']})."


if __name__ == "__main__":
    mcp.run()
