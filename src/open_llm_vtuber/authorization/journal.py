"""Durable, privacy-minimal transaction journal for consequential actions."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from filelock import FileLock

from .contracts import (
    ActionDescriptor,
    AuthorizationDecision,
    PolicyCheck,
    UserConfirmation,
)
from .profiles import ActionProfile

JOURNAL_SCHEMA_VERSION = 1
_ZERO_HASH = "0" * 64
_ACTIVE_STATUSES = frozenset(
    {"pending_confirmation", "approved", "committing", "verifying", "compensating"}
)
_TERMINAL_OUTCOMES = frozenset(
    {"denied", "declined", "previewed", "succeeded", "failed", "unresolved", "compensated", "replayed"}
)
_ATTEMPT_FIELDS = {
    "attempt_id",
    "delivery_key",
    "decision",
    "phase",
    "terminal_outcome",
    "reason_code",
    "started_at",
    "completed_at",
}
_ATTEMPT_PHASES = frozenset(
    {"policy", "committing", "verifying", "compensating", "terminal"}
)
_EFFECT_CLASSES = frozenset(
    {"none", "local_state", "external_state", "communication", "physical"}
)
_REVERSIBILITY = frozenset(
    {"reversible", "compensatable", "irreversible", "unknown"}
)
_VERIFICATION_KINDS = frozenset(
    {"none", "local_path", "adapter_evidence", "remote_readback", "frontend_ack"}
)
_EVIDENCE_FIELDS = frozenset(
    {"verified", "compensated", "kind", "evidence_digest"}
)


class AuthorizationJournalError(RuntimeError):
    """The authorization journal is corrupt or an invalid transition was requested."""


_JOURNALS: dict[Path, "ActionTransactionJournal"] = {}
_JOURNALS_LOCK = Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _entry_hash(entry: dict[str, Any]) -> str:
    material = {key: value for key, value in entry.items() if key != "entry_hash"}
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}:{secrets.token_hex(16)}"


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise AuthorizationJournalError(f"{field_name} must be a timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AuthorizationJournalError(f"{field_name} is invalid") from exc
    if parsed.tzinfo is None:
        raise AuthorizationJournalError(f"{field_name} requires an explicit offset")
    return parsed.astimezone(timezone.utc)


def _require_digest(value: Any, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise AuthorizationJournalError(f"{field_name} must be SHA-256")


def descriptor_from_record(record: dict[str, Any]) -> ActionDescriptor:
    """Reconstruct immutable descriptor fields after a JSON round trip."""

    return ActionDescriptor(
        **{
            **record,
            "evidence_refs": tuple(record.get("evidence_refs", ())),
        }
    )


class ActionTransactionJournal:
    """Append full transaction projections to a validated hash-chained JSONL log.

    The projection contains descriptors, decisions, and receipt digests, but never
    raw arguments or tool output. Reloading the final projection of each
    transaction makes recovery deterministic while retaining the complete audit
    trail.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root).resolve() / "authorization"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "transactions.jsonl"
        self.lock = FileLock(str(self.root / "transactions.lock"), timeout=5)
        self.recover_incomplete()

    @staticmethod
    def plan_id(transaction_id: str) -> str:
        return f"authz-{transaction_id.removeprefix('txn:')}"

    @staticmethod
    def transaction_id(plan_id: str) -> str | None:
        if not plan_id.startswith("authz-"):
            return None
        suffix = plan_id.removeprefix("authz-")
        if len(suffix) != 32 or any(ch not in "0123456789abcdef" for ch in suffix):
            return None
        return f"txn:{suffix}"

    def list_transactions(self) -> list[dict[str, Any]]:
        with self.lock:
            transactions, _, _ = self._load_unlocked()
        return sorted(
            (deepcopy(value) for value in transactions.values()),
            key=lambda value: value["created_at"],
        )

    def get(self, transaction_id: str) -> dict[str, Any] | None:
        with self.lock:
            transactions, _, _ = self._load_unlocked()
        transaction = transactions.get(transaction_id)
        return deepcopy(transaction) if transaction is not None else None

    def ensure_proposal(
        self,
        *,
        descriptor: ActionDescriptor,
        profile: ActionProfile,
        server_name: str,
        proposal_fingerprint: str,
        delivery_key: str,
    ) -> dict[str, Any]:
        descriptor.validate()
        with self.lock:
            transactions, sequence, previous_hash = self._load_unlocked()
            for transaction in reversed(list(transactions.values())):
                if delivery_key in transaction["delivery_keys"]:
                    return deepcopy(transaction)
            for transaction in reversed(list(transactions.values())):
                if (
                    transaction["proposal_fingerprint"] == proposal_fingerprint
                    and transaction["status"] in {"pending_confirmation", "approved"}
                ):
                    transaction = deepcopy(transaction)
                    transaction["delivery_keys"].append(delivery_key)
                    transaction["updated_at"] = _now()
                    self._append_unlocked(
                        "delivery_linked", transaction, sequence, previous_hash
                    )
                    return transaction

            timestamp = _now()
            transaction = {
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "transaction_id": _new_id("txn"),
                "descriptor": asdict(descriptor),
                "descriptor_digest": descriptor.digest,
                "proposal_fingerprint": proposal_fingerprint,
                "delivery_keys": [delivery_key],
                "server_name": server_name,
                "profile": asdict(profile),
                "status": (
                    "pending_confirmation"
                    if descriptor.required_user_authority == "explicit_confirmation"
                    else "approved"
                ),
                "confirmation": None,
                "attempts": [],
                "impact_receipt_digest": None,
                "verification": None,
                "compensation": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            self._validate_transaction(transaction)
            self._append_unlocked("proposal_created", transaction, sequence, previous_hash)
            return deepcopy(transaction)

    def decide(
        self,
        plan_id: str,
        decision: Literal["approved", "declined"],
    ) -> dict[str, Any] | None:
        transaction_id = self.transaction_id(plan_id)
        if transaction_id is None:
            return None
        with self.lock:
            transactions, sequence, previous_hash = self._load_unlocked()
            current = transactions.get(transaction_id)
            if current is None or current["status"] != "pending_confirmation":
                return None
            transaction = deepcopy(current)
            timestamp = _now()
            if decision == "approved":
                confirmation = UserConfirmation(
                    confirmation_id=_new_id("confirmation"),
                    descriptor_digest=transaction["descriptor_digest"],
                    issued_by="user",
                    issued_at=timestamp,
                    expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                )
                confirmation.validate()
                transaction["confirmation"] = asdict(confirmation)
                transaction["status"] = "approved"
            else:
                transaction["status"] = "declined"
            transaction["updated_at"] = timestamp
            self._append_unlocked(
                f"proposal_{decision}", transaction, sequence, previous_hash
            )
            return transaction

    def reopen_expired_confirmation(
        self, transaction_id: str
    ) -> dict[str, Any]:
        """Return an expired exact-confirmation proposal to the trusted UI."""

        with self.lock:
            transactions, sequence, previous_hash = self._load_unlocked()
            current = self._required(transactions, transaction_id)
            descriptor = descriptor_from_record(current["descriptor"])
            if (
                current["status"] != "approved"
                or descriptor.required_user_authority != "explicit_confirmation"
            ):
                return deepcopy(current)
            transaction = deepcopy(current)
            transaction["confirmation"] = None
            transaction["status"] = "pending_confirmation"
            transaction["updated_at"] = _now()
            self._append_unlocked(
                "confirmation_expired", transaction, sequence, previous_hash
            )
            return transaction

    def begin_attempt(
        self,
        transaction_id: str,
        *,
        delivery_key: str,
        decision: AuthorizationDecision,
    ) -> dict[str, Any]:
        decision.validate()
        with self.lock:
            transactions, sequence, previous_hash = self._load_unlocked()
            current = self._required(transactions, transaction_id)
            transaction = deepcopy(current)
            attempt = {
                "attempt_id": _new_id("attempt"),
                "delivery_key": delivery_key,
                "decision": asdict(decision),
                "phase": "policy",
                "terminal_outcome": None,
                "reason_code": None,
                "started_at": _now(),
                "completed_at": None,
            }
            transaction["attempts"].append(attempt)
            transaction["updated_at"] = attempt["started_at"]
            self._append_unlocked("attempt_started", transaction, sequence, previous_hash)
            return deepcopy(attempt)

    def transition(
        self,
        transaction_id: str,
        *,
        status: Literal["committing", "verifying", "compensating"],
        attempt_id: str,
        receipt_digest: str | None = None,
        verification: dict[str, Any] | None = None,
        compensation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            transactions, sequence, previous_hash = self._load_unlocked()
            transaction = deepcopy(self._required(transactions, transaction_id))
            attempt = self._attempt(transaction, attempt_id)
            if attempt["terminal_outcome"] is not None:
                raise AuthorizationJournalError("a terminal attempt cannot transition")
            attempt["phase"] = status
            transaction["status"] = status
            if receipt_digest is not None:
                transaction["impact_receipt_digest"] = receipt_digest
            if verification is not None:
                transaction["verification"] = deepcopy(verification)
            if compensation is not None:
                transaction["compensation"] = deepcopy(compensation)
            transaction["updated_at"] = _now()
            self._append_unlocked(
                f"transaction_{status}", transaction, sequence, previous_hash
            )
            return transaction

    def finish_attempt(
        self,
        transaction_id: str,
        *,
        attempt_id: str,
        outcome: Literal[
            "denied", "declined", "previewed", "succeeded", "failed",
            "unresolved", "compensated", "replayed"
        ],
        reason_code: str,
        transaction_status: str | None = None,
    ) -> dict[str, Any]:
        if outcome not in _TERMINAL_OUTCOMES:
            raise AuthorizationJournalError("invalid terminal outcome")
        with self.lock:
            transactions, sequence, previous_hash = self._load_unlocked()
            transaction = deepcopy(self._required(transactions, transaction_id))
            attempt = self._attempt(transaction, attempt_id)
            if attempt["terminal_outcome"] is not None:
                raise AuthorizationJournalError("attempt already has a terminal outcome")
            attempt["terminal_outcome"] = outcome
            attempt["reason_code"] = reason_code[:200]
            attempt["completed_at"] = _now()
            attempt["phase"] = "terminal"
            next_status = transaction_status or outcome
            if next_status not in _ACTIVE_STATUSES | _TERMINAL_OUTCOMES:
                raise AuthorizationJournalError("resulting transaction status is invalid")
            transaction["status"] = next_status
            transaction["updated_at"] = attempt["completed_at"]
            self._append_unlocked("attempt_finished", transaction, sequence, previous_hash)
            return transaction

    def recent_commit_count(self, *, limit: int = 20) -> int:
        transactions = self.list_transactions()
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
        count = 0
        for transaction in transactions:
            for attempt in transaction["attempts"]:
                if attempt["terminal_outcome"] not in {"succeeded", "unresolved", "compensated"}:
                    continue
                started = datetime.fromisoformat(attempt["started_at"])
                if started >= cutoff:
                    count += 1
        return min(count, limit)

    def pending_snapshot(self, *, limit: int = 3) -> dict[str, Any]:
        transactions = self.list_transactions()

        def render(transaction: dict[str, Any]) -> dict[str, Any]:
            descriptor = transaction["descriptor"]
            profile = transaction["profile"]
            destination = descriptor.get("external_destination")
            steps = [
                f"Effect: {descriptor['side_effect_class'].replace('_', ' ')}.",
                f"Reversibility: {profile['reversibility']}.",
                f"Verification: {profile['verification']}.",
            ]
            if destination:
                steps.append(f"Destination reference: {destination}.")
            return {
                "plan_id": self.plan_id(transaction["transaction_id"]),
                "title": f"Authorize {descriptor['capability'].replace('_', ' ')}",
                "goal": "Approve only if this exact action is expected. Private arguments are represented by a digest and are not stored here.",
                "steps": steps,
                "status": "pending" if transaction["status"] == "pending_confirmation" else transaction["status"],
                "created_at": transaction["created_at"],
            }

        pending = [item for item in transactions if item["status"] == "pending_confirmation"]
        recent = [item for item in transactions if item["status"] in {"approved", "declined"}]
        return {
            "pending": [render(item) for item in pending[:limit]],
            "pending_count": len(pending),
            "recent": render(recent[-1]) if recent else None,
        }

    def recover_incomplete(self) -> None:
        with self.lock:
            transactions, sequence, previous_hash = self._load_unlocked()
            for current in list(transactions.values()):
                transaction = deepcopy(current)
                open_attempts = [
                    item for item in transaction["attempts"] if item["terminal_outcome"] is None
                ]
                if not open_attempts:
                    continue
                attempt = open_attempts[-1]
                after_commit = attempt["phase"] in {
                    "committing",
                    "verifying",
                    "compensating",
                }
                outcome = "unresolved" if after_commit else "failed"
                attempt["terminal_outcome"] = outcome
                attempt["reason_code"] = (
                    "process_interrupted_after_commit_boundary"
                    if after_commit
                    else "process_interrupted_before_commit_boundary"
                )
                attempt["completed_at"] = _now()
                attempt["phase"] = "terminal"
                transaction["status"] = (
                    outcome
                    if after_commit
                    else current["status"]
                )
                transaction["updated_at"] = attempt["completed_at"]
                sequence, previous_hash = self._append_unlocked(
                    "crash_recovered", transaction, sequence, previous_hash
                )

    @staticmethod
    def _required(
        transactions: dict[str, dict[str, Any]], transaction_id: str
    ) -> dict[str, Any]:
        try:
            return transactions[transaction_id]
        except KeyError as exc:
            raise AuthorizationJournalError("transaction does not exist") from exc

    @staticmethod
    def _attempt(transaction: dict[str, Any], attempt_id: str) -> dict[str, Any]:
        for attempt in transaction["attempts"]:
            if attempt["attempt_id"] == attempt_id:
                return attempt
        raise AuthorizationJournalError("attempt does not exist")

    def _append_unlocked(
        self,
        event_type: str,
        transaction: dict[str, Any],
        sequence: int,
        previous_hash: str,
    ) -> tuple[int, str]:
        self._validate_transaction(transaction)
        entry = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "sequence": sequence + 1,
            "event_type": event_type,
            "transaction_id": transaction["transaction_id"],
            "transaction": transaction,
            "recorded_at": _now(),
            "previous_entry_hash": previous_hash,
        }
        entry["entry_hash"] = _entry_hash(entry)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical(entry) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry["sequence"], entry["entry_hash"]

    def _load_unlocked(self) -> tuple[dict[str, dict[str, Any]], int, str]:
        transactions: dict[str, dict[str, Any]] = {}
        if not self.path.exists():
            return transactions, 0, _ZERO_HASH
        previous_hash = _ZERO_HASH
        sequence = 0
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AuthorizationJournalError(
                        f"authorization journal line {line_number} is invalid JSON"
                    ) from exc
                if entry.get("schema_version") != JOURNAL_SCHEMA_VERSION:
                    raise AuthorizationJournalError("unsupported journal schema")
                if entry.get("sequence") != sequence + 1:
                    raise AuthorizationJournalError("journal sequence is discontinuous")
                if entry.get("previous_entry_hash") != previous_hash:
                    raise AuthorizationJournalError("journal hash chain is discontinuous")
                if entry.get("entry_hash") != _entry_hash(entry):
                    raise AuthorizationJournalError("journal entry digest does not match")
                transaction = entry.get("transaction")
                self._validate_transaction(transaction)
                if entry.get("transaction_id") != transaction["transaction_id"]:
                    raise AuthorizationJournalError("journal transaction identity mismatch")
                transactions[transaction["transaction_id"]] = transaction
                sequence = entry["sequence"]
                previous_hash = entry["entry_hash"]
        return transactions, sequence, previous_hash

    @staticmethod
    def _validate_transaction(transaction: Any) -> None:
        if not isinstance(transaction, dict):
            raise AuthorizationJournalError("transaction projection must be an object")
        required = {
            "schema_version", "transaction_id", "descriptor", "descriptor_digest",
                "proposal_fingerprint",
                "delivery_keys",
                "server_name",
                "profile",
                "status",
                "confirmation",
                "attempts",
                "impact_receipt_digest",
                "verification",
                "compensation",
                "created_at",
                "updated_at",
        }
        if set(transaction) != required:
            raise AuthorizationJournalError("transaction projection fields are invalid")
        if transaction["schema_version"] != JOURNAL_SCHEMA_VERSION:
            raise AuthorizationJournalError("unsupported transaction schema")
        transaction_id = transaction["transaction_id"]
        if not isinstance(transaction_id, str) or not transaction_id.startswith("txn:"):
            raise AuthorizationJournalError("transaction identity is invalid")
        _require_digest(transaction["proposal_fingerprint"], "proposal fingerprint")
        if not isinstance(transaction["server_name"], str) or not transaction[
            "server_name"
        ]:
            raise AuthorizationJournalError("transaction server name is invalid")
        created = _parse_timestamp(transaction["created_at"], "transaction.created_at")
        updated = _parse_timestamp(transaction["updated_at"], "transaction.updated_at")
        if updated < created:
            raise AuthorizationJournalError("transaction update precedes creation")
        descriptor = descriptor_from_record(transaction["descriptor"])
        descriptor.validate()
        if descriptor.digest != transaction["descriptor_digest"]:
            raise AuthorizationJournalError("descriptor digest does not match")
        if transaction["confirmation"] is not None:
            confirmation = UserConfirmation(**transaction["confirmation"])
            confirmation.validate()
            if confirmation.descriptor_digest != descriptor.digest:
                raise AuthorizationJournalError("confirmation does not bind descriptor")
        if (
            not isinstance(transaction["delivery_keys"], list)
            or not transaction["delivery_keys"]
        ):
            raise AuthorizationJournalError("transaction requires a delivery key")
        if len(transaction["delivery_keys"]) != len(set(transaction["delivery_keys"])):
            raise AuthorizationJournalError("transaction delivery keys must be unique")
        if any(
            not isinstance(key, str) or not key.startswith("delivery:")
            for key in transaction["delivery_keys"]
        ):
            raise AuthorizationJournalError("transaction delivery key is invalid")
        if transaction["status"] not in _ACTIVE_STATUSES | _TERMINAL_OUTCOMES:
            raise AuthorizationJournalError("transaction status is invalid")
        try:
            profile = ActionProfile(**transaction["profile"])
        except (TypeError, ValueError) as exc:
            raise AuthorizationJournalError("transaction profile is invalid") from exc
        if (
            profile.capability != descriptor.capability
            or profile.side_effect_class not in _EFFECT_CLASSES
            or profile.reversibility not in _REVERSIBILITY
            or profile.verification not in _VERIFICATION_KINDS
            or not isinstance(profile.native_idempotency, bool)
        ):
            raise AuthorizationJournalError("transaction profile does not match action")
        if transaction["impact_receipt_digest"] is not None:
            _require_digest(
                transaction["impact_receipt_digest"], "impact receipt digest"
            )
        for field_name in ("verification", "compensation"):
            evidence = transaction[field_name]
            if evidence is None:
                continue
            if not isinstance(evidence, dict) or not set(evidence).issubset(
                _EVIDENCE_FIELDS
            ):
                raise AuthorizationJournalError(
                    f"transaction {field_name} evidence is invalid"
                )
            if "evidence_digest" in evidence:
                _require_digest(
                    evidence["evidence_digest"], f"{field_name} evidence digest"
                )
        if not isinstance(transaction["attempts"], list):
            raise AuthorizationJournalError("transaction attempts must be a list")
        for attempt in transaction["attempts"]:
            if not isinstance(attempt, dict) or set(attempt) != _ATTEMPT_FIELDS:
                raise AuthorizationJournalError("transaction attempt fields are invalid")
            if (
                not isinstance(attempt["attempt_id"], str)
                or not attempt["attempt_id"].startswith("attempt:")
                or attempt["delivery_key"] not in transaction["delivery_keys"]
                or attempt["phase"] not in _ATTEMPT_PHASES
            ):
                raise AuthorizationJournalError("transaction attempt identity is invalid")
            _parse_timestamp(attempt["started_at"], "attempt.started_at")
            decision = AuthorizationDecision(
                **{
                    **attempt["decision"],
                    "checks": tuple(
                        PolicyCheck(**check)
                        for check in attempt["decision"]["checks"]
                    ),
                }
            )
            decision.validate()
            if (
                decision.action_id != descriptor.action_id
                or decision.descriptor_digest != descriptor.digest
            ):
                raise AuthorizationJournalError("attempt decision does not bind action")
            if (attempt["terminal_outcome"] is None) != (
                attempt["completed_at"] is None
            ):
                raise AuthorizationJournalError("attempt terminal record is incomplete")
            if attempt["terminal_outcome"] is None:
                if attempt["phase"] == "terminal" or attempt["reason_code"] is not None:
                    raise AuthorizationJournalError("open attempt lifecycle is invalid")
            else:
                if (
                    attempt["terminal_outcome"] not in _TERMINAL_OUTCOMES
                    or attempt["phase"] != "terminal"
                    or not isinstance(attempt["reason_code"], str)
                    or not attempt["reason_code"]
                ):
                    raise AuthorizationJournalError("terminal attempt lifecycle is invalid")
                completed = _parse_timestamp(
                    attempt["completed_at"], "attempt.completed_at"
                )
                started = _parse_timestamp(attempt["started_at"], "attempt.started_at")
                if completed < started:
                    raise AuthorizationJournalError(
                        "attempt completion precedes its start"
                    )


def get_action_transaction_journal(
    workspace_root: str | Path,
) -> ActionTransactionJournal:
    """Return one process-local journal facade per resolved workspace."""

    root = Path(workspace_root).resolve()
    with _JOURNALS_LOCK:
        journal = _JOURNALS.get(root)
        if journal is None:
            journal = ActionTransactionJournal(root)
            _JOURNALS[root] = journal
        return journal
