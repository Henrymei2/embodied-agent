"""Fail-closed authorization evaluation over trusted runtime facts.

Part 13 added the calibration gate here, and it is the seam where two systems
this program built separately finally meet. Part 3 measures, per source class,
how often a stated confidence turns out to be right — on the frozen fixture the
`model` class states 0.73 and is correct 47% of the time, an expected
calibration error of 0.26. Nothing consumed that number. The kernel compared a
model's *self-reported* confidence against a fixed 0.8, which is precisely what
`provenance/calibration.py` opens by saying not to do: a model that says 0.9 is
not right nine times in ten, it is right however often it has been right.

So when a calibrator is supplied, the confidence check asks three questions
instead of one:

1. How much confidence does *this* action's risk demand? An irreversible
   external send is a different bet than a scratch file write, so the threshold
   comes from the action rather than from a single global constant.
2. What is the stated confidence actually worth, given the measured history of
   this source class?
3. If that source class has never been measured — is there any answer at all?

The third one fails closed, and that is the substantive decision. The
comfortable alternative is to fall back to the stated number when calibration is
unavailable, which means an attacker's cheapest move is to arrive through an
unmeasured source. "We have never measured this source" is not evidence that it
is reliable, and treating it as such would make the whole mechanism decorative.

Supplying no calibrator preserves the previous behaviour exactly, so existing
deployments and the frozen Part 5 ablation are unaffected until the gate is
deliberately turned on.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from .contracts import (
    AUTHORITY_LEVELS,
    PRIVACY_CLASSES,
    ActionDescriptor,
    AuthorizationContext,
    AuthorizationDecision,
    PolicyCheck,
)

# How much calibrated confidence each risk band demands. These are policy, not
# measurements: they say how sure the system must be before taking a class of
# action, and they are stated here so a reviewer can argue with them.
RISK_THRESHOLDS: dict[str, float] = {
    "none": 0.0,  # read-only; confidence is not load-bearing
    "low": 0.70,  # reversible local change
    "medium": 0.85,  # external or communicative, but compensatable
    "high": 0.95,  # irreversible, unknown, or physical
}


class Calibrator(Protocol):
    """The part of Part 3's `SourceCalibrator` this kernel depends on."""

    def calibrate(
        self, source_class: str, reported_confidence: float
    ) -> float | None: ...


def risk_band(descriptor: ActionDescriptor) -> str:
    """How much this action can cost if the belief behind it is wrong.

    Reversibility dominates effect class: a compensatable email is a smaller bet
    than an irreversible local delete, even though one leaves the machine and
    the other does not.
    """
    if descriptor.side_effect_class == "none":
        return "none"
    if descriptor.reversibility in {"irreversible", "unknown"}:
        return "high"
    if descriptor.side_effect_class == "physical":
        return "high"
    if descriptor.side_effect_class in {"external_state", "communication"}:
        return "medium"
    return "low"


class AuthorizationKernel:
    """Produce one deterministic decision; never execute the described action."""

    def __init__(
        self,
        *,
        minimum_consequential_confidence: float = 0.8,
        calibrator: Calibrator | None = None,
    ) -> None:
        if isinstance(minimum_consequential_confidence, bool):
            raise ValueError("authorization confidence threshold must be 0 to 1")
        threshold = float(minimum_consequential_confidence)
        if not 0 <= threshold <= 1:
            raise ValueError("authorization confidence threshold must be 0 to 1")
        self.minimum_consequential_confidence = threshold
        self.calibrator = calibrator

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
        # Presence is an attention/consent boundary for work directed at the
        # user. Silent embodied actions (walking, sitting, standing) remain
        # physical and high-risk for confidence/authority purposes, but they
        # must not become impossible merely because the renderer is hidden or
        # its coarse presence heartbeat is stale.
        presence_required = descriptor.side_effect_class == "communication"
        confidence_check = self._confidence_check(descriptor)

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
            confidence_check,
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

    def _confidence_check(self, descriptor: ActionDescriptor) -> PolicyCheck:
        """Is the belief behind this action good enough for what it costs?"""
        band = risk_band(descriptor)
        if band == "none":
            # A read has no side effect to be wrong about. Demanding confidence
            # here would only teach callers to inflate it.
            return self._check(
                "confidence", True, "confidence_not_load_bearing", "unreachable"
            )

        if self.calibrator is None:
            # Pre-Part-13 behaviour, unchanged: the stated number against one
            # global threshold. Kept so that turning the gate on is a decision
            # somebody makes rather than a silent change of policy.
            return self._check(
                "confidence",
                descriptor.confidence >= self.minimum_consequential_confidence,
                "confidence_sufficient",
                "confidence_below_policy_threshold",
            )

        measured = self.calibrator.calibrate(
            descriptor.evidence_source_class, descriptor.confidence
        )
        if measured is None:
            # Fail closed. An unmeasured source is not a trustworthy one, and
            # falling back to the stated number here would make arriving through
            # an unmeasured channel the cheapest way past the gate.
            return PolicyCheck(
                check="confidence",
                passed=False,
                reason_code="calibration_unavailable_for_source_class",
            )
        required = RISK_THRESHOLDS[band]
        return PolicyCheck(
            check="confidence",
            passed=measured >= required,
            reason_code=(
                f"calibrated_confidence_meets_{band}_risk"
                if measured >= required
                else f"calibrated_confidence_below_{band}_risk"
            ),
        )

    @staticmethod
    def _required_authority(descriptor: ActionDescriptor) -> str:
        requested = AUTHORITY_LEVELS.index(descriptor.required_user_authority)
        # Any consequential effect needs at least session authority; only
        # communicative, irreversible, or unknown-effect actions escalate to
        # an explicit user decision at the writing desk. Reversible external
        # writes (archiving mail, calendar/drive edits) commit on session
        # authority like local work.
        policy_minimum = 1 if descriptor.side_effect_class != "none" else 0
        if descriptor.reversibility in {"irreversible", "unknown"}:
            policy_minimum = 2
        elif descriptor.side_effect_class == "communication":
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
