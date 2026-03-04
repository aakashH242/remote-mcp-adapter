"""Upstream connection schema models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UpstreamClientHeadersConfig(BaseModel):
    """Client header requirements and passthrough policy."""

    model_config = ConfigDict(extra="forbid")

    required: list[str] = Field(default_factory=list)
    passthrough: list[str] = Field(default_factory=list)


class UpstreamConfig(BaseModel):
    """Upstream server connection settings."""

    model_config = ConfigDict(extra="forbid")

    transport: Literal["streamable_http", "sse"] = "streamable_http"
    url: str
    insecure_tls: bool = False
    static_headers: dict[str, str] = Field(default_factory=dict)
    client_headers: UpstreamClientHeadersConfig = Field(default_factory=UpstreamClientHeadersConfig)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Strip whitespace and reject blank upstream URL.

        Args:
            value: Raw upstream URL.

        Returns:
            Stripped non-blank URL.

        Raises:
            ValueError: When the URL is blank.
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("servers[].upstream.url is required")
        return normalized
