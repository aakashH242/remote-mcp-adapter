"""SQLite-backed state repository for durable adapter metadata."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import logging
from pathlib import Path
import sqlite3
from typing import Final

from .records import SessionState, SessionTombstone
from .state_codec import (
    dumps_payload,
    loads_payload,
    session_state_from_payload,
    session_state_to_payload,
    tombstone_from_payload,
    tombstone_to_payload,
)
from .state_repository import SessionKey, StateRepository

logger = logging.getLogger(__name__)

_SESSION_TABLE_NAME: Final[str] = "sessions"
_TOMBSTONE_TABLE_NAME: Final[str] = "tombstones"
_DEFAULT_BUSY_TIMEOUT_MS: Final[int] = 5000


class SqliteStateRepository(StateRepository):
    """Durable session/tombstone state backed by SQLite."""

    def __init__(self, *, db_path: Path, wal_enabled: bool, refresh_on_startup: bool):
        """Initialize the SQLite state repository.

        Args:
            db_path: Path to the SQLite database file.
            wal_enabled: Whether to enable WAL journal mode.
            refresh_on_startup: Whether to clear all rows on initialization.
        """
        self._db_path = db_path.resolve()
        self._wal_enabled = wal_enabled
        self._sessions: dict[SessionKey, SessionState] = {}
        self._tombstones: dict[SessionKey, SessionTombstone] = {}
        self._initialize(refresh_on_startup=refresh_on_startup)

    def _connect(self) -> sqlite3.Connection:
        """Open a new SQLite connection with WAL timeout and row factory configured."""
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={_DEFAULT_BUSY_TIMEOUT_MS};")
        return connection

    def _initialize(self, *, refresh_on_startup: bool) -> None:
        """Create tables, optionally clear them, then warm the in-memory cache.

        Args:
            refresh_on_startup: Whether to truncate all tables before loading.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            journal_mode = "WAL" if self._wal_enabled else "DELETE"
            connection.execute(f"PRAGMA journal_mode={journal_mode};")
            connection.execute(f"""
                CREATE TABLE IF NOT EXISTS {_SESSION_TABLE_NAME} (
                    server_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (server_id, session_id)
                )
                """)
            connection.execute(f"""
                CREATE TABLE IF NOT EXISTS {_TOMBSTONE_TABLE_NAME} (
                    server_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (server_id, session_id)
                )
                """)
            if refresh_on_startup:
                connection.execute(f"DELETE FROM {_SESSION_TABLE_NAME}")
                connection.execute(f"DELETE FROM {_TOMBSTONE_TABLE_NAME}")
        self._load_cache()
        logger.info(
            "Initialized SQLite state repository",
            extra={
                "db_path": str(self._db_path),
                "wal_enabled": self._wal_enabled,
                "refresh_on_startup": refresh_on_startup,
                "sessions_loaded": len(self._sessions),
                "tombstones_loaded": len(self._tombstones),
            },
        )

    def _load_cache(self) -> None:
        """Read all rows from SQLite into the in-memory write-through dicts."""
        sessions: dict[SessionKey, SessionState] = {}
        tombstones: dict[SessionKey, SessionTombstone] = {}
        with self._connect() as connection:
            session_rows = connection.execute(f"SELECT server_id, session_id, payload FROM {_SESSION_TABLE_NAME}").fetchall()
            tombstone_rows = connection.execute(
                f"SELECT server_id, session_id, payload FROM {_TOMBSTONE_TABLE_NAME}"
            ).fetchall()
        for row in session_rows:
            key = (str(row["server_id"]), str(row["session_id"]))
            payload = loads_payload(str(row["payload"]))
            sessions[key] = session_state_from_payload(payload)
        for row in tombstone_rows:
            key = (str(row["server_id"]), str(row["session_id"]))
            payload = loads_payload(str(row["payload"]))
            tombstones[key] = tombstone_from_payload(payload)
        self._sessions = sessions
        self._tombstones = tombstones

    def _upsert_session(self, key: SessionKey, payload_json: str) -> None:
        """INSERT OR UPDATE one session row in SQLite.

        Args:
            key: ``(server_id, session_id)`` tuple.
            payload_json: Serialized session payload.
        """
        with self._connect() as connection:
            with connection:
                connection.execute(
                    f"""
                    INSERT INTO {_SESSION_TABLE_NAME} (server_id, session_id, payload)
                    VALUES (?, ?, ?)
                    ON CONFLICT(server_id, session_id)
                    DO UPDATE SET payload=excluded.payload
                    """,
                    (key[0], key[1], payload_json),
                )

    def _delete_session(self, key: SessionKey) -> None:
        """Delete one session row from SQLite.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        with self._connect() as connection:
            with connection:
                connection.execute(
                    f"DELETE FROM {_SESSION_TABLE_NAME} WHERE server_id = ? AND session_id = ?",
                    (key[0], key[1]),
                )

    def _upsert_tombstone(self, key: SessionKey, payload_json: str) -> None:
        """INSERT OR UPDATE one tombstone row in SQLite.

        Args:
            key: ``(server_id, session_id)`` tuple.
            payload_json: Serialized tombstone payload.
        """
        with self._connect() as connection:
            with connection:
                connection.execute(
                    f"""
                    INSERT INTO {_TOMBSTONE_TABLE_NAME} (server_id, session_id, payload)
                    VALUES (?, ?, ?)
                    ON CONFLICT(server_id, session_id)
                    DO UPDATE SET payload=excluded.payload
                    """,
                    (key[0], key[1], payload_json),
                )

    def _delete_tombstone(self, key: SessionKey) -> None:
        """Delete one tombstone row from SQLite.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        with self._connect() as connection:
            with connection:
                connection.execute(
                    f"DELETE FROM {_TOMBSTONE_TABLE_NAME} WHERE server_id = ? AND session_id = ?",
                    (key[0], key[1]),
                )

    def _delete_all_rows(self) -> None:
        """Truncate both session and tombstone tables in one transaction."""
        with self._connect() as connection:
            with connection:
                connection.execute(f"DELETE FROM {_SESSION_TABLE_NAME}")
                connection.execute(f"DELETE FROM {_TOMBSTONE_TABLE_NAME}")

    def _replace_all_rows(
        self,
        *,
        sessions: Sequence[tuple[SessionKey, SessionState]],
        tombstones: Sequence[tuple[SessionKey, SessionTombstone]],
    ) -> None:
        """Atomically replace all SQLite rows with a new snapshot.

        Args:
            sessions: Sequence of ``(key, SessionState)`` tuples.
            tombstones: Sequence of ``(key, SessionTombstone)`` tuples.
        """
        session_payload_rows = [(key[0], key[1], dumps_payload(session_state_to_payload(state))) for key, state in sessions]
        tombstone_payload_rows = [
            (key[0], key[1], dumps_payload(tombstone_to_payload(tombstone))) for key, tombstone in tombstones
        ]
        with self._connect() as connection:
            with connection:
                connection.execute(f"DELETE FROM {_SESSION_TABLE_NAME}")
                connection.execute(f"DELETE FROM {_TOMBSTONE_TABLE_NAME}")
                if session_payload_rows:
                    connection.executemany(
                        f"""
                        INSERT INTO {_SESSION_TABLE_NAME} (server_id, session_id, payload)
                        VALUES (?, ?, ?)
                        """,
                        session_payload_rows,
                    )
                if tombstone_payload_rows:
                    connection.executemany(
                        f"""
                        INSERT INTO {_TOMBSTONE_TABLE_NAME} (server_id, session_id, payload)
                        VALUES (?, ?, ?)
                        """,
                        tombstone_payload_rows,
                    )

    async def get_session(self, key: SessionKey) -> SessionState | None:
        """Return session from the write-through cache, or None.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        return self._sessions.get(key)

    async def set_session(self, key: SessionKey, state: SessionState) -> None:
        """Write session to SQLite and update the write-through cache.

        Args:
            key: ``(server_id, session_id)`` tuple.
            state: Session state to persist.
        """
        payload_json = dumps_payload(session_state_to_payload(state))
        await asyncio.to_thread(self._upsert_session, key, payload_json)
        self._sessions[key] = state

    async def pop_session(self, key: SessionKey) -> SessionState | None:
        """Delete session from SQLite and cache; return previous value.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        state = self._sessions.get(key)
        if state is None:
            return None
        await asyncio.to_thread(self._delete_session, key)
        self._sessions.pop(key, None)
        return state

    async def session_count(self) -> int:
        """Return the write-through cache session count without hitting disk."""
        return len(self._sessions)

    async def list_session_items(self) -> list[tuple[SessionKey, SessionState]]:
        """Return a list snapshot of all cached sessions."""
        return list(self._sessions.items())

    async def get_tombstone(self, key: SessionKey) -> SessionTombstone | None:
        """Return tombstone from the write-through cache, or None.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        return self._tombstones.get(key)

    async def set_tombstone(self, key: SessionKey, tombstone: SessionTombstone) -> None:
        """Write tombstone to SQLite and update the write-through cache.

        Args:
            key: ``(server_id, session_id)`` tuple.
            tombstone: Tombstone to persist.
        """
        payload_json = dumps_payload(tombstone_to_payload(tombstone))
        await asyncio.to_thread(self._upsert_tombstone, key, payload_json)
        self._tombstones[key] = tombstone

    async def pop_tombstone(self, key: SessionKey) -> SessionTombstone | None:
        """Delete tombstone from SQLite and cache; return previous value.

        Args:
            key: ``(server_id, session_id)`` tuple.
        """
        tombstone = self._tombstones.get(key)
        if tombstone is None:
            return None
        await asyncio.to_thread(self._delete_tombstone, key)
        self._tombstones.pop(key, None)
        return tombstone

    async def list_tombstone_items(self) -> list[tuple[SessionKey, SessionTombstone]]:
        """Return a list snapshot of all cached tombstones."""
        return list(self._tombstones.items())

    async def drain(self) -> tuple[list[SessionState], list[SessionTombstone]]:
        """Return all values, truncate SQLite tables, then clear local caches."""
        session_states = list(self._sessions.values())
        tombstones = list(self._tombstones.values())
        await asyncio.to_thread(self._delete_all_rows)
        self._sessions.clear()
        self._tombstones.clear()
        return session_states, tombstones

    async def replace_all(
        self,
        *,
        sessions: Sequence[tuple[SessionKey, SessionState]],
        tombstones: Sequence[tuple[SessionKey, SessionTombstone]],
    ) -> None:
        """Atomically replace all stored rows with one new snapshot.

        Args:
            sessions: Sequence of ``(key, SessionState)`` tuples.
            tombstones: Sequence of ``(key, SessionTombstone)`` tuples.
        """
        await asyncio.to_thread(
            self._replace_all_rows,
            sessions=sessions,
            tombstones=tombstones,
        )
        self._sessions = {key: state for key, state in sessions}
        self._tombstones = {key: tombstone for key, tombstone in tombstones}

    def snapshot_items(self) -> tuple[list[tuple[SessionKey, SessionState]], list[tuple[SessionKey, SessionTombstone]]]:
        """Return cached rows for synchronous bootstrapping flows."""
        return (list(self._sessions.items()), list(self._tombstones.items()))
