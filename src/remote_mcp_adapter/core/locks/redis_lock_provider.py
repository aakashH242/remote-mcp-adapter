"""Redis-backed distributed lock provider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
import time
from uuid import uuid4

from fastmcp.exceptions import ToolError

from .lock_provider import LockProvider
from ..persistence.redis_support import RedisKeyspace

logger = logging.getLogger(__name__)

_LOCK_ACQUIRE_RETRY_SECONDS = 0.05
_DEFAULT_LOCK_TTL_SECONDS = 30.0
_DEFAULT_LOCK_ACQUIRE_TIMEOUT_SECONDS = 10.0

_RELEASE_IF_OWNED_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

_RENEW_IF_OWNED_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


class RedisLockProvider(LockProvider):
    """Distributed lock provider with token-safe release and lock fencing."""

    def __init__(
        self,
        *,
        redis_client,
        keyspace: RedisKeyspace,
        lock_ttl_seconds: float = _DEFAULT_LOCK_TTL_SECONDS,
        acquire_timeout_seconds: float = _DEFAULT_LOCK_ACQUIRE_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the Redis lock provider.

        Args:
            redis_client: Async Redis client instance.
            keyspace: Redis key namespace configuration.
            lock_ttl_seconds: Lock expiration time in seconds.
            acquire_timeout_seconds: Maximum wait time to acquire a lock.
        """
        self._redis = redis_client
        self._keyspace = keyspace
        self._lock_ttl_ms = int(max(lock_ttl_seconds, 1.0) * 1000)
        self._acquire_timeout_seconds = max(acquire_timeout_seconds, 1.0)
        self._renew_interval_seconds = max(lock_ttl_seconds / 3.0, 1.0)

    def _lock_key(self, lock_name: str) -> str:
        """Build the Redis key that holds the lock token.

        Args:
            lock_name: Logical lock name.

        Returns:
            Namespaced Redis key string.
        """
        return f"{self._keyspace.lock_prefix}:{lock_name}"

    def _fence_key(self, lock_name: str) -> str:
        """Build the Redis key for the monotonic fence counter.

        Args:
            lock_name: Logical lock name.

        Returns:
            Namespaced Redis key string.
        """
        return f"{self._keyspace.lock_fence_prefix}:{lock_name}"

    async def _acquire_lock(self, lock_key: str, token: str) -> bool:
        """Attempt a single NX SET to claim the lock; return True on success.

        Args:
            lock_key: Redis key for the lock.
            token: Unique token identifying this lock holder.

        Returns:
            True if the lock was successfully acquired.
        """
        acquired = await self._redis.set(lock_key, token, nx=True, px=self._lock_ttl_ms)
        return bool(acquired)

    async def _renew_lock(self, lock_key: str, token: str) -> bool:
        """Extend TTL atomically only when the lock is still owned by our token.

        Args:
            lock_key: Redis key for the lock.
            token: Unique token identifying this lock holder.

        Returns:
            True if the TTL was successfully extended.
        """
        renewed = await self._redis.eval(_RENEW_IF_OWNED_SCRIPT, 1, lock_key, token, str(self._lock_ttl_ms))
        return bool(renewed)

    async def _release_lock(self, lock_key: str, token: str) -> None:
        """Delete the lock key only when our token still owns it.

        Args:
            lock_key: Redis key for the lock.
            token: Unique token identifying this lock holder.
        """
        await self._redis.eval(_RELEASE_IF_OWNED_SCRIPT, 1, lock_key, token)

    async def _run_lease_renewer(self, *, lock_key: str, token: str, stop_event: asyncio.Event) -> None:
        """Periodically renew the lock TTL until stop_event fires or renewal fails.

        Args:
            lock_key: Redis key for the lock.
            token: Unique token identifying this lock holder.
            stop_event: Event signalling the renewer should exit.
        """
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._renew_interval_seconds)
                return
            except asyncio.TimeoutError:
                pass

            try:
                renewed = await self._renew_lock(lock_key, token)
                if not renewed:
                    logger.warning(
                        "Redis lock lease renewal failed; lock likely lost",
                        extra={"lock_key": lock_key},
                    )
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Redis lock renewal raised unexpectedly",
                    extra={"lock_key": lock_key},
                )
                return

    @asynccontextmanager
    async def hold(self, lock_name: str) -> AsyncIterator[None]:
        """Acquire the distributed Redis lock and release it on context exit.

        Args:
            lock_name: Logical lock name.

        Raises:
            ToolError: If the lock cannot be acquired within the configured
                timeout.
        """
        lock_key = self._lock_key(lock_name)
        token = uuid4().hex
        deadline = time.monotonic() + self._acquire_timeout_seconds

        while True:
            if await self._acquire_lock(lock_key, token):
                break
            if time.monotonic() >= deadline:
                raise ToolError(f"Timed out waiting for redis lock: {lock_name}")
            await asyncio.sleep(_LOCK_ACQUIRE_RETRY_SECONDS)

        fence_token = await self._redis.incr(self._fence_key(lock_name))
        logger.debug(
            "Redis lock acquired",
            extra={
                "lock_name": lock_name,
                "lock_key": lock_key,
                "fence_token": int(fence_token),
            },
        )

        renew_stop = asyncio.Event()
        renew_task = asyncio.create_task(
            self._run_lease_renewer(lock_key=lock_key, token=token, stop_event=renew_stop),
            name=f"redis-lock-renewer:{lock_name}",
        )
        try:
            yield
        finally:
            renew_stop.set()
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass
            try:
                await self._release_lock(lock_key, token)
            except Exception:
                logger.exception(
                    "Redis lock release failed",
                    extra={"lock_name": lock_name, "lock_key": lock_key},
                )
