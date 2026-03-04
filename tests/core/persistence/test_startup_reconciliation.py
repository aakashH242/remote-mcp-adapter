from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remote_mcp_adapter.core.persistence.startup_reconciliation import (
    StartupStateReconciler,
    run_startup_state_reconciliation,
)
from remote_mcp_adapter.core.repo.records import SessionState, SessionTombstone


class _Repo:
    def __init__(self, sessions=None, tombstones=None):
        self.sessions = dict(sessions or {})
        self.tombstones = dict(tombstones or {})

    async def list_session_items(self):
        return list(self.sessions.items())

    async def list_tombstone_items(self):
        return list(self.tombstones.items())

    async def set_session(self, key, state):
        self.sessions[key] = state


def _config(tmp_path, mode="always", refresh=False):
    return SimpleNamespace(
        storage=SimpleNamespace(root=str(tmp_path)),
        servers=[SimpleNamespace(id="s1")],
        artifacts=SimpleNamespace(expose_as_resources=True),
        state_persistence=SimpleNamespace(
            refresh_on_startup=refresh,
            reconciliation=SimpleNamespace(mode=mode, legacy_server_id=None),
        ),
    )


def _seed_file(path: Path, content: bytes = b"abc"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.mark.asyncio
async def test_reconcile_skip_paths(tmp_path):
    repo = _Repo()

    cfg_disabled = _config(tmp_path, mode="disabled")
    out = await StartupStateReconciler(config=cfg_disabled, state_repository=repo).reconcile()
    assert out["status"] == "disabled"

    cfg_refresh = _config(tmp_path, mode="if_empty", refresh=True)
    out2 = await StartupStateReconciler(config=cfg_refresh, state_repository=repo).reconcile()
    assert out2["status"] == "skipped"

    repo2 = _Repo(sessions={("s1", "sess"): SessionState(server_id="s1", session_id="sess", created_at=1.0, last_accessed=1.0)})
    cfg_if_empty = _config(tmp_path, mode="if_empty")
    out3 = await StartupStateReconciler(config=cfg_if_empty, state_repository=repo2).reconcile()
    assert out3["reason"] == "repository_not_empty"


@pytest.mark.asyncio
async def test_reconcile_applied_and_helpers(tmp_path):
    repo = _Repo()
    cfg = _config(tmp_path, mode="always")

    upload_file = tmp_path / "uploads" / "sessions" / "sess1" / "u1" / "a.txt"
    artifact_file = tmp_path / "artifacts" / "sessions" / "sess1" / "a1" / "b.txt"
    _seed_file(upload_file, b"up")
    _seed_file(artifact_file, b"art")

    reconciler = StartupStateReconciler(config=cfg, state_repository=repo)

    hints = reconciler._build_session_server_hints(session_items=[], tombstone_items=[])
    assert hints == {}
    assert reconciler._resolve_server_id("sess1", hints) == "s1"

    report = await reconciler.reconcile()
    assert report["status"] == "applied"
    assert report["uploads_backfilled"] >= 1
    assert report["artifacts_backfilled"] >= 1

    assert repo.sessions

    snapshot = reconciler._discover_filesystem_state()
    assert snapshot.upload_count >= 1
    assert snapshot.artifact_count >= 1

    created_at = reconciler._resolve_session_created_at({}, {})
    assert created_at == 0.0


@pytest.mark.asyncio
async def test_discovery_edge_cases_and_run_wrapper(tmp_path):
    cfg = _config(tmp_path, mode="always")
    repo = _Repo()
    reconciler = StartupStateReconciler(config=cfg, state_repository=repo)

    multi_dir = tmp_path / "uploads" / "sessions" / "sessx" / "ux"
    multi_dir.mkdir(parents=True)
    (multi_dir / "a.txt").write_text("a", encoding="utf-8")
    (multi_dir / "b.txt").write_text("b", encoding="utf-8")

    snapshot = reconciler._discover_filesystem_state()
    assert snapshot.skipped_entries >= 1

    state = SessionState(server_id="s1", session_id="sess1", created_at=1.0, last_accessed=1.0)
    tomb = SessionTombstone(state=state, expires_at=10.0)
    hints = reconciler._build_session_server_hints(session_items=[], tombstone_items=[(("s1", "sess1"), tomb)])
    assert hints["sess1"] == {"s1"}

    result = await run_startup_state_reconciliation(config=cfg, state_repository=repo)
    assert result["status"] in {"applied", "skipped"}
