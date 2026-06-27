# config_manager/character.py
from pydantic import Field, field_validator
from typing import Dict, ClassVar
from .i18n import I18nMixin, Description
from .asr import ASRConfig
from .tts import TTSConfig
from .vad import VADConfig
from .tts_preprocessor import TTSPreprocessorConfig

from .agent import AgentConfig


class CharacterConfig(I18nMixin):
    """Character configuration settings."""

    conf_name: str = Field(..., alias="conf_name")
    conf_uid: str = Field(..., alias="conf_uid")
    live2d_model_name: str = Field(..., alias="live2d_model_name")
    character_name: str = Field(default="", alias="character_name")
    human_name: str = Field(default="Human", alias="human_name")
    avatar: str = Field(default="", alias="avatar")
    persona_prompt: str = Field(..., alias="persona_prompt")
    agent_config: AgentConfig = Field(..., alias="agent_config")
    asr_config: ASRConfig = Field(..., alias="asr_config")
    tts_config: TTSConfig = Field(..., alias="tts_config")
    vad_config: VADConfig = Field(..., alias="vad_config")
    tts_preprocessor_config: TTSPreprocessorConfig = Field(
        ..., alias="tts_preprocessor_config"
    )

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "conf_name": Description(
            en="Name of the character configuration"
        ),
        "conf_uid": Description(
            en="Unique identifier for the character configuration",
        ),
        "live2d_model_name": Description(
            en="Name of the Live2D model to use"
        ),
        "character_name": Description(
            en="Name of the AI character in conversation"
        ),
        "persona_prompt": Description(
            en="Persona prompt. The persona of your character."
        ),
        "agent_config": Description(
            en="Configuration for the conversation agent"
        ),
        "asr_config": Description(
            en="Configuration for Automatic Speech Recognition"
        ),
        "tts_config": Description(
            en="Configuration for Text-to-Speech"
        ),
        "vad_config": Description(
            en="Configuration for Voice Activity Detection"
        ),
        "tts_preprocessor_config": Description(
            en="Configuration for Text-to-Speech Preprocessor",
        ),
        "human_name": Description(
            en="Name of the human user in conversation"
        ),
        "avatar": Description(
            en="Avatar image path for the character"
        ),
    }

    @field_validator("persona_prompt")
    def check_default_persona_prompt(cls, v):
        if not v:
            raise ValueError(
                "Persona_prompt cannot be empty. Please provide a persona prompt."
            )
        return v

    @field_validator("character_name")
    def set_default_character_name(cls, v, values):
        if not v and "conf_name" in values:
            return values["conf_name"]
        return v
