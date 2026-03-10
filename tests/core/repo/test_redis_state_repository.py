from __future__ import annotations

from dataclasses import dataclass

import pytest

from remote_mcp_adapter.core.persistence.redis_support import build_keyspace
from remote_mcp_adapter.core.repo.records import SessionState, SessionTombstone
from remote_mcp_adapter.core.repo.redis_state_repository import (
	_CAS_UPSERT_SCRIPT,
	RedisStateRepository,
	_decode_field,
	_encode_field,
)
from remote_mcp_adapter.core.repo.state_codec import (
	dumps_payload,
	session_state_to_payload,
	tombstone_to_payload,
)


@dataclass
class _PipelineCommand:
	hash_name: str
	field: str


class _FakePipeline:
	def __init__(self, redis_client: "_FakeRedis") -> None:
		self._redis = redis_client
		self._commands: list[_PipelineCommand] = []

	async def __aenter__(self) -> "_FakePipeline":
		return self

	async def __aexit__(self, exc_type, exc, tb) -> None:
		return None

	def hdel(self, hash_name: str, field: str) -> None:
		self._commands.append(_PipelineCommand(hash_name=hash_name, field=field))

	async def execute(self) -> None:
		for command in self._commands:
			await self._redis.hdel(command.hash_name, command.field)


class _FakeRedis:
	def __init__(self) -> None:
		self.hashes: dict[str, dict[str, str]] = {}
		self.deleted_keys: list[str] = []
		self.eval_results: list[list[int | str]] = []

	async def hgetall(self, hash_name: str) -> dict[str, str]:
		return dict(self.hashes.get(hash_name, {}))

	async def delete(self, *keys: str) -> None:
		self.deleted_keys.extend(keys)
		for key in keys:
			self.hashes.pop(key, None)

	async def eval(self, script: str, numkeys: int, *args: str) -> list[int | str]:
		if self.eval_results:
			return self.eval_results.pop(0)

		assert script == _CAS_UPSERT_SCRIPT
		assert numkeys == 2
		payload_hash, version_hash, field, expected_version, payload_json = args
		current_version = self.hashes.setdefault(version_hash, {}).get(field, "0")
		if int(current_version) != int(expected_version):
			return [0, current_version]
		next_version = str(int(current_version) + 1)
		self.hashes.setdefault(payload_hash, {})[field] = payload_json
		self.hashes.setdefault(version_hash, {})[field] = next_version
		return [1, next_version]

	def pipeline(self, transaction: bool = True) -> _FakePipeline:
		assert transaction is True
		return _FakePipeline(self)

	async def hdel(self, hash_name: str, field: str) -> None:
		self.hashes.setdefault(hash_name, {}).pop(field, None)


def _session_state(session_id: str = "sess") -> SessionState:
	return SessionState(server_id="server", session_id=session_id, created_at=1.0, last_accessed=2.0)


def _tombstone(session_id: str = "sess") -> SessionTombstone:
	return SessionTombstone(state=_session_state(session_id), expires_at=99.0)


@pytest.mark.asyncio
async def test_encode_decode_and_refresh_on_startup_clears_existing_state() -> None:
	key = ("server", "sess")
	assert _decode_field(_encode_field(key)) == key

	redis_client = _FakeRedis()
	keyspace = build_keyspace("adapter")
	encoded_field = _encode_field(key)
	redis_client.hashes[keyspace.sessions_hash] = {encoded_field: "stale"}

	repository = RedisStateRepository(
		redis_client=redis_client,
		keyspace=keyspace,
		refresh_on_startup=True,
	)

	assert await repository.session_count() == 0
	assert keyspace.sessions_hash in redis_client.deleted_keys
	assert await repository.get_session(key) is None


@pytest.mark.asyncio
async def test_hydrate_cache_drops_invalid_entries_and_exposes_loaded_state() -> None:
	redis_client = _FakeRedis()
	keyspace = build_keyspace("adapter")
	session_key = ("server", "sess")
	tombstone_key = ("server", "grave")

	redis_client.hashes[keyspace.sessions_hash] = {
		_encode_field(session_key): dumps_payload(session_state_to_payload(_session_state("sess"))),
		"broken-field": dumps_payload(session_state_to_payload(_session_state("ignore"))),
		_encode_field(("server", "bad-payload")): "{not-json",
	}
	redis_client.hashes[keyspace.session_versions_hash] = {
		_encode_field(session_key): "1",
		"bad-version-field": "2",
		_encode_field(("server", "bad-version")): "nan",
	}
	redis_client.hashes[keyspace.tombstones_hash] = {
		_encode_field(tombstone_key): dumps_payload(tombstone_to_payload(_tombstone("grave"))),
		"broken-tombstone": dumps_payload(tombstone_to_payload(_tombstone("ignore"))),
	}
	redis_client.hashes[keyspace.tombstone_versions_hash] = {
		_encode_field(tombstone_key): "4",
		_encode_field(("server", "bad-tombstone-version")): "nan",
	}

	repository = RedisStateRepository(
		redis_client=redis_client,
		keyspace=keyspace,
		refresh_on_startup=False,
	)

	loaded_state = await repository.get_session(session_key)
	loaded_tombstone = await repository.get_tombstone(tombstone_key)

	assert loaded_state is not None
	assert loaded_state.session_id == "sess"
	assert loaded_tombstone is not None
	assert loaded_tombstone.state.session_id == "grave"
	assert await repository.session_count() == 1
	assert await repository.list_session_items() == [(session_key, loaded_state)]
	assert await repository.list_tombstone_items() == [(tombstone_key, loaded_tombstone)]


@pytest.mark.asyncio
async def test_cas_upsert_retries_then_updates_and_delete_helpers_remove_entries() -> None:
	redis_client = _FakeRedis()
	keyspace = build_keyspace("adapter")
	repository = RedisStateRepository(
		redis_client=redis_client,
		keyspace=keyspace,
		refresh_on_startup=False,
	)
	key = ("server", "sess")
	field = _encode_field(key)
	redis_client.hashes[keyspace.session_versions_hash] = {field: "1"}

	new_version = await repository._cas_upsert(
		payload_hash=keyspace.sessions_hash,
		version_hash=keyspace.session_versions_hash,
		key=key,
		payload_json='{"ok": true}',
		version_map={},
	)

	assert new_version == 2
	assert redis_client.hashes[keyspace.sessions_hash][field] == '{"ok": true}'

	state = _session_state()
	tombstone = _tombstone()
	await repository.set_session(key, state)
	await repository.set_tombstone(key, tombstone)

	popped_state = await repository.pop_session(key)
	popped_tombstone = await repository.pop_tombstone(key)

	assert popped_state is state
	assert popped_tombstone is tombstone
	assert await repository.pop_session(("server", "missing")) is None
	assert await repository.pop_tombstone(("server", "missing")) is None


@pytest.mark.asyncio
async def test_cas_upsert_raises_after_retry_exhaustion_and_drain_clears_all_state() -> None:
	redis_client = _FakeRedis()
	keyspace = build_keyspace("adapter")
	repository = RedisStateRepository(
		redis_client=redis_client,
		keyspace=keyspace,
		refresh_on_startup=False,
	)
	key = ("server", "sess")

	redis_client.eval_results = [[0, "7"]] * 5
	with pytest.raises(RuntimeError, match="Redis CAS upsert failed after retries"):
		await repository._cas_upsert(
			payload_hash=keyspace.sessions_hash,
			version_hash=keyspace.session_versions_hash,
			key=key,
			payload_json="{}",
			version_map={},
		)

	state = _session_state()
	tombstone = _tombstone()
	await repository.set_session(key, state)
	await repository.set_tombstone(key, tombstone)

	drained_sessions, drained_tombstones = await repository.drain()

	assert drained_sessions == [state]
	assert drained_tombstones == [tombstone]
	assert await repository.session_count() == 0
	assert await repository.list_tombstone_items() == []
