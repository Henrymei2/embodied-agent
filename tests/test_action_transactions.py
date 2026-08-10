from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from src.open_llm_vtuber.authorization import (
    ActionTransactionCoordinator,
    ActionTransactionJournal,
    AuthorizationJournalError,
)


class _Manager:
    def __init__(self, *, reachable: tuple[str, ...] = ("computer",)) -> None:
        self.reachable = reachable

    def get_required_object(self, capability: str) -> str | None:
        return "computer" if capability in {"write_file", "mystery_write"} else None

    def get_reachable_objects(self) -> list[str]:
        return list(self.reachable)

    def authorize_tool(self, capability: str) -> tuple[bool, str | None]:
        requirement = self.get_required_object(capability)
        allowed = requirement is None or requirement in self.reachable
        return allowed, None if allowed else "computer is not reachable"


def _coordinator(
    root: Path,
    *,
    verifier=None,
    compensator=None,
    manager=None,
) -> ActionTransactionCoordinator:
    return ActionTransactionCoordinator(
        workspace_root=root,
        client_uid="client-one",
        tool_manager=manager or _Manager(),
        verifier=verifier,
        compensator=compensator,
        journal=ActionTransactionJournal(root),
    )


def _approve_pending(coordinator: ActionTransactionCoordinator) -> str:
    snapshot = coordinator.journal.pending_snapshot()
    assert snapshot["pending_count"] == 1
    plan_id = snapshot["pending"][0]["plan_id"]
    decided = coordinator.journal.decide(plan_id, "approved")
    assert decided is not None
    assert decided["status"] == "approved"
    return plan_id


async def test_exact_confirmation_commit_verification_and_duplicate_suppression(
    tmp_path,
):
    coordinator = _coordinator(tmp_path)
    calls = 0
    target = tmp_path / "notes" / "demo.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("delete me", encoding="utf-8")
    arguments = {"path": "notes/demo.txt"}

    async def invoke():
        nonlocal calls
        calls += 1
        target.unlink()
        return {
            "metadata": {},
            "content_items": [{"type": "text", "text": "OK: wrote file"}],
        }

    proposed = await coordinator.execute(
        server_name="bash",
        capability="delete_path",
        tool_id="call-one",
        arguments=arguments,
        invoke=invoke,
    )
    assert proposed.is_error is True
    assert "requires your approval" in proposed.text
    assert proposed.metadata["action_transaction"]["state"] == (
        "pending_confirmation"
    )
    assert proposed.metadata["action_transaction"]["transaction_id"].startswith(
        "txn:"
    )
    assert calls == 0
    _approve_pending(coordinator)

    committed = await coordinator.execute(
        server_name="bash",
        capability="delete_path",
        tool_id="call-one",
        arguments=arguments,
        invoke=invoke,
    )
    replayed = await coordinator.execute(
        server_name="bash",
        capability="delete_path",
        tool_id="call-one",
        arguments=arguments,
        invoke=invoke,
    )

    assert committed.is_error is False
    assert committed.metadata["action_transaction"]["state"] == "succeeded"
    assert replayed.is_error is False
    assert "duplicate delivery had no effect" in replayed.text
    assert calls == 1
    transaction = coordinator.journal.list_transactions()[0]
    assert transaction["status"] == "succeeded"
    assert [item["terminal_outcome"] for item in transaction["attempts"]] == [
        "denied",
        "succeeded",
        "replayed",
    ]


async def test_confirmation_binds_exact_arguments_but_can_link_a_new_delivery_id(
    tmp_path,
):
    coordinator = _coordinator(tmp_path)

    async def invoke():
        target = tmp_path / active_arguments["path"]
        target.unlink()
        return {
            "metadata": {},
            "content_items": [{"type": "text", "text": "OK"}],
        }

    (tmp_path / "one.txt").write_text("one", encoding="utf-8")
    (tmp_path / "two.txt").write_text("two", encoding="utf-8")
    active_arguments = {"path": "one.txt"}
    first = dict(active_arguments)
    await coordinator.execute(
        server_name="bash",
        capability="delete_path",
        tool_id="proposal-delivery",
        arguments=first,
        invoke=invoke,
    )
    _approve_pending(coordinator)
    committed = await coordinator.execute(
        server_name="bash",
        capability="delete_path",
        tool_id="retry-delivery",
        arguments=first,
        invoke=invoke,
    )
    changed = await coordinator.execute(
        server_name="bash",
        capability="delete_path",
        tool_id="retry-delivery",
        arguments={"path": "two.txt"},
        invoke=invoke,
    )

    assert committed.is_error is False
    assert changed.is_error is True
    assert "requires your approval" in changed.text
    assert len(coordinator.journal.list_transactions()) == 2
    assert coordinator.journal.pending_snapshot()["pending_count"] == 1


async def test_unreviewed_dynamic_mutation_stays_blocked_even_if_user_approves(
    tmp_path,
):
    coordinator = _coordinator(tmp_path)
    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        return {"metadata": {}, "content_items": []}

    arguments = {"payload": "untrusted"}
    await coordinator.execute(
        server_name="dynamic",
        capability="mystery_write",
        tool_id="unknown-one",
        arguments=arguments,
        invoke=invoke,
    )
    _approve_pending(coordinator)
    denied = await coordinator.execute(
        server_name="dynamic",
        capability="mystery_write",
        tool_id="unknown-one",
        arguments=arguments,
        invoke=invoke,
    )

    assert denied.is_error is True
    assert "no reviewed transaction adapter" in denied.text
    assert calls == 0


async def test_adapter_error_after_commit_is_unresolved_and_never_retried(tmp_path):
    coordinator = _coordinator(tmp_path)
    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        return {
            "metadata": {},
            "content_items": [{"type": "text", "text": "Error: timed out"}],
        }

    arguments = {"path": "uncertain.txt"}
    await coordinator.execute(
        server_name="bash",
        capability="delete_path",
        tool_id="uncertain",
        arguments=arguments,
        invoke=invoke,
    )
    _approve_pending(coordinator)
    first = await coordinator.execute(
        server_name="bash",
        capability="delete_path",
        tool_id="uncertain",
        arguments=arguments,
        invoke=invoke,
    )
    second = await coordinator.execute(
        server_name="bash",
        capability="delete_path",
        tool_id="uncertain",
        arguments=arguments,
        invoke=invoke,
    )

    assert first.is_error is True
    assert first.metadata["action_transaction"]["state"] == "unresolved"
    assert second.is_error is True
    assert "will not be retried" in second.text
    assert calls == 1


async def test_spatial_denial_is_a_terminal_policy_attempt_not_an_early_bypass(
    tmp_path,
):
    coordinator = _coordinator(tmp_path, manager=_Manager(reachable=()))
    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        return {"metadata": {}, "content_items": []}

    result = await coordinator.execute(
        server_name="bash",
        capability="write_file",
        tool_id="too-far",
        arguments={"path": "far.txt", "content": "no effect"},
        invoke=invoke,
    )

    assert result.is_error is True
    assert calls == 0
    transaction = coordinator.journal.list_transactions()[0]
    assert transaction["status"] == "approved"
    assert transaction["attempts"][0]["terminal_outcome"] == "denied"
    failed = {
        check["check"]
        for check in transaction["attempts"][0]["decision"]["checks"]
        if not check["passed"]
    }
    assert failed == {"spatial"}


async def test_move_to_commits_without_presence_when_saved_scene_verifies(tmp_path):
    async def verifier(_descriptor, profile, arguments, _result):
        assert profile.capability == "move_to"
        assert arguments == {"target": "calendar"}
        return {
            "verified": True,
            "kind": "persisted_frontend_state",
            "evidence_digest": "a" * 64,
        }

    coordinator = _coordinator(tmp_path, verifier=verifier)

    async def invoke():
        return {
            "metadata": {},
            "content_items": [{"type": "text", "text": "OK: arrived"}],
        }

    result = await coordinator.execute(
        server_name="scene",
        capability="move_to",
        tool_id="move-calendar",
        arguments={"target": "calendar"},
        invoke=invoke,
    )

    assert result.is_error is False
    assert result.metadata["action_transaction"]["state"] == "succeeded"


async def test_move_to_is_unresolved_when_saved_scene_does_not_verify(tmp_path):
    async def verifier(_descriptor, _profile, _arguments, _result):
        return {
            "verified": False,
            "kind": "persisted_frontend_state",
            "evidence_digest": "b" * 64,
        }

    coordinator = _coordinator(tmp_path, verifier=verifier)

    async def invoke():
        return {
            "metadata": {},
            "content_items": [{"type": "text", "text": "OK: arrived"}],
        }

    result = await coordinator.execute(
        server_name="scene",
        capability="move_to",
        tool_id="move-calendar",
        arguments={"target": "calendar"},
        invoke=invoke,
    )

    assert result.is_error is True
    assert result.metadata["action_transaction"]["state"] == "unresolved"


async def test_optional_frontend_verifier_does_not_shadow_local_verification(
    tmp_path,
):
    async def frontend_only_verifier(_descriptor, _profile, _arguments, _result):
        return None

    coordinator = _coordinator(tmp_path, verifier=frontend_only_verifier)
    target = tmp_path / "verified.txt"

    async def invoke():
        target.write_text("saved", encoding="utf-8")
        return {
            "metadata": {},
            "content_items": [{"type": "text", "text": "OK: wrote file"}],
        }

    result = await coordinator.execute(
        server_name="bash",
        capability="write_file",
        tool_id="write-verified",
        arguments={"path": "verified.txt", "content": "saved"},
        invoke=invoke,
    )

    assert result.is_error is False
    assert result.metadata["action_transaction"]["state"] == "succeeded"


async def test_failed_verification_can_be_compensated_with_bounded_evidence(tmp_path):
    compensation_calls = 0

    async def verifier(_descriptor, _profile, _arguments, _result):
        return {
            "verified": False,
            "kind": "test_postcondition",
            "evidence_digest": "a" * 64,
        }

    async def compensator(_descriptor, _profile, _arguments):
        nonlocal compensation_calls
        compensation_calls += 1
        return {
            "compensated": True,
            "kind": "test_compensation",
            "evidence_digest": "b" * 64,
        }

    coordinator = _coordinator(
        tmp_path,
        verifier=verifier,
        compensator=compensator,
    )

    async def invoke():
        return {
            "metadata": {},
            "content_items": [{"type": "text", "text": "OK"}],
        }

    arguments = {"path": "compensate.txt", "content": "effect"}
    result = await coordinator.execute(
        server_name="bash",
        capability="write_file",
        tool_id="compensate",
        arguments=arguments,
        invoke=invoke,
    )

    assert result.is_error is True
    assert result.metadata["action_transaction"]["state"] == "compensated"
    assert compensation_calls == 1
    transaction = coordinator.journal.list_transactions()[0]
    assert transaction["compensation"]["compensated"] is True


async def test_restart_marks_an_interrupted_commit_unresolved(tmp_path):
    coordinator = _coordinator(tmp_path)
    arguments = {"path": "crash.txt"}

    async def never_called():
        return {"metadata": {}, "content_items": []}

    await coordinator.execute(
        server_name="bash",
        capability="delete_path",
        tool_id="crash",
        arguments=arguments,
        invoke=never_called,
    )
    _approve_pending(coordinator)

    async def crash_after_claim():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        await coordinator.execute(
            server_name="bash",
            capability="delete_path",
            tool_id="crash",
            arguments=arguments,
            invoke=crash_after_claim,
        )

    before = coordinator.journal.list_transactions()[0]
    assert before["status"] == "committing"
    recovered = ActionTransactionJournal(tmp_path).list_transactions()[0]
    assert recovered["status"] == "unresolved"
    assert recovered["attempts"][-1]["reason_code"] == (
        "process_interrupted_after_commit_boundary"
    )


def test_journal_detects_hash_chain_tampering(tmp_path):
    journal = ActionTransactionJournal(tmp_path)
    assert journal.list_transactions() == []
    path = tmp_path / "authorization" / "transactions.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sequence": 1,
                "previous_entry_hash": "0" * 64,
                "entry_hash": "f" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(AuthorizationJournalError, match="digest"):
        ActionTransactionJournal(tmp_path)


async def test_journal_rejects_rehashed_unknown_attempt_fields(tmp_path):
    coordinator = _coordinator(tmp_path)

    async def invoke():
        return {
            "metadata": {},
            "content_items": [{"type": "text", "text": "OK"}],
        }

    await coordinator.execute(
        server_name="bash",
        capability="write_file",
        tool_id="strict-schema",
        arguments={"path": "schema.txt", "content": "data"},
        invoke=invoke,
    )
    path = tmp_path / "authorization" / "transactions.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    entry = entries[-1]
    entry["transaction"]["attempts"][-1]["private_output"] = "must not persist"
    material = {key: value for key, value in entry.items() if key != "entry_hash"}
    entry["entry_hash"] = hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    entries[-1] = entry
    path.write_text(
        "".join(
            json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
            for item in entries
        ),
        encoding="utf-8",
    )

    with pytest.raises(AuthorizationJournalError, match="attempt fields"):
        ActionTransactionJournal(tmp_path)


async def test_reversible_external_actions_commit_without_user_confirmation(
    tmp_path,
):
    async def verifier(_descriptor, _profile, _arguments, _result):
        return {"verified": True, "kind": "adapter_evidence", "evidence_digest": "a" * 64}

    coordinator = _coordinator(tmp_path, verifier=verifier)
    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        return {"metadata": {}, "content_items": [{"type": "text", "text": "ok"}]}

    for capability, arguments in (
        ("archive_email", {"message_id": "m1"}),
        ("mark_email_read", {"message_id": "m1"}),
    ):
        result = await coordinator.execute(
            server_name="gmail",
            capability=capability,
            tool_id=f"direct-{capability}",
            arguments=arguments,
            invoke=invoke,
        )
        assert result.is_error is False, capability

    assert calls == 2
    assert coordinator.journal.pending_snapshot()["pending_count"] == 0


async def test_sandboxed_commands_commit_without_user_confirmation(tmp_path):
    async def verifier(_descriptor, _profile, _arguments, _result):
        return {"verified": True, "kind": "adapter_evidence", "evidence_digest": "a" * 64}

    coordinator = _coordinator(tmp_path, verifier=verifier)
    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        return {"metadata": {}, "content_items": [{"type": "text", "text": "ok"}]}

    result = await coordinator.execute(
        server_name="bash",
        capability="execute_bash",
        tool_id="run-1",
        arguments={"command": "echo hi"},
        invoke=invoke,
    )
    assert result.is_error is False
    assert calls == 1
    assert coordinator.journal.pending_snapshot()["pending_count"] == 0


async def test_communicative_and_irreversible_actions_still_need_confirmation(
    tmp_path,
):
    coordinator = _coordinator(tmp_path)
    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        return {"metadata": {}, "content_items": [{"type": "text", "text": "ok"}]}

    for capability, arguments in (
        ("send_email", {"to": "a@example.com", "subject": "Hi", "body": "Hello"}),
        ("delete_path", {"path": "notes/demo.txt"}),
    ):
        result = await coordinator.execute(
            server_name="gmail" if capability == "send_email" else "bash",
            capability=capability,
            tool_id=f"blocked-{capability}",
            arguments=arguments,
            invoke=invoke,
        )
        assert result.is_error is True, capability
        assert "requires your approval" in result.text, capability
        assert calls == 0, capability

    assert coordinator.journal.pending_snapshot()["pending_count"] == 2
