from __future__ import annotations

import copy
import math

from src.open_llm_vtuber.mcpp.tool_manager import PROXIMITY_THRESHOLD
from src.open_llm_vtuber.stage_state_manager import (
    get_default_stage,
    migrate_stage,
    validate_stage,
)


def test_default_stage_is_v5_with_google_furniture():
    stage = get_default_stage("conf-a")

    assert stage["version"] == 5
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


def test_tool_furniture_does_not_share_a_proximity_radius():
    """Standing at one object must not unlock another object's tools."""
    nodes = {node["id"]: node for node in get_default_stage("conf-a")["nodes"]}
    gated = [
        "todo_list",
        "hourglass",
        "window",
        "computer",
        "calendar",
        "archive_shelf",
        "mailbox",
    ]

    for index, first in enumerate(gated):
        for second in gated[index + 1 :]:
            distance = math.dist(
                (nodes[first]["x"], nodes[first]["y"]),
                (nodes[second]["x"], nodes[second]["y"]),
            )
            assert distance > PROXIMITY_THRESHOLD, f"{first} overlaps {second}"


def test_v1_stage_migrates_to_v5_and_preserves_positions():
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

    assert migrated["version"] == 5
    assert nodes["avatar"]["x"] == 123
    assert nodes["avatar"]["y"] == 234
    assert nodes["avatar"]["spritesheet"]["src"] == "./stage-assets/avatar.png"
    assert nodes["computer"]["texture"]["src"] == "./stage-assets/computer.png"
    assert validate_stage(v1_stage) == (True, [])


def test_v2_stage_migrates_to_v5_with_calendar_window():
    stage = get_default_stage("conf-a")
    stage["version"] = 2
    nodes = {node["id"]: node for node in stage["nodes"]}
    nodes["calendar"].pop("uiWindow")

    migrated = migrate_stage(stage)

    assert migrated["version"] == 5
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

    assert migrated["version"] == 5
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

    assert migrated["version"] == 5
    assert {"archive_shelf", "mailbox"} <= set(nodes)
    assert nodes["calendar"]["x"] == 555
    assert validate_stage(migrated) == (True, [])


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
