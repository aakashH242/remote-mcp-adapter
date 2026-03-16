"""Core state and storage primitives for the adapter."""

from .locks.lock_provider import InMemoryLockProvider, LockProvider
from .persistence.memory_snapshot import MemorySnapshotManager
from .persistence.persistence_factory import (
    PersistenceRuntime,
    build_memory_persistence_runtime,
    build_persistence_runtime,
    build_state_repository,
)
from .persistence.persistence_policy import PersistencePolicyController, PersistenceUnavailableError
from .locks.redis_lock_provider import RedisLockProvider
from .repo.redis_state_repository import RedisStateRepository
from .repo.sqlite_state_repository import SqliteStateRepository
from .persistence.startup_reconciliation import StartupStateReconciler, run_startup_state_reconciliation
from .repo.state_repository import InMemoryStateRepository, StateRepository
from .storage.errors import SessionTrustContextMismatchError, TerminalSessionInvalidatedError
from .storage.store import SessionStore

__all__ = [
    "build_persistence_runtime",
    "build_state_repository",
    "build_memory_persistence_runtime",
    "InMemoryLockProvider",
    "InMemoryStateRepository",
    "LockProvider",
    "MemorySnapshotManager",
    "PersistenceRuntime",
    "PersistencePolicyController",
    "PersistenceUnavailableError",
    "RedisLockProvider",
    "RedisStateRepository",
    "SessionStore",
    "SqliteStateRepository",
    "StartupStateReconciler",
    "StateRepository",
    "SessionTrustContextMismatchError",
    "TerminalSessionInvalidatedError",
    "run_startup_state_reconciliation",
]
