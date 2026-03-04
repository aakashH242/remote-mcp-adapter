"""Active health monitoring for Redis persistence backend."""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class RedisPersistenceHealthMonitor:
    """Periodic Redis ping monitor for health endpoint metadata."""

    def __init__(
        self,
        *,
        redis_client,
        ping_interval_seconds: int,
    ) -> None:
        """Initialize the Redis persistence health monitor.

        Args:
            redis_client: Async Redis client instance.
            ping_interval_seconds: Seconds between health check pings.
        """
        self._redis = redis_client
        self._ping_interval_seconds = max(int(ping_interval_seconds), 1)
        self._ping_timeout_seconds = max(int(ping_interval_seconds), 1)
        self._lock = asyncio.Lock()
        self._last_latency_ms: float | None = None
        self._last_error: str | None = None
        self._last_success_at_epoch: float | None = None
        self._last_failure_at_epoch: float | None = None

    @property
    def interval_seconds(self) -> int:
        """Redis health check interval in seconds."""
        return self._ping_interval_seconds

    async def run_once(self) -> None:
        """Run one Redis ping check and update health state."""
        start = time.perf_counter()
        try:
            await asyncio.wait_for(self._redis.ping(), timeout=self._ping_timeout_seconds)
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            async with self._lock:
                self._last_latency_ms = latency_ms
                self._last_error = str(exc)
                self._last_failure_at_epoch = time.time()
            logger.warning(
                "Redis persistence ping failed",
                extra={"latency_ms": latency_ms, "error": str(exc)},
            )
            return

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        async with self._lock:
            self._last_latency_ms = latency_ms
            self._last_error = None
            self._last_success_at_epoch = time.time()

    async def run_loop(self) -> None:
        """Run periodic Redis health checks until cancelled."""
        while True:
            await self.run_once()
            await asyncio.sleep(self._ping_interval_seconds)

    async def health_snapshot(self) -> dict[str, object]:
        """Build health payload for Redis persistence.

        Returns:
            Dict including status, ping interval, latency, and error info.
        """
        async with self._lock:
            status = "ok" if self._last_error is None else "degraded"
            payload: dict[str, object] = {
                "type": "redis",
                "status": status,
                "ping": {
                    "interval_seconds": self._ping_interval_seconds,
                    "timeout_seconds": self._ping_timeout_seconds,
                    "last_latency_ms": self._last_latency_ms,
                    "last_error": self._last_error,
                    "last_success_at": self._last_success_at_epoch,
                    "last_failure_at": self._last_failure_at_epoch,
                },
            }
            if status != "ok":
                payload["detail"] = "redis_unhealthy"
            return payload
