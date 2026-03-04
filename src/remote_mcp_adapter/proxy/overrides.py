"""Adapter/server/core override precedence helpers."""

from __future__ import annotations

from ..config import ToolDefaults


def _first_defined(*values):
    """Return the first non-None value from the sequence, or None if all are None.

    Args:
        *values: Values to check in order.
    """
    for value in values:
        if value is not None:
            return value
    return None


def resolve_tool_timeout_seconds(
    *,
    core_defaults: ToolDefaults,
    server_defaults: ToolDefaults,
    adapter_overrides: ToolDefaults,
) -> int | None:
    """Resolve tool timeout precedence: adapter > server > core.

    Args:
        core_defaults: Core-level default tool settings.
        server_defaults: Server-level default tool settings.
        adapter_overrides: Adapter-level override tool settings.
    """
    return _first_defined(
        adapter_overrides.tool_call_timeout_seconds,
        server_defaults.tool_call_timeout_seconds,
        core_defaults.tool_call_timeout_seconds,
    )


def resolve_allow_raw_output(
    *,
    core_defaults: ToolDefaults,
    server_defaults: ToolDefaults,
    adapter_overrides: ToolDefaults,
    adapter_allow_raw_output: bool | None = None,
) -> bool:
    """Resolve allow_raw_output precedence: adapter explicit > adapter overrides > server > core.

    Args:
        core_defaults: Core-level default tool settings.
        server_defaults: Server-level default tool settings.
        adapter_overrides: Adapter-level override tool settings.
        adapter_allow_raw_output: Optional explicit override from adapter config.
    """
    value = _first_defined(
        adapter_allow_raw_output,
        adapter_overrides.allow_raw_output,
        server_defaults.allow_raw_output,
        core_defaults.allow_raw_output,
        False,
    )
    return bool(value)
