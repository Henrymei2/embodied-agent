"""Postcondition verification by reading back workspace-local effects.

Real Google/Gmail readback is intentionally unresolved when no live service is
available (the Part 5 harness treats ``remote_readback`` without a service as
unresolved).  The simulated demo scenario is a local, deterministic service:
its writes are durable records under the workspace, so a readback verifier can
confirm the exact effect without any external call.  Local stores such as the
writing-desk plan file are always readable back, in every mode.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import ActionDescriptor
from .profiles import ActionProfile

_WRITES_FILENAME = "vr_demo_scenario_writes.json"

# capability -> (record payload action, argument fields to match)
_GMAIL_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "create_email_draft": ("create_draft", ("to", "subject")),
    "send_email": ("send", ("to", "subject")),
    "mark_email_read": ("modify", ("message_id",)),
    "mark_email_unread": ("modify", ("message_id",)),
    "archive_email": ("modify", ("message_id",)),
}

# capability -> expected label change for simulated ``modify`` records.
_MODIFY_EXPECTATIONS: dict[str, tuple[list[str], list[str]]] = {
    "mark_email_read": ([], ["UNREAD"]),
    "mark_email_unread": (["UNREAD"], []),
    "archive_email": ([], ["INBOX"]),
}


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _result(verified: bool, observed: Any) -> dict[str, Any]:
    return {
        "verified": bool(verified),
        "kind": "workspace_readback",
        "evidence_digest": _digest(observed),
    }


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_records(root: Path) -> list[dict[str, Any]]:
    value = _load_json(root / "agenda" / _WRITES_FILENAME)
    return value if isinstance(value, list) else []


def _tool_result_text(tool_result: dict[str, Any]) -> str:
    content_items = tool_result.get("content_items")
    if not isinstance(content_items, list) or not content_items:
        return ""
    first = content_items[0]
    if not isinstance(first, dict):
        return ""
    text = first.get("text")
    return text if isinstance(text, str) else ""


def _receipt_id(tool_result: dict[str, Any]) -> str | None:
    text = _tool_result_text(tool_result).strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("draft_id", "record_id", "message_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _record_matches(
    record: dict[str, Any],
    *,
    capability: str,
    action: str,
    arguments: dict[str, Any],
    receipt_id: str | None,
) -> bool:
    if record.get("kind") != "gmail":
        return False
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return False
    if payload.get("action") != action:
        return False
    mapping = _GMAIL_ACTIONS.get(capability)
    if mapping is None:
        return False
    for field in mapping[1]:
        if payload.get(field) != arguments.get(field):
            return False
    if action == "modify":
        expected_add, expected_remove = _MODIFY_EXPECTATIONS.get(capability, ([], []))
        if list(payload.get("add") or []) != expected_add:
            return False
        if list(payload.get("remove") or []) != expected_remove:
            return False
    if receipt_id:
        record_id = record.get("id")
        if isinstance(record_id, str) and record_id != receipt_id:
            return False
    return True


def _verify_plan_written(root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    """Confirm a pending plan with the requested title/goal is on the desk."""
    value = _load_json(root / "plans" / "plans.json")
    plans = value.get("plans") if isinstance(value, dict) else None
    if not isinstance(plans, list):
        return _result(False, {"capability": "write_plan", "arguments": arguments})
    title = arguments.get("title")
    goal = arguments.get("goal")
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        if plan.get("status") != "pending":
            continue
        if title is not None and plan.get("title") != title:
            continue
        if goal is not None and plan.get("goal") != goal:
            continue
        return _result(True, plan)
    return _result(False, {"capability": "write_plan", "arguments": arguments})


def build_workspace_readback_verifier(workspace_root: str | Path):
    """Build a verifier that reads back workspace-local effects.

    Handles simulated Gmail writes while the demo scenario is active, and the
    writing-desk plan store in every mode.  Every other call returns ``None``
    so the transaction falls through to its normal verification behavior.
    """

    root = Path(workspace_root).resolve()

    async def verify(
        _descriptor: ActionDescriptor,
        profile: ActionProfile,
        arguments: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        if profile.capability == "write_plan":
            return _verify_plan_written(root, arguments)
        if profile.verification != "remote_readback":
            return None
        from ..demo_scenarios import simulated_fixture

        if simulated_fixture(root) is None:
            return None
        action = _GMAIL_ACTIONS.get(profile.capability)
        if action is None:
            return None
        receipt_id = _receipt_id(tool_result)
        for record in reversed(_load_records(root)):
            if _record_matches(
                record,
                capability=profile.capability,
                action=action[0],
                arguments=arguments,
                receipt_id=receipt_id,
            ):
                return _result(True, record)
        return _result(
            False,
            {
                "capability": profile.capability,
                "action": action[0],
                "arguments": arguments,
            },
        )

    return verify
