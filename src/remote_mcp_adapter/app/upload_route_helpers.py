"""Shared helper functions for upload and artifact HTTP routes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException, UploadFile, status

logger = logging.getLogger(__name__)


def normalize_sha256_inputs(sha256_values: list[str] | None) -> list[str]:
    """Trim and keep non-empty SHA values from multipart form inputs.

    Args:
        sha256_values: Raw SHA-256 values from the request, possibly None.

    Returns:
        List of stripped, non-empty SHA-256 strings.
    """
    if not sha256_values:
        return []
    return [value.strip() for value in sha256_values if value and value.strip()]


def validate_sha256_count(*, files_count: int, sha256_values: list[str]) -> None:
    """Ensure provided SHA list matches uploaded file count when set.

    Args:
        files_count: Number of uploaded files in the batch.
        sha256_values: Normalized SHA-256 values (may be empty).

    Raises:
        HTTPException: With 400 status if counts do not match.
    """
    if not sha256_values:
        return
    if len(sha256_values) != files_count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"sha256 count mismatch: got {len(sha256_values)} value(s) for {files_count} file(s). "
                "Provide one sha256 per uploaded file or omit sha256."
            ),
        )


def build_upload_response_payload(*, server_id: str, session_id: str, uri_scheme: str, records) -> dict[str, object]:
    """Build upload response payload for one or many uploaded files.

    Args:
        server_id: Identifier of the upstream server.
        session_id: Current MCP session identifier.
        uri_scheme: Upload URI scheme prefix.
        records: Sequence of ``UploadRecord`` instances.

    Returns:
        Response dict containing upload handles, metadata per file, and count.
    """
    uploads: list[dict[str, object]] = []
    upload_handles: list[str] = []
    for record in records:
        upload_handle = f"{uri_scheme}sessions/{session_id}/{record.upload_id}"
        upload_handles.append(upload_handle)
        uploads.append(
            {
                "upload_handle": upload_handle,
                "upload_id": record.upload_id,
                "filename": record.filename,
                "mime_type": record.mime_type,
                "size_bytes": record.size_bytes,
                "sha256": record.sha256,
            }
        )

    payload: dict[str, object] = {
        "server_id": server_id,
        "session_id": session_id,
        "uploads": uploads,
        "upload_handles": upload_handles,
        "count": len(uploads),
    }

    if len(uploads) == 1:
        only_upload = uploads[0]
        payload["upload_handle"] = only_upload["upload_handle"]
        payload["upload"] = {
            "upload_id": only_upload["upload_id"],
            "filename": only_upload["filename"],
            "mime_type": only_upload["mime_type"],
            "size_bytes": only_upload["size_bytes"],
            "sha256": only_upload["sha256"],
        }
    return payload


async def save_one_uploaded_file(
    *,
    persist_upload_stream,
    resolved_config,
    session_store,
    server_id: str,
    session_id: str,
    uploaded_file: UploadFile,
    expected_sha256: str | None,
):
    """Persist one multipart UploadFile and return its UploadRecord.

    Args:
        persist_upload_stream: Callable to persist the stream to disk.
        resolved_config: Full adapter configuration.
        session_store: Session store for upload registration.
        server_id: Identifier of the upstream server.
        session_id: Current MCP session identifier.
        uploaded_file: FastAPI ``UploadFile`` from the multipart request.
        expected_sha256: Optional expected SHA-256 digest for integrity.

    Returns:
        ``UploadRecord`` for the persisted file.
    """
    return await persist_upload_stream(
        config=resolved_config,
        store=session_store,
        server_id=server_id,
        session_id=session_id,
        filename=uploaded_file.filename or "upload",
        stream=uploaded_file.file,
        mime_type=uploaded_file.content_type,
        sha256_expected=expected_sha256,
    )


async def rollback_successful_uploads(
    *,
    session_store,
    server_id: str,
    session_id: str,
    records,
) -> None:
    """Rollback uploaded records/files from a failed multi-file batch.

    Best-effort removal: logs warnings on partial or failed rollbacks.

    Args:
        session_store: Session store managing upload state.
        server_id: Identifier of the upstream server.
        session_id: Current MCP session identifier.
        records: Sequence of ``UploadRecord`` instances to remove.
    """
    if not records:
        return
    upload_ids = [record.upload_id for record in records]
    try:
        removed_count = await session_store.remove_uploads(
            server_id=server_id,
            session_id=session_id,
            upload_ids=upload_ids,
        )
    except Exception:
        logger.exception(
            "Failed to rollback multi-file upload batch",
            extra={"server_id": server_id, "session_id": session_id, "upload_ids": upload_ids},
        )
        return
    if removed_count != len(upload_ids):
        logger.warning(
            "Partial rollback for multi-file upload batch",
            extra={
                "server_id": server_id,
                "session_id": session_id,
                "expected_removed": len(upload_ids),
                "actual_removed": removed_count,
            },
        )


def raise_unknown_server_if_missing(*, server_id: str, proxy_map) -> None:
    """Raise 404 when the requested server_id is not configured.

    Args:
        server_id: Requested server identifier.
        proxy_map: Mapping of known server IDs to ``ProxyMount`` instances.

    Raises:
        HTTPException: With 404 status if *server_id* is unknown.
    """
    if server_id in proxy_map:
        return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown server id: {server_id}")


def raise_fail_closed_if_rejecting(*, persistence_policy) -> None:
    """Raise 503 when fail-closed policy is currently rejecting stateful requests.

    Args:
        persistence_policy: Policy controller to query.

    Raises:
        HTTPException: With 503 status when policy is in reject mode.
    """
    if not persistence_policy.should_reject_stateful_requests():
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Persistence backend unavailable under fail-closed policy.",
    )


async def close_uploaded_files(files: list[UploadFile]) -> None:
    """Close uploaded multipart files and log any close failures.

    Args:
        files: List of FastAPI ``UploadFile`` objects to close.
    """
    close_results = await asyncio.gather(
        *(uploaded_file.close() for uploaded_file in files),
        return_exceptions=True,
    )
    for close_result in close_results:
        if isinstance(close_result, Exception):
            logger.warning("Failed to close uploaded multipart file cleanly", exc_info=close_result)


async def record_upload_batch_metrics(*, telemetry, server_id: str, records) -> None:
    """Emit upload batch metrics when telemetry is enabled.

    Args:
        telemetry: Optional telemetry recorder (no-op when None or disabled).
        server_id: Identifier of the upstream server.
        records: Sequence of ``UploadRecord`` instances in the batch.
    """
    if telemetry is None or not getattr(telemetry, "enabled", False):
        return
    await telemetry.record_upload_batch(
        server_id=server_id,
        file_count=len(records),
        bytes_total=sum(int(record.size_bytes) for record in records),
    )


async def record_upload_failure_metrics(*, telemetry, server_id: str, reason: str) -> None:
    """Emit upload failure metrics when telemetry is enabled.

    Args:
        telemetry: Optional telemetry recorder (no-op when None or disabled).
        server_id: Identifier of the upstream server.
        reason: Failure reason label.
    """
    if telemetry is None or not getattr(telemetry, "enabled", False):
        return
    await telemetry.record_upload_failure(
        server_id=server_id,
        reason=reason,
    )
