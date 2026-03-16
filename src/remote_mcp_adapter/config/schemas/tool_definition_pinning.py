"""Schema models for tool-definition pinning policy."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ToolDefinitionPinningMode = Literal["off", "warn", "block"]
ToolDefinitionPinningBlockStrategy = Literal["error", "baseline_subset"]
ToolDefinitionPinningSessionAction = Literal["keep", "invalidate"]


class ToolDefinitionPinningConfig(BaseModel):
    """Global tool-definition pinning defaults."""

    model_config = ConfigDict(extra="forbid")

    mode: ToolDefinitionPinningMode = "warn"
    block_strategy: ToolDefinitionPinningBlockStrategy = "error"
    block_error_session_action: ToolDefinitionPinningSessionAction = "invalidate"


class ToolDefinitionPinningOverridesConfig(BaseModel):
    """Per-server overrides for tool-definition pinning policy."""

    model_config = ConfigDict(extra="forbid")

    mode: ToolDefinitionPinningMode | None = None
    block_strategy: ToolDefinitionPinningBlockStrategy | None = None
    block_error_session_action: ToolDefinitionPinningSessionAction | None = None
