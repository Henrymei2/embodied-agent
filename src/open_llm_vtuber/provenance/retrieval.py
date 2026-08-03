"""Retrieving the least the agent needs, and nothing it cannot justify.

Two different failures are being avoided here. The first is stuffing a prompt
with everything remotely related, which costs latency and buries the one fact
that mattered. The second is quieter and worse: presenting an inference as if
it were something the user said. So retrieval is per-claim, capped, and refuses
any belief whose supporting evidence is gone.

Contested claims are not resolved on the way out. If the user said one thing
and a sensor reported another, both are returned marked contested, because a
retrieval layer that silently picks a winner would undo the whole point of the
contradiction policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

from .content_store import ContentDeletedError, PrivateContentStore
from .records import (
    AUTHORITY_RANK,
    PRIVACY_CLASSES,
    SOURCE_RANK,
    is_effective,
    parse_timestamp,
)

# Ordered from least to most sensitive; a retrieval ceiling admits everything up
# to and including its own level.
_PRIVACY_ORDER = ["public", "internal", "private", "restricted"]
_MAX_RECORDS = 32
_MAX_PER_CLAIM = 3


@dataclass(frozen=True)
class RetrievalRequest:
    """What the current intention needs — not everything that exists."""

    claim_keys: Sequence[str]
    now: datetime
    max_records: int = 8
    max_per_claim: int = 2
    privacy_ceiling: str = "private"


@dataclass(frozen=True)
class RetrievedClaim:
    """One claim the agent may currently rely on."""

    record_id: str
    claim_key: str
    record_type: str
    status: str
    source_class: str
    authority: str
    content_ref: str | None
    confidence: float | None
    calibrated_confidence: float | None
    evidence_ids: tuple[str, ...]

    @property
    def contested(self) -> bool:
        return self.status == "contested"


@dataclass
class RetrievedContext:
    """The retrieval result, including what was left out and why."""

    entries: list[RetrievedClaim] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False

    @property
    def content_refs(self) -> list[str]:
        return [entry.content_ref for entry in self.entries if entry.content_ref]

    @property
    def contested_claims(self) -> list[str]:
        return sorted({entry.claim_key for entry in self.entries if entry.contested})


def _rank(record: dict[str, Any]) -> tuple[int, int]:
    return (
        AUTHORITY_RANK.get(record["authority"], 0),
        SOURCE_RANK.get(record["source_class"], 0),
    )


def _privacy_allowed(record: dict[str, Any], ceiling: str) -> bool:
    if ceiling not in PRIVACY_CLASSES:
        return False
    return _PRIVACY_ORDER.index(record["privacy_class"]) <= _PRIVACY_ORDER.index(
        ceiling
    )


def _support_failure(
    record: dict[str, Any], index: dict[str, dict[str, Any]]
) -> str | None:
    """Why a record may not be relied on, or ``None`` if it may.

    A user's statement and a sensor reading are where evidence *comes from*;
    requiring them to cite something else would leave the agent unable to rely
    on anything. Everything the system concluded, intended, or reported has to
    still be able to point at what it concluded it from.
    """

    if record["record_type"] not in {"belief", "intention", "outcome"}:
        return None
    if not record["evidence_ids"]:
        return "no_supporting_evidence"
    for evidence_id in record["evidence_ids"]:
        evidence = index.get(evidence_id)
        if evidence is None:
            return "evidence_missing"
        if evidence["status"] == "tombstoned":
            return "evidence_deleted"
    return None


def retrieve_context(
    records: Iterable[dict[str, Any]],
    request: RetrievalRequest,
    *,
    calibrator: Any | None = None,
) -> RetrievedContext:
    """Select the smallest evidence-backed set of claims for an intention."""

    index = {record["record_id"]: record for record in records}
    max_records = max(1, min(int(request.max_records), _MAX_RECORDS))
    max_per_claim = max(1, min(int(request.max_per_claim), _MAX_PER_CLAIM))
    context = RetrievedContext()
    seen: set[str] = set()

    for claim_key in list(dict.fromkeys(request.claim_keys)):
        candidates = [
            record for record in index.values() if record["claim_key"] == claim_key
        ]
        usable: list[dict[str, Any]] = []
        for record in sorted(candidates, key=lambda item: item["record_id"]):
            if not is_effective(record, request.now):
                continue
            if not _privacy_allowed(record, request.privacy_ceiling):
                context.skipped.append(
                    {"record_id": record["record_id"], "reason": "privacy_ceiling"}
                )
                continue
            failure = _support_failure(record, index)
            if failure is not None:
                context.skipped.append(
                    {"record_id": record["record_id"], "reason": failure}
                )
                continue
            usable.append(record)

        usable.sort(
            key=lambda record: (
                _rank(record),
                record["confidence"] if record["confidence"] is not None else 0.0,
                parse_timestamp(record["recorded_at"]),
                record["record_id"],
            ),
            reverse=True,
        )
        # A settled claim needs one record. A contested one needs every side of
        # the disagreement, or the prompt would state a contested claim as fact.
        contested = [record for record in usable if record["status"] == "contested"]
        chosen = (contested or usable)[:max_per_claim]
        chosen_ids = {record["record_id"] for record in chosen}
        for record in usable:
            if record["record_id"] not in chosen_ids:
                context.skipped.append(
                    {
                        "record_id": record["record_id"],
                        "reason": "lower_ranked_duplicate",
                    }
                )

        for record in chosen:
            if record["record_id"] in seen:
                continue
            if len(context.entries) >= max_records:
                context.truncated = True
                break
            seen.add(record["record_id"])
            calibrated = None
            if calibrator is not None and record["confidence"] is not None:
                calibrated = calibrator.calibrate(
                    record["source_class"], float(record["confidence"])
                )
            context.entries.append(
                RetrievedClaim(
                    record_id=record["record_id"],
                    claim_key=claim_key,
                    record_type=record["record_type"],
                    status=record["status"],
                    source_class=record["source_class"],
                    authority=record["authority"],
                    content_ref=record["content_ref"],
                    confidence=record["confidence"],
                    calibrated_confidence=calibrated,
                    evidence_ids=tuple(record["evidence_ids"]),
                )
            )
        if context.truncated:
            break

    return context


def render_context_block(
    context: RetrievedContext,
    store: PrivateContentStore,
    *,
    max_chars_per_entry: int = 400,
) -> str:
    """Turn retrieved claims into prompt text by reading the content store.

    Content is fetched here and only here. A reference whose content has been
    deleted yields nothing at all — deletion holds even if a stale retrieval
    result is rendered afterwards.
    """

    lines: list[str] = []
    for entry in context.entries:
        if entry.content_ref is None:
            continue
        try:
            text = store.get_text(entry.content_ref)
        except (ContentDeletedError, KeyError, OSError):
            continue
        marker = f"{entry.record_type} via {entry.source_class}"
        if entry.contested:
            marker += ", contested"
        if entry.calibrated_confidence is not None:
            marker += f", calibrated {entry.calibrated_confidence:.2f}"
        elif entry.confidence is not None:
            marker += f", stated {entry.confidence:.2f} (uncalibrated)"
        lines.append(f"- [{marker}] {text.strip()[:max_chars_per_entry]}")

    if not lines:
        return ""
    header = ["The following is what you actually have evidence for:"]
    if context.contested_claims:
        header.append("Some of it is contested. Say so rather than choosing a side.")
    return "\n".join(header + lines)
