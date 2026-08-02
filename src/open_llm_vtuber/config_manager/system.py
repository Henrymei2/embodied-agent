# config_manager/system.py
from pydantic import Field, model_validator
from pathlib import Path
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
    trusted_hosts: list[str] = Field(default_factory=list, alias="trusted_hosts")
    ssl_certfile: str = Field("", alias="ssl_certfile")
    ssl_keyfile: str = Field("", alias="ssl_keyfile")
    tool_prompts: Dict[str, str] = Field(..., alias="tool_prompts")
    enable_proxy: bool = Field(False, alias="enable_proxy")
    timezone: str = Field("", alias="timezone")
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
        "trusted_hosts": Description(
            en="Additional exact hostnames or IP addresses allowed to access the server",
        ),
        "ssl_certfile": Description(en="Optional HTTPS certificate path"),
        "ssl_keyfile": Description(en="Optional HTTPS private-key path"),
        "tool_prompts": Description(
            en="Tool prompts to be inserted into persona prompt",
        ),
        "enable_proxy": Description(
            en="Enable proxy mode for multiple clients",
        ),
        "timezone": Description(
            en="IANA timezone (e.g. 'America/Chicago') to use for all "
            "timestamps. Leave blank to auto-detect from the system clock.",
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
        from ..request_security import build_trusted_hosts

        build_trusted_hosts(self.host, self.trusted_hosts)
        cert = self.ssl_certfile.strip()
        key = self.ssl_keyfile.strip()
        if bool(cert) != bool(key):
            raise ValueError("ssl_certfile and ssl_keyfile must be configured together")
        for field_name, raw_path in (("ssl_certfile", cert), ("ssl_keyfile", key)):
            if not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if not path.is_file():
                raise ValueError(f"{field_name} must reference a readable regular file")
            try:
                with path.open("rb"):
                    pass
            except OSError as exc:
                raise ValueError(f"{field_name} must reference a readable regular file") from exc
            setattr(self, field_name, str(path.resolve()))
        return self
