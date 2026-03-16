from __future__ import annotations

import pytest
from remote_mcp_adapter.config.schemas.root import AdapterConfig
from remote_mcp_adapter.core.repo.records import ToolDefinitionBaseline, ToolDefinitionDriftSummary, ToolDefinitionSnapshot
from remote_mcp_adapter.core.storage.errors import SessionTrustContextMismatchError, TerminalSessionInvalidatedError
from remote_mcp_adapter.core.storage.store import SessionStore
from remote_mcp_adapter.session_integrity.models import SessionTrustContext


def _config(tmp_path):
    return AdapterConfig.model_validate(
        {
            "storage": {"root": str(tmp_path / "shared")},
            "sessions": {"tombstone_ttl_seconds": 60},
            "servers": [
                {
                    "id": "playwright",
                    "mount_path": "/mcp/playwright",
                    "upstream": {"url": "http://localhost:8931/mcp", "transport": "streamable_http"},
                }
            ],
        }
    )


class _Telemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def record_session_lifecycle(self, *, event: str, server_id: str) -> None:
        self.events.append((event, server_id))


@pytest.mark.asyncio
async def test_invalidate_session_blocks_reuse_until_new_session(tmp_path):
    telemetry = _Telemetry()
    store = SessionStore(config=_config(tmp_path), telemetry=telemetry)

    await store.ensure_session("playwright", "sess-1")
    await store.invalidate_session(
        server_id="playwright",
        session_id="sess-1",
        reason="Upstream tool catalog changed.",
    )

    assert await store.get_session("playwright", "sess-1") is None
    assert await store.get_terminal_session_reason("playwright", "sess-1") == "Upstream tool catalog changed."
    assert telemetry.events == [("created", "playwright"), ("tool_definition_invalidated", "playwright")]

    with pytest.raises(TerminalSessionInvalidatedError, match="Start a new Mcp-Session-Id"):
        await store.ensure_session("playwright", "sess-1")


@pytest.mark.asyncio
async def test_get_terminal_session_reason_drops_expired_terminal_tombstone(monkeypatch, tmp_path):
    store = SessionStore(config=_config(tmp_path))
    await store.invalidate_session(
        server_id="playwright",
        session_id="sess-1",
        reason="Upstream tool catalog changed.",
    )

    key = ("playwright", "sess-1")
    tombstone = await store._state_repository.get_tombstone(key)
    assert tombstone is not None
    tombstone.expires_at = 5.0

    monkeypatch.setattr("remote_mcp_adapter.core.storage.store.now_ts", lambda: 10.0)

    assert await store.get_terminal_session_reason("playwright", "sess-1") is None
    assert await store._state_repository.get_tombstone(key) is None


@pytest.mark.asyncio
async def test_invalidate_session_refreshes_expired_terminal_tombstone(monkeypatch, tmp_path):
    store = SessionStore(config=_config(tmp_path))
    await store.invalidate_session(
        server_id="playwright",
        session_id="sess-1",
        reason="Upstream tool catalog changed.",
    )

    key = ("playwright", "sess-1")
    tombstone = await store._state_repository.get_tombstone(key)
    assert tombstone is not None
    tombstone.expires_at = 5.0

    monkeypatch.setattr("remote_mcp_adapter.core.storage.store.now_ts", lambda: 10.0)

    await store.invalidate_session(
        server_id="playwright",
        session_id="sess-1",
        reason="Upstream tool catalog changed.",
    )

    refreshed_tombstone = await store._state_repository.get_tombstone(key)
    assert refreshed_tombstone is not None
    assert refreshed_tombstone.expires_at == 70.0


@pytest.mark.asyncio
async def test_bind_or_validate_session_trust_context_rejects_mismatch(tmp_path):
    store = SessionStore(config=_config(tmp_path))

    first = SessionTrustContext(binding_kind="adapter_auth_token", fingerprint="a" * 64)
    second = SessionTrustContext(binding_kind="adapter_auth_token", fingerprint="b" * 64)

    await store.bind_or_validate_session_trust_context(
        server_id="playwright",
        session_id="sess-1",
        trust_context=first,
    )

    bound = await store.get_session_trust_context("playwright", "sess-1")
    assert bound == first

    with pytest.raises(SessionTrustContextMismatchError, match="different authenticated request context"):
        await store.bind_or_validate_session_trust_context(
            server_id="playwright",
            session_id="sess-1",
            trust_context=second,
        )


@pytest.mark.asyncio
async def test_tool_definition_baseline_and_drift_summary_round_trip(tmp_path):
    store = SessionStore(config=_config(tmp_path))

    baseline = ToolDefinitionBaseline(
        established_at=1.0,
        tools={
            "tool_a": ToolDefinitionSnapshot(
                name="tool_a",
                canonical_hash="hash-a",
                payload={"name": "tool_a"},
            )
        },
    )
    summary = ToolDefinitionDriftSummary(
        detected_at=2.0,
        mode="warn",
        block_strategy="error",
        changed_tools=["tool_a"],
        preview="changed=tool_a[description]",
    )

    await store.set_tool_definition_baseline("playwright", "sess-1", baseline)
    await store.set_tool_definition_drift_summary("playwright", "sess-1", summary)

    loaded_baseline = await store.get_tool_definition_baseline("playwright", "sess-1")
    loaded_summary = await store.get_tool_definition_drift_summary("playwright", "sess-1")

    assert loaded_baseline is not None
    assert loaded_baseline.tools["tool_a"].canonical_hash == "hash-a"
    assert loaded_summary is not None
    assert loaded_summary.preview == "changed=tool_a[description]"

    await store.clear_tool_definition_drift_summary("playwright", "sess-1")
    assert await store.get_tool_definition_drift_summary("playwright", "sess-1") is None


@pytest.mark.asyncio
async def test_begin_and_end_in_flight_enforces_limit_and_never_goes_negative(tmp_path):
    config = _config(tmp_path)
    config.sessions.max_in_flight_per_session = 1
    store = SessionStore(config=config)

    await store.begin_in_flight("playwright", "sess-1")
    state = await store.get_session("playwright", "sess-1")
    assert state is not None
    assert state.in_flight == 1

    with pytest.raises(Exception, match="Maximum in-flight requests exceeded"):
        await store.begin_in_flight("playwright", "sess-1")

    await store.end_in_flight("playwright", "sess-1")
    await store.end_in_flight("playwright", "sess-1")
    state = await store.get_session("playwright", "sess-1")
    assert state is not None
    assert state.in_flight == 0


@pytest.mark.asyncio
async def test_upload_lifecycle_register_resolve_and_remove(tmp_path):
    store = SessionStore(config=_config(tmp_path))

    upload_id, abs_path, rel_path = await store.allocate_upload_path(
        server_id="playwright",
        session_id="sess-1",
        filename="payload.txt",
    )
    assert upload_id
    assert rel_path.endswith("payload.txt")

    abs_path.write_text("hello upload", encoding="utf-8")
    record = await store.register_upload(
        server_id="playwright",
        session_id="sess-1",
        upload_id=upload_id,
        abs_path=abs_path,
    )
    assert record.filename == "payload.txt"
    assert record.size_bytes > 0

    resolved = await store.resolve_upload_handle(
        server_id="playwright",
        session_id="sess-1",
        handle=f"upload://sessions/sess-1/{upload_id}",
    )
    assert resolved.upload_id == upload_id

    with pytest.raises(ValueError, match="session mismatch"):
        await store.resolve_upload_handle(
            server_id="playwright",
            session_id="sess-2",
            handle=f"upload://sessions/sess-1/{upload_id}",
        )

    removed = await store.remove_uploads(
        server_id="playwright",
        session_id="sess-1",
        upload_ids=[upload_id],
    )
    assert removed == 1
    assert await store.remove_uploads(server_id="playwright", session_id="sess-1", upload_ids=[]) == 0


@pytest.mark.asyncio
async def test_artifact_lifecycle_allocate_finalize_get_list_resolve_and_snapshot(tmp_path):
    store = SessionStore(config=_config(tmp_path))

    artifact_id, abs_path, rel_path = await store.allocate_artifact_path(
        server_id="playwright",
        session_id="sess-1",
        filename="artifact",
        tool_name="capture",
    )
    assert artifact_id
    assert rel_path.endswith("artifact")

    abs_path.write_bytes(b"artifact-data")
    finalized = await store.finalize_artifact(
        server_id="playwright",
        session_id="sess-1",
        artifact_id=artifact_id,
        mime_type="image/jpeg",
    )
    assert finalized.filename.endswith(".jpg")
    assert finalized.visibility_state == "committed"

    resolved = await store.get_artifact(
        server_id="playwright",
        session_id="sess-1",
        artifact_id=artifact_id,
    )
    assert resolved.artifact_id == artifact_id

    listed = await store.list_artifacts(
        server_id="playwright",
        session_id="sess-1",
        touch=True,
    )
    assert [record.artifact_id for record in listed] == [artifact_id]

    resolved_uri = await store.resolve_artifact_uri(
        server_id="playwright",
        session_id="sess-1",
        artifact_uri=f"artifact://sessions/sess-1/{artifact_id}/{finalized.filename}",
    )
    assert resolved_uri.filename == finalized.filename

    with pytest.raises(ValueError, match="session mismatch"):
        await store.resolve_artifact_uri(
            server_id="playwright",
            session_id="sess-2",
            artifact_uri=f"artifact://sessions/sess-1/{artifact_id}/{finalized.filename}",
        )

    snapshot = await store.get_session_snapshot("playwright", "sess-1")
    assert snapshot["uploads_count"] == 0
    assert snapshot["artifacts_count"] == 1
    assert snapshot["session_id"] == "sess-1"
