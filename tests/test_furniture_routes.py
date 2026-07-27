from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcp_servers import plan_tools
from src.open_llm_vtuber import furniture_state
from src.open_llm_vtuber.request_security import build_trusted_hosts
from src.open_llm_vtuber.routes import init_furniture_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("localhost", ["testserver"])
    app.include_router(init_furniture_routes())
    return TestClient(app, headers={"origin": "http://testserver"})


def _write_plan(title: str = "Tidy the knowledge folder") -> dict:
    return json.loads(
        plan_tools.write_plan(title, goal="Notes piled up.", steps=["List", "Merge"])
    )["plan"]


def test_bookshelf_and_reading_windows_start_empty(client):
    shelf = client.get("/api/furniture/bookshelf").json()
    reading = client.get("/api/furniture/reading").json()

    assert shelf == {"available": True, "count": 0, "books": [], "remaining": 0}
    assert reading["session"] is None
    assert reading["finished_count"] == 0


def test_reading_window_reports_progress_and_recent_notes(client):
    furniture_state.save_library(
        {
            "shelf": [],
            "sessions": [
                {
                    "book_id": "b1",
                    "title": "Frankenstein",
                    "author": "Mary Shelley",
                    "status": "reading",
                    "section": 4,
                    "progress_percent": 42,
                    "updated_at": "2026-07-25T00:00:00+00:00",
                    "notes": [
                        {"section": index, "summary": f"note {index}"}
                        for index in range(1, 5)
                    ],
                }
            ],
        }
    )

    body = client.get("/api/furniture/reading").json()

    assert body["session"]["title"] == "Frankenstein"
    assert body["session"]["progress_percent"] == 42
    # Newest first, and only the last few.
    assert [note["section"] for note in body["session"]["notes"]] == [4, 3, 2]


def test_plan_window_exposes_pending_plans(client):
    plan = _write_plan()

    body = client.get("/api/furniture/plans").json()

    assert body["pending_count"] == 1
    assert body["pending"][0]["plan_id"] == plan["plan_id"]
    assert body["recent"] is None


def test_the_window_is_the_only_way_to_approve_a_plan(client):
    plan = _write_plan()

    response = client.post(
        f"/api/furniture/plans/{plan['plan_id']}/decision",
        json={"decision": "approved"},
    )

    assert response.status_code == 200
    assert response.json() == {"plan_id": plan["plan_id"], "status": "approved"}
    stored = json.loads(plan_tools.get_plan(plan["plan_id"]))["plan"]
    assert stored["status"] == "approved"
    assert stored["decided_by"] == "user"


def test_a_decision_route_rejects_unknown_verdicts_and_ids(client):
    plan = _write_plan()

    assert (
        client.post(
            f"/api/furniture/plans/{plan['plan_id']}/decision",
            json={"decision": "maybe"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/furniture/plans/does-not-exist/decision",
            json={"decision": "approved"},
        ).status_code
        == 404
    )
    stored = json.loads(plan_tools.get_plan(plan["plan_id"]))["plan"]
    assert stored["status"] == "pending"


def test_a_second_decision_on_the_same_plan_is_rejected(client):
    plan = _write_plan()
    path = f"/api/furniture/plans/{plan['plan_id']}/decision"
    client.post(path, json={"decision": "approved"})

    assert client.post(path, json={"decision": "declined"}).status_code == 404


def test_skill_window_lists_drafts_and_the_route_is_the_only_approver(client):
    from mcp_servers import skill_tools

    skill_tools.draft_skill(
        "Summarize a long thread",
        purpose="Turn a long email thread into a short brief.",
        when_to_use="The user asks what happened in a thread.",
        steps=["Read oldest to newest", "List decisions"],
    )

    listed = client.get("/api/furniture/skills").json()
    assert listed["pending_count"] == 1
    assert listed["active_count"] == 0
    assert listed["pending"][0]["slug"] == "summarize-a-long-thread"

    response = client.post(
        "/api/furniture/skills/summarize-a-long-thread/decision",
        json={"decision": "approved"},
    )

    assert response.status_code == 200
    assert client.get("/api/furniture/skills").json()["active_count"] == 1
    # A second decision has nothing pending left to act on.
    assert (
        client.post(
            "/api/furniture/skills/summarize-a-long-thread/decision",
            json={"decision": "declined"},
        ).status_code
        == 404
    )


def test_skill_decision_route_validates_its_input(client):
    assert (
        client.post(
            "/api/furniture/skills/whatever/decision", json={"decision": "maybe"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/furniture/skills/Not A Slug/decision", json={"decision": "approved"}
        ).status_code
        == 400
    )


def test_furniture_windows_report_an_unreadable_store_instead_of_failing(
    client, tmp_path
):
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "library" / "library.json").write_text("{ broken", encoding="utf-8")
    (tmp_path / "plans").mkdir(parents=True, exist_ok=True)
    (tmp_path / "plans" / "plans.json").write_text("{ broken", encoding="utf-8")

    shelf = client.get("/api/furniture/bookshelf").json()
    plans = client.get("/api/furniture/plans").json()

    assert shelf["available"] is False
    assert "unreadable" in shelf["message"]
    assert plans["available"] is False
    assert plans["pending"] == []
