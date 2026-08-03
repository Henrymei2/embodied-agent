"""Privacy-safe ingestion bridge from live conversation turns to provenance."""

from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from .ledger import ProvenanceLedger
from .records import derive_claim_key


@dataclass(frozen=True)
class ConversationTurnEvidence:
    turn_ref: str
    assertion_id: str
    intention_id: str


class ConversationProvenanceBridge:
    """Record raw turn text only in the governed private content store.

    A turn assertion is deliberately scoped to one turn. Extracting a durable
    semantic preference or belief is a separate, policy-reviewed operation;
    simply hearing a sentence must not silently promote it into a permanent
    fact about the user.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.root = Path(workspace_root).resolve() / "provenance"
        self.ledger = ProvenanceLedger(self.root)
        self._lock = threading.RLock()

    @staticmethod
    def source_ref(source_id: str) -> str:
        digest = hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()[:24]
        return f"conversation-client:{digest}"

    def begin_turn(
        self,
        text: str,
        *,
        source_id: str,
        turn_ref: str | None = None,
    ) -> ConversationTurnEvidence:
        cleaned = str(text).strip()
        if not cleaned:
            raise ValueError("conversation provenance requires non-empty text")
        stable_turn = turn_ref or f"turn-{uuid.uuid4().hex}"
        claim_key = derive_claim_key("conversation_turn", stable_turn)
        source_ref = self.source_ref(source_id)
        with self._lock:
            existing = self.ledger.records_for_claim(claim_key)
            assertion = next(
                (record for record in existing if record["record_type"] == "assertion"),
                None,
            )
            intention = next(
                (record for record in existing if record["record_type"] == "intention"),
                None,
            )
            if assertion is not None and intention is not None:
                return ConversationTurnEvidence(
                    turn_ref=stable_turn,
                    assertion_id=assertion["record_id"],
                    intention_id=intention["record_id"],
                )

            if assertion is None:
                assertion_ref = f"content-{uuid.uuid4().hex}"
                stored = self.ledger.content_store.put(
                    assertion_ref, cleaned, privacy_class="private"
                )
                try:
                    assertion = self.ledger.append_record(
                        record_type="assertion",
                        claim_key=claim_key,
                        source_class="user",
                        source_id=source_ref,
                        authority="reported",
                        privacy_class="private",
                        content_ref=stored.content_ref,
                        content_digest=stored.content_digest,
                        reason="conversation_turn",
                    )
                except Exception:
                    self.ledger.content_store.delete(
                        stored.content_ref, reason="ledger_append_failed"
                    )
                    raise

            intention_ref = f"content-{uuid.uuid4().hex}"
            intention_content = "respond_to_user"
            intention_stored = self.ledger.content_store.put(
                intention_ref, intention_content, privacy_class="internal"
            )
            try:
                intention = self.ledger.append_record(
                    record_type="intention",
                    claim_key=claim_key,
                    source_class="system",
                    source_id="conversation-loop",
                    authority="system_authoritative",
                    privacy_class="internal",
                    content_ref=intention_stored.content_ref,
                    content_digest=intention_stored.content_digest,
                    evidence_ids=[assertion["record_id"]],
                    reason="turn_intention",
                )
            except Exception:
                self.ledger.content_store.delete(
                    intention_stored.content_ref, reason="ledger_append_failed"
                )
                raise
            return ConversationTurnEvidence(
                turn_ref=stable_turn,
                assertion_id=assertion["record_id"],
                intention_id=intention["record_id"],
            )

    def finish_turn(
        self,
        evidence: ConversationTurnEvidence,
        *,
        success: bool,
    ) -> str:
        """Append a content-free outcome, idempotently for one turn."""

        claim_key = derive_claim_key("conversation_outcome", evidence.turn_ref)
        with self._lock:
            existing = self.ledger.records_for_claim(claim_key)
            outcome = next(
                (record for record in existing if record["record_type"] == "outcome"),
                None,
            )
            if outcome is not None:
                return outcome["record_id"]
            content_ref = f"content-{uuid.uuid4().hex}"
            outcome_code = "turn_succeeded" if success else "turn_failed"
            stored = self.ledger.content_store.put(
                content_ref, outcome_code, privacy_class="internal"
            )
            try:
                outcome = self.ledger.append_record(
                    record_type="outcome",
                    claim_key=claim_key,
                    source_class="system",
                    source_id="conversation-loop",
                    authority="system_authoritative",
                    privacy_class="internal",
                    content_ref=stored.content_ref,
                    content_digest=stored.content_digest,
                    evidence_ids=[evidence.intention_id],
                    reason=outcome_code,
                )
            except Exception:
                self.ledger.content_store.delete(
                    stored.content_ref, reason="ledger_append_failed"
                )
                raise
            return outcome["record_id"]


_bridges: dict[str, ConversationProvenanceBridge] = {}
_bridges_lock = threading.Lock()


def get_conversation_provenance_bridge(
    workspace_root: Path,
) -> ConversationProvenanceBridge:
    key = str(Path(workspace_root).resolve())
    with _bridges_lock:
        bridge = _bridges.get(key)
        if bridge is None:
            bridge = ConversationProvenanceBridge(Path(key))
            _bridges[key] = bridge
        return bridge


def reset_conversation_provenance_bridges() -> None:
    """Drop process-local handles; durable data remains untouched."""

    with _bridges_lock:
        _bridges.clear()
