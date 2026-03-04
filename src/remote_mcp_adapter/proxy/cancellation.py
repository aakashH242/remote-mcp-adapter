"""Lightweight MCP cancellation observability for proxy sessions."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)
_NUMERIC_REQUEST_ID_PATTERN = re.compile(r"^-?\d+$")
_CANCEL_NOTIFICATION_METHOD = "notifications/cancelled"
_INITIALIZE_METHOD = "initialize"
_TASK_CANCEL_METHOD = "tasks/cancel"

RequestId = int | str


@dataclass(slots=True, frozen=True)
class ProxySessionContext:
    """Server/session identity for one proxied MCP connection."""

    server_id: str
    session_id: str


@dataclass(slots=True, frozen=True)
class InboundRequest:
    """One inbound JSON-RPC request that may later be cancelled."""

    request_id: RequestId
    method: str
    is_task_augmented: bool


@dataclass(slots=True, frozen=True)
class CancellationNotification:
    """A parsed MCP cancellation notification payload."""

    request_id: RequestId | None
    reason: str | None


@dataclass(slots=True)
class ParsedMcpEnvelope:
    """Relevant MCP request/notification metadata extracted from HTTP payload."""

    requests: list[InboundRequest]
    cancellations: list[CancellationNotification]


@dataclass(slots=True, frozen=True)
class _TrackedRequest:
    """Metadata for one in-flight JSON-RPC request being tracked."""

    method: str
    is_task_augmented: bool


def _normalize_request_id(value: Any) -> RequestId | None:
    """Coerce a raw JSON id into a typed RequestId, rejecting booleans and blank strings.

    Args:
        value: Raw JSON value to normalize.

    Returns:
        Typed ``RequestId``, or None if invalid.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if _NUMERIC_REQUEST_ID_PATTERN.fullmatch(normalized):
            try:
                return int(normalized)
            except ValueError:
                return normalized
        return normalized
    return None


def _normalize_reason(value: Any) -> str | None:
    """Strip and return a cancellation reason, treating non-strings and blank strings as None.

    Args:
        value: Raw reason value.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _is_task_augmented(params: Any) -> bool:
    """Return True when params contain a nested task dict indicating a task-augmented call.

    Args:
        params: JSON-RPC params object.
    """
    if not isinstance(params, dict):
        return False
    return isinstance(params.get("task"), dict)


def _is_jsonrpc_request(message: dict[str, Any]) -> bool:
    """Return True when the message has both an id and a method, making it a JSON-RPC request.

    Args:
        message: Single JSON-RPC message dict.
    """
    return "id" in message and isinstance(message.get("method"), str)


def _iter_messages(payload: Any) -> list[dict[str, Any]]:
    """Flatten a single message or batch into an iterable of dict messages.

    Args:
        payload: Parsed JSON payload (single message or batch array).
    """
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def parse_mcp_envelope(raw_body: bytes) -> ParsedMcpEnvelope:
    """Parse MCP JSON-RPC payload into request/cancellation metadata.

    Args:
        raw_body: Raw HTTP body bytes.

    Returns:
        Parsed envelope with extracted requests and cancellations.
    """
    if not raw_body:
        return ParsedMcpEnvelope(requests=[], cancellations=[])

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ParsedMcpEnvelope(requests=[], cancellations=[])

    requests: list[InboundRequest] = []
    cancellations: list[CancellationNotification] = []
    for message in _iter_messages(payload):
        method = message.get("method")
        if method == _CANCEL_NOTIFICATION_METHOD and "id" not in message:
            params = message.get("params")
            request_id = None
            reason = None
            if isinstance(params, dict):
                request_id = _normalize_request_id(params.get("requestId"))
                reason = _normalize_reason(params.get("reason"))
            cancellations.append(CancellationNotification(request_id=request_id, reason=reason))
            continue

        if not _is_jsonrpc_request(message):
            continue
        request_id = _normalize_request_id(message.get("id"))
        method_name = message.get("method")
        if request_id is None or not isinstance(method_name, str):
            continue

        requests.append(
            InboundRequest(
                request_id=request_id,
                method=method_name,
                is_task_augmented=_is_task_augmented(message.get("params")),
            )
        )

    return ParsedMcpEnvelope(requests=requests, cancellations=cancellations)


class CancellationObserver:
    """Track in-flight requests and log lightweight cancellation policy signals."""

    def __init__(self) -> None:
        """Initialize with an empty in-flight registry and guard lock."""
        self._in_flight: dict[tuple[str, str, RequestId], _TrackedRequest] = {}
        self._lock = asyncio.Lock()

    async def register_requests(
        self,
        context: ProxySessionContext,
        requests: list[InboundRequest],
    ) -> None:
        """Track new in-flight requests so cancellations can be correlated.

        Args:
            context: Server/session identity.
            requests: Inbound requests to track.
        """
        if not requests:
            return
        async with self._lock:
            for request in requests:
                key = (context.server_id, context.session_id, request.request_id)
                self._in_flight[key] = _TrackedRequest(
                    method=request.method,
                    is_task_augmented=request.is_task_augmented,
                )

    async def complete_requests(
        self,
        context: ProxySessionContext,
        requests: list[InboundRequest],
    ) -> None:
        """Remove completed requests from the in-flight tracking map.

        Args:
            context: Server/session identity.
            requests: Completed requests to untrack.
        """
        if not requests:
            return
        async with self._lock:
            for request in requests:
                key = (context.server_id, context.session_id, request.request_id)
                self._in_flight.pop(key, None)

    async def observe_cancellations(
        self,
        context: ProxySessionContext,
        cancellations: list[CancellationNotification],
    ) -> None:
        """Log diagnostics for each cancellation notification against in-flight requests.

        Args:
            context: Server/session identity.
            cancellations: Cancellation notifications to process.
        """
        if not cancellations:
            return

        async with self._lock:
            for cancellation in cancellations:
                request_id = cancellation.request_id
                if request_id is None:
                    logger.warning(
                        "Malformed cancellation notification ignored (missing requestId)",
                        extra={
                            "server_id": context.server_id,
                            "session_id": context.session_id,
                            "reason": cancellation.reason,
                        },
                    )
                    continue

                key = (context.server_id, context.session_id, request_id)
                tracked = self._in_flight.get(key)
                is_initialize = tracked is not None and tracked.method == _INITIALIZE_METHOD
                if is_initialize:
                    logger.warning(
                        "Cancellation notification targets initialize request; initialize must not be cancelled",
                        extra={
                            "server_id": context.server_id,
                            "session_id": context.session_id,
                            "request_id": request_id,
                            "reason": cancellation.reason,
                        },
                    )
                    continue

                if tracked is None and request_id == 0:
                    logger.warning(
                        "Cancellation notification uses request id 0 and may target initialize request",
                        extra={
                            "server_id": context.server_id,
                            "session_id": context.session_id,
                            "request_id": request_id,
                            "reason": cancellation.reason,
                        },
                    )
                    continue

                if tracked is None:
                    logger.debug(
                        "Cancellation notification for unknown or already-completed request ignored",
                        extra={
                            "server_id": context.server_id,
                            "session_id": context.session_id,
                            "request_id": request_id,
                            "reason": cancellation.reason,
                        },
                    )
                    continue

                if tracked.is_task_augmented:
                    logger.warning(
                        "Task-augmented request cancelled via notifications/cancelled; use tasks/cancel",
                        extra={
                            "server_id": context.server_id,
                            "session_id": context.session_id,
                            "request_id": request_id,
                            "reason": cancellation.reason,
                            "required_method": _TASK_CANCEL_METHOD,
                        },
                    )
                    continue

                logger.info(
                    "Observed cancellation notification for in-flight request",
                    extra={
                        "server_id": context.server_id,
                        "session_id": context.session_id,
                        "request_id": request_id,
                        "reason": cancellation.reason,
                    },
                )
