from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from fastmcp.exceptions import ToolError

from remote_mcp_adapter.core.locks import redis_lock_provider as redis_locks
from remote_mcp_adapter.core.persistence.redis_support import build_keyspace


@dataclass
class _EvalCall:
	script: str
	key: str
	token: str
	ttl_ms: str | None = None


class _FakeRedis:
	def __init__(self) -> None:
		self.values: dict[str, str] = {}
		self.counters: dict[str, int] = {}
		self.eval_calls: list[_EvalCall] = []
		self.fail_renew = False
		self.raise_renew = False
		self.raise_release = False

	async def set(self, key: str, value: str, *, nx: bool, px: int):
		if nx and key in self.values:
			return None
		self.values[key] = value
		return True

	async def eval(self, script: str, numkeys: int, key: str, token: str, ttl_ms: str | None = None):
		assert numkeys == 1
		self.eval_calls.append(_EvalCall(script=script, key=key, token=token, ttl_ms=ttl_ms))
		if script == redis_locks._RENEW_IF_OWNED_SCRIPT:
			if self.raise_renew:
				raise RuntimeError("renew failed")
			return 0 if self.fail_renew or self.values.get(key) != token else 1
		if self.raise_release:
			raise RuntimeError("release failed")
		if self.values.get(key) == token:
			self.values.pop(key, None)
			return 1
		return 0

	async def incr(self, key: str) -> int:
		self.counters[key] = self.counters.get(key, 0) + 1
		return self.counters[key]


@pytest.mark.asyncio
async def test_lock_provider_key_helpers_and_direct_lock_operations() -> None:
	redis_client = _FakeRedis()
	provider = redis_locks.RedisLockProvider(redis_client=redis_client, keyspace=build_keyspace("adapter"))

	lock_key = provider._lock_key("alpha")
	fence_key = provider._fence_key("alpha")

	assert lock_key.endswith(":locks:alpha")
	assert fence_key.endswith(":locks:fence:alpha")
	assert await provider._acquire_lock(lock_key, "token") is True
	assert await provider._renew_lock(lock_key, "token") is True
	await provider._release_lock(lock_key, "token")
	assert lock_key not in redis_client.values


@pytest.mark.asyncio
async def test_hold_acquires_releases_and_timeout_path(monkeypatch) -> None:
	redis_client = _FakeRedis()
	provider = redis_locks.RedisLockProvider(
		redis_client=redis_client,
		keyspace=build_keyspace("adapter"),
		lock_ttl_seconds=3.0,
		acquire_timeout_seconds=1.0,
	)

	async with provider.hold("alpha"):
		assert provider._lock_key("alpha") in redis_client.values
		assert redis_client.counters[provider._fence_key("alpha")] == 1
	assert provider._lock_key("alpha") not in redis_client.values

	monotonic_values = iter([0.0, 0.4, 1.5])
	monkeypatch.setattr(redis_locks.time, "monotonic", lambda: next(monotonic_values, 1.5))

	async def never_acquire(lock_key: str, token: str) -> bool:
		return False

	async def no_sleep(seconds: float) -> None:
		return None

	monkeypatch.setattr(provider, "_acquire_lock", never_acquire)
	monkeypatch.setattr(redis_locks.asyncio, "sleep", no_sleep)
	with pytest.raises(ToolError, match="Timed out waiting for redis lock"):
		async with provider.hold("beta"):
			pass


@pytest.mark.asyncio
async def test_lease_renewer_stop_lost_lock_and_exception_paths(monkeypatch) -> None:
	redis_client = _FakeRedis()
	provider = redis_locks.RedisLockProvider(redis_client=redis_client, keyspace=build_keyspace("adapter"))
	lock_key = provider._lock_key("alpha")
	redis_client.values[lock_key] = "token"

	stop_event = asyncio.Event()
	waiter = asyncio.create_task(provider._run_lease_renewer(lock_key=lock_key, token="token", stop_event=stop_event))
	stop_event.set()
	await waiter

	async def force_timeout(awaitable, timeout: float):
		awaitable.close()
		raise asyncio.TimeoutError()

	warnings: list[str] = []
	monkeypatch.setattr(redis_locks.asyncio, "wait_for", force_timeout)
	monkeypatch.setattr(redis_locks.logger, "warning", lambda message, extra=None: warnings.append(message))
	redis_client.fail_renew = True
	await provider._run_lease_renewer(lock_key=lock_key, token="token", stop_event=asyncio.Event())
	assert warnings == ["Redis lock lease renewal failed; lock likely lost"]

	exceptions: list[str] = []
	monkeypatch.setattr(redis_locks.logger, "exception", lambda message, extra=None: exceptions.append(message))
	redis_client.fail_renew = False
	redis_client.raise_renew = True
	await provider._run_lease_renewer(lock_key=lock_key, token="token", stop_event=asyncio.Event())
	assert exceptions == ["Redis lock renewal raised unexpectedly"]


@pytest.mark.asyncio
async def test_hold_logs_release_failures(monkeypatch) -> None:
	redis_client = _FakeRedis()
	provider = redis_locks.RedisLockProvider(redis_client=redis_client, keyspace=build_keyspace("adapter"))
	redis_client.raise_release = True
	seen: list[str] = []
	monkeypatch.setattr(redis_locks.logger, "exception", lambda message, extra=None: seen.append(message))

	async with provider.hold("alpha"):
		assert provider._lock_key("alpha") in redis_client.values

	assert seen == ["Redis lock release failed"]
