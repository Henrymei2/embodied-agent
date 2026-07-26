"""Conditions and the session log that make the presence claim measurable.

Two things have to hold for a study to be worth running: an ablation must
actually remove the pillar it names, and the log must record what happened
without recording what was said.
"""

from __future__ import annotations

import json

import pytest

from src.open_llm_vtuber import study


@pytest.fixture(autouse=True)
def clean_study(monkeypatch, tmp_path):
    monkeypatch.delenv("EMBODIED_AGENT_CONDITION", raising=False)
    monkeypatch.delenv("EMBODIED_AGENT_STUDY_LOG", raising=False)
    monkeypatch.delenv("EMBODIED_AGENT_PARTICIPANT", raising=False)
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    study.reset_for_tests()
    yield
    study.reset_for_tests()


def _events(tmp_path) -> list[dict]:
    logs = sorted((tmp_path / "study").glob("*.jsonl"))
    assert len(logs) == 1, logs
    return [
        json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()
    ]


def test_everything_is_on_and_nothing_is_recorded_by_default(tmp_path):
    """Ordinary use must not become a study, or start writing a log."""
    assert study.conditions().as_dict() == {
        "embodiment": True,
        "memory": True,
        "autonomy": True,
    }
    assert study.conditions().label == "full"
    assert study.logging_enabled() is False

    study.log_event("object_click", object_id="bookshelf")

    assert not (tmp_path / "study").exists()


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("memory=off", {"embodiment": True, "memory": False, "autonomy": True}),
        (
            "embodiment=off,autonomy=off",
            {"embodiment": False, "memory": True, "autonomy": False},
        ),
        (
            "memory=0; autonomy=no",
            {"embodiment": True, "memory": False, "autonomy": False},
        ),
        ("memory=on", {"embodiment": True, "memory": True, "autonomy": True}),
    ],
)
def test_a_condition_is_read_from_the_environment(monkeypatch, spec, expected):
    monkeypatch.setenv("EMBODIED_AGENT_CONDITION", spec)
    study.reset_for_tests()

    assert study.conditions().as_dict() == expected


def test_a_typo_in_a_launcher_does_not_cost_a_session(monkeypatch):
    """Unreadable clauses are ignored, and the log records what was applied."""
    monkeypatch.setenv("EMBODIED_AGENT_CONDITION", "memroy=off,memory=maybe,,")
    study.reset_for_tests()

    assert study.conditions().as_dict() == {
        "embodiment": True,
        "memory": True,
        "autonomy": True,
    }


def test_condition_labels_name_what_is_missing(monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_CONDITION", "memory=off")
    study.reset_for_tests()
    assert study.conditions().label == "no-memory"
    assert study.conditions().is_ablated is True


def test_an_ablated_run_logs_itself_without_being_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_CONDITION", "memory=off")
    study.reset_for_tests()

    assert study.logging_enabled() is True
    study.log_event("object_click", object_id="bookshelf")

    events = _events(tmp_path)
    assert events[0]["event"] == "session_start"
    assert events[0]["condition"] == "no-memory"
    assert events[0]["pillar_memory"] is False
    assert events[1]["event"] == "object_click"
    assert events[1]["object_id"] == "bookshelf"


def test_logging_can_be_turned_on_for_a_full_condition(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_STUDY_LOG", "1")
    study.reset_for_tests()

    study.log_event("object_click", object_id="bed")

    assert _events(tmp_path)[0]["condition"] == "full"


def test_naming_a_participant_is_enough_to_record_the_control(monkeypatch, tmp_path):
    """The unablated condition is the baseline; forgetting to log it wastes it."""
    monkeypatch.setenv("EMBODIED_AGENT_PARTICIPANT", "P02")
    study.reset_for_tests()

    assert study.conditions().is_ablated is False
    assert study.logging_enabled() is True

    study.log_event("object_click", object_id="bed")
    assert _events(tmp_path)[0]["condition"] == "full"


def test_logging_can_be_forced_off_even_when_ablated(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_CONDITION", "memory=off")
    monkeypatch.setenv("EMBODIED_AGENT_STUDY_LOG", "0")
    study.reset_for_tests()

    study.log_event("object_click", object_id="bed")

    assert not (tmp_path / "study").exists()


def test_the_log_cannot_carry_what_the_user_said(monkeypatch, tmp_path):
    """The guard that makes these logs shareable: length, never content."""
    monkeypatch.setenv("EMBODIED_AGENT_STUDY_LOG", "1")
    study.reset_for_tests()
    secret = "my therapist's name is " + "x" * 200

    study.log_event("turn_started", source="text", oops=secret)

    written = sorted((tmp_path / "study").glob("*.jsonl"))[0].read_text(
        encoding="utf-8"
    )
    assert "therapist" not in written
    assert _events(tmp_path)[-1]["oops"] == {"chars": len(secret)}


def test_a_participant_is_recorded_and_names_the_file(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_STUDY_LOG", "1")
    monkeypatch.setenv("EMBODIED_AGENT_PARTICIPANT", "P07")
    study.reset_for_tests()

    study.log_event("object_click", object_id="bed")

    log = sorted((tmp_path / "study").glob("*.jsonl"))[0]
    assert "P07" in log.name
    assert _events(tmp_path)[0]["participant"] == "P07"


def test_turns_record_how_long_the_user_took_to_answer(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_STUDY_LOG", "1")
    study.reset_for_tests()

    study.log_turn_started("text")
    study.log_turn_finished(reply_chars=120)
    study.log_turn_started("object_click")

    events = [event for event in _events(tmp_path) if event["event"].startswith("turn")]
    assert events[0]["seconds_since_agent_finished"] is None
    assert events[1]["reply_chars"] == 120
    assert events[1]["duration_seconds"] >= 0
    # The second turn knows how long the user left the agent waiting.
    assert events[2]["seconds_since_agent_finished"] >= 0


def test_a_session_ends_with_its_duration(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_STUDY_LOG", "1")
    study.reset_for_tests()

    study.log_event("client_connected")
    study.end_session()

    last = _events(tmp_path)[-1]
    assert last["event"] == "session_end"
    assert last["duration_seconds"] >= 0


def test_reloading_the_page_does_not_split_one_sitting_in_two(monkeypatch, tmp_path):
    """Two short sessions instead of one would halve every per-session measure."""
    monkeypatch.setenv("EMBODIED_AGENT_STUDY_LOG", "1")
    study.reset_for_tests()

    study.log_event("client_connected")
    study.end_session()  # the participant reloads
    study.log_event("client_connected")
    study.log_turn_started("text")
    study.end_session()  # and closes the window for real

    # One file, one sitting, and the last duration is the whole of it.
    assert len(list((tmp_path / "study").glob("*.jsonl"))) == 1
    events = _events(tmp_path)
    assert [event["event"] for event in events].count("session_start") == 1
    ends = [event for event in events if event["event"] == "session_end"]
    assert ends[-1]["duration_seconds"] >= ends[0]["duration_seconds"]
    # The turn taken after the reload belongs to the same session.
    assert events[-2]["event"] == "turn_started"


def test_an_unwritable_log_never_takes_the_session_down(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBODIED_AGENT_STUDY_LOG", "1")
    study.reset_for_tests()

    def explode(*_args, **_kwargs):
        raise OSError("disk is gone")

    monkeypatch.setattr(study.Path, "mkdir", explode)

    study.log_event("object_click", object_id="bed")
    study.end_session()


def test_the_memory_ablation_removes_recall_but_not_the_character(
    monkeypatch, tmp_path
):
    from src.open_llm_vtuber.workspace import build_workspace_prompt_context

    workspace = tmp_path / "ws"
    (workspace / "memory").mkdir(parents=True)
    (workspace / "AGENT.md").write_text("You are Mao.", encoding="utf-8")
    (workspace / "MEMORY.md").write_text(
        "The user has a cat named Pep.", encoding="utf-8"
    )
    (workspace / "memory" / "2026-07-20.md").write_text(
        "We talked about the dishwasher.", encoding="utf-8"
    )

    monkeypatch.setenv("EMBODIED_AGENT_EMBEDDINGS", "off")
    study.reset_for_tests()
    full = build_workspace_prompt_context("dishwasher", workspace_dir=workspace)
    assert "You are Mao." in full
    assert "Pep" in full
    assert "dishwasher" in full

    monkeypatch.setenv("EMBODIED_AGENT_CONDITION", "memory=off")
    study.reset_for_tests()
    ablated = build_workspace_prompt_context("dishwasher", workspace_dir=workspace)

    # Same character, same rules, no accumulated past.
    assert "You are Mao." in ablated
    assert "Pep" not in ablated
    assert "dishwasher" not in ablated


def test_recall_is_recorded_whether_or_not_it_found_anything(monkeypatch, tmp_path):
    from src.open_llm_vtuber.workspace import build_workspace_prompt_context

    workspace = tmp_path / "ws"
    (workspace / "knowledge").mkdir(parents=True)
    (workspace / "knowledge" / "coffee.md").write_text(
        "The user takes their coffee black.", encoding="utf-8"
    )

    monkeypatch.setenv("EMBODIED_AGENT_EMBEDDINGS", "off")
    monkeypatch.setenv("EMBODIED_AGENT_STUDY_LOG", "1")
    study.reset_for_tests()

    build_workspace_prompt_context("coffee", workspace_dir=workspace)
    build_workspace_prompt_context("submarines", workspace_dir=workspace)

    recalls = [
        event for event in _events(tmp_path) if event["event"] == "memory_recall"
    ]
    assert recalls[0]["documents"] == 1
    assert recalls[0]["top_source"] == "knowledge"
    assert recalls[0]["top_score"] > 0
    assert recalls[1]["documents"] == 0


def test_the_embodiment_ablation_removes_the_gate_not_the_tools(monkeypatch):
    """Without a room to walk in, gating a tool on a place only breaks it."""
    from src.open_llm_vtuber.mcpp.tool_manager import ToolManager

    manager = ToolManager(conf_uid="character")

    study.reset_for_tests()
    assert manager.get_required_object("execute_bash") == "computer"

    monkeypatch.setenv("EMBODIED_AGENT_CONDITION", "embodiment=off")
    study.reset_for_tests()
    assert manager.get_required_object("execute_bash") is None
    assert manager.authorize_tool("execute_bash") == (True, None)


def test_blocked_tool_calls_are_recorded(monkeypatch, tmp_path):
    from src.open_llm_vtuber.mcpp.tool_manager import ToolManager

    monkeypatch.setenv("EMBODIED_AGENT_STUDY_LOG", "1")
    monkeypatch.chdir(tmp_path)
    study.reset_for_tests()

    manager = ToolManager(conf_uid="character")
    allowed, _message = manager.authorize_tool("execute_bash")

    assert allowed is False
    call = [event for event in _events(tmp_path) if event["event"] == "tool_call"][0]
    assert call["tool"] == "execute_bash"
    assert call["requires"] == "computer"
    assert call["allowed"] is False


def test_the_autonomy_ablation_switches_initiative_off(monkeypatch):
    from src.open_llm_vtuber.autonomy.settings import AutonomySettings

    class Config:
        autonomy_config = type("Autonomy", (), {"enabled": True})()

    study.reset_for_tests()
    assert AutonomySettings.from_config(Config()).enabled is True

    monkeypatch.setenv("EMBODIED_AGENT_CONDITION", "autonomy=off")
    study.reset_for_tests()
    assert AutonomySettings.from_config(Config()).enabled is False


def test_the_ablation_can_never_switch_autonomy_on(monkeypatch):
    """A condition narrows what the companion does; it must not widen it."""
    from src.open_llm_vtuber.autonomy.settings import AutonomySettings

    class Disabled:
        autonomy_config = type("Autonomy", (), {"enabled": False})()

    monkeypatch.setenv("EMBODIED_AGENT_CONDITION", "autonomy=on")
    study.reset_for_tests()

    assert AutonomySettings.from_config(Disabled()).enabled is False
