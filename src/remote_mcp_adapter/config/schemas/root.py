"""Top-level adapter config schema and helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import EffectiveStorageLockMode, WritePolicyLockMode
from .core import CoreConfig
from .persistence import StatePersistenceConfig
from .server import ServerConfig
from .storage import ArtifactsConfig, SessionsConfig, StorageConfig, UploadsConfig
from .telemetry import TelemetryConfig


class AdapterConfig(BaseModel):
    """Top-level adapter configuration."""

    model_config = ConfigDict(extra="forbid")

    core: CoreConfig = Field(default_factory=CoreConfig)
    state_persistence: StatePersistenceConfig = Field(default_factory=StatePersistenceConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    uploads: UploadsConfig = Field(default_factory=UploadsConfig)
    artifacts: ArtifactsConfig = Field(default_factory=ArtifactsConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    servers: list[ServerConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_state_persistence(self) -> "AdapterConfig":
        """Validate persistence/storage cross-field constraints.

        Returns:
            The validated config instance.

        Raises:
            ValueError: When Redis host is missing or lock mode conflicts.
        """
        if self.state_persistence.type == "redis" and not self.state_persistence.redis.host:
            raise ValueError("state_persistence.redis.host is required when state_persistence.type='redis'")
        if self.storage.lock_mode == "redis" and self.state_persistence.type != "redis":
            raise ValueError("storage.lock_mode='redis' requires state_persistence.type='redis'")
        if self.state_persistence.disk.local_path is None:
            default_path = Path(self.storage.root) / "state" / "adapter_state.sqlite3"
            self.state_persistence.disk.local_path = str(default_path)
        return self

    @model_validator(mode="after")
    def validate_unique_servers(self) -> "AdapterConfig":
        """Ensure all server IDs and mount paths are unique.

        Returns:
            The validated config instance.

        Raises:
            ValueError: On duplicate server IDs, mount paths, or invalid
                legacy server ID references.
        """
        ids: set[str] = set()
        mounts: set[str] = set()
        for server in self.servers:
            if server.id in ids:
                raise ValueError(f"Duplicate servers[].id: {server.id}")
            ids.add(server.id)
            if server.mount_path in mounts:
                raise ValueError(f"Duplicate servers[].mount_path: {server.mount_path}")
            mounts.add(server.mount_path)
        legacy_server_id = self.state_persistence.reconciliation.legacy_server_id
        if legacy_server_id is not None and legacy_server_id not in ids:
            raise ValueError("state_persistence.reconciliation.legacy_server_id must match one configured servers[].id")
        return self


def resolve_storage_lock_mode(config: AdapterConfig) -> EffectiveStorageLockMode:
    """Resolve runtime lock mode, including ``auto`` behavior.

    Args:
        config: Top-level adapter configuration.

    Returns:
        Effective storage lock mode string.
    """
    if config.storage.lock_mode == "auto":
        if config.state_persistence.type == "redis":
            return "redis"
        return "file"
    return config.storage.lock_mode


def resolve_write_policy_lock_mode(config: AdapterConfig) -> WritePolicyLockMode:
    """Resolve lock mode for current local write-policy implementation.

    Args:
        config: Top-level adapter configuration.

    Returns:
        Write policy lock mode string.
    """
    return resolve_storage_lock_mode(config)


def config_to_dict(config: AdapterConfig) -> dict[str, Any]:
    """Return a plain dict representation for debugging/logging.

    Args:
        config: Top-level adapter configuration.

    Returns:
        Dict-serialized copy of the config.
    """
    return config.model_dump(mode="python")
