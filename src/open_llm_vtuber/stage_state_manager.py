import copy
import json
import os
import re
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from typing import Any

from loguru import logger

from .utils.timezone import now_local
from .world_model import (
    REACH_CEILING as WORLD_REACH_CEILING,
    World,
    warn_once,
    default_world_block,
    effective_label,
    world_from_stage,
)


# How long an object's name may be. The gate matches on it, `describe_room`
# reads it back to the model, and a click quotes it, so it is a name rather
# than a place to put prose.
LABEL_LIMIT = 80

STAGE_VERSION = 9
STAGE_BASE_DIR = "stage_state"
# Bumping this rebuilds every stored room from the defaults. v4 ships the
# full-HD room shell while retaining v3's collision-safe furniture layout.
# The renderer adds depth without changing these world coordinates.
STAGE_THEME_REVISION = "cozy-bedroom-v4"

# Live data sources a furniture hover window may render. Most are backed by a
# read-only backend route the frontend polls while the window is open;
# `room_layout` is rendered from the stage document the frontend already holds.
UI_WINDOW_PROVIDERS = (
    "google_calendar_status",
    "todo_list",
    "timer_status",
    "google_gmail_unread",
    "bookshelf",
    "reading_status",
    "plan_status",
    "room_layout",
    "skill_status",
    "drawing_board_image",
)

# Avatar postures. Anything else is rejected, and walking clears the pose, so a
# seated avatar can never be shown mid-stride.
AVATAR_POSES = ("standing", "sitting")


def _is_safe_filename(filename: str) -> bool:
    if not isinstance(filename, str) or not filename:
        return False
    try:
        if len(filename.encode("utf-8")) > 255:
            return False
    except UnicodeEncodeError:
        return False
    if filename != filename.strip() or filename.endswith("."):
        return False
    # Reject names that are only dots/spaces ("." / ".." / "  ").
    if filename.strip(". ") == "":
        return False
    # Reject path separators, drive colons, wildcards and control characters.
    if re.search(r'[\\/:*?"<>|\x00-\x1f]', filename):
        return False
    if PureWindowsPath(filename).is_reserved():
        return False
    return bool(re.fullmatch(r"[\w\-_\u0020-\u007E\u00A0-\uFFFF]+", filename))


def _sanitize_path_component(component: str) -> str:
    if not _is_safe_filename(component):
        raise ValueError(f"Invalid characters in path component: {component}")
    return component


def _ensure_stage_dir(conf_uid: str) -> str:
    safe_conf_uid = _sanitize_path_component(conf_uid)
    root = Path(STAGE_BASE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    base_dir = (root / safe_conf_uid).resolve()
    try:
        base_dir.relative_to(root)
    except ValueError as exc:
        raise ValueError("Invalid path: Path traversal detected") from exc
    base_dir.mkdir(parents=True, exist_ok=True)
    return str(base_dir)


def _get_stage_path(conf_uid: str) -> str:
    base_dir = Path(_ensure_stage_dir(conf_uid))
    full_path = (base_dir / "stage.json").resolve()
    try:
        full_path.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError("Invalid path: Path traversal detected") from exc
    return str(full_path)


def _now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


def get_default_stage(conf_uid: str | None = None) -> dict[str, Any]:
    now = _now_iso()
    return {
        "version": STAGE_VERSION,
        "canvas": {
            "width": 960,
            "height": 540,
            "background": "#e8c98f",
            "texture": {
                "src": "./stage-assets/bedroom-background-hd.png",
                "fit": "cover",
                "alt": "high-definition cozy illustrated companion studio",
            },
        },
        # What the drawing above actually means, for anything that is not
        # drawing it. See world_model.py; a node may override `reach`, which
        # is otherwise derived from its footprint.
        "world": default_world_block(),
        "nodes": [
            {
                "id": "computer",
                "type": "panel",
                "label": "computer",
                "x": 120,
                "y": 370,
                "width": 230,
                "height": 190,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "computer",
                "texture": {
                    "src": "./stage-assets/computer.png",
                    "fit": "contain",
                    "alt": "computer",
                },
                "style": {
                    "background": "rgba(79, 70, 229, 0.88)",
                    "borderColor": "rgba(199, 210, 254, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 14,
                    "fontWeight": "700",
                },
            },
            {
                "id": "calendar",
                "type": "panel",
                "label": "calendar",
                "x": 425,
                "y": 110,
                "width": 110,
                "height": 130,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "calendar",
                "texture": {
                    "src": "./stage-assets/calendar.png",
                    "fit": "contain",
                    "alt": "calendar",
                },
                "uiWindow": {
                    "title": "Google Calendar",
                    "body": "Connection status for the primary calendar.",
                    "provider": "google_calendar_status",
                },
                "style": {
                    "background": "rgba(13, 148, 136, 0.9)",
                    "borderColor": "rgba(153, 246, 228, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 14,
                    "fontWeight": "700",
                },
            },
            {
                "id": "bed",
                "type": "panel",
                "label": "bed",
                "x": 480,
                "y": 465,
                "width": 210,
                "height": 150,
                # Front band: drawn over the furniture standing behind it.
                "zIndex": 3,
                "interaction": {"on_click": True},
                "text": "bed",
                "texture": {
                    "src": "./stage-assets/bed.png",
                    "fit": "contain",
                    "alt": "bed",
                },
                "style": {
                    "background": "rgba(225, 29, 72, 0.86)",
                    "borderColor": "rgba(254, 205, 211, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 14,
                    "fontWeight": "700",
                },
            },
            {
                "id": "archive_shelf",
                "type": "panel",
                "label": "archive shelf",
                "x": 360,
                "y": 370,
                "width": 125,
                "height": 110,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "archive shelf",
                "texture": {
                    "src": "./stage-assets/archive-shelf.png",
                    "fit": "contain",
                    "alt": "archive shelf",
                },
                "style": {
                    "background": "rgba(146, 64, 14, 0.88)",
                    "borderColor": "rgba(254, 215, 170, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 13,
                    "fontWeight": "700",
                },
            },
            {
                "id": "mailbox",
                "type": "panel",
                "label": "mailbox",
                "x": 900,
                "y": 110,
                "width": 96,
                "height": 88,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "mailbox",
                "texture": {
                    "src": "./stage-assets/mailbox-empty.png",
                    "fit": "contain",
                    "alt": "mailbox",
                },
                "stateTextures": {
                    "unread": {
                        "src": "./stage-assets/mailbox-unread.png",
                        "fit": "contain",
                        "alt": "mailbox with unread mail",
                    }
                },
                "uiWindow": {
                    "title": "Unread Gmail",
                    "body": "Unread messages for the connected Google account.",
                    "provider": "google_gmail_unread",
                },
                "style": {
                    "background": "rgba(71, 85, 105, 0.9)",
                    "borderColor": "rgba(203, 213, 225, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 13,
                    "fontWeight": "700",
                },
            },
            {
                "id": "todo_list",
                "type": "panel",
                "label": "todo_list",
                "x": 265,
                "y": 110,
                "width": 105,
                "height": 125,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "todo list",
                "texture": {
                    "src": "./stage-assets/todo-list.png",
                    "fit": "contain",
                    "alt": "todo list",
                },
                "uiWindow": {
                    "title": "Todo list",
                    "body": "Open items the agent is tracking.",
                    "provider": "todo_list",
                },
                "style": {
                    "background": "rgba(217, 119, 6, 0.9)",
                    "borderColor": "rgba(253, 230, 138, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 14,
                    "fontWeight": "700",
                },
            },
            {
                "id": "hourglass",
                "type": "panel",
                "label": "hourglass",
                "x": 585,
                "y": 110,
                "width": 65,
                "height": 90,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "hourglass",
                "texture": {
                    "src": "./stage-assets/hourglass.png",
                    "fit": "contain",
                    "alt": "hourglass",
                },
                "uiWindow": {
                    "title": "Timers",
                    "body": "Timers the agent has set.",
                    "provider": "timer_status",
                },
                "style": {
                    "background": "rgba(124, 58, 237, 0.88)",
                    "borderColor": "rgba(221, 214, 254, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 13,
                    "fontWeight": "700",
                },
            },
            {
                "id": "window",
                "type": "panel",
                "label": "window",
                "x": 105,
                "y": 110,
                "width": 185,
                "height": 155,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "window",
                "texture": {
                    "src": "./stage-assets/window.png",
                    "fit": "contain",
                    "alt": "bedroom window",
                },
                "uiWindow": {
                    "title": "Window",
                    "body": (
                        "The agent checks the weather from here, using a public "
                        "forecast service that needs no account."
                    ),
                },
                "style": {
                    "background": "rgba(56, 189, 248, 0.85)",
                    "borderColor": "rgba(224, 242, 254, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 14,
                    "fontWeight": "700",
                },
            },
            {
                "id": "bookshelf",
                "type": "panel",
                "label": "bookshelf",
                "x": 600,
                "y": 370,
                "width": 120,
                "height": 150,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "bookshelf",
                "texture": {
                    "src": "./stage-assets/bookshelf.png",
                    "fit": "contain",
                    "alt": "bookshelf",
                },
                "uiWindow": {
                    "title": "Bookshelf",
                    "body": "Books the agent has shelved to read.",
                    "provider": "bookshelf",
                },
                "style": {
                    "background": "rgba(120, 53, 15, 0.9)",
                    "borderColor": "rgba(253, 230, 138, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 13,
                    "fontWeight": "700",
                },
            },
            {
                "id": "reading_chair",
                "type": "panel",
                "label": "reading chair",
                "x": 240,
                "y": 477,
                "width": 120,
                "height": 125,
                "zIndex": 3,
                "interaction": {"on_click": True},
                "text": "reading chair",
                "texture": {
                    "src": "./stage-assets/reading-chair.png",
                    "fit": "contain",
                    "alt": "reading chair",
                },
                "uiWindow": {
                    "title": "Reading chair",
                    "body": "The book being read, and the notes taken so far.",
                    "provider": "reading_status",
                },
                "style": {
                    "background": "rgba(47, 125, 117, 0.9)",
                    "borderColor": "rgba(204, 251, 241, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 13,
                    "fontWeight": "700",
                },
            },
            {
                "id": "writing_desk",
                "type": "panel",
                "label": "writing desk",
                "x": 840,
                "y": 370,
                "width": 150,
                "height": 120,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "writing desk",
                "texture": {
                    "src": "./stage-assets/writing-desk.png",
                    "fit": "contain",
                    "alt": "writing desk",
                },
                "uiWindow": {
                    "title": "Plans",
                    "body": "Plans the agent has drafted, awaiting your decision.",
                    "provider": "plan_status",
                },
                "style": {
                    "background": "rgba(180, 118, 62, 0.9)",
                    "borderColor": "rgba(254, 240, 204, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 13,
                    "fontWeight": "700",
                },
            },
            {
                "id": "sofa",
                "type": "panel",
                "label": "sofa",
                "x": 720,
                "y": 480,
                "width": 190,
                "height": 105,
                "zIndex": 3,
                "interaction": {"on_click": True},
                "text": "sofa",
                "texture": {
                    "src": "./stage-assets/sofa.png",
                    "fit": "contain",
                    "alt": "sofa",
                },
                "uiWindow": {
                    "title": "Sofa",
                    "body": (
                        "Where the agent sits when there is nothing to do. It "
                        "still hears you, and walking anywhere stands it up."
                    ),
                },
                "style": {
                    "background": "rgba(124, 74, 99, 0.9)",
                    "borderColor": "rgba(245, 214, 228, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 13,
                    "fontWeight": "700",
                },
            },
            {
                "id": "design_board",
                "type": "panel",
                "label": "design board",
                "x": 745,
                "y": 110,
                "width": 120,
                "height": 110,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "design board",
                "texture": {
                    "src": "./stage-assets/design-board.png",
                    "fit": "contain",
                    "alt": "design board",
                },
                "uiWindow": {
                    "title": "Room layout",
                    "body": "Every object in the room, and which are fixed.",
                    "provider": "room_layout",
                },
                "style": {
                    "background": "rgba(146, 100, 46, 0.9)",
                    "borderColor": "rgba(254, 226, 176, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 13,
                    "fontWeight": "700",
                },
            },
            {
                "id": "tool_rack",
                "type": "panel",
                "label": "tool rack",
                # The one wall spot left that keeps its own 150-unit radius.
                "x": 503,
                "y": 245,
                "width": 120,
                "height": 100,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "tool rack",
                "texture": {
                    "src": "./stage-assets/tool-rack.png",
                    "fit": "contain",
                    "alt": "tool rack",
                },
                "uiWindow": {
                    "title": "Skills",
                    "body": "Skills the agent has written for itself, and drafts waiting on you.",
                    "provider": "skill_status",
                },
                "style": {
                    "background": "rgba(75, 85, 99, 0.9)",
                    "borderColor": "rgba(226, 232, 240, 0.9)",
                    "color": "#ffffff",
                    "borderRadius": 8,
                    "fontSize": 13,
                    "fontWeight": "700",
                },
            },
            {
                "id": "drawing_board",
                "type": "panel",
                "label": "drawing_board",
                # Wall-mounted between the design board and the back-row
                # furniture. It stays more than the 150-unit safety ceiling
                # from every other tool-gated object.
                "x": 720,
                "y": 265,
                "width": 140,
                "height": 100,
                "zIndex": 2,
                "interaction": {"on_click": True},
                "text": "drawing board",
                "uiWindow": {
                    "title": "Drawing Board",
                    "body": "The latest image generated by the agent.",
                    "provider": "drawing_board_image",
                },
                "style": {
                    "background": "rgba(244, 234, 213, 0.96)",
                    "borderColor": "#8a5a32",
                    "color": "#4b3423",
                    "borderRadius": 3,
                    "fontSize": 11,
                    "fontWeight": "800",
                },
            },
            {
                "id": "avatar",
                "type": "avatar",
                "label": "avatar",
                # Standing clear of every object's radius, so the first thing the
                # agent does with any object-bound tool is walk to it.
                "x": 60,
                "y": 515,
                "width": 70,
                "height": 92,
                # Above the front band, so the avatar is never hidden by furniture.
                "zIndex": 4,
                "interaction": {"on_click": True},
                "text": "avatar",
                "spritesheet": {
                    "src": "./stage-assets/avatar.png",
                    "frameWidth": 48,
                    "frameHeight": 64,
                    "fps": 6,
                    "defaultAnimations": {
                        "idle": "idle_down",
                        "walk": "walk_down",
                    },
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
                "style": {
                    "background": "#2563eb",
                    "borderColor": "rgba(191, 219, 254, 0.9)",
                    "color": "#172554",
                    "fontSize": 12,
                    "fontWeight": "700",
                },
            },
        ],
        "metadata": {
            "conf_uid": conf_uid,
            "created_at": now,
            "updated_at": now,
            "updated_by": "default",
            "theme_revision": STAGE_THEME_REVISION,
        },
    }


@lru_cache(maxsize=1)
def _canonical_node_ids() -> frozenset[str]:
    """The ids the shipped room defines, which never change during a run.

    Deriving these built a whole default stage — fifteen nodes and a timestamp
    — on every `load_stage_state`, which the proximity gate calls several times
    a turn.
    """
    return frozenset(node["id"] for node in get_default_stage()["nodes"])


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_safe_color(value: Any) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, str)
        and len(value) <= 180
        and re.match(r"^(#|rgb|rgba|hsl|hsla|linear-gradient|[a-zA-Z])", value)
        is not None
    )


def _is_safe_text(value: Any, max_length: int = 500) -> bool:
    return value is None or (isinstance(value, str) and len(value) <= max_length)


def _is_safe_asset_src(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 1200:
        return False
    if re.match(r"^javascript:", value, re.IGNORECASE):
        return False
    return (
        re.match(r"^https?://", value, re.IGNORECASE) is not None
        or re.match(
            r"^data:image/(?:png|jpeg|jpg|webp|gif|svg\+xml);base64,",
            value,
            re.IGNORECASE,
        )
        is not None
        or re.match(
            r"^(?:\.{0,2}/)?[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*$",
            value,
        )
        is not None
    )


def _uses_default_sheet(node: dict[str, Any], default_node: dict[str, Any]) -> bool:
    """True when a node's spritesheet points at the image the default room ships."""
    stored = node.get("spritesheet")
    shipped = default_node.get("spritesheet")
    if not isinstance(stored, dict) or not isinstance(shipped, dict):
        return False
    return stored.get("src") == shipped.get("src")


def migrate_stage(stage: Any) -> Any:
    """Return a current stage document, preserving existing layout and assets.

    Every supported older version also gains the furniture introduced by newer
    default rooms: a tool stays hidden while the object it is bound to is absent
    from the scene, so a room saved before the object shipped would permanently
    lock the agent out of those tools.
    """
    if not isinstance(stage, dict) or stage.get("version") == STAGE_VERSION:
        return stage
    if stage.get("version") not in {1, 2, 3, 4, 5, 6, 7, 8}:
        return stage

    metadata = stage.get("metadata") if isinstance(stage.get("metadata"), dict) else {}
    defaults = get_default_stage(metadata.get("conf_uid"))
    default_nodes = {node["id"]: node for node in defaults["nodes"]}
    nodes: list[Any] = []

    if isinstance(stage.get("nodes"), list):
        for node in stage["nodes"]:
            if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                nodes.append(node)
                continue
            default_node = default_nodes.get(node["id"])
            if not default_node:
                nodes.append(node)
                continue
            migrated_node = {
                **copy.deepcopy(default_node),
                **copy.deepcopy(node),
                "style": {
                    **copy.deepcopy(default_node.get("style", {})),
                    **(
                        copy.deepcopy(node.get("style", {}))
                        if isinstance(node.get("style"), dict)
                        else {}
                    ),
                },
            }
            if "texture" not in migrated_node and "texture" in default_node:
                migrated_node["texture"] = copy.deepcopy(default_node["texture"])
            if "spritesheet" not in migrated_node and "spritesheet" in default_node:
                migrated_node["spritesheet"] = copy.deepcopy(
                    default_node["spritesheet"]
                )
            elif _uses_default_sheet(migrated_node, default_node):
                # The stored animation map describes the shipped image file, and
                # that file gained rows (a seated pose) with this version. A
                # custom sheet — any other src — keeps its own map untouched.
                migrated_node["spritesheet"] = copy.deepcopy(
                    default_node["spritesheet"]
                )
            if "stateTextures" not in migrated_node and "stateTextures" in default_node:
                migrated_node["stateTextures"] = copy.deepcopy(
                    default_node["stateTextures"]
                )
            nodes.append(migrated_node)
    else:
        nodes = copy.deepcopy(defaults["nodes"])

    existing_ids = {
        node.get("id")
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    for node_id, default_node in default_nodes.items():
        if node_id not in existing_ids:
            nodes.append(copy.deepcopy(default_node))

    return {
        **defaults,
        **stage,
        "version": STAGE_VERSION,
        "canvas": {
            **defaults["canvas"],
            **(stage.get("canvas") if isinstance(stage.get("canvas"), dict) else {}),
        },
        # Merged rather than replaced, so a document written before the world
        # block existed gains every setting instead of only the ones it names.
        "world": {
            **defaults["world"],
            **(stage.get("world") if isinstance(stage.get("world"), dict) else {}),
        },
        "nodes": nodes,
        "metadata": {
            **defaults["metadata"],
            **metadata,
            "updated_at": _now_iso(),
            "updated_by": "migration",
        },
    }


def _validate_texture(texture: Any, node_id: str, errors: list[str]) -> None:
    if texture is None:
        return
    if not isinstance(texture, dict):
        errors.append(f"Node {node_id} texture must be an object.")
        return
    allowed_keys = {"src", "fit", "alt"}
    for key in texture:
        if key not in allowed_keys:
            errors.append(f"Node {node_id} texture.{key} is not allowed.")
    if not _is_safe_asset_src(texture.get("src")):
        errors.append(f"Node {node_id} texture.src is invalid.")
    if texture.get("fit") not in (None, "contain", "cover"):
        errors.append(f"Node {node_id} texture.fit must be contain or cover.")
    if not _is_safe_text(texture.get("alt"), 160):
        errors.append(f"Node {node_id} texture.alt contains unsafe text length.")


def _validate_state_textures(
    state_textures: Any, node_id: str, errors: list[str]
) -> None:
    if state_textures is None:
        return
    if not isinstance(state_textures, dict):
        errors.append(f"Node {node_id} stateTextures must be an object.")
        return
    for key, texture in state_textures.items():
        if key != "unread":
            errors.append(f"Node {node_id} stateTextures.{key} is not supported.")
            continue
        _validate_texture(texture, f"{node_id} stateTextures.{key}", errors)


def _validate_world(world: Any, errors: list[str]) -> float:
    """Check the renderer-agnostic block and return the reach ceiling in force.

    Absent is fine: the world model falls back to its own defaults, which are
    the values the room has always been gated on.
    """
    if world is None:
        return WORLD_REACH_CEILING
    if not isinstance(world, dict):
        errors.append("Stage world must be an object.")
        return WORLD_REACH_CEILING

    limits = {
        "units_per_meter": (1.0, 10000.0),
        "reach_ceiling": (1.0, 4096.0),
        "move_speed": (1.0, 10000.0),
    }
    for key in world:
        if key not in limits:
            errors.append(f"Stage world.{key} is not allowed.")

    for key, (low, high) in limits.items():
        if key not in world:
            continue
        value = world[key]
        if not _is_number(value) or value < low or value > high:
            errors.append(f"Stage world.{key} must be {low:g}-{high:g}.")

    ceiling = world.get("reach_ceiling")
    if _is_number(ceiling) and 1.0 <= ceiling <= 4096.0:
        return float(ceiling)
    return WORLD_REACH_CEILING


def _validate_reach(
    reach: Any, node_id: str, ceiling: float, errors: list[str]
) -> None:
    """A node may shrink its own reach but never claim more than the ceiling.

    This is the rule that keeps the room's spatial gate honest: without it a
    placed object could declare a reach spanning the room and hand the agent
    every other object's tools from where it stands.
    """
    if reach is None:
        return
    if not _is_number(reach) or reach <= 0:
        errors.append(f"Node {node_id} reach must be a positive number.")
        return
    if reach > ceiling:
        errors.append(f"Node {node_id} reach cannot exceed {ceiling:g}.")


def _validate_interaction(interaction: Any, node_id: str, errors: list[str]) -> None:
    """Allow only `on_click`, and only as a flag.

    The prompt for a click is composed on the backend from the stored label, so
    the stage document deliberately cannot carry any text for it: a room object
    must not be able to put words into a conversation.
    """
    if interaction is None:
        return
    if not isinstance(interaction, dict):
        errors.append(f"Node {node_id} interaction must be an object.")
        return
    for key, value in interaction.items():
        if key != "on_click":
            errors.append(f"Node {node_id} interaction.{key} is not allowed.")
        elif not isinstance(value, bool):
            errors.append(f"Node {node_id} interaction.on_click must be true or false.")


def _validate_ui_window(ui_window: Any, node_id: str, errors: list[str]) -> None:
    if ui_window is None:
        return
    if not isinstance(ui_window, dict):
        errors.append(f"Node {node_id} uiWindow must be an object.")
        return
    allowed_keys = {"title", "body", "provider"}
    for key in ui_window:
        if key not in allowed_keys:
            errors.append(f"Node {node_id} uiWindow.{key} is not allowed.")
    title = ui_window.get("title")
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > 120
        or re.search(r"[<>]", title)
    ):
        errors.append(f"Node {node_id} uiWindow.title must contain 1-120 characters.")
    body = ui_window.get("body")
    if not _is_safe_text(body, 500) or (
        isinstance(body, str) and re.search(r"[<>]", body)
    ):
        errors.append(f"Node {node_id} uiWindow.body contains unsafe text.")
    provider = ui_window.get("provider")
    if provider not in (None, *UI_WINDOW_PROVIDERS):
        errors.append(f"Node {node_id} uiWindow.provider is not supported.")


def _validate_spritesheet(spritesheet: Any, node_id: str, errors: list[str]) -> None:
    if spritesheet is None:
        return
    if not isinstance(spritesheet, dict):
        errors.append(f"Node {node_id} spritesheet must be an object.")
        return

    allowed_keys = {
        "src",
        "frameWidth",
        "frameHeight",
        "fps",
        "animations",
        "defaultAnimations",
    }
    for key in spritesheet:
        if key not in allowed_keys:
            errors.append(f"Node {node_id} spritesheet.{key} is not allowed.")
    if not _is_safe_asset_src(spritesheet.get("src")):
        errors.append(f"Node {node_id} spritesheet.src is invalid.")
    frame_width = spritesheet.get("frameWidth")
    frame_height = spritesheet.get("frameHeight")
    fps = spritesheet.get("fps")
    if not _is_number(frame_width) or frame_width < 1 or frame_width > 1024:
        errors.append(f"Node {node_id} spritesheet.frameWidth must be 1-1024.")
    if not _is_number(frame_height) or frame_height < 1 or frame_height > 1024:
        errors.append(f"Node {node_id} spritesheet.frameHeight must be 1-1024.")
    if fps is not None and (not _is_number(fps) or fps < 1 or fps > 30):
        errors.append(f"Node {node_id} spritesheet.fps must be 1-30.")

    animations = spritesheet.get("animations")
    if not isinstance(animations, dict) or not animations:
        errors.append(
            f"Node {node_id} spritesheet.animations must be a non-empty object."
        )
    elif len(animations) > 64:
        errors.append(
            f"Node {node_id} spritesheet.animations can contain at most 64 animations."
        )
    else:
        for name, animation in animations.items():
            if not isinstance(name, str) or not re.match(
                r"^[A-Za-z0-9_-]{1,80}$", name
            ):
                errors.append(
                    f"Node {node_id} spritesheet animation name {name} is invalid."
                )
            if not isinstance(animation, dict):
                errors.append(
                    f"Node {node_id} spritesheet animation {name} must be an object."
                )
                continue
            row = animation.get("row")
            frames = animation.get("frames")
            if not _is_number(row) or row < 0 or row > 256 or int(row) != row:
                errors.append(
                    f"Node {node_id} spritesheet animation {name}.row must be 0-256."
                )
            if not isinstance(frames, list) or not frames or len(frames) > 120:
                errors.append(
                    f"Node {node_id} spritesheet animation {name}.frames must contain 1-120 frames."
                )
            else:
                for frame in frames:
                    if (
                        not _is_number(frame)
                        or frame < 0
                        or frame > 256
                        or int(frame) != frame
                    ):
                        errors.append(
                            f"Node {node_id} spritesheet animation {name}.frames contains an invalid frame."
                        )

    default_animations = spritesheet.get("defaultAnimations")
    if default_animations is not None:
        if not isinstance(default_animations, dict):
            errors.append(
                f"Node {node_id} spritesheet.defaultAnimations must be an object."
            )
        else:
            for key, value in default_animations.items():
                if (
                    key not in {"idle", "walk"}
                    or not isinstance(value, str)
                    or not re.match(r"^[A-Za-z0-9_-]{1,80}$", value)
                ):
                    errors.append(
                        f"Node {node_id} spritesheet.defaultAnimations.{key} is invalid."
                    )


def validate_stage(stage: Any) -> tuple[bool, list[str]]:
    stage = migrate_stage(stage)
    errors: list[str] = []
    if not isinstance(stage, dict):
        return False, ["Stage document must be an object."]

    if stage.get("version") != STAGE_VERSION:
        errors.append(f"Stage version must be {STAGE_VERSION}.")

    canvas = stage.get("canvas")
    if not isinstance(canvas, dict):
        errors.append("Stage canvas must be an object.")
    else:
        width = canvas.get("width")
        height = canvas.get("height")
        if not _is_number(width) or width < 320 or width > 4096:
            errors.append("Canvas width must be 320-4096.")
        if not _is_number(height) or height < 240 or height > 4096:
            errors.append("Canvas height must be 240-4096.")
        if not _is_safe_color(canvas.get("background")):
            errors.append("Canvas background is invalid.")
        _validate_texture(canvas.get("texture"), "canvas", errors)

    reach_ceiling = _validate_world(stage.get("world"), errors)

    nodes = stage.get("nodes")
    if not isinstance(nodes, list):
        errors.append("Stage nodes must be an array.")
    elif len(nodes) > 80:
        errors.append("Stage can contain at most 80 nodes.")
    else:
        seen_ids: set[str] = set()
        # Tools are gated on an object's *label*, so two objects answering to
        # one name would let a second "computer" placed next to the agent
        # unlock the real computer's tools without the walk. Labels are
        # therefore unique, compared the way a careless collision would look
        # rather than byte for byte.
        seen_labels: dict[str, str] = {}
        valid_types = {"shape", "text", "image", "avatar", "panel"}
        allowed_style = {
            "background",
            "borderColor",
            "color",
            "borderRadius",
            "opacity",
            "fontSize",
            "fontWeight",
        }
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                errors.append(f"Node {index} must be an object.")
                continue
            node_id = node.get("id")
            if not isinstance(node_id, str) or not re.match(
                r"^[A-Za-z0-9_-]{1,80}$", node_id
            ):
                errors.append(f"Node {index} id is invalid.")
            elif node_id in seen_ids:
                errors.append(f"Node id {node_id} is duplicated.")
            else:
                seen_ids.add(node_id)
            label = effective_label(node)
            if label is not None:
                key = label.casefold()
                if key in seen_labels:
                    errors.append(
                        f"Node {node_id or index} label duplicates "
                        f"{seen_labels[key]}; object labels must be unique."
                    )
                else:
                    seen_labels[key] = str(node_id or index)
            if node.get("type") not in valid_types:
                errors.append(f"Node {node_id or index} type is invalid.")
            for key in ["x", "y", "width", "height"]:
                if not _is_number(node.get(key)):
                    errors.append(f"Node {node_id or index} {key} must be numeric.")
            style = node.get("style")
            # A label is read back out into the model's context by
            # `describe_room`, so an unbounded one is a way to spend the whole
            # context window on a single object. Matches the frontend's rule.
            if (
                not _is_safe_text(node.get("label"), LABEL_LIMIT)
                or not _is_safe_text(node.get("text"))
                or not _is_safe_text(node.get("alt"), 160)
            ):
                errors.append(f"Node {node_id or index} contains unsafe text length.")
            if node.get("src") is not None and not _is_safe_asset_src(node.get("src")):
                errors.append(f"Node {node_id or index} src is invalid.")
            _validate_texture(node.get("texture"), str(node_id or index), errors)
            _validate_state_textures(
                node.get("stateTextures"), str(node_id or index), errors
            )
            _validate_ui_window(node.get("uiWindow"), str(node_id or index), errors)
            _validate_spritesheet(
                node.get("spritesheet"), str(node_id or index), errors
            )
            _validate_interaction(
                node.get("interaction"), str(node_id or index), errors
            )
            _validate_reach(
                node.get("reach"), str(node_id or index), reach_ceiling, errors
            )
            if node.get("pose") not in (None, *AVATAR_POSES):
                errors.append(f"Node {node_id or index} pose is not supported.")
            if style is not None:
                if not isinstance(style, dict):
                    errors.append(f"Node {node_id or index} style must be an object.")
                else:
                    for key in style:
                        if key not in allowed_style:
                            errors.append(
                                f"Node {node_id or index} style.{key} is not allowed."
                            )
                    for key in ["background", "borderColor", "color"]:
                        if not _is_safe_color(style.get(key)):
                            errors.append(
                                f"Node {node_id or index} style.{key} is invalid."
                            )

    if not isinstance(stage.get("metadata"), dict):
        errors.append("Stage metadata must be an object.")

    return len(errors) == 0, errors


def load_stage_state(conf_uid: str) -> dict[str, Any]:
    if not conf_uid:
        return get_default_stage()

    filepath = _get_stage_path(conf_uid)
    if not os.path.exists(filepath):
        return get_default_stage(conf_uid)

    try:
        with open(filepath, "r", encoding="utf-8") as file:
            stage = json.load(file)
        stage = _repair_stage(migrate_stage(stage))
        metadata = stage.get("metadata")
        if (
            not isinstance(metadata, dict)
            or metadata.get("theme_revision") != STAGE_THEME_REVISION
        ):
            stage = get_default_stage(conf_uid)
            save_stage_state(conf_uid, stage, updated_by="theme_reset")
            return stage
        ok, errors = validate_stage(stage)
        if ok:
            stage.setdefault("metadata", {})
            stage["metadata"]["conf_uid"] = conf_uid
            return stage
        logger.warning(f"Invalid stage state for {conf_uid}: {errors}")
    except Exception as e:
        logger.error(f"Failed to load stage state for {conf_uid}: {e}")

    return get_default_stage(conf_uid)


def _repair_stage(stage: Any) -> Any:
    """Make a stored room loadable, keeping as much of the layout as possible.

    Names became unique, and bounded, only once tools were gated on them and
    `describe_room` began reading them back to the model. A room saved before
    those rules could hold two `computer`s or a label the length of a chapter,
    and any room can be hand-edited into one `validate_stage` refuses. Refusing
    to *load* it would cost the user their whole layout over something they are
    not doing wrong, so it is repaired here instead — the alternative is
    replacing sixteen good objects because of one bad field.

    Saving a bad room is still refused outright, so nothing silently renames an
    object out from under the agent that just placed it.

    Note the repair is not written back, so the file on disk keeps whatever it
    had; each load repeats this. That is deliberate — loading is not the place
    to rewrite a user's file — and it is why the warning below fires once.
    """
    if not isinstance(stage, dict) or not isinstance(stage.get("nodes"), list):
        return stage

    taken: set[str] = set()
    repairs: list[str] = []

    # A node with no usable id cannot be repaired: nothing can reference it,
    # no tool can be gated on it, and the renderer keys on that id. Dropping
    # the one corrupt entry keeps the other sixteen, where leaving it in means
    # validation rejects the document and the whole layout is replaced.
    keep = [
        node
        for node in stage["nodes"]
        if isinstance(node, dict) and isinstance(node.get("id"), str) and node["id"]
    ]
    if len(keep) != len(stage["nodes"]):
        repairs.append(
            f"dropped {len(stage['nodes']) - len(keep)} unidentifiable node(s)"
        )
        stage["nodes"] = keep

    # Overlong or non-string text next: the uniqueness pass below assigns
    # names, and it must not hand out one the validator would then reject.
    for node in stage["nodes"]:
        for field, limit in (("label", LABEL_LIMIT), ("text", 500), ("alt", 160)):
            value = node.get(field)
            if value is None or _is_safe_text(value, limit):
                continue
            node[field] = str(value)[:limit] if isinstance(value, str) else None
            repairs.append(f"{field} of {node.get('id')!r} shortened")

    # The shipped furniture keeps its name; anything else gives way to it.
    canonical = _canonical_node_ids()
    ordered = sorted(stage["nodes"], key=lambda node: node["id"] not in canonical)
    for node in ordered:
        # The effective name, so a node whose label was blanked is still seen
        # answering to its id — otherwise a decoy could share that name, and
        # the world model would lock both objects' tools forever.
        label = effective_label(node)
        if label is None:
            continue
        if label.casefold() not in taken:
            taken.add(label.casefold())
            continue
        for suffix in range(2, 100):
            # Truncated to leave room for the suffix, so repairing a room can
            # never produce a label the validator would then reject.
            marker = f" {suffix}"
            candidate = label[: LABEL_LIMIT - len(marker)].rstrip() + marker
            if candidate.casefold() not in taken:
                node["label"] = candidate
                taken.add(candidate.casefold())
                repairs.append(f"{label!r} -> {candidate!r}")
                break

    if repairs:
        # The repair is not written back, so a stored room reports the same
        # thing on every load. Say it once.
        warn_once(tuple(repairs), f"Stage: repaired stored room: {repairs}")
    return stage


def load_world(conf_uid: str) -> World:
    """Load the persisted room as the renderer-agnostic world."""
    return world_from_stage(load_stage_state(conf_uid))


def get_reachable_objects(
    conf_uid: str,
    distance: float | None = None,
) -> list[str]:
    """Return the labels of objects the persisted avatar can currently use.

    Each object decides its own reach from its footprint, so a bed is usable
    from further away than an hourglass. ``distance`` overrides that for
    callers asking a different question ("what is within 300 units?"); the
    proximity gate itself always passes None.
    """
    if not conf_uid:
        raise ValueError("conf_uid cannot be empty")
    if distance is not None:
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            raise ValueError("distance must be numeric")
        if distance < 0:
            raise ValueError("distance cannot be negative")
        distance = float(distance)

    return load_world(conf_uid).reachable_labels(within=distance)


def save_stage_state(
    conf_uid: str, stage: dict[str, Any], updated_by: str = "frontend_action"
) -> tuple[bool, list[str]]:
    if not conf_uid:
        return False, ["conf_uid cannot be empty."]

    stage_to_save = migrate_stage(copy.deepcopy(stage))
    stage_to_save.setdefault("metadata", {})
    stage_to_save["metadata"]["conf_uid"] = conf_uid
    stage_to_save["metadata"]["updated_at"] = _now_iso()
    stage_to_save["metadata"]["updated_by"] = updated_by

    ok, errors = validate_stage(stage_to_save)
    if not ok:
        return False, errors

    filepath = _get_stage_path(conf_uid)
    try:
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(stage_to_save, file, ensure_ascii=False, indent=2)
        return True, []
    except Exception as e:
        logger.error(f"Failed to save stage state for {conf_uid}: {e}")
        return False, [str(e)]


def reset_stage_state(conf_uid: str) -> dict[str, Any]:
    stage = get_default_stage(conf_uid)
    save_stage_state(conf_uid, stage, updated_by="reset")
    return stage
