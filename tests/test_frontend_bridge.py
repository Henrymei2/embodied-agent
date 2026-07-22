from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.open_llm_vtuber import frontend_bridge
from src.open_llm_vtuber.service_context import ServiceContext
from src.open_llm_vtuber.stage_state_manager import get_default_stage, load_stage_state
from src.open_llm_vtuber.websocket_handler import WebSocketHandler


@pytest.fixture(autouse=True)
def clear_bridge_state():
    frontend_bridge._clients.clear()
    frontend_bridge._pending_requests.clear()
    yield
    frontend_bridge._clients.clear()
    frontend_bridge._pending_requests.clear()


@pytest.mark.asyncio
async def test_frontend_action_routes_to_exact_client_and_character():
    received: dict[str, list[dict]] = {"client-a": [], "client-b": []}

    def sender(client_uid: str):
        async def send(message: str) -> None:
            payload = json.loads(message)
            received[client_uid].append(payload)
            frontend_bridge.resolve_action_result(
                payload["request_id"],
                True,
                {"ok": True, "owner": client_uid},
                client_uid=client_uid,
            )

        return send

    frontend_bridge.register_client_sender(
        "client-a", sender("client-a"), "character-a"
    )
    frontend_bridge.register_client_sender(
        "client-b", sender("client-b"), "character-b"
    )

    ok, result, error = await frontend_bridge.request_frontend_action(
        "move_to",
        {"x": 1, "y": 2},
        client_uid="client-b",
        conf_uid="character-b",
    )

    assert (ok, error) == (True, None)
    assert result["owner"] == "client-b"
    assert received["client-a"] == []
    assert received["client-b"][0]["action"] == "move_to"


@pytest.mark.asyncio
async def test_frontend_action_refuses_ambiguous_client_selection():
    async def send(_message: str) -> None:
        raise AssertionError("ambiguous requests must not be sent")

    frontend_bridge.register_client_sender("client-a", send, "same-character")
    frontend_bridge.register_client_sender("client-b", send, "same-character")

    ok, result, error = await frontend_bridge.request_frontend_action(
        "move_to",
        {},
        conf_uid="same-character",
    )

    assert ok is False
    assert result == {}
    assert "multiple frontend clients" in error


@pytest.mark.asyncio
async def test_frontend_action_rejects_result_spoofing():
    sent = asyncio.Event()
    request: dict = {}

    async def send(message: str) -> None:
        request.update(json.loads(message))
        sent.set()

    frontend_bridge.register_client_sender("owner", send, "character")
    frontend_bridge.register_client_sender("other", send, "other-character")
    task = asyncio.create_task(
        frontend_bridge.request_frontend_action(
            "move_to",
            {},
            client_uid="owner",
            timeout=1,
        )
    )
    await asyncio.wait_for(sent.wait(), timeout=1)

    accepted = frontend_bridge.resolve_action_result(
        request["request_id"],
        True,
        {"ok": True},
        client_uid="other",
    )

    assert accepted is False
    assert task.done() is False

    assert frontend_bridge.resolve_action_result(
        request["request_id"],
        True,
        {"ok": True},
        client_uid="owner",
    )
    assert await task == (True, {"ok": True}, None)


@pytest.mark.asyncio
async def test_disconnect_fails_pending_action_without_waiting_for_timeout():
    sent = asyncio.Event()

    async def send(_message: str) -> None:
        sent.set()

    frontend_bridge.register_client_sender("client", send, "character")
    task = asyncio.create_task(
        frontend_bridge.request_frontend_action(
            "move_to",
            {},
            client_uid="client",
            timeout=30,
        )
    )
    await asyncio.wait_for(sent.wait(), timeout=1)

    frontend_bridge.unregister_client_sender("client")
    ok, result, error = await asyncio.wait_for(task, timeout=1)

    assert ok is False
    assert result == {}
    assert "disconnected" in error
    assert frontend_bridge._pending_requests == {}


@pytest.mark.asyncio
async def test_malformed_frontend_result_fails_pending_action_safely():
    sent = asyncio.Event()
    request: dict = {}

    async def send(message: str) -> None:
        request.update(json.loads(message))
        sent.set()

    frontend_bridge.register_client_sender("client", send, "character")
    task = asyncio.create_task(
        frontend_bridge.request_frontend_action(
            "move_to",
            {},
            client_uid="client",
            timeout=1,
        )
    )
    await asyncio.wait_for(sent.wait(), timeout=1)

    handler = WebSocketHandler(default_context_cache=None)
    await handler._handle_frontend_action_result(
        SimpleNamespace(),
        "client",
        {"request_id": request["request_id"], "result": "invalid"},
    )

    ok, result, error = await task
    assert ok is False
    assert result == {
        "ok": False,
        "error": "frontend action result must be an object",
    }
    assert error == "frontend action result must be an object"


@pytest.mark.asyncio
async def test_character_binding_updates_after_config_switch():
    async def send(message: str) -> None:
        payload = json.loads(message)
        frontend_bridge.resolve_action_result(
            payload["request_id"],
            True,
            {"ok": True},
            client_uid="client",
        )

    frontend_bridge.register_client_sender("client", send, "old-character")
    frontend_bridge.update_client_configuration("client", "new-character")

    ok, _result, error = await frontend_bridge.request_frontend_action(
        "move_to",
        {},
        client_uid="client",
        conf_uid="old-character",
    )
    assert ok is False
    assert "not using" in error

    ok, _result, error = await frontend_bridge.request_frontend_action(
        "move_to",
        {},
        client_uid="client",
        conf_uid="new-character",
    )
    assert (ok, error) == (True, None)


def test_mcp_subprocess_environment_is_session_bound(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setattr(
        "src.open_llm_vtuber.bridge_auth.get_bridge_token",
        lambda: "bridge-secret",
    )
    context = ServiceContext()
    server = SimpleNamespace(env={"EXISTING": "value"})
    context.mcp_server_registery = SimpleNamespace(servers={"scene": server})
    context.system_config = SimpleNamespace(host="0.0.0.0", port=12393)
    context.character_config = SimpleNamespace(conf_uid="fallback-character")
    context.client_uid = "client-123"

    context._configure_mcp_server_environment(conf_uid="active-character")

    assert server.env == {
        "EXISTING": "value",
        "EMBODIED_AGENT_BACKEND_URL": "http://127.0.0.1:12393",
        "EMBODIED_AGENT_BRIDGE_TOKEN": "bridge-secret",
        "EMBODIED_AGENT_WORKSPACE": str((tmp_path / "workspace").resolve()),
        "EMBODIED_AGENT_CLIENT_UID": "client-123",
        "EMBODIED_AGENT_CONF_UID": "active-character",
    }


def test_google_subprocesses_receive_only_google_token_path(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setattr(
        "src.open_llm_vtuber.bridge_auth.get_bridge_token", lambda: "bridge-secret"
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.google_integration.default_token_file",
        lambda: tmp_path / "app-data" / "google_token.json",
    )
    context = ServiceContext()
    google_servers = {
        name: SimpleNamespace(env={}) for name in ("calendar", "drive", "gmail")
    }
    context.mcp_server_registery = SimpleNamespace(
        servers=google_servers
    )
    context.system_config = SimpleNamespace(
        host="localhost",
        port=12393,
        google_integration=SimpleNamespace(
            enabled=True,
            client_secrets_file="/private/google-client.json",
            redirect_uri=(
                "http://localhost:12393/api/integrations/google/oauth/callback"
            ),
        ),
    )
    context.character_config = SimpleNamespace(conf_uid="single-character")
    context.client_uid = "client"

    context._configure_mcp_server_environment()

    for server in google_servers.values():
        assert server.env["EMBODIED_AGENT_GOOGLE_TOKEN_FILE"].endswith(
            "app-data/google_token.json"
        )
        assert "EMBODIED_AGENT_GOOGLE_CLIENT_SECRETS_FILE" not in server.env
        assert "EMBODIED_AGENT_GOOGLE_REDIRECT_URI" not in server.env


@pytest.mark.asyncio
async def test_accepted_frontend_state_is_persisted_for_owning_client(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    sent = asyncio.Event()
    request: dict = {}

    async def send(message: str) -> None:
        request.update(json.loads(message))
        sent.set()

    client_uid = "client"
    conf_uid = "character"
    frontend_bridge.register_client_sender(client_uid, send, conf_uid)
    handler = WebSocketHandler(default_context_cache=None)
    handler.client_contexts[client_uid] = SimpleNamespace(
        character_config=SimpleNamespace(conf_uid=conf_uid)
    )
    action_task = asyncio.create_task(
        frontend_bridge.request_frontend_action(
            "move_to",
            {},
            client_uid=client_uid,
        )
    )
    await asyncio.wait_for(sent.wait(), timeout=1)

    stage = get_default_stage(conf_uid)
    avatar = next(node for node in stage["nodes"] if node["id"] == "avatar")
    avatar["x"] = 321
    avatar["y"] = 123
    await handler._handle_frontend_action_result(
        SimpleNamespace(),
        client_uid,
        {
            "request_id": request["request_id"],
            "result": {"ok": True, "state": stage},
        },
    )

    ok, result, error = await action_task
    assert (ok, error) == (True, None)
    assert result["state"]["nodes"]
    persisted_avatar = next(
        node for node in load_stage_state(conf_uid)["nodes"] if node["id"] == "avatar"
    )
    assert (persisted_avatar["x"], persisted_avatar["y"]) == (321, 123)
