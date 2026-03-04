from __future__ import annotations

from pathlib import Path

import pytest

from remote_mcp_adapter.config.load import _interpolate_env, _interpolate_string, load_config
from remote_mcp_adapter.config.schemas.persistence import StateReconciliationConfig
from remote_mcp_adapter.config.schemas.root import AdapterConfig, config_to_dict, resolve_storage_lock_mode, resolve_write_policy_lock_mode


def _base_config_dict():
    return {
        "servers": [
            {
                "id": "s1",
                "mount_path": "/mcp/s1",
                "upstream": {"url": "http://localhost:9000", "transport": "streamable_http"},
                "adapters": [],
            }
        ]
    }


def test_interpolate_string_and_env(monkeypatch):
    monkeypatch.setenv("A", "x")
    assert _interpolate_string("${A}") == "x"
    assert _interpolate_string("${MISSING:-d}") == "d"

    with pytest.raises(ValueError, match="Missing environment variable"):
        _interpolate_string("${MISSING}")

    data = {"a": "${A}", "b": ["${MISSING:-d}", 1], "c": {"x": "ok"}}
    assert _interpolate_env(data) == {"a": "x", "b": ["d", 1], "c": {"x": "ok"}}


def test_adapter_config_validation_and_lock_resolution(tmp_path):
    cfg_data = _base_config_dict()
    cfg_data["storage"] = {"root": str(tmp_path / "root"), "lock_mode": "auto"}
    cfg_data["state_persistence"] = {"type": "disk", "disk": {"local_path": None}}

    cfg = AdapterConfig.model_validate(cfg_data)
    assert cfg.state_persistence.disk.local_path.replace("\\", "/").endswith("state/adapter_state.sqlite3")
    assert resolve_storage_lock_mode(cfg) == "file"
    assert resolve_write_policy_lock_mode(cfg) == "file"

    cfg_redis = _base_config_dict()
    cfg_redis["storage"] = {"lock_mode": "auto"}
    cfg_redis["state_persistence"] = {"type": "redis", "redis": {"host": "localhost"}}
    validated_redis = AdapterConfig.model_validate(cfg_redis)
    assert resolve_storage_lock_mode(validated_redis) == "redis"

    cfg_explicit = _base_config_dict()
    cfg_explicit["storage"] = {"lock_mode": "process"}
    validated_explicit = AdapterConfig.model_validate(cfg_explicit)
    assert resolve_storage_lock_mode(validated_explicit) == "process"

    bad_redis = _base_config_dict()
    bad_redis["state_persistence"] = {"type": "redis", "redis": {"host": None}}
    with pytest.raises(ValueError, match="redis.host is required"):
        AdapterConfig.model_validate(bad_redis)

    bad_lock = _base_config_dict()
    bad_lock["storage"] = {"lock_mode": "redis"}
    bad_lock["state_persistence"] = {"type": "disk"}
    with pytest.raises(ValueError, match="lock_mode='redis' requires"):
        AdapterConfig.model_validate(bad_lock)


def test_adapter_config_uniqueness_and_legacy_server_validation():
    assert StateReconciliationConfig(legacy_server_id=None).legacy_server_id is None

    dup_id = {
        "servers": [
            {"id": "s", "mount_path": "/a", "upstream": {"url": "http://x", "transport": "streamable_http"}},
            {"id": "s", "mount_path": "/b", "upstream": {"url": "http://y", "transport": "streamable_http"}},
        ]
    }
    with pytest.raises(ValueError, match=r"Duplicate servers\[\]\.id"):
        AdapterConfig.model_validate(dup_id)

    dup_mount = {
        "servers": [
            {"id": "a", "mount_path": "/m", "upstream": {"url": "http://x", "transport": "streamable_http"}},
            {"id": "b", "mount_path": "/m", "upstream": {"url": "http://y", "transport": "streamable_http"}},
        ]
    }
    with pytest.raises(ValueError, match=r"Duplicate servers\[\]\.mount_path"):
        AdapterConfig.model_validate(dup_mount)

    bad_legacy = _base_config_dict()
    bad_legacy["state_persistence"] = {"reconciliation": {"legacy_server_id": "missing"}}
    with pytest.raises(ValueError, match="legacy_server_id must match"):
        AdapterConfig.model_validate(bad_legacy)


def test_config_to_dict_and_load_config(tmp_path, monkeypatch):
    cfg_data = _base_config_dict()
    cfg = AdapterConfig.model_validate(cfg_data)
    dumped = config_to_dict(cfg)
    assert dumped["servers"][0]["id"] == "s1"

    monkeypatch.setenv("UP_URL", "http://localhost:9100")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
servers:
  - id: s1
    mount_path: /mcp/s1
    upstream:
      transport: streamable_http
      url: ${UP_URL}
    adapters: []
""".strip(),
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    assert loaded.servers[0].upstream.url == "http://localhost:9100"
