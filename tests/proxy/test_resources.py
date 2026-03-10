from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ResourceError, ToolError

from remote_mcp_adapter.core.repo.records import ArtifactRecord
from remote_mcp_adapter.core.storage.artifact_access import (
    ArtifactFileMissingError,
    ArtifactFilenameMismatchError,
    ArtifactNotFoundError,
    ArtifactSessionMismatchError,
)
from remote_mcp_adapter.proxy import resources as res


def _record(*, expose=True, mime_type="text/plain", abs_path: Path | None = None):
    path = abs_path or Path("D:/tmp/f.txt")
    return ArtifactRecord(
        server_id="srv",
        session_id="sess",
        artifact_id="a1",
        filename="f.txt",
        abs_path=path,
        rel_path="r/f.txt",
        mime_type=mime_type,
        size_bytes=3,
        created_at=0.0,
        last_accessed=0.0,
        last_updated=0.0,
        tool_name="tool",
        expose_as_resource=expose,
        visibility_state="committed",
    )


class _FakeStore:
    def __init__(self, *, list_records=None, resolved_record=None, resolve_exc=None):
        self._list_records = list_records or []
        self._resolved_record = resolved_record
        self._resolve_exc = resolve_exc
        self.list_calls = []

    async def list_artifacts(self, **kwargs):
        self.list_calls.append(kwargs)
        return list(self._list_records)

    async def resolve_artifact_uri(self, **kwargs):
        if self._resolve_exc is not None:
            raise self._resolve_exc
        return self._resolved_record


def test_artifact_uri_builder():
    assert res._artifact_uri("artifact://", "s", "a", "f.txt") == "artifact://sessions/s/a/f.txt"


@pytest.mark.parametrize(
    "error, message",
    [
        (ArtifactSessionMismatchError("x"), "Artifact session mismatch."),
        (ArtifactNotFoundError("x"), "Artifact not found."),
        (ArtifactFilenameMismatchError("x"), "Artifact not found."),
        (ArtifactFileMissingError("x"), "Artifact file missing."),
    ],
)
def test_raise_resource_error_for_known_artifact_errors(error, message):
    with pytest.raises(ResourceError, match=message):
        res._raise_resource_error_for_artifact_access(error)


def test_raise_resource_error_re_raises_unknown_error():
    with pytest.raises(RuntimeError, match="boom"):
        res._raise_resource_error_for_artifact_access(RuntimeError("boom"))


@pytest.mark.asyncio
async def test_record_to_resource_reads_text(monkeypatch):
    class FakePath:
        def read_bytes(self):
            return b"hello"

    record = _record(mime_type="text/plain", abs_path=FakePath())
    provider = res.SessionArtifactProvider(store=_FakeStore(), server_id="srv", uri_scheme="artifact://", enabled=True)

    monkeypatch.setattr(res, "get_context", lambda: SimpleNamespace(session_id="sess"))
    monkeypatch.setattr(res, "ensure_artifact_session_match", lambda **kwargs: None)

    async def fake_resolve(**kwargs):
        return record

    monkeypatch.setattr(res, "resolve_artifact_for_read", fake_resolve)

    class CapturedResource:
        def __init__(self, fn):
            self.fn = fn

    monkeypatch.setattr(
        res.Resource,
        "from_function",
        staticmethod(lambda **kwargs: CapturedResource(kwargs["fn"])),
    )

    resource = provider._record_to_resource(record)
    assert await resource.fn() == "hello"


@pytest.mark.asyncio
async def test_record_to_resource_reads_binary(monkeypatch):
    class FakePath:
        def read_bytes(self):
            return b"\x00\x01"

    record = _record(mime_type="application/octet-stream", abs_path=FakePath())
    provider = res.SessionArtifactProvider(store=_FakeStore(), server_id="srv", uri_scheme="artifact://", enabled=True)

    monkeypatch.setattr(res, "get_context", lambda: SimpleNamespace(session_id="sess"))
    monkeypatch.setattr(res, "ensure_artifact_session_match", lambda **kwargs: None)

    async def fake_resolve(**kwargs):
        return record

    monkeypatch.setattr(res, "resolve_artifact_for_read", fake_resolve)

    class CapturedResource:
        def __init__(self, fn):
            self.fn = fn

    monkeypatch.setattr(res.Resource, "from_function", staticmethod(lambda **kwargs: CapturedResource(kwargs["fn"])))

    resource = provider._record_to_resource(record)
    assert await resource.fn() == b"\x00\x01"


@pytest.mark.asyncio
async def test_record_to_resource_maps_session_mismatch_to_resource_error(monkeypatch):
    record = _record()
    provider = res.SessionArtifactProvider(store=_FakeStore(), server_id="srv", uri_scheme="artifact://", enabled=True)

    monkeypatch.setattr(res, "get_context", lambda: SimpleNamespace(session_id="other"))

    def bad_match(**kwargs):
        raise ArtifactSessionMismatchError("no")

    monkeypatch.setattr(res, "ensure_artifact_session_match", bad_match)

    class CapturedResource:
        def __init__(self, fn):
            self.fn = fn

    monkeypatch.setattr(res.Resource, "from_function", staticmethod(lambda **kwargs: CapturedResource(kwargs["fn"])))

    resource = provider._record_to_resource(record)
    with pytest.raises(ResourceError, match="Artifact session mismatch"):
        await resource.fn()


@pytest.mark.asyncio
async def test_record_to_resource_maps_resolve_errors_to_resource_error(monkeypatch):
    record = _record()
    provider = res.SessionArtifactProvider(store=_FakeStore(), server_id="srv", uri_scheme="artifact://", enabled=True)

    monkeypatch.setattr(res, "get_context", lambda: SimpleNamespace(session_id="sess"))
    monkeypatch.setattr(res, "ensure_artifact_session_match", lambda **kwargs: None)

    class CapturedResource:
        def __init__(self, fn):
            self.fn = fn

    monkeypatch.setattr(res.Resource, "from_function", staticmethod(lambda **kwargs: CapturedResource(kwargs["fn"])))

    async def raise_not_found(**kwargs):
        raise ArtifactNotFoundError("x")

    monkeypatch.setattr(res, "resolve_artifact_for_read", raise_not_found)
    resource = provider._record_to_resource(record)
    with pytest.raises(ResourceError, match="Artifact not found"):
        await resource.fn()

    async def raise_missing(**kwargs):
        raise ArtifactFileMissingError("x")

    monkeypatch.setattr(res, "resolve_artifact_for_read", raise_missing)
    resource = provider._record_to_resource(record)
    with pytest.raises(ResourceError, match="Artifact file missing"):
        await resource.fn()

    async def raise_mismatch(**kwargs):
        raise ArtifactFilenameMismatchError("x")

    monkeypatch.setattr(res, "resolve_artifact_for_read", raise_mismatch)
    resource = provider._record_to_resource(record)
    with pytest.raises(ResourceError, match="Artifact not found"):
        await resource.fn()


@pytest.mark.asyncio
async def test_list_resources_covers_disabled_context_error_and_filtering(monkeypatch):
    exposed = _record(expose=True)
    hidden = _record(expose=False)
    store = _FakeStore(list_records=[exposed, hidden])
    provider = res.SessionArtifactProvider(store=store, server_id="srv", uri_scheme="artifact://", enabled=True)

    monkeypatch.setattr(provider, "_record_to_resource", lambda record: f"res-{record.artifact_id}")

    disabled_provider = res.SessionArtifactProvider(store=store, server_id="srv", uri_scheme="artifact://", enabled=False)
    assert await disabled_provider._list_resources() == []

    def raise_context_error():
        raise RuntimeError("no context")

    monkeypatch.setattr(res, "get_context", raise_context_error)
    assert await provider._list_resources() == []

    monkeypatch.setattr(res, "get_context", lambda: SimpleNamespace(session_id="sess"))
    listed = await provider._list_resources()
    assert listed == ["res-a1"]


@pytest.mark.asyncio
async def test_get_resource_covers_all_paths(monkeypatch):
    record_visible = _record(expose=True)
    record_hidden = _record(expose=False)

    provider_disabled = res.SessionArtifactProvider(
        store=_FakeStore(),
        server_id="srv",
        uri_scheme="artifact://",
        enabled=False,
    )
    assert await provider_disabled._get_resource("artifact://sessions/sess/a1/f.txt") is None

    provider = res.SessionArtifactProvider(store=_FakeStore(), server_id="srv", uri_scheme="artifact://", enabled=True)
    assert await provider._get_resource("other://bad") is None

    def raise_context_error():
        raise RuntimeError("no ctx")

    monkeypatch.setattr(res, "get_context", raise_context_error)
    assert await provider._get_resource("artifact://sessions/sess/a1/f.txt") is None

    monkeypatch.setattr(res, "get_context", lambda: SimpleNamespace(session_id="sess"))

    for exc in (KeyError("x"), ValueError("x"), ToolError("x"), Exception("x")):
        provider_exc = res.SessionArtifactProvider(
            store=_FakeStore(resolve_exc=exc),
            server_id="srv",
            uri_scheme="artifact://",
            enabled=True,
        )
        assert await provider_exc._get_resource("artifact://sessions/sess/a1/f.txt") is None

    provider_hidden = res.SessionArtifactProvider(
        store=_FakeStore(resolved_record=record_hidden),
        server_id="srv",
        uri_scheme="artifact://",
        enabled=True,
    )
    assert await provider_hidden._get_resource("artifact://sessions/sess/a1/f.txt") is None

    provider_ok = res.SessionArtifactProvider(
        store=_FakeStore(resolved_record=record_visible),
        server_id="srv",
        uri_scheme="artifact://",
        enabled=True,
    )
    monkeypatch.setattr(provider_ok, "_record_to_resource", lambda r: "resource-ok")
    assert await provider_ok._get_resource("artifact://sessions/sess/a1/f.txt") == "resource-ok"


@pytest.mark.asyncio
async def test_list_tools_returns_empty():
    provider = res.SessionArtifactProvider(store=_FakeStore(), server_id="srv", uri_scheme="artifact://", enabled=True)
    assert await provider._list_tools() == []
