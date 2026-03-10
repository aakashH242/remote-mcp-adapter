from __future__ import annotations

from fastmcp.server.providers.proxy import ProxyProvider
from fastmcp.server.transforms.visibility import Visibility
import pytest

from remote_mcp_adapter.proxy import code_mode as cm
from remote_mcp_adapter.proxy import tool_names


class _FakeProxy:
    def __init__(self, providers=None):
        self.providers = list(providers or [])


def test_resolve_code_mode_enabled_prefers_server_override():
    assert cm.resolve_code_mode_enabled(core_enabled=False, server_enabled=True) is True
    assert cm.resolve_code_mode_enabled(core_enabled=True, server_enabled=False) is False
    assert cm.resolve_code_mode_enabled(core_enabled=True, server_enabled=None) is True


def test_build_code_mode_transforms_disabled_returns_empty():
    assert cm.build_code_mode_transforms(enabled=False, server_id="playwright") == []


def test_build_code_mode_transforms_enabled_returns_code_mode():
    transforms = cm.build_code_mode_transforms(enabled=True, server_id="playwright-server")

    assert len(transforms) == 1
    assert transforms[0].__class__.__name__ == "CodeMode"
    assert transforms[0].execute_tool_name == "playwright_server_execute"
    assert [tool.name for tool in transforms[0]._build_discovery_tools()] == [
        "playwright_server_search",
        "playwright_server_get_schema",
        "playwright_server_tags",
        "playwright_server_list_tools",
    ]


def test_build_code_mode_transforms_enabled_wraps_import_error(monkeypatch):
    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fastmcp.experimental.transforms.code_mode":
            raise ModuleNotFoundError("missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(RuntimeError, match="Code Mode requires FastMCP Code Mode dependencies"):
        cm.build_code_mode_transforms(enabled=True, server_id="playwright")


def test_tool_name_helpers_normalize_server_ids():
    assert tool_names.get_upload_url_tool_name("my server-1") == "my_server_1_get_upload_url"
    assert tool_names.code_mode_execute_tool_name("my server-1") == "my_server_1_execute"
    assert tool_names.code_mode_search_tool_name("my server-1") == "my_server_1_search"
    assert tool_names.code_mode_get_schema_tool_name("my server-1") == "my_server_1_get_schema"
    assert tool_names.code_mode_tags_tool_name("my server-1") == "my_server_1_tags"
    assert tool_names.code_mode_list_tools_tool_name("my server-1") == "my_server_1_list_tools"


def test_hide_upstream_tool_names_adds_visibility_to_proxy_provider():
    provider = ProxyProvider(lambda: None)
    proxy = _FakeProxy(providers=[provider])

    cm.hide_upstream_tool_names(proxy=proxy, tool_names={"tool_a", ""})

    assert len(provider._transforms) == 1
    assert isinstance(provider._transforms[0], Visibility)


def test_hide_upstream_tool_names_ignores_missing_provider():
    proxy = _FakeProxy(providers=[object()])

    cm.hide_upstream_tool_names(proxy=proxy, tool_names={"tool_a"})

    assert proxy.providers == [proxy.providers[0]]


def test_hide_upstream_tool_names_ignores_empty_names():
    provider = ProxyProvider(lambda: None)
    proxy = _FakeProxy(providers=[provider])

    cm.hide_upstream_tool_names(proxy=proxy, tool_names={""})

    assert provider._transforms == []


def test_find_proxy_provider_returns_none_when_proxy_has_no_providers():
    assert cm._find_proxy_provider(object()) is None
