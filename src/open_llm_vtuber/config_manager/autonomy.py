# config_manager/autonomy.py
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import ClassVar, Dict, Literal

from .i18n import I18nMixin, Description


class PollingProducerConfig(BaseModel):
    enabled: bool = False
    poll_seconds: float = Field(5.0, ge=1.0, le=3600.0)


class WorkspaceProducerConfig(PollingProducerConfig):
    watch_path: str = "demo-inbox"
    settle_seconds: float = Field(1.0, ge=0.1, le=60.0)

    @field_validator("watch_path")
    @classmethod
    def validate_watch_path(cls, value: str) -> str:
        from pathlib import Path

        cleaned = str(value).strip()
        path = Path(cleaned)
        if not cleaned or path.is_absolute() or ".." in path.parts:
            raise ValueError("watch_path must be a relative workspace path")
        if any(part in {"", "."} for part in path.parts):
            raise ValueError("watch_path must be a normalized relative path")
        return path.as_posix()


class EventProducersConfig(BaseModel):
    gmail: PollingProducerConfig = Field(default_factory=PollingProducerConfig)
    google_calendar: PollingProducerConfig = Field(
        default_factory=PollingProducerConfig
    )
    workspace: WorkspaceProducerConfig = Field(default_factory=WorkspaceProducerConfig)


class AutonomyConfig(I18nMixin):
    """Configuration for the agent's autonomous behavior loop."""

    enabled: bool = Field(True, alias="enabled")
    presence_mode: Literal["enforced", "observe_only"] = Field(
        "enforced", alias="presence_mode"
    )
    headless_enabled: bool = Field(True, alias="headless_enabled")
    welcome_back_greeting_enabled: bool = Field(
        True, alias="welcome_back_greeting_enabled"
    )
    welcome_back_min_absence_minutes: int = Field(
        30, ge=1, le=10080, alias="welcome_back_min_absence_minutes"
    )
    lease_ttl_seconds: float = Field(30.0, ge=5, le=300)
    lease_renew_seconds: float = Field(10.0, ge=1, le=150)
    lease_retry_seconds: float = Field(2.0, ge=0.25, le=60)
    default_pursuit_seconds: int = Field(900, ge=30, le=7200)
    maximum_pursuit_seconds: int = Field(1800, ge=60, le=14400)
    idle_min_seconds: int = Field(60, ge=5, le=3600)
    idle_max_seconds: int = Field(600, ge=5, le=7200)
    planner_retry_limit: int = Field(3, ge=1, le=10)
    planner_retry_backoff_seconds: float = Field(2.0, ge=0, le=60)
    retry_backoff_seconds: float = Field(2.0, ge=0, le=60)
    max_retry_backoff_seconds: float = Field(30.0, ge=1, le=300)
    planner_circuit_failure_threshold: int = Field(3, ge=1, le=20)
    planner_circuit_max_seconds: int = Field(1800, ge=10, le=14400)
    step_timeout_seconds: int = Field(30, ge=5, le=3600)
    max_plausible_duration_seconds: float = Field(0.0, ge=0.0, le=60.0)
    human_preemption_timeout_seconds: float = Field(0.25, ge=0.05, le=5.0)
    fallback_reconsideration_seconds: int = Field(300, ge=10, le=7200)
    store_poll_seconds: float = Field(1.0, ge=0.1, le=10.0)
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
    report_retry_seconds: int = Field(60, ge=5, le=86400)
    report_retry_limit: int = Field(5, ge=1, le=20)
    report_backlog_limit: int = Field(20, ge=1, le=500)
    event_backlog_limit: int = Field(500, ge=1, le=5000)
    event_retry_limit: int = Field(5, ge=1, le=20)
    event_retry_seconds: int = Field(60, ge=1, le=86400)
    event_claim_timeout_seconds: int = Field(300, ge=10, le=86400)
    quota_window_seconds: int = Field(3600, ge=60, le=86400)
    planner_calls_per_window: int = Field(120, ge=1, le=10000)
    capability_calls_per_window: int = Field(30, ge=1, le=10000)
    execution_failure_limit: int = Field(3, ge=1, le=20)
    trace_max_bytes: int = Field(10_000_000, ge=100_000, le=1_000_000_000)
    chain_report_hours: int = Field(168, ge=1, le=8760)
    event_producers: EventProducersConfig = Field(default_factory=EventProducersConfig)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_settings(cls, value):
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        legacy_tick = migrated.pop("tick_seconds", None)
        if (
            legacy_tick is not None
            and "fallback_reconsideration_seconds" not in migrated
        ):
            migrated["fallback_reconsideration_seconds"] = legacy_tick
            logger.warning(
                "autonomy_config.tick_seconds is deprecated; migrated it to "
                "fallback_reconsideration_seconds"
            )
        for field_name in (
            "interruption_frequency",
            "help_style",
            "confidence_threshold",
        ):
            if migrated.pop(field_name, None) is not None:
                logger.warning(
                    f"autonomy_config.{field_name} is obsolete and was ignored"
                )
        return migrated

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "enabled": Description(en="Whether the autonomous behavior loop runs"),
        "presence_mode": Description(
            en="Enforce Presence gates or record them without blocking initiative"
        ),
        "headless_enabled": Description(
            en="Keep safe autonomous continuity while no client is connected"
        ),
        "welcome_back_greeting_enabled": Description(
            en="Welcome the user after a meaningful return"
        ),
        "welcome_back_min_absence_minutes": Description(
            en="Minutes away before another welcome-back greeting is eligible"
        ),
        "lease_ttl_seconds": Description(en="Cross-process workspace lease lifetime"),
        "lease_renew_seconds": Description(en="Workspace lease renewal cadence"),
        "lease_retry_seconds": Description(
            en="Standby delay before retrying workspace ownership"
        ),
        "default_pursuit_seconds": Description(en="Default autonomous pursuit budget"),
        "maximum_pursuit_seconds": Description(en="Maximum autonomous pursuit budget"),
        "idle_min_seconds": Description(en="Minimum bounded idle duration"),
        "idle_max_seconds": Description(en="Maximum bounded idle duration"),
        "planner_retry_limit": Description(en="Rejected proposals allowed per pass"),
        "planner_retry_backoff_seconds": Description(
            en="Delay between planner retries"
        ),
        "retry_backoff_seconds": Description(
            en="Base delay before re-executing a failed activity step"
        ),
        "max_retry_backoff_seconds": Description(
            en="Ceiling for the exponential activity-step retry delay"
        ),
        "planner_circuit_failure_threshold": Description(
            en="Failed planning passes before opening the circuit"
        ),
        "planner_circuit_max_seconds": Description(
            en="Maximum planner circuit cooldown"
        ),
        "step_timeout_seconds": Description(
            en="Hard activity timeout after queued work starts"
        ),
        "max_plausible_duration_seconds": Description(
            en="Maximum artificial pacing added after an activity result"
        ),
        "human_preemption_timeout_seconds": Description(
            en="Maximum delay before foreground user work proceeds"
        ),
        "fallback_reconsideration_seconds": Description(
            en="Agenda fallback wake cadence"
        ),
        "store_poll_seconds": Description(
            en="Cross-process agenda change polling cadence"
        ),
        "restart_recovery_grace_seconds": Description(
            en="Restart recovery grace period"
        ),
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
        "report_retry_seconds": Description(
            en="Base delay before retrying a deferred completion report"
        ),
        "report_retry_limit": Description(
            en="Maximum failed delivery attempts for a completion report"
        ),
        "report_backlog_limit": Description(
            en="Pending user-facing reports allowed before autonomy waits"
        ),
        "event_backlog_limit": Description(en="Maximum pending external events"),
        "event_retry_limit": Description(
            en="External event failures before dead-lettering"
        ),
        "event_retry_seconds": Description(en="Base external event retry delay"),
        "event_claim_timeout_seconds": Description(
            en="Timeout for recovering an abandoned event claim"
        ),
        "quota_window_seconds": Description(en="Planner and capability quota window"),
        "planner_calls_per_window": Description(
            en="Planner calls allowed per quota window"
        ),
        "capability_calls_per_window": Description(
            en="Calls allowed per capability per quota window"
        ),
        "execution_failure_limit": Description(
            en="Activity failures before dead-lettering"
        ),
        "trace_max_bytes": Description(
            en="Maximum autonomy trace file size before rotation"
        ),
        "chain_report_hours": Description(
            en="Default long-horizon autonomy report window"
        ),
        "event_producers": Description(
            en="Opt-in Gmail, Google Calendar, and workspace event producers"
        ),
    }
