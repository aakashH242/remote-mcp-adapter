from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from remote_mcp_adapter.core.persistence import memory_snapshot as ms
from remote_mcp_adapter.core.persistence import persistence_factory as pf
from remote_mcp_adapter.core.persistence import persistence_policy as pp
from remote_mcp_adapter.core.persistence import redis_support as rs
from remote_mcp_adapter.core.repo.records import SessionState, SessionTombstone


class _Telemetry:
    def __init__(self):
        self.calls = []

    def record_persistence_policy_transition_nowait(self, **kwargs):
        self.calls.append(kwargs)


def test_persistence_policy_controller_paths():
    t = _Telemetry()
    ctrl = pp.PersistencePolicyController(configured_backend="disk", unavailable_policy="fallback_memory", telemetry=t)
    assert ctrl.handle_startup_failure(phase="runtime_build", error="x") == "switch_to_fallback"
    assert ctrl.active_backend == "memory"

    assert ctrl.handle_runtime_failure(component="c", error="e") == "none"

    ctrl2 = pp.PersistencePolicyController(configured_backend="disk", unavailable_policy="fail_closed", telemetry=t)
    assert ctrl2.handle_startup_failure(phase="runtime_build", error="x") == "continue_fail_closed"
    assert ctrl2.should_reject_stateful_requests() is True
    assert ctrl2.handle_runtime_failure(component="c", error="e") == "activate_fail_closed"
    ctrl2.handle_runtime_recovery(component="c")
    assert ctrl2.should_reject_stateful_requests() is False

    ctrl3 = pp.PersistencePolicyController(configured_backend="disk", unavailable_policy="exit", telemetry=t)
    assert ctrl3.handle_startup_failure(phase="runtime_build", error="x") == "exit"
    assert ctrl3.handle_runtime_failure(component="c", error="e") == "exit"
    snap = ctrl3.snapshot()
    assert snap["status"] == "degraded"
    assert t.calls


@pytest.mark.asyncio
async def test_memory_snapshot_manager_paths(monkeypatch):
    class _Repo:
        def __init__(self):
            self.sessions = [(('s1', 'sess'), SimpleNamespace(uploads={}, artifacts={}, in_flight=0))]
            self.tombstones = []

        async def list_session_items(self):
            return self.sessions

        async def list_tombstone_items(self):
            return self.tombstones

    class _SnapRepo:
        def __init__(self):
            self.calls = []
            self.fail = False

        async def replace_all(self, **kwargs):
            self.calls.append(kwargs)
            if self.fail:
                raise RuntimeError("snap")

    repo = _Repo()
    snap_repo = _SnapRepo()
    mgr = ms.MemorySnapshotManager(source_repository=repo, snapshot_repository=snap_repo, interval_seconds=0)
    assert mgr.interval_seconds == 1

    await mgr.run_once()
    health = await mgr.health_snapshot()
    assert health["status"] == "ok"

    snap_repo.fail = True
    with pytest.raises(RuntimeError):
        await mgr.run_once()
    health2 = await mgr.health_snapshot()
    assert health2["status"] == "degraded"

    stop = asyncio.Event()
    stop.set()
    await mgr.run_loop(stop)


def test_redis_support_helpers(monkeypatch):
    cfg = SimpleNamespace(host="h", port=1, db=0, username=None, password=None, tls_insecure=False, key_base="kb")
    assert rs._build_tls_kwargs(cfg) == {}
    cfg.tls_insecure = True
    assert rs._build_tls_kwargs(cfg)["ssl"] is True

    keyspace = rs.build_keyspace("  base  ")
    assert keyspace.sessions_hash.startswith("base")

    class _RedisModule:
        class Redis:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

    monkeypatch.setattr(rs, "_import_redis_asyncio", lambda: _RedisModule)
    client = rs.create_redis_client(cfg)
    assert client.kwargs["host"] == "h"


@pytest.mark.asyncio
async def test_persistence_factory_runtime_and_health(monkeypatch, tmp_path):
    db = tmp_path / "state.sqlite3"
    db.write_text("x", encoding="utf-8")
    status = pf._filesystem_backend_status(db)
    assert status["status"] == "ok"

    bad = pf._filesystem_backend_status(tmp_path / "missing.sqlite3")
    assert bad["status"] == "degraded"

    monkeypatch.setattr(pf, "_build_snapshot_enabled_memory_runtime", lambda config: "mem")
    monkeypatch.setattr(pf, "_build_disk_runtime", lambda config: "disk")
    monkeypatch.setattr(pf, "_build_redis_runtime", lambda config: "redis")

    cfg = SimpleNamespace(state_persistence=SimpleNamespace(type="memory"))
    assert pf.build_persistence_runtime(cfg) == "mem"
    cfg.state_persistence.type = "disk"
    assert pf.build_persistence_runtime(cfg) == "disk"
    cfg.state_persistence.type = "redis"
    assert pf.build_persistence_runtime(cfg) == "redis"

    cfg.state_persistence.type = "other"
    with pytest.raises(ValueError):
        pf.build_persistence_runtime(cfg)

    monkeypatch.setattr(pf, "_build_ephemeral_memory_runtime", lambda: "fallback")
    assert pf.build_memory_persistence_runtime() == "fallback"

    cfg.state_persistence.type = "memory"
    monkeypatch.setattr(pf, "build_persistence_runtime", lambda config: SimpleNamespace(state_repository="repo"))
    assert pf.build_state_repository(cfg) == "repo"

    class _Repo:
        async def list_session_items(self):
            return []

        async def list_tombstone_items(self):
            return []

    class _Provider:
        def __init__(self):
            self.closed = False
            self.flushed = False

        def shutdown(self):
            self.closed = True

        def force_flush(self, **kwargs):
            self.flushed = True

    runtime = pf.PersistenceRuntime(
        backend_type="disk",
        state_repository=_Repo(),
        lock_provider=object(),
        backend_details={"db_path": str(db)},
        redis_client=SimpleNamespace(aclose=lambda: asyncio.sleep(0)),
    )
    snap = await runtime.health_snapshot()
    assert snap["status"] in {"ok", "degraded"}
    await runtime.close()


@pytest.mark.asyncio
async def test_persistence_runtime_health_snapshot_degraded_paths(tmp_path):
    class _BadRepo:
        async def list_session_items(self):
            raise RuntimeError("repo unavailable")

        async def list_tombstone_items(self):
            return []

    runtime = pf.PersistenceRuntime(
        backend_type="disk",
        state_repository=_BadRepo(),
        lock_provider=object(),
        backend_details={"db_path": str(tmp_path / "missing.sqlite3")},
        redis_health_monitor=SimpleNamespace(health_snapshot=lambda: asyncio.sleep(0, result={"status": "degraded"})),
        memory_snapshot_manager=SimpleNamespace(health_snapshot=lambda: asyncio.sleep(0, result={"status": "degraded"})),
    )

    snapshot = await runtime.health_snapshot()

    assert snapshot["status"] == "degraded"
    assert snapshot["state_inventory"]["detail"] == "state_repository_unavailable"
    assert snapshot["backend_status"]["detail"] == "state_db_missing_or_invalid"


def test_persistence_factory_builders_cover_disk_memory_and_redis(monkeypatch, tmp_path):
    snapshot_repo_calls = []
    redis_repo_calls = []
    lock_calls = []
    monitor_calls = []

    class _SnapshotRepo:
        def __init__(self, **kwargs):
            snapshot_repo_calls.append(kwargs)

        def snapshot_items(self):
            state = SessionState(server_id="s1", session_id="sess", created_at=1.0, last_accessed=1.0)
            tombstone = SessionTombstone(state=state, expires_at=2.0)
            return [(("s1", "sess"), state)], [(("s1", "sess"), tombstone)]

    class _MemoryRepo:
        def __init__(self):
            self.replaced = None

        def replace_all(self, **kwargs):
            self.replaced = kwargs

    class _MemorySnapshotManager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _RedisRepo:
        def __init__(self, **kwargs):
            redis_repo_calls.append(kwargs)

    class _RedisLockProvider:
        def __init__(self, **kwargs):
            lock_calls.append(kwargs)

    class _RedisMonitor:
        def __init__(self, **kwargs):
            monitor_calls.append(kwargs)

    memory_repo = _MemoryRepo()
    monkeypatch.setattr(pf, "InMemoryStateRepository", lambda: memory_repo)
    monkeypatch.setattr(pf, "SqliteStateRepository", lambda **kwargs: _SnapshotRepo(**kwargs))
    monkeypatch.setattr(pf, "MemorySnapshotManager", lambda **kwargs: _MemorySnapshotManager(**kwargs))
    monkeypatch.setattr(pf, "RedisStateRepository", lambda **kwargs: _RedisRepo(**kwargs))
    monkeypatch.setattr(pf, "RedisLockProvider", lambda **kwargs: _RedisLockProvider(**kwargs))
    monkeypatch.setattr(pf, "RedisPersistenceHealthMonitor", lambda **kwargs: _RedisMonitor(**kwargs))
    monkeypatch.setattr(pf, "create_redis_client", lambda cfg: "redis-client")
    monkeypatch.setattr(pf, "build_keyspace", lambda key_base: f"ks:{key_base}")

    config = SimpleNamespace(
        storage=SimpleNamespace(root=str(tmp_path)),
        state_persistence=SimpleNamespace(
            refresh_on_startup=False,
            snapshot_interval_seconds=11,
            type="memory",
            disk=SimpleNamespace(local_path=None, wal=SimpleNamespace(enabled=True)),
            redis=SimpleNamespace(
                host="redis",
                port=6379,
                db=2,
                key_base="demo",
                tls_insecure=False,
                ping_seconds=9,
            ),
        ),
    )

    memory_runtime = pf._build_snapshot_enabled_memory_runtime(config)
    assert memory_runtime.backend_type == "memory"
    assert memory_runtime.memory_snapshot_manager is not None
    assert memory_repo.replaced is not None

    config.state_persistence.disk.local_path = str(tmp_path / "disk.sqlite3")
    disk_runtime = pf._build_disk_runtime(config)
    assert disk_runtime.backend_type == "disk"
    assert disk_runtime.backend_details["mode"] == "sqlite"

    redis_runtime = pf._build_redis_runtime(config)
    assert redis_runtime.backend_type == "redis"
    assert redis_runtime.redis_client == "redis-client"
    assert redis_repo_calls
    assert lock_calls
    assert monitor_calls
