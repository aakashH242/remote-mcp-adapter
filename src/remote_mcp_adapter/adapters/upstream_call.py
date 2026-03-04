"""Shared upstream tool call helper for adapter handlers."""

from __future__ import annotations

import inspect
import time
from typing import Any, Callable

from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolResult

ClientFactory = Callable[[], Any]


async def call_upstream_tool(
    *,
    client_factory: ClientFactory,
    tool_name: str,
    arguments: dict[str, Any],
    timeout_seconds: int | None,
    telemetry=None,
    server_id: str | None = None,
) -> ToolResult:
    """Call an upstream tool with optional per-call timeout control.

    Opens a client session using *client_factory*, invokes the named tool,
    records telemetry on success/failure/timeout, and wraps the raw response
    in a ``ToolResult``.

    Args:
        client_factory: Factory callable returning an upstream MCP client.
        tool_name: Name of the upstream tool to invoke.
        arguments: Arguments dict forwarded to the tool.
        timeout_seconds: Optional per-call timeout in seconds.
        telemetry: Optional telemetry recorder.
        server_id: Server identifier used for telemetry tagging.

    Returns:
        ``ToolResult`` containing the upstream tool's content, structured
        content, and metadata.

    Raises:
        ToolError: If the upstream call times out.
    """
    started = time.perf_counter()
    client = client_factory()
    if inspect.isawaitable(client):
        client = await client
    async with client:
        try:
            result = await client.call_tool(
                tool_name,
                arguments,
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            if telemetry is not None and getattr(telemetry, "enabled", False) and server_id:
                await telemetry.record_upstream_tool_call(
                    server_id=server_id,
                    tool_name=tool_name,
                    result="timeout",
                    duration_seconds=(time.perf_counter() - started),
                )
            raise ToolError(f"Tool call timed out after {timeout_seconds}s: {tool_name}") from exc
        except Exception:
            if telemetry is not None and getattr(telemetry, "enabled", False) and server_id:
                await telemetry.record_upstream_tool_call(
                    server_id=server_id,
                    tool_name=tool_name,
                    result="error",
                    duration_seconds=(time.perf_counter() - started),
                )
            raise

    if telemetry is not None and getattr(telemetry, "enabled", False) and server_id:
        await telemetry.record_upstream_tool_call(
            server_id=server_id,
            tool_name=tool_name,
            result="success",
            duration_seconds=(time.perf_counter() - started),
        )

    return ToolResult(
        content=result.content,
        structured_content=result.structured_content,
        meta=result.meta,
    )
