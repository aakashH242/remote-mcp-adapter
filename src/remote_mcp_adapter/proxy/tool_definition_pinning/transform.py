"""Catalog transform that pins tool definitions for one adapter session."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from collections.abc import Callable
from typing import Any

from fastmcp.exceptions import FastMCPError, ToolError
from fastmcp.server.dependencies import get_context
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.catalog import CatalogTransform
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec

from ...core.repo.records import ToolDefinitionBaseline, now_ts
from ...core.storage.store import SessionStore
from ...telemetry import AdapterTelemetry
from .canonical import canonicalize_tool, canonicalize_tools
from .diff import build_drift_preview, compare_tool_catalogs, differing_top_level_fields
from .models import ToolDefinitionDriftResult, ToolDefinitionPinningPolicy
from .warnings import apply_catalog_warnings

logger = logging.getLogger(__name__)


class ToolDefinitionPinningTransform(CatalogTransform):
    """Protect a session from mid-session tool-definition drift."""

    def __init__(
        self,
        *,
        server_id: str,
        session_store: SessionStore,
        policy: ToolDefinitionPinningPolicy,
        telemetry: AdapterTelemetry | None = None,
        catalog_ready: Callable[[], bool] | None = None,
    ) -> None:
        """Initialize the pinning transform.

        Args:
            server_id: Configured upstream server identifier.
            session_store: Shared session store used for persistence.
            policy: Effective pinning policy for this server.
            telemetry: Optional telemetry recorder.
            catalog_ready: Optional readiness check for whether this server's
                visible tool surface is fully wired and safe to pin.
        """
        super().__init__()
        self._server_id = server_id
        self._session_store = session_store
        self._policy = policy
        self._telemetry = telemetry
        self._catalog_ready = catalog_ready

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Pin or enforce the client-visible tool catalog for the current session.

        Args:
            tools: Current tool catalog before any later transforms.

        Returns:
            Either the original catalog, a warning-annotated catalog, or the
            unchanged trusted subset.

        Raises:
            FastMCPError: When drift is detected in ``block + error`` mode.
        """
        if not self._policy.enabled:
            return tools

        session_id = self._current_session_id()
        if session_id is None:
            return tools
        terminal_reason = await self._session_store.get_terminal_session_reason(self._server_id, session_id)
        if terminal_reason is not None:
            raise FastMCPError(self._terminal_session_message(reason=terminal_reason))

        baseline = await self._session_store.get_tool_definition_baseline(self._server_id, session_id)
        if baseline is None and not self._catalog_is_ready():
            return tools
        if baseline is None:
            await self._pin_baseline(session_id=session_id, tools=tools)
            return tools

        current_snapshots = canonicalize_tools(tuple(tools))
        drift = compare_tool_catalogs(baseline=baseline, current=current_snapshots)
        if not drift.has_drift:
            await self._session_store.clear_tool_definition_drift_summary(self._server_id, session_id)
            return tools

        await self._record_drift(session_id=session_id, drift=drift)
        if self._policy.warn_only:
            return apply_catalog_warnings(tools=tools, drift=drift)
        if self._policy.baseline_subset:
            trusted_names = set(drift.unchanged_tools)
            return [tool for tool in tools if tool.name in trusted_names]
        await self._invalidate_session_if_configured(session_id=session_id, preview=drift.preview)
        raise FastMCPError(self._catalog_block_error_message(session_id=session_id, preview=drift.preview))

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        """Allow or deny direct tool lookups against the pinned baseline.

        Args:
            name: Requested tool name.
            call_next: Downstream tool lookup callable.
            version: Optional version filter.

        Returns:
            Resolved tool, a blocking tool, or ``None`` when the tool should
            remain hidden.
        """
        tool = await call_next(name, version=version)
        if not self._policy.enabled:
            return tool

        session_id = self._current_session_id()
        if session_id is None:
            return tool
        terminal_reason = await self._session_store.get_terminal_session_reason(self._server_id, session_id)
        if terminal_reason is not None:
            return build_blocking_tool(
                name=name,
                message=self._terminal_session_message(reason=terminal_reason),
                original=tool,
            )

        baseline = await self._session_store.get_tool_definition_baseline(self._server_id, session_id)
        if baseline is None and not self._catalog_is_ready():
            return tool
        if baseline is None:
            return tool

        drift = self._compare_one_tool(name=name, baseline=baseline, tool=tool)
        if drift is None:
            return tool

        await self._record_drift(session_id=session_id, drift=drift)
        if self._policy.warn_only:
            return tool
        if self._policy.baseline_subset:
            return None
        await self._invalidate_session_if_configured(session_id=session_id, preview=drift.preview)
        return build_blocking_tool(
            name=name,
            message=self._direct_call_block_message(session_id=session_id, tool_name=name, preview=drift.preview),
            original=tool,
        )

    def _current_session_id(self) -> str | None:
        """Return the current adapter session id from FastMCP request context.

        Returns:
            Current session id, or ``None`` when called outside request scope.
        """
        try:
            return str(get_context().session_id)
        except Exception:
            return None

    def _catalog_is_ready(self) -> bool:
        """Return whether this server mount is ready for stable baseline pinning.

        Returns:
            ``True`` when the server's visible tool surface is ready. Defaults
            to ``True`` when no readiness callback was provided.
        """
        if self._catalog_ready is None:
            return True
        try:
            return bool(self._catalog_ready())
        except Exception:
            logger.debug(
                "Catalog readiness check failed; treating server as not ready for pinning",
                extra={"server_id": self._server_id},
                exc_info=True,
            )
            return False

    async def _pin_baseline(self, *, session_id: str, tools: Sequence[Tool]) -> None:
        """Persist the initial baseline for the current adapter session.

        Args:
            session_id: Adapter session identifier.
            tools: Current visible tool catalog.
        """
        baseline = ToolDefinitionBaseline(established_at=now_ts(), tools=canonicalize_tools(tuple(tools)))
        await self._session_store.set_tool_definition_baseline(self._server_id, session_id, baseline)
        await self._session_store.clear_tool_definition_drift_summary(self._server_id, session_id)
        logger.info(
            "Pinned tool-definition baseline for adapter session",
            extra={
                "server_id": self._server_id,
                "session_id": session_id,
                "tool_count": len(baseline.tools),
                "mode": self._policy.mode,
                "block_strategy": self._policy.block_strategy,
            },
        )

    async def _record_drift(self, *, session_id: str, drift: ToolDefinitionDriftResult) -> None:
        """Persist and report drift when it differs from the last stored summary.

        Args:
            session_id: Adapter session identifier.
            drift: Drift comparison result to persist and emit.
        """
        summary = drift.to_summary(policy=self._policy, detected_at=now_ts())
        existing = await self._session_store.get_tool_definition_drift_summary(self._server_id, session_id)
        if existing is not None and existing.fingerprint() == summary.fingerprint():
            return

        await self._session_store.set_tool_definition_drift_summary(self._server_id, session_id, summary)
        logger.warning(
            "Tool-definition drift detected for adapter session",
            extra={
                "server_id": self._server_id,
                "session_id": session_id,
                "mode": summary.mode,
                "block_strategy": summary.block_strategy,
                "block_error_session_action": self._policy.block_error_session_action,
                "changed_tools": summary.changed_tools,
                "new_tools": summary.new_tools,
                "removed_tools": summary.removed_tools,
                "preview": summary.preview,
            },
        )
        if self._telemetry is not None and getattr(self._telemetry, "enabled", False):
            await self._telemetry.record_tool_definition_drift(
                server_id=self._server_id,
                mode=summary.mode,
                block_strategy=summary.block_strategy,
                outcome=self._telemetry_outcome(),
            )

    def _compare_one_tool(
        self,
        *,
        name: str,
        baseline: ToolDefinitionBaseline,
        tool: Tool | None,
    ) -> ToolDefinitionDriftResult | None:
        """Compare one tool lookup against the pinned baseline.

        Args:
            name: Requested tool name.
            baseline: Pinned baseline.
            tool: Current resolved tool, if any.

        Returns:
            Drift result for the single tool, or ``None`` when the tool remains
            trusted.
        """
        baseline_snapshot = baseline.tools.get(name)
        if tool is None:
            if baseline_snapshot is None:
                return None
            return ToolDefinitionDriftResult(
                removed_tools=(name,),
                preview=build_drift_preview(
                    changed_tools=(),
                    new_tools=(),
                    removed_tools=(name,),
                    changed_fields={},
                ),
            )

        current_snapshot = canonicalize_tool(tool)
        if baseline_snapshot is None:
            return ToolDefinitionDriftResult(
                new_tools=(name,),
                preview=build_drift_preview(
                    changed_tools=(),
                    new_tools=(name,),
                    removed_tools=(),
                    changed_fields={},
                ),
            )
        if baseline_snapshot.canonical_hash == current_snapshot.canonical_hash:
            return None

        changed_fields = differing_top_level_fields(
            baseline=baseline_snapshot,
            current=current_snapshot,
        )
        return ToolDefinitionDriftResult(
            changed_tools=(name,),
            changed_fields={name: changed_fields},
            preview=build_drift_preview(
                changed_tools=(name,),
                new_tools=(),
                removed_tools=(),
                changed_fields={name: changed_fields},
            ),
        )

    def _catalog_block_error_message(self, *, session_id: str, preview: str | None) -> str:
        """Build the catalog error message for ``block + error`` mode.

        Args:
            session_id: Adapter session identifier.
            preview: Concise drift preview.

        Returns:
            Human-readable error message.
        """
        message = (
            "Tool definition drift detected for this adapter session. "
            "Start a new Mcp-Session-Id to accept upstream tool updates."
        )
        if self._policy.invalidates_session_on_block_error:
            message = (
                "Tool definition drift detected for this adapter session. "
                "The current session was invalidated. Start a new Mcp-Session-Id to accept upstream tool updates."
            )
        if preview:
            message = f"{message} Drift: {preview}"
        return message

    def _direct_call_block_message(self, *, session_id: str, tool_name: str, preview: str | None) -> str:
        """Build the direct-call error message for a drifted tool.

        Args:
            session_id: Adapter session identifier.
            tool_name: Tool name being blocked.
            preview: Concise drift preview.

        Returns:
            Human-readable error message for tool execution.
        """
        message = (
            f"Tool '{tool_name}' is blocked because its definition drifted after this adapter session pinned "
            "the upstream tool catalog. Start a new Mcp-Session-Id to accept upstream tool updates."
        )
        if self._policy.invalidates_session_on_block_error:
            message = (
                f"Tool '{tool_name}' is blocked because its definition drifted after this adapter session pinned "
                "the upstream tool catalog. The current session was invalidated. Start a new Mcp-Session-Id "
                "to accept upstream tool updates."
            )
        if preview:
            message = f"{message} Drift: {preview}"
        return message

    def _telemetry_outcome(self) -> str:
        """Return the telemetry outcome label for the current policy.

        Returns:
            Outcome label describing how drift is enforced.
        """
        if self._policy.warn_only:
            return "warn"
        if self._policy.baseline_subset:
            return "block_baseline_subset"
        if self._policy.invalidates_session_on_block_error:
            return "block_error_invalidate_session"
        return "block_error"

    async def _invalidate_session_if_configured(self, *, session_id: str, preview: str | None) -> None:
        """Invalidate the current adapter session when the policy requires it.

        Args:
            session_id: Adapter session identifier.
            preview: Concise drift preview for the invalidation reason.
        """
        if not self._policy.invalidates_session_on_block_error:
            return
        await self._session_store.invalidate_session(
            server_id=self._server_id,
            session_id=session_id,
            reason=self._tool_definition_invalidation_reason(preview=preview),
        )

    @staticmethod
    def _tool_definition_invalidation_reason(preview: str | None) -> str:
        """Build the persisted session invalidation reason for drift.

        Args:
            preview: Concise drift preview.

        Returns:
            Human-readable invalidation reason.
        """
        message = "Upstream tool catalog changed after this adapter session pinned its baseline."
        if preview:
            return f"{message} Drift: {preview}"
        return message

    @staticmethod
    def _terminal_session_message(*, reason: str) -> str:
        """Build the direct client-facing message for an invalidated session.

        Args:
            reason: Persisted terminal invalidation reason.

        Returns:
            Error message instructing the client to start a new session.
        """
        return f"{reason} Start a new Mcp-Session-Id to continue."


def build_blocking_tool(*, name: str, message: str, original: Tool | None) -> Tool:
    """Build a permissive tool that always fails with a drift explanation.

    Args:
        name: Tool name to expose.
        message: Error message raised on every invocation.
        original: Original tool when it still exists upstream.

    Returns:
        Synthetic tool that always raises ``ToolError``.
    """

    class BlockingTool(Tool):
        """Permissive synthetic tool used to block drifted direct calls."""

        async def run(self, arguments: dict[str, Any]) -> ToolResult:
            """Reject every execution attempt for the blocked tool.

            Args:
                arguments: Caller-supplied tool arguments.

            Raises:
                ToolError: Always raised with the blocking reason.
            """
            raise ToolError(message)

    return BlockingTool(
        name=name,
        title=original.title if original is not None else None,
        description=message,
        icons=original.icons if original is not None else None,
        tags=original.tags if original is not None else None,
        meta=original.meta if original is not None else None,
        annotations=original.annotations if original is not None else None,
        parameters={"type": "object", "additionalProperties": True},
        output_schema=original.output_schema if original is not None else None,
    )
