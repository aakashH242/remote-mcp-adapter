"""Schema models for model-visible tool metadata sanitization."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ToolMetadataSanitizationMode = Literal["off", "sanitize", "block"]


class ToolMetadataSanitizationConfig(BaseModel):
    """Global defaults for tool metadata sanitization."""

    model_config = ConfigDict(extra="forbid")

    mode: ToolMetadataSanitizationMode = "sanitize"
    normalize_unicode: bool = True
    remove_invisible_characters: bool = True
    max_tool_title_chars: int | None = Field(default=256, gt=0)
    max_tool_description_chars: int | None = Field(default=2000, gt=0)
    max_schema_text_chars: int | None = Field(default=1000, gt=0)


class ToolMetadataSanitizationOverridesConfig(BaseModel):
    """Per-server overrides for tool metadata sanitization."""

    model_config = ConfigDict(extra="forbid")

    mode: ToolMetadataSanitizationMode | None = None
    normalize_unicode: bool | None = None
    remove_invisible_characters: bool | None = None
    max_tool_title_chars: int | None = Field(default=None, gt=0)
    max_tool_description_chars: int | None = Field(default=None, gt=0)
    max_schema_text_chars: int | None = Field(default=None, gt=0)
