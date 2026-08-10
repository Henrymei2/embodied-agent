from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.open_llm_vtuber.browser_session import (
    MAX_IMAGE_BYTES,
    MAX_TEXT_CHARS,
    browser_image_bytes,
    browser_snapshot,
    clear_browser_session,
    record_browser_activity,
)
from src.open_llm_vtuber.mcpp.tool_executor import ToolExecutor
from src.open_llm_vtuber.mcpp.tool_manager import ToolManager
from src.open_llm_vtuber.mcpp.types import FormattedTool
from src.open_llm_vtuber.request_security import build_trusted_hosts
from src.open_llm_vtuber.routes import init_furniture_routes
from src.open_llm_vtuber.stage_state_manager import (
    get_default_stage,
    load_stage_state,
    save_stage_state,
)
from src.open_llm_vtuber.vr_stage_state_manager import (
    get_default_vr_stage,
    load_vr_stage_state,
    save_vr_stage_state,
    validate_vr_stage,
)


@pytest.fixture(autouse=True)
def _clean_snapshot():
    clear_browser_session()
    yield
    clear_browser_session()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("localhost", ["testserver"])
    app.include_router(init_furniture_routes())
    return TestClient(app, headers={"origin": "http://testserver"})


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"fake-image-data"


# ── snapshot store ──────────────────────────────────────────────────────────


def test_snapshot_starts_empty_and_clears_idempotently():
    assert browser_snapshot()["available"] is False
    assert browser_image_bytes() is None
    clear_browser_session()
    clear_browser_session()
    assert browser_snapshot()["available"] is False


def test_record_keeps_latest_activity_only():
    record_browser_activity(
        server="ddg-search",
        tool="web_search",
        text="first results",
    )
    record_browser_activity(
        server="playwright",
        tool="browser_navigate",
        text="Navigated to example.com",
        url="https://example.com",
        image_bytes=_png_bytes(),
        mime_type="image/png",
    )
    snapshot = browser_snapshot()
    assert snapshot["tool"] == "browser_navigate"
    assert snapshot["url"] == "https://example.com"
    assert snapshot["text"] == "Navigated to example.com"
    assert snapshot["has_image"] is True
    assert snapshot["image_url"] == "/api/furniture/browser/image"
    image, mime_type = browser_image_bytes()
    assert image == _png_bytes()
    assert mime_type == "image/png"


def test_snapshot_truncates_long_text():
    record_browser_activity(server="ddg-search", tool="web_search", text="x" * 5000)
    assert len(browser_snapshot()["text"]) == MAX_TEXT_CHARS


def test_snapshot_drops_oversized_screenshot():
    record_browser_activity(
        server="playwright",
        tool="browser_take_screenshot",
        text="captured",
        image_bytes=b"x" * (MAX_IMAGE_BYTES + 1),
    )
    snapshot = browser_snapshot()
    assert snapshot["has_image"] is False
    assert snapshot["text"] == "captured"
    assert browser_image_bytes() is None


# ── furniture routes ────────────────────────────────────────────────────────


def test_browser_window_route_starts_empty(client):
    body = client.get("/api/furniture/browser").json()
    assert body["available"] is False


def test_browser_window_route_serves_latest_activity(client):
    record_browser_activity(
        server="ddg-search",
        tool="web_search",
        text="top results for embodied agents",
        url="",
    )
    body = client.get("/api/furniture/browser").json()
    assert body["available"] is True
    assert body["tool"] == "web_search"
    assert body["text"] == "top results for embodied agents"
    assert body["has_image"] is False
    assert body["image_url"] == "/api/furniture/browser/image"


def test_browser_image_route_serves_bytes_then_404(client):
    assert client.get("/api/furniture/browser/image").status_code == 404
    record_browser_activity(
        server="playwright",
        tool="browser_take_screenshot",
        text="captured",
        image_bytes=_png_bytes(),
        mime_type="image/png",
    )
    response = client.get("/api/furniture/browser/image")
    assert response.status_code == 200
    assert response.content == _png_bytes()
    assert response.headers["content-type"] == "image/png"
    clear_browser_session()
    assert client.get("/api/furniture/browser/image").status_code == 404


# ── ToolExecutor recording ──────────────────────────────────────────────────


class _FakeTransaction:
    def __init__(self, result: SimpleNamespace) -> None:
        self._result = result

    async def execute(self, **kwargs) -> SimpleNamespace:
        return self._result


def _result(content_items: list[dict]) -> SimpleNamespace:
    text = next(
        (item.get("text", "") for item in content_items if item.get("type") == "text"),
        "",
    )
    return SimpleNamespace(
        is_error=False,
        text=text,
        metadata={},
        content_items=content_items,
    )


def _executor_with_tools(tools: dict, content_items: list[dict] | None = None) -> ToolExecutor:
    manager = ToolManager(initial_tools_dict=tools)
    return ToolExecutor(
        mcp_client=None,
        tool_manager=manager,
        action_transactions=_FakeTransaction(
            _result(content_items or [{"type": "text", "text": "ok"}])
        ),
    )


async def _run_tool(executor: ToolExecutor, name: str, args: dict) -> None:
    updates = []
    async for update in executor.execute_tools(
        [{"id": "t1", "name": name, "args": args}],
        caller_mode="Prompt",
    ):
        updates.append(update)
    assert any(item.get("type") == "final_tool_results" for item in updates)


def test_executor_records_playwright_navigation_url_and_screenshot():
    executor = _executor_with_tools(
        {
            "browser_navigate": FormattedTool(
                input_schema={},
                related_server="playwright",
            )
        },
        content_items=[
            {"type": "text", "text": "Navigated to example.com"},
            {
                "type": "image",
                "data": base64.b64encode(_png_bytes()).decode("ascii"),
                "mimeType": "image/png",
            },
        ],
    )

    async def run():
        await _run_tool(
            executor,
            "browser_navigate",
            {"url": "https://example.com"},
        )

    asyncio.run(run())
    snapshot = browser_snapshot()
    assert snapshot["tool"] == "browser_navigate"
    assert snapshot["url"] == "https://example.com"
    assert snapshot["text"] == "Navigated to example.com"
    assert snapshot["has_image"] is True
    image, _mime = browser_image_bytes()
    assert image == _png_bytes()


def test_executor_records_web_search_text():
    executor = _executor_with_tools(
        {
            "web_search": FormattedTool(
                input_schema={},
                related_server="ddg-search",
            )
        }
    )

    async def run():
        await _run_tool(executor, "web_search", {"query": "embodied agents"})

    asyncio.run(run())
    assert browser_snapshot()["tool"] == "web_search"


def test_executor_does_not_record_other_servers_or_errors():
    executor = _executor_with_tools(
        {
            "list_books": FormattedTool(
                input_schema={},
                related_server="library",
            )
        }
    )

    async def run():
        await _run_tool(executor, "list_books", {})

    asyncio.run(run())
    assert browser_snapshot()["available"] is False

    executor = _executor_with_tools(
        {
            "browser_navigate": FormattedTool(
                input_schema={},
                related_server="playwright",
            )
        }
    )
    executor._action_transactions = _FakeTransaction(
        SimpleNamespace(
            is_error=True,
            text="Error: timeout",
            metadata={},
            content_items=[{"type": "text", "text": "Error: timeout"}],
        )
    )

    async def run_error():
        await _run_tool(executor, "browser_navigate", {"url": "https://example.com"})

    asyncio.run(run_error())
    assert browser_snapshot()["available"] is False


# ── default documents and legacy backfill ───────────────────────────────────


def test_computer_has_browser_window_in_both_defaults():
    normal = get_default_stage("character")
    vr = get_default_vr_stage("character")
    normal_computer = next(n for n in normal["nodes"] if n["id"] == "computer")
    vr_computer = next(n for n in vr["nodes"] if n["id"] == "computer")
    assert normal_computer["uiWindow"]["provider"] == "browser_status"
    assert vr_computer["uiWindow"] == normal_computer["uiWindow"]
    assert validate_vr_stage(vr) == (True, [])


def test_legacy_vr_room_gains_browser_window_in_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    legacy = get_default_vr_stage("character")
    legacy_computer = next(n for n in legacy["nodes"] if n["id"] == "computer")
    legacy_computer.pop("uiWindow", None)
    saved, errors = save_vr_stage_state("character", legacy)
    assert saved, errors

    loaded = load_vr_stage_state("character")
    loaded_ids = {n["id"]: n for n in loaded["nodes"]}
    assert loaded_ids["computer"]["uiWindow"]["provider"] == "browser_status"
    for node_id, node in loaded_ids.items():
        if node_id == "computer":
            continue
        saved_node = next(n for n in legacy["nodes"] if n["id"] == node_id)
        assert node == saved_node

    again = load_vr_stage_state("character")
    assert again == loaded


def test_legacy_normal_room_gains_browser_window_in_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    legacy = get_default_stage("character")
    legacy_computer = next(n for n in legacy["nodes"] if n["id"] == "computer")
    legacy_computer.pop("uiWindow", None)
    saved, errors = save_stage_state("character", legacy)
    assert saved, errors

    loaded = load_stage_state("character")
    loaded_ids = {n["id"]: n for n in loaded["nodes"]}
    assert loaded_ids["computer"]["uiWindow"]["provider"] == "browser_status"
    for node_id, node in loaded_ids.items():
        if node_id == "computer":
            continue
        saved_node = next(n for n in legacy["nodes"] if n["id"] == node_id)
        assert node == saved_node

    again = load_stage_state("character")
    assert again == loaded
