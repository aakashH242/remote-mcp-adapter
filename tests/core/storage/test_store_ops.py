from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from remote_mcp_adapter.core.repo.records import ArtifactRecord, SessionState, UploadRecord
from remote_mcp_adapter.core.storage.store_ops import StoreOps


def _cfg(eviction_policy="lru_uploads_then_artifacts", max_session=100, max_storage=None):
    return SimpleNamespace(
        sessions=SimpleNamespace(eviction_policy=eviction_policy, max_total_session_size=max_session),
        storage=SimpleNamespace(max_size=max_storage),
    )


def _upload(path: Path, upload_id="u1", size=5, last_accessed=1.0):
    return UploadRecord(
        server_id="s1",
        session_id="sess",
        upload_id=upload_id,
        filename=path.name,
        abs_path=path,
        rel_path="x",
        mime_type="text/plain",
        size_bytes=size,
        sha256="a",
        created_at=1.0,
        last_accessed=last_accessed,
        last_updated=1.0,
    )


def _artifact(path: Path, artifact_id="a1", size=5, last_accessed=1.0):
    return ArtifactRecord(
        server_id="s1",
        session_id="sess",
        artifact_id=artifact_id,
        filename=path.name,
        abs_path=path,
        rel_path="x",
        mime_type="text/plain",
        size_bytes=size,
        created_at=1.0,
        last_accessed=last_accessed,
        last_updated=1.0,
        visibility_state="committed",
    )


def _state():
    return SessionState(server_id="s1", session_id="sess", created_at=1.0, last_accessed=1.0)


def _ops(tmp_path, cfg):
    return StoreOps(
        config=cfg,
        storage_root=tmp_path,
        upload_session_dir=lambda sid: tmp_path / "uploads" / sid,
        artifact_session_dir=lambda sid: tmp_path / "artifacts" / sid,
    )


def test_remove_and_quota_sync(tmp_path):
    cfg = _cfg()
    ops = _ops(tmp_path, cfg)
    state = _state()

    up_dir = tmp_path / "uploads" / "sess" / "u1"
    up_dir.mkdir(parents=True)
    up_path = up_dir / "f.txt"
    up_path.write_text("x", encoding="utf-8")
    state.uploads["u1"] = _upload(up_path, size=10)

    art_dir = tmp_path / "artifacts" / "sess" / "a1"
    art_dir.mkdir(parents=True)
    art_path = art_dir / "f.txt"
    art_path.write_text("x", encoding="utf-8")
    state.artifacts["a1"] = _artifact(art_path, size=20)

    assert ops.session_total_bytes(state) == 30

    removed_u = ops.remove_upload_record(state, "u1")
    assert removed_u == (True, 10)
    assert ops.remove_upload_record(state, "missing") == (False, 0)

    removed_a = ops.remove_artifact_record(state, "a1")
    assert removed_a == (True, 20)
    assert ops.remove_artifact_record(state, "missing") == (False, 0)

    state2 = _state()
    p1 = tmp_path / "uploads" / "sess" / "u2" / "x.txt"
    p1.parent.mkdir(parents=True)
    p1.write_text("x", encoding="utf-8")
    p2 = tmp_path / "artifacts" / "sess" / "a2" / "x.txt"
    p2.parent.mkdir(parents=True)
    p2.write_text("x", encoding="utf-8")
    state2.uploads["u2"] = _upload(p1, upload_id="u2", size=80, last_accessed=1)
    state2.artifacts["a2"] = _artifact(p2, artifact_id="a2", size=80, last_accessed=2)

    ops.enforce_session_quota(state2, incoming_bytes=10)
    with pytest.raises(ToolError):
        ops.enforce_session_quota(state2, incoming_bytes=1000)


def test_storage_and_orphan_sync(tmp_path):
    cfg = _cfg(max_session=None, max_storage=10)
    ops = _ops(tmp_path, cfg)

    f = tmp_path / "d" / "a.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"12345678901")

    total = ops._storage_total_bytes()
    assert total >= 11

    removed = ops._purge_orphan_files_sync(root=tmp_path / "d", referenced_paths=set(), older_than_epoch=10**12)
    assert removed >= 1


@pytest.mark.asyncio
async def test_async_variants(tmp_path):
    cfg = _cfg(max_session=100, max_storage=1)
    ops = _ops(tmp_path, cfg)
    state = _state()

    up_path = tmp_path / "uploads" / "sess" / "u1" / "f.txt"
    up_path.parent.mkdir(parents=True)
    up_path.write_text("x", encoding="utf-8")
    state.uploads["u1"] = _upload(up_path, size=2)

    art_path = tmp_path / "artifacts" / "sess" / "a1" / "f.txt"
    art_path.parent.mkdir(parents=True)
    art_path.write_text("x", encoding="utf-8")
    state.artifacts["a1"] = _artifact(art_path, size=2)

    assert await ops.remove_upload_record_async(state, "u1") == (True, 2)
    assert await ops.remove_upload_record_async(state, "missing") == (False, 0)

    assert await ops.remove_artifact_record_async(state, "a1") == (True, 2)
    assert await ops.remove_artifact_record_async(state, "missing") == (False, 0)

    await ops.purge_empty_session_dirs_async("sess")

    state2 = _state()
    up2 = tmp_path / "uploads" / "sess" / "u2" / "x.txt"
    up2.parent.mkdir(parents=True, exist_ok=True)
    up2.write_text("x", encoding="utf-8")
    state2.uploads["u2"] = _upload(up2, upload_id="u2", size=2)
    await ops.purge_state_files_async(state2)

    quota_file = tmp_path / "quota" / "big.bin"
    quota_file.parent.mkdir(parents=True, exist_ok=True)
    quota_file.write_bytes(b"12345")

    with pytest.raises(ToolError):
        await ops.enforce_global_storage_quota()

    removed = await ops.purge_orphan_files_async(root=tmp_path, referenced_paths=set(), older_than_epoch=10**12)
    assert removed >= 0
