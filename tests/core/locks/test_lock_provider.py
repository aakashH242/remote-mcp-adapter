from __future__ import annotations

import asyncio

import pytest

from remote_mcp_adapter.core.locks.lock_provider import InMemoryLockProvider


@pytest.mark.asyncio
async def test_in_memory_lock_provider_reuses_named_lock_and_serializes_access() -> None:
	provider = InMemoryLockProvider()

	first_lock = await provider._get_lock("alpha")
	second_lock = await provider._get_lock("alpha")
	third_lock = await provider._get_lock("beta")

	assert first_lock is second_lock
	assert first_lock is not third_lock

	events: list[str] = []

	async def first_holder() -> None:
		async with provider.hold("alpha"):
			events.append("first-enter")
			await asyncio.sleep(0)
			events.append("first-exit")

	async def second_holder() -> None:
		await asyncio.sleep(0)
		async with provider.hold("alpha"):
			events.append("second-enter")

	await asyncio.gather(first_holder(), second_holder())
	assert events == ["first-enter", "first-exit", "second-enter"]
