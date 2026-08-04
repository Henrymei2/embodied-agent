"""Versioned, content-minimal contracts for consequential agent actions.

The descriptor says what an action proposes. Authority is deliberately absent
from the proposal: only :class:`AuthorizationContext` carries trusted runtime
grants, so text recovered from a page, email, tool result, or model output can
never authorize itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal

ACTION_DESCRIPTOR_SCHEMA_VERSION = 2
AUTHORIZATION_POLICY_VERSION = "2026-08-04.1"

# Where the belief behind an action came from. Mirrors the provenance source
# classes exactly, so the authorization kernel can look up *measured* per-class
# reliability without a translation layer that could drift from the ledger.
#
# Version 2 exists for this field. Part 3 has measured since its completion that
# the `model` class states 0.73 and is right 47% of the time, and until now
# nothing consumed that: the kernel compared a model's self-reported number
# against a fixed threshold, which is the one thing Part 3's own docstring says
# not to do. A descriptor carrying no source class defaults to `model` — the
# least-trusted measured class — because an action whose provenance nobody
# stated should not be treated better than one whose provenance is a guess.
EVIDENCE_SOURCE_CLASSES = frozenset(
    {"user", "sensor", "tool", "model", "agent_hypothesis", "external"}
)

ACTORS = frozenset({"user", "agent", "system", "external"})
SIDE_EFFECT_CLASSES = frozenset(
    {"none", "local_state", "external_state", "communication", "physical"}
)
REVERSIBILITY_CLASSES = frozenset(
    {"reversible", "compensatable", "irreversible", "unknown"}
)
AUTHORITY_LEVELS = ("none", "session", "explicit_confirmation")
PRIVACY_CLASSES = ("public", "internal", "private", "restricted")
EXECUTION_MODES = frozenset({"preview", "commit"})

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_MAX_REFS = 64


class AuthorizationValidationError(ValueError):
    """An action or trusted policy input violates the closed contract."""


def _utc(value: datetime | None = None) -> datetime:
    candidate = value or datetime.now(timezone.utc)
    if candidate.tzinfo is None:
        raise AuthorizationValidationError("timestamps require an explicit offset")
    return candidate.astimezone(timezone.utc)


def _timestamp(value: str, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise AuthorizationValidationError(f"{field_name} must be a UTC timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AuthorizationValidationError(
            f"{field_name} must be a UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise AuthorizationValidationError(
            f"{field_name} must have an explicit UTC offset"
        )
    return parsed


def _token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise AuthorizationValidationError(
            f"{field_name} must be a bounded opaque identifier"
        )
    return value


def _bounded(value: str, field_name: str, *, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise AuthorizationValidationError(
            f"{field_name} must contain 1 to {limit} characters"
        )
    return value


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise AuthorizationValidationError(
            f"action data is not canonical JSON: {exc}"
        ) from exc


def hash_arguments(arguments: dict[str, Any]) -> str:
    """Digest private arguments without copying them into the policy record."""

    if not isinstance(arguments, dict):
        raise AuthorizationValidationError("action arguments must be an object")
    return hashlib.sha256(_canonical(arguments).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActionDescriptor:
    action_id: str
    actor: Literal["user", "agent", "system", "external"]
    capability: str
    target_ref: str
    arguments_digest: str
    side_effect_class: Literal[
        "none", "local_state", "external_state", "communication", "physical"
    ]
    reversibility: Literal[
        "reversible", "compensatable", "irreversible", "unknown"
    ]
    confidence: float
    evidence_refs: tuple[str, ...]
    required_user_authority: Literal["none", "session", "explicit_confirmation"]
    spatial_requirement: str | None
    idempotency_key: str
    privacy_class: Literal["public", "internal", "private", "restricted"]
    external_destination: str | None = None
    evidence_source_class: str = "model"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: int = ACTION_DESCRIPTOR_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != ACTION_DESCRIPTOR_SCHEMA_VERSION:
            raise AuthorizationValidationError(
                "unsupported action descriptor schema version"
            )
        _token(self.action_id, "action.action_id")
        if self.actor not in ACTORS:
            raise AuthorizationValidationError("action actor is invalid")
        _token(self.capability, "action.capability")
        _bounded(self.target_ref, "action.target_ref", limit=500)
        if not _DIGEST.fullmatch(self.arguments_digest):
            raise AuthorizationValidationError(
                "action.arguments_digest must be SHA-256"
            )
        if self.side_effect_class not in SIDE_EFFECT_CLASSES:
            raise AuthorizationValidationError("action side-effect class is invalid")
        if self.reversibility not in REVERSIBILITY_CLASSES:
            raise AuthorizationValidationError("action reversibility is invalid")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(float(self.confidence))
            or not 0 <= float(self.confidence) <= 1
        ):
            raise AuthorizationValidationError(
                "action confidence must be finite and between 0 and 1"
            )
        if not isinstance(self.evidence_refs, tuple) or len(
            self.evidence_refs
        ) > _MAX_REFS:
            raise AuthorizationValidationError(
                f"action evidence_refs must contain at most {_MAX_REFS} references"
            )
        for reference in self.evidence_refs:
            _token(reference, "action.evidence_refs")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise AuthorizationValidationError(
                "action evidence_refs cannot contain duplicates"
            )
        if self.required_user_authority not in AUTHORITY_LEVELS:
            raise AuthorizationValidationError("action authority requirement is invalid")
        if self.spatial_requirement is not None:
            _bounded(
                self.spatial_requirement,
                "action.spatial_requirement",
                limit=200,
            )
        _token(self.idempotency_key, "action.idempotency_key")
        if self.privacy_class not in PRIVACY_CLASSES:
            raise AuthorizationValidationError("action privacy class is invalid")
        if self.evidence_source_class not in EVIDENCE_SOURCE_CLASSES:
            raise AuthorizationValidationError(
                "action evidence source class is invalid"
            )
        if self.external_destination is not None:
            _bounded(
                self.external_destination,
                "action.external_destination",
                limit=500,
            )
        if self.side_effect_class == "none" and self.reversibility != "reversible":
            raise AuthorizationValidationError(
                "read-only actions must use reversible semantics"
            )
        if self.side_effect_class in {"external_state", "communication"}:
            if self.external_destination is None:
                raise AuthorizationValidationError(
                    "external actions require an explicit destination"
                )
        elif self.external_destination is not None:
            raise AuthorizationValidationError(
                "only external actions may name an external destination"
            )
        _timestamp(self.created_at, "action.created_at")

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(_canonical(asdict(self)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UserConfirmation:
    confirmation_id: str
    descriptor_digest: str
    issued_by: Literal["user"]
    issued_at: str
    expires_at: str

    def validate(self) -> None:
        _token(self.confirmation_id, "confirmation.confirmation_id")
        if not _DIGEST.fullmatch(self.descriptor_digest):
            raise AuthorizationValidationError(
                "confirmation descriptor digest must be SHA-256"
            )
        if self.issued_by != "user":
            raise AuthorizationValidationError(
                "only a trusted user channel can issue confirmation"
            )
        issued = _timestamp(self.issued_at, "confirmation.issued_at")
        expires = _timestamp(self.expires_at, "confirmation.expires_at")
        if expires <= issued:
            raise AuthorizationValidationError(
                "confirmation expiry must follow its issue time"
            )

    def authorizes(self, descriptor_digest: str, *, at: str) -> bool:
        """Return whether this trusted receipt binds this action at this time."""

        self.validate()
        moment = _timestamp(at, "confirmation.evaluation_time")
        return (
            self.descriptor_digest == descriptor_digest
            and _timestamp(self.issued_at, "confirmation.issued_at")
            <= moment
            < _timestamp(self.expires_at, "confirmation.expires_at")
        )


@dataclass(frozen=True)
class AuthorizationContext:
    requesting_actor: str
    granted_capabilities: frozenset[str]
    trusted_evidence_refs: frozenset[str]
    reachable_spatial_targets: frozenset[str]
    session_authorities: frozenset[str]
    presence_allowed: bool
    quota_remaining: int
    privacy_ceiling: Literal["public", "internal", "private", "restricted"]
    allowed_external_destinations: frozenset[str]
    execution_mode: Literal["preview", "commit"] = "commit"
    confirmation: UserConfirmation | None = None
    evaluated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def validate(self) -> None:
        _token(self.requesting_actor, "context.requesting_actor")
        for name, values in (
            ("granted_capabilities", self.granted_capabilities),
            ("trusted_evidence_refs", self.trusted_evidence_refs),
            ("session_authorities", self.session_authorities),
        ):
            if not isinstance(values, frozenset) or len(values) > 1024:
                raise AuthorizationValidationError(
                    f"context {name} must be a bounded frozen set"
                )
            for value in values:
                _token(value, f"context.{name}")
        for name, values in (
            ("reachable_spatial_targets", self.reachable_spatial_targets),
            ("allowed_external_destinations", self.allowed_external_destinations),
        ):
            if not isinstance(values, frozenset) or len(values) > 1024:
                raise AuthorizationValidationError(
                    f"context {name} must be a bounded frozen set"
                )
            for value in values:
                _bounded(value, f"context.{name}", limit=500)
        if not isinstance(self.presence_allowed, bool):
            raise AuthorizationValidationError("context presence decision is invalid")
        if isinstance(self.quota_remaining, bool) or not isinstance(
            self.quota_remaining, int
        ):
            raise AuthorizationValidationError("context quota must be an integer")
        if self.quota_remaining < 0:
            raise AuthorizationValidationError("context quota cannot be negative")
        if self.privacy_ceiling not in PRIVACY_CLASSES:
            raise AuthorizationValidationError("context privacy ceiling is invalid")
        if self.execution_mode not in EXECUTION_MODES:
            raise AuthorizationValidationError("context execution mode is invalid")
        if self.confirmation is not None:
            self.confirmation.validate()
        _timestamp(self.evaluated_at, "context.evaluated_at")


@dataclass(frozen=True)
class PolicyCheck:
    check: str
    passed: bool
    reason_code: str

    def validate(self) -> None:
        _token(self.check, "policy_check.check")
        if not isinstance(self.passed, bool):
            raise AuthorizationValidationError("policy check status must be boolean")
        _token(self.reason_code, "policy_check.reason_code")


@dataclass(frozen=True)
class AuthorizationDecision:
    decision_id: str
    action_id: str
    descriptor_digest: str
    outcome: Literal["authorized", "preview_only", "denied"]
    checks: tuple[PolicyCheck, ...]
    decided_at: str
    policy_version: str = AUTHORIZATION_POLICY_VERSION

    @property
    def may_commit(self) -> bool:
        return self.outcome == "authorized"

    def validate(self) -> None:
        _token(self.decision_id, "decision.decision_id")
        _token(self.action_id, "decision.action_id")
        if not _DIGEST.fullmatch(self.descriptor_digest):
            raise AuthorizationValidationError(
                "decision descriptor digest must be SHA-256"
            )
        if self.outcome not in {"authorized", "preview_only", "denied"}:
            raise AuthorizationValidationError("authorization outcome is invalid")
        if not self.checks:
            raise AuthorizationValidationError("authorization decision requires checks")
        for check in self.checks:
            check.validate()
        all_passed = all(check.passed for check in self.checks)
        if (self.outcome == "denied") == all_passed:
            raise AuthorizationValidationError(
                "authorization outcome is inconsistent with its policy checks"
            )
        _timestamp(self.decided_at, "decision.decided_at")


def build_action_descriptor(
    *,
    action_id: str,
    actor: Literal["user", "agent", "system", "external"],
    capability: str,
    target_ref: str,
    arguments: dict[str, Any],
    side_effect_class: Literal[
        "none", "local_state", "external_state", "communication", "physical"
    ],
    reversibility: Literal[
        "reversible", "compensatable", "irreversible", "unknown"
    ],
    confidence: float,
    evidence_refs: Iterable[str],
    required_user_authority: Literal[
        "none", "session", "explicit_confirmation"
    ],
    spatial_requirement: str | None,
    idempotency_key: str,
    privacy_class: Literal["public", "internal", "private", "restricted"],
    external_destination: str | None = None,
    evidence_source_class: str = "model",
    created_at: datetime | None = None,
) -> ActionDescriptor:
    descriptor = ActionDescriptor(
        action_id=action_id,
        actor=actor,
        capability=capability,
        target_ref=target_ref,
        arguments_digest=hash_arguments(arguments),
        side_effect_class=side_effect_class,
        reversibility=reversibility,
        confidence=confidence,
        evidence_refs=tuple(evidence_refs),
        required_user_authority=required_user_authority,
        spatial_requirement=spatial_requirement,
        idempotency_key=idempotency_key,
        privacy_class=privacy_class,
        external_destination=external_destination,
        evidence_source_class=evidence_source_class,
        created_at=_utc(created_at).isoformat(),
    )
    descriptor.validate()
    return descriptor
