"""Connection-scoped embodied relocation during agenda planning gaps."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from loguru import logger

from ..frontend_bridge import request_frontend_action
from ..stage_state_manager import load_stage_state, load_world

IdlePosture = Literal["standing", "sitting"]
_SEATED_OBJECT_IDS = frozenset({"sofa", "reading_chair"})
IDLE_RELOCATION_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class IdleObject:
    """One planner-visible destination from the current room snapshot."""

    object_id: str
    label: str
    x: float
    y: float
    distance: float | None
    posture: IdlePosture


@dataclass(frozen=True)
class IdleScene:
    """The bounded spatial context used for an idle-placement decision."""

    current_position: tuple[float, float] | None
    objects: tuple[IdleObject, ...]


@dataclass(frozen=True)
class IdlePlacement:
    """A constrained planner choice; ``object_id=None`` means stay put."""

    object_id: str | None
    reason: str


def idle_scene_snapshot(conf_uid: str) -> IdleScene:
    """Read all non-avatar world objects and the persisted avatar position."""
    world = load_world(conf_uid)
    origin = world.agent_position
    objects = tuple(
        IdleObject(
            object_id=obj.id,
            label=obj.label,
            x=obj.position.x,
            y=obj.position.y,
            distance=obj.position.distance_to(origin) if origin is not None else None,
            posture="sitting" if obj.id in _SEATED_OBJECT_IDS else "standing",
        )
        for obj in world.objects
    )
    current = (origin.x, origin.y) if origin is not None else None
    return IdleScene(current_position=current, objects=objects)


def validate_idle_placement(
    placement: IdlePlacement,
    scene: IdleScene,
) -> IdlePlacement:
    """Constrain a planner result to the supplied room, with a sofa fallback."""
    object_ids = {obj.object_id for obj in scene.objects}
    if placement.object_id in object_ids:
        return IdlePlacement(placement.object_id, placement.reason[:200])
    if "sofa" in object_ids:
        return IdlePlacement(
            "sofa",
            "The planner did not return a current room object; using the sofa.",
        )
    return IdlePlacement(
        None,
        "The planner did not return a current room object and no sofa is present.",
    )


async def _relocate_for_idle(
    *,
    client_uid: str,
    conf_uid: str,
    placement: IdlePlacement,
) -> bool:
    """Move through the live frontend, verify arrival, and apply a known seat pose."""
    if placement.object_id is None:
        return False

    world = load_world(conf_uid)
    destination = next(
        (obj for obj in world.objects if obj.id == placement.object_id),
        None,
    )
    if destination is None and placement.object_id != "sofa":
        destination = next((obj for obj in world.objects if obj.id == "sofa"), None)
    if destination is None:
        logger.info(
            "agenda: idle relocation skipped because no safe destination exists"
        )
        return False

    path = world.path_to(destination.position)
    ok, _result, error = await request_frontend_action(
        "move_to",
        {
            "x": destination.position.x,
            "y": destination.position.y,
            "waypoints": [{"x": point.x, "y": point.y} for point in path],
        },
        client_uid=client_uid,
        conf_uid=conf_uid,
    )
    if not ok:
        logger.warning(f"agenda: idle relocation to {destination.id!r} failed: {error}")
        return False

    arrived = load_world(conf_uid)
    if not arrived.has_arrived_at(destination.position):
        logger.warning(
            f"agenda: idle relocation to {destination.id!r} was not persisted as arrived"
        )
        return False

    posture: IdlePosture = (
        "sitting" if destination.id in _SEATED_OBJECT_IDS else "standing"
    )
    if posture == "sitting":
        ok, _result, error = await request_frontend_action(
            "stage_patch_state",
            {
                "patch": {
                    "nodes": [{"id": "avatar", "pose": "sitting"}],
                    "metadata": {"updated_by": "agenda_idle_relocation"},
                }
            },
            client_uid=client_uid,
            conf_uid=conf_uid,
        )
        if not ok:
            logger.warning(
                f"agenda: idle posture at {destination.id!r} failed: {error}"
            )
            return False
        stage = load_stage_state(conf_uid)
        avatar = next(
            (node for node in stage.get("nodes", []) if node.get("id") == "avatar"),
            None,
        )
        if avatar is None or avatar.get("pose") != "sitting":
            logger.warning(
                f"agenda: seated posture at {destination.id!r} was not persisted"
            )
            return False

    logger.info(f"agenda: settled at {destination.id!r} while choosing what to do next")
    return True


async def relocate_for_idle(
    *,
    client_uid: str,
    conf_uid: str,
    placement: IdlePlacement,
) -> bool:
    """Bound the complete move-and-pose transition to one bridge timeout."""
    try:
        return await asyncio.wait_for(
            _relocate_for_idle(
                client_uid=client_uid,
                conf_uid=conf_uid,
                placement=placement,
            ),
            timeout=IDLE_RELOCATION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "agenda: idle relocation exceeded the frontend action timeout; "
            "successor planning will continue"
        )
        return False
