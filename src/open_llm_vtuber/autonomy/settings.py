"""Runtime settings for the continuous global agenda."""

from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class AutonomySettings:
    """Immutable agenda, pacing, retention, and attention settings."""

    enabled: bool = True
    default_pursuit_seconds: int = 900
    maximum_pursuit_seconds: int = 1800
    idle_min_seconds: int = 60
    idle_max_seconds: int = 600
    planner_retry_limit: int = 3
    planner_retry_backoff_seconds: float = 2.0
    step_timeout_seconds: int = 120
    fallback_reconsideration_seconds: int = 300
    restart_recovery_grace_seconds: int = 10
    terminal_history_hours: int = 24
    terminal_history_entries: int = 30
    # Refillable presence budget. Silent work costs 0.25, asking costs 1.25,
    # and proactive speech costs 2 points.
    attention_budget: float = 6.0
    attention_refill_per_hour: float = 1.0
    receipt_retention: int = 50
    default_snooze_minutes: int = 60

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "default_pursuit_seconds", max(30, int(self.default_pursuit_seconds))
        )
        object.__setattr__(
            self, "maximum_pursuit_seconds", max(60, int(self.maximum_pursuit_seconds))
        )
        object.__setattr__(self, "idle_min_seconds", max(5, int(self.idle_min_seconds)))
        object.__setattr__(
            self,
            "idle_max_seconds",
            max(self.idle_min_seconds, int(self.idle_max_seconds)),
        )
        object.__setattr__(self, "planner_retry_limit", max(1, int(self.planner_retry_limit)))
        object.__setattr__(
            self,
            "fallback_reconsideration_seconds",
            max(10, int(self.fallback_reconsideration_seconds)),
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
            default_pursuit_seconds=int(getattr(cfg, "default_pursuit_seconds", 900)),
            maximum_pursuit_seconds=int(getattr(cfg, "maximum_pursuit_seconds", 1800)),
            idle_min_seconds=int(getattr(cfg, "idle_min_seconds", 60)),
            idle_max_seconds=int(getattr(cfg, "idle_max_seconds", 600)),
            planner_retry_limit=int(getattr(cfg, "planner_retry_limit", 3)),
            planner_retry_backoff_seconds=float(
                getattr(cfg, "planner_retry_backoff_seconds", 2)
            ),
            step_timeout_seconds=int(getattr(cfg, "step_timeout_seconds", 120)),
            fallback_reconsideration_seconds=int(
                getattr(cfg, "fallback_reconsideration_seconds", 300)
            ),
            restart_recovery_grace_seconds=int(
                getattr(cfg, "restart_recovery_grace_seconds", 10)
            ),
            terminal_history_hours=int(getattr(cfg, "terminal_history_hours", 24)),
            terminal_history_entries=int(
                getattr(cfg, "terminal_history_entries", 30)
            ),
            attention_budget=float(getattr(cfg, "attention_budget", 6.0)),
            attention_refill_per_hour=float(
                getattr(cfg, "attention_refill_per_hour", 1.0)
            ),
            receipt_retention=int(getattr(cfg, "receipt_retention", 50)),
            default_snooze_minutes=int(getattr(cfg, "default_snooze_minutes", 60)),
        )
