"""Helpers for FastMCP Code Mode and proxy-side tool visibility."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp.server.providers.proxy import FastMCPProxy, ProxyProvider
from fastmcp.server.transforms import Transform
from fastmcp.server.transforms.visibility import Visibility

from .tool_names import (
    code_mode_execute_tool_name,
    code_mode_get_schema_tool_name,
    code_mode_list_tools_tool_name,
    code_mode_search_tool_name,
    code_mode_tags_tool_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


def resolve_code_mode_enabled(*, core_enabled: bool, server_enabled: bool | None) -> bool:
    """Resolve the effective Code Mode toggle for one server.

    Args:
        core_enabled: Global default from ``core.code_mode_enabled``.
        server_enabled: Optional per-server override.

    Returns:
        Effective Code Mode state for the server.
    """
    if server_enabled is not None:
        return bool(server_enabled)
    return bool(core_enabled)


def build_code_mode_transforms(*, enabled: bool, server_id: str) -> list[Transform]:
    """Build server-level FastMCP transforms for Code Mode.

    Args:
        enabled: Whether Code Mode should be active for the proxy mount.
        server_id: Configured server identifier for synthetic tool naming.

    Returns:
        Transform list suitable for ``FastMCP`` or ``FastMCPProxy``.

    Raises:
        RuntimeError: If Code Mode is enabled but FastMCP Code Mode
            dependencies are unavailable.
    """
    if not enabled:
        return []
    try:
        from fastmcp.experimental.transforms.code_mode import CodeMode, GetSchemas, GetTags, ListTools, Search
    except ModuleNotFoundError as exc:
        raise RuntimeError("Code Mode requires FastMCP Code Mode dependencies. Install `fastmcp[code-mode]`.") from exc
    return [
        CodeMode(
            execute_tool_name=code_mode_execute_tool_name(server_id),
            discovery_tools=[
                Search(name=code_mode_search_tool_name(server_id)),
                GetSchemas(name=code_mode_get_schema_tool_name(server_id)),
                GetTags(name=code_mode_tags_tool_name(server_id)),
                ListTools(name=code_mode_list_tools_tool_name(server_id)),
            ],
        )
    ]


def hide_upstream_tool_names(*, proxy: FastMCPProxy, tool_names: Iterable[str]) -> None:
    """Hide upstream originals for tool names replaced by local overrides.

    This applies a provider-level visibility transform to the upstream proxy
    provider only, so locally registered override tools with the same names
    remain visible and callable.

    Args:
        proxy: Server-specific FastMCP proxy instance.
        tool_names: Upstream tool names to hide from direct provider listings.

    """
    hidden_tool_names = {name for name in tool_names if name}
    if not hidden_tool_names:
        return
    provider = _find_proxy_provider(proxy)
    if provider is None:
        return
    provider.add_transform(
        Visibility(
            False,
            names=hidden_tool_names,
            components={"tool"},
        )
    )


def _find_proxy_provider(proxy: FastMCPProxy) -> ProxyProvider | None:
    """Return the upstream proxy provider attached to a FastMCP proxy.

    Args:
        proxy: FastMCP proxy server instance.

    Returns:
        The ``ProxyProvider`` used for upstream MCP access, or ``None`` when
        the proxy is a lightweight test double without upstream providers.
    """
    providers = getattr(proxy, "providers", None)
    if providers is None:
        return None
    for provider in reversed(providers):
        if isinstance(provider, ProxyProvider):
            return provider
    return None
