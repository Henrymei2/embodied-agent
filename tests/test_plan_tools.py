from __future__ import annotations

import json

import pytest

from mcp_servers import plan_tools
from src.open_llm_vtuber import furniture_state


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    return tmp_path


def _write(title: str = "Tidy the knowledge folder") -> dict:
    payload = json.loads(
        plan_tools.write_plan(
            title,
            goal="Notes have piled up and are hard to search.",
            steps=["List the pages", "Merge duplicates", "Write an index"],
        )
    )
    return payload["plan"]


def test_a_new_plan_starts_pending():
    plan = _write()

    assert plan["status"] == "pending"
    assert plan["revision"] == 1
    assert plan["decided_at"] is None
    assert len(plan["steps"]) == 3


def test_the_agent_has_no_tool_that_approves_a_plan():
    """Approval is the user's alone; only the desk's window can grant it."""
    tool_names = {name for name in dir(plan_tools) if not name.startswith("_")}

    assert not {"approve_plan", "decide_plan", "accept_plan"} & tool_names


def test_user_decision_moves_a_plan_out_of_pending():
    plan = _write()

    decided = furniture_state.record_plan_decision(plan["plan_id"], "approved")

    assert decided["status"] == "approved"
    assert decided["decided_by"] == "user"
    listed = json.loads(plan_tools.list_plans(status="approved"))
    assert listed["count"] == 1


def test_a_decision_only_applies_to_a_pending_plan():
    plan = _write()
    furniture_state.record_plan_decision(plan["plan_id"], "declined")

    assert furniture_state.record_plan_decision(plan["plan_id"], "approved") is None
    assert furniture_state.record_plan_decision("missing", "approved") is None
    with pytest.raises(ValueError):
        furniture_state.record_plan_decision(plan["plan_id"], "shipped")


def test_revising_a_declined_plan_asks_again_rather_than_inheriting_approval():
    plan = _write()
    furniture_state.record_plan_decision(plan["plan_id"], "declined")

    revised = json.loads(
        plan_tools.revise_plan(plan["plan_id"], steps=["Write an index only"])
    )["plan"]

    assert revised["status"] == "pending"
    assert revised["revision"] == 2
    assert revised["decided_at"] is None


def test_an_approved_plan_cannot_be_edited():
    plan = _write()
    furniture_state.record_plan_decision(plan["plan_id"], "approved")

    result = plan_tools.revise_plan(plan["plan_id"], title="Something else")

    assert "an approved plan cannot be revised" in result


def test_withdrawing_a_plan_keeps_it_with_its_reason():
    plan = _write()

    plan_tools.withdraw_plan(plan["plan_id"], reason="The user did it themselves.")

    stored = json.loads(plan_tools.get_plan(plan["plan_id"]))["plan"]
    assert stored["status"] == "withdrawn"
    assert stored["withdraw_reason"] == "The user did it themselves."
    assert "already withdrawn" in plan_tools.withdraw_plan(plan["plan_id"])


def test_plans_are_validated_before_they_are_stored():
    assert "steps must be a non-empty list" in plan_tools.write_plan("t", "g", [])
    assert "title cannot be empty" in plan_tools.write_plan("  ", "g", ["s"])
    assert "goal cannot exceed" in plan_tools.write_plan("t", "g" * 5000, ["s"])
    assert furniture_state.load_plans() == []


def test_plan_snapshot_shows_pending_first_and_the_last_decision():
    first = _write("Older plan")
    furniture_state.record_plan_decision(first["plan_id"], "approved")
    _write("Newer plan")

    snapshot = furniture_state.plan_snapshot()

    assert snapshot["pending_count"] == 1
    assert snapshot["pending"][0]["title"] == "Newer plan"
    assert snapshot["recent"]["title"] == "Older plan"
    assert snapshot["recent"]["status"] == "approved"
