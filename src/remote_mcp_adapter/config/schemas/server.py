"""Server-level schema models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .adapters import AdapterDefinition
from .common import ToolDefaults, normalize_path
from .core import UpstreamPingOverridesConfig
from .tool_description_policy import ToolDescriptionPolicyOverridesConfig
from .tool_metadata_sanitization import ToolMetadataSanitizationOverridesConfig
from .tool_definition_pinning import ToolDefinitionPinningOverridesConfig
from .upstream import UpstreamConfig


class ServerConfig(BaseModel):
    """Per-upstream server mount configuration."""

    model_config = ConfigDict(extra="forbid")

    id: str
    mount_path: str
    upstream: UpstreamConfig
    upstream_ping: UpstreamPingOverridesConfig = Field(default_factory=UpstreamPingOverridesConfig)
    tool_defaults: ToolDefaults = Field(default_factory=ToolDefaults)
    code_mode_enabled: bool | None = None
    shorten_descriptions: bool | None = None
    short_description_max_tokens: int | None = Field(default=None, gt=0)
    tool_description_policy: ToolDescriptionPolicyOverridesConfig = Field(default_factory=ToolDescriptionPolicyOverridesConfig)
    tool_metadata_sanitization: ToolMetadataSanitizationOverridesConfig = Field(
        default_factory=ToolMetadataSanitizationOverridesConfig
    )
    tool_definition_pinning: ToolDefinitionPinningOverridesConfig = Field(default_factory=ToolDefinitionPinningOverridesConfig)
    disabled_tools: list[str] = Field(default_factory=list)
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
