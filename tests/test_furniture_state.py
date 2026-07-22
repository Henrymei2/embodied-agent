from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from src.open_llm_vtuber import furniture_state
from src.open_llm_vtuber.mcpp.tool_manager import ToolManager
from src.open_llm_vtuber.mcpp.types import FormattedTool
from src.open_llm_vtuber.stage_state_manager import get_default_stage, save_stage_state


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    return tmp_path / "workspace"


def _todo(text: str, priority: str = "normal", status: str = "open") -> dict:
    return {
        "todo_id": text[:8],
        "text": text,
        "status": status,
        "priority": priority,
        "due": None,
        "created_at": "2026-07-21T00:00:00+00:00",
    }


def test_todo_snapshot_shows_open_items_in_priority_order():
    furniture_state.save_todos(
        [
            _todo("low thing", priority="low"),
            _todo("done thing", status="done"),
            _todo("urgent thing", priority="high"),
        ]
    )

    snapshot = furniture_state.todo_snapshot()

    assert snapshot["available"] is True
    assert snapshot["open_count"] == 2
    assert [item["text"] for item in snapshot["items"]] == [
        "urgent thing",
        "low thing",
    ]


def test_todo_snapshot_caps_rows_and_reports_the_remainder():
    furniture_state.save_todos([_todo(f"item {index}") for index in range(11)])

    snapshot = furniture_state.todo_snapshot(limit=4)

    assert len(snapshot["items"]) == 4
    assert snapshot["remaining"] == 7
    assert snapshot["open_count"] == 11


def test_todo_snapshot_degrades_instead_of_raising():
    path = furniture_state.todos_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json", encoding="utf-8")

    snapshot = furniture_state.todo_snapshot()

    assert snapshot["available"] is False
    assert snapshot["items"] == []
    assert "unreadable" in snapshot["message"]


def test_timer_snapshot_counts_only_timers_and_sorts_by_urgency():
    now = datetime.now()
    furniture_state.save_tasks(
        {
            "a": {
                "id": "a",
                "kind": "timer",
                "label": "later",
                "next_run": (now + timedelta(minutes=20)).isoformat(timespec="seconds"),
            },
            "b": {
                "id": "b",
                "kind": "timer",
                "label": "sooner",
                "next_run": (now + timedelta(minutes=3)).isoformat(timespec="seconds"),
            },
            "c": {"id": "c", "prompt": "a plain scheduled task", "next_run": "x"},
        }
    )

    snapshot = furniture_state.timer_snapshot()

    assert snapshot["count"] == 2
    assert [timer["label"] for timer in snapshot["timers"]] == ["sooner", "later"]
    assert 2 * 60 < snapshot["timers"][0]["remaining_seconds"] <= 3 * 60


def test_timer_snapshot_floors_overdue_timers_at_zero():
    furniture_state.save_tasks(
        {
            "a": {
                "id": "a",
                "kind": "timer",
                "label": "overdue",
                "next_run": (datetime.now() - timedelta(minutes=5)).isoformat(
                    timespec="seconds"
                ),
            }
        }
    )

    assert furniture_state.timer_snapshot()["timers"][0]["remaining_seconds"] == 0


def test_task_store_survives_a_corrupt_file():
    path = furniture_state.tasks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{oops", encoding="utf-8")

    assert furniture_state.load_tasks() == {}
    assert furniture_state.timer_snapshot()["timers"] == []


def test_saved_stores_are_valid_json_on_disk():
    furniture_state.save_todos([_todo("round trip")])

    payload = json.loads(furniture_state.todos_path().read_text(encoding="utf-8"))

    assert payload["version"] == 1
    assert payload["items"][0]["text"] == "round trip"


def test_hover_routes_serve_snapshots_and_stay_read_only():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.open_llm_vtuber.routes import init_furniture_routes

    furniture_state.save_todos([_todo("buy milk", priority="high")])
    app = FastAPI()
    app.include_router(init_furniture_routes())
    client = TestClient(app)

    todo_response = client.get("/api/furniture/todo")
    assert todo_response.status_code == 200
    payload = todo_response.json()
    assert payload["open_count"] == 1
    assert payload["items"][0]["text"] == "buy milk"
    # Only what the panel renders; never the record's identifiers.
    assert "todo_id" not in payload["items"][0]

    assert client.get("/api/furniture/timers").json() == {
        "available": True,
        "count": 0,
        "timers": [],
        "remaining": 0,
    }

    assert client.post("/api/furniture/todo").status_code == 405


def _manager_at(monkeypatch, tmp_path, target_id: str | None) -> ToolManager:
    """Build a ToolManager with the avatar standing on a given object."""
    monkeypatch.chdir(tmp_path)
    stage = get_default_stage("character")
    nodes = {node["id"]: node for node in stage["nodes"]}
    if target_id:
        nodes["avatar"]["x"] = nodes[target_id]["x"]
        nodes["avatar"]["y"] = nodes[target_id]["y"]
    assert save_stage_state("character", stage)[0]

    return ToolManager(
        initial_tools_dict={
            "add_todo": FormattedTool(input_schema={}, related_server="todo"),
            "set_timer": FormattedTool(input_schema={}, related_server="timer"),
            "get_current_weather": FormattedTool(
                input_schema={}, related_server="weather"
            ),
        },
        conf_uid="character",
    )


@pytest.mark.parametrize(
    ("tool_name", "furniture"),
    [
        ("add_todo", "todo_list"),
        ("set_timer", "hourglass"),
        ("get_current_weather", "window"),
    ],
)
def test_new_tools_are_gated_on_their_furniture(
    monkeypatch, tmp_path, tool_name, furniture
):
    manager = _manager_at(monkeypatch, tmp_path, furniture)
    assert manager.get_required_object(tool_name) == furniture
    assert manager.authorize_tool(tool_name) == (True, None)

    away = _manager_at(monkeypatch, tmp_path, "bed")
    allowed, reason = away.authorize_tool(tool_name)
    assert allowed is False
    assert furniture in reason
