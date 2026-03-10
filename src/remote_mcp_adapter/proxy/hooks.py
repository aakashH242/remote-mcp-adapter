"""Proxy tool override wiring for configured adapters."""

from __future__ import annotations

import copy
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
from typing import Any
from urllib.parse import urlencode

from mcp.types import TextContent
from pydantic import PrivateAttr
from fastmcp.server.transforms.visibility import Visibility

from fastmcp import Context
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context
from fastmcp.tools.tool import Tool, ToolResult

from ..adapters.artifact_producer import handle_artifact_producer_tool
from ..adapters.upload_consumer import handle_upload_consumer_tool
from ..config import (
    AdapterConfig,
    ArtifactProducerAdapterConfig,
    UploadConsumerAdapterConfig,
    resolve_write_policy_lock_mode,
)
from ..core.storage.store import SessionStore
from .artifact_download_credentials import ArtifactDownloadCredentialManager
from .factory import ProxyMount
from .upload_credentials import UploadCredentialManager
from .local_resources import register_upload_workflow_resource
from .local_tools import get_upload_url_tool_name, register_get_upload_url_tool
from .overrides import resolve_allow_raw_output, resolve_tool_timeout_seconds
from .resources import SessionArtifactProvider
from .upload_helpers import build_artifact_download_path, derive_public_base_url

logger = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any], Context], Awaitable[ToolResult]]


def _append_download_link_block(content_blocks: list[Any], download_url: str) -> list[Any]:
    """Append a plain + markdown download link unless already present.

    Args:
        content_blocks: Existing result content blocks.
        download_url: Download URL to append.
    """
    for block in content_blocks:
        if isinstance(block, TextContent) and download_url in block.text:
            return content_blocks
    merged = list(content_blocks)
    merged.append(
        TextContent(type="text", text=(f"Artifact download URL: {download_url}\n" f"[Download artifact]({download_url})"))
    )
    return merged


@dataclass(slots=True)
class AdapterWireState:
    """Tracks idempotent wiring state across retries."""

    providers_added: set[str] = field(default_factory=set)
    local_resources_added: set[str] = field(default_factory=set)
    local_tools_added: set[str] = field(default_factory=set)
    registered_tools_by_server: dict[str, set[str]] = field(default_factory=dict)


class OverrideTool(Tool):
    """Tool wrapper that preserves upstream schema and delegates to custom handler."""

    _handler: ToolHandler = PrivateAttr()

    def __init__(self, handler: ToolHandler, **kwargs: Any):
        """Initialize the override tool.

        Args:
            handler: Custom async handler for tool execution.
            **kwargs: Keyword arguments forwarded to ``Tool``.
        """
        super().__init__(**kwargs)
        self._handler = handler

    async def run(self, arguments: dict[str, Any], context: Context | None = None) -> ToolResult:
        """Delegate execution to the injected custom handler.

        Args:
            arguments: Tool arguments dict.
            context: Optional MCP context; falls back to ``get_context()``.
        """
        ctx = context or get_context()
        return await self._handler(arguments, ctx)

    @classmethod
    def from_mcp_tool(
        cls,
        mcp_tool: Any,
        handler: ToolHandler,
        *,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> "OverrideTool":
        """Construct an OverrideTool from an upstream MCP tool, preserving its schema.

        Args:
            mcp_tool: Upstream MCP tool object.
            handler: Custom async handler for tool execution.
            description: Optional override description.
            parameters: Optional override input schema.
        """
        return cls(
            handler=handler,
            name=mcp_tool.name,
            title=mcp_tool.title,
            description=description if description is not None else mcp_tool.description,
            parameters=parameters if parameters is not None else mcp_tool.inputSchema,
            annotations=mcp_tool.annotations,
            output_schema=mcp_tool.outputSchema,
            icons=mcp_tool.icons,
            meta=mcp_tool.meta,
            tags=(mcp_tool.meta or {}).get("_fastmcp", {}).get("tags", []),
        )


def _append_description(existing: Any, addition: str) -> str:
    """Append addition to existing description text, separated by a blank line.

    Args:
        existing: Existing description value.
        addition: Text to append.
    """
    base = existing.strip() if isinstance(existing, str) else ""
    if not base:
        return addition
    return f"{base}\n\n{addition}"


def _upload_consumer_note(
    *,
    file_path_argument: str,
    uri_scheme: str,
    uri_prefix: bool | None,
    upload_endpoint_tool_name: str,
) -> str:
    """Build the adapter guidance note injected into upload_consumer tool descriptions.

    Args:
        file_path_argument: Name of the file-path argument.
        uri_scheme: URI scheme prefix for upload handles.
        uri_prefix: Whether to forward resolved paths as file:// URIs.
        upload_endpoint_tool_name: Server-specific upload tool name.
    """
    base_note = (
        f"Adapter behavior: `{file_path_argument}` must use `{uri_scheme}` handles scoped to the current session. "
        "Do not pass local filesystem paths. If unsure, call "
        f"`{upload_endpoint_tool_name}`, upload file(s), "
        f"then pass returned `{uri_scheme}` handles."
    )
    if uri_prefix:
        return f"{base_note} Resolved local paths are forwarded upstream as `file://` URIs."
    return base_note


def _clone_input_schema(mcp_tool: Any) -> dict[str, Any] | None:
    """Deep-copy the tool's input schema if it is a dict, else return None.

    Args:
        mcp_tool: Upstream MCP tool object.
    """
    schema = mcp_tool.inputSchema
    if isinstance(schema, dict):
        return copy.deepcopy(schema)
    return None


def _annotate_schema_path_description(schema: dict[str, Any], dotted_path: str, note: str) -> bool:
    """Best-effort JSON schema path annotation for tool argument guidance.

    Args:
        schema: JSON schema dict to annotate.
        dotted_path: Dot-separated path to the target property.
        note: Guidance text to inject.

    Returns:
        True if the annotation was applied successfully.
    """
    current: dict[str, Any] = schema
    parts = [part for part in dotted_path.split(".") if part]
    if not parts:
        return False

    for index, part in enumerate(parts):
        properties = current.get("properties")
        if not isinstance(properties, dict):
            return False
        node = properties.get(part)
        if not isinstance(node, dict):
            return False

        is_leaf = index == len(parts) - 1
        if is_leaf:
            node["description"] = _append_description(node.get("description"), note)
            return True

        next_node = node
        if next_node.get("type") == "array" and isinstance(next_node.get("items"), dict):
            next_node = next_node["items"]
        current = next_node

    return False


def _build_upload_consumer_override_tool(
    *,
    upstream_tool: Any,
    handler: ToolHandler,
    adapter: UploadConsumerAdapterConfig,
    upload_endpoint_tool_name: str,
) -> OverrideTool:
    """Wrap the upstream tool with upload handle rewriting and annotated schema.

    Args:
        upstream_tool: Upstream MCP tool object.
        handler: Custom async handler for tool execution.
        adapter: Upload consumer adapter configuration.
        upload_endpoint_tool_name: Server-specific upload tool name.
    """
    note = _upload_consumer_note(
        file_path_argument=adapter.file_path_argument,
        uri_scheme=adapter.uri_scheme,
        uri_prefix=adapter.uri_prefix,
        upload_endpoint_tool_name=upload_endpoint_tool_name,
    )
    description = _append_description(upstream_tool.description, note)
    schema = _clone_input_schema(upstream_tool)
    if schema is not None:
        annotated = _annotate_schema_path_description(schema, adapter.file_path_argument, note)
        if not annotated:
            schema["description"] = _append_description(schema.get("description"), note)

    return OverrideTool.from_mcp_tool(
        upstream_tool,
        handler=handler,
        description=description,
        parameters=schema,
    )


async def _list_upstream_tools(mount: ProxyMount) -> dict[str, Any]:
    """Probe the upstream server and return its tool list keyed by tool name.

    Args:
        mount: Proxy mount providing client access.
    """
    probe_client: Client = mount.clients.build_probe_client()
    async with probe_client:
        upstream_tools = await probe_client.list_tools()
    return {tool.name: tool for tool in upstream_tools}


def _is_tool_disabled(tool_name: str, patterns: list[str]) -> bool:
    """Return True when *tool_name* matches any entry in *patterns*.

    Each pattern is tested first as a plain exact-match string, then as a Python
    ``re.fullmatch`` regex. Invalid regex patterns are skipped with a warning.

    Args:
        tool_name: Name of the tool to check.
        patterns: Exact names or regex patterns from ``disabled_tools``.
    """
    for pattern in patterns:
        if tool_name == pattern:
            return True
        try:
            if re.fullmatch(pattern, tool_name):
                return True
        except re.error:
            logger.warning(
                "Invalid regex in disabled_tools; pattern skipped",
                extra={"pattern": pattern},
            )
    return False


def _build_disabled_matcher(patterns: list[str]) -> Callable[[str], bool]:
    """Compile *patterns* once and return a matcher callable.

    Invalid regex entries are logged as warnings exactly once (not once per
    tool name), and are still checked as plain exact-match strings.

    Args:
        patterns: Exact names or regex patterns from ``disabled_tools``.

    Returns:
        A callable ``(tool_name: str) -> bool`` that returns ``True`` when the
        tool should be suppressed.
    """
    exact: set[str] = set(patterns)
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            logger.warning(
                "Invalid regex in disabled_tools; pattern skipped",
                extra={"pattern": pattern},
            )

    def _match(tool_name: str) -> bool:
        if tool_name in exact:
            return True
        return any(r.fullmatch(tool_name) for r in compiled)

    return _match


def _build_upload_consumer_handler(
    *,
    store: SessionStore,
    mount: ProxyMount,
    config: AdapterConfig,
    adapter: UploadConsumerAdapterConfig,
    tool_name: str,
    upload_endpoint_tool_name: str,
    telemetry=None,
) -> ToolHandler:
    """Build the handler closure for an upload_consumer adapter tool.

    Args:
        store: Session store for file resolution.
        mount: Proxy mount for the target server.
        config: Full adapter configuration.
        adapter: Upload consumer adapter configuration.
        tool_name: Name of the tool being overridden.
        upload_endpoint_tool_name: Server-specific upload tool name.
        telemetry: Optional telemetry manager.
    """
    timeout_seconds = resolve_tool_timeout_seconds(
        core_defaults=config.core.defaults,
        server_defaults=mount.server.tool_defaults,
        adapter_overrides=adapter.overrides,
    )

    async def handler(arguments: dict[str, Any], context: Context) -> ToolResult:
        """Rewrite upload handles and forward the tool call upstream.

        Args:
            arguments: Tool call arguments dict.
            context: MCP context for the tool invocation.
        """
        return await handle_upload_consumer_tool(
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            server_id=mount.server.id,
            file_path_argument=adapter.file_path_argument,
            uri_scheme=adapter.uri_scheme,
            uri_prefix=adapter.uri_prefix,
            telemetry=telemetry,
            store=store,
            client_factory=mount.clients.get_session_client,
            tool_call_timeout_seconds=timeout_seconds,
            upload_endpoint_tool_name=upload_endpoint_tool_name,
        )

    return handler


def _build_artifact_producer_handler(
    *,
    store: SessionStore,
    mount: ProxyMount,
    config: AdapterConfig,
    adapter: ArtifactProducerAdapterConfig,
    tool_name: str,
    artifact_download_credentials: ArtifactDownloadCredentialManager | None = None,
    telemetry=None,
) -> ToolHandler:
    """Build the handler closure for an artifact_producer adapter tool.

    Args:
        store: Session store for artifact writes.
        mount: Proxy mount for the target server.
        config: Full adapter configuration.
        adapter: Artifact producer adapter configuration.
        tool_name: Name of the tool being overridden.
        artifact_download_credentials: Optional credential manager for download URLs.
        telemetry: Optional telemetry manager.
    """
    timeout_seconds = resolve_tool_timeout_seconds(
        core_defaults=config.core.defaults,
        server_defaults=mount.server.tool_defaults,
        adapter_overrides=adapter.overrides,
    )
    allow_raw_output = resolve_allow_raw_output(
        core_defaults=config.core.defaults,
        server_defaults=mount.server.tool_defaults,
        adapter_overrides=adapter.overrides,
        adapter_allow_raw_output=adapter.allow_raw_output,
    )
    try:
        write_lock_mode = resolve_write_policy_lock_mode(config)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    def with_download_url(result: ToolResult, context: Context) -> ToolResult:
        """Inject a public download_url into artifact result metadata when enabled.

        Args:
            result: Original tool result.
            context: MCP context for session info.
        """
        if not config.core.allow_artifacts_download or not isinstance(result.meta, dict):
            return result
        artifact_meta = result.meta.get("artifact")
        if not isinstance(artifact_meta, dict):
            return result

        artifact_id = artifact_meta.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            return result

        artifact_filename = artifact_meta.get("filename")
        if not isinstance(artifact_filename, str) or not artifact_filename:
            return result
        download_path = build_artifact_download_path(
            mount.server.id,
            context.session_id,
            artifact_id,
            artifact_filename,
        )
        query_params: dict[str, str] = {"session_id": context.session_id}
        if artifact_download_credentials is not None and artifact_download_credentials.enabled:
            query_params = artifact_download_credentials.issue(
                server_id=mount.server.id,
                session_id=context.session_id,
                artifact_id=artifact_id,
                filename=artifact_filename,
            )
        query_string = urlencode(query_params)
        download_url = f"{derive_public_base_url(config, context)}{download_path}"
        if query_string:
            download_url = f"{download_url}?{query_string}"
        merged_artifact_meta = dict(artifact_meta)
        merged_artifact_meta["download_url"] = download_url

        merged_meta = dict(result.meta)
        merged_meta["artifact"] = merged_artifact_meta
        merged_content = _append_download_link_block(list(result.content), download_url)
        return ToolResult(
            content=merged_content,
            structured_content=result.structured_content,
            meta=merged_meta,
        )

    async def handler(arguments: dict[str, Any], context: Context) -> ToolResult:
        """Invoke the artifact producer, then enrich the result with a download URL.

        Args:
            arguments: Tool arguments dict.
            context: MCP context.
        """
        result = await handle_artifact_producer_tool(
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            server_id=mount.server.id,
            adapter=adapter,
            config_artifact_uri_scheme=config.artifacts.uri_scheme,
            store=store,
            client_factory=mount.clients.get_session_client,
            tool_call_timeout_seconds=timeout_seconds,
            telemetry=telemetry,
            allow_raw_output=allow_raw_output,
            locator_policy=config.storage.artifact_locator_policy,
            locator_allowed_roots=config.storage.artifact_locator_allowed_roots,
            atomic_writes=config.storage.atomic_writes,
            lock_mode=write_lock_mode,
        )
        return with_download_url(result, context)

    return handler


async def wire_adapters(
    *,
    config: AdapterConfig,
    proxy_map: dict[str, ProxyMount],
    store: SessionStore,
    state: AdapterWireState | None = None,
    upload_credentials: UploadCredentialManager | None = None,
    artifact_download_credentials: ArtifactDownloadCredentialManager | None = None,
    telemetry=None,
) -> dict[str, bool]:
    """Register configured adapter overrides on each server proxy.

    Args:
        config: Full adapter configuration.
        proxy_map: Server-id to ``ProxyMount`` mapping.
        store: Session store for adapter operations.
        state: Optional wiring state to enable idempotent retries.
        upload_credentials: Optional upload credential manager.
        artifact_download_credentials: Optional download credential manager.
        telemetry: Optional telemetry manager.

    Returns:
        Dict mapping server id to whether wiring succeeded.
    """
    wire_state = state or AdapterWireState()
    server_status: dict[str, bool] = {}

    for server in config.servers:
        mount = proxy_map[server.id]
        helper_tool_name = get_upload_url_tool_name(server.id)

        upload_consumers = [adapter for adapter in server.adapters if isinstance(adapter, UploadConsumerAdapterConfig)]
        artifact_producers = [adapter for adapter in server.adapters if isinstance(adapter, ArtifactProducerAdapterConfig)]
        upload_helper_enabled = bool(upload_consumers and config.uploads.enabled)
        is_disabled = _build_disabled_matcher(server.disabled_tools)

        helper_disabled = is_disabled(helper_tool_name)
        if helper_disabled and upload_helper_enabled:
            logger.info(
                "Upload helper tool suppressed by disabled_tools",
                extra={"server_id": server.id, "tool_name": helper_tool_name},
            )
        if upload_helper_enabled and not helper_disabled and server.id not in wire_state.local_resources_added:
            register_upload_workflow_resource(
                mount=mount,
                upload_endpoint_tool_name=helper_tool_name,
            )
            wire_state.local_resources_added.add(server.id)
        if upload_helper_enabled and not helper_disabled and server.id not in wire_state.local_tools_added:
            register_get_upload_url_tool(
                mount=mount,
                config=config,
                upload_credentials=upload_credentials,
            )
            wire_state.local_tools_added.add(server.id)
        if server.id not in wire_state.providers_added:
            mount.proxy.add_provider(
                SessionArtifactProvider(
                    store=store,
                    server_id=server.id,
                    uri_scheme=config.artifacts.uri_scheme,
                    enabled=config.artifacts.enabled and config.artifacts.expose_as_resources,
                )
            )
            wire_state.providers_added.add(server.id)

        if not upload_consumers and not artifact_producers and not server.disabled_tools:
            server_status[server.id] = True
            continue

        try:
            upstream_tool_map = await _list_upstream_tools(mount)
        except Exception as exc:
            logger.warning(
                "Failed to list upstream tools while wiring adapters for server '%s' on '%s'",
                server.id,
                server.mount_path,
                extra={"server_id": server.id, "mount_path": server.mount_path, "error": str(exc)},
            )
            server_status[server.id] = False
            continue

        if server.disabled_tools:
            disabled_names = {name for name in upstream_tool_map if is_disabled(name)}
            if disabled_names:
                logger.info(
                    "Applying disabled_tools suppression via Visibility transform",
                    extra={"server_id": server.id, "disabled_names": sorted(disabled_names)},
                )
                mount.proxy.add_transform(Visibility(False, names=disabled_names))

        registered_tools = wire_state.registered_tools_by_server.setdefault(server.id, set())
        for adapter in upload_consumers:
            for tool_name in adapter.tools:
                if tool_name in registered_tools:
                    continue
                if is_disabled(tool_name):
                    logger.info(
                        "Upload consumer tool suppressed by disabled_tools",
                        extra={"server_id": server.id, "tool_name": tool_name},
                    )
                    continue
                upstream_tool = upstream_tool_map.get(tool_name)
                if upstream_tool is None:
                    logger.warning(
                        "Configured upload_consumer tool not found upstream",
                        extra={"server_id": server.id, "tool_name": tool_name},
                    )
                    continue

                handler = _build_upload_consumer_handler(
                    store=store,
                    mount=mount,
                    config=config,
                    adapter=adapter,
                    tool_name=tool_name,
                    upload_endpoint_tool_name=helper_tool_name,
                    telemetry=telemetry,
                )
                mount.proxy.add_tool(
                    _build_upload_consumer_override_tool(
                        upstream_tool=upstream_tool,
                        handler=handler,
                        adapter=adapter,
                        upload_endpoint_tool_name=helper_tool_name,
                    )
                )
                registered_tools.add(tool_name)

        for adapter in artifact_producers:
            for tool_name in adapter.tools:
                if tool_name in registered_tools:
                    continue
                if is_disabled(tool_name):
                    logger.info(
                        "Artifact producer tool suppressed by disabled_tools",
                        extra={"server_id": server.id, "tool_name": tool_name},
                    )
                    continue
                upstream_tool = upstream_tool_map.get(tool_name)
                if upstream_tool is None:
                    logger.warning(
                        "Configured artifact_producer tool not found upstream",
                        extra={"server_id": server.id, "tool_name": tool_name},
                    )
                    continue

                handler = _build_artifact_producer_handler(
                    store=store,
                    mount=mount,
                    config=config,
                    adapter=adapter,
                    tool_name=tool_name,
                    artifact_download_credentials=artifact_download_credentials,
                    telemetry=telemetry,
                )
                mount.proxy.add_tool(OverrideTool.from_mcp_tool(upstream_tool, handler=handler))
                registered_tools.add(tool_name)
        server_status[server.id] = True

    return server_status
