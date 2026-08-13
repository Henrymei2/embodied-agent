"""Configuration tests for agenda-driven autonomy."""

import pytest

from src.open_llm_vtuber.autonomy import AutonomySettings
from src.open_llm_vtuber.config_manager.autonomy import AutonomyConfig
from src.open_llm_vtuber.config_manager.main import Config


def test_agenda_settings_apply_safe_lower_bounds():
    settings = AutonomySettings(
        maximum_pursuit_seconds=1,
        idle_min_seconds=1,
        idle_max_seconds=1,
        idle_relocation_min_seconds=0,
        idle_relocation_max_seconds=0,
        planner_retry_limit=0,
        planner_timeout_seconds=1,
        intention_model_timeout_seconds=1,
        intention_max_turns=0,
        successor_delay_seconds=-1,
        fallback_reconsideration_seconds=1,
        human_preemption_timeout_seconds=0,
    )

    assert settings.maximum_pursuit_seconds == 60
    assert settings.idle_min_seconds == 5
    assert settings.idle_max_seconds == 5
    assert settings.idle_relocation_min_seconds == 1.0
    assert settings.idle_relocation_max_seconds == 1.0
    assert settings.planner_retry_limit == 1
    assert settings.planner_timeout_seconds == 5.0
    assert settings.intention_model_timeout_seconds == 5.0
    assert settings.intention_max_turns == 1
    assert settings.successor_delay_seconds == 0
    assert settings.fallback_reconsideration_seconds == 10
    assert settings.human_preemption_timeout_seconds == 0.05


def test_latency_settings_default_to_fast_hard_limits():
    config = AutonomyConfig()
    settings = AutonomySettings.from_config(
        type("System", (), {"autonomy_config": config})()
    )

    assert config.step_timeout_seconds == 30
    assert config.planner_timeout_seconds == 30.0
    assert config.intention_model_timeout_seconds == 30.0
    assert config.intention_max_turns == 12
    assert config.max_plausible_duration_seconds == 0.0
    assert config.successor_delay_seconds == 10.0
    assert settings.step_timeout_seconds == 30
    assert settings.planner_timeout_seconds == 30.0
    assert settings.intention_model_timeout_seconds == 30.0
    assert settings.intention_max_turns == 12
    assert settings.max_plausible_duration_seconds == 0.0
    assert settings.successor_delay_seconds == 10.0
    assert not hasattr(config, "default_pursuit_seconds")
    assert not hasattr(settings, "default_pursuit_seconds")
    assert config.idle_relocation_min_seconds == 10.0
    assert config.idle_relocation_max_seconds == 30.0
    assert settings.idle_relocation_min_seconds == 10.0
    assert settings.idle_relocation_max_seconds == 30.0
    assert config.welcome_back_greeting_enabled is True
    assert config.welcome_back_min_absence_minutes == 30
    assert config.debug_reset_agenda_on_connect is False
    assert settings.welcome_back_greeting_enabled is True
    assert settings.welcome_back_min_absence_minutes == 30
    assert settings.debug_reset_agenda_on_connect is False


def test_idle_relocation_config_rejects_reversed_wait_range():
    with pytest.raises(ValueError, match="idle_relocation_max_seconds"):
        AutonomyConfig(
            idle_relocation_min_seconds=30,
            idle_relocation_max_seconds=10,
        )


def test_welcome_back_absence_is_bounded():
    assert (
        AutonomySettings(
            welcome_back_min_absence_minutes=0
        ).welcome_back_min_absence_minutes
        == 1
    )
    assert (
        AutonomySettings(
            welcome_back_min_absence_minutes=20_000
        ).welcome_back_min_absence_minutes
        == 10_080
    )


def test_activity_retry_backoff_is_bounded():
    settings = AutonomySettings(retry_backoff_seconds=90, max_retry_backoff_seconds=1)

    assert settings.retry_backoff_seconds == 60
    assert settings.max_retry_backoff_seconds == 60

    assert AutonomySettings().retry_backoff_seconds == 2.0
    assert AutonomySettings().max_retry_backoff_seconds == 30.0


def test_plausible_duration_limit_is_bounded():
    assert (
        AutonomySettings(
            max_plausible_duration_seconds=-1
        ).max_plausible_duration_seconds
        == 0
    )
    assert (
        AutonomySettings(
            max_plausible_duration_seconds=120
        ).max_plausible_duration_seconds
        == 60
    )


def test_legacy_tick_setting_migrates_to_the_agenda_cadence():
    config = AutonomyConfig.model_validate(
        {
            "tick_seconds": 45,
            "interruption_frequency": "medium",
            "help_style": "proactive",
            "confidence_threshold": "balanced",
        }
    )

    assert config.fallback_reconsideration_seconds == 45


def test_root_autonomy_config_migrates_under_system_config():
    migrated = Config.migrate_root_autonomy_config(
        {
            "system_config": {"host": "localhost"},
            "autonomy_config": {"presence_mode": "observe_only"},
        }
    )

    assert "autonomy_config" not in migrated
    assert migrated["system_config"]["autonomy_config"] == {
        "presence_mode": "observe_only"
    }


def test_nested_autonomy_config_wins_over_deprecated_root_value():
    migrated = Config.migrate_root_autonomy_config(
        {
            "system_config": {"autonomy_config": {"presence_mode": "enforced"}},
            "autonomy_config": {"presence_mode": "observe_only"},
        }
    )

    assert migrated["system_config"]["autonomy_config"]["presence_mode"] == ("enforced")


def test_event_producers_are_opt_in_and_workspace_path_is_confined():
    config = AutonomyConfig()
    assert config.event_producers.gmail.enabled is False
    assert config.event_producers.google_calendar.enabled is False
    assert config.event_producers.workspace.enabled is False

    with pytest.raises(ValueError, match="relative workspace path"):
        AutonomyConfig.model_validate(
            {"event_producers": {"workspace": {"watch_path": "../outside"}}}
        )
