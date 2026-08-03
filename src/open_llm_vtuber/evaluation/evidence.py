"""Versioned, privacy-safe evidence contracts for deterministic evaluation.

Run manifests make results interpretable. Event envelopes make causal traces
tamper-evident without storing conversation, prompt, file, or screen content.
This module validates data only; adapters that record and replay real system
events are deliberately layered on top of these contracts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

from .research_contract import load_research_contract, validate_research_contract

EVIDENCE_SCHEMA_VERSION = 1
RUN_STATUSES = frozenset({"running", "completed", "failed"})
SCENARIO_SPLITS = frozenset({"development", "validation", "held_out", "pilot", "study"})
EVENT_CATEGORIES = frozenset(
    {
        "observation",
        "belief",
        "plan",
        "authorization",
        "action",
        "outcome",
        "recovery",
        "system",
        "metric",
    }
)

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_PAYLOAD_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_CODE_REVISION = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
_MANIFEST_KEYS = {"schema_version", "status"}
_PLATFORM_KEYS = {"system", "release", "machine", "python"}
_EVENT_KEYS = {
    "schema_version",
    "event_id",
    "run_id",
    "sequence",
    "timestamp",
    "category",
    "event_type",
    "correlation_id",
    "causation_id",
    "payload",
    "previous_event_hash",
    "event_hash",
}
_MAX_PAYLOAD_FIELDS = 64
_MAX_PAYLOAD_LIST_ITEMS = 64


class EvidenceValidationError(ValueError):
    """A run manifest or event envelope is malformed or unsafe to share."""


def _fail(path: str, message: str) -> None:
    raise EvidenceValidationError(f"{path}: {message}")


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        _fail(path, f"keys differ; missing={missing}, extra={extra}")


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        _fail(path, "must be a bounded content-free identifier")
    return value


def _digest(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _fail(path, "must be a lowercase SHA-256 digest")
    return value


def _timestamp(value: Any, path: str) -> datetime:
    if not isinstance(value, str) or not value:
        _fail(path, "must be an RFC 3339 UTC timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EvidenceValidationError(
            f"{path}: must be an RFC 3339 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail(path, "must include an explicit UTC offset")
    return parsed


def _format_timestamp(value: str | datetime, path: str) -> str:
    if isinstance(value, str):
        _timestamp(value, path)
        return value
    if not isinstance(value, datetime):
        _fail(path, "must be a datetime or RFC 3339 UTC timestamp")
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        _fail(path, "must include an explicit UTC offset")
    return value.isoformat(timespec="milliseconds")


def canonical_json(value: Any) -> str:
    """Serialize a JSON value deterministically, rejecting non-finite numbers."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError(f"value is not canonical JSON: {exc}") from exc


def canonical_digest(value: Any) -> str:
    """Return the SHA-256 digest of a canonical JSON value."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validated_contract(contract: dict[str, Any] | None) -> dict[str, Any]:
    if contract is None:
        return load_research_contract()
    validate_research_contract(contract)
    return contract


def _identifier_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(path, "must be a non-empty array")
    result = [_identifier(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        _fail(path, "must not contain duplicates")
    return result


def validate_run_manifest(
    manifest: dict[str, Any],
    *,
    contract: dict[str, Any] | None = None,
) -> None:
    """Validate a complete or in-progress evaluation run manifest."""

    manifest = _mapping(manifest, "manifest")
    research = _validated_contract(contract)
    expected = _MANIFEST_KEYS | set(
        research["reproducibility"]["required_manifest_fields"]
    )
    _exact_keys(manifest, expected, "manifest")
    if manifest.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        _fail("manifest.schema_version", "only schema version 1 is supported")

    status = manifest.get("status")
    if status not in RUN_STATUSES:
        _fail("manifest.status", f"must be one of {sorted(RUN_STATUSES)}")
    _identifier(manifest.get("run_id"), "manifest.run_id")
    code_revision = manifest.get("code_revision")
    if not isinstance(code_revision, str) or not _CODE_REVISION.fullmatch(
        code_revision
    ):
        _fail("manifest.code_revision", "must be a lowercase Git object id")
    if not isinstance(manifest.get("worktree_dirty"), bool):
        _fail("manifest.worktree_dirty", "must be a boolean")
    if manifest.get("contract_revision") != research["contract_revision"]:
        _fail(
            "manifest.contract_revision",
            f"must equal the validated contract revision {research['contract_revision']}",
        )

    condition_id = manifest.get("condition_id")
    known_conditions = {item["id"] for item in research["conditions"]}
    if condition_id not in known_conditions:
        _fail("manifest.condition_id", f"must be one of {sorted(known_conditions)}")
    _identifier(manifest.get("scenario_set_revision"), "manifest.scenario_set_revision")
    if manifest.get("scenario_split") not in SCENARIO_SPLITS:
        _fail(
            "manifest.scenario_split",
            f"must be one of {sorted(SCENARIO_SPLITS)}",
        )
    _digest(manifest.get("fixture_digest"), "manifest.fixture_digest")
    _digest(manifest.get("configuration_digest"), "manifest.configuration_digest")
    _identifier_list(manifest.get("model_identifiers"), "manifest.model_identifiers")
    _identifier_list(manifest.get("engine_identifiers"), "manifest.engine_identifiers")

    platform = _mapping(manifest.get("platform"), "manifest.platform")
    _exact_keys(platform, _PLATFORM_KEYS, "manifest.platform")
    for key in sorted(_PLATFORM_KEYS):
        _identifier(platform.get(key), f"manifest.platform.{key}")

    seed = manifest.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**63:
        _fail("manifest.seed", "must be a non-negative signed 64-bit integer")
    started_at = _timestamp(manifest.get("started_at"), "manifest.started_at")
    finished_at = manifest.get("finished_at")
    if status == "running":
        if finished_at is not None:
            _fail("manifest.finished_at", "must be null while status is running")
    else:
        finished = _timestamp(finished_at, "manifest.finished_at")
        if finished < started_at:
            _fail("manifest.finished_at", "cannot precede started_at")


def build_run_manifest(
    *,
    run_id: str,
    code_revision: str,
    worktree_dirty: bool,
    contract_revision: str,
    condition_id: str,
    scenario_set_revision: str,
    scenario_split: str,
    fixture_digest: str,
    configuration_digest: str,
    model_identifiers: list[str],
    engine_identifiers: list[str],
    platform: dict[str, str],
    seed: int,
    started_at: str | datetime,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate an in-progress run manifest."""

    manifest: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "status": "running",
        "run_id": run_id,
        "code_revision": code_revision,
        "worktree_dirty": worktree_dirty,
        "contract_revision": contract_revision,
        "condition_id": condition_id,
        "scenario_set_revision": scenario_set_revision,
        "scenario_split": scenario_split,
        "fixture_digest": fixture_digest,
        "configuration_digest": configuration_digest,
        "model_identifiers": list(model_identifiers),
        "engine_identifiers": list(engine_identifiers),
        "platform": dict(platform),
        "seed": seed,
        "started_at": _format_timestamp(started_at, "manifest.started_at"),
        "finished_at": None,
    }
    validate_run_manifest(manifest, contract=contract)
    return manifest


def finish_run_manifest(
    manifest: dict[str, Any],
    *,
    status: str,
    finished_at: str | datetime,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a validated terminal copy of a running manifest."""

    if status not in {"completed", "failed"}:
        _fail("manifest.status", "terminal status must be completed or failed")
    validate_run_manifest(manifest, contract=contract)
    if manifest["status"] != "running":
        _fail("manifest.status", "only a running manifest can be finished")
    result = deepcopy(manifest)
    result["status"] = status
    result["finished_at"] = _format_timestamp(finished_at, "manifest.finished_at")
    validate_run_manifest(result, contract=contract)
    return result


def _normalized_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _is_forbidden_field(field: str, forbidden_fields: set[str]) -> bool:
    normalized = f"_{_normalized_field_name(field)}_"
    return any(
        f"_{_normalized_field_name(item)}_" in normalized for item in forbidden_fields
    )


def _payload_scalar(value: Any, path: str, max_chars: int) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            _fail(path, "must be a signed 64-bit integer")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail(path, "must be a finite number")
        return
    if isinstance(value, str):
        if len(value) > max_chars or not _SAFE_IDENTIFIER.fullmatch(value):
            _fail(path, "must be a bounded content-free identifier")
        return
    _fail(path, "must be a JSON scalar or an array of JSON scalars")


def _validate_payload(payload: Any, research: dict[str, Any]) -> dict[str, Any]:
    payload = _mapping(payload, "event.payload")
    if len(payload) > _MAX_PAYLOAD_FIELDS:
        _fail("event.payload", f"must contain at most {_MAX_PAYLOAD_FIELDS} fields")
    forbidden = set(research["privacy"]["forbidden_fields"])
    max_chars = int(research["privacy"]["max_shareable_string_chars"])
    for key, value in payload.items():
        if not isinstance(key, str) or not _PAYLOAD_KEY.fullmatch(key):
            _fail(f"event.payload.{key}", "field name is not a supported identifier")
        if _is_forbidden_field(key, forbidden):
            _fail(f"event.payload.{key}", "field is forbidden by the privacy contract")
        if isinstance(value, list):
            if len(value) > _MAX_PAYLOAD_LIST_ITEMS:
                _fail(
                    f"event.payload.{key}",
                    f"must contain at most {_MAX_PAYLOAD_LIST_ITEMS} values",
                )
            for index, item in enumerate(value):
                _payload_scalar(item, f"event.payload.{key}[{index}]", max_chars)
        else:
            _payload_scalar(value, f"event.payload.{key}", max_chars)
    return payload


def _event_hash(event: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "event_hash"}
    return canonical_digest(unsigned)


def validate_event_envelope(
    event: dict[str, Any],
    *,
    contract: dict[str, Any] | None = None,
) -> None:
    """Validate one content-free, integrity-chained evaluation event."""

    event = _mapping(event, "event")
    _exact_keys(event, _EVENT_KEYS, "event")
    research = _validated_contract(contract)
    if event.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        _fail("event.schema_version", "only schema version 1 is supported")
    _identifier(event.get("event_id"), "event.event_id")
    _identifier(event.get("run_id"), "event.run_id")
    sequence = event.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        _fail("event.sequence", "must be a non-negative integer")
    _timestamp(event.get("timestamp"), "event.timestamp")
    if event.get("category") not in EVENT_CATEGORIES:
        _fail("event.category", f"must be one of {sorted(EVENT_CATEGORIES)}")
    _identifier(event.get("event_type"), "event.event_type")
    _identifier(event.get("correlation_id"), "event.correlation_id")
    causation_id = event.get("causation_id")
    if causation_id is not None:
        _identifier(causation_id, "event.causation_id")
    _validate_payload(event.get("payload"), research)
    previous_hash = event.get("previous_event_hash")
    if sequence == 0:
        if previous_hash is not None:
            _fail("event.previous_event_hash", "must be null for sequence zero")
    else:
        _digest(previous_hash, "event.previous_event_hash")
    supplied_hash = _digest(event.get("event_hash"), "event.event_hash")
    expected_hash = _event_hash(event)
    if not hmac.compare_digest(supplied_hash, expected_hash):
        _fail("event.event_hash", "does not match the canonical event content")


def build_event_envelope(
    *,
    event_id: str,
    run_id: str,
    sequence: int,
    timestamp: str | datetime,
    category: str,
    event_type: str,
    correlation_id: str,
    payload: dict[str, Any],
    causation_id: str | None = None,
    previous_event_hash: str | None = None,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate one deterministic evidence event."""

    research = _validated_contract(contract)
    safe_payload = deepcopy(payload)
    _validate_payload(safe_payload, research)
    event: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "event_id": event_id,
        "run_id": run_id,
        "sequence": sequence,
        "timestamp": _format_timestamp(timestamp, "event.timestamp"),
        "category": category,
        "event_type": event_type,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "payload": safe_payload,
        "previous_event_hash": previous_event_hash,
    }
    event["event_hash"] = _event_hash(event)
    validate_event_envelope(event, contract=research)
    return event


def validate_event_chain(
    events: Iterable[dict[str, Any]],
    *,
    manifest: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> None:
    """Detect missing, duplicated, reordered, tampered, or miscaused events."""

    research = _validated_contract(contract)
    if manifest is not None:
        validate_run_manifest(manifest, contract=research)
    expected_run_id = manifest["run_id"] if manifest is not None else None
    seen_ids: set[str] = set()
    previous_hash: str | None = None
    chain_run_id: str | None = expected_run_id
    for expected_sequence, event in enumerate(events):
        validate_event_envelope(event, contract=research)
        if event["sequence"] != expected_sequence:
            _fail(
                f"events[{expected_sequence}].sequence",
                f"expected {expected_sequence}, got {event['sequence']}",
            )
        if chain_run_id is None:
            chain_run_id = event["run_id"]
        elif event["run_id"] != chain_run_id:
            _fail(f"events[{expected_sequence}].run_id", "does not match the chain run")
        if event["event_id"] in seen_ids:
            _fail(f"events[{expected_sequence}].event_id", "is duplicated")
        if event["previous_event_hash"] != previous_hash:
            _fail(
                f"events[{expected_sequence}].previous_event_hash",
                "does not link to the preceding event",
            )
        causation_id = event["causation_id"]
        if causation_id is not None and causation_id not in seen_ids:
            _fail(
                f"events[{expected_sequence}].causation_id",
                "must reference an earlier event in the chain",
            )
        seen_ids.add(event["event_id"])
        previous_hash = event["event_hash"]
