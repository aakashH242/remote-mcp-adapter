from __future__ import annotations

import asyncio

import pytest

from remote_mcp_adapter.core.persistence.redis_persistence_health import RedisPersistenceHealthMonitor


class _Redis:
    def __init__(self, fail=False):
        self.fail = fail

    async def ping(self):
        if self.fail:
            raise RuntimeError("down")
        return True


@pytest.mark.asyncio
async def test_health_monitor_paths(monkeypatch):
    monitor = RedisPersistenceHealthMonitor(redis_client=_Redis(fail=False), ping_interval_seconds=0)
    assert monitor.interval_seconds == 1

    await monitor.run_once()
    snap = await monitor.health_snapshot()
    assert snap["status"] == "ok"

    monitor2 = RedisPersistenceHealthMonitor(redis_client=_Redis(fail=True), ping_interval_seconds=1)
    await monitor2.run_once()
    snap2 = await monitor2.health_snapshot()
    assert snap2["status"] == "degraded"
    assert snap2["detail"] == "redis_unhealthy"


@pytest.mark.asyncio
async def test_run_loop_cancel(monkeypatch):
    monitor = RedisPersistenceHealthMonitor(redis_client=_Redis(fail=False), ping_interval_seconds=1)

    async def cancel_sleep(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", cancel_sleep)
    with pytest.raises(asyncio.CancelledError):
        await monitor.run_loop()
