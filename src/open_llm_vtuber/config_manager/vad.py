# config_manager/vad.py
from pydantic import ValidationInfo, Field, model_validator
from typing import Literal, Optional, Dict, ClassVar
from .i18n import I18nMixin, Description


class SileroVADConfig(I18nMixin):
    """Configuration for Silero VAD service."""

    orig_sr: int = Field(..., alias="orig_sr")  # 16000
    target_sr: int = Field(..., alias="target_sr")  # 16000
    prob_threshold: float = Field(..., alias="prob_threshold")  # 0.4
    db_threshold: int = Field(..., alias="db_threshold")  # 60
    required_hits: int = Field(..., alias="required_hits")  # 3 * (0.032) = 0.1s
    required_misses: int = Field(..., alias="required_misses")  # 24 * (0.032) = 0.8s
    smoothing_window: int = Field(..., alias="smoothing_window")  # 5

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "orig_sr": Description(en="Original Audio Sample Rate"),
        "target_sr": Description(en="Target Audio Sample Rate"),
        "prob_threshold": Description(
            en="Probability Threshold for VAD"
        ),
        "db_threshold": Description(
            en="Decibel Threshold for VAD"
        ),
        "required_hits": Description(
            en="Number of consecutive hits required to consider speech",
        ),
        "required_misses": Description(
            en="Number of consecutive misses required to consider silence",
        ),
        "smoothing_window": Description(
            en="Smoothing window size for VAD"
        ),
    }


class VADConfig(I18nMixin):
    """Configuration for Automatic Speech Recognition."""

    vad_model: Optional[Literal["silero_vad"]] = Field(None, alias="vad_model")
    silero_vad: Optional[SileroVADConfig] = Field(None, alias="silero_vad")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "vad_model": Description(
            en="Voice Activity Detection model to use"
        ),
        "silero_vad": Description(
            en="Configuration for Silero VAD"
        ),
    }

    @model_validator(mode="after")
    def check_asr_config(cls, values: "VADConfig", info: ValidationInfo):
        vad_model = values.silero_vad

        # Only validate the selected ASR model
        if vad_model == "silero_vad" and values.silero_vad is not None:
            values.silero_vad.model_validate(values.silero_vad.model_dump())

        return values
