"""Runtime models and policy helpers for tool-definition pinning."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...config.schemas.root import AdapterConfig
from ...config.schemas.server import ServerConfig
from ...config.schemas.tool_definition_pinning import (
    ToolDefinitionPinningBlockStrategy,
    ToolDefinitionPinningMode,
    ToolDefinitionPinningSessionAction,
)
from ...core.repo.records import ToolDefinitionDriftSummary, ToolDefinitionSnapshot


@dataclass(frozen=True, slots=True)
class ToolDefinitionPinningPolicy:
    """Resolved tool-definition pinning policy for one server."""

    mode: ToolDefinitionPinningMode = "off"
    block_strategy: ToolDefinitionPinningBlockStrategy = "error"
    block_error_session_action: ToolDefinitionPinningSessionAction = "invalidate"

    @property
    def enabled(self) -> bool:
        """Return whether tool-definition pinning is active."""
        return self.mode != "off"

    @property
    def warn_only(self) -> bool:
        """Return whether drift should be surfaced without blocking."""
        return self.mode == "warn"

    @property
    def block(self) -> bool:
        """Return whether drift should block or hide tools."""
        return self.mode == "block"

    @property
    def baseline_subset(self) -> bool:
        """Return whether block mode should return only unchanged trusted tools."""
        return self.block and self.block_strategy == "baseline_subset"

    @property
    def invalidates_session_on_block_error(self) -> bool:
        """Return whether block+error should terminate the current adapter session."""
        return self.block and self.block_strategy == "error" and self.block_error_session_action == "invalidate"


@dataclass(frozen=True, slots=True)
class ToolDefinitionDriftResult:
    """Result of comparing the current catalog against the pinned baseline."""

    changed_tools: tuple[str, ...] = ()
    new_tools: tuple[str, ...] = ()
    removed_tools: tuple[str, ...] = ()
    unchanged_tools: tuple[str, ...] = ()
    changed_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    preview: str | None = None

    @property
    def has_drift(self) -> bool:
        """Return whether any changed, new, or removed tools were detected."""
        return bool(self.changed_tools or self.new_tools or self.removed_tools)

    def to_summary(self, *, policy: ToolDefinitionPinningPolicy, detected_at: float) -> ToolDefinitionDriftSummary:
        """Build a persistent drift summary from the comparison result.

        Args:
            policy: Effective enforcement policy used for this comparison.
            detected_at: UNIX timestamp for the drift event.

        Returns:
            Drift summary suitable for persistence and dedupe checks.
        """
        mode: ToolDefinitionPinningMode = "warn" if policy.warn_only else "block"
        return ToolDefinitionDriftSummary(
            detected_at=detected_at,
            mode=mode,
            block_strategy=policy.block_strategy,
            changed_tools=list(self.changed_tools),
            new_tools=list(self.new_tools),
            removed_tools=list(self.removed_tools),
            changed_fields={name: list(fields) for name, fields in self.changed_fields.items()},
            preview=self.preview,
        )


def resolve_tool_definition_pinning_policy(
    *,
    config: AdapterConfig,
    server: ServerConfig,
) -> ToolDefinitionPinningPolicy:
    """Resolve the effective tool-definition pinning policy for one server.

    Args:
        config: Top-level adapter configuration.
        server: Server-specific configuration.

    Returns:
        Effective policy using core defaults plus per-server overrides.
    """
    core_policy = config.core.tool_definition_pinning
    server_policy = server.tool_definition_pinning
    return ToolDefinitionPinningPolicy(
        mode=getattr(server_policy, "mode", None) or core_policy.mode,
        block_strategy=getattr(server_policy, "block_strategy", None) or core_policy.block_strategy,
        block_error_session_action=(
            getattr(server_policy, "block_error_session_action", None)
            or getattr(core_policy, "block_error_session_action", "invalidate")
        ),
    )


def trusted_tool_names(
    *,
    baseline_tools: dict[str, ToolDefinitionSnapshot],
    drift: ToolDefinitionDriftResult,
) -> set[str]:
    """Return the set of currently trusted tool names after drift evaluation.

    Args:
        baseline_tools: Pinned baseline snapshots keyed by tool name.
        drift: Drift comparison result.

    Returns:
        Set of unchanged tool names that remain trusted for the session.
    """
    return set(baseline_tools).intersection(drift.unchanged_tools)
