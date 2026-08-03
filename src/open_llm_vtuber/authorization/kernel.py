"""Fail-closed authorization evaluation over trusted runtime facts."""

from __future__ import annotations

import hashlib

from .contracts import (
    AUTHORITY_LEVELS,
    PRIVACY_CLASSES,
    ActionDescriptor,
    AuthorizationContext,
    AuthorizationDecision,
    PolicyCheck,
)


class AuthorizationKernel:
    """Produce one deterministic decision; never execute the described action."""

    def __init__(self, *, minimum_consequential_confidence: float = 0.8) -> None:
        if isinstance(minimum_consequential_confidence, bool):
            raise ValueError("authorization confidence threshold must be 0 to 1")
        threshold = float(minimum_consequential_confidence)
        if not 0 <= threshold <= 1:
            raise ValueError("authorization confidence threshold must be 0 to 1")
        self.minimum_consequential_confidence = threshold

    def evaluate(
        self,
        descriptor: ActionDescriptor,
        context: AuthorizationContext,
    ) -> AuthorizationDecision:
        descriptor.validate()
        context.validate()
        digest = descriptor.digest
        now = context.evaluated_at

        required_authority = self._required_authority(descriptor)
        confirmation = context.confirmation
        confirmation_valid = bool(
            confirmation is not None
            and confirmation.authorizes(digest, at=now)
        )
        if required_authority == "none":
            authority_allowed = True
        elif required_authority == "session":
            authority_allowed = (
                descriptor.capability in context.session_authorities
                or confirmation_valid
            )
        else:
            authority_allowed = confirmation_valid

        evidence_allowed = set(descriptor.evidence_refs).issubset(
            context.trusted_evidence_refs
        ) and bool(descriptor.evidence_refs or descriptor.actor == "user")
        spatial_allowed = (
            descriptor.spatial_requirement is None
            or descriptor.spatial_requirement in context.reachable_spatial_targets
        )
        external_allowed = (
            descriptor.external_destination is None
            or descriptor.external_destination
            in context.allowed_external_destinations
        )
        privacy_allowed = PRIVACY_CLASSES.index(
            descriptor.privacy_class
        ) <= PRIVACY_CLASSES.index(context.privacy_ceiling)
        presence_required = descriptor.side_effect_class in {
            "communication",
            "physical",
        }
        confidence_allowed = (
            descriptor.side_effect_class == "none"
            or descriptor.confidence >= self.minimum_consequential_confidence
        )

        checks = (
            self._check(
                "actor",
                descriptor.actor == context.requesting_actor,
                "actor_bound",
                "actor_mismatch",
            ),
            self._check(
                "capability",
                descriptor.capability in context.granted_capabilities,
                "capability_granted",
                "capability_not_granted",
            ),
            self._check(
                "evidence",
                evidence_allowed,
                "trusted_evidence_bound",
                "evidence_untrusted_or_missing",
            ),
            self._check(
                "confidence",
                confidence_allowed,
                "confidence_sufficient",
                "confidence_below_policy_threshold",
            ),
            self._check(
                "authority",
                authority_allowed,
                "user_authority_satisfied",
                "user_authority_missing",
            ),
            self._check(
                "spatial",
                spatial_allowed,
                "spatial_requirement_satisfied",
                "spatial_requirement_unsatisfied",
            ),
            self._check(
                "presence",
                not presence_required or context.presence_allowed,
                "presence_contract_satisfied",
                "presence_contract_denied",
            ),
            self._check(
                "quota",
                context.quota_remaining > 0,
                "quota_available",
                "quota_exhausted",
            ),
            self._check(
                "privacy",
                privacy_allowed,
                "privacy_ceiling_satisfied",
                "privacy_ceiling_exceeded",
            ),
            self._check(
                "destination",
                external_allowed,
                "external_destination_allowed",
                "external_destination_denied",
            ),
            self._check(
                "idempotency",
                bool(descriptor.idempotency_key),
                "idempotency_key_present",
                "idempotency_key_missing",
            ),
        )
        passed = all(check.passed for check in checks)
        outcome = (
            "denied"
            if not passed
            else "preview_only"
            if context.execution_mode == "preview"
            else "authorized"
        )
        identity = hashlib.sha256(
            f"{digest}\x00{now}\x00{outcome}".encode("utf-8")
        ).hexdigest()[:32]
        decision = AuthorizationDecision(
            decision_id=f"authz:{identity}",
            action_id=descriptor.action_id,
            descriptor_digest=digest,
            outcome=outcome,
            checks=checks,
            decided_at=now,
        )
        decision.validate()
        return decision

    @staticmethod
    def _required_authority(descriptor: ActionDescriptor) -> str:
        requested = AUTHORITY_LEVELS.index(descriptor.required_user_authority)
        policy_minimum = 0
        if descriptor.side_effect_class in {"local_state", "physical"}:
            policy_minimum = 1
        if descriptor.side_effect_class in {
            "external_state",
            "communication",
        }:
            policy_minimum = 2
        if descriptor.reversibility in {"irreversible", "unknown"}:
            policy_minimum = 2
        return AUTHORITY_LEVELS[max(requested, policy_minimum)]

    @staticmethod
    def _check(
        name: str,
        passed: bool,
        success_code: str,
        failure_code: str,
    ) -> PolicyCheck:
        return PolicyCheck(
            check=name,
            passed=bool(passed),
            reason_code=success_code if passed else failure_code,
        )
