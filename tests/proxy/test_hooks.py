from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from mcp.types import TextContent

from remote_mcp_adapter.proxy import hooks as h


class _FakeProxy:
    def __init__(self):
        self.providers = []
        self.tools = []
        self.transforms = []

    def add_provider(self, provider):
        self.providers.append(provider)

    def add_tool(self, tool):
        self.tools.append(tool)

    def add_transform(self, transform):
        self.transforms.append(transform)


class _FakeProbeClient:
    def __init__(self, tools):
        self.tools = tools

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def list_tools(self):
        return self.tools


class _FakeClients:
    def __init__(self, tools):
        self._tools = tools

    def build_probe_client(self):
        return _FakeProbeClient(self._tools)

    async def get_session_client(self):
        return object()


class _FakeUploadAdapter:
    def __init__(self, tools, file_path_argument="path", uri_scheme="upload://", uri_prefix=False):
        self.tools = tools
        self.file_path_argument = file_path_argument
        self.uri_scheme = uri_scheme
        self.uri_prefix = uri_prefix
        self.overrides = SimpleNamespace(tool_call_timeout_seconds=None, allow_raw_output=None)


class _FakeArtifactAdapter:
    def __init__(self, tools, allow_raw_output=None):
        self.tools = tools
        self.allow_raw_output = allow_raw_output
        self.overrides = SimpleNamespace(tool_call_timeout_seconds=None, allow_raw_output=None)


@dataclass
class _FakeToolResult:
    content: list
    structured_content: object
    meta: dict | None


def _mcp_tool(name="tool_a", description="desc", schema=None):
    return SimpleNamespace(
        name=name,
        title=f"Title {name}",
        description=description,
        inputSchema=schema if schema is not None else {"type": "object", "properties": {}},
        annotations={"x": 1},
        outputSchema={"type": "object"},
        icons=[],
        meta={"_fastmcp": {"tags": ["tag"]}},
    )


def _config(*, uploads_enabled=True, allow_download=True, artifacts_enabled=True, expose_resources=True):
    return SimpleNamespace(
        uploads=SimpleNamespace(enabled=uploads_enabled),
        artifacts=SimpleNamespace(enabled=artifacts_enabled, expose_as_resources=expose_resources, uri_scheme="artifact://"),
        core=SimpleNamespace(
            defaults=SimpleNamespace(tool_call_timeout_seconds=10, allow_raw_output=False),
            allow_artifacts_download=allow_download,
        ),
        storage=SimpleNamespace(
            artifact_locator_policy="strict",
            artifact_locator_allowed_roots=[],
            atomic_writes=True,
        ),
        servers=[],
    )


def _server(server_id="srv", adapters=None, disabled_tools=None):
    return SimpleNamespace(
        id=server_id,
        mount_path=f"/mcp/{server_id}",
        adapters=adapters or [],
        disabled_tools=disabled_tools or [],
        tool_defaults=SimpleNamespace(tool_call_timeout_seconds=5, allow_raw_output=False),
    )


def _mount(server_id="srv", upstream_tools=None, disabled_tools=None):
    return SimpleNamespace(
        server=SimpleNamespace(
            id=server_id,
            mount_path=f"/mcp/{server_id}",
            disabled_tools=disabled_tools or [],
            tool_defaults=SimpleNamespace(tool_call_timeout_seconds=5, allow_raw_output=False),
        ),
        clients=_FakeClients(upstream_tools or []),
        proxy=_FakeProxy(),
    )


def test_append_download_link_block_and_description_helpers():
    url = "http://x"
    existing = [TextContent(type="text", text=f"already {url}")]
    assert h._append_download_link_block(existing, url) is existing

    merged = h._append_download_link_block([TextContent(type="text", text="no link")], url)
    assert any(url in block.text for block in merged if isinstance(block, TextContent))

    assert h._append_description("  base  ", "add") == "base\n\nadd"
    assert h._append_description(None, "add") == "add"


def test_upload_consumer_note_and_schema_annotation_and_clone():
    note = h._upload_consumer_note(
        file_path_argument="payload.path",
        uri_scheme="upload://",
        uri_prefix=True,
        upload_endpoint_tool_name="get_upload",
    )
    assert "file://" in note

    schema = {
        "type": "object",
        "properties": {
            "payload": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "object", "properties": {"inner": {"type": "string"}}}},
                },
            }
        },
    }
    assert h._annotate_schema_path_description(schema, "payload.path", "note") is True
    assert "note" in schema["properties"]["payload"]["properties"]["path"]["description"]
    assert h._annotate_schema_path_description(schema, "payload.items.inner", "n2") is True
    assert h._annotate_schema_path_description(schema, "missing.path", "x") is False

    tool = _mcp_tool(schema={"a": 1})
    cloned = h._clone_input_schema(tool)
    assert cloned == {"a": 1}
    tool_bad = _mcp_tool(schema="not-dict")
    assert h._clone_input_schema(tool_bad) is None


def test_annotate_schema_path_description_failure_modes():
    assert h._annotate_schema_path_description({"type": "object"}, "..", "note") is False
    assert h._annotate_schema_path_description({"properties": "bad"}, "a", "note") is False
    assert h._annotate_schema_path_description({"properties": {"a": "bad"}}, "a", "note") is False


def test_build_upload_consumer_override_tool_annotates_schema_and_fallback(monkeypatch):
    captured = {}

    def fake_from_mcp_tool(upstream_tool, handler, **kwargs):
        captured["description"] = kwargs.get("description")
        captured["parameters"] = kwargs.get("parameters")
        return "override-tool"

    monkeypatch.setattr(h.OverrideTool, "from_mcp_tool", staticmethod(fake_from_mcp_tool))

    adapter = _FakeUploadAdapter(["tool_a"], file_path_argument="payload.path")
    schema = {"type": "object", "properties": {"payload": {"type": "object", "properties": {"path": {"type": "string"}}}}}
    tool = _mcp_tool(schema=schema)
    result = h._build_upload_consumer_override_tool(
        upstream_tool=tool,
        handler=lambda *_: None,
        adapter=adapter,
        upload_endpoint_tool_name="helper_tool",
    )
    assert result == "override-tool"
    assert "helper_tool" in captured["description"]

    adapter_bad_path = _FakeUploadAdapter(["tool_a"], file_path_argument="missing.path")
    tool2 = _mcp_tool(schema={"type": "object", "properties": {}})
    h._build_upload_consumer_override_tool(
        upstream_tool=tool2,
        handler=lambda *_: None,
        adapter=adapter_bad_path,
        upload_endpoint_tool_name="helper_tool",
    )
    assert "description" in captured["parameters"]


@pytest.mark.asyncio
async def test_list_upstream_tools_maps_by_name():
    tools = [_mcp_tool("a"), _mcp_tool("b")]
    mount = _mount("srv", upstream_tools=tools)
    mapped = await h._list_upstream_tools(mount)
    assert set(mapped.keys()) == {"a", "b"}


@pytest.mark.asyncio
async def test_build_upload_consumer_handler_invokes_adapter_function(monkeypatch):
    called = {}

    async def fake_handle_upload_consumer_tool(**kwargs):
        called.update(kwargs)
        return "ok"

    monkeypatch.setattr(h, "handle_upload_consumer_tool", fake_handle_upload_consumer_tool)
    monkeypatch.setattr(h, "resolve_tool_timeout_seconds", lambda **kwargs: 12)

    mount = _mount("srv")
    config = _config()
    handler = h._build_upload_consumer_handler(
        store=object(),
        mount=mount,
        config=config,
        adapter=_FakeUploadAdapter(["t"], file_path_argument="path", uri_scheme="upload://", uri_prefix=False),
        tool_name="t",
        upload_endpoint_tool_name="helper",
        telemetry="telemetry",
    )
    result = await handler({"a": 1}, SimpleNamespace(session_id="sess"))
    assert result == "ok"
    assert called["tool_name"] == "t"
    assert called["tool_call_timeout_seconds"] == 12


@pytest.mark.asyncio
async def test_build_artifact_producer_handler_and_download_url_injection(monkeypatch):
    monkeypatch.setattr(h, "resolve_tool_timeout_seconds", lambda **kwargs: 5)
    monkeypatch.setattr(h, "resolve_allow_raw_output", lambda **kwargs: True)
    monkeypatch.setattr(h, "resolve_write_policy_lock_mode", lambda config: "exclusive")
    monkeypatch.setattr(h, "build_artifact_download_path", lambda *args: "/artifacts/s/sess/a/f.txt")
    monkeypatch.setattr(h, "derive_public_base_url", lambda config, context: "https://public")
    monkeypatch.setattr(h, "ToolResult", _FakeToolResult)

    async def fake_handle_artifact_producer_tool(**kwargs):
        return _FakeToolResult(
            content=[TextContent(type="text", text="done")],
            structured_content={"ok": True},
            meta={"artifact": {"artifact_id": "a", "filename": "f.txt"}},
        )

    monkeypatch.setattr(h, "handle_artifact_producer_tool", fake_handle_artifact_producer_tool)

    cred = SimpleNamespace(enabled=True, issue=lambda **kwargs: {"sig": "x"})
    handler = h._build_artifact_producer_handler(
        store=object(),
        mount=_mount("s"),
        config=_config(allow_download=True),
        adapter=_FakeArtifactAdapter(["tool"]),
        tool_name="tool",
        artifact_download_credentials=cred,
        telemetry="telemetry",
    )

    result = await handler({}, SimpleNamespace(session_id="sess", request_context=None))
    assert isinstance(result, _FakeToolResult)
    assert result.meta["artifact"]["download_url"].startswith("https://public/artifacts")
    assert any("Download artifact" in block.text for block in result.content if isinstance(block, TextContent))


@pytest.mark.asyncio
async def test_build_artifact_producer_handler_without_download_meta_or_disabled(monkeypatch):
    monkeypatch.setattr(h, "resolve_tool_timeout_seconds", lambda **kwargs: 5)
    monkeypatch.setattr(h, "resolve_allow_raw_output", lambda **kwargs: False)
    monkeypatch.setattr(h, "resolve_write_policy_lock_mode", lambda config: "exclusive")

    async def base_result(**kwargs):
        return _FakeToolResult(content=[], structured_content=None, meta={"x": 1})

    monkeypatch.setattr(h, "handle_artifact_producer_tool", base_result)

    config = _config(allow_download=False)
    handler = h._build_artifact_producer_handler(
        store=object(),
        mount=_mount("s"),
        config=config,
        adapter=_FakeArtifactAdapter(["tool"]),
        tool_name="tool",
    )
    original = await handler({}, SimpleNamespace(session_id="sess", request_context=None))
    assert original.meta == {"x": 1}


@pytest.mark.asyncio
async def test_build_artifact_producer_handler_download_url_guards(monkeypatch):
    monkeypatch.setattr(h, "resolve_tool_timeout_seconds", lambda **kwargs: 5)
    monkeypatch.setattr(h, "resolve_allow_raw_output", lambda **kwargs: False)
    monkeypatch.setattr(h, "resolve_write_policy_lock_mode", lambda config: "exclusive")

    mount = _mount("s")
    config = _config(allow_download=True)

    cases = [
        {"artifact": "not-dict"},
        {"artifact": {"artifact_id": "", "filename": "f.txt"}},
        {"artifact": {"artifact_id": "a", "filename": ""}},
    ]

    for meta in cases:
        async def base_result(**kwargs):
            return _FakeToolResult(content=[], structured_content=None, meta=meta)

        monkeypatch.setattr(h, "handle_artifact_producer_tool", base_result)
        handler = h._build_artifact_producer_handler(
            store=object(),
            mount=mount,
            config=config,
            adapter=_FakeArtifactAdapter(["tool"]),
            tool_name="tool",
        )
        result = await handler({}, SimpleNamespace(session_id="sess", request_context=None))
        assert result.meta == meta



def test_build_artifact_producer_handler_raises_tool_error_on_invalid_lock_mode(monkeypatch):
    monkeypatch.setattr(h, "resolve_tool_timeout_seconds", lambda **kwargs: 5)
    monkeypatch.setattr(h, "resolve_allow_raw_output", lambda **kwargs: False)

    def raise_lock(config):
        raise ValueError("bad lock")

    monkeypatch.setattr(h, "resolve_write_policy_lock_mode", raise_lock)

    with pytest.raises(h.ToolError, match="bad lock"):
        h._build_artifact_producer_handler(
            store=object(),
            mount=_mount("s"),
            config=_config(),
            adapter=_FakeArtifactAdapter(["tool"]),
            tool_name="tool",
        )


@pytest.mark.asyncio
async def test_wire_adapters_full_flow_and_statuses(monkeypatch):
    monkeypatch.setattr(h, "UploadConsumerAdapterConfig", _FakeUploadAdapter)
    monkeypatch.setattr(h, "ArtifactProducerAdapterConfig", _FakeArtifactAdapter)

    resources_added = []
    tools_added = []
    monkeypatch.setattr(h, "register_upload_workflow_resource", lambda **kwargs: resources_added.append(kwargs["mount"].server.id))
    monkeypatch.setattr(h, "register_get_upload_url_tool", lambda **kwargs: tools_added.append(kwargs["mount"].server.id))

    upload_handler_calls = []
    artifact_handler_calls = []
    monkeypatch.setattr(h, "_build_upload_consumer_handler", lambda **kwargs: upload_handler_calls.append(kwargs) or (lambda a, c: None))
    monkeypatch.setattr(h, "_build_upload_consumer_override_tool", lambda **kwargs: f"upload-{kwargs['upstream_tool'].name}")
    monkeypatch.setattr(h, "_build_artifact_producer_handler", lambda **kwargs: artifact_handler_calls.append(kwargs) or (lambda a, c: None))

    monkeypatch.setattr(h.OverrideTool, "from_mcp_tool", staticmethod(lambda mcp_tool, handler: f"artifact-{mcp_tool.name}"))

    s1 = _server("s1", adapters=[_FakeUploadAdapter(["u1", "missing"]), _FakeArtifactAdapter(["a1", "missing_art"])])
    s2 = _server("s2", adapters=[])
    s3 = _server("s3", adapters=[_FakeUploadAdapter(["u3"])])

    config = _config(uploads_enabled=True)
    config.servers = [s1, s2, s3]

    mount1 = _mount("s1")
    mount2 = _mount("s2")
    mount3 = _mount("s3")
    proxy_map = {"s1": mount1, "s2": mount2, "s3": mount3}

    async def fake_list_upstream_tools(mount):
        if mount.server.id == "s1":
            return {"u1": _mcp_tool("u1"), "a1": _mcp_tool("a1")}
        raise RuntimeError("upstream down")

    monkeypatch.setattr(h, "_list_upstream_tools", fake_list_upstream_tools)

    warnings = []
    monkeypatch.setattr(h.logger, "warning", lambda message, *args, **kwargs: warnings.append(message))

    state = h.AdapterWireState()
    status = await h.wire_adapters(
        config=config,
        proxy_map=proxy_map,
        store=object(),
        state=state,
        upload_credentials="cred",
        artifact_download_credentials="artcred",
        telemetry="telemetry",
    )

    assert status == {"s1": True, "s2": True, "s3": False}
    assert resources_added == ["s1", "s3"]
    assert tools_added == ["s1", "s3"]
    assert "Configured upload_consumer tool not found upstream" in warnings
    assert "Configured artifact_producer tool not found upstream" in warnings
    assert len(mount1.proxy.providers) == 1
    assert set(mount1.proxy.tools) == {"upload-u1", "artifact-a1"}

    # second run is idempotent via state
    status2 = await h.wire_adapters(
        config=config,
        proxy_map=proxy_map,
        store=object(),
        state=state,
        upload_credentials="cred",
        artifact_download_credentials="artcred",
        telemetry="telemetry",
    )
    assert status2["s1"] is True


@pytest.mark.asyncio
async def test_override_tool_run_and_from_mcp_tool(monkeypatch):
    monkeypatch.setattr(h.Tool, "__init__", lambda self, **kwargs: None)

    async def fake_handler(arguments, context):
        return {"arguments": arguments, "session_id": context.session_id}

    tool = h.OverrideTool(
        handler=fake_handler,
        name="n",
        title="t",
        description="d",
        parameters={},
        annotations={},
        output_schema={},
        icons=[],
        meta={"_fastmcp": {"tags": []}},
        tags=[],
    )

    res1 = await tool.run({"a": 1}, context=SimpleNamespace(session_id="s1"))
    assert res1["session_id"] == "s1"

    monkeypatch.setattr(h, "get_context", lambda: SimpleNamespace(session_id="s2"))
    res2 = await tool.run({"a": 2}, context=None)
    assert res2["session_id"] == "s2"

    mcp_tool = _mcp_tool("x")
    built = h.OverrideTool.from_mcp_tool(mcp_tool, handler=fake_handler)
    assert isinstance(built, h.OverrideTool)


def test_is_tool_disabled_exact_and_regex():
    assert h._is_tool_disabled("my_tool", []) is False
    assert h._is_tool_disabled("my_tool", ["my_tool"]) is True
    assert h._is_tool_disabled("my_tool", ["other_tool"]) is False
    assert h._is_tool_disabled("internal_debug", ["^internal_.*"]) is True
    assert h._is_tool_disabled("public_tool", ["^internal_.*"]) is False
    # Invalid regex silently falls back to exact-match only — no exception, no warning
    assert h._is_tool_disabled("any_tool", ["[invalid"]) is False
    assert h._is_tool_disabled("[invalid", ["[invalid"]) is True  # still matches as exact



@pytest.mark.asyncio
async def test_wire_adapters_respects_disabled_tools(monkeypatch):
    monkeypatch.setattr(h, "UploadConsumerAdapterConfig", _FakeUploadAdapter)
    monkeypatch.setattr(h, "ArtifactProducerAdapterConfig", _FakeArtifactAdapter)
    monkeypatch.setattr(h, "register_upload_workflow_resource", lambda **kw: None)
    monkeypatch.setattr(h, "register_get_upload_url_tool", lambda **kw: None)
    monkeypatch.setattr(h, "_build_upload_consumer_handler", lambda **kw: lambda a, c: None)
    monkeypatch.setattr(h, "_build_upload_consumer_override_tool", lambda **kw: f"upload-{kw['upstream_tool'].name}")
    monkeypatch.setattr(h, "_build_artifact_producer_handler", lambda **kw: lambda a, c: None)
    monkeypatch.setattr(h.OverrideTool, "from_mcp_tool", staticmethod(lambda mcp_tool, handler: f"artifact-{mcp_tool.name}"))

    # u1 is disabled; a1 should still be registered
    s1 = _server("s1", adapters=[_FakeUploadAdapter(["u1"]), _FakeArtifactAdapter(["a1"])], disabled_tools=["u1"])
    config = _config(uploads_enabled=True)
    config.servers = [s1]
    mount1 = _mount("s1")
    # wire_adapters reads server.disabled_tools from the config server object
    # The mount's server namespace also needs disabled_tools for the helper check
    mount1.server.disabled_tools = ["u1"]

    async def fake_list(mount):
        return {"u1": _mcp_tool("u1"), "a1": _mcp_tool("a1")}

    monkeypatch.setattr(h, "_list_upstream_tools", fake_list)

    status = await h.wire_adapters(config=config, proxy_map={"s1": mount1}, store=object())
    assert status["s1"] is True
    assert "upload-u1" not in mount1.proxy.tools
    assert "artifact-a1" in mount1.proxy.tools


@pytest.mark.asyncio
async def test_wire_adapters_disabled_upload_helper_suppresses_resource_and_tool(monkeypatch):
    monkeypatch.setattr(h, "UploadConsumerAdapterConfig", _FakeUploadAdapter)
    monkeypatch.setattr(h, "ArtifactProducerAdapterConfig", _FakeArtifactAdapter)

    resources_added = []
    tools_added = []
    monkeypatch.setattr(h, "register_upload_workflow_resource", lambda **kw: resources_added.append(kw["mount"].server.id))
    monkeypatch.setattr(h, "register_get_upload_url_tool", lambda **kw: tools_added.append(kw["mount"].server.id))
    monkeypatch.setattr(h, "_build_upload_consumer_handler", lambda **kw: lambda a, c: None)
    monkeypatch.setattr(h, "_build_upload_consumer_override_tool", lambda **kw: f"upload-{kw['upstream_tool'].name}")
    monkeypatch.setattr(h, "_build_artifact_producer_handler", lambda **kw: lambda a, c: None)
    monkeypatch.setattr(h.OverrideTool, "from_mcp_tool", staticmethod(lambda mcp_tool, handler: f"artifact-{mcp_tool.name}"))

    from remote_mcp_adapter.proxy.local_tools import get_upload_url_tool_name
    helper_name = get_upload_url_tool_name("s1")

    s1 = _server("s1", adapters=[_FakeUploadAdapter(["u1"])], disabled_tools=[helper_name])
    config = _config(uploads_enabled=True)
    config.servers = [s1]
    mount1 = _mount("s1")
    mount1.server.disabled_tools = [helper_name]

    async def fake_list(mount):
        return {"u1": _mcp_tool("u1")}

    monkeypatch.setattr(h, "_list_upstream_tools", fake_list)

    status = await h.wire_adapters(config=config, proxy_map={"s1": mount1}, store=object())
    assert status["s1"] is True
    # Helper tool and resource suppressed
    assert resources_added == []
    assert tools_added == []


@pytest.mark.asyncio
async def test_wire_adapters_disabled_tools_no_adapters(monkeypatch):
    """disabled_tools must be applied even when no upload/artifact adapters are configured."""
    monkeypatch.setattr(h, "UploadConsumerAdapterConfig", _FakeUploadAdapter)
    monkeypatch.setattr(h, "ArtifactProducerAdapterConfig", _FakeArtifactAdapter)
    monkeypatch.setattr(h, "register_upload_workflow_resource", lambda **kw: None)
    monkeypatch.setattr(h, "register_get_upload_url_tool", lambda **kw: None)

    # Server has no adapters at all — only disabled_tools
    s1 = _server("s1", adapters=[], disabled_tools=["secret_tool"])
    config = _config()
    config.servers = [s1]
    mount1 = _mount("s1")
    mount1.server.disabled_tools = ["secret_tool"]

    async def fake_list(mount):
        return {"secret_tool": _mcp_tool("secret_tool"), "public_tool": _mcp_tool("public_tool")}

    monkeypatch.setattr(h, "_list_upstream_tools", fake_list)

    status = await h.wire_adapters(config=config, proxy_map={"s1": mount1}, store=object())
    assert status["s1"] is True
    # Visibility transform must have been applied with the disabled name
    from fastmcp.server.transforms.visibility import Visibility
    assert any(
        isinstance(t, Visibility) and "secret_tool" in (t.names or set())
        for t in mount1.proxy.transforms
    )

