# config_manager/main.py
from loguru import logger
from pydantic import BaseModel, Field, model_validator
from typing import Dict, ClassVar

from .system import SystemConfig
from .character import CharacterConfig
from .live import LiveConfig
from .i18n import I18nMixin, Description


class Config(I18nMixin, BaseModel):
    """
    Main configuration for the application.
    """

    system_config: SystemConfig = Field(default=None, alias="system_config")
    character_config: CharacterConfig = Field(..., alias="character_config")
    live_config: LiveConfig = Field(default=LiveConfig(), alias="live_config")

    @model_validator(mode="before")
    @classmethod
    def migrate_root_autonomy_config(cls, value):
        """Recover the briefly documented root-level autonomy configuration."""
        if not isinstance(value, dict) or "autonomy_config" not in value:
            return value
        migrated = dict(value)
        root_autonomy = migrated.pop("autonomy_config")
        system = dict(migrated.get("system_config") or {})
        if "autonomy_config" not in system:
            system["autonomy_config"] = root_autonomy
            logger.warning(
                "Root-level autonomy_config is deprecated; migrated it to "
                "system_config.autonomy_config"
            )
        else:
            logger.warning(
                "Ignored deprecated root-level autonomy_config because "
                "system_config.autonomy_config is already configured"
            )
        migrated["system_config"] = system
        return migrated

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "system_config": Description(en="System configuration settings"),
        "character_config": Description(en="Character configuration settings"),
        "live_config": Description(en="Live streaming platform integration settings"),
    }
