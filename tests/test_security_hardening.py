"""Regression tests for the security hardening pass."""

from __future__ import annotations

import os
import re

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketDisconnect

from src.open_llm_vtuber import bridge_auth
from src.open_llm_vtuber.request_security import (
    build_trusted_hosts,
    cors_origin_regex,
    is_trusted_request,
    require_trusted_http_request,
)
from src.open_llm_vtuber.routes import init_client_ws_route
from src.open_llm_vtuber.server import ProtectedStaticFiles
from src.open_llm_vtuber.stage_state_manager import (
    _is_safe_filename,
    _sanitize_path_component,
)


# --------------------------------------------------------------------------- #
# Bridge token auth
# --------------------------------------------------------------------------- #
def test_bridge_token_is_stable_within_run():
    assert bridge_auth.get_bridge_token() == bridge_auth.get_bridge_token()


def test_verify_bridge_token_accepts_real_rejects_others():
    token = bridge_auth.get_bridge_token()
    assert bridge_auth.verify_bridge_token(token) is True
    assert bridge_auth.verify_bridge_token("nope") is False
    assert bridge_auth.verify_bridge_token(None) is False
    assert bridge_auth.verify_bridge_token("") is False


# --------------------------------------------------------------------------- #
# Stage-state filename sanitization (path traversal)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad",
    [
        "..",
        ".",
        "   ",
        "../etc",
        "a/b",
        "a\\b",
        "c:evil",
        "star*",
        "q?mark",
        "pipe|x",
        "null\x00byte",
    ],
)
def test_unsafe_filenames_rejected(bad):
    assert _is_safe_filename(bad) is False


@pytest.mark.parametrize("good", ["mao_pro_001", "char-1", "Agent_42", "default"])
def test_safe_filenames_accepted(good):
    assert _is_safe_filename(good) is True


@pytest.mark.parametrize(
    "colliding",
    ["../character", r"..\character", " character ", "CON", "name."],
)
def test_stage_path_components_are_rejected_instead_of_rewritten(colliding):
    with pytest.raises(ValueError):
        _sanitize_path_component(colliding)


# --------------------------------------------------------------------------- #
# Bash MCP env sanitization
# --------------------------------------------------------------------------- #
def test_sanitized_env_strips_credentials(monkeypatch, tmp_path):
    from mcp_servers import bash_tools

    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("MY_PASSWORD", "hunter2")
    monkeypatch.setenv("SESSION_TOKEN", "abc")
    monkeypatch.setenv("SAFE_VALUE", "keepme")

    env = bash_tools._sanitized_env(tmp_path)

    assert "OPENAI_API_KEY" not in env
    assert "MY_PASSWORD" not in env
    assert "SESSION_TOKEN" not in env
    assert env.get("SAFE_VALUE") == "keepme"
    # Home is pinned to the workspace so ~ expansion can't reach the real home.
    assert env["HOME"] == str(tmp_path)
    assert env["USERPROFILE"] == str(tmp_path)


def test_sanitized_env_keeps_path(tmp_path):
    from mcp_servers import bash_tools

    env = bash_tools._sanitized_env(tmp_path)
    # PATH must survive or commands won't resolve.
    assert "PATH" in env or "Path" in os.environ or "PATH" in os.environ


# --------------------------------------------------------------------------- #
# Browser and Host-header access control
# --------------------------------------------------------------------------- #
def _trusted(**overrides):
    values = {
        "host_header": "127.0.0.1:12393",
        "origin": "http://localhost:5173",
        "client_host": "127.0.0.1",
        "user_agent": "Mozilla/5.0",
        "trusted_hosts": build_trusted_hosts("localhost"),
    }
    values.update(overrides)
    return is_trusted_request(**values)


def test_same_server_and_loopback_development_origins_are_trusted():
    assert _trusted() is True
    assert (
        _trusted(
            host_header="companion.lan:12393",
            origin="http://companion.lan:12393",
            client_host="192.168.1.20",
            trusted_hosts=build_trusted_hosts(
                "0.0.0.0",
                ["companion.lan"],
            ),
        )
        is True
    )


def test_cross_site_and_dns_rebinding_origins_are_rejected():
    assert _trusted(origin="https://attacker.example") is False
    assert (
        _trusted(
            host_header="attacker.example",
            origin="https://attacker.example",
        )
        is False
    )
    assert (
        _trusted(
            host_header="127.0.0.1:12393",
            origin="http://127.0.0.1:12393",
            client_host="192.168.1.20",
        )
        is False
    )
    assert (
        _trusted(
            host_header="companion.lan:12393",
            origin="http://companion.lan:5173",
            client_host="192.168.1.20",
            trusted_hosts=build_trusted_hosts(
                "0.0.0.0",
                ["companion.lan"],
            ),
        )
        is False
    )


def test_originless_requests_are_loopback_only():
    assert _trusted(origin=None, client_host="127.0.0.1") is True
    assert _trusted(origin=None, client_host="192.168.1.20") is False


def test_null_origin_is_reserved_for_loopback_electron():
    assert (
        _trusted(
            origin="null",
            user_agent="Mozilla/5.0 Electron/31.0.2",
        )
        is True
    )
    assert _trusted(origin="null", user_agent="Mozilla/5.0 Chrome/126") is False
    assert (
        _trusted(
            origin="file://",
            user_agent="Mozilla/5.0 Electron/31.0.2",
        )
        is True
    )
    assert (
        _trusted(
            origin="null",
            client_host="192.168.1.20",
            user_agent="Mozilla/5.0 Electron/31.0.2",
        )
        is False
    )


def test_trusted_referer_supports_same_origin_gets_without_origin_header():
    assert (
        _trusted(
            host_header="companion.lan:12393",
            origin=None,
            referer="http://companion.lan:12393/",
            client_host="192.168.1.20",
            trusted_hosts=build_trusted_hosts("0.0.0.0", ["companion.lan"]),
        )
        is True
    )
    assert (
        _trusted(
            origin=None,
            referer="https://attacker.example/",
            client_host="192.168.1.20",
        )
        is False
    )


def test_cors_regex_does_not_match_untrusted_or_lookalike_hosts():
    regex = cors_origin_regex(build_trusted_hosts("localhost", ["companion.lan"]))

    assert re.fullmatch(regex, "http://localhost:5173")
    assert re.fullmatch(regex, "https://companion.lan")
    assert not re.fullmatch(regex, "https://attacker.example")
    assert not re.fullmatch(regex, "http://localhost.evil:5173")


def test_private_http_dependency_rejects_cross_origin_requests():
    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("localhost", ["testserver"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["null", "file://"],
        allow_origin_regex=cors_origin_regex(app.state.trusted_hosts),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/private", dependencies=[Depends(require_trusted_http_request)])
    def private():
        return {"secret": True}

    client = TestClient(app)
    denied = client.get(
        "/private",
        headers={"origin": "https://attacker.example"},
    )
    allowed = client.get(
        "/private",
        headers={"origin": "http://testserver"},
    )

    assert denied.status_code == 403
    assert "secret" not in denied.text
    assert "access-control-allow-origin" not in denied.headers
    assert allowed.json() == {"secret": True}
    assert allowed.headers["access-control-allow-origin"] == "http://testserver"


def test_missing_trusted_host_state_defaults_to_loopback_only():
    app = FastAPI()

    @app.get("/private", dependencies=[Depends(require_trusted_http_request)])
    def private():
        return {"secret": True}

    local_client = TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )
    remote_client = TestClient(
        app,
        base_url="http://attacker.example",
        client=("192.0.2.10", 50000),
    )

    assert local_client.get("/private").json() == {"secret": True}
    denied = remote_client.get(
        "/private",
        headers={"origin": "http://attacker.example"},
    )
    assert denied.status_code == 403
    assert "secret" not in denied.text
    assert (
        is_trusted_request(
            host_header="attacker.example",
            origin="http://attacker.example",
            client_host="192.0.2.10",
            user_agent="audit",
            trusted_hosts=None,
        )
        is False
    )


@pytest.fixture
def protected_cache_client(tmp_path):
    (tmp_path / "private.wav").write_bytes(b"private audio")
    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("localhost")
    app.mount("/cache", ProtectedStaticFiles(directory=tmp_path), name="cache")
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.mark.parametrize(
    "headers",
    [
        {
            "host": "attacker.example",
            "origin": "https://attacker.example",
        },
        {
            "host": "127.0.0.1:12393",
            "origin": "https://attacker.example",
        },
    ],
)
def test_protected_cache_rejects_untrusted_requests(protected_cache_client, headers):
    response = protected_cache_client.get("/cache/private.wav", headers=headers)

    assert response.status_code == 403
    assert b"private audio" not in response.content
    assert response.headers["cache-control"] == "no-store"


def test_protected_cache_serves_trusted_loopback_requests(protected_cache_client):
    headers = {
        "host": "127.0.0.1:12393",
        "origin": "http://localhost:5173",
    }

    response = protected_cache_client.get("/cache/private.wav", headers=headers)
    missing = protected_cache_client.get("/cache/missing.wav", headers=headers)

    assert response.status_code == 200
    assert response.content == b"private audio"
    assert missing.status_code == 404


def test_client_websocket_route_rejects_cross_origin_handshake():
    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("localhost", ["testserver"])
    app.include_router(init_client_ws_route(default_context_cache=None))
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/client-ws",
            headers={"origin": "https://attacker.example"},
        ):
            pass

    assert exc_info.value.code == 1008


def test_wildcard_bind_address_is_never_a_trusted_host():
    hosts = build_trusted_hosts("0.0.0.0", ["192.168.1.10"])

    assert "0.0.0.0" not in hosts
    assert "192.168.1.10" in hosts
    with pytest.raises(ValueError):
        build_trusted_hosts("localhost", ["*"])
    with pytest.raises(ValueError):
        build_trusted_hosts("localhost", ["companion.lan:12393"])
