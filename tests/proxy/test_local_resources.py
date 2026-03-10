from __future__ import annotations

from types import SimpleNamespace

import pytest

from remote_mcp_adapter.proxy import local_resources as lr


class _FakeProxy:
    def __init__(self):
        self.resources = []

    def add_resource(self, resource):
        self.resources.append(resource)


def test_upload_workflow_doc_path_points_to_resources_markdown():
    path = lr._upload_workflow_doc_path()
    assert path.name == "upload_workflow.md"
    assert "resources" in path.parts


def test_default_upload_workflow_text_includes_tool_name():
    text = lr._default_upload_workflow_text("srv_get_upload_url")
    assert "srv_get_upload_url" in text
    assert "upload://sessions" in text


def test_load_upload_workflow_text_uses_file_and_replaces_tool_name(tmp_path, monkeypatch):
    doc = tmp_path / "upload_workflow.md"
    doc.write_text("call {{UPLOAD_TOOL_NAME}} now", encoding="utf-8")
    monkeypatch.setattr(lr, "_upload_workflow_doc_path", lambda: doc)

    text = lr._load_upload_workflow_text("my_tool")
    assert text == "call my_tool now"


def test_load_upload_workflow_text_legacy_placeholder_replacement(tmp_path, monkeypatch):
    doc = tmp_path / "upload_workflow.md"
    doc.write_text("call `get_upload_url` now", encoding="utf-8")
    monkeypatch.setattr(lr, "_upload_workflow_doc_path", lambda: doc)

    text = lr._load_upload_workflow_text("my_tool")
    assert text == "call `my_tool` now"


def test_load_upload_workflow_text_does_not_double_prefix_existing_tool_names(tmp_path, monkeypatch):
    doc = tmp_path / "upload_workflow.md"
    doc.write_text("example `playwright_get_upload_url` usage", encoding="utf-8")
    monkeypatch.setattr(lr, "_upload_workflow_doc_path", lambda: doc)

    text = lr._load_upload_workflow_text("playwright_get_upload_url")
    assert text == "example `playwright_get_upload_url` usage"


def test_load_upload_workflow_text_falls_back_on_read_error(monkeypatch):
    class BadPath:
        def read_text(self, encoding):
            raise OSError("boom")

    monkeypatch.setattr(lr, "_upload_workflow_doc_path", lambda: BadPath())
    text = lr._load_upload_workflow_text("my_tool")
    assert "my_tool" in text


@pytest.mark.asyncio
async def test_get_upload_workflow_text_caches_per_tool_name(monkeypatch):
    lr._UPLOAD_WORKFLOW_DOC_CACHE.clear()
    calls: list[str] = []

    def fake_load(tool_name: str):
        calls.append(tool_name)
        return f"doc-{tool_name}"

    monkeypatch.setattr(lr, "_load_upload_workflow_text", fake_load)

    first = await lr._get_upload_workflow_text("tool_a")
    second = await lr._get_upload_workflow_text("tool_a")
    third = await lr._get_upload_workflow_text("tool_b")

    assert first == "doc-tool_a"
    assert second == "doc-tool_a"
    assert third == "doc-tool_b"
    assert calls == ["tool_a", "tool_b"]


@pytest.mark.asyncio
async def test_register_upload_workflow_resource_adds_resource_with_async_reader(monkeypatch):
    lr._UPLOAD_WORKFLOW_DOC_CACHE.clear()

    async def fake_get_upload_workflow_text(name: str):
        return f"workflow for {name}"

    monkeypatch.setattr(lr, "_get_upload_workflow_text", fake_get_upload_workflow_text)

    class CapturedResource:
        def __init__(self, fn, uri, name, description, mime_type):
            self.fn = fn
            self.uri = uri
            self.name = name
            self.description = description
            self.mime_type = mime_type

    def fake_from_function(*, fn, uri, name, description, mime_type):
        return CapturedResource(fn, uri, name, description, mime_type)

    monkeypatch.setattr(lr.Resource, "from_function", staticmethod(fake_from_function))

    mount = SimpleNamespace(proxy=_FakeProxy())
    lr.register_upload_workflow_resource(mount=mount, upload_endpoint_tool_name="srv_tool")

    assert len(mount.proxy.resources) == 1
    resource = mount.proxy.resources[0]
    assert resource.uri == "doc://upload_workflow.md"
    assert resource.mime_type == "text/markdown"
    assert await resource.fn() == "workflow for srv_tool"
