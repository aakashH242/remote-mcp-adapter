from __future__ import annotations

import remote_mcp_adapter
from remote_mcp_adapter import constants
from remote_mcp_adapter import config
from remote_mcp_adapter.config import schemas


def test_package_exports_create_app():
    assert remote_mcp_adapter.__all__ == ["create_app"]
    assert callable(remote_mcp_adapter.create_app)


def test_constants_values():
    assert constants.GLOBAL_SERVER_ID == "global"
    assert constants.UNKNOWN_SERVER_ID == "unknown"
    assert constants.MCP_SESSION_ID_HEADER == "mcp-session-id"
    assert constants.DEFAULT_ADAPTER_AUTH_HEADER == "X-Mcp-Adapter-Auth-Token"
    assert constants.ARTIFACT_PATH_PREFIX == "/artifacts/"


def test_config_exports():
    assert "load_config" in config.__all__
    assert callable(config.load_config)
    assert "AdapterConfig" in schemas.__all__
    assert "resolve_storage_lock_mode" in schemas.__all__
