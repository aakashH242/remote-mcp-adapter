from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from remote_mcp_adapter.adapters import raw_output as ro
from remote_mcp_adapter.adapters import upstream_call as uc


def test_base64_ascii():
    assert ro._base64_ascii(b"abc") == "YWJj"


@pytest.mark.asyncio
async def test_build_raw_artifact_content_block(tmp_path):
    p = tmp_path / "i.bin"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    image_block = await ro.build_raw_artifact_content_block(
        artifact_uri="artifact://sessions/s/a/f",
        artifact_path=p,
        mime_type="image/png",
    )
    assert image_block.type == "image"

    t = tmp_path / "t.txt"
    t.write_text("hello", encoding="utf-8")
    text_block = await ro.build_raw_artifact_content_block(
        artifact_uri="artifact://sessions/s/a/f",
        artifact_path=t,
        mime_type="text/plain",
    )
    assert text_block.type == "resource"

    b = tmp_path / "b.bin"
    b.write_bytes(b"abc")
    blob_block = await ro.build_raw_artifact_content_block(
        artifact_uri="artifact://sessions/s/a/f",
        artifact_path=b,
        mime_type="application/octet-stream",
    )
    assert blob_block.type == "resource"


@pytest.mark.asyncio
async def test_call_upstream_tool_success_timeout_error():
    class _Client:
        def __init__(self, result=None, exc=None):
            self.result = result
            self.exc = exc

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def call_tool(self, name, arguments, timeout):
            if self.exc:
                raise self.exc
            return self.result

    class _Telemetry:
        enabled = True

        def __init__(self):
            self.calls = []

        async def record_upstream_tool_call(self, **kwargs):
            self.calls.append(kwargs)

    result_obj = SimpleNamespace(content=[{"type": "text", "text": "ok"}], structured_content={"a": 1}, meta={"m": 1})
    telemetry = _Telemetry()

    out = await uc.call_upstream_tool(
        client_factory=lambda: _Client(result=result_obj),
        tool_name="tool",
        arguments={"x": 1},
        timeout_seconds=1,
        telemetry=telemetry,
        server_id="s1",
    )
    assert out.meta["m"] == 1
    assert telemetry.calls[-1]["result"] == "success"

    with pytest.raises(ToolError):
        await uc.call_upstream_tool(
            client_factory=lambda: _Client(exc=TimeoutError("x")),
            tool_name="tool",
            arguments={},
            timeout_seconds=1,
            telemetry=telemetry,
            server_id="s1",
        )
    assert telemetry.calls[-1]["result"] == "timeout"

    with pytest.raises(RuntimeError):
        await uc.call_upstream_tool(
            client_factory=lambda: _Client(exc=RuntimeError("x")),
            tool_name="tool",
            arguments={},
            timeout_seconds=1,
            telemetry=telemetry,
            server_id="s1",
        )
    assert telemetry.calls[-1]["result"] == "error"
