from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remote_mcp_adapter.core.storage import artifact_access as aa


def test_ensure_artifact_session_match():
    aa.ensure_artifact_session_match(expected_session_id="s", actual_session_id="s")
    with pytest.raises(aa.ArtifactSessionMismatchError):
        aa.ensure_artifact_session_match(expected_session_id="s1", actual_session_id="s2")


@pytest.mark.asyncio
async def test_resolve_artifact_for_read_branches(monkeypatch, tmp_path):
    class _Store:
        def __init__(self, record=None, err=False):
            self.record = record
            self.err = err

        async def get_artifact(self, **kwargs):
            if self.err:
                raise KeyError("missing")
            return self.record

    path = tmp_path / "file.txt"
    path.write_text("x", encoding="utf-8")
    record = SimpleNamespace(abs_path=path, filename="file.txt")

    resolved = await aa.resolve_artifact_for_read(
        store=_Store(record=record),
        server_id="srv",
        session_id="sess",
        artifact_id="a1",
        expected_filename="file.txt",
    )
    assert resolved is record

    with pytest.raises(aa.ArtifactNotFoundError):
        await aa.resolve_artifact_for_read(
            store=_Store(err=True),
            server_id="srv",
            session_id="sess",
            artifact_id="a1",
        )

    missing_record = SimpleNamespace(abs_path=Path(tmp_path / "missing.txt"), filename="missing.txt")
    with pytest.raises(aa.ArtifactFileMissingError):
        await aa.resolve_artifact_for_read(
            store=_Store(record=missing_record),
            server_id="srv",
            session_id="sess",
            artifact_id="a1",
        )

    with pytest.raises(aa.ArtifactFilenameMismatchError):
        await aa.resolve_artifact_for_read(
            store=_Store(record=record),
            server_id="srv",
            session_id="sess",
            artifact_id="a1",
            expected_filename="other.txt",
        )
