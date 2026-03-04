from __future__ import annotations

import hashlib

import pytest

from remote_mcp_adapter.core.storage import storage_utils as su


def test_sanitize_filename_variants():
    assert su.sanitize_filename("a/b?.txt", default_name="x") == "b_.txt"
    assert su.sanitize_filename("...", default_name="fallback") == "fallback"
    assert su.sanitize_filename(None, default_name="fallback") == "fallback"
    assert su.sanitize_filename("name", default_name="x", default_ext="txt") == "name.txt"
    assert su.sanitize_filename("name", default_name="x", default_ext=".log") == "name.log"


def test_ensure_within_base(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    child = base / "d" / "f.txt"
    child.parent.mkdir(parents=True)
    child.write_text("x", encoding="utf-8")

    assert su.ensure_within_base(child, base) == child.resolve()
    assert su.ensure_within_base(base, base) == base.resolve()

    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        su.ensure_within_base(outside, base)


def test_sha256_file_and_parse_session_scoped_uri(tmp_path):
    path = tmp_path / "data.bin"
    content = b"abcdef"
    path.write_bytes(content)
    assert su.sha256_file(path) == hashlib.sha256(content).hexdigest()

    assert su.parse_session_scoped_uri("upload://sessions/s1/u1", "upload://") == ("s1", "u1")
    assert su.parse_session_scoped_uri("artifact://sessions/s2/a2/extra", "artifact://") == ("s2", "a2")

    with pytest.raises(ValueError):
        su.parse_session_scoped_uri("upload://sessions/s1", "upload://")
    with pytest.raises(ValueError):
        su.parse_session_scoped_uri("upload://bad/s1/u1", "upload://")
    with pytest.raises(ValueError):
        su.parse_session_scoped_uri("upload://sessions/s1/u1", "bad")
    with pytest.raises(ValueError):
        su.parse_session_scoped_uri("upload://sessions/s1/u1", "artifact://")
