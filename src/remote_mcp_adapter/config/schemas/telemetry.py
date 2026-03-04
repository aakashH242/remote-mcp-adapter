"""Telemetry schema models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TelemetryTransport = Literal["grpc", "http"]


class TelemetryConfig(BaseModel):
    """OpenTelemetry export settings for metrics and optional logs."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    transport: TelemetryTransport = "grpc"
    endpoint: str | None = None
    logs_endpoint: str | None = None
    insecure: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    export_interval_seconds: int = Field(default=15, gt=0)
    export_timeout_seconds: int = Field(default=10, gt=0)
    max_queue_size: int = Field(default=5000, gt=0)
    queue_batch_size: int = Field(default=256, gt=0)
    periodic_flush_seconds: int = Field(default=5, gt=0)
    shutdown_drain_timeout_seconds: int = Field(default=10, gt=0)
    log_batch_max_queue_size: int | None = Field(default=None, gt=0)
    log_batch_max_export_batch_size: int | None = Field(default=None, gt=0)
    log_batch_schedule_delay_millis: int | None = Field(default=None, gt=0)
    log_batch_export_timeout_millis: int | None = Field(default=None, gt=0)
    drop_on_queue_full: bool = True
    flush_on_shutdown: bool = True
    flush_on_terminate: bool = True
    emit_logs: bool = False
    service_name: str = "remote-mcp-adapter"
    service_namespace: str | None = None

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        """Strip whitespace and reject blank endpoint when set.

        Args:
            value: Raw endpoint URL or None.

        Returns:
            Stripped endpoint or None.

        Raises:
            ValueError: When value is a blank string.
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("telemetry.endpoint cannot be blank when set")
        return normalized

    @field_validator("logs_endpoint")
    @classmethod
    def validate_logs_endpoint(cls, value: str | None) -> str | None:
        """Strip whitespace and reject blank logs_endpoint when set.

        Args:
            value: Raw logs endpoint URL or None.

        Returns:
            Stripped endpoint or None.

        Raises:
            ValueError: When value is a blank string.
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("telemetry.logs_endpoint cannot be blank when set")
        return normalized

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        """Strip whitespace and reject blank service_name.

        Args:
            value: Raw service name.

        Returns:
            Stripped non-blank service name.

        Raises:
            ValueError: When the value is blank.
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("telemetry.service_name cannot be blank")
        return normalized

    @field_validator("service_namespace")
    @classmethod
    def validate_service_namespace(cls, value: str | None) -> str | None:
        """Strip whitespace and convert blank to None.

        Args:
            value: Raw namespace or None.

        Returns:
            Stripped namespace, or None when blank.
        """
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def apply_endpoint_defaults(self) -> "TelemetryConfig":
        """Set default endpoints based on transport when not explicitly configured.

        Returns:
            Model instance with endpoint defaults applied.
        """
        if self.endpoint is None:
            if self.transport == "grpc":
                self.endpoint = "http://localhost:4317"
            else:
                self.endpoint = "http://localhost:4318/v1/metrics"
        if self.logs_endpoint is None and self.transport == "http":
            self.logs_endpoint = "http://localhost:4318/v1/logs"
        return self
