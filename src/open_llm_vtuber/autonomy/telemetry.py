"""Content-free autonomy traces and long-horizon chain quality reports."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterator

from filelock import FileLock, Timeout

from .agenda import AgendaState, AgendaStore, TERMINAL_STATUSES, utc_now


def new_trace_id() -> str:
    return uuid.uuid4().hex


def _safe_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for raw_key, raw_value in (attributes or {}).items():
        key = str(raw_key).strip()[:100]
        if not key:
            continue
        if raw_value is None or isinstance(raw_value, (bool, int, float)):
            safe[key] = raw_value
        else:
            safe[key] = str(raw_value)[:500]
    return safe


class _Span:
    def __init__(
        self,
        tracer: "AutonomyTracer",
        name: str,
        *,
        trace_id: str | None,
        parent_span_id: str | None,
        attributes: dict[str, Any] | None,
    ) -> None:
        self.tracer = tracer
        self.name = str(name)[:200]
        self.trace_id = trace_id or new_trace_id()
        self.span_id = uuid.uuid4().hex[:16]
        self.parent_span_id = parent_span_id
        self.attributes = _safe_attributes(attributes)
        self.started = time.time_ns()
        self.status = "OK"
        self.status_message = ""

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes.update(_safe_attributes({key: value}))

    def fail(self, error: BaseException | str) -> None:
        self.status = "ERROR"
        self.status_message = (
            f"{type(error).__name__}: {error}"
            if isinstance(error, BaseException)
            else str(error)
        )[:500]

    def finish(self) -> None:
        self.tracer._append(
            {
                "traceId": self.trace_id,
                "spanId": self.span_id,
                "parentSpanId": self.parent_span_id or "",
                "name": self.name,
                "kind": "SPAN_KIND_INTERNAL",
                "startTimeUnixNano": str(self.started),
                "endTimeUnixNano": str(time.time_ns()),
                "attributes": self.attributes,
                "status": {
                    "code": (
                        "STATUS_CODE_ERROR"
                        if self.status == "ERROR"
                        else "STATUS_CODE_OK"
                    ),
                    "message": self.status_message,
                },
                "scope": {
                    "name": "open_llm_vtuber.autonomy",
                    "version": "3",
                },
                "resource": {
                    "service.name": "open-llm-vtuber",
                    "service.namespace": "embodied-agent",
                },
            }
        )


class AutonomyTracer:
    """Append OTLP-JSON-compatible span records without prompts or results."""

    def __init__(self, workspace_root: Path, *, max_bytes: int = 10_000_000) -> None:
        self.root = workspace_root.resolve()
        self.path = self.root / "agenda" / "traces.jsonl"
        self.rotated_path = self.root / "agenda" / "traces.1.jsonl"
        self.lock = FileLock(str(self.root / "agenda" / "traces.lock"), timeout=5)
        self.max_bytes = max(100_000, int(max_bytes))

    @contextmanager
    def span(
        self,
        name: str,
        *,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[_Span]:
        span = _Span(
            self,
            name,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            attributes=attributes,
        )
        try:
            yield span
        except BaseException as exc:
            span.fail(exc)
            raise
        finally:
            span.finish()

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self.lock:
                current_size = self.path.stat().st_size if self.path.exists() else 0
                if current_size + len(line.encode("utf-8")) > self.max_bytes:
                    self.rotated_path.unlink(missing_ok=True)
                    if self.path.exists():
                        self.path.replace(self.rotated_path)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line)
        except Timeout:
            # Telemetry cannot block or fail autonomous work.
            return
        except OSError:
            return


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def build_chain_report(
    store: AgendaStore,
    *,
    horizon_hours: int = 168,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize durable behavior without copying agenda content."""
    state = store.load()
    return build_chain_report_from_state(
        state,
        horizon_hours=horizon_hours,
        now=now,
    )


def build_chain_report_from_state(
    state: AgendaState,
    *,
    horizon_hours: int = 168,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or utc_now()).astimezone(timezone.utc)
    horizon_hours = max(1, min(8760, int(horizon_hours)))
    cutoff = now - timedelta(hours=horizon_hours)
    entries = [
        entry
        for entry in state.entries
        if (_parse_time(entry.created_at) or now) >= cutoff
    ]
    entries.sort(key=lambda entry: entry.created_at)
    terminal = [entry for entry in entries if entry.status in TERMINAL_STATUSES]
    completed = [entry for entry in terminal if entry.status == "completed"]
    autonomous = [entry for entry in entries if entry.kind != "user"]
    grounded = [entry for entry in autonomous if bool(entry.motivation.evidence_refs)]
    idle = [entry for entry in autonomous if entry.kind == "idle"]

    duplicate_pairs = 0
    comparable_pairs = 0
    previous = None
    for entry in autonomous:
        current = _normalized(f"{entry.title} {entry.description}")
        if previous and current:
            comparable_pairs += 1
            duplicate_pairs += int(
                SequenceMatcher(None, previous, current).ratio() >= 0.82
            )
        previous = current

    chain_lengths: list[int] = []
    current_chain = 0
    for entry in entries:
        if entry.kind == "user":
            if current_chain:
                chain_lengths.append(current_chain)
            current_chain = 0
        elif entry.status in TERMINAL_STATUSES:
            current_chain += 1
    if current_chain:
        chain_lengths.append(current_chain)

    durations = []
    for entry in terminal:
        started = _parse_time(entry.started_at or entry.created_at)
        ended = _parse_time(entry.ended_at)
        if started and ended and ended >= started:
            durations.append((ended - started).total_seconds())

    event_counts = Counter(event.status for event in state.inbox)
    report_counts = Counter(report.status for report in state.reports)
    dead_letter_counts = Counter(item.category for item in state.dead_letters)
    return {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "horizon_hours": horizon_hours,
        "fencing_token": state.fencing_token,
        "entries": {
            "total": len(entries),
            "terminal": len(terminal),
            "completed": len(completed),
            "completion_rate": (len(completed) / len(terminal) if terminal else None),
            "grounded_rate": (len(grounded) / len(autonomous) if autonomous else None),
            "idle_ratio": len(idle) / len(autonomous) if autonomous else None,
            "repetition_rate": (
                duplicate_pairs / comparable_pairs if comparable_pairs else None
            ),
            "average_chain_length": (
                sum(chain_lengths) / len(chain_lengths) if chain_lengths else 0.0
            ),
            "average_duration_seconds": (
                sum(durations) / len(durations) if durations else None
            ),
            "distinct_capabilities": sorted(
                {
                    capability
                    for entry in autonomous
                    for capability in entry.required_capabilities
                }
            ),
            "distinct_objects": sorted(
                {
                    entry.preferred_object
                    for entry in autonomous
                    if entry.preferred_object
                }
            ),
        },
        "events": dict(sorted(event_counts.items())),
        "reports": dict(sorted(report_counts.items())),
        "dead_letters": {
            "total": len(state.dead_letters),
            "by_category": dict(sorted(dead_letter_counts.items())),
        },
        "quota": asdict(state.quota),
        "planner": asdict(state.planner_health),
    }
