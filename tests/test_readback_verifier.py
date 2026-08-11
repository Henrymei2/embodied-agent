"""Readback verification for simulated remote writes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.open_llm_vtuber.authorization import (
    ActionTransactionCoordinator,
    ActionTransactionJournal,
)
from src.open_llm_vtuber.authorization.profiles import ACTION_PROFILES
from src.open_llm_vtuber.authorization.readback_verifier import (
    build_workspace_readback_verifier,
)
from src.open_llm_vtuber.demo_scenarios import (
    EMAIL_CHANGES_PLAN,
    ScenarioRun,
    ScenarioStore,
)


def _activate_scenario(root: Path) -> None:
    ScenarioStore(root).save(
        ScenarioRun(
            run_id="run-verify",
            scenario_id=EMAIL_CHANGES_PLAN.id,
            conf_uid="character-a",
            phase="running",
            progress_summary="Running",
            start_request_id="verify",
        )
    )


def _write_record(root: Path, record: dict) -> None:
    path = root / "agenda" / "vr_demo_scenario_writes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([record]), encoding="utf-8")


def _draft_record(*, draft_id: str = "simulated-abc", subject: str = "3:30 works"):
    return {
        "id": draft_id,
        "kind": "gmail",
        "payload": {
            "action": "create_draft",
            "to": "jordan@example.test",
            "subject": subject,
            "body": "Would 3:30 PM work?",
            "cc": "",
            "bcc": "",
        },
        "simulated": True,
        "created_at": "2026-08-10T18:37:00+00:00",
    }


def _draft_result(draft_id: str = "simulated-abc") -> dict:
    return {
        "metadata": {},
        "content_items": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "ok": True,
                        "draft_id": draft_id,
                        "message_id": "",
                        "simulated": True,
                    }
                ),
            }
        ],
    }


@pytest.mark.asyncio
async def test_simulated_draft_readback_verifies_matching_record(tmp_path):
    _activate_scenario(tmp_path)
    _write_record(tmp_path, _draft_record())
    verifier = build_workspace_readback_verifier(tmp_path)

    result = await verifier(
        SimpleNamespace(digest="ignored"),
        ACTION_PROFILES["create_email_draft"],
        {
            "to": "jordan@example.test",
            "subject": "3:30 works",
            "body": "Would 3:30 PM work?",
        },
        _draft_result(),
    )

    assert result is not None
    assert result["verified"] is True
    assert result["kind"] == "workspace_readback"
    assert result["evidence_digest"]


@pytest.mark.asyncio
async def test_simulated_draft_readback_rejects_mismatched_record(tmp_path):
    _activate_scenario(tmp_path)
    _write_record(tmp_path, _draft_record(subject="something else"))
    verifier = build_workspace_readback_verifier(tmp_path)

    result = await verifier(
        SimpleNamespace(digest="ignored"),
        ACTION_PROFILES["create_email_draft"],
        {
            "to": "jordan@example.test",
            "subject": "3:30 works",
            "body": "Would 3:30 PM work?",
        },
        _draft_result(),
    )

    assert result is not None
    assert result["verified"] is False


@pytest.mark.asyncio
async def test_simulated_draft_readback_binds_to_returned_receipt(tmp_path):
    _activate_scenario(tmp_path)
    _write_record(tmp_path, _draft_record(draft_id="simulated-other"))
    verifier = build_workspace_readback_verifier(tmp_path)

    result = await verifier(
        SimpleNamespace(digest="ignored"),
        ACTION_PROFILES["create_email_draft"],
        {
            "to": "jordan@example.test",
            "subject": "3:30 works",
            "body": "Would 3:30 PM work?",
        },
        _draft_result(draft_id="simulated-abc"),
    )

    assert result is not None
    assert result["verified"] is False


@pytest.mark.asyncio
async def test_simulated_verifier_stays_inactive_outside_scenario(tmp_path):
    _write_record(tmp_path, _draft_record())
    verifier = build_workspace_readback_verifier(tmp_path)

    result = await verifier(
        SimpleNamespace(digest="ignored"),
        ACTION_PROFILES["create_email_draft"],
        {"to": "jordan@example.test", "subject": "3:30 works"},
        _draft_result(),
    )

    assert result is None


@pytest.mark.asyncio
async def test_simulated_verifier_ignores_non_readback_profiles(tmp_path):
    _activate_scenario(tmp_path)
    verifier = build_workspace_readback_verifier(tmp_path)

    result = await verifier(
        SimpleNamespace(digest="ignored"),
        ACTION_PROFILES["move_to"],
        {"target": "mailbox"},
        {"metadata": {}, "content_items": []},
    )

    assert result is None


@pytest.mark.asyncio
async def test_simulated_modify_readback_matches_label_direction(tmp_path):
    _activate_scenario(tmp_path)
    _write_record(
        tmp_path,
        {
            "id": "simulated-modify",
            "kind": "gmail",
            "payload": {
                "action": "modify",
                "message_id": "scenario-meeting-moved",
                "add": [],
                "remove": ["UNREAD"],
            },
            "simulated": True,
            "created_at": "2026-08-10T18:37:00+00:00",
        },
    )
    verifier = build_workspace_readback_verifier(tmp_path)

    read = await verifier(
        SimpleNamespace(digest="ignored"),
        ACTION_PROFILES["mark_email_read"],
        {"message_id": "scenario-meeting-moved"},
        {
            "metadata": {},
            "content_items": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "ok": True,
                            "message_id": "scenario-meeting-moved",
                            "record_id": "simulated-modify",
                            "simulated": True,
                        }
                    ),
                }
            ],
        },
    )
    unread = await verifier(
        SimpleNamespace(digest="ignored"),
        ACTION_PROFILES["mark_email_unread"],
        {"message_id": "scenario-meeting-moved"},
        {
            "metadata": {},
            "content_items": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "ok": True,
                            "message_id": "scenario-meeting-moved",
                            "record_id": "simulated-modify",
                            "simulated": True,
                        }
                    ),
                }
            ],
        },
    )

    assert read["verified"] is True
    assert unread["verified"] is False


@pytest.mark.asyncio
async def test_coordinator_commits_simulated_draft_with_readback(tmp_path):
    _activate_scenario(tmp_path)
    verifier = build_workspace_readback_verifier(tmp_path)
    coordinator = ActionTransactionCoordinator(
        workspace_root=tmp_path,
        client_uid="client-one",
        tool_manager=SimpleNamespace(
            authorize_tool=lambda _capability: (True, None),
            get_required_object=lambda _capability: "mailbox",
            get_reachable_objects=lambda: ["mailbox"],
        ),
        verifier=verifier,
        journal=ActionTransactionJournal(tmp_path),
    )

    async def invoke():
        _write_record(tmp_path, _draft_record())
        return _draft_result()

    result = await coordinator.execute(
        server_name="gmail",
        capability="create_email_draft",
        tool_id="call-draft",
        arguments={
            "to": "jordan@example.test",
            "subject": "3:30 works",
            "body": "Would 3:30 PM work?",
        },
        invoke=invoke,
    )

    assert result.is_error is False
    assert result.metadata["action_transaction"]["state"] == "succeeded"


def _write_plan_store(root: Path, plans: list[dict]) -> None:
    path = root / "plans" / "plans.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-08-10T18:37:00+00:00",
                "plans": plans,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_plan_readback_verifies_pending_plan(tmp_path):
    _write_plan_store(
        tmp_path,
        [
            {
                "plan_id": "plan-abc",
                "title": "Confirm 3:30 PM",
                "goal": "Resolve the sync conflict",
                "steps": ["Reply to Jordan"],
                "status": "pending",
            }
        ],
    )
    verifier = build_workspace_readback_verifier(tmp_path)

    result = await verifier(
        SimpleNamespace(digest="ignored"),
        ACTION_PROFILES["write_plan"],
        {
            "title": "Confirm 3:30 PM",
            "goal": "Resolve the sync conflict",
            "steps": ["Reply to Jordan"],
        },
        {"metadata": {}, "content_items": []},
    )

    assert result is not None
    assert result["verified"] is True
    assert result["kind"] == "workspace_readback"


@pytest.mark.asyncio
async def test_plan_readback_rejects_when_plan_missing(tmp_path):
    _write_plan_store(
        tmp_path,
        [
            {
                "plan_id": "plan-abc",
                "title": "Some other plan",
                "goal": "Something else",
                "steps": ["Do it"],
                "status": "pending",
            }
        ],
    )
    verifier = build_workspace_readback_verifier(tmp_path)

    result = await verifier(
        SimpleNamespace(digest="ignored"),
        ACTION_PROFILES["write_plan"],
        {
            "title": "Confirm 3:30 PM",
            "goal": "Resolve the sync conflict",
            "steps": ["Reply to Jordan"],
        },
        {"metadata": {}, "content_items": []},
    )

    assert result is not None
    assert result["verified"] is False


@pytest.mark.asyncio
async def test_coordinator_commits_write_plan_with_readback(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    verifier = build_workspace_readback_verifier(tmp_path)
    coordinator = ActionTransactionCoordinator(
        workspace_root=tmp_path,
        client_uid="client-one",
        tool_manager=SimpleNamespace(
            authorize_tool=lambda _capability: (True, None),
            get_required_object=lambda _capability: "writing desk",
            get_reachable_objects=lambda: ["writing desk"],
        ),
        verifier=verifier,
        journal=ActionTransactionJournal(tmp_path),
    )

    from mcp_servers import plan_tools

    async def invoke():
        text = plan_tools.write_plan(
            "Confirm 3:30 PM",
            "Resolve the sync conflict",
            ["Reply to Jordan"],
        )
        return {"metadata": {}, "content_items": [{"type": "text", "text": text}]}

    result = await coordinator.execute(
        server_name="plan",
        capability="write_plan",
        tool_id="call-plan",
        arguments={
            "title": "Confirm 3:30 PM",
            "goal": "Resolve the sync conflict",
            "steps": ["Reply to Jordan"],
        },
        invoke=invoke,
    )

    assert result.is_error is False
    assert result.metadata["action_transaction"]["state"] == "succeeded"
