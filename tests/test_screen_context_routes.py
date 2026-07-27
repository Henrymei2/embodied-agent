from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.open_llm_vtuber import bridge_auth, routes
from src.open_llm_vtuber.autonomy import screen_context as sc
from src.open_llm_vtuber.request_security import build_trusted_hosts
from src.open_llm_vtuber.routes import init_frontend_action_routes


@pytest.fixture
def client(monkeypatch):
    sc._CLIENT_CONTEXT.clear()
    monkeypatch.setattr(bridge_auth, "get_bridge_token", lambda: "bridge-secret")
    # TestClient connects as host "testclient"; treat it as loopback so the
    # same guard the real subprocess passes is exercised here.
    monkeypatch.setattr(
        routes, "_LOOPBACK_HOSTS", routes._LOOPBACK_HOSTS | {"testclient"}
    )
    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("localhost", ["testserver"])
    app.include_router(init_frontend_action_routes())
    yield TestClient(app, headers={"origin": "http://testserver"})
    sc._CLIENT_CONTEXT.clear()


_TOKEN = {bridge_auth.BRIDGE_TOKEN_HEADER: "bridge-secret"}


def test_internal_route_returns_latest_context(client):
    sc.update_screen_context(
        "client-1", {"app": "VS Code", "title": "file.py", "idle_seconds": 3}
    )

    response = client.post(
        "/internal/screen-context",
        json={"client_uid": "client-1"},
        headers=_TOKEN,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["app"] == "VS Code"
    assert body["summary"] == "VS Code — file.py"


def test_internal_route_reports_absence_without_error(client):
    response = client.post(
        "/internal/screen-context",
        json={"client_uid": "nobody"},
        headers=_TOKEN,
    )

    assert response.status_code == 200
    assert response.json() == {"available": False}


def test_internal_route_rejects_non_loopback_caller(monkeypatch, client):
    monkeypatch.setattr(routes, "_LOOPBACK_HOSTS", {"127.0.0.1", "::1", "localhost"})
    sc.update_screen_context("client-1", {"app": "VS Code", "title": "file.py"})

    response = client.post(
        "/internal/screen-context",
        json={"client_uid": "client-1"},
        headers=_TOKEN,
    )
    assert response.status_code == 403


def test_internal_route_requires_bridge_token(client):
    sc.update_screen_context("client-1", {"app": "VS Code", "title": "file.py"})

    missing = client.post("/internal/screen-context", json={"client_uid": "client-1"})
    assert missing.status_code == 403

    wrong = client.post(
        "/internal/screen-context",
        json={"client_uid": "client-1"},
        headers={bridge_auth.BRIDGE_TOKEN_HEADER: "nope"},
    )
    assert wrong.status_code == 403


def test_internal_route_validates_client_uid(client):
    response = client.post(
        "/internal/screen-context", json={"client_uid": ""}, headers=_TOKEN
    )
    assert response.status_code == 400
