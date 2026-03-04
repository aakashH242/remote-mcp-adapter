"""Storage/session/upload/artifact schema models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import StorageLockMode, parse_byte_size


class StorageConfig(BaseModel):
    """Shared storage settings."""

    model_config = ConfigDict(extra="forbid")

    root: str = "/data/shared"
    max_size: int | str | None = None
    atomic_writes: bool = True
    lock_mode: StorageLockMode = "auto"
    orphan_sweeper_enabled: bool = True
    orphan_sweeper_grace_seconds: int = Field(default=300, ge=0)
    artifact_locator_policy: Literal["storage_only", "allow_configured_roots"] = "storage_only"
    artifact_locator_allowed_roots: list[str] = Field(default_factory=list)

    @field_validator("max_size", mode="before")
    @classmethod
    def validate_max_size(cls, value: int | str | None) -> int | None:
        """Parse human-readable byte size for storage.max_size.

        Args:
            value: Raw byte size value.

        Returns:
            Parsed integer byte count or None.
        """
        return parse_byte_size(value, "storage.max_size")

    @field_validator("artifact_locator_allowed_roots")
    @classmethod
    def validate_allowed_roots(cls, value: list[str]) -> list[str]:
        """Strip whitespace and drop blank entries from allowed roots.

        Args:
            value: Raw allowed roots list.

        Returns:
            Cleaned list with blank entries removed.
        """
        return [item.strip() for item in value if item.strip()]

    @model_validator(mode="after")
    def validate_locator_policy(self) -> "StorageConfig":
        """Require allowed_roots when locator policy is allow_configured_roots.

        Returns:
            Validated model instance.

        Raises:
            ValueError: When allowed_roots is empty for the policy.
        """
        if self.artifact_locator_policy == "allow_configured_roots" and not self.artifact_locator_allowed_roots:
            raise ValueError(
                "storage.artifact_locator_allowed_roots must be set when "
                "storage.artifact_locator_policy='allow_configured_roots'"
            )
        return self


class SessionsConfig(BaseModel):
    """Session limit and lifecycle settings."""

    model_config = ConfigDict(extra="forbid")

    max_active: int | None = Field(default=None, gt=0)
    max_in_flight_per_session: int | None = Field(default=None, gt=0)
    idle_ttl_seconds: int | None = Field(default=None, gt=0)
    allow_revival: bool = True
    tombstone_ttl_seconds: int = Field(default=86400, gt=0)
    upstream_session_termination_retries: int = Field(default=1, ge=0, le=5)
    max_total_session_size: int | str | None = None
    eviction_policy: Literal["lru_uploads_then_artifacts", "lru_artifacts_then_uploads"] = "lru_uploads_then_artifacts"

    @field_validator("max_total_session_size", mode="before")
    @classmethod
    def validate_max_total_session_size(cls, value: int | str | None) -> int | None:
        """Parse human-readable byte size for sessions.max_total_session_size.

        Args:
            value: Raw byte size value.

        Returns:
            Parsed integer byte count or None.
        """
        return parse_byte_size(value, "sessions.max_total_session_size")


class UploadsConfig(BaseModel):
    """Upload staging settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_file_bytes: int | str = 10 * 1024 * 1024
    ttl_seconds: int | None = Field(default=120, gt=0)
    require_sha256: bool = False
    uri_scheme: str = "upload://"

    @field_validator("max_file_bytes", mode="before")
    @classmethod
    def validate_max_file_bytes(cls, value: int | str) -> int:
        """Parse and require positive byte size for uploads.max_file_bytes.

        Args:
            value: Raw byte size value.

        Returns:
            Parsed positive integer byte count.

        Raises:
            ValueError: When the parsed value is zero or negative.
        """
        parsed = parse_byte_size(value, "uploads.max_file_bytes")
        if parsed is None or parsed <= 0:
            raise ValueError("uploads.max_file_bytes must be > 0")
        return parsed

    @field_validator("uri_scheme")
    @classmethod
    def validate_upload_scheme(cls, value: str) -> str:
        """Normalize URI scheme and require ``://`` suffix.

        Args:
            value: Raw URI scheme.

        Returns:
            Lowercased scheme ending with ``://``.

        Raises:
            ValueError: When the scheme does not end with ``://``.
        """
        normalized = value.strip().lower()
        if not normalized.endswith("://"):
            raise ValueError("uploads.uri_scheme must end with '://'")
        return normalized


class ArtifactsConfig(BaseModel):
    """Artifact persistence settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    ttl_seconds: int | None = Field(default=600, gt=0)
    max_per_session: int | None = Field(default=None, gt=0)
    expose_as_resources: bool = True
    uri_scheme: str = "artifact://"

    @field_validator("uri_scheme")
    @classmethod
    def validate_artifact_scheme(cls, value: str) -> str:
        """Normalize artifact URI scheme and require ``://`` suffix.

        Args:
            value: Raw URI scheme.

        Returns:
            Lowercased scheme ending with ``://``.

        Raises:
            ValueError: When the scheme does not end with ``://``.
        """
        normalized = value.strip().lower()
        if not normalized.endswith("://"):
            raise ValueError("artifacts.uri_scheme must end with '://'")
        return normalized
