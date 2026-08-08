"""The only production commit boundary for consequential MCP actions."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from .contracts import (
    ActionDescriptor,
    AuthorizationContext,
    UserConfirmation,
    build_action_descriptor,
    hash_arguments,
)
from .journal import (
    ActionTransactionJournal,
    descriptor_from_record,
    get_action_transaction_journal,
)
from .kernel import AuthorizationKernel
from .profiles import ACTION_PROFILES, ActionProfile, profile_for

ToolInvoker = Callable[[], Awaitable[dict[str, Any]]]
Verifier = Callable[
    [ActionDescriptor, ActionProfile, dict[str, Any], dict[str, Any]],
    Awaitable[dict[str, Any] | None],
]
Compensator = Callable[
    [ActionDescriptor, ActionProfile, dict[str, Any]],
    Awaitable[dict[str, Any] | None],
]


@dataclass(frozen=True)
class TransactionResult:
    is_error: bool
    text: str
    metadata: dict[str, Any]
    content_items: list[dict[str, Any]]


def _digest(*parts: str) -> str:
    material = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _opaque_ref(kind: str, value: Any) -> str:
    return f"{kind}:sha256:{_canonical_digest(value)}"


class ActionTransactionCoordinator:
    """Authorize, commit, verify, and if possible compensate one action."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        client_uid: str,
        tool_manager: Any,
        verifier: Verifier | None = None,
        compensator: Compensator | None = None,
        journal: ActionTransactionJournal | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.client_uid = client_uid or "anonymous"
        self.tool_manager = tool_manager
        self.journal = journal or get_action_transaction_journal(self.workspace_root)
        self.kernel = AuthorizationKernel()
        self.verifier = verifier
        self.compensator = compensator

    async def execute(
        self,
        *,
        server_name: str,
        capability: str,
        tool_id: str,
        arguments: dict[str, Any],
        invoke: ToolInvoker,
    ) -> TransactionResult:
        if not isinstance(arguments, dict):
            return self._error("Action arguments must be an object.")
        profile = profile_for(capability, server_name=server_name)
        proximity_allowed = True
        proximity_reason = None
        authorize_tool = getattr(self.tool_manager, "authorize_tool", None)
        if callable(authorize_tool):
            proximity_allowed, proximity_reason = authorize_tool(capability)
        if not profile.consequential:
            if not proximity_allowed:
                return self._error(proximity_reason or "Spatial access is denied.")
            return self._from_tool_result(await invoke())

        arguments_digest = hash_arguments(arguments)
        session_ref = _digest(self.client_uid)[:32]
        proposal_fingerprint = _digest(
            self.client_uid, server_name, capability, arguments_digest
        )
        delivery_key = (
            "delivery:"
            + _digest(
                self.client_uid,
                server_name,
                tool_id,
                arguments_digest,
            )
        )
        spatial_requirement = self.tool_manager.get_required_object(capability)
        target_value = (
            arguments.get(profile.target_argument)
            if profile.target_argument is not None
            else capability
        )
        destination_value = (
            arguments.get(profile.destination_argument)
            if profile.destination_argument is not None
            else profile.destination_service or server_name
        )
        external_destination = None
        if profile.side_effect_class in {"external_state", "communication"}:
            external_destination = _opaque_ref("destination", destination_value)
        required_authority = (
            "explicit_confirmation"
            if profile.side_effect_class in {"external_state", "communication"}
            or profile.reversibility in {"irreversible", "unknown"}
            else "session"
        )
        descriptor = build_action_descriptor(
            action_id=f"action:{proposal_fingerprint[:32]}",
            actor="agent",
            capability=capability,
            target_ref=_opaque_ref("target", target_value),
            arguments=arguments,
            side_effect_class=profile.side_effect_class,
            reversibility=profile.reversibility,
            confidence=0.99,
            evidence_refs=(f"session:{session_ref}",),
            required_user_authority=required_authority,
            spatial_requirement=spatial_requirement,
            idempotency_key=f"idempotency:{delivery_key.removeprefix('delivery:')}",
            privacy_class="private",
            external_destination=external_destination,
        )
        transaction = self.journal.ensure_proposal(
            descriptor=descriptor,
            profile=profile,
            server_name=server_name,
            proposal_fingerprint=proposal_fingerprint,
            delivery_key=delivery_key,
        )
        descriptor = descriptor_from_record(transaction["descriptor"])
        confirmation = (
            UserConfirmation(**transaction["confirmation"])
            if transaction["confirmation"] is not None
            else None
        )
        evaluation_time = datetime.now(timezone.utc).isoformat()
        if (
            confirmation is not None
            and descriptor.required_user_authority == "explicit_confirmation"
            and not confirmation.authorizes(descriptor.digest, at=evaluation_time)
        ):
            transaction = self.journal.reopen_expired_confirmation(
                transaction["transaction_id"]
            )
            confirmation = None
        presence_allowed, presence_summary = self._presence_snapshot()
        context = self._context(
            descriptor,
            confirmation=confirmation,
            reviewed=capability in ACTION_PROFILES,
            evaluated_at=evaluation_time,
            presence_allowed=presence_allowed,
        )
        decision = self.kernel.evaluate(descriptor, context)
        attempt = self.journal.begin_attempt(
            transaction["transaction_id"],
            delivery_key=delivery_key,
            decision=decision,
        )
        attempt_id = attempt["attempt_id"]

        if not decision.may_commit:
            status = transaction["status"]
            outcome = "declined" if status == "declined" else "denied"
            failed = [
                check.reason_code for check in decision.checks if not check.passed
            ]
            reason = failed[0] if failed else "policy_denied"
            logger.warning(
                "authorization denied capability={} failed_checks={} "
                "presence_fresh={} user_present={} focus_mode={}",
                capability,
                failed,
                presence_summary["fresh"],
                presence_summary["user_present"],
                presence_summary["focus_mode"],
            )
            self.journal.finish_attempt(
                transaction["transaction_id"],
                attempt_id=attempt_id,
                outcome=outcome,
                reason_code=reason,
                transaction_status=status,
            )
            if capability not in ACTION_PROFILES:
                return self._error(
                    f"Action {descriptor.action_id} is blocked because capability "
                    f"'{capability}' has no reviewed transaction adapter."
                )
            if status == "pending_confirmation":
                return self._error(
                    f"Approval required for exact action {descriptor.action_id}. "
                    "Review it at the writing desk, then retry the same action."
                )
            return self._error(
                f"Action {descriptor.action_id} was not committed ({reason})."
            )

        if transaction["status"] == "succeeded":
            self.journal.finish_attempt(
                transaction["transaction_id"],
                attempt_id=attempt_id,
                outcome="replayed",
                reason_code="delivery_already_committed",
                transaction_status="succeeded",
            )
            return TransactionResult(
                is_error=False,
                text="Action already completed; the duplicate delivery had no effect.",
                metadata=self._metadata(transaction["transaction_id"], "replayed"),
                content_items=[
                    {
                        "type": "text",
                        "text": (
                            "Action already completed; the duplicate delivery had "
                            "no effect."
                        ),
                    }
                ],
            )

        if transaction["status"] in {
            "unresolved",
            "compensated",
            "failed",
            "replayed",
        }:
            terminal_state = transaction["status"]
            self.journal.finish_attempt(
                transaction["transaction_id"],
                attempt_id=attempt_id,
                outcome="denied",
                reason_code="terminal_delivery_not_retriable",
                transaction_status=terminal_state,
            )
            return self._error(
                "This delivery reached a terminal transaction state and will not be "
                "retried automatically.",
                transaction_id=transaction["transaction_id"],
                state=terminal_state,
            )

        self.journal.transition(
            transaction["transaction_id"], status="committing", attempt_id=attempt_id
        )
        try:
            tool_result = await invoke()
        except asyncio.CancelledError:
            self.journal.finish_attempt(
                transaction["transaction_id"],
                attempt_id=attempt_id,
                outcome="unresolved",
                reason_code="execution_cancelled_after_commit_boundary",
            )
            raise
        except Exception:
            self.journal.finish_attempt(
                transaction["transaction_id"],
                attempt_id=attempt_id,
                outcome="unresolved",
                reason_code="adapter_exception_after_commit_boundary",
            )
            raise

        parsed = self._from_tool_result(tool_result)
        receipt_digest = _canonical_digest(
            {
                "metadata": parsed.metadata,
                "content_items": parsed.content_items,
            }
        )
        if parsed.is_error:
            self.journal.finish_attempt(
                transaction["transaction_id"],
                attempt_id=attempt_id,
                outcome="unresolved",
                reason_code="adapter_reported_error_after_commit_boundary",
            )
            return self._annotate(
                parsed, transaction["transaction_id"], "unresolved"
            )

        self.journal.transition(
            transaction["transaction_id"],
            status="verifying",
            attempt_id=attempt_id,
            receipt_digest=receipt_digest,
        )
        try:
            verification = await self._verify(
                descriptor, profile, arguments, tool_result
            )
        except Exception:
            self.journal.finish_attempt(
                transaction["transaction_id"],
                attempt_id=attempt_id,
                outcome="unresolved",
                reason_code="verification_adapter_failed",
            )
            return self._error(
                "The action returned, but its postcondition verifier failed. It will "
                "not be retried automatically.",
                transaction_id=transaction["transaction_id"],
                state="unresolved",
            )
        verified = bool(verification and verification.get("verified") is True)
        if verified:
            self.journal.transition(
                transaction["transaction_id"],
                status="verifying",
                attempt_id=attempt_id,
                verification=self._minimal_evidence(verification),
            )
            self.journal.finish_attempt(
                transaction["transaction_id"],
                attempt_id=attempt_id,
                outcome="succeeded",
                reason_code="postcondition_verified",
            )
            logger.info(
                "authorization transaction succeeded capability={} action={}",
                capability,
                descriptor.action_id,
            )
            return self._annotate(parsed, transaction["transaction_id"], "succeeded")

        if profile.reversibility == "compensatable" and self.compensator is not None:
            self.journal.transition(
                transaction["transaction_id"],
                status="compensating",
                attempt_id=attempt_id,
                verification=self._minimal_evidence(verification),
            )
            try:
                compensation = await self.compensator(
                    descriptor, profile, arguments
                )
            except Exception:
                self.journal.finish_attempt(
                    transaction["transaction_id"],
                    attempt_id=attempt_id,
                    outcome="unresolved",
                    reason_code="compensation_adapter_failed",
                )
                return self._error(
                    "Verification and compensation could not establish a safe "
                    "outcome. The action remains unresolved.",
                    transaction_id=transaction["transaction_id"],
                    state="unresolved",
                )
            compensated = bool(
                compensation and compensation.get("compensated") is True
            )
            if compensated:
                self.journal.transition(
                    transaction["transaction_id"],
                    status="compensating",
                    attempt_id=attempt_id,
                    compensation=self._minimal_evidence(compensation),
                )
                self.journal.finish_attempt(
                    transaction["transaction_id"],
                    attempt_id=attempt_id,
                    outcome="compensated",
                    reason_code="verification_failed_compensation_verified",
                )
                return self._error(
                    "The action could not be verified and was safely compensated.",
                    transaction_id=transaction["transaction_id"],
                    state="compensated",
                )

        self.journal.finish_attempt(
            transaction["transaction_id"],
            attempt_id=attempt_id,
            outcome="unresolved",
            reason_code="postcondition_unverified",
        )
        return self._error(
            "The service returned, but the action outcome could not be verified. "
            "It will not be retried automatically.",
            transaction_id=transaction["transaction_id"],
            state="unresolved",
        )

    def _context(
        self,
        descriptor: ActionDescriptor,
        *,
        confirmation: UserConfirmation | None,
        reviewed: bool,
        evaluated_at: str,
        presence_allowed: bool,
    ) -> AuthorizationContext:
        reachable = frozenset(self.tool_manager.get_reachable_objects())

        destination_allowed = bool(
            confirmation is not None
            and confirmation.descriptor_digest == descriptor.digest
        )
        return AuthorizationContext(
            requesting_actor="agent",
            granted_capabilities=(
                frozenset({descriptor.capability}) if reviewed else frozenset()
            ),
            trusted_evidence_refs=frozenset(descriptor.evidence_refs),
            reachable_spatial_targets=reachable,
            session_authorities=(
                frozenset({descriptor.capability}) if reviewed else frozenset()
            ),
            presence_allowed=presence_allowed,
            quota_remaining=max(0, 20 - self.journal.recent_commit_count()),
            privacy_ceiling="private",
            allowed_external_destinations=(
                frozenset({descriptor.external_destination})
                if descriptor.external_destination is not None and destination_allowed
                else frozenset()
            ),
            execution_mode="commit",
            confirmation=confirmation,
            evaluated_at=evaluated_at,
        )

    def _presence_snapshot(self) -> tuple[bool, dict[str, bool]]:
        summary = {"fresh": False, "user_present": False, "focus_mode": False}
        try:
            from ..autonomy.observation import get_client_presence

            presence = get_client_presence(self.client_uid)
            summary = {
                "fresh": presence.fresh,
                "user_present": presence.user_present,
                "focus_mode": presence.focus_mode,
            }
            allowed = (
                presence.fresh and presence.user_present and not presence.focus_mode
            )
        except Exception:
            allowed = False
        return allowed, summary

    async def _verify(
        self,
        descriptor: ActionDescriptor,
        profile: ActionProfile,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self.verifier is not None:
            verification = await self.verifier(
                descriptor, profile, arguments, result
            )
            if verification is not None:
                return verification
        if profile.verification == "local_path":
            return self._verify_local_path(profile, arguments, result)
        evidence = result.get("metadata", {}).get("authorization_evidence")
        if (
            isinstance(evidence, dict)
            and evidence.get("descriptor_digest") == descriptor.digest
            and isinstance(evidence.get("evidence_digest"), str)
        ):
            return {
                "verified": evidence.get("verified") is True,
                "evidence_digest": evidence["evidence_digest"],
                "kind": "adapter_evidence",
            }
        return None

    def _verify_local_path(
        self,
        profile: ActionProfile,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        from ..workspace import resolve_workspace_path

        capability = profile.capability
        raw_path = arguments.get(profile.target_argument or "path")
        if capability == "download_file" and not raw_path:
            text = self._from_tool_result(result).text.splitlines()[0]
            prefix = "OK: downloaded "
            if text.startswith(prefix) and " (" in text:
                raw_path = text[len(prefix) :].split(" (", 1)[0]
        if not isinstance(raw_path, str) or not raw_path:
            return None
        try:
            target = resolve_workspace_path(raw_path, self.workspace_root)
            if capability == "move_path":
                destination = arguments.get("destination")
                if not isinstance(destination, str):
                    return None
                target = resolve_workspace_path(destination, self.workspace_root)
            if capability == "delete_path":
                verified = not target.exists()
            elif capability == "create_directory":
                verified = target.is_dir()
            elif capability == "write_file":
                verified = target.is_file() and target.read_text(encoding="utf-8") == arguments.get("content")
            elif capability == "append_to_file":
                content = arguments.get("content")
                verified = isinstance(content, str) and target.is_file() and target.read_text(encoding="utf-8").endswith(content)
            elif capability == "edit_file":
                replacement = arguments.get("new_string")
                verified = isinstance(replacement, str) and target.is_file() and replacement in target.read_text(encoding="utf-8")
            else:
                verified = target.exists()
        except (OSError, UnicodeError, ValueError):
            return None
        return {
            "verified": verified,
            "kind": "local_path_postcondition",
            "evidence_digest": _digest(str(target), str(verified)),
        }

    @staticmethod
    def _from_tool_result(result: dict[str, Any]) -> TransactionResult:
        metadata = result.get("metadata", {})
        content_items = result.get("content_items", [])
        if not isinstance(metadata, dict):
            metadata = {}
        if not isinstance(content_items, list):
            content_items = []
        first = content_items[0] if content_items else {}
        first_type = first.get("type") if isinstance(first, dict) else None
        text = first.get("text", "") if isinstance(first, dict) else ""
        is_error = first_type == "error" or (
            isinstance(text, str) and text.lstrip().startswith("Error:")
        )
        return TransactionResult(
            is_error=is_error,
            text=text if isinstance(text, str) else "",
            metadata=metadata,
            content_items=content_items,
        )

    @staticmethod
    def _minimal_evidence(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(evidence, dict):
            return None
        return {
            key: value
            for key, value in evidence.items()
            if key in {"verified", "compensated", "kind", "evidence_digest"}
            and isinstance(value, (str, bool))
        }

    @staticmethod
    def _metadata(transaction_id: str, state: str) -> dict[str, Any]:
        return {
            "action_transaction": {
                "transaction_id": transaction_id,
                "state": state,
            }
        }

    def _annotate(
        self, result: TransactionResult, transaction_id: str, state: str
    ) -> TransactionResult:
        return TransactionResult(
            is_error=result.is_error,
            text=result.text,
            metadata={**result.metadata, **self._metadata(transaction_id, state)},
            content_items=result.content_items,
        )

    def _error(
        self,
        message: str,
        *,
        transaction_id: str | None = None,
        state: str = "denied",
    ) -> TransactionResult:
        metadata = (
            self._metadata(transaction_id, state) if transaction_id is not None else {}
        )
        return TransactionResult(
            is_error=True,
            text=message,
            metadata=metadata,
            content_items=[{"type": "error", "text": message}],
        )
