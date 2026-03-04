"""Telemetry event dispatch logic for instrument writes."""

from __future__ import annotations

from typing import Any, Callable

from ..constants import GLOBAL_SERVER_ID

EventPayload = dict[str, Any]
EventHandler = Callable[[Any, EventPayload], None]


def _server_id(payload: EventPayload) -> str:
    """Resolve a payload server identifier with global fallback.

    Args:
        payload: Telemetry event payload.

    Returns:
        Server identifier in payload, or ``global`` when absent.
    """
    return str(payload.get("server_id", GLOBAL_SERVER_ID))


def _status_class(status_code: int) -> str:
    """Build HTTP status-class label.

    Args:
        status_code: HTTP status code.

    Returns:
        Status class label such as ``2xx`` or ``5xx``.
    """
    return f"{status_code // 100}xx"


def _breaker_numeric_state(state_name: str) -> int:
    """Map breaker state string to numeric gauge value.

    Args:
        state_name: Breaker state label.

    Returns:
        Numeric state where closed=0, half_open=1, open=2.
    """
    if state_name == "half_open":
        return 1
    if state_name == "open":
        return 2
    return 0


def _handle_http_request(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one HTTP request event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    status_code = int(payload["status_code"])
    attrs = {
        "server_id": _server_id(payload),
        "http.request.method": payload["method"],
        "adapter.route_group": payload["route_group"],
        "http.response.status_code": status_code,
        "http.response.status_class": _status_class(status_code),
    }
    manager._http_requests_total.add(1, attrs)
    manager._http_request_duration.record(float(payload["duration_seconds"]), attrs)


def _handle_upload_batch(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one upload batch event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {"server_id": payload["server_id"]}
    manager._upload_batches_total.add(1, attrs)
    manager._upload_files_total.add(int(payload["file_count"]), attrs)
    manager._upload_bytes_total.add(int(payload["bytes_total"]), attrs)


def _handle_auth_rejection(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one auth rejection event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {
        "server_id": _server_id(payload),
        "reason": payload["reason"],
        "adapter.route_group": payload["route_group"],
    }
    manager._auth_rejections_total.add(1, attrs)


def _handle_upstream_tool_call(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one upstream tool-call event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {
        "server_id": payload["server_id"],
        "tool_name": payload["tool_name"],
        "result": payload["result"],
    }
    manager._upstream_tool_calls_total.add(1, attrs)
    manager._upstream_tool_call_duration.record(float(payload["duration_seconds"]), attrs)


def _handle_upstream_ping(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one upstream ping event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {
        "server_id": payload["server_id"],
        "result": payload["result"],
        "state_before_probe": payload["state_before_probe"],
    }
    manager._upstream_ping_total.add(1, attrs)
    manager._upstream_ping_latency.record(float(payload["latency_ms"]) / 1000.0, attrs)


def _handle_breaker_state(manager: Any, payload: EventPayload) -> None:
    """Write gauge for one breaker-state event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {"server_id": payload["server_id"]}
    manager._circuit_breaker_state.record(_breaker_numeric_state(str(payload["state"])), attrs)


def _handle_persistence_policy(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one persistence-policy event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {
        "server_id": _server_id(payload),
        "action": payload["action"],
        "source": payload["source"],
        "policy": payload["policy"],
        "configured_backend": payload["configured_backend"],
    }
    manager._persistence_policy_transitions_total.add(1, attrs)


def _handle_nonce_operation(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one nonce operation event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {
        "server_id": _server_id(payload),
        "operation": payload["operation"],
        "result": payload["result"],
        "backend": payload["backend"],
    }
    manager._nonce_operations_total.add(1, attrs)


def _handle_upload_credential(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one upload credential event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {
        "server_id": _server_id(payload),
        "operation": payload["operation"],
        "result": payload["result"],
        "backend": payload["backend"],
    }
    manager._upload_credentials_total.add(1, attrs)


def _handle_artifact_download(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one artifact download event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {
        "server_id": _server_id(payload),
        "result": payload["result"],
        "auth_mode": payload["auth_mode"],
    }
    manager._artifact_downloads_total.add(1, attrs)
    manager._artifact_download_duration.record(float(payload["duration_seconds"]), attrs)
    served_bytes = int(payload["size_bytes"])
    if served_bytes > 0:
        manager._artifact_download_bytes_total.add(served_bytes, attrs)


def _handle_upload_failure(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one upload failure event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {
        "server_id": _server_id(payload),
        "reason": payload["reason"],
    }
    manager._upload_failures_total.add(1, attrs)


def _handle_request_rejection(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one non-auth request rejection event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    status_code = int(payload["status_code"])
    attrs = {
        "server_id": _server_id(payload),
        "adapter.route_group": payload["route_group"],
        "reason": payload["reason"],
        "http.response.status_code": status_code,
        "http.response.status_class": _status_class(status_code),
    }
    manager._request_rejections_total.add(1, attrs)


def _handle_adapter_wiring(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one adapter wiring event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    resolved_server_id = _server_id(payload)
    manager._adapter_wiring_runs_total.add(
        1,
        {
            "server_id": resolved_server_id,
            "result": payload["result"],
        },
    )
    manager._adapter_wiring_not_ready_servers.record(
        int(payload["not_ready_servers"]),
        {"server_id": resolved_server_id},
    )


def _handle_cleanup_cycle(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one cleanup cycle event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    server_id = _server_id(payload)
    manager._cleanup_cycles_total.add(
        1,
        {"server_id": server_id, "status": payload["status"]},
    )
    for bucket, removed_count in payload["result"].items():
        count = int(removed_count)
        if count <= 0:
            continue
        manager._cleanup_removed_records_total.add(
            count,
            {"server_id": server_id, "bucket": bucket},
        )


def _handle_session_lifecycle(manager: Any, payload: EventPayload) -> None:
    """Write metrics for one session lifecycle event.

    Args:
        manager: ``AdapterTelemetry`` instance.
        payload: Event payload values.
    """
    attrs = {
        "event": payload["event"],
        "server_id": payload["server_id"],
    }
    manager._sessions_lifecycle_total.add(1, attrs)


_EVENT_HANDLERS: dict[str, EventHandler] = {
    "http_request": _handle_http_request,
    "upload_batch": _handle_upload_batch,
    "auth_rejection": _handle_auth_rejection,
    "upstream_tool_call": _handle_upstream_tool_call,
    "upstream_ping": _handle_upstream_ping,
    "breaker_state": _handle_breaker_state,
    "persistence_policy": _handle_persistence_policy,
    "nonce_operation": _handle_nonce_operation,
    "upload_credential": _handle_upload_credential,
    "artifact_download": _handle_artifact_download,
    "upload_failure": _handle_upload_failure,
    "request_rejection": _handle_request_rejection,
    "adapter_wiring": _handle_adapter_wiring,
    "cleanup_cycle": _handle_cleanup_cycle,
    "session_lifecycle": _handle_session_lifecycle,
}


def handle_event(*, manager: Any, event: Any) -> None:
    """Dispatch one telemetry event into instrument writes.

    Args:
        manager: ``AdapterTelemetry`` instance owning OTel instruments.
        event: ``TelemetryEvent`` with ``kind`` and ``payload`` fields.
    """
    handler = _EVENT_HANDLERS.get(event.kind)
    if handler is None:
        return
    handler(manager, event.payload)
