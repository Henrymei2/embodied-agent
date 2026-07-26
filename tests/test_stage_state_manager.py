from __future__ import annotations

import copy
import json
import math

from src.open_llm_vtuber.mcpp.tool_manager import (
    PROXIMITY_REQUIREMENTS,
    PROXIMITY_THRESHOLD,
    SERVER_PROXIMITY_REQUIREMENTS,
)
from src.open_llm_vtuber.stage_state_manager import (
    STAGE_VERSION,
    get_default_stage,
    load_stage_state,
    migrate_stage,
    STAGE_THEME_REVISION,
    validate_stage,
)


def test_default_stage_is_current_with_google_furniture():
    stage = get_default_stage("conf-a")

    assert stage["version"] == STAGE_VERSION
    assert stage["metadata"]["theme_revision"] == STAGE_THEME_REVISION
    assert stage["canvas"]["texture"]["src"].endswith("bedroom-background.png")
    nodes = {node["id"]: node for node in stage["nodes"]}
    assert nodes["avatar"]["spritesheet"]["src"] == "./stage-assets/avatar.png"
    assert nodes["bed"]["texture"]["src"] == "./stage-assets/bed.png"
    assert nodes["calendar"]["uiWindow"]["provider"] == "google_calendar_status"
    assert nodes["archive_shelf"]["texture"]["src"].endswith("archive-shelf.png")
    assert nodes["mailbox"]["uiWindow"]["provider"] == "google_gmail_unread"
    assert nodes["mailbox"]["stateTextures"]["unread"]["src"].endswith(
        "mailbox-unread.png"
    )
    assert validate_stage(stage) == (True, [])


def test_default_stage_carries_tool_furniture():
    nodes = {node["id"]: node for node in get_default_stage("conf-a")["nodes"]}

    assert nodes["todo_list"]["uiWindow"]["provider"] == "todo_list"
    assert nodes["hourglass"]["uiWindow"]["provider"] == "timer_status"
    assert "provider" not in nodes["window"]["uiWindow"]
    assert nodes["todo_list"]["texture"]["src"].endswith("todo-list.png")
    assert nodes["hourglass"]["texture"]["src"].endswith("hourglass.png")
    assert nodes["window"]["texture"]["src"].endswith("window.png")
    assert nodes["bookshelf"]["uiWindow"]["provider"] == "bookshelf"
    assert nodes["reading_chair"]["uiWindow"]["provider"] == "reading_status"
    assert nodes["writing_desk"]["uiWindow"]["provider"] == "plan_status"
    assert nodes["design_board"]["uiWindow"]["provider"] == "room_layout"
    assert nodes["tool_rack"]["uiWindow"]["provider"] == "skill_status"
    assert "provider" not in nodes["sofa"]["uiWindow"]
    assert nodes["sofa"]["texture"]["src"].endswith("sofa.png")
    assert nodes["avatar"]["spritesheet"]["animations"]["sit_down"]["row"] == 8


def test_every_gated_object_exists_in_the_default_room():
    """A tool bound to a missing object would be permanently unreachable."""
    labels = {
        node.get("label") or node["id"] for node in get_default_stage("conf-a")["nodes"]
    }
    required = set(PROXIMITY_REQUIREMENTS.values()) | set(
        SERVER_PROXIMITY_REQUIREMENTS.values()
    )

    assert required <= labels


def test_avatar_pose_is_validated():
    stage = get_default_stage("conf-a")
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")

    avatar["pose"] = "sitting"
    assert validate_stage(stage) == (True, [])

    avatar["pose"] = "levitating"
    ok, errors = validate_stage(stage)
    assert ok is False
    assert "Node avatar pose is not supported." in errors


def test_unmarked_stage_is_force_reset_and_persisted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = get_default_stage("conf-a")
    saved["metadata"].pop("theme_revision")
    saved["canvas"]["background"] = "#000000"
    saved["nodes"].append(
        {
            "id": "custom_chair",
            "type": "panel",
            "x": 50,
            "y": 50,
            "width": 50,
            "height": 50,
        }
    )
    path = tmp_path / "stage_state" / "conf-a" / "stage.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(saved), encoding="utf-8")

    loaded = load_stage_state("conf-a")

    assert loaded["metadata"]["theme_revision"] == STAGE_THEME_REVISION
    assert loaded["metadata"]["updated_by"] == "default"
    assert loaded["canvas"]["background"] == "#e8c98f"
    assert "custom_chair" not in {node["id"] for node in loaded["nodes"]}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["metadata"]["theme_revision"] == STAGE_THEME_REVISION
    assert persisted["metadata"]["updated_by"] == "theme_reset"


def test_stage_marked_with_the_current_theme_is_not_reset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = get_default_stage("conf-a")
    saved["canvas"]["background"] = "#123456"
    path = tmp_path / "stage_state" / "conf-a" / "stage.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(saved), encoding="utf-8")

    loaded = load_stage_state("conf-a")

    assert loaded["canvas"]["background"] == "#123456"


def test_tool_furniture_does_not_share_a_proximity_radius():
    """Standing at one object must not unlock another object's tools."""
    nodes = {
        node.get("label") or node["id"]: node
        for node in get_default_stage("conf-a")["nodes"]
    }
    gated = sorted(
        set(PROXIMITY_REQUIREMENTS.values())
        | set(SERVER_PROXIMITY_REQUIREMENTS.values())
    )

    for index, first in enumerate(gated):
        for second in gated[index + 1 :]:
            distance = math.dist(
                (nodes[first]["x"], nodes[first]["y"]),
                (nodes[second]["x"], nodes[second]["y"]),
            )
            assert distance > PROXIMITY_THRESHOLD, f"{first} overlaps {second}"


def test_default_room_objects_stay_inside_the_canvas():
    stage = get_default_stage("conf-a")
    canvas = stage["canvas"]

    for node in stage["nodes"]:
        # Furniture is centered on its position; the avatar is not (the frontend
        # anchors it near its feet), so it is checked separately below.
        if node["id"] == "avatar":
            continue
        assert node["x"] - node["width"] / 2 >= 0, node["id"]
        assert node["x"] + node["width"] / 2 <= canvas["width"], node["id"]
        assert node["y"] - node["height"] / 2 >= 0, node["id"]
        assert node["y"] + node["height"] / 2 <= canvas["height"], node["id"]

    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    # Matches `translate(-50%, -78%)` in dynamic-stage.tsx.
    assert avatar["x"] - avatar["width"] / 2 >= 0
    assert avatar["x"] + avatar["width"] / 2 <= canvas["width"]
    assert avatar["y"] - avatar["height"] * 0.78 >= 0
    assert avatar["y"] + avatar["height"] * 0.22 <= canvas["height"]


def test_nothing_is_reachable_from_where_the_avatar_starts():
    """The agent must walk to an object before that object's tools appear."""
    stage = get_default_stage("conf-a")
    nodes = {node.get("label") or node["id"]: node for node in stage["nodes"]}
    avatar = nodes["avatar"]
    gated = set(PROXIMITY_REQUIREMENTS.values()) | set(
        SERVER_PROXIMITY_REQUIREMENTS.values()
    )

    for label in gated:
        distance = math.dist(
            (avatar["x"], avatar["y"]), (nodes[label]["x"], nodes[label]["y"])
        )
        assert distance > PROXIMITY_THRESHOLD, f"{label} is reachable at spawn"


def test_v1_stage_migrates_forward_and_preserves_positions():
    v1_stage = get_default_stage("conf-a")
    v1_stage["version"] = 1
    v1_stage["nodes"] = [
        {
            "id": "avatar",
            "type": "avatar",
            "label": "avatar",
            "x": 123,
            "y": 234,
            "width": 70,
            "height": 92,
            "zIndex": 3,
            "text": "avatar",
            "style": {"background": "#111827"},
        }
    ]

    migrated = migrate_stage(v1_stage)
    nodes = {node["id"]: node for node in migrated["nodes"]}

    assert migrated["version"] == STAGE_VERSION
    assert nodes["avatar"]["x"] == 123
    assert nodes["avatar"]["y"] == 234
    assert nodes["avatar"]["spritesheet"]["src"] == "./stage-assets/avatar.png"
    assert nodes["computer"]["texture"]["src"] == "./stage-assets/computer.png"
    assert validate_stage(v1_stage) == (True, [])


def test_v2_stage_migrates_forward_with_calendar_window():
    stage = get_default_stage("conf-a")
    stage["version"] = 2
    nodes = {node["id"]: node for node in stage["nodes"]}
    nodes["calendar"].pop("uiWindow")

    migrated = migrate_stage(stage)

    assert migrated["version"] == STAGE_VERSION
    calendar = next(node for node in migrated["nodes"] if node["id"] == "calendar")
    assert calendar["uiWindow"]["provider"] == "google_calendar_status"


def test_v3_stage_gains_new_furniture_but_keeps_custom_layout():
    """A room saved before the furniture shipped must not stay locked out."""
    stage = get_default_stage("conf-a")
    stage["version"] = 3
    stage["nodes"] = [
        node
        for node in stage["nodes"]
        if node["id"] not in {"todo_list", "hourglass", "window"}
    ]
    moved_bed = next(node for node in stage["nodes"] if node["id"] == "bed")
    moved_bed["x"] = 321

    migrated = migrate_stage(stage)
    nodes = {node["id"]: node for node in migrated["nodes"]}

    assert migrated["version"] == STAGE_VERSION
    assert {"todo_list", "hourglass", "window"} <= set(nodes)
    assert nodes["bed"]["x"] == 321
    assert validate_stage(migrated) == (True, [])


def test_v4_stage_gains_google_furniture_and_keeps_custom_layout():
    stage = get_default_stage("conf-a")
    stage["version"] = 4
    stage["nodes"] = [
        node
        for node in stage["nodes"]
        if node["id"] not in {"archive_shelf", "mailbox"}
    ]
    next(node for node in stage["nodes"] if node["id"] == "calendar")["x"] = 555

    migrated = migrate_stage(stage)
    nodes = {node["id"]: node for node in migrated["nodes"]}

    assert migrated["version"] == STAGE_VERSION
    assert {"archive_shelf", "mailbox"} <= set(nodes)
    assert nodes["calendar"]["x"] == 555
    assert validate_stage(migrated) == (True, [])


def test_v5_stage_gains_furniture_and_a_seated_avatar_animation():
    """The shipped spritesheet grew a seated row, so its animation map must too."""
    stage = get_default_stage("conf-a")
    stage["version"] = 5
    stage["nodes"] = [
        node
        for node in stage["nodes"]
        if node["id"]
        not in {"bookshelf", "reading_chair", "writing_desk", "sofa", "design_board"}
    ]
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    avatar["x"] = 321
    avatar["spritesheet"] = {
        **avatar["spritesheet"],
        "animations": {
            name: value
            for name, value in avatar["spritesheet"]["animations"].items()
            if name != "sit_down"
        },
    }

    migrated = migrate_stage(stage)
    nodes = {node["id"]: node for node in migrated["nodes"]}

    assert migrated["version"] == STAGE_VERSION
    assert {
        "bookshelf",
        "reading_chair",
        "writing_desk",
        "sofa",
        "design_board",
    } <= set(nodes)
    assert nodes["avatar"]["x"] == 321
    assert "sit_down" in nodes["avatar"]["spritesheet"]["animations"]
    assert validate_stage(migrated) == (True, [])


def test_v6_stage_gains_the_tool_rack():
    """A room saved before skills shipped must not stay locked out of them."""
    stage = get_default_stage("conf-a")
    stage["version"] = 6
    stage["nodes"] = [node for node in stage["nodes"] if node["id"] != "tool_rack"]
    next(node for node in stage["nodes"] if node["id"] == "sofa")["x"] = 700

    migrated = migrate_stage(stage)
    nodes = {node["id"]: node for node in migrated["nodes"]}

    assert migrated["version"] == STAGE_VERSION
    assert nodes["tool_rack"]["uiWindow"]["provider"] == "skill_status"
    assert nodes["sofa"]["x"] == 700
    assert validate_stage(migrated) == (True, [])


def test_migration_keeps_a_custom_spritesheet_untouched():
    stage = get_default_stage("conf-a")
    stage["version"] = 5
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    custom = {
        "src": "https://example.com/custom-avatar.png",
        "frameWidth": 32,
        "frameHeight": 32,
        "animations": {"idle_down": {"row": 0, "frames": [0]}},
    }
    avatar["spritesheet"] = custom

    migrated = migrate_stage(stage)
    nodes = {node["id"]: node for node in migrated["nodes"]}

    assert nodes["avatar"]["spritesheet"] == custom


def test_stage_rejects_unknown_ui_provider_and_extra_fields():
    stage = get_default_stage("conf-a")
    calendar = next(node for node in stage["nodes"] if node["id"] == "calendar")
    calendar["uiWindow"] = {
        "title": "Unsafe",
        "body": "<script>alert(1)</script>",
        "provider": "arbitrary_endpoint",
        "html": "<script>alert(1)</script>",
    }

    ok, errors = validate_stage(stage)

    assert ok is False
    assert "Node calendar uiWindow.provider is not supported." in errors
    assert "Node calendar uiWindow.html is not allowed." in errors
    assert "Node calendar uiWindow.body contains unsafe text." in errors


def test_v2_stage_accepts_external_texture_url():
    stage = get_default_stage("conf-a")
    nodes = {node["id"]: node for node in stage["nodes"]}
    nodes["bed"]["texture"] = {
        "src": "https://example.com/bed.png",
        "fit": "cover",
        "alt": "bed",
    }

    assert validate_stage(stage) == (True, [])


def test_stage_rejects_javascript_asset_urls():
    stage = get_default_stage("conf-a")
    invalid = copy.deepcopy(stage)
    nodes = {node["id"]: node for node in invalid["nodes"]}
    nodes["bed"]["texture"]["src"] = "javascript:alert(1)"
    nodes["avatar"]["spritesheet"]["src"] = "javascript:alert(2)"

    ok, errors = validate_stage(invalid)

    assert ok is False
    assert "Node bed texture.src is invalid." in errors
    assert "Node avatar spritesheet.src is invalid." in errors


def test_stage_rejects_invalid_canvas_texture():
    stage = get_default_stage("conf-a")
    stage["canvas"]["texture"] = {
        "src": "javascript:alert(1)",
        "fit": "stretch",
    }

    ok, errors = validate_stage(stage)

    assert ok is False
    assert "Node canvas texture.src is invalid." in errors
    assert "Node canvas texture.fit must be contain or cover." in errors
