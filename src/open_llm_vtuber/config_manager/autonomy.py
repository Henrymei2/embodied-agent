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

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "enabled": Description(en="Whether the autonomous behavior loop runs"),
        "tick_seconds": Description(
            en="Seconds between autonomy decision ticks (min 30)"
        ),
        "interruption_frequency": Description(
            en="How readily the agent interrupts: low, medium, or high"
        ),
        "help_style": Description(
            en="Delivery bias: quiet, proactive, or talkative"
        ),
        "confidence_threshold": Description(
            en="How confident the agent must be to act: strict, balanced, or loose"
        ),
    }
