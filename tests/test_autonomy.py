"""Configuration tests for agenda-driven autonomy."""

from src.open_llm_vtuber.autonomy import AutonomySettings
from src.open_llm_vtuber.config_manager.autonomy import AutonomyConfig


def test_agenda_settings_apply_safe_lower_bounds():
    settings = AutonomySettings(
        default_pursuit_seconds=1,
        maximum_pursuit_seconds=1,
        idle_min_seconds=1,
        idle_max_seconds=1,
        planner_retry_limit=0,
        fallback_reconsideration_seconds=1,
    )

    assert settings.default_pursuit_seconds == 30
    assert settings.maximum_pursuit_seconds == 60
    assert settings.idle_min_seconds == 5
    assert settings.idle_max_seconds == 5
    assert settings.planner_retry_limit == 1
    assert settings.fallback_reconsideration_seconds == 10


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
