"""Schema models for forwarded tool-description handling."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ToolDescriptionPolicyMode = Literal["preserve", "truncate", "strip"]


class ToolDescriptionPolicyConfig(BaseModel):
    """Global defaults for forwarded tool-description handling."""

    model_config = ConfigDict(extra="forbid")

    mode: ToolDescriptionPolicyMode = "preserve"
    max_tool_description_chars: int | None = Field(default=280, gt=0)
    max_schema_description_chars: int | None = Field(default=280, gt=0)


class ToolDescriptionPolicyOverridesConfig(BaseModel):
    """Per-server overrides for forwarded tool-description handling."""

    model_config = ConfigDict(extra="forbid")

    mode: ToolDescriptionPolicyMode | None = None
    max_tool_description_chars: int | None = Field(default=None, gt=0)
    max_schema_description_chars: int | None = Field(default=None, gt=0)
