"""Core runtime and upstream-ping schema models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...constants import DEFAULT_ADAPTER_AUTH_HEADER
from .common import ToolDefaults, normalize_path


class CoreAuthConfig(BaseModel):
    """Adapter authentication settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    header_name: str = DEFAULT_ADAPTER_AUTH_HEADER
    token: str | None = None
    signed_upload_ttl_seconds: int = Field(default=120, gt=0)
    signing_secret: str | None = None

    @model_validator(mode="after")
    def validate_token_if_enabled(self) -> "CoreAuthConfig":
        """Require auth token when enabled and reject blank signing_secret.

        Returns:
            Validated model instance.

        Raises:
            ValueError: When required auth fields are missing or blank.
        """
        if self.enabled and not (self.token or "").strip():
            raise ValueError("core.auth.token must be set when core.auth.enabled=true")
        if self.signing_secret is not None and not self.signing_secret.strip():
            raise ValueError("core.auth.signing_secret cannot be blank when set")
        return self


class CoreCorsConfig(BaseModel):
    """Adapter CORS settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    allowed_origins: list[str] = Field(default_factory=list)
    allowed_methods: list[str] = Field(default_factory=lambda: ["POST", "GET", "OPTIONS"])
    allowed_headers: list[str] = Field(default_factory=lambda: ["*"])
    allow_credentials: bool = False


class UpstreamPingConfig(BaseModel):
    """Global upstream ping and circuit-breaker defaults."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    interval_seconds: int = Field(default=15, gt=0)
    timeout_seconds: int = Field(default=5, gt=0)
    failure_threshold: int = Field(default=3, gt=0)
    open_cooldown_seconds: int = Field(default=30, gt=0)
    half_open_probe_allowance: int = Field(default=2, gt=0)


class UpstreamPingOverridesConfig(BaseModel):
    """Per-server overrides for upstream ping and breaker settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, gt=0)
    timeout_seconds: int | None = Field(default=None, gt=0)
    failure_threshold: int | None = Field(default=None, gt=0)
    open_cooldown_seconds: int | None = Field(default=None, gt=0)
    half_open_probe_allowance: int | None = Field(default=None, gt=0)


class CoreConfig(BaseModel):
    """Core server and runtime settings."""

    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8932, ge=1, le=65535)
    log_level: str = "warning"
    max_start_wait_seconds: int = Field(default=60, ge=0)
    cleanup_interval_seconds: int | None = Field(default=60, gt=0)
    public_base_url: str | None = None
    allow_artifacts_download: bool = False
    code_mode_enabled: bool = False
    shorten_descriptions: bool = False
    short_description_max_tokens: int = Field(default=16, gt=0)
    upload_path: str = "/upload"
    upstream_metadata_cache_ttl_seconds: int = Field(default=300, ge=0)
    upstream_ping: UpstreamPingConfig = Field(default_factory=UpstreamPingConfig)
    auth: CoreAuthConfig = Field(default_factory=CoreAuthConfig)
    cors: CoreCorsConfig = Field(default_factory=CoreCorsConfig)
    defaults: ToolDefaults = Field(default_factory=lambda: ToolDefaults(tool_call_timeout_seconds=60, allow_raw_output=False))

    @field_validator("upload_path")
    @classmethod
    def validate_upload_path(cls, value: str) -> str:
        """Normalize upload path to an absolute slash-prefixed string.

        Args:
            value: Raw upload path.

        Returns:
            Normalized path.

        Raises:
            ValueError: If the normalized path is the root path ("/").
        """
        normalized = normalize_path(value, "core.upload_path")
        if normalized == "/":
            raise ValueError("core.upload_path cannot be '/', please use a non-root path such as '/upload'")
        return normalized

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Normalize log level and reject unsupported values.

        Args:
            value: Raw log level string.

        Returns:
            Lowercased log level.

        Raises:
            ValueError: When the value is not a recognized level.
        """
        allowed = {"debug", "info", "warning", "error", "critical"}
        normalized = value.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"core.log_level must be one of {sorted(allowed)}")
        return normalized
