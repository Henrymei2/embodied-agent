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


def _memory_passages(
    root: Path, limit_chars: int = 5000
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


def build_agenda_context(
    store: AgendaStore,
    *,
    client_uid: str,
    capabilities: set[str] | None = None,
    object_names: set[str] | None = None,
    execution_mode: str = "live",
) -> AgendaContext:
    state = store.load()
    recent = sorted(state.entries, key=lambda item: item.created_at, reverse=True)[:20]
    memory, refs = _memory_passages(store.root)
    for entry in recent:
        refs.add(f"agenda:{entry.id}")
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
        conf_uid = getattr(getattr(client_context, "character_config", None), "conf_uid", "")
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
                (asdict(item) for item in recent if item.status == "active"), None
            ),
            "recent_entries": [asdict(item) for item in recent],
            "rejections": [asdict(item) for item in state.rejections[-10:]],
            "memory_passages": memory,
            "external_events": [
                {
                    "id": event.id,
                    "source": event.source,
                    "kind": event.kind,
                    "summary": event.summary,
                    "evidence_ref": event.evidence_ref,
                    "payload": event.payload,
                    "status": event.status,
                    "attempts": event.attempts,
                }
                for event in inbox
            ],
            "presence": presence,
            "attention": attention,
            "physical_context": physical_context,
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
        },
        evidence_refs=refs,
        capabilities=capabilities or set(),
        object_names=object_names or set(),
    )
