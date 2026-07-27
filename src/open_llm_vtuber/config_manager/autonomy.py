# config_manager/autonomy.py
from pydantic import Field
from typing import ClassVar, Dict

from .i18n import I18nMixin, Description


class AutonomyConfig(I18nMixin):
    """Configuration for the agent's autonomous behavior loop."""

    enabled: bool = Field(True, alias="enabled")
    default_pursuit_seconds: int = Field(900, ge=30, le=7200)
    maximum_pursuit_seconds: int = Field(1800, ge=60, le=14400)
    idle_min_seconds: int = Field(60, ge=5, le=3600)
    idle_max_seconds: int = Field(600, ge=5, le=7200)
    planner_retry_limit: int = Field(3, ge=1, le=10)
    planner_retry_backoff_seconds: float = Field(2.0, ge=0, le=60)
    step_timeout_seconds: int = Field(120, ge=5, le=3600)
    fallback_reconsideration_seconds: int = Field(300, ge=10, le=7200)
    restart_recovery_grace_seconds: int = Field(10, ge=0, le=600)
    terminal_history_hours: int = Field(24, ge=1, le=720)
    terminal_history_entries: int = Field(30, ge=5, le=1000)
    attention_budget: float = Field(6.0, ge=1.0, le=100.0, alias="attention_budget")
    attention_refill_per_hour: float = Field(
        1.0, ge=0.0, le=100.0, alias="attention_refill_per_hour"
    )
    receipt_retention: int = Field(50, ge=5, le=500, alias="receipt_retention")
    default_snooze_minutes: int = Field(
        60, ge=5, le=1440, alias="default_snooze_minutes"
    )

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "enabled": Description(en="Whether the autonomous behavior loop runs"),
        "default_pursuit_seconds": Description(en="Default autonomous pursuit budget"),
        "maximum_pursuit_seconds": Description(en="Maximum autonomous pursuit budget"),
        "idle_min_seconds": Description(en="Minimum bounded idle duration"),
        "idle_max_seconds": Description(en="Maximum bounded idle duration"),
        "planner_retry_limit": Description(en="Rejected proposals allowed per pass"),
        "planner_retry_backoff_seconds": Description(en="Delay between planner retries"),
        "step_timeout_seconds": Description(en="Default activity step timeout"),
        "fallback_reconsideration_seconds": Description(en="Agenda fallback wake cadence"),
        "restart_recovery_grace_seconds": Description(en="Restart recovery grace period"),
        "terminal_history_hours": Description(en="Hours of detailed terminal history"),
        "terminal_history_entries": Description(en="Maximum detailed terminal entries"),
        "attention_budget": Description(
            en="Maximum attention points autonomous actions may spend"
        ),
        "attention_refill_per_hour": Description(
            en="Attention points restored per hour"
        ),
        "receipt_retention": Description(
            en="Number of autonomous action receipts retained locally"
        ),
        "default_snooze_minutes": Description(
            en="Default duration for snoozing proactive speech"
        ),
    }
