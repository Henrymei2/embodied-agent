from src.open_llm_vtuber import furniture_state, skill_state
from src.open_llm_vtuber.autonomy.agenda import AgendaStore
from src.open_llm_vtuber.autonomy.agenda_context import build_agenda_context


def test_agenda_context_includes_bounded_furniture_evidence(monkeypatch, tmp_path):
    (tmp_path / "MEMORY.md").write_text("The user is learning piano.", encoding="utf-8")
    monkeypatch.setattr(
        furniture_state,
        "todo_snapshot",
        lambda _limit: {
            "available": True,
            "open_count": 1,
            "items": [{"text": "Practice"}],
        },
    )
    monkeypatch.setattr(
        furniture_state,
        "timer_snapshot",
        lambda _limit: {"available": True, "count": 0, "timers": []},
    )
    monkeypatch.setattr(
        furniture_state,
        "bookshelf_snapshot",
        lambda _limit: {"available": True, "count": 0, "books": []},
    )
    monkeypatch.setattr(
        furniture_state,
        "reading_snapshot",
        lambda: {"available": True, "session": {"title": "Music Theory"}},
    )
    monkeypatch.setattr(
        furniture_state,
        "plan_snapshot",
        lambda _limit: {"available": True, "pending_count": 0, "pending": []},
    )
    monkeypatch.setattr(
        furniture_state,
        "drawing_board_snapshot",
        lambda: {"available": True, "has_image": False, "drawing": None},
    )
    monkeypatch.setattr(
        furniture_state,
        "scheduler_events_snapshot",
        lambda: {
            "count": 8,
            "events": [{"id": str(index)} for index in range(8)],
        },
    )
    monkeypatch.setattr(
        skill_state,
        "skill_snapshot",
        lambda _limit: {"available": True, "pending_count": 0, "active_count": 0},
    )

    context = build_agenda_context(AgendaStore(tmp_path), client_uid="client")

    assert (
        context.payload["memory_passages"][0]["text"] == "The user is learning piano."
    )
    assert context.payload["furniture_state"]["todo"]["open_count"] == 1
    assert len(context.payload["furniture_state"]["scheduler"]["events"]) == 5
    assert context.payload["furniture_state"]["scheduler"]["remaining"] == 3
    assert "furniture:todo" in context.evidence_refs
    assert "furniture:reading" in context.evidence_refs
    assert "furniture:scheduler" in context.evidence_refs
    assert "furniture:timers" not in context.evidence_refs


def test_one_broken_furniture_snapshot_does_not_drop_the_context(monkeypatch, tmp_path):
    def broken(_limit):
        raise ValueError("broken todo store")

    monkeypatch.setattr(furniture_state, "todo_snapshot", broken)
    context = build_agenda_context(AgendaStore(tmp_path), client_uid="client")

    assert context.payload["furniture_state"]["todo"]["available"] is False
    assert "furniture:todo" not in context.evidence_refs
    assert "memory_passages" in context.payload
