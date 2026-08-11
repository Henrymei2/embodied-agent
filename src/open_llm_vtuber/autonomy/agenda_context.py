"""Bounded evidence assembly for agenda planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .agenda import AgendaStore

_FURNITURE_ITEM_LIMIT = 5
_FURNITURE_STRING_LIMIT = 600
_PLANNER_RECENT_ENTRY_LIMIT = 8
_PLANNER_PROGRESS_LIMIT = 300
_PLANNER_PHYSICAL_CONTEXT_LIMIT = 2000


@dataclass
class AgendaContext:
    payload: dict[str, Any]
    evidence_refs: set[str]
    capabilities: set[str]
    object_names: set[str]


def _memory_passages(
    root: Path, limit_chars: int = 3000
) -> tuple[list[dict], set[str]]:
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


def _furniture_snapshots() -> tuple[dict[str, Any], set[str]]:
    """Read the same compact, path-free views shown by furniture windows."""
    from ..furniture_state import (
        bookshelf_snapshot,
        drawing_board_snapshot,
        plan_snapshot,
        reading_snapshot,
        scheduler_events_snapshot,
        timer_snapshot,
        todo_snapshot,
    )
    from ..skill_state import skill_snapshot

    readers = {
        "todo": lambda: todo_snapshot(_FURNITURE_ITEM_LIMIT),
        "timers": lambda: timer_snapshot(_FURNITURE_ITEM_LIMIT),
        "bookshelf": lambda: bookshelf_snapshot(_FURNITURE_ITEM_LIMIT),
        "reading": reading_snapshot,
        "plans": lambda: plan_snapshot(_FURNITURE_ITEM_LIMIT),
        "drawing_board": drawing_board_snapshot,
        "scheduler": scheduler_events_snapshot,
        "skills": lambda: skill_snapshot(_FURNITURE_ITEM_LIMIT),
    }
    snapshots: dict[str, Any] = {}
    refs: set[str] = set()
    for name, reader in readers.items():
        try:
            snapshot = _bounded_furniture_value(reader())
            if isinstance(snapshot, dict) and snapshot.get("available") is False:
                snapshot = {"available": False}
            if name == "scheduler" and isinstance(snapshot, dict):
                events = snapshot.get("events", [])
                snapshot = {
                    **snapshot,
                    "events": events[:_FURNITURE_ITEM_LIMIT]
                    if isinstance(events, list)
                    else [],
                    "remaining": max(
                        0,
                        int(snapshot.get("count", 0)) - _FURNITURE_ITEM_LIMIT,
                    ),
                }
            snapshots[name] = snapshot
            if _has_furniture_content(snapshot):
                refs.add(f"furniture:{name}")
        except (OSError, TypeError, ValueError):
            snapshots[name] = {"available": False}
    return snapshots, refs


def _bounded_furniture_value(value: Any, depth: int = 0) -> Any:
    """Keep user-authored furniture data useful without unbounded prompts."""
    if depth >= 6:
        return None
    if isinstance(value, str):
        return value[:_FURNITURE_STRING_LIMIT]
    if isinstance(value, list):
        return [
            _bounded_furniture_value(item, depth + 1)
            for item in value[:_FURNITURE_ITEM_LIMIT]
        ]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _bounded_furniture_value(item, depth + 1)
            for key, item in list(value.items())[:30]
        }
    return (
        value
        if isinstance(value, (int, float, bool)) or value is None
        else str(value)[:_FURNITURE_STRING_LIMIT]
    )


def _has_furniture_content(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict) or snapshot.get("available") is False:
        return False
    if snapshot.get("session") or snapshot.get("recent") or snapshot.get("drawing"):
        return True
    for key in (
        "open_count",
        "count",
        "pending_count",
        "active_count",
        "finished_count",
    ):
        value = snapshot.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return True
    return any(
        isinstance(snapshot.get(key), list) and bool(snapshot[key])
        for key in (
            "items",
            "timers",
            "books",
            "pending",
            "active",
            "events",
            "skills",
        )
    )


def _planner_entry(entry) -> dict[str, Any]:
    """Project durable work into the small history needed for a new decision."""
    interruption = None
    if entry.metadata.get("intention_outcome") == "interrupted":
        interruption = {
            "outcome": "interrupted",
            "reason": str(entry.metadata.get("interruption_reason", ""))[:100],
        }
    return {
        "id": entry.id,
        "kind": entry.kind,
        "title": entry.title,
        "origin": entry.origin,
        "status": entry.status,
        "motivation_type": entry.motivation.type,
        "evidence_refs": list(entry.motivation.evidence_refs),
        "progress_summary": entry.progress_summary[:_PLANNER_PROGRESS_LIMIT],
        "preferred_object": entry.preferred_object,
        "required_capabilities": list(entry.required_capabilities),
        "report_policy": entry.report_policy,
        "ended_at": entry.ended_at,
        "interruption": interruption,
    }


def build_agenda_context(
    store: AgendaStore,
    *,
    client_uid: str,
    capabilities: set[str] | None = None,
    object_names: set[str] | None = None,
    tool_schemas: dict[str, dict] | None = None,
    execution_mode: str = "live",
) -> AgendaContext:
    state = store.load()
    recent = sorted(state.entries, key=lambda item: item.created_at, reverse=True)[
        :_PLANNER_RECENT_ENTRY_LIMIT
    ]
    memory, refs = _memory_passages(store.root)
    furniture_state, furniture_refs = _furniture_snapshots()
    refs.update(furniture_refs)
    for entry in recent:
        refs.add(f"agenda:{entry.id}")
        refs.update(entry.motivation.evidence_refs)
    inbox = [event for event in state.inbox if event.status in {"pending", "claimed"}][
        :5
    ]
    for event in inbox:
        refs.add(event.evidence_ref)
        refs.add(f"event:{event.id}")

    presence: dict[str, Any] = {}
    attention: dict[str, Any] = {}
    physical_context = ""
    try:
        from .observation import build_observation
        from .presence import get_presence_ledger

        presence = asdict(build_observation(client_uid))
        ledger = get_presence_ledger(client_uid)
        if ledger is not None:
            snapshot = ledger.snapshot()
            attention = {
                "mode": snapshot.get("mode", "enforced"),
                "budget": snapshot.get("attention_budget"),
                "budget_max": snapshot.get("attention_budget_max"),
                "snoozed": snapshot.get("snoozed"),
                "snoozed_until": snapshot.get("snoozed_until"),
                "direction_affinity": snapshot.get("direction_affinity", {}),
                "intention_generation_unrestricted": (
                    snapshot.get("mode") == "observe_only"
                ),
            }
    except Exception:
        pass
    try:
        from ..physical_context import consume_physical_prompt
        from ..client_registry import get_client_context

        client_context = get_client_context(client_uid)
        conf_uid = getattr(
            getattr(client_context, "character_config", None), "conf_uid", ""
        )
        physical_context = (
            consume_physical_prompt(conf_uid)
            if execution_mode == "live" and conf_uid
            else ""
        )
    except Exception:
        physical_context = ""

    return AgendaContext(
        payload={
            "paused": state.paused,
            "next_reconsideration_at": state.next_reconsideration_at,
            "active": next(
                (_planner_entry(item) for item in recent if item.status == "active"),
                None,
            ),
            "recent_entries": [_planner_entry(item) for item in recent],
            "rejections": [asdict(item) for item in state.rejections[-5:]],
            "memory_passages": memory,
            "furniture_state": furniture_state,
            "external_events": [
                {
                    "id": event.id,
                    "source": event.source,
                    "kind": event.kind,
                    "summary": event.summary,
                    "evidence_ref": event.evidence_ref,
                    "payload": _bounded_furniture_value(event.payload),
                    "status": event.status,
                    "attempts": event.attempts,
                }
                for event in inbox
            ],
            "presence": presence,
            "attention": attention,
            "physical_context": physical_context[:_PLANNER_PHYSICAL_CONTEXT_LIMIT],
            "continuity_health": {
                "planner": asdict(state.planner_health),
                "pending_reports": sum(
                    1 for report in state.reports if report.status == "pending"
                ),
                "dead_letters": len(state.dead_letters),
                "quota": asdict(state.quota),
            },
            "execution_mode": execution_mode,
            "execution_constraints": (
                [
                    "No live embodied room is connected.",
                    "Do not propose movement or object-bound tool work.",
                    "Safe reflection, evaluation, and bounded waiting may continue.",
                    "Completion reports are retained for later delivery.",
                ]
                if execution_mode == "headless"
                else []
            ),
            "capabilities": sorted(capabilities or set()),
            "objects": sorted(object_names or set()),
            "tool_schemas": dict(tool_schemas or {}),
            "available_evidence_refs": sorted(refs),
        },
        evidence_refs=refs,
        capabilities=capabilities or set(),
        object_names=object_names or set(),
    )
