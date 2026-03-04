"""State persistence schema models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

StatePersistenceType = Literal["memory", "disk", "redis"]
StatePersistenceUnavailablePolicy = Literal["fail_closed", "exit", "fallback_memory"]
StateReconciliationMode = Literal["disabled", "if_empty", "always"]


class StatePersistenceWalConfig(BaseModel):
    """Write-ahead-log settings for persistence backends."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class StatePersistenceDiskConfig(BaseModel):
    """Disk backend settings for adapter state persistence."""

    model_config = ConfigDict(extra="forbid")

    local_path: str | None = None
    wal: StatePersistenceWalConfig = Field(default_factory=StatePersistenceWalConfig)

    @field_validator("local_path")
    @classmethod
    def validate_local_path(cls, value: str | None) -> str | None:
        """Strip whitespace and reject blank local_path when set.

        Args:
            value: Raw path or None.

        Returns:
            Stripped path or None.

        Raises:
            ValueError: When value is a blank string.
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("state_persistence.disk.local_path cannot be blank when set")
        return normalized


class StatePersistenceRedisConfig(BaseModel):
    """Redis backend settings for adapter state persistence."""

    model_config = ConfigDict(extra="forbid")

    host: str | None = None
    port: int = Field(default=6379, ge=1, le=65535)
    db: int = Field(default=0, ge=0)
    username: str | None = None
    password: str | None = None
    tls_insecure: bool = False
    key_base: str = "mcp_remote_adapter"
    ping_seconds: int = Field(default=5, gt=0)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str | None) -> str | None:
        """Strip whitespace and reject blank Redis host when set.

        Args:
            value: Raw host or None.

        Returns:
            Stripped host or None.

        Raises:
            ValueError: When value is a blank string.
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("state_persistence.redis.host cannot be blank when set")
        return normalized

    @field_validator("key_base")
    @classmethod
    def validate_key_base(cls, value: str) -> str:
        """Strip whitespace and reject blank key_base.

        Args:
            value: Raw key base.

        Returns:
            Stripped key base.

        Raises:
            ValueError: When the value is blank.
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("state_persistence.redis.key_base cannot be blank")
        return normalized


class StateReconciliationConfig(BaseModel):
    """Startup reconciliation settings for legacy file-state migration."""

    model_config = ConfigDict(extra="forbid")

    mode: StateReconciliationMode = "if_empty"
    legacy_server_id: str | None = None

    @field_validator("legacy_server_id")
    @classmethod
    def validate_legacy_server_id(cls, value: str | None) -> str | None:
        """Strip whitespace and reject blank legacy_server_id when set.

        Args:
            value: Raw server id or None.

        Returns:
            Stripped server id or None.

        Raises:
            ValueError: When value is a blank string.
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("state_persistence.reconciliation.legacy_server_id cannot be blank when set")
        return normalized


class StatePersistenceConfig(BaseModel):
    """Runtime state persistence settings."""

    model_config = ConfigDict(extra="forbid")

    type: StatePersistenceType = "disk"
    refresh_on_startup: bool = False
    snapshot_interval_seconds: int = Field(default=30, gt=0)
    unavailable_policy: StatePersistenceUnavailablePolicy = "fail_closed"
    disk: StatePersistenceDiskConfig = Field(default_factory=StatePersistenceDiskConfig)
    redis: StatePersistenceRedisConfig = Field(default_factory=StatePersistenceRedisConfig)
    reconciliation: StateReconciliationConfig = Field(default_factory=StateReconciliationConfig)
