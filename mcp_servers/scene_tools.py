"""Scene / spatial MCP server for the embodied agent.

Exposes tools that let the agent query and navigate the 2D stage defined in
`stage_state_manager.py` (bed, computer, calendar, avatar/agent).

Spatial questions are answered through `world_model.py` rather than by reading
node dictionaries, so nothing here depends on how the room is drawn.

get_object_position, list_objects, and get_scene_status read directly from
the persisted stage JSON (no live frontend round-trip needed).

move_to is different: it has to ask the running frontend to actually animate
the avatar and report back when done, so it goes through the live WebSocket
channel (see mcp_servers/bridge_client.py and frontend_bridge.py for the
request/response plumbing). It sends a path and then confirms arrival against
the saved room, so a walk that is interrupted is reported as one. The three
tools that change the room itself — place_object, move_object, and
remove_object — work the same way, and are only offered at the `design board`.

Run standalone for a quick sanity check:
    uv run python mcp_servers/scene_tools.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from mcp_servers.bridge_client import get_conf_uid as _get_conf_uid
from mcp_servers.bridge_client import get_scene_mode as _get_scene_mode
from mcp_servers.bridge_client import post_frontend_action as _post_frontend_action
from mcp_servers.bridge_client import (
    post_frontend_action_result as _post_frontend_action_result,
)
from src.open_llm_vtuber.scene_state import (
    load_scene_state,
    load_scene_world,
    validate_scene,
)
from src.open_llm_vtuber.stage_state_manager import (
    load_stage_state,
    validate_stage,
    world_from_stage,
)
from src.open_llm_vtuber.vr_stage_state_manager import (
    docking_point,
    verify_vr_target_reach,
)
from src.open_llm_vtuber.world_model import World, WorldPoint

_OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# Removing these would strand the tools that are only reachable at them.
_PROTECTED_OBJECT_IDS = frozenset({"avatar", "computer", "design_board"})

mcp = FastMCP("scene")


def _get_nodes(conf_uid: str) -> list[dict[str, Any]]:
    stage = _load_state(conf_uid)
    return stage.get("nodes", [])


def _get_world(conf_uid: str) -> World:
    """The room as positions and reach, with nothing about how it is drawn."""
    return (
        load_scene_world(conf_uid, "vr")
        if _get_scene_mode() == "vr"
        else world_from_stage(load_stage_state(conf_uid))
    )


def _load_state(conf_uid: str) -> dict[str, Any]:
    return (
        load_scene_state(conf_uid, "vr")
        if _get_scene_mode() == "vr"
        else load_stage_state(conf_uid)
    )


def _validate_state(stage: Any) -> tuple[bool, list[str]]:
    return (
        validate_scene(stage, "vr")
        if _get_scene_mode() == "vr"
        else validate_stage(stage)
    )


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
    obj = _get_world(_get_conf_uid()).find(name)
    if obj is None:
        return f"Error: no object found with label '{name}'."
    return f"{name} is at x={obj.position.x:g}, y={obj.position.y:g}."


@mcp.tool()
def list_objects(distance: float | None = None) -> str:
    """List objects in the scene, optionally filtered to within a distance of the agent.

    Args:
        distance: Only return objects within this distance of the agent's
            current position. Omit to return all objects.

    Returns:
        A string listing each object's label and position.
    """
    world = _get_world(_get_conf_uid())
    origin = world.agent_position or WorldPoint(0.0, 0.0)

    results = []
    for obj in world.objects:
        if distance is not None and obj.position.distance_to(origin) > distance:
            continue
        results.append(f"{obj.id} (x={obj.position.x:g}, y={obj.position.y:g})")

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

    Each object sets its own reach from how big it is, so a bed can be used
    from further away than an hourglass.

    Returns:
        A string with the agent's position and a list of reachable object labels.
    """
    world = _get_world(_get_conf_uid())
    position = world.agent_position
    if position is None:
        return "Error: no avatar node found in the current scene."

    reachable = [obj.id for obj in world.reachable_from(position)]
    reachable_str = ", ".join(reachable) if reachable else "(none)"
    return (
        f"Agent is at x={position.x:g}, y={position.y:g}. "
        f"Reachable objects: {reachable_str}."
    )


@mcp.tool()
async def move_to(target: str) -> str:
    """Move the agent to a named object's location and wait until it arrives.

    Args:
        target: The label of the object to move to (e.g. "calendar").

    Returns:
        A string confirming arrival, or describing the error.
    """
    conf_uid = _get_conf_uid()
    world = _get_world(conf_uid)
    destination = world.find(target)
    if destination is None:
        return f"Error: no object found with label '{target}'; cannot move there."

    final_position = destination.position
    if _get_scene_mode() == "vr":
        final_position, docking_error = docking_point(
            _load_state(conf_uid),
            target,
        )
        if final_position is None:
            return f"Error: {docking_error}."
    path = world.path_to(final_position)
    ok, error, _result = await _post_frontend_action_result(
        "move_to",
        {
            "target": destination.id,
            "x": final_position.x,
            "y": final_position.y,
            "waypoints": [{"x": point.x, "y": point.y} for point in path],
        },
    )
    if not ok:
        return f"Error: move_to failed: {error}"

    # The frontend reports where it ended up and the backend has already saved
    # that stage, so arrival is checked against the room rather than assumed
    # from a request that was merely accepted.
    arrived = _get_world(conf_uid)
    if not arrived.has_arrived_at(final_position):
        position = arrived.agent_position
        where = f"x={position.x:g}, y={position.y:g}" if position else "an unknown spot"
        return f"Error: the walk to {target} did not finish; the agent is at {where}."

    if _get_scene_mode() == "vr":
        reachable, detail = verify_vr_target_reach(_load_state(conf_uid), target)
        if not reachable:
            return (
                f"Error: arrived at {target}, but its tools remain unavailable "
                f"after reloading the saved VR scene ({detail})."
            )

    return f"OK: arrived at {target} (x={final_position.x:g}, y={final_position.y:g})."


@mcp.tool()
def describe_room() -> str:
    """Describe the room layout: every object, its size, and free space.

    Requires standing at the design board, which is where the room is redrawn.
    """
    world = _get_world(_get_conf_uid())
    objects = []
    for obj in world.objects:
        width, height = obj.footprint
        objects.append(
            f"{obj.id} (label={obj.label}, x={obj.position.x:g}, "
            f"y={obj.position.y:g}, {width:g}x{height:g}, reach {obj.reach:g}"
            f"{', fixed' if obj.id in _PROTECTED_OBJECT_IDS else ''})"
        )
    if not objects:
        return "The room is empty."
    extent_width, extent_height = world.extent
    return (
        f"Room is {extent_width:g}x{extent_height:g} units "
        f"({world.units_per_meter:g} units per metre). "
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
    depth: float | None = None,
    elevation: float | None = None,
    color: str = "#334155",
) -> str:
    """Place a validated furniture panel in the persistent room.

    Requires standing at the design board. How close the agent must stand to
    use the new object is derived from its size, so a larger object is usable
    from further away.

    Args:
        name: Unique object ID/label using letters, numbers, hyphens, or underscores.
        x: Horizontal center coordinate within the room.
        y: Vertical center coordinate within the room.
        width: Object width in world units (default 120).
        height: Object height in world units (default 90).
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

    mode = _get_scene_mode()
    stage = _load_state(_get_conf_uid())
    if _find_node(stage.get("nodes", []), name) is not None:
        return f"Error: an object named '{name}' already exists."
    extent = stage.get("room", {}) if mode == "vr" else stage.get("canvas", {})
    extent_width = extent.get("width", 0)
    extent_height = extent.get("depth", 0) if mode == "vr" else extent.get("height", 0)
    if not 0 <= x <= extent_width or not 0 <= y <= extent_height:
        return "Error: x and y must be inside the stage canvas."

    if mode == "vr":
        box_depth = float(depth if depth is not None else 90.0)
        box_elevation = float(elevation if elevation is not None else height / 2)
        node = {
            "id": name,
            "type": "furniture",
            "label": name,
            "x": float(x),
            "y": float(y),
            "width": float(width),
            "depth": box_depth,
            "height": float(height),
            "elevation": box_elevation,
            "color": color,
            "interaction": {"on_click": True},
        }
    else:
        if depth is not None or elevation is not None or color != "#334155":
            return "Error: depth, elevation, and color are only available in VR mode."
        node = {
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
    valid, errors = _validate_state(candidate)
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
async def place_prop(
    name: str,
    x: float,
    y: float,
    width: float = 24.0,
    depth: float = 12.0,
    height: float = 16.0,
    color: str = "#7c3aed",
) -> str:
    """Place a persistent, user-grabbable VR prop at the design board."""
    if _get_scene_mode() != "vr":
        return "Error: physical props are only available in VR mode."
    if not isinstance(name, str) or not _OBJECT_ID_RE.fullmatch(name):
        return "Error: name must match [A-Za-z0-9_-] and contain 1-80 characters."
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in (x, y, width, depth, height)
    ):
        return "Error: prop position and dimensions must be numeric."
    stage = _load_state(_get_conf_uid())
    if _find_node(stage.get("nodes", []), name) is not None:
        return f"Error: an object named '{name}' already exists."
    node = {
        "id": name,
        "type": "prop",
        "label": name,
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "depth": float(depth),
        "height": float(height),
        "elevation": float(height) / 2,
        "yaw": 0.0,
        "support_id": "floor",
        "color": color,
        "interaction": {"on_click": True, "grabbable": True},
    }
    valid, errors = _validate_state({**stage, "nodes": [*stage.get("nodes", []), node]})
    if not valid:
        return "Error: prop was rejected: " + "; ".join(errors)
    ok, error = await _post_frontend_action(
        "stage_patch_state",
        {"patch": {"nodes": [node], "metadata": {"updated_by": "place_prop"}}},
    )
    return (
        f"OK: placed grabbable prop {name}."
        if ok
        else f"Error: place_prop failed: {error}"
    )


async def _physical_action(action: str, args: dict[str, Any]) -> str:
    if _get_scene_mode() != "vr":
        return "Error: this physical affordance is only available in VR mode."
    ok, error, result = await _post_frontend_action_result(action, args)
    if not ok:
        return f"Error: {action} failed: {error}"
    summary = result.get("summary")
    return (
        str(summary)[:2000]
        if isinstance(summary, str) and summary
        else f"OK: {action} completed."
    )


@mcp.tool()
async def list_physical_entities() -> str:
    """List persistent props and session-only cards/panels in the VR room."""
    return await _physical_action("list_physical_entities", {})


@mcp.tool()
async def show_vr_panel(
    title: str,
    blocks: list[Any] | None = None,
    pages: list[Any] | None = None,
    initial_page_id: str = "",
) -> str:
    """Create a bounded, movable information panel in front of the VR user.

    Existing callers can pass ``blocks`` for one scrollable page. To create a
    navigable document, pass up to eight pages. Each page accepts ``id``,
    ``title``, ``blocks`` and optional ``links`` entries containing only
    ``label`` and ``target_page_id``.
    """
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        return "Error: panel title must contain 1-120 characters."

    def validate_blocks(value: Any) -> tuple[str | None, int]:
        if not isinstance(value, list) or not 1 <= len(value) <= 6:
            return "panel must contain 1-6 text, list, or key-value blocks.", 0
        total = 0
        for block in value:
            if isinstance(block, str):
                total += len(block)
                continue
            if not isinstance(block, dict) or block.get("type") not in {
                "text", "list", "key_value"
            }:
                return "panel blocks must be text, list, or key_value structures.", 0
            allowed = {
                "text": {"type", "text"},
                "list": {"type", "items"},
                "key_value": {"type", "items"},
            }[str(block["type"])]
            if set(block) - allowed:
                return "panel block contains unsupported fields.", 0
            value = block.get("text") if block["type"] == "text" else block.get("items")
            if block["type"] == "text" and not isinstance(value, str):
                return "text blocks require a string text field.", 0
            if block["type"] == "list" and (
                not isinstance(value, list) or len(value) > 12
                or any(not isinstance(item, str) for item in value)
            ):
                return "list blocks require at most 12 string items.", 0
            if block["type"] == "key_value" and (
                not isinstance(value, dict) or len(value) > 12
                or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items())
            ):
                return "key_value blocks require at most 12 string pairs.", 0
            total += (
                len(value) if isinstance(value, str)
                else sum(len(str(key)) + len(item) for key, item in value.items())
                if isinstance(value, dict)
                else sum(len(item) for item in value)
            )
        return None, total

    if pages is not None:
        if blocks not in (None, []):
            return "Error: use either blocks or pages, not both."
        if not isinstance(pages, list) or not 1 <= len(pages) <= 8:
            return "Error: panel pages must contain 1-8 pages."
        page_ids: set[str] = set()
        total_text = 0
        for page in pages:
            if not isinstance(page, dict) or set(page) - {"id", "title", "blocks", "links"}:
                return "Error: panel page contains unsupported fields."
            page_id = page.get("id")
            page_title = page.get("title")
            if not isinstance(page_id, str) or not page_id or len(page_id) > 64 or page_id in page_ids:
                return "Error: panel page ids must be unique strings of 1-64 characters."
            if not isinstance(page_title, str) or not page_title.strip() or len(page_title) > 120:
                return "Error: panel page titles must contain 1-120 characters."
            error, text_count = validate_blocks(page.get("blocks"))
            if error:
                return f"Error: {error}"
            links = page.get("links", [])
            if not isinstance(links, list) or len(links) > 4:
                return "Error: a panel page can contain at most four links."
            for link in links:
                if not isinstance(link, dict) or set(link) != {"label", "target_page_id"}:
                    return "Error: panel links require only label and target_page_id."
                if not isinstance(link["label"], str) or not 1 <= len(link["label"]) <= 80:
                    return "Error: panel link labels must contain 1-80 characters."
                if not isinstance(link["target_page_id"], str):
                    return "Error: panel link targets must be strings."
                total_text += len(link["label"])
            page_ids.add(page_id)
            total_text += len(page_title) + text_count
        if total_text > 8000:
            return "Error: panel content cannot exceed 8000 characters."
        if initial_page_id and initial_page_id not in page_ids:
            return "Error: initial_page_id does not name a panel page."
        for page in pages:
            if any(link["target_page_id"] not in page_ids for link in page.get("links", [])):
                return "Error: panel link target does not name a panel page."
        payload = {
            "title": title.strip(),
            "pages": pages,
            "initial_page_id": initial_page_id or pages[0]["id"],
        }
        return await _physical_action("show_vr_panel", payload)

    error, total_text = validate_blocks(blocks)
    if error:
        return f"Error: {error}"
    if total_text > 4000:
        return "Error: panel content cannot exceed 4000 characters."
    return await _physical_action(
        "show_vr_panel", {"title": title.strip(), "blocks": blocks}
    )


@mcp.tool()
async def pick_up_entity(entity_id: str) -> str:
    """Pick up one nearby grabbable physical entity with the agent's hand."""
    return await _physical_action("pick_up_entity", {"entity_id": entity_id})


@mcp.tool()
async def put_down_entity(entity_id: str, target: str = "floor") -> str:
    """Put an agent-held entity on a named support surface or the floor."""
    return await _physical_action(
        "put_down_entity", {"entity_id": entity_id, "target": target}
    )


@mcp.tool()
async def offer_entity(entity_id: str) -> str:
    """Offer an agent-held entity to the present VR user without assuming acceptance."""
    return await _physical_action("offer_entity", {"entity_id": entity_id})


@mcp.tool()
async def dismiss_entity(entity_id: str) -> str:
    """Dismiss a session-only VR card or panel."""
    return await _physical_action("dismiss_entity", {"entity_id": entity_id})


@mcp.tool()
async def get_user_location() -> str:
    """Get the current VR user's fresh location, facing, and nearby furniture."""
    return await _physical_action("get_user_location", {})


@mcp.tool()
async def move_to_user() -> str:
    """Move toward a safe conversational position near the current VR user."""
    return await _physical_action("move_to_user", {})


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

    mode = _get_scene_mode()
    stage = _load_state(_get_conf_uid())
    node = _find_node(stage.get("nodes", []), name)
    if node is None:
        return f"Error: no object found with label '{name}'."
    if node.get("id") == "avatar":
        return "Error: use move_to to move the avatar."

    extent = stage.get("room", {}) if mode == "vr" else stage.get("canvas", {})
    extent_width = extent.get("width", 0)
    extent_height = extent.get("depth", 0) if mode == "vr" else extent.get("height", 0)
    if not 0 <= x <= extent_width or not 0 <= y <= extent_height:
        return "Error: x and y must be inside the stage canvas."

    dx, dy = float(x) - float(node["x"]), float(y) - float(node["y"])
    moved = {**node, "x": float(x), "y": float(y)}
    supported_props = []
    if mode == "vr" and node.get("type") == "furniture":
        supported_props = [
            {**item, "x": float(item["x"]) + dx, "y": float(item["y"]) + dy}
            for item in stage.get("nodes", [])
            if item.get("type") == "prop" and item.get("support_id") == node.get("id")
        ]
    replacements = {item["id"]: item for item in [moved, *supported_props]}
    candidate = {
        **stage,
        "nodes": [
            replacements.get(item.get("id"), item) for item in stage.get("nodes", [])
        ],
    }
    valid, errors = _validate_state(candidate)
    if not valid:
        return "Error: the move was rejected: " + "; ".join(errors)

    ok, error = await _post_frontend_action(
        "stage_patch_state",
        {
            "patch": {
                "nodes": [moved, *supported_props],
                "metadata": {"updated_by": "move_object"},
            }
        },
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
    supported = [
        item.get("id")
        for item in nodes
        if item.get("type") == "prop" and item.get("support_id") == node_id
    ]
    if supported:
        return (
            f"Error: '{node_id}' supports props {', '.join(supported)}; "
            "move those props before removing the furniture."
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
