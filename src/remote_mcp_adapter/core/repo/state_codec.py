"""Serialization helpers for session/tombstone persistence payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .records import ArtifactRecord, SessionState, SessionTombstone, UploadRecord


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
    }


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
