from __future__ import annotations

import copy

from src.open_llm_vtuber.stage_state_manager import (
    get_default_stage,
    migrate_stage,
    validate_stage,
)


def test_default_stage_is_v2_with_asset_fields():
    stage = get_default_stage("conf-a")

    assert stage["version"] == 2
    nodes = {node["id"]: node for node in stage["nodes"]}
    assert nodes["avatar"]["spritesheet"]["src"] == "./stage-assets/avatar.png"
    assert nodes["bed"]["texture"]["src"] == "./stage-assets/bed.png"
    assert validate_stage(stage) == (True, [])


def test_v1_stage_migrates_to_v2_and_preserves_positions():
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

    assert migrated["version"] == 2
    assert nodes["avatar"]["x"] == 123
    assert nodes["avatar"]["y"] == 234
    assert nodes["avatar"]["spritesheet"]["src"] == "./stage-assets/avatar.png"
    assert nodes["computer"]["texture"]["src"] == "./stage-assets/computer.png"
    assert validate_stage(v1_stage) == (True, [])


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
