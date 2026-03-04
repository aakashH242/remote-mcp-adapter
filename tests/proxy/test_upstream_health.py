from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from remote_mcp_adapter.proxy import upstream_health as uh


class _FakeProbeClient:
    def __init__(self, *, ping_result=True, ping_exc: Exception | None = None):
        self.ping_result = ping_result
        self.ping_exc = ping_exc
        self.entered = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def ping(self):
        if self.ping_exc is not None:
            raise self.ping_exc
        return self.ping_result


class _FakeClientRegistry:
    def __init__(self, probe_client=None, reset_count=2):
        self.probe_client = probe_client or _FakeProbeClient()
        self.reset_count = reset_count
        self.build_calls: list[float] = []
        self.reset_calls: list[str] = []

    def build_probe_client(self, *, timeout_seconds):
        self.build_calls.append(timeout_seconds)
        return self.probe_client

    async def reset_cached_clients(self, *, reason: str):
        self.reset_calls.append(reason)
        return self.reset_count


class _FakeTelemetry:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.breaker_states: list[tuple[str, str]] = []
        self.pings: list[dict[str, object]] = []

    async def set_circuit_breaker_state(self, *, server_id: str, state: str):
        self.breaker_states.append((server_id, state))

    async def record_upstream_ping(self, **kwargs):
        self.pings.append(kwargs)


def _policy(**overrides):
    base = dict(
        enabled=True,
        interval_seconds=15,
        timeout_seconds=5,
        failure_threshold=3,
        open_cooldown_seconds=30,
        half_open_probe_allowance=2,
    )
    base.update(overrides)
    return uh.ResolvedUpstreamPingPolicy(**base)


def _monitor(*, policy=None, clients=None, telemetry=None):
    return uh.UpstreamHealthMonitor(
        server_id="srv",
        mount_path="/mcp/srv",
        upstream_url="http://up.example",
        policy=policy or _policy(),
        client_registry=clients or _FakeClientRegistry(),
        telemetry=telemetry,
    )


def test_first_defined_and_policy_resolution():
    assert uh._first_defined(None, None, "x") == "x"
    assert uh._first_defined(None, None) is None

    core = SimpleNamespace(
        enabled=False,
        interval_seconds=20,
        timeout_seconds=6,
        failure_threshold=4,
        open_cooldown_seconds=40,
        half_open_probe_allowance=3,
    )
    server = SimpleNamespace(
        enabled=True,
        interval_seconds=10,
        timeout_seconds=2,
        failure_threshold=2,
        open_cooldown_seconds=5,
        half_open_probe_allowance=1,
    )

    resolved = uh.resolve_upstream_ping_policy(core_defaults=core, server_overrides=server)
    assert resolved.enabled is True
    assert resolved.interval_seconds == 10
    assert resolved.timeout_seconds == 2
    assert resolved.failure_threshold == 2
    assert resolved.open_cooldown_seconds == 5
    assert resolved.half_open_probe_allowance == 1


def test_monitor_properties_and_state_transitions(monkeypatch):
    monitor = _monitor()
    assert monitor.enabled is True
    assert monitor.server_id == "srv"

    infos: list[str] = []
    monkeypatch.setattr(uh.logger, "info", lambda msg, *args, **kwargs: infos.append(msg))

    monitor._transition_to_open_locked(100.0)
    assert monitor._state == "open"
    monitor._advance_state_for_time_locked(130.0)
    assert monitor._state == "half_open"
    assert any("open to half_open" in msg for msg in infos)

    monitor._transition_to_closed_locked()
    assert monitor._state == "closed"


@pytest.mark.asyncio
async def test_emit_breaker_state_noop_and_enabled_paths():
    monitor_none = _monitor(telemetry=None)
    await monitor_none._emit_breaker_state("open")

    tel_disabled = _FakeTelemetry(enabled=False)
    monitor_disabled = _monitor(telemetry=tel_disabled)
    await monitor_disabled._emit_breaker_state("open")
    assert tel_disabled.breaker_states == []

    tel_enabled = _FakeTelemetry(enabled=True)
    monitor_enabled = _monitor(telemetry=tel_enabled)
    await monitor_enabled._emit_breaker_state("open")
    assert tel_enabled.breaker_states == [("srv", "open")]


@pytest.mark.asyncio
async def test_begin_probe_open_and_half_open_allowance(monkeypatch):
    monitor = _monitor(policy=_policy(open_cooldown_seconds=50, half_open_probe_allowance=1))

    monitor._state = "open"
    monitor._opened_at_monotonic = 100.0
    monkeypatch.setattr(uh.time, "monotonic", lambda: 120.0)
    should_probe, state = await monitor._begin_probe()
    assert (should_probe, state) == (False, "open")

    monitor._state = "half_open"
    monitor._half_open_probe_count = 1
    should_probe, state = await monitor._begin_probe()
    assert (should_probe, state) == (False, "half_open")

    monitor._half_open_probe_count = 0
    should_probe, state = await monitor._begin_probe()
    assert (should_probe, state) == (True, "half_open")
    assert monitor._half_open_probe_count == 1


@pytest.mark.asyncio
async def test_run_once_disabled_and_skip_when_begin_probe_false(monkeypatch):
    disabled = _monitor(policy=_policy(enabled=False))
    await disabled.run_once()

    monitor = _monitor()

    async def fake_begin_probe():
        return False, "open"

    called = {"failure": 0, "success": 0}

    async def fake_failure(**kwargs):
        called["failure"] += 1

    async def fake_success(**kwargs):
        called["success"] += 1

    monkeypatch.setattr(monitor, "_begin_probe", fake_begin_probe)
    monkeypatch.setattr(monitor, "_record_failure", fake_failure)
    monkeypatch.setattr(monitor, "_record_success", fake_success)

    await monitor.run_once()
    assert called == {"failure": 0, "success": 0}


@pytest.mark.asyncio
async def test_run_once_success_and_failure_paths(monkeypatch):
    successes: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    monitor_success = _monitor(clients=_FakeClientRegistry(probe_client=_FakeProbeClient(ping_result=True)))

    async def begin_probe_success():
        return True, "closed"

    async def record_success(**kwargs):
        successes.append(kwargs)

    async def record_failure(**kwargs):
        failures.append(kwargs)

    monkeypatch.setattr(monitor_success, "_begin_probe", begin_probe_success)
    monkeypatch.setattr(monitor_success, "_record_success", record_success)
    monkeypatch.setattr(monitor_success, "_record_failure", record_failure)
    monkeypatch.setattr(uh.time, "perf_counter", lambda: 1.0)

    await monitor_success.run_once()
    assert len(successes) == 1

    monitor_false_ping = _monitor(clients=_FakeClientRegistry(probe_client=_FakeProbeClient(ping_result=False)))
    monkeypatch.setattr(monitor_false_ping, "_begin_probe", begin_probe_success)
    monkeypatch.setattr(monitor_false_ping, "_record_success", record_success)
    monkeypatch.setattr(monitor_false_ping, "_record_failure", record_failure)
    await monitor_false_ping.run_once()

    monitor_exc = _monitor(clients=_FakeClientRegistry(probe_client=_FakeProbeClient(ping_exc=RuntimeError("boom"))))
    monkeypatch.setattr(monitor_exc, "_begin_probe", begin_probe_success)
    monkeypatch.setattr(monitor_exc, "_record_success", record_success)
    monkeypatch.setattr(monitor_exc, "_record_failure", record_failure)
    await monitor_exc.run_once()

    assert len(failures) == 2


@pytest.mark.asyncio
async def test_run_loop_calls_once_then_sleeps(monkeypatch):
    monitor = _monitor(policy=_policy(interval_seconds=7))
    calls = {"run_once": 0, "sleep": 0}

    async def fake_run_once():
        calls["run_once"] += 1

    async def fake_sleep(seconds):
        calls["sleep"] += 1
        raise asyncio.CancelledError()

    monkeypatch.setattr(monitor, "run_once", fake_run_once)
    monkeypatch.setattr(uh.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await monitor.run_loop()

    assert calls == {"run_once": 1, "sleep": 1}


@pytest.mark.asyncio
async def test_record_success_closed_recovery_and_half_open_closure(monkeypatch):
    telemetry = _FakeTelemetry(enabled=True)
    monitor = _monitor(telemetry=telemetry)

    infos: list[str] = []
    monkeypatch.setattr(uh.logger, "debug", lambda *args, **kwargs: None)
    monkeypatch.setattr(uh.logger, "info", lambda message, *args, **kwargs: infos.append(message))
    monkeypatch.setattr(uh.time, "time", lambda: 10.0)

    monitor._state = "closed"
    monitor._consecutive_failures = 2
    await monitor._record_success(latency_ms=1.2, prior_state="closed")
    assert monitor._consecutive_failures == 0
    assert any("recovered after prior ping failures" in msg for msg in infos)

    monitor._state = "half_open"
    monitor._half_open_success_count = 1
    monitor._policy = _policy(half_open_probe_allowance=2)
    await monitor._record_success(latency_ms=1.3, prior_state="half_open")
    assert monitor._state == "closed"
    assert any("Circuit breaker closed after successful half-open probes" in msg for msg in infos)
    assert telemetry.pings[-1]["result"] == "success"


@pytest.mark.asyncio
async def test_record_failure_paths_and_open_transition(monkeypatch):
    telemetry = _FakeTelemetry(enabled=True)
    clients = _FakeClientRegistry(reset_count=5)
    monitor = _monitor(policy=_policy(failure_threshold=2), clients=clients, telemetry=telemetry)

    warnings: list[str] = []
    monkeypatch.setattr(uh.logger, "warning", lambda message, *args, **kwargs: warnings.append(message))
    monkeypatch.setattr(uh.time, "time", lambda: 11.0)
    monkeypatch.setattr(uh.time, "monotonic", lambda: 100.0)

    monitor._state = "closed"
    monitor._consecutive_failures = 0
    await monitor._record_failure(exc=RuntimeError("a"), latency_ms=1.0, prior_state="closed")
    assert monitor._state == "closed"
    assert clients.reset_calls == []

    await monitor._record_failure(exc=RuntimeError("b"), latency_ms=1.0, prior_state="closed")
    assert monitor._state == "open"
    assert clients.reset_calls == ["circuit_breaker_open"]

    monitor._state = "half_open"
    await monitor._record_failure(exc=RuntimeError("c"), latency_ms=1.0, prior_state="half_open")
    assert monitor._state == "open"

    monitor._state = "open"
    await monitor._record_failure(exc=RuntimeError("d"), latency_ms=1.0, prior_state="open")
    assert monitor._state == "open"
    assert telemetry.pings[-1]["result"] == "failure"
    assert any("Circuit breaker opened" in msg for msg in warnings)


@pytest.mark.asyncio
async def test_allow_proxy_request_and_health_snapshot(monkeypatch):
    disabled = _monitor(policy=_policy(enabled=False))
    assert await disabled.allow_proxy_request() == (True, None)
    disabled_snapshot = await disabled.health_snapshot()
    assert disabled_snapshot["detail"] == "upstream_ping_disabled"

    monitor = _monitor()
    monkeypatch.setattr(uh.time, "monotonic", lambda: 100.0)

    monitor._state = "closed"
    assert await monitor.allow_proxy_request() == (True, None)

    monitor._state = "open"
    monitor._opened_at_monotonic = 90.0
    monitor._policy = _policy(open_cooldown_seconds=50)
    assert await monitor.allow_proxy_request() == (False, "Upstream is unhealthy (circuit breaker open).")

    monitor._state = "half_open"
    assert await monitor.allow_proxy_request() == (False, "Upstream is recovering (circuit breaker half_open).")

    monitor._state = "closed"
    snap_closed = await monitor.health_snapshot()
    assert snap_closed["status"] == "ok"
    assert "detail" not in snap_closed

    monitor._state = "open"
    monitor._opened_at_monotonic = 95.0
    monitor._policy = _policy(open_cooldown_seconds=30)
    snap_degraded = await monitor.health_snapshot()
    assert snap_degraded["status"] == "degraded"
    assert snap_degraded["detail"] == "upstream_unhealthy"
