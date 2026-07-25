"""Idle tools for the embodied companion.

These belong to the `sofa`: the agent walks over and sits when it has nothing to
do, instead of standing in the middle of the room forever. Sitting does no work
and touches no stores — it only changes the avatar's pose, which the frontend
renders as the seated animation.

Walking away clears the pose automatically, so a seated avatar can never be
animated mid-stride: `move_to` resets it as part of the same stage update.

Run standalone for a quick sanity check:
    uv run python mcp_servers/idle_tools.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from mcp_servers.bridge_client import get_conf_uid, post_frontend_action
from src.open_llm_vtuber.stage_state_manager import load_stage_state

_MAX_REASON = 200

mcp = FastMCP("idle")


async def _set_pose(pose: str | None) -> tuple[bool, str | None]:
    """Patch only the avatar's pose, leaving its position and sprite alone."""
    nodes = load_stage_state(get_conf_uid()).get("nodes", [])
    if not any(node.get("id") == "avatar" for node in nodes):
        return False, "this room has no avatar to pose"
    return await post_frontend_action(
        "stage_patch_state",
        {
            "patch": {
                "nodes": [{"id": "avatar", "pose": pose}],
                "metadata": {"updated_by": "idle"},
            }
        },
    )


@mcp.tool()
async def sit_down(reason: str = "") -> str:
    """Sit down and rest. Requires standing at the sofa.

    Use this when there is nothing to do rather than idling on your feet. You
    stay reachable: a message from the user, a scheduled task, or a timer all
    still arrive while you are seated, and walking anywhere stands you up.

    Args:
        reason: Optional short note about why you are sitting down.
    """
    ok, error = await _set_pose("sitting")
    if not ok:
        return f"Error: sit_down failed: {error}"
    note = " ".join(str(reason).split())[:_MAX_REASON]
    return f"OK: sitting on the sofa.{f' ({note})' if note else ''}"


@mcp.tool()
async def stand_up() -> str:
    """Stand up from wherever you are sitting. Requires the sofa."""
    ok, error = await _set_pose(None)
    if not ok:
        return f"Error: stand_up failed: {error}"
    return "OK: standing."


if __name__ == "__main__":
    mcp.run()
