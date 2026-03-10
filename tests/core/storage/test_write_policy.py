from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastmcp.exceptions import ToolError

from remote_mcp_adapter.core.storage import write_policy as wp


@pytest.mark.asyncio
async def test_storage_write_lock_modes(tmp_path):
    target = tmp_path / "a.txt"

    async with wp.storage_write_lock(target, "none"):
        pass

    async with wp.storage_write_lock(target, "process"):
        pass

    with pytest.raises(ToolError):
        async with wp.storage_write_lock(target, "redis"):
            pass

    with pytest.raises(ToolError):
        async with wp.storage_write_lock(target, "bad"):
            pass


@pytest.mark.asyncio
async def test_storage_write_lock_file_and_redis(tmp_path, monkeypatch):
    target = tmp_path / "f.txt"
    lock_file = wp._lock_file_path(target)

    async with wp.storage_write_lock(target, "file"):
        assert lock_file.exists()
    assert not lock_file.exists()

    holder = []

    class _Provider:
        @asynccontextmanager
        async def hold(self, name):
            holder.append(name)
            yield

    provider = _Provider()
    wp.set_redis_storage_lock_provider(provider)
    async with wp.storage_write_lock(target, "redis"):
        pass
    assert holder and holder[-1].startswith("storage-write:")
    wp.set_redis_storage_lock_provider(None)


def test_staged_path_commit_cleanup(tmp_path):
    target = tmp_path / "x.txt"
    staged = wp.create_staged_path(target, True)
    assert staged != target

    staged.write_text("hello", encoding="utf-8")
    wp.commit_staged_path(staged, target, True)
    assert target.read_text(encoding="utf-8") == "hello"

    staged2 = wp.create_staged_path(target, True)
    staged2.write_text("x", encoding="utf-8")
    wp.cleanup_staged_path(staged2, target, True)
    assert not staged2.exists()

    target.write_text("z", encoding="utf-8")
    wp.cleanup_staged_path(target, target, False)
    assert not target.exists()


@pytest.mark.asyncio
async def test_copy_and_write_with_policy(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"abc")

    await wp.copy_file_with_policy(source_path=src, target_path=dst, atomic_writes=True, lock_mode="process")
    assert dst.read_bytes() == b"abc"

    same = tmp_path / "same.bin"
    same.write_bytes(b"x")
    await wp.copy_file_with_policy(source_path=same, target_path=same, atomic_writes=True, lock_mode="process")

    await wp.write_bytes_with_policy(target_path=dst, data=b"hello", atomic_writes=True, lock_mode="process")
    assert dst.read_bytes() == b"hello"

    async def failing_to_thread(func, *args, **kwargs):
        if func is wp._copy_file_bytes:
            raise RuntimeError("copy")
        return func(*args, **kwargs)

    monkeypatch.setattr(wp.asyncio, "to_thread", failing_to_thread)
    with pytest.raises(RuntimeError):
        await wp.copy_file_with_policy(source_path=src, target_path=dst, atomic_writes=True, lock_mode="process")
