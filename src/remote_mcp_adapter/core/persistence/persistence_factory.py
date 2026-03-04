"""Factory helpers for persistence runtime components."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ...config import AdapterConfig
from ..locks.lock_provider import InMemoryLockProvider, LockProvider
from .memory_snapshot import MemorySnapshotManager
from ..locks.redis_lock_provider import RedisLockProvider
from .redis_persistence_health import RedisPersistenceHealthMonitor
from ..repo.redis_state_repository import RedisStateRepository
from .redis_support import build_keyspace, create_redis_client
from ..repo.sqlite_state_repository import SqliteStateRepository
from ..repo.state_repository import InMemoryStateRepository, StateRepository

PersistenceBackendType = Literal["memory", "disk", "redis"]


def _filesystem_backend_status(path: Path) -> dict[str, object]:
    """Build filesystem-backed status metadata for persistence health.

    Args:
        path: Filesystem path to the state database file.

    Returns:
        Status dict including ``status``, ``path``, ``exists``, and ``size_bytes``.
    """
    try:
        exists = path.exists()
        is_file = path.is_file() if exists else False
        size_bytes: int | None = None
        if is_file:
            size_bytes = int(path.stat().st_size)
        status = "ok" if exists and is_file else "degraded"
        payload: dict[str, object] = {
            "status": status,
            "path": str(path),
            "exists": exists,
            "is_file": is_file,
            "size_bytes": size_bytes,
            "parent_exists": path.parent.exists(),
        }
        if status != "ok":
            payload["detail"] = "state_db_missing_or_invalid"
        return payload
    except OSError as exc:
        return {
            "status": "degraded",
            "path": str(path),
            "detail": "state_db_stat_failed",
            "error": str(exc),
        }


@dataclass(slots=True)
class PersistenceRuntime:
    """Resolved persistence runtime stack for app startup."""

    backend_type: PersistenceBackendType
    state_repository: StateRepository
    lock_provider: LockProvider
    backend_details: dict[str, object]
    redis_health_monitor: RedisPersistenceHealthMonitor | None = None
    memory_snapshot_manager: MemorySnapshotManager | None = None
    redis_client: object | None = None

    async def close(self) -> None:
        """Close the Redis client connection when present.

        No-op if no Redis client was configured for this runtime.
        """
        if self.redis_client is None:
            return
        close = getattr(self.redis_client, "aclose", None)
        if callable(close):
            await close()

    async def _state_inventory_snapshot(self) -> dict[str, object]:
        """Count sessions, tombstones, uploads, and in-flight requests for health output.

        Returns:
            Dict with session/tombstone/upload/artifact/in-flight counts or
            a degraded status dict on failure.
        """
        try:
            session_items = list(await self.state_repository.list_session_items())
            tombstone_items = list(await self.state_repository.list_tombstone_items())
        except Exception as exc:
            return {
                "status": "degraded",
                "detail": "state_repository_unavailable",
                "error": str(exc),
            }

        total_uploads = 0
        total_artifacts = 0
        total_in_flight = 0
        for _, session_state in session_items:
            total_uploads += len(session_state.uploads)
            total_artifacts += len(session_state.artifacts)
            total_in_flight += session_state.in_flight
        return {
            "status": "ok",
            "sessions_count": len(session_items),
            "tombstones_count": len(tombstone_items),
            "uploads_count": total_uploads,
            "artifacts_count": total_artifacts,
            "in_flight_count": total_in_flight,
        }

    async def _backend_status_snapshot(self) -> dict[str, object]:
        """Check filesystem presence/size for disk-backed backends.

        Returns:
            Status dict from ``_filesystem_backend_status`` or ``{"status": "ok"}``.
        """
        db_path_value = self.backend_details.get("db_path") or self.backend_details.get("snapshot_db_path")
        if isinstance(db_path_value, str) and db_path_value:
            return await asyncio.to_thread(_filesystem_backend_status, Path(db_path_value))
        return {"status": "ok"}

    async def health_snapshot(self) -> dict[str, object]:
        """Build composite health payload for the ``/healthz`` persistence section.

        Returns:
            Dict including backend type, inventory, status, and redis/snapshot
            sub-sections when applicable.
        """
        payload = {
            "type": self.backend_type,
            "status": "ok",
            "backend_details": dict(self.backend_details),
        }
        state_inventory = await self._state_inventory_snapshot()
        payload["state_inventory"] = state_inventory
        if state_inventory.get("status") != "ok":
            payload["status"] = "degraded"

        backend_status = await self._backend_status_snapshot()
        payload["backend_status"] = backend_status
        if backend_status.get("status") != "ok":
            payload["status"] = "degraded"

        if self.redis_health_monitor is not None:
            redis_snapshot = await self.redis_health_monitor.health_snapshot()
            payload.update(redis_snapshot)
            payload["redis"] = redis_snapshot
            if redis_snapshot.get("status") != "ok":
                payload["status"] = "degraded"
        if self.memory_snapshot_manager is not None:
            snapshot = await self.memory_snapshot_manager.health_snapshot()
            payload["memory_snapshot"] = snapshot
            if snapshot.get("status") != "ok":
                payload["status"] = "degraded"
        return payload


def _resolve_disk_wal_enabled(config: AdapterConfig) -> bool:
    """Return True when SQLite WAL mode is enabled for disk persistence.

    Args:
        config: Full adapter configuration.
    """
    return config.state_persistence.disk.wal.enabled


def _resolve_memory_snapshot_path(config: AdapterConfig) -> Path:
    """Return the SQLite snapshot path, falling back to a default under storage root.

    Args:
        config: Full adapter configuration.

    Returns:
        Resolved filesystem ``Path`` for the snapshot database.
    """
    local_path = config.state_persistence.disk.local_path
    if local_path:
        return Path(local_path)
    return (Path(config.storage.root) / "state" / "adapter_state_snapshot.sqlite3").resolve()


def _build_snapshot_enabled_memory_runtime(config: AdapterConfig) -> PersistenceRuntime:
    """Build an in-memory runtime that periodically snapshots state to SQLite.

    Args:
        config: Full adapter configuration.

    Returns:
        ``PersistenceRuntime`` with a ``MemorySnapshotManager`` attached.
    """
    memory_repo = InMemoryStateRepository()
    snapshot_path = _resolve_memory_snapshot_path(config)
    wal_enabled = _resolve_disk_wal_enabled(config)
    snapshot_repo = SqliteStateRepository(
        db_path=snapshot_path,
        wal_enabled=wal_enabled,
        refresh_on_startup=config.state_persistence.refresh_on_startup,
    )
    if not config.state_persistence.refresh_on_startup:
        sessions, tombstones = snapshot_repo.snapshot_items()
        memory_repo.replace_all(sessions=sessions, tombstones=tombstones)

    snapshot_manager = MemorySnapshotManager(
        source_repository=memory_repo,
        snapshot_repository=snapshot_repo,
        interval_seconds=config.state_persistence.snapshot_interval_seconds,
    )
    return PersistenceRuntime(
        backend_type="memory",
        state_repository=memory_repo,
        lock_provider=InMemoryLockProvider(),
        backend_details={
            "mode": "snapshot_backed_memory",
            "snapshot_db_path": str(snapshot_path),
            "snapshot_interval_seconds": config.state_persistence.snapshot_interval_seconds,
            "snapshot_wal_enabled": wal_enabled,
            "refresh_on_startup": config.state_persistence.refresh_on_startup,
        },
        memory_snapshot_manager=snapshot_manager,
    )


def _build_ephemeral_memory_runtime() -> PersistenceRuntime:
    """Build a pure in-memory runtime with no durable backing store."""
    return PersistenceRuntime(
        backend_type="memory",
        state_repository=InMemoryStateRepository(),
        lock_provider=InMemoryLockProvider(),
        backend_details={"mode": "ephemeral_memory_fallback"},
    )


def _build_disk_runtime(config: AdapterConfig) -> PersistenceRuntime:
    """Build a SQLite-backed runtime from the resolved disk config.

    Args:
        config: Full adapter configuration.

    Returns:
        ``PersistenceRuntime`` with a ``SqliteStateRepository`` backend.

    Raises:
        ValueError: If ``disk.local_path`` has not been resolved.
    """
    local_path = config.state_persistence.disk.local_path
    if local_path is None:
        raise ValueError("state_persistence.disk.local_path must be resolved before building repository")
    wal_enabled = _resolve_disk_wal_enabled(config)
    return PersistenceRuntime(
        backend_type="disk",
        state_repository=SqliteStateRepository(
            db_path=Path(local_path),
            wal_enabled=wal_enabled,
            refresh_on_startup=config.state_persistence.refresh_on_startup,
        ),
        lock_provider=InMemoryLockProvider(),
        backend_details={
            "mode": "sqlite",
            "db_path": str(Path(local_path)),
            "wal_enabled": wal_enabled,
            "refresh_on_startup": config.state_persistence.refresh_on_startup,
        },
    )


def _build_redis_runtime(config: AdapterConfig) -> PersistenceRuntime:
    """Build a Redis-backed runtime with distributed locking and health monitoring.

    Args:
        config: Full adapter configuration.

    Returns:
        ``PersistenceRuntime`` with Redis repository, lock provider, and health monitor.
    """
    redis_config = config.state_persistence.redis
    redis_client = create_redis_client(redis_config)
    keyspace = build_keyspace(redis_config.key_base)
    lock_provider = RedisLockProvider(redis_client=redis_client, keyspace=keyspace)
    health_monitor = RedisPersistenceHealthMonitor(
        redis_client=redis_client,
        ping_interval_seconds=redis_config.ping_seconds,
    )
    return PersistenceRuntime(
        backend_type="redis",
        state_repository=RedisStateRepository(
            redis_client=redis_client,
            keyspace=keyspace,
            refresh_on_startup=config.state_persistence.refresh_on_startup,
        ),
        lock_provider=lock_provider,
        backend_details={
            "mode": "redis",
            "host": redis_config.host,
            "port": redis_config.port,
            "db": redis_config.db,
            "key_base": redis_config.key_base,
            "tls_insecure": redis_config.tls_insecure,
            "ping_seconds": redis_config.ping_seconds,
            "refresh_on_startup": config.state_persistence.refresh_on_startup,
        },
        redis_health_monitor=health_monitor,
        redis_client=redis_client,
    )


def build_persistence_runtime(config: AdapterConfig) -> PersistenceRuntime:
    """Create persistence runtime stack from configured mode.

    Args:
        config: Full adapter configuration.

    Returns:
        ``PersistenceRuntime`` for the configured persistence type.

    Raises:
        ValueError: If the persistence type is unsupported.
    """
    persistence = config.state_persistence
    if persistence.type == "memory":
        return _build_snapshot_enabled_memory_runtime(config)
    if persistence.type == "disk":
        return _build_disk_runtime(config)
    if persistence.type == "redis":
        return _build_redis_runtime(config)
    raise ValueError(f"Unsupported state persistence type: {persistence.type}")


def build_memory_persistence_runtime() -> PersistenceRuntime:
    """Create an in-memory persistence runtime for fallback behavior."""
    return _build_ephemeral_memory_runtime()


def build_state_repository(config: AdapterConfig) -> StateRepository:
    """Backward-compatible accessor for callers expecting repository only.

    Args:
        config: Full adapter configuration.

    Returns:
        ``StateRepository`` from the built runtime.
    """
    return build_persistence_runtime(config).state_repository
