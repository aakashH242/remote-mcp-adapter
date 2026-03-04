from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.tools.tool import ToolResult
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent, TextContent, TextResourceContents

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

    assert ap._extract_structured_fallback_path(ToolResult(content=[], structured_content={"a": {"b": "/tmp/x"}}, meta={})) == "/tmp/x"

    assert ap._safe_name_from_argument("/tmp/a.txt") == "a.txt"
    assert ap._safe_name_from_argument(1) is None


@pytest.mark.asyncio
async def test_embedded_extract_and_materialize(tmp_path, monkeypatch):
    image_res = ToolResult(content=[ImageContent(type="image", data="YWJj", mimeType="image/png")], structured_content={}, meta={})
    data, mime = await ap._extract_embedded_bytes(image_res)
    assert data == b"abc" and mime == "image/png"
    jpeg_res = ToolResult(content=[ImageContent(type="image", data="YWJj", mimeType="image/jpeg")], structured_content={}, meta={})
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

    dict_res = ToolResult(content=[{"type": "image", "data": "YWJj", "mimeType": "image/png"}], structured_content={}, meta={})
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
    adapter_struct = SimpleNamespace(output_locator=SimpleNamespace(mode="structured", output_path_key="a.b", output_path_regexes=[]))
    result = ToolResult(content=[], structured_content={"a": {"b": "/tmp/out.bin"}}, meta={})
    assert ap._extract_locator_path(result, adapter_struct) == "/tmp/out.bin"

    adapter_regex = SimpleNamespace(output_locator=SimpleNamespace(mode="regex", output_path_key=None, output_path_regexes=[r"path=(/tmp/[^\s]+)"]))
    result2 = ToolResult(content=[TextContent(type="text", text="path=/tmp/a.txt")], structured_content={}, meta={})
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
