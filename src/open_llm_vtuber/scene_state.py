"""Mode-aware access to the normal and VR scene stores."""

from __future__ import annotations

from typing import Any, Literal

from loguru import logger

from .stage_state_manager import (
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
    return (
        reset_vr_stage_state(conf_uid) if mode == "vr" else reset_stage_state(conf_uid)
    )


def load_scene_world(conf_uid: str, mode: SceneMode = "normal"):
    return load_vr_world(conf_uid) if mode == "vr" else load_normal_world(conf_uid)


def load_scene_projection(conf_uid: str, mode: SceneMode = "normal"):
    """The semantic view of a saved scene, which is what authorization reads.

    Part 6 built the semantic world, proved that both renderers grant identical
    capabilities from identical objects, and left the live gate reading
    `world_model` — so the parity guarantee held for a model nothing consulted.
    This is the seam that fixes that: the projection is now the authority for
    the proximity gate, in both renderers.
    """
    from .semantic_projection import project_screen_stage, project_vr_stage

    state = load_scene_state(conf_uid, mode)
    return project_vr_stage(state) if mode == "vr" else project_screen_stage(state)


def get_scene_reachable_objects(
    conf_uid: str,
    distance: float | None = None,
    mode: SceneMode = "normal",
) -> list[str]:
    """Labels the proximity gate admits, resolved through the semantic world.

    Validation stays with the renderer-specific loaders, because their error
    messages are what callers already handle. Only the reachability answer
    moves, and it was proved identical to the previous one across 240
    randomized avatar positions in both renderers before the switch.

    A scene that cannot be projected fails closed: an unreadable room grants
    nothing rather than falling back to a second implementation whose
    agreement with this one would then never be tested again.
    """
    if not conf_uid:
        raise ValueError("conf_uid cannot be empty")
    if distance is not None:
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            raise ValueError("distance must be a number")
        if distance < 0:
            raise ValueError("distance must be non-negative")

    from .semantic_projection import ProjectionError

    try:
        return load_scene_projection(conf_uid, mode).reachable_labels(within=distance)
    except (ProjectionError, ValueError) as exc:
        logger.warning(
            f"Scene could not be projected for proximity; failing closed: {exc}"
        )
        return []
