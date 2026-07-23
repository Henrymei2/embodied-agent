from __future__ import annotations

import pytest

from src.open_llm_vtuber.autonomy import screen_context as sc


@pytest.fixture(autouse=True)
def clean_store():
    sc._CLIENT_CONTEXT.clear()
    yield
    sc._CLIENT_CONTEXT.clear()


def test_first_context_is_a_task_change_and_is_stored():
    context, changed = sc.update_screen_context(
        "client", {"app": "VS Code", "title": "memory_manager.py", "idle_seconds": 4}
    )

    assert changed is True
    assert context.app == "VS Code"
    assert context.title == "memory_manager.py"
    assert sc.get_screen_context("client") is not None


def test_summary_is_a_single_coarse_line():
    context, _ = sc.update_screen_context(
        "client", {"app": "Chrome", "title": "Open-Meteo docs", "idle_seconds": 130}
    )
    assert context.summary() == "Chrome — Open-Meteo docs (idle 2m)"

    title_only, _ = sc.update_screen_context("client", {"title": "Just a title"})
    assert title_only.summary() == "Just a title"


def test_same_task_does_not_re_trigger_a_change():
    sc.update_screen_context("client", {"app": "VS Code", "title": "file.py"})
    # Cosmetic churn: an unsaved-dot marker and new idle must not re-fire.
    _context, changed = sc.update_screen_context(
        "client", {"app": "VS Code", "title": "● file.py", "idle_seconds": 45}
    )
    assert changed is False


def test_switching_task_fires_again_and_back_is_a_change_too():
    sc.update_screen_context("client", {"app": "VS Code", "title": "file.py"})
    _c, changed = sc.update_screen_context(
        "client", {"app": "Terminal", "title": "zsh"}
    )
    assert changed is True
    # Returning to the first task is a fresh change (we only remember the last).
    _c, changed_back = sc.update_screen_context(
        "client", {"app": "VS Code", "title": "file.py"}
    )
    assert changed_back is True


def test_per_client_isolation():
    sc.update_screen_context("a", {"app": "VS Code", "title": "a.py"})
    _c, changed = sc.update_screen_context("b", {"app": "VS Code", "title": "a.py"})
    assert changed is True  # different client, first observation


def test_context_expires_after_ttl(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(sc, "monotonic", lambda: clock["now"])

    sc.update_screen_context("client", {"app": "VS Code", "title": "file.py"})
    assert sc.get_screen_context("client") is not None

    clock["now"] += sc._SIGNAL_TTL_SECONDS + 1
    assert sc.get_screen_context("client") is None


def test_clear_removes_context():
    sc.update_screen_context("client", {"app": "VS Code", "title": "file.py"})
    sc.clear_screen_context("client")
    assert sc.get_screen_context("client") is None


def test_requires_app_or_title():
    with pytest.raises(ValueError, match="requires an app or title"):
        sc.update_screen_context("client", {"idle_seconds": 5})


def test_rejects_oversized_payload_as_probable_raw_screen_text():
    with pytest.raises(ValueError, match="cannot exceed"):
        sc.update_screen_context("client", {"title": "x" * 5000})


def test_rejects_non_string_and_structured_fields():
    with pytest.raises(ValueError, match="must be a string"):
        sc.update_screen_context("client", {"app": {"nested": "object"}})
    with pytest.raises(ValueError, match="idle_seconds"):
        sc.update_screen_context("client", {"title": "ok", "idle_seconds": "soon"})


def test_control_characters_are_stripped():
    context, _ = sc.update_screen_context(
        "client", {"app": "VS\tCode\n", "title": "a\x00b   c"}
    )
    assert context.app == "VS Code"
    assert context.title == "a b c"
