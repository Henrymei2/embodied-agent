"""A durable, append-only ledger of typed provenance records.

Belief changes are stored as *operations*, not as edits. A record's status
moves from active to contested to retracted by appending an entry that says so;
nothing already written is ever rewritten. That is what makes "why does the
agent think this?" answerable after the fact, and what stops a later mistake
from quietly erasing the evidence that produced an earlier one.

Every entry carries the derived state changes it caused, so replaying the log
reproduces the same graph without re-running the policy that decided them.
Policy may improve between versions; history may not.

Two validation passes, on purpose:

*At write time* the candidate state is checked incrementally and the entry is
only appended if the resulting graph would be valid — an operation that would
corrupt the ledger leaves no trace at all.

*At load time* the whole chain is re-hashed and the materialized graph is
validated in full, so a truncated, reordered, or hand-edited file fails closed
rather than being interpreted as a smaller history.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from filelock import FileLock, Timeout
from loguru import logger

from .content_store import PrivateContentStore
from .records import (
    PROVENANCE_SCHEMA_VERSION,
    RESOLVED_STATUSES,
    ProvenanceValidationError,
    build_provenance_record,
    is_effective,
    parse_timestamp,
    validate_provenance_graph,
    validate_provenance_record,
)

LEDGER_SCHEMA_VERSION = 1
LEDGER_OPERATIONS = frozenset({"append", "contest", "retract", "tombstone"})
_MAX_ENTRIES_DEFAULT = 200_000
_ENTRY_KEYS = {
    "schema_version",
    "sequence",
    "entry_id",
    "recorded_at",
    "operation",
    "reason",
    "record",
    "targets",
    "status_changes",
    "link_changes",
    "previous_entry_hash",
    "entry_hash",
}


class LedgerIntegrityError(ProvenanceValidationError):
    """The durable ledger is missing, truncated, reordered, or tampered with."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> str:
    # Deliberately the same recipe the evaluation evidence chain uses; kept
    # local so the runtime memory path does not depend on the research
    # measurement package.
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise LedgerIntegrityError(
            f"ledger entry is not canonical JSON: {exc}"
        ) from exc


def _entry_hash(entry: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in entry.items() if key != "entry_hash"}
    return hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()


class ProvenanceLedger:
    """Append-only provenance storage with an atomically validated materialization."""

    def __init__(
        self,
        root: Path,
        *,
        content_store: PrivateContentStore | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
        max_entries: int = _MAX_ENTRIES_DEFAULT,
    ) -> None:
        self.root = Path(root).resolve()
        self.entries_path = self.root / "ledger.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self._file_lock = FileLock(str(self.root / "ledger.lock"), timeout=5)
        self.content_store = content_store or PrivateContentStore(self.root / "content")
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._max_entries = max(1, min(int(max_entries), _MAX_ENTRIES_DEFAULT))
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._sequence = 0
        self._previous_hash: str | None = None
        self._claims: dict[str, set[str]] = {}
        try:
            with self._file_lock:
                self._load()
        except Timeout as exc:
            raise LedgerIntegrityError("provenance ledger is busy") from exc

    # ------------------------------------------------------------------ read

    @property
    def sequence(self) -> int:
        """How many entries have been appended."""

        with self._lock:
            self._refresh()
            return self._sequence

    def records(self) -> list[dict[str, Any]]:
        """A snapshot of the materialized graph, ordered by record id."""

        with self._lock:
            self._refresh()
            return [
                json.loads(_canonical_json(self._records[key]))
                for key in sorted(self._records)
            ]

    def now(self) -> datetime:
        """The ledger's time source, so callers do not mix it with wall-clock."""

        return self._clock()

    def new_record_id(self) -> str:
        """Mint an id so a caller can reason about a record before storing it."""

        return f"rec-{self._id_factory()}"

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._refresh()
            record = self._records.get(record_id)
            return json.loads(_canonical_json(record)) if record else None

    def records_for_claim(self, claim_key: str) -> list[dict[str, Any]]:
        with self._lock:
            self._refresh()
            ids = sorted(self._claims.get(claim_key, set()))
            return [json.loads(_canonical_json(self._records[key])) for key in ids]

    def effective_records(self, at: datetime | None = None) -> list[dict[str, Any]]:
        """Records that currently speak for their claim."""

        moment = at or self._clock()
        return [record for record in self.records() if is_effective(record, moment)]

    def verify(self) -> None:
        """Re-validate the whole materialized graph."""

        with self._lock:
            self._refresh()
            validate_provenance_graph(list(self._records.values()), allow_empty=True)

    # ----------------------------------------------------------------- write

    def append_record(
        self,
        *,
        record_id: str | None = None,
        record_type: str,
        claim_key: str,
        source_class: str,
        source_id: str,
        authority: str,
        privacy_class: str,
        content_ref: str | None,
        content_digest: str,
        observed_at: str | datetime | None = None,
        recorded_at: str | datetime | None = None,
        valid_from: str | datetime | None = None,
        expires_at: str | datetime | None = None,
        confidence: float | None = None,
        evidence_ids: Iterable[str] | None = None,
        supersedes_id: str | None = None,
        reason: str = "recorded",
    ) -> dict[str, Any]:
        """Append one new record, superseding a prior record where asked."""

        with self._lock:
            self._refresh()
            now = self._clock()
            stamp = _stamp(recorded_at, now)
            record = build_provenance_record(
                record_id=record_id or f"rec-{self._id_factory()}",
                record_type=record_type,
                claim_key=claim_key,
                source_class=source_class,
                source_id=source_id,
                authority=authority,
                observed_at=_stamp(observed_at, now),
                recorded_at=stamp,
                valid_from=_stamp(valid_from, now),
                expires_at=_stamp(expires_at, None),
                confidence=confidence,
                privacy_class=privacy_class,
                content_ref=content_ref,
                content_digest=content_digest,
                evidence_ids=list(evidence_ids or []),
            )
            if record["record_id"] in self._records:
                raise ProvenanceValidationError(
                    f"record {record['record_id']!r} already exists in the ledger"
                )
            status_changes: list[dict[str, str]] = []
            if supersedes_id is not None:
                prior = self._require(supersedes_id)
                if prior["status"] in RESOLVED_STATUSES:
                    raise ProvenanceValidationError(
                        f"record {supersedes_id!r} is {prior['status']} and cannot be "
                        "superseded"
                    )
                record["supersedes_id"] = supersedes_id
                status_changes.append(
                    {"record_id": supersedes_id, "status": "superseded"}
                )
            validate_provenance_record(record)
            return self._commit(
                operation="append",
                reason=reason,
                record=record,
                targets=[supersedes_id] if supersedes_id else [],
                status_changes=status_changes,
                link_changes=[],
                recorded_at=stamp,
            )["record"]

    def contest(
        self, first_id: str, second_id: str, *, reason: str = "contradiction"
    ) -> None:
        """Link two records as contradicting each other, symmetrically."""

        with self._lock:
            self._refresh()
            if first_id == second_id:
                raise ProvenanceValidationError("a record cannot contradict itself")
            first = self._require(first_id)
            second = self._require(second_id)
            for record in (first, second):
                if record["status"] in RESOLVED_STATUSES:
                    raise ProvenanceValidationError(
                        f"record {record['record_id']!r} is {record['status']} and "
                        "cannot enter a new contradiction"
                    )
            link_changes = [
                {
                    "record_id": first_id,
                    "contradiction_ids": sorted(
                        set(first["contradiction_ids"]) | {second_id}
                    ),
                },
                {
                    "record_id": second_id,
                    "contradiction_ids": sorted(
                        set(second["contradiction_ids"]) | {first_id}
                    ),
                },
            ]
            status_changes = [
                {"record_id": first_id, "status": "contested"},
                {"record_id": second_id, "status": "contested"},
            ]
            self._commit(
                operation="contest",
                reason=reason,
                record=None,
                targets=sorted([first_id, second_id]),
                status_changes=status_changes,
                link_changes=link_changes,
                recorded_at=_iso(self._clock()),
            )

    def retract(self, record_id: str, *, reason: str = "outranked") -> None:
        """Withdraw a claim while keeping its evidence and its content."""

        with self._lock:
            self._refresh()
            record = self._require(record_id)
            if record["status"] in RESOLVED_STATUSES:
                return
            self._commit(
                operation="retract",
                reason=reason,
                record=None,
                targets=[record_id],
                status_changes=[{"record_id": record_id, "status": "retracted"}]
                + self._releases(record_id),
                link_changes=[],
                recorded_at=_iso(self._clock()),
            )

    def tombstone(
        self, record_id: str, *, reason: str = "user_request"
    ) -> frozenset[str]:
        """Delete the private content behind a record and retire the record.

        Returns the derived-artifact keys — embeddings, summaries — that the
        caller must purge for the deletion to actually mean anything.
        """

        with self._lock:
            self._refresh()
            record = self._require(record_id)
            if record["status"] == "tombstoned":
                return frozenset()
            content_ref = record["content_ref"]
            purge_keys: frozenset[str] = frozenset()
            if content_ref is not None and not self.content_store.is_deleted(
                content_ref
            ):
                try:
                    purge_keys = self.content_store.delete(content_ref, reason=reason)
                except KeyError:
                    # The ledger is the authority on what exists; content that
                    # is already gone must still let the record be retired.
                    purge_keys = frozenset()
            self._commit(
                operation="tombstone",
                reason=reason,
                record=None,
                targets=[record_id],
                status_changes=[{"record_id": record_id, "status": "tombstoned"}]
                + self._releases(record_id),
                link_changes=[],
                recorded_at=_iso(self._clock()),
            )
            return purge_keys

    # -------------------------------------------------------------- internal

    def _refresh(self) -> None:
        try:
            with self._file_lock:
                self._load()
        except Timeout as exc:
            raise LedgerIntegrityError("provenance ledger is busy") from exc

    def _require(self, record_id: str) -> dict[str, Any]:
        record = self._records.get(record_id)
        if record is None:
            raise ProvenanceValidationError(f"unknown record {record_id!r}")
        return record

    def _releases(self, resolved_id: str) -> list[dict[str, str]]:
        """Contested peers that become settled once ``resolved_id`` is resolved."""

        changes: list[dict[str, str]] = []
        for peer_id in self._records[resolved_id]["contradiction_ids"]:
            peer = self._records.get(peer_id)
            if peer is None or peer["status"] != "contested":
                continue
            unresolved = [
                other
                for other in peer["contradiction_ids"]
                if other != resolved_id
                and self._records.get(other, {}).get("status") not in RESOLVED_STATUSES
            ]
            if not unresolved:
                changes.append({"record_id": peer_id, "status": "active"})
        return changes

    def _commit(
        self,
        *,
        operation: str,
        reason: str,
        record: dict[str, Any] | None,
        targets: list[str],
        status_changes: list[dict[str, str]],
        link_changes: list[dict[str, Any]],
        recorded_at: str,
    ) -> dict[str, Any]:
        """Serialize writers across processes and refresh before sequencing."""

        try:
            with self._file_lock:
                # Another server process may have appended since this instance
                # was constructed. Replaying under the writer lock prevents two
                # valid local sequence numbers from corrupting one shared chain.
                self._load()
                return self._commit_locked(
                    operation=operation,
                    reason=reason,
                    record=record,
                    targets=targets,
                    status_changes=status_changes,
                    link_changes=link_changes,
                    recorded_at=recorded_at,
                )
        except Timeout as exc:
            raise LedgerIntegrityError("provenance ledger is busy") from exc

    def _commit_locked(
        self,
        *,
        operation: str,
        reason: str,
        record: dict[str, Any] | None,
        targets: list[str],
        status_changes: list[dict[str, str]],
        link_changes: list[dict[str, Any]],
        recorded_at: str,
    ) -> dict[str, Any]:
        if self._sequence >= self._max_entries:
            raise LedgerIntegrityError("provenance ledger entry budget exhausted")
        entry = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "sequence": self._sequence,
            "entry_id": self._id_factory(),
            "recorded_at": recorded_at,
            "operation": operation,
            "reason": str(reason)[:64],
            "record": record,
            "targets": list(targets),
            "status_changes": status_changes,
            "link_changes": link_changes,
            "previous_entry_hash": self._previous_hash,
        }
        entry["entry_hash"] = _entry_hash(entry)

        # Apply to a copy first: a rejected operation must leave both the file
        # and the in-memory graph exactly as they were. ``_apply`` copies each
        # record before touching it, so this shallow copy is enough.
        candidate = dict(self._records)
        _apply(candidate, entry)
        validate_provenance_graph(list(candidate.values()), allow_empty=True)

        line = f"{_canonical_json(entry)}\n"
        with self.entries_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

        self._records = candidate
        self._reindex()
        self._sequence += 1
        self._previous_hash = entry["entry_hash"]
        return entry

    def _reindex(self) -> None:
        self._claims = {}
        for record_id, record in self._records.items():
            self._claims.setdefault(record["claim_key"], set()).add(record_id)

    def _load(self) -> None:
        if not self.entries_path.exists():
            return
        try:
            data = self.entries_path.read_bytes()
            # A final line with no newline after it is a write that a crash cut
            # in half. That operation was never acknowledged, so discarding it
            # loses nothing anyone was told had happened — whereas refusing to
            # open the ledger would lose a whole memory over one lost append.
            # It is repaired on disk, or the next append would splice itself
            # onto the fragment and corrupt an entry that is currently only
            # incomplete.
            if data and not data.endswith(b"\n"):
                keep = data.rfind(b"\n") + 1
                with self.entries_path.open("r+b") as handle:
                    handle.truncate(keep)
                    handle.flush()
                    os.fsync(handle.fileno())
                data = data[:keep]
                logger.warning(
                    "Provenance: discarded an unterminated final ledger entry; "
                    "the operation interrupted by the last shutdown was not kept."
                )
            raw = data.decode("utf-8")
        except OSError as exc:
            raise LedgerIntegrityError(f"cannot read provenance ledger: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise LedgerIntegrityError(
                f"provenance ledger is not UTF-8: {exc}"
            ) from exc

        lines = raw.splitlines()
        records: dict[str, dict[str, Any]] = {}
        previous_hash: str | None = None
        sequence = 0
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerIntegrityError(
                    f"ledger[{index}]: invalid JSON; a truncated ledger is not a "
                    "shorter history"
                ) from exc
            _validate_entry(entry, index)
            if entry["sequence"] != sequence:
                raise LedgerIntegrityError(
                    f"ledger[{index}]: sequence {entry['sequence']} breaks the "
                    f"expected order at {sequence}"
                )
            if entry["previous_entry_hash"] != previous_hash:
                raise LedgerIntegrityError(
                    f"ledger[{index}]: previous_entry_hash does not chain"
                )
            if entry["entry_hash"] != _entry_hash(entry):
                raise LedgerIntegrityError(
                    f"ledger[{index}]: entry hash does not match"
                )
            _apply(records, entry)
            previous_hash = entry["entry_hash"]
            sequence += 1

        validate_provenance_graph(list(records.values()), allow_empty=True)
        self._records = records
        self._sequence = sequence
        self._previous_hash = previous_hash
        self._reindex()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _stamp(value: str | datetime | None, default: datetime | None) -> str | None:
    if value is None:
        return _iso(default) if default is not None else None
    if isinstance(value, datetime):
        return _iso(value)
    parse_timestamp(value, "timestamp")
    return value


def _validate_entry(entry: Any, index: int) -> None:
    if not isinstance(entry, dict):
        raise LedgerIntegrityError(f"ledger[{index}]: entry must be an object")
    missing = sorted(_ENTRY_KEYS - set(entry))
    extra = sorted(set(entry) - _ENTRY_KEYS)
    if missing or extra:
        raise LedgerIntegrityError(
            f"ledger[{index}]: keys differ; missing={missing}, extra={extra}"
        )
    if entry["schema_version"] != LEDGER_SCHEMA_VERSION:
        raise LedgerIntegrityError(
            f"ledger[{index}]: unsupported ledger schema version"
        )
    if entry["operation"] not in LEDGER_OPERATIONS:
        raise LedgerIntegrityError(
            f"ledger[{index}]: unknown operation {entry['operation']!r}"
        )
    if not isinstance(entry["sequence"], int) or isinstance(entry["sequence"], bool):
        raise LedgerIntegrityError(f"ledger[{index}]: sequence must be an integer")


def _apply(records: dict[str, dict[str, Any]], entry: dict[str, Any]) -> None:
    """Apply one entry's recorded effects to a materialized graph.

    Copy-on-write: callers pass a shallow copy of the live graph, so a record
    is duplicated before it is changed and a rejected operation cannot leave a
    half-applied mutation behind.
    """

    def mutable(record_id: str, operation: str) -> dict[str, Any]:
        target = records.get(record_id)
        if target is None:
            raise LedgerIntegrityError(
                f"ledger {operation} unknown record {record_id!r}"
            )
        clone = dict(target)
        records[record_id] = clone
        return clone

    record = entry["record"]
    if record is not None:
        if not isinstance(record, dict):
            raise LedgerIntegrityError("ledger entry record must be an object")
        if record.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
            raise LedgerIntegrityError(
                "ledger entry record uses an unsupported record schema version"
            )
        record_id = record.get("record_id")
        if record_id in records:
            raise LedgerIntegrityError(
                f"ledger replays a duplicate record {record_id!r}"
            )
        records[record_id] = json.loads(_canonical_json(record))

    for change in entry["link_changes"]:
        target = mutable(change["record_id"], "links")
        target["contradiction_ids"] = list(change["contradiction_ids"])

    for change in entry["status_changes"]:
        target = mutable(change["record_id"], "transitions")
        target["status"] = change["status"]
        if change["status"] == "tombstoned":
            target["content_ref"] = None
