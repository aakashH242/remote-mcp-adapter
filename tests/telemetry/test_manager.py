from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from remote_mcp_adapter.telemetry import manager as tm


class _Provider:
    def __init__(self):
        self.shutdown_called = False
        self.force_called = False

    def shutdown(self):
        self.shutdown_called = True

    def force_flush(self, **kwargs):
        self.force_called = True


def _config(**overrides):
    defaults = dict(
        enabled=True,
        max_queue_size=100,
        emit_logs=False,
        flush_on_terminate=False,
        flush_on_shutdown=True,
        shutdown_drain_timeout_seconds=1,
        export_timeout_seconds=1,
        drop_on_queue_full=True,
        periodic_flush_seconds=0.01,
        queue_batch_size=10,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_start_shutdown_and_enqueue_paths(monkeypatch):
    telemetry = tm.AdapterTelemetry(config=_config())

    monkeypatch.setattr(tm, "initialize_metrics_backend", lambda config: (object(), _Provider(), object(), object()))
    monkeypatch.setattr(tm, "create_metric_instruments", lambda meter: {})

    await telemetry.start()
    assert telemetry.enabled is True

    await telemetry.record_http_request(method="get", route_group="/g", status_code=200, duration_seconds=-1)
    await telemetry.record_upload_batch(server_id="s1", file_count=-2, bytes_total=-4)
    await telemetry.record_auth_rejection(reason="bad", route_group="/g")
    await telemetry.record_upstream_tool_call(server_id="s1", tool_name="t", result="ok", duration_seconds=0.1)
    await telemetry.record_upstream_ping(server_id="s1", result="ok", latency_ms=-1, state_before_probe="closed")
    await telemetry.record_persistence_policy_transition(action="a", source="s", policy="p", configured_backend="disk")
    telemetry.record_persistence_policy_transition_nowait(action="a", source="s", policy="p", configured_backend="disk")
    await telemetry.record_nonce_operation(operation="consume", result="ok", backend="mem")
    await telemetry.record_upload_credential_event(operation="validate", result="ok", backend="mem")
    await telemetry.record_artifact_download(server_id="s1", result="ok", auth_mode="session", duration_seconds=-1, size_bytes=-3)
    await telemetry.record_upload_failure(server_id="s1", reason="bad")
    await telemetry.record_request_rejection(server_id="s1", route_group="/g", reason="x", status_code=503)
    await telemetry.record_adapter_wiring_run(result="ok", total_servers=-1, not_ready_servers=-2)
    await telemetry.record_cleanup_cycle(result={"a": 2}, status="ok")
    await telemetry.record_session_lifecycle(event="created", server_id="s1")
    await telemetry.set_circuit_breaker_state(server_id="s1", state="open")

    await telemetry.shutdown()


@pytest.mark.asyncio
async def test_start_failure_and_emit_logs_and_worker_helpers(monkeypatch):
    telemetry = tm.AdapterTelemetry(config=_config(emit_logs=True, flush_on_terminate=True, drop_on_queue_full=False))

    def fail_backend(config):
        raise RuntimeError("otel")

    monkeypatch.setattr(tm, "initialize_metrics_backend", fail_backend)
    await telemetry.start()
    assert telemetry.enabled is False

    telemetry2 = tm.AdapterTelemetry(config=_config(emit_logs=True, flush_on_terminate=True))
    monkeypatch.setattr(tm, "initialize_metrics_backend", lambda config: (object(), _Provider(), object(), object()))
    monkeypatch.setattr(tm, "create_metric_instruments", lambda meter: {})
    monkeypatch.setattr(tm, "setup_log_export", lambda **kwargs: (_Provider(), object()))
    monkeypatch.setattr(tm.atexit, "register", lambda fn: None)
    await telemetry2.start()

    await telemetry2._enqueue("k", {"x": 1})
    telemetry2._enqueue_nowait("k2", {"x": 2})

    drained = await telemetry2._drain_event_batch()
    assert drained

    stop = telemetry2._process_drained_events([tm.TelemetryEvent(kind="shutdown", payload={})])
    assert stop is True

    telemetry2._meter_provider = _Provider()
    telemetry2._logger_provider = _Provider()
    telemetry2._force_flush_providers(timeout_seconds=1)
    assert telemetry2._meter_provider.force_called is True

    telemetry2._queue.put_nowait(tm.TelemetryEvent(kind="k", payload={}))
    telemetry2._queue.put_nowait(tm.TelemetryEvent(kind="shutdown", payload={}))
    telemetry2._on_process_terminate()

    await telemetry2.shutdown()


@pytest.mark.asyncio
async def test_worker_loop_and_handle_event(monkeypatch):
    telemetry = tm.AdapterTelemetry(config=_config(periodic_flush_seconds=0.01))
    telemetry._enabled = True

    processed = []

    def fake_handle_event(manager, event):
        processed.append(event.kind)

    monkeypatch.setattr(tm, "handle_event", fake_handle_event)

    async def run_worker():
        task = asyncio.create_task(telemetry._worker_loop())
        await telemetry._queue.put(tm.TelemetryEvent(kind="a", payload={}))
        await telemetry._queue.put(tm.TelemetryEvent(kind="shutdown", payload={}))
        await task

    await run_worker()
    assert "a" in processed
