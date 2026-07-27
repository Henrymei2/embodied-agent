"""Persistent, transactional global agenda.

The agenda is the single source of truth for foreground ownership, autonomous
continuity, scheduled work, timers, and restart recovery.  It intentionally
stores semantic activities rather than individual tool calls.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional, TypeVar

from filelock import FileLock, Timeout

from ..workspace import DEFAULT_WORKSPACE_DIR

SCHEMA_VERSION = 1
TERMINAL_STATUSES = {"completed", "failed", "abandoned"}
ACTIVE_STATUSES = {"active"}
ENTRY_KINDS = {"pursuit", "idle", "scheduled", "user"}
ENTRY_STATUSES = {
    "proposed",
    "blocked_approval",
    "queued",
    "active",
    "suspended",
    *TERMINAL_STATUSES,
}
REPORT_POLICIES = {"silent", "if_noteworthy", "on_completion", "ask_first"}
STEP_KINDS = {
    "move_to_object",
    "invoke_tool",
    "wait",
    "read_result",
    "write_artifact",
    "reflect",
    "evaluate_completion",
    "report_result",
    "request_approval",
}
_T = TypeVar("_T")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime] = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat()


def agenda_workspace_root() -> Path:
    configured = os.environ.get("EMBODIED_AGENT_WORKSPACE")
    return Path(configured).expanduser() if configured else DEFAULT_WORKSPACE_DIR


@dataclass
class Motivation:
    type: str
    evidence_refs: list[str]


@dataclass
class CompletionCondition:
    description: str


@dataclass
class ResourceBudget:
    time_budget_seconds: int
    deadline_at: Optional[str] = None


@dataclass
class ExecutionCheckpoint:
    step_id: str
    kind: str
    state: Literal["planned", "running", "result_ready", "completed", "uncertain"]
    purpose: str = ""
    required_object: Optional[str] = None
    required_capability: Optional[str] = None
    arguments: dict[str, Any] = field(default_factory=dict)
    result_summary: Optional[str] = None
    timeout_seconds: int = 120
    plausible_duration_seconds: float = 0.0
    interruptible: bool = True
    side_effecting: bool = False
    updated_at: str = field(default_factory=_iso)

    def validate(self) -> None:
        if self.kind not in STEP_KINDS:
            raise ValueError(f"unsupported agenda step kind: {self.kind}")
        if not self.step_id or self.timeout_seconds <= 0:
            raise ValueError("agenda steps require an id and positive timeout")


@dataclass
class ScheduleSpec:
    next_run: str
    interval_seconds: Optional[float] = None
    priority: int = 10
    report: bool = True
    timer: bool = False
    prompt: str = ""
    run_count: int = 0


@dataclass
class AgendaEntry:
    id: str
    kind: str
    title: str
    description: str
    origin: str
    motivation: Motivation
    status: str
    completion_condition: CompletionCondition
    budget: ResourceBudget
    created_at: str = field(default_factory=_iso)
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    progress_summary: str = ""
    preferred_object: Optional[str] = None
    required_capabilities: list[str] = field(default_factory=list)
    risk: str = "low"
    requires_approval: bool = False
    interruptibility: str = "high"
    resume_policy: str = "reconsider"
    report_policy: str = "silent"
    parent_id: Optional[str] = None
    plan_id: Optional[str] = None
    schedule: Optional[ScheduleSpec] = None
    checkpoint: Optional[ExecutionCheckpoint] = None
    queue_position: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.kind not in ENTRY_KINDS:
            raise ValueError(f"invalid agenda entry kind: {self.kind}")
        if self.status not in ENTRY_STATUSES:
            raise ValueError(f"invalid agenda entry status: {self.status}")
        if not self.id or not self.title.strip():
            raise ValueError("agenda entries require an id and title")
        if self.kind != "user":
            if not self.motivation.evidence_refs:
                raise ValueError("autonomous agenda entries require grounding evidence")
            if self.budget.time_budget_seconds <= 0:
                raise ValueError("agenda entries require a finite positive time budget")
            if not self.completion_condition.description.strip():
                raise ValueError("agenda entries require a completion condition")
        if self.report_policy not in REPORT_POLICIES:
            raise ValueError(f"invalid report policy: {self.report_policy}")
        if self.checkpoint:
            self.checkpoint.validate()


@dataclass
class AgendaEvent:
    id: str
    entry_id: Optional[str]
    type: str
    created_at: str = field(default_factory=_iso)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProposalRejection:
    created_at: str
    title: str
    reason: str
    fingerprint: str = ""


@dataclass
class AgendaState:
    version: int = SCHEMA_VERSION
    paused: bool = False
    next_reconsideration_at: Optional[str] = None
    entries: list[AgendaEntry] = field(default_factory=list)
    events: list[AgendaEvent] = field(default_factory=list)
    rejections: list[ProposalRejection] = field(default_factory=list)
    updated_at: str = field(default_factory=_iso)


def _entry_from_dict(raw: dict[str, Any]) -> AgendaEntry:
    raw = dict(raw)
    raw["motivation"] = Motivation(**raw.get("motivation", {}))
    raw["completion_condition"] = CompletionCondition(
        **raw.get("completion_condition", {})
    )
    raw["budget"] = ResourceBudget(**raw.get("budget", {}))
    if raw.get("schedule"):
        raw["schedule"] = ScheduleSpec(**raw["schedule"])
    if raw.get("checkpoint"):
        raw["checkpoint"] = ExecutionCheckpoint(**raw["checkpoint"])
    entry = AgendaEntry(**raw)
    entry.validate()
    return entry


def _state_from_dict(raw: dict[str, Any]) -> AgendaState:
    if int(raw.get("version", 0)) != SCHEMA_VERSION:
        raise ValueError("unsupported agenda schema version")
    return AgendaState(
        version=SCHEMA_VERSION,
        paused=bool(raw.get("paused", False)),
        next_reconsideration_at=raw.get("next_reconsideration_at"),
        entries=[_entry_from_dict(item) for item in raw.get("entries", [])],
        events=[AgendaEvent(**item) for item in raw.get("events", [])],
        rejections=[ProposalRejection(**item) for item in raw.get("rejections", [])],
        updated_at=str(raw.get("updated_at") or _iso()),
    )


class AgendaStore:
    """File-backed agenda with cross-process locking and atomic replacement."""

    def __init__(self, workspace_root: Optional[Path] = None) -> None:
        self.root = workspace_root or agenda_workspace_root()
        self.path = self.root / "agenda" / "agenda.json"
        self.lock = FileLock(str(self.root / "agenda" / "agenda.lock"), timeout=5)

    def load(self) -> AgendaState:
        if not self.path.exists():
            return AgendaState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("agenda root must be an object")
            state = _state_from_dict(raw)
            self._validate_state(state)
            return state
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"agenda store is unreadable: {exc}") from exc

    def save(self, state: AgendaState) -> None:
        self._validate_state(state)
        state.updated_at = _iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(asdict(state), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def mutate(self, operation: Callable[[AgendaState], _T]) -> _T:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.lock:
                state = self.load()
                result = operation(state)
                self.save(state)
                return result
        except Timeout as exc:
            raise OSError("agenda store is busy") from exc

    @staticmethod
    def _validate_state(state: AgendaState) -> None:
        active = 0
        ids: set[str] = set()
        for entry in state.entries:
            entry.validate()
            if entry.id in ids:
                raise ValueError(f"duplicate agenda entry id: {entry.id}")
            ids.add(entry.id)
            active += int(entry.status == "active")
        if active > 1:
            raise ValueError("agenda may have at most one active foreground entry")

    def snapshot(self, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        state = self.load()
        active = next((e for e in state.entries if e.status == "active"), None)
        ordered = sorted(state.entries, key=lambda e: e.created_at, reverse=True)
        return {
            "version": state.version,
            "paused": state.paused,
            "next_reconsideration_at": state.next_reconsideration_at,
            "active": asdict(active) if active else None,
            "entries": [asdict(e) for e in ordered[offset : offset + limit]],
            "total": len(ordered),
            "updated_at": state.updated_at,
        }

    def add(self, entry: AgendaEntry) -> AgendaEntry:
        entry.validate()

        def operation(state: AgendaState) -> AgendaEntry:
            if any(existing.id == entry.id for existing in state.entries):
                raise ValueError(f"agenda entry already exists: {entry.id}")
            if entry.status == "active":
                self._suspend_active(state, except_id=entry.id)
                entry.started_at = entry.started_at or _iso()
            state.entries.append(entry)
            self._event(state, entry.id, "created")
            return entry

        return self.mutate(operation)

    def transition(
        self,
        entry_id: str,
        status: str,
        *,
        progress_summary: Optional[str] = None,
        checkpoint: Optional[ExecutionCheckpoint] = None,
    ) -> AgendaEntry:
        if status not in ENTRY_STATUSES:
            raise ValueError(f"invalid agenda status: {status}")

        def operation(state: AgendaState) -> AgendaEntry:
            entry = self._find(state, entry_id)
            if entry.status in TERMINAL_STATUSES:
                raise ValueError("terminal agenda entries cannot transition")
            if status == "active":
                self._suspend_active(state, except_id=entry_id)
                entry.started_at = entry.started_at or _iso()
                if not entry.budget.deadline_at:
                    entry.budget.deadline_at = _iso(
                        utc_now() + timedelta(seconds=entry.budget.time_budget_seconds)
                    )
            if status in TERMINAL_STATUSES:
                entry.ended_at = _iso()
                entry.checkpoint = None
            entry.status = status
            if progress_summary is not None:
                entry.progress_summary = progress_summary[:1000]
            if checkpoint is not None:
                checkpoint.validate()
                entry.checkpoint = checkpoint
            self._event(state, entry_id, "transition", {"status": status})
            return entry

        return self.mutate(operation)

    def pause(self, paused: bool) -> AgendaState:
        def operation(state: AgendaState) -> AgendaState:
            state.paused = paused
            self._event(state, None, "paused" if paused else "resumed")
            if paused:
                for entry in state.entries:
                    if entry.status == "active" and entry.origin == "autonomous":
                        entry.status = "suspended"
            return state

        return self.mutate(operation)

    def remove_queued(self, entry_id: str) -> bool:
        def operation(state: AgendaState) -> bool:
            entry = self._find(state, entry_id)
            if entry.status not in {"queued", "proposed"}:
                raise ValueError("only queued or proposed entries may be removed")
            state.entries = [item for item in state.entries if item.id != entry_id]
            self._event(state, entry_id, "removed")
            return True

        return self.mutate(operation)

    def reorder(self, entry_id: str, position: int) -> AgendaEntry:
        def operation(state: AgendaState) -> AgendaEntry:
            entry = self._find(state, entry_id)
            if entry.status != "queued":
                raise ValueError("only queued entries may be reordered")
            entry.queue_position = max(0, int(position))
            return entry

        return self.mutate(operation)

    def claim_due(self, now: Optional[datetime] = None) -> list[AgendaEntry]:
        now = (now or utc_now()).astimezone(timezone.utc)

        def operation(state: AgendaState) -> list[AgendaEntry]:
            claimed: list[AgendaEntry] = []
            schedules = [
                item
                for item in state.entries
                if item.schedule and item.status in {"queued", "suspended"}
            ]
            for template in schedules:
                due = datetime.fromisoformat(template.schedule.next_run)
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                if due > now:
                    continue
                occurrence = _entry_from_dict(asdict(template))
                occurrence.id = new_entry_id("scheduled")
                occurrence.parent_id = template.id
                occurrence.status = "queued"
                occurrence.created_at = _iso(now)
                occurrence.schedule = None
                occurrence.metadata["schedule_occurrence"] = True
                claimed.append(occurrence)
                state.entries.append(occurrence)
                template.schedule.run_count += 1
                if template.schedule.interval_seconds:
                    template.schedule.next_run = _iso(
                        now + timedelta(seconds=template.schedule.interval_seconds)
                    )
                else:
                    template.status = "completed"
                    template.ended_at = _iso(now)
                self._event(state, occurrence.id, "schedule_due")
            return claimed

        return self.mutate(operation)

    def recover_interrupted(self) -> list[str]:
        def operation(state: AgendaState) -> list[str]:
            recovered: list[str] = []
            for entry in state.entries:
                if entry.status != "active":
                    continue
                if entry.checkpoint and entry.checkpoint.side_effecting:
                    entry.checkpoint.state = "uncertain"
                entry.status = "suspended"
                entry.progress_summary = "Interrupted by restart; requires reconsideration."
                recovered.append(entry.id)
                self._event(state, entry.id, "restart_recovery")
            return recovered

        return self.mutate(operation)

    def compact_terminal_history(
        self,
        write_digest: Callable[[str], bool],
        *,
        retention_hours: int = 24,
        retention_entries: int = 30,
    ) -> int:
        """Write semantic digests to memory before pruning terminal entries."""
        state = self.load()
        terminal = sorted(
            (entry for entry in state.entries if entry.status in TERMINAL_STATUSES),
            key=lambda entry: entry.ended_at or entry.created_at,
            reverse=True,
        )
        cutoff = utc_now() - timedelta(hours=retention_hours)
        expired = [
            entry
            for index, entry in enumerate(terminal)
            if index >= retention_entries
            or datetime.fromisoformat(entry.ended_at or entry.created_at) < cutoff
        ]
        if not expired:
            return 0
        lines = ["Agenda history digest:"]
        for entry in reversed(expired):
            relationship = f"; parent={entry.parent_id}" if entry.parent_id else ""
            progress = f"; outcome={entry.progress_summary}" if entry.progress_summary else ""
            lines.append(
                f"- [{entry.status}] {entry.title}: {entry.description}"
                f"{progress}{relationship}; evidence={','.join(entry.motivation.evidence_refs)}"
            )
        if not write_digest("\n".join(lines)):
            return 0
        expired_ids = {entry.id for entry in expired}

        def operation(current: AgendaState) -> int:
            current.entries = [
                entry for entry in current.entries if entry.id not in expired_ids
            ]
            current.events = [
                event for event in current.events if event.entry_id not in expired_ids
            ]
            return len(expired_ids)

        return self.mutate(operation)

    @staticmethod
    def _find(state: AgendaState, entry_id: str) -> AgendaEntry:
        for entry in state.entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(entry_id)

    @staticmethod
    def _suspend_active(state: AgendaState, except_id: str) -> None:
        for entry in state.entries:
            if entry.id != except_id and entry.status == "active":
                entry.status = "suspended"
                entry.progress_summary = entry.progress_summary or "Preempted by foreground work."

    @staticmethod
    def _event(
        state: AgendaState,
        entry_id: Optional[str],
        kind: str,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        state.events.append(
            AgendaEvent(
                id=uuid.uuid4().hex,
                entry_id=entry_id,
                type=kind,
                data=data or {},
            )
        )
        state.events = state.events[-500:]


def new_entry_id(prefix: str = "intention") -> str:
    return f"{prefix}_{utc_now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def create_entry(
    *,
    kind: str,
    title: str,
    description: str,
    origin: str,
    evidence_refs: list[str],
    completion_condition: str,
    time_budget_seconds: int,
    status: str = "queued",
    report_policy: str = "silent",
    **kwargs: Any,
) -> AgendaEntry:
    return AgendaEntry(
        id=new_entry_id(kind),
        kind=kind,
        title=title,
        description=description,
        origin=origin,
        motivation=Motivation(type=origin, evidence_refs=evidence_refs),
        status=status,
        completion_condition=CompletionCondition(completion_condition),
        budget=ResourceBudget(time_budget_seconds),
        report_policy=report_policy,
        **kwargs,
    )


_default_store: Optional[AgendaStore] = None


def get_agenda_store() -> AgendaStore:
    global _default_store
    root = agenda_workspace_root()
    if _default_store is None or _default_store.root != root:
        _default_store = AgendaStore(root)
    return _default_store
