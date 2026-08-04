"""Assembling the causal chain behind one thing the agent did.

The ledger already stores everything needed to answer "why did you do that?" —
what was observed, what was believed and on whose authority, what was planned,
what was authorized and on what grounds, what actually happened. It stores it
as a graph of typed records linked by evidence, which is right for replay and
useless for reading. Nobody can look at a hash-chained JSONL file and see a
reason.

This walks that graph backwards from any record and returns the chain in the
order a person would tell the story: observation first, outcome last. It is the
one place in the system where the invisible work becomes visible, which is
exactly why it exists — a companion that can explain itself only in principle
is a companion that cannot explain itself.

Two properties it must keep:

**No private content.** Records hold opaque references and digests, never the
text. The chain inherits that, so it is safe to put on a projector in front of
an audience — which is the whole point of building it for a live demo.

**Honest about what is missing.** A belief whose evidence was deleted shows a
gap rather than a plausible-looking chain with a link quietly dropped. The
inspector's value is that it can be trusted when it says "this is why".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# Where each record type sits in the story, earliest first. A chain is sorted
# by this rather than by timestamp: two records written in the same millisecond
# still have a causal order, and the audience needs the causal one.
STAGE_ORDER: tuple[str, ...] = (
    "observation",
    "assertion",
    "belief",
    "preference",
    "intention",
    "outcome",
)

_STAGE_LABEL = {
    "observation": "Observed",
    "assertion": "Was told",
    "belief": "Believed",
    "preference": "Preference",
    "intention": "Planned",
    "outcome": "Result",
}

MAX_CHAIN_RECORDS = 200


@dataclass(frozen=True)
class ChainLink:
    """One step in the story, with nothing private in it."""

    record_id: str
    record_type: str
    stage_label: str
    claim_key: str
    source_class: str
    source_id: str
    authority: str
    status: str
    confidence: float | None
    recorded_at: str
    evidence_ids: tuple[str, ...]
    content_digest: str
    missing_evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "record_type": self.record_type,
            "stage": self.stage_label,
            "claim_key": self.claim_key,
            "source_class": self.source_class,
            "source_id": self.source_id,
            "authority": self.authority,
            "status": self.status,
            "confidence": self.confidence,
            "recorded_at": self.recorded_at,
            "evidence_ids": list(self.evidence_ids),
            "content_digest": self.content_digest[:16],
            "missing_evidence": list(self.missing_evidence),
        }


@dataclass(frozen=True)
class CausalChain:
    """Everything that led to one record, ordered as a story."""

    root_id: str
    links: tuple[ChainLink, ...]
    truncated: bool = False

    @property
    def complete(self) -> bool:
        """Whether every cited piece of evidence was actually found."""
        return not any(link.missing_evidence for link in self.links)

    def as_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "complete": self.complete,
            "truncated": self.truncated,
            "links": [link.as_dict() for link in self.links],
        }


def _index(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        r["record_id"]: r for r in records if isinstance(r, dict) and r.get("record_id")
    }


def _link(record: dict[str, Any], known: set[str]) -> ChainLink:
    cited = tuple(record.get("evidence_ids") or ())
    return ChainLink(
        record_id=record["record_id"],
        record_type=record.get("record_type", "unknown"),
        stage_label=_STAGE_LABEL.get(record.get("record_type", ""), "Step"),
        claim_key=record.get("claim_key", ""),
        source_class=record.get("source_class", "unknown"),
        source_id=record.get("source_id", ""),
        authority=record.get("authority", "unknown"),
        status=record.get("status", "active"),
        confidence=record.get("confidence"),
        recorded_at=record.get("recorded_at", ""),
        evidence_ids=cited,
        content_digest=str(record.get("content_digest", "")),
        # A deleted or expired piece of evidence leaves a hole, and the hole is
        # reported. Dropping it silently would produce a chain that looks whole
        # and is not, which is the one failure that would make this untrustworthy.
        missing_evidence=tuple(ref for ref in cited if ref not in known),
    )


def build_chain(
    records: Iterable[dict[str, Any]],
    root_id: str,
    *,
    limit: int = MAX_CHAIN_RECORDS,
) -> CausalChain:
    """Walk evidence links back from ``root_id`` and order the result as a story."""
    index = _index(records)
    if root_id not in index:
        raise KeyError(f"no such record: {root_id}")

    known = set(index)
    collected: dict[str, dict[str, Any]] = {}
    queue = [root_id]
    truncated = False
    while queue:
        current = queue.pop()
        if current in collected:
            continue
        record = index.get(current)
        if record is None:
            continue
        if len(collected) >= limit:
            # Bounded, because a long-lived ledger is a graph and a projector
            # is not. Truncation is reported rather than hidden.
            truncated = True
            break
        collected[current] = record
        queue.extend(record.get("evidence_ids") or ())

    def sort_key(record: dict[str, Any]) -> tuple[int, str]:
        record_type = record.get("record_type", "")
        position = (
            STAGE_ORDER.index(record_type)
            if record_type in STAGE_ORDER
            else len(STAGE_ORDER)
        )
        return position, str(record.get("recorded_at", ""))

    ordered = sorted(collected.values(), key=sort_key)
    return CausalChain(
        root_id=root_id,
        links=tuple(_link(record, known) for record in ordered),
        truncated=truncated,
    )


def recent_roots(
    records: Iterable[dict[str, Any]], *, limit: int = 25
) -> tuple[dict[str, Any], ...]:
    """The most recent records worth asking "why?" about.

    Outcomes and intentions first: those are the things the agent *did*, which
    is what an audience points at. A raw observation is a leaf, not a decision.
    """
    interesting = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("record_type") in {"outcome", "intention", "belief"}
    ]
    interesting.sort(key=lambda r: str(r.get("recorded_at", "")), reverse=True)
    return tuple(
        {
            "record_id": record["record_id"],
            "record_type": record.get("record_type"),
            "stage": _STAGE_LABEL.get(record.get("record_type", ""), "Step"),
            "claim_key": record.get("claim_key", ""),
            "recorded_at": record.get("recorded_at", ""),
            "status": record.get("status", "active"),
        }
        for record in interesting[:limit]
        if record.get("record_id")
    )
