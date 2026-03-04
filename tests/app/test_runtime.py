from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from remote_mcp_adapter.app import runtime as rt


class _FakePolicyController:
    def __init__(self, runtime_action="switch_to_fallback", startup_action="switch_to_fallback"):
        self.runtime_action = runtime_action
        self.startup_action = startup_action
        self.runtime_failures = []
        self.recoveries = []

    def handle_runtime_failure(self, *, component: str, error: str):
        self.runtime_failures.append((component, error))
        return self.runtime_action

    def handle_runtime_recovery(self, *, component: str):
        self.recoveries.append(component)

    def handle_startup_failure(self, *, phase: str, error: str):
        return self.startup_action


class _FakeSessionStore:
    def __init__(self):
        self.replacements = []
        self.cleanup_results = []

    def replace_backends(self, **kwargs):
        self.replacements.append(kwargs)

    async def cleanup_once(self):
        if self.cleanup_results:
            value = self.cleanup_results.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        return {}


class _FakeRuntime:
    def __init__(self, backend_type="disk"):
        self.backend_type = backend_type
        self.state_repository = object()
        self.lock_provider = object()
        self.closed = 0

    async def close(self):
        self.closed += 1


class _FakeUploadCredentials:
    def __init__(self):
        self.nonce_backend = "redis"
        self.used_memory = 0

    def use_memory_nonce_store(self):
        self.used_memory += 1
        self.nonce_backend = "memory"


class _FakeHealthMonitor:
    def __init__(self, snapshots=None, interval_seconds=1):
        self.snapshots = list(snapshots or [{"status": "ok"}])
        self.interval_seconds = interval_seconds
        self.run_once_calls = 0

    async def run_once(self):
        self.run_once_calls += 1

    async def health_snapshot(self):
        if self.snapshots:
            return self.snapshots.pop(0)
        return {"status": "ok"}


class _FakeUpstreamMonitor:
    def __init__(self, crash=None):
        self.crash = crash
        self.server_id = "s1"

    async def run_loop(self):
        if self.crash is not None:
            raise self.crash


class _FakeTelemetry:
    def __init__(self):
        self.cleanup_cycles = []
        self.wiring_runs = []

    async def record_cleanup_cycle(self, **kwargs):
        self.cleanup_cycles.append(kwargs)

    async def record_adapter_wiring_run(self, **kwargs):
        self.wiring_runs.append(kwargs)


def test_terminate_process_for_policy_exit(monkeypatch):
    called = {}
    monkeypatch.setattr(rt.os, "_exit", lambda code: called.setdefault("code", code))
    rt.terminate_process_for_policy_exit()
    assert called["code"] == 1


def test_build_upstream_health_monitors(monkeypatch):
    monitor_calls = []
    monkeypatch.setattr(rt, "resolve_upstream_ping_policy", lambda **kwargs: "policy")

    class FakeMonitor:
        def __init__(self, **kwargs):
            monitor_calls.append(kwargs)

    monkeypatch.setattr(rt, "UpstreamHealthMonitor", FakeMonitor)

    config = SimpleNamespace(
        core=SimpleNamespace(upstream_ping=object()),
        servers=[
            SimpleNamespace(id="s1", mount_path="/mcp/s1", upstream=SimpleNamespace(url="http://u1"), upstream_ping=object()),
            SimpleNamespace(id="s2", mount_path="/mcp/s2", upstream=SimpleNamespace(url="http://u2"), upstream_ping=object()),
        ],
    )
    proxy_map = {
        "s1": SimpleNamespace(clients="c1"),
        "s2": SimpleNamespace(clients="c2"),
    }
    monitors = rt.build_upstream_health_monitors(config, proxy_map, telemetry="tel")
    assert set(monitors.keys()) == {"s1", "s2"}
    assert len(monitor_calls) == 2


@pytest.mark.asyncio
async def test_collect_and_run_upstream_health_helpers(monkeypatch):
    checks = await rt.collect_upstream_health_checks(
        {
            "a": SimpleNamespace(health_snapshot=lambda: asyncio.sleep(0, result={"status": "ok"})),
            "b": SimpleNamespace(health_snapshot=lambda: asyncio.sleep(0, result={"status": "degraded"})),
        }
    )
    assert len(checks) == 2

    monitor_ok = _FakeUpstreamMonitor()
    await rt.run_upstream_health_monitor(monitor_ok)

    monitor_cancel = _FakeUpstreamMonitor(crash=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await rt.run_upstream_health_monitor(monitor_cancel)

    errors = []
    monkeypatch.setattr(rt.logger, "exception", lambda *args, **kwargs: errors.append("logged"))
    monitor_err = _FakeUpstreamMonitor(crash=RuntimeError("boom"))
    await rt.run_upstream_health_monitor(monitor_err)
    assert errors == ["logged"]


@pytest.mark.asyncio
async def test_activate_memory_persistence_fallback_paths(monkeypatch):
    app = FastAPI()
    app.state.upload_credentials = _FakeUploadCredentials()

    current = _FakeRuntime("disk")
    runtime_ref = {"current": current}
    store = _FakeSessionStore()

    fallback_runtime = _FakeRuntime("memory")

    monkeypatch.setattr(rt, "set_redis_storage_lock_provider", lambda value: None)
    monkeypatch.setattr(rt, "UploadCredentialManager", _FakeUploadCredentials)

    await rt.activate_memory_persistence_fallback(
        runtime_ref=runtime_ref,
        session_store=store,
        app=app,
        build_memory_persistence_runtime=lambda: fallback_runtime,
    )

    assert runtime_ref["current"] is fallback_runtime
    assert app.state.persistence_runtime is fallback_runtime
    assert store.replacements
    assert app.state.upload_credentials.nonce_backend == "memory"
    assert current.closed == 1

    memory_runtime_ref = {"current": _FakeRuntime("memory")}
    before = memory_runtime_ref["current"]
    await rt.activate_memory_persistence_fallback(
        runtime_ref=memory_runtime_ref,
        session_store=store,
        app=app,
        build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
    )
    assert memory_runtime_ref["current"] is before


@pytest.mark.asyncio
async def test_run_redis_persistence_monitor_actions(monkeypatch):
    app = FastAPI()
    session_store = _FakeSessionStore()
    runtime_ref = {"current": _FakeRuntime("disk")}

    switched = {"n": 0}

    async def fake_switch(**kwargs):
        switched["n"] += 1

    monkeypatch.setattr(rt, "activate_memory_persistence_fallback", fake_switch)

    monitor_ok = _FakeHealthMonitor(snapshots=[{"status": "ok"}])
    policy_ok = _FakePolicyController(runtime_action="switch_to_fallback")

    async def stop_sleep(_):
        raise asyncio.CancelledError()

    monkeypatch.setattr(rt.asyncio, "sleep", stop_sleep)
    with pytest.raises(asyncio.CancelledError):
        await rt.run_redis_persistence_monitor(
            monitor=monitor_ok,
            policy_controller=policy_ok,
            runtime_ref=runtime_ref,
            session_store=session_store,
            app=app,
            build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
        )
    assert policy_ok.recoveries == ["redis_persistence_health"]

    monitor_fail = _FakeHealthMonitor(snapshots=[{"status": "error", "ping": {"last_error": "x"}}])
    policy_switch = _FakePolicyController(runtime_action="switch_to_fallback")
    await rt.run_redis_persistence_monitor(
        monitor=monitor_fail,
        policy_controller=policy_switch,
        runtime_ref=runtime_ref,
        session_store=session_store,
        app=app,
        build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
    )
    assert switched["n"] == 1

    monitor_exit = _FakeHealthMonitor(snapshots=[{"status": "error", "ping": {"last_error": "x"}}])
    policy_exit = _FakePolicyController(runtime_action="exit")
    terminated = {"n": 0}
    monkeypatch.setattr(rt, "terminate_process_for_policy_exit", lambda: terminated.__setitem__("n", 1))
    await rt.run_redis_persistence_monitor(
        monitor=monitor_exit,
        policy_controller=policy_exit,
        runtime_ref=runtime_ref,
        session_store=session_store,
        app=app,
        build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
    )
    assert terminated["n"] == 1

    errors = []
    monkeypatch.setattr(rt.logger, "exception", lambda *args, **kwargs: errors.append("crashed"))

    class CrashMonitor(_FakeHealthMonitor):
        async def run_once(self):
            raise RuntimeError("boom")

    await rt.run_redis_persistence_monitor(
        monitor=CrashMonitor(),
        policy_controller=policy_ok,
        runtime_ref=runtime_ref,
        session_store=session_store,
        app=app,
        build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
    )
    assert errors == ["crashed"]


@pytest.mark.asyncio
async def test_apply_runtime_failure_policy_and_startup_reconciliation(monkeypatch):
    app = FastAPI()
    store = _FakeSessionStore()
    runtime_ref = {"current": _FakeRuntime("disk")}

    switched = {"n": 0}
    exited = {"n": 0}

    async def fake_switch(**kwargs):
        switched["n"] += 1

    monkeypatch.setattr(rt, "activate_memory_persistence_fallback", fake_switch)
    monkeypatch.setattr(rt, "terminate_process_for_policy_exit", lambda: exited.__setitem__("n", 1))

    await rt.apply_runtime_failure_policy(
        policy_controller=_FakePolicyController(runtime_action="switch_to_fallback"),
        runtime_ref=runtime_ref,
        session_store=store,
        app=app,
        component="c",
        error="e",
        build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
    )
    await rt.apply_runtime_failure_policy(
        policy_controller=_FakePolicyController(runtime_action="exit"),
        runtime_ref=runtime_ref,
        session_store=store,
        app=app,
        component="c",
        error="e",
        build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
    )
    assert switched["n"] == 1
    assert exited["n"] == 1

    config = SimpleNamespace(state_persistence=SimpleNamespace(reconciliation=SimpleNamespace(mode="if_empty")))

    async def reconcile_ok(**kwargs):
        return {"status": "ok"}

    monkeypatch.setattr(rt, "run_startup_state_reconciliation", reconcile_ok)
    ok = await rt.run_startup_reconciliation(
        config=config,
        policy_controller=_FakePolicyController(),
        runtime_ref=runtime_ref,
        session_store=store,
        app=app,
        build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
    )
    assert ok["status"] == "ok"

    async def reconcile_fail(**kwargs):
        raise RuntimeError("fail")

    monkeypatch.setattr(rt, "run_startup_state_reconciliation", reconcile_fail)

    with pytest.raises(RuntimeError):
        await rt.run_startup_reconciliation(
            config=config,
            policy_controller=_FakePolicyController(startup_action="exit"),
            runtime_ref=runtime_ref,
            session_store=store,
            app=app,
            build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
        )

    fallback_result = await rt.run_startup_reconciliation(
        config=config,
        policy_controller=_FakePolicyController(startup_action="switch_to_fallback"),
        runtime_ref=runtime_ref,
        session_store=store,
        app=app,
        build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
    )
    assert fallback_result["status"] == "error"
    assert fallback_result["reason"] == "startup_reconciliation_failed_after_fallback"

    no_switch_result = await rt.run_startup_reconciliation(
        config=config,
        policy_controller=_FakePolicyController(startup_action="ignore"),
        runtime_ref=runtime_ref,
        session_store=store,
        app=app,
        build_memory_persistence_runtime=lambda: _FakeRuntime("memory"),
    )
    assert no_switch_result["reason"] == "startup_reconciliation_failed"


@pytest.mark.asyncio
async def test_cleanup_worker_and_supervisor(monkeypatch):
    telemetry = _FakeTelemetry()
    stop_event = asyncio.Event()
    store = _FakeSessionStore()
    store.cleanup_results = [
        asyncio.CancelledError(),
    ]

    with pytest.raises(asyncio.CancelledError):
        await rt.cleanup_worker(
            session_store=store,
            interval_seconds=0,
            stop_event=stop_event,
            telemetry=telemetry,
        )

    store2 = _FakeSessionStore()
    store2.cleanup_results = [RuntimeError("x"), {"x": 0}, {"x": 1}]
    stop2 = asyncio.Event()

    waits = {"n": 0}

    async def fake_wait_for(awaitable, timeout):
        waits["n"] += 1
        if waits["n"] <= 3:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError()
        stop2.set()
        return await awaitable

    monkeypatch.setattr(rt.asyncio, "wait_for", fake_wait_for)
    await rt.cleanup_worker(
        session_store=store2,
        interval_seconds=1,
        stop_event=stop2,
        telemetry=telemetry,
    )
    assert telemetry.cleanup_cycles

    # supervisor normal worker exit + restart path
    stop3 = asyncio.Event()
    worker_calls = {"n": 0}

    async def fake_worker(**kwargs):
        worker_calls["n"] += 1
        if worker_calls["n"] == 2:
            stop3.set()

    monkeypatch.setattr(rt, "cleanup_worker", fake_worker)

    async def fake_wait_for_supervisor(awaitable, timeout):
        stop3.set()
        return await awaitable

    monkeypatch.setattr(rt.asyncio, "wait_for", fake_wait_for_supervisor)
    await rt.run_cleanup_supervisor(
        session_store=store2,
        interval_seconds=1,
        stop_event=stop3,
        telemetry=telemetry,
    )
    assert worker_calls["n"] >= 1

    # supervisor cancelled branch
    stop4 = asyncio.Event()

    async def cancel_worker(**kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(rt, "cleanup_worker", cancel_worker)

    async def fake_wait_for_cancel(awaitable, timeout):
        stop4.set()
        return await awaitable

    monkeypatch.setattr(rt.asyncio, "wait_for", fake_wait_for_cancel)
    await rt.run_cleanup_supervisor(
        session_store=store2,
        interval_seconds=1,
        stop_event=stop4,
        telemetry=telemetry,
    )


@pytest.mark.asyncio
async def test_cleanup_worker_returns_immediately_when_stopped():
    stop_event = asyncio.Event()
    stop_event.set()
    await rt.cleanup_worker(
        session_store=_FakeSessionStore(),
        interval_seconds=1,
        stop_event=stop_event,
        telemetry=None,
    )


@pytest.mark.asyncio
async def test_cleanup_supervisor_worker_exit_with_stop_event_returns(monkeypatch):
    stop_event = asyncio.Event()

    async def worker_that_stops(**kwargs):
        stop_event.set()

    monkeypatch.setattr(rt, "cleanup_worker", worker_that_stops)
    await rt.run_cleanup_supervisor(
        session_store=_FakeSessionStore(),
        interval_seconds=1,
        stop_event=stop_event,
        telemetry=None,
    )


@pytest.mark.asyncio
async def test_cleanup_supervisor_cancelled_branch_raises_when_stopped(monkeypatch):
    stop_event = asyncio.Event()

    class CancelledWorker:
        def cancel(self):
            stop_event.set()

        def __await__(self):
            raise asyncio.CancelledError()
            yield

    def fake_create_task_cancelled(coro, name=None):
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return CancelledWorker()

    monkeypatch.setattr(rt.asyncio, "create_task", fake_create_task_cancelled)

    with pytest.raises(asyncio.CancelledError):
        await rt.run_cleanup_supervisor(
            session_store=_FakeSessionStore(),
            interval_seconds=1,
            stop_event=stop_event,
            telemetry=None,
        )


@pytest.mark.asyncio
async def test_cleanup_supervisor_crash_branch_and_backoff_timeout(monkeypatch):
    stop_event = asyncio.Event()

    class CrashWorker:
        def cancel(self):
            return None

        def __await__(self):
            raise RuntimeError("boom")
            yield

    def fake_create_task_crash(coro, name=None):
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return CrashWorker()

    monkeypatch.setattr(rt.asyncio, "create_task", fake_create_task_crash)

    errors = []
    monkeypatch.setattr(rt.logger, "exception", lambda *args, **kwargs: errors.append("crash"))

    async def fake_wait_for(awaitable, timeout):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        stop_event.set()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(rt.asyncio, "wait_for", fake_wait_for)

    await rt.run_cleanup_supervisor(
        session_store=_FakeSessionStore(),
        interval_seconds=1,
        stop_event=stop_event,
        telemetry=None,
    )
    assert errors == ["crash"]


@pytest.mark.asyncio
async def test_wire_adapters_until_ready(monkeypatch):
    telemetry = _FakeTelemetry()

    runs = [
        {"s1": False, "s2": True},
        {"s1": True, "s2": True},
    ]

    async def fake_wire_adapters(**kwargs):
        return runs.pop(0)

    monkeypatch.setattr(rt, "wire_adapters", fake_wire_adapters)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(rt.asyncio, "sleep", fake_sleep)

    status = await rt.wire_adapters_until_ready(
        config=SimpleNamespace(),
        proxy_map={},
        session_store=_FakeSessionStore(),
        state=SimpleNamespace(),
        upload_credentials=None,
        artifact_download_credentials=None,
        telemetry=telemetry,
        retry_interval_seconds=7,
    )
    assert status == {"s1": True, "s2": True}
    assert sleep_calls == [7]
    assert telemetry.wiring_runs[0]["result"] == "retry"
    assert telemetry.wiring_runs[1]["result"] == "ready"
