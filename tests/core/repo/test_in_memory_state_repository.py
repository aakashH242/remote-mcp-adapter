from __future__ import annotations

import pytest

from remote_mcp_adapter.core.repo.records import SessionState, SessionTombstone
from remote_mcp_adapter.core.repo.state_repository import InMemoryStateRepository


def _session(session_id: str) -> SessionState:
	return SessionState(server_id="server", session_id=session_id, created_at=1.0, last_accessed=2.0)


@pytest.mark.asyncio
async def test_in_memory_state_repository_crud_and_replace_all() -> None:
	repository = InMemoryStateRepository()
	key_one = ("server", "one")
	key_two = ("server", "two")
	session_one = _session("one")
	session_two = _session("two")
	tombstone = SessionTombstone(state=session_two, expires_at=50.0)

	await repository.set_session(key_one, session_one)
	await repository.set_tombstone(key_two, tombstone)

	assert await repository.get_session(key_one) is session_one
	assert await repository.session_count() == 1
	assert await repository.list_session_items() == [(key_one, session_one)]
	assert await repository.get_tombstone(key_two) is tombstone
	assert await repository.list_tombstone_items() == [(key_two, tombstone)]
	assert await repository.pop_session(("server", "missing")) is None
	assert await repository.pop_tombstone(("server", "missing")) is None

	drained_sessions, drained_tombstones = await repository.drain()
	assert drained_sessions == [session_one]
	assert drained_tombstones == [tombstone]
	assert await repository.session_count() == 0

	repository.replace_all(
		sessions=[(key_two, session_two)],
		tombstones=[(key_one, SessionTombstone(state=session_one, expires_at=60.0))],
	)

	assert await repository.get_session(key_two) is session_two
	assert (await repository.pop_session(key_two)) is session_two
	assert (await repository.pop_tombstone(key_one)).expires_at == 60.0
