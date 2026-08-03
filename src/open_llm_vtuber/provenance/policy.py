"""What to do when two records answer the same question differently.

The failure this prevents is quiet: a model infers something, writes it into
memory, and it displaces what the user actually said — after which the agent
confidently repeats its own mistake back to them, with no way to see where it
came from. So a contradiction never resolves itself by overwriting. The losing
claim is *retracted*, both sides stay in the ledger, and the reason is a code
that can be shown to the user.

Conflicts are detected without reading content: records that share a claim key
answer the same question, and records whose content digests differ give
different answers to it.

Five outcomes, in the order the system prefers them:

``supersede``       a source updating itself, or a strictly higher authority
``retract_candidate`` the new claim loses; the incumbent is untouched
``retain_both``     two holders can both be right; keep both hypotheses
``re_observe``      neither side is trustworthy enough; go look again
``ask``             only the user can settle it — and only if they are there
``defer``           asking would interrupt, or the evidence is not in yet
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from .records import (
    AUTHORITY_RANK,
    SOURCE_RANK,
    ProvenanceValidationError,
    build_provenance_record,
    is_effective,
    parse_timestamp,
)

DECISIONS = frozenset(
    {
        "supersede",
        "retract_candidate",
        "retract_incumbent",
        "retain_both",
        "re_observe",
        "ask",
        "defer",
    }
)

_DIRECT_OBSERVERS = frozenset({"sensor", "tool"})
_DEFAULT_STALE_AFTER = timedelta(hours=24)
_DEFAULT_DEFER = 900


@dataclass(frozen=True)
class PolicyContext:
    """What the policy is allowed to assume about the moment it runs in.

    ``now`` should come from ``ProvenanceLedger.now()``. Two clocks drift, and
    a record stamped ahead of the ledger that stores it is not yet in effect
    when the ledger is asked what it currently holds.

    ``user_present`` and ``attention_available`` come from the Presence
    contract. Asking a question is a cost against a budget, not a free move, so
    a policy that would ask when nobody is there defers instead.
    """

    now: datetime
    user_present: bool = False
    attention_available: bool = False
    stale_after: timedelta = _DEFAULT_STALE_AFTER
    defer_seconds: int = _DEFAULT_DEFER


@dataclass(frozen=True)
class ContradictionDecision:
    """One bounded decision about one conflicting pair."""

    decision: str
    reason: str
    candidate_id: str
    incumbent_id: str
    claim_key: str
    defer_seconds: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "decision": self.decision,
            "reason": self.reason,
            "candidate_id": self.candidate_id,
            "incumbent_id": self.incumbent_id,
            "claim_key": self.claim_key,
        }
        if self.defer_seconds is not None:
            payload["defer_seconds"] = self.defer_seconds
        return payload


@dataclass
class IngestOutcome:
    """The result of offering one new record to the ledger."""

    record: dict[str, Any] | None = None
    decisions: list[ContradictionDecision] = field(default_factory=list)

    @property
    def status(self) -> str:
        """``deferred`` means nothing was written: the claim was not accepted."""

        return self.record["status"] if self.record else "deferred"

    @property
    def questions(self) -> list[ContradictionDecision]:
        return [item for item in self.decisions if item.decision == "ask"]


def _rank(record: dict[str, Any]) -> tuple[int, int]:
    return (
        AUTHORITY_RANK.get(record["authority"], 0),
        SOURCE_RANK.get(record["source_class"], 0),
    )


def detect_contradictions(
    candidate: dict[str, Any],
    records: Iterable[dict[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Find effective records that answer the candidate's claim differently.

    Same claim key, different content digest. Records that agree corroborate
    rather than conflict, and records that are superseded, retracted,
    tombstoned, expired, or not yet valid are history, not competition.
    """

    conflicts = [
        record
        for record in records
        if record["record_id"] != candidate["record_id"]
        and record["claim_key"] == candidate["claim_key"]
        and record["content_digest"] != candidate["content_digest"]
        and is_effective(record, now)
    ]
    return sorted(conflicts, key=lambda record: record["record_id"])


def _is_stale(record: dict[str, Any], context: PolicyContext) -> bool:
    observed = parse_timestamp(record["observed_at"], "record.observed_at")
    return context.now - observed > context.stale_after


def _ask_or_defer(
    candidate: dict[str, Any],
    incumbent: dict[str, Any],
    context: PolicyContext,
    reason: str,
) -> ContradictionDecision:
    if context.user_present and context.attention_available:
        return ContradictionDecision(
            decision="ask",
            reason=reason,
            candidate_id=candidate["record_id"],
            incumbent_id=incumbent["record_id"],
            claim_key=candidate["claim_key"],
        )
    return ContradictionDecision(
        decision="defer",
        reason="attention_unavailable" if context.user_present else "user_absent",
        candidate_id=candidate["record_id"],
        incumbent_id=incumbent["record_id"],
        claim_key=candidate["claim_key"],
        defer_seconds=context.defer_seconds,
    )


def decide_contradiction(
    candidate: dict[str, Any],
    incumbent: dict[str, Any],
    context: PolicyContext,
) -> ContradictionDecision:
    """Decide one conflict without ever letting the lower authority win."""

    def decision(name: str, reason: str) -> ContradictionDecision:
        return ContradictionDecision(
            decision=name,
            reason=reason,
            candidate_id=candidate["record_id"],
            incumbent_id=incumbent["record_id"],
            claim_key=candidate["claim_key"],
        )

    candidate_rank = _rank(candidate)
    incumbent_rank = _rank(incumbent)

    if candidate_rank < incumbent_rank:
        # The one case where a lower-authority claim changes anything: it can
        # ask for a fresh look at a stale one. It still cannot overwrite it.
        if (
            _is_stale(incumbent, context)
            and candidate["source_class"] in _DIRECT_OBSERVERS
        ):
            return decision("re_observe", "stale_incumbent")
        return decision("retract_candidate", "lower_authority")

    if candidate_rank > incumbent_rank:
        if candidate["record_type"] == incumbent["record_type"]:
            return decision("supersede", "higher_authority")
        return decision("retract_incumbent", "higher_authority")

    if candidate["source_id"] == incumbent["source_id"]:
        candidate_time = parse_timestamp(candidate["observed_at"])
        incumbent_time = parse_timestamp(incumbent["observed_at"])
        if candidate_time > incumbent_time:
            if candidate["record_type"] == incumbent["record_type"]:
                return decision("supersede", "same_source_update")
            return decision("retract_incumbent", "same_source_update")
        return decision("retract_candidate", "not_newer_than_incumbent")

    if (
        candidate["source_class"] in _DIRECT_OBSERVERS
        and incumbent["source_class"] in _DIRECT_OBSERVERS
    ):
        return decision("re_observe", "conflicting_observations")

    if candidate["record_type"] == "preference" and incumbent["record_type"] == (
        "preference"
    ):
        # Two people can hold different preferences about the same thing, and
        # neither is wrong. Keeping one hypothesis here would invent a winner.
        return decision("retain_both", "multiple_holders")

    return _ask_or_defer(candidate, incumbent, context, "unresolved_conflict")


def _evidence_is_available(
    candidate: dict[str, Any],
    index: dict[str, dict[str, Any]],
    context: PolicyContext,
) -> bool:
    for evidence_id in candidate["evidence_ids"]:
        evidence = index.get(evidence_id)
        if evidence is None:
            return False
        if evidence["status"] == "tombstoned":
            return False
        if evidence["status"] == "active" and not is_effective(evidence, context.now):
            return False
    return True


def apply_decisions(
    ledger: Any,
    candidate_id: str,
    decisions: Sequence[ContradictionDecision],
) -> None:
    """Carry out decisions on the ledger, appending never overwriting.

    Every outcome except supersession first links the two records, so the
    ledger shows what the losing claim was and what it lost to.

    All the links are made before any claim is withdrawn. Interleaving them
    would let a record lose its first conflict and then be unable to record its
    second, because a withdrawn claim may not enter a new contradiction.
    """

    # Supersession is expressed by the record itself at append time; nothing
    # further to do once the ledger holds the link.
    pairs = [
        item
        for item in decisions
        if item.decision != "supersede" and ledger.get(item.incumbent_id) is not None
    ]
    for item in pairs:
        ledger.contest(candidate_id, item.incumbent_id, reason=item.reason)
    for item in pairs:
        if item.decision == "retract_incumbent":
            ledger.retract(item.incumbent_id, reason=item.reason)
    losing = next(
        (item for item in pairs if item.decision == "retract_candidate"), None
    )
    if losing is not None:
        ledger.retract(candidate_id, reason=losing.reason)
    # retain_both, re_observe, ask, and defer all leave both claims standing
    # and visibly contested. The difference is what the system does next, not
    # what it currently believes.


def ingest_record(
    ledger: Any,
    *,
    context: PolicyContext,
    record_type: str,
    claim_key: str,
    source_class: str,
    source_id: str,
    authority: str,
    privacy_class: str,
    content_ref: str | None,
    content_digest: str,
    observed_at: str | None = None,
    valid_from: str | None = None,
    expires_at: str | None = None,
    confidence: float | None = None,
    evidence_ids: Sequence[str] | None = None,
    record_id: str | None = None,
    reason: str = "recorded",
) -> IngestOutcome:
    """Offer one claim to the ledger and let the policy decide its fate."""

    now_iso = context.now.isoformat()
    candidate_id = record_id or ledger.new_record_id()
    evidence = list(evidence_ids or [])
    index = {record["record_id"]: record for record in ledger.records()}

    provisional = build_provenance_record(
        record_id=candidate_id,
        record_type=record_type,
        claim_key=claim_key,
        source_class=source_class,
        source_id=source_id,
        authority=authority,
        observed_at=observed_at or now_iso,
        recorded_at=now_iso,
        valid_from=valid_from or now_iso,
        expires_at=expires_at,
        confidence=confidence,
        privacy_class=privacy_class,
        content_ref=content_ref,
        content_digest=content_digest,
        evidence_ids=evidence,
    )

    missing = [item for item in evidence if item not in index]
    if missing:
        raise ProvenanceValidationError(
            f"evidence {sorted(missing)} is not in the ledger"
        )
    if not _evidence_is_available(provisional, index, context):
        # Writing a belief whose support has been deleted or has expired would
        # create a claim that outlives its own reason for existing.
        return IngestOutcome(
            record=None,
            decisions=[
                ContradictionDecision(
                    decision="defer",
                    reason="evidence_unavailable",
                    candidate_id=candidate_id,
                    incumbent_id=sorted(evidence)[0] if evidence else candidate_id,
                    claim_key=claim_key,
                    defer_seconds=context.defer_seconds,
                )
            ],
        )

    conflicts = detect_contradictions(provisional, index.values(), now=context.now)
    decisions = [
        decide_contradiction(provisional, incumbent, context) for incumbent in conflicts
    ]

    candidate_loses = any(item.decision == "retract_candidate" for item in decisions)
    supersedes_id: str | None = None
    resolved: list[ContradictionDecision] = []
    for item in decisions:
        if item.decision != "supersede":
            resolved.append(item)
            continue
        if candidate_loses:
            # A claim that another record already outranks does not get to
            # replace a third one on the way down.
            resolved.append(
                ContradictionDecision(
                    decision="retain_both",
                    reason="candidate_outranked_elsewhere",
                    candidate_id=item.candidate_id,
                    incumbent_id=item.incumbent_id,
                    claim_key=item.claim_key,
                )
            )
        elif supersedes_id is None:
            supersedes_id = item.incumbent_id
            resolved.append(item)
        else:
            # A record can only have one successor, so the remaining losers are
            # retracted rather than silently dropped.
            resolved.append(
                ContradictionDecision(
                    decision="retract_incumbent",
                    reason=item.reason,
                    candidate_id=item.candidate_id,
                    incumbent_id=item.incumbent_id,
                    claim_key=item.claim_key,
                )
            )

    stored = ledger.append_record(
        record_id=candidate_id,
        record_type=record_type,
        claim_key=claim_key,
        source_class=source_class,
        source_id=source_id,
        authority=authority,
        privacy_class=privacy_class,
        content_ref=content_ref,
        content_digest=content_digest,
        observed_at=observed_at or now_iso,
        recorded_at=now_iso,
        valid_from=valid_from or now_iso,
        expires_at=expires_at,
        confidence=confidence,
        evidence_ids=evidence,
        supersedes_id=supersedes_id,
        reason=reason,
    )
    apply_decisions(ledger, candidate_id, resolved)
    return IngestOutcome(record=ledger.get(candidate_id) or stored, decisions=resolved)
