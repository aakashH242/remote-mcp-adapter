"""Warning text helpers for tool-definition drift surfacing."""

from __future__ import annotations

from collections.abc import Sequence

from fastmcp.tools.tool import Tool

from .models import ToolDefinitionDriftResult

_SESSION_WARNING_PREFIX = "WARNING: Tool definitions changed during this adapter session."
_TOOL_CHANGED_WARNING = "WARNING: This tool definition changed after the session baseline was pinned."
_TOOL_NEW_WARNING = "WARNING: This tool was not present when the session baseline was pinned."


def apply_catalog_warnings(
    *,
    tools: Sequence[Tool],
    drift: ToolDefinitionDriftResult,
) -> list[Tool]:
    """Return tool copies annotated with session-wide and per-tool drift warnings.

    Args:
        tools: Current catalog tools.
        drift: Drift comparison result.

    Returns:
        Warning-annotated tool copies.
    """
    session_warning = build_session_warning_banner(drift.preview)
    changed_names = set(drift.changed_tools)
    new_names = set(drift.new_tools)

    warned_tools: list[Tool] = []
    for tool in tools:
        description = prepend_warning(tool.description, session_warning)
        if tool.name in changed_names:
            description = prepend_warning(description, _TOOL_CHANGED_WARNING)
        elif tool.name in new_names:
            description = prepend_warning(description, _TOOL_NEW_WARNING)
        warned_tools.append(tool.model_copy(update={"description": description}))
    return warned_tools


def build_session_warning_banner(preview: str | None) -> str:
    """Build the session-wide warning banner shown on all returned tools.

    Args:
        preview: Concise preview of detected drift.

    Returns:
        Human-readable warning banner.
    """
    if preview:
        return f"{_SESSION_WARNING_PREFIX} Drift detected: {preview}"
    return _SESSION_WARNING_PREFIX


def prepend_warning(description: str | None, warning_line: str) -> str:
    """Prepend one warning line unless it already exists.

    Args:
        description: Existing description text.
        warning_line: Warning line to prepend.

    Returns:
        Combined description text.
    """
    description_text = (description or "").strip()
    description_lines = [line.strip() for line in description_text.splitlines() if line.strip()]
    if warning_line in description_lines:
        return description_text
    if not description_text:
        return warning_line
    return f"{warning_line}\n\n{description_text}"
