from types import SimpleNamespace

import pytest

from src.open_llm_vtuber.authorization.contracts import build_action_descriptor
from src.open_llm_vtuber.authorization.frontend_verifier import (
    build_frontend_action_verifier,
)
from src.open_llm_vtuber.authorization.profiles import profile_for


def _descriptor(capability, arguments):
    profile = profile_for(capability)
    return build_action_descriptor(
        action_id=f"action:{capability}",
        actor="agent",
        capability=capability,
        target_ref="target:test",
        arguments=arguments,
        side_effect_class=profile.side_effect_class,
        reversibility=profile.reversibility,
        confidence=0.99,
        evidence_refs=("session:test",),
        required_user_authority="session",
        spatial_requirement=None,
        idempotency_key=f"idempotency:{capability}",
        privacy_class="private",
        external_destination=None,
    )


@pytest.mark.asyncio
async def test_normal_move_requires_persisted_arrival(monkeypatch):
    destination = SimpleNamespace(position=SimpleNamespace(x=425.0, y=110.0))
    world = SimpleNamespace(
        agent_position=SimpleNamespace(x=425.0, y=110.0),
        find=lambda target: destination if target == "calendar" else None,
        has_arrived_at=lambda point: point is destination.position,
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.scene_state.load_scene_state",
        lambda _conf_uid, _mode: {"nodes": []},
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.scene_state.load_scene_world",
        lambda _conf_uid, _mode: world,
    )
    arguments = {"target": "calendar"}

    evidence = await build_frontend_action_verifier("character")(
        _descriptor("move_to", arguments),
        profile_for("move_to"),
        arguments,
        {},
    )

    assert evidence["verified"] is True
    assert evidence["kind"] == "persisted_frontend_state"


@pytest.mark.asyncio
async def test_vr_move_requires_persisted_target_reach(monkeypatch):
    state = {"nodes": [{"id": "avatar", "x": 400.0, "y": 130.0}]}
    monkeypatch.setattr(
        "src.open_llm_vtuber.scene_state.load_scene_state",
        lambda _conf_uid, _mode: state,
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.vr_stage_state_manager.verify_vr_target_reach",
        lambda candidate, target: (candidate is state and target == "calendar", "ok"),
    )
    arguments = {"target": "calendar"}

    evidence = await build_frontend_action_verifier("character", "vr")(
        _descriptor("move_to", arguments),
        profile_for("move_to"),
        arguments,
        {},
    )

    assert evidence["verified"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "pose", "expected"),
    [
        ("sit_down", "sitting", True),
        ("sit_down", None, False),
        ("stand_up", None, True),
        ("stand_up", "sitting", False),
    ],
)
async def test_pose_verification_reads_persisted_avatar(
    monkeypatch, capability, pose, expected
):
    monkeypatch.setattr(
        "src.open_llm_vtuber.scene_state.load_scene_state",
        lambda _conf_uid, _mode: {"nodes": [{"id": "avatar", "pose": pose}]},
    )
    arguments = {}

    evidence = await build_frontend_action_verifier("character")(
        _descriptor(capability, arguments),
        profile_for(capability),
        arguments,
        {},
    )

    assert evidence["verified"] is expected
