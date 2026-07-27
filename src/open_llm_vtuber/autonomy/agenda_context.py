"""Bounded evidence assembly for agenda planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .agenda import AgendaStore


@dataclass
class AgendaContext:
    payload: dict[str, Any]
    evidence_refs: set[str]
    capabilities: set[str]
    object_names: set[str]


def _memory_passages(root: Path, limit_chars: int = 5000) -> tuple[list[dict], set[str]]:
    passages: list[dict] = []
    references: set[str] = set()
    candidates = [
        root / "MEMORY.md",
        root / "memory" / f"{date.today().isoformat()}.md",
    ]
    remaining = limit_chars
    for path in candidates:
        if remaining <= 0 or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()[:remaining]
        except OSError:
            continue
        if not text:
            continue
        ref = f"memory:{path.name}"
        passages.append({"ref": ref, "text": text})
        references.add(ref)
        remaining -= len(text)
    return passages, references


def build_agenda_context(
    store: AgendaStore,
    *,
    client_uid: str,
    capabilities: set[str] | None = None,
    object_names: set[str] | None = None,
) -> AgendaContext:
    state = store.load()
    recent = sorted(state.entries, key=lambda item: item.created_at, reverse=True)[:20]
    memory, refs = _memory_passages(store.root)
    for entry in recent:
        refs.add(f"agenda:{entry.id}")

    presence: dict[str, Any] = {}
    try:
        from .observation import get_client_presence

        presence = asdict(get_client_presence(client_uid))
    except Exception:
        pass

    return AgendaContext(
        payload={
            "paused": state.paused,
            "next_reconsideration_at": state.next_reconsideration_at,
            "active": next(
                (asdict(item) for item in recent if item.status == "active"), None
            ),
            "recent_entries": [asdict(item) for item in recent],
            "rejections": [asdict(item) for item in state.rejections[-10:]],
            "memory_passages": memory,
            "presence": presence,
            "capabilities": sorted(capabilities or set()),
            "objects": sorted(object_names or set()),
        },
        evidence_refs=refs,
        capabilities=capabilities or set(),
        object_names=object_names or set(),
    )
