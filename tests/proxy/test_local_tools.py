from __future__ import annotations

from types import SimpleNamespace

import pytest

from remote_mcp_adapter.proxy import local_tools


class FakeProxy:
    def __init__(self):
        self.tool_calls: list[dict[str, object]] = []
        self.registered_handler = None

    def tool(self, **kwargs):
        self.tool_calls.append(kwargs)

        def decorator(func):
            self.registered_handler = func
            return func

        return decorator


class FakeUploadCredentials:
    def __init__(self, issued: dict[str, str], ttl_seconds: int = 77):
        self._issued = issued
        self.ttl_seconds = ttl_seconds
        self.calls: list[dict[str, str]] = []

    async def issue(self, *, server_id: str, session_id: str):
        self.calls.append({"server_id": server_id, "session_id": session_id})
        return dict(self._issued)


def _mount(server_id: str = "srv-1"):
    return SimpleNamespace(server=SimpleNamespace(id=server_id), proxy=FakeProxy())


def _config(upload_path: str = "/uploads", *, require_sha256: bool = False):
    return SimpleNamespace(
        core=SimpleNamespace(upload_path=upload_path),
        uploads=SimpleNamespace(require_sha256=require_sha256),
    )


def _ctx(session_id: str = "sess-1"):
    return SimpleNamespace(session_id=session_id, request_context=None)


def test_get_upload_url_tool_name_normalizes_and_defaults_for_empty_value():
    assert local_tools.get_upload_url_tool_name("my server-1") == "my_server_1_get_upload_url"
    assert local_tools.get_upload_url_tool_name("   ") == "server_get_upload_url"


@pytest.mark.asyncio
async def test_register_get_upload_url_tool_with_ctx_and_signed_credentials(monkeypatch):
    mount = _mount("server@id")
    config = _config("uploads", require_sha256=True)
    creds = FakeUploadCredentials(
        issued={"mcp_upload_exp": "10", "mcp_upload_nonce": "n1", "mcp_upload_sig": "sig"},
        ttl_seconds=120,
    )

    monkeypatch.setattr(local_tools, "derive_public_base_url", lambda cfg, ctx: "https://public.example")

    local_tools.register_get_upload_url_tool(mount=mount, config=config, upload_credentials=creds)

    assert mount.proxy.registered_handler is not None
    assert mount.proxy.tool_calls[0]["name"] == "server_id_get_upload_url"

    payload = await mount.proxy.registered_handler(_ctx("sess-abc"))

    assert creds.calls == [{"server_id": "server@id", "session_id": "sess-abc"}]
    assert payload["server_id"] == "server@id"
    assert payload["tool_name"] == "server_id_get_upload_url"
    assert payload["upload_url"].startswith("https://public.example/uploads/server@id?")
    assert "mcp_upload_exp=10" in payload["upload_url"]
    assert payload["auth_mode"] == "signed_upload_credentials"
    assert payload["credential_ttl_seconds"] == 120
    assert payload["headers"] == {"Mcp-Session-Id": "sess-abc"}
    assert payload["method"] == "POST"
    assert payload["field_name"] == "file"
    assert payload["supports_multiple_files"] is True
    assert payload["sha256_required"] is True
    assert payload["sha256_field_name"] == "sha256"
    assert payload["sha256_per_file"] is True
    assert '-F "sha256=<sha256_for_file>"' in payload["example_curl"]
    assert '-F "sha256=<sha256_for_file1>"' in payload["example_curl_multiple"]
    assert "Provide one `sha256` multipart form field per uploaded file" in payload["integrity_note"]


@pytest.mark.asyncio
async def test_register_get_upload_url_tool_uses_get_context_and_unsigned_mode(monkeypatch):
    mount = _mount("srv")
    config = _config("/uploads/")

    monkeypatch.setattr(local_tools, "derive_public_base_url", lambda cfg, ctx: "http://127.0.0.1:8000")
    monkeypatch.setattr(local_tools, "get_context", lambda: _ctx("sess-from-di"))

    local_tools.register_get_upload_url_tool(mount=mount, config=config, upload_credentials=None)

    payload = await mount.proxy.registered_handler(None)

    assert payload["upload_url"] == "http://127.0.0.1:8000/uploads/srv"
    assert payload["auth_mode"] == "header_token_or_none"
    assert payload["credential_ttl_seconds"] is None
    assert payload["session_id"] == "sess-from-di"
    assert "curl -X POST" in payload["example_curl"]
    assert "file1" in payload["example_curl_multiple"]
    assert "upload://" in payload["note"]
    assert payload["sha256_required"] is False
    assert payload["sha256_field_name"] == "sha256"
    assert payload["sha256_per_file"] is True
    assert "sha256=<sha256_for_file>" not in payload["example_curl"]
    assert "Optional: include `sha256` multipart form fields" in payload["integrity_note"]
