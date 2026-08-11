"""Runtime settings for the continuous global agenda."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AutonomySettings:
    """Immutable agenda, pacing, retention, and attention settings."""

    enabled: bool = True
    presence_mode: str = "enforced"
    headless_enabled: bool = True
    welcome_back_greeting_enabled: bool = True
    welcome_back_min_absence_minutes: int = 30
    lease_ttl_seconds: float = 30.0
    lease_renew_seconds: float = 10.0
    lease_retry_seconds: float = 2.0
    default_pursuit_seconds: int = 900
    maximum_pursuit_seconds: int = 1800
    idle_min_seconds: int = 60
    idle_max_seconds: int = 600
    planner_retry_limit: int = 3
    planner_retry_backoff_seconds: float = 2.0
    retry_backoff_seconds: float = 2.0
    max_retry_backoff_seconds: float = 30.0
    planner_circuit_failure_threshold: int = 3
    planner_circuit_max_seconds: int = 1800
    planner_timeout_seconds: float = 30.0
    intention_model_timeout_seconds: float = 30.0
    intention_max_turns: int = 12
    step_timeout_seconds: int = 30
    max_plausible_duration_seconds: float = 0.0
    human_preemption_timeout_seconds: float = 0.25
    fallback_reconsideration_seconds: int = 300
    store_poll_seconds: float = 1.0
    restart_recovery_grace_seconds: int = 10
    terminal_history_hours: int = 24
    terminal_history_entries: int = 30
    # Refillable presence budget. Silent work costs 0.25, asking costs 1.25,
    # and proactive speech costs 2 points.
    attention_budget: float = 6.0
    attention_refill_per_hour: float = 1.0
    receipt_retention: int = 50
    default_snooze_minutes: int = 60
    report_retry_seconds: int = 60
    report_retry_limit: int = 5
    report_backlog_limit: int = 20
    event_backlog_limit: int = 500
    event_retry_limit: int = 5
    event_retry_seconds: int = 60
    event_claim_timeout_seconds: int = 300
    quota_window_seconds: int = 3600
    planner_calls_per_window: int = 120
    capability_calls_per_window: int = 30
    execution_failure_limit: int = 3
    trace_max_bytes: int = 10_000_000
    chain_report_hours: int = 168

    def __post_init__(self) -> None:
        presence_mode = str(self.presence_mode).strip().lower()
        if presence_mode not in {"enforced", "observe_only"}:
            presence_mode = "enforced"
        object.__setattr__(self, "presence_mode", presence_mode)
        object.__setattr__(
            self,
            "welcome_back_min_absence_minutes",
            max(1, min(10080, int(self.welcome_back_min_absence_minutes))),
        )
        lease_ttl_seconds = max(5.0, min(300.0, float(self.lease_ttl_seconds)))
        object.__setattr__(self, "lease_ttl_seconds", lease_ttl_seconds)
        object.__setattr__(
            self,
            "lease_renew_seconds",
            max(
                1.0,
                min(lease_ttl_seconds / 2.0, float(self.lease_renew_seconds)),
            ),
        )
        object.__setattr__(
            self,
            "lease_retry_seconds",
            max(0.25, min(60.0, float(self.lease_retry_seconds))),
        )
        maximum_pursuit_seconds = max(60, int(self.maximum_pursuit_seconds))
        object.__setattr__(
            self,
            "maximum_pursuit_seconds",
            maximum_pursuit_seconds,
        )
        object.__setattr__(
            self,
            "default_pursuit_seconds",
            min(
                maximum_pursuit_seconds,
                max(30, int(self.default_pursuit_seconds)),
            ),
        )
        object.__setattr__(self, "idle_min_seconds", max(5, int(self.idle_min_seconds)))
        object.__setattr__(
            self,
            "idle_max_seconds",
            max(self.idle_min_seconds, int(self.idle_max_seconds)),
        )
        object.__setattr__(
            self, "planner_retry_limit", max(1, int(self.planner_retry_limit))
        )
        object.__setattr__(
            self,
            "fallback_reconsideration_seconds",
            max(10, int(self.fallback_reconsideration_seconds)),
        )
        object.__setattr__(
            self,
            "store_poll_seconds",
            max(0.1, min(10.0, float(self.store_poll_seconds))),
        )
        object.__setattr__(
            self,
            "planner_retry_backoff_seconds",
            max(0.0, min(60.0, float(self.planner_retry_backoff_seconds))),
        )
        object.__setattr__(
            self,
            "retry_backoff_seconds",
            max(0.0, min(60.0, float(self.retry_backoff_seconds))),
        )
        object.__setattr__(
            self,
            "max_retry_backoff_seconds",
            max(
                self.retry_backoff_seconds,
                min(300.0, float(self.max_retry_backoff_seconds)),
            ),
        )
        object.__setattr__(
            self,
            "planner_circuit_failure_threshold",
            max(1, min(20, int(self.planner_circuit_failure_threshold))),
        )
        object.__setattr__(
            self,
            "planner_circuit_max_seconds",
            max(
                10,
                min(
                    maximum_pursuit_seconds,
                    int(self.planner_circuit_max_seconds),
                ),
            ),
        )
        object.__setattr__(
            self,
            "planner_timeout_seconds",
            max(5.0, min(300.0, float(self.planner_timeout_seconds))),
        )
        object.__setattr__(
            self,
            "intention_model_timeout_seconds",
            max(5.0, min(300.0, float(self.intention_model_timeout_seconds))),
        )
        object.__setattr__(
            self,
            "intention_max_turns",
            max(1, min(100, int(self.intention_max_turns))),
        )
        object.__setattr__(
            self, "step_timeout_seconds", max(5, int(self.step_timeout_seconds))
        )
        object.__setattr__(
            self,
            "max_plausible_duration_seconds",
            max(0.0, min(60.0, float(self.max_plausible_duration_seconds))),
        )
        object.__setattr__(
            self,
            "human_preemption_timeout_seconds",
            max(0.05, min(5.0, float(self.human_preemption_timeout_seconds))),
        )
        object.__setattr__(
            self,
            "restart_recovery_grace_seconds",
            max(0, int(self.restart_recovery_grace_seconds)),
        )
        object.__setattr__(
            self, "attention_budget", max(1.0, min(100.0, float(self.attention_budget)))
        )
        object.__setattr__(
            self,
            "attention_refill_per_hour",
            max(0.0, min(100.0, float(self.attention_refill_per_hour))),
        )
        object.__setattr__(
            self, "receipt_retention", max(5, min(500, int(self.receipt_retention)))
        )
        object.__setattr__(
            self,
            "default_snooze_minutes",
            max(5, min(1440, int(self.default_snooze_minutes))),
        )
        object.__setattr__(
            self,
            "report_retry_seconds",
            max(5, min(86400, int(self.report_retry_seconds))),
        )
        object.__setattr__(
            self,
            "report_retry_limit",
            max(1, min(20, int(self.report_retry_limit))),
        )
        object.__setattr__(
            self,
            "report_backlog_limit",
            max(1, min(500, int(self.report_backlog_limit))),
        )
        object.__setattr__(
            self,
            "event_backlog_limit",
            max(1, min(5000, int(self.event_backlog_limit))),
        )
        object.__setattr__(
            self,
            "event_retry_limit",
            max(1, min(20, int(self.event_retry_limit))),
        )
        object.__setattr__(
            self,
            "event_retry_seconds",
            max(1, min(86400, int(self.event_retry_seconds))),
        )
        object.__setattr__(
            self,
            "event_claim_timeout_seconds",
            max(10, min(86400, int(self.event_claim_timeout_seconds))),
        )
        object.__setattr__(
            self,
            "quota_window_seconds",
            max(60, min(86400, int(self.quota_window_seconds))),
        )
        object.__setattr__(
            self,
            "planner_calls_per_window",
            max(1, min(10000, int(self.planner_calls_per_window))),
        )
        object.__setattr__(
            self,
            "capability_calls_per_window",
            max(1, min(10000, int(self.capability_calls_per_window))),
        )
        object.__setattr__(
            self,
            "execution_failure_limit",
            max(1, min(20, int(self.execution_failure_limit))),
        )
        object.__setattr__(
            self,
            "trace_max_bytes",
            max(100_000, min(1_000_000_000, int(self.trace_max_bytes))),
        )
        object.__setattr__(
            self,
            "chain_report_hours",
            max(1, min(8760, int(self.chain_report_hours))),
        )

    @classmethod
    def from_config(cls, system_config: object | None) -> "AutonomySettings":
        """Build settings from a loaded SystemConfig (or defaults if absent).

        Resilient by design: reads an optional ``autonomy_config`` attribute so
        the loop works even against configs that predate this feature.
        """
        from ..study import conditions

        # The ablation overrides configuration in one direction only: it can
        # switch initiative off for a session, never on.
        permitted = conditions().autonomy

        cfg = getattr(system_config, "autonomy_config", None)
        if cfg is None:
            return cls(enabled=permitted)
        return cls(
            enabled=bool(getattr(cfg, "enabled", True)) and permitted,
            presence_mode=str(getattr(cfg, "presence_mode", "enforced")),
            headless_enabled=bool(getattr(cfg, "headless_enabled", True)),
            welcome_back_greeting_enabled=bool(
                getattr(cfg, "welcome_back_greeting_enabled", True)
            ),
            welcome_back_min_absence_minutes=int(
                getattr(cfg, "welcome_back_min_absence_minutes", 30)
            ),
            lease_ttl_seconds=float(getattr(cfg, "lease_ttl_seconds", 30)),
            lease_renew_seconds=float(getattr(cfg, "lease_renew_seconds", 10)),
            lease_retry_seconds=float(getattr(cfg, "lease_retry_seconds", 2)),
            default_pursuit_seconds=int(getattr(cfg, "default_pursuit_seconds", 900)),
            maximum_pursuit_seconds=int(getattr(cfg, "maximum_pursuit_seconds", 1800)),
            idle_min_seconds=int(getattr(cfg, "idle_min_seconds", 60)),
            idle_max_seconds=int(getattr(cfg, "idle_max_seconds", 600)),
            planner_retry_limit=int(getattr(cfg, "planner_retry_limit", 3)),
            planner_retry_backoff_seconds=float(
                getattr(cfg, "planner_retry_backoff_seconds", 2)
            ),
            retry_backoff_seconds=float(getattr(cfg, "retry_backoff_seconds", 2)),
            max_retry_backoff_seconds=float(
                getattr(cfg, "max_retry_backoff_seconds", 30)
            ),
            planner_circuit_failure_threshold=int(
                getattr(cfg, "planner_circuit_failure_threshold", 3)
            ),
            planner_circuit_max_seconds=int(
                getattr(cfg, "planner_circuit_max_seconds", 1800)
            ),
            planner_timeout_seconds=float(
                getattr(cfg, "planner_timeout_seconds", 30.0)
            ),
            intention_model_timeout_seconds=float(
                getattr(cfg, "intention_model_timeout_seconds", 30.0)
            ),
            intention_max_turns=int(getattr(cfg, "intention_max_turns", 12)),
            step_timeout_seconds=int(getattr(cfg, "step_timeout_seconds", 30)),
            max_plausible_duration_seconds=float(
                getattr(cfg, "max_plausible_duration_seconds", 0.0)
            ),
            human_preemption_timeout_seconds=float(
                getattr(cfg, "human_preemption_timeout_seconds", 0.25)
            ),
            fallback_reconsideration_seconds=int(
                getattr(cfg, "fallback_reconsideration_seconds", 300)
            ),
            store_poll_seconds=float(getattr(cfg, "store_poll_seconds", 1.0)),
            restart_recovery_grace_seconds=int(
                getattr(cfg, "restart_recovery_grace_seconds", 10)
            ),
            terminal_history_hours=int(getattr(cfg, "terminal_history_hours", 24)),
            terminal_history_entries=int(getattr(cfg, "terminal_history_entries", 30)),
            attention_budget=float(getattr(cfg, "attention_budget", 6.0)),
            attention_refill_per_hour=float(
                getattr(cfg, "attention_refill_per_hour", 1.0)
            ),
            receipt_retention=int(getattr(cfg, "receipt_retention", 50)),
            default_snooze_minutes=int(getattr(cfg, "default_snooze_minutes", 60)),
            report_retry_seconds=int(getattr(cfg, "report_retry_seconds", 60)),
            report_retry_limit=int(getattr(cfg, "report_retry_limit", 5)),
            report_backlog_limit=int(getattr(cfg, "report_backlog_limit", 20)),
            event_backlog_limit=int(getattr(cfg, "event_backlog_limit", 500)),
            event_retry_limit=int(getattr(cfg, "event_retry_limit", 5)),
            event_retry_seconds=int(getattr(cfg, "event_retry_seconds", 60)),
            event_claim_timeout_seconds=int(
                getattr(cfg, "event_claim_timeout_seconds", 300)
            ),
            quota_window_seconds=int(getattr(cfg, "quota_window_seconds", 3600)),
            planner_calls_per_window=int(getattr(cfg, "planner_calls_per_window", 120)),
            capability_calls_per_window=int(
                getattr(cfg, "capability_calls_per_window", 30)
            ),
            execution_failure_limit=int(getattr(cfg, "execution_failure_limit", 3)),
            trace_max_bytes=int(getattr(cfg, "trace_max_bytes", 10_000_000)),
            chain_report_hours=int(getattr(cfg, "chain_report_hours", 168)),
        )
