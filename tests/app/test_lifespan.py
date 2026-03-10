from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from remote_mcp_adapter.app import lifespan as ls


class _MountedApp:
    def __init__(self, events: list[str], name: str):
        self._events = events
        self._name = name

    @asynccontextmanager
    async def lifespan(self, app):
        self._events.append(f"enter:{self._name}")
        yield
        self._events.append(f"exit:{self._name}")


class _StateRepository:
    def __init__(self, exc: Exception | None = None):
        self._exc = exc
        self.calls = 0

    async def session_count(self):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return 1


class _Runtime:
    def __init__(self, *, backend_type="disk", state_repository=None, redis_health_monitor=None, memory_snapshot_manager=None):
        self.backend_type = backend_type
        self.state_repository = state_repository or _StateRepository()
        self.lock_provider = object()
        self.redis_health_monitor = redis_health_monitor
        self.memory_snapshot_manager = memory_snapshot_manager
        self.closed = 0

    async def close(self):
        self.closed += 1


class _SessionStore:
    def __init__(self):
        self.shutdown_calls = 0

    async def shutdown(self):
        self.shutdown_calls += 1


class _Monitor:
    def __init__(self, *, enabled=True):
        self.enabled = enabled
        self.run_once_calls = 0

    async def run_once(self):
        self.run_once_calls += 1


class _MemorySnapshotManager:
    def __init__(self, *, fail_once=False):
        self.fail_once = fail_once
        self.run_once_calls = 0
        self.run_loop_calls = 0

    async def run_once(self):
        self.run_once_calls += 1
        if self.fail_once:
            raise RuntimeError("snapshot failed")

    async def run_loop(self, stop_event):
        self.run_loop_calls += 1
        await stop_event.wait()


class _Telemetry:
    def __init__(self, *, enabled=True):
        self.enabled = enabled
        self.started = 0
        self.shutdowns = 0

    async def start(self):
        self.started += 1

    async def shutdown(self):
        self.shutdowns += 1


@pytest.mark.asyncio
async def test_build_lifespan_full_runtime_flow(monkeypatch):
    app = FastAPI()
    session_store = _SessionStore()
    mounted_events: list[str] = []
    memory_snapshot_manager = _MemorySnapshotManager(fail_once=True)
    active_runtime = _Runtime(
        backend_type="disk",
        redis_health_monitor=SimpleNamespace(),
        memory_snapshot_manager=memory_snapshot_manager,
    )
    runtime_ref = {"current": active_runtime}
    telemetry = _Telemetry(enabled=True)
    readiness_logs: list[str] = []
    redaction_calls: list[object] = []
    lock_provider_values: list[object] = []

    async def _wait_forever(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(ls, "install_log_redaction_filter", lambda config: redaction_calls.append(config))
    monkeypatch.setattr(ls, "set_redis_storage_lock_provider", lambda provider: lock_provider_values.append(provider))
    monkeypatch.setattr(ls, "run_startup_reconciliation", lambda **kwargs: asyncio.sleep(0, result={"status": "ok"}))
    monkeypatch.setattr(
        ls,
        "wait_for_upstream_readiness",
        lambda **kwargs: asyncio.sleep(0, result=([{"server_id": "s1", "status": "error"}], 4.2)),
    )
    monkeypatch.setattr(
        ls,
        "build_startup_readiness",
        lambda **kwargs: {
            "ready_within_wait_budget": False,
            "waited_seconds": kwargs["waited_seconds"],
            "not_ready_servers": ["s1"],
        },
    )
    monkeypatch.setattr(
        ls,
        "wire_adapters",
        lambda **kwargs: asyncio.sleep(0, result={"s1": False}),
    )
    monkeypatch.setattr(
        ls,
        "wire_adapters_until_ready",
        lambda **kwargs: asyncio.sleep(0, result={"s1": True}),
    )
    monkeypatch.setattr(ls, "run_cleanup_supervisor", _wait_forever)
    monkeypatch.setattr(ls, "run_upstream_health_monitor", _wait_forever)
    monkeypatch.setattr(ls, "run_redis_persistence_monitor", _wait_forever)
    monkeypatch.setattr(ls.logger, "warning", lambda *args, **kwargs: readiness_logs.append("warning"))
    monkeypatch.setattr(ls.logger, "exception", lambda *args, **kwargs: readiness_logs.append("exception"))

    lifespan_cm = ls.build_lifespan(
        resolved_config=SimpleNamespace(core=SimpleNamespace(max_start_wait_seconds=9, cleanup_interval_seconds=1)),
        runtime_ref=runtime_ref,
        session_store=session_store,
        proxy_map={"s1": SimpleNamespace(clients=SimpleNamespace(close_all=lambda: asyncio.sleep(0)))},
        upstream_health={"s1": _Monitor(enabled=True), "s2": _Monitor(enabled=False)},
        write_policy_lock_mode="redis",
        persistence_policy=SimpleNamespace(handle_startup_failure=lambda **kwargs: "continue_fail_closed"),
        mounted_http_apps={"a": _MountedApp(mounted_events, "a")},
        upload_credentials=None,
        artifact_download_credentials=None,
        telemetry=telemetry,
        build_memory_persistence_runtime=lambda: None,
    )

    async with lifespan_cm(app):
        await asyncio.sleep(0)
        assert app.state.startup_readiness["ready_within_wait_budget"] is False
        assert app.state.adapter_wiring["ready"] is True
        assert telemetry.started == 1

    assert mounted_events == ["enter:a", "exit:a"]
    assert session_store.shutdown_calls == 1
    assert active_runtime.closed == 1
    assert telemetry.shutdowns == 1
    assert len(redaction_calls) == 2
    assert lock_provider_values[0] is active_runtime.lock_provider
    assert lock_provider_values[-1] is None
    assert "warning" in readiness_logs
    assert "exception" in readiness_logs
    assert memory_snapshot_manager.run_once_calls == 1
    assert memory_snapshot_manager.run_loop_calls == 1


@pytest.mark.asyncio
async def test_build_lifespan_startup_probe_fallback_and_ready_path(monkeypatch):
    app = FastAPI()
    fallback_calls: list[str] = []
    info_logs: list[str] = []
    active_runtime = _Runtime(backend_type="memory", state_repository=_StateRepository(exc=RuntimeError("db down")))

    monkeypatch.setattr(ls, "install_log_redaction_filter", lambda config: None)
    monkeypatch.setattr(ls, "set_redis_storage_lock_provider", lambda provider: None)
    monkeypatch.setattr(
        ls,
        "activate_memory_persistence_fallback",
        lambda **kwargs: asyncio.sleep(0, result=fallback_calls.append("switched")),
    )
    monkeypatch.setattr(ls, "run_startup_reconciliation", lambda **kwargs: asyncio.sleep(0, result={"status": "ok"}))
    monkeypatch.setattr(
        ls,
        "wait_for_upstream_readiness",
        lambda **kwargs: asyncio.sleep(0, result=([{"server_id": "s1", "status": "ok"}], 1.0)),
    )
    monkeypatch.setattr(
        ls,
        "build_startup_readiness",
        lambda **kwargs: {
            "ready_within_wait_budget": True,
            "waited_seconds": kwargs["waited_seconds"],
            "not_ready_servers": [],
        },
    )
    monkeypatch.setattr(ls, "wire_adapters", lambda **kwargs: asyncio.sleep(0, result={"s1": True}))
    monkeypatch.setattr(ls.logger, "info", lambda *args, **kwargs: info_logs.append("info"))

    lifespan_cm = ls.build_lifespan(
        resolved_config=SimpleNamespace(core=SimpleNamespace(max_start_wait_seconds=5, cleanup_interval_seconds=None)),
        runtime_ref={"current": active_runtime},
        session_store=_SessionStore(),
        proxy_map={"s1": SimpleNamespace(clients=SimpleNamespace(close_all=lambda: asyncio.sleep(0)))},
        upstream_health={"s1": _Monitor(enabled=False)},
        write_policy_lock_mode="file",
        persistence_policy=SimpleNamespace(handle_startup_failure=lambda **kwargs: "switch_to_fallback"),
        mounted_http_apps={},
        upload_credentials=None,
        artifact_download_credentials=None,
        telemetry=None,
        build_memory_persistence_runtime=lambda: None,
    )

    async with lifespan_cm(app):
        assert app.state.startup_readiness["ready_within_wait_budget"] is True
        assert app.state.adapter_wiring["ready"] is True

    assert fallback_calls == ["switched"]
    assert info_logs == ["info"]
