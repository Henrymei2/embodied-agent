"""Postcondition verification for actions rendered by the live frontend."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .contracts import ActionDescriptor
from .profiles import ActionProfile


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _result(verified: bool, observed: Any) -> dict[str, Any]:
    return {
        "verified": bool(verified),
        "kind": "persisted_frontend_state",
        "evidence_digest": _digest(observed),
    }


def _find_node(state: dict[str, Any], name: str) -> dict[str, Any] | None:
    for node in state.get("nodes", []):
        if node.get("id") == name or node.get("label") == name:
            return node
    return None


def _same_number(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
        return False
    return math.isclose(float(actual), float(expected), abs_tol=1e-6)


def build_frontend_action_verifier(conf_uid: str, scene_mode: str = "normal"):
    """Build a verifier bound to one connection's persisted scene."""

    mode = "vr" if scene_mode == "vr" else "normal"

    async def verify(
        _descriptor: ActionDescriptor,
        profile: ActionProfile,
        arguments: dict[str, Any],
        _tool_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        if profile.verification != "frontend_ack":
            return None

        from ..scene_state import load_scene_state, load_scene_world

        state = load_scene_state(conf_uid, mode)
        capability = profile.capability

        if capability == "move_to":
            target = arguments.get("target")
            if not isinstance(target, str) or not target:
                return _result(False, {"capability": capability, "target": None})
            if mode == "vr":
                from ..vr_stage_state_manager import verify_vr_target_reach

                verified, detail = verify_vr_target_reach(state, target)
                avatar = _find_node(state, "avatar")
                return _result(
                    verified,
                    {
                        "capability": capability,
                        "target": target,
                        "avatar": (
                            {"x": avatar.get("x"), "y": avatar.get("y")}
                            if avatar
                            else None
                        ),
                        "reach_detail": detail,
                    },
                )

            world = load_scene_world(conf_uid, "normal")
            destination = world.find(target)
            position = world.agent_position
            verified = bool(
                destination is not None
                and position is not None
                and world.has_arrived_at(destination.position)
            )
            return _result(
                verified,
                {
                    "capability": capability,
                    "target": target,
                    "avatar": (
                        {"x": position.x, "y": position.y} if position else None
                    ),
                    "destination": (
                        {
                            "x": destination.position.x,
                            "y": destination.position.y,
                        }
                        if destination
                        else None
                    ),
                },
            )

        avatar = _find_node(state, "avatar")
        if capability in {"sit_down", "stand_up"}:
            pose = avatar.get("pose") if avatar else None
            verified = bool(
                avatar
                and (
                    pose == "sitting"
                    if capability == "sit_down"
                    else pose in {None, "standing"}
                )
            )
            return _result(
                verified,
                {"capability": capability, "avatar_present": bool(avatar), "pose": pose},
            )

        name = arguments.get("name")
        if not isinstance(name, str) or not name:
            return _result(False, {"capability": capability, "name": None})
        node = _find_node(state, name)

        if capability == "remove_object":
            return _result(
                node is None,
                {"capability": capability, "name": name, "present": node is not None},
            )

        if capability in {"place_object", "move_object"}:
            verified = bool(
                node
                and _same_number(node.get("x"), arguments.get("x"))
                and _same_number(node.get("y"), arguments.get("y"))
            )
            if capability == "place_object" and node:
                expected_width = arguments.get("width", 120.0)
                expected_height = arguments.get("height", 90.0)
                verified = bool(
                    verified
                    and _same_number(node.get("width"), expected_width)
                    and _same_number(node.get("height"), expected_height)
                )
            return _result(
                verified,
                {
                    "capability": capability,
                    "name": name,
                    "node": (
                        {
                            "x": node.get("x"),
                            "y": node.get("y"),
                            "width": node.get("width"),
                            "height": node.get("height"),
                        }
                        if node
                        else None
                    ),
                },
            )

        return None

    return verify
