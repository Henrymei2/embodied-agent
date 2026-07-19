# config_manager/system.py
from pydantic import Field, model_validator
from typing import Dict, ClassVar
from .i18n import I18nMixin, Description
from pydantic import BaseModel

from .autonomy import AutonomyConfig


class GoogleIntegrationConfig(BaseModel):
    """Application-wide Google OAuth configuration."""

    enabled: bool = False
    client_secrets_file: str = ""
    redirect_uri: str = "http://localhost:12393/api/integrations/google/oauth/callback"


class SystemConfig(I18nMixin):
    """System configuration settings."""

    conf_version: str = Field(..., alias="conf_version")
    host: str = Field(..., alias="host")
    port: int = Field(..., alias="port")
    tool_prompts: Dict[str, str] = Field(..., alias="tool_prompts")
    enable_proxy: bool = Field(False, alias="enable_proxy")
    autonomy_config: AutonomyConfig = Field(
        default_factory=AutonomyConfig, alias="autonomy_config"
    )
    google_integration: GoogleIntegrationConfig = Field(
        default_factory=GoogleIntegrationConfig, alias="google_integration"
    )

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "conf_version": Description(en="Configuration version"),
        "host": Description(en="Server host address"),
        "port": Description(en="Server port number"),
        "tool_prompts": Description(
            en="Tool prompts to be inserted into persona prompt",
        ),
        "enable_proxy": Description(
            en="Enable proxy mode for multiple clients",
        ),
        "autonomy_config": Description(
            en="Autonomous behavior loop settings",
        ),
        "google_integration": Description(
            en="Application-wide Google OAuth integration settings",
        ),
    }

    @model_validator(mode="after")
    def check_port(self):
        if self.port < 0 or self.port > 65535:
            raise ValueError("Port must be between 0 and 65535")
        return self
