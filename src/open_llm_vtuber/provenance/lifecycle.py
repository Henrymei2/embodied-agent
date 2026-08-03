"""What the user can do to the agent's memory of them.

Four operations, because anything less makes persistent memory something that
happens *to* a person: see it, correct it, take it away with them, and delete
it. Deletion is the one that has to be true rather than convincing.

Deleting a record deletes the private content behind it, and then follows the
evidence links forward. A summary written from a deleted message is still that
message; an embedding of it is still that message. Anything that was derived
from deleted content is deleted too, and the artifact keys of everything that
was ever built from it are returned so caches can be purged. The records
themselves stay — tombstoned, evidence-free, still linked — so history remains
readable without the content being recoverable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .content_store import PrivateContentStore
from .ledger import ProvenanceLedger
from .records import ProvenanceValidationError, is_effective

_MAX_CASCADE = 10_000


@dataclass(frozen=True)
class MemoryEntry:
    """One row of the memory the user is allowed to see."""

    record_id: str
    claim_key: str
    record_type: str
    status: str
    source_class: str
    source_id: str
    authority: str
    recorded_at: str
    confidence: float | None
    privacy_class: str
    evidence_ids: tuple[str, ...]
    contradiction_ids: tuple[str, ...]
    content: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "record_id": self.record_id,
            "claim_key": self.claim_key,
            "record_type": self.record_type,
            "status": self.status,
            "source_class": self.source_class,
            "source_id": self.source_id,
            "authority": self.authority,
            "recorded_at": self.recorded_at,
            "confidence": self.confidence,
            "privacy_class": self.privacy_class,
            "evidence_ids": list(self.evidence_ids),
            "contradiction_ids": list(self.contradiction_ids),
        }
        if self.content is not None:
            payload["content"] = self.content
        return payload


@dataclass
class DeletionResult:
    """Everything one deletion touched."""

    tombstoned_ids: list[str] = field(default_factory=list)
    purge_keys: set[str] = field(default_factory=set)
    purged_artifacts: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tombstoned_ids": sorted(self.tombstoned_ids),
            "purge_keys": sorted(self.purge_keys),
            "purged_artifacts": self.purged_artifacts,
        }


def inspect_memory(
    ledger: ProvenanceLedger,
    *,
    store: PrivateContentStore | None = None,
    claim_key: str | None = None,
    include_history: bool = False,
    at: datetime | None = None,
) -> list[MemoryEntry]:
    """List what the agent holds, optionally including retired claims.

    Content is included only when a store is passed, so the same function
    serves an operator view that must stay content-free and a user view that
    must not.
    """

    # The ledger's clock, not the wall clock: a record's validity window was
    # written against the same time source that stamped it.
    moment = at or ledger.now()
    records = ledger.records_for_claim(claim_key) if claim_key else ledger.records()
    entries: list[MemoryEntry] = []
    for record in records:
        if not include_history and not is_effective(record, moment):
            continue
        content: str | None = None
        if store is not None and record["content_ref"] is not None:
            try:
                content = store.get_text(record["content_ref"])
            except Exception:  # noqa: BLE001 - a missing blob must not hide the row
                content = None
        entries.append(
            MemoryEntry(
                record_id=record["record_id"],
                claim_key=record["claim_key"],
                record_type=record["record_type"],
                status=record["status"],
                source_class=record["source_class"],
                source_id=record["source_id"],
                authority=record["authority"],
                recorded_at=record["recorded_at"],
                confidence=record["confidence"],
                privacy_class=record["privacy_class"],
                evidence_ids=tuple(record["evidence_ids"]),
                contradiction_ids=tuple(record["contradiction_ids"]),
                content=content,
            )
        )
    return sorted(entries, key=lambda entry: (entry.recorded_at, entry.record_id))


def correct_memory(
    ledger: ProvenanceLedger,
    record_id: str,
    *,
    content: str,
    source_id: str,
    store: PrivateContentStore | None = None,
    content_ref: str | None = None,
    reason: str = "user_correction",
) -> dict[str, Any]:
    """Replace a claim with what the user says is actually true.

    The correction is a user assertion, so it outranks whatever it replaces and
    supersession is recorded rather than the old value being edited away. A
    correction of a *model's* conclusion cannot supersede it — different record
    types are different kinds of claim — so the conclusion is retracted and the
    user's version stands beside it in the history.
    """

    existing = ledger.get(record_id)
    if existing is None:
        raise ProvenanceValidationError(f"unknown record {record_id!r}")
    if existing["status"] == "tombstoned":
        raise ProvenanceValidationError(
            f"record {record_id!r} was deleted and cannot be corrected"
        )

    target_store = store or ledger.content_store
    new_ref = content_ref or f"{record_id}-correction-{ledger.sequence}"
    stored = target_store.put(new_ref, content, privacy_class=existing["privacy_class"])
    same_type = existing["record_type"] == "assertion"
    corrected = ledger.append_record(
        record_type="assertion",
        claim_key=existing["claim_key"],
        source_class="user",
        source_id=source_id,
        authority="reported",
        privacy_class=existing["privacy_class"],
        content_ref=stored.content_ref,
        content_digest=stored.content_digest,
        supersedes_id=record_id if same_type else None,
        reason=reason,
    )
    if not same_type:
        ledger.contest(corrected["record_id"], record_id, reason=reason)
        ledger.retract(record_id, reason=reason)
    return corrected


def export_memory(
    ledger: ProvenanceLedger,
    *,
    store: PrivateContentStore | None = None,
    include_history: bool = True,
) -> dict[str, Any]:
    """Export the user's memory, content included.

    This is the user's own data leaving the system at their request, so it is
    deliberately *not* the content-free shape used for shareable research
    traces. It must never be routed into an evaluation artifact.
    """

    entries = inspect_memory(ledger, store=store, include_history=include_history)
    return {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "entry_count": len(entries),
        "entries": [entry.as_dict() for entry in entries],
    }


def _dependents(records: Iterable[dict[str, Any]], root_ids: set[str]) -> set[str]:
    """Everything derived, transitively, from the given records."""

    by_evidence: dict[str, set[str]] = {}
    for record in records:
        for evidence_id in record["evidence_ids"]:
            by_evidence.setdefault(evidence_id, set()).add(record["record_id"])

    found: set[str] = set()
    pending = list(root_ids)
    while pending:
        if len(found) > _MAX_CASCADE:
            raise ProvenanceValidationError("deletion cascade exceeded its bound")
        current = pending.pop()
        for dependent in by_evidence.get(current, set()):
            if dependent not in found:
                found.add(dependent)
                pending.append(dependent)
    return found


def delete_memory(
    ledger: ProvenanceLedger,
    record_id: str,
    *,
    reason: str = "user_request",
    purge: Callable[[Iterable[str]], int] | None = None,
) -> DeletionResult:
    """Delete a memory, everything derived from it, and every derived artifact.

    Returns the artifact keys the caller must drop. ``purge`` is called with
    them when given, and its return value counts what was actually removed, so
    a deletion that could not reach a cache is visible instead of assumed.
    """

    if ledger.get(record_id) is None:
        raise ProvenanceValidationError(f"unknown record {record_id!r}")

    targets = {record_id} | _dependents(ledger.records(), {record_id})
    result = DeletionResult()
    for target in sorted(targets):
        record = ledger.get(target)
        if record is None or record["status"] == "tombstoned":
            continue
        result.purge_keys.update(ledger.tombstone(target, reason=reason))
        result.tombstoned_ids.append(target)
    if purge is not None and result.purge_keys:
        result.purged_artifacts = int(purge(sorted(result.purge_keys)))
    return result
