from __future__ import annotations

from types import SimpleNamespace

import pytest

from remote_mcp_adapter.proxy import upload_nonce_store as uns


class FakeRedis:
    def __init__(self, *, set_result=True, eval_result=1):
        self.set_result = set_result
        self.eval_result = eval_result
        self.set_calls: list[tuple[object, ...]] = []
        self.eval_calls: list[tuple[object, ...]] = []

    async def set(self, key, value, ex, nx):
        self.set_calls.append((key, value, ex, nx))
        return self.set_result

    async def eval(self, script, numkeys, key, payload):
        self.eval_calls.append((script, numkeys, key, payload))
        return self.eval_result


def _config(*, local_path: str = "", redis_key_base: str = "adapter"):
    return SimpleNamespace(
        state_persistence=SimpleNamespace(
            disk=SimpleNamespace(local_path=local_path),
            redis=SimpleNamespace(key_base=redis_key_base),
        )
    )


@pytest.mark.asyncio
async def test_in_memory_backend_label():
    store = uns.InMemoryUploadNonceStore()
    assert store.backend == "memory"


@pytest.mark.asyncio
async def test_in_memory_reserve_and_collision_and_consume_success():
    store = uns.InMemoryUploadNonceStore()

    first = await store.reserve_nonce(nonce="n1", server_id="s", session_id="sess", expires_at=200, now_epoch=100)
    second = await store.reserve_nonce(nonce="n1", server_id="s", session_id="sess", expires_at=200, now_epoch=100)
    consumed = await store.consume_nonce(nonce="n1", server_id="s", session_id="sess", expires_at=200, now_epoch=100)

    assert first is True
    assert second is False
    assert consumed is True


@pytest.mark.asyncio
async def test_in_memory_consume_returns_false_for_missing_and_mismatch_and_expired():
    store = uns.InMemoryUploadNonceStore()

    missing = await store.consume_nonce(nonce="missing", server_id="s", session_id="sess", expires_at=1, now_epoch=1)

    await store.reserve_nonce(nonce="n2", server_id="s", session_id="sess", expires_at=200, now_epoch=100)
    wrong_server = await store.consume_nonce(nonce="n2", server_id="other", session_id="sess", expires_at=200, now_epoch=100)
    wrong_session = await store.consume_nonce(nonce="n2", server_id="s", session_id="other", expires_at=200, now_epoch=100)
    wrong_expiry = await store.consume_nonce(nonce="n2", server_id="s", session_id="sess", expires_at=201, now_epoch=100)

    await store.reserve_nonce(nonce="n3", server_id="s", session_id="sess", expires_at=50, now_epoch=49)
    expired = await store.consume_nonce(nonce="n3", server_id="s", session_id="sess", expires_at=50, now_epoch=51)

    assert missing is False
    assert wrong_server is False
    assert wrong_session is False
    assert wrong_expiry is False
    assert expired is False


@pytest.mark.asyncio
async def test_in_memory_prunes_expired_on_reserve():
    store = uns.InMemoryUploadNonceStore()
    await store.reserve_nonce(nonce="n4", server_id="s", session_id="sess", expires_at=10, now_epoch=9)
    kept = await store.reserve_nonce(nonce="n4", server_id="s", session_id="sess", expires_at=20, now_epoch=11)
    assert kept is True


@pytest.mark.asyncio
async def test_sqlite_backend_and_initialization_and_reserve_consume(tmp_path):
    db_path = tmp_path / "state" / "nonces.sqlite3"
    store = uns.SqliteUploadNonceStore(db_path=db_path)

    assert store.backend == "sqlite"
    await store._ensure_initialized()
    await store._ensure_initialized()

    assert db_path.exists()
    inserted = await store.reserve_nonce(nonce="n1", server_id="s", session_id="sess", expires_at=200, now_epoch=100)
    duplicate = await store.reserve_nonce(nonce="n1", server_id="s", session_id="sess", expires_at=200, now_epoch=100)
    consumed = await store.consume_nonce(nonce="n1", server_id="s", session_id="sess", expires_at=200, now_epoch=100)
    consumed_again = await store.consume_nonce(nonce="n1", server_id="s", session_id="sess", expires_at=200, now_epoch=100)

    assert inserted is True
    assert duplicate is False
    assert consumed is True
    assert consumed_again is False


@pytest.mark.asyncio
async def test_sqlite_ensure_initialized_returns_inside_lock_when_already_set(tmp_path):
    store = uns.SqliteUploadNonceStore(db_path=tmp_path / "nonces.sqlite3")

    class LockThatSetsInitialized:
        async def __aenter__(self):
            store._initialized = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    store._init_lock = LockThatSetsInitialized()
    await store._ensure_initialized()
    assert store._initialized is True


@pytest.mark.asyncio
async def test_sqlite_consume_false_when_filters_do_not_match(tmp_path):
    store = uns.SqliteUploadNonceStore(db_path=tmp_path / "nonces.sqlite3")
    await store.reserve_nonce(nonce="n2", server_id="s", session_id="sess", expires_at=300, now_epoch=100)

    bad_server = await store.consume_nonce(nonce="n2", server_id="wrong", session_id="sess", expires_at=300, now_epoch=100)
    bad_session = await store.consume_nonce(nonce="n2", server_id="s", session_id="wrong", expires_at=300, now_epoch=100)
    bad_expiry = await store.consume_nonce(nonce="n2", server_id="s", session_id="sess", expires_at=301, now_epoch=100)

    assert bad_server is False
    assert bad_session is False
    assert bad_expiry is False


@pytest.mark.asyncio
async def test_sqlite_prunes_expired_rows_before_insert_and_consume(tmp_path):
    store = uns.SqliteUploadNonceStore(db_path=tmp_path / "nonces.sqlite3")

    await store.reserve_nonce(nonce="old", server_id="s", session_id="sess", expires_at=50, now_epoch=10)
    await store.reserve_nonce(nonce="new", server_id="s", session_id="sess", expires_at=120, now_epoch=100)

    old_consumed = await store.consume_nonce(nonce="old", server_id="s", session_id="sess", expires_at=50, now_epoch=100)
    new_consumed = await store.consume_nonce(nonce="new", server_id="s", session_id="sess", expires_at=120, now_epoch=100)

    assert old_consumed is False
    assert new_consumed is True


@pytest.mark.asyncio
async def test_redis_backend_key_payload_and_reserve_bool_and_ttl_floor():
    redis = FakeRedis(set_result="OK")
    store = uns.RedisUploadNonceStore(redis_client=redis, key_prefix="kb:upload_nonces")

    assert store.backend == "redis"
    assert store._key("n1") == "kb:upload_nonces:n1"
    assert store._payload(server_id="s", session_id="sess", expires_at=105) == "s\x1fsess\x1f105"

    reserved = await store.reserve_nonce(nonce="n1", server_id="s", session_id="sess", expires_at=100, now_epoch=100)
    key, payload, ex, nx = redis.set_calls[-1]

    assert reserved is True
    assert key == "kb:upload_nonces:n1"
    assert payload == "s\x1fsess\x1f100"
    assert ex == 1
    assert nx is True


@pytest.mark.asyncio
async def test_redis_reserve_false_when_set_returns_falsey():
    redis = FakeRedis(set_result=False)
    store = uns.RedisUploadNonceStore(redis_client=redis, key_prefix="kb:upload_nonces")

    reserved = await store.reserve_nonce(nonce="n2", server_id="s", session_id="sess", expires_at=120, now_epoch=100)
    assert reserved is False


@pytest.mark.asyncio
async def test_redis_consume_returns_false_when_expired_or_no_match_and_true_when_match():
    redis_ok = FakeRedis(eval_result=1)
    store_ok = uns.RedisUploadNonceStore(redis_client=redis_ok, key_prefix="kb:upload_nonces")
    redis_no = FakeRedis(eval_result=0)
    store_no = uns.RedisUploadNonceStore(redis_client=redis_no, key_prefix="kb:upload_nonces")

    expired = await store_ok.consume_nonce(nonce="n1", server_id="s", session_id="sess", expires_at=9, now_epoch=10)
    matched = await store_ok.consume_nonce(nonce="n1", server_id="s", session_id="sess", expires_at=10, now_epoch=10)
    not_matched = await store_no.consume_nonce(nonce="n2", server_id="s", session_id="sess", expires_at=20, now_epoch=10)

    assert expired is False
    assert matched is True
    assert not_matched is False
    assert len(redis_ok.eval_calls) == 1
    script, numkeys, key, payload = redis_ok.eval_calls[0]
    assert "redis.call('GET'" in script
    assert numkeys == 1
    assert key == "kb:upload_nonces:n1"
    assert payload == "s\x1fsess\x1f10"


def test_runtime_backend_details_returns_dict_or_empty():
    with_dict = SimpleNamespace(backend_details={"k": "v"})
    with_non_dict = SimpleNamespace(backend_details="not-dict")

    assert uns._runtime_backend_details(with_dict) == {"k": "v"}
    assert uns._runtime_backend_details(with_non_dict) == {}
    assert uns._runtime_backend_details(SimpleNamespace()) == {}


def test_resolve_sqlite_nonce_db_path_prefers_runtime_paths_then_config_then_none():
    config = _config(local_path=" D:/cfg.sqlite3 ")

    runtime_db_path = SimpleNamespace(backend_details={"db_path": " D:/r1.sqlite3 "})
    runtime_snapshot_path = SimpleNamespace(backend_details={"snapshot_db_path": " D:/r2.sqlite3 "})
    runtime_none = SimpleNamespace(backend_details={})
    config_none = _config(local_path="   ")

    assert uns._resolve_sqlite_nonce_db_path(config=config, runtime=runtime_db_path) == uns.Path("D:/r1.sqlite3")
    assert uns._resolve_sqlite_nonce_db_path(config=config, runtime=runtime_snapshot_path) == uns.Path("D:/r2.sqlite3")
    assert uns._resolve_sqlite_nonce_db_path(config=config, runtime=runtime_none) == uns.Path("D:/cfg.sqlite3")
    assert uns._resolve_sqlite_nonce_db_path(config=config_none, runtime=runtime_none) is None


def test_build_upload_nonce_store_prefers_redis_with_runtime_key_base():
    redis_client = object()
    config = _config(local_path="D:/fallback.sqlite3", redis_key_base="cfgbase")
    runtime = SimpleNamespace(backend_type="redis", redis_client=redis_client, backend_details={"key_base": " rtbase "})

    store = uns.build_upload_nonce_store(config=config, runtime=runtime)

    assert isinstance(store, uns.RedisUploadNonceStore)
    assert store._key_prefix == "rtbase:upload_nonces"


def test_build_upload_nonce_store_uses_config_key_base_when_runtime_missing_or_blank():
    redis_client = object()
    config = _config(local_path="D:/fallback.sqlite3", redis_key_base="cfgbase")

    runtime_missing = SimpleNamespace(backend_type="redis", redis_client=redis_client, backend_details={})
    runtime_blank = SimpleNamespace(backend_type="redis", redis_client=redis_client, backend_details={"key_base": "   "})

    store_missing = uns.build_upload_nonce_store(config=config, runtime=runtime_missing)
    store_blank = uns.build_upload_nonce_store(config=config, runtime=runtime_blank)

    assert isinstance(store_missing, uns.RedisUploadNonceStore)
    assert isinstance(store_blank, uns.RedisUploadNonceStore)
    assert store_missing._key_prefix == "cfgbase:upload_nonces"
    assert store_blank._key_prefix == "cfgbase:upload_nonces"


def test_build_upload_nonce_store_falls_back_to_sqlite_then_memory(tmp_path):
    sqlite_path = str(tmp_path / "state.sqlite3")

    config_sqlite = _config(local_path=sqlite_path, redis_key_base="cfgbase")
    runtime_no_redis = SimpleNamespace(backend_type="redis", redis_client=None, backend_details={})
    sqlite_store = uns.build_upload_nonce_store(config=config_sqlite, runtime=runtime_no_redis)

    config_memory = _config(local_path="   ", redis_key_base="cfgbase")
    runtime_memory = SimpleNamespace(backend_type="memory", backend_details={})
    memory_store = uns.build_upload_nonce_store(config=config_memory, runtime=runtime_memory)

    assert isinstance(sqlite_store, uns.SqliteUploadNonceStore)
    assert isinstance(memory_store, uns.InMemoryUploadNonceStore)
