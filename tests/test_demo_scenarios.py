import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.open_llm_vtuber.autonomy.agenda import AgendaStore, create_entry
from src.open_llm_vtuber.autonomy.agenda_context import build_agenda_context
from src.open_llm_vtuber.demo_scenarios import (
    EMAIL_CHANGES_PLAN,
    PREPARE_PRESENTATION_PRACTICE,
    REMEMBER_UNFINISHED_INTENTION,
    ScenarioConflictError,
    ScenarioRun,
    ScenarioStore,
    cancel_scenario,
    record_simulated_write,
    scenario_state_payload,
    simulated_fixture,
    start_scenario,
)
from src.open_llm_vtuber.autonomy.triggers import trigger_handling_policy
from src.open_llm_vtuber.workspace import build_workspace_prompt_context


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


def test_catalog_exposes_all_authored_demo_scenarios_in_order(tmp_path):
    payload = scenario_state_payload(AgendaStore(tmp_path))
    assert payload["version"] == 1
    assert [item["id"] for item in payload["scenarios"]] == [
        EMAIL_CHANGES_PLAN.id,
        REMEMBER_UNFINISHED_INTENTION.id,
        PREPARE_PRESENTATION_PRACTICE.id,
    ]
    assert all(item["simulated"] is True for item in payload["scenarios"])
    assert all("runner" not in item for item in payload["scenarios"])


def test_scenario_definition_describes_the_full_autonomous_chain(tmp_path):
    scenario = scenario_state_payload(AgendaStore(tmp_path))["scenarios"][0]
    setup_text = " ".join(scenario["setup"])
    sequence_text = " ".join(scenario["sequence"])

    assert "mailbox" in setup_text
    assert "calendar" in setup_text
    assert "writing desk" in setup_text
    assert "bookshelf" in setup_text
    assert "mailbox" in sequence_text
    assert "calendar" in sequence_text
    assert "writing desk" in sequence_text
    assert "draft" in sequence_text
    assert "report" in sequence_text


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
    monkeypatch.setattr(
        "src.open_llm_vtuber.demo_scenarios.get_local_tz",
        lambda: ZoneInfo("America/Chicago"),
    )
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
    fixed_now = datetime(2026, 8, 10, 15, 0, tzinfo=ZoneInfo("America/Chicago"))
    fixture = simulated_fixture(now=fixed_now)
    assert fixture["simulated"] is True
    assert fixture["email"]["date"] == "2026-08-10T15:00:00-05:00"
    assert fixture["email"]["snippet"] == (
        "The project sync moved and now overlaps your review."
    )
    assert fixture["events"][0]["event_id"] == "scenario-review-conflict"
    assert fixture["events"][0]["start"] == "2026-08-11T15:00:00-05:00"
    assert fixture["events"][0]["end"] == "2026-08-11T15:30:00-05:00"
    assert "2026-08-11" in fixture["email"]["body"]
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
    read = json.loads(
        gmail_tools.get_email(
            "89bc0a7350259e2d6c9d7744216b5fef0f3227a0620c125478fec7123991b37d"
        )
    )
    calendar = json.loads(calendar_tools.list_calendar_events())
    draft = json.loads(
        gmail_tools.create_email_draft(
            "jordan@example.test",
            "3:30 works",
            "Could we move it?",
        )
    )
    assert gmail["simulated"] is True
    assert read["simulated"] is True
    assert read["message_id"] == "scenario-meeting-moved"
    assert calendar["simulated"] is True
    assert draft["simulated"] is True


def test_finished_paper_setup_uses_core_memory_and_restores_fixtures(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(
        "src.open_llm_vtuber.demo_scenarios.reset_scene_state", lambda *_args: {}
    )
    original_memory = "# Existing memory\n" + ("existing detail " * 300)
    (tmp_path / "MEMORY.md").write_text(original_memory, encoding="utf-8")
    (tmp_path / "final_paper.md").write_text("original paper\n", encoding="utf-8")
    agenda = AgendaStore(tmp_path)
    executive = Executive(agenda)

    run = asyncio.run(
        start_scenario(
            scenario_id=REMEMBER_UNFINISHED_INTENTION.id,
            request_id="paper-start",
            conf_uid="character-a",
            agenda=agenda,
            executive=executive,
        )
    )

    memory = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "send a message to Alex (alex@example.test)" in memory
    assert list((tmp_path / "memory").glob("*.md")) == []
    assert "ready for submission" in (tmp_path / "final_paper.md").read_text()
    event = next(item for item in agenda.load().inbox if item.id == run.event_id)
    policy = trigger_handling_policy(event)
    assert policy is not None
    assert policy.policy_id == "demo-context-only-v1"
    assert policy.use_triggered_planner is False
    assert policy.directive == ""
    assert policy.completion_condition == ""

    prompt = build_workspace_prompt_context(
        workspace_dir=tmp_path,
        include_retrieved_documents=False,
    )
    assert "send a message to Alex (alex@example.test)" in prompt
    planner_context = build_agenda_context(agenda, client_uid="paper-planner")
    assert any(
        passage["ref"] == "memory:MEMORY.md"
        and "send a message to Alex (alex@example.test)" in passage["text"]
        for passage in planner_context.payload["memory_passages"]
    )

    repeated = asyncio.run(
        start_scenario(
            scenario_id=REMEMBER_UNFINISHED_INTENTION.id,
            request_id="paper-start",
            conf_uid="character-a",
            agenda=agenda,
            executive=executive,
        )
    )
    assert repeated.run_id == run.run_id
    assert (tmp_path / "MEMORY.md").read_text().count("send a message to Alex") == 1

    asyncio.run(
        cancel_scenario(
            run_id=run.run_id,
            request_id="paper-cancel",
            agenda=agenda,
            executive=executive,
        )
    )
    assert (tmp_path / "MEMORY.md").read_text() == original_memory
    assert (tmp_path / "final_paper.md").read_text() == "original paper\n"


def test_presentation_setup_uses_daily_memory_and_requires_physical_handoff(
    tmp_path, monkeypatch
):
    fixed_now = datetime(2026, 8, 11, 10, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(
        "src.open_llm_vtuber.demo_scenarios.reset_scene_state", lambda *_args: {}
    )
    monkeypatch.setattr(
        "src.open_llm_vtuber.demo_scenarios.now_local", lambda: fixed_now
    )
    daily = tmp_path / "memory" / "2026-08-11.md"
    daily.parent.mkdir(parents=True)
    daily.write_text(
        "# Daily Memory - 2026-08-11\n\nExisting note.\n", encoding="utf-8"
    )
    agenda = AgendaStore(tmp_path)
    executive = Executive(agenda)

    run = asyncio.run(
        start_scenario(
            scenario_id=PREPARE_PRESENTATION_PRACTICE.id,
            request_id="presentation-start",
            conf_uid="character-a",
            agenda=agenda,
            executive=executive,
        )
    )

    assert "important presentation later today" in daily.read_text()
    script = (tmp_path / "presentation_script.md").read_text()
    assert "perceives changes" in script
    assert "approval boundaries" in script
    event = next(item for item in agenda.load().inbox if item.id == run.event_id)
    policy = trigger_handling_policy(event)
    assert policy is not None
    assert policy.policy_id == "demo-context-only-v1"
    assert policy.use_triggered_planner is False
    assert policy.directive == ""
    assert policy.completion_condition == ""

    asyncio.run(
        cancel_scenario(
            run_id=run.run_id,
            request_id="presentation-cancel",
            agenda=agenda,
            executive=executive,
        )
    )
    assert daily.read_text() == "# Daily Memory - 2026-08-11\n\nExisting note.\n"
    assert not (tmp_path / "presentation_script.md").exists()


def test_fixture_cleanup_preserves_a_user_modified_scenario_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(
        "src.open_llm_vtuber.demo_scenarios.reset_scene_state", lambda *_args: {}
    )
    agenda = AgendaStore(tmp_path)
    executive = Executive(agenda)
    run = asyncio.run(
        start_scenario(
            scenario_id=REMEMBER_UNFINISHED_INTENTION.id,
            request_id="modified-start",
            conf_uid="character-a",
            agenda=agenda,
            executive=executive,
        )
    )
    (tmp_path / "final_paper.md").write_text("user revision\n", encoding="utf-8")

    cancelled = asyncio.run(
        cancel_scenario(
            run_id=run.run_id,
            request_id="modified-cancel",
            agenda=agenda,
            executive=executive,
        )
    )

    assert (tmp_path / "final_paper.md").read_text() == "user revision\n"
    assert "Preserved a modified scenario file" in cancelled.progress_summary
    assert list((tmp_path / "agenda").glob("*.recovery.json"))


def test_finished_paper_gmail_is_draft_only_and_never_uses_real_service(
    tmp_path, monkeypatch
):
    from mcp_servers import gmail_tools

    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    ScenarioStore(tmp_path).save(
        ScenarioRun(
            run_id="paper-tools",
            scenario_id=REMEMBER_UNFINISHED_INTENTION.id,
            conf_uid="character-a",
            phase="completed",
            progress_summary="Draft and pending plan are ready.",
            start_request_id="paper-tools-request",
        )
    )
    (tmp_path / "agenda" / "vr_demo_scenario_fixture.json").write_text(
        "{}", encoding="utf-8"
    )
    monkeypatch.setattr(
        gmail_tools, "_messages", lambda: pytest.fail("real Gmail accessed")
    )
    monkeypatch.setattr(
        gmail_tools, "_service", lambda: pytest.fail("real Gmail service constructed")
    )

    search = json.loads(gmail_tools.search_emails())
    draft = json.loads(
        gmail_tools.create_email_draft(
            "alex@example.test",
            "The paper is ready",
            "Hi Alex, the paper is ready.",
        )
    )
    assert search == {"messages": [], "next_page_token": None, "simulated": True}
    assert "no messages" in gmail_tools.get_email("anything")
    assert draft["simulated"] is True
    writes = json.loads(
        (tmp_path / "agenda" / "vr_demo_scenario_writes.json").read_text()
    )
    assert writes[0]["payload"]["to"] == "alex@example.test"
