from __future__ import annotations

from pathlib import Path

import pytest

from remote_mcp_adapter.core.repo.records import (
    ArtifactRecord,
    SessionState,
    SessionTombstone,
    UploadRecord,
)
from remote_mcp_adapter.core.repo.sqlite_state_repository import SqliteStateRepository


def _upload(upload_id: str, base: Path) -> UploadRecord:
    return UploadRecord(
        server_id="s1",
        session_id="sess",
        upload_id=upload_id,
        filename=f"{upload_id}.txt",
        abs_path=base / f"{upload_id}.txt",
        rel_path=f"uploads/{upload_id}.txt",
        mime_type="text/plain",
        size_bytes=10,
        sha256="abcd",
        created_at=1.0,
        last_accessed=1.0,
        last_updated=1.0,
    )


def _artifact(artifact_id: str, base: Path) -> ArtifactRecord:
    return ArtifactRecord(
        server_id="s1",
        session_id="sess",
        artifact_id=artifact_id,
        filename=f"{artifact_id}.png",
        abs_path=base / f"{artifact_id}.png",
        rel_path=f"artifacts/{artifact_id}.png",
        mime_type="image/png",
        size_bytes=20,
        created_at=2.0,
        last_accessed=2.0,
        last_updated=2.0,
        tool_name="tool",
        expose_as_resource=True,
        visibility_state="committed",
    )


def _state(base: Path) -> SessionState:
    state = SessionState(server_id="s1", session_id="sess", created_at=1.0, last_accessed=1.0, in_flight=1)
    state.uploads["u1"] = _upload("u1", base)
    state.artifacts["a1"] = _artifact("a1", base)
    return state


@pytest.mark.asyncio
async def test_sqlite_state_repository_round_trip_and_cache_reload(tmp_path):
    db_path = tmp_path / "state" / "adapter.sqlite3"
    repo = SqliteStateRepository(db_path=db_path, wal_enabled=True, refresh_on_startup=False)
    key = ("s1", "sess")
    state = _state(tmp_path)
    tombstone = SessionTombstone(state=state, expires_at=999.0)

    assert await repo.get_session(key) is None
    await repo.set_session(key, state)
    assert await repo.get_session(key) is state
    assert await repo.session_count() == 1
    assert await repo.list_session_items() == [(key, state)]

    await repo.set_tombstone(key, tombstone)
    assert await repo.get_tombstone(key) is tombstone
    assert await repo.list_tombstone_items() == [(key, tombstone)]
    assert repo.snapshot_items() == ([(key, state)], [(key, tombstone)])

    reloaded = SqliteStateRepository(db_path=db_path, wal_enabled=True, refresh_on_startup=False)
    loaded_state = await reloaded.get_session(key)
    loaded_tombstone = await reloaded.get_tombstone(key)
    assert loaded_state is not None
    assert loaded_tombstone is not None
    assert loaded_state.session_id == "sess"
    assert loaded_tombstone.expires_at == 999.0

    popped_state = await reloaded.pop_session(key)
    popped_tombstone = await reloaded.pop_tombstone(key)
    assert popped_state is not None
    assert popped_tombstone is not None
    assert await reloaded.pop_session(key) is None
    assert await reloaded.pop_tombstone(key) is None


@pytest.mark.asyncio
async def test_sqlite_state_repository_replace_drain_and_refresh(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    repo = SqliteStateRepository(db_path=db_path, wal_enabled=True, refresh_on_startup=False)

    session_a = _state(tmp_path)
    session_b = SessionState(server_id="s2", session_id="sess2", created_at=3.0, last_accessed=3.0)
    tombstone = SessionTombstone(state=session_a, expires_at=888.0)

    await repo.replace_all(
        sessions=[(("s1", "sess"), session_a), (("s2", "sess2"), session_b)],
        tombstones=[(("s1", "sess"), tombstone)],
    )

    session_states, tombstones = await repo.drain()
    assert [item.session_id for item in session_states] == ["sess", "sess2"]
    assert [item.expires_at for item in tombstones] == [888.0]
    assert await repo.session_count() == 0
    assert await repo.list_tombstone_items() == []

    await repo.set_session(("s1", "sess"), session_a)
    cleared = SqliteStateRepository(db_path=db_path, wal_enabled=True, refresh_on_startup=True)
    assert await cleared.session_count() == 0
    assert await cleared.list_session_items() == []


@pytest.mark.asyncio
async def test_sqlite_state_repository_supports_delete_journal_mode(tmp_path):
    db_path = tmp_path / "delete-mode.sqlite3"

    repo = SqliteStateRepository(db_path=db_path, wal_enabled=False, refresh_on_startup=False)

    assert await repo.session_count() == 0
    assert await repo.list_session_items() == []
