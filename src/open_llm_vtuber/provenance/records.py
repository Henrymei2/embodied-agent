"""Typed, content-referenced provenance records for belief-memory work.

The ledger stores identity, authority, evidence, lifecycle, and a digest. Raw
private content belongs in a separately governed store addressed by the opaque
``content_ref``; it is never embedded in these inspectable records.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta
from typing import Any, Iterable

# Version 2 adds the required ``claim_key``. Contradiction detection cannot work
# without a content-free way to say "these two records answer the same
# question", and no version-1 record ever reached durable storage, so the
# version moves rather than silently changing the meaning of a stored shape.
PROVENANCE_SCHEMA_VERSION = 2
RECORD_TYPES = frozenset(
    {"observation", "assertion", "belief", "preference", "intention", "outcome"}
)
# ``retracted`` is not ``tombstoned``: a claim can be withdrawn — because a
# higher-authority source contradicted it — while the content it referred to is
# still the user's and still there. Deleting content is a separate decision from
# no longer believing something.
RECORD_STATUSES = frozenset(
    {"active", "contested", "superseded", "retracted", "tombstoned"}
)
SOURCE_CLASSES = frozenset({"user", "sensor", "tool", "model", "system", "external"})
PRIVACY_CLASSES = frozenset({"public", "internal", "private", "restricted"})
AUTHORITIES = frozenset(
    {"observed", "reported", "inferred", "user_authoritative", "system_authoritative"}
)

_TYPE_SOURCES = {
    "observation": {"sensor", "tool", "system"},
    "assertion": {"user", "external"},
    "belief": {"model", "system"},
    "preference": {"user"},
    "intention": {"model", "system"},
    "outcome": {"sensor", "tool", "system"},
}
_TYPE_AUTHORITIES = {
    "observation": {"observed"},
    "assertion": {"reported"},
    "belief": {"inferred"},
    "preference": {"user_authoritative"},
    "intention": {"system_authoritative"},
    "outcome": {"observed", "system_authoritative"},
}
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_RECORD_KEYS = {
    "schema_version",
    "record_id",
    "record_type",
    "claim_key",
    "status",
    "source_class",
    "source_id",
    "authority",
    "observed_at",
    "recorded_at",
    "valid_from",
    "expires_at",
    "confidence",
    "privacy_class",
    "content_ref",
    "content_digest",
    "evidence_ids",
    "contradiction_ids",
    "supersedes_id",
}
_MAX_LINKS = 256

# A contradiction is resolved when the losing side stops being a live claim.
# Until then the surviving record must say so, rather than presenting itself as
# settled fact while an unreconciled peer is still in the ledger.
RESOLVED_STATUSES = frozenset({"superseded", "retracted", "tombstoned"})

# How much a source class is allowed to overrule another. A model that reports
# high confidence still cannot outrank what the user said about themselves;
# confidence is evidence about a claim, never a grant of authority.
AUTHORITY_RANK: dict[str, int] = {
    "reported": 3,
    "user_authoritative": 4,
    "observed": 3,
    "system_authoritative": 2,
    "inferred": 1,
}
SOURCE_RANK: dict[str, int] = {
    "user": 4,
    "sensor": 3,
    "tool": 3,
    "system": 2,
    "external": 2,
    "model": 1,
}


class ProvenanceValidationError(ValueError):
    """A provenance record or graph violates the typed ledger contract."""


def _fail(path: str, message: str) -> None:
    raise ProvenanceValidationError(f"{path}: {message}")


def _exact(value: dict[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        _fail(path, f"keys differ; missing={missing}, extra={extra}")


def _token(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        _fail(path, "must be a bounded opaque identifier")
    return value


def _timestamp(value: Any, path: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        _fail(path, "must be an RFC 3339 UTC timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ProvenanceValidationError(
            f"{path}: must be an RFC 3339 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _fail(path, "must include an explicit UTC offset")
    return parsed


def _links(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    if len(value) > _MAX_LINKS:
        _fail(path, f"cannot contain more than {_MAX_LINKS} links")
    links = [_token(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if len(links) != len(set(links)):
        _fail(path, "cannot contain duplicates")
    return links


def content_digest(content: str | bytes) -> str:
    """Digest private content without retaining it in the provenance ledger.

    An unkeyed digest of a short private value is guessable, so a store holding
    real user content should supply a keyed digest instead (see
    ``content_store.PrivateContentStore``). This plain form stays available for
    fixtures and for content that is not private.
    """

    if isinstance(content, str):
        raw = content.encode("utf-8")
    elif isinstance(content, bytes):
        raw = content
    else:
        raise TypeError("content must be str or bytes")
    return hashlib.sha256(raw).hexdigest()


def derive_claim_key(namespace: str, subject: str) -> str:
    """Build a claim key whose subject cannot be read back out of the ledger.

    Claim keys group the records that answer the same question, so they must be
    comparable, but the subject may itself be private ("the person named in the
    message"). Callers with a safe vocabulary can pass a readable key directly;
    callers holding private subjects use this instead.
    """

    if not isinstance(namespace, str) or not _TOKEN.fullmatch(namespace):
        raise ProvenanceValidationError("claim namespace must be a bounded identifier")
    if not isinstance(subject, str) or not subject:
        raise ProvenanceValidationError("claim subject must be a non-empty string")
    digest = hashlib.sha256(f"{namespace}\x00{subject}".encode("utf-8")).hexdigest()
    return f"{namespace}:{digest[:32]}"


def validate_provenance_record(record: dict[str, Any]) -> None:
    """Validate one record independently of its cross-record links."""

    if not isinstance(record, dict):
        _fail("record", "must be an object")
    _exact(record, _RECORD_KEYS, "record")
    if record.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        _fail(
            "record.schema_version",
            f"only schema version {PROVENANCE_SCHEMA_VERSION} is supported",
        )
    record_id = _token(record.get("record_id"), "record.record_id")
    record_type = record.get("record_type")
    if record_type not in RECORD_TYPES:
        _fail("record.record_type", f"must be one of {sorted(RECORD_TYPES)}")
    _token(record.get("claim_key"), "record.claim_key")
    status = record.get("status")
    if status not in RECORD_STATUSES:
        _fail("record.status", f"must be one of {sorted(RECORD_STATUSES)}")
    source_class = record.get("source_class")
    if source_class not in SOURCE_CLASSES:
        _fail("record.source_class", f"must be one of {sorted(SOURCE_CLASSES)}")
    if source_class not in _TYPE_SOURCES[record_type]:
        _fail(
            "record.source_class",
            f"source {source_class!r} cannot create a {record_type} record",
        )
    _token(record.get("source_id"), "record.source_id")
    authority = record.get("authority")
    if authority not in AUTHORITIES:
        _fail("record.authority", f"must be one of {sorted(AUTHORITIES)}")
    if authority not in _TYPE_AUTHORITIES[record_type]:
        _fail(
            "record.authority",
            f"authority {authority!r} is invalid for {record_type}",
        )

    observed = _timestamp(record.get("observed_at"), "record.observed_at")
    recorded = _timestamp(record.get("recorded_at"), "record.recorded_at")
    valid_from = _timestamp(record.get("valid_from"), "record.valid_from")
    expires = _timestamp(record.get("expires_at"), "record.expires_at", optional=True)
    if observed > recorded:
        _fail("record.recorded_at", "cannot precede observed_at")
    if expires is not None and expires <= valid_from:
        _fail("record.expires_at", "must follow valid_from")

    confidence = record.get("confidence")
    if confidence is not None:
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            _fail("record.confidence", "must be null or a finite value from 0 to 1")
    if record_type == "belief" and confidence is None:
        _fail("record.confidence", "is required for a belief")

    if record.get("privacy_class") not in PRIVACY_CLASSES:
        _fail(
            "record.privacy_class",
            f"must be one of {sorted(PRIVACY_CLASSES)}",
        )
    content_ref = record.get("content_ref")
    if status == "tombstoned":
        if content_ref is not None:
            _fail("record.content_ref", "must be null after tombstoning")
    else:
        _token(content_ref, "record.content_ref")
    digest = record.get("content_digest")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        _fail("record.content_digest", "must be a SHA-256 digest")

    evidence = _links(record.get("evidence_ids"), "record.evidence_ids")
    contradictions = _links(record.get("contradiction_ids"), "record.contradiction_ids")
    if record_id in evidence or record_id in contradictions:
        _fail("record", "cannot link to itself")
    if record_type in {"belief", "intention", "outcome"} and not evidence:
        _fail("record.evidence_ids", f"is required for a {record_type}")
    if status == "contested" and not contradictions:
        _fail("record.contradiction_ids", "is required for contested status")
    # Contradiction links survive the contradiction being resolved: dropping
    # them once a peer is superseded would erase the reason the surviving
    # record won. Whether an active record is *allowed* to carry links depends
    # on its peers' statuses, which only the graph can see.
    supersedes = record.get("supersedes_id")
    if supersedes is not None:
        _token(supersedes, "record.supersedes_id")
        if supersedes == record_id:
            _fail("record.supersedes_id", "cannot reference itself")


def build_provenance_record(
    *,
    record_id: str,
    record_type: str,
    claim_key: str,
    source_class: str,
    source_id: str,
    authority: str,
    observed_at: str,
    recorded_at: str,
    valid_from: str,
    privacy_class: str,
    content_ref: str | None,
    content_digest: str,
    evidence_ids: list[str] | None = None,
    contradiction_ids: list[str] | None = None,
    supersedes_id: str | None = None,
    expires_at: str | None = None,
    confidence: float | None = None,
    status: str = "active",
) -> dict[str, Any]:
    """Build a strict record while copying caller-owned link arrays."""

    record: dict[str, Any] = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "record_id": record_id,
        "record_type": record_type,
        "claim_key": claim_key,
        "status": status,
        "source_class": source_class,
        "source_id": source_id,
        "authority": authority,
        "observed_at": observed_at,
        "recorded_at": recorded_at,
        "valid_from": valid_from,
        "expires_at": expires_at,
        "confidence": confidence,
        "privacy_class": privacy_class,
        "content_ref": content_ref,
        "content_digest": content_digest,
        "evidence_ids": list(evidence_ids or []),
        "contradiction_ids": list(contradiction_ids or []),
        "supersedes_id": supersedes_id,
    }
    validate_provenance_record(record)
    return record


def parse_timestamp(value: Any, path: str = "timestamp") -> datetime:
    """Parse a record timestamp, rejecting anything that is not explicit UTC."""

    parsed = _timestamp(value, path)
    assert parsed is not None  # a non-optional parse either returns or raises
    return parsed


def is_effective(record: dict[str, Any], at: datetime) -> bool:
    """Whether a record still speaks for its claim at the given instant.

    Superseded, tombstoned, not-yet-valid, and expired records stay in the
    ledger as evidence of what was once believed; none of them may be presented
    to a prompt as something the agent currently holds true.
    """

    if record.get("status") not in {"active", "contested"}:
        return False
    if parse_timestamp(record["valid_from"], "record.valid_from") > at:
        return False
    expires_at = record.get("expires_at")
    if (
        expires_at is not None
        and parse_timestamp(expires_at, "record.expires_at") <= at
    ):
        return False
    return True


def _assert_acyclic(records: dict[str, dict[str, Any]], field: str) -> None:
    """Iterative depth-first search.

    A ledger accumulates long supersession and evidence chains, and a recursive
    walk would raise ``RecursionError`` on a graph that is perfectly valid —
    turning "this memory is deep" into "this memory is corrupt".
    """

    visited: set[str] = set()
    for start in records:
        if start in visited:
            continue
        on_path: set[str] = set()
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            record_id, leaving = stack.pop()
            if leaving:
                on_path.discard(record_id)
                visited.add(record_id)
                continue
            if record_id in visited:
                continue
            if record_id in on_path:
                _fail("records", f"cycle detected through {field} at {record_id!r}")
            on_path.add(record_id)
            stack.append((record_id, True))
            value = records[record_id][field]
            links = value if isinstance(value, list) else [value] if value else []
            for linked_id in links:
                if linked_id not in visited:
                    stack.append((linked_id, False))


def validate_provenance_graph(
    records: Iterable[dict[str, Any]], *, allow_empty: bool = False
) -> None:
    """Validate evidence, contradiction, chronology, and supersession links.

    Records are read but never written, so callers keep ownership of the
    objects they pass in.
    """

    indexed: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        validate_provenance_record(record)
        record_id = record["record_id"]
        if record_id in indexed:
            _fail(f"records[{index}].record_id", f"duplicates {record_id!r}")
        indexed[record_id] = record
    if not indexed and not allow_empty:
        _fail("records", "must not be empty")

    # Parsed once: re-parsing a neighbour's timestamp inside the link loops
    # made whole-graph validation quadratic in the size of the ledger.
    recorded_times = {
        record_id: _timestamp(record["recorded_at"], f"records.{record_id}.recorded_at")
        for record_id, record in indexed.items()
    }

    superseded_by: dict[str, str] = {}
    for record_id, record in indexed.items():
        recorded_at = recorded_times[record_id]
        for evidence_id in record["evidence_ids"]:
            if evidence_id not in indexed:
                _fail(
                    f"records.{record_id}.evidence_ids",
                    f"references missing record {evidence_id!r}",
                )
            if recorded_times[evidence_id] > recorded_at:
                _fail(
                    f"records.{record_id}.evidence_ids",
                    f"evidence {evidence_id!r} was recorded later",
                )
        for contradiction_id in record["contradiction_ids"]:
            if contradiction_id not in indexed:
                _fail(
                    f"records.{record_id}.contradiction_ids",
                    f"references missing record {contradiction_id!r}",
                )
            if record_id not in indexed[contradiction_id]["contradiction_ids"]:
                _fail(
                    f"records.{record_id}.contradiction_ids",
                    f"contradiction with {contradiction_id!r} must be symmetric",
                )
            if (
                record["status"] == "active"
                and indexed[contradiction_id]["status"] not in RESOLVED_STATUSES
            ):
                _fail(
                    f"records.{record_id}.status",
                    f"must be contested while {contradiction_id!r} is unresolved",
                )
        supersedes_id = record["supersedes_id"]
        if supersedes_id is not None:
            if supersedes_id not in indexed:
                _fail(
                    f"records.{record_id}.supersedes_id",
                    f"references missing record {supersedes_id!r}",
                )
            prior = indexed[supersedes_id]
            if prior["record_type"] != record["record_type"]:
                _fail(
                    f"records.{record_id}.supersedes_id",
                    "must reference the same record type",
                )
            if prior["claim_key"] != record["claim_key"]:
                _fail(
                    f"records.{record_id}.supersedes_id",
                    "must answer the same claim as the superseded record",
                )
            if prior["status"] != "superseded":
                _fail(
                    f"records.{supersedes_id}.status",
                    "must be superseded when a successor references it",
                )
            if supersedes_id in superseded_by:
                _fail(
                    f"records.{record_id}.supersedes_id",
                    f"record already superseded by {superseded_by[supersedes_id]!r}",
                )
            superseded_by[supersedes_id] = record_id
            prior_time = recorded_times[supersedes_id]
            if prior_time >= recorded_at:
                _fail(
                    f"records.{record_id}.supersedes_id",
                    "successor must be recorded later than the superseded record",
                )

    orphaned = sorted(
        record_id
        for record_id, record in indexed.items()
        if record["status"] == "superseded" and record_id not in superseded_by
    )
    if orphaned:
        _fail("records", f"superseded records lack successors: {orphaned}")
    _assert_acyclic(indexed, "evidence_ids")
    _assert_acyclic(indexed, "supersedes_id")
