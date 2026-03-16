"""Session, upload, artifact, cleanup, and quota state management."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
import logging
import mimetypes
from pathlib import Path
import shutil
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from fastmcp.exceptions import ToolError

from ...config import AdapterConfig
from ...session_integrity.models import SessionTrustContext
from ..locks.lock_provider import InMemoryLockProvider, LockProvider
from ..repo.records import ArtifactRecord, SessionState, SessionTombstone, UploadRecord, now_ts
from ..repo.records import ToolDefinitionBaseline, ToolDefinitionDriftSummary
from ..repo.state_repository import InMemoryStateRepository, SessionKey, StateRepository
from .errors import SessionTrustContextMismatchError, TerminalSessionInvalidatedError
from .store_ops import StoreOps
from .storage_utils import (
    ARTIFACT_URI_RE,
    UPLOAD_HANDLE_RE,
    ensure_within_base,
    parse_session_scoped_uri,
    sanitize_filename,
    sha256_file,
)

if TYPE_CHECKING:
    from ...telemetry import TelemetryManager

logger = logging.getLogger(__name__)


def _file_size_bytes(path: Path) -> int:
    """Return the file size in bytes.

    Args:
        path: Filesystem path to measure.
    """
    return path.stat().st_size


def _preferred_extension_for_mime(mime_type: str | None) -> str | None:
    """Return a normalized file extension for a MIME type.

    Args:
        mime_type: MIME type string, or None.

    Returns:
        File extension string (e.g. ``'.jpg'``), or None if no mapping exists.
    """
    if not mime_type or mime_type == "application/octet-stream":
        return None
    ext = mimetypes.guess_extension(mime_type, strict=False)
    if ext == ".jpe":
        return ".jpg"
    return ext


class SessionStore:
    """Server-aware state manager for sessions, uploads, and artifacts."""

    _STATE_LOCK_NAME = "session_store_state"

    def __init__(
        self,
        config: AdapterConfig,
        *,
        state_repository: StateRepository | None = None,
        lock_provider: LockProvider | None = None,
        telemetry: TelemetryManager | None = None,
    ):
        """Initialize the session store.

        Args:
            config: Full adapter configuration.
            state_repository: Optional persistence backend (defaults to in-memory).
            lock_provider: Optional lock provider (defaults to in-memory).
            telemetry: Optional telemetry recorder.
        """
        self._config = config
        self._storage_root = Path(config.storage.root).resolve()
        self._uploads_root = self._storage_root / "uploads" / "sessions"
        self._artifacts_root = self._storage_root / "artifacts" / "sessions"
        self._state_repository = state_repository or InMemoryStateRepository()
        self._lock_provider = lock_provider or InMemoryLockProvider()
        self._telemetry = telemetry
        self._ops = StoreOps(
            config=config,
            storage_root=self._storage_root,
            upload_session_dir=self.upload_session_dir,
            artifact_session_dir=self.artifact_session_dir,
        )

    @property
    def storage_root(self) -> Path:
        """Resolved absolute path to the shared storage root directory."""
        return self._storage_root

    def upload_session_dir(self, session_id: str) -> Path:
        """Return per-session uploads directory path.

        Args:
            session_id: Session identifier.
        """
        return self._uploads_root / session_id

    def artifact_session_dir(self, session_id: str) -> Path:
        """Return per-session artifacts directory path.

        Args:
            session_id: Session identifier.
        """
        return self._artifacts_root / session_id

    @staticmethod
    def _session_key(server_id: str, session_id: str) -> SessionKey:
        """Build the canonical (server_id, session_id) repository key.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
        """
        return (server_id, session_id)

    @staticmethod
    def _artifact_is_committed(record: ArtifactRecord) -> bool:
        """Return True when the artifact has been fully written and committed.

        Args:
            record: Artifact record to check.
        """
        return record.visibility_state == "committed"

    def replace_backends(self, *, state_repository: StateRepository, lock_provider: LockProvider) -> None:
        """Replace persistence backends for policy-driven runtime fallback.

        Args:
            state_repository: New state repository to use.
            lock_provider: New lock provider to use.
        """
        self._state_repository = state_repository
        self._lock_provider = lock_provider

    async def _persist_session_state(self, state: SessionState) -> None:
        """Persist one session state through the configured repository.

        Args:
            state: Session state to persist.
        """
        key = self._session_key(state.server_id, state.session_id)
        await self._state_repository.set_session(key, state)

    async def ensure_session(self, server_id: str, session_id: str) -> SessionState:
        """Get or create state for ``(server_id, session_id)``.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.

        Returns:
            Existing or newly created ``SessionState``.

        Raises:
            ToolError: If the maximum active sessions limit is exceeded.
        """
        key = self._session_key(server_id, session_id)
        now = now_ts()
        async with self._lock_provider.hold(self._STATE_LOCK_NAME):
            tombstone = await self._state_repository.get_tombstone(key)
            if tombstone is not None:
                if tombstone.terminal_reason is not None:
                    if tombstone.expires_at > now:
                        raise TerminalSessionInvalidatedError(self._terminal_session_message(tombstone.terminal_reason))
                    await self._state_repository.pop_tombstone(key)
                    logger.info(
                        "Expired terminal session tombstone removed during session lookup",
                        extra={"server_id": server_id, "session_id": session_id},
                    )
                elif tombstone.expires_at > now:
                    state = tombstone.state
                    state.touch(now)
                    await self._persist_session_state(state)
                    await self._state_repository.pop_tombstone(key)
                    logger.info(
                        "Session revived from tombstone",
                        extra={"server_id": server_id, "session_id": session_id},
                    )
                    if self._telemetry is not None:
                        await self._telemetry.record_session_lifecycle(event="revived", server_id=server_id)
                    return state
                else:
                    await self._state_repository.pop_tombstone(key)
                    logger.info(
                        "Expired tombstone removed during session lookup",
                        extra={"server_id": server_id, "session_id": session_id},
                    )

            state = await self._state_repository.get_session(key)
            if state is None:
                max_active = self._config.sessions.max_active
                active_sessions = await self._state_repository.session_count()
                if max_active is not None and active_sessions >= max_active:
                    raise ToolError("Maximum active sessions exceeded.")
                state = SessionState(server_id=server_id, session_id=session_id, created_at=now, last_accessed=now)
                await self._persist_session_state(state)
                logger.info(
                    "Session created",
                    extra={"server_id": server_id, "session_id": session_id},
                )
                if self._telemetry is not None:
                    await self._telemetry.record_session_lifecycle(event="created", server_id=server_id)
            return state

    @staticmethod
    def _terminal_session_message(reason: str) -> str:
        """Build the session-invalidated message shown to clients.

        Args:
            reason: Human-readable invalidation reason.

        Returns:
            Error message instructing the client to start a new session.
        """
        return (
            f"{reason} This adapter session was invalidated. "
            "Start a new Mcp-Session-Id to accept the updated upstream state."
        )

    async def get_session(self, server_id: str, session_id: str) -> SessionState | None:
        """Return existing session state, if any.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
        """
        async with self._lock_provider.hold(self._STATE_LOCK_NAME):
            return await self._state_repository.get_session(self._session_key(server_id, session_id))

    async def get_session_trust_context(self, server_id: str, session_id: str) -> SessionTrustContext | None:
        """Return the bound trust context for one session, if any.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.

        Returns:
            Bound trust context or ``None`` when the session has not bound one.
        """
        state = await self.get_session(server_id, session_id)
        if state is None:
            return None
        return state.trust_context

    async def get_terminal_session_reason(self, server_id: str, session_id: str) -> str | None:
        """Return the terminal invalidation reason for a session, if any.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.

        Returns:
            Terminal invalidation reason or ``None``.
        """
        key = self._session_key(server_id, session_id)
        now = now_ts()
        async with self._lock_provider.hold(self._STATE_LOCK_NAME):
            tombstone = await self._state_repository.get_tombstone(key)
            if tombstone is None or tombstone.terminal_reason is None:
                return None
            if tombstone.expires_at <= now:
                await self._state_repository.pop_tombstone(key)
                logger.info(
                    "Expired terminal session tombstone removed during reason lookup",
                    extra={"server_id": server_id, "session_id": session_id},
                )
                return None
            return tombstone.terminal_reason

    async def invalidate_session(
        self,
        *,
        server_id: str,
        session_id: str,
        reason: str,
    ) -> None:
        """Invalidate one adapter session and block reuse of the same session id.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            reason: Human-readable invalidation reason.
        """
        key = self._session_key(server_id, session_id)
        now = now_ts()
        async with self._lock_provider.hold(self._STATE_LOCK_NAME):
            existing_tombstone = await self._state_repository.get_tombstone(key)
            if existing_tombstone is not None and existing_tombstone.terminal_reason == reason:
                return

            state = await self._state_repository.pop_session(key)
            if state is None:
                state = (
                    existing_tombstone.state
                    if existing_tombstone is not None
                    else SessionState(
                        server_id=server_id,
                        session_id=session_id,
                        created_at=now,
                        last_accessed=now,
                    )
                )
            await self._state_repository.pop_tombstone(key)
            await self._ops.purge_state_files_async(state)
            state.uploads.clear()
            state.artifacts.clear()
            state.in_flight = 0
            state.touch(now)
            expires_at = now + self._config.sessions.tombstone_ttl_seconds
            await self._state_repository.set_tombstone(
                key,
                SessionTombstone(
                    state=state,
                    expires_at=expires_at,
                    terminal_reason=reason,
                ),
            )

        logger.warning(
            "Session invalidated",
            extra={"server_id": server_id, "session_id": session_id, "reason": reason},
        )
        if self._telemetry is not None:
            await self._telemetry.record_session_lifecycle(event="tool_definition_invalidated", server_id=server_id)

    async def bind_or_validate_session_trust_context(
        self,
        *,
        server_id: str,
        session_id: str,
        trust_context: SessionTrustContext,
    ) -> None:
        """Bind or validate the adapter trust context for one session.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            trust_context: Request-derived trust context to enforce.

        Raises:
            SessionTrustContextMismatchError: If the request does not match the
                trust context already bound to the session.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            existing = state.trust_context
            if existing is None:
                state.trust_context = trust_context
                state.touch()
                await self._persist_session_state(state)
                if self._telemetry is not None:
                    await self._telemetry.record_session_lifecycle(event="auth_context_bound", server_id=server_id)
                return
            if existing == trust_context:
                return
        raise SessionTrustContextMismatchError(self._session_trust_context_mismatch_message())

    @staticmethod
    def _session_trust_context_mismatch_message() -> str:
        """Build the client-facing error for a bound session-context mismatch.

        Returns:
            Error message instructing the client to reuse the same auth context
            or start a new adapter session.
        """
        return (
            "This adapter session is already bound to a different authenticated request context. "
            "Retry with the same adapter auth token that established the session, or start a new Mcp-Session-Id."
        )

    async def get_tool_definition_baseline(self, server_id: str, session_id: str) -> ToolDefinitionBaseline | None:
        """Return the pinned tool-definition baseline for one session, if present.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.

        Returns:
            Pinned baseline or ``None`` when the session has not pinned one yet.
        """
        state = await self.get_session(server_id, session_id)
        if state is None:
            return None
        return state.tool_definition_baseline

    async def set_tool_definition_baseline(
        self,
        server_id: str,
        session_id: str,
        baseline: ToolDefinitionBaseline,
    ) -> None:
        """Persist the pinned tool-definition baseline for one session.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            baseline: Baseline to persist.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            state.tool_definition_baseline = baseline
            state.touch()
            await self._persist_session_state(state)

    async def get_tool_definition_drift_summary(self, server_id: str, session_id: str) -> ToolDefinitionDriftSummary | None:
        """Return the last drift summary for one session, if present.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.

        Returns:
            Stored drift summary or ``None``.
        """
        state = await self.get_session(server_id, session_id)
        if state is None:
            return None
        return state.tool_definition_drift_summary

    async def set_tool_definition_drift_summary(
        self,
        server_id: str,
        session_id: str,
        summary: ToolDefinitionDriftSummary,
    ) -> None:
        """Persist the last tool-definition drift summary for one session.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            summary: Drift summary to persist.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            state.tool_definition_drift_summary = summary
            state.touch()
            await self._persist_session_state(state)

    async def clear_tool_definition_drift_summary(self, server_id: str, session_id: str) -> None:
        """Clear any persisted tool-definition drift summary for one session.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
        """
        state = await self.get_session(server_id, session_id)
        if state is None:
            return
        async with state.lock:
            if state.tool_definition_drift_summary is None:
                return
            state.tool_definition_drift_summary = None
            state.touch()
            await self._persist_session_state(state)

    async def touch_tool_activity(self, server_id: str, session_id: str) -> None:
        """Touch session activity for tool-call related operations.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            state.touch()
            await self._persist_session_state(state)

    async def begin_in_flight(self, server_id: str, session_id: str) -> None:
        """Increment in-flight counter for cleanup decisions.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.

        Raises:
            ToolError: If the per-session in-flight limit is exceeded.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            max_in_flight = self._config.sessions.max_in_flight_per_session
            if max_in_flight is not None and state.in_flight >= max_in_flight:
                raise ToolError("Maximum in-flight requests exceeded for session.")
            state.in_flight += 1
            state.touch()
            await self._persist_session_state(state)

    async def end_in_flight(self, server_id: str, session_id: str) -> None:
        """Decrement in-flight counter for cleanup decisions.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            state.in_flight = max(0, state.in_flight - 1)
            state.touch()
            await self._persist_session_state(state)

    async def allocate_upload_path(
        self,
        *,
        server_id: str,
        session_id: str,
        filename: str,
        upload_id: str | None = None,
    ) -> tuple[str, Path, str]:
        """Allocate storage path for a staged upload.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            filename: Client-supplied filename.
            upload_id: Optional explicit upload ID.

        Returns:
            Tuple of ``(upload_id, abs_path, rel_path)``.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            normalized_upload_id = upload_id or uuid4().hex
            safe_name = sanitize_filename(filename, default_name="upload")
            upload_dir = self.upload_session_dir(session_id) / normalized_upload_id
            abs_path = ensure_within_base(upload_dir / safe_name, self._storage_root)
            await asyncio.to_thread(abs_path.parent.mkdir, parents=True, exist_ok=True)
            rel_path = abs_path.relative_to(self._storage_root).as_posix()
            state.touch()
            await self._persist_session_state(state)
            return normalized_upload_id, abs_path, rel_path

    async def register_upload(
        self,
        *,
        server_id: str,
        session_id: str,
        upload_id: str,
        abs_path: Path,
        mime_type: str | None = None,
        sha256: str | None = None,
    ) -> UploadRecord:
        """Register a completed upload in session state.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            upload_id: Unique upload identifier.
            abs_path: Absolute filesystem path of the uploaded file.
            mime_type: Optional MIME type override.
            sha256: Optional pre-computed SHA-256 digest.

        Returns:
            The registered ``UploadRecord``.

        Raises:
            FileNotFoundError: If the upload file does not exist.
            ToolError: If quota limits are exceeded.
        """
        state = await self.ensure_session(server_id, session_id)
        abs_path = ensure_within_base(abs_path, self._storage_root)
        if not await asyncio.to_thread(abs_path.exists):
            raise FileNotFoundError(f"Upload file not found: {abs_path}")

        size_bytes = await asyncio.to_thread(_file_size_bytes, abs_path)
        now = now_ts()
        digest = sha256 or await asyncio.to_thread(sha256_file, abs_path)
        guessed_mime = mime_type or mimetypes.guess_type(abs_path.name)[0] or "application/octet-stream"
        record = UploadRecord(
            server_id=server_id,
            session_id=session_id,
            upload_id=upload_id,
            filename=abs_path.name,
            abs_path=abs_path,
            rel_path=abs_path.relative_to(self._storage_root).as_posix(),
            mime_type=guessed_mime,
            size_bytes=size_bytes,
            sha256=digest,
            created_at=now,
            last_accessed=now,
            last_updated=now,
        )
        async with state.lock:
            try:
                self._ops.enforce_session_quota(state, incoming_bytes=size_bytes)
                await self._ops.enforce_global_storage_quota()
            except ToolError:
                await self._persist_session_state(state)
                await asyncio.to_thread(abs_path.unlink, missing_ok=True)
                with contextlib.suppress(OSError):
                    await asyncio.to_thread(abs_path.parent.rmdir)
                raise
            state.uploads[upload_id] = record
            state.touch(now)
            await self._persist_session_state(state)
        return record

    async def resolve_upload_handle(
        self,
        *,
        server_id: str,
        session_id: str,
        handle: str,
        uri_scheme: str = "upload://",
    ) -> UploadRecord:
        """Resolve and touch an ``upload://`` handle for the current session.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            handle: Upload handle string.
            uri_scheme: URI scheme prefix for parsing.

        Returns:
            The resolved ``UploadRecord``.

        Raises:
            ValueError: If the handle session does not match.
            KeyError: If the upload ID is unknown.
        """
        match = UPLOAD_HANDLE_RE.fullmatch(handle)
        if match:
            handle_session_id, upload_id = match.group(1), match.group(2)
        else:
            handle_session_id, upload_id = parse_session_scoped_uri(handle, uri_scheme)
        if handle_session_id != session_id:
            raise ValueError("Upload handle session mismatch.")

        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            record = state.uploads.get(upload_id)
            if record is None:
                raise KeyError(f"Unknown upload handle id: {upload_id}")
            record.touch()
            state.touch(record.last_accessed)
            await self._persist_session_state(state)
            return record

    async def remove_uploads(
        self,
        *,
        server_id: str,
        session_id: str,
        upload_ids: list[str],
    ) -> int:
        """Remove multiple upload records/files and persist state once.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            upload_ids: List of upload IDs to remove.

        Returns:
            Number of uploads actually removed.
        """
        if not upload_ids:
            return 0
        state = await self.ensure_session(server_id, session_id)
        removed_count = 0
        async with state.lock:
            for upload_id in upload_ids:
                removed, _ = await self._ops.remove_upload_record_async(state, upload_id)
                if removed:
                    removed_count += 1
            if removed_count:
                state.touch()
                await self._persist_session_state(state)
        if removed_count:
            await self._ops.purge_empty_session_dirs_async(session_id)
            logger.info(
                "Removed upload batch",
                extra={"server_id": server_id, "session_id": session_id, "removed_count": removed_count},
            )
        return removed_count

    async def allocate_artifact_path(
        self,
        *,
        server_id: str,
        session_id: str,
        filename: str | None,
        default_ext: str | None = None,
        artifact_id: str | None = None,
        tool_name: str | None = None,
        expose_as_resource: bool = True,
    ) -> tuple[str, Path, str]:
        """Allocate and pre-register path for an artifact output file.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            filename: Optional client-supplied filename.
            default_ext: Fallback file extension.
            artifact_id: Optional explicit artifact ID.
            tool_name: Name of the tool producing this artifact.
            expose_as_resource: Whether to expose the artifact as an MCP resource.

        Returns:
            Tuple of ``(artifact_id, abs_path, rel_path)``.

        Raises:
            ToolError: If the per-session artifact limit is exceeded.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            max_per_session = self._config.artifacts.max_per_session
            if max_per_session is not None and len(state.artifacts) >= max_per_session:
                raise ToolError("Maximum artifacts per session exceeded.")
            normalized_artifact_id = artifact_id or uuid4().hex
            default_name = f"artifact-{normalized_artifact_id}"
            safe_name = sanitize_filename(filename, default_name=default_name, default_ext=default_ext)
            artifact_dir = self.artifact_session_dir(session_id) / normalized_artifact_id
            abs_path = ensure_within_base(artifact_dir / safe_name, self._storage_root)
            await asyncio.to_thread(abs_path.parent.mkdir, parents=True, exist_ok=True)
            rel_path = abs_path.relative_to(self._storage_root).as_posix()
            now = now_ts()
            record = ArtifactRecord(
                server_id=server_id,
                session_id=session_id,
                artifact_id=normalized_artifact_id,
                filename=safe_name,
                abs_path=abs_path,
                rel_path=rel_path,
                mime_type=mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
                size_bytes=0,
                created_at=now,
                last_accessed=now,
                last_updated=now,
                tool_name=tool_name,
                expose_as_resource=expose_as_resource,
                visibility_state="pending",
            )
            state.artifacts[normalized_artifact_id] = record
            state.touch(now)
            await self._persist_session_state(state)
            return normalized_artifact_id, abs_path, rel_path

    async def finalize_artifact(
        self,
        *,
        server_id: str,
        session_id: str,
        artifact_id: str,
        mime_type: str | None = None,
    ) -> ArtifactRecord:
        """Finalize artifact metadata after file materialization.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            artifact_id: Artifact to finalize.
            mime_type: Optional MIME type override.

        Returns:
            The finalized ``ArtifactRecord``.

        Raises:
            KeyError: If the artifact ID is unknown.
            FileNotFoundError: If the artifact file is missing.
            ToolError: If quota limits are exceeded after finalization.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            record = state.artifacts.get(artifact_id)
            if record is None:
                raise KeyError(f"Unknown artifact id: {artifact_id}")
            if not await asyncio.to_thread(record.abs_path.exists):
                raise FileNotFoundError(f"Artifact file missing: {record.abs_path}")
            resolved_mime_type = mime_type or mimetypes.guess_type(record.filename)[0] or record.mime_type

            # If upstream wrote to a placeholder name (e.g., "artifact"), attach a
            # stable extension from detected MIME so URIs and downloads are meaningful.
            if not Path(record.filename).suffix:
                inferred_ext = _preferred_extension_for_mime(resolved_mime_type)
                if inferred_ext:
                    candidate_abs_path = ensure_within_base(
                        record.abs_path.with_name(f"{record.filename}{inferred_ext}"),
                        self._storage_root,
                    )
                    candidate_exists = await asyncio.to_thread(candidate_abs_path.exists)
                    if not candidate_exists:
                        await asyncio.to_thread(record.abs_path.rename, candidate_abs_path)
                        record.abs_path = candidate_abs_path
                        record.filename = candidate_abs_path.name
                        record.rel_path = candidate_abs_path.relative_to(self._storage_root).as_posix()

            previous_size = record.size_bytes
            record.size_bytes = await asyncio.to_thread(_file_size_bytes, record.abs_path)
            growth_bytes = max(0, record.size_bytes - previous_size)
            try:
                self._ops.enforce_session_quota(state, incoming_bytes=growth_bytes)
                await self._ops.enforce_global_storage_quota()
            except ToolError:
                self._ops.remove_artifact_record(state, artifact_id)
                await self._persist_session_state(state)
                raise
            record.mime_type = resolved_mime_type
            record.visibility_state = "committed"
            record.touch()
            state.touch(record.last_accessed)
            await self._persist_session_state(state)
            return record

    async def get_artifact(
        self,
        *,
        server_id: str,
        session_id: str,
        artifact_id: str,
        include_pending: bool = False,
    ) -> ArtifactRecord:
        """Resolve and touch one artifact record for a session.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            artifact_id: Artifact to resolve.
            include_pending: When True, return artifacts not yet committed.

        Returns:
            The resolved ``ArtifactRecord``.

        Raises:
            KeyError: If the artifact ID is unknown or not committed.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            record = state.artifacts.get(artifact_id)
            if record is None:
                raise KeyError(f"Unknown artifact id: {artifact_id}")
            if not include_pending and not self._artifact_is_committed(record):
                raise KeyError(f"Unknown artifact id: {artifact_id}")
            record.touch()
            state.touch(record.last_accessed)
            await self._persist_session_state(state)
            return record

    async def list_artifacts(
        self,
        *,
        server_id: str,
        session_id: str,
        touch: bool = False,
    ) -> list[ArtifactRecord]:
        """List artifact records for a session.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            touch: When True, update access timestamps.

        Returns:
            List of committed ``ArtifactRecord`` instances.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            records = [record for record in state.artifacts.values() if self._artifact_is_committed(record)]
            if touch:
                now = now_ts()
                for record in records:
                    record.touch(now)
                state.touch(now)
                await self._persist_session_state(state)
            return records

    async def resolve_artifact_uri(
        self,
        *,
        server_id: str,
        session_id: str,
        artifact_uri: str,
        uri_scheme: str = "artifact://",
    ) -> ArtifactRecord:
        """Resolve and touch an ``artifact://`` URI for the current session.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            artifact_uri: Artifact URI string.
            uri_scheme: URI scheme prefix for parsing.

        Returns:
            The resolved ``ArtifactRecord``.

        Raises:
            ValueError: If the URI session does not match.
        """
        match = ARTIFACT_URI_RE.fullmatch(artifact_uri)
        if match:
            uri_session_id, artifact_id = match.group(1), match.group(2)
        else:
            uri_session_id, artifact_id = parse_session_scoped_uri(artifact_uri, uri_scheme)
        if uri_session_id != session_id:
            raise ValueError("Artifact URI session mismatch.")
        return await self.get_artifact(server_id=server_id, session_id=session_id, artifact_id=artifact_id)

    async def get_session_snapshot(self, server_id: str, session_id: str) -> dict[str, object]:
        """Return lightweight session metadata for diagnostics.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.

        Returns:
            Dict with session timestamps, counts, and in-flight status.
        """
        state = await self.ensure_session(server_id, session_id)
        async with state.lock:
            return {
                "server_id": state.server_id,
                "session_id": state.session_id,
                "created_at": state.created_at,
                "last_accessed": state.last_accessed,
                "created_at_iso": datetime.fromtimestamp(state.created_at, tz=timezone.utc).isoformat(),
                "last_accessed_iso": datetime.fromtimestamp(state.last_accessed, tz=timezone.utc).isoformat(),
                "uploads_count": len(state.uploads),
                "artifacts_count": len(state.artifacts),
                "in_flight": state.in_flight,
            }

    async def iter_sessions(self) -> list[SessionState]:
        """Return a snapshot list of all active session states."""
        async with self._lock_provider.hold(self._STATE_LOCK_NAME):
            session_items = await self._state_repository.list_session_items()
            return [state for _, state in session_items]

    async def _tombstone_or_delete_session(self, key: SessionKey, state: SessionState, now: float) -> str:
        """Apply configured idle-expiry behavior for one session.

        Args:
            key: Session repository key.
            state: Session state to tombstone or delete.
            now: Current epoch timestamp.

        Returns:
            ``'tombstoned'`` or ``'deleted'``.
        """
        if self._config.sessions.allow_revival:
            expires_at = now + self._config.sessions.tombstone_ttl_seconds
            await self._state_repository.set_tombstone(key, SessionTombstone(state=state, expires_at=expires_at))
            return "tombstoned"
        await self._ops.purge_state_files_async(state)
        return "deleted"

    async def cleanup_once(self) -> dict[str, int]:
        """Run one cleanup cycle for TTL expiry and idle session management.

        Returns:
            Dict with counts of expired sessions, removed uploads/artifacts,
            tombstones, and orphan files.
        """
        now = now_ts()
        removed_uploads = 0
        removed_artifacts = 0
        expired_sessions = 0
        tombstoned_sessions = 0
        removed_tombstones = 0
        removed_dangling_upload_records = 0
        removed_dangling_artifact_records = 0
        removed_orphan_files = 0
        referenced_paths: set[str] = set()
        pending_artifact_cutoff = now - self._config.storage.orphan_sweeper_grace_seconds

        async with self._lock_provider.hold(self._STATE_LOCK_NAME):
            session_items = list(await self._state_repository.list_session_items())

            for key, tombstone in list(await self._state_repository.list_tombstone_items()):
                if tombstone.expires_at <= now:
                    await self._state_repository.pop_tombstone(key)
                    await self._ops.purge_state_files_async(tombstone.state)
                    removed_tombstones += 1
                    logger.info(
                        "Expired tombstone purged",
                        extra={
                            "server_id": tombstone.state.server_id,
                            "session_id": tombstone.state.session_id,
                            "expires_at": tombstone.expires_at,
                        },
                    )
                else:
                    for upload in tombstone.state.uploads.values():
                        referenced_paths.add(str(upload.abs_path.resolve()))
                    for artifact in tombstone.state.artifacts.values():
                        referenced_paths.add(str(artifact.abs_path.resolve()))

            for key, state in session_items:
                async with state.lock:
                    state_changed = False
                    session_removed = False
                    for upload in list(state.uploads.values()):
                        if not await asyncio.to_thread(upload.abs_path.exists):
                            removed, _ = await self._ops.remove_upload_record_async(state, upload.upload_id)
                            if removed:
                                removed_dangling_upload_records += 1
                                state_changed = True

                    for artifact in list(state.artifacts.values()):
                        is_pending_stale = (
                            artifact.visibility_state != "committed" and artifact.last_updated < pending_artifact_cutoff
                        )
                        if is_pending_stale or not await asyncio.to_thread(artifact.abs_path.exists):
                            removed, _ = await self._ops.remove_artifact_record_async(state, artifact.artifact_id)
                            if removed:
                                removed_dangling_artifact_records += 1
                                state_changed = True

                    upload_ttl = self._config.uploads.ttl_seconds
                    if upload_ttl is not None:
                        cutoff = now - upload_ttl
                        for upload in list(state.uploads.values()):
                            if upload.last_accessed < cutoff:
                                removed, _ = await self._ops.remove_upload_record_async(state, upload.upload_id)
                                if removed:
                                    removed_uploads += 1
                                    state_changed = True

                    artifact_ttl = self._config.artifacts.ttl_seconds
                    if artifact_ttl is not None:
                        cutoff = now - artifact_ttl
                        for artifact in list(state.artifacts.values()):
                            if artifact.last_accessed < cutoff:
                                removed, _ = await self._ops.remove_artifact_record_async(state, artifact.artifact_id)
                                if removed:
                                    removed_artifacts += 1
                                    state_changed = True

                    await self._ops.purge_empty_session_dirs_async(state.session_id)
                    idle_ttl = self._config.sessions.idle_ttl_seconds
                    if idle_ttl is not None and state.in_flight == 0 and (now - state.last_accessed) > idle_ttl:
                        await self._state_repository.pop_session(key)
                        outcome = await self._tombstone_or_delete_session(key, state, now)
                        session_removed = True
                        if outcome == "tombstoned":
                            tombstoned_sessions += 1
                            logger.info(
                                "Session idle-expired and tombstoned",
                                extra={"server_id": state.server_id, "session_id": state.session_id},
                            )
                            if self._telemetry is not None:
                                await self._telemetry.record_session_lifecycle(
                                    event="idle_expired_tombstoned",
                                    server_id=state.server_id,
                                )
                        else:
                            expired_sessions += 1
                            logger.info(
                                "Session idle-expired and deleted",
                                extra={"server_id": state.server_id, "session_id": state.session_id},
                            )
                            if self._telemetry is not None:
                                await self._telemetry.record_session_lifecycle(
                                    event="idle_expired_deleted",
                                    server_id=state.server_id,
                                )
                    else:
                        for upload in state.uploads.values():
                            referenced_paths.add(str(upload.abs_path.resolve()))
                        for artifact in state.artifacts.values():
                            referenced_paths.add(str(artifact.abs_path.resolve()))
                    if state_changed and not session_removed:
                        await self._persist_session_state(state)

            if self._config.storage.orphan_sweeper_enabled:
                orphan_cutoff = now - self._config.storage.orphan_sweeper_grace_seconds
                removed_orphan_files += await self._ops.purge_orphan_files_async(
                    root=self._uploads_root,
                    referenced_paths=referenced_paths,
                    older_than_epoch=orphan_cutoff,
                )
                removed_orphan_files += await self._ops.purge_orphan_files_async(
                    root=self._artifacts_root,
                    referenced_paths=referenced_paths,
                    older_than_epoch=orphan_cutoff,
                )

        return {
            "expired_sessions": expired_sessions,
            "tombstoned_sessions": tombstoned_sessions,
            "removed_tombstones": removed_tombstones,
            "removed_uploads": removed_uploads,
            "removed_artifacts": removed_artifacts,
            "removed_dangling_upload_records": removed_dangling_upload_records,
            "removed_dangling_artifact_records": removed_dangling_artifact_records,
            "removed_orphan_files": removed_orphan_files,
        }

    async def shutdown(self, mode: Literal["keep_files", "delete_files"] = "keep_files") -> None:
        """Release in-memory state and optionally delete storage directories.

        Args:
            mode: ``'keep_files'`` preserves disk data; ``'delete_files'`` purges
                all upload and artifact files.
        """
        if mode == "keep_files":
            return

        async with self._lock_provider.hold(self._STATE_LOCK_NAME):
            session_states, tombstones = await self._state_repository.drain()

        if mode == "delete_files":
            for state in session_states:
                await self._ops.purge_state_files_async(state)
            for tombstone in tombstones:
                await self._ops.purge_state_files_async(tombstone.state)
            await asyncio.to_thread(shutil.rmtree, self._uploads_root, True)
            await asyncio.to_thread(shutil.rmtree, self._artifacts_root, True)
