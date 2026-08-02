"""Mode-aware access to the normal and VR scene stores."""

from __future__ import annotations

from typing import Any, Literal

from .stage_state_manager import (
    get_reachable_objects as get_normal_reachable_objects,
    load_stage_state,
    load_world as load_normal_world,
    reset_stage_state,
    save_stage_state,
    validate_stage,
)
from .vr_stage_state_manager import (
    load_vr_stage_state,
    load_vr_world,
    reset_vr_stage_state,
    save_vr_stage_state,
    validate_vr_stage,
)

SceneMode = Literal["normal", "vr"]


def normalize_scene_mode(value: Any) -> SceneMode:
    if value in (None, "", "normal"):
        return "normal"
    if value == "vr":
        return "vr"
    raise ValueError(f"Unsupported renderer mode: {value}")


def load_scene_state(conf_uid: str, mode: SceneMode = "normal") -> dict[str, Any]:
    return load_vr_stage_state(conf_uid) if mode == "vr" else load_stage_state(conf_uid)


def save_scene_state(
    conf_uid: str,
    state: dict[str, Any],
    *,
    mode: SceneMode = "normal",
    updated_by: str = "frontend_action",
) -> tuple[bool, list[str]]:
    if mode == "vr":
        return save_vr_stage_state(conf_uid, state, updated_by=updated_by)
    return save_stage_state(conf_uid, state, updated_by=updated_by)


def validate_scene(state: Any, mode: SceneMode = "normal") -> tuple[bool, list[str]]:
    return validate_vr_stage(state) if mode == "vr" else validate_stage(state)


def reset_scene_state(conf_uid: str, mode: SceneMode = "normal") -> dict[str, Any]:
    return reset_vr_stage_state(conf_uid) if mode == "vr" else reset_stage_state(conf_uid)


def load_scene_world(conf_uid: str, mode: SceneMode = "normal"):
    return load_vr_world(conf_uid) if mode == "vr" else load_normal_world(conf_uid)


def get_scene_reachable_objects(
    conf_uid: str,
    distance: float | None = None,
    mode: SceneMode = "normal",
) -> list[str]:
    if mode == "normal":
        return get_normal_reachable_objects(conf_uid, distance)
    if distance is not None and distance < 0:
        raise ValueError("distance must be non-negative")
    return load_vr_world(conf_uid).reachable_labels(within=distance)
