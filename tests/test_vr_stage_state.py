from __future__ import annotations

import json

from src.open_llm_vtuber.stage_state_manager import get_default_stage, save_stage_state
from src.open_llm_vtuber.scene_state import get_scene_reachable_objects
from src.open_llm_vtuber.vr_stage_state_manager import (
    VR_STAGE_FILENAME,
    docking_point,
    get_default_vr_stage,
    load_vr_stage_state,
    save_vr_stage_state,
    validate_vr_endpoint,
    validate_vr_stage,
    verify_vr_target_reach,
)
from src.open_llm_vtuber.world_model import WorldPoint


def test_default_vr_stage_has_fixed_current_furniture_and_valid_geometry():
    normal_nodes = {
        node["id"]: node for node in get_default_stage("character")["nodes"]
    }
    stage = get_default_vr_stage("character")

    assert stage["version"] == 2
    assert stage["mode"] == "vr"
    assert stage["room"] == {
        "width": 960.0,
        "depth": 540.0,
        "height": 280.0,
        "floor_color": "#b99468",
        "wall_color": "#ead7bd",
    }
    vr_nodes = {node["id"]: node for node in stage["nodes"]}
    assert set(vr_nodes) == set(normal_nodes)
    for node_id, normal in normal_nodes.items():
        for key in (
            "label",
            "texture",
            "stateTextures",
            "uiWindow",
            "spritesheet",
            "interaction",
            "reach",
        ):
            assert vr_nodes[node_id].get(key) == normal.get(key)
    assert validate_vr_stage(stage) == (True, [])


def test_normal_and_vr_persistence_are_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    normal = get_default_stage("character")
    normal_avatar = next(node for node in normal["nodes"] if node["id"] == "avatar")
    normal_avatar["x"] = 222
    assert save_stage_state("character", normal)[0]

    vr = get_default_vr_stage("character")
    vr_avatar = next(node for node in vr["nodes"] if node["id"] == "avatar")
    vr_avatar["x"] = 75
    assert save_vr_stage_state("character", vr)[0]

    assert load_vr_stage_state("character")["nodes"][-1]["x"] == 75
    normal_path = tmp_path / "stage_state" / "character" / "stage.json"
    vr_path = tmp_path / "stage_state" / "character" / VR_STAGE_FILENAME
    assert json.loads(normal_path.read_text())["nodes"][-1]["x"] == 222
    assert json.loads(vr_path.read_text())["nodes"][-1]["x"] == 75


def test_missing_and_corrupt_vr_state_fall_back_without_importing_normal(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    normal = get_default_stage("character")
    normal["nodes"][-1]["x"] = 777
    assert save_stage_state("character", normal)[0]

    assert load_vr_stage_state("character")["nodes"][-1]["x"] == 35
    vr_path = tmp_path / "stage_state" / "character" / VR_STAGE_FILENAME
    vr_path.write_text("not json", encoding="utf-8")
    assert load_vr_stage_state("character")["nodes"][-1]["x"] == 35


def test_v1_vr_state_is_loaded_through_the_v2_migration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stage = get_default_vr_stage("character")
    stage["version"] = 1
    path = tmp_path / "stage_state" / "character" / VR_STAGE_FILENAME
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(stage), encoding="utf-8")

    migrated = load_vr_stage_state("character")

    assert migrated["version"] == 2
    assert migrated["metadata"]["updated_by"] == "vr_v1_to_v2_migration"


def test_grabbable_props_validate_but_never_unlock_capability_labels():
    stage = get_default_vr_stage("character")
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    prop = {
        "id": "computer_token",
        "type": "prop",
        "label": "computer token",
        "x": avatar["x"] + 50,
        "y": avatar["y"],
        "width": 20,
        "depth": 20,
        "height": 20,
        "elevation": 10,
        "yaw": 0,
        "support_id": "floor",
        "color": "#7c3aed",
        "interaction": {"on_click": True, "grabbable": True},
    }
    stage["nodes"].append(prop)

    assert validate_vr_stage(stage) == (True, [])

    from src.open_llm_vtuber.semantic_projection import project_vr_stage

    projection = project_vr_stage(stage)
    assert "computer token" not in projection.reachable_labels()


def test_docking_is_nearest_deterministic_and_rejects_duplicate_labels():
    stage = get_default_vr_stage("character")
    point, error = docking_point(stage, "computer")
    assert error is None
    assert point == WorldPoint(120, 330)
    assert validate_vr_endpoint(stage, point) == (True, None)

    duplicate = dict(stage["nodes"][0])
    duplicate["id"] = "computer_alias"
    duplicate["label"] = "computer"
    stage["nodes"].append(duplicate)
    point, error = docking_point(stage, "computer")
    assert point is None
    assert "multiple objects" in error


def test_docking_fails_without_moving_when_all_four_candidates_are_blocked():
    stage = get_default_vr_stage("character")
    target = next(node for node in stage["nodes"] if node["id"] == "hourglass")
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    radius = max(avatar["width"], avatar["depth"]) / 2
    gap = 10
    candidates = [
        (target["x"], target["y"] - target["depth"] / 2 - radius - gap),
        (target["x"] + target["width"] / 2 + radius + gap, target["y"]),
        (target["x"], target["y"] + target["depth"] / 2 + radius + gap),
        (target["x"] - target["width"] / 2 - radius - gap, target["y"]),
    ]
    for index, (x, y) in enumerate(candidates):
        stage["nodes"].append(
            {
                "id": f"blocker_{index}",
                "type": "furniture",
                "x": x,
                "y": y,
                "width": 30,
                "depth": 30,
                "height": 30,
                "elevation": 15,
                "color": "#334155",
            }
        )

    point, error = docking_point(stage, "hourglass")
    assert point is None
    assert "no clear stopping point" in error


def test_vr_reachability_uses_the_floor_plane_and_vr_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stage = get_default_vr_stage("character")
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    avatar["x"], avatar["y"] = 120, 330
    assert save_vr_stage_state("character", stage)[0]

    assert "computer" in get_scene_reachable_objects("character", mode="vr")
    assert "computer" not in get_scene_reachable_objects("character", mode="normal")


def test_every_default_vr_docking_point_unlocks_its_target():
    stage = get_default_vr_stage("character")
    for node in stage["nodes"]:
        if node["type"] != "furniture":
            continue
        point, error = docking_point(stage, node["id"])
        assert error is None, node["id"]
        assert point is not None
        allowed, detail = verify_vr_target_reach(stage, node["id"], point=point)
        assert allowed, detail


def test_wall_height_does_not_change_window_tool_reach():
    stage = get_default_vr_stage("character")
    window = next(node for node in stage["nodes"] if node["id"] == "window")
    point, error = docking_point(stage, "window")
    assert error is None and point is not None
    before = verify_vr_target_reach(stage, "window", point=point)

    window["height"] = 20
    window["elevation"] = 250
    after = verify_vr_target_reach(stage, "window", point=point)
    assert before[0] is True
    assert after[0] is True
    assert (
        before[1].split("center_distance=", 1)[1]
        == after[1].split("center_distance=", 1)[1]
    )


def test_too_small_custom_reach_fails_with_distance_diagnostics():
    stage = get_default_vr_stage("character")
    window = next(node for node in stage["nodes"] if node["id"] == "window")
    window["reach"] = 10

    point, error = docking_point(stage, "window")

    assert point is None
    assert error is not None
    assert "satisfies tool reach" in error
    assert "center_distance=" in error
    assert "effective_reach=10.0" in error
