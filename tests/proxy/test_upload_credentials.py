from __future__ import annotations

from types import SimpleNamespace

import pytest

from remote_mcp_adapter.proxy import upload_credentials as uc


class FakeNonceStore:
    def __init__(self, *, backend: str = "fake", reserve_results: list[bool] | None = None, consume_result: bool = True):
        self.backend = backend
        self.reserve_results = list(reserve_results or [True])
        self.consume_result = consume_result
        self.reserve_calls: list[dict[str, object]] = []
        self.consume_calls: list[dict[str, object]] = []

    async def reserve_nonce(self, **kwargs):
        self.reserve_calls.append(kwargs)
        if self.reserve_results:
            return self.reserve_results.pop(0)
        return False

    async def consume_nonce(self, **kwargs):
        self.consume_calls.append(kwargs)
        return self.consume_result


class FakeTelemetry:
    def __init__(self):
        self.nonce_events: list[dict[str, str]] = []
        self.credential_events: list[dict[str, str]] = []

    async def record_nonce_operation(self, **kwargs):
        self.nonce_events.append(kwargs)

    async def record_upload_credential_event(self, **kwargs):
        self.credential_events.append(kwargs)


@pytest.mark.asyncio
async def test_signature_payload_builds_expected_bytes():
    payload = uc._signature_payload(server_id="s", session_id="sess", expires_at=123, nonce="n")
    assert payload == b"s\nsess\n123\nn"


def test_init_sets_min_ttl_and_properties_and_nonce_backend():
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=0)
    assert manager.enabled is True
    assert manager.ttl_seconds == 1
    assert manager.nonce_backend == "memory"


def test_from_config_uses_signing_secret_and_token_fallback():
    with_secret = SimpleNamespace(
        core=SimpleNamespace(
            auth=SimpleNamespace(enabled=True, signing_secret="  s1 ", token="tok", signed_upload_ttl_seconds=30)
        )
    )
    manager_a = uc.UploadCredentialManager.from_config(with_secret)
    assert manager_a.enabled is True

    with_token_only = SimpleNamespace(
        core=SimpleNamespace(auth=SimpleNamespace(enabled=True, signing_secret="", token="tok", signed_upload_ttl_seconds=15))
    )
    manager_b = uc.UploadCredentialManager.from_config(with_token_only)
    assert manager_b.enabled is True

    disabled = SimpleNamespace(
        core=SimpleNamespace(auth=SimpleNamespace(enabled=True, signing_secret="  ", token="", signed_upload_ttl_seconds=15))
    )
    manager_c = uc.UploadCredentialManager.from_config(disabled)
    assert manager_c.enabled is False


def test_set_nonce_store_and_use_memory_nonce_store():
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    custom_store = FakeNonceStore(backend="custom")
    manager.set_nonce_store(custom_store)
    assert manager.nonce_backend == "custom"
    manager.use_memory_nonce_store()
    assert manager.nonce_backend == "memory"


@pytest.mark.asyncio
async def test_issue_returns_empty_dict_when_disabled():
    telemetry = FakeTelemetry()
    manager = uc.UploadCredentialManager(enabled=False, secret="sec", ttl_seconds=10, telemetry=telemetry)
    issued = await manager.issue(server_id="s", session_id="sess")
    assert issued == {}
    assert telemetry.credential_events == [
        {
            "operation": "issue",
            "result": "disabled",
            "backend": "memory",
            "server_id": "s",
        }
    ]


@pytest.mark.asyncio
async def test_issue_retries_on_collision_then_succeeds(monkeypatch):
    telemetry = FakeTelemetry()
    store = FakeNonceStore(reserve_results=[False, True])
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=20, nonce_store=store, telemetry=telemetry)

    monkeypatch.setattr(uc.time, "time", lambda: 1000)
    nonces = iter(["nonce_a", "nonce_b"])
    monkeypatch.setattr(uc, "token_hex", lambda _: next(nonces))

    issued = await manager.issue(server_id="srv", session_id="sess")
    expected_signature = manager._sign(server_id="srv", session_id="sess", expires_at=1020, nonce="nonce_b")

    assert issued == {
        "mcp_upload_exp": "1020",
        "mcp_upload_nonce": "nonce_b",
        "mcp_upload_sig": expected_signature,
    }
    assert telemetry.nonce_events == [
        {
            "operation": "reserve",
            "result": "collision",
            "backend": "fake",
            "server_id": "srv",
        },
        {
            "operation": "reserve",
            "result": "success",
            "backend": "fake",
            "server_id": "srv",
        },
    ]
    assert telemetry.credential_events == [
        {
            "operation": "issue",
            "result": "issued",
            "backend": "fake",
            "server_id": "srv",
        }
    ]


@pytest.mark.asyncio
async def test_issue_raises_after_retry_exhaustion(monkeypatch):
    telemetry = FakeTelemetry()
    store = FakeNonceStore(reserve_results=[False, False, False, False, False])
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=5, nonce_store=store, telemetry=telemetry)

    monkeypatch.setattr(uc.time, "time", lambda: 200)
    monkeypatch.setattr(uc, "token_hex", lambda _: "same")

    with pytest.raises(RuntimeError, match="Failed to reserve upload nonce after retries"):
        await manager.issue(server_id="srv", session_id="sess")

    assert len(store.reserve_calls) == 5
    assert all(event["operation"] == "reserve" for event in telemetry.nonce_events)
    assert all(event["result"] == "collision" for event in telemetry.nonce_events)
    assert all(event["backend"] == "fake" for event in telemetry.nonce_events)
    assert all(event["server_id"] == "srv" for event in telemetry.nonce_events)
    assert telemetry.credential_events == [
        {
            "operation": "issue",
            "result": "reserve_failed",
            "backend": "fake",
            "server_id": "srv",
        }
    ]


@pytest.mark.asyncio
async def test_validate_returns_false_when_disabled():
    telemetry = FakeTelemetry()
    manager = uc.UploadCredentialManager(enabled=False, secret="sec", ttl_seconds=10, telemetry=telemetry)
    result = await manager.validate_and_consume(server_id="s", session_id="sess", query_params={})
    assert result is False
    assert telemetry.credential_events[-1]["result"] == "disabled"


@pytest.mark.asyncio
async def test_validate_returns_false_for_missing_fields():
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    assert await manager.validate_and_consume(server_id="s", session_id="sess", query_params={"mcp_upload_exp": "9"}) is False


@pytest.mark.asyncio
async def test_validate_returns_false_for_invalid_expiry():
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    query = {"mcp_upload_exp": "abc", "mcp_upload_nonce": "n", "mcp_upload_sig": "sig"}
    assert await manager.validate_and_consume(server_id="s", session_id="sess", query_params=query) is False


@pytest.mark.asyncio
async def test_validate_returns_false_for_expired(monkeypatch):
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    monkeypatch.setattr(uc.time, "time", lambda: 50)
    query = {"mcp_upload_exp": "49", "mcp_upload_nonce": "n", "mcp_upload_sig": "sig"}
    assert await manager.validate_and_consume(server_id="s", session_id="sess", query_params=query) is False


@pytest.mark.asyncio
async def test_validate_returns_false_for_bad_signature(monkeypatch):
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    monkeypatch.setattr(uc.time, "time", lambda: 50)
    query = {"mcp_upload_exp": "55", "mcp_upload_nonce": "n", "mcp_upload_sig": "wrong"}
    assert await manager.validate_and_consume(server_id="s", session_id="sess", query_params=query) is False


@pytest.mark.asyncio
async def test_validate_accepts_and_consumes(monkeypatch):
    telemetry = FakeTelemetry()
    store = FakeNonceStore(backend="fake", consume_result=True)
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=10, nonce_store=store, telemetry=telemetry)

    monkeypatch.setattr(uc.time, "time", lambda: 100)
    signature = manager._sign(server_id="s", session_id="sess", expires_at=105, nonce="n1")
    query = {"mcp_upload_exp": "105", "mcp_upload_nonce": "n1", "mcp_upload_sig": signature}

    accepted = await manager.validate_and_consume(server_id="s", session_id="sess", query_params=query)
    assert accepted is True
    assert telemetry.nonce_events[-1]["result"] == "success"
    assert telemetry.credential_events[-1]["result"] == "accepted"


@pytest.mark.asyncio
async def test_validate_rejects_replay_or_invalid_after_signature(monkeypatch):
    telemetry = FakeTelemetry()
    store = FakeNonceStore(backend="fake", consume_result=False)
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=10, nonce_store=store, telemetry=telemetry)

    monkeypatch.setattr(uc.time, "time", lambda: 100)
    signature = manager._sign(server_id="s", session_id="sess", expires_at=105, nonce="n1")
    query = {"mcp_upload_exp": "105", "mcp_upload_nonce": "n1", "mcp_upload_sig": signature}

    accepted = await manager.validate_and_consume(server_id="s", session_id="sess", query_params=query)
    assert accepted is False
    assert telemetry.nonce_events[-1]["result"] == "invalid"
    assert telemetry.credential_events[-1]["result"] == "replay_or_invalid"


@pytest.mark.asyncio
async def test_private_record_helpers_noop_without_telemetry():
    manager = uc.UploadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    await manager._record_nonce(operation="reserve", result="success", server_id="s")
    await manager._record_upload_credential(operation="issue", result="issued", server_id="s")
