from __future__ import annotations

from types import SimpleNamespace

from remote_mcp_adapter.telemetry import event_dispatch as ed


class _Metric:
    def __init__(self):
        self.calls = []

    def add(self, value, attrs):
        self.calls.append(("add", value, attrs))

    def record(self, value, attrs):
        self.calls.append(("record", value, attrs))


class _Manager:
    def __init__(self):
        self._http_requests_total = _Metric()
        self._http_request_duration = _Metric()
        self._upload_batches_total = _Metric()
        self._upload_files_total = _Metric()
        self._upload_bytes_total = _Metric()
        self._auth_rejections_total = _Metric()
        self._upstream_tool_calls_total = _Metric()
        self._upstream_tool_call_duration = _Metric()
        self._upstream_ping_total = _Metric()
        self._upstream_ping_latency = _Metric()
        self._circuit_breaker_state = _Metric()
        self._persistence_policy_transitions_total = _Metric()
        self._nonce_operations_total = _Metric()
        self._upload_credentials_total = _Metric()
        self._artifact_downloads_total = _Metric()
        self._artifact_download_duration = _Metric()
        self._artifact_download_bytes_total = _Metric()
        self._upload_failures_total = _Metric()
        self._request_rejections_total = _Metric()
        self._adapter_wiring_runs_total = _Metric()
        self._adapter_wiring_not_ready_servers = _Metric()
        self._cleanup_cycles_total = _Metric()
        self._cleanup_removed_records_total = _Metric()
        self._sessions_lifecycle_total = _Metric()


def test_primitives():
    assert ed._server_id({}) == "global"
    assert ed._server_id({"server_id": "s1"}) == "s1"
    assert ed._status_class(200) == "2xx"
    assert ed._breaker_numeric_state("closed") == 0
    assert ed._breaker_numeric_state("half_open") == 1
    assert ed._breaker_numeric_state("open") == 2


def test_handle_event_all_handlers():
    manager = _Manager()
    events = [
        ("http_request", {"method": "GET", "route_group": "/g", "status_code": 200, "duration_seconds": 0.1}),
        ("upload_batch", {"server_id": "s1", "file_count": 2, "bytes_total": 10}),
        ("auth_rejection", {"route_group": "/g", "reason": "bad"}),
        ("upstream_tool_call", {"server_id": "s1", "tool_name": "t", "result": "ok", "duration_seconds": 0.2}),
        ("upstream_ping", {"server_id": "s1", "result": "ok", "state_before_probe": "closed", "latency_ms": 5}),
        ("breaker_state", {"server_id": "s1", "state": "half_open"}),
        ("persistence_policy", {"action": "switch", "source": "x", "policy": "fail_open", "configured_backend": "sqlite"}),
        ("nonce_operation", {"operation": "consume", "result": "ok", "backend": "mem"}),
        ("upload_credential", {"operation": "validate", "result": "ok", "backend": "mem"}),
        ("artifact_download", {"result": "success", "auth_mode": "session_context", "duration_seconds": 0.2, "size_bytes": 11}),
        ("artifact_download", {"result": "success", "auth_mode": "session_context", "duration_seconds": 0.1, "size_bytes": 0}),
        ("upload_failure", {"reason": "bad"}),
        ("request_rejection", {"route_group": "/g", "reason": "x", "status_code": 503}),
        ("adapter_wiring", {"result": "ok", "not_ready_servers": 1}),
        ("cleanup_cycle", {"status": "ok", "result": {"uploads": 2, "artifacts": 0}}),
        ("session_lifecycle", {"event": "create", "server_id": "s1"}),
    ]

    for kind, payload in events:
        ed.handle_event(manager=manager, event=SimpleNamespace(kind=kind, payload=payload))

    ed.handle_event(manager=manager, event=SimpleNamespace(kind="unknown", payload={}))

    assert manager._http_requests_total.calls
    assert manager._upload_batches_total.calls
    assert manager._auth_rejections_total.calls
    assert manager._upstream_tool_calls_total.calls
    assert manager._upstream_ping_total.calls
    assert manager._circuit_breaker_state.calls
    assert manager._persistence_policy_transitions_total.calls
    assert manager._nonce_operations_total.calls
    assert manager._upload_credentials_total.calls
    assert manager._artifact_downloads_total.calls
    assert manager._upload_failures_total.calls
    assert manager._request_rejections_total.calls
    assert manager._adapter_wiring_runs_total.calls
    assert manager._cleanup_cycles_total.calls
    assert manager._sessions_lifecycle_total.calls

    assert any(call[1] > 0 for call in manager._artifact_download_bytes_total.calls if call[0] == "add")
