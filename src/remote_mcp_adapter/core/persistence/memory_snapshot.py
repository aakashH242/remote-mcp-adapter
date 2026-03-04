"""Periodic memory-mode state snapshots to durable disk storage."""

from __future__ import annotations

import asyncio
import logging
import time

from ..repo.state_repository import StateRepository
from ..repo.sqlite_state_repository import SqliteStateRepository

logger = logging.getLogger(__name__)


class MemorySnapshotManager:
    """Periodically snapshots in-memory repository state into SQLite."""

    def __init__(
        self,
        *,
        source_repository: StateRepository,
        snapshot_repository: SqliteStateRepository,
        interval_seconds: int,
    ) -> None:
        """Initialize the snapshot manager.

        Args:
            source_repository: In-memory state repository to drain for snapshots.
            snapshot_repository: SQLite-backed repository receiving snapshot writes.
            interval_seconds: Minimum seconds between snapshot cycles.
        """
        self._source_repository = source_repository
        self._snapshot_repository = snapshot_repository
        self._interval_seconds = max(int(interval_seconds), 1)
        self._lock = asyncio.Lock()
        self._last_success_at: float | None = None
        self._last_failure_at: float | None = None
        self._last_error: str | None = None
        self._last_sessions_count: int = 0
        self._last_tombstones_count: int = 0

    @property
    def interval_seconds(self) -> int:
        """Snapshot cycle interval in seconds."""
        return self._interval_seconds

    async def run_once(self) -> None:
        """Persist one atomic snapshot cycle.

        Raises:
            Exception: Re-raised after recording the failure timestamp.
        """
        async with self._lock:
            sessions = list(await self._source_repository.list_session_items())
            tombstones = list(await self._source_repository.list_tombstone_items())
            try:
                await self._snapshot_repository.replace_all(
                    sessions=sessions,
                    tombstones=tombstones,
                )
            except Exception as exc:
                self._last_error = str(exc)
                self._last_failure_at = time.time()
                logger.exception(
                    "Memory snapshot cycle failed",
                    extra={
                        "sessions_count": len(sessions),
                        "tombstones_count": len(tombstones),
                    },
                )
                raise
            self._last_error = None
            self._last_success_at = time.time()
            self._last_sessions_count = len(sessions)
            self._last_tombstones_count = len(tombstones)

    async def run_loop(self, stop_event: asyncio.Event) -> None:
        """Run periodic snapshot cycles until stop is requested.

        Args:
            stop_event: Event signalling the loop should exit.
        """
        while True:
            if stop_event.is_set():
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_seconds)
                return
            except asyncio.TimeoutError:
                pass

            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    async def health_snapshot(self) -> dict[str, object]:
        """Snapshot health payload for ``/healthz`` persistence section.

        Returns:
            Dict with status, interval, last success/failure timestamps,
            and last session/tombstone counts.
        """
        async with self._lock:
            status = "ok" if self._last_error is None else "degraded"
            return {
                "status": status,
                "interval_seconds": self._interval_seconds,
                "last_success_at": self._last_success_at,
                "last_failure_at": self._last_failure_at,
                "last_error": self._last_error,
                "last_sessions_count": self._last_sessions_count,
                "last_tombstones_count": self._last_tombstones_count,
            }
