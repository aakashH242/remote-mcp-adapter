"""Redis-backed state repository for shared durable metadata."""

from __future__ import annotations

import asyncio
import logging
from typing import Final

from .records import SessionState, SessionTombstone
from ..persistence.redis_support import RedisKeyspace
from .state_codec import (
    dumps_payload,
    loads_payload,
    session_state_from_payload,
    session_state_to_payload,
    tombstone_from_payload,
    tombstone_to_payload,
)
from .state_repository import SessionKey, StateRepository

logger = logging.getLogger(__name__)

_FIELD_DELIMITER: Final[str] = "\x1f"
_MAX_CAS_RETRIES: Final[int] = 5

_CAS_UPSERT_SCRIPT = """
local current = redis.call('HGET', KEYS[2], ARGV[1])
if not current then
  current = "0"
end
if tonumber(current) ~= tonumber(ARGV[2]) then
  return {0, current}
end
local next_version = tonumber(current) + 1
redis.call('HSET', KEYS[1], ARGV[1], ARGV[3])
redis.call('HSET', KEYS[2], ARGV[1], tostring(next_version))
return {1, tostring(next_version)}
"""


def _encode_field(key: SessionKey) -> str:
    """Encode a SessionKey tuple into a single Redis hash field string.

    Args:
        key: ``(server_id, session_id)`` tuple.
    """
    return f"{key[0]}{_FIELD_DELIMITER}{key[1]}"


def _decode_field(field: str) -> SessionKey:
    """Decode a Redis hash field string back to a SessionKey tuple.

    Args:
        field: Encoded hash field string.
    """
    server_id, session_id = field.split(_FIELD_DELIMITER, 1)
    return (server_id, session_id)


class RedisStateRepository(StateRepository):
    """Durable state repository backed by Redis hashes."""

    def __init__(
        self,
        *,
        redis_client,
        keyspace: RedisKeyspace,
        refresh_on_startup: bool,
    ) -> None:
        """Initialize the Redis state repository.

        Args:
            redis_client: Async Redis client instance.
            keyspace: Redis key namespace for state hashes.
            refresh_on_startup: Whether to purge existing state on first access.
        """
        self._redis = redis_client
        self._keyspace = keyspace
        self._refresh_on_startup = refresh_on_startup
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._sessions: dict[SessionKey, SessionState] = {}
        self._session_versions: dict[SessionKey, int] = {}
        self._tombstones: dict[SessionKey, SessionTombstone] = {}
        self._tombstone_versions: dict[SessionKey, int] = {}

    async def _ensure_initialized(self) -> None:
        """Lazy-load state from Redis on first repository access."""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            if self._refresh_on_startup:
                await self._redis.delete(
                    self._keyspace.sessions_hash,
                    self._keyspace.session_versions_hash,
                    self._keyspace.tombstones_hash,
                    self._keyspace.tombstone_versions_hash,
                )
            await self._hydrate_cache()
            self._initialized = True
            logger.info(
                "Initialized Redis state repository",
                extra={
                    "sessions_loaded": len(self._sessions),
                    "tombstones_loaded": len(self._tombstones),
                },
            )

    async def _hydrate_cache(self) -> None:
        """Read all sessions and tombstones from Redis into local dicts."""
        sessions_payloads = await self._redis.hgetall(self._keyspace.sessions_hash)
        session_versions_raw = await self._redis.hgetall(self._keyspace.session_versions_hash)
        tombstone_payloads = await self._redis.hgetall(self._keyspace.tombstones_hash)
        tombstone_versions_raw = await self._redis.hgetall(self._keyspace.tombstone_versions_hash)

        sessions: dict[SessionKey, SessionState] = {}
        session_versions: dict[SessionKey, int] = {}
        for field, payload_json in sessions_payloads.items():
            try:
                key = _decode_field(str(field))
                payload = loads_payload(str(payload_json))
                sessions[key] = session_state_from_payload(payload)
            except Exception:
                logger.exception(
                    "Failed to decode Redis session payload; dropping entry",
                    extra={"field": str(field)},
                )

        for field, raw_version in session_versions_raw.items():
            try:
                key = _decode_field(str(field))
                session_versions[key] = int(raw_version)
            except Exception:
                logger.exception(
                    "Failed to decode Redis session version; dropping entry",
                    extra={"field": str(field)},
                )

        tombstones: dict[SessionKey, SessionTombstone] = {}
        tombstone_versions: dict[SessionKey, int] = {}
        for field, payload_json in tombstone_payloads.items():
            try:
                key = _decode_field(str(field))
                payload = loads_payload(str(payload_json))
                tombstones[key] = tombstone_from_payload(payload)
            except Exception:
                logger.exception(
                    "Failed to decode Redis tombstone payload; dropping entry",
                    extra={"field": str(field)},
                )

        for field, raw_version in tombstone_versions_raw.items():
            try:
                key = _decode_field(str(field))
                tombstone_versions[key] = int(raw_version)
            except Exception:
                logger.exception(
                    "Failed to decode Redis tombstone version; dropping entry",
                    extra={"field": str(field)},
                )

        self._sessions = sessions
        self._session_versions = session_versions
        self._tombstones = tombstones
        self._tombstone_versions = tombstone_versions

    async def _cas_upsert(
        self,
        *,
        payload_hash: str,
        version_hash: str,
        key: SessionKey,
        payload_json: str,
        version_map: dict[SessionKey, int],
    ) -> int:
        """Attempt a version-gated CAS upsert, retrying on concurrent writes.

        Args:
            payload_hash: Redis hash key that stores encoded payloads.
            version_hash: Redis hash key that stores version counters.
            key: Session key identifying the entry to upsert.
            payload_json: Encoded JSON string to store.
            version_map: Local version-tracking dict updated on success.

        Returns:
            The new version number after a successful write.

        Raises:
            RuntimeError: When the maximum retry count is exceeded.
        """
        field = _encode_field(key)
        expected_version = version_map.get(key, 0)
        last_seen_version = expected_version

        for _ in range(_MAX_CAS_RETRIES):
            result = await self._redis.eval(
                _CAS_UPSERT_SCRIPT,
                2,
                payload_hash,
                version_hash,
                field,
                str(expected_version),
                payload_json,
            )
            ok = int(result[0])
            observed_version = int(result[1])
            if ok == 1:
                version_map[key] = observed_version
                return observed_version
            expected_version = observed_version
            last_seen_version = observed_version

        raise RuntimeError(
            "Redis CAS upsert failed after retries "
            f"(field={field}, expected_version={expected_version}, observed_version={last_seen_version})"
        )

    async def _delete_entry(self, *, payload_hash: str, version_hash: str, key: SessionKey) -> None:
        """Atomically delete both payload and version fields for one key.

        Args:
            payload_hash: Redis hash key storing payloads.
            version_hash: Redis hash key storing version counters.
            key: Session key to delete.
        """
        field = _encode_field(key)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hdel(payload_hash, field)
            pipe.hdel(version_hash, field)
            await pipe.execute()

    async def get_session(self, key: SessionKey) -> SessionState | None:
        """Ensure cache is warm, then return session from local dict.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        await self._ensure_initialized()
        return self._sessions.get(key)

    async def set_session(self, key: SessionKey, state: SessionState) -> None:
        """CAS-upsert session to Redis and update local cache.

        Args:
            key: ``(server_id, session_id)`` tuple.
            state: Session state to persist.
        """
        await self._ensure_initialized()
        payload_json = dumps_payload(session_state_to_payload(state))
        await self._cas_upsert(
            payload_hash=self._keyspace.sessions_hash,
            version_hash=self._keyspace.session_versions_hash,
            key=key,
            payload_json=payload_json,
            version_map=self._session_versions,
        )
        self._sessions[key] = state

    async def pop_session(self, key: SessionKey) -> SessionState | None:
        """Delete session from Redis and local cache; return previous value.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        await self._ensure_initialized()
        state = self._sessions.get(key)
        if state is None:
            return None
        await self._delete_entry(
            payload_hash=self._keyspace.sessions_hash,
            version_hash=self._keyspace.session_versions_hash,
            key=key,
        )
        self._sessions.pop(key, None)
        self._session_versions.pop(key, None)
        return state

    async def session_count(self) -> int:
        """Ensure cache is warm, then return local session dict length."""
        await self._ensure_initialized()
        return len(self._sessions)

    async def list_session_items(self) -> list[tuple[SessionKey, SessionState]]:
        """Ensure cache is warm, then return a list snapshot of all sessions."""
        await self._ensure_initialized()
        return list(self._sessions.items())

    async def get_tombstone(self, key: SessionKey) -> SessionTombstone | None:
        """Ensure cache is warm, then return tombstone from local dict.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        await self._ensure_initialized()
        return self._tombstones.get(key)

    async def set_tombstone(self, key: SessionKey, tombstone: SessionTombstone) -> None:
        """CAS-upsert tombstone to Redis and update local cache.

        Args:
            key: ``(server_id, session_id)`` tuple.
            tombstone: Tombstone record to persist.
        """
        await self._ensure_initialized()
        payload_json = dumps_payload(tombstone_to_payload(tombstone))
        await self._cas_upsert(
            payload_hash=self._keyspace.tombstones_hash,
            version_hash=self._keyspace.tombstone_versions_hash,
            key=key,
            payload_json=payload_json,
            version_map=self._tombstone_versions,
        )
        self._tombstones[key] = tombstone

    async def pop_tombstone(self, key: SessionKey) -> SessionTombstone | None:
        """Delete tombstone from Redis and local cache; return previous value.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        await self._ensure_initialized()
        tombstone = self._tombstones.get(key)
        if tombstone is None:
            return None
        await self._delete_entry(
            payload_hash=self._keyspace.tombstones_hash,
            version_hash=self._keyspace.tombstone_versions_hash,
            key=key,
        )
        self._tombstones.pop(key, None)
        self._tombstone_versions.pop(key, None)
        return tombstone

    async def list_tombstone_items(self) -> list[tuple[SessionKey, SessionTombstone]]:
        """Ensure cache is warm, then return a list snapshot of all tombstones."""
        await self._ensure_initialized()
        return list(self._tombstones.items())

    async def drain(self) -> tuple[list[SessionState], list[SessionTombstone]]:
        """Return all values, delete all Redis keys, then clear local caches."""
        await self._ensure_initialized()
        session_states = list(self._sessions.values())
        tombstones = list(self._tombstones.values())
        await self._redis.delete(
            self._keyspace.sessions_hash,
            self._keyspace.session_versions_hash,
            self._keyspace.tombstones_hash,
            self._keyspace.tombstone_versions_hash,
        )
        self._sessions.clear()
        self._session_versions.clear()
        self._tombstones.clear()
        self._tombstone_versions.clear()
        return session_states, tombstones
