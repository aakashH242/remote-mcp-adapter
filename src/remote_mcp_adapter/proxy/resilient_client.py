"""Resilient upstream MCP client with configurable session-termination retries."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
import time
from typing import Any, TypeVar

import httpx
from mcp import McpError
from pydantic import AnyUrl

from fastmcp.client import Client
from fastmcp.exceptions import ToolError

_SESSION_TERMINATION_MESSAGE_TOKENS = (
    "session terminated",
    "session not found",
    "session gone",
    "unknown session",
    "invalid session",
    "server not initialized",
    "session task completed unexpectedly",
)
_SESSION_TERMINATION_MCP_ERROR_CODE = 32600
_SESSION_TERMINATION_HTTP_STATUS_CODES = {404}
_LIST_TOOLS_CACHE_KEY = "list_tools"
_LIST_RESOURCES_CACHE_KEY = "list_resources"
_LIST_RESOURCE_TEMPLATES_CACHE_KEY = "list_resource_templates"
_LIST_PROMPTS_CACHE_KEY = "list_prompts"
logger = logging.getLogger(__name__)
ResultT = TypeVar("ResultT")


def _message_contains_session_termination_signal(message: str | None) -> bool:
    """Return True when the message contains a known session termination token.

    Args:
        message: Error message string to check.
    """
    if not message:
        return False
    normalized = message.strip().lower()
    return any(token in normalized for token in _SESSION_TERMINATION_MESSAGE_TOKENS)


def _first_content_text(result: Any) -> str | None:
    """Extract the text from the first content block of a tool result, if present.

    Args:
        result: Tool call result object.
    """
    content = getattr(result, "content", None) or []
    if not content:
        return None
    first = content[0]
    text = getattr(first, "text", None)
    return text if isinstance(text, str) else None


def _iter_exception_chain(exc: BaseException):
    """Yield each exception in the chain, guarding against cycles.

    Args:
        exc: Root exception to traverse.
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


class ResilientClient(Client):
    """Client that reconnects and retries on upstream session termination."""

    def __init__(
        self,
        *args: Any,
        default_timeout: float | None = None,
        session_termination_retries: int = 1,
        metadata_cache_ttl_seconds: int = 30,
        **kwargs: Any,
    ):
        """Initialize the resilient client.

        Args:
            *args: Positional arguments forwarded to ``Client``.
            default_timeout: Default tool-call timeout in seconds.
            session_termination_retries: Max reconnect attempts on session loss.
            metadata_cache_ttl_seconds: TTL for cached metadata responses.
            **kwargs: Keyword arguments forwarded to ``Client``.
        """
        super().__init__(*args, **kwargs)
        self._default_timeout = default_timeout
        self._session_termination_retries = max(0, session_termination_retries)
        self._reconnect_lock = asyncio.Lock()
        self._metadata_cache_ttl_seconds = max(0, metadata_cache_ttl_seconds)
        self._metadata_cache: dict[str, tuple[float, Any]] = {}
        self._metadata_cache_lock = asyncio.Lock()
        self._metadata_fetch_locks: dict[str, asyncio.Lock] = {}
        self._metadata_fetch_locks_lock = asyncio.Lock()

    @staticmethod
    def _clone_cached_value(value: Any) -> Any:
        """Shallow-copy lists so callers cannot mutate cached entries.

        Args:
            value: Cached value to clone.
        """
        if isinstance(value, list):
            return list(value)
        return value

    def _cache_expiry_deadline(self) -> float:
        """Return the monotonic timestamp before which cached entries are considered stale."""
        return time.monotonic() - float(self._metadata_cache_ttl_seconds)

    async def _get_metadata_cache(self, cache_key: str) -> Any | None:
        """Return a cached list-metadata value if it exists and is still fresh.

        Args:
            cache_key: Cache slot identifier.
        """
        if self._metadata_cache_ttl_seconds <= 0:
            return None
        async with self._metadata_cache_lock:
            cached = self._metadata_cache.get(cache_key)
            if cached is None:
                return None
            cached_at, value = cached
            if cached_at < self._cache_expiry_deadline():
                self._metadata_cache.pop(cache_key, None)
                return None
            return self._clone_cached_value(value)

    async def _set_metadata_cache(self, cache_key: str, value: Any) -> None:
        """Store a list-metadata value in the cache with the current timestamp.

        Args:
            cache_key: Cache slot identifier.
            value: Value to cache.
        """
        if self._metadata_cache_ttl_seconds <= 0:
            return
        async with self._metadata_cache_lock:
            self._metadata_cache[cache_key] = (time.monotonic(), self._clone_cached_value(value))

    async def _clear_metadata_cache(self) -> None:
        """Evict all cached metadata entries."""
        async with self._metadata_cache_lock:
            self._metadata_cache.clear()

    async def _metadata_fetch_lock(self, cache_key: str) -> asyncio.Lock:
        """Return or create the per-cache-key lock that serializes parallel fetches.

        Args:
            cache_key: Cache slot identifier.
        """
        async with self._metadata_fetch_locks_lock:
            lock = self._metadata_fetch_locks.get(cache_key)
            if lock is None:
                lock = asyncio.Lock()
                self._metadata_fetch_locks[cache_key] = lock
            return lock

    async def _call_with_cached_session_termination_retry(
        self,
        *,
        cache_key: str,
        operation_name: str,
        call_factory: Callable[[], Awaitable[ResultT]],
    ) -> ResultT:
        """Execute a cached list-metadata call with deduplicated fetches and retry on session loss.

        Args:
            cache_key: Cache slot identifier.
            operation_name: Human-readable operation name for logging.
            call_factory: Async callable producing the result.
        """
        cached = await self._get_metadata_cache(cache_key)
        if cached is not None:
            return cached

        fetch_lock = await self._metadata_fetch_lock(cache_key)
        async with fetch_lock:
            cached = await self._get_metadata_cache(cache_key)
            if cached is not None:
                return cached
            result = await self._call_with_session_termination_retry(
                operation_name=operation_name,
                call_factory=call_factory,
            )
            await self._set_metadata_cache(cache_key, result)
            return self._clone_cached_value(result)

    def _upstream_session_id(self) -> str | None:
        """Return the current upstream HTTP session id for log context, if available."""
        getter = getattr(self.transport, "get_session_id", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    async def _reconnect_preserving_nesting(self) -> None:
        """Force-close and re-establish the upstream connection, restoring nesting depth."""
        async with self._reconnect_lock:
            await self._clear_metadata_cache()
            desired_nesting = max(1, self._session_state.nesting_counter)
            logger.info(
                "Reconnecting upstream client session",
                extra={"desired_nesting": desired_nesting, "upstream_session_id": self._upstream_session_id()},
            )
            await self._disconnect(force=True)
            with contextlib.suppress(Exception):
                await self.transport.close()
            for _ in range(desired_nesting):
                await self._connect()
            logger.info(
                "Upstream client session reconnected",
                extra={"desired_nesting": desired_nesting, "upstream_session_id": self._upstream_session_id()},
            )

    @staticmethod
    def _is_session_terminated_result(result: Any) -> bool:
        """Return True when the error result text signals the upstream session was lost.

        Args:
            result: Tool call result object.
        """
        is_error = bool(getattr(result, "isError", False))
        if not is_error:
            return False
        return _message_contains_session_termination_signal(_first_content_text(result))

    @staticmethod
    def _is_session_terminated_exception(exc: BaseException) -> bool:
        """Return True when any exception in the chain indicates session termination.

        Args:
            exc: Exception to inspect.
        """
        for candidate in _iter_exception_chain(exc):
            if isinstance(candidate, httpx.HTTPStatusError):
                status_code = candidate.response.status_code
                if status_code in _SESSION_TERMINATION_HTTP_STATUS_CODES:
                    return True

            if isinstance(candidate, McpError):
                error = getattr(candidate, "error", None)
                if error is not None:
                    code = getattr(error, "code", None)
                    message = getattr(error, "message", None)
                    if code == _SESSION_TERMINATION_MCP_ERROR_CODE and (
                        _message_contains_session_termination_signal(message)
                        or _message_contains_session_termination_signal(str(candidate))
                    ):
                        return True
                    if _message_contains_session_termination_signal(message):
                        return True

            if isinstance(candidate, ToolError) and _message_contains_session_termination_signal(str(candidate)):
                return True

            if _message_contains_session_termination_signal(str(candidate)):
                return True
        return False

    @staticmethod
    def _is_timeout_exception(exc: BaseException) -> bool:
        """Return True for any asyncio, stdlib, or httpx timeout exception.

        Args:
            exc: Exception to check.
        """
        return isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException))

    def _resolve_timeout(self, timeout: Any) -> Any:
        """Return the caller's timeout if set, else fall back to the instance default.

        Args:
            timeout: Caller-supplied timeout, or None.
        """
        if timeout is not None or self._default_timeout is None:
            return timeout
        return self._default_timeout

    async def _call_with_session_termination_retry(
        self,
        *,
        operation_name: str,
        call_factory: Callable[[], Awaitable[ResultT]],
    ) -> ResultT:
        """Retry the operation once after reconnecting on upstream session termination.

        Args:
            operation_name: Human-readable operation name for logging.
            call_factory: Async callable producing the result.
        """
        attempts_used = 0
        while True:
            try:
                return await call_factory()
            except Exception as exc:
                if not self._is_session_terminated_exception(exc) or (attempts_used >= self._session_termination_retries):
                    raise
                attempts_used += 1
                logger.warning(
                    "Upstream session termination detected; reconnecting and retrying operation",
                    extra={
                        "operation_name": operation_name,
                        "attempt": attempts_used,
                        "max_retries": self._session_termination_retries,
                        "upstream_session_id": self._upstream_session_id(),
                    },
                )
                await self._reconnect_preserving_nesting()

    async def list_tools(self):  # type: ignore[override]
        """List tools with TTL caching and session-termination retry."""
        return await self._call_with_cached_session_termination_retry(
            cache_key=_LIST_TOOLS_CACHE_KEY,
            operation_name="list_tools",
            call_factory=super().list_tools,
        )

    async def list_resources(self):  # type: ignore[override]
        """List resources with TTL caching and session-termination retry."""
        return await self._call_with_cached_session_termination_retry(
            cache_key=_LIST_RESOURCES_CACHE_KEY,
            operation_name="list_resources",
            call_factory=super().list_resources,
        )

    async def list_resource_templates(self):  # type: ignore[override]
        """List resource templates with TTL caching and session-termination retry."""
        return await self._call_with_cached_session_termination_retry(
            cache_key=_LIST_RESOURCE_TEMPLATES_CACHE_KEY,
            operation_name="list_resource_templates",
            call_factory=super().list_resource_templates,
        )

    async def list_prompts(self):  # type: ignore[override]
        """List prompts with TTL caching and session-termination retry."""
        return await self._call_with_cached_session_termination_retry(
            cache_key=_LIST_PROMPTS_CACHE_KEY,
            operation_name="list_prompts",
            call_factory=super().list_prompts,
        )

    async def read_resource(self, uri: AnyUrl | str, **kwargs: Any):  # type: ignore[override]
        """Read a resource with session-termination retry.

        Args:
            uri: Resource URI.
            **kwargs: Additional arguments forwarded to the parent.
        """
        parent = super()
        return await self._call_with_session_termination_retry(
            operation_name="read_resource",
            call_factory=lambda: parent.read_resource(uri, **kwargs),
        )

    async def get_prompt(  # type: ignore[override]
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        """Get a prompt with session-termination retry.

        Args:
            name: Prompt name.
            arguments: Optional prompt arguments.
            **kwargs: Additional arguments forwarded to the parent.
        """
        parent = super()
        return await self._call_with_session_termination_retry(
            operation_name="get_prompt",
            call_factory=lambda: parent.get_prompt(name=name, arguments=arguments, **kwargs),
        )

    async def _reconnect_on_timeout(self, *, tool_name: str, exc: BaseException, wrap_tool_error: bool) -> None:
        """Reconnect after a timeout and optionally re-raise as a ToolError.

        Args:
            tool_name: Name of the timed-out tool.
            exc: Original timeout exception.
            wrap_tool_error: If True, re-raise as ``ToolError``.
        """
        logger.warning(
            "Upstream timeout; reconnecting client",
            extra={"tool_name": tool_name, "upstream_session_id": self._upstream_session_id()},
        )
        await self._reconnect_preserving_nesting()
        if wrap_tool_error:
            raise ToolError(f"Upstream tool call timed out and was reconnected: {tool_name}") from exc
        raise exc

    async def call_tool_mcp(  # type: ignore[override]
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler: Any = None,
        timeout: Any = None,
        meta: dict[str, Any] | None = None,
    ):
        """Call a tool at the MCP protocol level with timeout handling and session-termination retry.

        Args:
            name: Tool name.
            arguments: Tool arguments dict.
            progress_handler: Optional progress callback.
            timeout: Optional timeout override.
            meta: Optional metadata dict.
        """
        resolved_timeout = self._resolve_timeout(timeout)
        attempts_used = 0
        while True:
            try:
                result = await super().call_tool_mcp(
                    name=name,
                    arguments=arguments,
                    progress_handler=progress_handler,
                    timeout=resolved_timeout,
                    meta=meta,
                )
            except Exception as exc:
                if self._is_timeout_exception(exc):
                    await self._reconnect_on_timeout(tool_name=name, exc=exc, wrap_tool_error=False)
                if not self._is_session_terminated_exception(exc) or attempts_used >= self._session_termination_retries:
                    raise
                attempts_used += 1
                logger.warning(
                    "Upstream session termination detected during call_tool_mcp; reconnecting and retrying",
                    extra={
                        "tool_name": name,
                        "attempt": attempts_used,
                        "max_retries": self._session_termination_retries,
                        "upstream_session_id": self._upstream_session_id(),
                    },
                )
                await self._reconnect_preserving_nesting()
                continue

            if self._is_session_terminated_result(result) and attempts_used < self._session_termination_retries:
                attempts_used += 1
                logger.warning(
                    "Upstream session termination result from call_tool_mcp; reconnecting and retrying",
                    extra={
                        "tool_name": name,
                        "attempt": attempts_used,
                        "max_retries": self._session_termination_retries,
                        "upstream_session_id": self._upstream_session_id(),
                    },
                )
                await self._reconnect_preserving_nesting()
                continue
            return result

    async def call_tool(  # type: ignore[override]
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: Any = None,
        progress_handler: Any = None,
        raise_on_error: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool = False,
        task_id: str | None = None,
        ttl: int = 60000,
    ):
        """Call a tool with timeout handling and session-termination retry.

        Raises ``ToolError`` on timeout.

        Args:
            name: Tool name.
            arguments: Tool arguments dict.
            timeout: Optional timeout override.
            progress_handler: Optional progress callback.
            raise_on_error: If True, raise on tool errors.
            meta: Optional metadata dict.
            task: If True, use task mode.
            task_id: Optional task identifier.
            ttl: Task TTL in milliseconds.
        """
        resolved_timeout = self._resolve_timeout(timeout)
        attempts_used = 0
        while True:
            try:
                return await super().call_tool(
                    name=name,
                    arguments=arguments,
                    timeout=resolved_timeout,
                    progress_handler=progress_handler,
                    raise_on_error=raise_on_error,
                    meta=meta,
                    task=task,
                    task_id=task_id,
                    ttl=ttl,
                )
            except Exception as exc:
                if self._is_timeout_exception(exc):
                    await self._reconnect_on_timeout(tool_name=name, exc=exc, wrap_tool_error=True)
                if not self._is_session_terminated_exception(exc) or attempts_used >= self._session_termination_retries:
                    raise
                attempts_used += 1
                logger.warning(
                    "Upstream session termination detected during call_tool; reconnecting and retrying",
                    extra={
                        "tool_name": name,
                        "attempt": attempts_used,
                        "max_retries": self._session_termination_retries,
                        "upstream_session_id": self._upstream_session_id(),
                    },
                )
                await self._reconnect_preserving_nesting()
