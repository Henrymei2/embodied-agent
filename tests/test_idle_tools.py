from __future__ import annotations

from mcp_servers import idle_tools
from src.open_llm_vtuber.stage_state_manager import get_default_stage, validate_stage


async def test_sitting_down_patches_only_the_pose(monkeypatch):
    captured: dict = {}

    async def post(action, args):
        captured["action"] = action
        captured["args"] = args
        return True, None

    monkeypatch.setattr(
        idle_tools, "load_stage_state", lambda _conf_uid: get_default_stage("character")
    )
    monkeypatch.setattr(idle_tools, "post_frontend_action", post)

    result = await idle_tools.sit_down("nothing to do")

    assert result.startswith("OK: sitting on the sofa.")
    assert "nothing to do" in result
    assert captured["action"] == "stage_patch_state"
    assert captured["args"]["patch"]["nodes"] == [{"id": "avatar", "pose": "sitting"}]


async def test_standing_up_clears_the_pose(monkeypatch):
    captured: dict = {}

    async def post(_action, args):
        captured["args"] = args
        return True, None

    monkeypatch.setattr(
        idle_tools, "load_stage_state", lambda _conf_uid: get_default_stage("character")
    )
    monkeypatch.setattr(idle_tools, "post_frontend_action", post)

    assert await idle_tools.stand_up() == "OK: standing."
    assert captured["args"]["patch"]["nodes"] == [{"id": "avatar", "pose": None}]


async def test_a_room_without_an_avatar_reports_instead_of_posting(monkeypatch):
    async def should_not_post(_action, _args):
        raise AssertionError("there is nothing to pose")

    monkeypatch.setattr(idle_tools, "load_stage_state", lambda _conf_uid: {"nodes": []})
    monkeypatch.setattr(idle_tools, "post_frontend_action", should_not_post)

    assert "no avatar to pose" in await idle_tools.sit_down()


def test_a_seated_avatar_is_a_valid_stage():
    stage = get_default_stage("character")
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    avatar["pose"] = "sitting"

    assert validate_stage(stage) == (True, [])
