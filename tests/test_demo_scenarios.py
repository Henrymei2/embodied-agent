import asyncio
import json

import pytest

from src.open_llm_vtuber.autonomy.agenda import AgendaStore, create_entry
from src.open_llm_vtuber.demo_scenarios import (
    EMAIL_CHANGES_PLAN,
    ScenarioConflictError,
    ScenarioRun,
    ScenarioStore,
    cancel_scenario,
    record_simulated_write,
    scenario_state_payload,
    simulated_fixture,
    start_scenario,
)


class Executive:
    def __init__(self, store: AgendaStore):
        self.store = store
        self.abandoned: list[str] = []
        self.wakes = 0

    async def abandon(self, entry_id: str):
        self.abandoned.append(entry_id)
        self.store.transition(
            entry_id, "abandoned", progress_summary="Stopped for scenario setup."
        )

    def wake(self):
        self.wakes += 1


def entry(kind="pursuit", status="queued"):
    return create_entry(
        kind=kind,
        title="Existing work",
        description="Existing work must not leak into a scenario.",
        origin="autonomous" if kind != "user" else "user",
        evidence_refs=["test:existing"] if kind != "user" else [],
        completion_condition="Finish.",
        time_budget_seconds=60,
        status=status,
    )


def test_catalog_exposes_only_the_authored_demo_scenario(tmp_path):
    payload = scenario_state_payload(AgendaStore(tmp_path))
    assert payload["version"] == 1
    assert [item["id"] for item in payload["scenarios"]] == [EMAIL_CHANGES_PLAN.id]
    assert payload["scenarios"][0]["simulated"] is True
    assert "runner" not in payload["scenarios"][0]


def test_start_resets_only_after_safe_agenda_cleanup(tmp_path, monkeypatch):
    agenda = AgendaStore(tmp_path)
    old = agenda.add(entry())
    terminal = agenda.add(entry(status="completed"))
    resets = []
    monkeypatch.setattr(
        "src.open_llm_vtuber.demo_scenarios.reset_scene_state",
        lambda conf_uid, mode: resets.append((conf_uid, mode)),
    )
    executive = Executive(agenda)

    run = asyncio.run(
        start_scenario(
            scenario_id=EMAIL_CHANGES_PLAN.id,
            request_id="request-one",
            conf_uid="character-a",
            agenda=agenda,
            executive=executive,
        )
    )

    assert resets == [("character-a", "vr")]
    assert executive.abandoned == [old.id]
    assert (
        next(item for item in agenda.load().entries if item.id == terminal.id).status
        == "completed"
    )
    assert run.phase == "waiting_event"
    assert run.event_id
    assert run.background_entry_id
    assert executive.wakes == 1

    repeated = asyncio.run(
        start_scenario(
            scenario_id=EMAIL_CHANGES_PLAN.id,
            request_id="request-one",
            conf_uid="character-a",
            agenda=agenda,
            executive=executive,
        )
    )
    assert repeated.run_id == run.run_id
    assert resets == [("character-a", "vr")]


def test_user_work_blocks_start_without_reset(tmp_path, monkeypatch):
    agenda = AgendaStore(tmp_path)
    agenda.add(entry(kind="user", status="active"))

    def reset(*_args):
        pytest.fail("room must not reset")

    monkeypatch.setattr("src.open_llm_vtuber.demo_scenarios.reset_scene_state", reset)

    with pytest.raises(ScenarioConflictError, match="conversation"):
        asyncio.run(
            start_scenario(
                scenario_id=EMAIL_CHANGES_PLAN.id,
                request_id="blocked",
                conf_uid="character-a",
                agenda=agenda,
                executive=Executive(agenda),
            )
        )
    assert ScenarioStore(tmp_path).load() is None


def test_cancel_withdraws_event_and_abandons_owned_background_work(
    tmp_path, monkeypatch
):
    agenda = AgendaStore(tmp_path)
    monkeypatch.setattr(
        "src.open_llm_vtuber.demo_scenarios.reset_scene_state", lambda *_args: {}
    )
    executive = Executive(agenda)
    run = asyncio.run(
        start_scenario(
            scenario_id=EMAIL_CHANGES_PLAN.id,
            request_id="start",
            conf_uid="character-a",
            agenda=agenda,
            executive=executive,
        )
    )

    cancelled = asyncio.run(
        cancel_scenario(
            run_id=run.run_id,
            request_id="cancel",
            agenda=agenda,
            executive=executive,
        )
    )
    state = agenda.load()
    assert cancelled.phase == "cancelled"
    assert all(item.id != run.event_id for item in state.inbox)
    background = next(
        item for item in state.entries if item.id == run.background_entry_id
    )
    assert background.status == "abandoned"


def test_simulated_google_data_and_writes_are_workspace_local(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    store = ScenarioStore(tmp_path)
    store.save(
        ScenarioRun(
            run_id="run-one",
            scenario_id=EMAIL_CHANGES_PLAN.id,
            conf_uid="character-a",
            phase="running",
            progress_summary="Running",
            start_request_id="request-one",
        )
    )
    fixture = simulated_fixture()
    assert fixture["simulated"] is True
    assert "3:30 PM" in fixture["email"]["body"]
    record = record_simulated_write("gmail", {"action": "create_draft"})
    assert record["simulated"] is True
    writes = json.loads(
        (tmp_path / "agenda" / "vr_demo_scenario_writes.json").read_text()
    )
    assert writes == [record]


def test_simulated_google_tools_never_construct_a_real_service(tmp_path, monkeypatch):
    from mcp_servers import calendar_tools, gmail_tools

    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    ScenarioStore(tmp_path).save(
        ScenarioRun(
            run_id="run-tools",
            scenario_id=EMAIL_CHANGES_PLAN.id,
            conf_uid="character-a",
            phase="running",
            progress_summary="Running",
            start_request_id="request-tools",
        )
    )
    monkeypatch.setattr(
        gmail_tools, "_messages", lambda: pytest.fail("real Gmail accessed")
    )
    monkeypatch.setattr(
        calendar_tools,
        "_get_calendar_service",
        lambda: pytest.fail("real Calendar accessed"),
    )

    gmail = json.loads(gmail_tools.search_emails("is:unread"))
    calendar = json.loads(calendar_tools.list_calendar_events())
    draft = json.loads(
        gmail_tools.create_email_draft(
            "jordan@example.test",
            "3:30 works",
            "Could we move it?",
        )
    )
    assert gmail["simulated"] is True
    assert calendar["simulated"] is True
    assert draft["simulated"] is True
