from __future__ import annotations

from pathlib import Path

from remote_mcp_adapter.core.repo import records as rec
from remote_mcp_adapter.core.repo import state_codec as codec
from remote_mcp_adapter.session_integrity.models import SessionTrustContext


def _upload(upload_id: str) -> rec.UploadRecord:
    return rec.UploadRecord(
        server_id="s1",
        session_id="sess",
        upload_id=upload_id,
        filename="u.txt",
        abs_path=Path("/tmp/u.txt"),
        rel_path="sessions/sess/u.txt",
        mime_type="text/plain",
        size_bytes=10,
        sha256="abcd",
        created_at=1.0,
        last_accessed=1.0,
        last_updated=1.0,
    )


def _artifact(artifact_id: str, visibility_state: str = "pending") -> rec.ArtifactRecord:
    return rec.ArtifactRecord(
        server_id="s1",
        session_id="sess",
        artifact_id=artifact_id,
        filename="a.txt",
        abs_path=Path("/tmp/a.txt"),
        rel_path="sessions/sess/a.txt",
        mime_type="text/plain",
        size_bytes=20,
        created_at=2.0,
        last_accessed=2.0,
        last_updated=2.0,
        tool_name="tool",
        expose_as_resource=True,
        visibility_state=visibility_state,
    )


def test_records_touch_and_now_ts(monkeypatch):
    monkeypatch.setattr(rec, "now_ts", lambda: 123.0)

    up = _upload("u1")
    up.touch()
    assert up.last_accessed == 123.0 and up.last_updated == 123.0
    up.touch(55.0)
    assert up.last_accessed == 55.0 and up.last_updated == 55.0

    art = _artifact("a1")
    art.touch()
    assert art.last_accessed == 123.0 and art.last_updated == 123.0
    art.touch(66.0)
    assert art.last_accessed == 66.0 and art.last_updated == 66.0

    state = rec.SessionState(server_id="s1", session_id="sess", created_at=1.0, last_accessed=1.0)
    state.touch()
    assert state.last_accessed == 123.0
    state.touch(77.0)
    assert state.last_accessed == 77.0


def test_state_codec_roundtrip_and_visibility_fallback():
    state = rec.SessionState(server_id="s1", session_id="sess", created_at=1.0, last_accessed=1.0, in_flight=2)
    state.uploads["u1"] = _upload("u1")
    state.artifacts["a1"] = _artifact("a1", visibility_state="committed")
    state.trust_context = SessionTrustContext(
        binding_kind="adapter_auth_token",
        fingerprint="f" * 64,
    )
    state.tool_definition_baseline = rec.ToolDefinitionBaseline(
        established_at=3.0,
        tools={
            "tool_a": rec.ToolDefinitionSnapshot(
                name="tool_a",
                canonical_hash="hash-a",
                payload={"name": "tool_a", "description": "desc"},
            )
        },
    )
    state.tool_definition_drift_summary = rec.ToolDefinitionDriftSummary(
        detected_at=4.0,
        mode="warn",
        block_strategy="error",
        changed_tools=["tool_a"],
        preview="changed=tool_a[description]",
    )

    payload = codec.session_state_to_payload(state)
    loaded = codec.session_state_from_payload(payload)
    assert loaded.server_id == "s1"
    assert loaded.in_flight == 2
    assert "u1" in loaded.uploads
    assert "a1" in loaded.artifacts
    assert loaded.trust_context is not None
    assert loaded.trust_context.binding_kind == "adapter_auth_token"
    assert loaded.trust_context.fingerprint == "f" * 64
    assert loaded.tool_definition_baseline is not None
    assert loaded.tool_definition_baseline.tools["tool_a"].canonical_hash == "hash-a"
    assert loaded.tool_definition_drift_summary is not None
    assert loaded.tool_definition_drift_summary.preview == "changed=tool_a[description]"

    bad_visibility_payload = {
        "server_id": "s1",
        "session_id": "sess",
        "artifact_id": "a2",
        "filename": "a.txt",
        "abs_path": "/tmp/a.txt",
        "rel_path": "sessions/sess/a.txt",
        "mime_type": "text/plain",
        "size_bytes": 1,
        "created_at": 1.0,
        "last_accessed": 1.0,
        "last_updated": 1.0,
        "visibility_state": "invalid",
    }
    rec2 = codec._artifact_record_from_payload(bad_visibility_payload)
    assert rec2.visibility_state == "committed"

    tombstone = rec.SessionTombstone(state=state, expires_at=999.0, terminal_reason="tool_definition_drift")
    tomb_payload = codec.tombstone_to_payload(tombstone)
    tomb_loaded = codec.tombstone_from_payload(tomb_payload)
    assert tomb_loaded.expires_at == 999.0
    assert tomb_loaded.state.session_id == "sess"
    assert tomb_loaded.terminal_reason == "tool_definition_drift"

    raw = codec.dumps_payload(payload)
    decoded = codec.loads_payload(raw)
    assert decoded["session_id"] == "sess"
