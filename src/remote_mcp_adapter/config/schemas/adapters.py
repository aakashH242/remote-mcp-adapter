"""Adapter definition schema models."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Discriminator, Field, field_validator, model_validator

from .common import ToolDefaults


class UploadConsumerAdapterConfig(BaseModel):
    """Adapter config for tools that consume upload handles."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["upload_consumer"]
    tools: list[str] = Field(min_length=1)
    file_path_argument: str
    uri_scheme: str = "upload://"
    uri_prefix: bool | None = None
    overrides: ToolDefaults = Field(default_factory=ToolDefaults)

    @field_validator("file_path_argument")
    @classmethod
    def validate_file_path_argument(cls, value: str) -> str:
        """Reject blank file_path_argument values.

        Args:
            value: Raw field value.

        Returns:
            Stripped non-blank string.

        Raises:
            ValueError: When the value is blank.
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("upload_consumer.file_path_argument is required")
        return normalized


class OutputLocatorConfig(BaseModel):
    """How artifact paths/content are extracted from tool output."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["structured", "regex", "embedded", "none"] = "none"
    output_path_key: str | None = None
    output_path_regexes: list[str] = Field(default_factory=list)


class ArtifactProducerAdapterConfig(BaseModel):
    """Adapter config for tools that produce artifacts."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["artifact_producer"]
    tools: list[str] = Field(min_length=1)
    output_path_argument: str | None = None
    output_locator: OutputLocatorConfig = Field(default_factory=OutputLocatorConfig)
    persist: bool = True
    expose_as_resource: bool = True
    allow_raw_output: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("allow_raw_output", "include_raw_output"),
    )
    overrides: ToolDefaults = Field(default_factory=ToolDefaults)

    @model_validator(mode="after")
    def validate_locator_requirements(self) -> "ArtifactProducerAdapterConfig":
        """Ensure output_path_key is set when mode is structured and output_path_argument is null.

        Returns:
            Validated model instance.

        Raises:
            ValueError: When required locator fields are missing.
        """
        if self.output_path_argument is not None:
            return self
        if self.output_locator.mode == "structured" and not (self.output_locator.output_path_key or "").strip():
            raise ValueError(
                "artifact_producer.output_locator.output_path_key is required when "
                "mode='structured' and output_path_argument is null"
            )
        return self


AdapterDefinition = Annotated[
    UploadConsumerAdapterConfig | ArtifactProducerAdapterConfig,
    Discriminator("type"),
]
