import copy
import json
import os
import re
from datetime import datetime
from typing import Any

from loguru import logger


STAGE_VERSION = 1
STAGE_BASE_DIR = "stage_state"


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
                "id": "avatar",
                "type": "avatar",
                "label": "avatar",
                "x": 480,
                "y": 270,
                "width": 70,
                "height": 92,
                "zIndex": 3,
                "text": "avatar",
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


def validate_stage(stage: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(stage, dict):
        return False, ["Stage document must be an object."]

    if stage.get("version") != STAGE_VERSION:
        errors.append("Stage version must be 1.")

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
            if not isinstance(node_id, str) or not re.match(r"^[A-Za-z0-9_-]{1,80}$", node_id):
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
            if style is not None:
                if not isinstance(style, dict):
                    errors.append(f"Node {node_id or index} style must be an object.")
                else:
                    for key in style:
                        if key not in allowed_style:
                            errors.append(f"Node {node_id or index} style.{key} is not allowed.")
                    for key in ["background", "borderColor", "color"]:
                        if not _is_safe_color(style.get(key)):
                            errors.append(f"Node {node_id or index} style.{key} is invalid.")

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
        ok, errors = validate_stage(stage)
        if ok:
            stage.setdefault("metadata", {})
            stage["metadata"]["conf_uid"] = conf_uid
            return stage
        logger.warning(f"Invalid stage state for {conf_uid}: {errors}")
    except Exception as e:
        logger.error(f"Failed to load stage state for {conf_uid}: {e}")

    return get_default_stage(conf_uid)


def save_stage_state(conf_uid: str, stage: dict[str, Any], updated_by: str = "frontend_action") -> tuple[bool, list[str]]:
    if not conf_uid:
        return False, ["conf_uid cannot be empty."]

    stage_to_save = copy.deepcopy(stage)
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
