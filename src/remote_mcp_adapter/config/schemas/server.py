"""Server-level schema models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .adapters import AdapterDefinition
from .common import ToolDefaults, normalize_path
from .core import UpstreamPingOverridesConfig
from .upstream import UpstreamConfig


class ServerConfig(BaseModel):
    """Per-upstream server mount configuration."""

    model_config = ConfigDict(extra="forbid")

    id: str
    mount_path: str
    upstream: UpstreamConfig
    upstream_ping: UpstreamPingOverridesConfig = Field(default_factory=UpstreamPingOverridesConfig)
    tool_defaults: ToolDefaults = Field(default_factory=ToolDefaults)
    adapters: list[AdapterDefinition] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_server_id(cls, value: str) -> str:
        """Strip whitespace and reject blank server id.

        Args:
            value: Raw server id.

        Returns:
            Stripped non-blank string.

        Raises:
            ValueError: When the value is blank.
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("servers[].id is required")
        return normalized

    @field_validator("mount_path")
    @classmethod
    def validate_mount_path(cls, value: str) -> str:
        """Normalize mount path and reject the root path.

        Args:
            value: Raw mount path.

        Returns:
            Normalized absolute mount path.

        Raises:
            ValueError: When the path is root ``/``.
        """
        normalized = normalize_path(value, "servers[].mount_path")
        if normalized == "/":
            raise ValueError("servers[].mount_path cannot be '/'")
        return normalized
