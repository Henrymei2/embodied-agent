"""Reviewed capability inventory for action authorization and verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EffectClass = Literal[
    "none", "local_state", "external_state", "communication", "physical"
]
Reversibility = Literal[
    "reversible", "compensatable", "irreversible", "unknown"
]
VerificationKind = Literal[
    "none", "local_path", "adapter_evidence", "remote_readback", "frontend_ack"
]


@dataclass(frozen=True)
class ActionProfile:
    capability: str
    side_effect_class: EffectClass
    reversibility: Reversibility
    verification: VerificationKind
    target_argument: str | None = None
    destination_argument: str | None = None
    destination_service: str | None = None
    compensation_capability: str | None = None
    native_idempotency: bool = False

    @property
    def consequential(self) -> bool:
        return self.side_effect_class != "none"


_READ_PREFIXES = (
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


def _profile(
    capability: str,
    effect: EffectClass,
    reversibility: Reversibility,
    verification: VerificationKind,
    **kwargs: Any,
) -> ActionProfile:
    return ActionProfile(
        capability=capability,
        side_effect_class=effect,
        reversibility=reversibility,
        verification=verification,
        **kwargs,
    )


# This inventory covers every bundled state-changing MCP capability. Dynamically
# discovered tools that are absent here do not become read-only by accident:
# ``profile_for`` returns an explicit-confirmation, unknown-effect profile.
ACTION_PROFILES: dict[str, ActionProfile] = {
    # Workspace and network-to-workspace operations.
    "download_file": _profile(
        "download_file", "local_state", "compensatable", "local_path",
        target_argument="path",
    ),
    "execute_bash": _profile(
        # Runs inside the container sandbox, so its workspace-confined effects
        # are recoverable rather than unknown: commands commit on session
        # authority instead of requiring a desk decision.
        "execute_bash", "local_state", "compensatable", "adapter_evidence"
    ),
    "write_file": _profile(
        "write_file", "local_state", "compensatable", "local_path",
        target_argument="path",
    ),
    "append_to_file": _profile(
        "append_to_file", "local_state", "compensatable", "local_path",
        target_argument="path",
    ),
    "edit_file": _profile(
        "edit_file", "local_state", "compensatable", "local_path",
        target_argument="path",
    ),
    "create_directory": _profile(
        "create_directory", "local_state", "compensatable", "local_path",
        target_argument="path",
    ),
    "delete_path": _profile(
        "delete_path", "local_state", "irreversible", "local_path",
        target_argument="path",
    ),
    "move_path": _profile(
        "move_path", "local_state", "compensatable", "local_path",
        target_argument="destination", compensation_capability="move_path",
    ),
    # Google external state.
    "add_calendar_event": _profile(
        "add_calendar_event", "external_state", "compensatable", "remote_readback",
        target_argument="title", destination_service="google-calendar",
        compensation_capability="remove_calendar_event",
    ),
    "update_calendar_event": _profile(
        "update_calendar_event", "external_state", "compensatable", "remote_readback",
        target_argument="event_id", destination_service="google-calendar",
    ),
    "remove_calendar_event": _profile(
        "remove_calendar_event", "external_state", "irreversible", "remote_readback",
        target_argument="event_id", destination_service="google-calendar",
    ),
    "create_drive_text_file": _profile(
        "create_drive_text_file", "external_state", "compensatable", "remote_readback",
        target_argument="name", destination_service="google-drive",
    ),
    "update_drive_text_file": _profile(
        "update_drive_text_file", "external_state", "compensatable", "remote_readback",
        target_argument="file_id", destination_service="google-drive",
    ),
    "move_drive_file": _profile(
        "move_drive_file", "external_state", "compensatable", "remote_readback",
        target_argument="file_id", destination_service="google-drive",
    ),
    "create_email_draft": _profile(
        "create_email_draft", "external_state", "reversible", "remote_readback",
        target_argument="to", destination_argument="to",
    ),
    "send_email": _profile(
        "send_email", "communication", "irreversible", "remote_readback",
        target_argument="to", destination_argument="to",
    ),
    "mark_email_read": _profile(
        "mark_email_read", "external_state", "reversible", "remote_readback",
        target_argument="message_id", destination_service="gmail",
    ),
    "mark_email_unread": _profile(
        "mark_email_unread", "external_state", "reversible", "remote_readback",
        target_argument="message_id", destination_service="gmail",
    ),
    "archive_email": _profile(
        "archive_email", "external_state", "reversible", "remote_readback",
        target_argument="message_id", destination_service="gmail",
    ),
    # Local semantic stores. Destructive operations are not described as
    # reversible merely because the store itself is local.
    "add_todo": _profile(
        "add_todo", "local_state", "compensatable", "adapter_evidence",
        target_argument="text", compensation_capability="remove_todo",
    ),
    "update_todo": _profile(
        "update_todo", "local_state", "reversible", "adapter_evidence",
        target_argument="todo_id",
    ),
    "complete_todo": _profile(
        "complete_todo", "local_state", "reversible", "adapter_evidence",
        target_argument="todo_id",
    ),
    "remove_todo": _profile(
        "remove_todo", "local_state", "irreversible", "adapter_evidence",
        target_argument="todo_id",
    ),
    "set_timer": _profile(
        "set_timer", "local_state", "compensatable", "adapter_evidence",
        target_argument="label", compensation_capability="cancel_timer",
    ),
    "cancel_timer": _profile(
        "cancel_timer", "local_state", "irreversible", "adapter_evidence",
        target_argument="timer_id",
    ),
    "schedule_task": _profile(
        "schedule_task", "local_state", "compensatable", "adapter_evidence",
        target_argument="prompt", compensation_capability="remove_task",
    ),
    "remove_task": _profile(
        "remove_task", "local_state", "irreversible", "adapter_evidence",
        target_argument="task_id",
    ),
    "write_plan": _profile(
        "write_plan", "local_state", "compensatable", "adapter_evidence",
        target_argument="title", compensation_capability="withdraw_plan",
    ),
    "revise_plan": _profile(
        "revise_plan", "local_state", "compensatable", "adapter_evidence",
        target_argument="plan_id",
    ),
    "withdraw_plan": _profile(
        "withdraw_plan", "local_state", "irreversible", "adapter_evidence",
        target_argument="plan_id",
    ),
    "draft_skill": _profile(
        "draft_skill", "local_state", "compensatable", "adapter_evidence",
        target_argument="name", compensation_capability="retire_skill",
    ),
    "revise_skill": _profile(
        "revise_skill", "local_state", "reversible", "adapter_evidence",
        target_argument="slug",
    ),
    "retire_skill": _profile(
        "retire_skill", "local_state", "irreversible", "adapter_evidence",
        target_argument="slug",
    ),
    "shelve_book": _profile(
        "shelve_book", "local_state", "compensatable", "adapter_evidence",
        target_argument="title", compensation_capability="remove_from_shelf",
    ),
    "remove_from_shelf": _profile(
        "remove_from_shelf", "local_state", "irreversible", "adapter_evidence",
        target_argument="book_id",
    ),
    "save_reading_note": _profile(
        "save_reading_note", "local_state", "irreversible", "adapter_evidence",
        target_argument="summary",
    ),
    "finish_book": _profile(
        "finish_book", "local_state", "irreversible", "adapter_evidence",
    ),
    "draw_image": _profile(
        "draw_image", "local_state", "compensatable", "adapter_evidence",
        target_argument="prompt",
    ),
    # Embodied/frontend effects.
    "move_to": _profile(
        "move_to", "physical", "reversible", "frontend_ack",
        target_argument="target",
    ),
    "sit_down": _profile(
        "sit_down", "physical", "reversible", "frontend_ack"
    ),
    "stand_up": _profile(
        "stand_up", "physical", "reversible", "frontend_ack"
    ),
    "materialize_email": _profile(
        "materialize_email", "physical", "reversible", "frontend_ack",
        target_argument="message_id",
    ),
    "show_vr_panel": _profile(
        "show_vr_panel", "physical", "reversible", "frontend_ack",
        target_argument="title",
    ),
    "pick_up_entity": _profile(
        "pick_up_entity", "physical", "reversible", "frontend_ack",
        target_argument="entity_id",
    ),
    "put_down_entity": _profile(
        "put_down_entity", "physical", "reversible", "frontend_ack",
        target_argument="entity_id",
    ),
    "offer_entity": _profile(
        "offer_entity", "physical", "reversible", "frontend_ack",
        target_argument="entity_id",
    ),
    "dismiss_entity": _profile(
        "dismiss_entity", "physical", "reversible", "frontend_ack",
        target_argument="entity_id",
    ),
    "move_to_user": _profile(
        "move_to_user", "physical", "reversible", "frontend_ack",
    ),
    "place_prop": _profile(
        "place_prop", "local_state", "compensatable", "frontend_ack",
        target_argument="name", compensation_capability="remove_object",
    ),
    "place_object": _profile(
        "place_object", "local_state", "compensatable", "frontend_ack",
        target_argument="name", compensation_capability="remove_object",
    ),
    "move_object": _profile(
        "move_object", "local_state", "compensatable", "frontend_ack",
        target_argument="name",
    ),
    "remove_object": _profile(
        "remove_object", "local_state", "irreversible", "frontend_ack",
        target_argument="name",
    ),
}


def profile_for(capability: str, *, server_name: str | None = None) -> ActionProfile:
    """Return a reviewed profile, failing consequential for unknown tools."""

    if capability in ACTION_PROFILES:
        return ACTION_PROFILES[capability]
    if capability.startswith(_READ_PREFIXES):
        return _profile(capability, "none", "reversible", "none")
    return _profile(
        capability,
        "external_state",
        "unknown",
        "adapter_evidence",
        destination_service=f"mcp:{server_name or 'unknown'}",
    )


def bundled_consequential_capabilities() -> frozenset[str]:
    return frozenset(
        name for name, profile in ACTION_PROFILES.items() if profile.consequential
    )
