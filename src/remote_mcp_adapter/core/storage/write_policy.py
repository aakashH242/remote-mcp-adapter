"""Best-effort write policy helpers for storage locking and atomic writes."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
import os
from pathlib import Path
import time
from typing import Literal
from uuid import uuid4

from fastmcp.exceptions import ToolError

from ..locks.lock_provider import LockProvider

LockMode = Literal["none", "process", "file", "redis"]

_PROCESS_LOCKS: dict[str, asyncio.Lock] = {}
_PROCESS_LOCKS_GUARD = asyncio.Lock()
_LOCK_WAIT_TIMEOUT_SECONDS = 10.0
_LOCK_RETRY_DELAY_SECONDS = 0.05
_REDIS_STORAGE_LOCK_PROVIDER: LockProvider | None = None


def set_redis_storage_lock_provider(lock_provider: LockProvider | None) -> None:
    """Set module-level distributed lock provider for ``lock_mode='redis'``.

    Args:
        lock_provider: Redis lock provider, or None to unset.
    """
    global _REDIS_STORAGE_LOCK_PROVIDER
    _REDIS_STORAGE_LOCK_PROVIDER = lock_provider


def _fsync_if_possible(file_descriptor: int) -> None:
    """Best-effort fsync for durability; skipped when unsupported.

    Args:
        file_descriptor: Open file descriptor to sync.
    """
    with contextlib.suppress(OSError):
        os.fsync(file_descriptor)


def _copy_file_bytes(source_path: Path, target_path: Path) -> None:
    """Copy one file and fsync written data before commit.

    Args:
        source_path: Source file to read.
        target_path: Destination file to write.
    """
    with source_path.open("rb") as src_handle, target_path.open("wb") as dst_handle:
        while True:
            chunk = src_handle.read(1024 * 1024)
            if not chunk:
                break
            dst_handle.write(chunk)
        dst_handle.flush()
        _fsync_if_possible(dst_handle.fileno())


def _write_bytes_to_path(target_path: Path, data: bytes) -> None:
    """Write bytes and fsync data before commit.

    Args:
        target_path: Destination file path.
        data: Bytes to write.
    """
    with target_path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        _fsync_if_possible(handle.fileno())


def _is_same_path(source_path: Path, target_path: Path) -> bool:
    """Return True when both paths resolve to the same filesystem entry.

    Args:
        source_path: First path.
        target_path: Second path.
    """
    with contextlib.suppress(OSError):
        return source_path.resolve() == target_path.resolve()
    return False


async def _get_process_lock(path: Path) -> asyncio.Lock:
    """Return the process-scoped asyncio.Lock for the given resolved path.

    Args:
        path: Target path to derive a lock key from.
    """
    key = str(path.resolve())
    async with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _PROCESS_LOCKS[key] = lock
        return lock


def _lock_file_path(target_path: Path) -> Path:
    """Return the advisory lock file path for *target_path*.

    Args:
        target_path: File being locked.
    """
    return target_path.parent / f".{target_path.name}.lock"


def _redis_lock_name(target_path: Path) -> str:
    """Build a deterministic Redis lock name from the resolved target path.

    Args:
        target_path: File path to derive the lock name from.
    """
    with contextlib.suppress(OSError):
        return f"storage-write:{target_path.resolve().as_posix()}"
    return f"storage-write:{target_path.as_posix()}"


def _create_lock_file(lock_file: Path) -> int:
    """Create an exclusive lock file, returning the open file descriptor.

    Args:
        lock_file: Path to the lock file to create.
    """
    fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
    return fd


def _close_fd(fd: int) -> None:
    """Close an open file descriptor.

    Args:
        fd: File descriptor to close.
    """
    os.close(fd)


def _unlink_lock_file(lock_file: Path) -> None:
    """Remove the advisory lock file after releasing the lock.

    Args:
        lock_file: Path to the lock file to remove.
    """
    lock_file.unlink()


@asynccontextmanager
async def storage_write_lock(target_path: Path, lock_mode: LockMode):
    """Acquire best-effort lock for one target path.

    Args:
        target_path: File path to protect.
        lock_mode: Locking strategy (``'none'``, ``'process'``, ``'file'``, ``'redis'``).

    Raises:
        ToolError: If the lock mode is unsupported or the lock cannot be acquired.
    """
    if lock_mode == "none":
        yield
        return

    if lock_mode == "process":
        lock = await _get_process_lock(target_path)
        async with lock:
            yield
        return

    if lock_mode == "redis":
        if _REDIS_STORAGE_LOCK_PROVIDER is None:
            raise ToolError("Redis storage lock provider is not configured.")
        async with _REDIS_STORAGE_LOCK_PROVIDER.hold(_redis_lock_name(target_path)):
            yield
        return

    if lock_mode != "file":
        raise ToolError(f"Unsupported lock_mode: {lock_mode}")

    lock_file = _lock_file_path(target_path)
    fd: int | None = None
    deadline = time.monotonic() + _LOCK_WAIT_TIMEOUT_SECONDS
    while True:
        try:
            fd = await asyncio.to_thread(_create_lock_file, lock_file)
            break
        except FileExistsError as exc:
            if time.monotonic() >= deadline:
                raise ToolError(f"Timed out waiting for file lock: {lock_file}") from exc
            await asyncio.sleep(_LOCK_RETRY_DELAY_SECONDS)

    try:
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(_close_fd, fd)
        with contextlib.suppress(OSError):
            await asyncio.to_thread(_unlink_lock_file, lock_file)


def create_staged_path(target_path: Path, atomic_writes: bool) -> Path:
    """Return staging path for writes (target itself when atomic mode is disabled).

    Args:
        target_path: Final destination path.
        atomic_writes: Whether atomic writes are enabled.
    """
    if not atomic_writes:
        return target_path
    return target_path.parent / f".{target_path.name}.{uuid4().hex}.tmp"


def commit_staged_path(staged_path: Path, target_path: Path, atomic_writes: bool) -> None:
    """Promote staged file to target path in atomic mode.

    Args:
        staged_path: Temporary staging file.
        target_path: Final destination path.
        atomic_writes: Whether atomic writes are enabled.
    """
    if atomic_writes:
        os.replace(staged_path, target_path)


def cleanup_staged_path(staged_path: Path, target_path: Path, atomic_writes: bool) -> None:
    """Remove staged/target file after failed write.

    Args:
        staged_path: Temporary staging file.
        target_path: Final destination path.
        atomic_writes: Whether atomic writes are enabled.
    """
    cleanup_target = staged_path if atomic_writes else target_path
    cleanup_target.unlink(missing_ok=True)


async def copy_file_with_policy(
    *,
    source_path: Path,
    target_path: Path,
    atomic_writes: bool,
    lock_mode: LockMode,
) -> None:
    """Copy one file honoring configured lock mode and atomic write mode.

    Args:
        source_path: Source file to copy.
        target_path: Destination file path.
        atomic_writes: Whether atomic writes are enabled.
        lock_mode: Locking strategy.
    """
    async with storage_write_lock(target_path, lock_mode):
        if _is_same_path(source_path, target_path):
            return
        staged_path = create_staged_path(target_path, atomic_writes)
        try:
            await asyncio.to_thread(_copy_file_bytes, source_path, staged_path)
            await asyncio.to_thread(commit_staged_path, staged_path, target_path, atomic_writes)
        except Exception:
            await asyncio.to_thread(cleanup_staged_path, staged_path, target_path, atomic_writes)
            raise


async def write_bytes_with_policy(
    *,
    target_path: Path,
    data: bytes,
    atomic_writes: bool,
    lock_mode: LockMode,
) -> None:
    """Write bytes to target path honoring configured lock mode and atomic mode.

    Args:
        target_path: Destination file path.
        data: Bytes to write.
        atomic_writes: Whether atomic writes are enabled.
        lock_mode: Locking strategy.
    """
    async with storage_write_lock(target_path, lock_mode):
        staged_path = create_staged_path(target_path, atomic_writes)
        try:
            await asyncio.to_thread(_write_bytes_to_path, staged_path, data)
            await asyncio.to_thread(commit_staged_path, staged_path, target_path, atomic_writes)
        except Exception:
            await asyncio.to_thread(cleanup_staged_path, staged_path, target_path, atomic_writes)
            raise
