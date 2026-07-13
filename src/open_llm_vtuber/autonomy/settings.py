"""Runtime settings for autonomous behavior, including interruption controls.

The three interruption controls from the project scope are exposed here as
human-friendly enums (low/medium/high, quiet/proactive/talkative,
strict/balanced/loose) and mapped to the numeric knobs the planner actually
uses. Keeping the mapping in one place makes the behavior easy to tune.
"""

from __future__ import annotations

from dataclasses import dataclass

# interruption_frequency -> (base act probability, min seconds between spoken
# interruptions, intrusiveness sensitivity multiplier).
_FREQUENCY_MAP: dict[str, tuple[float, float, float]] = {
    "low": (0.25, 3600.0, 1.4),
    "medium": (0.50, 1800.0, 1.0),
    "high": (0.80, 600.0, 0.6),
}

# help_style -> additive bias applied to *spoken* deliveries (negative = prefer
# staying silent, positive = happier to speak).
_HELP_STYLE_SPEAK_BIAS: dict[str, float] = {
    "quiet": -0.15,
    "proactive": 0.0,
    "talkative": 0.15,
}

# confidence_threshold -> (minimum score to act at all, minimum score to
# surface via speech rather than acting silently).
_CONFIDENCE_MAP: dict[str, tuple[float, float]] = {
    "strict": (0.60, 0.75),
    "balanced": (0.45, 0.60),
    "loose": (0.30, 0.45),
}

_VALID_FREQUENCY = set(_FREQUENCY_MAP)
_VALID_HELP_STYLE = set(_HELP_STYLE_SPEAK_BIAS)
_VALID_CONFIDENCE = set(_CONFIDENCE_MAP)


@dataclass(frozen=True)
class AutonomySettings:
    """Immutable settings bundle consumed by the planner and driver."""

    enabled: bool = True
    tick_seconds: int = 900  # how often the autonomy loop wakes (default 15 min)
    interruption_frequency: str = "medium"
    help_style: str = "proactive"
    confidence_threshold: str = "balanced"
    # Refillable presence budget. Silent work costs 0.25, asking costs 1.25,
    # and proactive speech costs 2 points.
    attention_budget: float = 6.0
    attention_refill_per_hour: float = 1.0
    receipt_retention: int = 50
    default_snooze_minutes: int = 60

    def __post_init__(self) -> None:
        # Normalize/validate; fall back to the sensible middle option rather
        # than raising, so a bad config value never crashes the loop.
        object.__setattr__(
            self,
            "interruption_frequency",
            self.interruption_frequency
            if self.interruption_frequency in _VALID_FREQUENCY
            else "medium",
        )
        object.__setattr__(
            self,
            "help_style",
            self.help_style if self.help_style in _VALID_HELP_STYLE else "proactive",
        )
        object.__setattr__(
            self,
            "confidence_threshold",
            self.confidence_threshold
            if self.confidence_threshold in _VALID_CONFIDENCE
            else "balanced",
        )
        object.__setattr__(self, "tick_seconds", max(30, int(self.tick_seconds)))
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

    # --- derived numeric knobs ------------------------------------------- #
    @property
    def base_act_probability(self) -> float:
        return _FREQUENCY_MAP[self.interruption_frequency][0]

    @property
    def min_speak_gap_seconds(self) -> float:
        return _FREQUENCY_MAP[self.interruption_frequency][1]

    @property
    def intrusion_sensitivity(self) -> float:
        return _FREQUENCY_MAP[self.interruption_frequency][2]

    @property
    def speak_bias(self) -> float:
        return _HELP_STYLE_SPEAK_BIAS[self.help_style]

    @property
    def act_threshold(self) -> float:
        return _CONFIDENCE_MAP[self.confidence_threshold][0]

    @property
    def speak_threshold(self) -> float:
        return _CONFIDENCE_MAP[self.confidence_threshold][1]

    @classmethod
    def from_config(cls, system_config: object | None) -> "AutonomySettings":
        """Build settings from a loaded SystemConfig (or defaults if absent).

        Resilient by design: reads an optional ``autonomy_config`` attribute so
        the loop works even against configs that predate this feature.
        """
        cfg = getattr(system_config, "autonomy_config", None)
        if cfg is None:
            return cls()
        return cls(
            enabled=bool(getattr(cfg, "enabled", True)),
            tick_seconds=int(getattr(cfg, "tick_seconds", 900)),
            interruption_frequency=str(
                getattr(cfg, "interruption_frequency", "medium")
            ),
            help_style=str(getattr(cfg, "help_style", "proactive")),
            confidence_threshold=str(getattr(cfg, "confidence_threshold", "balanced")),
            attention_budget=float(getattr(cfg, "attention_budget", 6.0)),
            attention_refill_per_hour=float(
                getattr(cfg, "attention_refill_per_hour", 1.0)
            ),
            receipt_retention=int(getattr(cfg, "receipt_retention", 50)),
            default_snooze_minutes=int(getattr(cfg, "default_snooze_minutes", 60)),
        )
