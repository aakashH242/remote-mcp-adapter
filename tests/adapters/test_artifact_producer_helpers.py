from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp.tools.tool import ToolResult
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent, TextContent, TextResourceContents

from remote_mcp_adapter.core.repo.records import ArtifactRecord

from remote_mcp_adapter.adapters import artifact_producer as ap


def test_path_and_text_helpers(tmp_path):
    assert ap._decode_base64("YWJj") == b"abc"
    assert ap._get_nested({"a": {"b": 1}}, "a.b") == 1
    assert ap._get_nested({"a": {}}, "a.x") is None
    assert ap._get_nested({}, None) is None

    result = ToolResult(
        content=[TextContent(type="text", text="hello"), {"type": "text", "text": "world"}],
        structured_content={"x": 1},
        meta={},
    )
    payload_text = ap._extract_text_payload(result)
    assert payload_text.startswith("hello\n")
    assert "world" in payload_text

    assert ap._looks_path_like(" /tmp/a ") is True
    assert ap._looks_path_like(" ") is False

    structured_path = ap._extract_structured_fallback_path(
        ToolResult(
            content=[],
            structured_content={"a": {"b": "/tmp/x"}},
            meta={},
        )
    )
    assert structured_path == "/tmp/x"

    assert ap._safe_name_from_argument("/tmp/a.txt") == "a.txt"
    assert ap._safe_name_from_argument(1) is None


@pytest.mark.asyncio
async def test_embedded_extract_and_materialize(tmp_path, monkeypatch):
    image_res = ToolResult(
        content=[ImageContent(type="image", data="YWJj", mimeType="image/png")],
        structured_content={},
        meta={},
    )
    data, mime = await ap._extract_embedded_bytes(image_res)
    assert data == b"abc" and mime == "image/png"
    jpeg_res = ToolResult(
        content=[ImageContent(type="image", data="YWJj", mimeType="image/jpeg")],
        structured_content={},
        meta={},
    )
    data_jpeg, mime_jpeg = await ap._extract_embedded_bytes(jpeg_res)
    assert data_jpeg == b"abc" and mime_jpeg == "image/jpeg"

    blob_res = ToolResult(
        content=[
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri="https://example.com/blob",
                    blob="YWJj",
                    mimeType="application/octet-stream",
                ),
            )
        ],
        structured_content={},
        meta={},
    )
    data2, mime2 = await ap._extract_embedded_bytes(blob_res)
    assert data2 == b"abc" and mime2 == "application/octet-stream"

    text_res = ToolResult(
        content=[
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(uri="https://example.com/text", text="hi", mimeType="text/plain"),
            )
        ],
        structured_content={},
        meta={},
    )
    data3, mime3 = await ap._extract_embedded_bytes(text_res)
    assert data3 == b"hi" and mime3 == "text/plain"

    dict_res = ToolResult(
        content=[{"type": "image", "data": "YWJj", "mimeType": "image/png"}],
        structured_content={},
        meta={},
    )
    assert await ap._extract_embedded_bytes(dict_res) is None

    empty_res = ToolResult(content=[{"type": "other"}], structured_content={}, meta={})
    assert await ap._extract_embedded_bytes(empty_res) is None

    target = tmp_path / "x.bin"

    writes = []

    async def write_bytes_with_policy(**kwargs):
        kwargs["target_path"].write_bytes(kwargs["data"])
        writes.append(kwargs)

    monkeypatch.setattr(ap, "write_bytes_with_policy", write_bytes_with_policy)
    ok = await ap._materialize_from_embedded(
        result=blob_res,
        target_path=target,
        atomic_writes=True,
        lock_mode="process",
    )
    assert ok is True and target.exists() and writes


def test_locator_helpers(tmp_path):
    adapter_struct = SimpleNamespace(
        output_locator=SimpleNamespace(
            mode="structured",
            output_path_key="a.b",
            output_path_regexes=[],
        )
    )
    result = ToolResult(content=[], structured_content={"a": {"b": "/tmp/out.bin"}}, meta={})
    assert ap._extract_locator_path(result, adapter_struct) == "/tmp/out.bin"

    adapter_regex = SimpleNamespace(
        output_locator=SimpleNamespace(
            mode="regex",
            output_path_key=None,
            output_path_regexes=[r"path=(/tmp/[^\s]+)"],
        )
    )
    result2 = ToolResult(
        content=[TextContent(type="text", text="path=/tmp/a.txt")],
        structured_content={},
        meta={},
    )
    assert ap._extract_locator_path(result2, adapter_regex) == "/tmp/a.txt"

    assert "a.txt" in str(ap._normalize_locator_path('"/tmp/a.txt",'))

    storage = tmp_path / "root"
    c1 = ap._storage_suffix_candidate("/x/artifacts/sessions/s/a/f", storage, ap.ARTIFACTS_SUFFIX_MARKER)
    assert c1 is not None
    c2 = ap._storage_suffix_candidate("/x/uploads/sessions/s/u", storage, ap.UPLOADS_SUFFIX_MARKER)
    assert c2 is not None

    candidates = ap._iter_locator_candidates("/x/artifacts/sessions/s/a/f", storage)
    assert candidates


def test_sibling_and_descendant_variant_selectors(tmp_path):
    parent = tmp_path / "p"
    parent.mkdir()
    target = parent / "file.txt"
    target.write_text("x", encoding="utf-8")
    sib = parent / "file.log"
    sib.write_text("y", encoding="utf-8")

    selected = ap._select_sibling_variant(target)
    assert selected is not None

    child_dir = parent / "nested"
    child_dir.mkdir()
    nested = child_dir / "n.bin"
    nested.write_text("z", encoding="utf-8")
    selected_desc = ap._select_descendant_variant(target)
    assert selected_desc is not None


def test_ensure_within_storage_and_uri(tmp_path):
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    inside = storage_root / "a" / "x.txt"
    inside.parent.mkdir(parents=True)
    inside.write_text("x", encoding="utf-8")

    resolved = ap._ensure_within_storage(inside, storage_root, "storage_only", [])
    assert resolved == inside.resolve()

    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(Exception):
        ap._ensure_within_storage(outside, storage_root, "storage_only", [])

    allowed = ap._ensure_within_storage(outside, storage_root, "allow_configured_roots", [str(tmp_path)])
    assert allowed == outside.resolve()

    assert ap._artifact_uri("artifact://", "sess", "a1", "f.txt") == "artifact://sessions/sess/a1/f.txt"


@pytest.mark.asyncio
async def test_materialize_from_sibling_and_resolve_locator_source(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    parent = storage_root / "artifacts" / "sessions" / "s1" / "a1"
    parent.mkdir(parents=True)
    target = parent / "out.txt"
    sibling = parent / "out.log"
    sibling.write_text("abc", encoding="utf-8")

    async def copy_file_with_policy(**kwargs):
        kwargs["target_path"].write_text(kwargs["source_path"].read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(ap, "copy_file_with_policy", copy_file_with_policy)
    ok = await ap._materialize_from_sibling_variant(target_path=target, atomic_writes=True, lock_mode="process")
    assert ok is True and target.exists()

    found = await ap._resolve_locator_source(
        raw_path=str(target),
        storage_root=storage_root,
        locator_policy="storage_only",
        locator_allowed_roots=[],
    )
    assert found is not None


@pytest.mark.asyncio
async def test_extract_embedded_bytes_handles_blank_image_mime_and_dict_resources():
    image_res = ToolResult(
        content=[ImageContent(type="image", data="YWJj", mimeType="")],
        structured_content={},
        meta={},
    )
    image_bytes, image_mime = await ap._extract_embedded_bytes(image_res)
    assert image_bytes == b"abc"
    assert image_mime == "image/png"

    blob_dict_res = SimpleNamespace(
        content=[{"type": "resource", "resource": {"type": "blob", "blob": "YWJj", "mimeType": "app/x"}}],
        structured_content={},
        meta={},
    )
    blob_bytes, blob_mime = await ap._extract_embedded_bytes(blob_dict_res)
    assert blob_bytes == b"abc"
    assert blob_mime == "app/x"

    text_dict_res = SimpleNamespace(
        content=[{"type": "resource", "resource": {"type": "text", "text": "hello", "mimeType": "text/plain"}}],
        structured_content={},
        meta={},
    )
    text_bytes, text_mime = await ap._extract_embedded_bytes(text_dict_res)
    assert text_bytes == b"hello"
    assert text_mime == "text/plain"


def test_extract_locator_path_regex_uses_default_and_full_match():
    adapter_regex = SimpleNamespace(
        output_locator=SimpleNamespace(
            mode="regex",
            output_path_key=None,
            output_path_regexes=[],
        )
    )
    result = ToolResult(
        content=[TextContent(type="text", text="saved to /tmp/example/output.txt")],
        structured_content={},
        meta={},
    )
    assert ap._extract_locator_path(result, adapter_regex) == "/tmp/example/output.txt"

    adapter_none = SimpleNamespace(output_locator=SimpleNamespace(mode="none", output_path_key=None, output_path_regexes=[]))
    assert ap._extract_locator_path(result, adapter_none) is None


@pytest.mark.asyncio
async def test_resolve_locator_source_returns_none_when_no_candidate_exists(tmp_path):
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    found = await ap._resolve_locator_source(
        raw_path="/missing/file.txt",
        storage_root=storage_root,
        locator_policy="storage_only",
        locator_allowed_roots=[],
    )
    assert found is None


class _Store:
    def __init__(self, storage_root):
        self.storage_root = storage_root
        self.allocated = []
        self.finalized = []

    async def allocate_artifact_path(
        self,
        *,
        server_id,
        session_id,
        filename,
        tool_name,
        expose_as_resource,
    ):
        artifact_id = f"artifact-{len(self.allocated) + 1}"
        target_path = self.storage_root / "artifacts" / "sessions" / session_id / artifact_id / (filename or "artifact.bin")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        self.allocated.append((server_id, session_id, filename, tool_name, expose_as_resource, target_path))
        return artifact_id, target_path, target_path.relative_to(self.storage_root).as_posix()

    async def finalize_artifact(self, *, server_id, session_id, artifact_id, mime_type=None):
        target_path = next(path for _, _, _, _, _, path in self.allocated if path.parent.name == artifact_id)
        record = ArtifactRecord(
            server_id=server_id,
            session_id=session_id,
            artifact_id=artifact_id,
            filename=target_path.name,
            abs_path=target_path,
            rel_path=target_path.relative_to(self.storage_root).as_posix(),
            mime_type=mime_type or "application/octet-stream",
            size_bytes=target_path.stat().st_size,
            created_at=1.0,
            last_accessed=1.0,
            last_updated=1.0,
            tool_name="capture",
            expose_as_resource=True,
            visibility_state="committed",
        )
        self.finalized.append(record)
        return record


def _adapter(*, mode="structured", persist=True, output_path_argument="filename"):
    return SimpleNamespace(
        output_path_argument=output_path_argument,
        output_locator=SimpleNamespace(mode=mode, output_path_key="artifact.path", output_path_regexes=[]),
        persist=persist,
        expose_as_resource=True,
        allow_raw_output=None,
    )


@pytest.mark.asyncio
async def test_handle_artifact_producer_tool_returns_upstream_result_when_persist_disabled(tmp_path, monkeypatch):
    store = _Store(tmp_path)
    result = ToolResult(content=[TextContent(type="text", text="ok")], structured_content={}, meta={})

    async def fake_call_upstream_tool(**kwargs):
        return result

    monkeypatch.setattr(ap, "call_upstream_tool", fake_call_upstream_tool)

    returned = await ap.handle_artifact_producer_tool(
        tool_name="capture",
        arguments={},
        context=SimpleNamespace(session_id="sess-1"),
        server_id="playwright",
        adapter=_adapter(persist=False),
        config_artifact_uri_scheme="artifact://",
        store=store,
        client_factory=lambda: None,
        tool_call_timeout_seconds=None,
        telemetry=None,
        allow_raw_output=False,
        locator_policy="storage_only",
        locator_allowed_roots=[],
        atomic_writes=True,
        lock_mode="process",
    )

    assert returned is result
    assert len(store.allocated) == 1
    assert store.finalized == []


@pytest.mark.asyncio
async def test_handle_artifact_producer_tool_copies_from_locator_and_adds_artifact_meta(tmp_path, monkeypatch):
    store = _Store(tmp_path)
    source_path = tmp_path / "source.txt"
    source_path.write_text("artifact-bytes", encoding="utf-8")
    result = ToolResult(
        content=[TextContent(type="text", text="done")],
        structured_content={"artifact": {"path": str(source_path)}},
        meta={"upstream": True},
    )

    async def fake_call_upstream_tool(**kwargs):
        return result

    async def fake_copy_file_with_policy(**kwargs):
        kwargs["target_path"].write_bytes(kwargs["source_path"].read_bytes())

    async def fake_build_raw_artifact_content_block(**kwargs):
        return TextContent(type="text", text=f"raw:{kwargs['artifact_uri']}")

    monkeypatch.setattr(ap, "call_upstream_tool", fake_call_upstream_tool)
    monkeypatch.setattr(ap, "copy_file_with_policy", fake_copy_file_with_policy)
    monkeypatch.setattr(ap, "build_raw_artifact_content_block", fake_build_raw_artifact_content_block)
    monkeypatch.setattr(ap, "detect_mime_type", lambda path, fallback=None: "text/plain")

    returned = await ap.handle_artifact_producer_tool(
        tool_name="capture",
        arguments={"filename": "report.txt"},
        context=SimpleNamespace(session_id="sess-1"),
        server_id="playwright",
        adapter=_adapter(mode="structured", persist=True, output_path_argument="filename"),
        config_artifact_uri_scheme="artifact://",
        store=store,
        client_factory=lambda: None,
        tool_call_timeout_seconds=None,
        telemetry=None,
        allow_raw_output=True,
        locator_policy="allow_configured_roots",
        locator_allowed_roots=[str(tmp_path)],
        atomic_writes=True,
        lock_mode="process",
    )

    assert store.finalized
    assert returned.meta["artifact"]["artifact_uri"].startswith("artifact://sessions/sess-1/")
    assert returned.meta["upstream"] is True
    assert any(getattr(block, "text", "").startswith("raw:artifact://") for block in returned.content)


@pytest.mark.asyncio
async def test_handle_artifact_producer_tool_embedded_mode_allocates_and_writes(tmp_path, monkeypatch):
    store = _Store(tmp_path)
    result = ToolResult(
        content=[ImageContent(type="image", data="YWJj", mimeType="image/png")],
        structured_content={},
        meta={},
    )

    async def fake_call_upstream_tool(**kwargs):
        return result

    async def fake_write_bytes_with_policy(**kwargs):
        kwargs["target_path"].write_bytes(kwargs["data"])

    monkeypatch.setattr(ap, "call_upstream_tool", fake_call_upstream_tool)
    monkeypatch.setattr(ap, "write_bytes_with_policy", fake_write_bytes_with_policy)
    monkeypatch.setattr(ap, "detect_mime_type", lambda path, fallback=None: "image/png")

    returned = await ap.handle_artifact_producer_tool(
        tool_name="capture",
        arguments={},
        context=SimpleNamespace(session_id="sess-1"),
        server_id="playwright",
        adapter=_adapter(mode="embedded", persist=True, output_path_argument=None),
        config_artifact_uri_scheme="artifact://",
        store=store,
        client_factory=lambda: None,
        tool_call_timeout_seconds=None,
        telemetry=None,
        allow_raw_output=False,
        locator_policy="storage_only",
        locator_allowed_roots=[],
        atomic_writes=True,
        lock_mode="process",
    )

    assert store.finalized
    assert returned.meta["artifact"]["mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_handle_artifact_producer_tool_mode_none_returns_result_when_no_artifact_found(tmp_path, monkeypatch):
    store = _Store(tmp_path)
    result = ToolResult(content=[TextContent(type="text", text="ok")], structured_content={}, meta={})

    async def fake_call_upstream_tool(**kwargs):
        return result

    monkeypatch.setattr(ap, "call_upstream_tool", fake_call_upstream_tool)

    returned = await ap.handle_artifact_producer_tool(
        tool_name="capture",
        arguments={"filename": "report.txt"},
        context=SimpleNamespace(session_id="sess-1"),
        server_id="playwright",
        adapter=_adapter(mode="none", persist=True, output_path_argument="filename"),
        config_artifact_uri_scheme="artifact://",
        store=store,
        client_factory=lambda: None,
        tool_call_timeout_seconds=None,
        telemetry=None,
        allow_raw_output=False,
        locator_policy="storage_only",
        locator_allowed_roots=[],
        atomic_writes=True,
        lock_mode="process",
    )

    assert returned is result
    assert store.finalized == []


@pytest.mark.asyncio
async def test_handle_artifact_producer_tool_raises_when_artifact_cannot_be_found(tmp_path, monkeypatch):
    store = _Store(tmp_path)
    result = ToolResult(content=[TextContent(type="text", text="no file")], structured_content={}, meta={})

    async def fake_call_upstream_tool(**kwargs):
        return result

    monkeypatch.setattr(ap, "call_upstream_tool", fake_call_upstream_tool)

    with pytest.raises(Exception, match="Could not locate or materialize artifact output"):
        await ap.handle_artifact_producer_tool(
            tool_name="capture",
            arguments={"filename": "report.txt"},
            context=SimpleNamespace(session_id="sess-1"),
            server_id="playwright",
            adapter=_adapter(mode="structured", persist=True, output_path_argument="filename"),
            config_artifact_uri_scheme="artifact://",
            store=store,
            client_factory=lambda: None,
            tool_call_timeout_seconds=None,
            telemetry=None,
            allow_raw_output=False,
            locator_policy="storage_only",
            locator_allowed_roots=[],
            atomic_writes=True,
            lock_mode="process",
        )
