"""Shared artifact access helpers for HTTP routes and resource providers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..repo.records import ArtifactRecord
    from .store import SessionStore


class ArtifactSessionMismatchError(ValueError):
    """Raised when an artifact operation targets a different session than expected."""


class ArtifactNotFoundError(LookupError):
    """Raised when the requested artifact record cannot be resolved."""


class ArtifactFileMissingError(FileNotFoundError):
    """Raised when the artifact record exists but its file is missing on disk."""


class ArtifactFilenameMismatchError(ValueError):
    """Raised when the requested filename does not match stored artifact metadata."""


def ensure_artifact_session_match(*, expected_session_id: str, actual_session_id: str) -> None:
    """Validate that artifact session matches expected caller session.

    Args:
        expected_session_id: Session the caller expects.
        actual_session_id: Session the artifact belongs to.

    Raises:
        ArtifactSessionMismatchError: On mismatch.
    """
    if expected_session_id != actual_session_id:
        raise ArtifactSessionMismatchError("Artifact session mismatch.")


async def resolve_artifact_for_read(
    *,
    store: SessionStore,
    server_id: str,
    session_id: str,
    artifact_id: str,
    expected_filename: str | None = None,
) -> ArtifactRecord:
    """Resolve committed artifact and validate on-disk existence and optional filename.

    Args:
        store: Session store providing artifact lookups.
        server_id: Server identifier.
        session_id: Session identifier.
        artifact_id: Artifact to resolve.
        expected_filename: Optional filename to validate against.

    Returns:
        The resolved ``ArtifactRecord``.

    Raises:
        ArtifactNotFoundError: If the artifact record cannot be found.
        ArtifactFileMissingError: If the file is missing on disk.
        ArtifactFilenameMismatchError: If the filename does not match.
    """
    try:
        record = await store.get_artifact(
            server_id=server_id,
            session_id=session_id,
            artifact_id=artifact_id,
        )
    except KeyError as exc:
        raise ArtifactNotFoundError("Artifact not found.") from exc

    if not await asyncio.to_thread(record.abs_path.exists):
        raise ArtifactFileMissingError("Artifact file missing.")

    if expected_filename is not None and expected_filename != record.filename:
        raise ArtifactFilenameMismatchError("Artifact filename mismatch.")

    return record
