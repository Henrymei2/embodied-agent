"""Deterministic validation for LLM-generated activity checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .agenda import AgendaEntry, ExecutionCheckpoint
from .tool_schemas import validate_tool_arguments


_READ_ONLY_PREFIXES = (
    "check_",
    "describe_",
    "fetch_",
    "get_",
    "inspect_",
    "list_",
    "query_",
    "read_",
    "search_",
)

# Audited read-only capabilities whose names do not carry a conventional
# read/list/get prefix. Unknown capabilities remain side-effecting by default.
_READ_ONLY_CAPABILITIES = {
    "browser_tabs",
}

# Only irreversible or communicative capabilities require an approved plan
# before autonomous execution. Reversible external writes and sandboxed local
# work (archiving mail, calendar/drive edits, commands, todos, timers, notes)
# run directly; unknown tools still fail safe as side-effecting, but they are
# not part of this plan gate.
_APPROVAL_REQUIRED = {
    "cancel_timer",
    "delete_path",
    "finish_book",
    "remove_calendar_event",
    "remove_from_shelf",
    "remove_object",
    "remove_task",
    "remove_todo",
    "retire_skill",
    "save_reading_note",
    "send_email",
    "withdraw_plan",
}


def capability_requires_approval(capability: str) -> bool:
    return capability in _APPROVAL_REQUIRED


@dataclass(frozen=True)
class StepVerdict:
    accepted: bool
    reason: str
    retryable: bool = False


class StepPolicy:
    """Validate and normalize a generated step before it reaches execution."""

    def __init__(
        self,
        *,
        capabilities: set[str],
        object_names: set[str],
        capability_objects: dict[str, str] | None = None,
        tool_schemas: Mapping[str, dict] | None = None,
        connected_client: bool = True,
    ) -> None:
        self.capabilities = capabilities
        self.object_names = object_names
        self.capability_objects = capability_objects or {}
        self.tool_schemas = dict(tool_schemas or {})
        self.connected_client = connected_client

    def validate(
        self,
        entry: AgendaEntry,
        checkpoint: ExecutionCheckpoint,
    ) -> StepVerdict:
        try:
            checkpoint.validate()
        except ValueError as exc:
            return StepVerdict(False, str(exc))

        capability = checkpoint.required_capability
        if checkpoint.kind == "invoke_tool" and not capability:
            return StepVerdict(False, "tool steps require a named capability")
        if capability and capability not in self.capabilities:
            return StepVerdict(False, f"unavailable capability: {capability}")
        if checkpoint.kind == "invoke_tool" and capability in self.tool_schemas:
            argument_error = validate_tool_arguments(
                capability,
                checkpoint.arguments,
                self.tool_schemas,
            )
            if argument_error:
                return StepVerdict(False, argument_error)

        expected_object = (
            self.capability_objects.get(capability) if capability else None
        )
        required_object = checkpoint.required_object
        if required_object and required_object not in self.object_names:
            return StepVerdict(False, f"unknown required object: {required_object}")
        if expected_object and required_object not in {None, expected_object}:
            return StepVerdict(
                False,
                f"capability {capability} requires object {expected_object}",
            )
        if expected_object:
            checkpoint.required_object = expected_object
        if not self.connected_client and (
            checkpoint.required_object is not None
            or checkpoint.kind
            in {"move_to_object", "report_result", "request_approval"}
        ):
            return StepVerdict(
                False,
                "Waiting for a connected client and embodied room.",
                retryable=True,
            )

        checkpoint.side_effecting = self._is_side_effecting(checkpoint)
        checkpoint.interruptible = not checkpoint.side_effecting

        if (
            capability
            and capability_requires_approval(capability)
            and entry.metadata.get("trigger_policy") != "gmail-unread-v1"
            and not (
                entry.requires_approval
                and entry.plan_id
                and entry.metadata.get("approval_status") == "approved"
            )
        ):
            return StepVerdict(
                False,
                f"capability {capability} requires an approved plan",
            )
        if checkpoint.kind == "request_approval" and not entry.requires_approval:
            return StepVerdict(
                False,
                "approval steps require an approval-gated agenda entry",
            )
        return StepVerdict(True, "accepted")

    @staticmethod
    def _is_side_effecting(checkpoint: ExecutionCheckpoint) -> bool:
        if checkpoint.kind in {"write_artifact", "report_result", "request_approval"}:
            return True
        if checkpoint.kind != "invoke_tool":
            return False
        capability = checkpoint.required_capability or ""
        return capability not in _READ_ONLY_CAPABILITIES and not capability.startswith(
            _READ_ONLY_PREFIXES
        )
