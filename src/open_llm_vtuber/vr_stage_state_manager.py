"""Persistent Meta Quest VR scene state.

VR deliberately has its own v3 document and file.  The normal 2D stage remains
v9 and never migrates into, or is overwritten by, this store.
"""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
from typing import Any

from loguru import logger

from .stage_state_manager import (
    AVATAR_POSES,
    BROWSER_WINDOW,
    LABEL_LIMIT,
    _ensure_stage_dir,
    _backfill_browser_window,
    _is_number,
    _is_safe_color,
    _is_safe_text,
    _now_iso,
    _validate_interaction,
    _validate_reach,
    _validate_spritesheet,
    _validate_state_textures,
    _validate_texture,
    _validate_ui_window,
)
from .world_model import (
    REACH_CEILING,
    World,
    WorldPoint,
    effective_label,
    world_from_stage,
)

VR_STAGE_VERSION = 3
VR_THEME_REVISION = "quest-room-v3"
VR_STAGE_FILENAME = "vr_stage.json"
AGENT_CLEARANCE = 10.0

# x, y, width, depth, height, elevation, color
_VR_LAYOUT: dict[str, tuple[float, float, float, float, float, float, str]] = {
    # North wall, left to right. Each footprint is flush with y=0.
    "window": (173, 8, 185, 16, 155, 165, "#9cc9e8"),
    "todo_list": (384, 6, 105, 12, 125, 165, "#d9952f"),
    "calendar": (557, 6, 110, 12, 130, 165, "#2c9b8e"),
    "design_board": (738, 6, 120, 12, 110, 160, "#596273"),
    "mailbox": (912, 12, 96, 24, 88, 140, "#596579"),
    "hourglass": (1064, 10, 65, 20, 90, 45, "#b77b25"),
    "drawing_board": (1215, 6, 115, 12, 125, 100, "#6366f1"),
    "tool_rack": (1398, 17.5, 120, 35, 100, 50, "#565b65"),
    "archive_shelf": (1587, 27.5, 125, 55, 170, 85, "#87532f"),
    "bookshelf": (1775, 22.5, 120, 45, 190, 95, "#765035"),
    # South wall, left to right. Each footprint is flush with y=1080.
    "computer": (265, 1025, 230, 110, 140, 70, "#4543a8"),
    "reading_chair": (620, 1027.5, 120, 105, 100, 50, "#805d43"),
    "bed": (965, 1005, 210, 150, 65, 32.5, "#a84e65"),
    "sofa": (1345, 1027.5, 190, 105, 85, 42.5, "#58647a"),
    "writing_desk": (1695, 1045, 150, 70, 85, 42.5, "#74513c"),
    "avatar": (960, 540, 70, 20, 92, 46, "#2563eb"),
}


def _furniture(
    label: str,
    *,
    texture: str | None = None,
    alt: str | None = None,
    title: str | None = None,
    body: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "label": label,
        "interaction": {"on_click": True},
    }
    if texture:
        value["texture"] = {
            "src": f"./stage-assets/{texture}",
            "fit": "contain",
            "alt": alt or label,
        }
    if title:
        value["uiWindow"] = {"title": title, "body": body or ""}
        if provider:
            value["uiWindow"]["provider"] = provider
    return value


# This is a deliberate snapshot of the current v9 presentation metadata, not a
# view of either stage.json or the normal default builder. Tests flag ID drift.
_VR_PRESENTATION: dict[str, dict[str, Any]] = {
    "computer": _furniture(
        "computer",
        texture="computer.png",
        title=BROWSER_WINDOW["title"],
        body=BROWSER_WINDOW["body"],
        provider=BROWSER_WINDOW["provider"],
    ),
    "calendar": _furniture(
        "calendar",
        texture="calendar.png",
        title="Google Calendar",
        body="Connection status for the primary calendar.",
        provider="google_calendar_status",
    ),
    "bed": _furniture("bed", texture="bed.png"),
    "archive_shelf": _furniture("archive shelf", texture="archive-shelf.png"),
    "mailbox": {
        **_furniture(
            "mailbox",
            texture="mailbox-empty.png",
            title="Unread Gmail",
            body="Unread messages for the connected Google account.",
            provider="google_gmail_unread",
        ),
        "stateTextures": {
            "unread": {
                "src": "./stage-assets/mailbox-unread.png",
                "fit": "contain",
                "alt": "mailbox with unread mail",
            }
        },
    },
    "todo_list": _furniture(
        "todo_list",
        texture="todo-list.png",
        alt="todo list",
        title="Todo list",
        body="Open items the agent is tracking.",
        provider="todo_list",
    ),
    "hourglass": _furniture(
        "hourglass",
        texture="hourglass.png",
        title="Timers",
        body="Timers the agent has set.",
        provider="timer_status",
    ),
    "window": _furniture(
        "window",
        texture="window.png",
        alt="bedroom window",
        title="Window",
        body=(
            "The agent checks the weather from here, using a public forecast "
            "service that needs no account."
        ),
    ),
    "bookshelf": _furniture(
        "bookshelf",
        texture="bookshelf.png",
        title="Bookshelf",
        body="Books the agent has shelved to read.",
        provider="bookshelf",
    ),
    "reading_chair": _furniture(
        "reading chair",
        texture="reading-chair.png",
        title="Reading chair",
        body="The book being read, and the notes taken so far.",
        provider="reading_status",
    ),
    "writing_desk": _furniture(
        "writing desk",
        texture="writing-desk.png",
        title="Plans",
        body="Plans the agent has drafted, awaiting your decision.",
        provider="plan_status",
    ),
    "sofa": _furniture(
        "sofa",
        texture="sofa.png",
        title="Sofa",
        body=(
            "Where the agent sits when there is nothing to do. It still hears "
            "you, and walking anywhere stands it up."
        ),
    ),
    "design_board": _furniture(
        "design board",
        texture="design-board.png",
        title="Room layout",
        body="Every object in the room, and which are fixed.",
        provider="room_layout",
    ),
    "tool_rack": _furniture(
        "tool rack",
        texture="tool-rack.png",
        title="Skills",
        body="Skills the agent has written for itself, and drafts waiting on you.",
        provider="skill_status",
    ),
    "drawing_board": _furniture(
        "drawing_board",
        title="Drawing Board",
        body="The latest image generated by the agent.",
        provider="drawing_board_image",
    ),
    "avatar": {
        "label": "avatar",
        "interaction": {"on_click": True},
        "spritesheet": {
            "src": "./stage-assets/avatar.png",
            "frameWidth": 48,
            "frameHeight": 64,
            "fps": 6,
            "defaultAnimations": {"idle": "idle_down", "walk": "walk_down"},
            "animations": {
                "idle_down": {"row": 0, "frames": [0, 1, 2, 1]},
                "idle_left": {"row": 1, "frames": [0, 1, 2, 1]},
                "idle_right": {"row": 2, "frames": [0, 1, 2, 1]},
                "idle_up": {"row": 3, "frames": [0, 1, 2, 1]},
                "walk_down": {"row": 4, "frames": [0, 1, 2, 3]},
                "walk_left": {"row": 5, "frames": [0, 1, 2, 3]},
                "walk_right": {"row": 6, "frames": [0, 1, 2, 3]},
                "walk_up": {"row": 7, "frames": [0, 1, 2, 3]},
                "sit_down": {"row": 8, "frames": [0, 1, 2, 1]},
            },
        },
    },
}


def _vr_path(conf_uid: str) -> Path:
    base = Path(_ensure_stage_dir(conf_uid)).resolve()
    path = (base / VR_STAGE_FILENAME).resolve()
    path.relative_to(base)
    return path


def get_default_vr_stage(conf_uid: str | None = None) -> dict[str, Any]:
    """Build the fixed, renderer-specific default without reading saved 2D state."""
    nodes: list[dict[str, Any]] = []
    for node_id, geometry in _VR_LAYOUT.items():
        x, y, width, depth, height, elevation, color = geometry
        node: dict[str, Any] = {
            "id": node_id,
            "type": "avatar" if node_id == "avatar" else "furniture",
            "x": float(x),
            "y": float(y),
            "width": float(width),
            "depth": float(depth),
            "height": float(height),
            "elevation": float(elevation),
            "color": color,
            **copy.deepcopy(_VR_PRESENTATION[node_id]),
        }
        nodes.append(node)

    now = _now_iso()
    return {
        "version": VR_STAGE_VERSION,
        "mode": "vr",
        "room": {
            "width": 1920.0,
            "depth": 1080.0,
            "height": 280.0,
            "floor_color": "#b99468",
            "wall_color": "#ead7bd",
        },
        "world": {
            "units_per_meter": 100.0,
            "reach_ceiling": REACH_CEILING,
            "move_speed": 200.0,
        },
        "nodes": nodes,
        "metadata": {
            "conf_uid": conf_uid,
            "created_at": now,
            "updated_at": now,
            "updated_by": "default",
            "theme_revision": VR_THEME_REVISION,
        },
    }


def _bounds(node: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(node["x"]) - float(node["width"]) / 2,
        float(node["x"]) + float(node["width"]) / 2,
        float(node["y"]) - float(node["depth"]) / 2,
        float(node["y"]) + float(node["depth"]) / 2,
    )


def point_inside_node(point: WorldPoint, node: dict[str, Any]) -> bool:
    """Inclusive floor-AABB test used by endpoint validation."""
    left, right, back, front = _bounds(node)
    return left <= point.x <= right and back <= point.y <= front


def validate_vr_endpoint(
    stage: dict[str, Any], point: WorldPoint
) -> tuple[bool, str | None]:
    room = stage.get("room", {})
    avatar = next((n for n in stage.get("nodes", []) if n.get("id") == "avatar"), None)
    if avatar is None:
        return False, "VR scene has no avatar."
    radius = max(float(avatar["width"]), float(avatar["depth"])) / 2
    if not radius <= point.x <= float(room.get("width", 0)) - radius:
        return False, "Agent endpoint is outside the VR room."
    if not radius <= point.y <= float(room.get("depth", 0)) - radius:
        return False, "Agent endpoint is outside the VR room."
    for node in stage.get("nodes", []):
        if node.get("id") != "avatar" and point_inside_node(point, node):
            return (
                False,
                f"Agent endpoint is inside {effective_label(node) or node.get('id')}.",
            )
    return True, None


def verify_vr_target_reach(
    stage: dict[str, Any],
    target_name: str,
    *,
    point: WorldPoint | None = None,
) -> tuple[bool, str]:
    """Explain whether a VR position unlocks a target without widening reach."""
    from .semantic_projection import ProjectionError, project_vr_stage

    try:
        projection = project_vr_stage(stage)
    except (ProjectionError, ValueError) as exc:
        return False, f"VR proximity projection failed: {exc}"
    target = projection.world.find(target_name)
    if target is None or target.entity_id in projection.withheld_ids:
        return False, f"VR target '{target_name}' is missing or has an ambiguous label"
    origin = point
    if origin is None:
        agent = projection.agent()
        if agent is None:
            return False, "VR scene has no avatar"
        origin = WorldPoint(agent.position.x, agent.position.y)
    center_distance = origin.distance_to(
        WorldPoint(target.position.x, target.position.y)
    )
    effective_reach = projection.reaches.get(target.entity_id, 0.0)
    allowed = center_distance <= effective_reach
    detail = (
        f"target={target.label}, position=({origin.x:g}, {origin.y:g}), "
        f"center_distance={center_distance:.1f}, effective_reach={effective_reach:.1f}"
    )
    return allowed, detail


def docking_point(
    stage: dict[str, Any], target_name: str
) -> tuple[WorldPoint | None, str | None]:
    """Return the nearest deterministic clear point outside a target box."""
    nodes = [node for node in stage.get("nodes", []) if isinstance(node, dict)]
    matches = [
        node
        for node in nodes
        if node.get("id") == target_name or effective_label(node) == target_name
    ]
    target = matches[0] if len(matches) == 1 else None
    avatar = next((node for node in nodes if node.get("id") == "avatar"), None)
    if len(matches) > 1:
        return None, f"multiple objects match label '{target_name}'"
    if target is None:
        return None, f"no object found with label '{target_name}'"
    if avatar is None:
        return None, "VR scene has no avatar"
    radius = max(float(avatar["width"]), float(avatar["depth"])) / 2
    half_width = float(target["width"]) / 2
    half_depth = float(target["depth"]) / 2
    x, y = float(target["x"]), float(target["y"])
    candidates = (
        WorldPoint(x, y - half_depth - radius - AGENT_CLEARANCE),
        WorldPoint(x + half_width + radius + AGENT_CLEARANCE, y),
        WorldPoint(x, y + half_depth + radius + AGENT_CLEARANCE),
        WorldPoint(x - half_width - radius - AGENT_CLEARANCE, y),
    )
    valid = [point for point in candidates if validate_vr_endpoint(stage, point)[0]]
    if not valid:
        return None, f"no clear stopping point is available near {target_name}"
    reachable = [
        point
        for point in valid
        if verify_vr_target_reach(stage, target_name, point=point)[0]
    ]
    if not reachable:
        closest = min(valid, key=WorldPoint(x, y).distance_to)
        _allowed, detail = verify_vr_target_reach(stage, target_name, point=closest)
        return (
            None,
            f"no stopping point near {target_name} satisfies tool reach ({detail})",
        )
    valid = reachable
    origin = WorldPoint(float(avatar["x"]), float(avatar["y"]))
    # Python's stable sort preserves north/east/south/west for exact ties.
    valid.sort(key=origin.distance_to)

    # A clear stopping point is not the same as a reachable one. The VR
    # document is a real floor plan, so the nearest candidate the agent can
    # actually walk to wins; a point standing behind a wall it cannot round
    # would otherwise be reported as arrival and unlock that object's tools on
    # the strength of a walk that never happened.
    routable, route_error = _first_routable(stage, target_name, origin, valid)
    if routable is not None:
        return routable, None
    return (
        None,
        route_error or f"no clear stopping point is available near {target_name}",
    )


def _first_routable(
    stage: dict[str, Any],
    target_name: str,
    origin: WorldPoint,
    candidates: list[WorldPoint],
) -> tuple[WorldPoint | None, str | None]:
    """Nearest candidate with an obstacle-aware route, or why there is none."""
    try:
        from .semantic_navigation import plan_route
        from .semantic_projection import ProjectionError, project_vr_stage

        projection = project_vr_stage(stage)
    except (ImportError, ProjectionError, ValueError) as exc:
        logger.warning(f"VR route planning unavailable; docking unverified: {exc}")
        return (candidates[0] if candidates else None), None

    target = projection.world.find(target_name)
    ignoring = frozenset({target.entity_id}) if target is not None else frozenset()
    last_reason: str | None = None
    for point in candidates:
        result = plan_route(
            projection.world,
            (origin.x, origin.y),
            (point.x, point.y),
            ignoring=ignoring,
        )
        if result.reachable:
            return point, None
        last_reason = result.reason
    return None, (
        f"no route to a stopping point near {target_name} ({last_reason})"
        if last_reason
        else None
    )


def validate_vr_stage(stage: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(stage, dict):
        return False, ["VR stage document must be an object."]
    if stage.get("version") != VR_STAGE_VERSION:
        errors.append(f"VR stage version must be {VR_STAGE_VERSION}.")
    if stage.get("mode") != "vr":
        errors.append("VR stage mode must be vr.")

    room = stage.get("room")
    room_width = room_depth = room_height = 0.0
    if not isinstance(room, dict):
        errors.append("VR stage room must be an object.")
    else:
        allowed = {"width", "depth", "height", "floor_color", "wall_color"}
        for key in room:
            if key not in allowed:
                errors.append(f"VR stage room.{key} is not allowed.")
        for key in ("width", "depth", "height"):
            value = room.get(key)
            if not _is_number(value) or value < 1 or value > 4096:
                errors.append(f"VR stage room.{key} must be 1-4096.")
        for key in ("floor_color", "wall_color"):
            if not _is_safe_color(room.get(key)):
                errors.append(f"VR stage room.{key} is invalid.")
        if all(_is_number(room.get(key)) for key in ("width", "depth", "height")):
            room_width = float(room["width"])
            room_depth = float(room["depth"])
            room_height = float(room["height"])

    world = stage.get("world")
    ceiling = REACH_CEILING
    if not isinstance(world, dict):
        errors.append("VR stage world must be an object.")
    else:
        limits = {
            "units_per_meter": (1, 10000),
            "reach_ceiling": (1, 4096),
            "move_speed": (1, 10000),
        }
        for key in world:
            if key not in limits:
                errors.append(f"VR stage world.{key} is not allowed.")
        for key, (low, high) in limits.items():
            value = world.get(key)
            if not _is_number(value) or not low <= value <= high:
                errors.append(f"VR stage world.{key} must be {low}-{high}.")
        if _is_number(world.get("reach_ceiling")):
            ceiling = float(world["reach_ceiling"])

    nodes = stage.get("nodes")
    avatar_count = 0
    seen_ids: set[str] = set()
    seen_labels: dict[str, str] = {}
    if not isinstance(nodes, list):
        errors.append("VR stage nodes must be an array.")
    elif len(nodes) > 80:
        errors.append("VR stage can contain at most 80 nodes.")
    else:
        import re

        allowed_keys = {
            "id",
            "type",
            "label",
            "x",
            "y",
            "width",
            "depth",
            "height",
            "elevation",
            "color",
            "texture",
            "stateTextures",
            "spritesheet",
            "uiWindow",
            "pose",
            "interaction",
            "reach",
            "yaw",
            "support_id",
        }
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                errors.append(f"VR node {index} must be an object.")
                continue
            node_id = node.get("id")
            if not isinstance(node_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,80}", node_id
            ):
                errors.append(f"VR node {index} id is invalid.")
                node_id = str(index)
            elif node_id in seen_ids:
                errors.append(f"VR node id {node_id} is duplicated.")
            else:
                seen_ids.add(node_id)
            for key in node:
                if key not in allowed_keys:
                    errors.append(f"VR node {node_id} {key} is not allowed.")
            node_type = node.get("type")
            if node_type not in {"furniture", "avatar", "prop"}:
                errors.append(f"VR node {node_id} type is invalid.")
            if node_id == "avatar":
                avatar_count += 1
                if node_type != "avatar":
                    errors.append("VR avatar node must use type avatar.")
                if node.get("uiWindow") is not None:
                    errors.append("VR avatar cannot have a furniture window.")
            elif node_type == "avatar":
                errors.append("Only the avatar id may use type avatar.")
            elif node.get("spritesheet") is not None:
                errors.append(f"VR furniture {node_id} cannot have a spritesheet.")

            interaction = node.get("interaction")
            if node_type == "prop":
                if not isinstance(interaction, dict) or interaction.get("grabbable") is not True:
                    errors.append(f"VR prop {node_id} must be explicitly grabbable.")
                elif any(key not in {"on_click", "grabbable"} for key in interaction):
                    errors.append(f"VR prop {node_id} interaction is invalid.")
                yaw = node.get("yaw", 0.0)
                if not _is_number(yaw) or not -180.0 <= float(yaw) <= 180.0:
                    errors.append(f"VR prop {node_id} yaw must be between -180 and 180.")
                support_id = node.get("support_id")
                if not isinstance(support_id, str) or not support_id or len(support_id) > 80:
                    errors.append(f"VR prop {node_id} support_id is invalid.")
                if node.get("reach") is not None or node.get("uiWindow") is not None:
                    errors.append(f"VR prop {node_id} cannot grant tools or host a furniture window.")
            else:
                if isinstance(interaction, dict) and interaction.get("grabbable") is not None:
                    errors.append(f"VR node {node_id} cannot be grabbable.")
                _validate_interaction(interaction, node_id, errors)

            label = effective_label(node)
            if label:
                folded = label.casefold()
                if folded in seen_labels:
                    errors.append(
                        f"VR node {node_id} label duplicates {seen_labels[folded]}."
                    )
                else:
                    seen_labels[folded] = node_id
            if not _is_safe_text(node.get("label"), LABEL_LIMIT):
                errors.append(f"VR node {node_id} label is unsafe.")
            for key in ("x", "y", "width", "depth", "height", "elevation"):
                if not _is_number(node.get(key)):
                    errors.append(f"VR node {node_id} {key} must be numeric.")
            if all(
                _is_number(node.get(key))
                for key in ("x", "y", "width", "depth", "height", "elevation")
            ):
                x, y = float(node["x"]), float(node["y"])
                width, depth, height = (
                    float(node["width"]),
                    float(node["depth"]),
                    float(node["height"]),
                )
                elevation = float(node["elevation"])
                if width <= 0 or depth <= 0 or height <= 0:
                    errors.append(f"VR node {node_id} dimensions must be positive.")
                if (
                    x - width / 2 < 0
                    or x + width / 2 > room_width
                    or y - depth / 2 < 0
                    or y + depth / 2 > room_depth
                ):
                    errors.append(
                        f"VR node {node_id} footprint must be inside the room."
                    )
                if elevation - height / 2 < 0 or elevation + height / 2 > room_height:
                    errors.append(f"VR node {node_id} height must be inside the room.")
            if not _is_safe_color(node.get("color")):
                errors.append(f"VR node {node_id} color is invalid.")
            _validate_texture(node.get("texture"), node_id, errors)
            _validate_state_textures(node.get("stateTextures"), node_id, errors)
            _validate_ui_window(node.get("uiWindow"), node_id, errors)
            _validate_spritesheet(node.get("spritesheet"), node_id, errors)
            _validate_reach(node.get("reach"), node_id, ceiling, errors)
            if node.get("pose") not in (None, *AVATAR_POSES):
                errors.append(f"VR node {node_id} pose is not supported.")
    if avatar_count != 1:
        errors.append("VR stage must contain exactly one avatar.")
    if not isinstance(stage.get("metadata"), dict):
        errors.append("VR stage metadata must be an object.")

    if isinstance(nodes, list):
        by_id = {
            node.get("id"): node
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
        for node in nodes:
            if not isinstance(node, dict) or node.get("type") != "prop":
                continue
            support_id = node.get("support_id")
            support = by_id.get(support_id)
            if support_id != "floor" and support is None:
                errors.append(f"VR prop {node.get('id')} support does not exist.")
                continue
            if support is not None and support.get("type") != "furniture":
                errors.append(f"VR prop {node.get('id')} support must be furniture.")
                continue
            numeric = all(
                _is_number(node.get(key))
                for key in ("x", "y", "width", "depth", "height", "elevation")
            )
            if not numeric:
                continue
            support_top = 0.0
            if support is not None:
                support_top = float(support["elevation"]) + float(support["height"]) / 2
                yaw_radians = math.radians(float(node.get("yaw", 0)))
                prop_half_x = (
                    abs(math.cos(yaw_radians)) * float(node["width"])
                    + abs(math.sin(yaw_radians)) * float(node["depth"])
                ) / 2
                prop_half_y = (
                    abs(math.sin(yaw_radians)) * float(node["width"])
                    + abs(math.cos(yaw_radians)) * float(node["depth"])
                ) / 2
                if (
                    abs(float(node["x"]) - float(support["x"])) + prop_half_x
                    > float(support["width"]) / 2 - 2
                    or abs(float(node["y"]) - float(support["y"])) + prop_half_y
                    > float(support["depth"]) / 2 - 2
                ):
                    errors.append(f"VR prop {node.get('id')} footprint exceeds its support.")
            expected_elevation = support_top + float(node["height"]) / 2
            if abs(float(node["elevation"]) - expected_elevation) > 1:
                errors.append(f"VR prop {node.get('id')} is not resting on its support.")

    if isinstance(nodes, list) and avatar_count == 1 and not errors:
        avatar = next(node for node in nodes if node.get("id") == "avatar")
        point = WorldPoint(float(avatar["x"]), float(avatar["y"]))
        for node in nodes:
            if node.get("id") != "avatar" and point_inside_node(point, node):
                errors.append(
                    f"VR avatar starts inside {effective_label(node) or node.get('id')}."
                )
                break
    return not errors, errors


def load_vr_stage_state(conf_uid: str) -> dict[str, Any]:
    if not conf_uid:
        return get_default_vr_stage()
    path = _vr_path(conf_uid)
    if not path.exists():
        return get_default_vr_stage(conf_uid)
    try:
        with path.open("r", encoding="utf-8") as handle:
            stage = json.load(handle)
        if isinstance(stage, dict) and stage.get("version") in {1, 2}:
            reset = get_default_vr_stage(conf_uid)
            reset["metadata"]["updated_by"] = "vr_v3_room_reset"
            saved, errors = save_vr_stage_state(
                conf_uid, reset, updated_by="vr_v3_room_reset"
            )
            if not saved:
                logger.warning(f"Failed to persist VR v3 room reset: {errors}")
            return reset
        valid, errors = validate_vr_stage(stage)
        if valid:
            return _backfill_browser_window(stage)
        logger.warning(f"Invalid VR stage state for {conf_uid}: {errors}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to load VR stage state for {conf_uid}: {exc}")
    return get_default_vr_stage(conf_uid)


def save_vr_stage_state(
    conf_uid: str,
    stage: dict[str, Any],
    updated_by: str = "frontend_action",
) -> tuple[bool, list[str]]:
    candidate = copy.deepcopy(stage)
    candidate.setdefault("metadata", {})
    candidate["metadata"].update(
        conf_uid=conf_uid,
        updated_at=_now_iso(),
        updated_by=updated_by,
    )
    valid, errors = validate_vr_stage(candidate)
    if not valid:
        return False, errors
    path = _vr_path(conf_uid)
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(candidate, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to save VR stage state for {conf_uid}: {exc}")
        return False, [str(exc)]
    return True, []


def reset_vr_stage_state(conf_uid: str) -> dict[str, Any]:
    stage = get_default_vr_stage(conf_uid)
    save_vr_stage_state(conf_uid, stage, updated_by="reset")
    return stage


def world_from_vr_stage(stage: dict[str, Any]) -> World:
    """Adapt the grounded VR document to the existing horizontal World loader."""
    room = stage.get("room", {})
    adapted = {
        "canvas": {"width": room.get("width", 0), "height": room.get("depth", 0)},
        "world": stage.get("world", {}),
        "nodes": [
            {
                **node,
                # In the common horizontal world, height means floor depth.
                "height": node.get("depth", 0),
            }
            for node in stage.get("nodes", [])
        ],
    }
    return world_from_stage(adapted)


def load_vr_world(conf_uid: str) -> World:
    return world_from_vr_stage(load_vr_stage_state(conf_uid))
