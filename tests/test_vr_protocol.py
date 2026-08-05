from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.open_llm_vtuber import frontend_bridge
from src.open_llm_vtuber.config_manager.system import SystemConfig
from src.open_llm_vtuber.stage_state_manager import load_stage_state
from src.open_llm_vtuber.vr_stage_state_manager import (
    get_default_vr_stage,
    load_vr_stage_state,
)
from src.open_llm_vtuber.websocket_handler import WebSocketHandler


def _system_config(**overrides):
    values = {
        "conf_version": "1.0.0",
        "host": "localhost",
        "port": 12393,
        "tool_prompts": {},
    }
    values.update(overrides)
    return SystemConfig(**values)


def test_tls_requires_a_readable_certificate_and_key_pair(tmp_path):
    with pytest.raises(ValidationError, match="configured together"):
        _system_config(ssl_certfile=str(tmp_path / "cert.pem"))

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("certificate", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    config = _system_config(ssl_certfile=str(cert), ssl_keyfile=str(key))
    assert config.ssl_certfile == str(cert.resolve())
    assert config.ssl_keyfile == str(key.resolve())


def test_vr_connection_ownership_is_exclusive_per_character():
    handler = WebSocketHandler(default_context_cache=None)
    handler.client_contexts["desktop-a"] = SimpleNamespace(
        scene_mode="normal",
        character_config=SimpleNamespace(conf_uid="character-a"),
    )

    assert handler.can_connect("normal", "character-a") == (True, "")
    assert handler.can_connect("vr", "character-a")[0] is False
    assert handler.can_connect("vr", "character-b") == (True, "")

    handler.client_contexts.clear()
    handler.client_contexts["quest"] = SimpleNamespace(
        scene_mode="vr",
        character_config=SimpleNamespace(conf_uid="character-a"),
    )
    assert handler.can_connect("normal", "character-a")[0] is False
    assert handler.can_connect("vr", "character-a")[0] is False


@pytest.mark.asyncio
async def test_vr_frontend_result_is_authoritatively_saved_only_to_vr(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    sent = asyncio.Event()
    request: dict = {}

    async def send(message: str) -> None:
        request.update(json.loads(message))
        sent.set()

    client_uid = "quest"
    conf_uid = "character"
    frontend_bridge.register_client_sender(client_uid, send, conf_uid, scene_mode="vr")
    handler = WebSocketHandler(default_context_cache=None)
    handler.client_contexts[client_uid] = SimpleNamespace(
        scene_mode="vr",
        character_config=SimpleNamespace(conf_uid=conf_uid),
    )
    action_task = asyncio.create_task(
        frontend_bridge.request_frontend_action("move_to", {}, client_uid=client_uid)
    )
    await asyncio.wait_for(sent.wait(), timeout=1)

    stage = get_default_vr_stage(conf_uid)
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    avatar["x"] = 75
    await handler._handle_frontend_action_result(
        SimpleNamespace(),
        client_uid,
        {
            "request_id": request["request_id"],
            "result": {
                "ok": True,
                "state": stage,
                "mode": "normal",
                "arrived": True,
                "duration_ms": 250,
                "position": {"x": 75, "y": avatar["y"]},
            },
        },
    )

    ok, result, error = await action_task
    assert ok is True
    assert error is None
    assert result["state"]["nodes"][-1]["x"] == 75
    assert load_vr_stage_state(conf_uid)["nodes"][-1]["x"] == 75
    assert load_stage_state(conf_uid)["nodes"][-1]["x"] != 75
    frontend_bridge.unregister_client_sender(client_uid)
