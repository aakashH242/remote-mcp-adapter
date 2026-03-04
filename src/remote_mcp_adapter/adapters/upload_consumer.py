"""Upload consumer adapter implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context
from fastmcp.tools.tool import ToolResult

from ..core.storage.store import SessionStore
from .upstream_call import ClientFactory, call_upstream_tool


def _upload_flow_hint(
    *,
    tool_name: str,
    file_path_argument: str,
    uri_scheme: str,
    upload_endpoint_tool_name: str,
) -> str:
    """Build a concise upload-flow hint for inclusion in error messages.

    Args:
        tool_name: Name of the tool expecting upload handles.
        file_path_argument: Argument name that carries the upload handle.
        uri_scheme: Expected URI scheme prefix (e.g. ``"upload://"``).
        upload_endpoint_tool_name: Name of the upload endpoint tool.

    Returns:
        Human-readable hint string describing the upload workflow.
    """
    return (
        f"Call `{upload_endpoint_tool_name}`, POST multipart upload(s), then pass returned "
        f"`{uri_scheme}` handles to `{tool_name}` via `{file_path_argument}`."
    )


def _upload_input_error(
    reason: str,
    *,
    tool_name: str,
    file_path_argument: str,
    uri_scheme: str,
    upload_endpoint_tool_name: str,
) -> ToolError:
    """Construct a ToolError with the reason and upload-flow hint for invalid inputs.

    Args:
        reason: Human-readable explanation of the validation failure.
        tool_name: Name of the tool that received invalid input.
        file_path_argument: Argument name that carries the upload handle.
        uri_scheme: Expected URI scheme prefix.
        upload_endpoint_tool_name: Name of the upload endpoint tool.

    Returns:
        ``ToolError`` combining *reason* with a corrective upload-flow hint.
    """
    hint = _upload_flow_hint(
        tool_name=tool_name,
        file_path_argument=file_path_argument,
        uri_scheme=uri_scheme,
        upload_endpoint_tool_name=upload_endpoint_tool_name,
    )
    return ToolError(f"{reason} {hint}")


def _get_nested_arg(arguments: dict[str, Any], dotted_path: str) -> Any:
    """Traverse a dotted path into arguments, raising KeyError on any missing segment.

    Args:
        arguments: Top-level arguments dict.
        dotted_path: Dot-separated key path (e.g. ``"options.file"``).

    Returns:
        The value at the leaf of the dotted path.

    Raises:
        KeyError: If any intermediate or leaf key is missing.
    """
    current: Any = arguments
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"Missing required argument path: {dotted_path}")
        current = current[part]
    return current


def _set_nested_arg(arguments: dict[str, Any], dotted_path: str, value: Any) -> None:
    """Set the leaf value at a dotted path in arguments, raising KeyError on missing segments.

    Args:
        arguments: Top-level arguments dict to mutate.
        dotted_path: Dot-separated key path (e.g. ``"options.file"``).
        value: Value to set at the leaf position.

    Raises:
        KeyError: If any intermediate key is missing.
    """
    parts = dotted_path.split(".")
    current: dict[str, Any] = arguments
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            raise KeyError(f"Missing required argument path: {dotted_path}")
        current = current[part]
    current[parts[-1]] = value


async def _resolve_handle(
    *,
    store: SessionStore,
    server_id: str,
    session_id: str,
    raw_value: str,
    uri_scheme: str,
    tool_name: str,
    file_path_argument: str,
    upload_endpoint_tool_name: str,
) -> str:
    """Validate an upload:// handle and resolve it to the upload record's absolute filesystem path.

    Args:
        store: Session store for upload record lookup.
        server_id: Identifier of the upstream server.
        session_id: Current MCP session identifier.
        raw_value: Raw handle string provided by the caller.
        uri_scheme: Expected URI scheme prefix (e.g. ``"upload://"``).
        tool_name: Name of the tool consuming the upload.
        file_path_argument: Argument name carrying the handle.
        upload_endpoint_tool_name: Name of the upload endpoint tool.

    Returns:
        Absolute filesystem path to the uploaded file.

    Raises:
        ToolError: If the handle does not start with the expected scheme
            or cannot be resolved to a valid upload record.
    """
    if not raw_value.startswith(uri_scheme):
        raise _upload_input_error(
            f"Expected {uri_scheme} handle, received: {raw_value}",
            tool_name=tool_name,
            file_path_argument=file_path_argument,
            uri_scheme=uri_scheme,
            upload_endpoint_tool_name=upload_endpoint_tool_name,
        )
    try:
        record = await store.resolve_upload_handle(
            server_id=server_id,
            session_id=session_id,
            handle=raw_value,
            uri_scheme=uri_scheme,
        )
    except (ValueError, KeyError) as exc:
        raise _upload_input_error(
            f"Invalid upload handle: {raw_value}.",
            tool_name=tool_name,
            file_path_argument=file_path_argument,
            uri_scheme=uri_scheme,
            upload_endpoint_tool_name=upload_endpoint_tool_name,
        ) from exc
    return str(record.abs_path)


async def handle_upload_consumer_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    context: Context | None,
    server_id: str,
    file_path_argument: str,
    uri_scheme: str,
    uri_prefix: bool | None,
    telemetry=None,
    store: SessionStore,
    client_factory: ClientFactory,
    tool_call_timeout_seconds: int | None,
    upload_endpoint_tool_name: str,
) -> ToolResult:
    """Rewrite ``upload://`` args to absolute paths and forward call upstream.

    Resolves single-string or list-of-strings upload handles to their
    on-disk paths, optionally prepends a ``file://`` URI prefix, then
    delegates the rewritten arguments to the upstream tool.

    Args:
        tool_name: Name of the upstream tool to invoke.
        arguments: Original arguments dict containing upload handles.
        context: Optional FastMCP context (falls back to ``get_context()``).
        server_id: Identifier of the upstream server.
        file_path_argument: Dotted argument path carrying the upload handle(s).
        uri_scheme: Upload URI scheme prefix.
        uri_prefix: When truthy, convert resolved paths to ``file://`` URIs.
        telemetry: Optional telemetry recorder.
        store: Session store for upload record lookup.
        client_factory: Factory callable returning an upstream MCP client.
        tool_call_timeout_seconds: Optional timeout for the upstream call.
        upload_endpoint_tool_name: Name of the upload endpoint tool.

    Returns:
        ``ToolResult`` from the upstream tool call.

    Raises:
        ToolError: If handle resolution or argument rewriting fails.
    """
    ctx = context or get_context()
    session_id = ctx.session_id
    payload = dict(arguments or {})
    try:
        current_value = _get_nested_arg(payload, file_path_argument)
    except KeyError as exc:
        raise _upload_input_error(
            str(exc),
            tool_name=tool_name,
            file_path_argument=file_path_argument,
            uri_scheme=uri_scheme,
            upload_endpoint_tool_name=upload_endpoint_tool_name,
        ) from exc

    def apply_uri_prefix(value: str) -> str:
        """Prepend a ``file://`` URI prefix to *value* when *uri_prefix* is enabled.

        Args:
            value: Resolved absolute filesystem path string.

        Returns:
            Original or ``file://``-prefixed path.
        """
        if not uri_prefix:
            return value
        return Path(value).resolve().as_uri()

    if isinstance(current_value, str):
        rewritten = await _resolve_handle(
            store=store,
            server_id=server_id,
            session_id=session_id,
            raw_value=current_value,
            uri_scheme=uri_scheme,
            tool_name=tool_name,
            file_path_argument=file_path_argument,
            upload_endpoint_tool_name=upload_endpoint_tool_name,
        )
        rewritten = apply_uri_prefix(rewritten)
    elif isinstance(current_value, list):
        rewritten = []
        for item in current_value:
            if not isinstance(item, str):
                raise _upload_input_error(
                    f"{file_path_argument} list values must be strings.",
                    tool_name=tool_name,
                    file_path_argument=file_path_argument,
                    uri_scheme=uri_scheme,
                    upload_endpoint_tool_name=upload_endpoint_tool_name,
                )
            resolved_value = await _resolve_handle(
                store=store,
                server_id=server_id,
                session_id=session_id,
                raw_value=item,
                uri_scheme=uri_scheme,
                tool_name=tool_name,
                file_path_argument=file_path_argument,
                upload_endpoint_tool_name=upload_endpoint_tool_name,
            )
            rewritten.append(apply_uri_prefix(resolved_value))
    else:
        raise _upload_input_error(
            f"{file_path_argument} must be a string or list of strings.",
            tool_name=tool_name,
            file_path_argument=file_path_argument,
            uri_scheme=uri_scheme,
            upload_endpoint_tool_name=upload_endpoint_tool_name,
        )

    try:
        _set_nested_arg(payload, file_path_argument, rewritten)
    except KeyError as exc:
        raise _upload_input_error(
            str(exc),
            tool_name=tool_name,
            file_path_argument=file_path_argument,
            uri_scheme=uri_scheme,
            upload_endpoint_tool_name=upload_endpoint_tool_name,
        ) from exc

    return await call_upstream_tool(
        client_factory=client_factory,
        tool_name=tool_name,
        arguments=payload,
        timeout_seconds=tool_call_timeout_seconds,
        telemetry=telemetry,
        server_id=server_id,
    )
