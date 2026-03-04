from __future__ import annotations

import io
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from remote_mcp_adapter.core.storage import uploads as up


def _config(enabled=True, require_sha=False, atomic=True, max_bytes=1024):
    return SimpleNamespace(
        uploads=SimpleNamespace(enabled=enabled, require_sha256=require_sha, max_file_bytes=max_bytes),
        storage=SimpleNamespace(atomic_writes=atomic),
    )


def test_normalize_expected_sha256_and_cleanup(tmp_path):
    assert up._normalize_expected_sha256(None) is None
    assert up._normalize_expected_sha256("  ABCD  ") == "abcd"
    assert up._normalize_expected_sha256("   ") is None

    d = tmp_path / "d"
    d.mkdir()
    (d / "x.txt").write_text("x", encoding="utf-8")
    up._cleanup_dir(d)
    assert not d.exists()


def test_persist_upload_stream_sync(tmp_path):
    out = tmp_path / "f.bin"
    digest = up._persist_upload_stream_sync(stream=io.BytesIO(b"abc"), staged_path=out, max_file_bytes=10)
    assert out.read_bytes() == b"abc"
    assert len(digest) == 64

    with pytest.raises(ToolError):
        up._persist_upload_stream_sync(stream=io.BytesIO(b"0123456789"), staged_path=out, max_file_bytes=3)


@pytest.mark.asyncio
async def test_save_upload_stream_paths(monkeypatch, tmp_path):
    cfg = _config(enabled=False)
    store = SimpleNamespace()
    with pytest.raises(ToolError):
        await up.save_upload_stream(
            config=cfg,
            store=store,
            server_id="s1",
            session_id="sess",
            filename="a.txt",
            stream=io.BytesIO(b"a"),
        )

    cfg = _config(enabled=True, require_sha=True)
    with pytest.raises(ToolError):
        await up.save_upload_stream(
            config=cfg,
            store=store,
            server_id="s1",
            session_id="sess",
            filename="a.txt",
            stream=io.BytesIO(b"a"),
        )

    cfg = _config(enabled=True, require_sha=False)
    abs_path = tmp_path / "uploads" / "a.txt"
    abs_path.parent.mkdir(parents=True)

    async def allocate_upload_path(**kwargs):
        return "u1", abs_path, "uploads/a.txt"

    async def register_upload(**kwargs):
        return {"ok": True, **kwargs}

    store = SimpleNamespace(allocate_upload_path=allocate_upload_path, register_upload=register_upload)

    monkeypatch.setattr(up, "resolve_write_policy_lock_mode", lambda config: "process")

    @asynccontextmanager
    async def fake_storage_write_lock(path, mode):
        yield

    monkeypatch.setattr(up, "storage_write_lock", fake_storage_write_lock)

    result = await up.save_upload_stream(
        config=cfg,
        store=store,
        server_id="s1",
        session_id="sess",
        filename="a.txt",
        stream=io.BytesIO(b"abc"),
        sha256_expected=None,
    )
    assert result["upload_id"] == "u1"

    with pytest.raises(ToolError):
        await up.save_upload_stream(
            config=cfg,
            store=store,
            server_id="s1",
            session_id="sess",
            filename="a.txt",
            stream=io.BytesIO(b"abc"),
            sha256_expected="deadbeef",
        )

    monkeypatch.setattr(up, "resolve_write_policy_lock_mode", lambda config: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(ToolError):
        await up.save_upload_stream(
            config=cfg,
            store=store,
            server_id="s1",
            session_id="sess",
            filename="a.txt",
            stream=io.BytesIO(b"abc"),
            sha256_expected=None,
        )
