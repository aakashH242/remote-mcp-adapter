"""Nonce storage backends for signed upload replay protection."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
from typing import Any, Protocol

from ..config import AdapterConfig

_SQLITE_BUSY_TIMEOUT_SECONDS = 5.0
_NONCE_TABLE = "upload_nonces"
_REDIS_NONCE_KEY_SUFFIX = "upload_nonces"
_FIELD_DELIMITER = "\x1f"

_REDIS_CONSUME_IF_MATCH_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if not value then
  return 0
end
if value ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[1])
return 1
"""


class UploadNonceStore(Protocol):
    """Persistence boundary for one-time upload nonce replay protection."""

    @property
    def backend(self) -> str:
        """Return backend label used for diagnostics."""

    async def reserve_nonce(
        self,
        *,
        nonce: str,
        server_id: str,
        session_id: str,
        expires_at: int,
        now_epoch: int,
    ) -> bool:
        """Persist a nonce when available and return True on success.

        Args:
            nonce: Unique nonce string.
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            now_epoch: Current epoch timestamp.
        """

    async def consume_nonce(
        self,
        *,
        nonce: str,
        server_id: str,
        session_id: str,
        expires_at: int,
        now_epoch: int,
    ) -> bool:
        """Atomically validate and consume one nonce.

        Args:
            nonce: Unique nonce string.
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            now_epoch: Current epoch timestamp.
        """


class InMemoryUploadNonceStore:
    """In-memory nonce store used when durable backend is unavailable."""

    def __init__(self) -> None:
        """Initialize the in-memory nonce store with an empty dict and lock."""
        self._lock = asyncio.Lock()
        self._nonces: dict[str, tuple[str, str, int]] = {}

    @property
    def backend(self) -> str:
        """Return backend label used for diagnostics."""
        return "memory"

    async def _prune_expired(self, *, now_epoch: int) -> None:
        """Remove expired nonces from the in-memory dict.

        Args:
            now_epoch: Current epoch timestamp.
        """
        if not self._nonces:
            return
        expired = [nonce for nonce, (_, _, expires_at) in self._nonces.items() if expires_at < now_epoch]
        for nonce in expired:
            self._nonces.pop(nonce, None)

    async def reserve_nonce(
        self,
        *,
        nonce: str,
        server_id: str,
        session_id: str,
        expires_at: int,
        now_epoch: int,
    ) -> bool:
        """Store one nonce in memory when not already present.

        Args:
            nonce: Unique nonce string.
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            now_epoch: Current epoch timestamp.
        """
        async with self._lock:
            await self._prune_expired(now_epoch=now_epoch)
            if nonce in self._nonces:
                return False
            self._nonces[nonce] = (server_id, session_id, expires_at)
            return True

    async def consume_nonce(
        self,
        *,
        nonce: str,
        server_id: str,
        session_id: str,
        expires_at: int,
        now_epoch: int,
    ) -> bool:
        """Atomically match and consume one in-memory nonce.

        Args:
            nonce: Unique nonce string.
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            now_epoch: Current epoch timestamp.
        """
        async with self._lock:
            await self._prune_expired(now_epoch=now_epoch)
            issued = self._nonces.get(nonce)
            if issued is None:
                return False
            issued_server_id, issued_session_id, issued_expires_at = issued
            if (
                issued_server_id != server_id
                or issued_session_id != session_id
                or issued_expires_at != expires_at
                or issued_expires_at < now_epoch
            ):
                return False
            self._nonces.pop(nonce, None)
            return True


class SqliteUploadNonceStore:
    """SQLite-backed nonce store with atomic consume semantics."""

    def __init__(self, *, db_path: Path):
        """Initialize the SQLite nonce store.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = db_path.resolve()
        self._init_lock = asyncio.Lock()
        self._initialized = False

    @property
    def backend(self) -> str:
        """Return backend label used for diagnostics."""
        return "sqlite"

    def _connect(self) -> sqlite3.Connection:
        """Open a connection to the SQLite database.

        Returns:
            Configured SQLite connection.
        """
        connection = sqlite3.connect(self._db_path, timeout=_SQLITE_BUSY_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_sync(self) -> None:
        """Create the nonce table and expiry index if they do not exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            with connection:
                connection.execute(f"""
                    CREATE TABLE IF NOT EXISTS {_NONCE_TABLE} (
                        nonce TEXT PRIMARY KEY,
                        server_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        expires_at INTEGER NOT NULL
                    )
                    """)
                connection.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{_NONCE_TABLE}_expires_at
                    ON {_NONCE_TABLE} (expires_at)
                    """)

    async def _ensure_initialized(self) -> None:
        """Lazily initialize the SQLite schema on first use."""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    def _reserve_nonce_sync(
        self,
        *,
        nonce: str,
        server_id: str,
        session_id: str,
        expires_at: int,
        now_epoch: int,
    ) -> bool:
        """Insert one nonce row synchronously if not already present.

        Args:
            nonce: Unique nonce string.
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            now_epoch: Current epoch timestamp.
        """
        with self._connect() as connection:
            with connection:
                connection.execute(f"DELETE FROM {_NONCE_TABLE} WHERE expires_at < ?", (now_epoch,))
                cursor = connection.execute(
                    f"""
                    INSERT OR IGNORE INTO {_NONCE_TABLE} (nonce, server_id, session_id, expires_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (nonce, server_id, session_id, expires_at),
                )
                return int(cursor.rowcount or 0) == 1

    def _consume_nonce_sync(
        self,
        *,
        nonce: str,
        server_id: str,
        session_id: str,
        expires_at: int,
        now_epoch: int,
    ) -> bool:
        """Atomically match and delete one nonce row synchronously.

        Args:
            nonce: Unique nonce string.
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            now_epoch: Current epoch timestamp.
        """
        with self._connect() as connection:
            with connection:
                connection.execute(f"DELETE FROM {_NONCE_TABLE} WHERE expires_at < ?", (now_epoch,))
                cursor = connection.execute(
                    f"""
                    DELETE FROM {_NONCE_TABLE}
                    WHERE nonce = ?
                      AND server_id = ?
                      AND session_id = ?
                      AND expires_at = ?
                      AND expires_at >= ?
                    """,
                    (nonce, server_id, session_id, expires_at, now_epoch),
                )
                return int(cursor.rowcount or 0) == 1

    async def reserve_nonce(
        self,
        *,
        nonce: str,
        server_id: str,
        session_id: str,
        expires_at: int,
        now_epoch: int,
    ) -> bool:
        """Persist one nonce row if it is not already present.

        Args:
            nonce: Unique nonce string.
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            now_epoch: Current epoch timestamp.
        """
        await self._ensure_initialized()
        return await asyncio.to_thread(
            self._reserve_nonce_sync,
            nonce=nonce,
            server_id=server_id,
            session_id=session_id,
            expires_at=expires_at,
            now_epoch=now_epoch,
        )

    async def consume_nonce(
        self,
        *,
        nonce: str,
        server_id: str,
        session_id: str,
        expires_at: int,
        now_epoch: int,
    ) -> bool:
        """Atomically match and consume one nonce row.

        Args:
            nonce: Unique nonce string.
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            now_epoch: Current epoch timestamp.
        """
        await self._ensure_initialized()
        return await asyncio.to_thread(
            self._consume_nonce_sync,
            nonce=nonce,
            server_id=server_id,
            session_id=session_id,
            expires_at=expires_at,
            now_epoch=now_epoch,
        )


class RedisUploadNonceStore:
    """Redis-backed nonce store with single-use consume script."""

    def __init__(self, *, redis_client: Any, key_prefix: str):
        """Initialize the Redis nonce store.

        Args:
            redis_client: Async Redis client instance.
            key_prefix: Redis key namespace prefix.
        """
        self._redis = redis_client
        self._key_prefix = key_prefix

    @property
    def backend(self) -> str:
        """Return backend label used for diagnostics."""
        return "redis"

    def _key(self, nonce: str) -> str:
        """Build the Redis key for a nonce.

        Args:
            nonce: Unique nonce string.
        """
        return f"{self._key_prefix}:{nonce}"

    def _payload(self, *, server_id: str, session_id: str, expires_at: int) -> str:
        """Build the serialized payload value for a nonce.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
        """
        return f"{server_id}{_FIELD_DELIMITER}{session_id}{_FIELD_DELIMITER}{expires_at}"

    async def reserve_nonce(
        self,
        *,
        nonce: str,
        server_id: str,
        session_id: str,
        expires_at: int,
        now_epoch: int,
    ) -> bool:
        """Reserve nonce with TTL and value-bound expectations.

        Args:
            nonce: Unique nonce string.
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            now_epoch: Current epoch timestamp.
        """
        ttl_seconds = max(1, expires_at - now_epoch)
        stored = await self._redis.set(
            self._key(nonce),
            self._payload(server_id=server_id, session_id=session_id, expires_at=expires_at),
            ex=ttl_seconds,
            nx=True,
        )
        return bool(stored)

    async def consume_nonce(
        self,
        *,
        nonce: str,
        server_id: str,
        session_id: str,
        expires_at: int,
        now_epoch: int,
    ) -> bool:
        """Atomically compare and delete nonce in one Redis script.

        Args:
            nonce: Unique nonce string.
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            now_epoch: Current epoch timestamp.
        """
        if expires_at < now_epoch:
            return False
        matched = await self._redis.eval(
            _REDIS_CONSUME_IF_MATCH_SCRIPT,
            1,
            self._key(nonce),
            self._payload(server_id=server_id, session_id=session_id, expires_at=expires_at),
        )
        return int(matched) == 1


def _runtime_backend_details(runtime: object) -> dict[str, object]:
    """Extract backend details dict from the persistence runtime.

    Args:
        runtime: Persistence runtime object.
    """
    details = getattr(runtime, "backend_details", {})
    return details if isinstance(details, dict) else {}


def _resolve_sqlite_nonce_db_path(*, config: AdapterConfig, runtime: object) -> Path | None:
    """Resolve the SQLite database path from runtime details or config.

    Args:
        config: Full adapter configuration.
        runtime: Persistence runtime object.
    """
    details = _runtime_backend_details(runtime)
    for key in ("db_path", "snapshot_db_path"):
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value.strip())
    local_path = config.state_persistence.disk.local_path
    if local_path and local_path.strip():
        return Path(local_path.strip())
    return None


def build_upload_nonce_store(*, config: AdapterConfig, runtime: object) -> UploadNonceStore:
    """Select nonce store from active persistence runtime and config.

    Args:
        config: Full adapter configuration.
        runtime: Persistence runtime object.
    """
    backend_type = str(getattr(runtime, "backend_type", "memory"))
    if backend_type == "redis":
        redis_client = getattr(runtime, "redis_client", None)
        if redis_client is not None:
            details = _runtime_backend_details(runtime)
            key_base = details.get("key_base")
            if not isinstance(key_base, str) or not key_base.strip():
                key_base = config.state_persistence.redis.key_base
            return RedisUploadNonceStore(
                redis_client=redis_client,
                key_prefix=f"{str(key_base).strip()}:{_REDIS_NONCE_KEY_SUFFIX}",
            )

    sqlite_path = _resolve_sqlite_nonce_db_path(config=config, runtime=runtime)
    if sqlite_path is not None:
        return SqliteUploadNonceStore(db_path=sqlite_path)
    return InMemoryUploadNonceStore()
