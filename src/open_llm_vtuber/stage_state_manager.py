import copy
import json
import math
import os
import re
from datetime import datetime
from typing import Any

from loguru import logger


STAGE_VERSION = 5
STAGE_BASE_DIR = "stage_state"

# Live data sources a furniture hover window may render. Each one is backed by a
# read-only backend route the frontend polls while the window is open.
UI_WINDOW_PROVIDERS = (
    "google_calendar_status",
    "todo_list",
    "timer_status",
    "google_gmail_unread",
)


def _is_safe_filename(filename: str) -> bool:
    if not filename or len(filename) > 255:
        return False
    # Reject names that are only dots/spaces ("." / ".." / "  ").
    if filename.strip(". ") == "":
        return False
    # Reject path separators, drive colons, wildcards and control characters.
    if re.search(r'[\\/:*?"<>|\x00-\x1f]', filename):
        return False
    return bool(re.match(r"^[\w\-_\u0020-\u007E\u00A0-\uFFFF]+$", filename))


def _sanitize_path_component(component: str) -> str:
    sanitized = os.path.basename(component.strip())
    if not _is_safe_filename(sanitized):
        raise ValueError(f"Invalid characters in path component: {component}")
    return sanitized


def _ensure_stage_dir(conf_uid: str) -> str:
    safe_conf_uid = _sanitize_path_component(conf_uid)
    base_dir = os.path.join(STAGE_BASE_DIR, safe_conf_uid)
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def _get_stage_path(conf_uid: str) -> str:
    base_dir = _ensure_stage_dir(conf_uid)
    full_path = os.path.normpath(os.path.join(base_dir, "stage.json"))
    if not full_path.startswith(base_dir):
        raise ValueError("Invalid path: Path traversal detected")
    return full_path


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_default_stage(conf_uid: str | None = None) -> dict[str, Any]:
    now = _now_iso()
    return {
        "version": STAGE_VERSION,
        "canvas": {
            "width": 960,
            "height": 540,
            "background": "linear-gradient(180deg, #d7ecf3 0%, #d7ecf3 48%, #c6a983 48%, #c6a983 100%)",
            "theme": {
                "floorLine": "rgba(88, 67, 48, 0.32)",
                "floorStripe": "rgba(104, 75, 46, 0.16)",
            },
        },
        "nodes": [
            {
                "id": "computer",
                "type": "panel",
                "label": "computer",
                "x": 260,
                "y": 255,
                "width": 150,
                "height": 104,
                "zIndex": 2,
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
                "x": 480,
                "y": 420,
                "width": 118,
                "height": 92,
                "zIndex": 2,
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
                "x": 700,
                "y": 270,
                "width": 188,
                "height": 118,
                "zIndex": 2,
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
                "x": 300,
                "y": 430,
                "width": 125,
                "height": 110,
                "zIndex": 2,
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
                "x": 830,
                "y": 430,
                "width": 96,
                "height": 88,
                "zIndex": 2,
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
                "x": 110,
                "y": 420,
                "width": 112,
                "height": 132,
                "zIndex": 2,
                "text": "todo list",
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
                "x": 860,
                "y": 150,
                "width": 72,
                "height": 100,
                "zIndex": 2,
                "text": "hourglass",
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
                "x": 110,
                "y": 140,
                "width": 140,
                "height": 96,
                "zIndex": 2,
                "text": "window",
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
                "id": "avatar",
                "type": "avatar",
                "label": "avatar",
                "x": 480,
                "y": 270,
                "width": 70,
                "height": 92,
                "zIndex": 3,
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
        },
    }


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


def migrate_stage(stage: Any) -> Any:
    """Return a v5 stage document, preserving existing layout and assets.

    Every supported older version also gains the furniture introduced by newer
    default rooms: a tool stays hidden while the object it is bound to is absent
    from the scene, so a room saved before the object shipped would permanently
    lock the agent out of those tools.
    """
    if not isinstance(stage, dict) or stage.get("version") == STAGE_VERSION:
        return stage
    if stage.get("version") not in {1, 2, 3, 4}:
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
        errors.append("Stage version must be 5.")

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

    nodes = stage.get("nodes")
    if not isinstance(nodes, list):
        errors.append("Stage nodes must be an array.")
    elif len(nodes) > 80:
        errors.append("Stage can contain at most 80 nodes.")
    else:
        seen_ids: set[str] = set()
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
            if node.get("type") not in valid_types:
                errors.append(f"Node {node_id or index} type is invalid.")
            for key in ["x", "y", "width", "height"]:
                if not _is_number(node.get(key)):
                    errors.append(f"Node {node_id or index} {key} must be numeric.")
            style = node.get("style")
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
        stage = migrate_stage(stage)
        ok, errors = validate_stage(stage)
        if ok:
            stage.setdefault("metadata", {})
            stage["metadata"]["conf_uid"] = conf_uid
            return stage
        logger.warning(f"Invalid stage state for {conf_uid}: {errors}")
    except Exception as e:
        logger.error(f"Failed to load stage state for {conf_uid}: {e}")

    return get_default_stage(conf_uid)


def get_reachable_objects(
    conf_uid: str,
    distance: float = 150.0,
) -> list[str]:
    """Return object labels within ``distance`` of the persisted avatar."""
    if not conf_uid:
        raise ValueError("conf_uid cannot be empty")
    if isinstance(distance, bool) or not isinstance(distance, (int, float)):
        raise ValueError("distance must be numeric")
    if distance < 0:
        raise ValueError("distance cannot be negative")

    nodes = load_stage_state(conf_uid).get("nodes", [])
    avatar = next(
        (node for node in nodes if node.get("id") == "avatar"),
        None,
    )
    if avatar is None:
        return []

    ax, ay = avatar["x"], avatar["y"]
    return [
        node.get("label") or node.get("id")
        for node in nodes
        if node.get("id") != "avatar"
        and math.dist((ax, ay), (node["x"], node["y"])) <= float(distance)
    ]


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
