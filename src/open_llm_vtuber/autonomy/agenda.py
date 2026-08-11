"""Persistent, transactional global agenda.

The agenda is the single source of truth for foreground ownership, autonomous
continuity, scheduled work, timers, and restart recovery.  It intentionally
stores semantic activities rather than individual tool calls.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import nullcontext
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
MOTIVATION_TYPES = {
    "curiosity",
    "care",
    "housekeeping",
    "growth",
    "social",
    "work",
}
REPORT_STATUSES = {"pending", "delivering", "delivered", "failed"}
EVENT_STATUSES = {"pending", "claimed", "acknowledged", "dead_letter"}
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
        if not isinstance(self.arguments, dict):
            raise ValueError("agenda step arguments must be an object")
        if len(json.dumps(self.arguments, ensure_ascii=False)) > 10_000:
            raise ValueError("agenda step arguments are too large")
        if self.plausible_duration_seconds < 0:
            raise ValueError("agenda step duration cannot be negative")
        for value, field_name in (
            (self.required_object, "required object"),
            (self.required_capability, "required capability"),
        ):
            if value is not None and (
                not isinstance(value, str) or not value.strip() or len(value) > 160
            ):
                raise ValueError(f"agenda step {field_name} is invalid")


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
        if self.schedule:
            try:
                datetime.fromisoformat(self.schedule.next_run)
            except (TypeError, ValueError) as exc:
                raise ValueError("scheduled entries require a valid next run") from exc
            if not 1 <= int(self.schedule.priority) <= 100:
                raise ValueError("scheduled priority must be between 1 and 100")
            if (
                self.schedule.interval_seconds is not None
                and self.schedule.interval_seconds <= 0
            ):
                raise ValueError("scheduled interval must be positive")
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
class ReportOutboxItem:
    """One durable completion report awaiting Presence-gated delivery."""

    id: str
    entry_id: str
    summary: str
    report_key: str = "completion"
    status: str = "pending"
    attempts: int = 0
    failures: int = 0
    next_attempt_at: str = field(default_factory=_iso)
    created_at: str = field(default_factory=_iso)
    updated_at: str = field(default_factory=_iso)
    error: Optional[str] = None

    def validate(self) -> None:
        if not self.id or not self.entry_id or not self.report_key:
            raise ValueError("report outbox items require ids")
        if self.status not in REPORT_STATUSES:
            raise ValueError(f"invalid report outbox status: {self.status}")
        if not self.summary.strip() or len(self.summary) > 2000:
            raise ValueError("report summaries must contain at most 2000 characters")
        if self.attempts < 0 or self.failures < 0:
            raise ValueError("report attempt counts cannot be negative")
        try:
            datetime.fromisoformat(self.next_attempt_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("report next-attempt time is invalid") from exc


@dataclass
class PlannerHealth:
    """Persistent circuit-breaker state across executive restarts."""

    consecutive_failures: int = 0
    circuit_open_until: Optional[str] = None
    last_error: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_success_at: Optional[str] = None


@dataclass
class ExternalEvent:
    """Durable, idempotent signal that may ground the next intention."""

    id: str
    idempotency_key: str
    source: str
    kind: str
    summary: str
    evidence_ref: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    attempts: int = 0
    available_at: str = field(default_factory=_iso)
    created_at: str = field(default_factory=_iso)
    updated_at: str = field(default_factory=_iso)
    claimed_at: Optional[str] = None
    claimed_by: Optional[str] = None
    claimed_fencing_token: Optional[int] = None
    linked_entry_id: Optional[str] = None
    error: Optional[str] = None

    def validate(self) -> None:
        if not self.id or not self.idempotency_key:
            raise ValueError("external events require ids and idempotency keys")
        if len(self.idempotency_key) > 200:
            raise ValueError("event idempotency key is too long")
        if not self.source.strip() or not self.kind.strip():
            raise ValueError("external events require source and kind")
        if len(self.source) > 100 or len(self.kind) > 100:
            raise ValueError("event source or kind is too long")
        if not self.summary.strip() or len(self.summary) > 500:
            raise ValueError("event summary must contain at most 500 characters")
        if not self.evidence_ref.strip() or len(self.evidence_ref) > 300:
            raise ValueError("event evidence reference is invalid")
        if self.status not in EVENT_STATUSES:
            raise ValueError(f"invalid external event status: {self.status}")
        if not isinstance(self.payload, dict):
            raise ValueError("event payload must be an object")
        if len(json.dumps(self.payload, ensure_ascii=False)) > 10_000:
            raise ValueError("event payload is too large")
        if self.attempts < 0:
            raise ValueError("event attempts cannot be negative")
        try:
            datetime.fromisoformat(self.available_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("event availability timestamp is invalid") from exc


@dataclass
class DeadLetter:
    id: str
    category: str
    reference_id: str
    reason: str
    attempts: int
    created_at: str = field(default_factory=_iso)
    last_failed_at: str = field(default_factory=_iso)
    data: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.id or not self.category or not self.reference_id:
            raise ValueError("dead letters require identity fields")
        if not self.reason.strip() or len(self.reason) > 500:
            raise ValueError("dead-letter reason is invalid")
        if self.attempts < 1:
            raise ValueError("dead-letter attempts must be positive")
        if len(json.dumps(self.data, ensure_ascii=False)) > 10_000:
            raise ValueError("dead-letter data is too large")


@dataclass
class QuotaWindow:
    window_started_at: str = field(default_factory=_iso)
    planner_calls: int = 0
    capability_calls: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    remaining: int
    reset_at: str


@dataclass
class AgendaState:
    version: int = SCHEMA_VERSION
    revision: int = 0
    fencing_token: int = 0
    paused: bool = False
    next_reconsideration_at: Optional[str] = None
    entries: list[AgendaEntry] = field(default_factory=list)
    events: list[AgendaEvent] = field(default_factory=list)
    rejections: list[ProposalRejection] = field(default_factory=list)
    reports: list[ReportOutboxItem] = field(default_factory=list)
    planner_health: PlannerHealth = field(default_factory=PlannerHealth)
    inbox: list[ExternalEvent] = field(default_factory=list)
    dead_letters: list[DeadLetter] = field(default_factory=list)
    quota: QuotaWindow = field(default_factory=QuotaWindow)
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
        revision=max(0, int(raw.get("revision", 0))),
        fencing_token=max(0, int(raw.get("fencing_token", 0))),
        paused=bool(raw.get("paused", False)),
        next_reconsideration_at=raw.get("next_reconsideration_at"),
        entries=[_entry_from_dict(item) for item in raw.get("entries", [])],
        events=[AgendaEvent(**item) for item in raw.get("events", [])],
        rejections=[ProposalRejection(**item) for item in raw.get("rejections", [])],
        reports=[ReportOutboxItem(**item) for item in raw.get("reports", [])],
        planner_health=PlannerHealth(**raw.get("planner_health", {})),
        inbox=[ExternalEvent(**item) for item in raw.get("inbox", [])],
        dead_letters=[DeadLetter(**item) for item in raw.get("dead_letters", [])],
        quota=QuotaWindow(**raw.get("quota", {})),
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
        from .lease import WorkspaceLease, current_lease

        lease_handle = current_lease()
        fenced = (
            lease_handle is not None
            and lease_handle.workspace_root.resolve() == self.root.resolve()
        )
        fence_guard = (
            WorkspaceLease(self.root).guard_current(lease_handle)
            if fenced
            else nullcontext()
        )
        try:
            with fence_guard:
                with self.lock:
                    state = self.load()
                    before = asdict(state)
                    if fenced:
                        if state.fencing_token > lease_handle.fencing_token:
                            from .lease import LeaseLostError

                            raise LeaseLostError(
                                "agenda state has a newer fencing token"
                            )
                        state.fencing_token = lease_handle.fencing_token
                    result = operation(state)
                    changed = asdict(state) != before
                    if changed:
                        state.revision += 1
                        self.save(state)
        except Timeout as exc:
            raise OSError("agenda store is busy") from exc
        if changed:
            from .change_bus import notify_agenda_changed

            notify_agenda_changed(self.root)
        return result

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
        if state.fencing_token < 0:
            raise ValueError("agenda fencing token cannot be negative")
        report_ids: set[str] = set()
        for report in state.reports:
            report.validate()
            if report.id in report_ids:
                raise ValueError(f"duplicate report outbox id: {report.id}")
            report_ids.add(report.id)
        if state.planner_health.consecutive_failures < 0:
            raise ValueError("planner failure count cannot be negative")
        event_ids: set[str] = set()
        idempotency_keys: set[tuple[str, str]] = set()
        for event in state.inbox:
            event.validate()
            if event.id in event_ids:
                raise ValueError(f"duplicate external event id: {event.id}")
            idempotency_identity = (event.source, event.idempotency_key)
            if idempotency_identity in idempotency_keys:
                raise ValueError(
                    "duplicate event idempotency key for source: "
                    f"{event.source}:{event.idempotency_key}"
                )
            event_ids.add(event.id)
            idempotency_keys.add(idempotency_identity)
        dead_letter_ids: set[str] = set()
        for item in state.dead_letters:
            item.validate()
            if item.id in dead_letter_ids:
                raise ValueError(f"duplicate dead-letter id: {item.id}")
            dead_letter_ids.add(item.id)
        if state.quota.planner_calls < 0 or any(
            value < 0 for value in state.quota.capability_calls.values()
        ):
            raise ValueError("quota usage cannot be negative")
        try:
            datetime.fromisoformat(state.quota.window_started_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("quota window timestamp is invalid") from exc

    def snapshot(self, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        state = self.load()
        active = next((e for e in state.entries if e.status == "active"), None)
        ordered = sorted(state.entries, key=lambda e: e.created_at, reverse=True)
        terminal = [
            entry for entry in state.entries if entry.status in TERMINAL_STATUSES
        ]
        terminal_counts = {
            status: sum(1 for entry in terminal if entry.status == status)
            for status in sorted(TERMINAL_STATUSES)
        }
        finished = sum(terminal_counts.values())
        completion_rate = terminal_counts["completed"] / finished if finished else None
        report_counts = {
            status: sum(1 for report in state.reports if report.status == status)
            for status in sorted(REPORT_STATUSES)
        }
        event_counts = {
            status: sum(1 for event in state.inbox if event.status == status)
            for status in sorted(EVENT_STATUSES)
        }
        return {
            "version": state.version,
            "revision": state.revision,
            "fencing_token": state.fencing_token,
            "paused": state.paused,
            "next_reconsideration_at": state.next_reconsideration_at,
            "active": asdict(active) if active else None,
            "entries": [asdict(e) for e in ordered[offset : offset + limit]],
            "total": len(ordered),
            "health": {
                "terminal": terminal_counts,
                "completion_rate": completion_rate,
                "reports": report_counts,
                "events": event_counts,
                "dead_letters": len(state.dead_letters),
                "quota": asdict(state.quota),
                "planner": asdict(state.planner_health),
            },
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
            self._queue_terminal_voice_report(state, entry)
            self._event(state, entry_id, "transition", {"status": status})
            return entry

        return self.mutate(operation)

    def transition_if_status(
        self,
        entry_id: str,
        expected_status: str,
        status: str,
        *,
        metadata_updates: Optional[dict[str, Any]] = None,
        progress_summary: Optional[str] = None,
    ) -> Optional[AgendaEntry]:
        """Atomically transition only while an entry still has the expected state.

        Terminal targets retain the reporting semantics of ``transition`` while
        allowing a stale caller to lose the race without raising.
        """
        if expected_status not in ENTRY_STATUSES or status not in ENTRY_STATUSES:
            raise ValueError("invalid agenda status")

        def operation(state: AgendaState) -> Optional[AgendaEntry]:
            entry = self._find(state, entry_id)
            if entry.status != expected_status:
                return None
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
            for key, value in (metadata_updates or {}).items():
                if value is None:
                    entry.metadata.pop(str(key), None)
                else:
                    entry.metadata[str(key)] = value
            self._queue_terminal_voice_report(state, entry)
            self._event(
                state,
                entry_id,
                "conditional_transition",
                {"expected_status": expected_status, "status": status},
            )
            return entry

        return self.mutate(operation)

    def set_checkpoint_if_status(
        self,
        entry_id: str,
        expected_status: str,
        checkpoint: Optional[ExecutionCheckpoint],
    ) -> Optional[AgendaEntry]:
        """Replace the current execution checkpoint without reviving stale work."""
        if expected_status not in ENTRY_STATUSES:
            raise ValueError("invalid agenda status")
        if checkpoint is not None:
            checkpoint.validate()

        def operation(state: AgendaState) -> Optional[AgendaEntry]:
            entry = self._find(state, entry_id)
            if entry.status != expected_status:
                return None
            entry.checkpoint = checkpoint
            self._event(
                state,
                entry_id,
                "checkpoint_updated",
                {"present": checkpoint is not None},
            )
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

    def withdraw_event(self, event_id: str) -> bool:
        """Withdraw an unlinked external event owned by a cancelled demo run.

        Removing a claimed event is safe under the store lock: a planner that
        already copied the claim can no longer acknowledge it and therefore
        cannot attach an intention after cancellation.
        """

        def operation(state: AgendaState) -> bool:
            event = self._find_event(state, event_id)
            if event.status == "acknowledged" or event.linked_entry_id:
                return False
            state.inbox = [item for item in state.inbox if item.id != event_id]
            self._event(state, None, "external_event_withdrawn", {"event_id": event_id})
            return True

        try:
            return self.mutate(operation)
        except KeyError:
            return False

    def add_from_event(
        self,
        entry: AgendaEntry,
        *,
        event_id: str,
        fencing_token: int,
    ) -> AgendaEntry:
        """Atomically accept an intention and acknowledge its grounding event."""
        entry.validate()

        def operation(state: AgendaState) -> AgendaEntry:
            event = self._find_event(state, event_id)
            self._assert_event_claim(event, fencing_token)
            if any(existing.id == entry.id for existing in state.entries):
                raise ValueError(f"agenda entry already exists: {entry.id}")
            if entry.status == "active":
                self._suspend_active(state, except_id=entry.id)
                entry.started_at = entry.started_at or _iso()
            state.entries.append(entry)
            event.status = "acknowledged"
            event.linked_entry_id = entry.id
            event.updated_at = _iso()
            event.error = None
            self._event(state, entry.id, "created")
            self._event(
                state,
                entry.id,
                "external_event_acknowledged",
                {"event_id": event.id},
            )
            self._trim_inbox(state)
            return entry

        return self.mutate(operation)

    def update_metadata(self, entry_id: str, **values: Any) -> AgendaEntry:
        """Merge bounded runtime metadata into an existing agenda entry."""

        def operation(state: AgendaState) -> AgendaEntry:
            entry = self._find(state, entry_id)
            entry.metadata.update(values)
            return entry

        return self.mutate(operation)

    def block_for_authorization(
        self,
        entry_id: str,
        *,
        transaction_id: str,
        capability: str,
        summary: str,
    ) -> AgendaEntry:
        """Persist an exact external-action approval boundary and one report."""
        cleaned_summary = " ".join(str(summary).split())[:2000]

        def operation(state: AgendaState) -> AgendaEntry:
            entry = self._find(state, entry_id)
            if entry.status in TERMINAL_STATUSES:
                return entry
            if entry.checkpoint is not None:
                entry.checkpoint.state = "planned"
            entry.status = "blocked_approval"
            entry.report_policy = "ask_first"
            entry.progress_summary = cleaned_summary[:1000]
            entry.metadata.update(
                {
                    "authorization_transaction_id": transaction_id,
                    "authorization_capability": capability,
                    "authorization_status": "pending",
                }
            )
            report_key = f"approval:{transaction_id}"
            existing = next(
                (
                    report
                    for report in state.reports
                    if report.entry_id == entry_id and report.report_key == report_key
                ),
                None,
            )
            if existing is None:
                report = ReportOutboxItem(
                    id=f"report_{uuid.uuid4().hex}",
                    entry_id=entry_id,
                    summary=cleaned_summary or "This email task needs your approval.",
                    report_key=report_key,
                )
                state.reports.append(report)
                entry.metadata["report_status"] = "pending"
                self._event(
                    state,
                    entry_id,
                    "report_queued",
                    {"report_id": report.id, "report_key": report_key},
                )
            self._event(
                state,
                entry_id,
                "authorization_required",
                {"transaction_id": transaction_id, "capability": capability},
            )
            return entry

        return self.mutate(operation)

    def resolve_authorization(
        self,
        entry_id: str,
        *,
        transaction_id: str,
        decision: str,
    ) -> AgendaEntry:
        """Resume an approved exact action or terminate a declined one."""
        if decision not in {"approved", "declined"}:
            raise ValueError("authorization decision is invalid")

        def operation(state: AgendaState) -> AgendaEntry:
            entry = self._find(state, entry_id)
            if entry.metadata.get("authorization_transaction_id") != transaction_id:
                raise ValueError("authorization transaction does not match agenda")
            if entry.status != "blocked_approval":
                return entry
            entry.metadata["authorization_status"] = decision
            entry.metadata["resumable_plan_id"] = None
            entry.metadata["resumable_plan_absorbed"] = None
            if decision == "approved":
                if entry.checkpoint is None:
                    raise ValueError("approved agenda entry has no pending checkpoint")
                entry.checkpoint.step_id = new_entry_id("step")
                entry.checkpoint.state = "planned"
                entry.status = "queued"
                entry.progress_summary = "Exact external action approved; resuming."
            else:
                entry.status = "abandoned"
                entry.ended_at = _iso()
                entry.checkpoint = None
                entry.progress_summary = (
                    "The proposed external action was declined and was not executed."
                )
                report_key = f"decision:{transaction_id}"
                if not any(
                    report.entry_id == entry_id and report.report_key == report_key
                    for report in state.reports
                ):
                    state.reports.append(
                        ReportOutboxItem(
                            id=f"report_{uuid.uuid4().hex}",
                            entry_id=entry_id,
                            summary=entry.progress_summary,
                            report_key=report_key,
                        )
                    )
            self._event(
                state,
                entry_id,
                "authorization_resolved",
                {"transaction_id": transaction_id, "decision": decision},
            )
            return entry

        return self.mutate(operation)

    def absorb_resumable_plan(
        self,
        entry_id: str,
        *,
        plan_id: str,
        status: str,
        progress_summary: str,
        report_summary: Optional[str] = None,
    ) -> AgendaEntry:
        """Atomically make a terminal sidecar result durable in the agenda.

        The plan journal and agenda are separate files, so the agenda must
        commit its status, handoff marker, and optional report in one mutation
        before the plan report can be acknowledged. Replaying the same handoff
        is idempotent; a different plan cannot overwrite an unacknowledged one.
        """

        if status not in ENTRY_STATUSES:
            raise ValueError(f"invalid agenda status: {status}")
        if not isinstance(plan_id, str) or not plan_id.strip() or len(plan_id) > 200:
            raise ValueError("resumable plan id is invalid")
        cleaned_progress = " ".join(str(progress_summary).split())[:1000]
        cleaned_report = (
            " ".join(str(report_summary).split())[:2000]
            if report_summary is not None
            else None
        )
        if report_summary is not None and not cleaned_report:
            cleaned_report = "Agenda intention completed."

        def operation(state: AgendaState) -> AgendaEntry:
            entry = self._find(state, entry_id)
            absorbed = entry.metadata.get("resumable_plan_absorbed")
            if absorbed not in {None, plan_id}:
                raise ValueError("another resumable plan handoff is still pending")
            if absorbed == plan_id:
                if entry.status != status:
                    raise ValueError(
                        "resumable plan handoff already has a different status"
                    )
                return entry
            if entry.status in TERMINAL_STATUSES and absorbed != plan_id:
                raise ValueError("terminal agenda entries cannot absorb a new plan")

            entry.metadata["resumable_plan_absorbed"] = plan_id
            entry.metadata["resumable_plan_id"] = None
            if status == "active":
                self._suspend_active(state, except_id=entry_id)
                entry.started_at = entry.started_at or _iso()
            # The checkpoint belongs to the absorbed one-step sidecar. Keeping
            # it on a queued entry can cause restart reconciliation to relink
            # the already-consumed plan instead of asking for the next step.
            entry.checkpoint = None
            if status in TERMINAL_STATUSES:
                entry.ended_at = entry.ended_at or _iso()
            entry.status = status
            entry.progress_summary = cleaned_progress
            self._event(
                state,
                entry_id,
                "resumable_plan_absorbed",
                {"plan_id": plan_id, "status": status},
            )

            self._queue_terminal_voice_report(state, entry)
            if (
                cleaned_report is not None
                and status != "abandoned"
                and not entry.metadata.get("terminal_voice_report")
            ):
                existing = next(
                    (
                        report
                        for report in state.reports
                        if report.entry_id == entry_id
                        and report.report_key == "completion"
                    ),
                    None,
                )
                if existing is None:
                    report = ReportOutboxItem(
                        id=f"report_{uuid.uuid4().hex}",
                        entry_id=entry_id,
                        summary=cleaned_report,
                    )
                    state.reports.append(report)
                    entry.metadata["report_status"] = "pending"
                    self._event(
                        state,
                        entry_id,
                        "report_queued",
                        {"report_id": report.id},
                    )
            return entry

        return self.mutate(operation)

    def clear_absorbed_resumable_plan(
        self, entry_id: str, *, plan_id: str
    ) -> AgendaEntry:
        """Clear exactly the acknowledged handoff without clobbering a newer one."""

        def operation(state: AgendaState) -> AgendaEntry:
            entry = self._find(state, entry_id)
            if entry.metadata.get("resumable_plan_absorbed") == plan_id:
                entry.metadata["resumable_plan_absorbed"] = None
            return entry

        return self.mutate(operation)

    def enqueue_report(
        self, entry_id: str, summary: str, *, report_key: str = "completion"
    ) -> ReportOutboxItem:
        """Persist one idempotent completion report for an agenda entry."""
        cleaned = " ".join(str(summary).split())[:2000]
        if not cleaned:
            cleaned = "Agenda intention completed."
        cleaned_key = str(report_key).strip()[:100]
        if not cleaned_key:
            raise ValueError("report key is required")

        def operation(state: AgendaState) -> ReportOutboxItem:
            self._find(state, entry_id)
            existing = next(
                (
                    report
                    for report in state.reports
                    if report.entry_id == entry_id and report.report_key == cleaned_key
                ),
                None,
            )
            if existing is not None:
                return existing
            report = ReportOutboxItem(
                id=f"report_{uuid.uuid4().hex}",
                entry_id=entry_id,
                summary=cleaned,
                report_key=cleaned_key,
            )
            state.reports.append(report)
            entry = self._find(state, entry_id)
            entry.metadata["report_status"] = "pending"
            self._event(state, entry_id, "report_queued", {"report_id": report.id})
            return report

        return self.mutate(operation)

    def claim_due_report(
        self,
        now: Optional[datetime] = None,
        *,
        silent_only: bool = False,
    ) -> Optional[ReportOutboxItem]:
        """Atomically claim the oldest due report for one delivery attempt."""
        now = (now or utc_now()).astimezone(timezone.utc)

        def operation(state: AgendaState) -> Optional[ReportOutboxItem]:
            due = []
            for report in state.reports:
                if report.status != "pending":
                    continue
                if silent_only:
                    entry = self._find(state, report.entry_id)
                    if entry.metadata.get("terminal_voice_report") or (
                        entry.report_policy != "silent"
                    ):
                        continue
                candidate = datetime.fromisoformat(report.next_attempt_at)
                if candidate.tzinfo is None:
                    candidate = candidate.replace(tzinfo=timezone.utc)
                if candidate <= now:
                    due.append(report)
            if not due:
                return None
            report = min(due, key=lambda item: (item.next_attempt_at, item.created_at))
            report.status = "delivering"
            report.attempts += 1
            report.updated_at = _iso(now)
            report.error = None
            self._event(
                state,
                report.entry_id,
                "report_delivery_started",
                {"report_id": report.id, "attempt": report.attempts},
            )
            return ReportOutboxItem(**asdict(report))

        return self.mutate(operation)

    def expedite_pending_reports(self) -> int:
        """Make deferred reports eligible after a meaningful presence change."""
        now_value = utc_now()
        now = _iso(now_value)

        def operation(state: AgendaState) -> int:
            changed = 0
            for report in state.reports:
                if report.status != "pending":
                    continue
                due = datetime.fromisoformat(report.next_attempt_at)
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                if due <= now_value:
                    continue
                report.next_attempt_at = now
                report.updated_at = now
                changed += 1
            return changed

        return self.mutate(operation)

    def finish_report(
        self,
        report_id: str,
        *,
        delivered: bool,
        error: Optional[str] = None,
        retry_seconds: float = 60,
        max_attempts: int = 5,
        counts_as_failure: bool = True,
        now: Optional[datetime] = None,
    ) -> ReportOutboxItem:
        """Complete or reschedule a claimed report without duplicating it."""
        now = (now or utc_now()).astimezone(timezone.utc)

        def operation(state: AgendaState) -> ReportOutboxItem:
            report = next(
                (item for item in state.reports if item.id == report_id),
                None,
            )
            if report is None:
                raise KeyError(report_id)
            if report.status != "delivering":
                raise ValueError("only a delivering report may be finished")
            report.updated_at = _iso(now)
            report.error = str(error)[:500] if error else None
            entry = self._find(state, report.entry_id)
            if delivered:
                report.status = "delivered"
                entry.metadata["report_status"] = "reported"
                event = "report_delivered"
            else:
                if counts_as_failure:
                    report.failures += 1
            if not delivered and report.failures >= max(1, int(max_attempts)):
                report.status = "failed"
                entry.metadata["report_status"] = "failed"
                self._append_dead_letter(
                    state,
                    category="completion_report",
                    reference_id=report.id,
                    reason=report.error or "completion report delivery failed",
                    attempts=max(1, report.failures),
                    data={"entry_id": report.entry_id},
                )
                event = "report_failed"
            elif not delivered:
                report.status = "pending"
                report.next_attempt_at = _iso(
                    now + timedelta(seconds=max(1.0, float(retry_seconds)))
                )
                entry.metadata["report_status"] = "deferred"
                event = "report_deferred"
            self._event(
                state,
                report.entry_id,
                event,
                {"report_id": report.id, "attempt": report.attempts},
            )
            return report

        return self.mutate(operation)

    def record_execution_failure(
        self,
        entry_id: str,
        *,
        error: str,
        max_attempts: int,
    ) -> AgendaEntry:
        """Bound repeated activity failures and dead-letter the terminal entry."""

        def operation(state: AgendaState) -> AgendaEntry:
            entry = self._find(state, entry_id)
            failures = int(entry.metadata.get("execution_failures", 0)) + 1
            entry.metadata["execution_failures"] = failures
            entry.metadata["last_execution_error"] = str(error)[:500]
            if failures >= max(1, int(max_attempts)):
                entry.status = "failed"
                entry.ended_at = _iso()
                entry.checkpoint = None
                entry.progress_summary = (
                    f"Execution failed repeatedly: {str(error)[:450]}"
                )
                self._append_dead_letter(
                    state,
                    category="activity_step",
                    reference_id=entry.id,
                    reason=str(error)[:500] or "activity execution failed",
                    attempts=failures,
                    data={"kind": entry.kind},
                )
                self._event(
                    state,
                    entry.id,
                    "execution_dead_lettered",
                    {"attempts": failures},
                )
                self._queue_terminal_voice_report(state, entry)
            else:
                if entry.status == "active":
                    entry.status = "suspended"
                self._event(
                    state,
                    entry.id,
                    "execution_failed",
                    {"attempts": failures},
                )
            return entry

        return self.mutate(operation)

    def record_planner_failure(
        self,
        error: str,
        *,
        circuit_open_until: Optional[str],
    ) -> PlannerHealth:
        def operation(state: AgendaState) -> PlannerHealth:
            health = state.planner_health
            health.consecutive_failures += 1
            health.last_error = str(error)[:500]
            health.last_failure_at = _iso()
            health.circuit_open_until = circuit_open_until
            return health

        return self.mutate(operation)

    def record_planner_success(self) -> PlannerHealth:
        def operation(state: AgendaState) -> PlannerHealth:
            health = state.planner_health
            health.consecutive_failures = 0
            health.circuit_open_until = None
            health.last_error = None
            health.last_success_at = _iso()
            state.next_reconsideration_at = None
            return health

        return self.mutate(operation)

    def set_reconsideration(self, next_at: Optional[str]) -> None:
        def operation(state: AgendaState) -> None:
            state.next_reconsideration_at = next_at

        self.mutate(operation)

    def publish_event(
        self,
        *,
        idempotency_key: str,
        source: str,
        kind: str,
        summary: str,
        evidence_ref: str,
        payload: Optional[dict[str, Any]] = None,
        backlog_limit: int = 500,
    ) -> ExternalEvent:
        """Publish one external event, returning the prior copy on replay."""
        candidate = ExternalEvent(
            id=f"event_{uuid.uuid4().hex}",
            idempotency_key=str(idempotency_key).strip(),
            source=str(source).strip(),
            kind=str(kind).strip(),
            summary=" ".join(str(summary).split())[:500],
            evidence_ref=str(evidence_ref).strip(),
            payload=dict(payload or {}),
        )
        candidate.validate()

        def operation(state: AgendaState) -> ExternalEvent:
            existing = next(
                (
                    event
                    for event in state.inbox
                    if event.source == candidate.source
                    and event.idempotency_key == candidate.idempotency_key
                ),
                None,
            )
            if existing is not None:
                return existing
            backlog = sum(
                event.status in {"pending", "claimed"} for event in state.inbox
            )
            if backlog >= max(1, int(backlog_limit)):
                raise ValueError("external event inbox backlog is full")
            state.inbox.append(candidate)
            self._event(
                state,
                None,
                "external_event_published",
                {
                    "event_id": candidate.id,
                    "source": candidate.source,
                    "kind": candidate.kind,
                },
            )
            self._trim_inbox(state)
            return candidate

        return self.mutate(operation)

    def claim_next_event(
        self,
        *,
        owner_id: str,
        fencing_token: int,
        now: Optional[datetime] = None,
        claim_timeout_seconds: float = 300,
        priority: Optional[Callable[[ExternalEvent], tuple]] = None,
    ) -> Optional[ExternalEvent]:
        """Claim one due event under the active fenced workspace lease."""
        from .lease import LeaseLostError, current_lease

        handle = current_lease()
        if (
            handle is None
            or handle.owner_id != owner_id
            or handle.fencing_token != fencing_token
            or handle.workspace_root.resolve() != self.root.resolve()
        ):
            raise LeaseLostError("event claims require the current workspace lease")
        now = (now or utc_now()).astimezone(timezone.utc)
        claim_cutoff = now - timedelta(seconds=max(1.0, claim_timeout_seconds))

        def operation(state: AgendaState) -> Optional[ExternalEvent]:
            for event in state.inbox:
                if event.status != "claimed":
                    continue
                claimed_at = (
                    datetime.fromisoformat(event.claimed_at)
                    if event.claimed_at
                    else claim_cutoff
                )
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=timezone.utc)
                if (
                    event.claimed_fencing_token is not None
                    and event.claimed_fencing_token < fencing_token
                ) or claimed_at <= claim_cutoff:
                    event.status = "pending"
                    event.claimed_at = None
                    event.claimed_by = None
                    event.claimed_fencing_token = None
                    event.updated_at = _iso(now)

            due = []
            for event in state.inbox:
                if event.status != "pending":
                    continue
                available = datetime.fromisoformat(event.available_at)
                if available.tzinfo is None:
                    available = available.replace(tzinfo=timezone.utc)
                if available <= now:
                    due.append(event)
            if not due:
                return None
            event = min(
                due,
                key=(
                    priority
                    if priority is not None
                    else lambda item: (item.available_at, item.created_at)
                ),
            )
            event.status = "claimed"
            event.attempts += 1
            event.claimed_at = _iso(now)
            event.claimed_by = owner_id
            event.claimed_fencing_token = fencing_token
            event.updated_at = _iso(now)
            event.error = None
            self._event(
                state,
                None,
                "external_event_claimed",
                {
                    "event_id": event.id,
                    "attempt": event.attempts,
                    "fencing_token": fencing_token,
                },
            )
            return ExternalEvent(**asdict(event))

        return self.mutate(operation)

    def acknowledge_event(
        self,
        event_id: str,
        *,
        fencing_token: int,
        linked_entry_id: Optional[str] = None,
    ) -> ExternalEvent:
        def operation(state: AgendaState) -> ExternalEvent:
            event = self._find_event(state, event_id)
            self._assert_event_claim(event, fencing_token)
            event.status = "acknowledged"
            event.linked_entry_id = linked_entry_id
            event.updated_at = _iso()
            event.error = None
            self._event(
                state,
                linked_entry_id,
                "external_event_acknowledged",
                {"event_id": event.id},
            )
            self._trim_inbox(state)
            return event

        return self.mutate(operation)

    def fail_event(
        self,
        event_id: str,
        *,
        fencing_token: int,
        error: str,
        retry_seconds: float,
        max_attempts: int,
    ) -> ExternalEvent:
        def operation(state: AgendaState) -> ExternalEvent:
            event = self._find_event(state, event_id)
            self._assert_event_claim(event, fencing_token)
            event.error = str(error)[:500]
            event.updated_at = _iso()
            event.claimed_at = None
            event.claimed_by = None
            event.claimed_fencing_token = None
            if event.attempts >= max(1, int(max_attempts)):
                event.status = "dead_letter"
                self._append_dead_letter(
                    state,
                    category="external_event",
                    reference_id=event.id,
                    reason=event.error or "external event failed",
                    attempts=event.attempts,
                    data={"source": event.source, "kind": event.kind},
                )
                event_type = "external_event_dead_lettered"
            else:
                event.status = "pending"
                event.available_at = _iso(
                    utc_now() + timedelta(seconds=max(1.0, retry_seconds))
                )
                event_type = "external_event_deferred"
            self._event(
                state,
                None,
                event_type,
                {"event_id": event.id, "attempt": event.attempts},
            )
            self._trim_inbox(state)
            return event

        return self.mutate(operation)

    def defer_event(
        self,
        event_id: str,
        *,
        fencing_token: int,
        available_at: str,
        reason: str,
    ) -> ExternalEvent:
        """Release a claim for operational backpressure without dead-lettering."""

        def operation(state: AgendaState) -> ExternalEvent:
            event = self._find_event(state, event_id)
            self._assert_event_claim(event, fencing_token)
            datetime.fromisoformat(available_at)
            event.status = "pending"
            event.available_at = available_at
            event.updated_at = _iso()
            event.error = str(reason)[:500]
            event.claimed_at = None
            event.claimed_by = None
            event.claimed_fencing_token = None
            self._event(
                state,
                None,
                "external_event_deferred",
                {"event_id": event.id, "reason": "operational_backpressure"},
            )
            return event

        return self.mutate(operation)

    def release_claimed_events(
        self,
        *,
        owner_id: str,
        fencing_token: int,
        reason: str,
        now: Optional[datetime] = None,
    ) -> int:
        """Release this owner's interrupted claims for immediate safe retry."""
        from .lease import LeaseLostError, current_lease

        handle = current_lease()
        if (
            handle is None
            or handle.owner_id != owner_id
            or handle.fencing_token != fencing_token
            or handle.workspace_root.resolve() != self.root.resolve()
        ):
            raise LeaseLostError("event release requires the current workspace lease")
        now = (now or utc_now()).astimezone(timezone.utc)

        def operation(state: AgendaState) -> int:
            released = 0
            for event in state.inbox:
                if (
                    event.status != "claimed"
                    or event.claimed_by != owner_id
                    or event.claimed_fencing_token != fencing_token
                ):
                    continue
                event.status = "pending"
                event.available_at = _iso(now)
                event.updated_at = _iso(now)
                event.error = str(reason)[:500]
                event.claimed_at = None
                event.claimed_by = None
                event.claimed_fencing_token = None
                self._event(
                    state,
                    None,
                    "external_event_deferred",
                    {
                        "event_id": event.id,
                        "reason": "owner_interrupted",
                    },
                )
                released += 1
            return released

        return self.mutate(operation)

    def retry_dead_event(self, event_id: str) -> ExternalEvent:
        """Explicitly requeue a dead-lettered external event."""

        def operation(state: AgendaState) -> ExternalEvent:
            event = self._find_event(state, event_id)
            if event.status != "dead_letter":
                raise ValueError("only dead-lettered events may be retried")
            event.status = "pending"
            event.attempts = 0
            event.available_at = _iso()
            event.updated_at = _iso()
            event.error = None
            event.claimed_at = None
            event.claimed_by = None
            event.claimed_fencing_token = None
            self._event(
                state,
                None,
                "external_event_requeued",
                {"event_id": event.id},
            )
            return event

        return self.mutate(operation)

    def add_dead_letter(
        self,
        *,
        category: str,
        reference_id: str,
        reason: str,
        attempts: int,
        data: Optional[dict[str, Any]] = None,
    ) -> DeadLetter:
        def operation(state: AgendaState) -> DeadLetter:
            return self._append_dead_letter(
                state,
                category=category,
                reference_id=reference_id,
                reason=reason,
                attempts=attempts,
                data=data or {},
            )

        return self.mutate(operation)

    def consume_quota(
        self,
        *,
        category: str,
        key: str = "",
        limit: int,
        window_seconds: int = 3600,
        now: Optional[datetime] = None,
    ) -> QuotaDecision:
        """Atomically consume one planner or per-capability quota unit."""
        if category not in {"planner", "capability"}:
            raise ValueError("quota category must be planner or capability")
        if category == "capability" and not key:
            raise ValueError("capability quota requires a key")
        now = (now or utc_now()).astimezone(timezone.utc)
        limit = max(1, int(limit))
        window_seconds = max(1, int(window_seconds))

        def operation(state: AgendaState) -> QuotaDecision:
            started = datetime.fromisoformat(state.quota.window_started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            reset = started + timedelta(seconds=window_seconds)
            if now >= reset:
                state.quota = QuotaWindow(window_started_at=_iso(now))
                started = now
                reset = now + timedelta(seconds=window_seconds)
            if category == "planner":
                current = state.quota.planner_calls
                if current >= limit:
                    return QuotaDecision(False, 0, _iso(reset))
                state.quota.planner_calls += 1
                remaining = limit - state.quota.planner_calls
            else:
                current = int(state.quota.capability_calls.get(key, 0))
                if current >= limit:
                    return QuotaDecision(False, 0, _iso(reset))
                state.quota.capability_calls[key] = current + 1
                remaining = limit - current - 1
            return QuotaDecision(True, remaining, _iso(reset))

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
                occurrence.metadata["queue_priority"] = template.schedule.priority
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
                entry.progress_summary = (
                    "Interrupted by restart; requires reconsideration."
                )
                recovered.append(entry.id)
                self._event(state, entry.id, "restart_recovery")
            for report in state.reports:
                if report.status != "delivering":
                    continue
                report.status = "pending"
                report.next_attempt_at = _iso()
                report.updated_at = _iso()
                report.error = "delivery interrupted by server restart"
                self._event(
                    state,
                    report.entry_id,
                    "report_recovered",
                    {"report_id": report.id},
                )
            return recovered

        return self.mutate(operation)

    def prune_terminal_history(
        self,
        *,
        retention_hours: int = 24,
        retention_entries: int = 30,
    ) -> int:
        """Prune old terminal entries without routing routine work into memory."""
        state = self.load()
        terminal = sorted(
            (
                entry
                for entry in state.entries
                if entry.status in TERMINAL_STATUSES
                and not any(
                    report.entry_id == entry.id
                    and report.status in {"pending", "delivering"}
                    for report in state.reports
                )
            ),
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
        expired_ids = {entry.id for entry in expired}

        def operation(current: AgendaState) -> int:
            current.entries = [
                entry for entry in current.entries if entry.id not in expired_ids
            ]
            current.events = [
                event for event in current.events if event.entry_id not in expired_ids
            ]
            current.reports = [
                report
                for report in current.reports
                if report.entry_id not in expired_ids
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
    def _find_event(state: AgendaState, event_id: str) -> ExternalEvent:
        for event in state.inbox:
            if event.id == event_id:
                return event
        raise KeyError(event_id)

    @staticmethod
    def _assert_event_claim(event: ExternalEvent, fencing_token: int) -> None:
        if event.status != "claimed" or event.claimed_fencing_token != int(
            fencing_token
        ):
            raise ValueError("external event is not claimed by this fence")

    @staticmethod
    def _append_dead_letter(
        state: AgendaState,
        *,
        category: str,
        reference_id: str,
        reason: str,
        attempts: int,
        data: dict[str, Any],
    ) -> DeadLetter:
        existing = next(
            (
                item
                for item in state.dead_letters
                if item.category == category and item.reference_id == reference_id
            ),
            None,
        )
        if existing is not None:
            existing.reason = str(reason)[:500]
            existing.attempts = max(existing.attempts, int(attempts))
            existing.last_failed_at = _iso()
            return existing
        item = DeadLetter(
            id=f"dead_{uuid.uuid4().hex}",
            category=str(category)[:100],
            reference_id=str(reference_id)[:200],
            reason=str(reason)[:500] or "autonomous work failed",
            attempts=max(1, int(attempts)),
            data=dict(data),
        )
        item.validate()
        state.dead_letters.append(item)
        state.dead_letters = state.dead_letters[-500:]
        return item

    @staticmethod
    def _trim_inbox(state: AgendaState, retention: int = 1000) -> None:
        excess = len(state.inbox) - max(1, retention)
        if excess <= 0:
            return
        removable = {
            event.id
            for event in sorted(state.inbox, key=lambda item: item.updated_at)
            if event.status in {"acknowledged", "dead_letter"}
        }
        retained = []
        removed = 0
        for event in state.inbox:
            if removed < excess and event.id in removable:
                removed += 1
                continue
            retained.append(event)
        state.inbox = retained

    @staticmethod
    def _suspend_active(state: AgendaState, except_id: str) -> None:
        for entry in state.entries:
            if entry.id != except_id and entry.status == "active":
                entry.status = "suspended"
                entry.progress_summary = (
                    entry.progress_summary or "Preempted by foreground work."
                )

    @classmethod
    def _queue_terminal_voice_report(
        cls, state: AgendaState, entry: AgendaEntry
    ) -> None:
        """Atomically queue one spoken result for a finished business intention."""
        if entry.kind not in {"pursuit", "scheduled"} or entry.status not in {
            "completed",
            "failed",
        }:
            return
        entry.metadata["terminal_voice_report"] = True
        existing = next(
            (
                report
                for report in state.reports
                if report.entry_id == entry.id and report.report_key == "completion"
            ),
            None,
        )
        if existing is not None:
            return
        fallback = (
            "The autonomous task finished."
            if entry.status == "completed"
            else "The autonomous task could not be completed."
        )
        report = ReportOutboxItem(
            id=f"report_{uuid.uuid4().hex}",
            entry_id=entry.id,
            summary=(entry.progress_summary or fallback)[:2000],
        )
        state.reports.append(report)
        entry.metadata["report_status"] = "pending"
        cls._event(
            state,
            entry.id,
            "report_queued",
            {"report_id": report.id, "report_key": "completion"},
        )

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
    motivation_type: str | None = None,
    **kwargs: Any,
) -> AgendaEntry:
    return AgendaEntry(
        id=new_entry_id(kind),
        kind=kind,
        title=title,
        description=description,
        origin=origin,
        motivation=Motivation(
            type=motivation_type or origin,
            evidence_refs=evidence_refs,
        ),
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
