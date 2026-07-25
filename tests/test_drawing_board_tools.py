from __future__ import annotations

import base64
import importlib
import json
import sys

import pytest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@pytest.fixture
def drawing_board(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for name in ("src.open_llm_vtuber.furniture_state", "mcp_servers.drawing_board_tools"):
        sys.modules.pop(name, None)
    module = importlib.import_module("mcp_servers.drawing_board_tools")
    return module, tmp_path / "workspace"


def _decode(result: str) -> dict:
    assert not result.startswith("Error:"), result
    return json.loads(result)


def test_draw_image_saves_latest_image_and_metadata(drawing_board, monkeypatch):
    tools, workspace = drawing_board
    encoded = base64.b64encode(PNG_1X1).decode("ascii")

    def fake_call(prompt: str, *, model: str, size: str, quality: str) -> str:
        assert prompt == "a tiny pixel cat"
        assert model == "gpt-image-1"
        assert size == "1024x1024"
        assert quality == "auto"
        return encoded

    monkeypatch.setattr(tools, "_call_openai_image_api", fake_call)

    payload = _decode(tools.draw_image("a tiny pixel cat"))

    drawing = payload["drawing"]
    assert payload["ok"] is True
    assert drawing["prompt"] == "a tiny pixel cat"
    assert drawing["image_url"] == "/api/furniture/drawing-board/image/latest"
    assert (workspace / "drawing_board" / "latest.png").read_bytes() == PNG_1X1
    assert (workspace / "drawing_board" / f"{drawing['drawing_id']}.png").exists()

    snapshot = _decode(tools.get_last_drawing())
    assert snapshot["available"] is True
    assert snapshot["has_image"] is True
    assert snapshot["drawing"]["prompt"] == "a tiny pixel cat"


def test_get_last_drawing_reports_empty_state(drawing_board):
    tools, _workspace = drawing_board

    payload = _decode(tools.get_last_drawing())

    assert payload == {
        "available": True,
        "has_image": False,
        "drawing": None,
    }


def test_draw_image_rejects_bad_inputs(drawing_board):
    tools, _workspace = drawing_board

    assert tools.draw_image("   ") == "Error: prompt cannot be empty"
    assert tools.draw_image("x", size="huge") == (
        "Error: size must be one of: 1024x1024, 1024x1536, 1536x1024, auto"
    )
    assert tools.draw_image("x", quality="perfect") == (
        "Error: quality must be one of: auto, high, low, medium"
    )


def test_draw_image_requires_api_key(drawing_board, monkeypatch):
    tools, _workspace = drawing_board
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert tools.draw_image("a small house") == "Error: OPENAI_API_KEY is not set"
