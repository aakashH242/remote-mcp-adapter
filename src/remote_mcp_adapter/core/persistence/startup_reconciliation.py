"""Startup reconciliation for legacy file-only session state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import mimetypes
from pathlib import Path
from typing import Any

from ...config import AdapterConfig
from ..repo.records import ArtifactRecord, SessionState, UploadRecord
from ..repo.state_repository import SessionKey, StateRepository
from ..storage.storage_utils import ensure_within_base, sha256_file

logger = logging.getLogger(__name__)
_DEFAULT_MIME_TYPE = "application/octet-stream"


@dataclass(slots=True)
class _UploadSeed:
    """Intermediate file metadata for one discovered upload."""

    upload_id: str
    filename: str
    abs_path: Path
    rel_path: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: float
    last_accessed: float
    last_updated: float


@dataclass(slots=True)
class _ArtifactSeed:
    """Intermediate file metadata for one discovered artifact."""

    artifact_id: str
    filename: str
    abs_path: Path
    rel_path: str
    mime_type: str
    size_bytes: int
    created_at: float
    last_accessed: float
    last_updated: float


@dataclass(slots=True)
class _DiscoverySnapshot:
    """Accumulates file-system discoveries for startup reconciliation."""

    uploads_by_session: dict[str, dict[str, _UploadSeed]] = field(default_factory=dict)
    artifacts_by_session: dict[str, dict[str, _ArtifactSeed]] = field(default_factory=dict)
    skipped_entries: int = 0

    @property
    def upload_count(self) -> int:
        """Total uploads across all discovered sessions."""
        return sum(len(entries) for entries in self.uploads_by_session.values())

    @property
    def artifact_count(self) -> int:
        """Total artifacts across all discovered sessions."""
        return sum(len(entries) for entries in self.artifacts_by_session.values())


class StartupStateReconciler:
    """Backfill missing repository state from existing storage files."""

    def __init__(self, *, config: AdapterConfig, state_repository: StateRepository) -> None:
        """Initialize the startup reconciler.

        Args:
            config: Full adapter configuration.
            state_repository: State repository to backfill.
        """
        self._config = config
        self._state_repository = state_repository
        self._storage_root = Path(config.storage.root).resolve()
        self._uploads_root = self._storage_root / "uploads" / "sessions"
        self._artifacts_root = self._storage_root / "artifacts" / "sessions"
        self._server_ids = {server.id for server in config.servers}
        self._legacy_server_id = config.state_persistence.reconciliation.legacy_server_id
        self._mode = config.state_persistence.reconciliation.mode

    async def reconcile(self) -> dict[str, Any]:
        """Run one startup reconciliation pass.

        Returns:
            Structured status report dict.
        """
        if self._mode == "disabled":
            return {
                "status": "disabled",
                "mode": self._mode,
                "reason": "reconciliation_disabled",
            }
        if self._config.state_persistence.refresh_on_startup and self._mode != "always":
            return {
                "status": "skipped",
                "mode": self._mode,
                "reason": "refresh_on_startup_enabled",
            }

        session_items = list(await self._state_repository.list_session_items())
        tombstone_items = list(await self._state_repository.list_tombstone_items())
        repository_empty = not session_items and not tombstone_items
        if self._mode == "if_empty" and not repository_empty:
            return {
                "status": "skipped",
                "mode": self._mode,
                "reason": "repository_not_empty",
                "repository_empty": repository_empty,
            }

        discovery = await asyncio.to_thread(self._discover_filesystem_state)
        if discovery.upload_count == 0 and discovery.artifact_count == 0:
            return {
                "status": "skipped",
                "mode": self._mode,
                "reason": "no_files_found",
                "repository_empty": repository_empty,
                "uploads_discovered": 0,
                "artifacts_discovered": 0,
                "skipped_entries": discovery.skipped_entries,
            }

        existing_session_hints = self._build_session_server_hints(
            session_items=session_items,
            tombstone_items=tombstone_items,
        )
        resolved_uploads, unresolved_upload_sessions = self._resolve_upload_sessions(
            uploads_by_session=discovery.uploads_by_session,
            session_hints=existing_session_hints,
        )
        resolved_artifacts, unresolved_artifact_sessions = self._resolve_artifact_sessions(
            artifacts_by_session=discovery.artifacts_by_session,
            session_hints=existing_session_hints,
        )
        unresolved_sessions = sorted(unresolved_upload_sessions | unresolved_artifact_sessions)
        states_by_key: dict[SessionKey, SessionState] = dict(session_items)
        changed_keys: set[SessionKey] = set()
        created_sessions = 0
        uploads_backfilled = 0
        artifacts_backfilled = 0

        for key in set(resolved_uploads) | set(resolved_artifacts):
            upload_seeds = resolved_uploads.get(key, {})
            artifact_seeds = resolved_artifacts.get(key, {})
            state = states_by_key.get(key)
            if state is None:
                created_at = self._resolve_session_created_at(upload_seeds, artifact_seeds)
                state = SessionState(
                    server_id=key[0],
                    session_id=key[1],
                    created_at=created_at,
                    last_accessed=created_at,
                )
                states_by_key[key] = state
                changed_keys.add(key)
                created_sessions += 1

            state_changed = False
            session_last_accessed = state.last_accessed
            for upload_seed in upload_seeds.values():
                session_last_accessed = max(session_last_accessed, upload_seed.last_accessed)
                if upload_seed.upload_id in state.uploads:
                    continue
                state.uploads[upload_seed.upload_id] = UploadRecord(
                    server_id=key[0],
                    session_id=key[1],
                    upload_id=upload_seed.upload_id,
                    filename=upload_seed.filename,
                    abs_path=upload_seed.abs_path,
                    rel_path=upload_seed.rel_path,
                    mime_type=upload_seed.mime_type,
                    size_bytes=upload_seed.size_bytes,
                    sha256=upload_seed.sha256,
                    created_at=upload_seed.created_at,
                    last_accessed=upload_seed.last_accessed,
                    last_updated=upload_seed.last_updated,
                )
                uploads_backfilled += 1
                state_changed = True

            for artifact_seed in artifact_seeds.values():
                session_last_accessed = max(session_last_accessed, artifact_seed.last_accessed)
                if artifact_seed.artifact_id in state.artifacts:
                    continue
                state.artifacts[artifact_seed.artifact_id] = ArtifactRecord(
                    server_id=key[0],
                    session_id=key[1],
                    artifact_id=artifact_seed.artifact_id,
                    filename=artifact_seed.filename,
                    abs_path=artifact_seed.abs_path,
                    rel_path=artifact_seed.rel_path,
                    mime_type=artifact_seed.mime_type,
                    size_bytes=artifact_seed.size_bytes,
                    created_at=artifact_seed.created_at,
                    last_accessed=artifact_seed.last_accessed,
                    last_updated=artifact_seed.last_updated,
                    expose_as_resource=self._config.artifacts.expose_as_resources,
                    visibility_state="committed",
                )
                artifacts_backfilled += 1
                state_changed = True

            if session_last_accessed > state.last_accessed:
                state.touch(session_last_accessed)
                state_changed = True
            if state_changed:
                changed_keys.add(key)

        for key in changed_keys:
            await self._state_repository.set_session(key, states_by_key[key])

        report = {
            "status": "applied",
            "mode": self._mode,
            "repository_empty": repository_empty,
            "legacy_migration": repository_empty,
            "uploads_discovered": discovery.upload_count,
            "artifacts_discovered": discovery.artifact_count,
            "uploads_backfilled": uploads_backfilled,
            "artifacts_backfilled": artifacts_backfilled,
            "sessions_created": created_sessions,
            "sessions_updated": len(changed_keys),
            "unresolved_sessions": unresolved_sessions,
            "unresolved_sessions_count": len(unresolved_sessions),
            "skipped_entries": discovery.skipped_entries,
        }
        logger.info("Startup state reconciliation complete", extra=report)
        return report

    def _resolve_session_created_at(
        self,
        upload_seeds: dict[str, _UploadSeed],
        artifact_seeds: dict[str, _ArtifactSeed],
    ) -> float:
        """Return the earliest created_at timestamp across all seeds, or 0.0.

        Args:
            upload_seeds: Upload seed records keyed by id.
            artifact_seeds: Artifact seed records keyed by id.
        """
        timestamps = [seed.created_at for seed in upload_seeds.values()]
        timestamps.extend(seed.created_at for seed in artifact_seeds.values())
        if not timestamps:
            return 0.0
        return min(timestamps)

    def _build_session_server_hints(
        self,
        *,
        session_items: list[tuple[SessionKey, SessionState]],
        tombstone_items: list[tuple[SessionKey, object]],
    ) -> dict[str, set[str]]:
        """Build a session_id → server_ids hint map from existing repository state.

        Args:
            session_items: Existing session entries.
            tombstone_items: Existing tombstone entries.
        """
        session_hints: dict[str, set[str]] = {}
        for key, _ in session_items:
            session_hints.setdefault(key[1], set()).add(key[0])
        for key, _ in tombstone_items:
            session_hints.setdefault(key[1], set()).add(key[0])
        return session_hints

    def _resolve_server_id(self, session_id: str, session_hints: dict[str, set[str]]) -> str | None:
        """Determine the server_id owning session_id, or None if ambiguous.

        Args:
            session_id: Session identifier to resolve.
            session_hints: Hint map from existing repository state.
        """
        hinted_servers = session_hints.get(session_id, set())
        if len(hinted_servers) == 1:
            return next(iter(hinted_servers))
        if self._legacy_server_id is not None:
            return self._legacy_server_id
        if len(self._server_ids) == 1:
            return next(iter(self._server_ids))
        return None

    def _resolve_upload_sessions(
        self,
        *,
        uploads_by_session: dict[str, dict[str, _UploadSeed]],
        session_hints: dict[str, set[str]],
    ) -> tuple[dict[SessionKey, dict[str, _UploadSeed]], set[str]]:
        """Map discovered upload sessions to server-qualified keys.

        Args:
            uploads_by_session: Upload seeds keyed by session id.
            session_hints: Hint map from existing repository state.

        Returns:
            Tuple of resolved uploads and unresolvable session ids.
        """
        resolved: dict[SessionKey, dict[str, _UploadSeed]] = {}
        unresolved_sessions: set[str] = set()
        for session_id, uploads in uploads_by_session.items():
            server_id = self._resolve_server_id(session_id, session_hints)
            if server_id is None:
                unresolved_sessions.add(session_id)
                continue
            resolved[(server_id, session_id)] = uploads
        return resolved, unresolved_sessions

    def _resolve_artifact_sessions(
        self,
        *,
        artifacts_by_session: dict[str, dict[str, _ArtifactSeed]],
        session_hints: dict[str, set[str]],
    ) -> tuple[dict[SessionKey, dict[str, _ArtifactSeed]], set[str]]:
        """Map discovered artifact sessions to server-qualified keys.

        Args:
            artifacts_by_session: Artifact seeds keyed by session id.
            session_hints: Hint map from existing repository state.

        Returns:
            Tuple of resolved artifacts and unresolvable session ids.
        """
        resolved: dict[SessionKey, dict[str, _ArtifactSeed]] = {}
        unresolved_sessions: set[str] = set()
        for session_id, artifacts in artifacts_by_session.items():
            server_id = self._resolve_server_id(session_id, session_hints)
            if server_id is None:
                unresolved_sessions.add(session_id)
                continue
            resolved[(server_id, session_id)] = artifacts
        return resolved, unresolved_sessions

    def _discover_filesystem_state(self) -> _DiscoverySnapshot:
        """Walk uploads and artifacts directories, returning a full discovery snapshot."""
        snapshot = _DiscoverySnapshot()
        self._discover_upload_files(snapshot)
        self._discover_artifact_files(snapshot)
        return snapshot

    def _discover_upload_files(self, snapshot: _DiscoverySnapshot) -> None:
        """Populate snapshot.uploads_by_session from the uploads storage root.

        Args:
            snapshot: Discovery snapshot to populate.
        """
        for session_dir in self._iter_session_dirs(self._uploads_root):
            session_id = session_dir.name
            session_uploads: dict[str, _UploadSeed] = {}
            for upload_dir in self._iter_item_dirs(session_dir):
                upload_id = upload_dir.name
                selected_file = self._select_file(upload_dir, snapshot)
                if selected_file is None:
                    continue
                seed = self._build_upload_seed(selected_file, upload_id, snapshot)
                if seed is not None:
                    session_uploads[upload_id] = seed
            if session_uploads:
                snapshot.uploads_by_session[session_id] = session_uploads

    def _discover_artifact_files(self, snapshot: _DiscoverySnapshot) -> None:
        """Populate snapshot.artifacts_by_session from the artifacts storage root.

        Args:
            snapshot: Discovery snapshot to populate.
        """
        for session_dir in self._iter_session_dirs(self._artifacts_root):
            session_id = session_dir.name
            session_artifacts: dict[str, _ArtifactSeed] = {}
            for artifact_dir in self._iter_item_dirs(session_dir):
                artifact_id = artifact_dir.name
                selected_file = self._select_file(artifact_dir, snapshot)
                if selected_file is None:
                    continue
                seed = self._build_artifact_seed(selected_file, artifact_id, snapshot)
                if seed is not None:
                    session_artifacts[artifact_id] = seed
            if session_artifacts:
                snapshot.artifacts_by_session[session_id] = session_artifacts

    def _iter_session_dirs(self, root: Path) -> list[Path]:
        """List subdirectories of root, returning empty list on missing or unreadable root.

        Args:
            root: Parent directory to enumerate.
        """
        if not root.exists():
            return []
        try:
            return [entry for entry in root.iterdir() if entry.is_dir()]
        except OSError:
            logger.exception("Failed to enumerate session directories", extra={"root": str(root)})
            return []

    def _iter_item_dirs(self, session_dir: Path) -> list[Path]:
        """List item subdirectories within a session directory.

        Args:
            session_dir: Session directory to enumerate.
        """
        try:
            return [entry for entry in session_dir.iterdir() if entry.is_dir()]
        except OSError:
            logger.exception("Failed to enumerate item directories", extra={"session_dir": str(session_dir)})
            return []

    def _select_file(self, item_dir: Path, snapshot: _DiscoverySnapshot) -> Path | None:
        """Pick the single file from an item dir, preferring the newest when multiples exist.

        Args:
            item_dir: Item directory to inspect.
            snapshot: Discovery snapshot for tracking skipped entries.
        """
        try:
            candidates = [entry for entry in item_dir.iterdir() if entry.is_file()]
        except OSError:
            snapshot.skipped_entries += 1
            logger.exception("Failed to enumerate files in item directory", extra={"item_dir": str(item_dir)})
            return None
        if not candidates:
            snapshot.skipped_entries += 1
            return None
        if len(candidates) > 1:
            mtime_ranked: list[tuple[float, Path]] = []
            for candidate in candidates:
                try:
                    mtime_ranked.append((candidate.stat().st_mtime, candidate))
                except OSError:
                    snapshot.skipped_entries += 1
            if not mtime_ranked:
                return None
            mtime_ranked.sort(key=lambda item: item[0], reverse=True)
            candidates = [item[1] for item in mtime_ranked]
            snapshot.skipped_entries += len(candidates) - 1
            logger.warning(
                "Multiple files found for one item directory; selecting newest file",
                extra={"item_dir": str(item_dir), "selected": str(candidates[0]), "count": len(candidates)},
            )
        return candidates[0]

    def _build_upload_seed(self, file_path: Path, upload_id: str, snapshot: _DiscoverySnapshot) -> _UploadSeed | None:
        """Stat and hash the file to build an upload seed, or return None on IO error.

        Args:
            file_path: Path to the upload file.
            upload_id: Upload identifier.
            snapshot: Discovery snapshot for tracking skipped entries.
        """
        try:
            safe_path = ensure_within_base(file_path, self._storage_root)
            file_stat = safe_path.stat()
            digest = sha256_file(safe_path)
        except (OSError, ValueError):
            snapshot.skipped_entries += 1
            logger.exception("Failed to inspect upload file during reconciliation", extra={"path": str(file_path)})
            return None
        mtime = float(file_stat.st_mtime)
        atime = float(file_stat.st_atime)
        return _UploadSeed(
            upload_id=upload_id,
            filename=safe_path.name,
            abs_path=safe_path,
            rel_path=safe_path.relative_to(self._storage_root).as_posix(),
            mime_type=mimetypes.guess_type(safe_path.name)[0] or _DEFAULT_MIME_TYPE,
            size_bytes=int(file_stat.st_size),
            sha256=digest,
            created_at=mtime,
            last_accessed=max(atime, mtime),
            last_updated=mtime,
        )

    def _build_artifact_seed(
        self,
        file_path: Path,
        artifact_id: str,
        snapshot: _DiscoverySnapshot,
    ) -> _ArtifactSeed | None:
        """Stat the file to build an artifact seed, or return None on IO error.

        Args:
            file_path: Path to the artifact file.
            artifact_id: Artifact identifier.
            snapshot: Discovery snapshot for tracking skipped entries.
        """
        try:
            safe_path = ensure_within_base(file_path, self._storage_root)
            file_stat = safe_path.stat()
        except (OSError, ValueError):
            snapshot.skipped_entries += 1
            logger.exception("Failed to inspect artifact file during reconciliation", extra={"path": str(file_path)})
            return None
        mtime = float(file_stat.st_mtime)
        atime = float(file_stat.st_atime)
        return _ArtifactSeed(
            artifact_id=artifact_id,
            filename=safe_path.name,
            abs_path=safe_path,
            rel_path=safe_path.relative_to(self._storage_root).as_posix(),
            mime_type=mimetypes.guess_type(safe_path.name)[0] or _DEFAULT_MIME_TYPE,
            size_bytes=int(file_stat.st_size),
            created_at=mtime,
            last_accessed=max(atime, mtime),
            last_updated=mtime,
        )


async def run_startup_state_reconciliation(*, config: AdapterConfig, state_repository: StateRepository) -> dict[str, Any]:
    """Run startup migration/reconciliation and return a structured status report.

    Args:
        config: Full adapter configuration.
        state_repository: State repository to backfill.
    """
    reconciler = StartupStateReconciler(config=config, state_repository=state_repository)
    return await reconciler.reconcile()
