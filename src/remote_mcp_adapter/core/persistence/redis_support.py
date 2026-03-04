"""Redis client helpers with optional dependency loading."""

from __future__ import annotations

from dataclasses import dataclass
import ssl
from typing import Any

from ...config import StatePersistenceRedisConfig


class RedisDependencyError(RuntimeError):
    """Raised when Redis mode is configured but dependency is unavailable."""


def _import_redis_asyncio() -> Any:
    """Import and return the ``redis.asyncio`` module, raising on missing dependency."""
    try:
        from redis import asyncio as redis_asyncio  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in runtime environments
        raise RedisDependencyError(
            "Redis persistence requires the 'redis' package. " "Install with: pip install redis>=5.0.0"
        ) from exc
    return redis_asyncio


def _build_tls_kwargs(config: StatePersistenceRedisConfig) -> dict[str, Any]:
    """Return TLS kwargs for insecure connections; empty dict otherwise.

    Args:
        config: Redis persistence configuration.
    """
    if not config.tls_insecure:
        return {}
    return {
        "ssl": True,
        "ssl_cert_reqs": ssl.CERT_NONE,
    }


def create_redis_client(config: StatePersistenceRedisConfig) -> Any:
    """Create an async Redis client with response decoding enabled.

    Args:
        config: Redis persistence configuration.
    """
    redis_asyncio = _import_redis_asyncio()
    return redis_asyncio.Redis(
        host=config.host,
        port=config.port,
        db=config.db,
        username=config.username,
        password=config.password,
        decode_responses=True,
        **_build_tls_kwargs(config),
    )


@dataclass(slots=True, frozen=True)
class RedisKeyspace:
    """Resolved Redis keyspace for persistence and locking."""

    sessions_hash: str
    session_versions_hash: str
    tombstones_hash: str
    tombstone_versions_hash: str
    lock_prefix: str
    lock_fence_prefix: str


def build_keyspace(key_base: str) -> RedisKeyspace:
    """Build a RedisKeyspace from the configured key base string.

    Args:
        key_base: Root key string for Redis namespacing.
    """
    prefix = key_base.strip()
    return RedisKeyspace(
        sessions_hash=f"{prefix}:state:sessions",
        session_versions_hash=f"{prefix}:state:sessions:versions",
        tombstones_hash=f"{prefix}:state:tombstones",
        tombstone_versions_hash=f"{prefix}:state:tombstones:versions",
        lock_prefix=f"{prefix}:locks",
        lock_fence_prefix=f"{prefix}:locks:fence",
    )
