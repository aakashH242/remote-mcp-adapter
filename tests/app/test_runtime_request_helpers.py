from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException

from remote_mcp_adapter.app import runtime_request_helpers as rrh


def _config(*, auth_enabled=False, header_name="X-Token", token="t", cors_enabled=False):
    return SimpleNamespace(
        core=SimpleNamespace(
            auth=SimpleNamespace(enabled=auth_enabled, header_name=header_name, token=token),
            cors=SimpleNamespace(
                enabled=cors_enabled,
                allowed_origins=["*"],
                allowed_methods=["GET"],
                allowed_headers=["*"],
                allow_credentials=False,
            ),
        )
    )


def test_validate_adapter_auth_paths():
    request = SimpleNamespace(headers={})
    rrh.validate_adapter_auth(request, _config(auth_enabled=False))

    with pytest.raises(HTTPException, match="missing or invalid"):
        rrh.validate_adapter_auth(request, _config(auth_enabled=True, header_name="X-Token", token="abc"))

    ok_request = SimpleNamespace(headers={"X-Token": "abc"})
    rrh.validate_adapter_auth(ok_request, _config(auth_enabled=True, header_name=" X-Token ", token="abc"))


def test_apply_cors_middleware_enabled_and_disabled(monkeypatch):
    app = FastAPI()
    calls = []
    monkeypatch.setattr(app, "add_middleware", lambda *args, **kwargs: calls.append((args, kwargs)))

    rrh.apply_cors_middleware(app, _config(cors_enabled=False))
    assert calls == []

    rrh.apply_cors_middleware(app, _config(cors_enabled=True))
    assert len(calls) == 1


def test_resolve_config_and_paths(monkeypatch):
    cfg_obj = SimpleNamespace(x=1)
    assert rrh.resolve_config(cfg_obj, None) is cfg_obj

    monkeypatch.setenv("MCP_ADAPTER_CONFIG", "from-env.yaml")
    monkeypatch.setattr(rrh, "load_config", lambda path: {"path": path})
    assert rrh.resolve_config(None, None) == {"path": "from-env.yaml"}
    assert rrh.resolve_config(None, "explicit.yaml") == {"path": "explicit.yaml"}

    mounts = {"/mcp/a": "a", "/mcp/b": "b"}
    assert rrh.resolve_server_id_for_path("/mcp/a/tools", mounts) == "a"
    assert rrh.resolve_server_id_for_path("/none", mounts) is None

    monkeypatch.setattr(rrh, "build_server_upload_path", lambda upload_path, sid: "/upload/")
    assert rrh.upload_path_prefix("/upload") == "/upload/"


def test_is_stateful_request_path():
    mounts = {"/mcp/a": "a"}
    assert rrh.is_stateful_request_path(path="/mcp/a/tools", mount_path_to_server_id=mounts, upload_path_prefix="/upload") is True
    assert rrh.is_stateful_request_path(path="/upload/a", mount_path_to_server_id=mounts, upload_path_prefix="/upload") is True
    assert rrh.is_stateful_request_path(path="/artifacts/a/s/x/f", mount_path_to_server_id=mounts, upload_path_prefix="/upload") is True
    assert rrh.is_stateful_request_path(path="/public", mount_path_to_server_id=mounts, upload_path_prefix="/upload") is False
