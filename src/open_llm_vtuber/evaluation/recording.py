"""Durable recording and safe projection into evaluation evidence envelopes."""

from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from filelock import FileLock

from .evidence import (
    EvidenceValidationError,
    build_event_envelope,
    canonical_json,
    finish_run_manifest,
    validate_event_chain,
    validate_run_manifest,
)

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_TOKEN_CLEANER = re.compile(r"[^a-z0-9._:-]+")
_MAX_TOKEN_CHARS = 120
_MAX_EVENTS_DEFAULT = 100_000

_STUDY_FIELDS: dict[str, frozenset[str]] = {
    "session_start": frozenset(
        {
            "condition",
            "pillar_embodiment",
            "pillar_memory",
            "pillar_autonomy",
        }
    ),
    "session_end": frozenset({"duration_seconds"}),
    "client_connected": frozenset(),
    "client_disconnected": frozenset(),
    "turn_started": frozenset({"source", "seconds_since_agent_finished"}),
    "turn_finished": frozenset({"duration_seconds", "reply_chars"}),
    "object_click": frozenset({"object_id"}),
    "memory_recall": frozenset(
        {"enabled", "query_chars", "documents", "top_score", "top_source"}
    ),
    "tool_call": frozenset({"tool", "requires", "allowed"}),
    "autonomy_action": frozenset({"action", "direction", "status", "attention_cost"}),
    "autonomy_feedback": frozenset({"receipt_id", "feedback"}),
}
_STUDY_CATEGORY = {
    "session_start": "system",
    "session_end": "system",
    "client_connected": "system",
    "client_disconnected": "system",
    "turn_started": "observation",
    "turn_finished": "outcome",
    "object_click": "observation",
    "memory_recall": "belief",
    "tool_call": "authorization",
    "autonomy_action": "action",
    "autonomy_feedback": "outcome",
}
_AUTONOMY_FIELDS = frozenset(
    {
        "status",
        "attempt",
        "attempts",
        "report_id",
        "event_id",
        "source",
        "kind",
        "fencing_token",
        "delivered",
        "retry_in_seconds",
        "receipt_id",
        "feedback",
    }
)


@dataclass(frozen=True)
class EventProjection:
    """A content-free source event ready for the durable recorder."""

    category: str
    event_type: str
    correlation_id: str
    payload: dict[str, Any]
    timestamp: str | datetime | None = None
    causation_id: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token(value: Any, fallback: str = "unknown") -> str:
    """Turn an allowlisted semantic identifier into an envelope-safe token."""

    if value is None:
        return fallback
    text = str(value).strip()
    if _TOKEN.fullmatch(text):
        return text
    cleaned = _TOKEN_CLEANER.sub("-", text.casefold()).strip("-._:")
    return cleaned[:_MAX_TOKEN_CHARS] or fallback


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(-(2**63), min(2**63 - 1, value))
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return _token(value)


def project_study_event(record: dict[str, Any]) -> EventProjection:
    """Project one legacy study record without copying arbitrary fields."""

    if not isinstance(record, dict):
        raise EvidenceValidationError("study event must be an object")
    event_type = _token(record.get("event"), "unknown_study_event")
    allowed = _STUDY_FIELDS.get(event_type, frozenset())
    payload = {
        key: _safe_scalar(record[key]) for key in sorted(allowed) if key in record
    }
    condition = record.get("condition")
    if condition is not None and "condition" not in payload:
        payload["condition"] = _token(condition)
    return EventProjection(
        category=_STUDY_CATEGORY.get(event_type, "system"),
        event_type=f"study_{event_type}",
        correlation_id=_token(record.get("session"), "study-session"),
        payload=payload,
        timestamp=record.get("ts"),
    )


def project_autonomy_event(
    *,
    entry_id: str | None,
    kind: str,
    data: dict[str, Any] | None = None,
    trace_id: str | None = None,
    timestamp: str | datetime | None = None,
) -> EventProjection:
    """Project an agenda event while excluding titles, summaries, and errors."""

    event_type = _token(kind, "unknown_autonomy_event")
    raw = data if isinstance(data, dict) else {}
    payload = {
        key: _safe_scalar(raw[key]) for key in sorted(_AUTONOMY_FIELDS) if key in raw
    }
    if entry_id:
        payload["entry_id"] = _token(entry_id)
    if event_type.startswith("external_event"):
        category = "observation"
    elif event_type in {"created", "transition", "schedule_due"}:
        category = "plan"
    elif "report" in event_type or event_type in {"completed", "failed"}:
        category = "outcome"
    elif "recover" in event_type or "retry" in event_type:
        category = "recovery"
    else:
        category = "system"
    return EventProjection(
        category=category,
        event_type=f"autonomy_{event_type}",
        correlation_id=_token(trace_id or entry_id, "autonomy-system"),
        payload=payload,
        timestamp=timestamp,
    )


class EvidenceRecorder:
    """Append a validated hash chain and atomically finalize its manifest."""

    def __init__(
        self,
        root: Path,
        manifest: dict[str, Any],
        *,
        clock: Callable[[], datetime] = _now,
        id_factory: Callable[[], str] | None = None,
        max_events: int = _MAX_EVENTS_DEFAULT,
    ) -> None:
        validate_run_manifest(manifest)
        if manifest["status"] != "running":
            raise EvidenceValidationError("recorder requires a running manifest")
        self.root = Path(root).resolve()
        self.manifest_path = self.root / "manifest.json"
        self.events_path = self.root / "events.jsonl"
        self.lock_path = self.root / "recording.lock"
        self._manifest = json.loads(canonical_json(manifest))
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._max_events = max(1, min(int(max_events), 1_000_000))
        self._sequence = 0
        self._previous_hash: str | None = None
        self._thread_lock = threading.RLock()
        self._file_lock = FileLock(str(self.lock_path), timeout=5)
        self._closed = False

        self.root.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists() or self.events_path.exists():
            raise EvidenceValidationError(
                f"evidence run already exists and will not be overwritten: {self.root}"
            )
        self._atomic_manifest_write(self._manifest)
        self.events_path.touch(exist_ok=False)

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(canonical_json(self._manifest))

    def _atomic_manifest_write(self, manifest: dict[str, Any]) -> None:
        temporary = self.manifest_path.with_suffix(".json.tmp")
        payload = f"{canonical_json(manifest)}\n"
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.manifest_path)

    def append(
        self,
        *,
        category: str,
        event_type: str,
        correlation_id: str,
        payload: dict[str, Any],
        timestamp: str | datetime | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        with self._thread_lock, self._file_lock:
            if self._closed:
                raise EvidenceValidationError("cannot append to a finalized run")
            if self._sequence >= self._max_events:
                raise EvidenceValidationError("event budget exhausted")
            event = build_event_envelope(
                event_id=_token(self._id_factory(), "event"),
                run_id=self._manifest["run_id"],
                sequence=self._sequence,
                timestamp=timestamp or self._clock(),
                category=category,
                event_type=event_type,
                correlation_id=correlation_id,
                causation_id=causation_id,
                payload=payload,
                previous_event_hash=self._previous_hash,
            )
            line = f"{canonical_json(event)}\n"
            with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            self._sequence += 1
            self._previous_hash = event["event_hash"]
            return event

    def append_projection(self, projection: EventProjection) -> dict[str, Any]:
        return self.append(
            category=projection.category,
            event_type=projection.event_type,
            correlation_id=projection.correlation_id,
            payload=projection.payload,
            timestamp=projection.timestamp,
            causation_id=projection.causation_id,
        )

    def finish(
        self,
        *,
        status: str,
        finished_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        with self._thread_lock, self._file_lock:
            if self._closed:
                raise EvidenceValidationError("run is already finalized")
            terminal = finish_run_manifest(
                self._manifest,
                status=status,
                finished_at=finished_at or self._clock(),
            )
            self._atomic_manifest_write(terminal)
            self._manifest = terminal
            self._closed = True
            return self.manifest


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvidenceValidationError(f"cannot read evidence events: {exc}") from exc
    for index, line in enumerate(lines):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceValidationError(
                f"events[{index}]: invalid JSON; truncated evidence is not accepted"
            ) from exc
        if not isinstance(event, dict):
            raise EvidenceValidationError(f"events[{index}]: must be an object")
        events.append(event)
    return events


def load_evidence_bundle(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load a manifest and event chain, rejecting partial or tampered runs."""

    root = Path(root).resolve()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceValidationError(f"cannot read evidence manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise EvidenceValidationError("evidence manifest must be an object")
    events = _read_events(root / "events.jsonl")
    validate_event_chain(events, manifest=manifest)
    return manifest, events


def project_study_events(records: Iterable[dict[str, Any]]) -> list[EventProjection]:
    return [project_study_event(record) for record in records]
