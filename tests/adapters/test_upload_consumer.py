from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolResult

from remote_mcp_adapter.adapters import upload_consumer as uc


def test_upload_flow_hint_and_nested_helpers():
    hint = uc._upload_flow_hint(
        tool_name="tool",
        file_path_argument="args.file",
        uri_scheme="upload://",
        upload_endpoint_tool_name="upload",
    )
    assert "upload://" in hint

    err = uc._upload_input_error(
        "bad",
        tool_name="tool",
        file_path_argument="args.file",
        uri_scheme="upload://",
        upload_endpoint_tool_name="upload",
    )
    assert "bad" in str(err)

    payload = {"a": {"b": "x"}}
    assert uc._get_nested_arg(payload, "a.b") == "x"
    uc._set_nested_arg(payload, "a.b", "y")
    assert payload["a"]["b"] == "y"

    with pytest.raises(KeyError):
        uc._get_nested_arg(payload, "a.missing")
    with pytest.raises(KeyError):
        uc._set_nested_arg(payload, "a.missing.x", "z")


@pytest.mark.asyncio
async def test_resolve_handle_and_consumer_paths(monkeypatch):
    class _Store:
        async def resolve_upload_handle(self, **kwargs):
            if kwargs["handle"] == "upload://bad":
                raise KeyError("bad")
            return SimpleNamespace(abs_path="C:/tmp/file.txt")

    with pytest.raises(ToolError):
        await uc._resolve_handle(
            store=_Store(),
            server_id="s1",
            session_id="sess",
            raw_value="not-upload",
            uri_scheme="upload://",
            tool_name="tool",
            file_path_argument="file",
            upload_endpoint_tool_name="upload",
        )

    with pytest.raises(ToolError):
        await uc._resolve_handle(
            store=_Store(),
            server_id="s1",
            session_id="sess",
            raw_value="upload://bad",
            uri_scheme="upload://",
            tool_name="tool",
            file_path_argument="file",
            upload_endpoint_tool_name="upload",
        )

    ok = await uc._resolve_handle(
        store=_Store(),
        server_id="s1",
        session_id="sess",
        raw_value="upload://ok",
        uri_scheme="upload://",
        tool_name="tool",
        file_path_argument="file",
        upload_endpoint_tool_name="upload",
    )
    assert str(ok).endswith("file.txt")

    calls = []

    async def fake_call(**kwargs):
        calls.append(kwargs)
        return ToolResult(content=[], structured_content={}, meta={})

    monkeypatch.setattr(uc, "call_upstream_tool", fake_call)
    monkeypatch.setattr(uc, "get_context", lambda: SimpleNamespace(session_id="sess"))

    result = await uc.handle_upload_consumer_tool(
        tool_name="tool",
        arguments={"file": "upload://ok"},
        context=None,
        server_id="s1",
        file_path_argument="file",
        uri_scheme="upload://",
        uri_prefix=False,
        telemetry=None,
        store=_Store(),
        client_factory=lambda: object(),
        tool_call_timeout_seconds=1,
        upload_endpoint_tool_name="upload",
    )
    assert isinstance(result, ToolResult)
    assert calls[-1]["arguments"]["file"].endswith("file.txt")

    await uc.handle_upload_consumer_tool(
        tool_name="tool",
        arguments={"file": ["upload://ok"]},
        context=SimpleNamespace(session_id="sess"),
        server_id="s1",
        file_path_argument="file",
        uri_scheme="upload://",
        uri_prefix=True,
        telemetry=None,
        store=_Store(),
        client_factory=lambda: object(),
        tool_call_timeout_seconds=1,
        upload_endpoint_tool_name="upload",
    )
    assert calls[-1]["arguments"]["file"][0].startswith("file://")

    with pytest.raises(ToolError):
        await uc.handle_upload_consumer_tool(
            tool_name="tool",
            arguments={"file": [1]},
            context=SimpleNamespace(session_id="sess"),
            server_id="s1",
            file_path_argument="file",
            uri_scheme="upload://",
            uri_prefix=False,
            telemetry=None,
            store=_Store(),
            client_factory=lambda: object(),
            tool_call_timeout_seconds=1,
            upload_endpoint_tool_name="upload",
        )

    with pytest.raises(ToolError):
        await uc.handle_upload_consumer_tool(
            tool_name="tool",
            arguments={"file": 1},
            context=SimpleNamespace(session_id="sess"),
            server_id="s1",
            file_path_argument="file",
            uri_scheme="upload://",
            uri_prefix=False,
            telemetry=None,
            store=_Store(),
            client_factory=lambda: object(),
            tool_call_timeout_seconds=1,
            upload_endpoint_tool_name="upload",
        )
