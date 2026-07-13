from __future__ import annotations

import io
import json

from mcp_servers import scene_tools
from src.open_llm_vtuber.stage_state_manager import get_default_stage


def test_scene_reads_session_bound_character(monkeypatch):
    requested: list[str] = []

    def load(conf_uid: str) -> dict:
        requested.append(conf_uid)
        return {"nodes": [{"id": "calendar", "label": "calendar", "x": 12, "y": 34}]}

    monkeypatch.setenv("EMBODIED_AGENT_CONF_UID", "character-42")
    monkeypatch.setattr(scene_tools, "load_stage_state", load)

    assert scene_tools.get_object_position("calendar") == "calendar is at x=12, y=34."
    assert requested == ["character-42"]


def test_frontend_bridge_request_carries_session_identity(monkeypatch):
    captured: dict = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.headers)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("EMBODIED_AGENT_CLIENT_UID", "client-42")
    monkeypatch.setenv("EMBODIED_AGENT_CONF_UID", "character-42")
    monkeypatch.setenv("EMBODIED_AGENT_BRIDGE_TOKEN", "secret")
    monkeypatch.setattr(scene_tools.urllib.request, "urlopen", urlopen)

    assert scene_tools._post_frontend_action_sync("move_to", {"x": 1, "y": 2}) == (
        True,
        None,
    )
    assert captured["body"] == {
        "action": "move_to",
        "args": {"x": 1, "y": 2},
        "timeout": scene_tools._FRONTEND_ACTION_TIMEOUT_SECONDS,
        "client_uid": "client-42",
        "conf_uid": "character-42",
    }
    assert captured["headers"]["X-bridge-token"] == "secret"
    assert captured["url"].endswith("/internal/frontend-action")


def test_invalid_bridge_response_is_reported(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return io.BytesIO(b"not json").read()

    monkeypatch.setattr(
        scene_tools.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    ok, error = scene_tools._post_frontend_action_sync("move_to", {})

    assert ok is False
    assert "could not reach backend" in error


async def test_place_object_uses_validated_stage_patch(monkeypatch):
    stage = get_default_stage("character")
    captured: dict = {}

    async def post(action, args):
        captured["action"] = action
        captured["args"] = args
        return True, None

    monkeypatch.setenv("EMBODIED_AGENT_CONF_UID", "character")
    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)
    monkeypatch.setattr(scene_tools, "_post_frontend_action", post)

    result = await scene_tools.place_object(
        "reading_chair",
        300,
        220,
        image_url="./stage-assets/chair.png",
    )

    assert result == "OK: placed reading_chair at x=300, y=220."
    assert captured["action"] == "stage_patch_state"
    node = captured["args"]["patch"]["nodes"][0]
    assert node["id"] == "reading_chair"
    assert node["texture"]["src"] == "./stage-assets/chair.png"


async def test_place_object_rejects_unsafe_assets_before_frontend(monkeypatch):
    stage = get_default_stage("character")

    async def should_not_post(_action, _args):
        raise AssertionError("invalid objects must not reach the frontend")

    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)
    monkeypatch.setattr(scene_tools, "_post_frontend_action", should_not_post)

    result = await scene_tools.place_object(
        "unsafe",
        100,
        100,
        image_url="javascript:alert(1)",
    )

    assert result.startswith("Error: object was rejected:")


async def test_remove_object_protects_avatar(monkeypatch):
    stage = get_default_stage("character")
    monkeypatch.setattr(scene_tools, "load_stage_state", lambda _conf_uid: stage)

    assert await scene_tools.remove_object("avatar") == (
        "Error: the avatar cannot be removed from its own room."
    )
