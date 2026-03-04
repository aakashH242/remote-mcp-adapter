"""Filesystem and quota operations for SessionStore."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable

from fastmcp.exceptions import ToolError

from ...config import AdapterConfig
from ..repo.records import SessionState


@dataclass(slots=True)
class StoreOps:
    """Encapsulates mutable file/quota operations for session records."""

    config: AdapterConfig
    storage_root: Path
    upload_session_dir: Callable[[str], Path]
    artifact_session_dir: Callable[[str], Path]

    @staticmethod
    def _delete_record_path(path: Path) -> None:
        """Unlink the file and remove its parent directory when empty.

        Args:
            path: Filesystem path to the record file.
        """
        path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            path.parent.rmdir()

    @staticmethod
    def _purge_empty_session_dir(path: Path) -> None:
        """Remove a session directory when it exists and is empty.

        Args:
            path: Session directory to check and remove.
        """
        with contextlib.suppress(OSError):
            if path.exists() and not any(path.iterdir()):
                path.rmdir()

    @staticmethod
    def session_total_bytes(state: SessionState) -> int:
        """Sum upload and artifact byte counts for a session.

        Args:
            state: Session state to measure.
        """
        upload_bytes = sum(record.size_bytes for record in state.uploads.values())
        artifact_bytes = sum(record.size_bytes for record in state.artifacts.values())
        return upload_bytes + artifact_bytes

    def remove_upload_record(self, state: SessionState, upload_id: str) -> tuple[bool, int]:
        """Pop upload from state dict and unlink its file.

        Args:
            state: Session state containing uploads.
            upload_id: Upload to remove.

        Returns:
            Tuple of ``(removed, freed_bytes)``.
        """
        record = state.uploads.pop(upload_id, None)
        if record is None:
            return (False, 0)
        record.abs_path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            record.abs_path.parent.rmdir()
        return (True, record.size_bytes)

    async def remove_upload_record_async(self, state: SessionState, upload_id: str) -> tuple[bool, int]:
        """Async variant of ``remove_upload_record`` using ``asyncio.to_thread``.

        Args:
            state: Session state containing uploads.
            upload_id: Upload to remove.

        Returns:
            Tuple of ``(removed, freed_bytes)``.
        """
        record = state.uploads.pop(upload_id, None)
        if record is None:
            return (False, 0)
        await asyncio.to_thread(self._delete_record_path, record.abs_path)
        return (True, record.size_bytes)

    def remove_artifact_record(self, state: SessionState, artifact_id: str) -> tuple[bool, int]:
        """Pop artifact from state dict and unlink its file.

        Args:
            state: Session state containing artifacts.
            artifact_id: Artifact to remove.

        Returns:
            Tuple of ``(removed, freed_bytes)``.
        """
        record = state.artifacts.pop(artifact_id, None)
        if record is None:
            return (False, 0)
        record.abs_path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            record.abs_path.parent.rmdir()
        return (True, record.size_bytes)

    async def remove_artifact_record_async(self, state: SessionState, artifact_id: str) -> tuple[bool, int]:
        """Async variant of ``remove_artifact_record`` using ``asyncio.to_thread``.

        Args:
            state: Session state containing artifacts.
            artifact_id: Artifact to remove.

        Returns:
            Tuple of ``(removed, freed_bytes)``.
        """
        record = state.artifacts.pop(artifact_id, None)
        if record is None:
            return (False, 0)
        await asyncio.to_thread(self._delete_record_path, record.abs_path)
        return (True, record.size_bytes)

    def purge_empty_session_dirs(self, session_id: str) -> None:
        """Synchronously remove upload and artifact dirs for a session when empty.

        Args:
            session_id: Session whose directories to purge.
        """
        for directory in (self.upload_session_dir(session_id), self.artifact_session_dir(session_id)):
            self._purge_empty_session_dir(directory)

    async def purge_empty_session_dirs_async(self, session_id: str) -> None:
        """Async variant of ``purge_empty_session_dirs`` using ``asyncio.to_thread``.

        Args:
            session_id: Session whose directories to purge.
        """
        for directory in (self.upload_session_dir(session_id), self.artifact_session_dir(session_id)):
            await asyncio.to_thread(self._purge_empty_session_dir, directory)

    def purge_state_files(self, state: SessionState) -> None:
        """Synchronously delete all upload and artifact files for a session.

        Args:
            state: Session state whose files to purge.
        """
        for upload_id in list(state.uploads.keys()):
            self.remove_upload_record(state, upload_id)
        for artifact_id in list(state.artifacts.keys()):
            self.remove_artifact_record(state, artifact_id)
        self.purge_empty_session_dirs(state.session_id)

    async def purge_state_files_async(self, state: SessionState) -> None:
        """Async variant of ``purge_state_files`` using ``asyncio.to_thread``.

        Args:
            state: Session state whose files to purge.
        """
        for upload_id in list(state.uploads.keys()):
            await self.remove_upload_record_async(state, upload_id)
        for artifact_id in list(state.artifacts.keys()):
            await self.remove_artifact_record_async(state, artifact_id)
        await self.purge_empty_session_dirs_async(state.session_id)

    def _evict_session_lru(self, state: SessionState, bytes_needed: int) -> int:
        """Evict LRU uploads and/or artifacts until *bytes_needed* is freed.

        Args:
            state: Session state to evict from.
            bytes_needed: Minimum bytes to free.

        Returns:
            Total bytes actually freed.
        """
        policy = self.config.sessions.eviction_policy
        freed_bytes = 0

        def evict_uploads() -> int:
            """Evict uploads in LRU order until enough bytes are freed."""
            freed = 0
            ordered = sorted(state.uploads.values(), key=lambda record: record.last_accessed)
            for record in ordered:
                _, size_bytes = self.remove_upload_record(state, record.upload_id)
                freed += size_bytes
                if freed + freed_bytes >= bytes_needed:
                    break
            return freed

        def evict_artifacts() -> int:
            """Evict artifacts in LRU order until enough bytes are freed."""
            freed = 0
            ordered = sorted(state.artifacts.values(), key=lambda record: record.last_accessed)
            for record in ordered:
                _, size_bytes = self.remove_artifact_record(state, record.artifact_id)
                freed += size_bytes
                if freed + freed_bytes >= bytes_needed:
                    break
            return freed

        if policy == "lru_uploads_then_artifacts":
            freed_bytes += evict_uploads()
            if freed_bytes < bytes_needed:
                freed_bytes += evict_artifacts()
        else:
            freed_bytes += evict_artifacts()
            if freed_bytes < bytes_needed:
                freed_bytes += evict_uploads()
        return freed_bytes

    def enforce_session_quota(self, state: SessionState, *, incoming_bytes: int) -> None:
        """Evict LRU items to satisfy the session quota.

        Args:
            state: Session state to enforce quota on.
            incoming_bytes: Bytes about to be added.

        Raises:
            ToolError: If the quota cannot be satisfied after eviction.
        """
        limit = self.config.sessions.max_total_session_size
        if limit is None:
            return
        total = self.session_total_bytes(state)
        projected = total + incoming_bytes
        if projected <= limit:
            return
        needed = projected - limit
        freed = self._evict_session_lru(state, needed)
        if projected - freed > limit:
            raise ToolError("Session storage limit exceeded.")

    def _storage_total_bytes(self) -> int:
        """Walk the storage root and sum all file sizes (slow path, run in thread)."""
        if not self.storage_root.exists():
            return 0
        total_bytes = 0
        for path in self.storage_root.rglob("*"):
            with contextlib.suppress(OSError):
                if path.is_file():
                    total_bytes += path.stat().st_size
        return total_bytes

    async def enforce_global_storage_quota(self) -> None:
        """Raise ``ToolError`` when total disk usage exceeds the configured global limit.

        Raises:
            ToolError: If global storage quota is exceeded.
        """
        limit = self.config.storage.max_size
        if limit is None:
            return
        total_bytes = await asyncio.to_thread(self._storage_total_bytes)
        if total_bytes > limit:
            raise ToolError("Global storage limit exceeded.")

    @staticmethod
    def _purge_orphan_files_sync(
        *,
        root: Path,
        referenced_paths: set[str],
        older_than_epoch: float,
    ) -> int:
        """Delete unreferenced files older than the grace cutoff.

        Args:
            root: Directory tree to scan.
            referenced_paths: Set of resolved paths to keep.
            older_than_epoch: Files modified after this are kept.

        Returns:
            Count of files removed.
        """
        if not root.exists():
            return 0
        removed = 0
        for current_root, _, filenames in os.walk(root):
            for filename in filenames:
                file_path = Path(current_root) / filename
                resolved = str(file_path.resolve())
                if resolved in referenced_paths:
                    continue
                try:
                    mtime = file_path.stat().st_mtime
                except OSError:
                    continue
                if mtime > older_than_epoch:
                    continue
                with contextlib.suppress(OSError):
                    file_path.unlink(missing_ok=True)
                    removed += 1
                with contextlib.suppress(OSError):
                    file_path.parent.rmdir()
        return removed

    async def purge_orphan_files_async(
        self,
        *,
        root: Path,
        referenced_paths: set[str],
        older_than_epoch: float,
    ) -> int:
        """Async wrapper for ``_purge_orphan_files_sync`` using ``asyncio.to_thread``.

        Args:
            root: Directory tree to scan.
            referenced_paths: Set of resolved paths to keep.
            older_than_epoch: Files modified after this are kept.

        Returns:
            Count of files removed.
        """
        return await asyncio.to_thread(
            self._purge_orphan_files_sync,
            root=root,
            referenced_paths=referenced_paths,
            older_than_epoch=older_than_epoch,
        )
