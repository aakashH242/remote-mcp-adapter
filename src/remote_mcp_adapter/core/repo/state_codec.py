"""Serialization helpers for session/tombstone persistence payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...session_integrity.models import SessionTrustContext
from .records import (
    ArtifactRecord,
    SessionState,
    SessionTombstone,
    ToolDefinitionBaseline,
    ToolDefinitionDriftSummary,
    ToolDefinitionSnapshot,
    UploadRecord,
)


def _upload_record_to_payload(record: UploadRecord) -> dict[str, Any]:
    """Serialize UploadRecord to a JSON-serializable dict.

    Args:
        record: Upload record to serialize.
    """
    return {
        "server_id": record.server_id,
        "session_id": record.session_id,
        "upload_id": record.upload_id,
        "filename": record.filename,
        "abs_path": str(record.abs_path),
        "rel_path": record.rel_path,
        "mime_type": record.mime_type,
        "size_bytes": record.size_bytes,
        "sha256": record.sha256,
        "created_at": record.created_at,
        "last_accessed": record.last_accessed,
        "last_updated": record.last_updated,
    }


def _artifact_record_to_payload(record: ArtifactRecord) -> dict[str, Any]:
    """Serialize ArtifactRecord to a JSON-serializable dict.

    Args:
        record: Artifact record to serialize.
    """
    return {
        "server_id": record.server_id,
        "session_id": record.session_id,
        "artifact_id": record.artifact_id,
        "filename": record.filename,
        "abs_path": str(record.abs_path),
        "rel_path": record.rel_path,
        "mime_type": record.mime_type,
        "size_bytes": record.size_bytes,
        "created_at": record.created_at,
        "last_accessed": record.last_accessed,
        "last_updated": record.last_updated,
        "tool_name": record.tool_name,
        "expose_as_resource": record.expose_as_resource,
        "visibility_state": record.visibility_state,
    }


def session_state_to_payload(state: SessionState) -> dict[str, Any]:
    """Build a JSON-serializable payload from a SessionState instance.

    Args:
        state: Session state to serialize.
    """
    return {
        "server_id": state.server_id,
        "session_id": state.session_id,
        "created_at": state.created_at,
        "last_accessed": state.last_accessed,
        "in_flight": state.in_flight,
        "uploads": [_upload_record_to_payload(record) for record in state.uploads.values()],
        "artifacts": [_artifact_record_to_payload(record) for record in state.artifacts.values()],
        "trust_context": _session_trust_context_to_payload(state.trust_context),
        "tool_definition_baseline": _tool_definition_baseline_to_payload(state.tool_definition_baseline),
        "tool_definition_drift_summary": _tool_definition_drift_summary_to_payload(state.tool_definition_drift_summary),
    }


def _session_trust_context_to_payload(context: SessionTrustContext | None) -> dict[str, Any] | None:
    """Serialize a bound session trust context when present.

    Args:
        context: Bound trust context or ``None``.

    Returns:
        JSON-serializable payload or ``None``.
    """
    if context is None:
        return None
    return {
        "binding_kind": context.binding_kind,
        "fingerprint": context.fingerprint,
    }


def _session_trust_context_from_payload(payload: dict[str, Any] | None) -> SessionTrustContext | None:
    """Deserialize a bound session trust context from payload data.

    Args:
        payload: Decoded trust-context payload or ``None``.

    Returns:
        Bound trust context or ``None``.
    """
    if payload is None:
        return None
    return SessionTrustContext(
        binding_kind=str(payload["binding_kind"]),  # type: ignore[arg-type]
        fingerprint=str(payload["fingerprint"]),
    )


def _tool_definition_snapshot_to_payload(snapshot: ToolDefinitionSnapshot) -> dict[str, Any]:
    """Serialize a pinned tool snapshot to JSON-friendly payload data.

    Args:
        snapshot: Tool snapshot to serialize.

    Returns:
        JSON-serializable dictionary.
    """
    return {
        "name": snapshot.name,
        "canonical_hash": snapshot.canonical_hash,
        "payload": snapshot.payload,
    }


def _tool_definition_snapshot_from_payload(payload: dict[str, Any]) -> ToolDefinitionSnapshot:
    """Deserialize a pinned tool snapshot from payload data.

    Args:
        payload: Decoded payload dictionary.

    Returns:
        Deserialized tool snapshot instance.
    """
    return ToolDefinitionSnapshot(
        name=str(payload["name"]),
        canonical_hash=str(payload["canonical_hash"]),
        payload=dict(payload.get("payload", {})),
    )


def _tool_definition_baseline_to_payload(baseline: ToolDefinitionBaseline | None) -> dict[str, Any] | None:
    """Serialize a pinned tool baseline when present.

    Args:
        baseline: Baseline instance or ``None``.

    Returns:
        JSON-serializable payload or ``None``.
    """
    if baseline is None:
        return None
    return {
        "established_at": baseline.established_at,
        "tools": {name: _tool_definition_snapshot_to_payload(snapshot) for name, snapshot in baseline.tools.items()},
    }


def _tool_definition_baseline_from_payload(payload: dict[str, Any] | None) -> ToolDefinitionBaseline | None:
    """Deserialize a pinned tool baseline from payload data.

    Args:
        payload: Decoded baseline payload or ``None``.

    Returns:
        Baseline instance or ``None``.
    """
    if payload is None:
        return None
    return ToolDefinitionBaseline(
        established_at=float(payload["established_at"]),
        tools={
            str(name): _tool_definition_snapshot_from_payload(dict(snapshot_payload))
            for name, snapshot_payload in dict(payload.get("tools", {})).items()
        },
    )


def _tool_definition_drift_summary_to_payload(
    summary: ToolDefinitionDriftSummary | None,
) -> dict[str, Any] | None:
    """Serialize a drift summary when present.

    Args:
        summary: Drift summary or ``None``.

    Returns:
        JSON-serializable payload or ``None``.
    """
    if summary is None:
        return None
    return {
        "detected_at": summary.detected_at,
        "mode": summary.mode,
        "block_strategy": summary.block_strategy,
        "changed_tools": list(summary.changed_tools),
        "new_tools": list(summary.new_tools),
        "removed_tools": list(summary.removed_tools),
        "changed_fields": {name: list(fields) for name, fields in summary.changed_fields.items()},
        "preview": summary.preview,
    }


def _tool_definition_drift_summary_from_payload(
    payload: dict[str, Any] | None,
) -> ToolDefinitionDriftSummary | None:
    """Deserialize a drift summary from payload data.

    Args:
        payload: Decoded summary payload or ``None``.

    Returns:
        Drift summary or ``None``.
    """
    if payload is None:
        return None
    return ToolDefinitionDriftSummary(
        detected_at=float(payload["detected_at"]),
        mode=str(payload["mode"]),  # type: ignore[arg-type]
        block_strategy=str(payload["block_strategy"]),  # type: ignore[arg-type]
        changed_tools=[str(name) for name in payload.get("changed_tools", [])],
        new_tools=[str(name) for name in payload.get("new_tools", [])],
        removed_tools=[str(name) for name in payload.get("removed_tools", [])],
        changed_fields={
            str(name): [str(field_name) for field_name in fields]
            for name, fields in dict(payload.get("changed_fields", {})).items()
        },
        preview=str(payload["preview"]) if payload.get("preview") is not None else None,
    )


def _upload_record_from_payload(payload: dict[str, Any]) -> UploadRecord:
    """Deserialize UploadRecord from a decoded JSON dict.

    Args:
        payload: Decoded JSON dict.
    """
    return UploadRecord(
        server_id=str(payload["server_id"]),
        session_id=str(payload["session_id"]),
        upload_id=str(payload["upload_id"]),
        filename=str(payload["filename"]),
        abs_path=Path(str(payload["abs_path"])),
        rel_path=str(payload["rel_path"]),
        mime_type=str(payload["mime_type"]),
        size_bytes=int(payload["size_bytes"]),
        sha256=str(payload["sha256"]),
        created_at=float(payload["created_at"]),
        last_accessed=float(payload["last_accessed"]),
        last_updated=float(payload["last_updated"]),
    )


def _artifact_record_from_payload(payload: dict[str, Any]) -> ArtifactRecord:
    """Deserialize ArtifactRecord from a decoded JSON dict.

    Args:
        payload: Decoded JSON dict.
    """
    visibility_state = str(payload.get("visibility_state", "committed"))
    if visibility_state not in {"pending", "committed"}:
        visibility_state = "committed"
    return ArtifactRecord(
        server_id=str(payload["server_id"]),
        session_id=str(payload["session_id"]),
        artifact_id=str(payload["artifact_id"]),
        filename=str(payload["filename"]),
        abs_path=Path(str(payload["abs_path"])),
        rel_path=str(payload["rel_path"]),
        mime_type=str(payload["mime_type"]),
        size_bytes=int(payload["size_bytes"]),
        created_at=float(payload["created_at"]),
        last_accessed=float(payload["last_accessed"]),
        last_updated=float(payload["last_updated"]),
        tool_name=payload.get("tool_name"),
        expose_as_resource=bool(payload.get("expose_as_resource", True)),
        visibility_state=visibility_state,
    )


def session_state_from_payload(payload: dict[str, Any]) -> SessionState:
    """Build a SessionState instance from a decoded JSON payload.

    Args:
        payload: Decoded JSON dict.
    """
    state = SessionState(
        server_id=str(payload["server_id"]),
        session_id=str(payload["session_id"]),
        created_at=float(payload["created_at"]),
        last_accessed=float(payload["last_accessed"]),
        in_flight=int(payload.get("in_flight", 0)),
        trust_context=_session_trust_context_from_payload(payload.get("trust_context")),
        tool_definition_baseline=_tool_definition_baseline_from_payload(payload.get("tool_definition_baseline")),
        tool_definition_drift_summary=_tool_definition_drift_summary_from_payload(payload.get("tool_definition_drift_summary")),
    )
    for upload_payload in payload.get("uploads", []):
        record = _upload_record_from_payload(upload_payload)
        state.uploads[record.upload_id] = record
    for artifact_payload in payload.get("artifacts", []):
        record = _artifact_record_from_payload(artifact_payload)
        state.artifacts[record.artifact_id] = record
    return state


def tombstone_to_payload(tombstone: SessionTombstone) -> dict[str, Any]:
    """Build a JSON-serializable payload from a SessionTombstone instance.

    Args:
        tombstone: Tombstone to serialize.
    """
    return {
        "expires_at": tombstone.expires_at,
        "terminal_reason": tombstone.terminal_reason,
        "state": session_state_to_payload(tombstone.state),
    }


def tombstone_from_payload(payload: dict[str, Any]) -> SessionTombstone:
    """Build a SessionTombstone from a decoded JSON payload.

    Args:
        payload: Decoded JSON dict.
    """
    return SessionTombstone(
        state=session_state_from_payload(payload["state"]),
        expires_at=float(payload["expires_at"]),
        terminal_reason=str(payload["terminal_reason"]) if payload.get("terminal_reason") is not None else None,
    )


def dumps_payload(payload: dict[str, Any]) -> str:
    """Encode a payload dictionary as compact JSON.

    Args:
        payload: Dict to serialize.
    """
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def loads_payload(raw: str) -> dict[str, Any]:
    """Decode a JSON payload string into a dictionary.

    Args:
        raw: JSON string to decode.
    """
    return json.loads(raw)
