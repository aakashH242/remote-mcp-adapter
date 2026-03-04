"""State repository interfaces and in-memory adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeAlias

from .records import SessionState, SessionTombstone

SessionKey: TypeAlias = tuple[str, str]


class StateRepository(Protocol):
    """Persistence boundary for session/tombstone state."""

    async def get_session(self, key: SessionKey) -> SessionState | None:
        """Return one session state by key, if present.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """

    async def set_session(self, key: SessionKey, state: SessionState) -> None:
        """Store or replace one session state.

        Args:
            key: ``(server_id, session_id)`` tuple.
            state: Session state to persist.
        """

    async def pop_session(self, key: SessionKey) -> SessionState | None:
        """Remove and return one session state, if present.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """

    async def session_count(self) -> int:
        """Return active session count."""

    async def list_session_items(self) -> Sequence[tuple[SessionKey, SessionState]]:
        """Return a stable snapshot of session key/value pairs."""

    async def get_tombstone(self, key: SessionKey) -> SessionTombstone | None:
        """Return one tombstone by key, if present.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """

    async def set_tombstone(self, key: SessionKey, tombstone: SessionTombstone) -> None:
        """Store or replace one tombstone.

        Args:
            key: ``(server_id, session_id)`` tuple.
            tombstone: Tombstone record to persist.
        """

    async def pop_tombstone(self, key: SessionKey) -> SessionTombstone | None:
        """Remove and return one tombstone, if present.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """

    async def list_tombstone_items(self) -> Sequence[tuple[SessionKey, SessionTombstone]]:
        """Return a stable snapshot of tombstone key/value pairs."""

    async def drain(self) -> tuple[list[SessionState], list[SessionTombstone]]:
        """Return all values and clear repository state."""


class InMemoryStateRepository(StateRepository):
    """In-memory repository used by current runtime behavior."""

    def __init__(self):
        """Initialize with empty session and tombstone dicts."""
        self._sessions: dict[SessionKey, SessionState] = {}
        self._tombstones: dict[SessionKey, SessionTombstone] = {}

    async def get_session(self, key: SessionKey) -> SessionState | None:
        """Return session from in-memory dict, or None.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        return self._sessions.get(key)

    async def set_session(self, key: SessionKey, state: SessionState) -> None:
        """Upsert session into the in-memory dict.

        Args:
            key: ``(server_id, session_id)`` tuple.
            state: Session state to store.
        """
        self._sessions[key] = state

    async def pop_session(self, key: SessionKey) -> SessionState | None:
        """Remove and return session from in-memory dict, or None.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        return self._sessions.pop(key, None)

    async def session_count(self) -> int:
        """Return the length of the in-memory sessions dict."""
        return len(self._sessions)

    async def list_session_items(self) -> Sequence[tuple[SessionKey, SessionState]]:
        """Return a stable list snapshot of all in-memory sessions."""
        return list(self._sessions.items())

    async def get_tombstone(self, key: SessionKey) -> SessionTombstone | None:
        """Return tombstone from in-memory dict, or None.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        return self._tombstones.get(key)

    async def set_tombstone(self, key: SessionKey, tombstone: SessionTombstone) -> None:
        """Upsert tombstone into the in-memory dict.

        Args:
            key: ``(server_id, session_id)`` tuple.
            tombstone: Tombstone record to store.
        """
        self._tombstones[key] = tombstone

    async def pop_tombstone(self, key: SessionKey) -> SessionTombstone | None:
        """Remove and return tombstone from in-memory dict, or None.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        return self._tombstones.pop(key, None)

    async def list_tombstone_items(self) -> Sequence[tuple[SessionKey, SessionTombstone]]:
        """Return a stable list snapshot of all in-memory tombstones."""
        return list(self._tombstones.items())

    async def drain(self) -> tuple[list[SessionState], list[SessionTombstone]]:
        """Return all values, then clear both in-memory dicts."""
        session_states = list(self._sessions.values())
        tombstones = list(self._tombstones.values())
        self._sessions.clear()
        self._tombstones.clear()
        return session_states, tombstones

    def replace_all(
        self,
        *,
        sessions: Sequence[tuple[SessionKey, SessionState]],
        tombstones: Sequence[tuple[SessionKey, SessionTombstone]],
    ) -> None:
        """Replace entire in-memory state for bootstrap flows.

        Args:
            sessions: Complete set of session key/state pairs.
            tombstones: Complete set of tombstone key/value pairs.
        """
        self._sessions = {key: state for key, state in sessions}
        self._tombstones = {key: tombstone for key, tombstone in tombstones}
