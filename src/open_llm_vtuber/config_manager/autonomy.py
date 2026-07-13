# config_manager/autonomy.py
from pydantic import Field
from typing import ClassVar, Dict, Literal

from .i18n import I18nMixin, Description


class AutonomyConfig(I18nMixin):
    """Configuration for the agent's autonomous behavior loop."""

    enabled: bool = Field(True, alias="enabled")
    tick_seconds: int = Field(900, alias="tick_seconds")
    interruption_frequency: Literal["low", "medium", "high"] = Field(
        "medium", alias="interruption_frequency"
    )
    help_style: Literal["quiet", "proactive", "talkative"] = Field(
        "proactive", alias="help_style"
    )
    confidence_threshold: Literal["strict", "balanced", "loose"] = Field(
        "balanced", alias="confidence_threshold"
    )
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
        "tick_seconds": Description(
            en="Seconds between autonomy decision ticks (min 30)"
        ),
        "interruption_frequency": Description(
            en="How readily the agent interrupts: low, medium, or high"
        ),
        "help_style": Description(en="Delivery bias: quiet, proactive, or talkative"),
        "confidence_threshold": Description(
            en="How confident the agent must be to act: strict, balanced, or loose"
        ),
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
