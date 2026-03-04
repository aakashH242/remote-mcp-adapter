"""Upload stream persistence utilities."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from pathlib import Path
import shutil
from typing import IO

from fastmcp.exceptions import ToolError

from ...config import AdapterConfig, resolve_write_policy_lock_mode
from ..repo.records import UploadRecord
from .store import SessionStore
from .write_policy import (
    cleanup_staged_path,
    commit_staged_path,
    create_staged_path,
    storage_write_lock,
)


def _normalize_expected_sha256(value: str | None) -> str | None:
    """Lowercase and strip the expected digest, returning None for blank input.

    Args:
        value: SHA-256 hex digest string, or None.

    Returns:
        Normalized lowercase digest, or None.
    """
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _cleanup_dir(path: Path) -> None:
    """Best-effort recursive removal of a temporary staging directory.

    Args:
        path: Directory to remove.
    """
    shutil.rmtree(path, ignore_errors=True)


def _persist_upload_stream_sync(*, stream: IO[bytes], staged_path: Path, max_file_bytes: int) -> str:
    """Persist stream bytes to ``staged_path`` and return lowercase sha256 digest.

    Args:
        stream: Binary readable stream.
        staged_path: Filesystem path to write to.
        max_file_bytes: Maximum allowed file size.

    Returns:
        Lowercase hex SHA-256 digest of the written data.

    Raises:
        ToolError: If the upload exceeds *max_file_bytes*.
    """
    if hasattr(stream, "seek"):
        stream.seek(0)

    written_bytes = 0
    hasher = hashlib.sha256()
    with staged_path.open("wb") as write_handle:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            written_bytes += len(chunk)
            if written_bytes > max_file_bytes:
                raise ToolError("Upload exceeds uploads.max_file_bytes.")
            hasher.update(chunk)
            write_handle.write(chunk)
        write_handle.flush()
        with contextlib.suppress(OSError):
            os.fsync(write_handle.fileno())
    return hasher.hexdigest().lower()


async def save_upload_stream(
    *,
    config: AdapterConfig,
    store: SessionStore,
    server_id: str,
    session_id: str,
    filename: str,
    stream: IO[bytes],
    mime_type: str | None = None,
    sha256_expected: str | None = None,
) -> UploadRecord:
    """Persist a multipart upload stream to session-scoped disk storage.

    Args:
        config: Full adapter configuration.
        store: Session store managing the session.
        server_id: Server identifier.
        session_id: Session identifier.
        filename: Client-supplied filename.
        stream: Binary readable stream.
        mime_type: Optional MIME type override.
        sha256_expected: Optional expected SHA-256 for integrity check.

    Returns:
        The registered ``UploadRecord``.

    Raises:
        ToolError: If uploads are disabled, SHA-256 is required but missing,
            or the digest does not match.
    """
    if not config.uploads.enabled:
        raise ToolError("Uploads are disabled.")

    expected_sha256 = _normalize_expected_sha256(sha256_expected)
    if config.uploads.require_sha256 and expected_sha256 is None:
        raise ToolError("sha256 is required for uploads.")

    upload_id, abs_path, _ = await store.allocate_upload_path(
        server_id=server_id,
        session_id=session_id,
        filename=filename,
    )

    staged_path = create_staged_path(abs_path, config.storage.atomic_writes)
    try:
        try:
            write_lock_mode = resolve_write_policy_lock_mode(config)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        async with storage_write_lock(abs_path, write_lock_mode):
            digest = await asyncio.to_thread(
                _persist_upload_stream_sync,
                stream=stream,
                staged_path=staged_path,
                max_file_bytes=config.uploads.max_file_bytes,
            )
            await asyncio.to_thread(commit_staged_path, staged_path, abs_path, config.storage.atomic_writes)
    except Exception:
        await asyncio.to_thread(cleanup_staged_path, staged_path, abs_path, config.storage.atomic_writes)
        await asyncio.to_thread(_cleanup_dir, abs_path.parent)
        raise

    if expected_sha256 is not None and digest != expected_sha256:
        await asyncio.to_thread(abs_path.unlink, missing_ok=True)
        await asyncio.to_thread(_cleanup_dir, abs_path.parent)
        raise ToolError("sha256 mismatch for uploaded file.")

    return await store.register_upload(
        server_id=server_id,
        session_id=session_id,
        upload_id=upload_id,
        abs_path=abs_path,
        mime_type=mime_type,
        sha256=digest,
    )
