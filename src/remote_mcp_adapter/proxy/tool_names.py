"""Shared naming helpers for synthetic proxy-exposed tools."""

from __future__ import annotations

import re

_TOOL_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_]+")


def normalize_server_tool_prefix(server_id: str) -> str:
    """Return a deterministic, tool-safe prefix for one server id.

    Args:
        server_id: Configured server identifier.

    Returns:
        Sanitized prefix safe to use in FastMCP tool names.
    """
    normalized = _TOOL_NAME_SAFE_RE.sub("_", server_id.strip()).strip("_")
    if not normalized:
        return "server"
    return normalized


def get_upload_url_tool_name(server_id: str) -> str:
    """Return the server-prefixed upload helper tool name.

    Args:
        server_id: Configured server identifier.

    Returns:
        Synthetic upload helper tool name.
    """
    return f"{normalize_server_tool_prefix(server_id)}_get_upload_url"


def code_mode_execute_tool_name(server_id: str) -> str:
    """Return the server-prefixed Code Mode execute tool name.

    Args:
        server_id: Configured server identifier.

    Returns:
        Synthetic execute tool name for one proxy mount.
    """
    return f"{normalize_server_tool_prefix(server_id)}_execute"


def code_mode_search_tool_name(server_id: str) -> str:
    """Return the server-prefixed Code Mode search tool name.

    Args:
        server_id: Configured server identifier.

    Returns:
        Synthetic search tool name for one proxy mount.
    """
    return f"{normalize_server_tool_prefix(server_id)}_search"


def code_mode_get_schema_tool_name(server_id: str) -> str:
    """Return the server-prefixed Code Mode schema tool name.

    Args:
        server_id: Configured server identifier.

    Returns:
        Synthetic schema discovery tool name for one proxy mount.
    """
    return f"{normalize_server_tool_prefix(server_id)}_get_schema"


def code_mode_tags_tool_name(server_id: str) -> str:
    """Return the server-prefixed Code Mode tags tool name.

    Args:
        server_id: Configured server identifier.

    Returns:
        Synthetic tag discovery tool name for one proxy mount.
    """
    return f"{normalize_server_tool_prefix(server_id)}_tags"


def code_mode_list_tools_tool_name(server_id: str) -> str:
    """Return the server-prefixed Code Mode list-tools name.

    Args:
        server_id: Configured server identifier.

    Returns:
        Synthetic list-tools discovery tool name for one proxy mount.
    """
    return f"{normalize_server_tool_prefix(server_id)}_list_tools"
