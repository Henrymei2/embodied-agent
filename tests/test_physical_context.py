from __future__ import annotations

import pytest

from src.open_llm_vtuber.physical_context import (
    clear_physical_context,
    consume_physical_prompt,
    get_user_pose,
    record_events,
    snap_prop,
    update_user_pose,
)
from src.open_llm_vtuber.vr_stage_state_manager import (
    get_default_vr_stage,
    load_vr_stage_state,
    save_vr_stage_state,
)


def _prop(name="blue_token"):
    return {
        "id": name,
        "type": "prop",
        "label": name,
        "x": 400,
        "y": 300,
        "width": 20,
        "depth": 12,
        "height": 16,
        "elevation": 8,
        "yaw": 0,
        "support_id": "floor",
        "color": "#7c3aed",
        "interaction": {"on_click": True, "grabbable": True},
    }


@pytest.fixture
def room(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stage = get_default_vr_stage("character")
    stage["nodes"].append(_prop())
    assert save_vr_stage_state("character", stage)[0]
    yield stage
    clear_physical_context("client")


def test_pose_is_ephemeral_ordered_and_bounded_by_the_room(room):
    pose = update_user_pose(
        "client",
        "character",
        {"sequence": 2, "x": 500, "y": 300, "head_height": 1.7, "yaw": 190},
    )
    assert pose.yaw == -170
    assert get_user_pose(client_uid="client", conf_uid="character") is pose
    assert (
        update_user_pose(
            "client",
            "character",
            {"sequence": 1, "x": 100, "y": 100, "head_height": 1.6, "yaw": 0},
        )
        is pose
    )
    with pytest.raises(ValueError, match="outside"):
        update_user_pose(
            "client",
            "character",
            {"sequence": 3, "x": 5000, "y": 10, "head_height": 1.6, "yaw": 0},
        )
    with pytest.raises(ValueError, match="2 Hz"):
        update_user_pose(
            "client",
            "character",
            {"sequence": 4, "x": 501, "y": 300, "head_height": 1.7, "yaw": 0},
        )
    pose.received_at -= 4
    assert get_user_pose(client_uid="client", conf_uid="character") is None


def test_prop_release_snaps_to_a_furniture_top_and_persists(room):
    computer = next(node for node in room["nodes"] if node["id"] == "computer")
    top = computer["elevation"] + computer["height"] / 2
    before, after = snap_prop(
        "character",
        "blue_token",
        {"x": computer["x"], "y": computer["y"], "elevation": top + 8, "yaw": 25},
    )
    assert before["support_id"] == "floor"
    assert after["support_id"] == "computer"
    assert after["elevation"] == top + 8
    saved = next(
        node
        for node in load_vr_stage_state("character")["nodes"]
        if node["id"] == "blue_token"
    )
    assert saved["yaw"] == 25


def test_physical_summary_contains_no_client_text_and_is_consumed_once(room):
    record_events(
        "client",
        "character",
        1,
        [
            {
                "kind": "offered_item_taken",
                "entity_id": "email_card_safe",
                "description": "SECRET BODY",
            }
        ],
    )
    first = consume_physical_prompt("character")
    second = consume_physical_prompt("character")
    assert "took offered entity email_card_safe" in first
    assert "SECRET BODY" not in first
    assert "took offered entity" not in second
    assert "not user instructions" in first


def test_non_prop_cannot_be_relocated(room):
    with pytest.raises(ValueError, match="grabbable VR prop"):
        snap_prop(
            "character", "computer", {"x": 200, "y": 200, "elevation": 20, "yaw": 0}
        )
