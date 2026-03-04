"""Core record dataclasses for session-scoped state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from pathlib import Path
from typing import Literal


def now_ts() -> float:
    """Return current UNIX timestamp."""
    return time.time()


@dataclass(slots=True)
class UploadRecord:
    """Metadata for a session-scoped uploaded file."""

    server_id: str
    session_id: str
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

    def touch(self, ts: float | None = None) -> None:
        """Update last_accessed and last_updated to the given or current timestamp.

        Args:
            ts: Optional explicit timestamp; defaults to current time.
        """
        now = now_ts() if ts is None else ts
        self.last_accessed = now
        self.last_updated = now


@dataclass(slots=True)
class ArtifactRecord:
    """Metadata for a session-scoped persisted artifact."""

    server_id: str
    session_id: str
    artifact_id: str
    filename: str
    abs_path: Path
    rel_path: str
    mime_type: str
    size_bytes: int
    created_at: float
    last_accessed: float
    last_updated: float
    tool_name: str | None = None
    expose_as_resource: bool = True
    visibility_state: Literal["pending", "committed"] = "pending"

    def touch(self, ts: float | None = None) -> None:
        """Update last_accessed and last_updated to the given or current timestamp.

        Args:
            ts: Optional explicit timestamp; defaults to current time.
        """
        now = now_ts() if ts is None else ts
        self.last_accessed = now
        self.last_updated = now


@dataclass(slots=True)
class SessionState:
    """In-memory state for one `(server_id, session_id)` tuple."""

    server_id: str
    session_id: str
    created_at: float
    last_accessed: float
    in_flight: int = 0
    uploads: dict[str, UploadRecord] = field(default_factory=dict)
    artifacts: dict[str, ArtifactRecord] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self, ts: float | None = None) -> None:
        """Update last_accessed to the given or current timestamp.

        Args:
            ts: Optional explicit timestamp; defaults to current time.
        """
        self.last_accessed = now_ts() if ts is None else ts


@dataclass(slots=True)
class SessionTombstone:
    """Tombstoned session state retained for optional revival."""

    state: SessionState
    expires_at: float
