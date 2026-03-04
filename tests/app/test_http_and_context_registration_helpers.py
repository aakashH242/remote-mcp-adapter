from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi import status as http_status

from remote_mcp_adapter.app import health_policy_helpers as hph
from remote_mcp_adapter.app import http as app_http
from remote_mcp_adapter.app import http_contexts as ctx
from remote_mcp_adapter.app import http_registration as hr
from remote_mcp_adapter.app import persistence_http_helpers as phh
from remote_mcp_adapter.app import runtime_upstream_helpers as ruh


def test_http_module_exports():
    assert set(app_http.__all__) == {"register_middlewares", "register_routes"}


def test_context_dataclasses_init():
    app = FastAPI()
    mctx = ctx.MiddlewareRegistrationContext(
        app=app,
        resolved_config=object(),
        persistence_policy=object(),
        runtime_ref={"current": object()},
        session_store=object(),
        upstream_health={},
        mount_path_to_server_id={},
        cancellation_observer=object(),
        upload_path_prefix="/upload",
        upload_credentials=None,
        artifact_download_credentials=None,
        telemetry=None,
        build_memory_persistence_runtime=object(),
    )
    rctx = ctx.RouteRegistrationContext(
        app=app,
        resolved_config=object(),
        proxy_map={},
        upstream_health={},
        persistence_policy=object(),
        runtime_ref={"current": object()},
        session_store=object(),
        upload_route="/upload/{server_id}",
        telemetry=None,
        build_memory_persistence_runtime=object(),
        save_upload_stream=object(),
    )
    assert mctx.upload_path_prefix == "/upload"
    assert rctx.upload_route == "/upload/{server_id}"


def test_persistence_http_helpers_response_and_exception():
    response = phh.persistence_backend_operation_failed_response()
    assert response.status_code == http_status.HTTP_503_SERVICE_UNAVAILABLE

    with pytest.raises(Exception) as exc_info:
        phh.raise_persistence_backend_operation_failed_http_exception(cause=RuntimeError("x"))
    assert "Persistence backend operation failed" in str(exc_info.value)


def test_register_middlewares_and_routes_delegation(monkeypatch):
    app = FastAPI()
    captured_m = {}
    captured_r = {}

    monkeypatch.setattr(hr, "register_middleware_stack", lambda *, context: captured_m.setdefault("context", context))
    monkeypatch.setattr(hr, "register_route_stack", lambda *, context: captured_r.setdefault("context", context))

    hr.register_middlewares(
        app=app,
        resolved_config=object(),
        persistence_policy=object(),
        runtime_ref={"current": object()},
        session_store=object(),
        upstream_health={},
        mount_path_to_server_id={},
        cancellation_observer=object(),
        upload_path_prefix="/upload",
        upload_credentials=None,
        artifact_download_credentials=None,
        telemetry=None,
        build_memory_persistence_runtime=object(),
    )
    hr.register_routes(
        app=app,
        resolved_config=object(),
        proxy_map={},
        upstream_health={},
        persistence_policy=object(),
        runtime_ref={"current": object()},
        session_store=object(),
        upload_route="/upload/{server_id}",
        telemetry=None,
        build_memory_persistence_runtime=object(),
        save_upload_stream=object(),
    )

    assert isinstance(captured_m["context"], ctx.MiddlewareRegistrationContext)
    assert isinstance(captured_r["context"], ctx.RouteRegistrationContext)


@pytest.mark.asyncio
async def test_apply_runtime_failure_policy_and_health_payload(monkeypatch):
    called = {"n": 0}

    async def fake_apply(**kwargs):
        called["n"] += 1

    monkeypatch.setattr(hph, "apply_runtime_failure_policy", fake_apply)

    memory_cfg = SimpleNamespace(state_persistence=SimpleNamespace(type="memory"))
    other_cfg = SimpleNamespace(state_persistence=SimpleNamespace(type="disk"))

    result_memory = await hph.apply_runtime_failure_policy_if_persistent_backend(
        resolved_config=memory_cfg,
        persistence_policy=object(),
        runtime_ref={"current": object()},
        session_store=object(),
        app=FastAPI(),
        component="x",
        error="e",
        build_memory_persistence_runtime=object(),
    )
    result_other = await hph.apply_runtime_failure_policy_if_persistent_backend(
        resolved_config=other_cfg,
        persistence_policy=object(),
        runtime_ref={"current": object()},
        session_store=object(),
        app=FastAPI(),
        component="x",
        error="e",
        build_memory_persistence_runtime=object(),
    )

    assert result_memory is False
    assert result_other is True
    assert called["n"] == 1

    app = FastAPI()
    app.state.startup_readiness = {"ready": True}
    app.state.adapter_wiring = {"ready": True}
    app.state.startup_reconciliation = {"done": True}

    policy = SimpleNamespace(snapshot=lambda: {"status": "ok"})
    payload, has_error = hph.build_healthz_payload(
        app=app,
        resolved_config=SimpleNamespace(state_persistence=SimpleNamespace(type="disk")),
        checks=[{"status": "ok"}],
        persistence={"status": "ok", "type": "disk"},
        persistence_policy=policy,
    )
    assert has_error is False
    assert payload["status"] == "ok"

    bad_policy = SimpleNamespace(snapshot=lambda: {"status": "degraded", "degraded_reason": "x"})
    app.state.adapter_wiring = {"ready": False}
    payload2, has_error2 = hph.build_healthz_payload(
        app=app,
        resolved_config=SimpleNamespace(state_persistence=SimpleNamespace(type="disk")),
        checks=[{"status": "error"}],
        persistence={"status": "ok", "type": "disk"},
        persistence_policy=bad_policy,
    )
    assert has_error2 is True
    assert payload2["status"] == "degraded"

    payload_wiring, has_error_wiring = hph.build_healthz_payload(
        app=app,
        resolved_config=SimpleNamespace(state_persistence=SimpleNamespace(type="disk")),
        checks=[{"status": "ok"}],
        persistence={"status": "ok", "type": "disk"},
        persistence_policy=policy,
    )
    assert has_error_wiring is True
    assert payload_wiring["degraded_reason"] == "adapter_wiring_incomplete"

    app.state.adapter_wiring = {"ready": True}
    payload3, has_error3 = hph.build_healthz_payload(
        app=app,
        resolved_config=SimpleNamespace(state_persistence=SimpleNamespace(type="disk")),
        checks=[{"status": "ok"}],
        persistence={"status": "ok", "type": "disk"},
        persistence_policy=bad_policy,
    )
    assert has_error3 is True
    assert payload3["degraded_reason"] == "x"


@pytest.mark.asyncio
async def test_runtime_upstream_helpers(monkeypatch):
    ok_mount = SimpleNamespace(
        server=SimpleNamespace(id="s1", mount_path="/mcp/s1", upstream=SimpleNamespace(url="http://x")),
        clients=SimpleNamespace(build_probe_client=lambda: _ProbeClient(ok=True)),
    )
    err_mount = SimpleNamespace(
        server=SimpleNamespace(id="s2", mount_path="/mcp/s2", upstream=SimpleNamespace(url="http://y")),
        clients=SimpleNamespace(build_probe_client=lambda: _ProbeClient(ok=False)),
    )

    ok = await ruh.probe_upstream(ok_mount)
    bad = await ruh.probe_upstream(err_mount)
    assert ok["status"] == "ok"
    assert bad["status"] == "error"

    checks = [{"server_id": "s1", "status": "ok"}, {"server_id": "s2", "status": "error"}]
    assert ruh.all_upstreams_ready(checks) is False
    assert ruh.not_ready_server_ids(checks) == ["s2"]

    gathered = await ruh.probe_all_upstreams({"s1": ok_mount})
    assert len(gathered) == 1

    seq = [
        [{"server_id": "s1", "status": "error"}],
        [{"server_id": "s1", "status": "ok"}],
    ]

    async def fake_probe_all(_):
        return seq.pop(0)

    mono_state = {"value": 0.0}

    def fake_monotonic():
        mono_state["value"] += 0.1
        return mono_state["value"]

    monkeypatch.setattr(ruh, "probe_all_upstreams", fake_probe_all)
    monkeypatch.setattr(ruh.time, "monotonic", fake_monotonic)

    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(ruh.asyncio, "sleep", fake_sleep)

    checks_final, elapsed = await ruh.wait_for_upstream_readiness({"s1": ok_mount}, 5)
    assert checks_final[0]["status"] == "ok"
    assert sleep_calls and sleep_calls[0] <= 1
    assert elapsed >= 0

    payload = ruh.build_startup_readiness(5, 0.23456, [{"server_id": "s1", "status": "error"}])
    assert payload["ready_within_wait_budget"] is False
    assert payload["waited_seconds"] == 0.235

    async def always_error(_):
        return [{"server_id": "s1", "status": "error"}]

    monkeypatch.setattr(ruh, "probe_all_upstreams", always_error)
    t_state = {"value": 0.0}

    def fake_monotonic_timeout():
        t_state["value"] += 1.0
        return t_state["value"]

    monkeypatch.setattr(ruh.time, "monotonic", fake_monotonic_timeout)

    timeout_checks, _ = await ruh.wait_for_upstream_readiness({"s1": ok_mount}, 0)
    assert timeout_checks[0]["status"] == "error"


class _ProbeClient:
    def __init__(self, ok=True):
        self.ok = ok

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def list_tools(self):
        if not self.ok:
            raise RuntimeError("down")
        return []
