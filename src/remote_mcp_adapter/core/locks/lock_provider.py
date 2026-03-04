"""Lock provider interfaces and in-memory adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol


class LockProvider(Protocol):
    """Lock boundary for cross-module state mutation coordination."""

    def hold(self, lock_name: str) -> AbstractAsyncContextManager[None]:
        """Return an async context manager for one named lock.

        Args:
            lock_name: Logical name identifying the lock.
        """


class InMemoryLockProvider:
    """In-process named async lock provider."""

    def __init__(self):
        """Initialize with an empty named-lock registry and guard lock."""
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _get_lock(self, lock_name: str) -> asyncio.Lock:
        """Lazily create and return the asyncio.Lock for lock_name.

        Args:
            lock_name: Logical name identifying the lock.

        Returns:
            The ``asyncio.Lock`` associated with *lock_name*.
        """
        async with self._guard:
            lock = self._locks.get(lock_name)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[lock_name] = lock
            return lock

    @asynccontextmanager
    async def hold(self, lock_name: str) -> AsyncIterator[None]:
        """Acquire the named asyncio lock for the duration of the context.

        Args:
            lock_name: Logical name identifying the lock.
        """
        lock = await self._get_lock(lock_name)
        async with lock:
            yield
