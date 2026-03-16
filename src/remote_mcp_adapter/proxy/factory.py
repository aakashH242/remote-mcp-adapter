"""Factory helpers for constructing per-server MCP proxies."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
from typing import Any

import httpx
from fastmcp.client import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.proxy import FastMCPProxy

from ..config import AdapterConfig, ServerConfig
from ..core.storage.store import SessionStore
from .code_mode import build_code_mode_transforms, resolve_code_mode_enabled
from .resilient_client import ResilientClient
from .tool_description_policy import (
    ToolDescriptionPolicyTransform,
    resolve_tool_description_policy,
)
from .tool_metadata_sanitization import (
    ToolMetadataSanitizationTransform,
    resolve_tool_metadata_sanitization_policy,
)
from .tool_definition_pinning import (
    ToolDefinitionPinningTransform,
    resolve_tool_definition_pinning_policy,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SessionClientRegistry:
    """Session-aware upstream client cache for one configured server."""

    server: ServerConfig
    session_store: SessionStore | None = None
    default_timeout_seconds: int | None = None
    session_termination_retries: int = 1
    metadata_cache_ttl_seconds: int = 30
    bypass_list_tools_cache: bool = False
    _clients: dict[str, Client] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _build_headers(self, inbound_headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Build upstream headers from static and passthrough policies.

        Args:
            inbound_headers: Optional inbound request headers for passthrough.

        Returns:
            Merged header dict ready for upstream dispatch.
        """
        headers = dict(self.server.upstream.static_headers)
        if not inbound_headers:
            return headers

        lowered = {key.lower(): value for key, value in inbound_headers.items()}
        for header_name in self.server.upstream.client_headers.passthrough:
            value = lowered.get(header_name.lower())
            if value:
                headers[header_name] = value
        return headers

    def _validate_required_headers(self, inbound_headers: Mapping[str, str] | None) -> None:
        """Ensure required client headers are present before upstream calls.

        Args:
            inbound_headers: Inbound request headers to check.

        Raises:
            RuntimeError: If any required headers are missing.
        """
        required = self.server.upstream.client_headers.required
        if not required:
            return
        lowered = {key.lower(): value for key, value in (inbound_headers or {}).items()}
        missing = [name for name in required if not lowered.get(name.lower())]
        if missing:
            raise RuntimeError(f"Missing required client headers for server '{self.server.id}': {', '.join(missing)}")

    def _build_httpx_client_factory(self) -> Any | None:
        """Build an AsyncClient factory when insecure TLS mode is enabled."""
        if not self.server.upstream.insecure_tls:
            return None

        def factory(**kwargs: Any) -> httpx.AsyncClient:
            """Create AsyncClient with TLS verification disabled.

            Args:
                **kwargs: Keyword arguments forwarded to ``httpx.AsyncClient``.
            """
            kwargs.setdefault("verify", False)
            return httpx.AsyncClient(**kwargs)

        return factory

    def _build_transport(self, headers: Mapping[str, str] | None = None) -> Any:
        """Create the concrete transport object based on upstream transport type.

        Args:
            headers: Optional headers to inject into the transport.
        """
        httpx_client_factory = self._build_httpx_client_factory()
        common_kwargs: dict[str, Any] = {"headers": dict(headers or {})}
        if httpx_client_factory is not None:
            common_kwargs["httpx_client_factory"] = httpx_client_factory

        if self.server.upstream.transport == "sse":
            return SSETransport(url=self.server.upstream.url, **common_kwargs)
        return StreamableHttpTransport(url=self.server.upstream.url, **common_kwargs)

    def _build_client(self, inbound_headers: Mapping[str, str] | None = None) -> Client:
        """Create a fresh fastmcp client for this server.

        Args:
            inbound_headers: Optional inbound request headers for passthrough.
        """
        headers = self._build_headers(inbound_headers)
        transport = self._build_transport(headers)
        return ResilientClient(
            transport=transport,
            timeout=self.default_timeout_seconds,
            default_timeout=self.default_timeout_seconds,
            session_termination_retries=self.session_termination_retries,
            metadata_cache_ttl_seconds=self.metadata_cache_ttl_seconds,
            bypass_list_tools_cache=self.bypass_list_tools_cache,
        )

    async def get_session_client(self) -> Client:
        """Return the session-pinned client for the current MCP request context.

        Returns:
            Cached or freshly created upstream ``Client``.
        """
        ctx = get_context()
        session_id = ctx.session_id
        inbound_headers: Mapping[str, str] | None = None
        if ctx.request_context and ctx.request_context.request:
            inbound_headers = ctx.request_context.request.headers
        self._validate_required_headers(inbound_headers)
        if self.session_store is not None:
            await self.session_store.touch_tool_activity(self.server.id, session_id)

        async with self._lock:
            client = self._clients.get(session_id)
            if client is None:
                client = self._build_client(inbound_headers)
                # Keep one upstream session open per adapter session. Per-call
                # async-with scopes then nest without terminating the upstream
                # MCP session on every tool/resource/prompt operation.
                await client.__aenter__()
                self._clients[session_id] = client
                logger.info(
                    "Created upstream session client",
                    extra={"server_id": self.server.id, "session_id": session_id},
                )
            return client

    def build_probe_client(self, *, timeout_seconds: int | float | None = None) -> Client:
        """Build an isolated one-off client for health probing.

        Args:
            timeout_seconds: Override probe timeout in seconds.
        """
        resolved_timeout: int | float = 5
        if timeout_seconds is not None:
            resolved_timeout = timeout_seconds
        elif self.default_timeout_seconds is not None:
            resolved_timeout = min(resolved_timeout, self.default_timeout_seconds)
        headers = self._build_headers()
        transport = self._build_transport(headers)
        return Client(transport=transport, timeout=resolved_timeout)

    async def reset_cached_clients(self, *, reason: str) -> int:
        """Close and clear all cached clients for reconnect/reinitialize flow.

        Args:
            reason: Human-readable reason for the reset.

        Returns:
            Number of clients that were closed.
        """
        async with self._lock:
            session_clients = list(self._clients.items())
            self._clients.clear()

        for session_id, client in session_clients:
            try:
                await client.close()
                logger.warning(
                    "Reset upstream session client due to health policy",
                    extra={"server_id": self.server.id, "session_id": session_id, "reason": reason},
                )
            except Exception:
                logger.debug(
                    "Failed to close upstream client during reset",
                    extra={"server_id": self.server.id, "session_id": session_id, "reason": reason},
                    exc_info=True,
                )
        return len(session_clients)

    async def close_all(self) -> None:
        """Close all cached clients for this server."""
        async with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                await client.close()
            except Exception:
                logger.debug(
                    "Failed to close upstream client cleanly",
                    extra={"server_id": self.server.id},
                    exc_info=True,
                )


@dataclass(slots=True)
class ProxyMount:
    """Container for one configured proxy mount and its client registry."""

    server: ServerConfig
    proxy: FastMCPProxy
    clients: SessionClientRegistry
    wiring_readiness: "ServerWiringReadiness"


@dataclass(slots=True)
class ServerWiringReadiness:
    """Tracks whether one server mount is fully wired for stable discovery."""

    ready: bool = False

    def is_ready(self) -> bool:
        """Return whether the server tool surface is stable for pinning."""
        return self.ready

    def set_ready(self, ready: bool) -> None:
        """Update readiness state.

        Args:
            ready: Whether the server mount is ready for stable catalog pinning.
        """
        self.ready = ready


def _resolve_timeout_seconds(config: AdapterConfig, server: ServerConfig) -> int | None:
    """Resolve effective tool timeout for a server using config precedence.

    Args:
        config: Full adapter configuration.
        server: Server-specific configuration.

    Returns:
        Effective timeout in seconds, or ``None``.
    """
    return server.tool_defaults.tool_call_timeout_seconds or config.core.defaults.tool_call_timeout_seconds


def _resolve_code_mode_for_server(config: AdapterConfig, server: ServerConfig) -> bool:
    """Resolve effective Code Mode toggle for one server.

    This helper tolerates lightweight config doubles used in unit tests that
    may not define the new ``code_mode_enabled`` fields yet.

    Args:
        config: Full adapter configuration or compatible test double.
        server: Server-specific configuration or compatible test double.

    Returns:
        Effective Code Mode state for the server.
    """
    core_enabled = bool(getattr(config.core, "code_mode_enabled", False))
    server_enabled = getattr(server, "code_mode_enabled", None)
    return resolve_code_mode_enabled(
        core_enabled=core_enabled,
        server_enabled=server_enabled,
    )


def _build_server_transforms(
    *,
    config: AdapterConfig,
    server: ServerConfig,
    session_store: SessionStore | None,
    telemetry: Any | None,
    wiring_readiness: ServerWiringReadiness,
) -> list[Any]:
    """Build server-level transforms in the order they should run.

    Args:
        config: Full adapter configuration.
        server: Server-specific configuration.
        session_store: Shared session store, when available.
        telemetry: Optional telemetry recorder.

    Returns:
        Ordered transform list for the server proxy.
    """
    transforms: list[Any] = []
    sanitization_policy = resolve_tool_metadata_sanitization_policy(config=config, server=server)
    if sanitization_policy.enabled:
        transforms.append(
            ToolMetadataSanitizationTransform(
                server_id=server.id,
                policy=sanitization_policy,
            )
        )
    description_policy = resolve_tool_description_policy(config=config, server=server)
    if description_policy.enabled:
        transforms.append(
            ToolDescriptionPolicyTransform(
                server_id=server.id,
                policy=description_policy,
            )
        )
    pinning_policy = resolve_tool_definition_pinning_policy(config=config, server=server)
    if pinning_policy.enabled and session_store is not None:
        transforms.append(
            ToolDefinitionPinningTransform(
                server_id=server.id,
                session_store=session_store,
                policy=pinning_policy,
                telemetry=telemetry,
                catalog_ready=wiring_readiness.is_ready,
            )
        )
    transforms.extend(
        build_code_mode_transforms(
            enabled=_resolve_code_mode_for_server(config, server),
            server_id=server.id,
        )
    )
    return transforms


def build_proxy_map(
    config: AdapterConfig,
    session_store: SessionStore | None = None,
    *,
    telemetry: Any | None = None,
) -> dict[str, ProxyMount]:
    """Build per-server proxy objects keyed by server id.

    Args:
        config: Full adapter configuration.
        session_store: Optional session store for session-aware client caching.
        telemetry: Optional telemetry recorder for server transforms.

    Returns:
        Dict mapping server IDs to ``ProxyMount`` instances.
    """
    proxy_map: dict[str, ProxyMount] = {}
    for server in config.servers:
        timeout_seconds = _resolve_timeout_seconds(config, server)
        pinning_policy = resolve_tool_definition_pinning_policy(config=config, server=server)
        wiring_readiness = ServerWiringReadiness()
        client_registry = SessionClientRegistry(
            server=server,
            session_store=session_store,
            default_timeout_seconds=timeout_seconds,
            session_termination_retries=config.sessions.upstream_session_termination_retries,
            metadata_cache_ttl_seconds=config.core.upstream_metadata_cache_ttl_seconds,
            bypass_list_tools_cache=pinning_policy.enabled,
        )
        proxy = FastMCPProxy(
            name=f"MCP Proxy [{server.id}]",
            client_factory=client_registry.get_session_client,
            transforms=_build_server_transforms(
                config=config,
                server=server,
                session_store=session_store,
                telemetry=telemetry,
                wiring_readiness=wiring_readiness,
            ),
        )
        proxy_map[server.id] = ProxyMount(
            server=server,
            proxy=proxy,
            clients=client_registry,
            wiring_readiness=wiring_readiness,
        )
    return proxy_map
